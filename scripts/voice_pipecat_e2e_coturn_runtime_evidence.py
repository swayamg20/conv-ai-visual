"""Immediate attached-stdout pump into the bounded Coturn evidence parser."""

from __future__ import annotations

import threading

from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as _drain_registry
from scripts import voice_pipecat_e2e_coturn_runtime_process_claims as _process_claims
from scripts.voice_pipecat_e2e_coturn_evidence import (
    CoturnEvidenceParser,
    CoturnProbeResultSlot,
    CoturnProbeSummary,
    coturn_probe_summary_from_slot,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_PUMP_TOKEN = object()
_MAX_ABORT_ATTEMPTS = 8
_MAX_CLAIM_ATTEMPTS = 8


class _DrainClaim:
    """Complete one-shot snapshot published through one atomic field store."""

    __slots__ = (
        "absolute_deadline",
        "clock",
        "drain",
        "owner",
        "process",
        "return_key",
    )

    def __init__(
        self,
        *,
        owner: object | None = None,
        process: AttachedCoturnProcess | None = None,
        drain: object | None = None,
        absolute_deadline: float | None = None,
        clock: object | None = None,
        return_key: object | None = None,
    ) -> None:
        self.owner = owner
        self.process = process
        self.drain = drain
        self.absolute_deadline = absolute_deadline
        self.clock = clock
        self.return_key = return_key


class AttachedCoturnEvidencePump:
    """Own one process/parser pair without retaining any raw output chunk."""

    __slots__ = (
        "_claim_owner",
        "_claim_process",
        "_drain_claim",
        "_failed",
        "_finished",
        "_lock",
        "_parser",
        "_parser_terminalized",
        "_process",
        "_result_slot",
        "_summary",
    )

    def __init__(
        self,
        token: object,
        *,
        process: AttachedCoturnProcess,
        parser: CoturnEvidenceParser,
        result_slot: CoturnProbeResultSlot,
        claim_owner: object,
    ) -> None:
        if (
            token is not _PUMP_TOKEN
            or type(process) is not AttachedCoturnProcess
            or type(parser) is not CoturnEvidenceParser
            or type(result_slot) is not CoturnProbeResultSlot
            or claim_owner is None
        ):
            raise TypeError("Coturn evidence pump is factory-owned")
        try:
            lock = self._lock
        except AttributeError:
            raise TypeError("Coturn evidence pump is factory-owned") from None
        with lock:
            if self._process is not None or self._parser is not None:
                raise TypeError("Coturn evidence pump is factory-owned")
            self._claim_owner = claim_owner
            self._claim_process = process
            self._process = process
            self._parser = parser
            self._result_slot = result_slot

    @classmethod
    def _new_destination(cls) -> AttachedCoturnEvidencePump:
        """Return a harmless, scrub-safe exact destination before adoption."""

        pump = object.__new__(cls)
        pump._lock = threading.Lock()
        pump._claim_owner = None
        pump._claim_process = None
        pump._drain_claim = _DrainClaim()
        pump._failed = False
        pump._finished = False
        pump._parser = None
        pump._parser_terminalized = False
        pump._process = None
        pump._result_slot = None
        pump._summary = None
        return pump

    def pump_once(self, *, timeout_seconds: float) -> bool:
        """Read, validate, and feed one stdout chunk before returning control."""

        with self._lock:
            if self._failed or self._finished or self._parser is None or self._process is None:
                timeout_seconds = 0.0
                self = None  # type: ignore[assignment]
                raise CoturnRuntimeError("Coturn attached evidence pump is unavailable")
            chunk: bytes | None = None
            control: ControlSignal | None = None
            failed = False
            try:
                chunk = self._process.read_chunk(timeout_seconds=timeout_seconds)
                if chunk is not None:
                    self._parser.feed(chunk)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
                failed = True
            except BaseException:
                failed = True
            observed = chunk is not None and not failed
            chunk = None
            if failed:
                self._failed = True
                abort_failed, abort_control = self._abort_locked()
                control = control or abort_control
                failed = bool(failed or abort_failed)
        if control is not None:
            timeout_seconds = 0.0
            self = None  # type: ignore[assignment]
            raise_control(control)
        if failed:
            timeout_seconds = 0.0
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError("Coturn attached evidence pump failed") from None
        return observed

    def _matches_process(self, process: AttachedCoturnProcess) -> bool:
        """Match only the exact active process adopted by this pump."""

        with self._lock:
            return bool(
                type(process) is AttachedCoturnProcess
                and self._process is process
                and type(self._parser) is CoturnEvidenceParser
                and type(self._result_slot) is CoturnProbeResultSlot
                and not self._failed
                and not self._finished
            )

    def _claim_drain(
        self,
        process: AttachedCoturnProcess,
        owner: object,
        drain: object,
        absolute_deadline: float,
        clock: object,
        return_key: object,
    ) -> bool:
        """Atomically adopt this exact active pump/process pair once."""

        with self._lock:
            if (
                owner is None
                or drain is None
                or return_key is None
                or type(process) is not AttachedCoturnProcess
                or type(absolute_deadline) is not float
                or self._process is not process
                or type(self._parser) is not CoturnEvidenceParser
                or type(self._result_slot) is not CoturnProbeResultSlot
                or self._failed
                or self._finished
            ):
                return False
            claimed = self._drain_claim.owner
            if claimed is None:
                record = _DrainClaim(
                    owner=owner,
                    process=process,
                    drain=drain,
                    absolute_deadline=absolute_deadline,
                    clock=clock,
                    return_key=return_key,
                )
                self._drain_claim = record
                if _drain_registry.publish_canonical_return(return_key, drain):
                    return True
                self._clear_drain_claim_locked()
                return False
            return bool(
                claimed is owner
                and self._drain_claim.drain is drain
                and _drain_registry.publish_canonical_return(
                    self._drain_claim.return_key,
                    drain,
                )
            )

    def _claimed_drain_key(
        self,
        process: AttachedCoturnProcess,
        absolute_deadline: float,
        clock: object,
    ) -> object | None:
        """Recover the one canonical owner after its public return was lost."""

        with self._lock:
            claim = self._drain_claim
            if (
                type(process) is AttachedCoturnProcess
                and type(absolute_deadline) is float
                and claim.owner is not None
                and claim.process is process
                and claim.absolute_deadline == absolute_deadline
                and claim.clock is clock
            ):
                if _drain_registry.publish_canonical_return(
                    claim.return_key,
                    claim.drain,
                ):
                    return claim.return_key
            return None

    def _release_drain_claim(
        self,
        process: AttachedCoturnProcess,
        owner: object,
        drain: object,
    ) -> bool:
        with self._lock:
            claim = self._drain_claim
            if (
                claim.owner is None
                and claim.process is None
                and claim.drain is None
                and claim.absolute_deadline is None
                and claim.clock is None
                and claim.return_key is None
            ):
                return True
            if claim.owner is not owner or claim.process is not process or claim.drain is not drain:
                return False
            if not _drain_registry.release_canonical_return(claim.return_key, drain):
                return False
            self._clear_drain_claim_locked()
            return True

    def _clear_drain_claim_locked(self) -> None:
        self._drain_claim = _DrainClaim()

    def _matches_drain(self, process: AttachedCoturnProcess, owner: object) -> bool:
        with self._lock:
            return bool(
                owner is not None
                and type(process) is AttachedCoturnProcess
                and self._drain_claim.process is process
                and self._drain_claim.owner is owner
                and not self._failed
                and not self._finished
            )

    def _abort(self) -> tuple[bool, ControlSignal | None]:
        """Idempotently terminalize the parser and discard all retained state."""

        with self._lock:
            if self._drain_claim.owner is not None:
                return True, None
            return self._abort_locked()

    def _abort_drain(
        self,
        process: AttachedCoturnProcess,
        owner: object,
    ) -> tuple[bool, ControlSignal | None]:
        """Abort only for the exact drain that atomically claimed this pair."""

        with self._lock:
            if self._drain_claim.owner is not owner or self._drain_claim.process is not process:
                return True, None
            if self._process is not None and self._process is not process:
                return True, None
            return self._abort_locked()

    def _abort_locked(self) -> tuple[bool, ControlSignal | None]:
        parser = self._parser
        control: ControlSignal | None = None
        failed = False
        self._failed = True
        self._finished = False
        self._summary = None
        if parser is not None and not self._parser_terminalized:
            scrubbed = False
            attempts = 0
            while attempts < _MAX_ABORT_ATTEMPTS and not scrubbed:
                attempts += 1
                try:
                    parser._terminalize(failed=True)
                    scrubbed = True
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    pass
            if not scrubbed:
                scrubbed, scrub_control = _force_scrub_parser(parser)
                control = control or scrub_control
            failed = not scrubbed
            if scrubbed:
                self._parser_terminalized = True
        if not failed and self._parser_terminalized:
            self._parser = None
            self._result_slot = None
            release_failed, release_control = self._release_process_claim_locked()
            failed = release_failed
            control = control or release_control
            if not failed:
                self._process = None
        parser = None
        return failed, control

    def _release_process_claim_locked(self) -> tuple[bool, ControlSignal | None]:
        process = self._claim_process
        if process is None:
            return False, None
        control: ControlSignal | None = None
        released = False
        for _ in range(_MAX_CLAIM_ATTEMPTS):
            try:
                released = _process_claims.release_evidence_pump(
                    process,
                    self._claim_owner,
                    self,
                )
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                pass
            if released:
                self._claim_process = None
                break
        process = None
        return not released, control

    def __copy__(self) -> AttachedCoturnEvidencePump:
        raise TypeError("Coturn evidence pump cannot be copied")

    def __deepcopy__(self, _memo: object) -> AttachedCoturnEvidencePump:
        raise TypeError("Coturn evidence pump cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn evidence pump cannot be serialized")

    def finalize(
        self,
        *,
        clean_exit: CleanCoturnExitReceipt,
    ) -> CoturnProbeSummary:
        """Publish and return one retained, categorically non-passing summary."""

        summary: CoturnProbeSummary | None = None
        control: ControlSignal | None = None
        failure: str | None = None
        with self._lock:
            try:
                if _valid_summary(self._summary):
                    summary = self._recover_summary_locked(clean_exit)
                elif (
                    self._failed
                    or self._finished
                    or type(self._parser) is not CoturnEvidenceParser
                    or type(self._result_slot) is not CoturnProbeResultSlot
                    or type(self._process) is not AttachedCoturnProcess
                    or type(clean_exit) is not CleanCoturnExitReceipt
                    or not self._process._matches_exit(clean_exit)
                ):
                    failure = "Coturn evidence finalization requires clean drain"
                    _, control = self._abort_locked()
                else:
                    parser = self._parser
                    result_slot = self._result_slot
                    parser.finish_probe_into()
                    candidate = coturn_probe_summary_from_slot(result_slot)
                    if not _valid_summary(candidate):
                        raise CoturnRuntimeError("Coturn evidence finalization failed")
                    self._summary = candidate
                    summary = self._recover_summary_locked(clean_exit)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
            except BaseException:
                failure = "Coturn evidence finalization failed"
            attempts = 0
            while (
                summary is None
                and failure != "Coturn evidence finalization requires clean drain"
                and attempts < _MAX_CLAIM_ATTEMPTS
            ):
                attempts += 1
                try:
                    summary = self._recover_summary_locked(clean_exit)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    pass
            if summary is None and self._parser is not None:
                abort_failed, abort_control = self._abort_locked()
                control = control or abort_control
                if abort_failed and failure is None:
                    failure = "Coturn evidence finalization failed"
            parser = result_slot = candidate = None
        clean_exit = None  # type: ignore[assignment]
        if control is not None:
            summary = None
            self = None  # type: ignore[assignment]
            raise_control(control)
        if failure is not None:
            summary = None
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError(failure) from None
        if type(summary) is not CoturnProbeSummary:
            summary = None
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError(failure or "Coturn evidence finalization failed") from None
        self = None  # type: ignore[assignment]
        return summary

    def _recover_summary_locked(
        self,
        clean_exit: CleanCoturnExitReceipt,
    ) -> CoturnProbeSummary | None:
        candidate = self._summary
        process = self._process
        parser = self._parser
        slot = self._result_slot
        if not _valid_summary(candidate):
            if (
                type(process) is not AttachedCoturnProcess
                or type(clean_exit) is not CleanCoturnExitReceipt
                or not process._matches_exit(clean_exit)
                or type(parser) is not CoturnEvidenceParser
                or type(slot) is not CoturnProbeResultSlot
            ):
                return None
            candidate = slot._read()
            if not _valid_summary(candidate):
                return None
        self._summary = candidate
        if not self._parser_terminalized:
            if type(parser) is not CoturnEvidenceParser:
                return None
            parser._terminalize(failed=False)
            self._parser_terminalized = True
        self._parser = None
        release_failed, release_control = self._release_process_claim_locked()
        if release_control is not None:
            raise_control(release_control)
        if release_failed:
            return None
        self._finished = True
        self._failed = False
        self._process = None
        self._result_slot = None
        return candidate

    def _reconcile_finalization(
        self,
        process: AttachedCoturnProcess,
        owner: object,
        drain: object,
        clean_exit: CleanCoturnExitReceipt,
    ) -> CoturnProbeSummary | None:
        with self._lock:
            claim = self._drain_claim
            if claim.owner is not owner or claim.process is not process or claim.drain is not drain:
                return None
            return self._recover_summary_locked(clean_exit)

    def __repr__(self) -> str:
        return "AttachedCoturnEvidencePump()"


def _valid_summary(candidate: object) -> bool:
    return bool(
        type(candidate) is CoturnProbeSummary
        and candidate.grammar_verified is False
        and not candidate
    )


def _force_scrub_parser(
    parser: CoturnEvidenceParser,
) -> tuple[bool, ControlSignal | None]:
    """Last-resort raw-state scrub when a hostile terminalizer never returns."""

    control: ControlSignal | None = None
    state: object = None
    line: object = None
    try:
        state = object.__getattribute__(parser, "_state")
        if state is not None:
            state.clear()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    try:
        if state is not None:
            for name in (
                "_expected_username",
                "_expected_realm",
                "_expected_container",
                "_expected_gateway",
            ):
                value = object.__getattribute__(state, name)
                if type(value) is bytearray:
                    value[:] = b"\x00" * len(value)
                    value.clear()
            for name in (
                "_allocations",
                "_readiness",
                "_startup_digests",
                "_observed",
            ):
                value = object.__getattribute__(state, name)
                value.clear()
            object.__setattr__(state, "_last_startup_digest", None)
        line = object.__getattribute__(parser, "_line")
        if type(line) is bytearray:
            line[:] = b"\x00" * len(line)
            line.clear()
        object.__setattr__(parser, "_state", None)
        object.__setattr__(parser, "_failed", True)
        object.__setattr__(parser, "_finished", False)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or control_signal(error)
    except BaseException:
        pass
    try:
        scrubbed = object.__getattribute__(
            parser, "_state"
        ) is None and not object.__getattribute__(
            parser,
            "_line",
        )
    except BaseException:
        scrubbed = False
    state = line = parser = None  # type: ignore[assignment]
    return bool(scrubbed), control


from scripts.voice_pipecat_e2e_coturn_runtime_evidence_factory import (  # noqa: E402
    create_attached_coturn_evidence_pump,
)

__all__ = [
    "AttachedCoturnEvidencePump",
    "CoturnProbeSummary",
    "create_attached_coturn_evidence_pump",
]

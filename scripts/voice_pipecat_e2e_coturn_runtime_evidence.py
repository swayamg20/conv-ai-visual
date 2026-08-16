"""Immediate attached-stdout pump into the bounded Coturn evidence parser."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_evidence import (
    COTURN_REALM,
    CoturnEvidenceParser,
    CoturnProbeResultSlot,
    CoturnProbeSummary,
    coturn_probe_summary_from_slot,
    new_coturn_probe_result_slot,
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


class AttachedCoturnEvidencePump:
    """Own one process/parser pair without retaining any raw output chunk."""

    __slots__ = (
        "_failed",
        "_finished",
        "_lock",
        "_parser",
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
    ) -> None:
        if token is not _PUMP_TOKEN:
            raise TypeError("Coturn evidence pump is factory-owned")
        self._process: AttachedCoturnProcess | None = process
        self._parser: CoturnEvidenceParser | None = parser
        self._result_slot: CoturnProbeResultSlot | None = result_slot
        self._summary: CoturnProbeSummary | None = None
        self._failed = False
        self._finished = False
        self._lock = threading.Lock()

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
                self._parser = None
                self._process = None
        if control is not None:
            timeout_seconds = 0.0
            self = None  # type: ignore[assignment]
            raise_control(control)
        if failed:
            timeout_seconds = 0.0
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError("Coturn attached evidence pump failed") from None
        return observed

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
                if type(self._summary) is CoturnProbeSummary:
                    summary = self._summary
                    self._finished = True
                    self._parser = None
                    self._process = None
                    self._result_slot = None
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
                    self._failed = True
                    self._parser = None
                    self._process = None
                    self._result_slot = None
                else:
                    parser = self._parser
                    result_slot = self._result_slot
                    parser.finish_probe_into()
                    candidate = coturn_probe_summary_from_slot(result_slot)
                    if (
                        type(candidate) is not CoturnProbeSummary
                        or candidate.grammar_verified is not False
                        or bool(candidate)
                    ):
                        raise CoturnRuntimeError("Coturn evidence finalization failed")
                    self._summary = candidate
                    summary = candidate
                    self._finished = True
                    self._parser = None
                    self._process = None
                    self._result_slot = None
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
            except BaseException:
                failure = "Coturn evidence finalization failed"
                slot = self._result_slot
                ready = False
                try:
                    ready = type(slot) is CoturnProbeResultSlot and slot.ready is True
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                except BaseException:
                    ready = False
                if not ready and control is None:
                    self._failed = True
                    self._parser = None
                    self._process = None
                    self._result_slot = None
            parser = result_slot = candidate = slot = None
        clean_exit = None  # type: ignore[assignment]
        if control is not None:
            summary = None
            self = None  # type: ignore[assignment]
            raise_control(control)
        if type(summary) is not CoturnProbeSummary:
            summary = None
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError(failure or "Coturn evidence finalization failed") from None
        self = None  # type: ignore[assignment]
        return summary

    def __repr__(self) -> str:
        return "AttachedCoturnEvidencePump()"


def create_attached_coturn_evidence_pump(
    *,
    process: AttachedCoturnProcess,
    expected_username: object,
    expected_topology: object,
    expected_realm: object = COTURN_REALM,
) -> AttachedCoturnEvidencePump:
    """Bind one hidden, categorically non-passing probe parser to one process."""

    parser: CoturnEvidenceParser | None = None
    result_slot: CoturnProbeResultSlot | None = None
    control: ControlSignal | None = None
    try:
        if type(process) is not AttachedCoturnProcess or not process._matches_topology(
            expected_topology
        ):
            raise CoturnRuntimeError("Coturn evidence pump input is invalid")
        result_slot = new_coturn_probe_result_slot()
        parser = CoturnEvidenceParser.for_probe(
            expected_username=expected_username,
            expected_topology=expected_topology,
            expected_realm=expected_realm,
            result_slot=result_slot,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    expected_username = expected_topology = expected_realm = None
    if control is not None:
        parser = None
        result_slot = None
        process = None  # type: ignore[assignment]
        raise_control(control)
    if parser is None or result_slot is None:
        process = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence pump input is invalid") from None
    pump = AttachedCoturnEvidencePump(
        _PUMP_TOKEN,
        process=process,
        parser=parser,
        result_slot=result_slot,
    )
    process = None  # type: ignore[assignment]
    parser = None
    result_slot = None
    return pump


__all__ = [
    "AttachedCoturnEvidencePump",
    "CoturnProbeSummary",
    "create_attached_coturn_evidence_pump",
]

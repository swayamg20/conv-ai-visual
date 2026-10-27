"""Bounded concurrent owner for one attached Coturn evidence stream."""

from __future__ import annotations

import threading
from collections.abc import Callable

from scripts import voice_pipecat_e2e_coturn_runtime_drain_recovery as _recovery
from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as _registry
from scripts import voice_pipecat_e2e_coturn_runtime_drain_terminal as _terminal
from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_runtime_drain_registry import (
    CoturnEvidenceDrainCleanupAuthority,
    CoturnEvidenceDrainCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import AttachedCoturnEvidencePump
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
    confirm_attached_coturn_clean_exit,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_DRAIN_TOKEN = object()
_MAX_DRAIN_ATTEMPTS = 4_096
_READ_TIMEOUT_SECONDS = 0.05
_MAX_JOIN_ATTEMPTS = 5
_MAX_TERMINAL_PUBLICATION_ATTEMPTS = 8


class AttachedCoturnEvidenceDrain:
    __slots__ = (
        "_attempts",
        "_cleanup_authority",
        "_clock",
        "_control",
        "_deadline",
        "_done",
        "_lock",
        "_operation_lock",
        "_owner_token",
        "_process",
        "_pump",
        "_state",
        "_summary",
        "_terminal_transition",
        "_thread",
        "_worker_drained",
        "_worker_failed",
    )

    def __init__(
        self,
        token: object,
        *,
        process: AttachedCoturnProcess,
        pump: AttachedCoturnEvidencePump,
        absolute_deadline: float,
        clock: Callable[[], float],
    ) -> None:
        if token is not _DRAIN_TOKEN:
            raise TypeError("Coturn evidence drain is factory-owned")
        self._cleanup_authority = _registry.new_cleanup_authority()
        self._process: AttachedCoturnProcess | None = process
        self._pump: AttachedCoturnEvidencePump | None = pump
        self._owner_token = object()
        self._deadline = absolute_deadline
        self._clock: Callable[[], float] | None = clock
        self._attempts = 0
        self._control: ControlSignal | None = None
        self._worker_drained = False
        self._worker_failed = False
        self._summary: CoturnProbeSummary | None = None
        self._terminal_transition: _terminal.DrainTerminalTransition | None = None
        self._thread: threading.Thread | None = None
        self._state = "created"
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._operation_lock = threading.RLock()

    def _start(self) -> tuple[bool, ControlSignal | None]:
        control: ControlSignal | None = None
        failed = False
        started = False
        thread: threading.Thread | None = None
        with self._operation_lock:
            should_start = False
            try:
                with self._lock:
                    if self._state in {"running", "drained", "worker-failed"}:
                        return False, None
                    if self._state == "starting":
                        thread = self._thread
                    elif (
                        self._state == "created"
                        and self._process is not None
                        and self._pump is not None
                        and self._pump._matches_drain(self._process, self._owner_token)
                    ):
                        thread = threading.Thread(
                            target=self._run,
                            name="coturn-evidence-drain",
                            daemon=False,
                        )
                        self._thread = thread
                        self._state = "starting"
                        should_start = True
                    else:
                        return True, self._control
                if thread is None:
                    failed = True
                elif should_start:
                    thread.start()
                    started = True
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
            except BaseException:
                failed = True
            if not started and thread is not None:
                try:
                    started = thread.ident is not None or thread.is_alive() or self._done.is_set()
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    failed = True
            with self._lock:
                self._record_control_locked(control)
                if started:
                    if self._state == "starting":
                        self._state = "running"
                else:
                    self._state = "start-failed"
                    failed = True
                if control is not None:
                    control = self._control
            thread = None
        return failed, control

    def _run(self) -> None:
        drained = False
        failed = False
        control: ControlSignal | None = None
        attempts = 0
        process = self._process
        pump = self._pump
        try:
            if (
                type(process) is not AttachedCoturnProcess
                or type(pump) is not AttachedCoturnEvidencePump
                or not pump._matches_drain(process, self._owner_token)
            ):
                raise CoturnRuntimeError("Coturn evidence drain failed")
            while attempts < _MAX_DRAIN_ATTEMPTS and not drained:
                now = _recovery.read_clock(self._clock)
                remaining = self._deadline - now
                if remaining < 0.01:
                    failed = True
                    break
                attempts += 1
                observed = pump.pump_once(timeout_seconds=min(_READ_TIMEOUT_SECONDS, remaining))
                if not observed:
                    drained = process.drained
            if not drained:
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
            failed = True
        except BaseException:
            failed = True
        process = pump = None
        self._publish_worker_outcome(
            attempts=attempts,
            drained=drained,
            failed=failed,
            control=control,
        )

    def _publish_worker_outcome(
        self,
        *,
        attempts: int,
        drained: bool,
        failed: bool,
        control: ControlSignal | None,
    ) -> None:
        terminal_failed = bool(failed or not drained)
        terminal_control = control
        done_published = False
        publication_attempts = 0
        while publication_attempts < _MAX_TERMINAL_PUBLICATION_ATTEMPTS and not done_published:
            publication_attempts += 1
            try:
                with self._lock:
                    self._attempts = attempts
                    self._record_control_locked(terminal_control)
                    self._worker_drained = bool(drained and not terminal_failed)
                    self._worker_failed = bool(terminal_failed or not drained)
                    if self._state in {"starting", "running", "drained", "worker-failed"}:
                        self._state = "drained" if self._worker_drained else "worker-failed"
            except (KeyboardInterrupt, SystemExit) as error:
                terminal_control = terminal_control or control_signal(error)
                terminal_failed = True
            except BaseException:
                terminal_failed = True
            try:
                self._done.set()
                done_published = True
            except (KeyboardInterrupt, SystemExit) as error:
                terminal_control = terminal_control or control_signal(error)
                terminal_failed = True
            except BaseException:
                terminal_failed = True
        try:
            with self._lock:
                self._attempts = attempts
                self._record_control_locked(terminal_control)
                self._worker_drained = bool(drained and not terminal_failed)
                self._worker_failed = bool(terminal_failed or not drained)
                if self._state in {"starting", "running", "drained", "worker-failed"}:
                    self._state = "drained" if self._worker_drained else "worker-failed"
        except (KeyboardInterrupt, SystemExit):
            pass
        except BaseException:
            pass

    def _finish(
        self,
    ) -> tuple[CoturnProbeSummary | None, bool, ControlSignal | None]:
        with self._operation_lock:
            terminal_target = _terminal.transition_target(self)
            with self._lock:
                if (
                    self._state
                    in {
                        "complete",
                        "cleanup-required",
                        "terminalizing-complete",
                    }
                    or terminal_target == "complete"
                ) and type(self._summary) is CoturnProbeSummary:
                    summary = self._summary
                else:
                    summary = None
                joinable = self._state in {
                    "starting",
                    "running",
                    "drained",
                    "worker-failed",
                    "cleanup-required",
                }
            if summary is not None:
                release_failed, release_control = _terminal.settle_terminal_transition(
                    self,
                    target="complete",
                    release_claim=_recovery.release_drain_claim,
                )
                return (
                    summary if release_control is None and not release_failed else None,
                    release_failed,
                    self._retain_control(release_control),
                )
            if not joinable:
                return None, True, self._control
            joined = self._join_worker()
            with self._lock:
                control = self._control
                worker_drained = self._worker_drained
                worker_failed = self._worker_failed
            if not joined:
                with self._lock:
                    self._state = "cleanup-required"
                return None, True, self._retain_control(control)
            if control is not None or worker_failed or not worker_drained:
                cleanup_failed, cleanup_control = self._abort_joined()
                control = control or cleanup_control
                return None, cleanup_failed, self._retain_control(control)

            clean_exit = None
            process: object = None
            pump: object = None
            failed = False
            try:
                process = self._process
                pump = self._pump
                if (
                    type(process) is not AttachedCoturnProcess
                    or type(pump) is not AttachedCoturnEvidencePump
                    or not pump._matches_drain(process, self._owner_token)
                ):
                    raise CoturnRuntimeError("Coturn evidence drain failed")
                clean_exit = confirm_attached_coturn_clean_exit(process)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                failed = True
            if type(clean_exit) is not CleanCoturnExitReceipt:
                recovered, recovery_control = _recovery.recover_clean_exit(process)
                clean_exit = recovered
                control = control or recovery_control
            if type(clean_exit) is CleanCoturnExitReceipt:
                try:
                    summary = pump.finalize(clean_exit=clean_exit)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    failed = True
                if not _recovery.valid_summary(summary):
                    recovered, recovery_control = _recovery.recover_pump_summary(
                        pump,
                        process,
                        self,
                        self._owner_token,
                        clean_exit,
                    )
                    summary = recovered
                    control = control or recovery_control
            process = pump = clean_exit = None
            if not _recovery.valid_summary(summary):
                cleanup_failed, cleanup_control = self._abort_joined()
                control = control or cleanup_control
                return None, cleanup_failed, self._retain_control(control)
            with self._lock:
                self._summary = summary
            release_failed, release_control = _terminal.settle_terminal_transition(
                self,
                target="complete",
                release_claim=_recovery.release_drain_claim,
            )
            control = control or release_control
            if control is not None or failed or release_failed:
                return None, release_failed, self._retain_control(control)
            return summary, False, None

    def _cleanup(self) -> tuple[bool, ControlSignal | None]:
        with self._operation_lock:
            terminal_target = _terminal.transition_target(self)
            control: ControlSignal | None = None
            if terminal_target == "complete":
                failed, control = _terminal.settle_terminal_transition(
                    self,
                    target="complete",
                    release_claim=_recovery.release_drain_claim,
                )
                if failed:
                    return True, self._retain_control(control)
                terminal_target = None
            if terminal_target == "cleaned":
                failed, cleanup_control = _terminal.settle_terminal_transition(
                    self,
                    target="cleaned",
                    release_claim=_recovery.release_drain_claim,
                )
                control = control or cleanup_control
                return failed, self._retain_control(control)
            with self._lock:
                if self._state == "cleaned":
                    self._control = None
                    return False, None
                complete = self._state == "complete"
                started = self._thread is not None and self._state not in {
                    "created",
                    "start-failed",
                }
            if complete:
                failed, cleanup_control = _terminal.settle_terminal_transition(
                    self,
                    target="cleaned",
                    release_claim=_recovery.release_drain_claim,
                )
                control = control or cleanup_control
                return failed, self._retain_control(control)
            process_failed = False
            if started:
                process_failed, process_control = self._terminate_process()
                control = control or process_control
            joined = not started or self._join_worker()
            if not joined:
                with self._lock:
                    self._state = "cleanup-required"
                    self._record_control_locked(control)
                    control = self._control
                return True, self._retain_control(control)
            if started:
                failed, cleanup_control = self._scrub_after_process(
                    process_failed=process_failed,
                    process_control=control,
                )
            else:
                failed, cleanup_control = self._abort_joined()
            control = control or cleanup_control
            return failed, self._retain_control(control)

    def _join_worker(self) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return False
        attempts = 0
        while attempts < _MAX_JOIN_ATTEMPTS:
            attempts += 1
            try:
                if not thread.is_alive():
                    thread = None
                    return True
                thread.join(_READ_TIMEOUT_SECONDS)
                if not thread.is_alive():
                    thread = None
                    return True
            except (KeyboardInterrupt, SystemExit) as error:
                with self._lock:
                    self._record_control_locked(control_signal(error))
            except BaseException:
                break
        thread = None
        return False

    def _abort_joined(self) -> tuple[bool, ControlSignal | None]:
        unstarted, probe_failed, control = _recovery.recover_unstarted_process(self._process)
        if probe_failed:
            with self._lock:
                self._record_control_locked(control)
                self._state = "cleanup-required"
            return True, control
        if unstarted:
            return self._scrub_after_process(
                process_failed=False,
                process_control=control,
            )
        process_failed, terminate_control = self._terminate_process()
        control = control or terminate_control
        return self._scrub_after_process(
            process_failed=process_failed,
            process_control=control,
        )

    def _scrub_after_process(
        self,
        *,
        process_failed: bool,
        process_control: ControlSignal | None,
    ) -> tuple[bool, ControlSignal | None]:
        if process_failed:
            with self._lock:
                self._record_control_locked(process_control)
                self._state = "cleanup-required"
            return True, process_control
        pump = self._pump
        pump_failed = type(pump) is not AttachedCoturnEvidencePump
        pump_control: ControlSignal | None = None
        if type(pump) is AttachedCoturnEvidencePump:
            try:
                process = self._process
                if type(process) is not AttachedCoturnProcess:
                    raise CoturnRuntimeError("Coturn evidence drain cleanup failed")
                pump_failed, pump_control = pump._abort_drain(
                    process,
                    self._owner_token,
                )
            except (KeyboardInterrupt, SystemExit) as error:
                pump_control = control_signal(error)
                pump_failed = True
            except BaseException:
                pump_failed = True
        with self._lock:
            self._record_control_locked(pump_control)
        process = None
        failed = bool(pump_failed or process_failed)
        control = process_control or pump_control
        if not failed:
            terminal_failed, terminal_control = _terminal.settle_terminal_transition(
                self,
                target="cleaned",
                release_claim=_recovery.release_drain_claim,
            )
            failed = terminal_failed
            release_control = terminal_control
            control = control or release_control
        terminal_pending = _terminal.transition_target(self)
        with self._lock:
            self._record_control_locked(control)
            if failed and terminal_pending is None:
                self._state = "cleanup-required"
        pump = None
        return failed, control

    def _terminate_process(self) -> tuple[bool, ControlSignal | None]:
        process = self._process
        failed = type(process) is not AttachedCoturnProcess
        control: ControlSignal | None = None
        if type(process) is AttachedCoturnProcess:
            try:
                process.terminate()
                failed = False
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
                failed = True
            except BaseException:
                failed = True
        with self._lock:
            self._record_control_locked(control)
        process = None
        return failed, control

    def _consume_control(
        self,
        control: ControlSignal | None,
    ) -> ControlSignal | None:
        with self._lock:
            observed = self._control or control
            self._control = None
            return observed

    def _retain_control(self, control: ControlSignal | None) -> ControlSignal | None:
        with self._lock:
            self._record_control_locked(control)
            return self._control

    def _record_control_locked(self, control: ControlSignal | None) -> None:
        if self._control is None and control is not None:
            self._control = control

    def __reduce__(self) -> object:
        raise TypeError("Coturn evidence drain cannot be copied or serialized")


from scripts.voice_pipecat_e2e_coturn_runtime_drain_factory import (  # noqa: E402, F401
    new_attached_coturn_evidence_drain,
)


def start_attached_coturn_evidence_drain(drain: AttachedCoturnEvidenceDrain) -> None:
    if type(drain) is not AttachedCoturnEvidenceDrain:
        drain = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence drain is invalid")
    failed = True
    control: ControlSignal | None = None
    try:
        failed, control = drain._start()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    with drain._lock:
        start_failed = drain._state in {"created", "start-failed"}
    cleanup_failed = False
    if (failed or control is not None) and start_failed:
        try:
            cleanup_failed, cleanup_control = drain._cleanup()
            control = control or cleanup_control
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                control = control or control_signal(error)
            cleanup_failed = True
    if control is not None:
        control = drain._consume_control(control)
        public_authority = None
        if cleanup_failed:
            public_authority, retain_control = _recovery.retain_cleanup_authority(drain)
            control = control or retain_control
        drain = None  # type: ignore[assignment]
        raise_control(control, public_authority)  # type: ignore[arg-type]
    if failed:
        if cleanup_failed:
            public_authority, control = _recovery.retain_cleanup_authority(drain)
            if control is not None:
                drain = None  # type: ignore[assignment]
                raise_control(control, public_authority)
            if public_authority is not None:
                drain = None  # type: ignore[assignment]
                raise CoturnEvidenceDrainCleanupRequired(public_authority) from None
        drain = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence drain failed") from None


def finish_attached_coturn_evidence_drain(
    drain: AttachedCoturnEvidenceDrain,
) -> CoturnProbeSummary:
    if type(drain) is not AttachedCoturnEvidenceDrain:
        drain = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence drain is invalid")
    summary: CoturnProbeSummary | None = None
    cleanup_failed = True
    control: ControlSignal | None = None
    try:
        summary, cleanup_failed, control = drain._finish()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        with drain._lock:
            cleanup_failed = drain._state not in {"complete", "cleaned"}
    except BaseException:
        with drain._lock:
            cleanup_failed = drain._state not in {"complete", "cleaned"}
    if control is not None:
        control = drain._consume_control(control)
        public_authority = None
        if cleanup_failed:
            public_authority, retain_control = _recovery.retain_cleanup_authority(drain)
            control = control or retain_control
        drain = summary = None  # type: ignore[assignment]
        raise_control(control, public_authority)
    if type(summary) is CoturnProbeSummary:
        release_failed, release_control = _recovery.release_cleanup_authority(drain)
        if release_control is not None:
            public_authority = drain._cleanup_authority if release_failed else None
            drain = summary = None  # type: ignore[assignment]
            raise_control(release_control, public_authority)
        if release_failed:
            public_authority, control = _recovery.retain_cleanup_authority(drain)
            drain = summary = None  # type: ignore[assignment]
            if control is not None:
                raise_control(control, public_authority)
            raise CoturnEvidenceDrainCleanupRequired(public_authority) from None  # type: ignore[arg-type]
        return summary
    if cleanup_failed:
        public_authority, control = _recovery.retain_cleanup_authority(drain)
        if control is not None:
            drain = None  # type: ignore[assignment]
            raise_control(control, public_authority)
        if public_authority is not None:
            drain = None  # type: ignore[assignment]
            raise CoturnEvidenceDrainCleanupRequired(public_authority) from None
    drain = None  # type: ignore[assignment]
    raise CoturnRuntimeError("Coturn evidence drain failed") from None


def cleanup_attached_coturn_evidence_drain(
    authority: AttachedCoturnEvidenceDrain | CoturnEvidenceDrainCleanupAuthority,
) -> None:
    owned, resolve_failed, control = _recovery.resolve_cleanup_authority(authority)
    if control is not None:
        retained = authority if type(authority) is CoturnEvidenceDrainCleanupAuthority else None
        authority = owned = None  # type: ignore[assignment]
        raise_control(control, retained)
    if resolve_failed:
        authority = owned = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence drain cleanup authority is invalid")
    if owned is None and type(authority) is CoturnEvidenceDrainCleanupAuthority:
        return
    if type(owned) is not AttachedCoturnEvidenceDrain:
        authority = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn evidence drain cleanup authority is invalid")
    failed = True
    control = None
    public_authority: CoturnEvidenceDrainCleanupAuthority | None = None
    try:
        with owned._operation_lock:
            failed, control = owned._cleanup()
            if failed:
                public_authority, retain_control = _recovery.retain_cleanup_authority(owned)
                control = control or retain_control
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        with owned._lock:
            failed = owned._state not in {"complete", "cleaned"}
    except BaseException:
        with owned._lock:
            failed = owned._state not in {"complete", "cleaned"}
    if not failed:
        release_failed, release_control = _recovery.release_cleanup_authority(owned)
        failed = release_failed
        control = control or release_control
    if control is not None:
        control = owned._consume_control(control)
        if failed and public_authority is None:
            public_authority, retain_control = _recovery.retain_cleanup_authority(owned)
            control = control or retain_control
        owned = None  # type: ignore[assignment]
        authority = None  # type: ignore[assignment]
        raise_control(control, public_authority)
    if failed:
        if public_authority is None:
            public_authority, control = _recovery.retain_cleanup_authority(owned)
            if control is not None:
                owned = authority = None  # type: ignore[assignment]
                raise_control(control, public_authority)
        if public_authority is None:
            owned = None  # type: ignore[assignment]
            authority = None  # type: ignore[assignment]
            raise CoturnRuntimeError("Coturn evidence drain cleanup failed") from None
        owned = None  # type: ignore[assignment]
        authority = None  # type: ignore[assignment]
        raise CoturnEvidenceDrainCleanupRequired(public_authority) from None

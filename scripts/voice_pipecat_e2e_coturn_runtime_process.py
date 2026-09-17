"""Attached Coturn stream boundary and clean-exit proof."""

from __future__ import annotations

import math
import threading

from scripts import voice_pipecat_e2e_coturn_runtime_process_claims as _claims
from scripts import voice_pipecat_e2e_coturn_runtime_process_registry as _registry
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ValidatedContainer,
)
from scripts.voice_pipecat_e2e_coturn_host import AttachedCommand, CommandRequest, CommandRunner
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_MAX_CHUNK_BYTES = 4_096
_PROCESS_TOKEN = object()
_EXIT_TOKEN = object()
_UNPUBLISHED_TOKEN = object()
_ACTIVE_RUNS = _registry._ACTIVE_RUNS
_active_run_matches = _registry._active_run_matches
_container_recovery_is_allowed = _registry._container_recovery_is_allowed
_register_active_run = _registry._register_active_run
_release_active_run = _registry._release_active_run


class UnpublishedAttachedCleanupAuthority:
    """Opaque retry owner for a handle that could not be safely published."""

    __slots__ = ("_handle", "_lock", "_runner", "_state")

    def __init__(self, token: object, *, runner: object) -> None:
        if token is not _UNPUBLISHED_TOKEN:
            raise TypeError("Coturn attached cleanup authority is factory-owned")
        self._handle: AttachedCommand | None = None
        self._runner = runner
        self._state = "armed"
        self._lock = threading.Lock()

    def _adopt(self, handle: object) -> bool:
        with self._lock:
            if self._state != "armed" or handle is None:
                return False
            self._handle = handle  # type: ignore[assignment]
            self._state = "retained"
            return True

    def _settle(self) -> tuple[bool, ControlSignal | None]:
        with self._lock:
            if self._state == "settled":
                self._handle = None
                self._runner = None
                return False, None
            if self._state == "terminating" and self._runner is None:
                self._state = "settled"
                self._handle = None
                return False, None
            if self._state not in {"armed", "retained", "terminating"} or self._runner is None:
                return True, None
            handle = self._handle
            runner = self._runner
            terminate_handle = self._state != "terminating"
            self._state = "terminating"
            result: object = None
            control: ControlSignal | None = None
            if terminate_handle and handle is not None:
                try:
                    handle.terminate()
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                except BaseException:
                    pass
            try:
                result = runner.settle_owned()
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                pass
            failed = type(result) is not bool or result is not True
            result = None
            if failed:
                self._state = "terminating"
            else:
                self._state = "settled"
                self._handle = None
                self._runner = None
            handle = runner = None
        return failed, control

    def __repr__(self) -> str:
        return "UnpublishedAttachedCleanupAuthority()"


class CoturnAttachedCleanupRequired(CoturnRuntimeError):
    """Fixed failure carrying only an opaque unpublished-handle retry owner."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: UnpublishedAttachedCleanupAuthority) -> None:
        super().__init__("Coturn attached cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> UnpublishedAttachedCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnAttachedCleanupRequired('Coturn attached cleanup failed')"


class CoturnAttachedProcessCleanupRequired(CoturnRuntimeError):
    """Fixed failure retaining the caller-owned published process for retry."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: AttachedCoturnProcess) -> None:
        super().__init__("Coturn attached process cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> AttachedCoturnProcess:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnAttachedProcessCleanupRequired('Coturn attached process cleanup failed')"


class CleanCoturnExitReceipt:
    """Opaque proof that one exact attached container drained and exited zero."""

    __slots__ = ("_authority", "_process_identity")

    def __init__(
        self,
        token: object,
        *,
        authority: ContainerCleanupAuthority,
        process_identity: object,
    ) -> None:
        if token is not _EXIT_TOKEN:
            raise TypeError("Coturn clean-exit receipt is factory-owned")
        self._authority = authority
        self._process_identity = process_identity

    def _matches(
        self,
        *,
        authority: ContainerCleanupAuthority,
        process_identity: object,
    ) -> bool:
        return self._authority is authority and self._process_identity is process_identity

    def __copy__(self) -> CleanCoturnExitReceipt:
        raise TypeError("Coturn clean-exit receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> CleanCoturnExitReceipt:
        raise TypeError("Coturn clean-exit receipt cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn clean-exit receipt cannot be serialized")

    def __repr__(self) -> str:
        return "CleanCoturnExitReceipt()"


class AttachedCoturnProcess:
    """Validate transient structural chunks without aggregating a transcript."""

    __slots__ = (
        "_authority",
        "_clean_receipt",
        "_failed_output",
        "_handle",
        "_identity",
        "_lock",
        "_maximum_output_bytes",
        "_observed_bytes",
        "_observed_drained",
        "_pump_claim",
        "_pump_operation_lock",
        "_pump_owner",
        "_runner",
        "_runner_settled",
        "_state",
        "_stop_operation_lock",
        "_stop_receipt",
        "_terminated",
    )

    def __init__(
        self,
        token: object,
        *,
        authority: ContainerCleanupAuthority,
    ) -> None:
        if token is not _PROCESS_TOKEN:
            raise TypeError("Attached Coturn process is factory-owned")
        self._handle: AttachedCommand | None = None
        self._runner: CommandRunner | None = None
        self._runner_settled = False
        self._maximum_output_bytes = 0
        self._authority = authority
        self._identity = object()
        self._observed_bytes = 0
        self._observed_drained = False
        self._failed_output = False
        self._terminated = False
        self._state = "empty"
        self._clean_receipt: CleanCoturnExitReceipt | None = None
        self._pump_claim = _claims.new_pump_claim()
        self._pump_operation_lock = threading.RLock()
        self._pump_owner = object()
        self._stop_receipt: object | None = None
        self._stop_operation_lock = threading.Lock()
        self._lock = threading.RLock()

    def _publish(
        self,
        *,
        handle: AttachedCommand,
        runner: CommandRunner,
        request: CommandRequest,
    ) -> bool:
        control: ControlSignal | None = None
        published = False
        failed = False
        attempts = 0
        while not published and not failed and attempts < 8:
            attempts += 1
            try:
                with self._lock:
                    if self._state == "running":
                        published = _active_run_matches(self._authority, self._identity)
                    elif self._state == "publishing":
                        if not (
                            _active_run_matches(self._authority, self._identity)
                            or _register_active_run(self._authority, self._identity)
                        ):
                            failed = True
                        else:
                            self._state = "running"
                            published = True
                    elif self._state != "empty" or handle is None or runner is None:
                        failed = True
                    else:
                        self._handle = handle
                        self._runner = runner
                        self._maximum_output_bytes = request.maximum_output_bytes
                        self._state = "publishing"
                        if not _register_active_run(self._authority, self._identity):
                            self._state = "empty"
                            self._handle = self._runner = None
                            failed = True
                        else:
                            self._state = "running"
                            published = True
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                failed = True
                if self._state == "empty":
                    self._handle = self._runner = None
        handle = runner = request = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control)
        return bool(published and not failed)

    def _matches_container(self, container: ValidatedContainer) -> bool:
        with self._lock:
            return (
                type(container) is ValidatedContainer
                and container.authority is self._authority
                and self._state == "empty"
            )

    @property
    def _started(self) -> bool:
        with self._lock:
            return self._state != "empty"

    def _retire_unstarted_for_drain_cleanup(self) -> bool:
        """Atomically retire an exact destination that owns no runtime effect."""

        with self._lock:
            if self._state == "drain-retired":
                return self._handle is None and self._runner is None
            if (
                self._state != "empty"
                or self._handle is not None
                or self._runner is not None
                or self._clean_receipt is not None
                or self._terminated
                or _active_run_matches(self._authority, self._identity)
            ):
                return False
            self._state = "drain-retired"
            return True

    def read_chunk(self, *, timeout_seconds: float) -> bytes | None:
        """Return one validated stdout chunk, or ``None`` after a bounded wait."""

        timeout_control: ControlSignal | None = None
        try:
            timeout_valid = _valid_read_timeout(timeout_seconds)
        except (KeyboardInterrupt, SystemExit) as error:
            timeout_control = control_signal(error)
            timeout_valid = False
        except BaseException:
            timeout_valid = False
        if not timeout_valid:
            timeout_seconds = 0.0
            self = None  # type: ignore[assignment]
            if timeout_control is not None:
                raise_control(timeout_control)
            raise CoturnRuntimeError("Coturn attached read timeout is invalid")
        with self._lock:
            if self._state != "running" or self._handle is None:
                self = None  # type: ignore[assignment]
                raise CoturnRuntimeError("Coturn attached process is unavailable")
            if self._observed_drained:
                return None
            chunk, failure, control = self._read_chunk_outcome(float(timeout_seconds))
        if control is not None:
            chunk = None
            self = None  # type: ignore[assignment]
            raise_control(control)
        if failure is not None:
            chunk = None
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError(failure) from None
        return chunk

    @property
    def drained(self) -> bool:
        """Return only an exact structural drain fact from the attached owner."""

        with self._lock:
            if self._state != "running" or self._handle is None:
                self = None  # type: ignore[assignment]
                raise CoturnRuntimeError("Coturn attached process is unavailable")
            value, failure, control = self._drained_outcome()
        if control is not None:
            self = None  # type: ignore[assignment]
            raise_control(control)
        if failure is not None:
            self = None  # type: ignore[assignment]
            raise CoturnRuntimeError(failure) from None
        return value

    def terminate(self) -> None:
        """Request the concrete owner's bounded termination path at most once."""

        control: ControlSignal | None = None
        failed = False
        with self._lock:
            if self._state in {"terminated", "clean", "drain-retired"}:
                try:
                    _release_active_run(self._authority, self._identity)
                    self._handle = None
                    self._runner = None
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                    failed = True
                except BaseException:
                    failed = True
            elif self._state not in {"publishing", "running", "terminating"}:
                raise CoturnRuntimeError("Coturn attached termination failed")
            else:
                terminate_handle = self._state != "terminating"
                self._state = "terminating"
                self._terminated = True
                handle = self._handle
                runner = self._runner
                result: object = object()
                settled: object = None
                failed = handle is None or runner is None
                if terminate_handle and handle is not None:
                    try:
                        result = handle.terminate()
                    except (KeyboardInterrupt, SystemExit) as error:
                        control = control_signal(error)
                    except BaseException:
                        failed = True
                    if result is not None:
                        failed = True
                if runner is not None:
                    try:
                        settled = runner.settle_owned()
                    except (KeyboardInterrupt, SystemExit) as error:
                        control = control or control_signal(error)
                    except BaseException:
                        failed = True
                    if type(settled) is not bool or settled is not True:
                        failed = True
                result = settled = None
                if not failed:
                    try:
                        self._state = "terminated"
                        self._handle = None
                        self._runner = None
                        _release_active_run(self._authority, self._identity)
                    except (KeyboardInterrupt, SystemExit) as error:
                        control = control or control_signal(error)
                        failed = True
                    except BaseException:
                        failed = True
                handle = runner = None
        if control is not None:
            recovery = self if failed else None
            self = None  # type: ignore[assignment]
            raise_control(control, recovery)
        if failed:
            recovery = self
            self = None  # type: ignore[assignment]
            raise CoturnAttachedProcessCleanupRequired(recovery) from None

    def _confirm_clean_exit(self) -> CleanCoturnExitReceipt:
        with self._lock:
            if self._clean_receipt is not None:
                self._state = "clean"
                _release_active_run(self._authority, self._identity)
                self._handle = None
                self._runner = None
                return self._clean_receipt
            if self._state != "running" or self._failed_output or self._terminated:
                raise CoturnRuntimeError("Coturn attached clean exit was not proven")
            if not self._runner_settled:
                drained, failure, control = self._drained_outcome()
                if control is not None:
                    raise_control(control)
                if failure is not None or not drained:
                    raise CoturnRuntimeError("Coturn attached clean exit was not proven") from None
                returncode, failure, control = self._poll_outcome()
                if control is not None:
                    raise_control(control)
                if failure is not None or returncode != 0:
                    raise CoturnRuntimeError("Coturn attached clean exit was not proven") from None
                runner = self._runner
                settled: object = None
                if runner is None:
                    raise CoturnRuntimeError("Coturn attached clean exit was not proven")
                try:
                    settled = runner.settle_owned()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    raise CoturnRuntimeError("Coturn attached clean exit was not proven") from None
                if type(settled) is not bool or settled is not True:
                    settled = runner = None
                    raise CoturnRuntimeError("Coturn attached clean exit was not proven")
                self._runner_settled = True
                settled = runner = None
            self._clean_receipt = CleanCoturnExitReceipt(
                _EXIT_TOKEN,
                authority=self._authority,
                process_identity=self._identity,
            )
            self._state = "clean"
            _release_active_run(self._authority, self._identity)
            self._handle = None
            self._runner = None
            return self._clean_receipt

    def _read_chunk_outcome(
        self,
        timeout_seconds: float,
    ) -> tuple[bytes | None, str | None, ControlSignal | None]:
        raw: object = None
        stream: object = None
        data: object = None
        control: ControlSignal | None = None
        failure: str | None = None
        try:
            handle = self._handle
            if handle is None:
                raise CoturnRuntimeError("Coturn attached chunk is invalid")
            raw = handle.read_chunk(timeout_seconds=timeout_seconds)
            if raw is None:
                return None, None, None
            stream = object.__getattribute__(raw, "stream")
            data = object.__getattribute__(raw, "data")
            if (
                type(stream) is not str
                or stream not in {"stdout", "stderr"}
                or type(data) is not bytes
                or not 1 <= len(data) <= _MAX_CHUNK_BYTES
            ):
                failure = "Coturn attached chunk is invalid"
            elif self._observed_bytes + len(data) > self._maximum_output_bytes:
                failure = "Coturn attached output is oversized"
            else:
                self._observed_bytes += len(data)
                if stream == "stderr":
                    failure = "Coturn attached stderr is forbidden"
                else:
                    return data, None, None
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            failure = "Coturn attached chunk is invalid"
        finally:
            raw = None
            stream = None
            if failure is not None or control is not None:
                self._failed_output = True
                data = None
        return None, failure, control

    def _drained_outcome(self) -> tuple[bool, str | None, ControlSignal | None]:
        value: object = None
        control: ControlSignal | None = None
        failure: str | None = None
        try:
            handle = self._handle
            if handle is None:
                raise CoturnRuntimeError("Coturn attached drain check failed")
            value = handle.drained
            if type(value) is not bool:
                failure = "Coturn attached drain state is invalid"
            elif self._observed_drained and value is False:
                failure = "Coturn attached drain state is invalid"
            elif value:
                self._observed_drained = True
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            failure = "Coturn attached drain check failed"
        if failure is not None or control is not None:
            self._failed_output = True
        result = value if type(value) is bool else False
        value = None
        return result, failure, control

    def _poll_outcome(self) -> tuple[int | None, str | None, ControlSignal | None]:
        value: object = None
        control: ControlSignal | None = None
        failure: str | None = None
        try:
            handle = self._handle
            if handle is None:
                raise CoturnRuntimeError("Coturn attached process poll failed")
            value = handle.poll()
            if value is not None and (type(value) is not int):
                failure = "Coturn attached process state is invalid"
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            failure = "Coturn attached process poll failed"
        if failure is not None or control is not None:
            self._failed_output = True
        result = value if value is None or type(value) is int else None
        value = None
        return result, failure, control

    def _matches_exit(self, receipt: CleanCoturnExitReceipt) -> bool:
        return type(receipt) is CleanCoturnExitReceipt and receipt._matches(
            authority=self._authority,
            process_identity=self._identity,
        )

    def _matches_topology(self, topology: object) -> bool:
        return self._authority.plan.network.authority.plan.topology == topology

    def _lifecycle_identity(self, authority: ContainerCleanupAuthority) -> object | None:
        return (
            self._identity
            if self._authority is authority and self._state in {"running", "clean"}
            else None
        )

    @property
    def _container_authority(self) -> ContainerCleanupAuthority:
        return self._authority

    def __copy__(self) -> AttachedCoturnProcess:
        raise TypeError("Attached Coturn process cannot be copied")

    def __deepcopy__(self, _memo: object) -> AttachedCoturnProcess:
        raise TypeError("Attached Coturn process cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Attached Coturn process cannot be serialized")

    def __repr__(self) -> str:
        return "AttachedCoturnProcess()"


def _new_attached_coturn_process(
    *,
    authority: ContainerCleanupAuthority,
) -> AttachedCoturnProcess:
    return AttachedCoturnProcess(
        _PROCESS_TOKEN,
        authority=authority,
    )


def new_attached_coturn_process(
    container: ValidatedContainer,
) -> AttachedCoturnProcess:
    """Create a harmless caller-owned destination before attached start."""

    if type(container) is not ValidatedContainer:
        container = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn attached process is invalid")
    authority = container.authority
    if type(authority) is not ContainerCleanupAuthority:
        container = authority = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn attached process is invalid")
    process = _new_attached_coturn_process(authority=authority)
    container = authority = None  # type: ignore[assignment]
    return process


def _new_unpublished_attached_cleanup_authority(
    runner: object,
) -> UnpublishedAttachedCleanupAuthority:
    return UnpublishedAttachedCleanupAuthority(_UNPUBLISHED_TOKEN, runner=runner)


def cleanup_unpublished_attached(
    authority: UnpublishedAttachedCleanupAuthority,
) -> None:
    """Retry synchronous settlement of one unpublished attached handle."""

    if type(authority) is not UnpublishedAttachedCleanupAuthority:
        authority = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn attached cleanup authority is invalid")
    failed = True
    control: ControlSignal | None = None
    try:
        failed, control = authority._settle()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    if control is not None:
        retained = authority if failed else None
        authority = None  # type: ignore[assignment]
        raise_control(control, retained)
    if failed:
        recovery = authority
        authority = None  # type: ignore[assignment]
        raise CoturnAttachedCleanupRequired(recovery) from None
    authority = None  # type: ignore[assignment]


def confirm_attached_coturn_clean_exit(
    process: AttachedCoturnProcess,
) -> CleanCoturnExitReceipt:
    """Prove drain first and only then accept the concrete owner's ``poll() == 0``."""

    if type(process) is not AttachedCoturnProcess:
        process = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn attached process is invalid")
    receipt: CleanCoturnExitReceipt | None = None
    control: ControlSignal | None = None
    try:
        receipt = process._confirm_clean_exit()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    process = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn attached clean exit was not proven") from None
    return receipt


def _valid_read_timeout(value: object) -> bool:
    return bool(type(value) is float and math.isfinite(value) and 0.01 <= value <= 60.0)


__all__ = [
    "AttachedCoturnProcess",
    "CleanCoturnExitReceipt",
    "CoturnAttachedCleanupRequired",
    "CoturnAttachedProcessCleanupRequired",
    "UnpublishedAttachedCleanupAuthority",
    "cleanup_unpublished_attached",
    "confirm_attached_coturn_clean_exit",
    "new_attached_coturn_process",
]

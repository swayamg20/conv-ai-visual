"""Normal running, stop, drain-gated removal, and absence lifecycle."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_docker import (
    decode_inspection_result,
    translate_created_id,
)
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ValidatedContainerRemoval,
    ValidatedRunningContainer,
    build_container_absence_request,
    build_container_inspect_request,
    build_container_remove_request,
    build_container_stop_request,
    validate_container_cleanup_target,
    validate_container_for_start,
    validate_container_removal_target,
    validate_container_running,
    validate_container_stop_target,
)
from scripts.voice_pipecat_e2e_coturn_host import CommandRunner, TrustedHostTools, execute_checked
from scripts.voice_pipecat_e2e_coturn_runtime_container_absence import (
    ContainerAbsenceReceipt,
    RemovedContainerReceipt,
    _persist_container_absence,
    finalize_container_absence,
)
from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    CoturnDirectorySyncCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_runtime_private_cleanup import (
    _RuntimePrivateCleanupCapture,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
    _release_active_run,
)
from scripts.voice_pipecat_e2e_coturn_runtime_readiness import RuntimeReadinessBudget
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_STOP_TOKEN = object()
_RECOVERY_TOKEN = object()


class StoppedCoturnReceipt:
    """Opaque proof that stop targeted a freshly validated running container."""

    __slots__ = (
        "_authority",
        "_lock",
        "_process_identity",
        "_removal_receipt",
        "_removal_target",
    )

    def __init__(
        self,
        token: object,
        *,
        authority: ContainerCleanupAuthority,
        process_identity: object,
        removal_target: ValidatedContainerRemoval | None = None,
    ) -> None:
        if token is not _STOP_TOKEN:
            raise TypeError("Coturn stop receipt is factory-owned")
        self._authority = authority
        self._process_identity = process_identity
        self._removal_target = removal_target
        self._removal_receipt: ContainerAbsenceReceipt | None = None
        self._lock = threading.Lock()

    def _matches_process(
        self,
        authority: ContainerCleanupAuthority,
        process_identity: object,
    ) -> bool:
        return self._authority is authority and self._process_identity is process_identity

    def _publish_removal_target(self, target: ValidatedContainerRemoval) -> bool:
        with self._lock:
            if self._removal_target is None:
                self._removal_target = target
            return self._removal_target is target

    def _matches_exit(self, clean_exit: CleanCoturnExitReceipt) -> bool:
        return clean_exit._matches(
            authority=self._authority,
            process_identity=self._process_identity,
        )

    def __copy__(self) -> StoppedCoturnReceipt:
        raise TypeError("Coturn stop receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> StoppedCoturnReceipt:
        raise TypeError("Coturn stop receipt cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn stop receipt cannot be serialized")

    def __repr__(self) -> str:
        return "StoppedCoturnReceipt()"


class RecoveredContainerCleanupAuthority:
    """Explicit crash-recovery owner, separate from the normal drain path."""

    __slots__ = ("_authority", "_lock", "_receipt", "_removal_target")

    def __init__(
        self,
        token: object,
        *,
        authority: ContainerCleanupAuthority,
        removal_target: ValidatedContainerRemoval | None,
    ) -> None:
        if token is not _RECOVERY_TOKEN:
            raise TypeError("Coturn recovered cleanup authority is factory-owned")
        self._authority = authority
        self._removal_target = removal_target
        self._receipt: ContainerAbsenceReceipt | None = None
        self._lock = threading.Lock()

    @property
    def container_id(self) -> str:
        return self._authority.container_id

    def __repr__(self) -> str:
        return "RecoveredContainerCleanupAuthority()"


def validate_owned_container_running(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: ContainerCleanupAuthority,
    readiness_budget: RuntimeReadinessBudget,
) -> ValidatedRunningContainer:
    """Retry only an exact created state, then prove full running parity."""

    result: ValidatedRunningContainer | None = None
    control: ControlSignal | None = None
    inspected: object = None
    inspection: object = None
    try:
        if (
            type(authority) is not ContainerCleanupAuthority
            or type(readiness_budget) is not RuntimeReadinessBudget
        ):
            raise CoturnRuntimeError("Coturn running container validation failed")
        cached = readiness_budget._cached_container(authority)
        if cached is not None:
            if type(cached) is not ValidatedRunningContainer:
                raise CoturnRuntimeError("Coturn running container validation failed")
            result = cached
        while result is None:
            request = readiness_budget._prepare_request(
                "container",
                build_container_inspect_request(tools, authority.plan, authority.container_id),
            )
            inspected = execute_checked(
                runner,
                request,
                failure="Coturn running container inspection failed",
            )
            request = None
            inspection = decode_inspection_result(inspected, label="container")
            inspected = None
            candidate: ValidatedRunningContainer | None = None
            try:
                candidate = validate_container_running(authority, inspection)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                candidate = None
            if candidate is not None:
                published = readiness_budget._container_ready(authority, candidate)
                if type(published) is not ValidatedRunningContainer:
                    raise CoturnRuntimeError("Coturn running container validation failed")
                result = published
                published = None
                candidate = None
                break
            validate_container_for_start(authority, inspection)
            inspection = None
            readiness_budget._retry("container")
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    if result is None and control is None and type(readiness_budget) is RuntimeReadinessBudget:
        readiness_budget._fail()
    inspected = inspection = None
    runner = tools = authority = readiness_budget = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if result is None:
        raise CoturnRuntimeError("Coturn running container validation failed") from None
    return result


def stop_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    running: ValidatedRunningContainer,
    process: AttachedCoturnProcess,
) -> StoppedCoturnReceipt:
    """Stop or reconcile an exact stopped target and bind it to one process run."""

    receipt: StoppedCoturnReceipt | None = None
    control: ControlSignal | None = None
    inspected: object = None
    stopped_result: object = None
    authority: ContainerCleanupAuthority | None = None
    target: object = None
    cleanup_target: object = None
    removal_target: ValidatedContainerRemoval | None = None
    inspection: object = None
    process_identity: object | None = None
    candidate: object = None
    try:
        if (
            type(running) is not ValidatedRunningContainer
            or type(process) is not AttachedCoturnProcess
        ):
            raise CoturnRuntimeError("Coturn container stop failed")
        authority = running.authority
        process_identity = process._lifecycle_identity(authority)
        if process_identity is None:
            raise CoturnRuntimeError("Coturn container stop failed")
        with process._stop_operation_lock:
            candidate = process._stop_receipt
            if candidate is None:
                receipt = StoppedCoturnReceipt(
                    _STOP_TOKEN,
                    authority=authority,
                    process_identity=process_identity,
                )
                process._stop_receipt = receipt
            elif type(candidate) is StoppedCoturnReceipt and candidate._matches_process(
                authority, process_identity
            ):
                receipt = candidate
            else:
                raise CoturnRuntimeError("Coturn container stop failed")
            if receipt._removal_target is None:
                inspected = execute_checked(
                    runner,
                    build_container_inspect_request(
                        tools,
                        authority.plan,
                        authority.container_id,
                    ),
                    failure="Coturn container stop inspection failed",
                )
                inspection = decode_inspection_result(inspected, label="container")
                inspected = None
                cleanup_target = validate_container_cleanup_target(authority, inspection)
                if cleanup_target.running:
                    target = validate_container_stop_target(authority, inspection)
                    stopped_result = execute_checked(
                        runner,
                        build_container_stop_request(tools, target),
                        failure="Coturn container stop failed",
                    )
                    if (
                        stopped_result.stdout != (authority.container_id + "\n").encode("ascii")
                        or stopped_result.stderr != b""
                    ):
                        raise CoturnRuntimeError("Coturn container stop failed")
                    stopped_result = target = inspection = None
                    inspected = execute_checked(
                        runner,
                        build_container_inspect_request(
                            tools,
                            authority.plan,
                            authority.container_id,
                        ),
                        failure="Coturn stopped container inspection failed",
                    )
                    inspection = decode_inspection_result(inspected, label="container")
                    inspected = None
                removal_target = validate_container_removal_target(authority, inspection)
                if not receipt._publish_removal_target(removal_target):
                    raise CoturnRuntimeError("Coturn container stop failed")
                removal_target = None
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    valid_receipt = bool(
        type(receipt) is StoppedCoturnReceipt
        and type(receipt._removal_target) is ValidatedContainerRemoval
    )
    inspected = stopped_result = target = cleanup_target = removal_target = candidate = None
    inspection = process_identity = authority = None
    runner = tools = running = process = None  # type: ignore[assignment]
    if control is not None:
        receipt = None
        raise_control(control)
    if not valid_receipt:
        receipt = None
        raise CoturnRuntimeError("Coturn container stop failed") from None
    assert receipt is not None
    return receipt


def remove_stopped_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    stopped: StoppedCoturnReceipt,
    clean_exit: CleanCoturnExitReceipt,
) -> ContainerAbsenceReceipt:
    """Remove only after exact stop, complete drain, zero exit, and stopped inspect."""

    receipt: ContainerAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    control: ControlSignal | None = None
    inspected: object = None
    absence: object = None
    removed_result: object = None
    authority: ContainerCleanupAuthority | None = None
    target: object = None
    inspect_failed = False
    try:
        if type(stopped) is not StoppedCoturnReceipt:
            raise CoturnRuntimeError("Coturn stopped container receipt is invalid")
        authority = stopped._authority
        if (
            type(clean_exit) is not CleanCoturnExitReceipt
            or type(stopped._removal_target) is not ValidatedContainerRemoval
            or not stopped._matches_exit(clean_exit)
        ):
            raise CoturnRuntimeError("Coturn stopped container receipt is invalid")
        with stopped._lock:
            if stopped._removal_receipt is not None:
                if type(stopped._removal_receipt) is not ContainerAbsenceReceipt:
                    raise CoturnRuntimeError("Coturn stopped container receipt is invalid")
                receipt = stopped._removal_receipt
            else:
                target = stopped._removal_target
                try:
                    inspected = execute_checked(
                        runner,
                        build_container_inspect_request(
                            tools,
                            authority.plan,
                            authority.container_id,
                        ),
                        failure="Coturn stopped container inspection failed",
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    inspect_failed = True
                if not inspect_failed:
                    target = validate_container_removal_target(
                        authority,
                        decode_inspection_result(inspected, label="container"),
                    )
                    inspected = None
                    removed_result = execute_checked(
                        runner,
                        build_container_remove_request(tools, target),
                        failure="Coturn container removal failed",
                    )
                    if (
                        removed_result.stdout != (authority.container_id + "\n").encode("ascii")
                        or removed_result.stderr != b""
                    ):
                        raise CoturnRuntimeError("Coturn container removal failed")
                    removed_result = None
                absence = execute_checked(
                    runner,
                    build_container_absence_request(tools, target),
                    failure="Coturn container absence check failed",
                )
                if absence.stdout != b"" or absence.stderr != b"":
                    raise CoturnRuntimeError("Coturn container removal was not confirmed")
                _release_active_run(authority, stopped._process_identity)
                receipt = _persist_container_absence(
                    plan=authority.plan,
                    container_id=authority.container_id,
                )
                stopped._removal_receipt = receipt
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        capture.capture_error(error)
    inspected = absence = removed_result = target = authority = None
    runner = tools = stopped = clean_exit = None  # type: ignore[assignment]
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if control is not None:
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn stopped container removal failed") from None
    return receipt


def _new_recovered_container_cleanup_authority(
    *,
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> RecoveredContainerCleanupAuthority:
    cleanup_target = validate_container_cleanup_target(authority, inspection)
    removal_target = (
        None if cleanup_target.running else validate_container_removal_target(authority, inspection)
    )
    return RecoveredContainerCleanupAuthority(
        _RECOVERY_TOKEN,
        authority=authority,
        removal_target=removal_target,
    )


def cleanup_recovered_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    recovery: RecoveredContainerCleanupAuthority,
) -> ContainerAbsenceReceipt:
    """Reconcile irreversible stop/remove windows and prove exact absence."""

    receipt: ContainerAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    control: ControlSignal | None = None
    try:
        if type(recovery) is not RecoveredContainerCleanupAuthority:
            raise CoturnRuntimeError("Coturn recovered container cleanup failed")
        with recovery._lock:
            if recovery._receipt is not None:
                receipt = recovery._receipt
            else:
                receipt = _cleanup_recovered_owned_container(runner, tools, recovery)
                recovery._receipt = receipt
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        capture.capture_error(error)
    runner = tools = recovery = None  # type: ignore[assignment]
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if control is not None:
        receipt = None
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn recovered container cleanup failed") from None
    return receipt


def _cleanup_recovered_owned_container(
    runner: CommandRunner,
    tools: TrustedHostTools,
    recovery: RecoveredContainerCleanupAuthority,
) -> ContainerAbsenceReceipt:
    authority = recovery._authority
    target = recovery._removal_target
    inspected: object = None
    inspect_failed = False
    try:
        inspected = execute_checked(
            runner,
            build_container_inspect_request(tools, authority.plan, authority.container_id),
            failure="Coturn recovered container inspection failed",
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        inspect_failed = True
    if not inspect_failed:
        inspection = decode_inspection_result(inspected, label="container")
        inspected = None
        cleanup_target = validate_container_cleanup_target(authority, inspection)
        if cleanup_target.running:
            stop_target = validate_container_stop_target(authority, inspection)
            stopped = execute_checked(
                runner,
                build_container_stop_request(tools, stop_target),
                failure="Coturn recovered container stop failed",
            )
            if (
                stopped.stderr != b""
                or translate_created_id(stopped.stdout) != authority.container_id
            ):
                raise CoturnRuntimeError("Coturn recovered container stop failed")
            stopped = stop_target = cleanup_target = inspection = None
            inspected = execute_checked(
                runner,
                build_container_inspect_request(tools, authority.plan, authority.container_id),
                failure="Coturn recovered stopped-container inspection failed",
            )
            inspection = decode_inspection_result(inspected, label="container")
            inspected = None
        target = validate_container_removal_target(authority, inspection)
        recovery._removal_target = target
        removed = execute_checked(
            runner,
            build_container_remove_request(tools, target),
            failure="Coturn recovered container removal failed",
        )
        if removed.stderr != b"" or translate_created_id(removed.stdout) != authority.container_id:
            raise CoturnRuntimeError("Coturn recovered container removal failed")
        removed = None
    if type(target) is not ValidatedContainerRemoval:
        raise CoturnRuntimeError("Coturn recovered container cleanup failed")
    absence = execute_checked(
        runner,
        build_container_absence_request(tools, target),
        failure="Coturn recovered container absence check failed",
    )
    if absence.stdout != b"" or absence.stderr != b"":
        raise CoturnRuntimeError("Coturn recovered container absence was not confirmed")
    absence = None
    return _persist_container_absence(
        plan=authority.plan,
        container_id=authority.container_id,
    )


__all__ = [
    "ContainerAbsenceReceipt",
    "RecoveredContainerCleanupAuthority",
    "RemovedContainerReceipt",
    "StoppedCoturnReceipt",
    "cleanup_recovered_owned_container",
    "finalize_container_absence",
    "remove_stopped_owned_container",
    "stop_owned_container",
    "validate_owned_container_running",
]

"""Private start, join, result, and release facade for one relay build."""

from __future__ import annotations

import math
import time
import traceback

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_graph,
    _build_process_terminal,
    _build_process_worker_status,
    _publish_build_process_result,
    _registered_cleanup_authority,
    _resolve_build_process_owner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _release_build_process_step as _release_step,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _BuildWorkerKernel,
    _preown_worker_controller,
    _reserve_worker_kernel,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _OWNER_TOKEN,
    _RelayLinuxBuildCleanupAuthority,
    _RelayLinuxBuildCleanupRequired,
    _RelayLinuxBuildProcessError,
    _RelayLinuxBuildProcessOwner,
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_thread import (
    _BuildThreadStartOutcome,
    _construct_registered_build_thread,
    _join_registered_build_thread,
    _start_registered_build_thread,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker import (
    _relay_linux_build_worker_entry,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildControlSignal,
    _BuildWorkerController,
    _new_build_worker_controller,
)

_FAILURE = "Relay Linux build process operation failed"
_MAX_RUN_SECONDS = 600.0
_START_CONFIRM_SECONDS = 5.0
_WAIT_SECONDS = 0.05
_MAX_RELEASE_FAULTS = _MAX_CONTROL_HOLDER_FAULTS = 64
_CONTROL_HOLDER_FACTORY = _new_build_worker_controller


def _local_monotonic() -> float:
    return time.monotonic()


def _start_relay_linux_build_process(
    owner: _RelayLinuxBuildProcessOwner,
    *,
    run_deadline: float,
) -> None:
    """Start the exact registered worker and prove its atomic kernel claim."""

    retained: _RelayLinuxBuildProcessOwner | None = None
    state: object | None = None
    acquired = False
    owned_before = False
    try:
        retained = _require_owner(owner)
        deadline = _validated_deadline(run_deadline, maximum_span=_MAX_RUN_SECONDS)
        state = retained._facade_state
        owned_before = _operation_lock_owned(state)
        if owned_before:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        acquired = _acquire_operation(state, deadline)
        if not acquired:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        _start_locked(retained, deadline)
    except _RelayLinuxBuildCleanupRequired:
        recovery = _recover_owner(owner, retained)
        if recovery is not None:
            _request_cleanup(recovery)
            _raise_latched_control_if_present(recovery)
        raise
    except (KeyboardInterrupt, SystemExit) as error:
        recovery = _recover_owner(owner, retained)
        signal = _capture_recovered_control(recovery, error)
        if recovery is not None:
            _request_cleanup(recovery)
        _raise_control(signal, _registered_cleanup_authority(recovery))
    except BaseException as error:
        _scrub_exception(error)
        recovery = _recover_owner(owner, retained)
        if recovery is None:
            raise _RelayLinuxBuildProcessError(_FAILURE) from None
        _request_cleanup(recovery)
        raise _cleanup_failure(recovery) from None
    finally:
        _release_operation(state, acquired=acquired, owned_before=owned_before)


def _start_locked(owner: _RelayLinuxBuildProcessOwner, run_deadline: float) -> None:
    state = owner._facade_state
    phase = state._phase_value()
    if not state._bind_run_deadline(run_deadline) or state._release_was_requested():
        raise _cleanup_failure(owner)
    if phase in {"started", "joined"}:
        if _build_process_worker_status(owner) not in {"started", "settled"}:
            raise _cleanup_failure(owner)
        return
    if phase not in {"new", "thread-ready", "start-intended"}:
        raise _cleanup_failure(owner)

    controller: _BuildWorkerController
    kernel: _BuildWorkerKernel
    thread: object
    fresh_intent = False
    if phase == "new":
        controller = _preown_worker_controller(owner, run_deadline)
        kernel = _reserve_worker_kernel(owner)
        thread, coherent = _construct_registered_build_thread(
            owner,
            controller=controller,
            target=_relay_linux_build_worker_entry,
            kernel_token=kernel._token,
        )
        if not coherent or not state._transition("thread-ready"):
            raise _cleanup_failure(owner)
        phase = "thread-ready"
    else:
        controller, kernel, thread = _exact_start_graph(owner)

    if phase == "thread-ready":
        if state._release_was_requested() or not state._transition("start-intended"):
            raise _cleanup_failure(owner)
        fresh_intent = True
    elif phase != "start-intended":
        raise _cleanup_failure(owner)

    controller, kernel, thread = _exact_start_graph(owner)
    if state._release_was_requested():
        raise _cleanup_failure(owner)

    pending_control: _BuildControlSignal | None = None
    start_rejected = False
    if fresh_intent:
        if not state._enter_start_effect():
            raise _cleanup_failure(owner)
        try:
            outcome = _start_registered_build_thread(owner, controller, thread)
            if type(outcome) is not _BuildThreadStartOutcome:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            if outcome.status == "rejected":
                if not state._reject_start_effect():
                    raise _RelayLinuxBuildProcessError(_FAILURE)
                start_rejected = True
            elif outcome.status != "returned":
                raise _RelayLinuxBuildProcessError(_FAILURE)
        except (KeyboardInterrupt, SystemExit) as error:
            pending_control = _capture_control(owner, error)
            _request_cleanup(owner)
        except BaseException as error:
            _scrub_exception(error)

    if start_rejected:
        _request_cleanup(owner)
        raise _cleanup_failure(owner)

    confirm_deadline = min(run_deadline, _clock() + _START_CONFIRM_SECONDS)
    while True:
        status = _build_process_worker_status(owner)
        if status in {"started", "settled"}:
            if not state._transition("started"):
                raise _cleanup_failure(owner)
            try:
                _process_start_committed()
            except (KeyboardInterrupt, SystemExit) as error:
                if pending_control is None:
                    pending_control = _capture_control(owner, error)
                else:
                    controller._capture_control(error)
                _request_cleanup(owner)
            except BaseException as error:
                _scrub_exception(error)
            if state._release_was_requested() and pending_control is None:
                raise _cleanup_failure(owner)
            if pending_control is not None:
                _raise_control(pending_control, _registered_cleanup_authority(owner))
            return
        if status in {"cancelled", "invalid", "unregistered"}:
            break
        now = _clock()
        if now >= confirm_deadline:
            break
        _wait_state(state, min(_WAIT_SECONDS, confirm_deadline - now))
    _request_cleanup(owner)
    if pending_control is not None:
        _raise_control(pending_control, _registered_cleanup_authority(owner))
    raise _cleanup_failure(owner)


def _join_relay_linux_build_process(
    owner: _RelayLinuxBuildProcessOwner,
    *,
    join_deadline: float,
) -> None:
    """Boundedly join the exact worker and retain its terminal result."""

    retained: _RelayLinuxBuildProcessOwner | None = None
    state: object | None = None
    acquired = False
    owned_before = False
    try:
        retained = _require_owner(owner)
        deadline = _validated_deadline(join_deadline, maximum_span=_MAX_RUN_SECONDS)
        state = retained._facade_state
        owned_before = _operation_lock_owned(state)
        if owned_before:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        acquired = _acquire_operation(state, deadline)
        if not acquired:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        _join_locked(retained, deadline)
    except _RelayLinuxBuildCleanupRequired:
        recovery = _recover_owner(owner, retained)
        if recovery is not None:
            _request_cleanup(recovery)
            _raise_latched_control_if_present(recovery)
        raise
    except (KeyboardInterrupt, SystemExit) as error:
        recovery = _recover_owner(owner, retained)
        signal = _capture_recovered_control(recovery, error)
        if recovery is not None:
            _request_cleanup(recovery)
        _raise_control(signal, _registered_cleanup_authority(recovery))
    except BaseException as error:
        _scrub_exception(error)
        recovery = _recover_owner(owner, retained)
        if recovery is None:
            raise _RelayLinuxBuildProcessError(_FAILURE) from None
        _request_cleanup(recovery)
        raise _cleanup_failure(recovery) from None
    finally:
        _release_operation(state, acquired=acquired, owned_before=owned_before)


def _join_locked(owner: _RelayLinuxBuildProcessOwner, join_deadline: float) -> None:
    state = owner._facade_state
    phase = state._phase_value()
    if phase == "joined":
        return
    if phase != "started" or state._release_was_requested():
        raise _cleanup_failure(owner)
    controller, _kernel, thread = _exact_start_graph(owner)
    pending_control: _BuildControlSignal | None = None
    while True:
        now = _clock()
        if now >= join_deadline or state._release_was_requested():
            _request_cleanup(owner)
            if pending_control is not None:
                _raise_control(pending_control, _registered_cleanup_authority(owner))
            raise _cleanup_failure(owner)
        status = _build_process_worker_status(owner)
        if status not in {"started", "settled"}:
            _request_cleanup(owner)
            raise _cleanup_failure(owner)
        timeout = min(_WAIT_SECONDS, join_deadline - now)
        try:
            dead = _join_registered_build_thread(thread, float(timeout))
        except (KeyboardInterrupt, SystemExit) as error:
            if pending_control is None:
                pending_control = _capture_control(owner, error)
            else:
                controller._capture_control(error)
            _request_cleanup(owner)
            continue
        except BaseException as error:
            _scrub_exception(error)
            dead = not thread.is_alive()
        if not dead:
            continue
        if _build_process_worker_status(owner) != "settled":
            _wait_state(state, min(_WAIT_SECONDS, max(0.0, join_deadline - _clock())))
            continue
        terminal = _build_process_terminal(owner)
        if terminal is None or owner._raw_destination._read(owner._spec) is not None:
            _request_cleanup(owner)
            raise _cleanup_failure(owner)
        if not state._transition("joined"):
            _request_cleanup(owner)
            raise _cleanup_failure(owner)
        worker_control = controller._control_value()
        if pending_control is None and type(worker_control) is _BuildControlSignal:
            pending_control = worker_control
            _request_cleanup(owner)
        try:
            _process_join_committed()
        except (KeyboardInterrupt, SystemExit) as error:
            if pending_control is None:
                pending_control = _capture_control(owner, error)
            else:
                controller._capture_control(error)
            _request_cleanup(owner)
        except BaseException as error:
            _scrub_exception(error)
        if pending_control is not None:
            _raise_control(pending_control, _registered_cleanup_authority(owner))
        return


def _relay_linux_build_process_result(
    owner: _RelayLinuxBuildProcessOwner,
) -> _RelayLinuxBuildProcessReceipt:
    """Return the one canonical falsey receipt for an exact zero exit."""

    retained: _RelayLinuxBuildProcessOwner | None = None
    state: object | None = None
    acquired = False
    owned_before = False
    try:
        retained = _require_owner(owner)
        state = retained._facade_state
        owned_before = _operation_lock_owned(state)
        if owned_before:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        acquired = _acquire_operation_nowait(state)
        if type(acquired) is not bool or not acquired:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        if state._phase_value() != "joined" or state._release_was_requested():
            raise _cleanup_failure(retained)
        try:
            receipt = _publish_build_process_result(retained)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            _scrub_exception(error)
            receipt = retained._result_destination._read()
        if type(receipt) is not _RelayLinuxBuildProcessReceipt or not receipt._matches(
            retained._owner_token
        ):
            _request_cleanup(retained)
            raise _cleanup_failure(retained)
        return receipt
    except _RelayLinuxBuildCleanupRequired:
        recovery = _recover_owner(owner, retained)
        if recovery is not None:
            _request_cleanup(recovery)
            _raise_latched_control_if_present(recovery)
        raise
    except (KeyboardInterrupt, SystemExit) as error:
        recovery = _recover_owner(owner, retained)
        signal = _capture_recovered_control(recovery, error)
        if recovery is not None:
            _request_cleanup(recovery)
        _raise_control(signal, _registered_cleanup_authority(recovery))
    except BaseException as error:
        _scrub_exception(error)
        recovery = _recover_owner(owner, retained)
        if recovery is None:
            raise _RelayLinuxBuildProcessError(_FAILURE) from None
        _request_cleanup(recovery)
        raise _cleanup_failure(recovery) from None
    finally:
        _release_operation(state, acquired=acquired, owned_before=owned_before)


def _release_relay_linux_build_process(
    value: _RelayLinuxBuildProcessOwner | _RelayLinuxBuildCleanupAuthority,
    *,
    cleanup_deadline: float,
) -> None:
    """Revoke process authority before releasing the recoverable owner."""

    authority: _RelayLinuxBuildCleanupAuthority | None = None
    owner: _RelayLinuxBuildProcessOwner | None = None
    state: object | None = None
    pending_control: _BuildControlSignal | None = None
    acquired = False
    owned_before = False
    completed = False
    faults = 0
    try:
        authority = _authority_from(value)
        owner = _resolve_build_process_owner(value)
        if owner is None:
            return
        deadline = _validated_deadline(cleanup_deadline, maximum_span=_MAX_RUN_SECONDS)
        state = owner._facade_state
        owned_before = _operation_lock_owned(state)
        if owned_before:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        state._request_release()
        _request_controller_termination(owner)
        while faults < _MAX_RELEASE_FAULTS:
            try:
                if _clock() >= deadline:
                    break
                if not acquired:
                    acquired = _acquire_operation(state, deadline)
                    if not acquired:
                        break
                if _resolve_build_process_owner(authority) is None:
                    completed = True
                    break
                remaining = max(0.0, deadline - _clock())
                join_timeout = float(min(_WAIT_SECONDS, remaining))
                if _release_step(owner, join_timeout):
                    completed = True
                    break
                remaining = max(0.0, deadline - _clock())
                _wait_state(state, min(_WAIT_SECONDS, remaining))
            except (KeyboardInterrupt, SystemExit) as error:
                if not acquired and _operation_lock_owned(state):
                    acquired = True
                signal = _capture_control(owner, error)
                if pending_control is None:
                    pending_control = signal
                _request_cleanup(owner)
                faults += 1
                _wait_state(state, _WAIT_SECONDS)
            except BaseException as error:
                if not acquired and _operation_lock_owned(state):
                    acquired = True
                _scrub_exception(error)
                _request_cleanup(owner)
                faults += 1
        if not completed and _resolve_build_process_owner(authority) is None:
            completed = True
        if completed:
            _mark_released(state)
            if pending_control is not None:
                _raise_control(pending_control, None)
            return
        if pending_control is not None:
            _raise_control(pending_control, _registered_cleanup_authority(owner))
        raise _cleanup_failure(owner) from None
    except _RelayLinuxBuildCleanupRequired:
        raise
    except (KeyboardInterrupt, SystemExit) as error:
        recovery = _recover_owner(value, owner)
        signal = _capture_recovered_control(recovery, error)
        if recovery is not None:
            _request_cleanup(recovery)
        _raise_control(signal, _registered_cleanup_authority(recovery))
    except BaseException as error:
        _scrub_exception(error)
        recovery = _recover_owner(value, owner)
        if recovery is None:
            if authority is not None and _resolve_build_process_owner(authority) is None:
                if state is not None:
                    _mark_released(state)
                return
            raise _RelayLinuxBuildProcessError(_FAILURE) from None
        _request_cleanup(recovery)
        raise _cleanup_failure(recovery) from None
    finally:
        _release_operation(state, acquired=acquired, owned_before=owned_before)


def _exact_start_graph(
    owner: _RelayLinuxBuildProcessOwner,
) -> tuple[_BuildWorkerController, _BuildWorkerKernel, object]:
    controller, kernel, thread = _build_process_graph(owner)
    if (
        type(controller) is not _BuildWorkerController
        or type(kernel) is not _BuildWorkerKernel
        or thread is None
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return controller, kernel, thread


def _mark_released(state: object) -> None:
    phase = state._phase_value()  # type: ignore[attr-defined]
    if phase != "released" and not state._transition("released"):  # type: ignore[attr-defined]
        raise _RelayLinuxBuildProcessError(_FAILURE)


def _require_owner(value: object) -> _RelayLinuxBuildProcessOwner:
    if type(value) is not _RelayLinuxBuildProcessOwner:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    owner = _resolve_build_process_owner(value)
    if owner is not value or not owner._facade_state._matches(owner._owner_token):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return owner


def _recover_owner(
    value: object,
    retained: _RelayLinuxBuildProcessOwner | None,
) -> _RelayLinuxBuildProcessOwner | None:
    try:
        if (
            type(retained) is _RelayLinuxBuildProcessOwner
            and _registered_cleanup_authority(retained) is not None
        ):
            return retained
        candidate = _resolve_build_process_owner(value)
        return candidate if type(candidate) is _RelayLinuxBuildProcessOwner else None
    except BaseException as error:
        _scrub_exception(error)
        return None


def _authority_from(value: object) -> _RelayLinuxBuildCleanupAuthority:
    if type(value) is _RelayLinuxBuildProcessOwner:
        authority = value._cleanup_authority
    elif type(value) is _RelayLinuxBuildCleanupAuthority:
        authority = value
    else:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    if not authority._is_authentic():
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return authority


def _validated_deadline(value: object, *, maximum_span: float) -> float:
    now = _clock()
    if (
        type(value) is not float
        or not math.isfinite(value)
        or value <= now
        or value - now > maximum_span
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return value


def _clock() -> float:
    value = _local_monotonic()
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return value


def _acquire_operation(state: object, deadline: float) -> bool:
    remaining = max(0.0, deadline - _clock())
    acquired = state._operation_lock.acquire(timeout=remaining)  # type: ignore[attr-defined]
    if type(acquired) is not bool:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    if acquired:
        _operation_lock_acquired()
    return acquired


def _acquire_operation_nowait(state: object) -> bool:
    acquired = state._operation_lock.acquire(blocking=False)  # type: ignore[attr-defined]
    if type(acquired) is not bool:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    if acquired:
        _operation_lock_acquired()
    return acquired


def _operation_lock_owned(state: object) -> bool:
    owned = state._operation_lock._is_owned()  # type: ignore[attr-defined]
    if type(owned) is not bool:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return owned


def _release_operation(state: object | None, *, acquired: bool, owned_before: bool) -> None:
    if state is None or owned_before:
        return
    try:
        owned_now = _operation_lock_owned(state)
    except BaseException:
        owned_now = acquired
    if acquired or owned_now:
        state._operation_lock.release()  # type: ignore[attr-defined]


def _wait_state(state: object, timeout: float) -> None:
    if timeout <= 0.0:
        return
    with state._condition:  # type: ignore[attr-defined]
        state._condition.wait(timeout)  # type: ignore[attr-defined]


def _request_cleanup(owner: _RelayLinuxBuildProcessOwner) -> None:
    owner._facade_state._request_release()
    _request_controller_termination(owner)


def _request_controller_termination(owner: _RelayLinuxBuildProcessOwner) -> None:
    controller = owner._controller_destination._read()
    if type(controller) is _BuildWorkerController:
        controller._request_termination()


def _cleanup_failure(owner: _RelayLinuxBuildProcessOwner) -> _RelayLinuxBuildCleanupRequired:
    authority = _registered_cleanup_authority(owner)
    if authority is None:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return _RelayLinuxBuildCleanupRequired(_OWNER_TOKEN, authority=authority)


def _capture_control(
    owner: _RelayLinuxBuildProcessOwner,
    error: KeyboardInterrupt | SystemExit,
) -> _BuildControlSignal:
    controller = owner._controller_destination._read()
    if type(controller) is _BuildWorkerController:
        controller._capture_control(error)
        signal = controller._control_value()
        if type(signal) is _BuildControlSignal:
            return signal
        controller._fail()
    return _capture_unowned_control(error)


def _raise_latched_control_if_present(owner: _RelayLinuxBuildProcessOwner) -> None:
    controller = owner._controller_destination._read()
    if type(controller) is not _BuildWorkerController:
        return
    signal = controller._control_value()
    if type(signal) is _BuildControlSignal:
        _raise_control(signal, _registered_cleanup_authority(owner))


def _capture_recovered_control(
    owner: _RelayLinuxBuildProcessOwner | None,
    error: KeyboardInterrupt | SystemExit,
) -> _BuildControlSignal:
    if type(owner) is _RelayLinuxBuildProcessOwner:
        return _capture_control(owner, error)
    return _capture_unowned_control(error)


def _capture_unowned_control(
    error: KeyboardInterrupt | SystemExit,
) -> _BuildControlSignal:
    retained: list[KeyboardInterrupt | SystemExit] = [error]
    controller: _BuildWorkerController | None = None
    for _attempt in range(_MAX_CONTROL_HOLDER_FAULTS):
        try:
            candidate = _new_build_worker_controller(owner_token=object(), run_deadline=1.0)
            if type(candidate) is not _BuildWorkerController:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            controller = candidate
            break
        except (KeyboardInterrupt, SystemExit) as nested:
            retained.append(nested)
        except BaseException as failure:
            _scrub_exception(failure)
    if controller is None:
        controller = _CONTROL_HOLDER_FACTORY(owner_token=object(), run_deadline=1.0)
    for control in retained:
        controller._capture_control(control)
    retained.clear()
    signal = controller._control_value()
    if type(signal) is not _BuildControlSignal:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return signal


def _raise_control(
    signal: _BuildControlSignal,
    authority: _RelayLinuxBuildCleanupAuthority | None,
) -> None:
    error: KeyboardInterrupt | SystemExit
    if signal.kind == "keyboard":
        error = KeyboardInterrupt()
    else:
        error = SystemExit(signal.code)
    if authority is not None:
        error.cleanup_authority = authority  # type: ignore[attr-defined]
    raise error from None


def _scrub_exception(error: BaseException) -> None:
    try:
        trace = BaseException.__getattribute__(error, "__traceback__")
        BaseException.__setattr__(error, "__traceback__", None)
        BaseException.__setattr__(error, "__cause__", None)
        BaseException.__setattr__(error, "__context__", None)
        BaseException.__setattr__(error, "__suppress_context__", True)
        if trace is not None:
            traceback.clear_frames(trace)
    except BaseException:
        pass


def _process_start_committed() -> None:
    """Deterministic cut after exact worker-start proof became durable."""


def _process_join_committed() -> None:
    """Deterministic cut after exact joined-terminal proof became durable."""


def _operation_lock_acquired() -> None:
    """Deterministic cut after the current facade thread owns its RLock."""


__all__: list[str] = []

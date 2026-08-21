"""Exact registry reads and release for the private relay build facade."""

from __future__ import annotations

import threading

from scripts import voice_pipecat_e2e_relay_linux_build_process_registry as _registry
from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildCleanupAuthority,
    _RelayLinuxBuildProcessError,
    _RelayLinuxBuildProcessOwner,
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_thread import (
    _join_registered_build_thread,
    _registered_build_thread_started,
    _scrub_registered_build_thread,
    _scrub_unstarted_registered_build_thread,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerClaim,
    _BuildWorkerController,
    _BuildWorkerTerminal,
)

_FAILURE = "Relay Linux build process facade registry is unavailable"


def _resolve_build_process_owner(
    value: object,
) -> _RelayLinuxBuildProcessOwner | None:
    owner: _RelayLinuxBuildProcessOwner | None = None
    authority: _RelayLinuxBuildCleanupAuthority | None = None
    if type(value) is _RelayLinuxBuildProcessOwner:
        owner = value
        candidate = owner._cleanup_authority
        authority = candidate if candidate._is_authentic() else None
    elif type(value) is _RelayLinuxBuildCleanupAuthority and value._is_authentic():
        authority = value
    if authority is None:
        return None
    with _registry._LOCK:
        retained = _registry._OWNERS.get(authority._key)
        if type(retained) is not _RelayLinuxBuildProcessOwner:
            return None
        if owner is not None and retained is not owner:
            return None
        if retained._cleanup_authority is not authority:
            return None
        return retained


def _registered_cleanup_authority(
    owner: object,
) -> _RelayLinuxBuildCleanupAuthority | None:
    if type(owner) is not _RelayLinuxBuildProcessOwner:
        return None
    authority = owner._cleanup_authority
    with _registry._LOCK:
        return authority if _registry._OWNERS.get(authority._key) is owner else None


def _build_process_graph(
    owner: _RelayLinuxBuildProcessOwner,
) -> tuple[object | None, object | None, object | None]:
    if type(owner) is not _RelayLinuxBuildProcessOwner:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _registry._LOCK:
        if _registry._OWNERS.get(owner._cleanup_authority._key) is not owner:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        controller = owner._controller_destination._read()
        kernel = owner._kernel_destination._read()
        thread = owner._thread_destination._read()
        if controller is not None and (
            type(controller) is not _BuildWorkerController
            or not controller._matches(owner._owner_token)
        ):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        if kernel is not None and (
            type(kernel) is not _registry._BuildWorkerKernel or kernel._owner is not owner
        ):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        if thread is not None and type(thread) is not threading.Thread:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        if thread is not None and (
            type(controller) is not _BuildWorkerController or controller._thread() is not thread
        ):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return controller, kernel, thread


def _build_process_worker_status(
    owner: _RelayLinuxBuildProcessOwner,
) -> str:
    """Return one identity-checked status without exposing the transition."""

    controller, kernel, thread = _build_process_graph(owner)
    if controller is None and kernel is None and thread is None:
        return "empty"
    if type(kernel) is not _registry._BuildWorkerKernel:
        return "invalid"
    with _registry._LOCK:
        registered = _registry._KERNELS.get(kernel._token)
        if registered not in {None, kernel}:
            return "invalid"
        transition = kernel._transition
        if transition.phase in {"available", "cancelling"}:
            return "pending" if registered is kernel else "unregistered"
        if transition.phase == "reported" and transition.worker is None:
            return "pending"
        if (
            transition.phase == "settled"
            and transition.worker is None
            and transition.claim is None
            and type(transition.terminal) is _BuildWorkerTerminal
        ):
            return "cancelled"
        if (
            transition.phase in {"claimed", "reported", "settled"}
            and thread is not None
            and transition.worker is thread
            and transition.claim is not None
            and transition.claim._matches(owner._owner_token, thread)
        ):
            return "settled" if transition.phase == "settled" else "started"
        return "invalid"


def _build_process_terminal(
    owner: _RelayLinuxBuildProcessOwner,
) -> _BuildWorkerTerminal | None:
    controller, kernel, thread = _build_process_graph(owner)
    if (
        type(controller) is not _BuildWorkerController
        or type(kernel) is not _registry._BuildWorkerKernel
        or thread is None
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _registry._LOCK:
        transition = kernel._transition
        terminal = transition.terminal
        if (
            _registry._KERNELS.get(kernel._token) is not kernel
            or transition.phase != "settled"
            or transition.worker is not thread
            or type(transition.claim) is not _BuildWorkerClaim
            or not transition.claim._matches(owner._owner_token, thread)
            or type(terminal) is not _BuildWorkerTerminal
            or not terminal._matches(owner._owner_token)
            or not controller._matches(owner._owner_token)
            or controller._thread() is not thread
            or controller._phase_value() != "settled"
        ):
            return None
        return terminal


def _publish_build_process_result(
    owner: _RelayLinuxBuildProcessOwner,
) -> _RelayLinuxBuildProcessReceipt | None:
    terminal = _build_process_terminal(owner)
    if terminal is None or terminal.returncode != 0 or terminal.succeeded is not True:
        return None
    if owner._raw_destination._read(owner._spec) is not None:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return owner._result_destination._publish_success(owner._owner_token)


def _release_build_process_registries(
    owner: _RelayLinuxBuildProcessOwner,
) -> bool:
    """Release kernel first and the recoverable owner registry last."""

    if type(owner) is not _RelayLinuxBuildProcessOwner:
        return False
    with _registry._LOCK:
        key = owner._cleanup_authority._key
        retained_owner = _registry._OWNERS.get(key)
        if retained_owner is None:
            return True
        if retained_owner is not owner:
            return False
        controller = owner._controller_destination._read()
        kernel = owner._kernel_destination._read()
        thread = owner._thread_destination._read()
        phase = owner._facade_state._phase_value()
        if owner._raw_destination._read(owner._spec) is not None:
            return False
        if kernel is None:
            if thread is not None or phase not in {"new", "cancelled"}:
                return False
            if controller is not None and (
                type(controller) is not _BuildWorkerController
                or not controller._matches(owner._owner_token)
                or controller._phase_value() != "settled"
            ):
                return False
        else:
            if type(kernel) is not _registry._BuildWorkerKernel or kernel._owner is not owner:
                return False
            transition = kernel._transition
            worker_joined = bool(
                phase == "joined"
                and type(controller) is _BuildWorkerController
                and controller._matches(owner._owner_token)
                and controller._thread() is thread
                and controller._phase_value() == "settled"
                and thread is not None
                and type(thread.ident) is int
                and thread.ident > 0
                and thread.is_alive() is False
                and transition.phase == "settled"
                and transition.worker is thread
                and type(transition.claim) is _BuildWorkerClaim
                and transition.claim._matches(owner._owner_token, thread)
                and type(transition.terminal) is _BuildWorkerTerminal
                and transition.terminal._matches(owner._owner_token)
            )
            prestart_cancelled = bool(
                phase == "cancelled"
                and type(controller) is _BuildWorkerController
                and controller._matches(owner._owner_token)
                and controller._phase_value() == "settled"
                and _prestart_thread_is_scrubbed(owner, controller, thread)
                and transition.phase == "settled"
                and transition.worker is None
                and transition.claim is None
                and type(transition.terminal) is _BuildWorkerTerminal
                and transition.terminal._matches(owner._owner_token)
            )
            unadmitted_candidate = bool(
                phase == "cancelled"
                and thread is None
                and transition.phase in {"available", "cancelling"}
                and transition.worker is None
                and transition.claim is None
                and transition.terminal is None
                and _registry._KERNELS.get(kernel._token) is None
            )
            if not (worker_joined or prestart_cancelled or unadmitted_candidate):
                return False
            retained_kernel = _registry._KERNELS.get(kernel._token)
            if retained_kernel not in {None, kernel}:
                return False
            if retained_kernel is kernel:
                if not owner._facade_state._intend_kernel_release():
                    return False
                del _registry._KERNELS[kernel._token]
                if not owner._facade_state._complete_kernel_release():
                    return False
                _kernel_registry_released()
                if _registry._KERNELS.get(kernel._token) is not None:
                    return False
            elif worker_joined or prestart_cancelled:
                release_phase = owner._facade_state._kernel_release_phase_value()
                if release_phase == "intended":
                    if not owner._facade_state._complete_kernel_release():
                        return False
                elif release_phase != "released":
                    return False
        retained_owner = _registry._OWNERS.get(key)
        if retained_owner is owner:
            del _registry._OWNERS[key]
            _owner_registry_released()
        return _registry._OWNERS.get(key) is None


def _release_build_process_step(
    owner: _RelayLinuxBuildProcessOwner,
    join_timeout: float,
) -> bool:
    """Perform one identity-checked, bounded release transition."""

    state = owner._facade_state
    controller, kernel, thread = _build_process_graph(owner)
    if type(controller) is _BuildWorkerController:
        controller._request_termination()
    if kernel is None:
        if (
            thread is not None
            or state._start_effect_was_entered()
            or not _settle_prestart_controller(controller)
        ):
            return False
        return bool(_mark_cancelled(state) and _release_build_process_registries(owner))

    status = _build_process_worker_status(owner)
    if status == "unregistered":
        if (
            thread is not None
            or state._start_effect_was_entered()
            or not _settle_prestart_controller(controller)
        ):
            return False
        return bool(_mark_cancelled(state) and _release_build_process_registries(owner))
    if status == "pending":
        if not _registry._cancel_unstarted_worker_kernel(kernel):
            return False
        status = _build_process_worker_status(owner)
    if status == "cancelled":
        if thread is not None:
            if not state._start_effect_was_entered():
                _scrub_unstarted_registered_build_thread(thread)
            elif _registered_build_thread_started(thread):
                if not _join_registered_build_thread(thread, join_timeout):
                    return False
                _scrub_registered_build_thread(thread)
            else:
                return False
        if not _settle_prestart_controller(controller) or not _mark_cancelled(state):
            return False
        return _release_build_process_registries(owner)
    if status not in {"started", "settled"} or thread is None:
        return False
    if state._phase_value() == "start-intended" and not state._transition("started"):
        return False
    if not _join_registered_build_thread(thread, join_timeout):
        return False
    if _build_process_worker_status(owner) != "settled":
        return False
    if _build_process_terminal(owner) is None:
        return False
    if owner._raw_destination._read(owner._spec) is not None:
        return False
    if not state._transition("joined"):
        return False
    _scrub_registered_build_thread(thread)
    return _release_build_process_registries(owner)


def _settle_prestart_controller(controller: object | None) -> bool:
    if controller is None:
        return True
    if type(controller) is not _BuildWorkerController:
        return False
    phase = controller._phase_value()
    return bool(phase == "settled" or (phase == "registered" and controller._transition("settled")))


def _mark_cancelled(state: object) -> bool:
    phase = state._phase_value()  # type: ignore[attr-defined]
    return bool(
        phase == "cancelled" or state._transition("cancelled")  # type: ignore[attr-defined]
    )


def _prestart_thread_is_scrubbed(
    owner: _RelayLinuxBuildProcessOwner,
    controller: _BuildWorkerController,
    thread: object | None,
) -> bool:
    effect_entered = owner._facade_state._start_effect_was_entered()
    if thread is None:
        return bool(not effect_entered and controller._thread() is None)
    if type(thread) is not threading.Thread or controller._thread() is not thread:
        return False
    try:
        values = vars(thread)
        scrubbed = bool(
            values.get("_target") is None
            and values.get("_args") == ()
            and values.get("_kwargs") == {}
        )
        if not effect_entered:
            return scrubbed
        return bool(
            scrubbed
            and type(thread.ident) is int
            and thread.ident > 0
            and thread.is_alive() is False
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _kernel_registry_released() -> None:
    """Deterministic cut after exact kernel registry deletion."""


def _owner_registry_released() -> None:
    """Deterministic cut after exact owner registry deletion."""


__all__: list[str] = []

"""Control-safe construction of the dormant relay workspace worker thread.

Only an opaque receipt crosses this module boundary.  The registry keeps the
raw thread, and this checkpoint never starts, joins, or otherwise runs it.
"""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
    _workspace_worker_binding_deadline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle import (
    _cancel_workspace_worker_thread_before_start,
    _start_workspace_worker_thread,
    _WorkspaceWorkerStartReceipt,
    _WorkspaceWorkerTerminalReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _advance_workspace_worker_thread,
    _capture_workspace_worker_control,
    _poison_workspace_worker_thread,
    _resolve_workspace_worker_thread_binding,
    _WorkspaceWorkerThreadBinding,
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_release import (
    _join_workspace_worker_thread,
    _release_workspace_worker_thread,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_MAX_FACADE_CONTROL_ATTEMPTS = 3
_FACADE_LIFECYCLE_SECONDS = 0.05
_REQUEST_CANCEL = _WorkspaceWorkerController._request_cancel


def _new_relay_linux_build_workspace_worker_thread(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
) -> tuple[_WorkspaceWorkerThreadReceipt | None, bool]:
    """Construct one registry-owned thread and return only its opaque receipt."""

    binding: _WorkspaceWorkerThreadBinding | None = None
    controller: _WorkspaceWorkerController | None = None
    retained: list[KeyboardInterrupt | SystemExit] = []
    outcome: tuple[_WorkspaceWorkerThreadReceipt | None, bool] = (None, False)
    phase = "construct"
    advance_attempts = 0
    cancel_faulted = False
    poison_attempted = False
    skip_final_validation = False
    control_attempts = 0
    deadline: float | None = None

    while True:
        try:
            if controller is None:
                controller = _provisional_workspace_worker_controller(owner, bundle)
            if phase == "construct":
                if deadline is None:
                    deadline = time.monotonic() + _FACADE_LIFECYCLE_SECONDS
                candidate_binding = _resolve_binding_before(owner, bundle, deadline)
                if binding is None:
                    binding = candidate_binding
                    controller = candidate_binding._controller
                elif not binding._matches(candidate_binding):
                    raise TypeError("Relay Linux workspace worker binding changed")
                if controller is None or candidate_binding._controller is not controller:
                    raise TypeError("Relay Linux workspace worker controller changed")
                _capture_retained_controls(controller, retained)
                advance_attempts += 1
                outcome = _advance_binding_before(binding, deadline)
                phase = "finalize"
                continue

            if phase == "reconcile":
                if binding is None or controller is None:
                    outcome = (None, False)
                    phase = "finalize"
                    continue
                _capture_retained_controls(controller, retained)
                advance_attempts += 1
                receipt, _coherent = _advance_binding_before(binding, deadline)
                outcome = (receipt, False)
                phase = "cancel"
                continue

            if phase == "cancel":
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    controller._request_cancel()
                    _capture_retained_controls(controller, retained)
                phase = "drain" if skip_final_validation else "finalize"
                continue

            if phase == "poison":
                poison_attempted = True
                if binding is not None:
                    receipt = _poison_binding_before(binding, deadline)
                    outcome = (receipt, False)
                phase = "drain" if skip_final_validation else "finalize"
                continue

            if phase == "drain":
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    _capture_retained_controls(controller, retained)
                phase = "handoff"
                continue

            if phase == "finalize":
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    _capture_retained_controls(controller, retained)
                    candidate_binding = _resolve_binding_before(owner, bundle, deadline)
                    if binding is None or not binding._matches(candidate_binding):
                        outcome = (None, False)
                        skip_final_validation = True
                        phase = (
                            "poison"
                            if cancel_faulted and not poison_attempted
                            else "handoff"
                            if cancel_faulted
                            else "cancel"
                        )
                        continue
                    if candidate_binding._controller is not controller:
                        outcome = (None, False)
                        skip_final_validation = True
                        phase = (
                            "poison"
                            if cancel_faulted and not poison_attempted
                            else "handoff"
                            if cancel_faulted
                            else "cancel"
                        )
                        continue
                    if controller._cancellation_requested():
                        outcome = (outcome[0], False)
                phase = "handoff"
                continue

            if phase == "fallback-handoff":
                return outcome
            if phase != "handoff":
                raise RuntimeError("Relay Linux workspace worker phase is invalid")
            return _handoff_workspace_worker_thread_result(outcome)
        except (KeyboardInterrupt, SystemExit) as control:
            retained.append(control)
            control_attempts += 1
            if control_attempts >= _MAX_FACADE_CONTROL_ATTEMPTS:
                outcome = (outcome[0], False)
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    _capture_retained_controls(controller, retained)
                return outcome
            if phase in {"construct", "reconcile"} and advance_attempts < 2:
                phase = "reconcile" if advance_attempts else "construct"
            elif phase == "poison" and not poison_attempted:
                phase = "poison"
            elif phase == "drain":
                phase = "drain"
            elif controller is not None:
                outcome = (outcome[0], False)
                phase = (
                    "poison"
                    if cancel_faulted and not poison_attempted
                    else "finalize"
                    if cancel_faulted
                    else "cancel"
                )
            else:
                outcome = (outcome[0], False)
                phase = "finalize"
        except BaseException as error:
            _scrub_control_minimal(error)
            outcome = (outcome[0], False)
            if phase == "cancel":
                cancel_faulted = True
                phase = "poison"
            elif phase == "poison":
                phase = "drain" if skip_final_validation else "finalize"
            elif phase == "drain":
                _scrub_retained_controls(retained)
                phase = "handoff"
            elif phase in {"construct", "reconcile"} and advance_attempts < 2:
                if advance_attempts:
                    phase = "reconcile"
                else:
                    phase = "cancel" if controller is not None else "finalize"
            elif phase == "handoff":
                phase = "fallback-handoff"
            elif phase == "finalize":
                skip_final_validation = True
                phase = "cancel" if controller is not None and not cancel_faulted else "drain"
            elif controller is not None and not cancel_faulted:
                phase = "cancel"
            else:
                phase = "handoff"


def _capture_retained_controls(
    controller: _WorkspaceWorkerController,
    retained: list[KeyboardInterrupt | SystemExit],
) -> None:
    while retained:
        control = retained[0]
        _capture_workspace_worker_control(controller, control)
        del retained[0]


def _scrub_retained_controls(
    retained: list[KeyboardInterrupt | SystemExit],
) -> None:
    while retained:
        _scrub_control_minimal(retained.pop(0))


def _handoff_workspace_worker_thread_result(
    outcome: tuple[_WorkspaceWorkerThreadReceipt | None, bool],
) -> tuple[_WorkspaceWorkerThreadReceipt | None, bool]:
    """Narrow return seam kept inside the guarded construction boundary."""

    return outcome


def _start_relay_linux_build_workspace_worker(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    start_deadline: float,
) -> tuple[_WorkspaceWorkerStartReceipt | None, bool]:
    """Start once and retain the bundle until the fixed worker terminal."""

    binding: _WorkspaceWorkerThreadBinding | None = None
    controller: _WorkspaceWorkerController | None = None
    retained: list[KeyboardInterrupt | SystemExit] = []
    faulted = False
    failures = 0
    control_failures = 0
    if type(start_deadline) is not float or not math.isfinite(start_deadline):
        return None, False
    while True:
        try:
            if controller is None:
                controller = _provisional_workspace_worker_controller(owner, bundle)
            candidate = _resolve_binding_before(owner, bundle, start_deadline)
            if binding is None:
                binding = candidate
                controller = candidate._controller
            elif not binding._matches(candidate):
                raise TypeError("Relay Linux workspace worker binding changed")
            if controller is None:
                raise TypeError("Relay Linux workspace worker controller is invalid")
            _capture_retained_controls(controller, retained)
            if time.monotonic() >= start_deadline:
                _REQUEST_CANCEL(controller)
            receipt, coherent = _start_workspace_worker_thread(
                binding,
                construction,
                start_deadline,
            )
            return receipt, bool(coherent and not faulted)
        except (KeyboardInterrupt, SystemExit) as control:
            retained.append(control)
            faulted = True
            control_failures += 1
            if controller is not None:
                if control_failures >= _MAX_FACADE_CONTROL_ATTEMPTS:
                    _capture_retained_controls(controller, retained)
                    return None, False
                _REQUEST_CANCEL(controller)
            elif control_failures >= _MAX_FACADE_CONTROL_ATTEMPTS:
                _scrub_retained_controls(retained)
                return None, False
        except BaseException as error:
            _scrub_control_minimal(error)
            faulted = True
            failures += 1
            if binding is None or controller is None:
                _scrub_retained_controls(retained)
                return None, False
            _REQUEST_CANCEL(controller)
            if failures >= 2:
                _capture_retained_controls(controller, retained)
                return None, False


def _cancel_relay_linux_build_workspace_worker(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
) -> _WorkspaceWorkerTerminalReceipt | None:
    """Let cancellation win only before durable start-effect entry."""

    binding: _WorkspaceWorkerThreadBinding | None = None
    controller: _WorkspaceWorkerController | None = None
    retained: list[KeyboardInterrupt | SystemExit] = []
    failures = 0
    control_attempts = 0
    deadline: float | None = None
    while True:
        try:
            if controller is None:
                controller = _provisional_workspace_worker_controller(owner, bundle)
            if deadline is None:
                deadline = time.monotonic() + _FACADE_LIFECYCLE_SECONDS
            candidate = _resolve_binding_before(owner, bundle, deadline)
            if binding is None:
                binding = candidate
                controller = candidate._controller
            elif not binding._matches(candidate):
                raise TypeError("Relay Linux workspace worker binding changed")
            if controller is None:
                raise TypeError("Relay Linux workspace worker controller is invalid")
            controller._request_cancel()
            _capture_retained_controls(controller, retained)
            return _cancel_workspace_worker_thread_before_start(
                binding,
                construction,
                deadline,
            )
        except (KeyboardInterrupt, SystemExit) as control:
            retained.append(control)
            control_attempts += 1
            if control_attempts >= _MAX_FACADE_CONTROL_ATTEMPTS:
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    _capture_retained_controls(controller, retained)
                return None
        except BaseException as error:
            _scrub_control_minimal(error)
            failures += 1
            if controller is None:
                _scrub_retained_controls(retained)
                return None
            _REQUEST_CANCEL(controller)
            if failures >= 2:
                _capture_retained_controls(controller, retained)
                return None


def _join_relay_linux_build_workspace_worker(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    timeout_seconds: float,
) -> tuple[_WorkspaceWorkerTerminalReceipt | None, bool]:
    """Perform only finite registry-owned joins against the exact record."""

    if (
        type(timeout_seconds) is not float
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0.0
    ):
        return None, False
    deadline: float | None = None
    binding: _WorkspaceWorkerThreadBinding | None = None
    controller: _WorkspaceWorkerController | None = None
    retained: list[KeyboardInterrupt | SystemExit] = []
    failures = 0
    control_failures = 0
    while True:
        try:
            if controller is None:
                controller = _provisional_workspace_worker_controller(owner, bundle)
            now = time.monotonic()
            if deadline is None:
                deadline = now + min(timeout_seconds, 1.0)
            candidate = _resolve_binding_before(owner, bundle, deadline)
            if binding is None:
                binding = candidate
                controller = candidate._controller
            elif not binding._matches(candidate):
                raise TypeError("Relay Linux workspace worker binding changed")
            if controller is None:
                raise TypeError("Relay Linux workspace worker controller is invalid")
            _capture_retained_controls(controller, retained)
            return _join_workspace_worker_thread(
                binding,
                construction,
                deadline,
            )
        except (KeyboardInterrupt, SystemExit) as control:
            retained.append(control)
            control_failures += 1
            if control_failures >= _MAX_FACADE_CONTROL_ATTEMPTS:
                if controller is not None:
                    _capture_retained_controls(controller, retained)
                else:
                    _scrub_retained_controls(retained)
                return None, False
        except BaseException as error:
            _scrub_control_minimal(error)
            failures += 1
            if binding is None or failures >= 2:
                if controller is not None:
                    _capture_retained_controls(controller, retained)
                else:
                    _scrub_retained_controls(retained)
                return None, False
            if controller is None:
                _scrub_retained_controls(retained)
                return None, False


def _release_relay_linux_build_workspace_worker(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    terminal: _WorkspaceWorkerTerminalReceipt,
) -> bool:
    """Scrub and delete only a terminal, joined or proven-no-effect record."""

    binding: _WorkspaceWorkerThreadBinding | None = None
    controller: _WorkspaceWorkerController | None = None
    retained: list[KeyboardInterrupt | SystemExit] = []
    failures = 0
    control_attempts = 0
    deadline: float | None = None
    while True:
        try:
            if controller is None:
                controller = _provisional_workspace_worker_controller(owner, bundle)
            if deadline is None:
                deadline = time.monotonic() + _FACADE_LIFECYCLE_SECONDS
            candidate = _resolve_binding_before(owner, bundle, deadline)
            if binding is None:
                binding = candidate
                controller = candidate._controller
            elif not binding._matches(candidate):
                raise TypeError("Relay Linux workspace worker binding changed")
            if controller is None:
                raise TypeError("Relay Linux workspace worker controller is invalid")
            _capture_retained_controls(controller, retained)
            return _release_workspace_worker_thread(
                binding,
                construction,
                terminal,
                deadline,
            )
        except (KeyboardInterrupt, SystemExit) as control:
            retained.append(control)
            control_attempts += 1
            if control_attempts >= _MAX_FACADE_CONTROL_ATTEMPTS:
                if controller is None:
                    _scrub_retained_controls(retained)
                else:
                    _capture_retained_controls(controller, retained)
                return False
        except BaseException as error:
            _scrub_control_minimal(error)
            failures += 1
            if binding is None or failures >= 2:
                _scrub_retained_controls(retained)
                return False


def _provisional_workspace_worker_controller(
    owner: object,
    bundle: object,
) -> _WorkspaceWorkerController | None:
    if (
        type(owner) is not _RelayLinuxBuildWorkspaceOwner
        or type(bundle) is not _WorkspaceWorkerBundle
        or type(bundle._controller) is not _WorkspaceWorkerController
        or bundle._owner_token is not owner._cleanup_authority._key
        or not bundle._controller._matches(bundle._owner_token)
    ):
        return None
    return bundle._controller


def _resolve_binding_before(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    deadline: float,
) -> _WorkspaceWorkerThreadBinding:
    with _workspace_worker_binding_deadline(deadline):
        return _resolve_workspace_worker_thread_binding(owner, bundle)


def _advance_binding_before(
    binding: _WorkspaceWorkerThreadBinding,
    deadline: float,
) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
    with _workspace_worker_binding_deadline(deadline):
        return _advance_workspace_worker_thread(binding)


def _poison_binding_before(
    binding: _WorkspaceWorkerThreadBinding,
    deadline: float,
) -> _WorkspaceWorkerThreadReceipt:
    with _workspace_worker_binding_deadline(deadline):
        return _poison_workspace_worker_thread(binding)


__all__: list[str] = []

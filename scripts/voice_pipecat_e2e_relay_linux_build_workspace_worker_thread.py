"""Control-safe construction of the dormant relay workspace worker thread.

Only an opaque receipt crosses this module boundary.  The registry keeps the
raw thread, and this checkpoint never starts, joins, or otherwise runs it.
"""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _advance_workspace_worker_thread,
    _capture_workspace_worker_control,
    _poison_workspace_worker_thread,
    _resolve_workspace_worker_thread_binding,
    _WorkspaceWorkerThreadBinding,
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)


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

    while True:
        try:
            if phase == "construct":
                candidate_binding = _resolve_workspace_worker_thread_binding(owner, bundle)
                if binding is None:
                    binding = candidate_binding
                    controller = candidate_binding._controller
                elif not binding._matches(candidate_binding):
                    raise TypeError("Relay Linux workspace worker binding changed")
                if controller is None or candidate_binding._controller is not controller:
                    raise TypeError("Relay Linux workspace worker controller changed")
                _capture_retained_controls(controller, retained)
                advance_attempts += 1
                outcome = _advance_workspace_worker_thread(binding)
                phase = "finalize"
                continue

            if phase == "reconcile":
                if binding is None or controller is None:
                    outcome = (None, False)
                    phase = "finalize"
                    continue
                _capture_retained_controls(controller, retained)
                advance_attempts += 1
                receipt, _coherent = _advance_workspace_worker_thread(binding)
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
                    receipt = _poison_workspace_worker_thread(binding)
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
                    candidate_binding = _resolve_workspace_worker_thread_binding(
                        owner,
                        bundle,
                    )
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


__all__: list[str] = []

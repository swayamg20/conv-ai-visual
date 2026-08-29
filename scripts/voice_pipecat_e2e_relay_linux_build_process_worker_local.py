"""Claim-bound spawn owner for the sole relay Linux build worker."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _BuildWorkerKernelTake,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_process_local import (
    _ALL_HANDLES,
    _LocalBuildProcessMixin,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerClaim,
    _BuildWorkerController,
)
from scripts.voice_pipecat_e2e_relay_linux_build_spawn_local import (
    _spawn_registered_relay_linux_build,
)

_DRIVER_TOKEN = object()


class _LocalBuildWorkerDriver(_LocalBuildProcessMixin):
    """Initially authority-free state retained only by the registered worker."""

    __slots__ = (
        "_claim",
        "_close_intents",
        "_closed_handles",
        "_controller",
        "_group_absent",
        "_handle",
        "_handle_name",
        "_kernel",
        "_kill_deadline",
        "_kill_intended",
        "_kill_sent",
        "_pgid",
        "_pid",
        "_process",
        "_query_epoch",
        "_query_inflight",
        "_raw_clear_intended",
        "_raw_cleared",
        "_raw_destination",
        "_reaped",
        "_returncode",
        "_signal_revoked",
        "_spawn_intended",
        "_spec",
        "_state",
        "_term_ambiguous",
        "_term_deadline",
        "_term_intended",
        "_term_sent",
        "_terminal_latched",
        "_yield_required",
    )

    def __init__(self, token: object) -> None:
        if token is not _DRIVER_TOKEN:
            raise TypeError("Relay Linux build worker driver is factory-owned")
        values = {
            "_kernel": None,
            "_claim": None,
            "_controller": None,
            "_spec": None,
            "_raw_destination": None,
            "_process": None,
            "_spawn_intended": False,
            "_pid": None,
            "_pgid": None,
            "_returncode": None,
            "_reaped": False,
            "_group_absent": False,
            "_signal_revoked": False,
            "_query_epoch": 0,
            "_query_inflight": None,
            "_term_intended": False,
            "_term_sent": False,
            "_term_ambiguous": False,
            "_term_deadline": None,
            "_kill_intended": False,
            "_kill_sent": False,
            "_kill_deadline": None,
            "_closed_handles": 0,
            "_close_intents": 0,
            "_handle_name": None,
            "_handle": None,
            "_raw_clear_intended": False,
            "_raw_cleared": False,
            "_yield_required": False,
            "_terminal_latched": None,
            "_state": "unbound",
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        values.clear()

    def _bind(self, take: _BuildWorkerKernelTake) -> bool:
        if type(take) is not _BuildWorkerKernelTake or take.status != "claimed":
            return False
        kernel = take.kernel
        claim = take.claim
        if type(claim) is not _BuildWorkerClaim:
            return False
        owner = kernel._owner
        worker = threading.current_thread()
        controller = owner._controller_destination._read()
        transition = kernel._transition
        initially_unbound = self._state == "unbound"
        valid_phase = (
            transition.phase == "claimed"
            if initially_unbound
            else transition.phase in {"claimed", "reported", "settled"}
        )
        if (
            owner._thread_destination._read() is not worker
            or type(controller) is not _BuildWorkerController
            or controller._thread() is not worker
            or not valid_phase
            or transition.worker is not worker
            or transition.claim is not claim
            or not claim._matches(owner._owner_token, worker)
        ):
            return False
        if initially_unbound:
            object.__setattr__(self, "_kernel", kernel)
            object.__setattr__(self, "_claim", claim)
            object.__setattr__(self, "_controller", controller)
            object.__setattr__(self, "_spec", owner._spec)
            object.__setattr__(self, "_raw_destination", owner._raw_destination)
            object.__setattr__(self, "_state", "ready")
        return bool(
            self._kernel is kernel
            and self._claim is claim
            and self._controller is controller
            and self._spec is owner._spec
            and self._raw_destination is owner._raw_destination
        )

    def _step(self) -> str:
        controller = self._controller
        if type(controller) is not _BuildWorkerController or not self._authority_is_current():
            return "invalid"
        if not self._recover_inflight():
            return self._state
        self._reconcile_signal_state()
        if self._yield_required:
            return "waiting"
        if self._state in {"terminal", "quarantined"}:
            return self._state
        if self._state != "ready":
            return self._process_step()
        if controller._termination_requested() and not self._spawn_intended:
            controller._fail()
            object.__setattr__(self, "_raw_cleared", True)
            object.__setattr__(self, "_state", "terminal")
            return self._state
        if not self._spawn_intended:
            object.__setattr__(self, "_spawn_intended", True)
            if not controller._transition("spawning"):
                controller._fail()
                object.__setattr__(self, "_state", "terminal")
                return self._state
            _spawn_registered_relay_linux_build(self._spec, self._raw_destination)
        process = self._raw_destination._read(self._spec)
        if process is not None:
            object.__setattr__(self, "_process", process)
            object.__setattr__(self, "_state", "identity")
            return "active"
        controller._fail()
        object.__setattr__(self, "_raw_cleared", True)
        object.__setattr__(self, "_state", "terminal")
        return self._state

    def _authority_is_current(self) -> bool:
        kernel = self._kernel
        claim = self._claim
        controller = self._controller
        if kernel is None or type(claim) is not _BuildWorkerClaim:
            return False
        owner = kernel._owner
        worker = threading.current_thread()
        transition = kernel._transition
        return bool(
            type(controller) is _BuildWorkerController
            and owner._thread_destination._read() is worker
            and controller._thread() is worker
            and transition.phase in {"claimed", "reported", "settled"}
            and transition.worker is worker
            and transition.claim is claim
            and claim._matches(owner._owner_token, worker)
            and self._spec is owner._spec
            and self._raw_destination is owner._raw_destination
        )

    def _note_failure(self) -> None:
        object.__setattr__(self, "_yield_required", True)
        controller = self._controller
        if type(controller) is _BuildWorkerController:
            controller._fail()

    def _wait_completed(self) -> None:
        if self._authority_is_current():
            object.__setattr__(self, "_yield_required", False)

    def _terminal_values(self) -> tuple[int | None, bool] | None:
        latched = self._terminal_latched
        if type(latched) is tuple:
            return latched
        if self._state != "terminal" or self._process is not None or not self._raw_cleared:
            return None
        if type(self._claim) is not _BuildWorkerClaim:
            return None
        returncode = self._returncode
        if returncode is None:
            terminal = (None, False)
            object.__setattr__(self, "_terminal_latched", terminal)
            return terminal
        controller = self._controller
        if (
            type(returncode) is not int
            or not self._reaped
            or not self._group_absent
            or self._closed_handles != _ALL_HANDLES
            or type(controller) is not _BuildWorkerController
        ):
            return None
        succeeded = bool(
            returncode == 0
            and not controller._failed()
            and not controller._termination_requested()
            and controller._control_value() is None
        )
        terminal = (returncode, succeeded)
        object.__setattr__(self, "_terminal_latched", terminal)
        return terminal

    def _kernel_claim(self) -> tuple[object, _BuildWorkerClaim] | None:
        claim = self._claim
        if type(claim) is not _BuildWorkerClaim or self._kernel is None:
            return None
        return self._kernel, claim

    def _controller_value(self) -> _BuildWorkerController | None:
        controller = self._controller
        return controller if type(controller) is _BuildWorkerController else None

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_LocalBuildWorkerDriver()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker driver is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker driver cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker driver cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker driver cannot be serialized")


def _new_local_build_worker_driver() -> _LocalBuildWorkerDriver:
    return _LocalBuildWorkerDriver(_DRIVER_TOKEN)


__all__: list[str] = []

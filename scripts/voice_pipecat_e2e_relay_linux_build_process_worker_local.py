"""Private local state for the sole relay Linux build worker.

Construction is deliberately data-only.  The driver may acquire the build
specification and raw-process destination only after the atomic registry has
bound the exact current registered thread to its worker claim.
"""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _BuildWorkerKernelTake,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerClaim,
    _BuildWorkerController,
)
from scripts.voice_pipecat_e2e_relay_linux_build_spawn_local import (
    _spawn_registered_relay_linux_build,
)

_DRIVER_TOKEN = object()


class _LocalBuildWorkerDriver:
    """One worker-local, initially authority-free build lifecycle record."""

    __slots__ = (
        "_claim",
        "_controller",
        "_kernel",
        "_process",
        "_raw_destination",
        "_spawn_intended",
        "_spawn_returned",
        "_spec",
        "_state",
    )

    def __init__(self, token: object) -> None:
        if token is not _DRIVER_TOKEN:
            raise TypeError("Relay Linux build worker driver is factory-owned")
        object.__setattr__(self, "_kernel", None)
        object.__setattr__(self, "_claim", None)
        object.__setattr__(self, "_controller", None)
        object.__setattr__(self, "_spec", None)
        object.__setattr__(self, "_raw_destination", None)
        object.__setattr__(self, "_process", None)
        object.__setattr__(self, "_spawn_intended", False)
        object.__setattr__(self, "_spawn_returned", False)
        object.__setattr__(self, "_state", "unbound")

    def _bind(self, take: _BuildWorkerKernelTake) -> bool:
        """Adopt raw authority only for the canonical current worker claim."""

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
            else transition.phase
            in {
                "claimed",
                "reported",
                "settled",
            }
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
        """Advance once without ever retrying a possibly-started spawn."""

        controller = self._controller
        if type(controller) is not _BuildWorkerController or not self._authority_is_current():
            return "invalid"
        if self._state in {"terminal", "quarantined"}:
            return self._state
        if self._state != "ready":
            return "invalid"
        if controller._termination_requested() and not self._spawn_intended:
            controller._fail()
            object.__setattr__(self, "_state", "terminal")
            return self._state
        if not self._spawn_intended:
            # The intent is durable before either the phase transition or the
            # registered-before-init factory can run.  Any return loss therefore
            # reconciles the raw destination and never issues a second spawn.
            object.__setattr__(self, "_spawn_intended", True)
            if not controller._transition("spawning"):
                controller._fail()
                object.__setattr__(self, "_state", "terminal")
                return self._state
            _spawn_registered_relay_linux_build(self._spec, self._raw_destination)
            object.__setattr__(self, "_spawn_returned", True)
        process = self._raw_destination._read(self._spec)
        if process is not None:
            object.__setattr__(self, "_process", process)
            controller._fail()
            controller._transition("quarantined")
            object.__setattr__(self, "_state", "quarantined")
            return self._state
        controller._fail()
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
        """Make a caught failure observable without discarding retained raw state."""

        controller = self._controller
        if type(controller) is _BuildWorkerController:
            controller._fail()

    def _terminal_values(self) -> tuple[int | None, bool] | None:
        if (
            self._state == "terminal"
            and self._process is None
            and type(self._claim) is _BuildWorkerClaim
        ):
            return None, False
        return None

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
    """Return a data-only driver carrying no raw, process, or callable authority."""

    return _LocalBuildWorkerDriver(_DRIVER_TOKEN)


__all__: list[str] = []

"""Atomic owner/controller/kernel registry for the private relay B0 worker.

This checkpoint supports one exact worker claim, prestart cancellation, and
worker terminal settlement. It deliberately has no facade, join, result
publication, or owner-release operation.
"""

from __future__ import annotations

import math
import threading

from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _new_build_process_owner,
    _RelayLinuxBuildCleanupAuthority,
    _RelayLinuxBuildProcessError,
    _RelayLinuxBuildProcessOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerClaim,
    _BuildWorkerController,
    _BuildWorkerTerminal,
    _new_build_worker_claim,
    _new_build_worker_controller,
    _new_build_worker_terminal,
)
from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _RawBuildProcessDestination,
    _RelayLinuxBuildSpec,
)

_DESTINATION_TOKEN = object()
_KERNEL_TOKEN = object()
_LOCK = threading.RLock()
_OWNERS: dict[object, _RelayLinuxBuildProcessOwner] = {}
_KERNELS: dict[object, _BuildWorkerKernel] = {}
_FAILURE = "Relay Linux build process registry is unavailable"
_TAKE_TOKEN = object()
_TRANSITION_TOKEN = object()


class _BuildKernelTransition:
    """One immutable atomic kernel lifecycle fact."""

    __slots__ = ("claim", "phase", "terminal", "worker")

    def __init__(
        self,
        token: object,
        *,
        phase: str,
        worker: object | None,
        claim: _BuildWorkerClaim | None,
        terminal: _BuildWorkerTerminal | None,
    ) -> None:
        available = phase in {"available", "cancelling"} and all(
            value is None for value in (worker, claim, terminal)
        )
        claimed = (
            phase == "claimed"
            and worker is not None
            and type(claim) is _BuildWorkerClaim
            and terminal is None
        )
        reported_or_settled = bool(
            phase in {"reported", "settled"}
            and type(terminal) is _BuildWorkerTerminal
            and (
                (worker is None and claim is None)
                or (worker is not None and type(claim) is _BuildWorkerClaim)
            )
        )
        if token is not _TRANSITION_TOKEN or not (available or claimed or reported_or_settled):
            raise TypeError("Relay Linux build kernel transition is factory-owned")
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "worker", worker)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "terminal", terminal)

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build kernel transition is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build kernel transition cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build kernel transition cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build kernel transition cannot be serialized")


def _kernel_transition(
    phase: str,
    *,
    worker: object | None = None,
    claim: _BuildWorkerClaim | None = None,
    terminal: _BuildWorkerTerminal | None = None,
) -> _BuildKernelTransition:
    return _BuildKernelTransition(
        _TRANSITION_TOKEN,
        phase=phase,
        worker=worker,
        claim=claim,
        terminal=terminal,
    )


class _BuildWorkerKernelTake:
    """Exact durable claim outcome for one prepublished worker identity."""

    __slots__ = ("claim", "kernel", "status")

    def __init__(
        self,
        token: object,
        *,
        claim: _BuildWorkerClaim | None,
        kernel: _BuildWorkerKernel,
        status: str,
    ) -> None:
        if (
            token is not _TAKE_TOKEN
            or status not in {"claimed", "cancelled"}
            or (status == "claimed") is not (type(claim) is _BuildWorkerClaim)
        ):
            raise TypeError("Relay Linux build worker take is factory-owned")
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "kernel", kernel)
        object.__setattr__(self, "status", status)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerKernelTake()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker take is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker take cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker take cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker take cannot be serialized")


class _BuildWorkerKernel:
    """Opaque registry record reserved before any future thread construction."""

    __slots__ = ("_owner", "_token", "_transition")

    def __init__(self, token: object, *, owner: _RelayLinuxBuildProcessOwner) -> None:
        if token is not _KERNEL_TOKEN:
            raise TypeError("Relay Linux build worker kernel is factory-owned")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_token", object())
        object.__setattr__(self, "_transition", _kernel_transition("available"))

    @property
    def _worker(self) -> object | None:
        return self._transition.worker

    @property
    def _claim(self) -> _BuildWorkerClaim | None:
        return self._transition.claim

    @property
    def _terminal(self) -> _BuildWorkerTerminal | None:
        return self._transition.terminal

    @property
    def _cancelled(self) -> bool:
        transition = self._transition
        return transition.phase == "settled" and transition.worker is None

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerKernel()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker kernel is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be serialized")


class _BuildOwnerDestination:
    """Caller-preowned exact owner graph for one exact spec/raw pair."""

    __slots__ = ("_owner", "_raw_destination", "_spec")

    def __init__(
        self,
        token: object,
        *,
        spec: _RelayLinuxBuildSpec,
        raw_destination: _RawBuildProcessDestination,
    ) -> None:
        if token is not _DESTINATION_TOKEN or not spec._matches_destination(raw_destination):
            raise TypeError("Relay Linux build owner destination is factory-owned")
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_raw_destination", raw_destination)
        object.__setattr__(
            self,
            "_owner",
            _new_build_process_owner(
                spec=spec,
                raw_destination=raw_destination,
                cleanup_key=object(),
            ),
        )

    def _read(self) -> _RelayLinuxBuildProcessOwner:
        return self._owner

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildOwnerDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build owner destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build owner destination cannot be serialized")


def _new_build_owner_destination(
    spec: _RelayLinuxBuildSpec,
    raw_destination: _RawBuildProcessDestination,
) -> _BuildOwnerDestination:
    if (
        type(spec) is not _RelayLinuxBuildSpec
        or type(raw_destination) is not _RawBuildProcessDestination
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return _BuildOwnerDestination(
        _DESTINATION_TOKEN,
        spec=spec,
        raw_destination=raw_destination,
    )


def _preown_build_process(
    *,
    spec: _RelayLinuxBuildSpec,
    raw_destination: _RawBuildProcessDestination,
    destination: _BuildOwnerDestination,
) -> _RelayLinuxBuildProcessOwner:
    if (
        type(spec) is not _RelayLinuxBuildSpec
        or type(raw_destination) is not _RawBuildProcessDestination
        or type(destination) is not _BuildOwnerDestination
        or destination._spec is not spec
        or destination._raw_destination is not raw_destination
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    owner = destination._owner
    if owner._spec is not spec or owner._raw_destination is not raw_destination:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _LOCK:
        key = owner._cleanup_authority._key
        registered = _OWNERS.get(key)
        if registered is None:
            if _OWNERS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            _OWNERS[key] = owner
        elif registered is not owner:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return owner


def _reserve_worker_kernel(owner: _RelayLinuxBuildProcessOwner) -> _BuildWorkerKernel:
    if type(owner) is not _RelayLinuxBuildProcessOwner:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _LOCK:
        if not _owner_registered_unlocked(owner):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        existing = owner._kernel_destination._read()
        if existing is None:
            if _KERNELS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            existing = _BuildWorkerKernel(_KERNEL_TOKEN, owner=owner)
            # Retain the candidate in the preowned graph before admitting it.
            # A control after this store is reconciled by the retry below.
            owner._kernel_destination._publish(existing)
        if type(existing) is not _BuildWorkerKernel or existing._owner is not owner:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        kernel = existing
        registered = _KERNELS.get(kernel._token)
        if registered is None:
            if _KERNELS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            _KERNELS[kernel._token] = kernel
        elif registered is not kernel:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return kernel


def _preown_worker_controller(
    owner: _RelayLinuxBuildProcessOwner,
    run_deadline: float,
) -> _BuildWorkerController:
    if (
        type(owner) is not _RelayLinuxBuildProcessOwner
        or type(run_deadline) is not float
        or not math.isfinite(run_deadline)
        or run_deadline <= 0.0
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _LOCK:
        if not _owner_registered_unlocked(owner):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        existing = owner._controller_destination._read()
        if existing is None:
            existing = _new_build_worker_controller(
                owner_token=owner._owner_token,
                run_deadline=run_deadline,
            )
            owner._controller_destination._publish(existing)
        if type(existing) is not _BuildWorkerController or not existing._matches(
            owner._owner_token, run_deadline
        ):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return existing


def _take_worker_kernel(token: object) -> _BuildWorkerKernelTake | None:
    """Bind the retained kernel to one exact prepublished worker identity."""

    worker = threading.current_thread()
    with _LOCK:
        kernel = _KERNELS.get(token)
        if type(kernel) is not _BuildWorkerKernel:
            return None
        owner = kernel._owner
        controller = owner._controller_destination._read()
        if (
            not _owner_registered_unlocked(owner)
            or owner._kernel_destination._read() is not kernel
            or owner._thread_destination._read() is not worker
            or type(controller) is not _BuildWorkerController
            or controller._thread() is not worker
        ):
            return None
        transition = kernel._transition
        if transition.phase == "cancelling":
            return None
        if transition.phase == "settled" and transition.worker is None:
            if type(transition.terminal) is not _BuildWorkerTerminal:
                return None
            return _BuildWorkerKernelTake(
                _TAKE_TOKEN,
                claim=None,
                kernel=kernel,
                status="cancelled",
            )
        if transition.phase == "available":
            claim = _new_build_worker_claim(
                owner_token=owner._owner_token,
                worker=worker,
            )
            transition = _kernel_transition(
                "claimed",
                worker=worker,
                claim=claim,
            )
            object.__setattr__(kernel, "_transition", transition)
            _kernel_claim_published()
        if transition.phase != "claimed" or transition.worker is not worker:
            return None
        claim = transition.claim
        if type(claim) is not _BuildWorkerClaim:
            return None
        return _BuildWorkerKernelTake(
            _TAKE_TOKEN,
            claim=claim,
            kernel=kernel,
            status="claimed",
        )


def _settle_worker_kernel(
    kernel: _BuildWorkerKernel,
    claim: _BuildWorkerClaim,
) -> bool:
    worker = threading.current_thread()
    if type(kernel) is not _BuildWorkerKernel or type(claim) is not _BuildWorkerClaim:
        return False
    with _LOCK:
        owner = kernel._owner
        controller = owner._controller_destination._read()
        transition = kernel._transition
        if (
            _KERNELS.get(kernel._token) is not kernel
            or not _owner_registered_unlocked(owner)
            or owner._kernel_destination._read() is not kernel
            or owner._thread_destination._read() is not worker
            or type(controller) is not _BuildWorkerController
            or controller._thread() is not worker
            or transition.claim is not claim
            or not claim._matches(owner._owner_token, worker)
        ):
            return False
        if (
            transition.phase == "settled"
            and transition.worker is worker
            and transition.claim is claim
            and type(transition.terminal) is _BuildWorkerTerminal
        ):
            return True
        if (
            transition.phase != "reported"
            or transition.worker is not worker
            or type(transition.terminal) is not _BuildWorkerTerminal
        ):
            return False
        object.__setattr__(
            kernel,
            "_transition",
            _kernel_transition(
                "settled",
                worker=worker,
                claim=claim,
                terminal=transition.terminal,
            ),
        )
        _kernel_terminal_published()
        return True


def _publish_worker_terminal(
    kernel: _BuildWorkerKernel,
    claim: _BuildWorkerClaim,
    *,
    returncode: int | None,
    succeeded: bool,
) -> bool:
    worker = threading.current_thread()
    if (
        type(kernel) is not _BuildWorkerKernel
        or type(claim) is not _BuildWorkerClaim
        or (returncode is not None and type(returncode) is not int)
        or type(succeeded) is not bool
        or (succeeded and returncode != 0)
    ):
        return False
    with _LOCK:
        owner = kernel._owner
        controller = owner._controller_destination._read()
        transition = kernel._transition
        if (
            _KERNELS.get(kernel._token) is not kernel
            or not _owner_registered_unlocked(owner)
            or owner._kernel_destination._read() is not kernel
            or owner._thread_destination._read() is not worker
            or type(controller) is not _BuildWorkerController
            or controller._thread() is not worker
            or transition.claim is not claim
            or not claim._matches(owner._owner_token, worker)
        ):
            return False
        if (
            transition.phase in {"reported", "settled"}
            and transition.worker is worker
            and transition.claim is claim
            and type(transition.terminal) is _BuildWorkerTerminal
        ):
            terminal = transition.terminal
            return bool(terminal.returncode == returncode and terminal.succeeded is succeeded)
        if transition.phase != "claimed" or transition.worker is not worker:
            return False
        terminal = _new_build_worker_terminal(
            owner_token=owner._owner_token,
            returncode=returncode,
            succeeded=succeeded,
        )
        object.__setattr__(
            kernel,
            "_transition",
            _kernel_transition(
                "reported",
                worker=worker,
                claim=claim,
                terminal=terminal,
            ),
        )
        _kernel_report_published()
        return True


def _cancel_unstarted_worker_kernel(
    kernel: _BuildWorkerKernel,
) -> bool:
    if type(kernel) is not _BuildWorkerKernel:
        return False
    with _LOCK:
        owner = kernel._owner
        if _KERNELS.get(kernel._token) is not kernel:
            return False
        transition = kernel._transition
        if transition.phase == "settled":
            return bool(
                transition.worker is None and type(transition.terminal) is _BuildWorkerTerminal
            )
        if transition.phase == "available":
            transition = _kernel_transition("cancelling")
            object.__setattr__(kernel, "_transition", transition)
            _kernel_cancel_published()
        if transition.phase == "cancelling":
            terminal = _new_build_worker_terminal(
                owner_token=owner._owner_token,
                returncode=None,
                succeeded=False,
            )
            transition = _kernel_transition("reported", terminal=terminal)
            object.__setattr__(kernel, "_transition", transition)
            _kernel_cancel_reported()
        if transition.phase != "reported" or transition.worker is not None:
            return False
        terminal = transition.terminal
        if type(terminal) is not _BuildWorkerTerminal:
            return False
        object.__setattr__(
            kernel,
            "_transition",
            _kernel_transition("settled", terminal=terminal),
        )
        _kernel_terminal_published()
        return True


def _resolve_cleanup_authority(
    authority: _RelayLinuxBuildCleanupAuthority,
) -> _RelayLinuxBuildProcessOwner | None:
    if type(authority) is not _RelayLinuxBuildCleanupAuthority or not authority._is_authentic():
        return None
    with _LOCK:
        return _OWNERS.get(authority._key)


def _controller_for_worker_token(token: object) -> _BuildWorkerController | None:
    with _LOCK:
        kernel = _KERNELS.get(token)
        if type(kernel) is not _BuildWorkerKernel:
            return None
        owner = kernel._owner
        controller = owner._controller_destination._read()
        if (
            not _owner_registered_unlocked(owner)
            or owner._kernel_destination._read() is not kernel
            or type(controller) is not _BuildWorkerController
            or not controller._matches(owner._owner_token)
        ):
            return None
        return controller


def _owner_registered_unlocked(owner: _RelayLinuxBuildProcessOwner) -> bool:
    authority = owner._cleanup_authority
    return _OWNERS.get(authority._key) is owner


def _kernel_claim_published() -> None:
    """Control seam after the exact worker capability is durable."""


def _kernel_cancel_published() -> None:
    """Control seam after irreversible cancellation reservation."""


def _kernel_cancel_reported() -> None:
    """Control seam after the cancellation terminal is durable."""


def _kernel_report_published() -> None:
    """Control seam after the exact worker terminal is durable."""


def _kernel_terminal_published() -> None:
    """Deterministic control seam after the exact terminal fact is durable."""


__all__: list[str] = []

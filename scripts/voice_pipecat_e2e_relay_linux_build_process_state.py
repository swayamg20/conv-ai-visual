"""Factory-owned values for the dormant relay B0 build controller.

This module deliberately contains no worker, subprocess, signal, wait, or
terminal-publication operation.  It only constructs an identity-bound owner
graph which a later dedicated worker slice may consume.
"""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _RawBuildProcessDestination,
    _RelayLinuxBuildSpec,
)

_OWNER_TOKEN = object()
_AUTHORITY_TOKEN = object()
_RECEIPT_TOKEN = object()
_DESTINATION_TOKEN = object()
_FACADE_TOKEN = object()
_FAILURE = "Relay Linux build process state is invalid"

_FACADE_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"thread-ready", "cancelled", "released"}),
    "thread-ready": frozenset({"start-intended", "cancelled"}),
    "start-intended": frozenset({"started", "cancelled"}),
    "started": frozenset({"joined"}),
    "joined": frozenset({"released"}),
    "cancelled": frozenset({"released"}),
    "released": frozenset(),
}


class _RelayLinuxBuildProcessError(RuntimeError):
    """The dormant build controller state was inconsistent."""

    def __repr__(self) -> str:
        return "_RelayLinuxBuildProcessError()"


class _RelayLinuxBuildCleanupAuthority:
    """Stable opaque key for one retained build owner graph."""

    __slots__ = ("_authentic", "_key")

    def __init__(
        self,
        token: object,
        *,
        key: object,
    ) -> None:
        if token is not _AUTHORITY_TOKEN or key is None:
            raise TypeError("Relay Linux build cleanup authority is factory-owned")
        object.__setattr__(self, "_authentic", _AUTHORITY_TOKEN)
        object.__setattr__(self, "_key", key)

    def _is_authentic(self) -> bool:
        try:
            return object.__getattribute__(self, "_authentic") is _AUTHORITY_TOKEN
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildCleanupAuthority()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build cleanup authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build cleanup authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build cleanup authority cannot be serialized")


class _RelayLinuxBuildCleanupRequired(_RelayLinuxBuildProcessError):
    """Stable fixed failure bound to one cleanup authority."""

    __slots__ = ("_cleanup_authority",)

    def __init__(
        self,
        token: object,
        *,
        authority: _RelayLinuxBuildCleanupAuthority,
    ) -> None:
        if token is not _OWNER_TOKEN or type(authority) is not _RelayLinuxBuildCleanupAuthority:
            raise TypeError("Relay Linux build cleanup failure is factory-owned")
        super().__init__("Relay Linux build process cleanup requires retry")
        object.__setattr__(self, "_cleanup_authority", authority)

    @property
    def cleanup_authority(self) -> _RelayLinuxBuildCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "_RelayLinuxBuildCleanupRequired()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build cleanup failure is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build cleanup failure cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build cleanup failure cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build cleanup failure cannot be serialized")


class _RelayLinuxBuildProcessReceipt:
    """Falsey immutable result slot value for a future exact zero-exit build."""

    __slots__ = ("_owner_token", "status")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Relay Linux build process receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "status", "build-process-exited-zero")

    def _matches(self, owner_token: object) -> bool:
        return self._owner_token is owner_token

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildProcessReceipt(status='build-process-exited-zero')"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build process receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build process receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build process receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build process receipt cannot be serialized")


class _BuildProcessFacadeState:
    """Path-free caller coordination; it never owns a thread or child."""

    __slots__ = (
        "_condition",
        "_kernel_release_phase",
        "_operation_lock",
        "_owner_token",
        "_phase",
        "_release_requested",
        "_run_deadline",
        "_start_effect_phase",
    )

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _FACADE_TOKEN or owner_token is None:
            raise TypeError("Relay Linux build process facade state is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_operation_lock", threading.RLock())
        object.__setattr__(self, "_condition", threading.Condition())
        object.__setattr__(self, "_kernel_release_phase", "none")
        object.__setattr__(self, "_phase", "new")
        object.__setattr__(self, "_release_requested", False)
        object.__setattr__(self, "_run_deadline", None)
        object.__setattr__(self, "_start_effect_phase", "none")

    def _matches(self, owner_token: object) -> bool:
        return self._owner_token is owner_token

    def _bind_run_deadline(self, value: float) -> bool:
        with self._condition:
            if self._run_deadline is None:
                object.__setattr__(self, "_run_deadline", value)
            valid = self._run_deadline == value
            self._condition.notify_all()
            return valid

    def _transition(self, phase: str) -> bool:
        with self._condition:
            current = self._phase
            if phase == current:
                return True
            if phase not in _FACADE_TRANSITIONS.get(current, frozenset()):
                return False
            object.__setattr__(self, "_phase", phase)
            self._condition.notify_all()
            return True

    def _phase_value(self) -> str:
        with self._condition:
            return self._phase

    def _request_release(self) -> None:
        with self._condition:
            object.__setattr__(self, "_release_requested", True)
            self._condition.notify_all()

    def _release_was_requested(self) -> bool:
        with self._condition:
            return self._release_requested

    def _enter_start_effect(self) -> bool:
        with self._condition:
            if self._phase != "start-intended" or self._release_requested:
                return False
            if self._start_effect_phase == "none":
                object.__setattr__(self, "_start_effect_phase", "entered")
            self._condition.notify_all()
            return self._start_effect_phase == "entered"

    def _reject_start_effect(self) -> bool:
        with self._condition:
            if self._start_effect_phase == "entered":
                object.__setattr__(self, "_start_effect_phase", "rejected")
            self._condition.notify_all()
            return self._start_effect_phase == "rejected"

    def _start_effect_was_entered(self) -> bool:
        with self._condition:
            return self._start_effect_phase == "entered"

    def _intend_kernel_release(self) -> bool:
        with self._condition:
            if self._kernel_release_phase == "none":
                object.__setattr__(self, "_kernel_release_phase", "intended")
            valid = self._kernel_release_phase in {"intended", "released"}
            self._condition.notify_all()
            return valid

    def _complete_kernel_release(self) -> bool:
        with self._condition:
            if self._kernel_release_phase == "intended":
                object.__setattr__(self, "_kernel_release_phase", "released")
            valid = self._kernel_release_phase == "released"
            self._condition.notify_all()
            return valid

    def _kernel_release_phase_value(self) -> str:
        with self._condition:
            return self._kernel_release_phase

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildProcessFacadeState()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build process facade state is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build process facade state cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build process facade state cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build process facade state cannot be serialized")


class _IdentityDestination:
    """Single-assignment exact-identity slot for a thread or worker kernel."""

    __slots__ = ("_kind", "_lock", "_value")

    def __init__(self, token: object, *, kind: str) -> None:
        if token is not _DESTINATION_TOKEN or kind not in {"controller", "thread", "kernel"}:
            raise TypeError("Relay Linux build identity destination is factory-owned")
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_value", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _publish(self, value: object) -> None:
        if value is None:
            raise TypeError("Relay Linux build identity publication is invalid")
        with self._lock:
            if self._value is None:
                object.__setattr__(self, "_value", value)
            elif self._value is not value:
                raise TypeError("Relay Linux build identity publication is invalid")

    def _read(self) -> object | None:
        with self._lock:
            return self._value

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build identity destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build identity destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build identity destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build identity destination cannot be serialized")


class _ResultDestination:
    """Caller-preowned slot that constructs one canonical zero-exit receipt."""

    __slots__ = ("_lock", "_owner_token", "_receipt")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _DESTINATION_TOKEN:
            raise TypeError("Relay Linux build result destination is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_receipt", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _read(self) -> _RelayLinuxBuildProcessReceipt | None:
        with self._lock:
            return self._receipt

    def _publish_success(self, owner_token: object) -> _RelayLinuxBuildProcessReceipt:
        if owner_token is not self._owner_token:
            raise TypeError("Relay Linux build result publication is invalid")
        with self._lock:
            receipt = self._receipt
            if receipt is None:
                receipt = _RelayLinuxBuildProcessReceipt(
                    _RECEIPT_TOKEN,
                    owner_token=owner_token,
                )
                object.__setattr__(self, "_receipt", receipt)
                _result_receipt_published()
            if type(receipt) is not _RelayLinuxBuildProcessReceipt or not receipt._matches(
                owner_token
            ):
                raise TypeError("Relay Linux build result publication is invalid")
            return receipt

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build result destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build result destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build result destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build result destination cannot be serialized")


class _RelayLinuxBuildProcessOwner:
    """Preowned graph root; this checkpoint starts no worker or process."""

    __slots__ = (
        "_cleanup_authority",
        "_controller_destination",
        "_facade_state",
        "_kernel_destination",
        "_owner_token",
        "_raw_destination",
        "_result_destination",
        "_spec",
        "_thread_destination",
    )

    def __init__(
        self,
        token: object,
        *,
        spec: _RelayLinuxBuildSpec,
        raw_destination: _RawBuildProcessDestination,
        cleanup_key: object,
    ) -> None:
        if token is not _OWNER_TOKEN or not spec._matches_destination(raw_destination):
            raise TypeError("Relay Linux build process owner is factory-owned")
        owner_token = object()
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_raw_destination", raw_destination)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(
            self,
            "_facade_state",
            _BuildProcessFacadeState(_FACADE_TOKEN, owner_token=owner_token),
        )
        object.__setattr__(
            self,
            "_cleanup_authority",
            _RelayLinuxBuildCleanupAuthority(_AUTHORITY_TOKEN, key=cleanup_key),
        )
        object.__setattr__(
            self,
            "_controller_destination",
            _IdentityDestination(_DESTINATION_TOKEN, kind="controller"),
        )
        object.__setattr__(
            self,
            "_thread_destination",
            _IdentityDestination(_DESTINATION_TOKEN, kind="thread"),
        )
        object.__setattr__(
            self,
            "_kernel_destination",
            _IdentityDestination(_DESTINATION_TOKEN, kind="kernel"),
        )
        object.__setattr__(
            self,
            "_result_destination",
            _ResultDestination(_DESTINATION_TOKEN, owner_token=owner_token),
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildProcessOwner()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build process owner is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build process owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build process owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build process owner cannot be serialized")


def _new_build_process_owner(
    *,
    spec: _RelayLinuxBuildSpec,
    raw_destination: _RawBuildProcessDestination,
    cleanup_key: object,
) -> _RelayLinuxBuildProcessOwner:
    return _RelayLinuxBuildProcessOwner(
        _OWNER_TOKEN,
        spec=spec,
        raw_destination=raw_destination,
        cleanup_key=cleanup_key,
    )


def _result_receipt_published() -> None:
    """Deterministic cut after the canonical result is durable."""


__all__: list[str] = []

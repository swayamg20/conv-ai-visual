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
_FAILURE = "Relay Linux build process state is invalid"


class _RelayLinuxBuildProcessError(RuntimeError):
    """The dormant build controller state was inconsistent."""

    def __repr__(self) -> str:
        return "_RelayLinuxBuildProcessError()"


class _RelayLinuxBuildCleanupAuthority:
    """Stable opaque key for one retained build owner graph."""

    __slots__ = ("_authentic", "_key")

    def __init__(self, token: object, *, key: object) -> None:
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


class _IdentityDestination:
    """Single-assignment exact-identity slot for a thread or worker kernel."""

    __slots__ = ("_kind", "_lock", "_value")

    def __init__(self, token: object, *, kind: str) -> None:
        if token is not _DESTINATION_TOKEN or kind not in {"thread", "kernel"}:
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
    """Caller-preowned empty slot; this slice exposes no commit operation."""

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
            "_cleanup_authority",
            _RelayLinuxBuildCleanupAuthority(_AUTHORITY_TOKEN, key=cleanup_key),
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


__all__: list[str] = []

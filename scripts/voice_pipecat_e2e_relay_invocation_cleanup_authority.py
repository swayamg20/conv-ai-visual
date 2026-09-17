"""Recoverable registry and terminal proof for invocation cleanup authority."""

from __future__ import annotations

import threading
import weakref

from scripts.voice_pipecat_e2e_relay_invocation_values import (
    _FAILURE,
    RelayInvocationError,
)

_AUTHORITY_TOKEN = object()
_CLEANUP_FAILURE = "Relay invocation cleanup failed"
_MAX_ACTIVE_INVOCATIONS = 32
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[object, object] = {}
_OWNER_REFERENCES: weakref.WeakKeyDictionary[
    RelayInvocationCleanupAuthority, weakref.ReferenceType[object]
] = weakref.WeakKeyDictionary()
_TERMINAL_AUTHORITIES: weakref.WeakKeyDictionary[RelayInvocationCleanupAuthority, bool] = (
    weakref.WeakKeyDictionary()
)


class RelayInvocationCleanupAuthority:
    """Opaque key whose graph never reaches callbacks, paths, or child owners."""

    __slots__ = ("__weakref__", "_key")

    def __init__(self, token: object, *, key: object) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Relay invocation cleanup authority is factory-owned")
        object.__setattr__(self, "_key", key)

    def __repr__(self) -> str:
        return "RelayInvocationCleanupAuthority()"

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay invocation cleanup authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay invocation cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation cleanup authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation cleanup authority cannot be serialized")


class RelayInvocationCleanupRequired(RelayInvocationError):
    """Fixed retry failure carrying only one graph-opaque authority."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: RelayInvocationCleanupAuthority) -> None:
        if type(authority) is not RelayInvocationCleanupAuthority:
            raise TypeError("Relay invocation cleanup error is factory-owned")
        super().__init__(_CLEANUP_FAILURE)
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> RelayInvocationCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "RelayInvocationCleanupRequired('Relay invocation cleanup failed')"


def _new_cleanup_authority() -> RelayInvocationCleanupAuthority:
    return RelayInvocationCleanupAuthority(_AUTHORITY_TOKEN, key=object())


def _register_cleanup_owner(
    authority: RelayInvocationCleanupAuthority,
    owner: object,
    *,
    max_active_invocations: int = _MAX_ACTIVE_INVOCATIONS,
) -> None:
    if (
        type(authority) is not RelayInvocationCleanupAuthority
        or owner is None
        or type(max_active_invocations) is not int
        or max_active_invocations <= 0
    ):
        raise RelayInvocationError(_CLEANUP_FAILURE)
    try:
        candidate_reference = weakref.ref(owner)
    except TypeError:
        raise RelayInvocationError(_CLEANUP_FAILURE) from None
    with _REGISTRY_LOCK:
        if _TERMINAL_AUTHORITIES.get(authority) is True:
            raise RelayInvocationError(_CLEANUP_FAILURE)
        reference = _OWNER_REFERENCES.get(authority)
        referenced_owner = reference() if reference is not None else None
        if referenced_owner is not None and referenced_owner is not owner:
            raise RelayInvocationError(_CLEANUP_FAILURE)
        current = _REGISTRY.get(authority._key)
        if current is not None and current is not owner:
            raise RelayInvocationError(_CLEANUP_FAILURE)
        if current is None and len(_REGISTRY) >= max_active_invocations:
            raise RelayInvocationError(_FAILURE)
        _OWNER_REFERENCES[authority] = candidate_reference
        _REGISTRY[authority._key] = owner


def _resolve_cleanup_owner(authority: object, owner_type: type[object]) -> object | None:
    if type(authority) is owner_type:
        return authority
    if type(authority) is not RelayInvocationCleanupAuthority:
        return None
    with _REGISTRY_LOCK:
        owner = _REGISTRY.get(authority._key)
        reference = _OWNER_REFERENCES.get(authority)
        recovered = reference() if reference is not None else None
        if owner is not None and owner is not recovered:
            return None
        if owner is None and _TERMINAL_AUTHORITIES.get(authority) is not True:
            if type(recovered) is owner_type and len(_REGISTRY) < _MAX_ACTIVE_INVOCATIONS:
                _REGISTRY[authority._key] = recovered
                owner = recovered
    return owner if type(owner) is owner_type else None


def _cleanup_authority_is_terminal(authority: object) -> bool:
    if type(authority) is not RelayInvocationCleanupAuthority:
        return False
    with _REGISTRY_LOCK:
        return bool(
            _TERMINAL_AUTHORITIES.get(authority) is True and authority._key not in _REGISTRY
        )


def _release_cleanup_owner(
    authority: RelayInvocationCleanupAuthority,
    owner: object,
) -> None:
    with _REGISTRY_LOCK:
        current = _REGISTRY.get(authority._key)
        reference = _OWNER_REFERENCES.get(authority)
        referenced_owner = reference() if reference is not None else None
        if (current is not None and current is not owner) or (
            referenced_owner is not None and referenced_owner is not owner
        ):
            raise RelayInvocationError(_CLEANUP_FAILURE)
        _TERMINAL_AUTHORITIES[authority] = True
        if current is owner:
            del _REGISTRY[authority._key]
        _OWNER_REFERENCES.pop(authority, None)


__all__: list[str] = []

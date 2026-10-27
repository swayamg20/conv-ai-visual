"""Graph-opaque retry registry for Coturn evidence-drain cleanup."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_runtime_values import CoturnRuntimeError

_TOKEN = object()
_MAX_RETAINED_DRAINS = 256
_LOCK = threading.Lock()
_OWNERS: dict[object, object] = {}
_CANONICAL: dict[object, object] = {}


class CoturnEvidenceDrainCleanupAuthority:
    __slots__ = ("_key",)

    def __init__(self, token: object, key: object) -> None:
        if token is not _TOKEN:
            raise TypeError("Coturn evidence drain cleanup authority is factory-owned")
        object.__setattr__(self, "_key", key)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("Coturn evidence drain cleanup authority is immutable")

    def __copy__(self) -> CoturnEvidenceDrainCleanupAuthority:
        raise TypeError("Coturn evidence drain cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> CoturnEvidenceDrainCleanupAuthority:
        raise TypeError("Coturn evidence drain cleanup authority cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn evidence drain cleanup authority cannot be copied or serialized")

    def __repr__(self) -> str:
        return "CoturnEvidenceDrainCleanupAuthority()"


class CoturnEvidenceDrainCleanupRequired(CoturnRuntimeError):
    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: CoturnEvidenceDrainCleanupAuthority) -> None:
        if type(authority) is not CoturnEvidenceDrainCleanupAuthority:
            raise TypeError("Coturn evidence drain cleanup error is factory-owned")
        super().__init__("Coturn evidence drain cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> CoturnEvidenceDrainCleanupAuthority:
        return self._cleanup_authority


def retain_cleanup_authority(drain: object) -> CoturnEvidenceDrainCleanupAuthority | None:
    operation_lock = object.__getattribute__(drain, "_operation_lock")
    state_lock = object.__getattribute__(drain, "_lock")
    with operation_lock:
        with state_lock:
            if object.__getattribute__(drain, "_state") in {"complete", "cleaned"}:
                return None
            current = object.__getattribute__(drain, "_cleanup_authority")
            if type(current) is not CoturnEvidenceDrainCleanupAuthority:
                return None
        with _LOCK:
            retained = _OWNERS.get(current._key)
            if retained is None:
                if len(_OWNERS) >= _MAX_RETAINED_DRAINS:
                    return None
                _OWNERS[current._key] = drain
            elif retained is not drain:
                return None
            return current


def new_cleanup_authority() -> CoturnEvidenceDrainCleanupAuthority:
    """Preown the only public key before the drain can start any effect."""

    return CoturnEvidenceDrainCleanupAuthority(_TOKEN, object())


def resolve_cleanup_authority(authority: object) -> object | None:
    if type(authority) is not CoturnEvidenceDrainCleanupAuthority:
        return authority
    with _LOCK:
        return _OWNERS.get(authority._key)


def release_cleanup_authority(drain: object) -> None:
    authority = object.__getattribute__(drain, "_cleanup_authority")
    if type(authority) is not CoturnEvidenceDrainCleanupAuthority:
        return
    with _LOCK:
        retained = _OWNERS.get(authority._key)
        if retained is drain:
            del _OWNERS[authority._key]


def publish_canonical_return(key: object, drain: object) -> bool:
    with _LOCK:
        current = _CANONICAL.get(key)
        if current is None:
            if len(_CANONICAL) >= _MAX_RETAINED_DRAINS:
                return False
            _CANONICAL[key] = drain
            return True
        return current is drain


def release_canonical_return(key: object, drain: object) -> bool:
    with _LOCK:
        current = _CANONICAL.get(key)
        if current is drain:
            del _CANONICAL[key]
            return True
        return current is None


def return_canonical_drain(key: object) -> object | None:
    with _LOCK:
        if key not in _CANONICAL:
            raise CoturnRuntimeError("Coturn evidence drain input is invalid")
        return _CANONICAL[key]


def retained_owner_count() -> int:
    with _LOCK:
        return len(_OWNERS)


def canonical_drain_count() -> int:
    with _LOCK:
        return len(_CANONICAL)

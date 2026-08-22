"""Public, sanitized values for the relay B0 aggregate owner."""

from __future__ import annotations

from collections.abc import Callable

_AUTHORITY_TOKEN = object()
_OBSERVATION_TOKEN = object()
_FAILURE = "Relay probe execution failed"
_CLEANUP_FAILURE = "Relay probe cleanup requires retry"


class RelayProbeOwnerError(RuntimeError):
    """The exact relay probe could not finish through its owned boundary."""

    def __repr__(self) -> str:
        return "RelayProbeOwnerError()"


class RelayProbeCleanupAuthority:
    """Graph-opaque key for retrying one aggregate's remaining cleanup."""

    __slots__ = ("_authentic", "_key")

    def __init__(self, token: object, *, key: object) -> None:
        if token is not _AUTHORITY_TOKEN or key is None:
            raise TypeError("Relay probe cleanup authority is factory-owned")
        object.__setattr__(self, "_authentic", _AUTHORITY_TOKEN)
        object.__setattr__(self, "_key", key)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayProbeCleanupAuthority()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("Relay probe cleanup authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay probe cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe cleanup authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe cleanup authority cannot be serialized")

    def _is_authentic(self) -> bool:
        try:
            return object.__getattribute__(self, "_authentic") is _AUTHORITY_TOKEN
        except BaseException:
            return False


class RelayProbeCleanupRequired(RelayProbeOwnerError):
    """Fixed failure carrying only the aggregate's opaque retry key."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: RelayProbeCleanupAuthority) -> None:
        if type(authority) is not RelayProbeCleanupAuthority:
            raise TypeError("Relay probe cleanup error is factory-owned")
        super().__init__(_CLEANUP_FAILURE)
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> RelayProbeCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "RelayProbeCleanupRequired()"


class RelayProbeObservation:
    """Falsey B0 terminal artifact published only after complete teardown."""

    __slots__ = (
        "artifacts_deleted",
        "cleanup_complete",
        "coturn_grammar_verified",
        "qualification_verified",
        "source_revalidated",
        "status",
    )

    def __init__(
        self,
        token: object,
        *,
        publisher: Callable[[RelayProbeObservation], bool],
    ) -> None:
        authorized = token is _OBSERVATION_TOKEN
        token = None
        if not authorized or not callable(publisher):
            raise TypeError("Relay probe observation is factory-owned")
        object.__setattr__(self, "status", "probe-observed")
        object.__setattr__(self, "artifacts_deleted", True)
        object.__setattr__(self, "cleanup_complete", True)
        object.__setattr__(self, "coturn_grammar_verified", False)
        object.__setattr__(self, "qualification_verified", False)
        object.__setattr__(self, "source_revalidated", True)
        if not publisher(self):
            raise TypeError("Relay probe observation publication failed")

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayProbeObservation(status='probe-observed', qualification_verified=False)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay probe observation is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay probe observation cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe observation cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe observation cannot be serialized")


def _new_cleanup_authority(key: object) -> RelayProbeCleanupAuthority:
    return RelayProbeCleanupAuthority(_AUTHORITY_TOKEN, key=key)


def _new_observation(
    publisher: Callable[[RelayProbeObservation], bool],
) -> RelayProbeObservation:
    return RelayProbeObservation(_OBSERVATION_TOKEN, publisher=publisher)


__all__ = [
    "RelayProbeCleanupAuthority",
    "RelayProbeCleanupRequired",
    "RelayProbeObservation",
    "RelayProbeOwnerError",
]

"""Effect-free private values for the concrete relay invocation adapter."""

from __future__ import annotations

_SELECTION_TOKEN = object()
_ROLE_AUTHORITY_TOKEN = object()
_ROLES = frozenset({"app", "web", "browser"})


class _RelayConcreteInvocationSelection:
    """Inert singleton selecting post-consumption concrete construction."""

    __slots__ = ()

    def __init__(self, token: object) -> None:
        if token is not _SELECTION_TOKEN:
            raise TypeError("Relay concrete invocation selection is factory-owned")

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayConcreteInvocationSelection()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay concrete invocation selection is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay concrete invocation selection cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete invocation selection cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete invocation selection cannot be serialized")


_CONCRETE_INVOCATION_SELECTION = _RelayConcreteInvocationSelection(_SELECTION_TOKEN)


def _concrete_invocation_selection() -> _RelayConcreteInvocationSelection:
    return _CONCRETE_INVOCATION_SELECTION


def _is_concrete_invocation_selection(value: object) -> bool:
    return value is _CONCRETE_INVOCATION_SELECTION


class _RelayProcessRoleAuthority:
    """Immutable view of one role in a destination-owned process graph."""

    __slots__ = ("__weakref__", "_claim", "_destination", "_role_name")

    def __init__(
        self,
        token: object,
        *,
        destination: object,
        claim: object,
        pair_key: object,
        role: str,
    ) -> None:
        if (
            token is not _ROLE_AUTHORITY_TOKEN
            or type(claim) is not object
            or type(pair_key) is not object
            or role not in _ROLES
        ):
            raise TypeError("Relay concrete process role authority is factory-owned")
        object.__setattr__(self, "_destination", destination)
        object.__setattr__(self, "_claim", claim)
        object.__setattr__(self, "_role_name", role)

    @property
    def _pair_key(self) -> object | None:
        try:
            return self._destination._role_pair_key(  # type: ignore[attr-defined]
                self._claim,
                self,
                self._role_name,
            )
        except BaseException:
            return None

    @property
    def _role(self) -> str | None:
        return self._role_name if self._pair_key is not None else None

    def _matches(self, pair_key: object, role: str) -> bool:
        try:
            return bool(
                role == self._role_name
                and self._destination._role_authority_matches(  # type: ignore[attr-defined]
                    self._claim,
                    pair_key,
                    role,
                    self,
                )
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayProcessRoleAuthority()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay concrete process role authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay concrete process role authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete process role authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete process role authority cannot be serialized")


def _new_process_role_authority(
    destination: object,
    claim: object,
    pair_key: object,
    role: str,
) -> _RelayProcessRoleAuthority:
    return _RelayProcessRoleAuthority(
        _ROLE_AUTHORITY_TOKEN,
        destination=destination,
        claim=claim,
        pair_key=pair_key,
        role=role,
    )


__all__: list[str] = []

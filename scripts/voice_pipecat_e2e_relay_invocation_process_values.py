"""Effect-free private values for the concrete relay invocation adapter."""

from __future__ import annotations

_SELECTION_TOKEN = object()


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


__all__: list[str] = []

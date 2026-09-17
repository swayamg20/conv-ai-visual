"""Shared sanitized values for the unwired Coturn runtime adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")
_MISSING = object()

ControlSignal = tuple[type[KeyboardInterrupt] | type[SystemExit], int | None]


class CoturnRuntimeError(RuntimeError):
    """An owned lifecycle step could not complete safely."""


def call_runtime_boundary(operation: Callable[[], _T], *, failure: str) -> _T:
    """Discard an implementation traceback before emitting a fixed failure."""

    result: _T | object = _MISSING
    control: ControlSignal | None = None
    try:
        result = operation()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    operation = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if result is _MISSING:
        raise CoturnRuntimeError(failure) from None
    return result  # type: ignore[return-value]


def control_signal(error: KeyboardInterrupt | SystemExit) -> ControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt, None
    code = error.code
    return SystemExit, code if code is None or type(code) is int else 1


def raise_control(control: ControlSignal, cleanup_authority: object | None = None) -> None:
    kind, code = control
    if kind is KeyboardInterrupt:
        error: KeyboardInterrupt | SystemExit = KeyboardInterrupt()
    else:
        error = SystemExit(code)
    if cleanup_authority is not None:
        error.cleanup_authority = cleanup_authority  # type: ignore[attr-defined]
    raise error from None


__all__ = ["CoturnRuntimeError"]

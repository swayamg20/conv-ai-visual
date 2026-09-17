"""Traceback-safe boundary for pure Coturn inspection validators."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def validate_without_raw_traceback(
    operation: Callable[[], _T],
    *,
    error_type: type[Exception],
    fallback: str,
    allowed: frozenset[str],
) -> _T:
    """Discard the raw parser traceback before emitting one fixed outcome."""

    receipt: _T | None = None
    message = fallback
    candidate: str | None = None
    control: tuple[type[KeyboardInterrupt] | type[SystemExit], int | None] | None = None
    try:
        receipt = operation()
    except (KeyboardInterrupt, SystemExit) as error:
        control = _control_signal(error)
    except BaseException as error:
        if isinstance(error, error_type):
            candidate = str(error)
            if candidate in allowed:
                message = candidate
    operation = None  # type: ignore[assignment]
    candidate = None
    if control is not None:
        _raise_control(control)
    if receipt is None:
        raise error_type(message) from None
    return receipt


def _control_signal(
    error: KeyboardInterrupt | SystemExit,
) -> tuple[type[KeyboardInterrupt] | type[SystemExit], int | None]:
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt, None
    code = error.code
    return SystemExit, code if code is None or type(code) is int else 1


def _raise_control(
    control: tuple[type[KeyboardInterrupt] | type[SystemExit], int | None],
) -> None:
    kind, code = control
    if kind is KeyboardInterrupt:
        raise KeyboardInterrupt from None
    raise SystemExit(code) from None


__all__ = ["validate_without_raw_traceback"]

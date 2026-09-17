"""Deep bounded exception-graph scanner for Coturn TLS secrecy tests."""

from __future__ import annotations

import os
import types
from collections import deque
from queue import Queue

_MAX_DEPTH = 20
_MAX_OBJECTS = 8_192


def traceback_contains(exc: BaseException, *secrets: str | bytes) -> bool:
    needles = tuple(
        value if isinstance(value, bytes) else value.encode("utf-8") for value in secrets
    )
    return _contains(exc, needles, set(), depth=0)


def _contains(
    value: object,
    needles: tuple[bytes, ...],
    seen: set[int],
    *,
    depth: int,
) -> bool:
    if depth > _MAX_DEPTH or len(seen) >= _MAX_OBJECTS:
        return False
    identifier = id(value)
    if identifier in seen:
        return False
    seen.add(identifier)
    if isinstance(value, bytes):
        return any(needle in value for needle in needles)
    if isinstance(value, (bytearray, memoryview)):
        raw = bytes(value)
        return any(needle in raw for needle in needles)
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="ignore")
        return any(needle in raw for needle in needles)
    if isinstance(value, os.PathLike):
        try:
            return _contains(os.fspath(value), needles, seen, depth=depth + 1)
        except (TypeError, ValueError):
            return False
    if isinstance(value, types.TracebackType):
        frame = value
        while frame is not None:
            filename = frame.tb_frame.f_code.co_filename
            is_harness = "/tests/" in filename and frame.tb_frame.f_code.co_name.startswith("test_")
            if not is_harness and _contains(
                frame.tb_frame,
                needles,
                seen,
                depth=depth + 1,
            ):
                return True
            frame = frame.tb_next
        return False
    if isinstance(value, types.FrameType):
        return _contains(value.f_locals, needles, seen, depth=depth + 1)
    if isinstance(value, BaseException):
        related: list[object] = [
            value.__traceback__,
            value.__cause__,
            value.__context__,
            value.args,
            getattr(value, "__dict__", None),
        ]
        notes = getattr(value, "__notes__", None)
        if notes is not None:
            related.append(notes)
        if isinstance(value, BaseExceptionGroup):
            related.append(value.exceptions)
        return any(_contains(candidate, needles, seen, depth=depth + 1) for candidate in related)
    if isinstance(value, dict):
        return any(
            _contains(candidate, needles, seen, depth=depth + 1)
            for pair in value.items()
            for candidate in pair
        )
    if isinstance(value, (deque, list, tuple, set, frozenset)):
        return any(_contains(candidate, needles, seen, depth=depth + 1) for candidate in value)
    if isinstance(value, Queue):
        return _contains(value.queue, needles, seen, depth=depth + 1)
    if isinstance(
        value,
        (
            types.BuiltinFunctionType,
            types.BuiltinMethodType,
            types.FunctionType,
            types.MethodType,
            types.ModuleType,
            type,
        ),
    ):
        return False
    namespace = getattr(value, "__dict__", None)
    candidates: list[object] = [] if namespace is None else [namespace]
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    for slot in slots:
        if isinstance(slot, str):
            try:
                candidates.append(object.__getattribute__(value, slot))
            except BaseException:
                pass
    return any(_contains(candidate, needles, seen, depth=depth + 1) for candidate in candidates)


__all__ = ["traceback_contains"]

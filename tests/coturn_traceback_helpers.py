"""Small hostile traceback-local scanner for Coturn contract tests."""

from __future__ import annotations

import os
from collections import deque
from queue import Queue

_MAX_DEPTH = 16
_MAX_OBJECTS = 4_096


def traceback_contains(exc: BaseException, *secrets: str | bytes) -> bool:
    needles = tuple(
        value if isinstance(value, bytes) else value.encode("utf-8") for value in secrets
    )
    frame = exc.__traceback__
    while frame is not None:
        filename = frame.tb_frame.f_code.co_filename
        is_test_harness = "/tests/" in filename and frame.tb_frame.f_code.co_name.startswith(
            "test_"
        )
        if not is_test_harness:
            for value in tuple(frame.tb_frame.f_locals.values()):
                if _contains(value, needles, set(), depth=0):
                    return True
        frame = frame.tb_next
    return False


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
    if isinstance(value, bytearray):
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
    if type(value).__module__.startswith("scripts.voice_pipecat_e2e_coturn"):
        attributes: list[object] = []
        namespace = getattr(value, "__dict__", None)
        if namespace is not None:
            attributes.append(namespace)
        slots = getattr(type(value), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        attributes.extend(
            getattr(value, slot) for slot in slots if isinstance(slot, str) and hasattr(value, slot)
        )
        return any(_contains(candidate, needles, seen, depth=depth + 1) for candidate in attributes)
    return False


__all__ = ["traceback_contains"]

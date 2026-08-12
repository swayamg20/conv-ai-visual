"""Small helpers for recursively immutable JSON contract values."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import JsonValue


def freeze_json(value: JsonValue) -> object:
    """Copy JSON into mappings/tuples that callers cannot mutate in place."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(child) for key, child in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze_json(child) for child in value)
    return value


def thaw_json(value: object) -> JsonValue:
    """Copy a frozen JSON value back to ordinary serializer-friendly JSON."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_json(child) for child in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported frozen JSON value: {type(value).__name__}")

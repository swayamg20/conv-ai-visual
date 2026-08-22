"""One-shot call authority rooted on the original preowned executor graph."""

from __future__ import annotations

import threading
import weakref

_AUTHORITY_TOKEN = object()
_FAILURE = "Relay Linux executor inner authority is invalid"


def _new_anchor_type() -> type:
    retained: weakref.WeakKeyDictionary[object, tuple[object, ...] | None] = (
        weakref.WeakKeyDictionary()
    )
    locks: weakref.WeakKeyDictionary[object, threading.RLock] = weakref.WeakKeyDictionary()

    class _InnerAuthorityAnchor:
        __slots__ = ("__weakref__", "_authentic")

        def __init__(self, token: object) -> None:
            if token is not _AUTHORITY_TOKEN:
                raise TypeError(_FAILURE)
            object.__setattr__(self, "_authentic", _AUTHORITY_TOKEN)
            retained[self] = None
            locks[self] = threading.RLock()

        def _bind(self, values: object) -> tuple[object, ...] | None:
            if type(values) is not tuple:
                return None
            lock = locks.get(self)
            if lock is None:
                return None
            with lock:
                current = retained.get(self)
                if current is None:
                    retained[self] = values
                    current = retained.get(self)
                return current if current is values else None

        def _matches(self, values: object) -> bool:
            lock = locks.get(self)
            if lock is None:
                return False
            with lock:
                current = retained.get(self)
                return bool(
                    self._authentic is _AUTHORITY_TOKEN
                    and type(current) is tuple
                    and current is values
                )

        def _is_authentic(self) -> bool:
            return bool(self._authentic is _AUTHORITY_TOKEN and self in retained and self in locks)

        def __setattr__(self, _name: str, _value: object) -> None:
            raise AttributeError("Relay Linux executor inner authority is immutable")

    return _InnerAuthorityAnchor


_RelayLinuxExecutorInnerAuthorityAnchor = _new_anchor_type()


def _new_executor_inner_authority_anchor() -> _RelayLinuxExecutorInnerAuthorityAnchor:
    return _RelayLinuxExecutorInnerAuthorityAnchor(_AUTHORITY_TOKEN)


def _new_original_anchor_registry() -> tuple[object, object]:
    records: weakref.WeakKeyDictionary[
        object,
        tuple[
            weakref.ReferenceType[object],
            weakref.ReferenceType[object],
            weakref.ReferenceType[object],
        ],
    ] = weakref.WeakKeyDictionary()
    lock = threading.RLock()

    def register(key: object, owner: object, destination: object, anchor: object) -> bool:
        if (
            type(anchor) is not _RelayLinuxExecutorInnerAuthorityAnchor
            or not anchor._is_authentic()
        ):
            return False
        try:
            candidate = (weakref.ref(owner), weakref.ref(destination), weakref.ref(anchor))
        except TypeError:
            return False
        with lock:
            record = records.get(key)
            if record is None:
                records[key] = candidate
                record = records.get(key)
            return bool(
                type(record) is tuple
                and len(record) == 3
                and record[0]() is owner
                and record[1]() is destination
                and record[2]() is anchor
            )

    def resolve(
        key: object,
        owner: object,
        destination: object,
    ) -> _RelayLinuxExecutorInnerAuthorityAnchor | None:
        with lock:
            record = records.get(key)
            if not (
                type(record) is tuple
                and len(record) == 3
                and record[0]() is owner
                and record[1]() is destination
                and type(record[2]()) is _RelayLinuxExecutorInnerAuthorityAnchor
            ):
                return None
            anchor = record[2]()
            return anchor if anchor is not None and anchor._is_authentic() else None

    return register, resolve


(
    _register_original_executor_inner_anchor,
    _original_executor_inner_anchor,
) = _new_original_anchor_registry()


def _executor_inner_authority_anchor(
    key: object,
) -> _RelayLinuxExecutorInnerAuthorityAnchor | None:
    from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
        _AUTHORITY_KEYS,
        _DESTINATION_KEYS,
        _LOCK,
        _OWNER_KEYS,
        _executor_value_matches,
        _RelayLinuxExecutorKey,
    )

    if type(key) is not _RelayLinuxExecutorKey:
        return None
    with _LOCK:
        owners = [owner for owner, candidate in _OWNER_KEYS.items() if candidate is key]
        destinations = [
            destination for destination, candidate in _DESTINATION_KEYS.items() if candidate is key
        ]
        authorities = [
            authority for authority, candidate in _AUTHORITY_KEYS.items() if candidate is key
        ]
        if len(owners) != 1 or len(destinations) != 1 or len(authorities) != 1:
            return None
        owner, destination = owners[0], destinations[0]
        original = _original_executor_inner_anchor(key, owner, destination)
        if (
            authorities[0] is not owner._cleanup_authority
            or not _executor_value_matches(owner, destination)
            or original is None
            or owner._inner_authority_anchor is not original
            or destination._inner_authority_anchor is not original
        ):
            return None
        return original


__all__: list[str] = []

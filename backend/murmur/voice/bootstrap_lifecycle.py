"""Bounded process-local coordination primitives for Voice V2 bootstrap."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class CallLockRegistry:
    """Reference-counted per-call locks that do not retain completed IDs."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[str, _LockEntry] = {}

    @asynccontextmanager
    async def hold(
        self,
        key: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.setdefault(key, _LockEntry(lock=asyncio.Lock()))
            entry.users += 1

        acquired = False
        try:
            if timeout_seconds is None:
                await entry.lock.acquire()
            else:
                await asyncio.wait_for(entry.lock.acquire(), timeout=timeout_seconds)
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(key) is entry:
                    self._entries.pop(key, None)

    @property
    def active_key_count(self) -> int:
        return len(self._entries)

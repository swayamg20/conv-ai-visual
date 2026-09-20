"""Process-local admission ownership for bounded streaming work."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


class StreamAdmissionError(RuntimeError):
    """Expected rejection before a bounded streaming operation can start."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StreamAdmissionMessages:
    """User-safe messages for one admission domain."""

    rate_limited: str
    user_busy: str
    capacity_reached: str


class StreamAdmissionLease:
    """Idempotent ownership token for one admitted stream."""

    def __init__(self, owner: StreamAdmission, user_id: str) -> None:
        self._owner = owner
        self._user_id = user_id
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            await self._owner._release(self._user_id)
            self._closed = True


class StreamAdmission:
    """Reject excess concurrent or per-user rolling-minute streams without queueing."""

    def __init__(
        self,
        *,
        global_limit: int,
        per_user_limit: int,
        requests_per_minute: int,
        messages: StreamAdmissionMessages,
        global_requests_per_minute: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("global_limit", global_limit),
            ("per_user_limit", per_user_limit),
            ("requests_per_minute", requests_per_minute),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if per_user_limit > global_limit:
            raise ValueError("per_user_limit must not exceed global_limit")
        if global_requests_per_minute is not None and (
            isinstance(global_requests_per_minute, bool)
            or not isinstance(global_requests_per_minute, int)
            or global_requests_per_minute <= 0
        ):
            raise ValueError("global_requests_per_minute must be a positive integer")
        if not isinstance(messages, StreamAdmissionMessages):
            raise TypeError("messages must be StreamAdmissionMessages")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._global_limit = global_limit
        self._per_user_limit = per_user_limit
        self._requests_per_minute = requests_per_minute
        self._global_requests_per_minute = global_requests_per_minute
        self._messages = messages
        self._clock = clock
        self._lock = asyncio.Lock()
        self._active_total = 0
        self._active_by_user: dict[str, int] = defaultdict(int)
        self._starts_by_user: dict[str, deque[float]] = defaultdict(deque)
        self._start_expirations: deque[tuple[float, str]] = deque()
        self._global_starts: deque[float] = deque()

    def _prune_expired_starts(self, now: float) -> None:
        while self._global_starts and now - self._global_starts[0] >= 60.0:
            self._global_starts.popleft()
        while self._start_expirations and now - self._start_expirations[0][0] >= 60.0:
            started_at, user_id = self._start_expirations.popleft()
            starts = self._starts_by_user.get(user_id)
            if starts and starts[0] == started_at:
                starts.popleft()
            if not starts:
                self._starts_by_user.pop(user_id, None)

    async def acquire(self, user_id: str) -> StreamAdmissionLease:
        if not isinstance(user_id, str) or not user_id:
            raise TypeError("user_id must be a non-empty string")

        async with self._lock:
            now = self._clock()
            self._prune_expired_starts(now)
            if (
                self._global_requests_per_minute is not None
                and len(self._global_starts) >= self._global_requests_per_minute
            ):
                raise StreamAdmissionError("rate_limited", self._messages.rate_limited)
            starts = self._starts_by_user.get(user_id)
            if starts is not None and len(starts) >= self._requests_per_minute:
                raise StreamAdmissionError("rate_limited", self._messages.rate_limited)
            if self._active_by_user.get(user_id, 0) >= self._per_user_limit:
                raise StreamAdmissionError("user_busy", self._messages.user_busy)
            if self._active_total >= self._global_limit:
                raise StreamAdmissionError("capacity_reached", self._messages.capacity_reached)

            if starts is None:
                starts = deque()
                self._starts_by_user[user_id] = starts
            starts.append(now)
            self._start_expirations.append((now, user_id))
            if self._global_requests_per_minute is not None:
                self._global_starts.append(now)
            self._active_total += 1
            self._active_by_user[user_id] += 1
            return StreamAdmissionLease(self, user_id)

    async def _release(self, user_id: str) -> None:
        async with self._lock:
            active = self._active_by_user.get(user_id, 0)
            if active <= 0:
                return
            if active == 1:
                self._active_by_user.pop(user_id, None)
            else:
                self._active_by_user[user_id] = active - 1
            self._active_total -= 1


__all__ = [
    "StreamAdmission",
    "StreamAdmissionError",
    "StreamAdmissionLease",
    "StreamAdmissionMessages",
]

"""Process-local admission limits for paid live-scene generations."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SceneAdmissionError(RuntimeError):
    """Expected rejection before a paid provider request can start."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SceneAdmissionLease:
    """Idempotent ownership token for one admitted generation."""

    def __init__(self, owner: SceneAuthoringAdmission, user_id: str) -> None:
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


class SceneAuthoringAdmission:
    """Reject excess concurrent or per-minute requests without queueing cost."""

    def __init__(
        self,
        *,
        global_limit: int = 4,
        per_user_limit: int = 1,
        requests_per_minute: int = 10,
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
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._global_limit = global_limit
        self._per_user_limit = per_user_limit
        self._requests_per_minute = requests_per_minute
        self._clock = clock
        self._lock = asyncio.Lock()
        self._active_total = 0
        self._active_by_user: dict[str, int] = defaultdict(int)
        self._starts_by_user: dict[str, deque[float]] = defaultdict(deque)
        self._start_expirations: deque[tuple[float, str]] = deque()

    def _prune_expired_starts(self, now: float) -> None:
        while self._start_expirations and now - self._start_expirations[0][0] >= 60.0:
            started_at, user_id = self._start_expirations.popleft()
            starts = self._starts_by_user.get(user_id)
            if starts and starts[0] == started_at:
                starts.popleft()
            if not starts:
                self._starts_by_user.pop(user_id, None)

    async def acquire(self, user_id: str) -> SceneAdmissionLease:
        if not isinstance(user_id, str) or not user_id:
            raise TypeError("user_id must be a non-empty string")

        async with self._lock:
            now = self._clock()
            self._prune_expired_starts(now)
            starts = self._starts_by_user.get(user_id)
            if starts is not None and len(starts) >= self._requests_per_minute:
                raise SceneAdmissionError(
                    "rate_limited",
                    "Too many visual generations. Please wait before trying again.",
                )
            if self._active_by_user.get(user_id, 0) >= self._per_user_limit:
                raise SceneAdmissionError(
                    "user_busy",
                    "A visual generation is already active for this account.",
                )
            if self._active_total >= self._global_limit:
                raise SceneAdmissionError(
                    "capacity_reached",
                    "Visual generation is busy. Please try again shortly.",
                )

            if starts is None:
                starts = deque()
                self._starts_by_user[user_id] = starts
            starts.append(now)
            self._start_expirations.append((now, user_id))
            self._active_total += 1
            self._active_by_user[user_id] += 1
            return SceneAdmissionLease(self, user_id)

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


class SceneProviderDispatchAdmission:
    """Process-local global ceiling checked immediately before each provider call."""

    def __init__(
        self,
        *,
        requests_per_minute: int = 10,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(requests_per_minute, bool)
            or not isinstance(requests_per_minute, int)
            or requests_per_minute <= 0
        ):
            raise ValueError("requests_per_minute must be a positive integer")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._requests_per_minute = requests_per_minute
        self._clock = clock
        self._lock = asyncio.Lock()
        self._dispatches: deque[float] = deque()

    async def acquire(self) -> None:
        """Reserve one dispatch or reject without queueing hidden provider work."""

        async with self._lock:
            now = self._clock()
            while self._dispatches and now - self._dispatches[0] >= 60.0:
                self._dispatches.popleft()
            if len(self._dispatches) >= self._requests_per_minute:
                raise SceneAdmissionError(
                    "provider_rate_limited",
                    "Visual model capacity is busy. Please try again shortly.",
                )
            self._dispatches.append(now)


__all__ = [
    "SceneAdmissionError",
    "SceneAdmissionLease",
    "SceneAuthoringAdmission",
    "SceneProviderDispatchAdmission",
]

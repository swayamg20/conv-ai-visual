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

    async def acquire(self, user_id: str) -> SceneAdmissionLease:
        if not isinstance(user_id, str) or not user_id:
            raise TypeError("user_id must be a non-empty string")

        async with self._lock:
            now = self._clock()
            starts = self._starts_by_user[user_id]
            while starts and now - starts[0] >= 60.0:
                starts.popleft()
            if len(starts) >= self._requests_per_minute:
                raise SceneAdmissionError(
                    "rate_limited",
                    "Too many visual generations. Please wait before trying again.",
                )
            if self._active_by_user[user_id] >= self._per_user_limit:
                raise SceneAdmissionError(
                    "user_busy",
                    "A visual generation is already active for this account.",
                )
            if self._active_total >= self._global_limit:
                raise SceneAdmissionError(
                    "capacity_reached",
                    "Visual generation is busy. Please try again shortly.",
                )

            starts.append(now)
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


__all__ = [
    "SceneAdmissionError",
    "SceneAdmissionLease",
    "SceneAuthoringAdmission",
]

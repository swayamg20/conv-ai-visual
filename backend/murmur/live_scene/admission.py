"""Process-local admission limits for paid live-scene generations."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable

from murmur.core.admission import (
    StreamAdmission,
    StreamAdmissionError,
    StreamAdmissionLease,
    StreamAdmissionMessages,
)

SceneAdmissionError = StreamAdmissionError
SceneAdmissionLease = StreamAdmissionLease


class SceneAuthoringAdmission(StreamAdmission):
    """Reject excess concurrent or per-minute requests without queueing cost."""

    def __init__(
        self,
        *,
        global_limit: int = 4,
        per_user_limit: int = 1,
        requests_per_minute: int = 10,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            global_limit=global_limit,
            per_user_limit=per_user_limit,
            requests_per_minute=requests_per_minute,
            messages=StreamAdmissionMessages(
                rate_limited="Too many visual generations. Please wait before trying again.",
                user_busy="A visual generation is already active for this account.",
                capacity_reached="Visual generation is busy. Please try again shortly.",
            ),
            clock=clock,
        )


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

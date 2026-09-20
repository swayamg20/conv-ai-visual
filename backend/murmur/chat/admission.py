"""Process-local admission limits for paid text-chat streams."""

from __future__ import annotations

import time
from collections.abc import Callable

from murmur.core.admission import (
    StreamAdmission,
    StreamAdmissionError,
    StreamAdmissionLease,
    StreamAdmissionMessages,
)

ChatAdmissionError = StreamAdmissionError
ChatAdmissionLease = StreamAdmissionLease


class ChatAdmission(StreamAdmission):
    """Bound concurrent and rolling-minute chat streams without queueing cost."""

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
            global_requests_per_minute=requests_per_minute,
            messages=StreamAdmissionMessages(
                rate_limited="Too many chat requests. Please wait before trying again.",
                user_busy="A chat response is already active for this account.",
                capacity_reached="Chat capacity is busy. Please try again shortly.",
            ),
            clock=clock,
        )


__all__ = ["ChatAdmission", "ChatAdmissionError", "ChatAdmissionLease"]

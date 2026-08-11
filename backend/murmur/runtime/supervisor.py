"""Periodic cleanup for process-local chat and voice sessions."""

from __future__ import annotations

import asyncio
import logging
import time

from murmur.chat import ChatService
from murmur.voice import VoiceService

logger = logging.getLogger(__name__)

DEFAULT_IDLE_EVICTION_SECONDS = 2 * 60 * 60
DEFAULT_SWEEP_INTERVAL_SECONDS = 5 * 60


class SessionSupervisor:
    """Coordinate idle eviction without owning service implementation details."""

    def __init__(
        self,
        chat_service: ChatService,
        voice_service: VoiceService,
        *,
        idle_after_seconds: float = DEFAULT_IDLE_EVICTION_SECONDS,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self.chat_service = chat_service
        self.voice_service = voice_service
        self.idle_after_seconds = idle_after_seconds
        self.interval_seconds = interval_seconds

    async def sweep_once(self, *, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else now
        await self.chat_service.evict_idle(
            idle_after_seconds=self.idle_after_seconds,
            now=current_time,
        )
        await self.voice_service.evict_idle(
            idle_after_seconds=self.idle_after_seconds,
            now=current_time,
        )

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                await self.sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Session supervisor failed: %s", exc)

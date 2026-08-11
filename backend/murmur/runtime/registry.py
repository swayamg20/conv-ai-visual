"""Typed ownership of process-local chat and voice runtime state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiortc import RTCDataChannel, RTCPeerConnection

    from funcs.llm_pipeline import LLMPipeline
    from funcs.smart_turn import SmartTurnSession

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatRuntimeSession:
    """All process-local state for one text-chat pipeline."""

    pipeline: LLMPipeline
    user_id: str
    agent_id: str | None = None
    last_activity: float = field(default_factory=time.monotonic)
    finalizing: bool = False
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(slots=True)
class RuntimeRegistry:
    """Own all process-local session state and provide idempotent teardown."""

    chat_sessions: dict[str, ChatRuntimeSession] = field(default_factory=dict)

    voice_sessions: dict[str, LLMPipeline] = field(default_factory=dict)
    voice_session_activity: dict[str, float] = field(default_factory=dict)
    voice_session_finalizing: set[str] = field(default_factory=set)
    peer_connections: set[RTCPeerConnection] = field(default_factory=set)
    datachannels: dict[str, RTCDataChannel] = field(default_factory=dict)
    peer_user_ids: dict[str, str] = field(default_factory=dict)
    peer_agent_ids: dict[str, str] = field(default_factory=dict)
    peer_session_ids: dict[str, str] = field(default_factory=dict)
    peer_canvas_modes: dict[str, bool] = field(default_factory=dict)
    tts_interrupt_flags: dict[str, bool] = field(default_factory=dict)
    pending_sdl: dict[str, dict[str, Any]] = field(default_factory=dict)
    smart_turn_sessions: dict[str, SmartTurnSession] = field(default_factory=dict)
    turn_processing_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    turn_timing: dict[str, dict[str, Any]] = field(default_factory=dict)

    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    sweeper_task: asyncio.Task[Any] | None = None

    def touch_chat(self, session_id: str) -> None:
        session = self.chat_sessions.get(session_id)
        if session:
            session.last_activity = time.monotonic()

    def register_chat(
        self,
        session_id: str,
        pipeline: LLMPipeline,
        *,
        user_id: str,
        agent_id: str | None,
    ) -> ChatRuntimeSession:
        session = ChatRuntimeSession(
            pipeline=pipeline,
            user_id=user_id,
            agent_id=agent_id,
        )
        self.chat_sessions[session_id] = session
        return session

    def get_chat(self, session_id: str | None) -> ChatRuntimeSession | None:
        return self.chat_sessions.get(session_id) if session_id else None

    def pop_chat(self, session_id: str) -> ChatRuntimeSession | None:
        return self.chat_sessions.pop(session_id, None)

    def touch_voice(self, peer_id: str) -> None:
        self.voice_session_activity[peer_id] = time.monotonic()

    def clear(self) -> None:
        """Clear references after work has been cancelled and peers have been closed."""
        self.chat_sessions.clear()
        self.voice_sessions.clear()
        self.voice_session_activity.clear()
        self.voice_session_finalizing.clear()
        self.peer_connections.clear()
        self.datachannels.clear()
        self.peer_user_ids.clear()
        self.peer_agent_ids.clear()
        self.peer_session_ids.clear()
        self.peer_canvas_modes.clear()
        self.tts_interrupt_flags.clear()
        self.pending_sdl.clear()
        self.smart_turn_sessions.clear()
        self.turn_processing_tasks.clear()
        self.turn_timing.clear()
        self.background_tasks.clear()

    async def shutdown(self) -> None:
        """Cancel owned tasks, clean analyzers, close peers, and clear all state."""
        sweeper_task = self.sweeper_task
        self.sweeper_task = None
        await self._cancel_tasks({sweeper_task} if sweeper_task else set())

        owned_tasks = set(self.turn_processing_tasks.values()) | set(self.background_tasks)
        await self._cancel_tasks(owned_tasks)

        for smart_turn_session in list(self.smart_turn_sessions.values()):
            try:
                smart_turn_session.cleanup()
            except Exception as exc:
                logger.warning("Smart Turn cleanup failed during shutdown: %s", exc)

        peers = list(self.peer_connections)
        if peers:
            await asyncio.gather(*(peer.close() for peer in peers), return_exceptions=True)

        self.clear()

    @staticmethod
    async def _cancel_tasks(tasks: set[asyncio.Task[Any]]) -> None:
        current_task = asyncio.current_task()
        pending = [task for task in tasks if task is not current_task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

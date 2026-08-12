"""Typed ownership of process-local chat and voice runtime state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiortc import RTCDataChannel, RTCPeerConnection

    from murmur.llm.pipeline import LLMPipeline
    from murmur.voice.smart_turn import SmartTurnSession

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
class VoiceRuntimeSession:
    """All process-local state for one authenticated WebRTC peer."""

    peer_id: str
    peer: RTCPeerConnection
    user_id: str
    agent_id: str | None = None
    persistent_session_id: str | None = None
    canvas_mode: bool = False
    pipeline: LLMPipeline | None = None
    datachannel: RTCDataChannel | None = None
    tts_active: bool = False
    pending_sdl: dict[str, Any] | None = None
    smart_turn: SmartTurnSession | None = None
    audio_task: asyncio.Task[Any] | None = None
    turn_task: asyncio.Task[Any] | None = None
    turn_timing: dict[str, Any] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)
    finalizing: bool = False


@dataclass(slots=True)
class RuntimeRegistry:
    """Own all process-local session state and provide idempotent teardown."""

    chat_sessions: dict[str, ChatRuntimeSession] = field(default_factory=dict)

    voice_sessions: dict[str, VoiceRuntimeSession] = field(default_factory=dict)

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
        session = self.voice_sessions.get(peer_id)
        if session:
            session.last_activity = time.monotonic()

    def register_voice(
        self,
        peer_id: str,
        peer: RTCPeerConnection,
        *,
        user_id: str,
        agent_id: str | None,
        persistent_session_id: str | None,
        canvas_mode: bool,
    ) -> VoiceRuntimeSession:
        session = VoiceRuntimeSession(
            peer_id=peer_id,
            peer=peer,
            user_id=user_id,
            agent_id=agent_id,
            persistent_session_id=persistent_session_id,
            canvas_mode=canvas_mode,
        )
        self.voice_sessions[peer_id] = session
        return session

    def get_voice(self, peer_id: str) -> VoiceRuntimeSession | None:
        return self.voice_sessions.get(peer_id)

    def pop_voice(self, peer_id: str) -> VoiceRuntimeSession | None:
        return self.voice_sessions.pop(peer_id, None)

    def clear(self) -> None:
        """Clear references after work has been cancelled and peers have been closed."""
        self.chat_sessions.clear()
        self.voice_sessions.clear()
        self.background_tasks.clear()

    async def shutdown(self) -> None:
        """Cancel owned tasks, clean analyzers, close peers, and clear all state."""
        sweeper_task = self.sweeper_task
        self.sweeper_task = None
        await self._cancel_tasks({sweeper_task} if sweeper_task else set())

        voice_tasks = {
            task
            for session in self.voice_sessions.values()
            for task in (session.audio_task, session.turn_task)
            if task
        }
        owned_tasks = voice_tasks | set(self.background_tasks)
        await self._cancel_tasks(owned_tasks)

        for session in list(self.voice_sessions.values()):
            smart_turn_session = session.smart_turn
            if not smart_turn_session:
                continue
            try:
                smart_turn_session.cleanup()
            except Exception as exc:
                logger.warning("Smart Turn cleanup failed during shutdown: %s", exc)

        peers = [session.peer for session in self.voice_sessions.values()]
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

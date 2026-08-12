"""Single-owner lifecycle for the LiveKit AgentSession used by Voice V2."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Protocol

from livekit.agents import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions

from murmur.voice.profile import PreparedVoiceProfile
from murmur.voice.worker_contracts import VoiceSessionLifecycleError


class OwnedAgentSession(Protocol):
    async def start(
        self,
        agent: Agent,
        *,
        room: object,
        room_options: RoomOptions,
    ) -> object: ...

    def interrupt(self, *, force: bool = False) -> Awaitable[None]: ...

    def shutdown(self, *, drain: bool = True) -> None: ...

    async def aclose(self) -> None: ...


class AgentSessionFactory(Protocol):
    def __call__(self, prepared: PreparedVoiceProfile) -> tuple[OwnedAgentSession, Agent]: ...


class AgentSessionOwner:
    """Own exactly one LiveKit ``AgentSession`` and its bounded lifecycle."""

    def __init__(
        self,
        prepared: PreparedVoiceProfile,
        *,
        session_factory: AgentSessionFactory,
        cleanup_timeout_seconds: float,
        interruption_timeout_seconds: float,
    ) -> None:
        self._prepared = prepared
        # This is the sole construction point. No provider/profile object owns a
        # second AgentSession and repeated start calls cannot create another one.
        self._session, self._agent = session_factory(prepared)
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._interruption_timeout_seconds = interruption_timeout_seconds
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self, *, room: object, participant_identity: str) -> None:
        async with self._start_lock:
            if self._closed:
                raise VoiceSessionLifecycleError("voice session owner is already closed")
            if self._started:
                return
            await self._session.start(
                self._agent,
                room=room,
                room_options=RoomOptions(
                    participant_identity=participant_identity,
                    text_input=False,
                ),
            )
            self._started = True

    async def interrupt(self) -> None:
        if not self._started or self._closed:
            raise VoiceSessionLifecycleError("voice session is not running")
        try:
            await asyncio.wait_for(
                self._session.interrupt(force=True),
                timeout=self._interruption_timeout_seconds,
            )
        except TimeoutError as exc:
            self._session.shutdown(drain=False)
            raise VoiceSessionLifecycleError("voice interruption timed out") from exc

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._session.shutdown(drain=False)
            errors: list[Exception] = []

            async def cleanup() -> None:
                try:
                    await self._session.aclose()
                except Exception as exc:
                    errors.append(exc)
                if self._prepared.close_callback is not None:
                    try:
                        await self._prepared.close_callback()
                    except Exception as exc:
                        errors.append(exc)

            try:
                await asyncio.wait_for(cleanup(), timeout=self._cleanup_timeout_seconds)
            except TimeoutError as exc:
                raise VoiceSessionLifecycleError("voice session cleanup timed out") from exc
            if errors:
                raise VoiceSessionLifecycleError(
                    "voice session cleanup did not complete"
                ) from errors[0]


def livekit_session_factory(prepared: PreparedVoiceProfile) -> tuple[OwnedAgentSession, Agent]:
    """Construct one pinned LiveKit Agents 1.6.9 session from direct objects."""
    components = {"stt": prepared.stt, "llm": prepared.llm, "tts": prepared.tts}
    managed = sorted(name for name, component in components.items() if isinstance(component, str))
    if managed:
        raise VoiceSessionLifecycleError(
            "managed-inference model strings are forbidden for: " + ", ".join(managed)
        )
    session = AgentSession(
        stt=prepared.stt,  # type: ignore[arg-type]
        llm=prepared.llm,  # type: ignore[arg-type]
        tts=prepared.tts,  # type: ignore[arg-type]
        vad=prepared.vad,  # type: ignore[arg-type]
    )
    agent = Agent(instructions=prepared.instructions)
    return session, agent

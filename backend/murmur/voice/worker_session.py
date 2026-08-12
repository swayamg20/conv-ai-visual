"""Single-owner lifecycle for the LiveKit AgentSession used by Voice V2."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Protocol

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.agents.voice.room_io import AudioInputOptions, AudioOutputOptions, RoomOptions

from murmur.voice.profile import PreparedVoiceProfile
from murmur.voice.worker_contracts import VoiceSessionLifecycleError


class OwnedAgentSession(Protocol):
    @property
    def room_io(self) -> OwnedRoomIO: ...

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


class OwnedRoomIO(Protocol):
    async def wait_for_ready(self) -> None: ...


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

    @property
    def session(self) -> OwnedAgentSession:
        """Return the owned public session surface for lifecycle event binding."""
        return self._session

    async def start(self, *, room: object, participant_identity: str) -> None:
        async with self._start_lock:
            if self._closed:
                raise VoiceSessionLifecycleError("voice session owner is already closed")
            if self._started:
                return
            await self._session.start(
                self._agent,
                room=room,
                room_options=_room_options(self._prepared, participant_identity),
            )
            # AgentSession.start() creates RoomIO, but its public readiness future
            # is the proof that participant selection and audio output publication
            # have actually completed.
            await self._session.room_io.wait_for_ready()
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
    session_kwargs: dict[str, object] = {
        "stt": prepared.stt,
        "llm": prepared.llm,
        "tts": prepared.tts,
        "vad": prepared.vad,
    }
    policy = prepared.session_policy
    if policy is not None:
        session_kwargs.update(
            turn_handling={
                "turn_detection": policy.turn_detection,
                "endpointing": {
                    "mode": policy.endpointing_mode,
                    "min_delay": policy.min_endpointing_delay_seconds,
                    "max_delay": policy.max_endpointing_delay_seconds,
                },
                "interruption": {
                    "enabled": True,
                    "min_duration": 0.0,
                    "min_words": 0,
                    "resume_false_interruption": policy.resume_false_interruption,
                    "false_interruption_timeout": None,
                },
                "preemptive_generation": {"enabled": policy.preemptive_generation},
            },
            aec_warmup_duration=policy.aec_warmup_duration_seconds,
        )
    session = AgentSession(
        **session_kwargs,  # type: ignore[arg-type]
    )
    agent = Agent(instructions=prepared.instructions)
    return session, agent


def _room_options(prepared: PreparedVoiceProfile, participant_identity: str) -> RoomOptions:
    policy = prepared.media_policy
    if policy is None:
        # Preserve the foundation worker's production defaults when a profile
        # has not opted into an explicit deterministic media contract.
        return RoomOptions(
            participant_identity=participant_identity,
            text_input=False,
        )

    track_source = rtc.TrackSource.SOURCE_MICROPHONE
    return RoomOptions(
        participant_identity=participant_identity,
        text_input=policy.text_input,
        text_output=policy.text_output,
        audio_input=AudioInputOptions(
            sample_rate=policy.input_sample_rate,
            num_channels=policy.input_channels,
            frame_size_ms=policy.input_frame_size_ms,
            noise_cancellation=None,
            auto_gain_control=policy.input_auto_gain_control,
            pre_connect_audio=policy.input_preconnect,
        ),
        audio_output=AudioOutputOptions(
            sample_rate=policy.output_sample_rate,
            num_channels=policy.output_channels,
            track_publish_options=rtc.TrackPublishOptions(source=track_source),
            track_name=policy.output_track_name,
        ),
    )

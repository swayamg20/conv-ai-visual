"""WebRTC, speech, turn-detection, LLM, and TTS orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole

from funcs.config import config
from funcs.smart_turn import SmartTurnAnalyzer
from murmur.core import InvalidRequestError, PermissionDeniedError, ResourceNotFoundError
from murmur.llm.pipeline import LLMPipeline
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.runtime import RuntimeRegistry
from murmur.voice.models import VoiceOfferAnswer, VoiceOfferRequest
from murmur.voice.pipeline import VoicePipelineFactory
from murmur.voice.synthesis import SpeechSynthesizer
from murmur.voice.transcription import VoiceTranscriber
from murmur.voice.turns import cancel_turn, schedule_turn

logger = logging.getLogger(__name__)

SESSION_IDLE_EVICTION_SECS = 2 * 60 * 60
SESSION_SUMMARY_MIN_MESSAGES = 4


class VoiceService:
    """Own the full lifetime of authenticated WebRTC voice sessions."""

    def __init__(
        self,
        runtime: RuntimeRegistry,
        *,
        peer_connection_factory: Callable[[], RTCPeerConnection] = RTCPeerConnection,
        media_blackhole_factory: Callable[[], MediaBlackhole] = MediaBlackhole,
    ) -> None:
        self.runtime = runtime
        self.peer_connection_factory = peer_connection_factory
        self.media_blackhole_factory = media_blackhole_factory
        self.smart_turn_analyzer: SmartTurnAnalyzer | None = None
        self.started = False
        self.synthesizer = SpeechSynthesizer()
        self.pipeline_factory = VoicePipelineFactory(runtime)
        self.transcriber = VoiceTranscriber(
            runtime,
            analyzer_provider=lambda: _get_smart_turn_analyzer(self),
            confirmed_turn_handler=lambda peer_id, text: schedule_turn(
                self,
                peer_id,
                text,
            ),
        )

    def start(self) -> None:
        """Initialize optional provider clients at application startup."""
        if self.started:
            return
        self.started = True
        try:
            config.validate()
            self.synthesizer.start()

            if config.SMART_TURN_ENABLED:
                logger.info("Smart Turn enabled; analyzer will initialize on first voice session")
            else:
                logger.info("Smart Turn disabled, using Deepgram endpointing only")
        except Exception as exc:
            logger.error("Failed to initialize voice pipelines: %s", exc)
            self.synthesizer.reset()
            self.smart_turn_analyzer = None

    async def negotiate(
        self,
        user_id: str,
        request: VoiceOfferRequest,
    ) -> VoiceOfferAnswer:
        return await _negotiate_offer(self, user_id, request)

    async def finalize(
        self,
        peer_id: str,
        *,
        min_messages: int = SESSION_SUMMARY_MIN_MESSAGES,
        background: bool = True,
        peer: RTCPeerConnection | None = None,
    ) -> str | None:
        return await _finalize_voice_session(
            self,
            peer_id,
            min_messages=min_messages,
            background=background,
            pc=peer,
        )

    async def evict_idle(
        self,
        *,
        idle_after_seconds: float = SESSION_IDLE_EVICTION_SECS,
        now: float | None = None,
    ) -> None:
        await _evict_idle_voice_sessions(
            self,
            idle_after_seconds=idle_after_seconds,
            now=now,
        )


def _get_smart_turn_analyzer(service: VoiceService) -> SmartTurnAnalyzer | None:
    """Lazily initialize Smart Turn so importing `main` stays lightweight."""
    if not config.SMART_TURN_ENABLED:
        return None
    if service.smart_turn_analyzer is not None:
        return service.smart_turn_analyzer
    try:
        service.smart_turn_analyzer = SmartTurnAnalyzer.get_instance(
            model_path=config.SMART_TURN_MODEL_PATH,
            threshold=config.SMART_TURN_THRESHOLD,
            stop_secs=config.SMART_TURN_STOP_SECS,
        )
        logger.info(
            "Smart Turn analyzer initialized lazily (threshold=%.2f, stop_secs=%.1f)",
            config.SMART_TURN_THRESHOLD,
            config.SMART_TURN_STOP_SECS,
        )
    except Exception as e:
        logger.warning("Smart Turn init failed, falling back to Deepgram endpointing: %s", e)
        service.smart_turn_analyzer = None
    return service.smart_turn_analyzer


def _pipeline_message_count(pipeline: LLMPipeline | None) -> int:
    memory = getattr(pipeline, "memory", None)
    context = getattr(memory, "context", None)
    messages = getattr(context, "messages", None)
    if isinstance(messages, list):
        return len(messages)
    return 0


def _log_background_task(service: VoiceService, task: asyncio.Task, label: str) -> None:
    """Surface exceptions from fire-and-forget cleanup work."""
    service.runtime.background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        service.runtime.background_tasks.discard(t)
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("%s task inspection failed: %s", label, e)
            return

        if exc:
            logger.warning("%s failed: %s", label, exc)

    task.add_done_callback(_done)


async def _persist_pipeline_summary(
    pipeline: LLMPipeline,
    session_id: str,
    *,
    min_messages: int,
    persist_db_summary: bool,
) -> str | None:
    """Generate, save, and close a pipeline summary if the session was long enough."""
    persisted_session_id = getattr(pipeline, "session_id", session_id)
    if _pipeline_message_count(pipeline) < min_messages:
        try:
            pipeline.end_session(None)
        except Exception as e:
            logger.warning("[%s] Failed to clear pipeline without summary: %s", session_id, e)
        return None

    summary: str | None = None
    try:
        summary = await pipeline.generate_session_summary()
        summary = summary.strip() if summary else None
    except Exception as e:
        logger.warning("[%s] Failed to generate session summary: %s", session_id, e)

    try:
        pipeline.end_session(summary)
    except Exception as e:
        logger.warning("[%s] Failed to close pipeline after summary: %s", session_id, e)

    if summary and persist_db_summary:
        try:
            SessionRepo.update_summary(persisted_session_id, summary)
        except Exception as e:
            logger.warning(
                "[%s] Failed to persist session summary to DB for session=%s: %s",
                session_id,
                persisted_session_id,
                e,
            )

    return summary


async def _finalize_voice_session(
    service: VoiceService,
    pc_id: str,
    *,
    min_messages: int = SESSION_SUMMARY_MIN_MESSAGES,
    background: bool = True,
    pc: RTCPeerConnection | None = None,
) -> str | None:
    """Remove a voice peer session, cancel work, and optionally persist a summary."""
    voice_session = service.runtime.get_voice(pc_id)
    if voice_session is None or voice_session.finalizing:
        return None

    voice_session.finalizing = True
    try:
        await cancel_turn(service, pc_id)

        service.runtime.pop_voice(pc_id)
        pipeline = voice_session.pipeline
        voice_session.pipeline = None
        voice_session.datachannel = None
        voice_session.pending_sdl = None
        voice_session.turn_timing.clear()

        if voice_session.smart_turn:
            voice_session.smart_turn.cleanup()
            voice_session.smart_turn = None

        peer = pc or voice_session.peer
        if peer.connectionState not in ("closed", "failed"):
            try:
                await peer.close()
            except Exception as e:
                logger.warning("[%s] Failed to close peer connection: %s", pc_id, e)

        if not pipeline:
            return None

        persist_voice_summary = bool(
            getattr(pipeline, "agent_id", None) and getattr(pipeline, "session_id", pc_id) != pc_id
        )

        if background:
            task = asyncio.create_task(
                _persist_pipeline_summary(
                    pipeline,
                    pc_id,
                    min_messages=min_messages,
                    persist_db_summary=persist_voice_summary,
                )
            )
            _log_background_task(service, task, f"voice session finalizer [{pc_id}]")
            return None

        return await _persist_pipeline_summary(
            pipeline,
            pc_id,
            min_messages=min_messages,
            persist_db_summary=persist_voice_summary,
        )
    finally:
        voice_session.finalizing = False


async def _evict_idle_voice_sessions(
    service: VoiceService,
    *,
    idle_after_seconds: float,
    now: float | None,
) -> None:
    """Remove inactive voice sessions so peer state cannot accumulate forever."""
    current_time = time.monotonic() if now is None else now
    for pc_id, voice_session in list(service.runtime.voice_sessions.items()):
        if current_time - voice_session.last_activity < idle_after_seconds:
            continue
        logger.info("[%s] Evicting idle voice session", pc_id)
        await _finalize_voice_session(
            service,
            pc_id,
            min_messages=SESSION_SUMMARY_MIN_MESSAGES,
            background=True,
        )


async def _negotiate_offer(
    service: VoiceService,
    user_id: str,
    body: VoiceOfferRequest,
) -> VoiceOfferAnswer:
    """Validate ownership before allocating and wiring a WebRTC peer."""
    remote_offer = RTCSessionDescription(sdp=body.sdp, type=body.type)
    persistent_session_id = body.session_id
    agent_id = body.agent_id

    if persistent_session_id:
        existing_session = SessionRepo.get_by_id(persistent_session_id)
        if not existing_session:
            raise ResourceNotFoundError("Session not found")
        if existing_session.user_id != user_id:
            raise PermissionDeniedError("Forbidden")
        if agent_id and agent_id != existing_session.agent_id:
            raise InvalidRequestError("Session does not belong to the supplied agent")
        agent_id = existing_session.agent_id

    if agent_id:
        agent = AgentRepo.get_by_id(agent_id)
        if not agent:
            raise ResourceNotFoundError("Agent not found")
        if agent.user_id != user_id:
            raise PermissionDeniedError("Forbidden")

    if not persistent_session_id and agent_id:
        db_session = SessionRepo.create(user_id=user_id, agent_id=agent_id)
        persistent_session_id = db_session.id

    pc = service.peer_connection_factory()
    pc_id = f"pc-{id(pc)}"
    canvas_mode = body.canvas_mode
    voice_session = service.runtime.register_voice(
        pc_id,
        pc,
        user_id=user_id,
        agent_id=agent_id,
        persistent_session_id=persistent_session_id,
        canvas_mode=canvas_mode,
    )
    logger.info(
        "[%s] User ID: %s, Canvas Mode: %s, Agent: %s, Session: %s",
        pc_id,
        user_id,
        canvas_mode,
        agent_id or "none",
        persistent_session_id or "none",
    )

    @pc.on("datachannel")
    def on_datachannel(channel):
        voice_session.datachannel = channel

        @channel.on("message")
        def on_message(message):
            try:
                data = json.loads(message)
                # Handle stop_tts command from client
                if data.get("type") == "stop_tts":
                    logger.warning("[%s] Client requested TTS stop", pc_id)
                    voice_session.tts_active = False
                    st = voice_session.smart_turn
                    if st:
                        st._reset_turn()
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug("[%s] Ignoring malformed data-channel message: %s", pc_id, exc)

        @channel.on("close")
        async def on_close():
            logger.info("[%s] DataChannel closed", pc_id)
            await _finalize_voice_session(service, pc_id, background=True, pc=pc)

    logger.info("[%s] created for incoming offer", pc_id)

    media_blackhole = service.media_blackhole_factory()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("[%s] Connection: %s", pc_id, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await _finalize_voice_session(service, pc_id, background=True, pc=pc)
            logger.info("[%s] Closed", pc_id)

    @pc.on("track")
    def on_track(track):
        logger.info(
            "[%s] Track received: kind=%s id=%s", pc_id, track.kind, getattr(track, "id", "?")
        )
        if track.kind == "audio":
            task = asyncio.create_task(service.transcriber.consume(track, pc_id))

            @track.on("ended")
            def on_ended():
                logger.info("[%s] Track ended", pc_id)
                task.cancel()

        else:
            pc.addTrack(track)
            blackhole_task = asyncio.create_task(media_blackhole.start())
            _log_background_task(service, blackhole_task, f"media blackhole [{pc_id}]")

    try:
        await pc.setRemoteDescription(remote_offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
    except Exception:
        await _finalize_voice_session(service, pc_id, background=False, pc=pc)
        raise

    logger.info("[%s] Answer created", pc_id)
    return VoiceOfferAnswer(
        sdp=pc.localDescription.sdp,
        type=pc.localDescription.type,
        session_id=persistent_session_id,
        agent_id=agent_id,
    )

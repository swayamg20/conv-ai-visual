import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

import numpy as np
import uvicorn
import websockets
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole
from aiortc.mediastreams import AudioFrame
from fastapi import Request
from fastapi.responses import JSONResponse
from murmur.agents.runtime import (
    build_agent_runtime_config,
    register_agent_resource_tool,
)
from murmur.api import create_application
from murmur.api.schemas import Offer
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.observability import (
    TTSResilienceLogRepo,
    VoicePipelineLogRepo,
)
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.runtime import RuntimeRegistry

from funcs.auth import require_auth
from funcs.config import config
from funcs.kokoro_tts import KokoroTTSPipeline
from funcs.llm_pipeline import LLMPipeline
from funcs.model_router import route_model
from funcs.smart_turn import SmartTurnAnalyzer, SmartTurnSession
from funcs.tts_pipeline import TTSPipeline, is_retryable_tts_error

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-deepgram")
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

runtime = RuntimeRegistry()
app = create_application(runtime=runtime, session_sweeper=lambda: _session_sweeper_loop())

try:
    config.validate()

    if config.TTS_PROVIDER == "kokoro":
        tts_pipeline = KokoroTTSPipeline(model_path=config.KOKORO_MODEL_PATH)
        logger.info("Kokoro TTS pipeline initialized (local ONNX)")
    else:
        tts_pipeline = TTSPipeline(
            api_key=config.ELEVENLABS_API_KEY,
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL_ID,
            stability=config.TTS_STABILITY,
            similarity_boost=config.TTS_SIMILARITY_BOOST,
            style=config.TTS_STYLE,
            use_speaker_boost=config.TTS_USE_SPEAKER_BOOST,
        )
        logger.info("ElevenLabs TTS pipeline initialized")
    tts_fallback_pipeline: KokoroTTSPipeline | None = None

    # Smart Turn analyzer (singleton, shared across sessions)
    smart_turn_analyzer: SmartTurnAnalyzer | None = None
    if config.SMART_TURN_ENABLED:
        logger.info("Smart Turn enabled; analyzer will initialize on first voice session")
    else:
        logger.info("Smart Turn disabled, using Deepgram endpointing only")

except Exception as e:
    logger.error("Failed to initialize pipelines: %s", e)
    tts_pipeline = None
    tts_fallback_pipeline = None
    smart_turn_analyzer = None


def _get_voice_user_id(pc_id: str) -> str:
    """Return the authenticated user ID for a voice peer connection."""
    user_id = runtime.peer_user_ids.get(pc_id)
    if not user_id:
        raise RuntimeError(f"Missing authenticated voice user for peer {pc_id}")
    return user_id


def _get_tts_provider_name(pipeline: Any) -> str | None:
    if pipeline is None:
        return None
    if isinstance(pipeline, KokoroTTSPipeline):
        return "kokoro"
    if isinstance(pipeline, TTSPipeline):
        return "elevenlabs"
    return pipeline.__class__.__name__.lower()


def _get_kokoro_fallback_pipeline() -> KokoroTTSPipeline | None:
    """Lazily initialize a Kokoro fallback pipeline when enabled."""
    global tts_fallback_pipeline
    if not config.TTS_FALLBACK_TO_KOKORO:
        return None
    if isinstance(tts_pipeline, KokoroTTSPipeline):
        return tts_pipeline
    if tts_fallback_pipeline is None:
        try:
            tts_fallback_pipeline = KokoroTTSPipeline(model_path=config.KOKORO_MODEL_PATH)
            logger.info("Kokoro fallback TTS pipeline initialized")
        except Exception as e:
            logger.warning("Failed to initialize Kokoro fallback pipeline: %s", e)
            tts_fallback_pipeline = None
    return tts_fallback_pipeline


def _get_smart_turn_analyzer() -> SmartTurnAnalyzer | None:
    """Lazily initialize Smart Turn so importing `main` stays lightweight."""
    global smart_turn_analyzer
    if not config.SMART_TURN_ENABLED:
        return None
    if smart_turn_analyzer is not None:
        return smart_turn_analyzer
    try:
        smart_turn_analyzer = SmartTurnAnalyzer.get_instance(
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
        smart_turn_analyzer = None
    return smart_turn_analyzer


SESSION_IDLE_EVICTION_SECS = 2 * 60 * 60
SESSION_SWEEP_INTERVAL_SECS = 5 * 60
SESSION_SUMMARY_MIN_MESSAGES = 4


def _touch_voice_session(pc_id: str) -> None:
    runtime.touch_voice(pc_id)


def _pipeline_message_count(pipeline: LLMPipeline | None) -> int:
    memory = getattr(pipeline, "memory", None)
    context = getattr(memory, "context", None)
    messages = getattr(context, "messages", None)
    if isinstance(messages, list):
        return len(messages)
    return 0


def _log_background_task(task: asyncio.Task, label: str) -> None:
    """Surface exceptions from fire-and-forget cleanup work."""
    runtime.background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        runtime.background_tasks.discard(t)
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
    pc_id: str,
    *,
    min_messages: int = SESSION_SUMMARY_MIN_MESSAGES,
    background: bool = True,
    pc: RTCPeerConnection | None = None,
) -> str | None:
    """Remove a voice peer session, cancel work, and optionally persist a summary."""
    if pc_id in runtime.voice_session_finalizing:
        return None

    runtime.voice_session_finalizing.add(pc_id)
    try:
        await _cancel_active_turn(pc_id)

        pipeline = runtime.voice_sessions.pop(pc_id, None)
        runtime.voice_session_activity.pop(pc_id, None)
        runtime.datachannels.pop(pc_id, None)
        runtime.peer_user_ids.pop(pc_id, None)
        runtime.peer_agent_ids.pop(pc_id, None)
        runtime.peer_session_ids.pop(pc_id, None)
        runtime.peer_canvas_modes.pop(pc_id, None)
        runtime.tts_interrupt_flags.pop(pc_id, None)
        runtime.pending_sdl.pop(pc_id, None)
        runtime.turn_timing.pop(pc_id, None)

        task = runtime.turn_processing_tasks.pop(pc_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        st = runtime.smart_turn_sessions.pop(pc_id, None)
        if st:
            st.cleanup()

        if pc is not None:
            if pc.connectionState not in ("closed", "failed"):
                try:
                    await pc.close()
                except Exception as e:
                    logger.warning("[%s] Failed to close peer connection: %s", pc_id, e)
            runtime.peer_connections.discard(pc)

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
            _log_background_task(task, f"voice session finalizer [{pc_id}]")
            return None

        return await _persist_pipeline_summary(
            pipeline,
            pc_id,
            min_messages=min_messages,
            persist_db_summary=persist_voice_summary,
        )
    finally:
        runtime.voice_session_finalizing.discard(pc_id)


async def _evict_idle_sessions() -> None:
    """Remove inactive in-memory sessions so they do not accumulate forever."""
    now = time.monotonic()
    await app.state.chat_service.evict_idle(
        idle_after_seconds=SESSION_IDLE_EVICTION_SECS,
        min_messages=SESSION_SUMMARY_MIN_MESSAGES,
        now=now,
    )

    for pc_id, last_seen in list(runtime.voice_session_activity.items()):
        if pc_id in runtime.voice_sessions and now - last_seen >= SESSION_IDLE_EVICTION_SECS:
            logger.info("[%s] Evicting idle voice session", pc_id)
            await _finalize_voice_session(
                pc_id,
                min_messages=SESSION_SUMMARY_MIN_MESSAGES,
                background=True,
            )


async def _session_sweeper_loop() -> None:
    """Periodic task that reaps idle in-memory sessions."""
    try:
        while True:
            await asyncio.sleep(SESSION_SWEEP_INTERVAL_SECS)
            await _evict_idle_sessions()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("Session sweeper failed: %s", e)


def audioframe_to_pcm16_bytes(frame: AudioFrame) -> bytes:
    """
    Convert aiortc.AudioFrame to interleaved 16-bit PCM bytes.
    Handles frames where to_ndarray() returns either (samples, channels) OR (channels, samples).
    """
    arr = frame.to_ndarray()

    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    samples = getattr(frame, "samples", None)
    if samples is not None:
        if arr.shape[0] != samples and arr.shape[1] == samples:
            arr = arr.T
    if np.issubdtype(arr.dtype, np.floating):
        arr = (arr * 32767).astype("int16")
    elif arr.dtype != np.int16:
        arr = arr.astype("int16")

    return arr.tobytes()


async def deepgram_stream_ws_send_and_recv(
    websocket_url: str,
    auth_key: str,
    audio_queue: "asyncio.Queue[bytes]",
    results_callback,
    on_connected=None,
):
    """Stream audio to Deepgram and receive transcripts"""
    headers = {"Authorization": f"Token {auth_key}"}
    logger.info("Connecting to Deepgram at %s", websocket_url)
    try:
        async with websockets.connect(
            websocket_url,
            additional_headers=headers,
            max_size=None,
        ) as ws:
            logger.info("Deepgram WS connected")
            # Notify that Deepgram is ready
            if on_connected:
                await on_connected()

            async def sender():
                try:
                    while True:
                        pcm_bytes = await audio_queue.get()
                        if pcm_bytes is None:
                            await ws.send(json.dumps({"type": "Finalize"}))
                            return
                        await ws.send(pcm_bytes)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("sender error: %s", e)
                    raise

            send_task = asyncio.create_task(sender())

            try:
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        await results_callback(data)
                    except json.JSONDecodeError:
                        pass
            except websockets.ConnectionClosed as e:
                logger.info("Deepgram WS closed: %s", e)
            finally:
                send_task.cancel()
                try:
                    await audio_queue.put(None)
                except Exception:
                    pass

    except Exception as e:
        logger.exception("Failed to connect or stream to Deepgram: %s", e)


async def _ensure_voice_session(pc_id: str) -> LLMPipeline:
    """Get or create the LLMPipeline for a voice peer connection."""
    if pc_id not in runtime.voice_sessions:
        user_id = _get_voice_user_id(pc_id)
        agent_id = runtime.peer_agent_ids.get(pc_id)
        persistent_session_id = runtime.peer_session_ids.get(pc_id)
        session_key = persistent_session_id or pc_id
        agent = None

        if agent_id:
            agent = AgentRepo.get_by_id(agent_id)
            if not agent:
                raise RuntimeError(f"Agent not found for voice session: {agent_id}")
            if agent.user_id != user_id:
                raise RuntimeError(
                    f"Forbidden voice  agent access for user={user_id} agent={agent_id}"
                )

        if agent:
            agent_config = build_agent_runtime_config(user_id, agent)
            voice_pipeline = LLMPipeline(
                provider=config.LLM_PROVIDER,
                api_key=None,
                model=None,
                system_prompt=agent_config.prompt,
                max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                user_id=user_id,
                session_id=session_key,
                agent_id=agent.id,
                enable_memory=True,
                canvas_mode=agent_config.canvas_enabled,
                canvas_system_prompt=(agent_config.prompt if agent_config.canvas_enabled else None),
            )
            ready_resources = agent_config.ready_resources
        else:
            ready_resources = ()
            voice_pipeline = LLMPipeline(
                provider=config.LLM_PROVIDER,
                api_key=None,
                model=None,
                system_prompt=config.LLM_SYSTEM_PROMPT,
                max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                user_id=user_id,
                session_id=session_key,
                agent_id=agent_id,
                enable_memory=True,
                canvas_mode=True,
                canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT,
            )

        if voice_pipeline.memory and agent_id:
            voice_pipeline.memory.agent_id = agent_id

        if persistent_session_id and voice_pipeline.memory:
            existing_session = SessionRepo.get_by_id(persistent_session_id)
            if existing_session and existing_session.message_count > 0:
                voice_pipeline.memory.load_session_messages(persistent_session_id)
                logger.info(
                    "[%s] Resumed persistent voice session %s for user=%s agent=%s with %d persisted messages",
                    pc_id,
                    persistent_session_id,
                    user_id,
                    agent_id or "none",
                    existing_session.message_count,
                )

        voice_pipeline.load_tools_from_db()
        if agent and ready_resources:
            register_agent_resource_tool(voice_pipeline, agent.id, ready_resources)

        async def canvas_broadcast(operations):
            ch = runtime.datachannels.get(pc_id)
            if ch and ch.readyState == "open":
                ch.send(
                    json.dumps(
                        {
                            "type": "canvas_update",
                            "operations": operations,
                        }
                    )
                )

        voice_pipeline.set_canvas_callback(canvas_broadcast)

        async def animation_broadcast(data):
            tool_name = data.get("tool", "")
            if tool_name == "teach_with_visuals" and data.get("sdl"):
                # Capture SDL for step-pipelined sync — _run_llm_tts will handle it
                runtime.pending_sdl[pc_id] = data["sdl"]

        voice_pipeline.set_animation_callback(animation_broadcast)
        runtime.voice_sessions[pc_id] = voice_pipeline
        _touch_voice_session(pc_id)
        logger.info(
            "[%s] Voice session created (persistent_session=%s, agent=%s, tools=%d)",
            pc_id,
            session_key,
            agent_id or "none",
            len(voice_pipeline.get_tools_schema()),
        )
    else:
        _touch_voice_session(pc_id)
    return runtime.voice_sessions[pc_id]


async def _cancel_active_turn(pc_id: str):
    """Cancel any in-flight LLM+TTS processing for this peer."""
    prev = runtime.turn_processing_tasks.pop(pc_id, None)
    if prev and not prev.done():
        logger.info("[%s] Cancelling previous turn processing", pc_id)
        runtime.tts_interrupt_flags[pc_id] = False  # Stop TTS immediately
        prev.cancel()
        try:
            await prev
        except (asyncio.CancelledError, Exception):
            pass


async def _process_user_turn(pc_id: str, user_text: str):
    """
    Schedule LLM → TTS pipeline for a confirmed user turn.
    Cancels any previous in-flight turn for this peer first.
    """
    _touch_voice_session(pc_id)
    # Stamp turn-confirmed time
    timing = runtime.turn_timing.setdefault(pc_id, {})
    timing["turn_confirmed_ts"] = time.perf_counter()
    await _cancel_active_turn(pc_id)
    task = asyncio.create_task(_run_llm_tts(pc_id, user_text))
    runtime.turn_processing_tasks[pc_id] = task


def _split_sentence(buf: str):
    """Split buffer at the first sentence boundary, returning (sentence, remainder).
    Returns (None, buf) if no boundary found yet."""
    # Match sentence-ending punctuation followed by a space, newline, or end-of-string
    m = re.search(r"[.!?](?:\s|$)", buf)
    if m:
        idx = m.end()
        return buf[:idx].strip(), buf[idx:]
    return None, buf


async def _run_sdl_step_pipeline(pc_id: str, sdl: dict):
    """
    Step-pipelined SDL: stream per-step TTS + visual commands.

    For each SDL step, sends the visual commands and TTS audio together,
    creating a 'person drawing while talking' effect.
    """
    ch = runtime.datachannels.get(pc_id)
    steps = sdl.get("steps", [])
    seq_id = f"seq_{uuid.uuid4().hex[:8]}"

    if ch and ch.readyState == "open":
        ch.send(
            json.dumps(
                {
                    "type": "sdl_start",
                    "sequence_id": seq_id,
                    "total_steps": len(steps),
                    "sdl": sdl,
                }
            )
        )

    runtime.tts_interrupt_flags[pc_id] = True

    for step_idx, step in enumerate(steps):
        if not runtime.tts_interrupt_flags.get(pc_id, False):
            break  # interrupted

        say_text = step.get("say", "").strip()

        # Generate TTS for this step's say text
        if say_text and tts_pipeline:
            total_audio_bytes = 0
            async for audio_chunk in tts_pipeline.text_to_speech_stream(say_text):
                if not runtime.tts_interrupt_flags.get(pc_id, False):
                    break

                # Send sdl_step on first chunk so frontend starts animation with audio
                if total_audio_bytes == 0 and ch and ch.readyState == "open":
                    ch.send(
                        json.dumps(
                            {
                                "type": "sdl_step",
                                "sequence_id": seq_id,
                                "step_index": step_idx,
                            }
                        )
                    )

                total_audio_bytes += len(audio_chunk)
                if ch and ch.readyState == "open":
                    ch.send(
                        json.dumps(
                            {
                                "type": "tts_chunk",
                                "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                                "sequence_id": seq_id,
                                "step_index": step_idx,
                            }
                        )
                    )

            # Audio duration from PCM byte count: 16-bit (2 bytes) @ 16kHz
            audio_duration_ms = round(total_audio_bytes / 32) if total_audio_bytes > 0 else 0
            if ch and ch.readyState == "open":
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_step_complete",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                            "audio_duration_ms": audio_duration_ms,
                        }
                    )
                )
        else:
            # No speech for this step — send sdl_step and complete immediately
            if ch and ch.readyState == "open":
                ch.send(
                    json.dumps(
                        {
                            "type": "sdl_step",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                        }
                    )
                )
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_step_complete",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                            "audio_duration_ms": 0,
                        }
                    )
                )

    # Sequence complete
    runtime.tts_interrupt_flags[pc_id] = False
    if ch and ch.readyState == "open":
        ch.send(
            json.dumps(
                {
                    "type": "sdl_complete",
                    "sequence_id": seq_id,
                }
            )
        )


async def _run_llm_tts(pc_id: str, user_text: str):
    """
    LLM → TTS pipeline with sentence-pipelined audio.

    Key optimisation: TTS starts on the FIRST complete sentence from the LLM
    text stream, rather than waiting for the full response. This cuts perceived
    latency roughly in half.

    Drawings (tool calls) are dispatched during the LLM tool-calling rounds,
    which happen BEFORE text streaming, so the user sees whiteboard activity
    even while waiting for audio.

    If the LLM calls teach_with_visuals (SDL), after text TTS completes,
    the SDL steps are streamed with per-step TTS for voice-visual sync.
    """
    ch = runtime.datachannels.get(pc_id)
    pipeline = await _ensure_voice_session(pc_id)

    # Model routing: fast model by default, escalate for complex queries
    routed_provider, routed_key, routed_model = route_model(user_text)
    pipeline.switch_provider(routed_provider, routed_key, routed_model)

    # Clear any stale SDL from previous turns
    runtime.pending_sdl.pop(pc_id, None)

    # Grab timing context from STT/turn detection phase
    timing = runtime.turn_timing.pop(pc_id, {})
    t_speech_start = timing.get("speech_start_ts")
    t_stt_final = timing.get("stt_final_ts")
    t_turn_confirmed = timing.get("turn_confirmed_ts", time.perf_counter())
    smart_turn_result = timing.get("smart_turn_result")
    t_turn_detection = timing.get("turn_detection_ms")

    t_llm_start = None
    t_llm_end = None
    t_tts_start = None
    t_tts_first_chunk = None
    t_tts_end = None
    tts_chunks_sent = 0
    tts_interrupted = False
    tts_retry_count = 0
    tts_fallback_used = False
    tts_fallback_provider = None
    tts_providers_used: list[str] = []
    llm_response = ""
    error_msg = None

    # Sentence queue: LLM text stream → sentence buffer → TTS tasks
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _tts_sender():
        """Consume sentences from the queue and stream TTS audio to client."""
        nonlocal tts_chunks_sent
        nonlocal tts_fallback_provider
        nonlocal tts_fallback_used
        nonlocal tts_interrupted
        nonlocal tts_retry_count
        nonlocal t_tts_first_chunk
        nonlocal t_tts_end
        nonlocal t_tts_start
        nonlocal error_msg

        tts_started_sent = False

        async def _emit_audio_chunk(audio_chunk: bytes) -> None:
            nonlocal tts_chunks_sent, t_tts_first_chunk, tts_interrupted
            if not runtime.tts_interrupt_flags.get(pc_id, False):
                tts_interrupted = True
                raise asyncio.CancelledError("TTS interrupted")

            if ch and ch.readyState == "open":
                if tts_chunks_sent == 0:
                    t_tts_first_chunk = time.perf_counter()
                    logger.info("[%s] First TTS chunk (%d bytes)", pc_id, len(audio_chunk))
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_chunk",
                            "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                        }
                    )
                )
                tts_chunks_sent += 1

        async def _stream_with_pipeline(sentence: str, pipeline_obj: Any) -> None:
            async for audio_chunk in pipeline_obj.text_to_speech_stream(sentence):
                await _emit_audio_chunk(audio_chunk)

        async def _stream_sentence_with_resilience(sentence: str) -> dict[str, Any]:
            metadata = {
                "provider_used": None,
                "retry_count": 0,
                "fallback_used": False,
                "fallback_provider": None,
                "final_error": None,
            }
            primary_pipeline = tts_pipeline
            primary_provider = _get_tts_provider_name(primary_pipeline)

            if primary_pipeline is not None:
                max_attempts = 1
                if primary_provider == "elevenlabs":
                    max_attempts += max(0, config.TTS_MAX_RETRIES)

                for attempt in range(max_attempts):
                    try:
                        await _stream_with_pipeline(sentence, primary_pipeline)
                        metadata["provider_used"] = primary_provider
                        return metadata
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        metadata["final_error"] = f"{type(exc).__name__}: {exc}"
                        should_retry = (
                            primary_provider == "elevenlabs"
                            and attempt < max_attempts - 1
                            and is_retryable_tts_error(exc)
                        )
                        if not should_retry:
                            break

                        metadata["retry_count"] += 1
                        delay_secs = config.TTS_RETRY_BASE_DELAY_SECS * (2**attempt)
                        logger.warning(
                            "[%s] ElevenLabs TTS failed on attempt %d/%d, retrying in %.2fs: %s",
                            pc_id,
                            attempt + 1,
                            max_attempts,
                            delay_secs,
                            exc,
                        )
                        await asyncio.sleep(delay_secs)

            fallback_pipeline = _get_kokoro_fallback_pipeline()
            if fallback_pipeline is not None and fallback_pipeline is not primary_pipeline:
                try:
                    await _stream_with_pipeline(sentence, fallback_pipeline)
                    metadata["provider_used"] = "kokoro"
                    metadata["fallback_used"] = True
                    metadata["fallback_provider"] = "kokoro"
                    return metadata
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    metadata["final_error"] = f"{type(exc).__name__}: {exc}"

            raise RuntimeError(metadata["final_error"] or "TTS failed without a fallback path")

        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                break  # poison pill — no more sentences

            if not sentence.strip():
                continue

            if not tts_pipeline and not _get_kokoro_fallback_pipeline():
                continue

            if not tts_started_sent:
                t_tts_start = time.perf_counter()
                runtime.tts_interrupt_flags[pc_id] = True
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({"type": "tts_started"}))
                tts_started_sent = True

            try:
                sentence_meta = await _stream_sentence_with_resilience(sentence)
                tts_retry_count += int(sentence_meta.get("retry_count") or 0)
                if sentence_meta.get("fallback_used"):
                    tts_fallback_used = True
                    tts_fallback_provider = sentence_meta.get("fallback_provider") or "kokoro"
                provider_used = sentence_meta.get("provider_used")
                if provider_used and provider_used not in tts_providers_used:
                    tts_providers_used.append(provider_used)
            except asyncio.CancelledError:
                tts_interrupted = True
                logger.warning("[%s] TTS interrupted after %d chunks", pc_id, tts_chunks_sent)
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({"type": "tts_interrupted", "chunks_sent": tts_chunks_sent}))
                return
            except Exception as tts_err:
                logger.exception("[%s] TTS error on sentence: %s", pc_id, tts_err)
                error_msg = error_msg or str(tts_err)

        # All sentences processed
        t_tts_end = time.perf_counter()
        runtime.tts_interrupt_flags[pc_id] = False
        if tts_started_sent and not tts_interrupted:
            logger.info("[%s] TTS complete (%d chunks)", pc_id, tts_chunks_sent)
            if ch and ch.readyState == "open":
                ch.send(json.dumps({"type": "tts_complete"}))

    try:
        logger.info("[%s] → LLM: '%s'", pc_id, user_text[:60])
        t_llm_start = time.perf_counter()

        # Start TTS sender task — it blocks on the queue until sentences arrive
        tts_task = asyncio.create_task(_tts_sender())

        sentence_buf = ""
        async for chunk in pipeline.chat_with_tools_stream(
            user_text,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
        ):
            llm_response += chunk
            sentence_buf += chunk

            # Try to extract complete sentences and push them to TTS immediately
            while True:
                sentence, sentence_buf = _split_sentence(sentence_buf)
                if sentence is None:
                    break
                await sentence_queue.put(sentence)

        t_llm_end = time.perf_counter()
        logger.info("[%s] ← LLM (%d chars)", pc_id, len(llm_response))

        # Flush remaining text to TTS
        if sentence_buf.strip():
            await sentence_queue.put(sentence_buf.strip())

        # Signal TTS sender to finish
        await sentence_queue.put(None)

        # Send full response to client (for display)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "llm_response", "text": llm_response}))

        # Wait for sentence TTS to finish
        await tts_task

        # If SDL was captured during tool execution, run step-pipelined sync
        pending_sdl = runtime.pending_sdl.pop(pc_id, None)
        if pending_sdl and pending_sdl.get("steps"):
            logger.info(
                "[%s] Starting SDL step pipeline (%d steps)", pc_id, len(pending_sdl["steps"])
            )
            await _run_sdl_step_pipeline(pc_id, pending_sdl)

    except asyncio.CancelledError:
        logger.info("[%s] Turn processing cancelled (new turn arrived)", pc_id)
        tts_interrupted = True
        runtime.tts_interrupt_flags[pc_id] = False
        # Kill TTS sender
        sentence_queue.put_nowait(None)
        ch = runtime.datachannels.get(pc_id)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "tts_interrupted", "reason": "new_turn"}))
    except Exception as e:
        error_msg = str(e)
        logger.exception("[%s] Pipeline error: %s", pc_id, e)
        sentence_queue.put_nowait(None)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "error", "message": error_msg}))
    finally:
        t_end = time.perf_counter()
        runtime.turn_processing_tasks.pop(pc_id, None)
        runtime.tts_interrupt_flags.pop(pc_id, None)

        # ── Compute all latencies ──
        def _ms(start, end):
            if start is not None and end is not None:
                return round((end - start) * 1000, 2)
            return None

        llm_metrics = pipeline.get_last_call_metrics() or {}

        latency_vad_ms = None
        latency_stt_ms = (
            _ms(t_speech_start, t_stt_final) if t_speech_start and t_stt_final else None
        )
        latency_stt_to_llm_ms = _ms(t_turn_confirmed, t_llm_start)
        latency_llm_ms = llm_metrics.get("latency_llm_ms") or _ms(t_llm_start, t_llm_end)
        latency_llm_first_token_ms = llm_metrics.get("latency_llm_first_token_ms")
        latency_tool_ms = llm_metrics.get("latency_tool_ms")
        latency_tts_ms = _ms(t_tts_start, t_tts_end)
        latency_tts_first_chunk_ms = _ms(t_tts_start, t_tts_first_chunk)
        latency_total_ms = _ms(t_speech_start or t_turn_confirmed, t_end)
        turn_detection_ms = t_turn_detection

        metrics_payload = {
            "latency_stt_ms": latency_stt_ms,
            "latency_turn_detection_ms": turn_detection_ms,
            "latency_stt_to_llm_ms": latency_stt_to_llm_ms,
            "latency_llm_ms": latency_llm_ms,
            "latency_llm_first_token_ms": latency_llm_first_token_ms,
            "latency_context_ms": llm_metrics.get("latency_context_ms"),
            "latency_tools_schema_ms": llm_metrics.get("latency_tools_schema_ms"),
            "latency_tool_ms": latency_tool_ms,
            "latency_tts_ms": latency_tts_ms,
            "latency_tts_first_chunk_ms": latency_tts_first_chunk_ms,
            "latency_total_ms": latency_total_ms,
            "tts_chunks_sent": tts_chunks_sent,
            "tts_interrupted": tts_interrupted,
            "tts_provider_used": ",".join(tts_providers_used) if tts_providers_used else None,
            "tts_retry_count": tts_retry_count,
            "tts_fallback_used": tts_fallback_used,
            "tts_fallback_provider": tts_fallback_provider,
            "smart_turn_result": smart_turn_result,
        }

        # Send metrics to client
        ch = runtime.datachannels.get(pc_id)
        if ch and ch.readyState == "open":
            try:
                ch.send(json.dumps({"type": "pipeline_metrics", **metrics_payload}))
            except Exception:
                pass

        # Log to console
        logger.info(
            "[%s] METRICS: stt=%s turn=%s llm=%s tool=%s tts=%s tts_ttfb=%s total=%s interrupted=%s provider=%s retries=%s fallback=%s",
            pc_id,
            latency_stt_ms,
            turn_detection_ms,
            latency_llm_ms,
            latency_tool_ms,
            latency_tts_ms,
            latency_tts_first_chunk_ms,
            latency_total_ms,
            tts_interrupted,
            metrics_payload["tts_provider_used"],
            tts_retry_count,
            tts_fallback_used,
        )

        # ── Save to DB ──
        try:
            user_id = _get_voice_user_id(pc_id)
            log_record = VoicePipelineLogRepo.save(
                session_id=getattr(pipeline, "session_id", pc_id),
                user_id=user_id,
                mode="voice",
                user_message=user_text,
                response_text=(llm_response[:2000] if llm_response else None),
                llm_provider=pipeline.provider,
                llm_model=getattr(pipeline.client, "model", pipeline.provider),
                latency_vad_ms=latency_vad_ms,
                latency_stt_ms=latency_stt_ms,
                latency_turn_detection_ms=turn_detection_ms,
                latency_stt_to_llm_ms=latency_stt_to_llm_ms,
                latency_llm_ms=latency_llm_ms,
                latency_llm_first_token_ms=latency_llm_first_token_ms,
                latency_tool_ms=latency_tool_ms,
                latency_tts_ms=latency_tts_ms,
                latency_tts_first_chunk_ms=latency_tts_first_chunk_ms,
                latency_total_ms=latency_total_ms,
                tool_calls_json=json.dumps(llm_metrics.get("tool_calls", []))
                if llm_metrics.get("tool_calls")
                else None,
                tokens_in=llm_metrics.get("tokens_in"),
                tokens_out=llm_metrics.get("tokens_out"),
                tts_chunks_sent=tts_chunks_sent,
                tts_interrupted=tts_interrupted,
                smart_turn_used=smart_turn_result is not None,
                smart_turn_result=smart_turn_result,
                error=error_msg,
            )
            TTSResilienceLogRepo.save(
                voice_log_id=log_record.id,
                provider_used=metrics_payload["tts_provider_used"],
                retry_count=tts_retry_count,
                fallback_used=tts_fallback_used,
                fallback_provider=tts_fallback_provider,
                final_error=error_msg,
            )
        except Exception as log_err:
            logger.warning("[%s] Failed to save voice pipeline log: %s", pc_id, log_err)


async def consume_audio_track(track: MediaStreamTrack, pc_id: str):
    """
    Main pipeline: Audio → Deepgram STT → [Smart Turn] → LLM → TTS

    When Smart Turn is enabled:
      1. Audio streams to Deepgram AND into a Smart Turn audio buffer
      2. On Deepgram is_final, Smart Turn analyzes the buffered audio
      3. If turn complete → send accumulated text to LLM
      4. If turn incomplete → wait for more speech (fallback timer kicks in)

    When Smart Turn is disabled:
      Deepgram is_final triggers LLM directly (legacy behavior).
    """
    logger.info("[%s] Audio consumer started", pc_id)

    audio_q: "asyncio.Queue[bytes]" = asyncio.Queue()
    sample_rate = None
    channel_count = None

    # Smart Turn session for this peer (if enabled)
    st_session: SmartTurnSession | None = None
    # Watchdog: force turn if is_final fires but speech_final never arrives
    _watchdog_task: asyncio.Task | None = None
    _WATCHDOG_TIMEOUT = 3.0  # seconds after last is_final before forcing turn

    analyzer = _get_smart_turn_analyzer()
    if analyzer:
        st_session = SmartTurnSession(analyzer)
        runtime.smart_turn_sessions[pc_id] = st_session

        # Wire up fallback callback: when silence exceeds stop_secs, force-complete
        async def on_fallback_complete(text: str):
            timing = runtime.turn_timing.setdefault(pc_id, {})
            timing["smart_turn_result"] = "fallback"
            await _process_user_turn(pc_id, text)

        st_session.set_turn_complete_callback(on_fallback_complete)
        logger.info("[%s] Smart Turn session created", pc_id)

    async def on_deepgram_event(data: dict[str, Any]):
        """Handle Deepgram transcript events with full timing instrumentation.

        Key distinction:
          - is_final: transcript for this audio segment is finalized (fires often)
          - speech_final: endpointing detected a real pause in speech (fires on silence)

        With Smart Turn: accumulate on is_final, run Smart Turn on speech_final.
        Without Smart Turn: process on speech_final (legacy behavior).
        """
        event_type = data.get("type")

        if event_type in ("Results", "results"):
            channel_obj = data.get("channel", {})
            alts = channel_obj.get("alternatives", [])
            if not alts:
                return

            transcript = alts[0].get("transcript", "")
            is_final = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if transcript.strip() and (is_final or speech_final):
                logger.debug(
                    "[%s] DG event: is_final=%s speech_final=%s text='%s'",
                    pc_id,
                    is_final,
                    speech_final,
                    transcript[:40],
                )

            ch = runtime.datachannels.get(pc_id)
            tts_was_active = runtime.tts_interrupt_flags.get(pc_id, False)

            # ── Timing: record first speech detection ──
            timing = runtime.turn_timing.setdefault(pc_id, {})
            if transcript.strip() and "speech_start_ts" not in timing:
                timing["speech_start_ts"] = time.perf_counter()

            if ch and ch.readyState == "open":
                # Interruption: stop TTS if user speaks during playback
                if transcript.strip() and tts_was_active:
                    logger.warning(
                        "[%s] INTERRUPT - User speaking during TTS: '%s'",
                        pc_id,
                        transcript[:30],
                    )
                    runtime.tts_interrupt_flags[pc_id] = False
                    if st_session:
                        st_session._reset_turn()
                        logger.info("[%s] Smart Turn reset for new turn after interrupt", pc_id)

                # Forward all transcripts to client
                ch.send(
                    json.dumps(
                        {
                            "type": "transcript",
                            "text": transcript,
                            "is_final": is_final,
                            "speech_final": speech_final,
                        }
                    )
                )

            if st_session:
                # ── Smart Turn path ──────────────────────────────
                if is_final and transcript.strip():
                    st_session.accumulate_transcript(transcript)
                    # Record STT final timestamp
                    timing["stt_final_ts"] = time.perf_counter()

                    # Start/restart watchdog: if speech_final never arrives,
                    # force the turn after _WATCHDOG_TIMEOUT seconds.
                    nonlocal _watchdog_task
                    if _watchdog_task is not None:
                        _watchdog_task.cancel()

                    async def _watchdog_force_turn():
                        try:
                            await asyncio.sleep(_WATCHDOG_TIMEOUT)
                            text = st_session.accumulated_transcript.strip()
                            if text:
                                logger.warning(
                                    "[%s] Watchdog: speech_final never fired, forcing turn (text='%s')",
                                    pc_id,
                                    text[:60],
                                )
                                timing = runtime.turn_timing.setdefault(pc_id, {})
                                timing["smart_turn_result"] = "watchdog"
                                st_session._reset_turn()
                                await _process_user_turn(pc_id, text)
                        except asyncio.CancelledError:
                            pass

                    _watchdog_task = asyncio.create_task(_watchdog_force_turn())

                if speech_final and st_session.accumulated_transcript.strip():
                    # Cancel watchdog — speech_final arrived normally
                    if _watchdog_task is not None:
                        _watchdog_task.cancel()
                        _watchdog_task = None

                    logger.info(
                        "[%s] Speech final → Smart Turn (text='%s')",
                        pc_id,
                        st_session.accumulated_transcript[:60],
                    )

                    t_smart_start = time.perf_counter()
                    is_complete, accumulated_text = await st_session.on_speech_final("")
                    t_smart_end = time.perf_counter()

                    timing["turn_detection_ms"] = round((t_smart_end - t_smart_start) * 1000, 2)
                    timing["smart_turn_result"] = "complete" if is_complete else "incomplete"

                    if ch and ch.readyState == "open":
                        ch.send(
                            json.dumps(
                                {
                                    "type": "smart_turn",
                                    "is_complete": is_complete,
                                    "latency_ms": timing["turn_detection_ms"],
                                }
                            )
                        )

                    if is_complete and accumulated_text:
                        await _process_user_turn(pc_id, accumulated_text)

            else:
                # ── Legacy path (no Smart Turn) ──────────────────
                if (is_final or speech_final) and transcript.strip():
                    logger.info("[%s] Final: '%s'", pc_id, transcript)
                    timing["stt_final_ts"] = time.perf_counter()
                    await _process_user_turn(pc_id, transcript)

    dg_stream_task: asyncio.Task | None = None

    try:
        # Get audio parameters from first frame
        first_frame: AudioFrame = await track.recv()
        sample_rate = getattr(first_frame, "sample_rate", 48000)

        # Determine channel count
        channel_count = None
        layout = getattr(first_frame, "layout", None)
        if layout is not None:
            channel_count = getattr(layout, "channels", None)
        if channel_count is None:
            channel_count = getattr(first_frame, "channels", None)

        if isinstance(channel_count, Sequence) and not isinstance(channel_count, (str, bytes)):
            channel_count = len(channel_count)

        if channel_count is None or not isinstance(channel_count, int):
            arr = first_frame.to_ndarray()
            if arr.ndim == 1:
                channel_count = 1
            else:
                channel_count = arr.shape[-1]

        logger.info("[%s] Audio: %sHz, %dch", pc_id, sample_rate, channel_count)

        # ── Deepgram connection ──
        base_url = "wss://api.deepgram.com/v1/listen"
        # When Smart Turn is active, use moderate endpointing so Deepgram fires
        # speech_final on real pauses. Smart Turn then decides if the turn is done.
        # Too low (500ms) fragments speech; too high adds latency.
        dg_endpointing = 1000 if st_session else config.DEEPGRAM_ENDPOINTING
        dg_utterance_end = 1000 if st_session else config.DEEPGRAM_UTTERANCE_END_MS
        websocket_url = (
            f"{base_url}?model={config.DEEPGRAM_MODEL}"
            f"&encoding=linear16&sample_rate={sample_rate}&channels={channel_count}"
            f"&interim_results=true&endpointing={dg_endpointing}"
            f"&utterance_end_ms={dg_utterance_end}"
            f"&smart_format=false&punctuate=false&diarize=false"
        )

        async def on_deepgram_connected():
            ch = runtime.datachannels.get(pc_id)
            if ch and ch.readyState == "open":
                ch.send(json.dumps({"type": "ready"}))
                logger.info("[%s] Sent ready signal to client", pc_id)

        dg_stream_task = asyncio.create_task(
            deepgram_stream_ws_send_and_recv(
                websocket_url,
                config.DEEPGRAM_KEY,
                audio_q,
                on_deepgram_event,
                on_connected=on_deepgram_connected,
            )
        )

        # Send first frame to both Deepgram and Smart Turn buffer
        first_pcm = audioframe_to_pcm16_bytes(first_frame)
        await audio_q.put(first_pcm)
        if st_session:
            st_session.feed_audio(first_pcm, sample_rate, channel_count)

        # ── Audio streaming loop ──
        while True:
            frame = await track.recv()
            pcm_bytes = audioframe_to_pcm16_bytes(frame)
            await audio_q.put(pcm_bytes)

            # Feed audio into Smart Turn buffer (alongside Deepgram)
            if st_session:
                st_session.feed_audio(pcm_bytes, sample_rate, channel_count)

    except asyncio.CancelledError:
        logger.info("[%s] Cancelled", pc_id)
        raise
    except Exception as e:
        logger.exception("[%s] Error: %s", pc_id, e)
    finally:
        try:
            await audio_q.put(None)
        except Exception:
            pass

        if dg_stream_task:
            try:
                await asyncio.wait_for(dg_stream_task, timeout=2.0)
            except Exception:
                dg_stream_task.cancel()

        # Cleanup Smart Turn session
        if st_session:
            st_session.cleanup()
            runtime.smart_turn_sessions.pop(pc_id, None)

        logger.info("[%s] Consumer finished", pc_id)


@app.post("/offer")
async def offer(body: Offer, request: Request):
    offer = RTCSessionDescription(sdp=body.sdp, type=body.type)
    user_id = require_auth(request)
    persistent_session_id = body.session_id
    agent_id = body.agent_id
    existing_session = None

    if persistent_session_id:
        existing_session = SessionRepo.get_by_id(persistent_session_id)
        if not existing_session:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        if existing_session.user_id != user_id:
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        if agent_id and agent_id != existing_session.agent_id:
            return JSONResponse(
                {"error": "Session does not belong to the supplied agent"}, status_code=400
            )
        agent_id = existing_session.agent_id

    if agent_id:
        agent = AgentRepo.get_by_id(agent_id)
        if not agent:
            return JSONResponse({"error": "Agent not found"}, status_code=404)
        if agent.user_id != user_id:
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    if not persistent_session_id and agent_id:
        db_session = SessionRepo.create(user_id=user_id, agent_id=agent_id)
        persistent_session_id = db_session.id
        existing_session = db_session

    pc = RTCPeerConnection()
    pc_id = f"pc-{id(pc)}"
    runtime.peer_user_ids[pc_id] = user_id
    if agent_id:
        runtime.peer_agent_ids[pc_id] = agent_id
    if persistent_session_id:
        runtime.peer_session_ids[pc_id] = persistent_session_id
    _touch_voice_session(pc_id)
    canvas_mode = body.canvas_mode
    runtime.peer_canvas_modes[pc_id] = canvas_mode
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
        runtime.datachannels[pc_id] = channel

        @channel.on("message")
        def on_message(message):
            try:
                data = json.loads(message)
                # Handle stop_tts command from client
                if data.get("type") == "stop_tts":
                    logger.warning("[%s] Client requested TTS stop", pc_id)
                    runtime.tts_interrupt_flags[pc_id] = False
                    st = runtime.smart_turn_sessions.get(pc_id)
                    if st:
                        st._reset_turn()
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug("[%s] Ignoring malformed data-channel message: %s", pc_id, exc)

        @channel.on("close")
        async def on_close():
            logger.info("[%s] DataChannel closed", pc_id)
            await _finalize_voice_session(pc_id, background=True, pc=pc)

    runtime.peer_connections.add(pc)
    logger.info("[%s] created for incoming offer", pc_id)

    media_blackhole = MediaBlackhole()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("[%s] Connection: %s", pc_id, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await _finalize_voice_session(pc_id, background=True, pc=pc)
            logger.info("[%s] Closed", pc_id)

    @pc.on("track")
    def on_track(track):
        logger.info(
            "[%s] Track received: kind=%s id=%s", pc_id, track.kind, getattr(track, "id", "?")
        )
        if track.kind == "audio":
            task = asyncio.create_task(consume_audio_track(track, pc_id))

            @track.on("ended")
            def on_ended():
                logger.info("[%s] Track ended", pc_id)
                task.cancel()

        else:
            pc.addTrack(track)
            blackhole_task = asyncio.create_task(media_blackhole.start())
            _log_background_task(blackhole_task, f"media blackhole [{pc_id}]")

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info("[%s] Answer created", pc_id)
    return JSONResponse(
        {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "session_id": persistent_session_id,
            "agent_id": agent_id,
        }
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)

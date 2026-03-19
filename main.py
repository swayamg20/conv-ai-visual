import asyncio
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
import base64
import json
import logging
import os
import re
import time
import uuid
from typing import Any
from uuid import uuid4 as _uuid4
import numpy as np
import uvicorn
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaBlackhole
from aiortc.mediastreams import AudioFrame
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from funcs.agents import compile_agent_prompt, get_agent_tools, append_resource_context
from funcs.auth import get_current_user_id, get_current_user, require_auth
from funcs.config import config
from funcs.llm_clients import create_llm_client
from funcs.llm_pipeline import LLMPipeline
from funcs.models import (
    UserRepo, AgentRepo, SessionRepo, ConversationMessageRepo, ResourceRepo,
    TopicMasteryRepo, TopicMasteryModel, LLMCallLogRepo, VoicePipelineLogRepo,
)
from collections.abc import Sequence
from funcs.model_router import route_model
from funcs.resources import ingest_pdf, ingest_url, search_chunks
from funcs.search import register_web_search_tool
from funcs.smart_turn import SmartTurnAnalyzer, SmartTurnSession
from funcs.tts_pipeline import TTSPipeline

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-deepgram")

try:
    config.validate()
        
    llm_pipeline = LLMPipeline(
        provider=config.LLM_PROVIDER,
        api_key=None,  # Factory will get from config based on provider
        model=None,    # Factory will get from config based on provider
        system_prompt=config.LLM_SYSTEM_PROMPT,
        max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES
    )
    logger.info("LLM pipeline initialized successfully")

    # Register web_search tool in DB so it's available for agents with web_search capability
    try:
        register_web_search_tool()
    except Exception as e:
        logger.warning("Failed to register web_search tool: %s", e)
    
    if config.TTS_PROVIDER == "kokoro":
        from funcs.kokoro_tts import KokoroTTSPipeline
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
            use_speaker_boost=config.TTS_USE_SPEAKER_BOOST
        )
        logger.info("ElevenLabs TTS pipeline initialized")

    # Smart Turn analyzer (singleton, shared across sessions)
    smart_turn_analyzer: SmartTurnAnalyzer | None = None
    if config.SMART_TURN_ENABLED:
        try:
            smart_turn_analyzer = SmartTurnAnalyzer.get_instance(
                model_path=config.SMART_TURN_MODEL_PATH,
                threshold=config.SMART_TURN_THRESHOLD,
                stop_secs=config.SMART_TURN_STOP_SECS,
            )
            logger.info("Smart Turn analyzer initialized (threshold=%.2f, stop_secs=%.1f)",
                         config.SMART_TURN_THRESHOLD, config.SMART_TURN_STOP_SECS)
        except Exception as e:
            logger.warning("Smart Turn init failed, falling back to Deepgram endpointing: %s", e)
            smart_turn_analyzer = None
    else:
        logger.info("Smart Turn disabled, using Deepgram endpointing only")
    
except Exception as e:
    logger.error("Failed to initialize pipelines: %s", e)
    llm_pipeline = None
    tts_pipeline = None
    smart_turn_analyzer = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Offer(BaseModel):
    sdp: str
    type: str

class ChatMessage(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str | None = None
    canvas_mode: bool | None = None
    agent_id: str | None = None

class CanvasModeRequest(BaseModel):
    enabled: bool
    custom_prompt: str | None = None

class CreateSessionRequest(BaseModel):
    agent_id: str

class CreateAgentRequest(BaseModel):
    name: str
    description: str | None = None
    persona: dict[str, Any] | None = None
    capabilities: list[str] = ["canvas"]
    icon: str | None = None

class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    persona: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    icon: str | None = None
    is_default: bool | None = None

class AddResourceURLRequest(BaseModel):
    url: str

chat_sessions: dict[str, LLMPipeline] = {}
peer_canvas_modes: dict[str, bool] = {}
pcs: set[Any] = set()
datachannels: dict[str, Any] = {}
voice_sessions: dict[str, LLMPipeline] = {}
peer_user_ids: dict[str, str] = {}
tts_interrupt_flags: dict[str, bool] = {}  # Simple interrupt flag: True = TTS active, False = stop
_pending_sdl: dict[str, dict] = {}  # Per-peer captured SDL from teach_with_visuals tool calls
smart_turn_sessions: dict[str, SmartTurnSession] = {}  # Per-peer Smart Turn state
turn_processing_tasks: dict[str, asyncio.Task] = {}  # Per-peer active LLM+TTS task (cancel on new turn)

# Per-peer turn timing data. Populated by consume_audio_track, consumed by _run_llm_tts.
# Keys: speech_start_ts, stt_final_ts, turn_confirmed_ts, smart_turn_result, vad_detect_ts
turn_timing: dict[str, dict[str, Any]] = {}

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
    if pc_id not in voice_sessions:
        user_id = peer_user_ids.get(pc_id, "default_user")
        canvas_mode = True
        voice_pipeline = LLMPipeline(
            provider=config.LLM_PROVIDER,
            api_key=None,
            model=None,
            system_prompt=config.LLM_SYSTEM_PROMPT,
            max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
            user_id=user_id,
            session_id=pc_id,
            enable_memory=True,
            canvas_mode=canvas_mode,
            canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT,
        )
        voice_pipeline.load_tools_from_db()

        async def canvas_broadcast(operations):
            ch = datachannels.get(pc_id)
            if ch and ch.readyState == "open":
                ch.send(json.dumps({
                    "type": "canvas_update",
                    "operations": operations,
                }))

        voice_pipeline.set_canvas_callback(canvas_broadcast)

        async def animation_broadcast(data):
            tool_name = data.get("tool", "")
            if tool_name == "teach_with_visuals" and data.get("sdl"):
                # Capture SDL for step-pipelined sync — _run_llm_tts will handle it
                _pending_sdl[pc_id] = data["sdl"]

        voice_pipeline.set_animation_callback(animation_broadcast)
        voice_sessions[pc_id] = voice_pipeline
        logger.info("[%s] Session created (%d tools)", pc_id, len(voice_pipeline.get_tools_schema()))
    return voice_sessions[pc_id]


async def _cancel_active_turn(pc_id: str):
    """Cancel any in-flight LLM+TTS processing for this peer."""
    prev = turn_processing_tasks.pop(pc_id, None)
    if prev and not prev.done():
        logger.info("[%s] Cancelling previous turn processing", pc_id)
        tts_interrupt_flags[pc_id] = False  # Stop TTS immediately
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
    # Stamp turn-confirmed time
    timing = turn_timing.setdefault(pc_id, {})
    timing["turn_confirmed_ts"] = time.perf_counter()
    await _cancel_active_turn(pc_id)
    task = asyncio.create_task(_run_llm_tts(pc_id, user_text))
    turn_processing_tasks[pc_id] = task


def _split_sentence(buf: str):
    """Split buffer at the first sentence boundary, returning (sentence, remainder).
    Returns (None, buf) if no boundary found yet."""
    # Match sentence-ending punctuation followed by a space, newline, or end-of-string
    m = re.search(r'[.!?](?:\s|$)', buf)
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
    ch = datachannels.get(pc_id)
    steps = sdl.get("steps", [])
    seq_id = f"seq_{uuid.uuid4().hex[:8]}"

    if ch and ch.readyState == "open":
        ch.send(json.dumps({
            "type": "sdl_start",
            "sequence_id": seq_id,
            "total_steps": len(steps),
            "sdl": sdl,
        }))

    tts_interrupt_flags[pc_id] = True

    for step_idx, step in enumerate(steps):
        if not tts_interrupt_flags.get(pc_id, False):
            break  # interrupted

        say_text = step.get("say", "").strip()

        # Generate TTS for this step's say text
        if say_text and tts_pipeline:
            total_audio_bytes = 0
            async for audio_chunk in tts_pipeline.text_to_speech_stream(say_text):
                if not tts_interrupt_flags.get(pc_id, False):
                    break

                # Send sdl_step on first chunk so frontend starts animation with audio
                if total_audio_bytes == 0 and ch and ch.readyState == "open":
                    ch.send(json.dumps({
                        "type": "sdl_step",
                        "sequence_id": seq_id,
                        "step_index": step_idx,
                    }))

                total_audio_bytes += len(audio_chunk)
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({
                        "type": "tts_chunk",
                        "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                        "sequence_id": seq_id,
                        "step_index": step_idx,
                    }))

            # Audio duration from PCM byte count: 16-bit (2 bytes) @ 16kHz
            audio_duration_ms = round(total_audio_bytes / 32) if total_audio_bytes > 0 else 0
            if ch and ch.readyState == "open":
                ch.send(json.dumps({
                    "type": "tts_step_complete",
                    "sequence_id": seq_id,
                    "step_index": step_idx,
                    "audio_duration_ms": audio_duration_ms,
                }))
        else:
            # No speech for this step — send sdl_step and complete immediately
            if ch and ch.readyState == "open":
                ch.send(json.dumps({
                    "type": "sdl_step",
                    "sequence_id": seq_id,
                    "step_index": step_idx,
                }))
                ch.send(json.dumps({
                    "type": "tts_step_complete",
                    "sequence_id": seq_id,
                    "step_index": step_idx,
                    "audio_duration_ms": 0,
                }))

    # Sequence complete
    tts_interrupt_flags[pc_id] = False
    if ch and ch.readyState == "open":
        ch.send(json.dumps({
            "type": "sdl_complete",
            "sequence_id": seq_id,
        }))


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
    ch = datachannels.get(pc_id)
    pipeline = await _ensure_voice_session(pc_id)

    # Model routing: fast model by default, escalate for complex queries
    routed_provider, routed_key, routed_model = route_model(user_text)
    pipeline.switch_provider(routed_provider, routed_key, routed_model)

    # Clear any stale SDL from previous turns
    _pending_sdl.pop(pc_id, None)

    # Grab timing context from STT/turn detection phase
    timing = turn_timing.pop(pc_id, {})
    t_speech_start = timing.get("speech_start_ts")
    t_stt_final = timing.get("stt_final_ts")
    t_turn_confirmed = timing.get("turn_confirmed_ts", time.perf_counter())
    smart_turn_result = timing.get("smart_turn_result")
    t_turn_detection = timing.get("turn_detection_ms")

    t_pipeline_start = time.perf_counter()
    t_llm_start = None
    t_llm_end = None
    t_tts_start = None
    t_tts_first_chunk = None
    t_tts_end = None
    tts_chunks_sent = 0
    tts_interrupted = False
    llm_response = ""
    error_msg = None

    # Sentence queue: LLM text stream → sentence buffer → TTS tasks
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _tts_sender():
        """Consume sentences from the queue and stream TTS audio to client."""
        nonlocal tts_chunks_sent, tts_interrupted, t_tts_first_chunk, t_tts_end, t_tts_start

        tts_started_sent = False

        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                break  # poison pill — no more sentences

            if not sentence.strip():
                continue

            if not tts_pipeline:
                continue

            if not tts_started_sent:
                t_tts_start = time.perf_counter()
                tts_interrupt_flags[pc_id] = True
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({"type": "tts_started"}))
                tts_started_sent = True

            try:
                async for audio_chunk in tts_pipeline.text_to_speech_stream(sentence):
                    if not tts_interrupt_flags.get(pc_id, False):
                        tts_interrupted = True
                        logger.warning("[%s] TTS interrupted after %d chunks", pc_id, tts_chunks_sent)
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({"type": "tts_interrupted", "chunks_sent": tts_chunks_sent}))
                        return  # exit sender entirely

                    if ch and ch.readyState == "open":
                        if tts_chunks_sent == 0:
                            t_tts_first_chunk = time.perf_counter()
                            logger.info("[%s] First TTS chunk (%d bytes)", pc_id, len(audio_chunk))
                        ch.send(json.dumps({
                            "type": "tts_chunk",
                            "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                        }))
                        tts_chunks_sent += 1
            except Exception as tts_err:
                logger.exception("[%s] TTS error on sentence: %s", pc_id, tts_err)

        # All sentences processed
        t_tts_end = time.perf_counter()
        tts_interrupt_flags[pc_id] = False
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
        pending_sdl = _pending_sdl.pop(pc_id, None)
        if pending_sdl and pending_sdl.get("steps"):
            logger.info("[%s] Starting SDL step pipeline (%d steps)", pc_id, len(pending_sdl["steps"]))
            await _run_sdl_step_pipeline(pc_id, pending_sdl)

    except asyncio.CancelledError:
        logger.info("[%s] Turn processing cancelled (new turn arrived)", pc_id)
        tts_interrupted = True
        tts_interrupt_flags[pc_id] = False
        # Kill TTS sender
        sentence_queue.put_nowait(None)
        ch = datachannels.get(pc_id)
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
        turn_processing_tasks.pop(pc_id, None)
        tts_interrupt_flags.pop(pc_id, None)

        # ── Compute all latencies ──
        def _ms(start, end):
            if start is not None and end is not None:
                return round((end - start) * 1000, 2)
            return None

        llm_metrics = pipeline.get_last_call_metrics() or {}

        latency_vad_ms = None
        latency_stt_ms = _ms(t_speech_start, t_stt_final) if t_speech_start and t_stt_final else None
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
            "smart_turn_result": smart_turn_result,
        }

        # Send metrics to client
        ch = datachannels.get(pc_id)
        if ch and ch.readyState == "open":
            try:
                ch.send(json.dumps({"type": "pipeline_metrics", **metrics_payload}))
            except Exception:
                pass

        # Log to console
        logger.info(
            "[%s] METRICS: stt=%s turn=%s llm=%s tool=%s tts=%s tts_ttfb=%s total=%s interrupted=%s",
            pc_id,
            latency_stt_ms, turn_detection_ms, latency_llm_ms, latency_tool_ms,
            latency_tts_ms, latency_tts_first_chunk_ms, latency_total_ms, tts_interrupted,
        )

        # ── Save to DB ──
        try:
            from funcs.models import VoicePipelineLogRepo
            user_id = peer_user_ids.get(pc_id, "default_user")
            VoicePipelineLogRepo.save(
                session_id=pc_id,
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
                tool_calls_json=json.dumps(llm_metrics.get("tool_calls", [])) if llm_metrics.get("tool_calls") else None,
                tokens_in=llm_metrics.get("tokens_in"),
                tokens_out=llm_metrics.get("tokens_out"),
                tts_chunks_sent=tts_chunks_sent,
                tts_interrupted=tts_interrupted,
                smart_turn_used=smart_turn_result is not None,
                smart_turn_result=smart_turn_result,
                error=error_msg,
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

    if smart_turn_analyzer:
        st_session = SmartTurnSession(smart_turn_analyzer)
        smart_turn_sessions[pc_id] = st_session

        # Wire up fallback callback: when silence exceeds stop_secs, force-complete
        async def on_fallback_complete(text: str):
            timing = turn_timing.setdefault(pc_id, {})
            timing["smart_turn_result"] = "fallback"
            await _process_user_turn(pc_id, text)

        st_session.set_turn_complete_callback(on_fallback_complete)
        logger.info("[%s] Smart Turn session created", pc_id)

    async def on_deepgram_event(data: Dict):
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
                    pc_id, is_final, speech_final, transcript[:40],
                )

            ch = datachannels.get(pc_id)
            tts_was_active = tts_interrupt_flags.get(pc_id, False)

            # ── Timing: record first speech detection ──
            timing = turn_timing.setdefault(pc_id, {})
            if transcript.strip() and "speech_start_ts" not in timing:
                timing["speech_start_ts"] = time.perf_counter()

            if ch and ch.readyState == "open":
                # Interruption: stop TTS if user speaks during playback
                if transcript.strip() and tts_was_active:
                    logger.warning(
                        "[%s] INTERRUPT - User speaking during TTS: '%s'",
                        pc_id, transcript[:30],
                    )
                    tts_interrupt_flags[pc_id] = False
                    if st_session:
                        st_session._reset_turn()
                        logger.info("[%s] Smart Turn reset for new turn after interrupt", pc_id)

                # Forward all transcripts to client
                ch.send(json.dumps({
                    "type": "transcript",
                    "text": transcript,
                    "is_final": is_final,
                    "speech_final": speech_final,
                }))

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
                                    pc_id, text[:60],
                                )
                                timing = turn_timing.setdefault(pc_id, {})
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

                    logger.info("[%s] Speech final → Smart Turn (text='%s')",
                                pc_id, st_session.accumulated_transcript[:60])

                    t_smart_start = time.perf_counter()
                    is_complete, accumulated_text = await st_session.on_speech_final("")
                    t_smart_end = time.perf_counter()

                    timing["turn_detection_ms"] = round((t_smart_end - t_smart_start) * 1000, 2)
                    timing["smart_turn_result"] = "complete" if is_complete else "incomplete"

                    if ch and ch.readyState == "open":
                        ch.send(json.dumps({
                            "type": "smart_turn",
                            "is_complete": is_complete,
                            "latency_ms": timing["turn_detection_ms"],
                        }))

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
            ch = datachannels.get(pc_id)
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
            smart_turn_sessions.pop(pc_id, None)

        logger.info("[%s] Consumer finished", pc_id)

@app.post("/chat")
async def chat(chat_msg: ChatMessage, request: Request):
    """
    Chat mode endpoint with SSE streaming.
    Each session gets its own LLMPipeline with 4-layer memory.
    Supports cross-session persistence when agent_id is provided.
    """
    # user_id from middleware (auth) or request body
    user_id = chat_msg.user_id or get_current_user_id(request)

    # Queues for events during this request
    canvas_events = []
    animation_events = []

    # Resolve agent if provided
    agent = None
    agent_id = chat_msg.agent_id
    if agent_id:
        agent = AgentRepo.get_by_id(agent_id)
        if not agent:
            return JSONResponse({"error": "Agent not found"}, status_code=404)

    # Resolve or create a persistent session when we have an agent
    session_id = chat_msg.session_id
    is_new_session = False
    if not session_id:
        if agent_id and user_id:
            # Create a persistent DB session
            db_session = SessionRepo.create(user_id=user_id, agent_id=agent_id)
            session_id = db_session.id
            is_new_session = True
        else:
            session_id = str(uuid.uuid4())

    # Get or create session pipeline with memory and tools
    if session_id not in chat_sessions:
        try:
            # Use agent system prompt and capabilities when available
            if agent:
                capabilities = agent.get_capabilities()
                has_canvas = "canvas" in capabilities

                # Enrich system prompt with resource names if agent has resources
                agent_prompt = agent.system_prompt
                agent_resources = ResourceRepo.list_by_agent(agent.id)
                ready_resources = [r for r in agent_resources if r.status == "ready"]
                if ready_resources:
                    agent_prompt = append_resource_context(
                        agent_prompt,
                        [r.name for r in ready_resources],
                    )

                pipeline = LLMPipeline(
                    provider=config.LLM_PROVIDER,
                    api_key=None,
                    model=None,
                    system_prompt=agent_prompt,
                    max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                    user_id=user_id,
                    session_id=session_id,
                    enable_memory=True,
                    canvas_mode=has_canvas,
                    canvas_system_prompt=agent_prompt if has_canvas else None,
                )
            else:
                pipeline = LLMPipeline(
                    provider=config.LLM_PROVIDER,
                    api_key=None,
                    model=None,
                    system_prompt=config.LLM_SYSTEM_PROMPT,
                    max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                    user_id=user_id,
                    session_id=session_id,
                    enable_memory=True,
                    canvas_mode=True,
                    canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT,
                )

            # Set agent_id on the memory manager for cross-session context
            if pipeline.memory and agent_id:
                pipeline.memory.agent_id = agent_id

            # If resuming an existing session, load persisted messages
            if not is_new_session and pipeline.memory:
                existing = SessionRepo.get_by_id(session_id)
                if existing and existing.message_count > 0:
                    pipeline.memory.load_session_messages(session_id)
                    logger.info("Resumed session %s with %d persisted messages", session_id, existing.message_count)

            pipeline.load_tools_from_db()

            # Register search_resources tool if this agent has resources
            if agent and agent_resources and ready_resources:
                _agent_id_for_search = agent.id
                def _search_resources_handler(query: str, limit: int = 5) -> str:
                    chunks = search_chunks(_agent_id_for_search, query, limit=limit)
                    if not chunks:
                        return "No relevant content found in the resources."
                    results = []
                    for c in chunks:
                        page_info = f" (page {c.page_number})" if c.page_number else ""
                        results.append(f"[Chunk {c.chunk_index}{page_info}]\n{c.content}")
                    return "\n\n---\n\n".join(results)

                pipeline.register_tool(
                    name="search_resources",
                    description="Search the agent's uploaded resources (PDFs, URLs) for relevant content. Use this when the student asks about topics covered in their materials.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query to find relevant content"},
                            "limit": {"type": "integer", "description": "Max results to return", "default": 5},
                        },
                        "required": ["query"],
                    },
                    func=_search_resources_handler,
                )

            chat_sessions[session_id] = pipeline
            logger.info("Created chat session %s with %d tools (canvas_mode=%s, agent=%s)", session_id, len(pipeline.get_tools_schema()), pipeline.canvas_mode, agent_id)
        except Exception as e:
            logger.exception("Failed to create chat session: %s", e)
            return JSONResponse({"error": "Failed to initialize chat"}, status_code=500)

    # Auto-title the session from the first user message
    if is_new_session and agent_id:
        title = chat_msg.message[:80].strip()
        try:
            SessionRepo.update_title(session_id, title)
        except Exception:
            pass
    
    pipeline = chat_sessions[session_id]
    
    # Update canvas mode if specified in this request
    if chat_msg.canvas_mode is not None:
        pipeline.set_canvas_mode(chat_msg.canvas_mode)
    
    # Set canvas callback to queue events
    def canvas_callback(operations):
        canvas_events.append(operations)
    pipeline.set_canvas_callback(canvas_callback)

    # Set animation callback to queue events
    def animation_callback(data):
        animation_events.append(data)
    pipeline.set_animation_callback(animation_callback)
    
    async def generate():
        try:
            # Send session_id first
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
            
            # Stream response with tools support
            async for chunk in pipeline.chat_with_tools_stream(
                chat_msg.message,
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS
            ):
                # Check if we have pending canvas events to send
                while canvas_events:
                    ops = canvas_events.pop(0)
                    yield f"data: {json.dumps({'type': 'canvas_update', 'operations': ops})}\n\n"

                # Check if we have pending animation events to send
                while animation_events:
                    anim = animation_events.pop(0)
                    yield f"data: {json.dumps({'type': 'animation_event', **anim})}\n\n"

                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            # Send any remaining canvas events
            while canvas_events:
                ops = canvas_events.pop(0)
                yield f"data: {json.dumps({'type': 'canvas_update', 'operations': ops})}\n\n"

            # Send any remaining animation events
            while animation_events:
                anim = animation_events.pop(0)
                yield f"data: {json.dumps({'type': 'animation_event', **anim})}\n\n"

            # Save observability log (legacy + unified)
            try:
                metrics = pipeline.get_last_call_metrics()
                if metrics:
                    LLMCallLogRepo.save(
                        session_id=session_id,
                        user_id=user_id,
                        user_message=chat_msg.message,
                        llm_provider=pipeline.provider,
                        llm_model=getattr(pipeline.client, 'model', pipeline.provider),
                        tool_calls_json=json.dumps(metrics.get("tool_calls", [])),
                        response_text=(metrics.get("response_text") or "")[:2000],
                        latency_total_ms=metrics.get("latency_total_ms"),
                        latency_llm_ms=metrics.get("latency_llm_ms"),
                        latency_tool_ms=metrics.get("latency_tool_ms"),
                        latency_stream_ms=metrics.get("latency_stream_ms"),
                        tokens_in=metrics.get("tokens_in"),
                        tokens_out=metrics.get("tokens_out"),
                        error=metrics.get("error"),
                    )
                    # Also save to unified pipeline log
                    VoicePipelineLogRepo.save(
                        session_id=session_id,
                        user_id=user_id,
                        mode="chat",
                        user_message=chat_msg.message,
                        response_text=(metrics.get("response_text") or "")[:2000],
                        llm_provider=pipeline.provider,
                        llm_model=getattr(pipeline.client, 'model', pipeline.provider),
                        latency_llm_ms=metrics.get("latency_llm_ms"),
                        latency_llm_first_token_ms=metrics.get("latency_llm_first_token_ms"),
                        latency_tool_ms=metrics.get("latency_tool_ms"),
                        latency_total_ms=metrics.get("latency_total_ms"),
                        tool_calls_json=json.dumps(metrics.get("tool_calls", [])),
                        tokens_in=metrics.get("tokens_in"),
                        tokens_out=metrics.get("tokens_out"),
                        error=metrics.get("error"),
                    )
            except Exception as log_err:
                logger.warning("Failed to save LLM call log: %s", log_err)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.exception("Chat stream error: %s", e)
            try:
                LLMCallLogRepo.save(
                    session_id=session_id,
                    user_id=user_id,
                    user_message=chat_msg.message,
                    llm_provider=pipeline.provider,
                    llm_model=getattr(pipeline.client, 'model', pipeline.provider),
                    error=str(e),
                )
            except Exception:
                pass
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.delete("/chat/{session_id}")
async def clear_chat(session_id: str):
    """Clear chat session and optionally save summary to episodic memory."""
    pipeline = chat_sessions.pop(session_id, None)
    if pipeline:
        try:
            # Generate and save session summary before clearing
            summary = await pipeline.generate_session_summary()
            if summary:
                pipeline.end_session(summary)
        except Exception as e:
            logger.warning("Failed to save session summary: %s", e)
    return JSONResponse({"status": "cleared"})



@app.post("/chat/{session_id}/canvas-mode")
async def set_canvas_mode(session_id: str, req: CanvasModeRequest):
    """Toggle canvas mode for an existing chat session."""
    pipeline = chat_sessions.get(session_id)
    if not pipeline:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    
    pipeline.set_canvas_mode(req.enabled, req.custom_prompt)
    return JSONResponse({
        "session_id": session_id,
        "canvas_mode": pipeline.canvas_mode,
        "tools_count": len(pipeline.get_tools_schema())
    })


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the currently authenticated user (Firebase token)."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    return JSONResponse({
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
        }
    })


# ============== Agent CRUD Endpoints ==============


def _agent_to_dict(agent) -> dict:
    """Serialize an AgentModel to a JSON-safe dict."""
    return {
        "id": agent.id,
        "user_id": agent.user_id,
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "persona": agent.get_persona(),
        "capabilities": agent.get_capabilities(),
        "icon": agent.icon,
        "is_default": agent.is_default,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


@app.post("/api/agents")
async def create_agent(body: CreateAgentRequest, request: Request):
    """Create a new agent for the authenticated user."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    persona = body.persona or {}
    system_prompt = compile_agent_prompt(persona, body.capabilities)

    agent = AgentRepo.create(
        user_id=user["id"],
        name=body.name,
        description=body.description,
        system_prompt=system_prompt,
        persona_json=json.dumps(persona) if persona else None,
        capabilities_json=json.dumps(body.capabilities),
        icon=body.icon,
    )
    return JSONResponse(_agent_to_dict(agent), status_code=201)


@app.get("/api/agents")
async def list_agents(request: Request):
    """List all agents for the authenticated user."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agents = AgentRepo.list_by_user(user["id"])
    return JSONResponse({"agents": [_agent_to_dict(a) for a in agents]})


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    """Get a single agent by ID (must be owned by current user)."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    return JSONResponse(_agent_to_dict(agent))


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: UpdateAgentRequest, request: Request):
    """Update an existing agent. Recompiles system prompt if persona or capabilities change."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    updates: dict[str, Any] = {}

    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.icon is not None:
        updates["icon"] = body.icon

    # Determine if we need to recompile the prompt
    needs_recompile = body.persona is not None or body.capabilities is not None
    if needs_recompile:
        persona = body.persona if body.persona is not None else agent.get_persona()
        capabilities = body.capabilities if body.capabilities is not None else agent.get_capabilities()
        updates["system_prompt"] = compile_agent_prompt(persona, capabilities)
        if body.persona is not None:
            updates["persona_json"] = json.dumps(persona)
        if body.capabilities is not None:
            updates["capabilities_json"] = json.dumps(capabilities)

    if body.is_default is True:
        AgentRepo.set_default(agent_id, user["id"])

    updated = AgentRepo.update(agent_id, **updates) if updates else AgentRepo.get_by_id(agent_id)
    return JSONResponse(_agent_to_dict(updated))


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent (must be owned by current user)."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    AgentRepo.delete(agent_id)
    return JSONResponse({"status": "deleted"})


# ============== Resource Endpoints ==============


def _resource_to_dict(r) -> dict:
    """Serialize a ResourceModel to a JSON-safe dict."""
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "name": r.name,
        "resource_type": r.resource_type,
        "chunk_count": r.chunk_count,
        "size_bytes": r.size_bytes,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@app.post("/api/agents/{agent_id}/resources")
async def add_resource(
    agent_id: str,
    request: Request,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
):
    """Upload a PDF file or provide a URL to ingest as a resource for this agent."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if file and file.filename:
        # File upload path
        resource_id = str(_uuid4())
        upload_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "uploads", user["id"]
        )
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, f"{resource_id}.pdf")

        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        resource = ingest_pdf(file_path, agent_id=agent_id, user_id=user["id"])
        return JSONResponse(_resource_to_dict(resource))

    elif url:
        resource = await ingest_url(url, agent_id=agent_id, user_id=user["id"])
        return JSONResponse(_resource_to_dict(resource))

    else:
        return JSONResponse(
            {"error": "Provide either a file upload or a url parameter"},
            status_code=400,
        )


@app.get("/api/agents/{agent_id}/resources")
async def list_resources(agent_id: str, request: Request):
    """List resources for an agent."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    resources = ResourceRepo.list_by_agent(agent_id)
    return JSONResponse([_resource_to_dict(r) for r in resources])


@app.delete("/api/agents/{agent_id}/resources/{resource_id}")
async def delete_resource(agent_id: str, resource_id: str, request: Request):
    """Delete a resource and its chunks."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    deleted = ResourceRepo.delete(resource_id)
    if not deleted:
        return JSONResponse({"error": "Resource not found"}, status_code=404)
    return JSONResponse({"status": "deleted"})


# ============== Mastery / Struggle Heatmap Endpoints ==============


@app.get("/api/agents/{agent_id}/mastery")
async def get_mastery(agent_id: str, request: Request):
    """Get concept mastery data (struggle heatmap) for the current user + agent."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if agent.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    summary = TopicMasteryRepo.get_summary(user["id"], agent_id)
    return JSONResponse(summary)


# ============== Session Endpoints ==============

@app.post("/api/sessions")
async def create_session(body: CreateSessionRequest, request: Request):
    """Create a new persistent session for an agent."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    agent = AgentRepo.get_by_id(body.agent_id)
    if not agent:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    session = SessionRepo.create(user_id=user["id"], agent_id=body.agent_id)
    return JSONResponse({
        "id": session.id,
        "user_id": session.user_id,
        "agent_id": session.agent_id,
        "title": session.title,
        "summary": session.summary,
        "message_count": session.message_count,
        "created_at": session.created_at.isoformat(),
    })


@app.get("/api/sessions")
async def list_sessions(request: Request, agent_id: str | None = None):
    """List sessions for the current user, optionally filtered by agent_id."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    sessions = SessionRepo.list_by_user(user["id"], agent_id=agent_id)
    return JSONResponse([
        {
            "id": s.id,
            "agent_id": s.agent_id,
            "title": s.title,
            "summary": s.summary,
            "message_count": s.message_count,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in sessions
    ])


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str, request: Request):
    """Get session details and recent messages."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    session = SessionRepo.get_by_id(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if session.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    messages = ConversationMessageRepo.get_recent(session_id, limit=50)
    return JSONResponse({
        "id": session.id,
        "agent_id": session.agent_id,
        "title": session.title,
        "summary": session.summary,
        "message_count": session.message_count,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    })


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str, request: Request):
    """End a session and generate an LLM summary."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    session = SessionRepo.get_by_id(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if session.user_id != user["id"]:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    # Load recent messages for summarization
    messages = ConversationMessageRepo.get_recent(session_id, limit=30)
    if not messages:
        return JSONResponse({"id": session_id, "summary": None, "status": "ended"})

    # Build conversation text for the summarizer
    convo_text = "\n".join([f"{m.role}: {m.content}" for m in messages])

    # Generate summary using Groq for speed
    try:
        summary_client = create_llm_client(provider="groq")
        summary = await summary_client.complete(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this tutoring session in 2-3 sentences. "
                        "Focus on what topics were covered, what the student understood, "
                        "and what they struggled with."
                    ),
                },
                {"role": "user", "content": convo_text},
            ],
            temperature=0.3,
            max_tokens=150,
        )
    except Exception as e:
        logger.warning("Failed to generate session summary via LLM: %s", e)
        summary = None

    if summary:
        SessionRepo.update_summary(session_id, summary)

    # Extract topic mastery signals (struggle heatmap)
    mastery_entries = []
    try:
        mastery_client = create_llm_client(provider="groq")
        mastery_response = await mastery_client.complete(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "From this tutoring conversation, identify concepts/topics discussed. "
                        "For each, classify the student's understanding. "
                        'Return a JSON array: [{"topic": "...", "chapter": "...", '
                        '"signal_type": "understood|struggled|unclear", "details": "..."}]. '
                        "Only include topics that were actually discussed. "
                        "Be specific about topic names. Return ONLY valid JSON, no markdown."
                    ),
                },
                {"role": "user", "content": convo_text},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", mastery_response.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "topic" in item and "signal_type" in item:
                    mastery_entries.append(TopicMasteryModel(
                        user_id=user["id"],
                        agent_id=session.agent_id,
                        session_id=session_id,
                        topic=item["topic"],
                        chapter=item.get("chapter"),
                        signal_type=item["signal_type"],
                        details=item.get("details"),
                    ))
        if mastery_entries:
            TopicMasteryRepo.save_batch(mastery_entries)
    except Exception as e:
        logger.warning("Failed to extract topic mastery: %s", e)

    # Also clean up in-memory pipeline if it exists
    pipeline = chat_sessions.pop(session_id, None)
    if pipeline and summary:
        try:
            pipeline.end_session(summary)
        except Exception:
            pass

    return JSONResponse({
        "id": session_id,
        "summary": summary,
        "mastery_count": len(mastery_entries),
        "status": "ended",
    })


@app.get("/api/logs")
async def get_logs(limit: int = 50, offset: int = 0):
    """Get recent LLM call logs with pagination."""
    logs = LLMCallLogRepo.get_recent(limit=min(limit, 200), offset=offset)
    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "session_id": log.session_id,
                "user_id": log.user_id,
                "user_message": log.user_message,
                "llm_provider": log.llm_provider,
                "llm_model": log.llm_model,
                "tool_calls": log.get_tool_calls(),
                "response_text": log.response_text,
                "latency_total_ms": log.latency_total_ms,
                "latency_llm_ms": log.latency_llm_ms,
                "latency_tool_ms": log.latency_tool_ms,
                "latency_stream_ms": log.latency_stream_ms,
                "tokens_in": log.tokens_in,
                "tokens_out": log.tokens_out,
                "error": log.error,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/logs/stats")
async def get_logs_stats():
    """Get aggregated LLM call stats."""
    stats = LLMCallLogRepo.get_stats()
    return JSONResponse(stats)


@app.get("/api/voice-logs")
async def get_voice_logs(limit: int = 50, offset: int = 0, mode: str | None = None):
    """Get recent voice pipeline logs with full stage latencies."""
    logs = VoicePipelineLogRepo.get_recent(limit=min(limit, 200), offset=offset, mode=mode)
    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "session_id": log.session_id,
                "user_id": log.user_id,
                "mode": log.mode,
                "user_message": log.user_message,
                "response_text": log.response_text,
                "llm_provider": log.llm_provider,
                "llm_model": log.llm_model,
                "latency_vad_ms": log.latency_vad_ms,
                "latency_stt_ms": log.latency_stt_ms,
                "latency_turn_detection_ms": log.latency_turn_detection_ms,
                "latency_stt_to_llm_ms": log.latency_stt_to_llm_ms,
                "latency_llm_ms": log.latency_llm_ms,
                "latency_llm_first_token_ms": log.latency_llm_first_token_ms,
                "latency_tool_ms": log.latency_tool_ms,
                "latency_tts_ms": log.latency_tts_ms,
                "latency_tts_first_chunk_ms": log.latency_tts_first_chunk_ms,
                "latency_total_ms": log.latency_total_ms,
                "tool_calls": log.get_tool_calls(),
                "tokens_in": log.tokens_in,
                "tokens_out": log.tokens_out,
                "tts_chunks_sent": log.tts_chunks_sent,
                "tts_interrupted": log.tts_interrupted,
                "smart_turn_used": log.smart_turn_used,
                "smart_turn_result": log.smart_turn_result,
                "error": log.error,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/voice-logs/stats")
async def get_voice_logs_stats(mode: str | None = None):
    """Get aggregated voice pipeline stats with per-stage averages."""
    stats = VoicePipelineLogRepo.get_stats(mode=mode)
    return JSONResponse(stats)


@app.post("/offer")
async def offer(request: Request):
    data: dict = await request.json()
    offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
    pc = RTCPeerConnection()
    pc_id = f"pc-{id(pc)}"
    user_id = data.get("user_id") or get_current_user_id(request)
    peer_user_ids[pc_id] = user_id
    canvas_mode = data.get("canvas_mode", False)
    peer_canvas_modes[pc_id] = canvas_mode
    logger.info("[%s] User ID: %s, Canvas Mode: %s", pc_id, user_id, canvas_mode)

    @pc.on("datachannel")
    def on_datachannel(channel):
        datachannels[pc_id] = channel

        @channel.on("message")
        def on_message(message):
            try:
                data = json.loads(message)
                # Handle stop_tts command from client
                if data.get("type") == "stop_tts":
                    logger.warning("[%s] Client requested TTS stop", pc_id)
                    tts_interrupt_flags[pc_id] = False
                    st = smart_turn_sessions.get(pc_id)
                    if st:
                        st._reset_turn()
            except:
                pass

        @channel.on("close")
        async def on_close():
            logger.info("[%s] DataChannel closed", pc_id)
            datachannels.pop(pc_id, None)
            voice_sessions.pop(pc_id, None)
            peer_user_ids.pop(pc_id, None)
            peer_canvas_modes.pop(pc_id, None)
            tts_interrupt_flags.pop(pc_id, None)
            t = turn_processing_tasks.pop(pc_id, None)
            if t and not t.done():
                t.cancel()
            st = smart_turn_sessions.pop(pc_id, None)
            if st:
                st.cleanup()


    pcs.add(pc)
    logger.info("[%s] created for incoming offer", pc_id)

    media_blackhole = MediaBlackhole()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("[%s] Connection: %s", pc_id, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
            datachannels.pop(pc_id, None)
            voice_sessions.pop(pc_id, None)
            peer_user_ids.pop(pc_id, None)
            peer_canvas_modes.pop(pc_id, None)
            tts_interrupt_flags.pop(pc_id, None)
            t = turn_processing_tasks.pop(pc_id, None)
            if t and not t.done():
                t.cancel()
            st = smart_turn_sessions.pop(pc_id, None)
            if st:
                st.cleanup()
            logger.info("[%s] Closed", pc_id)

    @pc.on("track")
    def on_track(track):
        logger.info("[%s] Track received: kind=%s id=%s", pc_id, track.kind, getattr(track, "id", "?"))
        if track.kind == "audio":
            task = asyncio.create_task(consume_audio_track(track, pc_id))

            @track.on("ended")
            def on_ended():
                logger.info("[%s] Track ended", pc_id)
                task.cancel()

        else:
            pc.addTrack(track)
            asyncio.ensure_future(media_blackhole.start())

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info("[%s] Answer created", pc_id)
    return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

@app.on_event("shutdown")
async def on_shutdown():
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()
    logger.info("Server shutdown, pcs closed")

if __name__ == "__main__":

    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)

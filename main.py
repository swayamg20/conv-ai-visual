import asyncio
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
import json
from typing import Any, Dict, Optional
import uvicorn
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.mediastreams import AudioFrame
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import logging
import websockets
import numpy as np
from aiortc.contrib.media import MediaBlackhole
from funcs.llm_pipeline import LLMPipeline
from funcs.tts_pipeline import TTSPipeline
from funcs.config import config
from funcs.auth import get_current_user_id
from funcs.smart_turn import SmartTurnAnalyzer, SmartTurnSession

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
    
    tts_pipeline = TTSPipeline(
        api_key=config.ELEVENLABS_API_KEY,
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL_ID,
        stability=config.TTS_STABILITY,
        similarity_boost=config.TTS_SIMILARITY_BOOST,
        style=config.TTS_STYLE,
        use_speaker_boost=config.TTS_USE_SPEAKER_BOOST
    )
    logger.info("TTS pipeline initialized successfully")

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
    logger.error(f"Failed to initialize pipelines: {e}")
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
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    canvas_mode: Optional[bool] = None

class CanvasModeRequest(BaseModel):
    enabled: bool
    custom_prompt: Optional[str] = None

chat_sessions: Dict[str, LLMPipeline] = {}
peer_canvas_modes: Dict[str, bool] = {}
pcs = set[Any]()
datachannels: Dict[str, Any] = {}
voice_sessions: Dict[str, LLMPipeline] = {}
peer_user_ids: Dict[str, str] = {}
tts_interrupt_flags: Dict[str, bool] = {}  # Simple interrupt flag: True = TTS active, False = stop
smart_turn_sessions: Dict[str, SmartTurnSession] = {}  # Per-peer Smart Turn state
turn_processing_tasks: Dict[str, asyncio.Task] = {}  # Per-peer active LLM+TTS task (cancel on new turn)

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
            ch = datachannels.get(pc_id)
            if ch and ch.readyState == "open":
                tool_name = data.get("tool", "")
                if tool_name == "create_teaching_sequence" and data.get("timeline"):
                    ch.send(json.dumps({
                        "type": "teaching_sequence",
                        "timeline": data["timeline"],
                    }))
                elif tool_name == "render_latex" and data.get("element"):
                    ch.send(json.dumps({
                        "type": "latex",
                        "element": data["element"],
                    }))
                elif tool_name == "animate_element" and data.get("animation_command"):
                    ch.send(json.dumps({
                        "type": "animation",
                        "tool": tool_name,
                        **data,
                    }))
                elif tool_name == "plot_function" and data.get("graph"):
                    ch.send(json.dumps({
                        "type": "animation",
                        "tool": tool_name,
                        **data,
                    }))

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
    await _cancel_active_turn(pc_id)
    task = asyncio.create_task(_run_llm_tts(pc_id, user_text))
    turn_processing_tasks[pc_id] = task


async def _run_llm_tts(pc_id: str, user_text: str):
    """
    Actual LLM → TTS → client pipeline. Runs as a cancellable task.
    """
    import base64

    ch = datachannels.get(pc_id)
    pipeline = await _ensure_voice_session(pc_id)

    try:
        logger.info("[%s] → LLM: '%s'", pc_id, user_text[:60])
        llm_response = ""
        async for chunk in pipeline.chat_with_tools_stream(
            user_text,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
        ):
            llm_response += chunk

        logger.info("[%s] ← LLM: '%s'", pc_id, llm_response[:50] + "...")

        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "llm_response", "text": llm_response}))

        # TTS streaming
        if tts_pipeline and llm_response.strip():
            logger.info("[%s] → TTS", pc_id)
            tts_interrupt_flags[pc_id] = True

            if ch and ch.readyState == "open":
                ch.send(json.dumps({"type": "tts_started"}))

            try:
                chunks_sent = 0
                async for audio_chunk in tts_pipeline.text_to_speech_stream(llm_response):
                    if not tts_interrupt_flags.get(pc_id, False):
                        logger.warning("[%s] 🛑 TTS interrupted after %d chunks", pc_id, chunks_sent)
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({"type": "tts_interrupted", "chunks_sent": chunks_sent}))
                        break

                    if ch and ch.readyState == "open":
                        if chunks_sent == 0:
                            logger.info("[%s] Sending first TTS chunk (%d bytes)", pc_id, len(audio_chunk))
                        ch.send(json.dumps({
                            "type": "tts_chunk",
                            "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                        }))
                        chunks_sent += 1
                else:
                    logger.info("[%s] ✓ TTS complete (%d chunks)", pc_id, chunks_sent)
                    if ch and ch.readyState == "open":
                        ch.send(json.dumps({"type": "tts_complete"}))
            finally:
                tts_interrupt_flags[pc_id] = False

    except asyncio.CancelledError:
        logger.info("[%s] Turn processing cancelled (new turn arrived)", pc_id)
        tts_interrupt_flags[pc_id] = False
        ch = datachannels.get(pc_id)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "tts_interrupted", "reason": "new_turn"}))
    except Exception as e:
        logger.exception("[%s] Pipeline error: %s", pc_id, e)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "error", "message": str(e)}))
    finally:
        turn_processing_tasks.pop(pc_id, None)


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
    if smart_turn_analyzer:
        st_session = SmartTurnSession(smart_turn_analyzer)
        smart_turn_sessions[pc_id] = st_session

        # Wire up fallback callback: when silence exceeds stop_secs, force-complete
        async def on_fallback_complete(text: str):
            await _process_user_turn(pc_id, text)

        st_session.set_turn_complete_callback(on_fallback_complete)
        logger.info("[%s] Smart Turn session created", pc_id)

    async def on_deepgram_event(data: Dict):
        """Handle Deepgram transcript events.

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

            ch = datachannels.get(pc_id)
            tts_was_active = tts_interrupt_flags.get(pc_id, False)

            if ch and ch.readyState == "open":
                # Interruption: stop TTS if user speaks during playback
                if transcript.strip() and tts_was_active:
                    logger.warning(
                        "[%s] 🛑 INTERRUPT - User speaking during TTS: '%s'",
                        pc_id, transcript[:30],
                    )
                    tts_interrupt_flags[pc_id] = False
                    # Reset Smart Turn so interrupt speech starts a fresh turn
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
                # Step 1: Accumulate finalized transcript segments
                if is_final and transcript.strip():
                    st_session.accumulate_transcript(transcript)

                # Step 2: Only run Smart Turn when Deepgram detects an actual pause
                if speech_final and st_session.accumulated_transcript.strip():
                    logger.info("[%s] Speech final → Smart Turn (text='%s')",
                                pc_id, st_session.accumulated_transcript[:60])

                    is_complete, accumulated_text = await st_session.on_speech_final("")

                    if ch and ch.readyState == "open":
                        ch.send(json.dumps({
                            "type": "smart_turn",
                            "is_complete": is_complete,
                        }))

                    if is_complete and accumulated_text:
                        await _process_user_turn(pc_id, accumulated_text)

            else:
                # ── Legacy path (no Smart Turn) ──────────────────
                if (is_final or speech_final) and transcript.strip():
                    logger.info("[%s] Final: '%s'", pc_id, transcript)
                    await _process_user_turn(pc_id, transcript)

    dg_stream_task: Optional[asyncio.Task] = None

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

        from collections.abc import Sequence
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
    """
    import uuid
    session_id = chat_msg.session_id or str(uuid.uuid4())
    # user_id from middleware (auth) or request body
    user_id = chat_msg.user_id or get_current_user_id(request)
    
    # Queues for events during this request
    canvas_events = []
    animation_events = []

    # Get or create session pipeline with memory and tools
    if session_id not in chat_sessions:
        try:
            pipeline = LLMPipeline(
                provider=config.LLM_PROVIDER,
                api_key=None,  # Factory will get from config based on provider
                model=None,    # Factory will get from config based on provider
                system_prompt=config.LLM_SYSTEM_PROMPT,
                max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                user_id=user_id,
                session_id=session_id,
                enable_memory=True,
                canvas_mode=True,  # Always enabled for math tutor
                canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT
            )
            pipeline.load_tools_from_db()
            chat_sessions[session_id] = pipeline
            logger.info("Created chat session %s with %d tools (canvas_mode=%s)", session_id, len(pipeline.get_tools_schema()), pipeline.canvas_mode)
        except Exception as e:
            logger.exception("Failed to create chat session: %s", e)
            return JSONResponse({"error": "Failed to initialize chat"}, status_code=500)
    
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

            # Save observability log
            try:
                from funcs.models import LLMCallLogRepo
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
            except Exception as log_err:
                logger.warning("Failed to save LLM call log: %s", log_err)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.exception("Chat stream error: %s", e)
            try:
                from funcs.models import LLMCallLogRepo
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


@app.get("/api/logs")
async def get_logs(limit: int = 50, offset: int = 0):
    """Get recent LLM call logs with pagination."""
    from funcs.models import LLMCallLogRepo
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
    from funcs.models import LLMCallLogRepo
    stats = LLMCallLogRepo.get_stats()
    return JSONResponse(stats)


@app.post("/offer")
async def offer(request: Request):
    data: Dict = await request.json()
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

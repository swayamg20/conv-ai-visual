import asyncio
from types import NoneType
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
from funcs.vad_gate import SileroVADGate
from funcs.endpoint_detector import get_endpoint_detector, EndpointDetector
from funcs.sentence_streamer import SentenceStreamingPipeline
import time
import base64

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
    
except Exception as e:
    logger.error(f"Failed to initialize pipelines: {e}")
    llm_pipeline = None
    tts_pipeline = None

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
    canvas_mode: Optional[bool] = NoneType

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

async def consume_audio_track(track: MediaStreamTrack, pc_id: str):
    """
    Main pipeline: Audio → VAD + STT → Endpoint Detection → LLM → TTS

    Uses Silero VAD for fast endpoint detection (~300ms) instead of
    Deepgram's slow endpointing (1800ms default).
    """
    logger.info("[%s] Audio consumer started", pc_id)

    audio_q: "asyncio.Queue[bytes]" = asyncio.Queue()
    sample_rate = None
    channel_count = None

    # VAD endpoint detection (initialized after we know sample rate)
    vad_gate: Optional[SileroVADGate] = None
    endpoint_detector: Optional[EndpointDetector] = None

    # Track processing state to prevent double-processing
    processing_state = {
        "is_processing": False,  # True while LLM+TTS is running
        "last_processed_transcript": "",  # Prevent duplicate processing
        "processing_start_time": None,  # When current processing started
    }

    # Latency tracking
    latency_metrics = {
        "speech_end_time": None,
        "llm_start_time": None,
        "llm_first_token_time": None,
        "tts_start_time": None,
        "tts_first_chunk_time": None,
    }

    async def process_transcript_to_llm_tts(transcript: str, source: str = "vad"):
        """
        Process transcript through LLM and TTS pipeline.
        Extracted to handle both VAD-triggered and Deepgram-fallback endpoints.
        """
        nonlocal latency_metrics, processing_state
        ch = datachannels.get(pc_id)

        # Prevent double-processing of same transcript
        transcript_normalized = transcript.strip().lower()
        if transcript_normalized == processing_state["last_processed_transcript"]:
            logger.debug("[%s] Skipping duplicate transcript: '%s'", pc_id, transcript[:30])
            return

        # Prevent concurrent processing
        if processing_state["is_processing"]:
            logger.debug("[%s] Already processing, skipping: '%s' (source=%s)", pc_id, transcript[:30], source)
            return

        # Mark as processing
        processing_state["is_processing"] = True
        processing_state["last_processed_transcript"] = transcript_normalized
        processing_state["processing_start_time"] = time.time()

        # Record timing
        latency_metrics["llm_start_time"] = time.time()
        if latency_metrics["speech_end_time"]:
            endpoint_latency = (latency_metrics["llm_start_time"] - latency_metrics["speech_end_time"]) * 1000
            logger.info("[%s] [LATENCY] endpoint_to_llm: %.0fms (source=%s)", pc_id, endpoint_latency, source)

        # Get or create voice session
        if pc_id not in voice_sessions:
            user_id = peer_user_ids.get(pc_id, "default_user")
            canvas_mode = peer_canvas_modes.get(pc_id, False)
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
                canvas_system_prompt=config.LLM_CANVAS_SYSTEM_PROMPT
            )
            voice_pipeline.load_tools_from_db()

            # Canvas callback
            async def canvas_broadcast(data, tool_type="canvas"):
                ch = datachannels.get(pc_id)
                if ch and ch.readyState == "open":
                    if tool_type == "manim":
                        # Send each Manim command individually for streaming
                        for cmd in data:
                            ch.send(json.dumps({
                                "type": "manim_command",
                                "command": cmd
                            }))
                    else:
                        ch.send(json.dumps({
                            "type": "canvas_update",
                            "operations": data
                        }))

            voice_pipeline.set_canvas_callback(canvas_broadcast)
            voice_sessions[pc_id] = voice_pipeline
            logger.info("[%s] Session created (%d tools)", pc_id, len(voice_pipeline.get_tools_schema()))

        try:
            # Mark TTS as active early (for interruption detection during LLM)
            tts_interrupt_flags[pc_id] = True

            # Notify client TTS started
            if ch and ch.readyState == "open":
                ch.send(json.dumps({"type": "tts_started"}))

            def check_interrupt() -> bool:
                """Check if TTS should be interrupted."""
                return not tts_interrupt_flags.get(pc_id, False)

            # Use sentence-level streaming if enabled (for lower latency)
            if config.SENTENCE_STREAM_ENABLED and tts_pipeline:
                logger.info("[%s] → LLM + TTS (sentence streaming, source=%s)", pc_id, source)

                # Create LLM stream
                llm_stream = voice_sessions[pc_id].chat_with_tools_stream(
                    transcript,
                    temperature=config.LLM_TEMPERATURE,
                    max_tokens=config.LLM_MAX_TOKENS
                )

                # Create sentence streaming pipeline
                pipeline = SentenceStreamingPipeline(
                    tts_func=tts_pipeline.text_to_speech_stream,
                    min_sentence_chars=config.MIN_SENTENCE_CHARS
                )

                chunks_sent = 0
                first_chunk = True

                try:
                    async for audio_chunk in pipeline.stream(llm_stream, check_interrupt):
                        if check_interrupt():
                            logger.warning("[%s] 🛑 TTS interrupted after %d chunks", pc_id, chunks_sent)
                            if ch and ch.readyState == "open":
                                ch.send(json.dumps({
                                    "type": "tts_interrupted",
                                    "chunks_sent": chunks_sent
                                }))
                            break

                        # Send audio chunk to client
                        if ch and ch.readyState == "open":
                            if first_chunk:
                                latency_metrics["tts_first_chunk_time"] = time.time()
                                total_latency = (latency_metrics["tts_first_chunk_time"] - latency_metrics["speech_end_time"]) * 1000 if latency_metrics["speech_end_time"] else 0
                                logger.info("[%s] [LATENCY] total_to_first_audio: %.0fms (sentence streaming)", pc_id, total_latency)
                                first_chunk = False

                            ch.send(json.dumps({
                                "type": "tts_chunk",
                                "audio": base64.b64encode(audio_chunk).decode('utf-8')
                            }))
                            chunks_sent += 1

                    else:
                        # TTS completed without interruption
                        logger.info("[%s] ✓ TTS complete (%d chunks, sentence streaming)", pc_id, chunks_sent)
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({"type": "tts_complete"}))

                    # Get full response and send to client
                    llm_response = pipeline.get_full_response()
                    logger.info("[%s] ← LLM: '%s'", pc_id, llm_response[:50] + "...")
                    if ch and ch.readyState == "open":
                        ch.send(json.dumps({
                            "type": "llm_response",
                            "text": llm_response
                        }))

                finally:
                    tts_interrupt_flags[pc_id] = False

            else:
                # Fallback: Sequential LLM → TTS (original behavior)
                logger.info("[%s] → LLM (sequential, source=%s)", pc_id, source)
                llm_response = ""
                first_token = True

                async for chunk in voice_sessions[pc_id].chat_with_tools_stream(
                    transcript,
                    temperature=config.LLM_TEMPERATURE,
                    max_tokens=config.LLM_MAX_TOKENS
                ):
                    if first_token:
                        latency_metrics["llm_first_token_time"] = time.time()
                        ttft = (latency_metrics["llm_first_token_time"] - latency_metrics["llm_start_time"]) * 1000
                        logger.info("[%s] [LATENCY] llm_ttft: %.0fms", pc_id, ttft)
                        first_token = False
                    llm_response += chunk

                logger.info("[%s] ← LLM: '%s'", pc_id, llm_response[:50] + "...")

                # Send LLM response to client
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({
                        "type": "llm_response",
                        "text": llm_response
                    }))

                # Generate and stream TTS
                if tts_pipeline and llm_response.strip():
                    latency_metrics["tts_start_time"] = time.time()
                    logger.info("[%s] → TTS (sequential)", pc_id)

                    try:
                        chunks_sent = 0
                        async for audio_chunk in tts_pipeline.text_to_speech_stream(llm_response):
                            if check_interrupt():
                                logger.warning("[%s] 🛑 TTS interrupted after %d chunks", pc_id, chunks_sent)
                                if ch and ch.readyState == "open":
                                    ch.send(json.dumps({
                                        "type": "tts_interrupted",
                                        "chunks_sent": chunks_sent
                                    }))
                                break

                            if ch and ch.readyState == "open":
                                if chunks_sent == 0:
                                    latency_metrics["tts_first_chunk_time"] = time.time()
                                    tts_latency = (latency_metrics["tts_first_chunk_time"] - latency_metrics["tts_start_time"]) * 1000
                                    total_latency = (latency_metrics["tts_first_chunk_time"] - latency_metrics["speech_end_time"]) * 1000 if latency_metrics["speech_end_time"] else 0
                                    logger.info("[%s] [LATENCY] tts_first_chunk: %.0fms, total: %.0fms (sequential)", pc_id, tts_latency, total_latency)

                                ch.send(json.dumps({
                                    "type": "tts_chunk",
                                    "audio": base64.b64encode(audio_chunk).decode('utf-8')
                                }))
                                chunks_sent += 1

                        else:
                            logger.info("[%s] ✓ TTS complete (%d chunks, sequential)", pc_id, chunks_sent)
                            if ch and ch.readyState == "open":
                                ch.send(json.dumps({"type": "tts_complete"}))

                    finally:
                        tts_interrupt_flags[pc_id] = False
                else:
                    tts_interrupt_flags[pc_id] = False

        except Exception as e:
            logger.exception("[%s] Pipeline error: %s", pc_id, e)
            tts_interrupt_flags[pc_id] = False
            if ch and ch.readyState == "open":
                ch.send(json.dumps({
                    "type": "error",
                    "message": str(e)
                }))
        finally:
            # Always clear processing state when done
            processing_state["is_processing"] = False

    async def on_deepgram_event(data: Dict):
        """
        Handle Deepgram events.

        With VAD endpoint detection enabled:
        - Interim transcripts → feed to endpoint detector
        - Final transcripts → use as fallback if VAD didn't trigger
        """
        nonlocal latency_metrics, processing_state
        event_type = data.get("type")

        if event_type in ("Results", "results"):
            channel_obj = data.get("channel", {})
            alts = channel_obj.get("alternatives", [])
            if not alts:
                return

            transcript = alts[0].get("transcript", "")
            is_final = data.get("is_final", False) or data.get("speech_final", False)

            ch = datachannels.get(pc_id)
            if ch and ch.readyState == "open":
                # INTERRUPTION: Only interrupt if this is NEW speech, not the same transcript we're processing
                if transcript.strip() and tts_interrupt_flags.get(pc_id, False):
                    transcript_normalized = transcript.strip().lower()
                    # Check if this is different from what triggered current processing
                    is_same_transcript = transcript_normalized == processing_state["last_processed_transcript"]
                    # Also check if enough time has passed (>500ms) to consider it new speech
                    time_since_processing = 0
                    if processing_state["processing_start_time"]:
                        time_since_processing = (time.time() - processing_state["processing_start_time"]) * 1000

                    if not is_same_transcript and time_since_processing > 500:
                        logger.warning("[%s] 🛑 INTERRUPT - New speech during TTS: '%s' (final=%s, time=%.0fms)",
                                       pc_id, transcript[:30], is_final, time_since_processing)
                        tts_interrupt_flags[pc_id] = False  # Signal TTS to stop immediately
                    else:
                        logger.debug("[%s] Ignoring transcript during TTS (same=%s, time=%.0fms): '%s'",
                                     pc_id, is_same_transcript, time_since_processing, transcript[:30])

                # Send all transcripts to client (interim + final)
                ch.send(json.dumps({
                    "type": "transcript",
                    "text": transcript,
                    "is_final": is_final
                }))

            # Feed transcript to endpoint detector (if VAD enabled)
            if config.VAD_ENDPOINT_ENABLED and endpoint_detector and transcript.strip():
                endpoint_detector.process_interim_transcript(pc_id, transcript, is_final)

            # Process final transcripts as fallback (when VAD is disabled or didn't trigger)
            if is_final and transcript.strip():
                logger.info("[%s] Deepgram final: '%s'", pc_id, transcript)

                # Skip if we're already processing this transcript
                if processing_state["is_processing"]:
                    logger.debug("[%s] Skipping Deepgram final - already processing", pc_id)
                    return

                if config.VAD_ENDPOINT_ENABLED and endpoint_detector:
                    # Check if VAD already processed this (pending transcript should be empty)
                    state = endpoint_detector.get_state(pc_id)
                    if state and state.pending_transcript.strip():
                        # VAD didn't trigger yet - use Deepgram's final as fallback
                        logger.info("[%s] Using Deepgram final as fallback (VAD didn't trigger)", pc_id)
                        triggered = endpoint_detector.force_endpoint(pc_id)
                        if triggered:
                            latency_metrics["speech_end_time"] = time.time()
                            await process_transcript_to_llm_tts(triggered, source="deepgram_fallback")
                else:
                    # VAD disabled - use Deepgram directly
                    latency_metrics["speech_end_time"] = time.time()
                    await process_transcript_to_llm_tts(transcript, source="deepgram")

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

        # Initialize VAD endpoint detection (if enabled)
        if config.VAD_ENDPOINT_ENABLED:
            vad_gate = SileroVADGate(
                sample_rate=sample_rate,
                channels=channel_count,
                threshold=config.VAD_THRESHOLD
            )
            endpoint_detector = get_endpoint_detector()
            endpoint_detector.create_state(pc_id)
            logger.info("[%s] VAD endpoint detection enabled (threshold=%.2f, silence=%dms)",
                        pc_id, config.VAD_THRESHOLD, config.VAD_SILENCE_THRESHOLD_MS)

        # Connect to Deepgram
        # With VAD enabled, Deepgram's endpointing serves as backup only
        # - endpointing: Reduced to 500ms (was 1800ms) since VAD handles primary detection
        # - utterance_end_ms: Reduced to 300ms for faster interim results
        # - interim_results=true: Critical for VAD to have transcripts to work with
        base_url = "wss://api.deepgram.com/v1/listen"
        websocket_url = f"{base_url}?model={config.DEEPGRAM_MODEL}&encoding=linear16&sample_rate={sample_rate}&channels={channel_count}&interim_results=true&endpointing={config.DEEPGRAM_ENDPOINTING}&smart_format=false&punctuate=false&diarize=false"

        # Callback when Deepgram connects - notify client they can start speaking
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
                on_connected=on_deepgram_connected
            )
        )

        # Send first frame (also to VAD)
        first_pcm = audioframe_to_pcm16_bytes(first_frame)
        await audio_q.put(first_pcm)

        # Process first frame through VAD if enabled
        if config.VAD_ENDPOINT_ENABLED and vad_gate and endpoint_detector:
            is_speech, vad_latency = vad_gate.should_send(first_pcm)
            endpoint_detector.process_vad_result(pc_id, is_speech, vad_latency)

        # Stream remaining frames with parallel VAD processing
        while True:
            frame = await track.recv()
            pcm_bytes = audioframe_to_pcm16_bytes(frame)

            # Feed to VAD in parallel with Deepgram (if enabled)
            if config.VAD_ENDPOINT_ENABLED and vad_gate and endpoint_detector:
                is_speech, vad_latency = vad_gate.should_send(pcm_bytes)

                # Check for endpoint detection
                triggered_transcript = endpoint_detector.process_vad_result(pc_id, is_speech, vad_latency)

                if triggered_transcript:
                    # VAD detected endpoint - process immediately
                    latency_metrics["speech_end_time"] = time.time()
                    logger.info("[%s] [LATENCY] VAD endpoint detected", pc_id)

                    # Process in background to not block audio loop
                    asyncio.create_task(
                        process_transcript_to_llm_tts(triggered_transcript, source="vad")
                    )

            # Always send to Deepgram for transcription
            await audio_q.put(pcm_bytes)

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

        # Clean up VAD endpoint detector state
        if config.VAD_ENDPOINT_ENABLED and endpoint_detector:
            endpoint_detector.cleanup_state(pc_id)

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
    
    # Queue for canvas events during this request
    canvas_events = []
    
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
                canvas_mode=chat_msg.canvas_mode or False,
                canvas_system_prompt=config.LLM_CANVAS_SYSTEM_PROMPT
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
    def canvas_callback(data, tool_type="canvas"):
        canvas_events.append({"data": data, "tool_type": tool_type})
    pipeline.set_canvas_callback(canvas_callback)
    
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
                    event = canvas_events.pop(0)
                    if event.get("tool_type") == "manim":
                        for cmd in event["data"]:
                            yield f"data: {json.dumps({'type': 'manim_command', 'command': cmd})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'canvas_update', 'operations': event['data']})}\n\n"

                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            # Send any remaining canvas events
            while canvas_events:
                event = canvas_events.pop(0)
                if event.get("tool_type") == "manim":
                    for cmd in event["data"]:
                        yield f"data: {json.dumps({'type': 'manim_command', 'command': cmd})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'canvas_update', 'operations': event['data']})}\n\n"
            
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            
        except Exception as e:
            logger.exception("Chat stream error: %s", e)
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
                    tts_interrupt_flags[pc_id] = False  # Clear flag to stop streaming
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

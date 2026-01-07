import asyncio
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
import json
from typing import Any, Dict, Optional
import os
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
from funcs.vad_gate import SileroVADGate
from funcs.llm_pipeline import LLMPipeline
from funcs.tts_pipeline import TTSPipeline
from funcs.config import config
from funcs.auth import get_current_user_id
from funcs.interruption import interruption_manager

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-deepgram")

try:
    config.validate()
        
    llm_pipeline = LLMPipeline(
        api_key=config.OPENAI_API_KEY,
        model=config.OPENAI_MODEL,
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
    user_id: Optional[str] = None  # Persistent user identifier for memory
    canvas_mode: Optional[bool] = None  # Enable canvas visual mode for this message

# Per-session LLM pipelines (each has its own memory context)
chat_sessions: Dict[str, LLMPipeline] = {}

# Per-peer canvas mode state for WebRTC voice sessions
peer_canvas_modes: Dict[str, bool] = {}

DEEPGRAM_KEY = "your-key"
pcs = set[Any]()
datachannels: Dict[str, Any] = {}

# Per-peer LLM pipelines for voice (WebRTC)
voice_sessions: Dict[str, LLMPipeline] = {}

# Store VAD state per peer connection (tracks if speech was detected)
vad_speech_detected: Dict[str, bool] = {}

# Store user_id per peer connection (for memory)
peer_user_ids: Dict[str, str] = {}


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
    sample_rate: int,
    channel_count: int,
    audio_queue: "asyncio.Queue[bytes]",
    results_callback,
    keepalive_interval: float = 5.0,
):
    """
    """
    headers = {"Authorization": f"Token {auth_key}"}
    logger.info("Connecting to Deepgram at %s", websocket_url)
    try:
        async with websockets.connect(
            websocket_url,
            additional_headers=headers,
            max_size=None,
        ) as ws:
            logger.info("Deepgram WS connected")
            async def on_message(msg):
                data = json.loads(msg)
                # print(data)
                await results_callback(data)
                
            ws.onmessage = on_message

            async def sender():
                try:
                    while True:
                        pcm_bytes = await audio_queue.get()
                        if pcm_bytes is None:
                            logger.debug("sender got sentinel -> sending Finalize")
                            await ws.send(json.dumps({"type": "Finalize"}))
                            return
                        await ws.send(pcm_bytes)
                except asyncio.CancelledError:
                    logger.info("sender cancelled")
                    raise
                except Exception as e:
                    logger.exception("sender error: %s", e)
                    raise

            async def keepalive():
                try:
                    while True:
                        await asyncio.sleep(keepalive_interval)
                        try:
                            await ws.send(json.dumps({"type": "KeepAlive"}))
                        except Exception:
                            return
                except asyncio.CancelledError:
                    return

            send_task = asyncio.create_task(sender())
            keep_task = asyncio.create_task(keepalive())

            try:
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        await results_callback(data)
                    except json.JSONDecodeError:
                        logger.debug("Received non-json message (binary?) of length %d", len(msg))
            except websockets.ConnectionClosed as e:
                logger.info("Deepgram WS closed: %s", e)
            finally:
                send_task.cancel()
                keep_task.cancel()
                try:
                    await audio_queue.put(None)
                except Exception:
                    pass

    except Exception as e:
        logger.exception("Failed to connect or stream to Deepgram: %s", e)

async def consume_audio_track(track: MediaStreamTrack, pc_id: str):
    """
    Read frames from incoming audio track, forward PCM bytes to Deepgram via WS,
    and print transcription JSON results as they arrive.
    """
    logger.info("[%s] Started audio consumer for track id=%s", pc_id, getattr(track, "id", "?"))

    audio_q: "asyncio.Queue[bytes]" = asyncio.Queue()
    sample_rate = None
    channel_count = None
    base_listen = "wss://api.deepgram.com/v1/listen"
    websocket_url_template = f"{base_listen}?model={config.DEEPGRAM_MODEL}&encoding=linear16&interim_results=true&vad_events=true"

    async def on_deepgram_event(data: Dict):
        t = data.get("type")
        if t == "Results" or t == "results":
            channel_obj = data.get("channel", {})
            alts = channel_obj.get("alternatives", [])
            if alts:
                best = alts[0]
                transcript = best.get("transcript", "")
                is_final = data.get("is_final", False) or data.get("speech_final", False)
                
                ch = datachannels.get(pc_id)
                if ch and ch.readyState == "open":
                    # Send transcript to client
                    payload = json.dumps({"type": "transcript", "text": transcript, "is_final": is_final})
                    try:
                        ch.send(payload)
                    except Exception:
                        logger.exception("[%s] failed to send transcript over datachannel", pc_id)

                    # Check for interruption: user speaking while TTS is active
                    vad_detected = vad_speech_detected.get(pc_id, False)
                    state = interruption_manager.get_state(pc_id)
                    tts_active = state.tts_active if state else False

                    if interruption_manager.handle_interruption(
                        pc_id, vad_detected, tts_active, transcript
                    ):
                        if ch and ch.readyState == "open":
                            interrupt_ack = json.dumps({
                                "type": "interruption_ack",
                                "message": "Stopping response, listening to you"
                            })
                            ch.send(interrupt_ack)
                        await asyncio.sleep(0.05)
                    
                    if is_final and transcript.strip() and llm_pipeline:
                        if not vad_detected:
                            logger.info("[%s] Skipping LLM - VAD did not detect speech: %s", pc_id, transcript)
                            vad_speech_detected[pc_id] = False
                            return
                        
                        logger.info("[%s] Final transcript (VAD confirmed): %s", pc_id, transcript)
                        # Reset VAD state for next utterance
                        vad_speech_detected[pc_id] = False
                        try:
                            # Get or create voice session pipeline with tools
                            # user_id and canvas_mode from peer state (set during /offer)
                            if pc_id not in voice_sessions:
                                user_id = peer_user_ids.get(pc_id, "default_user")
                                canvas_mode = peer_canvas_modes.get(pc_id, False)
                                voice_pipeline = LLMPipeline(
                                    api_key=config.OPENAI_API_KEY,
                                    model=config.OPENAI_MODEL,
                                    system_prompt=config.LLM_SYSTEM_PROMPT,
                                    max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                                    user_id=user_id,
                                    session_id=pc_id,
                                    enable_memory=True,
                                    canvas_mode=canvas_mode,
                                    canvas_system_prompt=config.LLM_CANVAS_SYSTEM_PROMPT
                                )
                                voice_pipeline.load_tools_from_db()                                
                                async def canvas_broadcast(operations):
                                    ch = datachannels.get(pc_id)
                                    if ch and ch.readyState == "open":
                                        payload = json.dumps({
                                            "type": "canvas_update",
                                            "operations": operations
                                        })
                                        ch.send(payload)
                                        logger.info("[%s] Canvas broadcast: %d ops", pc_id, len(operations))
                                
                                voice_pipeline.set_canvas_callback(canvas_broadcast)
                                voice_sessions[pc_id] = voice_pipeline
                                logger.info("[%s] Voice session created with %d tools (canvas_mode=%s)", pc_id, len(voice_pipeline.get_tools_schema()), canvas_mode)
                            
                            # Process through LLM with tools support
                            llm_response = await voice_sessions[pc_id].chat_with_tools(
                                transcript,
                                temperature=config.LLM_TEMPERATURE,
                                max_tokens=config.LLM_MAX_TOKENS
                            )
                            
                            logger.info("[%s] LLM response: %s", pc_id, llm_response)
                            
                            # Send LLM response text to client
                            llm_payload = json.dumps({
                                "type": "llm_response",
                                "text": llm_response
                            })
                            ch.send(llm_payload)
                            
                            # Generate TTS audio if TTS pipeline is available
                            if tts_pipeline:
                                try:
                                    # Use interruption manager to stream TTS with cancellation support
                                    await interruption_manager.stream_tts_with_interruption(
                                        peer_id=pc_id,
                                        tts_generator=tts_pipeline.text_to_speech_stream(llm_response),
                                        datachannel=ch
                                    )
                                except Exception as tts_error:
                                    logger.exception("[%s] Failed to generate TTS: %s", pc_id, tts_error)
                            
                        except Exception as e:
                            logger.exception("[%s] Failed to process LLM: %s", pc_id, e)
                            error_payload = json.dumps({
                                "type": "error",
                                "message": "Failed to process with LLM"
                            })
                            try:
                                ch.send(error_payload)
                            except Exception:
                                pass
        else:
            logger.debug("[%s] Deepgram event: %s", pc_id, data)

    dg_stream_task: Optional[asyncio.Task] = None

    try:
        first_frame: AudioFrame = await track.recv()
        sample_rate = getattr(first_frame, "sample_rate", 48000)
        samples = getattr(first_frame, "samples", None)

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

        logger.info("[%s] first frame: sample_rate=%s channels=%s samples=%s", pc_id, sample_rate, channel_count, samples)

        try:
            vad_gate = SileroVADGate(sample_rate=sample_rate, channels=channel_count)
            # logger.info(
            #     "[%s] Silero VAD gate init: sr=%s ch=%s thr=%.3f",
            #     pc_id,
            #     sample_rate,
            #     channel_count,
            #     vad_gate.threshold,
            # )
        except Exception as e:
            logger.exception("[%s] Failed to initialize Silero VAD gate, bypassing VAD: %s", pc_id, e)
            vad_gate = None


        websocket_url = f"{websocket_url_template}&sample_rate={sample_rate}&channels={channel_count}"

        dg_stream_task = asyncio.create_task(
            deepgram_stream_ws_send_and_recv(
                websocket_url,
                config.DEEPGRAM_KEY,
                sample_rate,
                channel_count,
                audio_q,
                on_deepgram_event,
            )
        )

        # Initialize VAD state for this peer
        vad_speech_detected[pc_id] = False
        
        pcm_bytes = audioframe_to_pcm16_bytes(first_frame)
        # Run VAD and track if speech is detected
        if vad_gate is not None:
            speech_detected = vad_gate.should_send(pcm_bytes)
            if speech_detected:
                vad_speech_detected[pc_id] = True
                logger.info("[%s] VAD: Speech detected", pc_id)
        await audio_q.put(pcm_bytes)

        while True:
            frame = await track.recv()
            pcm_bytes = audioframe_to_pcm16_bytes(frame)
            if vad_gate is not None:
                speech_detected = vad_gate.should_send(pcm_bytes)
                if speech_detected and not vad_speech_detected[pc_id]:
                    vad_speech_detected[pc_id] = True
                    logger.info("[%s] VAD: Speech detected", pc_id)
            await audio_q.put(pcm_bytes)
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        logger.info("[%s] consume_audio_track cancelled", pc_id)
        raise
    except Exception as e:
        logger.exception("[%s] consume_audio_track stopped: %s", pc_id, e)
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
        
        # Cleanup VAD state
        vad_speech_detected.pop(pc_id, None)

        logger.info("[%s] consumer finished and Deepgram stream closed", pc_id)

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
                api_key=config.OPENAI_API_KEY,
                model=config.OPENAI_MODEL,
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
    def canvas_callback(operations):
        canvas_events.append(operations)
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
                    ops = canvas_events.pop(0)
                    yield f"data: {json.dumps({'type': 'canvas_update', 'operations': ops})}\n\n"
                
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
            
            # Send any remaining canvas events
            while canvas_events:
                ops = canvas_events.pop(0)
                yield f"data: {json.dumps({'type': 'canvas_update', 'operations': ops})}\n\n"
            
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


class CanvasModeRequest(BaseModel):
    enabled: bool
    custom_prompt: Optional[str] = None


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
    
    # Capture user_id and canvas_mode for this peer connection
    user_id = data.get("user_id") or get_current_user_id(request)
    peer_user_ids[pc_id] = user_id
    canvas_mode = data.get("canvas_mode", False)
    peer_canvas_modes[pc_id] = canvas_mode

    # Initialize interruption state for this peer connection
    interruption_manager.create_state(pc_id)

    logger.info("[%s] User ID: %s, Canvas Mode: %s", pc_id, user_id, canvas_mode)
    @pc.on("datachannel")
    def on_datachannel(channel):
        logger.info("[%s] DataChannel received: label=%s", pc_id, channel.label)
        datachannels[pc_id] = channel
        @channel.on("message")
        def on_message(message):
            logger.info("[%s] message from client: %s", pc_id, message)
            try:
                channel.send(f"echo: {message}")
            except Exception:
                pass
        @channel.on("close")
        async def on_close():
            logger.info("[%s] datachannel closed", pc_id)
            datachannels.pop(pc_id, None)
            
            # Save voice session to episodic memory before cleanup
            voice_pipeline = voice_sessions.pop(pc_id, None)
            if voice_pipeline:
                try:
                    summary = await voice_pipeline.generate_session_summary()
                    if summary:
                        voice_pipeline.end_session(summary)
                        logger.info("[%s] Voice session saved to episodic memory", pc_id)
                except Exception as e:
                    logger.warning("[%s] Failed to save voice session summary: %s", pc_id, e)
            
            vad_speech_detected.pop(pc_id, None)
            peer_user_ids.pop(pc_id, None)
            peer_canvas_modes.pop(pc_id, None)

            # Cleanup interruption state and stop any active TTS
            interruption_manager.cleanup_state(pc_id)


    pcs.add(pc)
    logger.info("[%s] created for incoming offer", pc_id)

    media_blackhole = MediaBlackhole()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("[%s] Connection state => %s", pc_id, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
            datachannels.pop(pc_id, None)
            
            # Save voice session to episodic memory before cleanup
            voice_pipeline = voice_sessions.pop(pc_id, None)
            if voice_pipeline:
                try:
                    summary = await voice_pipeline.generate_session_summary()
                    if summary:
                        voice_pipeline.end_session(summary)
                        logger.info("[%s] Voice session saved to episodic memory", pc_id)
                except Exception as e:
                    logger.warning("[%s] Failed to save voice session summary: %s", pc_id, e)
            
            vad_speech_detected.pop(pc_id, None)
            peer_user_ids.pop(pc_id, None)
            peer_canvas_modes.pop(pc_id, None)

            # Cleanup interruption state and stop any active TTS
            interruption_manager.cleanup_state(pc_id)

            logger.info("[%s] closed and removed", pc_id)

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

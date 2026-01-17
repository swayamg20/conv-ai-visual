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
    Main pipeline: Audio → STT → LLM → TTS
    Simple and clean - no VAD, just final transcripts
    """
    logger.info("[%s] Audio consumer started", pc_id)

    audio_q: "asyncio.Queue[bytes]" = asyncio.Queue()
    sample_rate = None
    channel_count = None

    async def on_deepgram_event(data: Dict):
        """Handle Deepgram events - only care about final transcripts"""
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
                # Send all transcripts to client (interim + final)
                ch.send(json.dumps({
                    "type": "transcript",
                    "text": transcript,
                    "is_final": is_final
                }))

                # INTERRUPT DETECTION: If we get a transcript while TTS is active, stop it
                if transcript.strip() and tts_interrupt_flags.get(pc_id, False):
                    logger.warning("[%s] 🛑 User speaking during TTS - interrupting", pc_id)
                    tts_interrupt_flags[pc_id] = False  # Stop TTS

            # Process final transcripts through LLM
            if is_final and transcript.strip():
                logger.info("[%s] Final: '%s'", pc_id, transcript)

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
                    async def canvas_broadcast(operations):
                        ch = datachannels.get(pc_id)
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({
                                "type": "canvas_update",
                                "operations": operations
                            }))

                    voice_pipeline.set_canvas_callback(canvas_broadcast)
                    voice_sessions[pc_id] = voice_pipeline
                    logger.info("[%s] Session created (%d tools)", pc_id, len(voice_pipeline.get_tools_schema()))

                try:
                    # Call LLM
                    logger.info("[%s] → LLM", pc_id)
                    llm_response = ""
                    async for chunk in voice_sessions[pc_id].chat_with_tools_stream(
                        transcript,
                        temperature=config.LLM_TEMPERATURE,
                        max_tokens=config.LLM_MAX_TOKENS
                    ):
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
                        logger.info("[%s] → TTS", pc_id)

                        # Mark TTS as active (for interruption detection)
                        tts_interrupt_flags[pc_id] = True

                        # Notify client TTS started
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({"type": "tts_started"}))

                        try:
                            import base64
                            chunks_sent = 0
                            async for audio_chunk in tts_pipeline.text_to_speech_stream(llm_response):
                                # Check interrupt flag before sending each chunk
                                if not tts_interrupt_flags.get(pc_id, False):
                                    logger.warning("[%s] TTS interrupted (sent %d chunks)", pc_id, chunks_sent)
                                    if ch and ch.readyState == "open":
                                        ch.send(json.dumps({
                                            "type": "tts_interrupted",
                                            "chunks_sent": chunks_sent
                                        }))
                                    break

                                # Send audio chunk to client
                                if ch and ch.readyState == "open":
                                    chunk_size = len(audio_chunk)
                                    if chunks_sent == 0:
                                        logger.info("[%s] Sending first TTS chunk (%d bytes)", pc_id, chunk_size)
                                    ch.send(json.dumps({
                                        "type": "tts_chunk",
                                        "audio": base64.b64encode(audio_chunk).decode('utf-8')
                                    }))
                                    chunks_sent += 1
                            else:
                                # TTS completed without interruption
                                logger.info("[%s] ✓ TTS complete (%d chunks)", pc_id, chunks_sent)
                                if ch and ch.readyState == "open":
                                    ch.send(json.dumps({"type": "tts_complete"}))

                        finally:
                            # Always clear the flag when done
                            tts_interrupt_flags[pc_id] = False

                except Exception as e:
                    logger.exception("[%s] Pipeline error: %s", pc_id, e)
                    if ch and ch.readyState == "open":
                        ch.send(json.dumps({
                            "type": "error",
                            "message": str(e)
                        }))

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

        # Connect to Deepgram
        base_url = "wss://api.deepgram.com/v1/listen"
        websocket_url = f"{base_url}?model={config.DEEPGRAM_MODEL}&encoding=linear16&sample_rate={sample_rate}&channels={channel_count}&interim_results=true"

        dg_stream_task = asyncio.create_task(
            deepgram_stream_ws_send_and_recv(
                websocket_url,
                config.DEEPGRAM_KEY,
                audio_q,
                on_deepgram_event
            )
        )

        # Send first frame
        await audio_q.put(audioframe_to_pcm16_bytes(first_frame))

        # Stream remaining frames
        while True:
            frame = await track.recv()
            pcm_bytes = audioframe_to_pcm16_bytes(frame)
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

import asyncio
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
import json
from typing import Any, Dict, Optional

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.mediastreams import AudioFrame
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import logging
import websockets

from aiortc.contrib.media import MediaBlackhole

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-deepgram")

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

DEEPGRAM_KEY = "dea381e9d217d2451a3ef550b95b2735e58f101b"

pcs = set[Any]()
datachannels: Dict[str, Any] = {}


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
    model = "nova"
    websocket_url_template = f"{base_listen}?model={model}&encoding=linear16&interim_results=true"

    async def on_deepgram_event(data: Dict):
        t = data.get("type")
        if t == "Results" or t == "results":
            channel_obj = data.get("channel", {})
            alts = channel_obj.get("alternatives", [])
            if alts:
                best = alts[0]
                transcript = best.get("transcript", "")
                is_final = data.get("is_final", False) or data.get("speech_final", False)
                logger.info("[%s] Deepgram transcript (final=%s): %s", pc_id, is_final, transcript)
                ch = datachannels.get(pc_id)
                if ch and ch.readyState == "open":
                    payload = json.dumps({"type": "transcript", "text": transcript, "is_final": is_final})
                    try:
                        ch.send(payload)
                    except Exception:
                        logger.exception("[%s] failed to send transcript over datachannel", pc_id)
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

        websocket_url = f"{websocket_url_template}&sample_rate={sample_rate}&channels={channel_count}"

        dg_stream_task = asyncio.create_task(
            deepgram_stream_ws_send_and_recv(
                websocket_url,
                DEEPGRAM_KEY,
                sample_rate,
                channel_count,
                audio_q,
                on_deepgram_event,
            )
        )

        pcm_bytes = audioframe_to_pcm16_bytes(first_frame)
        await audio_q.put(pcm_bytes)

        while True:
            frame = await track.recv()
            pcm_bytes = audioframe_to_pcm16_bytes(frame)
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

        logger.info("[%s] consumer finished and Deepgram stream closed", pc_id)

@app.post("/offer")
async def offer(request: Request):
    data: Dict = await request.json()
    offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])

    pc = RTCPeerConnection()
    pc_id = f"pc-{id(pc)}"
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
        def on_close():
            logger.info("[%s] datachannel closed", pc_id)
            datachannels.pop(pc_id, None)

    
    pcs.add(pc)
    logger.info("[%s] created for incoming offer", pc_id)

    media_blackhole = MediaBlackhole()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("[%s] Connection state => %s", pc_id, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
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
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

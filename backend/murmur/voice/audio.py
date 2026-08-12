"""Audio conversion and Deepgram WebSocket transport."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
import websockets
from aiortc.mediastreams import AudioFrame

logger = logging.getLogger(__name__)

TranscriptCallback = Callable[[dict[str, Any]], Awaitable[None]]
ConnectedCallback = Callable[[], Awaitable[None]]


def audioframe_to_pcm16_bytes(frame: AudioFrame) -> bytes:
    """Convert an aiortc audio frame to interleaved signed 16-bit PCM."""
    samples_array = frame.to_ndarray()

    if samples_array.ndim == 1:
        samples_array = samples_array.reshape(-1, 1)
    samples = getattr(frame, "samples", None)
    if (
        samples is not None
        and samples_array.shape[0] != samples
        and samples_array.shape[1] == samples
    ):
        samples_array = samples_array.T
    if np.issubdtype(samples_array.dtype, np.floating):
        samples_array = (samples_array * 32767).astype("int16")
    elif samples_array.dtype != np.int16:
        samples_array = samples_array.astype("int16")

    return samples_array.tobytes()


async def stream_deepgram(
    websocket_url: str,
    auth_key: str,
    audio_queue: asyncio.Queue[bytes | None],
    results_callback: TranscriptCallback,
    on_connected: ConnectedCallback | None = None,
) -> None:
    """Send PCM audio and deliver decoded transcript events over one WebSocket."""
    headers = {"Authorization": f"Token {auth_key}"}
    logger.info("Connecting to Deepgram at %s", websocket_url)
    try:
        async with websockets.connect(
            websocket_url,
            additional_headers=headers,
            max_size=None,
        ) as websocket:
            logger.info("Deepgram WebSocket connected")
            if on_connected:
                await on_connected()

            async def send_audio() -> None:
                try:
                    while True:
                        pcm_bytes = await audio_queue.get()
                        if pcm_bytes is None:
                            await websocket.send(json.dumps({"type": "Finalize"}))
                            return
                        await websocket.send(pcm_bytes)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Deepgram audio sender failed: %s", exc)
                    raise

            send_task = asyncio.create_task(send_audio())
            try:
                async for message in websocket:
                    try:
                        await results_callback(json.loads(message))
                    except json.JSONDecodeError:
                        logger.debug("Ignoring non-JSON Deepgram message")
            except websockets.ConnectionClosed as exc:
                logger.info("Deepgram WebSocket closed: %s", exc)
            finally:
                send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)
                await audio_queue.put(None)
    except Exception as exc:
        logger.exception("Failed to connect or stream to Deepgram: %s", exc)
        raise

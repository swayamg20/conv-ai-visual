import numpy as np
from aiortc import MediaStreamTrack, AudioFrame
import base64
import asyncio
def audio_frame_to_base64(frame: AudioFrame) -> str:
    arr = frame.to_ndarray()
    if np.issubdtype(arr.dtype, np.floating):
        arr = (arr * 32767).astype("int16")
    elif arr.dtype != np.int16:
        arr = arr.astype("int16")

    pcm_bytes = arr.tobytes()
    b64 = base64.b64encode(pcm_bytes).decode("ascii")
    return b64

async def consume_audio_track(track: MediaStreamTrack, pc_id: str):
    print(f"[{pc_id}] Started audio consumer for track kind={track.kind}")
    try:
        while True:
            frame = await track.recv()
            b64 = audio_frame_to_base64(frame)

            print(f"[{pc_id}] frame ts={frame.time} samples={frame.samples} rate={frame.sample_rate} b64(len)={len(b64)}")
            print(b64[:80] + "..." )
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        print(f"[{pc_id}] consumer task cancelled")
        raise
    except Exception as e:
        print(f"[{pc_id}] consumer stopped: {e}")

        
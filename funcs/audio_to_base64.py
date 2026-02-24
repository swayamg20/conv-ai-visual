import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack, AudioFrame
import base64
import asyncio
def audio_frame_to_base64(frame: AudioFrame) -> str:
    """
    Convert aiortc.AudioFrame -> base64 of raw 16-bit PCM interleaved bytes.

    Handles float32 frames by converting to int16. If frames are already int16,
    arr dtype will reflect that and we'll use the bytes directly.
    """

    arr = frame.to_ndarray()

    if np.issubdtype(arr.dtype, np.floating):
        arr = (arr * 32767).astype("int16")
    elif arr.dtype != np.int16:
        arr = arr.astype("int16")
    pcm_bytes = arr.tobytes()
    b64 = base64.b64encode(pcm_bytes).decode("ascii")
    return b64

async def consume_audio_track(track: MediaStreamTrack, pc_id: str):
    """
    Read frames from incoming audio track. Convert each frame into base64
    and handle it (here: print, but replace with your logic).
    """
    print(f"[{pc_id}] Started audio consumer for track kind={track.kind}")
    try:
        while True:
            frame = await track.recv()  # AudioFrame
            # Convert to base64
            b64 = audio_frame_to_base64(frame)

            # Example: print short metadata and first 80 chars of base64
            print(f"[{pc_id}] frame ts={frame.time} samples={frame.samples} rate={frame.sample_rate} b64(len)={len(b64)}")
            # CAUTION: printing whole base64 may be huge — here just a small prefix
            print(b64[:80] + "..." )

            # TODO: replace the print above with one of:
            # - push b64 into an asyncio.Queue to be processed by a writer task
            # - write to a .wav file (need a wav header and accumulating pcm_bytes)
            # - forward over HTTP/WS to another service
            # - store chunks in a DB / blob storage
            await asyncio.sleep(0)  # cooperative scheduling
    except asyncio.CancelledError:
        print(f"[{pc_id}] consumer task cancelled")
        raise
    except Exception as e:
        print(f"[{pc_id}] consumer stopped: {e}")

        
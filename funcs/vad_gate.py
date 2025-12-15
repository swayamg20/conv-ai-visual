import logging
import numpy as np
import torch
from silero_vad import load_silero_vad


logger = logging.getLogger("webrtc-deepgram.vad")


class SileroVADGate:
    def __init__(self, sample_rate: int, channels: int = 1, threshold: float = 0.2):
        """
        Lightweight streaming gate around Silero VAD.
        - sample_rate: incoming PCM sample rate from WebRTC (e.g. 48000)
        - channels:    number of interleaved channels in the PCM stream
        - threshold:   speech probability threshold
        """
        self.sample_rate = int(sample_rate)
        self.channels = int(channels) if channels else 1
        self.threshold = float(threshold)
        self._frame_index = 0

        # One VAD model instance per gate so that internal states are not shared
        self.model = load_silero_vad()
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()

    def _bytes_to_mono_float(self, pcm_bytes: bytes) -> np.ndarray:
        """Convert interleaved int16 PCM bytes -> mono float32 numpy array in [-1, 1]."""
        if not pcm_bytes:
            return np.zeros(0, dtype=np.float32)

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

        # Downmix to mono if we know the channel count
        if self.channels > 1:
            # Best-effort shape; if it doesn't divide evenly, just ignore extra samples
            usable = (audio.size // self.channels) * self.channels
            if usable == 0:
                return np.zeros(0, dtype=np.float32)
            audio = audio[:usable].reshape(-1, self.channels).mean(axis=1)

        return audio / 32768.0

    def should_send(self, pcm_bytes: bytes) -> bool:
        """
        Return True if this chunk is likely to contain speech (send to ASR),
        False if it is probably just silence / noise (drop it).
        """
        audio = self._bytes_to_mono_float(pcm_bytes)
        if audio.size == 0:
            return False

        sr = self.sample_rate

        # Silero only supports 8k / 16k (or multiples of 16k). Downsample if needed.
        if sr > 16000 and (sr % 16000 == 0):
            step = sr // 16000
            audio = audio[::step]
            sr = 16000
        elif sr not in (8000, 16000):
            # Unsupported sample rate, safest is to bypass VAD
            return True

        if audio.size == 0:
            return False

        # Silero expects fixed-size windows: 512 samples @ 16k, 256 @ 8k.
        window_size = 512 if sr == 16000 else 256

        if audio.size < window_size:
            pad = window_size - audio.size
            audio = np.pad(audio, (0, pad), mode="constant")
        else:
            audio = audio[-window_size:]

        chunk = torch.from_numpy(audio).unsqueeze(0)

        # Model returns speech probability for this window.
        prob = float(self.model(chunk, sr).item())
        decision = prob >= self.threshold

        # Sampled logging so we don't spam too hard
        self._frame_index += 1
        if self._frame_index % 50 == 0:
            if decision:
                logger.info("Speech detected %f", prob)
            else:
                logger.info("noise/silence detected %f", prob)
            

        return decision



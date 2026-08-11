"""
Smart Turn Detection - native audio turn detection using pipecat-ai/smart-turn-v3.

Determines when a user has finished their conversational turn by analyzing
raw audio waveforms (prosody, intonation, grammar cues) rather than just
silence duration.

Works alongside Silero VAD: VAD detects silence, Smart Turn decides if
the silence means the user is done or just pausing mid-thought.

Model: Whisper Tiny encoder + linear classifier head (~8M params, 8MB ONNX)
Speed: <60ms on most CPUs
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Coroutine, Optional, Tuple

import numpy as np
import onnxruntime as ort

logger = logging.getLogger("voiceai.smart_turn")

# HuggingFace model coordinates
HF_REPO_ID = "pipecat-ai/smart-turn-v3"
HF_MODEL_FILE = "smart-turn-v3.2-cpu.onnx"


def _truncate_or_pad(audio: np.ndarray, n_seconds: int = 8, sample_rate: int = 16000) -> np.ndarray:
    """Truncate to last n_seconds or zero-pad at the beginning (audio at end)."""
    max_samples = n_seconds * sample_rate
    if len(audio) > max_samples:
        return audio[-max_samples:]
    elif len(audio) < max_samples:
        padding = max_samples - len(audio)
        return np.pad(audio, (padding, 0), mode="constant", constant_values=0)
    return audio


class SmartTurnAnalyzer:
    """
    Smart Turn v3 ONNX inference wrapper.

    Singleton-ish: call get_instance() to share across sessions.
    Thread-safe for inference (ONNX runtime handles internal locking).
    """

    _instance: Optional["SmartTurnAnalyzer"] = None

    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold: float = 0.5,
        stop_secs: float = 2.0,
    ):
        self.threshold = threshold
        self.stop_secs = stop_secs

        resolved_path = model_path or self._resolve_model_path()
        logger.info("Loading Smart Turn ONNX model from %s", resolved_path)
        self._session = self._build_onnx_session(resolved_path)

        from transformers import WhisperFeatureExtractor

        self._feature_extractor = WhisperFeatureExtractor(chunk_length=8)

        logger.info(
            "Smart Turn analyzer ready (threshold=%.2f, stop_secs=%.1f)",
            threshold,
            stop_secs,
        )

    @classmethod
    def get_instance(cls, **kwargs) -> "SmartTurnAnalyzer":
        """Get or create the global Smart Turn analyzer."""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_model_path() -> str:
        """Check local paths, then download from HuggingFace."""
        models_dir = Path(__file__).parent.parent / "models"
        local_candidates = [
            models_dir / HF_MODEL_FILE,
            models_dir / "smart-turn-v3.onnx",
            Path(__file__).parent.parent / HF_MODEL_FILE,
        ]
        for p in local_candidates:
            if p.exists():
                return str(p)

        logger.info("Downloading Smart Turn v3 from HuggingFace (%s)...", HF_REPO_ID)
        from huggingface_hub import hf_hub_download

        models_dir.mkdir(parents=True, exist_ok=True)
        path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=HF_MODEL_FILE,
            local_dir=str(models_dir),
        )
        logger.info("Downloaded Smart Turn model to %s", path)
        return path

    @staticmethod
    def _build_onnx_session(onnx_path: str) -> ort.InferenceSession:
        so = ort.SessionOptions()
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(onnx_path, sess_options=so)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, audio_array: np.ndarray) -> dict:
        """
        Run Smart Turn inference on a mono 16kHz float32 audio array.

        Returns:
            {
                "prediction": 1 (complete) | 0 (incomplete),
                "probability": float (sigmoid output),
                "latency_ms": float,
            }
        """
        t0 = time.perf_counter()

        audio_array = _truncate_or_pad(audio_array, n_seconds=8, sample_rate=16000)

        inputs = self._feature_extractor(
            audio_array,
            sampling_rate=16000,
            return_tensors="np",
            padding="max_length",
            max_length=8 * 16000,
            truncation=True,
            do_normalize=True,
        )

        input_features = inputs.input_features.squeeze(0).astype(np.float32)
        input_features = np.expand_dims(input_features, axis=0)

        outputs = self._session.run(None, {"input_features": input_features})
        probability = float(outputs[0][0].item())
        prediction = 1 if probability > self.threshold else 0

        latency_ms = (time.perf_counter() - t0) * 1000

        return {
            "prediction": prediction,
            "probability": probability,
            "latency_ms": latency_ms,
        }


# ======================================================================
# Per-session audio buffer
# ======================================================================


class TurnAudioBuffer:
    """
    Ring buffer that accumulates PCM audio for one peer session.
    Stores up to max_secs of 16kHz mono float32 audio.
    Handles resampling from the WebRTC source sample rate.
    """

    def __init__(self, max_secs: int = 8, target_sr: int = 16000):
        self.max_samples = max_secs * target_sr
        self.target_sr = target_sr
        self._chunks: list[np.ndarray] = []
        self._total_samples = 0

    def append(self, pcm_bytes: bytes, source_sr: int, channels: int = 1):
        """Append raw int16 PCM bytes, converting to 16kHz mono float32."""
        if not pcm_bytes:
            return

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

        # Downmix to mono
        if channels > 1:
            usable = (audio.size // channels) * channels
            if usable == 0:
                return
            audio = audio[:usable].reshape(-1, channels).mean(axis=1)

        # Normalize int16 → [-1, 1]
        audio = audio / 32768.0

        # Downsample to 16kHz
        if source_sr > 16000 and (source_sr % 16000 == 0):
            step = source_sr // 16000
            audio = audio[::step]
        elif source_sr != 16000 and source_sr > 0:
            ratio = 16000 / source_sr
            new_len = int(len(audio) * ratio)
            if new_len == 0:
                return
            indices = np.linspace(0, len(audio) - 1, new_len).astype(int)
            audio = audio[indices]

        self._chunks.append(audio)
        self._total_samples += len(audio)

        # Trim from front if over budget
        while self._total_samples > self.max_samples and self._chunks:
            excess = self._total_samples - self.max_samples
            front = self._chunks[0]
            if len(front) <= excess:
                self._chunks.pop(0)
                self._total_samples -= len(front)
            else:
                self._chunks[0] = front[excess:]
                self._total_samples -= excess

    def get_audio(self) -> np.ndarray:
        """Return the buffered audio as a single contiguous array."""
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)

    def duration_secs(self) -> float:
        return self._total_samples / self.target_sr

    def clear(self):
        self._chunks.clear()
        self._total_samples = 0

    @property
    def is_empty(self) -> bool:
        return self._total_samples == 0


# ======================================================================
# Per-session Smart Turn state machine
# ======================================================================

# Type alias for the turn-complete callback
TurnCompleteCallback = Callable[[str], Coroutine]


class SmartTurnSession:
    """
    Per-peer-connection orchestrator for Smart Turn.

    Lifecycle:
        1. feed_audio()   - called on every WebRTC audio frame
        2. on_speech_final() - called when Deepgram returns is_final
        3. If Smart Turn says "incomplete" → accumulates transcript, starts fallback timer
        4. If Smart Turn says "complete" OR fallback fires → returns full text for LLM

    The fallback timer ensures we never hang indefinitely if Smart Turn keeps
    saying "incomplete" but the user has actually stopped.
    """

    def __init__(self, analyzer: SmartTurnAnalyzer):
        self.analyzer = analyzer
        self.audio_buffer = TurnAudioBuffer()
        self.accumulated_transcript: str = ""
        self._fallback_task: Optional[asyncio.Task] = None
        self._on_turn_complete: Optional[TurnCompleteCallback] = None
        self._pending = False

    def set_turn_complete_callback(self, callback: TurnCompleteCallback):
        """Register async callback invoked when fallback timer fires."""
        self._on_turn_complete = callback

    # ------------------------------------------------------------------
    # Audio feeding
    # ------------------------------------------------------------------

    def feed_audio(self, pcm_bytes: bytes, source_sr: int, channels: int = 1):
        """Feed raw PCM audio into the turn buffer. Called on every WebRTC frame."""
        self.audio_buffer.append(pcm_bytes, source_sr, channels)

    # ------------------------------------------------------------------
    # Transcript accumulation (called on every Deepgram is_final)
    # ------------------------------------------------------------------

    def accumulate_transcript(self, transcript: str):
        """
        Accumulate finalized transcript segments as the user speaks.
        Called on every Deepgram is_final event (which fires frequently).
        Does NOT trigger Smart Turn — just buffers text.
        """
        if transcript.strip():
            self.accumulated_transcript += (
                " " + transcript if self.accumulated_transcript else transcript
            )

    # ------------------------------------------------------------------
    # Turn decision (called only on Deepgram speech_final)
    # ------------------------------------------------------------------

    async def on_speech_final(self, transcript: str = "") -> Tuple[bool, str]:
        """
        Called when Deepgram fires speech_final (actual pause detected).
        Runs Smart Turn inference on the buffered audio to decide if
        the user's conversational turn is complete.

        Transcript arg is optional — text should already be accumulated
        via accumulate_transcript() on each is_final event.

        Returns:
            (is_turn_complete, accumulated_text)
            - If True: accumulated_text contains the full user utterance → send to LLM
            - If False: text is empty; Smart Turn says user isn't done yet
        """
        if transcript.strip():
            self.accumulate_transcript(transcript)

        # Cancel any pending fallback
        self._cancel_fallback()

        # If buffer is too short (<0.1s), just assume complete
        audio = self.audio_buffer.get_audio()
        if audio.size < 1600:
            text = self.accumulated_transcript.strip()
            self._reset_turn()
            return True, text

        # Run inference in a thread executor (model inference is ~10-60ms)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self.analyzer.predict, audio)

        logger.info(
            "Smart Turn: %s (prob=%.3f, latency=%.1fms, audio=%.1fs) transcript='%s'",
            "COMPLETE" if result["prediction"] == 1 else "INCOMPLETE",
            result["probability"],
            result["latency_ms"],
            self.audio_buffer.duration_secs(),
            self.accumulated_transcript[:60],
        )

        if result["prediction"] == 1:
            # Turn complete → flush to LLM
            text = self.accumulated_transcript.strip()
            self._reset_turn()
            return True, text
        else:
            # Turn incomplete → start fallback timer, wait for more speech
            self._pending = True
            self._fallback_task = asyncio.create_task(self._fallback_timeout())
            return False, ""

    async def _fallback_timeout(self):
        """Force-complete the turn if silence exceeds stop_secs."""
        try:
            await asyncio.sleep(self.analyzer.stop_secs)
            text = self.accumulated_transcript.strip()
            if text and self._on_turn_complete:
                logger.info(
                    "Smart Turn fallback: turn forced complete after %.1fs (text='%s')",
                    self.analyzer.stop_secs,
                    text[:60],
                )
                self._pending = False
                self._reset_turn()
                await self._on_turn_complete(text)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cancel_fallback(self):
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
            self._fallback_task = None

    def _reset_turn(self):
        """Reset state for the next turn."""
        self.accumulated_transcript = ""
        self.audio_buffer.clear()
        self._cancel_fallback()
        self._pending = False

    @property
    def has_pending_turn(self) -> bool:
        """True if Smart Turn said 'incomplete' and we're waiting."""
        return self._pending

    def cleanup(self):
        """Full teardown for session end."""
        self._reset_turn()
        self._on_turn_complete = None

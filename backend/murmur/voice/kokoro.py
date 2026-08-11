"""
Kokoro TTS — local text-to-speech via ONNX model.

Drop-in alternative to ElevenLabs with ~50ms TTFB (no network hop).
Requires: pip install kokoro-onnx
"""

import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger("kokoro-tts")

CHUNK_SIZE = 4096


class KokoroTTSPipeline:
    """Local TTS via Kokoro ONNX model."""

    def __init__(self, model_path: Optional[str] = None):
        self._model = None
        self._model_path = model_path

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            import kokoro_onnx

            self._model = (
                kokoro_onnx.Kokoro(self._model_path) if self._model_path else kokoro_onnx.Kokoro()
            )
            logger.info("Kokoro TTS model loaded")
        except ImportError as exc:
            raise ImportError("kokoro-onnx not installed. Run: pip install kokoro-onnx") from exc

    async def text_to_speech_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        Generate PCM16 audio chunks from text.
        Compatible with the existing TTS pipeline interface.
        """
        import asyncio

        self._ensure_model()

        loop = asyncio.get_event_loop()
        audio_bytes = await loop.run_in_executor(None, self._model.create, text)

        for i in range(0, len(audio_bytes), CHUNK_SIZE):
            yield audio_bytes[i : i + CHUNK_SIZE]

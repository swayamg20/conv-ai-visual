import logging
import os
from typing import AsyncIterator, Optional

from elevenlabs import AsyncElevenLabs
from elevenlabs.types import VoiceSettings

logger = logging.getLogger("tts-pipeline")

_RETRYABLE_TTS_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "rate limit",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "server error",
    "service unavailable",
    "remote protocol error",
)


def is_retryable_tts_error(exc: Exception) -> bool:
    """Heuristic classification for transient ElevenLabs/network errors."""
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _RETRYABLE_TTS_MARKERS)


class TTSPipeline:
    """
    Handles Text-to-Speech pipeline with ElevenLabs.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model_id: str = "eleven_turbo_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        use_speaker_boost: bool = True,
    ):
        """
        Initialize TTS pipeline.
        """
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ElevenLabs API key not provided")

        self.client = AsyncElevenLabs(api_key=self.api_key)
        self.voice_id = voice_id
        self.model_id = model_id

        self.voice_settings = VoiceSettings(
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            use_speaker_boost=use_speaker_boost,
        )

        logger.info(f"TTS pipeline initialized with voice: {voice_id}, model: {model_id}")

    async def text_to_speech_stream(
        self, text: str, voice_id: Optional[str] = None, model_id: Optional[str] = None
    ) -> AsyncIterator[bytes]:
        """
        Convert text to speech with streaming output.
        """
        try:
            audio_stream = self.client.text_to_speech.convert(
                text=text,
                voice_id=voice_id or self.voice_id,
                model_id=model_id or self.model_id,
                voice_settings=self.voice_settings,
                output_format="pcm_16000",
            )

            async for chunk in audio_stream:
                yield chunk

        except Exception as e:
            logger.exception(f"Error in TTS streaming: {e}")
            raise

    async def text_to_speech(
        self, text: str, voice_id: Optional[str] = None, model_id: Optional[str] = None
    ) -> bytes:
        """
        Convert text to speech and return complete audio.
        """
        try:
            audio_chunks = []
            audio_stream = self.client.text_to_speech.convert(
                text=text,
                voice_id=voice_id or self.voice_id,
                model_id=model_id or self.model_id,
                voice_settings=self.voice_settings,
                output_format="pcm_16000",
            )
            async for chunk in audio_stream:
                audio_chunks.append(chunk)
            return b"".join(audio_chunks)
        except Exception as e:
            logger.exception(f"Error in TTS conversion: {e}")
            raise

    async def get_available_voices(self):
        """
        Get list of available voices.
        """
        try:
            voices = await self.client.voices.get_all()
            return voices.voices
        except Exception as e:
            logger.exception(f"Error fetching voices: {e}")
            raise

    async def get_voice_info(self, voice_id: Optional[str] = None):
        """
        Get information about a specific voice.
        """
        try:
            voice = await self.client.voices.get(voice_id or self.voice_id)
            return voice
        except Exception as e:
            logger.exception(f"Error fetching voice info: {e}")
            raise

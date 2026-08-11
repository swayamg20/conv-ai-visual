"""TTS provider initialization, retry policy, and local fallback."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from murmur.core.config import config
from murmur.voice.elevenlabs import TTSPipeline, is_retryable_tts_error
from murmur.voice.kokoro import KokoroTTSPipeline

logger = logging.getLogger(__name__)
AudioEmitter = Callable[[bytes], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    provider_used: str
    retry_count: int = 0
    fallback_used: bool = False
    fallback_provider: str | None = None


class SpeechSynthesizer:
    """Stream speech through a primary provider with bounded fallback policy."""

    def __init__(self) -> None:
        self.primary: TTSPipeline | KokoroTTSPipeline | None = None
        self.fallback: KokoroTTSPipeline | None = None
        self._fallback_attempted = False
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        if config.TTS_PROVIDER == "kokoro":
            self.primary = KokoroTTSPipeline(model_path=config.KOKORO_MODEL_PATH)
            logger.info("Kokoro TTS pipeline initialized (local ONNX)")
            return

        self.primary = TTSPipeline(
            api_key=config.ELEVENLABS_API_KEY,
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL_ID,
            stability=config.TTS_STABILITY,
            similarity_boost=config.TTS_SIMILARITY_BOOST,
            style=config.TTS_STYLE,
            use_speaker_boost=config.TTS_USE_SPEAKER_BOOST,
        )
        logger.info("ElevenLabs TTS pipeline initialized")

    def reset(self) -> None:
        """Drop failed startup state while retaining idempotent startup semantics."""
        self.primary = None
        self.fallback = None
        self._fallback_attempted = False

    def available(self) -> bool:
        return self.primary is not None or self._fallback_pipeline() is not None

    async def stream(self, sentence: str, emit: AudioEmitter) -> SynthesisResult:
        """Stream one sentence, retrying only transient ElevenLabs failures."""
        primary = self.primary
        primary_provider = self.provider_name(primary)
        retry_count = 0
        final_error: str | None = None

        if primary is not None and primary_provider:
            max_attempts = 1
            if primary_provider == "elevenlabs":
                max_attempts += max(0, config.TTS_MAX_RETRIES)

            for attempt in range(max_attempts):
                try:
                    await self._stream_pipeline(sentence, primary, emit)
                    return SynthesisResult(
                        provider_used=primary_provider,
                        retry_count=retry_count,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    final_error = f"{type(exc).__name__}: {exc}"
                    should_retry = (
                        primary_provider == "elevenlabs"
                        and attempt < max_attempts - 1
                        and is_retryable_tts_error(exc)
                    )
                    if not should_retry:
                        break
                    retry_count += 1
                    delay_seconds = config.TTS_RETRY_BASE_DELAY_SECS * (2**attempt)
                    logger.warning(
                        "ElevenLabs TTS attempt %d/%d failed; retrying in %.2fs: %s",
                        attempt + 1,
                        max_attempts,
                        delay_seconds,
                        exc,
                    )
                    await asyncio.sleep(delay_seconds)

        fallback = self._fallback_pipeline()
        if fallback is not None and fallback is not primary:
            try:
                await self._stream_pipeline(sentence, fallback, emit)
                return SynthesisResult(
                    provider_used="kokoro",
                    retry_count=retry_count,
                    fallback_used=True,
                    fallback_provider="kokoro",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                final_error = f"{type(exc).__name__}: {exc}"

        raise RuntimeError(final_error or "TTS failed without an available provider")

    @staticmethod
    async def _stream_pipeline(sentence: str, pipeline: Any, emit: AudioEmitter) -> None:
        async for audio_chunk in pipeline.text_to_speech_stream(sentence):
            await emit(audio_chunk)

    def _fallback_pipeline(self) -> KokoroTTSPipeline | None:
        if not config.TTS_FALLBACK_TO_KOKORO:
            return None
        if isinstance(self.primary, KokoroTTSPipeline):
            return self.primary
        if self.fallback is None and not self._fallback_attempted:
            self._fallback_attempted = True
            try:
                self.fallback = KokoroTTSPipeline(model_path=config.KOKORO_MODEL_PATH)
                logger.info("Kokoro fallback TTS pipeline initialized")
            except Exception as exc:
                logger.warning("Failed to initialize Kokoro fallback pipeline: %s", exc)
                self.fallback = None
        return self.fallback

    @staticmethod
    def provider_name(pipeline: Any) -> str | None:
        if pipeline is None:
            return None
        if isinstance(pipeline, KokoroTTSPipeline):
            return "kokoro"
        if isinstance(pipeline, TTSPipeline):
            return "elevenlabs"
        return pipeline.__class__.__name__.lower()

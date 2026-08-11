"""Provider-free retry and fallback contracts for speech synthesis."""

import pytest
from murmur.voice.synthesis import SpeechSynthesizer

from funcs.config import config


class _FakePipeline:
    def __init__(self, failures: list[Exception], chunks: list[bytes]) -> None:
        self.failures = failures
        self.chunks = chunks
        self.attempts = 0

    async def text_to_speech_stream(self, _sentence):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_transient_primary_failure_is_retried(monkeypatch) -> None:
    synthesizer = SpeechSynthesizer()
    primary = _FakePipeline([RuntimeError("503 service unavailable")], [b"audio"])
    synthesizer.primary = primary
    emitted: list[bytes] = []

    async def emit(chunk: bytes) -> None:
        emitted.append(chunk)

    monkeypatch.setattr(config, "TTS_MAX_RETRIES", 1)
    monkeypatch.setattr(config, "TTS_RETRY_BASE_DELAY_SECS", 0)
    monkeypatch.setattr(synthesizer, "provider_name", lambda _pipeline: "elevenlabs")
    monkeypatch.setattr(synthesizer, "_fallback_pipeline", lambda: None)

    result = await synthesizer.stream("hello", emit)

    assert emitted == [b"audio"]
    assert primary.attempts == 2
    assert result.retry_count == 1
    assert result.provider_used == "elevenlabs"
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_non_retryable_primary_failure_uses_fallback(monkeypatch) -> None:
    synthesizer = SpeechSynthesizer()
    primary = _FakePipeline([RuntimeError("invalid voice")], [])
    fallback = _FakePipeline([], [b"local-audio"])
    synthesizer.primary = primary
    emitted: list[bytes] = []

    async def emit(chunk: bytes) -> None:
        emitted.append(chunk)

    monkeypatch.setattr(synthesizer, "provider_name", lambda _pipeline: "elevenlabs")
    monkeypatch.setattr(synthesizer, "_fallback_pipeline", lambda: fallback)

    result = await synthesizer.stream("hello", emit)

    assert emitted == [b"local-audio"]
    assert primary.attempts == 1
    assert fallback.attempts == 1
    assert result.fallback_used is True
    assert result.fallback_provider == "kokoro"

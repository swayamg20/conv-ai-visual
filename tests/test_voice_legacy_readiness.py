"""Provider-free readiness contracts for the bounded legacy voice baseline."""

from types import SimpleNamespace

import pytest
from murmur.core.config import config
from murmur.runtime import RuntimeRegistry
from murmur.voice.service import VoiceService
from murmur.voice.transcription import VoiceReadinessError


class _FakeModels:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.retrieved: list[str] = []

    async def retrieve(self, model: str) -> object:
        self.retrieved.append(model)
        if self.error:
            raise self.error
        return object()


class _FakeElevenLabs:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def get_voice_info(self) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()


def _fake_pipeline(models: _FakeModels, *, provider: str = "groq") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        client=SimpleNamespace(
            model="fake-model",
            client=SimpleNamespace(models=models),
        ),
    )


@pytest.mark.asyncio
async def test_missing_deepgram_configuration_is_a_persistent_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DEEPGRAM_KEY", "")
    service = VoiceService(RuntimeRegistry())

    service.start()

    assert service.started is True
    assert service.startup_failure is not None
    assert service.startup_failure.component == "stt"
    with pytest.raises(VoiceReadinessError, match="DEEPGRAM_KEY") as exc_info:
        await service.check_readiness("peer")
    assert exc_info.value.component == "stt"


@pytest.mark.asyncio
async def test_selected_llm_and_tts_are_probed_and_reported_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(config, "SMART_TURN_ENABLED", False)
    models = _FakeModels()
    tts = _FakeElevenLabs()
    service = VoiceService(RuntimeRegistry())
    service.started = True
    service.pipeline_factory = SimpleNamespace(
        get_or_create=lambda _peer_id: _fake_pipeline(models)
    )
    service.synthesizer.primary = tts

    checks = await service.check_readiness("peer")

    assert models.retrieved == ["fake-model"]
    assert tts.calls == 1
    assert checks == {
        "llm": {
            "provider": "groq",
            "configured": True,
            "reachable": True,
            "state": "ready",
        },
        "tts": {
            "provider": "elevenlabs",
            "configured": True,
            "reachable": True,
            "state": "ready",
        },
        "turn_detection": {
            "provider": "deepgram_endpointing",
            "configured": True,
            "reachable": True,
            "state": "ready",
        },
    }


@pytest.mark.asyncio
async def test_invalid_selected_tts_credentials_fail_closed_without_leaking_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(config, "SMART_TURN_ENABLED", False)
    service = VoiceService(RuntimeRegistry())
    service.started = True
    service.pipeline_factory = SimpleNamespace(
        get_or_create=lambda _peer_id: _fake_pipeline(_FakeModels())
    )
    service.synthesizer.primary = _FakeElevenLabs(RuntimeError("secret provider detail"))

    with pytest.raises(VoiceReadinessError) as exc_info:
        await service.check_readiness("peer")

    assert exc_info.value.component == "tts"
    assert "secret provider detail" not in str(exc_info.value)
    assert "Continue in text mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_selected_llm_credentials_fail_closed_without_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(config, "SMART_TURN_ENABLED", False)
    service = VoiceService(RuntimeRegistry())
    service.started = True
    service.pipeline_factory = SimpleNamespace(
        get_or_create=lambda _peer_id: _fake_pipeline(
            _FakeModels(RuntimeError("invalid model credentials"))
        )
    )
    service.synthesizer.primary = _FakeElevenLabs()

    with pytest.raises(VoiceReadinessError) as exc_info:
        await service.check_readiness("peer")

    assert exc_info.value.component == "llm"
    assert "invalid model credentials" not in str(exc_info.value)
    assert "Continue in text mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_selected_local_tts_dependency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro")
    monkeypatch.setattr(config, "SMART_TURN_ENABLED", False)

    class MissingKokoro:
        def _ensure_model(self) -> None:
            raise ImportError("kokoro-onnx not installed")

    service = VoiceService(RuntimeRegistry())
    service.started = True
    service.pipeline_factory = SimpleNamespace(
        get_or_create=lambda _peer_id: _fake_pipeline(_FakeModels())
    )
    service.synthesizer.primary = MissingKokoro()

    with pytest.raises(VoiceReadinessError) as exc_info:
        await service.check_readiness("peer")

    assert exc_info.value.component == "tts"
    assert "kokoro" in str(exc_info.value)


@pytest.mark.asyncio
async def test_enabled_smart_turn_dependency_failure_blocks_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SMART_TURN_ENABLED", True)
    service = VoiceService(
        RuntimeRegistry(),
        provider_readiness_probe=lambda _peer_id: _successful_provider_probe(),
    )
    service.started = True
    service.smart_turn_failure = VoiceReadinessError(
        "turn_detection",
        "Smart Turn dependency is unavailable. Continue in text mode.",
    )

    with pytest.raises(VoiceReadinessError) as exc_info:
        await service.check_readiness("peer")

    assert exc_info.value.component == "turn_detection"


async def _successful_provider_probe() -> dict[str, object]:
    return {
        "llm": {"provider": "fake", "state": "ready"},
        "tts": {"provider": "fake", "state": "ready"},
    }

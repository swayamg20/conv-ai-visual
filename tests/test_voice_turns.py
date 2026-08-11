"""End-to-end local contract for one confirmed LLM-to-TTS voice turn."""

import json
from types import SimpleNamespace

import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice.synthesis import SynthesisResult
from murmur.voice.turns import schedule_turn


class _FakeChannel:
    readyState = "open"

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, payload: str) -> None:
        self.messages.append(payload)


class _FakePipeline:
    provider = "test"
    client = SimpleNamespace(model="test-model")

    def __init__(self) -> None:
        self.switched_to = None

    def switch_provider(self, provider, key, model) -> None:
        self.switched_to = (provider, key, model)

    async def chat_with_tools_stream(self, _text, **_kwargs):
        yield "First sentence. "
        yield "Second sentence"

    def get_last_call_metrics(self):
        return {"tool_calls": [], "tokens_in": 4, "tokens_out": 5}


class _FakePipelineFactory:
    def __init__(self, pipeline: _FakePipeline) -> None:
        self.pipeline = pipeline

    def get_or_create(self, _peer_id: str) -> _FakePipeline:
        return self.pipeline


class _FakeSynthesizer:
    def __init__(self) -> None:
        self.sentences: list[str] = []

    def available(self) -> bool:
        return True

    async def stream(self, sentence, emit) -> SynthesisResult:
        self.sentences.append(sentence)
        await emit(f"audio:{sentence}".encode())
        return SynthesisResult(provider_used="fake")


@pytest.mark.asyncio
async def test_confirmed_turn_streams_sentences_and_metrics(monkeypatch) -> None:
    runtime = RuntimeRegistry()
    voice_session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    channel = _FakeChannel()
    voice_session.datachannel = channel
    pipeline = _FakePipeline()
    synthesizer = _FakeSynthesizer()
    context = SimpleNamespace(
        runtime=runtime,
        pipeline_factory=_FakePipelineFactory(pipeline),
        synthesizer=synthesizer,
    )
    monkeypatch.setattr(
        "murmur.voice.turns.route_model",
        lambda _text: ("test", "test-key", "test-model"),
    )
    monkeypatch.setattr(
        "murmur.voice.turns.VoicePipelineLogRepo.save",
        lambda **_kwargs: SimpleNamespace(id="voice-log"),
    )
    monkeypatch.setattr(
        "murmur.voice.turns.TTSResilienceLogRepo.save",
        lambda **_kwargs: None,
    )

    await schedule_turn(context, "peer", "teach me")
    turn_task = voice_session.turn_task
    assert turn_task is not None
    await turn_task

    message_types = {json.loads(payload)["type"] for payload in channel.messages}
    assert synthesizer.sentences == ["First sentence.", "Second sentence"]
    assert pipeline.switched_to == ("test", "test-key", "test-model")
    assert {
        "tts_started",
        "tts_chunk",
        "tts_complete",
        "llm_response",
        "pipeline_metrics",
    } <= message_types
    assert voice_session.turn_task is None
    assert voice_session.tts_active is False

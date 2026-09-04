"""End-to-end local contract for one confirmed LLM-to-TTS voice turn."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice.synthesis import SynthesisResult
from murmur.voice.turns import _run_sdl_steps, schedule_turn


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


@pytest.mark.asyncio
async def test_interrupted_sdl_reports_sequence_identity_and_reason() -> None:
    runtime = RuntimeRegistry()
    voice_session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=True,
    )
    channel = _FakeChannel()
    voice_session.datachannel = channel

    class _InterruptingSynthesizer:
        def available(self) -> bool:
            return True

        async def stream(self, _sentence, emit) -> SynthesisResult:
            voice_session.tts_active = False
            await emit(b"stale audio")
            raise AssertionError("interrupted audio should not finish")

    context = SimpleNamespace(runtime=runtime, synthesizer=_InterruptingSynthesizer())

    with pytest.raises(asyncio.CancelledError):
        await _run_sdl_steps(
            context,
            "peer",
            {
                "steps": [
                    {"say": "First step", "show": [{"action": "text"}]},
                    {"say": "Future step", "show": [{"action": "circle"}]},
                ]
            },
        )

    messages = [json.loads(payload) for payload in channel.messages]
    started = next(message for message in messages if message["type"] == "sdl_start")
    ended = next(message for message in messages if message["type"] == "sdl_complete")
    assert ended == {
        "type": "sdl_complete",
        "sequence_id": started["sequence_id"],
        "reason": "interrupted",
    }
    assert not any(message["type"] == "tts_step_complete" for message in messages)
    assert voice_session.tts_active is False


@pytest.mark.asyncio
async def test_completed_sdl_reports_completed_without_changing_step_events() -> None:
    runtime = RuntimeRegistry()
    voice_session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=True,
    )
    channel = _FakeChannel()
    voice_session.datachannel = channel
    synthesizer = _FakeSynthesizer()
    context = SimpleNamespace(runtime=runtime, synthesizer=synthesizer)

    interrupted = await _run_sdl_steps(
        context,
        "peer",
        {"steps": [{"say": "Only step", "show": [{"action": "text"}]}]},
    )

    messages = [json.loads(payload) for payload in channel.messages]
    started = next(message for message in messages if message["type"] == "sdl_start")
    ended = next(message for message in messages if message["type"] == "sdl_complete")
    assert interrupted is False
    assert ended == {
        "type": "sdl_complete",
        "sequence_id": started["sequence_id"],
        "reason": "completed",
    }
    assert any(message["type"] == "tts_step_complete" for message in messages)

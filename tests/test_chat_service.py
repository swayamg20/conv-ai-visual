"""Focused contracts for chat runtime serialization and cleanup."""

import asyncio
from types import SimpleNamespace

import pytest
from murmur.chat import ChatService, ChatTurn, ChatTurnRequest
from murmur.runtime import RuntimeRegistry


class _ConcurrentPipeline:
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.closed_calls = 0
        self.memory = None
        self.provider = "test"
        self.client = SimpleNamespace(model="test-model")
        self.call_options: list[dict[str, object]] = []

    def set_canvas_callback(self, _callback) -> None:
        pass

    def set_animation_callback(self, _callback) -> None:
        pass

    async def chat_with_tools_stream(self, message, **kwargs):
        self.call_options.append(kwargs)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0)
            yield message
            await asyncio.sleep(0)
        finally:
            self.active_calls -= 1
            self.closed_calls += 1

    def get_last_call_metrics(self):
        return None


class _FactoryPipeline(_ConcurrentPipeline):
    def __init__(self) -> None:
        super().__init__()
        self.canvas_mode = True

    def load_tools_from_db(self) -> None:
        return None

    def get_tools_schema(self) -> list[dict[str, object]]:
        return []


class _ClosablePipeline:
    def __init__(self) -> None:
        self.memory = SimpleNamespace(context=SimpleNamespace(messages=[]))
        self.ended_with: list[str | None] = []

    def end_session(self, summary: str | None) -> None:
        self.ended_with.append(summary)


@pytest.mark.asyncio
async def test_chat_turns_are_serialized_per_session(monkeypatch) -> None:
    monkeypatch.setattr("murmur.chat.service.config.MURMUR_CHAT_MAX_TOOL_ROUNDS", 2)
    runtime = RuntimeRegistry()
    service = ChatService(runtime)
    pipeline = _ConcurrentPipeline()
    session = runtime.register_chat(
        "chat-session",
        pipeline,
        user_id="owner",
        agent_id=None,
    )

    async def collect(message: str) -> list[dict]:
        turn = ChatTurn(
            session_id="chat-session",
            user_id="owner",
            message=message,
            session=session,
        )
        return [event async for event in service.stream_events(turn)]

    first, second = await asyncio.gather(collect("first"), collect("second"))

    assert pipeline.max_active_calls == 1
    assert [options["max_tool_rounds"] for options in pipeline.call_options] == [2, 2]
    assert [event["type"] for event in first] == ["session", "chunk", "done"]
    assert [event["type"] for event in second] == ["session", "chunk", "done"]


@pytest.mark.asyncio
async def test_closing_chat_events_closes_the_owned_model_stream(monkeypatch) -> None:
    monkeypatch.setattr("murmur.chat.service.config.MURMUR_CHAT_MAX_TOOL_ROUNDS", 2)
    runtime = RuntimeRegistry()
    service = ChatService(runtime)
    pipeline = _ConcurrentPipeline()
    session = runtime.register_chat(
        "chat-session",
        pipeline,
        user_id="owner",
        agent_id=None,
    )
    events = service.stream_events(
        ChatTurn(
            session_id="chat-session",
            user_id="owner",
            message="explain vectors",
            session=session,
        )
    )

    assert (await anext(events))["type"] == "session"
    assert (await anext(events))["type"] == "chunk"
    await events.aclose()

    assert pipeline.active_calls == 0
    assert pipeline.closed_calls == 1


def test_chat_pipeline_receives_transport_retry_ceiling(monkeypatch) -> None:
    captured: dict[str, object] = {}
    pipeline = _FactoryPipeline()

    def pipeline_factory(**kwargs):
        captured.update(kwargs)
        return pipeline

    monkeypatch.setattr(
        "murmur.chat.service.config.MURMUR_CHAT_LLM_TRANSPORT_MAX_RETRIES",
        0,
    )
    service = ChatService(RuntimeRegistry(), pipeline_factory=pipeline_factory)

    service.prepare_turn("owner", ChatTurnRequest(message="Explain vectors"))

    assert captured["transport_max_retries"] == 0


@pytest.mark.asyncio
async def test_chat_finalization_is_idempotent() -> None:
    runtime = RuntimeRegistry()
    service = ChatService(runtime)
    pipeline = _ClosablePipeline()
    runtime.register_chat(
        "chat-session",
        pipeline,
        user_id="owner",
        agent_id=None,
    )

    await asyncio.gather(
        service.finalize("chat-session", min_messages=1),
        service.finalize("chat-session", min_messages=1),
    )

    assert pipeline.ended_with == [None]
    assert runtime.get_chat("chat-session") is None

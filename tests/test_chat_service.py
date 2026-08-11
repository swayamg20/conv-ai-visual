"""Focused contracts for chat runtime serialization and cleanup."""

import asyncio
from types import SimpleNamespace

import pytest
from murmur.chat import ChatService, ChatTurn
from murmur.runtime import RuntimeRegistry


class _ConcurrentPipeline:
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.memory = None
        self.provider = "test"
        self.client = SimpleNamespace(model="test-model")

    def set_canvas_callback(self, _callback) -> None:
        pass

    def set_animation_callback(self, _callback) -> None:
        pass

    async def chat_with_tools_stream(self, message, **_kwargs):
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(0)
        yield message
        await asyncio.sleep(0)
        self.active_calls -= 1

    def get_last_call_metrics(self):
        return None


class _ClosablePipeline:
    def __init__(self) -> None:
        self.memory = SimpleNamespace(context=SimpleNamespace(messages=[]))
        self.ended_with: list[str | None] = []

    def end_session(self, summary: str | None) -> None:
        self.ended_with.append(summary)


@pytest.mark.asyncio
async def test_chat_turns_are_serialized_per_session() -> None:
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
    assert [event["type"] for event in first] == ["session", "chunk", "done"]
    assert [event["type"] for event in second] == ["session", "chunk", "done"]


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

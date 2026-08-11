"""Focused lifecycle contracts for WebRTC voice sessions."""

import asyncio
from types import SimpleNamespace

import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice import VoiceService


class _FakePeer:
    def __init__(self) -> None:
        self.connectionState = "connected"
        self.close_count = 0

    async def close(self) -> None:
        self.connectionState = "closed"
        self.close_count += 1


class _FakeSmartTurn:
    def __init__(self) -> None:
        self.cleanup_count = 0

    def cleanup(self) -> None:
        self.cleanup_count += 1


class _FakePipeline:
    def __init__(self) -> None:
        self.agent_id = "agent"
        self.session_id = "persistent-session"
        self.memory = SimpleNamespace(context=SimpleNamespace(messages=[]))
        self.ended_with: list[str | None] = []

    def end_session(self, summary: str | None) -> None:
        self.ended_with.append(summary)


@pytest.mark.asyncio
async def test_voice_finalization_cancels_and_cleans_once() -> None:
    runtime = RuntimeRegistry()
    service = VoiceService(runtime)
    peer = _FakePeer()
    smart_turn = _FakeSmartTurn()
    pipeline = _FakePipeline()
    turn_cancelled = asyncio.Event()

    async def active_turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            turn_cancelled.set()

    voice_session = runtime.register_voice(
        "peer",
        peer,
        user_id="owner",
        agent_id="agent",
        persistent_session_id="persistent-session",
        canvas_mode=True,
    )
    voice_session.pipeline = pipeline
    voice_session.smart_turn = smart_turn
    voice_session.turn_task = asyncio.create_task(active_turn())
    await asyncio.sleep(0)

    await service.finalize("peer", min_messages=1, background=False)
    await service.finalize("peer", min_messages=1, background=False)

    assert turn_cancelled.is_set()
    assert smart_turn.cleanup_count == 1
    assert peer.close_count == 1
    assert pipeline.ended_with == [None]
    assert runtime.get_voice("peer") is None

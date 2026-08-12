"""Focused lifecycle contracts for WebRTC voice sessions."""

import asyncio
from types import SimpleNamespace

import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice import VoiceService
from murmur.voice.models import VoiceOfferRequest


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


class _FakeChannel:
    readyState = "open"

    def __init__(self) -> None:
        self.handlers = {}

    def on(self, event_name):
        def register(handler):
            self.handlers[event_name] = handler
            return handler

        return register


class _NegotiationPeer(_FakePeer):
    def __init__(self) -> None:
        super().__init__()
        self.handlers = {}
        self.localDescription = None

    def on(self, event_name):
        def register(handler):
            self.handlers[event_name] = handler
            return handler

        return register

    async def setRemoteDescription(self, _description) -> None:
        return None

    async def createAnswer(self):
        return SimpleNamespace(sdp="answer", type="answer")

    async def setLocalDescription(self, description) -> None:
        self.localDescription = description


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
    audio_cancelled = asyncio.Event()

    async def active_turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            turn_cancelled.set()

    async def active_audio() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            audio_cancelled.set()

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
    voice_session.audio_task = asyncio.create_task(active_audio())
    voice_session.turn_task = asyncio.create_task(active_turn())
    await asyncio.sleep(0)

    await service.finalize("peer", min_messages=1, background=False)
    await service.finalize("peer", min_messages=1, background=False)

    assert turn_cancelled.is_set()
    assert audio_cancelled.is_set()
    assert smart_turn.cleanup_count == 1
    assert peer.close_count == 1
    assert pipeline.ended_with == [None]
    assert runtime.get_voice("peer") is None


@pytest.mark.asyncio
async def test_stop_tts_does_not_reset_current_smart_turn_input() -> None:
    runtime = RuntimeRegistry()
    peer = _NegotiationPeer()
    service = VoiceService(runtime, peer_connection_factory=lambda: peer)

    await service.negotiate(
        "owner",
        VoiceOfferRequest(sdp="offer", type="offer"),
    )
    channel = _FakeChannel()
    peer.handlers["datachannel"](channel)
    voice_session = next(iter(runtime.voice_sessions.values()))
    smart_turn = SimpleNamespace(_reset_turn=lambda: (_ for _ in ()).throw(AssertionError()))
    voice_session.smart_turn = smart_turn
    voice_session.tts_active = True

    channel.handlers["message"]('{"type":"stop_tts"}')

    assert voice_session.tts_active is False
    assert voice_session.smart_turn is smart_turn

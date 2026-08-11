"""Provider-free contracts for transcript forwarding and interruption."""

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice.transcription import VoiceTranscriber


class _FakeTrack:
    def __init__(self) -> None:
        self._first_frame_sent = False

    async def recv(self):
        if not self._first_frame_sent:
            self._first_frame_sent = True
            return SimpleNamespace(
                sample_rate=16000,
                samples=2,
                layout=SimpleNamespace(channels=["mono"]),
                to_ndarray=lambda: np.array([1, 2], dtype=np.int16),
            )
        await asyncio.Event().wait()


class _FakeChannel:
    readyState = "open"

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


@pytest.mark.asyncio
async def test_final_transcript_interrupts_tts_and_confirms_turn() -> None:
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
    voice_session.tts_active = True
    confirmed: list[tuple[str, str]] = []
    turn_confirmed = asyncio.Event()

    async def handle_turn(peer_id: str, text: str) -> None:
        confirmed.append((peer_id, text))
        turn_confirmed.set()

    async def fake_deepgram(
        _url,
        _key,
        _queue,
        callback,
        *,
        on_connected,
    ) -> None:
        await on_connected()
        await callback(
            {
                "type": "Results",
                "channel": {"alternatives": [{"transcript": "hello tutor"}]},
                "is_final": True,
                "speech_final": False,
            }
        )
        await asyncio.Event().wait()

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=handle_turn,
        deepgram_streamer=fake_deepgram,
    )
    consume_task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))

    await asyncio.wait_for(turn_confirmed.wait(), timeout=1)
    consume_task.cancel()
    await asyncio.gather(consume_task, return_exceptions=True)

    assert confirmed == [("peer", "hello tutor")]
    assert voice_session.tts_active is False
    assert {message["type"] for message in channel.messages} == {"ready", "transcript"}

"""Provider-free contracts for transcript assembly, interruption, and readiness."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from murmur.runtime import RuntimeRegistry
from murmur.voice.transcription import VoiceReadinessError, VoiceTranscriber


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
    def __init__(self, *, ready_state: str = "open") -> None:
        self.readyState = ready_state
        self.messages: list[dict[str, Any]] = []
        self.message_sent = asyncio.Event()

    def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))
        self.message_sent.set()

    async def wait_for(self, event_type: str) -> dict[str, Any]:
        while True:
            matching = next(
                (message for message in self.messages if message.get("type") == event_type),
                None,
            )
            if matching is not None:
                return matching
            self.message_sent.clear()
            await self.message_sent.wait()


def _result(
    transcript: Any,
    *,
    is_final: bool = False,
    speech_final: bool = False,
    start: float | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "Results",
        "channel": {"alternatives": [{"transcript": transcript}]},
        "is_final": is_final,
        "speech_final": speech_final,
    }
    if start is not None:
        event["start"] = start
        event["duration"] = 0.5
    return event


async def _ready(_peer_id: str) -> Mapping[str, Any]:
    return {
        "llm": {"provider": "fake-llm", "state": "ready"},
        "tts": {"provider": "fake-tts", "state": "ready"},
    }


async def _run_events(
    events: Sequence[dict[str, Any]],
    *,
    readiness_check: Callable[[str], Awaitable[Mapping[str, Any]]] = _ready,
    tts_active: bool = False,
    connect_stt: bool = True,
    return_after_events: bool = False,
    analyzer: Any = None,
    clock: Callable[[], float] | None = None,
    channel: _FakeChannel | None = None,
    wait_for_channel: Callable[[Any, asyncio.Event], Awaitable[None]] | None = None,
) -> tuple[list[tuple[str, str]], _FakeChannel, Any]:
    runtime = RuntimeRegistry()
    voice_session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    channel = channel or _FakeChannel()
    voice_session.datachannel = channel
    voice_session.tts_active = tts_active
    confirmed: list[tuple[str, str]] = []
    events_delivered = asyncio.Event()

    async def handle_turn(peer_id: str, text: str) -> None:
        confirmed.append((peer_id, text))

    async def fake_deepgram(
        _url,
        _key,
        audio_queue,
        callback,
        *,
        on_connected,
    ) -> None:
        if connect_stt:
            await on_connected()
        for event in events:
            await callback(event)
        events_delivered.set()
        if return_after_events:
            return
        while await audio_queue.get() is not None:
            pass

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: analyzer,
        confirmed_turn_handler=handle_turn,
        deepgram_streamer=fake_deepgram,
        readiness_check=readiness_check,
        **({"clock": clock} if clock is not None else {}),
        **({"wait_for_channel": wait_for_channel} if wait_for_channel is not None else {}),
    )
    consume_task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    try:
        await asyncio.wait_for(events_delivered.wait(), timeout=1)
        await asyncio.sleep(0)
        if return_after_events:
            await asyncio.wait_for(channel.wait_for("error"), timeout=1)
        return confirmed, channel, voice_session
    finally:
        consume_task.cancel()
        await asyncio.gather(consume_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_final_segment_interrupts_tts_but_waits_for_explicit_eot() -> None:
    confirmed, channel, voice_session = await _run_events(
        [_result("hello tutor", is_final=True, start=0.0)],
        tts_active=True,
    )

    assert confirmed == []
    assert voice_session.tts_active is False
    assert {message["type"] for message in channel.messages} == {"ready", "transcript"}
    ready = next(message for message in channel.messages if message["type"] == "ready")
    assert set(ready["checks"]) == {"event_channel", "stt", "llm", "tts"}


@pytest.mark.asyncio
async def test_final_segments_accumulate_and_dispatch_once_on_speech_final() -> None:
    first = _result("hello", is_final=True, start=0.0)
    final = _result("tutor", is_final=True, speech_final=True, start=0.5)
    confirmed, _, _ = await _run_events([first, dict(first), final, dict(final)])

    assert confirmed == [("peer", "hello tutor")]


@pytest.mark.asyncio
async def test_confirmed_turn_event_is_emitted_exactly_once_at_semantic_commit() -> None:
    first = _result("hello", is_final=True, start=0.0)
    boundary = _result("tutor", is_final=True, speech_final=True, start=0.5)
    confirmed, channel, _ = await _run_events([first, boundary, dict(boundary)])

    commit_events = [message for message in channel.messages if message["type"] == "turn_committed"]
    assert commit_events == [{"type": "turn_committed", "text": "hello tutor"}]
    assert confirmed == [("peer", "hello tutor")]


@pytest.mark.asyncio
async def test_turn_timing_uses_the_injected_monotonic_clock() -> None:
    values = iter([10.0, 10.1, 10.2, 10.3])
    _, _, voice_session = await _run_events(
        [_result("hello", is_final=True, speech_final=True, start=0.0)],
        clock=lambda: next(values),
    )

    assert voice_session.turn_timing == {
        "first_transcript_ts": 10.0,
        "last_final_segment_ts": 10.1,
    }


@pytest.mark.asyncio
async def test_empty_speech_final_commits_buffered_segments() -> None:
    confirmed, _, _ = await _run_events(
        [
            _result("hello tutor", is_final=True, start=0.0),
            _result("", speech_final=True),
        ]
    )

    assert confirmed == [("peer", "hello tutor")]


@pytest.mark.asyncio
async def test_utterance_end_commits_buffered_segments() -> None:
    confirmed, _, _ = await _run_events(
        [
            _result("hello tutor", is_final=True, start=0.0),
            {"type": "UtteranceEnd"},
            {"type": "UtteranceEnd"},
        ]
    )

    assert confirmed == [("peer", "hello tutor")]


@pytest.mark.asyncio
async def test_invalid_utterance_end_is_ignored_and_boundary_is_coalesced() -> None:
    confirmed, channel, _ = await _run_events(
        [
            _result("hello tutor", is_final=True, start=0.0),
            {"type": "UtteranceEnd", "last_word_end": -1},
            {"type": "UtteranceEnd", "last_word_end": 0.5},
            {"type": "UtteranceEnd", "last_word_end": 0.5},
        ]
    )

    assert confirmed == [("peer", "hello tutor")]
    assert [message["type"] for message in channel.messages].count("turn_committed") == 1


@pytest.mark.asyncio
async def test_smart_turn_also_waits_for_explicit_eot() -> None:
    confirmed, _, _ = await _run_events(
        [
            _result("hello", is_final=True, start=0.0),
            _result("tutor", is_final=True, start=0.5),
        ],
        analyzer=SimpleNamespace(),
    )
    assert confirmed == []

    confirmed, _, _ = await _run_events(
        [
            _result("hello", is_final=True, start=0.0),
            _result("tutor", is_final=True, start=0.5),
            _result("", speech_final=True),
        ],
        analyzer=SimpleNamespace(),
    )
    assert confirmed == [("peer", "hello tutor")]


@pytest.mark.asyncio
async def test_empty_missing_and_malformed_results_do_not_dispatch() -> None:
    confirmed, _, _ = await _run_events(
        [
            {},
            {"type": "Results"},
            {"type": "Results", "channel": None, "is_final": True},
            _result(None, is_final=True),
            _result("   ", is_final=True),
            _result("", speech_final=True),
        ]
    )

    assert confirmed == []


@pytest.mark.asyncio
async def test_fresh_speech_can_repeat_the_previous_committed_text() -> None:
    confirmed, _, _ = await _run_events(
        [
            _result("yes", is_final=True, speech_final=True),
            {"type": "SpeechStarted"},
            _result("yes", is_final=True, speech_final=True),
        ]
    )

    assert confirmed == [("peer", "yes"), ("peer", "yes")]


@pytest.mark.asyncio
async def test_fresh_positioned_speech_can_repeat_without_speech_started() -> None:
    confirmed, _, _ = await _run_events(
        [
            _result("yes", is_final=True, speech_final=True, start=0.0),
            _result("yes", is_final=True, speech_final=True, start=1.0),
        ]
    )

    assert confirmed == [("peer", "yes"), ("peer", "yes")]


@pytest.mark.asyncio
async def test_readiness_waits_for_late_datachannel_open() -> None:
    channel = _FakeChannel(ready_state="connecting")
    provider_connected = asyncio.Event()
    allow_events = asyncio.Event()

    async def wait_for_channel(_session, channel_changed: asyncio.Event) -> None:
        await allow_events.wait()
        channel.readyState = "open"
        channel_changed.set()

    async def readiness(_peer_id: str) -> Mapping[str, Any]:
        provider_connected.set()
        return await _ready(_peer_id)

    async def fake_deepgram(_url, _key, audio_queue, callback, *, on_connected) -> None:
        await on_connected()
        await provider_connected.wait()
        await allow_events.wait()
        await channel.wait_for("ready")
        await callback(_result("hello", is_final=True, speech_final=True, start=0.0))
        while await audio_queue.get() is not None:
            pass

    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    session.datachannel = channel
    confirmed: list[tuple[str, str]] = []
    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn(confirmed, peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=readiness,
        wait_for_channel=wait_for_channel,
    )
    task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    try:
        await asyncio.wait_for(provider_connected.wait(), timeout=1)
        assert channel.messages == []
        allow_events.set()
        await asyncio.wait_for(channel.wait_for("turn_committed"), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [message["type"] for message in channel.messages].count("ready") == 1
    assert confirmed == [("peer", "hello")]


@pytest.mark.asyncio
async def test_readiness_starts_channel_wait_only_after_providers_are_ready() -> None:
    channel = _FakeChannel(ready_state="connecting")
    provider_connected = asyncio.Event()
    open_channel = asyncio.Event()
    wait_started = asyncio.Event()

    async def wait_for_channel(_session, channel_changed: asyncio.Event) -> None:
        wait_started.set()
        await open_channel.wait()
        channel.readyState = "open"
        channel_changed.set()

    async def readiness(peer_id: str) -> Mapping[str, Any]:
        provider_connected.set()
        return await _ready(peer_id)

    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(connectionState="connecting"),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    session.datachannel = channel

    async def fake_deepgram(_url, _key, audio_queue, _callback, *, on_connected) -> None:
        await on_connected()
        while await audio_queue.get() is not None:
            pass

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn([], peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=readiness,
        wait_for_channel=wait_for_channel,
    )
    task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    try:
        await asyncio.wait_for(provider_connected.wait(), timeout=1)
        await asyncio.wait_for(wait_started.wait(), timeout=1)
        assert channel.messages == []
        open_channel.set()
        await asyncio.wait_for(channel.wait_for("ready"), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [message["type"] for message in channel.messages].count("ready") == 1


async def _append_turn(turns: list[tuple[str, str]], peer_id: str, text: str) -> None:
    turns.append((peer_id, text))


@pytest.mark.asyncio
async def test_readiness_failure_is_delivered_after_late_channel_open_and_consume_exits() -> None:
    channel = _FakeChannel(ready_state="connecting")
    failure_observed = asyncio.Event()
    open_channel = asyncio.Event()

    async def wait_for_channel(_session, channel_changed: asyncio.Event) -> None:
        await open_channel.wait()
        channel.readyState = "open"
        channel_changed.set()

    async def fail_readiness(_peer_id: str) -> Mapping[str, Any]:
        failure_observed.set()
        raise VoiceReadinessError("tts", "TTS unavailable. Continue in text mode.")

    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    session.datachannel = channel

    async def fake_deepgram(_url, _key, _audio_queue, _callback, *, on_connected) -> None:
        await on_connected()
        await asyncio.Event().wait()

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn([], peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=fail_readiness,
        wait_for_channel=wait_for_channel,
    )
    task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    await asyncio.wait_for(failure_observed.wait(), timeout=1)
    assert not task.done()
    assert channel.messages == []

    open_channel.set()
    await asyncio.wait_for(task, timeout=1)

    errors = [message for message in channel.messages if message["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["component"] == "tts"


@pytest.mark.asyncio
async def test_readiness_failure_exits_when_datachannel_never_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("murmur.voice.transcription.LIFECYCLE_DELIVERY_TIMEOUT_SECS", 0.01)
    channel = _FakeChannel(ready_state="connecting")

    async def fail_readiness(_peer_id: str) -> Mapping[str, Any]:
        raise VoiceReadinessError("tts", "TTS unavailable. Continue in text mode.")

    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(connectionState="connecting"),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    session.datachannel = channel

    async def fake_deepgram(_url, _key, _audio_queue, _callback, *, on_connected) -> None:
        await on_connected()
        await asyncio.Event().wait()

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn([], peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=fail_readiness,
    )

    await asyncio.wait_for(transcriber.consume(_FakeTrack(), "peer"), timeout=0.25)

    assert channel.messages == []


@pytest.mark.asyncio
async def test_stt_error_stops_later_results_and_consumer() -> None:
    confirmed: list[tuple[str, str]] = []
    events_finished = asyncio.Event()
    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    channel = _FakeChannel()
    session.datachannel = channel

    async def fake_deepgram(_url, _key, _audio_queue, callback, *, on_connected) -> None:
        await on_connected()
        await callback({"type": "Error"})
        await callback(_result("must not dispatch", is_final=True, speech_final=True, start=0.0))
        events_finished.set()

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn(confirmed, peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=_ready,
    )
    task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    await asyncio.wait_for(events_finished.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)

    assert confirmed == []
    assert [message["type"] for message in channel.messages].count("error") == 1


@pytest.mark.asyncio
async def test_pending_transcript_overflow_fails_closed_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("murmur.voice.transcription.PENDING_TRANSCRIPT_MAX_SEGMENTS", 1)
    confirmed, channel, _ = await _run_events(
        [
            _result("one", is_final=True, start=0.0),
            _result("two", is_final=True, start=0.5),
        ]
    )

    assert confirmed == []
    error = next(message for message in channel.messages if message["type"] == "error")
    assert error["code"] == "stt_eot_missing"


@pytest.mark.asyncio
async def test_pending_transcript_age_uses_injected_clock_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("murmur.voice.transcription.PENDING_TRANSCRIPT_MAX_AGE_SECS", 1.0)
    current = 10.0

    def clock() -> float:
        return current

    advanced = asyncio.Event()
    confirmed: list[tuple[str, str]] = []
    runtime = RuntimeRegistry()
    session = runtime.register_voice(
        "peer",
        SimpleNamespace(),
        user_id="owner",
        agent_id=None,
        persistent_session_id=None,
        canvas_mode=False,
    )
    channel = _FakeChannel()
    session.datachannel = channel

    async def fake_deepgram(_url, _key, audio_queue, callback, *, on_connected) -> None:
        nonlocal current
        await on_connected()
        await callback(_result("unfinished", is_final=True, start=0.0))
        current = 12.0
        advanced.set()
        while await audio_queue.get() is not None:
            pass

    transcriber = VoiceTranscriber(
        runtime,
        analyzer_provider=lambda: None,
        confirmed_turn_handler=lambda peer_id, text: _append_turn(confirmed, peer_id, text),
        deepgram_streamer=fake_deepgram,
        readiness_check=_ready,
        clock=clock,
    )
    task = asyncio.create_task(transcriber.consume(_FakeTrack(), "peer"))
    await asyncio.wait_for(advanced.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)

    assert confirmed == []
    error = next(message for message in channel.messages if message["type"] == "error")
    assert error["code"] == "stt_eot_missing"


@pytest.mark.asyncio
async def test_readiness_failure_never_emits_ready_and_offers_text_fallback() -> None:
    async def fail_readiness(_peer_id: str) -> Mapping[str, Any]:
        raise VoiceReadinessError(
            "tts",
            "The selected speech provider is unavailable. Continue in text mode.",
        )

    confirmed, channel, _ = await _run_events(
        [_result("must not run", is_final=True, speech_final=True)],
        readiness_check=fail_readiness,
    )

    assert confirmed == []
    assert not any(message["type"] == "ready" for message in channel.messages)
    error = next(message for message in channel.messages if message["type"] == "error")
    assert error == {
        "type": "error",
        "code": "voice_unavailable",
        "component": "tts",
        "message": "The selected speech provider is unavailable. Continue in text mode.",
        "fallback": "text",
        "recoverable": False,
    }


@pytest.mark.asyncio
async def test_stt_connection_failure_is_explicit_and_never_emits_ready() -> None:
    _, channel, _ = await _run_events(
        [],
        connect_stt=False,
        return_after_events=True,
    )

    assert not any(message["type"] == "ready" for message in channel.messages)
    error = next(message for message in channel.messages if message["type"] == "error")
    assert error["code"] == "voice_unavailable"
    assert error["component"] == "stt"
    assert error["fallback"] == "text"

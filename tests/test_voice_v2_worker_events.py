"""Focused policy, readiness, and serialized Voice V2 worker-event tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace

import murmur.voice.worker_session as worker_session
import pytest
from livekit import rtc
from livekit.agents.types import NOT_GIVEN
from murmur.voice.bootstrap_contracts import VOICE_V2_EVENT_TOPIC, VoiceJobMetadata
from murmur.voice.contracts import EventEnvelope, EventType
from murmur.voice.profile import (
    PreparedVoiceProfile,
    ProfilePreflight,
    VoiceMediaPolicy,
    VoiceSessionPolicy,
)
from murmur.voice.worker_contracts import VoiceSessionLifecycleError, VoiceWorkerSettings
from murmur.voice.worker_events import AgentSessionEventBridge, VoiceEventChannel
from murmur.voice.worker_runtime import build_entrypoint
from murmur.voice.worker_session import AgentSessionOwner, livekit_session_factory

FIXED_NOW = datetime(2026, 8, 12, 10, 30, tzinfo=UTC)
PARTICIPANT_IDENTITY = "user-a1b2c3"


def _metadata() -> VoiceJobMetadata:
    return VoiceJobMetadata(
        agent_id="agent-1",
        agent_participant_identity="agent-a1b2c3",
        environment="test",
        event_topic=VOICE_V2_EVENT_TOPIC,
        job_expires_at=1_786_500_300,
        job_issued_at=1_786_500_000,
        participant_identity=PARTICIPANT_IDENTITY,
        profile_id="fake-rtc-v1",
        room_name="murmur-test-room-1",
        runtime="livekit_v2",
        session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        voice_call_id="call-1",
        worker_name="murmur-voice-v2",
    )


def _preflight() -> ProfilePreflight:
    return ProfilePreflight(
        profile_id="fake-rtc-v1",
        required_components=("stt", "llm", "tts", "fake_media"),
        ready_components=("stt", "llm", "tts", "fake_media"),
    )


def _prepared(*, explicit_policy: bool = True) -> PreparedVoiceProfile:
    return PreparedVoiceProfile(
        profile_id="fake-rtc-v1",
        instructions="Answer deterministically.",
        stt=object(),
        llm=object(),
        tts=object(),
        vad=None,
        session_policy=VoiceSessionPolicy() if explicit_policy else None,
        media_policy=VoiceMediaPolicy() if explicit_policy else None,
    )


class FakePublisher:
    def __init__(
        self,
        lifecycle: list[str] | None = None,
        *,
        fail_on: EventType | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.lifecycle = lifecycle
        self.fail_on = fail_on

    async def publish_data(
        self,
        payload: bytes | str,
        *,
        reliable: bool,
        destination_identities: list[str],
        topic: str,
    ) -> None:
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0)
            event = EventEnvelope.model_validate_json(payload)
            if event.event_type is self.fail_on:
                raise RuntimeError("injected publish failure")
            self.calls.append(
                {
                    "event": event,
                    "reliable": reliable,
                    "destination_identities": list(destination_identities),
                    "topic": topic,
                }
            )
            if self.lifecycle is not None:
                self.lifecycle.append(f"publish:{event.event_type.value}")
        finally:
            self.concurrent -= 1


class FakeEventSource:
    def __init__(self) -> None:
        self.listeners: dict[str, list[Callable[[object], None]]] = {}

    def on(self, event: str, callback: Callable[[object], None]) -> object:
        self.listeners.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: Callable[[object], None]) -> None:
        self.listeners[event].remove(callback)

    def emit(self, event: str, value: object) -> None:
        for callback in list(self.listeners.get(event, [])):
            callback(value)

    def listener_count(self, event: str) -> int:
        return len(self.listeners.get(event, []))


class FakeSpeechHandle:
    def __init__(self, speech_id: str) -> None:
        self.id = speech_id
        self.interrupted = False
        self._done = False
        self._exception: BaseException | None = None
        self._callbacks: list[Callable[[FakeSpeechHandle], None]] = []

    def done(self) -> bool:
        return self._done

    def exception(self) -> BaseException | None:
        if not self._done:
            raise asyncio.InvalidStateError
        return self._exception

    def add_done_callback(self, callback: Callable[[FakeSpeechHandle], None]) -> None:
        self._callbacks.append(callback)

    def remove_done_callback(self, callback: Callable[[FakeSpeechHandle], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def finish(
        self,
        *,
        interrupted: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.interrupted = interrupted
        self._exception = error
        self._done = True
        for callback in list(self._callbacks):
            callback(self)

    @property
    def callback_count(self) -> int:
        return len(self._callbacks)


def _events(publisher: FakePublisher) -> list[EventEnvelope]:
    return [call["event"] for call in publisher.calls]  # type: ignore[misc]


def test_profile_policies_are_optional_frozen_and_fail_closed() -> None:
    assert _prepared(explicit_policy=False).session_policy is None
    assert _prepared(explicit_policy=False).media_policy is None
    with pytest.raises(FrozenInstanceError):
        VoiceSessionPolicy().turn_detection = "stt"
    with pytest.raises(ValueError, match="finite non-negative"):
        VoiceSessionPolicy(min_endpointing_delay_seconds=float("nan"))
    with pytest.raises(ValueError, match="below minimum"):
        VoiceSessionPolicy(
            min_endpointing_delay_seconds=0.2,
            max_endpointing_delay_seconds=0.1,
        )
    with pytest.raises(ValueError, match="positive integer"):
        VoiceMediaPolicy(input_sample_rate=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="contract identifier"):
        VoiceMediaPolicy(output_track_name="not a track name")
    with pytest.raises(ValueError, match="session_policy"):
        PreparedVoiceProfile(
            profile_id="fake-rtc-v1",
            instructions="Answer.",
            stt=object(),
            llm=object(),
            tts=object(),
            session_policy=object(),  # type: ignore[arg-type]
        )


def test_livekit_factory_maps_only_explicit_session_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    class CapturingSession:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(worker_session, "AgentSession", CapturingSession)
    monkeypatch.setattr(
        worker_session,
        "Agent",
        lambda *, instructions: SimpleNamespace(instructions=instructions),
    )

    livekit_session_factory(_prepared(explicit_policy=False))
    livekit_session_factory(_prepared())

    assert captured[0].keys() == {"stt", "llm", "tts", "vad"}
    assert captured[0]["vad"] is None
    assert captured[1]["vad"] is None
    assert captured[1]["aec_warmup_duration"] == 0.0
    assert captured[1]["turn_handling"] == {
        "turn_detection": "stt",
        "endpointing": {"mode": "fixed", "min_delay": 0.0, "max_delay": 0.0},
        "interruption": {
            "enabled": True,
            "min_duration": 0.0,
            "min_words": 0,
            "resume_false_interruption": False,
            "false_interruption_timeout": None,
        },
        "preemptive_generation": {"enabled": False},
    }


class FakeRoomIO:
    def __init__(self, lifecycle: list[str], ready: asyncio.Event | None = None) -> None:
        self.lifecycle = lifecycle
        self.ready = ready

    async def wait_for_ready(self) -> None:
        self.lifecycle.append("room_io_wait")
        if self.ready is not None:
            await self.ready.wait()
        self.lifecycle.append("room_io_ready")


class FakeOwnedSession(FakeEventSource):
    def __init__(
        self,
        lifecycle: list[str],
        *,
        ready: asyncio.Event | None = None,
        emit_transcript_on_start: bool = False,
        close_on_start: object | None = None,
        close_soon_on_start: object | None = None,
    ) -> None:
        super().__init__()
        self.lifecycle = lifecycle
        self.room_io = FakeRoomIO(lifecycle, ready)
        self.room_options: object | None = None
        self.emit_transcript_on_start = emit_transcript_on_start
        self.close_on_start = close_on_start
        self.close_soon_on_start = close_soon_on_start
        self.shutdown_calls: list[bool] = []
        self.closed = 0

    async def start(self, agent: object, *, room: object, room_options: object) -> None:
        del agent, room
        self.room_options = room_options
        self.lifecycle.append("session_start")
        if self.emit_transcript_on_start:
            self.emit(
                "user_input_transcribed",
                SimpleNamespace(transcript="hello tutor", is_final=True, item_id="segment-1"),
            )
        if self.close_on_start is not None:
            self.emit("close", self.close_on_start)
        if self.close_soon_on_start is not None:
            asyncio.get_running_loop().call_soon(
                self.emit,
                "close",
                self.close_soon_on_start,
            )

    async def interrupt(self, *, force: bool = False) -> None:
        del force

    def shutdown(self, *, drain: bool = True) -> None:
        self.shutdown_calls.append(drain)

    async def aclose(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_owner_waits_for_public_room_io_and_maps_exact_media_policy() -> None:
    lifecycle: list[str] = []
    ready = asyncio.Event()
    session = FakeOwnedSession(lifecycle, ready=ready)
    owner = AgentSessionOwner(
        _prepared(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        cleanup_timeout_seconds=0.1,
        interruption_timeout_seconds=0.1,
    )

    started = asyncio.create_task(
        owner.start(room=object(), participant_identity=PARTICIPANT_IDENTITY)
    )
    for _ in range(100):
        if "room_io_wait" in lifecycle:
            break
        await asyncio.sleep(0)

    assert lifecycle == ["session_start", "room_io_wait"]
    assert started.done() is False
    assert owner.started is False
    ready.set()
    await started

    options = session.room_options
    assert options is not None
    assert options.participant_identity == PARTICIPANT_IDENTITY
    assert options.text_input is False
    assert options.text_output is False
    assert options.audio_input.sample_rate == 16_000
    assert options.audio_input.num_channels == 1
    assert options.audio_input.frame_size_ms == 20
    assert options.audio_input.noise_cancellation is None
    assert options.audio_input.auto_gain_control is False
    assert options.audio_input.pre_connect_audio is True
    assert options.audio_output.sample_rate == 24_000
    assert options.audio_output.num_channels == 1
    assert options.audio_output.track_publish_options.source == rtc.TrackSource.SOURCE_MICROPHONE
    assert options.audio_output.track_name == "murmur_voice_v2_audio"
    assert owner.started is True
    assert lifecycle[-1] == "room_io_ready"
    await owner.close()

    default_session = FakeOwnedSession([])
    default_owner = AgentSessionOwner(
        _prepared(explicit_policy=False),
        session_factory=lambda _: (default_session, object()),  # type: ignore[arg-type]
        cleanup_timeout_seconds=0.1,
        interruption_timeout_seconds=0.1,
    )
    await default_owner.start(room=object(), participant_identity=PARTICIPANT_IDENTITY)
    assert default_session.room_options.audio_input is NOT_GIVEN
    assert default_session.room_options.audio_output is NOT_GIVEN
    assert default_session.room_options.text_input is False
    assert default_session.room_options.text_output is NOT_GIVEN
    await default_owner.close()


@pytest.mark.asyncio
async def test_event_channel_serializes_targets_and_readies_before_buffered_events() -> None:
    publisher = FakePublisher()
    event_ids = iter(("event-1", "event-2", "event-3"))
    channel = VoiceEventChannel(
        publisher,
        _metadata(),
        clock=lambda: FIXED_NOW,
        event_id_factory=lambda: next(event_ids),
    )
    channel.emit(
        EventType.TRANSCRIPT_SEGMENT,
        {"segment_id": "segment-1", "text": "hello", "is_final": False},
    )
    channel.emit(EventType.TURN_COMMITTED, {"text": "hello"}, turn_id="turn-1")

    await channel.activate(_preflight())

    events = _events(publisher)
    assert [event.event_type for event in events] == [
        EventType.AGENT_READY,
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TURN_COMMITTED,
    ]
    assert [event.producer_sequence for event in events] == [1, 2, 3]
    assert [event.event_id for event in events] == ["event-1", "event-2", "event-3"]
    assert publisher.max_concurrent == 1
    assert channel.producer_sequence == 3
    assert all(call["reliable"] is True for call in publisher.calls)
    assert all(call["destination_identities"] == [PARTICIPANT_IDENTITY] for call in publisher.calls)
    assert all(call["topic"] == VOICE_V2_EVENT_TOPIC for call in publisher.calls)
    await channel.close()
    await channel.close()
    assert channel.closed is True


@pytest.mark.asyncio
async def test_event_writer_failure_drains_queue_and_never_hangs_idle_waiters() -> None:
    publisher = FakePublisher(fail_on=EventType.TRANSCRIPT_SEGMENT)
    channel = VoiceEventChannel(publisher, _metadata())
    channel.emit(
        EventType.TRANSCRIPT_SEGMENT,
        {"segment_id": "segment-1", "text": "hello", "is_final": True},
    )
    channel.emit(EventType.TURN_COMMITTED, {"text": "hello"}, turn_id="turn-1")

    with pytest.raises(VoiceSessionLifecycleError, match="publication failed"):
        await asyncio.wait_for(channel.activate(_preflight()), timeout=0.1)
    with pytest.raises(VoiceSessionLifecycleError, match="publication failed"):
        await asyncio.wait_for(channel.wait_for_idle(), timeout=0.1)

    assert [event.event_type for event in _events(publisher)] == [EventType.AGENT_READY]
    assert channel.producer_sequence == 2
    await asyncio.gather(channel.close(), channel.close())
    assert channel.closed is True


@pytest.mark.asyncio
async def test_event_channel_failure_signal_is_sticky_and_single_assignment() -> None:
    channel = VoiceEventChannel(FakePublisher(), _metadata())
    first = RuntimeError("first failure")

    channel.fail(first)
    channel.fail(RuntimeError("later failure"))

    with pytest.raises(VoiceSessionLifecycleError, match="publication failed") as captured:
        await asyncio.wait_for(channel.wait_for_failure(), timeout=0.1)
    assert captured.value.__cause__ is first
    await channel.close()


@pytest.mark.asyncio
async def test_public_session_bridge_waits_for_handle_done_before_stopping() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    handle = FakeSpeechHandle("speech-1")
    bridge.bind()

    session.emit(
        "user_input_transcribed",
        SimpleNamespace(transcript="hello", is_final=True, item_id="segment-1"),
    )
    # Exercise the FIFO in the opposite order: speech handle before committed turn.
    session.emit("speech_created", SimpleNamespace(speech_handle=handle))
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-1", text_content="hello tutor")),
    )
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="speaking", new_state="listening"),
    )

    await channel.activate(_preflight())

    assert [event.event_type for event in _events(publisher)] == [
        EventType.AGENT_READY,
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TURN_COMMITTED,
        EventType.ASSISTANT_SPEECH_STARTED,
    ]
    assert handle.callback_count == 1

    handle.finish()
    await channel.wait_for_idle()

    stopped = _events(publisher)[-1]
    assert stopped.event_type is EventType.ASSISTANT_SPEECH_STOPPED
    assert stopped.turn_id == "turn-1"
    assert stopped.payload == {"speech_id": "speech-1", "reason": "completed"}
    assert handle.callback_count == 0

    bridge.close()
    bridge.close()
    assert all(
        session.listener_count(event_name) == 0
        for event_name in AgentSessionEventBridge._EVENT_CALLBACK_NAMES
    )
    published = len(publisher.calls)
    session.emit(
        "user_input_transcribed",
        SimpleNamespace(transcript="ignored", is_final=True, item_id="segment-2"),
    )
    await channel.wait_for_idle()
    assert len(publisher.calls) == published
    await channel.close()


@pytest.mark.parametrize(
    ("interrupted", "error", "expected_reason"),
    [
        (False, None, "completed"),
        (True, None, "interrupted"),
        (False, RuntimeError("tts failed"), "error"),
    ],
)
@pytest.mark.asyncio
async def test_public_session_bridge_maps_handle_terminal_reason(
    interrupted: bool,
    error: BaseException | None,
    expected_reason: str,
) -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    handle = FakeSpeechHandle("speech-1")
    bridge.bind()
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-1", text_content="hello")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=handle))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    handle.finish(interrupted=interrupted, error=error)

    await channel.activate(_preflight())

    stopped = _events(publisher)[-1]
    assert stopped.event_type is EventType.ASSISTANT_SPEECH_STOPPED
    assert stopped.payload["reason"] == expected_reason
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_terminalizes_failed_speech_before_speaking_without_stealing_next_turn() -> (
    None
):
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    failed = FakeSpeechHandle("speech-failed")
    actual = FakeSpeechHandle("speech-actual")
    bridge.bind()

    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-failed", text_content="first")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=failed))
    failed.finish(error=RuntimeError("tts failed before playout"))
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-actual", text_content="second")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=actual))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    actual.finish()

    await channel.activate(_preflight())

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [
        (
            event.event_type,
            event.turn_id,
            event.payload["speech_id"],
            event.payload.get("reason"),
        )
        for event in speech_events
    ] == [
        (EventType.ASSISTANT_SPEECH_STOPPED, "turn-failed", "speech-failed", "error"),
        (EventType.ASSISTANT_SPEECH_STARTED, "turn-actual", "speech-actual", None),
        (EventType.ASSISTANT_SPEECH_STOPPED, "turn-actual", "speech-actual", "completed"),
    ]
    assert failed.callback_count == 0
    assert actual.callback_count == 0
    bridge.close()
    await channel.close()


@pytest.mark.parametrize(
    ("interrupted", "error", "expected_reason"),
    [
        (True, None, "interrupted"),
        (False, RuntimeError("tts failed"), "error"),
    ],
)
@pytest.mark.asyncio
async def test_bridge_terminalizes_paired_handle_that_finishes_before_speaking(
    interrupted: bool,
    error: BaseException | None,
    expected_reason: str,
) -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    handle = FakeSpeechHandle("speech-early-terminal")
    bridge.bind()

    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-1", text_content="hello")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=handle))
    handle.finish(interrupted=interrupted, error=error)
    await channel.activate(_preflight())

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert len(speech_events) == 1
    assert speech_events[0].event_type is EventType.ASSISTANT_SPEECH_STOPPED
    assert speech_events[0].turn_id == "turn-1"
    assert speech_events[0].payload == {
        "speech_id": "speech-early-terminal",
        "reason": expected_reason,
    }
    assert handle.callback_count == 0
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_discards_unpaired_speech_that_finishes_before_turn() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    abandoned = FakeSpeechHandle("speech-abandoned")
    actual = FakeSpeechHandle("speech-actual")
    bridge.bind()

    session.emit("speech_created", SimpleNamespace(speech_handle=abandoned))
    abandoned.finish(interrupted=True)
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-actual", text_content="hello")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=actual))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    actual.finish()

    await channel.activate(_preflight())

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [event.payload["speech_id"] for event in speech_events] == [
        "speech-actual",
        "speech-actual",
    ]
    assert all(event.turn_id == "turn-actual" for event in speech_events)
    assert abandoned.callback_count == 0
    assert actual.callback_count == 0
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_clears_orphan_speaking_token_when_unpaired_handle_finishes() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    abandoned = FakeSpeechHandle("speech-abandoned")
    actual = FakeSpeechHandle("speech-actual")
    bridge.bind()

    session.emit("speech_created", SimpleNamespace(speech_handle=abandoned))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    abandoned.finish(error=RuntimeError("failed before turn association"))
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-actual", text_content="hello")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=actual))

    await channel.activate(_preflight())
    assert not any(
        event.event_type is EventType.ASSISTANT_SPEECH_STARTED for event in _events(publisher)
    )

    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    actual.finish()
    await channel.wait_for_idle()

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [event.payload["speech_id"] for event in speech_events] == [
        "speech-actual",
        "speech-actual",
    ]
    assert all(event.turn_id == "turn-actual" for event in speech_events)
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_skips_interrupted_preemptive_handle_before_pairing_replacement() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    stale = FakeSpeechHandle("speech-stale")
    replacement = FakeSpeechHandle("speech-replacement")
    bridge.bind()

    session.emit("speech_created", SimpleNamespace(speech_handle=stale))
    stale.interrupted = True
    session.emit("speech_created", SimpleNamespace(speech_handle=replacement))
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-1", text_content="hello")),
    )
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    replacement.finish()
    stale.finish(interrupted=True)
    await channel.activate(_preflight())

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [event.payload["speech_id"] for event in speech_events] == [
        "speech-replacement",
        "speech-replacement",
    ]
    assert all(event.turn_id == "turn-1" for event in speech_events)
    assert stale.callback_count == 0
    assert replacement.callback_count == 0
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_defers_pending_terminal_stop_until_active_speech_finishes() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    active = FakeSpeechHandle("speech-active")
    failed = FakeSpeechHandle("speech-failed")
    bridge.bind()

    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-active", text_content="first")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=active))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-failed", text_content="second")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=failed))
    failed.finish(error=RuntimeError("failed before playout"))
    await channel.activate(_preflight())

    assert [
        event.payload["speech_id"]
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ] == ["speech-active"]

    active.finish()
    await channel.wait_for_idle()
    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [
        (event.turn_id, event.payload["speech_id"], event.payload.get("reason"))
        for event in speech_events
    ] == [
        ("turn-active", "speech-active", None),
        ("turn-active", "speech-active", "completed"),
        ("turn-failed", "speech-failed", "error"),
    ]
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_drops_orphan_speaking_token_with_finished_unpaired_speech() -> None:
    publisher = FakePublisher()
    channel = VoiceEventChannel(publisher, _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel)
    abandoned = FakeSpeechHandle("speech-abandoned")
    actual = FakeSpeechHandle("speech-actual")
    bridge.bind()

    session.emit("speech_created", SimpleNamespace(speech_handle=abandoned))
    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    abandoned.finish(error=RuntimeError("failed before turn correlation"))
    session.emit(
        "conversation_item_added",
        SimpleNamespace(item=SimpleNamespace(role="user", id="turn-actual", text_content="hello")),
    )
    session.emit("speech_created", SimpleNamespace(speech_handle=actual))

    await channel.activate(_preflight())
    assert not any(
        event.event_type is EventType.ASSISTANT_SPEECH_STARTED for event in _events(publisher)
    )

    session.emit(
        "agent_state_changed",
        SimpleNamespace(old_state="thinking", new_state="speaking"),
    )
    actual.finish()
    await channel.wait_for_idle()

    speech_events = [
        event
        for event in _events(publisher)
        if event.event_type
        in {EventType.ASSISTANT_SPEECH_STARTED, EventType.ASSISTANT_SPEECH_STOPPED}
    ]
    assert [event.payload["speech_id"] for event in speech_events] == [
        "speech-actual",
        "speech-actual",
    ]
    assert all(event.turn_id == "turn-actual" for event in speech_events)
    assert abandoned.callback_count == 0
    assert actual.callback_count == 0
    bridge.close()
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_fifo_overflow_fails_channel_without_escaping_sdk_callback() -> None:
    channel = VoiceEventChannel(FakePublisher(), _metadata())
    session = FakeEventSource()
    bridge = AgentSessionEventBridge(session, channel, max_pending_speeches=1)
    first = FakeSpeechHandle("speech-1")
    bridge.bind()

    session.emit("speech_created", SimpleNamespace(speech_handle=first))
    session.emit("speech_created", SimpleNamespace(speech_handle=FakeSpeechHandle("speech-2")))

    with pytest.raises(VoiceSessionLifecycleError, match="publication failed"):
        await channel.activate(_preflight())
    bridge.close()
    assert first.callback_count == 0
    await channel.close()


@pytest.mark.asyncio
async def test_bridge_bind_rolls_back_partially_registered_public_callbacks() -> None:
    class FailingEventSource(FakeEventSource):
        def on(self, event: str, callback: Callable[[object], None]) -> object:
            if event == "speech_created":
                raise RuntimeError("injected bind failure")
            return super().on(event, callback)

    channel = VoiceEventChannel(FakePublisher(), _metadata())
    session = FailingEventSource()
    bridge = AgentSessionEventBridge(session, channel)

    with pytest.raises(RuntimeError, match="bind failure"):
        bridge.bind()

    assert bridge.bound is False
    assert all(
        session.listener_count(event_name) == 0
        for event_name in AgentSessionEventBridge._EVENT_CALLBACK_NAMES
    )
    bridge.close()
    await channel.close()


class FakeRuntimeRoom(FakeEventSource):
    def __init__(self, publisher: FakePublisher) -> None:
        super().__init__()
        self.local_participant = publisher


class FakeRuntimeParticipant:
    def __init__(self) -> None:
        self.identity = PARTICIPANT_IDENTITY
        self.track_publications = {
            "microphone": SimpleNamespace(
                source=rtc.TrackSource.SOURCE_MICROPHONE,
                track=object(),
            )
        }


class FakeRuntimeContext:
    def __init__(self, publisher: FakePublisher, lifecycle: list[str]) -> None:
        self.job = object()
        self.room = FakeRuntimeRoom(publisher)
        self.lifecycle = lifecycle
        self.shutdown_callbacks: list[Callable[[], object]] = []
        self.shutdown_reasons: list[str] = []

    async def connect(self, *, auto_subscribe: object) -> None:
        del auto_subscribe
        self.lifecycle.append("connect")

    async def wait_for_participant(self, *, identity: str) -> FakeRuntimeParticipant:
        assert identity == PARTICIPANT_IDENTITY
        self.lifecycle.append("participant_joined")
        return FakeRuntimeParticipant()

    def add_shutdown_callback(self, callback: Callable[[], object]) -> None:
        self.shutdown_callbacks.append(callback)

    def shutdown(self, reason: str = "user requested") -> None:
        self.shutdown_reasons.append(reason)


def _settings() -> VoiceWorkerSettings:
    return VoiceWorkerSettings(
        signing_secret="worker-signing-secret-that-is-long-enough",
        environment="test",
        profile_id="fake-rtc-v1",
        worker_name="murmur-voice-v2",
        event_topic=VOICE_V2_EVENT_TOPIC,
        repository_timeout_seconds=0.1,
        preflight_timeout_seconds=0.1,
        connect_timeout_seconds=0.1,
        participant_wait_timeout_seconds=0.1,
        input_wait_timeout_seconds=0.1,
        session_start_timeout_seconds=0.1,
        event_publish_timeout_seconds=0.1,
        cleanup_timeout_seconds=0.1,
        interruption_timeout_seconds=0.1,
    )


@pytest.mark.asyncio
async def test_runtime_owns_bridge_channel_and_publishes_ready_after_room_io() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle)
    session = FakeOwnedSession(lifecycle, emit_transcript_on_start=True)
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)

    await entrypoint(context)  # type: ignore[arg-type]

    assert lifecycle == [
        "connect",
        "participant_joined",
        "session_start",
        "room_io_wait",
        "room_io_ready",
        "publish:agent_ready",
        "publish:transcript_segment",
    ]
    assert [event.producer_sequence for event in _events(publisher)] == [1, 2]
    assert len(context.shutdown_callbacks) == 1
    await asyncio.gather(
        context.shutdown_callbacks[0](),  # type: ignore[misc]
        context.shutdown_callbacks[0](),  # type: ignore[misc]
    )
    assert session.shutdown_calls == [False]
    assert session.closed == 1
    assert all(
        session.listener_count(event_name) == 0
        for event_name in AgentSessionEventBridge._EVENT_CALLBACK_NAMES
    )
    assert context.shutdown_reasons == []


@pytest.mark.asyncio
async def test_runtime_session_close_during_start_never_publishes_ready() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle)
    session = FakeOwnedSession(
        lifecycle,
        close_on_start=SimpleNamespace(
            reason=SimpleNamespace(value="error"),
            error=RuntimeError("startup provider failed"),
        ),
    )
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)

    with pytest.raises(VoiceSessionLifecycleError, match="closed before readiness"):
        await entrypoint(context)  # type: ignore[arg-type]

    assert publisher.calls == []
    assert session.shutdown_calls == [False]
    assert session.closed == 1
    assert all(
        session.listener_count(event_name) == 0
        for event_name in AgentSessionEventBridge._EVENT_CALLBACK_NAMES
    )


@pytest.mark.asyncio
async def test_runtime_queued_session_close_cannot_race_ready_enqueue() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle)
    session = FakeOwnedSession(
        lifecycle,
        close_soon_on_start=SimpleNamespace(
            reason=SimpleNamespace(value="error"),
            error=RuntimeError("queued startup failure"),
        ),
    )
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)

    with pytest.raises(VoiceSessionLifecycleError, match="closed before readiness"):
        await entrypoint(context)  # type: ignore[arg-type]

    assert publisher.calls == []
    assert session.shutdown_calls == [False]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_runtime_post_ready_event_failure_requests_shutdown_and_cleans_once() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle, fail_on=EventType.TRANSCRIPT_SEGMENT)
    session = FakeOwnedSession(lifecycle)
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)
    await entrypoint(context)  # type: ignore[arg-type]

    session.emit(
        "user_input_transcribed",
        SimpleNamespace(transcript="late", is_final=True, item_id="segment-late"),
    )
    session.emit(
        "user_input_transcribed",
        SimpleNamespace(transcript="later", is_final=True, item_id="segment-later"),
    )
    for _ in range(100):
        if context.shutdown_reasons:
            break
        await asyncio.sleep(0)

    assert context.shutdown_reasons == ["voice event channel failed"]
    await asyncio.gather(
        context.shutdown_callbacks[0](),  # type: ignore[misc]
        context.shutdown_callbacks[0](),  # type: ignore[misc]
    )
    assert session.shutdown_calls == [False]
    assert session.closed == 1
    assert all(
        session.listener_count(event_name) == 0
        for event_name in AgentSessionEventBridge._EVENT_CALLBACK_NAMES
    )


@pytest.mark.asyncio
async def test_runtime_normal_shutdown_cancels_pending_event_failure_monitor() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle)
    session = FakeOwnedSession(lifecycle)
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)
    await entrypoint(context)  # type: ignore[arg-type]

    await asyncio.wait_for(context.shutdown_callbacks[0](), timeout=0.1)  # type: ignore[misc]

    assert context.shutdown_reasons == []
    assert session.shutdown_calls == [False]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_runtime_session_close_publishes_unavailable_then_requests_shutdown() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle)
    session = FakeOwnedSession(lifecycle)
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type]
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)
    await entrypoint(context)  # type: ignore[arg-type]

    session.emit(
        "close",
        SimpleNamespace(reason=SimpleNamespace(value="error"), error=RuntimeError("secret")),
    )
    for _ in range(100):
        if context.shutdown_reasons:
            break
        await asyncio.sleep(0)

    assert context.shutdown_reasons == ["voice agent session closed"]
    unavailable = _events(publisher)[-1]
    assert unavailable.event_type is EventType.AGENT_UNAVAILABLE
    assert unavailable.payload == {
        "code": "agent_session_closed",
        "message": "Voice agent session ended. Start a fresh voice call.",
        "retryable": True,
    }
    assert "secret" not in unavailable.model_dump_json()
    await asyncio.wait_for(context.shutdown_callbacks[0](), timeout=0.1)  # type: ignore[misc]
    assert session.shutdown_calls == [False]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_runtime_session_close_and_event_failure_race_shutdowns_once() -> None:
    lifecycle: list[str] = []
    publisher = FakePublisher(lifecycle, fail_on=EventType.AGENT_UNAVAILABLE)
    session = FakeOwnedSession(lifecycle)
    authorized = SimpleNamespace(metadata=_metadata(), profile_scope=object())

    class StubAuthorizer:
        async def authorize(self, job: object) -> object:
            del job
            return authorized

    class StubProfiles:
        async def prepare(self, scope: object) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
            del scope
            return _preflight(), _prepared()

    entrypoint = build_entrypoint(
        StubAuthorizer(),  # type: ignore[arg-type]
        StubProfiles(),  # type: ignore[arg-type],
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )
    context = FakeRuntimeContext(publisher, lifecycle)
    await entrypoint(context)  # type: ignore[arg-type]

    session.emit(
        "close",
        SimpleNamespace(reason=SimpleNamespace(value="participant_disconnected"), error=None),
    )
    for _ in range(100):
        if context.shutdown_reasons:
            break
        await asyncio.sleep(0)

    assert len(context.shutdown_reasons) == 1
    assert context.shutdown_reasons[0] in {
        "voice agent session closed",
        "voice event channel failed",
    }
    await asyncio.wait_for(context.shutdown_callbacks[0](), timeout=0.1)  # type: ignore[misc]
    assert session.shutdown_calls == [False]
    assert session.closed == 1

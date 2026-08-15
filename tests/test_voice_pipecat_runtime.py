"""Provider-free ownership, readiness, frame, and RTVI tests for Pipecat."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import murmur.voice.pipecat_runtime as pipecat_runtime_module
import pytest
from murmur.voice.contracts import EventEnvelope, EventType
from murmur.voice.pipecat_runtime import (
    CanonicalVoiceEventProcessor,
    GatedRTVIProcessor,
    PipecatEventChannel,
    PipecatRuntime,
    PipecatRuntimeError,
    PipecatRuntimeHandle,
    PipecatRuntimeStarter,
    _ReadinessGate,
)
from murmur.voice.profile import (
    ProfileReadiness,
    ProviderModelReadiness,
    VoiceConnectionPolicy,
    VoiceMediaPolicy,
    VoiceProfileScope,
    VoiceSessionPolicy,
)
from murmur.voice.provider_profiles.pipecat_cascade import (
    PIPECAT_DIRECT_CASCADE_PROFILE_ID,
    PreparedPipecatProfile,
)
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.transports.base_transport import BaseTransport

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)


def _claims() -> VoiceCallClaims:
    return VoiceCallClaims(
        user_id="firebase-user-1",
        session_id="00000000-0000-4000-8000-000000000001",
        agent_id="00000000-0000-4000-8000-000000000002",
        voice_call_id="00000000-0000-4000-8000-000000000003",
        trace_id="00000000-0000-4000-8000-000000000004",
        runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
        profile_id=PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
    )


class PassThrough(FrameProcessor):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cleanup_calls = 0

    async def cleanup(self) -> None:
        self.cleanup_calls += 1
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class FakeTransport(BaseTransport):
    def __init__(self) -> None:
        super().__init__(name="fake-smallwebrtc")
        self._input = PassThrough(name="fake-input")
        self._output = PassThrough(name="fake-output")
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output


class DeterministicSTT(FrameProcessor):
    def __init__(self, *, emit_interim: bool = True) -> None:
        super().__init__(name="deterministic-stt")
        self.emit_interim = emit_interim
        self.audio_frames = 0
        self.cleanup_calls = 0

    async def cleanup(self) -> None:
        self.cleanup_calls += 1
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.audio_frames += 1
            if self.emit_interim and self.audio_frames == 1:
                await self.push_frame(
                    InterimTranscriptionFrame("hello", "synthetic-user", NOW.isoformat())
                )
            else:
                await self.push_frame(
                    TranscriptionFrame(
                        "hello murmur",
                        "synthetic-user",
                        NOW.isoformat(),
                        finalized=True,
                    )
                )
            return
        await self.push_frame(frame, direction)


class DeterministicLLM(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(name="deterministic-llm")
        self.context_frames = 0
        self.cleanup_calls = 0

    async def cleanup(self) -> None:
        self.cleanup_calls += 1
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self.context_frames += 1
            await self.push_frame(LLMTextFrame("hello back"))
            return
        await self.push_frame(frame, direction)


class DeterministicTTS(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(name="deterministic-tts")
        self.text_frames = 0
        self.cleanup_calls = 0

    async def cleanup(self) -> None:
        self.cleanup_calls += 1
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.text_frames += 1
            await self.push_frame(TTSStartedFrame(context_id="speech-context"))
            await self.push_frame(
                TTSAudioRawFrame(
                    b"\x01\x00" * 240,
                    sample_rate=24_000,
                    num_channels=1,
                    context_id="speech-context",
                )
            )
            await self.push_frame(TTSStoppedFrame(context_id="speech-context"))
            return
        await self.push_frame(frame, direction)


class CapturingOutput(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(name="capturing-output")
        self.messages: list[dict[str, object]] = []
        self.audio_frames: list[TTSAudioRawFrame] = []
        self.cleanup_calls = 0

    async def cleanup(self) -> None:
        self.cleanup_calls += 1
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputTransportMessageUrgentFrame):
            self.messages.append(frame.message)
        elif isinstance(frame, TTSAudioRawFrame):
            if not self.audio_frames:
                await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
            self.audio_frames.append(frame)
        elif isinstance(frame, TTSStoppedFrame) and self.audio_frames:
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


class DeterministicTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self._output = CapturingOutput()


class CloseCounter:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


async def _streams_ready() -> None:
    return None


def _profile(
    close: CloseCounter | None = None,
    *,
    wait_streams_ready: object = _streams_ready,
) -> PreparedPipecatProfile:
    closer = close or CloseCounter()
    readiness = ProfileReadiness(
        profile_id=PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        required_components=("stt", "llm", "tts"),
        ready_components=("stt", "llm", "tts"),
        config_hash="a" * 64,
        provider_models=(
            ProviderModelReadiness(component="stt", provider="deepgram", model="nova-3"),
            ProviderModelReadiness(component="llm", provider="groq", model="openai/gpt-oss-120b"),
            ProviderModelReadiness(
                component="tts", provider="elevenlabs", model="eleven_flash_v2_5"
            ),
        ),
    )
    return PreparedPipecatProfile(
        profile_id=PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        instructions="Answer briefly.",
        stt=PassThrough(name="fake-stt"),
        llm=PassThrough(name="fake-llm"),
        tts=PassThrough(name="fake-tts"),
        readiness=readiness,
        close_callback=closer,
        session_policy=VoiceSessionPolicy(),
        media_policy=VoiceMediaPolicy(),
        connection_policy=VoiceConnectionPolicy(),
        wait_streams_ready=wait_streams_ready,  # type: ignore[arg-type]
    )


def _deterministic_profile(
    close: CloseCounter,
    *,
    emit_interim: bool = True,
) -> PreparedPipecatProfile:
    base = _profile(close)
    return PreparedPipecatProfile(
        profile_id=base.profile_id,
        instructions=base.instructions,
        stt=DeterministicSTT(emit_interim=emit_interim),
        llm=DeterministicLLM(),
        tts=DeterministicTTS(),
        readiness=base.readiness,
        close_callback=base.close_callback,
        session_policy=base.session_policy,
        media_policy=base.media_policy,
        connection_policy=base.connection_policy,
        wait_streams_ready=base.wait_streams_ready,
    )


@pytest.mark.parametrize(
    "timeout_seconds",
    [False, 0, -1, 900.1, float("inf"), float("nan"), "300", None],
)
def test_active_call_idle_timeout_must_be_positive_and_bounded(
    timeout_seconds: object,
) -> None:
    with pytest.raises(ValueError, match="active-call idle timeout"):
        PipecatRuntime(
            claims=_claims(),
            profile=_profile(),
            transport=FakeTransport(),
            active_call_idle_timeout_seconds=timeout_seconds,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="active-call idle timeout"):
        PipecatRuntimeStarter(
            object(),  # type: ignore[arg-type]
            lambda _claims: object(),  # type: ignore[arg-type,return-value]
            active_call_idle_timeout_seconds=timeout_seconds,  # type: ignore[arg-type]
        )


class RecordingRTVI:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_server_message(self, data: Any) -> None:
        self.messages.append(data)


class FailOnceRTVI(RecordingRTVI):
    async def send_server_message(self, data: Any) -> None:
        if not self.messages:
            self.messages.append({"failed": data})
            raise RuntimeError("synthetic server-message failure")
        self.messages.append(data)


@pytest.mark.asyncio
async def test_event_channel_buffers_then_serializes_ready_first_as_server_messages() -> None:
    rtvi = RecordingRTVI()
    channel = PipecatEventChannel(
        rtvi,  # type: ignore[arg-type]
        _claims(),
        clock=lambda: NOW,
        event_id_factory=iter(("event-ready", "event-connected")).__next__,
    )
    await channel.emit(EventType.TRANSPORT_CONNECTED, {"connection_id": "peer-1"})
    assert rtvi.messages == []

    await channel.activate(_profile())

    events = [EventEnvelope.model_validate(message) for message in rtvi.messages]
    assert [event.event_type for event in events] == [
        EventType.AGENT_READY,
        EventType.TRANSPORT_CONNECTED,
    ]
    assert [event.producer_sequence for event in events] == [1, 2]
    assert events[0].payload["profile_config_hash"] == "a" * 64
    assert events[0].payload["required_components"] == (
        "worker",
        "input",
        "output",
        "event_channel",
        "stt",
        "llm",
        "tts",
    )


@pytest.mark.asyncio
async def test_smallwebrtc_connection_id_is_stable_valid_and_never_exposes_raw_peer_id() -> None:
    raw_pc_id = "SmallWebRTCConnection#0-a8e10eabd43f4359b92dd9259874b93d"
    connection_id = pipecat_runtime_module._canonical_smallwebrtc_connection_id(raw_pc_id)

    assert connection_id == (
        "smallwebrtc-4c963a06133c8ade05602bf1c74665113a376a489ed0571b9e0439d00af3303f"
    )
    assert pipecat_runtime_module._canonical_smallwebrtc_connection_id(raw_pc_id) == connection_id
    assert raw_pc_id not in connection_id
    assert len(connection_id) == 76

    rtvi = RecordingRTVI()
    channel = PipecatEventChannel(
        rtvi,  # type: ignore[arg-type]
        _claims(),
        clock=lambda: NOW,
        event_id_factory=iter(("event-ready", "event-connected")).__next__,
    )
    await channel.emit(EventType.TRANSPORT_CONNECTED, {"connection_id": connection_id})
    await channel.activate(_profile())

    events = [EventEnvelope.model_validate(message) for message in rtvi.messages]
    assert events[1].payload["connection_id"] == connection_id
    assert raw_pc_id not in str(rtvi.messages)


@pytest.mark.asyncio
async def test_event_sequence_and_buffer_do_not_advance_on_failed_send() -> None:
    rtvi = FailOnceRTVI()
    channel = PipecatEventChannel(rtvi, _claims())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="synthetic server-message failure"):
        await channel.activate(_profile())

    assert channel.producer_sequence == 0
    assert channel.activated is False


@pytest.mark.asyncio
async def test_gated_rtvi_never_publishes_bot_ready_before_explicit_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def record_ready(_self: RTVIProcessor, about: object = None) -> None:
        calls.append(about)

    monkeypatch.setattr(RTVIProcessor, "set_bot_ready", record_ready)
    rtvi = GatedRTVIProcessor()

    await rtvi.set_bot_ready({"source": "automatic-client-ready"})
    assert calls == []

    await rtvi.release_bot_ready()
    await rtvi.release_bot_ready()
    assert calls == [{"source": "automatic-client-ready"}]


@pytest.mark.asyncio
async def test_ready_gate_requires_profile_pipeline_providers_rtc_and_client_in_any_order() -> None:
    lifecycle: list[str] = []

    class Channel:
        activated = False

        async def activate(self, _profile: PreparedPipecatProfile) -> None:
            lifecycle.append("canonical-ready")
            self.activated = True

    class RTVI:
        async def release_bot_ready(self) -> None:
            lifecycle.append("bot-ready")

    gate = _ReadinessGate(  # type: ignore[arg-type]
        _profile(),
        Channel(),
        RTVI(),
    )
    await gate.mark_client_ready()
    await gate.mark_pipeline_started()
    await gate.mark_rtc_connected()
    assert lifecycle == []

    await gate.mark_providers_ready()
    await gate.mark_providers_ready()

    assert lifecycle == ["bot-ready", "canonical-ready"]


@pytest.mark.asyncio
async def test_canonical_processor_maps_real_pipecat_frames_without_new_event_model() -> None:
    calls: list[tuple[EventType, dict[str, object], str | None]] = []

    class Channel:
        async def emit(
            self,
            event_type: EventType,
            payload: dict[str, object],
            *,
            turn_id: str | None = None,
        ) -> None:
            calls.append((event_type, payload, turn_id))

    ids = iter(("segment-1", "segment-2", "turn-1", "speech-1"))
    processor = CanonicalVoiceEventProcessor(
        Channel(),  # type: ignore[arg-type]
        contract_id_factory=lambda _prefix: next(ids),
    )

    async def discard(_frame: Frame, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]
    await processor.process_frame(
        InterimTranscriptionFrame("hel", "user", NOW.isoformat()),
        FrameDirection.DOWNSTREAM,
    )
    await processor.process_frame(
        TranscriptionFrame("hello", "user", NOW.isoformat(), finalized=True),
        FrameDirection.DOWNSTREAM,
    )
    await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    assert [event for event, _, _ in calls] == [
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TURN_COMMITTED,
        EventType.ASSISTANT_SPEECH_STARTED,
        EventType.ASSISTANT_SPEECH_STOPPED,
    ]
    assert calls[0][1]["is_final"] is False
    assert calls[1][1]["is_final"] is True
    assert calls[2] == (EventType.TURN_COMMITTED, {"text": "hello"}, "turn-1")
    assert calls[-1][1] == {"speech_id": "speech-1", "reason": "interrupted"}


@pytest.mark.asyncio
async def test_barge_in_keeps_interrupted_speech_bound_to_its_original_turn() -> None:
    calls: list[tuple[EventType, dict[str, object], str | None]] = []

    class Channel:
        async def emit(
            self,
            event_type: EventType,
            payload: dict[str, object],
            *,
            turn_id: str | None = None,
        ) -> None:
            calls.append((event_type, payload, turn_id))

    ids = iter(("segment-1", "turn-1", "speech-1", "segment-2", "turn-2"))
    processor = CanonicalVoiceEventProcessor(
        Channel(),  # type: ignore[arg-type]
        contract_id_factory=lambda _prefix: next(ids),
    )

    async def discard(_frame: Frame, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]
    await processor.process_frame(
        TranscriptionFrame("first", "user", NOW.isoformat(), finalized=True),
        FrameDirection.DOWNSTREAM,
    )
    await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(
        TranscriptionFrame("second", "user", NOW.isoformat(), finalized=True),
        FrameDirection.DOWNSTREAM,
    )
    await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    stopped = [call for call in calls if call[0] is EventType.ASSISTANT_SPEECH_STOPPED]
    assert stopped == [
        (
            EventType.ASSISTANT_SPEECH_STOPPED,
            {"speech_id": "speech-1", "reason": "interrupted"},
            "turn-1",
        )
    ]
    assert [call[2] for call in calls if call[0] is EventType.TURN_COMMITTED] == [
        "turn-1",
        "turn-2",
    ]


@pytest.mark.asyncio
async def test_runtime_constructs_one_public_worker_and_runner_and_closes_profile_once() -> None:
    close = CloseCounter()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(close),
        transport=FakeTransport(),
    )

    assert runtime.worker.name.endswith(_claims().voice_call_id)
    assert runtime.runner.name.endswith(_claims().voice_call_id)
    assert runtime.worker.rtvi is runtime.rtvi
    assert runtime.worker._idle_timeout_secs == 300.0  # type: ignore[attr-defined]
    assert len(runtime._pipeline.processors) == 12  # source + ten stages + sink
    strategies = runtime._turn_processor._user_turn_controller._user_turn_strategies
    assert [type(item).__name__ for item in strategies.start] == [
        "TranscriptionUserTurnStartStrategy"
    ]
    assert [type(item).__name__ for item in strategies.stop] == [
        "SpeechTimeoutUserTurnStopStrategy"
    ]

    await asyncio.gather(runtime.close(), runtime.close())
    assert close.calls == 1


@pytest.mark.asyncio
async def test_idle_call_timeout_terminates_run_cleans_pipeline_and_releases_observer() -> None:
    close = CloseCounter()
    profile = _profile(close)
    transport = FakeTransport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=profile,
        transport=transport,
        readiness_timeout_seconds=1,
        active_call_idle_timeout_seconds=0.02,
    )
    idle_detected = asyncio.Event()

    @runtime.worker.event_handler("on_idle_timeout")
    async def record_idle_timeout(_worker: object) -> None:
        idle_detected.set()

    run_task = asyncio.create_task(runtime.run())
    handle = PipecatRuntimeHandle(runtime, run_task, close_timeout_seconds=1)
    observer_released = asyncio.Event()

    async def observe_runtime() -> None:
        await handle.wait_closed()
        observer_released.set()

    observer_task = asyncio.create_task(observe_runtime())
    await asyncio.wait_for(idle_detected.wait(), timeout=2)
    await asyncio.wait_for(observer_released.wait(), timeout=2)
    await observer_task

    assert handle.done is True
    assert runtime.worker.murmur_cleanup_complete is True  # type: ignore[attr-defined]
    assert profile.stt.cleanup_calls == 1  # type: ignore[attr-defined]
    assert profile.llm.cleanup_calls == 1  # type: ignore[attr-defined]
    assert profile.tts.cleanup_calls == 1  # type: ignore[attr-defined]
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1
    assert close.calls == 0
    await handle.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_kind", ["transcription", "user_turn", "bot"])
async def test_real_activity_resets_no_vad_idle_timeout(activity_kind: str) -> None:
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(),
        transport=FakeTransport(),
        readiness_timeout_seconds=2,
        active_call_idle_timeout_seconds=0.18,
    )
    started = asyncio.Event()
    idle_detected = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    @runtime.worker.event_handler("on_idle_timeout")
    async def record_idle_timeout(_worker: object) -> None:
        idle_detected.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    for index in range(6):
        if activity_kind == "transcription":
            frame: Frame = InterimTranscriptionFrame(
                f"partial-{index}",
                "synthetic-user",
                NOW.isoformat(),
            )
        elif activity_kind == "user_turn":
            frame = UserStartedSpeakingFrame() if index % 2 == 0 else UserStoppedSpeakingFrame()
        else:
            frame = BotSpeakingFrame()
        await runtime.worker.queue_frame(frame)
        await asyncio.sleep(0.05)
        assert not idle_detected.is_set()
        assert not run_task.done()

    # Each activity kind independently lasts beyond one idle interval. Only
    # subsequent silence is allowed to expire the active-call budget.
    await asyncio.wait_for(idle_detected.wait(), timeout=2)
    await asyncio.wait_for(run_task, timeout=2)

    assert runtime.worker.murmur_cleanup_complete is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_deterministic_pipeline_consumes_audio_and_emits_canonical_rtvi_events() -> None:
    close = CloseCounter()
    profile = _deterministic_profile(close)
    transport = DeterministicTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=profile, transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=3)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()
    await runtime.worker.queue_frames(
        [InputAudioRawFrame(b"\x01\x00" * 320, sample_rate=16_000, num_channels=1)]
    )
    await asyncio.sleep(0.01)
    await runtime.worker.queue_frames(
        [InputAudioRawFrame(b"\x02\x00" * 320, sample_rate=16_000, num_channels=1)]
    )

    async def pipeline_completed() -> bool:
        return bool(transport._output.audio_frames) and profile.tts.text_frames == 1  # type: ignore[attr-defined]

    for _ in range(100):
        if await pipeline_completed():
            break
        await asyncio.sleep(0.01)

    await runtime.worker.queue_frames([EndFrame()])
    await asyncio.wait_for(run_task, timeout=3)

    assert profile.stt.audio_frames == 2  # type: ignore[attr-defined]
    assert profile.llm.context_frames == 1  # type: ignore[attr-defined]
    assert profile.tts.text_frames == 1  # type: ignore[attr-defined]
    assert transport._output.audio_frames[0].audio != bytes(
        len(transport._output.audio_frames[0].audio)
    )
    server_events = [
        EventEnvelope.model_validate(message["data"])
        for message in transport._output.messages
        if message.get("type") == "server-message"
    ]
    assert [event.event_type for event in server_events] == [
        EventType.AGENT_READY,
        EventType.TRANSPORT_CONNECTED,
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TRANSCRIPT_SEGMENT,
        EventType.TURN_COMMITTED,
        EventType.ASSISTANT_SPEECH_STARTED,
        EventType.ASSISTANT_SPEECH_STOPPED,
    ]
    assert profile.stt.cleanup_calls == 1  # type: ignore[attr-defined]
    assert profile.llm.cleanup_calls == 1  # type: ignore[attr-defined]
    assert profile.tts.cleanup_calls == 1  # type: ignore[attr-defined]
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1
    assert close.calls == 0


@pytest.mark.asyncio
async def test_final_only_transcript_fallback_commits_and_reaches_llm_tts() -> None:
    profile = _deterministic_profile(CloseCounter(), emit_interim=False)
    transport = DeterministicTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=profile, transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=3)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()
    await runtime.worker.queue_frames(
        [InputAudioRawFrame(b"\x01\x00" * 320, sample_rate=16_000, num_channels=1)]
    )
    for _ in range(100):
        if profile.tts.text_frames == 1:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0.01)
    await runtime.worker.queue_frames([EndFrame()])
    await asyncio.wait_for(run_task, timeout=3)

    assert profile.llm.context_frames == 1  # type: ignore[attr-defined]
    assert profile.tts.text_frames == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_readiness_timeout_terminates_owned_runner_without_task_leak() -> None:
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(),
        transport=FakeTransport(),
        readiness_timeout_seconds=0.02,
    )

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(runtime.run(), timeout=2)

    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("pipecat-lifecycle:")
    ]


@pytest.mark.asyncio
async def test_canonical_ready_waits_for_provider_stream_readiness() -> None:
    provider_ready = asyncio.Event()

    async def wait_streams_ready() -> None:
        await provider_ready.wait()

    transport = FakeTransport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(wait_streams_ready=wait_streams_ready),
        transport=transport,
    )
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()
    await asyncio.sleep(0)
    assert runtime.events.activated is False
    assert runtime.events.producer_sequence == 0

    provider_ready.set()
    for _ in range(100):
        if runtime.events.activated:
            break
        await asyncio.sleep(0.01)
    assert runtime.events.activated is True

    await runtime.worker.queue_frames([EndFrame()])
    await asyncio.wait_for(run_task, timeout=2)


@pytest.mark.asyncio
async def test_provider_stream_error_terminates_with_zero_canonical_ready() -> None:
    release_failure = asyncio.Event()

    async def wait_streams_ready() -> None:
        await release_failure.wait()
        raise RuntimeError("provider stream rejected")

    transport = FakeTransport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(wait_streams_ready=wait_streams_ready),
        transport=transport,
    )
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()
    release_failure.set()

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(run_task, timeout=2)
    assert runtime.events.producer_sequence == 0
    assert runtime.events.activated is False


@pytest.mark.asyncio
async def test_provider_stream_timeout_terminates_with_zero_canonical_ready() -> None:
    async def wait_streams_ready() -> None:
        await asyncio.Event().wait()

    transport = FakeTransport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(wait_streams_ready=wait_streams_ready),
        transport=transport,
        readiness_timeout_seconds=0.03,
    )
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(run_task, timeout=2)
    assert runtime.events.producer_sequence == 0
    assert runtime.events.activated is False


@pytest.mark.asyncio
async def test_canonical_ready_publish_failure_terminates_after_bot_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(),
        transport=FakeTransport(),
    )
    started = asyncio.Event()
    bot_ready_calls = 0

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    async def fail_send(_data: Any) -> None:
        raise RuntimeError("ready send failed")

    async def record_bot_ready(_self: RTVIProcessor, about: object = None) -> None:
        del about
        nonlocal bot_ready_calls
        bot_ready_calls += 1

    monkeypatch.setattr(runtime.rtvi, "send_server_message", fail_send)
    monkeypatch.setattr(RTVIProcessor, "set_bot_ready", record_bot_ready)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await runtime._transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(run_task, timeout=2)
    assert bot_ready_calls == 1
    assert runtime.events.producer_sequence == 0


@pytest.mark.asyncio
async def test_bot_ready_failure_emits_zero_canonical_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    async def fail_bot_ready(_self: RTVIProcessor, about: object = None) -> None:
        del about
        raise RuntimeError("bot-ready failed")

    monkeypatch.setattr(RTVIProcessor, "set_bot_ready", fail_bot_ready)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(run_task, timeout=2)
    assert runtime.events.producer_sequence == 0
    assert runtime.events.activated is False


@pytest.mark.asyncio
async def test_post_ready_event_publish_failure_terminates_runtime() -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())
    await runtime.rtvi.set_client_ready()
    for _ in range(100):
        if runtime.events.activated:
            break
        await asyncio.sleep(0.01)

    async def fail_send(_data: Any) -> None:
        raise RuntimeError("transcript publish failed")

    runtime.rtvi.send_server_message = fail_send  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="transcript publish failed"):
        await runtime._event_processor.process_frame(
            TranscriptionFrame("hello", "user", NOW.isoformat(), finalized=True),
            FrameDirection.DOWNSTREAM,
        )
    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(run_task, timeout=2)


@pytest.mark.asyncio
async def test_detached_transport_handler_failure_is_terminal() -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    async def fail_emit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("transport event failed")

    runtime.events.emit = fail_emit  # type: ignore[method-assign]
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    await transport._call_event_handler("on_client_connected", object())

    with pytest.raises(PipecatRuntimeError, match="terminated after failure") as error:
        await asyncio.wait_for(run_task, timeout=2)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "transport event failed"


@pytest.mark.asyncio
async def test_raw_pipeline_worker_exception_is_terminal_not_a_normal_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(),
        transport=transport,
    )

    async def fail_inside_worker() -> None:
        raise RuntimeError("raw worker failure")

    monkeypatch.setattr(runtime.worker, "_wait_for_pipeline_finished", fail_inside_worker)

    with pytest.raises(PipecatRuntimeError, match="terminated after failure") as error:
        await asyncio.wait_for(runtime.run(), timeout=2)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "raw worker failure"
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1


@pytest.mark.asyncio
async def test_partial_worker_task_creation_failure_cancels_push_task_and_cleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    original_create_tasks = runtime.worker._create_tasks

    async def fail_after_creating_push_task() -> None:
        await original_create_tasks()
        raise RuntimeError("partial task creation failed")

    monkeypatch.setattr(runtime.worker, "_create_tasks", fail_after_creating_push_task)

    with pytest.raises(PipecatRuntimeError, match="terminated after failure") as error:
        await asyncio.wait_for(runtime.run(), timeout=2)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1
    await asyncio.sleep(0)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and "_process_push_queue" in task.get_name()
    ]


@pytest.mark.asyncio
async def test_worker_setup_failure_cleans_pipeline_without_dangling_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)

    async def fail_setup(_params: object) -> None:
        raise RuntimeError("worker setup failed")

    monkeypatch.setattr(runtime.worker, "_setup", fail_setup)

    with pytest.raises(PipecatRuntimeError, match="terminated after failure") as error:
        await asyncio.wait_for(runtime.run(), timeout=2)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1
    await asyncio.sleep(0)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith(f"pipeline-{_claims().voice_call_id}")
    ]


@pytest.mark.asyncio
async def test_raw_worker_cleanup_failure_is_retried_by_runtime_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    original_cleanup = transport._input.cleanup
    cleanup_calls = 0

    async def flaky_cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise RuntimeError("transient cleanup failure")
        await original_cleanup()

    async def fail_inside_worker() -> None:
        raise RuntimeError("raw worker failure")

    monkeypatch.setattr(transport._input, "cleanup", flaky_cleanup)
    monkeypatch.setattr(runtime.worker, "_wait_for_pipeline_finished", fail_inside_worker)

    with pytest.raises(PipecatRuntimeError, match="terminated after failure"):
        await asyncio.wait_for(runtime.run(), timeout=2)
    assert cleanup_calls == 2
    assert runtime.worker.murmur_cleanup_complete is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_runtime_run_cancellation_propagates_and_cleans_owned_pipeline() -> None:
    transport = FakeTransport()
    runtime = PipecatRuntime(claims=_claims(), profile=_profile(), transport=transport)
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert transport._input.cleanup_calls == 1
    assert transport._output.cleanup_calls == 1


@pytest.mark.asyncio
async def test_close_retries_profile_cleanup_after_failure() -> None:
    class FlakyClose:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("cleanup failed")

    close = FlakyClose()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(close),  # type: ignore[arg-type]
        transport=FakeTransport(),
    )

    with pytest.raises(PipecatRuntimeError, match="cleanup failed"):
        await runtime.close()
    await runtime.close()
    assert close.calls == 2


@pytest.mark.asyncio
async def test_wait_closed_shields_owned_task_and_propagates_terminal_failure() -> None:
    release = asyncio.Event()

    async def owned() -> None:
        await release.wait()
        raise RuntimeError("terminal")

    task = asyncio.create_task(owned())
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        object(),
        task,
        close_timeout_seconds=1,
    )
    waiter = asyncio.create_task(handle.wait_closed())
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not task.cancelled()
    release.set()
    with pytest.raises(RuntimeError, match="terminal"):
        await handle.wait_closed()


@pytest.mark.asyncio
async def test_wait_closed_converts_owned_task_cancellation_to_terminal_failure() -> None:
    task = asyncio.create_task(asyncio.Event().wait())
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        object(),
        task,  # type: ignore[arg-type]
        close_timeout_seconds=1,
    )
    task.cancel()

    with pytest.raises(PipecatRuntimeError, match="owned runtime task was cancelled"):
        await handle.wait_closed()


@pytest.mark.asyncio
async def test_handle_close_uses_failed_run_task_only_as_unwind_barrier() -> None:
    class RuntimeClose:
        def __init__(self) -> None:
            self.calls = 0

        async def close(self) -> None:
            self.calls += 1

    async def failed_run() -> None:
        raise RuntimeError("already observed terminal runtime failure")

    runtime = RuntimeClose()
    task = asyncio.create_task(failed_run())
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        runtime,
        task,
        close_timeout_seconds=1,
    )
    with pytest.raises(RuntimeError, match="already observed terminal runtime failure"):
        await handle.wait_closed()

    await handle.aclose()
    await handle.aclose()

    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_handle_close_still_reports_runtime_close_failure() -> None:
    class FailedRuntimeClose:
        async def close(self) -> None:
            raise RuntimeError("runtime cleanup failed")

    task = asyncio.create_task(asyncio.sleep(0))
    await task
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        FailedRuntimeClose(),
        task,
        close_timeout_seconds=1,
    )

    with pytest.raises(PipecatRuntimeError, match="handle cleanup failed") as error:
        await handle.aclose()

    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "runtime cleanup failed"


@pytest.mark.asyncio
async def test_handle_close_still_reports_unwind_timeout_without_cancelling_run_task() -> None:
    class RuntimeClose:
        async def close(self) -> None:
            return None

    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        RuntimeClose(),
        task,  # type: ignore[arg-type]
        close_timeout_seconds=0.01,
    )

    with pytest.raises(PipecatRuntimeError, match="handle cleanup failed") as error:
        await handle.aclose()

    assert isinstance(error.value.__cause__, TimeoutError)
    assert not task.cancelled()
    release.set()
    await task
    await handle.aclose()


@pytest.mark.asyncio
async def test_handle_close_preserves_caller_cancellation_and_shields_run_task() -> None:
    class RuntimeClose:
        async def close(self) -> None:
            return None

    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    handle = PipecatRuntimeHandle(  # type: ignore[arg-type]
        RuntimeClose(),
        task,  # type: ignore[arg-type]
        close_timeout_seconds=1,
    )
    close_task = asyncio.create_task(handle.aclose())
    await asyncio.sleep(0)
    close_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert not task.cancelled()
    release.set()
    await task
    await handle.aclose()


@pytest.mark.asyncio
async def test_close_during_add_workers_never_starts_runner() -> None:
    close = CloseCounter()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=_profile(close),
        transport=FakeTransport(),
    )
    add_entered = asyncio.Event()
    release_add = asyncio.Event()
    runner_called = False

    async def gated_add(*_workers: object) -> None:
        add_entered.set()
        await release_add.wait()

    async def record_runner_call(*_args: object, **_kwargs: object) -> None:
        nonlocal runner_called
        runner_called = True

    async def ignore_cancel(*_args: object, **_kwargs: object) -> None:
        return None

    runtime.runner.add_workers = gated_add  # type: ignore[method-assign]
    runtime.runner.run = record_runner_call  # type: ignore[method-assign]
    runtime.runner.cancel = ignore_cancel  # type: ignore[method-assign]
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(add_entered.wait(), timeout=1)
    close_task = asyncio.create_task(runtime.close())
    await asyncio.sleep(0)
    release_add.set()

    with pytest.raises(PipecatRuntimeError, match="closed during startup"):
        await asyncio.wait_for(run_task, timeout=1)
    await asyncio.wait_for(close_task, timeout=1)
    assert runner_called is False
    assert runtime._ownership_transferred is False
    assert close.calls == 1


@pytest.mark.asyncio
async def test_starter_matches_signaling_seam_and_returns_bounded_handle() -> None:
    claims = _claims()
    close = CloseCounter()
    prepared = _profile(close)
    scopes: list[VoiceProfileScope] = []

    class Provider:
        async def prepare(self, scope: VoiceProfileScope) -> PreparedPipecatProfile:
            scopes.append(scope)
            return prepared

    def scope_factory(authoritative: VoiceCallClaims) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=authoritative.profile_id,
            user_id=authoritative.user_id,
            session_id=authoritative.session_id,
            agent_id=authoritative.agent_id,
            voice_call_id=authoritative.voice_call_id,
            trace_id=authoritative.trace_id,
            system_prompt="Answer briefly.",
        )

    starter = PipecatRuntimeStarter(
        Provider(),
        scope_factory,
        transport_factory=lambda _connection, _profile: FakeTransport(),
        active_call_idle_timeout_seconds=123,
    )
    handle = await starter.start(connection=object(), claims=claims)

    assert scopes[0].voice_call_id == claims.voice_call_id
    assert isinstance(handle.runtime, PipecatRuntime)
    assert handle.runtime.worker._idle_timeout_secs == 123  # type: ignore[attr-defined]
    await handle.aclose()
    await handle.aclose()
    assert close.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["transport", "build"])
async def test_starter_bounds_and_retries_profile_cleanup_before_runtime_handoff(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    claims = _claims()

    class HangingClose:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> None:
            self.calls += 1
            await asyncio.Event().wait()

    close = HangingClose()
    prepared = _profile(close)  # type: ignore[arg-type]

    class Provider:
        async def prepare(self, _scope: VoiceProfileScope) -> PreparedPipecatProfile:
            return prepared

    def scope_factory(authoritative: VoiceCallClaims) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=authoritative.profile_id,
            user_id=authoritative.user_id,
            session_id=authoritative.session_id,
            agent_id=authoritative.agent_id,
            voice_call_id=authoritative.voice_call_id,
            trace_id=authoritative.trace_id,
            system_prompt="Answer briefly.",
        )

    def transport_factory(_connection: object, _profile: PreparedPipecatProfile) -> BaseTransport:
        if failure_stage == "transport":
            raise RuntimeError("transport construction failed")
        return FakeTransport()

    if failure_stage == "build":

        def fail_build(**_kwargs: object) -> PipecatRuntime:
            raise RuntimeError("runtime construction failed")

        monkeypatch.setattr(pipecat_runtime_module, "build_pipecat_runtime", fail_build)

    starter = PipecatRuntimeStarter(
        Provider(),
        scope_factory,
        transport_factory=transport_factory,
        cleanup_timeout_seconds=0.01,
    )

    with pytest.raises(
        PipecatRuntimeError,
        match="startup failed and prepared-profile cleanup failed",
    ) as error:
        async with asyncio.timeout(0.2):
            await starter.start(connection=object(), claims=claims)

    assert isinstance(error.value.__cause__, ExceptionGroup)
    assert close.calls == 2


@pytest.mark.asyncio
async def test_starter_preserves_cancellation_after_bounded_profile_cleanup_retries() -> None:
    claims = _claims()

    class HangingClose:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> None:
            self.calls += 1
            await asyncio.Event().wait()

    close = HangingClose()
    prepared = _profile(close)  # type: ignore[arg-type]

    class Provider:
        async def prepare(self, _scope: VoiceProfileScope) -> PreparedPipecatProfile:
            return prepared

    def scope_factory(authoritative: VoiceCallClaims) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=authoritative.profile_id,
            user_id=authoritative.user_id,
            session_id=authoritative.session_id,
            agent_id=authoritative.agent_id,
            voice_call_id=authoritative.voice_call_id,
            trace_id=authoritative.trace_id,
            system_prompt="Answer briefly.",
        )

    def cancel_transport(
        _connection: object,
        _profile: PreparedPipecatProfile,
    ) -> BaseTransport:
        raise asyncio.CancelledError

    starter = PipecatRuntimeStarter(
        Provider(),
        scope_factory,
        transport_factory=cancel_transport,
        cleanup_timeout_seconds=0.01,
    )

    with pytest.raises(asyncio.CancelledError) as error:
        async with asyncio.timeout(0.2):
            await starter.start(connection=object(), claims=claims)

    assert close.calls == 2
    assert any(
        "startup cancellation cleanup failed" in note
        for note in getattr(error.value, "__notes__", ())
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_handoff", [False, True])
async def test_starter_bounds_never_settling_runtime_cleanup_at_handoff(
    monkeypatch: pytest.MonkeyPatch,
    cancel_handoff: bool,
) -> None:
    claims = _claims()
    prepared = _profile()

    class Provider:
        async def prepare(self, _scope: VoiceProfileScope) -> PreparedPipecatProfile:
            if cancel_handoff:
                current = asyncio.current_task()
                assert current is not None
                asyncio.get_running_loop().call_soon(current.cancel)
            return prepared

    class HangingRuntime:
        def __init__(self) -> None:
            self.close_calls = 0

        async def run(self) -> None:
            if cancel_handoff:
                await asyncio.Event().wait()
            raise RuntimeError("runtime startup failed")

        async def close(self) -> None:
            self.close_calls += 1
            await asyncio.Event().wait()

    def scope_factory(authoritative: VoiceCallClaims) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=authoritative.profile_id,
            user_id=authoritative.user_id,
            session_id=authoritative.session_id,
            agent_id=authoritative.agent_id,
            voice_call_id=authoritative.voice_call_id,
            trace_id=authoritative.trace_id,
            system_prompt="Answer briefly.",
        )

    runtime = HangingRuntime()
    monkeypatch.setattr(pipecat_runtime_module, "build_pipecat_runtime", lambda **_kwargs: runtime)
    starter = PipecatRuntimeStarter(
        Provider(),
        scope_factory,
        transport_factory=lambda _connection, _profile: FakeTransport(),
        cleanup_timeout_seconds=0.01,
    )

    expected = asyncio.CancelledError if cancel_handoff else PipecatRuntimeError
    with pytest.raises(expected) as error:
        async with asyncio.timeout(0.2):
            await starter.start(connection=object(), claims=claims)

    assert runtime.close_calls == 2
    if cancel_handoff:
        assert any(
            "runtime handoff cleanup failed" in note
            for note in getattr(error.value, "__notes__", ())
        )
    else:
        assert "runtime handoff failed and cleanup failed" in str(error.value)


@pytest.mark.asyncio
async def test_starter_cancellation_at_task_handoff_leaves_no_runtime_task() -> None:
    claims = _claims()
    prepared = _profile()

    class Provider:
        async def prepare(self, _scope: VoiceProfileScope) -> PreparedPipecatProfile:
            current = asyncio.current_task()
            assert current is not None
            asyncio.get_running_loop().call_soon(current.cancel)
            return prepared

    def scope_factory(authoritative: VoiceCallClaims) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=authoritative.profile_id,
            user_id=authoritative.user_id,
            session_id=authoritative.session_id,
            agent_id=authoritative.agent_id,
            voice_call_id=authoritative.voice_call_id,
            trace_id=authoritative.trace_id,
            system_prompt="Answer briefly.",
        )

    starter = PipecatRuntimeStarter(
        Provider(),
        scope_factory,
        transport_factory=lambda _connection, _profile: FakeTransport(),
    )

    with pytest.raises(asyncio.CancelledError):
        await starter.start(connection=object(), claims=claims)
    await asyncio.sleep(0)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name() == f"pipecat-runtime:{claims.voice_call_id}"
    ]

"""Provider-free frame, interruption, guard, and cleanup tests for Pipecat E2E."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from murmur.voice.contracts import EventEnvelope, EventType
from murmur.voice.pipecat_fake_rtc import (
    PIPECAT_FAKE_RTC_PROFILE_ID,
    PIPECAT_FAKE_RTC_TRANSCRIPTS,
    PipecatFakeRtcProvider,
    build_pipecat_fake_rtc_provider,
    summarize_pipecat_fake_evidence,
)
from murmur.voice.pipecat_runtime import PipecatRuntime
from murmur.voice.profile import VoiceProfileScope, VoiceProfileUnavailable
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageUrgentFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import BaseTransport

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSISTANT_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "voice" / "audio" / "assistant-long.wav"
CALL_ID = "10000000-0000-4000-8000-000000000001"
SESSION_ID = "20000000-0000-4000-8000-000000000002"
AGENT_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"


def _scope() -> VoiceProfileScope:
    return VoiceProfileScope(
        profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
        user_id="pipecat-e2e-user",
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        voice_call_id=CALL_ID,
        trace_id=TRACE_ID,
        system_prompt="Use the deterministic local response.",
    )


def _claims() -> VoiceCallClaims:
    issued_at = datetime.now(UTC)
    return VoiceCallClaims(
        user_id="pipecat-e2e-user",
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        voice_call_id=CALL_ID,
        trace_id=TRACE_ID,
        runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
        profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=2),
    )


class _PassThrough(FrameProcessor):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _CapturingOutput(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(name="pipecat-fake-capturing-output")
        self.messages: list[dict[str, object]] = []
        self.audio_frames: list[TTSAudioRawFrame] = []
        self.interruption_count = 0
        self.interruption_observed = asyncio.Event()
        self.assistant_completion_observed = asyncio.Event()
        self._speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputTransportMessageUrgentFrame):
            self.messages.append(frame.message)
            data = frame.message.get("data")
            if (
                isinstance(data, dict)
                and data.get("event_type") == EventType.ASSISTANT_SPEECH_STOPPED.value
                and isinstance(data.get("payload"), dict)
                and data["payload"].get("reason") == "completed"
            ):
                self.assistant_completion_observed.set()
        elif isinstance(frame, InterruptionFrame):
            self.interruption_count += 1
            self.interruption_observed.set()
        elif isinstance(frame, TTSAudioRawFrame):
            if not self._speaking:
                self._speaking = True
                await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
            self.audio_frames.append(frame)
        elif isinstance(frame, TTSStoppedFrame) and self._speaking:
            self._speaking = False
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


class _Transport(BaseTransport):
    def __init__(self) -> None:
        super().__init__(name="pipecat-fake-test-transport")
        self._input = _PassThrough(name="pipecat-fake-test-input")
        self._output = _CapturingOutput()
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output


class _InterruptiblePacer:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._block_first = True

    async def __call__(self, _seconds: float) -> None:
        if not self._block_first:
            await asyncio.sleep(0)
            return
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self._block_first = False
            self.cancelled.set()
            raise


async def _queue_utterance(runtime: PipecatRuntime, transport: _Transport) -> None:
    active = InputAudioRawFrame(
        (2000).to_bytes(2, "little", signed=True) * 320,
        sample_rate=16_000,
        num_channels=1,
    )
    transport._output.interruption_observed.clear()
    await runtime.worker.queue_frames([active])
    await asyncio.wait_for(
        transport._output.interruption_observed.wait(),
        timeout=3,
    )
    silence = [
        InputAudioRawFrame(
            bytes(640),
            sample_rate=16_000,
            num_channels=1,
        )
        for _ in range(16)
    ]
    await runtime.worker.queue_frames(silence)


def test_module_has_no_livekit_import_or_fake_profile_dependency() -> None:
    source_path = PROJECT_ROOT / "backend" / "murmur" / "voice" / "pipecat_fake_rtc.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )

    assert not any(name == "livekit" or name.startswith("livekit.") for name in imported)
    assert "murmur.voice.fake_rtc" not in imported


def test_guard_rejects_non_test_mode_and_paths_outside_fixed_roots(tmp_path: Path) -> None:
    with pytest.raises(VoiceProfileUnavailable, match="guarded test mode"):
        build_pipecat_fake_rtc_provider(
            e2e_mode="0",
            environment_name="test",
            profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
            assistant_fixture_path=ASSISTANT_FIXTURE,
            evidence_path=tmp_path / "evidence.jsonl",
        )

    with pytest.raises(VoiceProfileUnavailable, match="outside its guarded root"):
        build_pipecat_fake_rtc_provider(
            e2e_mode="1",
            environment_name="test",
            profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
            assistant_fixture_path=ASSISTANT_FIXTURE,
            evidence_path=tmp_path / "evidence.jsonl",
        )


@pytest.mark.asyncio
async def test_pre_pipeline_close_is_idempotent_and_records_exact_components(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "pre-pipeline.jsonl"
    provider = PipecatFakeRtcProvider(
        assistant_fixture_path=ASSISTANT_FIXTURE,
        evidence_path=evidence,
    )
    profile = await provider.prepare(_scope())

    await profile.close_callback()
    await profile.close_callback()

    summary = summarize_pipecat_fake_evidence(evidence, CALL_ID)
    assert summary["processor_cleanup_counts"] == {"stt": 1, "llm": 1, "tts": 1}
    assert summary["profile_close_count"] == 1
    assert summary["media_contract_satisfied"] is False


@pytest.mark.asyncio
async def test_real_interruption_frame_cancels_inline_paced_tts_and_second_turn_completes(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "pipeline.jsonl"
    provider = PipecatFakeRtcProvider(
        assistant_fixture_path=ASSISTANT_FIXTURE,
        evidence_path=evidence,
    )
    profile = await provider.prepare(_scope())
    pacer = _InterruptiblePacer()
    profile.tts._sleep = pacer  # type: ignore[attr-defined]
    transport = _Transport()
    runtime = PipecatRuntime(
        claims=_claims(),
        profile=profile,
        transport=transport,
        readiness_timeout_seconds=3.0,
    )
    started = asyncio.Event()

    @runtime.worker.event_handler("on_pipeline_started")
    async def record_started(_worker: object, _frame: Frame) -> None:
        started.set()

    run_task = asyncio.create_task(runtime.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        await transport._call_event_handler(
            "on_client_connected",
            SimpleNamespace(pc_id="SmallWebRTCConnection#fake-test"),
        )
        await runtime.rtvi.set_client_ready()

        await _queue_utterance(runtime, transport)
        await asyncio.wait_for(pacer.entered.wait(), timeout=3)
        await _queue_utterance(runtime, transport)
        await asyncio.wait_for(pacer.cancelled.wait(), timeout=3)
        await asyncio.wait_for(
            transport._output.assistant_completion_observed.wait(),
            timeout=3,
        )
        await runtime.worker.queue_frames([EndFrame()])
        await asyncio.wait_for(asyncio.shield(run_task), timeout=3)
    finally:
        if not run_task.done():
            await runtime.close()
        await asyncio.gather(run_task, return_exceptions=True)

    summary = summarize_pipecat_fake_evidence(evidence, CALL_ID)
    assert summary == {
        "input_frame_count": 34,
        "final_transcripts": list(PIPECAT_FAKE_RTC_TRANSCRIPTS),
        "llm_response_count": 2,
        "tts_frame_count": 301,
        "tts_cancelled_count": 1,
        "cleaned_processors": ["llm", "stt", "tts"],
        "processor_cleanup_counts": {"stt": 1, "llm": 1, "tts": 1},
        "profile_close_count": 1,
        "media_contract_satisfied": True,
    }
    assert transport._output.audio_frames
    assert all(frame.audio for frame in transport._output.audio_frames)
    events = [
        EventEnvelope.model_validate(message["data"])
        for message in transport._output.messages
        if message.get("type") == "server-message"
    ]
    assert [event.event_type for event in events[:2]] == [
        EventType.AGENT_READY,
        EventType.TRANSPORT_CONNECTED,
    ]
    turns = [event for event in events if event.event_type is EventType.TURN_COMMITTED]
    speech_stops = [
        event for event in events if event.event_type is EventType.ASSISTANT_SPEECH_STOPPED
    ]
    assert [event.payload["text"] for event in turns] == list(PIPECAT_FAKE_RTC_TRANSCRIPTS)
    assert [event.payload["reason"] for event in speech_stops] == ["interrupted", "completed"]
    assert speech_stops[0].turn_id == turns[0].turn_id
    assert speech_stops[1].turn_id == turns[1].turn_id

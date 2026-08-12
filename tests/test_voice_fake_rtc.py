"""Deterministic media-provider tests for the loopback-only RTC E2E lane."""

from __future__ import annotations

import asyncio
import json
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from livekit import rtc
from livekit.agents import llm, stt
from murmur.voice import fake_rtc
from murmur.voice.fake_rtc import (
    FAKE_RTC_PROFILE_ID,
    FAKE_RTC_REPLY,
    FAKE_RTC_TRANSCRIPTS,
    DeterministicLLM,
    DeterministicStreamingSTT,
    DeterministicTTS,
    JsonlEvidenceRecorder,
    create_fake_rtc_profile_provider,
    create_fake_rtc_profile_provider_from_environment,
)
from murmur.voice.profile import (
    VoiceMediaPolicy,
    VoiceProfileScope,
    VoiceProfileUnavailable,
    VoiceSessionPolicy,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "voice" / "audio"


def _write_pcm_wav(
    path: Path,
    *,
    sample_rate: int,
    duration_seconds: float,
    frequency: float = 440.0,
) -> None:
    sample_count = round(sample_rate * duration_seconds)
    timeline = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.round(np.sin(2 * np.pi * frequency * timeline) * 8_000).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _scope(**overrides: str) -> VoiceProfileScope:
    values = {
        "profile_id": FAKE_RTC_PROFILE_ID,
        "user_id": "user-1",
        "session_id": "session-1",
        "agent_id": "agent-1",
        "voice_call_id": "call-1",
        "trace_id": "trace-1",
        "system_prompt": "Respond using the deterministic fixture.",
    }
    values.update(overrides)
    return VoiceProfileScope(**values)


def _guarded_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(fake_rtc, "_SOURCE_ROOT", tmp_path)
    fixture = tmp_path / "tests" / "fixtures" / "voice" / "audio" / "assistant.wav"
    evidence = tmp_path / "var" / "evals" / "run-1" / "events.jsonl"
    _write_pcm_wav(fixture, sample_rate=24_000, duration_seconds=0.1)
    evidence.parent.mkdir(parents=True)
    return fixture, evidence


def _valid_factory_kwargs(fixture: Path, evidence: Path) -> dict[str, object]:
    return {
        "e2e_mode": "1",
        "environment": "test",
        "profile_id": FAKE_RTC_PROFILE_ID,
        "livekit_url": "ws://127.0.0.1:7880",
        "assistant_fixture_path": fixture,
        "evidence_path": evidence,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("e2e_mode", None),
        ("e2e_mode", "0"),
        ("environment", "development"),
        ("profile_id", "livekit-agents-cascade-v1"),
        ("livekit_url", "wss://example.test"),
        ("livekit_url", "ws://localhost.evil:7880"),
        ("livekit_url", "http://127.0.0.1:7880"),
        ("livekit_url", "ws://user@127.0.0.1:7880"),
    ],
)
def test_factory_refuses_without_every_test_only_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)
    kwargs = _valid_factory_kwargs(fixture, evidence)
    kwargs[field] = value

    with pytest.raises(VoiceProfileUnavailable):
        create_fake_rtc_profile_provider(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "livekit_url",
    ["ws://localhost:7880", "wss://127.0.0.1:7880", "ws://[::1]:7880"],
)
def test_factory_accepts_only_explicit_loopback_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    livekit_url: str,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)
    kwargs = _valid_factory_kwargs(fixture, evidence)
    kwargs["livekit_url"] = livekit_url

    provider = create_fake_rtc_profile_provider(**kwargs)  # type: ignore[arg-type]

    assert provider is not None
    assert evidence.is_file()


def test_environment_factory_uses_only_guarded_explicit_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)

    provider = create_fake_rtc_profile_provider_from_environment(
        {
            "MURMUR_E2E_MODE": "1",
            "MURMUR_ENVIRONMENT": "test",
            "VOICE_V2_PROFILE_ID": FAKE_RTC_PROFILE_ID,
            "LIVEKIT_URL": "ws://127.0.0.1:7880",
            "MURMUR_E2E_ASSISTANT_FIXTURE_PATH": str(fixture),
            "MURMUR_E2E_EVIDENCE_PATH": str(evidence),
        }
    )

    assert provider is not None
    with pytest.raises(VoiceProfileUnavailable, match="E2E mode"):
        create_fake_rtc_profile_provider_from_environment({})


def test_factory_refuses_paths_outside_canonical_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)
    outside_fixture = tmp_path / "outside.wav"
    outside_evidence = tmp_path / "outside.jsonl"
    _write_pcm_wav(outside_fixture, sample_rate=24_000, duration_seconds=0.1)

    with pytest.raises(VoiceProfileUnavailable, match="outside"):
        create_fake_rtc_profile_provider(
            **_valid_factory_kwargs(outside_fixture, evidence)  # type: ignore[arg-type]
        )
    with pytest.raises(VoiceProfileUnavailable, match="outside"):
        create_fake_rtc_profile_provider(
            **_valid_factory_kwargs(fixture, outside_evidence)  # type: ignore[arg-type]
        )
    with pytest.raises(VoiceProfileUnavailable, match="unsafe"):
        create_fake_rtc_profile_provider(
            **_valid_factory_kwargs(fixture, Path("relative.jsonl"))  # type: ignore[arg-type]
        )


def test_factory_refuses_symlink_and_invalid_pcm_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)
    fixture_link = fixture.with_name("linked.wav")
    fixture_link.symlink_to(fixture)

    with pytest.raises(VoiceProfileUnavailable):
        create_fake_rtc_profile_provider(
            **_valid_factory_kwargs(fixture_link, evidence)  # type: ignore[arg-type]
        )

    invalid = fixture.with_name("invalid.wav")
    _write_pcm_wav(invalid, sample_rate=16_000, duration_seconds=0.1)
    with pytest.raises(VoiceProfileUnavailable, match="valid WAV"):
        create_fake_rtc_profile_provider(
            **_valid_factory_kwargs(invalid, evidence)  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_profile_preflight_prepare_policy_and_idempotent_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, evidence = _guarded_paths(tmp_path, monkeypatch)
    provider = create_fake_rtc_profile_provider(**_valid_factory_kwargs(fixture, evidence))

    preflight = await provider.preflight(_scope())
    prepared = await provider.prepare(_scope())

    assert preflight.required_components == ("stt", "llm", "tts", "fake_media")
    assert preflight.ready_components == preflight.required_components
    assert prepared.session_policy == VoiceSessionPolicy()
    assert prepared.media_policy == VoiceMediaPolicy()
    assert isinstance(prepared.stt, DeterministicStreamingSTT)
    assert isinstance(prepared.llm, DeterministicLLM)
    assert isinstance(prepared.tts, DeterministicTTS)
    assert prepared.close_callback is not None

    await prepared.close_callback()
    await prepared.close_callback()

    events = _read_events(evidence)
    assert [event["event"] for event in events].count("profile_closed") == 1
    assert events[-1] == {
        "event": "profile_closed",
        "monotonic_ns": events[-1]["monotonic_ns"],
        "profile_id": FAKE_RTC_PROFILE_ID,
        "schema_version": 1,
        "session_id": "session-1",
        "voice_call_id": "call-1",
    }
    with pytest.raises(RuntimeError, match="closed"):
        prepared.stt.stream()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_streaming_stt_consumes_actual_audio_frames_and_segments_two_turns(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "stt.jsonl"
    recorder = JsonlEvidenceRecorder(evidence)
    recognizer = DeterministicStreamingSTT(recorder)
    fixture_path = FIXTURE_ROOT / "browser-barge-in.wav"

    with wave.open(str(fixture_path), "rb") as fixture:
        assert fixture.getframerate() == 16_000
        stream = recognizer.stream()
        while chunk := fixture.readframes(320):
            stream.push_frame(
                rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16_000,
                    num_channels=1,
                    samples_per_channel=len(chunk) // 2,
                )
            )
        stream.end_input()
        events = [event async for event in stream]
        await stream.aclose()

    await recognizer.aclose()
    recorder.close()

    assert [event.type for event in events] == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
    ]
    final_text = [
        event.alternatives[0].text
        for event in events
        if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    ]
    assert final_text == list(FAKE_RTC_TRANSCRIPTS)

    records = _read_events(evidence)
    input_records = [record for record in records if record["event"] == "input_frame"]
    assert len(input_records) > 250
    assert {record["num_channels"] for record in input_records} == {1}
    assert {record["sample_rate"] for record in input_records} == {16_000}
    assert max(record["peak"] for record in input_records) > 20_000
    assert max(record["rms"] for record in input_records) > 1_000
    assert [record["transcript"] for record in records if record["event"] == "speech_onset"] == [
        *FAKE_RTC_TRANSCRIPTS
    ]
    assert [
        record["text"]
        for record in records
        if record["event"] == "transcript_emitted" and record["transcript_type"] == "final"
    ] == list(FAKE_RTC_TRANSCRIPTS)
    speech_onsets = [
        record["stream_time_seconds"] for record in records if record["event"] == "speech_onset"
    ]
    speech_ends = [
        record["stream_time_seconds"] for record in records if record["event"] == "speech_end"
    ]
    first_turn_commit = speech_ends[0] + 0.30
    assert first_turn_commit < speech_onsets[1] < first_turn_commit + 6.0


@pytest.mark.asyncio
async def test_deterministic_llm_emits_one_public_chat_chunk(tmp_path: Path) -> None:
    evidence = tmp_path / "llm.jsonl"
    recorder = JsonlEvidenceRecorder(evidence)
    model = DeterministicLLM(recorder)

    stream = model.chat(chat_ctx=llm.ChatContext.empty())
    chunks = [chunk async for chunk in stream]
    await stream.aclose()
    await model.aclose()
    recorder.close()

    assert len(chunks) == 1
    assert chunks[0].id == "fake-llm-1"
    assert chunks[0].delta is not None
    assert chunks[0].delta.role == "assistant"
    assert chunks[0].delta.content == FAKE_RTC_REPLY
    assert [record["event"] for record in _read_events(evidence)] == ["llm_chunk_emitted"]


@pytest.mark.asyncio
async def test_tts_paces_pcm_in_real_time(tmp_path: Path) -> None:
    fixture_path = tmp_path / "paced.wav"
    evidence = tmp_path / "paced.jsonl"
    _write_pcm_wav(fixture_path, sample_rate=24_000, duration_seconds=0.1)
    fixture = fake_rtc._load_pcm_fixture(fixture_path, expected_sample_rate=24_000)
    recorder = JsonlEvidenceRecorder(evidence)
    synthesizer = DeterministicTTS(recorder, fixture)

    started = time.perf_counter()
    stream = synthesizer.synthesize("pace this")
    audio = [packet.frame async for packet in stream]
    elapsed = time.perf_counter() - started
    await stream.aclose()
    await synthesizer.aclose()
    recorder.close()

    assert elapsed >= 0.075
    assert sum(frame.duration for frame in audio) == pytest.approx(0.1)
    assert max(np.max(np.abs(np.frombuffer(frame.data, dtype="<i2"))) for frame in audio) > 0
    assert [record["event"] for record in _read_events(evidence)].count("tts_completed") == 1


@pytest.mark.asyncio
async def test_tts_cancellation_is_observable_and_stops_long_fixture(tmp_path: Path) -> None:
    evidence = tmp_path / "cancel.jsonl"
    fixture = fake_rtc._load_pcm_fixture(
        FIXTURE_ROOT / "assistant-long.wav",
        expected_sample_rate=24_000,
    )
    recorder = JsonlEvidenceRecorder(evidence)
    synthesizer = DeterministicTTS(recorder, fixture)
    stream = synthesizer.synthesize("cancel this response")

    first_packet = await anext(stream)
    assert np.any(np.frombuffer(first_packet.frame.data, dtype="<i2"))
    await asyncio.sleep(0.04)
    await stream.aclose()
    await synthesizer.aclose()
    recorder.close()

    records = _read_events(evidence)
    cancelled = [record for record in records if record["event"] == "tts_cancelled"]
    assert len(cancelled) == 1
    assert 1 <= cancelled[0]["emitted_frames"] < 300
    assert not any(record["event"] == "tts_completed" for record in records)


def test_checked_in_rtc_fixtures_have_exact_pcm_shapes_and_energy() -> None:
    browser_path = FIXTURE_ROOT / "browser-barge-in.wav"
    assistant_path = FIXTURE_ROOT / "assistant-long.wav"

    with wave.open(str(browser_path), "rb") as browser:
        assert (browser.getnchannels(), browser.getsampwidth(), browser.getframerate()) == (
            1,
            2,
            16_000,
        )
        browser_pcm = np.frombuffer(browser.readframes(browser.getnframes()), dtype="<i2")
        assert browser.getnframes() / browser.getframerate() == pytest.approx(12.08375)
    assert not np.any(browser_pcm[: 16_000 * 79 // 10])
    assert np.max(np.abs(browser_pcm[16_000 * 8 : 16_000 * 88 // 10])) > 20_000
    assert not np.any(browser_pcm[16_000 * 9 : 16_000 * 97 // 10])
    assert np.max(np.abs(browser_pcm[16_000 * 99 // 10 : 16_000 * 11])) > 20_000

    with wave.open(str(assistant_path), "rb") as assistant:
        assert (assistant.getnchannels(), assistant.getsampwidth(), assistant.getframerate()) == (
            1,
            2,
            24_000,
        )
        assistant_pcm = np.frombuffer(
            assistant.readframes(assistant.getnframes()),
            dtype="<i2",
        )
        assert assistant.getnframes() == 144_000
    assert np.max(np.abs(assistant_pcm)) > 0

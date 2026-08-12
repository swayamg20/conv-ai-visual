"""Provider-free tests for the deterministic voice replay harness."""

import json
from pathlib import Path

import pytest
from murmur.voice.evaluation import (
    ProviderEvent,
    ProviderEventType,
    ReplayScenario,
    TurnReplayEngine,
    evaluate_replay_gates,
    run_replay_suite,
    validate_audio_fixture,
    write_replay_artifacts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _event(
    event_id: str,
    at_ms: int,
    event_type: ProviderEventType,
    **kwargs,
) -> ProviderEvent:
    return ProviderEvent(event_id=event_id, at_ms=at_ms, type=event_type, **kwargs)


def test_final_segments_wait_for_explicit_end_of_turn() -> None:
    scenario = ReplayScenario(
        id="segments",
        provider_events_path="unused",
        reference_turns=("hello tutor",),
    )
    events = [
        _event("one", 0, ProviderEventType.SPEECH_STARTED),
        _event(
            "two",
            100,
            ProviderEventType.TRANSCRIPT,
            text="hello",
            is_final=True,
        ),
        _event(
            "three",
            200,
            ProviderEventType.TRANSCRIPT,
            text="tutor",
            is_final=True,
        ),
        _event("four", 300, ProviderEventType.UTTERANCE_END),
    ]

    result = TurnReplayEngine().replay(scenario, events)

    assert result.passed
    assert result.committed_turns == ("hello tutor",)
    assert [item.type for item in result.trace].count("turn_committed") == 1


def test_is_final_without_eot_remains_pending() -> None:
    scenario = ReplayScenario(
        id="pending",
        provider_events_path="unused",
        reference_turns=(),
        expected_pending_text="still speaking",
    )
    events = [
        _event(
            "segment",
            100,
            ProviderEventType.TRANSCRIPT,
            text="still speaking",
            is_final=True,
        )
    ]

    result = TurnReplayEngine().replay(scenario, events)

    assert result.passed
    assert result.committed_turns == ()
    assert result.pending_text == "still speaking"


def test_duplicate_event_id_is_idempotent() -> None:
    scenario = ReplayScenario(
        id="duplicate",
        provider_events_path="unused",
        reference_turns=("only once",),
    )
    segment = _event(
        "segment",
        100,
        ProviderEventType.TRANSCRIPT,
        text="only once",
        is_final=True,
    )
    events = [segment, segment, _event("eot", 200, ProviderEventType.UTTERANCE_END)]

    result = TurnReplayEngine().replay(scenario, events)

    assert result.passed
    assert result.committed_turns == ("only once",)
    assert any(item.type == "duplicate_ignored" for item in result.trace)


def test_decreasing_provider_timestamps_fail_closed() -> None:
    scenario = ReplayScenario(id="bad-order", provider_events_path="unused")
    events = [
        _event("later", 200, ProviderEventType.SPEECH_STARTED),
        _event("earlier", 100, ProviderEventType.UTTERANCE_END),
    ]

    with pytest.raises(ValueError, match="decreasing at_ms"):
        TurnReplayEngine().replay(scenario, events)


def test_virtual_time_exercises_the_production_pending_age_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("murmur.voice.evaluation.PENDING_TRANSCRIPT_MAX_AGE_SECS", 1.0)
    scenario = ReplayScenario(
        id="bounded",
        provider_events_path="unused",
        reference_turns=(),
    )
    events = [
        _event(
            "segment",
            0,
            ProviderEventType.TRANSCRIPT,
            text="unfinished",
            is_final=True,
        ),
        _event("late", 1_001, ProviderEventType.SPEECH_RESUMED),
    ]

    result = TurnReplayEngine().replay(scenario, events)

    assert result.passed
    assert result.pending_text == ""
    assert any(item.type == "pending_limit_exceeded" for item in result.trace)


def test_audio_fixture_validation_checks_pcm_format(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.wav"
    invalid.write_bytes(b"not a wave")

    with pytest.raises(ValueError, match="invalid WAV fixture"):
        validate_audio_fixture(invalid)

    metadata = validate_audio_fixture(
        PROJECT_ROOT / "tests/fixtures/voice/audio/short-complete.wav"
    )
    assert metadata["channels"] == 1
    assert metadata["sample_width_bytes"] == 2
    assert metadata["sample_rate_hz"] == 16_000
    assert metadata["compression_type"] == 0
    assert metadata["frame_count"] > 0


def test_repository_smoke_suite_is_repeatable_and_passes_gates(tmp_path: Path) -> None:
    suite_path = PROJECT_ROOT / "evals/voice/smoke.jsonl"
    gates = json.loads((PROJECT_ROOT / "evals/voice/gates.json").read_text())

    first = run_replay_suite(suite_path, project_root=PROJECT_ROOT, repeats=2)
    second = run_replay_suite(suite_path, project_root=PROJECT_ROOT, repeats=2)
    gate_result = evaluate_replay_gates(first, gates)

    assert first["combined_trace_hash"] == second["combined_trace_hash"]
    assert first["scenario_count"] == 7
    assert first["trace_repeat_mismatches"] == 0
    assert gate_result["passed"]
    assert "live_latency_ms" in gate_result["unmeasured_gate_groups"]

    output_dir = tmp_path / "run"
    first["gate_result"] = gate_result
    write_replay_artifacts(output_dir, first)
    assert {path.name for path in output_dir.iterdir()} == {
        "artifact-diff.json",
        "cost.json",
        "events.jsonl",
        "failures.json",
        "normalized-transcript.json",
        "state-trace.json",
        "summary.json",
    }


def test_qualification_suite_references_existing_synthetic_audio() -> None:
    summary = run_replay_suite(
        PROJECT_ROOT / "evals/voice/qualification.jsonl",
        project_root=PROJECT_ROOT,
        repeats=2,
    )

    assert summary["scenario_count"] == 7
    assert summary["scenario_pass_rate"] == 1.0

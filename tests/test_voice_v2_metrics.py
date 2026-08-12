"""Clock-domain and named-span contracts for Voice V2 metrics."""

import pytest
from murmur.voice.metrics import (
    ClockDomain,
    MetricSpanName,
    MetricStage,
    StageMark,
    StageRecorder,
    derive_stage_span,
    metric_completeness,
)
from pydantic import ValidationError


def _clock(values: list[float]):
    remaining = iter(values)
    return lambda: next(remaining)


def test_recorder_uses_injected_clock_and_derives_named_spans() -> None:
    recorder = StageRecorder(
        "turn-1",
        ClockDomain.SERVER_MONOTONIC,
        clock=_clock([10.0, 10.125]),
    )
    recorder.record(MetricStage.ACOUSTIC_SPEECH_ENDED)
    recorder.record(MetricStage.TURN_COMMITTED)

    span = recorder.span(MetricSpanName.SPEECH_END_TO_COMMIT)

    assert span.duration_ms == pytest.approx(125.0)
    assert span.clock_domain is ClockDomain.SERVER_MONOTONIC


def test_browser_interval_remains_in_browser_clock_domain() -> None:
    recorder = StageRecorder(
        "call-1",
        ClockDomain.BROWSER_PERFORMANCE,
        clock=lambda: 0,
    )
    recorder.record(MetricStage.CONNECT_REQUESTED, at_ms=100.0)
    recorder.record(MetricStage.AGENT_READY, at_ms=820.5)

    span = recorder.span(MetricSpanName.CONNECT_TO_READY)

    assert span.duration_ms == 720.5
    assert span.clock_domain is ClockDomain.BROWSER_PERFORMANCE


def test_cross_source_and_cross_clock_subtraction_fail_closed() -> None:
    browser_start = StageMark(
        source_id="call-1",
        clock_domain="browser_performance",
        stage="acoustic_speech_ended",
        at_ms=100.0,
    )
    server_end = StageMark(
        source_id="turn-1",
        clock_domain="server_monotonic",
        stage="audio_first_audible",
        at_ms=200.0,
    )

    with pytest.raises(ValueError, match="different sources"):
        derive_stage_span(
            MetricSpanName.SPEECH_END_TO_FIRST_AUDIBLE,
            browser_start,
            server_end,
        )

    same_source_server_end = server_end.model_copy(update={"source_id": "call-1"})
    with pytest.raises(ValueError, match="clock domains"):
        derive_stage_span(
            MetricSpanName.SPEECH_END_TO_FIRST_AUDIBLE,
            browser_start,
            same_source_server_end,
        )


def test_recorder_rejects_duplicate_or_backwards_stages_without_sleeping() -> None:
    recorder = StageRecorder(
        "turn-1",
        ClockDomain.SERVER_MONOTONIC,
        clock=lambda: 0,
    )
    recorder.record(MetricStage.TURN_COMMITTED, at_ms=20.0)

    with pytest.raises(ValueError, match="already recorded"):
        recorder.record(MetricStage.TURN_COMMITTED, at_ms=21.0)
    with pytest.raises(ValueError, match="backwards"):
        recorder.record(MetricStage.LLM_FIRST_SAFE_OUTPUT, at_ms=19.0)


def test_stage_records_reject_invalid_values_and_completeness_is_explicit() -> None:
    with pytest.raises(ValidationError):
        StageMark(
            source_id="turn 1",
            clock_domain="server_monotonic",
            stage="turn_committed",
            at_ms=-1.0,
        )

    recorder = StageRecorder(
        "turn-1",
        ClockDomain.SERVER_MONOTONIC,
        clock=lambda: 0,
    )
    recorder.record(MetricStage.TURN_COMMITTED, at_ms=10.0)
    recorder.record(MetricStage.LLM_FIRST_SAFE_OUTPUT, at_ms=20.0)
    observed = [recorder.span(MetricSpanName.COMMIT_TO_LLM_FIRST_SAFE_OUTPUT)]

    assert (
        metric_completeness(
            [
                MetricSpanName.COMMIT_TO_LLM_FIRST_SAFE_OUTPUT,
                MetricSpanName.LLM_TO_TTS_FIRST_BYTE,
            ],
            observed,
        )
        == 0.5
    )
    assert metric_completeness([], observed) == 1.0

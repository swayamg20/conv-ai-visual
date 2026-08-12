"""Clock-safe stage metrics for Voice V2.

Raw wall clocks from different processes are never subtracted. A span can be
derived only from marks produced by the same source and monotonic clock domain.
Browser intervals therefore stay browser intervals even when reported to the
backend for aggregation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from murmur.voice.contracts import ContractId


class ClockDomain(StrEnum):
    SERVER_MONOTONIC = "server_monotonic"
    BROWSER_PERFORMANCE = "browser_performance"


class MetricStage(StrEnum):
    CONNECT_REQUESTED = "connect_requested"
    TRANSPORT_CONNECTED = "transport_connected"
    AGENT_READY = "agent_ready"
    ACOUSTIC_SPEECH_STARTED = "acoustic_speech_started"
    ACOUSTIC_SPEECH_ENDED = "acoustic_speech_ended"
    TURN_COMMITTED = "turn_committed"
    LLM_FIRST_SAFE_OUTPUT = "llm_first_safe_output"
    TTS_FIRST_BYTE = "tts_first_byte"
    AUDIO_FIRST_AUDIBLE = "audio_first_audible"
    INTERRUPTION_DETECTED = "interruption_detected"
    AUDIO_SILENT = "audio_silent"
    CANVAS_PATCH_RECEIVED = "canvas_patch_received"
    CANVAS_FIRST_VISIBLE = "canvas_first_visible"


class MetricSpanName(StrEnum):
    CONNECT_TO_READY = "connect_to_ready"
    SPEECH_END_TO_COMMIT = "speech_end_to_commit"
    COMMIT_TO_LLM_FIRST_SAFE_OUTPUT = "commit_to_llm_first_safe_output"
    LLM_TO_TTS_FIRST_BYTE = "llm_to_tts_first_byte"
    SPEECH_END_TO_FIRST_AUDIBLE = "speech_end_to_first_audible"
    INTERRUPTION_TO_SILENCE = "interruption_to_silence"
    CANVAS_RECEIVE_TO_FIRST_VISIBLE = "canvas_receive_to_first_visible"


_SPAN_BOUNDARIES: Mapping[MetricSpanName, tuple[MetricStage, MetricStage]] = {
    MetricSpanName.CONNECT_TO_READY: (
        MetricStage.CONNECT_REQUESTED,
        MetricStage.AGENT_READY,
    ),
    MetricSpanName.SPEECH_END_TO_COMMIT: (
        MetricStage.ACOUSTIC_SPEECH_ENDED,
        MetricStage.TURN_COMMITTED,
    ),
    MetricSpanName.COMMIT_TO_LLM_FIRST_SAFE_OUTPUT: (
        MetricStage.TURN_COMMITTED,
        MetricStage.LLM_FIRST_SAFE_OUTPUT,
    ),
    MetricSpanName.LLM_TO_TTS_FIRST_BYTE: (
        MetricStage.LLM_FIRST_SAFE_OUTPUT,
        MetricStage.TTS_FIRST_BYTE,
    ),
    MetricSpanName.SPEECH_END_TO_FIRST_AUDIBLE: (
        MetricStage.ACOUSTIC_SPEECH_ENDED,
        MetricStage.AUDIO_FIRST_AUDIBLE,
    ),
    MetricSpanName.INTERRUPTION_TO_SILENCE: (
        MetricStage.INTERRUPTION_DETECTED,
        MetricStage.AUDIO_SILENT,
    ),
    MetricSpanName.CANVAS_RECEIVE_TO_FIRST_VISIBLE: (
        MetricStage.CANVAS_PATCH_RECEIVED,
        MetricStage.CANVAS_FIRST_VISIBLE,
    ),
}


class MetricRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class StageMark(MetricRecord):
    source_id: ContractId
    clock_domain: ClockDomain
    stage: MetricStage
    at_ms: Annotated[float, Field(strict=True, ge=0)]


class StageSpan(MetricRecord):
    source_id: ContractId
    clock_domain: ClockDomain
    name: MetricSpanName
    start_stage: MetricStage
    end_stage: MetricStage
    duration_ms: Annotated[float, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def validate_boundaries(self) -> StageSpan:
        expected = _SPAN_BOUNDARIES[self.name]
        if (self.start_stage, self.end_stage) != expected:
            raise ValueError(f"{self.name.value} has the wrong stage boundary")
        return self


def derive_stage_span(name: MetricSpanName, start: StageMark, end: StageMark) -> StageSpan:
    """Derive one named interval without crossing a process or clock domain."""
    expected_start, expected_end = _SPAN_BOUNDARIES[name]
    if start.stage is not expected_start or end.stage is not expected_end:
        raise ValueError(f"{name.value} requires {expected_start.value} -> {expected_end.value}")
    if start.source_id != end.source_id:
        raise ValueError("metric marks from different sources cannot form a span")
    if start.clock_domain is not end.clock_domain:
        raise ValueError("metric marks from different clock domains cannot form a span")
    if end.at_ms < start.at_ms:
        raise ValueError("metric end precedes metric start")
    return StageSpan(
        source_id=start.source_id,
        clock_domain=start.clock_domain,
        name=name,
        start_stage=start.stage,
        end_stage=end.stage,
        duration_ms=end.at_ms - start.at_ms,
    )


class StageRecorder:
    """Record one call/turn's stages with an injected monotonic clock."""

    def __init__(
        self,
        source_id: str,
        clock_domain: ClockDomain,
        *,
        clock: Callable[[], float],
    ) -> None:
        # Validate the source once using the same public wire model.
        StageMark(
            source_id=source_id,
            clock_domain=clock_domain,
            stage=MetricStage.CONNECT_REQUESTED,
            at_ms=0.0,
        )
        self.source_id = source_id
        self.clock_domain = clock_domain
        self.clock = clock
        self._marks: dict[MetricStage, StageMark] = {}
        self._last_at_ms = -1.0

    def record(self, stage: MetricStage, *, at_ms: float | None = None) -> StageMark:
        if stage in self._marks:
            raise ValueError(f"metric stage already recorded: {stage.value}")
        observed_at_ms = self.clock() * 1000 if at_ms is None else at_ms
        mark = StageMark(
            source_id=self.source_id,
            clock_domain=self.clock_domain,
            stage=stage,
            at_ms=observed_at_ms,
        )
        if mark.at_ms < self._last_at_ms:
            raise ValueError("metric clock moved backwards")
        self._marks[stage] = mark
        self._last_at_ms = mark.at_ms
        return mark

    def span(self, name: MetricSpanName) -> StageSpan:
        start_stage, end_stage = _SPAN_BOUNDARIES[name]
        try:
            start = self._marks[start_stage]
            end = self._marks[end_stage]
        except KeyError as exc:
            raise ValueError(f"missing stage for {name.value}: {exc.args[0].value}") from exc
        return derive_stage_span(name, start, end)

    @property
    def marks(self) -> tuple[StageMark, ...]:
        return tuple(self._marks.values())


def metric_completeness(
    expected: Iterable[MetricSpanName],
    observed: Iterable[StageSpan],
) -> float:
    """Return the fraction of expected named spans observed at least once."""
    expected_names = frozenset(expected)
    if not expected_names:
        return 1.0
    observed_names = {span.name for span in observed}
    return len(expected_names & observed_names) / len(expected_names)

"""Deterministic provider-event replay for Murmur voice qualification.

Replay deliberately stays provider-free. It proves turn assembly and state-trace
determinism; it does not claim that a browser, RTC transport, or live provider works.
"""

from __future__ import annotations

import hashlib
import json
import wave
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from murmur.voice.turn_assembly import (
    DEFAULT_MAX_PENDING_AGE_SECONDS as PENDING_TRANSCRIPT_MAX_AGE_SECS,
)
from murmur.voice.turn_assembly import (
    DEFAULT_MAX_PENDING_CHARACTERS as PENDING_TRANSCRIPT_MAX_CHARACTERS,
)
from murmur.voice.turn_assembly import (
    DEFAULT_MAX_PENDING_SEGMENTS as PENDING_TRANSCRIPT_MAX_SEGMENTS,
)
from murmur.voice.turn_assembly import TranscriptAccumulator, normalize_transcript


class ProviderEventType(StrEnum):
    """Provider-level inputs understood by the replay engine."""

    SPEECH_STARTED = "speech_started"
    SPEECH_RESUMED = "speech_resumed"
    TRANSCRIPT = "transcript"
    UTTERANCE_END = "utterance_end"
    TURN_TIMEOUT = "turn_timeout"


class ProviderEvent(BaseModel):
    """One normalized event captured from an STT/turn provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    at_ms: int = Field(ge=0)
    type: ProviderEventType
    text: str = ""
    is_final: bool = False
    speech_final: bool = False

    @model_validator(mode="after")
    def validate_payload(self) -> ProviderEvent:
        if self.type != ProviderEventType.TRANSCRIPT and (
            self.text or self.is_final or self.speech_final
        ):
            raise ValueError("only transcript events may carry transcript fields")
        return self


class ReplayScenario(BaseModel):
    """Expected turn outcome for one deterministic provider-event fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    provider_events_path: str = Field(min_length=1)
    reference_turns: tuple[str, ...] = ()
    expected_pending_text: str = ""
    critical_entities: tuple[str, ...] = ()
    language_tags: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    audio_path: str | None = None


class TraceEvent(BaseModel):
    """Normalized replay output; safe to hash after JSON serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    at_ms: int = Field(ge=0)
    type: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    text: str = ""
    reason: str | None = None


class ReplayResult(BaseModel):
    """Deterministic result for one scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    passed: bool
    committed_turns: tuple[str, ...]
    pending_text: str
    critical_entities_total: int
    critical_entities_retained: int
    premature_split_count: int
    incorrect_merge_count: int
    failures: tuple[str, ...]
    trace: tuple[TraceEvent, ...]
    trace_hash: str


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_trace(trace: Iterable[TraceEvent]) -> str:
    payload = [event.model_dump(mode="json") for event in trace]
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


class TurnReplayEngine:
    """Assemble final transcript segments and commit only on explicit EOT."""

    def replay(
        self,
        scenario: ReplayScenario,
        events: Iterable[ProviderEvent],
    ) -> ReplayResult:
        seen_event_ids: set[str] = set()
        accumulator = TranscriptAccumulator(
            max_segments=PENDING_TRANSCRIPT_MAX_SEGMENTS,
            max_characters=PENDING_TRANSCRIPT_MAX_CHARACTERS,
            max_age_seconds=PENDING_TRANSCRIPT_MAX_AGE_SECS,
        )
        committed_turns: list[str] = []
        trace: list[TraceEvent] = []
        last_at_ms = -1

        def append_trace(
            event: ProviderEvent,
            event_type: str,
            *,
            text: str = "",
            reason: str | None = None,
        ) -> None:
            trace.append(
                TraceEvent(
                    at_ms=event.at_ms,
                    type=event_type,
                    event_id=event.event_id,
                    text=text,
                    reason=reason,
                )
            )

        def commit(event: ProviderEvent, reason: str) -> None:
            if not accumulator.mark_boundary():
                append_trace(event, "empty_turn_boundary", reason=reason)
                return
            text = accumulator.text()
            committed_turns.append(text)
            accumulator.clear(committed=True)
            append_trace(event, "turn_committed", text=text, reason=reason)

        for event in events:
            if event.event_id in seen_event_ids:
                append_trace(event, "duplicate_ignored")
                continue
            seen_event_ids.add(event.event_id)

            if event.at_ms < last_at_ms:
                raise ValueError(
                    f"scenario {scenario.id!r} has decreasing at_ms at {event.event_id!r}"
                )
            last_at_ms = event.at_ms
            observed_at = event.at_ms / 1000

            if accumulator.exceeded_limits(observed_at):
                accumulator.clear()
                append_trace(event, "pending_limit_exceeded", reason="virtual_clock")
                continue

            if event.type == ProviderEventType.SPEECH_STARTED:
                accumulator.note_speech_resumed()
                append_trace(event, "speech_started")
                continue
            if event.type == ProviderEventType.SPEECH_RESUMED:
                accumulator.note_speech_resumed()
                append_trace(event, "speech_resumed")
                continue
            if event.type == ProviderEventType.UTTERANCE_END:
                commit(event, "utterance_end")
                continue
            if event.type == ProviderEventType.TURN_TIMEOUT:
                commit(event, "watchdog_timeout")
                continue

            transcript = normalize_transcript(event.text)
            append_trace(
                event,
                "transcript_final" if event.is_final else "transcript_interim",
                text=transcript,
            )
            if transcript and (event.is_final or event.speech_final):
                accumulator.add_final(
                    {},
                    transcript,
                    observed_at=observed_at,
                )
                if accumulator.exceeded_limits(observed_at):
                    accumulator.clear()
                    append_trace(event, "pending_limit_exceeded", reason="provider_event")
                    continue
            if event.speech_final:
                commit(event, "speech_final")

        pending_text = accumulator.text()
        expected_turns = tuple(normalize_transcript(text) for text in scenario.reference_turns)
        expected_pending = normalize_transcript(scenario.expected_pending_text)
        actual_turns = tuple(committed_turns)
        failures: list[str] = []
        if actual_turns != expected_turns:
            failures.append(f"turns expected={expected_turns!r} actual={actual_turns!r}")
        if pending_text != expected_pending:
            failures.append(f"pending expected={expected_pending!r} actual={pending_text!r}")

        committed_text = " ".join(actual_turns).casefold()
        retained = sum(
            1
            for entity in scenario.critical_entities
            if normalize_transcript(entity).casefold() in committed_text
        )
        result_trace = tuple(trace)
        return ReplayResult(
            scenario_id=scenario.id,
            passed=not failures,
            committed_turns=actual_turns,
            pending_text=pending_text,
            critical_entities_total=len(scenario.critical_entities),
            critical_entities_retained=retained,
            premature_split_count=max(0, len(actual_turns) - len(expected_turns)),
            incorrect_merge_count=max(0, len(expected_turns) - len(actual_turns)),
            failures=tuple(failures),
            trace=result_trace,
            trace_hash=_hash_trace(result_trace),
        )


def load_jsonl(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """Load strict JSONL while retaining useful path/line errors."""
    records: list[BaseModel] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            records.append(model.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"invalid JSONL in {path}:{line_number}: {exc}") from exc
    return records


def validate_audio_fixture(path: Path) -> dict[str, int]:
    """Require a non-empty 16 kHz mono 16-bit PCM WAV qualification fixture."""
    try:
        with wave.open(str(path), "rb") as audio:
            metadata = {
                "channels": audio.getnchannels(),
                "sample_width_bytes": audio.getsampwidth(),
                "sample_rate_hz": audio.getframerate(),
                "frame_count": audio.getnframes(),
                "compression_type": 0 if audio.getcomptype() == "NONE" else 1,
            }
    except (EOFError, wave.Error) as exc:
        raise ValueError(f"invalid WAV fixture {path}: {exc}") from exc

    expected = {
        "channels": 1,
        "sample_width_bytes": 2,
        "sample_rate_hz": 16_000,
        "compression_type": 0,
    }
    mismatches = {
        key: (metadata[key], value) for key, value in expected.items() if metadata[key] != value
    }
    if mismatches or metadata["frame_count"] <= 0:
        raise ValueError(
            f"audio fixture {path} must be non-empty mono 16 kHz 16-bit PCM WAV; "
            f"metadata={metadata}"
        )
    return metadata


def run_replay_suite(
    suite_path: Path,
    *,
    project_root: Path,
    repeats: int = 2,
) -> dict[str, Any]:
    """Replay every scenario and prove normalized traces repeat exactly."""
    if repeats < 2:
        raise ValueError("repeats must be at least 2 to prove determinism")

    raw_scenarios = load_jsonl(suite_path, ReplayScenario)
    scenarios = [ReplayScenario.model_validate(item) for item in raw_scenarios]
    engine = TurnReplayEngine()
    results: list[ReplayResult] = []
    trace_repeat_mismatches = 0

    for scenario in scenarios:
        events_path = Path(scenario.provider_events_path)
        if not events_path.is_absolute():
            events_path = project_root / events_path
        if scenario.audio_path:
            audio_path = Path(scenario.audio_path)
            if not audio_path.is_absolute():
                audio_path = project_root / audio_path
            if not audio_path.is_file():
                raise ValueError(
                    f"scenario {scenario.id!r} references missing audio fixture {audio_path}"
                )
            validate_audio_fixture(audio_path)
        raw_events = load_jsonl(events_path, ProviderEvent)
        events = [ProviderEvent.model_validate(item) for item in raw_events]
        repeated = [engine.replay(scenario, events) for _ in range(repeats)]
        first = repeated[0]
        if any(candidate.trace_hash != first.trace_hash for candidate in repeated[1:]):
            trace_repeat_mismatches += 1
        results.append(first)

    scenario_count = len(results)
    passed_count = sum(result.passed for result in results)
    entity_total = sum(result.critical_entities_total for result in results)
    entity_retained = sum(result.critical_entities_retained for result in results)
    combined_hash_payload = [
        {"scenario_id": result.scenario_id, "trace_hash": result.trace_hash} for result in results
    ]
    return {
        "schema_version": 1,
        "mode": "replay",
        "suite": str(suite_path),
        "scenario_count": scenario_count,
        "passed_count": passed_count,
        "scenario_pass_rate": passed_count / scenario_count if scenario_count else 0.0,
        "critical_entity_retention": (entity_retained / entity_total if entity_total else 1.0),
        "premature_split_count": sum(result.premature_split_count for result in results),
        "incorrect_merge_count": sum(result.incorrect_merge_count for result in results),
        "trace_repeat_mismatches": trace_repeat_mismatches,
        "combined_trace_hash": hashlib.sha256(
            _canonical_json(combined_hash_payload).encode()
        ).hexdigest(),
        "results": [result.model_dump(mode="json") for result in results],
    }


def evaluate_replay_gates(summary: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    """Evaluate only replay gates; live gates must remain explicitly unmeasured."""
    replay_gates = gates.get("replay")
    if not isinstance(replay_gates, dict):
        raise ValueError("gates file must contain a replay object")

    checks = {
        "scenario_pass_rate": {
            "actual": summary["scenario_pass_rate"],
            "operator": ">=",
            "expected": replay_gates["scenario_pass_rate_min"],
            "passed": summary["scenario_pass_rate"] >= replay_gates["scenario_pass_rate_min"],
        },
        "critical_entity_retention": {
            "actual": summary["critical_entity_retention"],
            "operator": ">=",
            "expected": replay_gates["critical_entity_retention_min"],
            "passed": summary["critical_entity_retention"]
            >= replay_gates["critical_entity_retention_min"],
        },
        "premature_split_count": {
            "actual": summary["premature_split_count"],
            "operator": "<=",
            "expected": replay_gates["premature_split_count_max"],
            "passed": summary["premature_split_count"] <= replay_gates["premature_split_count_max"],
        },
        "incorrect_merge_count": {
            "actual": summary["incorrect_merge_count"],
            "operator": "<=",
            "expected": replay_gates["incorrect_merge_count_max"],
            "passed": summary["incorrect_merge_count"] <= replay_gates["incorrect_merge_count_max"],
        },
        "trace_repeat_mismatches": {
            "actual": summary["trace_repeat_mismatches"],
            "operator": "<=",
            "expected": replay_gates["trace_repeat_mismatches_max"],
            "passed": summary["trace_repeat_mismatches"]
            <= replay_gates["trace_repeat_mismatches_max"],
        },
    }
    return {
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
        "unmeasured_gate_groups": sorted(
            key for key in gates if key not in {"schema_version", "replay"}
        ),
    }


def write_replay_artifacts(output_dir: Path, summary: dict[str, Any]) -> None:
    """Write the evidence layout promised by the ExecPlan."""
    output_dir.mkdir(parents=True, exist_ok=False)
    results = summary["results"]
    events = [
        {"scenario_id": result["scenario_id"], **event}
        for result in results
        for event in result["trace"]
    ]
    failures = [
        {"scenario_id": result["scenario_id"], "failures": result["failures"]}
        for result in results
        if result["failures"]
    ]
    transcripts = [
        {
            "scenario_id": result["scenario_id"],
            "committed_turns": result["committed_turns"],
            "pending_text": result["pending_text"],
        }
        for result in results
    ]

    (output_dir / "events.jsonl").write_text(
        "".join(f"{_canonical_json(event)}\n" for event in events)
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "normalized-transcript.json").write_text(json.dumps(transcripts, indent=2) + "\n")
    (output_dir / "state-trace.json").write_text(json.dumps(events, indent=2) + "\n")
    (output_dir / "artifact-diff.json").write_text("{}\n")
    (output_dir / "cost.json").write_text(
        json.dumps({"mode": "replay", "estimated_usd": 0.0}, indent=2) + "\n"
    )
    (output_dir / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")

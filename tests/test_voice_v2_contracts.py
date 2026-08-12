"""Fail-closed contract tests for Voice V2 events and reasoning records."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from murmur.reasoning import (
    ArtifactProposal,
    OperationsArtifactV1,
    ReasoningProgress,
    ReasoningRequest,
    ReasoningResult,
    SDLSceneArtifactV2,
    TaskTransition,
)
from murmur.reasoning.contracts import CanvasArtifact, TaskStatus
from murmur.voice.contracts import EventEnvelope, EventType
from pydantic import TypeAdapter, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _event(**overrides: object) -> EventEnvelope:
    values: dict[str, object] = {
        "event_id": "event-1",
        "event_type": "transport_connected",
        "trace_id": "trace-1",
        "voice_call_id": "call-1",
        "session_id": "session-1",
        "producer_id": "worker-1",
        "producer_sequence": 1,
        "emitted_at": datetime(2026, 8, 12, tzinfo=UTC),
        "payload": {},
    }
    values.update(overrides)
    return EventEnvelope.model_validate(values)


def _operations_artifact(**overrides: object) -> dict[str, object]:
    artifact: dict[str, object] = {
        "artifact_type": "operations_v1",
        "operations": [
            {
                "action": "rect",
                "id": "box-1",
                "width": 100,
                "height": 60,
            }
        ],
    }
    artifact.update(overrides)
    return artifact


def _reasoning_request(**overrides: object) -> ReasoningRequest:
    values: dict[str, object] = {
        "request_id": "request-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "agent_id": "agent-1",
        "agent_config_revision": "agent-revision-3",
        "turn_id": "turn-1",
        "task_id": "task-1",
        "task_generation": 1,
        "committed_turn_text": "Explain the Pythagorean theorem",
    }
    values.update(overrides)
    return ReasoningRequest.model_validate(values)


def test_event_envelope_tracks_producer_and_optional_ledger_sequences() -> None:
    emitted = _event(causation_id="event-0", correlation_id="flow-1")
    ingested = _event(event_id="event-2", producer_sequence=2, ledger_sequence=9)

    assert emitted.is_durable is False
    assert ingested.is_durable is True
    assert ingested.ledger_sequence == 9
    assert emitted.model_dump(mode="json")["event_type"] == "transport_connected"


def test_event_and_task_vocabularies_cover_the_reviewed_contract() -> None:
    assert {
        "transport_connected",
        "transport_reconnecting",
        "agent_ready",
        "transcript_segment",
        "turn_committed",
        "turn_resumed",
        "assistant_speech_started",
        "assistant_speech_stopped",
        "task_queued",
        "task_working",
        "task_needs_input",
        "task_verified",
        "task_failed",
        "task_cancelled",
        "task_superseded",
        "artifact_proposed",
        "artifact_accepted",
        "artifact_rejected",
        "canvas_patch",
        "canvas_apply_ack",
        "canvas_first_visible",
        "canvas_animation_complete",
        "canvas_render_failed",
        "usage_recorded",
        "session_ending",
        "session_ended",
    } <= {event_type.value for event_type in EventType}
    assert {status.value for status in TaskStatus} == {
        "queued",
        "working",
        "needs_input",
        "verified",
        "failed",
        "cancelled",
        "superseded",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("session_id", "has spaces"),
        ("producer_id", "../worker"),
        ("producer_sequence", 0),
        ("producer_sequence", "1"),
        ("ledger_sequence", 0),
        ("producer_sequence", 9_007_199_254_740_992),
    ],
)
def test_event_envelope_rejects_invalid_ids_and_sequences(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _event(**{field: value})


def test_event_envelope_fails_closed_on_unknown_version_type_and_fields() -> None:
    with pytest.raises(ValidationError):
        _event(schema_version=2)
    with pytest.raises(ValidationError):
        _event(event_type="future_event")
    with pytest.raises(ValidationError):
        _event(unexpected=True)
    with pytest.raises(ValidationError):
        _event(emitted_at=datetime(2026, 8, 12))
    with pytest.raises(ValidationError):
        _event(emitted_at=1_786_489_200)


def test_event_scope_and_canvas_revision_order_are_validated() -> None:
    with pytest.raises(ValidationError, match="requires turn_id"):
        _event(event_type="turn_committed")
    with pytest.raises(ValidationError, match="requires task_id"):
        _event(event_type="task_working")
    with pytest.raises(ValidationError, match="requires task_generation"):
        _event(event_type="canvas_patch", task_id="task-1")
    with pytest.raises(ValidationError, match="must equal"):
        _event(
            event_type="canvas_patch",
            task_id="task-1",
            task_generation=1,
            canvas_base_revision=3,
            canvas_result_revision=3,
        )

    patch = _event(
        event_type="canvas_patch",
        turn_id="turn-1",
        task_id="task-1",
        task_generation=1,
        canvas_base_revision=3,
        canvas_result_revision=4,
        payload={"artifact_id": "artifact-1", "artifact": {"artifact_type": "operations_v1"}},
    )
    assert patch.canvas_result_revision == 4


def test_canvas_patch_and_result_events_require_revision_causation_contract() -> None:
    with pytest.raises(ValidationError, match="base and result"):
        _event(
            event_type="canvas_patch",
            task_id="task-1",
            task_generation=1,
            payload={"artifact_id": "artifact-1", "artifact": {}},
        )
    with pytest.raises(ValidationError, match="canvas_result_revision"):
        _event(
            event_type="canvas_apply_ack",
            task_id="task-1",
            task_generation=1,
            causation_id="patch-1",
            payload={"artifact_id": "artifact-1"},
        )
    with pytest.raises(ValidationError, match="causation_id"):
        _event(
            event_type="canvas_apply_ack",
            task_id="task-1",
            task_generation=1,
            canvas_result_revision=1,
            payload={"artifact_id": "artifact-1"},
        )


def test_event_envelope_is_frozen() -> None:
    event = _event(payload={"connection_id": "rtc-1"})

    with pytest.raises(ValidationError, match="frozen"):
        event.producer_sequence = 2
    with pytest.raises(TypeError):
        event.payload["connection_id"] = "changed"  # type: ignore[index]


def test_event_payloads_are_typed_and_fail_closed() -> None:
    ready_payload = {
        "profile_id": "cascade-v1",
        "required_components": ["worker", "input", "output", "event_channel", "tts"],
        "ready_components": ["worker", "input", "output", "event_channel", "tts"],
    }
    assert (
        _event(event_type="agent_ready", payload=ready_payload).event_type is EventType.AGENT_READY
    )

    with pytest.raises(ValidationError):
        _event(event_type="agent_ready", payload={})
    with pytest.raises(ValidationError):
        _event(
            event_type="agent_ready",
            payload={**ready_payload, "ready_components": ["worker", "input"]},
        )
    with pytest.raises(ValidationError):
        _event(event_type="transport_connected", payload={"future": True})


def test_typescript_fixture_round_trips_through_python_contract() -> None:
    fixture = json.loads(
        (PROJECT_ROOT / "web/src/features/voice/voice-event.fixture.json").read_text()
    )

    event = EventEnvelope.model_validate(fixture)

    assert event.model_dump(mode="json", exclude_none=True) == fixture


def test_task_lifecycle_allows_declared_progression() -> None:
    queued = TaskTransition(
        task_id="task-1",
        task_generation=1,
        from_status=None,
        to_status="queued",
    )
    working = TaskTransition(
        task_id="task-1",
        task_generation=1,
        from_status=queued.to_status,
        to_status="working",
    )
    verified = TaskTransition(
        task_id="task-1",
        task_generation=1,
        from_status=working.to_status,
        to_status="verified",
    )

    assert verified.to_status.value == "verified"


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (None, "working"),
        ("queued", "verified"),
        ("working", "working"),
        ("verified", "working"),
        ("cancelled", "verified"),
    ],
)
def test_task_lifecycle_rejects_invalid_transitions(
    from_status: str | None,
    to_status: str,
) -> None:
    with pytest.raises(ValidationError, match="invalid task transition"):
        TaskTransition(
            task_id="task-1",
            task_generation=1,
            from_status=from_status,
            to_status=to_status,
        )


def test_operations_artifact_validates_and_serializes_by_discriminator() -> None:
    artifact = TypeAdapter(CanvasArtifact).validate_python(
        _operations_artifact(
            operations=[
                {
                    "action": "text",
                    "id": "label-1",
                    "label": "main answer",
                    "text": "a squared plus b squared",
                    "_centered": True,
                },
                {
                    "action": "curve",
                    "id": "curve-1",
                    "points": [[0, 0], [20, 40], [50, 50]],
                },
            ]
        )
    )

    assert isinstance(artifact, OperationsArtifactV1)
    assert artifact.operations[0].centered is True
    dumped = artifact.model_dump(mode="json", by_alias=True)
    assert dumped["artifact_type"] == "operations_v1"
    assert dumped["operations"][0]["_centered"] is True


def test_operations_artifact_accepts_existing_normalized_mutation_targets() -> None:
    artifact = TypeAdapter(CanvasArtifact).validate_python(
        _operations_artifact(
            operations=[
                {"action": "rect", "id": "box-1", "width": 100, "height": 60},
                {"action": "highlight", "id": "box-1"},
                {"action": "delete", "target_id": "box-1"},
            ]
        )
    )

    assert isinstance(artifact, OperationsArtifactV1)
    assert artifact.operations[1].id == "box-1"
    assert artifact.operations[2].target_id == "box-1"


@pytest.mark.parametrize(
    "artifact",
    [
        {"artifact_type": "future_v3", "operations": []},
        {"artifact_type": "operations_v1", "operations": []},
        {"artifact_type": "operations_v1", "operations": [{"action": "rect"}]},
        {"artifact_type": "operations_v1", "operations": [{"action": "delete"}]},
        {
            "artifact_type": "operations_v1",
            "operations": [{"action": "highlight", "id": "one", "target_id": "another"}],
        },
        {
            "artifact_type": "operations_v1",
            "operations": [{"action": "line", "id": "line-1", "points": [[0, 0]]}],
        },
        {
            "artifact_type": "operations_v1",
            "operations": [{"action": "text", "id": "text-1", "text": "ok", "future": 1}],
        },
        {
            "artifact_type": "operations_v1",
            "operations": [
                {"action": "text", "id": "same", "text": "One"},
                {"action": "text", "id": "same", "text": "Two"},
            ],
        },
    ],
)
def test_operations_artifact_rejects_invalid_variants_and_operations(
    artifact: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(CanvasArtifact).validate_python(artifact)


def test_artifact_numeric_and_boolean_fields_do_not_coerce_strings() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(CanvasArtifact).validate_python(
            _operations_artifact(
                operations=[{"action": "rect", "id": "box-1", "width": "100", "height": 60}]
            )
        )
    with pytest.raises(ValidationError):
        TypeAdapter(CanvasArtifact).validate_python(
            {
                "artifact_type": "sdl_scene_v2",
                "scene": {"steps": [{"say": "Look", "clear": "false"}]},
            }
        )


def test_sdl_scene_artifact_uses_existing_component_vocabulary() -> None:
    artifact = TypeAdapter(CanvasArtifact).validate_python(
        {
            "artifact_type": "sdl_scene_v2",
            "scene": {
                "steps": [
                    {
                        "say": "Start with a right triangle.",
                        "show": {
                            "component": "right_triangle",
                            "props": {"sides": ["a", "b", "c"]},
                            "position": {"rightOf": "intro", "gap": 20},
                            "id": "triangle-1",
                        },
                    }
                ]
            },
        }
    )

    assert isinstance(artifact, SDLSceneArtifactV2)
    dumped = artifact.model_dump(mode="json", by_alias=True)
    assert dumped["scene"]["steps"][0]["show"]["position"]["rightOf"] == "intro"
    show = artifact.scene.steps[0].show
    assert show is not None
    with pytest.raises(TypeError):
        show.props["sides"] = ["changed"]  # type: ignore[index]


@pytest.mark.parametrize(
    "scene",
    [
        {"steps": []},
        {"steps": [{"say": ""}]},
        {"steps": [{"say": "Look", "show": {"component": "video", "props": {}}}]},
        {"steps": [{"say": "Look", "unknown": True}]},
    ],
)
def test_sdl_scene_artifact_rejects_invalid_scenes(scene: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(CanvasArtifact).validate_python(
            {"artifact_type": "sdl_scene_v2", "scene": scene}
        )


def test_reasoning_records_are_versioned_strict_and_correlated() -> None:
    request = _reasoning_request(
        memory_reference_ids=("memory-1",),
        resource_reference_ids=("resource-1",),
        tool_policy={"allowed_tools": ["web_search"], "allow_side_effects": False},
    )
    progress = ReasoningProgress(
        progress_id="progress-1",
        request_id=request.request_id,
        session_id=request.session_id,
        task_id=request.task_id,
        task_generation=request.task_generation,
        sequence=1,
        kind="progress",
        message="Checking the proof",
    )
    proposal = ArtifactProposal(
        proposal_id="proposal-1",
        request_id=request.request_id,
        session_id=request.session_id,
        task_id=request.task_id,
        task_generation=request.task_generation,
        base_revision=request.canvas_base_revision,
        artifact=_operations_artifact(),
    )
    result = ReasoningResult(
        result_id="result-1",
        request_id=request.request_id,
        session_id=request.session_id,
        task_id=request.task_id,
        task_generation=request.task_generation,
        answer="The theorem relates the three sides of a right triangle.",
        artifact_proposal_ids=(proposal.proposal_id,),
    )

    assert request.schema_version == progress.schema_version == result.schema_version == 1
    assert proposal.artifact.artifact_type == "operations_v1"
    assert result.artifact_proposal_ids == ("proposal-1",)


def test_reasoning_records_reject_invalid_values_and_are_frozen() -> None:
    with pytest.raises(ValidationError):
        _reasoning_request(task_generation=0)
    with pytest.raises(ValidationError):
        _reasoning_request(committed_turn_text="   ")
    with pytest.raises(ValidationError):
        _reasoning_request(schema_version=2)
    with pytest.raises(ValidationError):
        _reasoning_request(extra_field=True)
    with pytest.raises(ValidationError):
        _reasoning_request(memory_reference_ids=("memory-1", "memory-1"))
    with pytest.raises(ValidationError):
        _reasoning_request(tool_policy={"allow_side_effects": "false"})
    with pytest.raises(ValidationError):
        ReasoningProgress(
            progress_id="progress-1",
            request_id="request-1",
            session_id="session-1",
            task_id="task-1",
            task_generation=1,
            sequence=0,
            kind="progress",
            message="Still working",
        )
    with pytest.raises(ValidationError):
        ReasoningResult(
            result_id="result-1",
            request_id="request-1",
            session_id="session-1",
            task_id="task-1",
            task_generation=1,
            answer="Done",
            artifact_proposal_ids=("proposal-1", "proposal-1"),
        )

    request = _reasoning_request()
    with pytest.raises(ValidationError, match="frozen"):
        request.committed_turn_text = "Changed"

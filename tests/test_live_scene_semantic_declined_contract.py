from __future__ import annotations

import json

import pytest
from murmur.live_scene.contracts import SCENE_STREAM_EVENT_ADAPTER
from murmur.live_scene.semantic_contracts import VisualActAbstainReason
from murmur.live_scene.semantic_service_contracts import (
    SEMANTIC_SCENE_STREAM_EVENT_ADAPTER,
    SemanticSceneStreamDeclinedEvent,
)
from murmur.live_scene.semantic_wire import encode_semantic_scene_stream_event
from pydantic import ValidationError


@pytest.mark.parametrize("reason", list(VisualActAbstainReason))
def test_declined_event_round_trips_through_the_semantic_wire(
    reason: VisualActAbstainReason,
) -> None:
    event = SemanticSceneStreamDeclinedEvent(
        generation=7,
        attempt=2,
        final_revision=0,
        reason_code=reason,
        message="No supported visual change is available.",
    )

    wire = encode_semantic_scene_stream_event(event)
    assert wire.endswith("\n\n")
    payload = json.loads(wire.removeprefix("data: ").removesuffix("\n\n"))
    assert payload == {
        "type": "semantic_scene_stream_declined",
        "generation": 7,
        "attempt": 2,
        "finalRevision": 0,
        "reasonCode": reason.value,
        "message": "No supported visual change is available.",
    }
    assert SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event


def test_declined_event_is_semantic_only() -> None:
    payload = {
        "type": "semantic_scene_stream_declined",
        "generation": 1,
        "attempt": 1,
        "finalRevision": 8,
        "reasonCode": "no_forward_progress",
        "message": "The accepted component is already complete.",
    }

    assert isinstance(
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload),
        SemanticSceneStreamDeclinedEvent,
    )
    with pytest.raises(ValidationError):
        SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", 0),
        ("attempt", 3),
        ("finalRevision", -1),
        ("reasonCode", "model_refused"),
        ("message", ""),
    ],
)
def test_declined_event_rejects_invalid_fields(field: str, value: object) -> None:
    payload = {
        "type": "semantic_scene_stream_declined",
        "generation": 1,
        "attempt": 1,
        "finalRevision": 0,
        "reasonCode": "unsupported_intent",
        "message": "No supported visual change is available.",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


def test_declined_event_rejects_unknown_fields_and_is_immutable() -> None:
    event = SemanticSceneStreamDeclinedEvent(
        generation=1,
        attempt=1,
        final_revision=0,
        reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
        message="No supported visual change is available.",
    )
    payload = event.model_dump(mode="json", by_alias=True)
    payload["providerText"] = "not allowed"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)
    with pytest.raises(ValidationError, match="frozen"):
        event.message = "changed"

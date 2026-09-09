from __future__ import annotations

import json

import pytest
from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.choreography_wire import (
    MAX_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_choreography_scene_stream_event,
)
from murmur.live_scene.completing_square_problem_parser import (
    CompletingSquareProblemFailureReason,
)
from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.parametric_choreography_service_contracts import (
    ParametricChoreographyFailureCode,
    ParametricChoreographySceneStreamDeclinedEventV3,
    ParametricChoreographySceneStreamFailedEventV3,
)
from murmur.live_scene.parametric_choreography_wire import (
    MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import VisualActAbstainReason
from murmur.live_scene.wire import MAX_SSE_EVENT_BYTES, SceneStreamWireError
from pydantic import ValidationError


def _declined(
    message: str = "The corner premise is unsupported — the accepted board stays safe ✓.",
) -> ParametricChoreographySceneStreamDeclinedEventV3:
    return ParametricChoreographySceneStreamDeclinedEventV3(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code=CompletingSquareProblemFailureReason.CONFLICT,
        message=message,
    )


def test_parametric_encoder_is_compact_unicode_and_uses_its_own_64k_limit() -> None:
    event = _declined()
    wire = encode_parametric_choreography_scene_stream_event(event)
    payload = json.loads(wire.removeprefix("data: ").strip())

    assert payload == event.model_dump(mode="json", by_alias=True)
    assert wire.startswith('data: {"type":"parametric_choreography_scene_stream_declined"')
    assert wire.endswith("\n\n")
    assert "✓" in wire
    assert "\\u2713" not in wire
    assert (
        MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES
        == MAX_CHOREOGRAPHY_SSE_EVENT_BYTES
        == MAX_NDJSON_FRAME_BYTES
        == 64 * 1024
    )
    assert MAX_SSE_EVENT_BYTES == 96 * 1024


def test_parametric_encoder_enforces_exact_utf8_byte_budget() -> None:
    event = _declined("Explain x² using equal tiles ✓.")
    wire = encode_parametric_choreography_scene_stream_event(event)
    byte_length = len(wire.encode("utf-8"))

    assert byte_length > len(wire)
    assert (
        encode_parametric_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length,
        )
        == wire
    )
    with pytest.raises(SceneStreamWireError, match="browser wire budget"):
        encode_parametric_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length - 1,
        )


@pytest.mark.parametrize(
    "budget",
    [
        0,
        -1,
        True,
        1.0,
        MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES + 1,
    ],
)
def test_parametric_encoder_rejects_invalid_budget_overrides(budget: object) -> None:
    with pytest.raises(ValueError, match="integer between 1"):
        encode_parametric_choreography_scene_stream_event(
            _declined(),
            max_event_bytes=budget,  # type: ignore[arg-type]
        )


def test_parametric_encoder_revalidates_discriminator_and_closed_failure_code() -> None:
    unknown = {
        "type": "scene_patch",
        "generation": 4,
        "attempt": 1,
        "sequence": 1,
        "baseRevision": 0,
        "resultRevision": 1,
        "patch": {},
    }
    open_failure = {
        "type": "parametric_choreography_scene_stream_failed",
        "generation": 4,
        "attempt": 1,
        "code": "private_provider_detail",
        "message": "The lesson failed.",
        "lastAcceptedRevision": 0,
        "retryable": False,
    }

    with pytest.raises(ValidationError):
        encode_parametric_choreography_scene_stream_event(unknown)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        encode_parametric_choreography_scene_stream_event(open_failure)  # type: ignore[arg-type]


def test_failed_event_encodes_only_the_closed_v3_shape() -> None:
    event = ParametricChoreographySceneStreamFailedEventV3(
        generation=4,
        attempt=1,
        code=ParametricChoreographyFailureCode.PROVIDER_TIMEOUT,
        message="Visual routing timed out.",
        last_accepted_revision=5,
        retryable=True,
    )
    payload = json.loads(
        encode_parametric_choreography_scene_stream_event(event).removeprefix("data: ").strip()
    )

    assert payload == {
        "type": "parametric_choreography_scene_stream_failed",
        "generation": 4,
        "attempt": 1,
        "code": "provider_timeout",
        "message": "Visual routing timed out.",
        "lastAcceptedRevision": 5,
        "retryable": True,
    }


def test_v2_and_v3_encoders_remain_mutually_isolated() -> None:
    v2 = ChoreographySceneStreamDeclinedEvent(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code=VisualActAbstainReason.NO_FORWARD_PROGRESS,
        message="The fixed lesson is unchanged.",
    )
    v3 = _declined()

    assert encode_choreography_scene_stream_event(v2).startswith(
        'data: {"type":"choreography_scene_stream_declined"'
    )
    assert encode_parametric_choreography_scene_stream_event(v3).startswith(
        'data: {"type":"parametric_choreography_scene_stream_declined"'
    )
    with pytest.raises(ValidationError):
        encode_parametric_choreography_scene_stream_event(v2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        encode_choreography_scene_stream_event(v3)  # type: ignore[arg-type]

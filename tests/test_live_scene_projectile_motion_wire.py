from __future__ import annotations

import json

import pytest
from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.choreography_wire import encode_choreography_scene_stream_event
from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.parametric_choreography_service_contracts import (
    ParametricChoreographySceneStreamDeclinedEventV3,
)
from murmur.live_scene.parametric_choreography_wire import (
    encode_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.projectile_motion_service_contracts import (
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographyFailureCode,
    ProjectileChoreographySceneStreamDeclinedEventV1,
    ProjectileChoreographySceneStreamFailedEventV1,
)
from murmur.live_scene.projectile_motion_wire import (
    MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import VisualActAbstainReason
from murmur.live_scene.wire import SceneStreamWireError
from pydantic import ValidationError


def _declined(
    message: str = "The projectile board stays unchanged — try a supported question ✓.",
) -> ProjectileChoreographySceneStreamDeclinedEventV1:
    return ProjectileChoreographySceneStreamDeclinedEventV1(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code=ProjectileChoreographyDeclineReason.UNSUPPORTED_INTENT,
        message=message,
    )


def test_projectile_encoder_is_data_only_compact_camelcase_unicode_and_64k_bounded() -> None:
    event = _declined()
    wire = encode_projectile_choreography_scene_stream_event(event)
    payload = json.loads(wire.removeprefix("data: ").strip())

    assert payload == event.model_dump(mode="json", by_alias=True)
    assert wire.startswith('data: {"type":"projectile_choreography_scene_stream_declined"')
    assert wire.endswith("\n\n")
    assert "event:" not in wire
    assert "id:" not in wire
    assert "retry:" not in wire
    assert "✓" in wire
    assert "\\u2713" not in wire
    assert MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES == MAX_NDJSON_FRAME_BYTES == 64 * 1024


def test_projectile_encoder_enforces_exact_utf8_byte_budget() -> None:
    event = _declined("Explain the launch using equal time slices ✓.")
    wire = encode_projectile_choreography_scene_stream_event(event)
    byte_length = len(wire.encode("utf-8"))

    assert byte_length > len(wire)
    assert (
        encode_projectile_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length,
        )
        == wire
    )
    with pytest.raises(SceneStreamWireError, match="browser wire budget"):
        encode_projectile_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length - 1,
        )


@pytest.mark.parametrize(
    "budget",
    [0, -1, True, 1.0, MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES + 1],
)
def test_projectile_encoder_rejects_invalid_budget_overrides(budget: object) -> None:
    with pytest.raises(ValueError, match="integer between 1"):
        encode_projectile_choreography_scene_stream_event(
            _declined(),
            max_event_bytes=budget,  # type: ignore[arg-type]
        )


def test_projectile_failed_event_encodes_only_the_closed_shape() -> None:
    event = ProjectileChoreographySceneStreamFailedEventV1(
        generation=4,
        attempt=1,
        code=ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT,
        message="Projectile routing timed out.",
        last_accepted_revision=5,
        retryable=True,
    )
    payload = json.loads(
        encode_projectile_choreography_scene_stream_event(event).removeprefix("data: ").strip()
    )
    assert payload == {
        "type": "projectile_choreography_scene_stream_failed",
        "generation": 4,
        "attempt": 1,
        "code": "provider_timeout",
        "message": "Projectile routing timed out.",
        "lastAcceptedRevision": 5,
        "retryable": True,
    }


def test_projectile_encoder_revalidates_discriminator_and_closed_failure_code() -> None:
    wrong_discriminator = {
        "type": "scene_patch",
        "generation": 4,
        "attempt": 1,
        "sequence": 1,
        "baseRevision": 0,
        "resultRevision": 1,
        "patch": {},
    }
    open_failure = {
        "type": "projectile_choreography_scene_stream_failed",
        "generation": 4,
        "attempt": 1,
        "code": "private_provider_detail",
        "message": "The lesson failed.",
        "lastAcceptedRevision": 0,
        "retryable": False,
    }

    with pytest.raises(ValidationError):
        encode_projectile_choreography_scene_stream_event(  # type: ignore[arg-type]
            wrong_discriminator
        )
    with pytest.raises(ValidationError):
        encode_projectile_choreography_scene_stream_event(open_failure)  # type: ignore[arg-type]


def test_gate15_gate16_and_projectile_encoders_are_mutually_isolated() -> None:
    gate15 = ChoreographySceneStreamDeclinedEvent(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code=VisualActAbstainReason.NO_FORWARD_PROGRESS,
        message="The fixed lesson is unchanged.",
    )
    gate16 = ParametricChoreographySceneStreamDeclinedEventV3(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code=VisualActAbstainReason.NO_FORWARD_PROGRESS,
        message="The parametric lesson is unchanged.",
    )
    projectile = _declined()

    assert encode_choreography_scene_stream_event(gate15).startswith(
        'data: {"type":"choreography_scene_stream_declined"'
    )
    assert encode_parametric_choreography_scene_stream_event(gate16).startswith(
        'data: {"type":"parametric_choreography_scene_stream_declined"'
    )
    assert encode_projectile_choreography_scene_stream_event(projectile).startswith(
        'data: {"type":"projectile_choreography_scene_stream_declined"'
    )
    with pytest.raises(ValidationError):
        encode_projectile_choreography_scene_stream_event(gate15)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        encode_projectile_choreography_scene_stream_event(gate16)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        encode_choreography_scene_stream_event(projectile)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        encode_parametric_choreography_scene_stream_event(  # type: ignore[arg-type]
            projectile
        )

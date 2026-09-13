from __future__ import annotations

import json

import pytest
from murmur.live_scene.choreography_service_contracts import (
    CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    SCENE_STREAM_EVENT_ADAPTER,
    SceneState,
    SceneStreamStartedEvent,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricChoreographySceneStreamDeclinedEventV3,
)
from murmur.live_scene.projectile_motion_service_contracts import (
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographySceneStreamDeclinedEventV1,
)
from murmur.live_scene.semantic_contracts import VisualActAbstainReason
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    compile_certified_semantic_storyboard_anchor,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    TraceStoryboardRecordV1,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER,
    SemanticStoryboardCompletionReason,
    SemanticStoryboardSceneCheckpointEventV1,
    SemanticStoryboardSceneStreamDeclinedEventV1,
)
from murmur.live_scene.semantic_storyboard_wire import (
    MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES,
    encode_semantic_storyboard_scene_stream_event,
)
from murmur.live_scene.wire import SceneStreamWireError
from pydantic import ValidationError


def _declined(
    message: str = "The accepted storyboard is unchanged — try another idea ✓.",
) -> SemanticStoryboardSceneStreamDeclinedEventV1:
    return SemanticStoryboardSceneStreamDeclinedEventV1(
        generation=4,
        attempt=1,
        base_revision=5,
        final_revision=5,
        reason_code="unsupported_intent",
        message=message,
    )


def _checkpoint_event(transition, *, sequence: int) -> SemanticStoryboardSceneCheckpointEventV1:
    return SemanticStoryboardSceneCheckpointEventV1(
        generation=4,
        attempt=1,
        sequence=sequence,
        base_revision=transition.base_scene.revision,
        result_revision=transition.result_scene.revision,
        patch=transition.checkpoint.patch,
        transition=transition,
    )


def test_encoder_is_compact_data_only_camelcase_unicode_and_64k_bounded() -> None:
    event = _declined()
    wire = encode_semantic_storyboard_scene_stream_event(event)
    payload = json.loads(wire.removeprefix("data: ").strip())

    assert payload == event.model_dump(mode="json", by_alias=True)
    assert wire.startswith('data: {"type":"semantic_storyboard_scene_stream_declined"')
    assert wire.endswith("\n\n")
    assert "event:" not in wire
    assert "id:" not in wire
    assert "retry:" not in wire
    assert "✓" in wire
    assert "\\u2713" not in wire
    assert MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES == MAX_NDJSON_FRAME_BYTES == 64 * 1024


def test_encoder_enforces_the_exact_utf8_byte_budget() -> None:
    event = _declined("Keep the accepted comparison exactly as shown ✓.")
    wire = encode_semantic_storyboard_scene_stream_event(event)
    size = len(wire.encode("utf-8"))

    assert size > len(wire)
    assert encode_semantic_storyboard_scene_stream_event(event, max_event_bytes=size) == wire
    with pytest.raises(SceneStreamWireError, match="browser wire budget"):
        encode_semantic_storyboard_scene_stream_event(
            event,
            max_event_bytes=size - 1,
        )


@pytest.mark.parametrize(
    "budget",
    [0, -1, True, 1.0, MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES + 1],
)
def test_encoder_rejects_invalid_budget_overrides(budget: object) -> None:
    with pytest.raises(ValueError, match="integer between 1"):
        encode_semantic_storyboard_scene_stream_event(
            _declined(),
            max_event_bytes=budget,  # type: ignore[arg-type]
        )


def test_every_supported_problem_and_full_legal_frontier_fits_one_sse_event() -> None:
    sizes: list[int] = []
    for speed in (20, 25, 30):
        for angles in ((30, 45), (30, 60), (45, 60)):
            problem = PairedProjectileComparisonSpecV1(
                speedMps=speed,
                anglesDeg=angles,
            )
            transition = compile_certified_semantic_storyboard_anchor(
                problem,
                base_scene=SceneState(revision=0),
                base_semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
            )
            sizes.append(
                len(
                    encode_semantic_storyboard_scene_stream_event(
                        _checkpoint_event(transition, sequence=1)
                    ).encode("utf-8")
                )
            )

            claim = "equal_range" if sum(angles) == 90 else "unequal_range"
            records = [
                TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="lower_angle"),
                TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="higher_angle"),
                RevealStoryboardRecordV1(v=1, act="reveal", conceptId="range_formula"),
            ]
            if sum(angles) == 90:
                records.append(
                    RevealStoryboardRecordV1(
                        v=1,
                        act="reveal",
                        conceptId="complementary_angles",
                    )
                )
            records.extend(
                (
                    RelateStoryboardRecordV1(
                        v=1,
                        act="relate",
                        claimId=claim,
                        evidenceIds=("lower_trajectory", "higher_trajectory"),
                    ),
                    RelateStoryboardRecordV1(
                        v=1,
                        act="relate",
                        claimId="higher_apex",
                        evidenceIds=("lower_trajectory", "higher_trajectory"),
                    ),
                    RelateStoryboardRecordV1(
                        v=1,
                        act="relate",
                        claimId="longer_flight",
                        evidenceIds=("lower_trajectory", "higher_trajectory"),
                    ),
                )
            )
            for ordinal, record in enumerate(records, start=1):
                beat = route_semantic_storyboard_record(
                    record,
                    problem_spec=problem,
                    semantic_scene=transition.result_semantic_scene,
                )
                transition = compile_certified_semantic_storyboard_checkpoint(
                    beat,
                    base_scene=transition.result_scene,
                    base_semantic_scene=transition.result_semantic_scene,
                )
                sizes.append(
                    len(
                        encode_semantic_storyboard_scene_stream_event(
                            _checkpoint_event(
                                transition,
                                sequence=((ordinal - 1) % 5) + 1,
                            )
                        ).encode("utf-8")
                    )
                )

    assert len(sizes) == 66
    assert max(sizes) <= MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES


def test_new_and_every_older_decoder_reject_one_another() -> None:
    old_events = (
        SceneStreamStartedEvent(generation=4, attempt=1, base_revision=5),
        ChoreographySceneStreamDeclinedEvent(
            generation=4,
            attempt=1,
            final_revision=5,
            reason_code=VisualActAbstainReason.NO_FORWARD_PROGRESS,
            message="The fixed lesson is unchanged.",
        ),
        ParametricChoreographySceneStreamDeclinedEventV3(
            generation=4,
            attempt=1,
            final_revision=5,
            reason_code=VisualActAbstainReason.NO_FORWARD_PROGRESS,
            message="The parametric lesson is unchanged.",
        ),
        ProjectileChoreographySceneStreamDeclinedEventV1(
            generation=4,
            attempt=1,
            final_revision=5,
            reason_code=ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS,
            message="The projectile lesson is unchanged.",
        ),
    )
    new_payload = _declined().model_dump(mode="json", by_alias=True)

    for old in old_events:
        with pytest.raises(ValidationError):
            SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(
                old.model_dump(mode="json", by_alias=True)
            )
        with pytest.raises(ValidationError):
            encode_semantic_storyboard_scene_stream_event(old)  # type: ignore[arg-type]

    for adapter in (
        SCENE_STREAM_EVENT_ADAPTER,
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
        PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(new_payload)


def test_encoder_revalidates_discriminator_nested_aliases_and_closed_terminal_reason() -> None:
    wrong_discriminator = _declined().model_dump(mode="json", by_alias=True)
    wrong_discriminator["type"] = "scene_stream_completed"
    snake_case = _declined().model_dump(mode="json", by_alias=True)
    snake_case["base_revision"] = snake_case.pop("baseRevision")
    open_reason = _declined().model_dump(mode="json", by_alias=True)
    open_reason["reasonCode"] = "private_refusal"

    for payload in (wrong_discriminator, snake_case, open_reason):
        with pytest.raises(ValidationError):
            encode_semantic_storyboard_scene_stream_event(payload)  # type: ignore[arg-type]


def test_completion_reason_vocabulary_is_not_an_open_string() -> None:
    assert {reason.value for reason in SemanticStoryboardCompletionReason} == {
        "anchor",
        "model_stop",
        "accepted_prefix",
    }

from __future__ import annotations

import json
from copy import deepcopy
from functools import cache

import pytest
from murmur.live_scene.contracts import SceneState
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    ValidatedSemanticStoryboardTransitionV1,
    compile_certified_semantic_storyboard_anchor,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    StoryboardAbstainReasonCode,
    TraceStoryboardRecordV1,
)
from murmur.live_scene.semantic_storyboard_routing import (
    route_semantic_storyboard_record,
)
from murmur.live_scene.semantic_storyboard_service_contracts import (
    MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
    SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER,
    SemanticStoryboardAcceptedPrefixCause,
    SemanticStoryboardCompletionReason,
    SemanticStoryboardFailureCode,
    SemanticStoryboardSceneCheckpointEventV1,
    SemanticStoryboardSceneStreamCompletedEventV1,
    SemanticStoryboardSceneStreamDeclinedEventV1,
    SemanticStoryboardSceneStreamFailedEventV1,
    SemanticStoryboardSceneStreamStartedEventV1,
    dump_semantic_storyboard_scene_stream_event,
)
from pydantic import ValidationError


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


@cache
def _anchor() -> ValidatedSemanticStoryboardTransitionV1:
    return compile_certified_semantic_storyboard_anchor(
        _problem(),
        base_scene=SceneState(revision=0),
        base_semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
    )


@cache
def _model_transition() -> ValidatedSemanticStoryboardTransitionV1:
    anchor = _anchor()
    beat = route_semantic_storyboard_record(
        TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="lower_angle"),
        problem_spec=_problem(),
        semantic_scene=anchor.result_semantic_scene,
    )
    return compile_certified_semantic_storyboard_checkpoint(
        beat,
        base_scene=anchor.result_scene,
        base_semantic_scene=anchor.result_semantic_scene,
    )


def _checkpoint_event(
    transition: ValidatedSemanticStoryboardTransitionV1 | None = None,
    *,
    sequence: int = 1,
) -> SemanticStoryboardSceneCheckpointEventV1:
    bound = _anchor() if transition is None else transition
    return SemanticStoryboardSceneCheckpointEventV1(
        generation=7,
        attempt=1,
        sequence=sequence,
        base_revision=bound.base_scene.revision,
        result_revision=bound.result_scene.revision,
        patch=bound.checkpoint.patch,
        transition=bound,
    )


def test_started_and_anchor_checkpoint_have_exact_isolated_wire_shapes() -> None:
    started = SemanticStoryboardSceneStreamStartedEventV1(
        generation=7,
        attempt=1,
        base_revision=0,
    )
    checkpoint = _checkpoint_event()

    started_payload = dump_semantic_storyboard_scene_stream_event(started)
    payload = dump_semantic_storyboard_scene_stream_event(checkpoint)

    assert started_payload == {
        "type": "semantic_storyboard_scene_stream_started",
        "generation": 7,
        "attempt": 1,
        "baseRevision": 0,
    }
    assert set(payload) == {
        "type",
        "generation",
        "attempt",
        "sequence",
        "baseRevision",
        "resultRevision",
        "patch",
        "transition",
    }
    assert payload["type"] == "semantic_storyboard_scene_checkpoint"
    assert payload["patch"] == payload["transition"]["checkpoint"]["patch"]
    assert SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == checkpoint
    with pytest.raises(ValidationError, match="frozen"):
        checkpoint.sequence = 2


def test_model_checkpoint_binds_the_full_low_level_and_semantic_transition() -> None:
    transition = _model_transition()
    event = _checkpoint_event(transition)
    body = transition.checkpoint.certificate.body

    assert event.base_revision == transition.base_scene.revision == 1
    assert event.result_revision == transition.result_scene.revision == 2
    assert transition.base_semantic_scene.revision == event.base_revision
    assert transition.result_semantic_scene.revision == event.result_revision
    assert transition.result_semantic_scene.certificate_head_sha256 == (
        transition.checkpoint.certificate.certificate_sha256
    )
    assert body.base_low_level_revision == event.base_revision
    assert body.result_low_level_revision == event.result_revision
    assert event.patch == transition.checkpoint.patch


def test_model_checkpoint_sequence_is_strictly_bounded_to_one_provider_turn() -> None:
    payload = _checkpoint_event(
        _model_transition(),
        sequence=MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
    ).model_dump(mode="json", by_alias=True)
    payload["sequence"] = MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS + 1
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        SemanticStoryboardSceneCheckpointEventV1.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("base_revision", "advance baseRevision"),
        ("result_revision", "advance baseRevision"),
        ("patch", "event patch"),
        ("anchor_sequence", "anchor checkpoint sequence"),
        ("attempt_two", "less than or equal to 1"),
        ("attempt_bool", "valid integer"),
    ],
)
def test_checkpoint_rejects_spliced_revisions_patch_origin_sequence_and_attempt(
    mutation: str,
    message: str,
) -> None:
    payload = _checkpoint_event().model_dump(mode="json", by_alias=True)
    if mutation == "base_revision":
        payload["baseRevision"] = 1
    elif mutation == "result_revision":
        payload["resultRevision"] = 2
    elif mutation == "patch":
        payload["patch"] = _model_transition().checkpoint.patch.model_dump(
            mode="json", by_alias=True
        )
    elif mutation == "anchor_sequence":
        payload["sequence"] = 2
    elif mutation == "attempt_two":
        payload["attempt"] = 2
    else:
        payload["attempt"] = True

    with pytest.raises(ValidationError, match=message):
        SemanticStoryboardSceneCheckpointEventV1.model_validate(payload)


def test_wire_adapter_requires_canonical_aliases_at_every_nested_layer() -> None:
    canonical = _checkpoint_event(_model_transition()).model_dump(mode="json", by_alias=True)
    payloads: list[dict[str, object]] = []
    for path, canonical_key, python_key in (
        ((), "baseRevision", "base_revision"),
        (("transition",), "baseScene", "base_scene"),
        (("transition", "resultSemanticScene"), "certificateHeadSha256", "certificate_head_sha256"),
        (
            ("transition", "checkpoint", "certificate", "body"),
            "baseLowLevelRevision",
            "base_low_level_revision",
        ),
    ):
        payload = deepcopy(canonical)
        target: object = payload
        for segment in path:
            target = target[segment]  # type: ignore[index]
        assert isinstance(target, dict)
        target[python_key] = target.pop(canonical_key)
        payloads.append(payload)

    for payload in payloads:
        with pytest.raises(ValidationError):
            SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)

    with pytest.raises(ValueError, match="canonical aliases"):
        SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            canonical,
            by_alias=False,
        )

    assert SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_json(
        json.dumps(canonical)
    ) == _checkpoint_event(_model_transition())
    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_json(json.dumps(payloads[1]))


def test_direct_model_construction_remains_ergonomic_with_python_field_names() -> None:
    event = SemanticStoryboardSceneStreamStartedEventV1.model_validate(
        {"generation": 2, "attempt": 1, "base_revision": 0}
    )
    assert event.base_revision == 0


@pytest.mark.parametrize(
    ("reason", "base", "final", "count", "cause"),
    [
        (SemanticStoryboardCompletionReason.ANCHOR, 0, 1, 1, None),
        (SemanticStoryboardCompletionReason.MODEL_STOP, 1, 3, 2, None),
        (
            SemanticStoryboardCompletionReason.ACCEPTED_PREFIX,
            4,
            6,
            2,
            SemanticStoryboardAcceptedPrefixCause.INVALID_MODEL_STREAM,
        ),
    ],
)
def test_each_completed_terminal_has_an_exact_reason_and_frontier_delta(
    reason: SemanticStoryboardCompletionReason,
    base: int,
    final: int,
    count: int,
    cause: SemanticStoryboardAcceptedPrefixCause | None,
) -> None:
    event = SemanticStoryboardSceneStreamCompletedEventV1(
        generation=9,
        attempt=1,
        base_revision=base,
        final_revision=final,
        checkpoint_count=count,
        first_checkpoint_ms=80.0,
        total_ms=120.0,
        reason_code=reason,
        accepted_prefix_cause=cause,
    )
    payload = dump_semantic_storyboard_scene_stream_event(event)

    assert payload["reasonCode"] == reason.value
    assert payload["acceptedPrefixCause"] == (None if cause is None else cause.value)
    assert payload["finalRevision"] == payload["baseRevision"] + payload["checkpointCount"]


@pytest.mark.parametrize("cause", list(SemanticStoryboardAcceptedPrefixCause))
def test_every_accepted_prefix_cause_is_closed_and_sanitized(
    cause: SemanticStoryboardAcceptedPrefixCause,
) -> None:
    event = SemanticStoryboardSceneStreamCompletedEventV1(
        generation=9,
        attempt=1,
        base_revision=1,
        final_revision=2,
        checkpoint_count=1,
        first_checkpoint_ms=80.0,
        total_ms=120.0,
        reason_code=SemanticStoryboardCompletionReason.ACCEPTED_PREFIX,
        accepted_prefix_cause=cause,
    )
    assert dump_semantic_storyboard_scene_stream_event(event)["acceptedPrefixCause"] == cause.value


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"finalRevision": 3}, "baseRevision plus checkpointCount"),
        ({"totalMs": 79.0}, "totalMs"),
        ({"reasonCode": "anchor", "baseRevision": 1, "finalRevision": 2}, "anchor completion"),
        ({"reasonCode": "model_stop", "acceptedPrefixCause": "provider_timeout"}, "forbids"),
        ({"reasonCode": "accepted_prefix", "acceptedPrefixCause": None}, "requires"),
        (
            {"reasonCode": "model_stop", "baseRevision": 0, "finalRevision": 1},
            "accepted anchor",
        ),
        ({"checkpointCount": MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS + 1}, "less than"),
    ],
)
def test_completed_terminal_rejects_false_success_claims(
    updates: dict[str, object],
    message: str,
) -> None:
    payload = SemanticStoryboardSceneStreamCompletedEventV1(
        generation=9,
        attempt=1,
        base_revision=1,
        final_revision=2,
        checkpoint_count=1,
        first_checkpoint_ms=80.0,
        total_ms=120.0,
        reason_code=SemanticStoryboardCompletionReason.MODEL_STOP,
    ).model_dump(mode="json", by_alias=True)
    payload.update(updates)
    with pytest.raises(ValidationError, match=message):
        SemanticStoryboardSceneStreamCompletedEventV1.model_validate(payload)


@pytest.mark.parametrize("reason", list(StoryboardAbstainReasonCode))
def test_sole_abstention_decline_is_closed_and_mutation_free(
    reason: StoryboardAbstainReasonCode,
) -> None:
    event = SemanticStoryboardSceneStreamDeclinedEventV1(
        generation=3,
        attempt=1,
        base_revision=4,
        final_revision=4,
        reason_code=reason,
        message="The accepted storyboard is unchanged.",
    )
    payload = dump_semantic_storyboard_scene_stream_event(event)
    assert payload["type"] == "semantic_storyboard_scene_stream_declined"
    assert payload["reasonCode"] == reason.value
    assert payload["baseRevision"] == payload["finalRevision"]


def test_decline_rejects_mutation_zero_revision_unknown_reason_and_extra_data() -> None:
    canonical = {
        "type": "semantic_storyboard_scene_stream_declined",
        "generation": 3,
        "attempt": 1,
        "baseRevision": 4,
        "finalRevision": 4,
        "reasonCode": "unsupported_intent",
        "message": "The accepted storyboard is unchanged.",
    }
    for updates in (
        {"finalRevision": 5},
        {"baseRevision": 0, "finalRevision": 0},
        {"reasonCode": "private_reason"},
        {"providerText": "secret"},
    ):
        payload = {**canonical, **updates}
        with pytest.raises(ValidationError):
            SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("code", list(SemanticStoryboardFailureCode))
def test_failure_codes_have_closed_retry_consistent_semantics(
    code: SemanticStoryboardFailureCode,
) -> None:
    retryable = code in {
        SemanticStoryboardFailureCode.INVALID_MODEL_STREAM,
        SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED,
        SemanticStoryboardFailureCode.PROVIDER_TIMEOUT,
        SemanticStoryboardFailureCode.PROVIDER_ERROR,
    }
    event = SemanticStoryboardSceneStreamFailedEventV1(
        generation=8,
        attempt=1,
        base_revision=1,
        code=code,
        message="The storyboard request could not be completed safely.",
        last_accepted_revision=1,
        retryable=retryable,
    )
    payload = dump_semantic_storyboard_scene_stream_event(event)
    assert payload["code"] == code.value
    assert payload["retryable"] is retryable

    payload["retryable"] = not retryable
    with pytest.raises(ValidationError, match="retryable must be"):
        SemanticStoryboardSceneStreamFailedEventV1.model_validate(payload)


def test_failure_cannot_follow_a_checkpoint_or_expose_open_codes() -> None:
    canonical = {
        "type": "semantic_storyboard_scene_stream_failed",
        "generation": 8,
        "attempt": 1,
        "baseRevision": 1,
        "code": "provider_timeout",
        "message": "The storyboard director timed out.",
        "lastAcceptedRevision": 1,
        "retryable": True,
    }
    with pytest.raises(ValidationError, match="cannot follow"):
        SemanticStoryboardSceneStreamFailedEventV1.model_validate(
            {**canonical, "lastAcceptedRevision": 2}
        )
    with pytest.raises(ValidationError):
        SemanticStoryboardSceneStreamFailedEventV1.model_validate(
            {**canonical, "code": "raw_vendor_failure", "retryable": False}
        )


@pytest.mark.parametrize(
    "event",
    [
        SemanticStoryboardSceneStreamStartedEventV1(generation=1, attempt=1, base_revision=0),
        SemanticStoryboardSceneStreamCompletedEventV1(
            generation=1,
            attempt=1,
            base_revision=0,
            final_revision=1,
            checkpoint_count=1,
            first_checkpoint_ms=10.0,
            total_ms=20.0,
            reason_code=SemanticStoryboardCompletionReason.ANCHOR,
        ),
    ],
)
def test_every_lifecycle_event_rejects_unknown_fields(event: object) -> None:
    payload = event.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    payload["providerTrace"] = "private"
    with pytest.raises(ValidationError, match="Extra inputs"):
        SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)

from __future__ import annotations

import math
from itertools import combinations

import pytest
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
    PROJECTILE_MOTION_GRAVITY_MPS2,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    PROJECTILE_MOTION_ROUTE_V1_ADAPTER,
    PROJECTILE_MOTION_STAGE_PREFIXES,
    ROUTED_PROJECTILE_MOTION_BEAT_V1_ADAPTER,
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStage,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    next_projectile_motion_main_checkpoint,
    projectile_motion_checkpoint_prefix,
    projectile_motion_checkpoints_through,
    projectile_motion_introduction_stage_for,
    projectile_motion_problem_sha256,
    routed_projectile_motion_beat_sha256,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState, semantic_scene_sha256
from pydantic import ValidationError

SUPPORTED_PROBLEMS = tuple(
    (speed, angle)
    for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
    for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
)


def _problem_payload(speed: int = 20, angle: int = 45) -> dict[str, int]:
    return {"v": 1, "speedMps": speed, "angleDeg": angle}


def _problem(speed: int = 20, angle: int = 45) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1.model_validate(_problem_payload(speed, angle))


def _state_payload(
    *,
    problem: ProjectileMotionProblemSpecV1 | None = None,
    last_checkpoint: ProjectileMotionMainCheckpoint | None = None,
    clarified_topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active_clarification: ProjectileMotionClarificationTopic | None = None,
) -> dict[str, object]:
    selected_problem = _problem() if problem is None else problem
    return {
        "kind": "projectile_motion",
        "id": "projectile-lesson",
        "problemSpec": selected_problem.model_dump(mode="json", by_alias=True),
        "lastMainCheckpoint": last_checkpoint,
        "clarifiedTopics": [topic.value for topic in clarified_topics],
        "activeClarification": (
            None if active_clarification is None else active_clarification.value
        ),
    }


def _advance_route(stage: ProjectileMotionStage = ProjectileMotionStage.SOLVE) -> dict[str, str]:
    return {"intent": "advance", "targetStage": stage.value}


def _beat_payload(
    *,
    base_problem: ProjectileMotionProblemSpecV1 | None,
    result_problem: ProjectileMotionProblemSpecV1 | None = None,
    route: dict[str, object] | None = None,
) -> dict[str, object]:
    selected_result = _problem() if result_problem is None else result_problem
    return {
        "v": 1,
        "beatId": "projectile-beat-1",
        "componentKind": "projectile_motion",
        "componentId": "projectile-lesson",
        "baseProblemSpec": (
            None if base_problem is None else base_problem.model_dump(mode="json", by_alias=True)
        ),
        "resultProblemSpec": selected_result.model_dump(mode="json", by_alias=True),
        "route": _advance_route() if route is None else route,
    }


@pytest.mark.parametrize(("speed", "angle"), SUPPORTED_PROBLEMS)
def test_all_nine_problem_specs_have_exact_wire_and_physics(
    speed: int,
    angle: int,
) -> None:
    payload = _problem_payload(speed, angle)
    problem = ProjectileMotionProblemSpecV1.model_validate(payload)
    radians = math.radians(angle)
    expected_vx = speed * math.cos(radians)
    expected_vy = speed * math.sin(radians)
    expected_ascent_time = expected_vy / PROJECTILE_MOTION_GRAVITY_MPS2
    expected_flight_time = 2 * expected_ascent_time
    expected_height = expected_vy**2 / (2 * PROJECTILE_MOTION_GRAVITY_MPS2)
    expected_range = expected_vx * expected_flight_time

    assert problem.model_dump(mode="json", by_alias=True) == payload
    assert problem.initial_horizontal_velocity_mps == pytest.approx(expected_vx)
    assert problem.initial_vertical_velocity_mps == pytest.approx(expected_vy)
    assert problem.ascent_time_seconds == pytest.approx(expected_ascent_time)
    assert problem.flight_time_seconds == pytest.approx(expected_flight_time)
    assert problem.maximum_height_m == pytest.approx(expected_height)
    assert problem.range_m == pytest.approx(expected_range)
    assert set(ProjectileMotionProblemSpecV1.model_fields) == {
        "v",
        "speed_mps",
        "angle_deg",
    }


def test_problem_hash_is_canonical_problem_bound_and_domain_separated() -> None:
    problem = _problem(20, 45)
    reordered = ProjectileMotionProblemSpecV1.model_validate(
        {"angleDeg": 45, "speedMps": 20, "v": 1}
    )

    assert projectile_motion_problem_sha256(problem) == projectile_motion_problem_sha256(reordered)
    assert projectile_motion_problem_sha256(problem) == (
        "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa"
    )
    assert projectile_motion_problem_sha256(problem) != projectile_motion_problem_sha256(
        _problem(20, 60)
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("v", True, "strict integer"),
        ("v", "1", "strict integer"),
        ("speedMps", True, "strict integer"),
        ("speedMps", "20", "strict integer"),
        ("speedMps", 20.0, "strict integer"),
        ("angleDeg", False, "strict integer"),
        ("angleDeg", "45", "strict integer"),
        ("angleDeg", 45.0, "strict integer"),
        ("speedMps", 19, "speedMps must be one of"),
        ("speedMps", 21, "speedMps must be one of"),
        ("speedMps", 31, "speedMps must be one of"),
        ("angleDeg", 0, "angleDeg must be one of"),
        ("angleDeg", 29, "angleDeg must be one of"),
        ("angleDeg", 90, "angleDeg must be one of"),
    ],
)
def test_problem_rejects_coercions_and_unsupported_neighbors(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _problem_payload()
    payload[field] = value  # type: ignore[assignment]

    with pytest.raises(ValidationError, match=message):
        ProjectileMotionProblemSpecV1.model_validate(payload)


@pytest.mark.parametrize("field", ["gravity", "launchHeight", "wind", "drag"])
def test_problem_rejects_client_supplied_physics(field: str) -> None:
    payload: dict[str, object] = _problem_payload()
    payload[field] = 0

    with pytest.raises(ValidationError, match="Extra inputs"):
        ProjectileMotionProblemSpecV1.model_validate(payload)


def test_problem_requires_both_inputs_and_is_immutable() -> None:
    for field in ("speedMps", "angleDeg"):
        payload = _problem_payload()
        del payload[field]
        with pytest.raises(ValidationError, match="Field required"):
            ProjectileMotionProblemSpecV1.model_validate(payload)

    problem = _problem()
    with pytest.raises(ValidationError, match="frozen"):
        problem.speed_mps = 25


def test_checkpoint_stage_and_sidecar_vocabularies_are_closed_and_exact() -> None:
    assert tuple(checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER) == (
        "setup",
        "decompose_velocity",
        "trace_ascent",
        "apex_state",
        "trace_descent",
        "summary",
    )
    assert tuple(checkpoint.value for checkpoint in ProjectileMotionMainCheckpoint) == tuple(
        checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    )
    assert tuple(checkpoint.value for checkpoint in ProjectileMotionCheckpointId) == (
        "setup",
        "decompose_velocity",
        "trace_ascent",
        "apex_state",
        "trace_descent",
        "summary",
        "horizontal_velocity_detail",
        "apex_acceleration_detail",
        "flight_symmetry_detail",
        "parameters_retargeted",
    )
    assert tuple(topic.value for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER) == (
        "horizontal_velocity",
        "apex_acceleration",
        "flight_symmetry",
    )
    assert tuple(ProjectileMotionStage) == (
        ProjectileMotionStage.SETUP,
        ProjectileMotionStage.LAUNCH,
        ProjectileMotionStage.FLIGHT,
        ProjectileMotionStage.SOLVE,
    )


@pytest.mark.parametrize(
    ("stage", "prefix_length"),
    [
        (ProjectileMotionStage.SETUP, 1),
        (ProjectileMotionStage.LAUNCH, 2),
        (ProjectileMotionStage.FLIGHT, 5),
        (ProjectileMotionStage.SOLVE, 6),
    ],
)
def test_each_stage_maps_to_its_exact_authoritative_prefix(
    stage: ProjectileMotionStage,
    prefix_length: int,
) -> None:
    expected = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[:prefix_length]

    assert projectile_motion_checkpoints_through(stage) == expected
    assert PROJECTILE_MOTION_STAGE_PREFIXES[stage] == expected


def test_stage_prefixes_and_clarification_maps_are_immutable() -> None:
    with pytest.raises(TypeError):
        PROJECTILE_MOTION_STAGE_PREFIXES[ProjectileMotionStage.SETUP] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[
            ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
        ] = ProjectileMotionMainCheckpoint.SETUP  # type: ignore[index]
    with pytest.raises(TypeError):
        PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[
            ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
        ] = ProjectileMotionCheckpointId.SUMMARY  # type: ignore[index]


def test_nullable_frontier_helpers_cover_every_prefix_and_next_checkpoint() -> None:
    assert projectile_motion_checkpoint_prefix(None) == ()
    assert next_projectile_motion_main_checkpoint(None) is ProjectileMotionMainCheckpoint.SETUP

    expected_stages = (
        ProjectileMotionStage.SETUP,
        ProjectileMotionStage.LAUNCH,
        ProjectileMotionStage.FLIGHT,
        ProjectileMotionStage.FLIGHT,
        ProjectileMotionStage.FLIGHT,
        ProjectileMotionStage.SOLVE,
    )
    for index, checkpoint in enumerate(PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER):
        assert (
            projectile_motion_checkpoint_prefix(checkpoint)
            == (PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[: index + 1])
        )
        expected_next = (
            None
            if index + 1 == len(PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER)
            else PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[index + 1]
        )
        assert next_projectile_motion_main_checkpoint(checkpoint) is expected_next
        assert projectile_motion_introduction_stage_for(checkpoint) is expected_stages[index]


@pytest.mark.parametrize(("speed", "angle"), SUPPORTED_PROBLEMS)
@pytest.mark.parametrize(
    "last_checkpoint",
    [None, *PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER],
)
def test_state_accepts_every_problem_frontier_and_legal_topic_subset(
    speed: int,
    angle: int,
    last_checkpoint: ProjectileMotionMainCheckpoint | None,
) -> None:
    problem = _problem(speed, angle)
    prefix = projectile_motion_checkpoint_prefix(last_checkpoint)
    available_topics = tuple(
        topic
        for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER
        if PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic] in prefix
    )

    for topic_count in range(len(available_topics) + 1):
        for topics in combinations(available_topics, topic_count):
            payload = _state_payload(
                problem=problem,
                last_checkpoint=last_checkpoint,
                clarified_topics=topics,
            )
            state = ProjectileMotionStateV1.model_validate(payload)

            assert state.model_dump(mode="json", by_alias=True) == payload
            assert state.problem_spec == problem
            assert state.last_main_checkpoint is last_checkpoint
            assert state.clarified_topics == topics
            assert state.active_clarification is None


@pytest.mark.parametrize(
    ("last_checkpoint", "topic", "required_checkpoint"),
    [
        (
            ProjectileMotionMainCheckpoint.SETUP,
            ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
            ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY,
        ),
        (
            ProjectileMotionMainCheckpoint.TRACE_ASCENT,
            ProjectileMotionClarificationTopic.APEX_ACCELERATION,
            ProjectileMotionMainCheckpoint.APEX_STATE,
        ),
        (
            ProjectileMotionMainCheckpoint.APEX_STATE,
            ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
            ProjectileMotionMainCheckpoint.TRACE_DESCENT,
        ),
    ],
)
def test_state_rejects_clarifications_before_their_prerequisite(
    last_checkpoint: ProjectileMotionMainCheckpoint,
    topic: ProjectileMotionClarificationTopic,
    required_checkpoint: ProjectileMotionMainCheckpoint,
) -> None:
    with pytest.raises(ValidationError, match=f"at or after {required_checkpoint.value}"):
        ProjectileMotionStateV1.model_validate(
            _state_payload(last_checkpoint=last_checkpoint, clarified_topics=(topic,))
        )


def test_state_rejects_duplicate_reordered_unknown_and_extra_topics() -> None:
    valid = _state_payload(
        last_checkpoint=ProjectileMotionMainCheckpoint.SUMMARY,
        clarified_topics=PROJECTILE_MOTION_CLARIFICATION_ORDER,
    )

    duplicate = {**valid, "clarifiedTopics": ["horizontal_velocity", "horizontal_velocity"]}
    with pytest.raises(ValidationError, match="unique"):
        ProjectileMotionStateV1.model_validate(duplicate)

    reordered = {
        **valid,
        "clarifiedTopics": ["flight_symmetry", "horizontal_velocity"],
    }
    with pytest.raises(ValidationError, match="canonical pedagogical order"):
        ProjectileMotionStateV1.model_validate(reordered)

    unknown = {**valid, "clarifiedTopics": ["air_resistance"]}
    with pytest.raises(ValidationError):
        ProjectileMotionStateV1.model_validate(unknown)

    extra = {**valid, "gravity": 10}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProjectileMotionStateV1.model_validate(extra)


def test_active_clarification_must_belong_to_the_legal_one_shot_ledger() -> None:
    active = ProjectileMotionClarificationTopic.APEX_ACCELERATION
    payload = _state_payload(
        last_checkpoint=ProjectileMotionMainCheckpoint.APEX_STATE,
        clarified_topics=(active,),
        active_clarification=active,
    )

    state = ProjectileMotionStateV1.model_validate(payload)

    assert state.active_clarification is active
    assert state.model_dump(mode="json", by_alias=True) == payload

    absent = {**payload, "clarifiedTopics": []}
    with pytest.raises(ValidationError, match="must be present in clarifiedTopics"):
        ProjectileMotionStateV1.model_validate(absent)

    too_early = {
        **payload,
        "lastMainCheckpoint": ProjectileMotionMainCheckpoint.TRACE_ASCENT.value,
    }
    with pytest.raises(ValidationError, match="at or after apex_state"):
        ProjectileMotionStateV1.model_validate(too_early)


def test_state_is_exact_immutable_and_semantic_union_bound() -> None:
    payload = _state_payload(
        last_checkpoint=ProjectileMotionMainCheckpoint.APEX_STATE,
        clarified_topics=(ProjectileMotionClarificationTopic.APEX_ACCELERATION,),
    )
    state = ProjectileMotionStateV1.model_validate(payload)
    scene_payload = {"revision": 4, "components": [payload]}
    scene = SemanticSceneState.model_validate(scene_payload)

    assert set(ProjectileMotionStateV1.model_fields) == {
        "kind",
        "id",
        "problem_spec",
        "last_main_checkpoint",
        "clarified_topics",
        "active_clarification",
    }
    assert scene.model_dump(mode="json", by_alias=True) == scene_payload
    assert isinstance(scene.components[0], ProjectileMotionStateV1)
    assert semantic_scene_sha256(scene) == semantic_scene_sha256(
        SemanticSceneState.model_validate(scene_payload)
    )
    with pytest.raises(ValidationError, match="frozen"):
        state.last_main_checkpoint = ProjectileMotionMainCheckpoint.SUMMARY


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (_advance_route(ProjectileMotionStage.FLIGHT), AdvanceProjectileMotionRouteV1),
        (
            {"intent": "clarify", "topic": "apex_acceleration"},
            ClarifyProjectileMotionRouteV1,
        ),
        (
            {
                "intent": "retarget",
                "targetProblemSpec": _problem_payload(30, 60),
            },
            RetargetProjectileMotionRouteV1,
        ),
    ],
)
def test_route_union_has_three_exact_server_recognized_variants(
    payload: dict[str, object],
    expected_type: type,
) -> None:
    route = PROJECTILE_MOTION_ROUTE_V1_ADAPTER.validate_python(payload)

    assert isinstance(route, expected_type)
    assert route.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "jump", "targetStage": "solve"},
        {"intent": "advance", "targetStage": "impact"},
        {"intent": "advance", "targetStage": "solve", "topic": "flight_symmetry"},
        {"intent": "clarify"},
        {"intent": "clarify", "topic": "drag"},
        {"intent": "retarget"},
        {
            "intent": "retarget",
            "targetProblemSpec": {**_problem_payload(), "gravity": 9.81},
        },
    ],
)
def test_route_union_rejects_unknown_missing_and_cross_variant_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PROJECTILE_MOTION_ROUTE_V1_ADAPTER.validate_python(payload)


def test_fresh_advance_beat_has_nullable_base_and_exact_wire() -> None:
    payload = _beat_payload(base_problem=None)
    beat = ROUTED_PROJECTILE_MOTION_BEAT_V1_ADAPTER.validate_python(payload)

    assert isinstance(beat, RoutedProjectileMotionBeatV1)
    assert beat.base_problem_spec is None
    assert beat.result_problem_spec == _problem()
    assert beat.model_dump(mode="json", by_alias=True) == payload


def test_continuation_clarification_and_retarget_beats_bind_problem_transition() -> None:
    current = _problem(20, 45)

    continuation_payload = _beat_payload(base_problem=current, result_problem=current)
    continuation = RoutedProjectileMotionBeatV1.model_validate(continuation_payload)
    assert isinstance(continuation.route, AdvanceProjectileMotionRouteV1)

    clarification_payload = _beat_payload(
        base_problem=current,
        result_problem=current,
        route={"intent": "clarify", "topic": "apex_acceleration"},
    )
    clarification = RoutedProjectileMotionBeatV1.model_validate(clarification_payload)
    assert isinstance(clarification.route, ClarifyProjectileMotionRouteV1)

    target = _problem(20, 60)
    retarget_payload = _beat_payload(
        base_problem=current,
        result_problem=target,
        route={
            "intent": "retarget",
            "targetProblemSpec": target.model_dump(mode="json", by_alias=True),
        },
    )
    retarget = RoutedProjectileMotionBeatV1.model_validate(retarget_payload)
    assert isinstance(retarget.route, RetargetProjectileMotionRouteV1)
    assert retarget.base_problem_spec == current
    assert retarget.result_problem_spec == target
    assert retarget.route.target_problem_spec == target


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _beat_payload(
                base_problem=None,
                route={"intent": "clarify", "topic": "horizontal_velocity"},
            ),
            "only a fresh advance",
        ),
        (
            _beat_payload(
                base_problem=None,
                route={
                    "intent": "retarget",
                    "targetProblemSpec": _problem_payload(20, 60),
                },
            ),
            "only a fresh advance",
        ),
        (
            _beat_payload(base_problem=_problem(20, 45), result_problem=_problem(20, 60)),
            "must preserve",
        ),
        (
            _beat_payload(
                base_problem=_problem(20, 45),
                result_problem=_problem(30, 60),
                route={
                    "intent": "retarget",
                    "targetProblemSpec": _problem_payload(25, 60),
                },
            ),
            "must match resultProblemSpec",
        ),
        (
            _beat_payload(
                base_problem=_problem(20, 45),
                result_problem=_problem(20, 45),
                route={
                    "intent": "retarget",
                    "targetProblemSpec": _problem_payload(20, 45),
                },
            ),
            "must change",
        ),
    ],
)
def test_routed_beat_rejects_illegal_problem_transitions(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RoutedProjectileMotionBeatV1.model_validate(payload)


def test_routed_beat_hash_is_canonical_and_binds_base_result_and_route() -> None:
    current = _problem(20, 45)
    payload = _beat_payload(base_problem=current, result_problem=current)
    beat = RoutedProjectileMotionBeatV1.model_validate(payload)
    reordered = RoutedProjectileMotionBeatV1.model_validate(
        {
            "route": payload["route"],
            "resultProblemSpec": payload["resultProblemSpec"],
            "baseProblemSpec": payload["baseProblemSpec"],
            "componentId": payload["componentId"],
            "componentKind": payload["componentKind"],
            "beatId": payload["beatId"],
            "v": payload["v"],
        }
    )

    assert routed_projectile_motion_beat_sha256(beat) == (
        routed_projectile_motion_beat_sha256(reordered)
    )
    assert routed_projectile_motion_beat_sha256(beat) == (
        "4fcdf4bf0b0a2b9121a5e7a06d03705e68124a67f03a108359ee6c335489a15f"
    )
    changed = RoutedProjectileMotionBeatV1.model_validate(
        {
            **payload,
            "route": _advance_route(ProjectileMotionStage.FLIGHT),
        }
    )
    assert routed_projectile_motion_beat_sha256(beat) != (
        routed_projectile_motion_beat_sha256(changed)
    )


def test_routed_beat_rejects_missing_extra_and_coerced_identity_fields() -> None:
    base = _beat_payload(base_problem=None)

    for field in ("beatId", "componentId", "baseProblemSpec", "resultProblemSpec", "route"):
        payload = dict(base)
        del payload[field]
        with pytest.raises(ValidationError, match="Field required"):
            RoutedProjectileMotionBeatV1.model_validate(payload)

    extra = {**base, "narration": "Trust me."}
    with pytest.raises(ValidationError, match="Extra inputs"):
        RoutedProjectileMotionBeatV1.model_validate(extra)

    coerced = {**base, "v": "1"}
    with pytest.raises(ValidationError, match="strict integer"):
        RoutedProjectileMotionBeatV1.model_validate(coerced)

    wrong_kind = {**base, "componentKind": "completing_square_parametric"}
    with pytest.raises(ValidationError):
        RoutedProjectileMotionBeatV1.model_validate(wrong_kind)

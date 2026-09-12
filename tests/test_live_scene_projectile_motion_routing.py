"""Provider-free qualification for the projectile routing boundary."""

from __future__ import annotations

from itertools import permutations

import pytest
from murmur.live_scene.contracts import MAX_SAFE_SEQUENCE
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
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
    projectile_motion_checkpoint_prefix,
    projectile_motion_checkpoints_through,
)
from murmur.live_scene.projectile_motion_routing import (
    PROJECTILE_MOTION_COMPONENT_ID,
    PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER,
    AbstainProjectileMotionDecisionV1,
    ClarifyProjectileMotionDecisionV1,
    ContinueProjectileMotionDecisionV1,
    ProjectileMotionRoutingError,
    ProjectileMotionRoutingErrorCode,
    ResolvedProjectileMotionAct,
    StartProjectileMotionDecisionV1,
    lower_resolved_projectile_motion_act,
    resolve_projectile_motion_director_decision,
    resolve_projectile_motion_reflex_route,
    validate_projectile_motion_frontier,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState
from pydantic import ValidationError


def _problem(speed: int = 20, angle: int = 45) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)


SUPPORTED_PROBLEMS = tuple(
    _problem(speed, angle)
    for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
    for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
)


def _scene(
    checkpoint: ProjectileMotionMainCheckpoint | None,
    *,
    problem: ProjectileMotionProblemSpecV1 | None = None,
    topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active: ProjectileMotionClarificationTopic | None = None,
    component_id: str = "projectile-custom",
) -> SemanticSceneState:
    return SemanticSceneState(
        revision=len(projectile_motion_checkpoint_prefix(checkpoint)) + len(topics),
        components=(
            ProjectileMotionStateV1(
                id=component_id,
                problemSpec=problem or _problem(),
                lastMainCheckpoint=checkpoint,
                clarifiedTopics=topics,
                activeClarification=active,
            ),
        ),
    )


def _decode(action: str, **fields: object) -> object:
    return PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER.validate_python(
        {"v": 1, "action": action, **fields}
    )


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_fields"),
    [
        (
            {"v": 1, "action": "start", "stage": "flight"},
            StartProjectileMotionDecisionV1,
            {"v", "action", "stage"},
        ),
        (
            {"v": 1, "action": "continue", "stage": "solve"},
            ContinueProjectileMotionDecisionV1,
            {"v", "action", "stage"},
        ),
        (
            {"v": 1, "action": "clarify", "topic": "apex_acceleration"},
            ClarifyProjectileMotionDecisionV1,
            {"v", "action", "topic"},
        ),
        (
            {"v": 1, "action": "abstain", "reasonCode": "unsupported_intent"},
            AbstainProjectileMotionDecisionV1,
            {"v", "action", "reason_code"},
        ),
    ],
)
def test_director_decisions_have_exact_minimal_wire_shapes(
    payload: dict[str, object],
    expected_type: type[object],
    expected_fields: set[str],
) -> None:
    decision = PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER.validate_python(payload)

    assert isinstance(decision, expected_type)
    assert set(type(decision).model_fields) == expected_fields
    assert decision.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "start", "stage": "setup"},
        {"v": True, "action": "start", "stage": "setup"},
        {"v": 2, "action": "start", "stage": "setup"},
        {"v": 1, "action": "retarget", "speedMps": 30},
        {"v": 1, "action": "start"},
        {"v": 1, "action": "continue", "stage": "apex"},
        {"v": 1, "action": "clarify"},
        {"v": 1, "action": "abstain"},
        {"v": 1, "action": "abstain", "reasonCode": "later"},
        {"v": 1, "action": "clarify", "topic": "apex_acceleration", "x": 10},
    ],
)
def test_director_decisions_reject_missing_open_cross_variant_and_numeric_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "problemSpec",
        "speedMps",
        "angleDeg",
        "gravity",
        "equation",
        "narration",
        "componentId",
        "beatId",
        "nodeId",
        "points",
        "style",
        "durationMs",
        "viewport",
        "patch",
        "receipt",
        "certificate",
        "generation",
        "revision",
    ],
)
def test_director_decision_cannot_own_physics_or_presentation(forbidden: str) -> None:
    payload: dict[str, object] = {"v": 1, "action": "start", "stage": "solve"}
    payload[forbidden] = "model-owned"

    with pytest.raises(ValidationError, match="Extra inputs"):
        PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("stage", tuple(ProjectileMotionStage))
def test_fresh_reflex_advance_owns_the_exact_missing_prefix(
    stage: ProjectileMotionStage,
) -> None:
    problem = _problem()
    route = AdvanceProjectileMotionRouteV1(targetStage=stage)

    resolved = resolve_projectile_motion_reflex_route(
        route,
        problem_spec=problem,
        semantic_scene=SemanticSceneState(revision=0),
    )

    expected = tuple(
        ProjectileMotionCheckpointId(checkpoint.value)
        for checkpoint in projectile_motion_checkpoints_through(stage)
    )
    assert resolved == ResolvedProjectileMotionAct(
        component_kind="projectile_motion",
        component_id=PROJECTILE_MOTION_COMPONENT_ID,
        base_problem_spec=None,
        result_problem_spec=problem,
        route=route,
        checkpoint_ids=expected,
    )


@pytest.mark.parametrize("stage", tuple(ProjectileMotionStage))
@pytest.mark.parametrize("frontier", (None, *PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER))
def test_advance_accepts_only_a_strict_later_stage_prefix(
    stage: ProjectileMotionStage,
    frontier: ProjectileMotionMainCheckpoint | None,
) -> None:
    problem = _problem()
    target = projectile_motion_checkpoints_through(stage)
    current = projectile_motion_checkpoint_prefix(frontier)
    should_advance = current == target[: len(current)] and len(current) < len(target)

    if should_advance:
        resolved = resolve_projectile_motion_reflex_route(
            AdvanceProjectileMotionRouteV1(targetStage=stage),
            problem_spec=problem,
            semantic_scene=(
                SemanticSceneState(revision=0)
                if frontier is None
                else _scene(frontier, problem=problem)
            ),
        )
        assert resolved.checkpoint_ids == tuple(
            ProjectileMotionCheckpointId(checkpoint.value) for checkpoint in target[len(current) :]
        )
    else:
        with pytest.raises(ProjectileMotionRoutingError) as captured:
            resolve_projectile_motion_reflex_route(
                AdvanceProjectileMotionRouteV1(targetStage=stage),
                problem_spec=problem,
                semantic_scene=(
                    SemanticSceneState(revision=0)
                    if frontier is None
                    else _scene(frontier, problem=problem)
                ),
            )
        assert captured.value.code is ProjectileMotionRoutingErrorCode.NON_FORWARD_TARGET


@pytest.mark.parametrize("topic", PROJECTILE_MOTION_CLARIFICATION_ORDER)
def test_each_clarification_is_available_once_after_its_prerequisite(
    topic: ProjectileMotionClarificationTopic,
) -> None:
    problem = _problem()
    route = ClarifyProjectileMotionRouteV1(topic=topic)
    prerequisite = {
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY: (
            ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY
        ),
        ProjectileMotionClarificationTopic.APEX_ACCELERATION: (
            ProjectileMotionMainCheckpoint.APEX_STATE
        ),
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY: (
            ProjectileMotionMainCheckpoint.TRACE_DESCENT
        ),
    }[topic]

    resolved = resolve_projectile_motion_reflex_route(
        route,
        problem_spec=problem,
        semantic_scene=_scene(prerequisite, problem=problem),
    )
    assert resolved.checkpoint_ids == (
        {
            ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY: (
                ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL
            ),
            ProjectileMotionClarificationTopic.APEX_ACCELERATION: (
                ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL
            ),
            ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY: (
                ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL
            ),
        }[topic],
    )

    with pytest.raises(ProjectileMotionRoutingError) as captured:
        resolve_projectile_motion_reflex_route(
            route,
            problem_spec=problem,
            semantic_scene=_scene(prerequisite, problem=problem, topics=(topic,)),
        )
    assert captured.value.code is ProjectileMotionRoutingErrorCode.CLARIFICATION_UNAVAILABLE


def test_clarification_before_its_prerequisite_and_on_empty_scene_declines_locally() -> None:
    problem = _problem()
    route = ClarifyProjectileMotionRouteV1(
        topic=ProjectileMotionClarificationTopic.APEX_ACCELERATION
    )
    with pytest.raises(ProjectileMotionRoutingError) as early:
        resolve_projectile_motion_reflex_route(
            route,
            problem_spec=problem,
            semantic_scene=_scene(
                ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY,
                problem=problem,
            ),
        )
    assert early.value.code is ProjectileMotionRoutingErrorCode.CLARIFICATION_UNAVAILABLE

    with pytest.raises(ProjectileMotionRoutingError) as empty:
        resolve_projectile_motion_reflex_route(
            route,
            problem_spec=problem,
            semantic_scene=SemanticSceneState(revision=0),
        )
    assert empty.value.code is ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND


def test_all_72_retargets_at_all_six_frontiers_preserve_identity_and_change_problem() -> None:
    for base_problem, target_problem in permutations(SUPPORTED_PROBLEMS, 2):
        for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER:
            route = RetargetProjectileMotionRouteV1(targetProblemSpec=target_problem)
            resolved = resolve_projectile_motion_reflex_route(
                route,
                problem_spec=base_problem,
                semantic_scene=_scene(frontier, problem=base_problem),
            )

            assert resolved.component_id == "projectile-custom"
            assert resolved.base_problem_spec == base_problem
            assert resolved.result_problem_spec == target_problem
            assert resolved.route is route
            assert resolved.checkpoint_ids == (ProjectileMotionCheckpointId.PARAMETERS_RETARGETED,)


def test_retarget_requires_a_settled_frontier_and_a_different_problem() -> None:
    problem = _problem()
    target = _problem(30, 60)
    with pytest.raises(ProjectileMotionRoutingError) as unsettled:
        resolve_projectile_motion_reflex_route(
            RetargetProjectileMotionRouteV1(targetProblemSpec=target),
            problem_spec=problem,
            semantic_scene=_scene(None, problem=problem),
        )
    assert unsettled.value.code is ProjectileMotionRoutingErrorCode.RETARGET_UNAVAILABLE

    with pytest.raises(ProjectileMotionRoutingError) as same:
        resolve_projectile_motion_reflex_route(
            RetargetProjectileMotionRouteV1(targetProblemSpec=problem),
            problem_spec=problem,
            semantic_scene=_scene(ProjectileMotionMainCheckpoint.SETUP, problem=problem),
        )
    assert same.value.code is ProjectileMotionRoutingErrorCode.NON_FORWARD_TARGET


def test_frontier_rejects_problem_mismatch_foreign_kind_and_multiple_components() -> None:
    problem = _problem()
    with pytest.raises(ProjectileMotionRoutingError) as mismatch:
        validate_projectile_motion_frontier(
            _problem(30, 60),
            _scene(ProjectileMotionMainCheckpoint.SETUP, problem=problem),
        )
    assert mismatch.value.code is ProjectileMotionRoutingErrorCode.PROBLEM_MISMATCH

    foreign = SemanticSceneState.model_validate(
        {
            "revision": 1,
            "components": [
                {
                    "kind": "completing_square_parametric",
                    "id": "square-lesson",
                    "problemSpec": {
                        "v": 1,
                        "linearCoefficient": 8,
                        "rightHandSide": 20,
                    },
                    "lastMainCheckpoint": "problem",
                    "cornerClarified": False,
                }
            ],
        }
    )
    with pytest.raises(ProjectileMotionRoutingError) as kind:
        validate_projectile_motion_frontier(problem, foreign)
    assert kind.value.code is ProjectileMotionRoutingErrorCode.COMPONENT_KIND_MISMATCH

    two = SemanticSceneState(
        revision=2,
        components=(
            _scene(ProjectileMotionMainCheckpoint.SETUP).components[0],
            _scene(
                ProjectileMotionMainCheckpoint.SETUP,
                component_id="projectile-other",
            ).components[0],
        ),
    )
    with pytest.raises(ProjectileMotionRoutingError) as multiple:
        validate_projectile_motion_frontier(problem, two)
    assert multiple.value.code is ProjectileMotionRoutingErrorCode.MULTIPLE_COMPONENTS_UNSUPPORTED


def test_director_start_continue_clarify_and_abstain_use_the_same_closed_resolver() -> None:
    problem = _problem()
    empty = SemanticSceneState(revision=0)
    started = resolve_projectile_motion_director_decision(
        _decode("start", stage="launch"),
        problem_spec=problem,
        semantic_scene=empty,
    )
    assert started is not None
    assert started.checkpoint_ids[-1] is ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY

    apex = _scene(ProjectileMotionMainCheckpoint.APEX_STATE, problem=problem)
    continued = resolve_projectile_motion_director_decision(
        _decode("continue", stage="solve"),
        problem_spec=problem,
        semantic_scene=apex,
    )
    assert continued is not None
    assert continued.checkpoint_ids == (
        ProjectileMotionCheckpointId.TRACE_DESCENT,
        ProjectileMotionCheckpointId.SUMMARY,
    )

    clarified = resolve_projectile_motion_director_decision(
        _decode("clarify", topic="apex_acceleration"),
        problem_spec=problem,
        semantic_scene=apex,
    )
    assert clarified is not None
    assert clarified.checkpoint_ids == (ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL,)

    abstained = resolve_projectile_motion_director_decision(
        _decode("abstain", reasonCode="unsupported_intent"),
        problem_spec=problem,
        semantic_scene=apex,
    )
    assert abstained is None


def test_director_action_ownership_rejects_start_on_existing_and_continue_on_empty() -> None:
    problem = _problem()
    existing = _scene(ProjectileMotionMainCheckpoint.SETUP, problem=problem)
    with pytest.raises(ProjectileMotionRoutingError) as start:
        resolve_projectile_motion_director_decision(
            _decode("start", stage="launch"),
            problem_spec=problem,
            semantic_scene=existing,
        )
    assert start.value.code is ProjectileMotionRoutingErrorCode.COMPONENT_ALREADY_EXISTS

    with pytest.raises(ProjectileMotionRoutingError) as continuation:
        resolve_projectile_motion_director_decision(
            _decode("continue", stage="launch"),
            problem_spec=problem,
            semantic_scene=SemanticSceneState(revision=0),
        )
    assert continuation.value.code is ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND


def test_lowering_owns_beat_identity_and_exact_problem_transition() -> None:
    base = _problem()
    target = _problem(30, 60)
    resolved = resolve_projectile_motion_reflex_route(
        RetargetProjectileMotionRouteV1(targetProblemSpec=target),
        problem_spec=base,
        semantic_scene=_scene(ProjectileMotionMainCheckpoint.APEX_STATE, problem=base),
    )

    beat = lower_resolved_projectile_motion_act(resolved, generation=31)

    assert beat.beat_id == "projectile-route-1f"
    assert beat.component_kind == "projectile_motion"
    assert beat.component_id == resolved.component_id
    assert beat.base_problem_spec == base
    assert beat.result_problem_spec == target
    assert beat.route == resolved.route


@pytest.mark.parametrize("generation", [True, 0, -1, MAX_SAFE_SEQUENCE + 1])
def test_lowering_rejects_non_strict_or_out_of_range_generation(generation: object) -> None:
    problem = _problem()
    resolved = resolve_projectile_motion_reflex_route(
        AdvanceProjectileMotionRouteV1(targetStage=ProjectileMotionStage.SETUP),
        problem_spec=problem,
        semantic_scene=SemanticSceneState(revision=0),
    )
    with pytest.raises((TypeError, ValueError)):
        lower_resolved_projectile_motion_act(resolved, generation=generation)  # type: ignore[arg-type]

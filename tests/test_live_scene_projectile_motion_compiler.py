from __future__ import annotations

import ast
import inspect
import math
from itertools import pairwise, permutations

import pytest
from murmur.live_scene import projectile_motion_compiler
from murmur.live_scene.choreography_contracts import (
    MAX_CHOREOGRAPHY_V2_CUES,
    MAX_CHOREOGRAPHY_V2_NODE_REFERENCES,
    TracePathCueV2,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    MAX_SCENE_NODES,
    LatexTokenSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
)
from murmur.live_scene.projectile_motion_compiler import (
    ProjectileMotionCheckpointBlueprint,
    ProjectileMotionCompilationError,
    compile_projectile_motion_checkpoint_blueprints,
    materialize_projectile_motion_nodes,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
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
    RoutedProjectileMotionBeatV1,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1

SUPPORTED_PROBLEMS = tuple(
    ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)
    for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
    for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
)
ALL_FRONTIERS = (None, *PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER)
_CUE_ORDER = ("enter", "exit", "transform", "trace_path", "emphasize", "focus")


def _state(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint | None,
    *,
    component_id: str = "lesson",
    clarified_topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active_clarification: ProjectileMotionClarificationTopic | None = None,
) -> ProjectileMotionStateV1:
    return ProjectileMotionStateV1(
        id=component_id,
        problem_spec=problem,
        last_main_checkpoint=frontier,
        clarified_topics=clarified_topics,
        active_clarification=active_clarification,
    )


def _advance_beat(
    problem: ProjectileMotionProblemSpecV1,
    *,
    base_problem: ProjectileMotionProblemSpecV1 | None,
    stage: ProjectileMotionStage = ProjectileMotionStage.SOLVE,
    component_id: str = "lesson",
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id="projectile-advance",
        component_id=component_id,
        base_problem_spec=base_problem,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=stage),
    )


def _clarify_beat(
    problem: ProjectileMotionProblemSpecV1,
    topic: ProjectileMotionClarificationTopic,
    *,
    component_id: str = "lesson",
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id=f"projectile-clarify-{topic.value}",
        component_id=component_id,
        base_problem_spec=problem,
        result_problem_spec=problem,
        route=ClarifyProjectileMotionRouteV1(topic=topic),
    )


def _retarget_beat(
    base_problem: ProjectileMotionProblemSpecV1,
    target_problem: ProjectileMotionProblemSpecV1,
    *,
    component_id: str = "lesson",
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id="projectile-retarget",
        component_id=component_id,
        base_problem_spec=base_problem,
        result_problem_spec=target_problem,
        route=RetargetProjectileMotionRouteV1(target_problem_spec=target_problem),
    )


def _node_map(nodes: tuple[SceneNode, ...]) -> dict[str, SceneNode]:
    return {node.id: node for node in nodes}


def _apply_blueprint(checkpoint: ProjectileMotionCheckpointBlueprint) -> tuple[SceneNode, ...]:
    nodes = _node_map(checkpoint.base_nodes)
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            nodes[operation.target_id] = operation.node
        else:
            assert isinstance(operation, RemoveSceneOperation)
            assert operation.id in nodes
            del nodes[operation.id]
    return tuple(nodes[node_id] for node_id in sorted(nodes))


def _cue(checkpoint: ProjectileMotionCheckpointBlueprint, kind: str):
    return next((cue for cue in checkpoint.choreography.phase.cues if cue.cue == kind), None)


def _assert_checkpoint_contract(checkpoint: ProjectileMotionCheckpointBlueprint) -> None:
    base_ids = set(_node_map(checkpoint.base_nodes))
    result_ids = set(_node_map(checkpoint.result_nodes))
    operation_ids = tuple(operation.target_id for operation in checkpoint.patch.operations)
    assert operation_ids == tuple(sorted(operation_ids))
    assert len(operation_ids) == len(set(operation_ids))
    assert 1 <= len(operation_ids) <= MAX_PATCH_OPERATIONS
    assert len(checkpoint.result_nodes) <= MAX_SCENE_NODES
    assert len(result_ids) == len(checkpoint.result_nodes)
    assert (
        len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
        <= MAX_NDJSON_FRAME_BYTES
    )
    assert _apply_blueprint(checkpoint) == checkpoint.result_nodes
    assert (
        materialize_projectile_motion_nodes(checkpoint.result_component) == checkpoint.result_nodes
    )
    assert checkpoint.patch.narration == checkpoint.presentation.checkpoint_narration
    assert checkpoint.presentation.transient_free is True

    cues = checkpoint.choreography.phase.cues
    assert 1 <= len(cues) <= MAX_CHOREOGRAPHY_V2_CUES
    assert tuple(cue.cue for cue in cues) == tuple(
        sorted((cue.cue for cue in cues), key=_CUE_ORDER.index)
    )
    reference_count = sum(
        2 if isinstance(cue, TracePathCueV2) else len(cue.target_ids) for cue in cues
    )
    assert reference_count <= MAX_CHOREOGRAPHY_V2_NODE_REFERENCES

    new_put_ids = {
        operation.target_id
        for operation in checkpoint.patch.operations
        if isinstance(operation, PutSceneOperation) and operation.target_id not in base_ids
    }
    updated_put_ids = {
        operation.target_id
        for operation in checkpoint.patch.operations
        if isinstance(operation, PutSceneOperation) and operation.target_id in base_ids
    }
    removed_ids = {
        operation.target_id
        for operation in checkpoint.patch.operations
        if isinstance(operation, RemoveSceneOperation)
    }
    enter = _cue(checkpoint, "enter")
    exit_ = _cue(checkpoint, "exit")
    transform = _cue(checkpoint, "transform")
    trace = _cue(checkpoint, "trace_path")
    assert set(enter.target_ids if enter is not None else ()) == new_put_ids
    assert set(exit_.target_ids if exit_ is not None else ()) == removed_ids

    trace_marker_ids: set[str] = set()
    if trace is not None:
        assert isinstance(trace, TracePathCueV2)
        assert trace.path_id in new_put_ids
        assert trace.marker_id in updated_put_ids
        trace_marker_ids.add(trace.marker_id)
        assert {trace.path_id, trace.marker_id}.isdisjoint(
            transform.target_ids if transform is not None else ()
        )
    assert set(transform.target_ids if transform is not None else ()) == (
        updated_put_ids - trace_marker_ids
    )
    assert new_put_ids.issubset(result_ids)
    assert updated_put_ids.issubset(base_ids & result_ids)
    assert removed_ids.issubset(base_ids - result_ids)


def test_all_nine_problems_resume_from_all_seven_frontiers() -> None:
    for problem in SUPPORTED_PROBLEMS:
        for prefix_length, frontier in enumerate(ALL_FRONTIERS):
            base = None if frontier is None else _state(problem, frontier)
            compiled = compile_projectile_motion_checkpoint_blueprints(
                _advance_beat(problem, base_problem=None if base is None else problem),
                base,
            )

            assert tuple(
                checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints
            ) == tuple(
                checkpoint.value
                for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[prefix_length:]
            )
            expected_base = _state(problem, frontier)
            assert compiled.base_component == expected_base
            assert compiled.result_component == _state(
                problem, ProjectileMotionMainCheckpoint.SUMMARY
            )
            if compiled.checkpoints:
                assert compiled.checkpoints[0].base_nodes == materialize_projectile_motion_nodes(
                    expected_base
                )
            for checkpoint in compiled.checkpoints:
                _assert_checkpoint_contract(checkpoint)
            for previous, current in pairwise(compiled.checkpoints):
                assert previous.result_component == current.base_component
                assert previous.result_nodes == current.base_nodes
                assert previous.presentation.result_viewports == current.presentation.base_viewports


def test_every_problem_uses_two_uniform_time_33_point_path_halves() -> None:
    for problem in SUPPORTED_PROBLEMS:
        terminal = compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=None)
        ).result_component
        nodes = _node_map(materialize_projectile_motion_nodes(terminal))
        ascent = nodes["lesson__trajectory_ascent"]
        descent = nodes["lesson__trajectory_descent"]
        assert isinstance(ascent, PathSceneNode)
        assert isinstance(descent, PathSceneNode)
        assert ascent.closed is False
        assert descent.closed is False
        assert len(ascent.points) == len(descent.points) == 33
        assert ascent.points[-1] == pytest.approx(descent.points[0])

        radians = math.radians(problem.angle_deg)
        horizontal = problem.speed_mps * math.cos(radians)
        vertical = problem.speed_mps * math.sin(radians)
        flight_time = 2.0 * vertical / 10.0
        height = vertical * vertical / 20.0
        range_m = horizontal * flight_time
        samples = (*ascent.points, *descent.points[1:])
        assert len(samples) == 65
        for index, point in enumerate(samples):
            normalized_time = index / 64.0
            expected = (
                70.0 + 5.0 * range_m * normalized_time,
                470.0 - 10.0 * 4.0 * height * normalized_time * (1.0 - normalized_time),
            )
            assert point == pytest.approx(expected, abs=1e-9)
        x_steps = tuple(right[0] - left[0] for left, right in pairwise(samples))
        assert max(x_steps) == pytest.approx(min(x_steps), abs=1e-12)


def test_trace_checkpoints_preserve_patch_classification_and_trace_ownership() -> None:
    compiled = compile_projectile_motion_checkpoint_blueprints(
        _advance_beat(SUPPORTED_PROBLEMS[1], base_problem=None)
    )
    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in compiled.checkpoints}

    for checkpoint_id, path_suffix in (
        (ProjectileMotionCheckpointId.TRACE_ASCENT, "trajectory_ascent"),
        (ProjectileMotionCheckpointId.TRACE_DESCENT, "trajectory_descent"),
    ):
        checkpoint = by_id[checkpoint_id]
        trace = _cue(checkpoint, "trace_path")
        enter = _cue(checkpoint, "enter")
        transform = _cue(checkpoint, "transform")
        assert isinstance(trace, TracePathCueV2)
        assert trace.path_id == f"lesson__{path_suffix}"
        assert trace.marker_id == "lesson__projectile_marker"
        assert enter is not None and trace.path_id in enter.target_ids
        assert transform is None or trace.marker_id not in transform.target_ids


def test_main_lesson_authors_exactly_36_6_seconds_with_bounded_holds() -> None:
    for problem in SUPPORTED_PROBLEMS:
        checkpoints = compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=None)
        ).checkpoints
        assert tuple(checkpoint.checkpoint_id.value for checkpoint in checkpoints) == tuple(
            checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
        )
        assert sum(checkpoint.choreography.phase.total_ms for checkpoint in checkpoints) == 36_600
        assert (
            max(checkpoint.choreography.phase.hold_after_ms for checkpoint in checkpoints) == 1_200
        )


def test_all_three_clarifications_are_legal_once_for_every_problem() -> None:
    for problem in SUPPORTED_PROBLEMS:
        for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER:
            frontier = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
            base = _state(problem, frontier)
            compiled = compile_projectile_motion_checkpoint_blueprints(
                _clarify_beat(problem, topic), base
            )

            assert len(compiled.checkpoints) == 1
            checkpoint = compiled.checkpoints[0]
            assert checkpoint.checkpoint_id is PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[topic]
            assert checkpoint.result_component.last_main_checkpoint is frontier
            assert checkpoint.result_component.clarified_topics == (topic,)
            assert checkpoint.result_component.active_clarification is topic
            _assert_checkpoint_contract(checkpoint)
            with pytest.raises(ProjectileMotionCompilationError, match="one-shot"):
                compile_projectile_motion_checkpoint_blueprints(
                    _clarify_beat(problem, topic), checkpoint.result_component
                )


def test_eligible_clarifications_work_out_of_pedagogical_order_and_advance_clears_detail() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    state = _state(problem, ProjectileMotionMainCheckpoint.SUMMARY)
    requested_order = (
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
        ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    )
    previous_checkpoint: ProjectileMotionCheckpointBlueprint | None = None
    for topic in requested_order:
        compiled = compile_projectile_motion_checkpoint_blueprints(
            _clarify_beat(problem, topic), state
        )
        checkpoint = compiled.checkpoints[0]
        assert checkpoint.result_component.active_clarification is topic
        expected_ledger = tuple(
            candidate
            for candidate in PROJECTILE_MOTION_CLARIFICATION_ORDER
            if candidate in (*state.clarified_topics, topic)
        )
        assert checkpoint.result_component.clarified_topics == expected_ledger
        assert {node.id for node in checkpoint.result_nodes if "__clarify_" in node.id} == {
            f"lesson__clarify_{topic.value}"
        }
        if previous_checkpoint is not None:
            assert (
                previous_checkpoint.presentation.result_viewports
                == checkpoint.presentation.base_viewports
            )
        _assert_checkpoint_contract(checkpoint)
        state = checkpoint.result_component
        previous_checkpoint = checkpoint

    launch = _state(
        problem,
        ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY,
        clarified_topics=(ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,),
        active_clarification=ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
    )
    continued = compile_projectile_motion_checkpoint_blueprints(
        _advance_beat(problem, base_problem=problem, stage=ProjectileMotionStage.FLIGHT),
        launch,
    )
    assert continued.checkpoints[0].result_component.active_clarification is None
    assert continued.result_component.clarified_topics == launch.clarified_topics
    assert all("__clarify_" not in node.id for node in continued.checkpoints[0].result_nodes)
    assert (
        continued.checkpoints[0].presentation.base_viewports
        == compile_projectile_motion_checkpoint_blueprints(
            _clarify_beat(problem, ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY),
            _state(problem, ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY),
        )
        .checkpoints[0]
        .presentation.result_viewports
    )


def test_clarifications_reject_every_frontier_before_their_prerequisite() -> None:
    problem = SUPPORTED_PROBLEMS[0]
    for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER:
        prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
        prerequisite_index = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(prerequisite)
        for frontier in ALL_FRONTIERS[: prerequisite_index + 1]:
            with pytest.raises(ProjectileMotionCompilationError, match="not legal"):
                compile_projectile_motion_checkpoint_blueprints(
                    _clarify_beat(problem, topic), _state(problem, frontier)
                )


def test_all_72_directed_retargets_work_at_every_legal_frontier_with_stable_ids() -> None:
    directed_pairs = tuple(permutations(SUPPORTED_PROBLEMS, 2))
    assert len(directed_pairs) == 72

    for base_problem, target_problem in directed_pairs:
        for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER:
            base = _state(base_problem, frontier)
            compiled = compile_projectile_motion_checkpoint_blueprints(
                _retarget_beat(base_problem, target_problem), base
            )

            assert len(compiled.checkpoints) == 1
            checkpoint = compiled.checkpoints[0]
            assert checkpoint.checkpoint_id is ProjectileMotionCheckpointId.PARAMETERS_RETARGETED
            assert checkpoint.result_component.id == base.id
            assert checkpoint.result_component.problem_spec == target_problem
            assert checkpoint.result_component.last_main_checkpoint is frontier
            assert checkpoint.result_component.clarified_topics == base.clarified_topics
            assert checkpoint.result_component.active_clarification is None
            assert tuple(node.id for node in checkpoint.base_nodes) == tuple(
                node.id for node in checkpoint.result_nodes
            )
            _assert_checkpoint_contract(checkpoint)


def test_retarget_preserves_active_detail_and_numeric_labels_crossfade_under_stable_ids() -> None:
    base_problem = SUPPORTED_PROBLEMS[0]
    target_problem = SUPPORTED_PROBLEMS[-2]
    topics = PROJECTILE_MOTION_CLARIFICATION_ORDER
    base = _state(
        base_problem,
        ProjectileMotionMainCheckpoint.SUMMARY,
        clarified_topics=topics,
        active_clarification=ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
    )
    checkpoint = compile_projectile_motion_checkpoint_blueprints(
        _retarget_beat(base_problem, target_problem), base
    ).checkpoints[0]

    assert checkpoint.result_component.clarified_topics == topics
    assert (
        checkpoint.result_component.active_clarification
        is ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
    )
    assert checkpoint.presentation.base_viewports == checkpoint.presentation.result_viewports
    base_nodes = _node_map(checkpoint.base_nodes)
    result_nodes = _node_map(checkpoint.result_nodes)
    transform = _cue(checkpoint, "transform")
    assert transform is not None
    for suffix in ("givens", "component_values", "height_value", "summary_values"):
        node_id = f"lesson__{suffix}"
        before = base_nodes[node_id]
        after = result_nodes[node_id]
        assert isinstance(before, LatexTokenSceneNode)
        assert isinstance(after, LatexTokenSceneNode)
        assert before.id == after.id == node_id
        assert before.latex != after.latex
        assert node_id in transform.target_ids


@pytest.mark.parametrize(
    ("frontier", "stage"),
    [
        (ProjectileMotionMainCheckpoint.SETUP, ProjectileMotionStage.SETUP),
        (ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY, ProjectileMotionStage.LAUNCH),
        (ProjectileMotionMainCheckpoint.TRACE_DESCENT, ProjectileMotionStage.FLIGHT),
        (ProjectileMotionMainCheckpoint.SUMMARY, ProjectileMotionStage.SOLVE),
    ],
)
def test_no_op_advance_returns_an_empty_immutable_batch(
    frontier: ProjectileMotionMainCheckpoint,
    stage: ProjectileMotionStage,
) -> None:
    problem = SUPPORTED_PROBLEMS[1]
    base = _state(problem, frontier)
    compiled = compile_projectile_motion_checkpoint_blueprints(
        _advance_beat(problem, base_problem=problem, stage=stage), base
    )

    assert compiled.checkpoints == ()
    assert compiled.base_component == compiled.result_component == base


@pytest.mark.parametrize(
    ("frontier", "stage"),
    [
        (ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY, ProjectileMotionStage.SETUP),
        (ProjectileMotionMainCheckpoint.TRACE_ASCENT, ProjectileMotionStage.LAUNCH),
        (ProjectileMotionMainCheckpoint.SUMMARY, ProjectileMotionStage.FLIGHT),
    ],
)
def test_backward_advance_is_rejected(
    frontier: ProjectileMotionMainCheckpoint,
    stage: ProjectileMotionStage,
) -> None:
    problem = SUPPORTED_PROBLEMS[1]
    with pytest.raises(ProjectileMotionCompilationError, match="cannot move backward"):
        compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=problem, stage=stage),
            _state(problem, frontier),
        )


def test_invalid_base_and_empty_retarget_fail_before_any_blueprint_exists() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    other = SUPPORTED_PROBLEMS[2]
    with pytest.raises(ProjectileMotionCompilationError, match="requires a base"):
        compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=problem)
        )
    with pytest.raises(ProjectileMotionCompilationError, match="requires baseProblemSpec"):
        compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=None),
            _state(problem, ProjectileMotionMainCheckpoint.SETUP),
        )
    with pytest.raises(ProjectileMotionCompilationError, match="componentId"):
        compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=problem, component_id="other"),
            _state(problem, ProjectileMotionMainCheckpoint.SETUP),
        )
    with pytest.raises(ProjectileMotionCompilationError, match="baseProblemSpec"):
        compile_projectile_motion_checkpoint_blueprints(
            _advance_beat(problem, base_problem=problem),
            _state(other, ProjectileMotionMainCheckpoint.SETUP),
        )
    with pytest.raises(ProjectileMotionCompilationError, match="only after a settled checkpoint"):
        compile_projectile_motion_checkpoint_blueprints(
            _retarget_beat(problem, other), _state(problem, None)
        )


def test_compilation_is_byte_deterministic_and_imports_no_entropy_source() -> None:
    beat = _advance_beat(SUPPORTED_PROBLEMS[1], base_problem=None)
    first = compile_projectile_motion_checkpoint_blueprints(beat)
    second = compile_projectile_motion_checkpoint_blueprints(beat)

    def payload(batch):
        return {
            "beat": batch.beat.model_dump(mode="json", by_alias=True),
            "base": batch.base_component.model_dump(mode="json", by_alias=True),
            "result": batch.result_component.model_dump(mode="json", by_alias=True),
            "checkpoints": [
                {
                    "id": checkpoint.checkpoint_id.value,
                    "base": checkpoint.base_component.model_dump(mode="json", by_alias=True),
                    "result": checkpoint.result_component.model_dump(mode="json", by_alias=True),
                    "baseNodes": [
                        node.model_dump(mode="json", by_alias=True)
                        for node in checkpoint.base_nodes
                    ],
                    "resultNodes": [
                        node.model_dump(mode="json", by_alias=True)
                        for node in checkpoint.result_nodes
                    ],
                    "patch": checkpoint.patch.model_dump(mode="json", by_alias=True),
                    "choreography": checkpoint.choreography.model_dump(mode="json", by_alias=True),
                    "presentation": checkpoint.presentation.model_dump(mode="json", by_alias=True),
                }
                for checkpoint in batch.checkpoints
            ],
        }

    assert canonical_json_v1(payload(first)) == canonical_json_v1(payload(second))

    tree = ast.parse(inspect.getsource(projectile_motion_compiler))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint({"datetime", "random", "secrets", "time", "uuid"})

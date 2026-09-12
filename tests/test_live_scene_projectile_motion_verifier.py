from __future__ import annotations

import ast
import inspect
import math
from dataclasses import replace
from itertools import permutations

import pytest
from murmur.live_scene import projectile_motion_verifier
from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV2,
    TracePathCueV2,
    TransformCueV1,
    ViewportPoseV1,
)
from murmur.live_scene.contracts import (
    LatexTokenSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneState,
)
from murmur.live_scene.projectile_motion_compiler import (
    ProjectileMotionCheckpointBlueprint,
    compile_projectile_motion_checkpoint_blueprints,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
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
from murmur.live_scene.projectile_motion_verifier import (
    ProjectileMotionVerificationCode,
    ProjectileMotionVerificationError,
    verify_projectile_motion_checkpoint,
    verify_projectile_motion_frontier,
)

SUPPORTED_PROBLEMS = tuple(
    ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)
    for speed in (20, 25, 30)
    for angle in (30, 45, 60)
)


def _state(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint | None,
    *,
    topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active: ProjectileMotionClarificationTopic | None = None,
) -> ProjectileMotionStateV1:
    return ProjectileMotionStateV1(
        id="lesson",
        problem_spec=problem,
        last_main_checkpoint=frontier,
        clarified_topics=topics,
        active_clarification=active,
    )


def _advance_beat(
    problem: ProjectileMotionProblemSpecV1,
    *,
    base_problem: ProjectileMotionProblemSpecV1 | None,
    stage: ProjectileMotionStage = ProjectileMotionStage.SOLVE,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id="advance-projectile",
        component_id="lesson",
        base_problem_spec=base_problem,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=stage),
    )


def _clarify_beat(
    problem: ProjectileMotionProblemSpecV1,
    topic: ProjectileMotionClarificationTopic,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id=f"clarify-{topic.value}",
        component_id="lesson",
        base_problem_spec=problem,
        result_problem_spec=problem,
        route=ClarifyProjectileMotionRouteV1(topic=topic),
    )


def _retarget_beat(
    base_problem: ProjectileMotionProblemSpecV1,
    result_problem: ProjectileMotionProblemSpecV1,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id="retarget-projectile",
        component_id="lesson",
        base_problem_spec=base_problem,
        result_problem_spec=result_problem,
        route=RetargetProjectileMotionRouteV1(target_problem_spec=result_problem),
    )


def _full_lesson(
    problem: ProjectileMotionProblemSpecV1 = SUPPORTED_PROBLEMS[1],
) -> tuple[ProjectileMotionCheckpointBlueprint, ...]:
    return compile_projectile_motion_checkpoint_blueprints(
        _advance_beat(problem, base_problem=None)
    ).checkpoints


def _checkpoint(
    checkpoint_id: ProjectileMotionCheckpointId,
    problem: ProjectileMotionProblemSpecV1 = SUPPORTED_PROBLEMS[1],
) -> ProjectileMotionCheckpointBlueprint:
    return next(
        checkpoint
        for checkpoint in _full_lesson(problem)
        if checkpoint.checkpoint_id is checkpoint_id
    )


def _replace_result_node(
    checkpoint: ProjectileMotionCheckpointBlueprint,
    node_id: str,
    node,
) -> ProjectileMotionCheckpointBlueprint:
    assert any(candidate.id == node_id for candidate in checkpoint.result_nodes)
    result_nodes = tuple(
        node if candidate.id == node_id else candidate for candidate in checkpoint.result_nodes
    )
    return replace(checkpoint, result_nodes=result_nodes)


def _replace_choreography_cues(
    checkpoint: ProjectileMotionCheckpointBlueprint,
    cues: tuple,
) -> ProjectileMotionCheckpointBlueprint:
    phase = checkpoint.choreography.phase.model_copy(update={"cues": cues})
    choreography = checkpoint.choreography.model_copy(update={"phase": phase})
    return replace(checkpoint, choreography=choreography)


def _assert_rejected(
    checkpoint: ProjectileMotionCheckpointBlueprint,
    code: ProjectileMotionVerificationCode,
) -> None:
    with pytest.raises(ProjectileMotionVerificationError) as captured:
        verify_projectile_motion_checkpoint(checkpoint)
    assert captured.value.code is code


def _apply_checkpoint_to_scene(
    scene: SceneState,
    checkpoint: ProjectileMotionCheckpointBlueprint,
) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        else:
            assert isinstance(operation, RemoveSceneOperation)
            del nodes[operation.id]
            order.remove(operation.id)
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _accepted_main_frontiers(
    problem: ProjectileMotionProblemSpecV1,
) -> tuple[tuple[ProjectileMotionStateV1, SceneState], ...]:
    scene = SceneState(revision=0)
    accepted: list[tuple[ProjectileMotionStateV1, SceneState]] = []
    for checkpoint in _full_lesson(problem):
        scene = _apply_checkpoint_to_scene(scene, checkpoint)
        accepted.append((checkpoint.result_component, scene))
    return tuple(accepted)


def _replace_scene_node(scene: SceneState, node_id: str, replacement) -> SceneState:
    assert any(node.id == node_id for node in scene.nodes)
    return scene.model_copy(
        update={"nodes": tuple(replacement if node.id == node_id else node for node in scene.nodes)}
    )


def _assert_frontier_rejected(
    component: ProjectileMotionStateV1,
    scene: SceneState,
    code: ProjectileMotionVerificationCode,
) -> None:
    with pytest.raises(ProjectileMotionVerificationError) as captured:
        verify_projectile_motion_frontier(component, scene)
    assert captured.value.code is code


def test_verifier_source_has_no_compiler_or_derived_problem_dependency() -> None:
    source = inspect.getsource(projectile_motion_verifier)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert all("projectile_motion_compiler" not in module for module in imported_modules)
    assert "initial_horizontal_velocity_mps" not in source
    assert "initial_vertical_velocity_mps" not in source
    assert "ascent_time_seconds" not in source
    assert "flight_time_seconds" not in source
    assert "maximum_height_m" not in source
    assert ".range_m" not in source


@pytest.mark.parametrize(
    "problem", SUPPORTED_PROBLEMS, ids=lambda item: f"{item.speed_mps}@{item.angle_deg}"
)
def test_every_committed_main_frontier_uses_patch_history_order_and_verifies(
    problem: ProjectileMotionProblemSpecV1,
) -> None:
    frontiers = _accepted_main_frontiers(problem)
    assert len(frontiers) == 6
    saw_nonlexical_order = False
    for component, scene in frontiers:
        node_ids = tuple(node.id for node in scene.nodes)
        saw_nonlexical_order |= node_ids != tuple(sorted(node_ids))
        assert verify_projectile_motion_frontier(component, scene) is None
    assert saw_nonlexical_order is True


def test_frontier_accepts_out_of_order_clarifications_and_retarget_in_place() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    target_problem = SUPPORTED_PROBLEMS[-1]
    component, scene = _accepted_main_frontiers(problem)[-1]
    for topic in (
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
        ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    ):
        checkpoint = compile_projectile_motion_checkpoint_blueprints(
            _clarify_beat(problem, topic),
            component,
        ).checkpoints[0]
        scene = _apply_checkpoint_to_scene(scene, checkpoint)
        component = checkpoint.result_component
        assert scene.nodes[-1].id == f"lesson__clarify_{topic.value}"
        assert verify_projectile_motion_frontier(component, scene) is None

    retarget = compile_projectile_motion_checkpoint_blueprints(
        _retarget_beat(problem, target_problem),
        component,
    ).checkpoints[0]
    scene = _apply_checkpoint_to_scene(scene, retarget)
    component = retarget.result_component
    assert verify_projectile_motion_frontier(component, scene) is None


def test_frontier_rejects_unsettled_or_revision_zero_committed_state() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    _assert_frontier_rejected(
        _state(problem, None),
        SceneState(revision=1),
        ProjectileMotionVerificationCode.TRANSITION,
    )
    component, scene = _accepted_main_frontiers(problem)[0]
    _assert_frontier_rejected(
        component,
        scene.model_copy(update={"revision": 0}),
        ProjectileMotionVerificationCode.TRANSITION,
    )
    summary_component, summary_scene = _accepted_main_frontiers(problem)[-1]
    _assert_frontier_rejected(
        summary_component,
        summary_scene.model_copy(update={"revision": 5}),
        ProjectileMotionVerificationCode.TRANSITION,
    )


def test_frontier_rejects_foreign_dirty_reordered_duplicate_and_unsafe_nodes() -> None:
    component, scene = _accepted_main_frontiers(SUPPORTED_PROBLEMS[1])[3]
    first = scene.nodes[0]

    foreign = first.model_copy(update={"id": "foreign__ground"})
    _assert_frontier_rejected(
        component,
        scene.model_copy(update={"nodes": (*scene.nodes, foreign)}),
        ProjectileMotionVerificationCode.STABLE_IDS,
    )

    dirty = first.model_copy(update={"id": "lesson__dirty"})
    _assert_frontier_rejected(
        component,
        scene.model_copy(update={"nodes": (*scene.nodes, dirty)}),
        ProjectileMotionVerificationCode.STABLE_IDS,
    )

    reordered = SceneState(
        revision=scene.revision,
        nodes=(scene.nodes[1], scene.nodes[0], *scene.nodes[2:]),
    )
    _assert_frontier_rejected(
        component,
        reordered,
        ProjectileMotionVerificationCode.STABLE_IDS,
    )

    duplicate = SceneState.model_construct(
        revision=scene.revision,
        nodes=(*scene.nodes, first),
    )
    _assert_frontier_rejected(
        component,
        duplicate,
        ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
    )

    unsafe = first.model_copy(update={"id": "lesson__unsafe!"})
    unsafe_scene = SceneState.model_construct(
        revision=scene.revision,
        nodes=(unsafe, *scene.nodes[1:]),
    )
    _assert_frontier_rejected(
        component,
        unsafe_scene,
        ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
    )


def test_frontier_rejects_physics_label_layout_style_and_trajectory_mutations() -> None:
    frontiers = _accepted_main_frontiers(SUPPORTED_PROBLEMS[1])

    setup_component, setup_scene = frontiers[0]
    resultant = next(node for node in setup_scene.nodes if node.id == "lesson__velocity_resultant")
    assert isinstance(resultant, PathSceneNode)
    points = (
        *resultant.points[:3],
        (resultant.points[3][0] + 1.0, resultant.points[3][1]),
        *resultant.points[4:],
    )
    _assert_frontier_rejected(
        setup_component,
        _replace_scene_node(
            setup_scene,
            resultant.id,
            resultant.model_copy(update={"points": points}),
        ),
        ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
    )
    summary_component, summary_scene = frontiers[-1]
    summary = next(node for node in summary_scene.nodes if node.id == "lesson__summary_values")
    assert isinstance(summary, LatexTokenSceneNode)
    _assert_frontier_rejected(
        summary_component,
        _replace_scene_node(
            summary_scene,
            summary.id,
            summary.model_copy(update={"latex": summary.latex.replace("R&=", "R&=999+", 1)}),
        ),
        ProjectileMotionVerificationCode.LABEL_FACT,
    )

    decompose_component, decompose_scene = frontiers[1]
    equation = next(node for node in decompose_scene.nodes if node.id == "lesson__equation_x")
    assert isinstance(equation, LatexTokenSceneNode)
    _assert_frontier_rejected(
        decompose_component,
        _replace_scene_node(
            decompose_scene,
            equation.id,
            equation.model_copy(update={"x": equation.x - 1.0}),
        ),
        ProjectileMotionVerificationCode.LABEL_LAYOUT,
    )

    ascent_component, ascent_scene = frontiers[2]
    ascent = next(node for node in ascent_scene.nodes if node.id == "lesson__trajectory_ascent")
    assert isinstance(ascent, PathSceneNode)
    style = ascent.style.model_copy(update={"stroke_width": 3.0})
    _assert_frontier_rejected(
        ascent_component,
        _replace_scene_node(
            ascent_scene,
            ascent.id,
            ascent.model_copy(update={"style": style}),
        ),
        ProjectileMotionVerificationCode.VISUAL_STYLE,
    )

    path_points = list(ascent.points)
    path_points[16] = (path_points[16][0], path_points[16][1] + 1.0)
    _assert_frontier_rejected(
        ascent_component,
        _replace_scene_node(
            ascent_scene,
            ascent.id,
            ascent.model_copy(update={"points": tuple(path_points)}),
        ),
        ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
    )


def test_frontier_rejects_board_clipping_and_text_collision() -> None:
    component, scene = _accepted_main_frontiers(SUPPORTED_PROBLEMS[1])[0]
    ground = next(node for node in scene.nodes if node.id == "lesson__ground")
    clipped = ground.model_copy(update={"points": ((0.0, ground.points[0][1]), ground.points[1])})
    _assert_frontier_rejected(
        component,
        _replace_scene_node(scene, ground.id, clipped),
        ProjectileMotionVerificationCode.BOARD_BOUNDS,
    )

    title = next(node for node in scene.nodes if node.id == "lesson__title")
    assert isinstance(title, LatexTokenSceneNode)
    collision = title.model_copy(update={"x": 600.0, "y": 92.0})
    _assert_frontier_rejected(
        component,
        _replace_scene_node(scene, title.id, collision),
        ProjectileMotionVerificationCode.TEXT_COLLISION,
    )


@pytest.mark.parametrize(
    "problem", SUPPORTED_PROBLEMS, ids=lambda item: f"{item.speed_mps}@{item.angle_deg}"
)
def test_every_main_checkpoint_of_every_problem_passes_independent_verification(
    problem: ProjectileMotionProblemSpecV1,
) -> None:
    checkpoints = _full_lesson(problem)
    assert len(checkpoints) == 6
    assert tuple(checkpoint.checkpoint_id.value for checkpoint in checkpoints) == tuple(
        checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    )
    for checkpoint in checkpoints:
        assert verify_projectile_motion_checkpoint(checkpoint) is None
    assert sum(checkpoint.choreography.phase.total_ms for checkpoint in checkpoints) == 36_600


def test_every_distinct_emitted_token_rejects_a_one_unit_smaller_frame() -> None:
    examples: dict[
        tuple[str, str, float, float],
        tuple[ProjectileMotionCheckpointBlueprint, LatexTokenSceneNode],
    ] = {}
    for problem in SUPPORTED_PROBLEMS:
        checkpoints = list(_full_lesson(problem))
        checkpoints.extend(
            compile_projectile_motion_checkpoint_blueprints(
                _clarify_beat(problem, topic),
                _state(problem, ProjectileMotionMainCheckpoint.SUMMARY),
            ).checkpoints[0]
            for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER
        )
        for checkpoint in checkpoints:
            for node in checkpoint.result_nodes:
                if not isinstance(node, LatexTokenSceneNode):
                    continue
                suffix = node.id.split("__", 1)[1]
                examples.setdefault(
                    (suffix, node.latex, node.width, node.height),
                    (checkpoint, node),
                )

    assert {key[0] for key in examples} == {
        "apex_acceleration",
        "apex_velocity",
        "axis_x",
        "axis_y",
        "clarify_apex_acceleration",
        "clarify_flight_symmetry",
        "clarify_horizontal_velocity",
        "component_values",
        "equation_x",
        "equation_y",
        "givens",
        "height_value",
        "label_horizontal",
        "label_resultant",
        "label_vertical",
        "summary_values",
        "title",
        "vertical_state",
    }
    for checkpoint, token in examples.values():
        narrower = _replace_result_node(
            checkpoint,
            token.id,
            token.model_copy(update={"width": token.width - 1.0}),
        )
        with pytest.raises(ProjectileMotionVerificationError, match="browser-safe frame") as error:
            verify_projectile_motion_checkpoint(narrower)
        assert error.value.code is ProjectileMotionVerificationCode.LABEL_LAYOUT

        shorter = _replace_result_node(
            checkpoint,
            token.id,
            token.model_copy(update={"height": token.height - 1.0}),
        )
        with pytest.raises(ProjectileMotionVerificationError, match="browser-safe frame") as error:
            verify_projectile_motion_checkpoint(shorter)
        assert error.value.code is ProjectileMotionVerificationCode.LABEL_LAYOUT


@pytest.mark.parametrize(
    "problem", SUPPORTED_PROBLEMS, ids=lambda item: f"{item.speed_mps}@{item.angle_deg}"
)
def test_paths_encode_exact_uniform_time_parabola_and_apex_invariants(
    problem: ProjectileMotionProblemSpecV1,
) -> None:
    summary = _checkpoint(ProjectileMotionCheckpointId.SUMMARY, problem)
    nodes = {node.id: node for node in summary.result_nodes}
    ascent = nodes["lesson__trajectory_ascent"]
    descent = nodes["lesson__trajectory_descent"]
    assert isinstance(ascent, PathSceneNode)
    assert isinstance(descent, PathSceneNode)
    assert len(ascent.points) == len(descent.points) == 33
    samples = (*ascent.points, *descent.points[1:])

    theta = math.pi * problem.angle_deg / 180.0
    horizontal = problem.speed_mps * math.cos(theta)
    vertical = problem.speed_mps * math.sin(theta)
    flight_time = 2.0 * vertical / 10.0
    height = vertical * vertical / 20.0
    range_m = horizontal * flight_time
    for index, point in enumerate(samples):
        normalized = index / 64.0
        assert point[0] == pytest.approx(70.0 + 5.0 * normalized * range_m, abs=1e-9)
        assert point[1] == pytest.approx(
            470.0 - 10.0 * 4.0 * height * normalized * (1.0 - normalized),
            abs=1e-9,
        )
    assert vertical - 10.0 * flight_time / 2.0 == pytest.approx(0.0, abs=1e-12)
    acceleration = nodes["lesson__acceleration"]
    assert isinstance(acceleration, PathSceneNode)
    assert acceleration.points[3][1] > ascent.points[-1][1]
    assert verify_projectile_motion_checkpoint(summary) is None


def test_all_eligible_clarifications_and_out_of_order_active_detours_verify() -> None:
    verified = 0
    for problem in SUPPORTED_PROBLEMS:
        for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER:
            prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
            start = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(prerequisite)
            for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[start:]:
                checkpoint = compile_projectile_motion_checkpoint_blueprints(
                    _clarify_beat(problem, topic),
                    _state(problem, frontier),
                ).checkpoints[0]
                assert verify_projectile_motion_checkpoint(checkpoint) is None
                verified += 1
    assert verified == 90

    problem = SUPPORTED_PROBLEMS[1]
    current = _state(problem, ProjectileMotionMainCheckpoint.SUMMARY)
    for topic in (
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
        ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    ):
        checkpoint = compile_projectile_motion_checkpoint_blueprints(
            _clarify_beat(problem, topic), current
        ).checkpoints[0]
        assert verify_projectile_motion_checkpoint(checkpoint) is None
        current = checkpoint.result_component


def test_forward_advance_closes_active_clarification_and_verifies() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    topic = ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
    base = _state(
        problem,
        ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY,
        topics=(topic,),
        active=topic,
    )
    checkpoints = compile_projectile_motion_checkpoint_blueprints(
        _advance_beat(
            problem,
            base_problem=problem,
            stage=ProjectileMotionStage.FLIGHT,
        ),
        base,
    ).checkpoints
    assert checkpoints[0].result_component.active_clarification is None
    for checkpoint in checkpoints:
        assert verify_projectile_motion_checkpoint(checkpoint) is None


def test_all_432_directed_retargets_verify_base_and_result_problem_geometry() -> None:
    verified = 0
    for base_problem, result_problem in permutations(SUPPORTED_PROBLEMS, 2):
        for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER:
            checkpoint = compile_projectile_motion_checkpoint_blueprints(
                _retarget_beat(base_problem, result_problem),
                _state(base_problem, frontier),
            ).checkpoints[0]
            assert checkpoint.base_component.problem_spec == base_problem
            assert checkpoint.result_component.problem_spec == result_problem
            assert verify_projectile_motion_checkpoint(checkpoint) is None
            verified += 1
    assert verified == 432


def test_retarget_preserves_and_verifies_the_active_clarification_detour() -> None:
    topics = PROJECTILE_MOTION_CLARIFICATION_ORDER
    for active in topics:
        checkpoint = compile_projectile_motion_checkpoint_blueprints(
            _retarget_beat(SUPPORTED_PROBLEMS[0], SUPPORTED_PROBLEMS[-1]),
            _state(
                SUPPORTED_PROBLEMS[0],
                ProjectileMotionMainCheckpoint.SUMMARY,
                topics=topics,
                active=active,
            ),
        ).checkpoints[0]
        assert checkpoint.result_component.active_clarification is active
        assert verify_projectile_motion_checkpoint(checkpoint) is None


def test_rejects_degrees_as_radians_launch_vector_mutation() -> None:
    checkpoint = _checkpoint(ProjectileMotionCheckpointId.SETUP)
    node = next(node for node in checkpoint.result_nodes if node.id == "lesson__velocity_resultant")
    assert isinstance(node, PathSceneNode)
    wrong_tip = (
        70.0 + 3.0 * 20.0 * math.cos(45.0),
        470.0 - 3.0 * 20.0 * math.sin(45.0),
    )
    points = (*node.points[:3], wrong_tip, *node.points[4:])
    mutated = _replace_result_node(checkpoint, node.id, node.model_copy(update={"points": points}))
    _assert_rejected(mutated, ProjectileMotionVerificationCode.PHYSICS_GEOMETRY)


def test_rejects_displayed_value_drift_and_wrong_acceleration_sign() -> None:
    decompose = _checkpoint(ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY)
    values = next(node for node in decompose.result_nodes if node.id == "lesson__component_values")
    assert isinstance(values, LatexTokenSceneNode)
    mutated_values = _replace_result_node(
        decompose,
        values.id,
        values.model_copy(update={"latex": values.latex.replace("14.14", "14.15", 1)}),
    )
    _assert_rejected(mutated_values, ProjectileMotionVerificationCode.LABEL_FACT)

    apex = _checkpoint(ProjectileMotionCheckpointId.APEX_STATE)
    acceleration_label = next(
        node for node in apex.result_nodes if node.id == "lesson__apex_acceleration"
    )
    assert isinstance(acceleration_label, LatexTokenSceneNode)
    mutated_sign = _replace_result_node(
        apex,
        acceleration_label.id,
        acceleration_label.model_copy(update={"latex": r"a_y=+g=+10\,\mathrm{m/s^2}"}),
    )
    _assert_rejected(mutated_sign, ProjectileMotionVerificationCode.LABEL_FACT)


def test_rejects_upward_acceleration_geometry() -> None:
    checkpoint = _checkpoint(ProjectileMotionCheckpointId.APEX_STATE)
    node = next(node for node in checkpoint.result_nodes if node.id == "lesson__acceleration")
    assert isinstance(node, PathSceneNode)
    apex_y = next(
        candidate.points[-1][1]
        for candidate in checkpoint.result_nodes
        if candidate.id == "lesson__trajectory_ascent" and isinstance(candidate, PathSceneNode)
    )
    reflected = tuple((x, 2.0 * apex_y - y) for x, y in node.points)
    mutated = _replace_result_node(
        checkpoint,
        node.id,
        node.model_copy(update={"points": reflected}),
    )
    _assert_rejected(mutated, ProjectileMotionVerificationCode.PHYSICS_GEOMETRY)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("point", ProjectileMotionVerificationCode.PHYSICS_GEOMETRY),
        ("straight_chord", ProjectileMotionVerificationCode.PHYSICS_GEOMETRY),
        ("below_ground", ProjectileMotionVerificationCode.PHYSICS_GEOMETRY),
        ("topology", ProjectileMotionVerificationCode.PHYSICS_GEOMETRY),
    ],
)
def test_rejects_trajectory_point_curve_ground_and_topology_mutations(
    mutation: str,
    expected_code: ProjectileMotionVerificationCode,
) -> None:
    checkpoint_id = (
        ProjectileMotionCheckpointId.TRACE_DESCENT
        if mutation == "below_ground"
        else ProjectileMotionCheckpointId.TRACE_ASCENT
    )
    checkpoint = _checkpoint(checkpoint_id)
    suffix = "trajectory_descent" if mutation == "below_ground" else "trajectory_ascent"
    node = next(node for node in checkpoint.result_nodes if node.id == f"lesson__{suffix}")
    assert isinstance(node, PathSceneNode)
    points = list(node.points)
    if mutation == "point":
        points[16] = (points[16][0], points[16][1] + 1.0)
    elif mutation == "straight_chord":
        start, end = points[0], points[-1]
        points = [
            (
                start[0] + (end[0] - start[0]) * index / 32.0,
                start[1] + (end[1] - start[1]) * index / 32.0,
            )
            for index in range(33)
        ]
    elif mutation == "below_ground":
        points[16] = (points[16][0], 471.0)
    else:
        points.pop(16)
    mutated = _replace_result_node(
        checkpoint,
        node.id,
        node.model_copy(update={"points": tuple(points)}),
    )
    _assert_rejected(mutated, expected_code)


def test_rejects_marker_endpoint_and_trace_identity_mutations() -> None:
    checkpoint = _checkpoint(ProjectileMotionCheckpointId.TRACE_ASCENT)
    marker = next(
        node for node in checkpoint.result_nodes if node.id == "lesson__projectile_marker"
    )
    assert isinstance(marker, PathSceneNode)
    shifted = tuple((x + 2.0, y) for x, y in marker.points)
    mutated_marker = _replace_result_node(
        checkpoint,
        marker.id,
        marker.model_copy(update={"points": shifted}),
    )
    _assert_rejected(mutated_marker, ProjectileMotionVerificationCode.PHYSICS_GEOMETRY)

    cues = tuple(
        cue.model_copy(update={"marker_id": cue.path_id})
        if isinstance(cue, TracePathCueV2)
        else cue
        for cue in checkpoint.choreography.phase.cues
    )
    _assert_rejected(
        _replace_choreography_cues(checkpoint, cues),
        ProjectileMotionVerificationCode.CHOREOGRAPHY,
    )


def test_rejects_trace_transform_ownership_overlap() -> None:
    checkpoint = _checkpoint(ProjectileMotionCheckpointId.TRACE_ASCENT)
    cues = list(checkpoint.choreography.phase.cues)
    trace_index = next(index for index, cue in enumerate(cues) if isinstance(cue, TracePathCueV2))
    cues.insert(trace_index, TransformCueV1(targetIds=("lesson__projectile_marker",)))
    _assert_rejected(
        _replace_choreography_cues(checkpoint, tuple(cues)),
        ProjectileMotionVerificationCode.CHOREOGRAPHY,
    )


def test_rejects_text_collision_and_clipped_viewport_mutations() -> None:
    summary = _checkpoint(ProjectileMotionCheckpointId.SUMMARY)
    token = next(node for node in summary.result_nodes if node.id == "lesson__summary_values")
    assert isinstance(token, LatexTokenSceneNode)
    collision = token.model_copy(update={"x": 640.0, "y": 222.0})
    _assert_rejected(
        _replace_result_node(summary, token.id, collision),
        ProjectileMotionVerificationCode.TEXT_COLLISION,
    )

    apex = _checkpoint(ProjectileMotionCheckpointId.APEX_STATE)
    clipped_pose = ViewportPoseV1(x=0.0, y=0.0, width=100.0, height=100.0)
    result_viewports = apex.presentation.result_viewports.model_copy(
        update={"cinematic": clipped_pose}
    )
    presentation = apex.presentation.model_copy(update={"result_viewports": result_viewports})
    _assert_rejected(
        replace(apex, presentation=presentation),
        ProjectileMotionVerificationCode.VIEWPORT,
    )


def test_rejects_style_label_layout_operation_and_timing_mutations() -> None:
    ascent = _checkpoint(ProjectileMotionCheckpointId.TRACE_ASCENT)
    path = next(node for node in ascent.result_nodes if node.id == "lesson__trajectory_ascent")
    assert isinstance(path, PathSceneNode)
    style = path.style.model_copy(update={"stroke_width": 3.0})
    _assert_rejected(
        _replace_result_node(ascent, path.id, path.model_copy(update={"style": style})),
        ProjectileMotionVerificationCode.VISUAL_STYLE,
    )

    decompose = _checkpoint(ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY)
    equation = next(node for node in decompose.result_nodes if node.id == "lesson__equation_x")
    assert isinstance(equation, LatexTokenSceneNode)
    _assert_rejected(
        _replace_result_node(
            decompose,
            equation.id,
            equation.model_copy(update={"x": equation.x - 1.0}),
        ),
        ProjectileMotionVerificationCode.LABEL_LAYOUT,
    )

    setup = _checkpoint(ProjectileMotionCheckpointId.SETUP)
    patch = setup.patch.model_copy(update={"operations": tuple(reversed(setup.patch.operations))})
    _assert_rejected(replace(setup, patch=patch), ProjectileMotionVerificationCode.PATCH)

    phase = setup.choreography.phase.model_copy(
        update={"duration_ms": setup.choreography.phase.duration_ms - 100}
    )
    plan = setup.choreography.model_copy(update={"phase": phase})
    assert isinstance(plan, ChoreographyPlanV2)
    _assert_rejected(replace(setup, choreography=plan), ProjectileMotionVerificationCode.TIMING)


def test_rejects_wrong_stable_id_and_caption_mutations() -> None:
    setup = _checkpoint(ProjectileMotionCheckpointId.SETUP)
    node = setup.result_nodes[0]
    renamed = node.model_copy(update={"id": "lesson__invented_role"})
    result_nodes = tuple(
        sorted(
            (renamed if candidate.id == node.id else candidate for candidate in setup.result_nodes),
            key=lambda candidate: candidate.id,
        )
    )
    _assert_rejected(
        replace(setup, result_nodes=result_nodes),
        ProjectileMotionVerificationCode.STABLE_IDS,
    )

    presentation = setup.presentation.model_copy(update={"checkpoint_narration": "Wrong fact."})
    patch = setup.patch.model_copy(update={"narration": "Wrong fact."})
    _assert_rejected(
        replace(setup, presentation=presentation, patch=patch),
        ProjectileMotionVerificationCode.CAPTION,
    )


def test_rejects_illegal_state_transition_and_retarget_problem_join() -> None:
    setup = _checkpoint(ProjectileMotionCheckpointId.SETUP)
    wrong_frontier = setup.result_component.model_copy(
        update={"last_main_checkpoint": ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY}
    )
    _assert_rejected(
        replace(setup, result_component=wrong_frontier),
        ProjectileMotionVerificationCode.TRANSITION,
    )

    retarget = compile_projectile_motion_checkpoint_blueprints(
        _retarget_beat(SUPPORTED_PROBLEMS[0], SUPPORTED_PROBLEMS[1]),
        _state(SUPPORTED_PROBLEMS[0], ProjectileMotionMainCheckpoint.SETUP),
    ).checkpoints[0]
    same_problem = retarget.result_component.model_copy(
        update={"problem_spec": retarget.base_component.problem_spec}
    )
    _assert_rejected(
        replace(retarget, result_component=same_problem),
        ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
    )


def test_rejects_non_blueprint_objects_at_public_boundary() -> None:
    class IncompleteBlueprint:
        checkpoint_id = ProjectileMotionCheckpointId.SETUP

    with pytest.raises(ProjectileMotionVerificationError) as captured:
        verify_projectile_motion_checkpoint(IncompleteBlueprint())  # type: ignore[arg-type]
    assert captured.value.code is ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT


def test_patch_put_nodes_are_bound_to_the_declared_result_snapshot() -> None:
    setup = _checkpoint(ProjectileMotionCheckpointId.SETUP)
    first = setup.patch.operations[0]
    assert isinstance(first, PutSceneOperation)
    wrong_node = first.node.model_copy(update={"id": "lesson__wrong_put_target"})
    wrong_operation = first.model_copy(update={"node": wrong_node})
    operations = (wrong_operation, *setup.patch.operations[1:])
    patch = setup.patch.model_copy(update={"operations": operations})
    _assert_rejected(replace(setup, patch=patch), ProjectileMotionVerificationCode.PATCH)

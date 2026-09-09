from __future__ import annotations

import ast
import inspect
from itertools import combinations, pairwise

import pytest
from murmur.live_scene import parametric_completing_square_compiler
from murmur.live_scene.choreography_contracts import (
    CompletingSquareStage,
    RoutedChoreographyBeatV3,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexTokenSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    ParametricCheckpointBlueprint,
    ParametricCompletingSquareCompilationError,
    compile_parametric_checkpoint_blueprints,
    materialize_parametric_nodes,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1

SUPPORTED_CASES = tuple(
    (half, magnitude, 2 * half, magnitude * magnitude - half * half)
    for half in range(1, 9)
    for magnitude in range(half + 1, 10)
)
SUPPORTED_CASE_IDS = tuple(f"h{half}-m{magnitude}" for half, magnitude, _, _ in SUPPORTED_CASES)


def _problem(linear_coefficient: int = 8, right_hand_side: int = 20):
    return CompletingSquareProblemSpecV1(
        linearCoefficient=linear_coefficient,
        rightHandSide=right_hand_side,
    )


def _advance_beat(
    problem: CompletingSquareProblemSpecV1,
    stage: CompletingSquareStage = CompletingSquareStage.SOLVE,
    *,
    beat_id: str = "beat-parametric-solve",
    component_id: str = "lesson",
) -> RoutedChoreographyBeatV3:
    return RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": beat_id,
            "componentKind": "completing_square_parametric",
            "componentId": component_id,
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "advance", "targetStage": stage.value},
        }
    )


def _clarify_beat(
    problem: CompletingSquareProblemSpecV1,
    *,
    component_id: str = "lesson",
) -> RoutedChoreographyBeatV3:
    return RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": "beat-parametric-corner",
            "componentKind": "completing_square_parametric",
            "componentId": component_id,
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "clarify_corner"},
        }
    )


def _state(
    problem: CompletingSquareProblemSpecV1,
    checkpoint: CompletingSquareMainCheckpoint | None,
    *,
    component_id: str = "lesson",
    clarified: bool = False,
) -> ParametricCompletingSquareStateV1:
    return ParametricCompletingSquareStateV1(
        id=component_id,
        problem_spec=problem,
        last_main_checkpoint=checkpoint,
        corner_clarified=clarified,
    )


def _node_map(nodes: tuple[SceneNode, ...]) -> dict[str, SceneNode]:
    return {node.id: node for node in nodes}


def _latex(nodes: tuple[SceneNode, ...], node_id: str) -> str:
    node = _node_map(nodes)[node_id]
    assert isinstance(node, LatexTokenSceneNode)
    return node.latex


def _apply_blueprint(checkpoint: ParametricCheckpointBlueprint) -> tuple[SceneNode, ...]:
    node_order = [node.id for node in checkpoint.base_nodes]
    nodes = _node_map(checkpoint.base_nodes)
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                node_order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        else:
            assert isinstance(operation, RemoveSceneOperation)
            node_order.remove(operation.id)
            del nodes[operation.id]
    return tuple(nodes[node_id] for node_id in node_order)


def _node_bounds(node: SceneNode) -> tuple[float, float, float, float]:
    if isinstance(node, PathSceneNode):
        xs = tuple(point[0] for point in node.points)
        ys = tuple(point[1] for point in node.points)
        padding = node.style.stroke_width / 2
        return min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding
    assert isinstance(node, LatexTokenSceneNode)
    left = node.x
    if node.anchor == "middle":
        left -= node.width / 2
    elif node.anchor == "end":
        left -= node.width
    return left, node.y, left + node.width, node.y + node.height


def _interiors_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
        left[1], right[1]
    )


def _serialized_batch(batch: object) -> bytes:
    checkpoints = batch.checkpoints  # type: ignore[attr-defined]
    return canonical_json_v1(
        {
            "beat": batch.beat.model_dump(mode="json", by_alias=True),  # type: ignore[attr-defined]
            "baseComponent": batch.base_component.model_dump(  # type: ignore[attr-defined]
                mode="json", by_alias=True
            ),
            "resultComponent": batch.result_component.model_dump(  # type: ignore[attr-defined]
                mode="json", by_alias=True
            ),
            "checkpoints": [
                {
                    "checkpointId": checkpoint.checkpoint_id.value,
                    "baseComponent": checkpoint.base_component.model_dump(
                        mode="json", by_alias=True
                    ),
                    "resultComponent": checkpoint.result_component.model_dump(
                        mode="json", by_alias=True
                    ),
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
                for checkpoint in checkpoints
            ],
        }
    )


@pytest.mark.parametrize(
    ("half", "magnitude", "linear_coefficient", "right_hand_side"),
    SUPPORTED_CASES,
    ids=SUPPORTED_CASE_IDS,
)
@pytest.mark.parametrize("prefix_length", range(9))
def test_all_36_problems_resume_from_all_nine_main_frontiers(
    half: int,
    magnitude: int,
    linear_coefficient: int,
    right_hand_side: int,
    prefix_length: int,
) -> None:
    problem = _problem(linear_coefficient, right_hand_side)
    frontier = (
        None if prefix_length == 0 else COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length - 1]
    )
    state = None if frontier is None else _state(problem, frontier)

    compiled = compile_parametric_checkpoint_blueprints(_advance_beat(problem), state)

    assert problem.half_coefficient == half
    assert problem.square_root_magnitude == magnitude
    assert tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints) == tuple(
        checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length:]
    )
    expected_base = _state(problem, frontier)
    assert compiled.base_component == expected_base
    assert compiled.result_component == _state(
        problem,
        CompletingSquareMainCheckpoint.SOLVE_ROOTS,
    )
    assert compiled.beat.problem_spec == problem
    if compiled.checkpoints:
        assert compiled.checkpoints[0].base_nodes == materialize_parametric_nodes(expected_base)
    else:
        assert frontier is CompletingSquareMainCheckpoint.SOLVE_ROOTS

    for checkpoint in compiled.checkpoints:
        assert checkpoint.base_component.problem_spec == problem
        assert checkpoint.result_component.problem_spec == problem
        assert checkpoint.patch.narration == checkpoint.presentation.checkpoint_narration
        assert 1 <= len(checkpoint.patch.operations) <= MAX_PATCH_OPERATIONS
        assert (
            len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
            <= MAX_NDJSON_FRAME_BYTES
        )
        assert tuple(operation.target_id for operation in checkpoint.patch.operations) == tuple(
            sorted(operation.target_id for operation in checkpoint.patch.operations)
        )
        assert _apply_blueprint(checkpoint) == checkpoint.result_nodes

    for previous, current in pairwise(compiled.checkpoints):
        assert previous.result_component == current.base_component
        assert previous.result_nodes == current.base_nodes
        assert previous.presentation.result_viewports == current.presentation.base_viewports


@pytest.mark.parametrize(
    ("half", "magnitude", "linear_coefficient", "right_hand_side"),
    SUPPORTED_CASES,
    ids=SUPPORTED_CASE_IDS,
)
def test_every_problem_has_one_atomic_corner_detour_that_rejoins(
    half: int,
    magnitude: int,
    linear_coefficient: int,
    right_hand_side: int,
) -> None:
    del half, magnitude
    problem = _problem(linear_coefficient, right_hand_side)
    missing = _state(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)

    detail = compile_parametric_checkpoint_blueprints(_clarify_beat(problem), missing)

    assert len(detail.checkpoints) == 1
    checkpoint = detail.checkpoints[0]
    assert checkpoint.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
    assert checkpoint.base_component == missing
    assert checkpoint.result_component == _state(
        problem,
        CompletingSquareMainCheckpoint.MISSING_CORNER,
        clarified=True,
    )
    assert {operation.target_id for operation in checkpoint.patch.operations} == {
        "lesson__corner_area",
        "lesson__corner_calc",
    }
    assert _apply_blueprint(checkpoint) == checkpoint.result_nodes
    assert materialize_parametric_nodes(checkpoint.result_component) == checkpoint.result_nodes

    with pytest.raises(
        ParametricCompletingSquareCompilationError,
        match="only at the unclarified",
    ):
        compile_parametric_checkpoint_blueprints(
            _clarify_beat(problem),
            checkpoint.result_component,
        )

    continuation = compile_parametric_checkpoint_blueprints(
        _advance_beat(
            problem,
            CompletingSquareStage.SOLVE,
            beat_id="beat-parametric-resume-after-corner",
        ),
        checkpoint.result_component,
    )
    assert tuple(item.checkpoint_id for item in continuation.checkpoints) == (
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
        CompletingSquareCheckpointId.FACTOR_SQUARE,
        CompletingSquareCheckpointId.SOLVE_ROOTS,
    )
    assert continuation.result_component.corner_clarified is True
    assert (
        checkpoint.presentation.result_viewports
        == continuation.checkpoints[0].presentation.base_viewports
    )


def test_x_squared_plus_eight_x_exactly_derives_every_visible_value() -> None:
    problem = _problem(8, 20)
    compiled = compile_parametric_checkpoint_blueprints(_advance_beat(problem))
    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in compiled.checkpoints}

    problem_nodes = by_id[CompletingSquareCheckpointId.PROBLEM].result_nodes
    assert _latex(problem_nodes, "lesson__eq_square") == "x^2"
    assert _latex(problem_nodes, "lesson__eq_linear") == "8x"
    assert _latex(problem_nodes, "lesson__eq_rhs") == "20"

    area_nodes = by_id[CompletingSquareCheckpointId.AREA_MODEL].result_nodes
    assert _latex(area_nodes, "lesson__area_half_a") == "4x"
    assert _latex(area_nodes, "lesson__area_half_b") == "4x"
    assert _latex(area_nodes, "lesson__scale_note") == r"\text{not to scale}"

    split_nodes = by_id[CompletingSquareCheckpointId.SPLIT_LINEAR_TERM].result_nodes
    assert _latex(split_nodes, "lesson__eq_half_a") == "4x"
    assert _latex(split_nodes, "lesson__eq_half_b") == "4x"
    assert _latex(split_nodes, "lesson__half_calc") == r"8\div 2=4"

    missing_nodes = by_id[CompletingSquareCheckpointId.MISSING_CORNER].result_nodes
    assert _latex(missing_nodes, "lesson__corner_dim_h") == "4"
    assert _latex(missing_nodes, "lesson__corner_dim_v") == "4"
    assert _latex(missing_nodes, "lesson__corner_area") == "?"

    detail = compile_parametric_checkpoint_blueprints(
        _clarify_beat(problem),
        by_id[CompletingSquareCheckpointId.MISSING_CORNER].result_component,
    ).checkpoints[0]
    assert _latex(detail.result_nodes, "lesson__corner_area") == "16"
    assert _latex(detail.result_nodes, "lesson__corner_calc") == r"4\times 4=4^2=16"

    complete_nodes = by_id[CompletingSquareCheckpointId.BALANCE_AND_COMPLETE].result_nodes
    assert _latex(complete_nodes, "lesson__eq_corner_value") == "16"
    assert _latex(complete_nodes, "lesson__eq_rhs") == "20"
    assert _latex(complete_nodes, "lesson__eq_rhs_corner") == "16"
    assert _latex(complete_nodes, "lesson__eq_completed_rhs") == "36=6^2"
    assert _latex(complete_nodes, "lesson__corner_calc") == r"4\times 4=4^2=16"

    factor_nodes = by_id[CompletingSquareCheckpointId.FACTOR_SQUARE].result_nodes
    assert _latex(factor_nodes, "lesson__eq_factor") == "(x+4)^2"
    assert _latex(factor_nodes, "lesson__eq_completed_rhs") == "36=6^2"

    root_nodes = by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].result_nodes
    assert _latex(root_nodes, "lesson__root_lhs") == "x+4"
    assert _latex(root_nodes, "lesson__root_pm") == r"\pm 6"
    assert _latex(root_nodes, "lesson__root_positive") == "2"
    assert _latex(root_nodes, "lesson__root_negative") == "-10"
    assert "plus or minus 6" in by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].patch.narration
    assert "x is 2 or -10" in by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].patch.narration


@pytest.mark.parametrize(
    ("linear_coefficient", "right_hand_side", "corner", "completed", "root_a", "root_b"),
    [
        (2, 80, 1, "81=9^2", "8", "-10"),
        (16, 17, 64, "81=9^2", "1", "-17"),
    ],
)
def test_boundary_problems_keep_exact_math_inside_the_same_symbolic_geometry(
    linear_coefficient: int,
    right_hand_side: int,
    corner: int,
    completed: str,
    root_a: str,
    root_b: str,
) -> None:
    problem = _problem(linear_coefficient, right_hand_side)
    checkpoints = compile_parametric_checkpoint_blueprints(_advance_beat(problem)).checkpoints
    complete = checkpoints[5].result_nodes
    solved = checkpoints[7].result_nodes

    assert _latex(complete, "lesson__corner_area") == str(corner)
    assert _latex(complete, "lesson__eq_completed_rhs") == completed
    assert _latex(solved, "lesson__root_positive") == root_a
    assert _latex(solved, "lesson__root_negative") == root_b

    reference = compile_parametric_checkpoint_blueprints(_advance_beat(_problem(8, 20)))
    reference_paths = {
        node.id: node.points
        for node in reference.checkpoints[7].result_nodes
        if isinstance(node, PathSceneNode)
    }
    actual_paths = {node.id: node.points for node in solved if isinstance(node, PathSceneNode)}
    assert actual_paths == reference_paths


def test_unit_half_coefficient_is_rendered_as_x_not_one_x() -> None:
    problem = _problem(2, 80)
    checkpoints = compile_parametric_checkpoint_blueprints(_advance_beat(problem)).checkpoints

    for index in (1, 2, 3, 4):
        nodes = checkpoints[index].result_nodes
        assert _latex(nodes, "lesson__area_half_a") == "x"
        assert _latex(nodes, "lesson__area_half_b") == "x"
    split_nodes = checkpoints[2].result_nodes
    assert _latex(split_nodes, "lesson__eq_half_a") == "x"
    assert _latex(split_nodes, "lesson__eq_half_b") == "x"
    assert "1x" not in " ".join(
        node.latex for node in checkpoints[2].result_nodes if isinstance(node, LatexTokenSceneNode)
    )
    assert "1 x" not in checkpoints[1].patch.narration
    assert "two equal x strips" in checkpoints[1].patch.narration


def test_every_problem_uses_identical_value_neutral_node_ids_and_fixed_tile_geometry() -> None:
    expected_ids_by_checkpoint: dict[CompletingSquareCheckpointId, tuple[str, ...]] | None = None
    expected_paths_by_checkpoint: (
        dict[
            CompletingSquareCheckpointId,
            dict[str, tuple[tuple[float, float], ...]],
        ]
        | None
    ) = None

    for _, _, linear_coefficient, right_hand_side in SUPPORTED_CASES:
        checkpoints = compile_parametric_checkpoint_blueprints(
            _advance_beat(_problem(linear_coefficient, right_hand_side))
        ).checkpoints
        ids_by_checkpoint = {
            checkpoint.checkpoint_id: tuple(node.id for node in checkpoint.result_nodes)
            for checkpoint in checkpoints
        }
        paths_by_checkpoint = {
            checkpoint.checkpoint_id: {
                node.id: node.points
                for node in checkpoint.result_nodes
                if isinstance(node, PathSceneNode)
            }
            for checkpoint in checkpoints
        }
        if expected_ids_by_checkpoint is None:
            expected_ids_by_checkpoint = ids_by_checkpoint
            expected_paths_by_checkpoint = paths_by_checkpoint
            continue
        assert ids_by_checkpoint == expected_ids_by_checkpoint
        assert paths_by_checkpoint == expected_paths_by_checkpoint

    assert expected_ids_by_checkpoint is not None
    all_ids = {
        node_id
        for checkpoint_ids in expected_ids_by_checkpoint.values()
        for node_id in checkpoint_ids
    }
    assert {
        "lesson__eq_linear",
        "lesson__eq_rhs",
        "lesson__eq_half_a",
        "lesson__eq_half_b",
        "lesson__eq_corner_value",
        "lesson__eq_rhs_corner",
        "lesson__eq_completed_rhs",
        "lesson__area_half_a",
        "lesson__area_half_b",
        "lesson__corner_dim_h",
        "lesson__corner_dim_v",
        "lesson__root_pm",
        "lesson__root_positive",
        "lesson__root_negative",
        "lesson__scale_note",
    }.issubset(all_ids)


def test_tiles_use_orientation_and_labels_so_color_is_not_the_only_carrier() -> None:
    checkpoints = compile_parametric_checkpoint_blueprints(_advance_beat(_problem())).checkpoints
    arranged = _node_map(checkpoints[3].result_nodes)
    strip_a = arranged["lesson__strip_a"]
    strip_b = arranged["lesson__strip_b"]
    assert isinstance(strip_a, PathSceneNode)
    assert isinstance(strip_b, PathSceneNode)
    a_width = max(point[0] for point in strip_a.points) - min(point[0] for point in strip_a.points)
    a_height = max(point[1] for point in strip_a.points) - min(point[1] for point in strip_a.points)
    b_width = max(point[0] for point in strip_b.points) - min(point[0] for point in strip_b.points)
    b_height = max(point[1] for point in strip_b.points) - min(point[1] for point in strip_b.points)

    assert a_width > a_height
    assert b_height > b_width
    assert _latex(checkpoints[3].result_nodes, "lesson__area_half_a") == "4x"
    assert _latex(checkpoints[3].result_nodes, "lesson__area_half_b") == "4x"
    assert _latex(checkpoints[4].result_nodes, "lesson__corner_dim_h") == "4"
    assert _latex(checkpoints[4].result_nodes, "lesson__corner_dim_v") == "4"
    assert _latex(checkpoints[4].result_nodes, "lesson__corner_area") == "?"


def test_every_problem_and_corner_detour_has_collision_free_visible_text() -> None:
    for _, _, linear_coefficient, right_hand_side in SUPPORTED_CASES:
        problem = _problem(linear_coefficient, right_hand_side)
        compiled = compile_parametric_checkpoint_blueprints(_advance_beat(problem))
        missing = compiled.checkpoints[4].result_component
        detail = compile_parametric_checkpoint_blueprints(
            _clarify_beat(problem),
            missing,
        ).checkpoints[0]

        for checkpoint in (*compiled.checkpoints, detail):
            text_nodes = tuple(
                node for node in checkpoint.result_nodes if isinstance(node, LatexTokenSceneNode)
            )
            for left, right in combinations(text_nodes, 2):
                assert not _interiors_overlap(_node_bounds(left), _node_bounds(right)), (
                    problem,
                    checkpoint.checkpoint_id,
                    left.id,
                    right.id,
                )


def test_delimiter_bearing_component_id_keeps_every_cue_target_real() -> None:
    component_id = "lesson__cp_alt"
    compiled = compile_parametric_checkpoint_blueprints(
        _advance_beat(_problem(), component_id=component_id)
    )

    for checkpoint in compiled.checkpoints:
        real_ids = {node.id for node in (*checkpoint.base_nodes, *checkpoint.result_nodes)}
        assert all(
            target_id in real_ids
            for cue in checkpoint.choreography.phase.cues
            for target_id in cue.target_ids
        )


def test_main_timing_is_47_seconds_and_viewport_targets_are_safe_for_all_problems() -> None:
    cue_order = ("enter", "exit", "transform", "emphasize", "focus")
    for _, _, linear_coefficient, right_hand_side in SUPPORTED_CASES:
        checkpoints = compile_parametric_checkpoint_blueprints(
            _advance_beat(_problem(linear_coefficient, right_hand_side))
        ).checkpoints
        assert sum(checkpoint.choreography.phase.total_ms for checkpoint in checkpoints) == 47_000
        assert all(checkpoint.presentation.transient_free for checkpoint in checkpoints)
        for previous, current in pairwise(checkpoints):
            assert previous.presentation.result_viewports == current.presentation.base_viewports

        for checkpoint in checkpoints:
            base_ids = set(_node_map(checkpoint.base_nodes))
            nodes = _node_map(checkpoint.result_nodes)
            result_ids = set(nodes)
            cues = checkpoint.choreography.phase.cues
            assert tuple(cue.cue for cue in cues) == tuple(
                sorted((cue.cue for cue in cues), key=cue_order.index)
            )
            for cue in cues:
                assert cue.target_ids == tuple(sorted(cue.target_ids))
                assert len(cue.target_ids) == len(set(cue.target_ids))
                if cue.cue == "exit":
                    assert set(cue.target_ids).issubset(base_ids)
                elif cue.cue == "transform":
                    assert set(cue.target_ids).issubset(base_ids & result_ids)
                else:
                    assert set(cue.target_ids).issubset(result_ids)
            target_ids = {
                node_id
                for cue in checkpoint.choreography.phase.cues
                if cue.cue in {"emphasize", "focus"}
                for node_id in cue.target_ids
            }
            for viewport in (
                checkpoint.presentation.result_viewports.cinematic,
                checkpoint.presentation.result_viewports.compact,
            ):
                right = viewport.x + viewport.width
                bottom = viewport.y + viewport.height
                for node_id in target_ids:
                    left, top, node_right, node_bottom = _node_bounds(nodes[node_id])
                    assert left >= viewport.x + 12.0
                    assert top >= viewport.y + 12.0
                    assert node_right <= right - 12.0
                    assert node_bottom <= bottom - 12.0


def test_compilation_is_byte_deterministic_and_imports_no_entropy_source() -> None:
    beat = _advance_beat(_problem())
    first = compile_parametric_checkpoint_blueprints(beat)
    second = compile_parametric_checkpoint_blueprints(beat)

    assert _serialized_batch(first) == _serialized_batch(second)

    tree = ast.parse(inspect.getsource(parametric_completing_square_compiler))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint({"datetime", "random", "secrets", "time", "uuid"})


def test_problem_component_and_backward_mismatches_fail_before_a_blueprint_exists() -> None:
    problem = _problem(8, 20)
    other_problem = _problem(6, 7)
    beat = _advance_beat(problem)

    with pytest.raises(ParametricCompletingSquareCompilationError, match="componentId"):
        compile_parametric_checkpoint_blueprints(
            _advance_beat(problem, component_id="other"),
            _state(problem, CompletingSquareMainCheckpoint.PROBLEM),
        )
    with pytest.raises(ParametricCompletingSquareCompilationError, match="problemSpec"):
        compile_parametric_checkpoint_blueprints(
            beat,
            _state(other_problem, CompletingSquareMainCheckpoint.PROBLEM),
        )
    with pytest.raises(ParametricCompletingSquareCompilationError, match="cannot move backward"):
        compile_parametric_checkpoint_blueprints(
            _advance_beat(problem, CompletingSquareStage.COMPLETE),
            _state(problem, CompletingSquareMainCheckpoint.FACTOR_SQUARE),
        )


@pytest.mark.parametrize(
    ("frontier", "clarified"),
    [
        (None, False),
        (CompletingSquareMainCheckpoint.PROBLEM, False),
        (CompletingSquareMainCheckpoint.AREA_MODEL, False),
        (CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM, False),
        (CompletingSquareMainCheckpoint.REARRANGE_HALVES, False),
        (CompletingSquareMainCheckpoint.MISSING_CORNER, True),
        (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, False),
        (CompletingSquareMainCheckpoint.FACTOR_SQUARE, False),
        (CompletingSquareMainCheckpoint.SOLVE_ROOTS, False),
    ],
)
def test_corner_clarification_rejects_every_illegal_frontier(
    frontier: CompletingSquareMainCheckpoint | None,
    clarified: bool,
) -> None:
    problem = _problem()
    state = _state(problem, frontier, clarified=clarified)

    with pytest.raises(
        ParametricCompletingSquareCompilationError,
        match="only at the unclarified",
    ):
        compile_parametric_checkpoint_blueprints(_clarify_beat(problem), state)

    assert state == _state(problem, frontier, clarified=clarified)

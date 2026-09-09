from __future__ import annotations

import ast
import inspect
from collections.abc import Callable

import pytest
from murmur.live_scene import parametric_completing_square_verifier
from murmur.live_scene.choreography_contracts import (
    ChoreographyPhaseV1,
    ChoreographyPlanV1,
    EmphasizeCueV1,
    FocusCueV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV3,
    ViewportPoseV1,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
    completing_square_problem_sha256,
)
from murmur.live_scene.contracts import (
    LatexTokenSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RectSceneNode,
    SceneNode,
    ScenePatchDraft,
    SceneState,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointVerificationObligationV3,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    ParametricCheckpointBlueprint,
    compile_parametric_checkpoint_blueprints,
    materialize_parametric_nodes,
)
from murmur.live_scene.parametric_completing_square_verifier import (
    ParametricCompletingSquareVerificationError,
    verify_parametric_completing_square_checkpoint,
    verify_parametric_completing_square_frontier,
)
from pydantic import ValidationError


def _problem(half: int, magnitude: int) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=2 * half,
        rightHandSide=magnitude * magnitude - half * half,
    )


PROBLEMS = tuple(
    _problem(half, magnitude) for half in range(1, 9) for magnitude in range(half + 1, 10)
)
assert len(PROBLEMS) == 36


def _problem_id(problem: CompletingSquareProblemSpecV1) -> str:
    return f"b{problem.linear_coefficient}-c{problem.right_hand_side}"


LEGAL_FRONTIERS = (
    (None, False),
    (CompletingSquareMainCheckpoint.PROBLEM, False),
    (CompletingSquareMainCheckpoint.AREA_MODEL, False),
    (CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM, False),
    (CompletingSquareMainCheckpoint.REARRANGE_HALVES, False),
    (CompletingSquareMainCheckpoint.MISSING_CORNER, False),
    (CompletingSquareMainCheckpoint.MISSING_CORNER, True),
    (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, False),
    (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, True),
    (CompletingSquareMainCheckpoint.FACTOR_SQUARE, False),
    (CompletingSquareMainCheckpoint.FACTOR_SQUARE, True),
    (CompletingSquareMainCheckpoint.SOLVE_ROOTS, False),
    (CompletingSquareMainCheckpoint.SOLVE_ROOTS, True),
)
assert len(LEGAL_FRONTIERS) == 13


def _advance_beat(problem: CompletingSquareProblemSpecV1) -> RoutedChoreographyBeatV3:
    return RoutedChoreographyBeatV3.model_validate(
        {
            "beatId": "beat-parametric-solve",
            "componentId": "lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "advance", "targetStage": "solve"},
        }
    )


def _clarify_beat(problem: CompletingSquareProblemSpecV1) -> RoutedChoreographyBeatV3:
    return RoutedChoreographyBeatV3.model_validate(
        {
            "beatId": "beat-parametric-corner",
            "componentId": "lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "clarify_corner"},
        }
    )


def _main_blueprints(
    problem: CompletingSquareProblemSpecV1,
) -> tuple[ParametricCheckpointBlueprint, ...]:
    return compile_parametric_checkpoint_blueprints(_advance_beat(problem)).checkpoints


def _detail_blueprint(
    problem: CompletingSquareProblemSpecV1,
) -> ParametricCheckpointBlueprint:
    state = ParametricCompletingSquareStateV1(
        id="lesson",
        problemSpec=problem,
        lastMainCheckpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
    )
    return compile_parametric_checkpoint_blueprints(_clarify_beat(problem), state).checkpoints[0]


def _blueprint(
    problem: CompletingSquareProblemSpecV1,
    checkpoint_id: CompletingSquareCheckpointId,
) -> ParametricCheckpointBlueprint:
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        return _detail_blueprint(problem)
    return next(
        checkpoint
        for checkpoint in _main_blueprints(problem)
        if checkpoint.checkpoint_id is checkpoint_id
    )


def _scenes(
    checkpoint: ParametricCheckpointBlueprint,
    *,
    base_nodes: tuple[SceneNode, ...] | None = None,
    result_nodes: tuple[SceneNode, ...] | None = None,
) -> tuple[SceneState, SceneState]:
    return (
        SceneState(
            revision=20,
            nodes=checkpoint.base_nodes if base_nodes is None else base_nodes,
        ),
        SceneState(
            revision=21,
            nodes=checkpoint.result_nodes if result_nodes is None else result_nodes,
        ),
    )


def _verify(
    problem: CompletingSquareProblemSpecV1,
    checkpoint: ParametricCheckpointBlueprint,
    *,
    base_scene: SceneState | None = None,
    result_scene: SceneState | None = None,
    patch: ScenePatchDraft | None = None,
    choreography: ChoreographyPlanV1 | None = None,
    presentation: PresentationCheckpointV1 | None = None,
):
    default_base, default_result = _scenes(checkpoint)
    return verify_parametric_completing_square_checkpoint(
        "lesson",
        problem,
        checkpoint.checkpoint_id,
        default_base if base_scene is None else base_scene,
        default_result if result_scene is None else result_scene,
        checkpoint.patch if patch is None else patch,
        checkpoint.presentation if presentation is None else presentation,
        checkpoint.choreography if choreography is None else choreography,
    )


def _result_node(
    checkpoint: ParametricCheckpointBlueprint,
    suffix: str,
) -> SceneNode:
    return next(node for node in checkpoint.result_nodes if node.id == f"lesson__{suffix}")


def _replace_result_puts(
    checkpoint: ParametricCheckpointBlueprint,
    replacements: dict[str, SceneNode],
) -> tuple[ScenePatchDraft, SceneState]:
    operations = tuple(
        operation.model_copy(update={"node": replacements[operation.target_id]})
        if isinstance(operation, PutSceneOperation) and operation.target_id in replacements
        else operation
        for operation in checkpoint.patch.operations
    )
    assert set(replacements) <= {
        operation.target_id
        for operation in checkpoint.patch.operations
        if isinstance(operation, PutSceneOperation)
    }
    result_nodes = tuple(replacements.get(node.id, node) for node in checkpoint.result_nodes)
    return (
        checkpoint.patch.model_copy(update={"operations": operations}),
        SceneState.model_construct(revision=21, nodes=result_nodes),
    )


def _replace_result_put(
    checkpoint: ParametricCheckpointBlueprint,
    replacement: SceneNode,
) -> tuple[ScenePatchDraft, SceneState]:
    return _replace_result_puts(checkpoint, {replacement.id: replacement})


@pytest.mark.parametrize("problem", PROBLEMS, ids=_problem_id)
@pytest.mark.parametrize("checkpoint_id", tuple(CompletingSquareCheckpointId))
def test_every_problem_and_checkpoint_passes_independent_verification(
    problem: CompletingSquareProblemSpecV1,
    checkpoint_id: CompletingSquareCheckpointId,
) -> None:
    checkpoint = _blueprint(problem, checkpoint_id)

    receipt = _verify(problem, checkpoint)

    assert receipt.component_kind == "completing_square_parametric"
    assert receipt.component_id == "lesson"
    assert receipt.problem_spec_sha256 == completing_square_problem_sha256(problem)
    assert receipt.checkpoint_id is checkpoint_id
    assert receipt.operation_targets == tuple(
        operation.target_id for operation in checkpoint.patch.operations
    )
    assert tuple(receipt.obligation_codes) == tuple(
        code for code in CheckpointVerificationObligationV3 if code in receipt.obligation_codes
    )


@pytest.mark.parametrize("problem", PROBLEMS, ids=_problem_id)
@pytest.mark.parametrize(
    ("last_checkpoint", "corner_clarified"),
    LEGAL_FRONTIERS,
    ids=lambda value: (
        value.value if isinstance(value, CompletingSquareMainCheckpoint) else str(value)
    ),
)
def test_every_problem_and_legal_semantic_frontier_is_verified(
    problem: CompletingSquareProblemSpecV1,
    last_checkpoint: CompletingSquareMainCheckpoint | None,
    corner_clarified: bool,
) -> None:
    state = ParametricCompletingSquareStateV1(
        id="lesson",
        problemSpec=problem,
        lastMainCheckpoint=last_checkpoint,
        cornerClarified=corner_clarified,
    )
    scene = SceneState(revision=12, nodes=materialize_parametric_nodes(state))

    verify_parametric_completing_square_frontier(state, scene)


def test_balance_transition_accepts_the_clarified_corner_predecessor() -> None:
    problem = _problem(4, 6)
    detail = _detail_blueprint(problem)
    beat = RoutedChoreographyBeatV3.model_validate(
        {
            "beatId": "beat-after-detail",
            "componentId": "lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "advance", "targetStage": "complete"},
        }
    )
    balance = compile_parametric_checkpoint_blueprints(beat, detail.result_component).checkpoints[0]

    assert balance.checkpoint_id is CompletingSquareCheckpointId.BALANCE_AND_COMPLETE
    _verify(problem, balance)


def test_receipt_obligations_include_problem_caption_and_timing_claims() -> None:
    problem = _problem(4, 6)
    receipt = _verify(problem, _blueprint(problem, CompletingSquareCheckpointId.SOLVE_ROOTS))

    assert {
        CheckpointVerificationObligationV3.PROBLEM_IDENTITY,
        CheckpointVerificationObligationV3.CAPTION_FACTS,
        CheckpointVerificationObligationV3.AUTHORED_TIMING,
    } <= set(receipt.obligation_codes)
    assert len(receipt.obligation_codes) == len(CheckpointVerificationObligationV3)
    with pytest.raises(ValidationError, match="frozen"):
        receipt.verified = False  # type: ignore[misc]


def test_verifier_imports_no_compiler_and_uses_no_problem_derived_helpers() -> None:
    source = inspect.getsource(parametric_completing_square_verifier)
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    all_attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert all("compiler" not in module for module in imports)
    assert {
        "half_coefficient",
        "corner_value",
        "completed_right_hand_side",
        "square_root_magnitude",
        "positive_root",
        "negative_root",
    }.isdisjoint(all_attribute_names)
    assert "_EXPECTED_CHECKPOINT" not in source


@pytest.mark.parametrize("problem", PROBLEMS, ids=_problem_id)
def test_each_problem_rejects_mutated_math_at_every_derived_stage(
    problem: CompletingSquareProblemSpecV1,
) -> None:
    mutations = (
        (CompletingSquareCheckpointId.PROBLEM, "eq_linear", "999x"),
        (CompletingSquareCheckpointId.SPLIT_LINEAR_TERM, "eq_half_a", "999x"),
        (CompletingSquareCheckpointId.CORNER_DETAIL, "corner_calc", r"1\times 1=1"),
        (CompletingSquareCheckpointId.BALANCE_AND_COMPLETE, "eq_rhs_corner", "999"),
        (CompletingSquareCheckpointId.FACTOR_SQUARE, "eq_factor", "(x+99)^2"),
        (CompletingSquareCheckpointId.SOLVE_ROOTS, "root_negative", "-999"),
    )
    for checkpoint_id, suffix, wrong_latex in mutations:
        checkpoint = _blueprint(problem, checkpoint_id)
        node = _result_node(checkpoint, suffix)
        assert isinstance(node, LatexTokenSceneNode)
        patch, result = _replace_result_put(
            checkpoint,
            node.model_copy(update={"latex": wrong_latex}),
        )
        with pytest.raises(ParametricCompletingSquareVerificationError, match="exact LaTeX"):
            _verify(problem, checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("checkpoint_id", "suffix", "wrong_latex"),
    [
        (CompletingSquareCheckpointId.PROBLEM, "eq_rhs", "999"),
        (CompletingSquareCheckpointId.REARRANGE_HALVES, "corner_dim_h", "9"),
        (CompletingSquareCheckpointId.MISSING_CORNER, "corner_area", "16"),
        (CompletingSquareCheckpointId.BALANCE_AND_COMPLETE, "eq_corner_value", "15"),
        (CompletingSquareCheckpointId.BALANCE_AND_COMPLETE, "eq_completed_rhs", "35=6^2"),
        (CompletingSquareCheckpointId.SOLVE_ROOTS, "root_pm", r"\pm 5"),
        (CompletingSquareCheckpointId.SOLVE_ROOTS, "root_positive", "3"),
    ],
)
def test_every_visible_derived_fact_has_a_mutation_guard(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    wrong_latex: str,
) -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, checkpoint_id)
    node = _result_node(checkpoint, suffix)
    assert isinstance(node, LatexTokenSceneNode)
    patch, result = _replace_result_put(
        checkpoint,
        node.model_copy(update={"latex": wrong_latex}),
    )

    with pytest.raises(ParametricCompletingSquareVerificationError, match="exact LaTeX"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("source", "claimed"),
    [
        (_problem(4, 6), _problem(4, 7)),
        (_problem(3, 4), _problem(4, 5)),
    ],
    ids=("same-half-different-rhs", "different-half"),
)
def test_cross_problem_scene_transplant_is_rejected(
    source: CompletingSquareProblemSpecV1,
    claimed: CompletingSquareProblemSpecV1,
) -> None:
    checkpoint = _blueprint(source, CompletingSquareCheckpointId.SOLVE_ROOTS)

    with pytest.raises(
        ParametricCompletingSquareVerificationError,
        match=r"derived fact|exact LaTeX",
    ):
        _verify(claimed, checkpoint)


def test_frontier_problem_transplant_and_stray_owned_node_fail_closed() -> None:
    source = _problem(4, 6)
    claimed = _problem(3, 4)
    source_state = ParametricCompletingSquareStateV1(
        id="lesson",
        problemSpec=source,
        lastMainCheckpoint=CompletingSquareMainCheckpoint.SOLVE_ROOTS,
    )
    claimed_state = source_state.model_copy(update={"problem_spec": claimed})
    scene = SceneState(revision=8, nodes=materialize_parametric_nodes(source_state))
    with pytest.raises(ParametricCompletingSquareVerificationError, match="exact LaTeX"):
        verify_parametric_completing_square_frontier(claimed_state, scene)

    empty = ParametricCompletingSquareStateV1(id="lesson", problemSpec=source)
    stray_scene = SceneState(revision=8, nodes=(scene.nodes[0],))
    with pytest.raises(ParametricCompletingSquareVerificationError, match="must own no"):
        verify_parametric_completing_square_frontier(empty, stray_scene)


def test_unvalidated_problem_cannot_bypass_independent_domain_checks() -> None:
    valid = _problem(4, 6)
    checkpoint = _blueprint(valid, CompletingSquareCheckpointId.PROBLEM)
    invalid = CompletingSquareProblemSpecV1.model_construct(
        v=1,
        linear_coefficient=3,
        right_hand_side=20,
    )

    with pytest.raises(ParametricCompletingSquareVerificationError, match="even domain"):
        _verify(invalid, checkpoint)


def test_caption_facts_and_authored_timing_are_independently_enforced() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.BALANCE_AND_COMPLETE)
    wrong_caption = "Add 15 to both sides and obtain 35."
    patch = checkpoint.patch.model_copy(update={"narration": wrong_caption})
    presentation = checkpoint.presentation.model_copy(
        update={"checkpoint_narration": wrong_caption}
    )
    with pytest.raises(ParametricCompletingSquareVerificationError, match="derived fact"):
        _verify(problem, checkpoint, patch=patch, presentation=presentation)

    phase = checkpoint.choreography.phase.model_copy(
        update={"duration_ms": checkpoint.choreography.phase.duration_ms + 1}
    )
    choreography = checkpoint.choreography.model_copy(update={"phase": phase})
    with pytest.raises(ParametricCompletingSquareVerificationError, match="authored timing"):
        _verify(problem, checkpoint, choreography=choreography)


def test_mute_first_board_carries_dimensions_calculation_balance_and_roots() -> None:
    problem = _problem(4, 6)
    missing = _blueprint(problem, CompletingSquareCheckpointId.MISSING_CORNER)
    detail = _blueprint(problem, CompletingSquareCheckpointId.CORNER_DETAIL)
    balance = _blueprint(problem, CompletingSquareCheckpointId.BALANCE_AND_COMPLETE)
    solved = _blueprint(problem, CompletingSquareCheckpointId.SOLVE_ROOTS)

    def latex(checkpoint: ParametricCheckpointBlueprint, suffix: str) -> str:
        node = _result_node(checkpoint, suffix)
        assert isinstance(node, LatexTokenSceneNode)
        return node.latex

    assert (latex(missing, "corner_dim_h"), latex(missing, "corner_dim_v")) == ("4", "4")
    assert latex(missing, "corner_area") == "?"
    assert latex(detail, "corner_calc") == r"4\times 4=4^2=16"
    assert latex(balance, "eq_completed_rhs") == "36=6^2"
    assert (latex(solved, "root_positive"), latex(solved, "root_negative")) == ("2", "-10")
    assert latex(solved, "scale_note") == r"\text{not to scale}"


def test_geometry_rejects_unequal_strips_wrong_gap_corner_and_dimensions() -> None:
    problem = _problem(4, 6)
    area = _blueprint(problem, CompletingSquareCheckpointId.AREA_MODEL)
    strip = _result_node(area, "strip_b")
    assert isinstance(strip, PathSceneNode)
    unequal = strip.model_copy(
        update={
            "points": (
                strip.points[0],
                strip.points[1],
                (strip.points[2][0], strip.points[2][1] + 8.0),
                (strip.points[3][0], strip.points[3][1] + 8.0),
            )
        }
    )
    patch, result = _replace_result_put(area, unequal)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="congruent"):
        _verify(problem, area, patch=patch, result_scene=result)

    rearranged = _blueprint(problem, CompletingSquareCheckpointId.REARRANGE_HALVES)
    strip = _result_node(rearranged, "strip_a")
    assert isinstance(strip, PathSceneNode)
    overlapping = strip.model_copy(update={"points": tuple((x, y - 10.0) for x, y in strip.points)})
    patch, result = _replace_result_put(rearranged, overlapping)
    with pytest.raises(
        ParametricCompletingSquareVerificationError,
        match=r"collision|attach|gap|fully contained",
    ):
        _verify(problem, rearranged, patch=patch, result_scene=result)

    missing = _blueprint(problem, CompletingSquareCheckpointId.MISSING_CORNER)
    corner = _result_node(missing, "corner")
    assert isinstance(corner, PathSceneNode)
    left, top = corner.points[0]
    wrong_corner = corner.model_copy(
        update={
            "points": (
                (left, top),
                (left + 45.0, top),
                (left + 45.0, top + 45.0),
                (left, top + 45.0),
            )
        }
    )
    patch, result = _replace_result_put(missing, wrong_corner)
    with pytest.raises(ParametricCompletingSquareVerificationError, match=r"thickness|gap"):
        _verify(problem, missing, patch=patch, result_scene=result)

    dimension = _result_node(rearranged, "corner_dim_h")
    assert isinstance(dimension, LatexTokenSceneNode)
    displaced = dimension.model_copy(update={"x": 650.0})
    patch, result = _replace_result_put(rearranged, displaced)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="align"):
        _verify(problem, rearranged, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("node_kind", "attribute", "value", "message"),
    [
        ("token", "style.opacity", 0.0, "remain visible"),
        ("token", "width", 1.0, "width is too small"),
        ("token", "height", 1.0, "height is too small"),
        ("token", "style.font_size", 80.0, "canonical visible token style"),
        ("token", "presentation.enter", "none", "canonical token presentation"),
        ("token", "presentation.exit", "none", "canonical token presentation"),
        ("token", "anchor", "start", "middle token anchor"),
        ("token", "style.color", "#FFFFFF", "canonical visible token style"),
        ("path", "style.opacity", 0.0, "remain visible"),
        ("path", "presentation.enter", "none", "canonical path presentation"),
        ("path", "presentation.exit", "none", "canonical path presentation"),
        ("path", "style.stroke", "#FFFFFF", "canonical visible path style"),
        ("path", "style.fill", "transparent", "canonical visible path style"),
    ],
    ids=[
        "token-opacity-zero",
        "token-tiny-width",
        "token-tiny-height",
        "token-oversized-font",
        "token-enter-none",
        "token-exit-none",
        "token-wrong-anchor",
        "token-wrong-paint",
        "path-opacity-zero",
        "path-enter-none",
        "path-exit-none",
        "path-wrong-stroke",
        "path-invisible-fill",
    ],
)
def test_visual_contract_rejects_invisible_unmeasured_or_noncanonical_nodes(
    node_kind: str,
    attribute: str,
    value: object,
    message: str,
) -> None:
    problem = _problem(4, 6)
    checkpoint_id = (
        CompletingSquareCheckpointId.PROBLEM
        if node_kind == "token"
        else CompletingSquareCheckpointId.AREA_MODEL
    )
    suffix = "eq_square" if node_kind == "token" else "x2_square"
    checkpoint = _blueprint(problem, checkpoint_id)
    node = _result_node(checkpoint, suffix)
    container, separator, member = attribute.partition(".")
    if not separator:
        replacement = node.model_copy(update={container: value})
    else:
        nested = getattr(node, container).model_copy(update={member: value})
        replacement = node.model_copy(update={container: nested})
    patch, result = _replace_result_put(checkpoint, replacement)

    with pytest.raises(ParametricCompletingSquareVerificationError, match=message):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("problem", "checkpoint_id", "suffix", "old_width"),
    [
        (_problem(4, 6), CompletingSquareCheckpointId.FACTOR_SQUARE, "eq_factor", 94.0),
        (
            _problem(4, 6),
            CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
            "eq_completed_rhs",
            81.0,
        ),
        (
            _problem(1, 2),
            CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
            "eq_completed_rhs",
            68.0,
        ),
        (_problem(4, 6), CompletingSquareCheckpointId.SOLVE_ROOTS, "root_lhs", 55.0),
        (_problem(4, 6), CompletingSquareCheckpointId.SOLVE_ROOTS, "root_negative", 55.0),
        (_problem(1, 2), CompletingSquareCheckpointId.SOLVE_ROOTS, "root_negative", 42.0),
        (_problem(8, 9), CompletingSquareCheckpointId.PROBLEM, "eq_linear", 55.0),
    ],
    ids=[
        "factor",
        "two-digit-completed-identity",
        "one-digit-completed-identity",
        "root-lhs",
        "two-digit-negative-root",
        "one-digit-negative-root",
        "two-digit-linear-term",
    ],
)
def test_visual_contract_rejects_the_old_cropping_widths(
    problem: CompletingSquareProblemSpecV1,
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    old_width: float,
) -> None:
    checkpoint = _blueprint(problem, checkpoint_id)
    node = _result_node(checkpoint, suffix)
    assert isinstance(node, LatexTokenSceneNode)
    assert node.width > old_width
    for rejected_width in (old_width, node.width - 1.0):
        patch, result = _replace_result_put(
            checkpoint,
            node.model_copy(update={"width": rejected_width}),
        )
        with pytest.raises(
            ParametricCompletingSquareVerificationError,
            match="width is too small",
        ):
            _verify(problem, checkpoint, patch=patch, result_scene=result)


def test_visual_contract_rejects_the_old_cropping_equation_height() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.FACTOR_SQUARE)
    factor = _result_node(checkpoint, "eq_factor")
    assert isinstance(factor, LatexTokenSceneNode)
    assert factor.height == 48.0 > 42.0
    patch, result = _replace_result_put(checkpoint, factor.model_copy(update={"height": 42.0}))

    with pytest.raises(ParametricCompletingSquareVerificationError, match="height is too small"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize("problem", PROBLEMS, ids=_problem_id)
@pytest.mark.parametrize("checkpoint_id", tuple(CompletingSquareCheckpointId))
def test_every_emitted_token_rejects_canonical_width_minus_one(
    problem: CompletingSquareProblemSpecV1,
    checkpoint_id: CompletingSquareCheckpointId,
) -> None:
    checkpoint = _blueprint(problem, checkpoint_id)
    canonical_scene = SceneState(revision=21, nodes=checkpoint.result_nodes)
    verify_parametric_completing_square_frontier(checkpoint.result_component, canonical_scene)

    for target in checkpoint.result_nodes:
        if not isinstance(target, LatexTokenSceneNode):
            continue
        narrowed = target.model_copy(update={"width": target.width - 1.0})
        mutated_scene = SceneState(
            revision=21,
            nodes=tuple(
                narrowed if node.id == target.id else node for node in checkpoint.result_nodes
            ),
        )
        with pytest.raises(
            ParametricCompletingSquareVerificationError,
            match=rf"{target.id.removeprefix('lesson__')} token width is too small",
        ):
            verify_parametric_completing_square_frontier(
                checkpoint.result_component,
                mutated_scene,
            )


def test_rectangle_paths_must_follow_perimeter_instead_of_bow_tie_order() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.AREA_MODEL)
    square = _result_node(checkpoint, "x2_square")
    assert isinstance(square, PathSceneNode)
    bow_tie = square.model_copy(
        update={
            "points": (
                square.points[0],
                square.points[2],
                square.points[1],
                square.points[3],
            )
        }
    )
    patch, result = _replace_result_put(checkpoint, bow_tie)

    with pytest.raises(ParametricCompletingSquareVerificationError, match="perimeter edge order"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("checkpoint_id", "suffix", "x"),
    [
        (CompletingSquareCheckpointId.AREA_MODEL, "area_half_b", 431.0),
        (CompletingSquareCheckpointId.MISSING_CORNER, "corner_area", 421.0),
    ],
)
def test_area_labels_require_full_box_containment_not_just_center(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    x: float,
) -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, checkpoint_id)
    label = _result_node(checkpoint, suffix)
    assert isinstance(label, LatexTokenSceneNode)
    patch, result = _replace_result_put(checkpoint, label.model_copy(update={"x": x}))

    with pytest.raises(ParametricCompletingSquareVerificationError, match="fully contained"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


def test_scale_note_and_all_token_collisions_fail_closed() -> None:
    problem = _problem(4, 6)
    area = _blueprint(problem, CompletingSquareCheckpointId.AREA_MODEL)
    note = _result_node(area, "scale_note")
    assert isinstance(note, LatexTokenSceneNode)
    patch, result = _replace_result_put(area, note.model_copy(update={"latex": ""}))
    with pytest.raises(ParametricCompletingSquareVerificationError, match="exact LaTeX"):
        _verify(problem, area, patch=patch, result_scene=result)

    split = _blueprint(problem, CompletingSquareCheckpointId.SPLIT_LINEAR_TERM)
    calculation = _result_node(split, "half_calc")
    half = _result_node(split, "eq_half_a")
    assert isinstance(calculation, LatexTokenSceneNode)
    assert isinstance(half, LatexTokenSceneNode)
    collision = calculation.model_copy(update={"x": half.x, "y": half.y})
    patch, result = _replace_result_put(split, collision)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="collision"):
        _verify(problem, split, patch=patch, result_scene=result)


def test_transform_must_preserve_kind_topology_winding_and_vertex_correspondence() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.REARRANGE_HALVES)
    path = _result_node(checkpoint, "strip_a")
    assert isinstance(path, PathSceneNode)
    left = min(point[0] for point in path.points)
    top = min(point[1] for point in path.points)
    replacement = RectSceneNode(
        id=path.id,
        kind="rect",
        presentation=path.presentation,
        x=left,
        y=top,
        width=max(point[0] for point in path.points) - left,
        height=max(point[1] for point in path.points) - top,
        style=path.style,
    )
    patch, result = _replace_result_put(checkpoint, replacement)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="changes node kind"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)

    reordered = path.model_copy(update={"points": path.points[2:] + path.points[:2]})
    patch, result = _replace_result_put(checkpoint, reordered)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="vertex correspondence"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)

    reversed_path = path.model_copy(update={"points": tuple(reversed(path.points))})
    patch, result = _replace_result_put(checkpoint, reversed_path)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="winding"):
        _verify(problem, checkpoint, patch=patch, result_scene=result)


def test_cues_and_viewports_must_track_exact_visible_semantics() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.PROBLEM)
    phase = checkpoint.choreography.phase
    cues = tuple(
        FocusCueV1(target_ids=("lesson__eq_rhs",)) if cue.cue == "focus" else cue
        for cue in phase.cues
    )
    wrong_focus = ChoreographyPlanV1(
        phase=ChoreographyPhaseV1(
            cues=cues,
            duration_ms=phase.duration_ms,
            easing=phase.easing,
            hold_after_ms=phase.hold_after_ms,
        )
    )
    with pytest.raises(ParametricCompletingSquareVerificationError, match="mathematical subject"):
        _verify(problem, checkpoint, choreography=wrong_focus)

    clipped_pose = ViewportPoseV1(x=0.0, y=0.0, width=160.0, height=90.0)
    clipped_map = LayoutViewportMapV1(
        cinematic=clipped_pose,
        compact=checkpoint.presentation.result_viewports.compact,
    )
    clipped_presentation = checkpoint.presentation.model_copy(
        update={"result_viewports": clipped_map}
    )
    with pytest.raises(ParametricCompletingSquareVerificationError, match="safe viewport padding"):
        _verify(problem, checkpoint, presentation=clipped_presentation)

    missing = _blueprint(problem, CompletingSquareCheckpointId.MISSING_CORNER)
    phase = missing.choreography.phase
    cues = tuple(
        EmphasizeCueV1(target_ids=("lesson__corner_area",)) if cue.cue == "emphasize" else cue
        for cue in phase.cues
    )
    wrong_emphasis = ChoreographyPlanV1(
        phase=ChoreographyPhaseV1(
            cues=cues,
            duration_ms=phase.duration_ms,
            easing=phase.easing,
            hold_after_ms=phase.hold_after_ms,
        )
    )
    with pytest.raises(ParametricCompletingSquareVerificationError, match="semantic emphasis"):
        _verify(problem, missing, choreography=wrong_emphasis)

    solved = _blueprint(problem, CompletingSquareCheckpointId.SOLVE_ROOTS)
    clipped_solve_pose = ViewportPoseV1(x=0.0, y=75.0, width=800.0, height=450.0)
    clipped_solve_map = LayoutViewportMapV1(
        cinematic=clipped_solve_pose,
        compact=solved.presentation.result_viewports.compact,
    )
    clipped_solve_presentation = solved.presentation.model_copy(
        update={"result_viewports": clipped_solve_map}
    )
    with pytest.raises(
        ParametricCompletingSquareVerificationError,
        match=r"result viewport subject .*clipped",
    ):
        _verify(problem, solved, presentation=clipped_solve_presentation)


@pytest.mark.parametrize(
    ("scene_side", "layout"),
    [
        ("base", "cinematic"),
        ("base", "compact"),
        ("result", "cinematic"),
        ("result", "compact"),
    ],
)
def test_each_viewport_side_and_layout_contains_non_focus_checkpoint_facts(
    scene_side: str,
    layout: str,
) -> None:
    problem = _problem(4, 6)
    checkpoint_id = (
        CompletingSquareCheckpointId.AREA_MODEL
        if scene_side == "base"
        else CompletingSquareCheckpointId.PROBLEM
    )
    checkpoint = _blueprint(problem, checkpoint_id)
    viewport_field = f"{scene_side}_viewports"
    viewports = getattr(checkpoint.presentation, viewport_field)
    clipped_pose = ViewportPoseV1(x=90.0, y=60.0, width=400.0, height=170.0)
    clipped_map = viewports.model_copy(update={layout: clipped_pose})
    presentation = checkpoint.presentation.model_copy(update={viewport_field: clipped_map})

    with pytest.raises(
        ParametricCompletingSquareVerificationError,
        match=rf"{scene_side} viewport subject 'lesson__eq_rhs'.*clipped by {layout}",
    ):
        _verify(problem, checkpoint, presentation=presentation)


def test_missing_corner_viewport_requires_the_contextual_horizontal_strip_label() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.MISSING_CORNER)
    old_right_shifted = ViewportPoseV1(x=360.0, y=370.0, width=320.0, height=190.0)
    viewports = checkpoint.presentation.result_viewports.model_copy(
        update={"cinematic": old_right_shifted}
    )
    presentation = checkpoint.presentation.model_copy(update={"result_viewports": viewports})

    with pytest.raises(
        ParametricCompletingSquareVerificationError,
        match=r"area_half_a.*clipped by cinematic",
    ):
        _verify(problem, checkpoint, presentation=presentation)


def test_duplicate_ids_and_board_clipping_fail_even_for_unvalidated_scenes() -> None:
    problem = _problem(4, 6)
    area = _blueprint(problem, CompletingSquareCheckpointId.AREA_MODEL)
    duplicate_base = SceneState.model_construct(
        revision=20,
        nodes=(area.base_nodes[0], area.base_nodes[0]),
    )
    with pytest.raises(ParametricCompletingSquareVerificationError, match="must be unique"):
        _verify(problem, area, base_scene=duplicate_base)

    problem_checkpoint = _blueprint(problem, CompletingSquareCheckpointId.PROBLEM)
    token = _result_node(problem_checkpoint, "eq_square")
    assert isinstance(token, LatexTokenSceneNode)
    clipped = token.model_copy(update={"x": 0.0})
    patch, result = _replace_result_put(problem_checkpoint, clipped)
    with pytest.raises(ParametricCompletingSquareVerificationError, match="clipped"):
        _verify(problem, problem_checkpoint, patch=patch, result_scene=result)


def test_patch_contract_failures_are_local_and_deterministic() -> None:
    problem = _problem(4, 6)
    checkpoint = _blueprint(problem, CompletingSquareCheckpointId.PROBLEM)
    base, result = _scenes(checkpoint)
    reversed_patch = checkpoint.patch.model_copy(
        update={"operations": tuple(reversed(checkpoint.patch.operations))}
    )
    first = checkpoint.patch.operations[0]
    assert isinstance(first, PutSceneOperation)
    foreign_node = first.node.model_copy(update={"id": "foreign__eq"})
    foreign_patch = checkpoint.patch.model_copy(
        update={
            "operations": (
                first.model_copy(update={"node": foreign_node}),
                *checkpoint.patch.operations[1:],
            )
        }
    )
    mutations: tuple[Callable[[], object], ...] = (
        lambda: _verify(
            problem,
            checkpoint,
            result_scene=result.model_copy(update={"revision": 22}),
        ),
        lambda: _verify(problem, checkpoint, patch=reversed_patch),
        lambda: _verify(problem, checkpoint, patch=foreign_patch),
        lambda: _verify(
            problem,
            checkpoint,
            result_scene=result.model_copy(update={"nodes": result.nodes[:-1]}),
        ),
        lambda: verify_parametric_completing_square_checkpoint(
            "lesson",
            problem,
            CompletingSquareCheckpointId.AREA_MODEL,
            base,
            result,
            checkpoint.patch,
            checkpoint.presentation,
            checkpoint.choreography,
        ),
    )
    for mutation in mutations:
        with pytest.raises(ParametricCompletingSquareVerificationError):
            mutation()


def test_complete_main_path_is_exactly_47_seconds() -> None:
    checkpoints = _main_blueprints(_problem(4, 6))

    assert tuple(checkpoint.checkpoint_id for checkpoint in checkpoints) == tuple(
        CompletingSquareCheckpointId(checkpoint.value)
        for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER
    )
    assert sum(checkpoint.choreography.phase.total_ms for checkpoint in checkpoints) == 47_000

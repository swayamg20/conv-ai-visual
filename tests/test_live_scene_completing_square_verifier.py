from __future__ import annotations

import ast
import inspect
from collections.abc import Callable

import pytest
from murmur.live_scene import completing_square_compiler, completing_square_verifier
from murmur.live_scene.choreography_contracts import (
    ChoreographyPhaseV1,
    ChoreographyPlanV1,
    FocusCueV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
    ViewportPoseV1,
)
from murmur.live_scene.completing_square_compiler import CompiledCheckpointBlueprint
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.completing_square_verifier import (
    CompletingSquareVerificationError,
    verify_completing_square_checkpoint,
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
from pydantic import ValidationError


def _advance_beat() -> RoutedChoreographyBeatV2:
    return RoutedChoreographyBeatV2.model_validate(
        {
            "beatId": "beat-solve",
            "componentId": "lesson",
            "route": {"intent": "advance", "targetStage": "solve"},
        }
    )


def _clarify_beat() -> RoutedChoreographyBeatV2:
    return RoutedChoreographyBeatV2.model_validate(
        {
            "beatId": "beat-corner-detail",
            "componentId": "lesson",
            "route": {"intent": "clarify_corner"},
        }
    )


def _main_blueprints() -> tuple[CompiledCheckpointBlueprint, ...]:
    return completing_square_compiler._compile_blueprints(
        _advance_beat(),
        None,
    ).checkpoints


def _detail_blueprint() -> CompiledCheckpointBlueprint:
    state = CompletingSquareState(
        id="lesson",
        last_main_checkpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
    )
    return completing_square_compiler._compile_blueprints(
        _clarify_beat(),
        state,
    ).checkpoints[0]


def _blueprint(checkpoint_id: CompletingSquareCheckpointId) -> CompiledCheckpointBlueprint:
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        return _detail_blueprint()
    return next(
        checkpoint for checkpoint in _main_blueprints() if checkpoint.checkpoint_id is checkpoint_id
    )


def _scenes(
    checkpoint: CompiledCheckpointBlueprint,
    *,
    base_nodes: tuple[SceneNode, ...] | None = None,
    result_nodes: tuple[SceneNode, ...] | None = None,
) -> tuple[SceneState, SceneState]:
    base = checkpoint.base_nodes if base_nodes is None else base_nodes
    result = checkpoint.result_nodes if result_nodes is None else result_nodes
    return SceneState(revision=20, nodes=base), SceneState(revision=21, nodes=result)


def _verify(
    checkpoint: CompiledCheckpointBlueprint,
    *,
    base_scene: SceneState | None = None,
    result_scene: SceneState | None = None,
    patch: ScenePatchDraft | None = None,
    choreography: ChoreographyPlanV1 | None = None,
    presentation: PresentationCheckpointV1 | None = None,
):
    default_base, default_result = _scenes(checkpoint)
    return verify_completing_square_checkpoint(
        "lesson",
        checkpoint.checkpoint_id,
        default_base if base_scene is None else base_scene,
        default_result if result_scene is None else result_scene,
        checkpoint.patch if patch is None else patch,
        checkpoint.presentation if presentation is None else presentation,
        checkpoint.choreography if choreography is None else choreography,
    )


def _replace_result_put(
    checkpoint: CompiledCheckpointBlueprint,
    replacement: SceneNode,
) -> tuple[ScenePatchDraft, SceneState]:
    operations = tuple(
        operation.model_copy(update={"node": replacement})
        if isinstance(operation, PutSceneOperation) and operation.target_id == replacement.id
        else operation
        for operation in checkpoint.patch.operations
    )
    assert any(
        isinstance(operation, PutSceneOperation) and operation.target_id == replacement.id
        for operation in checkpoint.patch.operations
    )
    result_nodes = tuple(
        replacement if node.id == replacement.id else node for node in checkpoint.result_nodes
    )
    return (
        checkpoint.patch.model_copy(update={"operations": operations}),
        SceneState.model_construct(revision=21, nodes=result_nodes),
    )


def _result_node(
    checkpoint: CompiledCheckpointBlueprint,
    suffix: str,
    expected_type: type[SceneNode],
) -> SceneNode:
    node = next(node for node in checkpoint.result_nodes if node.id == f"lesson__{suffix}")
    assert isinstance(node, expected_type)
    return node


@pytest.mark.parametrize("checkpoint_id", tuple(CompletingSquareCheckpointId))
def test_every_compiler_blueprint_passes_independent_verification(
    checkpoint_id: CompletingSquareCheckpointId,
) -> None:
    checkpoint = _blueprint(checkpoint_id)

    receipt = _verify(checkpoint)

    assert receipt.component_id == "lesson"
    assert receipt.checkpoint_id is checkpoint_id
    assert receipt.operation_targets == tuple(
        operation.target_id for operation in checkpoint.patch.operations
    )
    assert tuple(receipt.obligation_codes) == tuple(
        code for code in type(receipt.obligation_codes[0]) if code in receipt.obligation_codes
    )
    with pytest.raises(ValidationError, match="frozen"):
        receipt.verified = False  # type: ignore[misc]


def test_obligations_accumulate_through_the_mathematical_argument() -> None:
    problem = _verify(_blueprint(CompletingSquareCheckpointId.PROBLEM))
    detail = _verify(_blueprint(CompletingSquareCheckpointId.CORNER_DETAIL))
    solved = _verify(_blueprint(CompletingSquareCheckpointId.SOLVE_ROOTS))

    assert {code.value for code in problem.obligation_codes} == {
        "stable_id",
        "unique_ids",
        "board_bounds",
        "component_ownership",
        "patch_materialization",
        "compatible_morph",
        "viewport_containment",
        "equation_identity",
    }
    assert "missing_corner" in detail.obligation_codes
    assert "balanced_completion" not in detail.obligation_codes
    assert len(solved.obligation_codes) == 16


def test_verifier_imports_no_compiler_or_compiler_expected_table() -> None:
    tree = ast.parse(inspect.getsource(completing_square_verifier))
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

    assert all("compiler" not in module for module in imports)
    assert not any(
        name.startswith("_EXPECTED_CHECKPOINT") for name in vars(completing_square_verifier)
    )


def test_patch_must_exactly_materialize_the_declared_ordered_result() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.PROBLEM)
    _, result = _scenes(checkpoint)
    truncated = result.model_copy(update={"nodes": result.nodes[:-1]})

    with pytest.raises(CompletingSquareVerificationError, match="exactly materialize"):
        _verify(checkpoint, result_scene=truncated)


def test_patch_targets_must_be_canonical_and_component_owned() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.PROBLEM)
    reversed_patch = checkpoint.patch.model_copy(
        update={"operations": tuple(reversed(checkpoint.patch.operations))}
    )
    with pytest.raises(CompletingSquareVerificationError, match="lexical order"):
        _verify(checkpoint, patch=reversed_patch)

    first = checkpoint.patch.operations[0]
    assert isinstance(first, PutSceneOperation)
    foreign_node = first.node.model_copy(update={"id": "Aforeign"})
    foreign_operation = first.model_copy(update={"node": foreign_node})
    foreign_patch = checkpoint.patch.model_copy(
        update={"operations": (foreign_operation, *checkpoint.patch.operations[1:])}
    )
    with pytest.raises(CompletingSquareVerificationError, match="foreign component"):
        _verify(checkpoint, patch=foreign_patch)


def test_duplicate_ids_and_render_bounds_fail_closed_even_for_unvalidated_copies() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.PROBLEM)
    duplicate_base = SceneState.model_construct(
        revision=20,
        nodes=(checkpoint.result_nodes[0], checkpoint.result_nodes[0]),
    )
    with pytest.raises(CompletingSquareVerificationError, match="must be unique"):
        _verify(checkpoint, base_scene=duplicate_base)

    token = _result_node(checkpoint, "eq_x2", LatexTokenSceneNode)
    assert isinstance(token, LatexTokenSceneNode)
    clipped = token.model_copy(update={"x": 0.0})
    patch, result = _replace_result_put(checkpoint, clipped)
    with pytest.raises(CompletingSquareVerificationError, match="clipped"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_area_model_rejects_unequal_3x_strips() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.AREA_MODEL)
    strip = _result_node(checkpoint, "strip_b", PathSceneNode)
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
    patch, result = _replace_result_put(checkpoint, unequal)

    with pytest.raises(CompletingSquareVerificationError, match="congruent"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_area_labels_must_remain_associated_with_their_shapes() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.AREA_MODEL)
    label = _result_node(checkpoint, "area_3x_a", LatexTokenSceneNode)
    assert isinstance(label, LatexTokenSceneNode)
    displaced = label.model_copy(update={"x": 680.0})
    patch, result = _replace_result_put(checkpoint, displaced)

    with pytest.raises(CompletingSquareVerificationError, match="associated"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_rearrangement_rejects_overlap_and_wrong_gap() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.REARRANGE_HALVES)
    strip = _result_node(checkpoint, "strip_a", PathSceneNode)
    assert isinstance(strip, PathSceneNode)
    overlapping = strip.model_copy(update={"points": tuple((x, y - 10.0) for x, y in strip.points)})
    patch, result = _replace_result_put(checkpoint, overlapping)

    with pytest.raises(CompletingSquareVerificationError, match=r"collision|attach|gap"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_missing_corner_must_be_square_and_match_strip_thickness() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.MISSING_CORNER)
    corner = _result_node(checkpoint, "corner", PathSceneNode)
    assert isinstance(corner, PathSceneNode)
    left, top = corner.points[0]
    wrong = corner.model_copy(
        update={
            "points": (
                (left, top),
                (left + 50.0, top),
                (left + 50.0, top + 50.0),
                (left, top + 50.0),
            )
        }
    )
    patch, result = _replace_result_put(checkpoint, wrong)

    with pytest.raises(CompletingSquareVerificationError, match=r"strip thickness|gap"):
        _verify(checkpoint, patch=patch, result_scene=result)


@pytest.mark.parametrize(
    ("checkpoint_id", "suffix", "wrong_latex", "message"),
    [
        (CompletingSquareCheckpointId.BALANCE_AND_COMPLETE, "eq_rhs9", "8", "exact LaTeX"),
        (CompletingSquareCheckpointId.FACTOR_SQUARE, "eq_factor", "(x+4)^2", "exact LaTeX"),
        (CompletingSquareCheckpointId.SOLVE_ROOTS, "root_neg7", "-6", "exact LaTeX"),
    ],
)
def test_wrong_balance_factorization_or_root_is_rejected(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    wrong_latex: str,
    message: str,
) -> None:
    checkpoint = _blueprint(checkpoint_id)
    token = _result_node(checkpoint, suffix, LatexTokenSceneNode)
    assert isinstance(token, LatexTokenSceneNode)
    wrong = token.model_copy(update={"latex": wrong_latex})
    patch, result = _replace_result_put(checkpoint, wrong)

    with pytest.raises(CompletingSquareVerificationError, match=message):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_solution_requires_an_honest_geometry_domain_statement() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.SOLVE_ROOTS)
    narration = "The area picture proves both signed roots directly."
    patch = checkpoint.patch.model_copy(update={"narration": narration})
    presentation = checkpoint.presentation.model_copy(update={"checkpoint_narration": narration})

    with pytest.raises(CompletingSquareVerificationError, match="geometry domain honestly"):
        _verify(checkpoint, patch=patch, presentation=presentation)


def test_corner_detail_rejects_annotation_collision() -> None:
    checkpoint = _detail_blueprint()
    dimension = _result_node(checkpoint, "corner_dim_v", LatexTokenSceneNode)
    calculation = _result_node(checkpoint, "corner_calc", LatexTokenSceneNode)
    assert isinstance(dimension, LatexTokenSceneNode)
    assert isinstance(calculation, LatexTokenSceneNode)
    overlapping = calculation.model_copy(update={"x": dimension.x, "y": dimension.y})
    patch, result = _replace_result_put(checkpoint, overlapping)

    with pytest.raises(CompletingSquareVerificationError, match="collision"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_focus_targets_must_be_exact_and_safely_visible_in_both_layouts() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.PROBLEM)
    phase = checkpoint.choreography.phase
    cues = tuple(
        FocusCueV1(target_ids=("lesson__eq_rhs7",)) if cue.cue == "focus" else cue
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
    with pytest.raises(CompletingSquareVerificationError, match="mathematical subject"):
        _verify(checkpoint, choreography=wrong_focus)

    clipped_pose = ViewportPoseV1(x=0.0, y=0.0, width=160.0, height=90.0)
    clipped_map = LayoutViewportMapV1(
        cinematic=clipped_pose,
        compact=checkpoint.presentation.result_viewports.compact,
    )
    clipped_presentation = checkpoint.presentation.model_copy(
        update={"result_viewports": clipped_map}
    )
    with pytest.raises(CompletingSquareVerificationError, match="safe viewport padding"):
        _verify(checkpoint, presentation=clipped_presentation)


def test_transform_requires_same_kind_and_path_topology() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.REARRANGE_HALVES)
    path = _result_node(checkpoint, "strip_a", PathSceneNode)
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

    with pytest.raises(CompletingSquareVerificationError, match="changes node kind"):
        _verify(checkpoint, patch=patch, result_scene=result)

    reordered = path.model_copy(update={"points": path.points[2:] + path.points[:2]})
    patch, result = _replace_result_put(checkpoint, reordered)
    with pytest.raises(CompletingSquareVerificationError, match="vertex correspondence"):
        _verify(checkpoint, patch=patch, result_scene=result)


def test_mutation_matrix_keeps_all_rejections_local_and_deterministic() -> None:
    checkpoint = _blueprint(CompletingSquareCheckpointId.PROBLEM)
    base, result = _scenes(checkpoint)
    mutations: tuple[tuple[str, Callable[[], object]], ...] = (
        (
            "revision",
            lambda: verify_completing_square_checkpoint(
                "lesson",
                checkpoint.checkpoint_id,
                base,
                result.model_copy(update={"revision": 22}),
                checkpoint.patch,
                checkpoint.presentation,
                checkpoint.choreography,
            ),
        ),
        (
            "checkpoint",
            lambda: verify_completing_square_checkpoint(
                "lesson",
                CompletingSquareCheckpointId.AREA_MODEL,
                base,
                result,
                checkpoint.patch,
                checkpoint.presentation,
                checkpoint.choreography,
            ),
        ),
    )

    for _name, mutation in mutations:
        with pytest.raises(CompletingSquareVerificationError):
            mutation()

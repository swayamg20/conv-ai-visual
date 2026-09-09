from __future__ import annotations

from itertools import pairwise

import pytest
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import (
    CompletingSquareStage,
    RoutedChoreographyBeatV3,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
    completing_square_problem_sha256,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexSceneNode,
    LatexTokenSceneNode,
    PathSceneNode,
    SceneNode,
    ScenePatchDraft,
    SceneState,
    TextSceneNode,
)
from murmur.live_scene.parametric_checkpoint_compiler import (
    CompiledParametricCheckpointBeatV3,
    ParametricCheckpointCompilationError,
    compile_parametric_checkpoint_beat,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointCompilerCertificateV3,
    CompiledCheckpointV3,
    checkpoint_certificate_v3_sha256,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    ParametricCompletingSquareCompilationError,
    materialize_parametric_nodes,
)
from murmur.live_scene.semantic_contracts import (
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1
from pydantic import ValidationError

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


def _beat(
    problem: CompletingSquareProblemSpecV1,
    stage: CompletingSquareStage = CompletingSquareStage.SOLVE,
    *,
    beat_id: str = "beat-parametric",
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


def _clarify_beat(problem: CompletingSquareProblemSpecV1) -> RoutedChoreographyBeatV3:
    return RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": "beat-corner",
            "componentKind": "completing_square_parametric",
            "componentId": "lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "clarify_corner"},
        }
    )


def _state(
    problem: CompletingSquareProblemSpecV1,
    checkpoint: CompletingSquareMainCheckpoint | None,
    *,
    clarified: bool = False,
) -> ParametricCompletingSquareStateV1:
    return ParametricCompletingSquareStateV1(
        id="lesson",
        problem_spec=problem,
        last_main_checkpoint=checkpoint,
        corner_clarified=clarified,
    )


def _base(
    problem: CompletingSquareProblemSpecV1,
    prefix_length: int,
    *,
    revision: int = 17,
    certificate_head: str | None = "a" * 64,
) -> tuple[SceneState, SemanticSceneState]:
    if prefix_length == 0:
        return (
            SceneState(revision=revision),
            SemanticSceneState(
                revision=revision,
                certificate_head_sha256=certificate_head,
            ),
        )
    checkpoint = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length - 1]
    component = _state(problem, checkpoint)
    return (
        SceneState(revision=revision, nodes=materialize_parametric_nodes(component)),
        SemanticSceneState(
            revision=revision,
            components=(component,),
            certificate_head_sha256=certificate_head,
        ),
    )


def _compile(
    problem: CompletingSquareProblemSpecV1 | None = None,
    *,
    prefix_length: int = 0,
    revision: int = 17,
    certificate_head: str | None = "a" * 64,
) -> CompiledParametricCheckpointBeatV3:
    problem = problem or _problem()
    base_scene, base_semantic_scene = _base(
        problem,
        prefix_length,
        revision=revision,
        certificate_head=certificate_head,
    )
    return compile_parametric_checkpoint_beat(
        _beat(problem),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )


def _batch_with(
    compiled: CompiledParametricCheckpointBeatV3,
    *,
    checkpoints: tuple[CompiledCheckpointV3, ...] | None = None,
    result_scene: SceneState | None = None,
    result_semantic_scene: SemanticSceneState | None = None,
) -> CompiledParametricCheckpointBeatV3:
    return CompiledParametricCheckpointBeatV3(
        beat=compiled.beat,
        base_scene=compiled.base_scene,
        result_scene=result_scene or compiled.result_scene,
        base_semantic_scene=compiled.base_semantic_scene,
        result_semantic_scene=result_semantic_scene or compiled.result_semantic_scene,
        checkpoints=compiled.checkpoints if checkpoints is None else checkpoints,
    )


def _reissue_certificate(
    checkpoint: CompiledCheckpointV3,
    **body_updates: object,
) -> CompiledCheckpointV3:
    body = checkpoint.certificate.body.model_copy(update=body_updates)
    certificate = CheckpointCompilerCertificateV3(
        body=body,
        certificate_sha256=checkpoint_certificate_v3_sha256(body),
    )
    return CompiledCheckpointV3(
        beat=checkpoint.beat,
        checkpoint_id=checkpoint.checkpoint_id,
        patch=checkpoint.patch,
        receipt=checkpoint.receipt,
        presentation=checkpoint.presentation,
        choreography=checkpoint.choreography,
        certificate=certificate,
    )


@pytest.mark.parametrize(
    ("half", "magnitude", "linear_coefficient", "right_hand_side"),
    SUPPORTED_CASES,
    ids=SUPPORTED_CASE_IDS,
)
@pytest.mark.parametrize("prefix_length", range(9))
def test_all_36_problems_certify_every_main_frontier(
    half: int,
    magnitude: int,
    linear_coefficient: int,
    right_hand_side: int,
    prefix_length: int,
) -> None:
    problem = _problem(linear_coefficient, right_hand_side)
    compiled = _compile(problem, prefix_length=prefix_length)
    expected_ids = tuple(
        CompletingSquareCheckpointId(checkpoint.value)
        for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length:]
    )

    assert problem.half_coefficient == half
    assert problem.square_root_magnitude == magnitude
    assert tuple(checkpoint.checkpoint_id for checkpoint in compiled.checkpoints) == expected_ids
    assert compiled.result_scene.revision == 17 + len(expected_ids)
    assert compiled.result_semantic_scene.revision == compiled.result_scene.revision
    target = next(
        component
        for component in compiled.result_semantic_scene.components
        if component.id == "lesson"
    )
    assert target == _state(problem, CompletingSquareMainCheckpoint.SOLVE_ROOTS)

    problem_digest = completing_square_problem_sha256(problem)
    expected_low_level_hash = low_level_scene_sha256(compiled.base_scene)
    expected_semantic_hash = semantic_scene_sha256(compiled.base_semantic_scene)
    expected_previous_certificate = compiled.base_semantic_scene.certificate_head_sha256
    expected_revision = compiled.base_scene.revision
    for checkpoint in compiled.checkpoints:
        body = checkpoint.certificate.body
        assert checkpoint.beat == compiled.beat
        assert checkpoint.receipt.problem_spec_sha256 == problem_digest
        assert body.problem_spec_sha256 == problem_digest
        assert body.base_revision == expected_revision
        assert body.result_revision == expected_revision + 1
        assert body.base_low_level_scene_sha256 == expected_low_level_hash
        assert body.base_semantic_scene_sha256 == expected_semantic_hash
        assert body.previous_certificate_sha256 == expected_previous_certificate
        assert checkpoint.receipt.operation_targets == tuple(
            operation.target_id for operation in checkpoint.patch.operations
        )
        assert 1 <= len(checkpoint.patch.operations) <= MAX_PATCH_OPERATIONS
        assert (
            len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
            <= MAX_NDJSON_FRAME_BYTES
        )
        expected_revision = body.result_revision
        expected_low_level_hash = body.result_low_level_scene_sha256
        expected_semantic_hash = body.result_semantic_scene_sha256
        expected_previous_certificate = checkpoint.certificate.certificate_sha256

    assert expected_low_level_hash == low_level_scene_sha256(compiled.result_scene)
    assert expected_semantic_hash == semantic_scene_sha256(compiled.result_semantic_scene)
    assert compiled.result_semantic_scene.certificate_head_sha256 == (expected_previous_certificate)
    for previous, current in pairwise(compiled.checkpoints):
        assert previous.presentation.result_viewports == current.presentation.base_viewports


def test_completed_frontier_returns_an_exact_zero_suffix_without_changing_heads() -> None:
    base_scene, base_semantic_scene = _base(_problem(), 8, revision=29)

    compiled = compile_parametric_checkpoint_beat(
        _beat(_problem()),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )

    assert compiled.checkpoints == ()
    assert compiled.result_scene is compiled.base_scene
    assert compiled.result_semantic_scene is compiled.base_semantic_scene
    assert compiled.result_scene == base_scene
    assert compiled.result_semantic_scene == base_semantic_scene


@pytest.mark.parametrize(
    ("half", "magnitude", "linear_coefficient", "right_hand_side"),
    SUPPORTED_CASES,
    ids=SUPPORTED_CASE_IDS,
)
def test_every_problem_corner_clarification_certifies_then_continues_the_same_chain(
    half: int,
    magnitude: int,
    linear_coefficient: int,
    right_hand_side: int,
) -> None:
    del half, magnitude
    problem = _problem(linear_coefficient, right_hand_side)
    missing = _state(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)
    base_scene = SceneState(revision=5, nodes=materialize_parametric_nodes(missing))
    base_semantic_scene = SemanticSceneState(
        revision=5,
        components=(missing,),
        certificate_head_sha256="b" * 64,
    )

    detail = compile_parametric_checkpoint_beat(
        _clarify_beat(problem),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )

    assert tuple(checkpoint.checkpoint_id for checkpoint in detail.checkpoints) == (
        CompletingSquareCheckpointId.CORNER_DETAIL,
    )
    assert detail.checkpoints[0].certificate.body.previous_certificate_sha256 == "b" * 64
    detail_component = next(
        component
        for component in detail.result_semantic_scene.components
        if component.id == "lesson"
    )
    assert isinstance(detail_component, ParametricCompletingSquareStateV1)
    assert detail_component.corner_clarified is True

    continuation = compile_parametric_checkpoint_beat(
        _beat(problem, beat_id="beat-after-detail"),
        base_scene=detail.result_scene,
        base_semantic_scene=detail.result_semantic_scene,
    )
    assert tuple(checkpoint.checkpoint_id for checkpoint in continuation.checkpoints) == (
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
        CompletingSquareCheckpointId.FACTOR_SQUARE,
        CompletingSquareCheckpointId.SOLVE_ROOTS,
    )
    assert (
        continuation.checkpoints[0].certificate.body.previous_certificate_sha256
        == detail.result_semantic_scene.certificate_head_sha256
    )
    assert (
        detail.checkpoints[0].presentation.result_viewports
        == continuation.checkpoints[0].presentation.base_viewports
    )


def test_compiler_preserves_unrelated_low_level_and_semantic_state() -> None:
    note = LatexTokenSceneNode.model_validate(
        {
            "id": "other__note",
            "kind": "latex_token",
            "presentation": {"enter": "none", "exit": "none"},
            "x": 725.0,
            "y": 540.0,
            "width": 100.0,
            "height": 40.0,
            "anchor": "middle",
            "latex": "keep",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 20.0,
                "opacity": 1.0,
            },
        }
    )
    unrelated = PythagoreanAreaIdentityState(id="other", revealed_roles=())

    compiled = compile_parametric_checkpoint_beat(
        _beat(_problem(), CompletingSquareStage.SETUP),
        base_scene=SceneState(revision=3, nodes=(note,)),
        base_semantic_scene=SemanticSceneState(
            revision=3,
            components=(unrelated,),
        ),
    )

    assert compiled.result_scene.nodes[0] == note
    assert compiled.result_semantic_scene.components[0] == unrelated
    assert isinstance(
        compiled.result_semantic_scene.components[1],
        ParametricCompletingSquareStateV1,
    )


def test_component_id_containing_checkpoint_marker_keeps_exact_namespace() -> None:
    problem = _problem()
    component_id = "lesson__cp_alt"

    compiled = compile_parametric_checkpoint_beat(
        _beat(
            problem,
            CompletingSquareStage.SETUP,
            component_id=component_id,
        ),
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )

    prefix = f"{component_id}__"
    assert compiled.checkpoints
    assert all(
        operation.target_id.startswith(prefix)
        for checkpoint in compiled.checkpoints
        for operation in checkpoint.patch.operations
    )
    assert all(
        target_id.startswith(prefix)
        for checkpoint in compiled.checkpoints
        for cue in checkpoint.choreography.phase.cues
        for target_id in cue.target_ids
    )


def _foreign_token(
    *,
    node_id: str,
    x: float,
    y: float,
    width: float,
    height: float = 42.0,
) -> LatexTokenSceneNode:
    return LatexTokenSceneNode.model_validate(
        {
            "id": node_id,
            "kind": "latex_token",
            "presentation": {"enter": "none", "exit": "none"},
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "anchor": "middle",
            "latex": "OBSCURE",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 30.0,
                "opacity": 1.0,
            },
        }
    )


def _foreign_path() -> PathSceneNode:
    return PathSceneNode.model_validate(
        {
            "id": "other__overlap_path",
            "kind": "path",
            "presentation": {"enter": "none", "exit": "none"},
            "points": ((200.0, 220.0), (500.0, 220.0), (500.0, 520.0), (200.0, 520.0)),
            "closed": True,
            "style": {
                "stroke": "hsl(var(--chalk))",
                "strokeWidth": 2.0,
                "fill": "transparent",
                "opacity": 1.0,
                "roughness": 0.0,
            },
        }
    )


def _foreign_latex() -> LatexSceneNode:
    return LatexSceneNode.model_validate(
        {
            "id": "other__overlap_latex",
            "kind": "latex",
            "presentation": {"enter": "none", "exit": "none"},
            "x": 100.0,
            "y": 90.0,
            "latex": "OBSCURE",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 96.0,
                "opacity": 1.0,
            },
        }
    )


def _foreign_text() -> TextSceneNode:
    return TextSceneNode.model_validate(
        {
            "id": "other__overlap_text",
            "kind": "text",
            "presentation": {"enter": "none", "exit": "none"},
            "x": 400.0,
            "y": 110.0,
            "text": "OBSCURE THE EQUATION",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 48.0,
                "opacity": 1.0,
                "anchor": "middle",
            },
        }
    )


@pytest.mark.parametrize(
    "foreign_node",
    [
        _foreign_token(
            node_id="other__overlap_token",
            x=400.0,
            y=90.0,
            width=500.0,
        ),
        _foreign_path(),
        _foreign_latex(),
        _foreign_text(),
    ],
    ids=("token", "path", "legacy-latex", "legacy-text"),
)
def test_cross_component_visual_overlaps_fail_atomic_compilation(
    foreign_node: SceneNode,
) -> None:
    unrelated = PythagoreanAreaIdentityState(id="other", revealed_roles=())

    with pytest.raises(ParametricCheckpointCompilationError, match="cross-component collision"):
        compile_parametric_checkpoint_beat(
            _beat(_problem(), CompletingSquareStage.SETUP),
            base_scene=SceneState(revision=0, nodes=(foreign_node,)),
            base_semantic_scene=SemanticSceneState(
                revision=0,
                components=(unrelated,),
            ),
        )


def test_cross_component_boxes_may_touch_edges_without_overlapping() -> None:
    clean = compile_parametric_checkpoint_beat(
        _beat(_problem(), CompletingSquareStage.SETUP),
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )
    equation_rhs = next(node for node in clean.result_scene.nodes if node.id == "lesson__eq_rhs")
    assert isinstance(equation_rhs, LatexTokenSceneNode)
    rhs_right = equation_rhs.x + equation_rhs.width / 2.0
    touching = _foreign_token(
        node_id="other__touching",
        x=rhs_right + 50.0,
        y=equation_rhs.y,
        width=100.0,
    )
    unrelated = PythagoreanAreaIdentityState(id="other", revealed_roles=())

    compiled = compile_parametric_checkpoint_beat(
        _beat(_problem(), CompletingSquareStage.SETUP),
        base_scene=SceneState(revision=0, nodes=(touching,)),
        base_semantic_scene=SemanticSceneState(
            revision=0,
            components=(unrelated,),
        ),
    )

    assert compiled.result_scene.nodes[0] == touching


def test_revision_drift_dirty_namespace_foreign_kind_and_cross_problem_fail() -> None:
    problem = _problem()
    other_problem = _problem(6, 7)
    beat = _beat(problem)
    dirty = LatexTokenSceneNode.model_validate(
        {
            "id": "lesson__dirty",
            "kind": "latex_token",
            "presentation": {"enter": "none", "exit": "none"},
            "x": 400.0,
            "y": 300.0,
            "width": 80.0,
            "height": 40.0,
            "anchor": "middle",
            "latex": "dirty",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 20.0,
                "opacity": 1.0,
            },
        }
    )

    with pytest.raises(ParametricCheckpointCompilationError, match="revisions must match"):
        compile_parametric_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=1),
            base_semantic_scene=SemanticSceneState(revision=2),
        )
    with pytest.raises(ParametricCheckpointCompilationError, match="does not match"):
        compile_parametric_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=0, nodes=(dirty,)),
            base_semantic_scene=SemanticSceneState(revision=0),
        )
    with pytest.raises(ParametricCheckpointCompilationError, match="different semantic"):
        compile_parametric_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=0),
            base_semantic_scene=SemanticSceneState(
                revision=0,
                components=(CompletingSquareState(id="lesson"),),
            ),
        )
    other_state = _state(other_problem, CompletingSquareMainCheckpoint.PROBLEM)
    with pytest.raises(ParametricCheckpointCompilationError, match="problemSpec"):
        compile_parametric_checkpoint_beat(
            beat,
            base_scene=SceneState(
                revision=1,
                nodes=materialize_parametric_nodes(other_state),
            ),
            base_semantic_scene=SemanticSceneState(
                revision=1,
                components=(other_state,),
            ),
        )


@pytest.mark.parametrize(
    "body_updates",
    [
        {
            "base_revision": 101,
            "result_revision": 102,
        },
        {"base_low_level_scene_sha256": "f" * 64},
        {"result_low_level_scene_sha256": "e" * 64},
        {"base_semantic_scene_sha256": "d" * 64},
        {"result_semantic_scene_sha256": "c" * 64},
        {"previous_certificate_sha256": "b" * 64},
    ],
)
def test_batch_rejects_arbitrarily_reissued_revision_hash_and_head_claims(
    body_updates: dict[str, object],
) -> None:
    compiled = _compile()
    first = _reissue_certificate(compiled.checkpoints[0], **body_updates)

    with pytest.raises(ValidationError, match="independently verified compiler claim"):
        _batch_with(
            compiled,
            checkpoints=(first, *compiled.checkpoints[1:]),
        )


def test_batch_rejects_a_fully_reissued_corrupt_final_checkpoint() -> None:
    compiled = _compile()
    original = compiled.checkpoints[-1]
    narration = f"{original.patch.narration} Altered after verification."
    patch = ScenePatchDraft(
        patch_id=original.patch.patch_id,
        narration=narration,
        operations=original.patch.operations,
    )
    presentation = original.presentation.model_copy(update={"checkpoint_narration": narration})
    body = original.certificate.body.model_copy(
        update={
            "patch_sha256": scene_patch_sha256(patch),
            "presentation_checkpoint": presentation,
        }
    )
    certificate = CheckpointCompilerCertificateV3(
        body=body,
        certificate_sha256=checkpoint_certificate_v3_sha256(body),
    )
    corrupt = CompiledCheckpointV3(
        beat=original.beat,
        checkpoint_id=original.checkpoint_id,
        patch=patch,
        receipt=original.receipt,
        presentation=presentation,
        choreography=original.choreography,
        certificate=certificate,
    )

    with pytest.raises(ValidationError, match="independently verified compiler claim"):
        _batch_with(
            compiled,
            checkpoints=(*compiled.checkpoints[:-1], corrupt),
        )


def test_batch_rejects_reordered_suffix_and_inexact_result_boundaries() -> None:
    compiled = _compile()
    reordered = (
        compiled.checkpoints[1],
        compiled.checkpoints[0],
        *compiled.checkpoints[2:],
    )
    with pytest.raises(ValidationError, match="exact missing suffix"):
        _batch_with(compiled, checkpoints=reordered)

    reversed_result = SceneState(
        revision=compiled.result_scene.revision,
        nodes=tuple(reversed(compiled.result_scene.nodes)),
    )
    with pytest.raises(ValidationError, match="exact result scene"):
        _batch_with(compiled, result_scene=reversed_result)

    wrong_head = compiled.result_semantic_scene.model_copy(
        update={"certificate_head_sha256": "0" * 64}
    )
    with pytest.raises(ValidationError, match="exact semantic frontier"):
        _batch_with(compiled, result_semantic_scene=wrong_head)


def test_zero_suffix_batch_rejects_any_result_mutation() -> None:
    compiled = _compile(prefix_length=8)
    changed = compiled.result_semantic_scene.model_copy(
        update={"certificate_head_sha256": "9" * 64}
    )

    with pytest.raises(ValidationError, match="empty V3 checkpoint suffix"):
        _batch_with(compiled, result_semantic_scene=changed)


def test_compilation_is_byte_deterministic() -> None:
    first = _compile(revision=23, certificate_head="8" * 64)
    second = _compile(revision=23, certificate_head="8" * 64)

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)


def test_backward_route_fails_before_any_certified_checkpoint() -> None:
    problem = _problem()
    factor = _state(problem, CompletingSquareMainCheckpoint.FACTOR_SQUARE)

    with pytest.raises(ParametricCompletingSquareCompilationError, match="cannot move backward"):
        compile_parametric_checkpoint_beat(
            _beat(problem, CompletingSquareStage.COMPLETE),
            base_scene=SceneState(
                revision=7,
                nodes=materialize_parametric_nodes(factor),
            ),
            base_semantic_scene=SemanticSceneState(
                revision=7,
                components=(factor,),
            ),
        )

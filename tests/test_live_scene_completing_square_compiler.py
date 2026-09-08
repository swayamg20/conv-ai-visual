from __future__ import annotations

import ast
import inspect
from itertools import pairwise

import pytest
from murmur.live_scene import completing_square_compiler
from murmur.live_scene.checkpoint_contracts import (
    CheckpointCompilerCertificateV2,
    CompiledCheckpointV2,
    checkpoint_certificate_sha256,
    low_level_scene_sha256,
)
from murmur.live_scene.choreography_contracts import (
    CompletingSquareStage,
    RoutedChoreographyBeatV2,
)
from murmur.live_scene.completing_square_compiler import (
    CompiledCheckpointBeatV2,
    CompiledCheckpointBlueprint,
    CompiledCheckpointBlueprintBatch,
    CompletingSquareCompilationError,
    compile_checkpoint_beat,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexTokenSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
    SceneState,
)
from murmur.live_scene.semantic_contracts import (
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1
from pydantic import ValidationError


def _advance_beat(
    stage: CompletingSquareStage = CompletingSquareStage.SOLVE,
    *,
    beat_id: str = "beat-solve",
    component_id: str = "lesson",
) -> RoutedChoreographyBeatV2:
    return RoutedChoreographyBeatV2.model_validate(
        {
            "v": 2,
            "beatId": beat_id,
            "componentKind": "completing_square",
            "componentId": component_id,
            "route": {"intent": "advance", "targetStage": stage.value},
        }
    )


def _clarify_beat(*, component_id: str = "lesson") -> RoutedChoreographyBeatV2:
    return RoutedChoreographyBeatV2.model_validate(
        {
            "v": 2,
            "beatId": "beat-corner-detail",
            "componentKind": "completing_square",
            "componentId": component_id,
            "route": {"intent": "clarify_corner"},
        }
    )


def _state(
    checkpoint: CompletingSquareMainCheckpoint | None,
    *,
    component_id: str = "lesson",
    clarified: bool = False,
) -> CompletingSquareState:
    return CompletingSquareState(
        id=component_id,
        last_main_checkpoint=checkpoint,
        corner_clarified=clarified,
    )


def _blueprints(
    beat: RoutedChoreographyBeatV2,
    state: CompletingSquareState | None,
) -> CompiledCheckpointBlueprintBatch:
    return completing_square_compiler._compile_blueprints(beat, state)


def _compile(
    beat: RoutedChoreographyBeatV2,
    state: CompletingSquareState | None = None,
    *,
    revision: int = 0,
    certificate_head: str | None = None,
) -> CompiledCheckpointBeatV2:
    nodes = () if state is None else completing_square_compiler._nodes_for_state(state)
    base_scene = SceneState(revision=revision, nodes=nodes)
    base_semantic_scene = SemanticSceneState(
        revision=revision,
        components=() if state is None else (state,),
        certificate_head_sha256=certificate_head,
    )
    return compile_checkpoint_beat(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )


def _node_map(nodes: tuple[SceneNode, ...]) -> dict[str, SceneNode]:
    return {node.id: node for node in nodes}


def _apply_blueprint(checkpoint: CompiledCheckpointBlueprint) -> tuple[SceneNode, ...]:
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
        return (
            min(xs) - padding,
            min(ys) - padding,
            max(xs) + padding,
            max(ys) + padding,
        )
    if isinstance(node, LatexTokenSceneNode):
        left = node.x
        if node.anchor == "middle":
            left -= node.width / 2
        elif node.anchor == "end":
            left -= node.width
        return left, node.y, left + node.width, node.y + node.height
    raise AssertionError(f"unexpected flagship node kind: {node.kind}")


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


def _replace_checkpoint_certificate(
    checkpoint: CompiledCheckpointV2,
    **body_updates: object,
) -> CompiledCheckpointV2:
    body = checkpoint.certificate.body.model_copy(update=body_updates)
    certificate = CheckpointCompilerCertificateV2(
        body=body,
        certificate_sha256=checkpoint_certificate_sha256(body),
    )
    return CompiledCheckpointV2(
        beat=checkpoint.beat,
        checkpoint_id=checkpoint.checkpoint_id,
        patch=checkpoint.patch,
        receipt=checkpoint.receipt,
        presentation=checkpoint.presentation,
        choreography=checkpoint.choreography,
        certificate=certificate,
    )


@pytest.mark.parametrize(
    ("stage", "expected_count"),
    [
        (CompletingSquareStage.SETUP, 2),
        (CompletingSquareStage.SPLIT, 4),
        (CompletingSquareStage.COMPLETE, 6),
        (CompletingSquareStage.SOLVE, 8),
    ],
)
def test_closed_stages_compile_the_exact_checkpoint_prefix(
    stage: CompletingSquareStage,
    expected_count: int,
) -> None:
    compiled = _blueprints(_advance_beat(stage), None)

    assert tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints) == tuple(
        checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:expected_count]
    )
    assert compiled.base_component == _state(None)
    assert compiled.result_component == _state(
        COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[expected_count - 1]
    )


def test_full_lesson_has_exact_math_content_and_honest_domain_narration() -> None:
    compiled = _blueprints(_advance_beat(), None)
    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in compiled.checkpoints}

    problem = _node_map(by_id[CompletingSquareCheckpointId.PROBLEM].result_nodes)
    assert problem["lesson__eq_x2"].latex == "x^2"  # type: ignore[union-attr]
    assert problem["lesson__eq_6x"].latex == "6x"  # type: ignore[union-attr]
    assert problem["lesson__eq_rhs7"].latex == "7"  # type: ignore[union-attr]

    split = _node_map(by_id[CompletingSquareCheckpointId.SPLIT_LINEAR_TERM].result_nodes)
    assert split["lesson__eq_3x_a"].latex == "3x"  # type: ignore[union-attr]
    assert split["lesson__eq_3x_b"].latex == "3x"  # type: ignore[union-attr]
    assert "lesson__eq_6x" not in split

    complete = _node_map(by_id[CompletingSquareCheckpointId.BALANCE_AND_COMPLETE].result_nodes)
    assert complete["lesson__eq_corner9"].latex == "9"  # type: ignore[union-attr]
    assert complete["lesson__eq_rhs9"].latex == "9"  # type: ignore[union-attr]
    assert complete["lesson__eq_16"].latex == "16"  # type: ignore[union-attr]

    factored = _node_map(by_id[CompletingSquareCheckpointId.FACTOR_SQUARE].result_nodes)
    assert factored["lesson__eq_factor"].latex == "(x+3)^2"  # type: ignore[union-attr]
    roots = _node_map(by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].result_nodes)
    assert roots["lesson__root_pm4"].latex == r"\pm 4"  # type: ignore[union-attr]
    assert roots["lesson__root_one"].latex == "1"  # type: ignore[union-attr]
    assert roots["lesson__root_neg7"].latex == "-7"  # type: ignore[union-attr]
    assert (
        "nonnegative-length branch"
        in by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].patch.narration
    )
    assert (
        "algebra recovers both roots"
        in by_id[CompletingSquareCheckpointId.SOLVE_ROOTS].patch.narration
    )


def test_solution_derivation_reflows_above_the_area_model_with_clearance() -> None:
    checkpoints = _blueprints(_advance_beat(), None).checkpoints
    factored = checkpoints[6]
    solved = checkpoints[7]
    factored_nodes = _node_map(factored.result_nodes)
    solved_nodes = _node_map(solved.result_nodes)

    derivation_rows = (
        _node_bounds(solved_nodes["lesson__eq_factor"])[1],
        _node_bounds(solved_nodes["lesson__root_lhs"])[1],
        _node_bounds(solved_nodes["lesson__root_x_left"])[1],
    )
    assert all(right - left >= 52.0 for left, right in pairwise(derivation_rows))
    derivation = tuple(
        node
        for node_id, node in solved_nodes.items()
        if "__root_" in node_id
        or node_id in {"lesson__eq_factor", "lesson__eq_equal_result", "lesson__eq_16"}
    )
    geometry = tuple(node for node in solved.result_nodes if isinstance(node, PathSceneNode))
    equation_bottom = max(_node_bounds(node)[3] for node in derivation)
    geometry_top = min(_node_bounds(node)[1] for node in geometry)
    assert geometry_top - equation_bottom >= 16.0

    for suffix in ("eq_factor", "eq_equal_result", "eq_16"):
        node_id = f"lesson__{suffix}"
        assert solved_nodes[node_id].id == factored_nodes[node_id].id
        assert _node_bounds(solved_nodes[node_id])[1] < _node_bounds(factored_nodes[node_id])[1]
    assert solved.presentation.base_viewports == factored.presentation.result_viewports
    assert (
        solved.presentation.result_viewports.cinematic.y
        < solved.presentation.base_viewports.cinematic.y
    )
    assert (
        solved.presentation.result_viewports.cinematic.height
        > solved.presentation.base_viewports.cinematic.height
    )


def test_square_strips_and_persistent_tokens_keep_identity_and_path_topology() -> None:
    checkpoints = _blueprints(_advance_beat(), None).checkpoints
    node_maps = [_node_map(checkpoint.result_nodes) for checkpoint in checkpoints]
    geometric_ids = ("lesson__x2_square", "lesson__strip_a", "lesson__strip_b")

    for node_id in geometric_ids:
        appearances = [nodes[node_id] for nodes in node_maps if node_id in nodes]
        assert appearances
        assert all(isinstance(node, PathSceneNode) for node in appearances)
        assert all(len(node.points) == 4 and node.closed for node in appearances)  # type: ignore[union-attr]

    initial_strips = node_maps[1]
    arranged_strips = node_maps[3]
    for suffix in ("strip_a", "strip_b", "area_3x_a", "area_3x_b"):
        node_id = f"lesson__{suffix}"
        assert initial_strips[node_id].id == arranged_strips[node_id].id == node_id
        assert initial_strips[node_id] != arranged_strips[node_id]

    complete = node_maps[5]
    factored = node_maps[6]
    for suffix in ("eq_equal_result", "eq_16"):
        node_id = f"lesson__{suffix}"
        assert complete[node_id].id == factored[node_id].id
        assert complete[node_id].latex == factored[node_id].latex  # type: ignore[union-attr]


def test_every_patch_is_small_atomic_and_exactly_materializes_its_snapshot() -> None:
    compiled = _blueprints(_advance_beat(), None)

    for checkpoint in compiled.checkpoints:
        assert 1 <= len(checkpoint.patch.operations) <= MAX_PATCH_OPERATIONS
        assert (
            len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
            <= MAX_NDJSON_FRAME_BYTES
        )
        assert checkpoint.patch.narration == checkpoint.presentation.checkpoint_narration
        assert _apply_blueprint(checkpoint) == checkpoint.result_nodes
        assert len({operation.target_id for operation in checkpoint.patch.operations}) == len(
            checkpoint.patch.operations
        )


def test_main_viewports_join_exactly_and_main_timing_is_within_sixty_to_ninety_seconds() -> None:
    checkpoints = _blueprints(_advance_beat(), None).checkpoints

    for previous, current in pairwise(checkpoints):
        assert previous.presentation.result_viewports == current.presentation.base_viewports
    total_ms = sum(checkpoint.choreography.phase.total_ms for checkpoint in checkpoints)
    assert 60_000 <= total_ms <= 90_000
    assert all(checkpoint.presentation.transient_free for checkpoint in checkpoints)


def test_every_focus_and_emphasis_target_is_inside_each_result_viewport_with_padding() -> None:
    checkpoints = _blueprints(_advance_beat(), None).checkpoints
    detail = _blueprints(
        _clarify_beat(),
        checkpoints[4].result_component,
    ).checkpoints[0]

    for checkpoint in (*checkpoints, detail):
        nodes = _node_map(checkpoint.result_nodes)
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


def test_cues_and_operation_targets_are_canonical_and_server_owned() -> None:
    checkpoints = _blueprints(_advance_beat(), None).checkpoints
    cue_order = ("enter", "exit", "transform", "emphasize", "focus")

    for checkpoint in checkpoints:
        phase = checkpoint.choreography.phase
        assert tuple(cue.cue for cue in phase.cues) == tuple(
            sorted((cue.cue for cue in phase.cues), key=cue_order.index)
        )
        for cue in phase.cues:
            assert cue.target_ids == tuple(sorted(cue.target_ids))
            assert len(cue.target_ids) == len(set(cue.target_ids))
            assert all(node_id.startswith("lesson__") for node_id in cue.target_ids)
        assert tuple(operation.target_id for operation in checkpoint.patch.operations) == tuple(
            sorted(operation.target_id for operation in checkpoint.patch.operations)
        )


def test_compilation_is_byte_deterministic_and_imports_no_entropy_source() -> None:
    beat = _advance_beat()
    first = _blueprints(beat, None)
    second = _blueprints(beat, None)

    assert _serialized_batch(first) == _serialized_batch(second)

    tree = ast.parse(inspect.getsource(completing_square_compiler))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint({"datetime", "random", "secrets", "time", "uuid"})


def test_full_compilation_binds_every_scene_receipt_and_certificate_transition() -> None:
    compiled = _compile(_advance_beat(), revision=11, certificate_head="a" * 64)

    assert len(compiled.checkpoints) == 8
    assert compiled.base_scene.revision == compiled.base_semantic_scene.revision == 11
    assert compiled.result_scene.revision == compiled.result_semantic_scene.revision == 19
    result_component = next(
        component
        for component in compiled.result_semantic_scene.components
        if component.id == "lesson"
    )
    assert result_component == _state(CompletingSquareMainCheckpoint.SOLVE_ROOTS)

    previous_low_hash = low_level_scene_sha256(compiled.base_scene)
    previous_semantic_hash = semantic_scene_sha256(compiled.base_semantic_scene)
    previous_certificate = "a" * 64
    previous_viewports = None
    for ordinal, checkpoint in enumerate(compiled.checkpoints, start=1):
        body = checkpoint.certificate.body
        assert checkpoint.beat == compiled.beat
        assert body.base_revision == 10 + ordinal
        assert body.result_revision == 11 + ordinal
        assert body.base_low_level_scene_sha256 == previous_low_hash
        assert body.base_semantic_scene_sha256 == previous_semantic_hash
        assert body.previous_certificate_sha256 == previous_certificate
        assert checkpoint.receipt.operation_targets == tuple(
            operation.target_id for operation in checkpoint.patch.operations
        )
        assert checkpoint.receipt.issuer == "completing_square_verifier"
        if previous_viewports is not None:
            assert checkpoint.presentation.base_viewports == previous_viewports
        previous_low_hash = body.result_low_level_scene_sha256
        previous_semantic_hash = body.result_semantic_scene_sha256
        previous_certificate = checkpoint.certificate.certificate_sha256
        previous_viewports = checkpoint.presentation.result_viewports

    assert previous_low_hash == low_level_scene_sha256(compiled.result_scene)
    assert previous_semantic_hash == semantic_scene_sha256(compiled.result_semantic_scene)
    assert compiled.result_semantic_scene.certificate_head_sha256 == previous_certificate
    assert compiled.model_dump_json(by_alias=True) == _compile(
        _advance_beat(), revision=11, certificate_head="a" * 64
    ).model_dump_json(by_alias=True)


@pytest.mark.parametrize("prefix_length", range(9))
def test_certified_resume_from_every_frontier_emits_only_the_missing_suffix(
    prefix_length: int,
) -> None:
    frontier = (
        None if prefix_length == 0 else COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length - 1]
    )
    compiled = _compile(
        _advance_beat(),
        None if frontier is None else _state(frontier),
        revision=17,
        certificate_head="b" * 64,
    )

    assert tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints) == tuple(
        checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length:]
    )
    if compiled.checkpoints:
        assert compiled.checkpoints[0].certificate.body.previous_certificate_sha256 == "b" * 64
    else:
        assert compiled.result_scene == compiled.base_scene
        assert compiled.result_semantic_scene == compiled.base_semantic_scene


def test_full_clarification_and_continuation_form_one_certified_viewport_chain() -> None:
    missing = _state(CompletingSquareMainCheckpoint.MISSING_CORNER)
    detail = _compile(
        _clarify_beat(),
        missing,
        revision=5,
        certificate_head="c" * 64,
    )
    assert tuple(checkpoint.checkpoint_id for checkpoint in detail.checkpoints) == (
        CompletingSquareCheckpointId.CORNER_DETAIL,
    )
    detail_component = next(
        component
        for component in detail.result_semantic_scene.components
        if component.id == "lesson"
    )
    assert isinstance(detail_component, CompletingSquareState)
    assert detail_component.corner_clarified is True

    continuation = _compile(
        _advance_beat(CompletingSquareStage.COMPLETE, beat_id="beat-complete"),
        detail_component,
        revision=detail.result_scene.revision,
        certificate_head=detail.result_semantic_scene.certificate_head_sha256,
    )
    assert tuple(checkpoint.checkpoint_id for checkpoint in continuation.checkpoints) == (
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    )
    assert (
        detail.checkpoints[0].presentation.result_viewports
        == continuation.checkpoints[0].presentation.base_viewports
    )


def test_compiler_rejects_revision_drift_and_scene_semantic_mismatch() -> None:
    beat = _advance_beat(CompletingSquareStage.SETUP)
    with pytest.raises(CompletingSquareCompilationError, match="revisions must match"):
        compile_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=1),
            base_semantic_scene=SemanticSceneState(revision=2),
        )

    semantic = SemanticSceneState(
        revision=1,
        components=(_state(CompletingSquareMainCheckpoint.PROBLEM),),
    )
    with pytest.raises(CompletingSquareCompilationError, match="does not match"):
        compile_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=1),
            base_semantic_scene=semantic,
        )


def test_compiler_preserves_unrelated_low_level_and_semantic_state() -> None:
    note = LatexTokenSceneNode.model_validate(
        {
            "id": "other__note",
            "kind": "latex_token",
            "presentation": {"enter": "none", "exit": "none"},
            "x": 700.0,
            "y": 540.0,
            "width": 80.0,
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
    compiled = compile_checkpoint_beat(
        _advance_beat(CompletingSquareStage.SETUP),
        base_scene=SceneState(revision=3, nodes=(note,)),
        base_semantic_scene=SemanticSceneState(revision=3, components=(unrelated,)),
    )

    assert compiled.result_scene.nodes[0] == note
    assert compiled.result_semantic_scene.components[0] == unrelated
    assert isinstance(compiled.result_semantic_scene.components[1], CompletingSquareState)


def test_batch_contract_rejects_mutated_middle_scene_hash_and_viewport_join() -> None:
    compiled = _compile(_advance_beat())
    middle = compiled.checkpoints[3]
    wrong_hash = _replace_checkpoint_certificate(
        middle,
        base_low_level_scene_sha256="f" * 64,
    )
    with pytest.raises(ValidationError, match="low-level scene hashes"):
        CompiledCheckpointBeatV2(
            beat=compiled.beat,
            base_scene=compiled.base_scene,
            result_scene=compiled.result_scene,
            base_semantic_scene=compiled.base_semantic_scene,
            result_semantic_scene=compiled.result_semantic_scene,
            checkpoints=(*compiled.checkpoints[:3], wrong_hash, *compiled.checkpoints[4:]),
        )

    second = compiled.checkpoints[1]
    bad_presentation = second.presentation.model_copy(
        update={"base_viewports": second.presentation.result_viewports}
    )
    body = second.certificate.body.model_copy(update={"presentation_checkpoint": bad_presentation})
    certificate = CheckpointCompilerCertificateV2(
        body=body,
        certificate_sha256=checkpoint_certificate_sha256(body),
    )
    bad_viewport = CompiledCheckpointV2(
        beat=second.beat,
        checkpoint_id=second.checkpoint_id,
        patch=second.patch,
        receipt=second.receipt,
        presentation=bad_presentation,
        choreography=second.choreography,
        certificate=certificate,
    )
    with pytest.raises(ValidationError, match="viewport transitions"):
        CompiledCheckpointBeatV2(
            beat=compiled.beat,
            base_scene=compiled.base_scene,
            result_scene=compiled.result_scene,
            base_semantic_scene=compiled.base_semantic_scene,
            result_semantic_scene=compiled.result_semantic_scene,
            checkpoints=(compiled.checkpoints[0], bad_viewport, *compiled.checkpoints[2:]),
        )


@pytest.mark.parametrize("prefix_length", range(9))
def test_resume_from_every_main_frontier_emits_only_the_missing_suffix(
    prefix_length: int,
) -> None:
    checkpoint = (
        None if prefix_length == 0 else COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length - 1]
    )
    resumed = _blueprints(_advance_beat(), _state(checkpoint))

    assert tuple(item.checkpoint_id.value for item in resumed.checkpoints) == tuple(
        item.value for item in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[prefix_length:]
    )
    if resumed.checkpoints:
        assert resumed.checkpoints[0].base_component == _state(checkpoint)
    assert resumed.result_component == _state(CompletingSquareMainCheckpoint.SOLVE_ROOTS)


def test_corner_clarification_is_atomic_preserves_board_and_joins_continuation() -> None:
    main = _blueprints(_advance_beat(), None)
    missing = main.checkpoints[4]
    clarification = _blueprints(
        _clarify_beat(),
        missing.result_component,
    )

    assert len(clarification.checkpoints) == 1
    detail = clarification.checkpoints[0]
    assert detail.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
    assert detail.base_component == missing.result_component
    assert detail.result_component == _state(
        CompletingSquareMainCheckpoint.MISSING_CORNER,
        clarified=True,
    )
    assert set(_node_map(detail.base_nodes)).issubset(_node_map(detail.result_nodes))
    assert {operation.target_id for operation in detail.patch.operations} == {
        "lesson__corner_calc",
        "lesson__corner_dim_h",
        "lesson__corner_dim_v",
    }
    assert detail.presentation.base_viewports == missing.presentation.result_viewports

    continuation = _blueprints(
        _advance_beat(CompletingSquareStage.COMPLETE, beat_id="beat-complete"),
        detail.result_component,
    )
    assert tuple(item.checkpoint_id for item in continuation.checkpoints) == (
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    )
    assert (
        detail.presentation.result_viewports
        == continuation.checkpoints[0].presentation.base_viewports
    )
    assert continuation.result_component.corner_clarified is True
    assert len(continuation.checkpoints[0].patch.operations) <= MAX_PATCH_OPERATIONS


@pytest.mark.parametrize(
    ("checkpoint", "clarified"),
    [
        (None, False),
        (CompletingSquareMainCheckpoint.PROBLEM, False),
        (CompletingSquareMainCheckpoint.AREA_MODEL, False),
        (CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM, False),
        (CompletingSquareMainCheckpoint.REARRANGE_HALVES, False),
        (CompletingSquareMainCheckpoint.MISSING_CORNER, True),
        (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, False),
        (CompletingSquareMainCheckpoint.SOLVE_ROOTS, False),
    ],
)
def test_corner_clarification_rejects_every_illegal_frontier_without_mutation(
    checkpoint: CompletingSquareMainCheckpoint | None,
    clarified: bool,
) -> None:
    state = _state(checkpoint, clarified=clarified)

    with pytest.raises(CompletingSquareCompilationError, match="only at the unclarified"):
        _blueprints(_clarify_beat(), state)

    assert state == _state(checkpoint, clarified=clarified)


def test_backward_stage_and_cross_component_compilation_fail_before_emission() -> None:
    base = _state(CompletingSquareMainCheckpoint.FACTOR_SQUARE)
    with pytest.raises(CompletingSquareCompilationError, match="cannot move backward"):
        _blueprints(_advance_beat(CompletingSquareStage.COMPLETE), base)
    with pytest.raises(CompletingSquareCompilationError, match="componentId must match"):
        _blueprints(_advance_beat(component_id="other"), base)


def test_only_corner_area_changes_latex_for_a_persistent_token() -> None:
    main = _blueprints(_advance_beat(), None)
    changed_syntax: set[str] = set()
    for checkpoint in main.checkpoints:
        base = _node_map(checkpoint.base_nodes)
        result = _node_map(checkpoint.result_nodes)
        for node_id in set(base).intersection(result):
            before = base[node_id]
            after = result[node_id]
            if (
                isinstance(before, LatexTokenSceneNode)
                and isinstance(after, LatexTokenSceneNode)
                and before.latex != after.latex
            ):
                changed_syntax.add(node_id)

    assert changed_syntax == {"lesson__corner_area"}

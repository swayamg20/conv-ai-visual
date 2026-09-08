"""Deterministic visual choreography for the completing-square flagship.

The model-facing beat selects only a closed stage or the one supported
clarification.  This module owns every node, word, coordinate, viewport, cue,
and millisecond.  Checkpoints are built as immutable target snapshots and then
diffed, so a fresh lesson and every resumed suffix use the same realization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypeAlias

from pydantic import Field, model_validator

from murmur.live_scene.checkpoint_contracts import (
    CHECKPOINT_COMPILER_VERSION,
    CheckpointCompilerCertificateBodyV2,
    CheckpointCompilerCertificateV2,
    CompiledCheckpointV2,
    checkpoint_certificate_sha256,
    checkpoint_receipt_sha256,
    low_level_scene_sha256,
)
from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ChoreographyPhaseV1,
    ChoreographyPlanV1,
    ClarifyCornerRouteV2,
    EmphasizeCueV1,
    EnterCueV1,
    ExitCueV1,
    FocusCueV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
    TransformCueV1,
    ViewportPoseV1,
    choreography_plan_sha256,
    routed_choreography_beat_sha256,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
    checkpoint_prefix,
    checkpoints_through,
)
from murmur.live_scene.completing_square_verifier import (
    verify_completing_square_checkpoint,
)
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexTokenSceneNode,
    LiveSceneContract,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
    ScenePatchDraft,
    SceneState,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1

Point: TypeAlias = tuple[float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

COMPLETING_SQUARE_COMPILER_VERSION = CHECKPOINT_COMPILER_VERSION

_DRAW = {"enter": "draw", "exit": "fade"}
_FADE = {"enter": "fade", "exit": "fade"}
_GEOMETRY_STYLE = {
    "stroke": "hsl(var(--chalk))",
    "strokeWidth": 3.0,
    "fill": "#16171C",
    "opacity": 1.0,
    "roughness": 0.0,
}
_STRIP_A_STYLE = {
    "stroke": "hsl(var(--sage))",
    "strokeWidth": 3.0,
    "fill": "#173626",
    "opacity": 1.0,
    "roughness": 0.0,
}
_STRIP_B_STYLE = {
    "stroke": "hsl(var(--lavender))",
    "strokeWidth": 3.0,
    "fill": "#2E2850",
    "opacity": 1.0,
    "roughness": 0.0,
}
_MISSING_STYLE = {
    "stroke": "hsl(var(--amber))",
    "strokeWidth": 3.0,
    "fill": "transparent",
    "opacity": 1.0,
    "roughness": 0.0,
}
_FILLED_CORNER_STYLE = {**_MISSING_STYLE, "fill": "#4A3212"}
_EQUATION_STYLE = {
    "color": "hsl(var(--chalk))",
    "fontSize": 34.0,
    "opacity": 1.0,
}
_AMBER_EQUATION_STYLE = {**_EQUATION_STYLE, "color": "hsl(var(--amber))"}
_LABEL_STYLE = {
    "color": "hsl(var(--chalk))",
    "fontSize": 24.0,
    "opacity": 1.0,
}
_DETAIL_STYLE = {
    "color": "hsl(var(--amber))",
    "fontSize": 24.0,
    "opacity": 1.0,
}

# The solved state keeps the full derivation visible above the area model.
# Three 48-unit token rows separated by four units leave a final composition
# gutter before the completed-square stroke.
_SOLUTION_ROW_Y = (70.0, 122.0, 174.0)

_INITIAL_VIEWPORTS = ((0.0, 75.0, 800.0, 450.0), (70.0, 35.0, 660.0, 530.0))
_RESULT_VIEWPORTS = {
    CompletingSquareCheckpointId.PROBLEM: (
        (40.0, 75.0, 720.0, 405.0),
        (95.0, 40.0, 610.0, 500.0),
    ),
    CompletingSquareCheckpointId.AREA_MODEL: (
        (0.0, 75.0, 800.0, 450.0),
        (70.0, 35.0, 660.0, 530.0),
    ),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
        (0.0, 75.0, 800.0, 450.0),
        (70.0, 35.0, 660.0, 530.0),
    ),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (
        (32.0, 72.0, 736.0, 420.0),
        (100.0, 55.0, 600.0, 500.0),
    ),
    CompletingSquareCheckpointId.MISSING_CORNER: (
        (220.0, 220.0, 280.0, 280.0),
        (205.0, 205.0, 310.0, 310.0),
    ),
    CompletingSquareCheckpointId.CORNER_DETAIL: (
        (220.0, 220.0, 420.0, 330.0),
        (200.0, 200.0, 460.0, 370.0),
    ),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
        (0.0, 75.0, 800.0, 450.0),
        (70.0, 35.0, 660.0, 530.0),
    ),
    CompletingSquareCheckpointId.FACTOR_SQUARE: (
        (0.0, 75.0, 800.0, 450.0),
        (70.0, 35.0, 660.0, 530.0),
    ),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (
        (0.0, 25.0, 800.0, 500.0),
        (70.0, 25.0, 660.0, 550.0),
    ),
}

_NARRATION = {
    CompletingSquareCheckpointId.PROBLEM: (
        "We will solve x squared plus six x equals seven by turning the left side into an area."
    ),
    CompletingSquareCheckpointId.AREA_MODEL: (
        "The x squared term is a square, while six x is represented as two equal three x strips."
    ),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
        "Splitting six x evenly gives three x plus three x, one strip for each adjacent side."
    ),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (
        "Move those same strips beside the x by x square; no area has been added or removed."
    ),
    CompletingSquareCheckpointId.MISSING_CORNER: (
        "The almost-square is missing one corner whose side lengths are both three."
    ),
    CompletingSquareCheckpointId.CORNER_DETAIL: (
        "Both exposed edges measure three, so the missing corner is three by three "
        "and its area is nine."
    ),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
        "A three-by-three corner has area nine, so add nine to both sides: the "
        "right side becomes sixteen."
    ),
    CompletingSquareCheckpointId.FACTOR_SQUARE: (
        "The completed square factors as x plus three, squared, and equals sixteen."
    ),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (
        "Therefore x plus three equals plus or minus four, so x is one or negative "
        "seven. The picture shows the nonnegative-length branch; the algebra "
        "recovers both roots."
    ),
}

# Motion plus reading time totals 63.8 seconds for the eight-checkpoint main path.
_TIMING = {
    CompletingSquareCheckpointId.PROBLEM: (700, 6500, "ease_out_quart"),
    CompletingSquareCheckpointId.AREA_MODEL: (1200, 7000, "ease_out_quint"),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (900, 6500, "ease_in_out"),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (1600, 7200, "ease_in_out"),
    CompletingSquareCheckpointId.MISSING_CORNER: (850, 6500, "ease_out_quart"),
    CompletingSquareCheckpointId.CORNER_DETAIL: (1000, 6000, "ease_out_quart"),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (1250, 7000, "ease_in_out"),
    CompletingSquareCheckpointId.FACTOR_SQUARE: (1100, 6500, "ease_out_quint"),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (1200, 7800, "ease_out_quart"),
}


class CompletingSquareCompilationError(ValueError):
    """Raised before emitting any checkpoint when a route is not monotonic."""


@dataclass(frozen=True)
class CompiledCheckpointBlueprint:
    """One fully server-authored transition awaiting the V2 certificate envelope."""

    checkpoint_id: CompletingSquareCheckpointId
    base_component: CompletingSquareState
    result_component: CompletingSquareState
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV1
    presentation: PresentationCheckpointV1


@dataclass(frozen=True)
class CompiledCheckpointBlueprintBatch:
    """The exact missing checkpoint suffix for one routed beat."""

    beat: RoutedChoreographyBeatV2
    base_component: CompletingSquareState
    result_component: CompletingSquareState
    checkpoints: tuple[CompiledCheckpointBlueprint, ...]


class CompiledCheckpointBeatV2(LiveSceneContract):
    """Certified missing checkpoint suffix plus its exact scene boundaries."""

    beat: RoutedChoreographyBeatV2
    base_scene: SceneState = Field(alias="baseScene")
    result_scene: SceneState = Field(alias="resultScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")
    result_semantic_scene: SemanticSceneState = Field(alias="resultSemanticScene")
    checkpoints: Annotated[
        tuple[CompiledCheckpointV2, ...],
        Field(max_length=MAX_ACCEPTED_PATCHES),
    ] = ()

    @model_validator(mode="after")
    def validate_revision_boundaries(self) -> "CompiledCheckpointBeatV2":
        count = len(self.checkpoints)
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("base low-level and semantic revisions must match")
        if self.result_scene.revision != self.result_semantic_scene.revision:
            raise ValueError("result low-level and semantic revisions must match")
        if self.result_scene.revision != self.base_scene.revision + count:
            raise ValueError("result revisions must advance once per checkpoint")

        base_component = _target_component(self.beat, self.base_semantic_scene)
        expected = _compile_blueprints(self.beat, base_component)
        if tuple(checkpoint.checkpoint_id for checkpoint in self.checkpoints) != tuple(
            checkpoint.checkpoint_id for checkpoint in expected.checkpoints
        ):
            raise ValueError("checkpoints must be the routed beat's exact missing suffix")
        if not self.checkpoints:
            if (
                self.result_scene != self.base_scene
                or self.result_semantic_scene != self.base_semantic_scene
            ):
                raise ValueError("an empty checkpoint suffix must preserve both scenes")
            return self

        expected_low_level_hash = low_level_scene_sha256(self.base_scene)
        expected_semantic_hash = semantic_scene_sha256(self.base_semantic_scene)
        expected_previous_certificate = self.base_semantic_scene.certificate_head_sha256
        expected_revision = self.base_scene.revision
        previous_presentation: PresentationCheckpointV1 | None = None
        current_scene = self.base_scene
        current_semantic_scene = self.base_semantic_scene
        for checkpoint, blueprint in zip(
            self.checkpoints,
            expected.checkpoints,
            strict=True,
        ):
            if checkpoint.beat != self.beat:
                raise ValueError("every checkpoint must bind the batch routed beat")
            body = checkpoint.certificate.body
            if body.base_revision != expected_revision:
                raise ValueError("checkpoint base revisions must form an exact chain")
            if body.result_revision != expected_revision + 1:
                raise ValueError("checkpoint result revisions must form an exact chain")
            if body.base_low_level_scene_sha256 != expected_low_level_hash:
                raise ValueError("checkpoint low-level scene hashes must form an exact chain")
            if body.base_semantic_scene_sha256 != expected_semantic_hash:
                raise ValueError("checkpoint semantic scene hashes must form an exact chain")
            if body.previous_certificate_sha256 != expected_previous_certificate:
                raise ValueError("checkpoint certificate digests must form an exact chain")
            if (
                previous_presentation is not None
                and checkpoint.presentation.base_viewports != previous_presentation.result_viewports
            ):
                raise ValueError("checkpoint viewport transitions must join exactly")

            materialized_scene = _apply_scene_patch(current_scene, checkpoint.patch)
            if body.result_low_level_scene_sha256 != low_level_scene_sha256(materialized_scene):
                raise ValueError("checkpoint result hash must match its materialized patch")
            materialized_semantic_scene = _advance_semantic_scene(
                current_semantic_scene,
                blueprint.result_component,
                certificate_head_sha256=checkpoint.certificate.certificate_sha256,
            )
            if body.result_semantic_scene_sha256 != semantic_scene_sha256(
                materialized_semantic_scene
            ):
                raise ValueError(
                    "checkpoint result semantic hash must match its materialized frontier"
                )

            expected_low_level_hash = body.result_low_level_scene_sha256
            expected_semantic_hash = body.result_semantic_scene_sha256
            expected_previous_certificate = checkpoint.certificate.certificate_sha256
            expected_revision = body.result_revision
            previous_presentation = checkpoint.presentation
            current_scene = materialized_scene
            current_semantic_scene = materialized_semantic_scene

        if current_scene != self.result_scene or expected_low_level_hash != low_level_scene_sha256(
            self.result_scene
        ):
            raise ValueError("last checkpoint must bind the exact result scene")
        if (
            current_semantic_scene != self.result_semantic_scene
            or expected_semantic_hash != semantic_scene_sha256(self.result_semantic_scene)
        ):
            raise ValueError("last checkpoint must bind the exact result semantic scene")
        if self.result_semantic_scene.certificate_head_sha256 != expected_previous_certificate:
            raise ValueError("result semantic chain head must equal the last certificate")
        return self


def _node_id(component_id: str, suffix: str) -> str:
    return f"{component_id}__{suffix}"


def _path(
    component_id: str,
    suffix: str,
    points: tuple[Point, Point, Point, Point],
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode.model_validate(
        {
            "id": _node_id(component_id, suffix),
            "kind": "path",
            "presentation": _DRAW,
            "points": points,
            "closed": True,
            "style": style,
        }
    )


def _token(
    component_id: str,
    suffix: str,
    latex: str,
    x: float,
    y: float,
    width: float,
    *,
    height: float = 48.0,
    style: dict[str, object] = _EQUATION_STYLE,
) -> LatexTokenSceneNode:
    return LatexTokenSceneNode.model_validate(
        {
            "id": _node_id(component_id, suffix),
            "kind": "latex_token",
            "presentation": _FADE,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "anchor": "middle",
            "latex": latex,
            "style": style,
        }
    )


def _initial_equation(component_id: str) -> NodeMap:
    tokens = (
        ("eq_x2", "x^2", 151.0, 62.0),
        ("eq_plus_a", "+", 205.5, 25.0),
        ("eq_6x", "6x", 280.0, 90.0),
        ("eq_equal_main", "=", 403.5, 25.0),
        ("eq_rhs7", "7", 438.0, 28.0),
    )
    return {
        _node_id(component_id, suffix): _token(component_id, suffix, latex, x, 100.0, width)
        for suffix, latex, x, width in tokens
    }


def _split_equation(component_id: str) -> NodeMap:
    tokens = (
        ("eq_x2", "x^2", 151.0, 62.0),
        ("eq_plus_a", "+", 205.5, 25.0),
        ("eq_3x_a", "3x", 255.0, 58.0),
        ("eq_plus_b", "+", 304.5, 25.0),
        ("eq_3x_b", "3x", 354.0, 58.0),
        ("eq_equal_main", "=", 403.5, 25.0),
        ("eq_rhs7", "7", 438.0, 28.0),
    )
    return {
        _node_id(component_id, suffix): _token(component_id, suffix, latex, x, 100.0, width)
        for suffix, latex, x, width in tokens
    }


def _completed_equation(component_id: str) -> NodeMap:
    tokens = (
        ("eq_x2", "x^2", 151.0, 62.0),
        ("eq_plus_a", "+", 205.5, 25.0),
        ("eq_3x_a", "3x", 255.0, 58.0),
        ("eq_plus_b", "+", 304.5, 25.0),
        ("eq_3x_b", "3x", 354.0, 58.0),
        ("eq_plus_corner", "+", 403.5, 25.0),
        ("eq_corner9", "9", 438.0, 28.0),
        ("eq_equal_main", "=", 472.5, 25.0),
        ("eq_rhs7", "7", 507.0, 28.0),
        ("eq_plus_rhs", "+", 541.5, 25.0),
        ("eq_rhs9", "9", 576.0, 28.0),
        ("eq_equal_result", "=", 610.5, 25.0),
        ("eq_16", "16", 653.5, 45.0),
    )
    return {
        _node_id(component_id, suffix): _token(
            component_id,
            suffix,
            latex,
            x,
            100.0,
            width,
            style=_AMBER_EQUATION_STYLE
            if suffix in {"eq_corner9", "eq_rhs9", "eq_16"}
            else _EQUATION_STYLE,
        )
        for suffix, latex, x, width in tokens
    }


def _factored_equation(component_id: str, *, y: float = 100.0) -> NodeMap:
    tokens = (
        ("eq_factor", "(x+3)^2", 300.0, 150.0),
        ("eq_equal_result", "=", 403.5, 25.0),
        ("eq_16", "16", 470.0, 45.0),
    )
    return {
        _node_id(component_id, suffix): _token(
            component_id,
            suffix,
            latex,
            x,
            y,
            width,
            style=_AMBER_EQUATION_STYLE if suffix == "eq_16" else _EQUATION_STYLE,
        )
        for suffix, latex, x, width in tokens
    }


def _solution_tokens(component_id: str) -> NodeMap:
    root_equation_y, root_results_y = _SOLUTION_ROW_Y[1:]
    tokens = (
        ("root_lhs", "x+3", 280.0, root_equation_y, 100.0),
        ("root_equal", "=", 375.0, root_equation_y, 30.0),
        ("root_pm4", r"\pm 4", 440.0, root_equation_y, 70.0),
        ("root_x_left", "x", 210.0, root_results_y, 30.0),
        ("root_eq_left", "=", 250.0, root_results_y, 30.0),
        ("root_one", "1", 290.0, root_results_y, 30.0),
        ("root_or", r"\text{or}", 390.0, root_results_y, 100.0),
        ("root_x_right", "x", 500.0, root_results_y, 30.0),
        ("root_eq_right", "=", 540.0, root_results_y, 30.0),
        ("root_neg7", "-7", 590.0, root_results_y, 50.0),
    )
    return {
        _node_id(component_id, suffix): _token(
            component_id,
            suffix,
            latex,
            x,
            y,
            width,
            style=_AMBER_EQUATION_STYLE if suffix in {"root_one", "root_neg7"} else _EQUATION_STYLE,
        )
        for suffix, latex, x, y, width in tokens
    }


def _geometry(component_id: str, checkpoint_index: int) -> NodeMap:
    if checkpoint_index < 1:
        return {}
    arranged = checkpoint_index >= 3
    if arranged:
        square = ((240.0, 240.0), (420.0, 240.0), (420.0, 420.0), (240.0, 420.0))
        strip_a = ((240.0, 420.0), (420.0, 420.0), (420.0, 474.0), (240.0, 474.0))
        strip_b = ((420.0, 240.0), (474.0, 240.0), (474.0, 420.0), (420.0, 420.0))
        centers = ((330.0, 315.0), (330.0, 428.0), (447.0, 315.0))
    else:
        # Keep each detached piece on the same side of the square it will
        # eventually occupy. Straight interpolation can then close the gaps
        # without one identity cutting through another on its way into place.
        square = ((210.0, 210.0), (390.0, 210.0), (390.0, 390.0), (210.0, 390.0))
        strip_a = ((210.0, 450.0), (390.0, 450.0), (390.0, 504.0), (210.0, 504.0))
        strip_b = ((450.0, 210.0), (504.0, 210.0), (504.0, 390.0), (450.0, 390.0))
        centers = ((300.0, 285.0), (300.0, 458.0), (477.0, 285.0))

    nodes: tuple[SceneNode, ...] = (
        _path(component_id, "x2_square", square, _GEOMETRY_STYLE),
        _path(component_id, "strip_a", strip_a, _STRIP_A_STYLE),
        _path(component_id, "strip_b", strip_b, _STRIP_B_STYLE),
        _token(component_id, "area_x2", "x^2", *centers[0], 70.0, style=_LABEL_STYLE),
        _token(component_id, "area_3x_a", "3x", *centers[1], 50.0, style=_LABEL_STYLE),
        _token(component_id, "area_3x_b", "3x", *centers[2], 50.0, style=_LABEL_STYLE),
    )
    return {node.id: node for node in nodes}


def _corner(component_id: str, *, filled: bool) -> NodeMap:
    path = _path(
        component_id,
        "corner",
        ((420.0, 420.0), (474.0, 420.0), (474.0, 474.0), (420.0, 474.0)),
        _FILLED_CORNER_STYLE if filled else _MISSING_STYLE,
    )
    area = _token(
        component_id,
        "corner_area",
        "9" if filled else "?",
        447.0,
        425.0,
        34.0,
        height=42.0,
        style=_DETAIL_STYLE,
    )
    return {path.id: path, area.id: area}


def _corner_detail(component_id: str) -> NodeMap:
    nodes = (
        _token(component_id, "corner_dim_h", "3", 447.0, 475.0, 28.0, style=_DETAIL_STYLE),
        _token(component_id, "corner_dim_v", "3", 486.0, 425.0, 28.0, style=_DETAIL_STYLE),
        _token(
            component_id,
            "corner_calc",
            r"3\times3=9",
            550.0,
            480.0,
            125.0,
            style=_DETAIL_STYLE,
        ),
    )
    return {node.id: node for node in nodes}


def _desired_nodes(
    component_id: str,
    checkpoint: CompletingSquareMainCheckpoint,
    *,
    corner_clarified: bool,
) -> NodeMap:
    index = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(checkpoint)
    if index <= 1:
        nodes = _initial_equation(component_id)
    elif index <= 4:
        nodes = _split_equation(component_id)
    elif index == 5:
        nodes = _completed_equation(component_id)
    else:
        factored_y = _SOLUTION_ROW_Y[0] if index == 7 else 100.0
        nodes = _factored_equation(component_id, y=factored_y)
        if index == 7:
            nodes.update(_solution_tokens(component_id))

    nodes.update(_geometry(component_id, index))
    if index >= 4:
        nodes.update(_corner(component_id, filled=index >= 5))
    if corner_clarified and checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER:
        nodes.update(_corner_detail(component_id))
    return nodes


def _patch(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base_nodes: tuple[SceneNode, ...],
    desired_nodes: NodeMap,
) -> tuple[ScenePatchDraft, tuple[SceneNode, ...]]:
    base_by_id = {node.id: node for node in base_nodes}
    changed_ids = {
        node_id for node_id, node in desired_nodes.items() if base_by_id.get(node_id) != node
    }
    removed_ids = set(base_by_id).difference(desired_nodes)
    target_ids = sorted(changed_ids | removed_ids)
    operations = tuple(
        PutSceneOperation(op="put", node=desired_nodes[node_id])
        if node_id in desired_nodes
        else RemoveSceneOperation(op="remove", id=node_id)
        for node_id in target_ids
    )
    if not operations:
        raise CompletingSquareCompilationError("a checkpoint must make a visible scene change")
    if len(operations) > MAX_PATCH_OPERATIONS:
        raise CompletingSquareCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the {MAX_PATCH_OPERATIONS}-operation budget"
        )

    patch = ScenePatchDraft(
        patch_id=f"{component_id}__cp_{checkpoint_id.value}",
        narration=_NARRATION[checkpoint_id],
        operations=operations,
    )
    if (
        len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
        > MAX_NDJSON_FRAME_BYTES
    ):
        raise CompletingSquareCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the canonical 64 KiB budget"
        )

    result_order = [node.id for node in base_nodes if node.id in desired_nodes]
    result_order.extend(node_id for node_id in target_ids if node_id not in base_by_id)
    result_nodes = tuple(desired_nodes[node_id] for node_id in result_order)
    if {node.id: node for node in result_nodes} != desired_nodes:
        raise CompletingSquareCompilationError("checkpoint diff did not materialize its target")
    return patch, result_nodes


def _viewport_map(
    pair: tuple[tuple[float, float, float, float], tuple[float, float, float, float]],
) -> LayoutViewportMapV1:
    cinematic, compact = pair
    return LayoutViewportMapV1(
        cinematic=ViewportPoseV1(
            x=cinematic[0], y=cinematic[1], width=cinematic[2], height=cinematic[3]
        ),
        compact=ViewportPoseV1(x=compact[0], y=compact[1], width=compact[2], height=compact[3]),
    )


def _base_viewports(state: CompletingSquareState) -> LayoutViewportMapV1:
    if state.last_main_checkpoint is None:
        return _viewport_map(_INITIAL_VIEWPORTS)
    if (
        state.last_main_checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER
        and state.corner_clarified
    ):
        return _viewport_map(_RESULT_VIEWPORTS[CompletingSquareCheckpointId.CORNER_DETAIL])
    return _viewport_map(
        _RESULT_VIEWPORTS[CompletingSquareCheckpointId(state.last_main_checkpoint.value)]
    )


def _cue_plan(
    checkpoint_id: CompletingSquareCheckpointId,
    patch: ScenePatchDraft,
    base_nodes: tuple[SceneNode, ...],
) -> ChoreographyPlanV1:
    base_by_id = {node.id: node for node in base_nodes}
    enter = sorted(
        operation.target_id
        for operation in patch.operations
        if isinstance(operation, PutSceneOperation) and operation.target_id not in base_by_id
    )
    exit_ = sorted(
        operation.target_id
        for operation in patch.operations
        if isinstance(operation, RemoveSceneOperation)
    )
    transform = sorted(
        operation.target_id
        for operation in patch.operations
        if isinstance(operation, PutSceneOperation) and operation.target_id in base_by_id
    )
    component_id = patch.patch_id.split("__cp_", maxsplit=1)[0]
    emphasis_suffixes = {
        CompletingSquareCheckpointId.MISSING_CORNER: ("corner", "corner_area"),
        CompletingSquareCheckpointId.CORNER_DETAIL: (
            "corner_calc",
            "corner_dim_h",
            "corner_dim_v",
        ),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
            "corner",
            "eq_corner9",
            "eq_rhs9",
        ),
        CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_factor", "x2_square"),
        CompletingSquareCheckpointId.SOLVE_ROOTS: ("root_neg7", "root_one"),
    }.get(checkpoint_id, ())
    focus_suffixes = {
        CompletingSquareCheckpointId.PROBLEM: ("eq_6x", "eq_x2"),
        CompletingSquareCheckpointId.AREA_MODEL: ("strip_a", "strip_b", "x2_square"),
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: ("eq_3x_a", "eq_3x_b"),
        CompletingSquareCheckpointId.REARRANGE_HALVES: ("strip_a", "strip_b"),
        CompletingSquareCheckpointId.MISSING_CORNER: ("corner",),
        CompletingSquareCheckpointId.CORNER_DETAIL: ("corner", "corner_calc"),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: ("corner", "x2_square"),
        CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_factor",),
        CompletingSquareCheckpointId.SOLVE_ROOTS: ("root_neg7", "root_one"),
    }[checkpoint_id]

    cues = []
    if enter:
        cues.append(EnterCueV1(target_ids=tuple(enter)))
    if exit_:
        cues.append(ExitCueV1(target_ids=tuple(exit_)))
    if transform:
        cues.append(TransformCueV1(target_ids=tuple(transform)))
    if emphasis_suffixes:
        cues.append(
            EmphasizeCueV1(
                target_ids=tuple(
                    sorted(_node_id(component_id, suffix) for suffix in emphasis_suffixes)
                )
            )
        )
    cues.append(
        FocusCueV1(
            target_ids=tuple(sorted(_node_id(component_id, suffix) for suffix in focus_suffixes))
        )
    )
    duration_ms, hold_after_ms, easing = _TIMING[checkpoint_id]
    return ChoreographyPlanV1(
        phase=ChoreographyPhaseV1(
            cues=tuple(cues),
            duration_ms=duration_ms,
            easing=easing,
            hold_after_ms=hold_after_ms,
        )
    )


def _checkpoint(
    checkpoint_id: CompletingSquareCheckpointId,
    base_component: CompletingSquareState,
    result_component: CompletingSquareState,
    base_nodes: tuple[SceneNode, ...],
    desired_nodes: NodeMap,
) -> CompiledCheckpointBlueprint:
    patch, result_nodes = _patch(
        base_component.id,
        checkpoint_id,
        base_nodes,
        desired_nodes,
    )
    presentation = PresentationCheckpointV1(
        checkpoint_id=checkpoint_id.value,
        checkpoint_narration=_NARRATION[checkpoint_id],
        base_viewports=_base_viewports(base_component),
        result_viewports=_viewport_map(_RESULT_VIEWPORTS[checkpoint_id]),
    )
    return CompiledCheckpointBlueprint(
        checkpoint_id=checkpoint_id,
        base_component=base_component,
        result_component=result_component,
        base_nodes=base_nodes,
        result_nodes=result_nodes,
        patch=patch,
        choreography=_cue_plan(checkpoint_id, patch, base_nodes),
        presentation=presentation,
    )


def _nodes_for_state(state: CompletingSquareState) -> tuple[SceneNode, ...]:
    nodes: tuple[SceneNode, ...] = ()
    component = CompletingSquareState(id=state.id)
    for checkpoint in checkpoint_prefix(state.last_main_checkpoint):
        desired = _desired_nodes(
            state.id,
            checkpoint,
            corner_clarified=False,
        )
        _, nodes = _patch(
            state.id,
            CompletingSquareCheckpointId(checkpoint.value),
            nodes,
            desired,
        )
        component = CompletingSquareState(
            id=state.id,
            last_main_checkpoint=checkpoint,
            corner_clarified=component.corner_clarified,
        )
        if checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER and state.corner_clarified:
            detail = _desired_nodes(
                state.id,
                checkpoint,
                corner_clarified=True,
            )
            _, nodes = _patch(
                state.id,
                CompletingSquareCheckpointId.CORNER_DETAIL,
                nodes,
                detail,
            )
            component = CompletingSquareState(
                id=state.id,
                last_main_checkpoint=checkpoint,
                corner_clarified=True,
            )
    if component != state:
        raise CompletingSquareCompilationError("semantic frontier is not materializable")
    return nodes


def _compile_blueprints(
    beat: RoutedChoreographyBeatV2,
    base_component: CompletingSquareState | None,
) -> CompiledCheckpointBlueprintBatch:
    """Build exactly the missing deterministic component-local suffix."""

    if base_component is None:
        base_component = CompletingSquareState(id=beat.component_id)
    if base_component.id != beat.component_id:
        raise CompletingSquareCompilationError("routed beat componentId must match the base state")

    base_nodes = _nodes_for_state(base_component)
    checkpoints: list[CompiledCheckpointBlueprint] = []
    current_component = base_component
    current_nodes = base_nodes

    if isinstance(beat.route, ClarifyCornerRouteV2):
        if (
            current_component.last_main_checkpoint
            is not CompletingSquareMainCheckpoint.MISSING_CORNER
            or current_component.corner_clarified
        ):
            raise CompletingSquareCompilationError(
                "corner_detail is legal only at the unclarified missing_corner frontier"
            )
        result_component = CompletingSquareState(
            id=beat.component_id,
            last_main_checkpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
            corner_clarified=True,
        )
        checkpoint = _checkpoint(
            CompletingSquareCheckpointId.CORNER_DETAIL,
            current_component,
            result_component,
            current_nodes,
            _desired_nodes(
                beat.component_id,
                CompletingSquareMainCheckpoint.MISSING_CORNER,
                corner_clarified=True,
            ),
        )
        return CompiledCheckpointBlueprintBatch(
            beat=beat,
            base_component=base_component,
            result_component=result_component,
            checkpoints=(checkpoint,),
        )

    if not isinstance(beat.route, AdvanceChoreographyRouteV2):
        raise CompletingSquareCompilationError("unsupported choreography route")
    target = checkpoints_through(beat.route.target_stage)
    current = checkpoint_prefix(current_component.last_main_checkpoint)
    if current != target[: len(current)]:
        raise CompletingSquareCompilationError("a completing-square component cannot move backward")

    for main_checkpoint in target[len(current) :]:
        checkpoint_id = CompletingSquareCheckpointId(main_checkpoint.value)
        result_component = CompletingSquareState(
            id=beat.component_id,
            last_main_checkpoint=main_checkpoint,
            corner_clarified=current_component.corner_clarified,
        )
        checkpoint = _checkpoint(
            checkpoint_id,
            current_component,
            result_component,
            current_nodes,
            _desired_nodes(
                beat.component_id,
                main_checkpoint,
                corner_clarified=result_component.corner_clarified,
            ),
        )
        checkpoints.append(checkpoint)
        current_component = result_component
        current_nodes = checkpoint.result_nodes

    return CompiledCheckpointBlueprintBatch(
        beat=beat,
        base_component=base_component,
        result_component=current_component,
        checkpoints=tuple(checkpoints),
    )


def _target_component(
    beat: RoutedChoreographyBeatV2,
    scene: SemanticSceneState,
) -> CompletingSquareState | None:
    component = next(
        (candidate for candidate in scene.components if candidate.id == beat.component_id),
        None,
    )
    if component is None:
        return None
    if not isinstance(component, CompletingSquareState):
        raise CompletingSquareCompilationError(
            "routed beat componentId belongs to a different semantic component kind"
        )
    return component


def _validate_base_realization(
    component_id: str,
    component: CompletingSquareState | None,
    scene: SceneState,
) -> None:
    state = component or CompletingSquareState(id=component_id)
    expected = _nodes_for_state(state)
    prefix = f"{component_id}__"
    actual = tuple(node for node in scene.nodes if node.id.startswith(prefix))
    if actual != expected:
        raise CompletingSquareCompilationError(
            "base low-level scene does not match the semantic completing-square frontier"
        )


def _apply_scene_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
            continue
        if operation.id not in nodes:
            raise CompletingSquareCompilationError("checkpoint removes an absent node")
        del nodes[operation.id]
        order.remove(operation.id)
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _advance_semantic_scene(
    scene: SemanticSceneState,
    component: CompletingSquareState,
    *,
    certificate_head_sha256: str | None,
) -> SemanticSceneState:
    found = False
    components = []
    for existing in scene.components:
        if existing.id == component.id:
            components.append(component)
            found = True
        else:
            components.append(existing)
    if not found:
        components.append(component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=tuple(components),
        certificate_head_sha256=certificate_head_sha256,
    )


def compile_checkpoint_beat(
    beat: RoutedChoreographyBeatV2,
    *,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> CompiledCheckpointBeatV2:
    """Compile and certify exactly the missing checkpoint suffix for one route."""

    if base_scene.revision != base_semantic_scene.revision:
        raise CompletingSquareCompilationError("base low-level and semantic revisions must match")
    component = _target_component(beat, base_semantic_scene)
    _validate_base_realization(beat.component_id, component, base_scene)
    blueprints = _compile_blueprints(beat, component)

    routed_beat_digest = routed_choreography_beat_sha256(beat)
    current_scene = base_scene
    current_semantic_scene = base_semantic_scene
    checkpoints: list[CompiledCheckpointV2] = []
    for blueprint in blueprints.checkpoints:
        result_scene = _apply_scene_patch(current_scene, blueprint.patch)
        result_semantic_without_head = _advance_semantic_scene(
            current_semantic_scene,
            blueprint.result_component,
            certificate_head_sha256=None,
        )
        receipt = verify_completing_square_checkpoint(
            beat.component_id,
            blueprint.checkpoint_id,
            current_scene,
            result_scene,
            blueprint.patch,
            blueprint.presentation,
            blueprint.choreography,
        )
        certificate_body = CheckpointCompilerCertificateBodyV2(
            beat_id=beat.beat_id,
            routed_beat_sha256=routed_beat_digest,
            component_id=beat.component_id,
            checkpoint_id=blueprint.checkpoint_id,
            base_revision=current_scene.revision,
            result_revision=result_scene.revision,
            base_low_level_scene_sha256=low_level_scene_sha256(current_scene),
            result_low_level_scene_sha256=low_level_scene_sha256(result_scene),
            base_semantic_scene_sha256=semantic_scene_sha256(current_semantic_scene),
            result_semantic_scene_sha256=semantic_scene_sha256(result_semantic_without_head),
            patch_sha256=scene_patch_sha256(blueprint.patch),
            receipt_sha256=checkpoint_receipt_sha256(receipt),
            presentation_checkpoint=blueprint.presentation,
            choreography_sha256=choreography_plan_sha256(blueprint.choreography),
            previous_certificate_sha256=(current_semantic_scene.certificate_head_sha256),
        )
        certificate = CheckpointCompilerCertificateV2(
            body=certificate_body,
            certificate_sha256=checkpoint_certificate_sha256(certificate_body),
        )
        compiled = CompiledCheckpointV2(
            beat=beat,
            checkpoint_id=blueprint.checkpoint_id,
            patch=blueprint.patch,
            receipt=receipt,
            presentation=blueprint.presentation,
            choreography=blueprint.choreography,
            certificate=certificate,
        )
        checkpoints.append(compiled)
        current_scene = result_scene
        current_semantic_scene = _advance_semantic_scene(
            current_semantic_scene,
            blueprint.result_component,
            certificate_head_sha256=certificate.certificate_sha256,
        )

    return CompiledCheckpointBeatV2(
        beat=beat,
        base_scene=base_scene,
        result_scene=current_scene,
        base_semantic_scene=base_semantic_scene,
        result_semantic_scene=current_semantic_scene,
        checkpoints=tuple(checkpoints),
    )


__all__ = [
    "COMPLETING_SQUARE_COMPILER_VERSION",
    "CompiledCheckpointBeatV2",
    "CompiledCheckpointBlueprint",
    "CompiledCheckpointBlueprintBatch",
    "CompletingSquareCompilationError",
    "compile_checkpoint_beat",
]

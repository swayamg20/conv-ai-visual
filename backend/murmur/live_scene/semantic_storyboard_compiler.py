"""Deterministic visual compiler for Gate 1.8 projectile storyboards.

The provider chooses one catalog record.  This module owns every drawable
fact: physics, copy, geometry, stable identity, paint order, camera, and
timing.  One record therefore produces one blueprint and never a suffix or an
implicit prerequisite.  Physics helpers stay local so the independent
verifier can recompute the same obligations from primitives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, TypeAlias

from murmur.live_scene.choreography_contracts import (
    ChoreographyEasing,
    ChoreographyId,
    ChoreographyPhaseV2,
    ChoreographyPlanV2,
    EmphasizeCueV1,
    EnterCueV1,
    FocusCueV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    TracePathCueV2,
    TransformCueV1,
    ViewportPoseV1,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    LatexTokenSceneNode,
    LineSceneNode,
    PathSceneNode,
    PutSceneOperation,
    SceneNode,
    ScenePatchDraft,
    ScenePresentation,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1
from murmur.live_scene.semantic_storyboard_contracts import (
    PROJECTILE_STORYBOARD_COMPONENT_ID,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardClaimId,
    StoryboardConceptId,
    StoryboardEvidenceId,
    StoryboardTrajectoryId,
    TraceStoryboardRecordV1,
    semantic_storyboard_program_sha256,
    storyboard_record_slug,
)

Point: TypeAlias = tuple[float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

STORYBOARD_ANCHOR_CHECKPOINT_ID: Final = "storyboard-anchor"
MAX_STORYBOARD_ANCHOR_OPERATIONS: Final = 13
MAX_STORYBOARD_BEAT_OPERATIONS: Final = 7

_GRAVITY_MPS2 = 10.0
_ORIGIN: Point = (72.0, 468.0)
_PATH_SAMPLES = 65
_LAUNCH_RAY_LENGTH = 78.0
_PANEL_CENTER_X = 690.0
_PANEL_WIDTH = 188.0

_DRAW = ScenePresentation(enter="draw", exit="fade")
_FADE = ScenePresentation(enter="fade", exit="fade")

_AXIS_STYLE = {
    "stroke": "hsl(var(--chalk-soft))",
    "strokeWidth": 2.0,
    "opacity": 0.68,
    "roughness": 0.0,
}
_RULE_STYLE = {**_AXIS_STYLE, "strokeWidth": 1.5, "opacity": 0.36}
_LOWER_LINE_STYLE = {
    "stroke": "hsl(var(--amber))",
    "strokeWidth": 4.0,
    "opacity": 1.0,
    "roughness": 0.0,
}
_HIGHER_LINE_STYLE = {
    "stroke": "hsl(var(--lavender))",
    "strokeWidth": 4.0,
    "opacity": 1.0,
    "roughness": 0.0,
}
_LOWER_PATH_STYLE = {**_LOWER_LINE_STYLE, "fill": "none"}
_HIGHER_PATH_STYLE = {**_HIGHER_LINE_STYLE, "fill": "none"}
_LOWER_MARKER_STYLE = {**_LOWER_LINE_STYLE, "fill": "hsl(var(--amber))", "strokeWidth": 2.0}
_HIGHER_MARKER_STYLE = {**_HIGHER_LINE_STYLE, "fill": "transparent", "strokeWidth": 2.0}
_LOWER_BRACKET_STYLE = {**_LOWER_LINE_STYLE, "strokeWidth": 2.5, "opacity": 0.9}
_HIGHER_BRACKET_STYLE = {**_HIGHER_LINE_STYLE, "strokeWidth": 2.5, "opacity": 0.9}

_CHALK_TEXT = {"color": "hsl(var(--chalk))", "fontSize": 22.0, "opacity": 1.0}
_SOFT_TEXT = {"color": "hsl(var(--chalk-soft))", "fontSize": 17.0, "opacity": 0.82}
_AMBER_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--amber))"}
_LAVENDER_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--lavender))"}
_SAGE_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--sage))"}
_PANEL_TEXT = {**_CHALK_TEXT, "fontSize": 14.0}
_PANEL_AMBER_TEXT = {**_AMBER_TEXT, "fontSize": 19.0}
_DYNAMIC_AMBER_TEXT = {**_AMBER_TEXT, "fontSize": 16.0}
_DYNAMIC_LAVENDER_TEXT = {**_LAVENDER_TEXT, "fontSize": 16.0}
_DYNAMIC_SAGE_TEXT = {**_SAGE_TEXT, "fontSize": 16.0}

_ANCHOR_PAINT = (
    "ground_axis",
    "vertical_axis",
    "panel_rule",
    "launch_ray_lower",
    "launch_ray_higher",
    "projectile_marker_lower",
    "projectile_marker_higher",
    "axis_x_label",
    "axis_y_label",
    "launch_angle_lower",
    "launch_angle_higher",
    "givens",
    "range_relation",
)
_TRAJECTORY_EVIDENCE = (
    StoryboardEvidenceId.LOWER_TRAJECTORY,
    StoryboardEvidenceId.HIGHER_TRAJECTORY,
)


class SemanticStoryboardCompilationError(ValueError):
    """Raised before an invalid visual candidate can reach certification."""


@dataclass(frozen=True)
class SemanticStoryboardCheckpointBlueprint:
    """One provider-free anchor or one atomic model-selected transition."""

    checkpoint_id: ChoreographyId
    base_component: ProjectileStoryboardStateV1 | None
    result_component: ProjectileStoryboardStateV1
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV2
    presentation: PresentationCheckpointV1


@dataclass(frozen=True)
class _Trajectory:
    angle_deg: int
    range_m: float
    height_m: float
    flight_time_s: float
    points: tuple[Point, ...]


@dataclass(frozen=True)
class _VisualModel:
    lower: _Trajectory
    higher: _Trajectory


def _node_id(suffix: str) -> str:
    return f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__{suffix}"


def _format_value(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".")


def _raw_kinematics(speed_mps: int, angle_deg: int) -> tuple[float, float, float]:
    angle = math.radians(angle_deg)
    range_m = speed_mps * speed_mps * math.sin(2.0 * angle) / _GRAVITY_MPS2
    height_m = speed_mps * speed_mps * math.sin(angle) ** 2 / (2.0 * _GRAVITY_MPS2)
    flight_time_s = 2.0 * speed_mps * math.sin(angle) / _GRAVITY_MPS2
    return range_m, height_m, flight_time_s


def _visual_model(problem: PairedProjectileComparisonSpecV1) -> _VisualModel:
    lower_raw = _raw_kinematics(problem.speed_mps, problem.lower_angle_deg)
    higher_raw = _raw_kinematics(problem.speed_mps, problem.higher_angle_deg)
    lower_range, lower_height, lower_time = lower_raw
    higher_range, higher_height, higher_time = higher_raw
    if problem.has_complementary_angles:
        # One canonical value makes the analytically equal endpoints bit-identical.
        higher_range = lower_range
    scale = min(
        12.0,
        476.0 / max(lower_range, higher_range),
        240.0 / max(lower_height, higher_height),
    )

    def trajectory(
        angle_deg: int,
        range_m: float,
        height_m: float,
        flight_time_s: float,
    ) -> _Trajectory:
        points = tuple(
            (
                _ORIGIN[0] + scale * range_m * index / (_PATH_SAMPLES - 1),
                _ORIGIN[1]
                - scale
                * 4.0
                * height_m
                * (index / (_PATH_SAMPLES - 1))
                * (1.0 - index / (_PATH_SAMPLES - 1)),
            )
            for index in range(_PATH_SAMPLES)
        )
        return _Trajectory(angle_deg, range_m, height_m, flight_time_s, points)

    return _VisualModel(
        lower=trajectory(
            problem.lower_angle_deg,
            lower_range,
            lower_height,
            lower_time,
        ),
        higher=trajectory(
            problem.higher_angle_deg,
            higher_range,
            higher_height,
            higher_time,
        ),
    )


def _line(suffix: str, start: Point, end: Point, style: dict[str, object]) -> LineSceneNode:
    return LineSceneNode(
        id=_node_id(suffix),
        kind="line",
        presentation=_DRAW,
        points=(start, end),
        style=style,
    )


def _path(
    suffix: str,
    points: tuple[Point, ...],
    *,
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode(
        id=_node_id(suffix),
        kind="path",
        presentation=_DRAW,
        points=points,
        closed=False,
        style=style,
    )


def _ring(
    suffix: str,
    center: Point,
    radius: float,
    *,
    style: dict[str, object],
) -> PathSceneNode:
    points = tuple(
        (
            center[0] + radius * math.cos(math.tau * index / 16.0),
            center[1] + radius * math.sin(math.tau * index / 16.0),
        )
        for index in range(16)
    )
    return PathSceneNode(
        id=_node_id(suffix),
        kind="path",
        presentation=_DRAW,
        points=points,
        closed=True,
        style=style,
    )


def _token(
    suffix: str,
    latex: str,
    x: float,
    y: float,
    width: float,
    *,
    height: float = 38.0,
    style: dict[str, object] | None = None,
) -> LatexTokenSceneNode:
    return LatexTokenSceneNode(
        id=_node_id(suffix),
        kind="latex_token",
        presentation=_FADE,
        x=x,
        y=y,
        width=width,
        height=height,
        anchor="middle",
        latex=latex,
        style=style or _CHALK_TEXT,
    )


def _launch_end(angle_deg: int) -> Point:
    angle = math.radians(angle_deg)
    return (
        _ORIGIN[0] + _LAUNCH_RAY_LENGTH * math.cos(angle),
        _ORIGIN[1] - _LAUNCH_RAY_LENGTH * math.sin(angle),
    )


def _angle_label_position(angle_deg: int, radius: float) -> Point:
    angle = math.radians(angle_deg)
    return (
        _ORIGIN[0] + radius * math.cos(angle),
        _ORIGIN[1] - radius * math.sin(angle) - 15.0,
    )


def _range_relation_node(symbol: str) -> LatexTokenSceneNode:
    return _token(
        "range_relation",
        rf"R_L\ {symbol}\ R_H",
        _PANEL_CENTER_X,
        396.0,
        _PANEL_WIDTH,
        height=46.0,
        style=_SAGE_TEXT if symbol != "?" else _SOFT_TEXT,
    )


def _anchor_nodes(problem: PairedProjectileComparisonSpecV1) -> NodeMap:
    lower_label = _angle_label_position(problem.lower_angle_deg, 105.0)
    higher_label = _angle_label_position(problem.higher_angle_deg, 120.0)
    nodes = (
        _line("ground_axis", (44.0, 468.0), (568.0, 468.0), _AXIS_STYLE),
        _line("vertical_axis", (72.0, 488.0), (72.0, 196.0), _AXIS_STYLE),
        _line("panel_rule", (582.0, 80.0), (582.0, 576.0), _RULE_STYLE),
        _line("launch_ray_lower", _ORIGIN, _launch_end(problem.lower_angle_deg), _LOWER_LINE_STYLE),
        _line(
            "launch_ray_higher",
            _ORIGIN,
            _launch_end(problem.higher_angle_deg),
            _HIGHER_LINE_STYLE,
        ),
        _ring("projectile_marker_lower", _ORIGIN, 5.0, style=_LOWER_MARKER_STYLE),
        _ring("projectile_marker_higher", _ORIGIN, 8.0, style=_HIGHER_MARKER_STYLE),
        _token("axis_x_label", "x", 555.0, 472.0, 24.0, height=28.0, style=_SOFT_TEXT),
        _token("axis_y_label", "y", 58.0, 194.0, 24.0, height=28.0, style=_SOFT_TEXT),
        _token(
            "launch_angle_lower",
            rf"{problem.lower_angle_deg}^\circ",
            lower_label[0],
            lower_label[1],
            58.0,
            height=36.0,
            style=_AMBER_TEXT,
        ),
        _token(
            "launch_angle_higher",
            rf"{problem.higher_angle_deg}^\circ",
            higher_label[0],
            higher_label[1],
            58.0,
            height=36.0,
            style=_LAVENDER_TEXT,
        ),
        _token(
            "givens",
            rf"\begin{{aligned}}v_0&={problem.speed_mps}\,\mathrm{{m/s}}\\"
            rf"\theta_L&={problem.lower_angle_deg}^\circ,\quad "
            rf"\theta_H={problem.higher_angle_deg}^\circ\end{{aligned}}",
            _PANEL_CENTER_X,
            92.0,
            _PANEL_WIDTH,
            height=64.0,
            style=_PANEL_TEXT,
        ),
        _range_relation_node("?"),
    )
    return {node.id: node for node in nodes}


def _trajectory_for(record: TraceStoryboardRecordV1, visual: _VisualModel) -> _Trajectory:
    if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE:
        return visual.lower
    return visual.higher


def _trajectory_nodes(record: TraceStoryboardRecordV1, visual: _VisualModel) -> NodeMap:
    lower = record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE
    suffix = "lower" if lower else "higher"
    trajectory = visual.lower if lower else visual.higher
    style = _LOWER_PATH_STYLE if lower else _HIGHER_PATH_STYLE
    marker_style = _LOWER_MARKER_STYLE if lower else _HIGHER_MARKER_STYLE
    marker_radius = 5.0 if lower else 8.0
    nodes = (
        _path(f"trajectory_{suffix}", trajectory.points, style=style),
        _ring(
            f"projectile_marker_{suffix}",
            trajectory.points[-1],
            marker_radius,
            style=marker_style,
        ),
    )
    return {node.id: node for node in nodes}


def _reveal_nodes(
    record: RevealStoryboardRecordV1, problem: PairedProjectileComparisonSpecV1
) -> NodeMap:
    if record.concept_id is StoryboardConceptId.RANGE_FORMULA:
        node = _token(
            "range_formula",
            r"R(\theta)=\frac{v_0^2}{g}\sin(2\theta)",
            _PANEL_CENTER_X,
            164.0,
            _PANEL_WIDTH,
            height=48.0,
            style=_PANEL_AMBER_TEXT,
        )
    else:
        node = _token(
            "complementary_angles",
            rf"{problem.lower_angle_deg}^\circ+{problem.higher_angle_deg}^\circ=90^\circ",
            _PANEL_CENTER_X,
            224.0,
            _PANEL_WIDTH,
            height=42.0,
            style=_LAVENDER_TEXT,
        )
    return {node.id: node}


def _range_symbol(visual: _VisualModel) -> str:
    if math.isclose(visual.lower.range_m, visual.higher.range_m, abs_tol=1e-12):
        return "="
    return "<" if visual.lower.range_m < visual.higher.range_m else ">"


def _range_visual_nodes(visual: _VisualModel) -> NodeMap:
    lower_end = visual.lower.points[-1]
    higher_end = visual.higher.points[-1]
    lower_midpoint = (_ORIGIN[0] + lower_end[0]) / 2.0
    higher_midpoint = (_ORIGIN[0] + higher_end[0]) / 2.0
    nodes = (
        _line("range_bracket_lower", (72.0, 500.0), (lower_end[0], 500.0), _LOWER_BRACKET_STYLE),
        _line(
            "range_bracket_higher",
            (72.0, 548.0),
            (higher_end[0], 548.0),
            _HIGHER_BRACKET_STYLE,
        ),
        _ring("landing_ring_lower", lower_end, 5.0, style=_LOWER_MARKER_STYLE),
        _ring("landing_ring_higher", higher_end, 8.0, style=_HIGHER_MARKER_STYLE),
        _token(
            "range_value_lower",
            rf"R_L={_format_value(visual.lower.range_m)}\,\mathrm{{m}}",
            lower_midpoint,
            470.0,
            126.0,
            height=28.0,
            style=_DYNAMIC_AMBER_TEXT,
        ),
        _token(
            "range_value_higher",
            rf"R_H={_format_value(visual.higher.range_m)}\,\mathrm{{m}}",
            higher_midpoint,
            518.0,
            126.0,
            height=28.0,
            style=_DYNAMIC_LAVENDER_TEXT,
        ),
        _range_relation_node(_range_symbol(visual)),
    )
    return {node.id: node for node in nodes}


def _range_analytic_nodes(
    problem: PairedProjectileComparisonSpecV1, visual: _VisualModel
) -> NodeMap:
    symbol = _range_symbol(visual)
    doubled_lower = 2 * problem.lower_angle_deg
    doubled_higher = 2 * problem.higher_angle_deg
    node = _token(
        "sine_relation",
        rf"\sin({doubled_lower}^\circ)\ {symbol}\ \sin({doubled_higher}^\circ)",
        _PANEL_CENTER_X,
        348.0,
        _PANEL_WIDTH,
        height=38.0,
        style=_DYNAMIC_SAGE_TEXT,
    )
    relation = _range_relation_node(symbol)
    return {node.id: node, relation.id: relation}


def _height_nodes(visual: _VisualModel) -> NodeMap:
    lower_apex = visual.lower.points[(_PATH_SAMPLES - 1) // 2]
    higher_apex = visual.higher.points[(_PATH_SAMPLES - 1) // 2]
    lower_label_x = min(510.0, lower_apex[0] + 64.0)
    higher_label_x = max(130.0, higher_apex[0] - 64.0)
    nodes = (
        _line("height_bracket_lower", (lower_apex[0], 468.0), lower_apex, _LOWER_BRACKET_STYLE),
        _line(
            "height_bracket_higher",
            (higher_apex[0], 468.0),
            higher_apex,
            _HIGHER_BRACKET_STYLE,
        ),
        _ring("apex_ring_lower", lower_apex, 5.0, style=_LOWER_MARKER_STYLE),
        _ring("apex_ring_higher", higher_apex, 8.0, style=_HIGHER_MARKER_STYLE),
        _token(
            "height_value_lower",
            rf"H_L={_format_value(visual.lower.height_m)}\,\mathrm{{m}}",
            lower_label_x,
            min(430.0, lower_apex[1] + 18.0),
            126.0,
            height=30.0,
            style=_DYNAMIC_AMBER_TEXT,
        ),
        _token(
            "height_value_higher",
            rf"H_H={_format_value(visual.higher.height_m)}\,\mathrm{{m}}",
            higher_label_x,
            min(430.0, higher_apex[1] + 18.0),
            126.0,
            height=30.0,
            style=_DYNAMIC_LAVENDER_TEXT,
        ),
        _token(
            "height_relation",
            r"H_H>H_L",
            _PANEL_CENTER_X,
            452.0,
            _PANEL_WIDTH,
            height=38.0,
            style=_SAGE_TEXT,
        ),
    )
    return {node.id: node for node in nodes}


def _flight_nodes(visual: _VisualModel) -> NodeMap:
    nodes = (
        _token(
            "flight_time_lower",
            rf"T_L={_format_value(visual.lower.flight_time_s)}\,\mathrm{{s}}",
            _PANEL_CENTER_X,
            494.0,
            _PANEL_WIDTH,
            height=30.0,
            style=_DYNAMIC_AMBER_TEXT,
        ),
        _token(
            "flight_time_higher",
            rf"T_H={_format_value(visual.higher.flight_time_s)}\,\mathrm{{s}}",
            _PANEL_CENTER_X,
            526.0,
            _PANEL_WIDTH,
            height=30.0,
            style=_DYNAMIC_LAVENDER_TEXT,
        ),
        _token(
            "flight_relation",
            r"T_H>T_L",
            _PANEL_CENTER_X,
            558.0,
            _PANEL_WIDTH,
            height=30.0,
            style=_DYNAMIC_SAGE_TEXT,
        ),
    )
    return {node.id: node for node in nodes}


def _uses_trajectory_evidence(record: RelateStoryboardRecordV1) -> bool:
    return record.evidence_ids == _TRAJECTORY_EVIDENCE


def _effect_nodes(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
    problem: PairedProjectileComparisonSpecV1,
    visual: _VisualModel,
) -> NodeMap:
    if isinstance(record, RevealStoryboardRecordV1):
        return _reveal_nodes(record, problem)
    if isinstance(record, TraceStoryboardRecordV1):
        return _trajectory_nodes(record, visual)
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if _uses_trajectory_evidence(record):
            return _range_visual_nodes(visual)
        return _range_analytic_nodes(problem, visual)
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return _height_nodes(visual)
    return _flight_nodes(visual)


def _effect_paint_manifest(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
) -> tuple[str, ...]:
    if isinstance(record, RevealStoryboardRecordV1):
        suffix = (
            "range_formula"
            if record.concept_id is StoryboardConceptId.RANGE_FORMULA
            else "complementary_angles"
        )
        return (suffix,)
    if isinstance(record, TraceStoryboardRecordV1):
        suffix = "lower" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "higher"
        return (f"trajectory_{suffix}", f"projectile_marker_{suffix}")
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if not _uses_trajectory_evidence(record):
            return ("sine_relation", "range_relation")
        return (
            "range_bracket_lower",
            "range_bracket_higher",
            "landing_ring_lower",
            "landing_ring_higher",
            "range_value_lower",
            "range_value_higher",
            "range_relation",
        )
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return (
            "height_bracket_lower",
            "height_bracket_higher",
            "apex_ring_lower",
            "apex_ring_higher",
            "height_value_lower",
            "height_value_higher",
            "height_relation",
        )
    return ("flight_time_lower", "flight_time_higher", "flight_relation")


def _paint_ordered_nodes(component: ProjectileStoryboardStateV1) -> tuple[SceneNode, ...]:
    visual = _visual_model(component.problem_spec)
    nodes = _anchor_nodes(component.problem_spec)
    order = [_node_id(suffix) for suffix in _ANCHOR_PAINT]
    if set(nodes) != set(order):
        raise SemanticStoryboardCompilationError("anchor paint manifest is incomplete")
    for record in component.accepted_records:
        effect = _effect_nodes(record, component.problem_spec, visual)
        manifest = tuple(_node_id(suffix) for suffix in _effect_paint_manifest(record))
        if set(effect) != set(manifest):
            raise SemanticStoryboardCompilationError(
                "storyboard effect paint manifest is incomplete"
            )
        for node_id in manifest:
            if node_id not in nodes:
                order.append(node_id)
            nodes[node_id] = effect[node_id]
    return tuple(nodes[node_id] for node_id in order)


def materialize_semantic_storyboard_nodes(
    component: ProjectileStoryboardStateV1,
) -> tuple[SceneNode, ...]:
    """Reconstruct the exact retained-DOM paint order for one semantic ledger."""

    if not isinstance(component, ProjectileStoryboardStateV1):
        raise TypeError("component must be a ProjectileStoryboardStateV1")
    return _paint_ordered_nodes(component)


def _patch(
    *,
    patch_id: str,
    narration: str,
    base_nodes: tuple[SceneNode, ...],
    result_nodes: tuple[SceneNode, ...],
    paint_manifest: tuple[str, ...],
    operation_limit: int,
) -> ScenePatchDraft:
    base = {node.id: node for node in base_nodes}
    result = {node.id: node for node in result_nodes}
    target_ids = tuple(_node_id(suffix) for suffix in paint_manifest)
    changed = {node_id for node_id, node in result.items() if base.get(node_id) != node}
    removed = set(base).difference(result)
    if removed or changed != set(target_ids):
        raise SemanticStoryboardCompilationError(
            "one storyboard checkpoint may change only its explicit paint manifest"
        )
    operations = tuple(PutSceneOperation(op="put", node=result[node_id]) for node_id in target_ids)
    if not 1 <= len(operations) <= operation_limit:
        raise SemanticStoryboardCompilationError(
            f"storyboard checkpoint exceeds its {operation_limit}-operation budget"
        )
    patch = ScenePatchDraft(patchId=patch_id, narration=narration, operations=operations)
    if (
        len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
        > MAX_NDJSON_FRAME_BYTES
    ):
        raise SemanticStoryboardCompilationError("storyboard checkpoint exceeds the 64 KiB budget")
    return patch


def _viewport(
    cinematic: tuple[float, float, float, float],
    compact: tuple[float, float, float, float],
) -> LayoutViewportMapV1:
    return LayoutViewportMapV1(
        cinematic=ViewportPoseV1(
            x=cinematic[0], y=cinematic[1], width=cinematic[2], height=cinematic[3]
        ),
        compact=ViewportPoseV1(x=compact[0], y=compact[1], width=compact[2], height=compact[3]),
    )


_ANCHOR_VIEWPORT = _viewport((0.0, 0.0, 800.0, 600.0), (24.0, 336.0, 320.0, 240.0))
_TRAJECTORY_VIEWPORT = _viewport((36.0, 204.0, 548.0, 324.0), (36.0, 160.0, 548.0, 411.0))
_RANGE_VISUAL_VIEWPORT = _viewport((36.0, 230.0, 548.0, 338.0), (36.0, 230.0, 548.0, 338.0))
_FORMULA_VIEWPORT = _viewport((548.0, 128.0, 252.0, 142.0), (548.0, 114.0, 252.0, 189.0))
_COMPLEMENTARY_VIEWPORT = _viewport(
    (548.0, 176.0, 252.0, 142.0),
    (548.0, 154.0, 252.0, 189.0),
)
_RANGE_ANALYTIC_VIEWPORT = _viewport(
    (492.0, 140.0, 308.0, 326.0),
    (492.0, 140.0, 308.0, 326.0),
)
_FLIGHT_VIEWPORT = _viewport((569.0, 472.0, 231.0, 128.0), (584.0, 438.0, 216.0, 162.0))


def _record_viewports(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
) -> LayoutViewportMapV1:
    if isinstance(record, RevealStoryboardRecordV1):
        if record.concept_id is StoryboardConceptId.RANGE_FORMULA:
            return _FORMULA_VIEWPORT
        return _COMPLEMENTARY_VIEWPORT
    if isinstance(record, TraceStoryboardRecordV1):
        return _TRAJECTORY_VIEWPORT
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        return (
            _RANGE_VISUAL_VIEWPORT
            if _uses_trajectory_evidence(record)
            else _RANGE_ANALYTIC_VIEWPORT
        )
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return _TRAJECTORY_VIEWPORT
    return _FLIGHT_VIEWPORT


def _state_viewports(component: ProjectileStoryboardStateV1 | None) -> LayoutViewportMapV1:
    if component is None or not component.accepted_records:
        return _ANCHOR_VIEWPORT
    return _record_viewports(component.accepted_records[-1])


def _caption(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1 | None,
    problem: PairedProjectileComparisonSpecV1,
    visual: _VisualModel,
) -> str:
    lower = problem.lower_angle_deg
    higher = problem.higher_angle_deg
    if record is None:
        return (
            "Same launch speed, two angles. Watch how path, landing range, height, "
            "and flight time compare."
        )
    if isinstance(record, TraceStoryboardRecordV1):
        if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE:
            return f"The amber marker traces the {lower}° trajectory from launch to impact."
        return f"The lavender marker traces the {higher}° trajectory from launch to impact."
    if isinstance(record, RevealStoryboardRecordV1):
        if record.concept_id is StoryboardConceptId.RANGE_FORMULA:
            return (
                "For equal launch and landing heights, range is controlled by "
                r"\(R(\theta)=v_0^2\sin(2\theta)/g\)."
            )
        return (
            f"{lower}° and {higher}° are complementary, so their doubled angles are supplementary."
        )
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        symbol = _range_symbol(visual)
        if _uses_trajectory_evidence(record):
            if record.claim_id is StoryboardClaimId.EQUAL_RANGE:
                return (
                    "Both accepted trajectories terminate at the same range coordinate: "
                    f"{_format_value(visual.lower.range_m)} metres."
                )
            return (
                "The landing coordinates differ: "
                rf"\(R_{{{lower}}}={_format_value(visual.lower.range_m)}\) metres and "
                rf"\(R_{{{higher}}}={_format_value(visual.higher.range_m)}\) metres."
            )
        if record.claim_id is StoryboardClaimId.EQUAL_RANGE:
            return rf"\(\sin(2·{lower}°)=\sin(2·{higher}°)\), so the range law gives equal ranges."
        return (
            "The doubled-angle sines differ, so the ranges differ: "
            rf"\(R_{{{lower}}}\ {symbol}\ R_{{{higher}}}\)."
        )
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return (
            f"The {higher}° arc reaches {_format_value(visual.higher.height_m)} metres, above "
            f"the {lower}° arc at {_format_value(visual.lower.height_m)} metres."
        )
    return (
        f"The {higher}° launch remains airborne for "
        f"{_format_value(visual.higher.flight_time_s)} seconds, versus "
        f"{_format_value(visual.lower.flight_time_s)} seconds."
    )


def _timing(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1 | None,
    visual: _VisualModel,
) -> tuple[int, int, ChoreographyEasing]:
    if record is None:
        return 800, 250, ChoreographyEasing.EASE_OUT_QUART
    if isinstance(record, RevealStoryboardRecordV1):
        return 650, 300, ChoreographyEasing.EASE_OUT_QUART
    if isinstance(record, TraceStoryboardRecordV1):
        flight_time = _trajectory_for(record, visual).flight_time_s
        duration = min(3_200, max(1_600, round(900.0 + 450.0 * flight_time)))
        return duration, 200, ChoreographyEasing.EASE_IN_OUT
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if _uses_trajectory_evidence(record):
            return 1_000, 450, ChoreographyEasing.EASE_OUT_QUINT
        return 1_100, 550, ChoreographyEasing.EASE_OUT_QUINT
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return 1_100, 450, ChoreographyEasing.EASE_OUT_QUART
    return 900, 400, ChoreographyEasing.EASE_OUT_QUART


def _emphasis_and_focus(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1 | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if record is None:
        launch_targets = (
            "launch_ray_lower",
            "launch_ray_higher",
            "launch_angle_lower",
            "launch_angle_higher",
        )
        return launch_targets, launch_targets
    if isinstance(record, RevealStoryboardRecordV1):
        suffix = (
            "range_formula"
            if record.concept_id is StoryboardConceptId.RANGE_FORMULA
            else "complementary_angles"
        )
        return (suffix,), (suffix,)
    if isinstance(record, TraceStoryboardRecordV1):
        suffix = "lower" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "higher"
        return (f"trajectory_{suffix}",), (
            f"trajectory_{suffix}",
            f"projectile_marker_{suffix}",
        )
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if _uses_trajectory_evidence(record):
            return (
                "landing_ring_lower",
                "landing_ring_higher",
                "range_value_lower",
                "range_value_higher",
            ), (
                "range_bracket_lower",
                "range_bracket_higher",
                "landing_ring_lower",
                "landing_ring_higher",
            )
        return ("sine_relation", "range_relation"), ("sine_relation", "range_relation")
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return (
            "apex_ring_lower",
            "apex_ring_higher",
        ), (
            "height_bracket_lower",
            "height_bracket_higher",
            "apex_ring_lower",
            "apex_ring_higher",
        )
    return ("flight_relation",), (
        "flight_time_lower",
        "flight_time_higher",
        "flight_relation",
    )


def _choreography(
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1 | None,
    *,
    patch: ScenePatchDraft,
    base_nodes: tuple[SceneNode, ...],
    result_nodes: tuple[SceneNode, ...],
    visual: _VisualModel,
) -> ChoreographyPlanV2:
    base_ids = {node.id for node in base_nodes}
    result_ids = {node.id for node in result_nodes}
    new_ids = sorted(
        operation.target_id for operation in patch.operations if operation.target_id not in base_ids
    )
    updated_ids = sorted(
        operation.target_id for operation in patch.operations if operation.target_id in base_ids
    )
    cues = []
    if new_ids:
        cues.append(EnterCueV1(targetIds=tuple(new_ids)))

    trace: tuple[str, str] | None = None
    if isinstance(record, TraceStoryboardRecordV1):
        suffix = "lower" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "higher"
        trace = (_node_id(f"trajectory_{suffix}"), _node_id(f"projectile_marker_{suffix}"))
        updated_ids.remove(trace[1])
    if updated_ids:
        cues.append(TransformCueV1(targetIds=tuple(updated_ids)))
    if trace is not None:
        cues.append(TracePathCueV2(pathId=trace[0], markerId=trace[1]))

    emphasis, focus = _emphasis_and_focus(record)
    emphasis_ids = tuple(sorted(_node_id(suffix) for suffix in emphasis))
    focus_ids = tuple(sorted(_node_id(suffix) for suffix in focus))
    if not set((*emphasis_ids, *focus_ids)).issubset(result_ids):
        raise SemanticStoryboardCompilationError("storyboard cue references an absent result node")
    cues.append(EmphasizeCueV1(targetIds=emphasis_ids))
    cues.append(FocusCueV1(targetIds=focus_ids))
    duration_ms, hold_after_ms, easing = _timing(record, visual)
    return ChoreographyPlanV2(
        phase=ChoreographyPhaseV2(
            cues=tuple(cues),
            durationMs=duration_ms,
            easing=easing,
            holdAfterMs=hold_after_ms,
        )
    )


def _blueprint(
    *,
    checkpoint_id: str,
    base_component: ProjectileStoryboardStateV1 | None,
    result_component: ProjectileStoryboardStateV1,
    record: RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1 | None,
    patch_id: str,
    paint_manifest: tuple[str, ...],
    operation_limit: int,
) -> SemanticStoryboardCheckpointBlueprint:
    problem = result_component.problem_spec
    visual = _visual_model(problem)
    base_nodes = (
        () if base_component is None else materialize_semantic_storyboard_nodes(base_component)
    )
    result_nodes = materialize_semantic_storyboard_nodes(result_component)
    narration = _caption(record, problem, visual)
    patch = _patch(
        patch_id=patch_id,
        narration=narration,
        base_nodes=base_nodes,
        result_nodes=result_nodes,
        paint_manifest=paint_manifest,
        operation_limit=operation_limit,
    )
    return SemanticStoryboardCheckpointBlueprint(
        checkpoint_id=checkpoint_id,
        base_component=base_component,
        result_component=result_component,
        base_nodes=base_nodes,
        result_nodes=result_nodes,
        patch=patch,
        choreography=_choreography(
            record,
            patch=patch,
            base_nodes=base_nodes,
            result_nodes=result_nodes,
            visual=visual,
        ),
        presentation=PresentationCheckpointV1(
            checkpointId=checkpoint_id,
            checkpointNarration=narration,
            baseViewports=_state_viewports(base_component),
            resultViewports=_state_viewports(result_component),
        ),
    )


def compile_semantic_storyboard_anchor(
    problem_spec: PairedProjectileComparisonSpecV1,
) -> SemanticStoryboardCheckpointBlueprint:
    """Compile the sole provider-free anchor checkpoint for a fresh problem."""

    if not isinstance(problem_spec, PairedProjectileComparisonSpecV1):
        raise TypeError("problem_spec must be a PairedProjectileComparisonSpecV1")
    result = ProjectileStoryboardStateV1(problemSpec=problem_spec)
    return _blueprint(
        checkpoint_id=STORYBOARD_ANCHOR_CHECKPOINT_ID,
        base_component=None,
        result_component=result,
        record=None,
        patch_id=f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_anchor",
        paint_manifest=_ANCHOR_PAINT,
        operation_limit=MAX_STORYBOARD_ANCHOR_OPERATIONS,
    )


def compile_semantic_storyboard_checkpoint(
    beat: RoutedSemanticStoryboardBeatV1,
    base_component: ProjectileStoryboardStateV1,
) -> SemanticStoryboardCheckpointBlueprint:
    """Compile exactly one routed model record into exactly one checkpoint."""

    if not isinstance(beat, RoutedSemanticStoryboardBeatV1):
        raise TypeError("beat must be a RoutedSemanticStoryboardBeatV1")
    if not isinstance(base_component, ProjectileStoryboardStateV1):
        raise TypeError("base_component must be a ProjectileStoryboardStateV1")
    if beat.component_id != base_component.id or beat.problem_spec != base_component.problem_spec:
        raise SemanticStoryboardCompilationError(
            "routed beat must match the accepted storyboard component and problem"
        )
    base_records = base_component.accepted_records
    if beat.ordinal != len(base_records) + 1:
        raise SemanticStoryboardCompilationError("routed beat ordinal must advance by exactly one")
    if beat.base_program_sha256 != semantic_storyboard_program_sha256(
        beat.problem_spec, base_records
    ):
        raise SemanticStoryboardCompilationError(
            "routed beat base program hash does not match state"
        )
    result_records = (*base_records, beat.record)
    if beat.result_program_sha256 != semantic_storyboard_program_sha256(
        beat.problem_spec, result_records
    ):
        raise SemanticStoryboardCompilationError(
            "routed beat result program hash does not match state"
        )
    result = ProjectileStoryboardStateV1(
        problemSpec=base_component.problem_spec,
        acceptedRecords=result_records,
    )
    slug = storyboard_record_slug(beat.record)
    return _blueprint(
        checkpoint_id=beat.checkpoint_id,
        base_component=base_component,
        result_component=result,
        record=beat.record,
        patch_id=f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_{slug}",
        paint_manifest=_effect_paint_manifest(beat.record),
        operation_limit=MAX_STORYBOARD_BEAT_OPERATIONS,
    )


__all__ = [
    "MAX_STORYBOARD_ANCHOR_OPERATIONS",
    "MAX_STORYBOARD_BEAT_OPERATIONS",
    "STORYBOARD_ANCHOR_CHECKPOINT_ID",
    "SemanticStoryboardCheckpointBlueprint",
    "SemanticStoryboardCompilationError",
    "compile_semantic_storyboard_anchor",
    "compile_semantic_storyboard_checkpoint",
    "materialize_semantic_storyboard_nodes",
]

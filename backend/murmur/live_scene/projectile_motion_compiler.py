"""Deterministic visual compiler for the Gate 1.7 projectile model.

The compiler owns geometry, copy, timing, and stable node identity.  It accepts
only a server-lowered closed route; it never interprets learner prose.  Physics
helpers in this file are deliberately not shared with the independent
verifier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeAlias

from murmur.live_scene.choreography_contracts import (
    ChoreographyEasing,
    ChoreographyPhaseV2,
    ChoreographyPlanV2,
    EmphasizeCueV1,
    EnterCueV1,
    ExitCueV1,
    FocusCueV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    TracePathCueV2,
    TransformCueV1,
    ViewportPoseV1,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexTokenSceneNode,
    LineSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
    ScenePatchDraft,
    ScenePresentation,
    SceneState,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_GRAVITY_MPS2,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    projectile_motion_checkpoint_prefix,
    projectile_motion_checkpoints_through,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1

Point: TypeAlias = tuple[float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

_DRAW = ScenePresentation(enter="draw", exit="fade")
_FADE = ScenePresentation(enter="fade", exit="fade")
_PLOT_ORIGIN: Point = (70.0, 470.0)
_X_PIXELS_PER_METRE = 5.0
_Y_PIXELS_PER_METRE = 10.0
_TRAJECTORY_HALF_POINTS = 33

_AXIS_STYLE = {
    "stroke": "hsl(var(--chalk-soft))",
    "strokeWidth": 2.0,
    "opacity": 0.72,
    "roughness": 0.0,
}
_TRAJECTORY_STYLE = {
    "stroke": "hsl(var(--amber))",
    "strokeWidth": 4.0,
    "fill": "none",
    "opacity": 1.0,
    "roughness": 0.0,
}
_RESULTANT_STYLE = {
    "stroke": "hsl(var(--amber))",
    "strokeWidth": 2.0,
    "fill": "hsl(var(--amber))",
    "opacity": 1.0,
    "roughness": 0.0,
}
_HORIZONTAL_STYLE = {
    "stroke": "hsl(var(--sage))",
    "strokeWidth": 2.0,
    "fill": "hsl(var(--sage))",
    "opacity": 1.0,
    "roughness": 0.0,
}
_VERTICAL_STYLE = {
    "stroke": "hsl(var(--lavender))",
    "strokeWidth": 2.0,
    "fill": "hsl(var(--lavender))",
    "opacity": 1.0,
    "roughness": 0.0,
}
_ACCELERATION_STYLE = {
    "stroke": "hsl(var(--ember))",
    "strokeWidth": 2.0,
    "fill": "hsl(var(--ember))",
    "opacity": 1.0,
    "roughness": 0.0,
}
_MARKER_STYLE = {
    "stroke": "hsl(var(--chalk))",
    "strokeWidth": 2.0,
    "fill": "hsl(var(--amber))",
    "opacity": 1.0,
    "roughness": 0.0,
}
_GUIDE_STYLE = {
    "stroke": "hsl(var(--lavender))",
    "strokeWidth": 1.5,
    "opacity": 0.58,
    "roughness": 0.0,
}
_RANGE_STYLE = {
    "stroke": "hsl(var(--sage))",
    "strokeWidth": 3.0,
    "opacity": 0.9,
    "roughness": 0.0,
}
_CHALK_TEXT = {
    "color": "hsl(var(--chalk))",
    "fontSize": 24.0,
    "opacity": 1.0,
}
_SOFT_TEXT = {
    "color": "hsl(var(--chalk-soft))",
    "fontSize": 18.0,
    "opacity": 0.82,
}
_AMBER_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--amber))"}
_SAGE_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--sage))"}
_LAVENDER_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--lavender))"}
_EMBER_TEXT = {**_CHALK_TEXT, "color": "hsl(var(--ember))"}

_FULL_VIEWPORT = ((20.0, 20.0, 760.0, 560.0), (0.0, 0.0, 800.0, 600.0))
_PLOT_VIEWPORT = ((35.0, 75.0, 535.0, 455.0), (0.0, 70.0, 590.0, 470.0))

_TIMING: dict[ProjectileMotionCheckpointId, tuple[int, int, ChoreographyEasing]] = {
    ProjectileMotionCheckpointId.SETUP: (4_200, 1_000, ChoreographyEasing.EASE_OUT_QUINT),
    ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
        5_000,
        1_000,
        ChoreographyEasing.EASE_IN_OUT,
    ),
    ProjectileMotionCheckpointId.TRACE_ASCENT: (
        6_000,
        800,
        ChoreographyEasing.EASE_IN_OUT,
    ),
    ProjectileMotionCheckpointId.APEX_STATE: (
        4_500,
        1_100,
        ChoreographyEasing.EASE_OUT_QUART,
    ),
    ProjectileMotionCheckpointId.TRACE_DESCENT: (
        6_000,
        800,
        ChoreographyEasing.EASE_IN,
    ),
    ProjectileMotionCheckpointId.SUMMARY: (
        5_000,
        1_200,
        ChoreographyEasing.EASE_OUT_QUINT,
    ),
    ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: (
        2_800,
        700,
        ChoreographyEasing.EASE_OUT_QUART,
    ),
    ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: (
        2_800,
        700,
        ChoreographyEasing.EASE_OUT_QUART,
    ),
    ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
        2_800,
        700,
        ChoreographyEasing.EASE_OUT_QUART,
    ),
    ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
        3_800,
        500,
        ChoreographyEasing.EASE_IN_OUT,
    ),
}


class ProjectileMotionCompilationError(ValueError):
    """Raised before any checkpoint escapes for an invalid closed transition."""


@dataclass(frozen=True)
class ProjectileMotionCheckpointBlueprint:
    """One deterministic projectile transition awaiting independent verification."""

    checkpoint_id: ProjectileMotionCheckpointId
    base_component: ProjectileMotionStateV1
    result_component: ProjectileMotionStateV1
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV2
    presentation: PresentationCheckpointV1


@dataclass(frozen=True)
class ProjectileMotionCheckpointBlueprintBatch:
    """The complete missing suffix or one sidecar for a projectile beat."""

    beat: RoutedProjectileMotionBeatV1
    base_component: ProjectileMotionStateV1
    result_component: ProjectileMotionStateV1
    checkpoints: tuple[ProjectileMotionCheckpointBlueprint, ...]


def _node_id(component_id: str, suffix: str) -> str:
    return f"{component_id}__{suffix}"


def _format_value(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".")


def _physics(problem: ProjectileMotionProblemSpecV1) -> tuple[float, float, float, float, float]:
    angle = math.radians(problem.angle_deg)
    horizontal = problem.speed_mps * math.cos(angle)
    vertical = problem.speed_mps * math.sin(angle)
    flight_time = 2.0 * vertical / PROJECTILE_MOTION_GRAVITY_MPS2
    height = vertical * vertical / (2.0 * PROJECTILE_MOTION_GRAVITY_MPS2)
    range_m = horizontal * flight_time
    return horizontal, vertical, flight_time, height, range_m


def _position(problem: ProjectileMotionProblemSpecV1, time_seconds: float) -> Point:
    horizontal, vertical, _, _, _ = _physics(problem)
    x_metres = horizontal * time_seconds
    y_metres = (
        vertical * time_seconds - 0.5 * PROJECTILE_MOTION_GRAVITY_MPS2 * time_seconds * time_seconds
    )
    return (
        _PLOT_ORIGIN[0] + x_metres * _X_PIXELS_PER_METRE,
        _PLOT_ORIGIN[1] - max(0.0, y_metres) * _Y_PIXELS_PER_METRE,
    )


def _trajectory_halves(
    problem: ProjectileMotionProblemSpecV1,
) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    _, _, flight_time, _, _ = _physics(problem)
    apex_time = flight_time / 2.0
    ascent = tuple(
        _position(problem, apex_time * index / (_TRAJECTORY_HALF_POINTS - 1))
        for index in range(_TRAJECTORY_HALF_POINTS)
    )
    descent = tuple(
        _position(
            problem,
            apex_time + apex_time * index / (_TRAJECTORY_HALF_POINTS - 1),
        )
        for index in range(_TRAJECTORY_HALF_POINTS)
    )
    return ascent, descent


def _line(
    component_id: str, suffix: str, start: Point, end: Point, style: dict[str, object]
) -> LineSceneNode:
    return LineSceneNode(
        id=_node_id(component_id, suffix),
        kind="line",
        presentation=_DRAW,
        points=(start, end),
        style=style,
    )


def _path(
    component_id: str,
    suffix: str,
    points: tuple[Point, ...],
    *,
    closed: bool,
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode(
        id=_node_id(component_id, suffix),
        kind="path",
        presentation=_DRAW,
        points=points,
        closed=closed,
        style=style,
    )


def _token(
    component_id: str,
    suffix: str,
    latex: str,
    x: float,
    y: float,
    width: float,
    *,
    height: float = 42.0,
    style: dict[str, object] | None = None,
) -> LatexTokenSceneNode:
    return LatexTokenSceneNode(
        id=_node_id(component_id, suffix),
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


def _marker_points(center: Point, radius: float = 6.0) -> tuple[Point, ...]:
    return tuple(
        (
            center[0] + radius * math.cos(math.tau * index / 12.0),
            center[1] + radius * math.sin(math.tau * index / 12.0),
        )
        for index in range(12)
    )


def _arrow_points(start: Point, end: Point) -> tuple[Point, ...]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 12.0:
        raise ProjectileMotionCompilationError("a vector is too short to render safely")
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    head_length = min(12.0, length * 0.32)
    head_half = 7.0
    shaft_half = 2.2
    head_base = (end[0] - ux * head_length, end[1] - uy * head_length)
    return (
        (start[0] + px * shaft_half, start[1] + py * shaft_half),
        (head_base[0] + px * shaft_half, head_base[1] + py * shaft_half),
        (head_base[0] + px * head_half, head_base[1] + py * head_half),
        end,
        (head_base[0] - px * head_half, head_base[1] - py * head_half),
        (head_base[0] - px * shaft_half, head_base[1] - py * shaft_half),
        (start[0] - px * shaft_half, start[1] - py * shaft_half),
    )


def _arrow(
    component_id: str,
    suffix: str,
    start: Point,
    end: Point,
    style: dict[str, object],
) -> PathSceneNode:
    return _path(
        component_id,
        suffix,
        _arrow_points(start, end),
        closed=True,
        style=style,
    )


def _base_geometry(component_id: str, problem: ProjectileMotionProblemSpecV1) -> NodeMap:
    horizontal, vertical, _, _, _ = _physics(problem)
    vector_scale = 3.0
    vector_end = (
        _PLOT_ORIGIN[0] + horizontal * vector_scale,
        _PLOT_ORIGIN[1] - vertical * vector_scale,
    )
    nodes: NodeMap = {}
    for node in (
        _line(component_id, "ground", (55.0, 470.0), (530.0, 470.0), _AXIS_STYLE),
        _line(component_id, "y_axis", (70.0, 485.0), (70.0, 95.0), _AXIS_STYLE),
        _arrow(
            component_id,
            "velocity_resultant",
            _PLOT_ORIGIN,
            vector_end,
            _RESULTANT_STYLE,
        ),
        _path(
            component_id,
            "projectile_marker",
            _marker_points(_PLOT_ORIGIN),
            closed=True,
            style=_MARKER_STYLE,
        ),
        _token(
            component_id,
            "axis_x",
            "x",
            518.0,
            475.0,
            28.0,
            height=24.0,
            style=_SOFT_TEXT,
        ),
        _token(component_id, "axis_y", "y", 55.0, 96.0, 28.0, style=_SOFT_TEXT),
        _token(component_id, "title", r"\text{One launch, two motions}", 640.0, 38.0, 250.0),
        _token(
            component_id,
            "givens",
            rf"v_0={problem.speed_mps}\,\mathrm{{m/s}},\quad \theta={problem.angle_deg}^\circ",
            640.0,
            92.0,
            250.0,
            style=_AMBER_TEXT,
        ),
        _token(
            component_id,
            "label_resultant",
            "v_0",
            102.0,
            386.0,
            50.0,
            style=_AMBER_TEXT,
        ),
    ):
        nodes[node.id] = node
    return nodes


def _decomposition_nodes(component_id: str, problem: ProjectileMotionProblemSpecV1) -> NodeMap:
    horizontal, vertical, _, _, _ = _physics(problem)
    vector_scale = 3.0
    horizontal_end = (_PLOT_ORIGIN[0] + horizontal * vector_scale, _PLOT_ORIGIN[1])
    resultant_end = (horizontal_end[0], _PLOT_ORIGIN[1] - vertical * vector_scale)
    nodes: NodeMap = {}
    for node in (
        _arrow(
            component_id,
            "velocity_horizontal",
            _PLOT_ORIGIN,
            horizontal_end,
            _HORIZONTAL_STYLE,
        ),
        _arrow(
            component_id,
            "velocity_vertical",
            horizontal_end,
            resultant_end,
            _VERTICAL_STYLE,
        ),
        _token(
            component_id,
            "label_horizontal",
            "v_x",
            128.0,
            480.0,
            50.0,
            style=_SAGE_TEXT,
        ),
        _token(
            component_id,
            "label_vertical",
            "v_{y0}",
            166.0,
            422.0,
            72.0,
            style=_LAVENDER_TEXT,
        ),
        _token(component_id, "equation_x", r"x(t)=v_x t", 640.0, 166.0, 220.0, style=_SAGE_TEXT),
        _token(
            component_id,
            "equation_y",
            r"y(t)=v_{y0}t-\frac12gt^2",
            640.0,
            222.0,
            250.0,
            height=52.0,
            style=_LAVENDER_TEXT,
        ),
        _token(
            component_id,
            "component_values",
            rf"v_x={_format_value(horizontal)},\quad v_{{y0}}={_format_value(vertical)}\ \mathrm{{m/s}}",
            640.0,
            286.0,
            250.0,
            style=_SOFT_TEXT,
        ),
        _token(
            component_id,
            "vertical_state",
            r"a_x=0,\quad a_y=-g",
            640.0,
            334.0,
            210.0,
            style=_SOFT_TEXT,
        ),
    ):
        nodes[node.id] = node
    return nodes


def _trajectory_nodes(
    component_id: str,
    problem: ProjectileMotionProblemSpecV1,
    *,
    include_ascent: bool,
    include_descent: bool,
) -> NodeMap:
    ascent, descent = _trajectory_halves(problem)
    nodes: NodeMap = {}
    if include_ascent:
        ascent_node = _path(
            component_id,
            "trajectory_ascent",
            ascent,
            closed=False,
            style=_TRAJECTORY_STYLE,
        )
        nodes[ascent_node.id] = ascent_node
    if include_descent:
        descent_node = _path(
            component_id,
            "trajectory_descent",
            descent,
            closed=False,
            style=_TRAJECTORY_STYLE,
        )
        nodes[descent_node.id] = descent_node
    return nodes


def _apex_nodes(component_id: str, problem: ProjectileMotionProblemSpecV1) -> NodeMap:
    _, _, _, height, _ = _physics(problem)
    ascent, _ = _trajectory_halves(problem)
    apex = ascent[-1]
    label_x = min(405.0, apex[0] + 110.0)
    label_y = max(105.0, apex[1] - 120.0)
    acceleration_end = (apex[0], min(458.0, apex[1] + 58.0))
    nodes: NodeMap = {}
    for node in (
        _line(component_id, "apex_guide", (apex[0], 470.0), apex, _GUIDE_STYLE),
        _path(
            component_id,
            "apex_marker",
            _marker_points(apex, 4.5),
            closed=True,
            style={**_MARKER_STYLE, "fill": "hsl(var(--lavender))"},
        ),
        _arrow(
            component_id,
            "acceleration",
            apex,
            acceleration_end,
            _ACCELERATION_STYLE,
        ),
        _token(
            component_id,
            "apex_velocity",
            r"v_y=0\quad\text{at the apex}",
            label_x,
            label_y,
            220.0,
            style=_LAVENDER_TEXT,
        ),
        _token(
            component_id,
            "apex_acceleration",
            r"a_y=-g=-10\,\mathrm{m/s^2}",
            label_x,
            label_y + 48.0,
            190.0,
            style=_EMBER_TEXT,
        ),
        _token(
            component_id,
            "height_value",
            rf"H={_format_value(height)}\,\mathrm{{m}}",
            apex[0] - 54.0,
            (apex[1] + 470.0) / 2.0 - 20.0,
            100.0,
            style=_LAVENDER_TEXT,
        ),
    ):
        nodes[node.id] = node
    return nodes


def _descent_annotation(component_id: str) -> LatexTokenSceneNode:
    return _token(
        component_id,
        "vertical_state",
        r"v_y<0,\quad v_x\ \text{stays constant}",
        640.0,
        334.0,
        250.0,
        style=_LAVENDER_TEXT,
    )


def _summary_nodes(component_id: str, problem: ProjectileMotionProblemSpecV1) -> NodeMap:
    _, _, flight_time, height, range_m = _physics(problem)
    landing = _position(problem, flight_time)
    nodes: NodeMap = {}
    for node in (
        _line(component_id, "range_dimension", _PLOT_ORIGIN, (landing[0], 470.0), _RANGE_STYLE),
        _token(
            component_id,
            "summary_values",
            rf"T={_format_value(flight_time)}\,\mathrm{{s}},\quad H={_format_value(height)}\,\mathrm{{m}},\quad R={_format_value(range_m)}\,\mathrm{{m}}",
            635.0,
            510.0,
            260.0,
            height=52.0,
            style=_AMBER_TEXT,
        ),
    ):
        nodes[node.id] = node
    return nodes


def _clarification_nodes(state: ProjectileMotionStateV1) -> NodeMap:
    component_id = state.id
    nodes: NodeMap = {}
    if state.active_clarification is None:
        return nodes
    if state.active_clarification is ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY:
        node = _token(
            component_id,
            "clarify_horizontal_velocity",
            r"a_x=0\ \Longrightarrow\ v_x\ \text{stays constant}",
            280.0,
            524.0,
            276.0,
            style=_SAGE_TEXT,
        )
        nodes[node.id] = node
    if state.active_clarification is ProjectileMotionClarificationTopic.APEX_ACCELERATION:
        node = _token(
            component_id,
            "clarify_apex_acceleration",
            r"v_y=0\ \text{for an instant};\quad a_y=-g",
            280.0,
            524.0,
            286.0,
            style=_EMBER_TEXT,
        )
        nodes[node.id] = node
    if state.active_clarification is ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY:
        node = _token(
            component_id,
            "clarify_flight_symmetry",
            r"t_{\uparrow}=t_{\downarrow}\quad\text{when launch and impact heights match}",
            280.0,
            524.0,
            292.0,
            style=_LAVENDER_TEXT,
        )
        nodes[node.id] = node
    return nodes


def _desired_nodes(state: ProjectileMotionStateV1) -> NodeMap:
    if state.last_main_checkpoint is None:
        return {}
    index = list(ProjectileMotionMainCheckpoint).index(state.last_main_checkpoint)
    nodes = _base_geometry(state.id, state.problem_spec)
    if index >= 1:
        nodes.update(_decomposition_nodes(state.id, state.problem_spec))
    if index >= 2:
        nodes.update(
            _trajectory_nodes(
                state.id,
                state.problem_spec,
                include_ascent=True,
                include_descent=index >= 4,
            )
        )
        marker_position = (
            _trajectory_halves(state.problem_spec)[1][-1]
            if index >= 4
            else _trajectory_halves(state.problem_spec)[0][-1]
        )
        marker = _path(
            state.id,
            "projectile_marker",
            _marker_points(marker_position),
            closed=True,
            style=_MARKER_STYLE,
        )
        nodes[marker.id] = marker
    if index >= 3:
        for suffix in ("label_resultant", "label_horizontal", "label_vertical"):
            nodes.pop(_node_id(state.id, suffix), None)
        nodes.update(_apex_nodes(state.id, state.problem_spec))
    if index >= 4:
        descent = _descent_annotation(state.id)
        nodes[descent.id] = descent
    if index >= 5:
        nodes.update(_summary_nodes(state.id, state.problem_spec))
    nodes.update(_clarification_nodes(state))
    return nodes


def materialize_projectile_motion_nodes(state: ProjectileMotionStateV1) -> tuple[SceneNode, ...]:
    """Reconstruct the exact component-local board for one accepted frontier."""

    nodes = _desired_nodes(state)
    return tuple(nodes[node_id] for node_id in sorted(nodes))


def _patch(
    component_id: str,
    checkpoint_id: ProjectileMotionCheckpointId,
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
        raise ProjectileMotionCompilationError("a projectile checkpoint must change the scene")
    if len(operations) > MAX_PATCH_OPERATIONS:
        raise ProjectileMotionCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the {MAX_PATCH_OPERATIONS}-operation budget"
        )
    patch = ScenePatchDraft(
        patch_id=f"{component_id}__cp_{checkpoint_id.value}",
        narration=_narration(checkpoint_id),
        operations=operations,
    )
    if (
        len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
        > MAX_NDJSON_FRAME_BYTES
    ):
        raise ProjectileMotionCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the canonical 64 KiB budget"
        )
    result_nodes = tuple(desired_nodes[node_id] for node_id in sorted(desired_nodes))
    return patch, result_nodes


def _narration(checkpoint_id: ProjectileMotionCheckpointId) -> str:
    return {
        ProjectileMotionCheckpointId.SETUP: (
            "One launch contains two independent motions under the same clock."
        ),
        ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
            "Horizontal velocity stays constant while gravity changes only the vertical velocity."
        ),
        ProjectileMotionCheckpointId.TRACE_ASCENT: (
            "Equal slices of time carry the projectile rightward while its upward speed falls."
        ),
        ProjectileMotionCheckpointId.APEX_STATE: (
            "At the apex vertical velocity is zero for an instant, but acceleration is still downward."
        ),
        ProjectileMotionCheckpointId.TRACE_DESCENT: (
            "Gravity makes vertical velocity negative while horizontal velocity remains unchanged."
        ),
        ProjectileMotionCheckpointId.SUMMARY: (
            "The same component equations determine flight time, maximum height, and range."
        ),
        ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: (
            "With no horizontal acceleration, equal time intervals add equal horizontal distance."
        ),
        ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: (
            "Zero vertical velocity describes this instant; downward acceleration describes what happens next."
        ),
        ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
            "With equal launch and impact heights, ascent and descent take equal time."
        ),
        ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
            "The same model now responds to the new launch parameters without losing its reasoning state."
        ),
    }[checkpoint_id]


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


def _viewports_for_checkpoint(checkpoint_id: ProjectileMotionCheckpointId) -> LayoutViewportMapV1:
    if checkpoint_id in {
        ProjectileMotionCheckpointId.TRACE_ASCENT,
        ProjectileMotionCheckpointId.APEX_STATE,
        ProjectileMotionCheckpointId.TRACE_DESCENT,
    }:
        return _viewport_map(_PLOT_VIEWPORT)
    return _viewport_map(_FULL_VIEWPORT)


def _viewports_for_state(state: ProjectileMotionStateV1) -> LayoutViewportMapV1:
    if state.active_clarification is not None:
        return _viewports_for_checkpoint(
            PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[state.active_clarification]
        )
    if state.last_main_checkpoint is None:
        return _viewport_map(_FULL_VIEWPORT)
    return _viewports_for_checkpoint(ProjectileMotionCheckpointId(state.last_main_checkpoint.value))


def _retarget_focus_suffixes(state: ProjectileMotionStateV1) -> tuple[str, ...]:
    """Keep an in-place parameter morph focused inside its settled camera."""

    if state.active_clarification is not None:
        checkpoint_id = PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[state.active_clarification]
    elif state.last_main_checkpoint is not None:
        checkpoint_id = ProjectileMotionCheckpointId(state.last_main_checkpoint.value)
    else:  # Retarget compilation rejects an empty frontier before cue authoring.
        raise ProjectileMotionCompilationError(
            "a retarget cue requires a settled projectile frontier"
        )

    return {
        ProjectileMotionCheckpointId.SETUP: ("givens", "velocity_resultant"),
        ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
            "velocity_horizontal",
            "velocity_vertical",
        ),
        ProjectileMotionCheckpointId.TRACE_ASCENT: (
            "projectile_marker",
            "trajectory_ascent",
        ),
        ProjectileMotionCheckpointId.APEX_STATE: (
            "acceleration",
            "apex_velocity",
            "projectile_marker",
        ),
        ProjectileMotionCheckpointId.TRACE_DESCENT: (
            "projectile_marker",
            "trajectory_descent",
        ),
        ProjectileMotionCheckpointId.SUMMARY: (
            "range_dimension",
            "summary_values",
        ),
        ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: (
            "clarify_horizontal_velocity",
            "velocity_horizontal",
        ),
        ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: (
            "acceleration",
            "clarify_apex_acceleration",
        ),
        ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
            "clarify_flight_symmetry",
            "trajectory_ascent",
            "trajectory_descent",
        ),
        ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (),
    }[checkpoint_id]


def _cue_plan(
    component_id: str,
    checkpoint_id: ProjectileMotionCheckpointId,
    patch: ScenePatchDraft,
    base_nodes: tuple[SceneNode, ...],
    result_component: ProjectileMotionStateV1,
) -> ChoreographyPlanV2:
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
    trace_suffix = {
        ProjectileMotionCheckpointId.TRACE_ASCENT: "trajectory_ascent",
        ProjectileMotionCheckpointId.TRACE_DESCENT: "trajectory_descent",
    }.get(checkpoint_id)
    marker_id = _node_id(component_id, "projectile_marker")
    path_id = _node_id(component_id, trace_suffix) if trace_suffix else None
    if trace_suffix:
        transform = [node_id for node_id in transform if node_id != marker_id]

    result_ids = set(base_by_id)
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            result_ids.add(operation.target_id)
        else:
            result_ids.discard(operation.target_id)

    emphasis_suffixes = {
        ProjectileMotionCheckpointId.SETUP: ("velocity_resultant",),
        ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
            "velocity_horizontal",
            "velocity_vertical",
        ),
        ProjectileMotionCheckpointId.TRACE_ASCENT: ("trajectory_ascent",),
        ProjectileMotionCheckpointId.APEX_STATE: (
            "apex_velocity",
            "acceleration",
        ),
        ProjectileMotionCheckpointId.TRACE_DESCENT: ("trajectory_descent",),
        ProjectileMotionCheckpointId.SUMMARY: ("summary_values",),
        ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: ("clarify_horizontal_velocity",),
        ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: ("clarify_apex_acceleration",),
        ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: ("clarify_flight_symmetry",),
        ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
            "velocity_resultant",
            "trajectory_ascent",
        ),
    }[checkpoint_id]
    focus_suffixes = {
        ProjectileMotionCheckpointId.SETUP: ("givens", "velocity_resultant"),
        ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
            "equation_x",
            "equation_y",
            "velocity_horizontal",
            "velocity_vertical",
        ),
        ProjectileMotionCheckpointId.TRACE_ASCENT: (
            "projectile_marker",
            "trajectory_ascent",
        ),
        ProjectileMotionCheckpointId.APEX_STATE: (
            "acceleration",
            "apex_velocity",
            "projectile_marker",
        ),
        ProjectileMotionCheckpointId.TRACE_DESCENT: (
            "projectile_marker",
            "trajectory_descent",
        ),
        ProjectileMotionCheckpointId.SUMMARY: (
            "range_dimension",
            "summary_values",
        ),
        ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: (
            "clarify_horizontal_velocity",
            "velocity_horizontal",
        ),
        ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: (
            "acceleration",
            "clarify_apex_acceleration",
        ),
        ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
            "clarify_flight_symmetry",
            "trajectory_ascent",
            "trajectory_descent",
        ),
        ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: _retarget_focus_suffixes(
            result_component
        ),
    }[checkpoint_id]

    cues = []
    if enter:
        cues.append(EnterCueV1(target_ids=tuple(enter)))
    if exit_:
        cues.append(ExitCueV1(target_ids=tuple(exit_)))
    if transform:
        cues.append(TransformCueV1(target_ids=tuple(transform)))
    if trace_suffix and path_id:
        cues.append(TracePathCueV2(path_id=path_id, marker_id=marker_id))
    emphasis_ids = tuple(
        sorted(
            node_id
            for suffix in emphasis_suffixes
            if (node_id := _node_id(component_id, suffix)) in result_ids
        )
    )
    if emphasis_ids:
        cues.append(EmphasizeCueV1(target_ids=emphasis_ids))
    focus_ids = tuple(
        sorted(
            node_id
            for suffix in focus_suffixes
            if (node_id := _node_id(component_id, suffix)) in result_ids
        )
    )
    if not focus_ids:
        raise ProjectileMotionCompilationError("a checkpoint must retain a focus target")
    cues.append(FocusCueV1(target_ids=focus_ids))
    duration_ms, hold_after_ms, easing = _TIMING[checkpoint_id]
    return ChoreographyPlanV2(
        phase=ChoreographyPhaseV2(
            cues=tuple(cues),
            duration_ms=duration_ms,
            easing=easing,
            hold_after_ms=hold_after_ms,
        )
    )


def _checkpoint(
    checkpoint_id: ProjectileMotionCheckpointId,
    base_component: ProjectileMotionStateV1,
    result_component: ProjectileMotionStateV1,
    base_nodes: tuple[SceneNode, ...],
) -> ProjectileMotionCheckpointBlueprint:
    desired_nodes = _desired_nodes(result_component)
    patch, result_nodes = _patch(
        result_component.id,
        checkpoint_id,
        base_nodes,
        desired_nodes,
    )
    return ProjectileMotionCheckpointBlueprint(
        checkpoint_id=checkpoint_id,
        base_component=base_component,
        result_component=result_component,
        base_nodes=base_nodes,
        result_nodes=result_nodes,
        patch=patch,
        choreography=_cue_plan(
            result_component.id,
            checkpoint_id,
            patch,
            base_nodes,
            result_component,
        ),
        presentation=PresentationCheckpointV1(
            checkpoint_id=checkpoint_id.value,
            checkpoint_narration=patch.narration,
            base_viewports=_viewports_for_state(base_component),
            result_viewports=_viewports_for_state(result_component),
        ),
    )


def _empty_component(beat: RoutedProjectileMotionBeatV1) -> ProjectileMotionStateV1:
    return ProjectileMotionStateV1(
        id=beat.component_id,
        problem_spec=beat.result_problem_spec,
    )


def compile_projectile_motion_checkpoint_blueprints(
    beat: RoutedProjectileMotionBeatV1,
    base_component: ProjectileMotionStateV1 | None = None,
) -> ProjectileMotionCheckpointBlueprintBatch:
    """Build exactly one sidecar or the missing deterministic main suffix."""

    if base_component is None:
        if beat.base_problem_spec is not None:
            raise ProjectileMotionCompilationError(
                "a non-empty routed beat requires a base projectile component"
            )
        base_component = _empty_component(beat)
    else:
        if beat.base_problem_spec is None:
            raise ProjectileMotionCompilationError(
                "a submitted projectile component requires baseProblemSpec"
            )
        if base_component.id != beat.component_id:
            raise ProjectileMotionCompilationError(
                "routed beat componentId must match the base projectile state"
            )
        if base_component.problem_spec != beat.base_problem_spec:
            raise ProjectileMotionCompilationError(
                "routed beat baseProblemSpec must match the accepted state"
            )

    base_nodes = materialize_projectile_motion_nodes(base_component)

    if isinstance(beat.route, RetargetProjectileMotionRouteV1):
        if base_component.last_main_checkpoint is None:
            raise ProjectileMotionCompilationError(
                "parameters may be retargeted only after a settled checkpoint"
            )
        result_component = ProjectileMotionStateV1(
            id=base_component.id,
            problem_spec=beat.result_problem_spec,
            last_main_checkpoint=base_component.last_main_checkpoint,
            clarified_topics=base_component.clarified_topics,
            active_clarification=base_component.active_clarification,
        )
        checkpoint = _checkpoint(
            ProjectileMotionCheckpointId.PARAMETERS_RETARGETED,
            base_component,
            result_component,
            base_nodes,
        )
        return ProjectileMotionCheckpointBlueprintBatch(
            beat=beat,
            base_component=base_component,
            result_component=result_component,
            checkpoints=(checkpoint,),
        )

    if beat.result_problem_spec != base_component.problem_spec:
        raise ProjectileMotionCompilationError(
            "advance and clarification must preserve the accepted problem"
        )

    if isinstance(beat.route, ClarifyProjectileMotionRouteV1):
        if beat.route.topic in base_component.clarified_topics:
            raise ProjectileMotionCompilationError("a clarification topic is one-shot")
        clarified_topics = tuple(
            topic
            for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER
            if topic in (*base_component.clarified_topics, beat.route.topic)
        )
        try:
            result_component = ProjectileMotionStateV1(
                id=base_component.id,
                problem_spec=base_component.problem_spec,
                last_main_checkpoint=base_component.last_main_checkpoint,
                clarified_topics=clarified_topics,
                active_clarification=beat.route.topic,
            )
        except ValueError as error:
            raise ProjectileMotionCompilationError(
                "the clarification topic is not legal at this frontier"
            ) from error
        checkpoint_id = PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[beat.route.topic]
        checkpoint = _checkpoint(
            checkpoint_id,
            base_component,
            result_component,
            base_nodes,
        )
        return ProjectileMotionCheckpointBlueprintBatch(
            beat=beat,
            base_component=base_component,
            result_component=result_component,
            checkpoints=(checkpoint,),
        )

    if not isinstance(beat.route, AdvanceProjectileMotionRouteV1):
        raise ProjectileMotionCompilationError("unsupported projectile route")
    target = projectile_motion_checkpoints_through(beat.route.target_stage)
    current = projectile_motion_checkpoint_prefix(base_component.last_main_checkpoint)
    if current != target[: len(current)]:
        raise ProjectileMotionCompilationError("a projectile lesson cannot move backward")

    current_component = base_component
    current_nodes = base_nodes
    checkpoints: list[ProjectileMotionCheckpointBlueprint] = []
    for main_checkpoint in target[len(current) :]:
        result_component = ProjectileMotionStateV1(
            id=base_component.id,
            problem_spec=base_component.problem_spec,
            last_main_checkpoint=main_checkpoint,
            clarified_topics=current_component.clarified_topics,
            active_clarification=None,
        )
        checkpoint = _checkpoint(
            ProjectileMotionCheckpointId(main_checkpoint.value),
            current_component,
            result_component,
            current_nodes,
        )
        checkpoints.append(checkpoint)
        current_component = result_component
        current_nodes = checkpoint.result_nodes

    return ProjectileMotionCheckpointBlueprintBatch(
        beat=beat,
        base_component=base_component,
        result_component=current_component,
        checkpoints=tuple(checkpoints),
    )


def materialize_projectile_motion_scene(
    state: ProjectileMotionStateV1, revision: int
) -> SceneState:
    """Convenience boundary for tests and later service realization checks."""

    return SceneState(revision=revision, nodes=materialize_projectile_motion_nodes(state))


__all__ = [
    "ProjectileMotionCheckpointBlueprint",
    "ProjectileMotionCheckpointBlueprintBatch",
    "ProjectileMotionCompilationError",
    "compile_projectile_motion_checkpoint_blueprints",
    "materialize_projectile_motion_nodes",
    "materialize_projectile_motion_scene",
]

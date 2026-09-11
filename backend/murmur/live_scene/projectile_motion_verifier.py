"""Independent verification for the Gate 1.7 projectile visual model.

This module deliberately does not import :mod:`projectile_motion_compiler`.
It accepts a compiler blueprint through a structural protocol and reconstructs
the expected physics, scene, transition, and presentation from the primitive
``speedMps`` and ``angleDeg`` fields.  In particular, it never reads the
derived convenience properties on :class:`ProjectileMotionProblemSpecV1`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias

from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV2,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
    TracePathCueV2,
    ViewportPoseV1,
)
from murmur.live_scene.contracts import (
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    LatexTokenSceneNode,
    LineSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RemoveSceneOperation,
    SceneNode,
    ScenePatchDraft,
    ScenePresentation,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStateV1,
)

Point: TypeAlias = tuple[float, float]
Box: TypeAlias = tuple[float, float, float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

SAFE_VIEWPORT_PADDING = 12.0

_EPSILON = 1e-7
_GRAVITY_MPS2 = 10.0
_SUPPORTED_SPEEDS_MPS = (20, 25, 30)
_SUPPORTED_ANGLES_DEG = (30, 45, 60)
_PLOT_ORIGIN: Point = (70.0, 470.0)
_X_PIXELS_PER_METRE = 5.0
_Y_PIXELS_PER_METRE = 10.0
_TRAJECTORY_HALF_POINTS = 33

_DRAW = ScenePresentation(enter="draw", exit="fade")
_FADE = ScenePresentation(enter="fade", exit="fade")

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

_TIMING: Mapping[ProjectileMotionCheckpointId, tuple[int, int, str]] = {
    ProjectileMotionCheckpointId.SETUP: (4_200, 1_000, "ease_out_quint"),
    ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (5_000, 1_000, "ease_in_out"),
    ProjectileMotionCheckpointId.TRACE_ASCENT: (6_000, 800, "ease_in_out"),
    ProjectileMotionCheckpointId.APEX_STATE: (4_500, 1_100, "ease_out_quart"),
    ProjectileMotionCheckpointId.TRACE_DESCENT: (6_000, 800, "ease_in"),
    ProjectileMotionCheckpointId.SUMMARY: (5_000, 1_200, "ease_out_quint"),
    ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: (
        2_800,
        700,
        "ease_out_quart",
    ),
    ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: (
        2_800,
        700,
        "ease_out_quart",
    ),
    ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
        2_800,
        700,
        "ease_out_quart",
    ),
    ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (3_800, 500, "ease_in_out"),
}

_NARRATION: Mapping[ProjectileMotionCheckpointId, str] = {
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
        "Zero vertical velocity describes this instant; downward acceleration describes what "
        "happens next."
    ),
    ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: (
        "With equal launch and impact heights, ascent and descent take equal time."
    ),
    ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
        "The same model now responds to the new launch parameters without losing its reasoning "
        "state."
    ),
}

_EMPHASIS_SUFFIXES: Mapping[ProjectileMotionCheckpointId, tuple[str, ...]] = {
    ProjectileMotionCheckpointId.SETUP: ("velocity_resultant",),
    ProjectileMotionCheckpointId.DECOMPOSE_VELOCITY: (
        "velocity_horizontal",
        "velocity_vertical",
    ),
    ProjectileMotionCheckpointId.TRACE_ASCENT: ("trajectory_ascent",),
    ProjectileMotionCheckpointId.APEX_STATE: ("apex_velocity", "acceleration"),
    ProjectileMotionCheckpointId.TRACE_DESCENT: ("trajectory_descent",),
    ProjectileMotionCheckpointId.SUMMARY: ("summary_values",),
    ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL: ("clarify_horizontal_velocity",),
    ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL: ("clarify_apex_acceleration",),
    ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL: ("clarify_flight_symmetry",),
    ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
        "velocity_resultant",
        "trajectory_ascent",
    ),
}

_FOCUS_SUFFIXES: Mapping[ProjectileMotionCheckpointId, tuple[str, ...]] = {
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
    ProjectileMotionCheckpointId.SUMMARY: ("range_dimension", "summary_values"),
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
    ProjectileMotionCheckpointId.PARAMETERS_RETARGETED: (
        "givens",
        "projectile_marker",
        "velocity_resultant",
    ),
}

_RETARGET_MAIN_FOCUS_SUFFIXES: Mapping[ProjectileMotionCheckpointId, tuple[str, ...]] = {
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
    ProjectileMotionCheckpointId.SUMMARY: ("range_dimension", "summary_values"),
}

_CUE_ORDER = ("enter", "exit", "transform", "trace_path", "emphasize", "focus")


class ProjectileMotionVerificationCode(StrEnum):
    """Stable failure families for later service/certificate boundaries."""

    BLUEPRINT_CONTRACT = "blueprint_contract"
    PROBLEM_IDENTITY = "problem_identity"
    TRANSITION = "transition"
    STABLE_IDS = "stable_ids"
    BOARD_BOUNDS = "board_bounds"
    TEXT_COLLISION = "text_collision"
    PHYSICS_GEOMETRY = "physics_geometry"
    LABEL_FACT = "label_fact"
    LABEL_LAYOUT = "label_layout"
    VISUAL_STYLE = "visual_style"
    PATCH = "patch"
    CAPTION = "caption"
    CHOREOGRAPHY = "choreography"
    TIMING = "timing"
    VIEWPORT = "viewport"


class ProjectileMotionVerificationError(ValueError):
    """Fail-closed verifier error with a stable machine-readable family."""

    def __init__(self, code: ProjectileMotionVerificationCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


class ProjectileMotionCheckpointBlueprintLike(Protocol):
    """Structural boundary consumed without importing the concrete compiler."""

    checkpoint_id: ProjectileMotionCheckpointId
    base_component: ProjectileMotionStateV1
    result_component: ProjectileMotionStateV1
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV2
    presentation: PresentationCheckpointV1


@dataclass(frozen=True, slots=True)
class _Physics:
    speed_mps: int
    angle_deg: int
    theta_rad: float
    vx_mps: float
    vy0_mps: float
    t_apex_s: float
    t_flight_s: float
    h_max_m: float
    x_range_m: float


def _fail(code: ProjectileMotionVerificationCode, message: str) -> None:
    raise ProjectileMotionVerificationError(code, message)


def _derive_physics(problem: ProjectileMotionProblemSpecV1) -> _Physics:
    """Derive the qualified model from serialized primitive fields only."""

    if not isinstance(problem, ProjectileMotionProblemSpecV1):
        _fail(
            ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
            "problem must be a ProjectileMotionProblemSpecV1 contract",
        )
    speed = problem.speed_mps
    angle = problem.angle_deg
    if type(speed) is not int or type(angle) is not int:
        _fail(
            ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
            "speedMps and angleDeg must remain strict integers",
        )
    if speed not in _SUPPORTED_SPEEDS_MPS or angle not in _SUPPORTED_ANGLES_DEG:
        _fail(
            ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
            "problem is outside the independently qualified speed/angle domain",
        )

    theta = math.pi * angle / 180.0
    horizontal = speed * math.cos(theta)
    vertical = speed * math.sin(theta)
    ascent_time = vertical / _GRAVITY_MPS2
    flight_time = 2.0 * ascent_time
    height = vertical * vertical / (2.0 * _GRAVITY_MPS2)
    range_m = horizontal * flight_time
    return _Physics(
        speed_mps=speed,
        angle_deg=angle,
        theta_rad=theta,
        vx_mps=horizontal,
        vy0_mps=vertical,
        t_apex_s=ascent_time,
        t_flight_s=flight_time,
        h_max_m=height,
        x_range_m=range_m,
    )


def _node_id(component_id: str, suffix: str) -> str:
    return f"{component_id}__{suffix}"


def _format_value(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".")


def _close(left: float, right: float, *, tolerance: float = _EPSILON) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)


def _points_close(left: Sequence[Point], right: Sequence[Point]) -> bool:
    return len(left) == len(right) and all(
        _close(left_x, right_x) and _close(left_y, right_y)
        for (left_x, left_y), (right_x, right_y) in zip(left, right, strict=True)
    )


def _projected_position(physics: _Physics, normalized_time: float) -> Point:
    x_metres = normalized_time * physics.x_range_m
    y_metres = 4.0 * physics.h_max_m * normalized_time * (1.0 - normalized_time)
    return (
        _PLOT_ORIGIN[0] + _X_PIXELS_PER_METRE * x_metres,
        _PLOT_ORIGIN[1] - _Y_PIXELS_PER_METRE * y_metres,
    )


def _trajectory_halves(physics: _Physics) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    ascent = tuple(
        _projected_position(physics, index / 64.0) for index in range(_TRAJECTORY_HALF_POINTS)
    )
    descent = tuple(
        _projected_position(physics, (32 + index) / 64.0)
        for index in range(_TRAJECTORY_HALF_POINTS)
    )
    return ascent, descent


def _marker_points(center: Point, radius: float = 6.0) -> tuple[Point, ...]:
    return tuple(
        (
            center[0] + radius * math.cos(2.0 * math.pi * index / 12.0),
            center[1] + radius * math.sin(2.0 * math.pi * index / 12.0),
        )
        for index in range(12)
    )


def _arrow_points(start: Point, end: Point) -> tuple[Point, ...]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 12.0:
        _fail(
            ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
            "an independently derived vector is too short to render",
        )
    unit_x, unit_y = dx / length, dy / length
    normal_x, normal_y = -unit_y, unit_x
    head_length = min(12.0, length * 0.32)
    head_half_width = 7.0
    shaft_half_width = 2.2
    head_base = (end[0] - unit_x * head_length, end[1] - unit_y * head_length)
    return (
        (
            start[0] + normal_x * shaft_half_width,
            start[1] + normal_y * shaft_half_width,
        ),
        (
            head_base[0] + normal_x * shaft_half_width,
            head_base[1] + normal_y * shaft_half_width,
        ),
        (
            head_base[0] + normal_x * head_half_width,
            head_base[1] + normal_y * head_half_width,
        ),
        end,
        (
            head_base[0] - normal_x * head_half_width,
            head_base[1] - normal_y * head_half_width,
        ),
        (
            head_base[0] - normal_x * shaft_half_width,
            head_base[1] - normal_y * shaft_half_width,
        ),
        (
            start[0] - normal_x * shaft_half_width,
            start[1] - normal_y * shaft_half_width,
        ),
    )


def _line(
    component_id: str,
    suffix: str,
    start: Point,
    end: Point,
    style: Mapping[str, object],
) -> LineSceneNode:
    return LineSceneNode(
        id=_node_id(component_id, suffix),
        kind="line",
        presentation=_DRAW,
        points=(start, end),
        style=dict(style),
    )


def _path(
    component_id: str,
    suffix: str,
    points: tuple[Point, ...],
    *,
    closed: bool,
    style: Mapping[str, object],
) -> PathSceneNode:
    return PathSceneNode(
        id=_node_id(component_id, suffix),
        kind="path",
        presentation=_DRAW,
        points=points,
        closed=closed,
        style=dict(style),
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
    style: Mapping[str, object] | None = None,
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
        style=dict(style or _CHALK_TEXT),
    )


def _expected_nodes(state: ProjectileMotionStateV1, physics: _Physics) -> NodeMap:
    if state.last_main_checkpoint is None:
        return {}

    component_id = state.id
    vector_scale = 3.0
    resultant_end = (
        _PLOT_ORIGIN[0] + vector_scale * physics.vx_mps,
        _PLOT_ORIGIN[1] - vector_scale * physics.vy0_mps,
    )
    nodes: NodeMap = {}

    def add(*new_nodes: SceneNode) -> None:
        for node in new_nodes:
            nodes[node.id] = node

    add(
        _line(component_id, "ground", (55.0, 470.0), (530.0, 470.0), _AXIS_STYLE),
        _line(component_id, "y_axis", (70.0, 485.0), (70.0, 95.0), _AXIS_STYLE),
        _path(
            component_id,
            "velocity_resultant",
            _arrow_points(_PLOT_ORIGIN, resultant_end),
            closed=True,
            style=_RESULTANT_STYLE,
        ),
        _path(
            component_id,
            "projectile_marker",
            _marker_points(_PLOT_ORIGIN),
            closed=True,
            style=_MARKER_STYLE,
        ),
        _token(component_id, "axis_x", "x", 518.0, 475.0, 28.0, height=24.0, style=_SOFT_TEXT),
        _token(component_id, "axis_y", "y", 55.0, 96.0, 28.0, style=_SOFT_TEXT),
        _token(component_id, "title", r"\text{One launch, two motions}", 640.0, 38.0, 250.0),
        _token(
            component_id,
            "givens",
            rf"v_0={physics.speed_mps}\,\mathrm{{m/s}},\quad \theta={physics.angle_deg}^\circ",
            640.0,
            92.0,
            250.0,
            style=_AMBER_TEXT,
        ),
        _token(component_id, "label_resultant", "v_0", 102.0, 386.0, 50.0, style=_AMBER_TEXT),
    )

    main_index = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(state.last_main_checkpoint)
    if main_index >= 1:
        horizontal_end = (
            _PLOT_ORIGIN[0] + vector_scale * physics.vx_mps,
            _PLOT_ORIGIN[1],
        )
        add(
            _path(
                component_id,
                "velocity_horizontal",
                _arrow_points(_PLOT_ORIGIN, horizontal_end),
                closed=True,
                style=_HORIZONTAL_STYLE,
            ),
            _path(
                component_id,
                "velocity_vertical",
                _arrow_points(horizontal_end, resultant_end),
                closed=True,
                style=_VERTICAL_STYLE,
            ),
            _token(component_id, "label_horizontal", "v_x", 128.0, 480.0, 50.0, style=_SAGE_TEXT),
            _token(
                component_id,
                "label_vertical",
                "v_{y0}",
                166.0,
                422.0,
                72.0,
                style=_LAVENDER_TEXT,
            ),
            _token(
                component_id,
                "equation_x",
                r"x(t)=v_x t",
                640.0,
                166.0,
                220.0,
                style=_SAGE_TEXT,
            ),
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
                rf"v_x={_format_value(physics.vx_mps)},\quad "
                rf"v_{{y0}}={_format_value(physics.vy0_mps)}\ "
                r"\mathrm{m/s}",
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
        )

    ascent, descent = _trajectory_halves(physics)
    if main_index >= 2:
        add(
            _path(
                component_id,
                "trajectory_ascent",
                ascent,
                closed=False,
                style=_TRAJECTORY_STYLE,
            )
        )
        marker_center = descent[-1] if main_index >= 4 else ascent[-1]
        add(
            _path(
                component_id,
                "projectile_marker",
                _marker_points(marker_center),
                closed=True,
                style=_MARKER_STYLE,
            )
        )

    if main_index >= 3:
        for retired_suffix in ("label_resultant", "label_horizontal", "label_vertical"):
            nodes.pop(_node_id(component_id, retired_suffix), None)
        apex = ascent[-1]
        label_x = min(405.0, apex[0] + 110.0)
        label_y = max(105.0, apex[1] - 120.0)
        acceleration_end = (apex[0], min(458.0, apex[1] + 58.0))
        add(
            _line(component_id, "apex_guide", (apex[0], 470.0), apex, _GUIDE_STYLE),
            _path(
                component_id,
                "apex_marker",
                _marker_points(apex, 4.5),
                closed=True,
                style={**_MARKER_STYLE, "fill": "hsl(var(--lavender))"},
            ),
            _path(
                component_id,
                "acceleration",
                _arrow_points(apex, acceleration_end),
                closed=True,
                style=_ACCELERATION_STYLE,
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
                rf"H={_format_value(physics.h_max_m)}\,\mathrm{{m}}",
                apex[0] - 54.0,
                (apex[1] + 470.0) / 2.0 - 20.0,
                100.0,
                style=_LAVENDER_TEXT,
            ),
        )

    if main_index >= 4:
        add(
            _path(
                component_id,
                "trajectory_descent",
                descent,
                closed=False,
                style=_TRAJECTORY_STYLE,
            ),
            _token(
                component_id,
                "vertical_state",
                r"v_y<0,\quad v_x\ \text{stays constant}",
                640.0,
                334.0,
                250.0,
                style=_LAVENDER_TEXT,
            ),
        )

    if main_index >= 5:
        add(
            _line(
                component_id,
                "range_dimension",
                _PLOT_ORIGIN,
                (descent[-1][0], 470.0),
                _RANGE_STYLE,
            ),
            _token(
                component_id,
                "summary_values",
                rf"T={_format_value(physics.t_flight_s)}\,\mathrm{{s}},\quad "
                rf"H={_format_value(physics.h_max_m)}\,\mathrm{{m}},\quad "
                rf"R={_format_value(physics.x_range_m)}\,\mathrm{{m}}",
                635.0,
                510.0,
                260.0,
                height=52.0,
                style=_AMBER_TEXT,
            ),
        )

    active = state.active_clarification
    if active is ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY:
        add(
            _token(
                component_id,
                "clarify_horizontal_velocity",
                r"a_x=0\ \Longrightarrow\ v_x\ \text{stays constant}",
                280.0,
                524.0,
                276.0,
                style=_SAGE_TEXT,
            )
        )
    elif active is ProjectileMotionClarificationTopic.APEX_ACCELERATION:
        add(
            _token(
                component_id,
                "clarify_apex_acceleration",
                r"v_y=0\ \text{for an instant};\quad a_y=-g",
                280.0,
                524.0,
                286.0,
                style=_EMBER_TEXT,
            )
        )
    elif active is ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY:
        add(
            _token(
                component_id,
                "clarify_flight_symmetry",
                r"t_{\uparrow}=t_{\downarrow}\quad\text{when launch and impact heights match}",
                280.0,
                524.0,
                292.0,
                style=_LAVENDER_TEXT,
            )
        )
    return nodes


def _node_map(nodes: Sequence[SceneNode], *, label: str) -> NodeMap:
    node_ids = tuple(node.id for node in nodes)
    if len(node_ids) != len(set(node_ids)):
        _fail(
            ProjectileMotionVerificationCode.STABLE_IDS,
            f"{label} node IDs must be unique",
        )
    if node_ids != tuple(sorted(node_ids)):
        _fail(
            ProjectileMotionVerificationCode.STABLE_IDS,
            f"{label} nodes must use canonical lexical ID order",
        )
    return {node.id: node for node in nodes}


def _node_box(node: SceneNode) -> Box:
    if isinstance(node, (LineSceneNode, PathSceneNode)):
        half_stroke = node.style.stroke_width / 2.0
        xs = tuple(point[0] for point in node.points)
        ys = tuple(point[1] for point in node.points)
        return (
            min(xs) - half_stroke,
            min(ys) - half_stroke,
            max(xs) + half_stroke,
            max(ys) + half_stroke,
        )
    if isinstance(node, LatexTokenSceneNode):
        left = node.x
        if node.anchor == "middle":
            left -= node.width / 2.0
        elif node.anchor == "end":
            left -= node.width
        return (left, node.y, left + node.width, node.y + node.height)
    _fail(
        ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
        f"node {node.id!r} uses an unsupported projectile visual kind",
    )


def _verify_board_bounds(nodes: Iterable[SceneNode], *, label: str) -> None:
    for node in nodes:
        left, top, right, bottom = _node_box(node)
        if not all(math.isfinite(value) for value in (left, top, right, bottom)):
            _fail(
                ProjectileMotionVerificationCode.BOARD_BOUNDS,
                f"{label} node {node.id!r} has non-finite bounds",
            )
        if (
            left < -_EPSILON
            or top < -_EPSILON
            or right > LIVE_SCENE_BOARD_WIDTH + _EPSILON
            or bottom > LIVE_SCENE_BOARD_HEIGHT + _EPSILON
        ):
            _fail(
                ProjectileMotionVerificationCode.BOARD_BOUNDS,
                f"{label} node {node.id!r} is clipped by the canonical board",
            )


def _boxes_overlap(left: Box, right: Box) -> bool:
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > _EPSILON
        and min(left[3], right[3]) - max(left[1], right[1]) > _EPSILON
    )


def _verify_text_collisions(nodes: Iterable[SceneNode], *, label: str) -> None:
    tokens = tuple(node for node in nodes if isinstance(node, LatexTokenSceneNode))
    for index, left in enumerate(tokens):
        for right in tokens[index + 1 :]:
            if _boxes_overlap(_node_box(left), _node_box(right)):
                _fail(
                    ProjectileMotionVerificationCode.TEXT_COLLISION,
                    f"{label} LaTeX collision between {left.id!r} and {right.id!r}",
                )


def _verify_expected_node(actual: SceneNode, expected: SceneNode) -> None:
    if type(actual) is not type(expected):
        _fail(
            ProjectileMotionVerificationCode.STABLE_IDS,
            f"stable role {expected.id!r} changed node kind",
        )
    if actual.presentation != expected.presentation:
        _fail(
            ProjectileMotionVerificationCode.VISUAL_STYLE,
            f"node {expected.id!r} changed its lifecycle presentation",
        )
    if actual.style != expected.style:
        _fail(
            ProjectileMotionVerificationCode.VISUAL_STYLE,
            f"node {expected.id!r} changed its canonical visible style",
        )
    if isinstance(actual, LatexTokenSceneNode) and isinstance(expected, LatexTokenSceneNode):
        if actual.latex != expected.latex:
            _fail(
                ProjectileMotionVerificationCode.LABEL_FACT,
                f"label {expected.id!r} does not state the independently derived fact",
            )
        if actual.anchor != expected.anchor:
            _fail(
                ProjectileMotionVerificationCode.LABEL_LAYOUT,
                f"label {expected.id!r} changed its stable anchor",
            )
        actual_layout = (actual.x, actual.y, actual.width, actual.height)
        expected_layout = (expected.x, expected.y, expected.width, expected.height)
        if not all(
            _close(actual_value, expected_value)
            for actual_value, expected_value in zip(actual_layout, expected_layout, strict=True)
        ):
            _fail(
                ProjectileMotionVerificationCode.LABEL_LAYOUT,
                f"label {expected.id!r} changed its independently authored layout",
            )
        return
    if isinstance(actual, LineSceneNode) and isinstance(expected, LineSceneNode):
        if not _points_close(actual.points, expected.points):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                f"line {expected.id!r} does not match the independently derived geometry",
            )
        return
    if isinstance(actual, PathSceneNode) and isinstance(expected, PathSceneNode):
        if actual.closed != expected.closed or len(actual.points) != len(expected.points):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                f"path {expected.id!r} changed its certified topology",
            )
        if not _points_close(actual.points, expected.points):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                f"path {expected.id!r} does not match the independently derived geometry",
            )
        return
    _fail(
        ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
        f"node {expected.id!r} uses an unsupported projectile visual kind",
    )


def _verify_snapshot(
    state: ProjectileMotionStateV1,
    nodes: NodeMap,
    physics: _Physics,
    *,
    label: str,
) -> None:
    expected = _expected_nodes(state, physics)
    if set(nodes) != set(expected):
        missing = sorted(set(expected).difference(nodes))
        extra = sorted(set(nodes).difference(expected))
        _fail(
            ProjectileMotionVerificationCode.STABLE_IDS,
            f"{label} has wrong stable node roles; missing={missing}, extra={extra}",
        )
    for node_id, expected_node in expected.items():
        _verify_expected_node(nodes[node_id], expected_node)


def _verify_trajectory_physics(
    component_id: str,
    state: ProjectileMotionStateV1,
    nodes: NodeMap,
    physics: _Physics,
) -> None:
    if state.last_main_checkpoint is None:
        return
    main_index = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(state.last_main_checkpoint)
    if main_index < 2:
        return

    ascent_id = _node_id(component_id, "trajectory_ascent")
    ascent = nodes[ascent_id]
    if not isinstance(ascent, PathSceneNode) or ascent.closed or len(ascent.points) != 33:
        _fail(
            ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
            "trajectory ascent must be one open 33-point path",
        )
    halves: list[PathSceneNode] = [ascent]
    if main_index >= 4:
        descent = nodes[_node_id(component_id, "trajectory_descent")]
        if not isinstance(descent, PathSceneNode) or descent.closed or len(descent.points) != 33:
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "trajectory descent must be one open 33-point path",
            )
        if not _points_close((ascent.points[-1],), (descent.points[0],)):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "ascent and descent must share the exact apex sample",
            )
        halves.append(descent)

    samples = ascent.points
    if len(halves) == 2:
        samples = (*ascent.points, *halves[1].points[1:])
    expected_sample_count = 65 if main_index >= 4 else 33
    if len(samples) != expected_sample_count:
        _fail(
            ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
            "trajectory samples do not preserve fixed half-path topology",
        )

    prior_x: float | None = None
    for index, point in enumerate(samples):
        normalized_time = index / 64.0
        expected = _projected_position(physics, normalized_time)
        if not _points_close((point,), (expected,)):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "trajectory violates x=sR or y=4Hs(1-s) at a uniform-time sample",
            )

        time_seconds = normalized_time * physics.t_flight_s
        x_metres = (point[0] - _PLOT_ORIGIN[0]) / _X_PIXELS_PER_METRE
        y_metres = (_PLOT_ORIGIN[1] - point[1]) / _Y_PIXELS_PER_METRE
        time_form_x = physics.vx_mps * time_seconds
        time_form_y = (
            physics.vy0_mps * time_seconds - 0.5 * _GRAVITY_MPS2 * time_seconds * time_seconds
        )
        if not _close(x_metres, time_form_x) or not _close(y_metres, time_form_y):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "trajectory sample violates the component motion equations",
            )
        if y_metres < -_EPSILON:
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "qualified ground-to-ground path may not dip below launch height",
            )
        if prior_x is not None and point[0] <= prior_x + _EPSILON:
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "trajectory must move strictly rightward at equal time intervals",
            )
        prior_x = point[0]

        vertical_velocity = physics.vy0_mps - _GRAVITY_MPS2 * time_seconds
        if normalized_time < 0.5 and vertical_velocity <= _EPSILON:
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "vertical velocity must remain positive before the apex",
            )
        if _close(normalized_time, 0.5) and not _close(vertical_velocity, 0.0):
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "apex vertical velocity must be exactly zero within tolerance",
            )
        if normalized_time > 0.5 and vertical_velocity >= -_EPSILON:
            _fail(
                ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
                "vertical velocity must remain negative after the apex",
            )

    marker = nodes[_node_id(component_id, "projectile_marker")]
    if not isinstance(marker, PathSceneNode):
        _fail(
            ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
            "projectile marker must remain a closed path",
        )
    expected_center = ascent.points[-1] if main_index < 4 else halves[-1].points[-1]
    if not _points_close((_box_center(_node_box(marker)),), (expected_center,)):
        _fail(
            ProjectileMotionVerificationCode.PHYSICS_GEOMETRY,
            "projectile marker center does not equal the settled trajectory endpoint",
        )


def _box_center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _canonical_topics(
    topics: Iterable[ProjectileMotionClarificationTopic],
) -> tuple[ProjectileMotionClarificationTopic, ...]:
    materialized = tuple(topics)
    return tuple(topic for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER if topic in materialized)


def _verify_transition(
    checkpoint_id: ProjectileMotionCheckpointId,
    base: ProjectileMotionStateV1,
    result: ProjectileMotionStateV1,
) -> None:
    if base.id != result.id:
        _fail(
            ProjectileMotionVerificationCode.TRANSITION,
            "checkpoint may not change semantic component identity",
        )

    main_by_checkpoint = {
        ProjectileMotionCheckpointId(item.value): item
        for item in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    }
    if checkpoint_id in main_by_checkpoint:
        if base.problem_spec != result.problem_spec:
            _fail(
                ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
                "main advance may not change the accepted projectile problem",
            )
        base_prefix_length = (
            0
            if base.last_main_checkpoint is None
            else PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(base.last_main_checkpoint) + 1
        )
        if base_prefix_length >= len(PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER):
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "terminal main frontier has no forward checkpoint",
            )
        expected_main = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[base_prefix_length]
        if main_by_checkpoint[checkpoint_id] is not expected_main:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "main checkpoint is not the immediate missing frontier",
            )
        if result.last_main_checkpoint is not expected_main:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "result component does not settle the declared main checkpoint",
            )
        if result.clarified_topics != base.clarified_topics:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "main advance must preserve the one-shot clarification ledger",
            )
        if result.active_clarification is not None:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "main advance must close the active clarification detour",
            )
        return

    clarification_by_checkpoint = {
        checkpoint: topic
        for topic, checkpoint in PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS.items()
    }
    if checkpoint_id in clarification_by_checkpoint:
        topic = clarification_by_checkpoint[checkpoint_id]
        if base.problem_spec != result.problem_spec:
            _fail(
                ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
                "clarification may not change the accepted projectile problem",
            )
        if base.last_main_checkpoint != result.last_main_checkpoint:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification must preserve the settled main frontier",
            )
        if topic in base.clarified_topics:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification checkpoint may be accepted only once",
            )
        expected_topics = _canonical_topics((*base.clarified_topics, topic))
        if result.clarified_topics != expected_topics:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification result does not append the canonical one-shot ledger",
            )
        if result.active_clarification is not topic:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification result must identify the currently visible detour",
            )
        if base.last_main_checkpoint is None:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification has no settled prerequisite frontier",
            )
        prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
        if PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(base.last_main_checkpoint) < (
            PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(prerequisite)
        ):
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "clarification occurs before its pedagogical prerequisite",
            )
        return

    if checkpoint_id is ProjectileMotionCheckpointId.PARAMETERS_RETARGETED:
        if base.last_main_checkpoint is None:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "parameter retarget requires a settled main frontier",
            )
        if base.problem_spec == result.problem_spec:
            _fail(
                ProjectileMotionVerificationCode.PROBLEM_IDENTITY,
                "parameter retarget must name a different complete problem",
            )
        if base.last_main_checkpoint != result.last_main_checkpoint:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "parameter retarget must preserve the settled main frontier",
            )
        if base.clarified_topics != result.clarified_topics:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "parameter retarget must preserve the clarification ledger",
            )
        if base.active_clarification != result.active_clarification:
            _fail(
                ProjectileMotionVerificationCode.TRANSITION,
                "parameter retarget must preserve the active clarification detour",
            )
        return

    _fail(
        ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
        "checkpoint ID is outside the closed projectile vocabulary",
    )


def _materialize_patch(base: NodeMap, patch: ScenePatchDraft) -> NodeMap:
    materialized = dict(base)
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if materialized.get(operation.node.id) == operation.node:
                _fail(
                    ProjectileMotionVerificationCode.PATCH,
                    f"put operation for {operation.node.id!r} is a no-op",
                )
            materialized[operation.node.id] = operation.node
        elif isinstance(operation, RemoveSceneOperation):
            if operation.id not in materialized:
                _fail(
                    ProjectileMotionVerificationCode.PATCH,
                    f"remove operation targets absent node {operation.id!r}",
                )
            del materialized[operation.id]
        else:
            _fail(
                ProjectileMotionVerificationCode.PATCH,
                "patch contains an unsupported operation variant",
            )
    return materialized


def _verify_patch(
    component_id: str,
    checkpoint_id: ProjectileMotionCheckpointId,
    base: NodeMap,
    result: NodeMap,
    patch: ScenePatchDraft,
) -> tuple[str, ...]:
    expected_patch_id = f"{component_id}__cp_{checkpoint_id.value}"
    if patch.patch_id != expected_patch_id:
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patchId does not bind component and checkpoint identity",
        )
    operation_targets = tuple(operation.target_id for operation in patch.operations)
    if operation_targets != tuple(sorted(operation_targets)):
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patch operation targets must use canonical lexical order",
        )
    if len(operation_targets) != len(set(operation_targets)):
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patch operation targets must be unique",
        )
    prefix = f"{component_id}__"
    if any(not target.startswith(prefix) for target in operation_targets):
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patch may not mutate a foreign component namespace",
        )

    changed = {node_id for node_id in set(base) & set(result) if base[node_id] != result[node_id]}
    removed = set(base).difference(result)
    added = set(result).difference(base)
    expected_targets = tuple(sorted(changed | removed | added))
    if operation_targets != expected_targets:
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patch operations do not exactly cover the declared scene diff",
        )
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.target_id not in result or operation.node != result[operation.target_id]:
                _fail(
                    ProjectileMotionVerificationCode.PATCH,
                    "put operation does not equal its declared terminal node",
                )
        elif operation.id not in removed:
            _fail(
                ProjectileMotionVerificationCode.PATCH,
                "remove operation does not target an actually removed node",
            )
    if _materialize_patch(base, patch) != result:
        _fail(
            ProjectileMotionVerificationCode.PATCH,
            "patch does not exactly materialize the declared result nodes",
        )
    return operation_targets


def _cue(choreography: ChoreographyPlanV2, cue_name: str):
    return next((cue for cue in choreography.phase.cues if cue.cue == cue_name), None)


def _target_ids(choreography: ChoreographyPlanV2, cue_name: str) -> tuple[str, ...]:
    cue = _cue(choreography, cue_name)
    if cue is None or isinstance(cue, TracePathCueV2):
        return ()
    return cue.target_ids


def _expected_semantic_targets(
    component_id: str,
    checkpoint_id: ProjectileMotionCheckpointId,
    result: NodeMap,
    table: Mapping[ProjectileMotionCheckpointId, tuple[str, ...]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            node_id
            for suffix in table[checkpoint_id]
            if (node_id := _node_id(component_id, suffix)) in result
        )
    )


def _checkpoint_for_state(state: ProjectileMotionStateV1) -> ProjectileMotionCheckpointId:
    if state.active_clarification is not None:
        return PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[state.active_clarification]
    if state.last_main_checkpoint is None:
        _fail(
            ProjectileMotionVerificationCode.TRANSITION,
            "a settled semantic focus requires a main checkpoint",
        )
    return ProjectileMotionCheckpointId(state.last_main_checkpoint.value)


def _retarget_focus_suffixes(state: ProjectileMotionStateV1) -> tuple[str, ...]:
    checkpoint_id = _checkpoint_for_state(state)
    if state.active_clarification is not None:
        return _FOCUS_SUFFIXES[checkpoint_id]
    return _RETARGET_MAIN_FOCUS_SUFFIXES[checkpoint_id]


def _verify_trace(
    component_id: str,
    checkpoint_id: ProjectileMotionCheckpointId,
    base: NodeMap,
    result: NodeMap,
    choreography: ChoreographyPlanV2,
) -> str | None:
    trace = _cue(choreography, "trace_path")
    expected_suffix = {
        ProjectileMotionCheckpointId.TRACE_ASCENT: "trajectory_ascent",
        ProjectileMotionCheckpointId.TRACE_DESCENT: "trajectory_descent",
    }.get(checkpoint_id)
    if expected_suffix is None:
        if trace is not None:
            _fail(
                ProjectileMotionVerificationCode.CHOREOGRAPHY,
                "only launch-flight checkpoints may own a trace_path cue",
            )
        return None
    if not isinstance(trace, TracePathCueV2):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "flight checkpoint is missing its certified trace_path cue",
        )

    path_id = _node_id(component_id, expected_suffix)
    marker_id = _node_id(component_id, "projectile_marker")
    if (trace.path_id, trace.marker_id) != (path_id, marker_id):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace_path cue does not bind the certified path and stable projectile marker",
        )
    if path_id in base or path_id not in result:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace path must enter at its flight checkpoint",
        )
    if marker_id not in base or marker_id not in result or base[marker_id] == result[marker_id]:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace marker must be one changed stable node",
        )

    path = result[path_id]
    before_marker = base[marker_id]
    after_marker = result[marker_id]
    if not all(isinstance(node, PathSceneNode) for node in (path, before_marker, after_marker)):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace path and marker must remain path nodes",
        )
    assert isinstance(path, PathSceneNode)
    assert isinstance(before_marker, PathSceneNode)
    assert isinstance(after_marker, PathSceneNode)
    if path.closed or not before_marker.closed or not after_marker.closed:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace requires one open path and one closed marker",
        )
    if len(before_marker.points) != len(after_marker.points):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace marker topology changed during travel",
        )

    start = path.points[0]
    end = path.points[-1]
    if not _points_close((_box_center(_node_box(before_marker)),), (start,)):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace marker does not start at the path origin",
        )
    if not _points_close((_box_center(_node_box(after_marker)),), (end,)):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "trace marker does not settle at the path terminal point",
        )
    delta = (end[0] - start[0], end[1] - start[1])
    for before, after in zip(before_marker.points, after_marker.points, strict=True):
        if not _close(after[0] - before[0], delta[0]) or not _close(after[1] - before[1], delta[1]):
            _fail(
                ProjectileMotionVerificationCode.CHOREOGRAPHY,
                "trace marker update must be a pure start-to-end translation",
            )
    return marker_id


def _verify_choreography(
    result_component: ProjectileMotionStateV1,
    checkpoint_id: ProjectileMotionCheckpointId,
    base: NodeMap,
    result: NodeMap,
    choreography: ChoreographyPlanV2,
) -> tuple[str, ...]:
    component_id = result_component.id
    cue_names = tuple(cue.cue for cue in choreography.phase.cues)
    if cue_names != tuple(sorted(cue_names, key=_CUE_ORDER.index)):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "choreography cues do not use canonical execution order",
        )
    if len(cue_names) != len(set(cue_names)):
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "choreography cue kinds must be unique",
        )

    base_ids = set(base)
    result_ids = set(result)
    added = tuple(sorted(result_ids - base_ids))
    removed = tuple(sorted(base_ids - result_ids))
    changed = tuple(
        sorted(node_id for node_id in base_ids & result_ids if base[node_id] != result[node_id])
    )
    trace_marker_id = _verify_trace(
        component_id,
        checkpoint_id,
        base,
        result,
        choreography,
    )
    expected_transform = tuple(node_id for node_id in changed if node_id != trace_marker_id)
    if _target_ids(choreography, "enter") != added:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "enter cue does not exactly own every added node",
        )
    if _target_ids(choreography, "exit") != removed:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "exit cue does not exactly own every removed node",
        )
    if _target_ids(choreography, "transform") != expected_transform:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "transform cue does not exactly own non-trace node updates",
        )

    expected_emphasis = _expected_semantic_targets(
        component_id,
        checkpoint_id,
        result,
        _EMPHASIS_SUFFIXES,
    )
    if _target_ids(choreography, "emphasize") != expected_emphasis:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "emphasize cue does not name the checkpoint's authored subject",
        )
    if checkpoint_id is ProjectileMotionCheckpointId.PARAMETERS_RETARGETED:
        expected_focus = tuple(
            sorted(
                _node_id(component_id, suffix)
                for suffix in _retarget_focus_suffixes(result_component)
                if _node_id(component_id, suffix) in result
            )
        )
    else:
        expected_focus = _expected_semantic_targets(
            component_id,
            checkpoint_id,
            result,
            _FOCUS_SUFFIXES,
        )
    if _target_ids(choreography, "focus") != expected_focus:
        _fail(
            ProjectileMotionVerificationCode.CHOREOGRAPHY,
            "focus cue does not name the checkpoint's authored subject",
        )

    prefix = f"{component_id}__"
    camera_targets: set[str] = set(expected_emphasis) | set(expected_focus)
    for cue in choreography.phase.cues:
        if isinstance(cue, TracePathCueV2):
            target_ids = (cue.path_id, cue.marker_id)
        else:
            target_ids = cue.target_ids
        if any(not target_id.startswith(prefix) for target_id in target_ids):
            _fail(
                ProjectileMotionVerificationCode.CHOREOGRAPHY,
                f"{cue.cue} cue targets a foreign component",
            )
        valid_ids = base_ids if cue.cue == "exit" else result_ids
        if any(target_id not in valid_ids for target_id in target_ids):
            _fail(
                ProjectileMotionVerificationCode.CHOREOGRAPHY,
                f"{cue.cue} cue targets a node absent from its scene",
            )
        if isinstance(cue, TracePathCueV2):
            camera_targets.update(target_ids)
    return tuple(sorted(camera_targets))


def _verify_timing(
    checkpoint_id: ProjectileMotionCheckpointId,
    choreography: ChoreographyPlanV2,
) -> None:
    expected = _TIMING[checkpoint_id]
    actual = (
        choreography.phase.duration_ms,
        choreography.phase.hold_after_ms,
        getattr(choreography.phase.easing, "value", None),
    )
    if actual != expected:
        _fail(
            ProjectileMotionVerificationCode.TIMING,
            "checkpoint choreography does not match independently authored timing",
        )


def _verify_viewport_pose(pose: ViewportPoseV1, *, label: str) -> None:
    if not isinstance(pose, ViewportPoseV1):
        _fail(
            ProjectileMotionVerificationCode.VIEWPORT,
            f"{label} must be a ViewportPoseV1 contract",
        )
    if (
        pose.x < 0.0
        or pose.y < 0.0
        or pose.x + pose.width > LIVE_SCENE_BOARD_WIDTH
        or pose.y + pose.height > LIVE_SCENE_BOARD_HEIGHT
    ):
        _fail(
            ProjectileMotionVerificationCode.VIEWPORT,
            f"{label} leaves the canonical board",
        )
    if pose.width <= 2.0 * SAFE_VIEWPORT_PADDING or pose.height <= 2.0 * SAFE_VIEWPORT_PADDING:
        _fail(
            ProjectileMotionVerificationCode.VIEWPORT,
            f"{label} is too small for safe camera padding",
        )


def _verify_viewport_content(
    viewports: LayoutViewportMapV1,
    nodes: NodeMap,
    target_ids: Iterable[str],
    *,
    label: str,
) -> None:
    if not isinstance(viewports, LayoutViewportMapV1):
        _fail(
            ProjectileMotionVerificationCode.VIEWPORT,
            f"{label} must be a LayoutViewportMapV1 contract",
        )
    targets = tuple(sorted(set(target_ids)))
    for target_id in targets:
        if target_id not in nodes:
            _fail(
                ProjectileMotionVerificationCode.VIEWPORT,
                f"{label} subject {target_id!r} is absent from its scene",
            )
    for layout, pose in (("cinematic", viewports.cinematic), ("compact", viewports.compact)):
        _verify_viewport_pose(pose, label=f"{label}.{layout}")
        safe = (
            pose.x + SAFE_VIEWPORT_PADDING,
            pose.y + SAFE_VIEWPORT_PADDING,
            pose.x + pose.width - SAFE_VIEWPORT_PADDING,
            pose.y + pose.height - SAFE_VIEWPORT_PADDING,
        )
        for target_id in targets:
            target = _node_box(nodes[target_id])
            if (
                target[0] < safe[0] - _EPSILON
                or target[1] < safe[1] - _EPSILON
                or target[2] > safe[2] + _EPSILON
                or target[3] > safe[3] + _EPSILON
            ):
                _fail(
                    ProjectileMotionVerificationCode.VIEWPORT,
                    f"{label} subject {target_id!r} is clipped by {layout} safe padding",
                )


def _state_subject_ids(state: ProjectileMotionStateV1, nodes: NodeMap) -> tuple[str, ...]:
    if state.active_clarification is not None:
        checkpoint_id = PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[state.active_clarification]
    elif state.last_main_checkpoint is not None:
        checkpoint_id = ProjectileMotionCheckpointId(state.last_main_checkpoint.value)
    else:
        return ()
    return tuple(
        sorted(
            {
                _node_id(state.id, suffix)
                for suffix in (
                    *_FOCUS_SUFFIXES[checkpoint_id],
                    *_EMPHASIS_SUFFIXES[checkpoint_id],
                )
                if _node_id(state.id, suffix) in nodes
            }
        )
    )


def _verify_presentation(
    checkpoint_id: ProjectileMotionCheckpointId,
    base_component: ProjectileMotionStateV1,
    result_component: ProjectileMotionStateV1,
    base: NodeMap,
    result: NodeMap,
    presentation: PresentationCheckpointV1,
    visible_targets: Sequence[str],
) -> None:
    if presentation.checkpoint_id != checkpoint_id.value:
        _fail(
            ProjectileMotionVerificationCode.CAPTION,
            "presentation checkpointId does not match the blueprint checkpoint",
        )
    expected_narration = _NARRATION[checkpoint_id]
    if presentation.checkpoint_narration != expected_narration:
        _fail(
            ProjectileMotionVerificationCode.CAPTION,
            "checkpoint narration changes or omits an authored physics fact",
        )
    if presentation.transient_free is not True:
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "settled presentation must remain transient-free",
        )
    _verify_viewport_content(
        presentation.base_viewports,
        base,
        _state_subject_ids(base_component, base),
        label="base viewport",
    )
    _verify_viewport_content(
        presentation.result_viewports,
        result,
        (*visible_targets, *_state_subject_ids(result_component, result)),
        label="result viewport",
    )
    if (
        checkpoint_id is ProjectileMotionCheckpointId.PARAMETERS_RETARGETED
        and presentation.base_viewports != presentation.result_viewports
    ):
        _fail(
            ProjectileMotionVerificationCode.VIEWPORT,
            "in-place retarget must preserve its settled camera identity",
        )


def _read_blueprint(blueprint: object) -> ProjectileMotionCheckpointBlueprintLike:
    required = (
        "checkpoint_id",
        "base_component",
        "result_component",
        "base_nodes",
        "result_nodes",
        "patch",
        "choreography",
        "presentation",
    )
    if any(not hasattr(blueprint, attribute) for attribute in required):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "compiler blueprint is missing a required public field",
        )
    return blueprint  # type: ignore[return-value]


def verify_projectile_motion_checkpoint(
    blueprint: ProjectileMotionCheckpointBlueprintLike,
) -> None:
    """Verify one compiler-produced projectile checkpoint in full.

    The function returns only after the semantic transition, both independently
    materialized snapshots, exact patch, all physics, choreography, captions,
    collisions, and both camera layouts have passed.
    """

    checkpoint = _read_blueprint(blueprint)
    if not isinstance(checkpoint.checkpoint_id, ProjectileMotionCheckpointId):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "checkpoint_id must be a ProjectileMotionCheckpointId",
        )
    if not isinstance(checkpoint.base_component, ProjectileMotionStateV1) or not isinstance(
        checkpoint.result_component, ProjectileMotionStateV1
    ):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "blueprint components must be ProjectileMotionStateV1 contracts",
        )
    if not isinstance(checkpoint.base_nodes, tuple) or not isinstance(
        checkpoint.result_nodes, tuple
    ):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "blueprint node snapshots must be immutable tuples",
        )
    if not isinstance(checkpoint.patch, ScenePatchDraft):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "blueprint patch must be a ScenePatchDraft contract",
        )
    if not isinstance(checkpoint.choreography, ChoreographyPlanV2):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "blueprint choreography must be a ChoreographyPlanV2 contract",
        )
    if not isinstance(checkpoint.presentation, PresentationCheckpointV1):
        _fail(
            ProjectileMotionVerificationCode.BLUEPRINT_CONTRACT,
            "blueprint presentation must be a PresentationCheckpointV1 contract",
        )

    base_physics = _derive_physics(checkpoint.base_component.problem_spec)
    result_physics = _derive_physics(checkpoint.result_component.problem_spec)
    _verify_transition(
        checkpoint.checkpoint_id,
        checkpoint.base_component,
        checkpoint.result_component,
    )

    base = _node_map(checkpoint.base_nodes, label="base")
    result = _node_map(checkpoint.result_nodes, label="result")
    prefix = f"{checkpoint.result_component.id}__"
    if any(not node_id.startswith(prefix) for node_id in (*base, *result)):
        _fail(
            ProjectileMotionVerificationCode.STABLE_IDS,
            "projectile snapshots may not contain foreign component nodes",
        )
    _verify_board_bounds(base.values(), label="base")
    _verify_board_bounds(result.values(), label="result")
    _verify_text_collisions(base.values(), label="base")
    _verify_text_collisions(result.values(), label="result")
    _verify_snapshot(
        checkpoint.base_component,
        base,
        base_physics,
        label="base snapshot",
    )
    _verify_snapshot(
        checkpoint.result_component,
        result,
        result_physics,
        label="result snapshot",
    )
    _verify_trajectory_physics(
        checkpoint.base_component.id,
        checkpoint.base_component,
        base,
        base_physics,
    )
    _verify_trajectory_physics(
        checkpoint.result_component.id,
        checkpoint.result_component,
        result,
        result_physics,
    )

    _verify_patch(
        checkpoint.result_component.id,
        checkpoint.checkpoint_id,
        base,
        result,
        checkpoint.patch,
    )
    if checkpoint.patch.narration != checkpoint.presentation.checkpoint_narration:
        _fail(
            ProjectileMotionVerificationCode.CAPTION,
            "patch narration must equal checkpointNarration",
        )
    visible_targets = _verify_choreography(
        checkpoint.result_component,
        checkpoint.checkpoint_id,
        base,
        result,
        checkpoint.choreography,
    )
    _verify_timing(checkpoint.checkpoint_id, checkpoint.choreography)
    _verify_presentation(
        checkpoint.checkpoint_id,
        checkpoint.base_component,
        checkpoint.result_component,
        base,
        result,
        checkpoint.presentation,
        visible_targets,
    )


__all__ = [
    "SAFE_VIEWPORT_PADDING",
    "ProjectileMotionCheckpointBlueprintLike",
    "ProjectileMotionVerificationCode",
    "ProjectileMotionVerificationError",
    "verify_projectile_motion_checkpoint",
]

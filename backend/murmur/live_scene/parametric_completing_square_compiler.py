"""Deterministic blueprints for bounded parametric completing-square lessons.

This module is the Gate 1.6 compiler boundary before receipt and certificate
construction.  A routed V3 beat may select only a closed stage or the single
corner clarification.  The compiler owns every visible node, derived value,
coordinate, caption, viewport, cue, and millisecond.

The tile geometry is deliberately symbolic and fixed across the supported
problem family.  In particular, no length is derived from either solved root:
doing so would be circular and would make the negative algebraic solution
impossible to represent honestly as a geometric length.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

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
    RoutedChoreographyBeatV3,
    TransformCueV1,
    ViewportPoseV1,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
    checkpoint_prefix,
    checkpoints_through,
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
    ScenePatchDraft,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1

Point: TypeAlias = tuple[float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

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
    "fontSize": 30.0,
    "opacity": 1.0,
}
_AMBER_EQUATION_STYLE = {**_EQUATION_STYLE, "color": "hsl(var(--amber))"}
_LABEL_STYLE = {
    "color": "hsl(var(--chalk))",
    "fontSize": 22.0,
    "opacity": 1.0,
}
_DETAIL_STYLE = {
    "color": "hsl(var(--amber))",
    "fontSize": 22.0,
    "opacity": 1.0,
}
_NOTE_STYLE = {
    "color": "hsl(var(--chalk-soft))",
    "fontSize": 16.0,
    "opacity": 1.0,
}

_INITIAL_VIEWPORTS = ((20.0, 60.0, 760.0, 480.0), (60.0, 25.0, 680.0, 550.0))
_RESULT_VIEWPORTS = {
    CompletingSquareCheckpointId.PROBLEM: (
        (90.0, 60.0, 620.0, 170.0),
        (80.0, 45.0, 640.0, 220.0),
    ),
    CompletingSquareCheckpointId.AREA_MODEL: _INITIAL_VIEWPORTS,
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: _INITIAL_VIEWPORTS,
    CompletingSquareCheckpointId.REARRANGE_HALVES: _INITIAL_VIEWPORTS,
    CompletingSquareCheckpointId.MISSING_CORNER: (
        (283.0, 370.0, 328.0, 190.0),
        (262.0, 345.0, 370.0, 225.0),
    ),
    CompletingSquareCheckpointId.CORNER_DETAIL: (
        (350.0, 360.0, 360.0, 210.0),
        (325.0, 335.0, 410.0, 240.0),
    ),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: _INITIAL_VIEWPORTS,
    CompletingSquareCheckpointId.FACTOR_SQUARE: _INITIAL_VIEWPORTS,
    CompletingSquareCheckpointId.SOLVE_ROOTS: (
        (20.0, 15.0, 760.0, 525.0),
        (60.0, 15.0, 680.0, 560.0),
    ),
}

# The eight-checkpoint main path is exactly 47 seconds at authored speed.
_TIMING = {
    CompletingSquareCheckpointId.PROBLEM: (600, 4_200, "ease_out_quart"),
    CompletingSquareCheckpointId.AREA_MODEL: (1_100, 5_200, "ease_out_quint"),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (800, 4_500, "ease_in_out"),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (1_400, 5_000, "ease_in_out"),
    CompletingSquareCheckpointId.MISSING_CORNER: (800, 4_200, "ease_out_quart"),
    CompletingSquareCheckpointId.CORNER_DETAIL: (700, 3_800, "ease_out_quart"),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (1_100, 5_200, "ease_in_out"),
    CompletingSquareCheckpointId.FACTOR_SQUARE: (900, 4_400, "ease_out_quint"),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (1_100, 6_500, "ease_out_quart"),
}


class ParametricCompletingSquareCompilationError(ValueError):
    """Raised before any checkpoint can be emitted for an illegal V3 route."""


@dataclass(frozen=True)
class ParametricCheckpointBlueprint:
    """One deterministic transition awaiting independent V3 verification."""

    checkpoint_id: CompletingSquareCheckpointId
    base_component: ParametricCompletingSquareStateV1
    result_component: ParametricCompletingSquareStateV1
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV1
    presentation: PresentationCheckpointV1


@dataclass(frozen=True)
class ParametricCheckpointBlueprintBatch:
    """The exact missing V3 checkpoint suffix for one problem-bound beat."""

    beat: RoutedChoreographyBeatV3
    base_component: ParametricCompletingSquareStateV1
    result_component: ParametricCompletingSquareStateV1
    checkpoints: tuple[ParametricCheckpointBlueprint, ...]


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
    height: float = 42.0,
    style: dict[str, object] | None = None,
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
            "style": style or _EQUATION_STYLE,
        }
    )


def _token_width(latex: str) -> float:
    """Return a browser-safe authored width for the finite token vocabulary.

    KaTeX layout is not proportional to the source-string length: grouped
    superscripts and delimiters are wider than their character count suggests,
    while ``\\text{or}`` is much narrower.  Keep those closed syntax classes
    explicit so the dense balance row stays within its compact viewport.
    """

    if latex == r"\text{or}":
        return 44.0
    if latex.startswith("(x+") and latex.endswith(")^2"):
        return 136.0
    if "=" in latex and latex.endswith("^2"):
        return 128.0
    if latex.startswith("x+") and latex.removeprefix("x+").isdigit():
        return 92.0
    if latex.startswith(r"\pm ") and latex.removeprefix(r"\pm ").isdigit():
        return 60.0
    if latex.startswith("-") and latex.removeprefix("-").isdigit():
        return 76.0 if len(latex) == 3 else 56.0
    if latex.endswith("x") and latex.removesuffix("x").isdigit():
        return 68.0 if len(latex.removesuffix("x")) == 2 else 42.0
    if latex.isdigit():
        return 48.0 if len(latex) == 2 else 30.0

    visible = latex.replace("^", "")
    return float(max(29, min(170, 16 + 13 * len(visible))))


def _linear_term(coefficient: int) -> str:
    """Render the unit coefficient canonically without leaking a ``1``."""

    return "x" if coefficient == 1 else f"{coefficient}x"


def _spoken_linear_term(coefficient: int) -> str:
    return "x" if coefficient == 1 else f"{coefficient} x"


def _equation_row(
    component_id: str,
    items: tuple[tuple[str, str, bool], ...],
    *,
    y: float,
    gap: float = 8.0,
) -> NodeMap:
    widths = tuple(_token_width(latex) for _, latex, _ in items)
    total_width = sum(widths) + gap * (len(items) - 1)
    cursor = (800.0 - total_width) / 2
    nodes: NodeMap = {}
    for (suffix, latex, highlighted), width in zip(items, widths, strict=True):
        center = cursor + width / 2
        node = _token(
            component_id,
            suffix,
            latex,
            center,
            y,
            width,
            height=48.0,
            style=_AMBER_EQUATION_STYLE if highlighted else _EQUATION_STYLE,
        )
        nodes[node.id] = node
        cursor += width + gap
    return nodes


def _initial_equation(component_id: str, problem: CompletingSquareProblemSpecV1) -> NodeMap:
    return _equation_row(
        component_id,
        (
            ("eq_square", "x^2", False),
            ("eq_plus_a", "+", False),
            ("eq_linear", _linear_term(problem.linear_coefficient), True),
            ("eq_equal_main", "=", False),
            ("eq_rhs", str(problem.right_hand_side), False),
        ),
        y=90.0,
    )


def _split_equation(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
    *,
    show_half_calculation: bool,
) -> NodeMap:
    half = problem.half_coefficient
    nodes = _equation_row(
        component_id,
        (
            ("eq_square", "x^2", False),
            ("eq_plus_a", "+", False),
            ("eq_half_a", _linear_term(half), True),
            ("eq_plus_b", "+", False),
            ("eq_half_b", _linear_term(half), True),
            ("eq_equal_main", "=", False),
            ("eq_rhs", str(problem.right_hand_side), False),
        ),
        y=90.0,
    )
    if show_half_calculation:
        calculation = _token(
            component_id,
            "half_calc",
            rf"{problem.linear_coefficient}\div 2={half}",
            400.0,
            150.0,
            130.0,
            height=38.0,
            style=_DETAIL_STYLE,
        )
        nodes[calculation.id] = calculation
    return nodes


def _completed_equation(component_id: str, problem: CompletingSquareProblemSpecV1) -> NodeMap:
    half = problem.half_coefficient
    corner = problem.corner_value
    return _equation_row(
        component_id,
        (
            ("eq_square", "x^2", False),
            ("eq_plus_a", "+", False),
            ("eq_half_a", _linear_term(half), False),
            ("eq_plus_b", "+", False),
            ("eq_half_b", _linear_term(half), False),
            ("eq_plus_corner", "+", False),
            ("eq_corner_value", str(corner), True),
            ("eq_equal_main", "=", False),
            ("eq_rhs", str(problem.right_hand_side), False),
            ("eq_plus_rhs", "+", False),
            ("eq_rhs_corner", str(corner), True),
            ("eq_equal_completed", "=", False),
            (
                "eq_completed_rhs",
                f"{problem.completed_right_hand_side}={problem.square_root_magnitude}^2",
                True,
            ),
        ),
        y=90.0,
        gap=6.0,
    )


def _factored_equation(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
    *,
    y: float,
) -> NodeMap:
    return _equation_row(
        component_id,
        (
            ("eq_factor", f"(x+{problem.half_coefficient})^2", False),
            ("eq_equal_completed", "=", False),
            (
                "eq_completed_rhs",
                f"{problem.completed_right_hand_side}={problem.square_root_magnitude}^2",
                True,
            ),
        ),
        y=y,
        gap=14.0,
    )


def _solution_tokens(component_id: str, problem: CompletingSquareProblemSpecV1) -> NodeMap:
    half = problem.half_coefficient
    magnitude = problem.square_root_magnitude
    nodes = _equation_row(
        component_id,
        (
            ("root_lhs", f"x+{half}", False),
            ("root_equal", "=", False),
            ("root_pm", rf"\pm {magnitude}", True),
        ),
        y=88.0,
        gap=14.0,
    )
    nodes.update(
        _equation_row(
            component_id,
            (
                ("root_x_a", "x", False),
                ("root_equal_a", "=", False),
                ("root_positive", str(problem.positive_root), True),
                ("root_or", r"\text{or}", False),
                ("root_x_b", "x", False),
                ("root_equal_b", "=", False),
                ("root_negative", str(problem.negative_root), True),
            ),
            y=144.0,
            gap=12.0,
        )
    )
    return nodes


def _geometry(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
    *,
    arranged: bool,
) -> NodeMap:
    if arranged:
        square = ((220.0, 240.0), (420.0, 240.0), (420.0, 440.0), (220.0, 440.0))
        strip_a = ((220.0, 440.0), (420.0, 440.0), (420.0, 494.0), (220.0, 494.0))
        strip_b = ((420.0, 240.0), (474.0, 240.0), (474.0, 440.0), (420.0, 440.0))
        centers = ((320.0, 315.0), (320.0, 447.0), (447.0, 315.0))
    else:
        square = ((180.0, 230.0), (380.0, 230.0), (380.0, 430.0), (180.0, 430.0))
        strip_a = ((180.0, 455.0), (380.0, 455.0), (380.0, 509.0), (180.0, 509.0))
        strip_b = ((430.0, 230.0), (484.0, 230.0), (484.0, 430.0), (430.0, 430.0))
        centers = ((280.0, 305.0), (280.0, 462.0), (457.0, 305.0))

    half = problem.half_coefficient
    nodes: tuple[SceneNode, ...] = (
        _path(component_id, "x2_square", square, _GEOMETRY_STYLE),
        _path(component_id, "strip_a", strip_a, _STRIP_A_STYLE),
        _path(component_id, "strip_b", strip_b, _STRIP_B_STYLE),
        _token(component_id, "area_x2", "x^2", *centers[0], 62.0, style=_LABEL_STYLE),
        _token(
            component_id,
            "area_half_a",
            _linear_term(half),
            *centers[1],
            50.0,
            style=_LABEL_STYLE,
        ),
        _token(
            component_id,
            "area_half_b",
            _linear_term(half),
            *centers[2],
            50.0,
            style=_LABEL_STYLE,
        ),
        _token(
            component_id,
            "scale_note",
            r"\text{not to scale}",
            660.0,
            200.0,
            132.0,
            height=32.0,
            style=_NOTE_STYLE,
        ),
    )
    return {node.id: node for node in nodes}


def _dimension_labels(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
) -> NodeMap:
    half = str(problem.half_coefficient)
    nodes = (
        _token(component_id, "dimension_x_top", "x", 320.0, 198.0, 28.0, style=_LABEL_STYLE),
        _token(component_id, "dimension_x_left", "x", 194.0, 315.0, 28.0, style=_LABEL_STYLE),
        _token(
            component_id,
            "corner_dim_h",
            half,
            447.0,
            494.0,
            28.0,
            height=34.0,
            style=_DETAIL_STYLE,
        ),
        _token(
            component_id,
            "corner_dim_v",
            half,
            488.0,
            447.0,
            28.0,
            height=34.0,
            style=_DETAIL_STYLE,
        ),
    )
    return {node.id: node for node in nodes}


def _corner(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
    *,
    filled: bool,
    revealed: bool,
) -> NodeMap:
    path = _path(
        component_id,
        "corner",
        ((420.0, 440.0), (474.0, 440.0), (474.0, 494.0), (420.0, 494.0)),
        _FILLED_CORNER_STYLE if filled else _MISSING_STYLE,
    )
    area = _token(
        component_id,
        "corner_area",
        str(problem.corner_value) if revealed else "?",
        447.0,
        447.0,
        48.0,
        height=34.0,
        style=_DETAIL_STYLE,
    )
    return {path.id: path, area.id: area}


def _corner_calculation(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
) -> NodeMap:
    half = problem.half_coefficient
    calculation = _token(
        component_id,
        "corner_calc",
        rf"{half}\times {half}={half}^2={problem.corner_value}",
        574.0,
        490.0,
        190.0,
        height=38.0,
        style=_DETAIL_STYLE,
    )
    return {calculation.id: calculation}


def _narration(
    checkpoint_id: CompletingSquareCheckpointId,
    problem: CompletingSquareProblemSpecV1,
) -> str:
    coefficient = problem.linear_coefficient
    rhs = problem.right_hand_side
    half = problem.half_coefficient
    corner = problem.corner_value
    completed = problem.completed_right_hand_side
    magnitude = problem.square_root_magnitude
    positive = problem.positive_root
    negative = problem.negative_root
    return {
        CompletingSquareCheckpointId.PROBLEM: (
            f"We will solve x squared plus {coefficient} x equals {rhs} by completing a square."
        ),
        CompletingSquareCheckpointId.AREA_MODEL: (
            f"The x squared term is a square, and {coefficient} x becomes two equal "
            f"{_spoken_linear_term(half)} strips. These tiles are symbolic and not to scale."
        ),
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
            f"Halving {coefficient} gives {half}, so split "
            f"{_spoken_linear_term(coefficient)} into {_spoken_linear_term(half)} plus "
            f"{_spoken_linear_term(half)}."
        ),
        CompletingSquareCheckpointId.REARRANGE_HALVES: (
            "Move those same equal strips beside the x by x square; no area has been added."
        ),
        CompletingSquareCheckpointId.MISSING_CORNER: (
            f"The almost-square is missing one corner whose two side lengths are {half}."
        ),
        CompletingSquareCheckpointId.CORNER_DETAIL: (
            f"The corner is {half} by {half}, so its area is {corner}."
        ),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
            f"Add the corner area {corner} to both sides. The right side becomes "
            f"{rhs} plus {corner}, which is {completed}."
        ),
        CompletingSquareCheckpointId.FACTOR_SQUARE: (
            f"The completed left side is x plus {half}, squared, and equals {completed}."
        ),
        CompletingSquareCheckpointId.SOLVE_ROOTS: (
            f"Now x plus {half} equals plus or minus {magnitude}, so x is {positive} "
            f"or {negative}. The tiles show a nonnegative-length branch; the algebra "
            "recovers both roots."
        ),
    }[checkpoint_id]


def _desired_nodes(
    component_id: str,
    problem: CompletingSquareProblemSpecV1,
    checkpoint: CompletingSquareMainCheckpoint,
    *,
    corner_clarified: bool,
) -> NodeMap:
    index = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(checkpoint)
    if index <= 1:
        nodes = _initial_equation(component_id, problem)
    elif index <= 4:
        nodes = _split_equation(
            component_id,
            problem,
            show_half_calculation=index == 2,
        )
    elif index == 5:
        nodes = _completed_equation(component_id, problem)
    else:
        nodes = _factored_equation(component_id, problem, y=32.0 if index == 7 else 90.0)
        if index == 7:
            nodes.update(_solution_tokens(component_id, problem))

    if index >= 1:
        nodes.update(_geometry(component_id, problem, arranged=index >= 3))
    if index >= 3:
        nodes.update(_dimension_labels(component_id, problem))
    if index >= 4:
        revealed = index >= 5 or corner_clarified
        nodes.update(
            _corner(
                component_id,
                problem,
                filled=index >= 5,
                revealed=revealed,
            )
        )
    if (index == 4 and corner_clarified) or index == 5:
        nodes.update(_corner_calculation(component_id, problem))
    return nodes


def _patch(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    problem: CompletingSquareProblemSpecV1,
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
        raise ParametricCompletingSquareCompilationError(
            "a parametric checkpoint must make a visible scene change"
        )
    if len(operations) > MAX_PATCH_OPERATIONS:
        raise ParametricCompletingSquareCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the {MAX_PATCH_OPERATIONS}-operation budget"
        )

    patch = ScenePatchDraft(
        patch_id=f"{component_id}__cp_{checkpoint_id.value}",
        narration=_narration(checkpoint_id, problem),
        operations=operations,
    )
    if (
        len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
        > MAX_NDJSON_FRAME_BYTES
    ):
        raise ParametricCompletingSquareCompilationError(
            f"checkpoint {checkpoint_id.value} exceeds the canonical 64 KiB budget"
        )

    result_order = [node.id for node in base_nodes if node.id in desired_nodes]
    result_order.extend(node_id for node_id in target_ids if node_id not in base_by_id)
    result_nodes = tuple(desired_nodes[node_id] for node_id in result_order)
    if {node.id: node for node in result_nodes} != desired_nodes:
        raise ParametricCompletingSquareCompilationError(
            "checkpoint diff did not materialize its target"
        )
    return patch, result_nodes


def _viewport_map(
    pair: tuple[tuple[float, float, float, float], tuple[float, float, float, float]],
) -> LayoutViewportMapV1:
    cinematic, compact = pair
    return LayoutViewportMapV1(
        cinematic=ViewportPoseV1(
            x=cinematic[0],
            y=cinematic[1],
            width=cinematic[2],
            height=cinematic[3],
        ),
        compact=ViewportPoseV1(
            x=compact[0],
            y=compact[1],
            width=compact[2],
            height=compact[3],
        ),
    )


def _base_viewports(state: ParametricCompletingSquareStateV1) -> LayoutViewportMapV1:
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
    component_id: str,
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
    emphasis_suffixes = {
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
            "eq_half_a",
            "eq_half_b",
            "half_calc",
        ),
        CompletingSquareCheckpointId.REARRANGE_HALVES: ("strip_a", "strip_b"),
        CompletingSquareCheckpointId.MISSING_CORNER: (
            "corner",
            "corner_dim_h",
            "corner_dim_v",
        ),
        CompletingSquareCheckpointId.CORNER_DETAIL: ("corner", "corner_area", "corner_calc"),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
            "corner_area",
            "eq_corner_value",
            "eq_rhs_corner",
        ),
        CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_factor", "x2_square"),
        CompletingSquareCheckpointId.SOLVE_ROOTS: ("root_negative", "root_positive"),
    }.get(checkpoint_id, ())
    focus_suffixes = {
        CompletingSquareCheckpointId.PROBLEM: ("eq_linear", "eq_square"),
        CompletingSquareCheckpointId.AREA_MODEL: ("strip_a", "strip_b", "x2_square"),
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
            "eq_half_a",
            "eq_half_b",
            "half_calc",
        ),
        CompletingSquareCheckpointId.REARRANGE_HALVES: ("strip_a", "strip_b"),
        CompletingSquareCheckpointId.MISSING_CORNER: ("corner", "corner_area"),
        CompletingSquareCheckpointId.CORNER_DETAIL: ("corner", "corner_area", "corner_calc"),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
            "eq_corner_value",
            "eq_completed_rhs",
            "eq_rhs_corner",
        ),
        CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_factor", "eq_completed_rhs"),
        CompletingSquareCheckpointId.SOLVE_ROOTS: (
            "root_negative",
            "root_pm",
            "root_positive",
        ),
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
    base_component: ParametricCompletingSquareStateV1,
    result_component: ParametricCompletingSquareStateV1,
    base_nodes: tuple[SceneNode, ...],
    desired_nodes: NodeMap,
) -> ParametricCheckpointBlueprint:
    patch, result_nodes = _patch(
        base_component.id,
        checkpoint_id,
        base_component.problem_spec,
        base_nodes,
        desired_nodes,
    )
    presentation = PresentationCheckpointV1(
        checkpoint_id=checkpoint_id.value,
        checkpoint_narration=patch.narration,
        base_viewports=_base_viewports(base_component),
        result_viewports=_viewport_map(_RESULT_VIEWPORTS[checkpoint_id]),
    )
    return ParametricCheckpointBlueprint(
        checkpoint_id=checkpoint_id,
        base_component=base_component,
        result_component=result_component,
        base_nodes=base_nodes,
        result_nodes=result_nodes,
        patch=patch,
        choreography=_cue_plan(base_component.id, checkpoint_id, patch, base_nodes),
        presentation=presentation,
    )


def materialize_parametric_nodes(
    state: ParametricCompletingSquareStateV1,
) -> tuple[SceneNode, ...]:
    """Reconstruct the exact component-local board for one accepted frontier."""

    nodes: tuple[SceneNode, ...] = ()
    component = ParametricCompletingSquareStateV1(
        id=state.id,
        problem_spec=state.problem_spec,
    )
    for checkpoint in checkpoint_prefix(state.last_main_checkpoint):
        desired = _desired_nodes(
            state.id,
            state.problem_spec,
            checkpoint,
            corner_clarified=False,
        )
        _, nodes = _patch(
            state.id,
            CompletingSquareCheckpointId(checkpoint.value),
            state.problem_spec,
            nodes,
            desired,
        )
        component = ParametricCompletingSquareStateV1(
            id=state.id,
            problem_spec=state.problem_spec,
            last_main_checkpoint=checkpoint,
            corner_clarified=component.corner_clarified,
        )
        if checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER and state.corner_clarified:
            detail = _desired_nodes(
                state.id,
                state.problem_spec,
                checkpoint,
                corner_clarified=True,
            )
            _, nodes = _patch(
                state.id,
                CompletingSquareCheckpointId.CORNER_DETAIL,
                state.problem_spec,
                nodes,
                detail,
            )
            component = ParametricCompletingSquareStateV1(
                id=state.id,
                problem_spec=state.problem_spec,
                last_main_checkpoint=checkpoint,
                corner_clarified=True,
            )
    if component != state:
        raise ParametricCompletingSquareCompilationError(
            "parametric semantic frontier is not materializable"
        )
    return nodes


def compile_parametric_checkpoint_blueprints(
    beat: RoutedChoreographyBeatV3,
    base_component: ParametricCompletingSquareStateV1 | None = None,
) -> ParametricCheckpointBlueprintBatch:
    """Build exactly the missing deterministic V3 component-local suffix."""

    if base_component is None:
        base_component = ParametricCompletingSquareStateV1(
            id=beat.component_id,
            problem_spec=beat.problem_spec,
        )
    if base_component.id != beat.component_id:
        raise ParametricCompletingSquareCompilationError(
            "routed beat componentId must match the base parametric state"
        )
    if base_component.problem_spec != beat.problem_spec:
        raise ParametricCompletingSquareCompilationError(
            "routed beat problemSpec must match the base parametric state"
        )

    base_nodes = materialize_parametric_nodes(base_component)
    current_component = base_component
    current_nodes = base_nodes
    checkpoints: list[ParametricCheckpointBlueprint] = []

    if isinstance(beat.route, ClarifyCornerRouteV2):
        if (
            current_component.last_main_checkpoint
            is not CompletingSquareMainCheckpoint.MISSING_CORNER
            or current_component.corner_clarified
        ):
            raise ParametricCompletingSquareCompilationError(
                "corner_detail is legal only at the unclarified missing_corner frontier"
            )
        result_component = ParametricCompletingSquareStateV1(
            id=beat.component_id,
            problem_spec=beat.problem_spec,
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
                beat.problem_spec,
                CompletingSquareMainCheckpoint.MISSING_CORNER,
                corner_clarified=True,
            ),
        )
        return ParametricCheckpointBlueprintBatch(
            beat=beat,
            base_component=base_component,
            result_component=result_component,
            checkpoints=(checkpoint,),
        )

    if not isinstance(beat.route, AdvanceChoreographyRouteV2):
        raise ParametricCompletingSquareCompilationError(
            "unsupported parametric choreography route"
        )
    target = checkpoints_through(beat.route.target_stage)
    current = checkpoint_prefix(current_component.last_main_checkpoint)
    if current != target[: len(current)]:
        raise ParametricCompletingSquareCompilationError(
            "a parametric completing-square component cannot move backward"
        )

    for main_checkpoint in target[len(current) :]:
        checkpoint_id = CompletingSquareCheckpointId(main_checkpoint.value)
        result_component = ParametricCompletingSquareStateV1(
            id=beat.component_id,
            problem_spec=beat.problem_spec,
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
                beat.problem_spec,
                main_checkpoint,
                corner_clarified=result_component.corner_clarified,
            ),
        )
        checkpoints.append(checkpoint)
        current_component = result_component
        current_nodes = checkpoint.result_nodes

    return ParametricCheckpointBlueprintBatch(
        beat=beat,
        base_component=base_component,
        result_component=current_component,
        checkpoints=tuple(checkpoints),
    )


__all__ = [
    "ParametricCheckpointBlueprint",
    "ParametricCheckpointBlueprintBatch",
    "ParametricCompletingSquareCompilationError",
    "compile_parametric_checkpoint_blueprints",
    "materialize_parametric_nodes",
]

"""Independent verification for problem-bound completing-square choreography.

This module deliberately does not import the parametric compiler or any of its
expected-node, layout, caption, or timing tables.  It accepts the compiler's
public output, reconstructs the scene transition, and derives every expected
mathematical value directly from the problem's primitive ``b`` and ``c``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import isclose, isfinite, isqrt
from typing import TypeAlias

from pydantic import TypeAdapter, ValidationError

from murmur.live_scene.choreography_contracts import (
    ChoreographyComponentId,
    ChoreographyPlanV1,
    LayoutViewportMapV1,
    PresentationCheckpointV1,
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
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    LatexSceneNode,
    LatexTokenSceneNode,
    LineSceneNode,
    PathSceneNode,
    PutSceneOperation,
    RectSceneNode,
    RemoveSceneOperation,
    SceneNode,
    ScenePatchDraft,
    SceneState,
    TextSceneNode,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointVerificationObligationV3,
    CheckpointVerificationReceiptV3,
)

Point: TypeAlias = tuple[float, float]
Box: TypeAlias = tuple[float, float, float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

SAFE_VIEWPORT_PADDING = 12.0

_EPSILON = 1e-6
_MIN_EQUATION_GEOMETRY_CLEARANCE = 16.0
_MAX_DIMENSION_LABEL_GAP = 60.0
_COMPONENT_ID_ADAPTER = TypeAdapter(ChoreographyComponentId)

_EQUATION_STYLE = ("hsl(var(--chalk))", 30.0, 1.0)
_HIGHLIGHT_STYLE = ("hsl(var(--amber))", 30.0, 1.0)
_LABEL_STYLE = ("hsl(var(--chalk))", 22.0, 1.0)
_DETAIL_STYLE = ("hsl(var(--amber))", 22.0, 1.0)
_NOTE_STYLE = ("hsl(var(--chalk-soft))", 16.0, 1.0)

_SHAPE_STYLES: Mapping[str, tuple[str, float, str, float, float]] = {
    "x2_square": ("hsl(var(--chalk))", 3.0, "#16171C", 1.0, 0.0),
    "strip_a": ("hsl(var(--sage))", 3.0, "#173626", 1.0, 0.0),
    "strip_b": ("hsl(var(--lavender))", 3.0, "#2E2850", 1.0, 0.0),
}

_LABEL_SUFFIXES = frozenset(
    {
        "area_x2",
        "area_half_a",
        "area_half_b",
        "dimension_x_top",
        "dimension_x_left",
    }
)
_DETAIL_SUFFIXES = frozenset(
    {
        "half_calc",
        "corner_area",
        "corner_dim_h",
        "corner_dim_v",
        "corner_calc",
    }
)

_HIGHLIGHT_SUFFIXES: Mapping[CompletingSquareCheckpointId, frozenset[str]] = {
    CompletingSquareCheckpointId.PROBLEM: frozenset({"eq_linear"}),
    CompletingSquareCheckpointId.AREA_MODEL: frozenset({"eq_linear"}),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: frozenset({"eq_half_a", "eq_half_b"}),
    CompletingSquareCheckpointId.REARRANGE_HALVES: frozenset({"eq_half_a", "eq_half_b"}),
    CompletingSquareCheckpointId.MISSING_CORNER: frozenset({"eq_half_a", "eq_half_b"}),
    CompletingSquareCheckpointId.CORNER_DETAIL: frozenset({"eq_half_a", "eq_half_b"}),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: frozenset(
        {"eq_corner_value", "eq_rhs_corner", "eq_completed_rhs"}
    ),
    CompletingSquareCheckpointId.FACTOR_SQUARE: frozenset({"eq_completed_rhs"}),
    CompletingSquareCheckpointId.SOLVE_ROOTS: frozenset(
        {"eq_completed_rhs", "root_pm", "root_positive", "root_negative"}
    ),
}


@dataclass(frozen=True, slots=True)
class _ExpectedMath:
    coefficient: int
    rhs: int
    half: int
    corner: int
    completed: int
    magnitude: int
    root_positive: int
    root_negative: int


def _fail(message: str) -> None:
    raise ParametricCompletingSquareVerificationError(message)


def _derive_math(problem: CompletingSquareProblemSpecV1) -> _ExpectedMath:
    """Revalidate and derive the family using only primitive serialized fields."""

    if not isinstance(problem, CompletingSquareProblemSpecV1):
        _fail("problem_spec must be a CompletingSquareProblemSpecV1 contract")
    coefficient = problem.linear_coefficient
    rhs = problem.right_hand_side
    if type(coefficient) is not int or type(rhs) is not int:
        _fail("problem coefficients must be strict integers")
    if coefficient < 2 or coefficient > 16 or coefficient % 2:
        _fail("linear coefficient is outside the supported even domain")
    if rhs < 1 or rhs > 80:
        _fail("right-hand side is outside the supported positive domain")

    half = coefficient // 2
    corner = half * half
    completed = rhs + corner
    magnitude = isqrt(completed)
    if magnitude * magnitude != completed or not 1 <= half < magnitude <= 9:
        _fail("problem does not belong to the bounded completing-square family")
    return _ExpectedMath(
        coefficient=coefficient,
        rhs=rhs,
        half=half,
        corner=corner,
        completed=completed,
        magnitude=magnitude,
        root_positive=magnitude - half,
        root_negative=-(magnitude + half),
    )


_GEOMETRY_SUFFIXES = frozenset({"x2_square", "strip_a", "strip_b"})

_FOCUS_SUFFIXES: Mapping[CompletingSquareCheckpointId, tuple[str, ...]] = {
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
        "eq_completed_rhs",
        "eq_corner_value",
        "eq_rhs_corner",
    ),
    CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_completed_rhs", "eq_factor"),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (
        "root_negative",
        "root_pm",
        "root_positive",
    ),
}

_EMPHASIS_SUFFIXES: Mapping[CompletingSquareCheckpointId, tuple[str, ...]] = {
    CompletingSquareCheckpointId.PROBLEM: (),
    CompletingSquareCheckpointId.AREA_MODEL: (),
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
}

_INITIAL_EQUATION_VIEWPORT_SUFFIXES = frozenset(
    {"eq_square", "eq_plus_a", "eq_linear", "eq_equal_main", "eq_rhs"}
)
_SPLIT_EQUATION_VIEWPORT_SUFFIXES = frozenset(
    {
        "eq_square",
        "eq_plus_a",
        "eq_half_a",
        "eq_plus_b",
        "eq_half_b",
        "eq_equal_main",
        "eq_rhs",
    }
)
_COMPLETED_EQUATION_VIEWPORT_SUFFIXES = frozenset(
    {
        "eq_square",
        "eq_plus_a",
        "eq_half_a",
        "eq_plus_b",
        "eq_half_b",
        "eq_plus_corner",
        "eq_corner_value",
        "eq_equal_main",
        "eq_rhs",
        "eq_plus_rhs",
        "eq_rhs_corner",
        "eq_equal_completed",
        "eq_completed_rhs",
    }
)
_FACTORED_EQUATION_VIEWPORT_SUFFIXES = frozenset(
    {"eq_factor", "eq_equal_completed", "eq_completed_rhs"}
)
_AREA_VIEWPORT_SUFFIXES = frozenset(
    {
        "x2_square",
        "strip_a",
        "strip_b",
        "area_x2",
        "area_half_a",
        "area_half_b",
        "scale_note",
    }
)
_DIMENSION_VIEWPORT_SUFFIXES = frozenset(
    {"dimension_x_top", "dimension_x_left", "corner_dim_h", "corner_dim_v"}
)
_CORNER_VIEWPORT_SUFFIXES = frozenset({"corner", "corner_area"})
_ROOT_VIEWPORT_SUFFIXES = frozenset(
    {
        "root_lhs",
        "root_equal",
        "root_pm",
        "root_x_a",
        "root_equal_a",
        "root_positive",
        "root_or",
        "root_x_b",
        "root_equal_b",
        "root_negative",
    }
)

_PREDECESSORS: Mapping[
    CompletingSquareCheckpointId,
    tuple[CompletingSquareCheckpointId | None, ...],
] = {
    CompletingSquareCheckpointId.PROBLEM: (None,),
    CompletingSquareCheckpointId.AREA_MODEL: (CompletingSquareCheckpointId.PROBLEM,),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (CompletingSquareCheckpointId.AREA_MODEL,),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
    ),
    CompletingSquareCheckpointId.MISSING_CORNER: (CompletingSquareCheckpointId.REARRANGE_HALVES,),
    CompletingSquareCheckpointId.CORNER_DETAIL: (CompletingSquareCheckpointId.MISSING_CORNER,),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.CORNER_DETAIL,
    ),
    CompletingSquareCheckpointId.FACTOR_SQUARE: (
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    ),
    CompletingSquareCheckpointId.SOLVE_ROOTS: (CompletingSquareCheckpointId.FACTOR_SQUARE,),
}

_TIMING: Mapping[CompletingSquareCheckpointId, tuple[int, int, str]] = {
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

_MAIN_ORDINAL = {
    CompletingSquareCheckpointId(checkpoint.value): index
    for index, checkpoint in enumerate(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER)
}


class ParametricCompletingSquareVerificationError(ValueError):
    """Raised when any independent V3 verification obligation fails."""


def _node_id(component_id: str, suffix: str) -> str:
    return f"{component_id}__{suffix}"


def _suffix(node_id: str, component_id: str) -> str:
    prefix = f"{component_id}__"
    if not node_id.startswith(prefix):
        _fail(f"node {node_id!r} is outside component namespace {prefix!r}")
    return node_id[len(prefix) :]


def _node_map(nodes: Sequence[SceneNode], *, label: str) -> NodeMap:
    node_ids = tuple(node.id for node in nodes)
    if len(node_ids) != len(set(node_ids)):
        _fail(f"{label} scene node ids must be unique")
    return {node.id: node for node in nodes}


def _owned_nodes(nodes: Sequence[SceneNode], component_id: str) -> NodeMap:
    prefix = f"{component_id}__"
    return {node.id: node for node in nodes if node.id.startswith(prefix)}


def _close(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=0.0, abs_tol=_EPSILON)


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
    if isinstance(node, RectSceneNode):
        half_stroke = node.style.stroke_width / 2.0
        return (
            node.x - half_stroke,
            node.y - half_stroke,
            node.x + node.width + half_stroke,
            node.y + node.height + half_stroke,
        )
    if isinstance(node, LatexTokenSceneNode):
        left = node.x
        if node.anchor == "middle":
            left -= node.width / 2.0
        elif node.anchor == "end":
            left -= node.width
        return (left, node.y, left + node.width, node.y + node.height)
    if isinstance(node, (TextSceneNode, LatexSceneNode)):
        return (node.x, node.y, node.x, node.y)
    _fail(f"unsupported scene node kind for {node.id!r}")


def _require_box_on_board(box: Box, *, label: str) -> None:
    if not all(isfinite(value) for value in box):
        _fail(f"{label} has non-finite bounds")
    left, top, right, bottom = box
    if left < -_EPSILON or top < -_EPSILON:
        _fail(f"{label} is clipped above or left of the board")
    if right > LIVE_SCENE_BOARD_WIDTH + _EPSILON:
        _fail(f"{label} is clipped beyond the board width")
    if bottom > LIVE_SCENE_BOARD_HEIGHT + _EPSILON:
        _fail(f"{label} is clipped beyond the board height")
    if right < left or bottom < top:
        _fail(f"{label} has inverted bounds")


def _verify_board_bounds(nodes: Iterable[SceneNode], *, label: str) -> None:
    for node in nodes:
        _require_box_on_board(_node_box(node), label=f"{label} node {node.id}")


def _materialize_patch(base_scene: SceneState, patch: ScenePatchDraft) -> tuple[SceneNode, ...]:
    order = [node.id for node in base_scene.nodes]
    nodes = _node_map(base_scene.nodes, label="base")
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            previous = nodes.get(operation.node.id)
            if previous == operation.node:
                _fail(f"put operation for {operation.node.id!r} is a no-op")
            if previous is None:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
            continue
        if not isinstance(operation, RemoveSceneOperation):
            _fail("patch contains an unsupported operation")
        if operation.id not in nodes:
            _fail(f"remove operation targets absent node {operation.id!r}")
        order.remove(operation.id)
        del nodes[operation.id]
    return tuple(nodes[node_id] for node_id in order)


def _rectangle_box(node: SceneNode, *, suffix: str) -> Box:
    if not isinstance(node, PathSceneNode) or not node.closed or len(node.points) != 4:
        _fail(f"{suffix} must be one closed four-point path")
    xs = sorted({point[0] for point in node.points})
    ys = sorted({point[1] for point in node.points})
    if len(xs) != 2 or len(ys) != 2:
        _fail(f"{suffix} must be axis-aligned")
    corners = {(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])}
    if set(node.points) != corners:
        _fail(f"{suffix} must contain each rectangle corner exactly once")
    if _close(xs[0], xs[1]) or _close(ys[0], ys[1]):
        _fail(f"{suffix} must have positive area")
    for start, end in zip(node.points, (*node.points[1:], node.points[0]), strict=True):
        horizontal_edge = _close(start[1], end[1]) and not _close(start[0], end[0])
        vertical_edge = _close(start[0], end[0]) and not _close(start[1], end[1])
        if not (horizontal_edge or vertical_edge):
            _fail(f"{suffix} must follow rectangle perimeter edge order")
    return (xs[0], ys[0], xs[1], ys[1])


def _rectangle_corner_order(node: PathSceneNode) -> tuple[int, ...]:
    box = _rectangle_box(node, suffix=node.id)
    corners = (
        (box[0], box[1]),
        (box[2], box[1]),
        (box[2], box[3]),
        (box[0], box[3]),
    )
    return tuple(corners.index(point) for point in node.points)


def _box_width(box: Box) -> float:
    return box[2] - box[0]


def _box_height(box: Box) -> float:
    return box[3] - box[1]


def _box_area(box: Box) -> float:
    return _box_width(box) * _box_height(box)


def _union_box(boxes: Sequence[Box]) -> Box:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _interiors_overlap(left: Box, right: Box) -> bool:
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > _EPSILON
        and min(left[3], right[3]) - max(left[1], right[1]) > _EPSILON
    )


def _require_no_interior_overlap(boxes: Mapping[str, Box], *, label: str) -> None:
    items = tuple(boxes.items())
    for index, (left_id, left) in enumerate(items):
        for right_id, right in items[index + 1 :]:
            if _interiors_overlap(left, right):
                _fail(f"{label} collision between {left_id} and {right_id}")


def _box_center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _require_box_inside(inner: Box, outer: Box, *, label: str) -> None:
    if (
        inner[0] < outer[0] - _EPSILON
        or inner[1] < outer[1] - _EPSILON
        or inner[2] > outer[2] + _EPSILON
        or inner[3] > outer[3] + _EPSILON
    ):
        _fail(f"{label} is not fully contained in its associated area-model shape")


def _token(node: SceneNode, *, suffix: str, latex: str) -> LatexTokenSceneNode:
    if not isinstance(node, LatexTokenSceneNode):
        _fail(f"{suffix} must be a measured LaTeX token")
    if node.latex != latex:
        _fail(f"{suffix} must contain exact LaTeX {latex!r}")
    return node


def _visible_glyph_count(latex: str) -> int:
    visible = re.sub(r"\\text\{([^{}]*)\}", r"\1", latex)
    visible = visible.replace(r"\times", "x").replace(r"\div", "/").replace(r"\pm", "+")
    visible = visible.replace("^", "").replace("{", "").replace("}", "")
    visible = visible.replace("\\", "").replace(" ", "")
    return max(1, len(visible))


def _finite_token_minimum_width(suffix: str, latex: str) -> float:
    """Independently enforce browser-safe floors for compound V3 tokens."""

    if suffix == "eq_factor":
        return 136.0
    if suffix == "eq_completed_rhs":
        return 128.0
    if suffix == "root_lhs":
        return 92.0
    if suffix == "root_pm":
        return 60.0
    if suffix == "root_positive":
        return 30.0
    if suffix == "root_negative":
        return 76.0 if len(latex) == 3 else 56.0
    if suffix == "root_or":
        return 44.0
    if suffix == "eq_linear" and len(latex.removesuffix("x")) == 2:
        return 68.0
    return 20.0


def _expected_token_style(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
) -> tuple[str, float, float]:
    if suffix == "scale_note":
        return _NOTE_STYLE
    if suffix in _LABEL_SUFFIXES:
        return _LABEL_STYLE
    if suffix in _DETAIL_SUFFIXES:
        return _DETAIL_STYLE
    if suffix in _HIGHLIGHT_SUFFIXES[checkpoint_id]:
        return _HIGHLIGHT_STYLE
    return _EQUATION_STYLE


def _verify_token_visual_contract(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    node: LatexTokenSceneNode,
) -> None:
    if node.presentation.enter != "fade" or node.presentation.exit != "fade":
        _fail(f"{suffix} must use the visible canonical token presentation")
    if node.anchor != "middle":
        _fail(f"{suffix} must use the canonical middle token anchor")

    if node.style.opacity <= 0.0:
        _fail(f"{suffix} must remain visible")
    actual_style = (node.style.color, node.style.font_size, node.style.opacity)
    if actual_style != _expected_token_style(checkpoint_id, suffix):
        _fail(f"{suffix} does not use its canonical visible token style")

    glyph_count = _visible_glyph_count(node.latex)
    minimum_width = max(
        20.0,
        node.style.font_size * (0.28 * glyph_count + 0.45),
        _finite_token_minimum_width(suffix, node.latex),
    )
    if node.width < minimum_width - _EPSILON:
        _fail(f"{suffix} token width is too small for its visible mathematical fact")
    if node.width > 240.0 + _EPSILON:
        _fail(f"{suffix} token width exceeds the closed visual budget")
    minimum_height = 48.0 if node.style.font_size == 30.0 else node.style.font_size
    if node.height < minimum_height - _EPSILON:
        _fail(f"{suffix} token height is too small for its font")
    if node.height > 2.5 * node.style.font_size + _EPSILON:
        _fail(f"{suffix} token height exceeds the closed visual budget")


def _verify_path_visual_contract(
    checkpoint_id: CompletingSquareCheckpointId,
    suffix: str,
    node: PathSceneNode,
) -> None:
    if node.presentation.enter != "draw" or node.presentation.exit != "fade":
        _fail(f"{suffix} must use the visible canonical path presentation")
    if suffix == "corner":
        filled = _at_or_after(checkpoint_id, "balance_and_complete")
        expected = (
            "hsl(var(--amber))",
            3.0,
            "#4A3212" if filled else "transparent",
            1.0,
            0.0,
        )
    else:
        expected = _SHAPE_STYLES.get(suffix)
    if expected is None:
        _fail(f"{suffix} is not a recognized path visual class")
    if node.style.opacity <= 0.0:
        _fail(f"{suffix} must remain visible")
    actual = (
        node.style.stroke,
        node.style.stroke_width,
        node.style.fill,
        node.style.opacity,
        node.style.roughness,
    )
    if actual != expected:
        _fail(f"{suffix} does not use its canonical visible path style")


def _verify_visual_contract(
    checkpoint_id: CompletingSquareCheckpointId,
    by_suffix: Mapping[str, SceneNode],
) -> None:
    for suffix, node in by_suffix.items():
        if isinstance(node, LatexTokenSceneNode):
            _verify_token_visual_contract(checkpoint_id, suffix, node)
        elif isinstance(node, PathSceneNode):
            _verify_path_visual_contract(checkpoint_id, suffix, node)
        else:
            _fail(f"{suffix} is outside the closed token-and-path visual vocabulary")


def _initial_equation(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("eq_square", "x^2"),
        ("eq_plus_a", "+"),
        ("eq_linear", f"{math.coefficient}x"),
        ("eq_equal_main", "="),
        ("eq_rhs", str(math.rhs)),
    )


def _linear_term(coefficient: int) -> str:
    return "x" if coefficient == 1 else f"{coefficient}x"


def _spoken_linear_term(coefficient: int) -> str:
    return "x" if coefficient == 1 else f"{coefficient} x"


def _parse_linear_term(latex: str) -> int:
    return 1 if latex == "x" else int(latex.removesuffix("x"))


def _split_equation(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("eq_square", "x^2"),
        ("eq_plus_a", "+"),
        ("eq_half_a", _linear_term(math.half)),
        ("eq_plus_b", "+"),
        ("eq_half_b", _linear_term(math.half)),
        ("eq_equal_main", "="),
        ("eq_rhs", str(math.rhs)),
    )


def _completed_equation(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("eq_square", "x^2"),
        ("eq_plus_a", "+"),
        ("eq_half_a", _linear_term(math.half)),
        ("eq_plus_b", "+"),
        ("eq_half_b", _linear_term(math.half)),
        ("eq_plus_corner", "+"),
        ("eq_corner_value", str(math.corner)),
        ("eq_equal_main", "="),
        ("eq_rhs", str(math.rhs)),
        ("eq_plus_rhs", "+"),
        ("eq_rhs_corner", str(math.corner)),
        ("eq_equal_completed", "="),
        ("eq_completed_rhs", f"{math.completed}={math.magnitude}^2"),
    )


def _factored_equation(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("eq_factor", f"(x+{math.half})^2"),
        ("eq_equal_completed", "="),
        ("eq_completed_rhs", f"{math.completed}={math.magnitude}^2"),
    )


def _root_equation(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("root_lhs", f"x+{math.half}"),
        ("root_equal", "="),
        ("root_pm", rf"\pm {math.magnitude}"),
    )


def _root_results(math: _ExpectedMath) -> tuple[tuple[str, str], ...]:
    return (
        ("root_x_a", "x"),
        ("root_equal_a", "="),
        ("root_positive", str(math.root_positive)),
        ("root_or", r"\text{or}"),
        ("root_x_b", "x"),
        ("root_equal_b", "="),
        ("root_negative", str(math.root_negative)),
    )


def _at_or_after(checkpoint_id: CompletingSquareCheckpointId, threshold: str) -> bool:
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        ordinal = _MAIN_ORDINAL[CompletingSquareCheckpointId.MISSING_CORNER]
    else:
        ordinal = _MAIN_ORDINAL[checkpoint_id]
    return ordinal >= _MAIN_ORDINAL[CompletingSquareCheckpointId(threshold)]


def _equation_terms(
    checkpoint_id: CompletingSquareCheckpointId,
    math: _ExpectedMath,
) -> tuple[tuple[str, str], ...]:
    if checkpoint_id in {
        CompletingSquareCheckpointId.PROBLEM,
        CompletingSquareCheckpointId.AREA_MODEL,
    }:
        return _initial_equation(math)
    if checkpoint_id in {
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
        CompletingSquareCheckpointId.REARRANGE_HALVES,
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.CORNER_DETAIL,
    }:
        return _split_equation(math)
    if checkpoint_id is CompletingSquareCheckpointId.BALANCE_AND_COMPLETE:
        return _completed_equation(math)
    return _factored_equation(math)


def _expected_latex(
    checkpoint_id: CompletingSquareCheckpointId,
    math: _ExpectedMath,
) -> dict[str, str]:
    expected = dict(_equation_terms(checkpoint_id, math))
    if _at_or_after(checkpoint_id, "area_model"):
        expected.update(
            {
                "area_x2": "x^2",
                "area_half_a": _linear_term(math.half),
                "area_half_b": _linear_term(math.half),
                "scale_note": r"\text{not to scale}",
            }
        )
    if checkpoint_id is CompletingSquareCheckpointId.SPLIT_LINEAR_TERM:
        expected["half_calc"] = rf"{math.coefficient}\div 2={math.half}"
    if _at_or_after(checkpoint_id, "rearrange_halves"):
        expected.update(
            {
                "dimension_x_top": "x",
                "dimension_x_left": "x",
                "corner_dim_h": str(math.half),
                "corner_dim_v": str(math.half),
            }
        )
    if _at_or_after(checkpoint_id, "missing_corner"):
        revealed = checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL or _at_or_after(
            checkpoint_id,
            "balance_and_complete",
        )
        expected["corner_area"] = str(math.corner) if revealed else "?"
    if checkpoint_id in {
        CompletingSquareCheckpointId.CORNER_DETAIL,
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    }:
        expected["corner_calc"] = rf"{math.half}\times {math.half}={math.half}^2={math.corner}"
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        expected.update(_root_equation(math))
        expected.update(_root_results(math))
    return expected


def _expected_suffixes(
    checkpoint_id: CompletingSquareCheckpointId,
    math: _ExpectedMath,
) -> frozenset[str]:
    suffixes = set(_expected_latex(checkpoint_id, math))
    if _at_or_after(checkpoint_id, "area_model"):
        suffixes.update(_GEOMETRY_SUFFIXES)
    if _at_or_after(checkpoint_id, "missing_corner"):
        suffixes.add("corner")
    return frozenset(suffixes)


def _component_suffix_map(nodes: NodeMap, component_id: str) -> dict[str, SceneNode]:
    return {_suffix(node_id, component_id): node for node_id, node in nodes.items()}


def _require_token_row(
    nodes: NodeMap,
    component_id: str,
    terms: Sequence[tuple[str, str]],
) -> None:
    previous_box: Box | None = None
    row_y: float | None = None
    for suffix, latex in terms:
        node = _token(nodes[_node_id(component_id, suffix)], suffix=suffix, latex=latex)
        box = _node_box(node)
        if row_y is None:
            row_y = node.y
        elif not _close(node.y, row_y):
            _fail(f"equation token {suffix} is not on its required row")
        if previous_box is not None and box[0] < previous_box[2] - _EPSILON:
            _fail(f"equation token {suffix} overlaps or precedes its prior term")
        previous_box = box


def _verify_token_collisions(by_suffix: Mapping[str, SceneNode]) -> None:
    boxes = {
        suffix: _node_box(node)
        for suffix, node in by_suffix.items()
        if isinstance(node, LatexTokenSceneNode)
    }
    _require_no_interior_overlap(boxes, label="LaTeX token")


def _derivation_terms(
    checkpoint_id: CompletingSquareCheckpointId,
    math: _ExpectedMath,
) -> tuple[tuple[str, str], ...]:
    terms = _equation_terms(checkpoint_id, math)
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        return (*terms, *_root_equation(math), *_root_results(math))
    return terms


def _verify_equation_geometry_clearance(
    checkpoint_id: CompletingSquareCheckpointId,
    math: _ExpectedMath,
    by_suffix: Mapping[str, SceneNode],
) -> None:
    equation_bottom = max(
        _node_box(by_suffix[suffix])[3] for suffix, _ in _derivation_terms(checkpoint_id, math)
    )
    geometry_suffixes = (*_GEOMETRY_SUFFIXES, *(("corner",) if "corner" in by_suffix else ()))
    geometry_top = min(_node_box(by_suffix[suffix])[1] for suffix in geometry_suffixes)
    if geometry_top - equation_bottom < _MIN_EQUATION_GEOMETRY_CLEARANCE - _EPSILON:
        _fail("equation derivation must keep intentional clearance above area-model geometry")


def _verify_area_model(
    by_suffix: Mapping[str, SceneNode],
    math: _ExpectedMath,
) -> tuple[Box, Box, Box, float]:
    square = _rectangle_box(by_suffix["x2_square"], suffix="x2_square")
    strip_a = _rectangle_box(by_suffix["strip_a"], suffix="strip_a")
    strip_b = _rectangle_box(by_suffix["strip_b"], suffix="strip_b")
    side = _box_width(square)
    if not _close(side, _box_height(square)):
        _fail("x2_square must have four equal sides")

    dimensions_a = sorted((_box_width(strip_a), _box_height(strip_a)))
    dimensions_b = sorted((_box_width(strip_b), _box_height(strip_b)))
    if not all(_close(left, right) for left, right in zip(dimensions_a, dimensions_b, strict=True)):
        _fail("the two half-linear strips must be congruent")
    if not _close(dimensions_a[1], side):
        _fail("each half-linear strip must have the x2_square side length")
    thickness = dimensions_a[0]
    if thickness <= _EPSILON or thickness >= side - _EPSILON:
        _fail("each half-linear strip must have one positive shorter dimension")

    labels = {
        "area_x2": ("x^2", square),
        "area_half_a": (_linear_term(math.half), strip_a),
        "area_half_b": (_linear_term(math.half), strip_b),
    }
    for suffix, (latex, shape) in labels.items():
        label = _token(by_suffix[suffix], suffix=suffix, latex=latex)
        _require_box_inside(_node_box(label), shape, label=suffix)
    _token(by_suffix["scale_note"], suffix="scale_note", latex=r"\text{not to scale}")
    return square, strip_a, strip_b, thickness


def _attachment_side(square: Box, strip: Box) -> str | None:
    same_horizontal_span = _close(strip[0], square[0]) and _close(strip[2], square[2])
    same_vertical_span = _close(strip[1], square[1]) and _close(strip[3], square[3])
    if same_horizontal_span and _close(strip[3], square[1]):
        return "top"
    if same_horizontal_span and _close(strip[1], square[3]):
        return "bottom"
    if same_vertical_span and _close(strip[2], square[0]):
        return "left"
    if same_vertical_span and _close(strip[0], square[2]):
        return "right"
    return None


def _verify_adjacent_arrangement(
    square: Box,
    strip_a: Box,
    strip_b: Box,
    thickness: float,
) -> tuple[Box, Box]:
    boxes = {"x2_square": square, "strip_a": strip_a, "strip_b": strip_b}
    _require_no_interior_overlap(boxes, label="area-model shape")
    sides = (_attachment_side(square, strip_a), _attachment_side(square, strip_b))
    if None in sides or sides[0] == sides[1]:
        _fail("the two strips must attach to distinct full square sides")
    opposites = {frozenset({"top", "bottom"}), frozenset({"left", "right"})}
    if frozenset(sides) in opposites:
        _fail("the two strips must attach to adjacent, not opposite, sides")

    outer = _union_box(tuple(boxes.values()))
    side = _box_width(square)
    if not _close(_box_width(outer), side + thickness) or not _close(
        _box_height(outer),
        side + thickness,
    ):
        _fail("adjacent strips must leave one thickness-by-thickness corner gap")
    gap_area = _box_area(outer) - sum(_box_area(box) for box in boxes.values())
    if not _close(gap_area, thickness * thickness):
        _fail("rearranged strips leave the wrong missing-corner area")

    horizontal = "bottom" if "bottom" in sides else "top"
    vertical = "right" if "right" in sides else "left"
    gap_x = (square[2], outer[2]) if vertical == "right" else (outer[0], square[0])
    gap_y = (square[3], outer[3]) if horizontal == "bottom" else (outer[1], square[1])
    gap = (gap_x[0], gap_y[0], gap_x[1], gap_y[1])
    if not _close(_box_width(gap), thickness) or not _close(_box_height(gap), thickness):
        _fail("adjacent strips do not identify one square corner gap")
    return outer, gap


def _verify_dimensions(
    by_suffix: Mapping[str, SceneNode],
    square: Box,
    gap: Box,
    math: _ExpectedMath,
) -> None:
    top = _token(by_suffix["dimension_x_top"], suffix="dimension_x_top", latex="x")
    left = _token(by_suffix["dimension_x_left"], suffix="dimension_x_left", latex="x")
    top_box = _node_box(top)
    left_box = _node_box(left)
    top_center = _box_center(top_box)
    left_center = _box_center(left_box)
    if not square[0] <= top_center[0] <= square[2] or top_box[3] > square[1] + _EPSILON:
        _fail("top x dimension must be associated with the square's top edge")
    if not square[1] <= left_center[1] <= square[3] or left_box[2] > square[0] + _EPSILON:
        _fail("left x dimension must be associated with the square's left edge")

    horizontal = _token(
        by_suffix["corner_dim_h"],
        suffix="corner_dim_h",
        latex=str(math.half),
    )
    vertical = _token(
        by_suffix["corner_dim_v"],
        suffix="corner_dim_v",
        latex=str(math.half),
    )
    horizontal_box = _node_box(horizontal)
    vertical_box = _node_box(vertical)
    horizontal_center = _box_center(horizontal_box)
    vertical_center = _box_center(vertical_box)
    if not gap[0] <= horizontal_center[0] <= gap[2]:
        _fail("horizontal corner dimension must align with the missing gap")
    if horizontal_box[1] < gap[3] - _EPSILON:
        _fail("horizontal corner dimension must sit outside the missing gap")
    if horizontal_box[1] - gap[3] > _MAX_DIMENSION_LABEL_GAP:
        _fail("horizontal corner dimension is too far from the missing gap")
    if not gap[1] <= vertical_center[1] <= gap[3]:
        _fail("vertical corner dimension must align with the missing gap")
    if vertical_box[0] < gap[2] - _EPSILON:
        _fail("vertical corner dimension must sit outside the missing gap")
    if vertical_box[0] - gap[2] > _MAX_DIMENSION_LABEL_GAP:
        _fail("vertical corner dimension is too far from the missing gap")


def _boxes_match(left: Box, right: Box) -> bool:
    return all(_close(a, b) for a, b in zip(left, right, strict=True))


def _verify_corner(
    by_suffix: Mapping[str, SceneNode],
    *,
    outer_without_corner: Box,
    gap: Box,
    square: Box,
    strip_a: Box,
    strip_b: Box,
    thickness: float,
    filled: bool,
    revealed: bool,
    math: _ExpectedMath,
) -> None:
    corner_node = by_suffix["corner"]
    corner = _rectangle_box(corner_node, suffix="corner")
    if not _close(_box_width(corner), _box_height(corner)):
        _fail("missing corner must be square")
    if not _close(_box_width(corner), thickness) or not _close(_box_height(corner), thickness):
        _fail("missing corner side lengths must equal the strip thickness")
    if not _boxes_match(corner, gap):
        _fail("corner must exactly occupy the rearrangement's missing gap")

    all_boxes = {
        "x2_square": square,
        "strip_a": strip_a,
        "strip_b": strip_b,
        "corner": corner,
    }
    _require_no_interior_overlap(all_boxes, label="completed-square shape")
    completed_outer = _union_box(tuple(all_boxes.values()))
    if not _boxes_match(completed_outer, outer_without_corner):
        _fail("corner must preserve the rearrangement's outer bounds")
    if not _close(sum(_box_area(box) for box in all_boxes.values()), _box_area(completed_outer)):
        _fail("corner leaves a gap or overlaps the completed square")

    if not isinstance(corner_node, PathSceneNode):
        _fail("corner must be a path")
    empty_fill = corner_node.style.fill in {"none", "transparent"}
    if filled == empty_fill:
        _fail("corner fill does not match the checkpoint's completed state")
    expected_area = str(math.corner) if revealed else "?"
    _token(by_suffix["corner_area"], suffix="corner_area", latex=expected_area)
    _require_box_inside(_node_box(by_suffix["corner_area"]), corner, label="corner_area")


def _verify_caption(
    checkpoint_id: CompletingSquareCheckpointId,
    narration: str,
    math: _ExpectedMath,
) -> None:
    expected = {
        CompletingSquareCheckpointId.PROBLEM: (
            f"We will solve x squared plus {math.coefficient} x equals {math.rhs} by "
            "completing a square."
        ),
        CompletingSquareCheckpointId.AREA_MODEL: (
            f"The x squared term is a square, and {math.coefficient} x becomes two equal "
            f"{_spoken_linear_term(math.half)} strips. These tiles are symbolic and not "
            "to scale."
        ),
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (
            f"Halving {math.coefficient} gives {math.half}, so split "
            f"{_spoken_linear_term(math.coefficient)} into "
            f"{_spoken_linear_term(math.half)} plus {_spoken_linear_term(math.half)}."
        ),
        CompletingSquareCheckpointId.REARRANGE_HALVES: (
            "Move those same equal strips beside the x by x square; no area has been added."
        ),
        CompletingSquareCheckpointId.MISSING_CORNER: (
            f"The almost-square is missing one corner whose two side lengths are {math.half}."
        ),
        CompletingSquareCheckpointId.CORNER_DETAIL: (
            f"The corner is {math.half} by {math.half}, so its area is {math.corner}."
        ),
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: (
            f"Add the corner area {math.corner} to both sides. The right side becomes "
            f"{math.rhs} plus {math.corner}, which is {math.completed}."
        ),
        CompletingSquareCheckpointId.FACTOR_SQUARE: (
            f"The completed left side is x plus {math.half}, squared, and equals {math.completed}."
        ),
        CompletingSquareCheckpointId.SOLVE_ROOTS: (
            f"Now x plus {math.half} equals plus or minus {math.magnitude}, so x is "
            f"{math.root_positive} or {math.root_negative}. The tiles show a "
            "nonnegative-length branch; the algebra recovers both roots."
        ),
    }[checkpoint_id]
    if narration != expected:
        _fail("checkpoint narration omits or changes an independently derived fact")


def _verify_timing(
    checkpoint_id: CompletingSquareCheckpointId,
    choreography: ChoreographyPlanV1,
) -> None:
    expected_duration, expected_hold, expected_easing = _TIMING[checkpoint_id]
    actual = (
        choreography.phase.duration_ms,
        choreography.phase.hold_after_ms,
        getattr(choreography.phase.easing, "value", None),
    )
    if actual != (expected_duration, expected_hold, expected_easing):
        _fail("checkpoint choreography does not match the independently authored timing")


def _verify_math(
    checkpoint_id: CompletingSquareCheckpointId,
    by_suffix: Mapping[str, SceneNode],
    narration: str,
    math: _ExpectedMath,
) -> None:
    if checkpoint_id in {
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
        CompletingSquareCheckpointId.REARRANGE_HALVES,
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.CORNER_DETAIL,
    }:
        halves = tuple(
            _parse_linear_term(
                _token(
                    by_suffix[suffix],
                    suffix=suffix,
                    latex=_linear_term(math.half),
                ).latex
            )
            for suffix in ("eq_half_a", "eq_half_b")
        )
        if halves[0] != halves[1] or sum(halves) != math.coefficient:
            _fail("linear term was not split equally")
    if checkpoint_id is CompletingSquareCheckpointId.SPLIT_LINEAR_TERM:
        half_calc = _token(
            by_suffix["half_calc"],
            suffix="half_calc",
            latex=rf"{math.coefficient}\div 2={math.half}",
        ).latex
        if half_calc != rf"{math.coefficient}\div 2={math.half}":
            _fail("half-coefficient calculation is inconsistent")
    if checkpoint_id in {
        CompletingSquareCheckpointId.CORNER_DETAIL,
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    }:
        dimensions = tuple(
            int(_token(by_suffix[suffix], suffix=suffix, latex=str(math.half)).latex)
            for suffix in ("corner_dim_h", "corner_dim_v")
        )
        if dimensions[0] * dimensions[1] != math.corner:
            _fail("corner dimensions do not establish the derived area")
        _token(
            by_suffix["corner_calc"],
            suffix="corner_calc",
            latex=rf"{math.half}\times {math.half}={math.half}^2={math.corner}",
        )
    if checkpoint_id is CompletingSquareCheckpointId.BALANCE_AND_COMPLETE:
        left_added = int(
            _token(
                by_suffix["eq_corner_value"],
                suffix="eq_corner_value",
                latex=str(math.corner),
            ).latex
        )
        right_added = int(
            _token(
                by_suffix["eq_rhs_corner"],
                suffix="eq_rhs_corner",
                latex=str(math.corner),
            ).latex
        )
        completed_token = _token(
            by_suffix["eq_completed_rhs"],
            suffix="eq_completed_rhs",
            latex=f"{math.completed}={math.magnitude}^2",
        ).latex
        completed, magnitude_squared = completed_token.split("=", maxsplit=1)
        magnitude = int(magnitude_squared.removesuffix("^2"))
        if (
            left_added != right_added
            or math.rhs + right_added != int(completed)
            or magnitude * magnitude != int(completed)
        ):
            _fail("completion must add equal corner areas and produce the derived square")
    if _at_or_after(checkpoint_id, "factor_square"):
        factor = _token(
            by_suffix["eq_factor"],
            suffix="eq_factor",
            latex=f"(x+{math.half})^2",
        ).latex
        shift = int(factor.removeprefix("(x+").removesuffix(")^2"))
        if (
            2 * shift != math.coefficient
            or shift * shift != math.corner
            or math.rhs + shift * shift != math.completed
        ):
            _fail("factorization does not expand to the bound equation")
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        magnitude = int(
            _token(
                by_suffix["root_pm"],
                suffix="root_pm",
                latex=rf"\pm {math.magnitude}",
            ).latex.removeprefix(r"\pm ")
        )
        roots = (
            int(
                _token(
                    by_suffix["root_positive"],
                    suffix="root_positive",
                    latex=str(math.root_positive),
                ).latex
            ),
            int(
                _token(
                    by_suffix["root_negative"],
                    suffix="root_negative",
                    latex=str(math.root_negative),
                ).latex
            ),
        )
        expected_roots = {magnitude - math.half, -magnitude - math.half}
        if set(roots) != expected_roots or any(
            root * root + math.coefficient * root != math.rhs for root in roots
        ):
            _fail("root tokens do not solve the bound equation")
        normalized = " ".join(narration.casefold().split())
        if (
            "nonnegative-length branch" not in normalized
            or "algebra recovers both roots" not in normalized
        ):
            _fail("solve narration must qualify the geometry domain honestly")


def _verify_snapshot(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    nodes: NodeMap,
    math: _ExpectedMath,
) -> None:
    by_suffix = _component_suffix_map(nodes, component_id)
    expected_suffixes = _expected_suffixes(checkpoint_id, math)
    if set(by_suffix) != expected_suffixes:
        missing = sorted(expected_suffixes.difference(by_suffix))
        extra = sorted(set(by_suffix).difference(expected_suffixes))
        _fail(f"{checkpoint_id.value} has wrong stable node set; missing={missing}, extra={extra}")

    expected_latex = _expected_latex(checkpoint_id, math)
    for suffix, latex in expected_latex.items():
        _token(by_suffix[suffix], suffix=suffix, latex=latex)
    _verify_visual_contract(checkpoint_id, by_suffix)
    for suffix in _GEOMETRY_SUFFIXES.intersection(by_suffix):
        _rectangle_box(by_suffix[suffix], suffix=suffix)
    if "corner" in by_suffix:
        _rectangle_box(by_suffix["corner"], suffix="corner")

    _require_token_row(nodes, component_id, _equation_terms(checkpoint_id, math))
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        _require_token_row(nodes, component_id, _root_equation(math))
        _require_token_row(nodes, component_id, _root_results(math))
    _verify_token_collisions(by_suffix)

    if not _at_or_after(checkpoint_id, "area_model"):
        return
    square, strip_a, strip_b, thickness = _verify_area_model(by_suffix, math)
    _verify_equation_geometry_clearance(checkpoint_id, math, by_suffix)
    if not _at_or_after(checkpoint_id, "rearrange_halves"):
        _require_no_interior_overlap(
            {"x2_square": square, "strip_a": strip_a, "strip_b": strip_b},
            label="area-model shape",
        )
        if (
            _attachment_side(square, strip_a) is not None
            or _attachment_side(square, strip_b) is not None
        ):
            _fail("pre-rearrangement strips must remain visibly detached from x2_square")
        return

    outer, gap = _verify_adjacent_arrangement(square, strip_a, strip_b, thickness)
    _verify_dimensions(by_suffix, square, gap, math)
    if not _at_or_after(checkpoint_id, "missing_corner"):
        return
    filled = _at_or_after(checkpoint_id, "balance_and_complete")
    revealed = checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL or filled
    _verify_corner(
        by_suffix,
        outer_without_corner=outer,
        gap=gap,
        square=square,
        strip_a=strip_a,
        strip_b=strip_b,
        thickness=thickness,
        filled=filled,
        revealed=revealed,
        math=math,
    )


def _verify_checkpoint_order(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base_nodes: NodeMap,
    math: _ExpectedMath,
) -> CompletingSquareCheckpointId | None:
    actual_suffixes = frozenset(_component_suffix_map(base_nodes, component_id))
    for predecessor in _PREDECESSORS[checkpoint_id]:
        if predecessor is None:
            if not actual_suffixes:
                return None
            continue
        if actual_suffixes == _expected_suffixes(predecessor, math):
            _verify_snapshot(component_id, predecessor, base_nodes, math)
            return predecessor
    _fail(f"base scene is not a legal immediate predecessor of {checkpoint_id.value}")


def _cue_targets(choreography: ChoreographyPlanV1, cue: str) -> tuple[str, ...]:
    for item in choreography.phase.cues:
        if item.cue == cue:
            return item.target_ids
    return ()


def _verify_morph(before: SceneNode, after: SceneNode, math: _ExpectedMath) -> None:
    if type(before) is not type(after):
        _fail(f"transform target {before.id!r} changes node kind")
    if isinstance(before, PathSceneNode) and isinstance(after, PathSceneNode):
        if before.closed != after.closed or len(before.points) != len(after.points):
            _fail(f"transform target {before.id!r} changes path topology")
        before_area = sum(
            before.points[index][0] * before.points[(index + 1) % len(before.points)][1]
            - before.points[(index + 1) % len(before.points)][0] * before.points[index][1]
            for index in range(len(before.points))
        )
        after_area = sum(
            after.points[index][0] * after.points[(index + 1) % len(after.points)][1]
            - after.points[(index + 1) % len(after.points)][0] * after.points[index][1]
            for index in range(len(after.points))
        )
        if before_area * after_area <= 0.0:
            _fail(f"transform target {before.id!r} changes path winding or degenerates")
        if _rectangle_corner_order(before) != _rectangle_corner_order(after):
            _fail(f"transform target {before.id!r} changes rectangle vertex correspondence")
    if isinstance(before, LatexTokenSceneNode) and isinstance(after, LatexTokenSceneNode):
        if before.anchor != after.anchor:
            _fail(f"transform target {before.id!r} changes token anchor")
        allowed_syntax_change = before.id.endswith("__corner_area") and (
            before.latex,
            after.latex,
        ) == ("?", str(math.corner))
        if before.latex != after.latex and not allowed_syntax_change:
            _fail(f"transform target {before.id!r} changes mathematical referent")


def _verify_cues(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base: NodeMap,
    result: NodeMap,
    choreography: ChoreographyPlanV1,
    math: _ExpectedMath,
) -> tuple[str, ...]:
    base_ids = set(base)
    result_ids = set(result)
    added = tuple(sorted(result_ids - base_ids))
    removed = tuple(sorted(base_ids - result_ids))
    transformed = tuple(
        sorted(node_id for node_id in base_ids & result_ids if base[node_id] != result[node_id])
    )
    expected = {"enter": added, "exit": removed, "transform": transformed}
    for cue, target_ids in expected.items():
        if _cue_targets(choreography, cue) != target_ids:
            _fail(f"{cue} cue targets must exactly match the scene diff")

    prefix = f"{component_id}__"
    for cue in choreography.phase.cues:
        if any(not target_id.startswith(prefix) for target_id in cue.target_ids):
            _fail(f"{cue.cue} cue targets a foreign component")
        valid_ids = base_ids if cue.cue == "exit" else result_ids
        if any(target_id not in valid_ids for target_id in cue.target_ids):
            _fail(f"{cue.cue} cue targets a node absent from its scene")

    for node_id in transformed:
        _verify_morph(base[node_id], result[node_id], math)

    focus = _cue_targets(choreography, "focus")
    expected_focus = tuple(
        sorted(_node_id(component_id, suffix) for suffix in _FOCUS_SUFFIXES[checkpoint_id])
    )
    if focus != expected_focus:
        _fail("focus cue does not name the checkpoint's mathematical subject")
    emphasis = _cue_targets(choreography, "emphasize")
    expected_emphasis = tuple(
        sorted(_node_id(component_id, suffix) for suffix in _EMPHASIS_SUFFIXES[checkpoint_id])
    )
    if emphasis != expected_emphasis:
        _fail("emphasize cue does not match the checkpoint's semantic emphasis")
    return tuple(sorted(set(focus) | set(emphasis)))


def _verify_viewport_pose(pose: ViewportPoseV1, *, label: str) -> None:
    box = (pose.x, pose.y, pose.x + pose.width, pose.y + pose.height)
    _require_box_on_board(box, label=label)
    if pose.width <= 2.0 * SAFE_VIEWPORT_PADDING or pose.height <= 2.0 * SAFE_VIEWPORT_PADDING:
        _fail(f"{label} is too small for safe padding")


def _verify_viewport_map(viewports: LayoutViewportMapV1, *, label: str) -> None:
    _verify_viewport_pose(viewports.cinematic, label=f"{label}.cinematic")
    _verify_viewport_pose(viewports.compact, label=f"{label}.compact")


def _viewport_subject_suffixes(
    checkpoint_id: CompletingSquareCheckpointId,
) -> frozenset[str]:
    """Name every authored fact a checkpoint viewport promises to show."""

    if checkpoint_id is CompletingSquareCheckpointId.PROBLEM:
        return _INITIAL_EQUATION_VIEWPORT_SUFFIXES
    if checkpoint_id is CompletingSquareCheckpointId.AREA_MODEL:
        return _INITIAL_EQUATION_VIEWPORT_SUFFIXES | _AREA_VIEWPORT_SUFFIXES
    if checkpoint_id is CompletingSquareCheckpointId.SPLIT_LINEAR_TERM:
        return _SPLIT_EQUATION_VIEWPORT_SUFFIXES | _AREA_VIEWPORT_SUFFIXES | {"half_calc"}
    if checkpoint_id is CompletingSquareCheckpointId.REARRANGE_HALVES:
        return (
            _SPLIT_EQUATION_VIEWPORT_SUFFIXES
            | _AREA_VIEWPORT_SUFFIXES
            | _DIMENSION_VIEWPORT_SUFFIXES
        )
    if checkpoint_id is CompletingSquareCheckpointId.MISSING_CORNER:
        return _CORNER_VIEWPORT_SUFFIXES | {
            "area_half_a",
            "corner_dim_h",
            "corner_dim_v",
        }
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        return _CORNER_VIEWPORT_SUFFIXES | {"corner_dim_h", "corner_dim_v", "corner_calc"}
    if checkpoint_id is CompletingSquareCheckpointId.BALANCE_AND_COMPLETE:
        return (
            _COMPLETED_EQUATION_VIEWPORT_SUFFIXES
            | _AREA_VIEWPORT_SUFFIXES
            | _DIMENSION_VIEWPORT_SUFFIXES
            | _CORNER_VIEWPORT_SUFFIXES
            | {"corner_calc"}
        )
    if checkpoint_id is CompletingSquareCheckpointId.FACTOR_SQUARE:
        return (
            _FACTORED_EQUATION_VIEWPORT_SUFFIXES
            | _AREA_VIEWPORT_SUFFIXES
            | _DIMENSION_VIEWPORT_SUFFIXES
            | _CORNER_VIEWPORT_SUFFIXES
        )
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        return (
            _FACTORED_EQUATION_VIEWPORT_SUFFIXES
            | _ROOT_VIEWPORT_SUFFIXES
            | _AREA_VIEWPORT_SUFFIXES
            | _DIMENSION_VIEWPORT_SUFFIXES
            | _CORNER_VIEWPORT_SUFFIXES
        )
    _fail(f"unsupported viewport subject set for {checkpoint_id.value}")


def _verify_viewport_content(
    viewports: LayoutViewportMapV1,
    nodes: NodeMap,
    target_ids: Iterable[str],
    *,
    label: str,
) -> None:
    canonical_targets = tuple(sorted(set(target_ids)))
    for target_id in canonical_targets:
        if target_id not in nodes:
            _fail(f"{label} subject {target_id!r} is absent from its scene")
    for layout, pose in (
        ("cinematic", viewports.cinematic),
        ("compact", viewports.compact),
    ):
        safe = (
            pose.x + SAFE_VIEWPORT_PADDING,
            pose.y + SAFE_VIEWPORT_PADDING,
            pose.x + pose.width - SAFE_VIEWPORT_PADDING,
            pose.y + pose.height - SAFE_VIEWPORT_PADDING,
        )
        for target_id in canonical_targets:
            target = _node_box(nodes[target_id])
            if (
                target[0] < safe[0] - _EPSILON
                or target[1] < safe[1] - _EPSILON
                or target[2] > safe[2] + _EPSILON
                or target[3] > safe[3] + _EPSILON
            ):
                _fail(f"{label} subject {target_id!r} is clipped by {layout} safe viewport padding")


def _verify_viewports(
    presentation: PresentationCheckpointV1,
    component_id: str,
    base: NodeMap,
    result: NodeMap,
    base_checkpoint_id: CompletingSquareCheckpointId | None,
    result_checkpoint_id: CompletingSquareCheckpointId,
    cue_targets: Sequence[str],
) -> None:
    _verify_viewport_map(presentation.base_viewports, label="baseViewports")
    _verify_viewport_map(presentation.result_viewports, label="resultViewports")
    if base_checkpoint_id is not None:
        _verify_viewport_content(
            presentation.base_viewports,
            base,
            (
                _node_id(component_id, suffix)
                for suffix in _viewport_subject_suffixes(base_checkpoint_id)
            ),
            label="base viewport",
        )
    _verify_viewport_content(
        presentation.result_viewports,
        result,
        (
            *cue_targets,
            *(
                _node_id(component_id, suffix)
                for suffix in _viewport_subject_suffixes(result_checkpoint_id)
            ),
        ),
        label="result viewport",
    )


def _obligations(
    checkpoint_id: CompletingSquareCheckpointId,
) -> tuple[CheckpointVerificationObligationV3, ...]:
    required = {
        CheckpointVerificationObligationV3.STABLE_ID,
        CheckpointVerificationObligationV3.UNIQUE_IDS,
        CheckpointVerificationObligationV3.BOARD_BOUNDS,
        CheckpointVerificationObligationV3.COMPONENT_OWNERSHIP,
        CheckpointVerificationObligationV3.PATCH_MATERIALIZATION,
        CheckpointVerificationObligationV3.COMPATIBLE_MORPH,
        CheckpointVerificationObligationV3.VIEWPORT_CONTAINMENT,
        CheckpointVerificationObligationV3.EQUATION_IDENTITY,
        CheckpointVerificationObligationV3.PROBLEM_IDENTITY,
        CheckpointVerificationObligationV3.CAPTION_FACTS,
        CheckpointVerificationObligationV3.AUTHORED_TIMING,
    }
    if _at_or_after(checkpoint_id, "area_model"):
        required.add(CheckpointVerificationObligationV3.AREA_MODEL)
    if _at_or_after(checkpoint_id, "split_linear_term"):
        required.add(CheckpointVerificationObligationV3.EQUAL_LINEAR_SPLIT)
    if _at_or_after(checkpoint_id, "rearrange_halves"):
        required.add(CheckpointVerificationObligationV3.ADJACENT_REARRANGEMENT)
    if _at_or_after(checkpoint_id, "missing_corner"):
        required.add(CheckpointVerificationObligationV3.MISSING_CORNER)
    if _at_or_after(checkpoint_id, "balance_and_complete"):
        required.add(CheckpointVerificationObligationV3.BALANCED_COMPLETION)
    if _at_or_after(checkpoint_id, "factor_square"):
        required.add(CheckpointVerificationObligationV3.FACTORIZATION)
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        required.update(
            {
                CheckpointVerificationObligationV3.ROOTS,
                CheckpointVerificationObligationV3.GEOMETRY_DOMAIN,
            }
        )
    return tuple(code for code in CheckpointVerificationObligationV3 if code in required)


def verify_parametric_completing_square_frontier(
    component: ParametricCompletingSquareStateV1,
    scene: SceneState,
) -> None:
    """Verify that a V3 semantic frontier exactly realizes its bound problem."""

    if not isinstance(component, ParametricCompletingSquareStateV1):
        _fail("component must be a ParametricCompletingSquareStateV1 contract")
    if not isinstance(scene, SceneState):
        _fail("scene must be a SceneState contract")
    math = _derive_math(component.problem_spec)
    _node_map(scene.nodes, label="base")
    owned = _owned_nodes(scene.nodes, component.id)
    _verify_board_bounds(owned.values(), label="base")
    if component.last_main_checkpoint is None:
        if owned:
            _fail("an unrevealed parametric frontier must own no scene nodes")
        return

    checkpoint_id = CompletingSquareCheckpointId(component.last_main_checkpoint.value)
    if (
        component.last_main_checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER
        and component.corner_clarified
    ):
        checkpoint_id = CompletingSquareCheckpointId.CORNER_DETAIL
    _verify_snapshot(component.id, checkpoint_id, owned, math)


def verify_parametric_completing_square_checkpoint(
    component_id: str,
    problem_spec: CompletingSquareProblemSpecV1,
    checkpoint_id: CompletingSquareCheckpointId,
    base_scene: SceneState,
    result_scene: SceneState,
    patch: ScenePatchDraft,
    presentation: PresentationCheckpointV1,
    choreography: ChoreographyPlanV1,
) -> CheckpointVerificationReceiptV3:
    """Verify one ordered, problem-bound V3 checkpoint transition."""

    try:
        _COMPONENT_ID_ADAPTER.validate_python(component_id)
    except (TypeError, ValidationError, ValueError) as exc:
        raise ParametricCompletingSquareVerificationError(
            "component_id violates its closed contract"
        ) from exc
    math = _derive_math(problem_spec)
    if not isinstance(checkpoint_id, CompletingSquareCheckpointId):
        _fail("checkpoint_id must be a CompletingSquareCheckpointId")
    if not isinstance(base_scene, SceneState) or not isinstance(result_scene, SceneState):
        _fail("base_scene and result_scene must be SceneState contracts")
    if not isinstance(patch, ScenePatchDraft):
        _fail("patch must be a ScenePatchDraft contract")
    if not isinstance(presentation, PresentationCheckpointV1):
        _fail("presentation must be a PresentationCheckpointV1 contract")
    if not isinstance(choreography, ChoreographyPlanV1):
        _fail("choreography must be a ChoreographyPlanV1 contract")

    expected_patch_id = f"{component_id}__cp_{checkpoint_id.value}"
    if patch.patch_id != expected_patch_id:
        _fail("patchId does not match the component and checkpoint identity")
    if presentation.checkpoint_id != checkpoint_id.value:
        _fail("presentation checkpointId does not match checkpoint_id")
    if patch.narration != presentation.checkpoint_narration:
        _fail("patch narration must equal checkpointNarration")
    _verify_caption(checkpoint_id, presentation.checkpoint_narration, math)
    _verify_timing(checkpoint_id, choreography)
    if result_scene.revision != base_scene.revision + 1:
        _fail("result scene revision must be exactly one greater than base revision")

    operation_targets = tuple(operation.target_id for operation in patch.operations)
    if len(operation_targets) != len(set(operation_targets)):
        _fail("patch operation targets must be unique")
    if operation_targets != tuple(sorted(operation_targets)):
        _fail("patch operation targets must use canonical lexical order")
    prefix = f"{component_id}__"
    if any(not target.startswith(prefix) for target in operation_targets):
        _fail("patch may not touch a foreign component namespace")

    materialized = _materialize_patch(base_scene, patch)
    if materialized != result_scene.nodes:
        _fail("patch does not exactly materialize the declared result scene")

    base = _node_map(base_scene.nodes, label="base")
    result = _node_map(result_scene.nodes, label="result")
    base_owned = _owned_nodes(base_scene.nodes, component_id)
    result_owned = _owned_nodes(result_scene.nodes, component_id)
    _verify_board_bounds(base.values(), label="base")
    _verify_board_bounds(result.values(), label="result")
    base_checkpoint_id = _verify_checkpoint_order(
        component_id,
        checkpoint_id,
        base_owned,
        math,
    )
    visible_targets = _verify_cues(
        component_id,
        checkpoint_id,
        base,
        result,
        choreography,
        math,
    )
    _verify_snapshot(component_id, checkpoint_id, result_owned, math)
    _verify_viewports(
        presentation,
        component_id,
        base,
        result,
        base_checkpoint_id,
        checkpoint_id,
        visible_targets,
    )
    result_by_suffix = _component_suffix_map(result_owned, component_id)
    _verify_math(
        checkpoint_id,
        result_by_suffix,
        presentation.checkpoint_narration,
        math,
    )

    return CheckpointVerificationReceiptV3(
        component_id=component_id,
        problem_spec_sha256=completing_square_problem_sha256(problem_spec),
        checkpoint_id=checkpoint_id,
        operation_targets=operation_targets,
        obligation_codes=_obligations(checkpoint_id),
    )


__all__ = [
    "SAFE_VIEWPORT_PADDING",
    "ParametricCompletingSquareVerificationError",
    "verify_parametric_completing_square_checkpoint",
    "verify_parametric_completing_square_frontier",
]

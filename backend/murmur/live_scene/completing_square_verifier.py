"""Independent verification for the completing-square choreography.

The verifier intentionally depends only on public contracts.  It reconstructs
the complete base-to-result transition, then derives the mathematical and
presentation obligations from the supplied low-level scenes.  In particular,
it does not consume compiler blueprints, layout constants, or compiler-owned
expected snapshots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import isclose, isfinite
from typing import TypeAlias

from pydantic import TypeAdapter, ValidationError

from murmur.live_scene.checkpoint_contracts import (
    CheckpointVerificationObligation,
    CheckpointVerificationReceiptV2,
)
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

Point: TypeAlias = tuple[float, float]
Box: TypeAlias = tuple[float, float, float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

SAFE_VIEWPORT_PADDING = 12.0

_EPSILON = 1e-6
_COMPONENT_ID_ADAPTER = TypeAdapter(ChoreographyComponentId)

_INITIAL_EQUATION = (
    ("eq_x2", "x^2"),
    ("eq_plus_a", "+"),
    ("eq_6x", "6x"),
    ("eq_equal_main", "="),
    ("eq_rhs7", "7"),
)
_SPLIT_EQUATION = (
    ("eq_x2", "x^2"),
    ("eq_plus_a", "+"),
    ("eq_3x_a", "3x"),
    ("eq_plus_b", "+"),
    ("eq_3x_b", "3x"),
    ("eq_equal_main", "="),
    ("eq_rhs7", "7"),
)
_COMPLETED_EQUATION = (
    ("eq_x2", "x^2"),
    ("eq_plus_a", "+"),
    ("eq_3x_a", "3x"),
    ("eq_plus_b", "+"),
    ("eq_3x_b", "3x"),
    ("eq_plus_corner", "+"),
    ("eq_corner9", "9"),
    ("eq_equal_main", "="),
    ("eq_rhs7", "7"),
    ("eq_plus_rhs", "+"),
    ("eq_rhs9", "9"),
    ("eq_equal_result", "="),
    ("eq_16", "16"),
)
_FACTORED_EQUATION = (
    ("eq_factor", "(x+3)^2"),
    ("eq_equal_result", "="),
    ("eq_16", "16"),
)
_ROOT_EQUATION = (
    ("root_lhs", "x+3"),
    ("root_equal", "="),
    ("root_pm4", r"\pm 4"),
)
_ROOT_RESULTS = (
    ("root_x_left", "x"),
    ("root_eq_left", "="),
    ("root_one", "1"),
    ("root_or", r"\text{or}"),
    ("root_x_right", "x"),
    ("root_eq_right", "="),
    ("root_neg7", "-7"),
)
_AREA_LABELS = (
    ("area_x2", "x^2"),
    ("area_3x_a", "3x"),
    ("area_3x_b", "3x"),
)
_CORNER_MISSING = (("corner_area", "?"),)
_CORNER_FILLED = (("corner_area", "9"),)
_CORNER_DETAIL = (
    ("corner_dim_h", "3"),
    ("corner_dim_v", "3"),
    ("corner_calc", r"3\times3=9"),
)

_GEOMETRY_SUFFIXES = frozenset({"x2_square", "strip_a", "strip_b"})
_FOCUS_SUFFIXES: Mapping[CompletingSquareCheckpointId, tuple[str, ...]] = {
    CompletingSquareCheckpointId.PROBLEM: ("eq_6x", "eq_x2"),
    CompletingSquareCheckpointId.AREA_MODEL: ("strip_a", "strip_b", "x2_square"),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: ("eq_3x_a", "eq_3x_b"),
    CompletingSquareCheckpointId.REARRANGE_HALVES: ("strip_a", "strip_b"),
    CompletingSquareCheckpointId.MISSING_CORNER: ("corner",),
    CompletingSquareCheckpointId.CORNER_DETAIL: ("corner", "corner_calc"),
    CompletingSquareCheckpointId.BALANCE_AND_COMPLETE: ("corner", "x2_square"),
    CompletingSquareCheckpointId.FACTOR_SQUARE: ("eq_factor",),
    CompletingSquareCheckpointId.SOLVE_ROOTS: ("root_neg7", "root_one"),
}
_EMPHASIS_SUFFIXES: Mapping[CompletingSquareCheckpointId, tuple[str, ...]] = {
    CompletingSquareCheckpointId.PROBLEM: (),
    CompletingSquareCheckpointId.AREA_MODEL: (),
    CompletingSquareCheckpointId.SPLIT_LINEAR_TERM: (),
    CompletingSquareCheckpointId.REARRANGE_HALVES: (),
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
}

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

_MAIN_ORDINAL = {
    CompletingSquareCheckpointId(checkpoint.value): index
    for index, checkpoint in enumerate(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER)
}


class CompletingSquareVerificationError(ValueError):
    """Raised when a checkpoint fails any independent verification obligation."""


def _fail(message: str) -> None:
    raise CompletingSquareVerificationError(message)


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


def _require_center_inside(inner: Box, outer: Box, *, label: str) -> None:
    x, y = _box_center(inner)
    if not (
        outer[0] - _EPSILON <= x <= outer[2] + _EPSILON
        and outer[1] - _EPSILON <= y <= outer[3] + _EPSILON
    ):
        _fail(f"{label} is not associated with its area-model shape")


def _token(node: SceneNode, *, suffix: str, latex: str) -> LatexTokenSceneNode:
    if not isinstance(node, LatexTokenSceneNode):
        _fail(f"{suffix} must be a measured LaTeX token")
    if node.latex != latex:
        _fail(f"{suffix} must contain exact LaTeX {latex!r}")
    return node


def _require_token_row(nodes: NodeMap, component_id: str, terms: Sequence[tuple[str, str]]) -> None:
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


def _equation_terms(checkpoint_id: CompletingSquareCheckpointId) -> tuple[tuple[str, str], ...]:
    if checkpoint_id in {
        CompletingSquareCheckpointId.PROBLEM,
        CompletingSquareCheckpointId.AREA_MODEL,
    }:
        return _INITIAL_EQUATION
    if checkpoint_id in {
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
        CompletingSquareCheckpointId.REARRANGE_HALVES,
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.CORNER_DETAIL,
    }:
        return _SPLIT_EQUATION
    return (
        _FACTORED_EQUATION
        if checkpoint_id
        in {
            CompletingSquareCheckpointId.FACTOR_SQUARE,
            CompletingSquareCheckpointId.SOLVE_ROOTS,
        }
        else _COMPLETED_EQUATION
    )


def _at_or_after(checkpoint_id: CompletingSquareCheckpointId, threshold: str) -> bool:
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        ordinal = _MAIN_ORDINAL[CompletingSquareCheckpointId.MISSING_CORNER]
    else:
        ordinal = _MAIN_ORDINAL[checkpoint_id]
    return ordinal >= _MAIN_ORDINAL[CompletingSquareCheckpointId(threshold)]


def _expected_latex(checkpoint_id: CompletingSquareCheckpointId) -> dict[str, str]:
    expected = dict(_equation_terms(checkpoint_id))
    if _at_or_after(checkpoint_id, "area_model"):
        expected.update(_AREA_LABELS)
    if _at_or_after(checkpoint_id, "missing_corner"):
        expected.update(
            _CORNER_FILLED
            if _at_or_after(checkpoint_id, "balance_and_complete")
            else _CORNER_MISSING
        )
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        expected.update(_CORNER_DETAIL)
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        expected.update(_ROOT_EQUATION)
        expected.update(_ROOT_RESULTS)
    return expected


def _expected_suffixes(checkpoint_id: CompletingSquareCheckpointId) -> frozenset[str]:
    suffixes = set(_expected_latex(checkpoint_id))
    if _at_or_after(checkpoint_id, "area_model"):
        suffixes.update(_GEOMETRY_SUFFIXES)
    if _at_or_after(checkpoint_id, "missing_corner"):
        suffixes.add("corner")
    return frozenset(suffixes)


def _component_suffix_map(nodes: NodeMap, component_id: str) -> dict[str, SceneNode]:
    return {_suffix(node_id, component_id): node for node_id, node in nodes.items()}


def _verify_token_collisions(by_suffix: Mapping[str, SceneNode]) -> None:
    boxes = {
        suffix: _node_box(node)
        for suffix, node in by_suffix.items()
        if isinstance(node, LatexTokenSceneNode)
    }
    _require_no_interior_overlap(boxes, label="LaTeX token")


def _verify_area_model(by_suffix: Mapping[str, SceneNode]) -> tuple[Box, Box, Box, float]:
    square = _rectangle_box(by_suffix["x2_square"], suffix="x2_square")
    strip_a = _rectangle_box(by_suffix["strip_a"], suffix="strip_a")
    strip_b = _rectangle_box(by_suffix["strip_b"], suffix="strip_b")
    side = _box_width(square)
    if not _close(side, _box_height(square)):
        _fail("x2_square must have four equal sides")

    dimensions_a = sorted((_box_width(strip_a), _box_height(strip_a)))
    dimensions_b = sorted((_box_width(strip_b), _box_height(strip_b)))
    if not all(_close(left, right) for left, right in zip(dimensions_a, dimensions_b, strict=True)):
        _fail("the two 3x strips must be congruent")
    if not _close(dimensions_a[1], side):
        _fail("each 3x strip must have x2_square side length")
    thickness = dimensions_a[0]
    if thickness <= _EPSILON or thickness >= side - _EPSILON:
        _fail("each 3x strip must have one positive shorter dimension")

    labels = {
        "area_x2": ("x^2", square),
        "area_3x_a": ("3x", strip_a),
        "area_3x_b": ("3x", strip_b),
    }
    for suffix, (latex, shape) in labels.items():
        label = _token(by_suffix[suffix], suffix=suffix, latex=latex)
        _require_center_inside(_node_box(label), shape, label=suffix)
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
) -> Box:
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
        _box_height(outer), side + thickness
    ):
        _fail("adjacent strips must leave one thickness-by-thickness corner gap")
    gap_area = _box_area(outer) - sum(_box_area(box) for box in boxes.values())
    if not _close(gap_area, thickness * thickness):
        _fail("rearranged strips leave the wrong missing-corner area")
    return outer


def _verify_corner(
    by_suffix: Mapping[str, SceneNode],
    *,
    outer_without_corner: Box,
    square: Box,
    strip_a: Box,
    strip_b: Box,
    thickness: float,
    filled: bool,
) -> None:
    corner_node = by_suffix["corner"]
    corner = _rectangle_box(corner_node, suffix="corner")
    if not _close(_box_width(corner), _box_height(corner)):
        _fail("missing corner must be square")
    if not _close(_box_width(corner), thickness) or not _close(_box_height(corner), thickness):
        _fail("missing corner side lengths must equal the 3x-strip thickness")
    all_boxes = {
        "x2_square": square,
        "strip_a": strip_a,
        "strip_b": strip_b,
        "corner": corner,
    }
    _require_no_interior_overlap(all_boxes, label="completed-square shape")
    completed_outer = _union_box(tuple(all_boxes.values()))
    if completed_outer != outer_without_corner:
        _fail("corner must occupy the rearrangement's existing outer bounds")
    if not _close(sum(_box_area(box) for box in all_boxes.values()), _box_area(completed_outer)):
        _fail("corner leaves a gap or overlaps the completed square")

    if not isinstance(corner_node, PathSceneNode):
        _fail("corner must be a path")
    empty_fill = corner_node.style.fill in {"none", "transparent"}
    if filled == empty_fill:
        _fail("corner fill does not match the checkpoint's completed state")
    _token(
        by_suffix["corner_area"],
        suffix="corner_area",
        latex="9" if filled else "?",
    )
    _require_center_inside(
        _node_box(by_suffix["corner_area"]),
        corner,
        label="corner_area",
    )


def _verify_snapshot(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    nodes: NodeMap,
) -> None:
    by_suffix = _component_suffix_map(nodes, component_id)
    expected_suffixes = _expected_suffixes(checkpoint_id)
    if set(by_suffix) != expected_suffixes:
        missing = sorted(expected_suffixes.difference(by_suffix))
        extra = sorted(set(by_suffix).difference(expected_suffixes))
        _fail(f"{checkpoint_id.value} has wrong stable node set; missing={missing}, extra={extra}")

    expected_latex = _expected_latex(checkpoint_id)
    for suffix, latex in expected_latex.items():
        _token(by_suffix[suffix], suffix=suffix, latex=latex)
    for suffix in _GEOMETRY_SUFFIXES.intersection(by_suffix):
        _rectangle_box(by_suffix[suffix], suffix=suffix)
    if "corner" in by_suffix:
        _rectangle_box(by_suffix["corner"], suffix="corner")

    _require_token_row(
        nodes,
        component_id,
        _equation_terms(checkpoint_id),
    )
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        _require_token_row(nodes, component_id, _ROOT_EQUATION)
        _require_token_row(nodes, component_id, _ROOT_RESULTS)
    _verify_token_collisions(by_suffix)

    if not _at_or_after(checkpoint_id, "area_model"):
        return
    square, strip_a, strip_b, thickness = _verify_area_model(by_suffix)
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
    outer = _verify_adjacent_arrangement(square, strip_a, strip_b, thickness)
    if not _at_or_after(checkpoint_id, "missing_corner"):
        return
    filled = _at_or_after(checkpoint_id, "balance_and_complete")
    _verify_corner(
        by_suffix,
        outer_without_corner=outer,
        square=square,
        strip_a=strip_a,
        strip_b=strip_b,
        thickness=thickness,
        filled=filled,
    )
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        for suffix, latex in _CORNER_DETAIL:
            _token(by_suffix[suffix], suffix=suffix, latex=latex)


def _verify_checkpoint_order(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base_nodes: NodeMap,
) -> None:
    actual_suffixes = frozenset(_component_suffix_map(base_nodes, component_id))
    for predecessor in _PREDECESSORS[checkpoint_id]:
        if predecessor is None:
            if not actual_suffixes:
                return
            continue
        if actual_suffixes == _expected_suffixes(predecessor):
            _verify_snapshot(component_id, predecessor, base_nodes)
            return
    _fail(f"base scene is not a legal immediate predecessor of {checkpoint_id.value}")


def _cue_targets(choreography: ChoreographyPlanV1, cue: str) -> tuple[str, ...]:
    for item in choreography.phase.cues:
        if item.cue == cue:
            return item.target_ids
    return ()


def _verify_morph(before: SceneNode, after: SceneNode) -> None:
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
        ) == ("?", "9")
        if before.latex != after.latex and not allowed_syntax_change:
            _fail(f"transform target {before.id!r} changes mathematical referent")


def _verify_cues(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base: NodeMap,
    result: NodeMap,
    choreography: ChoreographyPlanV1,
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
        _verify_morph(base[node_id], result[node_id])

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


def _verify_viewports(
    presentation: PresentationCheckpointV1,
    result: NodeMap,
    visible_targets: Sequence[str],
) -> None:
    _verify_viewport_map(presentation.base_viewports, label="baseViewports")
    _verify_viewport_map(presentation.result_viewports, label="resultViewports")
    for layout, pose in (
        ("cinematic", presentation.result_viewports.cinematic),
        ("compact", presentation.result_viewports.compact),
    ):
        safe = (
            pose.x + SAFE_VIEWPORT_PADDING,
            pose.y + SAFE_VIEWPORT_PADDING,
            pose.x + pose.width - SAFE_VIEWPORT_PADDING,
            pose.y + pose.height - SAFE_VIEWPORT_PADDING,
        )
        for target_id in visible_targets:
            target = _node_box(result[target_id])
            if (
                target[0] < safe[0] - _EPSILON
                or target[1] < safe[1] - _EPSILON
                or target[2] > safe[2] + _EPSILON
                or target[3] > safe[3] + _EPSILON
            ):
                _fail(f"cue target {target_id!r} is clipped by {layout} safe viewport padding")


def _verify_math(
    checkpoint_id: CompletingSquareCheckpointId,
    result: NodeMap,
    component_id: str,
    narration: str,
) -> None:
    by_suffix = _component_suffix_map(result, component_id)
    if checkpoint_id in {
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
        CompletingSquareCheckpointId.REARRANGE_HALVES,
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.CORNER_DETAIL,
    }:
        halves = tuple(
            int(_token(by_suffix[suffix], suffix=suffix, latex="3x").latex.removesuffix("x"))
            for suffix in ("eq_3x_a", "eq_3x_b")
        )
        if halves[0] != halves[1] or sum(halves) != 6:
            _fail("linear term was not split equally")
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        dimensions = tuple(
            int(_token(by_suffix[suffix], suffix=suffix, latex="3").latex)
            for suffix in ("corner_dim_h", "corner_dim_v")
        )
        if dimensions[0] * dimensions[1] != 9:
            _fail("corner detail does not establish area nine")
    if _at_or_after(checkpoint_id, "balance_and_complete"):
        if checkpoint_id is CompletingSquareCheckpointId.BALANCE_AND_COMPLETE:
            left_added = int(_token(by_suffix["eq_corner9"], suffix="eq_corner9", latex="9").latex)
            right_added = int(_token(by_suffix["eq_rhs9"], suffix="eq_rhs9", latex="9").latex)
            result_value = int(_token(by_suffix["eq_16"], suffix="eq_16", latex="16").latex)
            if left_added != right_added or 7 + right_added != result_value:
                _fail("completion must add equal nines and produce sixteen")
    if _at_or_after(checkpoint_id, "factor_square"):
        factor = _token(
            by_suffix["eq_factor"],
            suffix="eq_factor",
            latex="(x+3)^2",
        ).latex
        shift = int(factor.removeprefix("(x+").removesuffix(")^2"))
        result_value = int(_token(by_suffix["eq_16"], suffix="eq_16", latex="16").latex)
        if 2 * shift != 6 or shift**2 != 9 or 7 + shift**2 != result_value:
            _fail("factorization does not expand to x squared plus six x plus nine")
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        magnitude = int(
            _token(by_suffix["root_pm4"], suffix="root_pm4", latex=r"\pm 4").latex.removeprefix(
                r"\pm "
            )
        )
        shift = 3
        roots = (
            int(_token(by_suffix["root_one"], suffix="root_one", latex="1").latex),
            int(_token(by_suffix["root_neg7"], suffix="root_neg7", latex="-7").latex),
        )
        if set(roots) != {-shift + magnitude, -shift - magnitude} or any(
            root * root + 6 * root != 7 for root in roots
        ):
            _fail("root tokens do not solve x squared plus six x equals seven")
        normalized = " ".join(narration.casefold().split())
        if (
            "nonnegative-length branch" not in normalized
            or "algebra recovers both roots" not in normalized
        ):
            _fail("solve narration must qualify the geometry domain honestly")


def _obligations(
    checkpoint_id: CompletingSquareCheckpointId,
) -> tuple[CheckpointVerificationObligation, ...]:
    required = {
        CheckpointVerificationObligation.STABLE_ID,
        CheckpointVerificationObligation.UNIQUE_IDS,
        CheckpointVerificationObligation.BOARD_BOUNDS,
        CheckpointVerificationObligation.COMPONENT_OWNERSHIP,
        CheckpointVerificationObligation.PATCH_MATERIALIZATION,
        CheckpointVerificationObligation.COMPATIBLE_MORPH,
        CheckpointVerificationObligation.VIEWPORT_CONTAINMENT,
        CheckpointVerificationObligation.EQUATION_IDENTITY,
    }
    if _at_or_after(checkpoint_id, "area_model"):
        required.add(CheckpointVerificationObligation.AREA_MODEL)
    if _at_or_after(checkpoint_id, "split_linear_term"):
        required.add(CheckpointVerificationObligation.EQUAL_LINEAR_SPLIT)
    if _at_or_after(checkpoint_id, "rearrange_halves"):
        required.add(CheckpointVerificationObligation.ADJACENT_REARRANGEMENT)
    if _at_or_after(checkpoint_id, "missing_corner"):
        required.add(CheckpointVerificationObligation.MISSING_CORNER)
    if _at_or_after(checkpoint_id, "balance_and_complete"):
        required.add(CheckpointVerificationObligation.BALANCED_COMPLETION)
    if _at_or_after(checkpoint_id, "factor_square"):
        required.add(CheckpointVerificationObligation.FACTORIZATION)
    if checkpoint_id is CompletingSquareCheckpointId.SOLVE_ROOTS:
        required.update(
            {
                CheckpointVerificationObligation.ROOTS,
                CheckpointVerificationObligation.GEOMETRY_DOMAIN,
            }
        )
    return tuple(
        obligation for obligation in CheckpointVerificationObligation if obligation in required
    )


def verify_completing_square_checkpoint(
    component_id: str,
    checkpoint_id: CompletingSquareCheckpointId,
    base_scene: SceneState,
    result_scene: SceneState,
    patch: ScenePatchDraft,
    presentation: PresentationCheckpointV1,
    choreography: ChoreographyPlanV1,
) -> CheckpointVerificationReceiptV2:
    """Verify one exact, ordered completing-square checkpoint transition."""

    try:
        _COMPONENT_ID_ADAPTER.validate_python(component_id)
    except (TypeError, ValidationError, ValueError) as exc:
        raise CompletingSquareVerificationError(
            "component_id violates its closed contract"
        ) from exc
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
    _verify_checkpoint_order(component_id, checkpoint_id, base_owned)
    visible_targets = _verify_cues(
        component_id,
        checkpoint_id,
        base,
        result,
        choreography,
    )
    _verify_snapshot(component_id, checkpoint_id, result_owned)
    _verify_viewports(presentation, result, visible_targets)
    _verify_math(
        checkpoint_id,
        result_owned,
        component_id,
        presentation.checkpoint_narration,
    )

    return CheckpointVerificationReceiptV2(
        component_id=component_id,
        checkpoint_id=checkpoint_id,
        operation_targets=operation_targets,
        obligation_codes=_obligations(checkpoint_id),
    )


__all__ = [
    "SAFE_VIEWPORT_PADDING",
    "CompletingSquareVerificationError",
    "verify_completing_square_checkpoint",
]

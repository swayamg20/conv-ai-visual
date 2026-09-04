"""Independent verification of serialized semantic-component realizations.

This module deliberately has no dependency on the semantic compiler.  Its
input is the serialized low-level node contract, and it derives receipts only
after independently parsing and checking the complete supplied role prefix.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import hypot, isclose, isfinite
from typing import TypeAlias

from pydantic import ValidationError

from murmur.live_scene.contracts import (
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    SCENE_NODE_ADAPTER,
    LatexSceneNode,
    PathSceneNode,
    SceneNode,
    TextSceneNode,
)
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    PythagoreanRole,
    VerificationObligation,
    VerificationReceipt,
)

SerializedSceneNode: TypeAlias = Mapping[str, object]
Point: TypeAlias = tuple[float, float]
Box: TypeAlias = tuple[float, float, float, float]

_VERIFICATION_EPSILON = 1e-6
_RENDERER_LATEX_WIDTH = 500.0
_RENDERER_LATEX_HEIGHT = 120.0
_REQUIRED_IDENTITY = "a^2+b^2=c^2"

_EXPECTED_ROLE_SUFFIX: dict[PythagoreanRole, str] = {
    PythagoreanRole.TRIANGLE: "triangle",
    PythagoreanRole.SQUARE_A: "square_a",
    PythagoreanRole.LABEL_A2: "label_a2",
    PythagoreanRole.SQUARE_B: "square_b",
    PythagoreanRole.LABEL_B2: "label_b2",
    PythagoreanRole.SQUARE_C: "square_c",
    PythagoreanRole.LABEL_C2: "label_c2",
    PythagoreanRole.IDENTITY: "identity",
}

_EXPECTED_LABEL_TEXT: dict[PythagoreanRole, str] = {
    PythagoreanRole.LABEL_A2: "a²",
    PythagoreanRole.LABEL_B2: "b²",
    PythagoreanRole.LABEL_C2: "c²",
}


class SemanticVerificationError(ValueError):
    """Raised when serialized nodes fail a realization obligation."""


def _fail(message: str) -> None:
    raise SemanticVerificationError(message)


def _expected_node_id(component_id: str, role: PythagoreanRole) -> str:
    return f"{component_id}__{_EXPECTED_ROLE_SUFFIX[role]}"


def _parse_serialized_nodes(
    serialized_nodes: Sequence[SerializedSceneNode],
) -> tuple[SceneNode, ...]:
    parsed: list[SceneNode] = []
    for value in serialized_nodes:
        if not isinstance(value, Mapping):
            _fail("verifier accepts only serialized low-level node mappings")
        try:
            parsed.append(SCENE_NODE_ADAPTER.validate_python(value))
        except (TypeError, ValidationError, ValueError) as exc:
            raise SemanticVerificationError(
                "serialized node violates the low-level contract"
            ) from exc
    return tuple(parsed)


def _point_close(left: Point, right: Point) -> bool:
    return isclose(left[0], right[0], abs_tol=_VERIFICATION_EPSILON) and isclose(
        left[1], right[1], abs_tol=_VERIFICATION_EPSILON
    )


def _vector(start: Point, end: Point) -> Point:
    return (end[0] - start[0], end[1] - start[1])


def _dot(left: Point, right: Point) -> float:
    return left[0] * right[0] + left[1] * right[1]


def _cross(left: Point, right: Point) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _length(vector: Point) -> float:
    return hypot(vector[0], vector[1])


def _require_finite_point(point: Point, *, role: PythagoreanRole) -> None:
    if len(point) != 2 or not all(isfinite(value) for value in point):
        _fail(f"{role.value} contains a non-finite point")


def _require_point_on_board(point: Point, *, role: PythagoreanRole) -> None:
    _require_finite_point(point, role=role)
    if not (0.0 <= point[0] <= LIVE_SCENE_BOARD_WIDTH):
        _fail(f"{role.value} exceeds board width")
    if not (0.0 <= point[1] <= LIVE_SCENE_BOARD_HEIGHT):
        _fail(f"{role.value} exceeds board height")


def _require_path_on_board(node: PathSceneNode, *, role: PythagoreanRole) -> None:
    for point in node.points:
        _require_point_on_board(point, role=role)


def _require_box_on_board(box: Box, *, role: PythagoreanRole) -> None:
    if not all(isfinite(value) for value in box):
        _fail(f"{role.value} contains non-finite bounds")
    left, top, right, bottom = box
    if left < 0.0 or top < 0.0 or right > LIVE_SCENE_BOARD_WIDTH:
        _fail(f"{role.value} exceeds board width")
    if bottom > LIVE_SCENE_BOARD_HEIGHT or left > right or top > bottom:
        _fail(f"{role.value} exceeds board height")


def _verify_triangle(node: SceneNode) -> tuple[Point, Point, Point]:
    role = PythagoreanRole.TRIANGLE
    if not isinstance(node, PathSceneNode) or not node.closed or len(node.points) != 3:
        _fail("triangle must be one closed three-point path")
    _require_path_on_board(node, role=role)

    right, side_a_end, side_b_end = node.points
    side_a = _vector(right, side_a_end)
    side_b = _vector(right, side_b_end)
    length_a = _length(side_a)
    length_b = _length(side_b)
    hypotenuse = _length(_vector(side_a_end, side_b_end))
    if min(length_a, length_b, hypotenuse) <= _VERIFICATION_EPSILON:
        _fail("triangle sides must have positive length")
    if not isclose(_dot(side_a, side_b), 0.0, abs_tol=_VERIFICATION_EPSILON):
        _fail("triangle does not contain the required right angle")
    if not (
        isclose(length_b / length_a, 4.0 / 3.0, abs_tol=_VERIFICATION_EPSILON)
        and isclose(hypotenuse / length_a, 5.0 / 3.0, abs_tol=_VERIFICATION_EPSILON)
    ):
        _fail("triangle does not satisfy the required 3:4:5 ratio")
    return right, side_a_end, side_b_end


def _verify_square(
    node: SceneNode,
    *,
    role: PythagoreanRole,
    attached_edge: tuple[Point, Point],
    triangle_opposite: Point,
) -> tuple[Point, Point, Point, Point]:
    if not isinstance(node, PathSceneNode) or not node.closed or len(node.points) != 4:
        _fail(f"{role.value} must be one closed four-point path")
    _require_path_on_board(node, role=role)

    points = node.points
    if not _point_close(points[0], attached_edge[0]) or not _point_close(
        points[1], attached_edge[1]
    ):
        _fail(f"{role.value} is not attached to its triangle side")

    edges = tuple(
        _vector(points[index], points[(index + 1) % len(points)]) for index in range(len(points))
    )
    attached_length = _length(_vector(*attached_edge))
    if any(
        not isclose(_length(edge), attached_length, abs_tol=_VERIFICATION_EPSILON) for edge in edges
    ):
        _fail(f"{role.value} edge length does not match its triangle side")
    if any(
        not isclose(
            _dot(edges[index], edges[(index + 1) % len(edges)]),
            0.0,
            abs_tol=_VERIFICATION_EPSILON,
        )
        for index in range(len(edges))
    ):
        _fail(f"{role.value} does not contain four right angles")

    side = _vector(*attached_edge)
    triangle_side = _vector(attached_edge[0], triangle_opposite)
    square_side = _vector(attached_edge[0], points[3])
    if _cross(side, triangle_side) * _cross(side, square_side) >= 0.0:
        _fail(f"{role.value} must lie outside the triangle")
    return points


def _label_box(node: TextSceneNode) -> Box:
    font_size = node.style.font_size
    width = max(48.0, len(node.text) * font_size * 0.75)
    height = max(36.0, font_size * 1.25)
    if node.style.anchor == "middle":
        left = node.x - width / 2.0
    elif node.style.anchor == "end":
        left = node.x - width
    else:
        left = node.x
    return (left, node.y - height / 2.0, left + width, node.y + height / 2.0)


def _point_in_convex_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    signs: set[int] = set()
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        cross = _cross(_vector(start, end), _vector(start, point))
        if abs(cross) > _VERIFICATION_EPSILON:
            signs.add(1 if cross > 0.0 else -1)
    return len(signs) <= 1


def _box_inside_polygon(box: Box, polygon: Sequence[Point]) -> bool:
    left, top, right, bottom = box
    return all(
        _point_in_convex_polygon(point, polygon)
        for point in ((left, top), (right, top), (right, bottom), (left, bottom))
    )


def _boxes_intersect(left: Box, right: Box) -> bool:
    return not (
        left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1]
    )


def _verify_label(
    node: SceneNode,
    *,
    role: PythagoreanRole,
    square: Sequence[Point],
) -> Box:
    if not isinstance(node, TextSceneNode):
        _fail(f"{role.value} must be a text node")
    if node.text != _EXPECTED_LABEL_TEXT[role]:
        _fail(f"{role.value} contains the wrong required label")
    box = _label_box(node)
    _require_box_on_board(box, role=role)
    if not _box_inside_polygon(box, square):
        _fail(f"{role.value} is not conservatively contained by its square")
    return box


def _receipt(
    component_id: str,
    role: PythagoreanRole,
    obligations: tuple[VerificationObligation, ...],
) -> VerificationReceipt:
    return VerificationReceipt(
        component_id=component_id,
        role=role,
        node_id=_expected_node_id(component_id, role),
        obligation_codes=obligations,
    )


def verify_pythagorean_realization(
    component_id: str,
    serialized_nodes: Sequence[SerializedSceneNode],
) -> tuple[VerificationReceipt, ...]:
    """Fail closed unless serialized nodes form one exact verified role prefix."""

    if not serialized_nodes or len(serialized_nodes) > len(PYTHAGOREAN_ROLE_ORDER):
        _fail("realization must contain a non-empty Pythagorean role prefix")
    roles = PYTHAGOREAN_ROLE_ORDER[: len(serialized_nodes)]
    nodes = _parse_serialized_nodes(serialized_nodes)

    expected_ids = tuple(_expected_node_id(component_id, role) for role in roles)
    actual_ids = tuple(node.id for node in nodes)
    if len(actual_ids) != len(set(actual_ids)):
        _fail("realization node ids must be unique")
    if actual_ids != expected_ids:
        _fail("realization node ids must be the exact stable role prefix")

    right, side_a_end, side_b_end = _verify_triangle(nodes[0])
    receipt_by_role: dict[PythagoreanRole, VerificationReceipt] = {
        PythagoreanRole.TRIANGLE: _receipt(
            component_id,
            PythagoreanRole.TRIANGLE,
            (
                VerificationObligation.STABLE_ID,
                VerificationObligation.UNIQUE_IDS,
                VerificationObligation.BOARD_BOUNDS,
                VerificationObligation.RIGHT_ANGLE,
                VerificationObligation.HYPOTENUSE_RATIO,
            ),
        )
    }

    square_by_role: dict[PythagoreanRole, tuple[Point, Point, Point, Point]] = {}
    square_specs = {
        PythagoreanRole.SQUARE_A: ((right, side_a_end), side_b_end),
        PythagoreanRole.SQUARE_B: ((right, side_b_end), side_a_end),
        PythagoreanRole.SQUARE_C: ((side_a_end, side_b_end), right),
    }
    label_square = {
        PythagoreanRole.LABEL_A2: PythagoreanRole.SQUARE_A,
        PythagoreanRole.LABEL_B2: PythagoreanRole.SQUARE_B,
        PythagoreanRole.LABEL_C2: PythagoreanRole.SQUARE_C,
    }
    label_boxes: list[tuple[PythagoreanRole, Box]] = []

    for index, role in enumerate(roles[1:], start=1):
        node = nodes[index]
        if role in square_specs:
            attached_edge, opposite = square_specs[role]
            square_by_role[role] = _verify_square(
                node,
                role=role,
                attached_edge=attached_edge,
                triangle_opposite=opposite,
            )
            receipt_by_role[role] = _receipt(
                component_id,
                role,
                (
                    VerificationObligation.STABLE_ID,
                    VerificationObligation.UNIQUE_IDS,
                    VerificationObligation.BOARD_BOUNDS,
                    VerificationObligation.ATTACHED_SQUARE,
                    VerificationObligation.SQUARE_EDGE_LENGTH,
                ),
            )
            continue

        if role in label_square:
            square_role = label_square[role]
            square = square_by_role.get(square_role)
            if square is None:
                _fail(f"{role.value} is missing its prerequisite square")
            label_boxes.append((role, _verify_label(node, role=role, square=square)))
            continue

        if role is PythagoreanRole.IDENTITY:
            if not isinstance(node, LatexSceneNode) or node.latex != _REQUIRED_IDENTITY:
                _fail("identity must be the exact required equation")
            viewport = (
                node.x,
                node.y,
                node.x + _RENDERER_LATEX_WIDTH,
                node.y + _RENDERER_LATEX_HEIGHT,
            )
            _require_box_on_board(viewport, role=role)
            receipt_by_role[role] = _receipt(
                component_id,
                role,
                (
                    VerificationObligation.STABLE_ID,
                    VerificationObligation.UNIQUE_IDS,
                    VerificationObligation.BOARD_BOUNDS,
                    VerificationObligation.COMPILER_IDENTITY,
                ),
            )
            continue

        _fail(f"unsupported Pythagorean role: {role.value}")

    for index, (role, box) in enumerate(label_boxes):
        if any(_boxes_intersect(box, other) for _, other in label_boxes[index + 1 :]):
            _fail("conservative label boxes intersect")
        receipt_by_role[role] = _receipt(
            component_id,
            role,
            (
                VerificationObligation.STABLE_ID,
                VerificationObligation.UNIQUE_IDS,
                VerificationObligation.BOARD_BOUNDS,
                VerificationObligation.LABEL_CONTAINMENT,
                VerificationObligation.LABEL_SEPARATION,
            ),
        )

    if set(receipt_by_role) != set(roles):
        _fail("verification receipts do not match the realization prefix")
    return tuple(receipt_by_role[role] for role in roles)

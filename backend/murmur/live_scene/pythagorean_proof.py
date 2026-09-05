"""Independent geometric checks for the deterministic Pythagorean proof suffix.

The helpers in this module inspect only low-level scene nodes and geometry supplied
by the semantic verifier.  They intentionally do not import the compiler.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import hypot, isclose, isfinite
from typing import TypeAlias

from murmur.live_scene.contracts import (
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    LatexSceneNode,
    PathSceneNode,
    SceneNode,
    TextSceneNode,
)

Point: TypeAlias = tuple[float, float]

PROJECTION_IDENTITY_TEXT = "AH = a²/c  ·  HB = b²/c"

_EPSILON = 1e-6
_LATEX_WIDTH = 500.0


class PythagoreanProofError(ValueError):
    """Raised when a proof-specific node fails an independent obligation."""


def _fail(message: str) -> None:
    raise PythagoreanProofError(message)


def _point_close(left: Point, right: Point) -> bool:
    return isclose(left[0], right[0], abs_tol=_EPSILON) and isclose(
        left[1], right[1], abs_tol=_EPSILON
    )


def _vector(start: Point, end: Point) -> Point:
    return (end[0] - start[0], end[1] - start[1])


def _dot(left: Point, right: Point) -> float:
    return left[0] * right[0] + left[1] * right[1]


def _cross(left: Point, right: Point) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _length(vector: Point) -> float:
    return hypot(vector[0], vector[1])


def _require_point_on_board(point: Point, *, name: str) -> None:
    if len(point) != 2 or not all(isfinite(value) for value in point):
        _fail(f"{name} contains a non-finite point")
    if not 0.0 <= point[0] <= LIVE_SCENE_BOARD_WIDTH:
        _fail(f"{name} exceeds board width")
    if not 0.0 <= point[1] <= LIVE_SCENE_BOARD_HEIGHT:
        _fail(f"{name} exceeds board height")


def polygon_area(points: Sequence[Point]) -> float:
    """Return the unsigned shoelace area of a polygon."""

    return (
        abs(
            sum(
                start[0] * end[1] - end[0] * start[1]
                for start, end in zip(points, (*points[1:], points[0]), strict=True)
            )
        )
        / 2.0
    )


def verify_altitude(
    node: SceneNode,
    *,
    right: Point,
    side_a_end: Point,
    side_b_end: Point,
) -> Point:
    """Verify and return the perpendicular foot on the hypotenuse."""

    if not isinstance(node, PathSceneNode) or node.closed or len(node.points) != 2:
        _fail("altitude must be one open two-point path")
    for point in node.points:
        _require_point_on_board(point, name="altitude")

    hypotenuse = _vector(side_a_end, side_b_end)
    denominator = _dot(hypotenuse, hypotenuse)
    if denominator <= _EPSILON:
        _fail("cannot project onto a degenerate hypotenuse")
    from_a = _vector(side_a_end, right)
    parameter = _dot(from_a, hypotenuse) / denominator
    expected_foot = (
        side_a_end[0] + parameter * hypotenuse[0],
        side_a_end[1] + parameter * hypotenuse[1],
    )
    if not 0.0 < parameter < 1.0:
        _fail("altitude foot must lie inside the hypotenuse")
    if not _point_close(node.points[0], right) or not _point_close(node.points[1], expected_foot):
        _fail("altitude must join the right angle to its hypotenuse projection")
    altitude = _vector(*node.points)
    if _length(altitude) <= _EPSILON or not isclose(
        _dot(altitude, hypotenuse), 0.0, abs_tol=_EPSILON
    ):
        _fail("altitude must be perpendicular to the hypotenuse")
    return expected_foot


def verify_partition(
    node: SceneNode,
    *,
    altitude_foot: Point,
    square_c: Sequence[Point],
) -> Point:
    """Verify and return the far endpoint of the extended altitude."""

    if not isinstance(node, PathSceneNode) or node.closed or len(node.points) != 2:
        _fail("partition must be one open two-point path")
    for point in node.points:
        _require_point_on_board(point, name="partition")

    square_offset = _vector(square_c[0], square_c[3])
    expected_far_point = (
        altitude_foot[0] + square_offset[0],
        altitude_foot[1] + square_offset[1],
    )
    if not _point_close(node.points[0], altitude_foot) or not _point_close(
        node.points[1], expected_far_point
    ):
        _fail("partition must extend the altitude across the hypotenuse square")

    far_edge = _vector(square_c[3], square_c[2])
    far_offset = _vector(square_c[3], expected_far_point)
    far_denominator = _dot(far_edge, far_edge)
    if far_denominator <= _EPSILON:
        _fail("hypotenuse square has a degenerate far edge")
    far_parameter = _dot(far_offset, far_edge) / far_denominator
    if not isclose(_cross(far_edge, far_offset), 0.0, abs_tol=_EPSILON) or not (
        0.0 < far_parameter < 1.0
    ):
        _fail("partition must terminate inside the opposite square edge")
    return expected_far_point


def verify_region(
    node: SceneNode,
    *,
    name: str,
    expected_points: tuple[Point, Point, Point, Point],
    expected_area: float,
) -> tuple[Point, Point, Point, Point]:
    """Verify one exact quadrilateral created by the square partition."""

    if not isinstance(node, PathSceneNode) or not node.closed or len(node.points) != 4:
        _fail(f"{name} must be one closed four-point path")
    for point in node.points:
        _require_point_on_board(point, name=name)
    if any(
        not _point_close(actual, expected)
        for actual, expected in zip(node.points, expected_points, strict=True)
    ):
        _fail(f"{name} does not match the verified square partition")
    if not isclose(polygon_area(node.points), expected_area, abs_tol=_EPSILON):
        _fail(f"{name} has the wrong area")
    return node.points


def verify_region_coverage(
    *,
    region_a: Sequence[Point],
    region_b: Sequence[Point],
    square_c: Sequence[Point],
    altitude_foot: Point,
    far_partition: Point,
) -> None:
    """Prove the regions exactly cover the square and occupy opposite half-planes."""

    if not isclose(
        polygon_area(region_a) + polygon_area(region_b),
        polygon_area(square_c),
        abs_tol=_EPSILON,
    ):
        _fail("proof regions do not exactly cover the hypotenuse square")
    partition = _vector(altitude_foot, far_partition)
    center_a = tuple(sum(point[axis] for point in region_a) / len(region_a) for axis in range(2))
    center_b = tuple(sum(point[axis] for point in region_b) / len(region_b) for axis in range(2))
    if (
        _cross(partition, _vector(altitude_foot, center_a))
        * _cross(partition, _vector(altitude_foot, center_b))
        >= 0.0
    ):
        _fail("proof regions overlap instead of occupying opposite half-planes")


def verify_projection_area_equivalence(
    *,
    side_a_end: Point,
    side_b_end: Point,
    altitude_foot: Point,
    square_a: Sequence[Point],
    square_b: Sequence[Point],
) -> None:
    """Verify c*AH=a^2 and c*HB=b^2 from measured geometry."""

    hypotenuse_length = _length(_vector(side_a_end, side_b_end))
    if not isclose(
        _length(_vector(side_a_end, altitude_foot)) * hypotenuse_length,
        polygon_area(square_a),
        abs_tol=_EPSILON,
    ) or not isclose(
        _length(_vector(altitude_foot, side_b_end)) * hypotenuse_length,
        polygon_area(square_b),
        abs_tol=_EPSILON,
    ):
        _fail("hypotenuse projections do not establish the leg-square areas")


def verify_projection_identity(node: SceneNode) -> None:
    """Verify the compact exact projection text and its board bounds."""

    if not isinstance(node, TextSceneNode) or node.text != PROJECTION_IDENTITY_TEXT:
        _fail("projection_identity must be the exact required text")
    font_size = node.style.font_size
    width = max(48.0, len(node.text) * font_size * 0.75)
    height = max(36.0, font_size * 1.25)
    if node.style.anchor == "middle":
        left = node.x - width / 2.0
    elif node.style.anchor == "end":
        left = node.x - width
    else:
        left = node.x
    box = (left, node.y - height / 2.0, left + width, node.y + height / 2.0)
    if not all(isfinite(value) for value in box):
        _fail("projection_identity contains non-finite bounds")
    if (
        box[0] < 0.0
        or box[1] < 0.0
        or box[2] > LIVE_SCENE_BOARD_WIDTH
        or box[3] > LIVE_SCENE_BOARD_HEIGHT
    ):
        _fail("projection_identity exceeds board bounds")


def verify_proof_conclusion(node: SceneNode, *, identity: SceneNode) -> None:
    """Verify an exact underline bound to the already-verified identity node."""

    if not isinstance(identity, LatexSceneNode):
        _fail("proof conclusion requires the verified identity")
    if not isinstance(node, PathSceneNode) or node.closed or len(node.points) != 2:
        _fail("proof_conclusion must be one open two-point emphasis path")
    for point in node.points:
        _require_point_on_board(point, name="proof_conclusion")
    expected = (
        (identity.x, identity.y + 102.0),
        (identity.x + _LATEX_WIDTH, identity.y + 102.0),
    )
    if any(
        not _point_close(actual, target)
        for actual, target in zip(node.points, expected, strict=True)
    ):
        _fail("proof_conclusion must emphasize the verified identity")


__all__ = [
    "PROJECTION_IDENTITY_TEXT",
    "PythagoreanProofError",
    "polygon_area",
    "verify_altitude",
    "verify_partition",
    "verify_projection_area_equivalence",
    "verify_projection_identity",
    "verify_proof_conclusion",
    "verify_region",
    "verify_region_coverage",
]

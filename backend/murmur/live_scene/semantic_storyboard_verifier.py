"""Independent verification for Gate 1.8 semantic storyboards.

The verifier intentionally shares no visual implementation with the compiler.
It rebuilds the expected physics, retained-DOM snapshot, atomic patch, copy,
motion, and camera from the closed problem and semantic-record contracts.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias

from pydantic import ValidationError

from murmur.live_scene.choreography_contracts import (
    ChoreographyEasing,
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
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    MAX_NDJSON_FRAME_BYTES,
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
from murmur.live_scene.semantic_integrity import canonical_json_v1, canonical_sha256
from murmur.live_scene.semantic_storyboard_contracts import (
    PROJECTILE_STORYBOARD_COMPONENT_ID,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardClaimId,
    StoryboardConceptId,
    StoryboardEvidenceId,
    StoryboardSemanticEffectClosureV1,
    StoryboardTrajectoryId,
    TraceStoryboardRecordV1,
)

Point: TypeAlias = tuple[float, float]
NodeMap: TypeAlias = dict[str, SceneNode]

_EPSILON = 1e-7
_GRAVITY_MPS2 = 10.0
_ORIGIN: Point = (72.0, 468.0)
_PATH_SAMPLES = 65
_LAUNCH_RAY_LENGTH = 78.0
_PANEL_CENTER_X = 690.0
_PANEL_WIDTH = 188.0
_CAMERA_SAFE_INSET = 12.0
_PROGRAM_HASH_DOMAIN = "murmur:semantic-storyboard-program:v1"
_CATALOG_VERSION = "projectile-comparison-catalog-v1"
_PREFIX = f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__"
_ANCHOR_CHECKPOINT_ID = "storyboard-anchor"

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
_LOWER_MARKER_STYLE = {
    **_LOWER_LINE_STYLE,
    "fill": "hsl(var(--amber))",
    "strokeWidth": 2.0,
}
_HIGHER_MARKER_STYLE = {
    **_HIGHER_LINE_STYLE,
    "fill": "transparent",
    "strokeWidth": 2.0,
}
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

_ANCHOR_MANIFEST = (
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
_PANEL_SUFFIXES = frozenset(
    {
        "givens",
        "range_formula",
        "complementary_angles",
        "sine_relation",
        "range_relation",
        "height_relation",
        "flight_time_lower",
        "flight_time_higher",
        "flight_relation",
    }
)
_TRAJECTORY_EVIDENCE = (
    StoryboardEvidenceId.LOWER_TRAJECTORY,
    StoryboardEvidenceId.HIGHER_TRAJECTORY,
)
_CLAIM_EVIDENCE = {
    StoryboardClaimId.EQUAL_RANGE: (
        _TRAJECTORY_EVIDENCE,
        (StoryboardEvidenceId.RANGE_FORMULA, StoryboardEvidenceId.COMPLEMENTARY_ANGLES),
    ),
    StoryboardClaimId.UNEQUAL_RANGE: (
        _TRAJECTORY_EVIDENCE,
        (StoryboardEvidenceId.RANGE_FORMULA,),
    ),
    StoryboardClaimId.HIGHER_APEX: (_TRAJECTORY_EVIDENCE,),
    StoryboardClaimId.LONGER_FLIGHT: (_TRAJECTORY_EVIDENCE,),
}


class SemanticStoryboardVerificationObligation(StrEnum):
    """Complete independent obligations bound by a successful result."""

    BLUEPRINT_CONTRACT = "blueprint_contract"
    PROBLEM_IDENTITY = "problem_identity"
    SEMANTIC_TRANSITION = "semantic_transition"
    EFFECT_CLOSURE = "effect_closure"
    PROGRAM_HASH = "program_hash"
    CERTIFICATE_CHAIN = "certificate_chain"
    STABLE_IDS = "stable_ids"
    BOARD_BOUNDS = "board_bounds"
    LANE_LAYOUT = "lane_layout"
    PHYSICS_GEOMETRY = "physics_geometry"
    LABEL_FACT = "label_fact"
    VISUAL_STYLE = "visual_style"
    PATCH = "patch"
    CAPTION = "caption"
    CHOREOGRAPHY = "choreography"
    TIMING = "timing"
    VIEWPORT = "viewport"


SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS = tuple(SemanticStoryboardVerificationObligation)


class SemanticStoryboardVerificationError(ValueError):
    """Typed, prompt-free rejection from the independent verifier."""

    def __init__(
        self,
        code: SemanticStoryboardVerificationObligation,
        message: str,
    ) -> None:
        if not isinstance(code, SemanticStoryboardVerificationObligation):
            raise TypeError("code must be a SemanticStoryboardVerificationObligation")
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedStoryboardCheckpoint:
    """Compact proof output consumed by the future certificate layer."""

    checkpoint_id: str
    operation_targets: tuple[str, ...]
    base_program_sha256: str
    result_program_sha256: str
    semantic_effect: StoryboardSemanticEffectClosureV1 | None
    obligation_codes: tuple[SemanticStoryboardVerificationObligation, ...]


class SemanticStoryboardCheckpointBlueprintLike(Protocol):
    checkpoint_id: str
    base_component: ProjectileStoryboardStateV1 | None
    result_component: ProjectileStoryboardStateV1
    base_nodes: tuple[SceneNode, ...]
    result_nodes: tuple[SceneNode, ...]
    patch: ScenePatchDraft
    choreography: ChoreographyPlanV2
    presentation: PresentationCheckpointV1


@dataclass(frozen=True, slots=True)
class _Trajectory:
    angle_deg: int
    range_m: float
    height_m: float
    flight_time_s: float
    points: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class _VisualModel:
    lower: _Trajectory
    higher: _Trajectory


def _fail(code: SemanticStoryboardVerificationObligation, message: str) -> None:
    raise SemanticStoryboardVerificationError(code, message)


def _strict_contract(value: object, expected_type: type, *, label: str):
    if not isinstance(value, expected_type):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            f"{label} has the wrong contract type",
        )
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"^Pydantic serializer warnings:",
                category=UserWarning,
            )
            payload = value.model_dump(mode="python", by_alias=True)
        rebuilt = expected_type.model_validate(payload)
    except (AttributeError, TypeError, ValidationError, ValueError):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            f"{label} does not survive strict contract validation",
        )
    if rebuilt != value:
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            f"{label} changes under strict contract validation",
        )
    return rebuilt


def _read_blueprint(blueprint: object) -> SemanticStoryboardCheckpointBlueprintLike:
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
    if any(not hasattr(blueprint, field) for field in required):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            "compiler blueprint is missing a required public field",
        )
    if not isinstance(blueprint.checkpoint_id, str):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            "checkpoint_id must be a string",
        )
    if not isinstance(blueprint.base_nodes, tuple) or not isinstance(blueprint.result_nodes, tuple):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            "blueprint node snapshots must be immutable tuples",
        )
    for snapshot_label, nodes in (
        ("base snapshot", blueprint.base_nodes),
        ("result snapshot", blueprint.result_nodes),
    ):
        for index, node in enumerate(nodes):
            if isinstance(node, LatexTokenSceneNode):
                node_contract = LatexTokenSceneNode
            elif isinstance(node, LineSceneNode):
                node_contract = LineSceneNode
            elif isinstance(node, PathSceneNode):
                node_contract = PathSceneNode
            else:
                _fail(
                    SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
                    f"{snapshot_label} node {index} has an unsupported contract type",
                )
            _strict_contract(node, node_contract, label=f"{snapshot_label} node {index}")
    _strict_contract(blueprint.result_component, ProjectileStoryboardStateV1, label="result")
    if blueprint.base_component is not None:
        _strict_contract(blueprint.base_component, ProjectileStoryboardStateV1, label="base")
    for label, value, contract in (
        ("patch", blueprint.patch, ScenePatchDraft),
        ("choreography", blueprint.choreography, ChoreographyPlanV2),
        ("presentation", blueprint.presentation, PresentationCheckpointV1),
    ):
        _strict_contract(value, contract, label=label)
    return blueprint  # type: ignore[return-value]


def _node_id(suffix: str) -> str:
    return f"{_PREFIX}{suffix}"


def _record_target(record: AcceptedSemanticStoryboardRecordV1) -> StrEnum:
    if isinstance(record, RevealStoryboardRecordV1):
        return record.concept_id
    if isinstance(record, TraceStoryboardRecordV1):
        return record.trajectory_id
    if isinstance(record, RelateStoryboardRecordV1):
        return record.claim_id
    _fail(
        SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
        "semantic program contains a non-accepted record",
    )


def _record_slug(record: AcceptedSemanticStoryboardRecordV1) -> str:
    return f"{record.act}-{_record_target(record).value.replace('_', '-')}"


def _verify_semantic_program(
    problem: PairedProjectileComparisonSpecV1,
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> None:
    """Recheck applicability, uniqueness, and evidence without routing helpers."""

    effects: set[tuple[str, StrEnum]] = set()
    visible: set[StoryboardEvidenceId] = set()
    if len(records) > (7 if problem.lower_angle_deg + problem.higher_angle_deg == 90 else 6):
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "semantic program exceeds the applicable closed catalog",
        )
    for record in records:
        if not isinstance(
            record,
            RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
        ):
            _fail(
                SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
                "semantic program contains a non-accepted record",
            )
        effect = (record.act, _record_target(record))
        if effect in effects:
            _fail(
                SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
                "semantic program repeats one catalog effect",
            )
        complementary = problem.lower_angle_deg + problem.higher_angle_deg == 90
        if isinstance(record, RevealStoryboardRecordV1):
            if record.concept_id is StoryboardConceptId.COMPLEMENTARY_ANGLES and not complementary:
                _fail(
                    SemanticStoryboardVerificationObligation.LABEL_FACT,
                    "complementary-angle reveal is false for the bound problem",
                )
            visible.add(
                StoryboardEvidenceId.RANGE_FORMULA
                if record.concept_id is StoryboardConceptId.RANGE_FORMULA
                else StoryboardEvidenceId.COMPLEMENTARY_ANGLES
            )
        elif isinstance(record, TraceStoryboardRecordV1):
            visible.add(
                StoryboardEvidenceId.LOWER_TRAJECTORY
                if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE
                else StoryboardEvidenceId.HIGHER_TRAJECTORY
            )
        else:
            range_truth = record.claim_id in {
                StoryboardClaimId.EQUAL_RANGE,
                StoryboardClaimId.UNEQUAL_RANGE,
            }
            if range_truth and (
                (record.claim_id is StoryboardClaimId.EQUAL_RANGE) != complementary
            ):
                _fail(
                    SemanticStoryboardVerificationObligation.LABEL_FACT,
                    "range claim is false for the bound problem",
                )
            if record.evidence_ids not in _CLAIM_EVIDENCE[record.claim_id]:
                _fail(
                    SemanticStoryboardVerificationObligation.EFFECT_CLOSURE,
                    "claim uses a non-catalog evidence combination",
                )
            if not set(record.evidence_ids).issubset(visible):
                _fail(
                    SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
                    "claim consumes evidence absent from its accepted prefix",
                )
        effects.add(effect)


def _format_value(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".")


def _derive_visual(problem: PairedProjectileComparisonSpecV1) -> _VisualModel:
    def raw(angle_deg: int) -> tuple[float, float, float]:
        angle = math.radians(angle_deg)
        speed_squared = problem.speed_mps * problem.speed_mps
        return (
            speed_squared * math.sin(2.0 * angle) / _GRAVITY_MPS2,
            speed_squared * math.sin(angle) ** 2 / (2.0 * _GRAVITY_MPS2),
            2.0 * problem.speed_mps * math.sin(angle) / _GRAVITY_MPS2,
        )

    lower_raw = raw(problem.lower_angle_deg)
    higher_raw = raw(problem.higher_angle_deg)
    lower_range, lower_height, lower_time = lower_raw
    higher_range, higher_height, higher_time = higher_raw
    if problem.lower_angle_deg + problem.higher_angle_deg == 90:
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
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode(
        id=_node_id(suffix),
        kind="path",
        presentation=_DRAW,
        points=tuple(
            (
                center[0] + radius * math.cos(math.tau * index / 16.0),
                center[1] + radius * math.sin(math.tau * index / 16.0),
            )
            for index in range(16)
        ),
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


def _range_symbol(visual: _VisualModel) -> str:
    if math.isclose(visual.lower.range_m, visual.higher.range_m, abs_tol=1e-12):
        return "="
    return "<" if visual.lower.range_m < visual.higher.range_m else ">"


def _range_relation(symbol: str) -> LatexTokenSceneNode:
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
    nodes: tuple[SceneNode, ...] = (
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
        _ring("projectile_marker_lower", _ORIGIN, 5.0, _LOWER_MARKER_STYLE),
        _ring("projectile_marker_higher", _ORIGIN, 8.0, _HIGHER_MARKER_STYLE),
        _token("axis_x_label", "x", 555.0, 472.0, 24.0, height=28.0, style=_SOFT_TEXT),
        _token("axis_y_label", "y", 58.0, 194.0, 24.0, height=28.0, style=_SOFT_TEXT),
        _token(
            "launch_angle_lower",
            rf"{problem.lower_angle_deg}^\circ",
            *lower_label,
            58.0,
            height=36.0,
            style=_AMBER_TEXT,
        ),
        _token(
            "launch_angle_higher",
            rf"{problem.higher_angle_deg}^\circ",
            *higher_label,
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
        _range_relation("?"),
    )
    return {node.id: node for node in nodes}


def _record_manifest(record: AcceptedSemanticStoryboardRecordV1) -> tuple[str, ...]:
    if isinstance(record, RevealStoryboardRecordV1):
        return (
            "range_formula"
            if record.concept_id is StoryboardConceptId.RANGE_FORMULA
            else "complementary_angles",
        )
    if isinstance(record, TraceStoryboardRecordV1):
        suffix = "lower" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "higher"
        return (f"trajectory_{suffix}", f"projectile_marker_{suffix}")
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if record.evidence_ids != _TRAJECTORY_EVIDENCE:
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


def _record_nodes(
    record: AcceptedSemanticStoryboardRecordV1,
    problem: PairedProjectileComparisonSpecV1,
    visual: _VisualModel,
) -> NodeMap:
    if isinstance(record, RevealStoryboardRecordV1):
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

    if isinstance(record, TraceStoryboardRecordV1):
        lower = record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE
        suffix = "lower" if lower else "higher"
        trajectory = visual.lower if lower else visual.higher
        path_style = _LOWER_PATH_STYLE if lower else _HIGHER_PATH_STYLE
        marker_style = _LOWER_MARKER_STYLE if lower else _HIGHER_MARKER_STYLE
        marker_radius = 5.0 if lower else 8.0
        nodes = (
            _path(f"trajectory_{suffix}", trajectory.points, path_style),
            _ring(
                f"projectile_marker_{suffix}",
                trajectory.points[-1],
                marker_radius,
                marker_style,
            ),
        )
        return {node.id: node for node in nodes}

    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        symbol = _range_symbol(visual)
        if record.evidence_ids != _TRAJECTORY_EVIDENCE:
            node = _token(
                "sine_relation",
                rf"\sin({2 * problem.lower_angle_deg}^\circ)\ {symbol}\ "
                rf"\sin({2 * problem.higher_angle_deg}^\circ)",
                _PANEL_CENTER_X,
                348.0,
                _PANEL_WIDTH,
                height=38.0,
                style=_DYNAMIC_SAGE_TEXT,
            )
            relation = _range_relation(symbol)
            return {node.id: node, relation.id: relation}
        lower_end = visual.lower.points[-1]
        higher_end = visual.higher.points[-1]
        nodes = (
            _line(
                "range_bracket_lower",
                (72.0, 500.0),
                (lower_end[0], 500.0),
                _LOWER_BRACKET_STYLE,
            ),
            _line(
                "range_bracket_higher",
                (72.0, 548.0),
                (higher_end[0], 548.0),
                _HIGHER_BRACKET_STYLE,
            ),
            _ring("landing_ring_lower", lower_end, 5.0, _LOWER_MARKER_STYLE),
            _ring("landing_ring_higher", higher_end, 8.0, _HIGHER_MARKER_STYLE),
            _token(
                "range_value_lower",
                rf"R_L={_format_value(visual.lower.range_m)}\,\mathrm{{m}}",
                (_ORIGIN[0] + lower_end[0]) / 2.0,
                470.0,
                126.0,
                height=28.0,
                style=_DYNAMIC_AMBER_TEXT,
            ),
            _token(
                "range_value_higher",
                rf"R_H={_format_value(visual.higher.range_m)}\,\mathrm{{m}}",
                (_ORIGIN[0] + higher_end[0]) / 2.0,
                518.0,
                126.0,
                height=28.0,
                style=_DYNAMIC_LAVENDER_TEXT,
            ),
            _range_relation(symbol),
        )
        return {node.id: node for node in nodes}

    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        lower_apex = visual.lower.points[32]
        higher_apex = visual.higher.points[32]
        nodes = (
            _line(
                "height_bracket_lower",
                (lower_apex[0], 468.0),
                lower_apex,
                _LOWER_BRACKET_STYLE,
            ),
            _line(
                "height_bracket_higher",
                (higher_apex[0], 468.0),
                higher_apex,
                _HIGHER_BRACKET_STYLE,
            ),
            _ring("apex_ring_lower", lower_apex, 5.0, _LOWER_MARKER_STYLE),
            _ring("apex_ring_higher", higher_apex, 8.0, _HIGHER_MARKER_STYLE),
            _token(
                "height_value_lower",
                rf"H_L={_format_value(visual.lower.height_m)}\,\mathrm{{m}}",
                min(510.0, lower_apex[0] + 64.0),
                min(430.0, lower_apex[1] + 18.0),
                126.0,
                height=30.0,
                style=_DYNAMIC_AMBER_TEXT,
            ),
            _token(
                "height_value_higher",
                rf"H_H={_format_value(visual.higher.height_m)}\,\mathrm{{m}}",
                max(130.0, higher_apex[0] - 64.0),
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
                style=_SAGE_TEXT,
            ),
        )
        return {node.id: node for node in nodes}

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


def _expected_nodes(component: ProjectileStoryboardStateV1) -> tuple[SceneNode, ...]:
    _verify_semantic_program(component.problem_spec, component.accepted_records)
    visual = _derive_visual(component.problem_spec)
    nodes = _anchor_nodes(component.problem_spec)
    order = [_node_id(suffix) for suffix in _ANCHOR_MANIFEST]
    for record in component.accepted_records:
        effect = _record_nodes(record, component.problem_spec, visual)
        manifest = tuple(_node_id(suffix) for suffix in _record_manifest(record))
        if set(effect) != set(manifest):
            _fail(
                SemanticStoryboardVerificationObligation.STABLE_IDS,
                "independent effect manifest is incomplete",
            )
        for node_id in manifest:
            if node_id not in nodes:
                order.append(node_id)
            nodes[node_id] = effect[node_id]
    return tuple(nodes[node_id] for node_id in order)


def _program_sha256(
    problem: PairedProjectileComparisonSpecV1,
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> str:
    return canonical_sha256(
        {
            "catalogVersion": _CATALOG_VERSION,
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "acceptedRecords": [
                record.model_dump(mode="json", by_alias=True) for record in records
            ],
        },
        domain=_PROGRAM_HASH_DOMAIN,
    )


def _semantic_effect(
    record: AcceptedSemanticStoryboardRecordV1,
) -> StoryboardSemanticEffectClosureV1:
    if isinstance(record, RevealStoryboardRecordV1):
        evidence = (
            StoryboardEvidenceId.RANGE_FORMULA
            if record.concept_id is StoryboardConceptId.RANGE_FORMULA
            else StoryboardEvidenceId.COMPLEMENTARY_ANGLES
        )
        return StoryboardSemanticEffectClosureV1(
            conceptIds=(record.concept_id,),
            producedEvidenceIds=(evidence,),
        )
    if isinstance(record, TraceStoryboardRecordV1):
        evidence = (
            StoryboardEvidenceId.LOWER_TRAJECTORY
            if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE
            else StoryboardEvidenceId.HIGHER_TRAJECTORY
        )
        return StoryboardSemanticEffectClosureV1(
            trajectoryIds=(record.trajectory_id,),
            producedEvidenceIds=(evidence,),
        )
    return StoryboardSemanticEffectClosureV1(
        claimIds=(record.claim_id,),
        consumedEvidenceIds=record.evidence_ids,
    )


def _caption(
    record: AcceptedSemanticStoryboardRecordV1 | None,
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
        color = (
            "amber" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "lavender"
        )
        angle = lower if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else higher
        return f"The {color} marker traces the {angle}° trajectory from launch to impact."
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
        if record.evidence_ids == _TRAJECTORY_EVIDENCE:
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


def _viewport(values: tuple[float, float, float, float]) -> ViewportPoseV1:
    return ViewportPoseV1(x=values[0], y=values[1], width=values[2], height=values[3])


def _viewports(
    record: AcceptedSemanticStoryboardRecordV1 | None,
) -> LayoutViewportMapV1:
    if record is None:
        values = ((0.0, 0.0, 800.0, 600.0), (24.0, 336.0, 320.0, 240.0))
    elif isinstance(record, RevealStoryboardRecordV1):
        values = (
            ((548.0, 128.0, 252.0, 142.0), (548.0, 114.0, 252.0, 189.0))
            if record.concept_id is StoryboardConceptId.RANGE_FORMULA
            else ((548.0, 176.0, 252.0, 142.0), (548.0, 154.0, 252.0, 189.0))
        )
    elif (
        isinstance(record, TraceStoryboardRecordV1)
        or record.claim_id is StoryboardClaimId.HIGHER_APEX
    ):
        values = ((36.0, 204.0, 548.0, 324.0), (36.0, 160.0, 548.0, 411.0))
    elif record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        values = (
            ((36.0, 230.0, 548.0, 338.0), (36.0, 230.0, 548.0, 338.0))
            if record.evidence_ids == _TRAJECTORY_EVIDENCE
            else ((492.0, 140.0, 308.0, 326.0), (492.0, 140.0, 308.0, 326.0))
        )
    else:
        values = ((569.0, 472.0, 231.0, 128.0), (584.0, 438.0, 216.0, 162.0))
    return LayoutViewportMapV1(cinematic=_viewport(values[0]), compact=_viewport(values[1]))


def _state_viewports(component: ProjectileStoryboardStateV1 | None) -> LayoutViewportMapV1:
    record = (
        None
        if component is None or not component.accepted_records
        else component.accepted_records[-1]
    )
    return _viewports(record)


def _timing(
    record: AcceptedSemanticStoryboardRecordV1 | None,
    visual: _VisualModel,
) -> tuple[int, int, ChoreographyEasing]:
    if record is None:
        return 800, 250, ChoreographyEasing.EASE_OUT_QUART
    if isinstance(record, RevealStoryboardRecordV1):
        return 650, 300, ChoreographyEasing.EASE_OUT_QUART
    if isinstance(record, TraceStoryboardRecordV1):
        trajectory = (
            visual.lower
            if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE
            else visual.higher
        )
        duration = min(3_200, max(1_600, round(900.0 + 450.0 * trajectory.flight_time_s)))
        return duration, 200, ChoreographyEasing.EASE_IN_OUT
    if record.claim_id in {StoryboardClaimId.EQUAL_RANGE, StoryboardClaimId.UNEQUAL_RANGE}:
        if record.evidence_ids == _TRAJECTORY_EVIDENCE:
            return 1_000, 450, ChoreographyEasing.EASE_OUT_QUINT
        return 1_100, 550, ChoreographyEasing.EASE_OUT_QUINT
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return 1_100, 450, ChoreographyEasing.EASE_OUT_QUART
    return 900, 400, ChoreographyEasing.EASE_OUT_QUART


def _subjects(
    record: AcceptedSemanticStoryboardRecordV1 | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if record is None:
        targets = (
            "launch_ray_lower",
            "launch_ray_higher",
            "launch_angle_lower",
            "launch_angle_higher",
        )
        return targets, targets
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
        if record.evidence_ids != _TRAJECTORY_EVIDENCE:
            return ("sine_relation", "range_relation"), ("sine_relation", "range_relation")
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
    if record.claim_id is StoryboardClaimId.HIGHER_APEX:
        return ("apex_ring_lower", "apex_ring_higher"), (
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


def _expected_choreography(
    record: AcceptedSemanticStoryboardRecordV1 | None,
    base_nodes: tuple[SceneNode, ...],
    result_nodes: tuple[SceneNode, ...],
    operation_targets: tuple[str, ...],
    visual: _VisualModel,
) -> ChoreographyPlanV2:
    base_ids = {node.id for node in base_nodes}
    result_ids = {node.id for node in result_nodes}
    added = sorted(target for target in operation_targets if target not in base_ids)
    updated = sorted(target for target in operation_targets if target in base_ids)
    cues = []
    if added:
        cues.append(EnterCueV1(targetIds=tuple(added)))
    trace: tuple[str, str] | None = None
    if isinstance(record, TraceStoryboardRecordV1):
        suffix = "lower" if record.trajectory_id is StoryboardTrajectoryId.LOWER_ANGLE else "higher"
        trace = (_node_id(f"trajectory_{suffix}"), _node_id(f"projectile_marker_{suffix}"))
        updated.remove(trace[1])
    if updated:
        cues.append(TransformCueV1(targetIds=tuple(updated)))
    if trace is not None:
        cues.append(TracePathCueV2(pathId=trace[0], markerId=trace[1]))
    emphasis, focus = _subjects(record)
    emphasis_ids = tuple(sorted(_node_id(suffix) for suffix in emphasis))
    focus_ids = tuple(sorted(_node_id(suffix) for suffix in focus))
    if not set((*emphasis_ids, *focus_ids)).issubset(result_ids):
        _fail(
            SemanticStoryboardVerificationObligation.CHOREOGRAPHY,
            "expected storyboard cue references an absent result node",
        )
    cues.append(EmphasizeCueV1(targetIds=emphasis_ids))
    cues.append(FocusCueV1(targetIds=focus_ids))
    duration, hold, easing = _timing(record, visual)
    return ChoreographyPlanV2(
        phase=ChoreographyPhaseV2(
            cues=tuple(cues),
            durationMs=duration,
            easing=easing,
            holdAfterMs=hold,
        )
    )


def _node_box(
    node: SceneNode,
    *,
    stroke_aware: bool = True,
) -> tuple[float, float, float, float]:
    if isinstance(node, LatexTokenSceneNode):
        left = node.x - node.width / 2.0 if node.anchor == "middle" else node.x
        if node.anchor == "end":
            left -= node.width
        return left, node.y, left + node.width, node.y + node.height
    if isinstance(node, LineSceneNode | PathSceneNode):
        xs = tuple(point[0] for point in node.points)
        ys = tuple(point[1] for point in node.points)
        padding = node.style.stroke_width / 2.0 if stroke_aware else 0.0
        return (
            min(xs) - padding,
            min(ys) - padding,
            max(xs) + padding,
            max(ys) + padding,
        )
    _fail(
        SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
        f"unsupported storyboard node kind for {node.id!r}",
    )


def _verify_bounds_and_lanes(nodes: Iterable[SceneNode]) -> None:
    panel_boxes: dict[str, tuple[float, float, float, float]] = {}
    for node in nodes:
        left, top, right, bottom = _node_box(node)
        if (
            left < -_EPSILON
            or top < -_EPSILON
            or right > LIVE_SCENE_BOARD_WIDTH + _EPSILON
            or bottom > LIVE_SCENE_BOARD_HEIGHT + _EPSILON
        ):
            _fail(
                SemanticStoryboardVerificationObligation.BOARD_BOUNDS,
                f"storyboard node {node.id!r} leaves the canonical board",
            )
        lane_left, lane_top, lane_right, lane_bottom = _node_box(node, stroke_aware=False)
        suffix = node.id.removeprefix(_PREFIX)
        if suffix == "panel_rule":
            if (lane_left, lane_right) != (582.0, 582.0):
                _fail(
                    SemanticStoryboardVerificationObligation.LANE_LAYOUT,
                    "panel rule does not preserve the lane boundary",
                )
        elif suffix in _PANEL_SUFFIXES:
            if lane_left < 582.0 - _EPSILON or lane_right > 800.0 + _EPSILON:
                _fail(
                    SemanticStoryboardVerificationObligation.LANE_LAYOUT,
                    f"right-lane node {node.id!r} crosses its lane",
                )
            panel_boxes[node.id] = (lane_left, lane_top, lane_right, lane_bottom)
        elif lane_right > 568.0 + _EPSILON:
            _fail(
                SemanticStoryboardVerificationObligation.LANE_LAYOUT,
                f"plot node {node.id!r} crosses the right-lane gutter",
            )
    panel_items = tuple(panel_boxes.items())
    for index, (left_id, left_box) in enumerate(panel_items):
        for right_id, right_box in panel_items[index + 1 :]:
            overlaps = (
                min(left_box[2], right_box[2]) - max(left_box[0], right_box[0]) > _EPSILON
                and min(left_box[3], right_box[3]) - max(left_box[1], right_box[1]) > _EPSILON
            )
            if overlaps:
                _fail(
                    SemanticStoryboardVerificationObligation.LANE_LAYOUT,
                    f"right-lane facts {left_id!r} and {right_id!r} collide",
                )


def _verify_viewport_targets(
    viewports: LayoutViewportMapV1,
    nodes: NodeMap,
    target_ids: Iterable[str],
) -> None:
    for target_id in set(target_ids):
        if target_id not in nodes:
            _fail(
                SemanticStoryboardVerificationObligation.VIEWPORT,
                f"camera target {target_id!r} is absent",
            )
        box = _node_box(nodes[target_id])
        for pose in (viewports.cinematic, viewports.compact):
            if (
                box[0] < pose.x + _CAMERA_SAFE_INSET - _EPSILON
                or box[1] < pose.y + _CAMERA_SAFE_INSET - _EPSILON
                or box[2] > pose.x + pose.width - _CAMERA_SAFE_INSET + _EPSILON
                or box[3] > pose.y + pose.height - _CAMERA_SAFE_INSET + _EPSILON
            ):
                _fail(
                    SemanticStoryboardVerificationObligation.VIEWPORT,
                    f"camera clips storyboard subject {target_id!r}",
                )


def _node_map(nodes: tuple[SceneNode, ...], *, label: str) -> NodeMap:
    if any(
        not isinstance(node, LineSceneNode | PathSceneNode | LatexTokenSceneNode) for node in nodes
    ):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            f"{label} contains an unsupported node contract",
        )
    ids = tuple(node.id for node in nodes)
    if len(ids) != len(set(ids)):
        _fail(
            SemanticStoryboardVerificationObligation.STABLE_IDS,
            f"{label} contains duplicate stable node IDs",
        )
    if any(not node_id.startswith(_PREFIX) for node_id in ids):
        _fail(
            SemanticStoryboardVerificationObligation.STABLE_IDS,
            f"{label} contains a foreign component node",
        )
    return {node.id: node for node in nodes}


def _verify_snapshot(
    actual: tuple[SceneNode, ...],
    expected: tuple[SceneNode, ...],
    *,
    label: str,
) -> NodeMap:
    nodes = _node_map(actual, label=label)
    if tuple(node.id for node in actual) != tuple(node.id for node in expected):
        _fail(
            SemanticStoryboardVerificationObligation.STABLE_IDS,
            f"{label} does not preserve the exact semantic paint order",
        )
    _verify_bounds_and_lanes(actual)
    for candidate, reference in zip(actual, expected, strict=True):
        if candidate == reference:
            continue
        if type(candidate) is not type(reference):
            code = SemanticStoryboardVerificationObligation.STABLE_IDS
        elif candidate.presentation != reference.presentation or candidate.style != reference.style:
            code = SemanticStoryboardVerificationObligation.VISUAL_STYLE
        elif (
            isinstance(candidate, LatexTokenSceneNode)
            and isinstance(reference, LatexTokenSceneNode)
            and candidate.latex != reference.latex
        ):
            code = SemanticStoryboardVerificationObligation.LABEL_FACT
        else:
            code = SemanticStoryboardVerificationObligation.PHYSICS_GEOMETRY
        _fail(code, f"{label} node {candidate.id!r} disagrees with independent derivation")
    return nodes


def _materialize_patch(
    base_nodes: tuple[SceneNode, ...],
    patch: ScenePatchDraft,
) -> tuple[SceneNode, ...]:
    order = [node.id for node in base_nodes]
    nodes = {node.id: node for node in base_nodes}
    for operation in patch.operations:
        if isinstance(operation, RemoveSceneOperation):
            _fail(
                SemanticStoryboardVerificationObligation.PATCH,
                "storyboard patches may never remove retained work",
            )
        if not isinstance(operation, PutSceneOperation):
            _fail(
                SemanticStoryboardVerificationObligation.PATCH,
                "storyboard patch contains an unsupported operation",
            )
        if nodes.get(operation.node.id) == operation.node:
            _fail(
                SemanticStoryboardVerificationObligation.PATCH,
                f"storyboard put for {operation.node.id!r} is a no-op",
            )
        if operation.node.id not in nodes:
            order.append(operation.node.id)
        nodes[operation.node.id] = operation.node
    return tuple(nodes[node_id] for node_id in order)


def _expected_patch(
    record: AcceptedSemanticStoryboardRecordV1 | None,
    problem: PairedProjectileComparisonSpecV1,
    result_nodes: tuple[SceneNode, ...],
    visual: _VisualModel,
) -> ScenePatchDraft:
    manifest = _ANCHOR_MANIFEST if record is None else _record_manifest(record)
    result = {node.id: node for node in result_nodes}
    slug = "anchor" if record is None else _record_slug(record)
    return ScenePatchDraft(
        patchId=f"{_PREFIX}cp_{slug}",
        narration=_caption(record, problem, visual),
        operations=tuple(
            PutSceneOperation(op="put", node=result[_node_id(suffix)]) for suffix in manifest
        ),
    )


def _verify_blueprint_visuals(
    checkpoint: SemanticStoryboardCheckpointBlueprintLike,
    *,
    record: AcceptedSemanticStoryboardRecordV1 | None,
    expected_base: ProjectileStoryboardStateV1 | None,
    expected_result: ProjectileStoryboardStateV1,
) -> tuple[str, ...]:
    if checkpoint.base_component != expected_base or checkpoint.result_component != expected_result:
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "blueprint components do not encode exactly one accepted semantic transition",
        )
    expected_base_nodes = () if expected_base is None else _expected_nodes(expected_base)
    expected_result_nodes = _expected_nodes(expected_result)
    visual = _derive_visual(expected_result.problem_spec)
    base = _verify_snapshot(checkpoint.base_nodes, expected_base_nodes, label="base snapshot")
    result = _verify_snapshot(
        checkpoint.result_nodes,
        expected_result_nodes,
        label="result snapshot",
    )
    expected_patch = _expected_patch(
        record,
        expected_result.problem_spec,
        expected_result_nodes,
        visual,
    )
    if checkpoint.patch != expected_patch:
        _fail(
            SemanticStoryboardVerificationObligation.PATCH,
            "patch does not match the exact atomic semantic effect",
        )
    if _materialize_patch(checkpoint.base_nodes, checkpoint.patch) != checkpoint.result_nodes:
        _fail(
            SemanticStoryboardVerificationObligation.PATCH,
            "patch does not exactly materialize the result snapshot",
        )
    if (
        len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
        > MAX_NDJSON_FRAME_BYTES
    ):
        _fail(
            SemanticStoryboardVerificationObligation.PATCH,
            "storyboard patch exceeds the wire frame budget",
        )
    operation_targets = tuple(operation.target_id for operation in checkpoint.patch.operations)
    expected_choreography = _expected_choreography(
        record,
        expected_base_nodes,
        expected_result_nodes,
        operation_targets,
        visual,
    )
    if checkpoint.choreography != expected_choreography:
        actual_phase = checkpoint.choreography.phase
        expected_phase = expected_choreography.phase
        if (
            actual_phase.duration_ms,
            actual_phase.hold_after_ms,
            actual_phase.easing,
        ) != (
            expected_phase.duration_ms,
            expected_phase.hold_after_ms,
            expected_phase.easing,
        ):
            code = SemanticStoryboardVerificationObligation.TIMING
        else:
            code = SemanticStoryboardVerificationObligation.CHOREOGRAPHY
        _fail(code, "choreography disagrees with the independently authored plan")
    narration = _caption(record, expected_result.problem_spec, visual)
    expected_presentation = PresentationCheckpointV1(
        checkpointId=checkpoint.checkpoint_id,
        checkpointNarration=narration,
        baseViewports=_state_viewports(expected_base),
        resultViewports=_state_viewports(expected_result),
    )
    if (
        checkpoint.patch.narration != narration
        or checkpoint.presentation.checkpoint_narration != narration
    ):
        _fail(
            SemanticStoryboardVerificationObligation.CAPTION,
            "storyboard caption changes an independently derived fact",
        )
    if checkpoint.presentation != expected_presentation:
        _fail(
            SemanticStoryboardVerificationObligation.VIEWPORT,
            "presentation checkpoint disagrees with the exact camera transition",
        )
    emphasis, focus = _subjects(record)
    camera_targets = [_node_id(suffix) for suffix in (*emphasis, *focus)]
    _verify_viewport_targets(checkpoint.presentation.result_viewports, result, camera_targets)
    if expected_base is not None and expected_base.accepted_records:
        base_emphasis, base_focus = _subjects(expected_base.accepted_records[-1])
        _verify_viewport_targets(
            checkpoint.presentation.base_viewports,
            base,
            (_node_id(suffix) for suffix in (*base_emphasis, *base_focus)),
        )
    return operation_targets


def verify_semantic_storyboard_anchor(
    problem_spec: PairedProjectileComparisonSpecV1,
    blueprint: SemanticStoryboardCheckpointBlueprintLike,
) -> VerifiedStoryboardCheckpoint:
    """Verify the sole provider-free anchor from primitive problem values."""

    problem = _strict_contract(
        problem_spec,
        PairedProjectileComparisonSpecV1,
        label="problem_spec",
    )
    checkpoint = _read_blueprint(blueprint)
    if checkpoint.checkpoint_id != _ANCHOR_CHECKPOINT_ID:
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "anchor checkpoint identity is not canonical",
        )
    result = ProjectileStoryboardStateV1(problemSpec=problem)
    operation_targets = _verify_blueprint_visuals(
        checkpoint,
        record=None,
        expected_base=None,
        expected_result=result,
    )
    program_hash = _program_sha256(problem, ())
    return VerifiedStoryboardCheckpoint(
        checkpoint_id=_ANCHOR_CHECKPOINT_ID,
        operation_targets=operation_targets,
        base_program_sha256=program_hash,
        result_program_sha256=program_hash,
        semantic_effect=None,
        obligation_codes=SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS,
    )


def verify_semantic_storyboard_checkpoint(
    beat: RoutedSemanticStoryboardBeatV1,
    blueprint: SemanticStoryboardCheckpointBlueprintLike,
    *,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> VerifiedStoryboardCheckpoint:
    """Verify one routed record as one atomic certified visual transition."""

    if not isinstance(beat, RoutedSemanticStoryboardBeatV1):
        _fail(
            SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT,
            "beat has the wrong contract type",
        )
    if isinstance(beat.record, RevealStoryboardRecordV1):
        record = _strict_contract(beat.record, RevealStoryboardRecordV1, label="record")
    elif isinstance(beat.record, TraceStoryboardRecordV1):
        record = _strict_contract(beat.record, TraceStoryboardRecordV1, label="record")
    elif isinstance(beat.record, RelateStoryboardRecordV1):
        record = _strict_contract(beat.record, RelateStoryboardRecordV1, label="record")
    else:
        _fail(
            SemanticStoryboardVerificationObligation.EFFECT_CLOSURE,
            "routed beat does not contain one accepted record",
        )
    expected_effect = _semantic_effect(record)
    if beat.semantic_effect != expected_effect:
        _fail(
            SemanticStoryboardVerificationObligation.EFFECT_CLOSURE,
            "routed semantic effect is not the exact closure of its record",
        )
    routed = _strict_contract(beat, RoutedSemanticStoryboardBeatV1, label="beat")
    semantic_scene = _strict_contract(
        base_semantic_scene,
        ProjectileStoryboardSemanticSceneStateV1,
        label="base_semantic_scene",
    )
    checkpoint = _read_blueprint(blueprint)
    if len(semantic_scene.components) != 1:
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "a model beat requires one certified storyboard component",
        )
    base_component = semantic_scene.components[0]
    if base_component.problem_spec != routed.problem_spec:
        _fail(
            SemanticStoryboardVerificationObligation.PROBLEM_IDENTITY,
            "routed problem does not match the certified semantic frontier",
        )
    if routed.component_id != PROJECTILE_STORYBOARD_COMPONENT_ID:
        _fail(
            SemanticStoryboardVerificationObligation.PROBLEM_IDENTITY,
            "routed beat targets a foreign component",
        )
    if routed.previous_certificate_sha256 != semantic_scene.certificate_head_sha256:
        _fail(
            SemanticStoryboardVerificationObligation.CERTIFICATE_CHAIN,
            "routed beat does not join the current certificate head",
        )
    base_records = base_component.accepted_records
    result_records = (*base_records, routed.record)
    _verify_semantic_program(routed.problem_spec, base_records)
    _verify_semantic_program(routed.problem_spec, result_records)
    if routed.ordinal != len(result_records):
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "routed ordinal does not advance by exactly one",
        )
    base_hash = _program_sha256(routed.problem_spec, base_records)
    result_hash = _program_sha256(routed.problem_spec, result_records)
    if routed.base_program_sha256 != base_hash or routed.result_program_sha256 != result_hash:
        _fail(
            SemanticStoryboardVerificationObligation.PROGRAM_HASH,
            "routed program hashes do not bind the exact accepted prefix",
        )
    slug = _record_slug(routed.record)
    if (
        routed.beat_id != f"storyboard-beat-{slug}"
        or routed.checkpoint_id != f"storyboard-checkpoint-{slug}"
        or checkpoint.checkpoint_id != routed.checkpoint_id
    ):
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "routed and visual checkpoint identities are not canonical",
        )
    result_component = ProjectileStoryboardStateV1(
        problemSpec=routed.problem_spec,
        acceptedRecords=result_records,
    )
    operation_targets = _verify_blueprint_visuals(
        checkpoint,
        record=routed.record,
        expected_base=base_component,
        expected_result=result_component,
    )
    return VerifiedStoryboardCheckpoint(
        checkpoint_id=routed.checkpoint_id,
        operation_targets=operation_targets,
        base_program_sha256=base_hash,
        result_program_sha256=result_hash,
        semantic_effect=expected_effect,
        obligation_codes=SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS,
    )


def verify_semantic_storyboard_frontier(
    problem_spec: PairedProjectileComparisonSpecV1,
    scene: SceneState,
    semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> None:
    """Verify an admitted semantic frontier against its complete retained DOM."""

    problem = _strict_contract(
        problem_spec,
        PairedProjectileComparisonSpecV1,
        label="problem_spec",
    )
    physical = _strict_contract(scene, SceneState, label="scene")
    semantic = _strict_contract(
        semantic_scene,
        ProjectileStoryboardSemanticSceneStateV1,
        label="semantic_scene",
    )
    if physical.revision != semantic.revision:
        _fail(
            SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
            "physical and semantic frontier revisions disagree",
        )
    if not semantic.components:
        if physical.nodes:
            _fail(
                SemanticStoryboardVerificationObligation.STABLE_IDS,
                "an empty semantic frontier cannot own storyboard nodes",
            )
        return
    component = semantic.components[0]
    if component.problem_spec != problem:
        _fail(
            SemanticStoryboardVerificationObligation.PROBLEM_IDENTITY,
            "semantic frontier is bound to a different problem",
        )
    _verify_snapshot(physical.nodes, _expected_nodes(component), label="committed frontier")


__all__ = [
    "SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS",
    "SemanticStoryboardCheckpointBlueprintLike",
    "SemanticStoryboardVerificationError",
    "SemanticStoryboardVerificationObligation",
    "VerifiedStoryboardCheckpoint",
    "verify_semantic_storyboard_anchor",
    "verify_semantic_storyboard_checkpoint",
    "verify_semantic_storyboard_frontier",
]

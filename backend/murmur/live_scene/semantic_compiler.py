"""Deterministic lowering of semantic teaching beats into visual atoms.

This module owns the canonical low-level realization. Before constructing any
atom it serializes the complete target prefix and sends those records across
the separate semantic-verifier boundary.
"""

from __future__ import annotations

from typing import TypeAlias

from murmur.live_scene.contracts import (
    LatexSceneNode,
    PathSceneNode,
    PutSceneOperation,
    SceneNode,
    ScenePatchDraft,
    TextSceneNode,
)
from murmur.live_scene.semantic_contracts import (
    CompiledTeachingBeat,
    CompiledVisualAtom,
    PythagoreanAreaIdentityState,
    PythagoreanRole,
    SemanticSceneState,
    TeachingBeatDraft,
    roles_through,
)
from murmur.live_scene.semantic_verifier import verify_pythagorean_realization

Point: TypeAlias = tuple[float, float]

_IDENTITY = "a^2+b^2=c^2"

_RIGHT_ANGLE: Point = (320.0, 260.0)
_SIDE_A_END: Point = (440.0, 260.0)
_SIDE_B_END: Point = (320.0, 420.0)

_ROLE_SUFFIX: dict[PythagoreanRole, str] = {
    PythagoreanRole.TRIANGLE: "triangle",
    PythagoreanRole.SQUARE_A: "square_a",
    PythagoreanRole.LABEL_A2: "label_a2",
    PythagoreanRole.SQUARE_B: "square_b",
    PythagoreanRole.LABEL_B2: "label_b2",
    PythagoreanRole.SQUARE_C: "square_c",
    PythagoreanRole.LABEL_C2: "label_c2",
    PythagoreanRole.IDENTITY: "identity",
}

_LABEL_TEXT: dict[PythagoreanRole, str] = {
    PythagoreanRole.LABEL_A2: "a²",
    PythagoreanRole.LABEL_B2: "b²",
    PythagoreanRole.LABEL_C2: "c²",
}

_DRAW_PRESENTATION = {"enter": "draw", "exit": "fade"}
_FADE_PRESENTATION = {"enter": "fade", "exit": "fade"}
_TRIANGLE_STYLE = {
    "stroke": "hsl(var(--chalk))",
    "strokeWidth": 4.0,
    "fill": "transparent",
    "opacity": 1.0,
    "roughness": 0.45,
}
_SQUARE_STYLES: dict[PythagoreanRole, dict[str, object]] = {
    PythagoreanRole.SQUARE_A: {
        "stroke": "hsl(var(--sage))",
        "strokeWidth": 3.0,
        "fill": "transparent",
        "opacity": 1.0,
        "roughness": 0.35,
    },
    PythagoreanRole.SQUARE_B: {
        "stroke": "hsl(var(--lavender))",
        "strokeWidth": 3.0,
        "fill": "transparent",
        "opacity": 1.0,
        "roughness": 0.35,
    },
    PythagoreanRole.SQUARE_C: {
        "stroke": "hsl(var(--amber))",
        "strokeWidth": 3.0,
        "fill": "transparent",
        "opacity": 1.0,
        "roughness": 0.35,
    },
}


class SemanticCompilationError(ValueError):
    """Raised when a semantic beat cannot be lowered monotonically."""


def _node_id(component_id: str, role: PythagoreanRole) -> str:
    return f"{component_id}__{_ROLE_SUFFIX[role]}"


def _atom_id(component_id: str, role: PythagoreanRole) -> str:
    return f"{component_id}__atom_{_ROLE_SUFFIX[role]}"


def _path(
    component_id: str,
    role: PythagoreanRole,
    points: tuple[Point, ...],
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode.model_validate(
        {
            "id": _node_id(component_id, role),
            "kind": "path",
            "presentation": _DRAW_PRESENTATION,
            "points": points,
            "closed": True,
            "style": style,
        }
    )


def _label(
    component_id: str,
    role: PythagoreanRole,
    point: Point,
) -> TextSceneNode:
    return TextSceneNode.model_validate(
        {
            "id": _node_id(component_id, role),
            "kind": "text",
            "presentation": _FADE_PRESENTATION,
            "x": point[0],
            "y": point[1],
            "text": _LABEL_TEXT[role],
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 30.0,
                "opacity": 1.0,
                "anchor": "middle",
            },
        }
    )


def _build_pythagorean_nodes(component_id: str) -> tuple[SceneNode, ...]:
    """Build the complete canonical realization in semantic reveal order."""

    square_a = (
        _RIGHT_ANGLE,
        _SIDE_A_END,
        (440.0, 140.0),
        (320.0, 140.0),
    )
    square_b = (
        _RIGHT_ANGLE,
        _SIDE_B_END,
        (160.0, 420.0),
        (160.0, 260.0),
    )
    square_c = (
        _SIDE_A_END,
        _SIDE_B_END,
        (480.0, 540.0),
        (600.0, 380.0),
    )

    return (
        _path(
            component_id,
            PythagoreanRole.TRIANGLE,
            (_RIGHT_ANGLE, _SIDE_A_END, _SIDE_B_END),
            _TRIANGLE_STYLE,
        ),
        _path(
            component_id,
            PythagoreanRole.SQUARE_A,
            square_a,
            _SQUARE_STYLES[PythagoreanRole.SQUARE_A],
        ),
        _label(component_id, PythagoreanRole.LABEL_A2, (380.0, 200.0)),
        _path(
            component_id,
            PythagoreanRole.SQUARE_B,
            square_b,
            _SQUARE_STYLES[PythagoreanRole.SQUARE_B],
        ),
        _label(component_id, PythagoreanRole.LABEL_B2, (240.0, 340.0)),
        _path(
            component_id,
            PythagoreanRole.SQUARE_C,
            square_c,
            _SQUARE_STYLES[PythagoreanRole.SQUARE_C],
        ),
        _label(component_id, PythagoreanRole.LABEL_C2, (460.0, 400.0)),
        LatexSceneNode.model_validate(
            {
                "id": _node_id(component_id, PythagoreanRole.IDENTITY),
                "kind": "latex",
                "presentation": _FADE_PRESENTATION,
                "x": 150.0,
                "y": 10.0,
                "latex": _IDENTITY,
                "style": {
                    "color": "hsl(var(--amber))",
                    "fontSize": 36.0,
                    "opacity": 1.0,
                },
            }
        ),
    )


def _serialize_nodes(nodes: tuple[SceneNode, ...]) -> tuple[dict[str, object], ...]:
    return tuple(node.model_dump(mode="json", by_alias=True) for node in nodes)


def compile_teaching_beat(
    beat: TeachingBeatDraft,
    base_scene: SemanticSceneState,
) -> CompiledTeachingBeat:
    """Compile one semantic beat into an all-or-nothing verified atom suffix."""

    component_id = beat.directive.id
    target_roles = roles_through(beat.directive.reveal_through)
    current = next(
        (component for component in base_scene.components if component.id == component_id),
        None,
    )
    current_roles = () if current is None else current.revealed_roles
    if current_roles != target_roles[: len(current_roles)]:
        raise SemanticCompilationError("a Pythagorean component cannot move backward")

    complete_nodes = _build_pythagorean_nodes(component_id)
    target_nodes = complete_nodes[: len(target_roles)]
    receipts = verify_pythagorean_realization(component_id, _serialize_nodes(target_nodes))

    atoms = tuple(
        CompiledVisualAtom(
            atom_id=_atom_id(component_id, role),
            beat_id=beat.beat_id,
            component_id=component_id,
            role=role,
            patch=ScenePatchDraft(
                patch_id=_atom_id(component_id, role),
                narration=beat.narration,
                operations=(PutSceneOperation(op="put", node=target_nodes[index]),),
            ),
            receipt=receipts[index],
        )
        for index, role in enumerate(target_roles)
        if index >= len(current_roles)
    )

    target_component = PythagoreanAreaIdentityState(
        id=component_id,
        revealed_roles=target_roles,
    )
    if current is None:
        result_components = (*base_scene.components, target_component)
    else:
        result_components = tuple(
            target_component if component.id == component_id else component
            for component in base_scene.components
        )
    result_scene = SemanticSceneState(
        revision=base_scene.revision + len(atoms),
        components=result_components,
    )
    return CompiledTeachingBeat(
        beat=beat,
        base_scene=base_scene,
        result_scene=result_scene,
        atoms=atoms,
    )


compile_pythagorean_area_identity = compile_teaching_beat

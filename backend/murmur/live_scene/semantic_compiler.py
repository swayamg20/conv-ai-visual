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
    SEMANTIC_COMPILER_VERSION,
    CompiledTeachingBeat,
    CompiledVisualAtom,
    CompilerCertificateBodyV1,
    CompilerCertificateV1,
    PythagoreanAreaIdentityState,
    PythagoreanRole,
    SemanticSceneState,
    TeachingBeatDraft,
    compiler_certificate_sha256,
    roles_through,
    scene_patch_sha256,
    semantic_scene_sha256,
    teaching_beat_sha256,
    verification_receipt_sha256,
)
from murmur.live_scene.semantic_verifier import verify_pythagorean_realization

Point: TypeAlias = tuple[float, float]

_IDENTITY = "a^2+b^2=c^2"
_PROJECTION_IDENTITY = "AH = a²/c  ·  HB = b²/c"

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
    PythagoreanRole.ALTITUDE: "altitude",
    PythagoreanRole.PARTITION: "partition",
    PythagoreanRole.REGION_A: "region_a",
    PythagoreanRole.REGION_A_LABEL: "region_a_label",
    PythagoreanRole.REGION_B: "region_b",
    PythagoreanRole.REGION_B_LABEL: "region_b_label",
    PythagoreanRole.PROJECTION_IDENTITY: "projection_identity",
    PythagoreanRole.PROOF_CONCLUSION: "proof_conclusion",
}

_LABEL_TEXT: dict[PythagoreanRole, str] = {
    PythagoreanRole.LABEL_A2: "a²",
    PythagoreanRole.LABEL_B2: "b²",
    PythagoreanRole.LABEL_C2: "c²",
    PythagoreanRole.REGION_A_LABEL: "a²",
    PythagoreanRole.REGION_B_LABEL: "b²",
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
_ALTITUDE_STYLE = {
    "stroke": "hsl(var(--chalk-soft))",
    "strokeWidth": 2.5,
    "opacity": 1.0,
    "roughness": 0.25,
    "fill": "transparent",
}
_PARTITION_STYLE = {
    "stroke": "hsl(var(--chalk))",
    "strokeWidth": 4.0,
    "opacity": 1.0,
    "roughness": 0.2,
    "fill": "transparent",
}
_PROOF_CONCLUSION_STYLE = {
    "stroke": "hsl(var(--amber))",
    "strokeWidth": 5.0,
    "opacity": 1.0,
    "roughness": 0.2,
    "fill": "transparent",
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
_REGION_STYLES: dict[PythagoreanRole, dict[str, object]] = {
    PythagoreanRole.REGION_A: {
        "stroke": "hsl(var(--sage))",
        "strokeWidth": 3.0,
        "fill": "#173626",
        "opacity": 1.0,
        "roughness": 0.2,
    },
    PythagoreanRole.REGION_B: {
        "stroke": "hsl(var(--lavender))",
        "strokeWidth": 3.0,
        "fill": "#2E2850",
        "opacity": 1.0,
        "roughness": 0.2,
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


def _line(
    component_id: str,
    role: PythagoreanRole,
    points: tuple[Point, Point],
    style: dict[str, object],
) -> PathSceneNode:
    return PathSceneNode.model_validate(
        {
            "id": _node_id(component_id, role),
            "kind": "path",
            "presentation": _DRAW_PRESENTATION,
            "points": points,
            "closed": False,
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
    hypotenuse_x = _SIDE_B_END[0] - _SIDE_A_END[0]
    hypotenuse_y = _SIDE_B_END[1] - _SIDE_A_END[1]
    from_a_x = _RIGHT_ANGLE[0] - _SIDE_A_END[0]
    from_a_y = _RIGHT_ANGLE[1] - _SIDE_A_END[1]
    projection = (from_a_x * hypotenuse_x + from_a_y * hypotenuse_y) / (
        hypotenuse_x * hypotenuse_x + hypotenuse_y * hypotenuse_y
    )
    altitude_foot = (
        _SIDE_A_END[0] + projection * hypotenuse_x,
        _SIDE_A_END[1] + projection * hypotenuse_y,
    )
    square_offset = (
        square_c[3][0] - square_c[0][0],
        square_c[3][1] - square_c[0][1],
    )
    far_partition = (
        altitude_foot[0] + square_offset[0],
        altitude_foot[1] + square_offset[1],
    )
    region_a = (square_c[0], altitude_foot, far_partition, square_c[3])
    region_b = (altitude_foot, square_c[1], square_c[2], far_partition)

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
        _line(
            component_id,
            PythagoreanRole.ALTITUDE,
            (_RIGHT_ANGLE, altitude_foot),
            _ALTITUDE_STYLE,
        ),
        _line(
            component_id,
            PythagoreanRole.PARTITION,
            (altitude_foot, far_partition),
            _PARTITION_STYLE,
        ),
        _path(
            component_id,
            PythagoreanRole.REGION_A,
            region_a,
            _REGION_STYLES[PythagoreanRole.REGION_A],
        ),
        _label(component_id, PythagoreanRole.REGION_A_LABEL, (498.4, 348.8)),
        _path(
            component_id,
            PythagoreanRole.REGION_B,
            region_b,
            _REGION_STYLES[PythagoreanRole.REGION_B],
        ),
        _label(component_id, PythagoreanRole.REGION_B_LABEL, (438.4, 428.8)),
        TextSceneNode.model_validate(
            {
                "id": _node_id(component_id, PythagoreanRole.PROJECTION_IDENTITY),
                "kind": "text",
                "presentation": _FADE_PRESENTATION,
                "x": 270.0,
                "y": 570.0,
                "text": _PROJECTION_IDENTITY,
                "style": {
                    "color": "hsl(var(--chalk))",
                    "fontSize": 20.0,
                    "opacity": 1.0,
                    "anchor": "middle",
                },
            }
        ),
        _line(
            component_id,
            PythagoreanRole.PROOF_CONCLUSION,
            ((150.0, 112.0), (650.0, 112.0)),
            _PROOF_CONCLUSION_STYLE,
        ),
    )


def _serialize_nodes(nodes: tuple[SceneNode, ...]) -> tuple[dict[str, object], ...]:
    return tuple(node.model_dump(mode="json", by_alias=True) for node in nodes)


def _advance_semantic_scene(
    scene: SemanticSceneState,
    *,
    component_id: str,
    revealed_roles: tuple[PythagoreanRole, ...],
    certificate_head_sha256: str | None,
) -> SemanticSceneState:
    component = PythagoreanAreaIdentityState(
        id=component_id,
        revealed_roles=revealed_roles,
    )
    if any(existing.id == component_id for existing in scene.components):
        components = tuple(
            component if existing.id == component_id else existing for existing in scene.components
        )
    else:
        components = (*scene.components, component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=components,
        certificate_head_sha256=certificate_head_sha256,
    )


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

    atoms: list[CompiledVisualAtom] = []
    current_scene = base_scene
    beat_digest = teaching_beat_sha256(beat)
    for index, role in enumerate(target_roles):
        if index < len(current_roles):
            continue

        atom_id = _atom_id(component_id, role)
        node = target_nodes[index]
        patch = ScenePatchDraft(
            patch_id=atom_id,
            narration=beat.narration,
            operations=(PutSceneOperation(op="put", node=node),),
        )
        receipt = receipts[index]
        next_scene_without_head = _advance_semantic_scene(
            current_scene,
            component_id=component_id,
            revealed_roles=target_roles[: index + 1],
            certificate_head_sha256=None,
        )
        certificate_body = CompilerCertificateBodyV1(
            compiler_version=SEMANTIC_COMPILER_VERSION,
            atom_id=atom_id,
            beat_id=beat.beat_id,
            beat_sha256=beat_digest,
            component_id=component_id,
            role=role,
            node_id=node.id,
            atom_ordinal=index + 1,
            base_semantic_revision=current_scene.revision,
            result_semantic_revision=next_scene_without_head.revision,
            base_scene_sha256=semantic_scene_sha256(current_scene),
            result_scene_sha256=semantic_scene_sha256(next_scene_without_head),
            patch_sha256=scene_patch_sha256(patch),
            receipt_sha256=verification_receipt_sha256(receipt),
            previous_certificate_sha256=current_scene.certificate_head_sha256,
        )
        certificate = CompilerCertificateV1(
            body=certificate_body,
            certificate_sha256=compiler_certificate_sha256(certificate_body),
        )
        atom = CompiledVisualAtom(
            atom_id=atom_id,
            beat_id=beat.beat_id,
            component_id=component_id,
            role=role,
            patch=patch,
            receipt=receipt,
            certificate=certificate,
        )
        atoms.append(atom)
        current_scene = _advance_semantic_scene(
            current_scene,
            component_id=component_id,
            revealed_roles=target_roles[: index + 1],
            certificate_head_sha256=certificate.certificate_sha256,
        )

    return CompiledTeachingBeat(
        beat=beat,
        base_scene=base_scene,
        result_scene=current_scene,
        atoms=tuple(atoms),
    )


compile_pythagorean_area_identity = compile_teaching_beat

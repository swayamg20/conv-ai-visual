"""Certify complete problem-bound Gate 1.6 checkpoint suffixes.

The parametric blueprint compiler authors one exact visual transition at a
time.  This outer layer materializes each transition into full low-level and
semantic scenes, asks the independently authored verifier to check it, and
binds every fact into the V3 certificate chain before returning anything to a
service.  The whole requested suffix is built and validated as one immutable
batch, so a corrupt later checkpoint cannot leak an accepted earlier prefix.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import (
    PresentationCheckpointV1,
    RoutedChoreographyBeatV3,
    choreography_plan_sha256,
    routed_choreography_beat_v3_sha256,
)
from murmur.live_scene.completing_square_contracts import (
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    completing_square_problem_sha256,
)
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LatexSceneNode,
    LatexTokenSceneNode,
    LineSceneNode,
    LiveSceneContract,
    PathSceneNode,
    PutSceneOperation,
    RectSceneNode,
    SceneNode,
    ScenePatchDraft,
    SceneState,
    TextSceneNode,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointCompilerCertificateBodyV3,
    CheckpointCompilerCertificateV3,
    CompiledCheckpointV3,
    checkpoint_certificate_v3_sha256,
    checkpoint_receipt_v3_sha256,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    ParametricCheckpointBlueprint,
    compile_parametric_checkpoint_blueprints,
    materialize_parametric_nodes,
)
from murmur.live_scene.parametric_completing_square_verifier import (
    verify_parametric_completing_square_checkpoint,
    verify_parametric_completing_square_frontier,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1


class ParametricCheckpointCompilationError(ValueError):
    """Raised when a V3 checkpoint suffix cannot be certified atomically."""


Box = tuple[float, float, float, float]
_COLLISION_EPSILON = 1e-6


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
    if isinstance(node, LatexSceneNode):
        # The canvas renderer gives legacy LaTeX one fixed foreignObject.
        return (node.x, node.y, node.x + 500.0, node.y + 120.0)
    if isinstance(node, TextSceneNode):
        # Legacy text is not measured on the wire.  Match the existing
        # semantic verifier's conservative estimate and respect its anchor.
        width = max(48.0, len(node.text) * node.style.font_size * 0.75)
        height = max(36.0, node.style.font_size * 1.25)
        if node.style.anchor == "middle":
            left = node.x - width / 2.0
        elif node.style.anchor == "end":
            left = node.x - width
        else:
            left = node.x
        return (left, node.y - height / 2.0, left + width, node.y + height / 2.0)
    raise ParametricCheckpointCompilationError(
        f"unsupported scene node kind for collision check: {node.kind}"
    )


def _positive_interior_overlap(left: Box, right: Box) -> bool:
    left_width = left[2] - left[0]
    left_height = left[3] - left[1]
    right_width = right[2] - right[0]
    right_height = right[3] - right[1]
    if left_width <= _COLLISION_EPSILON and left_height <= _COLLISION_EPSILON:
        return (
            right[0] + _COLLISION_EPSILON < left[0] < right[2] - _COLLISION_EPSILON
            and right[1] + _COLLISION_EPSILON < left[1] < right[3] - _COLLISION_EPSILON
        )
    if right_width <= _COLLISION_EPSILON and right_height <= _COLLISION_EPSILON:
        return _positive_interior_overlap(right, left)
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > _COLLISION_EPSILON
        and min(left[3], right[3]) - max(left[1], right[1]) > _COLLISION_EPSILON
    )


def _validate_cross_component_collisions(component_id: str, scene: SceneState) -> None:
    prefix = f"{component_id}__"
    owned = tuple(node for node in scene.nodes if node.id.startswith(prefix))
    foreign = tuple(node for node in scene.nodes if not node.id.startswith(prefix))
    for owned_node in owned:
        owned_box = _node_box(owned_node)
        for foreign_node in foreign:
            if _positive_interior_overlap(owned_box, _node_box(foreign_node)):
                raise ParametricCheckpointCompilationError(
                    f"cross-component collision between {owned_node.id!r} and {foreign_node.id!r}"
                )


def _target_component(
    beat: RoutedChoreographyBeatV3,
    scene: SemanticSceneState,
) -> ParametricCompletingSquareStateV1 | None:
    component = next(
        (candidate for candidate in scene.components if candidate.id == beat.component_id),
        None,
    )
    if component is None:
        return None
    if not isinstance(component, ParametricCompletingSquareStateV1):
        raise ParametricCheckpointCompilationError(
            "routed V3 componentId belongs to a different semantic component kind"
        )
    if component.problem_spec != beat.problem_spec:
        raise ParametricCheckpointCompilationError(
            "routed V3 problemSpec does not match the accepted semantic frontier"
        )
    return component


def _validate_base_realization(
    beat: RoutedChoreographyBeatV3,
    component: ParametricCompletingSquareStateV1 | None,
    scene: SceneState,
) -> ParametricCompletingSquareStateV1:
    accepted = component or ParametricCompletingSquareStateV1(
        id=beat.component_id,
        problem_spec=beat.problem_spec,
    )
    expected = materialize_parametric_nodes(accepted)
    prefix = f"{beat.component_id}__"
    actual = tuple(node for node in scene.nodes if node.id.startswith(prefix))
    if actual != expected:
        raise ParametricCheckpointCompilationError(
            "base low-level scene does not match the parametric semantic frontier"
        )
    verify_parametric_completing_square_frontier(accepted, scene)
    _validate_cross_component_collisions(beat.component_id, scene)
    return accepted


def _validate_patch_budget(patch: ScenePatchDraft) -> None:
    if not 1 <= len(patch.operations) <= MAX_PATCH_OPERATIONS:
        raise ParametricCheckpointCompilationError(
            f"parametric checkpoint exceeds the {MAX_PATCH_OPERATIONS}-operation budget"
        )
    size = len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
    if size > MAX_NDJSON_FRAME_BYTES:
        raise ParametricCheckpointCompilationError(
            f"parametric checkpoint exceeds the canonical {MAX_NDJSON_FRAME_BYTES}-byte budget"
        )


def _apply_scene_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
            continue
        if operation.id not in nodes:
            raise ParametricCheckpointCompilationError(
                "parametric checkpoint removes an absent node"
            )
        del nodes[operation.id]
        order.remove(operation.id)
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _advance_semantic_scene(
    scene: SemanticSceneState,
    component: ParametricCompletingSquareStateV1,
    *,
    certificate_head_sha256: str | None,
) -> SemanticSceneState:
    found = False
    components = []
    for existing in scene.components:
        if existing.id == component.id:
            components.append(component)
            found = True
        else:
            components.append(existing)
    if not found:
        components.append(component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=tuple(components),
        certificate_head_sha256=certificate_head_sha256,
    )


def _certify_blueprint(
    beat: RoutedChoreographyBeatV3,
    blueprint: ParametricCheckpointBlueprint,
    *,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> tuple[CompiledCheckpointV3, SceneState, SemanticSceneState]:
    if blueprint.base_component.problem_spec != beat.problem_spec:
        raise ParametricCheckpointCompilationError(
            "blueprint base problemSpec does not match the routed beat"
        )
    if blueprint.result_component.problem_spec != beat.problem_spec:
        raise ParametricCheckpointCompilationError(
            "blueprint result problemSpec does not match the routed beat"
        )
    _validate_patch_budget(blueprint.patch)
    result_scene = _apply_scene_patch(base_scene, blueprint.patch)
    _validate_cross_component_collisions(beat.component_id, result_scene)
    result_semantic_without_head = _advance_semantic_scene(
        base_semantic_scene,
        blueprint.result_component,
        certificate_head_sha256=None,
    )
    receipt = verify_parametric_completing_square_checkpoint(
        beat.component_id,
        beat.problem_spec,
        blueprint.checkpoint_id,
        base_scene,
        result_scene,
        blueprint.patch,
        blueprint.presentation,
        blueprint.choreography,
    )
    problem_digest = completing_square_problem_sha256(beat.problem_spec)
    certificate_body = CheckpointCompilerCertificateBodyV3(
        beat_id=beat.beat_id,
        routed_beat_sha256=routed_choreography_beat_v3_sha256(beat),
        component_id=beat.component_id,
        problem_spec_sha256=problem_digest,
        checkpoint_id=blueprint.checkpoint_id,
        base_revision=base_scene.revision,
        result_revision=result_scene.revision,
        base_low_level_scene_sha256=low_level_scene_sha256(base_scene),
        result_low_level_scene_sha256=low_level_scene_sha256(result_scene),
        base_semantic_scene_sha256=semantic_scene_sha256(base_semantic_scene),
        result_semantic_scene_sha256=semantic_scene_sha256(result_semantic_without_head),
        patch_sha256=scene_patch_sha256(blueprint.patch),
        receipt_sha256=checkpoint_receipt_v3_sha256(receipt),
        presentation_checkpoint=blueprint.presentation,
        choreography_sha256=choreography_plan_sha256(blueprint.choreography),
        previous_certificate_sha256=base_semantic_scene.certificate_head_sha256,
    )
    certificate = CheckpointCompilerCertificateV3(
        body=certificate_body,
        certificate_sha256=checkpoint_certificate_v3_sha256(certificate_body),
    )
    compiled = CompiledCheckpointV3(
        beat=beat,
        checkpoint_id=blueprint.checkpoint_id,
        patch=blueprint.patch,
        receipt=receipt,
        presentation=blueprint.presentation,
        choreography=blueprint.choreography,
        certificate=certificate,
    )
    result_semantic_scene = _advance_semantic_scene(
        base_semantic_scene,
        blueprint.result_component,
        certificate_head_sha256=certificate.certificate_sha256,
    )
    return compiled, result_scene, result_semantic_scene


class CompiledParametricCheckpointBeatV3(LiveSceneContract):
    """One fully preflighted V3 suffix and its exact scene boundaries."""

    beat: RoutedChoreographyBeatV3
    base_scene: SceneState = Field(alias="baseScene")
    result_scene: SceneState = Field(alias="resultScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")
    result_semantic_scene: SemanticSceneState = Field(alias="resultSemanticScene")
    checkpoints: Annotated[
        tuple[CompiledCheckpointV3, ...],
        Field(max_length=MAX_ACCEPTED_PATCHES),
    ] = ()

    @model_validator(mode="after")
    def validate_exact_certified_suffix(self) -> Self:
        count = len(self.checkpoints)
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("base low-level and semantic revisions must match")
        if self.result_scene.revision != self.result_semantic_scene.revision:
            raise ValueError("result low-level and semantic revisions must match")
        if self.result_scene.revision != self.base_scene.revision + count:
            raise ValueError("result revisions must advance exactly once per checkpoint")

        component = _target_component(self.beat, self.base_semantic_scene)
        accepted = _validate_base_realization(self.beat, component, self.base_scene)
        blueprints = compile_parametric_checkpoint_blueprints(self.beat, component)
        expected_ids = tuple(item.checkpoint_id for item in blueprints.checkpoints)
        if tuple(item.checkpoint_id for item in self.checkpoints) != expected_ids:
            raise ValueError("checkpoints must be the routed V3 beat's exact missing suffix")
        if blueprints.base_component != accepted:
            raise ValueError("blueprint base component must equal the accepted V3 frontier")

        if not self.checkpoints:
            if self.result_scene != self.base_scene:
                raise ValueError("an empty V3 checkpoint suffix must preserve the low-level scene")
            if self.result_semantic_scene != self.base_semantic_scene:
                raise ValueError("an empty V3 checkpoint suffix must preserve the semantic scene")
            return self

        current_scene = self.base_scene
        current_semantic_scene = self.base_semantic_scene
        previous_presentation: PresentationCheckpointV1 | None = None
        for actual, blueprint in zip(
            self.checkpoints,
            blueprints.checkpoints,
            strict=True,
        ):
            if actual.beat != self.beat:
                raise ValueError("every V3 checkpoint must bind the batch routed beat")
            if (
                previous_presentation is not None
                and actual.presentation.base_viewports != previous_presentation.result_viewports
            ):
                raise ValueError("V3 checkpoint viewport transitions must join exactly")
            expected, next_scene, next_semantic_scene = _certify_blueprint(
                self.beat,
                blueprint,
                base_scene=current_scene,
                base_semantic_scene=current_semantic_scene,
            )
            if actual != expected:
                raise ValueError(
                    "V3 checkpoint must equal the independently verified compiler claim"
                )
            current_scene = next_scene
            current_semantic_scene = next_semantic_scene
            previous_presentation = actual.presentation

        if current_scene != self.result_scene:
            raise ValueError("last V3 checkpoint must materialize the exact result scene")
        if current_semantic_scene != self.result_semantic_scene:
            raise ValueError("last V3 checkpoint must materialize the exact semantic frontier")
        if blueprints.result_component != _target_component(
            self.beat,
            self.result_semantic_scene,
        ):
            raise ValueError("result semantic frontier must equal the compiled V3 target")
        if (
            self.result_semantic_scene.certificate_head_sha256
            != self.checkpoints[-1].certificate.certificate_sha256
        ):
            raise ValueError("result semantic chain head must equal the last V3 certificate")
        return self


def compile_parametric_checkpoint_beat(
    beat: RoutedChoreographyBeatV3,
    *,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> CompiledParametricCheckpointBeatV3:
    """Compile, independently verify, and certify one complete V3 suffix."""

    if base_scene.revision != base_semantic_scene.revision:
        raise ParametricCheckpointCompilationError(
            "base low-level and semantic revisions must match"
        )
    component = _target_component(beat, base_semantic_scene)
    accepted = _validate_base_realization(beat, component, base_scene)
    blueprints = compile_parametric_checkpoint_blueprints(beat, component)
    if blueprints.base_component != accepted:
        raise ParametricCheckpointCompilationError(
            "compiled blueprint base does not equal the accepted semantic frontier"
        )

    current_scene = base_scene
    current_semantic_scene = base_semantic_scene
    checkpoints: list[CompiledCheckpointV3] = []
    for blueprint in blueprints.checkpoints:
        checkpoint, current_scene, current_semantic_scene = _certify_blueprint(
            beat,
            blueprint,
            base_scene=current_scene,
            base_semantic_scene=current_semantic_scene,
        )
        checkpoints.append(checkpoint)

    return CompiledParametricCheckpointBeatV3(
        beat=beat,
        base_scene=base_scene,
        result_scene=current_scene,
        base_semantic_scene=base_semantic_scene,
        result_semantic_scene=current_semantic_scene,
        checkpoints=tuple(checkpoints),
    )


__all__ = [
    "CompiledParametricCheckpointBeatV3",
    "ParametricCheckpointCompilationError",
    "compile_parametric_checkpoint_beat",
]

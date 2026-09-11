"""Atomically verify and certify Gate 1.7 projectile checkpoint suffixes.

The deterministic projectile compiler authors candidate blueprints.  This
boundary applies each candidate to the exact accepted scenes, invokes the
independent verifier, and builds a hash-linked immutable batch.  Nothing is
returned until the entire requested suffix has passed every check.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import (
    PresentationCheckpointV1,
    choreography_plan_v2_sha256,
)
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    MAX_NDJSON_FRAME_BYTES,
    MAX_PATCH_OPERATIONS,
    LiveSceneContract,
    PutSceneOperation,
    SceneNode,
    ScenePatchDraft,
    SceneState,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
    CompiledProjectileMotionCheckpointV1,
    ProjectileMotionCheckpointAction,
    ProjectileMotionCheckpointCompilerCertificateBodyV1,
    ProjectileMotionCheckpointCompilerCertificateV1,
    ProjectileMotionCheckpointVerificationReceiptV1,
    ProjectileMotionVerificationObligation,
    projectile_motion_checkpoint_certificate_sha256,
    projectile_motion_checkpoint_receipt_sha256,
)
from murmur.live_scene.projectile_motion_compiler import (
    ProjectileMotionCheckpointBlueprint,
    compile_projectile_motion_checkpoint_blueprints,
    materialize_projectile_motion_nodes,
)
from murmur.live_scene.projectile_motion_contracts import (
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionClarificationTopic,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    projectile_motion_problem_sha256,
    routed_projectile_motion_beat_sha256,
)
from murmur.live_scene.projectile_motion_verifier import (
    ProjectileMotionVerificationCode,
    verify_projectile_motion_checkpoint,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1


class ProjectileMotionCheckpointCompilationError(ValueError):
    """Raised before any partially certified projectile suffix can escape."""


_VERIFIER_OBLIGATIONS = tuple(
    ProjectileMotionVerificationObligation(code.value) for code in ProjectileMotionVerificationCode
)
if _VERIFIER_OBLIGATIONS != PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS:
    raise RuntimeError("projectile verifier and receipt obligation vocabularies diverged")


def _find_component(
    component_id: str,
    scene: SemanticSceneState,
) -> ProjectileMotionStateV1 | None:
    component = next(
        (candidate for candidate in scene.components if candidate.id == component_id),
        None,
    )
    if component is None:
        return None
    if not isinstance(component, ProjectileMotionStateV1):
        raise ProjectileMotionCheckpointCompilationError(
            "routed projectile componentId belongs to a different semantic component kind"
        )
    return component


def _target_component(
    beat: RoutedProjectileMotionBeatV1,
    scene: SemanticSceneState,
) -> ProjectileMotionStateV1 | None:
    component = _find_component(beat.component_id, scene)
    if component is None:
        if beat.base_problem_spec is not None:
            raise ProjectileMotionCheckpointCompilationError(
                "a non-empty routed beat requires an accepted projectile component"
            )
        return None
    if beat.base_problem_spec is None:
        raise ProjectileMotionCheckpointCompilationError(
            "a fresh routed beat requires an empty projectile frontier"
        )
    if component.problem_spec != beat.base_problem_spec:
        raise ProjectileMotionCheckpointCompilationError(
            "routed baseProblemSpec does not match the accepted projectile frontier"
        )
    return component


def _owned_node_map(component_id: str, nodes: tuple[SceneNode, ...]) -> dict[str, SceneNode]:
    prefix = f"{component_id}__"
    return {node.id: node for node in nodes if node.id.startswith(prefix)}


def _validate_base_realization(
    beat: RoutedProjectileMotionBeatV1,
    component: ProjectileMotionStateV1 | None,
    scene: SceneState,
) -> ProjectileMotionStateV1:
    accepted = component or ProjectileMotionStateV1(
        id=beat.component_id,
        problem_spec=beat.result_problem_spec,
    )
    expected = {node.id: node for node in materialize_projectile_motion_nodes(accepted)}
    actual = _owned_node_map(beat.component_id, scene.nodes)
    if actual != expected:
        raise ProjectileMotionCheckpointCompilationError(
            "base low-level scene does not match the projectile semantic frontier"
        )
    return accepted


def _validate_patch_budget(patch: ScenePatchDraft) -> None:
    if not 1 <= len(patch.operations) <= MAX_PATCH_OPERATIONS:
        raise ProjectileMotionCheckpointCompilationError(
            f"projectile checkpoint exceeds the {MAX_PATCH_OPERATIONS}-operation budget"
        )
    size = len(canonical_json_v1(patch.model_dump(mode="json", by_alias=True)))
    if size > MAX_NDJSON_FRAME_BYTES:
        raise ProjectileMotionCheckpointCompilationError(
            f"projectile checkpoint exceeds the canonical {MAX_NDJSON_FRAME_BYTES}-byte budget"
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
            raise ProjectileMotionCheckpointCompilationError(
                "projectile checkpoint removes an absent node"
            )
        del nodes[operation.id]
        order.remove(operation.id)
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _advance_semantic_scene(
    scene: SemanticSceneState,
    component: ProjectileMotionStateV1,
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


def _action_and_topic(
    beat: RoutedProjectileMotionBeatV1,
) -> tuple[ProjectileMotionCheckpointAction, ProjectileMotionClarificationTopic | None]:
    if isinstance(beat.route, AdvanceProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.ADVANCE, None
    if isinstance(beat.route, ClarifyProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.CLARIFY, beat.route.topic
    if isinstance(beat.route, RetargetProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.RETARGET, None
    raise ProjectileMotionCheckpointCompilationError("unsupported projectile route")


def _certify_blueprint(
    beat: RoutedProjectileMotionBeatV1,
    blueprint: ProjectileMotionCheckpointBlueprint,
    *,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
    allow_empty_start: bool,
) -> tuple[
    CompiledProjectileMotionCheckpointV1,
    SceneState,
    SemanticSceneState,
]:
    accepted_component = _find_component(beat.component_id, base_semantic_scene)
    if accepted_component is None:
        if not allow_empty_start:
            raise ProjectileMotionCheckpointCompilationError(
                "only the first fresh setup checkpoint may omit a base component"
            )
    elif accepted_component != blueprint.base_component:
        raise ProjectileMotionCheckpointCompilationError(
            "blueprint base component does not match the accepted semantic frontier"
        )

    actual_base = _owned_node_map(beat.component_id, base_scene.nodes)
    blueprint_base = {node.id: node for node in blueprint.base_nodes}
    if actual_base != blueprint_base:
        raise ProjectileMotionCheckpointCompilationError(
            "blueprint base nodes do not match the accepted low-level scene"
        )

    _validate_patch_budget(blueprint.patch)
    result_scene = _apply_scene_patch(base_scene, blueprint.patch)
    actual_result = _owned_node_map(beat.component_id, result_scene.nodes)
    blueprint_result = {node.id: node for node in blueprint.result_nodes}
    if actual_result != blueprint_result:
        raise ProjectileMotionCheckpointCompilationError(
            "projectile patch does not materialize the blueprint result"
        )

    # This call is intentionally after independent materialization and before
    # any receipt or externally visible batch exists.
    verify_projectile_motion_checkpoint(blueprint)

    action, topic = _action_and_topic(beat)
    result_problem_digest = projectile_motion_problem_sha256(
        blueprint.result_component.problem_spec
    )
    base_problem_digest = (
        None
        if allow_empty_start
        else projectile_motion_problem_sha256(blueprint.base_component.problem_spec)
    )
    receipt = ProjectileMotionCheckpointVerificationReceiptV1(
        component_id=beat.component_id,
        action=action,
        checkpoint_id=blueprint.checkpoint_id,
        clarification_topic=topic,
        base_problem_spec_sha256=base_problem_digest,
        result_problem_spec_sha256=result_problem_digest,
        operation_targets=tuple(operation.target_id for operation in blueprint.patch.operations),
        obligation_codes=_VERIFIER_OBLIGATIONS,
    )

    result_semantic_without_head = _advance_semantic_scene(
        base_semantic_scene,
        blueprint.result_component,
        certificate_head_sha256=None,
    )
    certificate_body = ProjectileMotionCheckpointCompilerCertificateBodyV1(
        beat_id=beat.beat_id,
        routed_beat_sha256=routed_projectile_motion_beat_sha256(beat),
        component_id=beat.component_id,
        action=action,
        checkpoint_id=blueprint.checkpoint_id,
        clarification_topic=topic,
        base_problem_spec_sha256=base_problem_digest,
        result_problem_spec_sha256=result_problem_digest,
        base_low_level_revision=base_scene.revision,
        result_low_level_revision=result_scene.revision,
        base_semantic_revision=base_semantic_scene.revision,
        result_semantic_revision=result_semantic_without_head.revision,
        base_low_level_scene_sha256=low_level_scene_sha256(base_scene),
        result_low_level_scene_sha256=low_level_scene_sha256(result_scene),
        base_semantic_scene_sha256=semantic_scene_sha256(base_semantic_scene),
        result_semantic_scene_sha256=semantic_scene_sha256(result_semantic_without_head),
        patch_sha256=scene_patch_sha256(blueprint.patch),
        receipt_sha256=projectile_motion_checkpoint_receipt_sha256(receipt),
        presentation_checkpoint=blueprint.presentation,
        choreography_sha256=choreography_plan_v2_sha256(blueprint.choreography),
        previous_certificate_sha256=base_semantic_scene.certificate_head_sha256,
    )
    certificate = ProjectileMotionCheckpointCompilerCertificateV1(
        body=certificate_body,
        certificate_sha256=projectile_motion_checkpoint_certificate_sha256(certificate_body),
    )
    compiled = CompiledProjectileMotionCheckpointV1(
        beat=beat,
        action=action,
        checkpoint_id=blueprint.checkpoint_id,
        clarification_topic=topic,
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


class CompiledProjectileMotionCheckpointBeatV1(LiveSceneContract):
    """One fully preflighted projectile suffix and its exact boundaries."""

    beat: RoutedProjectileMotionBeatV1
    base_scene: SceneState = Field(alias="baseScene")
    result_scene: SceneState = Field(alias="resultScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")
    result_semantic_scene: SemanticSceneState = Field(alias="resultSemanticScene")
    checkpoints: Annotated[
        tuple[CompiledProjectileMotionCheckpointV1, ...],
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
        blueprints = compile_projectile_motion_checkpoint_blueprints(
            self.beat,
            component,
        )
        expected_ids = tuple(item.checkpoint_id for item in blueprints.checkpoints)
        if tuple(item.checkpoint_id for item in self.checkpoints) != expected_ids:
            raise ValueError("checkpoints must be the routed beat's exact missing suffix")
        if blueprints.base_component != accepted:
            raise ValueError("blueprint base component must equal the accepted frontier")

        if not self.checkpoints:
            if self.result_scene != self.base_scene:
                raise ValueError("an empty checkpoint suffix must preserve the low-level scene")
            if self.result_semantic_scene != self.base_semantic_scene:
                raise ValueError("an empty checkpoint suffix must preserve the semantic scene")
            return self

        current_scene = self.base_scene
        current_semantic_scene = self.base_semantic_scene
        previous_presentation: PresentationCheckpointV1 | None = None
        for index, (actual, blueprint) in enumerate(
            zip(self.checkpoints, blueprints.checkpoints, strict=True)
        ):
            if actual.beat != self.beat:
                raise ValueError("every checkpoint must bind the batch routed beat")
            if (
                previous_presentation is not None
                and actual.presentation.base_viewports != previous_presentation.result_viewports
            ):
                raise ValueError("projectile checkpoint viewport transitions must join exactly")
            expected, next_scene, next_semantic_scene = _certify_blueprint(
                self.beat,
                blueprint,
                base_scene=current_scene,
                base_semantic_scene=current_semantic_scene,
                allow_empty_start=component is None and index == 0,
            )
            if actual != expected:
                raise ValueError("checkpoint must equal the independently verified compiler claim")
            current_scene = next_scene
            current_semantic_scene = next_semantic_scene
            previous_presentation = actual.presentation

        if current_scene != self.result_scene:
            raise ValueError("last checkpoint must materialize the exact result scene")
        if current_semantic_scene != self.result_semantic_scene:
            raise ValueError("last checkpoint must materialize the exact semantic frontier")
        result_component = _find_component(
            self.beat.component_id,
            self.result_semantic_scene,
        )
        if blueprints.result_component != result_component:
            raise ValueError("result semantic frontier must equal the compiled target")
        if (
            self.result_semantic_scene.certificate_head_sha256
            != self.checkpoints[-1].certificate.certificate_sha256
        ):
            raise ValueError("result semantic chain head must equal the last certificate")
        return self


def compile_projectile_motion_checkpoint_beat(
    beat: RoutedProjectileMotionBeatV1,
    *,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> CompiledProjectileMotionCheckpointBeatV1:
    """Compile and certify a whole projectile suffix as one transaction."""

    if base_scene.revision != base_semantic_scene.revision:
        raise ProjectileMotionCheckpointCompilationError(
            "base low-level and semantic revisions must match"
        )
    component = _target_component(beat, base_semantic_scene)
    accepted = _validate_base_realization(beat, component, base_scene)
    blueprints = compile_projectile_motion_checkpoint_blueprints(beat, component)
    if blueprints.base_component != accepted:
        raise ProjectileMotionCheckpointCompilationError(
            "compiled blueprint base does not equal the accepted semantic frontier"
        )

    current_scene = base_scene
    current_semantic_scene = base_semantic_scene
    checkpoints: list[CompiledProjectileMotionCheckpointV1] = []
    for index, blueprint in enumerate(blueprints.checkpoints):
        checkpoint, current_scene, current_semantic_scene = _certify_blueprint(
            beat,
            blueprint,
            base_scene=current_scene,
            base_semantic_scene=current_semantic_scene,
            allow_empty_start=component is None and index == 0,
        )
        checkpoints.append(checkpoint)

    # Pydantic reconstructs and independently revalidates the full chain here;
    # callers receive either this complete immutable batch or an exception.
    return CompiledProjectileMotionCheckpointBeatV1(
        beat=beat,
        base_scene=base_scene,
        result_scene=current_scene,
        base_semantic_scene=base_semantic_scene,
        result_semantic_scene=current_semantic_scene,
        checkpoints=tuple(checkpoints),
    )


__all__ = [
    "CompiledProjectileMotionCheckpointBeatV1",
    "ProjectileMotionCheckpointCompilationError",
    "compile_projectile_motion_checkpoint_beat",
]

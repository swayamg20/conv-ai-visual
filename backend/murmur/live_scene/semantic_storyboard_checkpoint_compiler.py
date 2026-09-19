"""Atomically compile, verify, and certify one Gate 1.8 storyboard checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import choreography_plan_v2_sha256
from murmur.live_scene.contracts import (
    LiveSceneContract,
    PutSceneOperation,
    ScenePatchDraft,
    SceneState,
)
from murmur.live_scene.semantic_contracts import scene_patch_sha256
from murmur.live_scene.semantic_storyboard_checkpoint_contracts import (
    CompiledSemanticStoryboardCheckpointV1,
    SemanticStoryboardCheckpointCompilerCertificateBodyV1,
    SemanticStoryboardCheckpointCompilerCertificateV1,
    SemanticStoryboardCheckpointOrigin,
    SemanticStoryboardCheckpointVerificationReceiptV1,
    semantic_storyboard_checkpoint_certificate_sha256,
    semantic_storyboard_checkpoint_receipt_sha256,
)
from murmur.live_scene.semantic_storyboard_compiler import (
    SemanticStoryboardCheckpointBlueprint,
    compile_semantic_storyboard_anchor,
    compile_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RoutedSemanticStoryboardBeatV1,
    paired_projectile_comparison_sha256,
    routed_semantic_storyboard_beat_sha256,
    semantic_storyboard_frontier_sha256,
    semantic_storyboard_program_sha256,
    semantic_storyboard_record_sha256,
)
from murmur.live_scene.semantic_storyboard_verifier import (
    SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS,
    SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS,
    verify_semantic_storyboard_anchor,
    verify_semantic_storyboard_checkpoint,
    verify_semantic_storyboard_frontier,
)


class SemanticStoryboardCheckpointCompilationError(ValueError):
    """Raised before an invalid or non-atomic transition can escape."""


@dataclass(frozen=True, slots=True)
class _CertifiedTransition:
    checkpoint: CompiledSemanticStoryboardCheckpointV1
    result_scene: SceneState
    result_semantic_scene: ProjectileStoryboardSemanticSceneStateV1


def _apply_scene_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.target_id not in nodes:
                order.append(operation.target_id)
            nodes[operation.target_id] = operation.node
            continue
        if operation.target_id not in nodes:
            raise SemanticStoryboardCheckpointCompilationError(
                "storyboard checkpoint removes an absent node"
            )
        del nodes[operation.target_id]
        order.remove(operation.target_id)
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _materialize_result_scene(
    blueprint: SemanticStoryboardCheckpointBlueprint,
    base_scene: SceneState,
) -> SceneState:
    if base_scene.nodes != blueprint.base_nodes:
        raise SemanticStoryboardCheckpointCompilationError(
            "accepted low-level frontier does not match the compiled base"
        )
    result_scene = _apply_scene_patch(base_scene, blueprint.patch)
    if result_scene.nodes != blueprint.result_nodes:
        raise SemanticStoryboardCheckpointCompilationError(
            "storyboard patch does not materialize the compiled result"
        )
    return result_scene


def _certificate(
    *,
    origin: SemanticStoryboardCheckpointOrigin,
    problem_spec: PairedProjectileComparisonSpecV1,
    beat: RoutedSemanticStoryboardBeatV1 | None,
    blueprint: SemanticStoryboardCheckpointBlueprint,
    receipt: SemanticStoryboardCheckpointVerificationReceiptV1,
    base_scene: SceneState,
    result_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
    result_component: ProjectileStoryboardStateV1,
    base_program_sha256: str,
    result_program_sha256: str,
) -> SemanticStoryboardCheckpointCompilerCertificateV1:
    body = SemanticStoryboardCheckpointCompilerCertificateBodyV1(
        checkpointOrigin=origin,
        beatId=None if beat is None else beat.beat_id,
        routedBeatSha256=(None if beat is None else routed_semantic_storyboard_beat_sha256(beat)),
        recordSha256=None if beat is None else semantic_storyboard_record_sha256(beat.record),
        checkpointId=blueprint.checkpoint_id,
        problemSpecSha256=paired_projectile_comparison_sha256(problem_spec),
        baseProgramSha256=base_program_sha256,
        resultProgramSha256=result_program_sha256,
        baseLowLevelRevision=base_scene.revision,
        resultLowLevelRevision=result_scene.revision,
        baseSemanticRevision=base_semantic_scene.revision,
        resultSemanticRevision=base_semantic_scene.revision + 1,
        baseLowLevelSceneSha256=low_level_scene_sha256(base_scene),
        resultLowLevelSceneSha256=low_level_scene_sha256(result_scene),
        baseSemanticSceneSha256=semantic_storyboard_frontier_sha256(
            base_semantic_scene.revision,
            base_semantic_scene.components,
        ),
        resultSemanticSceneSha256=semantic_storyboard_frontier_sha256(
            base_semantic_scene.revision + 1,
            (result_component,),
        ),
        patchSha256=scene_patch_sha256(blueprint.patch),
        receiptSha256=semantic_storyboard_checkpoint_receipt_sha256(receipt),
        presentationCheckpoint=blueprint.presentation,
        choreographySha256=choreography_plan_v2_sha256(blueprint.choreography),
        previousCertificateSha256=base_semantic_scene.certificate_head_sha256,
    )
    return SemanticStoryboardCheckpointCompilerCertificateV1(
        body=body,
        certificateSha256=semantic_storyboard_checkpoint_certificate_sha256(body),
    )


def _compiled_checkpoint(
    *,
    origin: SemanticStoryboardCheckpointOrigin,
    problem_spec: PairedProjectileComparisonSpecV1,
    beat: RoutedSemanticStoryboardBeatV1 | None,
    blueprint: SemanticStoryboardCheckpointBlueprint,
    base_scene: SceneState,
    result_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> _CertifiedTransition:
    if beat is None:
        base_program_hash = result_program_hash = semantic_storyboard_program_sha256(
            problem_spec,
            (),
        )
        semantic_effect = None
        obligations = SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS
        routed_digest = None
    else:
        base_component = base_semantic_scene.components[0]
        result_records = (*base_component.accepted_records, beat.record)
        base_program_hash = semantic_storyboard_program_sha256(
            problem_spec,
            base_component.accepted_records,
        )
        result_program_hash = semantic_storyboard_program_sha256(problem_spec, result_records)
        semantic_effect = beat.semantic_effect
        obligations = SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
        routed_digest = routed_semantic_storyboard_beat_sha256(beat)

    receipt = SemanticStoryboardCheckpointVerificationReceiptV1(
        checkpointOrigin=origin,
        checkpointId=blueprint.checkpoint_id,
        problemSpecSha256=paired_projectile_comparison_sha256(problem_spec),
        routedBeatSha256=routed_digest,
        baseProgramSha256=base_program_hash,
        resultProgramSha256=result_program_hash,
        semanticEffect=semantic_effect,
        operationTargets=tuple(operation.target_id for operation in blueprint.patch.operations),
        obligationCodes=obligations,
    )
    certificate = _certificate(
        origin=origin,
        problem_spec=problem_spec,
        beat=beat,
        blueprint=blueprint,
        receipt=receipt,
        base_scene=base_scene,
        result_scene=result_scene,
        base_semantic_scene=base_semantic_scene,
        result_component=blueprint.result_component,
        base_program_sha256=base_program_hash,
        result_program_sha256=result_program_hash,
    )
    checkpoint = CompiledSemanticStoryboardCheckpointV1(
        checkpointOrigin=origin,
        problemSpec=problem_spec,
        beat=beat,
        checkpointId=blueprint.checkpoint_id,
        patch=blueprint.patch,
        receipt=receipt,
        presentation=blueprint.presentation,
        choreography=blueprint.choreography,
        certificate=certificate,
    )
    result_semantic_scene = ProjectileStoryboardSemanticSceneStateV1(
        revision=base_semantic_scene.revision + 1,
        components=(blueprint.result_component,),
        certificateHeadSha256=certificate.certificate_sha256,
    )
    verify_semantic_storyboard_frontier(problem_spec, result_scene, result_semantic_scene)
    return _CertifiedTransition(
        checkpoint=checkpoint,
        result_scene=result_scene,
        result_semantic_scene=result_semantic_scene,
    )


def _certify_anchor(
    problem_spec: PairedProjectileComparisonSpecV1,
    *,
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> _CertifiedTransition:
    verify_semantic_storyboard_frontier(problem_spec, base_scene, base_semantic_scene)
    if base_scene != SceneState(revision=0) or base_semantic_scene != (
        ProjectileStoryboardSemanticSceneStateV1(revision=0)
    ):
        raise SemanticStoryboardCheckpointCompilationError(
            "storyboard anchor requires an exact empty revision-0 frontier"
        )
    blueprint = compile_semantic_storyboard_anchor(problem_spec)
    result_scene = _materialize_result_scene(blueprint, base_scene)

    # The verifier is authoritative for acceptance, but its return value is not
    # accepted as receipt material. Receipt fields are derived above from the
    # canonical problem, transition, patch, and closed obligation vocabulary.
    verify_semantic_storyboard_anchor(problem_spec, blueprint)
    return _compiled_checkpoint(
        origin=SemanticStoryboardCheckpointOrigin.ANCHOR,
        problem_spec=problem_spec,
        beat=None,
        blueprint=blueprint,
        base_scene=base_scene,
        result_scene=result_scene,
        base_semantic_scene=base_semantic_scene,
    )


def _certify_model_checkpoint(
    beat: RoutedSemanticStoryboardBeatV1,
    *,
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> _CertifiedTransition:
    verify_semantic_storyboard_frontier(beat.problem_spec, base_scene, base_semantic_scene)
    if len(base_semantic_scene.components) != 1:
        raise SemanticStoryboardCheckpointCompilationError(
            "a model checkpoint requires one accepted storyboard component"
        )
    if beat.previous_certificate_sha256 != base_semantic_scene.certificate_head_sha256:
        raise SemanticStoryboardCheckpointCompilationError(
            "routed beat does not join the accepted certificate head"
        )
    base_component = base_semantic_scene.components[0]
    blueprint = compile_semantic_storyboard_checkpoint(beat, base_component)
    if blueprint.base_component != base_component:
        raise SemanticStoryboardCheckpointCompilationError(
            "compiled base component does not match the accepted semantic frontier"
        )
    result_scene = _materialize_result_scene(blueprint, base_scene)
    verify_semantic_storyboard_checkpoint(
        beat,
        blueprint,
        base_semantic_scene=base_semantic_scene,
    )
    return _compiled_checkpoint(
        origin=SemanticStoryboardCheckpointOrigin.MODEL_RECORD,
        problem_spec=beat.problem_spec,
        beat=beat,
        blueprint=blueprint,
        base_scene=base_scene,
        result_scene=result_scene,
        base_semantic_scene=base_semantic_scene,
    )


class ValidatedSemanticStoryboardTransitionV1(LiveSceneContract):
    """One exact low-level, semantic, verifier, and certificate transition."""

    base_scene: SceneState = Field(alias="baseScene")
    result_scene: SceneState = Field(alias="resultScene")
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1 = Field(alias="baseSemanticScene")
    result_semantic_scene: ProjectileStoryboardSemanticSceneStateV1 = Field(
        alias="resultSemanticScene"
    )
    checkpoint: CompiledSemanticStoryboardCheckpointV1

    @model_validator(mode="after")
    def validate_exact_transition(self) -> Self:
        artifact = self.checkpoint
        if artifact.checkpoint_origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
            expected = _certify_anchor(
                artifact.problem_spec,
                base_scene=self.base_scene,
                base_semantic_scene=self.base_semantic_scene,
            )
        else:
            if artifact.beat is None:  # Defensive; the nested contract normally rejects this.
                raise ValueError("model transition requires a routed beat")
            expected = _certify_model_checkpoint(
                artifact.beat,
                base_scene=self.base_scene,
                base_semantic_scene=self.base_semantic_scene,
            )
        if artifact != expected.checkpoint:
            raise ValueError("checkpoint must equal the regenerated verified compiler claim")
        if self.result_scene != expected.result_scene:
            raise ValueError("checkpoint must materialize the exact result low-level scene")
        if self.result_semantic_scene != expected.result_semantic_scene:
            raise ValueError("checkpoint must materialize the exact certified semantic frontier")
        return self


VALIDATED_SEMANTIC_STORYBOARD_TRANSITION_V1_ADAPTER = TypeAdapter(
    ValidatedSemanticStoryboardTransitionV1
)


def compile_certified_semantic_storyboard_anchor(
    problem_spec: PairedProjectileComparisonSpecV1,
    *,
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> ValidatedSemanticStoryboardTransitionV1:
    """Compile and publish only one completely certified provider-free anchor."""

    if not isinstance(problem_spec, PairedProjectileComparisonSpecV1):
        raise TypeError("problem_spec must be a PairedProjectileComparisonSpecV1")
    if not isinstance(base_scene, SceneState):
        raise TypeError("base_scene must be a SceneState")
    if not isinstance(base_semantic_scene, ProjectileStoryboardSemanticSceneStateV1):
        raise TypeError("base_semantic_scene must be a ProjectileStoryboardSemanticSceneStateV1")
    certified = _certify_anchor(
        problem_spec,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    return ValidatedSemanticStoryboardTransitionV1(
        baseScene=base_scene,
        resultScene=certified.result_scene,
        baseSemanticScene=base_semantic_scene,
        resultSemanticScene=certified.result_semantic_scene,
        checkpoint=certified.checkpoint,
    )


def compile_certified_semantic_storyboard_checkpoint(
    beat: RoutedSemanticStoryboardBeatV1,
    *,
    base_scene: SceneState,
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> ValidatedSemanticStoryboardTransitionV1:
    """Compile and publish exactly one fully certified model-selected record."""

    if not isinstance(beat, RoutedSemanticStoryboardBeatV1):
        raise TypeError("beat must be a RoutedSemanticStoryboardBeatV1")
    if not isinstance(base_scene, SceneState):
        raise TypeError("base_scene must be a SceneState")
    if not isinstance(base_semantic_scene, ProjectileStoryboardSemanticSceneStateV1):
        raise TypeError("base_semantic_scene must be a ProjectileStoryboardSemanticSceneStateV1")
    certified = _certify_model_checkpoint(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    return ValidatedSemanticStoryboardTransitionV1(
        baseScene=base_scene,
        resultScene=certified.result_scene,
        baseSemanticScene=base_semantic_scene,
        resultSemanticScene=certified.result_semantic_scene,
        checkpoint=certified.checkpoint,
    )


__all__ = [
    "VALIDATED_SEMANTIC_STORYBOARD_TRANSITION_V1_ADAPTER",
    "SemanticStoryboardCheckpointCompilationError",
    "ValidatedSemanticStoryboardTransitionV1",
    "compile_certified_semantic_storyboard_anchor",
    "compile_certified_semantic_storyboard_checkpoint",
]

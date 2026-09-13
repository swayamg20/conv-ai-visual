"""Integrity contracts for one verified Gate 1.8 storyboard checkpoint."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyId,
    ChoreographyPlanV2,
    PresentationCheckpointV1,
    choreography_plan_v2_sha256,
)
from murmur.live_scene.contracts import (
    MAX_PATCH_OPERATIONS,
    LiveSceneContract,
    NonNegativeRevision,
    PositiveRevision,
    SceneNodeId,
    ScenePatchDraft,
)
from murmur.live_scene.semantic_contracts import Sha256Digest, scene_patch_sha256
from murmur.live_scene.semantic_integrity import (
    SEMANTIC_CANONICALIZATION,
    SEMANTIC_HASH_ALGORITHM,
    canonical_sha256,
    digest_matches,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PROJECTILE_STORYBOARD_COMPONENT_ID,
    SEMANTIC_STORYBOARD_CATALOG_VERSION,
    PairedProjectileComparisonSpecV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardSemanticEffectClosureV1,
    paired_projectile_comparison_sha256,
    routed_semantic_storyboard_beat_sha256,
    semantic_storyboard_program_sha256,
    semantic_storyboard_record_sha256,
    storyboard_record_slug,
)
from murmur.live_scene.semantic_storyboard_verifier import (
    SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS,
    SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS,
    SemanticStoryboardVerificationObligation,
)

SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION: Final = 1
SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION: Final = (
    "murmur.semantic_storyboard_choreography.v1"
)
SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID: Final = "storyboard-anchor"
SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN: Final = (
    "murmur:semantic-storyboard-checkpoint-receipt:v1"
)
SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN: Final = (
    "murmur:semantic-storyboard-checkpoint-certificate:v1"
)

StoryboardOperationTargets = Annotated[
    tuple[SceneNodeId, ...],
    Field(min_length=1, max_length=MAX_PATCH_OPERATIONS),
]


class SemanticStoryboardCheckpointOrigin(StrEnum):
    """Whether a checkpoint is the server genesis or one model-selected record."""

    ANCHOR = "anchor"
    MODEL_RECORD = "model_record"


class SemanticStoryboardCheckpointVerificationReceiptV1(LiveSceneContract):
    """Exact independent-verifier claim for one atomic storyboard checkpoint."""

    issuer: Literal["semantic_storyboard_verifier"] = "semantic_storyboard_verifier"
    checkpoint_origin: SemanticStoryboardCheckpointOrigin = Field(alias="checkpointOrigin")
    component_kind: Literal["projectile_comparison_storyboard"] = Field(
        default="projectile_comparison_storyboard",
        alias="componentKind",
    )
    component_id: Literal[PROJECTILE_STORYBOARD_COMPONENT_ID] = Field(
        default=PROJECTILE_STORYBOARD_COMPONENT_ID,
        alias="componentId",
    )
    checkpoint_id: ChoreographyId = Field(alias="checkpointId")
    problem_spec_sha256: Sha256Digest = Field(alias="problemSpecSha256")
    routed_beat_sha256: Sha256Digest | None = Field(alias="routedBeatSha256")
    base_program_sha256: Sha256Digest = Field(alias="baseProgramSha256")
    result_program_sha256: Sha256Digest = Field(alias="resultProgramSha256")
    semantic_effect: StoryboardSemanticEffectClosureV1 | None = Field(alias="semanticEffect")
    operation_targets: StoryboardOperationTargets = Field(alias="operationTargets")
    obligation_codes: Annotated[
        tuple[SemanticStoryboardVerificationObligation, ...],
        Field(min_length=1),
    ] = Field(alias="obligationCodes")
    verified: Literal[True] = True

    @field_validator("verified", mode="before")
    @classmethod
    def validate_strict_verified(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("verified must be a strict boolean")
        return value

    @model_validator(mode="after")
    def validate_origin_claim(self) -> Self:
        if len(self.operation_targets) != len(set(self.operation_targets)):
            raise ValueError("storyboard receipt operationTargets must be unique")
        if self.checkpoint_origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
            if self.checkpoint_id != SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID:
                raise ValueError("anchor receipt requires the canonical checkpointId")
            if self.routed_beat_sha256 is not None or self.semantic_effect is not None:
                raise ValueError("anchor receipt cannot bind a routed beat or semantic effect")
            if not digest_matches(self.base_program_sha256, self.result_program_sha256):
                raise ValueError("anchor receipt must preserve the empty program hash")
            expected_obligations = SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS
        else:
            if self.checkpoint_id == SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID:
                raise ValueError("model receipt cannot use the anchor checkpointId")
            if self.routed_beat_sha256 is None or self.semantic_effect is None:
                raise ValueError("model receipt requires a routed beat and semantic effect")
            if digest_matches(self.base_program_sha256, self.result_program_sha256):
                raise ValueError("model receipt must advance the semantic program")
            expected_obligations = SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
        if self.obligation_codes != expected_obligations:
            raise ValueError("storyboard receipt must bind the exact origin verifier obligations")
        return self


def semantic_storyboard_checkpoint_receipt_sha256(
    receipt: SemanticStoryboardCheckpointVerificationReceiptV1,
) -> str:
    """Hash one verifier receipt in its dedicated Gate 1.8 domain."""

    return canonical_sha256(
        receipt.model_dump(mode="json", by_alias=True),
        domain=SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    )


class SemanticStoryboardCheckpointCompilerCertificateBodyV1(LiveSceneContract):
    """Compiler commitments for one anchor or one atomic model-record transition."""

    v: Literal[SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION] = (
        SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION
    )
    issuer: Literal["semantic_storyboard_compiler"] = "semantic_storyboard_compiler"
    compiler_version: Literal[SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION] = Field(
        default=SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
        alias="compilerVersion",
    )
    canonicalization: Literal[SEMANTIC_CANONICALIZATION] = SEMANTIC_CANONICALIZATION
    hash_algorithm: Literal[SEMANTIC_HASH_ALGORITHM] = Field(
        default=SEMANTIC_HASH_ALGORITHM,
        alias="hashAlgorithm",
    )
    checkpoint_origin: SemanticStoryboardCheckpointOrigin = Field(alias="checkpointOrigin")
    catalog_version: Literal[SEMANTIC_STORYBOARD_CATALOG_VERSION] = Field(
        default=SEMANTIC_STORYBOARD_CATALOG_VERSION,
        alias="catalogVersion",
    )
    beat_id: ChoreographyId | None = Field(alias="beatId")
    routed_beat_sha256: Sha256Digest | None = Field(alias="routedBeatSha256")
    record_sha256: Sha256Digest | None = Field(alias="recordSha256")
    component_kind: Literal["projectile_comparison_storyboard"] = Field(
        default="projectile_comparison_storyboard",
        alias="componentKind",
    )
    component_id: Literal[PROJECTILE_STORYBOARD_COMPONENT_ID] = Field(
        default=PROJECTILE_STORYBOARD_COMPONENT_ID,
        alias="componentId",
    )
    checkpoint_id: ChoreographyId = Field(alias="checkpointId")
    problem_spec_sha256: Sha256Digest = Field(alias="problemSpecSha256")
    base_program_sha256: Sha256Digest = Field(alias="baseProgramSha256")
    result_program_sha256: Sha256Digest = Field(alias="resultProgramSha256")
    base_low_level_revision: NonNegativeRevision = Field(alias="baseLowLevelRevision")
    result_low_level_revision: PositiveRevision = Field(alias="resultLowLevelRevision")
    base_semantic_revision: NonNegativeRevision = Field(alias="baseSemanticRevision")
    result_semantic_revision: PositiveRevision = Field(alias="resultSemanticRevision")
    base_low_level_scene_sha256: Sha256Digest = Field(alias="baseLowLevelSceneSha256")
    result_low_level_scene_sha256: Sha256Digest = Field(alias="resultLowLevelSceneSha256")
    base_semantic_scene_sha256: Sha256Digest = Field(alias="baseSemanticSceneSha256")
    result_semantic_scene_sha256: Sha256Digest = Field(alias="resultSemanticSceneSha256")
    patch_sha256: Sha256Digest = Field(alias="patchSha256")
    receipt_sha256: Sha256Digest = Field(alias="receiptSha256")
    presentation_checkpoint: PresentationCheckpointV1 = Field(alias="presentationCheckpoint")
    choreography_sha256: Sha256Digest = Field(alias="choreographySha256")
    previous_certificate_sha256: Sha256Digest | None = Field(alias="previousCertificateSha256")

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_transition_identity(self) -> Self:
        if self.result_low_level_revision != self.base_low_level_revision + 1:
            raise ValueError("resultLowLevelRevision must advance exactly once")
        if self.result_semantic_revision != self.base_semantic_revision + 1:
            raise ValueError("resultSemanticRevision must advance exactly once")
        if self.base_low_level_revision != self.base_semantic_revision:
            raise ValueError("base low-level and semantic revisions must match")
        if self.result_low_level_revision != self.result_semantic_revision:
            raise ValueError("result low-level and semantic revisions must match")
        if self.presentation_checkpoint.checkpoint_id != self.checkpoint_id:
            raise ValueError("presentation checkpointId must match certificate checkpointId")

        model_fields = (
            self.beat_id,
            self.routed_beat_sha256,
            self.record_sha256,
            self.previous_certificate_sha256,
        )
        if self.checkpoint_origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
            if self.checkpoint_id != SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID:
                raise ValueError("anchor certificate requires the canonical checkpointId")
            if any(value is not None for value in model_fields):
                raise ValueError("anchor certificate forbids model and previous-head fields")
            if self.base_low_level_revision != 0:
                raise ValueError("anchor certificate must begin at revision 0")
            if not digest_matches(self.base_program_sha256, self.result_program_sha256):
                raise ValueError("anchor certificate must preserve the empty program hash")
        else:
            if self.checkpoint_id == SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID:
                raise ValueError("model certificate cannot use the anchor checkpointId")
            if any(value is None for value in model_fields):
                raise ValueError(
                    "model certificate requires beat, record, and previous-head fields"
                )
            if self.base_low_level_revision == 0:
                raise ValueError("model certificate requires the accepted anchor revision")
            if digest_matches(self.base_program_sha256, self.result_program_sha256):
                raise ValueError("model certificate must advance the semantic program")
        return self


def semantic_storyboard_checkpoint_certificate_sha256(
    body: SemanticStoryboardCheckpointCompilerCertificateBodyV1,
) -> str:
    """Hash one non-recursive certificate body in its dedicated domain."""

    return canonical_sha256(
        body.model_dump(mode="json", by_alias=True),
        domain=SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )


class SemanticStoryboardCheckpointCompilerCertificateV1(LiveSceneContract):
    """Self-checking compiler certificate for one storyboard checkpoint."""

    body: SemanticStoryboardCheckpointCompilerCertificateBodyV1
    certificate_sha256: Sha256Digest = Field(alias="certificateSha256")

    @model_validator(mode="after")
    def validate_certificate_digest(self) -> Self:
        expected = semantic_storyboard_checkpoint_certificate_sha256(self.body)
        if not digest_matches(self.certificate_sha256, expected):
            raise ValueError("certificateSha256 must match the canonical storyboard body")
        return self


class CompiledSemanticStoryboardCheckpointV1(LiveSceneContract):
    """One internally consistent compiler-and-verifier checkpoint artifact."""

    checkpoint_origin: SemanticStoryboardCheckpointOrigin = Field(alias="checkpointOrigin")
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    beat: RoutedSemanticStoryboardBeatV1 | None = None
    checkpoint_id: ChoreographyId = Field(alias="checkpointId")
    patch: ScenePatchDraft
    receipt: SemanticStoryboardCheckpointVerificationReceiptV1
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV2
    certificate: SemanticStoryboardCheckpointCompilerCertificateV1

    @model_validator(mode="after")
    def validate_all_recomputable_bindings(self) -> Self:
        problem_digest = paired_projectile_comparison_sha256(self.problem_spec)
        if self.checkpoint_origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
            if self.beat is not None:
                raise ValueError("anchor checkpoint forbids a routed beat")
            checkpoint_id = SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID
            routed_digest = None
            record_digest = None
            beat_id = None
            semantic_effect = None
            program_digest = semantic_storyboard_program_sha256(self.problem_spec, ())
            base_program_digest = result_program_digest = program_digest
            previous_certificate = None
            obligations = SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS
            patch_id = f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_anchor"
        else:
            if self.beat is None:
                raise ValueError("model checkpoint requires a routed beat")
            if self.beat.problem_spec != self.problem_spec:
                raise ValueError("checkpoint problemSpec must match routed beat")
            checkpoint_id = self.beat.checkpoint_id
            routed_digest = routed_semantic_storyboard_beat_sha256(self.beat)
            record_digest = semantic_storyboard_record_sha256(self.beat.record)
            beat_id = self.beat.beat_id
            semantic_effect = self.beat.semantic_effect
            base_program_digest = self.beat.base_program_sha256
            result_program_digest = self.beat.result_program_sha256
            previous_certificate = self.beat.previous_certificate_sha256
            obligations = SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
            patch_id = (
                f"{PROJECTILE_STORYBOARD_COMPONENT_ID}__cp_"
                f"{storyboard_record_slug(self.beat.record)}"
            )

        if self.checkpoint_id != checkpoint_id:
            raise ValueError("compiled checkpointId must match its origin")
        if self.patch.patch_id != patch_id:
            raise ValueError("compiled patchId must match its deterministic identity")
        if self.patch.narration != self.presentation.checkpoint_narration:
            raise ValueError("patch narration must match checkpointNarration")
        if self.presentation.checkpoint_id != checkpoint_id:
            raise ValueError("presentation checkpointId must match compiled checkpointId")

        expected_receipt = SemanticStoryboardCheckpointVerificationReceiptV1(
            checkpointOrigin=self.checkpoint_origin,
            checkpointId=checkpoint_id,
            problemSpecSha256=problem_digest,
            routedBeatSha256=routed_digest,
            baseProgramSha256=base_program_digest,
            resultProgramSha256=result_program_digest,
            semanticEffect=semantic_effect,
            operationTargets=tuple(operation.target_id for operation in self.patch.operations),
            obligationCodes=obligations,
        )
        if self.receipt != expected_receipt:
            raise ValueError("receipt must match all recomputed storyboard bindings")

        body = self.certificate.body
        expected_body = SemanticStoryboardCheckpointCompilerCertificateBodyV1(
            checkpointOrigin=self.checkpoint_origin,
            beatId=beat_id,
            routedBeatSha256=routed_digest,
            recordSha256=record_digest,
            checkpointId=checkpoint_id,
            problemSpecSha256=problem_digest,
            baseProgramSha256=base_program_digest,
            resultProgramSha256=result_program_digest,
            baseLowLevelRevision=body.base_low_level_revision,
            resultLowLevelRevision=body.result_low_level_revision,
            baseSemanticRevision=body.base_semantic_revision,
            resultSemanticRevision=body.result_semantic_revision,
            baseLowLevelSceneSha256=body.base_low_level_scene_sha256,
            resultLowLevelSceneSha256=body.result_low_level_scene_sha256,
            baseSemanticSceneSha256=body.base_semantic_scene_sha256,
            resultSemanticSceneSha256=body.result_semantic_scene_sha256,
            patchSha256=scene_patch_sha256(self.patch),
            receiptSha256=semantic_storyboard_checkpoint_receipt_sha256(self.receipt),
            presentationCheckpoint=self.presentation,
            choreographySha256=choreography_plan_v2_sha256(self.choreography),
            previousCertificateSha256=previous_certificate,
        )
        if body != expected_body:
            raise ValueError("certificate body must match all recomputed storyboard bindings")
        return self


SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_V1_ADAPTER = TypeAdapter(
    SemanticStoryboardCheckpointVerificationReceiptV1
)
SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_V1_ADAPTER = TypeAdapter(
    SemanticStoryboardCheckpointCompilerCertificateV1
)
COMPILED_SEMANTIC_STORYBOARD_CHECKPOINT_V1_ADAPTER = TypeAdapter(
    CompiledSemanticStoryboardCheckpointV1
)

__all__ = [
    "COMPILED_SEMANTIC_STORYBOARD_CHECKPOINT_V1_ADAPTER",
    "SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID",
    "SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN",
    "SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_V1_ADAPTER",
    "SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION",
    "SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION",
    "SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN",
    "SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_V1_ADAPTER",
    "CompiledSemanticStoryboardCheckpointV1",
    "SemanticStoryboardCheckpointCompilerCertificateBodyV1",
    "SemanticStoryboardCheckpointCompilerCertificateV1",
    "SemanticStoryboardCheckpointOrigin",
    "SemanticStoryboardCheckpointVerificationReceiptV1",
    "semantic_storyboard_checkpoint_certificate_sha256",
    "semantic_storyboard_checkpoint_receipt_sha256",
]

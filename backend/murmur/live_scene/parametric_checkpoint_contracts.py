"""Problem-bound integrity contracts for parametric choreography checkpoints.

Gate 1.6 keeps these V3 records separate from the sealed Gate 1.5 V2
certificate path.  The compiler and verifier independently commit to the same
bounded completing-square problem, so a valid checkpoint from one equation
cannot be transplanted into another equation's certificate chain.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyId,
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV3,
    choreography_plan_sha256,
    routed_choreography_beat_v3_sha256,
)
from murmur.live_scene.completing_square_contracts import CompletingSquareCheckpointId
from murmur.live_scene.completing_square_problem_contracts import (
    completing_square_problem_sha256,
)
from murmur.live_scene.contracts import (
    MAX_PATCH_OPERATIONS,
    LiveSceneContract,
    NonNegativeRevision,
    PositiveRevision,
    SceneNodeId,
    ScenePatchDraft,
)
from murmur.live_scene.semantic_contracts import (
    SemanticComponentId,
    Sha256Digest,
    scene_patch_sha256,
)
from murmur.live_scene.semantic_integrity import (
    SEMANTIC_CANONICALIZATION,
    SEMANTIC_HASH_ALGORITHM,
    canonical_sha256,
    digest_matches,
)

CHECKPOINT_CERTIFICATE_V3_VERSION: Final = 3
CHECKPOINT_COMPILER_V3_VERSION: Final = "murmur.completing_square_choreography.v2"

CHECKPOINT_RECEIPT_V3_HASH_DOMAIN: Final = "murmur:checkpoint-receipt:v3"
CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN: Final = "murmur:checkpoint-certificate:v3"

ParametricOperationTargets = Annotated[
    tuple[SceneNodeId, ...],
    Field(min_length=1, max_length=MAX_PATCH_OPERATIONS),
]


class CheckpointVerificationObligationV3(StrEnum):
    """Closed independent claims available to the parametric verifier.

    V2 remains sealed.  V3 repeats its structural and mathematical vocabulary
    and adds the claims introduced by problem-bound, mute-first choreography.
    """

    STABLE_ID = "stable_id"
    UNIQUE_IDS = "unique_ids"
    BOARD_BOUNDS = "board_bounds"
    COMPONENT_OWNERSHIP = "component_ownership"
    PATCH_MATERIALIZATION = "patch_materialization"
    COMPATIBLE_MORPH = "compatible_morph"
    VIEWPORT_CONTAINMENT = "viewport_containment"
    EQUATION_IDENTITY = "equation_identity"
    AREA_MODEL = "area_model"
    EQUAL_LINEAR_SPLIT = "equal_linear_split"
    ADJACENT_REARRANGEMENT = "adjacent_rearrangement"
    MISSING_CORNER = "missing_corner"
    BALANCED_COMPLETION = "balanced_completion"
    FACTORIZATION = "factorization"
    ROOTS = "roots"
    GEOMETRY_DOMAIN = "geometry_domain"
    PROBLEM_IDENTITY = "problem_identity"
    CAPTION_FACTS = "caption_facts"
    AUTHORED_TIMING = "authored_timing"


class CheckpointVerificationReceiptV3(LiveSceneContract):
    """Independent claims for one problem-bound checkpoint transition."""

    issuer: Literal["completing_square_verifier"] = "completing_square_verifier"
    component_kind: Literal["completing_square_parametric"] = Field(
        default="completing_square_parametric",
        alias="componentKind",
    )
    component_id: SemanticComponentId = Field(alias="componentId")
    problem_spec_sha256: Sha256Digest = Field(alias="problemSpecSha256")
    checkpoint_id: CompletingSquareCheckpointId = Field(alias="checkpointId")
    operation_targets: ParametricOperationTargets = Field(alias="operationTargets")
    obligation_codes: Annotated[
        tuple[CheckpointVerificationObligationV3, ...],
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
    def validate_unique_values(self) -> Self:
        if len(self.operation_targets) != len(set(self.operation_targets)):
            raise ValueError("checkpoint receipt operationTargets must be unique")
        if len(self.obligation_codes) != len(set(self.obligation_codes)):
            raise ValueError("checkpoint receipt obligationCodes must be unique")
        return self


def checkpoint_receipt_v3_sha256(receipt: CheckpointVerificationReceiptV3) -> str:
    """Hash the verifier's ordered V3 claims in the problem-bound domain."""

    return canonical_sha256(
        receipt.model_dump(mode="json", by_alias=True),
        domain=CHECKPOINT_RECEIPT_V3_HASH_DOMAIN,
    )


class CheckpointCompilerCertificateBodyV3(LiveSceneContract):
    """Exact compiler commitments for one parametric settled checkpoint."""

    v: Literal[CHECKPOINT_CERTIFICATE_V3_VERSION] = CHECKPOINT_CERTIFICATE_V3_VERSION
    issuer: Literal["semantic_compiler"] = "semantic_compiler"
    compiler_version: Literal[CHECKPOINT_COMPILER_V3_VERSION] = Field(
        default=CHECKPOINT_COMPILER_V3_VERSION,
        alias="compilerVersion",
    )
    canonicalization: Literal[SEMANTIC_CANONICALIZATION] = SEMANTIC_CANONICALIZATION
    hash_algorithm: Literal[SEMANTIC_HASH_ALGORITHM] = Field(
        default=SEMANTIC_HASH_ALGORITHM,
        alias="hashAlgorithm",
    )
    beat_id: ChoreographyId = Field(alias="beatId")
    routed_beat_sha256: Sha256Digest = Field(alias="routedBeatSha256")
    component_kind: Literal["completing_square_parametric"] = Field(
        default="completing_square_parametric",
        alias="componentKind",
    )
    component_id: SemanticComponentId = Field(alias="componentId")
    problem_spec_sha256: Sha256Digest = Field(alias="problemSpecSha256")
    checkpoint_id: CompletingSquareCheckpointId = Field(alias="checkpointId")
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    base_low_level_scene_sha256: Sha256Digest = Field(alias="baseLowLevelSceneSha256")
    result_low_level_scene_sha256: Sha256Digest = Field(alias="resultLowLevelSceneSha256")
    base_semantic_scene_sha256: Sha256Digest = Field(alias="baseSemanticSceneSha256")
    result_semantic_scene_sha256: Sha256Digest = Field(alias="resultSemanticSceneSha256")
    patch_sha256: Sha256Digest = Field(alias="patchSha256")
    receipt_sha256: Sha256Digest = Field(alias="receiptSha256")
    presentation_checkpoint: PresentationCheckpointV1 = Field(alias="presentationCheckpoint")
    choreography_sha256: Sha256Digest = Field(alias="choreographySha256")
    previous_certificate_sha256: Sha256Digest | None = Field(
        default=None,
        alias="previousCertificateSha256",
    )

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_transition_identity(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("certificate resultRevision must be one greater than baseRevision")
        if self.presentation_checkpoint.checkpoint_id != self.checkpoint_id.value:
            raise ValueError(
                "certificate presentation checkpointId must match certificate checkpointId"
            )
        return self


def checkpoint_certificate_v3_sha256(body: CheckpointCompilerCertificateBodyV3) -> str:
    """Hash the non-recursive V3 certificate body in its dedicated domain."""

    return canonical_sha256(
        body.model_dump(mode="json", by_alias=True),
        domain=CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN,
    )


class CheckpointCompilerCertificateV3(LiveSceneContract):
    """Self-checking compiler certificate for one parametric checkpoint."""

    body: CheckpointCompilerCertificateBodyV3
    certificate_sha256: Sha256Digest = Field(alias="certificateSha256")

    @model_validator(mode="after")
    def validate_certificate_digest(self) -> Self:
        expected = checkpoint_certificate_v3_sha256(self.body)
        if not digest_matches(self.certificate_sha256, expected):
            raise ValueError("certificateSha256 must match the canonical checkpoint body")
        return self


class CompiledCheckpointV3(LiveSceneContract):
    """One complete problem-bound compiler and verifier checkpoint claim."""

    beat: RoutedChoreographyBeatV3
    checkpoint_id: CompletingSquareCheckpointId = Field(alias="checkpointId")
    patch: ScenePatchDraft
    receipt: CheckpointVerificationReceiptV3
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV1
    certificate: CheckpointCompilerCertificateV3

    @model_validator(mode="after")
    def validate_all_bindings(self) -> Self:
        expected_patch_id = f"{self.beat.component_id}__cp_{self.checkpoint_id.value}"
        if self.patch.patch_id != expected_patch_id:
            raise ValueError("compiled checkpoint patchId must match its deterministic identity")
        if self.patch.narration != self.presentation.checkpoint_narration:
            raise ValueError("patch narration must match checkpointNarration")
        if self.presentation.checkpoint_id != self.checkpoint_id.value:
            raise ValueError("presentation checkpointId must match compiled checkpointId")

        operation_targets = tuple(operation.target_id for operation in self.patch.operations)
        if operation_targets != self.receipt.operation_targets:
            raise ValueError("receipt operationTargets must match ordered patch targets")

        expected_problem_sha256 = completing_square_problem_sha256(self.beat.problem_spec)
        if self.receipt.component_kind != self.beat.component_kind:
            raise ValueError("receipt componentKind must match routed beat")
        if self.receipt.component_id != self.beat.component_id:
            raise ValueError("receipt componentId must match routed beat componentId")
        if not digest_matches(self.receipt.problem_spec_sha256, expected_problem_sha256):
            raise ValueError("receipt problemSpecSha256 must match routed beat problemSpec")
        if self.receipt.checkpoint_id != self.checkpoint_id:
            raise ValueError("receipt checkpointId must match compiled checkpointId")

        body = self.certificate.body
        if body.beat_id != self.beat.beat_id:
            raise ValueError("certificate beatId must match routed beat")
        if not digest_matches(
            body.routed_beat_sha256,
            routed_choreography_beat_v3_sha256(self.beat),
        ):
            raise ValueError("certificate routedBeatSha256 must match routed beat")
        if body.component_kind != self.beat.component_kind:
            raise ValueError("certificate componentKind must match routed beat")
        if body.component_id != self.beat.component_id:
            raise ValueError("certificate componentId must match routed beat")
        if not digest_matches(body.problem_spec_sha256, expected_problem_sha256):
            raise ValueError("certificate problemSpecSha256 must match routed beat problemSpec")
        if body.checkpoint_id != self.checkpoint_id:
            raise ValueError("certificate checkpointId must match compiled checkpointId")
        if not digest_matches(body.patch_sha256, scene_patch_sha256(self.patch)):
            raise ValueError("certificate patchSha256 must match checkpoint patch")
        if not digest_matches(
            body.receipt_sha256,
            checkpoint_receipt_v3_sha256(self.receipt),
        ):
            raise ValueError("certificate receiptSha256 must match checkpoint receipt")
        if body.presentation_checkpoint != self.presentation:
            raise ValueError("certificate presentationCheckpoint must match presentation")
        if not digest_matches(
            body.choreography_sha256,
            choreography_plan_sha256(self.choreography),
        ):
            raise ValueError("certificate choreographySha256 must match choreography")
        return self


__all__ = [
    "CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN",
    "CHECKPOINT_CERTIFICATE_V3_VERSION",
    "CHECKPOINT_COMPILER_V3_VERSION",
    "CHECKPOINT_RECEIPT_V3_HASH_DOMAIN",
    "CheckpointCompilerCertificateBodyV3",
    "CheckpointCompilerCertificateV3",
    "CheckpointVerificationObligationV3",
    "CheckpointVerificationReceiptV3",
    "CompiledCheckpointV3",
    "checkpoint_certificate_v3_sha256",
    "checkpoint_receipt_v3_sha256",
]

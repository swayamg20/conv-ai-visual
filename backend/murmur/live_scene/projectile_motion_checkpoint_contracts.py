"""Integrity contracts for certified Gate 1.7 projectile checkpoints.

These records are deliberately separate from the sealed completing-square
certificate families.  A projectile transition may preserve or retarget its
problem, so both sides of that identity boundary are committed explicitly.
"""

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
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    projectile_motion_problem_sha256,
    routed_projectile_motion_beat_sha256,
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

PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION: Final = 1
PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION: Final = "murmur.projectile_motion_choreography.v1"
PROJECTILE_MOTION_CHECKPOINT_RECEIPT_HASH_DOMAIN: Final = (
    "murmur:projectile-motion-checkpoint-receipt:v1"
)
PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_HASH_DOMAIN: Final = (
    "murmur:projectile-motion-checkpoint-certificate:v1"
)

ProjectileMotionOperationTargets = Annotated[
    tuple[SceneNodeId, ...],
    Field(min_length=1, max_length=MAX_PATCH_OPERATIONS),
]


class ProjectileMotionCheckpointAction(StrEnum):
    """Closed routed action bound into each checkpoint claim."""

    ADVANCE = "advance"
    CLARIFY = "clarify"
    RETARGET = "retarget"


class ProjectileMotionVerificationObligation(StrEnum):
    """The complete independent verifier suite certified by one receipt."""

    BLUEPRINT_CONTRACT = "blueprint_contract"
    PROBLEM_IDENTITY = "problem_identity"
    TRANSITION = "transition"
    STABLE_IDS = "stable_ids"
    BOARD_BOUNDS = "board_bounds"
    TEXT_COLLISION = "text_collision"
    PHYSICS_GEOMETRY = "physics_geometry"
    LABEL_FACT = "label_fact"
    LABEL_LAYOUT = "label_layout"
    VISUAL_STYLE = "visual_style"
    PATCH = "patch"
    CAPTION = "caption"
    CHOREOGRAPHY = "choreography"
    TIMING = "timing"
    VIEWPORT = "viewport"


PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS: Final[
    tuple[ProjectileMotionVerificationObligation, ...]
] = tuple(ProjectileMotionVerificationObligation)

_MAIN_CHECKPOINT_IDS: Final[frozenset[ProjectileMotionCheckpointId]] = frozenset(
    ProjectileMotionCheckpointId(checkpoint.value) for checkpoint in ProjectileMotionMainCheckpoint
)


def _validate_action_and_problem_transition(
    *,
    action: ProjectileMotionCheckpointAction,
    checkpoint_id: ProjectileMotionCheckpointId,
    clarification_topic: ProjectileMotionClarificationTopic | None,
    base_problem_spec_sha256: str | None,
    result_problem_spec_sha256: str,
) -> None:
    if action is ProjectileMotionCheckpointAction.ADVANCE:
        if checkpoint_id not in _MAIN_CHECKPOINT_IDS:
            raise ValueError("advance action requires a main checkpointId")
        if clarification_topic is not None:
            raise ValueError("advance action must not bind clarificationTopic")
        if base_problem_spec_sha256 is None:
            if checkpoint_id is not ProjectileMotionCheckpointId.SETUP:
                raise ValueError(
                    "baseProblemSpecSha256 may be null only for a fresh setup checkpoint"
                )
        elif not digest_matches(base_problem_spec_sha256, result_problem_spec_sha256):
            raise ValueError("advance action must preserve the problem digest")
        return

    if action is ProjectileMotionCheckpointAction.CLARIFY:
        if clarification_topic is None:
            raise ValueError("clarify action requires clarificationTopic")
        if checkpoint_id is not PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[clarification_topic]:
            raise ValueError("clarificationTopic must match checkpointId")
        if base_problem_spec_sha256 is None:
            raise ValueError("clarify action requires baseProblemSpecSha256")
        if not digest_matches(base_problem_spec_sha256, result_problem_spec_sha256):
            raise ValueError("clarify action must preserve the problem digest")
        return

    if action is not ProjectileMotionCheckpointAction.RETARGET:
        raise ValueError("unsupported projectile checkpoint action")
    if checkpoint_id is not ProjectileMotionCheckpointId.PARAMETERS_RETARGETED:
        raise ValueError("retarget action requires parameters_retargeted checkpointId")
    if clarification_topic is not None:
        raise ValueError("retarget action must not bind clarificationTopic")
    if base_problem_spec_sha256 is None:
        raise ValueError("retarget action requires baseProblemSpecSha256")
    if digest_matches(base_problem_spec_sha256, result_problem_spec_sha256):
        raise ValueError("retarget action must change the problem digest")


def _beat_action(
    beat: RoutedProjectileMotionBeatV1,
) -> tuple[ProjectileMotionCheckpointAction, ProjectileMotionClarificationTopic | None]:
    if isinstance(beat.route, AdvanceProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.ADVANCE, None
    if isinstance(beat.route, ClarifyProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.CLARIFY, beat.route.topic
    if isinstance(beat.route, RetargetProjectileMotionRouteV1):
        return ProjectileMotionCheckpointAction.RETARGET, None
    raise ValueError("unsupported projectile routed beat")


class ProjectileMotionCheckpointVerificationReceiptV1(LiveSceneContract):
    """Independent all-obligation receipt for one projectile transition."""

    issuer: Literal["projectile_motion_verifier"] = "projectile_motion_verifier"
    component_kind: Literal["projectile_motion"] = Field(
        default="projectile_motion",
        alias="componentKind",
    )
    component_id: SemanticComponentId = Field(alias="componentId")
    action: ProjectileMotionCheckpointAction
    checkpoint_id: ProjectileMotionCheckpointId = Field(alias="checkpointId")
    clarification_topic: ProjectileMotionClarificationTopic | None = Field(
        default=None,
        alias="clarificationTopic",
    )
    base_problem_spec_sha256: Sha256Digest | None = Field(alias="baseProblemSpecSha256")
    result_problem_spec_sha256: Sha256Digest = Field(alias="resultProblemSpecSha256")
    operation_targets: ProjectileMotionOperationTargets = Field(alias="operationTargets")
    obligation_codes: Annotated[
        tuple[ProjectileMotionVerificationObligation, ...],
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
    def validate_complete_claim(self) -> Self:
        if len(self.operation_targets) != len(set(self.operation_targets)):
            raise ValueError("projectile receipt operationTargets must be unique")
        if self.obligation_codes != PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS:
            raise ValueError("projectile receipt must bind the complete verifier obligation suite")
        _validate_action_and_problem_transition(
            action=self.action,
            checkpoint_id=self.checkpoint_id,
            clarification_topic=self.clarification_topic,
            base_problem_spec_sha256=self.base_problem_spec_sha256,
            result_problem_spec_sha256=self.result_problem_spec_sha256,
        )
        return self


def projectile_motion_checkpoint_receipt_sha256(
    receipt: ProjectileMotionCheckpointVerificationReceiptV1,
) -> str:
    """Hash a projectile receipt in its dedicated domain."""

    return canonical_sha256(
        receipt.model_dump(mode="json", by_alias=True),
        domain=PROJECTILE_MOTION_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    )


class ProjectileMotionCheckpointCompilerCertificateBodyV1(LiveSceneContract):
    """Exact compiler commitments for one projectile checkpoint."""

    v: Literal[PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION] = (
        PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION
    )
    issuer: Literal["projectile_motion_compiler"] = "projectile_motion_compiler"
    compiler_version: Literal[PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION] = Field(
        default=PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
        alias="compilerVersion",
    )
    canonicalization: Literal[SEMANTIC_CANONICALIZATION] = SEMANTIC_CANONICALIZATION
    hash_algorithm: Literal[SEMANTIC_HASH_ALGORITHM] = Field(
        default=SEMANTIC_HASH_ALGORITHM,
        alias="hashAlgorithm",
    )
    beat_id: ChoreographyId = Field(alias="beatId")
    routed_beat_sha256: Sha256Digest = Field(alias="routedBeatSha256")
    component_kind: Literal["projectile_motion"] = Field(
        default="projectile_motion",
        alias="componentKind",
    )
    component_id: SemanticComponentId = Field(alias="componentId")
    action: ProjectileMotionCheckpointAction
    checkpoint_id: ProjectileMotionCheckpointId = Field(alias="checkpointId")
    clarification_topic: ProjectileMotionClarificationTopic | None = Field(
        default=None,
        alias="clarificationTopic",
    )
    base_problem_spec_sha256: Sha256Digest | None = Field(alias="baseProblemSpecSha256")
    result_problem_spec_sha256: Sha256Digest = Field(alias="resultProblemSpecSha256")
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
        if self.result_low_level_revision != self.base_low_level_revision + 1:
            raise ValueError(
                "certificate resultLowLevelRevision must be one greater than baseLowLevelRevision"
            )
        if self.result_semantic_revision != self.base_semantic_revision + 1:
            raise ValueError(
                "certificate resultSemanticRevision must be one greater than baseSemanticRevision"
            )
        if self.base_low_level_revision != self.base_semantic_revision:
            raise ValueError("certificate base low-level and semantic revisions must match")
        if self.result_low_level_revision != self.result_semantic_revision:
            raise ValueError("certificate result low-level and semantic revisions must match")
        if self.presentation_checkpoint.checkpoint_id != self.checkpoint_id.value:
            raise ValueError(
                "certificate presentation checkpointId must match certificate checkpointId"
            )
        _validate_action_and_problem_transition(
            action=self.action,
            checkpoint_id=self.checkpoint_id,
            clarification_topic=self.clarification_topic,
            base_problem_spec_sha256=self.base_problem_spec_sha256,
            result_problem_spec_sha256=self.result_problem_spec_sha256,
        )
        return self


def projectile_motion_checkpoint_certificate_sha256(
    body: ProjectileMotionCheckpointCompilerCertificateBodyV1,
) -> str:
    """Hash the non-recursive projectile certificate body."""

    return canonical_sha256(
        body.model_dump(mode="json", by_alias=True),
        domain=PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )


class ProjectileMotionCheckpointCompilerCertificateV1(LiveSceneContract):
    """Self-checking projectile checkpoint certificate."""

    body: ProjectileMotionCheckpointCompilerCertificateBodyV1
    certificate_sha256: Sha256Digest = Field(alias="certificateSha256")

    @model_validator(mode="after")
    def validate_certificate_digest(self) -> Self:
        expected = projectile_motion_checkpoint_certificate_sha256(self.body)
        if not digest_matches(self.certificate_sha256, expected):
            raise ValueError("certificateSha256 must match the canonical projectile body")
        return self


class CompiledProjectileMotionCheckpointV1(LiveSceneContract):
    """One complete projectile compiler-and-verifier checkpoint claim."""

    beat: RoutedProjectileMotionBeatV1
    action: ProjectileMotionCheckpointAction
    checkpoint_id: ProjectileMotionCheckpointId = Field(alias="checkpointId")
    clarification_topic: ProjectileMotionClarificationTopic | None = Field(
        default=None,
        alias="clarificationTopic",
    )
    patch: ScenePatchDraft
    receipt: ProjectileMotionCheckpointVerificationReceiptV1
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV2
    certificate: ProjectileMotionCheckpointCompilerCertificateV1

    @model_validator(mode="after")
    def validate_all_bindings(self) -> Self:
        expected_action, expected_topic = _beat_action(self.beat)
        if self.action is not expected_action:
            raise ValueError("compiled checkpoint action must match routed beat")
        if self.clarification_topic is not expected_topic:
            raise ValueError("compiled checkpoint clarificationTopic must match routed beat")

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
        if self.receipt.component_kind != self.beat.component_kind:
            raise ValueError("receipt componentKind must match routed beat")
        if self.receipt.component_id != self.beat.component_id:
            raise ValueError("receipt componentId must match routed beat componentId")
        if self.receipt.action is not self.action:
            raise ValueError("receipt action must match compiled checkpoint")
        if self.receipt.checkpoint_id is not self.checkpoint_id:
            raise ValueError("receipt checkpointId must match compiled checkpointId")
        if self.receipt.clarification_topic is not self.clarification_topic:
            raise ValueError("receipt clarificationTopic must match compiled checkpoint")

        result_problem_digest = projectile_motion_problem_sha256(self.beat.result_problem_spec)
        if not digest_matches(
            self.receipt.result_problem_spec_sha256,
            result_problem_digest,
        ):
            raise ValueError("receipt resultProblemSpecSha256 must match routed beat")
        if self.beat.base_problem_spec is None:
            expected_base_digest = (
                None
                if self.checkpoint_id is ProjectileMotionCheckpointId.SETUP
                else result_problem_digest
            )
        else:
            expected_base_digest = projectile_motion_problem_sha256(self.beat.base_problem_spec)
        if self.receipt.base_problem_spec_sha256 != expected_base_digest:
            raise ValueError("receipt baseProblemSpecSha256 must match routed transition")

        body = self.certificate.body
        if body.beat_id != self.beat.beat_id:
            raise ValueError("certificate beatId must match routed beat")
        if not digest_matches(
            body.routed_beat_sha256,
            routed_projectile_motion_beat_sha256(self.beat),
        ):
            raise ValueError("certificate routedBeatSha256 must match routed beat")
        if body.component_kind != self.beat.component_kind:
            raise ValueError("certificate componentKind must match routed beat")
        if body.component_id != self.beat.component_id:
            raise ValueError("certificate componentId must match routed beat")
        if body.action is not self.action:
            raise ValueError("certificate action must match compiled checkpoint")
        if body.checkpoint_id is not self.checkpoint_id:
            raise ValueError("certificate checkpointId must match compiled checkpointId")
        if body.clarification_topic is not self.clarification_topic:
            raise ValueError("certificate clarificationTopic must match compiled checkpoint")
        if body.base_problem_spec_sha256 != self.receipt.base_problem_spec_sha256:
            raise ValueError("certificate baseProblemSpecSha256 must match receipt")
        if not digest_matches(
            body.result_problem_spec_sha256,
            self.receipt.result_problem_spec_sha256,
        ):
            raise ValueError("certificate resultProblemSpecSha256 must match receipt")
        if not digest_matches(body.patch_sha256, scene_patch_sha256(self.patch)):
            raise ValueError("certificate patchSha256 must match checkpoint patch")
        if not digest_matches(
            body.receipt_sha256,
            projectile_motion_checkpoint_receipt_sha256(self.receipt),
        ):
            raise ValueError("certificate receiptSha256 must match checkpoint receipt")
        if body.presentation_checkpoint != self.presentation:
            raise ValueError("certificate presentationCheckpoint must match presentation")
        if not digest_matches(
            body.choreography_sha256,
            choreography_plan_v2_sha256(self.choreography),
        ):
            raise ValueError("certificate choreographySha256 must match choreography V2")
        return self


PROJECTILE_MOTION_CHECKPOINT_RECEIPT_V1_ADAPTER = TypeAdapter(
    ProjectileMotionCheckpointVerificationReceiptV1
)
PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_V1_ADAPTER = TypeAdapter(
    ProjectileMotionCheckpointCompilerCertificateV1
)
COMPILED_PROJECTILE_MOTION_CHECKPOINT_V1_ADAPTER = TypeAdapter(CompiledProjectileMotionCheckpointV1)


__all__ = [
    "COMPILED_PROJECTILE_MOTION_CHECKPOINT_V1_ADAPTER",
    "PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_HASH_DOMAIN",
    "PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_V1_ADAPTER",
    "PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION",
    "PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION",
    "PROJECTILE_MOTION_CHECKPOINT_RECEIPT_HASH_DOMAIN",
    "PROJECTILE_MOTION_CHECKPOINT_RECEIPT_V1_ADAPTER",
    "PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS",
    "CompiledProjectileMotionCheckpointV1",
    "ProjectileMotionCheckpointAction",
    "ProjectileMotionCheckpointCompilerCertificateBodyV1",
    "ProjectileMotionCheckpointCompilerCertificateV1",
    "ProjectileMotionCheckpointVerificationReceiptV1",
    "ProjectileMotionVerificationObligation",
    "projectile_motion_checkpoint_certificate_sha256",
    "projectile_motion_checkpoint_receipt_sha256",
]

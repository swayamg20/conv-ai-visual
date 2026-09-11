"""Exact service contracts for Gate 1.7 projectile choreography streams.

The projectile protocol has its own checkpoint and terminal discriminators so
that neither the sealed Gate 1.5 nor Gate 1.6 decoders can accidentally accept
its payloads.  Generic started, repairing, and completed records are reused
because their meanings are identical.  The generic failed record is excluded:
this boundary exposes only a closed, retry-consistent failure vocabulary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV2,
    PresentationCheckpointV1,
)
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    AttemptNumber,
    FriendlyMessage,
    LiveSceneContract,
    NonNegativeRevision,
    PositiveRevision,
    PositiveSequence,
    ScenePatchDraft,
    SceneStreamCompletedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    CompiledProjectileMotionCheckpointV1,
    ProjectileMotionCheckpointAction,
    ProjectileMotionCheckpointCompilerCertificateV1,
    ProjectileMotionCheckpointVerificationReceiptV1,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStateV1,
    RoutedProjectileMotionBeatV1,
    next_projectile_motion_main_checkpoint,
    projectile_motion_problem_sha256,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    Sha256Digest,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import digest_matches

MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS = MAX_ACCEPTED_PATCHES
ProjectileChoreographyCheckpointSequence = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS),
]


class ProjectileChoreographyDeclineReason(StrEnum):
    """Closed successful no-mutation outcomes at the projectile boundary."""

    UNSUPPORTED_INTENT = "unsupported_intent"
    NO_FORWARD_PROGRESS = "no_forward_progress"
    PROBLEM_CONFLICT = "problem_conflict"


class ProjectileChoreographyFailureCode(StrEnum):
    """Closed failures emitted by the projectile service boundary."""

    SEMANTIC_BASE_MISMATCH = "semantic_base_mismatch"
    CHOREOGRAPHY_CAPACITY_EXCEEDED = "choreography_capacity_exceeded"
    CHOREOGRAPHY_CAPACITY_LIMIT = "choreography_capacity_limit"
    REVISION_LIMIT = "revision_limit"
    CONTEXT_TOO_LARGE = "context_too_large"
    INVALID_VISUAL_ACT = "invalid_visual_act"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    CHOREOGRAPHY_INTEGRITY_ERROR = "choreography_integrity_error"


_RETRYABLE_FAILURE_CODES = frozenset(
    {
        ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT,
        ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED,
        ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT,
        ProjectileChoreographyFailureCode.PROVIDER_ERROR,
    }
)


def _problem_digest(problem: ProjectileMotionProblemSpecV1 | None) -> str | None:
    if problem is None:
        return None
    return projectile_motion_problem_sha256(problem)


def _component_scene_digest(
    *,
    revision: int,
    component: ProjectileMotionStateV1 | None,
) -> str:
    return semantic_scene_sha256(
        SemanticSceneState(
            revision=revision,
            components=() if component is None else (component,),
        )
    )


def _validate_component_transition(
    *,
    action: ProjectileMotionCheckpointAction,
    checkpoint_id: ProjectileMotionCheckpointId,
    clarification_topic: ProjectileMotionClarificationTopic | None,
    base_component: ProjectileMotionStateV1 | None,
    result_component: ProjectileMotionStateV1,
) -> None:
    if base_component is None:
        if action is not ProjectileMotionCheckpointAction.ADVANCE:
            raise ValueError("only a fresh advance may omit baseComponent")
        if checkpoint_id is not ProjectileMotionCheckpointId.SETUP:
            raise ValueError("baseComponent may be null only for a fresh setup checkpoint")
        if result_component.last_main_checkpoint is not ProjectileMotionMainCheckpoint.SETUP:
            raise ValueError("fresh setup resultComponent must settle the setup frontier")
        if result_component.clarified_topics or result_component.active_clarification is not None:
            raise ValueError("fresh setup resultComponent must not contain clarifications")
        return

    if result_component.id != base_component.id:
        raise ValueError("resultComponent id must preserve baseComponent id")

    if action is ProjectileMotionCheckpointAction.ADVANCE:
        expected = next_projectile_motion_main_checkpoint(base_component.last_main_checkpoint)
        if expected is None:
            raise ValueError("advance checkpoint requires remaining forward progress")
        if checkpoint_id is not ProjectileMotionCheckpointId(expected.value):
            raise ValueError("advance checkpointId must be the next main checkpoint")
        if result_component.last_main_checkpoint is not expected:
            raise ValueError("advance resultComponent must settle the next main frontier")
        if result_component.clarified_topics != base_component.clarified_topics:
            raise ValueError("advance must preserve the clarification ledger")
        if result_component.active_clarification is not None:
            raise ValueError("advance must clear activeClarification")
        return

    if action is ProjectileMotionCheckpointAction.CLARIFY:
        if clarification_topic is None:
            raise ValueError("clarify transition requires clarificationTopic")
        if clarification_topic in base_component.clarified_topics:
            raise ValueError("clarificationTopic is one-shot")
        if checkpoint_id is not PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[clarification_topic]:
            raise ValueError("clarificationTopic must match checkpointId")
        expected_topics = tuple(
            topic
            for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER
            if topic in {*base_component.clarified_topics, clarification_topic}
        )
        if result_component.last_main_checkpoint is not base_component.last_main_checkpoint:
            raise ValueError("clarification must preserve the main frontier")
        if result_component.clarified_topics != expected_topics:
            raise ValueError("clarification must append to the canonical one-shot ledger")
        if result_component.active_clarification is not clarification_topic:
            raise ValueError("clarification must set activeClarification to its topic")
        return

    if action is not ProjectileMotionCheckpointAction.RETARGET:
        raise ValueError("unsupported projectile checkpoint action")
    if checkpoint_id is not ProjectileMotionCheckpointId.PARAMETERS_RETARGETED:
        raise ValueError("retarget action requires parameters_retargeted checkpointId")
    if base_component.last_main_checkpoint is None:
        raise ValueError("retarget requires a settled main frontier")
    if result_component.last_main_checkpoint is not base_component.last_main_checkpoint:
        raise ValueError("retarget must preserve the main frontier")
    if result_component.clarified_topics != base_component.clarified_topics:
        raise ValueError("retarget must preserve the clarification ledger")
    if result_component.active_clarification is not base_component.active_clarification:
        raise ValueError("retarget must preserve activeClarification")


class ProjectileCheckpointSemanticMetadataV1(LiveSceneContract):
    """Cleartext projectile frontier joined to one certified checkpoint."""

    base_problem_spec: ProjectileMotionProblemSpecV1 | None = Field(alias="baseProblemSpec")
    result_problem_spec: ProjectileMotionProblemSpecV1 = Field(alias="resultProblemSpec")
    beat: RoutedProjectileMotionBeatV1
    action: ProjectileMotionCheckpointAction
    checkpoint_id: ProjectileMotionCheckpointId = Field(alias="checkpointId")
    clarification_topic: ProjectileMotionClarificationTopic | None = Field(
        default=None,
        alias="clarificationTopic",
    )
    base_component: ProjectileMotionStateV1 | None = Field(alias="baseComponent")
    result_component: ProjectileMotionStateV1 = Field(alias="resultComponent")
    semantic_base_revision: NonNegativeRevision = Field(alias="semanticBaseRevision")
    semantic_result_revision: PositiveRevision = Field(alias="semanticResultRevision")
    semantic_base_certificate_sha256: Sha256Digest | None = Field(
        alias="semanticBaseCertificateSha256"
    )
    semantic_result_certificate_sha256: Sha256Digest = Field(
        alias="semanticResultCertificateSha256"
    )
    receipt: ProjectileMotionCheckpointVerificationReceiptV1
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV2
    certificate: ProjectileMotionCheckpointCompilerCertificateV1

    @model_validator(mode="after")
    def validate_frontier_problem_and_certificate_bindings(self) -> Self:
        if self.semantic_result_revision != self.semantic_base_revision + 1:
            raise ValueError(
                "semanticResultRevision must be exactly one greater than semanticBaseRevision"
            )

        expected_base_problem = (
            None if self.base_component is None else self.base_component.problem_spec
        )
        if self.base_problem_spec != expected_base_problem:
            raise ValueError("baseProblemSpec must match baseComponent problemSpec")
        if self.result_problem_spec != self.result_component.problem_spec:
            raise ValueError("resultProblemSpec must match resultComponent problemSpec")
        if self.result_problem_spec != self.beat.result_problem_spec:
            raise ValueError("resultProblemSpec must match routed beat resultProblemSpec")
        if self.base_component is not None and self.base_component.id != self.beat.component_id:
            raise ValueError("baseComponent id must match routed beat componentId")
        if self.result_component.id != self.beat.component_id:
            raise ValueError("resultComponent id must match routed beat componentId")

        expected_beat_base = self.beat.base_problem_spec
        if (
            expected_beat_base is None
            and self.checkpoint_id is not ProjectileMotionCheckpointId.SETUP
        ):
            expected_beat_base = self.beat.result_problem_spec
        if self.base_problem_spec != expected_beat_base:
            raise ValueError("baseProblemSpec must match the routed checkpoint transition")

        _validate_component_transition(
            action=self.action,
            checkpoint_id=self.checkpoint_id,
            clarification_topic=self.clarification_topic,
            base_component=self.base_component,
            result_component=self.result_component,
        )

        base_digest = _problem_digest(self.base_problem_spec)
        result_digest = projectile_motion_problem_sha256(self.result_problem_spec)
        if self.receipt.base_problem_spec_sha256 != base_digest:
            raise ValueError("receipt baseProblemSpecSha256 must match metadata baseProblemSpec")
        if not digest_matches(self.receipt.result_problem_spec_sha256, result_digest):
            raise ValueError(
                "receipt resultProblemSpecSha256 must match metadata resultProblemSpec"
            )

        body = self.certificate.body
        if body.base_problem_spec_sha256 != base_digest:
            raise ValueError(
                "certificate baseProblemSpecSha256 must match metadata baseProblemSpec"
            )
        if not digest_matches(body.result_problem_spec_sha256, result_digest):
            raise ValueError(
                "certificate resultProblemSpecSha256 must match metadata resultProblemSpec"
            )
        if body.base_semantic_revision != self.semantic_base_revision:
            raise ValueError("certificate baseSemanticRevision must match semanticBaseRevision")
        if body.result_semantic_revision != self.semantic_result_revision:
            raise ValueError("certificate resultSemanticRevision must match semanticResultRevision")
        if body.previous_certificate_sha256 != self.semantic_base_certificate_sha256:
            raise ValueError(
                "certificate previousCertificateSha256 must match semantic base chain head"
            )
        if not digest_matches(
            self.semantic_result_certificate_sha256,
            self.certificate.certificate_sha256,
        ):
            raise ValueError("semantic result chain head must match certificate certificateSha256")

        expected_base_scene_digest = _component_scene_digest(
            revision=self.semantic_base_revision,
            component=self.base_component,
        )
        if not digest_matches(body.base_semantic_scene_sha256, expected_base_scene_digest):
            raise ValueError(
                "certificate baseSemanticSceneSha256 must match metadata baseComponent"
            )
        expected_result_scene_digest = _component_scene_digest(
            revision=self.semantic_result_revision,
            component=self.result_component,
        )
        if not digest_matches(body.result_semantic_scene_sha256, expected_result_scene_digest):
            raise ValueError(
                "certificate resultSemanticSceneSha256 must match metadata resultComponent"
            )
        return self


class ProjectileChoreographySceneCheckpointEventV1(LiveSceneContract):
    """One authoritative projectile patch with its settled certified frontier."""

    type: Literal["projectile_choreography_scene_checkpoint"] = (
        "projectile_choreography_scene_checkpoint"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    sequence: ProjectileChoreographyCheckpointSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft
    semantic: ProjectileCheckpointSemanticMetadataV1

    @model_validator(mode="after")
    def validate_revision_and_checkpoint_binding(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("resultRevision must be exactly one greater than baseRevision")
        if self.semantic.semantic_base_revision != self.base_revision:
            raise ValueError("semantic and low-level base revisions must match")
        if self.semantic.semantic_result_revision != self.result_revision:
            raise ValueError("semantic and low-level result revisions must match")

        body = self.semantic.certificate.body
        if body.base_low_level_revision != self.base_revision:
            raise ValueError("certificate baseLowLevelRevision must match event baseRevision")
        if body.result_low_level_revision != self.result_revision:
            raise ValueError("certificate resultLowLevelRevision must match event resultRevision")
        if body.base_semantic_revision != self.semantic.semantic_base_revision:
            raise ValueError("certificate baseSemanticRevision must match semanticBaseRevision")
        if body.result_semantic_revision != self.semantic.semantic_result_revision:
            raise ValueError("certificate resultSemanticRevision must match semanticResultRevision")

        CompiledProjectileMotionCheckpointV1(
            beat=self.semantic.beat,
            action=self.semantic.action,
            checkpoint_id=self.semantic.checkpoint_id,
            clarification_topic=self.semantic.clarification_topic,
            patch=self.patch,
            receipt=self.semantic.receipt,
            presentation=self.semantic.presentation,
            choreography=self.semantic.choreography,
            certificate=self.semantic.certificate,
        )
        return self


class ProjectileChoreographySceneStreamDeclinedEventV1(LiveSceneContract):
    """Successful projectile terminal that preserves the accepted scene."""

    type: Literal["projectile_choreography_scene_stream_declined"] = (
        "projectile_choreography_scene_stream_declined"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    final_revision: NonNegativeRevision = Field(alias="finalRevision")
    reason_code: ProjectileChoreographyDeclineReason = Field(alias="reasonCode")
    message: FriendlyMessage


class ProjectileChoreographySceneStreamFailedEventV1(LiveSceneContract):
    """Failed projectile terminal with a closed retry-consistent code."""

    type: Literal["projectile_choreography_scene_stream_failed"] = (
        "projectile_choreography_scene_stream_failed"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    code: ProjectileChoreographyFailureCode
    message: FriendlyMessage
    last_accepted_revision: NonNegativeRevision = Field(alias="lastAcceptedRevision")
    retryable: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_retryable_semantics(self) -> Self:
        expected = self.code in _RETRYABLE_FAILURE_CODES
        if self.retryable is not expected:
            raise ValueError(
                f"retryable must be {str(expected).lower()} for code {self.code.value}"
            )
        return self


ProjectileChoreographySceneStreamEventV1: TypeAlias = Annotated[
    SceneStreamStartedEvent
    | ProjectileChoreographySceneCheckpointEventV1
    | ProjectileChoreographySceneStreamDeclinedEventV1
    | SceneStreamRepairingEvent
    | SceneStreamCompletedEvent
    | ProjectileChoreographySceneStreamFailedEventV1,
    Field(discriminator="type"),
]

PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER = TypeAdapter(
    ProjectileChoreographySceneStreamEventV1
)


def dump_projectile_choreography_scene_stream_event(
    event: ProjectileChoreographySceneStreamEventV1,
) -> dict[str, object]:
    """Return one canonical camelCase JSON-ready projectile event."""

    validated = PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
    return PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.dump_python(
        validated,
        mode="json",
        by_alias=True,
    )


__all__ = [
    "MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS",
    "PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER",
    "ProjectileCheckpointSemanticMetadataV1",
    "ProjectileChoreographyCheckpointSequence",
    "ProjectileChoreographyDeclineReason",
    "ProjectileChoreographyFailureCode",
    "ProjectileChoreographySceneCheckpointEventV1",
    "ProjectileChoreographySceneStreamDeclinedEventV1",
    "ProjectileChoreographySceneStreamEventV1",
    "ProjectileChoreographySceneStreamFailedEventV1",
    "dump_projectile_choreography_scene_stream_event",
]

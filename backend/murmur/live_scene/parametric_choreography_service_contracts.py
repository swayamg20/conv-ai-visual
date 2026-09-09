"""Exact service contracts for Gate 1.6 parametric choreography streams.

V3 checkpoint and terminal records are deliberately separate from the sealed
Gate 1.5 V2 wire.  A checkpoint exposes enough cleartext identity for the
browser to join the bound problem, semantic frontier, and certificate chain
without treating an unkeyed digest as proof of provenance.

The generic started, repairing, and completed events are reused because their
wire meaning is identical across protocols.  The generic failed event is not:
its failure code is intentionally open-ended, while this product boundary
requires a closed stable vocabulary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV3,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
    completing_square_problem_sha256,
)
from murmur.live_scene.completing_square_problem_parser import (
    CompletingSquareProblemFailureReason,
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
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointCompilerCertificateV3,
    CheckpointVerificationReceiptV3,
    CompiledCheckpointV3,
)
from murmur.live_scene.semantic_contracts import Sha256Digest, VisualActAbstainReason
from murmur.live_scene.semantic_integrity import digest_matches

MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS = MAX_ACCEPTED_PATCHES
ParametricChoreographyCheckpointSequence = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS),
]

ParametricChoreographyDeclineReason: TypeAlias = (
    VisualActAbstainReason | CompletingSquareProblemFailureReason
)


class ParametricChoreographyFailureCode(StrEnum):
    """Closed failures emitted by the V3 service boundary."""

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
        ParametricChoreographyFailureCode.INVALID_VISUAL_ACT,
        ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED,
        ParametricChoreographyFailureCode.PROVIDER_TIMEOUT,
        ParametricChoreographyFailureCode.PROVIDER_ERROR,
    }
)


class ParametricCheckpointSemanticMetadataV3(LiveSceneContract):
    """Problem-bound semantic frontier and integrity claim for one checkpoint."""

    problem_spec: CompletingSquareProblemSpecV1 = Field(alias="problemSpec")
    beat: RoutedChoreographyBeatV3
    checkpoint_id: CompletingSquareCheckpointId = Field(alias="checkpointId")
    result_component: ParametricCompletingSquareStateV1 = Field(alias="resultComponent")
    semantic_base_revision: NonNegativeRevision = Field(alias="semanticBaseRevision")
    semantic_result_revision: PositiveRevision = Field(alias="semanticResultRevision")
    semantic_base_certificate_sha256: Sha256Digest | None = Field(
        alias="semanticBaseCertificateSha256"
    )
    semantic_result_certificate_sha256: Sha256Digest = Field(
        alias="semanticResultCertificateSha256"
    )
    receipt: CheckpointVerificationReceiptV3
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV1
    certificate: CheckpointCompilerCertificateV3

    @model_validator(mode="after")
    def validate_problem_frontier_and_certificate_bindings(self) -> Self:
        if self.semantic_result_revision != self.semantic_base_revision + 1:
            raise ValueError(
                "semanticResultRevision must be exactly one greater than semanticBaseRevision"
            )
        if self.problem_spec != self.beat.problem_spec:
            raise ValueError("problemSpec must match routed beat problemSpec")
        if self.result_component.id != self.beat.component_id:
            raise ValueError("resultComponent id must match routed beat componentId")
        if self.result_component.problem_spec != self.problem_spec:
            raise ValueError("resultComponent problemSpec must match metadata problemSpec")

        if self.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
            if (
                self.result_component.last_main_checkpoint
                is not CompletingSquareMainCheckpoint.MISSING_CORNER
                or not self.result_component.corner_clarified
            ):
                raise ValueError(
                    "corner_detail resultComponent must remain at missing_corner with "
                    "cornerClarified true"
                )
        else:
            expected_frontier = CompletingSquareMainCheckpoint(self.checkpoint_id.value)
            if self.result_component.last_main_checkpoint is not expected_frontier:
                raise ValueError(
                    "main checkpoint resultComponent lastMainCheckpoint must match checkpointId"
                )

        problem_digest = completing_square_problem_sha256(self.problem_spec)
        if not digest_matches(self.receipt.problem_spec_sha256, problem_digest):
            raise ValueError("receipt problemSpecSha256 must match metadata problemSpec")

        body = self.certificate.body
        if not digest_matches(body.problem_spec_sha256, problem_digest):
            raise ValueError("certificate problemSpecSha256 must match metadata problemSpec")
        if body.base_revision != self.semantic_base_revision:
            raise ValueError("certificate baseRevision must match semanticBaseRevision")
        if body.result_revision != self.semantic_result_revision:
            raise ValueError("certificate resultRevision must match semanticResultRevision")
        if body.component_id != self.result_component.id:
            raise ValueError("certificate componentId must match resultComponent id")
        if body.previous_certificate_sha256 != self.semantic_base_certificate_sha256:
            raise ValueError(
                "certificate previousCertificateSha256 must match semantic base chain head"
            )
        if not digest_matches(
            self.semantic_result_certificate_sha256,
            self.certificate.certificate_sha256,
        ):
            raise ValueError("semantic result chain head must match certificate certificateSha256")
        return self


class ParametricChoreographySceneCheckpointEventV3(LiveSceneContract):
    """One authoritative V3 patch with a certified settled checkpoint."""

    type: Literal["parametric_choreography_scene_checkpoint"] = (
        "parametric_choreography_scene_checkpoint"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    sequence: ParametricChoreographyCheckpointSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft
    semantic: ParametricCheckpointSemanticMetadataV3

    @model_validator(mode="after")
    def validate_revision_and_checkpoint_binding(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("resultRevision must be exactly one greater than baseRevision")
        if self.semantic.semantic_base_revision != self.base_revision:
            raise ValueError("semantic and low-level base revisions must match")
        if self.semantic.semantic_result_revision != self.result_revision:
            raise ValueError("semantic and low-level result revisions must match")

        body = self.semantic.certificate.body
        if body.base_revision != self.base_revision:
            raise ValueError("certificate baseRevision must match event baseRevision")
        if body.result_revision != self.result_revision:
            raise ValueError("certificate resultRevision must match event resultRevision")

        CompiledCheckpointV3(
            beat=self.semantic.beat,
            checkpoint_id=self.semantic.checkpoint_id,
            patch=self.patch,
            receipt=self.semantic.receipt,
            presentation=self.semantic.presentation,
            choreography=self.semantic.choreography,
            certificate=self.semantic.certificate,
        )
        return self


class ParametricChoreographySceneStreamDeclinedEventV3(LiveSceneContract):
    """Successful V3 terminal that deliberately preserves the accepted scene."""

    type: Literal["parametric_choreography_scene_stream_declined"] = (
        "parametric_choreography_scene_stream_declined"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    final_revision: NonNegativeRevision = Field(alias="finalRevision")
    reason_code: ParametricChoreographyDeclineReason = Field(alias="reasonCode")
    message: FriendlyMessage


class ParametricChoreographySceneStreamFailedEventV3(LiveSceneContract):
    """Failed V3 terminal with one closed, retry-consistent code."""

    type: Literal["parametric_choreography_scene_stream_failed"] = (
        "parametric_choreography_scene_stream_failed"
    )
    generation: PositiveSequence
    attempt: AttemptNumber
    code: ParametricChoreographyFailureCode
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


ParametricChoreographySceneStreamEventV3: TypeAlias = Annotated[
    SceneStreamStartedEvent
    | ParametricChoreographySceneCheckpointEventV3
    | ParametricChoreographySceneStreamDeclinedEventV3
    | SceneStreamRepairingEvent
    | SceneStreamCompletedEvent
    | ParametricChoreographySceneStreamFailedEventV3,
    Field(discriminator="type"),
]

PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER = TypeAdapter(
    ParametricChoreographySceneStreamEventV3
)


def dump_parametric_choreography_scene_stream_event(
    event: ParametricChoreographySceneStreamEventV3,
) -> dict[str, object]:
    """Return one canonical camelCase JSON-ready V3 event."""

    validated = PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
    return PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.dump_python(
        validated,
        mode="json",
        by_alias=True,
    )


__all__ = [
    "MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS",
    "PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER",
    "ParametricCheckpointSemanticMetadataV3",
    "ParametricChoreographyCheckpointSequence",
    "ParametricChoreographyDeclineReason",
    "ParametricChoreographyFailureCode",
    "ParametricChoreographySceneCheckpointEventV3",
    "ParametricChoreographySceneStreamDeclinedEventV3",
    "ParametricChoreographySceneStreamEventV3",
    "ParametricChoreographySceneStreamFailedEventV3",
    "dump_parametric_choreography_scene_stream_event",
]

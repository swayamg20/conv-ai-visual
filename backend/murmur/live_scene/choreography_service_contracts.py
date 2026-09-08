"""Strict service contracts for compiler-owned choreography checkpoints.

Gate 1.5 keeps this stream discriminator separate from both raw V1 patches and
semantic V1 atoms.  Each checkpoint event carries the complete V2 compiler
claim so validation can fail closed before the event reaches the browser.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.checkpoint_contracts import (
    CheckpointCompilerCertificateV2,
    CheckpointVerificationReceiptV2,
    CompiledCheckpointV2,
)
from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
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
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_contracts import VisualActAbstainReason

MAX_CHOREOGRAPHY_CHECKPOINTS = MAX_ACCEPTED_PATCHES
ChoreographyCheckpointSequence = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_CHOREOGRAPHY_CHECKPOINTS),
]


class CheckpointSemanticMetadataV2(LiveSceneContract):
    """Semantic frontier and complete integrity claim for one checkpoint."""

    beat: RoutedChoreographyBeatV2
    checkpoint_id: CompletingSquareCheckpointId = Field(alias="checkpointId")
    result_component: CompletingSquareState = Field(alias="resultComponent")
    semantic_base_revision: NonNegativeRevision = Field(alias="semanticBaseRevision")
    semantic_result_revision: PositiveRevision = Field(alias="semanticResultRevision")
    receipt: CheckpointVerificationReceiptV2
    presentation: PresentationCheckpointV1
    choreography: ChoreographyPlanV1
    certificate: CheckpointCompilerCertificateV2

    @model_validator(mode="after")
    def validate_frontier_and_certificate_bindings(self) -> Self:
        if self.semantic_result_revision != self.semantic_base_revision + 1:
            raise ValueError(
                "semanticResultRevision must be exactly one greater than semanticBaseRevision"
            )
        if self.result_component.id != self.beat.component_id:
            raise ValueError("resultComponent id must match routed beat componentId")

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

        body = self.certificate.body
        if body.base_revision != self.semantic_base_revision:
            raise ValueError("certificate baseRevision must match semanticBaseRevision")
        if body.result_revision != self.semantic_result_revision:
            raise ValueError("certificate resultRevision must match semanticResultRevision")
        if body.component_id != self.result_component.id:
            raise ValueError("certificate componentId must match resultComponent id")
        return self


class ChoreographySceneCheckpointEvent(LiveSceneContract):
    """One authoritative low-level patch with a certified settled checkpoint."""

    type: Literal["choreography_scene_checkpoint"] = "choreography_scene_checkpoint"
    generation: PositiveSequence
    attempt: AttemptNumber
    sequence: ChoreographyCheckpointSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft
    semantic: CheckpointSemanticMetadataV2

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

        CompiledCheckpointV2(
            beat=self.semantic.beat,
            checkpoint_id=self.semantic.checkpoint_id,
            patch=self.patch,
            receipt=self.semantic.receipt,
            presentation=self.semantic.presentation,
            choreography=self.semantic.choreography,
            certificate=self.semantic.certificate,
        )
        return self


class ChoreographySceneStreamDeclinedEvent(LiveSceneContract):
    """Successful choreography terminal that deliberately leaves the scene unchanged."""

    type: Literal["choreography_scene_stream_declined"] = "choreography_scene_stream_declined"
    generation: PositiveSequence
    attempt: AttemptNumber
    final_revision: NonNegativeRevision = Field(alias="finalRevision")
    reason_code: VisualActAbstainReason = Field(alias="reasonCode")
    message: FriendlyMessage


ChoreographySceneStreamEvent: TypeAlias = Annotated[
    SceneStreamStartedEvent
    | ChoreographySceneCheckpointEvent
    | ChoreographySceneStreamDeclinedEvent
    | SceneStreamRepairingEvent
    | SceneStreamCompletedEvent
    | SceneStreamFailedEvent,
    Field(discriminator="type"),
]

CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER = TypeAdapter(ChoreographySceneStreamEvent)


def dump_choreography_scene_stream_event(
    event: ChoreographySceneStreamEvent,
) -> dict[str, object]:
    """Return the canonical camelCase JSON-ready choreography event."""

    validated = CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
    return CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.dump_python(
        validated,
        mode="json",
        by_alias=True,
    )


__all__ = [
    "CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER",
    "MAX_CHOREOGRAPHY_CHECKPOINTS",
    "CheckpointSemanticMetadataV2",
    "ChoreographyCheckpointSequence",
    "ChoreographySceneCheckpointEvent",
    "ChoreographySceneStreamDeclinedEvent",
    "ChoreographySceneStreamEvent",
    "dump_choreography_scene_stream_event",
]

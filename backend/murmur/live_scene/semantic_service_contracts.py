"""Strict service and wire contracts for semantic live-scene authoring.

The raw ``ScenePatchEvent`` contract remains unchanged.  Semantic authoring has
its own patch-event discriminator and typed metadata so a consumer cannot
mistake an unverified raw patch for a compiler-certified visual atom.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.contracts import (
    AttemptNumber,
    FriendlyMessage,
    LiveSceneContract,
    NonNegativeRevision,
    PositiveRevision,
    PositiveSequence,
    PromptText,
    ScenePatchDraft,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    AtomId,
    CompiledVisualAtom,
    CompilerCertificateV1,
    PythagoreanRole,
    SemanticComponentId,
    SemanticSceneState,
    TeachingBeatDraft,
    VerificationReceipt,
    VisualActAbstainReason,
    roles_through,
    teaching_beat_sha256,
    verification_receipt_sha256,
)
from murmur.live_scene.semantic_integrity import digest_matches

MAX_COMPILED_ATOMS = len(PYTHAGOREAN_ROLE_ORDER)
SemanticAtomSequence = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_COMPILED_ATOMS),
]


class SemanticLiveSceneRequest(LiveSceneContract):
    """One request carrying lockstep low-level and semantic base snapshots."""

    prompt: PromptText
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")

    @model_validator(mode="after")
    def validate_lockstep_revisions(self) -> Self:
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("baseScene and baseSemanticScene revisions must match")
        return self


class SemanticAtomMetadata(LiveSceneContract):
    """Typed semantic meaning and evidence attached to one low-level patch."""

    beat: TeachingBeatDraft
    atom_id: AtomId = Field(alias="atomId")
    component_id: SemanticComponentId = Field(alias="componentId")
    role: PythagoreanRole
    atom_ordinal: PositiveSequence = Field(alias="atomOrdinal")
    semantic_base_revision: NonNegativeRevision = Field(alias="semanticBaseRevision")
    semantic_result_revision: PositiveRevision = Field(alias="semanticResultRevision")
    receipt: VerificationReceipt
    certificate: CompilerCertificateV1

    @model_validator(mode="after")
    def validate_certificate_binding(self) -> Self:
        body = self.certificate.body
        if self.beat.directive.id != self.component_id:
            raise ValueError("semantic beat directive id must match the atom componentId")
        if self.atom_ordinal > len(roles_through(self.beat.directive.reveal_through)):
            raise ValueError("semantic atomOrdinal must be within the beat revealThrough prefix")
        if body.atom_id != self.atom_id:
            raise ValueError("semantic atomId must match its certificate")
        if body.beat_id != self.beat.beat_id:
            raise ValueError("semantic beatId must match its certificate")
        if not digest_matches(body.beat_sha256, teaching_beat_sha256(self.beat)):
            raise ValueError("semantic beat must match its certificate beatSha256")
        if body.component_id != self.component_id:
            raise ValueError("semantic componentId must match its certificate")
        if body.role != self.role:
            raise ValueError("semantic role must match its certificate")
        if body.atom_ordinal != self.atom_ordinal:
            raise ValueError("semantic atomOrdinal must match its certificate")
        if body.base_semantic_revision != self.semantic_base_revision:
            raise ValueError("semantic base revision must match its certificate")
        if body.result_semantic_revision != self.semantic_result_revision:
            raise ValueError("semantic result revision must match its certificate")
        if self.receipt.component_id != self.component_id or self.receipt.role != self.role:
            raise ValueError("semantic receipt owner must match the atom metadata")
        if not digest_matches(
            body.receipt_sha256,
            verification_receipt_sha256(self.receipt),
        ):
            raise ValueError("semantic receipt must match its certificate receiptSha256")
        return self


class SemanticScenePatchEvent(LiveSceneContract):
    """One authoritative low-level patch with compiler-certified semantic metadata."""

    type: Literal["semantic_scene_patch"] = "semantic_scene_patch"
    generation: PositiveSequence
    attempt: AttemptNumber
    sequence: SemanticAtomSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft
    semantic: SemanticAtomMetadata

    @model_validator(mode="after")
    def validate_revision_and_atom_binding(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("resultRevision must be exactly one greater than baseRevision")
        if self.patch.narration != self.semantic.beat.narration:
            raise ValueError("patch narration must match semantic beat narration")
        if self.semantic.semantic_base_revision != self.base_revision:
            raise ValueError("semantic and low-level base revisions must match")
        if self.semantic.semantic_result_revision != self.result_revision:
            raise ValueError("semantic and low-level result revisions must match")
        CompiledVisualAtom(
            atom_id=self.semantic.atom_id,
            beat_id=self.semantic.beat.beat_id,
            component_id=self.semantic.component_id,
            role=self.semantic.role,
            patch=self.patch,
            receipt=self.semantic.receipt,
            certificate=self.semantic.certificate,
        )
        return self


class SemanticSceneStreamDeclinedEvent(LiveSceneContract):
    """Successful semantic terminal that deliberately leaves the scene unchanged."""

    type: Literal["semantic_scene_stream_declined"] = "semantic_scene_stream_declined"
    generation: PositiveSequence
    attempt: AttemptNumber
    final_revision: NonNegativeRevision = Field(alias="finalRevision")
    reason_code: VisualActAbstainReason = Field(alias="reasonCode")
    message: FriendlyMessage


SemanticSceneStreamEvent: TypeAlias = Annotated[
    SceneStreamStartedEvent
    | SemanticScenePatchEvent
    | SemanticSceneStreamDeclinedEvent
    | SceneStreamRepairingEvent
    | SceneStreamCompletedEvent
    | SceneStreamFailedEvent,
    Field(discriminator="type"),
]

SEMANTIC_SCENE_STREAM_EVENT_ADAPTER = TypeAdapter(SemanticSceneStreamEvent)


def dump_semantic_scene_stream_event(event: SemanticSceneStreamEvent) -> dict[str, object]:
    """Return the canonical camelCase JSON-ready semantic event representation."""

    validated = SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
    return SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.dump_python(
        validated,
        mode="json",
        by_alias=True,
    )


__all__ = [
    "MAX_COMPILED_ATOMS",
    "SEMANTIC_SCENE_STREAM_EVENT_ADAPTER",
    "SemanticAtomMetadata",
    "SemanticAtomSequence",
    "SemanticLiveSceneRequest",
    "SemanticScenePatchEvent",
    "SemanticSceneStreamDeclinedEvent",
    "SemanticSceneStreamEvent",
    "dump_semantic_scene_stream_event",
]

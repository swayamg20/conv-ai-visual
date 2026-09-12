"""Strict lifecycle contracts for Gate 1.8 semantic storyboard streams.

Every event discriminator is private to the storyboard protocol.  In
particular, the protocol does not reuse generic lifecycle records that an
older decoder could accept accidentally.  Checkpoint records carry the full
self-revalidating transition produced by the atomic compiler while retaining
a top-level patch for the browser playback boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.contracts import (
    FriendlyMessage,
    LiveSceneContract,
    Milliseconds,
    NonNegativeRevision,
    PositiveRevision,
    PositiveSequence,
    ScenePatchDraft,
)
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    ValidatedSemanticStoryboardTransitionV1,
)
from murmur.live_scene.semantic_storyboard_checkpoint_contracts import (
    SemanticStoryboardCheckpointOrigin,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
    StoryboardAbstainReasonCode,
)

MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS = MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN
SemanticStoryboardAttempt = Annotated[int, Field(strict=True, ge=1, le=1)]
SemanticStoryboardCheckpointSequence = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
    ),
]
SemanticStoryboardCheckpointCount = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS,
    ),
]


class SemanticStoryboardCompletionReason(StrEnum):
    """Closed successful terminal modes for one storyboard generation."""

    ANCHOR = "anchor"
    MODEL_STOP = "model_stop"
    ACCEPTED_PREFIX = "accepted_prefix"


class SemanticStoryboardAcceptedPrefixCause(StrEnum):
    """Sanitized reasons a verified model prefix ended before a clean stop."""

    INVALID_MODEL_STREAM = "invalid_model_stream"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    CAPACITY_LIMIT = "capacity_limit"
    REVISION_LIMIT = "revision_limit"
    INTERNAL_INTEGRITY_ERROR = "internal_integrity_error"


class SemanticStoryboardFailureCode(StrEnum):
    """Closed failures allowed before a generation publishes a checkpoint."""

    SEMANTIC_BASE_MISMATCH = "semantic_base_mismatch"
    STORYBOARD_CAPACITY_EXHAUSTED = "storyboard_capacity_exhausted"
    REVISION_LIMIT = "revision_limit"
    CONTEXT_TOO_LARGE = "context_too_large"
    INVALID_MODEL_STREAM = "invalid_model_stream"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    STORYBOARD_INTEGRITY_ERROR = "storyboard_integrity_error"


_RETRYABLE_FAILURE_CODES = frozenset(
    {
        SemanticStoryboardFailureCode.INVALID_MODEL_STREAM,
        SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED,
        SemanticStoryboardFailureCode.PROVIDER_TIMEOUT,
        SemanticStoryboardFailureCode.PROVIDER_ERROR,
    }
)


class SemanticStoryboardSceneStreamStartedEventV1(LiveSceneContract):
    """Open one isolated Reflex or Director storyboard generation."""

    type: Literal["semantic_storyboard_scene_stream_started"] = (
        "semantic_storyboard_scene_stream_started"
    )
    generation: PositiveSequence
    attempt: SemanticStoryboardAttempt
    base_revision: NonNegativeRevision = Field(alias="baseRevision")


class SemanticStoryboardSceneCheckpointEventV1(LiveSceneContract):
    """One exact certified transition ready for browser presentation."""

    type: Literal["semantic_storyboard_scene_checkpoint"] = "semantic_storyboard_scene_checkpoint"
    generation: PositiveSequence
    attempt: SemanticStoryboardAttempt
    sequence: SemanticStoryboardCheckpointSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft
    transition: ValidatedSemanticStoryboardTransitionV1

    @model_validator(mode="after")
    def validate_atomic_transition(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("resultRevision must advance baseRevision exactly once")
        if self.transition.base_scene.revision != self.base_revision:
            raise ValueError("transition baseScene revision must match event baseRevision")
        if self.transition.result_scene.revision != self.result_revision:
            raise ValueError("transition resultScene revision must match event resultRevision")
        if self.transition.base_semantic_scene.revision != self.base_revision:
            raise ValueError("transition baseSemanticScene revision must match event baseRevision")
        if self.transition.result_semantic_scene.revision != self.result_revision:
            raise ValueError(
                "transition resultSemanticScene revision must match event resultRevision"
            )
        if self.patch != self.transition.checkpoint.patch:
            raise ValueError("event patch must match the certified transition patch")

        origin = self.transition.checkpoint.checkpoint_origin
        if origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
            if self.sequence != 1:
                raise ValueError("anchor checkpoint sequence must be one")
            if self.base_revision != 0 or self.result_revision != 1:
                raise ValueError("anchor checkpoint must advance revision zero to one")
        elif self.base_revision < 1:
            raise ValueError("model checkpoint requires the accepted anchor revision")
        return self


class SemanticStoryboardSceneStreamCompletedEventV1(LiveSceneContract):
    """Successful anchor, clean model stop, or retained accepted prefix."""

    type: Literal["semantic_storyboard_scene_stream_completed"] = (
        "semantic_storyboard_scene_stream_completed"
    )
    generation: PositiveSequence
    attempt: SemanticStoryboardAttempt
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    final_revision: PositiveRevision = Field(alias="finalRevision")
    checkpoint_count: SemanticStoryboardCheckpointCount = Field(alias="checkpointCount")
    first_checkpoint_ms: Milliseconds = Field(alias="firstCheckpointMs")
    total_ms: Milliseconds = Field(alias="totalMs")
    reason_code: SemanticStoryboardCompletionReason = Field(alias="reasonCode")
    accepted_prefix_cause: SemanticStoryboardAcceptedPrefixCause | None = Field(
        default=None,
        alias="acceptedPrefixCause",
    )

    @model_validator(mode="after")
    def validate_terminal_semantics(self) -> Self:
        if self.final_revision != self.base_revision + self.checkpoint_count:
            raise ValueError("finalRevision must equal baseRevision plus checkpointCount")
        if self.total_ms < self.first_checkpoint_ms:
            raise ValueError("totalMs must not be less than firstCheckpointMs")

        if self.reason_code is SemanticStoryboardCompletionReason.ANCHOR:
            if self.base_revision != 0 or self.final_revision != 1 or self.checkpoint_count != 1:
                raise ValueError("anchor completion must publish exactly revision zero to one")
            if self.accepted_prefix_cause is not None:
                raise ValueError("anchor completion forbids acceptedPrefixCause")
            return self

        if self.base_revision < 1:
            raise ValueError("model completion requires the accepted anchor revision")
        if self.reason_code is SemanticStoryboardCompletionReason.ACCEPTED_PREFIX:
            if self.accepted_prefix_cause is None:
                raise ValueError("accepted_prefix completion requires acceptedPrefixCause")
        elif self.accepted_prefix_cause is not None:
            raise ValueError("model_stop completion forbids acceptedPrefixCause")
        return self


class SemanticStoryboardSceneStreamDeclinedEventV1(LiveSceneContract):
    """A sole clean model abstention that leaves the certified frontier intact."""

    type: Literal["semantic_storyboard_scene_stream_declined"] = (
        "semantic_storyboard_scene_stream_declined"
    )
    generation: PositiveSequence
    attempt: SemanticStoryboardAttempt
    base_revision: PositiveRevision = Field(alias="baseRevision")
    final_revision: PositiveRevision = Field(alias="finalRevision")
    reason_code: StoryboardAbstainReasonCode = Field(alias="reasonCode")
    message: FriendlyMessage

    @model_validator(mode="after")
    def validate_no_mutation(self) -> Self:
        if self.final_revision != self.base_revision:
            raise ValueError("declined storyboard generation must not change revision")
        return self


class SemanticStoryboardSceneStreamFailedEventV1(LiveSceneContract):
    """A pre-checkpoint failure with stable, retry-consistent semantics."""

    type: Literal["semantic_storyboard_scene_stream_failed"] = (
        "semantic_storyboard_scene_stream_failed"
    )
    generation: PositiveSequence
    attempt: SemanticStoryboardAttempt
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    code: SemanticStoryboardFailureCode
    message: FriendlyMessage
    last_accepted_revision: NonNegativeRevision = Field(alias="lastAcceptedRevision")
    retryable: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_failure_semantics(self) -> Self:
        if self.last_accepted_revision != self.base_revision:
            raise ValueError("failed storyboard generation cannot follow an accepted checkpoint")
        expected = self.code in _RETRYABLE_FAILURE_CODES
        if self.retryable is not expected:
            raise ValueError(
                f"retryable must be {str(expected).lower()} for code {self.code.value}"
            )
        return self


SemanticStoryboardSceneStreamEventV1: TypeAlias = Annotated[
    SemanticStoryboardSceneStreamStartedEventV1
    | SemanticStoryboardSceneCheckpointEventV1
    | SemanticStoryboardSceneStreamCompletedEventV1
    | SemanticStoryboardSceneStreamDeclinedEventV1
    | SemanticStoryboardSceneStreamFailedEventV1,
    Field(discriminator="type"),
]

_SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_RAW_ADAPTER = TypeAdapter(
    SemanticStoryboardSceneStreamEventV1
)


class _SemanticStoryboardSceneStreamEventWireAdapter:
    """Validate canonical aliases recursively at the untrusted wire boundary."""

    _adapter = _SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_RAW_ADAPTER

    def validate_python(
        self,
        value: object,
        *,
        by_alias: bool = True,
        by_name: bool = False,
    ) -> SemanticStoryboardSceneStreamEventV1:
        if by_alias is not True or by_name is not False:
            raise ValueError("storyboard event wire validation requires canonical aliases")
        return self._adapter.validate_python(value, by_alias=True, by_name=False)

    def validate_json(
        self,
        value: str | bytes | bytearray,
        *,
        by_alias: bool = True,
        by_name: bool = False,
    ) -> SemanticStoryboardSceneStreamEventV1:
        if by_alias is not True or by_name is not False:
            raise ValueError("storyboard event wire validation requires canonical aliases")
        return self._adapter.validate_json(value, by_alias=True, by_name=False)

    def dump_python(
        self,
        value: SemanticStoryboardSceneStreamEventV1,
    ) -> dict[str, object]:
        return self._adapter.dump_python(value, mode="json", by_alias=True)


SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER = _SemanticStoryboardSceneStreamEventWireAdapter()


def dump_semantic_storyboard_scene_stream_event(
    event: SemanticStoryboardSceneStreamEventV1,
) -> dict[str, object]:
    """Return one canonical camelCase JSON-ready storyboard event."""

    validated = SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
    return SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.dump_python(validated)


__all__ = [
    "MAX_SEMANTIC_STORYBOARD_STREAM_CHECKPOINTS",
    "SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER",
    "SemanticStoryboardAcceptedPrefixCause",
    "SemanticStoryboardAttempt",
    "SemanticStoryboardCheckpointCount",
    "SemanticStoryboardCheckpointSequence",
    "SemanticStoryboardCompletionReason",
    "SemanticStoryboardFailureCode",
    "SemanticStoryboardSceneCheckpointEventV1",
    "SemanticStoryboardSceneStreamCompletedEventV1",
    "SemanticStoryboardSceneStreamDeclinedEventV1",
    "SemanticStoryboardSceneStreamEventV1",
    "SemanticStoryboardSceneStreamFailedEventV1",
    "SemanticStoryboardSceneStreamStartedEventV1",
    "dump_semantic_storyboard_scene_stream_event",
]

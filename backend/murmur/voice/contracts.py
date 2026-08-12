"""Versioned, transport-neutral contracts for the Voice V2 event stream."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.types import StringConstraints

from murmur.core.immutable_json import freeze_json, thaw_json

VOICE_EVENT_SCHEMA_VERSION = 1
MAX_SAFE_SEQUENCE = 9_007_199_254_740_991

ContractId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
PositiveSequence = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_SEQUENCE)]
NonNegativeRevision = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_SEQUENCE)]
PayloadText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=16_000),
]
TranscriptText = Annotated[str, StringConstraints(strict=True, max_length=64_000)]
ProfileConfigHash = Annotated[
    str,
    StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
ProviderModelName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]


class EventPayload(BaseModel):
    """Fail-closed base for event-specific JSON payloads."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: object) -> object:
        if isinstance(value, Mapping):
            null_fields = sorted(key for key, child in value.items() if child is None)
            if null_fields:
                raise ValueError(
                    "optional payload fields must be omitted instead of null: "
                    + ", ".join(null_fields)
                )
        return value


class EmptyPayload(EventPayload):
    pass


class TransportConnectedPayload(EventPayload):
    connection_id: ContractId | None = None


class TransportReconnectingPayload(EventPayload):
    attempt: PositiveSequence
    reason: PayloadText | None = None


class TransportDisconnectedPayload(EventPayload):
    recoverable: StrictBool
    reason: PayloadText | None = None


class ProviderModelPayload(EventPayload):
    component: ContractId
    provider: ProviderModelName
    model: ProviderModelName

    @field_validator("provider", "model")
    @classmethod
    def reject_blank_names(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider and model names must not be blank")
        return value


class AgentReadyPayload(EventPayload):
    REQUIRED_COMPONENTS: ClassVar[frozenset[str]] = frozenset(
        {"worker", "input", "output", "event_channel"}
    )

    profile_id: ContractId
    required_components: Annotated[tuple[ContractId, ...], Field(min_length=1)]
    ready_components: Annotated[tuple[ContractId, ...], Field(min_length=1)]
    # Schema v1 originally omitted provider metadata. Decoders retain that
    # legacy shape, while current workers always publish these fields together.
    profile_config_hash: ProfileConfigHash | None = None
    provider_models: tuple[ProviderModelPayload, ...] | None = None

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        required = set(self.required_components)
        ready = set(self.ready_components)
        if len(required) != len(self.required_components):
            raise ValueError("required_components must be unique")
        if len(ready) != len(self.ready_components):
            raise ValueError("ready_components must be unique")
        if not self.REQUIRED_COMPONENTS.issubset(required):
            raise ValueError("required_components is missing a core voice component")
        if not required.issubset(ready):
            raise ValueError("every required component must be ready")
        if (self.profile_config_hash is None) != (self.provider_models is None):
            raise ValueError("profile_config_hash and provider_models must be supplied together")
        provider_components = [descriptor.component for descriptor in (self.provider_models or ())]
        if len(provider_components) != len(set(provider_components)):
            raise ValueError("provider_models components must be unique")
        if not set(provider_components).issubset(ready):
            raise ValueError("provider_models described a component that is not ready")
        return self


class FailurePayload(EventPayload):
    code: ContractId
    message: PayloadText
    retryable: StrictBool


class TranscriptSegmentPayload(EventPayload):
    segment_id: ContractId
    text: TranscriptText
    is_final: StrictBool


class TurnCommittedPayload(EventPayload):
    text: PayloadText


class OptionalReasonPayload(EventPayload):
    reason: PayloadText | None = None


class AssistantSpeechStartedPayload(EventPayload):
    speech_id: ContractId
    text: PayloadText | None = None


class AssistantSpeechStoppedPayload(EventPayload):
    speech_id: ContractId
    reason: Literal["completed", "interrupted", "cancelled", "error"]


class TaskQueuedPayload(EventPayload):
    label: PayloadText | None = None


class TaskWorkingPayload(EventPayload):
    message: PayloadText | None = None
    progress: Annotated[float, Field(strict=True, ge=0, le=1)] | None = None


class TaskNeedsInputPayload(EventPayload):
    prompt: PayloadText


class TaskVerifiedPayload(EventPayload):
    result_id: ContractId


class TaskSupersededPayload(OptionalReasonPayload):
    superseded_by_task_id: ContractId | None = None


class ArtifactProposedPayload(EventPayload):
    artifact_id: ContractId
    artifact_kind: ContractId


class ArtifactIdPayload(EventPayload):
    artifact_id: ContractId


class ArtifactRejectedPayload(ArtifactIdPayload):
    code: ContractId
    message: PayloadText


class CanvasPatchPayload(ArtifactIdPayload):
    artifact: Mapping[str, JsonValue]


class CanvasRenderFailedPayload(ArtifactRejectedPayload):
    pass


class UsageRecordedPayload(EventPayload):
    usage_id: ContractId
    category: ContractId
    quantity: Annotated[float, Field(strict=True, ge=0)]
    unit: ContractId
    estimated_cost_usd: Annotated[float, Field(strict=True, ge=0)] | None = None


class EventType(str, Enum):
    """Closed Voice V2 event vocabulary for the first contract version."""

    SESSION_STARTING = "session_starting"
    SESSION_STARTED = "session_started"
    TRANSPORT_CONNECTED = "transport_connected"
    TRANSPORT_RECONNECTING = "transport_reconnecting"
    TRANSPORT_DISCONNECTED = "transport_disconnected"
    AGENT_READY = "agent_ready"
    AGENT_UNAVAILABLE = "agent_unavailable"
    TRANSCRIPT_SEGMENT = "transcript_segment"
    TURN_COMMITTED = "turn_committed"
    TURN_RESUMED = "turn_resumed"
    ASSISTANT_SPEECH_STARTED = "assistant_speech_started"
    ASSISTANT_SPEECH_STOPPED = "assistant_speech_stopped"
    TASK_QUEUED = "task_queued"
    TASK_WORKING = "task_working"
    TASK_NEEDS_INPUT = "task_needs_input"
    TASK_VERIFIED = "task_verified"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    TASK_SUPERSEDED = "task_superseded"
    ARTIFACT_PROPOSED = "artifact_proposed"
    ARTIFACT_ACCEPTED = "artifact_accepted"
    ARTIFACT_REJECTED = "artifact_rejected"
    CANVAS_PATCH = "canvas_patch"
    CANVAS_APPLY_ACK = "canvas_apply_ack"
    CANVAS_FIRST_VISIBLE = "canvas_first_visible"
    CANVAS_ANIMATION_COMPLETE = "canvas_animation_complete"
    CANVAS_RENDER_FAILED = "canvas_render_failed"
    USAGE_RECORDED = "usage_recorded"
    SESSION_ENDING = "session_ending"
    SESSION_ENDED = "session_ended"


_TURN_SCOPED_EVENTS = frozenset(
    {
        EventType.TURN_COMMITTED,
        EventType.TURN_RESUMED,
        EventType.ASSISTANT_SPEECH_STARTED,
        EventType.ASSISTANT_SPEECH_STOPPED,
    }
)
_TASK_SCOPED_EVENTS = frozenset(
    {
        EventType.TASK_QUEUED,
        EventType.TASK_WORKING,
        EventType.TASK_NEEDS_INPUT,
        EventType.TASK_VERIFIED,
        EventType.TASK_FAILED,
        EventType.TASK_CANCELLED,
        EventType.TASK_SUPERSEDED,
        EventType.ARTIFACT_PROPOSED,
        EventType.ARTIFACT_ACCEPTED,
        EventType.ARTIFACT_REJECTED,
        EventType.CANVAS_PATCH,
        EventType.CANVAS_APPLY_ACK,
        EventType.CANVAS_FIRST_VISIBLE,
        EventType.CANVAS_ANIMATION_COMPLETE,
        EventType.CANVAS_RENDER_FAILED,
    }
)

_CANVAS_RESULT_EVENTS = frozenset(
    {
        EventType.CANVAS_APPLY_ACK,
        EventType.CANVAS_FIRST_VISIBLE,
        EventType.CANVAS_ANIMATION_COMPLETE,
        EventType.CANVAS_RENDER_FAILED,
    }
)

_PAYLOAD_MODELS: Mapping[EventType, type[EventPayload]] = {
    EventType.SESSION_STARTING: EmptyPayload,
    EventType.SESSION_STARTED: EmptyPayload,
    EventType.TRANSPORT_CONNECTED: TransportConnectedPayload,
    EventType.TRANSPORT_RECONNECTING: TransportReconnectingPayload,
    EventType.TRANSPORT_DISCONNECTED: TransportDisconnectedPayload,
    EventType.AGENT_READY: AgentReadyPayload,
    EventType.AGENT_UNAVAILABLE: FailurePayload,
    EventType.TRANSCRIPT_SEGMENT: TranscriptSegmentPayload,
    EventType.TURN_COMMITTED: TurnCommittedPayload,
    EventType.TURN_RESUMED: OptionalReasonPayload,
    EventType.ASSISTANT_SPEECH_STARTED: AssistantSpeechStartedPayload,
    EventType.ASSISTANT_SPEECH_STOPPED: AssistantSpeechStoppedPayload,
    EventType.TASK_QUEUED: TaskQueuedPayload,
    EventType.TASK_WORKING: TaskWorkingPayload,
    EventType.TASK_NEEDS_INPUT: TaskNeedsInputPayload,
    EventType.TASK_VERIFIED: TaskVerifiedPayload,
    EventType.TASK_FAILED: FailurePayload,
    EventType.TASK_CANCELLED: OptionalReasonPayload,
    EventType.TASK_SUPERSEDED: TaskSupersededPayload,
    EventType.ARTIFACT_PROPOSED: ArtifactProposedPayload,
    EventType.ARTIFACT_ACCEPTED: ArtifactIdPayload,
    EventType.ARTIFACT_REJECTED: ArtifactRejectedPayload,
    EventType.CANVAS_PATCH: CanvasPatchPayload,
    EventType.CANVAS_APPLY_ACK: ArtifactIdPayload,
    EventType.CANVAS_FIRST_VISIBLE: ArtifactIdPayload,
    EventType.CANVAS_ANIMATION_COMPLETE: ArtifactIdPayload,
    EventType.CANVAS_RENDER_FAILED: CanvasRenderFailedPayload,
    EventType.USAGE_RECORDED: UsageRecordedPayload,
    EventType.SESSION_ENDING: OptionalReasonPayload,
    EventType.SESSION_ENDED: OptionalReasonPayload,
}


class EventEnvelope(BaseModel):
    """One immutable event emitted by a single producer.

    ``producer_sequence`` is ordered only within ``producer_id``. A durable
    appender assigns ``ledger_sequence`` later; emitters must leave it absent.
    ``emitted_at`` exists for audit and must never be used for ordering.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    schema_version: Literal[VOICE_EVENT_SCHEMA_VERSION] = VOICE_EVENT_SCHEMA_VERSION
    event_id: ContractId
    event_type: EventType
    trace_id: ContractId
    voice_call_id: ContractId
    session_id: ContractId
    turn_id: ContractId | None = None
    task_id: ContractId | None = None
    producer_id: ContractId
    producer_sequence: PositiveSequence
    causation_id: ContractId | None = None
    correlation_id: ContractId | None = None
    ledger_sequence: PositiveSequence | None = None
    task_generation: PositiveSequence | None = None
    canvas_base_revision: NonNegativeRevision | None = None
    canvas_result_revision: PositiveSequence | None = None
    emitted_at: AwareDatetime
    payload: Mapping[str, JsonValue]

    @field_validator("emitted_at", mode="before")
    @classmethod
    def validate_timestamp_input(cls, value: object) -> object:
        # Python producers may pass an aware datetime; untrusted JSON must use
        # the same explicit ISO timestamp string accepted by the browser.
        if not isinstance(value, str | datetime):
            raise ValueError("emitted_at must be an ISO timestamp string or aware datetime")
        return value

    @model_validator(mode="after")
    def validate_scope_and_revisions(self) -> Self:
        if self.event_type in _TURN_SCOPED_EVENTS and self.turn_id is None:
            raise ValueError(f"{self.event_type.value} requires turn_id")

        if self.event_type in _TASK_SCOPED_EVENTS:
            if self.task_id is None:
                raise ValueError(f"{self.event_type.value} requires task_id")
            if self.task_generation is None:
                raise ValueError(f"{self.event_type.value} requires task_generation")

        if self.task_generation is not None and self.task_id is None:
            raise ValueError("task_generation requires task_id")

        revisions = (self.canvas_base_revision, self.canvas_result_revision)
        if any(revision is not None for revision in revisions) and self.task_id is None:
            raise ValueError("canvas revisions require task_id")
        if self.event_type is EventType.CANVAS_PATCH and any(
            revision is None for revision in revisions
        ):
            raise ValueError("canvas_patch requires base and result revisions")
        if self.event_type in _CANVAS_RESULT_EVENTS:
            if self.canvas_result_revision is None:
                raise ValueError(f"{self.event_type.value} requires canvas_result_revision")
            if self.causation_id is None:
                raise ValueError(f"{self.event_type.value} requires causation_id")
        if (
            self.canvas_base_revision is not None
            and self.canvas_result_revision is not None
            and self.canvas_result_revision != self.canvas_base_revision + 1
        ):
            raise ValueError("canvas_result_revision must equal canvas_base_revision + 1")

        payload_model = _PAYLOAD_MODELS[self.event_type]
        payload_model.model_validate(dict(self.payload))
        object.__setattr__(self, "payload", freeze_json(dict(self.payload)))

        return self

    @field_serializer("payload")
    def serialize_payload(self, payload: Mapping[str, JsonValue]) -> JsonValue:
        return thaw_json(payload)

    @property
    def is_durable(self) -> bool:
        """Return whether the authoritative ledger has ingested this event."""

        return self.ledger_sequence is not None

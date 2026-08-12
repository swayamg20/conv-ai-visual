"""SDK-neutral identity, assignment, and terminal contracts for voice runtimes.

These records freeze the boundary shared by runtime-specific control planes.
They deliberately do not model media, providers, rooms, peers, or mutable
reservation state.  They are not yet the deployed HTTP response schema.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.types import StringConstraints

MIN_ASSIGNMENT_TTL_SECONDS = 30
MAX_ASSIGNMENT_TTL_SECONDS = 900
LIVEKIT_EVENT_PROTOCOL = "livekit-murmur-v2"
PIPECAT_EVENT_PROTOCOL = "rtvi-murmur-v2"
VOICE_V2_EVENT_TOPIC = "murmur.voice.v2.events"

ContractId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
CanonicalUuid4 = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ),
]
OwnerId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]


class VoiceRuntimeKind(str, Enum):
    """The only server-selectable voice runtime identities."""

    LEGACY = "legacy"
    LIVEKIT_V2 = "livekit_v2"
    PIPECAT_SMALLWEBRTC_V1 = "pipecat_smallwebrtc_v1"


class _RuntimeContract(BaseModel):
    """Fail-closed immutable base for runtime coordination records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )

    @staticmethod
    def _require_timestamp_input(value: object, *, field_name: str) -> object:
        if not isinstance(value, str | datetime):
            raise ValueError(f"{field_name} must be an ISO timestamp string or datetime")
        return value

    @staticmethod
    def _require_utc(value: datetime, *, field_name: str) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError(f"{field_name} must use UTC")
        return value


class VoiceCallClaims(_RuntimeContract):
    """Immutable, authoritative ownership and sticky-runtime claims for one call."""

    user_id: OwnerId
    session_id: CanonicalUuid4
    agent_id: CanonicalUuid4
    voice_call_id: CanonicalUuid4
    trace_id: CanonicalUuid4
    runtime: VoiceRuntimeKind
    profile_id: ContractId
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("user_id must not contain control characters")
        return value

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def validate_timestamp_input(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name
        return cls._require_timestamp_input(value, field_name=field_name)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime, info: ValidationInfo) -> datetime:
        field_name = info.field_name
        return cls._require_utc(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_expiry(self) -> VoiceCallClaims:
        lifetime = (self.expires_at - self.issued_at).total_seconds()
        if not MIN_ASSIGNMENT_TTL_SECONDS <= lifetime <= MAX_ASSIGNMENT_TTL_SECONDS:
            raise ValueError("voice call claims lifetime must be between 30 and 900 seconds")
        return self


class VoiceRuntimeAssignmentBase(_RuntimeContract):
    """Fields common to runtime-specific, browser-connectable assignments."""

    runtime: VoiceRuntimeKind
    claims: VoiceCallClaims
    event_protocol: ContractId
    expires_at: AwareDatetime

    @field_validator("expires_at", mode="before")
    @classmethod
    def validate_expiry_input(cls, value: object) -> object:
        return cls._require_timestamp_input(value, field_name="expires_at")

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_utc(cls, value: datetime) -> datetime:
        return cls._require_utc(value, field_name="expires_at")

    @model_validator(mode="after")
    def validate_assignment_scope(self) -> VoiceRuntimeAssignmentBase:
        if self.runtime is not self.claims.runtime:
            raise ValueError("voice runtime assignment does not match its authoritative claims")
        lifetime = (self.expires_at - self.claims.issued_at).total_seconds()
        if not MIN_ASSIGNMENT_TTL_SECONDS <= lifetime <= MAX_ASSIGNMENT_TTL_SECONDS:
            raise ValueError("voice runtime assignment lifetime must be between 30 and 900 seconds")
        if self.expires_at > self.claims.expires_at:
            raise ValueError("voice runtime assignment outlives its authoritative claims")
        return self


class LiveKitVoiceRuntimeAssignment(VoiceRuntimeAssignmentBase):
    """SDK-neutral locator for the existing LiveKit Voice V2 runtime.

    ``participant_token`` stays redacted in generic dumps.  A later API adapter
    must deliberately reveal it into a browser-safe response after ownership
    checks; ``model_dump()`` is not that projection.
    """

    runtime: Literal[VoiceRuntimeKind.LIVEKIT_V2] = VoiceRuntimeKind.LIVEKIT_V2
    event_protocol: Literal[LIVEKIT_EVENT_PROTOCOL] = LIVEKIT_EVENT_PROTOCOL
    server_url: str
    room_name: ContractId
    participant_token: SecretStr = Field(repr=False)
    participant_identity: ContractId
    agent_participant_identity: ContractId
    dispatch_id: ContractId
    worker_name: ContractId
    event_topic: Literal[VOICE_V2_EVENT_TOPIC] = VOICE_V2_EVENT_TOPIC

    @field_validator("server_url", mode="before")
    @classmethod
    def validate_server_url_type(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("LiveKit server URL must be a string")
        return value

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, value: str) -> str:
        _validate_livekit_server_url(value)
        return value

    @field_validator("participant_token", mode="before")
    @classmethod
    def validate_participant_token(cls, value: object) -> SecretStr:
        return _validated_secret(value, field_name="participant token", max_length=16_000)


class PipecatVoiceRuntimeAssignment(VoiceRuntimeAssignmentBase):
    """A single-use Pipecat signaling locator, without a consumption state machine.

    The signaling service, not this immutable record, must atomically consume
    the opaque URL once for the exact reservation and reject reuse.
    ``webrtc_url`` stays redacted in generic dumps, so a later API adapter must
    deliberately reveal it only into the already-authorized browser response.
    """

    runtime: Literal[VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1] = (
        VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1
    )
    event_protocol: Literal[PIPECAT_EVENT_PROTOCOL] = PIPECAT_EVENT_PROTOCOL
    webrtc_url: SecretStr = Field(repr=False)
    peer_reservation_id: ContractId

    @field_validator("webrtc_url", mode="before")
    @classmethod
    def validate_webrtc_url(cls, value: object) -> SecretStr:
        locator = _validated_secret(value, field_name="Pipecat signaling URL", max_length=2_048)
        _validate_pipecat_signaling_url(locator.get_secret_value())
        return locator

    @model_validator(mode="after")
    def validate_opaque_locator(self) -> PipecatVoiceRuntimeAssignment:
        locator = self.webrtc_url.get_secret_value()
        identity_values = (
            self.claims.user_id,
            self.claims.session_id,
            self.claims.agent_id,
            self.claims.voice_call_id,
            self.claims.trace_id,
            self.claims.profile_id,
            self.peer_reservation_id,
        )
        identity_surface = _fully_unquote(locator).casefold()
        if any(value.casefold() in identity_surface for value in identity_values):
            raise ValueError("Pipecat signaling URL must not embed call identity")
        return self


VoiceRuntimeAssignment: TypeAlias = Annotated[
    LiveKitVoiceRuntimeAssignment | PipecatVoiceRuntimeAssignment,
    Field(discriminator="runtime"),
]


class VoiceRuntimeTerminalReason(str, Enum):
    """Runtime-neutral reasons for ending one bounded voice assignment."""

    USER_ENDED = "user_ended"
    CLIENT_DISCONNECTED = "client_disconnected"
    ASSIGNMENT_EXPIRED = "assignment_expired"
    ASSIGNMENT_REUSED = "assignment_reused"
    OWNER_MISMATCH = "owner_mismatch"
    RUNTIME_MISMATCH = "runtime_mismatch"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_STOPPED = "runtime_stopped"
    INTERNAL_ERROR = "internal_error"


class VoiceRuntimeTerminalResult(_RuntimeContract):
    """Final lifecycle fact emitted after one runtime has stopped owning a call."""

    claims: VoiceCallClaims
    reason: VoiceRuntimeTerminalReason
    retryable: StrictBool
    terminated_at: AwareDatetime

    @field_validator("terminated_at", mode="before")
    @classmethod
    def validate_terminated_at_input(cls, value: object) -> object:
        return cls._require_timestamp_input(value, field_name="terminated_at")

    @field_validator("terminated_at")
    @classmethod
    def validate_terminated_at_utc(cls, value: datetime) -> datetime:
        return cls._require_utc(value, field_name="terminated_at")

    @model_validator(mode="after")
    def validate_terminal_time(self) -> VoiceRuntimeTerminalResult:
        if self.terminated_at < self.claims.issued_at:
            raise ValueError("voice runtime cannot terminate before its claims were issued")
        return self


def _validated_secret(value: object, *, field_name: str, max_length: int) -> SecretStr:
    if isinstance(value, SecretStr):
        raw_value = value.get_secret_value()
    elif isinstance(value, str):
        raw_value = value
    else:
        raise ValueError(f"{field_name} must be a string")
    if (
        not raw_value
        or raw_value != raw_value.strip()
        or len(raw_value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return SecretStr(raw_value)


def _validated_url_parts(value: str, *, field_name: str) -> SplitResult:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} is invalid")
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} is malformed") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must not contain credentials, query, or fragment")
    return parsed


def _validate_livekit_server_url(value: str) -> None:
    parsed = _validated_url_parts(value, field_name="LiveKit server URL")
    if parsed.scheme not in {"ws", "wss"} or parsed.path not in {"", "/"}:
        raise ValueError("LiveKit server URL must be a ws/wss origin")


def _validate_pipecat_signaling_url(value: str) -> None:
    parsed = _validated_url_parts(value, field_name="Pipecat signaling URL")
    if parsed.path in {"", "/"}:
        raise ValueError("Pipecat signaling URL must contain an opaque path")
    if parsed.scheme == "https":
        return
    if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname):
        raise ValueError("Pipecat signaling URL must use HTTPS or loopback HTTP")


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _fully_unquote(value: str) -> str:
    """Decode nested URL escaping before enforcing locator opacity.

    Every effective percent-decoding round shortens the finite input, so the
    original character length is a proof-based upper bound on convergence.
    """

    decoded = value
    for _ in range(len(value) + 1):
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    raise ValueError("Pipecat signaling URL escaping did not converge")

"""Pure contracts and cryptographic derivation for Voice V2 bootstrap.

This module deliberately has no LiveKit SDK dependency and owns no mutable
assignment state.  Both the HTTP control plane and the worker can depend on
the signed metadata contract without importing bootstrap orchestration.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from murmur.persistence.models import AgentModel, SessionModel

VOICE_V2_RUNTIME = "livekit_v2"
VOICE_V2_EVENT_TOPIC = "murmur.voice.v2.events"
SIGNED_METADATA_VERSION = 1
SIGNED_METADATA_ALGORITHM = "hmac-sha256"
RELEASE_TOMBSTONE_TTL_SECONDS = 900

_CONTRACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class VoiceBootstrapError(Exception):
    """Base class for expected Voice V2 bootstrap failures."""


class VoiceBootstrapUnavailable(VoiceBootstrapError):
    """The Voice V2 control plane cannot safely serve a bootstrap."""


class VoiceBootstrapNotFound(VoiceBootstrapError):
    """An authoritative session or agent does not exist."""


class VoiceBootstrapForbidden(VoiceBootstrapError):
    """The authenticated identity does not own the requested scope."""


class VoiceBootstrapConflict(VoiceBootstrapError):
    """Existing control-plane state conflicts with the trusted assignment."""


@dataclass(frozen=True)
class VoiceBootstrapSettings:
    """Server-controlled Voice V2 assignment and token policy."""

    server_url: str
    environment: str
    profile_id: str
    worker_name: str
    event_topic: str
    signing_secret: str = field(repr=False)
    token_ttl_seconds: int = 300
    job_metadata_ttl_seconds: int = 300
    room_empty_timeout_seconds: int = 60
    room_departure_timeout_seconds: int = 30
    control_plane_timeout_seconds: float = 5.0
    repository_timeout_seconds: float = 2.0
    max_concurrent_bootstraps: int = 100
    max_active_calls: int = 1
    max_call_assignments: int = 10_000
    runtime: Literal["livekit_v2"] = VOICE_V2_RUNTIME

    def __post_init__(self) -> None:
        required = {
            "server_url": self.server_url,
            "environment": self.environment,
            "profile_id": self.profile_id,
            "worker_name": self.worker_name,
            "event_topic": self.event_topic,
            "signing_secret": self.signing_secret,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError("missing Voice V2 settings: " + ", ".join(sorted(missing)))
        for name in ("environment", "profile_id", "worker_name", "event_topic"):
            if not _CONTRACT_ID.fullmatch(getattr(self, name)):
                raise ValueError(f"Voice V2 {name} is not a valid contract identifier")
        if self.event_topic != VOICE_V2_EVENT_TOPIC:
            raise ValueError(f"Voice V2 event_topic must be {VOICE_V2_EVENT_TOPIC}")
        if len(self.signing_secret.encode("utf-8")) < 32:
            raise ValueError("Voice V2 signing secret must contain at least 32 bytes")
        if self.runtime != VOICE_V2_RUNTIME:
            raise ValueError(f"Voice V2 runtime must be {VOICE_V2_RUNTIME}")
        normalize_server_url(self.server_url)
        if not 30 <= self.token_ttl_seconds <= RELEASE_TOMBSTONE_TTL_SECONDS:
            raise ValueError("Voice V2 participant token TTL must be between 30 and 900 seconds")
        if not 30 <= self.job_metadata_ttl_seconds <= RELEASE_TOMBSTONE_TTL_SECONDS:
            raise ValueError("Voice V2 job metadata TTL must be between 30 and 900 seconds")
        if self.room_empty_timeout_seconds <= 0 or self.room_departure_timeout_seconds <= 0:
            raise ValueError("Voice V2 room timeouts must be positive")
        if (
            not math.isfinite(self.control_plane_timeout_seconds)
            or self.control_plane_timeout_seconds <= 0
            or self.control_plane_timeout_seconds > 30
        ):
            raise ValueError("Voice V2 control-plane timeout must be between 0 and 30 seconds")
        if (
            isinstance(self.repository_timeout_seconds, bool)
            or not math.isfinite(self.repository_timeout_seconds)
            or self.repository_timeout_seconds <= 0
            or self.repository_timeout_seconds > 30
        ):
            raise ValueError("Voice V2 repository timeout must be between 0 and 30 seconds")
        if self.max_concurrent_bootstraps <= 0:
            raise ValueError("Voice V2 concurrent-bootstrap limit must be positive")
        if self.max_active_calls <= 0:
            raise ValueError("Voice V2 active-call limit must be positive")
        if self.max_call_assignments <= 0:
            raise ValueError("Voice V2 call-assignment limit must be positive")
        if self.max_active_calls > self.max_call_assignments:
            raise ValueError("Voice V2 active-call limit cannot exceed assignment capacity")


@dataclass(frozen=True)
class VoiceScope:
    """Immutable ownership scope used by deterministic public derivations."""

    user_id: str
    session_id: str
    agent_id: str
    voice_call_id: str


@dataclass(frozen=True)
class VoiceJobMetadata:
    """Exact payload shared by bootstrap signing and worker verification."""

    agent_id: str
    agent_participant_identity: str
    environment: str
    event_topic: str
    job_expires_at: int
    job_issued_at: int
    participant_identity: str
    profile_id: str
    room_name: str
    runtime: str
    session_id: str
    trace_id: str
    user_id: str
    voice_call_id: str
    worker_name: str


def is_contract_id(value: object) -> bool:
    """Return whether a value is safe in Voice V2 signed identifier fields."""

    return isinstance(value, str) and _CONTRACT_ID.fullmatch(value) is not None


def build_job_metadata_payload(
    settings: VoiceBootstrapSettings,
    scope: VoiceScope,
    *,
    room_name: str,
    trace_id: str,
    participant_identity: str,
    agent_participant_identity: str,
    job_issued_at: int,
    job_expires_at: int,
) -> dict[str, object]:
    """Build the exact signed payload consumed by the Voice V2 worker."""

    metadata = VoiceJobMetadata(
        agent_id=scope.agent_id,
        agent_participant_identity=agent_participant_identity,
        environment=settings.environment,
        event_topic=settings.event_topic,
        job_expires_at=job_expires_at,
        job_issued_at=job_issued_at,
        participant_identity=participant_identity,
        profile_id=settings.profile_id,
        room_name=room_name,
        runtime=settings.runtime,
        session_id=scope.session_id,
        trace_id=trace_id,
        user_id=scope.user_id,
        voice_call_id=scope.voice_call_id,
        worker_name=settings.worker_name,
    )
    return dict(vars(metadata))


@dataclass(frozen=True)
class RoomRecord:
    name: str
    metadata: str
    num_participants: int = 0


@dataclass(frozen=True)
class DispatchRecord:
    id: str
    room_name: str
    agent_name: str
    metadata: str
    deleted_at: int = 0


@dataclass(frozen=True)
class CreateDispatchSpec:
    room_name: str
    agent_name: str
    metadata: str
    restart_policy: Literal["never"] = "never"

    def __post_init__(self) -> None:
        if self.restart_policy != "never":
            raise ValueError("Voice V2 dispatch restart policy must be never")


@dataclass(frozen=True)
class CreateRoomSpec:
    """Create one room with its sole named worker dispatch atomically.

    LiveKit Server creates a backward-compatible unnamed dispatch when a room
    starts without agents. Supplying the intended dispatch during room creation
    prevents that ambiguous state before the bootstrap service can reconcile it.
    """

    name: str
    metadata: str
    empty_timeout_seconds: int
    departure_timeout_seconds: int
    initial_dispatch: CreateDispatchSpec
    max_participants: int = 2

    def __post_init__(self) -> None:
        if self.initial_dispatch.room_name != self.name:
            raise ValueError("Voice V2 initial dispatch must target its room")


@dataclass(frozen=True)
class ParticipantGrants:
    """Minimum grants required by the browser participant."""

    room_name: str
    room_join: bool = True
    can_publish: bool = True
    can_subscribe: bool = True
    # Browser data publication is intentionally disabled. Voice V2 events are
    # server-to-browser only; accepting arbitrary client data would create a
    # second, unaudited path into the agent runtime.
    can_publish_data: bool = False
    can_update_own_metadata: bool = False
    can_publish_sources: tuple[str, ...] = ("microphone",)
    room_create: bool = False
    room_list: bool = False
    room_admin: bool = False
    room_record: bool = False
    ingress_admin: bool = False
    agent: bool = False
    can_manage_agent_session: bool = False


@dataclass(frozen=True)
class ParticipantTokenSpec:
    identity: str
    name: str
    metadata: str
    issued_at: datetime
    expires_at: datetime
    grants: ParticipantGrants


@dataclass(frozen=True)
class VoiceBootstrapResult:
    runtime: Literal["livekit_v2"]
    profile_id: str
    server_url: str
    room_name: str
    participant_token: str = field(repr=False)
    participant_identity: str
    agent_participant_identity: str
    session_id: str
    agent_id: str
    voice_call_id: str
    dispatch_id: str
    worker_name: str
    event_topic: str
    trace_id: str
    expires_at: datetime


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


class VoiceControlPlane(Protocol):
    """LiveKit-shaped operations expressed without LiveKit SDK objects."""

    async def get_room(self, room_name: str) -> RoomRecord | None: ...

    async def create_room(self, spec: CreateRoomSpec) -> RoomRecord: ...

    async def list_dispatches(self, room_name: str) -> Sequence[DispatchRecord]: ...

    async def create_dispatch(self, spec: CreateDispatchSpec) -> DispatchRecord: ...

    async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None: ...

    async def delete_room(self, room_name: str) -> None: ...

    def issue_participant_token(self, spec: ParticipantTokenSpec) -> str: ...


class VoiceBootstrapper(Protocol):
    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceBootstrapResult: ...

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None: ...

    async def aclose(self) -> None: ...


def normalize_server_url(server_url: str) -> str:
    """Return the WebSocket URL expected by LiveKit browser clients."""
    value = server_url.strip()
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed/out-of-range explicit ports.
        _parsed_port = parsed.port
    except ValueError as exc:
        raise VoiceBootstrapUnavailable("LIVEKIT_URL is malformed") from exc
    schemes = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    target_scheme = schemes.get(parsed.scheme.lower())
    if target_scheme is None:
        raise VoiceBootstrapUnavailable("LIVEKIT_URL must use http, https, ws, or wss")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise VoiceBootstrapUnavailable(
            "LIVEKIT_URL must be an origin without credentials, path, query, or fragment"
        )
    return urlunsplit((target_scheme, parsed.netloc, "", "", ""))


def derive_room_name(settings: VoiceBootstrapSettings, scope: VoiceScope) -> str:
    """Derive an opaque room name from trusted ownership and retry key."""
    trusted = _canonical_json(
        {
            "environment": settings.environment,
            "session_id": scope.session_id,
            "user_id": scope.user_id,
            "voice_call_id": scope.voice_call_id,
        }
    )
    digest = _mac(settings.signing_secret, b"room:v1\x00" + trusted)[:32]
    environment = re.sub(r"[^a-z0-9-]+", "-", settings.environment.lower()).strip("-")
    safe_environment = (environment or "env")[:20].rstrip("-")
    return f"murmur-{safe_environment}-{digest}"


def derive_participant_identity(
    settings: VoiceBootstrapSettings,
    scope: VoiceScope,
) -> str:
    trusted = _canonical_json(
        {
            "environment": settings.environment,
            "session_id": scope.session_id,
            "user_id": scope.user_id,
            "voice_call_id": scope.voice_call_id,
        }
    )
    return "user-" + _mac(settings.signing_secret, b"participant:v1\x00" + trusted)[:24]


def derive_agent_participant_identity(
    settings: VoiceBootstrapSettings,
    scope: VoiceScope,
) -> str:
    """Derive the identity the named worker must use when accepting this job."""
    trusted = _canonical_json(
        {
            "environment": settings.environment,
            "session_id": scope.session_id,
            "user_id": scope.user_id,
            "voice_call_id": scope.voice_call_id,
            "worker_name": settings.worker_name,
        }
    )
    return "agent-" + _mac(settings.signing_secret, b"agent-participant:v1\x00" + trusted)[:24]


def sign_metadata(payload: dict[str, object], secret: str, *, purpose: str) -> str:
    """Create a canonical, purpose-bound HMAC envelope for opaque metadata."""
    if not purpose:
        raise ValueError("signed metadata purpose is required")
    body = _canonical_json(payload)
    signature = _mac(secret, f"metadata:{purpose}:v1\x00".encode() + body)
    return _canonical_json(
        {
            "algorithm": SIGNED_METADATA_ALGORITHM,
            "payload": payload,
            "purpose": purpose,
            "schema_version": SIGNED_METADATA_VERSION,
            "signature": signature,
        }
    ).decode("utf-8")


def verify_signed_metadata(encoded: str, secret: str, *, purpose: str) -> dict[str, object]:
    """Verify and return a strict metadata payload, raising on any mismatch."""
    try:
        envelope = json.loads(encoded)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("signed metadata is not valid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "algorithm",
        "payload",
        "purpose",
        "schema_version",
        "signature",
    }:
        raise ValueError("signed metadata envelope has unexpected fields")
    if (
        envelope["algorithm"] != SIGNED_METADATA_ALGORITHM
        or envelope["schema_version"] != SIGNED_METADATA_VERSION
        or envelope["purpose"] != purpose
        or not isinstance(envelope["payload"], dict)
        or not isinstance(envelope["signature"], str)
    ):
        raise ValueError("signed metadata envelope is invalid")

    expected = _mac(
        secret,
        f"metadata:{purpose}:v1\x00".encode() + _canonical_json(envelope["payload"]),
    )
    if not hmac.compare_digest(envelope["signature"], expected):
        raise ValueError("signed metadata signature is invalid")
    return envelope["payload"]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _mac(secret: str, message: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

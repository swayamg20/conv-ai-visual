"""Authenticated, provider-free SmallWebRTC reservation and signaling core.

The module deliberately imports no Pipecat type.  Its protocols match only the
public ``SmallWebRTCRequestHandler`` and ``SmallWebRTCConnection`` surface in
the pinned SDK.  A separate composition root may inject those concrete objects
without making the control-plane or its unit tests depend on Pipecat.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceLeaseUnavailable
from murmur.voice.runtime_contracts import (
    MAX_ASSIGNMENT_TTL_SECONDS,
    MIN_ASSIGNMENT_TTL_SECONDS,
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeKind,
    VoiceRuntimeTerminalReason,
    VoiceRuntimeTerminalResult,
)

_RepositoryResult = TypeVar("_RepositoryResult")
_MAX_TOKEN_LENGTH = 512
_MAX_SDP_LENGTH = 1_000_000
_MAX_PC_ID_LENGTH = 256
_MAX_CANDIDATES = 128
_MAX_CANDIDATE_LENGTH = 8_192
_MAX_SDP_MID_LENGTH = 128


class PipecatSignalingError(Exception):
    """Base class for expected, safe-to-map signaling failures."""


class PipecatSignalingNotFound(PipecatSignalingError):
    """The opaque reservation, authoritative session, or agent was not found."""


class PipecatSignalingForbidden(PipecatSignalingError):
    """The authenticated Firebase identity does not own this reservation."""


class PipecatSignalingConflict(PipecatSignalingError):
    """The request conflicts with reservation state or its exact peer."""


class PipecatSignalingUnavailable(PipecatSignalingError):
    """Bounded capacity or a required signaling/runtime component is unavailable."""


class _ReservationCancelled(Exception):
    """Internal wake-up used when authenticated DELETE wins negotiation."""


class PipecatReservationState(str, Enum):
    """Monotonic lifecycle for one SmallWebRTC reservation."""

    RESERVED = "reserved"
    NEGOTIATING = "negotiating"
    ACTIVE = "active"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class PipecatSignalingSettings:
    """Server-owned policy for the process-local challenger prototype."""

    signaling_base_url: str
    profile_id: str
    reservation_ttl_seconds: int = 300
    repository_timeout_seconds: float = 2.0
    signaling_timeout_seconds: float = 10.0
    cleanup_timeout_seconds: float = 5.0
    terminal_cleanup_retry_initial_seconds: float = 0.25
    terminal_cleanup_retry_max_seconds: float = 5.0
    terminal_cleanup_retry_horizon_seconds: float = 30.0
    terminal_cleanup_retry_max_attempts: int = 8
    clock_skew_seconds: int = 30
    max_reservations: int = 1_000
    max_active_calls: Literal[1] = 1
    tombstone_ttl_seconds: int = MAX_ASSIGNMENT_TTL_SECONDS
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id or self.profile_id != self.profile_id.strip():
            raise ValueError("Pipecat profile_id is invalid")
        if (
            not MIN_ASSIGNMENT_TTL_SECONDS
            <= self.reservation_ttl_seconds
            <= (MAX_ASSIGNMENT_TTL_SECONDS)
        ):
            raise ValueError("Pipecat reservation TTL must be between 30 and 900 seconds")
        for name, value in (
            ("repository", self.repository_timeout_seconds),
            ("signaling", self.signaling_timeout_seconds),
            ("cleanup", self.cleanup_timeout_seconds),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 30:
                raise ValueError(f"Pipecat {name} timeout must be between 0 and 30 seconds")
        retry_delays = (
            self.terminal_cleanup_retry_initial_seconds,
            self.terminal_cleanup_retry_max_seconds,
            self.terminal_cleanup_retry_horizon_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
            for value in retry_delays
        ):
            raise ValueError("Pipecat terminal cleanup retry delays must be finite and positive")
        if not (
            self.terminal_cleanup_retry_initial_seconds
            <= self.terminal_cleanup_retry_max_seconds
            <= self.terminal_cleanup_retry_horizon_seconds
            <= 300
        ):
            raise ValueError(
                "Pipecat terminal cleanup retry delays must be ordered within 300 seconds"
            )
        if (
            isinstance(self.terminal_cleanup_retry_max_attempts, bool)
            or not isinstance(self.terminal_cleanup_retry_max_attempts, int)
            or not 1 <= self.terminal_cleanup_retry_max_attempts <= 20
        ):
            raise ValueError(
                "Pipecat terminal cleanup retry attempts must be between one and twenty"
            )
        if (
            isinstance(self.clock_skew_seconds, bool)
            or not isinstance(self.clock_skew_seconds, int)
            or not 0 <= self.clock_skew_seconds <= 60
        ):
            raise ValueError("Pipecat clock skew must be between 0 and 60 seconds")
        if (
            isinstance(self.max_reservations, bool)
            or not isinstance(self.max_reservations, int)
            or self.max_reservations <= 0
        ):
            raise ValueError("Pipecat reservation capacity must be positive")
        if self.max_active_calls != 1:
            raise ValueError("Pipecat Milestone 1 supports exactly one active call")
        if (
            isinstance(self.tombstone_ttl_seconds, bool)
            or not isinstance(self.tombstone_ttl_seconds, int)
            or not MIN_ASSIGNMENT_TTL_SECONDS
            <= self.tombstone_ttl_seconds
            <= MAX_ASSIGNMENT_TTL_SECONDS
        ):
            raise ValueError("Pipecat tombstone TTL must be between 30 and 900 seconds")
        _normalize_signaling_base_url(self.signaling_base_url)
        if not isinstance(self.allowed_origins, tuple):
            raise ValueError("Pipecat allowed origins must be an immutable tuple")
        if len(set(self.allowed_origins)) != len(self.allowed_origins):
            raise ValueError("Pipecat allowed origins must be unique")
        for origin in self.allowed_origins:
            _validate_cors_origin(origin)


@dataclass(frozen=True)
class PipecatCorsContract:
    """Strict browser signaling CORS policy for the future ASGI adapter."""

    allowed_origins: tuple[str, ...]
    allowed_methods: tuple[str, ...] = ("OPTIONS", "POST", "PATCH", "DELETE")
    allowed_headers: tuple[str, ...] = ("authorization", "content-type")
    allow_credentials: bool = False
    max_age_seconds: int = 600

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise ValueError("Pipecat CORS requires at least one explicit origin")
        if "*" in self.allowed_origins:
            raise ValueError("Pipecat CORS does not allow wildcard origins")
        for origin in self.allowed_origins:
            _validate_cors_origin(origin)
        if self.allowed_methods != ("OPTIONS", "POST", "PATCH", "DELETE"):
            raise ValueError("Pipecat CORS methods must match the signaling contract")
        if self.allowed_headers != ("authorization", "content-type"):
            raise ValueError("Pipecat CORS headers must match the signaling contract")
        if self.allow_credentials:
            raise ValueError("Pipecat signaling uses bearer auth, not browser credentials")
        if (
            isinstance(self.max_age_seconds, bool)
            or not isinstance(self.max_age_seconds, int)
            or not 0 <= self.max_age_seconds <= 3_600
        ):
            raise ValueError("Pipecat CORS max age must be between 0 and 3600 seconds")

    def allows(self, origin: str) -> bool:
        return origin in self.allowed_origins


@dataclass(frozen=True)
class PipecatOfferRequest:
    """SDK-neutral shape accepted by the pinned handler's web-request method."""

    sdp: str
    type: Literal["offer"] = "offer"
    pc_id: str | None = None
    restart_pc: bool = False
    request_data: None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sdp, str) or not self.sdp or len(self.sdp) > _MAX_SDP_LENGTH:
            raise ValueError("SmallWebRTC SDP offer is invalid")
        if self.type != "offer":
            raise ValueError("SmallWebRTC request type must be offer")
        if self.pc_id is not None:
            _validate_pc_id(self.pc_id)
        if not isinstance(self.restart_pc, bool):
            raise ValueError("SmallWebRTC restart_pc must be boolean")
        if self.restart_pc:
            raise ValueError("SmallWebRTC peer restart is unsupported; start a fresh call")
        if self.request_data is not None:
            raise ValueError("SmallWebRTC request_data is not accepted by the signaling core")


@dataclass(frozen=True)
class PipecatIceCandidate:
    """SDK-neutral trickle-ICE candidate shape used by Pipecat 1.7."""

    candidate: str
    sdp_mid: str
    sdp_mline_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, str) or len(self.candidate) > _MAX_CANDIDATE_LENGTH:
            raise ValueError("SmallWebRTC ICE candidate is invalid")
        if (
            not isinstance(self.sdp_mid, str)
            or not self.sdp_mid
            or len(self.sdp_mid) > _MAX_SDP_MID_LENGTH
        ):
            raise ValueError("SmallWebRTC ICE candidate sdp_mid is invalid")
        if (
            isinstance(self.sdp_mline_index, bool)
            or not isinstance(self.sdp_mline_index, int)
            or not 0 <= self.sdp_mline_index <= 128
        ):
            raise ValueError("SmallWebRTC ICE candidate m-line index is invalid")


@dataclass(frozen=True)
class PipecatPatchRequest:
    """SDK-neutral request for candidates belonging to one exact peer."""

    pc_id: str
    candidates: tuple[PipecatIceCandidate, ...]

    def __post_init__(self) -> None:
        _validate_pc_id(self.pc_id)
        if not isinstance(self.candidates, tuple):
            raise ValueError("SmallWebRTC candidates must be an immutable tuple")
        if not self.candidates or len(self.candidates) > _MAX_CANDIDATES:
            raise ValueError("SmallWebRTC candidate count is invalid")
        if not all(isinstance(candidate, PipecatIceCandidate) for candidate in self.candidates):
            raise ValueError("SmallWebRTC candidate is invalid")


@dataclass(frozen=True)
class PipecatOfferAnswer:
    """Strict response projection; no handler or connection escapes with it."""

    sdp: str
    type: str
    pc_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sdp, str) or not self.sdp or len(self.sdp) > _MAX_SDP_LENGTH:
            raise ValueError("SmallWebRTC SDP answer is invalid")
        if self.type != "answer":
            raise ValueError("SmallWebRTC response type must be answer")
        _validate_pc_id(self.pc_id)


@dataclass(frozen=True)
class PipecatReservationSnapshot:
    """Safe lifecycle view that deliberately omits bearer, handler, and runtime objects."""

    peer_reservation_id: str
    claims: VoiceCallClaims
    state: PipecatReservationState
    pc_id: str | None
    terminal_result: VoiceRuntimeTerminalResult | None
    cleanup_complete: bool


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


class PipecatPeerConnection(Protocol):
    """Only public connection members needed by signaling ownership."""

    @property
    def pc_id(self) -> str: ...

    def add_event_handler(
        self, event_name: str, handler: Callable[..., Awaitable[None]]
    ) -> None: ...

    async def disconnect(self) -> None: ...


class PipecatPeerHandler(Protocol):
    """Public Pipecat 1.7 request-handler surface, expressed structurally."""

    async def handle_web_request(
        self,
        request: PipecatOfferRequest,
        webrtc_connection_callback: Callable[[PipecatPeerConnection], Awaitable[None]],
    ) -> dict[str, str] | None: ...

    async def handle_patch_request(self, request: PipecatPatchRequest) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class PipecatHandlerRequestTypes:
    """Concrete SDK DTO constructors used only inside the handler adapter."""

    web_request: Callable[..., object]
    patch_request: Callable[..., object]
    ice_candidate: Callable[..., object]


class PipecatPeerHandlerAdapter:
    """Adapt neutral DTOs to pinned Pipecat 1.7 request objects lazily.

    The wrapped handler remains private.  Supplying ``request_types`` keeps the
    adapter testable in installations where the optional Pipecat extra is not
    present; omitting it imports the pinned public request module at runtime.
    """

    def __init__(
        self,
        handler: object,
        *,
        request_types: PipecatHandlerRequestTypes | None = None,
    ) -> None:
        for method_name in ("handle_web_request", "handle_patch_request", "close"):
            if not callable(getattr(handler, method_name, None)):
                raise TypeError(f"Pipecat handler has no public {method_name} method")
        self._handler = handler
        self._request_types = request_types or self._load_request_types()

    async def handle_web_request(
        self,
        request: PipecatOfferRequest,
        webrtc_connection_callback: Callable[[PipecatPeerConnection], Awaitable[None]],
    ) -> dict[str, str] | None:
        sdk_request = self._request_types.web_request(
            sdp=request.sdp,
            type=request.type,
            pc_id=request.pc_id,
            restart_pc=request.restart_pc,
            request_data=request.request_data,
        )
        return await self._handler.handle_web_request(
            sdk_request,
            webrtc_connection_callback,
        )

    async def handle_patch_request(self, request: PipecatPatchRequest) -> None:
        sdk_candidates = [
            self._request_types.ice_candidate(
                candidate=candidate.candidate,
                sdp_mid=candidate.sdp_mid,
                sdp_mline_index=candidate.sdp_mline_index,
            )
            for candidate in request.candidates
        ]
        sdk_request = self._request_types.patch_request(
            pc_id=request.pc_id,
            candidates=sdk_candidates,
        )
        await self._handler.handle_patch_request(sdk_request)

    async def close(self) -> None:
        await self._handler.close()

    @staticmethod
    def _load_request_types() -> PipecatHandlerRequestTypes:
        try:
            module = importlib.import_module("pipecat.transports.smallwebrtc.request_handler")
        except ImportError as exc:
            raise PipecatSignalingUnavailable(
                "Pipecat SmallWebRTC request types are unavailable"
            ) from exc
        return PipecatHandlerRequestTypes(
            web_request=module.SmallWebRTCRequest,
            patch_request=module.SmallWebRTCPatchRequest,
            ice_candidate=module.IceCandidate,
        )


class PipecatRuntimeHandle(Protocol):
    async def wait_closed(self) -> None: ...

    async def aclose(self) -> None: ...


class PipecatRuntimeStarter(Protocol):
    async def start(
        self,
        *,
        connection: PipecatPeerConnection,
        claims: VoiceCallClaims,
    ) -> PipecatRuntimeHandle: ...


@dataclass
class _CallbackSideChannel:
    calls: int = 0
    accepting: bool = True
    peer_closed: bool = False
    connection: PipecatPeerConnection | None = None
    runtime_handle: PipecatRuntimeHandle | None = None
    failure: BaseException | None = None


@dataclass
class _Reservation:
    peer_reservation_id: str
    claims: VoiceCallClaims
    ice_lease: PipecatIceLease = field(repr=False)
    token_hash: bytes = field(repr=False)
    handler: PipecatPeerHandler = field(repr=False)
    state: PipecatReservationState = PipecatReservationState.RESERVED
    pc_id: str | None = None
    runtime_handle: PipecatRuntimeHandle | None = field(default=None, repr=False)
    terminal_result: VoiceRuntimeTerminalResult | None = None
    tombstone_expires_at: datetime | None = None
    handler_cleanup_complete: bool = False
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    cancel_reason: VoiceRuntimeTerminalReason | None = None
    runtime_observer_task: asyncio.Task[None] | None = field(default=None, repr=False)
    expiry_task: asyncio.Task[None] | None = field(default=None, repr=False)
    cleanup_retry_task: asyncio.Task[None] | None = field(default=None, repr=False)
    trusted_release_task: asyncio.Task[VoiceRuntimeTerminalResult] | None = field(
        default=None,
        repr=False,
    )
    trusted_release_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class PipecatSignalingService:
    """Own one-time reservations and the exact SmallWebRTC peer they create."""

    def __init__(
        self,
        settings: PipecatSignalingSettings,
        *,
        handler_factory: Callable[[PipecatIceLease], PipecatPeerHandler],
        runtime_starter: PipecatRuntimeStarter,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.cors = (
            PipecatCorsContract(settings.allowed_origins) if settings.allowed_origins else None
        )
        self._handler_factory = handler_factory
        self._runtime_starter = runtime_starter
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(48))
        self._sleep = sleep or asyncio.sleep
        self._guard = asyncio.Lock()
        self._reservations: dict[str, _Reservation] = {}
        self._token_index: dict[bytes, str] = {}
        self._call_index: dict[str, str] = {}
        self._active_reservation_id: str | None = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    async def reserve(
        self,
        claims: VoiceCallClaims,
        ice_lease: PipecatIceLease,
    ) -> PipecatVoiceRuntimeAssignment:
        """Create one claim-bound reservation with its exact immutable ICE lease."""
        now = self._aware_now()
        self._validate_claims_policy(claims, now=now)
        self._validate_ice_lease(claims, ice_lease, now=now)
        await self._authorize_claims(claims)

        raw_token = self._validated_new_token()
        token_hash = _hash_token(raw_token)
        reservation_id = uuid4().hex
        url = _build_signaling_url(self.settings.signaling_base_url, raw_token)
        assignment = PipecatVoiceRuntimeAssignment(
            claims=claims,
            webrtc_url=url,
            peer_reservation_id=reservation_id,
            expires_at=claims.expires_at,
        )
        pruned_cleanup_tasks: tuple[asyncio.Task[None], ...] = ()
        try:
            async with self._guard:
                self._ensure_open_locked()
                pruned_cleanup_tasks = self._prune_tombstones_locked(now)
                if claims.voice_call_id in self._call_index:
                    raise PipecatSignalingConflict(
                        "voice_call_id already has a reservation; start a fresh call"
                    )
                if len(self._reservations) >= self.settings.max_reservations:
                    raise PipecatSignalingUnavailable("Pipecat reservation capacity is exhausted")
                if token_hash in self._token_index:
                    raise PipecatSignalingUnavailable(
                        "Pipecat token generator returned a collision"
                    )
                try:
                    handler = self._handler_factory(ice_lease)
                except Exception as exc:
                    raise PipecatSignalingUnavailable(
                        "Pipecat peer handler is unavailable"
                    ) from exc
                record = _Reservation(
                    peer_reservation_id=reservation_id,
                    claims=claims,
                    ice_lease=ice_lease,
                    token_hash=token_hash,
                    handler=handler,
                )
                self._reservations[reservation_id] = record
                self._token_index[token_hash] = reservation_id
                self._call_index[claims.voice_call_id] = reservation_id
                # Start ownership before the first post-insertion await below so
                # caller cancellation cannot strand a reservation without TTL.
                record.expiry_task = asyncio.create_task(
                    self._expire_unused_reservation(
                        reservation_id=reservation_id,
                        immutable_claims=claims,
                    ),
                    name=f"pipecat-reservation-expiry-{reservation_id}",
                )
        finally:
            if pruned_cleanup_tasks:
                await asyncio.gather(*pruned_cleanup_tasks, return_exceptions=True)
        return assignment

    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        """Consume a reservation once, or renegotiate only its exact active peer."""
        record = await self._resolve_authorized(token=token, user_id=user_id)
        async with record.lock:
            await self._authorize_live_record(record, user_id=user_id)
            if record.state is PipecatReservationState.RESERVED:
                if request.pc_id is not None:
                    raise PipecatSignalingConflict(
                        "initial SmallWebRTC offer must not claim a peer connection"
                    )
                return await self._initial_offer_locked(record, request)
            if record.state is PipecatReservationState.ACTIVE:
                self._require_exact_pc_id(record, request.pc_id)
                return await self._renegotiate_locked(record, request)
            if record.state is PipecatReservationState.NEGOTIATING:
                raise PipecatSignalingConflict("SmallWebRTC reservation is already negotiating")
            raise PipecatSignalingConflict("SmallWebRTC reservation is terminal")

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None:
        """Apply trickle ICE only to the owned reservation's exact active peer."""
        record = await self._resolve_authorized(token=token, user_id=user_id)
        async with record.lock:
            await self._authorize_live_record(record, user_id=user_id)
            if record.state is not PipecatReservationState.ACTIVE:
                raise PipecatSignalingConflict("SmallWebRTC peer is not active")
            self._require_exact_pc_id(record, request.pc_id)
            try:
                await asyncio.wait_for(
                    record.handler.handle_patch_request(request),
                    timeout=self.settings.signaling_timeout_seconds,
                )
            except TimeoutError as exc:
                await self._terminate_locked(
                    record,
                    reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                    retryable=True,
                )
                raise PipecatSignalingUnavailable("SmallWebRTC PATCH timed out") from exc
            except asyncio.CancelledError:
                await self._terminate_locked(
                    record,
                    reason=VoiceRuntimeTerminalReason.RUNTIME_STOPPED,
                    retryable=True,
                )
                raise
            except Exception as exc:
                await self._terminate_locked(
                    record,
                    reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                    retryable=True,
                )
                raise PipecatSignalingUnavailable("SmallWebRTC PATCH failed") from exc

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> VoiceRuntimeTerminalResult:
        """Idempotently terminate the exact peer, or an exact unused reservation."""
        if pc_id is not None:
            _validate_pc_id(pc_id)
        record = await self._resolve_authorized(token=token, user_id=user_id)
        # Authenticate before publishing negative intent. The event is separate
        # from the negotiation lock so DELETE can promptly cancel a handler call
        # that currently owns that lock and has not produced a pc_id yet.
        self._require_immutable_claimant(record, user_id=user_id)
        pre_peer_cancel = pc_id is None and record.state in {
            PipecatReservationState.RESERVED,
            PipecatReservationState.NEGOTIATING,
        }
        if pre_peer_cancel:
            self._publish_cancel_intent(record, VoiceRuntimeTerminalReason.USER_ENDED)
        async with record.lock:
            await self._authorize_record_ownership_locked(record, user_id=user_id)
            if record.state is PipecatReservationState.TERMINAL:
                if record.pc_id is not None and not pre_peer_cancel:
                    self._require_exact_pc_id(record, pc_id)
                elif pc_id is not None:
                    raise PipecatSignalingConflict(
                        "unused SmallWebRTC reservation has no peer connection"
                    )
                assert record.terminal_result is not None
                await self._cleanup_owned_resources_locked(record)
                return record.terminal_result
            if record.state is PipecatReservationState.RESERVED:
                if pc_id is not None:
                    raise PipecatSignalingConflict(
                        "unused SmallWebRTC reservation has no peer connection"
                    )
            else:
                self._require_exact_pc_id(record, pc_id)
            return await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.USER_ENDED,
                retryable=False,
            )

    async def release_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult:
        """Release one exact call from its authenticated bootstrap owner.

        This process-internal path deliberately needs neither the browser bearer
        nor its peer ID.  It first proves the immutable call scope and current
        repository authority, then publishes negative intent before waiting for
        an in-flight negotiation lock.  Once intent is published, an owned task
        completes terminalization even if the initiating request is cancelled.
        """

        record = await self._resolve_trusted_call(
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )
        authority_error: PipecatSignalingNotFound | PipecatSignalingForbidden | None = None
        release_task: asyncio.Task[VoiceRuntimeTerminalResult] | None
        async with record.trusted_release_lock:
            # The call index may have been pruned while this caller waited for a
            # concurrent release. Recheck the exact object and immutable scope
            # before either consulting authority or publishing cancellation.
            async with self._guard:
                self._ensure_open_locked()
                self._require_retained_trusted_call_locked(
                    record,
                    user_id=user_id,
                    session_id=session_id,
                    voice_call_id=voice_call_id,
                )
                if record.state is PipecatReservationState.TERMINAL:
                    assert record.terminal_result is not None
                    release_task = self._ensure_trusted_release_task_locked(
                        record,
                        reason=record.terminal_result.reason,
                    )
                else:
                    release_task = None

            if release_task is None:
                try:
                    await self._authorize_claims(record.claims)
                except (PipecatSignalingNotFound, PipecatSignalingForbidden) as exc:
                    # An exact immutable caller may revoke a call whose persistent
                    # authority disappeared or drifted. Availability failures are
                    # intentionally not caught and therefore cannot mutate state.
                    authority_error = exc

                # Linearize final authority/cancellation/task ownership with
                # service close. Either close snapshots this owned task, or it
                # closes first and this release fails before publishing intent.
                async with self._guard:
                    self._ensure_open_locked()
                    self._require_retained_trusted_call_locked(
                        record,
                        user_id=user_id,
                        session_id=session_id,
                        voice_call_id=voice_call_id,
                    )
                    if record.state is PipecatReservationState.TERMINAL:
                        assert record.terminal_result is not None
                        reason = record.terminal_result.reason
                    else:
                        reason = (
                            VoiceRuntimeTerminalReason.OWNER_MISMATCH
                            if authority_error is not None
                            else VoiceRuntimeTerminalReason.USER_ENDED
                        )
                        self._publish_cancel_intent(record, reason)
                    release_task = self._ensure_trusted_release_task_locked(
                        record,
                        reason=reason,
                    )

        assert release_task is not None
        result = await asyncio.shield(release_task)
        if authority_error is not None:
            raise authority_error
        return result

    async def status(
        self,
        *,
        token: str,
        user_id: str,
    ) -> PipecatReservationSnapshot:
        """Return a safe ownership-scoped snapshot without raw runtime objects."""
        record = await self._resolve_authorized(token=token, user_id=user_id)
        async with record.lock:
            await self._authorize_record_ownership_locked(record, user_id=user_id)
            return PipecatReservationSnapshot(
                peer_reservation_id=record.peer_reservation_id,
                claims=record.claims,
                state=record.state,
                pc_id=record.pc_id,
                terminal_result=record.terminal_result,
                cleanup_complete=self._cleanup_complete(record),
            )

    async def status_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatReservationSnapshot:
        """Return the retained lifecycle fact for one exact internal call scope.

        The authenticated bootstrap owner already established this immutable
        scope when it created the reservation, so this process-internal read
        deliberately performs no repository lookup.  Revalidating the call
        index while holding the reservation lock makes the snapshot linearize
        with terminalization, tombstone pruning, and service close without
        exposing the browser bearer or any owned runtime object.
        """

        record = await self._resolve_trusted_call(
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )
        async with record.lock:
            async with self._guard:
                self._ensure_open_locked()
                self._require_retained_trusted_call_locked(
                    record,
                    user_id=user_id,
                    session_id=session_id,
                    voice_call_id=voice_call_id,
                )
                return PipecatReservationSnapshot(
                    peer_reservation_id=record.peer_reservation_id,
                    claims=record.claims,
                    state=record.state,
                    pc_id=record.pc_id,
                    terminal_result=record.terminal_result,
                    cleanup_complete=self._cleanup_complete(record),
                )

    async def aclose(self) -> None:
        """Run one cancellation-safe close attempt and expose incomplete cleanup."""

        async with self._close_lock:
            task = self._close_task
            if task is None or task.cancelled() or (task.done() and task.exception() is not None):
                task = asyncio.create_task(
                    self._close_owned(),
                    name="pipecat-signaling-close",
                )
                task.add_done_callback(_consume_task_result)
                self._close_task = task
        await asyncio.shield(task)

    async def _close_owned(self) -> None:
        """Stop admission and retry each retained resource exactly once per call."""

        async with self._guard:
            self._closed = True
            records = tuple(self._reservations.values())
            for record in records:
                self._publish_cancel_intent(
                    record,
                    VoiceRuntimeTerminalReason.RUNTIME_STOPPED,
                )
        for record in records:
            trusted_release_task = record.trusted_release_task
            async with record.lock:
                await self._terminate_locked(
                    record,
                    reason=VoiceRuntimeTerminalReason.RUNTIME_STOPPED,
                    retryable=True,
                )
                await self._cancel_cleanup_retry_task_locked(record)
            if (
                trusted_release_task is not None
                and trusted_release_task is not asyncio.current_task()
            ):
                await asyncio.gather(trusted_release_task, return_exceptions=True)
        if any(not self._cleanup_complete(record) for record in records):
            raise PipecatSignalingUnavailable("Pipecat signaling close cleanup is incomplete")

    @property
    def reservation_count(self) -> int:
        return len(self._reservations)

    @property
    def active_call_count(self) -> int:
        return int(self._active_reservation_id is not None)

    @staticmethod
    def _cleanup_complete(record: _Reservation) -> bool:
        return record.runtime_handle is None and record.handler_cleanup_complete

    async def _initial_offer_locked(
        self,
        record: _Reservation,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        now = self._aware_now()
        if now >= record.claims.expires_at:
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.ASSIGNMENT_EXPIRED,
                retryable=False,
            )
            raise PipecatSignalingConflict("SmallWebRTC reservation expired; start a fresh call")
        await self._claim_active_slot(record)
        record.state = PipecatReservationState.NEGOTIATING
        side_channel = _CallbackSideChannel()
        remaining_ttl_seconds = (record.claims.expires_at - now).total_seconds()

        async def start_runtime(
            connection: PipecatPeerConnection,
            immutable_claims: VoiceCallClaims = record.claims,
            immutable_reservation_id: str = record.peer_reservation_id,
        ) -> None:
            side_channel.calls += 1
            if not side_channel.accepting:
                failure = RuntimeError("SmallWebRTC callback arrived after reservation closure")
                side_channel.failure = failure
                raise failure
            if side_channel.calls != 1:
                failure = RuntimeError("SmallWebRTC handler invoked its callback more than once")
                side_channel.failure = failure
                raise failure
            try:
                _validate_pc_id(connection.pc_id)

                async def peer_closed(
                    closed_connection: PipecatPeerConnection,
                    captured_claims: VoiceCallClaims = immutable_claims,
                    captured_reservation_id: str = immutable_reservation_id,
                ) -> None:
                    side_channel.peer_closed = True
                    await self._peer_closed(
                        reservation_id=captured_reservation_id,
                        immutable_claims=captured_claims,
                        connection=closed_connection,
                    )

                connection.add_event_handler("closed", peer_closed)
                runtime_handle = await self._runtime_starter.start(
                    connection=connection,
                    claims=immutable_claims,
                )
                if runtime_handle is None:
                    raise RuntimeError("Pipecat runtime starter returned no lifecycle handle")
                side_channel.connection = connection
                side_channel.runtime_handle = runtime_handle
            except BaseException as exc:
                side_channel.failure = exc
                raise

        handler_failure: BaseException | None = None
        raw_answer: dict[str, str] | None = None
        try:
            raw_answer = await self._run_initial_handler(
                record,
                request=request,
                callback=start_runtime,
                timeout_seconds=min(
                    self.settings.signaling_timeout_seconds,
                    remaining_ttl_seconds,
                ),
            )
        except _ReservationCancelled:
            reason = record.cancel_reason or VoiceRuntimeTerminalReason.USER_ENDED
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=reason,
                retryable=reason
                not in {
                    VoiceRuntimeTerminalReason.USER_ENDED,
                    VoiceRuntimeTerminalReason.OWNER_MISMATCH,
                },
            )
            raise PipecatSignalingConflict("SmallWebRTC reservation was cancelled") from None
        except asyncio.CancelledError:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.RUNTIME_STOPPED,
                retryable=True,
            )
            raise
        except BaseException as exc:
            handler_failure = exc
        finally:
            side_channel.accepting = False

        if side_channel.failure is not None:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.RUNTIME_UNAVAILABLE,
                retryable=True,
            )
            raise PipecatSignalingUnavailable("Pipecat runtime failed to start") from (
                side_channel.failure
            )
        if handler_failure is not None:
            reason = (
                VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE
                if isinstance(handler_failure, TimeoutError)
                else VoiceRuntimeTerminalReason.INTERNAL_ERROR
            )
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=reason,
                retryable=True,
            )
            raise PipecatSignalingUnavailable("SmallWebRTC initial negotiation failed") from (
                handler_failure
            )
        if side_channel.calls != 1 or side_channel.connection is None:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.RUNTIME_UNAVAILABLE,
                retryable=True,
            )
            raise PipecatSignalingUnavailable(
                "SmallWebRTC handler did not start the reserved runtime"
            )
        if side_channel.peer_closed:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.CLIENT_DISCONNECTED,
                retryable=True,
            )
            raise PipecatSignalingUnavailable(
                "SmallWebRTC peer closed before negotiation completed"
            )

        try:
            answer = _validated_answer(raw_answer)
        except ValueError as exc:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                retryable=True,
            )
            raise PipecatSignalingUnavailable("SmallWebRTC returned an invalid answer") from exc
        if not hmac.compare_digest(answer.pc_id, side_channel.connection.pc_id):
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                retryable=True,
            )
            raise PipecatSignalingUnavailable("SmallWebRTC returned a different peer connection")
        if self._aware_now() >= record.claims.expires_at:
            await self._terminate_after_initial_failure(
                record,
                side_channel,
                reason=VoiceRuntimeTerminalReason.ASSIGNMENT_EXPIRED,
                retryable=False,
            )
            raise PipecatSignalingConflict("SmallWebRTC reservation expired; start a fresh call")

        record.pc_id = answer.pc_id
        record.runtime_handle = side_channel.runtime_handle
        record.state = PipecatReservationState.ACTIVE
        self._cancel_expiry_task(record)
        record.runtime_observer_task = asyncio.create_task(
            self._observe_runtime(
                reservation_id=record.peer_reservation_id,
                immutable_claims=record.claims,
                runtime_handle=side_channel.runtime_handle,
            ),
            name=f"pipecat-runtime-observer-{record.peer_reservation_id}",
        )
        return answer

    async def _renegotiate_locked(
        self,
        record: _Reservation,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        callback_called = False

        async def reject_new_connection(
            connection: PipecatPeerConnection,
            immutable_claims: VoiceCallClaims = record.claims,
            immutable_reservation_id: str = record.peer_reservation_id,
        ) -> None:
            del connection, immutable_claims, immutable_reservation_id
            nonlocal callback_called
            callback_called = True
            raise RuntimeError("renegotiation attempted to create a new peer")

        try:
            raw_answer = await asyncio.wait_for(
                record.handler.handle_web_request(request, reject_new_connection),
                timeout=self.settings.signaling_timeout_seconds,
            )
            answer = _validated_answer(raw_answer)
        except asyncio.CancelledError:
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.RUNTIME_STOPPED,
                retryable=True,
            )
            raise
        except Exception as exc:
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                retryable=True,
            )
            raise PipecatSignalingUnavailable("SmallWebRTC renegotiation failed") from exc
        if (
            callback_called
            or record.pc_id is None
            or not hmac.compare_digest(answer.pc_id, record.pc_id)
        ):
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.TRANSPORT_UNAVAILABLE,
                retryable=False,
            )
            raise PipecatSignalingConflict("SmallWebRTC renegotiation changed the exact peer")
        return answer

    async def _terminate_after_initial_failure(
        self,
        record: _Reservation,
        side_channel: _CallbackSideChannel,
        *,
        reason: VoiceRuntimeTerminalReason,
        retryable: bool,
    ) -> None:
        if side_channel.runtime_handle is not None and record.runtime_handle is None:
            record.runtime_handle = side_channel.runtime_handle
        if side_channel.connection is not None and record.pc_id is None:
            record.pc_id = side_channel.connection.pc_id
        await self._terminate_locked(record, reason=reason, retryable=retryable)

    async def _run_initial_handler(
        self,
        record: _Reservation,
        *,
        request: PipecatOfferRequest,
        callback: Callable[[PipecatPeerConnection], Awaitable[None]],
        timeout_seconds: float,
    ) -> dict[str, str] | None:
        handler_task = asyncio.create_task(
            record.handler.handle_web_request(request, callback),
            name=f"pipecat-initial-offer-{record.peer_reservation_id}",
        )
        cancel_task = asyncio.create_task(
            record.cancel_requested.wait(),
            name=f"pipecat-cancel-wait-{record.peer_reservation_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {handler_task, cancel_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and cancel_task.result():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                raise _ReservationCancelled
            if handler_task in done:
                return await handler_task
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            raise TimeoutError("SmallWebRTC initial negotiation timed out")
        except asyncio.CancelledError:
            handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def _finish_trusted_release(
        self,
        *,
        record: _Reservation,
        reason: VoiceRuntimeTerminalReason,
    ) -> VoiceRuntimeTerminalResult:
        async with record.lock:
            # The first published terminal fact wins. This also makes repeated
            # trusted release calls retry incomplete cleanup without rewriting
            # an already-authoritative terminal reason.
            return await self._terminate_locked(
                record,
                reason=reason,
                retryable=False,
            )

    async def _terminate_locked(
        self,
        record: _Reservation,
        *,
        reason: VoiceRuntimeTerminalReason,
        retryable: bool,
    ) -> VoiceRuntimeTerminalResult:
        if record.state is PipecatReservationState.TERMINAL:
            assert record.terminal_result is not None
            await self._cleanup_owned_resources_locked(record)
            return record.terminal_result

        now = max(self._aware_now(), record.claims.issued_at)
        result = VoiceRuntimeTerminalResult(
            claims=record.claims,
            reason=reason,
            retryable=retryable,
            terminated_at=now,
        )
        # Publish the tombstone before invoking cleanup. Peer callbacks or
        # concurrent callers can therefore never resurrect this reservation.
        record.state = PipecatReservationState.TERMINAL
        record.terminal_result = result
        record.tombstone_expires_at = now + timedelta(seconds=self.settings.tombstone_ttl_seconds)
        self._cancel_expiry_task(record)
        await self._cleanup_owned_resources_locked(record)
        return result

    async def _cleanup_owned_resources_locked(self, record: _Reservation) -> None:
        try:
            observer_task, record.runtime_observer_task = record.runtime_observer_task, None
            if observer_task is not None and observer_task is not asyncio.current_task():
                observer_task.cancel()
                await asyncio.gather(observer_task, return_exceptions=True)
            if record.runtime_handle is not None:
                if await self._bounded_cleanup(record.runtime_handle.aclose):
                    record.runtime_handle = None
            if not record.handler_cleanup_complete:
                record.handler_cleanup_complete = await self._bounded_cleanup(record.handler.close)
            if record.runtime_handle is None and record.handler_cleanup_complete:
                await self._cancel_cleanup_retry_task_locked(record)
                async with self._guard:
                    if self._active_reservation_id == record.peer_reservation_id:
                        self._active_reservation_id = None
        finally:
            if record.state is PipecatReservationState.TERMINAL and (
                record.runtime_handle is not None or not record.handler_cleanup_complete
            ):
                self._ensure_cleanup_retry_task_locked(record)

    def _ensure_cleanup_retry_task_locked(self, record: _Reservation) -> None:
        if record.state is not PipecatReservationState.TERMINAL:
            return
        if record.runtime_handle is None and record.handler_cleanup_complete:
            return
        existing = record.cleanup_retry_task
        if existing is not None and not existing.done():
            return
        record.cleanup_retry_task = asyncio.create_task(
            self._retry_terminal_cleanup(
                record=record,
                immutable_terminal_result=record.terminal_result,
            ),
            name=f"pipecat-terminal-cleanup-{record.peer_reservation_id}",
        )

    async def _cancel_cleanup_retry_task_locked(self, record: _Reservation) -> None:
        retry_task = record.cleanup_retry_task
        if retry_task is None or retry_task is asyncio.current_task():
            return
        record.cleanup_retry_task = None
        retry_task.cancel()
        await asyncio.gather(retry_task, return_exceptions=True)

    async def _retry_terminal_cleanup(
        self,
        *,
        record: _Reservation,
        immutable_terminal_result: VoiceRuntimeTerminalResult | None,
    ) -> None:
        current_task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.terminal_cleanup_retry_horizon_seconds
        delay = self.settings.terminal_cleanup_retry_initial_seconds
        try:
            async with asyncio.timeout_at(deadline):
                for _ in range(self.settings.terminal_cleanup_retry_max_attempts):
                    await self._sleep(delay)
                    async with self._guard:
                        if (
                            self._closed
                            or self._reservations.get(record.peer_reservation_id) is not record
                        ):
                            return
                    async with record.lock:
                        if (
                            record.state is not PipecatReservationState.TERMINAL
                            or record.terminal_result is not immutable_terminal_result
                            or record.cleanup_retry_task is not current_task
                        ):
                            return
                        await self._cleanup_owned_resources_locked(record)
                        if record.runtime_handle is None and record.handler_cleanup_complete:
                            return
                    delay = min(delay * 2, self.settings.terminal_cleanup_retry_max_seconds)
        except TimeoutError:
            return
        except asyncio.CancelledError:
            raise
        finally:
            async with self._guard:
                retained = self._reservations.get(record.peer_reservation_id)
                if retained is record and record.cleanup_retry_task is current_task:
                    record.cleanup_retry_task = None

    async def _bounded_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> bool:
        try:
            await asyncio.wait_for(cleanup(), timeout=self.settings.cleanup_timeout_seconds)
        except Exception:
            return False
        return True

    async def _observe_runtime(
        self,
        *,
        reservation_id: str,
        immutable_claims: VoiceCallClaims,
        runtime_handle: PipecatRuntimeHandle,
    ) -> None:
        failure: BaseException | None = None
        try:
            await runtime_handle.wait_closed()
        except asyncio.CancelledError:
            return
        except BaseException as exc:
            failure = exc

        async with self._guard:
            record = self._reservations.get(reservation_id)
        if record is None or record.claims != immutable_claims:
            return
        async with record.lock:
            if (
                record.state is not PipecatReservationState.ACTIVE
                or record.runtime_handle is not runtime_handle
            ):
                return
            await self._terminate_locked(
                record,
                reason=(
                    VoiceRuntimeTerminalReason.RUNTIME_UNAVAILABLE
                    if failure is not None
                    else VoiceRuntimeTerminalReason.RUNTIME_STOPPED
                ),
                retryable=True,
            )

    async def _peer_closed(
        self,
        *,
        reservation_id: str,
        immutable_claims: VoiceCallClaims,
        connection: PipecatPeerConnection,
    ) -> None:
        async with self._guard:
            record = self._reservations.get(reservation_id)
        if record is None or record.claims != immutable_claims:
            return
        async with record.lock:
            if record.state is PipecatReservationState.TERMINAL:
                return
            if record.pc_id is None or connection.pc_id != record.pc_id:
                return
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.CLIENT_DISCONNECTED,
                retryable=True,
            )

    async def _expire_unused_reservation(
        self,
        *,
        reservation_id: str,
        immutable_claims: VoiceCallClaims,
    ) -> None:
        try:
            while True:
                delay = max(
                    0.0,
                    (immutable_claims.expires_at - self._aware_now()).total_seconds(),
                )
                if delay <= 0:
                    break
                await self._sleep(delay)
            async with self._guard:
                record = self._reservations.get(reservation_id)
            if record is None or record.claims != immutable_claims:
                return
            async with record.lock:
                if record.state in {
                    PipecatReservationState.RESERVED,
                    PipecatReservationState.NEGOTIATING,
                }:
                    await self._terminate_locked(
                        record,
                        reason=VoiceRuntimeTerminalReason.ASSIGNMENT_EXPIRED,
                        retryable=False,
                    )
        except asyncio.CancelledError:
            return

    async def _claim_active_slot(self, record: _Reservation) -> None:
        async with self._guard:
            active_id = self._active_reservation_id
            if active_id is not None and active_id != record.peer_reservation_id:
                raise PipecatSignalingUnavailable("Pipecat active-call capacity is exhausted")
            self._active_reservation_id = record.peer_reservation_id

    async def _resolve_authorized(self, *, token: str, user_id: str) -> _Reservation:
        token_hash = _hash_token(_validate_bearer(token))
        async with self._guard:
            reservation_id = self._token_index.get(token_hash)
            record = self._reservations.get(reservation_id) if reservation_id else None
        if record is None or not hmac.compare_digest(record.token_hash, token_hash):
            raise PipecatSignalingNotFound("SmallWebRTC reservation not found")
        if not isinstance(user_id, str) or not hmac.compare_digest(record.claims.user_id, user_id):
            raise PipecatSignalingForbidden("Forbidden")
        return record

    async def _resolve_trusted_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> _Reservation:
        if not isinstance(voice_call_id, str):
            raise PipecatSignalingNotFound("SmallWebRTC reservation not found")
        async with self._guard:
            self._ensure_open_locked()
            reservation_id = self._call_index.get(voice_call_id)
            record = self._reservations.get(reservation_id) if reservation_id else None
        if record is None:
            raise PipecatSignalingNotFound("SmallWebRTC reservation not found")
        self._require_trusted_scope(
            record,
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )
        return record

    def _require_retained_trusted_call_locked(
        self,
        record: _Reservation,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        retained_id = self._call_index.get(voice_call_id)
        retained = self._reservations.get(retained_id) if retained_id else None
        if retained is not record:
            raise PipecatSignalingNotFound("SmallWebRTC reservation not found")
        self._require_trusted_scope(
            record,
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )

    def _ensure_trusted_release_task_locked(
        self,
        record: _Reservation,
        *,
        reason: VoiceRuntimeTerminalReason,
    ) -> asyncio.Task[VoiceRuntimeTerminalResult]:
        existing = record.trusted_release_task
        if existing is not None and not existing.done():
            return existing
        release_task = asyncio.create_task(
            self._finish_trusted_release(record=record, reason=reason),
            name=f"pipecat-trusted-release-{record.peer_reservation_id}",
        )
        release_task.add_done_callback(_consume_task_result)
        record.trusted_release_task = release_task
        return release_task

    @staticmethod
    def _require_trusted_scope(
        record: _Reservation,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        if not all(isinstance(value, str) for value in (user_id, session_id, voice_call_id)):
            raise PipecatSignalingForbidden("Forbidden")
        if not (
            _constant_time_text_equal(record.claims.user_id, user_id)
            and _constant_time_text_equal(record.claims.session_id, session_id)
            and _constant_time_text_equal(record.claims.voice_call_id, voice_call_id)
        ):
            raise PipecatSignalingForbidden("Forbidden")

    @staticmethod
    def _publish_cancel_intent(
        record: _Reservation,
        reason: VoiceRuntimeTerminalReason,
    ) -> None:
        if record.cancel_reason is None:
            record.cancel_reason = reason
        record.cancel_requested.set()

    async def _authorize_live_record(self, record: _Reservation, *, user_id: str) -> None:
        await self._authorize_record_ownership_locked(record, user_id=user_id)
        if record.state is PipecatReservationState.TERMINAL:
            raise PipecatSignalingConflict("SmallWebRTC reservation is terminal")

    async def _authorize_record_ownership_locked(
        self,
        record: _Reservation,
        *,
        user_id: str,
    ) -> None:
        """Revalidate authority and revoke an observed ownership mismatch."""

        self._require_immutable_claimant(record, user_id=user_id)
        try:
            await self._authorize_claims(record.claims)
        except (PipecatSignalingNotFound, PipecatSignalingForbidden):
            # The opaque token and immutable claimant were already verified, so
            # an authoritative absence or ownership mismatch revokes this exact
            # reservation. Repository availability failures remain retryable and
            # deliberately do not mutate live call state.
            await self._terminate_locked(
                record,
                reason=VoiceRuntimeTerminalReason.OWNER_MISMATCH,
                retryable=False,
            )
            raise

    @staticmethod
    def _require_immutable_claimant(record: _Reservation, *, user_id: str) -> None:
        if not hmac.compare_digest(record.claims.user_id, user_id):
            raise PipecatSignalingForbidden("Forbidden")

    async def _authorize_claims(self, claims: VoiceCallClaims) -> None:
        session = await self._repository_lookup(self._session_repo.get_by_id, claims.session_id)
        if session is None:
            raise PipecatSignalingNotFound("Authoritative voice session was not found")
        agent = await self._repository_lookup(self._agent_repo.get_by_id, claims.agent_id)
        if agent is None:
            raise PipecatSignalingNotFound("Authoritative voice agent was not found")
        if (
            session.id != claims.session_id
            or session.user_id != claims.user_id
            or session.agent_id != claims.agent_id
            or agent.id != claims.agent_id
            or agent.user_id != claims.user_id
        ):
            raise PipecatSignalingForbidden(
                "Authoritative voice ownership does not match the reservation"
            )

    async def _repository_lookup(
        self,
        lookup: Callable[[str], _RepositoryResult],
        key: str,
    ) -> _RepositoryResult:
        try:
            return await self._repository_runner.run(
                lookup,
                key,
                timeout_seconds=self.settings.repository_timeout_seconds,
            )
        except TimeoutError as exc:
            raise PipecatSignalingUnavailable(
                "Pipecat authoritative repository lookup timed out"
            ) from exc
        except BoundedSyncRunnerUnavailable as exc:
            raise PipecatSignalingUnavailable(
                "Pipecat authoritative repository capacity is exhausted"
            ) from exc
        except Exception as exc:
            raise PipecatSignalingUnavailable(
                "Pipecat authoritative repository lookup failed"
            ) from exc

    def _validate_claims_policy(self, claims: VoiceCallClaims, *, now: datetime) -> None:
        if claims.runtime is not VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1:
            raise PipecatSignalingConflict("Pipecat reservation runtime is unsupported")
        if claims.profile_id != self.settings.profile_id:
            raise PipecatSignalingConflict("Pipecat reservation profile is unsupported")
        if claims.issued_at > now + timedelta(seconds=self.settings.clock_skew_seconds):
            raise PipecatSignalingConflict("Pipecat reservation was issued in the future")
        if claims.expires_at <= now:
            raise PipecatSignalingConflict("Pipecat reservation expired; start a fresh call")
        configured_expiry = claims.issued_at + timedelta(
            seconds=self.settings.reservation_ttl_seconds
        )
        if claims.expires_at > configured_expiry:
            raise PipecatSignalingConflict("Pipecat reservation exceeds server TTL policy")

    def _validate_ice_lease(
        self,
        claims: VoiceCallClaims,
        ice_lease: object,
        *,
        now: datetime,
    ) -> None:
        if (
            not isinstance(ice_lease, PipecatIceLease)
            or ice_lease.claims != claims
            or ice_lease.expires_at != claims.expires_at
            or now >= ice_lease.expires_at
        ):
            raise PipecatSignalingUnavailable("Pipecat ICE lease scope or expiry is invalid")
        try:
            ice_lease.require_compatible_signaling_base_url(self.settings.signaling_base_url)
        except PipecatIceLeaseUnavailable as exc:
            raise PipecatSignalingUnavailable(
                "Pipecat ICE lease is incompatible with the signaling origin"
            ) from exc

    def _validated_new_token(self) -> str:
        token = _validate_bearer(self._token_factory())
        # A URL-safe 384-bit default is used. Injected generators must preserve
        # at least 256 bits of encoded entropy surface rather than returning a
        # convenient short identifier.
        if len(token) < 43:
            raise PipecatSignalingUnavailable("Pipecat token generator returned a weak token")
        return token

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise PipecatSignalingUnavailable("Pipecat signaling clock must be timezone-aware")
        return now.astimezone(UTC)

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise PipecatSignalingUnavailable("Pipecat signaling service is closed")

    def _prune_tombstones_locked(self, now: datetime) -> tuple[asyncio.Task[None], ...]:
        expired = [
            reservation_id
            for reservation_id, record in self._reservations.items()
            if record.state is PipecatReservationState.TERMINAL
            and record.tombstone_expires_at is not None
            and record.tombstone_expires_at <= now
            and record.runtime_handle is None
            and record.handler_cleanup_complete
        ]
        cleanup_tasks: list[asyncio.Task[None]] = []
        for reservation_id in expired:
            record = self._reservations.pop(reservation_id)
            cleanup_task, record.cleanup_retry_task = record.cleanup_retry_task, None
            if cleanup_task is not None and cleanup_task is not asyncio.current_task():
                cleanup_task.cancel()
                cleanup_tasks.append(cleanup_task)
            self._token_index.pop(record.token_hash, None)
            if self._call_index.get(record.claims.voice_call_id) == reservation_id:
                self._call_index.pop(record.claims.voice_call_id, None)
        return tuple(cleanup_tasks)

    @staticmethod
    def _require_exact_pc_id(record: _Reservation, supplied_pc_id: str | None) -> None:
        if supplied_pc_id is None or record.pc_id is None:
            raise PipecatSignalingConflict("SmallWebRTC peer connection ID is required")
        if not hmac.compare_digest(record.pc_id, supplied_pc_id):
            raise PipecatSignalingConflict("SmallWebRTC peer connection ID does not match")

    @staticmethod
    def _cancel_expiry_task(record: _Reservation) -> None:
        expiry_task, record.expiry_task = record.expiry_task, None
        if expiry_task is not None and expiry_task is not asyncio.current_task():
            expiry_task.cancel()


def _validate_pc_id(pc_id: str) -> None:
    if (
        not isinstance(pc_id, str)
        or not pc_id
        or pc_id != pc_id.strip()
        or len(pc_id) > _MAX_PC_ID_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in pc_id)
    ):
        raise ValueError("SmallWebRTC peer connection ID is invalid")


def _validate_bearer(token: object) -> str:
    if (
        not isinstance(token, str)
        or not token
        or token != token.strip()
        or len(token) > _MAX_TOKEN_LENGTH
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        raise PipecatSignalingNotFound("SmallWebRTC reservation not found")
    return token


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _constant_time_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    task.exception()


def _normalize_signaling_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url or base_url != base_url.strip():
        raise ValueError("Pipecat signaling base URL is invalid")
    try:
        parsed = urlsplit(base_url)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("Pipecat signaling base URL is malformed") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Pipecat signaling base URL must be a credential-free URL")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and _is_loopback_hostname(parsed.hostname)
    ):
        raise ValueError("Pipecat signaling base URL must use HTTPS or loopback HTTP")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _build_signaling_url(base_url: str, raw_token: str) -> str:
    normalized = _normalize_signaling_base_url(base_url)
    parsed = urlsplit(normalized)
    path = f"{parsed.path}/{raw_token}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_cors_origin(origin: object) -> None:
    if not isinstance(origin, str) or not origin or origin != origin.strip() or origin == "*":
        raise ValueError("Pipecat CORS origin is invalid")
    try:
        parsed = urlsplit(origin)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("Pipecat CORS origin is malformed") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Pipecat CORS origin must be an HTTP(S) origin")


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validated_answer(raw_answer: dict[str, str] | None) -> PipecatOfferAnswer:
    if not isinstance(raw_answer, dict) or set(raw_answer) != {"sdp", "type", "pc_id"}:
        raise ValueError("SmallWebRTC answer has unexpected fields")
    return PipecatOfferAnswer(
        sdp=raw_answer["sdp"],
        type=raw_answer["type"],
        pc_id=raw_answer["pc_id"],
    )


__all__ = [
    "AgentRepository",
    "PipecatCorsContract",
    "PipecatHandlerRequestTypes",
    "PipecatIceCandidate",
    "PipecatOfferAnswer",
    "PipecatOfferRequest",
    "PipecatPatchRequest",
    "PipecatPeerConnection",
    "PipecatPeerHandler",
    "PipecatPeerHandlerAdapter",
    "PipecatReservationSnapshot",
    "PipecatReservationState",
    "PipecatRuntimeHandle",
    "PipecatRuntimeStarter",
    "PipecatSignalingConflict",
    "PipecatSignalingError",
    "PipecatSignalingForbidden",
    "PipecatSignalingNotFound",
    "PipecatSignalingService",
    "PipecatSignalingSettings",
    "PipecatSignalingUnavailable",
    "SessionRepository",
]

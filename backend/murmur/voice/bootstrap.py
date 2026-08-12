"""Authenticated Voice V2 control-plane bootstrap without SDK-specific types."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
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

VOICE_V2_RUNTIME = "livekit_v2"
VOICE_V2_EVENT_TOPIC = "murmur.voice.v2.events"
SIGNED_METADATA_VERSION = 1
SIGNED_METADATA_ALGORITHM = "hmac-sha256"
RELEASE_TOMBSTONE_TTL_SECONDS = 900
_ControlResult = TypeVar("_ControlResult")
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
class CreateRoomSpec:
    name: str
    metadata: str
    empty_timeout_seconds: int
    departure_timeout_seconds: int
    max_participants: int = 2


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


@dataclass(frozen=True)
class _TrustedScope:
    user_id: str
    session_id: str
    agent_id: str
    voice_call_id: str


@dataclass(frozen=True)
class _CallAssignment:
    scope: _TrustedScope
    trace_id: str
    job_issued_at: int
    job_expires_at: int
    dispatch_id: str | None = None
    participant_expires_at: int | None = None


@dataclass(frozen=True)
class _ReleaseTombstone:
    scope: _TrustedScope
    expires_at: int


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class _KeyedLockRegistry:
    """Reference-counted per-key locks that do not leak completed call IDs."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[str, _LockEntry] = {}

    @asynccontextmanager
    async def hold(
        self,
        key: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.setdefault(key, _LockEntry(lock=asyncio.Lock()))
            entry.users += 1

        acquired = False
        try:
            if timeout_seconds is None:
                await entry.lock.acquire()
            else:
                await asyncio.wait_for(entry.lock.acquire(), timeout=timeout_seconds)
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(key) is entry:
                    self._entries.pop(key, None)

    @property
    def active_key_count(self) -> int:
        return len(self._entries)


class UnavailableVoiceBootstrapService:
    """Explicitly fails when Voice V2 is disabled or incompletely configured."""

    def __init__(self, message: str = "Voice V2 bootstrap is unavailable") -> None:
        self._message = message

    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceBootstrapResult:
        del user_id, session_id, voice_call_id
        raise VoiceBootstrapUnavailable(self._message)

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        del user_id, session_id, voice_call_id
        raise VoiceBootstrapUnavailable(self._message)

    async def aclose(self) -> None:
        """Match the configured service lifecycle without allocating resources."""


class VoiceBootstrapService:
    """Authorize and reconcile one retry-stable Voice V2 call assignment."""

    def __init__(
        self,
        control_plane: VoiceControlPlane,
        settings: VoiceBootstrapSettings,
        *,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._control_plane = control_plane
        self.settings = settings
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._call_locks = _KeyedLockRegistry()
        self._assignments_guard = asyncio.Lock()
        self._bootstrap_capacity = asyncio.BoundedSemaphore(settings.max_concurrent_bootstraps)
        # Release must never queue behind bootstrap admission: a user cancel
        # has to be able to tombstone and clean a bootstrap that is already in
        # flight. It still has its own bound so release floods cannot create an
        # unbounded number of repository reads or keyed-lock waiters.
        self._release_capacity = asyncio.BoundedSemaphore(settings.max_concurrent_bootstraps)
        # Milestone 1 is deliberately process-local. Durable assignment lands in M3.
        self._assignments: dict[str, _CallAssignment] = {}
        self._release_tombstones: dict[str, _ReleaseTombstone] = {}
        # Once tombstone storage saturates, admitting any previously unseen
        # call would make a rejected cancel indistinguishable from no cancel.
        # Fail new calls closed until the oldest unexpired intent ages out.
        self._release_tombstone_overflow_until: int | None = None

    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceBootstrapResult:
        async with self._hold_bootstrap_capacity():
            scope = await self._authorize_scope(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            async with self._hold_call_lock(voice_call_id):
                return await self._bootstrap_locked(scope)

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        """Release one exact trusted assignment after confirmed remote cleanup."""
        async with self._hold_release_capacity():
            scope = await self._authorize_scope(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            # Record the negative intent before waiting behind an in-flight
            # bootstrap. The bootstrap rechecks it while holding the same call
            # lock, so cancellation cannot lose merely because reconciliation
            # is slow. Scope validation and mutation are atomic under the
            # assignment guard.
            await self._record_release_intent(scope)
            async with self._hold_call_lock(voice_call_id):
                assignment = self._assignments.get(voice_call_id)
                if assignment is not None and assignment.scope != scope:
                    raise VoiceBootstrapConflict(
                        "voice_call_id is assigned to another trusted scope"
                    )
                if assignment is not None:
                    await self._cleanup_remote_assignment(assignment)
                    await self._retire_assignment(assignment)

    async def _authorize_scope(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> _TrustedScope:
        session = await self._repository_lookup(self._session_repo.get_by_id, session_id)
        if session is None:
            raise VoiceBootstrapNotFound("Session not found")
        if session.user_id != user_id:
            raise VoiceBootstrapForbidden("Forbidden")

        agent = await self._repository_lookup(self._agent_repo.get_by_id, session.agent_id)
        if agent is None:
            raise VoiceBootstrapNotFound("Agent not found")
        if agent.user_id != user_id or agent.id != session.agent_id:
            raise VoiceBootstrapForbidden("Forbidden")

        scope = _TrustedScope(
            user_id=user_id,
            session_id=session.id,
            agent_id=agent.id,
            voice_call_id=voice_call_id,
        )
        if not isinstance(scope.user_id, str) or not scope.user_id or len(scope.user_id) > 128:
            raise VoiceBootstrapConflict("authoritative user_id is not compatible with Voice V2")
        for name, value in (
            ("session_id", scope.session_id),
            ("agent_id", scope.agent_id),
            ("voice_call_id", scope.voice_call_id),
        ):
            if not _CONTRACT_ID.fullmatch(value):
                raise VoiceBootstrapConflict(
                    f"authoritative {name} is not compatible with Voice V2"
                )
        return scope

    @asynccontextmanager
    async def _hold_bootstrap_capacity(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(
                self._bootstrap_capacity.acquire(),
                timeout=self.settings.control_plane_timeout_seconds,
            )
        except TimeoutError as exc:
            raise VoiceBootstrapUnavailable("Voice V2 bootstrap capacity is exhausted") from exc
        try:
            yield
        finally:
            self._bootstrap_capacity.release()

    @asynccontextmanager
    async def _hold_release_capacity(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(
                self._release_capacity.acquire(),
                timeout=self.settings.control_plane_timeout_seconds,
            )
        except TimeoutError as exc:
            raise VoiceBootstrapUnavailable("Voice V2 release capacity is exhausted") from exc
        try:
            yield
        finally:
            self._release_capacity.release()

    @asynccontextmanager
    async def _hold_call_lock(self, voice_call_id: str) -> AsyncIterator[None]:
        try:
            async with self._call_locks.hold(
                voice_call_id,
                timeout_seconds=self.settings.control_plane_timeout_seconds,
            ):
                yield
        except TimeoutError as exc:
            raise VoiceBootstrapUnavailable("Voice V2 call coordination timed out") from exc

    async def _repository_lookup(
        self,
        lookup: Callable[[str], _ControlResult],
        key: str,
    ) -> _ControlResult:
        try:
            return await self._repository_runner.run(
                lookup,
                key,
                timeout_seconds=self.settings.repository_timeout_seconds,
            )
        except TimeoutError as exc:
            raise VoiceBootstrapUnavailable("Voice V2 repository lookup timed out") from exc
        except BoundedSyncRunnerUnavailable as exc:
            raise VoiceBootstrapUnavailable("Voice V2 repository capacity is exhausted") from exc
        except Exception as exc:
            raise VoiceBootstrapUnavailable("Voice V2 repository lookup failed") from exc

    async def _bootstrap_locked(self, scope: _TrustedScope) -> VoiceBootstrapResult:
        blocker = await self._bootstrap_blocker(scope, include_overflow=False)
        self._raise_bootstrap_blocker(blocker)

        assignment = self._assignments.get(scope.voice_call_id)
        if assignment is not None and assignment.scope != scope:
            raise VoiceBootstrapConflict("voice_call_id is already assigned to another scope")
        if (
            assignment is not None
            and int(self._aware_now().timestamp()) >= assignment.job_expires_at
        ):
            raise VoiceBootstrapConflict("voice_call_id assignment expired; start a new call")
        created_assignment = assignment is None
        if created_assignment:
            assignment = await self._admit_new_assignment(scope)

        # A release may have been recorded before this bootstrap acquired its
        # call lock. Recheck after admission so no remote work starts for a
        # canceled call.
        try:
            blocker = await self._bootstrap_blocker(scope, include_overflow=False)
            self._raise_bootstrap_blocker(blocker)
        except VoiceBootstrapError:
            if created_assignment:
                if blocker == "released":
                    await self._retire_assignment(assignment)
                else:
                    await self._discard_assignment(assignment)
            raise

        room_name = derive_room_name(self.settings, scope)
        participant_identity = derive_participant_identity(self.settings, scope)
        agent_participant_identity = derive_agent_participant_identity(self.settings, scope)
        metadata_payload = self._metadata_payload(
            scope,
            room_name,
            assignment.trace_id,
            participant_identity,
            agent_participant_identity,
            assignment.job_issued_at,
            assignment.job_expires_at,
        )
        room_metadata = sign_metadata(
            metadata_payload,
            self.settings.signing_secret,
            purpose="room",
        )
        job_metadata = sign_metadata(
            metadata_payload,
            self.settings.signing_secret,
            purpose="job",
        )

        try:
            await self._reconcile_room(room_name, room_metadata)
            dispatch = await self._reconcile_dispatch(
                assignment,
                room_name=room_name,
                job_metadata=job_metadata,
            )
            if assignment.dispatch_id is None:
                assignment = replace(assignment, dispatch_id=dispatch.id)
                self._assignments[scope.voice_call_id] = assignment

            issued_at = self._aware_now()
            expires_at = issued_at + timedelta(seconds=self.settings.token_ttl_seconds)
            participant_metadata = sign_metadata(
                metadata_payload,
                self.settings.signing_secret,
                purpose="participant",
            )
            token = self._control_plane.issue_participant_token(
                ParticipantTokenSpec(
                    identity=participant_identity,
                    name="Murmur user",
                    metadata=participant_metadata,
                    issued_at=issued_at,
                    expires_at=expires_at,
                    grants=ParticipantGrants(room_name=room_name),
                )
            )
            assignment = replace(
                assignment,
                participant_expires_at=int(expires_at.timestamp()),
            )
            self._assignments[scope.voice_call_id] = assignment
        except VoiceBootstrapError:
            raise
        except Exception as exc:
            raise VoiceBootstrapUnavailable("Voice V2 control plane is unavailable") from exc

        if not token:
            raise VoiceBootstrapUnavailable("Voice V2 token issuer returned an empty token")

        try:
            blocker = await self._bootstrap_blocker(scope, include_overflow=False)
            self._raise_bootstrap_blocker(blocker)
        except VoiceBootstrapError:
            # A cancel may arrive while this call is reconciling. A newly
            # admitted call has never returned a usable token, so clean it
            # synchronously. For an existing assignment, only an exact release
            # intent may terminate it; global overflow could belong to another
            # call and therefore only blocks fresh token issuance.
            if created_assignment or blocker == "released":
                await self._cleanup_remote_assignment(assignment)
                if blocker == "released":
                    await self._retire_assignment(assignment)
                else:
                    await self._discard_assignment(assignment)
            raise

        return VoiceBootstrapResult(
            runtime=VOICE_V2_RUNTIME,
            profile_id=self.settings.profile_id,
            server_url=normalize_server_url(self.settings.server_url),
            room_name=room_name,
            participant_token=token,
            participant_identity=participant_identity,
            agent_participant_identity=agent_participant_identity,
            session_id=scope.session_id,
            agent_id=scope.agent_id,
            voice_call_id=scope.voice_call_id,
            dispatch_id=dispatch.id,
            worker_name=self.settings.worker_name,
            event_topic=self.settings.event_topic,
            trace_id=assignment.trace_id,
            expires_at=expires_at,
        )

    async def _admit_new_assignment(self, scope: _TrustedScope) -> _CallAssignment:
        """Atomically reserve capacity, reclaiming only provably inactive stale calls."""
        async with self._assignments_guard:
            now = int(self._aware_now().timestamp())
            self._raise_bootstrap_blocker(self._bootstrap_blocker_locked(scope, now))
            if (
                len(self._assignments) < self.settings.max_active_calls
                and self._registry_size_locked() < self.settings.max_call_assignments
            ):
                return self._create_assignment(scope)
            stale_call_ids = [
                call_id
                for call_id, assignment in self._assignments.items()
                if self._assignment_is_expired(assignment)
            ]

        for stale_call_id in stale_call_ids:
            if await self._try_reclaim_stale_assignment(stale_call_id):
                # One admission needs one slot. Reclaiming more calls would
                # terminate unrelated stale sessions unnecessarily.
                break

        async with self._assignments_guard:
            now = int(self._aware_now().timestamp())
            self._raise_bootstrap_blocker(self._bootstrap_blocker_locked(scope, now))
            if len(self._assignments) >= self.settings.max_active_calls:
                raise VoiceBootstrapUnavailable("Voice V2 active-call capacity is exhausted")
            if self._registry_size_locked() >= self.settings.max_call_assignments:
                raise VoiceBootstrapUnavailable("Voice V2 call-assignment capacity is exhausted")
            return self._create_assignment(scope)

    def _create_assignment(self, scope: _TrustedScope) -> _CallAssignment:
        if self._registry_size_locked() >= self.settings.max_call_assignments:
            raise VoiceBootstrapUnavailable("Voice V2 call-assignment capacity is exhausted")
        job_issued_at = int(self._aware_now().timestamp())
        assignment = _CallAssignment(
            scope=scope,
            trace_id=str(uuid4()),
            job_issued_at=job_issued_at,
            job_expires_at=job_issued_at + self.settings.job_metadata_ttl_seconds,
        )
        self._assignments[scope.voice_call_id] = assignment
        return assignment

    def _registry_size_locked(self) -> int:
        return len(self._assignments.keys() | self._release_tombstones.keys())

    def _assignment_is_expired(self, assignment: _CallAssignment) -> bool:
        latest_expiry = max(
            assignment.job_expires_at,
            assignment.participant_expires_at or assignment.job_expires_at,
        )
        return int(self._aware_now().timestamp()) >= latest_expiry

    async def _bootstrap_blocker(
        self,
        scope: _TrustedScope,
        *,
        include_overflow: bool,
    ) -> str | None:
        now = int(self._aware_now().timestamp())
        async with self._assignments_guard:
            return self._bootstrap_blocker_locked(
                scope,
                now,
                include_overflow=include_overflow,
            )

    def _bootstrap_blocker_locked(
        self,
        scope: _TrustedScope,
        now: int,
        *,
        include_overflow: bool = True,
    ) -> str | None:
        self._prune_release_tombstones(now)
        tombstone = self._release_tombstones.get(scope.voice_call_id)
        if tombstone is not None:
            if tombstone.scope != scope:
                raise VoiceBootstrapConflict("voice_call_id was released by another trusted scope")
            return "released"
        if (
            include_overflow
            and self._release_tombstone_overflow_until is not None
            and now < self._release_tombstone_overflow_until
        ):
            return "saturated"
        return None

    @staticmethod
    def _raise_bootstrap_blocker(blocker: str | None) -> None:
        if blocker == "released":
            raise VoiceBootstrapConflict("voice_call_id was released; start a new call")
        if blocker == "saturated":
            raise VoiceBootstrapUnavailable("Voice V2 cancellation state is saturated; retry later")

    async def _record_release_intent(self, scope: _TrustedScope) -> None:
        now = int(self._aware_now().timestamp())
        async with self._assignments_guard:
            self._prune_release_tombstones(now)
            assignment = self._assignments.get(scope.voice_call_id)
            if assignment is not None and assignment.scope != scope:
                raise VoiceBootstrapConflict("voice_call_id is assigned to another trusted scope")
            expires_at = now + RELEASE_TOMBSTONE_TTL_SECONDS
            existing = self._release_tombstones.get(scope.voice_call_id)
            if existing is not None:
                if existing.scope != scope:
                    raise VoiceBootstrapConflict(
                        "voice_call_id was released by another trusted scope"
                    )
                self._release_tombstones[scope.voice_call_id] = _ReleaseTombstone(
                    scope=scope,
                    expires_at=expires_at,
                )
                return
            if assignment is not None:
                # This ID already occupies a bounded registry slot; adding its
                # exact cancellation marker cannot increase registry capacity.
                self._release_tombstones[scope.voice_call_id] = _ReleaseTombstone(
                    scope=scope,
                    expires_at=expires_at,
                )
                return
            if self._registry_size_locked() >= self.settings.max_call_assignments:
                # An unexpired tombstone is an authoritative negative call
                # intent. Evicting one can resurrect an uncertain assignment,
                # so capacity exhaustion fails closed until an intent expires.
                self._release_tombstone_overflow_until = max(
                    self._release_tombstone_overflow_until or 0,
                    expires_at,
                )
                raise VoiceBootstrapUnavailable("Voice V2 release-tombstone capacity is exhausted")
            self._release_tombstones[scope.voice_call_id] = _ReleaseTombstone(
                scope=scope,
                expires_at=expires_at,
            )

    def _prune_release_tombstones(self, now: int) -> None:
        expired = [
            voice_call_id
            for voice_call_id, tombstone in self._release_tombstones.items()
            if now >= tombstone.expires_at
        ]
        for voice_call_id in expired:
            self._release_tombstones.pop(voice_call_id, None)
        if (
            self._release_tombstone_overflow_until is not None
            and now >= self._release_tombstone_overflow_until
        ):
            self._release_tombstone_overflow_until = None

    async def _try_reclaim_stale_assignment(self, voice_call_id: str) -> bool:
        async with self._hold_call_lock(voice_call_id):
            assignment = self._assignments.get(voice_call_id)
            if assignment is None or not self._assignment_is_expired(assignment):
                return assignment is None

            room, _dispatch = await self._read_remote_state(assignment)
            if room is None:
                # Explicit dispatches are room-bound. Once the room is absent,
                # LiveKit has already terminated its worker job; listing the
                # dispatch may itself return room-not-found on the real server.
                await self._retire_assignment(assignment)
                return True

            if room.num_participants < 0:
                raise VoiceBootstrapUnavailable(
                    "Voice V2 room returned an invalid participant count"
                )
            # Neither room nor dispatch deletion is conditional on the room
            # remaining empty. Mutating either can race a participant join and
            # kill its worker, so stale admission waits for LiveKit's configured
            # empty-room expiry and only reclaims after observing absence.
            return False

    async def _retire_assignment(self, assignment: _CallAssignment) -> None:
        now = int(self._aware_now().timestamp())
        async with self._assignments_guard:
            self._prune_release_tombstones(now)
            current = self._assignments.get(assignment.scope.voice_call_id)
            if (
                current is None
                or current.scope != assignment.scope
                or current.trace_id != assignment.trace_id
            ):
                return
            existing = self._release_tombstones.get(assignment.scope.voice_call_id)
            if existing is not None and existing.scope != assignment.scope:
                raise VoiceBootstrapConflict("voice_call_id was released by another trusted scope")
            expires_at = max(
                existing.expires_at if existing is not None else 0,
                now + RELEASE_TOMBSTONE_TTL_SECONDS,
            )
            self._assignments.pop(assignment.scope.voice_call_id, None)
            self._release_tombstones[assignment.scope.voice_call_id] = _ReleaseTombstone(
                scope=assignment.scope,
                expires_at=expires_at,
            )

    async def _discard_assignment(self, assignment: _CallAssignment) -> None:
        """Drop one exact local reservation without mutating release intent."""
        async with self._assignments_guard:
            current = self._assignments.get(assignment.scope.voice_call_id)
            if (
                current is not None
                and current.scope == assignment.scope
                and current.trace_id == assignment.trace_id
            ):
                self._assignments.pop(assignment.scope.voice_call_id, None)

    async def _read_remote_state(
        self,
        assignment: _CallAssignment,
    ) -> tuple[RoomRecord | None, DispatchRecord | None]:
        room_name, expected_room_metadata, expected_job_metadata = self._expected_remote_state(
            assignment
        )
        room = await self._control_call(self._control_plane.get_room(room_name))
        if room is None:
            return None, None
        dispatches = list(await self._control_call(self._control_plane.list_dispatches(room_name)))
        if room is not None and (room.name != room_name or room.metadata != expected_room_metadata):
            raise VoiceBootstrapConflict("Voice room metadata conflicts with the assignment")
        if len(dispatches) > 1:
            raise VoiceBootstrapConflict("Voice room has an ambiguous agent dispatch state")
        dispatch = dispatches[0] if dispatches else None
        if dispatch is not None and (
            not dispatch.id
            or dispatch.room_name != room_name
            or dispatch.agent_name != self.settings.worker_name
            or dispatch.metadata != expected_job_metadata
            or (assignment.dispatch_id is not None and dispatch.id != assignment.dispatch_id)
            or dispatch.deleted_at < 0
        ):
            raise VoiceBootstrapConflict("Voice worker dispatch conflicts with the assignment")
        return room, dispatch

    async def _cleanup_remote_assignment(
        self,
        assignment: _CallAssignment,
    ) -> bool:
        room_name, expected_room_metadata, _expected_job_metadata = self._expected_remote_state(
            assignment
        )
        room, dispatch = await self._read_remote_state(assignment)
        if room is not None and room.num_participants < 0:
            raise VoiceBootstrapUnavailable("Voice V2 room returned an invalid participant count")

        if dispatch is not None and dispatch.deleted_at == 0:
            delete_error: VoiceBootstrapUnavailable | None = None
            try:
                await self._control_call(
                    self._control_plane.delete_dispatch(dispatch.id, room_name)
                )
            except VoiceBootstrapUnavailable as exc:
                delete_error = exc

            # LiveKit may remove the room as a side effect of terminating its
            # last dispatch. Its OSS dispatch-list endpoint then returns an
            # error for that absent room, while room absence already confirms
            # both resources are gone.
            room_after_dispatch_delete = await self._control_call(
                self._control_plane.get_room(room_name)
            )
            if room_after_dispatch_delete is None:
                return True
            if (
                room_after_dispatch_delete.name != room_name
                or room_after_dispatch_delete.metadata != expected_room_metadata
            ):
                raise VoiceBootstrapConflict("Voice room state changed during cleanup")
            if room_after_dispatch_delete.num_participants < 0:
                raise VoiceBootstrapUnavailable(
                    "Voice V2 room returned an invalid participant count"
                )

            remaining = list(
                await self._control_call(self._control_plane.list_dispatches(room_name))
            )
            if remaining:
                if (
                    len(remaining) > 1
                    or remaining[0].id != dispatch.id
                    or remaining[0].room_name != dispatch.room_name
                    or remaining[0].agent_name != dispatch.agent_name
                    or remaining[0].metadata != dispatch.metadata
                    or remaining[0].deleted_at < 0
                ):
                    raise VoiceBootstrapConflict("Voice dispatch state changed during cleanup")
                if remaining[0].deleted_at == 0:
                    raise VoiceBootstrapUnavailable(
                        "Voice dispatch cleanup could not be confirmed"
                    ) from delete_error

        room = await self._control_call(self._control_plane.get_room(room_name))
        if room is not None and (room.name != room_name or room.metadata != expected_room_metadata):
            raise VoiceBootstrapConflict("Voice room state changed during cleanup")
        if room is not None and room.num_participants < 0:
            raise VoiceBootstrapUnavailable("Voice V2 room returned an invalid participant count")
        if room is not None:
            delete_error = None
            try:
                await self._control_call(self._control_plane.delete_room(room_name))
            except VoiceBootstrapUnavailable as exc:
                delete_error = exc
            remaining_room = await self._control_call(self._control_plane.get_room(room_name))
            if remaining_room is not None:
                if (
                    remaining_room.name != room_name
                    or remaining_room.metadata != expected_room_metadata
                ):
                    raise VoiceBootstrapConflict("Voice room state changed during cleanup")
                if remaining_room.num_participants < 0:
                    raise VoiceBootstrapUnavailable(
                        "Voice V2 room returned an invalid participant count"
                    )
                raise VoiceBootstrapUnavailable(
                    "Voice room cleanup could not be confirmed"
                ) from delete_error

        final_room = await self._control_call(self._control_plane.get_room(room_name))
        if final_room is None:
            return True
        if final_room.name != room_name or final_room.metadata != expected_room_metadata:
            raise VoiceBootstrapConflict("Voice room state changed during cleanup")
        final_dispatches = list(
            await self._control_call(self._control_plane.list_dispatches(room_name))
        )
        if final_dispatches:
            if (
                len(final_dispatches) != 1
                or dispatch is None
                or final_dispatches[0].id != dispatch.id
                or final_dispatches[0].room_name != dispatch.room_name
                or final_dispatches[0].agent_name != dispatch.agent_name
                or final_dispatches[0].metadata != dispatch.metadata
                or final_dispatches[0].deleted_at <= 0
            ):
                raise VoiceBootstrapUnavailable("Voice dispatch cleanup could not be confirmed")
        return True

    def _expected_remote_state(
        self,
        assignment: _CallAssignment,
    ) -> tuple[str, str, str]:
        scope = assignment.scope
        room_name = derive_room_name(self.settings, scope)
        payload = self._metadata_payload(
            scope,
            room_name,
            assignment.trace_id,
            derive_participant_identity(self.settings, scope),
            derive_agent_participant_identity(self.settings, scope),
            assignment.job_issued_at,
            assignment.job_expires_at,
        )
        return (
            room_name,
            sign_metadata(payload, self.settings.signing_secret, purpose="room"),
            sign_metadata(payload, self.settings.signing_secret, purpose="job"),
        )

    async def _reconcile_room(self, room_name: str, expected_metadata: str) -> None:
        room = await self._control_call(self._control_plane.get_room(room_name))
        if room is None:
            room = await self._control_call(
                self._control_plane.create_room(
                    CreateRoomSpec(
                        name=room_name,
                        metadata=expected_metadata,
                        empty_timeout_seconds=self.settings.room_empty_timeout_seconds,
                        departure_timeout_seconds=self.settings.room_departure_timeout_seconds,
                    )
                )
            )
        if room.name != room_name or room.metadata != expected_metadata:
            raise VoiceBootstrapConflict("Voice room metadata conflicts with the assignment")

    async def _reconcile_dispatch(
        self,
        assignment: _CallAssignment,
        *,
        room_name: str,
        job_metadata: str,
    ) -> DispatchRecord:
        dispatches = list(await self._control_call(self._control_plane.list_dispatches(room_name)))
        if len(dispatches) > 1:
            raise VoiceBootstrapConflict("Voice room has an ambiguous agent dispatch state")

        if dispatches and dispatches[0].deleted_at > 0:
            raise VoiceBootstrapConflict("Voice worker dispatch is already deleted")
        if dispatches:
            dispatch = dispatches[0]
        elif assignment.dispatch_id is not None:
            raise VoiceBootstrapConflict("Assigned voice dispatch is no longer present")
        else:
            dispatch = await self._control_call(
                self._control_plane.create_dispatch(
                    CreateDispatchSpec(
                        room_name=room_name,
                        agent_name=self.settings.worker_name,
                        metadata=job_metadata,
                    )
                )
            )

        if (
            not dispatch.id
            or dispatch.room_name != room_name
            or dispatch.agent_name != self.settings.worker_name
            or dispatch.metadata != job_metadata
        ):
            raise VoiceBootstrapConflict("Voice worker dispatch conflicts with the assignment")
        if assignment.dispatch_id is not None and dispatch.id != assignment.dispatch_id:
            raise VoiceBootstrapConflict("Voice dispatch identity changed during retry")
        return dispatch

    def _metadata_payload(
        self,
        scope: _TrustedScope,
        room_name: str,
        trace_id: str,
        participant_identity: str,
        agent_participant_identity: str,
        job_issued_at: int,
        job_expires_at: int,
    ) -> dict[str, object]:
        return {
            "agent_id": scope.agent_id,
            "agent_participant_identity": agent_participant_identity,
            "environment": self.settings.environment,
            "event_topic": self.settings.event_topic,
            "job_expires_at": job_expires_at,
            "job_issued_at": job_issued_at,
            "participant_identity": participant_identity,
            "profile_id": self.settings.profile_id,
            "room_name": room_name,
            "runtime": self.settings.runtime,
            "session_id": scope.session_id,
            "trace_id": trace_id,
            "user_id": scope.user_id,
            "voice_call_id": scope.voice_call_id,
            "worker_name": self.settings.worker_name,
        }

    async def _control_call(
        self,
        operation: Awaitable[_ControlResult],
    ) -> _ControlResult:
        try:
            return await asyncio.wait_for(
                operation,
                timeout=self.settings.control_plane_timeout_seconds,
            )
        except VoiceBootstrapError:
            raise
        except Exception as exc:
            raise VoiceBootstrapUnavailable("Voice V2 control plane is unavailable") from exc

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise VoiceBootstrapUnavailable("Voice V2 clock must return an aware timestamp")
        return now.astimezone(UTC)

    @property
    def active_lock_count(self) -> int:
        """Expose lock cleanup for deterministic lifecycle tests."""
        return self._call_locks.active_key_count

    @property
    def active_assignment_count(self) -> int:
        """Expose process-local admission state for deterministic lifecycle tests."""
        return len(self._assignments)

    @property
    def release_tombstone_count(self) -> int:
        """Expose bounded cancellation state for deterministic lifecycle tests."""
        return len(self._release_tombstones)

    async def aclose(self) -> None:
        """Release the owned control-plane client, if it has one."""
        close = getattr(self._control_plane, "aclose", None)
        if close is not None:
            await close()


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


def derive_room_name(settings: VoiceBootstrapSettings, scope: _TrustedScope) -> str:
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
    scope: _TrustedScope,
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
    scope: _TrustedScope,
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

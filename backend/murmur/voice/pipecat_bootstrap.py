"""Retry-stable authenticated ownership for Pipecat voice assignments.

This module is the process-local control-plane owner for the Milestone 1B
challenger.  It deliberately owns neither HTTP projection nor SmallWebRTC
media.  One exact authenticated call scope is authorized against the existing
repositories, admitted into a bounded registry, provisioned once through the
signaling service, and coupled to one immutable ICE lease.

The registry is intentionally process-local until durable voice-call
assignments land in a later milestone.  Negative release intent is retained
for the maximum supported assignment lifetime so a slow or cancelled
bootstrap cannot resurrect a call after its caller has gone away.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.bootstrap_lifecycle import CallLockRegistry
from murmur.voice.pipecat_ice import (
    PipecatIceLease,
    PipecatIceLeaseIssuer,
    PipecatIceLeaseUnavailable,
)
from murmur.voice.pipecat_signaling import (
    PipecatReservationSnapshot,
    PipecatReservationState,
    PipecatSignalingConflict,
    PipecatSignalingForbidden,
    PipecatSignalingNotFound,
    PipecatSignalingUnavailable,
)
from murmur.voice.runtime_contracts import (
    MAX_ASSIGNMENT_TTL_SECONDS,
    MIN_ASSIGNMENT_TTL_SECONDS,
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeKind,
    VoiceRuntimeTerminalResult,
)

PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS = MAX_ASSIGNMENT_TTL_SECONDS
_CONTRACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RepositoryResult = TypeVar("_RepositoryResult")


class PipecatBootstrapError(Exception):
    """Base class for expected, safe-to-map bootstrap failures."""


class PipecatBootstrapNotFound(PipecatBootstrapError):
    """The authoritative session or agent does not exist."""


class PipecatBootstrapForbidden(PipecatBootstrapError):
    """The authenticated identity does not own the requested call scope."""


class PipecatBootstrapConflict(PipecatBootstrapError):
    """The call ID conflicts with retained or terminal ownership state."""


class PipecatBootstrapUnavailable(PipecatBootstrapError):
    """Bounded capacity or a required internal owner is unavailable."""


@dataclass(frozen=True)
class PipecatBootstrapSettings:
    """Server-owned policy for process-local Pipecat assignment ownership."""

    profile_id: str
    assignment_ttl_seconds: int = 300
    repository_timeout_seconds: float = 2.0
    operation_timeout_seconds: float = 10.0
    coordination_timeout_seconds: float = 5.0
    max_concurrent_bootstraps: int = 100
    max_active_calls: int = 1
    max_call_assignments: int = 10_000

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not _CONTRACT_ID.fullmatch(self.profile_id):
            raise ValueError("Pipecat bootstrap profile_id is invalid")
        if (
            isinstance(self.assignment_ttl_seconds, bool)
            or not isinstance(self.assignment_ttl_seconds, int)
            or not MIN_ASSIGNMENT_TTL_SECONDS
            <= self.assignment_ttl_seconds
            <= MAX_ASSIGNMENT_TTL_SECONDS
        ):
            raise ValueError("Pipecat assignment TTL must be between 30 and 900 seconds")
        for name, value in (
            ("repository", self.repository_timeout_seconds),
            ("operation", self.operation_timeout_seconds),
            ("coordination", self.coordination_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not 0 < value <= 30
            ):
                raise ValueError(
                    f"Pipecat bootstrap {name} timeout must be between 0 and 30 seconds"
                )
        for name, value in (
            ("concurrent-bootstrap", self.max_concurrent_bootstraps),
            ("active-call", self.max_active_calls),
            ("call-assignment", self.max_call_assignments),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Pipecat bootstrap {name} capacity must be positive")
        if self.max_active_calls > self.max_call_assignments:
            raise ValueError("Pipecat active-call capacity cannot exceed assignment capacity")


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


class PipecatSignalingControl(Protocol):
    """The exact process-internal signaling ownership seam."""

    async def reserve(
        self,
        claims: VoiceCallClaims,
    ) -> PipecatVoiceRuntimeAssignment: ...

    async def release_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult: ...

    async def status_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatReservationSnapshot: ...


@dataclass(frozen=True)
class PipecatBootstrapResult:
    """One raw internal assignment coupled to its exact immutable ICE lease.

    Both members are hidden from ``repr`` so neither the opaque signaling bearer
    nor TURN credentials can leak through generic exception or log formatting.
    The authenticated runtime projector is the only intended reveal boundary.
    """

    assignment: PipecatVoiceRuntimeAssignment = field(repr=False)
    ice_lease: PipecatIceLease = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self.ice_lease.claims != self.assignment.claims
            or self.assignment.expires_at != self.assignment.claims.expires_at
            or self.ice_lease.expires_at != self.assignment.expires_at
        ):
            raise ValueError("Pipecat bootstrap result scope or expiry is inconsistent")


@dataclass(frozen=True)
class _BootstrapScope:
    user_id: str
    session_id: str
    agent_id: str
    voice_call_id: str


@dataclass
class _AssignmentRecord:
    scope: _BootstrapScope
    claims: VoiceCallClaims
    reservation_created: bool = False
    assignment: PipecatVoiceRuntimeAssignment | None = field(default=None, repr=False)
    result: PipecatBootstrapResult | None = field(default=None, repr=False)
    provision_task: asyncio.Task[PipecatBootstrapResult] | None = field(
        default=None,
        repr=False,
    )
    release_task: asyncio.Task[VoiceRuntimeTerminalResult] | None = field(
        default=None,
        repr=False,
    )
    release_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(frozen=True)
class _ReleaseTombstone:
    scope: _BootstrapScope
    expires_at: datetime
    terminal_result: VoiceRuntimeTerminalResult | None = field(default=None, repr=False)


@dataclass
class _BootstrapInvocation:
    """Request-local ownership; duplicate waiters never cancel a shared call."""

    owns_provision: bool = False


class PipecatBootstrapService:
    """Authorize, admit, and retry one exact Pipecat assignment."""

    def __init__(
        self,
        settings: PipecatBootstrapSettings,
        *,
        signaling: PipecatSignalingControl,
        ice_lease_issuer: PipecatIceLeaseIssuer,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._signaling = signaling
        self._ice_lease_issuer = ice_lease_issuer
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._call_locks = CallLockRegistry()
        self._state_guard = asyncio.Lock()
        self._bootstrap_admission = asyncio.BoundedSemaphore(settings.max_concurrent_bootstraps)
        self._release_admission = asyncio.BoundedSemaphore(settings.max_concurrent_bootstraps)
        # Provisioning has independent ownership after a request is cancelled.
        # Its capacity is therefore held by the owned task, not the HTTP caller.
        self._provision_admission = asyncio.BoundedSemaphore(settings.max_concurrent_bootstraps)
        self._records: dict[str, _AssignmentRecord] = {}
        self._release_tombstones: dict[str, _ReleaseTombstone] = {}
        self._release_tombstone_overflow_until: datetime | None = None
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._release_handoff_tasks: dict[str, asyncio.Task[None]] = {}
        self._release_handoffs_closed = False

    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatBootstrapResult:
        """Return the exact cached assignment for one authoritative call scope."""

        scope: _BootstrapScope | None = None
        invocation = _BootstrapInvocation()
        try:
            async with self._hold_admission(
                self._bootstrap_admission,
                unavailable_message="Pipecat bootstrap capacity is exhausted",
            ):
                scope = await self._authorize_scope(
                    user_id=user_id,
                    session_id=session_id,
                    voice_call_id=voice_call_id,
                )
                async with self._hold_call_lock(scope.voice_call_id):
                    return await self._bootstrap_locked(scope, invocation=invocation)
        except asyncio.CancelledError:
            if scope is not None and invocation.owns_provision:
                await self._publish_cancelled_bootstrap(scope)
            raise

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult | None:
        """Record negative intent first, then release the exact owned reservation."""

        scope: _BootstrapScope | None = None
        intent_recorded = False
        try:
            async with self._state_guard:
                self._ensure_open_locked()
            async with self._hold_admission(
                self._release_admission,
                unavailable_message="Pipecat release capacity is exhausted",
            ):
                scope = await self._scope_for_release(
                    user_id=user_id,
                    session_id=session_id,
                    voice_call_id=voice_call_id,
                )
                # This mutation is intentionally ahead of the keyed lock.  A
                # bootstrap already inside that lock rechecks it before publishing.
                await self._record_release_intent(scope)
                intent_recorded = True
                async with self._hold_call_lock(scope.voice_call_id):
                    record, tombstone = await self._exact_state(scope)
                    if record is None:
                        return tombstone.terminal_result if tombstone is not None else None
                    provision_task = record.provision_task
                    if record.assignment is None and provision_task is not None:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(provision_task),
                                timeout=self.settings.operation_timeout_seconds,
                            )
                        except asyncio.CancelledError:
                            raise
                        except TimeoutError:
                            # The owned provision task retains the negative intent
                            # and will release immediately if reserve later succeeds.
                            raise PipecatBootstrapUnavailable(
                                "Pipecat release is waiting for provisioning cleanup"
                            ) from None
                        except Exception:
                            # Provisioning observes the tombstone before it can
                            # publish. Inspect the resulting authoritative state.
                            pass
                    retained, tombstone = await self._exact_state(scope)
                    if retained is None:
                        return tombstone.terminal_result if tombstone is not None else None
                    if not retained.reservation_created:
                        return tombstone.terminal_result if tombstone is not None else None
                    return await self._release_record(retained)
        except asyncio.CancelledError:
            if scope is not None and intent_recorded:
                self._schedule_release_handoff(scope)
            raise

    async def _scope_for_release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> _BootstrapScope:
        """Prefer immutable retained ownership so deleted records can still close."""

        async with self._state_guard:
            self._ensure_open_locked()
            self._prune_tombstones_locked(self._aware_now())
            record = self._records.get(voice_call_id)
            tombstone = self._release_tombstones.get(voice_call_id)
            retained_scope = (
                record.scope
                if record is not None
                else (tombstone.scope if tombstone is not None else None)
            )
        if retained_scope is None:
            return await self._authorize_scope(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
        if retained_scope.user_id != user_id or retained_scope.session_id != session_id:
            raise PipecatBootstrapForbidden("Forbidden")
        return retained_scope

    async def _authorize_scope(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> _BootstrapScope:
        session = await self._repository_lookup(self._session_repo.get_by_id, session_id)
        if session is None:
            raise PipecatBootstrapNotFound("Session not found")
        try:
            authoritative_session_id = session.id
            authoritative_user_id = session.user_id
            authoritative_agent_id = session.agent_id
        except Exception as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat authoritative session record is invalid"
            ) from exc
        if not all(
            isinstance(value, str)
            for value in (
                authoritative_session_id,
                authoritative_user_id,
                authoritative_agent_id,
            )
        ):
            raise PipecatBootstrapUnavailable("Pipecat authoritative session record is invalid")
        if authoritative_session_id != session_id or authoritative_user_id != user_id:
            raise PipecatBootstrapForbidden("Forbidden")
        agent = await self._repository_lookup(
            self._agent_repo.get_by_id,
            authoritative_agent_id,
        )
        if agent is None:
            raise PipecatBootstrapNotFound("Agent not found")
        try:
            authoritative_agent_record_id = agent.id
            authoritative_agent_user_id = agent.user_id
        except Exception as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat authoritative agent record is invalid"
            ) from exc
        if not all(
            isinstance(value, str)
            for value in (
                authoritative_agent_record_id,
                authoritative_agent_user_id,
            )
        ):
            raise PipecatBootstrapUnavailable("Pipecat authoritative agent record is invalid")
        if (
            authoritative_agent_record_id != authoritative_agent_id
            or authoritative_agent_user_id != user_id
        ):
            raise PipecatBootstrapForbidden("Forbidden")
        return _BootstrapScope(
            user_id=user_id,
            session_id=authoritative_session_id,
            agent_id=authoritative_agent_record_id,
            voice_call_id=voice_call_id,
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
            raise PipecatBootstrapUnavailable(
                "Pipecat authoritative repository lookup timed out"
            ) from exc
        except BoundedSyncRunnerUnavailable as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat authoritative repository capacity is exhausted"
            ) from exc
        except Exception as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat authoritative repository lookup failed"
            ) from exc

    async def _bootstrap_locked(
        self,
        scope: _BootstrapScope,
        *,
        invocation: _BootstrapInvocation,
    ) -> PipecatBootstrapResult:
        record, tombstone = await self._exact_state(scope)
        if tombstone is not None:
            raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")
        if record is not None:
            if self._claims_expired(record.claims):
                await self._record_release_intent(scope)
                if record.reservation_created:
                    await self._release_record(record)
                raise PipecatBootstrapConflict(
                    "voice_call_id assignment expired; start a fresh call"
                )
            if record.result is not None:
                return await self._validated_cached_result(record)
            if record.provision_task is not None:
                return await self._await_provision(record)

        record = await self._admit_new_record(scope)
        invocation.owns_provision = True
        provision_task = asyncio.create_task(
            self._provision(record),
            name=f"pipecat-bootstrap-provision-{scope.voice_call_id}",
        )
        provision_task.add_done_callback(_consume_task_result)
        record.provision_task = provision_task
        return await self._await_provision(record)

    async def _validated_cached_result(
        self,
        record: _AssignmentRecord,
    ) -> PipecatBootstrapResult:
        result = record.result
        if result is None:  # pragma: no cover - guarded by caller
            raise PipecatBootstrapUnavailable("Pipecat cached assignment is unavailable")
        try:
            snapshot = await asyncio.wait_for(
                self._signaling.status_call(
                    user_id=record.scope.user_id,
                    session_id=record.scope.session_id,
                    voice_call_id=record.scope.voice_call_id,
                ),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except PipecatSignalingNotFound:
            await self._retire_absent_record(record)
            raise PipecatBootstrapConflict(
                "voice_call_id is no longer active; start a fresh call"
            ) from None
        except PipecatSignalingForbidden as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat cached assignment scope is unavailable"
            ) from exc
        except (TimeoutError, PipecatSignalingUnavailable) as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat cached assignment status is unavailable"
            ) from exc
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat cached assignment status failed") from exc
        if (
            not isinstance(snapshot, PipecatReservationSnapshot)
            or snapshot.claims != record.claims
            or snapshot.peer_reservation_id != result.assignment.peer_reservation_id
        ):
            raise PipecatBootstrapUnavailable("Pipecat cached assignment status is inconsistent")
        if snapshot.state is not PipecatReservationState.TERMINAL:
            # Release intent is published before acquiring this call's keyed
            # lock. The remote status may therefore have been sampled just
            # before release terminalized it. Linearize the bearer return with
            # local negative intent so an already-released call can never be
            # handed back to a retrying client.
            async with self._state_guard:
                self._ensure_open_locked()
                current = self._records.get(record.scope.voice_call_id)
                tombstone = self._release_tombstones.get(record.scope.voice_call_id)
                if current is not record or tombstone is not None:
                    raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")
            return result
        terminal_result = snapshot.terminal_result
        if terminal_result is None or terminal_result.claims != record.claims:
            raise PipecatBootstrapUnavailable("Pipecat terminal assignment status is inconsistent")
        await self._record_release_intent(record.scope)
        if not snapshot.cleanup_complete:
            self._schedule_release_handoff(record.scope)
            raise PipecatBootstrapUnavailable("Pipecat terminal assignment cleanup is incomplete")
        await self._finalize_release(record, terminal_result)
        raise PipecatBootstrapConflict("voice_call_id is terminal; start a fresh call")

    async def _retire_absent_record(self, record: _AssignmentRecord) -> None:
        await self._record_release_intent(record.scope)
        async with self._state_guard:
            if self._records.get(record.scope.voice_call_id) is record:
                self._records.pop(record.scope.voice_call_id, None)

    async def _await_provision(
        self,
        record: _AssignmentRecord,
    ) -> PipecatBootstrapResult:
        task = record.provision_task
        if task is None:
            raise PipecatBootstrapUnavailable("Pipecat provisioning owner is unavailable")
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            await self._record_release_intent(record.scope)
            self._schedule_release_if_possible(record)
            raise PipecatBootstrapUnavailable("Pipecat bootstrap provisioning timed out") from exc

    async def _provision(self, record: _AssignmentRecord) -> PipecatBootstrapResult:
        try:
            async with self._hold_admission(
                self._provision_admission,
                unavailable_message="Pipecat provisioning capacity is exhausted",
            ):
                if await self._has_release_intent(record.scope):
                    raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")
                assignment = await self._reserve(record.claims)
                # A successful reserve call owns remote capacity even if its
                # returned value is malformed. Keep that ownership fact before
                # validation so every later failure routes through trusted
                # release rather than merely dropping local state.
                record.reservation_created = True
                self._validate_assignment(record, assignment)
                record.assignment = assignment
                if await self._has_release_intent(record.scope):
                    await self._release_record(record)
                    raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")

                lease = await self._issue_ice_lease(record.claims)
                lease.require_compatible_signaling_base_url(
                    assignment.webrtc_url.get_secret_value()
                )
                result = PipecatBootstrapResult(assignment=assignment, ice_lease=lease)
                if self._claims_expired(record.claims):
                    await self._record_release_intent(record.scope)
                    await self._release_record(record)
                    raise PipecatBootstrapConflict(
                        "voice_call_id assignment expired; start a fresh call"
                    )
                async with self._state_guard:
                    self._ensure_open_locked()
                    self._prune_tombstones_locked(self._aware_now())
                    current = self._records.get(record.scope.voice_call_id)
                    tombstone = self._release_tombstones.get(record.scope.voice_call_id)
                    if current is not record or tombstone is not None:
                        cancelled = True
                    else:
                        record.result = result
                        cancelled = False
                if cancelled:
                    await self._release_record(record)
                    raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")
                return result
        except asyncio.CancelledError:
            await self._record_release_intent(record.scope)
            if record.reservation_created:
                await self._shielded_release(record)
            raise
        except PipecatBootstrapError:
            if record.reservation_created:
                await self._record_release_intent(record.scope)
                await self._release_record(record)
            else:
                await self._discard_unprovisioned_record(record)
            raise
        except Exception as exc:
            if record.reservation_created:
                await self._record_release_intent(record.scope)
                try:
                    await self._release_record(record)
                except PipecatBootstrapError:
                    pass
            else:
                await self._discard_unprovisioned_record(record)
            raise PipecatBootstrapUnavailable("Pipecat provisioning failed") from exc

    async def _reserve(
        self,
        claims: VoiceCallClaims,
    ) -> PipecatVoiceRuntimeAssignment:
        try:
            return await asyncio.wait_for(
                self._signaling.reserve(claims),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise PipecatBootstrapUnavailable("Pipecat reservation timed out") from exc
        except PipecatSignalingNotFound as exc:
            raise PipecatBootstrapNotFound("Pipecat reservation authority was not found") from exc
        except PipecatSignalingForbidden as exc:
            raise PipecatBootstrapForbidden("Forbidden") from exc
        except PipecatSignalingConflict as exc:
            raise PipecatBootstrapConflict("Pipecat reservation conflicts with call state") from exc
        except PipecatSignalingUnavailable as exc:
            raise PipecatBootstrapUnavailable("Pipecat reservation is unavailable") from exc
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat reservation failed") from exc

    async def _issue_ice_lease(self, claims: VoiceCallClaims) -> PipecatIceLease:
        try:
            lease = await asyncio.wait_for(
                self._ice_lease_issuer.issue(claims),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise PipecatBootstrapUnavailable("Pipecat ICE lease timed out") from exc
        except PipecatIceLeaseUnavailable as exc:
            raise PipecatBootstrapUnavailable("Pipecat ICE lease is unavailable") from exc
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat ICE lease failed") from exc
        if (
            not isinstance(lease, PipecatIceLease)
            or lease.claims != claims
            or lease.expires_at != claims.expires_at
            or self._aware_now() >= lease.expires_at
        ):
            raise PipecatBootstrapUnavailable("Pipecat ICE lease scope or expiry is invalid")
        return lease

    @staticmethod
    def _validate_assignment(
        record: _AssignmentRecord,
        assignment: object,
    ) -> None:
        if (
            not isinstance(assignment, PipecatVoiceRuntimeAssignment)
            or assignment.claims != record.claims
            or assignment.expires_at != record.claims.expires_at
            or assignment.runtime is not VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1
        ):
            raise PipecatBootstrapUnavailable("Pipecat signaling returned a different assignment")

    async def _admit_new_record(self, scope: _BootstrapScope) -> _AssignmentRecord:
        async with self._state_guard:
            self._ensure_open_locked()
            now = self._aware_now()
            self._prune_tombstones_locked(now)
            self._raise_overflow_locked(now)
            expired_call_ids = [
                call_id
                for call_id, record in self._records.items()
                if self._claims_expired(record.claims)
            ]
            has_capacity = self._has_new_record_capacity_locked()

        if not has_capacity:
            for expired_call_id in expired_call_ids:
                if await self._try_reclaim_expired(expired_call_id):
                    break

        async with self._state_guard:
            self._ensure_open_locked()
            now = self._aware_now()
            self._prune_tombstones_locked(now)
            self._raise_overflow_locked(now)
            existing = self._records.get(scope.voice_call_id)
            tombstone = self._release_tombstones.get(scope.voice_call_id)
            if existing is not None:
                if existing.scope != scope:
                    raise PipecatBootstrapConflict(
                        "voice_call_id is assigned to another trusted scope"
                    )
                return existing
            if tombstone is not None:
                if tombstone.scope != scope:
                    raise PipecatBootstrapConflict(
                        "voice_call_id was released by another trusted scope"
                    )
                raise PipecatBootstrapConflict("voice_call_id was released; start a fresh call")
            if not self._has_new_record_capacity_locked():
                raise PipecatBootstrapUnavailable("Pipecat call-assignment capacity is exhausted")
            claims = self._new_claims(scope, now)
            record = _AssignmentRecord(scope=scope, claims=claims)
            self._records[scope.voice_call_id] = record
            return record

    def _new_claims(self, scope: _BootstrapScope, issued_at: datetime) -> VoiceCallClaims:
        try:
            return VoiceCallClaims(
                user_id=scope.user_id,
                session_id=scope.session_id,
                agent_id=scope.agent_id,
                voice_call_id=scope.voice_call_id,
                trace_id=str(uuid4()),
                runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
                profile_id=self.settings.profile_id,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=self.settings.assignment_ttl_seconds),
            )
        except ValidationError as exc:
            raise PipecatBootstrapConflict(
                "authoritative call scope is incompatible with Voice V2"
            ) from exc

    async def _try_reclaim_expired(self, voice_call_id: str) -> bool:
        try:
            async with self._hold_call_lock(voice_call_id):
                async with self._state_guard:
                    record = self._records.get(voice_call_id)
                if record is None:
                    return True
                if not self._claims_expired(record.claims):
                    return False
                await self._record_release_intent(record.scope)
                if not record.reservation_created:
                    task = record.provision_task
                    if task is not None and not task.done():
                        return False
                    await self._discard_unprovisioned_record(record)
                    return True
                try:
                    await self._release_record(record)
                except PipecatBootstrapError:
                    return False
                return True
        except PipecatBootstrapUnavailable:
            return False

    async def _record_release_intent(self, scope: _BootstrapScope) -> None:
        now = self._aware_now()
        async with self._state_guard:
            self._prune_tombstones_locked(now)
            record = self._records.get(scope.voice_call_id)
            if record is not None and record.scope != scope:
                raise PipecatBootstrapConflict("voice_call_id is assigned to another trusted scope")
            existing = self._release_tombstones.get(scope.voice_call_id)
            if existing is not None and existing.scope != scope:
                raise PipecatBootstrapConflict(
                    "voice_call_id was released by another trusted scope"
                )
            if self._closed and record is None and existing is None:
                raise PipecatBootstrapUnavailable("Pipecat bootstrap service is closed")
            expires_at = now + timedelta(seconds=PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS)
            if existing is not None:
                self._release_tombstones[scope.voice_call_id] = _ReleaseTombstone(
                    scope=scope,
                    expires_at=max(existing.expires_at, expires_at),
                    terminal_result=existing.terminal_result,
                )
                return
            if (
                record is None
                and self._registry_size_locked() >= self.settings.max_call_assignments
            ):
                self._release_tombstone_overflow_until = max(
                    self._release_tombstone_overflow_until or expires_at,
                    expires_at,
                )
                raise PipecatBootstrapUnavailable("Pipecat release-tombstone capacity is exhausted")
            self._release_tombstones[scope.voice_call_id] = _ReleaseTombstone(
                scope=scope,
                expires_at=expires_at,
            )

    async def _has_release_intent(self, scope: _BootstrapScope) -> bool:
        async with self._state_guard:
            self._prune_tombstones_locked(self._aware_now())
            tombstone = self._release_tombstones.get(scope.voice_call_id)
            if tombstone is None:
                return False
            if tombstone.scope != scope:
                raise PipecatBootstrapConflict(
                    "voice_call_id was released by another trusted scope"
                )
            return True

    async def _exact_state(
        self,
        scope: _BootstrapScope,
    ) -> tuple[_AssignmentRecord | None, _ReleaseTombstone | None]:
        async with self._state_guard:
            self._ensure_open_locked()
            self._prune_tombstones_locked(self._aware_now())
            record = self._records.get(scope.voice_call_id)
            tombstone = self._release_tombstones.get(scope.voice_call_id)
            if record is not None and record.scope != scope:
                raise PipecatBootstrapConflict("voice_call_id is assigned to another trusted scope")
            if tombstone is not None and tombstone.scope != scope:
                raise PipecatBootstrapConflict(
                    "voice_call_id was released by another trusted scope"
                )
            return record, tombstone

    async def _release_record(
        self,
        record: _AssignmentRecord,
    ) -> VoiceRuntimeTerminalResult:
        if not record.reservation_created:
            raise PipecatBootstrapUnavailable(
                "Pipecat trusted release has no confirmed reservation"
            )
        async with record.release_lock:
            task = record.release_task
            if task is None or task.cancelled() or (task.done() and task.exception() is not None):
                task = asyncio.create_task(
                    self._trusted_release_and_finalize(record),
                    name=f"pipecat-bootstrap-release-{record.scope.voice_call_id}",
                )
                task.add_done_callback(_consume_task_result)
                record.release_task = task
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            self._schedule_release_handoff(record.scope)
            raise PipecatBootstrapUnavailable("Pipecat trusted release timed out") from exc
        except PipecatBootstrapError:
            self._schedule_release_handoff(record.scope)
            raise
        except Exception as exc:
            self._schedule_release_handoff(record.scope)
            raise PipecatBootstrapUnavailable("Pipecat trusted release failed") from exc
        return result

    async def _trusted_release_and_finalize(
        self,
        record: _AssignmentRecord,
    ) -> VoiceRuntimeTerminalResult:
        """Own remote release through local finalization beyond caller timeout."""

        try:
            result = await self._trusted_release(record)
        except (PipecatBootstrapNotFound, PipecatBootstrapForbidden) as authority_error:
            # The signaling owner deliberately revokes on authoritative
            # ownership drift, then re-raises the safe authority error. Recover
            # that terminal fact through the immutable internal status seam so
            # local capacity cannot remain wedged after repository deletion.
            result = await self._terminal_result_after_authority_error(record)
            await self._finalize_release(record, result)
            raise authority_error
        snapshot = await self._status_after_release(record, result=result)
        if not snapshot.cleanup_complete:
            self._schedule_release_handoff(record.scope)
            raise PipecatBootstrapUnavailable("Pipecat trusted release cleanup is incomplete")
        await self._finalize_release(record, result)
        return result

    async def _status_after_release(
        self,
        record: _AssignmentRecord,
        *,
        result: VoiceRuntimeTerminalResult,
    ) -> PipecatReservationSnapshot:
        try:
            snapshot = await asyncio.wait_for(
                self._signaling.status_call(
                    user_id=record.scope.user_id,
                    session_id=record.scope.session_id,
                    voice_call_id=record.scope.voice_call_id,
                ),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat release status is unavailable") from exc
        if (
            not isinstance(snapshot, PipecatReservationSnapshot)
            or snapshot.claims != record.claims
            or snapshot.state is not PipecatReservationState.TERMINAL
            or snapshot.terminal_result != result
        ):
            raise PipecatBootstrapUnavailable("Pipecat release status is inconsistent")
        return snapshot

    async def _terminal_result_after_authority_error(
        self,
        record: _AssignmentRecord,
    ) -> VoiceRuntimeTerminalResult:
        try:
            snapshot = await asyncio.wait_for(
                self._signaling.status_call(
                    user_id=record.scope.user_id,
                    session_id=record.scope.session_id,
                    voice_call_id=record.scope.voice_call_id,
                ),
                timeout=self.settings.operation_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PipecatBootstrapUnavailable(
                "Pipecat terminal release status is unavailable"
            ) from exc
        result = (
            snapshot.terminal_result if isinstance(snapshot, PipecatReservationSnapshot) else None
        )
        if (
            snapshot.state is not PipecatReservationState.TERMINAL
            or result is None
            or result.claims != record.claims
            or not snapshot.cleanup_complete
        ):
            raise PipecatBootstrapUnavailable("Pipecat terminal release status is inconsistent")
        return result

    async def _trusted_release(
        self,
        record: _AssignmentRecord,
    ) -> VoiceRuntimeTerminalResult:
        try:
            return await self._signaling.release_call(
                user_id=record.scope.user_id,
                session_id=record.scope.session_id,
                voice_call_id=record.scope.voice_call_id,
            )
        except asyncio.CancelledError:
            raise
        except PipecatSignalingNotFound as exc:
            raise PipecatBootstrapNotFound("Pipecat reservation was not found") from exc
        except PipecatSignalingForbidden as exc:
            raise PipecatBootstrapForbidden("Forbidden") from exc
        except PipecatSignalingConflict as exc:
            raise PipecatBootstrapConflict("Pipecat release conflicts with call state") from exc
        except PipecatSignalingUnavailable as exc:
            raise PipecatBootstrapUnavailable("Pipecat trusted release is unavailable") from exc
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat trusted release failed") from exc

    async def _finalize_release(
        self,
        record: _AssignmentRecord,
        result: VoiceRuntimeTerminalResult,
    ) -> None:
        if result.claims != record.claims:
            raise PipecatBootstrapUnavailable(
                "Pipecat trusted release returned a different call scope"
            )
        async with self._state_guard:
            current = self._records.get(record.scope.voice_call_id)
            if current is not record:
                return
            tombstone = self._release_tombstones.get(record.scope.voice_call_id)
            now = self._aware_now()
            expires_at = max(
                tombstone.expires_at if tombstone is not None else now,
                now + timedelta(seconds=PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS),
            )
            self._records.pop(record.scope.voice_call_id, None)
            self._release_tombstones[record.scope.voice_call_id] = _ReleaseTombstone(
                scope=record.scope,
                expires_at=expires_at,
                terminal_result=result,
            )

    async def _shielded_release(self, record: _AssignmentRecord) -> None:
        task = asyncio.create_task(self._release_record(record))
        task.add_done_callback(_consume_task_result)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except PipecatBootstrapError:
            pass

    def _schedule_release_if_possible(self, record: _AssignmentRecord) -> None:
        if not record.reservation_created:
            return
        self._schedule_release_handoff(record.scope)

    def _schedule_release_handoff(self, scope: _BootstrapScope) -> None:
        """Own release completion after an authenticated caller is cancelled."""

        if self._release_handoffs_closed:
            return
        existing = self._release_handoff_tasks.get(scope.voice_call_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._complete_release_handoff(scope),
            name=f"pipecat-bootstrap-release-handoff-{scope.voice_call_id}",
        )
        self._release_handoff_tasks[scope.voice_call_id] = task
        task.add_done_callback(
            lambda completed, call_id=scope.voice_call_id: self._finish_release_handoff(
                call_id,
                completed,
            )
        )

    def _finish_release_handoff(
        self,
        voice_call_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._release_handoff_tasks.get(voice_call_id) is task:
            self._release_handoff_tasks.pop(voice_call_id, None)
        _consume_task_result(task)

    async def _complete_release_handoff(self, scope: _BootstrapScope) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS
        delay = min(0.05, self.settings.coordination_timeout_seconds)
        while loop.time() < deadline:
            try:
                async with self._hold_call_lock(scope.voice_call_id):
                    async with self._state_guard:
                        record = self._records.get(scope.voice_call_id)
                        tombstone = self._release_tombstones.get(scope.voice_call_id)
                    if record is None or (
                        tombstone is not None and tombstone.terminal_result is not None
                    ):
                        return
                    # A confirmed reservation is independently releasable even
                    # while the owned provision task is still blocked issuing
                    # ICE. Waiting for that task here would let a cancelled
                    # caller strand remote signaling capacity behind an
                    # unrelated credential/lease dependency.
                    if record.reservation_created:
                        await self._release_record(record)
                        return
                    provision_task = record.provision_task
                    if provision_task is not None and not provision_task.done():
                        await asyncio.wait_for(
                            asyncio.shield(provision_task),
                            timeout=self.settings.operation_timeout_seconds,
                        )
                    async with self._state_guard:
                        retained = self._records.get(scope.voice_call_id)
                    if retained is not record:
                        return
                    if provision_task is None or provision_task.done():
                        await self._discard_unprovisioned_record(record)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)

    async def _publish_cancelled_bootstrap(self, scope: _BootstrapScope) -> None:
        intent_task = asyncio.create_task(self._record_release_intent(scope))
        intent_task.add_done_callback(_consume_task_result)
        try:
            await asyncio.shield(intent_task)
        except (asyncio.CancelledError, PipecatBootstrapError):
            return
        async with self._state_guard:
            record = self._records.get(scope.voice_call_id)
        if record is not None:
            self._schedule_release_if_possible(record)

    async def _discard_unprovisioned_record(self, record: _AssignmentRecord) -> None:
        async with self._state_guard:
            current = self._records.get(record.scope.voice_call_id)
            if current is record and not record.reservation_created:
                self._records.pop(record.scope.voice_call_id, None)

    @asynccontextmanager
    async def _hold_admission(
        self,
        semaphore: asyncio.BoundedSemaphore,
        *,
        unavailable_message: str,
    ) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=self.settings.coordination_timeout_seconds,
            )
        except TimeoutError as exc:
            raise PipecatBootstrapUnavailable(unavailable_message) from exc
        try:
            yield
        finally:
            semaphore.release()

    @asynccontextmanager
    async def _hold_call_lock(self, voice_call_id: str) -> AsyncIterator[None]:
        try:
            async with self._call_locks.hold(
                voice_call_id,
                timeout_seconds=self.settings.coordination_timeout_seconds,
            ):
                yield
        except TimeoutError as exc:
            raise PipecatBootstrapUnavailable("Pipecat call coordination timed out") from exc

    def _claims_expired(self, claims: VoiceCallClaims) -> bool:
        return self._aware_now() >= claims.expires_at

    def _has_new_record_capacity_locked(self) -> bool:
        return (
            len(self._records) < self.settings.max_active_calls
            and self._registry_size_locked() < self.settings.max_call_assignments
        )

    def _registry_size_locked(self) -> int:
        return len(self._records.keys() | self._release_tombstones.keys())

    def _raise_overflow_locked(self, now: datetime) -> None:
        if (
            self._release_tombstone_overflow_until is not None
            and now < self._release_tombstone_overflow_until
        ):
            raise PipecatBootstrapUnavailable(
                "Pipecat cancellation state is saturated; retry later"
            )

    def _prune_tombstones_locked(self, now: datetime) -> None:
        expired = [
            call_id
            for call_id, tombstone in self._release_tombstones.items()
            if tombstone.expires_at <= now and call_id not in self._records
        ]
        for call_id in expired:
            self._release_tombstones.pop(call_id, None)
        if (
            self._release_tombstone_overflow_until is not None
            and self._release_tombstone_overflow_until <= now
        ):
            self._release_tombstone_overflow_until = None

    def _aware_now(self) -> datetime:
        try:
            now = self._clock()
            offset = now.utcoffset() if isinstance(now, datetime) else None
        except Exception as exc:
            raise PipecatBootstrapUnavailable("Pipecat bootstrap clock is unavailable") from exc
        if not isinstance(now, datetime) or now.tzinfo is None or offset is None:
            raise PipecatBootstrapUnavailable(
                "Pipecat bootstrap clock must return an aware timestamp"
            )
        return now.astimezone(UTC)

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise PipecatBootstrapUnavailable("Pipecat bootstrap service is closed")

    @property
    def active_lock_count(self) -> int:
        return self._call_locks.active_key_count

    @property
    def active_assignment_count(self) -> int:
        return len(self._records)

    @property
    def release_tombstone_count(self) -> int:
        return len(self._release_tombstones)

    async def aclose(self) -> None:
        """Stop admission and hand every reservation to an owned close task."""

        async with self._close_lock:
            task = self._close_task
            if task is None or task.cancelled() or (task.done() and task.exception() is not None):
                task = asyncio.create_task(
                    self._close_owned(),
                    name="pipecat-bootstrap-close",
                )
                task.add_done_callback(_consume_task_result)
                self._close_task = task
        await asyncio.shield(task)

    async def _close_owned(self) -> None:
        """Finish bootstrap-to-release handoffs beyond caller cancellation."""

        async with self._state_guard:
            records = tuple(self._records.values())
            now = self._aware_now()
            for record in records:
                self._release_tombstones[record.scope.voice_call_id] = _ReleaseTombstone(
                    scope=record.scope,
                    expires_at=now + timedelta(seconds=PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS),
                )
            self._closed = True
        # A reserve already admitted before close remains owned by its
        # provisioning task. The tombstones above force those tasks through
        # trusted release as soon as reserve returns. Wait for that bounded
        # handoff before inspecting residual records.
        provision_tasks = [
            record.provision_task
            for record in records
            if record.provision_task is not None and not record.provision_task.done()
        ]
        if provision_tasks:
            await asyncio.gather(*provision_tasks, return_exceptions=True)
        async with self._state_guard:
            retained_records = tuple(self._records.values())
        for record in retained_records:
            if not record.reservation_created and (
                record.provision_task is None or record.provision_task.done()
            ):
                await self._discard_unprovisioned_record(record)
        async with self._state_guard:
            retained_records = tuple(self._records.values())
        release_tasks = [
            asyncio.create_task(self._release_record(record))
            for record in retained_records
            if record.reservation_created
        ]
        if release_tasks:
            await asyncio.gather(*release_tasks, return_exceptions=True)
        async with self._state_guard:
            cleanup_incomplete = bool(self._records)
        if cleanup_incomplete:
            raise PipecatBootstrapUnavailable("Pipecat bootstrap close cleanup is incomplete")
        # Cleanup is now authoritative and no record remains for a handoff to
        # reconcile. Close the synchronous task-admission latch before the
        # next await, then cancel and join every owned sleeper so successful
        # shutdown never returns with a background retry still alive.
        self._release_handoffs_closed = True
        handoff_tasks = tuple(self._release_handoff_tasks.values())
        for handoff_task in handoff_tasks:
            handoff_task.cancel()
        if handoff_tasks:
            await asyncio.gather(*handoff_tasks, return_exceptions=True)


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


__all__ = [
    "PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS",
    "AgentRepository",
    "PipecatBootstrapConflict",
    "PipecatBootstrapError",
    "PipecatBootstrapForbidden",
    "PipecatBootstrapNotFound",
    "PipecatBootstrapResult",
    "PipecatBootstrapService",
    "PipecatBootstrapSettings",
    "PipecatBootstrapUnavailable",
    "PipecatSignalingControl",
    "SessionRepository",
]

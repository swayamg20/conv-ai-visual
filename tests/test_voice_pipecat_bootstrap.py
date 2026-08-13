"""Adversarial tests for process-local Pipecat bootstrap ownership."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from murmur.voice.blocking import BoundedSyncRunner
from murmur.voice.pipecat_bootstrap import (
    PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS,
    PipecatBootstrapConflict,
    PipecatBootstrapForbidden,
    PipecatBootstrapNotFound,
    PipecatBootstrapService,
    PipecatBootstrapSettings,
    PipecatBootstrapUnavailable,
)
from murmur.voice.pipecat_ice import (
    PipecatIceLease,
    PipecatIceLeaseUnavailable,
    PipecatIceServer,
)
from murmur.voice.pipecat_signaling import (
    PipecatReservationSnapshot,
    PipecatReservationState,
    PipecatSignalingConflict,
    PipecatSignalingForbidden,
    PipecatSignalingUnavailable,
)
from murmur.voice.runtime_contracts import (
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeTerminalReason,
    VoiceRuntimeTerminalResult,
)
from pydantic import SecretStr

FIXED_NOW = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)
SESSION_1 = "11111111-1111-4111-8111-111111111111"
SESSION_2 = "22222222-2222-4222-8222-222222222222"
AGENT_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
AGENT_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CALL_1 = "10000000-0000-4000-8000-000000000001"
CALL_2 = "10000000-0000-4000-8000-000000000002"
CALL_3 = "10000000-0000-4000-8000-000000000003"


@dataclass(frozen=True)
class _Session:
    id: str
    user_id: str
    agent_id: str


@dataclass(frozen=True)
class _Agent:
    id: str
    user_id: str


class _Lookup:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get_by_id(self, key: str) -> Any | None:
        return self.values.get(key)


class _FailingLookup:
    def get_by_id(self, key: str) -> None:
        del key
        raise RuntimeError("repository unavailable")


class _BlockingLookup(_Lookup):
    def __init__(self, values: dict[str, Any]) -> None:
        super().__init__(values)
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_by_id(self, key: str) -> Any | None:
        self.entered.set()
        self.release.wait()
        return super().get_by_id(key)


class _FakeSignaling:
    def __init__(self) -> None:
        self.reserve_calls: list[VoiceCallClaims] = []
        self.reserve_leases: list[PipecatIceLease] = []
        self.release_calls: list[tuple[str, str, str]] = []
        self.reserve_entered = asyncio.Event()
        self.reserve_gate: asyncio.Event | None = None
        self.reserve_error: Exception | None = None
        self.release_error: Exception | None = None
        self.release_entered = asyncio.Event()
        self.release_gate: asyncio.Event | None = None
        self._assignments: dict[str, PipecatVoiceRuntimeAssignment] = {}
        self._terminal: dict[str, VoiceRuntimeTerminalResult] = {}
        self.status_calls: list[tuple[str, str, str]] = []
        self.status_entered = asyncio.Event()
        self.status_gate: asyncio.Event | None = None
        self.status_error: Exception | None = None
        self.cleanup_complete = True

    async def reserve(
        self,
        claims: VoiceCallClaims,
        ice_lease: PipecatIceLease,
    ) -> PipecatVoiceRuntimeAssignment:
        self.reserve_calls.append(claims)
        self.reserve_leases.append(ice_lease)
        self.reserve_entered.set()
        if self.reserve_gate is not None:
            await self.reserve_gate.wait()
        if self.reserve_error is not None:
            raise self.reserve_error
        assignment = PipecatVoiceRuntimeAssignment(
            claims=claims,
            webrtc_url=(
                "http://127.0.0.1:9000/api/voice/pipecat/signal/"
                f"opaque-token-{len(self.reserve_calls):04d}-"
                "abcdefghijklmnopqrstuvwxyz0123456789"
            ),
            peer_reservation_id=f"reservation-{len(self.reserve_calls)}",
            expires_at=claims.expires_at,
        )
        self._assignments[claims.voice_call_id] = assignment
        return assignment

    async def release_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult:
        self.release_calls.append((user_id, session_id, voice_call_id))
        self.release_entered.set()
        if self.release_gate is not None:
            await self.release_gate.wait()
        if self.release_error is not None:
            raise self.release_error
        existing = self._terminal.get(voice_call_id)
        if existing is not None:
            return existing
        assignment = self._assignments.get(voice_call_id)
        if assignment is None:
            raise PipecatSignalingConflict("reservation is absent")
        claims = assignment.claims
        if (
            claims.user_id != user_id
            or claims.session_id != session_id
            or claims.voice_call_id != voice_call_id
        ):
            raise PipecatSignalingConflict("scope mismatch")
        result = VoiceRuntimeTerminalResult(
            claims=claims,
            reason=VoiceRuntimeTerminalReason.USER_ENDED,
            retryable=False,
            terminated_at=max(FIXED_NOW, claims.issued_at),
        )
        self._terminal[voice_call_id] = result
        return result

    async def status_call(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatReservationSnapshot:
        self.status_calls.append((user_id, session_id, voice_call_id))
        if self.status_error is not None:
            error, self.status_error = self.status_error, None
            raise error
        assignment = self._assignments.get(voice_call_id)
        if assignment is None:
            from murmur.voice.pipecat_signaling import PipecatSignalingNotFound

            raise PipecatSignalingNotFound("reservation not found")
        if assignment.claims.user_id != user_id or assignment.claims.session_id != session_id:
            raise PipecatSignalingForbidden("Forbidden")
        terminal = self._terminal.get(voice_call_id)
        self.status_entered.set()
        if self.status_gate is not None:
            await self.status_gate.wait()
        return PipecatReservationSnapshot(
            peer_reservation_id=assignment.peer_reservation_id,
            claims=assignment.claims,
            state=(
                PipecatReservationState.TERMINAL
                if terminal is not None
                else PipecatReservationState.RESERVED
            ),
            pc_id=None,
            terminal_result=terminal,
            cleanup_complete=self.cleanup_complete,
        )


class _MalformedAssignmentSignaling(_FakeSignaling):
    async def reserve(self, claims: VoiceCallClaims, ice_lease: PipecatIceLease) -> Any:
        await super().reserve(claims, ice_lease)
        return object()


class _FakeIceIssuer:
    def __init__(self) -> None:
        self.calls: list[VoiceCallClaims] = []
        self.entered = asyncio.Event()
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None
        self.secret = "turn-secret-never-log"

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
        self.calls.append(claims)
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return PipecatIceLease(
            claims=claims,
            provider_id="test-ice",
            expires_at=claims.expires_at,
            ice_servers=(
                PipecatIceServer(urls=("stun:stun.example.test:3478",)),
                PipecatIceServer(
                    urls=("turns:turn.example.test:5349?transport=tcp",),
                    username=SecretStr("turn-user-never-log"),
                    credential=SecretStr(self.secret),
                ),
            ),
        )


class _MalformedIceIssuer(_FakeIceIssuer):
    async def issue(self, claims: VoiceCallClaims) -> Any:
        self.calls.append(claims)
        return object()


def _settings(**overrides: Any) -> PipecatBootstrapSettings:
    values: dict[str, Any] = {
        "profile_id": "pipecat-direct-cascade-v1",
        "assignment_ttl_seconds": 300,
        "operation_timeout_seconds": 1.0,
        "coordination_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return PipecatBootstrapSettings(**values)


def _service(
    *,
    signaling: _FakeSignaling | None = None,
    ice: _FakeIceIssuer | None = None,
    sessions: object | None = None,
    agents: object | None = None,
    settings: PipecatBootstrapSettings | None = None,
    clock: Any = None,
    repository_runner: BoundedSyncRunner | None = None,
) -> tuple[PipecatBootstrapService, _FakeSignaling, _FakeIceIssuer]:
    signaling = signaling or _FakeSignaling()
    ice = ice or _FakeIceIssuer()
    if sessions is None:
        sessions = _Lookup(
            {
                SESSION_1: _Session(SESSION_1, "user-1", AGENT_1),
                SESSION_2: _Session(SESSION_2, "user-2", AGENT_2),
            }
        )
    if agents is None:
        agents = _Lookup(
            {
                AGENT_1: _Agent(AGENT_1, "user-1"),
                AGENT_2: _Agent(AGENT_2, "user-2"),
            }
        )
    kwargs: dict[str, Any] = {}
    if repository_runner is not None:
        kwargs["repository_runner"] = repository_runner
    return (
        PipecatBootstrapService(
            settings or _settings(),
            signaling=signaling,
            ice_lease_issuer=ice,
            session_repo=sessions,
            agent_repo=agents,
            clock=clock or (lambda: FIXED_NOW),
            **kwargs,
        ),
        signaling,
        ice,
    )


async def _bootstrap(
    service: PipecatBootstrapService,
    *,
    user_id: str = "user-1",
    session_id: str = SESSION_1,
    voice_call_id: str = CALL_1,
):
    return await service.bootstrap(
        user_id=user_id,
        session_id=session_id,
        voice_call_id=voice_call_id,
    )


async def _release(
    service: PipecatBootstrapService,
    *,
    user_id: str = "user-1",
    session_id: str = SESSION_1,
    voice_call_id: str = CALL_1,
):
    return await service.release(
        user_id=user_id,
        session_id=session_id,
        voice_call_id=voice_call_id,
    )


@pytest.mark.asyncio
async def test_duplicate_bootstrap_returns_same_secret_assignment_and_lease() -> None:
    service, signaling, ice = _service()

    first = await _bootstrap(service)
    second = await _bootstrap(service)

    assert second is first
    assert second.assignment is first.assignment
    assert second.ice_lease is first.ice_lease
    assert len(signaling.reserve_calls) == 1
    assert len(ice.calls) == 1
    assert signaling.reserve_leases == [first.ice_lease]
    assert signaling.reserve_leases[0] is first.ice_lease
    rendered = repr(first)
    assert first.assignment.webrtc_url.get_secret_value() not in rendered
    assert ice.secret not in rendered


@pytest.mark.asyncio
async def test_concurrent_duplicate_bootstraps_linearize_to_one_reserve() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(signaling=signaling)

    first_task = asyncio.create_task(_bootstrap(service))
    await signaling.reserve_entered.wait()
    second_task = asyncio.create_task(_bootstrap(service))
    await asyncio.sleep(0)
    signaling.reserve_gate.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first is second
    assert len(signaling.reserve_calls) == 1
    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_cancelling_duplicate_waiter_never_revokes_shared_provision() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(signaling=signaling)

    first = asyncio.create_task(_bootstrap(service))
    await signaling.reserve_entered.wait()
    duplicate = asyncio.create_task(_bootstrap(service))
    await asyncio.sleep(0)
    duplicate.cancel()
    with pytest.raises(asyncio.CancelledError):
        await duplicate

    assert service.release_tombstone_count == 0
    signaling.reserve_gate.set()
    result = await first
    assert result.assignment.claims.voice_call_id == CALL_1
    assert len(signaling.reserve_calls) == 1
    assert signaling.release_calls == []
    assert service.active_assignment_count == 1


@pytest.mark.asyncio
async def test_cached_retry_rejects_out_of_band_terminal_reservation() -> None:
    service, signaling, _ = _service()
    await _bootstrap(service)
    await signaling.release_call(
        user_id="user-1",
        session_id=SESSION_1,
        voice_call_id=CALL_1,
    )

    with pytest.raises(PipecatBootstrapConflict, match=r"terminal.*fresh call"):
        await _bootstrap(service)

    assert signaling.status_calls == [("user-1", SESSION_1, CALL_1)]


@pytest.mark.asyncio
async def test_cached_terminal_cleanup_reconciles_without_caller_retry() -> None:
    service, signaling, _ = _service()
    await _bootstrap(service)
    await signaling.release_call(
        user_id="user-1",
        session_id=SESSION_1,
        voice_call_id=CALL_1,
    )
    signaling.cleanup_complete = False

    with pytest.raises(PipecatBootstrapUnavailable, match="cleanup is incomplete"):
        await _bootstrap(service)
    assert service.active_assignment_count == 1

    signaling.cleanup_complete = True
    for _ in range(200):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0.01)

    assert service.active_assignment_count == 0
    fresh = await _bootstrap(service, voice_call_id=CALL_2)
    assert fresh.assignment.claims.voice_call_id == CALL_2


@pytest.mark.asyncio
async def test_cached_retry_cannot_return_after_concurrent_release_intent() -> None:
    service, signaling, _ = _service()
    first = await _bootstrap(service)
    signaling.status_gate = asyncio.Event()

    retry = asyncio.create_task(_bootstrap(service))
    await signaling.status_entered.wait()
    release = asyncio.create_task(_release(service))
    for _ in range(100):
        if service.release_tombstone_count == 1:
            break
        await asyncio.sleep(0)
    assert service.release_tombstone_count == 1
    assert not release.done()

    signaling.status_gate.set()
    with pytest.raises(PipecatBootstrapConflict, match="fresh call"):
        await retry
    assert (await release).reason is VoiceRuntimeTerminalReason.USER_ENDED
    assert first.assignment.claims.voice_call_id == CALL_1
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1


@pytest.mark.asyncio
async def test_same_call_id_cannot_cross_authoritative_scope() -> None:
    service, signaling, _ = _service(settings=_settings(max_active_calls=2))
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapConflict, match="another trusted scope"):
        await _bootstrap(
            service,
            user_id="user-2",
            session_id=SESSION_2,
            voice_call_id=CALL_1,
        )

    assert len(signaling.reserve_calls) == 1


@pytest.mark.asyncio
async def test_release_before_bootstrap_tombstones_without_remote_reserve() -> None:
    service, signaling, _ = _service()

    assert await _release(service) is None
    with pytest.raises(PipecatBootstrapConflict, match="start a fresh call"):
        await _bootstrap(service)

    assert not signaling.reserve_calls
    assert not signaling.release_calls
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1


@pytest.mark.asyncio
async def test_release_racing_reserve_wins_before_assignment_publish() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(signaling=signaling)
    bootstrap_task = asyncio.create_task(_bootstrap(service))
    await signaling.reserve_entered.wait()

    release_task = asyncio.create_task(_release(service))
    for _ in range(100):
        if service.release_tombstone_count == 1:
            break
        await asyncio.sleep(0)
    assert service.release_tombstone_count == 1
    signaling.reserve_gate.set()

    with pytest.raises(PipecatBootstrapConflict, match="start a fresh call"):
        await bootstrap_task
    terminal = await release_task
    assert terminal is not None
    assert terminal.reason is VoiceRuntimeTerminalReason.USER_ENDED
    assert len(signaling.reserve_calls) == 1
    assert len(signaling.release_calls) == 1
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_active_release_is_idempotent_and_keeps_terminal_result() -> None:
    service, signaling, _ = _service()
    await _bootstrap(service)

    first = await _release(service)
    second = await _release(service)

    assert first is second
    assert len(signaling.release_calls) == 1
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1
    with pytest.raises(PipecatBootstrapConflict, match="fresh call"):
        await _bootstrap(service)


@pytest.mark.asyncio
async def test_expired_assignment_is_released_and_requires_fresh_call_id() -> None:
    now = [FIXED_NOW]
    service, signaling, _ = _service(
        clock=lambda: now[0],
        settings=_settings(assignment_ttl_seconds=30),
    )
    await _bootstrap(service)
    now[0] += timedelta(seconds=31)

    with pytest.raises(PipecatBootstrapConflict, match="expired"):
        await _bootstrap(service)
    assert len(signaling.release_calls) == 1

    fresh = await _bootstrap(service, voice_call_id=CALL_2)
    assert fresh.assignment.claims.voice_call_id == CALL_2


@pytest.mark.asyncio
async def test_active_and_registry_capacity_fail_closed_then_recover_on_release() -> None:
    service, signaling, _ = _service(settings=_settings(max_active_calls=1, max_call_assignments=2))
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapUnavailable, match="capacity"):
        await _bootstrap(service, voice_call_id=CALL_2)
    assert len(signaling.reserve_calls) == 1

    await _release(service)
    result = await _bootstrap(service, voice_call_id=CALL_2)
    assert result.assignment.claims.voice_call_id == CALL_2


@pytest.mark.asyncio
async def test_release_tombstone_overflow_blocks_new_calls_until_horizon() -> None:
    now = [FIXED_NOW]
    service, signaling, _ = _service(
        settings=_settings(max_active_calls=1, max_call_assignments=1),
        clock=lambda: now[0],
    )
    await _release(service, voice_call_id=CALL_1)
    with pytest.raises(PipecatBootstrapUnavailable, match="tombstone capacity"):
        await _release(service, voice_call_id=CALL_2)
    with pytest.raises(PipecatBootstrapUnavailable, match="saturated"):
        await _bootstrap(service, voice_call_id=CALL_3)
    assert not signaling.reserve_calls

    now[0] += timedelta(seconds=PIPECAT_RELEASE_TOMBSTONE_TTL_SECONDS + 1)
    result = await _bootstrap(service, voice_call_id=CALL_3)
    assert result.assignment.claims.voice_call_id == CALL_3


@pytest.mark.asyncio
async def test_repository_absence_ownership_and_failure_map_safely() -> None:
    missing_service, signaling, _ = _service(sessions=_Lookup({}))
    with pytest.raises(PipecatBootstrapNotFound, match="Session"):
        await _bootstrap(missing_service)

    forbidden_service, _, _ = _service(
        sessions=_Lookup({SESSION_1: _Session(SESSION_1, "other-user", AGENT_1)})
    )
    with pytest.raises(PipecatBootstrapForbidden, match="Forbidden"):
        await _bootstrap(forbidden_service)

    mismatched_id_service, _, _ = _service(
        sessions=_Lookup({SESSION_1: _Session(SESSION_2, "user-1", AGENT_1)})
    )
    with pytest.raises(PipecatBootstrapForbidden, match="Forbidden"):
        await _bootstrap(mismatched_id_service)

    failing_service, _, _ = _service(sessions=_FailingLookup())
    with pytest.raises(PipecatBootstrapUnavailable, match="lookup failed"):
        await _bootstrap(failing_service)
    assert not signaling.reserve_calls


@pytest.mark.asyncio
async def test_malformed_repository_record_and_clock_fail_safely() -> None:
    malformed_service, signaling, _ = _service(sessions=_Lookup({SESSION_1: object()}))
    with pytest.raises(PipecatBootstrapUnavailable, match="session record is invalid"):
        await _bootstrap(malformed_service)

    clock_service, _, _ = _service(clock=lambda: object())
    with pytest.raises(PipecatBootstrapUnavailable, match="aware timestamp"):
        await _bootstrap(clock_service)
    assert not signaling.reserve_calls


@pytest.mark.asyncio
async def test_repository_timeout_retains_bounded_runner_admission() -> None:
    lookup = _BlockingLookup({SESSION_1: _Session(SESSION_1, "user-1", AGENT_1)})
    runner = BoundedSyncRunner(max_workers=1, thread_name_prefix="pipecat-bootstrap-test")
    service, signaling, _ = _service(
        sessions=lookup,
        settings=_settings(repository_timeout_seconds=0.01),
        repository_runner=runner,
    )
    try:
        with pytest.raises(PipecatBootstrapUnavailable, match="timed out"):
            await _bootstrap(service)
        assert runner.inflight_count == 1
        with pytest.raises(PipecatBootstrapUnavailable, match="capacity"):
            await _bootstrap(service)
        assert not signaling.reserve_calls
    finally:
        lookup.release.set()
        for _ in range(100):
            if runner.inflight_count == 0:
                break
            await asyncio.sleep(0)
        await runner.aclose()


@pytest.mark.asyncio
async def test_cancellation_during_repository_read_never_calls_remote_reserve() -> None:
    lookup = _BlockingLookup({SESSION_1: _Session(SESSION_1, "user-1", AGENT_1)})
    runner = BoundedSyncRunner(max_workers=1, thread_name_prefix="pipecat-cancel-test")
    service, signaling, _ = _service(
        sessions=lookup,
        repository_runner=runner,
    )
    task = asyncio.create_task(_bootstrap(service))
    try:
        observed = await asyncio.to_thread(lookup.entered.wait, 1.0)
        assert observed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not signaling.reserve_calls
        assert service.active_assignment_count == 0
    finally:
        lookup.release.set()
        for _ in range(100):
            if runner.inflight_count == 0:
                break
            await asyncio.sleep(0)
        await runner.aclose()


@pytest.mark.asyncio
async def test_cancellation_during_ice_issue_never_creates_a_reservation() -> None:
    ice = _FakeIceIssuer()
    ice.gate = asyncio.Event()
    service, signaling, _ = _service(ice=ice)
    task = asyncio.create_task(_bootstrap(service))
    await ice.entered.wait()
    assert signaling.reserve_calls == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert signaling.release_calls == []

    ice.gate.set()
    for _ in range(100):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0)
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1
    with pytest.raises(PipecatBootstrapConflict, match="fresh call"):
        await _bootstrap(service)


@pytest.mark.asyncio
async def test_cancelled_queued_provision_never_calls_remote_reserve() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(
        signaling=signaling,
        settings=_settings(
            max_concurrent_bootstraps=1,
            max_active_calls=2,
            max_call_assignments=4,
            operation_timeout_seconds=5.0,
        ),
    )
    first = asyncio.create_task(_bootstrap(service, voice_call_id=CALL_1))
    await signaling.reserve_entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(_bootstrap(service, voice_call_id=CALL_2))
    for _ in range(100):
        if service.active_assignment_count == 2:
            break
        await asyncio.sleep(0)
    assert service.active_assignment_count == 2
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second

    signaling.reserve_gate.set()
    for _ in range(100):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0)
    assert [claims.voice_call_id for claims in signaling.reserve_calls] == [CALL_1]
    assert signaling.release_calls == [("user-1", SESSION_1, CALL_1)]
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_ice_failure_never_creates_or_caches_a_reservation() -> None:
    ice = _FakeIceIssuer()
    ice.error = PipecatIceLeaseUnavailable("turn unavailable")
    service, signaling, _ = _service(ice=ice)

    with pytest.raises(PipecatBootstrapUnavailable, match="ICE lease"):
        await _bootstrap(service)

    assert signaling.reserve_calls == []
    assert signaling.release_calls == []
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 0


@pytest.mark.asyncio
async def test_invalid_ice_lease_is_rejected_before_signaling_reservation() -> None:
    ice = _MalformedIceIssuer()
    service, signaling, _ = _service(ice=ice)

    with pytest.raises(PipecatBootstrapUnavailable, match="scope or expiry"):
        await _bootstrap(service)

    assert len(ice.calls) == 1
    assert signaling.reserve_calls == []
    assert signaling.release_calls == []
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_malformed_assignment_is_still_trusted_released() -> None:
    signaling = _MalformedAssignmentSignaling()
    service, _, ice = _service(signaling=signaling)

    with pytest.raises(PipecatBootstrapUnavailable, match="different assignment"):
        await _bootstrap(service)

    assert len(signaling.reserve_calls) == 1
    assert len(signaling.release_calls) == 1
    assert len(ice.calls) == 1
    assert signaling.reserve_leases[0].claims == signaling.reserve_calls[0]
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1


@pytest.mark.asyncio
async def test_reserve_failure_does_not_leak_local_capacity() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_error = PipecatSignalingUnavailable("down")
    service, _, _ = _service(signaling=signaling)

    with pytest.raises(PipecatBootstrapUnavailable, match="reservation"):
        await _bootstrap(service)

    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 0


@pytest.mark.asyncio
async def test_uncertain_release_keeps_assignment_capacity_owned() -> None:
    signaling = _FakeSignaling()
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)
    signaling.release_error = PipecatSignalingUnavailable("cleanup uncertain")

    with pytest.raises(PipecatBootstrapUnavailable, match="release"):
        await _release(service)
    assert service.active_assignment_count == 1
    assert service.release_tombstone_count == 1
    with pytest.raises(PipecatBootstrapUnavailable, match="capacity"):
        await _bootstrap(service, voice_call_id=CALL_2)


@pytest.mark.asyncio
async def test_terminal_result_retains_capacity_until_cleanup_is_confirmed() -> None:
    signaling = _FakeSignaling()
    signaling.cleanup_complete = False
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapUnavailable, match="cleanup is incomplete"):
        await _release(service)
    assert service.active_assignment_count == 1
    assert service.release_tombstone_count == 1

    signaling.cleanup_complete = True
    result = await _release(service)
    assert result is not None
    assert result.reason is VoiceRuntimeTerminalReason.USER_ENDED
    assert service.active_assignment_count == 0
    assert len(signaling.release_calls) == 2


@pytest.mark.asyncio
async def test_terminal_cleanup_completion_reconciles_without_caller_retry() -> None:
    signaling = _FakeSignaling()
    signaling.cleanup_complete = False
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapUnavailable, match="cleanup is incomplete"):
        await _release(service)
    assert service.active_assignment_count == 1
    handoff = service._release_handoff_tasks.get(CALL_1)
    assert handoff is not None and not handoff.done()

    signaling.cleanup_complete = True
    for _ in range(200):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0.01)

    assert service.active_assignment_count == 0
    fresh = await _bootstrap(service, voice_call_id=CALL_2)
    assert fresh.assignment.claims.voice_call_id == CALL_2
    assert len(signaling.release_calls) >= 2


@pytest.mark.asyncio
async def test_terminal_status_failure_reconciles_without_caller_retry() -> None:
    signaling = _FakeSignaling()
    signaling.status_error = PipecatSignalingUnavailable("transient status failure")
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapUnavailable, match="status is unavailable"):
        await _release(service)
    assert service.active_assignment_count == 1

    for _ in range(200):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0.01)

    assert service.active_assignment_count == 0
    assert len(signaling.status_calls) >= 2
    fresh = await _bootstrap(service, voice_call_id=CALL_2)
    assert fresh.assignment.claims.voice_call_id == CALL_2


@pytest.mark.asyncio
async def test_timed_out_release_task_finalizes_later_without_caller_retry() -> None:
    signaling = _FakeSignaling()
    signaling.release_gate = asyncio.Event()
    service, _, _ = _service(
        signaling=signaling,
        settings=_settings(operation_timeout_seconds=0.01),
    )
    await _bootstrap(service)

    with pytest.raises(PipecatBootstrapUnavailable, match="release timed out"):
        await _release(service)
    assert service.active_assignment_count == 1

    signaling.release_gate.set()
    for _ in range(100):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0)
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1
    assert len(signaling.release_calls) == 1


@pytest.mark.asyncio
async def test_cancelled_release_waiting_for_call_lock_hands_cleanup_to_owner() -> None:
    service, signaling, _ = _service()
    await _bootstrap(service)
    entered = asyncio.Event()
    unlock = asyncio.Event()

    async def hold_lock() -> None:
        async with service._hold_call_lock(CALL_1):
            entered.set()
            await unlock.wait()

    holder = asyncio.create_task(hold_lock())
    await entered.wait()
    release_task = asyncio.create_task(_release(service))
    for _ in range(100):
        if service.release_tombstone_count == 1:
            break
        await asyncio.sleep(0)
    release_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await release_task
    unlock.set()
    await holder

    for _ in range(200):
        if service.active_assignment_count == 0:
            break
        await asyncio.sleep(0.01)
    assert signaling.release_calls == [("user-1", SESSION_1, CALL_1)]
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_successful_close_cancels_and_joins_sleeping_release_handoff() -> None:
    signaling = _FakeSignaling()
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)
    signaling.release_error = PipecatSignalingUnavailable("transient cleanup failure")

    release_task = asyncio.create_task(_release(service))
    await signaling.release_entered.wait()
    release_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await release_task
    for _ in range(100):
        handoff = service._release_handoff_tasks.get(CALL_1)
        if handoff is not None and not handoff.done():
            break
        await asyncio.sleep(0)
    assert handoff is not None and not handoff.done()

    signaling.release_error = None
    await service.aclose()

    assert service.active_assignment_count == 0
    assert service._release_handoff_tasks == {}
    assert not any(
        task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith("pipecat-bootstrap-release-handoff-")
        for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_release_after_authoritative_session_deletion_still_cleans_exact_call() -> None:
    sessions = _Lookup({SESSION_1: _Session(SESSION_1, "user-1", AGENT_1)})
    service, signaling, _ = _service(sessions=sessions)
    await _bootstrap(service)
    sessions.values.clear()

    result = await _release(service)

    assert result is not None
    assert signaling.release_calls == [("user-1", SESSION_1, CALL_1)]
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_close_waits_for_inflight_reserve_and_leaves_no_orphan() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(signaling=signaling)
    bootstrap_task = asyncio.create_task(_bootstrap(service))
    await signaling.reserve_entered.wait()

    close_task = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()
    signaling.reserve_gate.set()
    await close_task

    with pytest.raises(PipecatBootstrapConflict, match="fresh call"):
        await bootstrap_task
    assert signaling.release_calls == [("user-1", SESSION_1, CALL_1)]
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1
    with pytest.raises(PipecatBootstrapUnavailable, match="closed"):
        await _bootstrap(service, voice_call_id=CALL_2)


@pytest.mark.asyncio
async def test_cancelled_close_continues_owned_release_and_is_reawaitable() -> None:
    signaling = _FakeSignaling()
    signaling.reserve_gate = asyncio.Event()
    service, _, _ = _service(signaling=signaling)
    bootstrap_task = asyncio.create_task(_bootstrap(service))
    await signaling.reserve_entered.wait()

    close_caller = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    close_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_caller

    signaling.reserve_gate.set()
    await service.aclose()
    with pytest.raises(PipecatBootstrapConflict, match="fresh call"):
        await bootstrap_task
    assert signaling.release_calls == [("user-1", SESSION_1, CALL_1)]
    assert service.active_assignment_count == 0


@pytest.mark.asyncio
async def test_close_reports_incomplete_cleanup_and_retries_on_next_call() -> None:
    signaling = _FakeSignaling()
    service, _, _ = _service(signaling=signaling)
    await _bootstrap(service)
    signaling.release_error = PipecatSignalingUnavailable("cleanup uncertain")

    with pytest.raises(PipecatBootstrapUnavailable, match="cleanup is incomplete"):
        await service.aclose()
    assert service.active_assignment_count == 1

    signaling.release_error = None
    await service.aclose()
    assert len(signaling.release_calls) >= 2
    assert service.active_assignment_count == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"assignment_ttl_seconds": 29}, "TTL"),
        ({"assignment_ttl_seconds": 901}, "TTL"),
        ({"operation_timeout_seconds": float("inf")}, "operation timeout"),
        ({"max_active_calls": 2, "max_call_assignments": 1}, "cannot exceed"),
    ],
)
def test_settings_reject_unsafe_policy(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**overrides)

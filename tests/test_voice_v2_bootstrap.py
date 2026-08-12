"""Provider-free tests for the Voice V2 bootstrap trust boundary."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from murmur.api.application import create_application
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError
from murmur.persistence.repositories.identities import AgentRepo, UserRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import BoundedSyncRunner
from murmur.voice.bootstrap import (
    RELEASE_TOMBSTONE_TTL_SECONDS,
    VOICE_V2_EVENT_TOPIC,
    CreateDispatchSpec,
    CreateRoomSpec,
    DispatchRecord,
    ParticipantTokenSpec,
    RoomRecord,
    VoiceBootstrapConflict,
    VoiceBootstrapForbidden,
    VoiceBootstrapNotFound,
    VoiceBootstrapService,
    VoiceBootstrapSettings,
    VoiceBootstrapUnavailable,
    VoiceScope,
    derive_room_name,
    normalize_server_url,
    verify_signed_metadata,
)
from murmur.voice.livekit_control import (
    LiveKitControlPlane,
    LiveKitCredentials,
    create_default_voice_bootstrap_service,
)

SIGNING_SECRET = "voice-v2-test-signing-secret-with-more-than-32-bytes"
FIXED_NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
API_CALL_ID = "10000000-0000-4000-8000-000000000001"


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
    def get_by_id(self, key: str) -> Any | None:
        del key
        raise RuntimeError("database unavailable")


class _BlockingFirstLookup(_Lookup):
    """Leave one sync read running so async timeout/cancellation is observable."""

    def __init__(self, values: dict[str, Any]) -> None:
        super().__init__(values)
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self._guard = threading.Lock()

    def get_by_id(self, key: str) -> Any | None:
        with self._guard:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.entered.set()
            self.release.wait()
            self.finished.set()
        return super().get_by_id(key)


async def _wait_for_thread_event(event: threading.Event) -> None:
    observed = await asyncio.wait_for(asyncio.to_thread(event.wait, 1.0), timeout=1.1)
    assert observed


async def _wait_for_runner_idle(runner: BoundedSyncRunner) -> None:
    for _ in range(1_000):
        if runner.inflight_count == 0:
            return
        await asyncio.sleep(0)
    pytest.fail("bounded sync runner did not become idle")


class _FakeControlPlane:
    def __init__(self, *, yield_during_create: bool = False) -> None:
        self.rooms: dict[str, RoomRecord] = {}
        self.dispatches: dict[str, list[DispatchRecord]] = {}
        self.room_create_count = 0
        self.dispatch_create_count = 0
        self.token_specs: list[ParticipantTokenSpec] = []
        self.dispatch_delete_calls: list[tuple[str, str]] = []
        self.room_delete_calls: list[str] = []
        self.fail_dispatch_delete = False
        self.fail_room_delete = False
        self.retain_deleted_dispatch_tombstone = False
        self.yield_during_create = yield_during_create

    async def get_room(self, room_name: str) -> RoomRecord | None:
        return self.rooms.get(room_name)

    async def create_room(self, spec: CreateRoomSpec) -> RoomRecord:
        if self.yield_during_create:
            await asyncio.sleep(0)
        self.room_create_count += 1
        room = RoomRecord(name=spec.name, metadata=spec.metadata, num_participants=0)
        self.rooms[spec.name] = room
        initial_dispatch = spec.initial_dispatch
        assert initial_dispatch.room_name == spec.name
        assert initial_dispatch.restart_policy == "never"
        self.dispatch_create_count += 1
        self.dispatches.setdefault(spec.name, []).append(
            DispatchRecord(
                id=f"dispatch-{self.dispatch_create_count}",
                room_name=spec.name,
                agent_name=initial_dispatch.agent_name,
                metadata=initial_dispatch.metadata,
            )
        )
        return room

    async def list_dispatches(self, room_name: str) -> list[DispatchRecord]:
        return list(self.dispatches.get(room_name, []))

    async def create_dispatch(self, spec: CreateDispatchSpec) -> DispatchRecord:
        if self.yield_during_create:
            await asyncio.sleep(0)
        assert spec.restart_policy == "never"
        self.dispatch_create_count += 1
        dispatch = DispatchRecord(
            id=f"dispatch-{self.dispatch_create_count}",
            room_name=spec.room_name,
            agent_name=spec.agent_name,
            metadata=spec.metadata,
        )
        self.dispatches.setdefault(spec.room_name, []).append(dispatch)
        return dispatch

    async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
        self.dispatch_delete_calls.append((dispatch_id, room_name))
        if self.fail_dispatch_delete:
            raise RuntimeError("dispatch deletion failed")
        if self.retain_deleted_dispatch_tombstone:
            self.dispatches[room_name] = [
                replace(dispatch, deleted_at=1) if dispatch.id == dispatch_id else dispatch
                for dispatch in self.dispatches.get(room_name, [])
            ]
        else:
            self.dispatches[room_name] = [
                dispatch
                for dispatch in self.dispatches.get(room_name, [])
                if dispatch.id != dispatch_id
            ]

    async def delete_room(self, room_name: str) -> None:
        self.room_delete_calls.append(room_name)
        if self.fail_room_delete:
            raise RuntimeError("room deletion failed")
        self.rooms.pop(room_name, None)

    def issue_participant_token(self, spec: ParticipantTokenSpec) -> str:
        if spec.expires_at <= spec.issued_at:
            raise ValueError("expired token")
        self.token_specs.append(spec)
        return f"participant-token-{len(self.token_specs)}"


def _settings(**overrides: Any) -> VoiceBootstrapSettings:
    values = {
        "server_url": "https://murmur-test.livekit.cloud",
        "environment": "test",
        "profile_id": "livekit-agents-cascade-v1",
        "worker_name": "murmur-voice-v2",
        "event_topic": VOICE_V2_EVENT_TOPIC,
        "signing_secret": SIGNING_SECRET,
        "token_ttl_seconds": 300,
        "job_metadata_ttl_seconds": 300,
    }
    values.update(overrides)
    return VoiceBootstrapSettings(**values)


def _service(
    control_plane: _FakeControlPlane | None = None,
    *,
    sessions: dict[str, _Session] | None = None,
    agents: dict[str, _Agent] | None = None,
    settings: VoiceBootstrapSettings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[VoiceBootstrapService, _FakeControlPlane]:
    control_plane = control_plane or _FakeControlPlane()
    if sessions is None:
        sessions = {"session-1": _Session("session-1", "user-1", "agent-1")}
    if agents is None:
        agents = {"agent-1": _Agent("agent-1", "user-1")}
    return (
        VoiceBootstrapService(
            control_plane,
            settings or _settings(),
            session_repo=_Lookup(sessions),
            agent_repo=_Lookup(agents),
            clock=clock or (lambda: FIXED_NOW),
        ),
        control_plane,
    )


@pytest.mark.asyncio
async def test_bootstrap_uses_opaque_room_signed_metadata_and_restricted_grants() -> None:
    service, control_plane = _service()

    result = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-1",
    )

    assert result.runtime == "livekit_v2"
    assert result.profile_id == "livekit-agents-cascade-v1"
    assert result.server_url == "wss://murmur-test.livekit.cloud"
    assert result.event_topic == "murmur.voice.v2.events"
    assert result.expires_at == FIXED_NOW + timedelta(minutes=5)
    assert all(raw not in result.room_name for raw in ("user-1", "session-1", "call-1"))
    assert result.room_name.startswith("murmur-test-")

    room = control_plane.rooms[result.room_name]
    room_payload = verify_signed_metadata(room.metadata, SIGNING_SECRET, purpose="room")
    dispatch = control_plane.dispatches[result.room_name][0]
    job_payload = verify_signed_metadata(dispatch.metadata, SIGNING_SECRET, purpose="job")
    token_spec = control_plane.token_specs[0]
    participant_payload = verify_signed_metadata(
        token_spec.metadata,
        SIGNING_SECRET,
        purpose="participant",
    )
    assert room_payload == job_payload == participant_payload
    assert room_payload == {
        "agent_id": "agent-1",
        "agent_participant_identity": result.agent_participant_identity,
        "environment": "test",
        "event_topic": "murmur.voice.v2.events",
        "job_expires_at": int((FIXED_NOW + timedelta(minutes=5)).timestamp()),
        "job_issued_at": int(FIXED_NOW.timestamp()),
        "participant_identity": result.participant_identity,
        "profile_id": "livekit-agents-cascade-v1",
        "room_name": result.room_name,
        "runtime": "livekit_v2",
        "session_id": "session-1",
        "trace_id": room_payload["trace_id"],
        "user_id": "user-1",
        "voice_call_id": "call-1",
        "worker_name": "murmur-voice-v2",
    }
    assert token_spec.grants.room_name == result.room_name
    assert token_spec.grants.can_publish_sources == ("microphone",)
    assert token_spec.grants.can_publish is True
    assert token_spec.grants.can_publish_data is False
    assert token_spec.grants.can_subscribe is True
    assert token_spec.grants.can_update_own_metadata is False
    assert token_spec.grants.room_create is False
    assert token_spec.grants.room_list is False
    assert token_spec.grants.room_admin is False
    assert token_spec.grants.room_record is False
    assert token_spec.grants.ingress_admin is False
    assert token_spec.grants.agent is False
    assert token_spec.grants.can_manage_agent_session is False
    assert result.trace_id == room_payload["trace_id"]


@pytest.mark.asyncio
async def test_retry_reuses_room_profile_and_exact_dispatch() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(clock=lambda: current_time[0])

    first = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    first_dispatch_metadata = control_plane.dispatches[first.room_name][0].metadata
    first_job_payload = verify_signed_metadata(
        first_dispatch_metadata,
        SIGNING_SECRET,
        purpose="job",
    )
    current_time[0] += timedelta(seconds=60)
    second = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )

    assert second.room_name == first.room_name
    assert second.profile_id == first.profile_id
    assert second.dispatch_id == first.dispatch_id
    assert second.participant_identity == first.participant_identity
    assert control_plane.dispatches[first.room_name][0].metadata == first_dispatch_metadata
    assert first_job_payload["job_issued_at"] == int(FIXED_NOW.timestamp())
    assert first_job_payload["job_expires_at"] == int(
        (FIXED_NOW + timedelta(minutes=5)).timestamp()
    )
    assert control_plane.room_create_count == 1
    assert control_plane.dispatch_create_count == 1
    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_retry_rejects_expired_retained_assignment_and_requires_new_call_id() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(clock=lambda: current_time[0])
    first = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-expiring",
    )
    control_plane.rooms[first.room_name] = replace(
        control_plane.rooms[first.room_name],
        num_participants=1,
    )
    current_time[0] = FIXED_NOW + timedelta(minutes=5)

    with pytest.raises(VoiceBootstrapConflict, match="assignment expired"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-expiring",
        )

    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-new",
        )
    assert control_plane.dispatch_delete_calls == []
    assert control_plane.room_delete_calls == []

    control_plane.rooms[first.room_name] = replace(
        control_plane.rooms[first.room_name],
        num_participants=0,
    )
    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-new",
        )
    assert control_plane.room_delete_calls == []

    # Stale reclamation mutates neither dispatch nor room. Simulate LiveKit's
    # configured empty-room expiry.
    control_plane.rooms.pop(first.room_name)
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-new"
    )
    assert result.voice_call_id == "call-new"
    assert control_plane.dispatch_delete_calls == []
    assert control_plane.room_delete_calls == []


@pytest.mark.asyncio
async def test_concurrent_retry_creates_one_room_and_one_dispatch() -> None:
    service, control_plane = _service(_FakeControlPlane(yield_during_create=True))

    first, second = await asyncio.gather(
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1"),
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1"),
    )

    assert first.room_name == second.room_name
    assert first.dispatch_id == second.dispatch_id
    assert control_plane.room_create_count == 1
    assert control_plane.dispatch_create_count == 1
    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_active_call_cap_allows_retry_but_rejects_a_different_call() -> None:
    service, control_plane = _service(settings=_settings(max_active_calls=1))

    first = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    retry = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )

    assert retry.dispatch_id == first.dispatch_id
    assert control_plane.dispatch_create_count == 1
    assert service.active_assignment_count == 1
    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-2")


@pytest.mark.asyncio
async def test_release_deletes_exact_dispatch_and_room_then_frees_capacity_idempotently() -> None:
    service, control_plane = _service(settings=_settings(max_active_calls=1))
    first = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert control_plane.dispatch_delete_calls == [(first.dispatch_id, first.room_name)]
    assert control_plane.room_delete_calls == [first.room_name]
    assert service.active_assignment_count == 0

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    assert control_plane.dispatch_delete_calls == [(first.dispatch_id, first.room_name)]
    assert control_plane.room_delete_calls == [first.room_name]

    second = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-2"
    )
    assert second.voice_call_id == "call-2"


@pytest.mark.asyncio
async def test_release_before_bootstrap_records_expiring_cancel_intent() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(
        clock=lambda: current_time[0],
    )

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-cancelled")
    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-cancelled")
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-cancelled",
        )
    assert service.release_tombstone_count == 1
    assert service.active_assignment_count == 0
    assert control_plane.room_create_count == 0

    current_time[0] += timedelta(seconds=RELEASE_TOMBSTONE_TTL_SECONDS - 1)
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-cancelled",
        )

    current_time[0] += timedelta(seconds=1)
    result = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-cancelled",
    )
    assert result.voice_call_id == "call-cancelled"
    assert service.release_tombstone_count == 0


@pytest.mark.asyncio
async def test_release_during_gated_auth_survives_short_credential_ttls() -> None:
    current_time = [FIXED_NOW]
    sessions = _BlockingFirstLookup({"session-1": _Session("session-1", "user-1", "agent-1")})
    control_plane = _FakeControlPlane()
    runner = BoundedSyncRunner(max_workers=2, thread_name_prefix="bootstrap-release-race-test")
    service = VoiceBootstrapService(
        control_plane,
        _settings(
            token_ttl_seconds=30,
            job_metadata_ttl_seconds=30,
            repository_timeout_seconds=30,
        ),
        session_repo=sessions,
        agent_repo=_Lookup({"agent-1": _Agent("agent-1", "user-1")}),
        repository_runner=runner,
        clock=lambda: current_time[0],
    )
    bootstrap = asyncio.create_task(
        service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-auth-race",
        )
    )

    try:
        await _wait_for_thread_event(sessions.entered)
        await service.release(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-auth-race",
        )
        assert service.release_tombstone_count == 1

        current_time[0] += timedelta(seconds=31)
        sessions.release.set()

        with pytest.raises(VoiceBootstrapConflict, match="was released"):
            await bootstrap

        assert service.release_tombstone_count == 1
        assert control_plane.room_create_count == 0
        assert control_plane.dispatch_create_count == 0
        assert control_plane.token_specs == []
    finally:
        sessions.release.set()
        if not bootstrap.done():
            await asyncio.gather(bootstrap, return_exceptions=True)
        await _wait_for_thread_event(sessions.finished)
        await _wait_for_runner_idle(runner)
        await runner.aclose()


@pytest.mark.asyncio
async def test_release_tombstones_fail_closed_at_capacity_without_eviction() -> None:
    service, _ = _service(settings=_settings(max_call_assignments=1))

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-old")
    with pytest.raises(VoiceBootstrapUnavailable, match="release-tombstone capacity"):
        await service.release(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-new",
        )

    assert service.release_tombstone_count == 1
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-old")
    with pytest.raises(VoiceBootstrapUnavailable, match="cancellation state is saturated"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-new")


@pytest.mark.asyncio
async def test_tombstone_overflow_blocks_rejected_call_for_full_rejection_ttl() -> None:
    current_time = [FIXED_NOW]
    service, _ = _service(
        settings=_settings(max_call_assignments=1),
        clock=lambda: current_time[0],
    )
    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-old")
    current_time[0] += timedelta(seconds=RELEASE_TOMBSTONE_TTL_SECONDS - 1)

    with pytest.raises(VoiceBootstrapUnavailable, match="release-tombstone capacity"):
        await service.release(
            user_id="user-1", session_id="session-1", voice_call_id="call-rejected"
        )

    # The stored old tombstone expires one second later, but saturation is
    # measured from the rejected cancel, so that call cannot resurrect early.
    current_time[0] += timedelta(seconds=2)
    with pytest.raises(VoiceBootstrapUnavailable, match="cancellation state is saturated"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-rejected"
        )

    current_time[0] += timedelta(seconds=RELEASE_TOMBSTONE_TTL_SECONDS - 2)
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-rejected"
    )
    assert result.voice_call_id == "call-rejected"


@pytest.mark.asyncio
async def test_tombstone_overflow_blocks_fresh_tokens_for_existing_assignments() -> None:
    service, control_plane = _service(
        settings=_settings(max_active_calls=1, max_call_assignments=2)
    )
    first = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-active"
    )
    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-old")
    control_plane.fail_dispatch_delete = True

    with pytest.raises(VoiceBootstrapUnavailable, match="dispatch cleanup"):
        await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-active")
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-active"
        )

    assert control_plane.dispatch_create_count == 1
    assert len(control_plane.token_specs) == 1
    assert service.active_assignment_count == 1
    assert service.release_tombstone_count == 2
    assert first.room_name in control_plane.rooms


@pytest.mark.asyncio
async def test_bootstrap_then_concurrent_release_cleans_and_prevents_late_retry() -> None:
    class _GatedBootstrapControlPlane(_FakeControlPlane):
        def __init__(self) -> None:
            super().__init__()
            self.bootstrap_entered = asyncio.Event()
            self.continue_bootstrap = asyncio.Event()
            self._blocked = False

        async def get_room(self, room_name: str) -> RoomRecord | None:
            if not self._blocked:
                self._blocked = True
                self.bootstrap_entered.set()
                await self.continue_bootstrap.wait()
            return await super().get_room(room_name)

    control_plane = _GatedBootstrapControlPlane()
    service, _ = _service(
        control_plane,
        settings=_settings(
            max_concurrent_bootstraps=1,
            control_plane_timeout_seconds=0.1,
        ),
    )
    bootstrap = asyncio.create_task(
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-race")
    )
    await control_plane.bootstrap_entered.wait()
    release = asyncio.create_task(
        service.release(user_id="user-1", session_id="session-1", voice_call_id="call-race")
    )
    for _ in range(1_000):
        if service.release_tombstone_count == 1:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("release did not record its cancellation intent")
    control_plane.continue_bootstrap.set()

    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await bootstrap
    await release

    assert control_plane.dispatch_delete_calls == [
        ("dispatch-1", control_plane.room_delete_calls[0])
    ]
    assert len(control_plane.room_delete_calls) == 1
    assert service.active_assignment_count == 0
    assert service.release_tombstone_count == 1
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-race")


@pytest.mark.asyncio
async def test_successful_release_tombstone_rejects_same_call_from_another_session() -> None:
    service, _ = _service(
        sessions={
            "session-1": _Session("session-1", "user-1", "agent-1"),
            "session-2": _Session("session-2", "user-1", "agent-1"),
        }
    )
    await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    with pytest.raises(VoiceBootstrapConflict, match="another trusted scope"):
        await service.bootstrap(user_id="user-1", session_id="session-2", voice_call_id="call-1")


@pytest.mark.asyncio
async def test_release_verifies_authoritative_owner_and_exact_assignment_scope() -> None:
    service, control_plane = _service(
        sessions={
            "session-1": _Session("session-1", "user-1", "agent-1"),
            "session-2": _Session("session-2", "user-1", "agent-1"),
            "foreign-session": _Session("foreign-session", "user-2", "agent-2"),
        },
        agents={
            "agent-1": _Agent("agent-1", "user-1"),
            "agent-2": _Agent("agent-2", "user-2"),
        },
    )
    await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    with pytest.raises(VoiceBootstrapForbidden):
        await service.release(
            user_id="user-1",
            session_id="foreign-session",
            voice_call_id="call-1",
        )
    with pytest.raises(VoiceBootstrapConflict, match="another trusted scope"):
        await service.release(user_id="user-1", session_id="session-2", voice_call_id="call-1")

    assert service.active_assignment_count == 1
    assert control_plane.dispatch_delete_calls == []
    assert control_plane.room_delete_calls == []


@pytest.mark.asyncio
async def test_uncertain_release_retains_capacity_until_cleanup_retry_is_confirmed() -> None:
    service, control_plane = _service(settings=_settings(max_active_calls=1))
    await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    control_plane.fail_dispatch_delete = True

    with pytest.raises(VoiceBootstrapUnavailable, match="dispatch cleanup"):
        await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert service.active_assignment_count == 1
    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-2")

    control_plane.fail_dispatch_delete = False
    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    second = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-2"
    )
    assert second.voice_call_id == "call-2"


@pytest.mark.asyncio
async def test_cleanup_timeout_is_unavailable_and_retains_assignment() -> None:
    class _HangingDeleteControlPlane(_FakeControlPlane):
        async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
            self.dispatch_delete_calls.append((dispatch_id, room_name))
            await asyncio.Event().wait()

    service, control_plane = _service(
        _HangingDeleteControlPlane(),
        settings=_settings(control_plane_timeout_seconds=0.01),
    )
    await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    with pytest.raises(VoiceBootstrapUnavailable, match="dispatch cleanup"):
        await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert service.active_assignment_count == 1
    assert service.release_tombstone_count == 1
    assert control_plane.room_delete_calls == []


@pytest.mark.asyncio
async def test_deleted_dispatch_tombstone_is_not_reconciled_as_active() -> None:
    service, control_plane = _service()
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    active = control_plane.dispatches[result.room_name][0]
    control_plane.dispatches[result.room_name] = [replace(active, deleted_at=123)]

    with pytest.raises(VoiceBootstrapConflict, match="already deleted"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    assert service.active_assignment_count == 0
    assert control_plane.dispatch_delete_calls == []
    assert control_plane.dispatches[result.room_name] == [replace(active, deleted_at=123)]


@pytest.mark.asyncio
async def test_release_accepts_exact_deleted_dispatch_tombstone_as_confirmation() -> None:
    service, control_plane = _service()
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    active = control_plane.dispatches[result.room_name][0]
    control_plane.retain_deleted_dispatch_tombstone = True

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert service.active_assignment_count == 0
    assert control_plane.dispatch_delete_calls == [(active.id, result.room_name)]
    assert control_plane.dispatches[result.room_name] == [replace(active, deleted_at=1)]


@pytest.mark.asyncio
async def test_release_accepts_delete_then_raise_when_readback_confirms_absence() -> None:
    class _DeleteThenRaiseControlPlane(_FakeControlPlane):
        async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
            await super().delete_dispatch(dispatch_id, room_name)
            raise RuntimeError("response was lost after deletion")

    service, control_plane = _service(_DeleteThenRaiseControlPlane())
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert service.active_assignment_count == 0
    assert control_plane.dispatch_delete_calls == [(result.dispatch_id, result.room_name)]
    assert control_plane.room_delete_calls == [result.room_name]


@pytest.mark.asyncio
async def test_release_accepts_room_disappearing_with_dispatch_without_listing_again() -> None:
    class _RoomDisappearsWithDispatchControlPlane(_FakeControlPlane):
        def __init__(self) -> None:
            super().__init__()
            self.dispatch_deleted = False

        async def list_dispatches(self, room_name: str) -> list[DispatchRecord]:
            if self.dispatch_deleted and room_name not in self.rooms:
                raise AssertionError("dispatch listing is invalid after room deletion")
            return await super().list_dispatches(room_name)

        async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
            await super().delete_dispatch(dispatch_id, room_name)
            self.rooms.pop(room_name, None)
            self.dispatch_deleted = True

    service, control_plane = _service(_RoomDisappearsWithDispatchControlPlane())
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )

    await service.release(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    assert service.active_assignment_count == 0
    assert control_plane.dispatch_delete_calls == [(result.dispatch_id, result.room_name)]
    assert control_plane.room_delete_calls == []


@pytest.mark.asyncio
async def test_stale_active_room_is_never_mutated_during_reclaim() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(clock=lambda: current_time[0])
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-old"
    )
    control_plane.rooms[result.room_name] = replace(
        control_plane.rooms[result.room_name],
        num_participants=1,
    )
    current_time[0] += timedelta(minutes=5)

    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-new")

    assert service.active_assignment_count == 1
    assert result.room_name in control_plane.rooms
    assert control_plane.dispatch_delete_calls == []
    assert control_plane.room_delete_calls == []


@pytest.mark.asyncio
async def test_stale_empty_room_waits_for_natural_disappearance_before_reclaim() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(clock=lambda: current_time[0])
    old = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-old"
    )
    current_time[0] += timedelta(minutes=5)

    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-new")

    assert control_plane.dispatch_delete_calls == []
    assert control_plane.room_delete_calls == []
    assert service.active_assignment_count == 1

    control_plane.rooms.pop(old.room_name)
    recovered = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-new"
    )
    assert recovered.voice_call_id == "call-new"
    assert service.active_assignment_count == 1


@pytest.mark.asyncio
async def test_one_admission_reclaims_at_most_one_stale_assignment() -> None:
    current_time = [FIXED_NOW]
    service, control_plane = _service(
        settings=_settings(max_active_calls=2, max_call_assignments=4),
        clock=lambda: current_time[0],
    )
    first = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-old-1"
    )
    second = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-old-2"
    )
    control_plane.rooms.pop(first.room_name)
    # Real LiveKit returns room-not-found if dispatch listing is attempted
    # after natural room expiry; reclamation must not make that call.
    control_plane.dispatches.pop(first.room_name)
    control_plane.rooms.pop(second.room_name)
    control_plane.dispatches.pop(second.room_name)
    current_time[0] += timedelta(minutes=5)

    admitted = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-new"
    )

    assert admitted.voice_call_id == "call-new"
    assert service.active_assignment_count == 2
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-old-1"
        )
    with pytest.raises(VoiceBootstrapConflict, match="assignment expired"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-old-2"
        )


@pytest.mark.asyncio
async def test_expired_pre_dispatch_assignment_with_absent_remote_state_is_reclaimed() -> None:
    class _FailOnceBeforeRoomControlPlane(_FakeControlPlane):
        fail_get_room = True

        async def get_room(self, room_name: str) -> RoomRecord | None:
            if self.fail_get_room:
                raise RuntimeError("control plane unavailable")
            return await super().get_room(room_name)

    current_time = [FIXED_NOW]
    control_plane = _FailOnceBeforeRoomControlPlane()
    service, _ = _service(control_plane, clock=lambda: current_time[0])

    with pytest.raises(VoiceBootstrapUnavailable, match="control plane"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-failed"
        )
    assert service.active_assignment_count == 1
    assert control_plane.room_create_count == 0
    assert control_plane.dispatch_create_count == 0

    control_plane.fail_get_room = False
    current_time[0] += timedelta(minutes=5)
    recovered = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-recovered"
    )

    assert recovered.voice_call_id == "call-recovered"
    assert service.active_assignment_count == 1
    with pytest.raises(VoiceBootstrapConflict, match="was released"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-failed"
        )


@pytest.mark.asyncio
async def test_absent_room_reclaim_never_lists_room_bound_dispatch() -> None:
    class _RoomNotFoundDispatchControlPlane(_FakeControlPlane):
        expired_room_name: str | None = None

        async def list_dispatches(self, room_name: str) -> list[DispatchRecord]:
            if room_name == self.expired_room_name and room_name not in self.rooms:
                raise RuntimeError("room not found")
            return await super().list_dispatches(room_name)

    current_time = [FIXED_NOW]
    control_plane = _RoomNotFoundDispatchControlPlane()
    service, _ = _service(control_plane, clock=lambda: current_time[0])
    old = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-old"
    )
    control_plane.expired_room_name = old.room_name
    control_plane.rooms.pop(old.room_name)
    current_time[0] += timedelta(minutes=5)

    recovered = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-new"
    )

    assert recovered.voice_call_id == "call-new"
    assert service.active_assignment_count == 1


@pytest.mark.asyncio
async def test_same_call_id_with_conflicting_session_fails_closed() -> None:
    service, control_plane = _service(
        sessions={
            "session-1": _Session("session-1", "user-1", "agent-1"),
            "session-2": _Session("session-2", "user-1", "agent-1"),
        }
    )
    await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    with pytest.raises(VoiceBootstrapConflict, match="already assigned"):
        await service.bootstrap(user_id="user-1", session_id="session-2", voice_call_id="call-1")

    assert control_plane.room_create_count == 1
    assert control_plane.dispatch_create_count == 1


@pytest.mark.asyncio
async def test_missing_foreign_and_mismatched_agent_scopes_are_rejected() -> None:
    missing_service, _ = _service(sessions={})
    with pytest.raises(VoiceBootstrapNotFound, match="Session"):
        await missing_service.bootstrap(
            user_id="user-1", session_id="missing", voice_call_id="call-1"
        )

    foreign_service, _ = _service(
        sessions={"session-1": _Session("session-1", "user-2", "agent-1")}
    )
    with pytest.raises(VoiceBootstrapForbidden):
        await foreign_service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-1"
        )

    mismatch_service, _ = _service(agents={"agent-1": _Agent("agent-1", "user-2")})
    with pytest.raises(VoiceBootstrapForbidden):
        await mismatch_service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-1"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["", "u" * 129])
async def test_bootstrap_rejects_invalid_opaque_user_locator(user_id: str) -> None:
    service, _ = _service(
        sessions={"session-1": _Session("session-1", user_id, "agent-1")},
        agents={"agent-1": _Agent("agent-1", user_id)},
    )

    with pytest.raises(VoiceBootstrapConflict, match="user_id"):
        await service.bootstrap(
            user_id=user_id,
            session_id="session-1",
            voice_call_id="call-1",
        )


@pytest.mark.asyncio
async def test_room_profile_or_signature_mismatch_fails_closed() -> None:
    service, control_plane = _service()
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    control_plane.rooms[result.room_name] = RoomRecord(
        name=result.room_name,
        metadata='{"profile_id":"attacker-selected"}',
    )

    with pytest.raises(VoiceBootstrapConflict, match="room metadata"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")


@pytest.mark.asyncio
async def test_worker_metadata_mismatch_duplicate_or_missing_dispatch_fails_closed() -> None:
    service, control_plane = _service()
    result = await service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    dispatch = control_plane.dispatches[result.room_name][0]
    control_plane.dispatches[result.room_name] = [
        DispatchRecord(
            id=dispatch.id,
            room_name=dispatch.room_name,
            agent_name=dispatch.agent_name,
            metadata="tampered",
        )
    ]
    with pytest.raises(VoiceBootstrapConflict, match="worker dispatch"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    control_plane.dispatches[result.room_name] = [dispatch, dispatch]
    with pytest.raises(VoiceBootstrapConflict, match="ambiguous"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")

    control_plane.dispatches[result.room_name] = []
    with pytest.raises(VoiceBootstrapConflict, match="no longer present"):
        await service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")


@pytest.mark.asyncio
async def test_control_plane_timeout_and_active_capacity_are_bounded() -> None:
    class _BlockedControlPlane(_FakeControlPlane):
        async def get_room(self, room_name: str) -> RoomRecord | None:
            del room_name
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    timeout_service, _ = _service(
        _BlockedControlPlane(),
        settings=_settings(control_plane_timeout_seconds=0.01),
    )
    with pytest.raises(VoiceBootstrapUnavailable, match="control plane"):
        await timeout_service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-timeout"
        )

    capacity_service, _ = _service(settings=_settings(max_active_calls=1))
    await capacity_service.bootstrap(
        user_id="user-1", session_id="session-1", voice_call_id="call-1"
    )
    with pytest.raises(VoiceBootstrapUnavailable, match="active-call capacity"):
        await capacity_service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-2"
        )


@pytest.mark.asyncio
async def test_bootstrap_acquires_capacity_before_starting_repository_io() -> None:
    sessions = _BlockingFirstLookup({"session-1": _Session("session-1", "user-1", "agent-1")})
    service = VoiceBootstrapService(
        _FakeControlPlane(),
        _settings(
            control_plane_timeout_seconds=0.01,
            repository_timeout_seconds=1.0,
            max_concurrent_bootstraps=1,
            max_active_calls=2,
        ),
        session_repo=sessions,
        agent_repo=_Lookup({"agent-1": _Agent("agent-1", "user-1")}),
        clock=lambda: FIXED_NOW,
    )
    first = asyncio.create_task(
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    )
    try:
        await _wait_for_thread_event(sessions.entered)

        with pytest.raises(VoiceBootstrapUnavailable, match="bootstrap capacity"):
            await service.bootstrap(
                user_id="user-1",
                session_id="session-1",
                voice_call_id="call-2",
            )

        assert sessions.calls == 1
    finally:
        sessions.release.set()
    await first

    result = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-3",
    )
    assert result.voice_call_id == "call-3"
    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_bootstrap_repository_timeout_releases_capacity_while_sync_read_finishes() -> None:
    sessions = _BlockingFirstLookup({"session-1": _Session("session-1", "user-1", "agent-1")})
    runner = BoundedSyncRunner(max_workers=1, thread_name_prefix="bootstrap-repo-timeout-test")
    service = VoiceBootstrapService(
        _FakeControlPlane(),
        _settings(
            control_plane_timeout_seconds=1.0,
            repository_timeout_seconds=0.01,
            max_concurrent_bootstraps=1,
        ),
        session_repo=sessions,
        agent_repo=_Lookup({"agent-1": _Agent("agent-1", "user-1")}),
        repository_runner=runner,
        clock=lambda: FIXED_NOW,
    )
    try:
        with pytest.raises(VoiceBootstrapUnavailable, match="repository lookup timed out"):
            await service.bootstrap(
                user_id="user-1",
                session_id="session-1",
                voice_call_id="call-timeout",
            )
        assert sessions.entered.is_set()

        with pytest.raises(VoiceBootstrapUnavailable, match="repository capacity"):
            await service.bootstrap(
                user_id="user-1",
                session_id="session-1",
                voice_call_id="call-after-timeout",
            )
        assert sessions.calls == 1
    finally:
        sessions.release.set()
        await _wait_for_thread_event(sessions.finished)
        await _wait_for_runner_idle(runner)

    result = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-after-timeout",
    )
    assert result.voice_call_id == "call-after-timeout"
    await runner.aclose()

    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_bootstrap_normalizes_unexpected_repository_failure() -> None:
    service = VoiceBootstrapService(
        _FakeControlPlane(),
        _settings(),
        session_repo=_FailingLookup(),
        agent_repo=_Lookup({"agent-1": _Agent("agent-1", "user-1")}),
        clock=lambda: FIXED_NOW,
    )

    with pytest.raises(VoiceBootstrapUnavailable, match="repository lookup failed"):
        await service.bootstrap(
            user_id="user-1", session_id="session-1", voice_call_id="call-failed"
        )


@pytest.mark.asyncio
async def test_cancelled_repository_wait_releases_bootstrap_capacity() -> None:
    sessions = _BlockingFirstLookup({"session-1": _Session("session-1", "user-1", "agent-1")})
    runner = BoundedSyncRunner(max_workers=1, thread_name_prefix="bootstrap-repo-cancel-test")
    service = VoiceBootstrapService(
        _FakeControlPlane(),
        _settings(
            control_plane_timeout_seconds=1.0,
            repository_timeout_seconds=1.0,
            max_concurrent_bootstraps=1,
        ),
        session_repo=sessions,
        agent_repo=_Lookup({"agent-1": _Agent("agent-1", "user-1")}),
        repository_runner=runner,
        clock=lambda: FIXED_NOW,
    )
    cancelled = asyncio.create_task(
        service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-cancelled",
        )
    )
    try:
        await _wait_for_thread_event(sessions.entered)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        with pytest.raises(VoiceBootstrapUnavailable, match="repository capacity"):
            await service.bootstrap(
                user_id="user-1",
                session_id="session-1",
                voice_call_id="call-after-cancel",
            )
        assert sessions.calls == 1
    finally:
        sessions.release.set()
        await _wait_for_thread_event(sessions.finished)
        await _wait_for_runner_idle(runner)

    result = await service.bootstrap(
        user_id="user-1",
        session_id="session-1",
        voice_call_id="call-after-cancel",
    )
    assert result.voice_call_id == "call-after-cancel"
    await runner.aclose()

    assert service.active_lock_count == 0


@pytest.mark.asyncio
async def test_cancelled_lock_waiter_does_not_leak_call_lock() -> None:
    class _GatedControlPlane(_FakeControlPlane):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def get_room(self, room_name: str) -> RoomRecord | None:
            self.entered.set()
            await self.release.wait()
            return await super().get_room(room_name)

    control_plane = _GatedControlPlane()
    service, _ = _service(control_plane)
    first = asyncio.create_task(
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    )
    await control_plane.entered.wait()
    waiter = asyncio.create_task(
        service.bootstrap(user_id="user-1", session_id="session-1", voice_call_id="call-1")
    )
    for _ in range(20):
        await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    control_plane.release.set()
    await first
    assert service.active_lock_count == 0


@pytest.mark.parametrize(
    "server_url",
    [
        "wss://user:password@example.livekit.cloud",
        "wss://example.livekit.cloud/secret/path",
        "wss://example.livekit.cloud?token=secret",
        "ftp://example.livekit.cloud",
    ],
)
def test_server_url_rejects_secret_bearing_or_non_origin_values(server_url: str) -> None:
    with pytest.raises(VoiceBootstrapUnavailable):
        normalize_server_url(server_url)

    with pytest.raises(VoiceBootstrapUnavailable):
        _settings(server_url=server_url)


@pytest.mark.asyncio
async def test_default_bootstrap_degrades_invalid_v2_url_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from murmur.voice import livekit_control

    monkeypatch.setattr(livekit_control.config, "VOICE_RUNTIME", "livekit_v2")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_URL", "wss://example.test/secret")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setattr(livekit_control.config, "VOICE_V2_SIGNING_SECRET", SIGNING_SECRET)

    service = create_default_voice_bootstrap_service()

    with pytest.raises(VoiceBootstrapUnavailable, match="configuration is invalid"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-1",
        )


@pytest.mark.parametrize("job_metadata_ttl_seconds", [29, 901])
def test_job_metadata_ttl_is_bounded(job_metadata_ttl_seconds: int) -> None:
    with pytest.raises(ValueError, match="job metadata TTL"):
        _settings(job_metadata_ttl_seconds=job_metadata_ttl_seconds)


@pytest.mark.parametrize(
    "repository_timeout_seconds",
    [0, -1, 31, float("inf"), float("nan")],
)
def test_repository_timeout_is_finite_and_bounded(repository_timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="repository timeout"):
        _settings(repository_timeout_seconds=repository_timeout_seconds)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_active_calls": 0},
        {"max_active_calls": 2, "max_call_assignments": 1},
    ],
)
def test_active_call_limit_is_positive_and_within_assignment_capacity(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="active-call limit"):
        _settings(**overrides)


def test_default_bootstrap_wires_repository_timeout_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from murmur.voice import livekit_control

    monkeypatch.setattr(livekit_control.config, "VOICE_RUNTIME", "livekit_v2")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_URL", "wss://example.test")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setattr(livekit_control.config, "VOICE_V2_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setattr(livekit_control.config, "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", 1.25)
    monkeypatch.setattr(
        livekit_control.config,
        "VOICE_V2_EVENT_TOPIC",
        "attacker.control.topic",
        raising=False,
    )
    monkeypatch.setattr(
        livekit_control,
        "LiveKitControlPlane",
        lambda credentials: _FakeControlPlane(),
    )

    service = create_default_voice_bootstrap_service()

    assert isinstance(service, VoiceBootstrapService)
    assert service.settings.repository_timeout_seconds == 1.25
    assert service.settings.max_active_calls == 1
    assert service.settings.event_topic == VOICE_V2_EVENT_TOPIC


def test_bootstrap_settings_reject_noncanonical_event_topic() -> None:
    with pytest.raises(ValueError, match="event_topic must be"):
        _settings(event_topic="murmur.voice.v2.custom")


def test_legacy_main_import_ignores_malformed_optional_voice_v2_numerics() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "VOICE_RUNTIME": "legacy",
            "VOICE_V2_TOKEN_TTL_SECONDS": "not-an-integer",
            "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS": "not-a-number",
            "VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS": "also-invalid",
            "VOICE_V2_MAX_ACTIVE_CALLS": "not-an-integer-either",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_default_bootstrap_degrades_invalid_v2_numeric_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from murmur.voice import livekit_control

    monkeypatch.setattr(livekit_control.config, "VOICE_RUNTIME", "livekit_v2")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_URL", "wss://example.test")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setattr(livekit_control.config, "VOICE_V2_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setattr(livekit_control.config, "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", "bad")

    service = create_default_voice_bootstrap_service()

    with pytest.raises(VoiceBootstrapUnavailable, match="configuration is invalid"):
        await service.bootstrap(
            user_id="user-1",
            session_id="session-1",
            voice_call_id="call-1",
        )


def test_room_derivation_changes_with_every_trusted_scope_dimension() -> None:
    settings = _settings()
    base = VoiceScope(
        user_id="user-1", session_id="session-1", agent_id="agent-1", voice_call_id="call-1"
    )
    baseline = derive_room_name(settings, base)

    assert derive_room_name(_settings(environment="prod"), base) != baseline
    assert derive_room_name(settings, replace(base, user_id="user-2")) != baseline
    assert derive_room_name(settings, replace(base, session_id="session-2")) != baseline
    assert derive_room_name(settings, replace(base, voice_call_id="call-2")) != baseline


class _FakeAccessToken:
    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.values: dict[str, Any] = {}

    def with_identity(self, value: str):
        self.values["identity"] = value
        return self

    def with_name(self, value: str):
        self.values["name"] = value
        return self

    def with_metadata(self, value: str):
        self.values["metadata"] = value
        return self

    def with_ttl(self, value: timedelta):
        self.values["ttl"] = value
        return self

    def with_grants(self, value: Any):
        self.values["grants"] = value
        return self

    def to_jwt(self) -> str:
        return "locally-signed-token"


def test_livekit_adapter_maps_only_restricted_grants_without_network() -> None:
    issued: list[_FakeAccessToken] = []

    def access_token(api_key: str, api_secret: str) -> _FakeAccessToken:
        token = _FakeAccessToken(api_key, api_secret)
        issued.append(token)
        return token

    fake_sdk = SimpleNamespace(
        AccessToken=access_token,
        VideoGrants=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    adapter = LiveKitControlPlane(
        LiveKitCredentials("wss://example.livekit.cloud", "server-key", "server-secret"),
        sdk=fake_sdk,
    )
    grants = SimpleNamespace(
        room_name="room-1",
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=False,
        can_update_own_metadata=False,
        can_publish_sources=("microphone",),
        room_create=False,
        room_list=False,
        room_admin=False,
        room_record=False,
        ingress_admin=False,
        agent=False,
        can_manage_agent_session=False,
    )
    spec = ParticipantTokenSpec(
        identity="participant-1",
        name="Murmur user",
        metadata="signed-metadata",
        issued_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(minutes=5),
        grants=grants,
    )

    assert adapter.issue_participant_token(spec) == "locally-signed-token"
    token = issued[0]
    assert token.api_key == "server-key"
    assert token.api_secret == "server-secret"
    assert token.values["ttl"] == timedelta(minutes=5)
    assert vars(token.values["grants"]) == {
        "room": "room-1",
        "room_join": True,
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_data": False,
        "can_update_own_metadata": False,
        "can_publish_sources": ["microphone"],
        "room_create": False,
        "room_list": False,
        "room_admin": False,
        "room_record": False,
        "ingress_admin": False,
        "agent": False,
        "can_manage_agent_session": False,
    }

    with pytest.raises(ValueError, match="expiry"):
        adapter.issue_participant_token(
            ParticipantTokenSpec(
                identity="participant-1",
                name="Murmur user",
                metadata="signed-metadata",
                issued_at=FIXED_NOW,
                expires_at=FIXED_NOW,
                grants=grants,
            )
        )


@pytest.mark.asyncio
async def test_livekit_adapter_creates_room_with_exact_initial_dispatch() -> None:
    requests: list[SimpleNamespace] = []

    class _RoomService:
        async def create_room(self, request: SimpleNamespace) -> SimpleNamespace:
            requests.append(request)
            return SimpleNamespace(
                name=request.name,
                metadata=request.metadata,
                num_participants=0,
            )

    client = SimpleNamespace(room=_RoomService())
    never = object()
    fake_sdk = SimpleNamespace(
        LiveKitAPI=lambda **kwargs: client,
        CreateRoomRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        RoomAgentDispatch=lambda **kwargs: SimpleNamespace(**kwargs),
        JobRestartPolicy=SimpleNamespace(JRP_NEVER=never),
    )
    adapter = LiveKitControlPlane(
        LiveKitCredentials("wss://example.livekit.cloud", "key", "secret"),
        sdk=fake_sdk,
    )
    spec = CreateRoomSpec(
        name="room-1",
        metadata="signed-room",
        empty_timeout_seconds=60,
        departure_timeout_seconds=20,
        initial_dispatch=CreateDispatchSpec(
            room_name="room-1",
            agent_name="murmur-voice-v2",
            metadata="signed-job",
        ),
    )

    result = await adapter.create_room(spec)

    assert result == RoomRecord(
        name="room-1",
        metadata="signed-room",
        num_participants=0,
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.name == "room-1"
    assert request.metadata == "signed-room"
    assert request.empty_timeout == 60
    assert request.departure_timeout == 20
    assert request.max_participants == 2
    assert len(request.agents) == 1
    assert vars(request.agents[0]) == {
        "agent_name": "murmur-voice-v2",
        "metadata": "signed-job",
        "restart_policy": never,
    }

    with pytest.raises(ValueError, match="must target its room"):
        replace(
            spec,
            initial_dispatch=replace(spec.initial_dispatch, room_name="another-room"),
        )


@pytest.mark.asyncio
async def test_livekit_adapter_pins_dispatch_restart_policy_to_never() -> None:
    requests: list[SimpleNamespace] = []

    class _DispatchService:
        async def create_dispatch(self, request: SimpleNamespace) -> SimpleNamespace:
            requests.append(request)
            return SimpleNamespace(
                id="dispatch-1",
                room=request.room,
                agent_name=request.agent_name,
                metadata=request.metadata,
                state=SimpleNamespace(deleted_at=0),
            )

    client = SimpleNamespace(agent_dispatch=_DispatchService())
    never = object()
    fake_sdk = SimpleNamespace(
        LiveKitAPI=lambda **kwargs: client,
        CreateAgentDispatchRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        JobRestartPolicy=SimpleNamespace(JRP_NEVER=never),
    )
    adapter = LiveKitControlPlane(
        LiveKitCredentials("wss://example.livekit.cloud", "key", "secret"),
        sdk=fake_sdk,
    )
    spec = CreateDispatchSpec(
        room_name="room-1",
        agent_name="murmur-voice-v2",
        metadata="signed-job",
    )

    result = await adapter.create_dispatch(spec)

    assert spec.restart_policy == "never"
    assert result.id == "dispatch-1"
    assert len(requests) == 1
    assert requests[0].room == "room-1"
    assert requests[0].agent_name == "murmur-voice-v2"
    assert requests[0].metadata == "signed-job"
    assert requests[0].restart_policy is never
    with pytest.raises(ValueError, match="restart policy must be never"):
        CreateDispatchSpec(
            room_name="room-1",
            agent_name="murmur-voice-v2",
            metadata="signed-job",
            restart_policy="on_failure",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_livekit_adapter_reuses_one_client_and_closes_it_once() -> None:
    class _RoomService:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def list_rooms(self, request: object) -> SimpleNamespace:
            del request
            return SimpleNamespace(rooms=[])

        async def delete_room(self, request: SimpleNamespace) -> None:
            self.deleted.append(request.room)

    class _DispatchService:
        def __init__(self) -> None:
            self.deleted: list[tuple[str, str]] = []

        async def list_dispatch(self, room_name: str) -> list[object]:
            return [
                SimpleNamespace(
                    id="dispatch-deleted",
                    room=room_name,
                    agent_name="murmur-voice-v2",
                    metadata="signed-job",
                    state=SimpleNamespace(deleted_at=123),
                )
            ]

        async def delete_dispatch(self, dispatch_id: str, room_name: str) -> None:
            self.deleted.append((dispatch_id, room_name))

    class _Client:
        def __init__(self) -> None:
            self.room = _RoomService()
            self.agent_dispatch = _DispatchService()
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    clients: list[_Client] = []

    def create_client(**kwargs: object) -> _Client:
        assert set(kwargs) == {"url", "api_key", "api_secret"}
        client = _Client()
        clients.append(client)
        return client

    fake_sdk = SimpleNamespace(
        LiveKitAPI=create_client,
        ListRoomsRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        DeleteRoomRequest=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    adapter = LiveKitControlPlane(
        LiveKitCredentials("wss://example.livekit.cloud", "key", "secret"),
        sdk=fake_sdk,
    )

    assert await adapter.get_room("room-1") is None
    assert await adapter.list_dispatches("room-1") == [
        DispatchRecord(
            id="dispatch-deleted",
            room_name="room-1",
            agent_name="murmur-voice-v2",
            metadata="signed-job",
            deleted_at=123,
        )
    ]
    await adapter.delete_dispatch("dispatch-1", "room-1")
    await adapter.delete_room("room-1")
    assert len(clients) == 1
    assert clients[0].agent_dispatch.deleted == [("dispatch-1", "room-1")]
    assert clients[0].room.deleted == ["room-1"]

    await adapter.aclose()
    await adapter.aclose()
    assert clients[0].close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        await adapter.get_room("room-1")


def _seed_api_scope() -> tuple[dict[str, str], Any, Any]:
    user = {"id": "api-owner", "email": "api-owner@example.com", "name": "Owner"}
    other = {"id": "api-other", "email": "api-other@example.com", "name": "Other"}
    UserRepo.get_or_create(uid=user["id"], email=user["email"], name=user["name"])
    UserRepo.get_or_create(uid=other["id"], email=other["email"], name=other["name"])
    agent = AgentRepo.create(user_id=user["id"], name="Tutor", system_prompt="Teach")
    other_agent = AgentRepo.create(
        user_id=other["id"], name="Other tutor", system_prompt="Teach other"
    )
    session = SessionRepo.create(user["id"], agent.id)
    other_session = SessionRepo.create(other["id"], other_agent.id)
    return user, session, other_session


def test_bootstrap_route_requires_auth_owned_session_and_strict_request() -> None:
    user, session, other_session = _seed_api_scope()
    service, control_plane = _service(
        sessions={
            session.id: _Session(session.id, user["id"], session.agent_id),
            other_session.id: _Session(
                other_session.id, other_session.user_id, other_session.agent_id
            ),
        },
        agents={
            session.agent_id: _Agent(session.agent_id, user["id"]),
            other_session.agent_id: _Agent(other_session.agent_id, other_session.user_id),
        },
    )
    app = create_application(voice_bootstrap_service=service)
    current_user: dict[str, str] | None = None

    def authenticate() -> dict[str, str]:
        if current_user is None:
            raise ApiError(401, "Not authenticated")
        return current_user

    app.dependency_overrides[get_authenticated_user] = authenticate
    with TestClient(app) as client:
        unauthenticated = client.post(
            "/api/voice/session",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert unauthenticated.status_code == 401
        unauthenticated_end = client.post(
            "/api/voice/session/end",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert unauthenticated_end.status_code == 401

        current_user = user
        foreign = client.post(
            "/api/voice/session",
            json={
                "session_id": other_session.id,
                "voice_call_id": "10000000-0000-4000-8000-000000000002",
            },
        )
        assert foreign.status_code == 403

        profile_override = client.post(
            "/api/voice/session",
            json={
                "session_id": session.id,
                "voice_call_id": "10000000-0000-4000-8000-000000000003",
                "profile_id": "attacker-selected",
            },
        )
        assert profile_override.status_code == 422

        success = client.post(
            "/api/voice/session",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert success.status_code == 200
        assert success.headers["cache-control"] == "no-store"

        invalid_end = client.post(
            "/api/voice/session/end",
            json={
                "session_id": session.id,
                "voice_call_id": "not-a-uuid",
                "extra": "forbidden",
            },
        )
        assert invalid_end.status_code == 422

        current_user = {"id": "api-other", "email": "other@example.com", "name": "Other"}
        foreign_end = client.post(
            "/api/voice/session/end",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert foreign_end.status_code == 403

        current_user = user
        ended = client.post(
            "/api/voice/session/end",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert ended.status_code == 204
        assert ended.headers["cache-control"] == "no-store"
        assert ended.content == b""

        ended_again = client.post(
            "/api/voice/session/end",
            json={"session_id": session.id, "voice_call_id": API_CALL_ID},
        )
        assert ended_again.status_code == 204

    payload = success.json()
    assert payload == {
        "runtime": "livekit_v2",
        "profile_id": "livekit-agents-cascade-v1",
        "server_url": "wss://murmur-test.livekit.cloud",
        "room_name": payload["room_name"],
        "participant_token": "participant-token-1",
        "participant_identity": payload["participant_identity"],
        "agent_participant_identity": payload["agent_participant_identity"],
        "session_id": session.id,
        "agent_id": session.agent_id,
        "voice_call_id": API_CALL_ID,
        "dispatch_id": "dispatch-1",
        "worker_name": "murmur-voice-v2",
        "event_topic": "murmur.voice.v2.events",
        "trace_id": payload["trace_id"],
        "expires_at": "2026-08-12T08:05:00Z",
    }
    serialized = success.text
    assert SIGNING_SECRET not in serialized
    assert "server-secret" not in serialized
    assert "api_key" not in serialized
    assert control_plane.dispatch_delete_calls == [("dispatch-1", payload["room_name"])]
    assert control_plane.room_delete_calls == [payload["room_name"]]


def test_bootstrap_and_release_routes_normalize_repository_failures_to_503() -> None:
    user, session, _other_session = _seed_api_scope()
    service = VoiceBootstrapService(
        _FakeControlPlane(),
        _settings(),
        session_repo=_FailingLookup(),
        agent_repo=_Lookup({}),
        clock=lambda: FIXED_NOW,
    )
    app = create_application(voice_bootstrap_service=service)
    app.dependency_overrides[get_authenticated_user] = lambda: user

    with TestClient(app) as client:
        for path in ("/api/voice/session", "/api/voice/session/end"):
            response = client.post(
                path,
                json={"session_id": session.id, "voice_call_id": API_CALL_ID},
            )

            assert response.status_code == 503
            assert response.json() == {"error": "Voice V2 repository lookup failed"}

"""Adversarial tests for authenticated Pipecat SmallWebRTC reservations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import pytest
from murmur.voice.pipecat_signaling import (
    PipecatCorsContract,
    PipecatHandlerRequestTypes,
    PipecatIceCandidate,
    PipecatOfferRequest,
    PipecatPatchRequest,
    PipecatPeerHandlerAdapter,
    PipecatReservationState,
    PipecatSignalingConflict,
    PipecatSignalingForbidden,
    PipecatSignalingNotFound,
    PipecatSignalingService,
    PipecatSignalingSettings,
    PipecatSignalingUnavailable,
)
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind

FIXED_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
SESSION_ID = "11111111-1111-4111-8111-111111111111"
AGENT_ID = "22222222-2222-4222-8222-222222222222"
CALL_ID = "33333333-3333-4333-8333-333333333333"
TRACE_ID = "44444444-4444-4444-8444-444444444444"
OTHER_CALL_ID = "55555555-5555-4555-8555-555555555555"
OTHER_TRACE_ID = "66666666-6666-4666-8666-666666666666"
TOKEN = "A" * 64


@dataclass(frozen=True)
class _Session:
    id: str
    user_id: str
    agent_id: str


@dataclass(frozen=True)
class _Agent:
    id: str
    user_id: str


class _Repo:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get_by_id(self, key: str) -> Any | None:
        return self.values.get(key)


class _UnavailableRepo:
    def get_by_id(self, key: str) -> None:
        del key
        raise RuntimeError("repository unavailable")


class _Connection:
    def __init__(self, pc_id: str) -> None:
        self.pc_id = pc_id
        self.handlers: dict[str, list[Any]] = {}
        self.disconnect_count = 0

    def add_event_handler(self, event_name: str, handler: Any) -> None:
        self.handlers.setdefault(event_name, []).append(handler)

    async def disconnect(self) -> None:
        self.disconnect_count += 1

    async def emit(self, event_name: str) -> None:
        for handler in tuple(self.handlers.get(event_name, ())):
            await handler(self)


class _RuntimeHandle:
    def __init__(
        self,
        *,
        fail_close_count: int = 0,
        completion_failure: BaseException | None = None,
    ) -> None:
        self.close_count = 0
        self.fail_close_count = fail_close_count
        self.completion_failure = completion_failure
        self.closed = asyncio.Event()

    async def wait_closed(self) -> None:
        await self.closed.wait()
        if self.completion_failure is not None:
            raise self.completion_failure

    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_count <= self.fail_close_count:
            raise RuntimeError("runtime cleanup failed")
        self.closed.set()

    def complete(self) -> None:
        self.closed.set()


class _RuntimeStarter:
    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        close_during_start: bool = False,
        fail_close_count: int = 0,
        completion_failure: BaseException | None = None,
    ) -> None:
        self.failure = failure
        self.close_during_start = close_during_start
        self.fail_close_count = fail_close_count
        self.completion_failure = completion_failure
        self.calls: list[tuple[_Connection, VoiceCallClaims]] = []
        self.handles: list[_RuntimeHandle] = []
        self.peer_close_tasks: list[asyncio.Task[None]] = []

    async def start(
        self,
        *,
        connection: _Connection,
        claims: VoiceCallClaims,
    ) -> _RuntimeHandle:
        self.calls.append((connection, claims))
        if self.failure is not None:
            raise self.failure
        handle = _RuntimeHandle(
            fail_close_count=self.fail_close_count,
            completion_failure=self.completion_failure,
        )
        self.handles.append(handle)
        if self.close_during_start:
            self.peer_close_tasks.append(asyncio.create_task(connection.emit("closed")))
            await asyncio.sleep(0)
        return handle


class _Handler:
    def __init__(
        self,
        *,
        pc_id: str = "SmallWebRTCConnection#1-peer-a",
        swallow_callback_failure: bool = True,
        enter: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        fail_close_count: int = 0,
    ) -> None:
        self.connection = _Connection(pc_id)
        self.swallow_callback_failure = swallow_callback_failure
        self.enter = enter
        self.release = release
        self.fail_close_count = fail_close_count
        self.initial_count = 0
        self.renegotiate_count = 0
        self.patch_requests: list[PipecatPatchRequest] = []
        self.close_count = 0
        self.callbacks: list[Any] = []

    async def handle_web_request(self, request: PipecatOfferRequest, callback: Any):
        if self.enter is not None:
            self.enter.set()
        if self.release is not None:
            await self.release.wait()
        if request.pc_id is None:
            self.initial_count += 1
            self.callbacks.append(callback)
            try:
                await callback(self.connection)
            except Exception:
                if not self.swallow_callback_failure:
                    raise
        else:
            self.renegotiate_count += 1
        return {"sdp": "answer-sdp", "type": "answer", "pc_id": self.connection.pc_id}

    async def handle_patch_request(self, request: PipecatPatchRequest) -> None:
        self.patch_requests.append(request)

    async def close(self) -> None:
        self.close_count += 1
        if self.close_count <= self.fail_close_count:
            raise RuntimeError("handler cleanup failed")


class _HangingHandler(_Handler):
    async def handle_web_request(self, request: PipecatOfferRequest, callback: Any):
        del request, callback
        await asyncio.Event().wait()


class _HangingCloseHandler(_Handler):
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        self.close_count += 1
        self.close_entered.set()
        await self.allow_close.wait()


class _CountingHangingCloseHandler(_Handler):
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = asyncio.Event()

    async def close(self) -> None:
        self.close_count += 1
        self.close_entered.set()
        await asyncio.Event().wait()


class _AdvanceClockHandler(_Handler):
    def __init__(self, advance: Any) -> None:
        super().__init__()
        self.advance = advance

    async def handle_web_request(self, request: PipecatOfferRequest, callback: Any):
        answer = await super().handle_web_request(request, callback)
        self.advance()
        return answer


def _claims(
    *,
    call_id: str = CALL_ID,
    trace_id: str = TRACE_ID,
    user_id: str = "firebase-user-1",
    issued_at: datetime = FIXED_NOW,
    expires_at: datetime | None = None,
) -> VoiceCallClaims:
    return VoiceCallClaims(
        user_id=user_id,
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        voice_call_id=call_id,
        trace_id=trace_id,
        runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
        profile_id="pipecat-direct-cascade-v1",
        issued_at=issued_at,
        expires_at=expires_at or issued_at + timedelta(seconds=60),
    )


def _settings(**overrides: Any) -> PipecatSignalingSettings:
    values: dict[str, Any] = {
        "signaling_base_url": "https://voice.example.test/v1/webrtc",
        "profile_id": "pipecat-direct-cascade-v1",
        "reservation_ttl_seconds": 60,
        "repository_timeout_seconds": 1.0,
        "signaling_timeout_seconds": 1.0,
        "cleanup_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return PipecatSignalingSettings(**values)


def _service(
    *,
    handlers: list[_Handler] | None = None,
    starter: _RuntimeStarter | None = None,
    clock: Any = None,
    sessions: dict[str, _Session] | None = None,
    agents: dict[str, _Agent] | None = None,
    settings: PipecatSignalingSettings | None = None,
    tokens: list[str] | None = None,
) -> tuple[PipecatSignalingService, list[_Handler], _RuntimeStarter]:
    handlers = handlers or []
    starter = starter or _RuntimeStarter()
    sessions = sessions or {
        SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID),
    }
    agents = agents or {AGENT_ID: _Agent(AGENT_ID, "firebase-user-1")}
    token_values = iter(tokens or [TOKEN, "B" * 64, "C" * 64])

    def handler_factory() -> _Handler:
        handler = _Handler(pc_id=f"SmallWebRTCConnection#{len(handlers) + 1}-peer")
        handlers.append(handler)
        return handler

    service = PipecatSignalingService(
        settings or _settings(),
        handler_factory=handler_factory,
        runtime_starter=starter,
        session_repo=_Repo(sessions),
        agent_repo=_Repo(agents),
        clock=clock or (lambda: FIXED_NOW),
        token_factory=lambda: next(token_values),
    )
    return service, handlers, starter


def _token(assignment: Any) -> str:
    url = assignment.webrtc_url.get_secret_value()
    return urlsplit(url).path.rsplit("/", 1)[1]


def _initial_offer() -> PipecatOfferRequest:
    return PipecatOfferRequest(sdp="offer-sdp")


def _patch(pc_id: str) -> PipecatPatchRequest:
    return PipecatPatchRequest(
        pc_id=pc_id,
        candidates=(
            PipecatIceCandidate(
                candidate="candidate:1 1 UDP 1 127.0.0.1 40000 typ host",
                sdp_mid="0",
                sdp_mline_index=0,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_reserve_returns_opaque_secret_url_and_stores_only_its_digest() -> None:
    service, handlers, _ = _service()

    assignment = await service.reserve(_claims())
    raw_url = assignment.webrtc_url.get_secret_value()
    token = _token(assignment)

    assert assignment.runtime is VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1
    assert assignment.event_protocol == "rtvi-murmur-v2"
    assert token == TOKEN
    assert assignment.peer_reservation_id not in raw_url
    assert all(
        identity not in raw_url
        for identity in (
            "firebase-user-1",
            SESSION_ID,
            AGENT_ID,
            CALL_ID,
            TRACE_ID,
        )
    )
    assert token not in repr(service.__dict__)
    assert token.encode() not in {record.token_hash for record in service._reservations.values()}
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.RESERVED
    assert not hasattr(snapshot, "handler")
    assert not hasattr(snapshot, "token")
    assert len(handlers) == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_initial_renegotiation_patch_and_delete_are_exact_and_idempotent() -> None:
    service, handlers, starter = _service()
    claims = _claims()
    assignment = await service.reserve(claims)
    token = _token(assignment)

    answer = await service.offer(
        token=token,
        user_id=claims.user_id,
        request=_initial_offer(),
    )
    assert answer.pc_id == handlers[0].connection.pc_id
    assert service.active_call_count == 1
    assert starter.calls == [(handlers[0].connection, claims)]
    callback_defaults = handlers[0].callbacks[0].__defaults__
    assert callback_defaults == (claims, assignment.peer_reservation_id)

    renegotiated = await service.offer(
        token=token,
        user_id=claims.user_id,
        request=PipecatOfferRequest(sdp="second-offer", pc_id=answer.pc_id),
    )
    assert renegotiated.pc_id == answer.pc_id
    patch = _patch(answer.pc_id)
    await service.patch(token=token, user_id=claims.user_id, request=patch)
    assert handlers[0].patch_requests == [patch]

    first = await service.delete(token=token, user_id=claims.user_id, pc_id=answer.pc_id)
    second = await service.delete(token=token, user_id=claims.user_id, pc_id=answer.pc_id)
    assert first == second
    assert first.reason.value == "user_ended"
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 1
    assert handlers[0].close_count == 1
    await service.aclose()
    assert handlers[0].close_count == 1


@pytest.mark.asyncio
async def test_one_time_creation_rejects_reuse_and_every_wrong_peer_operation() -> None:
    service, handlers, starter = _service()
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    with pytest.raises(PipecatSignalingConflict, match="ID is required"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    with pytest.raises(PipecatSignalingConflict, match="does not match"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=PipecatOfferRequest(sdp="offer", pc_id="different-peer"),
        )
    with pytest.raises(PipecatSignalingConflict, match="does not match"):
        await service.patch(
            token=token,
            user_id="firebase-user-1",
            request=_patch("different-peer"),
        )
    with pytest.raises(PipecatSignalingConflict, match="does not match"):
        await service.delete(token=token, user_id="firebase-user-1", pc_id="different-peer")

    assert handlers[0].initial_count == 1
    assert handlers[0].renegotiate_count == 0
    assert handlers[0].patch_requests == []
    assert len(starter.calls) == 1
    await service.delete(token=token, user_id="firebase-user-1", pc_id=answer.pc_id)


@pytest.mark.asyncio
async def test_concurrent_initial_offers_create_exactly_one_peer_runtime() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    handler = _Handler(enter=entered, release=release)
    starter = _RuntimeStarter()
    service, _, _ = _service(handlers=[handler], starter=starter)
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)

    first = asyncio.create_task(
        service.offer(token=token, user_id="firebase-user-1", request=_initial_offer())
    )
    await entered.wait()
    second = asyncio.create_task(
        service.offer(token=token, user_id="firebase-user-1", request=_initial_offer())
    )
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, PipecatSignalingConflict) for result in results) == 1
    assert handler.initial_count == 1
    assert len(starter.calls) == 1
    successful = next(result for result in results if not isinstance(result, BaseException))
    await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=successful.pc_id,
    )


@pytest.mark.asyncio
async def test_delete_during_initial_negotiation_wins_and_cannot_resurrect() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    handler = _Handler(enter=entered, release=release)
    service, _, _ = _service(handlers=[handler])
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    offer_task = asyncio.create_task(
        service.offer(token=token, user_id="firebase-user-1", request=_initial_offer())
    )
    await entered.wait()

    terminal = await service.delete(token=token, user_id="firebase-user-1", pc_id=None)
    with pytest.raises(PipecatSignalingConflict, match="cancelled"):
        await offer_task
    release.set()

    assert terminal.reason.value == "user_ended"
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.pc_id is None
    assert handler.close_count == 1
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_process_local_one_call_cap_releases_only_after_terminal_cleanup() -> None:
    service, handlers, _ = _service()
    first_assignment = await service.reserve(_claims())
    second_assignment = await service.reserve(
        _claims(call_id=OTHER_CALL_ID, trace_id=OTHER_TRACE_ID)
    )
    first_token = _token(first_assignment)
    second_token = _token(second_assignment)
    first_answer = await service.offer(
        token=first_token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    with pytest.raises(PipecatSignalingUnavailable, match="active-call capacity"):
        await service.offer(
            token=second_token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    second_status = await service.status(token=second_token, user_id="firebase-user-1")
    assert second_status.state is PipecatReservationState.RESERVED

    await service.delete(
        token=first_token,
        user_id="firebase-user-1",
        pc_id=first_answer.pc_id,
    )
    second_answer = await service.offer(
        token=second_token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )
    assert second_answer.pc_id == handlers[1].connection.pc_id
    await service.delete(
        token=second_token,
        user_id="firebase-user-1",
        pc_id=second_answer.pc_id,
    )


@pytest.mark.asyncio
async def test_cross_user_and_changed_authoritative_ownership_fail_closed() -> None:
    sessions = {SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID)}
    agents = {AGENT_ID: _Agent(AGENT_ID, "firebase-user-1")}
    service, _, _ = _service(sessions=sessions, agents=agents)
    assignment = await service.reserve(_claims())
    token = _token(assignment)

    with pytest.raises(PipecatSignalingForbidden):
        await service.offer(token=token, user_id="attacker", request=_initial_offer())

    sessions[SESSION_ID] = _Session(SESSION_ID, "different-owner", AGENT_ID)
    with pytest.raises(PipecatSignalingForbidden, match="Authoritative"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    assert service.active_call_count == 0
    sessions[SESSION_ID] = _Session(SESSION_ID, "firebase-user-1", AGENT_ID)
    await service.delete(token=token, user_id="firebase-user-1", pc_id=None)


@pytest.mark.asyncio
async def test_active_owner_change_revokes_exact_call_and_cleans_before_forbidden() -> None:
    sessions = {SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID)}
    agents = {AGENT_ID: _Agent(AGENT_ID, "firebase-user-1")}
    service, handlers, starter = _service(sessions=sessions, agents=agents)
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    sessions[SESSION_ID] = _Session(SESSION_ID, "different-owner", AGENT_ID)
    with pytest.raises(PipecatSignalingForbidden, match="Authoritative"):
        await service.patch(
            token=token,
            user_id="firebase-user-1",
            request=_patch(answer.pc_id),
        )
    with pytest.raises(PipecatSignalingForbidden, match="Authoritative"):
        await service.status(token=token, user_id="firebase-user-1")

    assert handlers[0].patch_requests == []
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 1
    assert handlers[0].close_count == 1

    sessions[SESSION_ID] = _Session(SESSION_ID, "firebase-user-1", AGENT_ID)
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "owner_mismatch"
    assert snapshot.terminal_result.retryable is False


@pytest.mark.asyncio
async def test_active_session_deletion_revokes_and_cleans_even_when_delete_returns_not_found() -> (
    None
):
    sessions = {SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID)}
    service, handlers, starter = _service(sessions=sessions)
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    del sessions[SESSION_ID]
    with pytest.raises(PipecatSignalingNotFound, match="Authoritative voice session"):
        await service.delete(
            token=token,
            user_id="firebase-user-1",
            pc_id=answer.pc_id,
        )

    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 1
    assert handlers[0].close_count == 1

    sessions[SESSION_ID] = _Session(SESSION_ID, "firebase-user-1", AGENT_ID)
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "owner_mismatch"
    assert snapshot.terminal_result.retryable is False


@pytest.mark.asyncio
async def test_repeated_owner_mismatch_retries_terminal_cleanup_until_capacity_releases() -> None:
    sessions = {SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID)}
    handler = _Handler(fail_close_count=1)
    starter = _RuntimeStarter(fail_close_count=1)
    service, _, _ = _service(
        handlers=[handler],
        starter=starter,
        sessions=sessions,
    )
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    sessions[SESSION_ID] = _Session(SESSION_ID, "different-owner", AGENT_ID)
    with pytest.raises(PipecatSignalingForbidden, match="Authoritative"):
        await service.status(token=token, user_id="firebase-user-1")
    terminal = service._reservations[assignment.peer_reservation_id].terminal_result
    assert terminal is not None
    assert terminal.reason.value == "owner_mismatch"
    assert service.active_call_count == 1
    assert starter.handles[0].close_count == 1
    assert handler.close_count == 1

    with pytest.raises(PipecatSignalingForbidden, match="Authoritative"):
        await service.status(token=token, user_id="firebase-user-1")
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 2
    assert handler.close_count == 2

    sessions[SESSION_ID] = _Session(SESSION_ID, "firebase-user-1", AGENT_ID)
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.terminal_result == terminal


@pytest.mark.asyncio
async def test_wrong_immutable_claimant_cannot_revoke_or_cleanup_an_active_call() -> None:
    service, handlers, starter = _service()
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    with pytest.raises(PipecatSignalingForbidden, match="Forbidden"):
        await service.delete(token=token, user_id="attacker", pc_id=answer.pc_id)

    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.ACTIVE
    assert snapshot.terminal_result is None
    assert service.active_call_count == 1
    assert starter.handles[0].close_count == 0
    assert handlers[0].close_count == 0

    await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )


@pytest.mark.asyncio
async def test_repository_unavailability_does_not_tombstone_or_cleanup_active_call() -> None:
    sessions = {SESSION_ID: _Session(SESSION_ID, "firebase-user-1", AGENT_ID)}
    service, handlers, starter = _service(sessions=sessions)
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    healthy_repo = service._session_repo
    service._session_repo = _UnavailableRepo()  # type: ignore[assignment]
    with pytest.raises(PipecatSignalingUnavailable, match="repository lookup failed"):
        await service.patch(
            token=token,
            user_id="firebase-user-1",
            request=_patch(answer.pc_id),
        )

    record = service._reservations[assignment.peer_reservation_id]
    assert record.state is PipecatReservationState.ACTIVE
    assert record.terminal_result is None
    assert service.active_call_count == 1
    assert starter.handles[0].close_count == 0
    assert handlers[0].close_count == 0
    assert handlers[0].patch_requests == []

    service._session_repo = healthy_repo
    await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )


@pytest.mark.asyncio
async def test_swallowed_runtime_callback_failure_is_a_terminal_side_channel_error() -> None:
    starter = _RuntimeStarter(failure=RuntimeError("pipeline failed"))
    service, handlers, _ = _service(starter=starter)
    assignment = await service.reserve(_claims())
    token = _token(assignment)

    with pytest.raises(PipecatSignalingUnavailable, match="runtime failed to start"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )

    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "runtime_unavailable"
    assert handlers[0].close_count == 1
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_signaling_timeout_and_caller_cancellation_both_tombstone_and_cleanup() -> None:
    timeout_handler = _HangingHandler()
    timeout_service, _, _ = _service(
        handlers=[timeout_handler],
        settings=_settings(signaling_timeout_seconds=0.01),
    )
    timeout_service._handler_factory = lambda: timeout_handler
    timeout_assignment = await timeout_service.reserve(_claims())
    timeout_token = _token(timeout_assignment)
    with pytest.raises(PipecatSignalingUnavailable, match="negotiation failed"):
        await timeout_service.offer(
            token=timeout_token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    assert timeout_handler.close_count == 1
    assert (
        await timeout_service.status(token=timeout_token, user_id="firebase-user-1")
    ).state is PipecatReservationState.TERMINAL

    entered = asyncio.Event()
    release = asyncio.Event()
    cancel_handler = _Handler(enter=entered, release=release)
    cancel_service, _, _ = _service(
        handlers=[cancel_handler],
        tokens=["D" * 64],
    )
    cancel_service._handler_factory = lambda: cancel_handler
    cancel_assignment = await cancel_service.reserve(_claims())
    cancel_token = _token(cancel_assignment)
    task = asyncio.create_task(
        cancel_service.offer(
            token=cancel_token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_handler.close_count == 1
    assert (
        await cancel_service.status(token=cancel_token, user_id="firebase-user-1")
    ).state is PipecatReservationState.TERMINAL


@pytest.mark.asyncio
async def test_expired_reservation_is_tombstoned_and_cannot_resurrect_same_call() -> None:
    now = [FIXED_NOW]
    service, handlers, _ = _service(clock=lambda: now[0])
    claims = _claims()
    assignment = await service.reserve(claims)
    token = _token(assignment)
    now[0] = claims.expires_at

    with pytest.raises(PipecatSignalingConflict, match="expired"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "assignment_expired"
    assert handlers[0].initial_count == 0
    assert handlers[0].close_count == 1
    with pytest.raises(PipecatSignalingConflict):
        await service.reserve(claims)


@pytest.mark.asyncio
async def test_offer_that_finishes_after_expiry_never_publishes_active() -> None:
    now = [FIXED_NOW]
    handler = _AdvanceClockHandler(lambda: now.__setitem__(0, FIXED_NOW + timedelta(seconds=60)))
    service, _, starter = _service(handlers=[handler], clock=lambda: now[0])
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)

    with pytest.raises(PipecatSignalingConflict, match="expired"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )

    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "assignment_expired"
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 1
    assert handler.close_count == 1


@pytest.mark.asyncio
async def test_peer_close_is_terminal_and_cannot_release_a_different_peer() -> None:
    service, handlers, starter = _service()
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    await _Connection("different-peer").emit("closed")
    assert service.active_call_count == 1
    await handlers[0].connection.emit("closed")
    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.pc_id == answer.pc_id
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "client_disconnected"
    assert starter.handles[0].close_count == 1
    assert handlers[0].close_count == 1


@pytest.mark.asyncio
async def test_peer_close_before_pc_id_publication_cannot_resurrect_active_state() -> None:
    starter = _RuntimeStarter(close_during_start=True)
    service, handlers, _ = _service(starter=starter)
    assignment = await service.reserve(_claims())
    token = _token(assignment)

    with pytest.raises(PipecatSignalingUnavailable, match="closed before negotiation"):
        await service.offer(
            token=token,
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    await asyncio.gather(*starter.peer_close_tasks)

    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "client_disconnected"
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 1
    assert handlers[0].close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion_failure", "expected_reason"),
    [
        (None, "runtime_stopped"),
        (RuntimeError("provider stream failed"), "runtime_unavailable"),
    ],
)
async def test_runtime_completion_tombstones_even_while_peer_remains_connected(
    completion_failure: BaseException | None,
    expected_reason: str,
) -> None:
    starter = _RuntimeStarter(completion_failure=completion_failure)
    service, handlers, _ = _service(starter=starter)
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    for _ in range(20):
        snapshot = await service.status(token=token, user_id="firebase-user-1")
        if snapshot.state is PipecatReservationState.TERMINAL:
            break
        await asyncio.sleep(0)

    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == expected_reason
    assert handlers[0].close_count == 1
    assert starter.handles[0].close_count == 1
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_runtime_completion_retries_cleanup_and_admits_the_next_call() -> None:
    first_handler = _Handler(fail_close_count=1)
    second_handler = _Handler(pc_id="SmallWebRTCConnection#2-peer")
    handler_values = iter((first_handler, second_handler))
    starter = _RuntimeStarter(fail_close_count=1)
    service, _, _ = _service(
        starter=starter,
        settings=_settings(
            terminal_cleanup_retry_initial_seconds=0.001,
            terminal_cleanup_retry_max_seconds=0.002,
            terminal_cleanup_retry_horizon_seconds=0.05,
            terminal_cleanup_retry_max_attempts=3,
        ),
    )
    service._handler_factory = lambda: next(handler_values)
    first_assignment = await service.reserve(_claims())
    await service.offer(
        token=_token(first_assignment),
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    for _ in range(100):
        if service.active_call_count == 0:
            break
        await asyncio.sleep(0.001)

    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 2
    assert first_handler.close_count == 2
    first_record = service._reservations[first_assignment.peer_reservation_id]
    assert first_record.cleanup_retry_task is None

    second_assignment = await service.reserve(
        _claims(call_id=OTHER_CALL_ID, trace_id=OTHER_TRACE_ID)
    )
    second_answer = await service.offer(
        token=_token(second_assignment),
        user_id="firebase-user-1",
        request=_initial_offer(),
    )
    assert second_answer.pc_id == second_handler.connection.pc_id
    await service.delete(
        token=_token(second_assignment),
        user_id="firebase-user-1",
        pc_id=second_answer.pc_id,
    )
    await service.aclose()


@pytest.mark.asyncio
async def test_persistent_cleanup_failure_has_bounded_retries_and_retains_capacity() -> None:
    first_handler = _Handler(fail_close_count=100)
    second_handler = _Handler(pc_id="SmallWebRTCConnection#2-peer")
    handler_values = iter((first_handler, second_handler))
    starter = _RuntimeStarter(fail_close_count=100)
    service, _, _ = _service(
        starter=starter,
        settings=_settings(
            terminal_cleanup_retry_initial_seconds=0.001,
            terminal_cleanup_retry_max_seconds=0.002,
            terminal_cleanup_retry_horizon_seconds=0.05,
            terminal_cleanup_retry_max_attempts=3,
        ),
    )
    service._handler_factory = lambda: next(handler_values)
    first_assignment = await service.reserve(_claims())
    first_token = _token(first_assignment)
    await service.offer(
        token=first_token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    first_record = service._reservations[first_assignment.peer_reservation_id]
    for _ in range(100):
        if (
            first_record.state is PipecatReservationState.TERMINAL
            and first_record.cleanup_retry_task is None
        ):
            break
        await service.status(token=first_token, user_id="firebase-user-1")
        await asyncio.sleep(0.001)

    assert first_record.state is PipecatReservationState.TERMINAL
    assert first_record.cleanup_retry_task is None
    assert starter.handles[0].close_count == 4
    assert first_handler.close_count == 4
    assert service.active_call_count == 1
    await asyncio.sleep(0.01)
    assert starter.handles[0].close_count == 4
    assert first_handler.close_count == 4

    second_assignment = await service.reserve(
        _claims(call_id=OTHER_CALL_ID, trace_id=OTHER_TRACE_ID)
    )
    with pytest.raises(PipecatSignalingUnavailable, match="active-call capacity"):
        await service.offer(
            token=_token(second_assignment),
            user_id="firebase-user-1",
            request=_initial_offer(),
        )

    starter.handles[0].fail_close_count = starter.handles[0].close_count
    first_handler.fail_close_count = first_handler.close_count
    await service.aclose()
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_cleanup_retry_horizon_bounds_a_hung_attempt_and_retains_capacity() -> None:
    handler = _CountingHangingCloseHandler()
    service, _, starter = _service(
        handlers=[handler],
        settings=_settings(
            cleanup_timeout_seconds=0.01,
            terminal_cleanup_retry_initial_seconds=0.001,
            terminal_cleanup_retry_max_seconds=0.002,
            terminal_cleanup_retry_horizon_seconds=0.03,
            terminal_cleanup_retry_max_attempts=10,
        ),
    )
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    await service.offer(
        token=_token(assignment),
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    record = service._reservations[assignment.peer_reservation_id]
    saw_retry_task = False
    for _ in range(100):
        retry_task = record.cleanup_retry_task
        saw_retry_task = saw_retry_task or retry_task is not None
        if (
            record.state is PipecatReservationState.TERMINAL
            and handler.close_count >= 1
            and saw_retry_task
            and retry_task is None
        ):
            break
        await asyncio.sleep(0.001)

    assert saw_retry_task is True
    assert record.cleanup_retry_task is None
    assert 2 <= handler.close_count <= 4
    assert service.active_call_count == 1
    await service.aclose()
    assert record.cleanup_retry_task is None


@pytest.mark.asyncio
async def test_service_close_cancels_and_awaits_terminal_cleanup_retry_task() -> None:
    handler = _Handler(fail_close_count=100)
    starter = _RuntimeStarter(fail_close_count=100)
    service, _, _ = _service(
        handlers=[handler],
        starter=starter,
        settings=_settings(
            terminal_cleanup_retry_initial_seconds=1.0,
            terminal_cleanup_retry_max_seconds=1.0,
            terminal_cleanup_retry_horizon_seconds=5.0,
            terminal_cleanup_retry_max_attempts=3,
        ),
    )
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    await service.offer(
        token=_token(assignment),
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    record = service._reservations[assignment.peer_reservation_id]
    for _ in range(100):
        if record.cleanup_retry_task is not None:
            break
        await asyncio.sleep(0)
    retry_task = record.cleanup_retry_task
    assert retry_task is not None
    assert not retry_task.done()

    await service.aclose()
    counts_after_close = (starter.handles[0].close_count, handler.close_count)
    assert record.cleanup_retry_task is None
    assert retry_task.done()
    await asyncio.sleep(0.01)
    assert (starter.handles[0].close_count, handler.close_count) == counts_after_close

    starter.handles[0].fail_close_count = starter.handles[0].close_count
    handler.fail_close_count = handler.close_count
    await service.aclose()
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_delete_racing_runtime_completion_keeps_one_terminal_result_and_cleanup() -> None:
    starter = _RuntimeStarter()
    service, handlers, _ = _service(starter=starter)
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    starter.handles[0].complete()
    deleted = await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )
    await asyncio.sleep(0)

    snapshot = await service.status(token=token, user_id="firebase-user-1")
    assert snapshot.terminal_result == deleted
    assert handlers[0].close_count == 1
    assert starter.handles[0].close_count == 1


@pytest.mark.asyncio
async def test_terminal_delete_retries_failed_cleanup_before_releasing_call_capacity() -> None:
    handler = _Handler(fail_close_count=1)
    starter = _RuntimeStarter(fail_close_count=1)
    service, _, _ = _service(handlers=[handler], starter=starter)
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    terminal = await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )
    assert terminal.reason.value == "user_ended"
    assert service.active_call_count == 1
    assert starter.handles[0].close_count == 1
    assert handler.close_count == 1

    retry = await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )
    assert retry == terminal
    assert service.active_call_count == 0
    assert starter.handles[0].close_count == 2
    assert handler.close_count == 2


@pytest.mark.asyncio
async def test_caller_cancellation_during_cleanup_is_propagated_and_delete_can_retry() -> None:
    handler = _HangingCloseHandler()
    service, _, _ = _service(handlers=[handler])
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    answer = await service.offer(
        token=token,
        user_id="firebase-user-1",
        request=_initial_offer(),
    )

    delete_task = asyncio.create_task(
        service.delete(token=token, user_id="firebase-user-1", pc_id=answer.pc_id)
    )
    await handler.close_entered.wait()
    delete_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await delete_task
    assert service.active_call_count == 1

    handler.allow_close.set()
    terminal = await service.delete(
        token=token,
        user_id="firebase-user-1",
        pc_id=answer.pc_id,
    )
    assert terminal.reason.value == "user_ended"
    assert handler.close_count == 2
    assert service.active_call_count == 0


@pytest.mark.asyncio
async def test_reservation_expiry_task_tombstones_without_a_followup_request() -> None:
    now = [FIXED_NOW]

    async def advance_clock(delay: float) -> None:
        now[0] += timedelta(seconds=delay)
        await asyncio.sleep(0)

    service, handlers, _ = _service(clock=lambda: now[0])
    service._sleep = advance_clock
    assignment = await service.reserve(_claims())
    token = _token(assignment)
    for _ in range(10):
        snapshot = await service.status(token=token, user_id="firebase-user-1")
        if snapshot.state is PipecatReservationState.TERMINAL:
            break
        await asyncio.sleep(0)

    assert snapshot.state is PipecatReservationState.TERMINAL
    assert snapshot.terminal_result is not None
    assert snapshot.terminal_result.reason.value == "assignment_expired"
    assert handlers[0].close_count == 1


@pytest.mark.asyncio
async def test_reserved_delete_and_service_close_are_bounded_idempotent_cleanup() -> None:
    service, handlers, _ = _service()
    first_assignment = await service.reserve(_claims())
    first_token = _token(first_assignment)
    first = await service.delete(token=first_token, user_id="firebase-user-1", pc_id=None)
    second = await service.delete(token=first_token, user_id="firebase-user-1", pc_id=None)
    assert first == second
    assert handlers[0].close_count == 1

    second_assignment = await service.reserve(
        _claims(call_id=OTHER_CALL_ID, trace_id=OTHER_TRACE_ID)
    )
    second_token = _token(second_assignment)
    await service.aclose()
    await service.aclose()
    assert handlers[1].close_count == 1
    assert (
        await service.status(token=second_token, user_id="firebase-user-1")
    ).state is PipecatReservationState.TERMINAL


@pytest.mark.asyncio
async def test_service_close_cancels_a_hung_initial_offer_before_waiting_for_its_lock() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    handler = _Handler(enter=entered, release=release)
    service, _, _ = _service(handlers=[handler])
    service._handler_factory = lambda: handler
    assignment = await service.reserve(_claims())
    offer_task = asyncio.create_task(
        service.offer(
            token=_token(assignment),
            user_id="firebase-user-1",
            request=_initial_offer(),
        )
    )
    await entered.wait()

    await asyncio.wait_for(service.aclose(), timeout=0.5)
    with pytest.raises(PipecatSignalingConflict, match="cancelled"):
        await offer_task
    assert handler.close_count == 1
    assert service.active_call_count == 0


def test_request_contracts_reject_restart_mutable_candidates_and_malformed_peer_ids() -> None:
    with pytest.raises(ValueError, match="restart is unsupported"):
        PipecatOfferRequest(sdp="offer", restart_pc=True)
    with pytest.raises(ValueError, match="immutable tuple"):
        PipecatPatchRequest(pc_id="peer", candidates=[])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="connection ID is invalid"):
        PipecatOfferRequest(sdp="offer", pc_id=" bad-peer ")
    with pytest.raises(ValueError, match="candidate count"):
        PipecatPatchRequest(pc_id="peer", candidates=())


@pytest.mark.parametrize(
    "overrides",
    [
        {"terminal_cleanup_retry_initial_seconds": 0},
        {
            "terminal_cleanup_retry_initial_seconds": 2,
            "terminal_cleanup_retry_max_seconds": 1,
        },
        {"terminal_cleanup_retry_horizon_seconds": 301},
        {"terminal_cleanup_retry_max_attempts": 0},
        {"terminal_cleanup_retry_max_attempts": 21},
    ],
)
def test_terminal_cleanup_retry_settings_are_strictly_bounded(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="terminal cleanup retry"):
        _settings(**overrides)


def test_cors_contract_is_explicit_bearer_only_and_has_no_wildcards() -> None:
    contract = PipecatCorsContract(
        allowed_origins=("https://app.example.test", "http://localhost:3000"),
    )
    assert contract.allows("https://app.example.test") is True
    assert contract.allows("https://attacker.example") is False
    assert contract.allowed_methods == ("OPTIONS", "POST", "PATCH", "DELETE")
    assert contract.allowed_headers == ("authorization", "content-type")
    assert contract.allow_credentials is False
    with pytest.raises(ValueError, match="wildcard"):
        PipecatCorsContract(allowed_origins=("*",))
    with pytest.raises(ValueError, match=r"HTTP\(S\) origin"):
        PipecatCorsContract(allowed_origins=("https://app.example.test/path",))


@dataclass(frozen=True)
class _SdkOffer:
    sdp: str
    type: str
    pc_id: str | None
    restart_pc: bool
    request_data: object | None


@dataclass(frozen=True)
class _SdkCandidate:
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


@dataclass(frozen=True)
class _SdkPatch:
    pc_id: str
    candidates: list[_SdkCandidate]


class _RawSdkHandler:
    def __init__(self) -> None:
        self.web_request: object | None = None
        self.patch_request: object | None = None
        self.closed = False

    async def handle_web_request(self, request: object, callback: Any):
        del callback
        self.web_request = request
        return {"sdp": "answer", "type": "answer", "pc_id": "peer"}

    async def handle_patch_request(self, request: object) -> None:
        self.patch_request = request

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_handler_adapter_projects_neutral_dtos_to_concrete_sdk_request_types() -> None:
    raw_handler = _RawSdkHandler()
    adapter = PipecatPeerHandlerAdapter(
        raw_handler,
        request_types=PipecatHandlerRequestTypes(
            web_request=_SdkOffer,
            patch_request=_SdkPatch,
            ice_candidate=_SdkCandidate,
        ),
    )
    offer = PipecatOfferRequest(sdp="offer")
    patch = _patch("peer")

    await adapter.handle_web_request(offer, lambda connection: asyncio.sleep(0))
    await adapter.handle_patch_request(patch)
    await adapter.close()

    assert isinstance(raw_handler.web_request, _SdkOffer)
    assert raw_handler.web_request == _SdkOffer("offer", "offer", None, False, None)
    assert isinstance(raw_handler.patch_request, _SdkPatch)
    assert raw_handler.patch_request == _SdkPatch(
        pc_id="peer",
        candidates=[
            _SdkCandidate(
                candidate="candidate:1 1 UDP 1 127.0.0.1 40000 typ host",
                sdp_mid="0",
                sdp_mline_index=0,
            )
        ],
    )
    assert raw_handler.closed is True
    assert not hasattr(adapter, "handler")

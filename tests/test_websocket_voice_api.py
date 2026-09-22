"""ASGI acceptance for the authenticated provider-free voice WebSocket."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from murmur.api.application import create_application
from murmur.api.dependencies import get_authenticated_user
from murmur.api.routers.websocket_voice import _run_provider_free_canary
from murmur.persistence.repositories.identities import AgentRepo, UserRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.websocket_protocol import (
    INPUT_FRAME_PCM_BYTES,
    WEBSOCKET_CANARY_MODE,
    WEBSOCKET_TICKET_PROTOCOL_PREFIX,
    WEBSOCKET_VOICE_PROTOCOL,
    VoiceBinaryFrame,
    VoiceFrameKind,
    decode_voice_binary_frame,
    encode_voice_binary_frame,
)
from murmur.voice.websocket_ticket import (
    WebSocketVoiceBootstrapService,
    WebSocketVoiceConnection,
    WebSocketVoiceScope,
    WebSocketVoiceSettings,
    WebSocketVoiceTicketRegistry,
)
from starlette.websockets import WebSocketDisconnect

TEST_ORIGIN = "https://murmur.example"


@dataclass(frozen=True)
class _Harness:
    client: TestClient
    user: dict[str, str]
    session_id: str
    agent_id: str
    service: WebSocketVoiceBootstrapService


@pytest.fixture
def websocket_harness() -> Iterator[_Harness]:
    user = {
        "id": "10000000-0000-4000-8000-000000000001",
        "email": "voice@example.com",
        "name": "Voice Owner",
    }
    UserRepo.get_or_create(uid=user["id"], email=user["email"], name=user["name"])
    agent = AgentRepo.create(user_id=user["id"], name="Tutor", system_prompt="Teach")
    session = SessionRepo.create(user["id"], agent.id)
    settings = WebSocketVoiceSettings(
        allowed_origins=(TEST_ORIGIN,),
        ticket_ttl_seconds=15,
        heartbeat_seconds=1,
    )
    service = WebSocketVoiceBootstrapService(WebSocketVoiceTicketRegistry(settings))
    app = create_application(websocket_voice_service=service)
    app.dependency_overrides[get_authenticated_user] = lambda: user
    with TestClient(app) as client:
        yield _Harness(client, user, session.id, agent.id, service)


def _bootstrap(harness: _Harness, *, session_id: str | None = None) -> dict:
    response = harness.client.post(
        "/api/voice/websocket/session",
        json={
            "session_id": session_id or harness.session_id,
            "voice_call_id": "20000000-0000-4000-8000-000000000002",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _subprotocols(ticket: str) -> list[str]:
    return [WEBSOCKET_VOICE_PROTOCOL, WEBSOCKET_TICKET_PROTOCOL_PREFIX + ticket]


def _canary_service_and_connection(
    *, send_timeout_seconds: float = 0.01
) -> tuple[WebSocketVoiceBootstrapService, WebSocketVoiceConnection]:
    settings = WebSocketVoiceSettings(
        allowed_origins=(TEST_ORIGIN,),
        heartbeat_seconds=1,
        max_session_seconds=30,
        send_timeout_seconds=send_timeout_seconds,
    )
    service = WebSocketVoiceBootstrapService(WebSocketVoiceTicketRegistry(settings))
    connection = WebSocketVoiceConnection(
        connection_id="70000000-0000-4000-8000-000000000007",
        scope=WebSocketVoiceScope(
            user_id="user-1",
            session_id="80000000-0000-4000-8000-000000000008",
            agent_id="90000000-0000-4000-8000-000000000009",
            voice_call_id="a0000000-0000-4000-8000-00000000000a",
        ),
        trace_id="b0000000-0000-4000-8000-00000000000b",
        profile_id="murmur-direct-cascade-v1",
        event_protocol=WEBSOCKET_VOICE_PROTOCOL,
        accepted_at=datetime.now(UTC),
        release_requested=asyncio.Event(),
    )
    return service, connection


class _StalledSendSocket:
    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.send_cancelled = asyncio.Event()

    async def send_json(self, _message: object) -> None:
        self.send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.send_cancelled.set()
            raise


class _BlockingReceiveSocket:
    def __init__(self) -> None:
        self.receive_started = asyncio.Event()
        self.receive_cancelled = asyncio.Event()
        self.sent: list[object] = []

    async def send_json(self, message: object) -> None:
        self.sent.append(message)

    async def receive(self) -> dict[str, object]:
        self.receive_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receive_cancelled.set()
            raise


class _UnexpectedConsumeFailure:
    def __init__(self) -> None:
        self.settings = WebSocketVoiceSettings(allowed_origins=(TEST_ORIGIN,))

    async def consume(self, _ticket: str) -> WebSocketVoiceConnection:
        raise RuntimeError("unexpected consume failure")

    async def disconnect(self, _connection: WebSocketVoiceConnection) -> None: ...

    async def aclose(self) -> None: ...


def test_authenticated_canary_is_bidirectional_generation_fenced_and_cleaned(
    websocket_harness: _Harness,
) -> None:
    assignment = _bootstrap(websocket_harness)
    assert assignment["runtime"] == "websocket_v1"
    assert assignment["session_id"] == websocket_harness.session_id
    assert assignment["agent_id"] == websocket_harness.agent_id
    UUID(assignment["trace_id"], version=4)

    with websocket_harness.client.websocket_connect(
        assignment["websocket_path"],
        subprotocols=_subprotocols(assignment["ticket"]),
        headers={"origin": TEST_ORIGIN},
    ) as socket:
        assert socket.accepted_subprotocol == WEBSOCKET_VOICE_PROTOCOL
        ready = socket.receive_json()
        assert ready["type"] == "canary_ready"
        assert ready["runtime_mode"] == WEBSOCKET_CANARY_MODE
        assert ready["generation"] == 0
        assert ready["trace_id"] == assignment["trace_id"]
        assert ready["input_sample_rate_hz"] == 16_000
        assert ready["output_sample_rate_hz"] == 16_000

        socket.send_json({"type": "ping", "sequence": 7})
        pong = socket.receive_json()
        assert pong["type"] == "pong"
        assert pong["client_sequence"] == 7

        input_frame = VoiceBinaryFrame(
            kind=VoiceFrameKind.INPUT_PCM,
            generation=0,
            sequence=0,
            payload=b"\x01\x02" * (INPUT_FRAME_PCM_BYTES // 2),
        )
        socket.send_bytes(encode_voice_binary_frame(input_frame))
        output_frame = decode_voice_binary_frame(socket.receive_bytes())
        assert output_frame.kind is VoiceFrameKind.OUTPUT_PCM
        assert output_frame.payload == input_frame.payload

        socket.send_json({"type": "interrupt"})
        clear = socket.receive_json()
        assert clear["type"] == "clear_audio"
        assert clear["generation"] == 1

        socket.send_json({"type": "close"})
        assert socket.receive_json()["type"] == "closing"

    assert websocket_harness.service.registry.counts == (0, 0)


@pytest.mark.asyncio
async def test_stalled_outbound_send_is_cancelled_at_the_deadline() -> None:
    service, connection = _canary_service_and_connection()
    socket = _StalledSendSocket()

    with pytest.raises(TimeoutError, match="send timed out"):
        await _run_provider_free_canary(socket, service, connection)  # type: ignore[arg-type]

    assert socket.send_started.is_set()
    assert socket.send_cancelled.is_set()


@pytest.mark.asyncio
async def test_handler_cancellation_joins_the_inflight_receive() -> None:
    service, connection = _canary_service_and_connection(send_timeout_seconds=0.1)
    socket = _BlockingReceiveSocket()
    run = asyncio.create_task(
        _run_provider_free_canary(socket, service, connection),  # type: ignore[arg-type]
    )
    await asyncio.wait_for(socket.receive_started.wait(), timeout=1)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert socket.receive_cancelled.is_set()
    assert socket.sent[0]["type"] == "canary_ready"  # type: ignore[index]


def test_unexpected_preaccept_failure_explicitly_denies_the_upgrade() -> None:
    app = create_application(websocket_voice_service=_UnexpectedConsumeFailure())

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                "/api/voice/websocket",
                subprotocols=_subprotocols("A" * 43),
                headers={"origin": TEST_ORIGIN},
            ):
                pass

    assert denied.value.code == 1011


def test_wrong_origin_does_not_consume_ticket_and_ticket_cannot_be_replayed(
    websocket_harness: _Harness,
) -> None:
    assignment = _bootstrap(websocket_harness)
    protocols = _subprotocols(assignment["ticket"])
    with pytest.raises(WebSocketDisconnect):
        with websocket_harness.client.websocket_connect(
            assignment["websocket_path"],
            subprotocols=protocols,
            headers={"origin": "https://attacker.example"},
        ):
            pass

    with websocket_harness.client.websocket_connect(
        assignment["websocket_path"],
        subprotocols=protocols,
        headers={"origin": TEST_ORIGIN},
    ) as socket:
        assert socket.receive_json()["type"] == "canary_ready"
        socket.send_json({"type": "close"})
        assert socket.receive_json()["type"] == "closing"

    with pytest.raises(WebSocketDisconnect):
        with websocket_harness.client.websocket_connect(
            assignment["websocket_path"],
            subprotocols=protocols,
            headers={"origin": TEST_ORIGIN},
        ):
            pass


def test_stale_generation_and_malformed_control_close_visibly(
    websocket_harness: _Harness,
) -> None:
    assignment = _bootstrap(websocket_harness)
    with websocket_harness.client.websocket_connect(
        assignment["websocket_path"],
        subprotocols=_subprotocols(assignment["ticket"]),
        headers={"origin": TEST_ORIGIN},
    ) as socket:
        assert socket.receive_json()["type"] == "canary_ready"
        socket.send_json({"type": "interrupt"})
        assert socket.receive_json()["generation"] == 1
        socket.send_bytes(
            encode_voice_binary_frame(
                VoiceBinaryFrame(
                    kind=VoiceFrameKind.INPUT_PCM,
                    generation=0,
                    sequence=0,
                    payload=b"\x00\x00" * (INPUT_FRAME_PCM_BYTES // 2),
                )
            )
        )
        assert socket.receive_json() == {"type": "error", "code": "binary_frame_invalid"}
        assert socket.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}


def test_http_release_closes_the_exact_active_socket_and_is_idempotent(
    websocket_harness: _Harness,
) -> None:
    assignment = _bootstrap(websocket_harness)
    payload = {
        "session_id": websocket_harness.session_id,
        "voice_call_id": assignment["voice_call_id"],
    }
    with websocket_harness.client.websocket_connect(
        assignment["websocket_path"],
        subprotocols=_subprotocols(assignment["ticket"]),
        headers={"origin": TEST_ORIGIN},
    ) as socket:
        assert socket.receive_json()["type"] == "canary_ready"
        first = websocket_harness.client.post(
            "/api/voice/websocket/session/end",
            json=payload,
        )
        assert first.status_code == 204
        assert socket.receive_json()["type"] == "session_released"
        assert socket.receive() == {"type": "websocket.close", "code": 1000, "reason": ""}

    retry = websocket_harness.client.post(
        "/api/voice/websocket/session/end",
        json=payload,
    )
    assert retry.status_code == 204
    assert websocket_harness.service.registry.counts == (0, 0)
    assert websocket_harness.service.registry.release_intent_count == 1


def test_bootstrap_enforces_persistent_session_ownership(websocket_harness: _Harness) -> None:
    missing = websocket_harness.client.post(
        "/api/voice/websocket/session",
        json={
            "session_id": "30000000-0000-4000-8000-000000000003",
            "voice_call_id": "40000000-0000-4000-8000-000000000004",
        },
    )
    assert missing.status_code == 404
    assert missing.json() == {"error": "Voice session was not found"}

    other_user = UserRepo.get_or_create(
        uid="50000000-0000-4000-8000-000000000005",
        email="other@example.com",
        name="Other",
    )
    other_agent = AgentRepo.create(user_id=other_user.id, name="Other", system_prompt="Other")
    other_session = SessionRepo.create(other_user.id, other_agent.id)
    forbidden = websocket_harness.client.post(
        "/api/voice/websocket/session",
        json={
            "session_id": other_session.id,
            "voice_call_id": "60000000-0000-4000-8000-000000000006",
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.json() == {"error": "Forbidden"}

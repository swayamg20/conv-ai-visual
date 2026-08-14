"""Adversarial HTTP contract tests for the dedicated Pipecat ASGI surface."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from loguru import logger as loguru_logger
from murmur.api import pipecat_application
from murmur.api.pipecat_application import (
    FirebasePipecatAuthenticator,
    PipecatApplicationShutdownError,
    PipecatApplicationStartupError,
    PipecatAuthenticationUnavailable,
    create_pipecat_application,
)
from murmur.persistence.repositories.identities import UserRepo
from murmur.voice.pipecat_bootstrap import (
    PipecatBootstrapConflict,
    PipecatBootstrapForbidden,
    PipecatBootstrapNotFound,
    PipecatBootstrapUnavailable,
)
from murmur.voice.pipecat_composition import (
    PipecatApplicationComposition,
    PipecatCompositionSettings,
    PipecatCompositionUnavailable,
)
from murmur.voice.pipecat_signaling import (
    PipecatCorsContract,
    PipecatIceCandidate,
    PipecatOfferAnswer,
    PipecatOfferRequest,
    PipecatPatchRequest,
    PipecatSignalingConflict,
    PipecatSignalingForbidden,
    PipecatSignalingNotFound,
    PipecatSignalingUnavailable,
)
from murmur.voice.runtime_projection import (
    PipecatBrowserIceServer,
    PipecatBrowserVoiceAssignment,
)
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

USER_ID = "firebase-user-http-1"
SESSION_ID = "10000000-0000-4000-8000-000000000001"
AGENT_ID = "20000000-0000-4000-8000-000000000002"
CALL_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"
TOKEN = "opaque-http-token-" + ("a" * 48)
PC_ID = "SmallWebRTCConnection#1-peer"
ALLOWED_ORIGIN = "https://murmur.example.test"
AUTHORIZATION = "Bearer browser-id-token"
SECRET_BEARER = "bearer-that-must-never-be-reflected-or-logged"
SECRET_INPUT = "request-secret-that-must-never-be-reflected-or-logged"


def _browser_assignment() -> PipecatBrowserVoiceAssignment:
    return PipecatBrowserVoiceAssignment(
        profile_id="pipecat-direct-cascade-v1",
        expires_at=datetime(2099, 8, 14, 1, 0, tzinfo=UTC),
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        voice_call_id=CALL_ID,
        trace_id=TRACE_ID,
        webrtc_url=("https://voice.example.test/api/voice/pipecat/signal/" + TOKEN),
        peer_reservation_id="peer-reservation-http-1",
        ice_servers=(
            PipecatBrowserIceServer(urls=("stun:stun.example.test:3478",)),
            PipecatBrowserIceServer(
                urls=("turns:turn.example.test:5349?transport=tcp",),
                username="turn-user",
                credential="turn-secret",
            ),
        ),
    )


class _FakeComposition:
    def __init__(self) -> None:
        self.cors = PipecatCorsContract((ALLOWED_ORIGIN,))
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failures: dict[str, BaseException] = {}
        self.assignment = _browser_assignment()
        self.close_entered = asyncio.Event()
        self.close_gate: asyncio.Event | None = None

    def _fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    async def bootstrap_browser_assignment(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatBrowserVoiceAssignment:
        values = {
            "user_id": user_id,
            "session_id": session_id,
            "voice_call_id": voice_call_id,
        }
        self.calls.append(("bootstrap", values))
        self._fail("bootstrap")
        return self.assignment

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        values = {
            "user_id": user_id,
            "session_id": session_id,
            "voice_call_id": voice_call_id,
        }
        self.calls.append(("release", values))
        self._fail("release")

    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        self.calls.append(("offer", {"token": token, "user_id": user_id, "request": request}))
        self._fail("offer")
        return PipecatOfferAnswer(sdp="answer-sdp", type="answer", pc_id=PC_ID)

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None:
        self.calls.append(("patch", {"token": token, "user_id": user_id, "request": request}))
        self._fail("patch")

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> None:
        self.calls.append(("delete", {"token": token, "user_id": user_id, "pc_id": pc_id}))
        self._fail("delete")

    async def aclose(self) -> None:
        self.calls.append(("close", {}))
        self.close_entered.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        self._fail("close")


class _Authenticator:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def __call__(self, request: Request) -> Mapping[str, object] | None:
        self.requests.append(request)
        if request.headers.get("authorization") == AUTHORIZATION:
            return {"id": USER_ID, "email": "ignored@example.test"}
        return None


class _InlineRunner:
    async def run(
        self,
        function: Any,
        *args: object,
        timeout_seconds: float,
    ) -> object:
        del timeout_seconds
        return function(*args)


class _ClosableAuthenticator(_Authenticator):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0
        self.close_entered = asyncio.Event()
        self.close_gate: asyncio.Event | None = None
        self.close_error: BaseException | None = None

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        if self.close_error is not None:
            raise self.close_error


class _CloseBootstrap:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.close_entered = asyncio.Event()
        self.close_gate = asyncio.Event()

    async def aclose(self) -> None:
        self.events.append("bootstrap-close")
        self.close_entered.set()
        await self.close_gate.wait()


class _CloseSignaling:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.close_entered = asyncio.Event()

    async def aclose(self) -> None:
        self.events.append("signaling-close")
        self.close_entered.set()


def _application(
    composition: _FakeComposition | None = None,
    authenticator: _Authenticator | None = None,
) -> tuple[FastAPI, _FakeComposition, _Authenticator]:
    exact_composition = composition or _FakeComposition()
    exact_authenticator = authenticator or _Authenticator()
    app = create_pipecat_application(
        exact_composition,  # type: ignore[arg-type]
        authenticator=exact_authenticator,
        database_initializer=lambda: None,
    )
    return app, exact_composition, exact_authenticator


def _default_auth_application(
    monkeypatch: pytest.MonkeyPatch,
    claims: Mapping[str, object],
) -> tuple[FastAPI, _FakeComposition]:
    def verify_token(
        _token: str,
        *,
        app: object,
        check_revoked: bool,
    ) -> Mapping[str, object]:
        assert app is not None
        assert check_revoked is True
        return claims

    monkeypatch.setattr(
        pipecat_application,
        "_get_pipecat_firebase_app",
        lambda: object(),
    )
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        verify_token,
    )
    composition = _FakeComposition()
    authenticator = FirebasePipecatAuthenticator(runner=_InlineRunner())  # type: ignore[arg-type]
    return (
        create_pipecat_application(
            composition,  # type: ignore[arg-type]
            authenticator=authenticator,
            database_initializer=lambda: None,
        ),
        composition,
    )


def _auth_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": AUTHORIZATION, **extra}


def _session_payload() -> dict[str, str]:
    return {"session_id": SESSION_ID, "voice_call_id": CALL_ID}


def _offer_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "sdp": "offer-sdp",
        "type": "offer",
        "pc_id": None,
        "restart_pc": False,
    }
    values.update(overrides)
    return values


def _patch_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "pc_id": PC_ID,
        "candidates": [
            {
                "candidate": "candidate:1 1 UDP 1 127.0.0.1 5000 typ host",
                "sdp_mid": "0",
                "sdp_mline_index": 0,
            }
        ],
    }
    values.update(overrides)
    return values


def _assert_security_headers(response: Any) -> None:
    assert "no-store" in response.headers["cache-control"].lower()
    assert response.headers["referrer-policy"].lower() == "no-referrer"
    assert response.headers["x-content-type-options"].lower() == "nosniff"


def _assert_asgi_security_headers(headers: Mapping[str, str]) -> None:
    assert "no-store" in headers["cache-control"].lower()
    assert headers["referrer-policy"].lower() == "no-referrer"
    assert headers["x-content-type-options"].lower() == "nosniff"


async def _asgi_request(
    app: FastAPI,
    *,
    method: str,
    path: str,
    headers: tuple[tuple[bytes, bytes], ...],
    chunks: AsyncIterator[bytes] | None = None,
    fail_on_receive: bool = False,
) -> tuple[int, dict[str, str], bytes, int]:
    messages: list[dict[str, Any]] = []
    receive_count = 0
    iterator = chunks.__aiter__() if chunks is not None else None

    async def receive() -> dict[str, Any]:
        nonlocal receive_count
        receive_count += 1
        if fail_on_receive:
            raise AssertionError("the request body must not be read")
        if iterator is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        try:
            chunk = await iterator.__anext__()
        except StopAsyncIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("127.0.0.1", 54321),
        "server": ("voice.example.test", 443),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1") for key, value in start["headers"]
    }
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, body, receive_count


async def _drive_asgi_lifespan(
    app: FastAPI,
    messages: list[dict[str, Any]],
) -> None:
    commands = iter(
        (
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        )
    )

    async def receive() -> dict[str, Any]:
        return next(commands)

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "lifespan",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "state": {},
        },
        receive,
        send,
    )


def test_dedicated_application_exposes_exactly_five_method_route_pairs() -> None:
    app, _, _ = _application()

    actual = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in {"POST", "PATCH", "DELETE"}
    }

    assert actual == {
        ("/api/voice/session", "POST"),
        ("/api/voice/session/end", "POST"),
        ("/api/voice/pipecat/signal/{opaque_token}", "POST"),
        ("/api/voice/pipecat/signal/{opaque_token}", "PATCH"),
        ("/api/voice/pipecat/signal/{opaque_token}", "DELETE"),
    }
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None


def test_opaque_signaling_trailing_slash_is_a_non_redirecting_fixed_404() -> None:
    app, composition, _ = _application()

    response = TestClient(app).post(
        f"/api/voice/pipecat/signal/{TOKEN}/",
        headers=_auth_headers(),
        json=_offer_payload(),
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "location" not in response.headers
    assert TOKEN not in response.text
    assert composition.calls == []
    _assert_security_headers(response)


def test_success_routes_project_only_browser_data_and_forward_exact_owned_scope() -> None:
    app, composition, _ = _application()
    client = TestClient(app)

    bootstrap = client.post(
        "/api/voice/session",
        headers=_auth_headers(),
        json=_session_payload(),
    )
    offer = client.post(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers=_auth_headers(),
        json=_offer_payload(),
    )
    patch = client.patch(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers=_auth_headers(),
        json=_patch_payload(),
    )
    delete = client.request(
        "DELETE",
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers=_auth_headers(),
        json={"pc_id": PC_ID},
    )
    end = client.post(
        "/api/voice/session/end",
        headers=_auth_headers(),
        json=_session_payload(),
    )

    assert bootstrap.status_code == 200
    assert bootstrap.json() == composition.assignment.model_dump(mode="json")
    assert offer.status_code == 200
    assert offer.json() == {"sdp": "answer-sdp", "type": "answer", "pc_id": PC_ID}
    for response in (patch, delete, end):
        assert response.status_code == 204
        assert response.content == b""
    for response in (bootstrap, offer, patch, delete, end):
        _assert_security_headers(response)

    assert [operation for operation, _ in composition.calls] == [
        "bootstrap",
        "offer",
        "patch",
        "delete",
        "release",
    ]
    assert composition.calls[0][1] == {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "voice_call_id": CALL_ID,
    }
    assert composition.calls[1][1]["token"] == TOKEN
    assert composition.calls[1][1]["user_id"] == USER_ID
    assert composition.calls[1][1]["request"] == PipecatOfferRequest(**_offer_payload())
    assert composition.calls[2][1]["request"] == PipecatPatchRequest(
        pc_id=PC_ID,
        candidates=(
            PipecatIceCandidate(
                candidate="candidate:1 1 UDP 1 127.0.0.1 5000 typ host",
                sdp_mid="0",
                sdp_mline_index=0,
            ),
        ),
    )
    assert composition.calls[3][1] == {
        "token": TOKEN,
        "user_id": USER_ID,
        "pc_id": PC_ID,
    }
    assert composition.calls[4][1] == composition.calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("framing_headers", "description"),
    [
        (((b"content-type", b"application/json"), (b"content-length", b"8")), "malformed"),
        (
            (
                (b"content-type", b"application/json"),
                (b"content-length", b"999999999"),
            ),
            "declared oversize",
        ),
        (
            (
                (b"content-type", b"application/json"),
                (b"transfer-encoding", b"chunked"),
            ),
            "streamed oversize",
        ),
    ],
)
async def test_invalid_auth_wins_before_any_body_read_or_sensitive_logging(
    framing_headers: tuple[tuple[bytes, bytes], ...],
    description: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del description
    app, composition, authenticator = _application()
    caplog.set_level(logging.DEBUG, logger="murmur")
    authorization = f"Bearer {SECRET_BEARER}".encode()

    status, headers, body, receive_count = await _asgi_request(
        app,
        method="POST",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=((b"authorization", authorization), *framing_headers),
        fail_on_receive=True,
    )

    assert status == 401
    assert receive_count == 0
    assert composition.calls == []
    assert len(authenticator.requests) == 1
    _assert_asgi_security_headers(headers)
    rendered = (
        body.decode("utf-8")
        + "\n"
        + "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
    )
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered


def test_missing_or_malformed_authenticated_json_has_fixed_safe_errors() -> None:
    app, composition, _ = _application()
    client = TestClient(app)
    path = f"/api/voice/pipecat/signal/{TOKEN}"

    missing_type = client.post(path, headers=_auth_headers(), content=b"{}")
    wrong_type = client.post(
        path,
        headers=_auth_headers(**{"Content-Type": "text/plain"}),
        content=b"{}",
    )
    empty = client.post(
        path,
        headers=_auth_headers(**{"Content-Type": "application/json"}),
        content=b"",
    )
    malformed_one = client.post(
        path,
        headers=_auth_headers(**{"Content-Type": "application/json"}),
        content=f'{{"sdp":"{SECRET_INPUT}"'.encode(),
    )
    malformed_two = client.post(
        path,
        headers=_auth_headers(**{"Content-Type": "application/json"}),
        content=b"not-json-at-all",
    )

    assert missing_type.status_code == 415
    assert wrong_type.status_code == 415
    assert empty.status_code == 400
    assert malformed_one.status_code == 400
    assert malformed_two.status_code == 400
    assert missing_type.json() == wrong_type.json()
    assert empty.json() == malformed_one.json() == malformed_two.json()
    for response in (missing_type, wrong_type, empty, malformed_one, malformed_two):
        assert set(response.json()) == {"error"}
        assert SECRET_INPUT not in response.text
        assert TOKEN not in response.text
        _assert_security_headers(response)
    assert composition.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_length", "expected_status"),
    [
        (b"-1", 400),
        (b"not-a-number", 400),
        (b"9" * 4_301, 400),
        (b"999999999", 413),
    ],
)
async def test_content_length_is_validated_before_body_receive(
    content_length: bytes,
    expected_status: int,
) -> None:
    app, composition, _ = _application()

    status, headers, body, receive_count = await _asgi_request(
        app,
        method="POST",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=(
            (b"authorization", AUTHORIZATION.encode()),
            (b"content-type", b"application/json"),
            (b"content-length", content_length),
        ),
        fail_on_receive=True,
    )

    assert status == expected_status
    assert receive_count == 0
    assert composition.calls == []
    assert set(json.loads(body)) == {"error"}
    _assert_asgi_security_headers(headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "framing_headers",
    [
        (
            (b"content-length", b"2"),
            (b"transfer-encoding", b"chunked"),
        ),
        (
            (b"transfer-encoding", b"chunked"),
            (b"transfer-encoding", b"chunked"),
        ),
        ((b"transfer-encoding", b"gzip"),),
        ((b"transfer-encoding", b"gzip, chunked"),),
    ],
)
async def test_ambiguous_or_unsupported_transfer_framing_fails_before_body_receive(
    framing_headers: tuple[tuple[bytes, bytes], ...],
) -> None:
    app, composition, _ = _application()

    status, headers, body, receive_count = await _asgi_request(
        app,
        method="POST",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=(
            (b"authorization", AUTHORIZATION.encode()),
            (b"content-type", b"application/json"),
            *framing_headers,
        ),
        fail_on_receive=True,
    )

    assert status == 400
    assert receive_count == 0
    assert composition.calls == []
    assert set(json.loads(body)) == {"error"}
    _assert_asgi_security_headers(headers)


@pytest.mark.asyncio
async def test_chunked_body_is_bounded_without_content_length() -> None:
    app, composition, _ = _application()
    produced = 0

    async def endless_chunks() -> AsyncIterator[bytes]:
        nonlocal produced
        while produced < 128:
            produced += 1
            yield b"x" * 65_536
        raise AssertionError("the application failed to enforce its streaming body bound")

    status, headers, body, receive_count = await _asgi_request(
        app,
        method="PATCH",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=(
            (b"authorization", AUTHORIZATION.encode()),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ),
        chunks=endless_chunks(),
    )

    assert status == 413
    assert 0 < receive_count < 128
    assert produced == receive_count
    assert composition.calls == []
    assert set(json.loads(body)) == {"error"}
    _assert_asgi_security_headers(headers)


@pytest.mark.asyncio
async def test_stalled_stream_body_has_a_bounded_safe_timeout() -> None:
    composition = _FakeComposition()
    authenticator = _Authenticator()
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
        body_read_timeout_seconds=0.01,
    )
    never = asyncio.Event()

    async def stalled_chunks() -> AsyncIterator[bytes]:
        await never.wait()
        yield b"unreachable"

    status, headers, body, receive_count = await _asgi_request(
        app,
        method="POST",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=(
            (b"authorization", AUTHORIZATION.encode()),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ),
        chunks=stalled_chunks(),
    )

    assert status == 408
    assert receive_count == 1
    assert composition.calls == []
    assert set(json.loads(body)) == {"error"}
    _assert_asgi_security_headers(headers)


def test_json_integer_over_interpreter_digit_limit_is_a_fixed_safe_400() -> None:
    app, composition, _ = _application()
    body = b'{"sdp":' + (b"9" * 4_301) + b',"type":"offer"}'

    response = TestClient(app).post(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers=_auth_headers(**{"Content-Type": "application/json"}),
        content=body,
    )

    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    assert composition.calls == []
    assert b"9" * 100 not in response.content
    _assert_security_headers(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, PipecatAuthenticationUnavailable(SECRET_INPUT)])
async def test_invalid_and_unavailable_authentication_are_distinct_fixed_failures(
    failure: BaseException | None,
) -> None:
    composition = _FakeComposition()

    async def authenticate(_request: Request) -> Mapping[str, object] | None:
        if failure is not None:
            raise failure
        return None

    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticate,
        database_initializer=lambda: None,
    )
    status, headers, body, receive_count = await _asgi_request(
        app,
        method="POST",
        path=f"/api/voice/pipecat/signal/{TOKEN}",
        headers=(
            (b"authorization", f"Bearer {SECRET_BEARER}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", b"999999999"),
        ),
        fail_on_receive=True,
    )

    assert status == (503 if failure is not None else 401)
    assert receive_count == 0
    assert composition.calls == []
    rendered = body.decode()
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered
    assert set(json.loads(body)) == {"error"}
    _assert_asgi_security_headers(headers)


def test_default_authenticator_checks_revocation_with_the_exact_app_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firebase_app = object()
    verifier = Mock(
        return_value={
            "uid": USER_ID,
            "email": "unverified@example.test",
            "email_verified": False,
        }
    )
    exact_user = SimpleNamespace(id=USER_ID)
    monkeypatch.setattr(
        pipecat_application,
        "_get_pipecat_firebase_app",
        lambda: firebase_app,
    )
    monkeypatch.setattr(firebase_auth, "verify_id_token", verifier)
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", Mock(return_value=exact_user))
    composition = _FakeComposition()
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=FirebasePipecatAuthenticator(runner=_InlineRunner()),  # type: ignore[arg-type]
        database_initializer=lambda: None,
    )

    response = TestClient(app).post(
        "/api/voice/session",
        headers=_auth_headers(),
        json=_session_payload(),
    )

    assert response.status_code == 200
    verifier.assert_called_once_with(
        "browser-id-token",
        app=firebase_app,
        check_revoked=True,
    )
    assert composition.calls[0][0] == "bootstrap"


@pytest.mark.parametrize(
    "failure",
    [
        firebase_auth.InvalidIdTokenError(SECRET_INPUT),
        firebase_auth.RevokedIdTokenError(SECRET_INPUT),
        firebase_auth.UserDisabledError(SECRET_INPUT),
        firebase_auth.UserNotFoundError(SECRET_INPUT),
    ],
)
def test_default_authenticator_classifies_invalid_or_revoked_identity_as_401(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        pipecat_application,
        "_get_pipecat_firebase_app",
        lambda: object(),
    )

    def reject_token(
        _token: str,
        *,
        app: object,
        check_revoked: bool,
    ) -> object:
        del app
        assert check_revoked is True
        raise failure

    monkeypatch.setattr(firebase_auth, "verify_id_token", reject_token)
    composition = _FakeComposition()
    authenticator = FirebasePipecatAuthenticator(runner=_InlineRunner())  # type: ignore[arg-type]
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    caplog.set_level(logging.DEBUG, logger="murmur")

    response = TestClient(app).post(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers={
            "Authorization": f"Bearer {SECRET_BEARER}",
            "Content-Type": "application/json",
            "Content-Length": "999999999",
        },
        content=b"not-json",
    )

    assert response.status_code == 401
    assert set(response.json()) == {"error"}
    assert composition.calls == []
    rendered = (
        response.text
        + "\n"
        + "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
    )
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered
    _assert_security_headers(response)


@pytest.mark.parametrize(
    "failure",
    [
        firebase_auth.CertificateFetchError(SECRET_INPUT, RuntimeError(SECRET_INPUT)),
        firebase_auth.ConfigurationNotFoundError(SECRET_INPUT),
        firebase_auth.InsufficientPermissionError(
            SECRET_INPUT,
            RuntimeError(SECRET_INPUT),
            None,
        ),
        firebase_exceptions.UnavailableError(SECRET_INPUT),
        ConnectionError(SECRET_INPUT),
    ],
)
def test_default_authenticator_classifies_verifier_infrastructure_failure_as_fixed_503(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        pipecat_application,
        "_get_pipecat_firebase_app",
        lambda: object(),
    )

    def fail_verification(
        _token: str,
        *,
        app: object,
        check_revoked: bool,
    ) -> object:
        del app
        assert check_revoked is True
        raise failure

    monkeypatch.setattr(firebase_auth, "verify_id_token", fail_verification)
    composition = _FakeComposition()
    authenticator = FirebasePipecatAuthenticator(runner=_InlineRunner())  # type: ignore[arg-type]
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    caplog.set_level(logging.DEBUG, logger="murmur")

    response = TestClient(app).post(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers={
            "Authorization": f"Bearer {SECRET_BEARER}",
            "Content-Type": "application/json",
            "Content-Length": "999999999",
        },
        content=b"not-json",
    )

    assert response.status_code == 503
    assert set(response.json()) == {"error"}
    assert composition.calls == []
    rendered = (
        response.text
        + "\n"
        + "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
    )
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered
    _assert_security_headers(response)


def test_default_authenticator_classifies_firebase_app_initialization_failure_as_fixed_503(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_initialization() -> object:
        raise RuntimeError(SECRET_INPUT)

    monkeypatch.setattr(
        pipecat_application,
        "_get_pipecat_firebase_app",
        fail_initialization,
    )
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        lambda *_args, **_kwargs: pytest.fail("verification must not run"),
    )
    composition = _FakeComposition()
    authenticator = FirebasePipecatAuthenticator(runner=_InlineRunner())  # type: ignore[arg-type]
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    caplog.set_level(logging.DEBUG, logger="murmur")

    response = TestClient(app).post(
        f"/api/voice/pipecat/signal/{TOKEN}",
        headers={
            "Authorization": f"Bearer {SECRET_BEARER}",
            "Content-Type": "application/json",
            "Content-Length": "999999999",
        },
        content=b"not-json",
    )

    assert response.status_code == 503
    assert set(response.json()) == {"error"}
    assert composition.calls == []
    rendered = (
        response.text
        + "\n"
        + "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
    )
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ("email", "email_verified"),
    [
        ("victim@example.test", False),
        ("victim@example.test", "true"),
        ("victim@example.test", None),
        ("malformed-email", True),
        ("two@@example.test", True),
        (" spaced@example.test", True),
        (None, True),
    ],
)
def test_unverified_or_malformed_email_uses_only_exact_uid_provisioning(
    email: object,
    email_verified: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = "firebase-unverified-http"
    exact_user = SimpleNamespace(
        id=uid,
        email="repository-owned-placeholder@murmur.invalid",
        name="Unverified caller",
    )
    exact = Mock(return_value=exact_user)
    legacy = Mock(side_effect=AssertionError("email linking must not run"))
    email_lookup = Mock(side_effect=AssertionError("email lookup must not run"))
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact)
    monkeypatch.setattr(UserRepo, "get_or_create", legacy)
    monkeypatch.setattr(UserRepo, "get_by_email", email_lookup)
    claims: dict[str, object] = {
        "uid": uid,
        "email": email,
        "name": "Unverified caller",
    }
    if email_verified is not None:
        claims["email_verified"] = email_verified
    app, composition = _default_auth_application(monkeypatch, claims)

    response = TestClient(app).post(
        "/api/voice/session",
        headers={"Authorization": AUTHORIZATION},
        json=_session_payload(),
    )

    assert response.status_code == 200
    exact.assert_called_once_with(uid=uid, name="Unverified caller")
    legacy.assert_not_called()
    email_lookup.assert_not_called()
    assert composition.calls[0][1]["user_id"] == uid


def test_existing_uid_with_unverified_victim_email_never_enters_email_update_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = "firebase-existing-http"
    exact = Mock(
        return_value=SimpleNamespace(
            id=uid,
            email="original-verified@example.test",
            name="Original profile",
        )
    )
    legacy = Mock(side_effect=AssertionError("existing email must not be updated"))
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact)
    monkeypatch.setattr(UserRepo, "get_or_create", legacy)
    app, composition = _default_auth_application(
        monkeypatch,
        {
            "uid": uid,
            "email": "victim@example.test",
            "email_verified": False,
            "name": "Attacker-selected name",
        },
    )

    response = TestClient(app).post(
        "/api/voice/session",
        headers={"Authorization": AUTHORIZATION},
        json=_session_payload(),
    )

    assert response.status_code == 200
    exact.assert_called_once_with(uid=uid, name="Attacker-selected name")
    legacy.assert_not_called()
    assert composition.calls[0][1]["user_id"] == uid


def test_exactly_verified_email_uses_intended_legacy_link_and_local_row_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = "firebase-verified-http"
    legacy_id = "legacy-local-row"
    exact = Mock(side_effect=AssertionError("exact-only branch must not run"))
    get_by_id = Mock(return_value=None)
    legacy = Mock(
        return_value=SimpleNamespace(
            id=legacy_id,
            email="verified@example.test",
            name="Verified caller",
        )
    )
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact)
    monkeypatch.setattr(UserRepo, "get_by_id", get_by_id)
    monkeypatch.setattr(UserRepo, "get_or_create", legacy)
    app, composition = _default_auth_application(
        monkeypatch,
        {
            "uid": uid,
            "email": "verified@example.test",
            "email_verified": True,
            "name": "Verified caller",
        },
    )

    response = TestClient(app).post(
        "/api/voice/session",
        headers={"Authorization": AUTHORIZATION},
        json=_session_payload(),
    )

    assert response.status_code == 200
    get_by_id.assert_called_once_with(uid)
    legacy.assert_called_once_with(
        uid=uid,
        email="verified@example.test",
        name="Verified caller",
    )
    exact.assert_not_called()
    assert composition.calls[0][1]["user_id"] == legacy_id


def test_exact_uid_repository_collision_maps_to_fixed_503_without_request_admission(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exact = Mock(
        side_effect=IntegrityError(
            "insert exact uid",
            {},
            RuntimeError(SECRET_INPUT),
        )
    )
    monkeypatch.setattr(UserRepo, "get_or_create_exact_uid", exact)
    app, composition = _default_auth_application(
        monkeypatch,
        {
            "uid": "firebase-collision-http",
            "email": "victim@example.test",
            "email_verified": False,
        },
    )
    caplog.set_level(logging.DEBUG, logger="murmur")

    response = TestClient(app).post(
        "/api/voice/session",
        headers={"Authorization": AUTHORIZATION},
        json=_session_payload(),
    )

    assert response.status_code == 503
    assert composition.calls == []
    rendered = (
        response.text
        + "\n"
        + "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
    )
    assert SECRET_INPUT not in rendered
    assert "victim@example.test" not in rendered
    assert set(response.json()) == {"error"}
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_raw_asgi_lifespan_startup_failure_is_fixed_secret_free_and_non_http() -> None:
    composition = _FakeComposition()
    authenticator = _ClosableAuthenticator()

    def fail_database_initialization() -> None:
        raise RuntimeError(f"database startup failed: {SECRET_INPUT}")

    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=fail_database_initialization,
    )
    messages: list[dict[str, Any]] = []

    with pytest.raises(PipecatApplicationStartupError) as captured:
        await _drive_asgi_lifespan(app, messages)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert [message["type"] for message in messages] == ["lifespan.startup.failed"]
    rendered = repr(messages) + "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert "Dedicated Pipecat application startup failed" in rendered
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered
    assert TOKEN not in rendered
    assert not any(message["type"].startswith("http.response.") for message in messages)
    assert composition.calls == [("close", {})]
    assert authenticator.close_calls == 1


@pytest.mark.asyncio
async def test_raw_asgi_lifespan_success_uses_only_lifespan_messages_and_closes() -> None:
    composition = _FakeComposition()
    authenticator = _ClosableAuthenticator()
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    messages: list[dict[str, Any]] = []

    await _drive_asgi_lifespan(app, messages)

    assert [message["type"] for message in messages] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert not any(message["type"].startswith("http.response.") for message in messages)
    assert composition.calls == [("close", {})]
    assert authenticator.close_calls == 1


@pytest.mark.asyncio
async def test_lifespan_cancellation_cannot_skip_ordered_composition_or_authenticator_close() -> (
    None
):
    events: list[str] = []
    bootstrap = _CloseBootstrap(events)
    signaling = _CloseSignaling(events)
    composition = PipecatApplicationComposition(
        bootstrap,  # type: ignore[arg-type]
        signaling,  # type: ignore[arg-type]
        PipecatCorsContract((ALLOWED_ORIGIN,)),
    )
    authenticator = _ClosableAuthenticator()
    authenticator.close_gate = asyncio.Event()
    app = create_pipecat_application(
        composition,
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await bootstrap.close_entered.wait()

    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown

    assert events == ["bootstrap-close"]
    assert authenticator.close_calls == 0
    bootstrap.close_gate.set()
    await asyncio.wait_for(signaling.close_entered.wait(), timeout=1.0)
    await asyncio.wait_for(authenticator.close_entered.wait(), timeout=1.0)
    assert events == ["bootstrap-close", "signaling-close"]
    assert authenticator.close_calls == 1

    authenticator.close_gate.set()
    for _ in range(100):
        if not any(
            "pipecat" in task.get_name().casefold() and not task.done()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        ):
            break
        await asyncio.sleep(0)
    assert composition._closed
    assert authenticator.close_calls == 1


@pytest.mark.asyncio
async def test_lifespan_shutdown_failure_is_safe_unchained_and_still_closes_authenticator() -> None:
    composition = _FakeComposition()
    composition.failures["close"] = RuntimeError(f"composition close failed: {SECRET_INPUT}")
    authenticator = _ClosableAuthenticator()
    authenticator.close_error = RuntimeError(f"auth close failed: {SECRET_BEARER}")
    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=authenticator,
        database_initializer=lambda: None,
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()

    with pytest.raises(PipecatApplicationShutdownError) as captured:
        await lifespan.__aexit__(None, None, None)

    assert composition.calls == [("close", {})]
    assert authenticator.close_calls == 1
    assert str(captured.value) == "Dedicated Pipecat application shutdown is incomplete"
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__
    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert SECRET_INPUT not in rendered
    assert SECRET_BEARER not in rendered


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/voice/session", {**_session_payload(), "extra": SECRET_INPUT}),
        ("POST", f"/api/voice/pipecat/signal/{TOKEN}", _offer_payload(type="answer")),
        ("POST", f"/api/voice/pipecat/signal/{TOKEN}", _offer_payload(restart_pc=1)),
        (
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(sdp="s" * 1_000_001),
        ),
        (
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(pc_id="p" * 257),
        ),
        (
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            {**_offer_payload(), "request_data": SECRET_INPUT},
        ),
        ("PATCH", f"/api/voice/pipecat/signal/{TOKEN}", _patch_payload(pc_id=None)),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[{"candidate": "candidate", "sdp_mid": None, "sdp_mline_index": 0}]
            ),
        ),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[{"candidate": "candidate", "sdp_mid": "0", "sdp_mline_index": False}]
            ),
        ),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[
                    {
                        "candidate": "c" * 8_193,
                        "sdp_mid": "0",
                        "sdp_mline_index": 0,
                    }
                ]
            ),
        ),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[
                    {
                        "candidate": "candidate",
                        "sdp_mid": "m" * 129,
                        "sdp_mline_index": 0,
                    }
                ]
            ),
        ),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[
                    {
                        "candidate": "candidate",
                        "sdp_mid": "0",
                        "sdp_mline_index": 129,
                    }
                ]
            ),
        ),
        (
            "PATCH",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _patch_payload(
                candidates=[
                    {
                        "candidate": f"candidate-{index}",
                        "sdp_mid": "0",
                        "sdp_mline_index": 0,
                    }
                    for index in range(129)
                ]
            ),
        ),
        (
            "DELETE",
            f"/api/voice/pipecat/signal/{TOKEN}",
            {"pc_id": PC_ID, "extra": SECRET_INPUT},
        ),
    ],
)
def test_strict_dtos_fail_with_non_reflective_422(
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    app, composition, _ = _application()
    client = TestClient(app)

    response = client.request(method, path, headers=_auth_headers(), json=payload)

    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert SECRET_INPUT not in response.text
    assert TOKEN not in response.text
    assert composition.calls == []
    _assert_security_headers(response)


def test_large_invalid_sdp_does_not_leak_request_or_provider_secrets_to_outputs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, composition, _ = _application()
    ice_username = "ice-username-that-must-never-leak"
    ice_credential = "ice-credential-that-must-never-leak"
    provider_secret = "provider-api-key-that-must-never-leak"
    secrets = (
        TOKEN,
        AUTHORIZATION,
        SECRET_BEARER,
        ice_username,
        ice_credential,
        provider_secret,
    )
    secret_prefix = "|".join(secrets)
    oversized_sdp = secret_prefix + ("s" * (1_000_001 - len(secret_prefix)))
    loguru_lines: list[str] = []
    caplog.set_level(logging.DEBUG, logger="murmur")
    caplog.set_level(logging.DEBUG, logger="pipecat")
    sink_id = loguru_logger.add(
        lambda message: loguru_lines.append(str(message)),
        level="DEBUG",
    )

    try:
        response = TestClient(app).post(
            f"/api/voice/pipecat/signal/{TOKEN}",
            headers=_auth_headers(),
            json=_offer_payload(sdp=oversized_sdp),
        )
    finally:
        loguru_logger.remove(sink_id)

    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert composition.calls == []
    standard_log_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "murmur"
        or record.name.startswith("murmur.")
        or record.name == "pipecat"
        or record.name.startswith("pipecat.")
    ]
    rendered_outputs = "\n".join(
        [response.text, repr(dict(response.headers)), *standard_log_lines, *loguru_lines]
    )
    for secret in secrets:
        assert secret not in rendered_outputs
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ("operation", "method", "path", "payload", "failure", "expected_status"),
    [
        (
            "bootstrap",
            "POST",
            "/api/voice/session",
            _session_payload(),
            PipecatBootstrapNotFound(f"not found {SECRET_INPUT}"),
            404,
        ),
        (
            "bootstrap",
            "POST",
            "/api/voice/session",
            _session_payload(),
            PipecatBootstrapForbidden(f"forbidden {SECRET_INPUT}"),
            403,
        ),
        (
            "bootstrap",
            "POST",
            "/api/voice/session",
            _session_payload(),
            PipecatBootstrapConflict(f"conflict {SECRET_INPUT}"),
            409,
        ),
        (
            "bootstrap",
            "POST",
            "/api/voice/session",
            _session_payload(),
            PipecatBootstrapUnavailable(f"unavailable {SECRET_INPUT}"),
            503,
        ),
        (
            "offer",
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(),
            PipecatSignalingNotFound(f"not found {SECRET_INPUT}"),
            404,
        ),
        (
            "offer",
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(),
            PipecatSignalingForbidden(f"forbidden {SECRET_INPUT}"),
            403,
        ),
        (
            "offer",
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(),
            PipecatSignalingConflict(f"conflict {SECRET_INPUT}"),
            409,
        ),
        (
            "offer",
            "POST",
            f"/api/voice/pipecat/signal/{TOKEN}",
            _offer_payload(),
            PipecatSignalingUnavailable(f"unavailable {SECRET_INPUT}"),
            503,
        ),
    ],
)
def test_domain_failures_map_to_fixed_safe_error_contracts(
    operation: str,
    method: str,
    path: str,
    payload: dict[str, object],
    failure: BaseException,
    expected_status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, composition, _ = _application()
    composition.failures[operation] = failure
    client = TestClient(app, raise_server_exceptions=False)
    caplog.set_level(logging.DEBUG, logger="murmur")

    response = client.request(method, path, headers=_auth_headers(), json=payload)

    assert response.status_code == expected_status
    assert set(response.json()) == {"error"}
    assert SECRET_INPUT not in response.text
    assert TOKEN not in response.text
    murmur_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "murmur" or record.name.startswith("murmur.")
    )
    assert SECRET_INPUT not in murmur_logs
    assert TOKEN not in murmur_logs
    _assert_security_headers(response)


def test_unexpected_failures_are_fixed_500s_with_no_secret_or_token_leakage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = []
    for suffix in ("one", "two"):
        app, composition, _ = _application()
        composition.failures["offer"] = RuntimeError(f"{SECRET_INPUT}-{suffix}")
        client = TestClient(app, raise_server_exceptions=False)
        caplog.clear()
        caplog.set_level(logging.DEBUG, logger="murmur")

        response = client.post(
            f"/api/voice/pipecat/signal/{TOKEN}",
            headers=_auth_headers(),
            json=_offer_payload(),
        )
        responses.append(response)

        assert response.status_code == 500
        assert set(response.json()) == {"error"}
        assert SECRET_INPUT not in response.text
        assert TOKEN not in response.text
        murmur_logs = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "murmur" or record.name.startswith("murmur.")
        )
        assert SECRET_INPUT not in murmur_logs
        assert TOKEN not in murmur_logs
        _assert_security_headers(response)

    assert responses[0].json() == responses[1].json()


def test_cors_preflight_is_explicit_noncredentialed_and_security_headered() -> None:
    app, composition, authenticator = _application()
    client = TestClient(app)
    path = f"/api/voice/pipecat/signal/{TOKEN}"
    preflight_headers = {
        "Origin": ALLOWED_ORIGIN,
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization, content-type",
    }

    allowed = client.options(path, headers=preflight_headers)
    denied_method = client.options(
        path,
        headers={**preflight_headers, "Access-Control-Request-Method": "GET"},
    )
    denied_header = client.options(
        path,
        headers={**preflight_headers, "Access-Control-Request-Headers": "x-secret"},
    )
    denied_origin = client.options(
        path,
        headers={**preflight_headers, "Origin": "https://attacker.example.test"},
    )

    assert allowed.status_code in {200, 204}
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "PATCH" in allowed.headers["access-control-allow-methods"]
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in allowed.headers
    assert "*" not in allowed.headers["access-control-allow-origin"]
    assert denied_method.status_code == 400
    assert denied_header.status_code == 400
    assert denied_origin.status_code == 400
    assert "access-control-allow-origin" not in denied_origin.headers
    for response in (allowed, denied_method, denied_header, denied_origin):
        _assert_security_headers(response)
    assert authenticator.requests == []
    assert composition.calls == []


def test_actual_cors_response_echoes_only_the_allowed_origin_without_credentials() -> None:
    app, _, _ = _application()
    client = TestClient(app)

    response = client.post(
        "/api/voice/session",
        headers=_auth_headers(Origin=ALLOWED_ORIGIN),
        json=_session_payload(),
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert "*" not in response.headers["access-control-allow-origin"]
    _assert_security_headers(response)


@pytest.mark.parametrize(
    "origin",
    [
        "https://public.example.test",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
)
def test_application_accepts_https_and_loopback_http_origins(origin: str) -> None:
    composition = _FakeComposition()
    composition.cors = PipecatCorsContract((origin,))

    app = create_pipecat_application(
        composition,  # type: ignore[arg-type]
        authenticator=_Authenticator(),
        database_initializer=lambda: None,
    )

    assert app.state.pipecat_composition is composition


def test_application_and_settings_reject_public_plain_http_origins() -> None:
    composition = _FakeComposition()
    composition.cors = PipecatCorsContract(("http://public.example.test",))

    with pytest.raises(ValueError, match="HTTPS or loopback HTTP"):
        create_pipecat_application(
            composition,  # type: ignore[arg-type]
            authenticator=_Authenticator(),
            database_initializer=lambda: None,
        )

    environment = {
        "VOICE_RUNTIME": "pipecat_smallwebrtc_v1",
        "VOICE_V2_PROFILE_ID": "pipecat-direct-cascade-v1",
        "PIPECAT_SIGNALING_BASE_URL": ("http://127.0.0.1:8001/api/voice/pipecat/signal"),
        "ALLOWED_CORS_ORIGINS": "http://public.example.test",
    }
    with pytest.raises(PipecatCompositionUnavailable) as captured:
        PipecatCompositionSettings.from_environment(environment)
    assert str(captured.value) == "The dedicated Pipecat process configuration is invalid"
    assert "public.example.test" not in str(captured.value)


def test_missing_and_oversized_opaque_paths_do_not_reflect_bearers() -> None:
    app, composition, _ = _application()
    client = TestClient(app)
    oversized_token = SECRET_INPUT + ("x" * 600)

    missing = client.post(
        "/api/voice/pipecat/signal/",
        headers=_auth_headers(),
        json=_offer_payload(),
    )
    oversized = client.post(
        f"/api/voice/pipecat/signal/{oversized_token}",
        headers=_auth_headers(),
        json=_offer_payload(),
    )

    assert missing.status_code == 404
    assert oversized.status_code == 422
    for response in (missing, oversized):
        assert SECRET_INPUT not in response.text
        assert oversized_token not in response.text
        _assert_security_headers(response)
    assert composition.calls == []

"""Provider-free HTTP and token-boundary tests for the Cosmo experiment."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError, api_error_handler
from murmur.api.routers.cosmo_voice import get_cosmo_broker, router
from murmur.voice.cosmo import (
    COSMO_TOKEN_URL,
    TOKEN_TTL_SECONDS,
    CosmoTokenBroker,
    CosmoUnavailable,
)

SECRET = "workspace-key-never-sent-to-browser"
JWT = "test.minted.jwt"


def token_body(**overrides):
    return {
        "jwt": JWT,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=TOKEN_TTL_SECONDS)).isoformat(),
        **overrides,
    }


def make_client(broker, *, authenticated=True):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.dependency_overrides[get_cosmo_broker] = lambda: broker
    if authenticated:
        app.dependency_overrides[get_authenticated_user] = lambda: {
            "id": "trusted-user",
            "email": "user@example.test",
            "name": None,
        }
    return TestClient(app)


def test_http_mint_uses_authenticated_identity_and_never_returns_workspace_key():
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json=token_body(token_id="not-needed-by-browser"))

    broker = CosmoTokenBroker(True, SECRET, httpx.MockTransport(upstream))
    with make_client(broker) as client:
        response = client.post("/api/voice/cosmo/token", json={})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert set(response.json()) == {"jwt", "expires_at"}
        assert response.json()["jwt"] == JWT
        assert SECRET not in response.text
        assert client.post("/api/voice/cosmo/token", json={"user_id": "victim"}).status_code == 422
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == COSMO_TOKEN_URL
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {
        "external_user_id": "trusted-user",
        "ttl_seconds": TOKEN_TTL_SECONDS,
    }
    assert SECRET not in repr(broker)


@pytest.mark.parametrize("endpoint", ["/status", "/token"])
def test_unauthenticated_http_request_cannot_access_provider(endpoint):
    def upstream(_request):
        pytest.fail("Unauthenticated request contacted Cosmo")

    broker = CosmoTokenBroker(True, SECRET, httpx.MockTransport(upstream))
    with make_client(broker, authenticated=False) as client:
        response = (
            client.get("/api/voice/cosmo/status")
            if endpoint == "/status"
            else client.post("/api/voice/cosmo/token", json={})
        )
    assert response.status_code == 401


@pytest.mark.parametrize("enabled,key", [(False, SECRET), (True, ""), (True, "  ")])
def test_unconfigured_lab_fails_without_provider_contact(enabled, key):
    def upstream(_request):
        pytest.fail("Unconfigured lab contacted Cosmo")

    broker = CosmoTokenBroker(enabled, key, httpx.MockTransport(upstream))
    with make_client(broker) as client:
        status = client.get("/api/voice/cosmo/status")
        assert status.json() == {"enabled": enabled, "configured": bool(key.strip())}
        assert "ready" not in status.json()
        assert client.post("/api/voice/cosmo/token", json={}).status_code == 503


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_provider_errors_are_sanitized_and_redirects_not_followed(status, caplog):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(status, text=SECRET, headers={"location": "https://untrusted.test/"})

    broker = CosmoTokenBroker(True, SECRET, httpx.MockTransport(upstream))
    with make_client(broker) as client:
        response = client.post("/api/voice/cosmo/token", json={})
    assert response.status_code == 503
    assert SECRET not in response.text + caplog.text
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        token_body(jwt=""),
        token_body(jwt="contains whitespace"),
        token_body(jwt="x" * 20000),
        token_body(expires_at="not-a-date"),
        token_body(expires_at=datetime.now(UTC).isoformat()),
        token_body(expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()),
        token_body(expires_at=datetime.now().isoformat()),
    ],
)
async def test_invalid_token_response_cannot_reach_browser(body):
    broker = CosmoTokenBroker(
        True, SECRET, httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(CosmoUnavailable, match="invalid token"):
        await broker.mint("user")


@pytest.mark.asyncio
async def test_non_json_and_transport_failures_are_sanitized():
    broker = CosmoTokenBroker(
        True, SECRET, httpx.MockTransport(lambda _: httpx.Response(200, text=SECRET))
    )
    with pytest.raises(CosmoUnavailable, match="invalid token"):
        await broker.mint("user")

    def timeout(_):
        raise httpx.ReadTimeout(SECRET)

    broker = CosmoTokenBroker(True, SECRET, httpx.MockTransport(timeout))
    with pytest.raises(CosmoUnavailable, match="Could not reach Cosmo") as result:
        await broker.mint("user")
    assert SECRET not in str(result.value)

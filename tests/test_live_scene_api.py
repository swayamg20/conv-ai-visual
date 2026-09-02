"""Route and application-composition tests for Gate 1 scene streaming."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import murmur.api.application as application
import pytest
from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError
from murmur.api.routers.live_scenes import _encode_scene_events, _OwnedStreamingResponse
from murmur.live_scene import (
    LiveSceneRequest,
    SceneAuthoringAdmission,
    ScenePatchEvent,
    SceneStreamCompletedEvent,
    SceneStreamEvent,
    SceneStreamStartedEvent,
)
from starlette.requests import ClientDisconnect

AUTHENTICATED_USER = {
    "id": "scene-user",
    "email": "scene-user@example.com",
    "name": "Scene User",
}


def _request_body() -> dict[str, object]:
    return {
        "prompt": "Explain a request flowing through an API and database.",
        "generation": 7,
        "baseScene": {"revision": 0, "nodes": []},
    }


def _scene_events() -> tuple[SceneStreamEvent, ...]:
    started = SceneStreamStartedEvent.model_validate(
        {
            "type": "scene_stream_started",
            "generation": 7,
            "attempt": 1,
            "baseRevision": 0,
        }
    )
    patch = ScenePatchEvent.model_validate(
        {
            "type": "scene_patch",
            "generation": 7,
            "attempt": 1,
            "sequence": 1,
            "baseRevision": 0,
            "resultRevision": 1,
            "patch": {
                "v": 1,
                "patchId": "api-node",
                "narration": "First, draw the API boundary.",
                "operations": [
                    {
                        "op": "put",
                        "node": {
                            "id": "api",
                            "kind": "rect",
                            "presentation": {"enter": "draw", "exit": "fade"},
                            "x": 100.0,
                            "y": 120.0,
                            "width": 180.0,
                            "height": 90.0,
                            "style": {
                                "stroke": "hsl(var(--chalk))",
                                "strokeWidth": 3.0,
                                "opacity": 1.0,
                                "roughness": 1.0,
                                "fill": "transparent",
                            },
                        },
                    }
                ],
            },
        }
    )
    completed = SceneStreamCompletedEvent.model_validate(
        {
            "type": "scene_stream_completed",
            "generation": 7,
            "finalRevision": 1,
            "patchCount": 1,
            "firstPatchMs": 12.5,
            "totalMs": 24.0,
            "repaired": False,
        }
    )
    return started, patch, completed


class FakeSceneAuthoringService:
    def __init__(self, events: tuple[SceneStreamEvent, ...] = ()) -> None:
        self.events = events
        self.requests: list[LiveSceneRequest] = []

    async def stream_events(
        self,
        request: LiveSceneRequest,
    ) -> AsyncIterator[SceneStreamEvent]:
        self.requests.append(request)
        for event in self.events:
            yield event


class _ClosingSceneEvents:
    def __init__(self, events: tuple[SceneStreamEvent, ...]) -> None:
        self._events = iter(events)
        self.closed = False

    def __aiter__(self) -> _ClosingSceneEvents:
        return self

    async def __anext__(self) -> SceneStreamEvent:
        return next(self._events)

    async def aclose(self) -> None:
        self.closed = True


def _sse_payloads(response_text: str) -> list[dict[str, Any]]:
    blocks = [block for block in response_text.split("\n\n") if block]
    assert all(block.startswith("data: ") for block in blocks)
    return [json.loads(block.removeprefix("data: ")) for block in blocks]


def _test_client(
    service: FakeSceneAuthoringService,
    *,
    authenticated: bool,
    scene_authoring_enabled: bool = True,
) -> TestClient:
    app = application.create_application(
        scene_authoring_service=service,  # type: ignore[arg-type]
        scene_authoring_enabled=scene_authoring_enabled,
    )

    def authenticate() -> dict[str, str | None]:
        if not authenticated:
            raise ApiError(401, "Not authenticated")
        return AUTHENTICATED_USER

    app.dependency_overrides[get_authenticated_user] = authenticate
    return TestClient(app)


@pytest.mark.asyncio
async def test_sse_encoder_closes_inner_service_iterator_on_consumer_abort() -> None:
    events = _ClosingSceneEvents(_scene_events())
    encoded = _encode_scene_events(events)

    assert (await anext(encoded)).startswith("data: ")
    await encoded.aclose()

    assert events.closed is True


@pytest.mark.asyncio
async def test_streaming_response_closes_events_and_admission_when_send_disconnects() -> None:
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )
    lease = await admission.acquire("scene-user")
    events = _ClosingSceneEvents(_scene_events())
    response = _OwnedStreamingResponse(_encode_scene_events(events, lease))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/live-scenes/stream",
        "raw_path": b"/api/live-scenes/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)  # type: ignore[arg-type]

    assert events.closed is True
    replacement = await admission.acquire("scene-user")
    await replacement.aclose()


def test_live_scene_stream_requires_authentication() -> None:
    service = FakeSceneAuthoringService(_scene_events())
    client = _test_client(service, authenticated=False)
    try:
        response = client.post("/api/live-scenes/stream", json=_request_body())
    finally:
        client.close()

    assert response.status_code == 401
    assert response.json() == {"error": "Not authenticated"}
    assert service.requests == []


def test_live_scene_stream_is_default_off_even_for_authenticated_users() -> None:
    service = FakeSceneAuthoringService(_scene_events())
    client = _test_client(
        service,
        authenticated=True,
        scene_authoring_enabled=False,
    )
    try:
        response = client.post("/api/live-scenes/stream", json=_request_body())
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {"error": "Live scene generation is not enabled"}
    assert service.requests == []


def test_live_scene_stream_emits_canonical_camel_case_sse() -> None:
    service = FakeSceneAuthoringService(_scene_events())
    client = _test_client(service, authenticated=True)
    try:
        response = client.post("/api/live-scenes/stream", json=_request_body())
    finally:
        client.close()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert _sse_payloads(response.text) == [
        {
            "type": "scene_stream_started",
            "generation": 7,
            "attempt": 1,
            "baseRevision": 0,
        },
        {
            "type": "scene_patch",
            "generation": 7,
            "attempt": 1,
            "sequence": 1,
            "baseRevision": 0,
            "resultRevision": 1,
            "patch": {
                "v": 1,
                "patchId": "api-node",
                "narration": "First, draw the API boundary.",
                "operations": [
                    {
                        "op": "put",
                        "node": {
                            "id": "api",
                            "kind": "rect",
                            "presentation": {"enter": "draw", "exit": "fade"},
                            "x": 100.0,
                            "y": 120.0,
                            "width": 180.0,
                            "height": 90.0,
                            "style": {
                                "stroke": "hsl(var(--chalk))",
                                "strokeWidth": 3.0,
                                "opacity": 1.0,
                                "roughness": 1.0,
                                "fill": "transparent",
                            },
                        },
                    }
                ],
            },
        },
        {
            "type": "scene_stream_completed",
            "generation": 7,
            "finalRevision": 1,
            "patchCount": 1,
            "firstPatchMs": 12.5,
            "totalMs": 24.0,
            "repaired": False,
        },
    ]
    assert len(service.requests) == 1
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()
    assert not hasattr(service.requests[0], "user_id")


def test_live_scene_request_rejects_client_identity_claims() -> None:
    service = FakeSceneAuthoringService(_scene_events())
    client = _test_client(service, authenticated=True)
    body = _request_body()
    body["userId"] = "attacker"
    try:
        response = client.post("/api/live-scenes/stream", json=body)
    finally:
        client.close()

    assert response.status_code == 422
    assert service.requests == []


def test_default_scene_service_uses_a_lazy_configured_client_factory(monkeypatch) -> None:
    captured: dict[str, object] = {}
    client_calls: list[tuple[str, str | None]] = []
    expected_client = object()

    class CapturingSceneAuthoringService:
        def __init__(
            self,
            *,
            client_factory,
            temperature: float,
            max_tokens: int,
            timeout_seconds: float,
        ) -> None:
            captured.update(
                {
                    "client_factory": client_factory,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout_seconds": timeout_seconds,
                }
            )

    def create_client(provider: str, *, model: str | None = None):
        client_calls.append((provider, model))
        return expected_client

    monkeypatch.setattr(application, "SceneAuthoringService", CapturingSceneAuthoringService)
    monkeypatch.setattr(application, "create_llm_client", create_client)
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_PROVIDER", "openai")
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_MODEL", "scene-model")
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_MAX_TOKENS", 1234)
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_TIMEOUT_SECONDS", 9.5)

    app = application.create_application()
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_PROVIDER", "groq")
    monkeypatch.setattr(application.config, "MURMUR_SCENE_LLM_MODEL", "later-model")

    assert app.state.scene_authoring_service.__class__ is CapturingSceneAuthoringService
    assert client_calls == []
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 1234
    assert captured["timeout_seconds"] == 9.5
    assert captured["client_factory"]() is expected_client  # type: ignore[operator]
    assert client_calls == [("openai", "scene-model")]


def test_scene_config_inherits_existing_llm_defaults() -> None:
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("MURMUR_SCENE_LLM_"):
            env.pop(name)
    env.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "LLM_PROVIDER": "openai",
            "OPENAI_MODEL": "existing-model",
            "LLM_MAX_TOKENS": "987",
            "LLM_TEMPERATURE": "0.35",
        }
    )
    script = """
import json
from murmur.core.config import config
print(json.dumps({
    'provider': config.MURMUR_SCENE_LLM_PROVIDER,
    'model': config.MURMUR_SCENE_LLM_MODEL,
    'max_tokens': config.MURMUR_SCENE_LLM_MAX_TOKENS,
    'temperature': config.MURMUR_SCENE_LLM_TEMPERATURE,
    'timeout_seconds': config.MURMUR_SCENE_LLM_TIMEOUT_SECONDS,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(completed.stdout) == {
        "provider": "openai",
        "model": "existing-model",
        "max_tokens": 987,
        "temperature": 0.35,
        "timeout_seconds": 20.0,
    }

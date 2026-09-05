"""Guard, wire, and ownership tests for semantic scene HTTP routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import murmur.api.application as application
import murmur.api.routers.live_scenes as live_scenes
import pytest
from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError
from murmur.api.routers.live_scenes import (
    _DEVELOPMENT_SCENE_LAB_IDENTITY,
    _encode_semantic_scene_events,
    _OwnedStreamingResponse,
)
from murmur.live_scene import SceneAdmissionError, SceneAuthoringAdmission
from murmur.live_scene.contracts import (
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    TeachingBeatDraft,
    VisualActAbstainReason,
)
from murmur.live_scene.semantic_service_contracts import (
    SemanticAtomMetadata,
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
    SemanticSceneStreamDeclinedEvent,
    SemanticSceneStreamEvent,
)
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

AUTHENTICATED_USER = {
    "id": "semantic-scene-user",
    "email": "semantic-scene-user@example.com",
    "name": "Semantic Scene User",
}


def _request_body() -> dict[str, object]:
    return {
        "prompt": "Teach the Pythagorean area identity progressively.",
        "generation": 7,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
    }


def _semantic_events() -> tuple[SemanticSceneStreamEvent, ...]:
    beat = TeachingBeatDraft.model_validate(
        {
            "v": 1,
            "beatId": "api-triangle",
            "narration": "First, establish the right triangle.",
            "act": "introduce",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": "areas",
                "revealThrough": "triangle",
            },
        }
    )
    compiled = compile_teaching_beat(beat, SemanticSceneState(revision=0))
    atom = compiled.atoms[0]
    certificate = atom.certificate
    assert certificate is not None

    started = SceneStreamStartedEvent(
        generation=7,
        attempt=1,
        base_revision=0,
    )
    patch = SemanticScenePatchEvent(
        generation=7,
        attempt=1,
        sequence=1,
        base_revision=0,
        result_revision=1,
        patch=atom.patch,
        semantic=SemanticAtomMetadata(
            beat=beat,
            atom_id=atom.atom_id,
            component_id=atom.component_id,
            role=atom.role,
            atom_ordinal=certificate.body.atom_ordinal,
            semantic_base_revision=0,
            semantic_result_revision=1,
            receipt=atom.receipt,
            certificate=certificate,
        ),
    )
    completed = SceneStreamCompletedEvent(
        generation=7,
        final_revision=1,
        patch_count=1,
        first_patch_ms=12.5,
        total_ms=24.0,
        repaired=False,
    )
    return started, patch, completed


def _semantic_declined_events() -> tuple[SemanticSceneStreamEvent, ...]:
    return (
        SceneStreamStartedEvent(
            generation=7,
            attempt=1,
            base_revision=0,
        ),
        SemanticSceneStreamDeclinedEvent(
            generation=7,
            attempt=1,
            final_revision=0,
            reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
            message="No supported visual change is available.",
        ),
    )


class FakeSemanticSceneAuthoringService:
    def __init__(self, events: tuple[SemanticSceneStreamEvent, ...] = ()) -> None:
        self.events = events
        self.requests: list[SemanticLiveSceneRequest] = []

    async def stream_routed_semantic_events(
        self,
        request: SemanticLiveSceneRequest,
    ) -> AsyncIterator[SemanticSceneStreamEvent]:
        self.requests.append(request)
        for event in self.events:
            yield event


class RecordingSceneAuthoringAdmission(SceneAuthoringAdmission):
    def __init__(self) -> None:
        super().__init__(global_limit=1, per_user_limit=1, requests_per_minute=10)
        self.user_ids: list[str] = []

    async def acquire(self, user_id: str):
        self.user_ids.append(user_id)
        return await super().acquire(user_id)


class RejectingSceneAuthoringAdmission(SceneAuthoringAdmission):
    def __init__(self) -> None:
        super().__init__(global_limit=1, per_user_limit=1, requests_per_minute=10)
        self.user_ids: list[str] = []

    async def acquire(self, user_id: str):
        self.user_ids.append(user_id)
        raise SceneAdmissionError("capacity_reached", "Visual generation is busy.")


class _ClosingSemanticEvents:
    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = iter(events)
        self.closed = False

    def __aiter__(self) -> _ClosingSemanticEvents:
        return self

    async def __anext__(self) -> SemanticSceneStreamEvent:
        return cast(SemanticSceneStreamEvent, next(self._events))

    async def aclose(self) -> None:
        self.closed = True


def _sse_payloads(response_text: str) -> list[dict[str, Any]]:
    blocks = [block for block in response_text.split("\n\n") if block]
    assert all(block.startswith("data: ") for block in blocks)
    return [json.loads(block.removeprefix("data: ")) for block in blocks]


def _test_client(
    service: FakeSemanticSceneAuthoringService,
    *,
    authenticated: bool = False,
    authentication_unavailable: bool = False,
    admission: SceneAuthoringAdmission | None = None,
    scene_authoring_enabled: bool = True,
    client_host: str = "127.0.0.1",
) -> TestClient:
    app = application.create_application(
        scene_authoring_service=service,  # type: ignore[arg-type]
        scene_authoring_admission=admission,
        scene_authoring_enabled=scene_authoring_enabled,
    )

    def authenticate_product_user() -> dict[str, str | None]:
        if authentication_unavailable:
            raise ApiError(503, "Authentication is unavailable")
        if not authenticated:
            raise ApiError(401, "Not authenticated")
        return AUTHENTICATED_USER

    app.dependency_overrides[get_authenticated_user] = authenticate_product_user
    return TestClient(app, client=(client_host, 50_000))


@pytest.mark.asyncio
async def test_semantic_encoder_closes_upstream_on_consumer_abort() -> None:
    events = _ClosingSemanticEvents(_semantic_events())
    encoded = _encode_semantic_scene_events(events)

    assert (await anext(encoded)).startswith("data: ")
    await encoded.aclose()

    assert events.closed is True


@pytest.mark.asyncio
async def test_semantic_encoder_failure_closes_upstream() -> None:
    events = _ClosingSemanticEvents(({"type": "scene_patch"},))
    encoded = _encode_semantic_scene_events(events)

    with pytest.raises(ValidationError):
        await anext(encoded)

    assert events.closed is True


@pytest.mark.asyncio
async def test_semantic_streaming_response_releases_resources_on_disconnect() -> None:
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )
    lease = await admission.acquire(_DEVELOPMENT_SCENE_LAB_IDENTITY)
    events = _ClosingSemanticEvents(_semantic_events())
    response = _OwnedStreamingResponse(
        _encode_semantic_scene_events(events),
        admission_lease=lease,
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/live-scenes/lab/semantic/stream",
        "raw_path": b"/api/live-scenes/lab/semantic/stream",
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
    replacement = await admission.acquire(_DEVELOPMENT_SCENE_LAB_IDENTITY)
    await replacement.aclose()


@pytest.mark.parametrize(
    ("flag", "environment"),
    [
        ("1", "production"),
        ("1", "staging"),
        ("1", "test"),
        ("0", "development"),
        (None, "development"),
    ],
)
def test_semantic_lab_is_hidden_without_both_server_guards(
    monkeypatch,
    flag: str | None,
    environment: str,
) -> None:
    if flag is None:
        monkeypatch.delenv("MURMUR_SCENE_LAB", raising=False)
    else:
        monkeypatch.setenv("MURMUR_SCENE_LAB", flag)
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", environment)
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    client = _test_client(service)
    try:
        response = client.post("/api/live-scenes/lab/semantic/stream", json={})
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json() == {"error": "Not found"}
    assert service.requests == []


def test_semantic_lab_rejects_non_loopback_and_spoofed_forwarding_headers(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    client = _test_client(service, client_host="192.0.2.10")
    try:
        response = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
            headers={
                "Host": "127.0.0.1:8000",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json() == {"error": "Not found"}
    assert service.requests == []


def test_semantic_lab_streams_strict_metadata_and_releases_admission(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(service, admission=admission)
    try:
        first = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
        )
        second = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["x-accel-buffering"] == "no"
    payloads = _sse_payloads(first.text)
    assert [payload["type"] for payload in payloads] == [
        "scene_stream_started",
        "semantic_scene_patch",
        "scene_stream_completed",
    ]
    semantic = payloads[1]["semantic"]
    assert semantic["role"] == "triangle"
    assert semantic["atomOrdinal"] == 1
    assert semantic["semanticBaseRevision"] == 0
    assert semantic["semanticResultRevision"] == 1
    assert semantic["receipt"]["issuer"] == "semantic_verifier"
    assert semantic["certificate"]["body"]["compilerVersion"] == (
        "murmur.pythagorean_area_identity.v2"
    )
    assert admission.user_ids == [
        _DEVELOPMENT_SCENE_LAB_IDENTITY,
        _DEVELOPMENT_SCENE_LAB_IDENTITY,
    ]
    assert len(service.requests) == 2
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()


def test_semantic_lab_streams_decline_without_mutating_revision(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_declined_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert _sse_payloads(response.text) == [
        {
            "type": "scene_stream_started",
            "generation": 7,
            "attempt": 1,
            "baseRevision": 0,
        },
        {
            "type": "semantic_scene_stream_declined",
            "generation": 7,
            "attempt": 1,
            "finalRevision": 0,
            "reasonCode": "unsupported_intent",
            "message": "No supported visual change is available.",
        },
    ]
    assert admission.user_ids == [_DEVELOPMENT_SCENE_LAB_IDENTITY]
    assert len(service.requests) == 1


def test_semantic_lab_validates_lockstep_request_before_paid_admission(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    body = _request_body()
    body["baseSemanticScene"] = {"revision": 1, "components": []}
    client = _test_client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=body,
        )
    finally:
        client.close()

    assert response.status_code == 422
    assert admission.user_ids == []
    assert service.requests == []


def test_semantic_lab_is_default_off_even_when_local_guard_is_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    client = _test_client(service, scene_authoring_enabled=False)
    try:
        response = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {"error": "Live scene generation is not enabled"}
    assert service.requests == []


def test_semantic_lab_rejects_before_provider_stream_when_admission_is_full(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    client = _test_client(service, admission=RejectingSceneAuthoringAdmission())
    try:
        response = client.post(
            "/api/live-scenes/lab/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 429
    assert response.json() == {"error": "Visual generation is busy."}
    assert service.requests == []


def test_semantic_product_stream_requires_authentication() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert response.json() == {"error": "Not authenticated"}
    assert admission.user_ids == []
    assert service.requests == []


def test_semantic_product_stream_contains_authentication_outages() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(
        service,
        authentication_unavailable=True,
        admission=admission,
    )
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {"error": "Authentication is unavailable"}
    assert admission.user_ids == []
    assert service.requests == []


def test_semantic_product_streams_canonical_events_for_trusted_user() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(
        service,
        authenticated=True,
        admission=admission,
    )
    try:
        first = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
        second = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["x-accel-buffering"] == "no"
    assert [payload["type"] for payload in _sse_payloads(first.text)] == [
        "scene_stream_started",
        "semantic_scene_patch",
        "scene_stream_completed",
    ]
    assert admission.user_ids == [
        AUTHENTICATED_USER["id"],
        AUTHENTICATED_USER["id"],
    ]
    assert len(service.requests) == 2
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()
    assert not hasattr(service.requests[0], "user_id")


def test_semantic_product_declines_without_mutating_revision() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_declined_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(
        service,
        authenticated=True,
        admission=admission,
    )
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert _sse_payloads(response.text)[-1] == {
        "type": "semantic_scene_stream_declined",
        "generation": 7,
        "attempt": 1,
        "finalRevision": 0,
        "reasonCode": "unsupported_intent",
        "message": "No supported visual change is available.",
    }
    assert admission.user_ids == [AUTHENTICATED_USER["id"]]
    assert len(service.requests) == 1


@pytest.mark.parametrize(
    "body_mutation",
    [
        pytest.param(
            {"userId": "attacker"},
            id="client-authored-identity",
        ),
        pytest.param(
            {"baseSemanticScene": {"revision": 1, "components": []}},
            id="mismatched-frontier",
        ),
    ],
)
def test_semantic_product_rejects_untrusted_request_fields_before_admission(
    body_mutation: dict[str, object],
) -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    body = {**_request_body(), **body_mutation}
    client = _test_client(
        service,
        authenticated=True,
        admission=admission,
    )
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=body,
        )
    finally:
        client.close()

    assert response.status_code == 422
    assert admission.user_ids == []
    assert service.requests == []


def test_semantic_product_stream_is_default_off_for_authenticated_users() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(
        service,
        authenticated=True,
        admission=admission,
        scene_authoring_enabled=False,
    )
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {"error": "Live scene generation is not enabled"}
    assert admission.user_ids == []
    assert service.requests == []


def test_semantic_product_rejects_before_provider_stream_when_admission_is_full() -> None:
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RejectingSceneAuthoringAdmission()
    client = _test_client(
        service,
        authenticated=True,
        admission=admission,
    )
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 429
    assert response.json() == {"error": "Visual generation is busy."}
    assert admission.user_ids == [AUTHENTICATED_USER["id"]]
    assert service.requests == []


def test_lab_flag_does_not_bypass_semantic_product_authentication(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeSemanticSceneAuthoringService(_semantic_events())
    admission = RecordingSceneAuthoringAdmission()
    client = _test_client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/semantic/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert response.json() == {"error": "Not authenticated"}
    assert admission.user_ids == []
    assert service.requests == []

"""HTTP ownership and guard tests for the separate choreography stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import murmur.api.application as application
import murmur.api.routers.live_scenes as live_scenes
import pytest
from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError
from murmur.live_scene import SceneAdmissionError, SceneAuthoringAdmission
from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneStreamDeclinedEvent,
    ChoreographySceneStreamEvent,
)
from murmur.live_scene.contracts import SceneStreamStartedEvent
from murmur.live_scene.parametric_choreography_requests import (
    ParametricChoreographyRequestV3,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    ParametricChoreographySceneStreamDeclinedEventV3,
    ParametricChoreographySceneStreamEventV3,
)
from murmur.live_scene.projectile_motion_requests import ProjectileMotionRequestV1
from murmur.live_scene.projectile_motion_service_contracts import (
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographySceneStreamDeclinedEventV1,
    ProjectileChoreographySceneStreamEventV1,
)
from murmur.live_scene.semantic_contracts import VisualActAbstainReason
from murmur.live_scene.semantic_service_contracts import SemanticLiveSceneRequest

AUTHENTICATED_USER = {
    "id": "choreography-user",
    "email": "choreography-user@example.com",
    "name": "Choreography User",
}


def _request_body() -> dict[str, object]:
    return {
        "prompt": "Teach completing the square visually.",
        "generation": 11,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
    }


def _parametric_request_body() -> dict[str, object]:
    return {
        "protocol": "parametric_choreography_v3",
        "problemText": "x² + 8x = 20",
        "generation": 12,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
        "routingMode": "reflex",
        "requestedRoute": {"intent": "advance", "targetStage": "setup"},
    }


def _projectile_request_body() -> dict[str, object]:
    return {
        "protocol": "projectile_choreography_v1",
        "problemSpec": {"v": 1, "speedMps": 20, "angleDeg": 45},
        "generation": 13,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
        "routingMode": "reflex",
        "requestedRoute": {"intent": "advance", "targetStage": "setup"},
    }


def _events() -> tuple[ChoreographySceneStreamEvent, ...]:
    return (
        SceneStreamStartedEvent(generation=11, attempt=1, base_revision=0),
        ChoreographySceneStreamDeclinedEvent(
            generation=11,
            attempt=1,
            final_revision=0,
            reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
            message="No supported choreography is available.",
        ),
    )


def _parametric_events() -> tuple[ParametricChoreographySceneStreamEventV3, ...]:
    return (
        SceneStreamStartedEvent(generation=12, attempt=1, base_revision=0),
        ParametricChoreographySceneStreamDeclinedEventV3(
            generation=12,
            attempt=1,
            final_revision=0,
            reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
            message="No supported parametric choreography is available.",
        ),
    )


def _projectile_events() -> tuple[ProjectileChoreographySceneStreamEventV1, ...]:
    return (
        SceneStreamStartedEvent(generation=13, attempt=1, base_revision=0),
        ProjectileChoreographySceneStreamDeclinedEventV1(
            generation=13,
            attempt=1,
            final_revision=0,
            reason_code=ProjectileChoreographyDeclineReason.UNSUPPORTED_INTENT,
            message="No supported projectile choreography is available.",
        ),
    )


class FakeChoreographyService:
    def __init__(self) -> None:
        self.requests: list[SemanticLiveSceneRequest] = []
        self.parametric_requests: list[ParametricChoreographyRequestV3] = []
        self.projectile_requests: list[ProjectileMotionRequestV1] = []
        self.semantic_calls = 0

    async def stream_routed_choreography_events(
        self,
        request: SemanticLiveSceneRequest,
    ) -> AsyncIterator[ChoreographySceneStreamEvent]:
        self.requests.append(request)
        for event in _events():
            yield event

    async def stream_parametric_choreography_events(
        self,
        request: ParametricChoreographyRequestV3,
    ) -> AsyncIterator[ParametricChoreographySceneStreamEventV3]:
        self.parametric_requests.append(request)
        for event in _parametric_events():
            yield event

    async def stream_projectile_choreography_events(
        self,
        request: ProjectileMotionRequestV1,
    ) -> AsyncIterator[ProjectileChoreographySceneStreamEventV1]:
        self.projectile_requests.append(request)
        for event in _projectile_events():
            yield event

    async def stream_routed_semantic_events(self, _request: object) -> AsyncIterator[object]:
        self.semantic_calls += 1
        if False:
            yield None


class RecordingAdmission(SceneAuthoringAdmission):
    def __init__(self, *, reject: bool = False) -> None:
        super().__init__(global_limit=1, per_user_limit=1, requests_per_minute=10)
        self.identities: list[str] = []
        self.reject = reject

    async def acquire(self, user_id: str):
        self.identities.append(user_id)
        if self.reject:
            raise SceneAdmissionError("capacity_reached", "Visual generation is busy.")
        return await super().acquire(user_id)


def _client(
    service: FakeChoreographyService,
    *,
    authenticated: bool = False,
    admission: SceneAuthoringAdmission | None = None,
    client_host: str = "127.0.0.1",
) -> TestClient:
    app = application.create_application(
        scene_authoring_service=service,  # type: ignore[arg-type]
        scene_authoring_admission=admission,
        scene_authoring_enabled=True,
    )

    def authenticate() -> dict[str, str]:
        if not authenticated:
            raise ApiError(401, "Not authenticated")
        return AUTHENTICATED_USER

    app.dependency_overrides[get_authenticated_user] = authenticate
    return TestClient(app, client=(client_host, 50_000))


def _payloads(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(block.removeprefix("data: ")) for block in response_text.split("\n\n") if block
    ]


def test_product_choreography_stream_requires_authentication() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == []


def test_product_parametric_choreography_stream_requires_authentication() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_parametric_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == []


def test_product_projectile_choreography_stream_requires_authentication() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_projectile_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == []


def test_product_choreography_stream_uses_distinct_service_path() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, authenticated=True, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "choreography_scene_stream_declined",
    ]
    assert service.semantic_calls == 0
    assert len(service.requests) == 1
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()
    assert admission.identities == [AUTHENTICATED_USER["id"]]


def test_product_parametric_choreography_uses_v3_service_and_encoder() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, authenticated=True, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_parametric_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "parametric_choreography_scene_stream_declined",
    ]
    assert service.requests == []
    assert len(service.parametric_requests) == 1
    assert service.projectile_requests == []
    assert (
        service.parametric_requests[0].model_dump(mode="json", by_alias=True)
        == _parametric_request_body()
    )
    assert admission.identities == [AUTHENTICATED_USER["id"]]


def test_product_projectile_choreography_uses_v1_service_and_encoder() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, authenticated=True, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_projectile_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "projectile_choreography_scene_stream_declined",
    ]
    assert service.requests == []
    assert service.parametric_requests == []
    assert len(service.projectile_requests) == 1
    assert (
        service.projectile_requests[0].model_dump(mode="json", by_alias=True)
        == _projectile_request_body()
    )
    assert admission.identities == [AUTHENTICATED_USER["id"]]


def test_missing_protocol_remains_the_exact_v2_request() -> None:
    service = FakeChoreographyService()
    client = _client(service, authenticated=True)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert len(service.requests) == 1
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()


@pytest.mark.parametrize(
    ("body", "unknown_protocol"),
    [
        (_parametric_request_body(), "parametric_choreography_v999"),
        (_projectile_request_body(), "projectile_choreography_v999"),
    ],
)
def test_unknown_protocol_is_not_reinterpreted_as_v2(
    body: dict[str, object],
    unknown_protocol: str,
) -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, authenticated=True, admission=admission)
    body["protocol"] = unknown_protocol
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=body,
        )
    finally:
        client.close()

    assert response.status_code == 422
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == []


def test_choreography_lab_is_hidden_without_server_guards(monkeypatch) -> None:
    monkeypatch.delenv("MURMUR_SCENE_LAB", raising=False)
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeChoreographyService()
    client = _client(service)
    try:
        response = client.post(
            "/api/live-scenes/lab/choreography/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 404
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []


def test_choreography_lab_streams_only_on_guarded_loopback(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/lab/choreography/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "choreography_scene_stream_declined",
    ]
    assert len(service.requests) == 1
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == [live_scenes._DEVELOPMENT_SCENE_LAB_IDENTITY]


def test_choreography_lab_dispatches_parametric_v3_on_guarded_loopback(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/lab/choreography/stream",
            json=_parametric_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "parametric_choreography_scene_stream_declined",
    ]
    assert service.requests == []
    assert len(service.parametric_requests) == 1
    assert service.projectile_requests == []
    assert admission.identities == [live_scenes._DEVELOPMENT_SCENE_LAB_IDENTITY]


def test_choreography_lab_dispatches_projectile_v1_on_guarded_loopback(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/lab/choreography/stream",
            json=_projectile_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert [event["type"] for event in _payloads(response.text)] == [
        "scene_stream_started",
        "projectile_choreography_scene_stream_declined",
    ]
    assert service.requests == []
    assert service.parametric_requests == []
    assert len(service.projectile_requests) == 1
    assert admission.identities == [live_scenes._DEVELOPMENT_SCENE_LAB_IDENTITY]


def test_choreography_lab_rejects_non_loopback_before_admission(monkeypatch) -> None:
    monkeypatch.setenv("MURMUR_SCENE_LAB", "1")
    monkeypatch.setattr(live_scenes.config, "MURMUR_ENVIRONMENT", "development")
    service = FakeChoreographyService()
    admission = RecordingAdmission()
    client = _client(service, admission=admission, client_host="192.0.2.20")
    try:
        response = client.post(
            "/api/live-scenes/lab/choreography/stream",
            json=_projectile_request_body(),
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
    finally:
        client.close()

    assert response.status_code == 404
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == []


def test_choreography_capacity_rejection_happens_before_service_call() -> None:
    service = FakeChoreographyService()
    admission = RecordingAdmission(reject=True)
    client = _client(service, authenticated=True, admission=admission)
    try:
        response = client.post(
            "/api/live-scenes/choreography/stream",
            json=_projectile_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 429
    assert service.requests == []
    assert service.parametric_requests == []
    assert service.projectile_requests == []
    assert admission.identities == [AUTHENTICATED_USER["id"]]

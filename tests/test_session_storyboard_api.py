"""Ownership and cleanup tests for the conversation-bound Gate 1.8 stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import murmur.api.application as application
import pytest
from fastapi.testclient import TestClient
from murmur.api.dependencies import get_authenticated_user
from murmur.api.errors import ApiError
from murmur.api.routers.live_scenes import _stream_semantic_storyboard_scene
from murmur.live_scene import SceneAdmissionError, SceneAuthoringAdmission
from murmur.live_scene.semantic_storyboard_requests import (
    SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER,
    SemanticStoryboardRequestV1,
)
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SemanticStoryboardFailureCode,
    SemanticStoryboardSceneStreamEventV1,
    SemanticStoryboardSceneStreamFailedEventV1,
    SemanticStoryboardSceneStreamStartedEventV1,
)
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from starlette.requests import ClientDisconnect

USER = {
    "id": "storyboard-owner",
    "email": "storyboard-owner@example.com",
    "name": "Storyboard Owner",
}
SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"
AGENT_ID = "c99c105b-d099-438d-812d-3af187c0118e"


def _request_body() -> dict[str, object]:
    return {
        "protocol": "projectile_comparison_storyboard_v1",
        "problemSpec": {"v": 1, "speedMps": 20, "anglesDeg": [30, 60]},
        "generation": 14,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
        "routingMode": "reflex",
    }


def _events() -> tuple[SemanticStoryboardSceneStreamEventV1, ...]:
    return (
        SemanticStoryboardSceneStreamStartedEventV1(
            generation=14,
            attempt=1,
            base_revision=0,
        ),
        SemanticStoryboardSceneStreamFailedEventV1(
            generation=14,
            attempt=1,
            base_revision=0,
            code=SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR,
            message="The storyboard could not be certified safely.",
            last_accepted_revision=0,
            retryable=False,
        ),
    )


class _StoryboardService:
    def __init__(self) -> None:
        self.requests: list[SemanticStoryboardRequestV1] = []

    async def stream_semantic_storyboard_events(
        self,
        request: SemanticStoryboardRequestV1,
    ) -> AsyncIterator[SemanticStoryboardSceneStreamEventV1]:
        self.requests.append(request)
        for event in _events():
            yield event


class _ClosingEvents:
    def __init__(self) -> None:
        self._events = iter(_events())
        self.closed = False

    def __aiter__(self) -> _ClosingEvents:
        return self

    async def __anext__(self) -> SemanticStoryboardSceneStreamEventV1:
        if self.closed:
            raise StopAsyncIteration
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


class _ClosingStoryboardService:
    def __init__(self, events: _ClosingEvents) -> None:
        self.events = events

    def stream_semantic_storyboard_events(
        self,
        _request: SemanticStoryboardRequestV1,
    ) -> AsyncIterator[SemanticStoryboardSceneStreamEventV1]:
        return self.events


class _RaisingStoryboardService:
    def stream_semantic_storyboard_events(
        self,
        _request: SemanticStoryboardRequestV1,
    ) -> AsyncIterator[SemanticStoryboardSceneStreamEventV1]:
        raise RuntimeError("provider construction failed")


class _LookupHarness:
    def __init__(self, *, session: object | None, agent: object | None) -> None:
        self.session = session
        self.agent = agent
        self.session_ids: list[str] = []
        self.agent_ids: list[str] = []

    def get_session(self, session_id: str) -> object | None:
        self.session_ids.append(session_id)
        return self.session

    def get_agent(self, agent_id: str) -> object | None:
        self.agent_ids.append(agent_id)
        return self.agent


class _RecordingAdmission(SceneAuthoringAdmission):
    def __init__(self) -> None:
        super().__init__(global_limit=1, per_user_limit=1, requests_per_minute=10)
        self.identities: list[str] = []

    async def acquire(self, identity: str):
        self.identities.append(identity)
        return await super().acquire(identity)


class _RejectingAdmission(_RecordingAdmission):
    async def acquire(self, identity: str):
        self.identities.append(identity)
        raise SceneAdmissionError("capacity_reached", "Visual generation is busy.")


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authenticated: bool,
    session: object | None,
    agent: object | None,
    reject_admission: bool = False,
) -> tuple[TestClient, _StoryboardService, _RecordingAdmission, _LookupHarness]:
    service = _StoryboardService()
    admission = _RejectingAdmission() if reject_admission else _RecordingAdmission()
    lookups = _LookupHarness(session=session, agent=agent)
    monkeypatch.setattr(SessionRepo, "get_by_id", lookups.get_session)
    monkeypatch.setattr(AgentRepo, "get_by_id", lookups.get_agent)

    app = application.create_application(
        scene_authoring_service=service,  # type: ignore[arg-type]
        scene_authoring_admission=admission,
        scene_authoring_enabled=True,
    )

    def authenticate() -> dict[str, str | None]:
        if not authenticated:
            raise ApiError(401, "Not authenticated")
        return USER

    app.dependency_overrides[get_authenticated_user] = authenticate
    return TestClient(app), service, admission, lookups


def _owned_session() -> SimpleNamespace:
    return SimpleNamespace(id=SESSION_ID, user_id=USER["id"], agent_id=AGENT_ID)


def _owned_agent() -> SimpleNamespace:
    return SimpleNamespace(id=AGENT_ID, user_id=USER["id"])


def _payloads(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(block.removeprefix("data: ")) for block in response_text.split("\n\n") if block
    ]


def test_session_storyboard_requires_authentication_before_ownership_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, admission, lookups = _client(
        monkeypatch,
        authenticated=False,
        session=_owned_session(),
        agent=_owned_agent(),
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 401
    assert response.json() == {"error": "Not authenticated"}
    assert lookups.session_ids == []
    assert lookups.agent_ids == []
    assert admission.identities == []
    assert service.requests == []


@pytest.mark.parametrize(
    ("authenticated", "session", "expected_status", "expected_error"),
    [
        (False, _owned_session(), 401, "Not authenticated"),
        (
            True,
            SimpleNamespace(id=SESSION_ID, user_id="another-user", agent_id=AGENT_ID),
            403,
            "Forbidden",
        ),
    ],
)
def test_identity_failures_precede_malformed_body_details(
    monkeypatch: pytest.MonkeyPatch,
    authenticated: bool,
    session: object,
    expected_status: int,
    expected_error: str,
) -> None:
    client, service, admission, _lookups = _client(
        monkeypatch,
        authenticated=authenticated,
        session=session,
        agent=_owned_agent(),
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json={"protocol": "attacker-authored-invalid-protocol"},
        )
    finally:
        client.close()

    assert response.status_code == expected_status
    assert response.json() == {"error": expected_error}
    assert admission.identities == []
    assert service.requests == []


@pytest.mark.parametrize(
    ("session", "expected_status", "expected_error", "expected_agent_ids"),
    [
        (None, 404, "Session not found", []),
        (
            SimpleNamespace(id=SESSION_ID, user_id="another-user", agent_id=AGENT_ID),
            403,
            "Forbidden",
            [],
        ),
    ],
)
def test_session_storyboard_rejects_missing_or_foreign_session_before_admission(
    monkeypatch: pytest.MonkeyPatch,
    session: object | None,
    expected_status: int,
    expected_error: str,
    expected_agent_ids: list[str],
) -> None:
    client, service, admission, lookups = _client(
        monkeypatch,
        authenticated=True,
        session=session,
        agent=_owned_agent(),
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == expected_status
    assert response.json() == {"error": expected_error}
    assert lookups.session_ids == [SESSION_ID]
    assert lookups.agent_ids == expected_agent_ids
    assert admission.identities == []
    assert service.requests == []


@pytest.mark.parametrize(
    ("agent", "expected_status", "expected_error"),
    [
        (None, 404, "Agent not found"),
        (
            SimpleNamespace(id=AGENT_ID, user_id="another-user"),
            403,
            "Forbidden",
        ),
        (
            SimpleNamespace(id="different-agent", user_id=USER["id"]),
            403,
            "Forbidden",
        ),
    ],
)
def test_session_storyboard_validates_the_session_bound_agent_before_admission(
    monkeypatch: pytest.MonkeyPatch,
    agent: object | None,
    expected_status: int,
    expected_error: str,
) -> None:
    client, service, admission, lookups = _client(
        monkeypatch,
        authenticated=True,
        session=_owned_session(),
        agent=agent,
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == expected_status
    assert response.json() == {"error": expected_error}
    assert lookups.agent_ids == [AGENT_ID]
    assert admission.identities == []
    assert service.requests == []


def test_owned_session_streams_the_exact_gate_18_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, admission, lookups = _client(
        monkeypatch,
        authenticated=True,
        session=_owned_session(),
        agent=_owned_agent(),
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert [event["type"] for event in _payloads(response.text)] == [
        "semantic_storyboard_scene_stream_started",
        "semantic_storyboard_scene_stream_failed",
    ]
    assert lookups.session_ids == [SESSION_ID]
    assert lookups.agent_ids == [AGENT_ID]
    assert admission.identities == [USER["id"]]
    assert len(service.requests) == 1
    assert service.requests[0].model_dump(mode="json", by_alias=True) == _request_body()


def test_owned_session_admission_rejection_never_dispatches_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, admission, _lookups = _client(
        monkeypatch,
        authenticated=True,
        session=_owned_session(),
        agent=_owned_agent(),
        reject_admission=True,
    )
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=_request_body(),
        )
    finally:
        client.close()

    assert response.status_code == 429
    assert response.json() == {"error": "Visual generation is busy."}
    assert admission.identities == [USER["id"]]
    assert service.requests == []


@pytest.mark.parametrize("identity_field", ["userId", "agentId", "sessionId"])
def test_session_storyboard_never_accepts_body_identity(
    monkeypatch: pytest.MonkeyPatch,
    identity_field: str,
) -> None:
    client, service, admission, _lookups = _client(
        monkeypatch,
        authenticated=True,
        session=_owned_session(),
        agent=_owned_agent(),
    )
    body = {**_request_body(), identity_field: "client-authored-identity"}
    try:
        response = client.post(
            f"/api/sessions/{SESSION_ID}/storyboard/stream",
            json=body,
        )
    finally:
        client.close()

    assert response.status_code == 422
    assert admission.identities == []
    assert service.requests == []


@pytest.mark.asyncio
async def test_session_storyboard_response_closes_events_and_lease_on_disconnect() -> None:
    body = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(_request_body())
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )
    events = _ClosingEvents()
    response = await _stream_semantic_storyboard_scene(
        body,
        admission_identity=USER["id"],
        admission=admission,
        scene_service=_ClosingStoryboardService(events),  # type: ignore[arg-type]
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/sessions/{SESSION_ID}/storyboard/stream",
        "raw_path": f"/api/sessions/{SESSION_ID}/storyboard/stream".encode(),
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
    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()


@pytest.mark.asyncio
async def test_session_storyboard_releases_lease_when_stream_construction_fails() -> None:
    body = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(_request_body())
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )

    with pytest.raises(RuntimeError, match="provider construction failed"):
        await _stream_semantic_storyboard_scene(
            body,
            admission_identity=USER["id"],
            admission=admission,
            scene_service=_RaisingStoryboardService(),  # type: ignore[arg-type]
        )

    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()

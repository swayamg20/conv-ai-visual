"""Paid chat request and stream-ownership safety contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import murmur.api.application as application
import pytest
from murmur.api.errors import ApiError
from murmur.api.routers.chat import chat
from murmur.api.schemas import MAX_CHAT_MESSAGE_CHARS, ChatMessage
from murmur.chat import ChatAdmission, ChatAdmissionError
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

USER = {"id": "chat-user", "email": "chat-user@example.com", "name": "Chat User"}


class _ChatEvents:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self._events = iter(({"type": "chunk", "text": "hello"}, {"type": "done"}))
        self._error = error
        self.closed = False

    def __aiter__(self) -> _ChatEvents:
        return self

    async def __anext__(self) -> dict[str, str]:
        if self._error is not None:
            error = self._error
            self._error = None
            raise error
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


class _ChatService:
    def __init__(
        self,
        events: _ChatEvents,
        *,
        prepare_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.prepare_error = prepare_error
        self.prepared: list[tuple[str, object]] = []

    def prepare_turn(self, user_id: str, request: object) -> object:
        self.prepared.append((user_id, request))
        if self.prepare_error is not None:
            raise self.prepare_error
        return SimpleNamespace(user_id=user_id)

    def stream_events(self, _turn: object) -> AsyncIterator[dict[str, str]]:
        return self.events


def _scope() -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }


async def _receive_disconnect() -> dict[str, str]:
    return {"type": "http.disconnect"}


def test_chat_message_has_a_hard_input_cap() -> None:
    assert len(ChatMessage(message="x" * MAX_CHAT_MESSAGE_CHARS).message) == 4_000

    with pytest.raises(ValidationError, match="at most 4000 characters"):
        ChatMessage(message="x" * (MAX_CHAT_MESSAGE_CHARS + 1))


@pytest.mark.asyncio
async def test_chat_admission_enforces_per_user_and_global_concurrency() -> None:
    admission = ChatAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )
    lease = await admission.acquire(USER["id"])

    with pytest.raises(ChatAdmissionError, match="already active") as limited:
        await admission.acquire(USER["id"])
    assert limited.value.code == "user_busy"

    with pytest.raises(ChatAdmissionError, match="capacity is busy") as full:
        await admission.acquire("another-user")
    assert full.value.code == "capacity_reached"

    await lease.aclose()
    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()


@pytest.mark.asyncio
async def test_chat_rolling_minute_limit_is_global_across_users() -> None:
    now = 100.0
    admission = ChatAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=2,
        clock=lambda: now,
    )
    for user_id in ("user-a", "user-b"):
        lease = await admission.acquire(user_id)
        await lease.aclose()

    with pytest.raises(ChatAdmissionError, match="Too many chat requests") as limited:
        await admission.acquire("user-c")
    assert limited.value.code == "rate_limited"

    now += 60.0
    replacement = await admission.acquire("user-c")
    await replacement.aclose()


@pytest.mark.asyncio
async def test_chat_holds_admission_until_sse_completion() -> None:
    events = _ChatEvents()
    service = _ChatService(events)
    admission = ChatAdmission(global_limit=1, per_user_limit=1, requests_per_minute=10)
    response = await chat(  # type: ignore[arg-type]
        ChatMessage(message="Explain vectors"),
        USER,
        service,
        admission,
    )

    with pytest.raises(ApiError) as rejected:
        await chat(  # type: ignore[arg-type]
            ChatMessage(message="Start another response"),
            USER,
            service,
            admission,
        )
    assert rejected.value.status_code == 429
    assert str(rejected.value) == "A chat response is already active for this account."
    assert len(service.prepared) == 1

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await response(_scope(), _receive_disconnect, send)  # type: ignore[arg-type]

    assert events.closed is True
    assert any(message["type"] == "http.response.body" for message in sent)
    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()


@pytest.mark.asyncio
async def test_chat_releases_admission_when_client_send_disconnects() -> None:
    events = _ChatEvents()
    service = _ChatService(events)
    admission = ChatAdmission(global_limit=1, per_user_limit=1, requests_per_minute=10)
    response = await chat(  # type: ignore[arg-type]
        ChatMessage(message="Explain vectors"),
        USER,
        service,
        admission,
    )

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(_scope(), _receive_disconnect, send)  # type: ignore[arg-type]

    assert events.closed is True
    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()


@pytest.mark.asyncio
async def test_chat_releases_admission_on_stream_and_prepare_errors() -> None:
    admission = ChatAdmission(global_limit=1, per_user_limit=1, requests_per_minute=10)
    stream_events = _ChatEvents(error=RuntimeError("stream failed"))
    stream_response = await chat(  # type: ignore[arg-type]
        ChatMessage(message="Explain vectors"),
        USER,
        _ChatService(stream_events),
        admission,
    )

    async def send(_message: dict[str, object]) -> None:
        return None

    with pytest.raises(RuntimeError, match="stream failed"):
        await stream_response(_scope(), _receive_disconnect, send)  # type: ignore[arg-type]
    assert stream_events.closed is True

    prepare_error = RuntimeError("prepare failed")
    with pytest.raises(RuntimeError, match="prepare failed"):
        await chat(  # type: ignore[arg-type]
            ChatMessage(message="Explain vectors"),
            USER,
            _ChatService(_ChatEvents(), prepare_error=prepare_error),
            admission,
        )

    replacement = await admission.acquire(USER["id"])
    await replacement.aclose()


def test_azure_pilot_sets_conservative_paid_chat_bounds() -> None:
    apps_bicep = (Path(__file__).resolve().parents[1] / "infra/azure/apps.bicep").read_text(
        encoding="utf-8"
    )

    assert "name: 'LLM_MAX_TOKENS'\n              value: '1024'" in apps_bicep
    assert "name: 'MURMUR_CHAT_GLOBAL_CONCURRENCY'\n              value: '1'" in apps_bicep
    assert "name: 'MURMUR_CHAT_PER_USER_CONCURRENCY'\n              value: '1'" in apps_bicep
    assert "name: 'MURMUR_CHAT_REQUESTS_PER_MINUTE'\n              value: '2'" in apps_bicep
    assert "name: 'MURMUR_CHAT_MAX_TOOL_ROUNDS'\n              value: '2'" in apps_bicep
    assert "name: 'MURMUR_CHAT_LLM_TRANSPORT_MAX_RETRIES'\n              value: '0'" in apps_bicep


def test_application_composes_chat_admission_from_config(monkeypatch) -> None:
    monkeypatch.setattr(application.config, "MURMUR_CHAT_GLOBAL_CONCURRENCY", 3)
    monkeypatch.setattr(application.config, "MURMUR_CHAT_PER_USER_CONCURRENCY", 2)
    monkeypatch.setattr(application.config, "MURMUR_CHAT_REQUESTS_PER_MINUTE", 7)

    app = application.create_application()
    admission = app.state.chat_admission

    assert admission._global_limit == 3
    assert admission._per_user_limit == 2
    assert admission._requests_per_minute == 7
    assert admission._global_requests_per_minute == 7

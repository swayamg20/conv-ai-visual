"""Lifecycle contracts for the process-local runtime registry."""

import asyncio
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient
from murmur.persistence.repositories.tools import ToolRepo
from murmur.runtime import RuntimeRegistry

import main


class _FakePeer:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeSmartTurnSession:
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


@pytest.mark.asyncio
async def test_runtime_shutdown_is_complete_and_idempotent() -> None:
    registry = RuntimeRegistry()
    cancelled = 0

    async def wait_forever() -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled += 1

    registry.sweeper_task = asyncio.create_task(wait_forever())
    registry.background_tasks.add(asyncio.create_task(wait_forever()))

    peer = _FakePeer()
    smart_turn = _FakeSmartTurnSession()
    voice_session = registry.register_voice(
        "peer",
        peer,
        user_id="user",
        agent_id="agent",
        persistent_session_id="session",
        canvas_mode=True,
    )
    voice_session.smart_turn = smart_turn
    voice_session.turn_task = asyncio.create_task(wait_forever())
    registry.register_chat("chat", object(), user_id="user", agent_id="agent")

    await asyncio.sleep(0)
    await registry.shutdown()
    await registry.shutdown()

    assert cancelled == 3
    assert peer.closed is True
    assert smart_turn.cleaned is True
    assert registry.sweeper_task is None
    for runtime_field in fields(registry):
        value = getattr(registry, runtime_field.name)
        if isinstance(value, (dict, set)):
            assert not value, runtime_field.name


def test_application_lifespan_owns_runtime_and_persistence() -> None:
    assert main.app.state.runtime is main.runtime

    with TestClient(main.app) as client:
        assert main.runtime.sweeper_task is not None
        assert client.get("/api/logs").status_code == 401
        assert ToolRepo.get("web_search") is not None

    assert main.runtime.sweeper_task is None
    assert not main.runtime.chat_sessions
    assert not main.runtime.voice_sessions

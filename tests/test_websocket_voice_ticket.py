"""Provider-free contracts for first-party WebSocket admission and PCM framing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from murmur.voice.bootstrap import (
    VoiceBootstrapConflict,
    VoiceBootstrapForbidden,
    VoiceBootstrapUnavailable,
)
from murmur.voice.websocket_protocol import (
    BINARY_HEADER_BYTES,
    VoiceBinaryFrame,
    VoiceBinaryFrameError,
    VoiceFrameKind,
    decode_voice_binary_frame,
    encode_voice_binary_frame,
)
from murmur.voice.websocket_ticket import (
    WebSocketVoiceScope,
    WebSocketVoiceSettings,
    WebSocketVoiceTicketRegistry,
    canonical_allowed_origins,
    normalize_websocket_origin,
)


class _Clock:
    def __init__(self) -> None:
        self.monotonic = 100.0

    def utc(self) -> datetime:
        return datetime(2026, 9, 22, 0, 0, tzinfo=UTC)


def _settings(**overrides) -> WebSocketVoiceSettings:
    values = {
        "allowed_origins": ("https://murmur.example",),
        "ticket_ttl_seconds": 15.0,
        "repository_timeout_seconds": 2.0,
        "max_pending_tickets": 2,
        "max_active_calls": 1,
        "max_call_assignments": 10,
        "max_session_seconds": 900.0,
        "heartbeat_seconds": 15.0,
    }
    values.update(overrides)
    return WebSocketVoiceSettings(**values)


def _scope(call: str = "10000000-0000-4000-8000-000000000001") -> WebSocketVoiceScope:
    return WebSocketVoiceScope(
        user_id="user-1",
        session_id="20000000-0000-4000-8000-000000000002",
        agent_id="30000000-0000-4000-8000-000000000003",
        voice_call_id=call,
    )


def _registry(clock: _Clock, **settings) -> WebSocketVoiceTicketRegistry:
    tokens = iter(("A" * 43, "B" * 43, "C" * 43))
    identifiers = iter(
        (
            "40000000-0000-4000-8000-000000000004",
            "50000000-0000-4000-8000-000000000005",
            "60000000-0000-4000-8000-000000000006",
            "70000000-0000-4000-8000-000000000007",
        )
    )
    return WebSocketVoiceTicketRegistry(
        _settings(**settings),
        utc_clock=clock.utc,
        monotonic_clock=lambda: clock.monotonic,
        ticket_factory=lambda: next(tokens),
        identifier_factory=lambda: next(identifiers),
    )


@pytest.mark.asyncio
async def test_ticket_is_retry_stable_one_use_and_connection_scoped() -> None:
    clock = _Clock()
    registry = _registry(clock)

    first = await registry.issue(_scope())
    retry = await registry.issue(_scope())
    assert retry == first
    assert registry.counts == (1, 0)

    connection = await registry.consume(first.ticket)
    assert connection.scope == _scope()
    assert connection.trace_id == first.trace_id
    assert registry.counts == (0, 1)

    with pytest.raises(VoiceBootstrapForbidden, match="invalid"):
        await registry.consume(first.ticket)
    with pytest.raises(VoiceBootstrapConflict, match="already connected"):
        await registry.issue(_scope())
    await registry.release_scope(_scope())
    await registry.release_scope(_scope())
    assert connection.release_requested.is_set()
    assert registry.counts == (0, 1)
    assert registry.release_intent_count == 1

    await registry.disconnect(connection)
    assert registry.counts == (0, 0)
    with pytest.raises(VoiceBootstrapConflict, match="start a new call"):
        await registry.issue(_scope())


@pytest.mark.asyncio
async def test_release_before_consume_wins_and_is_idempotent() -> None:
    clock = _Clock()
    registry = _registry(clock)
    assignment = await registry.issue(_scope())

    await registry.release_scope(_scope())
    await registry.release_scope(_scope())

    assert registry.counts == (0, 0)
    assert registry.release_intent_count == 1
    with pytest.raises(VoiceBootstrapForbidden, match="invalid"):
        await registry.consume(assignment.ticket)
    with pytest.raises(VoiceBootstrapConflict, match="start a new call"):
        await registry.issue(_scope())


@pytest.mark.asyncio
async def test_release_intent_capacity_fails_closed_until_expiry() -> None:
    clock = _Clock()
    registry = _registry(
        clock,
        max_pending_tickets=1,
        max_active_calls=1,
        max_call_assignments=2,
        max_session_seconds=30,
    )

    await registry.release_scope(_scope())
    await registry.release_scope(_scope("80000000-0000-4000-8000-000000000008"))
    with pytest.raises(VoiceBootstrapUnavailable, match="release-intent capacity"):
        await registry.release_scope(_scope("90000000-0000-4000-8000-000000000009"))
    with pytest.raises(VoiceBootstrapUnavailable, match="cancellation state"):
        await registry.issue(_scope("a0000000-0000-4000-8000-00000000000a"))

    clock.monotonic += 31
    assignment = await registry.issue(_scope("a0000000-0000-4000-8000-00000000000a"))
    assert assignment.voice_call_id == "a0000000-0000-4000-8000-00000000000a"


@pytest.mark.asyncio
async def test_release_after_registry_close_cannot_repopulate_state() -> None:
    clock = _Clock()
    registry = _registry(clock)
    await registry.aclose()

    with pytest.raises(VoiceBootstrapUnavailable, match="closed"):
        await registry.release_scope(_scope())

    assert registry.counts == (0, 0)
    assert registry.release_intent_count == 0


@pytest.mark.asyncio
async def test_ticket_expiry_capacity_and_scope_mismatch_fail_closed() -> None:
    clock = _Clock()
    registry = _registry(clock, max_pending_tickets=1)
    assignment = await registry.issue(_scope())

    mismatched = WebSocketVoiceScope(
        user_id="other-user",
        session_id=_scope().session_id,
        agent_id=_scope().agent_id,
        voice_call_id=_scope().voice_call_id,
    )
    with pytest.raises(VoiceBootstrapForbidden, match="another scope"):
        await registry.issue(mismatched)
    with pytest.raises(VoiceBootstrapUnavailable, match="capacity"):
        await registry.issue(_scope("80000000-0000-4000-8000-000000000008"))

    clock.monotonic += 16
    with pytest.raises(VoiceBootstrapForbidden, match="invalid"):
        await registry.consume(assignment.ticket)
    replacement = await registry.issue(_scope())
    assert replacement.ticket != assignment.ticket


@pytest.mark.asyncio
async def test_exact_connection_identity_prevents_stale_disconnect() -> None:
    clock = _Clock()
    registry = _registry(clock)
    first = await registry.issue(_scope())
    connection = await registry.consume(first.ticket)
    stale = type(connection)(
        connection_id="90000000-0000-4000-8000-000000000009",
        scope=connection.scope,
        trace_id=connection.trace_id,
        profile_id=connection.profile_id,
        event_protocol=connection.event_protocol,
        accepted_at=connection.accepted_at,
        release_requested=connection.release_requested,
    )
    await registry.disconnect(stale)
    assert registry.counts == (0, 1)
    await registry.disconnect(connection)
    assert registry.counts == (0, 0)
    assert registry.release_intent_count == 1
    with pytest.raises(VoiceBootstrapConflict, match="start a new call"):
        await registry.issue(_scope())


def test_binary_frame_round_trip_and_strict_rejection() -> None:
    frame = VoiceBinaryFrame(
        kind=VoiceFrameKind.INPUT_PCM,
        generation=7,
        sequence=11,
        payload=b"\x01\x02" * 640,
    )
    encoded = encode_voice_binary_frame(frame)
    assert len(encoded) == BINARY_HEADER_BYTES + 1_280
    assert decode_voice_binary_frame(encoded) == frame

    with pytest.raises(VoiceBinaryFrameError, match="version"):
        decode_voice_binary_frame(b"NO" + encoded[2:])
    with pytest.raises(VoiceBinaryFrameError, match="sample-aligned"):
        VoiceBinaryFrame(
            kind=VoiceFrameKind.INPUT_PCM,
            generation=0,
            sequence=0,
            payload=b"x",
        )


def test_origins_are_canonical_and_do_not_accept_paths_or_credentials() -> None:
    assert canonical_allowed_origins(
        [" HTTPS://MURMUR.EXAMPLE:443/ ", "https://murmur.example"]
    ) == ("https://murmur.example",)
    assert normalize_websocket_origin("http://localhost:3000/") == "http://localhost:3000"
    with pytest.raises(ValueError, match="invalid"):
        normalize_websocket_origin("https://murmur.example/path")
    with pytest.raises(ValueError, match="invalid"):
        normalize_websocket_origin("https://user:secret@murmur.example")

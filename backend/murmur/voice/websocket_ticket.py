"""Authenticated, one-use admission for the first-party voice WebSocket."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from murmur.core.config import config
from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.bootstrap import (
    VoiceBootstrapConflict,
    VoiceBootstrapForbidden,
    VoiceBootstrapNotFound,
    VoiceBootstrapUnavailable,
)
from murmur.voice.websocket_protocol import (
    WEBSOCKET_VOICE_PROFILE,
    WEBSOCKET_VOICE_PROTOCOL,
    WEBSOCKET_VOICE_RUNTIME,
)

_TICKET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


@dataclass(frozen=True, slots=True)
class WebSocketVoiceSettings:
    allowed_origins: tuple[str, ...]
    ticket_ttl_seconds: float = 15.0
    repository_timeout_seconds: float = 2.0
    max_pending_tickets: int = 100
    max_active_calls: int = 1
    max_call_assignments: int = 10_000
    max_session_seconds: float = 900.0
    heartbeat_seconds: float = 15.0
    send_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise ValueError("voice WebSocket requires at least one allowed origin")
        if any(normalize_websocket_origin(origin) != origin for origin in self.allowed_origins):
            raise ValueError("voice WebSocket origins must be canonical HTTP origins")
        for name, value, lower, upper in (
            ("ticket TTL", self.ticket_ttl_seconds, 1.0, 60.0),
            ("repository timeout", self.repository_timeout_seconds, 0.1, 30.0),
            ("session duration", self.max_session_seconds, 30.0, 7_200.0),
            ("heartbeat interval", self.heartbeat_seconds, 1.0, 60.0),
            ("send timeout", self.send_timeout_seconds, 0.01, 10.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise ValueError(f"voice WebSocket {name} must be between {lower} and {upper}")
        if self.max_pending_tickets <= 0 or self.max_active_calls <= 0:
            raise ValueError("voice WebSocket capacities must be positive")
        if self.max_call_assignments < self.max_pending_tickets + self.max_active_calls:
            raise ValueError(
                "voice WebSocket call-assignment capacity must cover pending and active calls"
            )


@dataclass(frozen=True, slots=True)
class WebSocketVoiceScope:
    user_id: str
    session_id: str
    agent_id: str
    voice_call_id: str


@dataclass(frozen=True, slots=True)
class WebSocketVoiceAssignment:
    runtime: str
    profile_id: str
    event_protocol: str
    websocket_path: str
    ticket: str = field(repr=False)
    session_id: str
    agent_id: str
    voice_call_id: str
    trace_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WebSocketVoiceConnection:
    connection_id: str
    scope: WebSocketVoiceScope
    trace_id: str
    profile_id: str
    event_protocol: str
    accepted_at: datetime
    release_requested: asyncio.Event = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _PendingTicket:
    digest: str
    ticket: str = field(repr=False)
    scope: WebSocketVoiceScope
    trace_id: str
    issued_at: datetime
    expires_at: datetime
    expires_monotonic: float


@dataclass(frozen=True, slots=True)
class _ReleaseIntent:
    scope: WebSocketVoiceScope
    expires_monotonic: float


def _ticket_digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("ascii")).hexdigest()


def is_websocket_ticket(value: object) -> bool:
    return isinstance(value, str) and _TICKET_PATTERN.fullmatch(value) is not None


class WebSocketVoiceTicketRegistry:
    """Own pending tickets and active calls for the one-replica MVP."""

    def __init__(
        self,
        settings: WebSocketVoiceSettings,
        *,
        utc_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        ticket_factory: Callable[[], str] | None = None,
        identifier_factory: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._ticket_factory = ticket_factory or (lambda: secrets.token_urlsafe(32))
        self._identifier_factory = identifier_factory or (lambda: str(uuid4()))
        self._lock = asyncio.Lock()
        self._pending: dict[str, _PendingTicket] = {}
        self._pending_by_call: dict[str, str] = {}
        self._active: dict[str, WebSocketVoiceConnection] = {}
        self._release_intents: dict[str, _ReleaseIntent] = {}
        self._release_overflow_until: float | None = None
        self._closed = False

    async def issue(self, scope: WebSocketVoiceScope) -> WebSocketVoiceAssignment:
        async with self._lock:
            self._ensure_open()
            self._prune_expired_locked()
            release_intent = self._release_intents.get(scope.voice_call_id)
            if release_intent is not None:
                if release_intent.scope != scope:
                    raise VoiceBootstrapForbidden("Voice call belongs to another scope")
                raise VoiceBootstrapConflict("Voice call was released; start a new call")
            if self._release_overflow_until is not None:
                raise VoiceBootstrapUnavailable(
                    "Voice WebSocket cancellation state is saturated; retry later"
                )
            active = self._active.get(scope.voice_call_id)
            if active is not None:
                if active.scope != scope:
                    raise VoiceBootstrapForbidden("Voice call belongs to another scope")
                raise VoiceBootstrapConflict("Voice call is already connected")

            existing_digest = self._pending_by_call.get(scope.voice_call_id)
            if existing_digest is not None:
                existing = self._pending[existing_digest]
                if existing.scope != scope:
                    raise VoiceBootstrapForbidden("Voice call belongs to another scope")
                return self._assignment(existing)

            if len(self._pending) >= self.settings.max_pending_tickets:
                raise VoiceBootstrapUnavailable("Voice WebSocket ticket capacity is exhausted")
            if self._registry_size_locked() >= self._max_registry_entries:
                raise VoiceBootstrapUnavailable("Voice WebSocket registry capacity is exhausted")
            ticket = self._ticket_factory()
            if not is_websocket_ticket(ticket):
                raise VoiceBootstrapUnavailable("Voice WebSocket ticket generation failed")
            digest = _ticket_digest(ticket)
            if digest in self._pending:
                raise VoiceBootstrapUnavailable("Voice WebSocket ticket generation collided")
            issued_at = self._utc_clock()
            pending = _PendingTicket(
                digest=digest,
                ticket=ticket,
                scope=scope,
                trace_id=self._identifier_factory(),
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=self.settings.ticket_ttl_seconds),
                expires_monotonic=self._monotonic_clock() + self.settings.ticket_ttl_seconds,
            )
            self._pending[digest] = pending
            self._pending_by_call[scope.voice_call_id] = digest
            return self._assignment(pending)

    async def consume(self, ticket: str) -> WebSocketVoiceConnection:
        if not is_websocket_ticket(ticket):
            raise VoiceBootstrapForbidden("Voice WebSocket ticket is invalid")
        digest = _ticket_digest(ticket)
        async with self._lock:
            self._ensure_open()
            self._prune_expired_locked()
            pending = self._pending.get(digest)
            if pending is None or not secrets.compare_digest(pending.ticket, ticket):
                raise VoiceBootstrapForbidden("Voice WebSocket ticket is invalid")
            if len(self._active) >= self.settings.max_active_calls:
                raise VoiceBootstrapUnavailable("Voice WebSocket call capacity is exhausted")
            self._remove_pending_locked(pending)
            connection = WebSocketVoiceConnection(
                connection_id=self._identifier_factory(),
                scope=pending.scope,
                trace_id=pending.trace_id,
                profile_id=WEBSOCKET_VOICE_PROFILE,
                event_protocol=WEBSOCKET_VOICE_PROTOCOL,
                accepted_at=self._utc_clock(),
                release_requested=asyncio.Event(),
            )
            self._active[pending.scope.voice_call_id] = connection
            return connection

    async def release_scope(self, scope: WebSocketVoiceScope) -> None:
        async with self._lock:
            self._ensure_open()
            self._prune_expired_locked()
            active = self._active.get(scope.voice_call_id)
            if active is not None:
                if active.scope != scope:
                    raise VoiceBootstrapForbidden("Voice call belongs to another scope")
            digest = self._pending_by_call.get(scope.voice_call_id)
            if digest is not None:
                pending = self._pending[digest]
                if pending.scope != scope:
                    raise VoiceBootstrapForbidden("Voice call belongs to another scope")
                self._remove_pending_locked(pending)
            existing = self._release_intents.get(scope.voice_call_id)
            if existing is not None and existing.scope != scope:
                raise VoiceBootstrapForbidden("Voice call belongs to another scope")
            expires_monotonic = self._monotonic_clock() + self.settings.max_session_seconds
            if existing is None and active is None and digest is None:
                if self._registry_size_locked() >= self._max_registry_entries:
                    self._release_overflow_until = max(
                        self._release_overflow_until or 0.0,
                        expires_monotonic,
                    )
                    raise VoiceBootstrapUnavailable(
                        "Voice WebSocket release-intent capacity is exhausted"
                    )
            self._release_intents[scope.voice_call_id] = _ReleaseIntent(
                scope=scope,
                expires_monotonic=expires_monotonic,
            )
            if active is not None:
                active.release_requested.set()

    async def disconnect(self, connection: WebSocketVoiceConnection) -> None:
        async with self._lock:
            retained = self._active.get(connection.scope.voice_call_id)
            if retained is not None and retained.connection_id == connection.connection_id:
                self._active.pop(connection.scope.voice_call_id, None)
                existing = self._release_intents.get(connection.scope.voice_call_id)
                expires_monotonic = self._monotonic_clock() + self.settings.max_session_seconds
                self._release_intents[connection.scope.voice_call_id] = _ReleaseIntent(
                    scope=connection.scope,
                    expires_monotonic=max(
                        existing.expires_monotonic if existing is not None else 0.0,
                        expires_monotonic,
                    ),
                )

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            for connection in self._active.values():
                connection.release_requested.set()
            self._pending.clear()
            self._pending_by_call.clear()
            self._active.clear()
            self._release_intents.clear()
            self._release_overflow_until = None

    @property
    def counts(self) -> tuple[int, int]:
        return len(self._pending), len(self._active)

    @property
    def release_intent_count(self) -> int:
        return len(self._release_intents)

    @property
    def _max_registry_entries(self) -> int:
        return self.settings.max_call_assignments

    def _registry_size_locked(self) -> int:
        return len(
            self._pending_by_call.keys() | self._active.keys() | self._release_intents.keys()
        )

    def _assignment(self, pending: _PendingTicket) -> WebSocketVoiceAssignment:
        return WebSocketVoiceAssignment(
            runtime=WEBSOCKET_VOICE_RUNTIME,
            profile_id=WEBSOCKET_VOICE_PROFILE,
            event_protocol=WEBSOCKET_VOICE_PROTOCOL,
            websocket_path="/api/voice/websocket",
            ticket=pending.ticket,
            session_id=pending.scope.session_id,
            agent_id=pending.scope.agent_id,
            voice_call_id=pending.scope.voice_call_id,
            trace_id=pending.trace_id,
            expires_at=pending.expires_at,
        )

    def _prune_expired_locked(self) -> None:
        now = self._monotonic_clock()
        expired = [item for item in self._pending.values() if item.expires_monotonic <= now]
        for pending in expired:
            self._remove_pending_locked(pending)
        expired_releases = [
            voice_call_id
            for voice_call_id, intent in self._release_intents.items()
            if intent.expires_monotonic <= now
        ]
        for voice_call_id in expired_releases:
            self._release_intents.pop(voice_call_id, None)
        if self._release_overflow_until is not None and self._release_overflow_until <= now:
            self._release_overflow_until = None

    def _remove_pending_locked(self, pending: _PendingTicket) -> None:
        self._pending.pop(pending.digest, None)
        if self._pending_by_call.get(pending.scope.voice_call_id) == pending.digest:
            self._pending_by_call.pop(pending.scope.voice_call_id, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise VoiceBootstrapUnavailable("Voice WebSocket admission is closed")


class WebSocketVoiceBootstrapService:
    """Authorize persistent Murmur ownership before minting a one-use ticket."""

    def __init__(
        self,
        registry: WebSocketVoiceTicketRegistry,
        *,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
    ) -> None:
        self.registry = registry
        self.settings = registry.settings
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner

    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> WebSocketVoiceAssignment:
        scope = await self._authorize_scope(user_id, session_id, voice_call_id)
        return await self.registry.issue(scope)

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        scope = await self._authorize_scope(user_id, session_id, voice_call_id)
        await self.registry.release_scope(scope)

    async def consume(self, ticket: str) -> WebSocketVoiceConnection:
        return await self.registry.consume(ticket)

    async def disconnect(self, connection: WebSocketVoiceConnection) -> None:
        await self.registry.disconnect(connection)

    async def aclose(self) -> None:
        await self.registry.aclose()

    async def _authorize_scope(
        self,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> WebSocketVoiceScope:
        try:
            session = await self._repository_runner.run(
                self._session_repo.get_by_id,
                session_id,
                timeout_seconds=self.settings.repository_timeout_seconds,
            )
            if session is None:
                raise VoiceBootstrapNotFound("Voice session was not found")
            if session.user_id != user_id:
                raise VoiceBootstrapForbidden("Forbidden")
            agent = await self._repository_runner.run(
                self._agent_repo.get_by_id,
                session.agent_id,
                timeout_seconds=self.settings.repository_timeout_seconds,
            )
        except (VoiceBootstrapNotFound, VoiceBootstrapForbidden):
            raise
        except (TimeoutError, BoundedSyncRunnerUnavailable) as exc:
            raise VoiceBootstrapUnavailable("Voice session ownership is unavailable") from exc
        except Exception as exc:
            raise VoiceBootstrapUnavailable("Voice session ownership is unavailable") from exc
        if agent is None:
            raise VoiceBootstrapNotFound("Voice agent was not found")
        if agent.user_id != user_id:
            raise VoiceBootstrapForbidden("Forbidden")
        return WebSocketVoiceScope(
            user_id=user_id,
            session_id=session.id,
            agent_id=agent.id,
            voice_call_id=voice_call_id,
        )


class UnavailableWebSocketVoiceBootstrapService:
    def __init__(self, message: str = "Voice WebSocket is unavailable") -> None:
        self._message = message

    async def bootstrap(self, **_scope: str) -> WebSocketVoiceAssignment:
        raise VoiceBootstrapUnavailable(self._message)

    async def release(self, **_scope: str) -> None:
        raise VoiceBootstrapUnavailable(self._message)

    async def consume(self, _ticket: str) -> WebSocketVoiceConnection:
        raise VoiceBootstrapUnavailable(self._message)

    async def disconnect(self, _connection: WebSocketVoiceConnection) -> None: ...

    async def aclose(self) -> None: ...


def canonical_allowed_origins(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalize_websocket_origin(value) for value in values if value and value.strip()
        )
    )


def normalize_websocket_origin(value: str) -> str:
    """Return one exact HTTP origin or reject path, credentials, and ambiguity."""

    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("voice WebSocket origin is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("voice WebSocket origin is invalid")
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    authority = hostname if parsed.port in {None, default_port} else f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), authority, "", "", ""))


def create_default_websocket_voice_service() -> (
    WebSocketVoiceBootstrapService | UnavailableWebSocketVoiceBootstrapService
):
    """Build the first-party transport only when the selected runtime requests it."""

    if str(getattr(config, "VOICE_RUNTIME", "legacy")).strip().lower() != WEBSOCKET_VOICE_RUNTIME:
        return UnavailableWebSocketVoiceBootstrapService(
            "Voice WebSocket is disabled by VOICE_RUNTIME"
        )
    try:
        settings = WebSocketVoiceSettings(
            allowed_origins=canonical_allowed_origins(config.ALLOWED_CORS_ORIGINS),
            ticket_ttl_seconds=float(config.VOICE_WEBSOCKET_TICKET_TTL_SECONDS),
            repository_timeout_seconds=float(config.VOICE_WEBSOCKET_REPOSITORY_TIMEOUT_SECONDS),
            max_pending_tickets=int(config.VOICE_WEBSOCKET_MAX_PENDING_TICKETS),
            max_active_calls=int(config.VOICE_WEBSOCKET_MAX_ACTIVE_CALLS),
            max_call_assignments=int(config.VOICE_WEBSOCKET_MAX_CALL_ASSIGNMENTS),
            max_session_seconds=float(config.VOICE_WEBSOCKET_MAX_SESSION_SECONDS),
            heartbeat_seconds=float(config.VOICE_WEBSOCKET_HEARTBEAT_SECONDS),
            send_timeout_seconds=float(config.VOICE_WEBSOCKET_SEND_TIMEOUT_SECONDS),
        )
    except (TypeError, ValueError) as exc:
        return UnavailableWebSocketVoiceBootstrapService(
            f"Voice WebSocket configuration is invalid: {exc}"
        )
    return WebSocketVoiceBootstrapService(WebSocketVoiceTicketRegistry(settings))


__all__ = [
    "UnavailableWebSocketVoiceBootstrapService",
    "WebSocketVoiceAssignment",
    "WebSocketVoiceBootstrapService",
    "WebSocketVoiceConnection",
    "WebSocketVoiceScope",
    "WebSocketVoiceSettings",
    "WebSocketVoiceTicketRegistry",
    "canonical_allowed_origins",
    "create_default_websocket_voice_service",
    "is_websocket_ticket",
    "normalize_websocket_origin",
]

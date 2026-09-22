#!/usr/bin/env python3
"""Run one source-pinned, provider-free Murmur WebSocket voice canary.

The probe never calls a speech or model provider.  It authenticates the HTTP
bootstrap with a Firebase ID token read from a private file, opens exactly one
WebSocket, exercises the raw echo protocol, and releases the call explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from murmur.api.schemas import WebSocketVoiceSessionBootstrapResponse
from murmur.voice.websocket_protocol import (
    INPUT_FRAME_PCM_BYTES,
    INPUT_SAMPLE_RATE_HZ,
    WEBSOCKET_CANARY_MODE,
    WEBSOCKET_TICKET_PROTOCOL_PREFIX,
    WEBSOCKET_VOICE_PROFILE,
    WEBSOCKET_VOICE_PROTOCOL,
    VoiceBinaryFrame,
    VoiceBinaryFrameError,
    VoiceFrameKind,
    decode_voice_binary_frame,
    encode_voice_binary_frame,
)
from websockets.asyncio.client import connect

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN_MAX_BYTES = 16_000
_JSON_MAX_BYTES = 64 * 1024
_PRIVATE_MODE = 0o600
_DEFAULT_DURATION_SECONDS = 310.0
_REMOTE_MIN_DURATION_SECONDS = 310.0
_DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 25.0
_CONTROL_TIMEOUT_SECONDS = 10.0
_PROFILE = "provider_free_echo"
_CONTROL_KEYS: Mapping[str, frozenset[str]] = {
    "canary_ready": frozenset(
        {
            "type",
            "server_sequence",
            "generation",
            "trace_id",
            "protocol",
            "runtime_mode",
            "session_id",
            "voice_call_id",
            "profile_id",
            "input_sample_rate_hz",
            "output_sample_rate_hz",
            "input_frame_pcm_bytes",
        }
    ),
    "heartbeat": frozenset(
        {"type", "server_sequence", "generation", "trace_id", "elapsed_ms"}
    ),
    "pong": frozenset(
        {"type", "server_sequence", "generation", "trace_id", "client_sequence"}
    ),
    "clear_audio": frozenset({"type", "server_sequence", "generation", "trace_id"}),
    "session_released": frozenset(
        {"type", "server_sequence", "generation", "trace_id"}
    ),
}


class ProbeRefusal(RuntimeError):
    """One stable, secret-free reason the canary cannot be accepted."""

    def __init__(self, code: str, stage: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


class WebSocketClient(Protocol):
    subprotocol: str | None
    close_code: int | None

    async def recv(self) -> str | bytes: ...

    async def send(self, message: str | bytes) -> None: ...

    async def wait_closed(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProbeSettings:
    base_url: str
    origin: str
    expected_sha: str
    session_id: str
    token_file: Path
    evidence_out: Path
    repo_root: Path = Path(__file__).resolve().parents[2]
    duration_seconds: float = _DEFAULT_DURATION_SECONDS
    heartbeat_timeout_seconds: float = _DEFAULT_HEARTBEAT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _canonical_http_origin(self.base_url, "base URL"))
        object.__setattr__(self, "origin", _canonical_http_origin(self.origin, "origin"))
        object.__setattr__(self, "repo_root", self.repo_root.expanduser().resolve())
        if not _FULL_SHA.fullmatch(self.expected_sha):
            raise ProbeRefusal(
                "settings_invalid", "settings", "expected SHA must be 40 lowercase hex characters"
            )
        _require_uuid4(self.session_id, "session ID")
        for name, value, lower, upper in (
            ("duration", self.duration_seconds, 1.0, 900.0),
            ("heartbeat timeout", self.heartbeat_timeout_seconds, 2.0, 60.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise ProbeRefusal(
                    "settings_invalid", "settings", f"{name} is outside its safe range"
                )
        if not _is_loopback_origin(self.base_url) and self.duration_seconds < _REMOTE_MIN_DURATION_SECONDS:
            raise ProbeRefusal(
                "settings_invalid",
                "settings",
                "remote acceptance requires at least 310 seconds",
            )
        if not self.repo_root.is_dir():
            raise ProbeRefusal("settings_invalid", "settings", "repository root is invalid")
        if self.token_file.resolve() == self.evidence_out.resolve():
            raise ProbeRefusal(
                "settings_invalid", "settings", "token and evidence paths must be different"
            )


@dataclass(frozen=True, slots=True)
class Assignment:
    websocket_path: str
    ticket: str = field(repr=False)
    trace_id: str
    voice_call_id: str
    expires_at: datetime


@dataclass(slots=True)
class Counters:
    server_sequence: int = 0
    generation: int = 0
    client_ping_sequence: int = 0
    pcm_sequence: int = 0
    heartbeats: int = 0
    pings: int = 0
    pongs: int = 0
    echoes: int = 0
    interrupts: int = 0


def _canonical_http_origin(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, ValueError):
        raise ProbeRefusal("settings_invalid", "settings", f"{label} is invalid") from None
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProbeRefusal("settings_invalid", "settings", f"{label} is not an HTTP origin")
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if scheme != "https" and not loopback:
        raise ProbeRefusal("settings_invalid", "settings", f"{label} must use HTTPS")
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if scheme == "https" else 80
    authority = rendered_host if port in {None, default_port} else f"{rendered_host}:{port}"
    return urlunsplit((scheme, authority, "", "", ""))


def _is_loopback_origin(value: str) -> bool:
    return (urlsplit(value).hostname or "").casefold() in {"localhost", "127.0.0.1", "::1"}


def _require_uuid4(value: object, label: str) -> str:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except (ValueError, TypeError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        raise ProbeRefusal("settings_invalid", "settings", f"{label} must be a canonical UUID4")
    return value


def read_private_token(path: Path) -> str:
    """Read one ID token without accepting symlinks, shared files, or whitespace."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ProbeRefusal("token_file_invalid", "authentication", "token file is unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != _PRIVATE_MODE:
            raise ProbeRefusal(
                "token_file_invalid",
                "authentication",
                "token file must be a mode-0600 regular file",
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ProbeRefusal("token_file_invalid", "authentication", "token file owner is invalid")
        if metadata.st_size <= 0 or metadata.st_size > _TOKEN_MAX_BYTES:
            raise ProbeRefusal("token_file_invalid", "authentication", "token file size is invalid")
        raw = os.read(descriptor, _TOKEN_MAX_BYTES + 1)
        token = raw.decode("ascii")
    except (OSError, UnicodeDecodeError):
        raise ProbeRefusal("token_file_invalid", "authentication", "token file is invalid") from None
    finally:
        os.close(descriptor)
    token = token.removesuffix("\n").removesuffix("\r")
    if not token or len(token) > _TOKEN_MAX_BYTES or any(character.isspace() for character in token):
        raise ProbeRefusal("token_file_invalid", "authentication", "token file is invalid")
    if any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise ProbeRefusal("token_file_invalid", "authentication", "token file is invalid")
    return token


def _git(repo_root: Path, *arguments: str) -> str:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeRefusal("source_preflight_failed", "source", "source preflight failed") from None
    if result.returncode != 0 or len(result.stdout) > 8_192:
        raise ProbeRefusal("source_preflight_failed", "source", "source preflight failed")
    return result.stdout.strip()


def verify_local_source(settings: ProbeSettings) -> str:
    """Require expected SHA == clean HEAD == the current origin branch tip."""

    root = _git(settings.repo_root, "rev-parse", "--show-toplevel")
    if Path(root).resolve() != settings.repo_root or _git(
        settings.repo_root, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise ProbeRefusal("source_tree_dirty", "source", "source tree is not clean")
    if _git(settings.repo_root, "rev-parse", "--verify", "HEAD") != settings.expected_sha:
        raise ProbeRefusal("source_sha_mismatch", "source", "local source SHA does not match")
    branch = _git(settings.repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch or "\n" in branch:
        raise ProbeRefusal("source_preflight_failed", "source", "source branch is invalid")
    remote_ref = f"refs/heads/{branch}"
    remote = _git(settings.repo_root, "ls-remote", "--exit-code", "origin", remote_ref)
    if remote != f"{settings.expected_sha}\t{remote_ref}":
        raise ProbeRefusal("source_not_pushed", "source", "expected SHA is not the origin branch tip")
    return branch


def _json_response(response: httpx.Response, code: str, stage: str) -> Mapping[str, Any]:
    if len(response.content) > _JSON_MAX_BYTES:
        raise ProbeRefusal(code, stage, f"{stage} response is too large")
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ProbeRefusal(code, stage, f"{stage} response is not JSON") from None
    if not isinstance(payload, dict):
        raise ProbeRefusal(code, stage, f"{stage} response is invalid")
    return payload


def _has_no_store(response: httpx.Response) -> bool:
    directives = {
        item.strip().casefold()
        for item in response.headers.get("cache-control", "").split(",")
        if item.strip()
    }
    return "no-store" in directives


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    code: str,
    stage: str,
    token: str | None = None,
    payload: Mapping[str, str] | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    try:
        response = await client.request(method, path, headers=headers, json=payload)
    except Exception:
        raise ProbeRefusal(code, stage, f"{stage} request failed") from None
    if 300 <= response.status_code < 400:
        raise ProbeRefusal(code, stage, f"{stage} redirect was refused")
    return response


async def verify_source_and_health(
    client: httpx.AsyncClient, expected_sha: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    health_response = await _request(
        client, "GET", "/healthz", code="health_http_failed", stage="health"
    )
    if health_response.status_code != 200 or not _has_no_store(health_response):
        raise ProbeRefusal("health_contract_invalid", "health", "health contract failed")
    health = _json_response(health_response, "health_contract_invalid", "health")
    if health.get("status") != "ok":
        raise ProbeRefusal("health_contract_invalid", "health", "health status is not ok")
    if health.get("release_sha") != expected_sha:
        raise ProbeRefusal("source_sha_mismatch", "health", "deployed source SHA does not match")

    ready_response = await _request(
        client, "GET", "/readyz", code="readiness_http_failed", stage="readiness"
    )
    if ready_response.status_code != 200 or not _has_no_store(ready_response):
        raise ProbeRefusal("readiness_contract_invalid", "readiness", "readiness contract failed")
    readiness = _json_response(
        ready_response, "readiness_contract_invalid", "readiness"
    )
    if readiness.get("status") != "ready":
        raise ProbeRefusal("readiness_contract_invalid", "readiness", "backend is not ready")
    if readiness.get("release_sha") != expected_sha:
        raise ProbeRefusal("source_sha_mismatch", "readiness", "deployed source SHA does not match")
    return health, readiness


async def bootstrap(
    client: httpx.AsyncClient,
    *,
    token: str,
    session_id: str,
    voice_call_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Assignment:
    response = await _request(
        client,
        "POST",
        "/api/voice/websocket/session",
        code="bootstrap_http_failed",
        stage="bootstrap",
        token=token,
        payload={"session_id": session_id, "voice_call_id": voice_call_id},
    )
    if response.status_code != 200 or not _has_no_store(response):
        raise ProbeRefusal(
            "bootstrap_contract_invalid", "bootstrap", "bootstrap contract failed"
        )
    payload = _json_response(response, "bootstrap_contract_invalid", "bootstrap")
    try:
        decoded = WebSocketVoiceSessionBootstrapResponse.model_validate(payload)
    except Exception:
        raise ProbeRefusal(
            "bootstrap_contract_invalid", "bootstrap", "bootstrap payload is invalid"
        ) from None
    if decoded.session_id != session_id or decoded.voice_call_id != voice_call_id:
        raise ProbeRefusal(
            "bootstrap_contract_invalid", "bootstrap", "bootstrap scope does not match"
        )
    current = now()
    remaining = (decoded.expires_at.astimezone(UTC) - current.astimezone(UTC)).total_seconds()
    if remaining <= 1 or remaining > 65:
        raise ProbeRefusal(
            "ticket_expiry_invalid", "bootstrap", "connection ticket lifetime is invalid"
        )
    return Assignment(
        websocket_path=decoded.websocket_path,
        ticket=decoded.ticket,
        trace_id=decoded.trace_id,
        voice_call_id=decoded.voice_call_id,
        expires_at=decoded.expires_at,
    )


async def release(
    client: httpx.AsyncClient,
    *,
    token: str,
    session_id: str,
    voice_call_id: str,
) -> None:
    response = await _request(
        client,
        "POST",
        "/api/voice/websocket/session/end",
        code="release_http_failed",
        stage="release",
        token=token,
        payload={"session_id": session_id, "voice_call_id": voice_call_id},
    )
    if response.status_code != 204 or not _has_no_store(response) or response.content:
        raise ProbeRefusal("release_contract_invalid", "release", "release contract failed")


def _websocket_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    if path != "/api/voice/websocket":
        raise ProbeRefusal(
            "bootstrap_contract_invalid", "bootstrap", "WebSocket path is invalid"
        )
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _decode_control(
    raw: str | bytes,
    *,
    expected_type: str,
    assignment: Assignment,
    counters: Counters,
) -> Mapping[str, Any]:
    if not isinstance(raw, str) or len(raw) > 1_024:
        raise ProbeRefusal("control_contract_invalid", "protocol", "control frame is invalid")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ProbeRefusal("control_contract_invalid", "protocol", "control frame is invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != expected_type
        or set(payload) != _CONTROL_KEYS[expected_type]
    ):
        raise ProbeRefusal(
            "control_contract_invalid", "protocol", f"expected {expected_type} control frame"
        )
    sequence = payload.get("server_sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence != counters.server_sequence + 1
    ):
        raise ProbeRefusal(
            "control_contract_invalid", "protocol", "server control sequence is invalid"
        )
    if payload.get("generation") != counters.generation or payload.get("trace_id") != assignment.trace_id:
        raise ProbeRefusal(
            "control_contract_invalid", "protocol", "server control scope is invalid"
        )
    counters.server_sequence = sequence
    return payload


async def _receive(
    socket: WebSocketClient, deadline_seconds: float, timeout_code: str
) -> str | bytes:
    try:
        return await asyncio.wait_for(socket.recv(), timeout=deadline_seconds)
    except TimeoutError:
        raise ProbeRefusal(timeout_code, "protocol", "WebSocket response timed out") from None
    except Exception:
        raise ProbeRefusal(
            "premature_disconnect", "protocol", "WebSocket closed before acceptance completed"
        ) from None


async def _send(socket: WebSocketClient, value: str | bytes) -> None:
    try:
        await asyncio.wait_for(socket.send(value), timeout=_CONTROL_TIMEOUT_SECONDS)
    except Exception:
        raise ProbeRefusal("websocket_send_failed", "protocol", "WebSocket send failed") from None


async def _ping_and_echo(
    socket: WebSocketClient,
    assignment: Assignment,
    counters: Counters,
) -> None:
    counters.client_ping_sequence += 1
    await _send(
        socket,
        json.dumps(
            {"type": "ping", "sequence": counters.client_ping_sequence},
            separators=(",", ":"),
        ),
    )
    counters.pings += 1
    pong = _decode_control(
        await _receive(socket, _CONTROL_TIMEOUT_SECONDS, "pong_timeout"),
        expected_type="pong",
        assignment=assignment,
        counters=counters,
    )
    if pong.get("client_sequence") != counters.client_ping_sequence:
        raise ProbeRefusal("pong_mismatch", "protocol", "pong sequence does not match")
    counters.pongs += 1

    pcm = b"\x01\x02" * (INPUT_FRAME_PCM_BYTES // 2)
    outgoing = VoiceBinaryFrame(
        kind=VoiceFrameKind.INPUT_PCM,
        generation=counters.generation,
        sequence=counters.pcm_sequence,
        payload=pcm,
    )
    await _send(socket, encode_voice_binary_frame(outgoing))
    raw_echo = await _receive(socket, _CONTROL_TIMEOUT_SECONDS, "echo_timeout")
    if not isinstance(raw_echo, bytes):
        raise ProbeRefusal("binary_contract_invalid", "protocol", "echo frame is not binary")
    try:
        echo = decode_voice_binary_frame(raw_echo)
    except VoiceBinaryFrameError:
        raise ProbeRefusal("binary_contract_invalid", "protocol", "echo frame is invalid") from None
    if (
        echo.kind is not VoiceFrameKind.OUTPUT_PCM
        or echo.generation != counters.generation
        or echo.sequence != counters.pcm_sequence
        or echo.payload != pcm
    ):
        raise ProbeRefusal("echo_mismatch", "protocol", "echo frame does not match")
    counters.pcm_sequence += 1
    counters.echoes += 1


async def exercise_protocol(
    socket: WebSocketClient,
    *,
    assignment: Assignment,
    session_id: str,
    duration_seconds: float,
    heartbeat_timeout_seconds: float,
    release_active: Callable[[], Any],
    monotonic: Callable[[], float] = time.monotonic,
    counters: Counters | None = None,
) -> tuple[Counters, float, int]:
    counters = counters or Counters()
    ready = _decode_control(
        await _receive(socket, _CONTROL_TIMEOUT_SECONDS, "ready_timeout"),
        expected_type="canary_ready",
        assignment=assignment,
        counters=counters,
    )
    if (
        ready.get("protocol") != WEBSOCKET_VOICE_PROTOCOL
        or ready.get("runtime_mode") != WEBSOCKET_CANARY_MODE
        or ready.get("profile_id") != WEBSOCKET_VOICE_PROFILE
        or ready.get("session_id") != session_id
        or ready.get("voice_call_id") != assignment.voice_call_id
        or ready.get("input_sample_rate_hz") != INPUT_SAMPLE_RATE_HZ
        or ready.get("output_sample_rate_hz") != INPUT_SAMPLE_RATE_HZ
        or ready.get("input_frame_pcm_bytes") != INPUT_FRAME_PCM_BYTES
    ):
        raise ProbeRefusal("ready_contract_invalid", "protocol", "ready contract is invalid")

    started = monotonic()
    await _ping_and_echo(socket, assignment, counters)
    interrupted = False
    deadline = started + duration_seconds
    while monotonic() < deadline:
        remaining = deadline - monotonic()
        try:
            raw = await asyncio.wait_for(
                socket.recv(), timeout=min(heartbeat_timeout_seconds, remaining)
            )
        except TimeoutError:
            if monotonic() >= deadline:
                break
            raise ProbeRefusal(
                "heartbeat_timeout", "protocol", "server heartbeat timed out"
            ) from None
        except Exception:
            raise ProbeRefusal(
                "premature_disconnect", "protocol", "WebSocket closed before the deadline"
            ) from None
        heartbeat = _decode_control(
            raw,
            expected_type="heartbeat",
            assignment=assignment,
            counters=counters,
        )
        elapsed_ms = heartbeat.get("elapsed_ms")
        if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, int) or elapsed_ms < 0:
            raise ProbeRefusal(
                "control_contract_invalid", "protocol", "heartbeat elapsed time is invalid"
            )
        counters.heartbeats += 1
        await _ping_and_echo(socket, assignment, counters)
        if not interrupted and monotonic() - started >= duration_seconds / 2:
            await _send(socket, '{"type":"interrupt"}')
            counters.generation += 1
            counters.pcm_sequence = 0
            _decode_control(
                await _receive(socket, _CONTROL_TIMEOUT_SECONDS, "interrupt_timeout"),
                expected_type="clear_audio",
                assignment=assignment,
                counters=counters,
            )
            counters.interrupts += 1
            interrupted = True
            await _ping_and_echo(socket, assignment, counters)

    connected_seconds = monotonic() - started
    if connected_seconds < duration_seconds:
        raise ProbeRefusal(
            "duration_short", "protocol", "WebSocket did not remain open for the requested duration"
        )
    if counters.heartbeats < 1 or counters.interrupts != 1:
        raise ProbeRefusal(
            "protocol_incomplete", "protocol", "protocol coverage is incomplete"
        )

    await release_active()
    _decode_control(
        await _receive(socket, _CONTROL_TIMEOUT_SECONDS, "release_signal_timeout"),
        expected_type="session_released",
        assignment=assignment,
        counters=counters,
    )
    try:
        await asyncio.wait_for(socket.wait_closed(), timeout=_CONTROL_TIMEOUT_SECONDS)
    except Exception:
        raise ProbeRefusal("close_contract_invalid", "release", "WebSocket did not close") from None
    close_code = socket.close_code
    if close_code != 1_000:
        raise ProbeRefusal("close_contract_invalid", "release", "WebSocket close code is invalid")
    return counters, connected_seconds, close_code


def _connector(url: str, *, origin: str, ticket: str):
    return connect(
        url,
        origin=origin,
        subprotocols=[WEBSOCKET_VOICE_PROTOCOL, WEBSOCKET_TICKET_PROTOCOL_PREFIX + ticket],
        open_timeout=_CONTROL_TIMEOUT_SECONDS,
        close_timeout=2,
        ping_interval=None,
        compression=None,
        max_size=8_192,
        max_queue=1,
    )


async def run_probe(
    settings: ProbeSettings,
    *,
    token: str | None = None,
    http_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    connector: Callable[..., Any] = _connector,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    source_verifier: Callable[[ProbeSettings], str] = verify_local_source,
) -> dict[str, Any]:
    voice_call_id = str(uuid4())
    assignment: Assignment | None = None
    release_calls = 0
    counters = Counters()
    connected_started: float | None = None
    connection_ended_recorded = False
    socket_ref: WebSocketClient | None = None
    started_at = now()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "profile": _PROFILE,
        "runtime_mode_expected": WEBSOCKET_CANARY_MODE,
        "runtime_mode_attested": False,
        "status": "failed",
        "expected_sha": settings.expected_sha,
        "observed_sha": None,
        "source_branch": None,
        "base_url": settings.base_url,
        "origin": settings.origin,
        "started_at": started_at.isoformat(),
        "ended_at": None,
        "acceptance_scope": (
            "local_smoke" if _is_loopback_origin(settings.base_url) else "remote_310s"
        ),
        "requested_duration_seconds": settings.duration_seconds,
        "connected_duration_seconds": 0.0,
        "connection_count": 0,
        "reconnect_count": 0,
        "provider_calls_observed_by_probe": 0,
        "provider_usage_verified": False,
        "auth_source": "private_file",
        "failure": None,
    }

    try:
        if token is None:
            token = read_private_token(settings.token_file)
        evidence["source_branch"] = source_verifier(settings)
        async with http_client_factory(
            base_url=settings.base_url,
            timeout=_CONTROL_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            await verify_source_and_health(client, settings.expected_sha)
            evidence["observed_sha"] = settings.expected_sha
            assignment = await bootstrap(
                client,
                token=token,
                session_id=settings.session_id,
                voice_call_id=voice_call_id,
                now=now,
            )

            async def release_active() -> None:
                nonlocal release_calls
                await release(
                    client,
                    token=token,
                    session_id=settings.session_id,
                    voice_call_id=voice_call_id,
                )
                release_calls += 1

            async def cleanup_assignment() -> None:
                if release_calls == 0:
                    try:
                        await release_active()
                    except Exception:
                        pass

            def record_connection_end() -> None:
                nonlocal connection_ended_recorded
                if connected_started is not None and not connection_ended_recorded:
                    evidence["connected_duration_seconds"] = round(
                        max(0.0, monotonic() - connected_started), 3
                    )
                    evidence["close_code"] = (
                        socket_ref.close_code if socket_ref is not None else None
                    )
                    connection_ended_recorded = True

            try:
                context = connector(
                    _websocket_url(settings.base_url, assignment.websocket_path),
                    origin=settings.origin,
                    ticket=assignment.ticket,
                )
                async with context as socket:
                    socket_ref = socket
                    evidence["connection_count"] = 1
                    connected_started = monotonic()
                    if socket.subprotocol != WEBSOCKET_VOICE_PROTOCOL:
                        raise ProbeRefusal(
                            "websocket_subprotocol_invalid",
                            "upgrade",
                            "server selected an invalid WebSocket subprotocol",
                        )
                    counters, duration, close_code = await exercise_protocol(
                        socket,
                        assignment=assignment,
                        session_id=settings.session_id,
                        duration_seconds=settings.duration_seconds,
                        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
                        release_active=release_active,
                        monotonic=monotonic,
                        counters=counters,
                    )
                record_connection_end()
                await release_active()
            except ProbeRefusal:
                record_connection_end()
                await cleanup_assignment()
                raise
            except Exception:
                record_connection_end()
                await cleanup_assignment()
                raise ProbeRefusal(
                    "websocket_runtime_failed", "protocol", "WebSocket run failed"
                ) from None
            finally:
                record_connection_end()

        evidence.update(
            {
                "status": "passed",
                "connected_duration_seconds": round(duration, 3),
                "close_code": close_code,
                "runtime_mode_attested": True,
            }
        )
    except ProbeRefusal as exc:
        evidence["status"] = "failed"
        evidence["runtime_mode_attested"] = False
        evidence["failure"] = {"code": exc.code, "stage": exc.stage}
    except Exception:
        evidence["status"] = "failed"
        evidence["runtime_mode_attested"] = False
        evidence["failure"] = {"code": "internal_error", "stage": "internal"}
    finally:
        evidence.update(
            {
                "ended_at": now().isoformat(),
                "release_calls": release_calls,
                "heartbeats": counters.heartbeats,
                "pings": counters.pings,
                "pongs": counters.pongs,
                "echoes": counters.echoes,
                "interrupts": counters.interrupts,
                "final_generation": counters.generation,
                "final_server_sequence": counters.server_sequence,
            }
        )
    return evidence


def atomic_private_evidence(path: Path, evidence: Mapping[str, Any], secrets: tuple[str, ...]) -> None:
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if any(secret and secret in rendered for secret in secrets):
        raise ProbeRefusal("evidence_secret_detected", "evidence", "evidence was not written")
    requested = path.expanduser().absolute()
    if requested.is_symlink() or requested.exists():
        raise ProbeRefusal(
            "evidence_path_unsafe", "evidence", "evidence path must not already exist"
        )
    parent = requested.parent
    parent_existed = parent.exists()
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed:
            os.chmod(parent, 0o700)
    except OSError:
        raise ProbeRefusal("evidence_path_unsafe", "evidence", "evidence path is invalid") from None
    if parent.is_symlink() or not parent.is_dir():
        raise ProbeRefusal("evidence_path_unsafe", "evidence", "evidence path is invalid")
    target = parent.resolve() / requested.name
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{requested.name}.", dir=target.parent
        )
        os.fchmod(descriptor, _PRIVATE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target, follow_symlinks=False)
        except OSError:
            raise ProbeRefusal(
                "evidence_path_unsafe", "evidence", "evidence path could not be created"
            ) from None
    except ProbeRefusal:
        raise
    except OSError:
        raise ProbeRefusal(
            "evidence_write_failed", "evidence", "evidence could not be written"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if temporary is not None:
                os.unlink(temporary)
        except FileNotFoundError:
            pass


async def _execute(settings: ProbeSettings) -> tuple[int, dict[str, Any]]:
    token = ""
    try:
        token = read_private_token(settings.token_file)
        evidence = await run_probe(settings, token=token)
    except ProbeRefusal as exc:
        evidence = {
            "schema_version": 1,
            "profile": _PROFILE,
            "status": "failed",
            "expected_sha": settings.expected_sha,
            "base_url": settings.base_url,
            "origin": settings.origin,
            "requested_duration_seconds": settings.duration_seconds,
            "connected_duration_seconds": 0.0,
            "connection_count": 0,
            "reconnect_count": 0,
            "provider_calls_observed_by_probe": 0,
            "provider_usage_verified": False,
            "failure": {"code": exc.code, "stage": exc.stage},
            "ended_at": datetime.now(UTC).isoformat(),
        }
    code = 0 if evidence["status"] == "passed" else 2
    atomic_private_evidence(settings.evidence_out, evidence, (token,))
    return code, evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--firebase-id-token-file", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=_DEFAULT_DURATION_SECONDS)
    parser.add_argument(
        "--heartbeat-timeout-seconds",
        type=float,
        default=_DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--evidence-out", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = ProbeSettings(
            base_url=args.base_url,
            origin=args.origin,
            expected_sha=args.expected_sha,
            session_id=args.session_id,
            token_file=args.firebase_id_token_file.expanduser(),
            evidence_out=args.evidence_out.expanduser(),
            duration_seconds=args.duration_seconds,
            heartbeat_timeout_seconds=args.heartbeat_timeout_seconds,
        )
        code, evidence = asyncio.run(_execute(settings))
    except ProbeRefusal as exc:
        print(f"WebSocket canary refused [{exc.code}]", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "failure_code": (evidence.get("failure") or {}).get("code"),
                "evidence_path": str(settings.evidence_out),
            },
            sort_keys=True,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

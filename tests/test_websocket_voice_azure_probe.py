from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from murmur.voice.websocket_protocol import (
    INPUT_FRAME_PCM_BYTES,
    INPUT_SAMPLE_RATE_HZ,
    WEBSOCKET_CANARY_MODE,
    WEBSOCKET_VOICE_PROFILE,
    WEBSOCKET_VOICE_PROTOCOL,
    VoiceBinaryFrame,
    VoiceFrameKind,
    decode_voice_binary_frame,
    encode_voice_binary_frame,
)

import scripts.manual.probe_websocket_voice_azure as probe_module
from scripts.manual.probe_websocket_voice_azure import (
    Assignment,
    ProbeRefusal,
    ProbeSettings,
    atomic_private_evidence,
    exercise_protocol,
    read_private_token,
    run_probe,
    verify_source_and_health,
)

SHA = "a" * 40
SESSION_ID = "10000000-0000-4000-8000-000000000001"
AGENT_ID = "20000000-0000-4000-8000-000000000002"
CALL_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"
TICKET = "T" * 43
NOW = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)


class _Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


class _ProtocolSocket:
    subprotocol = WEBSOCKET_VOICE_PROTOCOL

    def __init__(
        self,
        clock: _Clock,
        *,
        session_id: str = SESSION_ID,
        call_id: str = CALL_ID,
        trace_id: str = TRACE_ID,
        disconnect_at: float | None = None,
    ) -> None:
        self.clock = clock
        self.session_id = session_id
        self.call_id = call_id
        self.trace_id = trace_id
        self.disconnect_at = disconnect_at
        self.generation = 0
        self.server_sequence = 0
        self.queue: list[str | bytes] = []
        self.close_code: int | None = None
        self.released = False
        self.sent: list[str | bytes] = []
        self._control(
            "canary_ready",
            protocol=WEBSOCKET_VOICE_PROTOCOL,
            runtime_mode=WEBSOCKET_CANARY_MODE,
            session_id=session_id,
            voice_call_id=call_id,
            profile_id=WEBSOCKET_VOICE_PROFILE,
            input_sample_rate_hz=INPUT_SAMPLE_RATE_HZ,
            output_sample_rate_hz=INPUT_SAMPLE_RATE_HZ,
            input_frame_pcm_bytes=INPUT_FRAME_PCM_BYTES,
        )

    def _control(self, kind: str, **values: object) -> None:
        self.server_sequence += 1
        self.queue.append(
            json.dumps(
                {
                    "type": kind,
                    "server_sequence": self.server_sequence,
                    "generation": self.generation,
                    "trace_id": self.trace_id,
                    **values,
                },
                separators=(",", ":"),
            )
        )

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        if isinstance(message, str):
            control = json.loads(message)
            if control["type"] == "ping":
                self._control("pong", client_sequence=control["sequence"])
            elif control["type"] == "interrupt":
                self.generation += 1
                self._control("clear_audio")
        else:
            incoming = decode_voice_binary_frame(message)
            self.queue.append(
                encode_voice_binary_frame(
                    VoiceBinaryFrame(
                        kind=VoiceFrameKind.OUTPUT_PCM,
                        generation=incoming.generation,
                        sequence=incoming.sequence,
                        payload=incoming.payload,
                    )
                )
            )

    async def recv(self) -> str | bytes:
        if self.queue:
            return self.queue.pop(0)
        self.clock.value += 15
        if self.disconnect_at is not None and self.clock.value >= self.disconnect_at:
            raise RuntimeError("secret transport detail")
        self._control("heartbeat", elapsed_ms=round(self.clock.value * 1_000))
        return self.queue.pop(0)

    def release(self) -> None:
        self.released = True
        self._control("session_released")
        self.close_code = 1_000

    async def wait_closed(self) -> None:
        if not self.released:
            raise RuntimeError("not released")


class _SocketContext:
    def __init__(self, socket: _ProtocolSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> _ProtocolSocket:
        return self.socket

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeHttpClient:
    def __init__(self, *, bad_sha: bool = False, fail_exit: bool = False) -> None:
        self.bad_sha = bad_sha
        self.fail_exit = fail_exit
        self.release_calls = 0
        self.voice_call_id: str | None = None
        self.socket: _ProtocolSocket | None = None
        self.requests: list[tuple[str, str, dict | None]] = []

    async def __aenter__(self) -> _FakeHttpClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self.fail_exit:
            raise RuntimeError("private client shutdown detail")

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        self.requests.append((method, path, headers))
        no_store = {"Cache-Control": "no-store"}
        observed_sha = "b" * 40 if self.bad_sha else SHA
        if path == "/healthz":
            return httpx.Response(
                200, headers=no_store, json={"status": "ok", "release_sha": observed_sha}
            )
        if path == "/readyz":
            return httpx.Response(
                200,
                headers=no_store,
                json={"status": "ready", "release_sha": observed_sha, "checks": {}},
            )
        if path == "/api/voice/websocket/session":
            assert json is not None
            self.voice_call_id = json["voice_call_id"]
            return httpx.Response(
                200,
                headers=no_store,
                json={
                    "runtime": "websocket_v1",
                    "profile_id": WEBSOCKET_VOICE_PROFILE,
                    "event_protocol": WEBSOCKET_VOICE_PROTOCOL,
                    "websocket_path": "/api/voice/websocket",
                    "ticket": TICKET,
                    "session_id": json["session_id"],
                    "agent_id": AGENT_ID,
                    "voice_call_id": self.voice_call_id,
                    "trace_id": TRACE_ID,
                    "expires_at": (NOW + timedelta(seconds=15)).isoformat(),
                },
            )
        if path == "/api/voice/websocket/session/end":
            self.release_calls += 1
            if self.release_calls == 1 and self.socket is not None:
                self.socket.release()
            return httpx.Response(204, headers=no_store)
        raise AssertionError(path)


def _private_token(tmp_path: Path, value: str = "firebase-id-token") -> Path:
    path = tmp_path / "firebase-token"
    path.write_text(value, encoding="ascii")
    path.chmod(0o600)
    return path


def _settings(tmp_path: Path, **values: object) -> ProbeSettings:
    defaults = {
        "base_url": "http://127.0.0.1:8000",
        "origin": "http://127.0.0.1:3102",
        "expected_sha": SHA,
        "session_id": SESSION_ID,
        "token_file": _private_token(tmp_path),
        "evidence_out": tmp_path / "evidence.json",
        "duration_seconds": 30,
        "heartbeat_timeout_seconds": 25,
    }
    defaults.update(values)
    return ProbeSettings(**defaults)  # type: ignore[arg-type]


def test_settings_require_https_exact_sha_uuid_and_distinct_paths(tmp_path: Path) -> None:
    _settings(tmp_path)
    for override in (
        {"base_url": "http://api.example.test"},
        {"expected_sha": "abc"},
        {"session_id": "not-a-uuid"},
    ):
        with pytest.raises(ProbeRefusal) as refused:
            _settings(tmp_path, **override)
        assert refused.value.code == "settings_invalid"

    with pytest.raises(ProbeRefusal) as refused:
        _settings(
            tmp_path,
            base_url="https://api.example.test",
            origin="https://web.example.test",
            duration_seconds=309,
        )
    assert refused.value.code == "settings_invalid"


def test_token_requires_owned_mode_0600_regular_ascii_file(tmp_path: Path) -> None:
    token = _private_token(tmp_path, "sentinel-token\n")
    assert read_private_token(token) == "sentinel-token"
    token.chmod(0o644)
    with pytest.raises(ProbeRefusal) as refused:
        read_private_token(token)
    assert refused.value.code == "token_file_invalid"

    token.chmod(0o600)
    link = tmp_path / "token-link"
    link.symlink_to(token)
    with pytest.raises(ProbeRefusal) as refused:
        read_private_token(link)
    assert refused.value.code == "token_file_invalid"


def test_source_preflight_requires_clean_head_at_origin_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, repo_root=tmp_path)
    branch = "codex/test"
    outputs = {
        ("rev-parse", "--show-toplevel"): str(tmp_path),
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
        ("rev-parse", "--verify", "HEAD"): SHA,
        ("symbolic-ref", "--quiet", "--short", "HEAD"): branch,
        ("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"): (
            f"{SHA}\trefs/heads/{branch}"
        ),
    }

    def fake_git(_root: Path, *arguments: str) -> str:
        return outputs[arguments]

    monkeypatch.setattr(probe_module, "_git", fake_git)
    assert probe_module.verify_local_source(settings) == branch
    outputs[("status", "--porcelain=v1", "--untracked-files=all")] = "?? untracked"
    with pytest.raises(ProbeRefusal) as refused:
        probe_module.verify_local_source(settings)
    assert refused.value.code == "source_tree_dirty"


@pytest.mark.asyncio
async def test_source_health_is_exact_and_sends_no_authorization() -> None:
    client = _FakeHttpClient()
    await verify_source_and_health(client, SHA)  # type: ignore[arg-type]
    assert all(headers is None for _, _, headers in client.requests)
    with pytest.raises(ProbeRefusal) as refused:
        await verify_source_and_health(_FakeHttpClient(bad_sha=True), SHA)  # type: ignore[arg-type]
    assert refused.value.code == "source_sha_mismatch"


@pytest.mark.asyncio
async def test_protocol_exercises_heartbeat_echo_interrupt_and_active_release() -> None:
    clock = _Clock()
    socket = _ProtocolSocket(clock)
    assignment = Assignment(
        websocket_path="/api/voice/websocket",
        ticket=TICKET,
        trace_id=TRACE_ID,
        voice_call_id=CALL_ID,
        expires_at=NOW + timedelta(seconds=15),
    )

    async def release_active() -> None:
        socket.release()

    counters, duration, close_code = await exercise_protocol(
        socket,
        assignment=assignment,
        session_id=SESSION_ID,
        duration_seconds=30,
        heartbeat_timeout_seconds=25,
        release_active=release_active,
        monotonic=clock,
    )
    assert duration >= 30
    assert counters.heartbeats == 2
    assert counters.interrupts == 1
    assert counters.pings == counters.pongs == counters.echoes
    assert counters.generation == 1
    assert close_code == 1_000


@pytest.mark.asyncio
async def test_disconnect_at_240_seconds_fails_without_release_or_reconnect() -> None:
    clock = _Clock()
    socket = _ProtocolSocket(clock, disconnect_at=240)
    assignment = Assignment(
        websocket_path="/api/voice/websocket",
        ticket=TICKET,
        trace_id=TRACE_ID,
        voice_call_id=CALL_ID,
        expires_at=NOW + timedelta(seconds=15),
    )
    released = False

    async def release_active() -> None:
        nonlocal released
        released = True

    with pytest.raises(ProbeRefusal) as refused:
        await exercise_protocol(
            socket,
            assignment=assignment,
            session_id=SESSION_ID,
            duration_seconds=310,
            heartbeat_timeout_seconds=25,
            release_active=release_active,
            monotonic=clock,
        )
    assert refused.value.code == "premature_disconnect"
    assert clock.value == 240
    assert released is False


@pytest.mark.asyncio
async def test_full_probe_opens_once_releases_twice_and_never_records_secrets(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _FakeHttpClient()
    clock = _Clock()
    connector_calls = 0

    def client_factory(**_kwargs: object) -> _FakeHttpClient:
        return client

    def connector(url: str, *, origin: str, ticket: str) -> _SocketContext:
        nonlocal connector_calls
        connector_calls += 1
        assert url == "ws://127.0.0.1:8000/api/voice/websocket"
        assert origin == settings.origin
        assert ticket == TICKET
        assert client.voice_call_id is not None
        client.socket = _ProtocolSocket(clock, call_id=client.voice_call_id)
        return _SocketContext(client.socket)

    evidence = await run_probe(
        settings,
        http_client_factory=client_factory,  # type: ignore[arg-type]
        connector=connector,
        monotonic=clock,
        now=lambda: NOW,
        source_verifier=lambda _settings: "codex/test",
    )
    rendered = json.dumps(evidence)
    assert evidence["status"] == "passed"
    assert evidence["connection_count"] == 1
    assert evidence["reconnect_count"] == 0
    assert evidence["release_calls"] == 2
    assert evidence["runtime_mode_attested"] is True
    assert evidence["provider_usage_verified"] is False
    assert client.release_calls == 2
    assert connector_calls == 1
    assert TICKET not in rendered
    assert "firebase-id-token" not in rendered


@pytest.mark.asyncio
async def test_failed_probe_preserves_connection_duration_and_cleans_assignment(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, duration_seconds=310)
    client = _FakeHttpClient()
    clock = _Clock()
    connector_calls = 0

    def client_factory(**_kwargs: object) -> _FakeHttpClient:
        return client

    def connector(_url: str, *, origin: str, ticket: str) -> _SocketContext:
        nonlocal connector_calls
        connector_calls += 1
        assert origin == settings.origin
        assert ticket == TICKET
        assert client.voice_call_id is not None
        client.socket = _ProtocolSocket(
            clock,
            call_id=client.voice_call_id,
            disconnect_at=240,
        )
        return _SocketContext(client.socket)

    evidence = await run_probe(
        settings,
        http_client_factory=client_factory,  # type: ignore[arg-type]
        connector=connector,
        monotonic=clock,
        now=lambda: NOW,
        source_verifier=lambda _settings: "codex/test",
    )

    assert evidence["status"] == "failed"
    assert evidence["failure"] == {
        "code": "premature_disconnect",
        "stage": "protocol",
    }
    assert evidence["connection_count"] == 1
    assert evidence["connected_duration_seconds"] == 240
    assert evidence["close_code"] is None
    assert evidence["reconnect_count"] == 0
    assert evidence["release_calls"] == 1
    assert connector_calls == 1
    assert client.release_calls == 1


@pytest.mark.asyncio
async def test_http_client_exit_failure_cannot_leave_passed_evidence(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _FakeHttpClient(fail_exit=True)
    clock = _Clock()

    def connector(_url: str, *, origin: str, ticket: str) -> _SocketContext:
        assert origin == settings.origin
        assert ticket == TICKET
        assert client.voice_call_id is not None
        client.socket = _ProtocolSocket(clock, call_id=client.voice_call_id)
        return _SocketContext(client.socket)

    evidence = await run_probe(
        settings,
        http_client_factory=lambda **_kwargs: client,  # type: ignore[arg-type]
        connector=connector,
        monotonic=clock,
        now=lambda: NOW,
        source_verifier=lambda _settings: "codex/test",
    )

    assert evidence["status"] == "failed"
    assert evidence["runtime_mode_attested"] is False
    assert evidence["failure"] == {"code": "internal_error", "stage": "internal"}


def test_private_evidence_is_atomic_0600_and_refuses_secret(tmp_path: Path) -> None:
    parent = tmp_path / "evidence"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    output = parent / "result.json"
    atomic_private_evidence(output, {"status": "passed"}, ("secret-value",))
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "passed"}
    with pytest.raises(ProbeRefusal) as refused:
        atomic_private_evidence(output, {"detail": "secret-value"}, ("secret-value",))
    assert refused.value.code == "evidence_secret_detected"
    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "passed"}

    with pytest.raises(ProbeRefusal) as refused:
        atomic_private_evidence(output, {"status": "replacement"}, ())
    assert refused.value.code == "evidence_path_unsafe"

    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / "evidence-link"
    link.symlink_to(victim)
    with pytest.raises(ProbeRefusal) as refused:
        atomic_private_evidence(link, {"status": "passed"}, ())
    assert refused.value.code == "evidence_path_unsafe"
    assert victim.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_extra_control_field_is_rejected_strictly() -> None:
    clock = _Clock()
    socket = _ProtocolSocket(clock)
    ready = json.loads(socket.queue[0])
    ready["unexpected"] = True
    socket.queue[0] = json.dumps(ready)
    assignment = Assignment(
        websocket_path="/api/voice/websocket",
        ticket=TICKET,
        trace_id=TRACE_ID,
        voice_call_id=CALL_ID,
        expires_at=NOW + timedelta(seconds=15),
    )

    async def release_active() -> None:
        socket.release()

    with pytest.raises(ProbeRefusal) as refused:
        await exercise_protocol(
            socket,
            assignment=assignment,
            session_id=SESSION_ID,
            duration_seconds=30,
            heartbeat_timeout_seconds=25,
            release_active=release_active,
            monotonic=clock,
        )
    assert refused.value.code == "control_contract_invalid"

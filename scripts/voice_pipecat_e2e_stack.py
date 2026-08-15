#!/usr/bin/env python3
"""Boot and prove the first Docker-free Pipecat E2E backend checkpoint.

This runner intentionally stops at authenticated ASGI/bootstrap/release proof.
It does not claim browser media, Opus, canonical turns, TURN/TLS, provider
quality, geography, scale, or cost.  The later browser runner can reuse the
same guarded app and evidence contract without changing production routes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOICE_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "voice" / "audio"
ASSISTANT_FIXTURE = VOICE_FIXTURE_ROOT / "assistant-long.wav"
PIPECAT_HOST = "127.0.0.1"
PIPECAT_PORT = 8101
PIPECAT_BASE_URL = f"http://{PIPECAT_HOST}:{PIPECAT_PORT}"
PIPECAT_SIGNALING_BASE_URL = f"{PIPECAT_BASE_URL}/api/voice/pipecat/signal"
VOICE_PROFILE_ID = "pipecat-fake-rtc-v1"
VOICE_RUNTIME = "pipecat_smallwebrtc_v1"
E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76"
E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"
AUTHORIZATION = "Bearer voice-e2e"

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_STRIPPED_ENV_PREFIXES = (
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "COHERE_",
    "DEEPGRAM_",
    "ELEVENLABS_",
    "FIREBASE_",
    "GEMINI_",
    "GOOGLE_",
    "GROQ_",
    "HF_",
    "HUGGINGFACE_",
    "LIVEKIT_",
    "LLM_",
    "MEM0_",
    "MISTRAL_",
    "MURMUR_",
    "OPENAI_",
    "SMART_TURN_",
    "TAVILY_",
    "TTS_",
    "VOICE_",
)
_STRIPPED_ENV_NAMES = {
    "DATABASE_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "NO_PROXY",
    "PYTHONPATH",
    "no_proxy",
}
_SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_API_SECRET",
    "_AUTH_TOKEN",
    "_CREDENTIALS",
)


class StackError(RuntimeError):
    """The guarded local checkpoint could not prove its narrow contract."""


@dataclass(frozen=True)
class StackPaths:
    run_id: str
    run_dir: Path
    database: Path
    evidence: Path
    server_log: Path
    proof: Path


def make_paths(run_id: str) -> StackPaths:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise StackError("run ID must contain only lowercase letters, digits, and hyphens")
    run_dir = (PROJECT_ROOT / "var" / "voice-pipecat-e2e" / run_id).resolve()
    evidence = (PROJECT_ROOT / "var" / "evals" / f"voice-pipecat-e2e-{run_id}.jsonl").resolve()
    return StackPaths(
        run_id=run_id,
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=evidence,
        server_log=run_dir / "pipecat-asgi.log",
        proof=run_dir / "backend-checkpoint.json",
    )


def build_environment(paths: StackPaths) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _STRIPPED_ENV_NAMES
        and not any(key.startswith(prefix) for prefix in _STRIPPED_ENV_PREFIXES)
        and not key.endswith(_SENSITIVE_ENV_SUFFIXES)
    }
    python_path = os.pathsep.join((str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT)))
    environment.update(
        {
            "ALLOWED_CORS_ORIGINS": "http://127.0.0.1:3100,http://localhost:3100",
            "MURMUR_DATABASE_URL": f"sqlite:///{paths.database}",
            "MURMUR_E2E_MODE": "1",
            "MURMUR_ENVIRONMENT": "test",
            "MURMUR_PIPECAT_E2E_ASSISTANT_FIXTURE_PATH": str(ASSISTANT_FIXTURE.resolve()),
            "MURMUR_PIPECAT_E2E_EVIDENCE_PATH": str(paths.evidence),
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "PIPECAT_HOST": PIPECAT_HOST,
            "PIPECAT_PORT": str(PIPECAT_PORT),
            "PIPECAT_SIGNALING_BASE_URL": PIPECAT_SIGNALING_BASE_URL,
            "PYTHONPATH": python_path,
            "PYTHON_DOTENV_DISABLED": "1",
            "VOICE_RUNTIME": VOICE_RUNTIME,
            "VOICE_V2_PROFILE_ID": VOICE_PROFILE_ID,
            "no_proxy": "127.0.0.1,localhost,::1",
        }
    )
    return environment


class PipecatBackendCheckpoint:
    def __init__(self, paths: StackPaths, *, startup_timeout_seconds: float = 30.0) -> None:
        if not 1 <= startup_timeout_seconds <= 120:
            raise StackError("startup timeout must be between one and 120 seconds")
        self.paths = paths
        self.startup_timeout_seconds = startup_timeout_seconds
        self.environment = build_environment(paths)
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any | None = None

    def run(self) -> dict[str, object]:
        self._prepare()
        try:
            self._start_app()
            health = self._wait_for_health()
            voice_call_id = str(uuid.uuid4())
            assignment = self._bootstrap(voice_call_id)
            self._release(voice_call_id)
            status = self._status(voice_call_id)
            self._assert_backend_checkpoint(health, assignment, status, voice_call_id)
            proof = {
                "schema_version": 1,
                "status": "backend_checkpoint_passed",
                "run_id": self.paths.run_id,
                "runtime": VOICE_RUNTIME,
                "profile_id": VOICE_PROFILE_ID,
                "network": "direct-loopback",
                "providers": "fake",
                "cost": "unmeasured",
                "voice_call_id": voice_call_id,
                "health": health,
                "assignment": {
                    "runtime": assignment["runtime"],
                    "profile_id": assignment["profile_id"],
                    "event_protocol": assignment["event_protocol"],
                    "session_id": assignment["session_id"],
                    "agent_id": assignment["agent_id"],
                    "voice_call_id": assignment["voice_call_id"],
                    "ice_server_count": len(assignment["ice_servers"]),
                    "opaque_signaling_path": "/api/voice/pipecat/signal/<redacted>",
                },
                "terminal": status,
                "proved": [
                    "guarded dedicated Pipecat ASGI owner booted",
                    "production authenticated bootstrap issued an opaque assignment",
                    "loopback direct ICE contained no STUN or TURN server",
                    "production exact-call release reached terminal cleanup",
                    "bootstrap locks/assignments and active signaling call returned to zero",
                    "no LiveKit module loaded in the dedicated process",
                ],
                "limitations": [
                    "No browser, SDP offer, RTP, Opus, or audible media was exercised",
                    "Fake processor frame and interruption behavior is covered by focused tests",
                    "No Coturn, forced relay, TLS, paid provider, geography, scale, or cost result",
                ],
            }
            _atomic_write_json(self.paths.proof, proof)
            return proof
        finally:
            self._stop_app()

    def _prepare(self) -> None:
        if not ASSISTANT_FIXTURE.is_file():
            raise StackError("checked-in assistant fixture is missing")
        if self.paths.run_dir.exists() or self.paths.evidence.exists():
            raise StackError("run artifacts already exist; choose a fresh run ID")
        _require_port_available(PIPECAT_HOST, PIPECAT_PORT)
        self.paths.run_dir.mkdir(parents=True, exist_ok=False)
        self.paths.evidence.parent.mkdir(parents=True, exist_ok=True)

    def _start_app(self) -> None:
        self._log_handle = self.paths.server_log.open("xb")
        command = (
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.voice_pipecat_e2e_app:app",
            "--host",
            PIPECAT_HOST,
            "--port",
            str(PIPECAT_PORT),
            "--workers",
            "1",
            "--no-access-log",
            "--no-server-header",
            "--limit-concurrency",
            "100",
            "--lifespan",
            "on",
        )
        self._process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def _wait_for_health(self) -> dict[str, object]:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error = "not ready"
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                raise StackError("Pipecat ASGI process was not started")
            if process.poll() is not None:
                raise StackError(
                    f"Pipecat ASGI process exited early\n{_sanitized_log_tail(self.paths.server_log)}"
                )
            try:
                status, body = _http_json("GET", f"{PIPECAT_BASE_URL}/_e2e/health")
                if status == 200 and body.get("ok") is True:
                    return body
                last_error = f"HTTP {status}"
            except StackError as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise StackError(
            f"Pipecat ASGI health timed out ({last_error})\n"
            f"{_sanitized_log_tail(self.paths.server_log)}"
        )

    def _bootstrap(self, voice_call_id: str) -> dict[str, object]:
        status, body = _http_json(
            "POST",
            f"{PIPECAT_BASE_URL}/api/voice/session",
            {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id},
        )
        if status != 200:
            raise StackError(f"Pipecat bootstrap returned HTTP {status}")
        return body

    def _release(self, voice_call_id: str) -> None:
        status, _body = _http_json(
            "POST",
            f"{PIPECAT_BASE_URL}/api/voice/session/end",
            {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id},
            allow_empty=True,
        )
        if status != 204:
            raise StackError(f"Pipecat release returned HTTP {status}")

    def _status(self, voice_call_id: str) -> dict[str, object]:
        status, body = _http_json(
            "POST",
            f"{PIPECAT_BASE_URL}/_e2e/pipecat/status",
            {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id},
        )
        if status != 200:
            raise StackError(f"Pipecat cleanup status returned HTTP {status}")
        return body

    @staticmethod
    def _assert_backend_checkpoint(
        health: dict[str, object],
        assignment: dict[str, object],
        status: dict[str, object],
        voice_call_id: str,
    ) -> None:
        if health.get("livekit_imported") is not False:
            raise StackError("dedicated Pipecat process loaded a LiveKit module")
        required_assignment = {
            "runtime": VOICE_RUNTIME,
            "profile_id": VOICE_PROFILE_ID,
            "event_protocol": "rtvi-murmur-v2",
            "session_id": E2E_SESSION_ID,
            "agent_id": E2E_AGENT_ID,
            "voice_call_id": voice_call_id,
        }
        if any(assignment.get(key) != value for key, value in required_assignment.items()):
            raise StackError("Pipecat bootstrap assignment identity is inconsistent")
        if assignment.get("ice_servers") != []:
            raise StackError("direct-loopback checkpoint unexpectedly returned ICE servers")
        webrtc_url = assignment.get("webrtc_url")
        if not isinstance(webrtc_url, str):
            raise StackError("Pipecat bootstrap omitted its opaque signaling URL")
        parsed = urlsplit(webrtc_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != PIPECAT_HOST
            or parsed.port != PIPECAT_PORT
            or not parsed.path.startswith("/api/voice/pipecat/signal/")
            or any(value in webrtc_url for value in (E2E_SESSION_ID, E2E_AGENT_ID, voice_call_id))
        ):
            raise StackError("Pipecat signaling assignment is not opaque loopback state")
        reservation = status.get("reservation")
        control = status.get("control_plane")
        if not isinstance(reservation, dict) or (
            reservation.get("state") != "terminal"
            or reservation.get("cleanup_complete") is not True
        ):
            raise StackError("Pipecat release did not reach terminal cleanup")
        expected_control = {
            "bootstrap_active_assignment_count": 0,
            "bootstrap_active_lock_count": 0,
            "signaling_active_call_count": 0,
            "runtime_handle_retained": False,
            "cleanup_retry_pending": False,
            "runtime_observer_pending": False,
            "expiry_pending": False,
            "trusted_release_pending": False,
        }
        if control != expected_control:
            raise StackError("Pipecat release retained active control-plane resources")
        if status.get("status") != "pending":
            raise StackError("bootstrap-only checkpoint must not claim the media gate passed")

    def _stop_app(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if self._log_handle is not None:
            self._log_handle.close()
        self._process = None
        self._log_handle = None


def _http_json(
    method: str,
    url: str,
    body: dict[str, object] | None = None,
    *,
    allow_empty: bool = False,
) -> tuple[int, dict[str, object]]:
    encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": AUTHORIZATION, "User-Agent": "murmur-pipecat-e2e/1"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=encoded, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            status = response.status
            payload = response.read()
    except HTTPError as exc:
        status = exc.code
        payload = exc.read()
    except (TimeoutError, URLError, OSError) as exc:
        raise StackError("loopback Pipecat HTTP request failed") from exc
    if not payload:
        if allow_empty:
            return status, {}
        raise StackError("loopback Pipecat HTTP response was empty")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StackError("loopback Pipecat HTTP response was not JSON") from exc
    if not isinstance(decoded, dict):
        raise StackError("loopback Pipecat HTTP response was not an object")
    return status, decoded


def _require_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise StackError(f"loopback TCP port {port} is already in use") from exc


def _sanitized_log_tail(path: Path, lines: int = 80) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "Pipecat ASGI log unavailable"
    return "\n".join(content[-lines:]).replace(AUTHORIZATION, "Bearer <redacted>")


def _atomic_write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default=f"backend-{int(time.time())}",
        help="fresh lowercase artifact identifier",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=30.0,
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    checkpoint = PipecatBackendCheckpoint(
        make_paths(args.run_id),
        startup_timeout_seconds=args.startup_timeout_seconds,
    )
    proof = checkpoint.run()
    print(json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "ASSISTANT_FIXTURE",
    "PIPECAT_BASE_URL",
    "PIPECAT_PORT",
    "PipecatBackendCheckpoint",
    "StackError",
    "StackPaths",
    "build_environment",
    "make_paths",
]

"""Run Murmur's deterministic Voice V2 test through a real local RTC path.

The stack is intentionally self-contained and loopback-only: a digest-pinned
LiveKit Server, the guarded test FastAPI composition, the guarded deterministic
worker, a production Next.js server, and Chromium/Playwright.  Provider
credentials are removed from every child process and repository ``.env``
loading is disabled before any Murmur module is imported.

Run from the repository root after installing the locked backend and frontend
dependencies::

    uv run python scripts/voice_e2e_stack.py

``--dry-run`` prints the exact commands without starting processes.
``--skip-browser`` is an infrastructure smoke test; it does not claim media
acceptance because no microphone fixture is sent through the SFU.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LIVEKIT_SERVER_IMAGE = (
    "livekit/livekit-server@sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
)
LIVEKIT_SERVER_VERSION = "1.13.1"
LIVEKIT_AGENTS_VERSION = "1.6.9"
LIVEKIT_URL = "ws://127.0.0.1:7880"
LIVEKIT_API_KEY = "devkey"
LIVEKIT_API_SECRET = "secret"
APP_PORT = 8100
WEB_PORT = 3100
WORKER_PORT = 8081
VOICE_WORKER_NAME = "murmur-voice-v2-e2e"
VOICE_PROFILE_ID = "fake-rtc-v1"
VOICE_SIGNING_SECRET = "voice-e2e-signing-secret-32-bytes-minimum"
E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76"
E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
VOICE_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "voice" / "audio"
ASSISTANT_FIXTURE = VOICE_FIXTURE_ROOT / "assistant-long.wav"
BROWSER_FIXTURE = VOICE_FIXTURE_ROOT / "browser-barge-in.wav"

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_CONTRACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
    "LLM_",
    "LIVEKIT_",
    "MEM0_",
    "MISTRAL_",
    "MURMUR_",
    "OPENAI_",
    "SMART_TURN_",
    "TAVILY_",
    "TTS_",
    "VOICE_",
    "NEXT_PUBLIC_FIREBASE_",
)
_STRIPPED_ENV_NAMES = {
    "DATABASE_URL",
    "FORCE_COLOR",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "NEXT_PUBLIC_API_URL",
    "NEXT_PUBLIC_VOICE_RUNTIME",
    "PYTHON_DOTENV_DISABLED",
}
_SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_API_SECRET",
    "_AUTH_TOKEN",
    "_CREDENTIALS",
)
_TCP_PORTS = (WEB_PORT, 7880, 7881, APP_PORT, WORKER_PORT)
_UDP_PORTS = (7882,)


class StackError(RuntimeError):
    """A bounded stack operation failed with actionable local evidence."""


@dataclass(frozen=True)
class StackPaths:
    run_id: str
    run_dir: Path
    database: Path
    evidence: Path
    playwright_dir: Path
    browser_result: Path
    stack_proof: Path
    web_workspace: Path


@dataclass(frozen=True)
class StackOptions:
    run_id: str
    startup_timeout_seconds: float = 60.0
    browser_timeout_seconds: float = 120.0
    cleanup_timeout_seconds: float = 30.0
    web_build_timeout_seconds: float = 240.0
    skip_browser: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class CommandPlan:
    container_name: str
    livekit: tuple[str, ...]
    app: tuple[str, ...]
    worker: tuple[str, ...]
    web_build: tuple[str, ...]
    web_server: tuple[str, ...]
    browser: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "container_name": self.container_name,
            "livekit": list(self.livekit),
            "app": list(self.app),
            "worker": _redact_command(self.worker),
            "web_build": list(self.web_build),
            "web_server": list(self.web_server),
            "browser": list(self.browser),
        }


@dataclass
class ManagedProcess:
    name: str
    command: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    log_path: Path
    process: subprocess.Popen[bytes] | None = None
    _log_handle: Any = None

    def start(self) -> None:
        if self.process is not None:
            raise StackError(f"{self.name} has already been started")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("ab", buffering=0)
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=dict(self.environment),
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            self._close_log()
            raise

    def ensure_running(self) -> None:
        if self.process is None:
            raise StackError(f"{self.name} has not started")
        return_code = self.process.poll()
        if return_code is not None:
            raise StackError(f"{self.name} exited with status {return_code}\n{self.tail()}")

    def wait_success(self, timeout_seconds: float) -> None:
        if self.process is None:
            raise StackError(f"{self.name} has not started")
        try:
            return_code = self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self.stop()
            raise StackError(
                f"{self.name} exceeded its {timeout_seconds:.0f}s timeout\n{self.tail()}"
            ) from exc
        finally:
            self._close_log()
        if return_code != 0:
            raise StackError(f"{self.name} exited with status {return_code}\n{self.tail()}")

    def stop(self, *, grace_seconds: float = 8.0) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        self._close_log()

    def tail(self, *, max_bytes: int = 12_000) -> str:
        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                return handle.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return f"(no readable log at {self.log_path})"

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def make_paths(run_id: str) -> StackPaths:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise StackError("run ID must contain only lowercase letters, digits, and hyphens")
    run_dir = (PROJECT_ROOT / "var" / "voice-e2e" / run_id).resolve()
    evidence = (PROJECT_ROOT / "var" / "evals" / f"voice-e2e-{run_id}.jsonl").resolve()
    playwright_dir = run_dir / "playwright"
    return StackPaths(
        run_id=run_id,
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=evidence,
        playwright_dir=playwright_dir,
        browser_result=playwright_dir / "voice-rtc-result.json",
        stack_proof=run_dir / "stack-proof.json",
        web_workspace=run_dir / "web-workspace",
    )


def build_environment(paths: StackPaths, base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    for name in tuple(environment):
        if (
            name in _STRIPPED_ENV_NAMES
            or name.startswith(_STRIPPED_ENV_PREFIXES)
            or name.endswith(_SENSITIVE_ENV_SUFFIXES)
        ):
            environment.pop(name, None)

    # Empty values also prevent Next/python dotenv loaders from filling these
    # names from ambient developer configuration. Python dotenv loading is
    # disabled outright because this lane must never contact a provider.
    environment.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "NO_COLOR": "1",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "MURMUR_E2E_MODE": "1",
            "MURMUR_ENVIRONMENT": "test",
            "MURMUR_DATA_DIR": str(paths.run_dir / "data"),
            "MURMUR_DATABASE_URL": f"sqlite:///{paths.database}",
            "MURMUR_E2E_ASSISTANT_FIXTURE_PATH": str(ASSISTANT_FIXTURE.resolve()),
            "MURMUR_E2E_EVIDENCE_PATH": str(paths.evidence),
            "VOICE_RUNTIME": "livekit_v2",
            "VOICE_V2_PROFILE_ID": VOICE_PROFILE_ID,
            "VOICE_V2_WORKER_NAME": VOICE_WORKER_NAME,
            "VOICE_V2_SIGNING_SECRET": VOICE_SIGNING_SECRET,
            "VOICE_V2_TOKEN_TTL_SECONDS": "120",
            "VOICE_V2_JOB_METADATA_TTL_SECONDS": "120",
            "VOICE_V2_JOB_METADATA_CLOCK_SKEW_SECONDS": "10",
            "VOICE_V2_ROOM_EMPTY_TIMEOUT_SECONDS": "30",
            "VOICE_V2_ROOM_DEPARTURE_TIMEOUT_SECONDS": "5",
            "VOICE_V2_CONTROL_PLANE_TIMEOUT_SECONDS": "5",
            "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS": "2",
            "VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS": "5",
            "VOICE_V2_CONNECT_TIMEOUT_SECONDS": "10",
            "VOICE_V2_PARTICIPANT_WAIT_TIMEOUT_SECONDS": "15",
            "VOICE_V2_INPUT_WAIT_TIMEOUT_SECONDS": "10",
            "VOICE_V2_SESSION_START_TIMEOUT_SECONDS": "10",
            "VOICE_V2_EVENT_PUBLISH_TIMEOUT_SECONDS": "3",
            "LIVEKIT_URL": LIVEKIT_URL,
            "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
            "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
            "ALLOWED_CORS_ORIGINS": f"http://127.0.0.1:{WEB_PORT}",
            "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{APP_PORT}",
            "NEXT_PUBLIC_VOICE_RUNTIME": "voice_v2",
            "VOICE_E2E_API_URL": f"http://127.0.0.1:{APP_PORT}",
            "VOICE_E2E_WEB_URL": f"http://127.0.0.1:{WEB_PORT}",
            "VOICE_E2E_BROWSER_AUDIO_FIXTURE": str(BROWSER_FIXTURE.resolve()),
            "VOICE_E2E_ARTIFACT_DIR": str(paths.playwright_dir),
            "VOICE_E2E_RESULT_PATH": str(paths.browser_result),
            "SMART_TURN_ENABLED": "false",
            "DEEPGRAM_KEY": "",
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "",
            "GROQ_API_KEY": "",
            "GEMINI_API_KEY": "",
            "TTS_PROVIDER": "elevenlabs",
            "ELEVENLABS_API_KEY": "",
            "MEM0_API_KEY": "",
            "TAVILY_API_KEY": "",
            "FIREBASE_SERVICE_ACCOUNT_PATH": "",
            "FIREBASE_PROJECT_ID": "",
            "GOOGLE_APPLICATION_CREDENTIALS": "",
            "NEXT_PUBLIC_FIREBASE_API_KEY": "voice-e2e-test-key",
            "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": "voice-e2e.firebaseapp.com",
            "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "voice-e2e",
            "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET": "voice-e2e.appspot.com",
            "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID": "1234567890",
            "NEXT_PUBLIC_FIREBASE_APP_ID": "1:1234567890:web:voice-e2e",
            "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{paths.run_id}",
        }
    )
    return environment


def build_commands(paths: StackPaths, *, ownership_nonce: str | None = None) -> CommandPlan:
    container_name = f"murmur-voice-e2e-{paths.run_id}"
    ownership_nonce = ownership_nonce or "dry-run"
    python = sys.executable
    return CommandPlan(
        container_name=container_name,
        livekit=(
            "docker",
            "run",
            "--name",
            container_name,
            "--label",
            f"murmur.voice.e2e.run_id={paths.run_id}",
            "--label",
            f"murmur.voice.e2e.owner={ownership_nonce}",
            "--rm",
            "--publish",
            "127.0.0.1:7880:7880",
            "--publish",
            "127.0.0.1:7881:7881",
            "--publish",
            "127.0.0.1:7882:7882/udp",
            LIVEKIT_SERVER_IMAGE,
            "--dev",
            "--bind",
            "0.0.0.0",
            "--node-ip",
            "127.0.0.1",
            "--udp-port",
            "7882",
        ),
        app=(
            python,
            "-m",
            "uvicorn",
            "scripts.voice_e2e_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(APP_PORT),
        ),
        worker=(
            python,
            "-m",
            "livekit.agents",
            "start",
            "--url",
            LIVEKIT_URL,
            "--api-key",
            LIVEKIT_API_KEY,
            "--api-secret",
            LIVEKIT_API_SECRET,
            "--log-level",
            "INFO",
            "scripts/voice_e2e_worker.py",
        ),
        # Webpack accepts the isolated workspace's node_modules symlink. The
        # repository-wide frontend CI still exercises Next's default Turbopack
        # build, so this changes only the hermetic RTC stack composition.
        web_build=("npm", "run", "build", "--", "--webpack"),
        web_server=(
            "npm",
            "run",
            "start",
            "--",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(WEB_PORT),
        ),
        browser=("npm", "run", "test:e2e:offline"),
    )


def _redact_command(command: Sequence[str]) -> list[str]:
    redacted = list(command)
    for flag in ("--api-key", "--api-secret"):
        try:
            index = redacted.index(flag)
        except ValueError:
            continue
        if index + 1 < len(redacted):
            redacted[index + 1] = "<local-test-credential>"
    return redacted


def _validate_fixtures() -> None:
    for label, path in (
        ("assistant", ASSISTANT_FIXTURE),
        ("browser", BROWSER_FIXTURE),
    ):
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(VOICE_FIXTURE_ROOT.resolve()):
            raise StackError(f"{label} audio fixture is missing or outside {VOICE_FIXTURE_ROOT}")


def _preflight_ports() -> None:
    sockets: list[socket.socket] = []
    for transport, socket_type, ports in (
        ("TCP", socket.SOCK_STREAM, _TCP_PORTS),
        ("UDP", socket.SOCK_DGRAM, _UDP_PORTS),
    ):
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket_type)
            if socket_type == socket.SOCK_STREAM:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                sock.close()
                for reserved in sockets:
                    reserved.close()
                owner = _port_owner_hint(port, transport)
                raise StackError(
                    f"required {transport} loopback port {port} is unavailable ({exc}); {owner}"
                ) from exc
            sockets.append(sock)
    for sock in sockets:
        sock.close()


def _port_owner_hint(port: int, transport: str) -> str:
    selector = f"-iTCP:{port}" if transport == "TCP" else f"-iUDP:{port}"
    command = ["lsof", "-nP", selector]
    if transport == "TCP":
        command.append("-sTCP:LISTEN")
    if shutil.which("lsof") is not None:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.stdout.strip():
            rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return "owner: " + " | ".join(rows[:4])
    if transport == "TCP":
        return f"inspect the owner with `lsof -nP -iTCP:{port} -sTCP:LISTEN`"
    return f"inspect the owner with `lsof -nP -iUDP:{port}`"


def _require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise StackError(f"required executable is unavailable: {name}")


def _validate_local_dependencies() -> None:
    expected = {
        "livekit-agents": LIVEKIT_AGENTS_VERSION,
        "livekit-api": "1.2.0",
    }
    for package, version in expected.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise StackError(f"required locked dependency is unavailable: {package}") from exc
        if installed != version:
            raise StackError(f"{package} must be {version}, found {installed}")


def _http_response(url: str, *, timeout_seconds: float = 1.0) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "murmur-voice-e2e/1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def _http_json(url: str) -> dict[str, object]:
    status, body = _http_response(url)
    if status != 200:
        raise StackError(f"{url} returned HTTP {status}")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StackError(f"{url} did not return JSON") from exc
    if not isinstance(value, dict):
        raise StackError(f"{url} returned a non-object JSON response")
    return value


def _wait_for(
    description: str,
    probe: Callable[[], Any],
    *,
    timeout_seconds: float,
    processes: Sequence[ManagedProcess] = (),
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        for process in processes:
            process.ensure_running()
        try:
            value = probe()
            if value:
                return value
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    details = f": {last_error}" if last_error is not None else ""
    raise StackError(f"timed out waiting for {description}{details}")


def _read_browser_result(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StackError(f"browser did not write a valid result at {path}") from exc
    if not isinstance(value, dict):
        raise StackError("browser result must be a JSON object")
    if value.get("schema_version") != 1 or value.get("status") != "passed":
        raise StackError("browser result did not report schema_version=1 and status=passed")
    for field in ("room_name", "dispatch_id", "voice_call_id"):
        item = value.get(field)
        if not isinstance(item, str) or not _CONTRACT_ID_PATTERN.fullmatch(item):
            raise StackError(f"browser result contains an invalid {field}")
    return value


def _worker_state() -> dict[str, object]:
    state = _http_json(f"http://127.0.0.1:{WORKER_PORT}/worker")
    if state.get("agent_name") != VOICE_WORKER_NAME:
        raise StackError("worker health returned a different agent name")
    if state.get("sdk_version") != LIVEKIT_AGENTS_VERSION:
        raise StackError("worker health returned an unexpected LiveKit Agents version")
    active_jobs = state.get("active_jobs", 0)
    if isinstance(active_jobs, bool) or not isinstance(active_jobs, int) or active_jobs < 0:
        raise StackError("worker health returned an invalid active_jobs value")
    return {**state, "active_jobs": active_jobs}


async def _query_livekit_state(client: Any, room_name: str, dispatch_id: str) -> dict[str, object]:
    from livekit import api

    response = await client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
    rooms = [room.name for room in response.rooms]
    room_present = room_name in rooms
    if not room_present:
        # Dispatches are room-scoped in the pinned OSS server. After the room
        # actor is gone, ListDispatch returns 503 because there is no server to
        # answer the room topic. Exact ListRooms absence therefore proves both
        # resources are absent and avoids treating that expected 503 as an
        # inconclusive cleanup result.
        return {
            "room_name": room_name,
            "room_present": False,
            "dispatch_id": dispatch_id,
            "dispatch_present": False,
            "dispatch_ids": [],
            "dispatch_query": "room_absent_confirms_dispatch_absent",
        }

    try:
        dispatches = await client.agent_dispatch.list_dispatch(room_name)
    except api.TwirpError as exc:
        if exc.status == 404 or exc.code.lower() == "not_found":
            dispatch_ids: list[str] = []
            dispatch_query = "room_not_found"
        else:
            raise
    else:
        dispatch_ids = [dispatch.id for dispatch in dispatches]
        dispatch_query = "listed"
    return {
        "room_name": room_name,
        "room_present": True,
        "dispatch_id": dispatch_id,
        "dispatch_present": dispatch_id in dispatch_ids,
        "dispatch_ids": dispatch_ids,
        "dispatch_query": dispatch_query,
    }


async def _livekit_state(room_name: str, dispatch_id: str) -> dict[str, object]:
    from livekit import api

    client = api.LiveKitAPI(
        url="http://127.0.0.1:7880",
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    try:
        return await _query_livekit_state(client, room_name, dispatch_id)
    finally:
        await client.aclose()


def _read_evidence(path: Path) -> list[dict[str, object]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StackError(f"fake profile evidence is unavailable at {path}") from exc
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StackError(f"invalid fake profile evidence on line {line_number}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("event"), str):
            raise StackError(f"invalid fake profile evidence object on line {line_number}")
        records.append(record)
    return records


def _evidence_summary(path: Path, voice_call_id: str) -> dict[str, object]:
    records = _read_evidence(path)
    profile_closes = [record for record in records if record.get("event") == "profile_closed"]
    matching_close = [
        record for record in profile_closes if record.get("voice_call_id") == voice_call_id
    ]
    counts: dict[str, int] = {}
    for record in records:
        event = str(record["event"])
        counts[event] = counts.get(event, 0) + 1
    final_transcripts = sum(
        record.get("event") == "transcript_emitted" and record.get("transcript_type") == "final"
        for record in records
    )
    media_contract_satisfied = (
        counts.get("input_frame", 0) > 0
        and counts.get("speech_onset", 0) == 2
        and final_transcripts == 2
        and counts.get("tts_started", 0) >= 1
        and counts.get("tts_cancelled", 0) >= 1
        and len(profile_closes) == 1
        and len(matching_close) == 1
    )
    return {
        "record_count": len(records),
        "event_counts": counts,
        "final_transcript_emitted": final_transcripts,
        "profile_closed": len(profile_closes),
        "matching_profile_closed": len(matching_close),
        "media_contract_satisfied": media_contract_satisfied,
    }


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class VoiceE2EStack:
    def __init__(self, options: StackOptions) -> None:
        self.options = options
        self.paths = make_paths(options.run_id)
        self.environment = build_environment(self.paths)
        self._ownership_nonce = uuid.uuid4().hex
        self.commands = build_commands(self.paths, ownership_nonce=self._ownership_nonce)
        self._processes: list[ManagedProcess] = []
        self._web_cwd = WEB_ROOT
        self._owned_container_id: str | None = None

    def describe(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "run_id": self.paths.run_id,
            "run_dir": str(self.paths.run_dir),
            "livekit_server_image": LIVEKIT_SERVER_IMAGE,
            "livekit_server_version": LIVEKIT_SERVER_VERSION,
            "livekit_agents_version": LIVEKIT_AGENTS_VERSION,
            "commands": self.commands.public_dict(),
            "ports": {"tcp": list(_TCP_PORTS), "udp": list(_UDP_PORTS)},
            "skip_browser": self.options.skip_browser,
        }

    def run(self) -> dict[str, object]:
        _validate_fixtures()
        if self.options.dry_run:
            return self.describe()

        if self.paths.run_dir.exists() or self.paths.evidence.exists():
            raise StackError(f"refusing to reuse existing E2E run ID: {self.paths.run_id}")
        _require_executable("docker")
        _require_executable("npm")
        _validate_local_dependencies()
        self._docker_info()
        self._preflight_container_name()
        _preflight_ports()

        self.paths.playwright_dir.mkdir(parents=True, mode=0o700)
        self.paths.evidence.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._prepare_web_workspace()
            self._run_step(
                "Next production build",
                self.commands.web_build,
                self._web_cwd,
                self.paths.run_dir / "web-build.log",
                self.options.web_build_timeout_seconds,
            )
            # A long build gives another process time to occupy a required
            # port, so validate the exact topology again immediately before
            # binding any service.
            _preflight_ports()

            livekit = self._start(
                "LiveKit SFU",
                self.commands.livekit,
                PROJECT_ROOT,
                self.paths.run_dir / "livekit.log",
            )
            self._claim_started_container(livekit)
            self._wait_livekit(livekit)

            app = self._start(
                "FastAPI E2E app",
                self.commands.app,
                PROJECT_ROOT,
                self.paths.run_dir / "app.log",
            )
            self._wait_app(app, livekit)

            worker = self._start(
                "LiveKit E2E worker",
                self.commands.worker,
                PROJECT_ROOT,
                self.paths.run_dir / "worker.log",
            )
            self._wait_worker(worker, livekit)

            web = self._start(
                "Next E2E server",
                self.commands.web_server,
                self._web_cwd,
                self.paths.run_dir / "web.log",
            )
            self._wait_web(web)

            if self.options.skip_browser:
                worker_state = _worker_state()
                if worker_state["active_jobs"] != 0:
                    raise StackError("worker was not idle during infrastructure smoke test")
                return {
                    **self.describe(),
                    "status": "infrastructure_ready",
                    "worker": worker_state,
                }

            self._run_step(
                "Playwright RTC browser",
                self.commands.browser,
                self._web_cwd,
                self.paths.run_dir / "playwright.log",
                self.options.browser_timeout_seconds,
            )
            browser = _read_browser_result(self.paths.browser_result)
            proof = self._wait_for_cleanup(browser, worker, livekit)
            _atomic_write_json(self.paths.stack_proof, proof)
            return proof
        finally:
            self._teardown()

    def _docker_info(self) -> None:
        try:
            result = subprocess.run(
                ("docker", "info", "--format", "{{.ServerVersion}}"),
                cwd=PROJECT_ROOT,
                env=self.environment,
                check=False,
                capture_output=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired as exc:
            raise StackError("Docker daemon readiness check timed out") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise StackError(f"Docker daemon is unavailable: {detail}")

    def _preflight_container_name(self) -> None:
        result = subprocess.run(
            (
                "docker",
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{self.commands.container_name}$",
                "--format",
                "{{.ID}}",
            ),
            cwd=PROJECT_ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise StackError(f"Docker container-name preflight failed: {detail}")
        if result.stdout.strip():
            raise StackError(
                f"refusing to reuse existing Docker container: {self.commands.container_name}"
            )

    def _claim_started_container(self, process: ManagedProcess) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            process.ensure_running()
            result = subprocess.run(
                (
                    "docker",
                    "container",
                    "inspect",
                    self.commands.container_name,
                    "--format",
                    '{{.Id}} {{index .Config.Labels "murmur.voice.e2e.owner"}}',
                ),
                cwd=PROJECT_ROOT,
                env=self.environment,
                check=False,
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                decoded = result.stdout.decode("utf-8", errors="replace").strip().split()
                if len(decoded) != 2 or decoded[1] != self._ownership_nonce:
                    raise StackError("started LiveKit container ownership label did not match")
                self._owned_container_id = decoded[0]
                return
            time.sleep(0.05)
        raise StackError("timed out confirming ownership of the LiveKit container")

    def _prepare_web_workspace(self) -> None:
        destination = self.paths.web_workspace
        if destination.exists():
            raise StackError(f"isolated web workspace already exists: {destination}")
        destination.mkdir(parents=True, mode=0o700)
        required_entries = (
            ".env.example",
            "e2e",
            "eslint.config.mjs",
            "next-env.d.ts",
            "next.config.mjs",
            "package-lock.json",
            "package.json",
            "playwright.config.ts",
            "postcss.config.mjs",
            "src",
            "tailwind.config.ts",
            "tsconfig.json",
            "vitest.config.mts",
        )
        for relative in required_entries:
            source = WEB_ROOT / relative
            target = destination / relative
            if source.is_dir():
                shutil.copytree(source, target, symlinks=False)
            elif source.is_file():
                shutil.copy2(source, target, follow_symlinks=False)
            else:
                raise StackError(f"isolated web workspace source is missing: {source}")

        # Dependency installation is an explicit prerequisite. A per-run
        # symlink avoids copying a large immutable tree while keeping all build
        # writes and generated type files under ignored run artifacts.
        node_modules = WEB_ROOT / "node_modules"
        if not node_modules.is_dir():
            raise StackError("frontend dependencies are missing; run `npm ci` in web")
        (destination / "node_modules").symlink_to(node_modules, target_is_directory=True)
        self._web_cwd = destination

    def _start(
        self,
        name: str,
        command: tuple[str, ...],
        cwd: Path,
        log_path: Path,
    ) -> ManagedProcess:
        process = ManagedProcess(name, command, cwd, self.environment, log_path)
        process.start()
        self._processes.append(process)
        return process

    def _run_step(
        self,
        name: str,
        command: tuple[str, ...],
        cwd: Path,
        log_path: Path,
        timeout_seconds: float,
    ) -> None:
        process = ManagedProcess(name, command, cwd, self.environment, log_path)
        process.start()
        try:
            process.wait_success(timeout_seconds)
        except BaseException:
            process.stop()
            raise

    def _wait_livekit(self, process: ManagedProcess) -> None:
        def ready() -> bool:
            status, body = _http_response("http://127.0.0.1:7880/")
            return status == 200 and body.strip() == b"OK"

        _wait_for(
            "LiveKit SFU health",
            ready,
            timeout_seconds=self.options.startup_timeout_seconds,
            processes=(process,),
        )

    def _wait_app(self, app: ManagedProcess, livekit: ManagedProcess) -> None:
        def ready() -> bool:
            health = _http_json(f"http://127.0.0.1:{APP_PORT}/_e2e/health")
            return (
                health.get("ok") is True
                and health.get("agent_id") == E2E_AGENT_ID
                and health.get("session_id") == E2E_SESSION_ID
            )

        _wait_for(
            "guarded FastAPI health",
            ready,
            timeout_seconds=self.options.startup_timeout_seconds,
            processes=(app, livekit),
        )

    def _wait_worker(self, worker: ManagedProcess, livekit: ManagedProcess) -> None:
        def ready() -> bool:
            state = _worker_state()
            return state["active_jobs"] == 0

        _wait_for(
            "registered LiveKit worker health",
            ready,
            timeout_seconds=self.options.startup_timeout_seconds,
            processes=(worker, livekit),
        )

    def _wait_web(self, web: ManagedProcess) -> None:
        def ready() -> bool:
            status, body = _http_response(f"http://127.0.0.1:{WEB_PORT}/e2e/voice")
            return status == 200 and bool(body.strip())

        _wait_for(
            "guarded Next E2E route",
            ready,
            timeout_seconds=self.options.startup_timeout_seconds,
            processes=(web,),
        )

    def _wait_for_cleanup(
        self,
        browser: Mapping[str, object],
        worker: ManagedProcess,
        livekit: ManagedProcess,
    ) -> dict[str, object]:
        room_name = str(browser["room_name"])
        dispatch_id = str(browser["dispatch_id"])
        voice_call_id = str(browser["voice_call_id"])

        def cleaned() -> dict[str, object] | None:
            livekit_state = asyncio.run(_livekit_state(room_name, dispatch_id))
            worker_state = _worker_state()
            evidence = _evidence_summary(self.paths.evidence, voice_call_id)
            if (
                livekit_state["room_present"] is False
                and livekit_state["dispatch_present"] is False
                and livekit_state["dispatch_ids"] == []
                and worker_state["active_jobs"] == 0
                and evidence["media_contract_satisfied"] is True
            ):
                return {
                    "schema_version": 1,
                    "status": "passed",
                    "run_id": self.paths.run_id,
                    "browser": dict(browser),
                    "livekit_cleanup": livekit_state,
                    "worker_cleanup": worker_state,
                    "fake_profile_cleanup": evidence,
                    "artifacts": {
                        "run_dir": str(self.paths.run_dir),
                        "evidence": str(self.paths.evidence),
                    },
                }
            return None

        return _wait_for(
            "room, dispatch, worker-job, and fake-profile cleanup",
            cleaned,
            timeout_seconds=self.options.cleanup_timeout_seconds,
            processes=(worker, livekit),
        )

    def _teardown(self) -> None:
        for process in reversed(self._processes):
            process.stop()
        container_id = self._owned_container_id
        self._owned_container_id = None
        if container_id is not None:
            try:
                subprocess.run(
                    ("docker", "rm", "--force", container_id),
                    cwd=PROJECT_ROOT,
                    env=self.environment,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        workspace = self.paths.web_workspace.resolve()
        run_dir = self.paths.run_dir.resolve()
        if workspace.parent == run_dir and workspace.name == "web-workspace":
            shutil.rmtree(workspace, ignore_errors=True)


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 0 < parsed <= 900:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 900")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None, help="stable lowercase artifact identifier")
    parser.add_argument("--dry-run", action="store_true", help="print commands without starting")
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="start and health-check infrastructure without claiming media acceptance",
    )
    parser.add_argument("--startup-timeout", type=_positive_seconds, default=60.0)
    parser.add_argument("--browser-timeout", type=_positive_seconds, default=120.0)
    parser.add_argument("--cleanup-timeout", type=_positive_seconds, default=30.0)
    parser.add_argument("--web-build-timeout", type=_positive_seconds, default=240.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = StackOptions(
        run_id=args.run_id or _new_run_id(),
        startup_timeout_seconds=args.startup_timeout,
        browser_timeout_seconds=args.browser_timeout,
        cleanup_timeout_seconds=args.cleanup_timeout,
        web_build_timeout_seconds=args.web_build_timeout,
        skip_browser=args.skip_browser,
        dry_run=args.dry_run,
    )
    try:
        result = VoiceE2EStack(options).run()
    except KeyboardInterrupt:
        print("Voice RTC E2E interrupted", file=sys.stderr)
        return 130
    except StackError as exc:
        print(f"Voice RTC E2E failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

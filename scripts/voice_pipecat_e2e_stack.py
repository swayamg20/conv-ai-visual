#!/usr/bin/env python3
"""Prove Murmur's Docker-free Pipecat SmallWebRTC path on loopback.

The default runner builds the production web harness, boots the guarded
dedicated Pipecat ASGI owner, drives real Chromium audio through one production
SmallWebRTC peer, and retains only sanitized qualification artifacts.  The
``--backend-only`` mode preserves the narrower authenticated bootstrap/release
checkpoint without making a browser-media claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.voice_pipecat_e2e_coturn import (
    CoturnContractError,
    CoturnContractPaths,
    PipecatE2ENetworkMode,
    parse_network_mode,
    read_private_coturn_configuration,
    validate_turn_tls_ca_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
VOICE_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "voice" / "audio"
ASSISTANT_FIXTURE = VOICE_FIXTURE_ROOT / "assistant-long.wav"
BROWSER_FIXTURE = VOICE_FIXTURE_ROOT / "browser-barge-in.wav"
PIPECAT_HOST = "127.0.0.1"
PIPECAT_PORT = 8101
PIPECAT_BASE_URL = f"http://{PIPECAT_HOST}:{PIPECAT_PORT}"
PIPECAT_SIGNALING_BASE_URL = f"{PIPECAT_BASE_URL}/api/voice/pipecat/signal"
WEB_HOST = "127.0.0.1"
WEB_PORT = 3100
WEB_BASE_URL = f"http://{WEB_HOST}:{WEB_PORT}"
VOICE_PROFILE_ID = "pipecat-fake-rtc-v1"
VOICE_RUNTIME = "pipecat_smallwebrtc_v1"
E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76"
E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"
AUTHORIZATION = "Bearer voice-e2e"
_PLAYWRIGHT_PROCESS_NAME = "Playwright Pipecat RTC browser"
_AUDIO_CLOCK_QUANTUM_FRAMES = 128
_AUDIO_CLOCK_MAX_TRANSITIONS = 24
_AUDIO_CLOCK_LOCAL_ACTIVE_RMS = 0.005
_AUDIO_CLOCK_REMOTE_SILENCE_RMS = 0.012
_AUDIO_CLOCK_LOCAL_REGION_BRIDGE_MS = 500
_AUDIO_CLOCK_REQUIRED_SILENCE_MS = 200
_AUDIO_CLOCK_MAX_INTERRUPTION_MS = 250
_PROOF_TIMEOUT_PROGRESS_PREFIX = "VOICE_PIPECAT_PROOF_TIMEOUT_PROGRESS="
_PROOF_TIMEOUT_PROGRESS_MAX_BYTES = 2_048
_PROOF_TIMEOUT_PROGRESS_MAX_COUNTER = 2_147_483_647
_PROOF_TIMEOUT_PROGRESS_ERROR = "Playwright proof timeout progress capsule is invalid"
_TEARDOWN_FAILURE_CLASSIFICATION = "qualification_teardown=failed"
_ARTIFACT_SAFETY_FAILURE_CLASSIFICATION = "artifact_safety=failed_closed"
_RELAY_TLS_CONTRACT_ONLY_ERROR = "relay-tls contract defined; media qualification unavailable"

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_CONTRACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_PROVENANCE_ERROR = "source provenance is unavailable for RTC qualification"
_DIRTY_SOURCE_ERROR = "source repository must be clean for RTC qualification"
_SOURCE_CHANGED_ERROR = "source provenance changed during RTC qualification"
_ARTIFACT_MANIFEST_ERROR = "qualification artifact manifest is incomplete or unsafe"
_ARTIFACT_TAMPER_ERROR = "qualification artifact manifest no longer matches artifacts"
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
    "NEXT_PUBLIC_FIREBASE_",
    "NEXT_PUBLIC_LIVEKIT_",
    "OPENAI_",
    "SMART_TURN_",
    "TAVILY_",
    "TTS_",
    "TURN_",
    "VOICE_",
    "COTURN_",
)
_STRIPPED_ENV_NAMES = {
    "DATABASE_URL",
    "CURL_CA_BUNDLE",
    "FORCE_COLOR",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "NO_PROXY",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "PYTHONPATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSLKEYLOGFILE",
    "no_proxy",
}
_SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_API_SECRET",
    "_AUTH_TOKEN",
    "_CREDENTIALS",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_TOKEN",
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

    @property
    def playwright_dir(self) -> Path:
        return self.run_dir / "playwright"

    @property
    def browser_result(self) -> Path:
        return self.playwright_dir / "voice-pipecat-rtc-result.json"

    @property
    def playwright_report(self) -> Path:
        return self.playwright_dir / "report.json"

    @property
    def rtc_proof(self) -> Path:
        return self.run_dir / "rtc-stack-proof.json"

    @property
    def web_workspace(self) -> Path:
        return self.run_dir / "web-workspace"


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


GitCommandRunner = Callable[
    [tuple[str, ...], Path],
    subprocess.CompletedProcess[str],
]
SourceReader = Callable[[], object]


def _default_git_command_runner(
    arguments: tuple[str, ...],
    repository_root: Path,
) -> subprocess.CompletedProcess[str]:
    safe_environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ("git", "-C", str(repository_root), *arguments),
        cwd=repository_root,
        env=safe_environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _read_source_provenance(
    *,
    repository_root: Path = PROJECT_ROOT,
    command_runner: GitCommandRunner = _default_git_command_runner,
) -> dict[str, object]:
    """Fail closed on unknown or dirty Git state without exposing Git output."""

    try:
        resolved_root = repository_root.resolve(strict=True)
        root_result = command_runner(("rev-parse", "--show-toplevel"), resolved_root)
        sha_result = command_runner(
            ("rev-parse", "--verify", "HEAD^{commit}"),
            resolved_root,
        )
        status_result = command_runner(
            (
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            resolved_root,
        )
    except (OSError, subprocess.SubprocessError):
        raise StackError(_SOURCE_PROVENANCE_ERROR) from None

    if (
        not all(
            isinstance(result, subprocess.CompletedProcess)
            and isinstance(result.returncode, int)
            and not isinstance(result.returncode, bool)
            and isinstance(result.stdout, str)
            for result in (root_result, sha_result, status_result)
        )
        or root_result.returncode != 0
        or sha_result.returncode != 0
        or status_result.returncode != 0
    ):
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    root_output = root_result.stdout.strip()
    sha = sha_result.stdout.strip()
    if (
        not root_output
        or "\n" in root_output
        or "\r" in root_output
        or not _GIT_SHA_PATTERN.fullmatch(sha)
    ):
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    try:
        derived_root = Path(root_output).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise StackError(_SOURCE_PROVENANCE_ERROR) from None
    if derived_root != resolved_root:
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    if status_result.stdout:
        raise StackError(_DIRTY_SOURCE_ERROR)
    return {
        "commit_sha": sha,
        "repository_clean": True,
        "dirty_state_refused": True,
    }


def _validate_source_provenance(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "commit_sha",
        "repository_clean",
        "dirty_state_refused",
    }:
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    commit_sha = value.get("commit_sha")
    if (
        not isinstance(commit_sha, str)
        or not _GIT_SHA_PATTERN.fullmatch(commit_sha)
        or value.get("repository_clean") is not True
        or value.get("dirty_state_refused") is not True
    ):
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    return {
        "commit_sha": commit_sha,
        "repository_clean": True,
        "dirty_state_refused": True,
    }


def _require_unchanged_source(
    source_reader: SourceReader,
    expected: Mapping[str, object],
) -> None:
    observed = _validate_source_provenance(source_reader())
    if observed != expected:
        raise StackError(_SOURCE_CHANGED_ERROR)


def _clean_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    environment = {
        key: value
        for key, value in source.items()
        if key not in _STRIPPED_ENV_NAMES
        and not any(key.startswith(prefix) for prefix in _STRIPPED_ENV_PREFIXES)
        and not key.endswith(_SENSITIVE_ENV_SUFFIXES)
    }
    environment.update(
        {
            "NO_COLOR": "1",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "PYTHON_DOTENV_DISABLED": "1",
            "no_proxy": "127.0.0.1,localhost,::1",
        }
    )
    return environment


def build_environment(
    paths: StackPaths,
    base: Mapping[str, str] | None = None,
    *,
    network: PipecatE2ENetworkMode | str = PipecatE2ENetworkMode.DIRECT,
    turn_configuration_file: Path | None = None,
    turn_tls_ca_file: Path | None = None,
) -> dict[str, str]:
    try:
        mode = parse_network_mode(network)
    except CoturnContractError as exc:
        raise StackError("Pipecat E2E network mode is invalid") from exc
    if mode is PipecatE2ENetworkMode.DIRECT and (
        turn_configuration_file is not None or turn_tls_ca_file is not None
    ):
        raise StackError("direct Pipecat E2E does not accept relay material")
    relay_environment: dict[str, str] = {}
    if mode is PipecatE2ENetworkMode.RELAY_TLS:
        if turn_configuration_file is None or turn_tls_ca_file is None:
            raise StackError("relay-tls Pipecat E2E material is unavailable")
        try:
            coturn_paths = CoturnContractPaths.for_run_dir(paths.run_id, paths.run_dir)
            if turn_configuration_file != coturn_paths.config:
                raise CoturnContractError("Coturn configuration path is invalid")
            read_private_coturn_configuration(
                turn_configuration_file,
                expected_run_dir=paths.run_dir,
            )
            certificate = validate_turn_tls_ca_file(
                turn_tls_ca_file,
                expected_run_dir=paths.run_dir,
            )
        except CoturnContractError as exc:
            raise StackError("relay-tls Pipecat E2E material is unavailable") from exc
        relay_environment = {
            "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE": str(coturn_paths.config),
            "SSL_CERT_FILE": str(certificate),
        }
    environment = _clean_environment(base)
    python_path = os.pathsep.join((str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT)))
    environment.update(
        {
            "ALLOWED_CORS_ORIGINS": "http://127.0.0.1:3100,http://localhost:3100",
            "MURMUR_DATABASE_URL": f"sqlite:///{paths.database}",
            "MURMUR_E2E_MODE": "1",
            "MURMUR_ENVIRONMENT": "test",
            "MURMUR_PIPECAT_E2E_ASSISTANT_FIXTURE_PATH": str(ASSISTANT_FIXTURE.resolve()),
            "MURMUR_PIPECAT_E2E_EVIDENCE_PATH": str(paths.evidence),
            "MURMUR_PIPECAT_E2E_NETWORK": mode.value,
            "PIPECAT_HOST": PIPECAT_HOST,
            "PIPECAT_PORT": str(PIPECAT_PORT),
            "PIPECAT_SIGNALING_BASE_URL": PIPECAT_SIGNALING_BASE_URL,
            "PYTHONPATH": python_path,
            "VOICE_RUNTIME": VOICE_RUNTIME,
            "VOICE_V2_PROFILE_ID": VOICE_PROFILE_ID,
        }
    )
    environment.update(relay_environment)
    return environment


def build_web_environment(
    paths: StackPaths,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a separate browser/build environment with dummy public config."""

    environment = _clean_environment(base)
    environment.update(
        {
            "CI": "1",
            "MURMUR_E2E_MODE": "1",
            "NEXT_PUBLIC_API_URL": PIPECAT_BASE_URL,
            "NEXT_PUBLIC_FIREBASE_API_KEY": "voice-pipecat-e2e-test-key",
            "NEXT_PUBLIC_FIREBASE_APP_ID": "1:1234567890:web:voice-pipecat-e2e",
            "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": "voice-pipecat-e2e.firebaseapp.com",
            "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID": "1234567890",
            "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "voice-pipecat-e2e",
            "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET": "voice-pipecat-e2e.appspot.com",
            "NEXT_PUBLIC_VOICE_RUNTIME": "voice_v2",
            "NEXT_TELEMETRY_DISABLED": "1",
            "VOICE_E2E_API_URL": PIPECAT_BASE_URL,
            "VOICE_E2E_ARTIFACT_DIR": str(paths.playwright_dir),
            "VOICE_E2E_BROWSER_AUDIO_FIXTURE": str(BROWSER_FIXTURE.resolve()),
            "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{paths.run_id}",
            "VOICE_E2E_RESULT_PATH": str(paths.browser_result),
            "VOICE_E2E_WEB_URL": WEB_BASE_URL,
        }
    )
    return environment


class PipecatBackendCheckpoint:
    def __init__(
        self,
        paths: StackPaths,
        *,
        startup_timeout_seconds: float = 30.0,
        source_reader: SourceReader = _read_source_provenance,
    ) -> None:
        if not 1 <= startup_timeout_seconds <= 120:
            raise StackError("startup timeout must be between one and 120 seconds")
        self.paths = paths
        self.startup_timeout_seconds = startup_timeout_seconds
        self.environment = build_environment(paths)
        self._source_reader = source_reader
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any | None = None

    def run(self) -> dict[str, object]:
        source = _validate_source_provenance(self._source_reader())
        self._prepare()
        try:
            self._start_app()
            health = self._wait_for_health()
            voice_call_id = str(uuid.uuid4())
            assignment = self._bootstrap(voice_call_id)
            self._release(voice_call_id)
            status = self._status(voice_call_id)
            self._assert_backend_checkpoint(health, assignment, status, voice_call_id)
            _require_unchanged_source(self._source_reader, source)
            proof = {
                "schema_version": 1,
                "status": "backend_checkpoint_passed",
                "run_id": self.paths.run_id,
                "runtime": VOICE_RUNTIME,
                "profile_id": VOICE_PROFILE_ID,
                "network": "direct-loopback",
                "providers": "fake",
                "cost": "unmeasured",
                "source": source,
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


@dataclass
class _ManagedProcess:
    name: str
    command: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    log_path: Path
    process: subprocess.Popen[bytes] | None = None
    _log_handle: Any | None = None

    def start(self) -> None:
        if self.process is not None:
            raise StackError(f"{self.name} has already started")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("xb", buffering=0)
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
        except BaseException:
            self._close_log()
            raise

    def ensure_running(self) -> None:
        if self.process is None:
            raise StackError(f"{self.name} has not started")
        return_code = self.process.poll()
        if return_code is not None:
            raise StackError(
                f"{self.name} exited with status {return_code}\n{self.sanitized_tail()}"
            )

    def wait_success(self, timeout_seconds: float) -> None:
        if self.process is None:
            raise StackError(f"{self.name} has not started")
        try:
            return_code = self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self.stop()
            raise StackError(f"{self.name} exceeded its timeout\n{self.sanitized_tail()}") from exc
        finally:
            self._close_log()
        if return_code != 0:
            raise StackError(
                f"{self.name} exited with status {return_code}\n{self.sanitized_tail()}"
            )

    def stop(self, *, grace_seconds: float = 8.0) -> None:
        try:
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
                    except subprocess.TimeoutExpired as exc:
                        raise StackError(
                            f"{self.name} did not stop after process-group termination"
                        ) from exc
        finally:
            self._close_log()

    def sanitized_tail(self, *, max_bytes: int = 12_000) -> str:
        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                value = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return f"(no readable log for {self.name})"
        sanitized = _sanitize_sensitive_text(value).strip()
        if self.name == _PLAYWRIGHT_PROCESS_NAME and _browser_secret_findings(sanitized):
            return "[Playwright diagnostics redacted after forbidden browser data]"
        return sanitized

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class PipecatBrowserStack:
    """Own the exact local ASGI, production web, and Chromium process graph."""

    def __init__(
        self,
        paths: StackPaths,
        *,
        startup_timeout_seconds: float = 60.0,
        browser_timeout_seconds: float = 120.0,
        web_build_timeout_seconds: float = 240.0,
        source_reader: SourceReader = _read_source_provenance,
    ) -> None:
        for label, value, maximum in (
            ("startup", startup_timeout_seconds, 120.0),
            ("browser", browser_timeout_seconds, 180.0),
            ("web build", web_build_timeout_seconds, 600.0),
        ):
            if not 1 <= value <= maximum:
                raise StackError(f"{label} timeout is outside its bounded range")
        self.paths = paths
        self.startup_timeout_seconds = startup_timeout_seconds
        self.browser_timeout_seconds = browser_timeout_seconds
        self.web_build_timeout_seconds = web_build_timeout_seconds
        self._source_reader = source_reader
        self.backend_environment = build_environment(paths)
        self.web_environment = build_web_environment(paths)
        self._processes: list[_ManagedProcess] = []
        self._owned_logs: list[Path] = []
        self._web_cwd = WEB_ROOT

    def run(self) -> dict[str, object]:
        source = _validate_source_provenance(self._source_reader())
        self._prepare()
        browser: dict[str, object] | None = None
        health: dict[str, object] | None = None
        terminal: dict[str, object] | None = None
        primary_failure: BaseException | None = None
        try:
            self._prepare_web_workspace()
            self._run_step(
                "Next production build",
                ("npm", "run", "build", "--", "--webpack"),
                self._web_cwd,
                self.paths.run_dir / "web-build.log",
                self.web_build_timeout_seconds,
                self.web_environment,
            )
            _require_port_available(PIPECAT_HOST, PIPECAT_PORT)
            _require_port_available(WEB_HOST, WEB_PORT)

            app = self._start(
                "Pipecat ASGI owner",
                _pipecat_app_command(),
                PROJECT_ROOT,
                self.paths.server_log,
                self.backend_environment,
            )
            health = self._wait_for_app(app)
            web = self._start(
                "Next E2E server",
                (
                    "npm",
                    "run",
                    "start",
                    "--",
                    "--hostname",
                    WEB_HOST,
                    "--port",
                    str(WEB_PORT),
                ),
                self._web_cwd,
                self.paths.run_dir / "web.log",
                self.web_environment,
            )
            self._wait_for_web(web, app)
            self._run_step(
                _PLAYWRIGHT_PROCESS_NAME,
                _pipecat_browser_command(),
                self._web_cwd,
                self.paths.run_dir / "playwright.log",
                self.browser_timeout_seconds,
                self.web_environment,
            )
            app.ensure_running()
            web.ensure_running()
            browser = _read_pipecat_browser_result(self.paths.browser_result)
            _validate_playwright_report(self.paths.playwright_report)
            terminal = self._authoritative_terminal_status(str(browser["voice_call_id"]))
            if terminal != browser.get("terminal_cleanup"):
                raise StackError("browser and authoritative terminal cleanup snapshots differ")
            _validate_pipecat_terminal_status(terminal, str(browser["voice_call_id"]))
        except BaseException as exc:
            primary_failure = self._classify_primary_failure(exc)

        finalizer_classifications: list[str] = []
        finalizer_system_failure: BaseException | None = None
        for classification, finalizer in (
            (_TEARDOWN_FAILURE_CLASSIFICATION, self._teardown),
            (_ARTIFACT_SAFETY_FAILURE_CLASSIFICATION, self._sanitize_owned_logs),
        ):
            try:
                finalizer()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    if finalizer_system_failure is None:
                        finalizer_system_failure = exc
                else:
                    finalizer_classifications.append(classification)

        if primary_failure is not None:
            if isinstance(primary_failure, (KeyboardInterrupt, SystemExit)):
                raise primary_failure
            if finalizer_system_failure is not None:
                raise finalizer_system_failure
            if finalizer_classifications:
                raise StackError(
                    "\n".join((str(primary_failure), *finalizer_classifications))
                ) from None
            raise primary_failure
        if finalizer_system_failure is not None:
            raise finalizer_system_failure
        if finalizer_classifications:
            raise StackError("\n".join(finalizer_classifications)) from None

        if browser is None or health is None or terminal is None:
            raise StackError("Pipecat browser stack ended without complete proof state")
        if health.get("livekit_imported") is not False:
            raise StackError("dedicated Pipecat process loaded a LiveKit module")
        sanitized_browser = _read_pipecat_browser_result(self.paths.browser_result)
        _validate_playwright_report(self.paths.playwright_report)
        if sanitized_browser != browser:
            raise StackError("sanitized browser result changed after validation")
        browser = sanitized_browser
        _scan_qualification_artifacts(self.paths)
        manifest = _build_artifact_sha256_manifest(self.paths)
        proof = self._build_proof(browser, health, terminal, source, manifest)
        _validate_rtc_stack_proof(self.paths, proof, source, manifest)
        _assert_browser_artifact_safe(json.dumps(proof, sort_keys=True), "stack proof")
        _require_unchanged_source(self._source_reader, source)
        _validate_artifact_sha256_manifest(self.paths, manifest)
        return _write_validated_rtc_stack_proof(self.paths, proof, source, manifest)

    def _prepare(self) -> None:
        for label, fixture in (
            ("assistant", ASSISTANT_FIXTURE),
            ("browser", BROWSER_FIXTURE),
        ):
            if (
                not fixture.resolve().is_relative_to(VOICE_FIXTURE_ROOT.resolve())
                or not fixture.is_file()
            ):
                raise StackError(f"checked-in {label} fixture is missing")
        if self.paths.run_dir.exists() or self.paths.evidence.exists():
            raise StackError("run artifacts already exist; choose a fresh run ID")
        if shutil.which("npm") is None:
            raise StackError("npm is required for the production browser harness")
        if not (WEB_ROOT / "node_modules").is_dir():
            raise StackError("frontend dependencies are missing; run npm ci in web")
        _require_port_available(PIPECAT_HOST, PIPECAT_PORT)
        _require_port_available(WEB_HOST, WEB_PORT)
        self.paths.playwright_dir.mkdir(parents=True, mode=0o700)
        self.paths.evidence.parent.mkdir(parents=True, exist_ok=True)

    def _prepare_web_workspace(self) -> None:
        destination = self.paths.web_workspace
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
                raise StackError(f"isolated web source is missing: {relative}")
        (destination / "node_modules").symlink_to(
            WEB_ROOT / "node_modules",
            target_is_directory=True,
        )
        self._web_cwd = destination

    def _start(
        self,
        name: str,
        command: tuple[str, ...],
        cwd: Path,
        log_path: Path,
        environment: Mapping[str, str],
    ) -> _ManagedProcess:
        process = _ManagedProcess(name, command, cwd, environment, log_path)
        process.start()
        self._processes.append(process)
        self._owned_logs.append(log_path)
        return process

    def _run_step(
        self,
        name: str,
        command: tuple[str, ...],
        cwd: Path,
        log_path: Path,
        timeout_seconds: float,
        environment: Mapping[str, str],
    ) -> None:
        process = _ManagedProcess(name, command, cwd, environment, log_path)
        self._owned_logs.append(log_path)
        process.start()
        try:
            process.wait_success(timeout_seconds)
        except BaseException:
            process.stop()
            raise

    def _wait_for_app(self, app: _ManagedProcess) -> dict[str, object]:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            app.ensure_running()
            try:
                status, body = _http_json("GET", f"{PIPECAT_BASE_URL}/_e2e/health")
            except StackError:
                time.sleep(0.1)
                continue
            if status == 200 and body.get("ok") is True:
                if (
                    body.get("runtime") != VOICE_RUNTIME
                    or body.get("profile_id") != VOICE_PROFILE_ID
                    or body.get("network") != "direct-loopback"
                    or body.get("providers") != "fake"
                ):
                    raise StackError("Pipecat ASGI health identity is inconsistent")
                return body
            time.sleep(0.1)
        raise StackError(f"Pipecat ASGI health timed out\n{app.sanitized_tail()}")

    def _wait_for_web(self, web: _ManagedProcess, app: _ManagedProcess) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            web.ensure_running()
            app.ensure_running()
            try:
                status, body = _http_bytes(f"{WEB_BASE_URL}/e2e/voice")
            except StackError:
                time.sleep(0.1)
                continue
            if status == 200 and body.strip():
                return
            time.sleep(0.1)
        raise StackError(f"Next E2E route timed out\n{web.sanitized_tail()}")

    def _authoritative_terminal_status(self, voice_call_id: str) -> dict[str, object]:
        status, body = _http_json(
            "POST",
            f"{PIPECAT_BASE_URL}/_e2e/pipecat/status",
            {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id},
        )
        if status != 200:
            raise StackError("authoritative Pipecat terminal status was unavailable")
        return body

    def _classify_primary_failure(self, failure: BaseException) -> BaseException:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            return failure
        try:
            raw_playwright_log = (self.paths.run_dir / "playwright.log").read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return failure
        try:
            capsule = _extract_proof_timeout_progress_capsule(raw_playwright_log)
        except StackError as exc:
            return exc
        if capsule is None:
            return failure
        return StackError(f"primary=proof_wait_timeout\n{capsule}")

    def _teardown(self) -> None:
        first_failure: BaseException | None = None
        for process in reversed(self._processes):
            try:
                process.stop()
            except BaseException as exc:
                if first_failure is None:
                    first_failure = exc
        self._processes.clear()
        workspace = self.paths.web_workspace.resolve()
        if workspace.parent == self.paths.run_dir and workspace.name == "web-workspace":
            shutil.rmtree(workspace, ignore_errors=True)
        if first_failure is not None:
            if isinstance(first_failure, (KeyboardInterrupt, SystemExit)):
                raise first_failure
            raise StackError("qualification process teardown failed") from first_failure

    def _sanitize_owned_logs(self) -> None:
        unsafe_labels: set[str] = set()
        for path in self._owned_logs:
            findings = _sanitize_log_file(
                path,
                browser_artifact=path == self.paths.run_dir / "playwright.log",
            )
            unsafe_labels.update(findings - {"raw SmallWebRTC peer ID"})
        for path in self.paths.playwright_dir.rglob("*"):
            if path.is_file() and path.suffix.casefold() in {".json", ".log", ".md", ".txt"}:
                findings = _sanitize_log_file(path, browser_artifact=True)
                unsafe_labels.update(findings - {"raw SmallWebRTC peer ID"})
            elif path.is_file() and path.suffix.casefold() in {
                ".zip",
                ".webm",
                ".png",
                ".jpg",
                ".jpeg",
            }:
                path.unlink(missing_ok=True)
                unsafe_labels.add("trace, video, or screenshot attachment")
        if unsafe_labels:
            labels = ", ".join(sorted(unsafe_labels))
            raise StackError(f"qualification logs contained forbidden signaling data: {labels}")

    def _build_proof(
        self,
        browser: dict[str, object],
        health: dict[str, object],
        terminal: dict[str, object],
        source: dict[str, object],
        artifact_manifest: dict[str, object],
    ) -> dict[str, object]:
        manifest_files = artifact_manifest.get("files")
        if not isinstance(manifest_files, dict):
            raise StackError(_ARTIFACT_MANIFEST_ERROR)
        manifest_copy: dict[str, object] = {
            "algorithm": artifact_manifest.get("algorithm"),
            "files": dict(manifest_files),
        }
        return {
            "schema_version": 1,
            "status": "passed",
            "run_id": self.paths.run_id,
            "runtime": VOICE_RUNTIME,
            "profile_id": VOICE_PROFILE_ID,
            "network": "direct-loopback",
            "providers": "fake",
            "cost": "unmeasured",
            "source": dict(source),
            "topology": {
                "docker_used": False,
                "livekit_process_used": False,
                "livekit_imported_in_pipecat_process": health["livekit_imported"],
                "public_voice_gate": self.web_environment["NEXT_PUBLIC_VOICE_RUNTIME"],
                "smallwebrtc_peer_count": 1,
            },
            "browser": browser,
            "terminal_cleanup": terminal,
            "artifact_safety": {
                "passed": True,
                "trace_video_screenshot_retained": False,
                "text_files_scanned": list(manifest_files),
                "sha256_manifest": manifest_copy,
            },
            "limitations": [
                "Direct loopback candidates only; no Coturn, forced relay, or TLS proof",
                "Deterministic fake STT, LLM, and TTS; no provider quality claim",
                "No geography, scale, paid-provider, or measured cost result",
            ],
        }


def _pipecat_app_command() -> tuple[str, ...]:
    return (
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


def _pipecat_browser_command() -> tuple[str, ...]:
    return (
        "./node_modules/.bin/playwright",
        "test",
        "e2e/voice-pipecat-rtc.spec.ts",
    )


_SERVICE_SECRET_PATTERNS = (
    (
        "raw signaling locator",
        re.compile(r"/api/voice/pipecat/signal/(?!<redacted>)[A-Za-z0-9._~%+-]+", re.I),
    ),
    ("authorization bearer", re.compile(r"Bearer\s+(?!<redacted>)[^\s\"']+", re.I)),
    ("authorization field", re.compile(r'["\']authorization["\']\s*[:=]', re.I)),
    ("raw SDP", re.compile(r"(?:^|[\s\"'])v=0(?:\\r\\n|\r?\n)", re.I)),
    ("raw ICE credential", re.compile(r"ice-(?:ufrag|pwd):", re.I)),
    ("raw ICE candidate", re.compile(r"candidate:", re.I)),
    (
        "raw ICE server URL",
        re.compile(r"\b(?:stun|stuns|turn|turns):[^\s\"'<>]+", re.I),
    ),
    ("raw SmallWebRTC peer ID", re.compile(r"SmallWebRTCConnection#[A-Za-z0-9._:-]+")),
)
_BROWSER_SECRET_PATTERNS = (
    ("signaling locator", re.compile(r"/api/voice/pipecat/signal/", re.I)),
    (
        "network URL",
        re.compile(r"(?:(?:https?|wss?):\/\/|\b(?:stun|stuns|turn|turns):)", re.I),
    ),
    ("authorization value", re.compile(r"Bearer\s+", re.I)),
    ("authorization field", re.compile(r'"authorization"', re.I)),
    ("raw SDP field", re.compile(r'(?:(?:")|(?:\\"))sdp(?:(?:")|(?:\\"))', re.I)),
    (
        "raw ICE field",
        re.compile(
            r'(?:(?:")|(?:\\"))ice_servers(?:(?:")|(?:\\"))|ice-(?:ufrag|pwd):',
            re.I,
        ),
    ),
    ("raw ICE candidate", re.compile(r"candidate:", re.I)),
    (
        "raw peer ID field",
        re.compile(r'(?:(?:")|(?:\\"))pc_id(?:(?:")|(?:\\"))', re.I),
    ),
    ("raw IPv4 address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)
_PROOF_TIMEOUT_PROGRESS_FIELDS = {
    "snapshot": {
        "booleans": {
            "assignment_present",
            "local_track_present",
            "remote_track_present",
            "remote_audio_attached",
        },
        "counters": {"harness_error_count", "connection_gesture_count"},
    },
    "events": {
        "booleans": set(),
        "counters": {
            "total",
            "agent_ready",
            "turn_committed",
            "speech_started",
            "speech_stopped",
            "speech_stopped_interrupted",
            "speech_stopped_completed",
        },
    },
    "clock": {
        "booleans": {
            "worklet_loaded",
            "local_attached",
            "remote_attached",
            "local_correction_pending",
            "remote_correction_pending",
            "local_failed",
            "remote_failed",
        },
        "counters": {
            "local_processed_blocks",
            "remote_processed_blocks",
            "local_active_regions",
            "remote_active_regions",
        },
        "enums": {"bracket_status", "bracket_failure"},
    },
    "pcm": {
        "booleans": {
            "second_local_region_present",
            "remote_silence_present",
            "remote_audio_before_second_local",
        },
        "counters": {
            "local_sample_count",
            "remote_sample_count",
            "local_active_region_count",
        },
    },
    "rtc": {
        "booleans": {
            "outbound_bytes_present",
            "outbound_packets_present",
            "inbound_bytes_present",
            "inbound_packets_present",
        },
        "counters": {"peer_connection_count", "selected_candidate_pair_count"},
    },
    "gates": {
        "booleans": {
            "local_disabled_at_observation",
            "local_live_at_observation",
            "local_precedes_ready",
            "first_event_agent_ready",
            "first_reply_interrupted",
            "second_turn_present",
            "second_reply_started",
            "second_reply_after_silence",
            "second_reply_completed",
            "attribution_observation_complete",
            "stale_audio_detected",
            "proof_ready",
        },
        "counters": set(),
    },
}
_PROOF_TIMEOUT_CLOCK_FAILURES = {
    "none",
    "disposed",
    "cleanup_failed",
    "duplicate_probe",
    "frame_gap",
    "inconsistent_quantum",
    "message_gap",
    "missing_input",
    "probe_overflow",
    "probe_setup_failed",
    "sample_rate_mismatch",
    "too_many_local_active_regions",
    "unexpected_probe_message",
    "clock_not_prepared",
    "interruption_exceeds_limit",
    "local_active_region_count",
    "local_probe_missing",
    "remote_probe_missing",
    "remote_sustained_silence_missing",
    "stale_frame_correction_pending",
}


def _service_secret_findings(value: str) -> set[str]:
    return {label for label, pattern in _SERVICE_SECRET_PATTERNS if pattern.search(value)}


def _browser_secret_findings(value: str) -> set[str]:
    return {label for label, pattern in _BROWSER_SECRET_PATTERNS if pattern.search(value)}


def _validate_proof_timeout_progress_capsule(value: object) -> str:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "kind",
            "snapshot",
            "events",
            "clock",
            "pcm",
            "rtc",
            "gates",
        }
        or isinstance(value.get("schema_version"), bool)
        or not isinstance(value.get("schema_version"), int)
        or value.get("schema_version") != 1
        or value.get("kind") != "pipecat_proof_wait_timeout"
    ):
        raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)

    for section_name, field_groups in _PROOF_TIMEOUT_PROGRESS_FIELDS.items():
        section = value.get(section_name)
        boolean_fields = field_groups["booleans"]
        counter_fields = field_groups["counters"]
        enum_fields = field_groups.get("enums", set())
        if (
            not isinstance(section, dict)
            or set(section) != boolean_fields | counter_fields | enum_fields
            or any(not isinstance(section.get(field), bool) for field in boolean_fields)
            or any(
                isinstance(section.get(field), bool)
                or not isinstance(section.get(field), int)
                or not 0 <= section[field] <= _PROOF_TIMEOUT_PROGRESS_MAX_COUNTER
                for field in counter_fields
            )
        ):
            raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)

    clock = value["clock"]
    if not isinstance(clock, dict):
        raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)
    bracket_status = clock.get("bracket_status")
    bracket_failure = clock.get("bracket_failure")
    if (
        not isinstance(bracket_status, str)
        or bracket_status not in {"pending", "passed", "failed"}
        or not isinstance(bracket_failure, str)
        or bracket_failure not in _PROOF_TIMEOUT_CLOCK_FAILURES
    ):
        raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)

    rendered = _PROOF_TIMEOUT_PROGRESS_PREFIX + json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )
    if (
        len(rendered.encode("utf-8")) >= _PROOF_TIMEOUT_PROGRESS_MAX_BYTES
        or _browser_secret_findings(rendered)
        or _service_secret_findings(rendered)
    ):
        raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)
    return rendered


def _extract_proof_timeout_progress_capsule(value: str) -> str | None:
    offsets: list[int] = []
    offset = value.find(_PROOF_TIMEOUT_PROGRESS_PREFIX)
    while offset >= 0:
        offsets.append(offset + len(_PROOF_TIMEOUT_PROGRESS_PREFIX))
        offset = value.find(_PROOF_TIMEOUT_PROGRESS_PREFIX, offsets[-1])
    if not offsets:
        return None

    decoder = json.JSONDecoder()
    rendered_capsules: set[str] = set()
    for offset in offsets:
        try:
            candidate = value[offset : offset + _PROOF_TIMEOUT_PROGRESS_MAX_BYTES]
            capsule, _ = decoder.raw_decode(candidate)
            rendered_capsules.add(_validate_proof_timeout_progress_capsule(capsule))
        except (json.JSONDecodeError, RecursionError, StackError, ValueError):
            raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR) from None
    if len(rendered_capsules) != 1:
        raise StackError(_PROOF_TIMEOUT_PROGRESS_ERROR)
    return rendered_capsules.pop()


def _sanitize_sensitive_text(value: str) -> str:
    sanitized = re.sub(
        r"SmallWebRTCConnection#[A-Za-z0-9._:-]+",
        "smallwebrtc-<redacted>",
        value,
    )
    sanitized = re.sub(
        r"/api/voice/pipecat/signal/(?!<redacted>)[A-Za-z0-9._~%+-]+",
        "/api/voice/pipecat/signal/<redacted>",
        sanitized,
        flags=re.I,
    )
    sanitized = re.sub(
        r"Bearer\s+(?!<redacted>)[^\s\"']+",
        "Bearer <redacted>",
        sanitized,
        flags=re.I,
    )
    unsafe_line = re.compile(
        r"candidate:|ice-(?:ufrag|pwd):|\b(?:stun|stuns|turn|turns):|"
        r"(?:^|[\s\"'])v=0(?:\\r\\n|\r?\n)",
        re.I,
    )
    return "\n".join(
        "[redacted sensitive signaling line]" if unsafe_line.search(line) else line
        for line in sanitized.splitlines()
    )


def _sanitize_log_file(path: Path, *, browser_artifact: bool = False) -> set[str]:
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    findings = _service_secret_findings(value)
    if browser_artifact:
        findings.update(_browser_secret_findings(value))
    fatal_findings = findings - {"raw SmallWebRTC peer ID"}
    sanitized = (
        "[qualification artifact redacted after forbidden signaling data]\n"
        if fatal_findings
        else _sanitize_sensitive_text(value)
    )
    if sanitized != value:
        temporary = path.with_suffix(path.suffix + ".sanitizing")
        temporary.write_text(sanitized, encoding="utf-8")
        os.replace(temporary, path)
    return findings


def _assert_browser_artifact_safe(value: str, label: str) -> None:
    findings = sorted(_browser_secret_findings(value))
    if findings:
        raise StackError(f"{label} retained forbidden fields: {', '.join(findings)}")


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StackError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise StackError(f"{label} must be a JSON object")
    return value


def _required_contract_id(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not _CONTRACT_ID_PATTERN.fullmatch(item):
        raise StackError(f"browser result contains an invalid {field}")
    return item


def _aligned_audio_clock_frames(milliseconds: int, sample_rate_hz: int) -> int:
    denominator = 1_000 * _AUDIO_CLOCK_QUANTUM_FRAMES
    blocks = (milliseconds * sample_rate_hz + denominator - 1) // denominator
    return max(_AUDIO_CLOCK_QUANTUM_FRAMES, blocks * _AUDIO_CLOCK_QUANTUM_FRAMES)


def _validate_audio_clock_probe(
    value: object,
    *,
    label: str,
    exact_track_id: str,
    sample_rate_hz: int,
    threshold_rms: float,
    silence_hold_ms: int,
) -> tuple[list[dict[str, object]], int]:
    expected_keys = {
        "attached",
        "exact_track_id",
        "threshold_rms",
        "silence_hold_frames",
        "processed_block_count",
        "latest_block_end_frame",
        "current_state",
        "current_state_block_count",
        "active_region_count",
        "transitions",
        "overflow",
        "failure_code",
        "failure_message_sequence",
        "expected_block_start_frame",
        "observed_block_start_frame",
        "frame_delta_frames",
        "last_observed_block_start_frame",
        "context_state_at_message_delivery",
        "stale_frame_correction_count",
        "last_stale_observed_block_start_frame",
        "last_stale_logical_block_start_frame",
        "stale_frame_catch_up_observed_block_start_frame",
        "stale_frame_correction_pending",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise StackError(f"audio sample-clock {label} probe schema is invalid")
    if (
        value.get("attached") is not True
        or value.get("exact_track_id") != exact_track_id
        or value.get("overflow") is not False
        or value.get("failure_code") is not None
        or value.get("failure_message_sequence") is not None
        or value.get("expected_block_start_frame") is not None
        or value.get("observed_block_start_frame") is not None
        or value.get("frame_delta_frames") is not None
        or value.get("last_observed_block_start_frame") is not None
        or value.get("context_state_at_message_delivery") is not None
    ):
        raise StackError(f"audio sample-clock {label} probe is not clean and exact")
    correction_count = value.get("stale_frame_correction_count")
    stale_observed_frame = value.get("last_stale_observed_block_start_frame")
    stale_logical_frame = value.get("last_stale_logical_block_start_frame")
    stale_catch_up_frame = value.get("stale_frame_catch_up_observed_block_start_frame")
    if (
        isinstance(correction_count, bool)
        or not isinstance(correction_count, int)
        or correction_count not in {0, 1}
        or value.get("stale_frame_correction_pending") is not False
        or (
            correction_count == 0
            and (
                stale_observed_frame is not None
                or stale_logical_frame is not None
                or stale_catch_up_frame is not None
            )
        )
        or (
            correction_count == 1
            and (
                isinstance(stale_observed_frame, bool)
                or not isinstance(stale_observed_frame, int)
                or stale_observed_frame < 0
                or stale_observed_frame % _AUDIO_CLOCK_QUANTUM_FRAMES != 0
                or isinstance(stale_logical_frame, bool)
                or not isinstance(stale_logical_frame, int)
                or stale_logical_frame <= stale_observed_frame
                or (stale_logical_frame - stale_observed_frame) % _AUDIO_CLOCK_QUANTUM_FRAMES != 0
                or isinstance(stale_catch_up_frame, bool)
                or not isinstance(stale_catch_up_frame, int)
                or stale_catch_up_frame % _AUDIO_CLOCK_QUANTUM_FRAMES != 0
                or stale_catch_up_frame != stale_logical_frame + _AUDIO_CLOCK_QUANTUM_FRAMES
            )
        )
    ):
        raise StackError(f"audio sample-clock {label} stale-frame correction is invalid")
    observed_threshold = value.get("threshold_rms")
    if (
        isinstance(observed_threshold, bool)
        or not isinstance(observed_threshold, int | float)
        or not math.isfinite(observed_threshold)
        or observed_threshold != threshold_rms
    ):
        raise StackError(f"audio sample-clock {label} threshold is invalid")
    expected_hold_frames = _aligned_audio_clock_frames(
        silence_hold_ms,
        sample_rate_hz,
    )
    if value.get("silence_hold_frames") != expected_hold_frames:
        raise StackError(f"audio sample-clock {label} silence hold is invalid")

    processed_blocks = value.get("processed_block_count")
    latest_block_end = value.get("latest_block_end_frame")
    current_state_blocks = value.get("current_state_block_count")
    active_regions = value.get("active_region_count")
    if (
        isinstance(processed_blocks, bool)
        or not isinstance(processed_blocks, int)
        or processed_blocks <= 0
        or isinstance(latest_block_end, bool)
        or not isinstance(latest_block_end, int)
        or latest_block_end <= 0
        or latest_block_end % _AUDIO_CLOCK_QUANTUM_FRAMES != 0
        or value.get("current_state") not in {"active", "silent"}
        or isinstance(current_state_blocks, bool)
        or not isinstance(current_state_blocks, int)
        or current_state_blocks <= 0
        or current_state_blocks > processed_blocks
        or isinstance(active_regions, bool)
        or not isinstance(active_regions, int)
        or active_regions < 0
    ):
        raise StackError(f"audio sample-clock {label} counters are invalid")

    raw_transitions = value.get("transitions")
    if (
        not isinstance(raw_transitions, list)
        or not raw_transitions
        or len(raw_transitions) > _AUDIO_CLOCK_MAX_TRANSITIONS
    ):
        raise StackError(f"audio sample-clock {label} transitions are not bounded")
    transitions: list[dict[str, object]] = []
    previous_end = -1
    previous_state: object = None
    for transition in raw_transitions:
        if not isinstance(transition, dict) or set(transition) != {
            "state",
            "block_start_frame",
            "block_end_frame",
        }:
            raise StackError(f"audio sample-clock {label} transition schema is invalid")
        state = transition.get("state")
        block_start = transition.get("block_start_frame")
        block_end = transition.get("block_end_frame")
        if (
            state not in {"active", "silent"}
            or state == previous_state
            or isinstance(block_start, bool)
            or not isinstance(block_start, int)
            or block_start < 0
            or block_start % _AUDIO_CLOCK_QUANTUM_FRAMES != 0
            or isinstance(block_end, bool)
            or not isinstance(block_end, int)
            or block_end - block_start != _AUDIO_CLOCK_QUANTUM_FRAMES
            or block_start < previous_end
            or block_end > latest_block_end
        ):
            raise StackError(f"audio sample-clock {label} transition is invalid")
        transitions.append(transition)
        previous_end = block_end
        previous_state = state
    if value.get("current_state") != transitions[-1]["state"] or active_regions != sum(
        transition["state"] == "active" for transition in transitions
    ):
        raise StackError(f"audio sample-clock {label} state history is inconsistent")
    first_transition_start = transitions[0]["block_start_frame"]
    current_transition_start = transitions[-1]["block_start_frame"]
    if (
        not isinstance(first_transition_start, int)
        or latest_block_end - first_transition_start
        != processed_blocks * _AUDIO_CLOCK_QUANTUM_FRAMES
        or not isinstance(current_transition_start, int)
        or latest_block_end - current_transition_start
        != current_state_blocks * _AUDIO_CLOCK_QUANTUM_FRAMES
    ):
        raise StackError(f"audio sample-clock {label} timeline counters are inconsistent")
    if correction_count == 1 and (
        not isinstance(stale_observed_frame, int)
        or stale_observed_frame < first_transition_start
        or not isinstance(stale_catch_up_frame, int)
        or stale_catch_up_frame + _AUDIO_CLOCK_QUANTUM_FRAMES > latest_block_end
    ):
        raise StackError(
            f"audio sample-clock {label} stale-frame correction is outside its timeline"
        )
    for index, transition in enumerate(transitions):
        if transition["state"] != "silent":
            continue
        next_start = (
            transitions[index + 1]["block_start_frame"]
            if index + 1 < len(transitions)
            else latest_block_end
        )
        block_start = transition["block_start_frame"]
        if (
            not isinstance(next_start, int)
            or not isinstance(block_start, int)
            or next_start - block_start < expected_hold_frames
        ):
            raise StackError(f"audio sample-clock {label} silence was not sustained")
    return transitions, latest_block_end


def _validate_audio_sample_clock(
    browser: Mapping[str, object],
    exact_local_track_id: str,
    exact_remote_track_id: str,
) -> None:
    clock = browser.get("audio_sample_clock")
    if not isinstance(clock, dict) or set(clock) != {
        "evidence",
        "interruption_bracket",
    }:
        raise StackError("browser result omitted exact audio sample-clock evidence")
    evidence = clock.get("evidence")
    expected_evidence_keys = {
        "schema_version",
        "worklet_loaded",
        "sample_rate_hz",
        "quantum_frames",
        "local",
        "remote",
        "disposed",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_evidence_keys:
        raise StackError("audio sample-clock evidence schema is invalid")
    sample_rate_hz = evidence.get("sample_rate_hz")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("worklet_loaded") is not True
        or evidence.get("quantum_frames") != _AUDIO_CLOCK_QUANTUM_FRAMES
        or evidence.get("disposed") is not False
        or isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or sample_rate_hz <= 0
    ):
        raise StackError("audio sample-clock evidence identity is invalid")

    local_transitions, _ = _validate_audio_clock_probe(
        evidence.get("local"),
        label="local",
        exact_track_id=exact_local_track_id,
        sample_rate_hz=sample_rate_hz,
        threshold_rms=_AUDIO_CLOCK_LOCAL_ACTIVE_RMS,
        silence_hold_ms=_AUDIO_CLOCK_LOCAL_REGION_BRIDGE_MS,
    )
    remote_transitions, remote_latest_block_end = _validate_audio_clock_probe(
        evidence.get("remote"),
        label="remote",
        exact_track_id=exact_remote_track_id,
        sample_rate_hz=sample_rate_hz,
        threshold_rms=_AUDIO_CLOCK_REMOTE_SILENCE_RMS,
        silence_hold_ms=_AUDIO_CLOCK_REQUIRED_SILENCE_MS,
    )
    local_active_transitions = [
        transition for transition in local_transitions if transition["state"] == "active"
    ]
    if len(local_active_transitions) != 2:
        raise StackError("audio sample-clock did not prove exactly two local regions")
    second_local_start = local_active_transitions[1]["block_start_frame"]
    if not isinstance(second_local_start, int):
        raise StackError("audio sample-clock second local region is invalid")

    required_silence_frames = _aligned_audio_clock_frames(
        _AUDIO_CLOCK_REQUIRED_SILENCE_MS,
        sample_rate_hz,
    )
    qualifying_remote_silence: dict[str, object] | None = None
    for index, transition in enumerate(remote_transitions):
        block_start = transition["block_start_frame"]
        block_end = transition["block_end_frame"]
        if not isinstance(block_start, int) or not isinstance(block_end, int):
            raise StackError("audio sample-clock remote transition is invalid")
        next_start: object = (
            remote_transitions[index + 1]["block_start_frame"]
            if index + 1 < len(remote_transitions)
            else remote_latest_block_end
        )
        if not isinstance(next_start, int):
            raise StackError("audio sample-clock remote run is invalid")
        if (
            transition["state"] == "silent"
            and block_end >= second_local_start
            and next_start - block_start >= required_silence_frames
        ):
            qualifying_remote_silence = transition
            break
    if qualifying_remote_silence is None:
        raise StackError("audio sample-clock omitted sustained remote silence")
    remote_silence_end = qualifying_remote_silence["block_end_frame"]
    if not isinstance(remote_silence_end, int):
        raise StackError("audio sample-clock remote silence boundary is invalid")

    interruption_frames = remote_silence_end - second_local_start
    if interruption_frames < 0:
        raise StackError("audio sample-clock interruption frame bound is negative")
    interruption_ms = (
        (interruption_frames * 1_000_000 + sample_rate_hz - 1) // sample_rate_hz
    ) / 1_000
    bracket = clock.get("interruption_bracket")
    expected_bracket_keys = {
        "status",
        "failure_code",
        "sample_rate_hz",
        "quantum_frames",
        "required_silence_frames",
        "second_local_active_block_start_frame",
        "remote_silence_transition_block_end_frame",
        "interruption_upper_bound_frames",
        "interruption_upper_bound_ms",
    }
    if not isinstance(bracket, dict) or set(bracket) != expected_bracket_keys:
        raise StackError("audio sample-clock interruption bracket schema is invalid")
    observed_ms = bracket.get("interruption_upper_bound_ms")
    if (
        bracket.get("status") != "passed"
        or bracket.get("failure_code") is not None
        or bracket.get("sample_rate_hz") != sample_rate_hz
        or bracket.get("quantum_frames") != _AUDIO_CLOCK_QUANTUM_FRAMES
        or bracket.get("required_silence_frames") != required_silence_frames
        or bracket.get("second_local_active_block_start_frame") != second_local_start
        or bracket.get("remote_silence_transition_block_end_frame") != remote_silence_end
        or bracket.get("interruption_upper_bound_frames") != interruption_frames
        or isinstance(observed_ms, bool)
        or not isinstance(observed_ms, int | float)
        or not math.isfinite(observed_ms)
        or observed_ms != interruption_ms
    ):
        raise StackError("audio sample-clock conservative bracket is inconsistent")
    if interruption_ms > _AUDIO_CLOCK_MAX_INTERRUPTION_MS:
        raise StackError("audio sample-clock exceeded the hard 250 ms interruption limit")


def _read_pipecat_browser_result(path: Path) -> dict[str, object]:
    try:
        serialized = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StackError("browser did not write its sanitized Pipecat result") from exc
    _assert_browser_artifact_safe(serialized, "browser result")
    value = _read_json_object(path, "browser result")
    if (
        value.get("schema_version") != 1
        or value.get("status") != "passed"
        or value.get("runtime") != VOICE_RUNTIME
        or value.get("profile_id") != VOICE_PROFILE_ID
        or value.get("browser_cleanup_observed") is not True
    ):
        raise StackError("browser result identity or cleanup claim is invalid")
    for field in ("peer_reservation_id", "voice_call_id", "trace_id"):
        _required_contract_id(value, field)

    browser = value.get("browser_evidence")
    if not isinstance(browser, dict):
        raise StackError("browser result omitted RTC evidence")
    exact_local_track_id = _required_contract_id(browser, "exact_local_track_id")
    exact_remote_track_id = _required_contract_id(browser, "exact_remote_track_id")
    if browser.get("connection_gestures") != [
        {"sequence": 1, "action": "prepare"},
        {"sequence": 2, "action": "activate"},
    ]:
        raise StackError("browser result did not retain both required user gestures")
    if browser.get("peer_connection_count") != 1:
        raise StackError("browser result did not prove exactly one peer connection")
    for field in (
        "outbound_bytes_sent",
        "outbound_packets_sent",
        "inbound_bytes_received",
        "inbound_packets_received",
    ):
        item = browser.get(field)
        if isinstance(item, bool) or not isinstance(item, int | float) or item <= 0:
            raise StackError(f"browser result contains an invalid {field}")
    selected = browser.get("selected_candidate_pair")
    if not isinstance(selected, dict) or set(selected) != {
        "state",
        "nominated",
        "bytes_sent",
        "bytes_received",
        "current_round_trip_time_seconds",
        "local",
        "remote",
    }:
        raise StackError("browser result omitted its exact selected candidate pair")
    bytes_sent = selected.get("bytes_sent")
    bytes_received = selected.get("bytes_received")
    round_trip_time = selected.get("current_round_trip_time_seconds")
    if (
        selected.get("state") != "succeeded"
        or selected.get("nominated") is not True
        or isinstance(bytes_sent, bool)
        or not isinstance(bytes_sent, int | float)
        or bytes_sent <= 0
        or isinstance(bytes_received, bool)
        or not isinstance(bytes_received, int | float)
        or bytes_received <= 0
        or (
            round_trip_time is not None
            and (
                isinstance(round_trip_time, bool)
                or not isinstance(round_trip_time, int | float)
                or not math.isfinite(round_trip_time)
                or round_trip_time < 0
            )
        )
    ):
        raise StackError("selected candidate pair did not carry bidirectional RTP")
    for side in ("local", "remote"):
        candidate = selected.get(side)
        if candidate != {
            "candidate_type": "host",
            "protocol": "udp",
            "relay_protocol": None,
        }:
            raise StackError("selected candidate evidence retained invalid fields")

    request_counts = browser.get("signaling_request_counts")
    if not isinstance(request_counts, dict):
        raise StackError("browser result omitted signaling request counts")
    patches = request_counts.get("patch")
    if (
        request_counts.get("post") != 1
        or request_counts.get("authenticated_post") != 1
        or request_counts.get("delete") != 1
        or request_counts.get("authenticated_delete") != 1
        or request_counts.get("with_cookies") != 0
        or isinstance(patches, bool)
        or not isinstance(patches, int)
        or patches <= 0
        or request_counts.get("authenticated_patch") != patches
    ):
        raise StackError("browser result signaling counts are not exact")
    _validate_audio_sample_clock(
        browser,
        exact_local_track_id,
        exact_remote_track_id,
    )
    terminal = value.get("terminal_cleanup")
    if not isinstance(terminal, dict):
        raise StackError("browser result omitted terminal cleanup")
    _validate_pipecat_terminal_status(terminal, str(value["voice_call_id"]))
    return value


def _validate_pipecat_terminal_status(
    value: Mapping[str, object],
    voice_call_id: str,
) -> None:
    if (
        value.get("schema_version") != 1
        or value.get("status") != "passed"
        or value.get("runtime") != VOICE_RUNTIME
        or value.get("profile_id") != VOICE_PROFILE_ID
        or value.get("session_id") != E2E_SESSION_ID
        or value.get("voice_call_id") != voice_call_id
    ):
        raise StackError("terminal cleanup identity is inconsistent")
    reservation = value.get("reservation")
    if not isinstance(reservation, dict):
        raise StackError("terminal cleanup omitted its reservation")
    terminal_pair = (
        reservation.get("terminal_reason"),
        reservation.get("retryable"),
    )
    if (
        reservation.get("state") != "terminal"
        or reservation.get("cleanup_complete") is not True
        or terminal_pair not in {("user_ended", False), ("client_disconnected", True)}
    ):
        raise StackError("terminal reservation is not an allowed exact outcome")
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
    if value.get("control_plane") != expected_control:
        raise StackError("terminal cleanup retained control-plane work")
    media = value.get("fake_media")
    if not isinstance(media, dict):
        raise StackError("terminal cleanup omitted fake-media evidence")
    if (
        media.get("final_transcripts") != ["Hello tutor.", "Actually, stop."]
        or media.get("llm_response_count") != 2
        or media.get("tts_cancelled_count") != 1
        or media.get("cleaned_processors") != ["llm", "stt", "tts"]
        or media.get("processor_cleanup_counts") != {"stt": 1, "llm": 1, "tts": 1}
        or media.get("profile_close_count") != 1
        or media.get("media_contract_satisfied") is not True
    ):
        raise StackError("terminal fake-media contract is incomplete")
    for field in ("input_frame_count", "tts_frame_count"):
        item = media.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise StackError(f"terminal fake-media {field} is invalid")


def _validate_playwright_report(path: Path) -> None:
    report = _read_json_object(path, "Playwright JSON report")
    if report.get("errors") != []:
        raise StackError("Playwright report retained top-level errors")
    stats = report.get("stats")
    if not isinstance(stats, dict) or {
        "expected": stats.get("expected"),
        "unexpected": stats.get("unexpected"),
        "flaky": stats.get("flaky"),
    } != {"expected": 1, "unexpected": 0, "flaky": 0}:
        raise StackError("Playwright report did not contain one exact passing test")
    suites = report.get("suites")
    if not isinstance(suites, list) or len(suites) != 1:
        raise StackError("Playwright report suite count is invalid")
    suite = suites[0]
    if not isinstance(suite, dict) or suite.get("title") != "voice-pipecat-rtc.spec.ts":
        raise StackError("Playwright ran a different RTC specification")
    specs = suite.get("specs")
    if not isinstance(specs, list) or len(specs) != 1 or not isinstance(specs[0], dict):
        raise StackError("Playwright report spec count is invalid")
    spec = specs[0]
    tests = spec.get("tests")
    if spec.get("ok") is not True or not isinstance(tests, list) or len(tests) != 1:
        raise StackError("Pipecat browser specification did not pass exactly once")
    test = tests[0]
    if not isinstance(test, dict):
        raise StackError("Playwright report test record is invalid")
    results = test.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise StackError("Playwright report result count is invalid")
    result = results[0]
    if (
        result.get("status") != "passed"
        or result.get("errors") != []
        or result.get("attachments") != []
    ):
        raise StackError("Playwright retained a failure or sensitive attachment")


def _qualification_text_artifact_paths(paths: StackPaths) -> tuple[Path, ...]:
    return (
        paths.server_log,
        paths.run_dir / "web-build.log",
        paths.run_dir / "web.log",
        paths.run_dir / "playwright.log",
        paths.playwright_report,
        paths.browser_result,
        paths.evidence,
    )


def _repo_relative_artifact_path(path: Path, repository_root: Path) -> str:
    try:
        resolved_root = repository_root.resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            raise OSError
        lexical_path = Path(os.path.abspath(path))
        resolved_path = path.resolve(strict=True)
        if lexical_path != resolved_path:
            raise OSError
        relative = resolved_path.relative_to(resolved_root).as_posix()
        cursor = resolved_root
        for component in Path(relative).parts:
            cursor /= component
            if cursor.is_symlink():
                raise OSError
    except (OSError, ValueError):
        raise StackError(_ARTIFACT_MANIFEST_ERROR) from None
    if not relative or relative.startswith("../") or relative.startswith("/"):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    return relative


def _sha256_artifact(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
        with os.fdopen(file_descriptor, "rb") as handle:
            initial_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(initial_stat.st_mode):
                raise OSError
            while chunk := handle.read(128 * 1024):
                digest.update(chunk)
            final_stat = os.fstat(handle.fileno())
        current_stat = path.lstat()
    except OSError:
        raise StackError(_ARTIFACT_MANIFEST_ERROR) from None
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(
        getattr(initial_stat, field) != getattr(final_stat, field)
        or getattr(final_stat, field) != getattr(current_stat, field)
        for field in identity_fields
    ):
        raise StackError(_ARTIFACT_TAMPER_ERROR)
    return digest.hexdigest()


def _build_artifact_sha256_manifest(
    paths: StackPaths,
    *,
    repository_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    entries: list[tuple[str, Path]] = []
    for path in _qualification_text_artifact_paths(paths):
        label = _repo_relative_artifact_path(path, repository_root)
        entries.append((label, path))
    entries.sort(key=lambda item: item[0])
    labels = [label for label, _path in entries]
    if len(labels) != 7 or len(set(labels)) != len(labels):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    files: dict[str, str] = {}
    for label, path in entries:
        files[label] = _sha256_artifact(path)
        if _repo_relative_artifact_path(path, repository_root) != label:
            raise StackError(_ARTIFACT_TAMPER_ERROR)
    return {"algorithm": "sha256", "files": files}


def _validate_artifact_sha256_manifest(
    paths: StackPaths,
    manifest: object,
    *,
    repository_root: Path = PROJECT_ROOT,
) -> None:
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != {"algorithm", "files"}
        or manifest.get("algorithm") != "sha256"
    ):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    files = manifest.get("files")
    if (
        not isinstance(files, dict)
        or len(files) != 7
        or list(files) != sorted(files)
        or any(
            not isinstance(label, str)
            or not isinstance(digest, str)
            or not _SHA256_PATTERN.fullmatch(digest)
            for label, digest in files.items()
        )
    ):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    observed = _build_artifact_sha256_manifest(paths, repository_root=repository_root)
    if observed != manifest:
        raise StackError(_ARTIFACT_TAMPER_ERROR)


def _validate_rtc_stack_proof(
    paths: StackPaths,
    proof: object,
    source: object,
    manifest: object,
    *,
    repository_root: Path = PROJECT_ROOT,
) -> None:
    expected_keys = {
        "schema_version",
        "status",
        "run_id",
        "runtime",
        "profile_id",
        "network",
        "providers",
        "cost",
        "source",
        "topology",
        "browser",
        "terminal_cleanup",
        "artifact_safety",
        "limitations",
    }
    proof_schema_version = proof.get("schema_version") if isinstance(proof, Mapping) else None
    if (
        not isinstance(proof, Mapping)
        or set(proof) != expected_keys
        or (
            isinstance(proof_schema_version, bool)
            or not isinstance(proof_schema_version, int)
            or proof_schema_version != 1
            or proof.get("status") != "passed"
            or proof.get("run_id") != paths.run_id
            or proof.get("runtime") != VOICE_RUNTIME
            or proof.get("profile_id") != VOICE_PROFILE_ID
            or proof.get("network") != "direct-loopback"
            or proof.get("providers") != "fake"
            or proof.get("cost") != "unmeasured"
        )
    ):
        raise StackError("RTC stack proof identity is invalid")
    expected_source = _validate_source_provenance(source)
    proof_source = proof.get("source")
    if (
        not isinstance(proof_source, dict)
        or _validate_source_provenance(proof_source) != expected_source
    ):
        raise StackError(_SOURCE_PROVENANCE_ERROR)
    topology = proof.get("topology")
    if (
        not isinstance(topology, dict)
        or set(topology)
        != {
            "docker_used",
            "livekit_process_used",
            "livekit_imported_in_pipecat_process",
            "public_voice_gate",
            "smallwebrtc_peer_count",
        }
        or topology.get("docker_used") is not False
        or topology.get("livekit_process_used") is not False
        or topology.get("livekit_imported_in_pipecat_process") is not False
        or topology.get("public_voice_gate") != "voice_v2"
        or isinstance(topology.get("smallwebrtc_peer_count"), bool)
        or topology.get("smallwebrtc_peer_count") != 1
    ):
        raise StackError("RTC stack proof topology is invalid")
    browser = proof.get("browser")
    if not isinstance(browser, dict) or browser != _read_pipecat_browser_result(
        paths.browser_result
    ):
        raise StackError("RTC stack proof browser evidence is inconsistent")
    voice_call_id = browser.get("voice_call_id")
    terminal = proof.get("terminal_cleanup")
    if (
        not isinstance(voice_call_id, str)
        or not isinstance(terminal, dict)
        or terminal != browser.get("terminal_cleanup")
    ):
        raise StackError("RTC stack proof terminal cleanup is inconsistent")
    _validate_pipecat_terminal_status(terminal, voice_call_id)
    expected_limitations = [
        "Direct loopback candidates only; no Coturn, forced relay, or TLS proof",
        "Deterministic fake STT, LLM, and TTS; no provider quality claim",
        "No geography, scale, paid-provider, or measured cost result",
    ]
    if proof.get("limitations") != expected_limitations:
        raise StackError("RTC stack proof limitations are invalid")
    safety = proof.get("artifact_safety")
    if not isinstance(safety, dict) or set(safety) != {
        "passed",
        "trace_video_screenshot_retained",
        "text_files_scanned",
        "sha256_manifest",
    }:
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    if not isinstance(manifest, Mapping):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    if (
        safety.get("passed") is not True
        or safety.get("trace_video_screenshot_retained") is not False
        or safety.get("text_files_scanned") != list(files)
        or safety.get("sha256_manifest") != manifest
    ):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    proof_manifest = safety.get("sha256_manifest")
    if not isinstance(proof_manifest, dict):
        raise StackError(_ARTIFACT_MANIFEST_ERROR)
    _validate_artifact_sha256_manifest(
        paths,
        proof_manifest,
        repository_root=repository_root,
    )


def _write_validated_rtc_stack_proof(
    paths: StackPaths,
    proof: dict[str, object],
    source: object,
    manifest: object,
    *,
    repository_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    _atomic_write_json(paths.rtc_proof, proof)
    try:
        _validate_artifact_sha256_manifest(
            paths,
            manifest,
            repository_root=repository_root,
        )
        expected_serialized = json.dumps(proof, indent=2, sort_keys=True) + "\n"
        try:
            persisted_serialized = paths.rtc_proof.read_text(encoding="utf-8")
        except OSError:
            raise StackError("persisted RTC stack proof is unavailable") from None
        if persisted_serialized != expected_serialized:
            raise StackError("persisted RTC stack proof is not canonical")
        persisted_proof = _read_json_object(paths.rtc_proof, "RTC stack proof")
        _validate_rtc_stack_proof(
            paths,
            persisted_proof,
            source,
            manifest,
            repository_root=repository_root,
        )
        _scan_qualification_artifacts(paths, include_proof=True)
        _validate_artifact_sha256_manifest(
            paths,
            manifest,
            repository_root=repository_root,
        )
    except BaseException:
        paths.rtc_proof.unlink(missing_ok=True)
        raise
    return persisted_proof


def _scan_qualification_artifacts(paths: StackPaths, *, include_proof: bool = False) -> None:
    forbidden_files = [
        path
        for path in paths.run_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".zip", ".webm", ".png", ".jpg", ".jpeg"}
    ]
    if forbidden_files:
        raise StackError("qualification retained a trace, video, or screenshot")
    for path in _qualification_text_artifact_paths(paths):
        try:
            value = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise StackError(f"qualification artifact is missing: {path.name}") from exc
        if _service_secret_findings(value):
            raise StackError(f"qualification artifact retained signaling data: {path.name}")
        if path == paths.browser_result:
            _assert_browser_artifact_safe(value, "browser result")
    if include_proof:
        _assert_browser_artifact_safe(
            paths.rtc_proof.read_text(encoding="utf-8"),
            "stack proof",
        )


def _http_bytes(url: str) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "murmur-pipecat-e2e/1"})
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except (TimeoutError, URLError, OSError) as exc:
        raise StackError("loopback web HTTP request failed") from exc


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


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"rtc-{timestamp}-{uuid.uuid4().hex[:8]}"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default=None,
        help="fresh lowercase artifact identifier",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--browser-timeout-seconds",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--web-build-timeout-seconds",
        type=float,
        default=240.0,
    )
    parser.add_argument(
        "--network",
        choices=tuple(mode.value for mode in PipecatE2ENetworkMode),
        default=PipecatE2ENetworkMode.DIRECT.value,
        help="deterministic network contract (relay-tls is contract-only in Checkpoint A)",
    )
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="run only guarded bootstrap/release without a media claim",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    network = parse_network_mode(args.network)
    if network is PipecatE2ENetworkMode.RELAY_TLS:
        print(
            f"Pipecat RTC qualification failed: {_RELAY_TLS_CONTRACT_ONLY_ERROR}",
            file=sys.stderr,
        )
        return 1
    run_id = args.run_id or _new_run_id()
    try:
        if args.backend_only:
            proof = PipecatBackendCheckpoint(
                make_paths(run_id),
                startup_timeout_seconds=args.startup_timeout_seconds,
            ).run()
        else:
            proof = PipecatBrowserStack(
                make_paths(run_id),
                startup_timeout_seconds=args.startup_timeout_seconds,
                browser_timeout_seconds=args.browser_timeout_seconds,
                web_build_timeout_seconds=args.web_build_timeout_seconds,
            ).run()
    except KeyboardInterrupt:
        print("Pipecat RTC qualification interrupted", file=sys.stderr)
        return 130
    except StackError as exc:
        print(
            f"Pipecat RTC qualification failed: {_sanitize_sensitive_text(str(exc))}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ASSISTANT_FIXTURE",
    "BROWSER_FIXTURE",
    "PIPECAT_BASE_URL",
    "PIPECAT_PORT",
    "PipecatBackendCheckpoint",
    "PipecatBrowserStack",
    "PipecatE2ENetworkMode",
    "StackError",
    "StackPaths",
    "build_environment",
    "build_web_environment",
    "make_paths",
]

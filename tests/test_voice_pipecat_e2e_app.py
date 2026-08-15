"""Process-isolated guard and HTTP tests for the dedicated Pipecat E2E app."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_stack import (  # noqa: E402
    StackError,
    StackPaths,
    _require_port_available,
    build_environment,
)


def _paths(label: str) -> StackPaths:
    suffix = uuid.uuid4().hex
    run_dir = PROJECT_ROOT / "var" / "voice-pipecat-e2e" / f"pytest-{label}-{suffix}"
    return StackPaths(
        run_id=f"pytest-{label}-{suffix}",
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=PROJECT_ROOT / "var" / "evals" / f"voice-pipecat-pytest-{suffix}.jsonl",
        server_log=run_dir / "pipecat-asgi.log",
        proof=run_dir / "backend-checkpoint.json",
    )


def _run_isolated(code: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _cleanup(paths: StackPaths) -> None:
    shutil.rmtree(paths.run_dir, ignore_errors=True)
    paths.evidence.unlink(missing_ok=True)


def test_port_preflight_allows_reuse_after_close_but_rejects_live_listener() -> None:
    host = "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, 0))
        port = listener.getsockname()[1]
        listener.listen()

        with pytest.raises(StackError, match="already in use"):
            _require_port_available(host, port)

    _require_port_available(host, port)


def test_runner_strips_generic_credentials_and_forces_dotenv_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths("runner-env")
    monkeypatch.setenv("UNLISTED_VENDOR_API_KEY", "must-not-enter-child")
    monkeypatch.setenv("SECOND_VENDOR_AUTH_TOKEN", "must-not-enter-child")

    environment = build_environment(paths)

    assert "UNLISTED_VENDOR_API_KEY" not in environment
    assert "SECOND_VENDOR_AUTH_TOKEN" not in environment
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"


def test_import_guard_rejects_nonexact_browser_origins_before_murmur_import() -> None:
    paths = _paths("cors")
    environment = build_environment(paths)
    environment["ALLOWED_CORS_ORIGINS"] = "https://arbitrary.example.test"
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "exact loopback browser origins" in result.stderr
    assert "murmur.voice.pipecat_composition" not in result.stderr


def test_import_guard_rejects_broad_ambient_provider_or_firebase_credentials() -> None:
    paths = _paths("credentials")
    environment = build_environment(paths)
    environment["UNLISTED_VENDOR_API_KEY"] = "must-not-enter-fake-app"
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "does not accept provider credentials" in result.stderr
    assert environment["UNLISTED_VENDOR_API_KEY"] not in result.stderr


def test_import_guard_requires_dotenv_loading_disabled_before_murmur_import() -> None:
    paths = _paths("dotenv")
    environment = build_environment(paths)
    environment.pop("PYTHON_DOTENV_DISABLED")
    try:
        result = _run_isolated(
            "import scripts.voice_pipecat_e2e_app",
            environment,
        )
    finally:
        _cleanup(paths)

    assert result.returncode != 0
    assert "requires disabled dotenv loading" in result.stderr
    assert "murmur.voice.pipecat_composition" not in result.stderr


def test_guarded_app_uses_production_bootstrap_release_and_sanitized_status() -> None:
    paths = _paths("http")
    paths.run_dir.mkdir(parents=True)
    environment = build_environment(paths)
    code = r"""
import json
from fastapi.testclient import TestClient
from scripts.voice_pipecat_e2e_app import E2E_AGENT_ID, E2E_SESSION_ID, app

headers = {"Authorization": "Bearer voice-e2e"}
voice_call_id = "50000000-0000-4000-8000-000000000005"
body = {"session_id": E2E_SESSION_ID, "voice_call_id": voice_call_id}
with TestClient(app) as client:
    unauthorized = client.get("/_e2e/health")
    health = client.get("/_e2e/health", headers=headers)
    bootstrap = client.post("/api/voice/session", headers=headers, json=body)
    release = client.post("/api/voice/session/end", headers=headers, json=body)
    status = client.post("/_e2e/pipecat/status", headers=headers, json=body)
result = {
    "unauthorized": unauthorized.status_code,
    "health": health.json(),
    "bootstrap_status": bootstrap.status_code,
    "assignment": bootstrap.json(),
    "release_status": release.status_code,
    "status_status": status.status_code,
    "terminal": status.json(),
    "expected_agent_id": E2E_AGENT_ID,
}
print("PIPECAT_E2E_RESULT=" + json.dumps(result, sort_keys=True))
"""
    try:
        result = _run_isolated(code, environment)
        marker = next(
            (
                line.removeprefix("PIPECAT_E2E_RESULT=")
                for line in result.stdout.splitlines()
                if line.startswith("PIPECAT_E2E_RESULT=")
            ),
            None,
        )
        assert result.returncode == 0, result.stderr
        assert marker is not None, result.stdout
        payload = json.loads(marker)
    finally:
        _cleanup(paths)

    assert payload["unauthorized"] == 401
    assert payload["health"] == {
        "schema_version": 1,
        "ok": True,
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": "pipecat-fake-rtc-v1",
        "agent_id": payload["expected_agent_id"],
        "session_id": "a4f4328e-185e-4c65-b3f7-101e04a37578",
        "network": "direct-loopback",
        "providers": "fake",
        "livekit_imported": False,
        "cost": "unmeasured",
    }
    assert payload["bootstrap_status"] == 200
    assignment = payload["assignment"]
    assert assignment["runtime"] == "pipecat_smallwebrtc_v1"
    assert assignment["profile_id"] == "pipecat-fake-rtc-v1"
    assert assignment["ice_servers"] == []
    assert assignment["webrtc_url"].startswith("http://127.0.0.1:8101/api/voice/pipecat/signal/")
    assert payload["release_status"] == 204
    assert payload["status_status"] == 200
    terminal = payload["terminal"]
    assert terminal["status"] == "pending"
    assert terminal["reservation"] == {
        "state": "terminal",
        "cleanup_complete": True,
        "terminal_reason": "user_ended",
        "retryable": False,
    }
    assert terminal["control_plane"] == {
        "bootstrap_active_assignment_count": 0,
        "bootstrap_active_lock_count": 0,
        "signaling_active_call_count": 0,
        "runtime_handle_retained": False,
        "cleanup_retry_pending": False,
        "runtime_observer_pending": False,
        "expiry_pending": False,
        "trusted_release_pending": False,
    }
    assert terminal["fake_media"]["media_contract_satisfied"] is False

"""Guard and ownership tests for the loopback Voice V2 E2E application."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _guarded_env(database_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "MURMUR_E2E_MODE": "1",
        "MURMUR_ENVIRONMENT": "test",
        "VOICE_RUNTIME": "livekit_v2",
        "VOICE_V2_PROFILE_ID": "fake-rtc-v1",
        "LIVEKIT_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_API_KEY": "devkey",
        "LIVEKIT_API_SECRET": "secret",
        "VOICE_V2_SIGNING_SECRET": "voice-e2e-signing-secret-32-bytes-minimum",
        "MURMUR_DATABASE_URL": f"sqlite:///{database_path}",
    }


def test_e2e_app_refuses_import_without_explicit_guard(tmp_path: Path) -> None:
    env = _guarded_env(tmp_path / "var" / "voice-e2e" / "murmur.db")
    env.pop("MURMUR_E2E_MODE")
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.voice_e2e_app"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "MURMUR_E2E_MODE=1" in result.stderr


def test_e2e_app_seeds_exact_owned_scope_and_overrides_only_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "var" / "voice-e2e" / "run-1" / "murmur.db"
    database_path.parent.mkdir(parents=True)
    for key, value in _guarded_env(database_path).items():
        monkeypatch.setenv(key, value)

    # Configuration and database modules are import-time bound, so exercise the
    # positive route in an isolated interpreter as the real stack runner does.
    script = """
from fastapi.testclient import TestClient
from scripts.voice_e2e_app import E2E_AGENT_ID, E2E_SESSION_ID, app
with TestClient(app) as client:
    health = client.get('/_e2e/health')
    assert health.status_code == 200, health.text
    payload = health.json()
    assert payload['agent_id'] == E2E_AGENT_ID
    assert payload['session_id'] == E2E_SESSION_ID
    agent = client.get(f'/api/agents/{E2E_AGENT_ID}', headers={'Authorization': 'Bearer ignored'})
    assert agent.status_code == 200, agent.text
print('voice-e2e-app-ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=_guarded_env(database_path),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "voice-e2e-app-ok" in result.stdout

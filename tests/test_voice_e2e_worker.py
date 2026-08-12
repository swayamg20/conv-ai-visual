"""Fail-closed composition tests for the local RTC worker entrypoint."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _worker_env(tmp_path: Path) -> dict[str, str]:
    evidence = PROJECT_ROOT / "var" / "evals" / "voice-e2e-worker-test.jsonl"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "MURMUR_E2E_MODE": "1",
        "MURMUR_ENVIRONMENT": "test",
        "VOICE_V2_PROFILE_ID": "fake-rtc-v1",
        "VOICE_V2_WORKER_NAME": "murmur-voice-v2-e2e",
        "VOICE_V2_SIGNING_SECRET": "voice-e2e-signing-secret-32-bytes-minimum",
        "LIVEKIT_URL": "ws://127.0.0.1:7880",
        "MURMUR_E2E_ASSISTANT_FIXTURE_PATH": str(
            PROJECT_ROOT / "tests" / "fixtures" / "voice" / "audio" / "assistant-long.wav"
        ),
        "MURMUR_E2E_EVIDENCE_PATH": str(evidence),
        "MURMUR_DATABASE_URL": f"sqlite:///{tmp_path / 'worker.db'}",
    }


def test_e2e_worker_refuses_import_without_guard(tmp_path: Path) -> None:
    env = _worker_env(tmp_path)
    env.pop("MURMUR_E2E_MODE")
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.voice_e2e_worker"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "MURMUR_E2E_MODE=1" in result.stderr


def test_e2e_worker_builds_one_guarded_native_server(tmp_path: Path) -> None:
    script = """
from pathlib import Path
from livekit.agents.cli.discover import get_import_data
import scripts.voice_e2e_worker as worker
assert worker.server is not None
assert worker.server._agent_name == 'murmur-voice-v2-e2e'
discovered = get_import_data(path=Path('scripts/voice_e2e_worker.py'))
assert discovered.import_string == 'voice_e2e_worker:server'
print('voice-e2e-worker-ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=_worker_env(tmp_path),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "voice-e2e-worker-ok" in result.stdout

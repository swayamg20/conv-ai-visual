"""Focused unit checks for the loopback RTC stack orchestrator."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STACK_MODULE_PATH = PROJECT_ROOT / "scripts" / "voice_e2e_stack.py"
STACK_SPEC = importlib.util.spec_from_file_location("voice_e2e_stack", STACK_MODULE_PATH)
assert STACK_SPEC is not None and STACK_SPEC.loader is not None
voice_e2e_stack = importlib.util.module_from_spec(STACK_SPEC)
sys.modules[STACK_SPEC.name] = voice_e2e_stack
STACK_SPEC.loader.exec_module(voice_e2e_stack)

BROWSER_FIXTURE = voice_e2e_stack.BROWSER_FIXTURE
APP_PORT = voice_e2e_stack.APP_PORT
LIVEKIT_SERVER_IMAGE = voice_e2e_stack.LIVEKIT_SERVER_IMAGE
WEB_PORT = voice_e2e_stack.WEB_PORT
StackError = voice_e2e_stack.StackError
_preflight_ports = voice_e2e_stack._preflight_ports
_query_livekit_state = voice_e2e_stack._query_livekit_state
_read_browser_result = voice_e2e_stack._read_browser_result
_evidence_summary = voice_e2e_stack._evidence_summary
build_commands = voice_e2e_stack.build_commands
build_environment = voice_e2e_stack.build_environment
make_paths = voice_e2e_stack.make_paths
StackOptions = voice_e2e_stack.StackOptions
VoiceE2EStack = voice_e2e_stack.VoiceE2EStack


def test_environment_is_file_backed_guarded_and_provider_free() -> None:
    paths = make_paths("unit-environment")
    environment = build_environment(
        paths,
        {
            "PATH": os.environ["PATH"],
            "OPENAI_API_KEY": "real-openai-key",
            "ELEVENLABS_API_KEY": "real-elevenlabs-key",
            "UNLISTED_VENDOR_API_KEY": "real-unlisted-key",
            "GOOGLE_APPLICATION_CREDENTIALS": "/real/google.json",
            "MURMUR_DATABASE_URL": "sqlite:///:memory:",
            "VOICE_RUNTIME": "legacy",
        },
    )

    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert environment["MURMUR_E2E_MODE"] == "1"
    assert environment["MURMUR_ENVIRONMENT"] == "test"
    assert environment["VOICE_RUNTIME"] == "livekit_v2"
    assert environment["VOICE_V2_PROFILE_ID"] == "fake-rtc-v1"
    assert environment["MURMUR_DATABASE_URL"].startswith("sqlite:////")
    assert not environment["MURMUR_DATABASE_URL"].endswith(":memory:")
    assert environment["OPENAI_API_KEY"] == ""
    assert environment["ELEVENLABS_API_KEY"] == ""
    assert "UNLISTED_VENDOR_API_KEY" not in environment
    assert environment["GOOGLE_APPLICATION_CREDENTIALS"] == ""
    assert environment["VOICE_E2E_API_URL"] == f"http://127.0.0.1:{APP_PORT}"
    assert environment["VOICE_E2E_WEB_URL"] == f"http://127.0.0.1:{WEB_PORT}"
    assert Path(environment["VOICE_E2E_BROWSER_AUDIO_FIXTURE"]) == BROWSER_FIXTURE.resolve()
    assert Path(environment["VOICE_E2E_RESULT_PATH"]) == paths.browser_result
    assert environment["VOICE_E2E_NEXT_DIST_DIR"] == ".next-voice-e2e/unit-environment"
    assert environment["NO_PROXY"] == "127.0.0.1,localhost,::1"
    assert paths.web_workspace == paths.run_dir / "web-workspace"


def test_commands_pin_sfu_loopback_ports_and_production_worker() -> None:
    paths = make_paths("unit-commands")
    commands = build_commands(paths)

    assert LIVEKIT_SERVER_IMAGE in commands.livekit
    assert "127.0.0.1:7880:7880" in commands.livekit
    assert "127.0.0.1:7881:7881" in commands.livekit
    assert "127.0.0.1:7882:7882/udp" in commands.livekit
    assert commands.livekit[-6:] == (
        "--bind",
        "0.0.0.0",
        "--node-ip",
        "127.0.0.1",
        "--udp-port",
        "7882",
    )
    assert "--dev" not in commands.worker
    assert commands.worker[-1] == "scripts/voice_e2e_worker.py"
    assert commands.app[-5:] == (
        "scripts.voice_e2e_app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(APP_PORT),
    )
    assert commands.web_build == ("npm", "run", "build", "--", "--webpack")
    assert commands.browser == ("npm", "run", "test:e2e:offline")


def test_port_preflight_fails_on_an_exact_occupied_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        occupied_port = int(occupied.getsockname()[1])
        monkeypatch.setattr(voice_e2e_stack, "_TCP_PORTS", (occupied_port,))
        monkeypatch.setattr(voice_e2e_stack, "_UDP_PORTS", ())
        with pytest.raises(StackError, match=f"required TCP loopback port {occupied_port}"):
            _preflight_ports()
    finally:
        occupied.close()


def test_container_preflight_fails_closed_and_never_owns_foreign_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = VoiceE2EStack(StackOptions(run_id="unit-container-preflight", dry_run=True))
    calls: list[tuple[str, ...]] = []

    def existing(command: tuple[str, ...], **_: object) -> object:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"foreign-container\n", stderr=b"")

    monkeypatch.setattr(voice_e2e_stack.subprocess, "run", existing)
    with pytest.raises(StackError, match="refusing to reuse existing Docker container"):
        stack._preflight_container_name()

    assert calls[0][-2:] == ("--format", "{{.ID}}")

    stack._teardown()
    assert not any(command[:3] == ("docker", "rm", "--force") for command in calls)

    def unavailable(command: tuple[str, ...], **_: object) -> object:
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"daemon denied request")

    monkeypatch.setattr(voice_e2e_stack.subprocess, "run", unavailable)
    with pytest.raises(StackError, match="container-name preflight failed"):
        stack._preflight_container_name()
    stack._teardown()
    assert not any(command[:3] == ("docker", "rm", "--force") for command in calls)


def test_container_claim_requires_private_nonce_before_teardown_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = VoiceE2EStack(StackOptions(run_id="unit-container-claim", dry_run=True))
    removed: list[tuple[str, ...]] = []

    class RunningProcess:
        def ensure_running(self) -> None:
            return

    def foreign(command: tuple[str, ...], **_: object) -> object:
        return SimpleNamespace(
            returncode=0,
            stdout=b"foreign-id foreign-private-nonce\n",
            stderr=b"",
        )

    monkeypatch.setattr(voice_e2e_stack.subprocess, "run", foreign)
    with pytest.raises(StackError, match="ownership label did not match"):
        stack._claim_started_container(RunningProcess())  # type: ignore[arg-type]
    stack._teardown()

    def owned(command: tuple[str, ...], **_: object) -> object:
        if command[:3] == ("docker", "rm", "--force"):
            removed.append(command)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return SimpleNamespace(
            returncode=0,
            stdout=f"owned-id {stack._ownership_nonce}\n".encode(),
            stderr=b"",
        )

    monkeypatch.setattr(voice_e2e_stack.subprocess, "run", owned)
    stack._claim_started_container(RunningProcess())  # type: ignore[arg-type]
    stack._teardown()

    assert removed == [("docker", "rm", "--force", "owned-id")]


def test_browser_result_requires_cleanup_identifiers(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "room_name": "murmur-test-room",
                "dispatch_id": "AD_test",
                "voice_call_id": "6d50b297-2e3f-45ff-9cf2-512712c2e5e8",
                "additional_evidence": {"remote_audio": True},
            }
        ),
        encoding="utf-8",
    )

    decoded = _read_browser_result(result)

    assert decoded["room_name"] == "murmur-test-room"
    assert decoded["additional_evidence"] == {"remote_audio": True}


def test_room_absence_proves_dispatch_absence_without_room_scoped_query() -> None:
    class RoomService:
        async def list_rooms(self, _request: object) -> object:
            return SimpleNamespace(rooms=[])

    class DispatchService:
        async def list_dispatch(self, _room_name: str) -> object:
            raise AssertionError("dispatch query must not run after exact room absence")

    client = SimpleNamespace(room=RoomService(), agent_dispatch=DispatchService())

    state = voice_e2e_stack.asyncio.run(_query_livekit_state(client, "murmur-test-gone", "AD_gone"))

    assert state == {
        "room_name": "murmur-test-gone",
        "room_present": False,
        "dispatch_id": "AD_gone",
        "dispatch_present": False,
        "dispatch_ids": [],
        "dispatch_query": "room_absent_confirms_dispatch_absent",
    }


def test_evidence_summary_requires_complete_media_and_one_matching_profile(
    tmp_path: Path,
) -> None:
    voice_call_id = "6d50b297-2e3f-45ff-9cf2-512712c2e5e8"
    evidence = tmp_path / "evidence.jsonl"
    records = [
        {"event": "input_frame"},
        {"event": "speech_onset"},
        {"event": "transcript_emitted", "transcript_type": "interim"},
        {"event": "transcript_emitted", "transcript_type": "final"},
        {"event": "tts_started"},
        {"event": "speech_onset"},
        {"event": "transcript_emitted", "transcript_type": "final"},
        {"event": "tts_cancelled"},
        {"event": "profile_closed", "voice_call_id": voice_call_id},
    ]
    evidence.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    complete = _evidence_summary(evidence, voice_call_id)

    assert complete["event_counts"]["input_frame"] == 1
    assert complete["final_transcript_emitted"] == 2
    assert complete["profile_closed"] == 1
    assert complete["matching_profile_closed"] == 1
    assert complete["media_contract_satisfied"] is True

    records.append({"event": "profile_closed", "voice_call_id": "another-call"})
    evidence.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    ambiguous = _evidence_summary(evidence, voice_call_id)

    assert ambiguous["profile_closed"] == 2
    assert ambiguous["matching_profile_closed"] == 1
    assert ambiguous["media_contract_satisfied"] is False


def test_web_build_workspace_cannot_mutate_tracked_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-web"
    source.mkdir()
    for directory in ("e2e", "src", "node_modules"):
        (source / directory).mkdir()
    source_next_env = "/// original next env\n"
    source_tsconfig = '{"include": ["next-env.d.ts"]}\n'
    required_files = {
        ".env.example": "",
        "eslint.config.mjs": "",
        "next-env.d.ts": source_next_env,
        "next.config.mjs": "export default {};\n",
        "package-lock.json": "{}\n",
        "package.json": "{}\n",
        "playwright.config.ts": "",
        "postcss.config.mjs": "",
        "tailwind.config.ts": "",
        "tsconfig.json": source_tsconfig,
        "vitest.config.mts": "",
    }
    for relative, content in required_files.items():
        (source / relative).write_text(content, encoding="utf-8")

    stack = VoiceE2EStack(StackOptions(run_id="unit-web-isolation", dry_run=True))
    stack.paths = replace(
        stack.paths,
        run_dir=tmp_path / "run",
        web_workspace=tmp_path / "run" / "web-workspace",
    )
    monkeypatch.setattr(voice_e2e_stack, "WEB_ROOT", source)

    stack._prepare_web_workspace()
    (stack.paths.web_workspace / "next-env.d.ts").write_text("generated\n", encoding="utf-8")
    (stack.paths.web_workspace / "tsconfig.json").write_text("generated\n", encoding="utf-8")

    assert (source / "next-env.d.ts").read_text(encoding="utf-8") == source_next_env
    assert (source / "tsconfig.json").read_text(encoding="utf-8") == source_tsconfig
    assert (stack.paths.web_workspace / "node_modules").is_symlink()
    assert stack._web_cwd == stack.paths.web_workspace


def test_dry_run_is_side_effect_free_and_redacts_local_worker_credentials(tmp_path: Path) -> None:
    run_id = "unit-dry-run"
    paths = make_paths(run_id)
    assert not paths.run_dir.exists()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/voice_e2e_stack.py",
            "--dry-run",
            "--run-id",
            run_id,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "OPENAI_API_KEY": "must-not-appear"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    description = json.loads(result.stdout)
    assert description["livekit_server_image"] == LIVEKIT_SERVER_IMAGE
    assert "<local-test-credential>" in description["commands"]["worker"]
    assert "must-not-appear" not in result.stdout
    assert not paths.run_dir.exists()

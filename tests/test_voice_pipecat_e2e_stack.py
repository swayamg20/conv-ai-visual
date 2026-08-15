"""Focused contracts for the Docker-free Pipecat browser stack runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_stack import (  # noqa: E402
    _PLAYWRIGHT_PROCESS_NAME,
    PipecatBrowserStack,
    StackError,
    StackPaths,
    _ManagedProcess,
    _pipecat_app_command,
    _pipecat_browser_command,
    _read_pipecat_browser_result,
    _sanitize_log_file,
    _scan_qualification_artifacts,
    _validate_playwright_report,
    build_environment,
    build_web_environment,
)


def _paths(tmp_path: Path) -> StackPaths:
    run_dir = tmp_path / "rtc-test"
    return StackPaths(
        run_id="rtc-test",
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=tmp_path / "evidence.jsonl",
        server_log=run_dir / "pipecat-asgi.log",
        proof=run_dir / "backend-checkpoint.json",
    )


def _terminal(voice_call_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "passed",
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": "pipecat-fake-rtc-v1",
        "session_id": "a4f4328e-185e-4c65-b3f7-101e04a37578",
        "voice_call_id": voice_call_id,
        "reservation": {
            "state": "terminal",
            "cleanup_complete": True,
            "terminal_reason": "client_disconnected",
            "retryable": True,
        },
        "control_plane": {
            "bootstrap_active_assignment_count": 0,
            "bootstrap_active_lock_count": 0,
            "signaling_active_call_count": 0,
            "runtime_handle_retained": False,
            "cleanup_retry_pending": False,
            "runtime_observer_pending": False,
            "expiry_pending": False,
            "trusted_release_pending": False,
        },
        "fake_media": {
            "input_frame_count": 100,
            "final_transcripts": ["Hello tutor.", "Actually, stop."],
            "llm_response_count": 2,
            "tts_frame_count": 400,
            "tts_cancelled_count": 1,
            "cleaned_processors": ["llm", "stt", "tts"],
            "processor_cleanup_counts": {"stt": 1, "llm": 1, "tts": 1},
            "profile_close_count": 1,
            "media_contract_satisfied": True,
        },
    }


def _audio_sample_clock(
    local_track_id: str,
    remote_track_id: str,
) -> dict[str, object]:
    return {
        "evidence": {
            "schema_version": 1,
            "worklet_loaded": True,
            "sample_rate_hz": 48_000,
            "quantum_frames": 128,
            "disposed": False,
            "local": {
                "attached": True,
                "exact_track_id": local_track_id,
                "threshold_rms": 0.005,
                "silence_hold_frames": 24_064,
                "processed_block_count": 821,
                "latest_block_end_frame": 105_088,
                "current_state": "silent",
                "current_state_block_count": 196,
                "active_region_count": 2,
                "transitions": [
                    {"state": "silent", "block_start_frame": 0, "block_end_frame": 128},
                    {
                        "state": "active",
                        "block_start_frame": 30_080,
                        "block_end_frame": 30_208,
                    },
                    {
                        "state": "silent",
                        "block_start_frame": 45_056,
                        "block_end_frame": 45_184,
                    },
                    {
                        "state": "active",
                        "block_start_frame": 69_248,
                        "block_end_frame": 69_376,
                    },
                    {
                        "state": "silent",
                        "block_start_frame": 80_000,
                        "block_end_frame": 80_128,
                    },
                ],
                "overflow": False,
                "failure_code": None,
            },
            "remote": {
                "attached": True,
                "exact_track_id": remote_track_id,
                "threshold_rms": 0.012,
                "silence_hold_frames": 9_600,
                "processed_block_count": 860,
                "latest_block_end_frame": 110_080,
                "current_state": "silent",
                "current_state_block_count": 110,
                "active_region_count": 2,
                "transitions": [
                    {"state": "silent", "block_start_frame": 0, "block_end_frame": 128},
                    {
                        "state": "active",
                        "block_start_frame": 40_064,
                        "block_end_frame": 40_192,
                    },
                    {
                        "state": "silent",
                        "block_start_frame": 78_080,
                        "block_end_frame": 78_208,
                    },
                    {
                        "state": "active",
                        "block_start_frame": 88_064,
                        "block_end_frame": 88_192,
                    },
                    {
                        "state": "silent",
                        "block_start_frame": 96_000,
                        "block_end_frame": 96_128,
                    },
                ],
                "overflow": False,
                "failure_code": None,
            },
        },
        "interruption_bracket": {
            "status": "passed",
            "failure_code": None,
            "sample_rate_hz": 48_000,
            "quantum_frames": 128,
            "required_silence_frames": 9_600,
            "second_local_active_block_start_frame": 69_248,
            "remote_silence_transition_block_end_frame": 78_208,
            "interruption_upper_bound_frames": 8_960,
            "interruption_upper_bound_ms": 186.667,
        },
    }


def _browser_result() -> dict[str, object]:
    voice_call_id = "10000000-0000-4000-8000-000000000001"
    local_track_id = "local-track-test-1"
    remote_track_id = "remote-track-test-1"
    return {
        "schema_version": 1,
        "status": "passed",
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": "pipecat-fake-rtc-v1",
        "peer_reservation_id": "peer-reservation-test-1",
        "voice_call_id": voice_call_id,
        "trace_id": "20000000-0000-4000-8000-000000000002",
        "browser_evidence": {
            "exact_local_track_id": local_track_id,
            "exact_remote_track_id": remote_track_id,
            "connection_gestures": [
                {"sequence": 1, "action": "prepare"},
                {"sequence": 2, "action": "activate"},
            ],
            "peer_connection_count": 1,
            "outbound_bytes_sent": 100,
            "outbound_packets_sent": 10,
            "inbound_bytes_received": 200,
            "inbound_packets_received": 20,
            "selected_candidate_pair": {
                "state": "succeeded",
                "nominated": True,
                "bytes_sent": 100,
                "bytes_received": 200,
                "current_round_trip_time_seconds": 0.01,
                "local": {
                    "candidate_type": "host",
                    "protocol": "udp",
                    "relay_protocol": None,
                },
                "remote": {
                    "candidate_type": "host",
                    "protocol": "udp",
                    "relay_protocol": None,
                },
            },
            "signaling_request_counts": {
                "post": 1,
                "authenticated_post": 1,
                "patch": 2,
                "authenticated_patch": 2,
                "delete": 1,
                "authenticated_delete": 1,
                "with_cookies": 0,
            },
            "audio_sample_clock": _audio_sample_clock(
                local_track_id,
                remote_track_id,
            ),
        },
        "browser_cleanup_observed": True,
        "terminal_cleanup": _terminal(voice_call_id),
    }


def _playwright_report() -> dict[str, object]:
    return {
        "errors": [],
        "stats": {"expected": 1, "unexpected": 0, "flaky": 0},
        "suites": [
            {
                "title": "voice-pipecat-rtc.spec.ts",
                "specs": [
                    {
                        "ok": True,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "passed",
                                        "errors": [],
                                        "attachments": [],
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_backend_and_web_environments_are_separate_and_strip_ambient_secrets(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    ambient = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "provider-secret",
        "UNLISTED_VENDOR_AUTH_TOKEN": "provider-secret",
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "ambient-project",
        "NEXT_PUBLIC_LIVEKIT_URL": "ambient-livekit",
        "FORCE_COLOR": "1",
        "HTTPS_PROXY": "proxy.example.invalid",
    }

    backend = build_environment(paths, ambient)
    web = build_web_environment(paths, ambient)

    assert backend["PYTHON_DOTENV_DISABLED"] == "1"
    assert backend["NO_PROXY"] == backend["no_proxy"] == "127.0.0.1,localhost,::1"
    assert not any("FIREBASE" in name or "LIVEKIT" in name for name in backend)
    assert "OPENAI_API_KEY" not in backend
    assert "UNLISTED_VENDOR_AUTH_TOKEN" not in backend
    assert web["NEXT_PUBLIC_FIREBASE_PROJECT_ID"] == "voice-pipecat-e2e"
    assert "NEXT_PUBLIC_LIVEKIT_URL" not in web
    assert "UNLISTED_VENDOR_AUTH_TOKEN" not in web
    assert "FORCE_COLOR" not in web
    assert web["NEXT_PUBLIC_VOICE_RUNTIME"] == "voice_v2"
    assert web["VOICE_E2E_API_URL"] == "http://127.0.0.1:8101"
    assert Path(web["VOICE_E2E_RESULT_PATH"]).is_absolute()


def test_process_plan_is_docker_free_and_targets_only_pipecat_spec() -> None:
    commands = (_pipecat_app_command(), _pipecat_browser_command())
    rendered = " ".join(item for command in commands for item in command).casefold()

    assert "docker" not in rendered
    assert "livekit" not in rendered
    assert _pipecat_browser_command() == (
        "./node_modules/.bin/playwright",
        "test",
        "e2e/voice-pipecat-rtc.spec.ts",
    )
    assert "--no-access-log" in _pipecat_app_command()


def test_one_shot_step_stops_its_process_group_on_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[str] = []
    monkeypatch.setattr(_ManagedProcess, "start", lambda self: None)

    def interrupt(_self: _ManagedProcess, _timeout_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_ManagedProcess, "wait_success", interrupt)
    monkeypatch.setattr(
        _ManagedProcess,
        "stop",
        lambda self, **_kwargs: stopped.append(self.name),
    )
    stack = PipecatBrowserStack(_paths(tmp_path))

    with pytest.raises(KeyboardInterrupt):
        stack._run_step(
            "interruptible step",
            ("fake-command",),
            tmp_path,
            tmp_path / "step.log",
            5.0,
            {},
        )

    assert stopped == ["interruptible step"]


def test_managed_process_closes_log_when_process_group_stop_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _ManagedProcess("failing stop", (), tmp_path, {}, tmp_path / "stop.log")
    process.process = SimpleNamespace(pid=1234, poll=lambda: None)  # type: ignore[assignment]
    closed: list[bool] = []
    process._log_handle = SimpleNamespace(close=lambda: closed.append(True))

    def fail_kill(_pid: int, _signal: int) -> None:
        raise RuntimeError("synthetic process-group failure")

    monkeypatch.setattr("scripts.voice_pipecat_e2e_stack.os.killpg", fail_kill)

    with pytest.raises(RuntimeError, match="synthetic process-group failure"):
        process.stop()

    assert closed == [True]
    assert process._log_handle is None


def test_teardown_stops_every_process_and_sanitizes_after_stop_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    stack = PipecatBrowserStack(paths)
    stopped: list[str] = []
    sanitized: list[bool] = []

    def stop_failing_process() -> None:
        stopped.append("failing")
        raise RuntimeError("synthetic stop failure")

    def stop_healthy_process() -> None:
        stopped.append("healthy")

    stack._processes = [  # type: ignore[assignment]
        SimpleNamespace(stop=stop_healthy_process),
        SimpleNamespace(stop=stop_failing_process),
    ]
    paths.web_workspace.mkdir(parents=True)
    monkeypatch.setattr(stack, "_prepare", lambda: None)

    def fail_workspace_preparation() -> None:
        raise StackError("synthetic setup failure")

    monkeypatch.setattr(stack, "_prepare_web_workspace", fail_workspace_preparation)
    original_teardown = stack._teardown

    def teardown() -> None:
        try:
            original_teardown()
        finally:
            assert stack._processes == []
            assert not paths.web_workspace.exists()

    monkeypatch.setattr(stack, "_teardown", teardown)
    monkeypatch.setattr(stack, "_sanitize_owned_logs", lambda: sanitized.append(True))

    with pytest.raises(StackError, match="qualification process teardown failed") as captured:
        stack.run()

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert str(captured.value.__cause__) == "synthetic stop failure"
    assert stopped == ["failing", "healthy"]
    assert sanitized == [True]


def test_playwright_error_tail_never_returns_browser_secret_fields(tmp_path: Path) -> None:
    log = tmp_path / "playwright.log"
    log.write_text(
        'failure snapshot {"pc_id":"opaque-peer","host":"192.0.2.44"}\n',
        encoding="utf-8",
    )
    process = _ManagedProcess(_PLAYWRIGHT_PROCESS_NAME, (), tmp_path, {}, log)

    assert process.sanitized_tail() == (
        "[Playwright diagnostics redacted after forbidden browser data]"
    )


def test_browser_result_and_report_require_exact_media_and_cleanup_contract(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "result.json"
    report_path = tmp_path / "report.json"
    _write_json(result_path, _browser_result())
    _write_json(report_path, _playwright_report())

    assert _read_pipecat_browser_result(result_path) == _browser_result()
    _validate_playwright_report(report_path)

    invalid = _browser_result()
    browser = invalid["browser_evidence"]
    assert isinstance(browser, dict)
    browser["peer_connection_count"] = 2
    _write_json(result_path, invalid)
    with pytest.raises(StackError, match="exactly one peer connection"):
        _read_pipecat_browser_result(result_path)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("candidate_type", None),
        ("relay_protocol", "tcp"),
    ],
)
def test_browser_result_requires_exact_direct_loopback_candidate_tuple(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    selected = browser["selected_candidate_pair"]
    assert isinstance(selected, dict)
    local = selected["local"]
    assert isinstance(local, dict)
    local[field] = invalid_value

    _write_json(path, value)
    with pytest.raises(StackError, match="selected candidate evidence"):
        _read_pipecat_browser_result(path)


def test_browser_result_rejects_extra_selected_candidate_fields(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    selected = browser["selected_candidate_pair"]
    assert isinstance(selected, dict)
    selected["address"] = "redacted"

    _write_json(path, value)
    with pytest.raises(StackError, match="exact selected candidate pair"):
        _read_pipecat_browser_result(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "track_mismatch",
        "probe_failure",
        "probe_overflow",
        "disposed_measurement",
        "unaligned_hold",
        "invalid_transition",
        "processed_counter_mismatch",
        "current_state_counter_mismatch",
        "one_block_counter_forgery",
        "bracket_sample_rate_mismatch",
        "bracket_rounding_mismatch",
    ],
)
def test_browser_result_rejects_tampered_audio_sample_clock(
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    clock = browser["audio_sample_clock"]
    assert isinstance(clock, dict)
    evidence = clock["evidence"]
    bracket = clock["interruption_bracket"]
    assert isinstance(evidence, dict)
    assert isinstance(bracket, dict)
    local = evidence["local"]
    remote = evidence["remote"]
    assert isinstance(local, dict)
    assert isinstance(remote, dict)

    if mutation == "track_mismatch":
        local["exact_track_id"] = "different-local-track"
    elif mutation == "probe_failure":
        remote["failure_code"] = "frame_gap"
    elif mutation == "probe_overflow":
        local["overflow"] = True
    elif mutation == "disposed_measurement":
        evidence["disposed"] = True
    elif mutation == "unaligned_hold":
        remote["silence_hold_frames"] = 9_601
    elif mutation == "invalid_transition":
        transitions = local["transitions"]
        assert isinstance(transitions, list)
        transition = transitions[1]
        assert isinstance(transition, dict)
        transition["block_end_frame"] = 30_209
    elif mutation == "processed_counter_mismatch":
        local["processed_block_count"] = 820
    elif mutation == "current_state_counter_mismatch":
        remote["current_state_block_count"] = 109
    elif mutation == "one_block_counter_forgery":
        remote["processed_block_count"] = 1
        remote["current_state_block_count"] = 1
    elif mutation == "bracket_sample_rate_mismatch":
        bracket["sample_rate_hz"] = 44_100
    elif mutation == "bracket_rounding_mismatch":
        bracket["interruption_upper_bound_ms"] = 186.666
    else:  # pragma: no cover - the parameter list is exhaustive
        raise AssertionError(f"unknown mutation: {mutation}")

    _write_json(path, value)
    with pytest.raises(StackError, match="audio sample-clock"):
        _read_pipecat_browser_result(path)


def test_browser_result_independently_enforces_hard_sample_clock_limit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    clock = browser["audio_sample_clock"]
    assert isinstance(clock, dict)
    evidence = clock["evidence"]
    bracket = clock["interruption_bracket"]
    assert isinstance(evidence, dict)
    assert isinstance(bracket, dict)
    remote = evidence["remote"]
    assert isinstance(remote, dict)
    transitions = remote["transitions"]
    assert isinstance(transitions, list)
    second_silence = transitions[2]
    second_active = transitions[3]
    assert isinstance(second_silence, dict)
    assert isinstance(second_active, dict)
    second_silence.update({"block_start_frame": 81_792, "block_end_frame": 81_920})
    second_active.update({"block_start_frame": 91_392, "block_end_frame": 91_520})
    bracket.update(
        {
            "remote_silence_transition_block_end_frame": 81_920,
            "interruption_upper_bound_frames": 12_672,
            "interruption_upper_bound_ms": 264,
        }
    )

    _write_json(path, value)
    with pytest.raises(StackError, match="hard 250 ms"):
        _read_pipecat_browser_result(path)


@pytest.mark.parametrize(
    "secret",
    [
        "/api/voice/pipecat/signal/opaque-token",
        "Authorization: Bearer voice-e2e",
        '"sdp":"v=0\\r\\n"',
        "candidate:1 1 UDP 1 127.0.0.1 5000 typ host",
        '"pc_id":"SmallWebRTCConnection#raw"',
    ],
)
def test_browser_result_rejects_signaling_secrets(tmp_path: Path, secret: str) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    value["unexpected"] = secret
    _write_json(path, value)

    with pytest.raises(StackError, match="retained forbidden fields"):
        _read_pipecat_browser_result(path)


def test_log_sanitization_and_final_artifact_scan_refuse_sensitive_attachments(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _write_json(paths.browser_result, _browser_result())
    _write_json(paths.playwright_report, _playwright_report())
    for path in (
        paths.run_dir / "web-build.log",
        paths.run_dir / "web.log",
        paths.run_dir / "playwright.log",
    ):
        path.write_text("clean local qualification log\n", encoding="utf-8")
    paths.evidence.write_text('{"event":"profile_closed"}\n', encoding="utf-8")
    paths.server_log.write_text(
        "Discarding peer connection SmallWebRTCConnection#raw-peer\n",
        encoding="utf-8",
    )

    assert _sanitize_log_file(paths.server_log) == {"raw SmallWebRTC peer ID"}
    assert "SmallWebRTCConnection#" not in paths.server_log.read_text(encoding="utf-8")
    _scan_qualification_artifacts(paths)

    trace = paths.playwright_dir / "test-results" / "trace.zip"
    trace.parent.mkdir(parents=True)
    trace.write_bytes(b"sensitive")
    with pytest.raises(StackError, match="trace, video, or screenshot"):
        _scan_qualification_artifacts(paths)


def test_forbidden_service_log_is_replaced_in_full_before_failure(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.log"
    path.write_text(
        "safe prefix\nAuthorization: Bearer secret-value\nsafe suffix\n",
        encoding="utf-8",
    )

    assert _sanitize_log_file(path) == {"authorization bearer"}
    assert path.read_text(encoding="utf-8") == (
        "[qualification artifact redacted after forbidden signaling data]\n"
    )


def test_failed_playwright_artifact_uses_browser_secret_sanitizer(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    playwright_log = paths.run_dir / "playwright.log"
    unsafe_context = paths.playwright_dir / "test-results" / "error-context.md"
    unsafe_context.parent.mkdir(parents=True)
    playwright_log.write_text(
        'safe prefix\n{"pc_id":"opaque-peer","host":"192.0.2.44"}\nsafe suffix\n',
        encoding="utf-8",
    )
    unsafe_context.write_text(
        'safe prefix\n{"pc_id":"opaque-peer","host":"192.0.2.44"}\nsafe suffix\n',
        encoding="utf-8",
    )
    stack = PipecatBrowserStack(paths)
    stack._owned_logs.append(playwright_log)

    with pytest.raises(StackError, match="raw IPv4 address, raw peer ID field"):
        stack._sanitize_owned_logs()

    assert unsafe_context.read_text(encoding="utf-8") == (
        "[qualification artifact redacted after forbidden signaling data]\n"
    )
    assert playwright_log.read_text(encoding="utf-8") == (
        "[qualification artifact redacted after forbidden signaling data]\n"
    )

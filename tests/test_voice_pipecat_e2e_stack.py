"""Focused contracts for the Docker-free Pipecat browser stack runner."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_stack as stack_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    COTURN_FIXTURE_PATH,
    CoturnContractPaths,
    render_coturn_configuration,
)
from scripts.voice_pipecat_e2e_stack import (  # noqa: E402
    _ARTIFACT_MANIFEST_ERROR,
    _ARTIFACT_SAFETY_FAILURE_CLASSIFICATION,
    _ARTIFACT_TAMPER_ERROR,
    _DIRTY_SOURCE_ERROR,
    _PLAYWRIGHT_PROCESS_NAME,
    _PROOF_TIMEOUT_PROGRESS_ERROR,
    _PROOF_TIMEOUT_PROGRESS_MAX_BYTES,
    _PROOF_TIMEOUT_PROGRESS_PREFIX,
    _RELAY_TLS_CONTRACT_ONLY_ERROR,
    _SOURCE_CHANGED_ERROR,
    _SOURCE_PROVENANCE_ERROR,
    _TEARDOWN_FAILURE_CLASSIFICATION,
    PipecatBackendCheckpoint,
    PipecatBrowserStack,
    StackError,
    StackPaths,
    _atomic_write_json,
    _browser_secret_findings,
    _build_artifact_sha256_manifest,
    _default_git_command_runner,
    _extract_proof_timeout_progress_capsule,
    _ManagedProcess,
    _parse_args,
    _pipecat_app_command,
    _pipecat_browser_command,
    _qualification_text_artifact_paths,
    _read_pipecat_browser_result,
    _read_source_provenance,
    _require_unchanged_source,
    _sanitize_log_file,
    _sanitize_sensitive_text,
    _scan_qualification_artifacts,
    _service_secret_findings,
    _validate_artifact_sha256_manifest,
    _validate_playwright_report,
    _validate_proof_timeout_progress_capsule,
    _validate_rtc_stack_proof,
    _validate_source_provenance,
    _write_validated_rtc_stack_proof,
    build_environment,
    build_web_environment,
    main,
)

STATIC_TURN_SECRET = "0123456789abcdef" * 4
TEST_CERTIFICATE_PEM = """\
-----BEGIN CERTIFICATE-----
MIIC9DCCAdygAwIBAgIJAN0Y0Nf5BhrTMA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNV
BAMMCTEyNy4wLjAuMTAeFw0yNjA4MTUxODQzMzRaFw0zNjA4MTIxODQzMzRaMBQx
EjAQBgNVBAMMCTEyNy4wLjAuMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC
ggEBALm89xqJ+knCWn4TF0//M9iECFOV4Y8h7n7ByTv1xfru1pPvLCuw2a0smxu/
Nw+RkpenNkAyXtlENtd4X+1EW5TGR4ekL78Ce5W6PtYZ0fVHpcS2idsIeILkIhYh
3CQylQyqQxziumkEfMRB5h5v8Zc/o0hUYSTIi92oYPR8uZ2tfDjD1fY62ox/DVn7
2GKu22JCCW0OI1ur5CXaMyg7cYeS2eq6iuK2z6JsXSYEr2J3T4sIr51njmFt7+OW
TKfaby9oNVnc9D6aFdiQ1Q4LxZVOW9JyFk2GJYNultjAC7KPBPDz+mowauSRO2As
+6A2qUhdwzTI0j6f9JqEJedHvKECAwEAAaNJMEcwDwYDVR0RBAgwBocEfwAAATAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwICpDATBgNVHSUEDDAKBggrBgEF
BQcDATANBgkqhkiG9w0BAQsFAAOCAQEACi0bImQ3EJChUzmlyxdC35aN/HyGJo8a
sf46nbyz4ILEP0XJS3aoGjCbwoDR5vh6SCADuhGDkbMJ4cgMchm0XoVbrij9PFpZ
iCGf3zUmW+zfnzjvPm380IUPBgbpWX/o02gPHKyw095NhS7R0AUtBkSTeiJqcOdS
dxfiPXDIzxtRTa6yDOfrJZYWSj10IqBc0c5XTaR9yQzxaJ4i/PWS7pN1xEGNPoDr
AK1RHz0iqmKuoFCTbp/UyRWgH9dhRzXmPKkZVQ0IwPMfgaKyQQKDcxfJ6841kKtD
f1dZKbTifuE8OGfJN/9l0jKcX+J07pzQm/x5TvrxfzUc1il21KFmzQ==
-----END CERTIFICATE-----
"""


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


def _relay_material(paths: StackPaths) -> CoturnContractPaths:
    paths.run_dir.mkdir(mode=0o700)
    paths.run_dir.chmod(0o700)
    coturn = CoturnContractPaths.for_run_dir(paths.run_id, paths.run_dir)
    coturn.coturn_dir.mkdir(mode=0o700)
    coturn.coturn_dir.chmod(0o700)
    coturn.config.write_text(
        render_coturn_configuration(
            COTURN_FIXTURE_PATH.read_text(encoding="utf-8"),
            STATIC_TURN_SECRET,
        ),
        encoding="utf-8",
    )
    coturn.cert.write_text(TEST_CERTIFICATE_PEM, encoding="ascii")
    coturn.config.chmod(0o400)
    coturn.cert.chmod(0o400)
    return coturn


def _repo_paths(tmp_path: Path) -> StackPaths:
    run_dir = tmp_path / "var" / "voice-pipecat-e2e" / "rtc-test"
    return StackPaths(
        run_id="rtc-test",
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=tmp_path / "var" / "evals" / "voice-pipecat-e2e-rtc-test.jsonl",
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
                "failure_message_sequence": None,
                "expected_block_start_frame": None,
                "observed_block_start_frame": None,
                "frame_delta_frames": None,
                "last_observed_block_start_frame": None,
                "context_state_at_message_delivery": None,
                "stale_frame_correction_count": 0,
                "last_stale_observed_block_start_frame": None,
                "last_stale_logical_block_start_frame": None,
                "stale_frame_catch_up_observed_block_start_frame": None,
                "stale_frame_correction_pending": False,
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
                "failure_message_sequence": None,
                "expected_block_start_frame": None,
                "observed_block_start_frame": None,
                "frame_delta_frames": None,
                "last_observed_block_start_frame": None,
                "context_state_at_message_delivery": None,
                "stale_frame_correction_count": 0,
                "last_stale_observed_block_start_frame": None,
                "last_stale_logical_block_start_frame": None,
                "stale_frame_catch_up_observed_block_start_frame": None,
                "stale_frame_correction_pending": False,
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


def _source(commit_sha: str = "a" * 40) -> dict[str, object]:
    return {
        "commit_sha": commit_sha,
        "repository_clean": True,
        "dirty_state_refused": True,
    }


def _proof_timeout_progress_capsule() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "pipecat_proof_wait_timeout",
        "snapshot": {
            "assignment_present": True,
            "harness_error_count": 0,
            "connection_gesture_count": 2,
            "local_track_present": True,
            "remote_track_present": True,
            "remote_audio_attached": True,
        },
        "events": {
            "total": 12,
            "agent_ready": 1,
            "turn_committed": 2,
            "speech_started": 2,
            "speech_stopped": 2,
            "speech_stopped_interrupted": 1,
            "speech_stopped_completed": 1,
        },
        "clock": {
            "bracket_status": "pending",
            "bracket_failure": "local_active_region_count",
            "worklet_loaded": True,
            "local_attached": True,
            "remote_attached": True,
            "local_processed_blocks": 100,
            "remote_processed_blocks": 101,
            "local_active_regions": 1,
            "remote_active_regions": 2,
            "local_correction_pending": False,
            "remote_correction_pending": False,
            "local_failed": False,
            "remote_failed": False,
        },
        "pcm": {
            "local_sample_count": 500,
            "remote_sample_count": 501,
            "local_active_region_count": 1,
            "second_local_region_present": False,
            "remote_silence_present": False,
            "remote_audio_before_second_local": False,
        },
        "rtc": {
            "peer_connection_count": 1,
            "selected_candidate_pair_count": 1,
            "outbound_bytes_present": True,
            "outbound_packets_present": True,
            "inbound_bytes_present": True,
            "inbound_packets_present": True,
        },
        "gates": {
            "local_disabled_at_observation": True,
            "local_live_at_observation": True,
            "local_precedes_ready": True,
            "first_event_agent_ready": True,
            "first_reply_interrupted": True,
            "second_turn_present": True,
            "second_reply_started": True,
            "second_reply_after_silence": False,
            "second_reply_completed": True,
            "attribution_observation_complete": False,
            "stale_audio_detected": False,
            "proof_ready": False,
        },
    }


def _write_safe_qualification_artifacts(paths: StackPaths) -> None:
    _write_json(paths.browser_result, _browser_result())
    _write_json(paths.playwright_report, _playwright_report())
    paths.server_log.write_text("clean Pipecat qualification log\n", encoding="utf-8")
    for name in ("web-build.log", "web.log", "playwright.log"):
        (paths.run_dir / name).write_text("clean local qualification log\n", encoding="utf-8")
    paths.evidence.parent.mkdir(parents=True, exist_ok=True)
    paths.evidence.write_text('{"event":"profile_closed"}\n', encoding="utf-8")


def test_proof_timeout_progress_capsule_is_exact_bounded_and_secret_free() -> None:
    capsule = _proof_timeout_progress_capsule()
    rendered = _validate_proof_timeout_progress_capsule(capsule)

    assert rendered.startswith(_PROOF_TIMEOUT_PROGRESS_PREFIX)
    assert len(rendered.encode("utf-8")) < _PROOF_TIMEOUT_PROGRESS_MAX_BYTES
    assert json.loads(rendered.removeprefix(_PROOF_TIMEOUT_PROGRESS_PREFIX)) == capsule
    assert _browser_secret_findings(rendered) == set()
    assert _service_secret_findings(rendered) == set()
    assert not any(
        forbidden in rendered
        for forbidden in (
            "voice_call_id",
            "trace_id",
            "peer_reservation_id",
            "local_samples",
            "remote_samples",
            "transitions",
            "authorization",
            "sdp",
            "ice_servers",
        )
    )
    assert (
        _extract_proof_timeout_progress_capsule(
            f"Error: {rendered}\nunsafe suffix https://secret.invalid/path"
        )
        == rendered
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed",
        "extra",
        "string_secret",
        "invalid_enum",
        "invalid_enum_type",
        "float_schema",
        "distinct_duplicate",
    ],
)
def test_proof_timeout_progress_capsule_rejects_non_allowlisted_input(
    mutation: str,
) -> None:
    capsule = json.loads(json.dumps(_proof_timeout_progress_capsule()))
    if mutation == "malformed":
        raw = "{"
    elif mutation == "extra":
        capsule["secret"] = "https://secret.invalid/path"
        raw = json.dumps(capsule, separators=(",", ":"))
    elif mutation == "string_secret":
        capsule["events"]["total"] = "Bearer raw-secret"
        raw = json.dumps(capsule, separators=(",", ":"))
    elif mutation == "invalid_enum":
        capsule["clock"]["bracket_failure"] = "https://secret.invalid/path"
        raw = json.dumps(capsule, separators=(",", ":"))
    elif mutation == "invalid_enum_type":
        capsule["clock"]["bracket_failure"] = []
        raw = json.dumps(capsule, separators=(",", ":"))
    elif mutation == "float_schema":
        capsule["schema_version"] = 1.0
        raw = json.dumps(capsule, separators=(",", ":"))
    else:
        raw = json.dumps(capsule, separators=(",", ":"))
        other = json.loads(json.dumps(capsule))
        other["events"]["total"] = 13
        raw += (
            "\n"
            + _PROOF_TIMEOUT_PROGRESS_PREFIX
            + json.dumps(
                other,
                separators=(",", ":"),
            )
        )

    with pytest.raises(StackError) as captured:
        _extract_proof_timeout_progress_capsule(
            _PROOF_TIMEOUT_PROGRESS_PREFIX + raw,
        )

    assert str(captured.value) == _PROOF_TIMEOUT_PROGRESS_ERROR
    assert "secret" not in str(captured.value).casefold()


def test_source_provenance_derives_exact_root_sha_and_clean_state() -> None:
    commands: list[tuple[str, ...]] = []
    sha = "1" * 40

    def command_runner(
        arguments: tuple[str, ...],
        repository_root: Path,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        outputs = {
            ("rev-parse", "--show-toplevel"): f"{repository_root}\n",
            ("rev-parse", "--verify", "HEAD^{commit}"): f"{sha}\n",
            (
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ): "",
        }
        return subprocess.CompletedProcess(arguments, 0, outputs[arguments], "")

    assert _read_source_provenance(command_runner=command_runner) == _source(sha)
    assert commands == [
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--verify", "HEAD^{commit}"),
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
    ]


@pytest.mark.parametrize(
    "status_output",
    [
        "M  staged-secret-name.py\n",
        " M unstaged-secret-name.py\n",
        "?? untracked-secret-name.py\n",
        "   ",
    ],
)
def test_source_provenance_refuses_every_dirty_git_state_without_path_leakage(
    status_output: str,
) -> None:
    def command_runner(
        arguments: tuple[str, ...],
        repository_root: Path,
    ) -> subprocess.CompletedProcess[str]:
        if arguments == ("rev-parse", "--show-toplevel"):
            output = str(repository_root)
        elif arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            output = "2" * 40
        else:
            output = status_output
        return subprocess.CompletedProcess(arguments, 0, output, "")

    with pytest.raises(StackError) as captured:
        _read_source_provenance(command_runner=command_runner)

    assert str(captured.value) == _DIRTY_SOURCE_ERROR
    assert "secret-name" not in str(captured.value)


@pytest.mark.parametrize("failure", ["missing_git", "bad_sha", "wrong_root", "git_error"])
def test_source_provenance_failures_are_fixed_and_never_echo_git_output(
    failure: str,
) -> None:
    def command_runner(
        arguments: tuple[str, ...],
        repository_root: Path,
    ) -> subprocess.CompletedProcess[str]:
        if failure == "missing_git":
            raise FileNotFoundError("raw-secret-from-path")
        if arguments == ("rev-parse", "--show-toplevel"):
            output = str(repository_root) if failure != "wrong_root" else "/raw-secret-wrong-root"
            return subprocess.CompletedProcess(arguments, 0, output, "")
        if arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            output = "raw-secret-bad-sha" if failure == "bad_sha" else "3" * 40
            return subprocess.CompletedProcess(arguments, 0, output, "")
        return subprocess.CompletedProcess(
            arguments,
            1 if failure == "git_error" else 0,
            "",
            "raw-secret-git-error",
        )

    with pytest.raises(StackError) as captured:
        _read_source_provenance(command_runner=command_runner)

    assert str(captured.value) == _SOURCE_PROVENANCE_ERROR
    assert "raw-secret" not in str(captured.value)


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        SimpleNamespace(returncode=0, stdout=None),
        SimpleNamespace(returncode=True, stdout=""),
        SimpleNamespace(returncode="0", stdout=""),
    ],
)
def test_source_provenance_rejects_malformed_command_results_safely(
    malformed: object,
) -> None:
    def command_runner(
        _arguments: tuple[str, ...],
        _repository_root: Path,
    ) -> subprocess.CompletedProcess[str]:
        return malformed  # type: ignore[return-value]

    with pytest.raises(StackError) as captured:
        _read_source_provenance(command_runner=command_runner)

    assert str(captured.value) == _SOURCE_PROVENANCE_ERROR


@pytest.mark.parametrize("malformed", [None, [], {"commit_sha": "a" * 40}])
def test_source_payload_validation_is_fail_closed(malformed: object) -> None:
    with pytest.raises(StackError) as captured:
        _validate_source_provenance(malformed)

    assert str(captured.value) == _SOURCE_PROVENANCE_ERROR


def test_default_git_runner_uses_depoisoned_noninteractive_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("GIT_DIR", "/raw-secret-git-dir")
    monkeypatch.setenv("GIT_INDEX_FILE", "/raw-secret-index")

    def run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    _default_git_command_runner(("status", "--porcelain=v1"), PROJECT_ROOT)

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert not any(
        name.startswith("GIT_")
        for name in environment
        if name not in {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}
    )
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True
    assert captured["timeout"] == 10


@pytest.mark.parametrize("mode", ["browser", "backend"])
def test_source_gate_runs_before_prepare_and_creates_no_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    paths = _paths(tmp_path)
    prepared: list[bool] = []

    def refuse_dirty_source() -> dict[str, object]:
        raise StackError(_DIRTY_SOURCE_ERROR)

    stack = (
        PipecatBrowserStack(paths, source_reader=refuse_dirty_source)
        if mode == "browser"
        else PipecatBackendCheckpoint(paths, source_reader=refuse_dirty_source)
    )
    monkeypatch.setattr(stack, "_prepare", lambda: prepared.append(True))

    with pytest.raises(StackError) as captured:
        stack.run()

    assert str(captured.value) == _DIRTY_SOURCE_ERROR
    assert prepared == []
    assert not paths.run_dir.exists()
    assert not paths.evidence.exists()


def test_source_recheck_requires_the_same_clean_commit() -> None:
    observed = iter((_source("4" * 40), _source("5" * 40)))
    assert next(observed) == _source("4" * 40)

    with pytest.raises(StackError) as captured:
        _require_unchanged_source(lambda: next(observed), _source("4" * 40))

    assert str(captured.value) == _SOURCE_CHANGED_ERROR


def test_backend_final_source_recheck_blocks_proof_write_on_midrun_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    source_reads = iter((_source("6" * 40), _source("7" * 40)))
    stack = PipecatBackendCheckpoint(paths, source_reader=lambda: next(source_reads))
    monkeypatch.setattr(stack, "_prepare", lambda: None)
    monkeypatch.setattr(stack, "_start_app", lambda: None)
    monkeypatch.setattr(
        stack,
        "_wait_for_health",
        lambda: {"livekit_imported": False},
    )
    monkeypatch.setattr(stack, "_bootstrap", lambda _call_id: {})
    monkeypatch.setattr(stack, "_release", lambda _call_id: None)
    monkeypatch.setattr(stack, "_status", lambda _call_id: {})
    monkeypatch.setattr(stack, "_assert_backend_checkpoint", lambda *_args: None)
    stopped: list[bool] = []
    monkeypatch.setattr(stack, "_stop_app", lambda: stopped.append(True))

    with pytest.raises(StackError) as captured:
        stack.run()

    assert str(captured.value) == _SOURCE_CHANGED_ERROR
    assert not paths.proof.exists()
    assert stopped == [True]


def test_backend_clean_source_is_read_twice_and_recorded_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    source_reads: list[bool] = []

    def source_reader() -> dict[str, object]:
        source_reads.append(True)
        return _source("8" * 40)

    stack = PipecatBackendCheckpoint(paths, source_reader=source_reader)
    monkeypatch.setattr(stack, "_prepare", lambda: paths.run_dir.mkdir(parents=True))
    monkeypatch.setattr(stack, "_start_app", lambda: None)
    monkeypatch.setattr(
        stack,
        "_wait_for_health",
        lambda: {"livekit_imported": False},
    )

    def assignment(voice_call_id: str) -> dict[str, object]:
        return {
            "runtime": "pipecat_smallwebrtc_v1",
            "profile_id": "pipecat-fake-rtc-v1",
            "event_protocol": "rtvi-murmur-v2",
            "session_id": "session-test",
            "agent_id": "agent-test",
            "voice_call_id": voice_call_id,
            "ice_servers": [],
        }

    monkeypatch.setattr(stack, "_bootstrap", assignment)
    monkeypatch.setattr(stack, "_release", lambda _call_id: None)
    monkeypatch.setattr(stack, "_status", lambda _call_id: {})
    monkeypatch.setattr(stack, "_assert_backend_checkpoint", lambda *_args: None)
    monkeypatch.setattr(stack, "_stop_app", lambda: None)

    proof = stack.run()

    assert source_reads == [True, True]
    assert proof["source"] == _source("8" * 40)
    persisted = json.loads(paths.proof.read_text(encoding="utf-8"))
    assert persisted["source"] == _source("8" * 40)


def test_browser_run_orders_sanitization_validation_hashing_and_proof_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    events: list[str] = []
    browser = _browser_result()
    terminal = browser["terminal_cleanup"]
    assert isinstance(terminal, dict)

    def source_reader() -> dict[str, object]:
        events.append("source")
        return _source("9" * 40)

    stack = PipecatBrowserStack(paths, source_reader=source_reader)

    def prepare() -> None:
        events.append("prepare")
        paths.run_dir.mkdir(parents=True)

    monkeypatch.setattr(stack, "_prepare", prepare)
    monkeypatch.setattr(stack, "_prepare_web_workspace", lambda: None)
    monkeypatch.setattr(stack, "_run_step", lambda *_args: None)
    process = SimpleNamespace(ensure_running=lambda: None)
    monkeypatch.setattr(stack, "_start", lambda *_args: process)
    monkeypatch.setattr(
        stack,
        "_wait_for_app",
        lambda _app: {"livekit_imported": False},
    )
    monkeypatch.setattr(stack, "_wait_for_web", lambda _web, _app: None)
    monkeypatch.setattr(stack, "_authoritative_terminal_status", lambda _call_id: terminal)
    monkeypatch.setattr(stack, "_teardown", lambda: events.append("teardown"))
    monkeypatch.setattr(stack, "_sanitize_owned_logs", lambda: events.append("sanitize"))

    def read_browser(_path: Path) -> dict[str, object]:
        events.append("browser-read")
        return browser

    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._read_pipecat_browser_result",
        read_browser,
    )
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._validate_playwright_report",
        lambda _path: events.append("report-validate"),
    )
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._scan_qualification_artifacts",
        lambda _paths, **_kwargs: events.append("artifact-scan"),
    )
    manifest = {
        "algorithm": "sha256",
        "files": {f"safe-{index}.txt": "a" * 64 for index in range(7)},
    }
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._build_artifact_sha256_manifest",
        lambda _paths: events.append("manifest-build") or manifest,
    )
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._validate_artifact_sha256_manifest",
        lambda *_args, **_kwargs: events.append("manifest-validate"),
    )
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._validate_rtc_stack_proof",
        lambda *_args, **_kwargs: events.append("proof-validate"),
    )

    def write_proof(path: Path, value: dict[str, object]) -> None:
        events.append("proof-write")
        _atomic_write_json(path, value)

    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._atomic_write_json",
        write_proof,
    )

    result = stack.run()

    assert result["source"] == _source("9" * 40)
    assert events.count("source") == 2
    assert events.index("source") < events.index("prepare")
    sanitize_index = events.index("sanitize")
    post_sanitize_browser_index = events.index("browser-read", sanitize_index)
    post_sanitize_report_index = events.index("report-validate", sanitize_index)
    manifest_index = events.index("manifest-build")
    final_source_index = len(events) - 1 - events[::-1].index("source")
    first_rehash_index = events.index("manifest-validate")
    proof_write_index = events.index("proof-write")
    assert sanitize_index < post_sanitize_browser_index < manifest_index
    assert sanitize_index < post_sanitize_report_index < manifest_index
    assert manifest_index < final_source_index < first_rehash_index < proof_write_index
    assert events[proof_write_index + 1] == "manifest-validate"
    assert events[-3:] == ["proof-validate", "artifact-scan", "manifest-validate"]


def test_backend_and_web_environments_are_separate_and_strip_ambient_secrets(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    ambient = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "provider-secret",
        "UNLISTED_VENDOR_AUTH_TOKEN": "provider-secret",
        "UNLISTED_VENDOR_PASSWORD": "provider-password",
        "UNLISTED_VENDOR_PRIVATE_KEY": "provider-private-key",
        "UNLISTED_VENDOR_SECRET": "provider-secret",
        "UNLISTED_VENDOR_TOKEN": "provider-token",
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "ambient-project",
        "NEXT_PUBLIC_LIVEKIT_URL": "ambient-livekit",
        "FORCE_COLOR": "1",
        "HTTPS_PROXY": "proxy.example.invalid",
        "TURN_PASSWORD": "ambient-turn-password",
        "COTURN_SHARED_SECRET": "ambient-coturn-secret",
        "MURMUR_PIPECAT_E2E_TURN_URL": "turns:ambient.invalid:5349",
        "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE": "/tmp/ambient-turnserver.conf",
        "SSL_CERT_FILE": "/tmp/ambient-ca.pem",
        "SSL_CERT_DIR": "/tmp/ambient-ca-dir",
        "REQUESTS_CA_BUNDLE": "/tmp/ambient-requests.pem",
        "CURL_CA_BUNDLE": "/tmp/ambient-curl.pem",
        "OPENSSL_CONF": "/tmp/ambient-openssl.cnf",
        "OPENSSL_MODULES": "/tmp/ambient-openssl-modules",
        "SSLKEYLOGFILE": "/tmp/ambient-session-keys.log",
    }

    backend = build_environment(paths, ambient)
    web = build_web_environment(paths, ambient)

    assert backend["PYTHON_DOTENV_DISABLED"] == "1"
    assert backend["MURMUR_PIPECAT_E2E_NETWORK"] == "direct"
    assert backend["NO_PROXY"] == backend["no_proxy"] == "127.0.0.1,localhost,::1"
    assert not any("FIREBASE" in name or "LIVEKIT" in name for name in backend)
    assert "OPENAI_API_KEY" not in backend
    assert "UNLISTED_VENDOR_AUTH_TOKEN" not in backend
    assert "UNLISTED_VENDOR_PASSWORD" not in backend
    assert "UNLISTED_VENDOR_PRIVATE_KEY" not in backend
    assert "UNLISTED_VENDOR_SECRET" not in backend
    assert "UNLISTED_VENDOR_TOKEN" not in backend
    assert "TURN_PASSWORD" not in backend
    assert "COTURN_SHARED_SECRET" not in backend
    assert "MURMUR_PIPECAT_E2E_TURN_URL" not in backend
    assert "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE" not in backend
    assert "SSL_CERT_FILE" not in backend
    assert "SSL_CERT_DIR" not in backend
    assert "REQUESTS_CA_BUNDLE" not in backend
    assert "CURL_CA_BUNDLE" not in backend
    assert "OPENSSL_CONF" not in backend
    assert "OPENSSL_MODULES" not in backend
    assert "SSLKEYLOGFILE" not in backend
    assert web["NEXT_PUBLIC_FIREBASE_PROJECT_ID"] == "voice-pipecat-e2e"
    assert "NEXT_PUBLIC_LIVEKIT_URL" not in web
    assert "UNLISTED_VENDOR_AUTH_TOKEN" not in web
    assert "FORCE_COLOR" not in web
    assert web["NEXT_PUBLIC_VOICE_RUNTIME"] == "voice_v2"
    assert web["VOICE_E2E_API_URL"] == "http://127.0.0.1:8101"
    assert Path(web["VOICE_E2E_RESULT_PATH"]).is_absolute()
    assert "TURN_PASSWORD" not in web
    assert "COTURN_SHARED_SECRET" not in web
    assert "MURMUR_PIPECAT_E2E_TURN_URL" not in web
    assert "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE" not in web
    assert "SSL_CERT_FILE" not in web
    assert "SSL_CERT_DIR" not in web
    assert "REQUESTS_CA_BUNDLE" not in web
    assert "CURL_CA_BUNDLE" not in web
    assert "OPENSSL_CONF" not in web
    assert "OPENSSL_MODULES" not in web
    assert "SSLKEYLOGFILE" not in web


def test_relay_environment_accepts_only_exact_private_file_paths_and_no_raw_secret(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    coturn = _relay_material(paths)

    environment = build_environment(
        paths,
        {
            "PATH": "/usr/bin",
            "TURN_PASSWORD": "ambient-turn-password",
            "COTURN_SHARED_SECRET": "ambient-coturn-secret",
            "MURMUR_PIPECAT_E2E_TURN_URL": "turns:ambient.invalid:5349",
            "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE": "/tmp/ambient-turnserver.conf",
            "SSL_CERT_FILE": "/tmp/ambient-ca.pem",
            "SSL_CERT_DIR": "/tmp/ambient-ca-dir",
            "OPENSSL_CONF": "/tmp/ambient-openssl.cnf",
            "OPENSSL_MODULES": "/tmp/ambient-openssl-modules",
            "SSLKEYLOGFILE": "/tmp/ambient-session-keys.log",
        },
        network="relay-tls",
        turn_configuration_file=coturn.config,
        turn_tls_ca_file=coturn.cert,
    )

    assert environment["MURMUR_PIPECAT_E2E_NETWORK"] == "relay-tls"
    assert environment["MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE"] == str(coturn.config)
    assert environment["SSL_CERT_FILE"] == str(coturn.cert)
    assert "TURN_PASSWORD" not in environment
    assert "COTURN_SHARED_SECRET" not in environment
    assert "MURMUR_PIPECAT_E2E_TURN_URL" not in environment
    assert "SSL_CERT_DIR" not in environment
    assert "OPENSSL_CONF" not in environment
    assert "OPENSSL_MODULES" not in environment
    assert "SSLKEYLOGFILE" not in environment
    assert STATIC_TURN_SECRET not in repr(environment)


def test_environment_modes_reject_cross_mode_or_incomplete_relay_material(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    coturn = _relay_material(paths)

    with pytest.raises(StackError, match="direct Pipecat E2E does not accept relay material"):
        build_environment(paths, turn_configuration_file=coturn.config)
    with pytest.raises(StackError, match="relay-tls Pipecat E2E material is unavailable"):
        build_environment(paths, network="relay-tls")
    with pytest.raises(StackError, match="relay-tls Pipecat E2E material is unavailable"):
        build_environment(
            paths,
            network="relay-tls",
            turn_configuration_file=coturn.config,
            turn_tls_ca_file=coturn.config,
        )


def test_default_and_explicit_direct_cli_are_shape_compatible(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proof = {"schema_version": 1, "status": "synthetic-direct"}
    constructions: list[tuple[StackPaths, dict[str, object]]] = []

    class _DirectStack:
        def __init__(self, paths: StackPaths, **kwargs: object) -> None:
            constructions.append((paths, kwargs))

        def run(self) -> dict[str, object]:
            return proof

    monkeypatch.setattr(stack_module, "PipecatBrowserStack", _DirectStack)
    monkeypatch.setattr(stack_module, "_new_run_id", lambda: "rtc-direct-contract")

    assert main([]) == 0
    default_output = capsys.readouterr()
    assert main(["--network", "direct"]) == 0
    explicit_output = capsys.readouterr()

    assert default_output.err == explicit_output.err == ""
    assert default_output.out == explicit_output.out == json.dumps(proof, sort_keys=True) + "\n"
    assert len(constructions) == 2
    assert constructions[0] == constructions[1]
    assert _parse_args([]).network == _parse_args(["--network", "direct"]).network == "direct"


@pytest.mark.parametrize("extra", [(), ("--backend-only",)])
def test_relay_cli_refuses_before_paths_artifacts_or_any_runtime_owner(
    extra: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("relay contract-only refusal ran a side effect")

    monkeypatch.setattr(stack_module, "make_paths", forbidden)
    monkeypatch.setattr(stack_module, "read_private_coturn_configuration", forbidden)
    monkeypatch.setattr(stack_module, "PipecatBrowserStack", forbidden)
    monkeypatch.setattr(stack_module, "PipecatBackendCheckpoint", forbidden)

    assert main(("--network", "relay-tls", "--run-id", "never-created", *extra)) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (f"Pipecat RTC qualification failed: {_RELAY_TLS_CONTRACT_ONLY_ERROR}\n")
    assert "passed" not in captured.err
    assert "allocation" not in captured.err
    assert "bytes" not in captured.err


def test_direct_script_entrypoint_imports_contract_before_fixed_relay_refusal() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/voice_pipecat_e2e_stack.py",
            "--network",
            "relay-tls",
            "--run-id",
            "never-created-by-script-entrypoint",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Pipecat RTC qualification failed: {_RELAY_TLS_CONTRACT_ONLY_ERROR}\n"
    )
    assert not (
        PROJECT_ROOT / "var" / "voice-pipecat-e2e" / "never-created-by-script-entrypoint"
    ).exists()


def test_standard_ice_urls_are_forbidden_artifact_data_but_mode_label_is_safe() -> None:
    for value in (
        "stun:127.0.0.1:3478",
        "stuns:127.0.0.1:5349",
        "turn:127.0.0.1:3478?transport=udp",
        "turns:127.0.0.1:5349?transport=tcp",
    ):
        assert "raw ICE server URL" in _service_secret_findings(value)
        assert "network URL" in _browser_secret_findings(value)
        assert value not in _sanitize_sensitive_text(f"unsafe {value}\n")
    assert _service_secret_findings("network: relay-tls") == set()
    assert _browser_secret_findings("network: relay-tls") == set()


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


def test_primary_failure_survives_teardown_failure_and_still_sanitizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    stack = PipecatBrowserStack(paths, source_reader=_source)
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

    with pytest.raises(StackError, match="synthetic setup failure") as captured:
        stack.run()

    assert str(captured.value) == (f"synthetic setup failure\n{_TEARDOWN_FAILURE_CLASSIFICATION}")
    assert captured.value.__cause__ is None
    assert stopped == ["failing", "healthy"]
    assert sanitized == [True]


@pytest.mark.parametrize("teardown_fails", [False, True])
def test_finalizer_failures_are_fixed_and_fatal_when_browser_body_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    teardown_fails: bool,
) -> None:
    paths = _paths(tmp_path)
    browser = _browser_result()
    terminal = browser["terminal_cleanup"]
    assert isinstance(terminal, dict)
    stack = PipecatBrowserStack(paths, source_reader=_source)
    monkeypatch.setattr(stack, "_prepare", lambda: paths.run_dir.mkdir(parents=True))
    monkeypatch.setattr(stack, "_prepare_web_workspace", lambda: None)
    monkeypatch.setattr(stack, "_run_step", lambda *_args: None)
    process = SimpleNamespace(ensure_running=lambda: None)
    monkeypatch.setattr(stack, "_start", lambda *_args: process)
    monkeypatch.setattr(
        stack,
        "_wait_for_app",
        lambda _app: {"livekit_imported": False},
    )
    monkeypatch.setattr(stack, "_wait_for_web", lambda _web, _app: None)
    monkeypatch.setattr(stack, "_authoritative_terminal_status", lambda _call_id: terminal)
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._read_pipecat_browser_result",
        lambda _path: browser,
    )
    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._validate_playwright_report",
        lambda _path: None,
    )
    finalizers: list[str] = []

    def teardown() -> None:
        finalizers.append("teardown")
        if teardown_fails:
            raise StackError("synthetic teardown failure")

    def sanitize() -> None:
        finalizers.append("sanitize")
        raise StackError("synthetic sanitizer failure")

    monkeypatch.setattr(stack, "_teardown", teardown)
    monkeypatch.setattr(stack, "_sanitize_owned_logs", sanitize)

    with pytest.raises(StackError) as captured:
        stack.run()

    expected = [_ARTIFACT_SAFETY_FAILURE_CLASSIFICATION]
    if teardown_fails:
        expected.insert(0, _TEARDOWN_FAILURE_CLASSIFICATION)
    assert str(captured.value) == "\n".join(expected)
    assert "synthetic" not in str(captured.value)
    assert finalizers == ["teardown", "sanitize"]


def test_timeout_capsule_is_classified_before_whole_file_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    stack = PipecatBrowserStack(paths, source_reader=_source)
    capsule = _validate_proof_timeout_progress_capsule(_proof_timeout_progress_capsule())
    playwright_log = paths.run_dir / "playwright.log"
    events: list[str] = []

    def prepare() -> None:
        paths.run_dir.mkdir(parents=True)

    def fail_browser_step() -> None:
        playwright_log.write_text(
            f"Error: {capsule}\nnormal report URL https://secret.invalid/path\n",
            encoding="utf-8",
        )
        stack._owned_logs.append(playwright_log)
        raise StackError("synthetic Playwright process failure")

    original_classify = stack._classify_primary_failure
    original_sanitize = stack._sanitize_owned_logs

    def classify(failure: BaseException) -> BaseException:
        events.append("classify")
        return original_classify(failure)

    def teardown() -> None:
        events.append("teardown")

    def sanitize() -> None:
        events.append("sanitize")
        original_sanitize()

    monkeypatch.setattr(stack, "_prepare", prepare)
    monkeypatch.setattr(stack, "_prepare_web_workspace", fail_browser_step)
    monkeypatch.setattr(stack, "_classify_primary_failure", classify)
    monkeypatch.setattr(stack, "_teardown", teardown)
    monkeypatch.setattr(stack, "_sanitize_owned_logs", sanitize)

    with pytest.raises(StackError) as captured:
        stack.run()

    assert str(captured.value) == (
        f"primary=proof_wait_timeout\n{capsule}\n{_ARTIFACT_SAFETY_FAILURE_CLASSIFICATION}"
    )
    assert captured.value.__cause__ is None
    assert "synthetic" not in str(captured.value)
    assert events == ["classify", "teardown", "sanitize"]
    assert playwright_log.read_text(encoding="utf-8") == (
        "[qualification artifact redacted after forbidden signaling data]\n"
    )


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
    ("field", "failure_value"),
    [
        ("failure_message_sequence", 1),
        ("expected_block_start_frame", 128),
        ("observed_block_start_frame", 256),
        ("frame_delta_frames", 128),
        ("last_observed_block_start_frame", 0),
        ("context_state_at_message_delivery", "running"),
    ],
)
def test_browser_result_requires_null_audio_clock_fault_diagnostics(
    tmp_path: Path,
    field: str,
    failure_value: object,
) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    clock = browser["audio_sample_clock"]
    assert isinstance(clock, dict)
    evidence = clock["evidence"]
    assert isinstance(evidence, dict)
    local = evidence["local"]
    assert isinstance(local, dict)
    local[field] = failure_value

    _write_json(path, value)
    with pytest.raises(StackError, match="probe is not clean and exact"):
        _read_pipecat_browser_result(path)


@pytest.mark.parametrize(
    ("logical_frame", "catch_up_frame"),
    [
        (50_304, 50_432),  # Two corrected blocks.
        (58_240, 58_368),  # A bounded 64-block plateau in the same episode.
    ],
)
def test_browser_result_accepts_one_settled_stale_frame_plateau(
    tmp_path: Path,
    logical_frame: int,
    catch_up_frame: int,
) -> None:
    path = tmp_path / "result.json"
    value = _browser_result()
    browser = value["browser_evidence"]
    assert isinstance(browser, dict)
    clock = browser["audio_sample_clock"]
    assert isinstance(clock, dict)
    evidence = clock["evidence"]
    assert isinstance(evidence, dict)
    local = evidence["local"]
    assert isinstance(local, dict)
    local.update(
        {
            "stale_frame_correction_count": 1,
            "last_stale_observed_block_start_frame": 50_048,
            "last_stale_logical_block_start_frame": logical_frame,
            "stale_frame_catch_up_observed_block_start_frame": catch_up_frame,
        }
    )

    _write_json(path, value)
    assert _read_pipecat_browser_result(path) == value


@pytest.mark.parametrize(
    "mutation",
    [
        "boolean_count",
        "too_many_corrections",
        "zero_with_frame",
        "one_without_frame",
        "unaligned_frames",
        "wrong_plateau_order",
        "wrong_catch_up_mapping",
        "pending_correction",
        "correction_before_timeline",
        "correction_after_timeline",
    ],
)
def test_browser_result_rejects_invalid_stale_frame_correction(
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
    assert isinstance(evidence, dict)
    local = evidence["local"]
    assert isinstance(local, dict)

    if mutation == "boolean_count":
        local["stale_frame_correction_count"] = True
    elif mutation == "too_many_corrections":
        local.update(
            {
                "stale_frame_correction_count": 2,
                "last_stale_observed_block_start_frame": 50_048,
                "last_stale_logical_block_start_frame": 50_304,
                "stale_frame_catch_up_observed_block_start_frame": 50_432,
            }
        )
    elif mutation == "zero_with_frame":
        local["stale_frame_catch_up_observed_block_start_frame"] = 50_432
    elif mutation == "one_without_frame":
        local["stale_frame_correction_count"] = 1
        local["last_stale_observed_block_start_frame"] = 50_048
    elif mutation == "unaligned_frames":
        local.update(
            {
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 50_049,
                "last_stale_logical_block_start_frame": 50_305,
                "stale_frame_catch_up_observed_block_start_frame": 50_433,
            }
        )
    elif mutation == "wrong_plateau_order":
        local.update(
            {
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 50_304,
                "last_stale_logical_block_start_frame": 50_048,
                "stale_frame_catch_up_observed_block_start_frame": 50_176,
            }
        )
    elif mutation == "wrong_catch_up_mapping":
        local.update(
            {
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 50_048,
                "last_stale_logical_block_start_frame": 50_304,
                "stale_frame_catch_up_observed_block_start_frame": 50_560,
            }
        )
    elif mutation == "pending_correction":
        local.update(
            {
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 50_048,
                "last_stale_logical_block_start_frame": 50_304,
                "stale_frame_catch_up_observed_block_start_frame": 50_432,
                "stale_frame_correction_pending": True,
            }
        )
    elif mutation == "correction_before_timeline":
        transitions = local["transitions"]
        assert isinstance(transitions, list)
        first_transition = transitions[0]
        assert isinstance(first_transition, dict)
        first_transition.update({"block_start_frame": 128, "block_end_frame": 256})
        local.update(
            {
                "processed_block_count": 820,
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 0,
                "last_stale_logical_block_start_frame": 256,
                "stale_frame_catch_up_observed_block_start_frame": 384,
            }
        )
    elif mutation == "correction_after_timeline":
        local.update(
            {
                "stale_frame_correction_count": 1,
                "last_stale_observed_block_start_frame": 104_704,
                "last_stale_logical_block_start_frame": 104_960,
                "stale_frame_catch_up_observed_block_start_frame": 105_088,
            }
        )
    else:  # pragma: no cover - the parameter list is exhaustive
        raise AssertionError(f"unknown mutation: {mutation}")

    _write_json(path, value)
    with pytest.raises(StackError, match="stale-frame correction"):
        _read_pipecat_browser_result(path)


@pytest.mark.parametrize("mutation", ["missing_key", "extra_key"])
def test_browser_result_requires_exact_stale_plateau_schema(
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
    assert isinstance(evidence, dict)
    local = evidence["local"]
    assert isinstance(local, dict)
    if mutation == "missing_key":
        local.pop("stale_frame_catch_up_observed_block_start_frame")
    else:
        local["second_stale_frame_episode"] = None

    _write_json(path, value)
    with pytest.raises(StackError, match="probe schema is invalid"):
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


def test_artifact_manifest_binds_exact_post_sanitization_text_inputs_and_proof(
    tmp_path: Path,
) -> None:
    paths = _repo_paths(tmp_path)
    _write_safe_qualification_artifacts(paths)
    paths.server_log.write_text(
        "Discarding peer SmallWebRTCConnection#raw-peer\n",
        encoding="utf-8",
    )

    assert _sanitize_log_file(paths.server_log) == {"raw SmallWebRTC peer ID"}
    _scan_qualification_artifacts(paths)
    manifest = _build_artifact_sha256_manifest(paths, repository_root=tmp_path)
    expected_files = {
        path.resolve().relative_to(tmp_path.resolve()).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in _qualification_text_artifact_paths(paths)
    }
    expected_files = dict(sorted(expected_files.items()))
    assert list(expected_files) == [
        "var/evals/voice-pipecat-e2e-rtc-test.jsonl",
        "var/voice-pipecat-e2e/rtc-test/pipecat-asgi.log",
        "var/voice-pipecat-e2e/rtc-test/playwright.log",
        "var/voice-pipecat-e2e/rtc-test/playwright/report.json",
        "var/voice-pipecat-e2e/rtc-test/playwright/voice-pipecat-rtc-result.json",
        "var/voice-pipecat-e2e/rtc-test/web-build.log",
        "var/voice-pipecat-e2e/rtc-test/web.log",
    ]
    assert manifest == {"algorithm": "sha256", "files": expected_files}
    assert all(Path(label).is_absolute() is False for label in expected_files)
    assert all(".." not in Path(label).parts for label in expected_files)
    assert not any(
        label.endswith(("rtc-stack-proof.json", "murmur.db", "murmur.db-wal", "murmur.db-shm"))
        for label in expected_files
    )
    assert "SmallWebRTCConnection#" not in paths.server_log.read_text(encoding="utf-8")

    browser = _browser_result()
    terminal = browser["terminal_cleanup"]
    assert isinstance(terminal, dict)
    stack = PipecatBrowserStack(paths)
    proof = stack._build_proof(
        browser,
        {"livekit_imported": False},
        terminal,
        _source(),
        manifest,
    )
    _validate_rtc_stack_proof(
        paths,
        proof,
        _source(),
        manifest,
        repository_root=tmp_path,
    )

    safety = proof["artifact_safety"]
    assert isinstance(safety, dict)
    assert proof["source"] == _source()
    assert safety["text_files_scanned"] == list(expected_files)
    assert safety["sha256_manifest"] == manifest
    assert "files" not in safety


def test_artifact_manifest_rejects_same_size_tamper_after_hashing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_safe_qualification_artifacts(paths)
    manifest = _build_artifact_sha256_manifest(paths, repository_root=tmp_path)
    web_log = paths.run_dir / "web.log"
    original = web_log.read_bytes()
    web_log.write_bytes(b"X" + original[1:])
    assert web_log.stat().st_size == len(original)

    with pytest.raises(StackError) as captured:
        _validate_artifact_sha256_manifest(
            paths,
            manifest,
            repository_root=tmp_path,
        )

    assert str(captured.value) == _ARTIFACT_TAMPER_ERROR


@pytest.mark.parametrize("malformed", [None, [], {"algorithm": "sha256"}])
def test_artifact_manifest_validation_is_fail_closed(
    tmp_path: Path,
    malformed: object,
) -> None:
    paths = _paths(tmp_path)
    _write_safe_qualification_artifacts(paths)

    with pytest.raises(StackError) as captured:
        _validate_artifact_sha256_manifest(
            paths,
            malformed,
            repository_root=tmp_path,
        )

    assert str(captured.value) == _ARTIFACT_MANIFEST_ERROR


def test_persisted_proof_corruption_is_rejected_and_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _write_safe_qualification_artifacts(paths)
    manifest = _build_artifact_sha256_manifest(paths, repository_root=tmp_path)
    browser = _browser_result()
    terminal = browser["terminal_cleanup"]
    assert isinstance(terminal, dict)
    proof = PipecatBrowserStack(paths)._build_proof(
        browser,
        {"livekit_imported": False},
        terminal,
        _source(),
        manifest,
    )

    def corrupt_write(path: Path, value: dict[str, object]) -> None:
        corrupted = dict(value)
        corrupted["status"] = "tampered"
        path.write_text(
            json.dumps(corrupted, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "scripts.voice_pipecat_e2e_stack._atomic_write_json",
        corrupt_write,
    )

    with pytest.raises(StackError, match="persisted RTC stack proof is not canonical"):
        _write_validated_rtc_stack_proof(
            paths,
            proof,
            _source(),
            manifest,
            repository_root=tmp_path,
        )

    assert not paths.rtc_proof.exists()


@pytest.mark.parametrize("missing_index", range(7))
def test_artifact_manifest_rejects_each_missing_required_text_input(
    tmp_path: Path,
    missing_index: int,
) -> None:
    paths = _paths(tmp_path)
    _write_safe_qualification_artifacts(paths)
    _qualification_text_artifact_paths(paths)[missing_index].unlink()

    with pytest.raises(StackError) as captured:
        _build_artifact_sha256_manifest(paths, repository_root=tmp_path)

    assert str(captured.value) == _ARTIFACT_MANIFEST_ERROR


@pytest.mark.parametrize("symlink_kind", ["file", "parent"])
def test_artifact_manifest_rejects_symlinked_artifact_paths(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    if symlink_kind == "file":
        paths = _paths(tmp_path)
        _write_safe_qualification_artifacts(paths)
        web_log = paths.run_dir / "web.log"
        target = paths.run_dir / "web-target.log"
        web_log.replace(target)
        web_log.symlink_to(target)
    else:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        alias_parent = tmp_path / "alias-parent"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        paths = _paths(alias_parent)
        _write_safe_qualification_artifacts(paths)

    with pytest.raises(StackError) as captured:
        _build_artifact_sha256_manifest(paths, repository_root=tmp_path)

    assert str(captured.value) == _ARTIFACT_MANIFEST_ERROR


@pytest.mark.parametrize(
    "mutation",
    [
        "source_extra",
        "source_short_sha",
        "source_bool",
        "legacy_files_key",
        "manifest_hash",
        "schema_bool",
        "topology",
        "topology_peer_bool",
        "browser",
        "terminal",
        "limitations",
    ],
)
def test_rtc_stack_proof_validator_rejects_schema_or_value_tampering(
    tmp_path: Path,
    mutation: str,
) -> None:
    paths = _paths(tmp_path)
    _write_safe_qualification_artifacts(paths)
    manifest = _build_artifact_sha256_manifest(paths, repository_root=tmp_path)
    browser = _browser_result()
    terminal = browser["terminal_cleanup"]
    assert isinstance(terminal, dict)
    proof = PipecatBrowserStack(paths)._build_proof(
        browser,
        {"livekit_imported": False},
        terminal,
        _source(),
        manifest,
    )
    mutated = json.loads(json.dumps(proof))
    source = mutated["source"]
    safety = mutated["artifact_safety"]
    assert isinstance(source, dict)
    assert isinstance(safety, dict)

    if mutation == "source_extra":
        source["extra"] = None
    elif mutation == "source_short_sha":
        source["commit_sha"] = "abc"
    elif mutation == "source_bool":
        source["repository_clean"] = 1
    elif mutation == "legacy_files_key":
        safety["files"] = safety.pop("text_files_scanned")
    elif mutation == "manifest_hash":
        proof_manifest = safety["sha256_manifest"]
        assert isinstance(proof_manifest, dict)
        files = proof_manifest["files"]
        assert isinstance(files, dict)
        first_label = next(iter(files))
        files[first_label] = "0" * 64
    elif mutation == "schema_bool":
        mutated["schema_version"] = True
    elif mutation == "topology":
        topology = mutated["topology"]
        assert isinstance(topology, dict)
        topology["docker_used"] = True
    elif mutation == "topology_peer_bool":
        topology = mutated["topology"]
        assert isinstance(topology, dict)
        topology["smallwebrtc_peer_count"] = True
    elif mutation == "browser":
        proof_browser = mutated["browser"]
        assert isinstance(proof_browser, dict)
        proof_browser["trace_id"] = "tampered-trace"
    elif mutation == "terminal":
        proof_terminal = mutated["terminal_cleanup"]
        assert isinstance(proof_terminal, dict)
        proof_terminal["status"] = "pending"
    elif mutation == "limitations":
        mutated["limitations"] = []
    else:  # pragma: no cover - exhaustive parameter list
        raise AssertionError(mutation)

    with pytest.raises(StackError):
        _validate_rtc_stack_proof(
            paths,
            mutated,
            _source(),
            manifest,
            repository_root=tmp_path,
        )

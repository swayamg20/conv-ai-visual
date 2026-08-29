"""Synthetic ownership tests for relay browser artifact consumption."""
# ruff: noqa: E402

from __future__ import annotations

import inspect
import json
import os
import pickle
import socket
import stat
import sys
import threading
from collections import deque
from contextlib import contextmanager
from copy import copy, deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_artifact_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_artifact_files as artifact_files_module
import scripts.voice_pipecat_e2e_relay_artifact_owner as artifact_owner_module
import scripts.voice_pipecat_e2e_relay_artifact_remove as artifact_remove_module
import scripts.voice_pipecat_e2e_relay_browser_result as browser_result_module
from scripts.voice_pipecat_e2e_coturn_tls import cleanup_tls_material_generation_slot
from scripts.voice_pipecat_e2e_relay_browser_contract import (
    validate_relay_browser_artifacts,
)
from scripts.voice_pipecat_e2e_relay_browser_result import (
    RelayBrowserObservation,
    RelayBrowserResultCleanupRequired,
    RelayBrowserResultError,
    RelayBrowserResultOwner,
    cleanup_relay_browser_result_owner,
    consume_relay_browser_result,
    new_relay_browser_result_owner,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    authorize_relay_backend,
    authorize_relay_browser,
    new_relay_probe_run,
)
from scripts.voice_pipecat_e2e_stack import E2E_SESSION_ID, VOICE_PROFILE_ID, VOICE_RUNTIME
from tests.test_voice_pipecat_e2e_relay_probe import _ready_runtime, _source
from tests.test_voice_pipecat_e2e_stack import _audio_sample_clock

RAW_SENTINEL = "relay-raw-result-sentinel"
REPORT_SENTINEL = "relay-raw-report-sentinel"
SOURCE_SENTINEL = "a" * 40


@pytest.fixture
def authorized_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths, material, running, readiness, _ = _ready_runtime(tmp_path)
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    authorize_relay_backend(run, tls_material=material)
    authorize_relay_browser(
        run,
        running=running,
        tls_material=material,
        readiness=readiness,
    )
    yield run, paths, material
    if material._slot.has_material:
        cleanup_tls_material_generation_slot(material._slot)


def _valid_result(call_id: str) -> dict[str, object]:
    local_track = "local-track-relay-1"
    remote_track = "remote-track-relay-1"
    return {
        "schema_version": 1,
        "status": "passed",
        "completed_at": "2026-08-17T00:00:00.000Z",
        "runtime": VOICE_RUNTIME,
        "profile_id": VOICE_PROFILE_ID,
        "peer_reservation_id": "peer-reservation-relay-1",
        "voice_call_id": call_id,
        "trace_id": RAW_SENTINEL,
        "browser_evidence": {
            "relay_policy_attested": True,
            "tls_spki_pin_attested": True,
            "gateway_attested": True,
            "exact_local_track_id": local_track,
            "exact_remote_track_id": remote_track,
            "connection_gestures": [
                {"sequence": 1, "action": "prepare"},
                {"sequence": 2, "action": "activate"},
            ],
            "pre_ready_microphone_track": {
                "observed_at_ms": 10,
                "enabled_at_observation": False,
                "ready_state_at_observation": "live",
                "first_agent_ready_observed_at_ms": 20,
            },
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
                    "candidate_type": "relay",
                    "protocol": "udp",
                    "relay_protocol": "tls",
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
            "local_peak_rms": 0.1,
            "remote_peak_rms": 0.2,
            "audio_sample_clock": _audio_sample_clock(local_track, remote_track),
            "first_user_pcm_region_start_ms": 100,
            "second_user_pcm_region_start_ms": 1_000,
            "remote_pcm_silence_attribution_start_ms": 1_100,
            "sustained_pcm_silence_ms": 200,
            "no_stale_audio_guard_start_ms": 1_300,
            "no_stale_audio_guard_end_ms": 1_500,
            "remote_attribution_tolerance_ms": 100,
            "first_turn_id": "turn-relay-1",
            "second_turn_id": "turn-relay-2",
            "interrupted_speech_id": "speech-relay-1",
            "second_reply_completed_speech_id": "speech-relay-2",
            "canonical_event_count": 12,
        },
        "browser_cleanup_observed": True,
        "terminal_cleanup": {
            "schema_version": 1,
            "status": "pending",
            "runtime": VOICE_RUNTIME,
            "profile_id": VOICE_PROFILE_ID,
            "session_id": E2E_SESSION_ID,
            "voice_call_id": call_id,
            "reservation": {
                "state": "terminal",
                "cleanup_complete": True,
                "terminal_reason": "user_ended",
                "retryable": False,
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
        },
    }


def _safe_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "passed",
        "spec_id": "voice-pipecat-rtc-relay-tls",
        "pass_counts": {"tests_discovered": 1, "tests_passed": 1},
        "retention_policy": {
            "rich_reporters_disabled": True,
            "media_capture_disabled": True,
            "reporter_stdio_disabled": True,
            "runner_cleanup_required": True,
        },
    }


def _write_private_json(path: Path, value: object) -> None:
    encoded = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        assert os.write(descriptor, encoded) == len(encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _write_valid_artifacts(run: object) -> tuple[Path, Path]:
    paths = run._stack_paths  # type: ignore[attr-defined]
    _write_private_json(paths.browser_result, _valid_result(run._call_id))  # type: ignore[attr-defined]
    _write_private_json(paths.playwright_report, _safe_report())
    return paths.browser_result, paths.playwright_report


def _source_line(function: object, marker: str, *, after: bool = False) -> int:
    lines, first = inspect.getsourcelines(function)
    matches = [index for index, line in enumerate(lines) if marker in line]
    assert len(matches) == 1, (marker, matches)
    return first + matches[0] + int(after)


@contextmanager
def _control_at_line(
    function: object,
    line: int,
    error: KeyboardInterrupt | SystemExit,
) -> Iterator[list[bool]]:
    target = function.__code__  # type: ignore[attr-defined]
    previous = sys.gettrace()
    injected = [False]

    def trace(frame: object, event: str, _argument: object) -> object:
        if (
            not injected[0]
            and event == "line"
            and frame.f_code is target  # type: ignore[attr-defined]
            and frame.f_lineno == line  # type: ignore[attr-defined]
        ):
            injected[0] = True
            sys.settrace(None)
            raise error
        return trace

    sys.settrace(trace)
    try:
        yield injected
    finally:
        sys.settrace(previous)


def _traceback_contains(error: BaseException, *secrets: str | bytes) -> bool:
    needles = tuple(item if isinstance(item, bytes) else item.encode() for item in secrets)
    frame = error.__traceback__
    while frame is not None:
        if "/tests/" not in frame.tb_frame.f_code.co_filename:
            for value in tuple(frame.tb_frame.f_locals.values()):
                if _contains(value, needles, set(), 0):
                    return True
        frame = frame.tb_next
    return False


def _contains(
    value: object,
    needles: tuple[bytes, ...],
    seen: set[int],
    depth: int,
) -> bool:
    if depth > 14 or len(seen) > 4_096 or id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, bytes):
        return any(item in value for item in needles)
    if isinstance(value, str | os.PathLike):
        try:
            raw = os.fspath(value).encode()
        except TypeError:
            return False
        return any(item in raw for item in needles)
    if isinstance(value, dict):
        return any(
            _contains(item, needles, seen, depth + 1) for pair in value.items() for item in pair
        )
    if isinstance(value, (deque, list, tuple, set, frozenset)):
        return any(_contains(item, needles, seen, depth + 1) for item in value)
    if type(value).__module__.startswith("scripts.voice_pipecat_e2e_relay"):
        slots = getattr(type(value), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        return any(
            _contains(getattr(value, name), needles, seen, depth + 1)
            for name in slots
            if isinstance(name, str) and hasattr(value, name)
        )
    return False


def test_valid_result_is_deleted_before_boolean_only_observation(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    assert type(owner) is RelayBrowserResultOwner and owner.ready
    assert stat.S_IMODE(paths.contract.run_dir.joinpath("playwright").stat().st_mode) == 0o700
    assert (
        stat.S_IMODE(
            paths.contract.run_dir.joinpath("playwright/relay-ephemeral-output").stat().st_mode
        )
        == 0o700
    )
    result_path, report_path = _write_valid_artifacts(run)
    ephemeral = result_path.parent / "relay-ephemeral-output"
    nested = ephemeral / "playwright-results"
    nested.mkdir(mode=0o755)
    assert stat.S_IMODE(nested.stat().st_mode) == 0o755
    nested.joinpath("browser.tmp").write_bytes(b"bounded-ephemeral")
    paths.contract.run_dir.joinpath("playwright.log").write_text("bounded log\n")

    observation = consume_relay_browser_result(run, owner)

    assert type(observation) is RelayBrowserObservation
    assert not observation and owner.published
    assert consume_relay_browser_result(run, owner) is observation
    assert set(observation.__slots__) == {
        "artifacts_deleted",
        "browser_cleanup_attested",
        "hidden_call_attested",
        "qualification_verified",
        "relay_candidate_attested",
        "result_schema_attested",
        "safe_report_attested",
        "terminal_cleanup_attested",
    }
    assert all(type(getattr(observation, name)) is bool for name in observation.__slots__)
    assert observation.qualification_verified is False
    assert not result_path.exists() and not report_path.exists()
    assert not result_path.parent.exists()
    assert not paths.contract.run_dir.joinpath("playwright.log").exists()


def test_cleanup_only_revokes_publication_and_deletes_unconsumed_tree(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    output = paths.contract.run_dir / "playwright/relay-ephemeral-output"
    output.joinpath("unconsumed.bin").write_bytes(b"unconsumed")

    cleanup_relay_browser_result_owner(run)
    cleanup_relay_browser_result_owner(run)

    assert not (paths.contract.run_dir / "playwright").exists()
    assert not owner.published and not owner.ready
    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(status="failed"),
        lambda value: value.update(voice_call_id="00000000-0000-4000-8000-000000000000"),
        lambda value: value["browser_evidence"]["selected_candidate_pair"]["local"].update(  # type: ignore[index]
            candidate_type="host"
        ),
        lambda value: value["terminal_cleanup"].update(status="passed"),  # type: ignore[union-attr]
        lambda value: value.update(extra="forbidden"),
    ],
)
def test_invalid_rich_contract_is_erased_without_observation(
    authorized_run: tuple[object, ...],
    mutate: object,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result = _valid_result(run._call_id)
    mutate(result)  # type: ignore[operator]
    _write_private_json(run._stack_paths.browser_result, result)
    _write_private_json(run._stack_paths.playwright_report, _safe_report())

    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert not owner.published and not (paths.contract.run_dir / "playwright").exists()


def test_duplicate_json_key_is_rejected_and_erased(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result = json.dumps(_valid_result(run._call_id), separators=(",", ":"))
    duplicate = result.replace('"status":"passed"', '"status":"failed","status":"passed"')
    descriptor = os.open(
        run._stack_paths.browser_result,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        assert os.write(descriptor, duplicate.encode()) == len(duplicate.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _write_private_json(run._stack_paths.playwright_report, _safe_report())

    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert not owner.published and not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize(
    "case",
    [
        "peer-bool",
        "gesture-bool",
        "request-bool",
        "terminal-schema-bool",
        "tts-cancelled-bool",
        "safe-schema-float",
        "safe-count-bool",
        "audio-schema-bool",
    ],
)
def test_numeric_contract_fields_reject_boolean_and_float_aliases(case: str) -> None:
    call_id = "10000000-0000-4000-8000-000000000001"
    result = _valid_result(call_id)
    report = _safe_report()
    browser = result["browser_evidence"]
    terminal = result["terminal_cleanup"]
    assert isinstance(browser, dict) and isinstance(terminal, dict)
    if case == "peer-bool":
        browser["peer_connection_count"] = True
    elif case == "gesture-bool":
        gestures = browser["connection_gestures"]
        assert isinstance(gestures, list) and isinstance(gestures[0], dict)
        gestures[0]["sequence"] = True
    elif case == "request-bool":
        counts = browser["signaling_request_counts"]
        assert isinstance(counts, dict)
        counts["post"] = True
    elif case == "terminal-schema-bool":
        terminal["schema_version"] = True
    elif case == "tts-cancelled-bool":
        media = terminal["fake_media"]
        assert isinstance(media, dict)
        media["tts_cancelled_count"] = True
    elif case == "safe-schema-float":
        report["schema_version"] = 1.0
    elif case == "safe-count-bool":
        counts = report["pass_counts"]
        assert isinstance(counts, dict)
        counts["tests_passed"] = True
    else:
        clock = browser["audio_sample_clock"]
        assert isinstance(clock, dict) and isinstance(clock["evidence"], dict)
        clock["evidence"]["schema_version"] = True

    raw_result = json.dumps(result, separators=(",", ":")).encode()
    raw_report = json.dumps(report, separators=(",", ":")).encode()
    assert not validate_relay_browser_artifacts(raw_result, raw_report, call_id)


@pytest.mark.parametrize("unexpected", ["symlink", "fifo", "socket"])
def test_unexpected_ephemeral_entry_is_invalid_but_exact_entry_is_removed(
    authorized_run: tuple[object, ...],
    tmp_path: Path,
    unexpected: str,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    output = paths.contract.run_dir / "playwright/relay-ephemeral-output"
    target = tmp_path / "outside-sentinel"
    target.write_text("must survive")
    entry = output / "unexpected"
    if unexpected == "symlink":
        entry.symlink_to(target)
    elif unexpected == "fifo":
        os.mkfifo(entry, 0o600)
    else:
        with TemporaryDirectory(prefix="relay-socket-", dir="/tmp") as short_root:
            short_entry = Path(short_root) / "entry"
            endpoint = socket.socket(socket.AF_UNIX)
            try:
                endpoint.bind(os.fspath(short_entry))
            finally:
                endpoint.close()
            short_entry.rename(entry)

    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert target.read_text() == "must survive"
    assert not entry.exists() and not (paths.contract.run_dir / "playwright").exists()


def test_writable_nested_directory_is_invalid_but_confined_tree_is_removed(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    nested = paths.contract.run_dir / "playwright/relay-ephemeral-output/writable"
    nested.mkdir(mode=0o777)
    nested.chmod(0o777)
    nested.joinpath("untrusted.tmp").write_bytes(b"untrusted")

    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert not owner.published and not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize("artifact", ["result-symlink", "report-fifo", "result-mode"])
def test_result_and_report_require_exact_private_regular_files(
    authorized_run: tuple[object, ...],
    tmp_path: Path,
    artifact: str,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    outside = tmp_path / "outside-json"
    _write_private_json(outside, _valid_result(run._call_id))
    if artifact == "result-symlink":
        run._stack_paths.browser_result.symlink_to(outside)
        _write_private_json(run._stack_paths.playwright_report, _safe_report())
    elif artifact == "report-fifo":
        _write_private_json(run._stack_paths.browser_result, _valid_result(run._call_id))
        os.mkfifo(run._stack_paths.playwright_report, 0o600)
    else:
        _write_valid_artifacts(run)
        run._stack_paths.browser_result.chmod(0o640)

    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert outside.exists() and not owner.published
    assert not (paths.contract.run_dir / "playwright").exists()


def test_preexisting_artifact_root_is_rejected_and_never_deleted(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    preexisting = paths.contract.run_dir / "playwright"
    preexisting.mkdir(mode=0o700)
    marker = preexisting / "preexisting"
    marker.write_text("retain")

    with pytest.raises(RelayBrowserResultError, match=r"owner is unavailable$"):
        new_relay_browser_result_owner(run)

    assert marker.read_text() == "retain"


@pytest.mark.parametrize(
    "phase",
    [
        "run-open-returned",
        "playwright-mkdir-returned",
        "playwright-open-returned",
        "ephemeral-mkdir-returned",
        "ephemeral-open-returned",
    ],
)
def test_prepare_controls_settle_owned_partial_state_without_traceback_graph(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    run, paths, _ = authorized_run
    fired = False

    def interrupt(observed: str) -> None:
        nonlocal fired
        if observed == phase and not fired:
            fired = True
            raise SystemExit(23)

    monkeypatch.setattr(artifact_owner_module, "_artifact_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    assert fired and captured.value.code == 23
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not hasattr(captured.value, "cleanup_authority")
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


@pytest.mark.parametrize(
    "phase",
    [
        "run-open-returned",
        "playwright-mkdir-returned",
        "playwright-open-returned",
        "ephemeral-mkdir-returned",
        "ephemeral-open-returned",
    ],
)
def test_prepare_failures_settle_owned_partial_state(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    run, paths, _ = authorized_run
    fired = False

    def fail(observed: str) -> None:
        nonlocal fired
        if observed == phase and not fired:
            fired = True
            raise RuntimeError("private setup failure")

    monkeypatch.setattr(artifact_owner_module, "_artifact_boundary_hook", fail)
    with pytest.raises(RelayBrowserResultError, match=r"owner is unavailable$") as captured:
        new_relay_browser_result_owner(run)

    assert fired and not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_owner_factory_return_control_keeps_run_retained_owner_for_exact_retry(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original = browser_result_module._new_owner
    created: list[RelayBrowserResultOwner] = []
    fired = False

    def interrupt_after_factory(*args: object, **kwargs: object) -> RelayBrowserResultOwner:
        nonlocal fired
        owner = original(*args, **kwargs)  # type: ignore[arg-type]
        created.append(owner)
        if not fired:
            fired = True
            raise SystemExit(23)
        return owner

    monkeypatch.setattr(browser_result_module, "_new_owner", interrupt_after_factory)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    assert fired and len(created) == 1 and captured.value.code == 23
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    retry = new_relay_browser_result_owner(run)
    assert retry is created[0] and retry.ready
    cleanup_relay_browser_result_owner(run)


def test_owner_factory_return_failure_settles_run_retained_owner_without_leak(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original = browser_result_module._new_owner
    created: list[RelayBrowserResultOwner] = []

    def fail_after_factory(*args: object, **kwargs: object) -> RelayBrowserResultOwner:
        owner = original(*args, **kwargs)  # type: ignore[arg-type]
        created.append(owner)
        raise RuntimeError("private factory failure")

    monkeypatch.setattr(browser_result_module, "_new_owner", fail_after_factory)
    with pytest.raises(RelayBrowserResultError, match=r"owner is unavailable$") as captured:
        new_relay_browser_result_owner(run)

    assert len(created) == 1 and created[0]._settled and created[0]._failed
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)


def test_run_open_return_control_closes_workspace_retained_descriptor(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original = artifact_owner_module.open_exact_directory
    opened: list[int] = []
    fired = False

    def interrupt_after_open(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(descriptor: int, identity: tuple[int, int]) -> bool:
            opened.append(descriptor)
            return original_sink(descriptor, identity)  # type: ignore[operator]

        kwargs["sink"] = retain
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_owner_module, "open_exact_directory", interrupt_after_open)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    assert fired and opened and captured.value.code == 23
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_mkdir_return_control_reconciles_sunk_created_directory_and_identity(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original = artifact_owner_module.mkdir_exact
    fired = False

    def interrupt_after_mkdir(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_owner_module, "mkdir_exact", interrupt_after_mkdir)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    assert fired and captured.value.code == 23
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_child_open_return_control_closes_workspace_retained_descriptor(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original = artifact_owner_module.open_child_directory
    opened: list[int] = []
    fired = False

    def interrupt_after_open(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(descriptor: int, identity: tuple[int, int]) -> bool:
            opened.append(descriptor)
            return original_sink(descriptor, identity)  # type: ignore[operator]

        kwargs["sink"] = retain
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_owner_module, "open_child_directory", interrupt_after_open)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    assert fired and opened and captured.value.code == 23
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_snapshot_publication_control_scrubs_raw_graph_and_retry_returns_observation(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    fired = False

    def interrupt(phase: str) -> None:
        nonlocal fired
        if phase == "snapshot-published" and not fired:
            fired = True
            raise SystemExit(23)

    monkeypatch.setattr(artifact_owner_module, "_artifact_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured.value.code == 23
    assert not hasattr(captured.value, "cleanup_authority")
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    assert consume_relay_browser_result(run, owner).artifacts_deleted
    assert not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize(
    "marker",
    [
        "owner._validation_state = validated",
        "published = owner._validation_state is validated",
    ],
)
def test_atomic_validation_state_controls_retain_valid_outcome_and_scrub_graph(
    authorized_run: tuple[object, ...],
    marker: str,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    line = _source_line(browser_result_module._retain_validation_state, marker)
    with _control_at_line(
        browser_result_module._retain_validation_state,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and captured.value.code == 23
    assert owner._validation_state is True and owner.published
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation
    assert not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize(
    ("marker", "after"),
    [
        ("node = nodes[-1]", False),
        ("if not _sink_value(", False),
    ],
)
def test_capture_assignment_controls_close_partial_descriptors_and_scrub_raw_bytes(
    authorized_run: tuple[object, ...],
    marker: str,
    after: bool,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    line = _source_line(artifact_owner_module._capture_snapshot, marker, after=after)
    with _control_at_line(
        artifact_owner_module._capture_snapshot,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    assert not (paths.contract.run_dir / "playwright").exists()


def test_regular_open_assignment_control_closes_unpublished_descriptor(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    opened: list[int] = []
    original = artifact_files_module.open_regular
    fired = False

    def record_open(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(descriptor: int, identity: tuple[int, int]) -> bool:
            opened.append(descriptor)
            return original_sink(descriptor, identity)  # type: ignore[operator]

        kwargs["sink"] = retain
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and args[1] == "voice-pipecat-rtc-result.json" and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_files_module, "open_regular", record_open)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and opened
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
    )
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_capture_node_return_control_closes_sunk_raw_descriptor_graph(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = artifact_owner_module.capture_node
    opened: list[int] = []
    fired = False

    def interrupt_after_capture(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(node: object) -> bool:
            descriptor = node.descriptor  # type: ignore[attr-defined]
            if descriptor is not None:
                opened.append(descriptor)
            return original_sink(node)  # type: ignore[operator]

        kwargs["sink"] = retain
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and args[1] == "voice-pipecat-rtc-result.json" and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_owner_module, "capture_node", interrupt_after_capture)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and opened and captured.value.code == 23
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_nested_open_return_control_closes_node_owned_descriptor_graph(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    nested = paths.contract.run_dir / "playwright/relay-ephemeral-output/nested"
    nested.mkdir(mode=0o700)
    nested.joinpath("raw.tmp").write_bytes(RAW_SENTINEL.encode())
    original = artifact_files_module.open_child_directory
    opened: list[int] = []
    fired = False

    def interrupt_after_open(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(descriptor: int, identity: tuple[int, int]) -> bool:
            opened.append(descriptor)
            return original_sink(descriptor, identity)  # type: ignore[operator]

        kwargs["sink"] = retain
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and args[1] == "nested" and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_files_module, "open_child_directory", interrupt_after_open)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and opened and captured.value.code == 23
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_nested_capture_return_control_scrubs_and_closes_child_graph(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    nested = paths.contract.run_dir / "playwright/relay-ephemeral-output/nested"
    nested.mkdir(mode=0o700)
    nested.joinpath("raw.tmp").write_bytes(RAW_SENTINEL.encode())
    original = artifact_files_module.capture_node
    captured_nodes: list[object] = []
    fired = False

    def interrupt_after_capture(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and args[1] == "nested" and not fired:
            fired = True
            raise SystemExit(23)
        return result

    def capture_sink_wrapper(*args: object, **kwargs: object) -> bool:
        original_sink = kwargs["sink"]

        def retain(node: object) -> bool:
            if args[1] == "nested":
                captured_nodes.append(node)
            return original_sink(node)  # type: ignore[operator]

        kwargs["sink"] = retain
        return interrupt_after_capture(*args, **kwargs)

    monkeypatch.setattr(artifact_files_module, "capture_node", capture_sink_wrapper)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured_nodes and captured.value.code == 23
    node = captured_nodes[0]
    assert node.content == b"" and node.closed  # type: ignore[attr-defined]
    assert all(child.content == b"" and child.closed for child in node.children)  # type: ignore[attr-defined]
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_optional_log_capture_return_control_closes_sunk_descriptor(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    paths.contract.run_dir.joinpath("playwright.log").write_text("bounded log\n")
    original = artifact_owner_module.capture_optional_log
    opened: list[int] = []
    fired = False

    def interrupt_after_capture(*args: object, **kwargs: object) -> str:
        nonlocal fired
        original_sink = kwargs["sink"]

        def retain(node: object) -> bool:
            descriptor = node.descriptor  # type: ignore[attr-defined]
            if descriptor is not None:
                opened.append(descriptor)
            return original_sink(node)  # type: ignore[operator]

        kwargs["sink"] = retain
        status = original(*args, **kwargs)  # type: ignore[arg-type]
        if status == "found" and not fired:
            fired = True
            raise SystemExit(23)
        return status

    monkeypatch.setattr(
        artifact_owner_module,
        "capture_optional_log",
        interrupt_after_capture,
    )
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and opened and captured.value.code == 23
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not paths.contract.run_dir.joinpath("playwright.log").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )


def test_snapshot_construction_control_closes_all_descriptors_and_scrubs_both_raw_files(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_private_json(run._stack_paths.browser_result, _valid_result(run._call_id))
    report = _safe_report()
    report["raw_marker"] = REPORT_SENTINEL
    _write_private_json(run._stack_paths.playwright_report, report)
    opened: list[int] = []
    original = artifact_files_module.open_regular

    def record_open(*args: object, **kwargs: object) -> bool:
        original_sink = kwargs["sink"]

        def retain(descriptor: int, identity: tuple[int, int]) -> bool:
            opened.append(descriptor)
            return original_sink(descriptor, identity)  # type: ignore[operator]

        kwargs["sink"] = retain
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_files_module, "open_regular", record_open)
    line = _source_line(
        artifact_owner_module._capture_snapshot,
        "snapshot = ArtifactSnapshot(",
    )
    with _control_at_line(
        artifact_owner_module._capture_snapshot,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and opened and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_cleanup_retry_error_is_fixed_and_run_retains_only_recovery_root(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = cleanup_module.remove_node
    failed = False

    def fail_once(node: object, latch: object) -> bool:
        nonlocal failed
        if not failed:
            failed = True
            return False
        return original(node, latch)  # type: ignore[arg-type]

    monkeypatch.setattr(cleanup_module, "remove_node", fail_once)
    with pytest.raises(RelayBrowserResultCleanupRequired) as captured:
        consume_relay_browser_result(run, owner)

    assert str(captured.value) == "Relay browser result cleanup is required"
    assert not hasattr(captured.value, "cleanup_authority")
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_post_validation_inode_mutation_blocks_publication_and_rescans_for_cleanup(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result_path, _ = _write_valid_artifacts(run)
    mutated = False

    def mutate_after_validation(phase: str) -> None:
        nonlocal mutated
        if phase == "rich-artifacts-validated" and not mutated:
            mutated = True
            content = result_path.read_bytes()
            result_path.write_bytes(content.replace(b'"status":"passed"', b'"status":"failed"'))

    monkeypatch.setattr(browser_result_module, "_result_boundary_hook", mutate_after_validation)
    with pytest.raises(RelayBrowserResultCleanupRequired) as captured:
        consume_relay_browser_result(run, owner)

    assert mutated and not owner.published
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_cross_parent_rename_retains_cleanup_authority_until_identity_returns(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _, report_path = _write_valid_artifacts(run)
    moved = paths.contract.run_dir / "moved-relay-report.json"
    renamed = False

    def rename_after_validation(phase: str) -> None:
        nonlocal renamed
        if phase == "rich-artifacts-validated" and not renamed:
            renamed = True
            report_path.rename(moved)

    monkeypatch.setattr(browser_result_module, "_result_boundary_hook", rename_after_validation)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)
    with pytest.raises(RelayBrowserResultCleanupRequired) as captured:
        cleanup_relay_browser_result_owner(run)

    assert renamed and moved.exists() and not owner.published
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    moved.rename(report_path)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        cleanup_relay_browser_result_owner(run)
    cleanup_relay_browser_result_owner(run)
    assert not moved.exists() and not (paths.contract.run_dir / "playwright").exists()


def test_parent_sync_failure_retains_run_only_cleanup_retry(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = artifact_remove_module.sync_directory
    failed = False

    def fail_once(descriptor: int, latch: object) -> bool:
        nonlocal failed
        if not failed:
            failed = True
            return False
        return original(descriptor, latch)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_remove_module, "sync_directory", fail_once)
    with pytest.raises(RelayBrowserResultCleanupRequired) as captured:
        consume_relay_browser_result(run, owner)

    assert failed and not owner.published
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_removed_root_retries_run_parent_fsync_before_binding_close(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    workspace = owner._workspace
    run_binding = workspace._run_binding
    assert run_binding is not None
    original = artifact_remove_module.sync_directory
    failed = False

    def fail_removed_root_parent_once(descriptor: int, latch: object) -> bool:
        nonlocal failed
        root = workspace._playwright_root
        if (
            descriptor == run_binding.descriptor
            and root is not None
            and root.removed
            and not root.parent_synced
            and not failed
        ):
            failed = True
            return False
        return original(descriptor, latch)  # type: ignore[arg-type]

    monkeypatch.setattr(
        artifact_remove_module,
        "sync_directory",
        fail_removed_root_parent_once,
    )
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)

    root = workspace._playwright_root
    assert failed and root is not None and root.removed and not root.parent_synced
    assert not (paths.contract.run_dir / "playwright").exists()
    assert workspace._run_binding is not None and not workspace._complete
    cleanup_relay_browser_result_owner(run)
    assert root.parent_synced and workspace._complete


def test_hardlink_during_unlink_blocks_publication_until_retained_fd_reaches_zero_links(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result_path, _ = _write_valid_artifacts(run)
    escaped = tmp_path / "escaped-rich-result.json"
    original_unlink = artifact_remove_module.os.unlink
    original_link = artifact_remove_module.os.link
    fired = False

    def link_then_unlink(name: object, *args: object, **kwargs: object) -> None:
        nonlocal fired
        if name == result_path.name and not fired:
            fired = True
            original_link(result_path, escaped)
        original_unlink(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_remove_module.os, "unlink", link_then_unlink)
    with pytest.raises(RelayBrowserResultCleanupRequired) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and escaped.read_bytes().find(RAW_SENTINEL.encode()) >= 0
    assert not owner.published and (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    original_unlink(escaped)
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_directory_swap_during_quarantine_is_detected_and_retryable(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    output = paths.contract.run_dir / "playwright/relay-ephemeral-output"
    output.joinpath("owned.tmp").write_bytes(b"owned-tree")
    escaped = tmp_path / "escaped-relay-ephemeral-output"
    original_rename = artifact_remove_module.os.rename
    quarantine: list[str] = []
    fired = False

    def swap_then_rename(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal fired
        if source == "relay-ephemeral-output" and not fired:
            fired = True
            source_fd = kwargs["src_dir_fd"]
            original_rename(source, escaped, src_dir_fd=source_fd)  # type: ignore[arg-type]
            os.mkdir("relay-ephemeral-output", 0o700, dir_fd=source_fd)  # type: ignore[arg-type]
            quarantine.append(str(destination))
        original_rename(source, destination, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_remove_module.os, "rename", swap_then_rename)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)

    assert fired and len(quarantine) == 1 and escaped.joinpath("owned.tmp").exists()
    assert not owner.published
    replacement = output.parent / quarantine[0]
    assert replacement.is_dir()
    os.rmdir(replacement)
    original_rename(escaped, output)
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_preowned_ephemeral_identity_escape_before_capture_retains_cleanup_root(
    authorized_run: tuple[object, ...],
    tmp_path: Path,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    output = paths.contract.run_dir / "playwright/relay-ephemeral-output"
    escaped = tmp_path / "escaped-preowned-output"
    output.rename(escaped)
    output.mkdir(mode=0o700)
    _write_valid_artifacts(run)

    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)

    assert escaped.is_dir() and output.is_dir() and not owner.published
    output.rmdir()
    escaped.rename(output)
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_nonregular_swap_during_quarantine_is_detected_and_retryable(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    unexpected = paths.contract.run_dir / "playwright/relay-ephemeral-output/unexpected"
    os.mkfifo(unexpected, 0o600)
    escaped = tmp_path / "escaped-unexpected-fifo"
    original_rename = artifact_remove_module.os.rename
    original_unlink = artifact_remove_module.os.unlink
    quarantine: list[str] = []
    parent_descriptors: list[int] = []
    fired = False

    def swap_then_rename(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal fired
        if source == unexpected.name and not fired:
            fired = True
            source_fd = kwargs["src_dir_fd"]
            original_rename(source, escaped, src_dir_fd=source_fd)  # type: ignore[arg-type]
            os.mkfifo(unexpected.name, 0o600, dir_fd=source_fd)  # type: ignore[arg-type]
            quarantine.append(str(destination))
            parent_descriptors.append(source_fd)  # type: ignore[arg-type]
        original_rename(source, destination, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_remove_module.os, "rename", swap_then_rename)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)

    assert fired and len(quarantine) == 1 and escaped.exists()
    assert not owner.published
    source_fd = parent_descriptors[0]
    replacement = os.stat(quarantine[0], dir_fd=source_fd, follow_symlinks=False)
    assert stat.S_ISFIFO(replacement.st_mode)
    original_unlink(quarantine[0], dir_fd=source_fd)
    original_rename(escaped, unexpected.name, dst_dir_fd=source_fd)
    cleanup_relay_browser_result_owner(run)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_directory_rmdir_return_control_reconciles_quarantine_absence(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = artifact_remove_module.os.rmdir
    fired = False

    def interrupt_after_rmdir(name: object, *args: object, **kwargs: object) -> None:
        nonlocal fired
        original(name, *args, **kwargs)  # type: ignore[arg-type]
        if str(name).startswith(".relay-owned-delete-") and not fired:
            fired = True
            raise SystemExit(23)

    monkeypatch.setattr(artifact_remove_module.os, "rmdir", interrupt_after_rmdir)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured.value.code == 23
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation
    assert not (paths.contract.run_dir / "playwright").exists()


def test_late_playwright_log_is_rescanned_deleted_and_never_orphaned(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    late_log = paths.contract.run_dir / "playwright.log"
    fired = False

    def create_late_log(phase: str) -> None:
        nonlocal fired
        if phase == "late-log-absence-observed" and not fired:
            fired = True
            late_log.write_bytes(b"late bounded log")

    monkeypatch.setattr(artifact_owner_module, "_artifact_boundary_hook", create_late_log)
    observation = consume_relay_browser_result(run, owner)

    assert fired and observation.artifacts_deleted and not observation
    assert not late_log.exists() and not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "unsafe-mode"])
def test_late_invalid_log_is_deleted_and_irreversibly_blocks_publication(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    late_log = paths.contract.run_dir / "playwright.log"
    outside = tmp_path / "outside-log-target"
    outside.write_bytes(b"outside survives")
    fired = False

    def create_invalid_late_log(phase: str) -> None:
        nonlocal fired
        if phase != "late-log-absence-observed" or fired:
            return
        fired = True
        if kind == "symlink":
            late_log.symlink_to(outside)
        elif kind == "fifo":
            os.mkfifo(late_log, 0o600)
        else:
            late_log.write_bytes(b"unsafe late log")
            late_log.chmod(0o666)

    monkeypatch.setattr(
        artifact_owner_module,
        "_artifact_boundary_hook",
        create_invalid_late_log,
    )
    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert fired and not owner.published and not owner._workspace._publication_safe
    assert outside.read_bytes() == b"outside survives"
    assert not os.path.lexists(late_log)
    assert not (paths.contract.run_dir / "playwright").exists()


def test_capture_failure_is_rescanned_and_deleted_without_publication(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = artifact_owner_module.capture_optional_log
    calls = 0

    def fail_once(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return "error" if calls == 1 else original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(artifact_owner_module, "capture_optional_log", fail_once)
    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)

    assert calls >= 2 and not owner.published
    assert not (paths.contract.run_dir / "playwright").exists()


def test_concurrent_cleanup_is_serialized_and_idempotent(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    new_relay_browser_result_owner(run)
    errors: list[BaseException] = []

    def cleanup() -> None:
        try:
            cleanup_relay_browser_result_owner(run)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=cleanup) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == [] and not (paths.contract.run_dir / "playwright").exists()


def test_concurrent_consumers_share_one_sanitized_publication(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    observations: list[RelayBrowserObservation] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            observations.append(consume_relay_browser_result(run, owner))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == [] and len(observations) == 2
    assert observations[0] is observations[1] and not observations[0]
    assert not (paths.contract.run_dir / "playwright").exists()


def test_owner_cannot_cross_exact_run_source_call_or_derived_paths(
    authorized_run: tuple[object, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_run, first_paths, _ = authorized_run
    first_owner = new_relay_browser_result_owner(first_run)
    second_root = tmp_path / "second"
    second_root.mkdir(mode=0o700)
    second_paths, material, running, readiness, _ = _ready_runtime(second_root)
    second_run = new_relay_probe_run(
        runtime_paths=second_paths,
        source=_source(monkeypatch),
    )
    authorize_relay_backend(second_run, tls_material=material)
    authorize_relay_browser(
        second_run,
        running=running,
        tls_material=material,
        readiness=readiness,
    )
    second_owner = new_relay_browser_result_owner(second_run)
    try:
        with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
            consume_relay_browser_result(second_run, first_owner)

        assert first_owner.ready and second_owner.ready
        assert (first_paths.contract.run_dir / "playwright").is_dir()
        assert (second_paths.contract.run_dir / "playwright").is_dir()
    finally:
        cleanup_relay_browser_result_owner(first_run)
        cleanup_relay_browser_result_owner(second_run)
        if material._slot.has_material:
            cleanup_tls_material_generation_slot(material._slot)


def test_public_values_are_factory_owned_noncopyable_and_nonserializable(
    authorized_run: tuple[object, ...],
) -> None:
    run, _, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    observation = consume_relay_browser_result(run, owner)

    for value in (owner, observation):
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
    with pytest.raises(TypeError):
        RelayBrowserResultOwner(  # type: ignore[call-arg]
            object(),
            paths=run._stack_paths,
            call_id=run._call_id,
            source=run._source,
        )
    with pytest.raises(TypeError):
        RelayBrowserObservation(object())


def test_assignment_window_control_after_snapshot_sink_keeps_one_coherent_owner(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = artifact_owner_module._sink_value
    fired = False

    def interrupt_after_sink(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and args[3] == "snapshot" and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(artifact_owner_module, "_sink_value", interrupt_after_sink)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
    )
    assert not (paths.contract.run_dir / "playwright").exists()


def test_observation_assignment_control_publishes_before_control_escapes(
    authorized_run: tuple[object, ...],
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    line = _source_line(
        browser_result_module._retain_observation,
        "owner._observation = observation",
    )
    with _control_at_line(
        browser_result_module._retain_observation,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation
    assert not (paths.contract.run_dir / "playwright").exists()


def test_observation_constructor_return_control_keeps_exact_published_value(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = browser_result_module.RelayBrowserObservation
    created: list[RelayBrowserObservation] = []

    def interrupt_after_constructor(*args: object, **kwargs: object) -> RelayBrowserObservation:
        observation = original(*args, **kwargs)  # type: ignore[arg-type]
        created.append(observation)
        raise SystemExit(23)

    monkeypatch.setattr(
        browser_result_module,
        "RelayBrowserObservation",
        interrupt_after_constructor,
    )
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert len(created) == 1 and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    monkeypatch.setattr(browser_result_module, "RelayBrowserObservation", original)
    retry = consume_relay_browser_result(run, owner)
    assert retry is created[0] and retry.artifacts_deleted and not retry
    assert not (paths.contract.run_dir / "playwright").exists()


def test_cleanup_completion_control_retains_safe_publication(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    fired = False

    def interrupt(phase: str) -> None:
        nonlocal fired
        if phase == "workspace-complete-published" and not fired:
            fired = True
            raise SystemExit(23)

    monkeypatch.setattr(cleanup_module, "_artifact_cleanup_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured.value.code == 23
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation
    assert not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize(
    "marker",
    [
        "owner._settled = complete",
        "if not complete:",
        'hook_ok = _call_hook("rich-artifacts-deleted", latch)',
        "owner._validation_state is not True",
        "or not publication_safe",
        "or not hook_ok",
        "or not _authorized(run, owner, active=True)",
        "observation = _publish_observation(owner, latch)",
    ],
)
def test_safe_settlement_line_controls_resume_exact_observation_publication(
    authorized_run: tuple[object, ...],
    marker: str,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    line = _source_line(browser_result_module._consume_locked, marker)

    with _control_at_line(
        browser_result_module._consume_locked,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and captured.value.code == 23
    assert owner._workspace._complete and owner._workspace._publication_safe
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation and owner.published
    assert not (paths.contract.run_dir / "playwright").exists()


def test_safe_settlement_line_control_preserves_first_latched_control(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    first_control = False

    def interrupt_after_validation(phase: str) -> None:
        nonlocal first_control
        if phase == "rich-artifacts-validated" and not first_control:
            first_control = True
            raise SystemExit(23)

    monkeypatch.setattr(
        browser_result_module,
        "_result_boundary_hook",
        interrupt_after_validation,
    )
    line = _source_line(browser_result_module._consume_locked, "if not complete:")
    with _control_at_line(
        browser_result_module._consume_locked,
        line,
        KeyboardInterrupt("later settlement control"),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    assert injected == [True] and first_control and captured.value.code == 23
    assert owner._workspace._complete and owner._workspace._publication_safe
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation and owner.published
    assert not (paths.contract.run_dir / "playwright").exists()


@pytest.mark.parametrize(
    ("marker", "after"),
    [
        ("owner._settled = complete", False),
        ("if not complete:", False),
        ("if not complete:", True),
    ],
)
def test_incomplete_post_validation_settlement_is_poisoned_before_line_control(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    after: bool,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result_path, _ = _write_valid_artifacts(run)
    mutated = False

    def mutate_after_validation(phase: str) -> None:
        nonlocal mutated
        if phase == "rich-artifacts-validated" and not mutated:
            mutated = True
            content = result_path.read_bytes()
            result_path.write_bytes(content.replace(b'"status":"passed"', b'"status":"failed"'))

    monkeypatch.setattr(browser_result_module, "_result_boundary_hook", mutate_after_validation)
    line = _source_line(browser_result_module._consume_locked, marker, after=after)
    with _control_at_line(
        browser_result_module._consume_locked,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            consume_relay_browser_result(run, owner)

    workspace = owner._workspace
    assert injected == [True] and captured.value.code == 23 and mutated
    assert workspace._rescan_required and not workspace._publication_safe
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)
    assert workspace._complete and not workspace._publication_safe and not owner.published
    assert not (paths.contract.run_dir / "playwright").exists()


def test_binding_close_return_control_resumes_after_root_removal_without_poison(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original = cleanup_module.close_owned_descriptor
    fired = False

    def interrupt_after_close(*args: object, **kwargs: object) -> bool:
        nonlocal fired
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if result and not fired:
            fired = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(cleanup_module, "close_owned_descriptor", interrupt_after_close)
    with pytest.raises(SystemExit) as captured:
        consume_relay_browser_result(run, owner)

    assert fired and captured.value.code == 23
    assert owner._workspace._publication_safe
    observation = consume_relay_browser_result(run, owner)
    assert observation.artifacts_deleted and not observation
    assert not (paths.contract.run_dir / "playwright").exists()


def test_partial_binding_close_failure_poisoned_and_resumes_removed_root_cleanup(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    workspace = owner._workspace
    playwright = workspace._playwright_binding
    assert playwright is not None
    original = cleanup_module.close_owned_descriptor
    failed = False

    def fail_playwright_once(
        descriptor: int,
        identity: tuple[int, int],
        latch: object,
    ) -> bool:
        nonlocal failed
        if descriptor == playwright.descriptor and not failed:
            failed = True
            return False
        return original(descriptor, identity, latch)  # type: ignore[arg-type]

    monkeypatch.setattr(cleanup_module, "close_owned_descriptor", fail_playwright_once)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        consume_relay_browser_result(run, owner)

    assert failed and workspace._playwright_root is not None
    assert workspace._playwright_root.removed and not workspace._publication_safe
    cleanup_relay_browser_result_owner(run)
    assert not workspace._publication_safe and not (paths.contract.run_dir / "playwright").exists()


def test_directory_quarantine_pins_same_uid_mutator_assumption() -> None:
    documentation = artifact_remove_module.__doc__ or ""
    assert "no hostile same-UID process" in documentation
    assert "retained-directory unlink receipt" in documentation


def test_new_owner_public_handoff_preserves_first_control_across_prepare_return(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    original_prepare = artifact_owner_module._prepare_relay_artifact_workspace
    original_settle = browser_result_module._settle_owner
    first_fired = False
    later_prepare_fired = False
    later_settle_fired = False

    def first_control(phase: str) -> None:
        nonlocal first_fired
        if phase == "ephemeral-open-returned" and not first_fired:
            first_fired = True
            raise SystemExit(23)

    def later_control(*args: object, **kwargs: object) -> object:
        nonlocal later_prepare_fired
        result = original_prepare(*args, **kwargs)  # type: ignore[arg-type]
        if not later_prepare_fired:
            later_prepare_fired = True
            raise KeyboardInterrupt("later prepare return control")
        return result

    def final_control(*args: object, **kwargs: object) -> object:
        nonlocal later_settle_fired
        result = original_settle(*args, **kwargs)  # type: ignore[arg-type]
        if not later_settle_fired:
            later_settle_fired = True
            raise KeyboardInterrupt("later settle return control")
        return result

    monkeypatch.setattr(artifact_owner_module, "_artifact_boundary_hook", first_control)
    monkeypatch.setattr(
        browser_result_module,
        "_prepare_relay_artifact_workspace",
        later_control,
    )
    monkeypatch.setattr(browser_result_module, "_settle_owner", final_control)
    with pytest.raises(SystemExit) as captured:
        new_relay_browser_result_owner(run)

    owner = run._browser_artifact_cleanup_owner()
    assert first_fired and later_prepare_fired and later_settle_fired
    assert captured.value.code == 23
    assert type(owner) is RelayBrowserResultOwner
    assert owner._failed and owner._settled and owner._workspace._complete
    assert browser_result_module._authorized(run, owner, active=False)
    assert not owner.ready and not owner.published
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)
    with pytest.raises(RelayBrowserResultError, match=r"owner is unavailable$"):
        new_relay_browser_result_owner(run)


def test_cleanup_public_handoff_preserves_first_control_across_settle_return(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    _write_valid_artifacts(run)
    original_settle = browser_result_module._settle_owner
    first_fired = False
    later_fired = False

    def first_control(phase: str) -> None:
        nonlocal first_fired
        if phase == "workspace-complete-published" and not first_fired:
            first_fired = True
            raise SystemExit(23)

    def later_control(*args: object, **kwargs: object) -> object:
        nonlocal later_fired
        result = original_settle(*args, **kwargs)  # type: ignore[arg-type]
        if not later_fired:
            later_fired = True
            raise KeyboardInterrupt("later settle return control")
        return result

    monkeypatch.setattr(cleanup_module, "_artifact_cleanup_boundary_hook", first_control)
    monkeypatch.setattr(browser_result_module, "_settle_owner", later_control)
    with pytest.raises(SystemExit) as captured:
        cleanup_relay_browser_result_owner(run)

    assert first_fired and later_fired and captured.value.code == 23
    assert owner._failed and owner._settled and owner._workspace._complete
    assert browser_result_module._authorized(run, owner, active=False)
    assert not owner.ready and not owner.published
    assert not (paths.contract.run_dir / "playwright").exists()
    assert not _traceback_contains(
        captured.value,
        RAW_SENTINEL,
        REPORT_SENTINEL,
        run._call_id,
        os.fspath(paths.contract.run_dir),
        SOURCE_SENTINEL,
    )
    cleanup_relay_browser_result_owner(run)
    with pytest.raises(RelayBrowserResultError, match=r"result is unavailable$"):
        consume_relay_browser_result(run, owner)


def test_cleanup_enumeration_stops_at_budget_plus_one_and_retries_safely(
    authorized_run: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, paths, _ = authorized_run
    owner = new_relay_browser_result_owner(run)
    result_path, report_path = _write_valid_artifacts(run)
    original_scandir = artifact_files_module.os.scandir

    class HostileEntries:
        consumed = 0
        closed = False

        def __enter__(self) -> HostileEntries:
            return self

        def __exit__(self, *_args: object) -> None:
            self.closed = True

        def __iter__(self) -> HostileEntries:
            return self

        def __next__(self) -> object:
            self.consumed += 1
            if self.consumed > artifact_files_module.MAX_ENTRIES + 1:
                raise AssertionError("list_names consumed a 258th hostile entry")
            entry = type("HostileEntry", (), {})()
            entry.name = f"node-{self.consumed:04d}"
            return entry

    entries = HostileEntries()
    monkeypatch.setattr(artifact_files_module.os, "scandir", lambda _fd: entries)
    with pytest.raises(RelayBrowserResultCleanupRequired):
        cleanup_relay_browser_result_owner(run)

    assert entries.consumed == artifact_files_module.MAX_ENTRIES + 1
    assert entries.closed and not owner._workspace._complete and not owner.published
    assert result_path.exists() and report_path.exists()
    monkeypatch.setattr(artifact_files_module.os, "scandir", original_scandir)
    cleanup_relay_browser_result_owner(run)
    assert owner._workspace._complete and not (paths.contract.run_dir / "playwright").exists()

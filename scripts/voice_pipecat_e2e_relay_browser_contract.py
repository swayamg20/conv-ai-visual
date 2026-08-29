"""Exact rich-result contract for the relay-only Playwright observation."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime

from scripts.voice_pipecat_e2e_stack import (
    E2E_SESSION_ID,
    VOICE_PROFILE_ID,
    VOICE_RUNTIME,
    _assert_browser_artifact_safe,
    _validate_audio_sample_clock,
)

_CONTRACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_REPORT_SPEC_ID = "voice-pipecat-rtc-relay-tls"
_BROWSER_KEYS = {
    "relay_policy_attested",
    "tls_spki_pin_attested",
    "gateway_attested",
    "exact_local_track_id",
    "exact_remote_track_id",
    "connection_gestures",
    "pre_ready_microphone_track",
    "peer_connection_count",
    "outbound_bytes_sent",
    "outbound_packets_sent",
    "inbound_bytes_received",
    "inbound_packets_received",
    "selected_candidate_pair",
    "signaling_request_counts",
    "local_peak_rms",
    "remote_peak_rms",
    "audio_sample_clock",
    "first_user_pcm_region_start_ms",
    "second_user_pcm_region_start_ms",
    "remote_pcm_silence_attribution_start_ms",
    "sustained_pcm_silence_ms",
    "no_stale_audio_guard_start_ms",
    "no_stale_audio_guard_end_ms",
    "remote_attribution_tolerance_ms",
    "first_turn_id",
    "second_turn_id",
    "interrupted_speech_id",
    "second_reply_completed_speech_id",
    "canonical_event_count",
}


def validate_relay_browser_artifacts(
    raw_result: bytes,
    raw_report: bytes,
    expected_call_id: str,
) -> bool:
    result: dict[str, object] | None = None
    report: dict[str, object] | None = None
    try:
        result = _decode_json(raw_result, "browser result")
        report = _decode_json(raw_report, "safe reporter result")
        return _validate_rich_result(result, expected_call_id) and _validate_safe_report(report)
    finally:
        if result is not None:
            result.clear()
        if report is not None:
            report.clear()
        raw_result = raw_report = b""


def _validate_rich_result(value: dict[str, object], expected_call_id: str) -> bool:
    if set(value) != {
        "schema_version",
        "status",
        "completed_at",
        "runtime",
        "profile_id",
        "peer_reservation_id",
        "voice_call_id",
        "trace_id",
        "browser_evidence",
        "browser_cleanup_observed",
        "terminal_cleanup",
    }:
        return False
    completed_at = value.get("completed_at")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("status") != "passed"
        or value.get("runtime") != VOICE_RUNTIME
        or value.get("profile_id") != VOICE_PROFILE_ID
        or value.get("voice_call_id") != expected_call_id
        or value.get("browser_cleanup_observed") is not True
        or not _contract_id(value.get("peer_reservation_id"))
        or not _contract_id(value.get("trace_id"))
        or type(completed_at) is not str
        or not _ISO_UTC.fullmatch(completed_at)
    ):
        return False
    datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%S.%fZ")
    browser = value.get("browser_evidence")
    terminal = value.get("terminal_cleanup")
    return bool(
        isinstance(browser, dict)
        and _validate_browser_evidence(browser)
        and isinstance(terminal, dict)
        and _validate_terminal(terminal, expected_call_id)
    )


def _validate_browser_evidence(browser: dict[str, object]) -> bool:
    if set(browser) != _BROWSER_KEYS or any(
        browser.get(field) is not True
        for field in (
            "relay_policy_attested",
            "tls_spki_pin_attested",
            "gateway_attested",
        )
    ):
        return False
    local_track = browser.get("exact_local_track_id")
    remote_track = browser.get("exact_remote_track_id")
    if not _contract_id(local_track) or not _contract_id(remote_track):
        return False
    if not _validate_connection_gestures(browser.get("connection_gestures")):
        return False
    microphone = browser.get("pre_ready_microphone_track")
    if not isinstance(microphone, dict) or set(microphone) != {
        "observed_at_ms",
        "enabled_at_observation",
        "ready_state_at_observation",
        "first_agent_ready_observed_at_ms",
    }:
        return False
    observed = microphone.get("observed_at_ms")
    ready = microphone.get("first_agent_ready_observed_at_ms")
    if (
        not _finite_nonnegative(observed)
        or not _finite_nonnegative(ready)
        or ready <= observed  # type: ignore[operator]
        or microphone.get("enabled_at_observation") is not False
        or microphone.get("ready_state_at_observation") != "live"
        or not _exact_int(browser.get("peer_connection_count"), 1)
    ):
        return False
    for field in (
        "outbound_bytes_sent",
        "outbound_packets_sent",
        "inbound_bytes_received",
        "inbound_packets_received",
        "canonical_event_count",
    ):
        if not _positive_int(browser.get(field)):
            return False
    if not _finite_at_least(browser.get("local_peak_rms"), 0.005) or not _finite_at_least(
        browser.get("remote_peak_rms"), 0.02
    ):
        return False
    if not _validate_selected_pair(browser.get("selected_candidate_pair")):
        return False
    if not _validate_request_counts(browser.get("signaling_request_counts")):
        return False
    timing = [
        browser.get("first_user_pcm_region_start_ms"),
        browser.get("second_user_pcm_region_start_ms"),
        browser.get("remote_pcm_silence_attribution_start_ms"),
        browser.get("no_stale_audio_guard_start_ms"),
        browser.get("no_stale_audio_guard_end_ms"),
    ]
    if not all(_finite_nonnegative(item) for item in timing):
        return False
    first, second, silence, guard_start, guard_end = timing
    if not (first < second <= silence < guard_start <= guard_end):  # type: ignore[operator]
        return False
    if (
        not _exact_int(browser.get("sustained_pcm_silence_ms"), 200)
        or guard_start != silence + 200  # type: ignore[operator]
        or not _exact_int(browser.get("remote_attribution_tolerance_ms"), 100)
        or not all(
            _contract_id(browser.get(field))
            for field in (
                "first_turn_id",
                "second_turn_id",
                "interrupted_speech_id",
                "second_reply_completed_speech_id",
            )
        )
    ):
        return False
    if not _audio_numeric_types_are_exact(browser):
        return False
    _validate_audio_sample_clock(browser, str(local_track), str(remote_track))
    return True


def _validate_selected_pair(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "state",
        "nominated",
        "bytes_sent",
        "bytes_received",
        "current_round_trip_time_seconds",
        "local",
        "remote",
    }:
        return False
    return bool(
        value.get("state") == "succeeded"
        and value.get("nominated") is True
        and _positive_int(value.get("bytes_sent"))
        and _positive_int(value.get("bytes_received"))
        and (
            value.get("current_round_trip_time_seconds") is None
            or _finite_nonnegative(value.get("current_round_trip_time_seconds"))
        )
        and value.get("local")
        == {"candidate_type": "relay", "protocol": "udp", "relay_protocol": "tls"}
        and value.get("remote")
        == {"candidate_type": "host", "protocol": "udp", "relay_protocol": None}
    )


def _validate_request_counts(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "post",
        "authenticated_post",
        "patch",
        "authenticated_patch",
        "delete",
        "authenticated_delete",
        "with_cookies",
    }:
        return False
    patches = value.get("patch")
    return bool(
        _exact_int(value.get("post"), 1)
        and _exact_int(value.get("authenticated_post"), 1)
        and _positive_int(patches)
        and _positive_int(value.get("authenticated_patch"))
        and value.get("authenticated_patch") == patches
        and _exact_int(value.get("delete"), 1)
        and _exact_int(value.get("authenticated_delete"), 1)
        and _exact_int(value.get("with_cookies"), 0)
    )


def _validate_terminal(value: dict[str, object], call_id: str) -> bool:
    if set(value) != {
        "schema_version",
        "status",
        "runtime",
        "profile_id",
        "session_id",
        "voice_call_id",
        "reservation",
        "control_plane",
        "fake_media",
    } or (
        not _exact_int(value.get("schema_version"), 1)
        or value.get("status") != "pending"
        or value.get("runtime") != VOICE_RUNTIME
        or value.get("profile_id") != VOICE_PROFILE_ID
        or value.get("session_id") != E2E_SESSION_ID
        or value.get("voice_call_id") != call_id
    ):
        return False
    reservation = value.get("reservation")
    if not isinstance(reservation, dict) or set(reservation) != {
        "state",
        "cleanup_complete",
        "terminal_reason",
        "retryable",
    }:
        return False
    if (
        reservation.get("state") != "terminal"
        or reservation.get("cleanup_complete") is not True
        or not (
            (
                reservation.get("terminal_reason") == "user_ended"
                and reservation.get("retryable") is False
            )
            or (
                reservation.get("terminal_reason") == "client_disconnected"
                and reservation.get("retryable") is True
            )
        )
    ):
        return False
    if not _validate_control_plane(value.get("control_plane")):
        return False
    media = value.get("fake_media")
    if not isinstance(media, dict) or set(media) != {
        "input_frame_count",
        "final_transcripts",
        "llm_response_count",
        "tts_frame_count",
        "tts_cancelled_count",
        "cleaned_processors",
        "processor_cleanup_counts",
        "profile_close_count",
        "media_contract_satisfied",
    }:
        return False
    return bool(
        _positive_int(media.get("input_frame_count"))
        and media.get("final_transcripts") == ["Hello tutor.", "Actually, stop."]
        and _exact_int(media.get("llm_response_count"), 2)
        and _positive_int(media.get("tts_frame_count"))
        and _exact_int(media.get("tts_cancelled_count"), 1)
        and media.get("cleaned_processors") == ["llm", "stt", "tts"]
        and _validate_processor_cleanup_counts(media.get("processor_cleanup_counts"))
        and _exact_int(media.get("profile_close_count"), 1)
        and media.get("media_contract_satisfied") is True
    )


def _validate_safe_report(value: dict[str, object]) -> bool:
    if set(value) != {
        "schema_version",
        "status",
        "spec_id",
        "pass_counts",
        "retention_policy",
    }:
        return False
    counts = value.get("pass_counts")
    retention = value.get("retention_policy")
    return bool(
        _exact_int(value.get("schema_version"), 1)
        and value.get("status") == "passed"
        and value.get("spec_id") == _REPORT_SPEC_ID
        and isinstance(counts, dict)
        and set(counts) == {"tests_discovered", "tests_passed"}
        and _exact_int(counts.get("tests_discovered"), 1)
        and _exact_int(counts.get("tests_passed"), 1)
        and isinstance(retention, dict)
        and set(retention)
        == {
            "rich_reporters_disabled",
            "media_capture_disabled",
            "reporter_stdio_disabled",
            "runner_cleanup_required",
        }
        and all(item is True for item in retention.values())
    )


def _validate_connection_gestures(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], dict)
        and set(value[0]) == {"sequence", "action"}
        and _exact_int(value[0].get("sequence"), 1)
        and value[0].get("action") == "prepare"
        and isinstance(value[1], dict)
        and set(value[1]) == {"sequence", "action"}
        and _exact_int(value[1].get("sequence"), 2)
        and value[1].get("action") == "activate"
    )


def _validate_control_plane(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "bootstrap_active_assignment_count",
        "bootstrap_active_lock_count",
        "signaling_active_call_count",
        "runtime_handle_retained",
        "cleanup_retry_pending",
        "runtime_observer_pending",
        "expiry_pending",
        "trusted_release_pending",
    }:
        return False
    return bool(
        all(
            _exact_int(value.get(field), 0)
            for field in (
                "bootstrap_active_assignment_count",
                "bootstrap_active_lock_count",
                "signaling_active_call_count",
            )
        )
        and all(
            value.get(field) is False
            for field in (
                "runtime_handle_retained",
                "cleanup_retry_pending",
                "runtime_observer_pending",
                "expiry_pending",
                "trusted_release_pending",
            )
        )
    )


def _validate_processor_cleanup_counts(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == {"stt", "llm", "tts"}
        and all(_exact_int(value.get(field), 1) for field in ("stt", "llm", "tts"))
    )


def _audio_numeric_types_are_exact(browser: dict[str, object]) -> bool:
    clock = browser.get("audio_sample_clock")
    if not isinstance(clock, dict):
        return False
    evidence = clock.get("evidence")
    bracket = clock.get("interruption_bracket")
    if not isinstance(evidence, dict) or not isinstance(bracket, dict):
        return False
    if not all(
        type(evidence.get(field)) is int
        for field in ("schema_version", "sample_rate_hz", "quantum_frames")
    ):
        return False
    for probe_name in ("local", "remote"):
        probe = evidence.get(probe_name)
        if not isinstance(probe, dict) or not _audio_probe_numeric_types_are_exact(probe):
            return False
    return bool(
        all(
            type(bracket.get(field)) is int
            for field in (
                "sample_rate_hz",
                "quantum_frames",
                "required_silence_frames",
                "second_local_active_block_start_frame",
                "remote_silence_transition_block_end_frame",
                "interruption_upper_bound_frames",
            )
        )
        and type(bracket.get("interruption_upper_bound_ms")) in {int, float}
    )


def _audio_probe_numeric_types_are_exact(probe: dict[str, object]) -> bool:
    if type(probe.get("threshold_rms")) not in {int, float} or not all(
        type(probe.get(field)) is int
        for field in (
            "silence_hold_frames",
            "processed_block_count",
            "latest_block_end_frame",
            "current_state_block_count",
            "active_region_count",
            "stale_frame_correction_count",
        )
    ):
        return False
    for field in (
        "last_stale_observed_block_start_frame",
        "last_stale_logical_block_start_frame",
        "stale_frame_catch_up_observed_block_start_frame",
    ):
        if probe.get(field) is not None and type(probe.get(field)) is not int:
            return False
    transitions = probe.get("transitions")
    return bool(
        isinstance(transitions, list)
        and all(
            isinstance(item, dict)
            and type(item.get("block_start_frame")) is int
            and type(item.get("block_end_frame")) is int
            for item in transitions
        )
    )


def _decode_json(raw: bytes, label: str) -> dict[str, object]:
    if type(raw) is not bytes or not raw:
        raise ValueError("invalid relay browser artifact")
    text = raw.decode("utf-8", errors="strict")
    try:
        _assert_browser_artifact_safe(text, label)
        value = json.loads(
            text,
            object_pairs_hook=_exact_json_object,
            parse_constant=_reject_json_constant,
        )
    finally:
        text = ""
    if type(value) is not dict:
        raise ValueError("invalid relay browser artifact")
    return value


def _exact_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid relay browser artifact")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid relay browser artifact")


def _contract_id(value: object) -> bool:
    return bool(type(value) is str and _CONTRACT_ID.fullmatch(value))


def _positive_int(value: object) -> bool:
    return bool(type(value) is int and 0 < value <= 9_007_199_254_740_991)


def _exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _finite_nonnegative(value: object) -> bool:
    return bool(
        type(value) in {int, float}
        and math.isfinite(value)  # type: ignore[arg-type]
        and value >= 0  # type: ignore[operator]
    )


def _finite_at_least(value: object, minimum: float) -> bool:
    return bool(_finite_nonnegative(value) and value >= minimum)  # type: ignore[operator]

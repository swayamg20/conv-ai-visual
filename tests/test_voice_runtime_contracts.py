from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from murmur.voice.runtime_contracts import (
    LIVEKIT_EVENT_PROTOCOL,
    PIPECAT_EVENT_PROTOCOL,
    VOICE_V2_EVENT_TOPIC,
    LiveKitVoiceRuntimeAssignment,
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeAssignment,
    VoiceRuntimeKind,
    VoiceRuntimeTerminalReason,
    VoiceRuntimeTerminalResult,
)
from pydantic import TypeAdapter, ValidationError

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SESSION_ID = "10000000-0000-4000-8000-000000000001"
AGENT_ID = "20000000-0000-4000-8000-000000000002"
CALL_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"
PARTICIPANT_TOKEN = "participant-token-that-must-never-be-logged"
PIPECAT_LOCATOR = "https://voice.example.test/signal/M4bA8g7R2cQ9fN6xK3pL"


def _claims(
    runtime: VoiceRuntimeKind = VoiceRuntimeKind.LIVEKIT_V2,
    **overrides: object,
) -> VoiceCallClaims:
    values: dict[str, object] = {
        "user_id": "firebase-user-1",
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "voice_call_id": CALL_ID,
        "trace_id": TRACE_ID,
        "runtime": runtime,
        "profile_id": "direct-cascade-v1",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return VoiceCallClaims.model_validate(values)


def _livekit_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "runtime": "livekit_v2",
        "claims": _claims(),
        "event_protocol": LIVEKIT_EVENT_PROTOCOL,
        "expires_at": NOW + timedelta(minutes=5),
        "server_url": "wss://murmur-test.livekit.cloud",
        "room_name": "murmur-test-room-1",
        "participant_token": PARTICIPANT_TOKEN,
        "participant_identity": "user-call-1",
        "agent_participant_identity": "agent-call-1",
        "dispatch_id": "dispatch-1",
        "worker_name": "murmur-voice-v2",
        "event_topic": VOICE_V2_EVENT_TOPIC,
    }
    values.update(overrides)
    return values


def _pipecat_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "runtime": "pipecat_smallwebrtc_v1",
        "claims": _claims(VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1),
        "event_protocol": PIPECAT_EVENT_PROTOCOL,
        "expires_at": NOW + timedelta(minutes=5),
        "webrtc_url": PIPECAT_LOCATOR,
        "peer_reservation_id": "peer-reservation-1",
    }
    values.update(overrides)
    return values


def test_runtime_kinds_are_closed_and_exact() -> None:
    assert {runtime.value for runtime in VoiceRuntimeKind} == {
        "legacy",
        "livekit_v2",
        "pipecat_smallwebrtc_v1",
    }

    with pytest.raises(ValidationError, match="Input should be"):
        _claims(runtime="pipecat")


def test_call_claims_are_strict_immutable_and_exact() -> None:
    claims = _claims()

    assert set(VoiceCallClaims.model_fields) == {
        "user_id",
        "session_id",
        "agent_id",
        "voice_call_id",
        "trace_id",
        "runtime",
        "profile_id",
        "issued_at",
        "expires_at",
    }
    assert claims.runtime is VoiceRuntimeKind.LIVEKIT_V2
    with pytest.raises(ValidationError, match="frozen"):
        claims.runtime = VoiceRuntimeKind.LEGACY  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _claims(unexpected="forbidden")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "not-a-uuid"),
        ("agent_id", "20000000-0000-1000-8000-000000000002"),
        ("voice_call_id", "ABCDEFAB-0000-4000-8000-000000000003"),
        ("trace_id", "40000000-0000-4000-7000-000000000004"),
        ("profile_id", "contains spaces"),
        ("user_id", "contains\x00control"),
    ],
)
def test_call_claims_reject_invalid_identifiers(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _claims(**{field: value})


@pytest.mark.parametrize("user_id", ["contains spaces", "यूज़र १", "firebase|tenant:user"])
def test_call_claims_preserve_opaque_firebase_user_ids(user_id: str) -> None:
    assert _claims(user_id=user_id).user_id == user_id


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (NOW.replace(tzinfo=None), NOW + timedelta(minutes=5)),
        (NOW, NOW + timedelta(seconds=29)),
        (NOW, NOW + timedelta(seconds=901)),
        (NOW, NOW - timedelta(seconds=1)),
        (NOW, 1_786_534_100),
    ],
)
def test_call_claims_reject_invalid_expiry(issued_at: object, expires_at: object) -> None:
    with pytest.raises(ValidationError):
        _claims(issued_at=issued_at, expires_at=expires_at)


def test_assignment_union_selects_exactly_one_runtime_shape() -> None:
    adapter = TypeAdapter(VoiceRuntimeAssignment)

    livekit = adapter.validate_python(_livekit_values())
    pipecat = adapter.validate_python(_pipecat_values())

    assert isinstance(livekit, LiveKitVoiceRuntimeAssignment)
    assert isinstance(pipecat, PipecatVoiceRuntimeAssignment)
    assert set(LiveKitVoiceRuntimeAssignment.model_fields) == {
        "runtime",
        "claims",
        "event_protocol",
        "expires_at",
        "server_url",
        "room_name",
        "participant_token",
        "participant_identity",
        "agent_participant_identity",
        "dispatch_id",
        "worker_name",
        "event_topic",
    }
    assert set(PipecatVoiceRuntimeAssignment.model_fields) == {
        "runtime",
        "claims",
        "event_protocol",
        "expires_at",
        "webrtc_url",
        "peer_reservation_id",
    }


def test_assignments_reject_cross_runtime_fields_and_unknown_fields() -> None:
    adapter = TypeAdapter(VoiceRuntimeAssignment)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        adapter.validate_python(_livekit_values(webrtc_url=PIPECAT_LOCATOR))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        adapter.validate_python(_pipecat_values(room_name="livekit-room"))
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python({**_livekit_values(), "runtime": "legacy"})


def test_assignment_rejects_authoritative_runtime_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match its authoritative claims"):
        LiveKitVoiceRuntimeAssignment.model_validate(
            _livekit_values(claims=_claims(VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1))
        )

    with pytest.raises(ValidationError, match="does not match its authoritative claims"):
        PipecatVoiceRuntimeAssignment.model_validate(
            _pipecat_values(claims=_claims(VoiceRuntimeKind.LIVEKIT_V2))
        )


def test_assignment_expiry_is_bounded_by_authoritative_claims() -> None:
    with pytest.raises(ValidationError, match="outlives"):
        LiveKitVoiceRuntimeAssignment.model_validate(
            _livekit_values(expires_at=NOW + timedelta(minutes=6))
        )
    with pytest.raises(ValidationError, match="between 30 and 900"):
        PipecatVoiceRuntimeAssignment.model_validate(
            _pipecat_values(expires_at=NOW + timedelta(seconds=29))
        )


@pytest.mark.parametrize(
    "server_url",
    [
        "https://murmur-test.livekit.cloud",
        "wss://user:pass@murmur-test.livekit.cloud",
        "wss://murmur-test.livekit.cloud/path",
        "wss://murmur-test.livekit.cloud?token=secret",
        "wss://murmur-test.livekit.cloud#fragment",
    ],
)
def test_livekit_assignment_rejects_non_origin_server_urls(server_url: str) -> None:
    with pytest.raises(ValidationError):
        LiveKitVoiceRuntimeAssignment.model_validate(_livekit_values(server_url=server_url))


@pytest.mark.parametrize(
    "webrtc_url",
    [
        "http://voice.example.test/signal/opaque-token",
        "wss://voice.example.test/signal/opaque-token",
        "https://user:pass@voice.example.test/signal/opaque-token",
        "https://voice.example.test/",
        "https://voice.example.test/signal/opaque-token?call=1",
        "https://voice.example.test/signal/opaque-token#fragment",
        f"https://voice.example.test/signal/{CALL_ID}",
    ],
)
def test_pipecat_assignment_rejects_unsafe_or_identity_bearing_urls(webrtc_url: str) -> None:
    with pytest.raises(ValidationError):
        PipecatVoiceRuntimeAssignment.model_validate(_pipecat_values(webrtc_url=webrtc_url))


@pytest.mark.parametrize(
    "webrtc_url",
    [
        "https://voice.example.test/signal/firebase-user-1",
        "https://voice.example.test/signal/direct-cascade-v1",
        "https://voice.example.test/signal/peer-reservation-1",
        "https://voice.example.test/signal/firebase%252Duser%252D1",
        "https://firebase-user-1.voice.example.test/signal/opaque-token",
    ],
)
def test_pipecat_assignment_rejects_all_plain_or_encoded_scope_identity(
    webrtc_url: str,
) -> None:
    with pytest.raises(ValidationError, match="must not embed call identity"):
        PipecatVoiceRuntimeAssignment.model_validate(_pipecat_values(webrtc_url=webrtc_url))


def test_pipecat_assignment_rejects_deeply_nested_encoded_scope_identity() -> None:
    identity = "firebase-user-1"
    for _ in range(64):
        identity = identity.replace("%", "%25").replace("-", "%2D")

    with pytest.raises(ValidationError, match="must not embed call identity"):
        PipecatVoiceRuntimeAssignment.model_validate(
            _pipecat_values(webrtc_url=f"https://voice.example.test/signal/{identity}")
        )


def test_pipecat_assignment_allows_explicit_loopback_http_for_offline_qualification() -> None:
    assignment = PipecatVoiceRuntimeAssignment.model_validate(
        _pipecat_values(webrtc_url="http://127.0.0.1:8765/signal/offline-opaque-token")
    )
    assert assignment.webrtc_url.get_secret_value().startswith("http://127.0.0.1:8765/")


@pytest.mark.parametrize(
    ("builder", "secret"),
    [
        (_livekit_values, PARTICIPANT_TOKEN),
        (_pipecat_values, PIPECAT_LOCATOR),
    ],
)
def test_bearer_locators_are_redacted_from_repr_and_serialization(
    builder: object,
    secret: str,
) -> None:
    values = builder()  # type: ignore[operator]
    assignment = TypeAdapter(VoiceRuntimeAssignment).validate_python(values)

    assert secret not in repr(assignment)
    assert secret not in str(assignment.model_dump())
    assert secret not in str(assignment.model_dump(mode="json"))
    assert secret not in assignment.model_dump_json()


def test_pipecat_record_describes_single_use_without_owning_mutable_state() -> None:
    assert "single-use" in (PipecatVoiceRuntimeAssignment.__doc__ or "")
    assert not {"consumed", "used", "status"}.intersection(
        PipecatVoiceRuntimeAssignment.model_fields
    )
    assignment = PipecatVoiceRuntimeAssignment.model_validate(_pipecat_values())
    with pytest.raises(ValidationError, match="frozen"):
        assignment.peer_reservation_id = "peer-reservation-2"  # type: ignore[misc]


def test_bearer_contracts_require_an_explicit_browser_safe_projection() -> None:
    livekit = LiveKitVoiceRuntimeAssignment.model_validate(_livekit_values())
    pipecat = PipecatVoiceRuntimeAssignment.model_validate(_pipecat_values())

    assert livekit.participant_token.get_secret_value() == PARTICIPANT_TOKEN
    assert pipecat.webrtc_url.get_secret_value() == PIPECAT_LOCATOR
    assert livekit.model_dump(mode="json")["participant_token"] == "**********"
    assert pipecat.model_dump(mode="json")["webrtc_url"] == "**********"
    assert "deliberately reveal" in (LiveKitVoiceRuntimeAssignment.__doc__ or "")
    assert "deliberately reveal" in (PipecatVoiceRuntimeAssignment.__doc__ or "")


def test_terminal_result_is_strict_immutable_and_sdk_neutral() -> None:
    result = VoiceRuntimeTerminalResult(
        claims=_claims(),
        reason=VoiceRuntimeTerminalReason.CLIENT_DISCONNECTED,
        retryable=True,
        terminated_at=NOW + timedelta(minutes=2),
    )

    assert set(VoiceRuntimeTerminalResult.model_fields) == {
        "claims",
        "reason",
        "retryable",
        "terminated_at",
    }
    assert result.reason is VoiceRuntimeTerminalReason.CLIENT_DISCONNECTED
    with pytest.raises(ValidationError, match="valid boolean"):
        VoiceRuntimeTerminalResult(
            claims=_claims(),
            reason="runtime_stopped",
            retryable=1,
            terminated_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(ValidationError, match="before its claims"):
        VoiceRuntimeTerminalResult(
            claims=_claims(),
            reason="runtime_stopped",
            retryable=True,
            terminated_at=NOW - timedelta(seconds=1),
        )


def test_runtime_contract_module_has_no_sdk_provider_or_project_imports() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "murmur"
        / "voice"
        / "runtime_contracts.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots <= {
        "__future__",
        "datetime",
        "enum",
        "ipaddress",
        "pydantic",
        "typing",
        "urllib",
    }

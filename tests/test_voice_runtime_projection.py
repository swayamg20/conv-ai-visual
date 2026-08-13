from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceServer
from murmur.voice.runtime_contracts import (
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeKind,
)
from murmur.voice.runtime_projection import (
    PipecatBrowserVoiceAssignment,
    PipecatRuntimeProjectionForbidden,
    PipecatRuntimeProjectionUnavailable,
    project_pipecat_assignment_for_browser,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
SESSION_ID = "10000000-0000-4000-8000-000000000001"
AGENT_ID = "20000000-0000-4000-8000-000000000002"
CALL_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"
WEBRTC_URL = "https://voice.example.test/signal/M4bA8g7R2cQ9fN6xK3pL"
TURN_USERNAME = "1786622700:opaque-lease-user"
TURN_CREDENTIAL = "turn-password-that-must-not-be-logged"


def _claims(**overrides: object) -> VoiceCallClaims:
    values: dict[str, object] = {
        "user_id": "firebase-user-1",
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "voice_call_id": CALL_ID,
        "trace_id": TRACE_ID,
        "runtime": VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
        "profile_id": "pipecat-direct-cascade-v1",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return VoiceCallClaims.model_validate(values)


def _assignment(
    *,
    claims: VoiceCallClaims | None = None,
    expires_at: datetime | None = None,
    webrtc_url: str = WEBRTC_URL,
) -> PipecatVoiceRuntimeAssignment:
    authoritative = claims or _claims()
    return PipecatVoiceRuntimeAssignment(
        claims=authoritative,
        expires_at=expires_at or authoritative.expires_at,
        webrtc_url=webrtc_url,
        peer_reservation_id="peer-reservation-1",
    )


def _lease(*, claims: VoiceCallClaims | None = None) -> PipecatIceLease:
    authoritative = claims or _claims()
    return PipecatIceLease(
        claims=authoritative,
        provider_id="coturn-rest-v1",
        expires_at=authoritative.expires_at,
        ice_servers=(
            PipecatIceServer(urls=("stun:stun.example.test:3478",)),
            PipecatIceServer(
                urls=("turns:turn.example.test:5349?transport=tcp",),
                username=TURN_USERNAME,
                credential=TURN_CREDENTIAL,
            ),
        ),
    )


def _project(
    *,
    assignment: PipecatVoiceRuntimeAssignment | None = None,
    lease: PipecatIceLease | None = None,
    authenticated_user_id: str = "firebase-user-1",
    session_id: str = SESSION_ID,
    voice_call_id: str = CALL_ID,
) -> PipecatBrowserVoiceAssignment:
    return project_pipecat_assignment_for_browser(
        authenticated_user_id=authenticated_user_id,
        session_id=session_id,
        voice_call_id=voice_call_id,
        assignment=assignment or _assignment(),
        ice_lease=lease or _lease(),
        now=NOW + timedelta(seconds=1),
    )


def test_authenticated_projection_reveals_only_exact_browser_assignment() -> None:
    assignment = _assignment()
    lease = _lease()

    projected = _project(assignment=assignment, lease=lease)
    payload = projected.model_dump(mode="json")

    assert payload == {
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": "pipecat-direct-cascade-v1",
        "event_protocol": "rtvi-murmur-v2",
        "expires_at": "2026-08-13T12:05:00Z",
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "voice_call_id": CALL_ID,
        "trace_id": TRACE_ID,
        "webrtc_url": WEBRTC_URL,
        "peer_reservation_id": "peer-reservation-1",
        "ice_servers": [
            {
                "urls": ["stun:stun.example.test:3478"],
                "username": None,
                "credential": None,
                "credentialType": "password",
            },
            {
                "urls": ["turns:turn.example.test:5349?transport=tcp"],
                "username": TURN_USERNAME,
                "credential": TURN_CREDENTIAL,
                "credentialType": "password",
            },
        ],
    }
    assert not {
        "user_id",
        "claims",
        "issued_at",
        "provider_id",
        "coturn_shared_secret",
    }.intersection(payload)


def test_browser_and_pipecat_server_ice_views_are_byte_exact() -> None:
    lease = _lease()
    browser = _project(lease=lease).model_dump(mode="json")["ice_servers"]
    server = lease.to_pipecat_ice_servers()

    assert browser == [
        {
            "urls": entry.urls,
            "username": entry.username,
            "credential": entry.credential,
            "credentialType": entry.credentialType,
        }
        for entry in server
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"authenticated_user_id": "another-user"},
        {"session_id": "50000000-0000-4000-8000-000000000005"},
        {"voice_call_id": "60000000-0000-4000-8000-000000000006"},
        {"authenticated_user_id": 7},
    ],
)
def test_projection_rejects_every_authenticated_scope_mismatch_generically(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(PipecatRuntimeProjectionForbidden, match=r"^Forbidden$") as captured:
        _project(**overrides)  # type: ignore[arg-type]

    error = str(captured.value)
    assert "another-user" not in error
    assert SESSION_ID not in error
    assert CALL_ID not in error


def test_projection_rejects_claim_or_expiry_decoupling_as_unavailable() -> None:
    other_claims = _claims(trace_id="70000000-0000-4000-8000-000000000007")
    with pytest.raises(PipecatRuntimeProjectionUnavailable, match="unavailable"):
        _project(lease=_lease(claims=other_claims))

    claims = _claims()
    shorter_assignment = _assignment(
        claims=claims,
        expires_at=claims.issued_at + timedelta(minutes=4),
    )
    with pytest.raises(PipecatRuntimeProjectionUnavailable, match="unavailable"):
        _project(assignment=shorter_assignment, lease=_lease(claims=claims))


def test_projection_rejects_expired_assignment_and_non_utc_clock() -> None:
    assignment = _assignment()
    lease = _lease()
    common = {
        "authenticated_user_id": "firebase-user-1",
        "session_id": SESSION_ID,
        "voice_call_id": CALL_ID,
        "assignment": assignment,
        "ice_lease": lease,
    }
    with pytest.raises(PipecatRuntimeProjectionUnavailable, match="unavailable"):
        project_pipecat_assignment_for_browser(
            **common,
            now=assignment.expires_at,
        )
    with pytest.raises(PipecatRuntimeProjectionUnavailable, match="unavailable"):
        project_pipecat_assignment_for_browser(
            **common,
            now=NOW.replace(tzinfo=None),
        )


@pytest.mark.parametrize(
    "webrtc_url",
    [
        f"https://voice.example.test/signal/{CALL_ID}",
        "https://voice.example.test/signal/firebase%252Duser%252D1",
        "https://voice.example.test/signal/opaque?token=secret",
        "https://user:secret@voice.example.test/signal/opaque",
    ],
)
def test_projection_cannot_be_constructed_from_identity_bearing_or_token_query_urls(
    webrtc_url: str,
) -> None:
    with pytest.raises(ValidationError):
        _assignment(webrtc_url=webrtc_url)


def test_generic_source_repr_and_dumps_remain_redacted_before_explicit_projection() -> None:
    assignment = _assignment()
    lease = _lease()
    source_rendered = "\n".join(
        (
            repr(assignment),
            assignment.model_dump_json(),
            repr(lease),
            lease.model_dump_json(),
        )
    )

    assert WEBRTC_URL not in source_rendered
    assert TURN_USERNAME not in source_rendered
    assert TURN_CREDENTIAL not in source_rendered
    assert "coturn-rest-v1" not in source_rendered

    projected = _project(assignment=assignment, lease=lease)
    assert WEBRTC_URL not in repr(projected)
    assert TURN_USERNAME not in repr(projected)
    assert TURN_CREDENTIAL not in repr(projected)
    assert projected.model_dump(mode="json")["webrtc_url"] == WEBRTC_URL

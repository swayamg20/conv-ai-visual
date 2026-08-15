from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiortc.rtcicetransport import connection_kwargs
from murmur.voice.pipecat_ice import (
    LoopbackDirectIceLeaseIssuer,
    PipecatIceLease,
    PipecatIceLeaseUnavailable,
    PipecatIceServer,
    resolve_pipecat_ice_lease_issuer,
)
from murmur.voice.runtime_contracts import VoiceCallClaims, VoiceRuntimeKind
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import COTURN_TURNS_URL  # noqa: E402

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
SESSION_ID = "10000000-0000-4000-8000-000000000001"
AGENT_ID = "20000000-0000-4000-8000-000000000002"
CALL_ID = "30000000-0000-4000-8000-000000000003"
TRACE_ID = "40000000-0000-4000-8000-000000000004"
TURN_USERNAME = "1786622700:opaque-lease-user"
TURN_CREDENTIAL = "turn-password-that-must-not-be-logged"


def _claims(
    runtime: VoiceRuntimeKind = VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
    **overrides: object,
) -> VoiceCallClaims:
    values: dict[str, object] = {
        "user_id": "firebase-user-1",
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "voice_call_id": CALL_ID,
        "trace_id": TRACE_ID,
        "runtime": runtime,
        "profile_id": "pipecat-direct-cascade-v1",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return VoiceCallClaims.model_validate(values)


def _ice_servers() -> tuple[PipecatIceServer, ...]:
    return (
        PipecatIceServer(urls=("stun:stun.example.test:3478",)),
        PipecatIceServer(
            urls=("turns:turn.example.test:5349?transport=tcp",),
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ),
    )


def _lease(**overrides: object) -> PipecatIceLease:
    claims = overrides.pop("claims", _claims())
    values: dict[str, object] = {
        "claims": claims,
        "provider_id": "coturn-rest-v1",
        "expires_at": claims.expires_at,
        "ice_servers": _ice_servers(),
    }
    values.update(overrides)
    return PipecatIceLease.model_validate(values)


def test_lease_projects_exact_fresh_pipecat_aiortc_servers() -> None:
    lease = _lease()

    first = lease.to_pipecat_ice_servers()
    second = lease.to_pipecat_ice_servers()

    assert first is not second
    assert first[0] is not second[0]
    assert first[0].urls == list(lease.ice_servers[0].urls)
    assert first[0].username is None
    assert first[0].credential is None
    assert first[1].urls == list(lease.ice_servers[1].urls)
    assert first[1].username == TURN_USERNAME
    assert first[1].credential == TURN_CREDENTIAL
    assert first[1].credentialType == "password"
    assert first[1].username is not None
    assert first[1].credential is not None
    assert first[1].username.encode() == TURN_USERNAME.encode()
    assert first[1].credential.encode() == TURN_CREDENTIAL.encode()

    first[1].username = "mutated"
    first[1].urls.append("turn:attacker.example:3478")
    assert lease.ice_servers[1].username is not None
    assert lease.ice_servers[1].username.get_secret_value() == TURN_USERNAME
    assert lease.ice_servers[1].urls == ("turns:turn.example.test:5349?transport=tcp",)


def test_sdk_projection_is_an_explicit_ephemeral_secret_bearing_sink() -> None:
    sdk_server = _lease().to_pipecat_ice_servers()[1]

    # aiortc must receive the actual values.  Unlike the retained lease, this
    # third-party transport object is deliberately not safe to log or dump.
    assert sdk_server.username == TURN_USERNAME
    assert sdk_server.credential == TURN_CREDENTIAL
    assert TURN_USERNAME in repr(sdk_server)
    assert TURN_CREDENTIAL in repr(sdk_server)


def test_pinned_aiortc_maps_exact_e2e_turns_url_to_tls_over_tcp() -> None:
    server = PipecatIceServer(
        urls=(COTURN_TURNS_URL,),
        username=TURN_USERNAME,
        credential=TURN_CREDENTIAL,
    ).to_pipecat_ice_server()

    assert connection_kwargs([server]) == {
        "turn_server": ("127.0.0.1", 5349),
        "turn_ssl": True,
        "turn_transport": "tcp",
        "turn_username": TURN_USERNAME,
        "turn_password": TURN_CREDENTIAL,
    }


def test_lease_and_entries_redact_credentials_claims_and_provider_from_generic_dumps() -> None:
    lease = _lease()
    rendered = "\n".join(
        (
            repr(lease),
            repr(lease.ice_servers[1]),
            str(lease.model_dump()),
            str(lease.model_dump(mode="json")),
            lease.model_dump_json(),
        )
    )

    assert TURN_USERNAME not in rendered
    assert TURN_CREDENTIAL not in rendered
    assert "firebase-user-1" not in rendered
    assert "coturn-rest-v1" not in rendered
    assert set(lease.model_dump()) == {"expires_at", "ice_servers"}


def test_lease_snapshots_secret_inputs_before_later_projection() -> None:
    from pydantic import SecretStr

    username = SecretStr(TURN_USERNAME)
    credential = SecretStr(TURN_CREDENTIAL)
    server = PipecatIceServer(
        urls=("turns:turn.example.test:5349?transport=tcp",),
        username=username,
        credential=credential,
    )

    username._secret_value = "mutated-user"
    credential._secret_value = "mutated-password"
    sdk_server = server.to_pipecat_ice_server()

    assert sdk_server.username == TURN_USERNAME
    assert sdk_server.credential == TURN_CREDENTIAL
    assert server.username is not None
    with pytest.raises(AttributeError, match="immutable"):
        server.username._secret_value = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "url",
    [
        "http://turn.example.test:3478",
        "turn:user@turn.example.test:3478",
        "turn:turn.example.test:3478?credential=secret",
        "turn:turn.example.test:3478#secret",
        "turn:turn.example.test:3478?transport=quic",
        "turns:turn.example.test:5349?transport=udp",
        "stun:stun.example.test:3478?transport=udp",
        "stuns:stun.example.test:5349",
        "turn:turn%2Eexample%2Etest:3478",
        "turn:turn.example.test:0",
        "turn:turn.example.test:99999",
        " turn:turn.example.test:3478",
        "turn:turn.example.test:3478\n",
        "TURN:turn.example.test:3478",
    ],
)
def test_ice_urls_fail_closed_on_unsafe_or_sdk_unsupported_shapes(url: str) -> None:
    with pytest.raises(ValidationError):
        PipecatIceServer(
            urls=(url,),
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        )


def test_stun_and_turn_credential_shapes_are_not_interchangeable() -> None:
    with pytest.raises(ValidationError, match="STUN entries must not contain"):
        PipecatIceServer(
            urls=("stun:stun.example.test:3478",),
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        )
    with pytest.raises(ValidationError, match="TURN entries require"):
        PipecatIceServer(urls=("turn:turn.example.test:3478",))
    with pytest.raises(ValidationError, match="must not mix"):
        PipecatIceServer(
            urls=("stun:stun.example.test:3478", "turn:turn.example.test:3478"),
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        )


@pytest.mark.parametrize(
    ("username", "credential"),
    [
        ("", TURN_CREDENTIAL),
        (TURN_USERNAME, ""),
        (" user", TURN_CREDENTIAL),
        (TURN_USERNAME, "password\nsecret"),
        (1, TURN_CREDENTIAL),
    ],
)
def test_turn_credentials_are_strict_and_control_character_free(
    username: object,
    credential: object,
) -> None:
    with pytest.raises(ValidationError):
        PipecatIceServer(
            urls=("turn:turn.example.test:3478",),
            username=username,
            credential=credential,
        )


def test_lease_is_exactly_claim_bound_and_ttl_coupled() -> None:
    claims = _claims()
    with pytest.raises(ValidationError, match="expiry must equal"):
        _lease(claims=claims, expires_at=claims.expires_at - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="requires Pipecat"):
        _lease(claims=_claims(VoiceRuntimeKind.LIVEKIT_V2))
    with pytest.raises(ValidationError, match=r"timezone|UTC"):
        _lease(expires_at=claims.expires_at.replace(tzinfo=None))

    lease = _lease()
    with pytest.raises(ValidationError, match="frozen"):
        lease.expires_at = claims.expires_at + timedelta(seconds=1)  # type: ignore[misc]


def test_lease_rejects_more_ice_urls_than_pinned_aiortc_can_honor() -> None:
    with pytest.raises(ValidationError, match="at most one STUN and one TURN"):
        _lease(
            ice_servers=(
                PipecatIceServer(urls=("stun:stun-one.example.test:3478",)),
                PipecatIceServer(urls=("stun:stun-two.example.test:3478",)),
            )
        )
    with pytest.raises(ValidationError, match="at most one STUN and one TURN"):
        _lease(
            ice_servers=(
                PipecatIceServer(
                    urls=(
                        "turn:turn-one.example.test:3478",
                        "turns:turn-two.example.test:5349?transport=tcp",
                    ),
                    username=TURN_USERNAME,
                    credential=TURN_CREDENTIAL,
                ),
            )
        )


@pytest.mark.asyncio
async def test_empty_direct_lease_is_available_only_for_loopback_without_provider() -> None:
    issuer = resolve_pipecat_ice_lease_issuer("http://127.0.0.1:8765/signal", None)
    localhost_issuer = resolve_pipecat_ice_lease_issuer(
        "https://localhost:8765/signal",
        None,
    )

    lease = await issuer.issue(_claims())
    assert lease.ice_servers == ()
    assert lease.expires_at == lease.claims.expires_at
    assert (await localhost_issuer.issue(_claims())).ice_servers == ()

    with pytest.raises(PipecatIceLeaseUnavailable, match="required"):
        resolve_pipecat_ice_lease_issuer("https://voice.example.test/signal", None)


@pytest.mark.asyncio
async def test_explicit_provider_is_wrapped_and_validated_for_non_loopback_configuration() -> None:
    class _PublicIceIssuer:
        async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
            return _lease(claims=claims)

    issuer = resolve_pipecat_ice_lease_issuer(
        "https://voice.example.test/signal",
        _PublicIceIssuer(),
    )
    assert (await issuer.issue(_claims())).ice_servers == _ice_servers()


@pytest.mark.asyncio
async def test_empty_direct_lease_cannot_be_issued_for_public_signaling() -> None:
    lease = _lease(ice_servers=())
    issuer = resolve_pipecat_ice_lease_issuer(
        "https://voice.example.test/signal",
        LoopbackDirectIceLeaseIssuer(),
    )
    with pytest.raises(PipecatIceLeaseUnavailable, match="explicit TURN"):
        await issuer.issue(_claims())
    lease.require_compatible_signaling_base_url("http://127.0.0.1:8765/signal")


@pytest.mark.asyncio
async def test_explicit_issuer_must_return_the_exact_requested_claims() -> None:
    other_claims = _claims(trace_id="70000000-0000-4000-8000-000000000007")

    class _WrongScopeIssuer:
        async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
            del claims
            return _lease(claims=other_claims)

    issuer = resolve_pipecat_ice_lease_issuer(
        "https://voice.example.test/signal",
        _WrongScopeIssuer(),
    )
    with pytest.raises(PipecatIceLeaseUnavailable, match="unavailable"):
        await issuer.issue(_claims())


def test_explicit_provider_does_not_authorize_public_plaintext_signaling() -> None:
    class _PublicIceIssuer:
        async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
            return _lease(claims=claims)

    with pytest.raises(PipecatIceLeaseUnavailable, match="HTTPS"):
        resolve_pipecat_ice_lease_issuer(
            "http://voice.example.test/signal",
            _PublicIceIssuer(),
        )


@pytest.mark.asyncio
async def test_loopback_issuer_wraps_invalid_claim_scope_in_safe_error() -> None:
    with pytest.raises(PipecatIceLeaseUnavailable, match="unavailable") as captured:
        await LoopbackDirectIceLeaseIssuer().issue(_claims(VoiceRuntimeKind.LIVEKIT_V2))

    assert "livekit" not in str(captured.value).casefold()


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "ftp://127.0.0.1/signal",
        "http://voice.example.test/signal",
        "http://user:secret@127.0.0.1/signal",
        "http://127.0.0.1/signal?token=secret",
        "http://127.0.0.1/signal#token",
        "http://127.0.0.1/sig%6eal",
        "http://127.0.0.1/sig\\nal",
        "http://127.0.0.1/sig\tnal",
    ],
)
def test_issuer_resolution_rejects_unsafe_signaling_urls(base_url: str) -> None:
    with pytest.raises(PipecatIceLeaseUnavailable, match=r"invalid|HTTPS"):
        resolve_pipecat_ice_lease_issuer(base_url, None)

"""Guarded standalone Pipecat ASGI owner for deterministic loopback tests.

The environment is validated before any Murmur module is imported.  Only test
authentication and fake Pipecat profile construction are substituted; the
production bootstrap, signaling, SmallWebRTC handler, runtime starter,
transport, pipeline, repositories, and cleanup owners remain in the path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_TOPOLOGY_STATUS,
    COTURN_TURNS_URL,
    CoturnContractError,
    PipecatE2ENetworkMode,
    derive_turn_rest_credentials,
    parse_network_mode,
    read_private_coturn_configuration,
    validate_turn_tls_ca_file,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_ROOT = (_PROJECT_ROOT / "var" / "voice-pipecat-e2e").resolve()
_SIGNALING_PATH = "/api/voice/pipecat/signal"
_PROFILE_ID = "pipecat-fake-rtc-v1"
_RUNTIME = "pipecat_smallwebrtc_v1"
_AUTHORIZATION = "Bearer voice-e2e"
_ALLOWED_CORS_ORIGINS = {
    "http://127.0.0.1:3100",
    "http://localhost:3100",
}
_FORBIDDEN_PROVIDER_ENV_PREFIXES = (
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "COHERE_",
    "DEEPGRAM_",
    "ELEVENLABS_",
    "FIREBASE_",
    "GEMINI_",
    "GOOGLE_",
    "GROQ_",
    "HF_",
    "HUGGINGFACE_",
    "LIVEKIT_",
    "LLM_",
    "MEM0_",
    "MISTRAL_",
    "NEXT_PUBLIC_FIREBASE_",
    "NEXT_PUBLIC_LIVEKIT_",
    "OPENAI_",
    "SMART_TURN_",
    "TAVILY_",
    "TTS_",
)
_FORBIDDEN_PROVIDER_ENV_NAMES = {"GOOGLE_APPLICATION_CREDENTIALS"}
_FORBIDDEN_CREDENTIAL_SUFFIXES = (
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_API_SECRET",
    "_AUTH_TOKEN",
    "_CREDENTIALS",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_TOKEN",
)


@dataclass(frozen=True)
class _GuardedEnvironment:
    network: PipecatE2ENetworkMode
    turn_static_auth_secret: str | None = field(default=None, repr=False)


def _require_guarded_environment() -> _GuardedEnvironment:
    if os.getenv("MURMUR_E2E_MODE") != "1":
        raise RuntimeError("Pipecat E2E app requires MURMUR_E2E_MODE=1")
    if os.getenv("MURMUR_ENVIRONMENT") != "test":
        raise RuntimeError("Pipecat E2E app requires MURMUR_ENVIRONMENT=test")
    if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
        raise RuntimeError("Pipecat E2E app requires disabled dotenv loading")
    if os.getenv("VOICE_RUNTIME") != _RUNTIME:
        raise RuntimeError("Pipecat E2E app requires its exact runtime")
    if os.getenv("VOICE_V2_PROFILE_ID") != _PROFILE_ID:
        raise RuntimeError("Pipecat E2E app requires its exact fake profile")
    if any(
        value.strip()
        and (
            name in _FORBIDDEN_PROVIDER_ENV_NAMES
            or any(name.startswith(prefix) for prefix in _FORBIDDEN_PROVIDER_ENV_PREFIXES)
            or name.endswith(_FORBIDDEN_CREDENTIAL_SUFFIXES)
        )
        for name, value in os.environ.items()
    ):
        raise RuntimeError("Pipecat E2E app does not accept provider credentials")
    allowed_origins = tuple(
        origin.strip()
        for origin in os.getenv("ALLOWED_CORS_ORIGINS", "").split(",")
        if origin.strip()
    )
    if (
        len(allowed_origins) != len(_ALLOWED_CORS_ORIGINS)
        or set(allowed_origins) != _ALLOWED_CORS_ORIGINS
    ):
        raise RuntimeError("Pipecat E2E app requires its exact loopback browser origins")

    host = os.getenv("PIPECAT_HOST")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Pipecat E2E app requires a loopback bind host")
    try:
        port = int(os.getenv("PIPECAT_PORT", ""))
    except ValueError:
        raise RuntimeError("Pipecat E2E app port is invalid") from None
    if not 1 <= port <= 65_535:
        raise RuntimeError("Pipecat E2E app port is invalid")

    signaling = urlsplit(os.getenv("PIPECAT_SIGNALING_BASE_URL", ""))
    if (
        signaling.scheme != "http"
        or signaling.hostname not in {"127.0.0.1", "localhost", "::1"}
        or signaling.port != port
        or signaling.path.rstrip("/") != _SIGNALING_PATH
        or signaling.query
        or signaling.fragment
        or signaling.username is not None
        or signaling.password is not None
    ):
        raise RuntimeError("Pipecat E2E app requires its exact loopback signaling URL")

    database_url = os.getenv("MURMUR_DATABASE_URL", "")
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        raise RuntimeError("Pipecat E2E app requires a file-backed SQLite database")
    database_path = Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()
    try:
        database_path.relative_to(_DATABASE_ROOT)
    except ValueError:
        raise RuntimeError("Pipecat E2E database must stay under var/voice-pipecat-e2e") from None

    try:
        network = parse_network_mode(os.getenv("MURMUR_PIPECAT_E2E_NETWORK"))
    except CoturnContractError:
        raise RuntimeError("Pipecat E2E app network mode is invalid") from None
    relay_names = {
        "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE",
        "SSL_CERT_FILE",
    }
    trust_override_names = {
        "CURL_CA_BUNDLE",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSLKEYLOGFILE",
    }
    unexpected_relay_names = {
        name
        for name in os.environ
        if (
            name.startswith("TURN_")
            or name.startswith("COTURN_")
            or name.startswith("MURMUR_PIPECAT_E2E_TURN_")
            or name.startswith("MURMUR_PIPECAT_E2E_COTURN_")
        )
        and name not in relay_names
    }
    if unexpected_relay_names or any(name in os.environ for name in trust_override_names):
        raise RuntimeError("Pipecat E2E app does not accept ambient TURN configuration")
    if network is PipecatE2ENetworkMode.DIRECT:
        if any(name in os.environ for name in relay_names):
            raise RuntimeError("direct Pipecat E2E does not accept relay material")
        return _GuardedEnvironment(network=network)

    expected_run_dir = database_path.parent
    config_value = os.getenv("MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE")
    certificate_value = os.getenv("SSL_CERT_FILE")
    if config_value is None or certificate_value is None:
        raise RuntimeError("relay-tls Pipecat E2E material is unavailable")
    try:
        configuration = Path(config_value)
        certificate = Path(certificate_value)
        secret = read_private_coturn_configuration(
            configuration,
            expected_run_dir=expected_run_dir,
        )
        validate_turn_tls_ca_file(certificate, expected_run_dir=expected_run_dir)
    except (CoturnContractError, OSError, ValueError):
        raise RuntimeError("relay-tls Pipecat E2E material is unavailable") from None
    return _GuardedEnvironment(
        network=network,
        turn_static_auth_secret=secret,
    )


_guarded_environment = _require_guarded_environment()

from fastapi import HTTPException, Request  # noqa: E402
from murmur.api.pipecat_schemas import PipecatSessionRequest  # noqa: E402
from murmur.persistence import init_db  # noqa: E402
from murmur.persistence.database import engine  # noqa: E402
from murmur.persistence.models import AgentModel, SessionModel, UserModel  # noqa: E402
from murmur.voice.pipecat_app import create_app  # noqa: E402
from murmur.voice.pipecat_bootstrap import PipecatBootstrapSettings  # noqa: E402
from murmur.voice.pipecat_composition import (  # noqa: E402
    PipecatCompositionSettings,
    create_pipecat_composition,
)
from murmur.voice.pipecat_fake_rtc import (  # noqa: E402
    EVIDENCE_PATH_ENV,
    PIPECAT_FAKE_RTC_PROFILE_ID,
    build_pipecat_fake_rtc_provider_from_environment,
    summarize_pipecat_fake_evidence,
)
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceServer  # noqa: E402
from murmur.voice.pipecat_signaling import (  # noqa: E402
    PipecatReservationState,
    PipecatSignalingNotFound,
    PipecatSignalingSettings,
)
from murmur.voice.runtime_contracts import VoiceCallClaims  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

E2E_USER_ID = "voice-e2e-user"
E2E_USER_EMAIL = "voice-e2e@localhost.invalid"
E2E_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76"
E2E_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578"


@dataclass(frozen=True)
class _RelayTlsIceLeaseIssuer:
    static_auth_secret: str = field(repr=False)

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
        credentials = derive_turn_rest_credentials(
            static_auth_secret=self.static_auth_secret,
            voice_call_id=claims.voice_call_id,
            expires_at=claims.expires_at,
            now=datetime.now(UTC),
        )
        return PipecatIceLease(
            claims=claims,
            provider_id="e2e-coturn-rest-v1",
            expires_at=claims.expires_at,
            ice_servers=(
                PipecatIceServer(
                    urls=(COTURN_TURNS_URL,),
                    username=credentials.username,
                    credential=credentials.credential,
                ),
            ),
        )


def _seed_owned_scope() -> None:
    init_db()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if session.get(UserModel, E2E_USER_ID) is None:
            session.add(
                UserModel(
                    id=E2E_USER_ID,
                    email=E2E_USER_EMAIL,
                    name="Pipecat E2E",
                )
            )
        if session.get(AgentModel, E2E_AGENT_ID) is None:
            session.add(
                AgentModel(
                    id=E2E_AGENT_ID,
                    user_id=E2E_USER_ID,
                    name="Deterministic Pipecat RTC agent",
                    description="Loopback-only SmallWebRTC media validation agent",
                    system_prompt="Respond using the deterministic Pipecat RTC profile.",
                    capabilities_json="[]",
                )
            )
        if session.get(SessionModel, E2E_SESSION_ID) is None:
            session.add(
                SessionModel(
                    id=E2E_SESSION_ID,
                    user_id=E2E_USER_ID,
                    agent_id=E2E_AGENT_ID,
                    title="Pipecat RTC validation",
                )
            )
        session.commit()


async def _e2e_authenticator(request: Request) -> Mapping[str, object] | None:
    values = request.headers.getlist("authorization")
    if values != [_AUTHORIZATION]:
        return None
    return {"id": E2E_USER_ID}


def _composition_settings() -> PipecatCompositionSettings:
    profile_id = PIPECAT_FAKE_RTC_PROFILE_ID
    signaling_url = os.environ["PIPECAT_SIGNALING_BASE_URL"]
    origins = tuple(
        origin.strip()
        for origin in os.getenv("ALLOWED_CORS_ORIGINS", "").split(",")
        if origin.strip()
    )
    signaling = PipecatSignalingSettings(
        signaling_base_url=signaling_url,
        profile_id=profile_id,
        reservation_ttl_seconds=120,
        repository_timeout_seconds=2.0,
        signaling_timeout_seconds=10.0,
        cleanup_timeout_seconds=5.0,
        max_reservations=32,
        max_active_calls=1,
        allowed_origins=origins,
    )
    bootstrap = PipecatBootstrapSettings(
        profile_id=profile_id,
        assignment_ttl_seconds=120,
        repository_timeout_seconds=2.0,
        operation_timeout_seconds=10.0,
        coordination_timeout_seconds=5.0,
        max_concurrent_bootstraps=4,
        max_active_calls=1,
        max_call_assignments=32,
    )
    return PipecatCompositionSettings(
        signaling=signaling,
        bootstrap=bootstrap,
        cascade=None,
        projection_cleanup_timeout_seconds=5.0,
        runtime_cleanup_timeout_seconds=5.0,
        runtime_readiness_timeout_seconds=10.0,
        active_call_idle_timeout_seconds=30.0,
    )


_provider = build_pipecat_fake_rtc_provider_from_environment()
_evidence_path = Path(os.environ[EVIDENCE_PATH_ENV]).resolve()
_ice_lease_issuer = None
if _guarded_environment.network is PipecatE2ENetworkMode.RELAY_TLS:
    if _guarded_environment.turn_static_auth_secret is None:
        raise RuntimeError("relay-tls Pipecat E2E material is unavailable")
    _ice_lease_issuer = _RelayTlsIceLeaseIssuer(
        _guarded_environment.turn_static_auth_secret,
    )
_composition = create_pipecat_composition(
    _composition_settings(),
    profile_provider=_provider,
    ice_lease_issuer=_ice_lease_issuer,
)
app = create_app(
    composition=_composition,
    authenticator=_e2e_authenticator,
    database_initializer=_seed_owned_scope,
)


@app.get("/_e2e/health", include_in_schema=False)
async def e2e_health() -> dict[str, object]:
    health: dict[str, object] = {
        "schema_version": 1,
        "ok": True,
        "runtime": _RUNTIME,
        "profile_id": _PROFILE_ID,
        "agent_id": E2E_AGENT_ID,
        "session_id": E2E_SESSION_ID,
        "network": _guarded_environment.network.evidence_name,
        "providers": "fake",
        "livekit_imported": any(
            name == "livekit" or name.startswith("livekit.") for name in sys.modules
        ),
        "cost": "unmeasured",
    }
    if _guarded_environment.network is PipecatE2ENetworkMode.RELAY_TLS:
        health.update(
            {
                "qualification": "unavailable",
                "topology_status": COTURN_TOPOLOGY_STATUS,
            }
        )
    return health


@app.post("/_e2e/pipecat/status", include_in_schema=False)
async def e2e_status(body: PipecatSessionRequest, request: Request) -> dict[str, object]:
    identity = getattr(request.state, "pipecat_user", None)
    if not isinstance(identity, Mapping) or identity.get("id") != E2E_USER_ID:
        raise HTTPException(status_code=403, detail="Forbidden")
    if body.session_id != E2E_SESSION_ID:
        raise HTTPException(status_code=404, detail="Voice session was not found")
    try:
        snapshot = await _composition.signaling_service.status_call(
            user_id=E2E_USER_ID,
            session_id=body.session_id,
            voice_call_id=body.voice_call_id,
        )
    except PipecatSignalingNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice call was not found") from exc

    media = summarize_pipecat_fake_evidence(
        _evidence_path,
        body.voice_call_id,
    )
    bootstrap = _composition.bootstrap_service
    signaling = _composition.signaling_service
    retained_record = next(
        (
            record
            for record in getattr(signaling, "_reservations", {}).values()
            if record.claims.voice_call_id == body.voice_call_id
        ),
        None,
    )
    control_plane = {
        "bootstrap_active_assignment_count": bootstrap.active_assignment_count,
        "bootstrap_active_lock_count": bootstrap.active_lock_count,
        "signaling_active_call_count": signaling.active_call_count,
        "runtime_handle_retained": bool(
            retained_record is not None and retained_record.runtime_handle is not None
        ),
        "cleanup_retry_pending": _task_pending(
            getattr(retained_record, "cleanup_retry_task", None)
        ),
        "runtime_observer_pending": _task_pending(
            getattr(retained_record, "runtime_observer_task", None)
        ),
        "expiry_pending": _task_pending(getattr(retained_record, "expiry_task", None)),
        "trusted_release_pending": _task_pending(
            getattr(retained_record, "trusted_release_task", None)
        ),
    }
    terminal = snapshot.terminal_result
    reservation = {
        "state": snapshot.state.value,
        "cleanup_complete": snapshot.cleanup_complete,
        "terminal_reason": terminal.reason.value if terminal is not None else None,
        "retryable": terminal.retryable if terminal is not None else None,
    }
    control_plane_clean = control_plane == {
        "bootstrap_active_assignment_count": 0,
        "bootstrap_active_lock_count": 0,
        "signaling_active_call_count": 0,
        "runtime_handle_retained": False,
        "cleanup_retry_pending": False,
        "runtime_observer_pending": False,
        "expiry_pending": False,
        "trusted_release_pending": False,
    }
    passed = _qualification_passed(
        network=_guarded_environment.network,
        reservation_terminal=snapshot.state is PipecatReservationState.TERMINAL,
        cleanup_complete=snapshot.cleanup_complete,
        control_plane_clean=control_plane_clean,
        media_contract_satisfied=media["media_contract_satisfied"] is True,
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "pending",
        "runtime": _RUNTIME,
        "profile_id": _PROFILE_ID,
        "session_id": body.session_id,
        "voice_call_id": body.voice_call_id,
        "reservation": reservation,
        "control_plane": control_plane,
        "fake_media": media,
    }


def _qualification_passed(
    *,
    network: PipecatE2ENetworkMode,
    reservation_terminal: bool,
    cleanup_complete: bool,
    control_plane_clean: bool,
    media_contract_satisfied: bool,
) -> bool:
    """Keep Checkpoint A relay-TLS contract runs categorically unqualified."""

    return (
        network is PipecatE2ENetworkMode.DIRECT
        and reservation_terminal is True
        and cleanup_complete is True
        and control_plane_clean is True
        and media_contract_satisfied is True
    )


def _task_pending(task: object) -> bool:
    return bool(task is not None and callable(getattr(task, "done", None)) and not task.done())


__all__ = [
    "E2E_AGENT_ID",
    "E2E_SESSION_ID",
    "E2E_USER_ID",
    "app",
]

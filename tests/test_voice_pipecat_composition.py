"""Composition and shutdown tests for the standalone Pipecat process."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from loguru import logger as loguru_logger
from murmur.voice import pipecat_app
from murmur.voice.pipecat_app import PipecatServerSettings, uvicorn_options
from murmur.voice.pipecat_bootstrap import PipecatBootstrapResult, PipecatBootstrapSettings
from murmur.voice.pipecat_composition import (
    PipecatApplicationComposition,
    PipecatCompositionSettings,
    PipecatCompositionUnavailable,
    create_pipecat_composition,
    create_pipecat_peer_handler,
)
from murmur.voice.pipecat_ice import PipecatIceLease, PipecatIceServer
from murmur.voice.pipecat_signaling import (
    PipecatCorsContract,
    PipecatIceCandidate,
    PipecatOfferAnswer,
    PipecatOfferRequest,
    PipecatPatchRequest,
    PipecatSignalingSettings,
)
from murmur.voice.runtime_contracts import (
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeKind,
    VoiceRuntimeTerminalReason,
    VoiceRuntimeTerminalResult,
)
from murmur.voice.runtime_projection import (
    PipecatRuntimeProjectionForbidden,
    PipecatRuntimeProjectionUnavailable,
)
from pydantic import SecretStr

USER_ID = "firebase-user-composition-1"
SESSION_ID = "50000000-0000-4000-8000-000000000005"
AGENT_ID = "60000000-0000-4000-8000-000000000006"
CALL_ID = "70000000-0000-4000-8000-000000000007"
TRACE_ID = "80000000-0000-4000-8000-000000000008"
TOKEN = "opaque-composition-token-" + ("b" * 48)
PC_ID = "SmallWebRTCConnection#composition-peer"
ORIGIN = "https://murmur.example.test"
PROFILE_ID = "pipecat-direct-cascade-v1"
SECRET = "composition-secret-that-must-never-leak"
PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE = "pipecat.transports.smallwebrtc.request_handler"
AIORTC_PEER_CONNECTION_LOG_NAMESPACE = "aiortc.rtcpeerconnection"


def _claims(*, user_id: str = USER_ID) -> VoiceCallClaims:
    issued_at = datetime.now(UTC)
    return VoiceCallClaims(
        user_id=user_id,
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        voice_call_id=CALL_ID,
        trace_id=TRACE_ID,
        runtime=VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1,
        profile_id=PROFILE_ID,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
    )


def _assignment(claims: VoiceCallClaims) -> PipecatVoiceRuntimeAssignment:
    return PipecatVoiceRuntimeAssignment(
        claims=claims,
        webrtc_url=("https://voice.example.test/api/voice/pipecat/signal/" + TOKEN),
        peer_reservation_id="peer-reservation-composition-1",
        expires_at=claims.expires_at,
    )


def _lease(claims: VoiceCallClaims) -> PipecatIceLease:
    return PipecatIceLease(
        claims=claims,
        provider_id="test-coturn",
        expires_at=claims.expires_at,
        ice_servers=(
            PipecatIceServer(urls=("stun:stun.example.test:3478",)),
            PipecatIceServer(
                urls=("turns:turn.example.test:5349?transport=tcp",),
                username=SecretStr("turn-user"),
                credential=SecretStr(SECRET),
            ),
        ),
    )


def _bootstrap_result(*, user_id: str = USER_ID) -> PipecatBootstrapResult:
    claims = _claims(user_id=user_id)
    return PipecatBootstrapResult(
        assignment=_assignment(claims),
        ice_lease=_lease(claims),
    )


def _terminal_result(claims: VoiceCallClaims | None = None) -> VoiceRuntimeTerminalResult:
    authoritative = claims or _claims()
    return VoiceRuntimeTerminalResult(
        claims=authoritative,
        reason=VoiceRuntimeTerminalReason.USER_ENDED,
        retryable=False,
        terminated_at=authoritative.issued_at + timedelta(seconds=1),
    )


class _Bootstrap:
    def __init__(self, result: object | None = None, events: list[str] | None = None) -> None:
        self.result = result if result is not None else _bootstrap_result()
        self.events = events if events is not None else []
        self.bootstrap_calls: list[dict[str, str]] = []
        self.release_calls: list[dict[str, str]] = []
        self.release_entered = asyncio.Event()
        self.release_gate: asyncio.Event | None = None
        self.release_error: BaseException | None = None
        self.close_failures = 0
        self.close_entered = asyncio.Event()
        self.close_gate: asyncio.Event | None = None

    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> object:
        self.bootstrap_calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "voice_call_id": voice_call_id,
            }
        )
        return self.result

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult | None:
        self.release_calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "voice_call_id": voice_call_id,
            }
        )
        self.release_entered.set()
        if self.release_gate is not None:
            await self.release_gate.wait()
        if self.release_error is not None:
            raise self.release_error
        return None

    async def aclose(self) -> None:
        self.events.append("bootstrap-close")
        self.close_entered.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError(f"bootstrap close failed: {SECRET}")


class _Signaling:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.close_failures = 0
        self.close_entered = asyncio.Event()

    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        self.calls.append(("offer", {"token": token, "user_id": user_id, "request": request}))
        return PipecatOfferAnswer(sdp="answer-sdp", type="answer", pc_id=PC_ID)

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None:
        self.calls.append(("patch", {"token": token, "user_id": user_id, "request": request}))

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> VoiceRuntimeTerminalResult:
        self.calls.append(("delete", {"token": token, "user_id": user_id, "pc_id": pc_id}))
        return _terminal_result()

    async def aclose(self) -> None:
        self.events.append("signaling-close")
        self.close_entered.set()
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError(f"signaling close failed: {SECRET}")


def _composition(
    *,
    bootstrap: _Bootstrap | None = None,
    signaling: _Signaling | None = None,
    cleanup_timeout: float = 0.1,
) -> tuple[PipecatApplicationComposition, _Bootstrap, _Signaling]:
    exact_bootstrap = bootstrap or _Bootstrap()
    exact_signaling = signaling or _Signaling()
    return (
        PipecatApplicationComposition(
            exact_bootstrap,
            exact_signaling,
            PipecatCorsContract((ORIGIN,)),
            projection_cleanup_timeout_seconds=cleanup_timeout,
        ),
        exact_bootstrap,
        exact_signaling,
    )


def _scope() -> dict[str, str]:
    return {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "voice_call_id": CALL_ID,
    }


@pytest.mark.asyncio
async def test_composition_projects_once_then_delegates_only_exact_trusted_values() -> None:
    composition, bootstrap, signaling = _composition()
    offer = PipecatOfferRequest(sdp="offer-sdp")
    patch = PipecatPatchRequest(
        pc_id=PC_ID,
        candidates=(
            PipecatIceCandidate(
                candidate="candidate:1 1 UDP 1 127.0.0.1 5000 typ host",
                sdp_mid="0",
                sdp_mline_index=0,
            ),
        ),
    )

    browser = await composition.bootstrap_browser_assignment(**_scope())
    released = await composition.release(**_scope())
    answer = await composition.offer(token=TOKEN, user_id=USER_ID, request=offer)
    await composition.patch(token=TOKEN, user_id=USER_ID, request=patch)
    terminal = await composition.delete(token=TOKEN, user_id=USER_ID, pc_id=PC_ID)

    assert browser.model_dump(mode="json") == {
        "runtime": "pipecat_smallwebrtc_v1",
        "profile_id": PROFILE_ID,
        "event_protocol": "rtvi-murmur-v2",
        "expires_at": browser.expires_at.isoformat().replace("+00:00", "Z"),
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "voice_call_id": CALL_ID,
        "trace_id": TRACE_ID,
        "webrtc_url": ("https://voice.example.test/api/voice/pipecat/signal/" + TOKEN),
        "peer_reservation_id": "peer-reservation-composition-1",
        "ice_servers": [
            {
                "urls": ["stun:stun.example.test:3478"],
                "username": None,
                "credential": None,
                "credentialType": "password",
            },
            {
                "urls": ["turns:turn.example.test:5349?transport=tcp"],
                "username": "turn-user",
                "credential": SECRET,
                "credentialType": "password",
            },
        ],
    }
    assert bootstrap.bootstrap_calls == [_scope()]
    assert bootstrap.release_calls == [_scope()]
    assert released is None
    assert answer == PipecatOfferAnswer(sdp="answer-sdp", type="answer", pc_id=PC_ID)
    assert terminal.reason is VoiceRuntimeTerminalReason.USER_ENDED
    assert signaling.calls == [
        ("offer", {"token": TOKEN, "user_id": USER_ID, "request": offer}),
        ("patch", {"token": TOKEN, "user_id": USER_ID, "request": patch}),
        ("delete", {"token": TOKEN, "user_id": USER_ID, "pc_id": PC_ID}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        (object(), PipecatRuntimeProjectionUnavailable),
        (_bootstrap_result(user_id="different-owner"), PipecatRuntimeProjectionForbidden),
    ],
)
async def test_projection_failure_releases_the_exact_requested_scope(
    result: object,
    expected_error: type[BaseException],
) -> None:
    bootstrap = _Bootstrap(result)
    composition, _, signaling = _composition(bootstrap=bootstrap)

    with pytest.raises(expected_error) as captured:
        await composition.bootstrap_browser_assignment(**_scope())

    assert SECRET not in str(captured.value)
    assert bootstrap.release_calls == [_scope()]
    assert signaling.calls == []
    assert not composition._projection_cleanup_tasks


@pytest.mark.asyncio
async def test_projection_cleanup_timeout_keeps_owned_release_alive_until_it_converges() -> None:
    bootstrap = _Bootstrap(object())
    bootstrap.release_gate = asyncio.Event()
    composition, _, signaling = _composition(
        bootstrap=bootstrap,
        cleanup_timeout=0.01,
    )

    with pytest.raises(PipecatRuntimeProjectionUnavailable):
        await composition.bootstrap_browser_assignment(**_scope())

    assert bootstrap.release_entered.is_set()
    assert bootstrap.release_calls == [_scope()]
    assert len(composition._projection_cleanup_tasks) == 1
    assert signaling.calls == []

    bootstrap.release_gate.set()
    for _ in range(100):
        if not composition._projection_cleanup_tasks:
            break
        await asyncio.sleep(0)
    assert not composition._projection_cleanup_tasks


@pytest.mark.asyncio
async def test_projection_cleanup_failure_never_replaces_or_leaks_projection_error() -> None:
    bootstrap = _Bootstrap(object())
    bootstrap.release_error = RuntimeError(f"release failed: {SECRET}")
    composition, _, _ = _composition(bootstrap=bootstrap)

    with pytest.raises(PipecatRuntimeProjectionUnavailable) as captured:
        await composition.bootstrap_browser_assignment(**_scope())

    assert str(captured.value) == "Pipecat assignment is unavailable"
    assert SECRET not in str(captured.value)
    assert bootstrap.release_calls == [_scope()]
    assert not composition._projection_cleanup_tasks


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_cancel_projection_release_ownership() -> None:
    bootstrap = _Bootstrap(object())
    bootstrap.release_gate = asyncio.Event()
    composition, _, _ = _composition(bootstrap=bootstrap, cleanup_timeout=1.0)
    request = asyncio.create_task(composition.bootstrap_browser_assignment(**_scope()))
    await bootstrap.release_entered.wait()

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert len(composition._projection_cleanup_tasks) == 1
    bootstrap.release_gate.set()
    for _ in range(100):
        if not composition._projection_cleanup_tasks:
            break
        await asyncio.sleep(0)
    assert not composition._projection_cleanup_tasks


@pytest.mark.asyncio
async def test_shutdown_closes_bootstrap_before_signaling_and_is_idempotent() -> None:
    events: list[str] = []
    bootstrap = _Bootstrap(events=events)
    signaling = _Signaling(events=events)
    composition, _, _ = _composition(bootstrap=bootstrap, signaling=signaling)

    await composition.aclose()
    await composition.aclose()

    assert events == ["bootstrap-close", "signaling-close"]


@pytest.mark.asyncio
async def test_cancelled_close_waiter_cannot_cancel_owned_ordered_shutdown() -> None:
    events: list[str] = []
    bootstrap = _Bootstrap(events=events)
    bootstrap.close_gate = asyncio.Event()
    signaling = _Signaling(events=events)
    composition, _, _ = _composition(bootstrap=bootstrap, signaling=signaling)
    first = asyncio.create_task(composition.aclose())
    await bootstrap.close_entered.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert events == ["bootstrap-close"]
    second = asyncio.create_task(composition.aclose())
    await asyncio.sleep(0)
    assert events == ["bootstrap-close"]

    bootstrap.close_gate.set()
    await second
    await composition.aclose()

    assert events == ["bootstrap-close", "signaling-close"]


@pytest.mark.asyncio
async def test_incomplete_bootstrap_shutdown_never_closes_signaling_and_can_retry() -> None:
    events: list[str] = []
    bootstrap = _Bootstrap(events=events)
    bootstrap.close_failures = 1
    signaling = _Signaling(events=events)
    composition, _, _ = _composition(bootstrap=bootstrap, signaling=signaling)

    with pytest.raises(PipecatCompositionUnavailable) as captured:
        await composition.aclose()

    assert str(captured.value) == "Pipecat bootstrap shutdown is incomplete"
    assert SECRET not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__
    assert SECRET not in "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert events == ["bootstrap-close"]

    await composition.aclose()
    assert events == ["bootstrap-close", "bootstrap-close", "signaling-close"]


@pytest.mark.asyncio
async def test_incomplete_signaling_shutdown_is_truthful_and_retryable() -> None:
    events: list[str] = []
    bootstrap = _Bootstrap(events=events)
    signaling = _Signaling(events=events)
    signaling.close_failures = 1
    composition, _, _ = _composition(bootstrap=bootstrap, signaling=signaling)

    with pytest.raises(PipecatCompositionUnavailable) as captured:
        await composition.aclose()

    assert str(captured.value) == "Pipecat signaling shutdown is incomplete"
    assert SECRET not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__
    assert SECRET not in "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert events == ["bootstrap-close", "signaling-close"]

    await composition.aclose()
    assert events == [
        "bootstrap-close",
        "signaling-close",
        "bootstrap-close",
        "signaling-close",
    ]


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "VOICE_RUNTIME": "pipecat_smallwebrtc_v1",
        "VOICE_V2_PROFILE_ID": PROFILE_ID,
        "PIPECAT_SIGNALING_BASE_URL": ("http://127.0.0.1:8001/api/voice/pipecat/signal"),
        "ALLOWED_CORS_ORIGINS": ORIGIN,
        "DEEPGRAM_KEY": "deepgram-secret",
        "GROQ_API_KEY": "groq-secret",
        "ELEVENLABS_API_KEY": "elevenlabs-secret",
        "ELEVENLABS_VOICE_ID": "elevenlabs-voice-id",
    }
    values.update(overrides)
    return values


def test_environment_settings_are_exact_bounded_and_secret_redacted() -> None:
    environment = _environment()

    settings = PipecatCompositionSettings.from_environment(environment)

    assert settings.signaling.allowed_origins == (ORIGIN,)
    assert settings.signaling.max_active_calls == 1
    assert settings.bootstrap.max_active_calls == 1
    assert settings.signaling.reservation_ttl_seconds == 300
    assert settings.bootstrap.assignment_ttl_seconds == 300
    assert settings.signaling.signaling_base_url.endswith("/api/voice/pipecat/signal")
    rendered = repr(settings)
    for name in ("DEEPGRAM_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY"):
        assert environment[name] not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"VOICE_RUNTIME": "livekit_v2"},
        {"VOICE_V2_PROFILE_ID": "different-profile"},
        {"PIPECAT_SIGNALING_BASE_URL": "http://127.0.0.1:8001/wrong-path"},
        {"ALLOWED_CORS_ORIGINS": "*"},
        {"VOICE_V2_MAX_ACTIVE_CALLS": "2"},
        {"VOICE_V2_TOKEN_TTL_SECONDS": "not-an-integer"},
        {"PIPECAT_PROJECTION_CLEANUP_TIMEOUT_SECONDS": "nan"},
    ],
)
def test_environment_configuration_fails_closed_without_echoing_values(
    overrides: dict[str, str],
) -> None:
    with pytest.raises(PipecatCompositionUnavailable) as captured:
        PipecatCompositionSettings.from_environment(_environment(**overrides))

    assert set(overrides.values()).isdisjoint(str(captured.value).split())


def test_process_environment_error_traceback_does_not_retain_malformed_value() -> None:
    malformed_ttl = "process-ttl-secret-that-must-never-leak"

    with pytest.raises(PipecatCompositionUnavailable) as captured:
        PipecatCompositionSettings.from_environment(
            _environment(VOICE_V2_TOKEN_TTL_SECONDS=malformed_ttl)
        )

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert malformed_ttl not in rendered
    assert str(captured.value) == "The dedicated Pipecat process configuration is invalid"


def test_server_environment_error_traceback_does_not_retain_malformed_value() -> None:
    malformed_port = "server-port-secret-that-must-never-leak"

    with pytest.raises(PipecatCompositionUnavailable) as captured:
        PipecatServerSettings.from_environment({"PIPECAT_PORT": malformed_port})

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert malformed_port not in rendered
    assert str(captured.value) == "The dedicated Pipecat server configuration is invalid"


def test_auth_timeout_environment_error_traceback_does_not_retain_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed_timeout = "auth-timeout-secret-that-must-never-leak"
    monkeypatch.setenv("PIPECAT_AUTH_TIMEOUT_SECONDS", malformed_timeout)

    with pytest.raises(PipecatCompositionUnavailable) as captured:
        pipecat_app.create_app(
            composition=SimpleNamespace(),  # type: ignore[arg-type]
            database_initializer=None,
        )

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert malformed_timeout not in rendered
    assert str(captured.value) == "The dedicated Pipecat authentication configuration is invalid"


def test_composition_factory_shares_one_signaling_owner_with_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = PipecatCompositionSettings.from_environment(_environment())
    provider = SimpleNamespace(prepare=None)
    disabled_namespaces: list[str] = []
    monkeypatch.setattr(loguru_logger, "disable", disabled_namespaces.append)

    composition = create_pipecat_composition(
        settings,
        profile_provider=provider,  # type: ignore[arg-type]
        handler_factory=lambda _lease: SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert composition.bootstrap_service._signaling is composition.signaling_service
    assert composition.cors is composition.signaling_service.cors
    assert composition.cors.allowed_origins == (ORIGIN,)
    assert SECRET not in repr(composition.__dict__)
    assert disabled_namespaces == [PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE]


def test_injected_profile_can_use_matching_nonproduction_id_without_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_profile_id = "pipecat-fake-rtc-v1"
    provider = SimpleNamespace(prepare=None)
    disabled_namespaces: list[str] = []
    monkeypatch.setattr(loguru_logger, "disable", disabled_namespaces.append)
    settings = PipecatCompositionSettings(
        signaling=PipecatSignalingSettings(
            signaling_base_url="http://127.0.0.1:8101/api/voice/pipecat/signal",
            profile_id=fake_profile_id,
            allowed_origins=("http://127.0.0.1:3100",),
        ),
        bootstrap=PipecatBootstrapSettings(profile_id=fake_profile_id),
        cascade=None,
    )

    composition = create_pipecat_composition(
        settings,
        profile_provider=provider,  # type: ignore[arg-type]
        handler_factory=lambda _lease: SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert composition.bootstrap_service.settings.profile_id == fake_profile_id
    assert composition.signaling_service.settings.profile_id == fake_profile_id
    assert composition.signaling_service._runtime_starter._profile_provider is provider
    assert disabled_namespaces == [PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE]


def test_missing_injected_provider_with_no_cascade_fails_closed() -> None:
    fake_profile_id = "pipecat-fake-rtc-v1"
    settings = PipecatCompositionSettings(
        signaling=PipecatSignalingSettings(
            signaling_base_url="http://127.0.0.1:8101/api/voice/pipecat/signal",
            profile_id=fake_profile_id,
            allowed_origins=("http://127.0.0.1:3100",),
        ),
        bootstrap=PipecatBootstrapSettings(profile_id=fake_profile_id),
        cascade=None,
    )

    with pytest.raises(PipecatCompositionUnavailable, match="explicit profile provider"):
        create_pipecat_composition(settings)


def test_peer_handler_receives_fresh_claim_scoped_ice_and_single_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipecat.transports.smallwebrtc import request_handler

    captured: dict[str, object] = {}
    disabled_namespaces: list[str] = []
    monkeypatch.setattr(loguru_logger, "disable", disabled_namespaces.append)

    class FakeSdkHandler:
        def __init__(self, *, ice_servers: list[object], connection_mode: object) -> None:
            captured["ice_servers"] = ice_servers
            captured["connection_mode"] = connection_mode

        async def handle_web_request(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def handle_patch_request(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(request_handler, "SmallWebRTCRequestHandler", FakeSdkHandler)
    lease = _lease(_claims())

    adapter = create_pipecat_peer_handler(lease)

    servers = captured["ice_servers"]
    assert isinstance(servers, list)
    assert servers is not lease.ice_servers
    assert len(servers) == 2
    assert servers[0].urls == ["stun:stun.example.test:3478"]  # type: ignore[union-attr]
    assert servers[1].urls == [  # type: ignore[union-attr]
        "turns:turn.example.test:5349?transport=tcp"
    ]
    assert servers[1].username == "turn-user"  # type: ignore[union-attr]
    assert servers[1].credential == SECRET  # type: ignore[union-attr]
    assert captured["connection_mode"] is request_handler.ConnectionMode.SINGLE
    assert adapter._handler.__class__ is FakeSdkHandler
    assert not hasattr(adapter, "ice_servers")
    assert disabled_namespaces == [PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE]


@pytest.mark.asyncio
async def test_real_pinned_handler_cannot_log_full_invalid_sdp_after_production_disable() -> None:
    ice_ufrag = "ice-ufrag-that-must-never-reach-loguru"
    ice_password = "ice-password-that-must-never-reach-loguru"
    sentinel_sdp = f"v=0\r\na=ice-ufrag:{ice_ufrag}\r\na=ice-pwd:{ice_password}\r\n"
    captured_logs: list[str] = []
    loguru_logger.enable(PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE)
    sink_id = loguru_logger.add(
        lambda message: captured_logs.append(str(message)),
        level="DEBUG",
    )
    try:
        adapter = create_pipecat_peer_handler(_lease(_claims()))
        handler = adapter._handler
        handler._pcs_map["existing-peer"] = SimpleNamespace(pc_id="existing-peer")

        async def unexpected_callback(_connection: object) -> None:
            pytest.fail("single-connection rejection must occur before the callback")

        with pytest.raises(HTTPException):
            await adapter.handle_web_request(
                PipecatOfferRequest(sdp=sentinel_sdp),
                unexpected_callback,
            )
    finally:
        loguru_logger.remove(sink_id)
        loguru_logger.enable(PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE)

    rendered = "\n".join(captured_logs)
    assert sentinel_sdp not in rendered
    assert ice_ufrag not in rendered
    assert ice_password not in rendered
    assert "SmallWebRTC request details" not in rendered


@pytest.mark.asyncio
async def test_real_accepted_offer_cannot_log_remote_sdp_or_ice_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aiortc import RTCPeerConnection, RTCSessionDescription

    python_logger = logging.getLogger(AIORTC_PEER_CONNECTION_LOG_NAMESPACE)
    previous_disabled = python_logger.disabled
    previous_level = python_logger.level
    python_logger.disabled = False
    python_logger.setLevel(logging.NOTSET)
    client = RTCPeerConnection()
    adapter = None
    try:
        client.createDataChannel("pipecat-log-probe")
        offer = await client.createOffer()
        await client.setLocalDescription(offer)
        assert client.localDescription is not None
        remote_offer_sdp = client.localDescription.sdp
        remote_ice_ufrag = next(
            line.removeprefix("a=ice-ufrag:")
            for line in remote_offer_sdp.splitlines()
            if line.startswith("a=ice-ufrag:")
        )
        remote_ice_password = next(
            line.removeprefix("a=ice-pwd:")
            for line in remote_offer_sdp.splitlines()
            if line.startswith("a=ice-pwd:")
        )
        caplog.clear()
        caplog.set_level(logging.DEBUG)
        claims = _claims()
        adapter = create_pipecat_peer_handler(
            PipecatIceLease(
                claims=claims,
                provider_id="test-local-only",
                expires_at=claims.expires_at,
                ice_servers=(),
            )
        )
        assert python_logger.disabled is False
        assert python_logger.level >= logging.WARNING

        async def accept_connection(_connection: object) -> None:
            return None

        answer = await asyncio.wait_for(
            adapter.handle_web_request(
                PipecatOfferRequest(sdp=remote_offer_sdp),
                accept_connection,
            ),
            timeout=10,
        )
        assert answer is not None
        local_answer_sdp = answer["sdp"]
        local_ice_ufrag = next(
            line.removeprefix("a=ice-ufrag:")
            for line in local_answer_sdp.splitlines()
            if line.startswith("a=ice-ufrag:")
        )
        local_ice_password = next(
            line.removeprefix("a=ice-pwd:")
            for line in local_answer_sdp.splitlines()
            if line.startswith("a=ice-pwd:")
        )
        connected = asyncio.Event()

        @client.on("connectionstatechange")
        async def connection_state_changed() -> None:
            if client.connectionState in {"connected", "failed", "closed"}:
                connected.set()

        await client.setRemoteDescription(
            RTCSessionDescription(sdp=local_answer_sdp, type=answer["type"])
        )
        await asyncio.wait_for(connected.wait(), timeout=5)
        assert client.connectionState == "connected"
    finally:
        if adapter is not None:
            await asyncio.gather(adapter.close(), client.close())
        else:
            await client.close()
        await asyncio.sleep(0)
        python_logger.disabled = previous_disabled
        python_logger.setLevel(previous_level)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert remote_offer_sdp not in rendered
    assert local_answer_sdp not in rendered
    assert remote_ice_ufrag not in rendered
    assert remote_ice_password not in rendered
    assert local_ice_ufrag not in rendered
    assert local_ice_password not in rendered


@pytest.mark.parametrize("value", [False, 0, -1, float("nan"), 16])
def test_projection_cleanup_timeout_is_strictly_bounded(value: object) -> None:
    with pytest.raises(ValueError, match="projection cleanup timeout"):
        PipecatApplicationComposition(
            _Bootstrap(),
            _Signaling(),
            PipecatCorsContract((ORIGIN,)),
            projection_cleanup_timeout_seconds=value,  # type: ignore[arg-type]
        )


def test_server_options_fix_one_factory_worker_and_disable_access_logs() -> None:
    settings = PipecatServerSettings.from_environment(
        {"PIPECAT_HOST": "127.0.0.1", "PIPECAT_PORT": "8765"}
    )

    assert uvicorn_options(settings) == {
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "workers": 1,
        "access_log": False,
        "server_header": False,
        "limit_concurrency": 100,
    }
    with pytest.raises(ValueError, match="exactly one worker"):
        PipecatServerSettings(workers=2)
    with pytest.raises(ValueError, match="access logs"):
        PipecatServerSettings(access_log=True)


def test_main_passes_only_the_fixed_factory_runner_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setenv("PIPECAT_HOST", "127.0.0.1")
    monkeypatch.setenv("PIPECAT_PORT", "8877")
    monkeypatch.setattr(
        pipecat_app.uvicorn,
        "run",
        lambda target, **options: calls.append((target, options)),
    )

    pipecat_app.main()

    assert calls == [
        (
            "murmur.voice.pipecat_app:create_app",
            {
                "factory": True,
                "host": "127.0.0.1",
                "port": 8877,
                "workers": 1,
                "access_log": False,
                "server_header": False,
                "limit_concurrency": 100,
            },
        )
    ]

"""Authenticated, browser-safe projection of runtime bearer assignments."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from murmur.voice.pipecat_ice import PipecatIceLease
from murmur.voice.runtime_contracts import (
    PIPECAT_EVENT_PROTOCOL,
    CanonicalUuid4,
    ContractId,
    PipecatVoiceRuntimeAssignment,
)


class PipecatRuntimeProjectionForbidden(RuntimeError):
    """Authenticated request scope does not own the assignment."""


class PipecatRuntimeProjectionUnavailable(RuntimeError):
    """Internal assignment and ICE lease state cannot be projected safely."""


class _BrowserProjection(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
        serialize_by_alias=True,
    )


class PipecatBrowserIceServer(_BrowserProjection):
    """W3C-shaped ICE entry returned only after assignment authorization."""

    urls: tuple[str, ...]
    username: str | None = Field(default=None, repr=False)
    credential: str | None = Field(default=None, repr=False)
    credential_type: Literal["password"] = Field(
        default="password",
        serialization_alias="credentialType",
    )


class PipecatBrowserVoiceAssignment(_BrowserProjection):
    """Deliberate bearer projection; authoritative claims never cross this boundary."""

    runtime: Literal["pipecat_smallwebrtc_v1"] = "pipecat_smallwebrtc_v1"
    profile_id: ContractId
    event_protocol: Literal["rtvi-murmur-v2"] = PIPECAT_EVENT_PROTOCOL
    expires_at: AwareDatetime
    session_id: CanonicalUuid4
    agent_id: CanonicalUuid4
    voice_call_id: CanonicalUuid4
    trace_id: CanonicalUuid4
    webrtc_url: str = Field(repr=False)
    peer_reservation_id: ContractId
    ice_servers: tuple[PipecatBrowserIceServer, ...]


def project_pipecat_assignment_for_browser(
    *,
    authenticated_user_id: str,
    session_id: str,
    voice_call_id: str,
    assignment: PipecatVoiceRuntimeAssignment,
    ice_lease: PipecatIceLease,
    now: datetime | None = None,
) -> PipecatBrowserVoiceAssignment:
    """Reveal one exact Pipecat bearer assignment after trusted scope checks.

    The caller supplies identity derived from authentication and the request,
    never values copied out of the assignment.  Scope mismatches intentionally
    share one generic error.  Internal lease mismatches are unavailable rather
    than authorization failures, so an operator can distinguish corruption
    without exposing any claim to the browser.
    """

    claims = assignment.claims
    if not all(
        isinstance(value, str) for value in (authenticated_user_id, session_id, voice_call_id)
    ) or not (
        _constant_time_equal(authenticated_user_id, claims.user_id)
        and _constant_time_equal(session_id, claims.session_id)
        and _constant_time_equal(voice_call_id, claims.voice_call_id)
    ):
        raise PipecatRuntimeProjectionForbidden("Forbidden")

    if (
        ice_lease.claims != claims
        or assignment.expires_at != claims.expires_at
        or ice_lease.expires_at != assignment.expires_at
    ):
        raise PipecatRuntimeProjectionUnavailable("Pipecat assignment is unavailable")

    projection_time = now or datetime.now(UTC)
    if not isinstance(projection_time, datetime) or projection_time.utcoffset() != timedelta(0):
        raise PipecatRuntimeProjectionUnavailable("Pipecat assignment is unavailable")
    if projection_time >= assignment.expires_at:
        raise PipecatRuntimeProjectionUnavailable("Pipecat assignment is unavailable")

    browser_ice_servers = tuple(
        PipecatBrowserIceServer(
            urls=server.urls,
            username=(server.username.get_secret_value() if server.username is not None else None),
            credential=(
                server.credential.get_secret_value() if server.credential is not None else None
            ),
            credential_type=server.credential_type,
        )
        for server in ice_lease.ice_servers
    )
    return PipecatBrowserVoiceAssignment(
        profile_id=claims.profile_id,
        expires_at=assignment.expires_at,
        session_id=claims.session_id,
        agent_id=claims.agent_id,
        voice_call_id=claims.voice_call_id,
        trace_id=claims.trace_id,
        webrtc_url=assignment.webrtc_url.get_secret_value(),
        peer_reservation_id=assignment.peer_reservation_id,
        ice_servers=browser_ice_servers,
    )


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

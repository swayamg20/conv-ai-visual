"""Immutable ICE leases shared by Pipecat bootstrap and SmallWebRTC.

The lease keeps one validated source of truth for both the browser RTC
configuration and the aiortc objects consumed by Pipecat.  TURN credentials
remain ``SecretStr`` values until an authenticated browser projection or a
fresh server-side SDK object is deliberately constructed.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import timedelta
from typing import Protocol
from urllib.parse import urlsplit

from aiortc import RTCIceServer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from murmur.voice.runtime_contracts import ContractId, VoiceCallClaims, VoiceRuntimeKind

_ICE_URI = re.compile(
    r"^(?P<scheme>stun|turn|turns):(?P<host>[^/?#:@]+)"
    r"(?::(?P<port>[0-9]{1,5}))?(?:\?transport=(?P<transport>udp|tcp))?$",
)
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_MAX_ICE_SERVERS = 8
_MAX_ICE_URLS_PER_SERVER = 8


class PipecatIceLeaseUnavailable(RuntimeError):
    """A safe, fail-closed ICE configuration could not be issued."""


class _ImmutableSecretStr(SecretStr):
    """A ``SecretStr`` whose captured value cannot be reassigned normally."""

    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_secret_value" and not hasattr(self, name):
            object.__setattr__(self, name, value)
            return
        raise AttributeError("ICE secret is immutable")


class _IceContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )


class PipecatIceServer(_IceContract):
    """One immutable browser/server ICE entry with redacted credentials."""

    urls: tuple[str, ...]
    username: SecretStr | None = Field(default=None, repr=False)
    credential: SecretStr | None = Field(default=None, repr=False)
    credential_type: str = "password"

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, urls: tuple[str, ...]) -> tuple[str, ...]:
        if not urls or len(urls) > _MAX_ICE_URLS_PER_SERVER:
            raise ValueError("ICE server must contain between one and eight URLs")
        if len(set(urls)) != len(urls):
            raise ValueError("ICE server URLs must be unique")
        for url in urls:
            _validate_ice_url(url)
        return urls

    @field_validator("username", "credential", mode="before")
    @classmethod
    def validate_secret(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            raw_value = value.get_secret_value()
        elif isinstance(value, str):
            raw_value = value
        else:
            raise ValueError("ICE credential fields must be strings")
        if (
            not raw_value
            or raw_value != raw_value.strip()
            or len(raw_value) > 4_096
            or any(ord(character) < 32 or ord(character) == 127 for character in raw_value)
        ):
            raise ValueError("ICE credential field is invalid")
        return _ImmutableSecretStr(raw_value)

    @field_validator("credential_type")
    @classmethod
    def validate_credential_type(cls, value: str) -> str:
        if value != "password":
            raise ValueError("Pipecat ICE supports password credentials only")
        return value

    @model_validator(mode="after")
    def validate_credentials_match_urls(self) -> PipecatIceServer:
        schemes = {_ice_url_scheme(url) for url in self.urls}
        if schemes <= {"stun"}:
            if self.username is not None or self.credential is not None:
                raise ValueError("STUN entries must not contain TURN credentials")
            return self
        if schemes <= {"turn", "turns"}:
            if self.username is None or self.credential is None:
                raise ValueError("TURN entries require a username and credential")
            return self
        raise ValueError("one ICE entry must not mix STUN and TURN URLs")

    def to_pipecat_ice_server(self) -> RTCIceServer:
        """Create a fresh SDK object for immediate SmallWebRTC construction.

        The returned third-party dataclass necessarily contains plaintext TURN
        credentials.  It is a narrow, ephemeral transport sink that must never
        be logged, serialized, or retained as control-plane state.  The
        immutable lease remains the only redacted retained source of truth.
        """

        return RTCIceServer(
            urls=list(self.urls),
            username=(self.username.get_secret_value() if self.username is not None else None),
            credential=(
                self.credential.get_secret_value() if self.credential is not None else None
            ),
            credentialType=self.credential_type,
        )


class PipecatIceLease(_IceContract):
    """Claim-bound ICE material issued for exactly one assignment lifetime."""

    claims: VoiceCallClaims = Field(exclude=True, repr=False)
    provider_id: ContractId = Field(exclude=True, repr=False)
    expires_at: AwareDatetime
    ice_servers: tuple[PipecatIceServer, ...] = ()

    @field_validator("expires_at")
    @classmethod
    def validate_utc_expiry(cls, value: object) -> object:
        if not hasattr(value, "utcoffset") or value.utcoffset() != timedelta(0):
            raise ValueError("ICE lease expiry must use UTC")
        return value

    @field_validator("ice_servers")
    @classmethod
    def validate_ice_servers(
        cls,
        value: tuple[PipecatIceServer, ...],
    ) -> tuple[PipecatIceServer, ...]:
        if len(value) > _MAX_ICE_SERVERS:
            raise ValueError("ICE lease supports at most eight server entries")
        flattened_urls = [url for server in value for url in server.urls]
        if len(set(flattened_urls)) != len(flattened_urls):
            raise ValueError("ICE lease URLs must be unique across entries")
        schemes = [_ice_url_scheme(url) for url in flattened_urls]
        if schemes.count("stun") > 1 or sum(scheme in {"turn", "turns"} for scheme in schemes) > 1:
            raise ValueError(
                "ICE lease supports at most one STUN and one TURN URL for pinned aiortc"
            )
        return value

    @model_validator(mode="after")
    def validate_claim_scope_and_ttl(self) -> PipecatIceLease:
        if self.claims.runtime is not VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1:
            raise ValueError("ICE lease requires Pipecat call claims")
        if self.expires_at != self.claims.expires_at:
            raise ValueError("ICE lease expiry must equal its authoritative claims expiry")
        return self

    def to_pipecat_ice_servers(self) -> list[RTCIceServer]:
        """Return fresh aiortc objects accepted by Pipecat SmallWebRTC."""

        return [server.to_pipecat_ice_server() for server in self.ice_servers]

    def require_compatible_signaling_base_url(self, signaling_base_url: str) -> None:
        """Enforce that non-loopback signaling has explicit TURN material."""

        hostname, _scheme = _validated_signaling_origin(signaling_base_url)
        if not _is_loopback_hostname(hostname) and not any(
            _ice_url_scheme(url) in {"turn", "turns"}
            for server in self.ice_servers
            for url in server.urls
        ):
            raise PipecatIceLeaseUnavailable(
                "Non-loopback Pipecat ICE requires an explicit TURN server"
            )


class PipecatIceLeaseIssuer(Protocol):
    """Injectable issuer used by the Pipecat bootstrap owner."""

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease: ...


class _ValidatedPipecatIceLeaseIssuer:
    """Apply origin and claim policy to every injected issuer result."""

    def __init__(self, issuer: PipecatIceLeaseIssuer, *, signaling_base_url: str) -> None:
        self._issuer = issuer
        self._signaling_base_url = signaling_base_url

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
        try:
            lease = await self._issuer.issue(claims)
        except PipecatIceLeaseUnavailable:
            raise
        except Exception as exc:
            raise PipecatIceLeaseUnavailable("Pipecat ICE lease is unavailable") from exc
        if not isinstance(lease, PipecatIceLease) or lease.claims != claims:
            raise PipecatIceLeaseUnavailable("Pipecat ICE lease is unavailable")
        lease.require_compatible_signaling_base_url(self._signaling_base_url)
        return lease


class LoopbackDirectIceLeaseIssuer:
    """Provider-free empty ICE configuration for loopback qualification only."""

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
        try:
            return PipecatIceLease(
                claims=claims,
                provider_id="loopback-direct",
                expires_at=claims.expires_at,
            )
        except ValidationError as exc:
            raise PipecatIceLeaseUnavailable("Pipecat ICE lease is unavailable") from exc


def resolve_pipecat_ice_lease_issuer(
    signaling_base_url: str,
    issuer: PipecatIceLeaseIssuer | None,
) -> PipecatIceLeaseIssuer:
    """Resolve explicit ICE ownership and reject implicit public-host direct mode."""

    hostname, scheme = _validated_signaling_origin(signaling_base_url)
    is_loopback = _is_loopback_hostname(hostname)
    if scheme != "https" and not (scheme == "http" and is_loopback):
        raise PipecatIceLeaseUnavailable("Pipecat signaling URL must use HTTPS or loopback HTTP")
    if issuer is not None:
        return _ValidatedPipecatIceLeaseIssuer(
            issuer,
            signaling_base_url=signaling_base_url,
        )
    if is_loopback:
        return _ValidatedPipecatIceLeaseIssuer(
            LoopbackDirectIceLeaseIssuer(),
            signaling_base_url=signaling_base_url,
        )
    raise PipecatIceLeaseUnavailable("Pipecat ICE provider is required for non-loopback signaling")


def _validate_ice_url(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 2_048
        or "%" in value
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("ICE URL is invalid")
    match = _ICE_URI.fullmatch(value)
    if match is None:
        raise ValueError("ICE URL is malformed or unsupported")
    scheme = match.group("scheme").lower()
    transport = match.group("transport")
    if scheme == "stun" and transport is not None:
        raise ValueError("STUN URL must not declare a transport")
    if scheme == "turns" and transport not in {None, "tcp"}:
        raise ValueError("TURN/TLS URL must use TCP")
    raw_port = match.group("port")
    if raw_port is not None and not 1 <= int(raw_port) <= 65_535:
        raise ValueError("ICE URL port is invalid")
    _validate_ice_hostname(match.group("host"))


def _ice_url_scheme(value: str) -> str:
    match = _ICE_URI.fullmatch(value)
    if match is None:  # pragma: no cover - protected by field validation
        raise ValueError("ICE URL is malformed")
    return match.group("scheme").lower()


def _validate_ice_hostname(hostname: str) -> None:
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass
    if len(hostname) > 253 or hostname.endswith("."):
        raise ValueError("ICE URL hostname is invalid")
    labels = hostname.split(".")
    if len(labels) < 2 or any(_DNS_LABEL.fullmatch(label) is None for label in labels):
        raise ValueError("ICE URL hostname is invalid")


def _validated_signaling_origin(value: object) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "%" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PipecatIceLeaseUnavailable("Pipecat signaling URL is invalid")
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError as exc:
        raise PipecatIceLeaseUnavailable("Pipecat signaling URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PipecatIceLeaseUnavailable("Pipecat signaling URL is invalid")
    return parsed.hostname, parsed.scheme


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False

"""Pre-import Coturn contracts for the deterministic Pipecat E2E runner.

This core stays standard-library only because the guarded ASGI app imports it
before any Murmur module. It owns network selection, exact paths/config/CA,
and claim-bounded REST credentials. Docker/TLS process orchestration and log
evidence remain deferred to Checkpoint B.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Final

COTURN_IMAGE: Final = (
    "coturn/coturn@sha256:75e9ebd1e19005bec0c7f591d29afe22f959916ac8d9c852452f27db8c789828"
)
COTURN_PLATFORM: Final = "linux/amd64"
COTURN_REALM: Final = "voice-pipecat-e2e.invalid"
COTURN_TURNS_URL: Final = "turns:127.0.0.1:5349?transport=tcp"
COTURN_TLS_PORT: Final = 5349
COTURN_RELAY_MIN_PORT: Final = 49160
COTURN_RELAY_MAX_PORT: Final = 49169
COTURN_TOPOLOGY_STATUS: Final = "contract-unvalidated"
COTURN_RELAY_MEDIA_EXECUTABLE: Final = False
COTURN_FIXTURE_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "voice"
    / "coturn"
    / "turnserver.conf"
)

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_STATIC_AUTH_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_OPENSSL_ENV_NAMES = frozenset({"OPENSSL_CONF", "OPENSSL_MODULES", "SSLKEYLOGFILE"})
_EXPECTED_FIXTURE = """\
# Render-only owned /29 bridge template. Braced values are not executable.
# owned-network={network_cidr}
# owned-gateway={gateway_ipv4}
# owned-container={container_ipv4}
listening-ip={container_ipv4}
listening-port=3478
tls-listening-port=5349
relay-ip={container_ipv4}
external-ip=127.0.0.1/{container_ipv4}
min-port=49160
max-port=49169
relay-threads=1
# Call-level quota bound: at most two allocations; no endpoint attribution.
user-quota=2
total-quota=2
fingerprint
use-auth-secret
realm=voice-pipecat-e2e.invalid
cert=/run/murmur-coturn/cert.pem
pkey=/run/murmur-coturn/key.pem
no-udp
no-tcp
# Keep peer-side relay UDP-only; client-to-TURN remains TLS/TCP on turns:.
no-tcp-relay
no-multicast-peers
stale-nonce=60
log-file=stdout
simple-log
verbose
log-min-level=info
pidfile=/tmp/turnserver.pid
userdb=/tmp/turnserver.db
"""
_PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class CoturnContractError(RuntimeError):
    """A relay-TLS test contract is malformed or unsafe."""


class _UnsafeFilePermissionsError(OSError):
    """An opened contract file does not satisfy its exact mode policy."""


class PipecatE2ENetworkMode(str, Enum):
    """The two recognized deterministic runner network contracts."""

    DIRECT = "direct"
    RELAY_TLS = "relay-tls"

    @property
    def evidence_name(self) -> str:
        if self is PipecatE2ENetworkMode.DIRECT:
            return "direct-loopback"
        return "relay-tls"


@dataclass(frozen=True)
class CoturnBridgeTopology:
    """One exact private /29 bridge layout, redacted from diagnostics."""

    network: ipaddress.IPv4Network = field(repr=False)
    gateway: ipaddress.IPv4Address = field(repr=False)
    container: ipaddress.IPv4Address = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.network, ipaddress.IPv4Network) or self.network.prefixlen != 29:
            raise CoturnContractError("Coturn bridge topology is invalid")
        if not any(self.network.subnet_of(private) for private in _PRIVATE_IPV4_NETWORKS):
            raise CoturnContractError("Coturn bridge topology is invalid")
        if (
            not isinstance(self.gateway, ipaddress.IPv4Address)
            or not isinstance(self.container, ipaddress.IPv4Address)
            or self.gateway != self.network.network_address + 1
            or self.container != self.network.network_address + 2
        ):
            raise CoturnContractError("Coturn bridge topology is invalid")

    @classmethod
    def parse(
        cls,
        *,
        network: object,
        gateway: object,
        container: object,
    ) -> CoturnBridgeTopology:
        if not all(isinstance(value, str) for value in (network, gateway, container)):
            raise CoturnContractError("Coturn bridge topology is invalid")
        try:
            parsed_network = ipaddress.IPv4Network(network, strict=True)
            parsed_gateway = ipaddress.IPv4Address(gateway)
            parsed_container = ipaddress.IPv4Address(container)
        except ipaddress.AddressValueError:
            raise CoturnContractError("Coturn bridge topology is invalid") from None
        except ipaddress.NetmaskValueError:
            raise CoturnContractError("Coturn bridge topology is invalid") from None
        if str(parsed_network) != network:
            raise CoturnContractError("Coturn bridge topology is invalid")
        return cls(
            network=parsed_network,
            gateway=parsed_gateway,
            container=parsed_container,
        )


_CONTRACT_ONLY_TOPOLOGY = CoturnBridgeTopology.parse(
    network="10.255.255.0/29",
    gateway="10.255.255.1",
    container="10.255.255.2",
)


@dataclass(frozen=True)
class CoturnConfigurationReceipt:
    """Parsed private configuration bound to its exact rendered topology."""

    static_auth_secret: str = field(repr=False)
    topology: CoturnBridgeTopology = field(repr=False)

    def __post_init__(self) -> None:
        validate_static_auth_secret(self.static_auth_secret)
        if not isinstance(self.topology, CoturnBridgeTopology):
            raise CoturnContractError("Coturn configuration receipt is invalid")


def parse_network_mode(value: object) -> PipecatE2ENetworkMode:
    """Parse an exact mode without reflecting attacker-controlled input."""

    if not isinstance(value, str):
        raise CoturnContractError("Pipecat E2E network mode is invalid")
    try:
        return PipecatE2ENetworkMode(value)
    except ValueError:
        raise CoturnContractError("Pipecat E2E network mode is invalid") from None


@dataclass(frozen=True)
class CoturnContractPaths:
    """Exact host-side config and trust paths owned by one relay contract."""

    run_id: str
    run_dir: Path
    coturn_dir: Path
    config: Path
    cert: Path
    private_key: Path

    def __post_init__(self) -> None:
        if not _RUN_ID_PATTERN.fullmatch(self.run_id):
            raise CoturnContractError("Coturn run ID is invalid")
        if not self.run_dir.is_absolute() or self.run_dir.name != self.run_id:
            raise CoturnContractError("Coturn run directory is invalid")
        expected_dir = self.run_dir / "coturn"
        expected = {
            "coturn_dir": expected_dir,
            "config": expected_dir / "turnserver.conf",
            "cert": expected_dir / "cert.pem",
            "private_key": expected_dir / "key.pem",
        }
        for name, path in expected.items():
            if getattr(self, name) != path or not path.is_absolute():
                raise CoturnContractError("Coturn path contract is invalid")
            _require_safe_path_text(path)

    @classmethod
    def for_run_dir(cls, run_id: str, run_dir: Path) -> CoturnContractPaths:
        if not isinstance(run_dir, Path):
            raise CoturnContractError("Coturn run directory is invalid")
        coturn_dir = run_dir / "coturn"
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            coturn_dir=coturn_dir,
            config=coturn_dir / "turnserver.conf",
            cert=coturn_dir / "cert.pem",
            private_key=coturn_dir / "key.pem",
        )


def validate_coturn_fixture(value: object) -> str:
    """Require the checked-in, secret-free, allowlisted Coturn template."""

    if not isinstance(value, str) or value != _EXPECTED_FIXTURE:
        raise CoturnContractError("Coturn configuration fixture is invalid")
    if (
        "static-auth-secret" in value
        or "allow-loopback-peers" in value
        or "0.0.0.0" in value
        or value.count("{network_cidr}") != 1
        or value.count("{gateway_ipv4}") != 1
        or value.count("{container_ipv4}") != 4
    ):
        raise CoturnContractError("Coturn configuration fixture is invalid")
    return value


def render_coturn_configuration(
    fixture: object,
    static_auth_secret: object,
    topology: CoturnBridgeTopology | None = None,
) -> str:
    """Render one exact owned bridge layout and append one REST secret.

    The default is a contract-only private layout retained for Checkpoint A
    callers. Checkpoint B must supply the collision-checked owned topology.
    """

    template = validate_coturn_fixture(fixture)
    secret = validate_static_auth_secret(static_auth_secret)
    selected = _CONTRACT_ONLY_TOPOLOGY if topology is None else topology
    if not isinstance(selected, CoturnBridgeTopology):
        raise CoturnContractError("Coturn bridge topology is invalid")
    rendered = (
        template.replace("{network_cidr}", str(selected.network))
        .replace("{gateway_ipv4}", str(selected.gateway))
        .replace("{container_ipv4}", str(selected.container))
    )
    if "{" in rendered or "}" in rendered:
        raise CoturnContractError("Coturn configuration fixture is invalid")
    return f"{rendered}static-auth-secret={secret}\n"


def validate_static_auth_secret(value: object) -> str:
    if not isinstance(value, str) or not _STATIC_AUTH_SECRET_PATTERN.fullmatch(value):
        raise CoturnContractError("Coturn static authentication secret is invalid")
    return value


def read_private_coturn_configuration_receipt(
    path: object,
    *,
    expected_run_dir: Path,
) -> CoturnConfigurationReceipt:
    """Read the exact config and return a fully redacted parsed receipt."""

    expected_path = expected_run_dir / "coturn" / "turnserver.conf"
    config_path = _require_exact_regular_file(
        path,
        expected=expected_path,
        label="Coturn configuration",
    )
    _require_private_directory(expected_run_dir, label="Pipecat E2E run directory")
    _require_private_directory(expected_run_dir / "coturn", label="Coturn directory")
    try:
        value = _read_regular_file_no_follow(
            config_path,
            maximum_bytes=8_192,
            exact_mode=0o400,
        ).decode("utf-8")
    except _UnsafeFilePermissionsError:
        raise CoturnContractError("Coturn configuration permissions are unsafe") from None
    except (OSError, UnicodeError):
        raise CoturnContractError("Coturn configuration is unavailable") from None
    if not value.endswith("\n") or value.count("static-auth-secret=") != 1:
        raise CoturnContractError("Coturn generated configuration is invalid")
    rendered, separator, secret_line = value.rpartition("static-auth-secret=")
    if not separator:
        raise CoturnContractError("Coturn generated configuration is invalid")
    secret = secret_line.removesuffix("\n")
    if "\n" in secret or "\r" in secret:
        raise CoturnContractError("Coturn generated configuration is invalid")
    validate_static_auth_secret(secret)
    try:
        lines = rendered.splitlines()
        if len(lines) < 4:
            raise CoturnContractError("Coturn generated configuration is invalid")
        topology = CoturnBridgeTopology.parse(
            network=lines[1].removeprefix("# owned-network="),
            gateway=lines[2].removeprefix("# owned-gateway="),
            container=lines[3].removeprefix("# owned-container="),
        )
    except (IndexError, CoturnContractError):
        raise CoturnContractError("Coturn generated configuration is invalid") from None
    expected = render_coturn_configuration(_EXPECTED_FIXTURE, secret, topology)
    if value != expected:
        raise CoturnContractError("Coturn generated configuration is invalid")
    return CoturnConfigurationReceipt(static_auth_secret=secret, topology=topology)


def read_private_coturn_configuration(
    path: object,
    *,
    expected_run_dir: Path,
) -> str:
    """Compatibility reader returning only the validated static secret."""

    return read_private_coturn_configuration_receipt(
        path,
        expected_run_dir=expected_run_dir,
    ).static_auth_secret


def validate_turn_tls_ca_file(path: object, *, expected_run_dir: Path) -> Path:
    """Require one parseable generated trust anchor at its exact private path."""

    if any(name in os.environ for name in _FORBIDDEN_OPENSSL_ENV_NAMES):
        raise CoturnContractError("Coturn TLS validation environment is unsafe")
    _require_private_directory(expected_run_dir, label="Pipecat E2E run directory")
    _require_private_directory(expected_run_dir / "coturn", label="Coturn directory")
    certificate = _require_exact_regular_file(
        path,
        expected=expected_run_dir / "coturn" / "cert.pem",
        label="Coturn TLS certificate",
    )
    try:
        value = _read_regular_file_no_follow(
            certificate,
            maximum_bytes=65_536,
            exact_mode=0o400,
        ).decode("ascii")
    except _UnsafeFilePermissionsError:
        raise CoturnContractError("Coturn TLS certificate permissions are unsafe") from None
    except (OSError, UnicodeError):
        raise CoturnContractError("Coturn TLS certificate is unavailable") from None
    if not re.fullmatch(
        r"-----BEGIN CERTIFICATE-----\n(?:[A-Za-z0-9+/=]{1,64}\n)+"
        r"-----END CERTIFICATE-----\n?",
        value,
    ):
        raise CoturnContractError("Coturn TLS certificate is invalid")
    try:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=value)
    except ssl.SSLError:
        raise CoturnContractError("Coturn TLS certificate is invalid") from None
    return certificate


@dataclass(frozen=True)
class TurnRestCredentials:
    """One claim-bounded Coturn REST credential with redacted secret fields."""

    expires_at_epoch_seconds: int = field(repr=False)
    call_tag: str = field(repr=False)
    username: str = field(repr=False)
    credential: str = field(repr=False)


def derive_turn_rest_credentials(
    *,
    static_auth_secret: object,
    voice_call_id: object,
    expires_at: object,
    now: object,
) -> TurnRestCredentials:
    """Derive Coturn REST credentials that never outlive call claims.

    Coturn usernames carry integer Unix seconds, so a microsecond claim expiry
    is deliberately floored.  Credential validity can therefore be less than
    one second shorter than the authoritative claim and is never longer.
    """

    secret = validate_static_auth_secret(static_auth_secret)
    call_id = _validate_uuid4(voice_call_id)
    expiry = _require_utc_datetime(expires_at, label="TURN credential expiry")
    current = _require_utc_datetime(now, label="TURN credential clock")
    expiry_seconds = int(expiry.timestamp())
    if expiry_seconds <= int(current.timestamp()):
        raise CoturnContractError("TURN credential expiry is invalid")
    call_tag = hashlib.sha256(call_id.encode("ascii")).hexdigest()[:16]
    username = f"{expiry_seconds}:{call_tag}"
    credential = base64.b64encode(
        hmac.new(secret.encode("ascii"), username.encode("ascii"), hashlib.sha1).digest()
    ).decode("ascii")
    return TurnRestCredentials(
        expires_at_epoch_seconds=expiry_seconds,
        call_tag=call_tag,
        username=username,
        credential=credential,
    )


def _require_private_directory(path: Path, *, label: str) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except OSError:
        raise CoturnContractError(f"{label} is unavailable") from None
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise CoturnContractError(f"{label} is invalid")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise CoturnContractError(f"{label} permissions are unsafe")


def _require_exact_regular_file(path: object, *, expected: Path, label: str) -> Path:
    if not isinstance(path, Path) or path != expected or not path.is_absolute():
        raise CoturnContractError(f"{label} path is invalid")
    _require_safe_path_text(path)
    try:
        details = path.stat(follow_symlinks=False)
    except OSError:
        raise CoturnContractError(f"{label} is unavailable") from None
    if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or details.st_nlink != 1:
        raise CoturnContractError(f"{label} is invalid")
    return path


def _read_regular_file_no_follow(
    path: Path,
    *,
    maximum_bytes: int,
    exact_mode: int | None = None,
    forbidden_mode_mask: int = 0,
) -> bytes:
    """Read one owned file to EOF while binding all policy to its descriptor."""

    before = path.stat(follow_symlinks=False)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _validate_opened_file_metadata(
            (before, opened),
            maximum_bytes=maximum_bytes,
            exact_mode=exact_mode,
            forbidden_mode_mask=forbidden_mode_mask,
        )
        chunks: list[bytes] = []
        length = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum_bytes:
                raise OSError("regular file is too large")
        value = b"".join(chunks)
        opened_after = os.fstat(descriptor)
        path_after = path.stat(follow_symlinks=False)
        _validate_opened_file_metadata(
            (before, opened, opened_after, path_after),
            maximum_bytes=maximum_bytes,
            exact_mode=exact_mode,
            forbidden_mode_mask=forbidden_mode_mask,
        )
        identities = {
            (details.st_dev, details.st_ino)
            for details in (
                before,
                opened,
                opened_after,
                path_after,
            )
        }
        sizes = {details.st_size for details in (before, opened, opened_after, path_after)}
        if len(identities) != 1 or sizes != {len(value)}:
            raise OSError("unsafe regular file")
        return value
    finally:
        os.close(descriptor)


def _validate_opened_file_metadata(
    values: tuple[os.stat_result, ...],
    *,
    maximum_bytes: int,
    exact_mode: int | None,
    forbidden_mode_mask: int,
) -> None:
    for details in values:
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_nlink != 1
            or details.st_size < 0
            or details.st_size > maximum_bytes
        ):
            raise OSError("unsafe regular file")
        mode = stat.S_IMODE(details.st_mode)
        if (exact_mode is not None and mode != exact_mode) or mode & forbidden_mode_mask:
            raise _UnsafeFilePermissionsError("unsafe regular file permissions")


def _require_safe_path_text(path: Path) -> None:
    value = os.fspath(path)
    if "," in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CoturnContractError("Coturn path contract is invalid")


def _validate_uuid4(value: object) -> str:
    if not isinstance(value, str):
        raise CoturnContractError("TURN voice call scope is invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise CoturnContractError("TURN voice call scope is invalid") from None
    if parsed.version != 4 or str(parsed) != value:
        raise CoturnContractError("TURN voice call scope is invalid")
    return value


def _require_utc_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0) or value.tzinfo is None:
        raise CoturnContractError(f"{label} is invalid")
    return value


__all__ = [
    "COTURN_FIXTURE_PATH",
    "COTURN_IMAGE",
    "COTURN_PLATFORM",
    "COTURN_REALM",
    "COTURN_RELAY_MAX_PORT",
    "COTURN_RELAY_MEDIA_EXECUTABLE",
    "COTURN_RELAY_MIN_PORT",
    "COTURN_TLS_PORT",
    "COTURN_TOPOLOGY_STATUS",
    "COTURN_TURNS_URL",
    "CoturnBridgeTopology",
    "CoturnConfigurationReceipt",
    "CoturnContractError",
    "CoturnContractPaths",
    "PipecatE2ENetworkMode",
    "TurnRestCredentials",
    "derive_turn_rest_credentials",
    "parse_network_mode",
    "read_private_coturn_configuration",
    "read_private_coturn_configuration_receipt",
    "render_coturn_configuration",
    "validate_coturn_fixture",
    "validate_static_auth_secret",
    "validate_turn_tls_ca_file",
]

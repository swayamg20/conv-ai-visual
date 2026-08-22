"""Source-pinned Coturn record prefix and startup grammar.

The accepted bodies are mapped to Coturn 4.17.2 commit
``de0c9b28f22281a0251d7e98aa8a097895a5b185`` and the checked-in
qualification fixture.  They are source-known, but their complete runtime
ordering remains deliberately unverified until the first pinned-image probe.

Classification returns only sanitized enums.  ``split_coturn_record`` returns
the body solely so the streaming owner can classify it synchronously; callers
must not persist or report that value because it may contain endpoints, paths,
credentials, or other sensitive source fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from scripts.voice_pipecat_e2e_coturn import COTURN_REALM, COTURN_TLS_PORT

COTURN_SOURCE_COMMIT: Final = "de0c9b28f22281a0251d7e98aa8a097895a5b185"
COTURN_MAX_RECORD_BYTES: Final = 1024

_TIMESTAMP = rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}[+-][0-9]{4}"
_PREFIX_RE = re.compile(
    rb"^(?:" + _TIMESTAMP + rb") (?P<level>INFO|WARNING|ERROR) (?P<body>[\x20-\x7e]*)$"
)
_OCTET = rb"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4 = rb"(?:" + _OCTET + rb"\.){3}" + _OCTET
_PORT = rb"(?:0|[1-9][0-9]{0,4})"
_PRINTABLE = rb"[\x20-\x7e]"
_IDENTITY = rb"[\x21-\x3b\x3d\x3f-\x7e]"
_REALM_LITERAL = re.escape(COTURN_REALM.encode("ascii"))
_TLS_PORT_LITERAL = str(COTURN_TLS_PORT).encode("ascii")
_SID = rb"(?P<sid>[0-9]{18,20})"
_U64 = rb"(?P<value>[0-9]{1,20})"


class CoturnLogCategory(str, Enum):
    """Sanitized record classes; values never contain source-log fields."""

    START_LISTENER_ADDRESS = "start_listener_address"
    START_RELAY_ADDRESS = "start_relay_address"
    START_EXTERNAL_MAPPING = "start_external_mapping"
    START_CPU = "start_cpu"
    START_VERSION = "start_version"
    START_LIMIT = "start_limit"
    START_FEATURE = "start_feature"
    START_DOMAIN = "start_domain"
    START_REALM = "start_realm"
    START_POLICY = "start_policy"
    START_CERTIFICATE = "start_certificate"
    START_PRIVATE_KEY = "start_private_key"
    START_TLS_CIPHER = "start_tls_cipher"
    START_DTLS_DISABLED = "start_dtls_disabled"
    START_PIDFILE = "start_pidfile"
    START_IO_METHOD = "start_io_method"
    START_RFC5780_DISABLED = "start_rfc5780_disabled"
    START_RELAY_PORTS_BEGIN = "start_relay_ports_begin"
    START_RELAY_PORT_BEGIN = "start_relay_port_begin"
    START_RELAY_PORT_DONE = "start_relay_port_done"
    START_RELAY_PORTS_DONE = "start_relay_ports_done"
    START_RELAY_THREADS = "start_relay_threads"
    START_AUTH_THREADS = "start_auth_threads"
    START_TLS_LISTENER = "start_tls_listener"
    START_DATABASE = "start_database"
    READINESS_ACCEPT = "readiness_accept"
    READINESS_EMPTY_SESSION = "readiness_empty_session"
    AUTH_CHALLENGE = "auth_challenge"
    ALLOCATION_NEW = "allocation_new"
    ALLOCATION_SUCCESS = "allocation_success"
    ALLOCATION_METHOD = "allocation_method"
    ALLOCATION_REFRESH = "allocation_refresh"
    ALLOCATION_USAGE = "allocation_usage"
    ALLOCATION_PEER_USAGE = "allocation_peer_usage"
    ALLOCATION_CLOSE = "allocation_close"
    ALLOCATION_DELETE = "allocation_delete"
    UNKNOWN_INFO = "unknown_info"


@dataclass(frozen=True)
class CoturnStartupRecord:
    """One transient, source-known startup classification.

    Address and port captures exist only so the evidence owner can compare them
    with its configuration-bound topology. They are excluded from diagnostics.
    """

    category: CoturnLogCategory
    ipv4: bytes | None = field(default=None, repr=False)
    port: bytes | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "CoturnStartupRecord()"


_STARTUP_PATTERNS: Final[tuple[tuple[CoturnLogCategory, re.Pattern[bytes]], ...]] = (
    (
        CoturnLogCategory.START_LISTENER_ADDRESS,
        re.compile(rb"^Listener address to use: (?P<ipv4>" + _IPV4 + rb")$"),
    ),
    (
        CoturnLogCategory.START_RELAY_ADDRESS,
        re.compile(rb"^Relay address to use: (?P<ipv4>" + _IPV4 + rb")$"),
    ),
    (
        CoturnLogCategory.START_EXTERNAL_MAPPING,
        re.compile(rb"^Whitelisting external-ip private part: (?P<ipv4>" + _IPV4 + rb")$"),
    ),
    (
        CoturnLogCategory.START_CPU,
        re.compile(
            rb"^(?:System cpu num is|System enable num is|Configured cpu num is) [0-9]{1,3}$"
        ),
    ),
    (
        CoturnLogCategory.START_VERSION,
        re.compile(rb"^Coturn Version Coturn-4\.17\.2(?: " + _PRINTABLE + rb"{1,160})?$"),
    ),
    (
        CoturnLogCategory.START_LIMIT,
        re.compile(
            rb"^(?:Max number of open files/sockets allowed for this process: [0-9]{1,20}|"
            rb"Due to the open files/sockets limitation, max supported number of TURN Sessions possible is: "
            rb"[0-9]{1,20} \(approximately\))$"
        ),
    ),
    (
        CoturnLogCategory.START_FEATURE,
        re.compile(
            rb"^(?:==== Show him the instruments, Practical Frost: ====|"
            rb"OpenSSL compile-time version: " + _PRINTABLE + rb"{1,200} \(0x[0-9a-fA-F]{1,16}\)|"
            rb"TLS(?: 1(?:\.1|\.2|\.3)?)? (?:is not )?supported|"
            rb"DTLS(?: 1\.2)? (?:is not )?supported|TURN/STUN ALPN supported|"
            rb"Third-party authorization \(oAuth\) (?:is not )?supported|"
            rb"GCM \(AEAD\) (?:is not )?supported|"
            rb"SQLite supported, default database location is "
            + _PRINTABLE
            + rb"{1,512}|SQLite is not supported|"
            rb"(?:Redis|PostgreSQL|MySQL|MongoDB) (?:is not )?supported|"
            rb"Net Engine: UDP thread per CPU core)$"
        ),
    ),
    (CoturnLogCategory.START_DOMAIN, re.compile(rb"^Domain name: [A-Za-z0-9._-]{0,253}$")),
    (
        CoturnLogCategory.START_REALM,
        re.compile(rb"^Default realm: " + _REALM_LITERAL + rb"$"),
    ),
    (
        CoturnLogCategory.START_POLICY,
        re.compile(rb"^CONFIG: --no-tcp-relay: TCP relay endpoints are not allowed\.$"),
    ),
    (
        CoturnLogCategory.START_CERTIFICATE,
        re.compile(rb"^Certificate file found: /run/murmur-coturn/cert\.pem$"),
    ),
    (
        CoturnLogCategory.START_PRIVATE_KEY,
        re.compile(rb"^Private key file found: /run/murmur-coturn/key\.pem$"),
    ),
    (
        CoturnLogCategory.START_TLS_CIPHER,
        re.compile(rb"^TLS cipher suite: " + _PRINTABLE + rb"{1,900}$"),
    ),
    (
        CoturnLogCategory.START_DTLS_DISABLED,
        re.compile(rb"^DTLS listeners are not started; use --dtls to start them$"),
    ),
    (
        CoturnLogCategory.START_PIDFILE,
        re.compile(rb"^pid file created: /tmp/turnserver\.pid$"),
    ),
    (
        CoturnLogCategory.START_IO_METHOD,
        re.compile(rb"^IO method: epoll$"),
    ),
    (
        CoturnLogCategory.START_RFC5780_DISABLED,
        re.compile(rb"^RFC5780 disabled! /NAT behavior discovery/$"),
    ),
    (
        CoturnLogCategory.START_RELAY_PORTS_BEGIN,
        re.compile(rb"^Wait for relay ports initialization\.\.\.$"),
    ),
    (
        CoturnLogCategory.START_RELAY_PORT_BEGIN,
        re.compile(rb"^  relay (?P<ipv4>" + _IPV4 + rb") initialization\.\.\.$"),
    ),
    (
        CoturnLogCategory.START_RELAY_PORT_DONE,
        re.compile(rb"^  relay (?P<ipv4>" + _IPV4 + rb") initialization done$"),
    ),
    (
        CoturnLogCategory.START_RELAY_PORTS_DONE,
        re.compile(rb"^Relay ports initialization done$"),
    ),
    (
        CoturnLogCategory.START_RELAY_THREADS,
        re.compile(rb"^Total relay threads: 1$"),
    ),
    (
        CoturnLogCategory.START_AUTH_THREADS,
        re.compile(rb"^Total auth threads: 2$"),
    ),
    (
        CoturnLogCategory.START_TLS_LISTENER,
        re.compile(
            rb"^IPv4\. TLS listener opened on : (?P<ipv4>"
            + _IPV4
            + rb"):(?P<port>"
            + _TLS_PORT_LITERAL
            + rb")$"
        ),
    ),
    (
        CoturnLogCategory.START_DATABASE,
        re.compile(rb"^SQLite DB connection success: /tmp/turnserver\.db$"),
    ),
    (
        CoturnLogCategory.READINESS_ACCEPT,
        re.compile(
            rb"^IPv4\. tcp or tls connected to: (?P<ipv4>"
            + _IPV4
            + rb"):(?P<port>"
            + _PORT
            + rb")$"
        ),
    ),
)

COTURN_REQUIRED_STARTUP: Final[frozenset[CoturnLogCategory]] = frozenset(
    {
        CoturnLogCategory.START_LISTENER_ADDRESS,
        CoturnLogCategory.START_RELAY_ADDRESS,
        CoturnLogCategory.START_EXTERNAL_MAPPING,
        CoturnLogCategory.START_VERSION,
        CoturnLogCategory.START_REALM,
        CoturnLogCategory.START_POLICY,
        CoturnLogCategory.START_CERTIFICATE,
        CoturnLogCategory.START_PRIVATE_KEY,
        CoturnLogCategory.START_TLS_CIPHER,
        CoturnLogCategory.START_DTLS_DISABLED,
        CoturnLogCategory.START_PIDFILE,
        CoturnLogCategory.START_IO_METHOD,
        CoturnLogCategory.START_RFC5780_DISABLED,
        CoturnLogCategory.START_RELAY_PORTS_BEGIN,
        CoturnLogCategory.START_RELAY_PORT_BEGIN,
        CoturnLogCategory.START_RELAY_PORT_DONE,
        CoturnLogCategory.START_RELAY_PORTS_DONE,
        CoturnLogCategory.START_RELAY_THREADS,
        CoturnLogCategory.START_AUTH_THREADS,
        CoturnLogCategory.START_TLS_LISTENER,
        CoturnLogCategory.START_DATABASE,
    }
)

COTURN_CRITICAL_STARTUP_ORDER: Final[tuple[CoturnLogCategory, ...]] = (
    CoturnLogCategory.START_LISTENER_ADDRESS,
    CoturnLogCategory.START_RELAY_ADDRESS,
    CoturnLogCategory.START_EXTERNAL_MAPPING,
    CoturnLogCategory.START_VERSION,
    CoturnLogCategory.START_REALM,
    CoturnLogCategory.START_POLICY,
    CoturnLogCategory.START_CERTIFICATE,
    CoturnLogCategory.START_PRIVATE_KEY,
    CoturnLogCategory.START_TLS_CIPHER,
    CoturnLogCategory.START_DTLS_DISABLED,
    CoturnLogCategory.START_PIDFILE,
    CoturnLogCategory.START_IO_METHOD,
    CoturnLogCategory.START_RFC5780_DISABLED,
    CoturnLogCategory.START_RELAY_PORTS_BEGIN,
    CoturnLogCategory.START_RELAY_PORT_BEGIN,
    CoturnLogCategory.START_RELAY_PORT_DONE,
    CoturnLogCategory.START_RELAY_PORTS_DONE,
    CoturnLogCategory.START_TLS_LISTENER,
    CoturnLogCategory.START_RELAY_THREADS,
    CoturnLogCategory.START_AUTH_THREADS,
    CoturnLogCategory.START_DATABASE,
)

_REALM = rb"(?P<realm>" + _IDENTITY + rb"{1,127})"
_USERNAME = rb"(?P<username>" + _IDENTITY + rb"{1,512})"
_METHOD = rb"(?P<method>TLSv1(?:\.1|\.2|\.3)?)"
_LIFETIME = rb"(?P<lifetime>[0-9]{1,20})"
_TRAFFIC = (
    rb"rp=(?P<rp>[0-9]{1,20}), rb=(?P<rb>[0-9]{1,20}), "
    rb"sp=(?P<sp>[0-9]{1,20}), sb=(?P<sb>[0-9]{1,20})"
)
_METHOD_HEAD = (
    rb"^session "
    + _SID
    + rb": (?:origin <[^>\r\n]{0,512}> )?realm <"
    + _REALM
    + rb"> user <"
    + _USERNAME
    + rb">: incoming packet "
)


class CoturnLifecyclePatterns:
    """Static source grammar; no call identity is embedded in a regex cache."""

    __slots__ = ()

    new: Final = re.compile(
        rb"^session "
        + _SID
        + rb": new, realm=<"
        + _REALM
        + rb">, username=<"
        + _USERNAME
        + rb">, lifetime="
        + _LIFETIME
        + rb", cipher=[^,<>\r\n]{1,255}, method="
        + _METHOD
        + rb"$"
    )
    allocate_success: Final = re.compile(_METHOD_HEAD + rb"ALLOCATE processed, success$")
    method_success: Final = re.compile(
        _METHOD_HEAD + rb"(?P<request>CREATE_PERMISSION|CHANNEL_BIND) processed, success$"
    )
    refreshed: Final = re.compile(
        rb"^session "
        + _SID
        + rb": refreshed, realm=<"
        + _REALM
        + rb">, username=<"
        + _USERNAME
        + rb">, lifetime="
        + _LIFETIME
        + rb", cipher=[^,<>\r\n]{1,255}, method="
        + _METHOD
        + rb"$"
    )
    refresh_success: Final = re.compile(_METHOD_HEAD + rb"REFRESH processed, success$")
    usage: Final = re.compile(
        rb"^session "
        + _SID
        + rb": usage: realm=<"
        + _REALM
        + rb">, username=<"
        + _USERNAME
        + rb">, "
        + _TRAFFIC
        + rb"$"
    )
    peer_usage: Final = re.compile(
        rb"^session "
        + _SID
        + rb": peer usage: realm=<"
        + _REALM
        + rb">, username=<"
        + _USERNAME
        + rb">, "
        + _TRAFFIC
        + rb"$"
    )
    closed: Final = re.compile(
        rb"^session "
        + _SID
        + rb": closed \(2nd stage\), user <"
        + _USERNAME
        + rb"> realm <"
        + _REALM
        + rb"> origin <[^>\r\n]{0,512}>, local (?P<local_ipv4>"
        + _IPV4
        + rb"):(?P<local_port>"
        + _PORT
        + rb"), remote (?P<remote_ipv4>"
        + _IPV4
        + rb"):(?P<remote_port>"
        + _PORT
        + rb"), reason: "
        + _PRINTABLE
        + rb"{1,256}$"
    )
    delete: Final = re.compile(
        rb"^session "
        + _SID
        + rb": delete: realm=<"
        + _REALM
        + rb">, username=<"
        + _USERNAME
        + rb">$"
    )
    empty_usage: Final = re.compile(
        rb"^session "
        + _SID
        + rb": usage: realm=<(?P<realm>"
        + _IDENTITY
        + rb"{0,127})>, username=<>, "
        + _TRAFFIC
        + rb"$"
    )
    empty_peer_usage: Final = re.compile(
        rb"^session "
        + _SID
        + rb": peer usage: realm=<(?P<realm>"
        + _IDENTITY
        + rb"{0,127})>, username=<>, "
        + _TRAFFIC
        + rb"$"
    )
    empty_close: Final = re.compile(
        rb"^session "
        + _SID
        + rb": closed \(2nd stage\), user <> realm <(?P<realm>"
        + _IDENTITY
        + rb"{0,127})> origin <[^>\r\n]{0,512}>, local (?P<local_ipv4>"
        + _IPV4
        + rb"):(?P<local_port>"
        + _PORT
        + rb"), remote (?P<remote_ipv4>"
        + _IPV4
        + rb"):(?P<remote_port>"
        + _PORT
        + rb"), reason: "
        + _PRINTABLE
        + rb"{1,256}$"
    )
    transport_close: Final = re.compile(
        rb"^session "
        + _SID
        + rb": (?:TLS/TCP|TCP) socket (?:closed remotely |disconnected: )"
        + rb"(?P<remote_ipv4>"
        + _IPV4
        + rb"):(?P<remote_port>"
        + _PORT
        + rb")$"
    )
    empty_challenge: Final = re.compile(
        rb"^session "
        + _SID
        + rb": (?:origin <[^>\r\n]{0,512}> )?realm <(?P<realm>"
        + _IDENTITY
        + rb"{0,127})> user <>: incoming packet message processed, error "
        + rb"(?P<code>401: Unauthorized|438: Stale Nonce)$"
    )
    stale_challenge: Final = re.compile(
        _METHOD_HEAD + rb"message processed, error 438: Stale Nonce$"
    )


COTURN_LIFECYCLE_PATTERNS: Final = CoturnLifecyclePatterns()


def split_coturn_record(record: bytes) -> tuple[bytes, bytes] | None:
    """Return a transient ``(severity, body)`` pair for one prefix-valid record."""

    match: re.Match[bytes] | None = None
    try:
        match = _PREFIX_RE.fullmatch(record)
        if match is None:
            return None
        return match.group("level"), match.group("body")
    finally:
        record = b""
        match = None


def match_coturn_startup_info(body: bytes) -> CoturnStartupRecord | None:
    """Return one transient structured startup/readiness classification."""

    match: re.Match[bytes] | None = None
    groups: dict[str, bytes | None] | None = None
    try:
        for category, pattern in _STARTUP_PATTERNS:
            match = pattern.fullmatch(body)
            if match is not None:
                groups = match.groupdict()
                return CoturnStartupRecord(
                    category=category,
                    ipv4=groups.get("ipv4"),
                    port=groups.get("port"),
                )
        return None
    finally:
        body = b""
        match = None
        groups = None


def classify_coturn_startup_info(body: bytes) -> CoturnLogCategory | None:
    """Map an exact source-known startup/readiness body to a sanitized enum."""

    record: CoturnStartupRecord | None = None
    try:
        record = match_coturn_startup_info(body)
        return None if record is None else record.category
    finally:
        body = b""
        record = None


__all__ = [
    "COTURN_CRITICAL_STARTUP_ORDER",
    "COTURN_LIFECYCLE_PATTERNS",
    "COTURN_MAX_RECORD_BYTES",
    "COTURN_REALM",
    "COTURN_REQUIRED_STARTUP",
    "COTURN_SOURCE_COMMIT",
    "CoturnLifecyclePatterns",
    "CoturnLogCategory",
    "CoturnStartupRecord",
    "classify_coturn_startup_info",
    "match_coturn_startup_info",
    "split_coturn_record",
]

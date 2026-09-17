"""Synthetic contract tests for the source-pinned Coturn startup grammar."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn_log_grammar import (  # noqa: E402
    COTURN_MAX_RECORD_BYTES,
    COTURN_REALM,
    COTURN_REQUIRED_STARTUP,
    COTURN_SOURCE_COMMIT,
    CoturnLogCategory,
    CoturnStartupRecord,
    classify_coturn_startup_info,
    match_coturn_startup_info,
    split_coturn_record,
)

TIMESTAMP = b"2026-08-16T12:34:56.789+0000"


@pytest.mark.parametrize(
    ("body", "category"),
    [
        (b"Listener address to use: 172.30.0.2", CoturnLogCategory.START_LISTENER_ADDRESS),
        (b"Relay address to use: 172.30.0.2", CoturnLogCategory.START_RELAY_ADDRESS),
        (
            b"Whitelisting external-ip private part: 172.30.0.2",
            CoturnLogCategory.START_EXTERNAL_MAPPING,
        ),
        (b"Configured cpu num is 12", CoturnLogCategory.START_CPU),
        (b"Coturn Version Coturn-4.17.2 'source fixture'", CoturnLogCategory.START_VERSION),
        (
            b"Due to the open files/sockets limitation, max supported number of TURN Sessions "
            b"possible is: 500 (approximately)",
            CoturnLogCategory.START_LIMIT,
        ),
        (b"TLS 1.3 supported", CoturnLogCategory.START_FEATURE),
        (b"Domain name: ", CoturnLogCategory.START_DOMAIN),
        (b"Default realm: voice-pipecat-e2e.invalid", CoturnLogCategory.START_REALM),
        (
            b"CONFIG: --no-tcp-relay: TCP relay endpoints are not allowed.",
            CoturnLogCategory.START_POLICY,
        ),
        (
            b"Certificate file found: /run/murmur-coturn/cert.pem",
            CoturnLogCategory.START_CERTIFICATE,
        ),
        (
            b"Private key file found: /run/murmur-coturn/key.pem",
            CoturnLogCategory.START_PRIVATE_KEY,
        ),
        (b"TLS cipher suite: DEFAULT", CoturnLogCategory.START_TLS_CIPHER),
        (
            b"DTLS listeners are not started; use --dtls to start them",
            CoturnLogCategory.START_DTLS_DISABLED,
        ),
        (b"pid file created: /tmp/turnserver.pid", CoturnLogCategory.START_PIDFILE),
        (b"IO method: epoll", CoturnLogCategory.START_IO_METHOD),
        (
            b"RFC5780 disabled! /NAT behavior discovery/",
            CoturnLogCategory.START_RFC5780_DISABLED,
        ),
        (
            b"Wait for relay ports initialization...",
            CoturnLogCategory.START_RELAY_PORTS_BEGIN,
        ),
        (
            b"  relay 172.30.0.2 initialization...",
            CoturnLogCategory.START_RELAY_PORT_BEGIN,
        ),
        (
            b"  relay 172.30.0.2 initialization done",
            CoturnLogCategory.START_RELAY_PORT_DONE,
        ),
        (b"Relay ports initialization done", CoturnLogCategory.START_RELAY_PORTS_DONE),
        (b"Total relay threads: 1", CoturnLogCategory.START_RELAY_THREADS),
        (b"Total auth threads: 2", CoturnLogCategory.START_AUTH_THREADS),
        (
            b"IPv4. TLS listener opened on : 172.30.0.2:5349",
            CoturnLogCategory.START_TLS_LISTENER,
        ),
        (
            b"SQLite DB connection success: /tmp/turnserver.db",
            CoturnLogCategory.START_DATABASE,
        ),
        (
            b"IPv4. tcp or tls connected to: 172.30.0.1:43000",
            CoturnLogCategory.READINESS_ACCEPT,
        ),
    ],
)
def test_source_known_startup_bodies_map_only_to_sanitized_categories(
    body: bytes,
    category: CoturnLogCategory,
) -> None:
    classified = classify_coturn_startup_info(body)
    assert classified is category
    assert b"172.30" not in classified.value.encode("ascii")
    assert b"/run/" not in classified.value.encode("ascii")


def test_required_startup_contract_is_explicit_and_excludes_optional_probe_lines() -> None:
    expected = {
        category
        for category in CoturnLogCategory
        if category.value.startswith("start_")
        and category
        not in {
            CoturnLogCategory.START_CPU,
            CoturnLogCategory.START_LIMIT,
            CoturnLogCategory.START_FEATURE,
            CoturnLogCategory.START_DOMAIN,
        }
    }
    assert COTURN_REQUIRED_STARTUP == expected
    assert CoturnLogCategory.READINESS_ACCEPT not in COTURN_REQUIRED_STARTUP


@pytest.mark.parametrize(
    "body",
    [
        b"Total relay threads: 2",
        b"Total auth threads: 3",
        b"IPv4. TLS listener opened on : 172.30.0.2:3478",
        b"Default realm: attacker.invalid",
        b"Certificate file found: /tmp/cert.pem",
        b"Private key file found: /tmp/key.pem",
        b"SQLite DB connection success: /var/lib/coturn/turndb",
        b"Listener address to use: 999.30.0.2",
        b"IO method: poll",
        b"CONFIG: --no-cli option is deprecated, see --cli",
        b"CONFIG: Unknown argument: no-tlsv1",
    ],
)
def test_fixture_specific_required_bodies_fail_closed_on_drift(body: bytes) -> None:
    assert classify_coturn_startup_info(body) is None


@pytest.mark.parametrize("level", [b"INFO", b"WARNING", b"ERROR"])
def test_record_prefix_returns_only_transient_severity_and_body(level: bytes) -> None:
    body = b"memory-only source record"
    assert split_coturn_record(TIMESTAMP + b" " + level + b" " + body) == (level, body)


@pytest.mark.parametrize(
    "record",
    [
        b"2026-08-16 12:34:56 INFO body",
        TIMESTAMP + b" DEBUG body",
        TIMESTAMP + b" INFO body\n",
        TIMESTAMP + b" INFO non-ascii-\xff",
        TIMESTAMP + b" INFO embedded\rreturn",
    ],
)
def test_record_prefix_rejects_non_logger_or_non_ascii_records(record: bytes) -> None:
    assert split_coturn_record(record) is None


def test_pinned_constants_are_frozen() -> None:
    assert COTURN_SOURCE_COMMIT == "de0c9b28f22281a0251d7e98aa8a097895a5b185"
    assert COTURN_REALM == "voice-pipecat-e2e.invalid"
    assert COTURN_MAX_RECORD_BYTES == 1024


def test_structured_startup_capture_is_transient_and_redacted() -> None:
    record = match_coturn_startup_info(b"IPv4. TLS listener opened on : 172.30.0.2:5349")
    assert isinstance(record, CoturnStartupRecord)
    assert record.category is CoturnLogCategory.START_TLS_LISTENER
    assert record.ipv4 == b"172.30.0.2"
    assert record.port == b"5349"
    assert repr(record) == "CoturnStartupRecord()"
    assert "172.30" not in repr(record)

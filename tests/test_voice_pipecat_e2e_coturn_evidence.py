"""Synthetic tests for the source-pinned Coturn stdout grammar; no Docker is run."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_evidence as coturn_evidence_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence_result as coturn_evidence_result_module,
)
from scripts import voice_pipecat_e2e_coturn_evidence_state as coturn_state  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    COTURN_MAX_RECORD_BYTES,
    CoturnEvidence,
    CoturnEvidenceError,
    CoturnEvidenceParser,
    CoturnLogCategory,
    CoturnProbeResultSlot,
    CoturnProbeSummary,
    coturn_probe_summary_from_slot,
    new_coturn_probe_result_slot,
    parse_coturn_evidence,
    parse_coturn_probe,
)

USERNAME = "1780000000:0123456789abcdef"
REALM = "voice-pipecat-e2e.invalid"
TIMESTAMP = b"2026-08-16T12:34:56.789+0000"
TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.30.0.0/29",
    gateway="172.30.0.1",
    container="172.30.0.2",
)


def _log(body: str | bytes, *, level: str = "INFO") -> bytes:
    encoded = body.encode("ascii") if isinstance(body, str) else body
    return TIMESTAMP + b" " + level.encode("ascii") + b" " + encoded + b"\n"


def _sid(value: int) -> str:
    return f"{value:018d}"


def _startup() -> list[bytes]:
    return [
        _log("Listener address to use: 172.30.0.2"),
        _log("Relay address to use: 172.30.0.2"),
        _log("Whitelisting external-ip private part: 172.30.0.2"),
        _log("System cpu num is 12"),
        _log("System enable num is 12"),
        _log("Configured cpu num is 12"),
        _log("Coturn Version Coturn-4.17.2 'source fixture'"),
        _log("Max number of open files/sockets allowed for this process: 1024"),
        _log(
            "Due to the open files/sockets limitation, max supported number of TURN Sessions "
            "possible is: 500 (approximately)"
        ),
        _log("==== Show him the instruments, Practical Frost: ===="),
        _log("OpenSSL compile-time version: OpenSSL 3.5.1 (0x30500010)"),
        _log("TLS 1.2 supported"),
        _log("TLS 1.3 supported"),
        _log("DTLS 1.2 supported"),
        _log("TURN/STUN ALPN supported"),
        _log("Third-party authorization (oAuth) supported"),
        _log("GCM (AEAD) supported"),
        _log("SQLite supported, default database location is /var/lib/coturn/turndb"),
        _log("Redis supported"),
        _log("PostgreSQL supported"),
        _log("MySQL supported"),
        _log("MongoDB supported"),
        _log("Net Engine: UDP thread per CPU core"),
        _log("Domain name: "),
        _log(f"Default realm: {REALM}"),
        _log("CONFIG: --no-tcp-relay: TCP relay endpoints are not allowed."),
        _log("Certificate file found: /run/murmur-coturn/cert.pem"),
        _log("Private key file found: /run/murmur-coturn/key.pem"),
        _log("TLS cipher suite: DEFAULT:TLS_AES_256_GCM_SHA384"),
        _log("DTLS listeners are not started; use --dtls to start them"),
        _log("pid file created: /tmp/turnserver.pid"),
        _log("IO method: epoll"),
        _log("RFC5780 disabled! /NAT behavior discovery/"),
        _log("Wait for relay ports initialization..."),
        _log("  relay 172.30.0.2 initialization..."),
        _log("  relay 172.30.0.2 initialization done"),
        _log("Relay ports initialization done"),
        _log("IPv4. TLS listener opened on : 172.30.0.2:5349"),
        _log("Total relay threads: 1"),
        _log("Total auth threads: 2"),
        _log("SQLite DB connection success: /tmp/turnserver.db"),
    ]


def _new(
    value: int,
    *,
    username: str = USERNAME,
    lifetime: int = 600,
    transport_method: str = "TLSv1.3",
) -> bytes:
    return _log(
        f"session {_sid(value)}: new, realm=<{REALM}>, username=<{username}>, "
        f"lifetime={lifetime}, cipher=TLS_AES_256_GCM_SHA384, method={transport_method}"
    )


def _method(value: int, method: str, *, username: str = USERNAME) -> bytes:
    return _log(
        f"session {_sid(value)}: realm <{REALM}> user <{username}>: incoming packet "
        f"{method} processed, success"
    )


def _refresh(value: int, lifetime: int, *, transport_method: str = "TLSv1.3") -> bytes:
    return _log(
        f"session {_sid(value)}: refreshed, realm=<{REALM}>, username=<{USERNAME}>, "
        f"lifetime={lifetime}, cipher=TLS_AES_256_GCM_SHA384, method={transport_method}"
    )


def _usage(value: int, counters: tuple[int, int, int, int], *, peer: bool = False) -> bytes:
    rp, rb, sp, sb = counters
    kind = "peer usage" if peer else "usage"
    return _log(
        f"session {_sid(value)}: {kind}: realm=<{REALM}>, username=<{USERNAME}>, "
        f"rp={rp}, rb={rb}, sp={sp}, sb={sb}"
    )


def _close(value: int) -> bytes:
    return _log(
        f"session {_sid(value)}: closed (2nd stage), user <{USERNAME}> realm <{REALM}> "
        "origin <https://memory-only.invalid>, local 172.30.0.2:5349, "
        "remote 172.30.0.1:41000, reason: allocation timeout"
    )


def _delete(value: int) -> bytes:
    return _log(f"session {_sid(value)}: delete: realm=<{REALM}>, username=<{USERNAME}>")


def _complete_allocation(
    value: int,
    *,
    transport_method: str = "TLSv1.3",
    active_regular: tuple[int, int, int, int] = (1, 10, 2, 20),
    active_peer: tuple[int, int, int, int] = (3, 30, 4, 40),
    final_regular: tuple[int, int, int, int] = (5, 50, 6, 60),
    final_peer: tuple[int, int, int, int] = (7, 70, 8, 80),
) -> list[bytes]:
    return [
        _new(value, transport_method=transport_method),
        _method(value, "ALLOCATE"),
        _method(value, "CREATE_PERMISSION"),
        _method(value, "CHANNEL_BIND"),
        _usage(value, active_regular),
        _usage(value, active_peer, peer=True),
        _refresh(value, 600, transport_method=transport_method),
        _method(value, "REFRESH"),
        _refresh(value, 0, transport_method=transport_method),
        _method(value, "REFRESH"),
        _usage(value, final_regular),
        _usage(value, final_peer, peer=True),
        _close(value),
        _delete(value),
    ]


def _parse(records: list[bytes]) -> CoturnEvidence:
    return parse_coturn_evidence(
        records,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )


def _parser(*, probe: bool = False, result_slot: object = None) -> CoturnEvidenceParser:
    factory = CoturnEvidenceParser.for_probe if probe else CoturnEvidenceParser
    arguments: dict[str, object] = {
        "expected_username": USERNAME,
        "expected_topology": TOPOLOGY,
    }
    if probe:
        arguments["result_slot"] = result_slot
    return factory(**arguments)


def _replace_startup(records: list[bytes], prefix: bytes, replacement: bytes) -> list[bytes]:
    updated = list(records)
    index = next(index for index, record in enumerate(updated) if prefix in record)
    updated[index] = _log(replacement)
    return updated


def test_one_allocation_is_streamed_redacted_and_sums_delta_pairs() -> None:
    records = _startup()
    records.extend(_complete_allocation(1))
    payload = b"".join(records)
    parser = _parser()
    for byte in payload:
        parser.feed(bytes((byte,)))
    evidence = parser.finish()

    assert evidence.allocation_count == 1
    assert evidence.traffic.received_packets == 6
    assert evidence.traffic.received_bytes == 60
    assert evidence.traffic.sent_packets == 8
    assert evidence.traffic.sent_bytes == 80
    assert evidence.traffic.peer_received_packets == 10
    assert evidence.traffic.peer_received_bytes == 100
    assert evidence.traffic.peer_sent_packets == 12
    assert evidence.traffic.peer_sent_bytes == 120
    assert evidence.unknown_info_records == 0
    assert CoturnLogCategory.UNKNOWN_INFO not in evidence.observed_categories
    assert CoturnLogCategory.ALLOCATION_DELETE in evidence.observed_categories
    assert repr(parser) == "CoturnEvidenceParser()"
    assert repr(evidence) == "CoturnEvidence()"
    assert USERNAME not in repr(parser)
    assert USERNAME not in repr(evidence)


def test_one_source_known_startup_record_may_have_one_adjacent_stdout_duplicate() -> None:
    startup = _startup()
    evidence = _parse([startup[0], *startup, *_complete_allocation(1)])
    assert evidence.allocation_count == 1


def test_two_allocations_may_interleave_but_each_sid_order_remains_strict() -> None:
    records = [*_startup(), _new(1), _method(1, "ALLOCATE"), _new(2), _method(2, "ALLOCATE")]
    records.extend(
        [
            _usage(1, (1, 10, 1, 10)),
            _usage(2, (2, 20, 2, 20)),
            _usage(2, (3, 30, 3, 30), peer=True),
            _usage(1, (4, 40, 4, 40), peer=True),
            _refresh(1, 0),
            _refresh(2, 0),
            _method(2, "REFRESH"),
            _method(1, "REFRESH"),
            _usage(2, (0, 0, 0, 0)),
            _usage(1, (0, 0, 0, 0)),
            _usage(1, (0, 0, 0, 0), peer=True),
            _usage(2, (0, 0, 0, 0), peer=True),
            _close(2),
            _close(1),
            _delete(1),
            _delete(2),
        ]
    )
    evidence = _parse(records)
    assert evidence.allocation_count == 2
    assert evidence.traffic.peer_received_bytes == 70
    assert evidence.traffic.peer_sent_bytes == 70


def test_readiness_probe_and_empty_auth_session_are_sanitized_not_allocations() -> None:
    records = [
        *_startup(),
        _log("IPv4. tcp or tls connected to: 172.30.0.1:43000"),
        _log(
            f"session {_sid(90)}: realm <{REALM}> user <>: incoming packet message "
            "processed, error 401: Unauthorized"
        ),
        _log(f"session {_sid(90)}: usage: realm=<{REALM}>, username=<>, rp=0, rb=0, sp=0, sb=0"),
        _log(
            f"session {_sid(90)}: peer usage: realm=<{REALM}>, username=<>, rp=0, rb=0, sp=0, sb=0"
        ),
        _log(f"session {_sid(90)}: TLS/TCP socket closed remotely 172.30.0.1:43000"),
        _log(
            f"session {_sid(90)}: closed (2nd stage), user <> realm <{REALM}> origin <>, "
            "local 172.30.0.2:5349, remote 172.30.0.1:43000, reason: TLS/TCP connection "
            "closed by client (callback)"
        ),
        *_complete_allocation(1),
    ]
    evidence = _parse(records)
    assert evidence.allocation_count == 1
    assert CoturnLogCategory.READINESS_ACCEPT in evidence.observed_categories
    assert CoturnLogCategory.READINESS_EMPTY_SESSION in evidence.observed_categories
    assert CoturnLogCategory.AUTH_CHALLENGE in evidence.observed_categories


def test_stale_nonce_challenge_is_allowed_only_for_an_active_matching_sid() -> None:
    records = [*_startup(), _new(1), _method(1, "ALLOCATE")]
    records.append(
        _log(
            f"session {_sid(1)}: realm <{REALM}> user <{USERNAME}>: incoming packet message "
            "processed, error 438: Stale Nonce"
        )
    )
    records.extend(
        [
            _refresh(1, 0),
            _method(1, "REFRESH"),
            _usage(1, (1, 1, 1, 1)),
            _usage(1, (1, 2, 1, 3), peer=True),
            _close(1),
            _delete(1),
        ]
    )
    evidence = _parse(records)
    assert CoturnLogCategory.AUTH_CHALLENGE in evidence.observed_categories

    invalid = [*_startup()]
    invalid.append(records[len(_startup()) + 2])
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation correlation is invalid$"):
        _parse(invalid)


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_any_unsafe_severity_fails_without_reflecting_the_body(level: str) -> None:
    secret = f"unsafe-{USERNAME}"
    parser = _parser()
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn reported an unsafe severity$"
    ) as captured:
        parser.feed(_log(secret, level=level))
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_unknown_session_or_expected_username_info_fails_closed_and_redacted() -> None:
    for body in (
        f"session {_sid(3)}: future grammar containing {USERNAME}",
        f"future startup output containing {USERNAME}",
    ):
        with pytest.raises(
            CoturnEvidenceError, match=r"^Coturn grammar evidence is unverified$"
        ) as captured:
            _parse([*_startup(), _log(body), *_complete_allocation(1)])
        assert USERNAME not in str(captured.value)
        assert USERNAME not in repr(captured.value)


def test_unknown_info_is_discarded_and_strictly_bounded() -> None:
    accepted = [*_startup(), *(_log(f"unknown safe class {index}") for index in range(64))]
    accepted.extend(_complete_allocation(1))
    summary = parse_coturn_probe(
        accepted,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    assert isinstance(summary, CoturnProbeSummary)
    assert summary.grammar_verified is False
    assert not summary
    assert summary.unknown_info_records == 64
    assert repr(summary) == "CoturnProbeSummary(grammar_verified=False)"
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn grammar evidence is unverified$"):
        _parse(accepted)

    parser = _parser(probe=True)
    for record in _startup():
        parser.feed(record)
    for index in range(64):
        parser.feed(_log(f"unknown safe class {index}"))
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn emitted too many unknown info records$"
    ):
        parser.feed(_log("one too many"))


def test_record_content_cap_is_exact_and_partial_state_is_bounded() -> None:
    prefix = TIMESTAMP + b" INFO "
    allowed_body = b"x" * (COTURN_MAX_RECORD_BYTES - 1 - len(prefix))
    parser = _parser()
    parser.feed(prefix + allowed_body + b"\n")

    parser = _parser()
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn log record is oversized$"):
        parser.feed(prefix + allowed_body + b"x")
    assert repr(parser) == "CoturnEvidenceParser()"


def test_total_record_count_cap_is_exact_and_terminal() -> None:
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    readiness = _log("IPv4. tcp or tls connected to: 172.30.0.1:43000")
    while parser._record_count < 8192:
        parser.feed(readiness)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn log stream is oversized$"):
        parser.feed(readiness)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn evidence parser is unavailable$"):
        parser.finish()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\n", "Coturn log record is malformed"),
        (b"not-a-coturn-record\n", "Coturn log record is malformed"),
        (TIMESTAMP + b" INFO non-ascii-\xff\n", "Coturn log record is malformed"),
    ],
)
def test_malformed_records_fail_with_fixed_errors(payload: bytes, message: str) -> None:
    parser = _parser()
    with pytest.raises(CoturnEvidenceError, match=rf"^{message}$"):
        parser.feed(payload)


def test_unterminated_record_and_parser_reuse_fail() -> None:
    parser = _parser()
    parser.feed(TIMESTAMP + b" INFO partial")
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn log stream is truncated$"):
        parser.finish()
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn evidence parser is unavailable$"):
        parser.feed(b"")

    parser = _parser()
    for record in [*_startup(), *_complete_allocation(1)]:
        parser.feed(record)
    parser.finish()
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn evidence parser is unavailable$"):
        parser.finish()


def test_missing_startup_or_allocation_is_rejected() -> None:
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn startup evidence is incomplete$"):
        _parse(_complete_allocation(1))
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation evidence is incomplete$"):
        parser.finish()


@pytest.mark.parametrize(
    "records",
    [
        [*_startup(), _new(1), _method(1, "ALLOCATE")],
        [*_startup(), _new(1), _method(1, "ALLOCATE"), _close(1)],
        [
            *_startup(),
            _new(1),
            _method(1, "ALLOCATE"),
            _refresh(1, 0),
            _method(1, "REFRESH"),
            _usage(1, (1, 1, 1, 1)),
            _usage(1, (1, 1, 1, 1), peer=True),
            _close(1),
        ],
    ],
)
def test_all_allocations_require_explicit_release_final_usage_close_and_delete(
    records: list[bytes],
) -> None:
    with pytest.raises(CoturnEvidenceError):
        _parse(records)


def test_third_allocation_and_duplicate_new_are_rejected() -> None:
    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE"), _new(2), _method(2, "ALLOCATE")]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation evidence is invalid$"):
        parser.feed(_new(3))

    parser = _parser()
    for record in [*_startup(), _new(1)]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation evidence is invalid$"):
        parser.feed(_new(1))


def test_usage_and_peer_usage_must_pair_per_sid_in_order() -> None:
    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE")]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation record order is invalid$"):
        parser.feed(_usage(1, (1, 1, 1, 1), peer=True))

    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE"), _usage(1, (1, 1, 1, 1))]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn allocation record order is invalid$"):
        parser.feed(_refresh(1, 0))


def test_uint64_fields_and_delta_sums_are_checked() -> None:
    maximum = (1 << 64) - 1
    parser = _parser()
    for record in [
        *_startup(),
        _new(1),
        _method(1, "ALLOCATE"),
        _usage(1, (0, maximum, 0, 0)),
        _usage(1, (1, 1, 1, 1), peer=True),
        _usage(1, (0, 1, 0, 0)),
    ]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn traffic evidence overflowed$"):
        parser.feed(_usage(1, (1, 1, 1, 1), peer=True))
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn evidence parser is unavailable$"):
        parser.feed(b"")

    oversized = str(1 << 64)
    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE")]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn numeric evidence is invalid$"):
        parser.feed(
            _log(
                f"session {_sid(1)}: usage: realm=<{REALM}>, username=<{USERNAME}>, "
                f"rp=0, rb={oversized}, sp=0, sb=0"
            )
        )
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn evidence parser is unavailable$"):
        parser.finish()


def test_bidirectional_peer_bytes_must_be_positive_on_the_same_sid() -> None:
    records = [*_startup(), _new(1), _method(1, "ALLOCATE"), _new(2), _method(2, "ALLOCATE")]
    for value, peer in ((1, (1, 5, 0, 0)), (2, (0, 0, 1, 7))):
        records.extend(
            [
                _refresh(value, 0),
                _method(value, "REFRESH"),
                _usage(value, (1, 1, 1, 1)),
                _usage(value, peer, peer=True),
                _close(value),
                _delete(value),
            ]
        )
    with pytest.raises(
        CoturnEvidenceError,
        match=r"^Coturn bidirectional peer traffic evidence is incomplete$",
    ):
        _parse(records)


def test_wrong_username_and_unknown_expected_user_method_do_not_leak() -> None:
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    wrong = "1780000000:ffffffffffffffff"
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn emitted an unknown allocation record$"
    ) as captured:
        parser.feed(_new(1, username=wrong))
    assert wrong not in str(captured.value)
    assert USERNAME not in str(captured.value)

    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE")]:
        parser.feed(record)
    body = (
        f"session {_sid(1)}: realm <{REALM}> user <{USERNAME}>: incoming packet "
        "REFRESH processed, error 500: secret failure"
    )
    parser.feed(_log(body))
    for record in [
        _refresh(1, 0),
        _method(1, "REFRESH"),
        _usage(1, (1, 1, 1, 1)),
        _usage(1, (1, 2, 1, 3), peer=True),
        _close(1),
        _delete(1),
    ]:
        parser.feed(record)
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn grammar evidence is unverified$"
    ) as captured:
        parser.finish()
    assert "secret failure" not in str(captured.value)
    assert USERNAME not in str(captured.value)


@pytest.mark.parametrize("identity", ["", "has space", "bad<angle>", "snowman-\N{SNOWMAN}"])
def test_expected_identity_validation_is_ascii_bounded_and_redacted(identity: str) -> None:
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn expected username is invalid$"):
        CoturnEvidenceParser(expected_username=identity, expected_topology=TOPOLOGY)


def test_expected_realm_is_bound_to_the_source_pinned_configuration() -> None:
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn expected realm is invalid$"):
        CoturnEvidenceParser(
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
            expected_realm="attacker.invalid",
        )


def test_evidence_is_factory_owned_and_public_wrapper_redacts_iterator_failures() -> None:
    with pytest.raises(TypeError, match=r"^Coturn evidence is factory-owned$"):
        CoturnEvidence(  # type: ignore[call-arg]
            allocation_count=1,
            traffic=None,  # type: ignore[arg-type]
            observed_categories=frozenset(),
            unknown_info_records=0,
            total_records=0,
        )

    secret = f"iterator-{USERNAME}"

    def broken() -> object:
        raise ValueError(secret)
        yield b""  # pragma: no cover

    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn log stream is unavailable$"
    ) as captured:
        parse_coturn_evidence(broken(), expected_username=USERNAME, expected_topology=TOPOLOGY)  # type: ignore[arg-type]
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert captured.value.__context__ is None

    def public_error_mimic() -> object:
        raise CoturnEvidenceError(secret)
        yield b""  # pragma: no cover

    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn log stream is unavailable$"
    ) as captured:
        parse_coturn_evidence(
            public_error_mimic(),
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
        )
    assert secret not in str(captured.value)
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("prefix", "replacement"),
    [
        (b"Listener address to use:", b"Listener address to use: 172.30.0.3"),
        (b"Relay address to use:", b"Relay address to use: 172.30.0.3"),
        (
            b"Whitelisting external-ip private part:",
            b"Whitelisting external-ip private part: 172.30.0.3",
        ),
        (b"  relay 172.30.0.2 initialization...", b"  relay 172.30.0.3 initialization..."),
        (
            b"  relay 172.30.0.2 initialization done",
            b"  relay 172.30.0.3 initialization done",
        ),
        (
            b"IPv4. TLS listener opened on :",
            b"IPv4. TLS listener opened on : 172.30.0.3:5349",
        ),
    ],
)
def test_every_topology_bearing_startup_record_binds_to_container(
    prefix: bytes,
    replacement: bytes,
) -> None:
    records = _replace_startup(_startup(), prefix, replacement)
    with pytest.raises(CoturnEvidenceError, match=r"^Coturn startup topology is invalid$"):
        _parse([*records, *_complete_allocation(1)])


def test_critical_startup_order_and_single_adjacent_duplicate_are_exact() -> None:
    startup = _startup()
    assert _parse([startup[0], *startup, *_complete_allocation(1)]).allocation_count == 1

    with pytest.raises(CoturnEvidenceError, match=r"cardinality is invalid$"):
        _parse([startup[0], startup[0], *startup, *_complete_allocation(1)])

    with pytest.raises(CoturnEvidenceError, match=r"cardinality is invalid$"):
        _parse([startup[0], startup[3], *startup, *_complete_allocation(1)])

    reordered = [startup[1], startup[0], *startup[2:]]
    with pytest.raises(CoturnEvidenceError, match=r"order is invalid$"):
        _parse([*reordered, *_complete_allocation(1)])


def test_non_epoll_startup_is_never_qualified_but_probe_is_sanitized() -> None:
    records = _replace_startup(_startup(), b"IO method:", b"IO method: poll")
    payload = [*records, *_complete_allocation(1)]
    with pytest.raises(CoturnEvidenceError):
        _parse(payload)
    summary = parse_coturn_probe(
        payload,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    assert not summary
    assert summary.grammar_verified is False
    assert summary.unknown_info_records >= 1
    assert summary.grammar_violation_records >= 1


@pytest.mark.parametrize("method", ["TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"])
def test_source_pinned_tls_method_allowlist_qualifies(method: str) -> None:
    assert _parse([*_startup(), *_complete_allocation(1, transport_method=method)])


@pytest.mark.parametrize("method", ["TLS/TCP", "DTLSv1.2", "SSLv3", "unknown"])
def test_non_source_transport_methods_cannot_qualify(method: str) -> None:
    records = [*_startup(), *_complete_allocation(1, transport_method=method)]
    with pytest.raises(CoturnEvidenceError):
        _parse(records)
    summary = parse_coturn_probe(
        records,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    assert not summary
    assert summary.unknown_info_records >= 1


@pytest.mark.parametrize("lifetime", [600, 3600])
def test_positive_source_lifetime_bounds_are_inclusive(lifetime: int) -> None:
    allocation = _complete_allocation(1)
    allocation[0] = _new(1, lifetime=lifetime)
    allocation[6] = _refresh(1, lifetime)
    assert _parse([*_startup(), *allocation]).allocation_count == 1


@pytest.mark.parametrize("lifetime", [0, 599, 3601, (1 << 64) - 1])
def test_new_allocation_lifetime_outside_source_bounds_is_terminal(lifetime: int) -> None:
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"allocation evidence is invalid$"):
        parser.feed(_new(1, lifetime=lifetime))
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.feed(b"")


@pytest.mark.parametrize("lifetime", [1, 599, 3601, (1 << 64) - 1])
def test_positive_refresh_lifetime_outside_source_bounds_is_terminal(lifetime: int) -> None:
    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE")]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"allocation lifetime is invalid$"):
        parser.feed(_refresh(1, lifetime))
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.finish()


def test_stale_nonce_policy_allows_two_per_sid_and_rejects_the_third() -> None:
    stale = _log(
        f"session {_sid(1)}: realm <{REALM}> user <{USERNAME}>: incoming packet "
        "message processed, error 438: Stale Nonce"
    )
    tail = [
        _refresh(1, 0),
        _method(1, "REFRESH"),
        _usage(1, (1, 1, 1, 1)),
        _usage(1, (1, 2, 1, 3), peer=True),
        _close(1),
        _delete(1),
    ]
    assert _parse([*_startup(), _new(1), _method(1, "ALLOCATE"), stale, stale, *tail])

    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE"), stale, stale]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"challenge bound is invalid$"):
        parser.feed(stale)


def test_stale_nonce_policy_accepts_at_most_four_across_two_allocations() -> None:
    stale = {
        value: _log(
            f"session {_sid(value)}: realm <{REALM}> user <{USERNAME}>: incoming packet "
            "message processed, error 438: Stale Nonce"
        )
        for value in (1, 2)
    }
    records = [*_startup(), _new(1), _method(1, "ALLOCATE"), _new(2), _method(2, "ALLOCATE")]
    records.extend([stale[1], stale[1], stale[2], stale[2]])
    for value in (1, 2):
        records.extend(
            [
                _refresh(value, 0),
                _method(value, "REFRESH"),
                _usage(value, (1, 1, 1, 1)),
                _usage(value, (1, 2, 1, 3), peer=True),
                _close(value),
                _delete(value),
            ]
        )
    assert _parse(records).allocation_count == 2


def test_multiple_source_permitted_release_usage_pairs_are_summed() -> None:
    allocation = _complete_allocation(1)
    allocation[-2:-2] = [
        _usage(1, (1, 2, 3, 4)),
        _usage(1, (5, 6, 7, 8), peer=True),
    ]
    evidence = _parse([*_startup(), *allocation])
    assert evidence.traffic.received_bytes == 62
    assert evidence.traffic.peer_received_bytes == 106


def test_noncanonical_sid_and_u64_records_are_terminal() -> None:
    canonical = _sid(1).encode("ascii")
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"session identifier is invalid$"):
        parser.feed(_new(1).replace(canonical, b"0" + canonical))
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.feed(b"")

    parser = _parser()
    for record in [*_startup(), _new(1), _method(1, "ALLOCATE")]:
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"numeric evidence is invalid$"):
        parser.feed(_usage(1, (1, 1, 1, 1)).replace(b"rp=1", b"rp=01"))

    oversized_sid = str(1 << 64).encode("ascii")
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"session identifier is invalid$"):
        parser.feed(_new(1).replace(canonical, oversized_sid))

    maximum_sid = (1 << 64) - 1
    assert _parse([*_startup(), *_complete_allocation(maximum_sid)]).allocation_count == 1


def test_readiness_ids_counters_challenges_and_ports_are_bounded() -> None:
    oversized = 1 << 64
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"numeric evidence is invalid$"):
        parser.feed(
            _log(
                f"session {_sid(90)}: usage: realm=<{REALM}>, username=<>, "
                f"rp=0, rb={oversized}, sp=0, sb=0"
            )
        )

    parser = _parser()
    for record in _startup():
        parser.feed(record)
    challenge = (
        "session {sid}: realm <"
        f"{REALM}> user <>: incoming packet message processed, error 401: Unauthorized"
    )
    for value in (90, 91, 92):
        parser.feed(_log(challenge.format(sid=_sid(value))))
    with pytest.raises(CoturnEvidenceError, match=r"session bound is invalid$"):
        parser.feed(_log(challenge.format(sid=_sid(93))))

    parser = _parser()
    for record in _startup():
        parser.feed(record)
    duplicate = _log(challenge.format(sid=_sid(90)))
    parser.feed(duplicate)
    with pytest.raises(CoturnEvidenceError, match=r"record order is invalid$"):
        parser.feed(duplicate)

    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"endpoint port is invalid$"):
        parser.feed(_log("IPv4. tcp or tls connected to: 172.30.0.1:99999"))


def test_readiness_stale_nonce_requires_an_initial_unauthorized_challenge() -> None:
    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"record order is invalid$"):
        parser.feed(
            _log(
                f"session {_sid(90)}: realm <{REALM}> user <>: incoming packet "
                "message processed, error 438: Stale Nonce"
            )
        )

    challenge_head = f"session {_sid(90)}: realm <{REALM}> user <>: incoming packet message "
    evidence = _parse(
        [
            *_startup(),
            _log(challenge_head + "processed, error 401: Unauthorized"),
            _log(challenge_head + "processed, error 438: Stale Nonce"),
            *_complete_allocation(1),
        ]
    )
    assert CoturnLogCategory.AUTH_CHALLENGE in evidence.observed_categories


def test_allocation_close_endpoints_bind_to_owned_container_and_gateway() -> None:
    records = [*_startup(), *_complete_allocation(1)]
    records[-2] = records[-2].replace(b"local 172.30.0.2", b"local 172.30.0.3")
    with pytest.raises(CoturnEvidenceError, match=r"allocation topology is invalid$"):
        _parse(records)


def _value_contains_any(
    value: object,
    needles: tuple[bytes, ...],
    seen: set[int],
) -> bool:
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    if isinstance(value, str):
        encoded = value.encode("ascii", errors="backslashreplace")
        return any(needle in encoded for needle in needles)
    if isinstance(value, (bytes, bytearray)):
        return any(needle in value for needle in needles)
    if isinstance(value, memoryview):
        try:
            encoded = value.tobytes()
        except ValueError:
            return False
        return any(needle in encoded for needle in needles)
    if isinstance(value, re.Match):
        return _value_contains_any(value.string, needles, seen)
    if isinstance(value, BaseException):
        return any(
            _value_contains_any(candidate, needles, seen)
            for candidate in (
                value.args,
                getattr(value, "code", None),
                value.__dict__,
                value.__cause__,
                value.__context__,
            )
        )
    if isinstance(value, dict):
        return any(
            _value_contains_any(candidate, needles, seen)
            for pair in value.items()
            for candidate in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_value_contains_any(candidate, needles, seen) for candidate in value)
    if type(value).__module__.startswith("scripts.voice_pipecat_e2e_coturn"):
        attributes: list[object] = []
        namespace = getattr(value, "__dict__", None)
        if namespace is not None:
            attributes.append(namespace)
        slots = getattr(type(value), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        attributes.extend(
            getattr(value, slot) for slot in slots if isinstance(slot, str) and hasattr(value, slot)
        )
        return any(_value_contains_any(candidate, needles, seen) for candidate in attributes)
    return False


def _traceback_contains_any(exc: BaseException, *secrets: str | bytes) -> bool:
    needles = tuple(
        secret if isinstance(secret, bytes) else secret.encode("ascii") for secret in secrets
    )
    frame = exc.__traceback__
    while frame is not None:
        code = frame.tb_frame.f_code
        is_calling_test = code.co_filename == __file__ and code.co_name.startswith("test_")
        if not is_calling_test:
            for value in list(frame.tb_frame.f_locals.values()):
                if _value_contains_any(value, needles, set()):
                    return True
        frame = frame.tb_next
    return False


def _traceback_contains(exc: BaseException, secret: str) -> bool:
    return _traceback_contains_any(exc, secret)


def _traceback_frame_names(exc: BaseException) -> set[str]:
    names: set[str] = set()
    frame = exc.__traceback__
    while frame is not None:
        names.add(frame.tb_frame.f_code.co_name)
        frame = frame.tb_next
    return names


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("seam", ["record", "state"])
def test_control_flow_exceptions_propagate_after_full_traceback_scrub(
    monkeypatch: pytest.MonkeyPatch,
    signal: type[BaseException],
    seam: str,
) -> None:
    raw_record = _log(f"interrupt-{USERNAME}")
    parser = _parser()
    retained_state = parser._state
    assert retained_state is not None

    def interrupt(*_args: object, **_kwargs: object) -> None:
        try:
            raise ValueError(raw_record)
        except ValueError as cause:
            raise signal(raw_record) from cause

    if seam == "record":
        monkeypatch.setattr(coturn_evidence_module, "split_coturn_record", interrupt)
    else:
        monkeypatch.setattr(coturn_state, "match_coturn_startup_info", interrupt)

    with pytest.raises(signal) as captured:
        parser.feed(raw_record)

    assert type(captured.value) is signal
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert USERNAME not in str(captured.value)
    assert not _traceback_contains_any(captured.value, raw_record, USERNAME)
    frame_names = _traceback_frame_names(captured.value)
    assert {"feed", "_feed_chunk", "_process_buffered_line", "interrupt"} <= frame_names
    if seam == "state":
        assert {"consume", "_consume_known"} <= frame_names
    assert parser._state is None
    assert parser._line == bytearray()
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()
    if signal is SystemExit:
        assert captured.value.code == 1  # type: ignore[attr-defined]
    else:
        assert captured.value.args == ()
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.feed(b"")


def test_control_flow_scrub_covers_public_wrapper_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_record = _log(f"wrapper-interrupt-{USERNAME}")

    def interrupt(*_args: object, **_kwargs: object) -> None:
        try:
            raise ValueError(raw_record)
        except ValueError as cause:
            raise KeyboardInterrupt(raw_record) from cause

    monkeypatch.setattr(coturn_state, "match_coturn_startup_info", interrupt)
    with pytest.raises(KeyboardInterrupt) as captured:
        parse_coturn_evidence(
            [raw_record],
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
        )

    assert captured.value.args == ()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains_any(captured.value, raw_record, USERNAME)
    assert {
        "parse_coturn_evidence",
        "_parse_public",
        "_consume",
        "feed",
        "_feed_chunk",
        "_process_buffered_line",
        "consume",
        "_consume_known",
        "interrupt",
    } <= _traceback_frame_names(captured.value)


def test_ordinary_classifier_exception_keeps_fixed_public_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_record = _log(f"ordinary-failure-{USERNAME}")
    parser = _parser()
    retained_state = parser._state
    assert retained_state is not None

    def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError(raw_record)

    monkeypatch.setattr(coturn_evidence_module, "split_coturn_record", fail)
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn log stream is unavailable$"
    ) as captured:
        parser.feed(raw_record)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains_any(captured.value, raw_record, USERNAME)
    assert parser._state is None
    assert parser._line == bytearray()
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()


def test_terminal_paths_scrub_identity_regex_cache_context_and_traceback_locals() -> None:
    parser = _parser()
    completed_state = parser._state
    assert completed_state is not None
    for record in [*_startup(), *_complete_allocation(1)]:
        parser.feed(record)
    parser.finish()
    assert parser._state is None
    assert parser._line == bytearray()
    assert completed_state._expected_username == bytearray()
    assert completed_state._expected_realm == bytearray()
    assert not any(
        isinstance(part, bytes) and USERNAME.encode("ascii") in part
        for key in re._cache  # type: ignore[attr-defined]
        for part in key
    )

    secret = f"unsafe-{USERNAME}"
    parser = _parser()
    failed_state = parser._state
    assert failed_state is not None
    with pytest.raises(CoturnEvidenceError) as captured:
        parser.feed(_log(secret, level="ERROR"))
    assert parser._state is None
    assert parser._line == bytearray()
    assert failed_state._expected_username == bytearray()
    assert failed_state._expected_realm == bytearray()
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)


def test_unexpected_constructor_failure_is_context_free_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = f"constructor-{USERNAME}"

    def fail_identity(*_args: object, **_kwargs: object) -> bytearray:
        raise ValueError(secret)

    monkeypatch.setattr(coturn_state, "_validated_identity", fail_identity)
    with pytest.raises(
        CoturnEvidenceError, match=r"^Coturn evidence state is unavailable$"
    ) as captured:
        CoturnEvidenceParser(expected_username=USERNAME, expected_topology=TOPOLOGY)
    assert captured.value.__context__ is None
    assert secret not in str(captured.value)
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)


def test_probe_and_qualified_terminal_surfaces_cannot_be_confused() -> None:
    with pytest.raises(TypeError, match=r"probe summary is factory-owned$"):
        CoturnProbeSummary(
            grammar_verified=False,
            allocation_count=0,
            observed_categories=frozenset(),
            unknown_info_records=0,
            grammar_violation_records=0,
            total_records=0,
        )

    parser = _parser(probe=True)
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.finish()

    parser = _parser()
    for record in _startup():
        parser.feed(record)
    with pytest.raises(CoturnEvidenceError, match=r"parser is unavailable$"):
        parser.finish_probe()


def test_adjacent_startup_duplicate_retains_only_digest_not_raw_body() -> None:
    startup = _startup()
    parser = _parser()
    parser.feed(startup[0])
    parser.feed(startup[0])
    assert parser._state is not None
    assert parser._state._startup_digests
    assert all(len(value) == 32 for value in parser._state._startup_digests)
    assert all(b"Listener address" not in value for value in parser._state._startup_digests)


def test_probe_grammar_failures_roll_back_partial_state_before_discovery_continues() -> None:
    parser = _parser(probe=True)
    parser.feed(_log("Listener address to use: 172.30.0.3"))
    assert parser._state is not None
    assert parser._state._startup_digests == set()
    assert parser._state._critical_startup_index == 0

    for record in _startup():
        parser.feed(record)
    parser.feed(
        _log(
            f"session {_sid(90)}: realm <{REALM}> user <>: incoming packet "
            "message processed, error 438: Stale Nonce"
        )
    )
    assert parser._state._readiness == {}
    summary = parser.finish_probe()
    assert summary.grammar_violation_records == 2


def test_probe_result_slot_is_factory_owned_and_idempotently_retains_summary() -> None:
    secret = f"probe-slot-constructor-{USERNAME}"
    with pytest.raises(TypeError, match=r"probe result slot is factory-owned$") as error:
        CoturnProbeResultSlot(secret)
    assert not _traceback_contains(error.value, secret)
    assert not _traceback_contains(error.value, USERNAME)

    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    parser.finish_probe_into()
    first = coturn_probe_summary_from_slot(slot)
    parser.finish_probe_into()
    second = coturn_probe_summary_from_slot(slot)

    assert slot.ready is True
    assert first is second
    assert first.grammar_verified is False
    assert repr(slot) == "CoturnProbeResultSlot()"


@pytest.mark.parametrize(
    ("phase", "error_type", "code"),
    [
        ("validated", SystemExit, 19),
        ("summary-created", SystemExit, 23),
        ("summary-published", KeyboardInterrupt, None),
        ("parser-terminal", SystemExit, 37),
        ("finalizer-released", KeyboardInterrupt, None),
    ],
)
def test_probe_result_slot_survives_finalization_control_after_each_publication_cut(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error_type: type[KeyboardInterrupt] | type[SystemExit],
    code: int | None,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    raised = False

    def interrupt(current: str) -> None:
        nonlocal raised
        if current != phase or raised:
            return
        raised = True
        if error_type is KeyboardInterrupt:
            raise KeyboardInterrupt()
        raise SystemExit(code)

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", interrupt)
    with pytest.raises(error_type) as captured:
        parser.finish_probe_into()
    if error_type is SystemExit:
        assert captured.value.code == code
    assert raised is True
    assert slot.ready is True
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False
    parser.finish_probe_into()


def test_probe_result_slot_preserves_first_of_repeated_finalization_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    controls = {
        "summary-created": 23,
        "summary-published": 24,
        "parser-terminal": None,
    }

    def interrupt(phase: str) -> None:
        if phase not in controls:
            return
        code = controls.pop(phase)
        if code is None:
            raise KeyboardInterrupt()
        raise SystemExit(code)

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        parser.finish_probe_into()
    assert captured.value.code == 23
    assert controls == {}
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_result_slot_rejects_second_parser_without_replacing_summary() -> None:
    slot = new_coturn_probe_result_slot()
    first_parser = _parser(probe=True, result_slot=slot)
    with pytest.raises(CoturnEvidenceError, match=r"result slot is invalid$"):
        _parser(probe=True, result_slot=slot)
    for record in _startup():
        first_parser.feed(record)
    first_parser.finish_probe_into()
    first = coturn_probe_summary_from_slot(slot)

    first_parser.finish_probe_into()
    assert coturn_probe_summary_from_slot(slot) is first


def test_probe_result_read_control_does_not_consume_published_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    parser.finish_probe_into()
    raised = False
    secret = f"probe-result-read-{USERNAME}"

    def interrupt(phase: str) -> None:
        nonlocal raised
        if phase == "summary-read" and not raised:
            raised = True
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise SystemExit(secret) from cause

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        coturn_probe_summary_from_slot(slot)
    assert captured.value.code == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_result_read_ordinary_failure_is_fixed_and_non_consuming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    parser.finish_probe_into()
    secret = f"probe-result-read-failure-{USERNAME}"

    def fail(phase: str) -> None:
        if phase == "summary-read":
            raise RuntimeError(secret)

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", fail)
    with pytest.raises(CoturnEvidenceError, match=r"probe result read failed$") as captured:
        coturn_probe_summary_from_slot(slot)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    monkeypatch.setattr(
        coturn_evidence_result_module,
        "_probe_result_boundary_hook",
        lambda _phase: None,
    )
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_staggered_concurrent_probe_finalizers_are_serialized_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    first_validated = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    failures: list[BaseException] = []

    def pause_first(phase: str) -> None:
        if phase == "validated" and threading.current_thread().name == "probe-first":
            first_validated.set()
            if not release_first.wait(timeout=2):
                raise RuntimeError("probe finalization test timed out")

    def finish(*, second: bool) -> None:
        if second:
            second_started.set()
        try:
            parser.finish_probe_into()
        except BaseException as error:
            failures.append(error)
        finally:
            if second:
                second_finished.set()

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", pause_first)
    first = threading.Thread(target=finish, kwargs={"second": False}, name="probe-first")
    second = threading.Thread(target=finish, kwargs={"second": True}, name="probe-second")
    first.start()
    assert first_validated.wait(timeout=1)
    second.start()
    assert second_started.wait(timeout=1)
    assert not second_finished.wait(timeout=0.05)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert parser._finished is True
    assert parser._failed is False
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_finalizer_claim_reconciles_control_after_slot_ownership_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    original = CoturnProbeResultSlot._claim_finish
    raised = False

    def interrupt_after_claim(self: CoturnProbeResultSlot, owner: object, operation: object) -> str:
        nonlocal raised
        result = original(self, owner, operation)
        if not raised:
            raised = True
            raise SystemExit(23)
        return result

    monkeypatch.setattr(CoturnProbeResultSlot, "_claim_finish", interrupt_after_claim)
    with pytest.raises(SystemExit) as captured:
        parser.finish_probe_into()
    assert captured.value.code == 23
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert slot._finisher is None
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_finalizer_release_reconciles_nested_control_and_preserves_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    secret = f"probe-finalizer-release-{USERNAME}"
    first_raised = False
    release_raised = False
    original = CoturnProbeResultSlot._release_finish

    def interrupt_summary(phase: str) -> None:
        nonlocal first_raised
        if phase == "summary-created" and not first_raised:
            first_raised = True
            raise SystemExit(23)

    def interrupt_after_release(
        self: CoturnProbeResultSlot,
        owner: object,
        operation: object,
    ) -> bool:
        nonlocal release_raised
        result = original(self, owner, operation)
        if not release_raised:
            release_raised = True
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise SystemExit(secret) from cause
        return result

    monkeypatch.setattr(
        coturn_evidence_result_module,
        "_probe_result_boundary_hook",
        interrupt_summary,
    )
    monkeypatch.setattr(CoturnProbeResultSlot, "_release_finish", interrupt_after_release)
    with pytest.raises(SystemExit) as captured:
        parser.finish_probe_into()
    assert captured.value.code == 23
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    assert slot._finisher is None
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_nonowning_finalizer_claim_failure_does_not_terminalize_active_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    first_validated = threading.Event()
    release_first = threading.Event()
    failures: list[BaseException] = []
    raised = False
    original = CoturnProbeResultSlot._claim_finish

    def pause_first(phase: str) -> None:
        if phase == "validated" and threading.current_thread().name == "probe-owner":
            first_validated.set()
            if not release_first.wait(timeout=2):
                raise RuntimeError("probe owner test timed out")

    def fail_waiter_claim(
        self: CoturnProbeResultSlot,
        owner: object,
        operation: object,
    ) -> str:
        nonlocal raised
        if threading.current_thread().name == "probe-waiter" and not raised:
            raised = True
            raise RuntimeError("synthetic waiter failure")
        return original(self, owner, operation)

    def finish() -> None:
        try:
            parser.finish_probe_into()
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(coturn_evidence_result_module, "_probe_result_boundary_hook", pause_first)
    monkeypatch.setattr(CoturnProbeResultSlot, "_claim_finish", fail_waiter_claim)
    owner = threading.Thread(target=finish, name="probe-owner")
    waiter = threading.Thread(target=finish, name="probe-waiter")
    owner.start()
    assert first_validated.wait(timeout=1)
    waiter.start()
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert len(failures) == 1
    assert type(failures[0]) is CoturnEvidenceError
    assert parser._state is not None
    assert parser._failed is False
    release_first.set()
    owner.join(timeout=2)

    assert not owner.is_alive()
    assert len(failures) == 1
    assert parser._finished is True
    assert slot._finisher is None
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_finalization_public_handoff_sanitizes_nested_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    secret = f"probe-finalization-handoff-{USERNAME}"
    raised = False
    original = CoturnEvidenceParser._finish_probe_into_owned

    def interrupt_after_return(
        self: CoturnEvidenceParser,
        operation: object,
        control: object,
    ) -> object:
        nonlocal raised
        result = original(self, operation, control)  # type: ignore[arg-type]
        if not raised:
            raised = True
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise SystemExit(secret) from cause
        return result

    monkeypatch.setattr(CoturnEvidenceParser, "_finish_probe_into_owned", interrupt_after_return)
    with pytest.raises(SystemExit) as captured:
        parser.finish_probe_into()
    assert captured.value.code == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    assert slot._finisher is None
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_finalization_public_handoff_latches_fixed_ordinary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    secret = f"probe-finalization-ordinary-{USERNAME}"
    raised = False
    original = CoturnEvidenceParser._finish_probe_into_owned

    def fail_after_return(
        self: CoturnEvidenceParser,
        operation: object,
        control: object,
    ) -> object:
        nonlocal raised
        result = original(self, operation, control)  # type: ignore[arg-type]
        if not raised:
            raised = True
            raise RuntimeError(secret)
        return result

    monkeypatch.setattr(CoturnEvidenceParser, "_finish_probe_into_owned", fail_after_return)
    with pytest.raises(CoturnEvidenceError, match=r"evidence finalization failed$") as captured:
        parser.finish_probe_into()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    assert slot._finisher is None
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_probe_result_read_public_handoff_sanitizes_nested_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = new_coturn_probe_result_slot()
    parser = _parser(probe=True, result_slot=slot)
    for record in _startup():
        parser.feed(record)
    parser.finish_probe_into()
    secret = f"probe-read-handoff-{USERNAME}"
    raised = False
    original = coturn_evidence_result_module._read_probe_result_slot

    def interrupt_after_return(*args: object, **kwargs: object) -> object:
        nonlocal raised
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if not raised:
            raised = True
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise SystemExit(secret) from cause
        return result

    monkeypatch.setattr(
        coturn_evidence_result_module,
        "_read_probe_result_slot",
        interrupt_after_return,
    )
    with pytest.raises(SystemExit) as captured:
        coturn_probe_summary_from_slot(slot)
    assert captured.value.code == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_contains(captured.value, secret)
    assert not _traceback_contains(captured.value, USERNAME)
    assert coturn_probe_summary_from_slot(slot).grammar_verified is False


def test_empty_probe_result_slot_is_nonqualifying_and_unavailable() -> None:
    slot = new_coturn_probe_result_slot()
    assert slot.ready is False
    assert not bool(slot.ready)
    with pytest.raises(CoturnEvidenceError, match=r"probe result is unavailable$"):
        coturn_probe_summary_from_slot(slot)


def test_probe_result_slot_binding_rejects_hostile_input_without_reflection() -> None:
    secret = f"probe-slot-{USERNAME}"
    with pytest.raises(CoturnEvidenceError, match=r"probe result slot is invalid$") as error:
        CoturnEvidenceParser.for_probe(
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
            result_slot=secret,
        )
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not _traceback_contains(error.value, secret)
    assert not _traceback_contains(error.value, USERNAME)

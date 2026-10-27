"""Secret-owning state machine for source-pinned Coturn log evidence.

The public streaming layer owns raw chunks and record framing. This module owns
the shortest-lived copy of the expected TURN identity, exact topology binding,
and transactional startup/allocation/readiness state. It retains no raw record.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from scripts.voice_pipecat_e2e_coturn import COTURN_TLS_PORT, CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_log_grammar import (
    COTURN_CRITICAL_STARTUP_ORDER,
    COTURN_LIFECYCLE_PATTERNS,
    COTURN_REALM,
    COTURN_REQUIRED_STARTUP,
    CoturnLogCategory,
    CoturnStartupRecord,
    match_coturn_startup_info,
)

_MAX_UNKNOWN_INFO: Final = 64
_MAX_ALLOCATIONS: Final = 2
_MAX_READINESS_SESSIONS: Final = _MAX_ALLOCATIONS + 1
_MAX_READINESS_CHALLENGES_PER_SID: Final = 2
# Qualification policy for a bounded <=120 second call. Coturn itself does not
# impose a finite stale-nonce challenge count.
_MAX_STALE_CHALLENGES_PER_ALLOCATION: Final = 2
_MAX_STALE_CHALLENGES_PER_CALL: Final = 4
_MIN_POSITIVE_LIFETIME: Final = 600
_MAX_POSITIVE_LIFETIME: Final = 3600
_UINT64_MAX: Final = (1 << 64) - 1
_TOPOLOGY_STARTUP: Final = frozenset(
    {
        CoturnLogCategory.START_LISTENER_ADDRESS,
        CoturnLogCategory.START_RELAY_ADDRESS,
        CoturnLogCategory.START_EXTERNAL_MAPPING,
        CoturnLogCategory.START_RELAY_PORT_BEGIN,
        CoturnLogCategory.START_RELAY_PORT_DONE,
        CoturnLogCategory.START_TLS_LISTENER,
    }
)


class CoturnEvidenceStateError(RuntimeError):
    """Fixed-message state error; raw input and identities are never attached."""

    def __repr__(self) -> str:
        return "CoturnEvidenceStateError()"


class _GrammarViolation(CoturnEvidenceStateError):
    pass


@dataclass(frozen=True)
class CoturnStateEvidence:
    allocation_count: int
    traffic: tuple[int, int, int, int, int, int, int, int]
    observed_categories: frozenset[CoturnLogCategory]
    unknown_info_records: int


@dataclass(frozen=True)
class CoturnStateProbe:
    allocation_count: int
    observed_categories: frozenset[CoturnLogCategory]
    unknown_info_records: int
    grammar_violation_records: int


class _Phase(Enum):
    NEW = "new"
    ACTIVE = "active"
    REFRESH_PENDING = "refresh_pending"
    RELEASED = "released"
    CLOSED = "closed"
    DELETED = "deleted"


@dataclass
class _Allocation:
    phase: _Phase = _Phase.NEW
    pending_refresh_zero: bool | None = None
    pending_usage: tuple[int, int, int, int] | None = None
    release_usage_pairs: int = 0
    stale_nonce_challenges: int = 0
    traffic: list[int] = field(default_factory=lambda: [0] * 8)


@dataclass
class _ReadinessSession:
    challenge_codes: set[bytes] = field(default_factory=set)
    usage_records: int = 0
    peer_usage_records: int = 0
    transport_close_records: int = 0
    close_records: int = 0


@dataclass
class _StateSnapshot:
    observed: set[CoturnLogCategory]
    allocations: dict[int, _Allocation]
    readiness: dict[int, _ReadinessSession]
    startup_digests: set[bytes]
    last_startup_digest: bytes | None
    critical_startup_index: int
    adjacent_duplicate_used: bool
    lifecycle_started: bool
    stale_challenges: int
    unknown_info: int
    grammar_violations: int


class CoturnEvidenceState:
    """Validate one topology- and identity-bound Coturn evidence stream."""

    __slots__ = (
        "_adjacent_duplicate_used",
        "_allocations",
        "_critical_startup_index",
        "_expected_container",
        "_expected_gateway",
        "_expected_realm",
        "_expected_username",
        "_grammar_violations",
        "_last_startup_digest",
        "_lifecycle_started",
        "_observed",
        "_probe_only",
        "_readiness",
        "_stale_challenges",
        "_startup_digests",
        "_unknown_info",
    )

    def __init__(
        self,
        *,
        expected_username: object,
        expected_topology: object,
        expected_realm: object = COTURN_REALM,
        probe_only: bool = False,
    ) -> None:
        self._expected_username = bytearray()
        self._expected_realm = bytearray()
        self._expected_container = bytearray()
        self._expected_gateway = bytearray()
        self._observed: set[CoturnLogCategory] = set()
        self._allocations: dict[int, _Allocation] = {}
        self._readiness: dict[int, _ReadinessSession] = {}
        self._startup_digests: set[bytes] = set()
        self._last_startup_digest: bytes | None = None
        self._critical_startup_index = 0
        self._adjacent_duplicate_used = False
        self._lifecycle_started = False
        self._stale_challenges = 0
        self._unknown_info = 0
        self._grammar_violations = 0
        self._probe_only = probe_only
        failure: str | None = None
        try:
            if type(expected_topology) is not CoturnBridgeTopology:
                raise CoturnEvidenceStateError("Coturn expected topology is invalid")
            self._expected_username = _validated_identity(
                expected_username, maximum=512, label="username"
            )
            self._expected_realm = _validated_identity(expected_realm, maximum=127, label="realm")
            if not _same(COTURN_REALM.encode("ascii"), self._expected_realm):
                raise CoturnEvidenceStateError("Coturn expected realm is invalid")
            self._expected_container = bytearray(str(expected_topology.container), "ascii")
            self._expected_gateway = bytearray(str(expected_topology.gateway), "ascii")
        except CoturnEvidenceStateError as error:
            failure = str(error)
        except Exception:
            failure = "Coturn evidence state is unavailable"
        except BaseException:
            self.clear()
            expected_username = None
            expected_topology = None
            expected_realm = None
            raise
        if failure is not None:
            self.clear()
            expected_username = None
            expected_topology = None
            expected_realm = None
            raise CoturnEvidenceStateError(failure) from None

    def consume(self, body: bytes) -> None:
        """Consume one prefix-validated INFO body without retaining it."""

        snapshot = self._snapshot()
        try:
            self._consume_known(body)
        except _GrammarViolation:
            self._restore(snapshot)
            if not self._probe_only:
                raise
            if self._grammar_violations >= _MAX_UNKNOWN_INFO:
                raise CoturnEvidenceStateError(
                    "Coturn emitted too many grammar violations"
                ) from None
            self._grammar_violations += 1
        except BaseException:
            self._restore(snapshot)
            raise
        finally:
            body = b""

    def finish_evidence(self) -> CoturnStateEvidence:
        if self._probe_only:
            raise CoturnEvidenceStateError("Coturn evidence state is unavailable")
        if self._unknown_info or self._grammar_violations:
            raise CoturnEvidenceStateError("Coturn grammar evidence is unverified")
        self._require_startup_complete()
        if not 1 <= len(self._allocations) <= _MAX_ALLOCATIONS:
            raise CoturnEvidenceStateError("Coturn allocation evidence is incomplete")
        if any(
            value.phase is not _Phase.DELETED
            or value.pending_usage is not None
            or value.release_usage_pairs < 1
            for value in self._allocations.values()
        ):
            raise CoturnEvidenceStateError("Coturn allocation teardown evidence is incomplete")
        if not any(
            value.traffic[5] > 0 and value.traffic[7] > 0 for value in self._allocations.values()
        ):
            raise CoturnEvidenceStateError(
                "Coturn bidirectional peer traffic evidence is incomplete"
            )
        totals = [0] * 8
        for allocation in self._allocations.values():
            totals = [_checked_add(a, b) for a, b in zip(totals, allocation.traffic, strict=True)]
        return CoturnStateEvidence(
            allocation_count=len(self._allocations),
            traffic=tuple(totals),  # type: ignore[arg-type]
            observed_categories=frozenset(self._observed),
            unknown_info_records=self._unknown_info,
        )

    def finish_probe(self) -> CoturnStateProbe:
        if not self._probe_only:
            raise CoturnEvidenceStateError("Coturn evidence state is unavailable")
        return CoturnStateProbe(
            allocation_count=len(self._allocations),
            observed_categories=frozenset(self._observed),
            unknown_info_records=self._unknown_info,
            grammar_violation_records=self._grammar_violations,
        )

    def clear(self) -> None:
        for value in (
            self._expected_username,
            self._expected_realm,
            self._expected_container,
            self._expected_gateway,
        ):
            _wipe(value)
        self._allocations.clear()
        self._readiness.clear()
        self._startup_digests.clear()
        self._observed.clear()
        self._last_startup_digest = None

    def _snapshot(self) -> _StateSnapshot:
        return _StateSnapshot(
            observed=set(self._observed),
            allocations={key: _copy_allocation(value) for key, value in self._allocations.items()},
            readiness={key: _copy_readiness(value) for key, value in self._readiness.items()},
            startup_digests=set(self._startup_digests),
            last_startup_digest=self._last_startup_digest,
            critical_startup_index=self._critical_startup_index,
            adjacent_duplicate_used=self._adjacent_duplicate_used,
            lifecycle_started=self._lifecycle_started,
            stale_challenges=self._stale_challenges,
            unknown_info=self._unknown_info,
            grammar_violations=self._grammar_violations,
        )

    def _restore(self, snapshot: _StateSnapshot) -> None:
        self._observed = snapshot.observed
        self._allocations = snapshot.allocations
        self._readiness = snapshot.readiness
        self._startup_digests = snapshot.startup_digests
        self._last_startup_digest = snapshot.last_startup_digest
        self._critical_startup_index = snapshot.critical_startup_index
        self._adjacent_duplicate_used = snapshot.adjacent_duplicate_used
        self._lifecycle_started = snapshot.lifecycle_started
        self._stale_challenges = snapshot.stale_challenges
        self._unknown_info = snapshot.unknown_info
        self._grammar_violations = snapshot.grammar_violations

    def __repr__(self) -> str:
        return "CoturnEvidenceState()"

    def _consume_known(self, body: bytes) -> None:
        try:
            startup = match_coturn_startup_info(body)
            if startup is not None:
                if startup.category is CoturnLogCategory.READINESS_ACCEPT:
                    self._readiness_accept(startup)
                else:
                    self._startup(startup, body)
                return
            patterns = COTURN_LIFECYCLE_PATTERNS
            for pattern, handler in (
                (patterns.empty_usage, self._readiness_usage),
                (patterns.empty_peer_usage, self._readiness_peer_usage),
                (patterns.empty_close, self._readiness_close),
                (patterns.transport_close, self._readiness_transport_close),
                (patterns.empty_challenge, self._readiness_challenge),
                (patterns.stale_challenge, self._stale_challenge),
                (patterns.new, self._new_allocation),
                (patterns.allocate_success, self._allocate_success),
                (patterns.method_success, self._method_success),
                (patterns.refreshed, self._refresh_allocation),
                (patterns.refresh_success, self._complete_refresh),
                (patterns.usage, self._begin_usage),
                (patterns.peer_usage, self._complete_usage),
                (patterns.closed, self._close_allocation),
                (patterns.delete, self._delete_allocation),
            ):
                match = pattern.fullmatch(body)
                if match is not None:
                    handler(match)
                    self._lifecycle_started = True
                    self._last_startup_digest = None
                    return
            if body.startswith(b"session "):
                self._lifecycle_started = True
            self._last_startup_digest = None
            if self._unknown_info >= _MAX_UNKNOWN_INFO:
                raise CoturnEvidenceStateError("Coturn emitted too many unknown info records")
            self._unknown_info += 1
            self._observed.add(CoturnLogCategory.UNKNOWN_INFO)
        finally:
            body = b""
            startup = None
            match = None
            handler = None

    def _startup(self, record: CoturnStartupRecord, body: bytes) -> None:
        if self._lifecycle_started:
            raise _GrammarViolation("Coturn startup record order is invalid")
        digest = hashlib.sha256(body).digest()
        if digest in self._startup_digests:
            if not self._adjacent_duplicate_used and self._last_startup_digest == digest:
                self._adjacent_duplicate_used = True
                return
            raise _GrammarViolation("Coturn startup record cardinality is invalid")
        if record.category in _TOPOLOGY_STARTUP and not _same(
            record.ipv4, self._expected_container
        ):
            raise _GrammarViolation("Coturn startup topology is invalid")
        if record.port is not None and _parse_port(record.port) != COTURN_TLS_PORT:
            raise _GrammarViolation("Coturn startup topology is invalid")
        if record.category in COTURN_REQUIRED_STARTUP:
            if (
                self._critical_startup_index >= len(COTURN_CRITICAL_STARTUP_ORDER)
                or COTURN_CRITICAL_STARTUP_ORDER[self._critical_startup_index]
                is not record.category
            ):
                raise _GrammarViolation("Coturn startup record order is invalid")
        self._startup_digests.add(digest)
        self._last_startup_digest = digest
        if record.category in COTURN_REQUIRED_STARTUP:
            self._critical_startup_index += 1
        self._observed.add(record.category)

    def _readiness_accept(self, record: CoturnStartupRecord) -> None:
        self._require_startup_complete()
        if not _same(record.ipv4, self._expected_gateway) or record.port is None:
            raise _GrammarViolation("Coturn readiness topology is invalid")
        _parse_port(record.port)
        self._lifecycle_started = True
        self._last_startup_digest = None
        self._observed.add(CoturnLogCategory.READINESS_ACCEPT)

    def _new_allocation(self, match: re.Match[bytes]) -> None:
        self._require_startup_complete()
        self._require_call_identity(match)
        sid = _parse_sid(match.group("sid"))
        lifetime = _parse_u64(match.group("lifetime"))
        if (
            not _MIN_POSITIVE_LIFETIME <= lifetime <= _MAX_POSITIVE_LIFETIME
            or sid in self._allocations
            or len(self._allocations) >= _MAX_ALLOCATIONS
        ):
            raise _GrammarViolation("Coturn allocation evidence is invalid")
        self._allocations[sid] = _Allocation()
        self._observed.add(CoturnLogCategory.ALLOCATION_NEW)

    def _allocate_success(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if allocation.phase is not _Phase.NEW:
            raise _GrammarViolation("Coturn allocation record order is invalid")
        allocation.phase = _Phase.ACTIVE
        self._observed.add(CoturnLogCategory.ALLOCATION_SUCCESS)

    def _method_success(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if allocation.phase is not _Phase.ACTIVE or allocation.pending_usage is not None:
            raise _GrammarViolation("Coturn allocation record order is invalid")
        self._observed.add(CoturnLogCategory.ALLOCATION_METHOD)

    def _stale_challenge(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if (
            allocation.phase is not _Phase.ACTIVE
            or allocation.stale_nonce_challenges >= _MAX_STALE_CHALLENGES_PER_ALLOCATION
            or self._stale_challenges >= _MAX_STALE_CHALLENGES_PER_CALL
        ):
            raise _GrammarViolation("Coturn authentication challenge bound is invalid")
        allocation.stale_nonce_challenges += 1
        self._stale_challenges += 1
        self._observed.add(CoturnLogCategory.AUTH_CHALLENGE)

    def _refresh_allocation(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        lifetime = _parse_u64(match.group("lifetime"))
        if allocation.phase is not _Phase.ACTIVE or allocation.pending_usage is not None:
            raise _GrammarViolation("Coturn allocation record order is invalid")
        if lifetime != 0 and not _MIN_POSITIVE_LIFETIME <= lifetime <= _MAX_POSITIVE_LIFETIME:
            raise _GrammarViolation("Coturn allocation lifetime is invalid")
        allocation.phase = _Phase.REFRESH_PENDING
        allocation.pending_refresh_zero = lifetime == 0
        self._observed.add(CoturnLogCategory.ALLOCATION_REFRESH)

    def _complete_refresh(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if (
            allocation.phase is not _Phase.REFRESH_PENDING
            or allocation.pending_refresh_zero is None
        ):
            raise _GrammarViolation("Coturn allocation record order is invalid")
        allocation.phase = _Phase.RELEASED if allocation.pending_refresh_zero else _Phase.ACTIVE
        allocation.pending_refresh_zero = None
        self._observed.add(CoturnLogCategory.ALLOCATION_REFRESH)

    def _begin_usage(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if (
            allocation.phase not in {_Phase.ACTIVE, _Phase.RELEASED}
            or allocation.pending_usage is not None
        ):
            raise _GrammarViolation("Coturn allocation record order is invalid")
        allocation.pending_usage = _traffic(match)
        self._observed.add(CoturnLogCategory.ALLOCATION_USAGE)

    def _complete_usage(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if (
            allocation.phase not in {_Phase.ACTIVE, _Phase.RELEASED}
            or allocation.pending_usage is None
        ):
            raise _GrammarViolation("Coturn allocation record order is invalid")
        peer = _traffic(match)
        updated = [
            _checked_add(left, right)
            for left, right in zip(
                allocation.traffic,
                (*allocation.pending_usage, *peer),
                strict=True,
            )
        ]
        allocation.traffic = updated
        allocation.pending_usage = None
        if allocation.phase is _Phase.RELEASED:
            allocation.release_usage_pairs += 1
        self._observed.add(CoturnLogCategory.ALLOCATION_PEER_USAGE)

    def _close_allocation(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        self._require_owned_endpoint(match)
        if (
            allocation.phase is not _Phase.RELEASED
            or allocation.pending_usage is not None
            or allocation.release_usage_pairs < 1
        ):
            raise _GrammarViolation("Coturn allocation record order is invalid")
        allocation.phase = _Phase.CLOSED
        self._observed.add(CoturnLogCategory.ALLOCATION_CLOSE)

    def _delete_allocation(self, match: re.Match[bytes]) -> None:
        allocation = self._call_allocation(match)
        if allocation.phase is not _Phase.CLOSED or allocation.pending_usage is not None:
            raise _GrammarViolation("Coturn allocation record order is invalid")
        allocation.phase = _Phase.DELETED
        self._observed.add(CoturnLogCategory.ALLOCATION_DELETE)

    def _readiness_challenge(self, match: re.Match[bytes]) -> None:
        self._require_empty_realm(match)
        session = self._readiness_session(match.group("sid"))
        code = match.group("code")
        if (
            session.close_records
            or len(session.challenge_codes) >= _MAX_READINESS_CHALLENGES_PER_SID
            or code in session.challenge_codes
            or (code == b"438: Stale Nonce" and b"401: Unauthorized" not in session.challenge_codes)
        ):
            raise _GrammarViolation("Coturn readiness record order is invalid")
        session.challenge_codes.add(code)
        self._observed.add(CoturnLogCategory.AUTH_CHALLENGE)

    def _readiness_usage(self, match: re.Match[bytes]) -> None:
        self._require_empty_realm(match)
        _traffic(match)
        session = self._readiness_session(match.group("sid"))
        if session.usage_records or session.peer_usage_records or session.close_records:
            raise _GrammarViolation("Coturn readiness record order is invalid")
        session.usage_records = 1
        self._observed.add(CoturnLogCategory.READINESS_EMPTY_SESSION)

    def _readiness_peer_usage(self, match: re.Match[bytes]) -> None:
        self._require_empty_realm(match)
        _traffic(match)
        session = self._readiness_session(match.group("sid"))
        if session.usage_records != 1 or session.peer_usage_records or session.close_records:
            raise _GrammarViolation("Coturn readiness record order is invalid")
        session.peer_usage_records = 1
        self._observed.add(CoturnLogCategory.READINESS_EMPTY_SESSION)

    def _readiness_transport_close(self, match: re.Match[bytes]) -> None:
        self._require_remote_gateway(match)
        session = self._readiness_session(match.group("sid"))
        if session.transport_close_records or session.close_records:
            raise _GrammarViolation("Coturn readiness record order is invalid")
        session.transport_close_records = 1
        self._observed.add(CoturnLogCategory.READINESS_EMPTY_SESSION)

    def _readiness_close(self, match: re.Match[bytes]) -> None:
        self._require_empty_realm(match)
        self._require_owned_endpoint(match)
        session = self._readiness_session(match.group("sid"))
        if (
            session.close_records
            or session.usage_records != session.peer_usage_records
            or session.usage_records not in {0, 1}
        ):
            raise _GrammarViolation("Coturn readiness record order is invalid")
        session.close_records = 1
        self._observed.add(CoturnLogCategory.READINESS_EMPTY_SESSION)

    def _call_allocation(self, match: re.Match[bytes]) -> _Allocation:
        self._require_call_identity(match)
        allocation = self._allocations.get(_parse_sid(match.group("sid")))
        if allocation is None:
            raise _GrammarViolation("Coturn allocation correlation is invalid")
        return allocation

    def _readiness_session(self, encoded_sid: bytes) -> _ReadinessSession:
        sid = _parse_sid(encoded_sid)
        session = self._readiness.get(sid)
        if session is None:
            if len(self._readiness) >= _MAX_READINESS_SESSIONS:
                raise _GrammarViolation("Coturn readiness session bound is invalid")
            session = _ReadinessSession()
            self._readiness[sid] = session
        return session

    def _require_startup_complete(self) -> None:
        if not COTURN_REQUIRED_STARTUP.issubset(
            self._observed
        ) or self._critical_startup_index != len(COTURN_CRITICAL_STARTUP_ORDER):
            raise _GrammarViolation("Coturn startup evidence is incomplete")

    def _require_call_identity(self, match: re.Match[bytes]) -> None:
        if not _same(match.group("username"), self._expected_username) or not _same(
            match.group("realm"), self._expected_realm
        ):
            raise _GrammarViolation("Coturn emitted an unknown allocation record")

    def _require_empty_realm(self, match: re.Match[bytes]) -> None:
        realm = match.group("realm")
        if realm and not _same(realm, self._expected_realm):
            raise _GrammarViolation("Coturn readiness identity is invalid")

    def _require_remote_gateway(self, match: re.Match[bytes]) -> None:
        if not _same(match.group("remote_ipv4"), self._expected_gateway):
            raise _GrammarViolation("Coturn readiness topology is invalid")
        _parse_port(match.group("remote_port"))

    def _require_owned_endpoint(self, match: re.Match[bytes]) -> None:
        if (
            not _same(match.group("local_ipv4"), self._expected_container)
            or not _same(match.group("remote_ipv4"), self._expected_gateway)
            or _parse_port(match.group("local_port")) != COTURN_TLS_PORT
        ):
            raise _GrammarViolation("Coturn allocation topology is invalid")
        _parse_port(match.group("remote_port"))


def _copy_allocation(value: _Allocation) -> _Allocation:
    return _Allocation(
        phase=value.phase,
        pending_refresh_zero=value.pending_refresh_zero,
        pending_usage=value.pending_usage,
        release_usage_pairs=value.release_usage_pairs,
        stale_nonce_challenges=value.stale_nonce_challenges,
        traffic=list(value.traffic),
    )


def _copy_readiness(value: _ReadinessSession) -> _ReadinessSession:
    return _ReadinessSession(
        challenge_codes=set(value.challenge_codes),
        usage_records=value.usage_records,
        peer_usage_records=value.peer_usage_records,
        transport_close_records=value.transport_close_records,
        close_records=value.close_records,
    )


def _validated_identity(value: object, *, maximum: int, label: str) -> bytearray:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise CoturnEvidenceStateError(f"Coturn expected {label} is invalid")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise CoturnEvidenceStateError(f"Coturn expected {label} is invalid") from None
    if any(byte < 0x21 or byte > 0x7E or byte in b"<>\r\n" for byte in encoded):
        raise CoturnEvidenceStateError(f"Coturn expected {label} is invalid")
    return bytearray(encoded)


def _parse_sid(value: bytes) -> int:
    if not 18 <= len(value) <= 20 or not value.isdigit():
        raise CoturnEvidenceStateError("Coturn session identifier is invalid")
    result = int(value)
    if result > _UINT64_MAX or value != f"{result:018d}".encode("ascii"):
        raise CoturnEvidenceStateError("Coturn session identifier is invalid")
    return result


def _parse_u64(value: bytes) -> int:
    if (
        not 1 <= len(value) <= 20
        or not value.isdigit()
        or (len(value) > 1 and value.startswith(b"0"))
    ):
        raise CoturnEvidenceStateError("Coturn numeric evidence is invalid")
    result = int(value)
    if result > _UINT64_MAX:
        raise CoturnEvidenceStateError("Coturn numeric evidence is invalid")
    return result


def _parse_port(value: bytes) -> int:
    result = _parse_u64(value)
    if not 1 <= result <= 65535:
        raise CoturnEvidenceStateError("Coturn endpoint port is invalid")
    return result


def _traffic(match: re.Match[bytes]) -> tuple[int, int, int, int]:
    values = tuple(_parse_u64(match.group(name)) for name in ("rp", "rb", "sp", "sb"))
    return values  # type: ignore[return-value]


def _checked_add(left: int, right: int) -> int:
    if not 0 <= left <= _UINT64_MAX or not 0 <= right <= _UINT64_MAX or left > _UINT64_MAX - right:
        raise CoturnEvidenceStateError("Coturn traffic evidence overflowed")
    return left + right


def _same(value: bytes | None, expected: bytearray) -> bool:
    return value is not None and hmac.compare_digest(value, expected)


def _wipe(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)
    value.clear()


__all__ = [
    "CoturnEvidenceState",
    "CoturnEvidenceStateError",
    "CoturnStateEvidence",
    "CoturnStateProbe",
]

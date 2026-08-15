"""Bounded, redacting public stream parser for pinned Coturn evidence.

Raw chunks and records live only in this framing layer. The adjacent state owner
performs topology, identity, grammar, and lifecycle validation and is scrubbed
before any public error or terminal result crosses this module's boundary.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Iterable, NoReturn

from scripts.voice_pipecat_e2e_coturn_evidence_state import (
    CoturnEvidenceState,
    CoturnEvidenceStateError,
    CoturnStateEvidence,
    CoturnStateProbe,
)
from scripts.voice_pipecat_e2e_coturn_log_grammar import (
    COTURN_MAX_RECORD_BYTES,
    COTURN_REALM,
    COTURN_SOURCE_COMMIT,
    CoturnLogCategory,
    split_coturn_record,
)

_MAX_CONTENT_BYTES = COTURN_MAX_RECORD_BYTES - 1
_MAX_RECORDS = 8192
_FACTORY_TOKEN = object()
_PROBE_TOKEN = object()


class CoturnEvidenceError(RuntimeError):
    """The attached Coturn stream cannot support qualification evidence."""

    def __repr__(self) -> str:
        return "CoturnEvidenceError()"


class _ParseFailure(Exception):
    pass


@dataclass(frozen=True)
class CoturnTrafficTotals:
    """Allowlisted aggregate counters; all additions were checked as uint64."""

    received_packets: int
    received_bytes: int
    sent_packets: int
    sent_bytes: int
    peer_received_packets: int
    peer_received_bytes: int
    peer_sent_packets: int
    peer_sent_bytes: int


@dataclass(frozen=True)
class CoturnEvidence:
    """A redacted proof summary produced only by the qualified parser."""

    allocation_count: int = field(repr=False)
    traffic: CoturnTrafficTotals = field(repr=False)
    observed_categories: frozenset[CoturnLogCategory] = field(repr=False)
    unknown_info_records: int = field(repr=False)
    total_records: int = field(repr=False)
    _token: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._token is not _FACTORY_TOKEN:
            raise TypeError("Coturn evidence is factory-owned")

    def __repr__(self) -> str:
        return "CoturnEvidence()"


@dataclass(frozen=True)
class CoturnProbeSummary:
    """Sanitized discovery output that can never represent qualification."""

    grammar_verified: bool = field(repr=False)
    allocation_count: int = field(repr=False)
    observed_categories: frozenset[CoturnLogCategory] = field(repr=False)
    unknown_info_records: int = field(repr=False)
    grammar_violation_records: int = field(repr=False)
    total_records: int = field(repr=False)
    _token: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._token is not _FACTORY_TOKEN or self.grammar_verified is not False:
            raise TypeError("Coturn probe summary is factory-owned")

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "CoturnProbeSummary(grammar_verified=False)"


class CoturnEvidenceParser:
    """Consume attached output incrementally while retaining one bounded record."""

    __slots__ = ("_failed", "_finished", "_line", "_probe_only", "_record_count", "_state")

    def __init__(
        self,
        *,
        expected_username: object,
        expected_topology: object,
        expected_realm: object = COTURN_REALM,
        _mode: object = None,
    ) -> None:
        self._line = bytearray()
        self._record_count = 0
        self._probe_only = _mode is _PROBE_TOKEN
        self._finished = False
        self._failed = False
        self._state: CoturnEvidenceState | None = None
        failure: str | None = None
        try:
            self._state = CoturnEvidenceState(
                expected_username=expected_username,
                expected_topology=expected_topology,
                expected_realm=expected_realm,
                probe_only=self._probe_only,
            )
        except CoturnEvidenceStateError as error:
            failure = str(error)
        except Exception:
            failure = "Coturn evidence parser is unavailable"
        except BaseException as error:
            self._terminalize(failed=True)
            expected_username = None
            expected_topology = None
            expected_realm = None
            _scrub_control_flow_exception(error)
            raise
        expected_username = None
        expected_topology = None
        expected_realm = None
        if failure is not None:
            self._terminalize(failed=True)
            _raise_public(failure)

    @classmethod
    def for_probe(
        cls,
        *,
        expected_username: object,
        expected_topology: object,
        expected_realm: object = COTURN_REALM,
    ) -> CoturnEvidenceParser:
        parser: CoturnEvidenceParser | None = None
        failure: str | None = None
        try:
            parser = cls(
                expected_username=expected_username,
                expected_topology=expected_topology,
                expected_realm=expected_realm,
                _mode=_PROBE_TOKEN,
            )
        except CoturnEvidenceError as error:
            failure = str(error)
        except Exception:
            failure = "Coturn evidence parser is unavailable"
        except BaseException as error:
            if parser is not None:
                parser._terminalize(failed=True)
            expected_username = None
            expected_topology = None
            expected_realm = None
            _scrub_control_flow_exception(error)
            raise
        expected_username = None
        expected_topology = None
        expected_realm = None
        if failure is not None:
            _raise_public(failure)
        assert parser is not None
        return parser

    def feed(self, chunk: object) -> None:
        """Consume one bytes-like chunk without buffering more than one record."""

        failure: str | None = None
        if self._failed or self._finished:
            failure = "Coturn evidence parser is unavailable"
        try:
            if failure is None:
                self._feed_chunk(chunk)
        except (_ParseFailure, CoturnEvidenceStateError) as error:
            failure = str(error)
        except Exception:
            failure = "Coturn log stream is unavailable"
        except BaseException as error:
            self._terminalize(failed=True)
            chunk = None
            _scrub_control_flow_exception(error)
            raise
        if failure is not None:
            self._terminalize(failed=True)
            chunk = None
            _raise_public(failure)

    def finish(self) -> CoturnEvidence:
        """Validate qualified terminal state and return redacted evidence."""

        failure: str | None = None
        result: CoturnEvidence | None = None
        try:
            if self._failed or self._finished or self._probe_only:
                raise _ParseFailure("Coturn evidence parser is unavailable")
            if self._line:
                raise _ParseFailure("Coturn log stream is truncated")
            state = self._require_state().finish_evidence()
            result = _make_evidence(state, total_records=self._record_count)
        except (_ParseFailure, CoturnEvidenceStateError) as error:
            failure = str(error)
        except Exception:
            failure = "Coturn evidence finalization failed"
        except BaseException as error:
            self._terminalize(failed=True)
            _scrub_control_flow_exception(error)
            raise
        if failure is not None:
            self._terminalize(failed=True)
            _raise_public(failure)
        assert result is not None
        self._terminalize(failed=False)
        return result

    def finish_probe(self) -> CoturnProbeSummary:
        """Return sanitized discovery state that is categorically non-passing."""

        failure: str | None = None
        result: CoturnProbeSummary | None = None
        try:
            if self._failed or self._finished or not self._probe_only:
                raise _ParseFailure("Coturn evidence parser is unavailable")
            if self._line:
                raise _ParseFailure("Coturn log stream is truncated")
            state = self._require_state().finish_probe()
            result = _make_probe(state, total_records=self._record_count)
        except (_ParseFailure, CoturnEvidenceStateError) as error:
            failure = str(error)
        except Exception:
            failure = "Coturn evidence finalization failed"
        except BaseException as error:
            self._terminalize(failed=True)
            _scrub_control_flow_exception(error)
            raise
        if failure is not None:
            self._terminalize(failed=True)
            _raise_public(failure)
        assert result is not None
        self._terminalize(failed=False)
        return result

    def __repr__(self) -> str:
        return "CoturnEvidenceParser()"

    def _feed_chunk(self, chunk: object) -> None:
        view: memoryview | None = None
        try:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise _ParseFailure("Coturn log chunk is invalid")
            try:
                view = memoryview(chunk).cast("B")
            except (TypeError, ValueError):
                raise _ParseFailure("Coturn log chunk is invalid") from None
            for value in view:
                if value == 0x0A:
                    self._process_buffered_line()
                elif len(self._line) >= _MAX_CONTENT_BYTES:
                    raise _ParseFailure("Coturn log record is oversized")
                else:
                    self._line.append(value)
        finally:
            if view is not None:
                view.release()
            view = None
            chunk = None

    def _process_buffered_line(self) -> None:
        line = b""
        body = b""
        level = b""
        record: tuple[bytes, bytes] | None = None
        try:
            line = bytes(self._line)
            _wipe(self._line)
            if not line:
                raise _ParseFailure("Coturn log record is malformed")
            self._record_count += 1
            if self._record_count > _MAX_RECORDS:
                raise _ParseFailure("Coturn log stream is oversized")
            record = split_coturn_record(line)
            if record is None:
                raise _ParseFailure("Coturn log record is malformed")
            level, body = record
            if level != b"INFO":
                raise _ParseFailure("Coturn reported an unsafe severity")
            self._require_state().consume(body)
        finally:
            line = b""
            body = b""
            level = b""
            record = None

    def _require_state(self) -> CoturnEvidenceState:
        if self._state is None:
            raise _ParseFailure("Coturn evidence parser is unavailable")
        return self._state

    def _terminalize(self, *, failed: bool) -> None:
        if self._state is not None:
            self._state.clear()
        self._state = None
        _wipe(self._line)
        self._failed = failed
        self._finished = not failed


def parse_coturn_evidence(
    chunks: Iterable[bytes],
    *,
    expected_username: object,
    expected_topology: object,
    expected_realm: object = COTURN_REALM,
) -> CoturnEvidence:
    """Parse a finite iterable into qualification-capable evidence."""

    result: CoturnEvidence | CoturnProbeSummary | None = None
    failure: str | None = None
    try:
        result, failure = _parse_public(
            chunks,
            expected_username=expected_username,
            expected_topology=expected_topology,
            expected_realm=expected_realm,
            probe=False,
        )
    finally:
        chunks = ()
        expected_username = None
        expected_topology = None
        expected_realm = None
    if failure is not None:
        _raise_public(failure)
    assert isinstance(result, CoturnEvidence)
    return result


def parse_coturn_probe(
    chunks: Iterable[bytes],
    *,
    expected_username: object,
    expected_topology: object,
    expected_realm: object = COTURN_REALM,
) -> CoturnProbeSummary:
    """Parse a finite iterable into a non-qualifying discovery summary."""

    result: CoturnEvidence | CoturnProbeSummary | None = None
    failure: str | None = None
    try:
        result, failure = _parse_public(
            chunks,
            expected_username=expected_username,
            expected_topology=expected_topology,
            expected_realm=expected_realm,
            probe=True,
        )
    finally:
        chunks = ()
        expected_username = None
        expected_topology = None
        expected_realm = None
    if failure is not None:
        _raise_public(failure)
    assert isinstance(result, CoturnProbeSummary)
    return result


def _parse_public(
    chunks: Iterable[bytes],
    *,
    expected_username: object,
    expected_topology: object,
    expected_realm: object,
    probe: bool,
) -> tuple[CoturnEvidence | CoturnProbeSummary | None, str | None]:
    parser: CoturnEvidenceParser | None = None
    failure: str | None = None
    try:
        factory = CoturnEvidenceParser.for_probe if probe else CoturnEvidenceParser
        parser = factory(
            expected_username=expected_username,
            expected_topology=expected_topology,
            expected_realm=expected_realm,
        )
    except CoturnEvidenceError as error:
        failure = str(error)
    except Exception:
        failure = "Coturn evidence parser is unavailable"
    except BaseException as error:
        if parser is not None:
            parser._terminalize(failed=True)
        chunks = ()
        expected_username = None
        expected_topology = None
        expected_realm = None
        _scrub_control_flow_exception(error)
        raise
    expected_username = None
    expected_topology = None
    expected_realm = None
    if failure is not None or parser is None:
        chunks = ()
        return None, failure or "Coturn evidence parser is unavailable"
    try:
        return _consume(chunks, parser=parser, probe=probe)
    finally:
        chunks = ()


def _consume(
    chunks: Iterable[bytes],
    *,
    parser: CoturnEvidenceParser,
    probe: bool,
) -> tuple[CoturnEvidence | CoturnProbeSummary | None, str | None]:
    failure: str | None = None
    result: CoturnEvidence | CoturnProbeSummary | None = None
    chunk: object = None
    iterator: object = None
    try:
        iterator = iter(chunks)
    except Exception:
        parser._terminalize(failed=True)
        failure = "Coturn log stream is unavailable"
    except BaseException as error:
        parser._terminalize(failed=True)
        iterator = None
        chunks = ()
        _scrub_control_flow_exception(error)
        raise
    chunks = ()
    while failure is None:
        try:
            chunk = next(iterator)  # type: ignore[arg-type]
        except StopIteration:
            break
        except Exception:
            parser._terminalize(failed=True)
            failure = "Coturn log stream is unavailable"
            break
        except BaseException as error:
            parser._terminalize(failed=True)
            chunk = None
            iterator = None
            _scrub_control_flow_exception(error)
            raise
        try:
            parser.feed(chunk)
        except CoturnEvidenceError as error:
            failure = str(error)
        except Exception:
            parser._terminalize(failed=True)
            failure = "Coturn log stream is unavailable"
        except BaseException as error:
            parser._terminalize(failed=True)
            chunk = None
            iterator = None
            _scrub_control_flow_exception(error)
            raise
        chunk = None
    if failure is None:
        try:
            result = parser.finish_probe() if probe else parser.finish()
        except CoturnEvidenceError as error:
            failure = str(error)
        except Exception:
            parser._terminalize(failed=True)
            failure = "Coturn evidence finalization failed"
        except BaseException as error:
            parser._terminalize(failed=True)
            iterator = None
            _scrub_control_flow_exception(error)
            raise
    chunk = None
    iterator = None
    return result, failure


def _make_evidence(state: CoturnStateEvidence, *, total_records: int) -> CoturnEvidence:
    return CoturnEvidence(
        allocation_count=state.allocation_count,
        traffic=CoturnTrafficTotals(*state.traffic),
        observed_categories=state.observed_categories,
        unknown_info_records=state.unknown_info_records,
        total_records=total_records,
        _token=_FACTORY_TOKEN,
    )


def _make_probe(state: CoturnStateProbe, *, total_records: int) -> CoturnProbeSummary:
    return CoturnProbeSummary(
        grammar_verified=False,
        allocation_count=state.allocation_count,
        observed_categories=state.observed_categories,
        unknown_info_records=state.unknown_info_records,
        grammar_violation_records=state.grammar_violation_records,
        total_records=total_records,
        _token=_FACTORY_TOKEN,
    )


def _wipe(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)
    value.clear()


def _scrub_control_flow_exception(error: BaseException) -> None:
    """Preserve control flow while removing attached secrets and frame locals."""

    traceback.clear_frames(error.__traceback__)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    error.__dict__.clear()
    if isinstance(error, SystemExit):
        code = error.code if type(error.code) is int or error.code is None else 1
        error.code = code
        error.args = () if code is None else (code,)
    else:
        error.args = ()


def _raise_public(message: str) -> NoReturn:
    raise CoturnEvidenceError(message) from None


__all__ = [
    "COTURN_MAX_RECORD_BYTES",
    "COTURN_REALM",
    "COTURN_SOURCE_COMMIT",
    "CoturnEvidence",
    "CoturnEvidenceError",
    "CoturnEvidenceParser",
    "CoturnLogCategory",
    "CoturnProbeSummary",
    "CoturnTrafficTotals",
    "parse_coturn_evidence",
    "parse_coturn_probe",
]

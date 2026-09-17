"""Synthetic immediate Coturn stdout pump tests; no stream is persisted."""

from __future__ import annotations

import copy
import pickle
import sys
import threading
from functools import partial
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence as coturn_evidence_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence_destination as evidence_destination_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence_result as evidence_result_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_evidence as runtime_evidence_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_evidence_factory as evidence_factory_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_process_claims as process_claims_module,
)
from scripts.voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    _PROBE_DESTINATION_TOKEN,
    COTURN_REALM,
    CoturnEvidenceParser,
    CoturnProbeSummary,
    parse_coturn_probe,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    CoturnRuntimeError,
    confirm_attached_coturn_clean_exit,
    create_attached_coturn_evidence_pump,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_network import TOPOLOGY  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    USERNAME,
    _complete_allocation,
    _startup,
)
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    FakeAttached,
    RawChunk,
    _interrupt_before_line,
    _interrupt_on_return,
    _process,
    _source_line,
)


def _payload() -> bytes:
    value = b"".join([*_startup(), *_complete_allocation(1)])
    return value.replace(b"172.30.0.2", b"172.28.44.2").replace(
        b"172.30.0.1",
        b"172.28.44.1",
    )


def _chunks(payload: bytes, width: int = 137) -> list[RawChunk]:
    return [
        RawChunk("stdout", payload[index : index + width])
        for index in range(0, len(payload), width)
    ]


def test_probe_pump_feeds_split_chunks_and_retains_nonpassing_summary(
    tmp_path: Path,
) -> None:
    payload = _payload()
    attached = FakeAttached(
        chunks=_chunks(payload),
        returncode=0,
        drain_state=True,
    )
    process = _process(tmp_path, attached)
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    pumped = 0
    while pump.pump_once(timeout_seconds=0.1):
        pumped += 1
    assert pumped > 1
    assert attached.chunks == []
    assert process.drained is True
    clean_exit = confirm_attached_coturn_clean_exit(process)
    first = pump.finalize(clean_exit=clean_exit)
    second = pump.finalize(clean_exit=clean_exit)
    assert type(first) is CoturnProbeSummary
    assert first is second
    assert first.grammar_verified is False
    assert not first
    assert payload.decode("ascii") not in repr(pump)
    assert payload.decode("ascii") not in repr(first)
    assert list((tmp_path / "relay-test" / "coturn").iterdir()) == []


def test_probe_finalization_requires_the_same_process_clean_drain(
    tmp_path: Path,
) -> None:
    payload = _payload()
    premature_attached = FakeAttached(
        chunks=_chunks(payload, width=211),
        returncode=0,
        drain_state=True,
    )
    premature_process = _process(tmp_path / "target", premature_attached)
    premature_pump = create_attached_coturn_evidence_pump(
        process=premature_process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    parser = premature_pump._parser
    assert type(parser) is CoturnEvidenceParser
    retained_state = parser._state
    other = _process(
        tmp_path / "other",
        FakeAttached(returncode=0, drain_state=True),
    )
    foreign_exit = confirm_attached_coturn_clean_exit(other)
    with pytest.raises(CoturnRuntimeError, match="requires clean drain") as error:
        premature_pump.finalize(clean_exit=foreign_exit)
    assert not traceback_contains(error.value, payload)
    assert retained_state is not None
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()
    assert parser._line == bytearray()
    assert premature_pump._parser is None
    assert premature_pump._result_slot is None
    with pytest.raises(CoturnRuntimeError, match="pump is unavailable"):
        premature_pump.pump_once(timeout_seconds=0.1)


def test_stderr_is_rejected_before_parser_and_raw_bytes_leave_no_exception_trace(
    tmp_path: Path,
) -> None:
    raw = b"traceback-sentinel-evidence-stderr"
    process = _process(
        tmp_path,
        FakeAttached(chunks=[RawChunk("stderr", raw)], returncode=0, drain_state=True),
    )
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    with pytest.raises(
        CoturnRuntimeError, match=r"^Coturn attached evidence pump failed$"
    ) as error:
        pump.pump_once(timeout_seconds=0.1)
    assert not traceback_contains(error.value, raw)
    assert raw.decode() not in repr(pump)


def test_pump_rejects_mismatched_runtime_topology_before_consuming_output(
    tmp_path: Path,
) -> None:
    raw = b"traceback-sentinel-unconsumed-output"
    attached = FakeAttached(chunks=[RawChunk("stdout", raw)])
    process = _process(tmp_path, attached)
    wrong = type(TOPOLOGY).parse(
        network="172.29.0.0/29",
        gateway="172.29.0.1",
        container="172.29.0.2",
    )
    with pytest.raises(CoturnRuntimeError, match="pump input is invalid") as error:
        create_attached_coturn_evidence_pump(
            process=process,
            expected_username=USERNAME,
            expected_topology=wrong,
        )
    assert len(attached.chunks) == 1
    assert not traceback_contains(error.value, raw)


def _finished_pump(tmp_path: Path):
    attached = FakeAttached(
        chunks=_chunks(_payload(), width=173),
        returncode=0,
        drain_state=True,
    )
    process = _process(tmp_path, attached)
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    while pump.pump_once(timeout_seconds=0.1):
        pass
    return pump, confirm_attached_coturn_clean_exit(process)


def test_probe_finalization_survives_inner_finish_return_control(
    tmp_path: Path,
) -> None:
    pump, clean_exit = _finished_pump(tmp_path)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=CoturnEvidenceParser.finish_probe_into.__code__,
            operation=lambda: pump.finalize(clean_exit=clean_exit),
        )
    assert str(error.value) == ""
    summary = pump.finalize(clean_exit=clean_exit)
    assert type(summary) is CoturnProbeSummary
    assert summary.grammar_verified is False
    assert not summary


def test_probe_finalization_survives_nonconsuming_result_read_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pump, clean_exit = _finished_pump(tmp_path)
    secret = "traceback-sentinel-runtime-summary-read"
    raised = False

    def interrupt(phase: str) -> None:
        nonlocal raised
        if phase == "summary-read" and not raised:
            raised = True
            raise SystemExit(secret)

    monkeypatch.setattr(evidence_result_module, "_probe_result_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as error:
        pump.finalize(clean_exit=clean_exit)
    assert error.value.code == 1
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not traceback_contains(error.value, secret)
    summary = pump.finalize(clean_exit=clean_exit)
    assert summary.grammar_verified is False
    assert not summary


def test_probe_finalization_retains_commit_across_ordinary_read_return_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pump, clean_exit = _finished_pump(tmp_path)
    original = runtime_evidence_module.coturn_probe_summary_from_slot

    def read_then_fail(slot):
        original(slot)
        raise RuntimeError("summary-read-return-cut")

    monkeypatch.setattr(
        runtime_evidence_module,
        "coturn_probe_summary_from_slot",
        read_then_fail,
    )
    with pytest.raises(CoturnRuntimeError, match="evidence finalization failed") as error:
        pump.finalize(clean_exit=clean_exit)
    assert not traceback_contains(error.value, "summary-read-return-cut", USERNAME)
    committed = pump._summary
    assert type(committed) is CoturnProbeSummary
    assert pump.finalize(clean_exit=clean_exit) is committed


def test_probe_finalization_survives_its_outer_return_cut(tmp_path: Path) -> None:
    pump, clean_exit = _finished_pump(tmp_path)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=type(pump).finalize.__code__,
            operation=lambda: pump.finalize(clean_exit=clean_exit),
        )
    assert str(error.value) == "untrusted-return-publication-cut"
    summary = pump.finalize(clean_exit=clean_exit)
    assert summary.grammar_verified is False
    assert not summary


def test_evidence_pump_rejects_copy_and_serialization(tmp_path: Path) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    for operation in (
        lambda: copy.copy(pump),
        lambda: copy.deepcopy(pump),
        lambda: pickle.dumps(pump),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    assert pump._abort() == (False, None)
    process.terminate()


def test_process_publishes_one_canonical_pump_and_rejects_replacement(
    tmp_path: Path,
) -> None:
    baseline = process_claims_module.active_pump_count()
    attached = FakeAttached(chunks=_chunks(_payload()))
    process = _process(tmp_path, attached)
    first = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    second = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    assert first is second
    assert process_claims_module.active_pump_count() == baseline + 1
    with pytest.raises(CoturnRuntimeError, match="pump input is invalid"):
        create_attached_coturn_evidence_pump(
            process=process,
            expected_username=USERNAME + "-different",
            expected_topology=TOPOLOGY,
        )
    assert len(attached.chunks) == len(_chunks(_payload()))
    assert first._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    with pytest.raises(CoturnRuntimeError, match="pump input is invalid"):
        create_attached_coturn_evidence_pump(
            process=process,
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
        )
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


def test_canonical_pump_registry_cap_fails_closed_without_partial_growth(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached())
    fillers = [object() for _ in range(process_claims_module._MAX_ACTIVE_PUMPS)]
    with process_claims_module._REGISTRY_LOCK:
        assert process_claims_module._PUMPS == {}
        assert process_claims_module._PARTIALS == {}
        for key in fillers:
            process_claims_module._PUMPS[key] = (key, key)
    try:
        with pytest.raises(CoturnRuntimeError, match="pump input is invalid"):
            create_attached_coturn_evidence_pump(
                process=process,
                expected_username=USERNAME,
                expected_topology=TOPOLOGY,
            )
        assert process_claims_module.active_pump_count() == len(fillers)
        assert process._pump_claim.state == "empty"
    finally:
        with process_claims_module._REGISTRY_LOCK:
            for key in fillers:
                process_claims_module._PUMPS.pop(key, None)
    assert process_claims_module.active_pump_count() == 0
    process.terminate()


def test_concurrent_same_input_pump_factories_return_one_identity(tmp_path: Path) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def construct() -> None:
        barrier.wait()
        try:
            outcomes.append(
                create_attached_coturn_evidence_pump(
                    process=process,
                    expected_username=USERNAME,
                    expected_topology=TOPOLOGY,
                )
            )
        except BaseException as error:
            outcomes.append(error)

    threads = [threading.Thread(target=construct) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(1.0)
    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert (
        type(outcomes[0]) is type(outcomes[1]) is runtime_evidence_module.AttachedCoturnEvidencePump
    )
    assert outcomes[0] is outcomes[1]
    assert process_claims_module.active_pump_count() == baseline + 1
    assert outcomes[0]._abort() == (False, None)  # type: ignore[attr-defined]
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


@pytest.mark.parametrize("target_name", ["initializer", "init"])
def test_preowned_parser_survives_factory_and_initializer_return_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_name: str,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    original = CoturnEvidenceParser._initialize_probe
    parsers: list[CoturnEvidenceParser] = []
    reserved_counts: list[int] = []

    def capture(cls, **keywords: object):
        parser = keywords.get("_destination")
        assert type(parser) is CoturnEvidenceParser
        parsers.append(parser)
        reserved_counts.append(process_claims_module.active_pump_count())
        return original(**keywords)

    monkeypatch.setattr(CoturnEvidenceParser, "_initialize_probe", classmethod(capture))
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    target_code = (
        original.__func__.__code__
        if target_name == "initializer"
        else CoturnEvidenceParser.__init__.__code__
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(target_code=target_code, operation=operation)
    assert str(error.value) == ""
    assert not traceback_contains(
        error.value,
        USERNAME,
        COTURN_REALM,
        str(TOPOLOGY.network),
        str(TOPOLOGY.gateway),
        str(TOPOLOGY.container),
        str(tmp_path),
    )
    assert len(parsers) == 1
    assert reserved_counts == [baseline + 1]
    discarded = parsers[0]
    assert discarded._state is None
    assert discarded._line == bytearray()
    assert discarded._failed is True
    assert process._pump_claim.state == "empty"
    assert process_claims_module.active_pump_count() == baseline

    pump = operation()
    assert len(parsers) == 2
    assert reserved_counts == [baseline + 1, baseline + 1]
    assert type(pump) is runtime_evidence_module.AttachedCoturnEvidencePump
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


def test_direct_probe_factory_preowns_parser_before_initializer_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_terminalize = CoturnEvidenceParser._terminalize
    parsers: list[CoturnEvidenceParser] = []

    def capture(parser: CoturnEvidenceParser, *, failed: bool) -> None:
        parsers.append(parser)
        original_terminalize(parser, failed=failed)

    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", capture)
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    operation = partial(
        CoturnEvidenceParser._initialize_probe,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
        result_slot=evidence_result_module.new_coturn_probe_result_slot(),
        _destination=parser,
        _destination_token=_PROBE_DESTINATION_TOKEN,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=CoturnEvidenceParser.__init__.__code__,
            operation=operation,
        )
    assert str(error.value) == ""
    assert not traceback_contains(
        error.value,
        USERNAME,
        COTURN_REALM,
        str(TOPOLOGY.network),
        str(TOPOLOGY.gateway),
        str(TOPOLOGY.container),
    )
    assert len(parsers) == 1
    assert parsers[0]._state is None
    assert parsers[0]._line == bytearray()
    assert parsers[0]._failed is True


def test_public_probe_parser_factory_is_not_exposed() -> None:
    assert not hasattr(CoturnEvidenceParser, "for_probe")


def test_private_probe_initializer_return_carries_no_parser_graph() -> None:
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    target = CoturnEvidenceParser._initialize_probe.__func__.__code__
    fired = False

    def trace(frame, event: str, _value):
        nonlocal fired
        if frame.f_code is target and event == "return" and not fired:
            fired = True
            raise KeyboardInterrupt("initializer-return-cut")
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt) as error:
            CoturnEvidenceParser._initialize_probe(
                expected_username=USERNAME,
                expected_topology=TOPOLOGY,
                result_slot=evidence_result_module.new_coturn_probe_result_slot(),
                _destination=parser,
                _destination_token=_PROBE_DESTINATION_TOKEN,
            )
    finally:
        sys.settrace(None)
    assert fired
    assert not traceback_contains(
        error.value,
        USERNAME,
        COTURN_REALM,
        str(TOPOLOGY.network),
        str(TOPOLOGY.gateway),
        str(TOPOLOGY.container),
    )
    assert parser._state is not None
    parser._terminalize(failed=True)


@pytest.mark.parametrize(
    "marker",
    [
        "parser = _destination if",
        "arguments = dict(",
        "owned = True",
        "cls.__init__(parser",
    ],
)
def test_direct_probe_factory_sanitizes_preconstruction_line_controls(
    marker: str,
) -> None:
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    operation = partial(
        CoturnEvidenceParser._initialize_probe,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
        result_slot=evidence_result_module.new_coturn_probe_result_slot(),
        _destination=parser,
        _destination_token=_PROBE_DESTINATION_TOKEN,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=CoturnEvidenceParser._initialize_probe.__func__.__code__,
            line_number=_source_line(CoturnEvidenceParser._initialize_probe.__func__, marker),
            operation=operation,
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, COTURN_REALM)


@pytest.mark.parametrize(
    "marker",
    [
        "parser = object.__new__(parser_type)",
        "parser._state = None",
        "parser._line = bytearray()",
        "parser._failed = True",
        "parser._probe_destination_claim =",
    ],
)
def test_direct_probe_preallocator_line_controls_never_publish_partial_parser(
    marker: str,
) -> None:
    operation = partial(
        parse_coturn_probe,
        (),
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    target = evidence_destination_module.new_probe_parser_destination
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=_source_line(target, marker),
            operation=operation,
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, COTURN_REALM)


def test_probe_destination_requires_its_factory_issued_identity_claim() -> None:
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    copied = copy.copy(parser)
    slot = evidence_result_module.new_coturn_probe_result_slot()

    with pytest.raises(
        evidence_result_module.CoturnEvidenceError,
        match="parser is unavailable",
    ):
        CoturnEvidenceParser._initialize_probe(
            expected_username=USERNAME,
            expected_topology=TOPOLOGY,
            result_slot=slot,
            _destination=copied,
            _destination_token=_PROBE_DESTINATION_TOKEN,
        )

    assert copied._state is None
    assert copied._line == bytearray()
    assert copied._failed is True
    assert slot._owner is None
    CoturnEvidenceParser._initialize_probe(
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
        result_slot=slot,
        _destination=parser,
        _destination_token=_PROBE_DESTINATION_TOKEN,
    )
    assert parser._state is not None
    parser._terminalize(failed=True)


@pytest.mark.parametrize("marker", ['self._state = "claimed"', "self._parser = None"])
def test_probe_destination_claim_line_controls_scrub_public_parser_graph(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    original = evidence_destination_module.new_probe_parser_destination
    parsers: list[CoturnEvidenceParser] = []

    def capture(parser_type: type[object]) -> object:
        parser = original(parser_type)
        assert type(parser) is CoturnEvidenceParser
        parsers.append(parser)
        return parser

    monkeypatch.setattr(coturn_evidence_module, "new_probe_parser_destination", capture)
    claim = evidence_destination_module._ProbeParserDestinationClaim._claim
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=claim.__code__,
            line_number=_source_line(claim, marker),
            operation=partial(
                parse_coturn_probe,
                (),
                expected_username=USERNAME,
                expected_topology=TOPOLOGY,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, COTURN_REALM)
    assert len(parsers) == 1
    assert parsers[0]._state is None
    assert parsers[0]._line == bytearray()
    assert parsers[0]._failed is True


def test_public_probe_parse_scrubs_preowned_parser_on_initializer_return_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evidence_destination_module.new_probe_parser_destination
    parsers: list[CoturnEvidenceParser] = []

    def capture(parser_type: type[object]) -> object:
        parser = original(parser_type)
        assert type(parser) is CoturnEvidenceParser
        parsers.append(parser)
        return parser

    monkeypatch.setattr(coturn_evidence_module, "new_probe_parser_destination", capture)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=CoturnEvidenceParser._initialize_probe.__func__.__code__,
            operation=partial(
                parse_coturn_probe,
                (),
                expected_username=USERNAME,
                expected_topology=TOPOLOGY,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, COTURN_REALM)
    assert len(parsers) == 1
    assert parsers[0]._state is None
    assert parsers[0]._line == bytearray()
    assert parsers[0]._failed is True


def test_probe_factory_rejects_active_destination_without_mutating_it() -> None:
    original_slot = evidence_result_module.new_coturn_probe_result_slot()
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    CoturnEvidenceParser._initialize_probe(
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
        result_slot=original_slot,
        _destination=parser,
        _destination_token=_PROBE_DESTINATION_TOKEN,
    )
    state = parser._state
    owner = parser._finish_owner
    replacement_slot = evidence_result_module.new_coturn_probe_result_slot()
    with pytest.raises(
        evidence_result_module.CoturnEvidenceError,
        match="parser is unavailable",
    ) as error:
        CoturnEvidenceParser._initialize_probe(
            expected_username="replacement-user",
            expected_topology=TOPOLOGY,
            result_slot=replacement_slot,
            _destination=parser,
            _destination_token=_PROBE_DESTINATION_TOKEN,
        )
    assert not traceback_contains(error.value, USERNAME, "replacement-user")
    assert parser._state is state
    assert parser._finish_owner is owner
    assert parser._probe_result_slot is original_slot
    assert state is not None
    assert state._expected_username == bytearray(USERNAME.encode("ascii"))
    assert replacement_slot._owner is None
    parser._terminalize(failed=True)


def test_active_probe_destination_validation_control_preserves_live_state() -> None:
    original_slot = evidence_result_module.new_coturn_probe_result_slot()
    parser = evidence_destination_module.new_probe_parser_destination(CoturnEvidenceParser)
    CoturnEvidenceParser._initialize_probe(
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
        result_slot=original_slot,
        _destination=parser,
        _destination_token=_PROBE_DESTINATION_TOKEN,
    )
    state = parser._state
    owner = parser._finish_owner
    replacement_slot = evidence_result_module.new_coturn_probe_result_slot()
    with pytest.raises(KeyboardInterrupt) as error:
        claim = evidence_destination_module.claim_probe_parser_destination
        _interrupt_before_line(
            target_code=claim.__code__,
            line_number=_source_line(claim, "return bool"),
            operation=partial(
                CoturnEvidenceParser._initialize_probe,
                expected_username="replacement-user",
                expected_topology=TOPOLOGY,
                result_slot=replacement_slot,
                _destination=parser,
                _destination_token=_PROBE_DESTINATION_TOKEN,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, "replacement-user")
    assert parser._state is state
    assert parser._finish_owner is owner
    assert parser._probe_result_slot is original_slot
    assert state is not None
    assert state._expected_username == bytearray(USERNAME.encode("ascii"))
    assert replacement_slot._owner is None
    parser._terminalize(failed=True)


@pytest.mark.parametrize(
    "marker",
    [
        "lock = self._lock",
        "self._claim_process = process",
        "self._parser = parser",
        "self._result_slot = result_slot",
        None,
    ],
)
def test_preowned_pump_survives_initializer_line_and_return_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    marker: str | None,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    pump_type = runtime_evidence_module.AttachedCoturnEvidencePump
    original_new = pump_type._new_destination.__func__
    original_initializer = CoturnEvidenceParser._initialize_probe
    candidates: list[object] = []
    parsers: list[CoturnEvidenceParser] = []
    reserved_counts: list[int] = []

    def capture_pump(cls):
        pump = original_new(cls)
        candidates.append(pump)
        return pump

    def capture_parser(cls, **keywords: object):
        parser = keywords.get("_destination")
        assert type(parser) is CoturnEvidenceParser
        parsers.append(parser)
        reserved_counts.append(process_claims_module.active_pump_count())
        return original_initializer(**keywords)

    monkeypatch.setattr(pump_type, "_new_destination", classmethod(capture_pump))
    monkeypatch.setattr(
        CoturnEvidenceParser,
        "_initialize_probe",
        classmethod(capture_parser),
    )
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    if marker is None:
        interrupt = partial(
            _interrupt_on_return,
            target_code=pump_type.__init__.__code__,
        )
    else:
        interrupt = partial(
            _interrupt_before_line,
            target_code=pump_type.__init__.__code__,
            line_number=_source_line(pump_type.__init__, marker),
        )
    with pytest.raises(KeyboardInterrupt) as error:
        interrupt(operation=operation)
    assert str(error.value) == ""
    assert not traceback_contains(
        error.value,
        USERNAME,
        COTURN_REALM,
        str(TOPOLOGY.network),
        str(TOPOLOGY.gateway),
        str(TOPOLOGY.container),
        str(tmp_path),
    )
    assert len(candidates) == len(parsers) == 1
    assert reserved_counts == [baseline + 1]
    discarded = candidates[0]
    assert discarded._failed is True
    assert discarded._process is None
    assert discarded._claim_process is None
    assert discarded._parser is None
    assert discarded._result_slot is None
    assert parsers[0]._state is None
    assert parsers[0]._line == bytearray()
    assert process._pump_claim.state == "empty"
    assert process_claims_module.active_pump_count() == baseline

    pump = operation()
    assert len(candidates) == len(parsers) == 2
    assert reserved_counts == [baseline + 1, baseline + 1]
    assert type(pump) is pump_type
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


@pytest.mark.parametrize(
    "marker",
    [
        "outcome, key = _claims.claim_evidence_pump",
        "parser = new_probe_parser_destination(",
        "pump = AttachedCoturnEvidencePump._new_destination()",
        "if not _claims.retain_partial_pump(",
        "result_slot = new_coturn_probe_result_slot()",
        "CoturnEvidenceParser._initialize_probe(",
        "AttachedCoturnEvidencePump.__init__(",
        "if not _claims.publish_evidence_pump(",
        'outcome = "published"',
        "if fingerprint is not None and owner is not None:",
    ],
)
def test_pump_factory_transition_controls_leave_no_building_wedge(
    tmp_path: Path,
    marker: str,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    line = _source_line(evidence_factory_module._create_pump_key, marker)
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=evidence_factory_module._create_pump_key.__code__,
            line_number=line,
            operation=operation,
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, str(tmp_path))
    pump = operation()
    assert type(pump) is runtime_evidence_module.AttachedCoturnEvidencePump
    assert process_claims_module.active_pump_count() == baseline + 1
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


@pytest.mark.parametrize(
    "target",
    [
        process_claims_module.claim_evidence_pump,
        process_claims_module.publish_evidence_pump,
    ],
)
def test_pump_claim_and_publish_return_loss_are_recoverable(
    tmp_path: Path,
    target: object,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(target_code=target.__code__, operation=operation)  # type: ignore[attr-defined]
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, str(tmp_path))
    pump = operation()
    assert type(pump) is runtime_evidence_module.AttachedCoturnEvidencePump
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


def test_publish_commit_survives_a_second_control_during_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    original = process_claims_module.claim_evidence_pump
    calls = 0

    def second_control(*arguments: object, **keywords: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SystemExit(31)
        return original(*arguments, **keywords)

    monkeypatch.setattr(process_claims_module, "claim_evidence_pump", second_control)
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    line = _source_line(evidence_factory_module._create_pump_key, 'outcome = "published"')
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=evidence_factory_module._create_pump_key.__code__,
            line_number=line,
            operation=operation,
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, USERNAME, str(tmp_path))
    pump = operation()
    assert type(pump) is runtime_evidence_module.AttachedCoturnEvidencePump
    assert type(pump._parser) is CoturnEvidenceParser
    assert pump._failed is False
    assert pump._finished is False
    assert process_claims_module.active_pump_count() == baseline + 1
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


def test_unpublished_parser_is_force_scrubbed_after_more_than_eight_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    original_publish = process_claims_module.publish_evidence_pump
    original_terminalize = CoturnEvidenceParser._terminalize
    original_initializer = CoturnEvidenceParser._initialize_probe
    parsers: list[CoturnEvidenceParser] = []
    publish_calls = 0
    terminal_controls = 0

    def capture_parser(cls, **keywords: object):
        parser = keywords.get("_destination")
        assert type(parser) is CoturnEvidenceParser
        result = original_initializer(**keywords)
        parsers.append(parser)
        return result

    def fail_first_publish(*arguments: object, **keywords: object) -> bool:
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            raise RuntimeError("synthetic-publish-failure")
        return original_publish(*arguments, **keywords)

    def repeated_control(parser: CoturnEvidenceParser, *, failed: bool) -> None:
        nonlocal terminal_controls
        terminal_controls += 1
        if terminal_controls <= 65:
            raise SystemExit(41)
        original_terminalize(parser, failed=failed)

    monkeypatch.setattr(
        CoturnEvidenceParser,
        "_initialize_probe",
        classmethod(capture_parser),
    )
    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", repeated_control)
    monkeypatch.setattr(
        process_claims_module,
        "publish_evidence_pump",
        fail_first_publish,
    )
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    with pytest.raises(SystemExit) as error:
        operation()
    assert error.value.code == 41
    assert len(parsers) == 1
    discarded = parsers[0]
    assert discarded._state is None
    assert discarded._line == bytearray()
    assert process_claims_module.active_pump_count() == baseline

    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", original_terminalize)
    pump = operation()
    assert len(parsers) == 2
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


def test_partial_candidate_survives_sixty_five_release_controls_before_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = process_claims_module.active_pump_count()
    process = _process(tmp_path, FakeAttached())
    original_publish = process_claims_module.publish_evidence_pump
    original_release = process_claims_module.release_scrubbed_partial_pump
    original_initializer = CoturnEvidenceParser._initialize_probe
    parsers: list[CoturnEvidenceParser] = []
    publish_calls = 0
    release_controls = 0

    def capture_parser(cls, **keywords: object):
        parser = keywords.get("_destination")
        assert type(parser) is CoturnEvidenceParser
        result = original_initializer(**keywords)
        parsers.append(parser)
        return result

    def fail_first_publish(*arguments: object, **keywords: object) -> bool:
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            raise RuntimeError("synthetic-publish-failure")
        return original_publish(*arguments, **keywords)

    def repeated_release_control(*arguments: object, **keywords: object) -> bool:
        nonlocal release_controls
        release_controls += 1
        if release_controls <= 65:
            raise KeyboardInterrupt("partial-release-control")
        return original_release(*arguments, **keywords)

    monkeypatch.setattr(
        CoturnEvidenceParser,
        "_initialize_probe",
        classmethod(capture_parser),
    )
    monkeypatch.setattr(
        process_claims_module,
        "publish_evidence_pump",
        fail_first_publish,
    )
    monkeypatch.setattr(
        process_claims_module,
        "release_scrubbed_partial_pump",
        repeated_release_control,
    )
    operation = partial(
        create_attached_coturn_evidence_pump,
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    pump = None
    failures = 0
    while pump is None and failures < 12:
        try:
            pump = operation()
        except KeyboardInterrupt as error:
            failures += 1
            assert str(error) == ""
            assert len(parsers) in {1, 2}
            assert parsers[0]._state is None
            if len(parsers) == 2:
                assert release_controls == 66
                assert process._pump_claim.state == "published"
            assert process_claims_module.active_pump_count() == baseline + 1
    assert failures == 9
    assert type(pump) is runtime_evidence_module.AttachedCoturnEvidencePump
    assert len(parsers) == 2
    monkeypatch.setattr(
        process_claims_module,
        "release_scrubbed_partial_pump",
        original_release,
    )
    assert pump._abort() == (False, None)
    assert process_claims_module.active_pump_count() == baseline
    process.terminate()


@pytest.mark.parametrize(
    ("target", "marker"),
    [
        (runtime_evidence_module.AttachedCoturnEvidencePump.finalize, "self._summary = candidate"),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._parser_terminalized = True",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._parser = None",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._release_process_claim_locked,
            "self._claim_process = None",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._finished = True",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._failed = False",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._process = None",
        ),
        (
            runtime_evidence_module.AttachedCoturnEvidencePump._recover_summary_locked,
            "self._result_slot = None",
        ),
    ],
)
def test_finalize_publication_store_controls_reconcile_terminal_state(
    tmp_path: Path,
    target: object,
    marker: str,
) -> None:
    baseline = process_claims_module.active_pump_count()
    pump, clean_exit = _finished_pump(tmp_path)
    assert process_claims_module.active_pump_count() == baseline + 1
    line = _source_line(target, marker)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,  # type: ignore[attr-defined]
            line_number=line,
            operation=lambda: pump.finalize(clean_exit=clean_exit),
        )
    assert str(error.value) == ""
    summary = pump.finalize(clean_exit=clean_exit)
    assert summary is pump._summary
    assert pump._finished is True
    assert pump._failed is False
    assert pump._parser_terminalized is True
    assert pump._parser is None
    assert pump._process is None
    assert pump._result_slot is None
    assert pump._claim_process is None
    assert process_claims_module.active_pump_count() == baseline

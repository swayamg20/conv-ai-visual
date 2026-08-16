"""Synthetic immediate Coturn stdout pump tests; no stream is persisted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence_result as evidence_result_module,
)
from scripts.voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    CoturnEvidenceParser,
    CoturnProbeSummary,
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
    _interrupt_on_return,
    _process,
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
    other = _process(
        tmp_path / "other",
        FakeAttached(returncode=0, drain_state=True),
    )
    foreign_exit = confirm_attached_coturn_clean_exit(other)
    with pytest.raises(CoturnRuntimeError, match="requires clean drain") as error:
        premature_pump.finalize(clean_exit=foreign_exit)
    assert not traceback_contains(error.value, payload)
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

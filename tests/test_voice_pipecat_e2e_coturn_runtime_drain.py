"""Synthetic concurrent Coturn evidence-drain tests; no command is executed."""

from __future__ import annotations

import copy
import pickle
import sys
import threading
import time
from functools import partial
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_evidence_result as evidence_result_module,
)
from scripts import voice_pipecat_e2e_coturn_runtime_drain as drain_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_drain_recovery as drain_recovery_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_drain_terminal as drain_terminal_module,
)
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_evidence as runtime_evidence_module,
)
from scripts import voice_pipecat_e2e_coturn_runtime_process as process_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_process_claims as process_claims_module,
)
from scripts.voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    COTURN_REALM,
    CoturnEvidenceParser,
    CoturnProbeSummary,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    start_owned_container_attached,
)
from scripts.voice_pipecat_e2e_coturn_runtime_drain import (  # noqa: E402
    AttachedCoturnEvidenceDrain,
    CoturnEvidenceDrainCleanupAuthority,
    CoturnEvidenceDrainCleanupRequired,
    cleanup_attached_coturn_evidence_drain,
    finish_attached_coturn_evidence_drain,
    new_attached_coturn_evidence_drain,
    start_attached_coturn_evidence_drain,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (  # noqa: E402
    AttachedCoturnEvidencePump,
    create_attached_coturn_evidence_pump,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    AttachedCoturnProcess,
    new_attached_coturn_process,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (  # noqa: E402
    CoturnRuntimeError,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_network import TOPOLOGY  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_evidence import (  # noqa: E402
    USERNAME,
    _complete_allocation,
    _startup,
)
from tests.test_voice_pipecat_e2e_coturn_host import _tools  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    FakeAttached,
    RawChunk,
    _interrupt_before_line,
    _interrupt_on_return,
    _process,
    _source_line,
    _validated,
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


def _owner_graph_secrets(tmp_path: Path) -> tuple[str, ...]:
    return (
        USERNAME,
        COTURN_REALM,
        str(TOPOLOGY.network),
        str(TOPOLOGY.gateway),
        str(TOPOLOGY.container),
        str(tmp_path),
    )


def _owned_drain(
    tmp_path: Path,
    attached: object,
    *,
    clock=time.monotonic,
    absolute_deadline: float | None = None,
) -> tuple[object, AttachedCoturnEvidencePump, AttachedCoturnEvidenceDrain]:
    process = _process(tmp_path, attached)  # type: ignore[arg-type]
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = clock() + 2.0 if absolute_deadline is None else absolute_deadline
    drain = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
        clock=clock,
    )
    return process, pump, drain


def _unstarted_owned_drain(
    tmp_path: Path,
) -> tuple[
    AttachedCoturnProcess,
    AttachedCoturnEvidencePump,
    AttachedCoturnEvidenceDrain,
    object,
]:
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    drain = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=time.monotonic() + 2.0,
    )
    return process, pump, drain, validated


class StagedDrainAttached:
    def __init__(self, chunks: list[object], *, false_drains: int) -> None:
        self.chunks = chunks
        self.false_drains = false_drains
        self.drain_checks = 0
        self.reads: list[float] = []
        self.returncode = 0
        self.polls = 0
        self.terminations = 0

    def read_chunk(self, *, timeout_seconds: float) -> object | None:
        self.reads.append(timeout_seconds)
        if not self.chunks:
            return None
        value = self.chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    @property
    def drained(self) -> bool:
        self.drain_checks += 1
        return not self.chunks and self.drain_checks > self.false_drains

    def poll(self) -> int:
        self.polls += 1
        return self.returncode

    def terminate(self) -> None:
        self.terminations += 1


class BarrierAttached(StagedDrainAttached):
    def __init__(self, chunks: list[object]) -> None:
        super().__init__(chunks, false_drains=0)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._held = False

    def read_chunk(self, *, timeout_seconds: float) -> object | None:
        if not self._held:
            self._held = True
            self.entered.set()
            if not self.release.wait(1.0):
                raise RuntimeError("synthetic barrier timed out")
        return super().read_chunk(timeout_seconds=timeout_seconds)


class StepClock:
    def __init__(self, value: float, step: float) -> None:
        self.value = value
        self.step = step
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            value = self.value
            self.value += self.step
            return value


class FiniteThenHostileClock:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls == 1:
            return 10.0
        if self.mode == "nan":
            return float("nan")
        return 10.0 - float(self.calls)


class FaultyDoneEvent:
    def __init__(self, failures: list[BaseException], *, repeat: bool = False) -> None:
        self.failures = failures
        self.repeat = repeat
        self.calls = 0
        self.event = threading.Event()

    def set(self) -> None:
        self.calls += 1
        if self.failures:
            error = self.failures[0]
            if not self.repeat:
                self.failures.pop(0)
            raise error
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set()


class PreEffectStartRunner:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.starts = 0
        self.settlements = 0

    def run(self, _request: object) -> object:
        raise AssertionError("drain tests do not run commands")

    def start_attached(self, _request: object) -> object:
        self.starts += 1
        raise self.error

    def settle_owned(self) -> bool:
        self.settlements += 1
        return True


def test_drain_continues_after_idle_reads_and_finalizes_only_after_join(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attached = StagedDrainAttached(_chunks(_payload()), false_drains=2)
    process, _, drain = _owned_drain(tmp_path, attached)
    confirmations: list[bool] = []
    original = drain_module.confirm_attached_coturn_clean_exit

    def confirm_after_join(candidate):
        thread = drain._thread
        confirmations.append(drain._done.is_set() and thread is not None and not thread.is_alive())
        return original(candidate)

    monkeypatch.setattr(
        drain_module,
        "confirm_attached_coturn_clean_exit",
        confirm_after_join,
    )
    start_attached_coturn_evidence_drain(drain)
    summary = finish_attached_coturn_evidence_drain(drain)

    assert type(summary) is CoturnProbeSummary
    assert summary.grammar_verified is False
    assert not summary
    assert attached.drain_checks == 4
    assert len(attached.reads) == len(_chunks(_payload())) + 3
    assert confirmations == [True]
    assert process._state == "clean"
    assert "queue" not in type(drain).__slots__


def test_worker_is_caller_preowned_non_daemon_and_barrier_joined(
    tmp_path: Path,
) -> None:
    attached = BarrierAttached(_chunks(_payload(), width=211))
    _, _, drain = _owned_drain(tmp_path, attached)
    start_attached_coturn_evidence_drain(drain)
    assert attached.entered.wait(1.0)
    thread = drain._thread
    assert thread is not None
    assert thread.daemon is False
    assert thread.is_alive()
    attached.release.set()

    summary = finish_attached_coturn_evidence_drain(drain)
    assert summary.grammar_verified is False
    assert not summary


def test_drain_rejects_cross_process_pump_without_consuming_either(
    tmp_path: Path,
) -> None:
    first_attached = FakeAttached(chunks=_chunks(_payload()))
    second_attached = FakeAttached(chunks=_chunks(_payload()))
    first = _process(tmp_path / "first", first_attached)
    second = _process(tmp_path / "second", second_attached)
    pump = create_attached_coturn_evidence_pump(
        process=first,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )

    with pytest.raises(CoturnRuntimeError, match="drain input is invalid"):
        new_attached_coturn_evidence_drain(
            process=second,
            pump=pump,
            absolute_deadline=time.monotonic() + 1.0,
        )

    assert len(first_attached.chunks) == len(_chunks(_payload()))
    assert len(second_attached.chunks) == len(_chunks(_payload()))
    assert pump._drain_claim.owner is None
    assert pump._abort() == (False, None)
    first.terminate()
    second.terminate()


def test_same_process_pump_pair_has_one_atomic_drain_owner(tmp_path: Path) -> None:
    attached = FakeAttached(
        chunks=_chunks(_payload(), width=167),
        returncode=0,
        drain_state=True,
    )
    process = _process(tmp_path, attached)
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    barrier = threading.Barrier(2)
    outcomes: list[object] = []
    deadline = time.monotonic() + 2.0

    def construct() -> None:
        barrier.wait()
        try:
            outcomes.append(
                new_attached_coturn_evidence_drain(
                    process=process,
                    pump=pump,
                    absolute_deadline=deadline,
                )
            )
        except BaseException as error:
            outcomes.append(error)

    first = threading.Thread(target=construct)
    second = threading.Thread(target=construct)
    first.start()
    second.start()
    first.join(1.0)
    second.join(1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    winners = [value for value in outcomes if type(value) is AttachedCoturnEvidenceDrain]
    assert len(winners) == 2
    assert winners[0] is winners[1]
    assert process._state == "running"
    assert len(attached.chunks) == len(_chunks(_payload(), width=167))
    winner = winners[0]
    start_attached_coturn_evidence_drain(winner)
    summary = finish_attached_coturn_evidence_drain(winner)
    assert summary.grammar_verified is False
    assert not summary
    with pytest.raises(CoturnRuntimeError, match="drain input is invalid"):
        new_attached_coturn_evidence_drain(
            process=process,
            pump=pump,
            absolute_deadline=time.monotonic() + 1.0,
        )


def test_claim_return_control_retains_one_canonical_retry_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    original = AttachedCoturnEvidencePump._claim_drain

    deadline = time.monotonic() + 1.0

    def claim_then_control(
        candidate,
        owned_process,
        owner: object,
        claimed_drain,
        absolute_deadline: float,
        clock: object,
        return_key: object,
    ) -> bool:
        assert original(
            candidate,
            owned_process,
            owner,
            claimed_drain,
            absolute_deadline,
            clock,
            return_key,
        )
        raise KeyboardInterrupt("claim-publication-cut")

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_claim_drain", claim_then_control)
    with pytest.raises(KeyboardInterrupt) as error:
        new_attached_coturn_evidence_drain(
            process=process,
            pump=pump,
            absolute_deadline=deadline,
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert process._state == "running"
    assert type(pump._parser) is CoturnEvidenceParser
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    assert recovered is pump._drain_claim.drain
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"
    assert pump._drain_claim.owner is None


def test_claim_registry_publication_cut_recovers_exact_owner(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=drain_module._registry.publish_canonical_return.__code__,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    canonical = pump._drain_claim.drain
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    assert recovered is canonical
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


@pytest.mark.parametrize(
    "marker",
    [
        "self.owner = owner",
        "self.process = process",
        "self.drain = drain",
        "self.absolute_deadline = absolute_deadline",
        "self.clock = clock",
        "self.return_key = return_key",
    ],
)
def test_claim_snapshot_constructor_cuts_never_publish_partial_state(
    tmp_path: Path,
    marker: str,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    line = _source_line(runtime_evidence_module._DrainClaim.__init__, marker)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=runtime_evidence_module._DrainClaim.__init__.__code__,
            line_number=line,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert pump._drain_claim.owner is None
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


@pytest.mark.parametrize(
    ("marker", "claimed_after_cut"),
    [
        ("self._drain_claim = record", False),
        ("if _drain_registry.publish_canonical_return(return_key, drain):", True),
    ],
)
def test_claim_snapshot_atomic_store_cuts_are_recoverable(
    tmp_path: Path,
    marker: str,
    claimed_after_cut: bool,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    line = _source_line(AttachedCoturnEvidencePump._claim_drain, marker)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=AttachedCoturnEvidencePump._claim_drain.__code__,
            line_number=line,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert (pump._drain_claim.owner is not None) is claimed_after_cut
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


def test_constructor_public_return_loss_recovers_exact_owner_only(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=new_attached_coturn_evidence_drain.__code__,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == "untrusted-return-publication-cut"
    assert not hasattr(error.value, "cleanup_authority")
    canonical = pump._drain_claim.drain
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    assert recovered is canonical
    with pytest.raises(CoturnRuntimeError, match="drain input is invalid"):
        new_attached_coturn_evidence_drain(
            process=process,
            pump=pump,
            absolute_deadline=deadline + 0.1,
        )
    assert pump._drain_claim.drain is canonical
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


@pytest.mark.parametrize("preexisting", [False, True])
def test_constructor_final_line_control_is_graph_clean_and_retryable(
    tmp_path: Path,
    preexisting: bool,
) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    if preexisting:
        new_attached_coturn_evidence_drain(
            process=process,
            pump=pump,
            absolute_deadline=deadline,
        )
    line = _source_line(
        new_attached_coturn_evidence_drain,
        "return _registry.return_canonical_drain(return_key)",
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=new_attached_coturn_evidence_drain.__code__,
            line_number=line,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    canonical = pump._drain_claim.drain
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    assert recovered is canonical
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


def test_immutable_deadline_and_attempt_bound_fail_closed(tmp_path: Path) -> None:
    clock = StepClock(10.0, 0.02)
    attached = FakeAttached(returncode=0, drain_state=False)
    process, _, drain = _owned_drain(
        tmp_path,
        attached,
        clock=clock,
        absolute_deadline=10.07,
    )
    runner = process._runner
    start_attached_coturn_evidence_drain(drain)

    with pytest.raises(CoturnRuntimeError, match=r"^Coturn evidence drain failed$"):
        finish_attached_coturn_evidence_drain(drain)

    assert 1 <= len(attached.reads) <= 3
    assert drain._attempts == len(attached.reads)
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert attached.terminations == 1
    assert runner is not None
    assert runner.settlements == 1


@pytest.mark.parametrize("mode", ["nan", "backward"])
def test_dead_worker_cleanup_ignores_subsequently_hostile_clock(
    tmp_path: Path,
    mode: str,
) -> None:
    clock = FiniteThenHostileClock(mode)
    attached = FakeAttached(returncode=0, drain_state=False)
    process, _, drain = _owned_drain(
        tmp_path,
        attached,
        clock=clock,
        absolute_deadline=11.0,
    )
    runner = process._runner
    start_attached_coturn_evidence_drain(drain)
    assert drain._done.wait(1.0)

    with pytest.raises(CoturnRuntimeError, match=r"^Coturn evidence drain failed$"):
        finish_attached_coturn_evidence_drain(drain)

    assert clock.calls == (2 if mode == "nan" else 4_097)
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert runner is not None
    assert runner.settlements == 1
    cleanup_attached_coturn_evidence_drain(drain)


def test_worker_control_preserves_first_signal_and_scrubs_partial_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = b"traceback-sentinel-drain-partial"
    attached = FakeAttached(
        chunks=[RawChunk("stdout", secret), SystemExit(secret.decode())],
        returncode=0,
        drain_state=True,
    )
    process, pump, drain = _owned_drain(tmp_path, attached)
    runner = process._runner
    parser = pump._parser
    assert type(parser) is CoturnEvidenceParser
    retained_state = parser._state
    original_abort = AttachedCoturnEvidencePump._abort_drain

    def later_control(
        candidate: AttachedCoturnEvidencePump,
        owned_process,
        owner: object,
    ):
        failed, _ = original_abort(candidate, owned_process, owner)
        return failed, (KeyboardInterrupt, None)

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_abort_drain", later_control)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(SystemExit) as error:
        finish_attached_coturn_evidence_drain(drain)

    assert error.value.code == 1
    assert not hasattr(error.value, "cleanup_authority")
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not traceback_contains(error.value, secret)
    assert parser._line == bytearray()
    assert retained_state is not None
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()
    assert pump._parser is None
    assert pump._result_slot is None
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert attached.terminations == 1
    assert runner is not None
    assert runner.settlements == 1


def test_cleanup_scrubs_a_partial_record_and_is_idempotent(tmp_path: Path) -> None:
    secret = b"partial-record-never-published"
    attached = FakeAttached(
        chunks=[RawChunk("stdout", secret)],
        returncode=0,
        drain_state=True,
    )
    process, pump, drain = _owned_drain(tmp_path, attached)
    runner = process._runner
    parser = pump._parser
    assert type(parser) is CoturnEvidenceParser
    retained_state = parser._state
    start_attached_coturn_evidence_drain(drain)
    assert drain._done.wait(1.0)

    cleanup_attached_coturn_evidence_drain(drain)
    cleanup_attached_coturn_evidence_drain(drain)

    assert parser._line == bytearray()
    assert retained_state is not None
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()
    assert pump._parser is None
    assert pump._process is None
    assert pump._result_slot is None
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert attached.terminations == 1
    assert runner is not None
    assert runner.settlements == 1


def test_pump_abort_retries_control_and_wipes_before_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = b"partial-abort-control"
    process = _process(
        tmp_path,
        FakeAttached(chunks=[RawChunk("stdout", secret)]),
    )
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    assert pump.pump_once(timeout_seconds=0.1)
    parser = pump._parser
    assert type(parser) is CoturnEvidenceParser
    original = CoturnEvidenceParser._terminalize
    calls = 0

    def interrupt_once(candidate: CoturnEvidenceParser, *, failed: bool) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit(secret.decode())
        original(candidate, failed=failed)

    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", interrupt_once)
    failed, control = pump._abort()
    assert failed is False
    assert control == (SystemExit, 1)
    assert pump._abort() == (False, None)
    assert parser._line == bytearray()
    assert pump._parser is None
    assert pump._process is None
    process.terminate()


def test_partial_record_is_scrubbed_before_control_can_carry_drain_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = b"raw-partial-authority-graph-sentinel"
    attached = FakeAttached(
        chunks=[RawChunk("stdout", secret)],
        returncode=0,
        drain_state=True,
        terminate_result=SystemExit(29),
    )
    process, pump, drain = _owned_drain(tmp_path, attached)
    parser = pump._parser
    assert type(parser) is CoturnEvidenceParser
    original_terminalize = CoturnEvidenceParser._terminalize
    retained_state = parser._state
    start_attached_coturn_evidence_drain(drain)
    assert drain._done.wait(1.0)
    calls = 0

    def persistent_control(_parser: CoturnEvidenceParser, *, failed: bool) -> None:
        nonlocal calls
        assert failed is True
        calls += 1
        raise SystemExit(secret.decode())

    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", persistent_control)
    with pytest.raises(SystemExit) as error:
        cleanup_attached_coturn_evidence_drain(drain)

    assert error.value.code == 29
    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(error.value, secret, *_owner_graph_secrets(tmp_path))
    assert calls == 0
    assert parser._line == bytearray(secret)
    assert retained_state is not None
    assert retained_state._expected_username
    assert retained_state._expected_realm
    assert pump._parser is parser
    assert drain._state == "cleanup-required"
    assert process._state == "terminating"
    monkeypatch.setattr(CoturnEvidenceParser, "_terminalize", original_terminalize)
    cleanup_attached_coturn_evidence_drain(authority)
    assert parser._line == bytearray()
    assert retained_state._expected_username == bytearray()
    assert retained_state._expected_realm == bytearray()
    assert pump._parser is None
    assert drain._state == "cleaned"
    assert process._state == "terminated"


def test_cleanup_retries_are_serialized_on_the_same_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process, _, drain = _owned_drain(tmp_path, FakeAttached())
    original = AttachedCoturnEvidencePump._abort_drain
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def fail_once(
        candidate: AttachedCoturnEvidencePump,
        owned_process,
        owner: object,
    ):
        nonlocal calls
        with calls_lock:
            calls += 1
            current = calls
        if current == 1:
            entered.set()
            assert release.wait(1.0)
            scrubbed, control = original(candidate, owned_process, owner)
            assert scrubbed is False
            assert control is None
            return True, None
        return original(candidate, owned_process, owner)

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_abort_drain", fail_once)
    outcomes: list[object] = []

    def cleanup() -> None:
        try:
            cleanup_attached_coturn_evidence_drain(drain)
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append("cleaned")

    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=cleanup)
    first.start()
    assert entered.wait(1.0)
    second.start()
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == 2
    assert outcomes.count("cleaned") == 1
    failures = [value for value in outcomes if type(value) is CoturnEvidenceDrainCleanupRequired]
    assert len(failures) == 1
    authority = failures[0].cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert drain._state == "cleaned"
    cleanup_attached_coturn_evidence_drain(authority)
    assert process._state == "terminated"


def test_drain_owner_cannot_be_copied_or_serialized(tmp_path: Path) -> None:
    process, _, drain = _owned_drain(tmp_path, FakeAttached())
    for operation in (
        lambda: copy.copy(drain),
        lambda: copy.deepcopy(drain),
        lambda: pickle.dumps(drain),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    cleanup_attached_coturn_evidence_drain(drain)
    assert process._state == "terminated"


@pytest.mark.parametrize("failure_kind", ["ordinary", "control"])
def test_start_failure_settles_process_and_pump_before_public_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
) -> None:
    secret = "traceback-sentinel-drain-start"
    attached = FakeAttached()
    process, pump, drain = _owned_drain(tmp_path, attached)
    runner = process._runner

    def fail_start(_thread: threading.Thread) -> None:
        if failure_kind == "control":
            raise SystemExit(secret)
        raise RuntimeError(secret)

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    if failure_kind == "control":
        with pytest.raises(SystemExit) as error:
            start_attached_coturn_evidence_drain(drain)
        assert error.value.code == 1
    else:
        with pytest.raises(CoturnRuntimeError, match=r"^Coturn evidence drain failed$") as error:
            start_attached_coturn_evidence_drain(drain)
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, secret, *_owner_graph_secrets(tmp_path))

    cleanup_attached_coturn_evidence_drain(drain)
    assert drain._state == "cleaned"
    assert pump._parser is None
    assert process._state == "terminated"
    assert attached.terminations == 1
    assert runner is not None
    assert runner.settlements == 1


def test_starting_publication_control_reconciles_never_started_thread(
    tmp_path: Path,
) -> None:
    process, pump, drain = _owned_drain(tmp_path, FakeAttached())
    line = _source_line(type(drain)._start, "should_start = True")
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=type(drain)._start.__code__,
            line_number=line,
            operation=partial(start_attached_coturn_evidence_drain, drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleaned"
    assert drain._thread is None
    assert pump._parser is None
    assert process._state == "terminated"


def test_preexisting_never_started_starting_state_is_not_treated_as_success(
    tmp_path: Path,
) -> None:
    process, pump, drain = _owned_drain(tmp_path, FakeAttached())
    thread = threading.Thread(target=lambda: None, daemon=False)
    with drain._lock:
        drain._thread = thread
        drain._state = "starting"
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn evidence drain failed$"):
        start_attached_coturn_evidence_drain(drain)
    assert thread.ident is None
    assert not thread.is_alive()
    assert drain._state == "cleaned"
    assert pump._parser is None
    assert process._state == "terminated"


def test_start_control_uses_graph_opaque_authority_if_settlement_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attached = FakeAttached(terminate_result=RuntimeError("start-settlement-cut"))
    process, pump, drain = _owned_drain(tmp_path, attached)

    def fail_start(_thread: threading.Thread) -> None:
        raise SystemExit(23)

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(SystemExit) as error:
        start_attached_coturn_evidence_drain(drain)

    assert error.value.code == 23
    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert repr(authority) == "CoturnEvidenceDrainCleanupAuthority()"
    assert not traceback_contains(
        error.value,
        "start-settlement-cut",
        *_owner_graph_secrets(tmp_path),
    )
    for operation in (
        lambda: copy.copy(authority),
        lambda: copy.deepcopy(authority),
        lambda: pickle.dumps(authority),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    with pytest.raises(TypeError, match="immutable"):
        authority._key = object()
    assert drain._state == "cleanup-required"
    assert type(pump._parser) is CoturnEvidenceParser
    assert process._state == "terminating"

    cleanup_attached_coturn_evidence_drain(authority)
    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert process._state == "terminated"


@pytest.mark.parametrize(
    "marker",
    ["_OWNERS[current._key] = drain", "return current"],
)
def test_cleanup_authority_retain_cuts_preserve_first_control_and_exact_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    marker: str,
) -> None:
    baseline = drain_module._registry.retained_owner_count()
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(terminate_result=RuntimeError("retain-settlement-cut")),
    )

    def fail_start(_thread: threading.Thread) -> None:
        raise SystemExit(23)

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    line = _source_line(drain_module._registry.retain_cleanup_authority, marker)
    with pytest.raises(SystemExit) as error:
        _interrupt_before_line(
            target_code=drain_module._registry.retain_cleanup_authority.__code__,
            line_number=line,
            operation=partial(start_attached_coturn_evidence_drain, drain),
        )
    assert error.value.code == 23
    authority = error.value.cleanup_authority
    assert authority is drain._cleanup_authority
    assert not traceback_contains(
        error.value,
        "retain-settlement-cut",
        *_owner_graph_secrets(tmp_path),
    )
    assert drain_module._registry.retained_owner_count() == baseline + 1
    assert type(pump._parser) is CoturnEvidenceParser
    cleanup_attached_coturn_evidence_drain(authority)
    assert drain_module._registry.retained_owner_count() == baseline
    assert pump._parser is None
    assert process._state == "terminated"


def test_cleanup_authority_is_preowned_before_constructor_state_publication(
    tmp_path: Path,
) -> None:
    owner_baseline = drain_module._registry.retained_owner_count()
    drain_baseline = drain_module._registry.canonical_drain_count()
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    deadline = time.monotonic() + 1.0
    line = _source_line(
        AttachedCoturnEvidenceDrain.__init__,
        "self._process:",
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=AttachedCoturnEvidenceDrain.__init__.__code__,
            line_number=line,
            operation=partial(
                new_attached_coturn_evidence_drain,
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            ),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain_module._registry.retained_owner_count() == owner_baseline
    assert drain_module._registry.canonical_drain_count() == drain_baseline
    recovered = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    cleanup_attached_coturn_evidence_drain(recovered)
    assert process._state == "terminated"


@pytest.mark.parametrize("boundary", ["resolve", "release"])
def test_cleanup_authority_lookup_and_removal_return_cuts_are_idempotent(
    tmp_path: Path,
    boundary: str,
) -> None:
    baseline = drain_module._registry.retained_owner_count()
    process, _, drain = _owned_drain(tmp_path, FakeAttached())
    authority, control = drain_recovery_module.retain_cleanup_authority(drain)
    assert control is None
    assert authority is drain._cleanup_authority
    target = (
        drain_module._registry.resolve_cleanup_authority
        if boundary == "resolve"
        else drain_module._registry.release_cleanup_authority
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=target.__code__,
            operation=partial(cleanup_attached_coturn_evidence_drain, authority),
        )
    assert str(error.value) == ""
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    if boundary == "resolve":
        assert error.value.cleanup_authority is authority
        assert drain._state == "created"
        assert drain_module._registry.retained_owner_count() == baseline + 1
        cleanup_attached_coturn_evidence_drain(authority)
    else:
        assert not hasattr(error.value, "cleanup_authority")
        assert drain._state == "cleaned"
        cleanup_attached_coturn_evidence_drain(authority)
    assert drain_module._registry.retained_owner_count() == baseline
    assert process._state == "terminated"


def test_finish_failure_uses_graph_opaque_authority_after_scrubbing(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(
        chunks=_chunks(_payload()),
        returncode=1,
        drain_state=True,
        terminate_result=RuntimeError("finish-settlement-cut"),
    )
    process, pump, drain = _owned_drain(tmp_path, attached)
    start_attached_coturn_evidence_drain(drain)

    with pytest.raises(CoturnEvidenceDrainCleanupRequired) as error:
        finish_attached_coturn_evidence_drain(drain)

    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(
        error.value,
        "finish-settlement-cut",
        *_owner_graph_secrets(tmp_path),
    )
    assert drain._state == "cleanup-required"
    assert type(pump._parser) is CoturnEvidenceParser
    assert process._state == "terminating"

    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert process._state == "terminated"


def test_canonical_drain_registry_cap_releases_claim_for_reuse(tmp_path: Path) -> None:
    process = _process(tmp_path, FakeAttached())
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    fillers = [object() for _ in range(drain_module._registry._MAX_RETAINED_DRAINS)]
    with drain_module._registry._LOCK:
        assert drain_module._registry._CANONICAL == {}
        for key in fillers:
            drain_module._registry._CANONICAL[key] = key
    deadline = time.monotonic() + 2.0
    try:
        with pytest.raises(CoturnRuntimeError, match="drain input is invalid"):
            new_attached_coturn_evidence_drain(
                process=process,
                pump=pump,
                absolute_deadline=deadline,
            )
        assert pump._drain_claim.owner is None
        assert drain_module._registry.canonical_drain_count() == len(fillers)
    finally:
        with drain_module._registry._LOCK:
            for key in fillers:
                drain_module._registry._CANONICAL.pop(key, None)

    drain = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=deadline,
    )
    cleanup_attached_coturn_evidence_drain(drain)
    assert drain_module._registry.canonical_drain_count() == 0
    assert process._state == "terminated"


def test_cleanup_registry_cap_never_reports_incomplete_cleanup_as_success(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(terminate_result=RuntimeError("synthetic-settlement-cut"))
    process, _, drain = _owned_drain(tmp_path, attached)
    fillers = [object() for _ in range(drain_module._registry._MAX_RETAINED_DRAINS)]
    with drain_module._registry._LOCK:
        assert drain_module._registry._OWNERS == {}
        for key in fillers:
            drain_module._registry._OWNERS[key] = key
    try:
        with pytest.raises(
            CoturnRuntimeError,
            match=r"^Coturn evidence drain cleanup failed$",
        ):
            cleanup_attached_coturn_evidence_drain(drain)
        assert drain._state == "cleanup-required"
        assert process._state == "terminating"
        assert drain_module._registry.retained_owner_count() == len(fillers)
    finally:
        with drain_module._registry._LOCK:
            for key in fillers:
                drain_module._registry._OWNERS.pop(key, None)

    attached.terminate_result = None
    cleanup_attached_coturn_evidence_drain(drain)
    assert drain_module._registry.retained_owner_count() == 0
    assert process._state == "terminated"


def test_finish_pre_store_control_is_sanitized_and_settled(
    tmp_path: Path,
) -> None:
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=241),
            returncode=0,
            drain_state=True,
        ),
    )
    line = _source_line(type(drain)._finish, "process = self._process")
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=type(drain)._finish.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert pump._parser is None


def test_process_settlement_ambiguity_retains_same_retry_owner(
    tmp_path: Path,
) -> None:
    secret = "traceback-sentinel-process-settlement"
    attached = FakeAttached(terminate_result=RuntimeError(secret))
    process, pump, drain = _owned_drain(tmp_path, attached)
    runner = process._runner

    with pytest.raises(CoturnEvidenceDrainCleanupRequired) as error:
        cleanup_attached_coturn_evidence_drain(drain)
    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(error.value, secret, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleanup-required"
    assert drain._process is process
    assert drain._pump is pump
    assert process._state == "terminating"
    assert process._handle is not None
    assert runner is not None
    assert runner.settlements == 1

    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert process._handle is None
    assert runner.settlements == 2


def test_process_cleanup_control_is_sanitized_and_retryable(tmp_path: Path) -> None:
    secret = "traceback-sentinel-process-control"
    attached = FakeAttached(terminate_result=SystemExit(secret))
    process, _, drain = _owned_drain(tmp_path, attached)
    runner = process._runner

    with pytest.raises(SystemExit) as error:
        cleanup_attached_coturn_evidence_drain(drain)
    assert error.value.code == 1
    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(error.value, secret, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleanup-required"
    assert process._state == "terminating"

    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert runner is not None
    assert runner.settlements == 2


def test_process_control_precedes_later_pump_control_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attached = FakeAttached(terminate_result=SystemExit(7))
    process, _, drain = _owned_drain(tmp_path, attached)
    original = AttachedCoturnEvidencePump._abort_drain

    def pump_control(
        candidate: AttachedCoturnEvidencePump,
        owned_process,
        owner: object,
    ):
        scrubbed, control = original(candidate, owned_process, owner)
        assert scrubbed is False
        assert control is None
        return True, (KeyboardInterrupt, None)

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_abort_drain", pump_control)
    with pytest.raises(SystemExit) as error:
        cleanup_attached_coturn_evidence_drain(drain)
    assert error.value.code == 7
    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleanup-required"
    assert process._state == "terminating"

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_abort_drain", original)
    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert process._state == "terminated"


@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "system"])
def test_terminal_event_failure_is_recorded_scrubbed_and_reconciled(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    secret = "traceback-sentinel-terminal-event"
    error: BaseException
    if failure_kind == "ordinary":
        error = RuntimeError(secret)
    elif failure_kind == "keyboard":
        error = KeyboardInterrupt(secret)
    else:
        error = SystemExit(23)
    process, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=197),
            returncode=0,
            drain_state=True,
        ),
    )
    runner = process._runner
    done = FaultyDoneEvent([error])
    drain._done = done  # type: ignore[assignment]
    start_attached_coturn_evidence_drain(drain)

    if failure_kind == "ordinary":
        with pytest.raises(CoturnRuntimeError) as observed:
            finish_attached_coturn_evidence_drain(drain)
    elif failure_kind == "keyboard":
        with pytest.raises(KeyboardInterrupt) as observed:
            finish_attached_coturn_evidence_drain(drain)
        assert str(observed.value) == ""
    else:
        with pytest.raises(SystemExit) as observed:
            finish_attached_coturn_evidence_drain(drain)
        assert observed.value.code == 23

    assert not hasattr(observed.value, "cleanup_authority")
    assert not traceback_contains(observed.value, secret)
    assert done.calls == 2
    assert done.is_set()
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert runner is not None
    assert runner.settlements == 1
    cleanup_attached_coturn_evidence_drain(drain)


def test_dead_worker_reconciles_when_terminal_event_never_publishes(
    tmp_path: Path,
) -> None:
    process, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=181),
            returncode=0,
            drain_state=True,
        ),
    )
    runner = process._runner
    done = FaultyDoneEvent([SystemExit(23)], repeat=True)
    drain._done = done  # type: ignore[assignment]
    start_attached_coturn_evidence_drain(drain)
    thread = drain._thread
    assert thread is not None
    thread.join(1.0)
    assert not thread.is_alive()
    assert not done.is_set()
    assert drain._state == "worker-failed"

    with pytest.raises(SystemExit) as error:
        finish_attached_coturn_evidence_drain(drain)
    assert error.value.code == 23
    assert not hasattr(error.value, "cleanup_authority")
    assert done.calls == drain_module._MAX_TERMINAL_PUBLICATION_ATTEMPTS
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert runner is not None
    assert runner.settlements == 1
    cleanup_attached_coturn_evidence_drain(drain)
    cleanup_attached_coturn_evidence_drain(drain)


def test_live_cleanup_terminates_exact_process_before_joining_worker(
    tmp_path: Path,
) -> None:
    attached = BarrierAttached(_chunks(_payload(), width=251))
    process, _, drain = _owned_drain(tmp_path, attached)
    runner = process._runner
    start_attached_coturn_evidence_drain(drain)
    assert attached.entered.wait(1.0)
    outcomes: list[object] = []

    def cleanup() -> None:
        try:
            cleanup_attached_coturn_evidence_drain(drain)
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append("cleaned")

    cleaner = threading.Thread(target=cleanup)
    cleaner.start()
    attached.release.set()
    cleaner.join(1.0)

    assert not cleaner.is_alive()
    assert outcomes == ["cleaned"]
    assert drain._done.is_set()
    assert drain._state == "cleaned"
    assert process._state == "terminated"
    assert attached.terminations == 1
    assert runner is not None
    assert runner.settlements == 1


@pytest.mark.parametrize(
    ("first_kind", "second_kind", "expected_kind"),
    [
        ("keyboard", "keyboard", KeyboardInterrupt),
        ("system", "keyboard", SystemExit),
        ("keyboard", "system", KeyboardInterrupt),
    ],
)
def test_committed_clean_exit_and_summary_preserve_first_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first_kind: str,
    second_kind: str,
    expected_kind: type[BaseException],
) -> None:
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=193),
            returncode=0,
            drain_state=True,
        ),
    )
    original_confirm = drain_module.confirm_attached_coturn_clean_exit
    original_finalize = AttachedCoturnEvidencePump.finalize

    def confirm_then_control(candidate):
        original_confirm(candidate)
        if first_kind == "keyboard":
            raise KeyboardInterrupt("confirm-return-cut")
        raise SystemExit(23)

    def finalize_then_control(candidate, *, clean_exit):
        original_finalize(candidate, clean_exit=clean_exit)
        if second_kind == "keyboard":
            raise KeyboardInterrupt("finalize-return-cut")
        raise SystemExit(29)

    monkeypatch.setattr(
        drain_module,
        "confirm_attached_coturn_clean_exit",
        confirm_then_control,
    )
    monkeypatch.setattr(AttachedCoturnEvidencePump, "finalize", finalize_then_control)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(expected_kind) as error:
        _interrupt_on_return(
            target_code=type(drain)._finish.__code__,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )

    if expected_kind is KeyboardInterrupt:
        assert str(error.value) == ""
    else:
        assert error.value.code == 23
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert drain._state == "complete"
    assert process._state == "clean"
    assert finish_attached_coturn_evidence_drain(drain) is committed
    assert pump._drain_claim.owner is None


def test_runner_settlement_to_receipt_cut_is_reconciled(
    tmp_path: Path,
) -> None:
    process, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=211),
            returncode=0,
            drain_state=True,
        ),
    )
    runner = process._runner
    line = _source_line(type(process)._confirm_clean_exit, "self._clean_receipt =")
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=type(process)._confirm_clean_exit.__code__,
            line_number=line,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert runner is not None
    assert runner.settlements == 1
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


def test_finalize_ordinary_failure_after_commit_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=227),
            returncode=0,
            drain_state=True,
        ),
    )
    original = AttachedCoturnEvidencePump.finalize

    def finalize_then_fail(candidate, *, clean_exit):
        original(candidate, clean_exit=clean_exit)
        raise RuntimeError("finalize-ordinary-return-cut")

    monkeypatch.setattr(AttachedCoturnEvidencePump, "finalize", finalize_then_fail)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn evidence drain failed$") as error:
        finish_attached_coturn_evidence_drain(drain)
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


def test_inner_finalize_return_cut_commits_before_control(
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=229),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=AttachedCoturnEvidencePump.finalize.__code__,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


def test_result_slot_read_return_cut_commits_before_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=233),
            returncode=0,
            drain_state=True,
        ),
    )
    raised = False

    def interrupt(phase: str) -> None:
        nonlocal raised
        if phase == "summary-read-returned" and not raised:
            raised = True
            raise SystemExit(23)

    monkeypatch.setattr(evidence_result_module, "_probe_result_boundary_hook", interrupt)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(SystemExit) as error:
        finish_attached_coturn_evidence_drain(drain)
    assert error.value.code == 23
    assert not hasattr(error.value, "cleanup_authority")
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


@pytest.mark.parametrize("boundary", ["registry", "pump", "helper"])
def test_claim_release_return_cut_keeps_committed_summary_retryable(
    tmp_path: Path,
    boundary: str,
) -> None:
    _, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=239),
            returncode=0,
            drain_state=True,
        ),
    )
    if boundary == "registry":
        target = drain_module._registry.release_canonical_return.__code__
    elif boundary == "pump":
        target = AttachedCoturnEvidencePump._release_drain_claim.__code__
    else:
        target = drain_recovery_module.release_drain_claim.__code__
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=target,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert pump._drain_claim.owner is None
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


@pytest.mark.parametrize(
    "marker",
    [
        "self.owner = owner",
        "self.process = process",
        "self.drain = drain",
        "self.absolute_deadline = absolute_deadline",
        "self.clock = clock",
        "self.return_key = return_key",
        "clear-store",
    ],
)
def test_claim_clear_snapshot_cuts_keep_summary_retryable(
    tmp_path: Path,
    marker: str,
) -> None:
    _, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=243),
            returncode=0,
            drain_state=True,
        ),
    )
    if marker == "clear-store":
        target = AttachedCoturnEvidencePump._clear_drain_claim_locked
        line = _source_line(target, "self._drain_claim = _DrainClaim()")
    else:
        target = runtime_evidence_module._DrainClaim.__init__
        line = _source_line(target, marker)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert pump._drain_claim.owner is None
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert finish_attached_coturn_evidence_drain(drain) is committed


def test_inner_handoff_controls_are_sanitized_and_each_phase_is_retryable(
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path / "complete",
        FakeAttached(
            chunks=_chunks(_payload(), width=191),
            returncode=0,
            drain_state=True,
        ),
    )
    with pytest.raises(KeyboardInterrupt) as start_error:
        _interrupt_on_return(
            target_code=type(drain)._start.__code__,
            operation=lambda: start_attached_coturn_evidence_drain(drain),
        )
    assert str(start_error.value) == ""
    assert not hasattr(start_error.value, "cleanup_authority")
    start_attached_coturn_evidence_drain(drain)

    with pytest.raises(KeyboardInterrupt) as finish_error:
        _interrupt_on_return(
            target_code=type(drain)._finish.__code__,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )
    assert str(finish_error.value) == ""
    assert not hasattr(finish_error.value, "cleanup_authority")
    summary = finish_attached_coturn_evidence_drain(drain)
    assert summary.grammar_verified is False
    assert not summary

    process, _, cleanup = _owned_drain(tmp_path / "cleanup", FakeAttached())
    with pytest.raises(KeyboardInterrupt) as cleanup_error:
        _interrupt_on_return(
            target_code=type(cleanup)._cleanup.__code__,
            operation=lambda: cleanup_attached_coturn_evidence_drain(cleanup),
        )
    assert str(cleanup_error.value) == ""
    assert not hasattr(cleanup_error.value, "cleanup_authority")
    cleanup_attached_coturn_evidence_drain(cleanup)
    assert process._state == "terminated"


def test_public_return_loss_preserves_idempotent_start_finish_and_cleanup(
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path / "complete",
        FakeAttached(
            chunks=_chunks(_payload(), width=223),
            returncode=0,
            drain_state=True,
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        _interrupt_on_return(
            target_code=start_attached_coturn_evidence_drain.__code__,
            operation=lambda: start_attached_coturn_evidence_drain(drain),
        )
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(KeyboardInterrupt):
        _interrupt_on_return(
            target_code=finish_attached_coturn_evidence_drain.__code__,
            operation=lambda: finish_attached_coturn_evidence_drain(drain),
        )
    first = finish_attached_coturn_evidence_drain(drain)
    second = finish_attached_coturn_evidence_drain(drain)
    assert first is second

    process, _, cleanup = _owned_drain(tmp_path / "cleanup", FakeAttached())
    with pytest.raises(KeyboardInterrupt):
        _interrupt_on_return(
            target_code=cleanup_attached_coturn_evidence_drain.__code__,
            operation=lambda: cleanup_attached_coturn_evidence_drain(cleanup),
        )
    cleanup_attached_coturn_evidence_drain(cleanup)
    assert process._state == "terminated"


@pytest.mark.parametrize(
    ("target", "marker"),
    [
        (drain_terminal_module.DrainTerminalTransition.__init__, '"target", target'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"phase", phase'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"process", process'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"pump", pump'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"thread", thread'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"clock", clock'),
        (drain_terminal_module.DrainTerminalTransition.__init__, '"summary", summary'),
        (drain_terminal_module._terminal_step, "snapshot = DrainTerminalTransition("),
        (drain_terminal_module._terminal_step, '"_state", f"terminalizing-{target}"'),
        (drain_terminal_module._terminal_step, '"_terminal_transition", snapshot'),
        (drain_terminal_module._terminal_step, "failed, control = release_claim(drain)"),
        (drain_terminal_module._terminal_step, "replacement = DrainTerminalTransition("),
        (drain_terminal_module._terminal_step, '"_terminal_transition", replacement'),
        (drain_terminal_module._clear_one_terminal_resource, '"_process", None'),
        (drain_terminal_module._clear_one_terminal_resource, '"_pump", None'),
        (drain_terminal_module._clear_one_terminal_resource, '"_thread", None'),
        (drain_terminal_module._clear_one_terminal_resource, '"_clock", None'),
        (drain_terminal_module._clear_one_terminal_resource, "empty = DrainTerminalTransition("),
        (drain_terminal_module._clear_one_terminal_resource, '"_terminal_transition", empty'),
        (drain_terminal_module._terminal_step, '"_state", target'),
        (drain_terminal_module._terminal_step, '"_terminal_transition", None'),
    ],
)
def test_every_finish_terminal_transaction_cut_converges_without_graph(
    tmp_path: Path,
    target: object,
    marker: str,
) -> None:
    _, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=257),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    line = _source_line(target, marker)

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )

    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    committed = drain._summary
    assert type(committed) is CoturnProbeSummary
    assert drain._state == "complete"
    assert drain._terminal_transition is None
    assert all(getattr(drain, name) is None for name in ("_process", "_pump", "_thread", "_clock"))
    assert pump._drain_claim.owner is None
    assert finish_attached_coturn_evidence_drain(drain) is committed


@pytest.mark.parametrize(
    "marker",
    [
        '"_process", None',
        '"_pump", None',
        '"_thread", None',
        '"_clock", None',
    ],
)
def test_every_scrub_cleanup_resource_clear_cut_converges_without_graph(
    tmp_path: Path,
    marker: str,
) -> None:
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=[RawChunk("stdout", b"partial-terminal-record")],
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    assert drain._done.wait(1.0)
    target = drain_terminal_module._clear_one_terminal_resource
    line = _source_line(target, marker)

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(cleanup_attached_coturn_evidence_drain, drain),
        )

    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleaned"
    assert drain._terminal_transition is None
    assert all(
        getattr(drain, name) is None
        for name in ("_process", "_pump", "_thread", "_clock", "_summary")
    )
    assert process._state == "terminated"
    assert pump._parser is None
    cleanup_attached_coturn_evidence_drain(drain)


def test_complete_cleanup_summary_clear_cut_is_terminally_empty(tmp_path: Path) -> None:
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=263),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    summary = finish_attached_coturn_evidence_drain(drain)
    assert drain._summary is summary
    target = drain_terminal_module._clear_one_terminal_resource
    line = _source_line(target, '"_summary", None')

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(cleanup_attached_coturn_evidence_drain, drain),
        )

    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "cleaned"
    assert drain._summary is None
    assert drain._terminal_transition is None
    cleanup_attached_coturn_evidence_drain(drain)


def test_cached_finish_resumes_the_exact_recoverable_terminal_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = drain_module._registry.retained_owner_count()
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=269),
            returncode=0,
            drain_state=True,
        ),
    )
    original = AttachedCoturnEvidencePump._release_drain_claim
    blocked = True

    def release(candidate, process, owner, owned_drain):
        if blocked:
            return False
        return original(candidate, process, owner, owned_drain)

    monkeypatch.setattr(AttachedCoturnEvidencePump, "_release_drain_claim", release)
    start_attached_coturn_evidence_drain(drain)
    with pytest.raises(CoturnEvidenceDrainCleanupRequired) as first:
        finish_attached_coturn_evidence_drain(drain)
    authority = first.value.cleanup_authority
    committed = drain._summary
    transition = drain._terminal_transition
    assert type(committed) is CoturnProbeSummary
    assert type(transition) is drain_terminal_module.DrainTerminalTransition
    assert transition.phase == "owned"
    assert drain_module._registry.retained_owner_count() == baseline + 1

    blocked = False
    target = drain_terminal_module._clear_one_terminal_resource
    line = _source_line(target, '"_pump", None')
    with pytest.raises(KeyboardInterrupt):
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )
    assert finish_attached_coturn_evidence_drain(drain) is committed
    assert drain_module._registry.retained_owner_count() == baseline
    cleanup_attached_coturn_evidence_drain(authority)


def test_bounded_terminal_control_exposes_one_opaque_retry_and_reuses_capacity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner_baseline = drain_module._registry.retained_owner_count()
    drain_baseline = drain_module._registry.canonical_drain_count()
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=271),
            returncode=0,
            drain_state=True,
        ),
    )
    monkeypatch.setattr(drain_terminal_module, "_MAX_TERMINAL_TRANSITION_ATTEMPTS", 3)
    start_attached_coturn_evidence_drain(drain)
    target = drain_terminal_module._clear_one_terminal_resource
    line = _source_line(target, '"_process", None')

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )

    authority = error.value.cleanup_authority
    assert type(authority) is CoturnEvidenceDrainCleanupAuthority
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    transition = drain._terminal_transition
    assert type(transition) is drain_terminal_module.DrainTerminalTransition
    assert transition.phase == "released"
    assert transition.process is process and transition.pump is pump
    assert transition.summary is drain._summary
    with pytest.raises(TypeError, match="immutable"):
        transition.phase = "empty"
    for operation in (
        lambda: copy.copy(transition),
        lambda: copy.deepcopy(transition),
        lambda: pickle.dumps(transition),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    monkeypatch.setattr(drain_terminal_module, "_MAX_TERMINAL_TRANSITION_ATTEMPTS", 64)
    cleanup_attached_coturn_evidence_drain(authority)
    assert drain._state == "cleaned"
    assert drain._terminal_transition is None
    assert all(
        getattr(drain, name) is None
        for name in ("_process", "_pump", "_thread", "_clock", "_summary")
    )
    assert drain_module._registry.retained_owner_count() == owner_baseline
    assert drain_module._registry.canonical_drain_count() == drain_baseline

    next_process, _, next_drain = _owned_drain(tmp_path / "reused", FakeAttached())
    cleanup_attached_coturn_evidence_drain(next_drain)
    assert next_process._state == "terminated"
    assert drain_module._registry.retained_owner_count() == owner_baseline
    assert drain_module._registry.canonical_drain_count() == drain_baseline


def test_terminal_release_control_precedes_later_clear_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=277),
            returncode=0,
            drain_state=True,
        ),
    )
    original = drain_recovery_module.release_drain_claim

    def release_with_first_control(candidate):
        failed, _ = original(candidate)
        return failed, (SystemExit, 37)

    monkeypatch.setattr(drain_recovery_module, "release_drain_claim", release_with_first_control)
    start_attached_coturn_evidence_drain(drain)
    target = drain_terminal_module._clear_one_terminal_resource
    line = _source_line(target, '"_process", None')

    with pytest.raises(SystemExit) as error:
        _interrupt_before_line(
            target_code=target.__code__,
            line_number=line,
            operation=partial(finish_attached_coturn_evidence_drain, drain),
        )

    assert error.value.code == 37
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == "complete"
    assert drain._terminal_transition is None
    assert pump._drain_claim.owner is None
    committed = drain._summary
    assert finish_attached_coturn_evidence_drain(drain) is committed


def test_terminal_steps_never_publish_a_partial_resource_graph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process, pump, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=281),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    thread = drain._thread
    clock = drain._clock
    expected = (process, pump, thread, clock)
    observations: list[tuple[str, object, tuple[object, ...]]] = []
    original = drain_terminal_module._terminal_step

    def observe(candidate, *, target, release_claim):
        result = original(candidate, target=target, release_claim=release_claim)
        with candidate._lock:
            observations.append(
                (
                    candidate._state,
                    candidate._terminal_transition,
                    (
                        candidate._process,
                        candidate._pump,
                        candidate._thread,
                        candidate._clock,
                    ),
                )
            )
        return result

    monkeypatch.setattr(drain_terminal_module, "_terminal_step", observe)
    finish_attached_coturn_evidence_drain(drain)

    assert observations
    for state, transition, resources in observations:
        if state == "complete":
            assert resources == (None, None, None, None)
            assert transition is None or transition.phase == "empty"
        elif any(resource is None for resource in resources):
            assert type(transition) is drain_terminal_module.DrainTerminalTransition
            if transition.phase == "released":
                assert (
                    transition.process,
                    transition.pump,
                    transition.thread,
                    transition.clock,
                ) == expected
            else:
                assert transition.phase == "empty"
                assert resources == (None, None, None, None)


@pytest.mark.parametrize("operation", ["finish", "cleanup"])
def test_finite_terminal_controls_converge_and_preserve_the_first_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    process, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=283),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    original = drain_terminal_module._terminal_step
    controls: list[BaseException] = [SystemExit(43)] + [KeyboardInterrupt()] * 19

    def interrupt_then_settle(candidate, *, target, release_claim):
        if controls:
            raise controls.pop(0)
        return original(candidate, target=target, release_claim=release_claim)

    monkeypatch.setattr(drain_terminal_module, "_terminal_step", interrupt_then_settle)
    public_operation = (
        partial(finish_attached_coturn_evidence_drain, drain)
        if operation == "finish"
        else partial(cleanup_attached_coturn_evidence_drain, drain)
    )
    with pytest.raises(SystemExit) as error:
        public_operation()

    assert error.value.code == 43
    assert not controls
    assert not hasattr(error.value, "cleanup_authority")
    assert not traceback_contains(error.value, *_owner_graph_secrets(tmp_path))
    assert drain._state == ("complete" if operation == "finish" else "cleaned")
    assert drain._terminal_transition is None
    assert all(getattr(drain, name) is None for name in ("_process", "_pump", "_thread", "_clock"))
    if operation == "finish":
        assert type(finish_attached_coturn_evidence_drain(drain)) is CoturnProbeSummary
    else:
        assert process._state == "terminated"
        cleanup_attached_coturn_evidence_drain(drain)


@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    [
        ("ordinary", CoturnRuntimeError),
        ("keyboard", KeyboardInterrupt),
        ("system", SystemExit),
    ],
)
def test_pre_effect_process_start_failure_allows_exact_unstarted_drain_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
    expected: type[BaseException],
) -> None:
    owner_baseline = drain_module._registry.retained_owner_count()
    drain_baseline = drain_module._registry.canonical_drain_count()
    pump_baseline = process_claims_module.active_pump_count()
    process, pump, drain, validated = _unstarted_owned_drain(tmp_path)
    parser = pump._parser
    error: BaseException
    if failure_kind == "ordinary":
        error = RuntimeError("pre-effect-start-secret")
    elif failure_kind == "keyboard":
        error = KeyboardInterrupt("pre-effect-start-secret")
    else:
        error = SystemExit(47)
    runner = PreEffectStartRunner(error)

    with pytest.raises(expected) as captured:
        start_owned_container_attached(
            runner=runner,  # type: ignore[arg-type]
            tools=_tools(),
            container=validated,  # type: ignore[arg-type]
            process=process,
        )

    if failure_kind == "system":
        assert captured.value.code == 47  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "pre-effect-start-secret")
    assert runner.starts == runner.settlements == 1
    assert process._state == "empty"

    terminate_calls = 0

    def forbidden_terminate(_candidate: AttachedCoturnProcess) -> None:
        nonlocal terminate_calls
        terminate_calls += 1
        raise AssertionError("unstarted process termination is forbidden")

    monkeypatch.setattr(AttachedCoturnProcess, "terminate", forbidden_terminate)
    cleanup_attached_coturn_evidence_drain(drain)
    cleanup_attached_coturn_evidence_drain(drain)

    assert terminate_calls == 0
    assert process._state == "drain-retired"
    assert pump._parser is None
    assert parser is not None and parser._state is None
    assert pump._drain_claim.owner is None
    assert drain._state == "cleaned"
    assert drain_module._registry.retained_owner_count() == owner_baseline
    assert drain_module._registry.canonical_drain_count() == drain_baseline
    assert process_claims_module.active_pump_count() == pump_baseline


@pytest.mark.parametrize("timing", ["before", "after"])
@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "system"])
def test_unstarted_cleanup_reconciles_every_claim_release_cut(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timing: str,
    failure_kind: str,
) -> None:
    process, pump, drain, _ = _unstarted_owned_drain(tmp_path)
    original = drain_recovery_module.release_drain_claim
    calls = 0

    def release_with_cut(candidate):
        nonlocal calls
        calls += 1
        if calls == 1 and timing == "before":
            _raise_claim_cut(failure_kind)
        result = original(candidate)
        if calls == 1 and timing == "after":
            _raise_claim_cut(failure_kind)
        return result

    def forbidden_terminate(_candidate: AttachedCoturnProcess) -> None:
        raise AssertionError("unstarted process termination is forbidden")

    monkeypatch.setattr(drain_recovery_module, "release_drain_claim", release_with_cut)
    monkeypatch.setattr(AttachedCoturnProcess, "terminate", forbidden_terminate)
    if failure_kind == "ordinary":
        cleanup_attached_coturn_evidence_drain(drain)
    else:
        expected = KeyboardInterrupt if failure_kind == "keyboard" else SystemExit
        with pytest.raises(expected) as captured:
            cleanup_attached_coturn_evidence_drain(drain)
        if failure_kind == "system":
            assert captured.value.code == 53  # type: ignore[attr-defined]
        assert not hasattr(captured.value, "cleanup_authority")
        assert not traceback_contains(captured.value, "unstarted-claim-cut")

    assert calls >= 2
    assert process._state == "drain-retired"
    assert pump._parser is None
    assert pump._drain_claim.owner is None
    assert drain._state == "cleaned"
    cleanup_attached_coturn_evidence_drain(drain)


@pytest.mark.parametrize("boundary", ["state", "return"])
def test_unstarted_retirement_publication_cut_is_atomic_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
) -> None:
    process, pump, drain, _ = _unstarted_owned_drain(tmp_path)

    def forbidden_terminate(_candidate: AttachedCoturnProcess) -> None:
        raise AssertionError("unstarted process termination is forbidden")

    monkeypatch.setattr(AttachedCoturnProcess, "terminate", forbidden_terminate)
    operation = partial(cleanup_attached_coturn_evidence_drain, drain)
    with pytest.raises(KeyboardInterrupt) as captured:
        if boundary == "state":
            target = AttachedCoturnProcess._retire_unstarted_for_drain_cleanup
            line = _source_line(target, 'self._state = "drain-retired"')
            _interrupt_before_line(
                target_code=target.__code__,
                line_number=line,
                operation=operation,
            )
        else:
            _interrupt_on_return(
                target_code=AttachedCoturnProcess._retire_unstarted_for_drain_cleanup.__code__,
                operation=operation,
            )

    assert str(captured.value) == ""
    assert not hasattr(captured.value, "cleanup_authority")
    assert process._state == "drain-retired"
    assert pump._parser is None
    assert drain._state == "cleaned"
    cleanup_attached_coturn_evidence_drain(drain)


def test_unstarted_retirement_serializes_against_a_concurrent_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process, _, drain, validated = _unstarted_owned_drain(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = process_module._active_run_matches

    def hold_retirement(authority, identity):
        entered.set()
        assert release.wait(1.0)
        return original(authority, identity)

    monkeypatch.setattr(process_module, "_active_run_matches", hold_retirement)
    runner = PreEffectStartRunner(AssertionError("attached start must not execute"))
    outcomes: list[object] = []

    def cleanup() -> None:
        try:
            cleanup_attached_coturn_evidence_drain(drain)
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append("cleaned")

    def start() -> None:
        try:
            start_owned_container_attached(
                runner=runner,  # type: ignore[arg-type]
                tools=_tools(),
                container=validated,  # type: ignore[arg-type]
                process=process,
            )
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append("started")

    cleaner = threading.Thread(target=cleanup)
    starter = threading.Thread(target=start)
    cleaner.start()
    assert entered.wait(1.0)
    starter.start()
    release.set()
    cleaner.join(1.0)
    starter.join(1.0)

    assert not cleaner.is_alive() and not starter.is_alive()
    assert outcomes.count("cleaned") == 1
    failures = [value for value in outcomes if type(value) is CoturnRuntimeError]
    assert len(failures) == 1
    assert str(failures[0]) == "Coturn attached start failed"
    assert runner.starts == 0 and runner.settlements == 0
    assert process._state == "drain-retired"
    assert drain._state == "cleaned"


def _raise_claim_cut(failure_kind: str) -> None:
    if failure_kind == "ordinary":
        raise RuntimeError("unstarted-claim-cut")
    if failure_kind == "keyboard":
        raise KeyboardInterrupt("unstarted-claim-cut")
    raise SystemExit(53)


def test_cleanup_transition_retains_summary_until_graph_empty_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, drain = _owned_drain(
        tmp_path,
        FakeAttached(
            chunks=_chunks(_payload(), width=293),
            returncode=0,
            drain_state=True,
        ),
    )
    start_attached_coturn_evidence_drain(drain)
    summary = finish_attached_coturn_evidence_drain(drain)
    monkeypatch.setattr(drain_terminal_module, "_MAX_TERMINAL_TRANSITION_ATTEMPTS", 3)

    with pytest.raises(CoturnEvidenceDrainCleanupRequired) as captured:
        cleanup_attached_coturn_evidence_drain(drain)

    transition = drain._terminal_transition
    assert type(transition) is drain_terminal_module.DrainTerminalTransition
    assert transition.phase == "released"
    assert transition.summary is summary
    assert drain._summary is None
    assert drain._state == "terminalizing-cleaned"
    for operation in (
        lambda: copy.copy(transition),
        lambda: copy.deepcopy(transition),
        lambda: pickle.dumps(transition),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()

    monkeypatch.setattr(drain_terminal_module, "_MAX_TERMINAL_TRANSITION_ATTEMPTS", 64)
    cleanup_attached_coturn_evidence_drain(captured.value.cleanup_authority)
    assert drain._state == "cleaned"
    assert drain._terminal_transition is None

"""Canonical pump/drain return-loss tests for the relay aggregate."""
# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_owner_evidence as evidence_module
import scripts.voice_pipecat_e2e_relay_owner_username as username_module
from scripts import voice_pipecat_e2e_coturn_runtime_drain_recovery as drain_recovery
from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as drain_registry
from scripts import voice_pipecat_e2e_coturn_runtime_drain_terminal as drain_terminal
from scripts import voice_pipecat_e2e_coturn_runtime_process_claims as process_claims
from scripts.voice_pipecat_e2e_coturn_docker_network import NetworkPlan
from scripts.voice_pipecat_e2e_coturn_runtime import new_attached_coturn_process
from scripts.voice_pipecat_e2e_coturn_runtime_drain import (
    AttachedCoturnEvidenceDrain,
    cleanup_attached_coturn_evidence_drain,
    new_attached_coturn_evidence_drain,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
    AttachedCoturnEvidencePump,
    create_attached_coturn_evidence_pump,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_coturn_runtime_process_claims import PumpClaim
from scripts.voice_pipecat_e2e_relay_owner import (
    RelayProbeCleanupRequired,
    RelayProbeOwnerError,
    cleanup_relay_probe,
    new_relay_probe_owner,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import TOPOLOGY
from tests.test_voice_pipecat_e2e_coturn_evidence import USERNAME
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (
    FakeAttached,
    _process,
    _validated,
)
from tests.test_voice_pipecat_e2e_relay_owner import _object
from tests.test_voice_pipecat_e2e_relay_owner_recovery import _factory_context


class _AdoptionDestination:
    published = False

    def publish(self, value: object) -> None:
        assert value is True
        self.published = True


def _raise_loss(kind: str) -> None:
    if kind == "ordinary":
        raise RuntimeError("raw-canonical-return-loss")
    if kind == "keyboard":
        raise KeyboardInterrupt("raw-canonical-return-loss")
    raise SystemExit(73)


def _owner_with_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    started: bool,
) -> tuple[object, object, AttachedCoturnProcess, list[str]]:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    process = (
        _process(tmp_path / "started-process", FakeAttached())
        if started
        else new_attached_coturn_process(_validated(tmp_path / "empty-process"))
    )
    plan = NetworkPlan(identity=owner._identity, paths=owner._paths, topology=TOPOLOGY)
    owner._publish("network_plan", plan, NetworkPlan)
    owner._publish("process", process, AttachedCoturnProcess)
    return owner, destination, process, events


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_repeated_pump_return_loss_is_recovered_without_username(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    baseline = process_claims.active_pump_count()
    owner, destination, process, events = _owner_with_process(
        tmp_path,
        monkeypatch,
        started=False,
    )
    original = username_module.create_attached_coturn_evidence_pump
    calls = 0
    canonical: AttachedCoturnEvidencePump | None = None

    def lost_return(**kwargs: object) -> object:
        nonlocal calls, canonical
        calls += 1
        candidate = original(**kwargs)  # type: ignore[arg-type]
        assert type(candidate) is AttachedCoturnEvidencePump
        if canonical is None:
            canonical = candidate
        assert candidate is canonical
        _raise_loss(kind)

    monkeypatch.setattr(
        username_module,
        "create_attached_coturn_evidence_pump",
        lost_return,
    )
    monkeypatch.setattr(
        username_module,
        "new_attached_coturn_evidence_drain",
        lambda **_kwargs: pytest.fail("drain cannot precede recovered pump publication"),
    )
    sink = username_module._new_username_sink(owner)
    expected = {
        "ordinary": RelayProbeOwnerError,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        sink._accept_relay_turn_username(USERNAME, _AdoptionDestination())
    sink._clear()
    assert calls == username_module._RECOVERY_ATTEMPTS + 1
    assert owner._read("pump", AttachedCoturnEvidencePump) is None
    assert canonical is not None
    if kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    owner._remember_exception(captured.value)
    if kind == "ordinary":
        cleanup_relay_probe(owner)
    else:
        with pytest.raises(KeyboardInterrupt if kind == "keyboard" else SystemExit) as control:
            cleanup_relay_probe(owner)
        assert not hasattr(control.value, "cleanup_authority")
    assert process._pump_claim.state == "terminal"
    assert canonical._claim_process is None
    assert canonical._process is None
    assert canonical._parser is None
    assert canonical._result_slot is None
    assert process_claims.active_pump_count() == baseline
    assert destination._record is None  # type: ignore[attr-defined]
    assert events == ["settle-runner"]


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_repeated_drain_return_loss_recovers_exact_canonical_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    pump_baseline = process_claims.active_pump_count()
    drain_baseline = drain_registry.canonical_drain_count()
    owner, destination, process, events = _owner_with_process(
        tmp_path,
        monkeypatch,
        started=False,
    )
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    owner._publish("pump", pump, AttachedCoturnEvidencePump)
    original = username_module.new_attached_coturn_evidence_drain
    calls = 0
    canonical: AttachedCoturnEvidenceDrain | None = None

    def lost_return(**kwargs: object) -> object:
        nonlocal calls, canonical
        calls += 1
        candidate = original(**kwargs)  # type: ignore[arg-type]
        assert type(candidate) is AttachedCoturnEvidenceDrain
        if canonical is None:
            canonical = candidate
        assert candidate is canonical
        _raise_loss(kind)

    monkeypatch.setattr(
        username_module,
        "new_attached_coturn_evidence_drain",
        lost_return,
    )
    sink = username_module._new_username_sink(owner)
    expected = {
        "ordinary": RelayProbeOwnerError,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        sink._accept_relay_turn_username(USERNAME, _AdoptionDestination())
    sink._clear()
    assert calls == username_module._RECOVERY_ATTEMPTS + 1
    assert owner._read("drain", AttachedCoturnEvidenceDrain) is None
    assert canonical is not None
    owner._remember_exception(captured.value)
    if kind == "ordinary":
        cleanup_relay_probe(owner)
    else:
        with pytest.raises(KeyboardInterrupt if kind == "keyboard" else SystemExit) as control:
            cleanup_relay_probe(owner)
        assert not hasattr(control.value, "cleanup_authority")
    assert canonical._state == "cleaned"
    assert process._pump_claim.state == "terminal"
    assert pump._process is None
    assert process_claims.active_pump_count() == pump_baseline
    assert drain_registry.canonical_drain_count() == drain_baseline
    assert destination._record is None  # type: ignore[attr-defined]
    assert events == ["settle-runner"]


@pytest.mark.parametrize("boundary", ["owned", "released", "legacy-clear"])
@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_partial_drain_terminal_snapshot_resumes_through_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    kind: str,
) -> None:
    pump_baseline = process_claims.active_pump_count()
    drain_baseline = drain_registry.canonical_drain_count()
    owner_baseline = drain_registry.retained_owner_count()
    owner, destination, process, events = _owner_with_process(
        tmp_path,
        monkeypatch,
        started=False,
    )
    pump = create_attached_coturn_evidence_pump(
        process=process,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    drain = new_attached_coturn_evidence_drain(
        process=process,
        pump=pump,
        absolute_deadline=owner._absolute_deadline,
        clock=owner._clock,
    )
    owner._publish("pump", pump, AttachedCoturnEvidencePump)
    owner._publish("drain", drain, AttachedCoturnEvidenceDrain)
    owner._cleanup_phase = "drain"
    original_release = drain_recovery.release_drain_claim
    original_clear = drain_terminal._clear_one_terminal_resource
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        _raise_loss(kind)

    if boundary == "owned":

        def blocked_release(_drain: object) -> object:
            fail()

        monkeypatch.setattr(drain_recovery, "release_drain_claim", blocked_release)
    else:

        def blocked_clear(candidate: object, transition: object, target: str) -> object:
            if boundary == "released" or candidate._process is None:  # type: ignore[attr-defined]
                fail()
            return original_clear(candidate, transition, target)

        monkeypatch.setattr(drain_terminal, "_clear_one_terminal_resource", blocked_clear)
    expected = {
        "ordinary": RelayProbeCleanupRequired,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        cleanup_relay_probe(owner)
    aggregate = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert calls > 8
    assert drain._state == "terminalizing-cleaned"
    transition = drain._terminal_transition
    assert type(transition) is drain_terminal.DrainTerminalTransition
    assert transition.phase == ("owned" if boundary == "owned" else "released")
    if boundary == "legacy-clear":
        assert drain._process is None
        assert transition.process is process
    if kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    monkeypatch.setattr(drain_recovery, "release_drain_claim", original_release)
    monkeypatch.setattr(drain_terminal, "_clear_one_terminal_resource", original_clear)
    cleanup_relay_probe(aggregate)
    assert drain._state == "cleaned"
    assert drain._terminal_transition is None
    assert process._pump_claim.state == "terminal"
    assert pump._process is None
    assert pump._parser is None
    assert process_claims.active_pump_count() == pump_baseline
    assert drain_registry.canonical_drain_count() == drain_baseline
    assert drain_registry.retained_owner_count() == owner_baseline
    assert destination._record is None  # type: ignore[attr-defined]
    assert events == ["settle-runner"]


def test_friend_recovery_rejects_crosswire_partial_and_corrupt_claims(
    tmp_path: Path,
) -> None:
    first = _process(tmp_path / "first", FakeAttached())
    second = _process(tmp_path / "second", FakeAttached())
    first_pump = create_attached_coturn_evidence_pump(
        process=first,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    second_pump = create_attached_coturn_evidence_pump(
        process=second,
        expected_username=USERNAME,
        expected_topology=TOPOLOGY,
    )
    with pytest.raises(TypeError, match="evidence recovery failed"):
        evidence_module._recover_canonical_pump(first, second_pump)

    corrupt = new_attached_coturn_process(_validated(tmp_path / "corrupt"))
    corrupt._pump_claim = PumpClaim(
        state="building",
        key=object(),
        owner=corrupt._pump_owner,
    )
    with pytest.raises(TypeError, match="evidence recovery failed"):
        evidence_module._recover_canonical_pump(corrupt, None)
    corrupt._pump_claim = PumpClaim()

    def clock() -> float:
        return 10.0

    drain = new_attached_coturn_evidence_drain(
        process=first,
        pump=first_pump,
        absolute_deadline=11.0,
        clock=clock,
    )
    assert (
        evidence_module._recover_canonical_drain(
            first,
            first_pump,
            None,
            absolute_deadline=11.0,
            clock=clock,
        )
        is drain
    )
    with pytest.raises(TypeError, match="evidence recovery failed"):
        evidence_module._recover_canonical_drain(
            second,
            first_pump,
            None,
            absolute_deadline=11.0,
            clock=clock,
        )
    with pytest.raises(TypeError, match="evidence recovery failed"):
        evidence_module._recover_canonical_drain(
            first,
            first_pump,
            _object(AttachedCoturnEvidenceDrain),
            absolute_deadline=11.0,
            clock=clock,
        )
    cleanup_attached_coturn_evidence_drain(drain)
    assert evidence_module._recover_canonical_pump(first, first_pump) is first_pump
    assert first_pump._process is None
    assert second_pump._abort() == (False, None)
    second.terminate()

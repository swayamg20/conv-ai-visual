"""Return-loss and terminal-publication recovery tests for the relay owner."""
# ruff: noqa: E402

from __future__ import annotations

import copy
import os
import pickle
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_owner as owner_module
import scripts.voice_pipecat_e2e_relay_owner_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_owner_state as state_module
import scripts.voice_pipecat_e2e_relay_owner_username as username_module
from scripts.voice_pipecat_e2e_coturn_docker_network import NetworkPlan
from scripts.voice_pipecat_e2e_coturn_host import RuntimeIdentity
from scripts.voice_pipecat_e2e_coturn_runtime_drain import AttachedCoturnEvidenceDrain
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import AttachedCoturnEvidencePump
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_relay_browser_result import RelayBrowserResultOwner
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationOwner,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_owner import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeOwner,
    RelayProbeOwnerError,
    cleanup_relay_probe,
    new_relay_probe_owner,
    new_relay_probe_owner_destination,
    run_relay_probe,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import NONCE, TOPOLOGY
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools
from tests.test_voice_pipecat_e2e_relay_owner import (
    DEADLINE,
    NOW,
    SECRET,
    _BridgeProbe,
    _install_synthetic_lifecycle,
    _object,
    _Runner,
)
from tests.test_voice_pipecat_e2e_relay_probe import _source


def _factory_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    clock: Callable[[], float] | None = None,
) -> tuple[object, dict[str, object]]:
    paths = _paths(tmp_path)
    destination = new_relay_probe_owner_destination()
    observed_clock = clock or (lambda: NOW)

    def wait(_seconds: float) -> None:
        return None

    values: dict[str, object] = {
        "destination": destination,
        "paths": paths,
        "identity": RuntimeIdentity.create(run_id=paths.contract.run_id, owner_nonce=NONCE),
        "source": _source(monkeypatch),
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "invocation_tools": _object(RelayInvocationTools),
        "absolute_deadline": DEADLINE,
        "clock": observed_clock,
        "wait": wait,
    }
    return destination, values


def _assert_control_handoff(
    captured: BaseException,
    destination: object,
    values: dict[str, object],
    events: list[str],
) -> None:
    authority = captured.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    retained = state_module._resolve_owner(authority)
    assert retained is not None
    assert retained._cleanup_only
    with pytest.raises(RelayProbeCleanupRequired) as retry:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert retry.value.cleanup_authority is authority
    assert events == []
    cleanup_relay_probe(authority)
    assert events == ["settle-runner"]
    assert destination._record is None  # type: ignore[attr-defined]
    assert state_module._resolve_owner(authority) is None
    cleanup_relay_probe(authority)


@pytest.mark.parametrize("boundary", ["owner-factory", "budget-factory", "budget-slot"])
def test_factory_post_return_control_recovers_one_cleanup_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    fired = False

    if boundary == "owner-factory":
        original = owner_module._new_owner

        def lost_owner(**kwargs: object) -> object:
            nonlocal fired
            result = original(**kwargs)  # type: ignore[arg-type]
            if not fired:
                fired = True
                raise KeyboardInterrupt("raw-owner-return")
            return result

        monkeypatch.setattr(owner_module, "_new_owner", lost_owner)
    elif boundary == "budget-factory":
        original = owner_module.create_runtime_readiness_budget

        def lost_budget(**kwargs: object) -> object:
            nonlocal fired
            result = original(**kwargs)  # type: ignore[arg-type]
            if not fired:
                fired = True
                raise KeyboardInterrupt("raw-budget-return")
            return result

        monkeypatch.setattr(owner_module, "create_runtime_readiness_budget", lost_budget)
    else:
        original = state_module._RelaySlot.publish

        def lost_slot(self: object, value: object, expected: type[object]) -> object:
            nonlocal fired
            result = original(self, value, expected)  # type: ignore[arg-type]
            if not fired:
                fired = True
                raise KeyboardInterrupt("raw-slot-return")
            return result

        monkeypatch.setattr(state_module._RelaySlot, "publish", lost_slot)

    with pytest.raises(KeyboardInterrupt) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert fired
    assert str(captured.value) == ""
    _assert_control_handoff(captured.value, destination, values, events)


def test_retained_destination_read_return_loss_cannot_restore_runnable_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    retained = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    original = type(destination)._read
    fired = False

    def lost_read(self: object, binding: tuple[object, ...]) -> object:
        nonlocal fired
        result = original(self, binding)  # type: ignore[arg-type]
        if not fired:
            fired = True
            raise KeyboardInterrupt("raw-destination-return")
        return result

    monkeypatch.setattr(type(destination), "_read", lost_read)
    with pytest.raises(KeyboardInterrupt) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert state_module._resolve_owner(captured.value.cleanup_authority) is retained  # type: ignore[attr-defined]
    _assert_control_handoff(captured.value, destination, values, events)


@pytest.mark.parametrize("boundary", ["existing-read", "owner-return"])
def test_factory_recovers_registered_owner_without_destination_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    retained: RelayProbeOwner | None = None
    hostile = boundary == "existing-read"
    if boundary == "existing-read":
        retained = new_relay_probe_owner(**values)  # type: ignore[arg-type]

    original_read = type(destination)._read

    def blocked_read(self: object, binding: tuple[object, ...]) -> object:
        if hostile:
            raise KeyboardInterrupt("raw-repeated-destination-read")
        return original_read(self, binding)  # type: ignore[arg-type]

    monkeypatch.setattr(type(destination), "_read", blocked_read)
    if boundary == "owner-return":
        original_new = owner_module._new_owner

        def lost_owner(**kwargs: object) -> object:
            nonlocal hostile, retained
            retained = original_new(**kwargs)  # type: ignore[arg-type]
            hostile = True
            raise KeyboardInterrupt("raw-owner-return")

        monkeypatch.setattr(owner_module, "_new_owner", lost_owner)

    with pytest.raises(KeyboardInterrupt) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert str(captured.value) == ""
    assert state_module._resolve_owner(authority) is retained
    assert retained is not None and retained._cleanup_only
    hostile = False
    with pytest.raises(RelayProbeCleanupRequired) as retry:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert retry.value.cleanup_authority is authority
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]


def test_terminal_transition_rejects_copy_and_serialization() -> None:
    transition = state_module._RelayTerminalTransition(
        phase="scrubbed",
        publish=False,
        run=None,
        facts_valid=True,
    )
    for operation in (
        lambda: copy.copy(transition),
        lambda: copy.deepcopy(transition),
        lambda: pickle.dumps(transition),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()


def test_factory_poison_targets_current_destination_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_events: list[str] = []
    first_root = tmp_path / "first"
    first_root.mkdir()
    destination, first_values = _factory_context(first_root, monkeypatch, first_events)
    first = new_relay_probe_owner(**first_values)  # type: ignore[arg-type]
    assert owner_module._bounded_cleanup(first, publish=False)
    assert first._state == "cleaned"
    first_cleanup_only = first._cleanup_only
    assert destination._record is None  # type: ignore[attr-defined]

    second_events: list[str] = []
    second_root = tmp_path / "second"
    second_root.mkdir()
    _unused, second_values = _factory_context(second_root, monkeypatch, second_events)
    second_values["destination"] = destination
    second_paths = second_values["paths"]
    second_values["identity"] = RuntimeIdentity.create(
        run_id=second_paths.contract.run_id,  # type: ignore[union-attr]
        owner_nonce="cd" * 32,
    )
    second = new_relay_probe_owner(**second_values)  # type: ignore[arg-type]
    monkeypatch.setattr(
        type(destination),
        "_read",
        lambda _self, _binding: (_ for _ in ()).throw(
            KeyboardInterrupt("raw-current-generation-read")
        ),
    )
    with pytest.raises(KeyboardInterrupt) as captured:
        new_relay_probe_owner(**second_values)  # type: ignore[arg-type]
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert state_module._resolve_owner(authority) is second
    assert second._cleanup_only
    assert first._state == "cleaned"
    assert first._cleanup_only is first_cleanup_only
    cleanup_relay_probe(authority)
    cleanup_relay_probe(first)


@pytest.mark.parametrize(
    ("kind", "code"),
    [("keyboard", None), ("system-exit", 73)],
)
def test_clock_control_is_preserved_with_aggregate_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    code: int | None,
) -> None:
    events: list[str] = []

    def clock() -> float:
        if kind == "keyboard":
            raise KeyboardInterrupt("raw-clock")
        raise SystemExit(code)

    destination, values = _factory_context(
        tmp_path,
        monkeypatch,
        events,
        clock=clock,
    )
    expected = KeyboardInterrupt if kind == "keyboard" else SystemExit
    with pytest.raises(expected) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    if kind == "keyboard":
        assert str(captured.value) == ""
    else:
        assert captured.value.code == code  # type: ignore[attr-defined]
    _assert_control_handoff(captured.value, destination, values, events)


def test_ordinary_budget_failure_is_irreversible_cleanup_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    monkeypatch.setattr(
        owner_module,
        "create_runtime_readiness_budget",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("raw-budget-failure")),
    )
    with pytest.raises(RelayProbeCleanupRequired) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert str(captured.value) == "Relay probe cleanup requires retry"
    authority = captured.value.cleanup_authority
    with pytest.raises(RelayProbeCleanupRequired) as retry:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert retry.value.cleanup_authority is authority
    assert events == []
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]
    assert events == ["settle-runner"]


def test_source_revalidation_failure_publishes_nothing_and_cleans_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    calls = 0

    def changed_source(_run: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("raw-source-changed")

    monkeypatch.setattr(state_module, "revalidate_relay_probe_source", changed_source)
    with pytest.raises(RelayProbeOwnerError) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=SECRET,
            now=__import__("datetime").datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    assert str(captured.value) == "Relay probe execution failed"
    assert "raw-source-changed" not in repr(captured.value)
    assert calls == 1
    assert destination._record is None  # type: ignore[attr-defined]
    assert owner._state == "cleaned"
    assert owner._observation is None
    assert owner._terminal_roots_empty()
    cleanup_relay_probe(owner)


def test_cleanup_reconciles_canonical_invocation_and_artifact_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    invocation = _object(RelayInvocationOwner)
    artifact = _object(RelayBrowserResultOwner)
    invocation_destination = owner._invocation_destination
    invocation_destination._publish_owner(  # type: ignore[attr-defined]
        owner._run,
        owner._invocation_driver,
        owner._invocation_tools,
        invocation,
    )
    invocation_destination._publish_ready(invocation)  # type: ignore[attr-defined]
    object.__setattr__(owner._run, "_browser_artifact_owner", artifact)

    def cleanup_invocation(candidate: object) -> None:
        assert candidate is invocation
        assert invocation_destination._clear(candidate)  # type: ignore[attr-defined]
        events.append("cleanup-invocation")

    def cleanup_artifact(run: object) -> None:
        assert run is owner._run
        object.__setattr__(run, "_browser_artifact_owner", None)
        events.append("cleanup-artifact")

    monkeypatch.setattr(cleanup_module, "cleanup_relay_invocation", cleanup_invocation)
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_relay_browser_result_owner",
        cleanup_artifact,
    )
    cleanup_relay_probe(owner)
    assert events == ["cleanup-invocation", "cleanup-artifact", "settle-runner"]
    assert destination._record is None  # type: ignore[attr-defined]
    assert owner._terminal_roots_empty()


def test_terminal_destination_can_be_reused_without_retaining_prior_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_events: list[str] = []
    destination, first = _factory_context(tmp_path, monkeypatch, first_events)
    first_owner = new_relay_probe_owner(**first)  # type: ignore[arg-type]
    cleanup_relay_probe(first_owner)
    assert destination._record is None  # type: ignore[attr-defined]

    second_events: list[str] = []
    second_root = tmp_path / "second"
    second_root.mkdir()
    _unused, second = _factory_context(second_root, monkeypatch, second_events)
    second["destination"] = destination
    second_owner = new_relay_probe_owner(**second)  # type: ignore[arg-type]
    assert second_owner is not first_owner
    cleanup_relay_probe(second_owner)
    assert destination._record is None  # type: ignore[attr-defined]
    assert first_events == ["settle-runner"]
    assert second_events == ["settle-runner"]
    assert os.fspath(tmp_path) not in repr(destination)


@pytest.mark.parametrize("position", ["entry", "before-store", "after-store", "return"])
@pytest.mark.parametrize("earlier_control", [False, True])
def test_registry_release_control_is_reconciled_and_preserves_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: str,
    earlier_control: bool,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = owner._cleanup_authority
    if earlier_control:
        owner._remember_control((SystemExit, 41))
    fired = False

    def release_cut(observed: str) -> None:
        nonlocal fired
        if not fired and observed == position:
            fired = True
            raise SystemExit(73)

    monkeypatch.setattr(state_module, "_registry_release_hook", release_cut)
    with pytest.raises(SystemExit) as captured:
        cleanup_relay_probe(owner)
    assert fired
    assert captured.value.code == (41 if earlier_control else 73)
    assert not hasattr(captured.value, "cleanup_authority")
    assert state_module._resolve_owner(authority) is None
    assert destination._record is None  # type: ignore[attr-defined]
    cleanup_relay_probe(owner)


def test_repeated_registry_release_control_retains_exact_retry_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    calls = 0

    def release_cut(position: str) -> None:
        nonlocal calls
        if position == "entry":
            calls += 1
            raise KeyboardInterrupt("raw-release")

    monkeypatch.setattr(state_module, "_registry_release_hook", release_cut)
    with pytest.raises(KeyboardInterrupt) as captured:
        cleanup_relay_probe(owner)
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    assert calls == owner_module._MAX_RELEASE_ATTEMPTS
    assert destination._record is None  # type: ignore[attr-defined]
    assert state_module._resolve_owner(authority) is owner
    monkeypatch.setattr(state_module, "_registry_release_hook", lambda _position: None)
    cleanup_relay_probe(authority)
    assert state_module._resolve_owner(authority) is None


@pytest.mark.parametrize("position", ["entry", "before-store"])
def test_observation_waits_for_confirmed_registry_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = owner._cleanup_authority
    calls = 0

    def release_failure(observed: str) -> None:
        nonlocal calls
        if observed == position:
            calls += 1
            raise RuntimeError("raw-release-failure")

    monkeypatch.setattr(state_module, "_registry_release_hook", release_failure)
    with pytest.raises(RelayProbeCleanupRequired) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=SECRET,
            now=__import__("datetime").datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    assert captured.value.cleanup_authority is authority
    assert "raw-release-failure" not in repr(captured.value)
    assert calls == 1
    assert owner._state == "observed"
    observation = owner._observation
    assert observation is not None
    assert destination._record is None  # type: ignore[attr-defined]
    assert state_module._resolve_owner(authority) is owner
    prior_events = tuple(events)

    monkeypatch.setattr(state_module, "_registry_release_hook", lambda _position: None)
    retried = run_relay_probe(
        owner,
        static_auth_secret=SECRET,
        now=__import__("datetime").datetime(2026, 8, 19),
        browser_timeout_seconds=5.0,
    )
    assert retried is observation
    assert tuple(events) == prior_events
    assert state_module._resolve_owner(authority) is None


@pytest.mark.parametrize("boundary", ["pump-return", "drain-return"])
def test_username_adoption_recovers_canonical_evidence_owner_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    events: list[str] = []
    _destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    plan = NetworkPlan(identity=owner._identity, paths=owner._paths, topology=TOPOLOGY)
    process = _object(AttachedCoturnProcess)
    pump = _object(AttachedCoturnEvidencePump)
    drain = _object(AttachedCoturnEvidenceDrain)
    owner._publish("network_plan", plan, NetworkPlan)
    owner._publish("process", process, AttachedCoturnProcess)
    pump_calls = 0
    drain_calls = 0

    def create_pump(**_kwargs: object) -> object:
        nonlocal pump_calls
        pump_calls += 1
        if boundary == "pump-return" and pump_calls == 1:
            raise KeyboardInterrupt("raw-pump-return")
        return pump

    def create_drain(**_kwargs: object) -> object:
        nonlocal drain_calls
        drain_calls += 1
        if boundary == "drain-return" and drain_calls == 1:
            raise KeyboardInterrupt("raw-drain-return")
        return drain

    class AdoptionDestination:
        published = False

        def publish(self, value: object) -> None:
            assert value is True
            self.published = True

    monkeypatch.setattr(
        username_module,
        "create_attached_coturn_evidence_pump",
        create_pump,
    )
    monkeypatch.setattr(
        username_module,
        "new_attached_coturn_evidence_drain",
        create_drain,
    )
    sink = username_module._new_username_sink(owner)
    adoption_destination = AdoptionDestination()
    with pytest.raises(KeyboardInterrupt) as captured:
        sink._accept_relay_turn_username(
            "1786982460:123e4567-e89b-42d3-a456-426614174000",
            adoption_destination,
        )
    sink._clear()
    assert str(captured.value) == ""
    assert owner._read("pump", AttachedCoturnEvidencePump) is pump
    assert owner._read("drain", AttachedCoturnEvidenceDrain) is drain
    assert pump_calls == (2 if boundary == "pump-return" else 1)
    assert drain_calls == (2 if boundary == "drain-return" else 1)
    assert adoption_destination.published is False
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_attached_coturn_evidence_drain",
        lambda candidate: events.append("cleanup-drain") if candidate is drain else None,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_pump",
        lambda _process, retained: retained,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_drain",
        lambda _process, _pump, retained, **_kwargs: retained,
    )
    cleanup_relay_probe(owner)
    assert events == ["cleanup-drain", "settle-runner"]


@pytest.mark.parametrize("resource", ["container", "network"])
@pytest.mark.parametrize("failure", ["ordinary", "keyboard", "system-exit"])
def test_cleanup_postcondition_probe_is_total_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    failure: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    if resource == "container":
        from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerPlan

        plan = _object(ContainerPlan, paths=owner._paths)
        owner._publish("container_plan", plan, ContainerPlan)
        owner._cleanup_phase = "remove-container"
        target = "_container_recovery_exists"
    else:
        plan = _object(NetworkPlan, paths=owner._paths)
        owner._publish("network_plan", plan, NetworkPlan)
        owner._cleanup_phase = "remove-network"
        target = "_network_recovery_exists"
    fired = False

    def fail_once(_plan: object) -> bool:
        nonlocal fired
        if not fired:
            fired = True
            if failure == "ordinary":
                raise RuntimeError("raw-postcondition-path")
            if failure == "keyboard":
                raise KeyboardInterrupt("raw-postcondition-path")
            raise SystemExit(73)
        return False

    monkeypatch.setattr(cleanup_module, target, fail_once)
    if failure == "ordinary":
        with pytest.raises(RelayProbeCleanupRequired) as captured:
            cleanup_relay_probe(owner)
        authority = captured.value.cleanup_authority
        assert "raw-postcondition-path" not in repr(captured.value)
    else:
        expected = KeyboardInterrupt if failure == "keyboard" else SystemExit
        with pytest.raises(expected) as captured:
            cleanup_relay_probe(owner)
        authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
        if failure == "keyboard":
            assert str(captured.value) == ""
        else:
            assert captured.value.code == 73  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]
    assert state_module._resolve_owner(authority) is None


@pytest.mark.parametrize("kind", ["owner-cleanup", "owner-run", "authority"])
def test_forged_exact_public_roots_fail_closed(
    kind: str,
) -> None:
    if kind == "authority":
        forged: object = object.__new__(RelayProbeCleanupAuthority)
        operation = lambda: cleanup_relay_probe(forged)  # noqa: E731
    else:
        forged = object.__new__(RelayProbeOwner)
        if kind == "owner-cleanup":
            operation = lambda: cleanup_relay_probe(forged)  # noqa: E731
        else:
            operation = lambda: run_relay_probe(  # noqa: E731
                forged,  # type: ignore[arg-type]
                static_auth_secret="raw-secret",
                now=__import__("datetime").datetime(2026, 8, 19),
                browser_timeout_seconds=5.0,
            )
    with pytest.raises(RelayProbeOwnerError) as captured:
        operation()
    assert "AttributeError" not in repr(captured.value)
    assert "raw-secret" not in repr(captured.value)


def test_two_run_callers_join_without_downgrading_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    _destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    reached_cleanup = threading.Event()
    resume_cleanup = threading.Event()
    original = owner_module._bounded_cleanup
    paused = False

    def bounded(candidate: RelayProbeOwner, *, publish: bool) -> bool:
        nonlocal paused
        if publish and not paused:
            paused = True
            reached_cleanup.set()
            assert resume_cleanup.wait(2.0)
        return original(candidate, publish=publish)

    monkeypatch.setattr(owner_module, "_bounded_cleanup", bounded)
    results: list[object] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            results.append(
                run_relay_probe(
                    owner,
                    static_auth_secret=SECRET,
                    now=__import__("datetime").datetime(2026, 8, 19),
                    browser_timeout_seconds=5.0,
                )
            )
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert reached_cleanup.wait(2.0)
    second.start()
    resume_cleanup.set()
    first.join(2.0)
    second.join(2.0)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert len(results) == 2
    assert results[0] is results[1]
    assert results[0].status == "probe-observed"  # type: ignore[attr-defined]
    assert events.count("prepare") == 1
    assert events.count("revalidate-source") == 1

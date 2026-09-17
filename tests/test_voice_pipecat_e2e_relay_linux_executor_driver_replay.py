"""Replay and control-path tests for the private full-lifecycle driver."""
# ruff: noqa: E402

from __future__ import annotations

import gc
import sys
import threading
import weakref
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_executor_build_contract as executor_contract
import scripts.voice_pipecat_e2e_relay_linux_executor_driver as driver_module
import scripts.voice_pipecat_e2e_relay_linux_executor_driver_cleanup as driver_cleanup
import scripts.voice_pipecat_e2e_relay_linux_executor_driver_state as driver_state
import scripts.voice_pipecat_e2e_relay_linux_executor_inner_state as executor_inner_state
import scripts.voice_pipecat_e2e_relay_owner_state as relay_owner_state
from tests import test_voice_pipecat_e2e_relay_linux_executor_driver as support
from tests.relay_linux_executor_driver_assertions import (
    assert_sanitized_driver_failure,
)


@pytest.fixture(autouse=True)
def _isolated_driver_state() -> None:
    mappings = support._driver_state_mappings()
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


def _successful_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    observation = driver_module._run_preowned_relay_linux_executor(**arguments)
    support._assert_total_absence(executor, destination, key)
    return executor, destination, key, arguments, observation, events


def test_terminal_replay_rejects_changed_driver_timeouts_without_new_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, key, arguments, _observation, events = _successful_terminal(
        tmp_path,
        monkeypatch,
    )
    terminal_events = tuple(events)

    for name in (
        "start_timeout_seconds",
        "build_timeout_seconds",
        "cleanup_timeout_seconds",
    ):
        changed = dict(arguments)
        changed[name] = arguments[name] + 0.5
        with pytest.raises(RuntimeError):
            driver_module._run_preowned_relay_linux_executor(**changed)
        assert tuple(events) == terminal_events
        support._assert_total_absence(executor, destination, key)


def test_same_key_overlap_rechecks_terminal_inside_the_shared_operation_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    real_terminal = driver_module._terminal_binding_for_attempt
    overlap = threading.Barrier(2)
    first_inside_lock = threading.Event()
    results: dict[str, object] = {}
    failures: list[BaseException] = []

    def resolve_with_overlap(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        if threading.current_thread().name == "same-key-B":
            overlap.wait(timeout=5.0)
        return attempt

    def terminal_with_overlap(attempt: object) -> object:
        if threading.current_thread().name == "same-key-A":
            first_inside_lock.set()
            overlap.wait(timeout=5.0)
        return real_terminal(attempt)

    def invoke(label: str) -> None:
        try:
            results[label] = driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        resolve_with_overlap,
    )
    monkeypatch.setattr(
        driver_module,
        "_terminal_binding_for_attempt",
        terminal_with_overlap,
    )
    first = threading.Thread(target=invoke, args=("A",), name="same-key-A")
    second = threading.Thread(target=invoke, args=("B",), name="same-key-B")
    first.start()
    assert first_inside_lock.wait(timeout=5.0)
    second.start()
    first.join(timeout=10.0)
    second.join(timeout=10.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not failures
    assert set(results) == {"A", "B"}
    assert results["A"] is results["B"]
    for event in (
        "process-absent",
        "prepare",
        "create-network",
        "start-browser",
        "revalidate-source",
        "first-fs-delete",
    ):
        assert events.count(event) == 1
    support._assert_total_absence(executor, destination, key)


def test_terminal_probe_miss_replays_when_winner_finishes_before_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    real_terminal_probe = driver_module._terminal_binding_for_driver_call
    second_missed_terminal = threading.Event()
    winner_finished = threading.Event()
    second_probe_count = 0
    results: dict[str, object] = {}
    failures: list[BaseException] = []

    def terminal_probe(**kwargs: object) -> object:
        nonlocal second_probe_count
        if threading.current_thread().name == "probe-miss-B":
            second_probe_count += 1
            if second_probe_count == 1:
                second_missed_terminal.set()
                assert winner_finished.wait(timeout=10.0)
                return None
        return real_terminal_probe(**kwargs)

    def invoke(label: str) -> None:
        try:
            results[label] = driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(
        driver_module,
        "_terminal_binding_for_driver_call",
        terminal_probe,
    )
    second = threading.Thread(target=invoke, args=("B",), name="probe-miss-B")
    winner = threading.Thread(target=invoke, args=("A",), name="probe-winner-A")
    second.start()
    assert second_missed_terminal.wait(timeout=5.0)
    winner.start()
    winner.join(timeout=10.0)
    winner_finished.set()
    second.join(timeout=10.0)

    assert not winner.is_alive()
    assert not second.is_alive()
    assert not failures
    assert set(results) == {"A", "B"}
    assert results["A"] is results["B"]
    assert second_probe_count == 2
    for event in (
        "process-absent",
        "prepare",
        "create-network",
        "start-browser",
        "revalidate-source",
        "first-fs-delete",
    ):
        assert events.count(event) == 1
    support._assert_total_absence(executor, destination, key)


def test_initial_driver_record_store_failure_is_pre_effect_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    real_store = driver_state._store_driver_record
    failure = OSError("synthetic initial driver-record store failure")
    stores = 0

    def fail_initial_store(record_key: object, record: object) -> None:
        nonlocal stores
        stores += 1
        real_store(record_key, record)
        if stores == 1:
            raise failure

    monkeypatch.setattr(driver_state, "_store_driver_record", fail_initial_store)
    with pytest.raises(RuntimeError) as captured:
        driver_module._run_preowned_relay_linux_executor(**arguments)

    assert_sanitized_driver_failure(captured.value, failure)
    assert stores == 1
    assert not events
    assert not driver_state._DRIVER_RECORDS
    assert not support.executor_state._EXECUTORS
    assert not support.executor_state._PORT_RESERVATIONS
    assert not support.worker_registry._RECORDS
    assert not executor._workspace_owner._request._run_root.exists()

    observation = driver_module._run_preowned_relay_linux_executor(**arguments)
    assert type(observation) is support.RelayProbeObservation
    assert stores > 1
    support._assert_total_absence(executor, destination, key)


def test_retired_failure_attempt_is_collectable_after_last_waiter_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    arguments = support._driver_arguments(executor, destination, events)
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    attempt_refs: list[weakref.ReferenceType[object]] = []
    raised: list[OSError] = []
    secret_refs: list[weakref.ReferenceType[object]] = []

    def capture_attempt(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        attempt_refs.append(weakref.ref(attempt))
        return attempt

    def fail_preown(_destination: object) -> object:
        class SecretCarrier:
            pass

        secret = SecretCarrier()
        secret.value = support._SECRET
        error = OSError(support._SECRET)
        error.secret_payload = secret  # type: ignore[attr-defined]
        error.add_note(support._SECRET)
        secret_refs.append(weakref.ref(secret))
        raised.append(error)
        raise error

    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        capture_attempt,
    )
    monkeypatch.setattr(driver_module, "_preown_relay_linux_executor", fail_preown)
    with pytest.raises(RuntimeError, match="Relay Linux executor driver failed") as captured:
        driver_module._run_preowned_relay_linux_executor(**arguments)
    assert_sanitized_driver_failure(captured.value, raised[0], support._SECRET)
    attempt_ref = attempt_refs.pop()
    secret_ref = secret_refs.pop()
    raised.clear()
    del arguments, executor, destination
    assert not driver_state._DRIVER_RECORDS
    assert key not in support.executor_state._RETIRED_KEYS
    for _attempt in range(5):
        gc.collect()
    assert attempt_ref() is None
    assert secret_ref() is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError(support._SECRET),
        KeyboardInterrupt(support._SECRET),
        SystemExit(support._SECRET),
    ],
)
def test_preown_no_effect_failure_rethrows_exactly_without_secret_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    failure.secret_payload = support._SECRET  # type: ignore[attr-defined]
    failure.add_note(support._SECRET)
    failure.__cause__ = RuntimeError(support._SECRET)
    failure.__context__ = RuntimeError(support._SECRET)

    def fail_preown(_destination: object) -> object:
        raise failure

    monkeypatch.setattr(
        driver_module,
        "_preown_relay_linux_executor",
        fail_preown,
    )
    with pytest.raises((RuntimeError, KeyboardInterrupt, SystemExit)) as captured:
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert_sanitized_driver_failure(captured.value, failure, support._SECRET)
    assert not events
    assert not driver_state._DRIVER_RECORDS
    assert key not in support.executor_state._RETIRED_KEYS
    assert not support.executor_state._EXECUTORS
    assert not support.executor_state._PORT_RESERVATIONS
    assert not executor._workspace_owner._request._run_root.exists()


def test_foreign_capacity_failure_abandons_only_the_callers_driver_intent(
    tmp_path: Path,
) -> None:
    foreign, foreign_destination, foreign_key = support._driver_graph(
        tmp_path,
        sequence=1,
    )
    executor, destination, key = support._driver_graph(tmp_path, sequence=2)
    assert support.executor_state._preown_relay_linux_executor(foreign_destination) is foreign
    foreign_record = support.executor_state._EXECUTORS[foreign_key]
    events: list[str] = []

    with pytest.raises(
        support.executor_state._RelayLinuxExecutorError,
        match="Relay Linux executor driver failed",
    ):
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert not events
    assert not driver_state._DRIVER_RECORDS
    assert key not in support.executor_state._RETIRED_KEYS
    assert support.executor_state._EXECUTORS == {foreign_key: foreign_record}
    assert support.executor_state._EXECUTORS[foreign_key] is foreign_record
    assert support.executor_state._PORT_RESERVATIONS == {
        support.executor_state._FIXED_PORTS: foreign_key
    }
    assert not foreign._workspace_owner._request._run_root.exists()
    assert not executor._workspace_owner._request._run_root.exists()
    assert support.executor_state._release_unstarted_relay_linux_executor(
        foreign._cleanup_authority
    )
    assert not support.executor_state._EXECUTORS
    assert not support.executor_state._PORT_RESERVATIONS


@pytest.mark.parametrize(
    "factory_name",
    [
        "_new_relay_linux_build_workspace_worker_bundle",
        "_new_relay_linux_build_workspace_worker_thread",
    ],
)
def test_persistent_no_effect_worker_factory_failure_releases_outer_and_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory_name: str,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    failure = OSError(f"synthetic persistent {factory_name} failure")
    factory_calls = 0

    def fail_factory(*_args: object, **_kwargs: object) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise failure

    monkeypatch.setattr(driver_module, factory_name, fail_factory)
    monkeypatch.setattr(driver_cleanup, factory_name, fail_factory)
    with pytest.raises(RuntimeError) as captured:
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert_sanitized_driver_failure(captured.value, failure)
    assert factory_calls == 1
    assert not events
    support._assert_total_absence(executor, destination, key)


def test_structurally_coherent_terminal_timeout_rewrite_cannot_authorize_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, key, arguments, _observation, events = _successful_terminal(
        tmp_path,
        monkeypatch,
    )
    terminal_events = tuple(events)
    original = driver_state._DRIVER_TERMINALS[key]
    changed = dict(arguments)
    changed["start_timeout_seconds"] = original[0] + 0.5
    changed["build_timeout_seconds"] = original[1] + 0.5
    changed["cleanup_timeout_seconds"] = original[2] + 0.5
    rewritten = (
        changed["start_timeout_seconds"],
        changed["build_timeout_seconds"],
        changed["cleanup_timeout_seconds"],
        original[3],
        original[4],
        original[5],
    )
    assert len(rewritten) == 6
    assert all(type(rewritten[index]) is float for index in range(5))
    assert rewritten[5] is original[5]
    assert not driver_state._driver_terminal_matches(
        rewritten,
        key,
        start_timeout_seconds=changed["start_timeout_seconds"],
        build_timeout_seconds=changed["build_timeout_seconds"],
        cleanup_timeout_seconds=changed["cleanup_timeout_seconds"],
    )
    driver_state._DRIVER_TERMINALS[key] = rewritten

    with pytest.raises(RuntimeError):
        driver_module._run_preowned_relay_linux_executor(**changed)

    assert tuple(events) == terminal_events
    assert not driver_state._DRIVER_RECORDS
    assert not driver_state._driver_terminal_state_is_capacity_neutral()
    assert support.executor_cleanup._final_outer_absence(executor, destination, key)


@pytest.mark.parametrize(
    "control",
    [
        KeyboardInterrupt("synthetic preconsume keyboard interrupt"),
        SystemExit(73),
    ],
)
def test_preconsume_cleanup_rethrows_the_exact_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    real_reconcile = driver_cleanup._reconcile_existing_consumption
    secondary = (
        SystemExit(91)
        if type(control) is KeyboardInterrupt
        else KeyboardInterrupt("synthetic later cleanup control")
    )
    cleanup_calls = 0

    def interrupt_consume(**_kwargs: object) -> object:
        events.append("consume-control")
        raise control

    def reconcile_after_later_control(*args: object, **kwargs: object) -> object:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise secondary
        return real_reconcile(*args, **kwargs)

    monkeypatch.setattr(
        driver_module,
        "_consume_relay_linux_executor_built_lease",
        interrupt_consume,
    )
    monkeypatch.setattr(
        driver_cleanup,
        "_reconcile_existing_consumption",
        reconcile_after_later_control,
    )
    with pytest.raises((KeyboardInterrupt, SystemExit)) as captured:
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert_sanitized_driver_failure(captured.value, control)
    assert events.count("consume-control") == 1
    assert cleanup_calls >= 2
    support._assert_total_absence(executor, destination, key)


def test_failure_snapshot_mutation_cannot_replace_the_latched_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    arguments = support._driver_arguments(executor, destination, events)
    first = KeyboardInterrupt("synthetic retained first control")
    replacement = SystemExit(92)
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    real_settle = driver_module._settle_failed_driver_attempt
    attempts: list[object] = []
    cleanup_paused = threading.Event()
    resume_cleanup = threading.Event()
    raised: list[BaseException] = []

    def capture_attempt(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        attempts.append(attempt)
        return attempt

    def interrupt_consume(**_kwargs: object) -> object:
        raise first

    def pause_cleanup(
        attempt: object,
        failures: list[BaseException | None],
        *,
        cleanup_deadline: float,
    ) -> bool:
        cleanup_paused.set()
        assert resume_cleanup.wait(timeout=10.0)
        return real_settle(
            attempt,
            failures,
            cleanup_deadline=cleanup_deadline,
        )

    def invoke() -> None:
        try:
            driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            raised.append(error)

    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        capture_attempt,
    )
    monkeypatch.setattr(
        driver_module,
        "_consume_relay_linux_executor_built_lease",
        interrupt_consume,
    )
    monkeypatch.setattr(driver_module, "_settle_failed_driver_attempt", pause_cleanup)
    worker = threading.Thread(target=invoke)
    worker.start()
    assert cleanup_paused.wait(timeout=10.0)
    try:
        holder = object.__getattribute__(attempts[0], "_failure_holder")
        with pytest.raises(AttributeError):
            object.__getattribute__(holder, "_values")
        state = object.__getattribute__(holder, "_state")
        assert type(state) is tuple and len(state) == 3
        assert state[0] is KeyboardInterrupt and state[1:] == (None, False)
        with pytest.raises(TypeError):
            state[0] = replacement
        with pytest.raises(AttributeError):
            object.__getattribute__(state, "__setstate__")
        leaked = attempts[0].failures
        assert_sanitized_driver_failure(leaked[0], first)
        leaked[0] = replacement
    finally:
        resume_cleanup.set()
    worker.join(timeout=10.0)

    assert not worker.is_alive()
    assert len(raised) == 1
    assert_sanitized_driver_failure(raised[0], first)
    support._assert_total_absence(executor, destination, key)


def test_pre_effect_consume_failure_never_enters_the_consumed_inner_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    failure = RuntimeError("synthetic pre-effect consume failure")
    real_settle_consumed = driver_cleanup._settle_consumed_without_inner_owner
    consumed_cleanup_calls = 0

    def fail_before_consume(**_kwargs: object) -> object:
        raise failure

    def observe_consumed_cleanup(*args: object, **kwargs: object) -> bool:
        nonlocal consumed_cleanup_calls
        consumed_cleanup_calls += 1
        return real_settle_consumed(*args, **kwargs)

    monkeypatch.setattr(
        driver_module,
        "_consume_relay_linux_executor_built_lease",
        fail_before_consume,
    )
    monkeypatch.setattr(
        driver_cleanup,
        "_settle_consumed_without_inner_owner",
        observe_consumed_cleanup,
    )
    with pytest.raises(RuntimeError) as captured:
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert_sanitized_driver_failure(captured.value, failure)
    assert consumed_cleanup_calls == 0
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_inner_state._INNER_RESULTS
    assert not executor_inner_state._INNER_TERMINALS
    assert not executor_inner_state._INNER_AUTHORITIES
    assert not relay_owner_state._REGISTRY
    assert "prepare" not in events
    support._assert_total_absence(executor, destination, key)


def test_consume_return_loss_reconciles_the_committed_effect_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    real_consume = driver_module._consume_relay_linux_executor_built_lease
    real_reconcile = driver_cleanup._reconcile_existing_consumption
    lost: list[object] = []
    classified: list[object] = []
    failure = RuntimeError("synthetic consume return loss")

    def consume_then_lose_return(**kwargs: object) -> object:
        binding = real_consume(**kwargs)
        evidence = executor_contract._evidence_for_binding(binding)
        assert evidence is not None
        assert executor_contract._consumed_binding_matches(evidence)
        lost.append(binding)
        raise failure

    def classify_committed_consume(*args: object, **kwargs: object) -> object:
        binding = real_reconcile(*args, **kwargs)
        classified.append(binding)
        return binding

    monkeypatch.setattr(
        driver_module,
        "_consume_relay_linux_executor_built_lease",
        consume_then_lose_return,
    )
    monkeypatch.setattr(
        driver_cleanup,
        "_reconcile_existing_consumption",
        classify_committed_consume,
    )
    with pytest.raises(RuntimeError) as captured:
        driver_module._run_preowned_relay_linux_executor(
            **support._driver_arguments(executor, destination, events)
        )

    assert_sanitized_driver_failure(captured.value, failure)
    assert len(lost) == len(classified) == 1
    assert classified[0] is lost[0]
    support._assert_total_absence(executor, destination, key)

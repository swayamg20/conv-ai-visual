"""Adversarial authority and cleanup cuts for the private executor driver."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_executor_driver as driver_module
import scripts.voice_pipecat_e2e_relay_linux_executor_driver_cleanup as driver_cleanup
import scripts.voice_pipecat_e2e_relay_linux_executor_driver_state as driver_state
from tests import relay_linux_executor_driver_assertions as driver_assertions
from tests import test_voice_pipecat_e2e_relay_linux_executor_driver as support


class _HostileSystemExit(SystemExit):
    @property
    def code(self) -> object:
        raise RuntimeError("hostile SystemExit code property")


@pytest.fixture(autouse=True)
def _isolated_driver_state() -> None:
    mappings = support._driver_state_mappings()
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


def test_dynamic_driver_record_phase_and_receipt_rewrite_is_rejected(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    arguments = support._driver_arguments(executor, destination, events)
    attempt = driver_state._resolve_or_intend_driver_attempt(**arguments)
    assert driver_state._driver_record(attempt) is driver_state._DRIVER_RECORDS[key]
    bundle = driver_module._new_relay_linux_build_workspace_worker_bundle(executor._workspace_owner)
    owner_token = executor._workspace_owner._cleanup_authority._key
    record_token = object()
    forged_receipt = support._object(
        driver_module._WorkspaceWorkerThreadReceipt,
        _owner_token=owner_token,
        _record_token=record_token,
        _coherent=True,
    )
    rewritten = (
        attempt,
        bundle,
        forged_receipt,
        None,
        None,
        None,
        "worker-created",
    )
    assert type(rewritten[1]) is driver_module._WorkspaceWorkerBundle
    assert type(rewritten[2]) is driver_module._WorkspaceWorkerThreadReceipt
    assert forged_receipt._matches(owner_token, record_token)
    driver_state._DRIVER_RECORDS[key] = rewritten

    assert driver_state._driver_record(attempt) is None


def test_orphan_driver_record_quarantines_the_active_attempt(tmp_path: Path) -> None:
    events: list[str] = []
    active_root = tmp_path / "active"
    foreign_root = tmp_path / "foreign"
    active_root.mkdir()
    foreign_root.mkdir()
    executor, destination, _key = support._driver_graph(active_root)
    arguments = support._driver_arguments(executor, destination, events)
    attempt = driver_state._resolve_or_intend_driver_attempt(**arguments)
    _foreign_executor, _foreign_destination, foreign_key = support._driver_graph(foreign_root)
    driver_state._DRIVER_RECORDS[foreign_key] = object()

    assert driver_state._driver_record(attempt) is None
    with pytest.raises(RuntimeError):
        driver_module._run_preowned_relay_linux_executor(**arguments)
    assert not events


def test_orphan_terminal_quarantines_exact_terminal_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    active_root = tmp_path / "active"
    foreign_root = tmp_path / "foreign"
    active_root.mkdir()
    foreign_root.mkdir()
    executor, destination, key = support._driver_graph(active_root)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    observation = driver_module._run_preowned_relay_linux_executor(**arguments)
    terminal_events = tuple(events)
    _foreign_executor, _foreign_destination, foreign_key = support._driver_graph(foreign_root)
    driver_state._DRIVER_TERMINALS[foreign_key] = object()

    with pytest.raises(RuntimeError):
        driver_module._run_preowned_relay_linux_executor(**arguments)
    assert tuple(events) == terminal_events
    assert observation is not None
    assert driver_state._DRIVER_TERMINALS[key]
    assert not driver_state._driver_terminal_state_is_capacity_neutral()


def test_nested_cleanup_retries_share_one_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    arguments = support._driver_arguments(executor, destination, events)
    first = OSError("synthetic failure before preownership")
    nested = RuntimeError("synthetic nested cleanup retry")
    real_settle = driver_module._settle_failed_driver_attempt
    cleanup_deadlines: list[float] = []

    def fail_preown(_destination: object) -> object:
        raise first

    def observe_cleanup_deadline(
        attempt: object,
        failures: list[BaseException | None],
        *,
        cleanup_deadline: float,
    ) -> bool:
        cleanup_deadlines.append(cleanup_deadline)
        if len(cleanup_deadlines) == 1:
            raise nested
        return real_settle(
            attempt,
            failures,
            cleanup_deadline=cleanup_deadline,
        )

    monkeypatch.setattr(
        driver_module,
        "_preown_relay_linux_executor",
        fail_preown,
    )
    monkeypatch.setattr(
        driver_module,
        "_settle_failed_driver_attempt",
        observe_cleanup_deadline,
    )
    with pytest.raises(RuntimeError) as captured:
        driver_module._run_preowned_relay_linux_executor(**arguments)

    driver_assertions.assert_sanitized_driver_failure(captured.value, first)
    assert len(cleanup_deadlines) >= 2
    assert all(deadline == cleanup_deadlines[0] for deadline in cleanup_deadlines)
    assert all(deadline is cleanup_deadlines[0] for deadline in cleanup_deadlines)
    assert not events
    assert not driver_state._DRIVER_RECORDS
    assert not support.executor_state._EXECUTORS
    assert not support.executor_state._PORT_RESERVATIONS
    assert key not in support.executor_state._RETIRED_KEYS
    assert not support.worker_registry._RECORDS
    assert not executor._workspace_owner._request._run_root.exists()


def test_initial_store_return_loss_latches_before_same_key_waiter_enters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    failure = OSError("synthetic initial store return loss")
    real_store = driver_state._store_driver_record
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    before_operation = threading.Event()
    waiter_finished = threading.Event()
    finish_first = threading.Event()
    stores = 0
    failures: dict[str, BaseException] = {}

    def store_then_raise(record_key: object, record: object) -> None:
        nonlocal stores
        stores += 1
        real_store(record_key, record)
        if stores == 1:
            raise failure

    def resolve_before_waiter(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        if threading.current_thread().name == "publication-A":
            before_operation.set()
            assert finish_first.wait(timeout=10.0)
        return attempt

    def invoke(label: str) -> None:
        try:
            driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            failures[label] = error
        finally:
            if label == "B":
                waiter_finished.set()

    monkeypatch.setattr(driver_state, "_store_driver_record", store_then_raise)
    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        resolve_before_waiter,
    )
    first = threading.Thread(target=invoke, args=("A",), name="publication-A")
    waiter = threading.Thread(target=invoke, args=("B",), name="publication-B")
    first.start()
    assert before_operation.wait(timeout=5.0)
    waiter.start()
    waiter.join(timeout=10.0)
    assert not waiter.is_alive()
    assert waiter_finished.is_set()
    fresh = driver_module._run_preowned_relay_linux_executor(**arguments)
    assert type(fresh) is support.RelayProbeObservation
    finish_first.set()
    first.join(timeout=10.0)

    assert not first.is_alive()
    assert set(failures) == {"A", "B"}
    driver_assertions.assert_distinct_sanitized_driver_failures(failures, failure)
    assert stores > 1
    assert events.count("process-absent") == 1
    assert events.count("prepare") == 1
    assert events.count("first-fs-delete") == 1
    support._assert_total_absence(executor, destination, key)


@pytest.mark.parametrize(
    ("first", "secondary"),
    [
        (
            KeyboardInterrupt("synthetic first keyboard interrupt"),
            SystemExit(76),
        ),
        (
            _HostileSystemExit(77),
            KeyboardInterrupt("synthetic later keyboard interrupt"),
        ),
    ],
)
def test_cleanup_deadline_sampling_preserves_the_exact_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first: KeyboardInterrupt | SystemExit,
    secondary: KeyboardInterrupt | SystemExit,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    arguments = support._driver_arguments(executor, destination, events)
    real_monotonic = driver_module.time.monotonic
    samples = 0

    def fail_preown(_destination: object) -> object:
        raise first

    def sample_cleanup_clock() -> float:
        nonlocal samples
        samples += 1
        if samples == 1:
            raise secondary
        return real_monotonic()

    monkeypatch.setattr(driver_module, "_preown_relay_linux_executor", fail_preown)
    monkeypatch.setattr(
        driver_module,
        "time",
        SimpleNamespace(monotonic=sample_cleanup_clock),
    )
    outward_type = KeyboardInterrupt if isinstance(first, KeyboardInterrupt) else SystemExit
    with pytest.raises(outward_type) as captured:
        driver_module._run_preowned_relay_linux_executor(**arguments)

    driver_assertions.assert_sanitized_driver_failure(captured.value, first)
    assert samples == 2
    assert not events
    assert not driver_state._DRIVER_RECORDS
    assert not support.executor_state._EXECUTORS
    assert not support.executor_state._PORT_RESERVATIONS
    assert key not in support.executor_state._RETIRED_KEYS
    assert not support.worker_registry._RECORDS
    assert not executor._workspace_owner._request._run_root.exists()


@pytest.mark.parametrize(
    "failure",
    [
        OSError("synthetic abandoned-attempt ordinary failure"),
        KeyboardInterrupt("synthetic abandoned-attempt keyboard interrupt"),
        SystemExit(75),
    ],
)
def test_abandoned_attempt_remains_authoritative_until_failure_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    real_preown = driver_module._preown_relay_linux_executor
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    real_abandon = driver_cleanup._abandon_driver_attempt
    preown_lock = threading.Lock()
    abandoned = threading.Event()
    second_resolved = threading.Event()
    finish_original = threading.Event()
    preown_calls = 0
    original_attempt: list[object] = []
    resolved_attempts: list[object] = []
    results: dict[str, object] = {}
    failures: dict[str, BaseException] = {}

    def fail_first_preown(destination: object) -> object:
        nonlocal preown_calls
        with preown_lock:
            preown_calls += 1
            if preown_calls == 1:
                raise failure
        return real_preown(destination)

    def resolve_with_observation(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        if threading.current_thread().name == "abandoned-B":
            resolved_attempts.append(attempt)
            second_resolved.set()
        return attempt

    def abandon_then_pause(attempt: object) -> bool:
        result = real_abandon(attempt)
        if threading.current_thread().name == "abandoned-A":
            original_attempt.append(attempt)
            assert result
            assert driver_state._driver_attempt_is_abandoned(attempt)
            assert driver_state._DRIVER_RECORDS[key][0] is attempt
            abandoned.set()
            assert finish_original.wait(timeout=10.0)
        return result

    def invoke(label: str) -> None:
        try:
            results[label] = driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            failures[label] = error

    monkeypatch.setattr(
        driver_module,
        "_preown_relay_linux_executor",
        fail_first_preown,
    )
    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        resolve_with_observation,
    )
    monkeypatch.setattr(driver_cleanup, "_abandon_driver_attempt", abandon_then_pause)
    original = threading.Thread(target=invoke, args=("A",), name="abandoned-A")
    overlapping = threading.Thread(target=invoke, args=("B",), name="abandoned-B")
    original.start()
    assert abandoned.wait(timeout=5.0)
    overlapping.start()
    try:
        assert second_resolved.wait(timeout=5.0)
        assert overlapping.is_alive()
        assert preown_calls == 1
        assert not events
    finally:
        finish_original.set()
    original.join(timeout=10.0)
    overlapping.join(timeout=10.0)

    assert not original.is_alive()
    assert not overlapping.is_alive()
    assert not results
    assert set(failures) == {"A", "B"}
    driver_assertions.assert_distinct_sanitized_driver_failures(failures, failure)
    assert len(original_attempt) == len(resolved_attempts) == 1
    assert resolved_attempts[0] is original_attempt[0]
    assert not driver_state._DRIVER_RECORDS
    assert not events

    observation = driver_module._run_preowned_relay_linux_executor(**arguments)
    assert type(observation) is support.RelayProbeObservation
    assert preown_calls == 2
    support._assert_total_absence(executor, destination, key)


def test_retired_abandoned_waiter_replays_failure_after_fresh_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = support._driver_graph(tmp_path)
    support._install_synthetic_build(monkeypatch, executor, events)
    support._install_synthetic_relay(monkeypatch, events)
    arguments = support._driver_arguments(executor, destination, events)
    first = OSError("synthetic first preownership failure")
    real_preown = driver_module._preown_relay_linux_executor
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    real_abandon = driver_cleanup._abandon_driver_attempt
    real_cleanup_latched = driver_module._driver_cleanup_is_latched
    abandoned = threading.Event()
    waiter_resolved = threading.Event()
    finish_original = threading.Event()
    waiter_locked = threading.Event()
    finish_waiter = threading.Event()
    preown_lock = threading.Lock()
    preown_calls = 0
    original_attempt: list[object] = []
    waiter_attempt: list[object] = []
    results: dict[str, object] = {}
    failures: dict[str, BaseException] = {}

    def fail_first_preown(destination: object) -> object:
        nonlocal preown_calls
        with preown_lock:
            preown_calls += 1
            if preown_calls == 1:
                raise first
        return real_preown(destination)

    def resolve_with_barrier(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        if threading.current_thread().name == "retired-B":
            waiter_attempt.append(attempt)
            waiter_resolved.set()
        return attempt

    def abandon_with_barrier(attempt: object) -> bool:
        result = real_abandon(attempt)
        if threading.current_thread().name == "retired-A":
            assert result
            original_attempt.append(attempt)
            abandoned.set()
            assert finish_original.wait(timeout=10.0)
        return result

    def cleanup_latched_with_barrier(attempt: object) -> bool:
        result = real_cleanup_latched(attempt)
        if threading.current_thread().name == "retired-B":
            assert result
            waiter_locked.set()
            assert finish_waiter.wait(timeout=10.0)
        return result

    def invoke(label: str) -> None:
        try:
            results[label] = driver_module._run_preowned_relay_linux_executor(**arguments)
        except BaseException as error:
            failures[label] = error

    monkeypatch.setattr(driver_module, "_preown_relay_linux_executor", fail_first_preown)
    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        resolve_with_barrier,
    )
    monkeypatch.setattr(driver_cleanup, "_abandon_driver_attempt", abandon_with_barrier)
    monkeypatch.setattr(
        driver_module,
        "_driver_cleanup_is_latched",
        cleanup_latched_with_barrier,
    )
    original = threading.Thread(target=invoke, args=("A",), name="retired-A")
    waiter = threading.Thread(target=invoke, args=("B",), name="retired-B")
    original.start()
    assert abandoned.wait(timeout=5.0)
    waiter.start()
    assert waiter_resolved.wait(timeout=5.0)
    finish_original.set()
    original.join(timeout=10.0)
    assert not original.is_alive()
    assert set(failures) == {"A"}
    driver_assertions.assert_sanitized_driver_failure(failures["A"], first)
    failures["A"].args = (support._SECRET,)
    failures["A"].secret_payload = support._SECRET
    failures["A"].add_note(support._SECRET)
    failures["A"].__cause__ = OSError(support._SECRET)
    assert waiter_locked.wait(timeout=5.0)

    fresh = driver_module._run_preowned_relay_linux_executor(**arguments)
    assert type(fresh) is support.RelayProbeObservation
    assert preown_calls == 2
    assert driver_state._DRIVER_TERMINALS[key]
    finish_waiter.set()
    waiter.join(timeout=10.0)

    assert not waiter.is_alive()
    assert set(failures) == {"A", "B"}
    driver_assertions.assert_sanitized_driver_failure(failures["B"], first, support._SECRET)
    assert failures["A"] is not failures["B"]
    assert results == {}
    assert len(original_attempt) == len(waiter_attempt) == 1
    assert waiter_attempt[0] is original_attempt[0]
    support._assert_total_absence(executor, destination, key)


def test_retired_success_waiter_replays_while_different_key_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_events: list[str] = []
    second_events: list[str] = []
    first_executor, first_destination, first_key = support._driver_graph(first_root)
    first_arguments = support._driver_arguments(
        first_executor,
        first_destination,
        first_events,
    )
    real_resolve = driver_module._resolve_or_intend_driver_attempt
    real_historical = driver_module._historical_driver_terminal_observation
    first_inside_lock = threading.Event()
    waiter_resolved = threading.Event()
    waiter_locked = threading.Event()
    finish_waiter = threading.Event()
    first_attempt: list[object] = []
    waiter_attempt: list[object] = []
    results: dict[str, object] = {}
    failures: list[BaseException] = []

    def resolve_with_barrier(**kwargs: object) -> object:
        attempt = real_resolve(**kwargs)
        if threading.current_thread().name == "historical-B":
            waiter_attempt.append(attempt)
            waiter_resolved.set()
        return attempt

    def historical_with_barrier(attempt: object) -> object:
        thread_name = threading.current_thread().name
        if thread_name == "historical-A" and not first_inside_lock.is_set():
            first_attempt.append(attempt)
            first_inside_lock.set()
            assert waiter_resolved.wait(timeout=10.0)
        elif thread_name == "historical-B":
            waiter_locked.set()
            assert finish_waiter.wait(timeout=10.0)
        return real_historical(attempt)

    def invoke_first(label: str) -> None:
        try:
            results[label] = driver_module._run_preowned_relay_linux_executor(**first_arguments)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(
        driver_module,
        "_resolve_or_intend_driver_attempt",
        resolve_with_barrier,
    )
    monkeypatch.setattr(
        driver_module,
        "_historical_driver_terminal_observation",
        historical_with_barrier,
    )
    with monkeypatch.context() as first_effects:
        support._install_synthetic_build(first_effects, first_executor, first_events)
        support._install_synthetic_relay(first_effects, first_events)
        first = threading.Thread(target=invoke_first, args=("A",), name="historical-A")
        waiter = threading.Thread(target=invoke_first, args=("B",), name="historical-B")
        first.start()
        assert first_inside_lock.wait(timeout=5.0)
        waiter.start()
        first.join(timeout=10.0)
        assert not first.is_alive()
        assert waiter_locked.wait(timeout=5.0)

    second_executor, second_destination, second_key = support._driver_graph(second_root)
    second_arguments = support._driver_arguments(
        second_executor,
        second_destination,
        second_events,
    )
    with monkeypatch.context() as second_effects:
        support._install_synthetic_build(second_effects, second_executor, second_events)
        support._install_synthetic_relay(second_effects, second_events)
        second_observation = driver_module._run_preowned_relay_linux_executor(**second_arguments)
    assert type(second_observation) is support.RelayProbeObservation
    finish_waiter.set()
    waiter.join(timeout=10.0)

    assert not waiter.is_alive()
    assert not failures
    assert set(results) == {"A", "B"}
    assert results["A"] is results["B"]
    assert results["A"] is not second_observation
    assert len(first_attempt) == len(waiter_attempt) == 1
    assert waiter_attempt[0] is first_attempt[0]
    for events in (first_events, second_events):
        for event in (
            "process-absent",
            "prepare",
            "create-network",
            "start-browser",
            "revalidate-source",
            "first-fs-delete",
        ):
            assert events.count(event) == 1
    support._assert_total_absence(first_executor, first_destination, first_key)
    support._assert_total_absence(second_executor, second_destination, second_key)


def test_retirement_return_loss_is_complete_before_different_key_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_events: list[str] = []
    second_events: list[str] = []
    first_executor, first_destination, first_key = support._driver_graph(first_root)
    first_arguments = support._driver_arguments(
        first_executor,
        first_destination,
        first_events,
    )
    control = KeyboardInterrupt("synthetic retirement return loss")
    real_pop = driver_state._pop_driver_record
    real_finalize = driver_module._settle_finalize_and_raise
    retired = threading.Event()
    finish_retire = threading.Event()
    failures: list[BaseException] = []

    def pop_then_interrupt(key: object) -> None:
        real_pop(key)
        if threading.current_thread().name == "retirement-A" and not retired.is_set():
            raise control

    def finalize_after_admission(attempt: object) -> None:
        if threading.current_thread().name == "retirement-A":
            retired.set()
            assert finish_retire.wait(timeout=10.0)
        real_finalize(attempt)

    def invoke_first() -> None:
        try:
            driver_module._run_preowned_relay_linux_executor(**first_arguments)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(driver_state, "_pop_driver_record", pop_then_interrupt)
    monkeypatch.setattr(driver_module, "_settle_finalize_and_raise", finalize_after_admission)
    with monkeypatch.context() as first_effects:
        support._install_synthetic_build(first_effects, first_executor, first_events)
        support._install_synthetic_relay(first_effects, first_events)
        first = threading.Thread(target=invoke_first, name="retirement-A")
        first.start()
        assert retired.wait(timeout=10.0)
        first_effects.undo()

        second_executor, second_destination, second_key = support._driver_graph(second_root)
        second_arguments = support._driver_arguments(
            second_executor,
            second_destination,
            second_events,
        )
        with monkeypatch.context() as second_effects:
            support._install_synthetic_build(second_effects, second_executor, second_events)
            support._install_synthetic_relay(second_effects, second_events)
            second_observation = driver_module._run_preowned_relay_linux_executor(
                **second_arguments
            )
        assert type(second_observation) is support.RelayProbeObservation
        finish_retire.set()
        first.join(timeout=10.0)

    assert not first.is_alive()
    driver_assertions.assert_distinct_sanitized_driver_failures(failures, control)
    for events in (first_events, second_events):
        assert events.count("process-absent") == 1
        assert events.count("prepare") == 1
        assert events.count("first-fs-delete") == 1
    support._assert_total_absence(first_executor, first_destination, first_key)
    support._assert_total_absence(second_executor, second_destination, second_key)

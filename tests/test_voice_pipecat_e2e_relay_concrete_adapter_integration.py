"""Concrete invocation adapter integration and adversarial closure checks."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation_cleanup as invocation_cleanup
import scripts.voice_pipecat_e2e_relay_invocation_driver as invocation_driver
import scripts.voice_pipecat_e2e_relay_invocation_lifecycle as invocation_lifecycle
import scripts.voice_pipecat_e2e_relay_invocation_owner_values as invocation_owner_values
import scripts.voice_pipecat_e2e_relay_invocation_process_pair as process_pair
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_effects as pair_effects
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_retirement as pair_retirement
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_values as pair_values
import scripts.voice_pipecat_e2e_relay_invocation_process_values as process_values
import scripts.voice_pipecat_e2e_relay_invocation_support as invocation_support
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayInvocationError
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeRun
from tests import test_voice_pipecat_e2e_relay_linux_executor as executor_fixture

CALL_ID = "123e4567-e89b-42d3-a456-426614174000"
NOW = 1_786_982_400.0


@pytest.fixture(autouse=True)
def _isolated_concrete_adapter_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    mappings = (
        executor_fixture.process_registry._OWNERS,
        executor_fixture.process_registry._KERNELS,
        executor_fixture.process_registry._ABSENCE_RESERVATIONS,
        executor_fixture.process_state._AUTHORITY_BINDINGS,
        executor_fixture.build_values._COMMANDS,
        executor_fixture.build_values._COMMAND_GATES,
        executor_fixture.build_values._COMMAND_CONTROLLERS,
        executor_fixture.build_values._CONTROLLER_COMMANDS,
        executor_fixture.build_values._PROCESS_ASSOCIATIONS,
        executor_fixture.build_receipt._BUILT_LEASES,
        executor_fixture.build_receipt._BUILT_BY_COMMAND,
        executor_fixture.receipt_forget._RETIREMENTS,
        executor_fixture.receipt_forget._RETIREMENT_AUTHORITIES,
        executor_fixture.receipt_forget._RETIRED_RECEIPT_EVIDENCE,
        executor_fixture.consumer_values._BUILD_CONSUMERS,
        executor_fixture.consumer_values._BUILT_BY_CONSUMER,
        executor_fixture.consumer_values._CONSUMED_HISTORY,
        executor_fixture.consumer_values._CONSUMER_TOMBSTONES,
        executor_fixture.fs_contract._LEASES,
        executor_fixture.fs_contract._PREPARED_BUILDS,
        executor_fixture.fs_contract._SETTLEMENTS,
        executor_fixture.fs_contract._CLAIMS,
        executor_fixture.worker_consumer._CONSUMERS,
        executor_fixture.worker_registry._RECORDS,
        executor_fixture.executor_binding._EVIDENCE_BY_KEY,
        executor_fixture.executor_binding._KEYS_BY_BINDING,
        executor_fixture.executor_binding._BINDINGS_BY_BUILT,
        executor_fixture.executor_binding._RELEASE_BINDINGS,
        executor_fixture.executor_binding._BUILD_RETIREMENTS,
        executor_fixture.executor_state._EXECUTORS,
        executor_fixture.executor_state._PORT_RESERVATIONS,
        executor_fixture.executor_state._RETIRED_KEYS,
        executor_fixture.executor_state._AUTHORITY_KEYS,
        executor_fixture.executor_state._DESTINATION_KEYS,
        executor_fixture.executor_state._OWNER_KEYS,
        executor_fixture.executor_state._SOURCE_EVIDENCE,
        executor_fixture.executor_state._WORKSPACE_RELEASES,
        executor_fixture.executor_inner_state._INNER_RECORDS,
        executor_fixture.executor_inner_state._INNER_RESULTS,
        executor_fixture.executor_inner_state._INNER_TERMINALS,
        executor_fixture.executor_inner_state._INNER_AUTHORITIES,
        executor_fixture.relay_owner_state._REGISTRY,
        invocation_cleanup._REGISTRY,
        invocation_support._SECRET_RECORDS,
        process_pair._PAIR_ENTRIES,
    )
    for mapping in mappings:
        mapping.clear()
    yield
    if executor_fixture.executor_binding._EVIDENCE_BY_KEY:
        if process_pair._PAIR_ENTRIES:
            process_pair._PAIR_ENTRIES.clear()
        invocation_cleanup._REGISTRY.clear()
        invocation_support._SECRET_RECORDS.clear()
        evidence = next(iter(executor_fixture.executor_binding._EVIDENCE_BY_KEY.values()))
        _finish_consumed_executor(evidence, monkeypatch)
    for mapping in mappings:
        mapping.clear()


@pytest.fixture
def invocation_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        invocation_support,
        "_canonical_file",
        lambda value, *, executable=False: Path(value),
    )
    monkeypatch.setattr(invocation_support, "_canonical_directory", lambda value: Path(value))
    monkeypatch.setattr(
        invocation_support,
        "_app_command",
        lambda: ("/synthetic/python", "app"),
    )
    monkeypatch.setattr(
        invocation_support,
        "replacement_relay_backend_environment",
        lambda _run: {
            "MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID": CALL_ID,
            "SYNTHETIC_BACKEND": "1",
        },
    )
    monkeypatch.setattr(
        invocation_support,
        "replacement_relay_web_environment",
        lambda _run: {"SYNTHETIC_WEB": "1"},
    )
    monkeypatch.setattr(
        invocation_lifecycle,
        "replacement_relay_playwright_environment",
        lambda _run: {"SYNTHETIC_BROWSER": "1"},
    )


class _AdvancingClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value
        self.samples: list[float] = []

    def __call__(self) -> float:
        self.value += 0.25
        self.samples.append(self.value)
        return self.value


class _ObservedContextLock:
    def __init__(self, lock: object, observe: Callable[[str], None]) -> None:
        self._lock = lock
        self._observe = observe

    def __enter__(self) -> _ObservedContextLock:
        self._observe(threading.current_thread().name)
        self._lock.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self._lock.release()


def _no_wait(_seconds: float) -> None:
    return None


def _epoch_clock() -> float:
    return NOW


def _finish_consumed_executor(evidence: object, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    executor_fixture._install_synthetic_lifecycle(monkeypatch, events)
    executor_fixture.executor_facade._run_consumed_relay_linux_executor(
        executor=evidence.executor,
        destination=evidence.destination,
        binding=evidence.binding,
        runner=executor_fixture._Runner(events),
        bridge_probe=executor_fixture._BridgeProbe(events),
        tools=executor_fixture._tools(),
        invocation_selection=executor_fixture._synthetic_invocation_selection(),
        static_auth_secret=executor_fixture.SECRET,
        now=datetime(2026, 8, 23),
        browser_timeout_seconds=5.0,
        runtime_timeout_seconds=5.0,
        cleanup_timeout_seconds=15.0,
    )


def _new_consumed_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    clock: Callable[[], float],
    sequence: int = 0,
) -> tuple[object, object, object, object, dict[str, object]]:
    events: list[str] = []
    _executor, _destination, _built, binding = executor_fixture._consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
        sequence=sequence,
    )
    key = executor_fixture.executor_binding._KEYS_BY_BINDING[binding]
    build = executor_fixture.executor_binding._EVIDENCE_BY_KEY[key]
    selection = process_values._concrete_invocation_selection()
    runtime_timeout_seconds = 5.0
    runtime_deadline = clock() + runtime_timeout_seconds
    values: dict[str, object] = {
        "build": build,
        "binding": binding,
        "selection": selection,
        "runtime_deadline": runtime_deadline,
        "runtime_timeout_seconds": runtime_timeout_seconds,
        "cleanup_timeout_seconds": 15.0,
        "clock": clock,
        "wait": _no_wait,
        "epoch_clock": _epoch_clock,
    }
    pair_destination = process_pair._resolve_or_preown_concrete_pair_destination(**values)
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        pair_destination,
        **values,
    )
    return grant, driver, tools, pair_destination, values


def _new_owner(driver: object, tools: object) -> tuple[object, object]:
    destination = invocation_support._new_relay_invocation_owner_destination()
    owner = invocation_lifecycle._new_relay_invocation_owner(
        object.__new__(RelayProbeRun),
        driver=driver,
        tools=tools,
        destination=destination,
    )
    return owner, destination


def _assert_fully_retired_owner(
    owner: object,
    owner_destination: object,
    pair_destination: object,
    driver: object,
    tools: object,
    authorities: tuple[object, ...],
    starts: tuple[object, ...],
    stops: tuple[object, ...],
) -> None:
    assert owner._cleanup_phase == "scrubbed"
    assert owner._state == "cleaned"
    assert all(
        getattr(owner, attribute) is None for attribute in invocation_cleanup._TERMINAL_OWNER_FIELDS
    )
    assert owner_destination._is_sealed_empty()
    assert all(destination._is_sealed_empty() for destination in authorities)
    assert all(destination._is_sealed() for destination in starts)
    assert all(destination._is_sealed() for destination in stops)
    assert pair_destination._record is None
    assert pair_destination._retiring_record is None
    assert pair_destination._owner_destination is None
    assert pair_destination._owner_graph is None
    assert pair_destination._owner_token is None
    assert pair_destination._stop_request is None
    assert pair_destination._phase == "cleared"
    assert pair_destination._preown_intended_roles == (False, False, False)
    assert pair_destination._preowned_roles == (False, False, False)
    assert pair_destination._stopped_roles == (False, False, False)
    assert driver.concrete_adapter is False
    assert tools.concrete_adapter is False
    assert owner.concrete_adapter is False
    assert process_pair._concrete_invocation_pair_registries_are_empty()
    assert not invocation_cleanup._REGISTRY
    assert not invocation_support._SECRET_RECORDS


def test_selector_consumed_pair_owner_cleanup_retires_and_scrubs_exact_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    clock = _AdvancingClock()
    grant, driver, tools, pair_destination, values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=clock,
    )
    assert values["selection"] is process_values._concrete_invocation_selection()
    assert grant._build.binding is values["binding"]
    assert driver.concrete_adapter is True
    assert tools.concrete_adapter is True
    assert repr(driver) == "RelayInvocationDriver(concrete_adapter=True)"
    assert repr(tools) == "RelayInvocationTools(concrete_adapter=True)"
    assert len(process_pair._PAIR_ENTRIES) == 1

    owner, owner_destination = _new_owner(driver, tools)
    assert owner.concrete_adapter is True
    assert repr(owner) == "RelayInvocationOwner(concrete_adapter=True)"
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    owner_token = owner._owner_token
    cached_preown = driver._preown

    wrong_app = invocation_driver._RelayChildAuthorityDestination(
        invocation_driver._CHILD_DESTINATION_TOKEN,
        role="app",
    )
    with pytest.raises(RelayInvocationError, match="concrete invocation pair"):
        cached_preown("app", wrong_app)
    assert not process_pair._bind_concrete_invocation_owner_destinations(
        driver,
        tools,
        owner_destination,
        owner,
        owner_token,
        (wrong_app, authorities[1], authorities[2]),
        starts,
        stops,
    )

    stop_request = process_pair._resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        owner_destination,
    )
    assert stop_request is not None
    with pytest.raises(RelayInvocationError, match="concrete invocation pair"):
        cached_preown("app", authorities[0])
    wrong_stop = invocation_driver._RelayChildStopDestination(
        invocation_driver._STOP_DESTINATION_TOKEN,
        owner_token=owner_token,
        role="app",
    )
    with pytest.raises(RelayInvocationError, match="concrete invocation pair"):
        driver._stop(authorities[0]._read("app"), stop_request, wrong_stop)

    stop_roles: list[str] = []
    stop_requests: list[object] = []
    original_stop = pair_effects._inert_stop

    def observed_stop(pair_key, authority, request, destination):
        stop_roles.append(destination._role)
        stop_requests.append(request)
        return original_stop(pair_key, authority, request, destination)

    monkeypatch.setattr(pair_effects, "_inert_stop", observed_stop)
    invocation_lifecycle.cleanup_relay_invocation(owner)

    assert stop_roles == ["browser", "web", "app"]
    assert stop_requests == [stop_request, stop_request, stop_request]
    assert all(destination._is_sealed_empty() for destination in authorities)
    assert all(destination._is_sealed() for destination in starts)
    assert all(destination._is_sealed() for destination in stops)
    assert owner_destination._is_sealed_empty()
    assert wrong_app._peek() is None
    assert wrong_stop._owner_token is owner_token and not wrong_stop._sealed

    assert owner._cleanup_phase == "scrubbed"
    assert owner._state == "cleaned"
    assert owner._owner_token is None
    assert owner._driver is owner._tools is owner._destination is None
    assert pair_destination._record is None
    assert pair_destination._retiring_record is None
    assert pair_destination._owner_destination is None
    assert pair_destination._owner_graph is None
    assert pair_destination._owner_token is None
    assert pair_destination._stop_request is None
    assert pair_destination._phase == "cleared"
    assert pair_destination._preown_intended_roles == (False, False, False)
    assert pair_destination._preowned_roles == (False, False, False)
    assert pair_destination._stopped_roles == (False, False, False)

    assert driver.concrete_adapter is False
    assert tools.concrete_adapter is False
    assert owner.concrete_adapter is False
    assert repr(driver) == "RelayInvocationDriver(concrete_adapter=False)"
    assert repr(tools) == "RelayInvocationTools(concrete_adapter=False)"
    assert repr(owner) == "RelayInvocationOwner(concrete_adapter=False)"
    with pytest.raises(RelayInvocationError, match="concrete invocation pair"):
        cached_preown("app", authorities[0])
    assert process_pair._concrete_invocation_pair_registries_are_empty()
    assert not process_pair._PAIR_ENTRIES
    assert not invocation_cleanup._REGISTRY
    assert not invocation_support._SECRET_RECORDS


@pytest.mark.parametrize("cut", ["destination", "record", "publication"])
def test_advancing_clock_replay_recovers_exact_pair_after_each_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    events: list[str] = []
    _executor, _destination, _built, binding = executor_fixture._consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    key = executor_fixture.executor_binding._KEYS_BY_BINDING[binding]
    build = executor_fixture.executor_binding._EVIDENCE_BY_KEY[key]
    clock = _AdvancingClock()
    selection = process_values._concrete_invocation_selection()
    runtime_deadline = clock() + 5.0
    stable = {
        "build": build,
        "binding": binding,
        "selection": selection,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
        "clock": clock,
        "wait": _no_wait,
        "epoch_clock": _epoch_clock,
    }
    values = {**stable, "runtime_deadline": runtime_deadline}
    committed: list[object] = []
    raised = False
    original_store = process_pair._store_pair_entry
    original_publish = pair_values._RelayConcreteInvocationPairDestination._publish

    def store_then_lose(key, entry):
        nonlocal raised
        original_store(key, entry)
        matches_cut = (
            cut == "destination"
            and type(entry) is pair_values._RelayConcreteInvocationPairDestination
        ) or (cut == "record" and type(entry) is tuple)
        if matches_cut and not raised:
            raised = True
            committed.append(entry)
            raise OSError(f"synthetic {cut} return loss")

    def publish_then_lose(self, grant, driver, tools):
        nonlocal raised
        original_publish(self, grant, driver, tools)
        if cut == "publication" and not raised:
            raised = True
            committed.append((self, grant, driver, tools))
            raise OSError("synthetic publication return loss")

    monkeypatch.setattr(process_pair, "_store_pair_entry", store_then_lose)
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_publish",
        publish_then_lose,
    )

    if cut == "destination":
        with pytest.raises(OSError, match="destination return loss"):
            process_pair._resolve_or_preown_concrete_pair_destination(**values)
    else:
        pair_destination = process_pair._resolve_or_preown_concrete_pair_destination(**values)
        with pytest.raises(OSError, match=rf"{cut} return loss"):
            process_pair._resolve_or_mint_concrete_invocation_pair(
                pair_destination,
                **values,
            )
    assert raised and committed

    advanced_sample = clock()
    recovered_destination = process_pair._recover_concrete_pair_destination(**stable)
    assert type(recovered_destination) is pair_values._RelayConcreteInvocationPairDestination
    assert recovered_destination._runtime_deadline == runtime_deadline
    assert advanced_sample != runtime_deadline - 5.0
    retry_values = {
        **stable,
        "runtime_deadline": recovered_destination._runtime_deadline,
        "cleanup_timeout_seconds": recovered_destination._cleanup_timeout_seconds,
    }
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        recovered_destination,
        **retry_values,
    )
    record = process_pair._PAIR_ENTRIES[build.key]
    assert type(record) is tuple
    assert record[:4] == (grant, driver, tools, recovered_destination)
    assert recovered_destination._read() == (grant, driver, tools)
    if cut == "destination":
        assert committed[0] is recovered_destination
    elif cut == "record":
        assert committed[0] is record
    else:
        assert committed[0] == (recovered_destination, grant, driver, tools)
    assert driver.concrete_adapter is True and tools.concrete_adapter is True
    assert process_pair._retire_concrete_invocation_pair(driver, tools)
    assert driver.concrete_adapter is False and tools.concrete_adapter is False
    assert process_pair._concrete_invocation_pair_registries_are_empty()


@pytest.mark.parametrize("cut", ["begin", "clear", "scrub", "pop"])
def test_public_cleanup_authority_recovers_each_retirement_commit_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
    cut: str,
) -> None:
    _grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    authority = owner._cleanup_authority
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    losses = 0

    def lose_after_commit() -> None:
        nonlocal losses
        if losses == 0:
            losses += 1
            raise OSError(f"synthetic {cut} retirement return loss")

    if cut == "begin":
        original_begin = pair_values._RelayConcreteInvocationPairDestination._begin_retirement

        def begin_then_lose(self, expected):
            committed = original_begin(self, expected)
            if committed:
                lose_after_commit()
            return committed

        monkeypatch.setattr(
            pair_values._RelayConcreteInvocationPairDestination,
            "_begin_retirement",
            begin_then_lose,
        )
    elif cut == "clear":
        original_clear = pair_values._RelayConcreteInvocationPairDestination._clear

        def clear_then_lose(self, expected, destination):
            committed = original_clear(self, expected, destination)
            if committed:
                lose_after_commit()
            return committed

        monkeypatch.setattr(
            pair_values._RelayConcreteInvocationPairDestination,
            "_clear",
            clear_then_lose,
        )
    elif cut == "scrub":
        original_scrub = pair_retirement._scrub_pair_capabilities

        def scrub_then_lose(candidate_driver, candidate_tools):
            original_scrub(candidate_driver, candidate_tools)
            lose_after_commit()

        monkeypatch.setattr(pair_retirement, "_scrub_pair_capabilities", scrub_then_lose)
    else:
        original_pop = pair_retirement._pop_pair_entry

        def pop_then_lose(key):
            original_pop(key)
            lose_after_commit()

        monkeypatch.setattr(pair_retirement, "_pop_pair_entry", pop_then_lose)

    with pytest.raises(invocation_cleanup.RelayInvocationCleanupRequired) as captured:
        invocation_lifecycle.cleanup_relay_invocation(owner)
    assert losses == 1
    assert captured.value.cleanup_authority is authority
    assert invocation_cleanup._REGISTRY.get(authority._key) is owner

    invocation_lifecycle.cleanup_relay_invocation(captured.value.cleanup_authority)
    invocation_lifecycle.cleanup_relay_invocation(authority)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_constructor_recovery_and_cleanup_obey_global_lock_order_without_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    _grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    run = object.__new__(RelayProbeRun)
    owner_destination = invocation_support._new_relay_invocation_owner_destination()
    owner = invocation_lifecycle._new_relay_invocation_owner(
        run,
        driver=driver,
        tools=tools,
        destination=owner_destination,
    )
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)

    constructor_holds_construction = threading.Event()
    release_constructor = threading.Event()
    cleanup_attempted_construction = threading.Event()
    cleanup_attempted_operation = threading.Event()
    timeout_seconds = 2.0

    def observe_construction(thread_name: str) -> None:
        if thread_name == "concrete-cleanup":
            cleanup_attempted_construction.set()

    def observe_operation(thread_name: str) -> None:
        if thread_name == "concrete-cleanup":
            cleanup_attempted_operation.set()

    construction_lock = _ObservedContextLock(
        owner_destination._construction_lock,
        observe_construction,
    )
    operation_lock = _ObservedContextLock(owner._operation_lock, observe_operation)
    owner_destination._construction_lock = construction_lock
    owner._construction_lock = construction_lock
    owner._operation_lock = operation_lock

    original_construct = invocation_lifecycle._construct_relay_invocation_owner

    def gated_construct(*args):
        constructor_holds_construction.set()
        if not release_constructor.wait(timeout_seconds):
            raise TimeoutError("constructor lock-order gate timed out")
        return original_construct(*args)

    monkeypatch.setattr(
        invocation_lifecycle,
        "_construct_relay_invocation_owner",
        gated_construct,
    )
    recovered: list[object] = []
    cleaned: list[None] = []
    constructor_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []

    def recover_owner() -> None:
        try:
            recovered.append(
                invocation_lifecycle._new_relay_invocation_owner(
                    run,
                    driver=driver,
                    tools=tools,
                    destination=owner_destination,
                )
            )
        except BaseException as error:
            constructor_errors.append(error)

    def cleanup_owner() -> None:
        try:
            invocation_lifecycle.cleanup_relay_invocation(owner)
            cleaned.append(None)
        except BaseException as error:
            cleanup_errors.append(error)

    constructor_thread = threading.Thread(
        target=recover_owner,
        name="concrete-constructor",
    )
    cleanup_thread = threading.Thread(
        target=cleanup_owner,
        name="concrete-cleanup",
    )
    constructor_thread.start()
    try:
        assert constructor_holds_construction.wait(timeout_seconds)
        cleanup_thread.start()
        assert cleanup_attempted_construction.wait(timeout_seconds)
        assert not cleanup_attempted_operation.is_set()
    finally:
        release_constructor.set()
        constructor_thread.join(timeout_seconds)
        if cleanup_thread.ident is not None:
            cleanup_thread.join(timeout_seconds)

    assert not constructor_thread.is_alive()
    assert not cleanup_thread.is_alive()
    assert cleanup_attempted_operation.is_set()
    assert constructor_errors == []
    assert cleanup_errors == []
    assert recovered == [owner]
    assert cleaned == [None]
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_malformed_exact_adapter_values_are_total_and_non_authorizing() -> None:
    pair_key = object()
    driver = object.__new__(invocation_driver.RelayInvocationDriver)
    tools = object.__new__(invocation_driver.RelayInvocationTools)
    owner = object.__new__(invocation_owner_values.RelayInvocationOwner)
    object.__setattr__(driver, "_adapter_seal", invocation_driver._CONCRETE_ADAPTER_SEAL)
    object.__setattr__(driver, "_pair_key", pair_key)
    object.__setattr__(tools, "_adapter_seal", invocation_driver._CONCRETE_ADAPTER_SEAL)
    object.__setattr__(tools, "_pair_key", pair_key)
    object.__setattr__(owner, "_driver", driver)
    object.__setattr__(owner, "_tools", tools)
    process_pair._PAIR_ENTRIES[object()] = object()

    assert driver.concrete_adapter is False
    assert tools.concrete_adapter is False
    assert owner.concrete_adapter is False
    assert repr(driver) == "RelayInvocationDriver(concrete_adapter=False)"
    assert repr(tools) == "RelayInvocationTools(concrete_adapter=False)"
    assert repr(owner) == "RelayInvocationOwner(concrete_adapter=False)"


def test_cleanup_authority_recovers_after_primary_registry_entry_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    _grant, driver, tools, _pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    authority = owner._cleanup_authority
    assert invocation_cleanup._REGISTRY.pop(authority._key) is owner

    invocation_lifecycle.cleanup_relay_invocation(authority)
    invocation_lifecycle.cleanup_relay_invocation(authority)

    assert owner._cleanup_phase == "scrubbed"
    assert owner._owner_token is None
    assert owner_destination._is_sealed_empty()
    assert driver.concrete_adapter is False
    assert tools.concrete_adapter is False
    assert process_pair._concrete_invocation_pair_registries_are_empty()
    assert authority._key not in invocation_cleanup._REGISTRY

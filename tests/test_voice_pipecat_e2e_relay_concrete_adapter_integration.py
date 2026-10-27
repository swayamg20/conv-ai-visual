"""Concrete invocation adapter integration and adversarial closure checks."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import weakref
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
import scripts.voice_pipecat_e2e_relay_invocation_owner_foundation as owner_foundation
import scripts.voice_pipecat_e2e_relay_invocation_owner_values as invocation_owner_values
import scripts.voice_pipecat_e2e_relay_invocation_process_pair as process_pair
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_effects as pair_effects
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_retirement as pair_retirement
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_values as pair_values
import scripts.voice_pipecat_e2e_relay_invocation_process_state as process_state
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
        process_state._PROCESS_OWNERS,
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


def _new_consumed_pair_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    clock: Callable[[], float],
    sequence: int = 0,
) -> tuple[object, dict[str, object]]:
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
    return pair_destination, values


def _new_consumed_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    clock: Callable[[], float],
    sequence: int = 0,
) -> tuple[object, object, object, object, dict[str, object]]:
    pair_destination, values = _new_consumed_pair_inputs(
        tmp_path,
        monkeypatch,
        clock=clock,
        sequence=sequence,
    )
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
    grant: object,
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
    assert process_state._process_owner_total_absence(
        pair_destination._read_process_owner_destination()
    )
    admission = pair_destination._process_grant_admission
    assert (
        type(admission) is tuple
        and len(admission) == 6
        and admission[0] == "sealed"
        and admission[1] is pair_destination._read_process_owner_destination()
        and type(admission[2]) is object
        and type(admission[3]) is weakref.ReferenceType
        and admission[3]() is grant
        and type(admission[4]) is tuple
        and len(admission[4]) == 3
        and all(type(reference) is weakref.ReferenceType for reference in admission[4])
        and type(admission[5]) is weakref.ReferenceType
    )
    assert driver.concrete_adapter is False
    assert tools.concrete_adapter is False
    assert owner.concrete_adapter is False
    assert process_pair._concrete_invocation_pair_registries_are_empty()
    assert not invocation_cleanup._REGISTRY
    assert not invocation_support._SECRET_RECORDS


def test_owner_foundation_preserves_first_control_across_graph_match_and_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    _grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    original_read = pair_values._RelayConcreteInvocationPairDestination._read
    original_bind = owner_foundation._bind_concrete_invocation_owner_destinations
    cuts: list[str] = []

    def publication_read_then_interrupt(self):
        if self is pair_destination and not cuts:
            cuts.append("match")
            raise KeyboardInterrupt("first graph control")
        return original_read(self)

    def bind_then_exit(*args):
        assert original_bind(*args)
        cuts.append("bind")
        raise SystemExit(79)

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        publication_read_then_interrupt,
    )
    monkeypatch.setattr(
        owner_foundation,
        "_bind_concrete_invocation_owner_destinations",
        bind_then_exit,
    )

    matched, control = owner_foundation._reconcile_concrete_owner_graph(
        owner,
        driver,
        tools,
        owner_destination,
    )

    assert matched is True
    assert control == (KeyboardInterrupt, None)
    assert cuts == ["match", "bind"]
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        original_read,
    )
    monkeypatch.setattr(
        owner_foundation,
        "_bind_concrete_invocation_owner_destinations",
        original_bind,
    )
    invocation_lifecycle.cleanup_relay_invocation(owner)


def test_owner_foundation_rejects_crosswired_graph_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    _grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    original_read = pair_values._RelayConcreteInvocationPairDestination._read_retirement_state
    original_bind = owner_foundation._bind_concrete_invocation_owner_destinations
    bind_attempts = 0

    def crosswired_read(self):
        state = original_read(self)
        if self is pair_destination:
            graph = (*state[2][:-1], object())
            return (*state[:2], graph, *state[3:])
        return state

    def observed_bind(*args):
        nonlocal bind_attempts
        bind_attempts += 1
        return original_bind(*args)

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read_retirement_state",
        crosswired_read,
    )
    monkeypatch.setattr(
        owner_foundation,
        "_bind_concrete_invocation_owner_destinations",
        observed_bind,
    )

    matched, control = owner_foundation._reconcile_concrete_owner_graph(
        owner,
        driver,
        tools,
        owner_destination,
    )

    assert matched is False
    assert control is None
    assert bind_attempts == 2
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read_retirement_state",
        original_read,
    )
    monkeypatch.setattr(
        owner_foundation,
        "_bind_concrete_invocation_owner_destinations",
        original_bind,
    )
    invocation_lifecycle.cleanup_relay_invocation(owner)


@pytest.mark.parametrize("cut", ["outer", "registry", "graph"])
@pytest.mark.parametrize("after_commit", [False, True], ids=["before", "after"])
@pytest.mark.parametrize("control_kind", ["keyboard", "system-exit"])
def test_owner_constructor_foundation_recovers_every_publication_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
    cut: str,
    after_commit: bool,
    control_kind: str,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    run = object.__new__(RelayProbeRun)
    owner_destination = invocation_support._new_relay_invocation_owner_destination()
    captured: dict[str, object] = {}
    raised = False

    def capture(candidate: object) -> None:
        captured.setdefault("owner", candidate)
        captured.setdefault("authorities", (candidate._app, candidate._web, candidate._browser))
        captured.setdefault(
            "starts",
            (candidate._app_start, candidate._web_start, candidate._browser_start),
        )
        captured.setdefault(
            "stops",
            (candidate._app_stop, candidate._web_stop, candidate._browser_stop),
        )

    def interrupt() -> None:
        nonlocal raised
        assert not raised
        raised = True
        if control_kind == "keyboard":
            raise KeyboardInterrupt("constructor foundation cut")
        raise SystemExit(83)

    original_outer = invocation_driver._RelayInvocationOwnerDestination._publish_owner
    original_register = invocation_lifecycle._register_cleanup_owner
    original_graph = invocation_owner_values._bind_concrete_invocation_owner_destinations

    if cut == "outer":

        def cut_outer(self, run_value, driver_value, tools_value, candidate):
            capture(candidate)
            if not after_commit:
                interrupt()
            original_outer(self, run_value, driver_value, tools_value, candidate)
            interrupt()

        monkeypatch.setattr(
            invocation_driver._RelayInvocationOwnerDestination,
            "_publish_owner",
            cut_outer,
        )
    elif cut == "registry":

        def cut_registry(authority, candidate):
            capture(candidate)
            if not after_commit:
                interrupt()
            original_register(authority, candidate)
            interrupt()

        monkeypatch.setattr(invocation_lifecycle, "_register_cleanup_owner", cut_registry)
    else:

        def cut_graph(*args):
            capture(args[3])
            if not after_commit:
                interrupt()
            assert original_graph(*args)
            interrupt()

        monkeypatch.setattr(
            invocation_owner_values,
            "_bind_concrete_invocation_owner_destinations",
            cut_graph,
        )

    expected_control = KeyboardInterrupt if control_kind == "keyboard" else SystemExit
    with pytest.raises(expected_control) as escaped:
        invocation_lifecycle._new_relay_invocation_owner(
            run,
            driver=driver,
            tools=tools,
            destination=owner_destination,
        )
    assert raised
    if control_kind == "system-exit":
        assert escaped.value.code == 83
    candidate = captured["owner"]

    monkeypatch.setattr(
        invocation_driver._RelayInvocationOwnerDestination,
        "_publish_owner",
        original_outer,
    )
    monkeypatch.setattr(invocation_lifecycle, "_register_cleanup_owner", original_register)
    monkeypatch.setattr(
        invocation_owner_values,
        "_bind_concrete_invocation_owner_destinations",
        original_graph,
    )

    if cut == "outer" and not after_commit:
        assert owner_destination._record is None
        assert candidate._cleanup_authority._key not in invocation_cleanup._REGISTRY
        owner = invocation_lifecycle._new_relay_invocation_owner(
            run,
            driver=driver,
            tools=tools,
            destination=owner_destination,
        )
        assert owner is not candidate
        authorities = (owner._app, owner._web, owner._browser)
        starts = (owner._app_start, owner._web_start, owner._browser_start)
        stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    else:
        owner = candidate
        authorities = captured["authorities"]
        starts = captured["starts"]
        stops = captured["stops"]

    invocation_lifecycle.cleanup_relay_invocation(owner)
    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


@pytest.mark.parametrize("role_index,role", enumerate(("app", "web", "browser")))
@pytest.mark.parametrize(
    "stage",
    [
        "intent-before-publish",
        "publish-before-marker",
        "marker-after",
        "callback-after",
        "publication-return-loss",
    ],
)
@pytest.mark.parametrize("control_kind", ["keyboard", "system-exit"])
def test_partial_role_preownership_cleanup_converges_at_every_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
    role_index: int,
    role: str,
    stage: str,
    control_kind: str,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    run = object.__new__(RelayProbeRun)
    owner_destination = invocation_support._new_relay_invocation_owner_destination()
    captured: dict[str, object] = {}
    raised = False
    original_outer = invocation_driver._RelayInvocationOwnerDestination._publish_owner
    original_publish = invocation_driver._RelayChildAuthorityDestination.publish
    original_mark = pair_values._RelayConcreteInvocationPairDestination._mark_role_preowned
    original_callback = pair_effects._inert_preown

    def capture_outer(self, run_value, driver_value, tools_value, candidate):
        original_outer(self, run_value, driver_value, tools_value, candidate)
        captured.update(
            owner=candidate,
            authorities=(candidate._app, candidate._web, candidate._browser),
            starts=(candidate._app_start, candidate._web_start, candidate._browser_start),
            stops=(candidate._app_stop, candidate._web_stop, candidate._browser_stop),
        )

    def interrupt() -> None:
        nonlocal raised
        assert not raised
        raised = True
        if control_kind == "keyboard":
            raise KeyboardInterrupt("partial preownership cut")
        raise SystemExit(89)

    def cut_publish(self, authority):
        target = bool(captured and self is captured["authorities"][role_index])
        if target and stage == "intent-before-publish" and not raised:
            interrupt()
        original_publish(self, authority)
        if target and stage == "publication-return-loss" and not raised:
            interrupt()

    def cut_mark(self, current_role_index):
        target = self is pair_destination and current_role_index == role_index
        if target and stage == "publish-before-marker" and not raised:
            interrupt()
        committed = original_mark(self, current_role_index)
        if target and stage == "marker-after" and not raised:
            interrupt()
        return committed

    def cut_callback(pair_key, current_role, destination):
        original_callback(pair_key, current_role, destination)
        if current_role == role and stage == "callback-after" and not raised:
            interrupt()

    monkeypatch.setattr(
        invocation_driver._RelayInvocationOwnerDestination,
        "_publish_owner",
        capture_outer,
    )
    monkeypatch.setattr(
        invocation_driver._RelayChildAuthorityDestination,
        "publish",
        cut_publish,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_preowned",
        cut_mark,
    )
    monkeypatch.setattr(pair_effects, "_inert_preown", cut_callback)

    expected_control = KeyboardInterrupt if control_kind == "keyboard" else SystemExit
    with pytest.raises(expected_control) as escaped:
        invocation_lifecycle._new_relay_invocation_owner(
            run,
            driver=driver,
            tools=tools,
            destination=owner_destination,
        )
    assert raised
    if control_kind == "system-exit":
        assert escaped.value.code == 89

    owner = captured["owner"]
    invocation_lifecycle.cleanup_relay_invocation(owner)
    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        captured["authorities"],
        captured["starts"],
        captured["stops"],
    )


def test_factory_record_rejects_forged_first_process_owner_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    pair_destination, values = _new_consumed_pair_inputs(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    original = process_pair._resolve_or_preown_process_owner
    captured: dict[str, object] = {}

    class Forged:
        def __init__(self, pair_key: object) -> None:
            self._pair_key = pair_key

    def reject_forged_then_interrupt(destination, grant, roles):
        captured.update(destination=destination, grant=grant, roles=roles)
        forged = Forged(grant._pair_key)
        with pytest.raises(TypeError, match="process owner is invalid"):
            process_state._resolve_or_preown_process_owner(destination, forged, roles)
        with pytest.raises(TypeError, match="process owner is invalid"):
            process_state._RelayProcessOwner(
                process_state._OWNER_TOKEN,
                destination=destination,
                grant=forged,
                roles=roles,
            )
        assert process_state._PROCESS_OWNERS == {}
        assert destination._read_owner() is None
        raise OSError("synthetic process-owner boundary interruption")

    monkeypatch.setattr(
        process_pair,
        "_resolve_or_preown_process_owner",
        reject_forged_then_interrupt,
    )
    with pytest.raises(OSError, match="boundary interruption"):
        process_pair._resolve_or_mint_concrete_invocation_pair(pair_destination, **values)

    record = process_pair._PAIR_ENTRIES[values["build"].key]
    assert record[0] is captured["grant"]
    assert record[4] is captured["roles"]
    monkeypatch.setattr(process_pair, "_resolve_or_preown_process_owner", original)
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        pair_destination,
        **values,
    )
    try:
        assert grant is record[0]
        assert process_state._process_owner_matches(
            captured["destination"],
            grant,
            record[4],
        )
    finally:
        process_pair._retire_concrete_invocation_pair(driver, tools)
    assert process_pair._concrete_invocation_pair_registries_are_empty()


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
    pair_record = process_pair._PAIR_ENTRIES[grant._build.key]
    process_destination = pair_destination._read_process_owner_destination()
    process_owner = process_destination._read_owner()
    assert process_state._process_owner_matches(
        process_destination,
        grant,
        pair_record[4],
    )
    assert process_owner._role_phases() == ("preowned", "preowned", "preowned")
    assert tuple(role for role, _authority in pair_record[4]) == ("app", "web", "browser")

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
    assert process_owner._is_retired()
    assert process_state._process_owner_total_absence(process_destination)

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


def test_pair_effects_preserve_first_control_across_publication_and_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    pair_key = grant._pair_key

    original_authority_publish = invocation_driver._RelayChildAuthorityDestination.publish
    original_mark_preowned = pair_values._RelayConcreteInvocationPairDestination._mark_role_preowned
    preown_cuts: list[str] = []

    def authority_publish_then_interrupt(self, authority):
        original_authority_publish(self, authority)
        if self is authorities[0] and not preown_cuts:
            preown_cuts.append("publish")
            raise KeyboardInterrupt("first preown control")

    def preown_mark_then_exit(self, role_index):
        committed = original_mark_preowned(self, role_index)
        if self is pair_destination and role_index == 0 and len(preown_cuts) == 1:
            preown_cuts.append("mark")
            raise SystemExit(71)
        return committed

    monkeypatch.setattr(
        invocation_driver._RelayChildAuthorityDestination,
        "publish",
        authority_publish_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_preowned",
        preown_mark_then_exit,
    )
    with pytest.raises(KeyboardInterrupt, match="first preown control"):
        pair_effects._inert_preown(pair_key, "app", authorities[0])
    assert preown_cuts == ["publish", "mark"]

    monkeypatch.setattr(
        invocation_driver._RelayChildAuthorityDestination,
        "publish",
        original_authority_publish,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_preowned",
        original_mark_preowned,
    )
    pair_effects._inert_preown(pair_key, "app", authorities[0])

    stop_request = process_pair._resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        owner_destination,
    )
    assert stop_request is not None
    original_stop_publish = invocation_driver._RelayChildStopDestination.publish
    original_mark_stopped = pair_values._RelayConcreteInvocationPairDestination._mark_role_stopped
    stop_cuts: list[str] = []

    def stop_publish_then_interrupt(self, receipt):
        original_stop_publish(self, receipt)
        if self is stops[2] and not stop_cuts:
            stop_cuts.append("publish")
            raise KeyboardInterrupt("first stop control")

    def stop_mark_then_exit(self, role_index):
        committed = original_mark_stopped(self, role_index)
        if self is pair_destination and role_index == 2 and len(stop_cuts) == 1:
            stop_cuts.append("mark")
            raise SystemExit(71)
        return committed

    monkeypatch.setattr(
        invocation_driver._RelayChildStopDestination,
        "publish",
        stop_publish_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_stopped",
        stop_mark_then_exit,
    )
    with pytest.raises(KeyboardInterrupt, match="first stop control"):
        pair_effects._inert_stop(
            pair_key,
            authorities[2]._peek(),
            stop_request,
            stops[2],
        )
    assert stop_cuts == ["publish", "mark"]

    monkeypatch.setattr(
        invocation_driver._RelayChildStopDestination,
        "publish",
        original_stop_publish,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_stopped",
        original_mark_stopped,
    )
    pair_effects._inert_stop(
        pair_key,
        authorities[2]._peek(),
        stop_request,
        stops[2],
    )

    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_preown_post_publication_auth_cannot_replace_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    run = object.__new__(RelayProbeRun)
    owner_destination = invocation_support._new_relay_invocation_owner_destination()
    original_outer = invocation_driver._RelayInvocationOwnerDestination._publish_owner
    original_publish = invocation_driver._RelayChildAuthorityDestination.publish
    original_pair_read = pair_values._RelayConcreteInvocationPairDestination._read
    captured: dict[str, object] = {}
    cuts: list[str] = []

    def capture_outer(self, run_value, driver_value, tools_value, candidate):
        original_outer(self, run_value, driver_value, tools_value, candidate)
        captured.update(
            owner=candidate,
            authorities=(candidate._app, candidate._web, candidate._browser),
            starts=(candidate._app_start, candidate._web_start, candidate._browser_start),
            stops=(candidate._app_stop, candidate._web_stop, candidate._browser_stop),
        )

    def publish_then_interrupt(self, authority):
        original_publish(self, authority)
        if captured and self is captured["authorities"][0] and not cuts:
            cuts.append("publish")
            raise KeyboardInterrupt("first preown auth control")

    def pair_read_then_exit(self):
        published = original_pair_read(self)
        if self is pair_destination and cuts == ["publish"]:
            cuts.append("auth")
            raise SystemExit(107)
        return published

    monkeypatch.setattr(
        invocation_driver._RelayInvocationOwnerDestination,
        "_publish_owner",
        capture_outer,
    )
    monkeypatch.setattr(
        invocation_driver._RelayChildAuthorityDestination,
        "publish",
        publish_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        pair_read_then_exit,
    )

    with pytest.raises(KeyboardInterrupt):
        invocation_lifecycle._new_relay_invocation_owner(
            run,
            driver=driver,
            tools=tools,
            destination=owner_destination,
        )

    assert cuts == ["publish", "auth"]
    owner = captured["owner"]
    invocation_lifecycle.cleanup_relay_invocation(owner)
    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        captured["authorities"],
        captured["starts"],
        captured["stops"],
    )


def test_stop_post_publication_auth_cannot_replace_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    clock = _AdvancingClock()
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=clock,
    )
    owner, owner_destination = _new_owner(driver, tools)
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    stop_request = process_pair._resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        owner_destination,
    )
    assert stop_request is not None
    original_publish = invocation_driver._RelayChildStopDestination.publish
    original_pair_read = pair_values._RelayConcreteInvocationPairDestination._read
    cuts: list[str] = []

    def publish_then_interrupt(self, stopped):
        original_publish(self, stopped)
        if self is stops[2] and not cuts:
            cuts.append("publish")
            raise KeyboardInterrupt("first stop auth control")

    def pair_read_then_exit(self):
        published = original_pair_read(self)
        if self is pair_destination and cuts == ["publish"]:
            cuts.append("auth")
            raise SystemExit(109)
        return published

    monkeypatch.setattr(
        invocation_driver._RelayChildStopDestination,
        "publish",
        publish_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        pair_read_then_exit,
    )
    with pytest.raises(KeyboardInterrupt, match="first stop auth control"):
        pair_effects._inert_stop(
            grant._pair_key,
            authorities[2]._peek(),
            stop_request,
            stops[2],
        )

    assert cuts == ["publish", "auth"]
    assert pair_destination._stopped_roles == (False, False, False)
    monkeypatch.setattr(
        invocation_driver._RelayChildStopDestination,
        "publish",
        original_publish,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        original_pair_read,
    )
    samples_before_replay = len(clock.samples)
    pair_effects._inert_stop(
        grant._pair_key,
        authorities[2]._peek(),
        stop_request,
        stops[2],
    )
    assert len(clock.samples) == samples_before_replay
    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_preownership_reconciliation_observation_cannot_replace_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    owner, owner_destination = _new_owner(driver, tools)
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    stop_request = process_pair._resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        owner_destination,
    )
    assert stop_request is not None
    for role_index in (2, 1):
        pair_effects._inert_stop(
            grant._pair_key,
            authorities[role_index]._peek(),
            stop_request,
            stops[role_index],
        )
    with pair_destination._lock:
        pair_destination._preowned_roles = (False, True, True)
    original_mark = pair_values._RelayConcreteInvocationPairDestination._mark_role_preowned
    original_read = pair_values._RelayConcreteInvocationPairDestination._read_retirement_state
    cuts: list[str] = []

    def mark_then_interrupt(self, role_index):
        committed = original_mark(self, role_index)
        if self is pair_destination and role_index == 0 and not cuts:
            cuts.append("mark")
            raise KeyboardInterrupt("first reconciliation control")
        return committed

    def read_then_exit(self):
        state = original_read(self)
        if self is pair_destination and cuts == ["mark"]:
            cuts.append("observe")
            raise SystemExit(113)
        return state

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_preowned",
        mark_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read_retirement_state",
        read_then_exit,
    )
    with pytest.raises(KeyboardInterrupt, match="first reconciliation control"):
        pair_effects._inert_stop(
            grant._pair_key,
            authorities[0]._peek(),
            stop_request,
            stops[0],
        )

    assert cuts == ["mark", "observe"]
    assert pair_destination._preowned_roles == (True, True, True)
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_mark_role_preowned",
        original_mark,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read_retirement_state",
        original_read,
    )
    pair_effects._inert_stop(
        grant._pair_key,
        authorities[0]._peek(),
        stop_request,
        stops[0],
    )
    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_committed_process_stop_reconciles_after_cleanup_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
) -> None:
    clock = _AdvancingClock()
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=clock,
    )
    owner, owner_destination = _new_owner(driver, tools)
    authorities = (owner._app, owner._web, owner._browser)
    starts = (owner._app_start, owner._web_start, owner._browser_start)
    stops = (owner._app_stop, owner._web_stop, owner._browser_stop)
    stop_request = process_pair._resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        owner_destination,
    )
    assert stop_request is not None
    original_commit = process_state._commit_process_snapshot
    cuts: list[str] = []

    def process_stop_then_interrupt(destination, snapshot):
        original_commit(destination, snapshot)
        if (
            not cuts
            and snapshot.phase == "owned"
            and snapshot.role_phases == ("preowned", "preowned", "stopped")
        ):
            cuts.append("process-stop")
            raise KeyboardInterrupt("process stop return loss")

    monkeypatch.setattr(
        process_state,
        "_commit_process_snapshot",
        process_stop_then_interrupt,
    )
    with pytest.raises(KeyboardInterrupt, match="process stop return loss"):
        pair_effects._inert_stop(
            grant._pair_key,
            authorities[2]._peek(),
            stop_request,
            stops[2],
        )

    assert cuts == ["process-stop"]
    with pytest.raises(TypeError):
        stops[2]._read(owner._owner_token, "browser")
    assert pair_destination._stopped_roles == (False, False, False)
    monkeypatch.setattr(
        process_state,
        "_commit_process_snapshot",
        original_commit,
    )
    for role_index in (1, 0):
        pair_effects._inert_stop(
            grant._pair_key,
            authorities[role_index]._peek(),
            stop_request,
            stops[role_index],
        )
    assert pair_destination._stopped_roles == (True, True, False)
    clock.value = stop_request.absolute_deadline + 1.0
    samples_before_replay = len(clock.samples)

    pair_effects._inert_stop(
        grant._pair_key,
        authorities[2]._peek(),
        stop_request,
        stops[2],
    )
    assert len(clock.samples) == samples_before_replay
    receipt = stops[2]._read(owner._owner_token, "browser")
    assert type(receipt) is invocation_driver._RelayChildStopReceipt
    assert receipt._matches(owner._owner_token, "browser")
    assert pair_destination._stopped_roles == (True, True, True)

    invocation_lifecycle.cleanup_relay_invocation(owner)
    _assert_fully_retired_owner(
        owner,
        owner_destination,
        pair_destination,
        grant,
        driver,
        tools,
        authorities,
        starts,
        stops,
    )


def test_pair_post_owner_validation_preserves_transactional_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_destination, values = _new_consumed_pair_inputs(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    original = process_pair._transactional_record_process_owner_matches
    cuts: list[str] = []

    def interrupt_post_owner(pair_key, record):
        if not cuts:
            cuts.append("post-owner")
            raise KeyboardInterrupt("post-owner validation control")
        return original(pair_key, record)

    monkeypatch.setattr(
        process_pair,
        "_transactional_record_process_owner_matches",
        interrupt_post_owner,
    )
    with pytest.raises(KeyboardInterrupt, match="post-owner validation control"):
        process_pair._resolve_or_mint_concrete_invocation_pair(
            pair_destination,
            **values,
        )

    assert cuts == ["post-owner"]
    record = process_pair._PAIR_ENTRIES[values["build"].key]
    assert record[3] is pair_destination
    assert pair_destination._read() is None
    monkeypatch.setattr(
        process_pair,
        "_transactional_record_process_owner_matches",
        original,
    )
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        pair_destination,
        **values,
    )
    assert (grant, driver, tools) == record[:3]
    assert process_pair._retire_concrete_invocation_pair(driver, tools)


def test_pair_post_owner_validation_preserves_role_observation_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_destination, values = _new_consumed_pair_inputs(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    process_destination = pair_destination._read_process_owner_destination()
    original_resolve = process_pair._resolve_or_preown_process_owner
    original_role_match = process_state._RelayProcessOwnerDestination._role_authority_matches
    owner_resolved = False
    cuts: list[str] = []

    def observed_resolve(destination, grant, roles):
        nonlocal owner_resolved
        owner = original_resolve(destination, grant, roles)
        owner_resolved = True
        return owner

    def interrupt_role_match(self, claim, pair_key, role, authority):
        if self is process_destination and owner_resolved and not cuts:
            cuts.append(role)
            raise KeyboardInterrupt("role observation control")
        return original_role_match(self, claim, pair_key, role, authority)

    monkeypatch.setattr(
        process_pair,
        "_resolve_or_preown_process_owner",
        observed_resolve,
    )
    monkeypatch.setattr(
        process_state._RelayProcessOwnerDestination,
        "_role_authority_matches",
        interrupt_role_match,
    )
    with pytest.raises(KeyboardInterrupt, match="role observation control"):
        process_pair._resolve_or_mint_concrete_invocation_pair(
            pair_destination,
            **values,
        )

    assert cuts == ["app"]
    monkeypatch.setattr(
        process_pair,
        "_resolve_or_preown_process_owner",
        original_resolve,
    )
    monkeypatch.setattr(
        process_state._RelayProcessOwnerDestination,
        "_role_authority_matches",
        original_role_match,
    )
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        pair_destination,
        **values,
    )
    assert process_state._process_owner_matches(
        process_destination, grant, process_pair._PAIR_ENTRIES[values["build"].key][4]
    )
    assert process_pair._retire_concrete_invocation_pair(driver, tools)


def test_pair_publication_observation_cannot_replace_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_destination, values = _new_consumed_pair_inputs(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    original_publish = pair_values._RelayConcreteInvocationPairDestination._publish
    original_read = pair_values._RelayConcreteInvocationPairDestination._read
    cuts: list[str] = []

    def publish_then_interrupt(self, grant, driver, tools):
        original_publish(self, grant, driver, tools)
        if self is pair_destination and not cuts:
            cuts.append("publish")
            raise KeyboardInterrupt("first pair publication control")

    def read_then_exit(self):
        recovered = original_read(self)
        if self is pair_destination and cuts == ["publish"]:
            cuts.append("read")
            raise SystemExit(97)
        return recovered

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_publish",
        publish_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        read_then_exit,
    )
    with pytest.raises(KeyboardInterrupt, match="first pair publication control"):
        process_pair._resolve_or_mint_concrete_invocation_pair(
            pair_destination,
            **values,
        )

    assert cuts == ["publish", "read"]
    record = process_pair._PAIR_ENTRIES[values["build"].key]
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_publish",
        original_publish,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_read",
        original_read,
    )
    grant, driver, tools = process_pair._resolve_or_mint_concrete_invocation_pair(
        pair_destination,
        **values,
    )
    assert (grant, driver, tools) == record[:3]
    assert process_pair._retire_concrete_invocation_pair(driver, tools)


@pytest.mark.parametrize("cut", ["begin", "clear"])
def test_pair_retirement_observation_cannot_replace_first_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    _grant, driver, tools, pair_destination, _values = _new_consumed_pair(
        tmp_path,
        monkeypatch,
        clock=_AdvancingClock(),
    )
    original_begin = pair_values._RelayConcreteInvocationPairDestination._begin_retirement
    original_begin_observation = (
        pair_values._RelayConcreteInvocationPairDestination._retirement_in_progress
    )
    original_clear = pair_values._RelayConcreteInvocationPairDestination._clear
    original_clear_observation = (
        pair_values._RelayConcreteInvocationPairDestination._retirement_owner_matches
    )
    committed = False
    observed = False
    cuts: list[str] = []

    def begin_then_interrupt(self, expected):
        nonlocal committed
        result = original_begin(self, expected)
        if self is pair_destination and cut == "begin":
            committed = True
            cuts.append("begin")
            raise KeyboardInterrupt("first pair retirement control")
        return result

    def begin_observation_then_exit(self, expected):
        nonlocal observed
        result = original_begin_observation(self, expected)
        if self is pair_destination and cut == "begin" and committed and not observed:
            observed = True
            cuts.append("observe")
            raise SystemExit(101)
        return result

    def clear_then_interrupt(self, expected, owner_destination):
        nonlocal committed
        result = original_clear(self, expected, owner_destination)
        if self is pair_destination and cut == "clear":
            committed = True
            cuts.append("clear")
            raise KeyboardInterrupt("first pair retirement control")
        return result

    def clear_observation_then_exit(self, owner_destination):
        nonlocal observed
        result = original_clear_observation(self, owner_destination)
        if self is pair_destination and cut == "clear" and committed and not observed:
            observed = True
            cuts.append("observe")
            raise SystemExit(103)
        return result

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_begin_retirement",
        begin_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_retirement_in_progress",
        begin_observation_then_exit,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_clear",
        clear_then_interrupt,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_retirement_owner_matches",
        clear_observation_then_exit,
    )

    with pytest.raises(KeyboardInterrupt, match="first pair retirement control"):
        process_pair._retire_concrete_invocation_pair(driver, tools)

    assert cuts == [cut, "observe"]
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_begin_retirement",
        original_begin,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_retirement_in_progress",
        original_begin_observation,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_clear",
        original_clear,
    )
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_retirement_owner_matches",
        original_clear_observation,
    )
    assert process_pair._retire_concrete_invocation_pair(driver, tools)
    assert process_pair._concrete_invocation_pair_registries_are_empty()


@pytest.mark.parametrize("cut", ["destination", "grant", "record", "publication"])
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
    original_grant = (
        pair_values._RelayConcreteInvocationPairDestination._resolve_or_mint_process_grant
    )
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

    def grant_then_lose(self, token, pair_key, roles):
        nonlocal raised
        grant = original_grant(self, token, pair_key, roles)
        if cut == "grant" and not raised:
            raised = True
            committed.append((self, grant, roles))
            raise OSError("synthetic grant return loss")
        return grant

    monkeypatch.setattr(process_pair, "_store_pair_entry", store_then_lose)
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_mint_process_grant",
        grant_then_lose,
    )
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
    elif cut == "grant":
        assert committed[0] == (recovered_destination, grant, record[4])
    else:
        assert committed[0] == (recovered_destination, grant, driver, tools)
    assert driver.concrete_adapter is True and tools.concrete_adapter is True
    assert process_pair._retire_concrete_invocation_pair(driver, tools)
    assert driver.concrete_adapter is False and tools.concrete_adapter is False
    assert process_pair._concrete_invocation_pair_registries_are_empty()


@pytest.mark.parametrize("cut", ["begin", "clear", "admission", "scrub", "pop"])
def test_public_cleanup_authority_recovers_each_retirement_commit_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocation_runtime: None,
    cut: str,
) -> None:
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
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
    elif cut == "admission":
        original_seal = (
            pair_values._RelayConcreteInvocationPairDestination._seal_process_grant_admission
        )

        def seal_then_lose(self, grant, roles):
            committed = original_seal(self, grant, roles)
            if committed:
                lose_after_commit()
            return committed

        monkeypatch.setattr(
            pair_values._RelayConcreteInvocationPairDestination,
            "_seal_process_grant_admission",
            seal_then_lose,
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
        grant,
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
    grant, driver, tools, pair_destination, _values = _new_consumed_pair(
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
        grant,
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

"""Synthetic lifecycle tests for the effect-free workspace worker."""
# ruff: noqa: E402

from __future__ import annotations

import gc
import pickle
import sys
import threading
import time
import weakref
from copy import copy, deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active as active_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_control as control_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle as lifecycle_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_release as release_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as thread_module


def _graph_without_construction(tmp_path: Path, run_id: str = "worker-lifecycle"):
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=(tmp_path / "source").resolve(),
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id=run_id,
    )
    owner = destination._read(destination._request)
    bundle = state_module._new_relay_linux_build_workspace_worker_bundle(owner)
    return owner, bundle


def _graph(tmp_path: Path, run_id: str = "worker-lifecycle"):
    owner, bundle = _graph_without_construction(tmp_path, run_id)
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    assert construction is not None and coherent is True
    return owner, bundle, construction


def _raw(bundle: object):
    return registry_module._RECORDS[bundle]._entry[1]


def _future() -> float:
    return time.monotonic() + 1.0


def test_exact_worker_claim_is_scrubbed_before_fixed_terminal_and_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    raw = _raw(bundle)
    events: list[tuple[str, object]] = []

    def claim_taken(claim: object) -> None:
        assert threading.current_thread() is raw
        assert claim._bundle is bundle
        assert claim._request is owner._request
        assert claim._prepared_destination is owner._receipt_destination
        events.append(("taken", threading.get_ident()))

    def claim_scrubbed(claim: object) -> None:
        assert threading.current_thread() is raw
        assert claim._paths_cleared is True
        assert claim._bundle is None
        assert claim._request is None
        assert claim._prepared_destination is None
        events.append(("scrubbed", threading.get_ident()))

    def terminal_published() -> None:
        terminal = bundle._terminal_destination._read(owner._cleanup_authority._key)
        assert type(terminal) is lifecycle_module._WorkspaceWorkerTerminalReceipt
        events.append(("terminal", threading.get_ident()))

    monkeypatch.setattr(lifecycle_module, "_workspace_worker_claim_taken", claim_taken)
    monkeypatch.setattr(lifecycle_module, "_workspace_worker_claim_scrubbed", claim_scrubbed)
    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_terminal_published",
        terminal_published,
    )

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert type(start) is lifecycle_module._WorkspaceWorkerStartReceipt
    assert coherent is True
    assert type(terminal) is lifecycle_module._WorkspaceWorkerTerminalReceipt
    assert terminal.started is True
    assert joined is True
    assert [event[0] for event in events] == ["taken", "scrubbed", "terminal"]
    assert len({event[1] for event in events}) == 1
    assert events[0][1] != threading.get_ident()
    assert bundle._prepared_destination._read(owner._request) is None
    assert bundle._built_destination._read(owner._cleanup_authority._key) is None
    assert not owner._request._run_root.exists()

    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bundle not in registry_module._RECORDS
    assert raw._target is None and raw._args == () and raw._kwargs == {}
    assert thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle) == (
        None,
        False,
    )
    assert not owner._request._run_root.exists()


def test_cancel_before_start_is_no_effect_terminal_and_releases(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    joined_terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.0,
    )

    assert type(terminal) is lifecycle_module._WorkspaceWorkerTerminalReceipt
    assert terminal.started is False
    assert joined_terminal is terminal and joined is True
    assert raw.ident is None and raw.is_alive() is False
    assert raw._target is None and raw._args == () and raw._kwargs == {}
    assert bridge._raw_ref is control_module._RAW_CLEARED
    assert type(bridge._state) is tuple
    assert bundle._thread_destination._read(owner._cleanup_authority._key) is None
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bridge._state is None
    assert thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    ) == (None, False)


def test_take_rejects_before_start_wrong_thread_and_stale_token(tmp_path: Path) -> None:
    owner, bundle, construction = _graph(tmp_path)
    record = registry_module._RECORDS[bundle]

    assert lifecycle_module._take_workspace_worker_claim(record._record_token) is None
    assert lifecycle_module._take_workspace_worker_claim(object()) is None

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert lifecycle_module._take_workspace_worker_claim(record._record_token) is None


def test_start_deadline_returns_retained_authority_before_late_take(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    entered = threading.Event()
    resume = threading.Event()

    def block_before_take(_record_token: object) -> None:
        entered.set()
        assert resume.wait(1.0)

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_before_take",
        block_before_take,
    )
    started_at = time.monotonic()
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.05,
    )

    assert entered.is_set()
    assert type(start) is lifecycle_module._WorkspaceWorkerStartReceipt
    assert coherent is False
    assert time.monotonic() - started_at < 0.5
    assert bundle._lifecycle._phase == "start-intended"
    assert bundle._lifecycle._handoff_expired is True
    assert bundle._controller._cancellation_requested() is True
    assert bundle in registry_module._RECORDS
    assert thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.0,
    ) == (None, False)

    resume.set()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is True and joined is True
    repeated, repeated_coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    assert repeated is start and repeated_coherent is False
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_start_return_loss_reuses_exact_receipt_and_never_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    control = SystemExit(63)
    original_start = lifecycle_module._THREAD_START
    starts = 0
    lost = False

    def counted_start(raw: object) -> None:
        nonlocal starts
        starts += 1
        return original_start(raw)

    def lose_once() -> None:
        nonlocal lost
        if not lost:
            lost = True
            raise control

    monkeypatch.setattr(lifecycle_module, "_THREAD_START", counted_start)
    monkeypatch.setattr(lifecycle_module, "_workspace_worker_start_returned", lose_once)

    first, first_coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    second, second_coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert starts == 1
    assert first is second is bundle._thread_destination._read(owner._cleanup_authority._key)
    assert first_coherent is False and second_coherent is False
    retained = bundle._controller._control_value()
    assert retained is not None and (retained.kind, retained.code) == ("system-exit", 63)
    assert control.__traceback__ is None
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_concurrent_starts_share_one_base_start_and_one_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    original_start = lifecycle_module._THREAD_START
    count_lock = threading.Lock()
    starts = 0
    barrier = threading.Barrier(8)
    outcomes: list[tuple[object, bool]] = []

    def counted_start(raw: object) -> None:
        nonlocal starts
        with count_lock:
            starts += 1
        return original_start(raw)

    def invoke() -> None:
        barrier.wait()
        outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                _future(),
            )
        )

    monkeypatch.setattr(lifecycle_module, "_THREAD_START", counted_start)
    callers = [threading.Thread(target=invoke) for _ in range(8)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(2.0)

    assert all(not caller.is_alive() for caller in callers)
    assert starts == 1 and len(outcomes) == 8
    assert len({id(receipt) for receipt, _coherent in outcomes}) == 1
    assert all(coherent is True for _receipt, coherent in outcomes)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_active_root_store_return_loss_reconciles_before_one_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    record = registry_module._RECORDS[bundle]
    original_pin = lifecycle_module._pin_workspace_worker_bundle
    original_start = lifecycle_module._THREAD_START
    control = KeyboardInterrupt()
    pin_calls = 0
    starts = 0

    def lose_pin_return(record_token: object, exact_bundle: object) -> None:
        nonlocal pin_calls
        pin_calls += 1
        original_pin(record_token, exact_bundle)
        if pin_calls == 1:
            raise control

    def counted_start(raw: object) -> None:
        nonlocal starts
        starts += 1
        return original_start(raw)

    monkeypatch.setattr(lifecycle_module, "_pin_workspace_worker_bundle", lose_pin_return)
    monkeypatch.setattr(lifecycle_module, "_THREAD_START", counted_start)

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert start is not None and coherent is False
    assert pin_calls == 1 and starts == 1
    assert control.__traceback__ is None
    assert active_module._workspace_worker_active_root_occupied(record._record_token)
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert not active_module._workspace_worker_active_root_occupied(record._record_token)


@pytest.mark.parametrize(
    "control",
    [KeyboardInterrupt(), SystemExit(61)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_active_root_pre_store_control_cancels_without_start_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "pin-pre-store")
    original_start = lifecycle_module._THREAD_START
    pin_calls = 0
    starts = 0

    def fail_before_store(_record_token: object, _bundle: object) -> None:
        nonlocal pin_calls
        pin_calls += 1
        raise control

    def counted_start(raw: object) -> None:
        nonlocal starts
        starts += 1
        return original_start(raw)

    monkeypatch.setattr(lifecycle_module, "_pin_workspace_worker_bundle", fail_before_store)
    monkeypatch.setattr(lifecycle_module, "_THREAD_START", counted_start)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )

    assert (start, coherent) == (None, False)
    assert pin_calls == 1 and starts == 0
    assert control.__traceback__ is None
    assert not active_module._workspace_worker_active_root_occupied(construction._record_token)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is False and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_failed_partial_init_is_property_free_cancelled_and_releases_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph_without_construction(tmp_path / "failed", "failed")
    control = KeyboardInterrupt()

    def fail_partial(candidate: threading.Thread, *args: object, **kwargs: object) -> None:
        del args, kwargs
        candidate._target = object()
        candidate._args = (object(),)
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", fail_partial)
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    assert construction is not None and coherent is False
    monkeypatch.undo()

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None and terminal.started is False
    assert raw._target is None and raw._args == () and raw._kwargs == {}
    assert control.__traceback__ is None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )

    second_owner, second_bundle, second_construction = _graph(tmp_path / "second", "second")
    second_terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
    )
    assert second_terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
        second_terminal,
    )


def test_poisoned_record_without_raw_releases_capacity(tmp_path: Path) -> None:
    owner, bundle = _graph_without_construction(tmp_path / "poisoned", "poisoned")
    binding = registry_module._resolve_workspace_worker_thread_binding(owner, bundle)
    construction = registry_module._poison_workspace_worker_thread(binding)
    record = registry_module._RECORDS[bundle]
    assert record._entry[0] == registry_module._POISONED
    assert record._entry[1] is None

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None and terminal.started is False
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bundle not in registry_module._RECORDS


@pytest.mark.parametrize("store_first", [False, True])
def test_terminal_publish_loss_reconciles_without_raw_excepthook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_first: bool,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    original_publish = state_module._WorkspaceWorkerDestination._publish
    original_publish_before = state_module._WorkspaceWorkerDestination._publish_before
    failures = 0
    escaped: list[object] = []
    control = SystemExit(79)

    def lose_terminal_publish(
        destination: object,
        token: object,
        owner_token: object,
        value: object,
        deadline: float,
    ) -> tuple[object | None, bool]:
        nonlocal failures
        if destination._kind == "terminal" and failures == 0:
            failures += 1
            if store_first:
                original_publish(destination, token, owner_token, value)
            raise control
        return original_publish_before(
            destination,
            token,
            owner_token,
            value,
            deadline,
        )

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_publish_before",
        lose_terminal_publish,
    )
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args.thread))

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert start is not None and coherent is True
    assert failures == 1 and escaped == []
    assert control.__traceback__ is None
    assert terminal is bundle._terminal_destination._read(owner._cleanup_authority._key)
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_caller_reconciles_exhausted_worker_terminal_publish_and_reuses_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path / "first", "first")
    original_publish_before = state_module._WorkspaceWorkerDestination._publish_before
    terminal_attempts = 0
    escaped: list[object] = []

    def fail_worker_terminal(
        destination: object,
        token: object,
        owner_token: object,
        value: object,
        deadline: float,
    ) -> tuple[object | None, bool]:
        nonlocal terminal_attempts
        if destination._kind == "terminal":
            terminal_attempts += 1
            raise RuntimeError("synthetic pre-store loss")
        return original_publish_before(
            destination,
            token,
            owner_token,
            value,
            deadline,
        )

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_publish_before",
        fail_worker_terminal,
    )
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args.thread))
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.05,
    )

    assert start is not None and coherent is False
    assert terminal_attempts == 3 and escaped == []
    assert bundle._lifecycle._phase == "terminal-pending"
    assert bundle._terminal_destination._read(owner._cleanup_authority._key) is None
    assert bundle in registry_module._RECORDS

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_publish_before",
        original_publish_before,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )

    second_owner, second_bundle, second_construction = _graph(tmp_path / "second", "second")
    second_terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
    )
    assert second_terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
        second_terminal,
    )


def test_terminal_hook_holds_no_registry_lock_and_join_deadline_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    raw = _raw(bundle)
    hook_entered = threading.Event()
    resume = threading.Event()

    def block_after_terminal() -> None:
        hook_entered.set()
        assert resume.wait(1.0)

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_terminal_published",
        block_after_terminal,
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    assert start is not None and coherent is True and hook_entered.is_set()

    object.__setattr__(raw, "is_alive", lambda: False)
    started_at = time.monotonic()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.05,
    )
    elapsed = time.monotonic() - started_at
    assert terminal is not None and joined is False
    assert elapsed < 0.2
    assert (
        thread_module._release_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            terminal,
        )
        is False
    )

    object.__delattr__(raw, "is_alive")
    resume.set()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_release_orders_terminal_join_scrub_delete_and_recovers_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    raw = _raw(bundle)
    _start, _coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    events: list[str] = []
    control = SystemExit(81)

    def scrubbed() -> None:
        assert bundle in registry_module._RECORDS
        assert raw._target is None and raw._args == () and raw._kwargs == {}
        assert bundle._terminal_destination._read(owner._cleanup_authority._key) is terminal
        events.append("scrubbed")

    def released() -> None:
        assert bundle not in registry_module._RECORDS
        assert bundle._lifecycle._release_phase == "complete"
        events.append("released")
        raise control

    monkeypatch.setattr(release_module, "_workspace_worker_thread_scrubbed", scrubbed)
    monkeypatch.setattr(release_module, "_workspace_worker_record_released", released)

    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert events == ["scrubbed", "released"]
    assert control.__traceback__ is None
    assert not active_module._workspace_worker_active_root_occupied(construction._record_token)


def test_tokens_values_and_coordinator_reject_equality_copy_pickle_and_paths(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    args = vars(raw)["_args"]
    bridge = record._control_bridge
    assert type(args) is tuple and len(args) == 1 and args[0] is bridge
    assert type(bridge) is control_module._WorkspaceWorkerControlBridge
    assert bridge._matches(
        record._record_token,
        record._control_token,
        bundle._controller,
    )
    assert type(bridge._raw_ref) is weakref.ReferenceType
    assert bridge._raw_ref() is raw
    assert "_bundle" not in bridge.__slots__ and "_request" not in bridge.__slots__
    assert not any(
        isinstance(
            getattr(bridge, name),
            (
                Path,
                workspace_module._RelayLinuxBuildWorkspaceRequest,
                state_module._WorkspaceWorkerBundle,
            ),
        )
        for name in bridge.__slots__
    )

    equality_calls = 0

    class HostileEquality:
        def __eq__(self, _other: object) -> bool:
            nonlocal equality_calls
            equality_calls += 1
            raise AssertionError("identity validation must not call equality")

    object.__setattr__(raw, "_args", (HostileEquality(),))
    assert not registry_module._thread_initialization_is_complete(
        raw,
        record._record_token,
        bridge,
        record._control_token,
        bundle._controller,
    )
    assert equality_calls == 0
    object.__setattr__(raw, "_args", (bridge,))

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    coordinator = bundle._lifecycle
    assert start is not None and coherent is True
    assert terminal is not None and joined is True
    assert type(bridge._state) is tuple
    for value in (start, terminal, coordinator, bridge):
        assert not value
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(AttributeError):
            value.changed = True
    assert not hasattr(start, "_bundle")
    forbidden = (
        Path,
        workspace_module._RelayLinuxBuildWorkspaceRequest,
        workspace_module._WorkspacePreparationReceiptDestination,
        state_module._WorkspaceWorkerBundle,
        lifecycle_module._WorkspaceWorkerClaim,
    )
    for name in coordinator.__slots__:
        assert not isinstance(getattr(coordinator, name), forbidden)
    assert type(record._lifecycle) is lifecycle_module._WorkspaceWorkerCoordinator
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bridge._state is None and bridge._raw_ref is control_module._RAW_CLEARED


def test_control_bridge_rejects_wrong_thread_capture_and_target_invocation(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "wrong-thread-control-bridge")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge
    control = KeyboardInterrupt()

    def spoof_exact_attributes() -> None:
        current = threading.current_thread()
        for field in ("_args", "_kwargs", "_target", "_workspace_control_token"):
            object.__setattr__(current, field, vars(raw)[field])
        object.__setattr__(current, "_workspace_sealed", True)
        control_module._capture_worker_control(bridge, control)
        control_module._inert_workspace_worker_target(bridge)

    spoof = threading.Thread(target=spoof_exact_attributes)
    spoof.start()
    spoof.join(1.0)

    assert not spoof.is_alive()
    assert control.__traceback__ is None
    assert bundle._controller._control_value() is None
    assert bundle._controller._cancellation_requested() is False
    assert type(bridge._state) is tuple
    assert type(bridge._raw_ref) is weakref.ReferenceType
    assert bridge._raw_ref() is raw
    assert vars(raw)["_started"].is_set() is False

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert start is not None and coherent is True
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bridge._state is None and bridge._raw_ref is control_module._RAW_CLEARED


def test_control_bridge_bind_return_loss_reuses_exact_raw_before_one_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph_without_construction(tmp_path, "bridge-bind-return-loss")
    original_init = threading.Thread.__init__
    control = SystemExit(183)
    binds = 0
    inits = 0

    def lose_bind_return() -> None:
        nonlocal binds
        binds += 1
        if binds == 1:
            raise control

    def counted_init(candidate: object, *args: object, **kwargs: object) -> None:
        nonlocal inits
        inits += 1
        original_init(candidate, *args, **kwargs)

    monkeypatch.setattr(
        control_module,
        "_workspace_worker_control_bridge_bound",
        lose_bind_return,
    )
    monkeypatch.setattr(threading.Thread, "__init__", counted_init)
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge

    assert construction is not None and coherent is False
    assert binds == 2 and inits == 1
    assert type(bridge._raw_ref) is weakref.ReferenceType
    assert bridge._raw_ref() is raw
    assert vars(raw)["_workspace_control_token"] is bridge._state[2]
    bridge._bind_raw(raw)

    class Other:
        pass

    with pytest.raises(TypeError):
        bridge._bind_raw(Other())
    retained = bundle._controller._control_value()
    assert retained is not None and (retained.kind, retained.code) == ("system-exit", 183)
    assert control.__traceback__ is None

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None and terminal.started is False
    assert bridge._raw_ref is control_module._RAW_CLEARED
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bridge._state is None


def test_cross_controller_bridge_wiring_fails_exact_record_and_config_proofs(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "cross-controller-bridge")
    binding = registry_module._resolve_workspace_worker_thread_binding(owner, bundle)
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge
    owner_token = record._owner_token
    rogue = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner_token,
    )
    forged = control_module._new_workspace_worker_control_bridge(
        record._record_token,
        record._control_token,
        owner_token,
        rogue,
    )
    original_args = vars(raw)["_args"]
    original_worker_token = vars(raw)["_workspace_control_token"]
    object.__setattr__(record, "_control_bridge", forged)
    object.__setattr__(raw, "_args", (forged,))
    object.__setattr__(raw, "_workspace_control_token", forged._state[2])

    assert not record._matches(binding)
    assert not registry_module._thread_initialization_is_complete(
        raw,
        record._record_token,
        forged,
        record._control_token,
        bundle._controller,
    )

    object.__setattr__(record, "_control_bridge", bridge)
    object.__setattr__(raw, "_args", original_args)
    object.__setattr__(raw, "_workspace_control_token", original_worker_token)
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("_args", ()),
        ("_target", object()),
        ("_workspace_sealed", False),
        ("_workspace_control_token", object()),
        ("run", lambda: None),
        ("start", lambda: None),
        ("join", lambda: None),
        ("is_alive", lambda: False),
    ],
    ids=("args", "target", "seal", "control-token", "run", "start", "join", "is-alive"),
)
def test_pre_start_raw_configuration_drift_is_no_effect_releasable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    owner, bundle, construction = _graph(
        tmp_path,
        f"raw-drift-{field[1:].replace('_', '-')}",
    )
    raw = _raw(bundle)
    starts = 0
    escaped_threads: list[threading.Thread] = []

    def forbidden_start(_raw: object) -> None:
        nonlocal starts
        starts += 1

    object.__setattr__(raw, field, replacement)
    monkeypatch.setattr(lifecycle_module, "_THREAD_START", forbidden_start)
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: escaped_threads.append(args.thread),
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert (start, coherent) == (None, False)
    assert starts == 0 and escaped_threads == []
    assert terminal is not None and terminal.started is False and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    second_owner, second_bundle, second_construction = _graph(
        tmp_path / "second",
        "raw-drift-reuse",
    )
    second_terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
    )
    assert second_terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
        second_terminal,
    )


def test_destination_same_value_conflict_cross_owner_and_concurrent_publish(
    tmp_path: Path,
) -> None:
    first_owner, first_bundle = _graph_without_construction(tmp_path / "first", "first")
    second_owner, _second_bundle = _graph_without_construction(
        tmp_path / "second",
        "second",
    )
    first_token = first_owner._cleanup_authority._key
    second_token = second_owner._cleanup_authority._key
    first = lifecycle_module._WorkspaceWorkerStartReceipt(
        lifecycle_module._START_TOKEN,
        owner_token=first_token,
        record_token=object(),
    )
    conflict = lifecycle_module._WorkspaceWorkerStartReceipt(
        lifecycle_module._START_TOKEN,
        owner_token=first_token,
        record_token=object(),
    )
    destination = first_bundle._thread_destination

    assert destination._publish(state_module._DESTINATION_TOKEN, first_token, first) is first
    assert destination._publish(state_module._DESTINATION_TOKEN, first_token, first) is first
    with pytest.raises(TypeError):
        destination._publish(state_module._DESTINATION_TOKEN, first_token, conflict)
    with pytest.raises(TypeError):
        destination._publish(state_module._DESTINATION_TOKEN, second_token, first)
    with pytest.raises(TypeError):
        first_bundle._built_destination._publish(
            state_module._DESTINATION_TOKEN,
            first_token,
            first,
        )

    third_owner, third_bundle = _graph_without_construction(tmp_path / "third", "third")
    third_token = third_owner._cleanup_authority._key
    candidates = [
        lifecycle_module._WorkspaceWorkerStartReceipt(
            lifecycle_module._START_TOKEN,
            owner_token=third_token,
            record_token=object(),
        )
        for _index in range(2)
    ]
    outcomes: list[object] = []

    def publish(candidate: object) -> None:
        try:
            outcomes.append(
                third_bundle._thread_destination._publish(
                    state_module._DESTINATION_TOKEN,
                    third_token,
                    candidate,
                )
            )
        except TypeError as error:
            outcomes.append(error)

    callers = [threading.Thread(target=publish, args=(candidate,)) for candidate in candidates]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(1.0)
    stored = third_bundle._thread_destination._read(third_token)
    assert stored in candidates
    assert sum(value is stored for value in outcomes) == 1
    assert sum(type(value) is TypeError for value in outcomes) == 1


@pytest.mark.parametrize("clear_first", [False, True])
def test_release_reconciles_active_root_clear_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clear_first: bool,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    _start, _coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    original_clear = release_module._release_workspace_worker_pin
    control = SystemExit(91)
    calls = 0

    def lose_clear(record_token: object, exact_bundle: object) -> None:
        nonlocal calls
        calls += 1
        if clear_first:
            original_clear(record_token, exact_bundle)
        if calls == 1:
            raise control
        original_clear(record_token, exact_bundle)

    monkeypatch.setattr(release_module, "_release_workspace_worker_pin", lose_clear)
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert calls == 2
    assert control.__traceback__ is None
    assert bundle._lifecycle._release_phase == "complete"
    assert bundle not in registry_module._RECORDS
    assert not active_module._workspace_worker_active_root_occupied(construction._record_token)


@pytest.mark.parametrize(
    "control",
    [KeyboardInterrupt(), SystemExit(182)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_bridge_clear_control_is_captured_and_release_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "bridge-clear-control")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge
    _start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert coherent is True and terminal is not None and joined is True
    assert type(bridge._state) is tuple
    original_clear = control_module._WorkspaceWorkerControlBridge._clear
    clears = 0

    def clear_then_interrupt(value: object, raw_value: object) -> None:
        nonlocal clears
        clears += 1
        original_clear(value, raw_value)
        if clears == 1:
            raise control

    monkeypatch.setattr(
        control_module._WorkspaceWorkerControlBridge,
        "_clear",
        clear_then_interrupt,
    )

    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    retained = bundle._controller._control_value()
    assert clears == 2 and bridge._state is None
    assert raw._target is None and raw._args == () and raw._workspace_control_token is None
    assert retained is not None
    assert retained.kind == ("keyboard" if type(control) is KeyboardInterrupt else "system-exit")
    assert control.__traceback__ is None
    assert bundle not in registry_module._RECORDS


def test_join_clock_controls_are_fifo_captured_scrubbed_and_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    _start, _coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    first = SystemExit(97)
    nested = KeyboardInterrupt()
    original_monotonic = time.monotonic
    controls: list[KeyboardInterrupt | SystemExit] = [first, nested]

    def interrupted_clock() -> float:
        if controls:
            raise controls.pop(0)
        return original_monotonic()

    monkeypatch.setattr(
        thread_module,
        "time",
        SimpleNamespace(monotonic=interrupted_clock),
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert terminal is not None and joined is True
    retained = bundle._controller._control_value()
    assert retained is not None and (retained.kind, retained.code) == ("system-exit", 97)
    assert first.__traceback__ is None and nested.__traceback__ is None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_start_effect_wins_cancel_after_entry_without_taking_late_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    before_take = threading.Event()
    resume = threading.Event()
    outcomes: list[tuple[object, bool]] = []

    def block_before_take(_record_token: object) -> None:
        before_take.set()
        assert resume.wait(1.0)

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_before_take",
        block_before_take,
    )
    starter = threading.Thread(
        target=lambda: outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                _future(),
            )
        )
    )
    starter.start()
    assert before_take.wait(1.0)
    cancelled = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert cancelled is None
    resume.set()
    starter.join(2.0)

    assert not starter.is_alive() and len(outcomes) == 1
    start, coherent = outcomes[0]
    assert start is not None and coherent is False
    assert bundle._lifecycle._claim_token is None
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is True and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_concurrent_reconstruction_cannot_detach_record_during_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path)
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    record = registry_module._RECORDS[bundle]
    original_advance = registry_module._WorkspaceWorkerThreadRecord._advance
    entered = threading.Event()
    resume = threading.Event()
    blocked = False
    reconstruction: list[object] = []
    released: list[bool] = []
    release_done = threading.Event()

    def block_advance(self: object, binding: object):
        nonlocal blocked
        if self is record and not blocked:
            blocked = True
            entered.set()
            assert resume.wait(1.0)
        return original_advance(self, binding)

    monkeypatch.setattr(
        registry_module._WorkspaceWorkerThreadRecord,
        "_advance",
        block_advance,
    )
    constructor = threading.Thread(
        target=lambda: reconstruction.append(
            thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
        )
    )
    constructor.start()
    assert entered.wait(1.0)

    def release_once() -> None:
        released.append(
            thread_module._release_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                terminal,
            )
        )
        release_done.set()

    releaser = threading.Thread(target=release_once)
    releaser.start()
    assert release_done.wait(0.2)
    assert released == [False] and registry_module._RECORDS.get(bundle) is record
    resume.set()
    constructor.join(2.0)
    releaser.join(2.0)

    assert not constructor.is_alive() and not releaser.is_alive()
    assert reconstruction == [(None, False)]
    assert released == [False]
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bundle not in registry_module._RECORDS


def test_poison_admission_honors_path_free_active_root_capacity(
    tmp_path: Path,
) -> None:
    def leave_path_free_active_record() -> tuple[weakref.ReferenceType[object], object]:
        owner, bundle, construction = _graph(tmp_path, "old-active-root")
        start, coherent = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            _future(),
        )
        assert start is not None and coherent is True
        terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            1.0,
        )
        assert terminal is not None and joined is True
        return weakref.ref(bundle), construction._record_token

    old_bundle_ref, old_record_token = leave_path_free_active_record()
    gc.collect()
    assert old_bundle_ref() is None
    assert len(registry_module._RECORDS) == 0
    assert active_module._workspace_worker_active_root_occupied(old_record_token)

    owner, bundle = _graph_without_construction(tmp_path, "new-poison")
    binding = registry_module._resolve_workspace_worker_thread_binding(owner, bundle)
    with pytest.raises(RuntimeError, match="registry is occupied"):
        registry_module._poison_workspace_worker_thread(binding)
    assert bundle not in registry_module._RECORDS

    active_module._transfer_workspace_worker_bundle(old_record_token, bundle)
    poisoned = registry_module._poison_workspace_worker_thread(binding)
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        poisoned,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        poisoned,
        terminal,
    )


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("publish failed"), KeyboardInterrupt(), SystemExit(73)],
    ids=("runtime-error", "keyboard-interrupt", "system-exit"),
)
def test_worker_target_shields_persistent_terminal_publication_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "publish-fault")
    escaped_threads: list[threading.Thread] = []

    def fail_publish(*_args: object) -> bool:
        raise failure

    with monkeypatch.context() as scoped:
        scoped.setattr(lifecycle_module, "_publish_terminal_candidate", fail_publish)
        scoped.setattr(
            threading,
            "excepthook",
            lambda args: escaped_threads.append(args.thread),
        )
        start, coherent = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            time.monotonic() + 0.05,
        )

    assert start is not None and coherent is False
    assert escaped_threads == []
    assert failure.__traceback__ is None
    assert bundle._lifecycle._phase == "terminal-pending"
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize(
    "control",
    [KeyboardInterrupt(), SystemExit(74)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_post_claim_control_uses_exact_path_free_active_record_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "active-control-fallback")
    removed_records: list[object] = []
    escaped_threads: list[threading.Thread] = []
    publish_attempted = threading.Event()

    def drop_weak_record_after_scrub(claim: object) -> None:
        assert claim._paths_cleared is True and claim._bundle is None
        with registry_module._REGISTRY_LOCK:
            removed_records.append(registry_module._RECORDS.pop(bundle))
        gc.collect()
        active = active_module._ACTIVE_ROOT
        assert active is not None and active._bundle is None

    def fail_terminal_commit(*_args: object) -> bool:
        publish_attempted.set()
        raise control

    with monkeypatch.context() as scoped:
        scoped.setattr(
            lifecycle_module,
            "_workspace_worker_claim_scrubbed",
            drop_weak_record_after_scrub,
        )
        scoped.setattr(
            lifecycle_module,
            "_publish_terminal_candidate",
            fail_terminal_commit,
        )
        scoped.setattr(
            threading,
            "excepthook",
            lambda args: escaped_threads.append(args.thread),
        )
        start, coherent = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            time.monotonic() + 0.05,
        )
        assert publish_attempted.wait(1.0)

    assert start is not None and coherent is False
    assert len(removed_records) == 1 and bundle not in registry_module._RECORDS
    assert escaped_threads == [] and control.__traceback__ is None
    retained = bundle._controller._control_value()
    expected = "keyboard" if type(control) is KeyboardInterrupt else "system-exit"
    assert retained is not None and retained.kind == expected
    with registry_module._REGISTRY_LOCK:
        registry_module._RECORDS[bundle] = removed_records[0]
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("nested_system_exit", [False, True])
def test_join_persistent_clock_controls_have_an_independent_attempt_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nested_system_exit: bool,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "clock-cap")
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    assert start is not None and coherent is True
    controls: list[KeyboardInterrupt | SystemExit] = []
    calls = 0

    def broken_clock() -> float:
        nonlocal calls
        calls += 1
        control: KeyboardInterrupt | SystemExit
        if nested_system_exit and calls > 1:
            control = SystemExit(80 + calls)
        else:
            control = KeyboardInterrupt()
        controls.append(control)
        raise control

    with monkeypatch.context() as scoped:
        scoped.setattr(
            thread_module,
            "time",
            SimpleNamespace(monotonic=broken_clock),
        )
        terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            1.0,
        )

    assert (terminal, joined) == (None, False)
    assert calls == thread_module._MAX_FACADE_CONTROL_ATTEMPTS
    retained = bundle._controller._control_value()
    assert retained is not None and retained.kind == "keyboard"
    assert bundle._controller._cancellation_requested()
    assert all(control.__traceback__ is None for control in controls)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_start_persistent_clock_controls_retain_no_effect_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "start-clock-cap")
    controls: list[KeyboardInterrupt | SystemExit] = [
        SystemExit(91),
        KeyboardInterrupt(),
        SystemExit(92),
        KeyboardInterrupt(),
    ]
    raised: list[KeyboardInterrupt | SystemExit] = []
    calls = 0

    def broken_clock() -> float:
        nonlocal calls
        calls += 1
        control = controls.pop(0)
        raised.append(control)
        raise control

    with monkeypatch.context() as scoped:
        scoped.setattr(
            thread_module,
            "time",
            SimpleNamespace(monotonic=broken_clock),
        )
        start, coherent = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            _future(),
        )

    assert (start, coherent) == (None, False)
    assert calls == thread_module._MAX_FACADE_CONTROL_ATTEMPTS
    assert len(controls) == 1
    retained = bundle._controller._control_value()
    assert retained is not None and (retained.kind, retained.code) == (
        "system-exit",
        91,
    )
    assert bundle._controller._cancellation_requested()
    assert all(control.__traceback__ is None for control in raised)
    raw = _raw(bundle)
    assert not vars(raw)["_started"].is_set()
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None and terminal.started is False
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_construct_persistent_controls_are_bounded_and_fifo_latched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph_without_construction(tmp_path, "construct-control-cap")
    controls: list[KeyboardInterrupt | SystemExit] = []
    calls = 0

    def interrupted_advance(_binding: object) -> None:
        nonlocal calls
        calls += 1
        control = KeyboardInterrupt() if calls == 1 else SystemExit(100 + calls)
        controls.append(control)
        raise control

    started_at = time.monotonic()
    monkeypatch.setattr(
        thread_module,
        "_advance_workspace_worker_thread",
        interrupted_advance,
    )
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )

    assert (construction, coherent) == (None, False)
    assert 1 <= calls <= thread_module._MAX_FACADE_CONTROL_ATTEMPTS
    assert time.monotonic() - started_at < 0.5
    retained = bundle._controller._control_value()
    assert retained is not None and retained.kind == "keyboard"
    assert bundle._controller._cancellation_requested()
    assert all(control.__traceback__ is None for control in controls)
    assert bundle not in registry_module._RECORDS


def test_cancel_persistent_controls_are_bounded_with_record_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "cancel-control-cap")
    controls: list[KeyboardInterrupt | SystemExit] = []
    calls = 0

    def interrupted_cancel(*_args: object) -> None:
        nonlocal calls
        calls += 1
        control = KeyboardInterrupt() if calls == 1 else SystemExit(110 + calls)
        controls.append(control)
        raise control

    started_at = time.monotonic()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            thread_module,
            "_cancel_workspace_worker_thread_before_start",
            interrupted_cancel,
        )
        terminal = thread_module._cancel_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
        )

    assert terminal is None
    assert calls == thread_module._MAX_FACADE_CONTROL_ATTEMPTS
    assert time.monotonic() - started_at < 0.5
    assert bundle in registry_module._RECORDS
    assert all(control.__traceback__ is None for control in controls)
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_release_persistent_controls_are_bounded_without_false_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "release-control-cap")
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    controls: list[KeyboardInterrupt | SystemExit] = []
    calls = 0

    def interrupted_release(*_args: object) -> bool:
        nonlocal calls
        calls += 1
        control = KeyboardInterrupt() if calls == 1 else SystemExit(120 + calls)
        controls.append(control)
        raise control

    started_at = time.monotonic()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            thread_module,
            "_release_workspace_worker_thread",
            interrupted_release,
        )
        released = thread_module._release_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            terminal,
        )

    assert released is False
    assert calls == thread_module._MAX_FACADE_CONTROL_ATTEMPTS
    assert time.monotonic() - started_at < 0.5
    assert bundle in registry_module._RECORDS
    assert bundle._lifecycle._release_phase != "complete"
    assert all(control.__traceback__ is None for control in controls)
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_held_start_destination_respects_deadline_without_registry_locking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "held-start-slot")
    record = registry_module._RECORDS[bundle]
    destination = bundle._thread_destination
    original_start = lifecycle_module._THREAD_START
    base_start_entered = threading.Event()
    outcomes: list[tuple[object, bool]] = []
    starts = 0

    def counted_start(raw: object) -> None:
        nonlocal starts
        starts += 1
        base_start_entered.set()
        return original_start(raw)

    monkeypatch.setattr(lifecycle_module, "_THREAD_START", counted_start)
    destination._lock.acquire()
    started_at = time.monotonic()
    starter = threading.Thread(
        target=lambda: outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                time.monotonic() + 0.05,
            )
        )
    )
    try:
        starter.start()
        assert base_start_entered.wait(1.0)
        assert registry_module._REGISTRY_LOCK.acquire(timeout=0.05)
        try:
            assert record._lock.acquire(timeout=0.05)
            record._lock.release()
        finally:
            registry_module._REGISTRY_LOCK.release()
        starter.join(0.5)
        assert not starter.is_alive()
    finally:
        destination._lock.release()

    assert time.monotonic() - started_at < 0.3
    assert starts == 1 and len(outcomes) == 1
    start, coherent = outcomes[0]
    assert start is not None and coherent is False
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_held_terminal_destination_consumes_one_join_deadline_unlocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "held-terminal-slot")
    raw = _raw(bundle)
    record = registry_module._RECORDS[bundle]
    destination = bundle._terminal_destination
    original_publish_before = state_module._WorkspaceWorkerDestination._publish_before
    terminal_publish_entered = threading.Event()

    def mark_terminal_publish(
        exact: object,
        token: object,
        owner_token: object,
        value: object,
        deadline: float,
    ) -> tuple[object | None, bool]:
        if exact is destination:
            terminal_publish_entered.set()
        return original_publish_before(exact, token, owner_token, value, deadline)

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_publish_before",
        mark_terminal_publish,
    )
    destination._lock.acquire()
    join_outcomes: list[tuple[object, bool, float]] = []
    try:
        start, coherent = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            time.monotonic() + 0.05,
        )
        assert start is not None and coherent is False
        threading.Thread.join(raw, 0.5)
        assert not threading.Thread.is_alive(raw)
        terminal_publish_entered.clear()

        def join_once() -> None:
            started_at = time.monotonic()
            terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                0.05,
            )
            join_outcomes.append((terminal, joined, time.monotonic() - started_at))

        joiner = threading.Thread(target=join_once)
        joiner.start()
        assert terminal_publish_entered.wait(1.0)
        assert registry_module._REGISTRY_LOCK.acquire(timeout=0.05)
        try:
            assert record._lock.acquire(timeout=0.05)
            record._lock.release()
        finally:
            registry_module._REGISTRY_LOCK.release()
        joiner.join(0.5)
        assert not joiner.is_alive()
    finally:
        destination._lock.release()

    assert len(join_outcomes) == 1
    terminal, joined, elapsed = join_outcomes[0]
    assert terminal is None and joined is False and elapsed < 0.2
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_start_persistent_ordinary_faults_after_effect_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "start-error-cap")
    original_start = thread_module._start_workspace_worker_thread
    before_take = threading.Event()
    resume = threading.Event()
    calls = 0

    def block_before_take(_record_token: object) -> None:
        before_take.set()
        assert resume.wait(1.0)

    def persist_after_effect(*args: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            original_start(*args)
        raise RuntimeError("persistent start reconciliation fault")

    monkeypatch.setattr(lifecycle_module, "_workspace_worker_before_take", block_before_take)
    monkeypatch.setattr(
        thread_module,
        "_start_workspace_worker_thread",
        persist_after_effect,
    )
    started_at = time.monotonic()
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.05,
    )

    assert before_take.is_set()
    assert (start, coherent) == (None, False)
    assert calls == 2 and time.monotonic() - started_at < 0.3
    assert bundle in registry_module._RECORDS
    resume.set()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_cancel_persistent_ordinary_faults_after_effect_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "cancel-error-cap")
    before_take = threading.Event()
    resume = threading.Event()

    def block_before_take(_record_token: object) -> None:
        before_take.set()
        assert resume.wait(1.0)

    monkeypatch.setattr(lifecycle_module, "_workspace_worker_before_take", block_before_take)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.05,
    )
    assert before_take.is_set() and start is not None and coherent is False
    calls = 0

    def persistent_cancel(*_args: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("persistent cancel reconciliation fault")

    monkeypatch.setattr(
        thread_module,
        "_cancel_workspace_worker_thread_before_start",
        persistent_cancel,
    )
    started_at = time.monotonic()
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is None
    assert calls == 2 and time.monotonic() - started_at < 0.3
    assert bundle in registry_module._RECORDS
    resume.set()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("held_lock", ["registry", "record"])
def test_dead_scrubbed_worker_settlement_is_caller_recoverable_after_lock_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    held_lock: str,
) -> None:
    owner, bundle, construction = _graph(tmp_path, f"settlement-{held_lock}")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    claim_scrubbed = threading.Event()
    allow_settlement = threading.Event()
    start_outcomes: list[tuple[object, bool]] = []

    def block_after_claim_scrub(claim: object) -> None:
        assert claim._paths_cleared is True
        assert claim._bundle is None
        claim_scrubbed.set()
        assert allow_settlement.wait(1.0)

    def start_once() -> None:
        start_outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                time.monotonic() + 0.15,
            )
        )

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_claim_scrubbed",
        block_after_claim_scrub,
    )
    starter = threading.Thread(target=start_once)
    starter.start()
    assert claim_scrubbed.wait(1.0)

    lock = registry_module._REGISTRY_LOCK if held_lock == "registry" else record._lock
    assert lock.acquire(timeout=0.1)
    try:
        allow_settlement.set()
        threading.Thread.join(raw, 0.5)
        assert not threading.Thread.is_alive(raw)
    finally:
        lock.release()
    starter.join(0.5)
    assert not starter.is_alive()
    assert start_outcomes and start_outcomes[0][1] is False
    assert record._lifecycle._phase == "claimed"
    assert record._lifecycle._terminal is None

    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is True and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )

    second_owner, second_bundle, second_construction = _graph(
        tmp_path,
        f"settlement-{held_lock}-reuse",
    )
    second_terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
    )
    assert second_terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        second_owner,
        second_bundle,
        second_construction,
        second_terminal,
    )


@pytest.mark.parametrize("held_lock", ["registry", "record"])
@pytest.mark.parametrize(
    "control",
    [KeyboardInterrupt(), SystemExit(117)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_pre_take_control_and_dead_worker_recover_without_weak_registry_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    held_lock: str,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    owner, bundle, construction = _graph(tmp_path, f"pre-take-{held_lock}")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    before_take = threading.Event()
    resume = threading.Event()
    outcomes: list[tuple[object, bool]] = []

    def interrupt_before_take(_record_token: object) -> None:
        coordinator = record._lifecycle
        assert coordinator._phase == "start-intended"
        assert type(coordinator._settlement_token) is object
        assert coordinator._claim_token is None
        before_take.set()
        assert resume.wait(1.0)
        raise control

    def start_once() -> None:
        outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                time.monotonic() + 0.2,
            )
        )

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_before_take",
        interrupt_before_take,
    )
    starter = threading.Thread(target=start_once)
    starter.start()
    assert before_take.wait(1.0)
    lock = registry_module._REGISTRY_LOCK if held_lock == "registry" else record._lock
    assert lock.acquire(timeout=0.1)
    try:
        resume.set()
        threading.Thread.join(raw, 0.5)
        assert not threading.Thread.is_alive(raw)
    finally:
        lock.release()
    starter.join(0.5)
    assert not starter.is_alive() and outcomes and outcomes[0][1] is False
    assert control.__traceback__ is None
    retained = bundle._controller._control_value()
    assert retained is not None
    assert retained.kind == ("keyboard" if type(control) is KeyboardInterrupt else "system-exit")
    assert record._lifecycle._terminal is None

    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is True and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize(
    "control",
    [KeyboardInterrupt(), SystemExit(181)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_pre_take_control_bridge_ignores_simultaneous_registry_and_root_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "pre-take-two-lock-control")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    bridge = record._control_bridge
    before_take = threading.Event()
    resume = threading.Event()
    outcomes: list[tuple[object, bool]] = []
    escaped: list[threading.ExceptHookArgs] = []

    def interrupt_before_take(_record_token: object) -> None:
        before_take.set()
        assert resume.wait(1.0)
        raise control

    def start_once() -> None:
        outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                time.monotonic() + 0.2,
            )
        )

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_before_take",
        interrupt_before_take,
    )
    monkeypatch.setattr(threading, "excepthook", escaped.append)
    starter = threading.Thread(target=start_once)
    starter.start()
    assert before_take.wait(1.0)
    assert active_module._ROOTS_LOCK.acquire(timeout=0.1)
    assert registry_module._REGISTRY_LOCK.acquire(timeout=0.1)
    try:
        resume.set()
        threading.Thread.join(raw, 0.5)
        assert not threading.Thread.is_alive(raw)
    finally:
        registry_module._REGISTRY_LOCK.release()
        active_module._ROOTS_LOCK.release()
    starter.join(1.0)

    assert not starter.is_alive() and outcomes and outcomes[0][1] is False
    assert not escaped and control.__traceback__ is None
    retained = bundle._controller._control_value()
    assert retained is not None
    assert retained.kind == ("keyboard" if type(control) is KeyboardInterrupt else "system-exit")
    assert bundle._controller._cancellation_requested()
    assert type(bridge._state) is tuple
    assert record._lifecycle._terminal is None

    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and terminal.started is True and joined is True
    assert type(bridge._state) is tuple
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bridge._state is None


def test_held_active_root_and_owner_slots_bound_facades_and_retry_cleanly(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "held-private-slots")
    raw = _raw(bundle)

    assert active_module._ROOTS_LOCK.acquire(timeout=0.1)
    try:
        started_at = time.monotonic()
        outcome = thread_module._start_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            time.monotonic() + 0.05,
        )
        assert outcome == (None, False)
        assert time.monotonic() - started_at < 0.15
        assert not vars(raw)["_started"].is_set()
    finally:
        active_module._ROOTS_LOCK.release()

    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    slot = owner._worker_bundle_destination
    assert slot._lock.acquire(timeout=0.1)
    try:
        started_at = time.monotonic()
        assert not thread_module._release_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            terminal,
        )
        assert time.monotonic() - started_at < 0.15
    finally:
        slot._lock.release()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_effect_phase_tamper_cannot_release_a_live_initialized_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "live-release-tamper")
    raw = _raw(bundle)
    terminal_published = threading.Event()
    finish_worker = threading.Event()

    def block_after_terminal() -> None:
        terminal_published.set()
        assert finish_worker.wait(1.0)

    monkeypatch.setattr(
        lifecycle_module,
        "_workspace_worker_terminal_published",
        block_after_terminal,
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        _future(),
    )
    assert start is not None and coherent is True and terminal_published.wait(1.0)
    terminal = bundle._terminal_destination._read(owner._cleanup_authority._key)
    coordinator = registry_module._RECORDS[bundle]._lifecycle
    object.__setattr__(coordinator, "_effect_phase", "none")
    assert threading.Thread.is_alive(raw)
    assert not thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bundle in registry_module._RECORDS

    object.__setattr__(coordinator, "_effect_phase", "returned")
    finish_worker.set()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_dead_worker_missing_settlement_token_fails_closed_then_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "missing-settlement-token")
    raw = _raw(bundle)

    def fail_before_terminal(*_args: object) -> None:
        raise RuntimeError("terminal settlement unavailable")

    monkeypatch.setattr(
        lifecycle_module,
        "_publish_workspace_worker_terminal_for_token",
        fail_before_terminal,
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.05,
    )
    threading.Thread.join(raw, 0.5)
    assert start is not None and coherent is False
    assert not threading.Thread.is_alive(raw)
    coordinator = registry_module._RECORDS[bundle]._lifecycle
    settlement_token = coordinator._settlement_token
    assert coordinator._phase == "claimed"
    assert coordinator._claim_token is settlement_token
    assert coordinator._terminal is None

    object.__setattr__(coordinator, "_settlement_token", None)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.1,
    )
    assert terminal is None and joined is False
    assert coordinator._terminal is None

    object.__setattr__(coordinator, "_settlement_token", settlement_token)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_pending_terminal_effect_tamper_cannot_clear_live_active_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "pending-live-tamper")
    record = registry_module._RECORDS[bundle]
    raw = record._entry[1]
    original_publish = lifecycle_module._publish_terminal_candidate
    worker_publish = threading.Event()
    resume_worker = threading.Event()
    start_outcomes: list[tuple[object, bool]] = []

    def block_worker_publish(*args: object) -> bool:
        if threading.current_thread() is raw:
            worker_publish.set()
            assert resume_worker.wait(1.0)
            return False
        return original_publish(*args)

    def start_once() -> None:
        start_outcomes.append(
            thread_module._start_relay_linux_build_workspace_worker(
                owner,
                bundle,
                construction,
                _future(),
            )
        )

    monkeypatch.setattr(
        lifecycle_module,
        "_publish_terminal_candidate",
        block_worker_publish,
    )
    starter = threading.Thread(target=start_once)
    starter.start()
    assert worker_publish.wait(1.0)
    coordinator = record._lifecycle
    terminal = coordinator._terminal
    assert terminal is not None and coordinator._phase == "terminal-pending"
    object.__setattr__(coordinator, "_effect_phase", "none")

    assert not thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert active_module._workspace_worker_active_root_occupied(record._record_token)
    assert bundle in registry_module._RECORDS
    assert threading.Thread.is_alive(raw)

    object.__setattr__(coordinator, "_effect_phase", "returned")
    resume_worker.set()
    starter.join(1.0)
    assert not starter.is_alive()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_constructor_registry_lock_wait_uses_one_api_entry_deadline(
    tmp_path: Path,
) -> None:
    owner, bundle = _graph_without_construction(tmp_path, "construct-registry-deadline")
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_registry() -> None:
        with registry_module._REGISTRY_LOCK:
            lock_held.set()
            assert release_lock.wait(1.0)

    holder = threading.Thread(target=hold_registry)
    holder.start()
    assert lock_held.wait(1.0)
    started_at = time.monotonic()
    outcome = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    elapsed = time.monotonic() - started_at

    assert outcome == (None, False)
    assert elapsed < 0.15 and holder.is_alive()
    assert bundle not in registry_module._RECORDS
    release_lock.set()
    holder.join(1.0)
    assert not holder.is_alive()

    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    assert construction is not None and coherent is False
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_constructor_record_lock_wait_uses_one_api_entry_deadline(
    tmp_path: Path,
) -> None:
    owner, bundle, construction = _graph(tmp_path, "construct-record-deadline")
    record = registry_module._RECORDS[bundle]
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_record() -> None:
        with record._lock:
            lock_held.set()
            assert release_lock.wait(1.0)

    holder = threading.Thread(target=hold_record)
    holder.start()
    assert lock_held.wait(1.0)
    started_at = time.monotonic()
    outcome = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    elapsed = time.monotonic() - started_at

    assert outcome == (None, False)
    assert elapsed < 0.15 and holder.is_alive()
    assert registry_module._RECORDS.get(bundle) is record
    release_lock.set()
    holder.join(1.0)
    assert not holder.is_alive()

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    assert receipt is construction and coherent is False
    terminal = thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    assert terminal is not None
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )

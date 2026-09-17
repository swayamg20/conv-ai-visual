"""Focused inert ownership tests for the private relay Linux executor."""
# ruff: noqa: E402

from __future__ import annotations

import gc
import pickle
import sys
import threading
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer as worker_consumer
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as worker_registry
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_release as worker_release
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as worker_state
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as worker_thread
import scripts.voice_pipecat_e2e_relay_linux_executor_state as executor_state
import scripts.voice_pipecat_e2e_relay_linux_executor_workspace as executor_workspace
import scripts.voice_pipecat_e2e_relay_probe as relay_probe
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT


@pytest.fixture(autouse=True)
def _isolated_executor_state() -> None:
    worker_consumer._CONSUMERS.clear()
    executor_state._EXECUTORS.clear()
    executor_state._PORT_RESERVATIONS.clear()
    executor_state._RETIRED_KEYS.clear()
    executor_state._AUTHORITY_KEYS.clear()
    executor_state._DESTINATION_KEYS.clear()
    executor_state._OWNER_KEYS.clear()
    executor_state._SOURCE_EVIDENCE.clear()
    executor_state._WORKSPACE_RELEASES.clear()
    yield
    worker_consumer._CONSUMERS.clear()
    executor_state._EXECUTORS.clear()
    executor_state._PORT_RESERVATIONS.clear()
    executor_state._RETIRED_KEYS.clear()
    executor_state._AUTHORITY_KEYS.clear()
    executor_state._DESTINATION_KEYS.clear()
    executor_state._OWNER_KEYS.clear()
    executor_state._SOURCE_EVIDENCE.clear()
    executor_state._WORKSPACE_RELEASES.clear()


def _source() -> RelayProbeSource:
    return RelayProbeSource(relay_probe._SOURCE_TOKEN, commit_sha="a" * 40)


def _destination(tmp_path: Path):
    return executor_state._new_relay_linux_executor_destination(
        source_root=WEB_ROOT,
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id="outer-state",
        source=_source(),
    )


def _new_worker(executor):
    workspace = executor._workspace_owner
    bundle = worker_state._new_relay_linux_build_workspace_worker_bundle(workspace)
    construction, coherent = worker_thread._new_relay_linux_build_workspace_worker_thread(
        workspace,
        bundle,
    )
    assert construction is not None and coherent
    return workspace, bundle, construction


def _cancel_join_worker(workspace, bundle, construction):
    terminal = worker_thread._cancel_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
    )
    joined, complete = worker_thread._join_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        0.0,
    )
    assert joined is terminal and complete
    return terminal


def _release_bound_worker(executor, bundle, construction) -> None:
    workspace = executor._workspace_owner
    authority = executor._cleanup_authority
    graph = executor_workspace._intend_relay_linux_executor_workspace_release(authority)
    assert graph == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(authority)


def test_preowns_opaque_outer_graph_and_exact_fixed_port_reservation(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    owner = executor_state._preown_relay_linux_executor(destination)

    assert destination._read() is owner
    assert owner._workspace_owner is owner._workspace_destination._read(
        owner._workspace_destination._request
    )
    assert owner._source is destination._source
    assert owner._workspace_owner._request._source_root == WEB_ROOT
    assert type(owner._relay_owner_destination).__name__ == "RelayProbeOwnerDestination"
    assert executor_state._FIXED_PORTS == (
        8101,
        3100,
        5349,
        *range(49160, 49170),
    )
    assert executor_state._PORT_RESERVATIONS == {
        executor_state._FIXED_PORTS: owner._cleanup_authority._key,
    }
    assert len(executor_state._EXECUTORS) == 1

    for value in (destination, owner, owner._cleanup_authority):
        assert not value
        for operation in (copy, deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(value)


def test_rejects_noncanonical_source_root_before_preownership(tmp_path: Path) -> None:
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._new_relay_linux_executor_destination(
            source_root=(tmp_path / "wrong-web").resolve(),
            run_parent=(tmp_path / "runs").resolve(),
            node=(tmp_path / "node").resolve(),
            run_id="wrong-source",
            source=_source(),
        )
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS


def test_fixed_ports_are_atomically_cap_one_not_availability_checked(tmp_path: Path) -> None:
    first = _destination(tmp_path / "first")
    second = _destination(tmp_path / "second")
    owner = executor_state._preown_relay_linux_executor(first)

    with pytest.raises(executor_state._RelayLinuxExecutorError, match="capacity"):
        executor_state._preown_relay_linux_executor(second)
    assert len(executor_state._EXECUTORS) == 1
    assert executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] is (
        owner._cleanup_authority._key
    )


def test_hostile_equality_cannot_forge_executor_or_fixed_port_records(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    owner = destination._read()
    key = executor_state._OWNER_KEYS[owner]

    class _EqualAnything:
        def __eq__(self, _other: object) -> bool:
            return True

    executor_state._EXECUTORS[key] = tuple(_EqualAnything() for _ in range(6))
    executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] = key
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(destination)
    assert executor_state._EXECUTORS[key][0] is not owner

    class _EqualPorts(tuple):
        def __hash__(self) -> int:
            return hash(executor_state._FIXED_PORTS)

        def __eq__(self, _other: object) -> bool:
            return True

        def __ne__(self, _other: object) -> bool:
            return False

    executor_state._EXECUTORS[key] = (
        owner,
        destination,
        None,
        None,
        None,
        "preowned",
    )
    executor_state._PORT_RESERVATIONS.clear()
    hostile_ports = _EqualPorts()
    executor_state._PORT_RESERVATIONS[hostile_ports] = key
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(destination)
    assert next(iter(executor_state._PORT_RESERVATIONS)) is hostile_ports


@pytest.mark.parametrize("control", [False, True])
def test_preown_reconciles_executor_store_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: bool,
) -> None:
    destination = _destination(tmp_path)
    original = executor_state._store_executor_record
    raised = False

    def store_then_raise(key: object, record: tuple[object, ...]) -> None:
        nonlocal raised
        original(key, record)
        if not raised:
            raised = True
            if control:
                raise KeyboardInterrupt("synthetic preown control")
            raise OSError("synthetic preown return loss")

    monkeypatch.setattr(executor_state, "_store_executor_record", store_then_raise)
    expected = KeyboardInterrupt if control else OSError
    with pytest.raises(expected):
        executor_state._preown_relay_linux_executor(destination)

    owner = executor_state._preown_relay_linux_executor(destination)
    assert raised
    assert executor_state._EXECUTORS[owner._cleanup_authority._key][0] is owner
    assert executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] is (
        owner._cleanup_authority._key
    )


@pytest.mark.parametrize(
    "cut",
    ["_store_retired_key", "_pop_executor_record", "_pop_port_reservation"],
)
@pytest.mark.parametrize("control", [False, True])
def test_unstarted_release_reconciles_every_retirement_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
    control: bool,
) -> None:
    destination = _destination(tmp_path)
    owner = executor_state._preown_relay_linux_executor(destination)
    authority = owner._cleanup_authority
    original = getattr(executor_state, cut)
    raised = False

    def effect_then_raise(*args: object) -> None:
        nonlocal raised
        original(*args)
        if not raised:
            raised = True
            if control:
                raise KeyboardInterrupt("synthetic release control")
            raise OSError("synthetic release return loss")

    monkeypatch.setattr(executor_state, cut, effect_then_raise)
    expected = KeyboardInterrupt if control else OSError
    with pytest.raises(expected):
        executor_state._release_unstarted_relay_linux_executor(authority)

    assert executor_state._release_unstarted_relay_linux_executor(authority)
    assert raised
    assert authority._key in executor_state._RETIRED_KEYS
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS
    assert executor_state._release_unstarted_relay_linux_executor(authority)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(destination)


def test_retirement_tombstone_is_weak_after_the_preowned_graph_is_dropped(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    owner = executor_state._preown_relay_linux_executor(destination)
    authority = owner._cleanup_authority
    assert executor_state._release_unstarted_relay_linux_executor(authority)
    assert len(executor_state._RETIRED_KEYS) == 1

    destination = owner = authority = None
    gc.collect()
    assert len(executor_state._RETIRED_KEYS) == 0


def test_cleanup_authority_is_key_only_and_graph_opaque(tmp_path: Path) -> None:
    authority = executor_state._preown_relay_linux_executor(
        _destination(tmp_path)
    )._cleanup_authority
    assert authority.__slots__ == ("__weakref__", "_authentic", "_key")
    for name in ("_owner", "_destination", "_workspace", "_source"):
        with pytest.raises(AttributeError):
            object.__getattribute__(authority, name)


def test_canonical_identity_rejects_key_tamper_and_cross_owner_release(tmp_path: Path) -> None:
    first_destination = _destination(tmp_path / "first")
    first = executor_state._preown_relay_linux_executor(first_destination)
    first_authority = first._cleanup_authority
    assert executor_state._release_unstarted_relay_linux_executor(first_authority)

    object.__setattr__(first_authority, "_key", executor_state._RelayLinuxExecutorKey())
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(first_destination)

    second = executor_state._preown_relay_linux_executor(_destination(tmp_path / "second"))
    second_key = executor_state._OWNER_KEYS[second]
    object.__setattr__(first_authority, "_key", second_key)
    assert not executor_state._release_unstarted_relay_linux_executor(first_authority)
    assert executor_state._EXECUTORS[second_key][0] is second
    assert executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] is second_key
    assert executor_state._release_unstarted_relay_linux_executor(second._cleanup_authority)


def test_release_ignores_caller_tamper_but_rejects_orphan_registry_state(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    owner = executor_state._preown_relay_linux_executor(destination)
    authority = owner._cleanup_authority
    key = executor_state._AUTHORITY_KEYS[authority]
    object.__setattr__(destination, "_owner", None)
    object.__setattr__(authority, "_authentic", object())

    orphan_key = executor_state._RelayLinuxExecutorKey()
    executor_state._EXECUTORS[orphan_key] = (object(),)  # type: ignore[assignment]
    executor_state._PORT_RESERVATIONS[(1,)] = orphan_key
    assert not executor_state._release_unstarted_relay_linux_executor(authority)
    assert key in executor_state._EXECUTORS
    assert executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] is key

    executor_state._EXECUTORS.pop(orphan_key)
    executor_state._PORT_RESERVATIONS.pop((1,))
    assert executor_state._release_unstarted_relay_linux_executor(authority)
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS


def test_source_evidence_rejects_crosswire_and_commit_tamper_but_cleanup_recovers(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    owner = destination._read()
    original_source = owner._source
    replacement = RelayProbeSource(relay_probe._SOURCE_TOKEN, commit_sha="b" * 40)
    object.__setattr__(owner, "_source", replacement)
    object.__setattr__(destination, "_source", replacement)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(destination)

    object.__setattr__(owner, "_source", original_source)
    object.__setattr__(destination, "_source", original_source)
    executor_state._preown_relay_linux_executor(destination)
    object.__setattr__(original_source, "_commit_sha", "c" * 40)
    assert not executor_state._executor_value_matches(owner, destination)
    assert executor_state._release_unstarted_relay_linux_executor(owner._cleanup_authority)


def test_source_evidence_rejects_hostile_equality_and_non_string_sha(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    owner = destination._read()
    key = executor_state._OWNER_KEYS[owner]

    class _EqualAnything:
        def __eq__(self, _other: object) -> bool:
            return True

    object.__setattr__(owner._source, "_commit_sha", "b" * 40)
    executor_state._SOURCE_EVIDENCE[key] = (
        owner._source,
        _EqualAnything(),
        WEB_ROOT,
    )
    assert not executor_state._executor_value_matches(owner, destination)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_state._preown_relay_linux_executor(destination)


def test_workspace_binding_rejects_a_fully_released_worker_receipt(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )

    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    graph = executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert graph == (workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert bundle not in worker_registry._RECORDS
    assert executor_workspace._resolve_relay_linux_executor_workspace(executor, destination) is None
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_hostile_worker_phase_quarantines_worker_and_outer_graph(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    record = worker_registry._RECORDS[bundle]
    original_entry = record._entry

    class _EqualAnything:
        def __eq__(self, _other: object) -> bool:
            return True

        def __hash__(self) -> int:
            return hash(worker_registry._INITIALIZED)

    object.__setattr__(
        record,
        "_entry",
        (_EqualAnything(), original_entry[1], original_entry[2]),
    )
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert bundle in worker_registry._RECORDS
    assert not executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert not executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert len(executor_state._EXECUTORS) == 1
    assert len(executor_state._PORT_RESERVATIONS) == 1

    object.__setattr__(record, "_entry", original_entry)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_workspace_binding_rejects_failed_incoherent_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace = executor._workspace_owner
    bundle = worker_state._new_relay_linux_build_workspace_worker_bundle(workspace)

    def fail_construction(_thread: threading.Thread, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("synthetic construction failure")

    monkeypatch.setattr(threading.Thread, "__init__", fail_construction)
    construction, coherent = worker_thread._new_relay_linux_build_workspace_worker_thread(
        workspace,
        bundle,
    )
    assert construction is not None and coherent is False
    assert not executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert (
        executor_state._EXECUTORS[executor_state._OWNER_KEYS[executor]][5]
        == "workspace-pin-intended"
    )
    assert not executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = worker_thread._cancel_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
    )
    joined, complete = worker_thread._join_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        0.0,
    )
    assert joined is terminal and complete
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_workspace_pin_blocks_release_across_outer_store_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace, bundle, construction = _new_worker(executor)
    original = executor_workspace._store_executor_record
    raised = False

    def store_then_raise(key: object, record: tuple[object, ...]) -> None:
        nonlocal raised
        original(key, record)
        if record[5] == "workspace-bound" and not raised:
            raised = True
            raise OSError("synthetic bound-store return loss")

    monkeypatch.setattr(executor_workspace, "_store_executor_record", store_then_raise)
    with pytest.raises(OSError, match="bound-store"):
        executor_workspace._bind_relay_linux_executor_workspace(
            executor,
            bundle,
            construction,
        )

    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    key = executor_state._OWNER_KEYS[executor]
    assert raised and executor_state._EXECUTORS[key][5] == "workspace-bound"
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS


@pytest.mark.parametrize("forged_phase", ["preowned", "workspace-released"])
def test_forged_releasable_phase_cannot_retire_a_live_worker(
    tmp_path: Path,
    forged_phase: str,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    _workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    key = executor_state._OWNER_KEYS[executor]
    exact = executor_state._EXECUTORS[key]
    executor_state._EXECUTORS[key] = (
        (exact[0], exact[1], None, None, None, "preowned")
        if forged_phase == "preowned"
        else (*exact[:5], "workspace-released")
    )
    assert not executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert worker_registry._RECORDS.get(bundle) is not None
    assert worker_consumer._CONSUMERS.get(bundle) is not None
    assert executor_state._PORT_RESERVATIONS[executor_state._FIXED_PORTS] is key

    executor_state._EXECUTORS[key] = exact
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


@pytest.mark.parametrize("control", [False, True])
def test_workspace_pin_store_return_loss_is_retryable_and_blocks_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: bool,
) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace, bundle, construction = _new_worker(executor)
    original = worker_consumer._store_workspace_worker_consumer
    raised = False

    def store_then_raise(*args: object) -> None:
        nonlocal raised
        original(*args)
        if not raised:
            raised = True
            if control:
                raise KeyboardInterrupt("synthetic pin control")
            raise OSError("synthetic pin return loss")

    monkeypatch.setattr(
        worker_consumer,
        "_store_workspace_worker_consumer",
        store_then_raise,
    )
    expected = KeyboardInterrupt if control else OSError
    with pytest.raises(expected):
        executor_workspace._bind_relay_linux_executor_workspace(
            executor,
            bundle,
            construction,
        )
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


@pytest.mark.parametrize("control", [False, True])
def test_workspace_pin_clear_return_loss_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: bool,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    original = worker_consumer._pop_workspace_worker_consumer
    raised = False

    def pop_then_raise(*args: object) -> None:
        nonlocal raised
        original(*args)
        if not raised:
            raised = True
            if control:
                raise KeyboardInterrupt("synthetic pin-clear control")
            raise OSError("synthetic pin-clear return loss")

    monkeypatch.setattr(
        worker_consumer,
        "_pop_workspace_worker_consumer",
        pop_then_raise,
    )
    expected = KeyboardInterrupt if control else OSError
    with pytest.raises(expected):
        executor_workspace._intend_relay_linux_executor_workspace_release(
            executor._cleanup_authority
        )
    graph = executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert graph == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


@pytest.mark.parametrize("fault", ["ordinary", "keyboard", "system-exit"])
@pytest.mark.parametrize(
    "cut",
    ["_store_workspace_release_evidence", "_store_executor_record"],
)
def test_workspace_release_completion_store_return_loss_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    cut: str,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    original = getattr(executor_workspace, cut)
    raised = False

    def store_then_raise(key: object, value: tuple[object, ...]) -> None:
        nonlocal raised
        original(key, value)
        effect_reached = cut == "_store_workspace_release_evidence" or value[5] == (
            "workspace-released"
        )
        if effect_reached and not raised:
            raised = True
            if fault == "keyboard":
                raise KeyboardInterrupt("synthetic completion control")
            if fault == "system-exit":
                raise SystemExit(19)
            raise OSError("synthetic completion return loss")

    monkeypatch.setattr(executor_workspace, cut, store_then_raise)
    expected = {
        "ordinary": OSError,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[fault]
    with pytest.raises(expected):
        executor_workspace._complete_relay_linux_executor_workspace_release(
            executor._cleanup_authority
        )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert raised
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


@pytest.mark.parametrize("control", [False, True])
def test_completed_workspace_release_evidence_pop_return_loss_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: bool,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    original = executor_state._pop_workspace_release_evidence
    raised = False

    def pop_then_raise(*args: object) -> None:
        nonlocal raised
        original(*args)
        if not raised:
            raised = True
            if control:
                raise KeyboardInterrupt("synthetic release-evidence control")
            raise OSError("synthetic release-evidence return loss")

    monkeypatch.setattr(
        executor_state,
        "_pop_workspace_release_evidence",
        pop_then_raise,
    )
    expected = KeyboardInterrupt if control else OSError
    with pytest.raises(expected):
        executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert raised and not executor_state._WORKSPACE_RELEASES


def test_outer_retirement_atomically_rechecks_consumer_after_worker_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    original = executor_state._workspace_worker_receipt_is_current

    class _Orphan:
        pass

    orphan = _Orphan()
    injected = False

    def prove_absent_then_inject(*args: object, **kwargs: object) -> str | None:
        nonlocal injected
        result = original(*args, **kwargs)
        if result == "absent" and not injected:
            injected = True
            worker_consumer._CONSUMERS[orphan] = (object(), object())  # type: ignore[index]
        return result

    monkeypatch.setattr(
        executor_state,
        "_workspace_worker_receipt_is_current",
        prove_absent_then_inject,
    )
    assert not executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert injected and executor_state._EXECUTORS
    assert executor_state._PORT_RESERVATIONS
    worker_consumer._CONSUMERS.pop(orphan)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_missing_worker_record_still_requires_global_consumer_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)

    class _Orphan:
        pass

    orphan = _Orphan()
    injected = False

    def inject_orphan_then_raise() -> None:
        nonlocal injected
        if not injected:
            injected = True
            worker_consumer._CONSUMERS[orphan] = (object(), object())  # type: ignore[index]
            raise OSError("synthetic post-delete return loss")

    monkeypatch.setattr(
        worker_release,
        "_workspace_worker_record_released",
        inject_orphan_then_raise,
    )
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert bundle not in worker_registry._RECORDS
    assert worker_consumer._CONSUMERS.get(orphan) is not None
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    worker_consumer._CONSUMERS.pop(orphan)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


@pytest.mark.parametrize("return_loss", [False, True])
def test_post_delete_cleanup_requires_global_worker_registry_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    return_loss: bool,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert executor_workspace._intend_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    ) == (workspace, bundle, construction)
    terminal = _cancel_join_worker(workspace, bundle, construction)

    class _Orphan:
        pass

    orphan = _Orphan()
    injected = False

    def inject_registry_orphan() -> None:
        nonlocal injected
        if not injected:
            injected = True
            worker_registry._RECORDS[orphan] = object()  # type: ignore[index,assignment]
            if return_loss:
                raise OSError("synthetic post-delete registry return loss")

    monkeypatch.setattr(
        worker_release,
        "_workspace_worker_record_released",
        inject_registry_orphan,
    )
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert bundle not in worker_registry._RECORDS
    assert worker_registry._RECORDS.get(orphan) is not None
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    worker_registry._RECORDS.pop(orphan)
    assert worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    assert executor_workspace._complete_relay_linux_executor_workspace_release(
        executor._cleanup_authority
    )
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_malformed_or_orphan_consumer_state_blocks_every_worker_release(
    tmp_path: Path,
) -> None:
    executor = executor_state._preown_relay_linux_executor(_destination(tmp_path))
    workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    terminal = _cancel_join_worker(workspace, bundle, construction)
    exact = worker_consumer._CONSUMERS[bundle]
    worker_consumer._CONSUMERS[bundle] = (object(), object())
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )

    class _Orphan:
        pass

    orphan = _Orphan()
    worker_consumer._CONSUMERS[bundle] = exact
    worker_consumer._CONSUMERS[orphan] = (object(), object())  # type: ignore[index]
    assert not worker_thread._release_relay_linux_build_workspace_worker(
        workspace,
        bundle,
        construction,
        terminal,
    )
    worker_consumer._CONSUMERS.pop(orphan)
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_hostile_equality_cannot_forge_an_exact_worker_consumer_pin(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    _workspace, bundle, construction = _new_worker(executor)

    class _EqualAnything:
        def __eq__(self, _other: object) -> bool:
            return True

    worker_consumer._CONSUMERS[bundle] = (_EqualAnything(), _EqualAnything())
    assert not executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    assert (
        executor_workspace._resolve_relay_linux_executor_workspace(
            executor,
            destination,
        )
        is None
    )
    assert executor_state._EXECUTORS[executor_state._OWNER_KEYS[executor]][5] == (
        "workspace-pin-intended"
    )
    worker_consumer._CONSUMERS.clear()
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)


def test_live_receipt_tamper_quarantines_outer_and_worker_until_restored(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    executor = executor_state._preown_relay_linux_executor(destination)
    _workspace, bundle, construction = _new_worker(executor)
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    object.__setattr__(construction, "_coherent", False)
    assert (
        executor_workspace._resolve_relay_linux_executor_workspace(
            executor,
            destination,
        )
        is None
    )
    assert not executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
    assert worker_registry._RECORDS.get(bundle) is not None
    assert worker_consumer._CONSUMERS.get(bundle) is not None

    object.__setattr__(construction, "_coherent", True)
    _release_bound_worker(executor, bundle, construction)
    assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)

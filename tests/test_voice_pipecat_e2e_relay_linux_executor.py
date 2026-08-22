"""Private synthetic vertical for the consumed-build outer relay executor."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import time
import weakref
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as process_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as process_state
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer as build_consumer
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values as consumer_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_facade as build_facade
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_forget as receipt_forget
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values as build_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer as worker_consumer
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_transaction as fs_build_transaction
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values as fs_output_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as worker_registry
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as worker_state
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as worker_thread
import scripts.voice_pipecat_e2e_relay_linux_executor as executor_facade
import scripts.voice_pipecat_e2e_relay_linux_executor_build_binding as executor_binding
import scripts.voice_pipecat_e2e_relay_linux_executor_build_consume as executor_consume
import scripts.voice_pipecat_e2e_relay_linux_executor_cleanup as executor_cleanup
import scripts.voice_pipecat_e2e_relay_linux_executor_inner_anchor as executor_inner_anchor
import scripts.voice_pipecat_e2e_relay_linux_executor_inner_state as executor_inner_state
import scripts.voice_pipecat_e2e_relay_linux_executor_state as executor_state
import scripts.voice_pipecat_e2e_relay_linux_executor_workspace as executor_workspace
import scripts.voice_pipecat_e2e_relay_owner_state as relay_owner_state
import scripts.voice_pipecat_e2e_relay_probe as relay_probe
from scripts.voice_pipecat_e2e_relay_invocation import RelayInvocationDriver
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT
from tests.test_voice_pipecat_e2e_coturn_host import _tools
from tests.test_voice_pipecat_e2e_relay_owner import (
    SECRET,
    _BridgeProbe,
    _install_synthetic_lifecycle,
    _object,
    _Runner,
)


@pytest.fixture(autouse=True)
def _isolated_outer_state() -> None:
    mappings = (
        process_registry._OWNERS,
        process_registry._KERNELS,
        process_registry._ABSENCE_RESERVATIONS,
        process_state._AUTHORITY_BINDINGS,
        build_values._COMMANDS,
        build_values._COMMAND_GATES,
        build_values._COMMAND_CONTROLLERS,
        build_values._CONTROLLER_COMMANDS,
        build_values._PROCESS_ASSOCIATIONS,
        build_receipt._BUILT_LEASES,
        build_receipt._BUILT_BY_COMMAND,
        receipt_forget._RETIREMENTS,
        receipt_forget._RETIREMENT_AUTHORITIES,
        receipt_forget._RETIRED_RECEIPT_EVIDENCE,
        consumer_values._BUILD_CONSUMERS,
        consumer_values._BUILT_BY_CONSUMER,
        consumer_values._CONSUMED_HISTORY,
        consumer_values._CONSUMER_TOMBSTONES,
        fs_contract._LEASES,
        fs_contract._PREPARED_BUILDS,
        fs_contract._SETTLEMENTS,
        fs_contract._CLAIMS,
        worker_consumer._CONSUMERS,
        worker_registry._RECORDS,
        executor_binding._EVIDENCE_BY_KEY,
        executor_binding._KEYS_BY_BINDING,
        executor_binding._BINDINGS_BY_BUILT,
        executor_binding._RELEASE_BINDINGS,
        executor_binding._BUILD_RETIREMENTS,
        executor_state._EXECUTORS,
        executor_state._PORT_RESERVATIONS,
        executor_state._RETIRED_KEYS,
        executor_state._AUTHORITY_KEYS,
        executor_state._DESTINATION_KEYS,
        executor_state._OWNER_KEYS,
        executor_state._SOURCE_EVIDENCE,
        executor_state._WORKSPACE_RELEASES,
        executor_inner_state._INNER_RECORDS,
        executor_inner_state._INNER_RESULTS,
        executor_inner_state._INNER_TERMINALS,
        executor_inner_state._INNER_AUTHORITIES,
        relay_owner_state._REGISTRY,
    )
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


def _source() -> RelayProbeSource:
    return RelayProbeSource(relay_probe._SOURCE_TOKEN, commit_sha="a" * 40)


def _consumed_running_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    sequence: int = 0,
):
    run_parent = tmp_path / f"runs-{sequence}"
    run_parent.mkdir(mode=0o700)
    node = tmp_path / f"node-{sequence}"
    node.write_bytes(b"synthetic-node\n")
    node.chmod(0o700)
    destination = executor_state._new_relay_linux_executor_destination(
        source_root=WEB_ROOT,
        run_parent=run_parent.resolve(),
        node=node.resolve(),
        run_id=f"executor-inner-{sequence}",
        source=_source(),
    )
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace_owner = executor._workspace_owner
    bundle = worker_state._new_relay_linux_build_workspace_worker_bundle(workspace_owner)
    construction, coherent = worker_thread._new_relay_linux_build_workspace_worker_thread(
        workspace_owner,
        bundle,
    )
    assert construction is not None and coherent is True
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    snapshot: fs_output_values._WorkspaceBuildOutputSnapshot | None = None

    def fake_process_driver(
        *,
        command,
        request,
        controller,
        owner_token,
        record_token,
        build_deadline,
        prestart_authority,
    ):
        assert request is workspace_owner._request
        assert prestart_authority.claim._bundle is bundle
        assert build_values._bind_workspace_build_command_controller(
            command,
            controller=controller,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        )
        state = build_values._COMMANDS[command]
        build_values._store_command_state(command, (*state[:4], "running", state[5]))
        process_owner_token = object()
        authority = process_state._RelayLinuxBuildCleanupAuthority(
            process_state._AUTHORITY_TOKEN,
            key=object(),
            owner_token=process_owner_token,
        )
        receipt = process_state._RelayLinuxBuildProcessReceipt(
            process_state._RECEIPT_TOKEN,
            owner_token=process_owner_token,
        )
        build_values._PROCESS_ASSOCIATIONS[command] = (
            owner_token,
            record_token,
            process_owner_token,
            authority,
            state[5],
            receipt,
            "released-zero",
        )
        events.append("process-absent")
        return receipt

    def fake_validate_output(**kwargs):
        nonlocal snapshot
        if snapshot is None:
            workspace = workspace_owner._request._workspace
            parent = workspace / ".next-voice-e2e"
            dist = parent / workspace_owner._request._run_id
            parent.mkdir(mode=0o700)
            dist.mkdir(mode=0o700)
            (dist / "synthetic-output").write_bytes(b"validated")
            baseline = kwargs["baseline"]
            snapshot = fs_output_values._WorkspaceBuildOutputSnapshot(
                digest=b"d" * 32,
                dist_parent_identity=fs_contract._WorkspaceFilesystemIdentity.from_stat(
                    parent.stat(follow_symlinks=False)
                ),
                dist_root_identity=fs_contract._WorkspaceFilesystemIdentity.from_stat(
                    dist.stat(follow_symlinks=False)
                ),
                dist_nodes=(),
                node_modules_identity=baseline.node_modules_identity,
                workspace_nodes=(),
            )
        events.append("validated")
        return snapshot

    real_scope_cleanup = fs_build_transaction._cleanup_workspace_build_scope

    def record_scope_cleanup(state):
        assert not relay_owner_state._REGISTRY
        assert executor._relay_owner_destination._record is None
        events.append("first-fs-delete")
        return real_scope_cleanup(state)

    monkeypatch.setattr(
        fs_build_transaction,
        "_drive_workspace_build_process",
        fake_process_driver,
    )
    monkeypatch.setattr(
        fs_build_transaction,
        "_validate_workspace_build_output",
        fake_validate_output,
    )
    monkeypatch.setattr(
        fs_build_transaction,
        "_cleanup_workspace_build_scope",
        record_scope_cleanup,
    )
    start, coherent = worker_thread._start_relay_linux_build_workspace_worker(
        workspace_owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    assert start is not None and coherent is True
    prepared = workspace_owner._receipt_destination._read(workspace_owner._request)
    deadline = time.monotonic() + 2.0
    while prepared is None:
        assert time.monotonic() < deadline
        time.sleep(0.005)
        prepared = workspace_owner._receipt_destination._read(workspace_owner._request)
    built, coherent = build_facade._build_relay_linux_workspace(
        workspace_owner,
        bundle,
        construction,
        prepared,
        build_deadline=time.monotonic() + 3.0,
    )
    assert built is not None and coherent is True
    command = build_receipt._BUILT_LEASES[built][2]
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=built,
        operation_deadline=build_values._COMMANDS[command][3],
    )
    return executor, destination, built, binding


def test_outer_runs_inner_then_retires_every_effect_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    assert not executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=time.monotonic() + 0.1,
    )

    observation = executor_facade._run_consumed_relay_linux_executor(
        executor=executor,
        destination=destination,
        binding=binding,
        runner=_Runner(events),
        bridge_probe=_BridgeProbe(events),
        tools=_tools(),
        invocation_driver=_object(RelayInvocationDriver),
        static_auth_secret=SECRET,
        now=datetime(2026, 8, 23),
        browser_timeout_seconds=5.0,
        runtime_timeout_seconds=5.0,
        cleanup_timeout_seconds=15.0,
    )

    assert type(observation) is RelayProbeObservation
    assert events.index("revalidate-source") < events.index("first-fs-delete")
    assert executor._relay_owner_destination._record is None
    assert not relay_owner_state._REGISTRY
    assert not executor._workspace_owner._request._run_root.exists()
    assert not executor_inner_state._INNER_RECORDS
    assert executor_inner_state._INNER_RESULTS
    assert executor_inner_state._INNER_TERMINALS
    assert executor_inner_state._INNER_AUTHORITIES
    assert not worker_registry._RECORDS
    assert not worker_consumer._CONSUMERS
    assert not build_values._COMMANDS
    assert not build_values._COMMAND_GATES
    assert not build_values._PROCESS_ASSOCIATIONS
    assert not build_receipt._BUILT_LEASES
    assert not build_receipt._BUILT_BY_COMMAND
    assert build_consumer._workspace_built_consumer_all_state_is_empty()
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_binding._KEYS_BY_BINDING
    assert not executor_binding._BINDINGS_BY_BUILT
    assert not executor_binding._RELEASE_BINDINGS
    assert not executor_binding._BUILD_RETIREMENTS
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS
    assert built not in build_receipt._BUILT_LEASES


@pytest.mark.parametrize("cut", ["factory", "run"])
def test_outer_replays_one_exact_inner_owner_after_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    runner = _Runner(events)
    bridge_probe = _BridgeProbe(events)
    tools = _tools()
    driver = _object(RelayInvocationDriver)
    observed_owners: list[object] = []
    if cut == "factory":
        original = executor_facade.new_relay_probe_owner
        raised = False

        def call_with_loss(**kwargs):
            nonlocal raised
            owner = original(**kwargs)
            observed_owners.append(owner)
            if not raised:
                raised = True
                raise OSError("synthetic inner factory return loss")
            return owner

        monkeypatch.setattr(executor_facade, "new_relay_probe_owner", call_with_loss)
    else:
        original_run = executor_facade.run_relay_probe
        raised = False

        def run_with_loss(owner, **kwargs):
            nonlocal raised
            observed_owners.append(owner)
            result = original_run(owner, **kwargs)
            if not raised:
                raised = True
                raise OSError("synthetic inner run return loss")
            return result

        monkeypatch.setattr(executor_facade, "run_relay_probe", run_with_loss)
    arguments = dict(
        executor=executor,
        destination=destination,
        binding=binding,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_driver=driver,
        static_auth_secret=SECRET,
        now=datetime(2026, 8, 23),
        browser_timeout_seconds=5.0,
        runtime_timeout_seconds=5.0,
        cleanup_timeout_seconds=15.0,
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    result = executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert type(result) is RelayProbeObservation
    assert observed_owners
    assert all(owner is observed_owners[0] for owner in observed_owners)
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS


def test_inner_intent_return_loss_reuses_deadline_and_rejects_changed_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_store = executor_inner_state._store_inner_record
    raised = False

    def store_with_loss(key: object, record: tuple[object, ...]) -> None:
        nonlocal raised
        original_store(key, record)
        if not raised:
            raised = True
            raise OSError("synthetic inner intent return loss")

    monkeypatch.setattr(executor_inner_state, "_store_inner_record", store_with_loss)
    runner = _Runner(events)
    bridge_probe = _BridgeProbe(events)
    tools = _tools()
    driver = _object(RelayInvocationDriver)

    def run(timeout: float) -> RelayProbeObservation:
        return executor_facade._run_consumed_relay_linux_executor(
            executor=executor,
            destination=destination,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_driver=driver,
            static_auth_secret=SECRET,
            now=datetime(2026, 8, 23),
            browser_timeout_seconds=5.0,
            runtime_timeout_seconds=timeout,
            cleanup_timeout_seconds=15.0,
        )

    with pytest.raises(executor_state._RelayLinuxExecutorError):
        run(5.0)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        run(6.0)
    result = run(5.0)
    assert type(result) is RelayProbeObservation
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS


def test_terminal_replay_requires_the_exact_original_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    runner = _Runner(events)
    bridge_probe = _BridgeProbe(events)
    tools = _tools()
    driver = _object(RelayInvocationDriver)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": runner,
        "bridge_probe": bridge_probe,
        "tools": tools,
        "invocation_driver": driver,
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
    }
    observation = executor_facade._run_consumed_relay_linux_executor(**arguments)

    def wrong_clock() -> float:
        return time.monotonic()

    for changes in (
        {"binding": executor_binding._new_executor_built_binding()},
        {"runner": _Runner([])},
        {"bridge_probe": _BridgeProbe([])},
        {"tools": _tools()},
        {"invocation_driver": _object(RelayInvocationDriver)},
        {"static_auth_secret": object()},
        {"now": datetime(2026, 8, 24)},
        {"browser_timeout_seconds": 6.0},
        {"runtime_timeout_seconds": 6.0},
        {"clock": wrong_clock},
    ):
        with pytest.raises(executor_state._RelayLinuxExecutorError):
            executor_facade._run_consumed_relay_linux_executor(
                **(arguments | changes),
            )
    key = executor_state._canonical_executor_key(executor, destination)
    authority = executor_inner_state._INNER_AUTHORITIES[key]
    result_destination = executor_inner_state._INNER_RESULTS[key]
    terminal = executor_inner_state._INNER_TERMINALS[key]
    wrong_runner = _Runner([])
    forged_values = (*authority[1][:1], wrong_runner, *authority[1][2:])
    object.__setattr__(result_destination, "_replay_values", forged_values)
    executor_inner_state._INNER_AUTHORITIES[key] = (
        authority[0],
        forged_values,
        authority[2],
        authority[3],
    )
    executor_inner_state._INNER_TERMINALS[key] = (
        *terminal[:4],
        forged_values,
        terminal[5],
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(
            **(arguments | {"runner": wrong_runner}),
        )
    object.__setattr__(result_destination, "_replay_values", authority[1])
    executor_inner_state._INNER_AUTHORITIES[key] = authority
    executor_inner_state._INNER_TERMINALS[key] = terminal

    anchor = executor._inner_authority_anchor
    forged_anchor = executor_inner_anchor._new_executor_inner_authority_anchor()
    assert forged_anchor._bind(forged_values) is forged_values
    object.__setattr__(executor, "_inner_authority_anchor", forged_anchor)
    object.__setattr__(destination, "_inner_authority_anchor", forged_anchor)
    object.__setattr__(result_destination, "_replay_values", forged_values)
    executor_inner_state._INNER_AUTHORITIES[key] = (
        authority[0],
        forged_values,
        authority[2],
        authority[3],
    )
    executor_inner_state._INNER_TERMINALS[key] = (
        *terminal[:4],
        forged_values,
        terminal[5],
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(
            **(arguments | {"runner": wrong_runner}),
        )
    object.__setattr__(executor, "_inner_authority_anchor", anchor)
    object.__setattr__(destination, "_inner_authority_anchor", anchor)
    object.__setattr__(result_destination, "_replay_values", authority[1])
    executor_inner_state._INNER_AUTHORITIES[key] = authority
    executor_inner_state._INNER_TERMINALS[key] = terminal

    with pytest.raises(AttributeError):
        object.__setattr__(anchor, "_values", forged_values)
    object.__setattr__(executor, "_inner_authority_anchor", object())
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    object.__setattr__(executor, "_inner_authority_anchor", anchor)
    object.__setattr__(destination, "_inner_authority_anchor", object())
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    object.__setattr__(destination, "_inner_authority_anchor", anchor)

    orphan_key = executor_state._RelayLinuxExecutorKey()
    for mapping, value in (
        (executor_inner_state._INNER_RESULTS, result_destination),
        (executor_inner_state._INNER_TERMINALS, terminal),
    ):
        mapping[orphan_key] = value
        with pytest.raises(executor_state._RelayLinuxExecutorError):
            executor_facade._run_consumed_relay_linux_executor(**arguments)
        mapping.pop(orphan_key)

    executor_state._RETIRED_KEYS.discard(key)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    executor_state._RETIRED_KEYS.add(key)

    owner_destination = executor._relay_owner_destination
    with owner_destination._lock:
        owner_destination._record = ((), object())
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    with owner_destination._lock:
        owner_destination._record = None

    orphan_registry_key = object()
    relay_owner_state._REGISTRY[orphan_registry_key] = object()
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    relay_owner_state._REGISTRY.pop(orphan_registry_key)

    executor_state._WORKSPACE_RELEASES[key] = (object(), object(), object())
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    executor_state._WORKSPACE_RELEASES.pop(key)

    executor_binding._BUILD_RETIREMENTS[key] = object()
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    executor_binding._BUILD_RETIREMENTS.pop(key)
    assert built not in executor_binding._BINDINGS_BY_BUILT
    assert executor_facade._run_consumed_relay_linux_executor(**arguments) is observation


@pytest.mark.parametrize("poison", ["retired", "destination", "registry"])
def test_initial_return_requires_the_exact_terminal_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    poison: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    key = executor_state._canonical_executor_key(executor, destination)
    original_absence = executor_cleanup._outer_is_absent
    orphan_registry_key = object()
    injected = False

    def absence_with_late_poison(evidence: object) -> bool:
        nonlocal injected
        if not injected and not executor_state._EXECUTORS:
            injected = True
            if poison == "retired":
                executor_state._RETIRED_KEYS.discard(key)
            elif poison == "destination":
                with executor._relay_owner_destination._lock:
                    executor._relay_owner_destination._record = ((), object())
            else:
                relay_owner_state._REGISTRY[orphan_registry_key] = object()
        return original_absence(evidence)

    monkeypatch.setattr(executor_cleanup, "_outer_is_absent", absence_with_late_poison)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 0.2,
    }
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert injected
    if poison == "retired":
        executor_state._RETIRED_KEYS.add(key)
    elif poison == "destination":
        with executor._relay_owner_destination._lock:
            executor._relay_owner_destination._record = None
    else:
        relay_owner_state._REGISTRY.pop(orphan_registry_key)
    result = executor_facade._run_consumed_relay_linux_executor(
        **(arguments | {"cleanup_timeout_seconds": 15.0}),
    )
    assert type(result) is RelayProbeObservation


def test_owner_binding_tamper_is_rejected_before_any_inner_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    runner = _Runner(events)
    bridge_probe = _BridgeProbe(events)
    tools = _tools()
    driver = _object(RelayInvocationDriver)
    original_resolve = executor_facade._resolve_or_intend_inner_evidence
    captured: list[tuple[object, tuple[object, ...]]] = []

    def resolve_with_tamper(**kwargs):
        evidence = original_resolve(**kwargs)
        if not captured:
            owner_binding = evidence.owner_binding
            captured.append((evidence, owner_binding))
            object.__setattr__(
                evidence,
                "owner_binding",
                (*owner_binding[:8], owner_binding[8] + 1.0, *owner_binding[9:]),
            )
        return evidence

    monkeypatch.setattr(
        executor_facade,
        "_resolve_or_intend_inner_evidence",
        resolve_with_tamper,
    )
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": runner,
        "bridge_probe": bridge_probe,
        "tools": tools,
        "invocation_driver": driver,
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 0.2,
    }
    before = tuple(events)
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert tuple(events) == before
    evidence, owner_binding = captured[0]
    object.__setattr__(evidence, "owner_binding", owner_binding)
    result = executor_facade._run_consumed_relay_linux_executor(
        **(arguments | {"cleanup_timeout_seconds": 15.0}),
    )
    assert type(result) is RelayProbeObservation


def test_two_retained_terminal_graphs_reject_a_full_cross_key_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    retained: list[tuple[object, object, dict[str, object], RelayProbeObservation]] = []
    for sequence in range(2):
        executor, destination, _built, binding = _consumed_running_executor(
            tmp_path,
            monkeypatch,
            events,
            sequence=sequence,
        )
        arguments: dict[str, object] = {
            "executor": executor,
            "destination": destination,
            "binding": binding,
            "runner": _Runner(events),
            "bridge_probe": _BridgeProbe(events),
            "tools": _tools(),
            "invocation_driver": _object(RelayInvocationDriver),
            "static_auth_secret": SECRET,
            "now": datetime(2026, 8, 23),
            "browser_timeout_seconds": 5.0,
            "runtime_timeout_seconds": 5.0,
            "cleanup_timeout_seconds": 15.0,
        }
        observation = executor_facade._run_consumed_relay_linux_executor(**arguments)
        key = executor_state._canonical_executor_key(executor, destination)
        retained.append((key, executor, arguments, observation))
    key_a, executor_a, arguments_a, observation_a = retained[0]
    key_b, _executor_b, arguments_b, observation_b = retained[1]
    result_a, result_b = (
        executor_inner_state._INNER_RESULTS[key_a],
        executor_inner_state._INNER_RESULTS[key_b],
    )
    terminal_a, terminal_b = (
        executor_inner_state._INNER_TERMINALS[key_a],
        executor_inner_state._INNER_TERMINALS[key_b],
    )
    authority_a, authority_b = (
        executor_inner_state._INNER_AUTHORITIES[key_a],
        executor_inner_state._INNER_AUTHORITIES[key_b],
    )
    executor_inner_state._INNER_RESULTS[key_a] = result_b
    executor_inner_state._INNER_RESULTS[key_b] = result_a
    executor_inner_state._INNER_TERMINALS[key_a] = terminal_b
    executor_inner_state._INNER_TERMINALS[key_b] = terminal_a
    executor_inner_state._INNER_AUTHORITIES[key_a] = authority_b
    executor_inner_state._INNER_AUTHORITIES[key_b] = authority_a
    crossed = arguments_b | {
        "executor": executor_a,
        "destination": arguments_a["destination"],
    }
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**crossed)
    executor_inner_state._INNER_RESULTS[key_a] = result_a
    executor_inner_state._INNER_RESULTS[key_b] = result_b
    executor_inner_state._INNER_TERMINALS[key_a] = terminal_a
    executor_inner_state._INNER_TERMINALS[key_b] = terminal_b
    executor_inner_state._INNER_AUTHORITIES[key_a] = authority_a
    executor_inner_state._INNER_AUTHORITIES[key_b] = authority_b

    destination_a = arguments_a["destination"]
    authority_for_a = executor_a._cleanup_authority
    executor_state._OWNER_KEYS[executor_a] = key_b
    executor_state._DESTINATION_KEYS[destination_a] = key_b
    executor_state._AUTHORITY_KEYS[authority_for_a] = key_b
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**crossed)
    executor_state._OWNER_KEYS[executor_a] = key_a
    executor_state._DESTINATION_KEYS[destination_a] = key_a
    executor_state._AUTHORITY_KEYS[authority_for_a] = key_a

    original_key_ref = result_a._key_ref
    object.__setattr__(result_a, "_key_ref", weakref.ref(key_b))
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments_a)
    object.__setattr__(result_a, "_key_ref", original_key_ref)
    assert executor_facade._run_consumed_relay_linux_executor(**arguments_a) is observation_a
    assert executor_facade._run_consumed_relay_linux_executor(**arguments_b) is observation_b


def test_missing_live_inner_record_cannot_authorize_release_and_is_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_resolve = executor_facade._resolve_or_intend_inner_evidence
    removed = False
    retained_key: object | None = None

    def resolve_with_missing_record(**kwargs):
        nonlocal removed, retained_key
        evidence = original_resolve(**kwargs)
        if not removed:
            removed = True
            retained_key = evidence.key
            executor_inner_state._INNER_RECORDS.pop(evidence.key)
            raise OSError("synthetic lost live inner record")
        return evidence

    monkeypatch.setattr(
        executor_facade,
        "_resolve_or_intend_inner_evidence",
        resolve_with_missing_record,
    )
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
    }
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert retained_key is not None
    assert not executor_inner_state._inner_live_evidence_is_absent(retained_key)
    assert not executor_state._release_unstarted_relay_linux_executor(
        executor._cleanup_authority,
    )
    result = executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert type(result) is RelayProbeObservation
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic inner authority store loss"),
        KeyboardInterrupt("synthetic inner authority store control"),
        SystemExit(11),
    ],
)
def test_inner_authority_store_return_loss_recovers_the_exact_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_store = executor_inner_state._store_inner_authority
    raised = False

    def store_with_loss(key: object, authority: tuple[object, ...]) -> None:
        nonlocal raised
        original_store(key, authority)
        if not raised:
            raised = True
            raise error

    monkeypatch.setattr(executor_inner_state, "_store_inner_authority", store_with_loss)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
    }
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else executor_state._RelayLinuxExecutorError
    )
    with pytest.raises(expected) as caught:
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert caught.value is error
    key = executor_state._canonical_executor_key(executor, destination)
    assert key in executor_inner_state._INNER_AUTHORITIES
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_inner_state._inner_live_evidence_is_absent(key)
    result = executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert type(result) is RelayProbeObservation
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic inner terminal store loss"),
        KeyboardInterrupt("synthetic inner terminal store control"),
        SystemExit(12),
    ],
)
def test_inner_terminal_store_return_loss_does_not_expose_a_partial_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_store = executor_inner_state._store_inner_terminal
    raised = False

    def store_with_loss(key: object, terminal: tuple[object, ...]) -> None:
        nonlocal raised
        original_store(key, terminal)
        if not raised:
            raised = True
            raise error

    monkeypatch.setattr(executor_inner_state, "_store_inner_terminal", store_with_loss)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 0.2,
    }
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else executor_state._RelayLinuxExecutorError
    )
    with pytest.raises(expected) as caught:
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert caught.value is error
    key = executor_state._canonical_executor_key(executor, destination)
    assert key in executor_inner_state._INNER_TERMINALS
    assert executor_inner_state._inner_result(key) is None
    result = executor_facade._run_consumed_relay_linux_executor(
        **(arguments | {"cleanup_timeout_seconds": 15.0}),
    )
    assert type(result) is RelayProbeObservation
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic inner retirement return loss"),
        KeyboardInterrupt("synthetic inner retirement control"),
        SystemExit(9),
    ],
)
def test_inner_retirement_return_loss_finishes_outer_cleanup_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_retire = executor_cleanup._retire_settled_inner
    raised = False

    def retire_with_loss(evidence):
        nonlocal raised
        result = original_retire(evidence)
        if result and not raised:
            raised = True
            raise error
        return result

    monkeypatch.setattr(executor_cleanup, "_retire_settled_inner", retire_with_loss)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
    }
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else executor_state._RelayLinuxExecutorError
    )
    with pytest.raises(expected) as caught:
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert caught.value is error
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS
    assert type(executor_facade._run_consumed_relay_linux_executor(**arguments)) is (
        RelayProbeObservation
    )


def test_cleanup_wait_control_is_retained_through_full_outer_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    executor, destination, _built, binding = _consumed_running_executor(
        tmp_path,
        monkeypatch,
        events,
    )
    original_wait = executor_cleanup._wait
    control = KeyboardInterrupt("synthetic outer cleanup wait control")
    raised = False

    def wait_with_control(evidence, deadline):
        nonlocal raised
        if not raised:
            raised = True
            raise control
        return original_wait(evidence, deadline)

    monkeypatch.setattr(executor_cleanup, "_wait", wait_with_control)
    arguments = {
        "executor": executor,
        "destination": destination,
        "binding": binding,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": SECRET,
        "now": datetime(2026, 8, 23),
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
    }
    with pytest.raises(KeyboardInterrupt) as caught:
        executor_facade._run_consumed_relay_linux_executor(**arguments)
    assert caught.value is control
    assert raised
    assert not executor_inner_state._INNER_RECORDS
    assert not executor_state._EXECUTORS
    assert type(executor_facade._run_consumed_relay_linux_executor(**arguments)) is (
        RelayProbeObservation
    )

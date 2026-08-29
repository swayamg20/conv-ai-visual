"""Focused lifetime tests for one executor-owned workspace build."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry as process_facade_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as process_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as process_state
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer as build_consumer
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_contract as consumer_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values as consumer_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_facade as build_facade
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract as process_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract as receipt_contract
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
import scripts.voice_pipecat_e2e_relay_linux_executor_build_contract as executor_contract
import scripts.voice_pipecat_e2e_relay_linux_executor_build_linearize as executor_linearize
import scripts.voice_pipecat_e2e_relay_linux_executor_build_release as executor_release
import scripts.voice_pipecat_e2e_relay_linux_executor_cleanup as executor_cleanup
import scripts.voice_pipecat_e2e_relay_linux_executor_inner_state as executor_inner_state
import scripts.voice_pipecat_e2e_relay_linux_executor_state as executor_state
import scripts.voice_pipecat_e2e_relay_linux_executor_workspace as executor_workspace
import scripts.voice_pipecat_e2e_relay_owner_state as relay_owner_state
import scripts.voice_pipecat_e2e_relay_probe as relay_probe
from scripts.voice_pipecat_e2e_relay_invocation import RelayInvocationDriver
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT
from tests.relay_linux_runtime_proof import synthetic_runtime_proof
from tests.test_voice_pipecat_e2e_coturn_host import _tools
from tests.test_voice_pipecat_e2e_relay_linux_executor import _consumed_running_executor
from tests.test_voice_pipecat_e2e_relay_owner import (
    SECRET,
    _BridgeProbe,
    _install_synthetic_lifecycle,
    _object,
    _Runner,
)


@pytest.fixture(autouse=True)
def _isolated_consume_state() -> None:
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


@pytest.fixture
def _synthetic_inner_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        executor_inner_state,
        "_inner_settlement_matches_build",
        lambda _build: True,
    )


def _source() -> RelayProbeSource:
    return RelayProbeSource(relay_probe._SOURCE_TOKEN, commit_sha="a" * 40)


def _bound_executor(tmp_path: Path):
    run_parent = tmp_path / "runs"
    run_parent.mkdir(mode=0o700)
    node = tmp_path / "node"
    node.write_bytes(b"synthetic-node\n")
    node.chmod(0o700)
    destination = executor_state._new_relay_linux_executor_destination(
        source_root=WEB_ROOT,
        run_parent=run_parent.resolve(),
        node=node.resolve(),
        run_id="executor-consume",
        source=_source(),
    )
    executor = executor_state._preown_relay_linux_executor(destination)
    workspace = executor._workspace_owner
    bundle = worker_state._new_relay_linux_build_workspace_worker_bundle(workspace)
    construction, coherent = worker_thread._new_relay_linux_build_workspace_worker_thread(
        workspace,
        bundle,
    )
    assert construction is not None and coherent is True
    assert executor_workspace._bind_relay_linux_executor_workspace(
        executor,
        bundle,
        construction,
    )
    return executor, destination, bundle, construction


def _active_build(executor, bundle, construction):
    workspace = executor._workspace_owner
    owner_token = workspace._cleanup_authority._key
    record_token = construction._record_token
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    deadline = float(time.monotonic() + 5.0)
    request_fingerprint = process_contract._workspace_request_spawn_fingerprint(workspace._request)
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=deadline,
        expected_spawn_fingerprint=request_fingerprint,
    )
    assert (
        build_values._claim_workspace_build_command(
            command,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
        )
        == deadline
    )
    assert build_values._bind_workspace_build_command_controller(
        command,
        controller=bundle._controller,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    command_state = build_values._COMMANDS[command]
    build_values._store_command_state(
        command,
        (*command_state[:4], "running", command_state[5]),
    )
    process_owner_token = object()
    cleanup_authority = process_state._RelayLinuxBuildCleanupAuthority(
        process_state._AUTHORITY_TOKEN,
        key=object(),
        owner_token=process_owner_token,
    )
    process_receipt = process_state._RelayLinuxBuildProcessReceipt(
        process_state._RECEIPT_TOKEN,
        owner_token=process_owner_token,
    )
    build_values._PROCESS_ASSOCIATIONS[command] = (
        owner_token,
        record_token,
        process_owner_token,
        cleanup_authority,
        request_fingerprint,
        process_receipt,
        "released-zero",
    )
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner_token,
        record_token=record_token,
        output_digest=b"d" * 32,
        runtime_proof=synthetic_runtime_proof(
            owner_token,
            record_token,
            digest=b"d" * 32,
        ),
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    assert build_receipt._activate_workspace_built_receipt(
        built,
        owner_token,
        record_token,
        operation_deadline=deadline,
    )
    assert fs_contract._revoke_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    published, acquired = bundle._built_destination._publish_before(
        worker_state._DESTINATION_TOKEN,
        owner_token,
        built,
        deadline,
    )
    assert acquired and published is built
    assert build_receipt._workspace_built_receipt_is_stable_handoff(
        built,
        owner_token,
        record_token,
    )
    assert process_facade_registry._build_process_registries_are_empty()
    return SimpleNamespace(
        built=built,
        command=command,
        deadline=deadline,
        owner_token=owner_token,
        prepared=prepared,
        process_receipt=process_receipt,
        record_token=record_token,
        runtime_proof=build_receipt._BUILT_LEASES[built][3],
    )


def _evidence(binding):
    key = executor_binding._KEYS_BY_BINDING[binding]
    return executor_binding._EVIDENCE_BY_KEY[key]


def _assert_consume_attempt_restored(executor, destination, active) -> None:
    assert build_receipt._BUILT_LEASES[active.built][5] == "active"
    assert not consumer_values._BUILD_CONSUMERS
    assert not consumer_values._BUILT_BY_CONSUMER
    assert not consumer_values._CONSUMED_HISTORY
    assert not consumer_values._CONSUMER_TOMBSTONES
    assert not process_registry._ABSENCE_RESERVATIONS
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_binding._KEYS_BY_BINDING
    assert not executor_binding._BINDINGS_BY_BUILT
    assert not executor_binding._RELEASE_BINDINGS
    assert not executor_binding._BUILD_RETIREMENTS
    key = executor_state._canonical_executor_key(executor, destination)
    assert key is not None
    record = executor_state._EXECUTORS[key]
    assert record[4] is None and record[5] == "workspace-bound"


def _forgotten_consumed_build(executor, destination, active):
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    cleanup_deadline = time.monotonic() + 1.0
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._record_workspace_built_consumer_revoked(active.built)
    assert executor_consume._acknowledge_relay_linux_executor_built_revoked(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_receipt._BUILT_LEASES[active.built][3] is active.runtime_proof
    assert executor_consume._executor_consumed_build_allows_workspace_release(binding)
    assert build_receipt._forget_workspace_built_receipt(active.command)
    assert build_consumer._workspace_built_consumer_is_forgotten(
        active.built,
        evidence.consumer,
    )
    assert executor_consume._executor_consumed_build_is_forgotten(binding)
    return binding, evidence


def test_consume_is_one_shot_and_returns_only_an_opaque_binding(tmp_path: Path) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    replay = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)

    assert replay is binding
    assert not binding
    assert repr(binding) == "_RelayLinuxExecutorBuiltBinding()"
    assert not any(
        hasattr(binding, name)
        for name in (
            "request",
            "workspace",
            "dist_path",
            "digest",
            "runtime_proof",
            "owner",
            "executor",
        )
    )
    assert evidence.runtime_proof is active.runtime_proof
    assert build_receipt._BUILT_LEASES[active.built][3] is active.runtime_proof
    assert build_receipt._BUILT_LEASES[active.built][5] == "consumed"
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert executor_state._EXECUTORS[evidence.key][5] == "build-consumed"


def test_consumed_binding_rejects_a_distinct_runtime_proof_identity(
    tmp_path: Path,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    canonical = build_receipt._BUILT_LEASES[active.built]
    replacement = synthetic_runtime_proof(
        active.owner_token,
        active.record_token,
        digest=evidence.digest,
    )
    assert replacement is not active.runtime_proof
    assert replacement._matches(
        owner_token=active.owner_token,
        record_token=active.record_token,
        output_digest=evidence.digest,
    )

    build_receipt._BUILT_LEASES[active.built] = (
        *canonical[:3],
        replacement,
        *canonical[4:],
    )
    assert not executor_consume._consumed_binding_matches(evidence)
    assert not executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=time.monotonic() + 1.0,
    )

    build_receipt._BUILT_LEASES[active.built] = canonical
    assert executor_consume._consumed_binding_matches(evidence)


def test_settled_inner_cleanup_rejects_crosswired_proof_before_release_and_ack(
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
    evidence = _evidence(binding)
    rejected = {"release": 0, "ack": 0}
    original_release = executor_cleanup._release_relay_linux_executor_built_use
    original_ack = executor_cleanup._acknowledge_relay_linux_executor_built_revoked

    def crosswire_once(phase: str, operation, cleanup_deadline: float) -> bool:
        canonical = build_receipt._BUILT_LEASES[built]
        assert canonical[3] is evidence.runtime_proof and canonical[5] == phase
        replacement = synthetic_runtime_proof(
            evidence.owner_token,
            evidence.record_token,
            digest=evidence.digest,
        )
        build_receipt._BUILT_LEASES[built] = (
            *canonical[:3],
            replacement,
            *canonical[4:],
        )
        try:
            assert not operation(binding, cleanup_deadline=cleanup_deadline)
        finally:
            build_receipt._BUILT_LEASES[built] = canonical
        rejected["release" if phase == "consumed" else "ack"] += 1
        return operation(binding, cleanup_deadline=cleanup_deadline)

    def release_with_crosswire(candidate, *, cleanup_deadline: float) -> bool:
        assert candidate is binding
        assert executor_inner_state._inner_settlement_matches_build(evidence)
        if rejected["release"] == 0:
            return crosswire_once("consumed", original_release, cleanup_deadline)
        return original_release(candidate, cleanup_deadline=cleanup_deadline)

    def ack_with_crosswire(candidate, *, cleanup_deadline: float) -> bool:
        assert candidate is binding
        if rejected["ack"] == 0:
            return crosswire_once("revoked", original_ack, cleanup_deadline)
        return original_ack(candidate, cleanup_deadline=cleanup_deadline)

    monkeypatch.setattr(
        executor_cleanup,
        "_release_relay_linux_executor_built_use",
        release_with_crosswire,
    )
    monkeypatch.setattr(
        executor_cleanup,
        "_acknowledge_relay_linux_executor_built_revoked",
        ack_with_crosswire,
    )

    executor_facade._run_consumed_relay_linux_executor(
        executor=executor,
        destination=destination,
        binding=binding,
        runner=_Runner(events),
        bridge_probe=_BridgeProbe(events),
        tools=_tools(),
        invocation_driver=_object(RelayInvocationDriver),
        static_auth_secret=SECRET,
        now=datetime(2026, 8, 29),
        browser_timeout_seconds=5.0,
        runtime_timeout_seconds=5.0,
        cleanup_timeout_seconds=15.0,
    )

    assert rejected == {"release": 1, "ack": 1}
    assert built not in build_receipt._BUILT_LEASES
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_binding._RELEASE_BINDINGS
    assert not executor_inner_state._INNER_RECORDS
    assert not relay_owner_state._REGISTRY
    assert not executor._workspace_owner._request._run_root.exists()


def test_malformed_stored_runtime_proof_fails_closed_through_revoke_and_forget(
    tmp_path: Path,
) -> None:
    executor, _destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    canonical = build_receipt._BUILT_LEASES[active.built]
    malformed = object.__new__(fs_output_values._WorkspaceBuiltRuntimeProof)
    build_receipt._BUILT_LEASES[active.built] = (
        *canonical[:3],
        malformed,
        *canonical[4:],
    )

    assert malformed._canonical_digest() is None
    assert not malformed._matches_canonical(
        owner_token=active.owner_token,
        record_token=active.record_token,
    )
    assert not active.built._matches(
        active.owner_token,
        active.record_token,
        require_active=True,
    )
    assert not build_receipt._workspace_built_receipt_is_stable_handoff(
        active.built,
        active.owner_token,
        active.record_token,
    )
    assert not consumer_contract._active_lease_shape(
        active.built,
        build_receipt._BUILT_LEASES[active.built],
    )
    assert not build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=time.monotonic() + 1.0,
    )
    assert build_receipt._BUILT_LEASES[active.built][5] == "revoked"
    assert not build_receipt._workspace_built_receipt_is_revoked(
        active.built,
        active.owner_token,
        active.record_token,
    )
    assert not build_receipt._workspace_built_lease_is_revoked_or_absent(
        active.command,
        active.owner_token,
        active.record_token,
    )
    assert not build_receipt._forget_workspace_built_receipt(active.command)
    assert build_receipt._BUILT_BY_COMMAND.get(active.command) is active.built
    assert build_receipt._BUILT_LEASES[active.built][3] is malformed


def test_consume_rejects_at_the_canonical_deadline_without_changing_the_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    with monkeypatch.context() as deadline_patch:
        deadline_patch.setattr(
            executor_linearize.time,
            "monotonic",
            lambda: active.deadline,
        )
        with pytest.raises(executor_state._RelayLinuxExecutorError):
            executor_consume._consume_relay_linux_executor_built_lease(
                executor=executor,
                destination=destination,
                built=active.built,
                operation_deadline=active.deadline,
            )

    assert build_receipt._BUILT_LEASES[active.built][5] == "active"
    assert not consumer_values._BUILD_CONSUMERS
    assert not consumer_values._BUILT_BY_CONSUMER
    assert not consumer_values._CONSUMED_HISTORY
    assert not process_registry._ABSENCE_RESERVATIONS
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_binding._KEYS_BY_BINDING
    assert not executor_binding._BINDINGS_BY_BUILT
    record = next(iter(executor_state._EXECUTORS.values()))
    assert record[5] == "workspace-bound" and record[4] is None

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert build_receipt._BUILT_LEASES[active.built][5] == "consumed"
    assert _evidence(binding).built is active.built


@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic consumed-store return loss"),
        lambda: KeyboardInterrupt("synthetic consumed-store control"),
        lambda: SystemExit("synthetic consumed-store exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_consumed_store_return_loss_replays_after_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_factory,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    error = error_factory()
    clock = [active.deadline - 1.0]
    original_store = executor_consume._store_consumed_workspace_built_lease
    raised = False

    def store_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal raised
        original_store(*args, **kwargs)
        if not raised:
            raised = True
            clock[0] = active.deadline
            raise error

    monkeypatch.setattr(
        executor_consume,
        "_store_consumed_workspace_built_lease",
        store_then_raise,
    )
    monkeypatch.setattr(
        executor_linearize,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else (executor_state._RelayLinuxExecutorError)
    )
    with pytest.raises(expected) as captured:
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert captured.value is error

    assert raised
    assert build_receipt._BUILT_LEASES[active.built][5] == "consumed"
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    assert evidence.runtime_proof is active.runtime_proof
    assert build_receipt._BUILT_LEASES[active.built][3] is active.runtime_proof
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )


def test_consume_rechecks_deadline_inside_the_final_effect_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    clock = [active.deadline - 1.0]
    original_store = executor_consume._store_consumed_workspace_built_lease
    advanced = False

    def advance_before_store(*args: object, **kwargs: object) -> None:
        nonlocal advanced
        if not advanced:
            advanced = True
            clock[0] = active.deadline
        original_store(*args, **kwargs)

    monkeypatch.setattr(
        executor_consume,
        "_store_consumed_workspace_built_lease",
        advance_before_store,
    )
    monkeypatch.setattr(
        executor_linearize,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    assert advanced
    _assert_consume_attempt_restored(executor, destination, active)

    clock[0] = active.deadline - 1.0
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        _evidence(binding).consumer,
    )


@pytest.mark.parametrize("target", ("gate", "controller"))
@pytest.mark.parametrize(
    "value_factory",
    (lambda: 1, lambda: None, object),
    ids=("integer", "none", "object"),
)
def test_consume_rejects_non_boolean_cancellation_state_at_the_final_fence(
    tmp_path: Path,
    target: str,
    value_factory,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    gate = build_values._COMMAND_GATES[active.command]
    if target == "gate":
        gate.cancel_requested = value_factory()
    else:
        object.__setattr__(bundle._controller, "_cancel_requested", value_factory())

    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    _assert_consume_attempt_restored(executor, destination, active)

    gate.cancel_requested = False
    object.__setattr__(bundle._controller, "_cancel_requested", False)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        _evidence(binding).consumer,
    )


@pytest.mark.parametrize("drift", ("deleted", "malformed", "crosswired"))
def test_consume_rejects_worker_pin_drift_at_the_final_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    key = executor_state._canonical_executor_key(executor, destination)
    assert key is not None
    original_pinned = executor_consume._pinned_workspace_matches
    drifted = False

    def drift_after_precheck(evidence) -> bool:
        nonlocal drifted
        matched = original_pinned(evidence)
        if matched and not drifted:
            drifted = True
            if drift == "deleted":
                worker_consumer._CONSUMERS.pop(bundle, None)
            elif drift == "malformed":
                worker_consumer._CONSUMERS[bundle] = object()  # type: ignore[assignment]
            else:
                worker_consumer._CONSUMERS[bundle] = (construction, object())
        return matched

    monkeypatch.setattr(
        executor_consume,
        "_pinned_workspace_matches",
        drift_after_precheck,
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    assert drifted
    _assert_consume_attempt_restored(executor, destination, active)
    assert bundle in worker_registry._RECORDS
    assert (
        executor_workspace._resolve_relay_linux_executor_workspace(
            executor,
            destination,
        )
        is None
    )

    worker_consumer._CONSUMERS[bundle] = (construction, key)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        _evidence(binding).consumer,
    )


@pytest.mark.parametrize("effect_first", [False, True], ids=("before", "after"))
@pytest.mark.parametrize(
    "cut",
    ("_store_build_consumer", "_store_built_by_consumer"),
)
@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic consumer intent failure"),
        lambda: KeyboardInterrupt("synthetic consumer intent control"),
        lambda: SystemExit("synthetic consumer intent exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_consumer_intent_store_loss_restores_a_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect_first: bool,
    cut: str,
    error_factory,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    original = getattr(build_consumer, cut)
    error = error_factory()
    raised = False

    def store_with_loss(*args: object, **kwargs: object) -> None:
        nonlocal raised
        if not raised:
            raised = True
            if effect_first:
                original(*args, **kwargs)
            raise error
        original(*args, **kwargs)

    monkeypatch.setattr(build_consumer, cut, store_with_loss)
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else executor_state._RelayLinuxExecutorError
    )
    with pytest.raises(expected) as captured:
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert captured.value is error
    assert raised
    _assert_consume_attempt_restored(executor, destination, active)

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )


def test_held_publication_gate_times_out_without_stranding_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    gate = build_values._COMMAND_GATES[active.command]
    original_acquire = build_values._acquire_command_publication_gate

    def acquire_at_canonical_deadline(command, deadline):
        original_monotonic = build_values.time.monotonic
        try:
            build_values.time.monotonic = lambda: deadline
            return original_acquire(command, deadline)
        finally:
            build_values.time.monotonic = original_monotonic

    gate.publication_lock.acquire()
    try:
        monkeypatch.setattr(
            executor_consume,
            "_acquire_command_publication_gate",
            acquire_at_canonical_deadline,
        )
        with pytest.raises(executor_state._RelayLinuxExecutorError):
            executor_consume._consume_relay_linux_executor_built_lease(
                executor=executor,
                destination=destination,
                built=active.built,
                operation_deadline=active.deadline,
            )
    finally:
        gate.publication_lock.release()

    _assert_consume_attempt_restored(executor, destination, active)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert executor_state._EXECUTORS[evidence.key][5] == "build-consumed"
    assert not process_registry._ABSENCE_RESERVATIONS


def test_expired_retry_releases_an_exact_stranded_absence_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    key = executor_state._canonical_executor_key(executor, destination)
    assert key is not None
    stranded = process_facade_registry._reserve_build_process_absence(key)
    original_fresh = executor_consume._fresh_active_consumption_deadline
    expired = False

    def expire_during_freshness(*args: object, **kwargs: object) -> float:
        nonlocal expired
        if expired:
            return original_fresh(*args, **kwargs)
        expired = True
        original_monotonic = receipt_contract.time.monotonic
        try:
            receipt_contract.time.monotonic = lambda: active.deadline
            return original_fresh(*args, **kwargs)
        finally:
            receipt_contract.time.monotonic = original_monotonic

    assert process_facade_registry._build_process_absence_reservation_matches(stranded, key)
    monkeypatch.setattr(
        executor_consume,
        "_fresh_active_consumption_deadline",
        expire_during_freshness,
    )
    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    assert expired
    _assert_consume_attempt_restored(executor, destination, active)

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        _evidence(binding).consumer,
    )


def test_stale_precheck_replays_the_winner_after_publication_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    b_prechecked = threading.Event()
    resume_b = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []
    original_reconcile = executor_consume._reconcile_existing_consumption
    b_calls = 0
    b_thread: threading.Thread

    def pause_b_after_precheck(*args: object, **kwargs: object):
        nonlocal b_calls
        result = original_reconcile(*args, **kwargs)
        if threading.current_thread() is b_thread:
            b_calls += 1
            if b_calls == 1:
                b_prechecked.set()
                assert resume_b.wait(2.0)
        return result

    def consume_b() -> None:
        try:
            results.append(
                executor_consume._consume_relay_linux_executor_built_lease(
                    executor=executor,
                    destination=destination,
                    built=active.built,
                    operation_deadline=active.deadline,
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(
        executor_consume,
        "_reconcile_existing_consumption",
        pause_b_after_precheck,
    )
    b_thread = threading.Thread(target=consume_b)
    b_thread.start()
    assert b_prechecked.wait(2.0)
    winner = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    resume_b.set()
    b_thread.join(2.0)

    assert not b_thread.is_alive()
    assert not errors
    assert results == [winner]
    assert not process_registry._ABSENCE_RESERVATIONS
    assert (
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
        is winner
    )


def test_stale_precheck_cannot_abort_an_intent_paused_before_consume_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    a_at_effect = threading.Event()
    resume_a = threading.Event()
    b_prechecked = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []
    original_reconcile = executor_consume._reconcile_existing_consumption
    original_store = executor_consume._store_consumed_workspace_built_lease
    b_calls = 0
    a_thread: threading.Thread
    b_thread: threading.Thread

    def mark_b_precheck(*args: object, **kwargs: object):
        nonlocal b_calls
        result = original_reconcile(*args, **kwargs)
        if threading.current_thread() is b_thread:
            b_calls += 1
            if b_calls == 1:
                b_prechecked.set()
        return result

    def pause_a_before_effect(*args: object, **kwargs: object) -> None:
        if threading.current_thread() is a_thread:
            a_at_effect.set()
            assert resume_a.wait(2.0)
        original_store(*args, **kwargs)

    def consume() -> None:
        try:
            results.append(
                executor_consume._consume_relay_linux_executor_built_lease(
                    executor=executor,
                    destination=destination,
                    built=active.built,
                    operation_deadline=active.deadline,
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(
        executor_consume,
        "_reconcile_existing_consumption",
        mark_b_precheck,
    )
    monkeypatch.setattr(
        executor_consume,
        "_store_consumed_workspace_built_lease",
        pause_a_before_effect,
    )
    a_thread = threading.Thread(target=consume)
    b_thread = threading.Thread(target=consume)
    a_thread.start()
    assert a_at_effect.wait(2.0)
    b_thread.start()
    assert b_prechecked.wait(2.0)
    resume_a.set()
    a_thread.join(2.0)
    b_thread.join(2.0)

    assert not a_thread.is_alive() and not b_thread.is_alive()
    assert not errors
    assert len(results) == 2 and results[0] is results[1]
    binding = results[0]
    evidence = _evidence(binding)
    assert build_receipt._BUILT_LEASES[active.built][5] == "consumed"
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert executor_state._EXECUTORS[evidence.key][5] == "build-consumed"
    assert not process_registry._ABSENCE_RESERVATIONS


def test_raw_consumed_partial_state_replays_after_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    key = executor_state._canonical_executor_key(executor, destination)
    assert key is not None
    reservation = process_facade_registry._reserve_build_process_absence(key)
    failures: list[BaseException | None] = [None, None]
    evidence = executor_consume._resolve_or_intend_binding(
        executor,
        destination,
        active.built,
        reservation,
        failures,
    )
    assert failures == [None, None]
    publication_gate = build_values._acquire_command_publication_gate(
        active.command,
        active.deadline,
    )
    try:
        gate = build_values._acquire_command_gate(active.command, active.deadline)
        try:
            assert build_consumer._intend_workspace_built_consumption(
                receipt=active.built,
                owner=executor._workspace_owner,
                bundle=bundle,
                construction=construction,
                consumer=evidence.consumer,
                consumer_key=evidence.key,
                admission=evidence.reservation,
            )
            lease = build_receipt._BUILT_LEASES[active.built]
            build_receipt._store_built_lease(
                active.built,
                (*lease[:5], "consumed"),
            )
        finally:
            gate.lock.release()
    finally:
        publication_gate.publication_lock.release()

    state = consumer_values._BUILD_CONSUMERS[active.built]
    assert state[13] == "consume-intended"
    assert executor_state._EXECUTORS[evidence.key][5] == "consume-intended"
    assert build_consumer._workspace_built_consumption_effect_is_reconcilable(
        active.built,
        evidence.consumer,
    )
    monkeypatch.setattr(
        executor_linearize,
        "time",
        SimpleNamespace(monotonic=lambda: active.deadline),
    )

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert binding is evidence.binding
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert executor_state._EXECUTORS[evidence.key][5] == "build-consumed"
    assert not process_registry._ABSENCE_RESERVATIONS


@pytest.mark.parametrize(
    "cut",
    (
        "_store_evidence_by_key",
        "_store_key_by_binding",
        "_store_binding_by_built",
        "_store_executor_record",
    ),
)
@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic consume publication loss"),
        lambda: KeyboardInterrupt("synthetic consume publication control"),
        lambda: SystemExit("synthetic consume publication exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_consume_publication_return_loss_repairs_exact_maps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
    error_factory,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    original = getattr(executor_contract, cut)
    error = error_factory()
    raised = False

    def effect_then_raise(*args: object, **kwargs: object):
        nonlocal raised
        result = original(*args, **kwargs)
        if not raised:
            raised = True
            raise error
        return result

    monkeypatch.setattr(executor_contract, cut, effect_then_raise)
    expected = (
        type(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit))
        else (executor_state._RelayLinuxExecutorError)
    )
    with pytest.raises(expected) as captured:
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        assert captured.value is error

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    assert evidence.runtime_proof is active.runtime_proof
    assert build_receipt._BUILT_LEASES[active.built][3] is active.runtime_proof
    assert len(executor_binding._EVIDENCE_BY_KEY) == 1
    assert executor_binding._EVIDENCE_BY_KEY.get(evidence.key) is evidence
    assert len(executor_binding._KEYS_BY_BINDING) == 1
    assert executor_binding._KEYS_BY_BINDING.get(binding) is evidence.key
    assert len(executor_binding._BINDINGS_BY_BUILT) == 1
    assert executor_binding._BINDINGS_BY_BUILT.get(active.built) is binding
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )


@pytest.mark.parametrize("effect_first", [False, True], ids=("before", "after"))
@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic use-release failure"),
        lambda: KeyboardInterrupt("synthetic use-release control"),
        lambda: SystemExit("synthetic use-release exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_use_release_reconciles_outer_phase_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect_first: bool,
    error_factory,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    original_store = executor_release._store_outer_phase
    error = error_factory()
    raised = False

    def store_with_loss(*args: object, **kwargs: object) -> bool:
        nonlocal raised
        if not raised and args[2] == "use-release-intended":
            raised = True
            if effect_first:
                original_store(*args, **kwargs)
            raise error
        return original_store(*args, **kwargs)

    monkeypatch.setattr(executor_release, "_store_outer_phase", store_with_loss)
    with pytest.raises(type(error)) as captured:
        executor_consume._release_relay_linux_executor_built_use(
            binding,
            cleanup_deadline=float(time.monotonic() + 1.0),
        )
    assert captured.value is error
    consumer_state = consumer_values._BUILD_CONSUMERS[active.built]
    assert consumer_state[5] is evidence.consumer
    assert consumer_state[13] == "use-released"

    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=float(time.monotonic() + 1.0),
    )
    assert executor_state._EXECUTORS[evidence.key][5] == "use-release-intended"


@pytest.mark.parametrize("effect_first", [False, True], ids=("before", "after"))
@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic acknowledgment failure"),
        lambda: KeyboardInterrupt("synthetic acknowledgment control"),
        lambda: SystemExit("synthetic acknowledgment exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_acknowledgment_reconciles_generic_phase_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect_first: bool,
    error_factory,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    cleanup_deadline = float(time.monotonic() + 1.0)
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._record_workspace_built_consumer_revoked(active.built)
    original_acknowledge = executor_release._acknowledge_workspace_built_consumer_revoked
    error = error_factory()
    raised = False

    def acknowledge_with_loss(*args: object, **kwargs: object) -> bool:
        nonlocal raised
        if not raised:
            raised = True
            if effect_first:
                original_acknowledge(*args, **kwargs)
            raise error
        return original_acknowledge(*args, **kwargs)

    monkeypatch.setattr(
        executor_release,
        "_acknowledge_workspace_built_consumer_revoked",
        acknowledge_with_loss,
    )
    with pytest.raises(type(error)) as captured:
        executor_consume._acknowledge_relay_linux_executor_built_revoked(
            binding,
            cleanup_deadline=cleanup_deadline,
        )
    assert captured.value is error
    assert executor_state._EXECUTORS[evidence.key][5] == "build-revoked-acknowledged"

    assert executor_consume._acknowledge_relay_linux_executor_built_revoked(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._workspace_built_consumer_is_acknowledged(
        active.built,
        evidence.consumer,
    )


def test_consumed_build_survives_expiry_and_cancel_until_release_acknowledgment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)

    monkeypatch.setattr(
        receipt_contract,
        "time",
        SimpleNamespace(monotonic=lambda: active.deadline),
    )
    bundle._controller._request_cancel()
    assert not active.built._matches(
        active.owner_token,
        active.record_token,
        require_active=True,
    )
    assert build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )

    cleanup_deadline = float(time.monotonic() + 1.0)
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    partially_reconciled = consumer_values._BUILD_CONSUMERS[active.built]
    assert partially_reconciled[5] is evidence.consumer
    assert partially_reconciled[13] == "use-released"
    assert build_consumer._workspace_built_consumer_allows_revocation(active.built)
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._record_workspace_built_consumer_revoked(active.built)
    assert not build_consumer._workspace_built_consumer_cleanup_is_acknowledged(active.built)
    assert executor_consume._acknowledge_relay_linux_executor_built_revoked(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._workspace_built_consumer_cleanup_is_acknowledged(active.built)
    assert executor_consume._executor_consumed_build_allows_workspace_release(binding)
    assert build_receipt._forget_workspace_built_receipt(active.command)
    assert build_consumer._workspace_built_consumer_is_forgotten(
        active.built,
        evidence.consumer,
    )
    assert build_consumer._workspace_built_consumer_registries_are_empty()
    assert executor_consume._executor_consumed_build_is_forgotten(binding)


def test_worker_holds_active_only_before_deadline_but_consumed_until_use_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    clock = [active.deadline - 1.0]
    monkeypatch.setattr(
        build_consumer,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )

    assert build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )
    clock[0] = active.deadline
    assert not build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )

    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    assert build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=time.monotonic() + 1.0,
    )
    assert build_consumer._workspace_built_consumer_is_use_released(
        active.built,
        evidence.consumer,
    )
    assert not build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )


@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic built revocation return loss"),
        lambda: KeyboardInterrupt("synthetic built revocation control"),
        lambda: SystemExit("synthetic built revocation exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_worker_reconciles_revoked_lease_with_use_released_consumer(
    tmp_path: Path,
    error_factory,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    cleanup_deadline = time.monotonic() + 1.0
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._workspace_built_consumer_allows_revocation(active.built)
    error = error_factory()

    with pytest.raises(type(error)) as captured:
        assert build_receipt._revoke_workspace_built_receipt(
            active.built,
            active.owner_token,
            active.record_token,
            cleanup_deadline=cleanup_deadline,
        )
        raise error
    assert captured.value is error
    assert build_receipt._workspace_built_receipt_is_revoked(
        active.built,
        active.owner_token,
        active.record_token,
    )
    partially_reconciled = consumer_values._BUILD_CONSUMERS[active.built]
    assert partially_reconciled[5] is evidence.consumer
    assert partially_reconciled[13] == "use-released"

    assert build_consumer._workspace_built_consumer_allows_revocation(active.built)
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._record_workspace_built_consumer_revoked(active.built)
    assert build_consumer._workspace_built_consumer_is_revoked(
        active.built,
        evidence.consumer,
    )


def test_consume_rejects_nonretired_extra_source_evidence(tmp_path: Path) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    extra_key = executor_state._RelayLinuxExecutorKey()
    extra_source = _source()
    executor_state._SOURCE_EVIDENCE[extra_key] = (
        extra_source,
        extra_source.commit_sha,
        WEB_ROOT,
    )

    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    assert build_receipt._BUILT_LEASES[active.built][5] == "active"
    assert not consumer_values._BUILD_CONSUMERS
    assert not consumer_values._BUILT_BY_CONSUMER
    assert not process_registry._ABSENCE_RESERVATIONS
    assert not executor_binding._EVIDENCE_BY_KEY

    del executor_state._SOURCE_EVIDENCE[extra_key]
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert executor_consume._consumed_binding_matches(_evidence(binding))


@pytest.mark.parametrize(
    "mapping_name",
    ("_BUILD_RETIREMENTS", "_RELEASE_BINDINGS"),
)
def test_consume_rejects_poisoned_cleanup_evidence(
    tmp_path: Path,
    mapping_name: str,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    key = executor_state._OWNER_KEYS[executor]
    mapping = getattr(executor_binding, mapping_name)
    mapping[key] = object()

    with pytest.raises(executor_state._RelayLinuxExecutorError):
        executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=active.built,
            operation_deadline=active.deadline,
        )
    assert build_receipt._BUILT_LEASES[active.built][5] == "active"
    assert not consumer_values._BUILD_CONSUMERS
    assert not consumer_values._BUILT_BY_CONSUMER
    assert not process_registry._ABSENCE_RESERVATIONS
    assert not executor_binding._EVIDENCE_BY_KEY

    del mapping[key]
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    assert executor_consume._consumed_binding_matches(_evidence(binding))


@pytest.mark.parametrize("crosswire", ("consumer-key", "bundle"))
def test_consumer_rejects_crosswired_canonical_state(
    tmp_path: Path,
    crosswire: str,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    evidence = _evidence(binding)
    canonical_state = consumer_values._BUILD_CONSUMERS[active.built]
    malformed = list(canonical_state)
    if crosswire == "consumer-key":
        malformed[6] = executor_state._RelayLinuxExecutorKey()
    else:
        malformed[11] = worker_state._WorkspaceWorkerBundle(
            worker_state._BUNDLE_TOKEN,
            owner_token=active.owner_token,
            prepared_destination=bundle._prepared_destination,
        )
    consumer_values._BUILD_CONSUMERS[active.built] = tuple(malformed)

    assert not build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert not build_consumer._workspace_built_consumer_holds_worker(
        active.built,
        active.owner_token,
        active.record_token,
        controller=bundle._controller,
        lock_deadline=time.monotonic() + 1.0,
    )
    assert not executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=time.monotonic() + 1.0,
    )

    consumer_values._BUILD_CONSUMERS[active.built] = canonical_state
    assert build_consumer._workspace_built_consumer_is_in_use(
        active.built,
        evidence.consumer,
    )
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=time.monotonic() + 1.0,
    )


@pytest.mark.parametrize("tamper", ("digest", "process"))
def test_built_retirement_rejects_marker_tamper_after_lease_map_pops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding = executor_consume._consume_relay_linux_executor_built_lease(
        executor=executor,
        destination=destination,
        built=active.built,
        operation_deadline=active.deadline,
    )
    cleanup_deadline = time.monotonic() + 1.0
    assert executor_consume._release_relay_linux_executor_built_use(
        binding,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_consumer._record_workspace_built_consumer_revoked(active.built)
    assert executor_consume._acknowledge_relay_linux_executor_built_revoked(
        binding,
        cleanup_deadline=cleanup_deadline,
    )

    class _MarkerPopLoss(dict):
        raised = False

        def pop(self, key, default=None):
            if key is active.command and not self.raised:
                self.raised = True
                raise OSError("synthetic retirement-marker pop loss")
            return super().pop(key, default)

    retirement_markers = _MarkerPopLoss()
    monkeypatch.setattr(receipt_forget, "_RETIREMENTS", retirement_markers)
    with pytest.raises(OSError):
        build_receipt._forget_workspace_built_receipt(active.command)
    assert retirement_markers.raised
    assert active.command not in build_receipt._BUILT_BY_COMMAND
    assert active.built not in build_receipt._BUILT_LEASES
    exact_marker = retirement_markers[active.command]
    assert exact_marker[1] == active.runtime_proof.output.digest
    assert all(value is not active.runtime_proof for value in exact_marker)
    malformed = list(exact_marker)
    malformed[1 if tamper == "digest" else 2] = b"z" * 32 if tamper == "digest" else object()
    retirement_markers[active.command] = tuple(malformed)

    assert not build_receipt._forget_workspace_built_receipt(active.command)
    assert retirement_markers.get(active.command) == tuple(malformed)
    retirement_markers[active.command] = exact_marker
    assert build_receipt._forget_workspace_built_receipt(active.command)
    assert active.command not in retirement_markers
    assert active.command not in receipt_forget._RETIREMENT_AUTHORITIES


@pytest.mark.parametrize("tamper", ("digest", "process"))
def test_unconsumed_retirement_rejects_rebuilt_marker_after_lease_map_pops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    executor, _destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    cleanup_deadline = time.monotonic() + 1.0
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )
    assert build_receipt._BUILT_LEASES[active.built][3] is active.runtime_proof

    class _MarkerPopLoss(dict):
        raised = False

        def pop(self, key, default=None):
            if key is active.command and not self.raised:
                self.raised = True
                raise OSError("synthetic unconsumed retirement-marker pop loss")
            return super().pop(key, default)

    retirement_markers = _MarkerPopLoss()
    monkeypatch.setattr(receipt_forget, "_RETIREMENTS", retirement_markers)
    with pytest.raises(OSError):
        build_receipt._forget_workspace_built_receipt(active.command)
    assert retirement_markers.raised
    assert active.command not in build_receipt._BUILT_BY_COMMAND
    assert active.built not in build_receipt._BUILT_LEASES
    exact_marker = retirement_markers[active.command]
    assert exact_marker[1] == active.runtime_proof.output.digest
    assert all(value is not active.runtime_proof for value in exact_marker)
    assert exact_marker[3] is None
    wrong_digest = b"z" * 32 if tamper == "digest" else exact_marker[1]
    wrong_process = (
        process_state._RelayLinuxBuildProcessReceipt(
            process_state._RECEIPT_TOKEN,
            owner_token=object(),
        )
        if tamper == "process"
        else exact_marker[2]
    )
    wrong_authority = receipt_forget._WorkspaceBuiltRetirementAuthority(
        receipt_forget._RETIREMENT_TOKEN,
        command=active.command,
        receipt=active.built,
        digest=wrong_digest,
        process_receipt=wrong_process,
        consumer=None,
    )
    malformed_marker = (
        active.built,
        wrong_digest,
        wrong_process,
        None,
        wrong_authority,
    )
    retirement_markers[active.command] = malformed_marker

    assert not build_receipt._forget_workspace_built_receipt(active.command)
    assert retirement_markers.get(active.command) is malformed_marker
    retirement_markers[active.command] = exact_marker
    assert build_receipt._forget_workspace_built_receipt(active.command)
    assert active.command not in retirement_markers
    assert active.command not in receipt_forget._RETIREMENT_AUTHORITIES


@pytest.mark.parametrize("tamper", ("digest", "process"))
def test_unconsumed_retirement_rejects_rebuilt_authority_after_marker_pop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    executor, _destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    cleanup_deadline = time.monotonic() + 1.0
    assert build_receipt._revoke_workspace_built_receipt(
        active.built,
        active.owner_token,
        active.record_token,
        cleanup_deadline=cleanup_deadline,
    )

    class _MarkerPopEffectLoss(dict):
        raised = False

        def pop(self, key, default=None):
            value = super().pop(key, default)
            if key is active.command and not self.raised:
                self.raised = True
                raise OSError("synthetic post-marker-pop retirement loss")
            return value

    retirement_markers = _MarkerPopEffectLoss()
    monkeypatch.setattr(receipt_forget, "_RETIREMENTS", retirement_markers)
    with pytest.raises(OSError):
        build_receipt._forget_workspace_built_receipt(active.command)
    assert retirement_markers.raised
    assert active.command not in retirement_markers
    assert active.command not in build_receipt._BUILT_BY_COMMAND
    assert active.built not in build_receipt._BUILT_LEASES
    exact_authority = receipt_forget._RETIREMENT_AUTHORITIES[active.command]
    wrong_digest = b"z" * 32 if tamper == "digest" else exact_authority.digest
    wrong_process = (
        process_state._RelayLinuxBuildProcessReceipt(
            process_state._RECEIPT_TOKEN,
            owner_token=object(),
        )
        if tamper == "process"
        else exact_authority.process_receipt
    )
    wrong_authority = receipt_forget._WorkspaceBuiltRetirementAuthority(
        receipt_forget._RETIREMENT_TOKEN,
        command=active.command,
        receipt=active.built,
        digest=wrong_digest,
        process_receipt=wrong_process,
        consumer=None,
    )
    receipt_forget._RETIREMENT_AUTHORITIES[active.command] = wrong_authority

    assert not build_receipt._forget_workspace_built_receipt(active.command)
    assert receipt_forget._RETIREMENT_AUTHORITIES.get(active.command) is wrong_authority
    receipt_forget._RETIREMENT_AUTHORITIES[active.command] = exact_authority
    assert build_receipt._forget_workspace_built_receipt(active.command)
    assert active.command not in receipt_forget._RETIREMENT_AUTHORITIES


@pytest.mark.parametrize(
    "cut",
    (
        "marker-store",
        "evidence-by-key-pop",
        "key-by-binding-pop",
        "binding-by-built-pop",
        "tombstone-pop",
        "release-pop",
        "marker-pop",
    ),
)
@pytest.mark.parametrize(
    "error_factory",
    (
        lambda: OSError("synthetic executor build retirement loss"),
        lambda: KeyboardInterrupt("synthetic executor build retirement control"),
        lambda: SystemExit("synthetic executor build retirement exit"),
    ),
    ids=("ordinary", "keyboard", "system-exit"),
)
def test_executor_build_retirement_reconciles_every_effect_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
    error_factory,
    _synthetic_inner_settlement: None,
) -> None:
    executor, destination, bundle, construction = _bound_executor(tmp_path)
    active = _active_build(executor, bundle, construction)
    binding, evidence = _forgotten_consumed_build(executor, destination, active)
    error = error_factory()
    raised = False

    if cut in {
        "evidence-by-key-pop",
        "key-by-binding-pop",
        "binding-by-built-pop",
    }:
        original = executor_contract._pop_executor_built_evidence
        mapping, key = {
            "evidence-by-key-pop": (executor_binding._EVIDENCE_BY_KEY, evidence.key),
            "key-by-binding-pop": (executor_binding._KEYS_BY_BINDING, binding),
            "binding-by-built-pop": (executor_binding._BINDINGS_BY_BUILT, active.built),
        }[cut]

        def pop_with_loss(candidate) -> None:
            nonlocal raised
            if not raised:
                raised = True
                mapping.pop(key, None)
                raise error
            original(candidate)

        monkeypatch.setattr(
            executor_contract,
            "_pop_executor_built_evidence",
            pop_with_loss,
        )
    else:
        function_name = {
            "marker-store": "_store_executor_build_retirement",
            "tombstone-pop": "_retire_workspace_built_consumer_tombstone",
            "release-pop": "_pop_executor_build_release",
            "marker-pop": "_pop_executor_build_retirement",
        }[cut]
        original = getattr(executor_contract, function_name)

        def call_with_loss(*args: object, **kwargs: object):
            nonlocal raised
            result = original(*args, **kwargs)
            if not raised:
                raised = True
                raise error
            return result

        monkeypatch.setattr(executor_contract, function_name, call_with_loss)

    with pytest.raises(type(error)) as captured:
        executor_consume._retire_released_executor_built_state(evidence.key)
    assert captured.value is error
    assert raised
    assert executor_consume._retire_released_executor_built_state(evidence.key)
    assert executor_consume._retire_released_executor_built_state(evidence.key)
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_binding._KEYS_BY_BINDING
    assert not executor_binding._BINDINGS_BY_BUILT
    assert not executor_binding._RELEASE_BINDINGS
    assert not executor_binding._BUILD_RETIREMENTS
    assert build_consumer._workspace_built_consumer_all_state_is_empty()


def test_started_worker_keeps_consumed_output_until_outer_release_then_reuses_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _synthetic_inner_settlement: None,
) -> None:
    retained: list[object] = []
    real_scope_cleanup = fs_build_transaction._cleanup_workspace_build_scope

    for sequence in range(2):
        case_root = tmp_path / f"case-{sequence}"
        case_root.mkdir()
        executor, destination, bundle, construction = _bound_executor(case_root)
        owner = executor._workspace_owner
        events: list[str] = []
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
            _owner=owner,
            _bundle=bundle,
            _events=events,
        ):
            assert request is _owner._request
            assert prestart_authority.claim._bundle is _bundle
            assert build_values._bind_workspace_build_command_controller(
                command,
                controller=controller,
                owner_token=owner_token,
                record_token=record_token,
                build_deadline=build_deadline,
            )
            state = build_values._COMMANDS[command]
            build_values._store_command_state(
                command,
                (*state[:4], "running", state[5]),
            )
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
            _events.append("process-absent")
            return receipt

        def fake_validate_output(*, _owner=owner, _events=events, **kwargs):
            nonlocal snapshot
            assert process_facade_registry._build_process_registries_are_empty()
            workspace = _owner._request._workspace
            parent = workspace / ".next-voice-e2e"
            dist = parent / _owner._request._run_id
            if snapshot is None:
                parent.mkdir(mode=0o700)
                dist.mkdir(mode=0o700)
                (dist / "synthetic-output").write_bytes(b"validated")
                snapshot = fs_output_values._WorkspaceBuildOutputSnapshot(
                    digest=b"d" * 32,
                    dist_parent_identity=fs_contract._WorkspaceFilesystemIdentity.from_stat(
                        parent.stat(follow_symlinks=False)
                    ),
                    dist_root_identity=fs_contract._WorkspaceFilesystemIdentity.from_stat(
                        dist.stat(follow_symlinks=False)
                    ),
                    dist_nodes=(),
                    node_modules_identity=kwargs["baseline"].node_modules_identity,
                    workspace_nodes=(),
                )
            _events.append("validated")
            return snapshot

        def record_scope_cleanup(state, _events=events):
            assert process_facade_registry._build_process_registries_are_empty()
            _events.append("first-fs-delete")
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
            owner,
            bundle,
            construction,
            time.monotonic() + 2.0,
        )
        assert start is not None and coherent is True
        prepared_deadline = time.monotonic() + 2.0
        prepared = owner._receipt_destination._read(owner._request)
        while prepared is None:
            assert time.monotonic() < prepared_deadline
            time.sleep(0.005)
            prepared = owner._receipt_destination._read(owner._request)
        assert type(prepared) is fs_contract._WorkspacePreparedReceipt
        built, coherent = build_facade._build_relay_linux_workspace(
            owner,
            bundle,
            construction,
            prepared,
            build_deadline=time.monotonic() + 3.0,
        )
        assert coherent is True and built is not None
        command = build_receipt._BUILT_LEASES[built][2]
        binding = executor_consume._consume_relay_linux_executor_built_lease(
            executor=executor,
            destination=destination,
            built=built,
            operation_deadline=build_values._COMMANDS[command][3],
        )
        evidence = _evidence(binding)
        raw = worker_registry._RECORDS[bundle]._entry[1]
        assert raw.is_alive()
        assert owner._request._run_root.is_dir()

        bundle._controller._request_cancel()
        bundle._controller._wait(0.02)
        assert raw.is_alive()
        assert owner._request._run_root.is_dir()
        assert executor_state._AUTHORITY_KEYS.get(evidence.authority) is evidence.key
        assert executor_state._OWNER_KEYS.get(evidence.executor) is evidence.key
        assert executor_state._DESTINATION_KEYS.get(evidence.destination) is evidence.key
        assert next(iter(executor_state._PORT_RESERVATIONS)) is executor_state._FIXED_PORTS
        assert executor_state._PORT_RESERVATIONS.get(executor_state._FIXED_PORTS) is evidence.key
        assert executor_state._SOURCE_EVIDENCE[evidence.key][2] is WEB_ROOT
        assert executor_contract._binding_maps_match(evidence)
        assert executor_contract._cleanup_evidence_matches(evidence), (
            executor_state._EXECUTORS.get(evidence.key)[4:],
            len(executor_state._EXECUTORS),
            list(executor_state._PORT_RESERVATIONS.items()),
            executor_state._SOURCE_EVIDENCE.get(evidence.key),
            len(executor_binding._EVIDENCE_BY_KEY),
            len(executor_binding._KEYS_BY_BINDING),
            len(executor_binding._BINDINGS_BY_BUILT),
        )
        assert build_consumer._workspace_built_consumer_is_in_use(
            built,
            evidence.consumer,
        )
        assert executor_consume._release_relay_linux_executor_built_use(
            binding,
            cleanup_deadline=time.monotonic() + 2.0,
        )
        revoke_deadline = time.monotonic() + 2.0
        while not build_consumer._workspace_built_consumer_is_revoked(
            built,
            evidence.consumer,
        ):
            assert time.monotonic() < revoke_deadline
            time.sleep(0.005)
        assert owner._request._run_root.is_dir()
        assert executor_consume._acknowledge_relay_linux_executor_built_revoked(
            binding,
            cleanup_deadline=time.monotonic() + 2.0,
        )
        assert executor_consume._executor_consumed_build_allows_workspace_release(binding)
        graph = executor_workspace._intend_relay_linux_executor_workspace_release(
            executor._cleanup_authority
        )
        assert graph == (owner, bundle, construction)
        terminal, joined = worker_thread._join_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            3.0,
        )
        assert terminal is not None and joined is True
        assert worker_thread._release_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
            terminal,
        )
        assert executor_workspace._complete_relay_linux_executor_workspace_release(
            executor._cleanup_authority
        )
        assert executor_state._release_unstarted_relay_linux_executor(executor._cleanup_authority)
        assert "first-fs-delete" in events
        assert events.index("process-absent") < events.index("first-fs-delete")
        assert not owner._request._run_root.exists()
        retained.extend((executor, destination, bundle, construction, built, binding))

        assert not worker_registry._RECORDS
        assert not worker_consumer._CONSUMERS
        assert not build_values._COMMANDS
        assert not build_values._COMMAND_GATES
        assert not build_values._PROCESS_ASSOCIATIONS
        assert not build_receipt._BUILT_LEASES
        assert not build_receipt._BUILT_BY_COMMAND
        assert not receipt_forget._RETIREMENTS
        assert not receipt_forget._RETIREMENT_AUTHORITIES
        assert build_consumer._workspace_built_consumer_all_state_is_empty()
        assert not executor_binding._EVIDENCE_BY_KEY
        assert not executor_binding._KEYS_BY_BINDING
        assert not executor_binding._BINDINGS_BY_BUILT
        assert not executor_binding._RELEASE_BINDINGS
        assert not executor_binding._BUILD_RETIREMENTS
        assert not executor_state._EXECUTORS
        assert not executor_state._PORT_RESERVATIONS

    assert retained

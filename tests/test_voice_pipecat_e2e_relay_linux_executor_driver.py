"""Private synthetic vertical for the full relay Linux executor driver."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import time
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
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_forget as build_forget
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_forget as receipt_forget
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values as build_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer as worker_consumer
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_transaction as fs_build_transaction
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values as fs_output_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as worker_registry
import scripts.voice_pipecat_e2e_relay_linux_executor as executor_facade
import scripts.voice_pipecat_e2e_relay_linux_executor_build_binding as executor_binding
import scripts.voice_pipecat_e2e_relay_linux_executor_build_contract as executor_contract
import scripts.voice_pipecat_e2e_relay_linux_executor_cleanup as executor_cleanup
import scripts.voice_pipecat_e2e_relay_linux_executor_driver as driver_module
import scripts.voice_pipecat_e2e_relay_linux_executor_driver_state as driver_state
import scripts.voice_pipecat_e2e_relay_linux_executor_inner_state as executor_inner_state
import scripts.voice_pipecat_e2e_relay_linux_executor_state as executor_state
import scripts.voice_pipecat_e2e_relay_owner_cleanup as relay_cleanup
import scripts.voice_pipecat_e2e_relay_owner_forward as relay_forward
import scripts.voice_pipecat_e2e_relay_owner_state as relay_owner_state
import scripts.voice_pipecat_e2e_relay_probe as relay_probe
from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_docker import CoturnImageReceipt
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ContainerPlan,
    ValidatedContainer,
    ValidatedRunningContainer,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkCleanupAuthority,
    ValidatedNetwork,
)
from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_host import TrustedHostTools
from scripts.voice_pipecat_e2e_coturn_runtime import (
    ContainerAbsenceReceipt,
    DockerPrerequisites,
    NetworkAbsenceReceipt,
    OwnedContainer,
    OwnedNetwork,
)
from scripts.voice_pipecat_e2e_coturn_runtime_lifecycle import StoppedCoturnReceipt
from scripts.voice_pipecat_e2e_coturn_runtime_process import CleanCoturnExitReceipt
from scripts.voice_pipecat_e2e_coturn_runtime_tls import RuntimeTlsMaterial
from scripts.voice_pipecat_e2e_coturn_tls_readiness import OpenSslReadinessReceipt
from scripts.voice_pipecat_e2e_relay_browser_result import (
    RelayBrowserObservation,
    RelayBrowserResultOwner,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationOwner,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import (
    RelayPrebootstrapReceipt,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayPlaywrightExitReceipt
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT

_SECRET = "synthetic-static-auth-secret-0123456789"
_USERNAME = "1786982460:123e4567-e89b-42d3-a456-426614174000"
_NETWORK_ID = "1" * 64
_CONTAINER_ID = "2" * 64
_TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.28.44.0/29",
    gateway="172.28.44.1",
    container="172.28.44.2",
)


def _driver_state_mappings() -> tuple[object, ...]:
    return (
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
        build_forget._FORGOTTEN_RECORDS,
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
        driver_state._DRIVER_RECORDS,
        driver_state._DRIVER_TERMINALS,
        executor_inner_state._INNER_RECORDS,
        executor_inner_state._INNER_RESULTS,
        executor_inner_state._INNER_TERMINALS,
        executor_inner_state._INNER_AUTHORITIES,
        relay_owner_state._REGISTRY,
    )


@pytest.fixture(autouse=True)
def _isolated_driver_state() -> None:
    mappings = _driver_state_mappings()
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


class _Runner:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def run(self, _request: object) -> object:
        raise AssertionError("synthetic driver test executes no command")

    def start_attached(self, _request: object) -> object:
        raise AssertionError("synthetic driver test starts no command")

    def settle_owned(self) -> bool:
        self.events.append("settle-runner")
        return True


class _BridgeProbe:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def ipv4_routes(self) -> tuple[object, ...]:
        self.events.append("routes")
        return ()

    def interface_ipv4(self, _interface: str) -> object:
        raise AssertionError("synthetic driver test creates no bridge")


class _AdoptionDestination:
    def __init__(self) -> None:
        self.published = False

    def publish(self, value: object) -> None:
        assert value is True
        self.published = True


def _object(kind: type[object], **values: object) -> object:
    result = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _source() -> RelayProbeSource:
    return RelayProbeSource(relay_probe._SOURCE_TOKEN, commit_sha="a" * 40)


def _tools() -> TrustedHostTools:
    return _object(
        TrustedHostTools,
        _docker=Path("/usr/bin/docker"),
        _openssl=Path("/usr/bin/openssl"),
        _docker_socket=Path("/run/docker.sock"),
    )  # type: ignore[return-value]


def _driver_graph(tmp_path: Path, *, sequence: int = 0):
    run_parent = tmp_path / f"runs-{sequence}"
    run_parent.mkdir(mode=0o700)
    node = tmp_path / f"node-{sequence}"
    node.write_bytes(b"synthetic-node\n")
    node.chmod(0o700)
    destination = executor_state._new_relay_linux_executor_destination(
        source_root=WEB_ROOT,
        run_parent=run_parent.resolve(),
        node=node.resolve(),
        run_id=f"executor-driver-{sequence}",
        source=_source(),
    )
    executor = destination._read()
    key = executor_state._canonical_executor_key(executor, destination)
    assert type(key) is executor_state._RelayLinuxExecutorKey
    return executor, destination, key


def _driver_arguments(
    executor: object, destination: object, events: list[str]
) -> dict[str, object]:
    return {
        "executor": executor,
        "destination": destination,
        "runner": _Runner(events),
        "bridge_probe": _BridgeProbe(events),
        "tools": _tools(),
        "invocation_driver": _object(RelayInvocationDriver),
        "static_auth_secret": _SECRET,
        "now": datetime(2026, 8, 23),
        "start_timeout_seconds": 2.0,
        "build_timeout_seconds": 3.0,
        "browser_timeout_seconds": 5.0,
        "runtime_timeout_seconds": 5.0,
        "cleanup_timeout_seconds": 15.0,
        "clock": time.monotonic,
        "wait": time.sleep,
        "epoch_clock": time.time,
    }


def _install_synthetic_build(
    monkeypatch: pytest.MonkeyPatch,
    executor: object,
    events: list[str],
) -> None:
    workspace_owner = executor._workspace_owner
    snapshot: fs_output_values._WorkspaceBuildOutputSnapshot | None = None
    real_scope_cleanup = fs_build_transaction._cleanup_workspace_build_scope

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
        assert prestart_authority.claim._request is request
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


def _summary() -> CoturnProbeSummary:
    return _object(
        CoturnProbeSummary,
        _grammar_verified=False,
        _allocation_count=1,
        _observed_categories=frozenset(),
        _unknown_info_records=0,
        _grammar_violation_records=0,
        _total_records=1,
    )  # type: ignore[return-value]


def _browser_observation() -> RelayBrowserObservation:
    return _object(
        RelayBrowserObservation,
        result_schema_attested=True,
        hidden_call_attested=True,
        relay_candidate_attested=True,
        browser_cleanup_attested=True,
        terminal_cleanup_attested=True,
        safe_report_attested=True,
        artifacts_deleted=True,
        qualification_verified=False,
    )  # type: ignore[return-value]


def _absence(kind: type[object], plan: object) -> object:
    values = (
        {"_container_id": _CONTAINER_ID, "_plan": plan}
        if kind is ContainerAbsenceReceipt
        else {"_network_id": _NETWORK_ID, "_plan": plan}
    )
    return _object(kind, **values, _finalized=False, _lock=threading.Lock())


def _install_synthetic_relay(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    image = _object(
        CoturnImageReceipt,
        _image_id="sha256:" + "1" * 64,
        _environment=("PATH=/usr/bin",),
        _labels=(),
        _working_directory="",
    )
    prerequisites = _object(
        DockerPrerequisites,
        _image=image,
        _network_inventory=object(),
    )
    invocation = _object(RelayInvocationOwner)
    prebootstrap = _object(RelayPrebootstrapReceipt, _owner_token=object())
    playwright_exit = _object(RelayPlaywrightExitReceipt, _owner_token=object())
    artifact_owner = _object(RelayBrowserResultOwner)
    readiness = _object(
        OpenSslReadinessReceipt,
        _protocol="TLSv1.3",
        _cipher_suite="TLS_AES_256_GCM_SHA384",
    )
    stopped_plan: list[ContainerPlan] = []

    def prepare(**_kwargs: object) -> object:
        events.append("prepare")
        return prerequisites

    def generate(**kwargs: object) -> None:
        assert kwargs["static_auth_secret"] == _SECRET
        material = kwargs["material"]
        object.__setattr__(material, "_state", "generated")
        events.append("generate-tls")

    def create_network(**kwargs: object) -> OwnedNetwork:
        plan = kwargs["plan"]
        authority = _object(
            NetworkCleanupAuthority,
            _network_id=_NETWORK_ID,
            _plan=plan,
        )
        validated = _object(ValidatedNetwork, _authority=authority)
        events.append("create-network")
        return OwnedNetwork(authority=authority, validated=validated)  # type: ignore[arg-type]

    def create_container(**kwargs: object) -> OwnedContainer:
        plan = kwargs["plan"]
        assert type(plan) is ContainerPlan
        stopped_plan.append(plan)
        authority = _object(
            ContainerCleanupAuthority,
            _container_id=_CONTAINER_ID,
            _plan=plan,
        )
        validated = _object(ValidatedContainer, _authority=authority)
        events.append("create-container")
        return OwnedContainer(authority=authority, validated=validated)  # type: ignore[arg-type]

    def bind(material: RuntimeTlsMaterial, authority: object) -> None:
        object.__setattr__(material, "_container_id", authority.container_id)
        object.__setattr__(material, "_state", "bound")
        events.append("bind-tls")

    def adopt(_invocation: object, sink: object) -> None:
        destination = _AdoptionDestination()
        sink._accept_relay_turn_username(_USERNAME, destination)
        assert destination.published
        events.append("adopt-username")

    def validate_running(**kwargs: object) -> object:
        events.append("validate-running")
        return _object(ValidatedRunningContainer, _authority=kwargs["authority"])

    def cleanup_tls(material: RuntimeTlsMaterial, **_kwargs: object) -> None:
        object.__setattr__(material, "_state", "cleaned")
        events.append("cleanup-tls")

    monkeypatch.setattr(relay_forward, "prepare_docker_prerequisites", prepare)
    monkeypatch.setattr(relay_forward, "select_bridge_topology", lambda **_kwargs: _TOPOLOGY)
    monkeypatch.setattr(relay_forward, "generate_runtime_tls_material", generate)
    monkeypatch.setattr(
        relay_forward,
        "authorize_relay_backend",
        lambda *_args, **_kwargs: events.append("authorize-backend"),
    )
    monkeypatch.setattr(
        relay_forward,
        "_new_relay_invocation_owner",
        lambda *_args, **_kwargs: (events.append("new-invocation"), invocation)[1],
    )
    monkeypatch.setattr(
        relay_forward,
        "stage_relay_backend",
        lambda _owner: (events.append("stage-backend"), prebootstrap)[1],
    )
    monkeypatch.setattr(relay_forward, "create_owned_network", create_network)
    monkeypatch.setattr(relay_forward, "create_owned_container", create_container)
    monkeypatch.setattr(relay_forward, "bind_runtime_tls_material_to_container", bind)
    monkeypatch.setattr(relay_forward, "_adopt_expected_turn_username", adopt)
    monkeypatch.setattr(
        relay_forward,
        "start_owned_container_attached",
        lambda **_kwargs: events.append("start-coturn"),
    )
    monkeypatch.setattr(
        relay_forward,
        "start_attached_coturn_evidence_drain",
        lambda _drain: events.append("start-drain"),
    )
    monkeypatch.setattr(relay_forward, "validate_owned_container_running", validate_running)
    monkeypatch.setattr(
        relay_forward,
        "execute_openssl_readiness",
        lambda **_kwargs: (events.append("openssl-ready"), readiness)[1],
    )
    monkeypatch.setattr(
        relay_forward,
        "authorize_relay_browser",
        lambda *_args, **_kwargs: events.append("authorize-browser"),
    )
    monkeypatch.setattr(
        relay_forward,
        "new_relay_browser_result_owner",
        lambda _run: (events.append("prepare-artifacts"), artifact_owner)[1],
    )
    monkeypatch.setattr(
        relay_forward,
        "stage_relay_web",
        lambda _owner: events.append("stage-web"),
    )
    monkeypatch.setattr(
        relay_forward,
        "start_relay_playwright",
        lambda _owner: events.append("start-browser"),
    )
    monkeypatch.setattr(
        relay_forward,
        "finish_relay_playwright",
        lambda *_args, **_kwargs: (events.append("finish-browser"), playwright_exit)[1],
    )
    monkeypatch.setattr(
        relay_forward,
        "consume_relay_browser_result",
        lambda *_args: (events.append("consume-artifacts"), _browser_observation())[1],
    )
    monkeypatch.setattr(
        relay_cleanup,
        "cleanup_relay_invocation",
        lambda _owner: events.append("cleanup-invocation"),
    )
    monkeypatch.setattr(relay_cleanup, "_recover_canonical_pump", lambda _process, value: value)
    monkeypatch.setattr(
        relay_cleanup,
        "_recover_canonical_drain",
        lambda _process, _pump, value, **_kwargs: value,
    )
    monkeypatch.setattr(
        relay_cleanup,
        "stop_owned_container",
        lambda **_kwargs: (events.append("stop-container"), _object(StoppedCoturnReceipt))[1],
    )
    monkeypatch.setattr(
        relay_cleanup,
        "finish_attached_coturn_evidence_drain",
        lambda _drain: (events.append("finish-drain"), _summary())[1],
    )
    monkeypatch.setattr(
        relay_cleanup,
        "confirm_attached_coturn_clean_exit",
        lambda _process: (events.append("clean-exit"), _object(CleanCoturnExitReceipt))[1],
    )

    def remove_container(**kwargs: object) -> object:
        assert kwargs["stopped"] is not None
        events.append("remove-container")
        return _absence(ContainerAbsenceReceipt, stopped_plan[0])

    monkeypatch.setattr(relay_cleanup, "remove_stopped_owned_container", remove_container)
    monkeypatch.setattr(relay_cleanup, "cleanup_runtime_tls_material", cleanup_tls)

    def finalize_container(receipt: object) -> None:
        object.__setattr__(receipt, "_finalized", True)
        events.append("finalize-container")

    monkeypatch.setattr(relay_cleanup, "finalize_container_absence", finalize_container)

    def remove_network(**kwargs: object) -> object:
        events.append("remove-network")
        return _absence(NetworkAbsenceReceipt, kwargs["authority"].plan)

    monkeypatch.setattr(relay_cleanup, "cleanup_owned_network", remove_network)

    def finalize_network(receipt: object) -> None:
        object.__setattr__(receipt, "_finalized", True)
        events.append("finalize-network")

    monkeypatch.setattr(relay_cleanup, "finalize_network_absence", finalize_network)
    monkeypatch.setattr(
        relay_owner_state,
        "revalidate_relay_probe_source",
        lambda _run: events.append("revalidate-source"),
    )


def _assert_total_absence(executor: object, destination: object, key: object) -> None:
    assert executor_cleanup._final_outer_absence(executor, destination, key)
    assert driver_state._driver_state_is_empty()
    assert driver_state._driver_terminal_state_is_capacity_neutral()
    assert executor._relay_owner_destination._record is None
    assert not relay_owner_state._REGISTRY
    assert not executor._workspace_owner._request._run_root.exists()
    assert not executor_inner_state._INNER_RECORDS
    assert not worker_registry._RECORDS
    assert not worker_consumer._CONSUMERS
    assert not build_values._COMMANDS
    assert not build_values._PROCESS_ASSOCIATIONS
    assert not build_receipt._BUILT_LEASES
    assert build_consumer._workspace_built_consumer_all_state_is_empty()
    assert not executor_binding._EVIDENCE_BY_KEY
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS


def test_driver_runs_full_vertical_then_returns_only_after_total_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = _driver_graph(tmp_path)
    assert not executor_state._EXECUTORS
    assert not executor_state._PORT_RESERVATIONS
    assert not worker_registry._RECORDS
    _install_synthetic_build(monkeypatch, executor, events)
    _install_synthetic_relay(monkeypatch, events)

    arguments = _driver_arguments(executor, destination, events)
    observation = driver_module._run_preowned_relay_linux_executor(**arguments)

    assert type(observation) is RelayProbeObservation
    assert observation.cleanup_complete is True
    assert observation.qualification_verified is False
    assert events.index("process-absent") < events.index("prepare")
    assert events.index("settle-runner") < events.index("revalidate-source")
    assert events.index("revalidate-source") < events.index("first-fs-delete")
    _assert_total_absence(executor, destination, key)
    terminal_events = tuple(events)
    assert driver_module._run_preowned_relay_linux_executor(**arguments) is observation
    assert tuple(events) == terminal_events


def test_driver_cleans_every_effect_when_consume_fails_before_linearization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = _driver_graph(tmp_path)
    _install_synthetic_build(monkeypatch, executor, events)

    def fail_consume(**_kwargs: object) -> object:
        events.append("consume-abort")
        raise RuntimeError("synthetic preconsume abort")

    monkeypatch.setattr(
        driver_module,
        "_consume_relay_linux_executor_built_lease",
        fail_consume,
    )
    with pytest.raises(RuntimeError):
        driver_module._run_preowned_relay_linux_executor(
            **_driver_arguments(executor, destination, events)
        )

    assert events.count("consume-abort") == 1
    _assert_total_absence(executor, destination, key)


def test_driver_cleans_consumed_build_when_inner_intent_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor, destination, key = _driver_graph(tmp_path)
    _install_synthetic_build(monkeypatch, executor, events)

    def abort_before_inner(**kwargs: object) -> object:
        evidence = executor_contract._evidence_for_binding(kwargs["binding"])
        assert evidence is not None
        assert executor_contract._consumed_binding_matches(evidence)
        events.append("consumed-before-inner-abort")
        raise RuntimeError("synthetic consumed-before-inner abort")

    monkeypatch.setattr(
        executor_facade,
        "_resolve_or_intend_inner_evidence",
        abort_before_inner,
    )
    with pytest.raises(RuntimeError):
        driver_module._run_preowned_relay_linux_executor(
            **_driver_arguments(executor, destination, events)
        )

    assert events.count("consumed-before-inner-abort") == 1
    _assert_total_absence(executor, destination, key)

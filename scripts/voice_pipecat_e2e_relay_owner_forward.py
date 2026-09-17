"""Forward-only composition for one exact relay B0 aggregate."""

from __future__ import annotations

import os
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerPlan,
    ValidatedRunningContainer,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkPlan,
    select_bridge_topology,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (
    AttachedCoturnEvidencePump,
    AttachedCoturnProcess,
    DockerPrerequisites,
    OwnedContainer,
    OwnedNetwork,
    RuntimeReadinessBudget,
    RuntimeTlsMaterial,
    bind_runtime_tls_material_to_container,
    create_owned_container,
    create_owned_network,
    execute_openssl_readiness,
    generate_runtime_tls_material,
    new_attached_coturn_process,
    new_runtime_tls_material,
    prepare_docker_prerequisites,
    start_owned_container_attached,
    validate_owned_container_running,
)
from scripts.voice_pipecat_e2e_coturn_runtime_drain import (
    AttachedCoturnEvidenceDrain,
    start_attached_coturn_evidence_drain,
)
from scripts.voice_pipecat_e2e_coturn_tls_readiness import OpenSslReadinessReceipt
from scripts.voice_pipecat_e2e_relay_browser_result import (
    RelayBrowserObservation,
    RelayBrowserResultOwner,
    consume_relay_browser_result,
    new_relay_browser_result_owner,
)
from scripts.voice_pipecat_e2e_relay_invocation_lifecycle import (
    RelayInvocationOwner,
    _adopt_expected_turn_username,
    _new_relay_invocation_owner,
    finish_relay_playwright,
    stage_relay_backend,
    stage_relay_web,
    start_relay_playwright,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import RelayPrebootstrapReceipt
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayPlaywrightExitReceipt
from scripts.voice_pipecat_e2e_relay_owner_authority import (
    _retain_attached_cleanup_authority,
    _retain_runtime_persistence_authority,
    _retain_tls_generation_authority,
)
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwner,
)
from scripts.voice_pipecat_e2e_relay_owner_username import _new_username_sink
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeOwnerError
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeRun,
    authorize_relay_backend,
    authorize_relay_browser,
)


def _run_forward(
    owner: RelayProbeOwner,
    *,
    static_auth_secret: object,
    now: datetime,
    browser_timeout_seconds: float,
) -> None:
    """Run every forward edge once; callers own failure-to-cleanup transition."""

    with owner._operation_lock:
        with owner._lock:
            if owner._state == "observed":
                return
            if owner._state != "created" or owner._cleanup_only:
                raise RelayProbeOwnerError("Relay probe execution failed")
            owner._state = "forward"
        _prepare_runtime(owner)
        _generate_and_authorize_backend(
            owner,
            static_auth_secret=static_auth_secret,
            now=now,
        )
        static_auth_secret = now = None
        invocation = _start_backend_and_prebootstrap(owner)
        _create_runtime_owners(owner)
        _adopt_username_and_start_coturn(owner, invocation)
        _authorize_ready_runtime(owner)
        _run_browser_and_consume(owner, invocation, browser_timeout_seconds)
        with owner._lock:
            owner._forward_phase = "artifacts-consumed"
            owner._cleanup_only = True
            owner._state = "cleanup-only"


def _prepare_runtime(owner: RelayProbeOwner) -> None:
    runner = owner._runner
    bridge_probe = owner._bridge_probe
    tools = owner._tools
    paths = owner._paths
    identity = owner._identity
    if runner is None or bridge_probe is None or tools is None or paths is None or identity is None:
        raise RelayProbeOwnerError("Relay probe execution failed")
    prerequisites = prepare_docker_prerequisites(
        runner=runner,
        tools=tools,
        paths=paths,
        absolute_deadline=owner._absolute_deadline,
        clock=owner._clock,  # type: ignore[arg-type]
    )
    prerequisites = owner._publish(
        "prerequisites",
        prerequisites,
        DockerPrerequisites,
    )
    occupied_routes = bridge_probe.ipv4_routes()
    topology = select_bridge_topology(
        owner_nonce=identity.owner_nonce,
        occupied_routes=occupied_routes,
        completed_inventory=prerequisites.network_inventory,
    )
    plan = NetworkPlan(identity=identity, paths=paths, topology=topology)
    owner._publish("network_plan", plan, NetworkPlan)
    material = new_runtime_tls_material(paths=paths, topology=topology)
    owner._publish("tls_material", material, RuntimeTlsMaterial)
    occupied_routes = topology = None
    with owner._lock:
        owner._forward_phase = "runtime-preowned"


def _generate_and_authorize_backend(
    owner: RelayProbeOwner,
    *,
    static_auth_secret: object,
    now: datetime,
) -> None:
    run = owner._run
    runner = owner._runner
    tools = owner._tools
    paths = owner._paths
    plan = owner._read("network_plan", NetworkPlan)
    material = owner._read("tls_material", RuntimeTlsMaterial)
    if (
        type(run) is not RelayProbeRun
        or runner is None
        or tools is None
        or paths is None
        or type(plan) is not NetworkPlan
        or type(material) is not RuntimeTlsMaterial
        or type(now) is not datetime
    ):
        raise RelayProbeOwnerError("Relay probe execution failed")
    try:
        generate_runtime_tls_material(
            material=material,
            runner=runner,
            tools=tools,
            paths=paths,
            topology=plan.topology,
            static_auth_secret=static_auth_secret,
            now=now,
        )
    except BaseException as error:
        _retain_tls_generation_authority(owner, error, material=material)
        raise
    static_auth_secret = now = None
    authorize_relay_backend(run, tls_material=material)
    with owner._lock:
        owner._forward_phase = "backend-authorized"


def _start_backend_and_prebootstrap(owner: RelayProbeOwner) -> RelayInvocationOwner:
    run = owner._run
    driver = owner._invocation_driver
    tools = owner._invocation_tools
    destination = owner._invocation_destination
    if type(run) is not RelayProbeRun or driver is None or tools is None or destination is None:
        raise RelayProbeOwnerError("Relay probe execution failed")
    invocation = _new_relay_invocation_owner(
        run,
        driver=driver,
        tools=tools,
        destination=destination,  # type: ignore[arg-type]
    )
    invocation = owner._publish("invocation", invocation, RelayInvocationOwner)
    receipt = stage_relay_backend(invocation)
    if (
        type(receipt) is not RelayPrebootstrapReceipt
        or not receipt.prepared
        or not receipt.reservation_bound
        or bool(receipt)
    ):
        raise RelayProbeOwnerError("Relay probe execution failed")
    with owner._lock:
        owner._forward_phase = "prebootstrap-complete"
    return invocation


def _create_runtime_owners(owner: RelayProbeOwner) -> None:
    runner = owner._runner
    bridge_probe = owner._bridge_probe
    tools = owner._tools
    plan = owner._read("network_plan", NetworkPlan)
    prerequisites = owner._read("prerequisites", DockerPrerequisites)
    material = owner._read("tls_material", RuntimeTlsMaterial)
    if (
        runner is None
        or bridge_probe is None
        or tools is None
        or type(plan) is not NetworkPlan
        or type(prerequisites) is not DockerPrerequisites
        or type(material) is not RuntimeTlsMaterial
    ):
        raise RelayProbeOwnerError("Relay probe execution failed")
    try:
        network = create_owned_network(
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            plan=plan,
        )
    except BaseException as error:
        _retain_runtime_persistence_authority(owner, error)
        raise
    network = owner._publish("network", network, OwnedNetwork)
    container_plan = ContainerPlan(
        identity=plan.identity,
        paths=plan.paths,
        network=network.validated,
        image=prerequisites.image,
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    owner._publish("container_plan", container_plan, ContainerPlan)
    try:
        container = create_owned_container(
            runner=runner,
            tools=tools,
            plan=container_plan,
        )
    except BaseException as error:
        _retain_runtime_persistence_authority(owner, error)
        raise
    container = owner._publish("container", container, OwnedContainer)
    bind_runtime_tls_material_to_container(material, container.authority)
    process = new_attached_coturn_process(container.validated)
    owner._publish("process", process, AttachedCoturnProcess)
    with owner._lock:
        owner._forward_phase = "container-preowned"


def _adopt_username_and_start_coturn(
    owner: RelayProbeOwner,
    invocation: RelayInvocationOwner,
) -> None:
    sink = _new_username_sink(owner)
    try:
        _adopt_expected_turn_username(invocation, sink)
    finally:
        sink._clear()
    process = owner._read("process", AttachedCoturnProcess)
    pump = owner._read("pump", AttachedCoturnEvidencePump)
    drain = owner._read("drain", AttachedCoturnEvidenceDrain)
    container = owner._read("container", OwnedContainer)
    runner = owner._runner
    tools = owner._tools
    if (
        type(process) is not AttachedCoturnProcess
        or type(pump) is not AttachedCoturnEvidencePump
        or type(drain) is not AttachedCoturnEvidenceDrain
        or type(container) is not OwnedContainer
        or runner is None
        or tools is None
    ):
        raise RelayProbeOwnerError("Relay probe execution failed")
    try:
        start_owned_container_attached(
            runner=runner,
            tools=tools,
            container=container.validated,
            process=process,
        )
    except BaseException as error:
        _retain_attached_cleanup_authority(
            owner,
            error,
            process=process,
            runner=runner,
        )
        raise
    start_attached_coturn_evidence_drain(drain)
    with owner._lock:
        owner._forward_phase = "coturn-draining"


def _authorize_ready_runtime(owner: RelayProbeOwner) -> None:
    run = owner._run
    runner = owner._runner
    tools = owner._tools
    container = owner._read("container", OwnedContainer)
    material = owner._read("tls_material", RuntimeTlsMaterial)
    budget = owner._read("readiness_budget", RuntimeReadinessBudget)
    if (
        type(run) is not RelayProbeRun
        or runner is None
        or tools is None
        or type(container) is not OwnedContainer
        or type(material) is not RuntimeTlsMaterial
        or type(budget) is not RuntimeReadinessBudget
    ):
        raise RelayProbeOwnerError("Relay probe execution failed")
    running = validate_owned_container_running(
        runner=runner,
        tools=tools,
        authority=container.authority,
        readiness_budget=budget,
    )
    running = owner._publish("running", running, ValidatedRunningContainer)
    readiness = execute_openssl_readiness(
        runner=runner,
        tools=tools,
        running=running,
        tls_material=material,
        readiness_budget=budget,
    )
    readiness = owner._publish("readiness", readiness, OpenSslReadinessReceipt)
    authorize_relay_browser(
        run,
        running=running,
        tls_material=material,
        readiness=readiness,
    )
    with owner._lock:
        owner._forward_phase = "browser-authorized"


def _run_browser_and_consume(
    owner: RelayProbeOwner,
    invocation: RelayInvocationOwner,
    browser_timeout_seconds: float,
) -> None:
    run = owner._run
    if type(run) is not RelayProbeRun:
        raise RelayProbeOwnerError("Relay probe execution failed")
    artifact = new_relay_browser_result_owner(run)
    artifact = owner._publish("artifact_owner", artifact, RelayBrowserResultOwner)
    stage_relay_web(invocation)
    start_relay_playwright(invocation)
    receipt = finish_relay_playwright(
        invocation,
        timeout_seconds=browser_timeout_seconds,
    )
    receipt = owner._publish("playwright_exit", receipt, RelayPlaywrightExitReceipt)
    if not receipt.exited_successfully or bool(receipt):
        raise RelayProbeOwnerError("Relay probe execution failed")
    observed = consume_relay_browser_result(run, artifact)
    observed = owner._publish(
        "browser_observation",
        observed,
        RelayBrowserObservation,
    )
    if bool(observed) or not observed.artifacts_deleted:
        raise RelayProbeOwnerError("Relay probe execution failed")


__all__: list[str] = []

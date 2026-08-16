"""Synthetic preowned runtime TLS aggregate tests; no OpenSSL is executed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_runtime_tls as tls_runtime  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    ContainerPlan,
    establish_container_cleanup_authority,
    validate_container_running,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    NetworkPlan,
    establish_network_cleanup_authority,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    CoturnRuntimeError,
    RuntimeTlsMaterial,
    bind_runtime_tls_material_to_container,
    cleanup_unpublished_runtime_tls_material,
    create_runtime_readiness_budget,
    execute_openssl_readiness,
    generate_runtime_tls_material,
    new_runtime_tls_material,
)
from scripts.voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    cleanup_tls_material_generation_slot,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    NETWORK_ID,
    network_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_host import (  # noqa: E402
    QueueRunner,
    _paths,
    _result,
    _tools,
)
from tests.test_voice_pipecat_e2e_coturn_runtime import _container_plan  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    _interrupt_on_return,
)
from tests.test_voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CERTIFICATE,
    NOW,
    PRIVATE_KEY,
    SECRET,
    TOPOLOGY,
    _tls_results,
)


def _runner() -> QueueRunner:
    return QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()])


def _generate(material: RuntimeTlsMaterial, paths: object) -> None:
    generate_runtime_tls_material(
        material=material,
        runner=_runner(),
        tools=_tools(),
        paths=paths,  # type: ignore[arg-type]
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )


def _authority(paths: object, *, topology: CoturnBridgeTopology = TOPOLOGY):
    base = _container_plan(paths)  # type: ignore[arg-type]
    network_plan = NetworkPlan(
        identity=base.identity,
        paths=base.paths,
        topology=topology,
    )
    network_data = network_inspection(network_plan)
    network_data[0]["IPAM"] = {
        "Driver": "default",
        "Options": {},
        "Config": [{"Subnet": str(topology.network), "Gateway": str(topology.gateway)}],
    }
    network_authority = establish_network_cleanup_authority(
        plan=network_plan,
        network_id=NETWORK_ID,
        inspection=network_data,
    )
    network = validate_network_for_container(network_authority, network_data)
    plan = ContainerPlan(
        identity=base.identity,
        paths=base.paths,
        network=network,
        image=base.image,
        uid=base.uid,
        gid=base.gid,
    )
    inspection = _container_inspection(plan)
    return establish_container_cleanup_authority(
        plan=plan,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )


def _container_inspection(plan: ContainerPlan, *, running: bool = False):
    inspection = container_inspection(plan, running=running)
    settings = inspection[0]["NetworkSettings"]
    endpoint = settings["Networks"][plan.identity.network_name]
    topology = plan.network.authority.plan.topology
    endpoint["Gateway"] = str(topology.gateway)
    endpoint["IPAddress"] = str(topology.container)
    return inspection


def test_generation_populates_the_preowned_aggregate_and_returns_no_authority(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    result = _generate(material, paths)
    assert result is None
    assert len(material.certificate_sha256) == 64
    assert material._slot.has_material
    assert repr(material) == "RuntimeTlsMaterial()"
    assert SECRET not in repr(material)
    assert os.fspath(paths.contract.run_dir) not in repr(material)
    cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_inner_generation_return_control_retains_only_runtime_cleanup_authority(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    raw = "untrusted-return-publication-cut"
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=tls_runtime.generate_tls_and_config_material_into_slot.__code__,
            operation=lambda: _generate(material, paths),
        )
    assert str(error.value) == ""
    assert error.value.cleanup_authority is material  # type: ignore[attr-defined]
    assert material._slot.has_material
    assert not traceback_contains(error.value, raw, SECRET, PRIVATE_KEY)
    cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_generation_begin_return_cut_uses_actual_preowned_state_not_local_flag(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=RuntimeTlsMaterial._begin_generation.__code__,
            operation=lambda: _generate(material, paths),
        )
    assert str(error.value) == ""
    assert error.value.cleanup_authority is material  # type: ignore[attr-defined]
    assert not material._slot.has_material
    cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_outer_generation_return_control_leaves_caller_owned_cleanup_retry(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=generate_runtime_tls_material.__code__,
            operation=lambda: _generate(material, paths),
        )
    assert str(error.value) == "untrusted-return-publication-cut"
    assert material._slot.has_material
    cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_cross_root_binding_is_refused_before_container_commit(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _paths(first_root)
    second = _paths(second_root)
    material = new_runtime_tls_material(paths=first, topology=TOPOLOGY)
    _generate(material, first)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Runtime TLS container binding is invalid$",
    ) as error:
        bind_runtime_tls_material_to_container(material, _authority(second))
    assert not traceback_contains(
        error.value,
        os.fspath(first.contract.run_dir),
        os.fspath(second.contract.run_dir),
    )
    cleanup_unpublished_runtime_tls_material(material)


def test_cross_topology_binding_is_refused_before_container_commit(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    alternate = CoturnBridgeTopology.parse(
        network="172.29.0.0/29",
        gateway="172.29.0.1",
        container="172.29.0.2",
    )
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    _generate(material, paths)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Runtime TLS container binding is invalid$",
    ):
        bind_runtime_tls_material_to_container(
            material,
            _authority(paths, topology=alternate),
        )
    cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_cross_topology_running_proof_cannot_authorize_readiness(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    alternate = CoturnBridgeTopology.parse(
        network="172.29.0.0/29",
        gateway="172.29.0.1",
        container="172.29.0.2",
    )
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    _generate(material, paths)
    bind_runtime_tls_material_to_container(material, _authority(paths))
    alternate_authority = _authority(paths, topology=alternate)
    running = validate_container_running(
        alternate_authority,
        _container_inspection(alternate_authority.plan, running=True),
    )
    budget = create_runtime_readiness_budget(
        absolute_deadline=110.0,
        clock=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    budget._container_ready(alternate_authority, running)
    runner = QueueRunner([])
    try:
        with pytest.raises(
            CoturnRuntimeError,
            match=r"^Coturn OpenSSL readiness failed$",
        ):
            execute_openssl_readiness(
                runner=runner,
                tools=_tools(),
                running=running,
                tls_material=material,
                readiness_budget=budget,
            )
        assert runner.requests == []
    finally:
        cleanup_tls_material_generation_slot(material._slot)


def test_bound_material_cannot_use_precontainer_cleanup_escape(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    _generate(material, paths)
    bind_runtime_tls_material_to_container(material, _authority(paths))
    try:
        with pytest.raises(
            CoturnRuntimeError,
            match=r"^Runtime TLS unpublished cleanup is invalid$",
        ):
            cleanup_unpublished_runtime_tls_material(material)
        assert material._slot.has_material
    finally:
        cleanup_tls_material_generation_slot(material._slot)

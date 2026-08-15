"""Synthetic Docker bridge ownership tests; no network or Docker is used."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    CoturnDockerNetworkError,
    NetworkCleanupAuthority,
    NetworkPlan,
    build_network_absence_request,
    build_network_create_request,
    build_network_inspect_request,
    build_network_remove_request,
    establish_network_cleanup_authority,
    select_bridge_topology,
    validate_bridge_route_transition,
    validate_network_cleanup_target,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    CoturnRuntimePaths,
    HostIPv4Route,
    RuntimeIdentity,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402

NONCE = "ab" * 32
NETWORK_ID = "1" * 64
TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.28.44.0/29",
    gateway="172.28.44.1",
    container="172.28.44.2",
)


def plan(paths: CoturnRuntimePaths) -> NetworkPlan:
    return NetworkPlan(
        identity=RuntimeIdentity.create(run_id="relay-test", owner_nonce=NONCE),
        paths=paths,
        topology=TOPOLOGY,
    )


def network_inspection(
    selected: NetworkPlan,
    *,
    containers: object = None,
) -> list[dict[str, object]]:
    return [
        {
            "Id": NETWORK_ID,
            "Name": selected.identity.network_name,
            "Labels": selected.labels("network"),
            "Scope": "local",
            "Driver": "bridge",
            "Internal": True,
            "Attachable": False,
            "Ingress": False,
            "EnableIPv6": False,
            "ConfigOnly": False,
            "Containers": {} if containers is None else containers,
            "Options": selected.options,
            "IPAM": {
                "Driver": "default",
                "Options": {},
                "Config": [{"Subnet": "172.28.44.0/29", "Gateway": "172.28.44.1"}],
            },
        }
    ]


def test_topology_selection_is_deterministic_private_and_collision_checked() -> None:
    first = select_bridge_topology(
        owner_nonce=NONCE,
        occupied_routes=(),
        occupied_docker_networks=(),
    )
    assert (
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            occupied_docker_networks=(),
        )
        == first
    )
    assert first.network.prefixlen == 29
    assert first.gateway == first.network.network_address + 1
    assert first.container == first.network.network_address + 2
    collision = HostIPv4Route(first.network, "existing0")
    second = select_bridge_topology(
        owner_nonce=NONCE,
        occupied_routes=(collision,),
        occupied_docker_networks=(str(first.network),),
    )
    assert not second.network.overlaps(first.network)
    with pytest.raises(CoturnDockerNetworkError, match="selection input is invalid"):
        select_bridge_topology(
            owner_nonce="secret",
            occupied_routes=(),
            occupied_docker_networks=(),
        )


def test_network_plan_fingerprint_is_exact_deterministic_redacted_and_path_bound(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = plan(_paths(first_root))
    repeated = plan(first.paths)
    second = plan(_paths(second_root))
    expected = hashlib.sha256(
        b"murmur.voice-e2e.coturn.run-dir.v1\x00"
        + os.fspath(first.paths.contract.run_dir).encode("utf-8")
    ).hexdigest()
    assert first.run_dir_fingerprint == expected
    assert repeated.run_dir_fingerprint == expected
    assert second.run_dir_fingerprint != expected
    assert len(first.run_dir_fingerprint) == 64
    assert first.run_dir_fingerprint not in repr(first)
    assert os.fspath(first.paths.contract.run_dir) not in repr(first)
    assert first.run_dir_fingerprint in first.labels("network").values()


def test_topology_selection_caps_and_collapses_maximum_inventory() -> None:
    maximum = ("10.224.0.0/29",) * 4_096
    selected = select_bridge_topology(
        owner_nonce=NONCE,
        occupied_routes=(),
        occupied_docker_networks=maximum,
    )
    assert not selected.network.overlaps(ipaddress.IPv4Network(maximum[0]))
    with pytest.raises(CoturnDockerNetworkError, match="Docker IPAM input is invalid"):
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            occupied_docker_networks=(*maximum, "10.224.0.8/29"),
        )


def test_topology_selection_exhaustion_is_bounded_and_fail_closed() -> None:
    with pytest.raises(CoturnDockerNetworkError, match="No collision-free"):
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            occupied_docker_networks=tuple(
                str(pool)
                for pool in (
                    ipaddress.IPv4Network("10.224.0.0/11"),
                    ipaddress.IPv4Network("172.28.0.0/14"),
                    ipaddress.IPv4Network("192.168.0.0/16"),
                )
            ),
        )


def test_bridge_transition_allows_only_owned_exact_route_and_gateway(tmp_path: Path) -> None:
    selected = plan(_paths(tmp_path))
    default = HostIPv4Route(ipaddress.IPv4Network("0.0.0.0/0"), "eth0")
    owned = HostIPv4Route(TOPOLOGY.network, selected.identity.bridge_name)
    validate_bridge_route_transition(
        plan=selected,
        before=(default,),
        after=(default, owned),
        bridge_ipv4=TOPOLOGY.gateway,
    )
    for before, after, address in (
        ((owned,), (owned,), TOPOLOGY.gateway),
        ((), (HostIPv4Route(TOPOLOGY.network, "unknown0"),), TOPOLOGY.gateway),
        ((), (owned,), TOPOLOGY.container),
        (
            (),
            (owned, HostIPv4Route(ipaddress.IPv4Network("172.28.44.0/30"), "x0")),
            TOPOLOGY.gateway,
        ),
    ):
        with pytest.raises(CoturnDockerNetworkError):
            validate_bridge_route_transition(
                plan=selected,
                before=before,
                after=after,
                bridge_ipv4=address,
            )


def test_network_create_command_has_exact_internal_named_bridge_ipam_and_labels(
    tmp_path: Path,
) -> None:
    selected = plan(_paths(tmp_path))
    request = build_network_create_request(_tools(), selected)
    argv = request.argv
    assert argv[5:9] == ("network", "create", "--driver", "bridge")
    assert "--internal" in argv
    assert argv[argv.index("--subnet") + 1] == "172.28.44.0/29"
    assert argv[argv.index("--gateway") + 1] == "172.28.44.1"
    assert f"com.docker.network.bridge.name={selected.identity.bridge_name}" in argv
    assert all(f"{key}={value}" in argv for key, value in selected.labels("network").items())
    assert "--publish" not in argv and "--network" not in argv


def test_network_full_use_and_cleanup_authorities_are_separate_and_redacted(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = plan(paths)
    inspection = network_inspection(selected)
    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID + "\n",
        inspection=inspection,
    )
    validated = validate_network_for_container(authority, inspection)
    cleanup = validate_network_cleanup_target(authority, inspection)
    assert validated.authority is authority
    assert cleanup.network_id == NETWORK_ID
    assert repr(authority) == "NetworkCleanupAuthority()"
    assert NETWORK_ID not in repr(authority)
    assert build_network_inspect_request(_tools(), selected, NETWORK_ID).argv[-1] == NETWORK_ID
    assert build_network_remove_request(_tools(), cleanup).argv[-2:] == (
        "rm",
        NETWORK_ID,
    )
    assert build_network_absence_request(_tools(), cleanup).argv[-6:] == (
        "network",
        "ls",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={NETWORK_ID}",
    )
    with pytest.raises(TypeError, match="factory-owned"):
        NetworkCleanupAuthority(object(), NETWORK_ID, selected)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ((0, "Internal"), False),
        ((0, "Attachable"), True),
        ((0, "EnableIPv6"), True),
        ((0, "Options", "com.docker.network.bridge.enable_icc"), "true"),
        ((0, "IPAM", "Config", 0, "Subnet"), "172.28.44.0/24"),
        ((0, "IPAM", "Config", 0, "Gateway"), "172.28.44.2"),
    ],
)
def test_network_full_use_rejects_topology_and_isolation_tamper(
    tmp_path: Path,
    path: tuple[object, ...],
    value: object,
) -> None:
    selected = plan(_paths(tmp_path))
    inspection = copy.deepcopy(network_inspection(selected))
    target: object = inspection
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    with pytest.raises(CoturnDockerNetworkError, match="unsafe for container use"):
        validate_network_for_container(authority, inspection)
    assert validate_network_cleanup_target(authority, inspection).network_id == NETWORK_ID


def test_network_cleanup_refuses_unknown_attachment_or_ownership_tamper(
    tmp_path: Path,
) -> None:
    selected = plan(_paths(tmp_path))
    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID,
        inspection=network_inspection(selected),
    )
    attached = network_inspection(selected, containers={"2" * 64: {"Name": "foreign"}})
    with pytest.raises(CoturnDockerNetworkError, match="unknown attachments"):
        validate_network_cleanup_target(authority, attached)
    tampered = network_inspection(selected)
    tampered[0]["Labels"] = {"foreign": "true"}
    with pytest.raises(CoturnDockerNetworkError, match="ownership is invalid"):
        validate_network_cleanup_target(authority, tampered)

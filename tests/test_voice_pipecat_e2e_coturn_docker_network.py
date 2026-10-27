"""Synthetic Docker bridge ownership tests; no network or Docker is used."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
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
    NetworkInventoryBudget,
    NetworkPlan,
    build_network_absence_request,
    build_network_create_request,
    build_network_inspect_request,
    build_network_inventory_inspect_request,
    build_network_inventory_request,
    build_network_remove_request,
    complete_network_inventory,
    establish_network_cleanup_authority,
    parse_network_inventory_ids,
    parse_network_inventory_subnets,
    select_bridge_topology,
    validate_bridge_route_transition,
    validate_network_cleanup_target,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    CommandResult,
    CoturnRuntimePaths,
    HostIPv4Route,
    RuntimeIdentity,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
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


def completed_inventory(
    paths: CoturnRuntimePaths,
    subnets: tuple[str, ...],
):
    identifiers = tuple(f"{index + 1:064x}" for index in range(len(subnets)))
    budget = NetworkInventoryBudget(
        network_ids=identifiers,
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )
    for identifier, subnet in zip(identifiers, subnets, strict=True):
        build_network_inventory_inspect_request(_tools(), paths, identifier, budget)
        parse_network_inventory_subnets(
            CommandResult(
                0,
                json.dumps([{"Id": identifier, "IPAM": {"Config": [{"Subnet": subnet}]}}]).encode(
                    "ascii"
                ),
                b"",
            ),
            expected_network_id=identifier,
            budget=budget,
        )
    return complete_network_inventory(budget)


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


def test_topology_selection_is_deterministic_private_and_collision_checked(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    empty = completed_inventory(paths, ())
    first = select_bridge_topology(
        owner_nonce=NONCE,
        occupied_routes=(),
        completed_inventory=empty,
    )
    assert (
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            completed_inventory=empty,
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
        completed_inventory=completed_inventory(paths, (str(first.network),)),
    )
    assert not second.network.overlaps(first.network)
    with pytest.raises(CoturnDockerNetworkError, match="selection input is invalid"):
        select_bridge_topology(
            owner_nonce="secret",
            occupied_routes=(),
            completed_inventory=empty,
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


def test_topology_selection_requires_a_completed_inventory_receipt(tmp_path: Path) -> None:
    receipt = completed_inventory(_paths(tmp_path), ("10.224.0.0/29",))
    selected = select_bridge_topology(
        owner_nonce=NONCE,
        occupied_routes=(),
        completed_inventory=receipt,
    )
    assert not selected.network.overlaps(ipaddress.IPv4Network("10.224.0.0/29"))
    with pytest.raises(CoturnDockerNetworkError, match="Docker IPAM input is invalid"):
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            completed_inventory=object(),  # type: ignore[arg-type]
        )


def test_topology_selection_exhaustion_is_bounded_and_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CoturnDockerNetworkError, match="No collision-free"):
        select_bridge_topology(
            owner_nonce=NONCE,
            occupied_routes=(),
            completed_inventory=completed_inventory(
                _paths(tmp_path),
                tuple(
                    str(pool)
                    for pool in (
                        ipaddress.IPv4Network("10.224.0.0/11"),
                        ipaddress.IPv4Network("172.28.0.0/14"),
                        ipaddress.IPv4Network("192.168.0.0/16"),
                    )
                ),
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


def test_network_inventory_requests_and_ids_are_exact_bounded_and_nontruncated(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    request = build_network_inventory_request(_tools(), paths)
    assert request.argv[-4:] == ("network", "ls", "--quiet", "--no-trunc")
    assert request.maximum_output_bytes == 4_096 * 65
    identifiers = ("1" * 64, "a" * 64)
    result = CommandResult(0, ("\n".join(identifiers) + "\n").encode("ascii"), b"")
    assert parse_network_inventory_ids(result) == identifiers
    assert parse_network_inventory_ids(CommandResult(0, b"", b"")) == ()
    budget = NetworkInventoryBudget(
        network_ids=identifiers,
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )
    inspect = build_network_inventory_inspect_request(_tools(), paths, identifiers[1], budget)
    assert inspect.argv[-3:] == ("network", "inspect", identifiers[1])
    assert inspect.timeout_seconds == 10.0
    assert repr(budget) == "NetworkInventoryBudget()"
    assert identifiers[1] not in repr(budget)


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, b"", b""),
        CommandResult(0, b"1" * 64, b"daemon detail"),
        CommandResult(0, b"1" * 63 + b"\n", b""),
        CommandResult(0, (b"1" * 64 + b"\n") * 2, b""),
        CommandResult(0, b"1" * 64 + b"\r\n", b""),
        CommandResult(0, (b"1" * 64 + b"\n") * 4_097, b""),
    ],
)
def test_network_inventory_id_parser_fails_closed_with_one_fixed_error(
    result: CommandResult,
) -> None:
    with pytest.raises(
        CoturnDockerNetworkError,
        match=r"^Coturn Docker network inventory is invalid$",
    ):
        parse_network_inventory_ids(result)


@pytest.mark.parametrize(
    "network_ids",
    [
        [NETWORK_ID],
        ([],),
        (True,),
        ("traceback-sentinel-network-id",),
    ],
)
def test_network_inventory_budget_rejects_malformed_ids_with_fixed_scrubbed_error(
    network_ids: object,
) -> None:
    with pytest.raises(
        CoturnDockerNetworkError,
        match=r"^Coturn Docker network inventory budget is invalid$",
    ) as captured:
        NetworkInventoryBudget(  # type: ignore[arg-type]
            network_ids=network_ids,
            absolute_deadline=110.0,
            clock=lambda: 100.0,
        )
    assert not traceback_contains(captured.value, "traceback-sentinel-network-id")


def test_network_inventory_parser_failures_scrub_raw_traceback_locals(tmp_path: Path) -> None:
    sentinel = b"traceback-sentinel-network-output"
    with pytest.raises(CoturnDockerNetworkError) as captured:
        parse_network_inventory_ids(CommandResult(0, sentinel, b""))
    assert not traceback_contains(captured.value, sentinel)

    budget = NetworkInventoryBudget(
        network_ids=(NETWORK_ID,),
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )
    build_network_inventory_inspect_request(_tools(), _paths(tmp_path), NETWORK_ID, budget)
    with pytest.raises(CoturnDockerNetworkError) as captured:
        parse_network_inventory_subnets(
            CommandResult(0, sentinel, b""),
            expected_network_id=NETWORK_ID,
            budget=budget,
        )
    assert not traceback_contains(captured.value, sentinel)


def test_network_inventory_rejects_huge_json_integer_with_fixed_scrubbed_error(
    tmp_path: Path,
) -> None:
    sentinel = b"traceback-sentinel-network-huge-json"
    payload = (
        b'[{"Id":"'
        + NETWORK_ID.encode("ascii")
        + b'","IPAM":{"Config":[]},"Raw":'
        + b"9" * 5_000
        + b',"Marker":"'
        + sentinel
        + b'"}]'
    )
    budget = NetworkInventoryBudget(
        network_ids=(NETWORK_ID,),
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )
    build_network_inventory_inspect_request(_tools(), _paths(tmp_path), NETWORK_ID, budget)
    with pytest.raises(
        CoturnDockerNetworkError,
        match=r"^Coturn Docker network IPAM is invalid$",
    ) as captured:
        parse_network_inventory_subnets(
            CommandResult(0, payload, b""),
            expected_network_id=NETWORK_ID,
            budget=budget,
        )
    assert not traceback_contains(captured.value, sentinel)


def test_network_inventory_ipam_parser_binds_exact_id_and_canonicalizes_ipv4(
    tmp_path: Path,
) -> None:
    value = [
        {
            "Id": NETWORK_ID,
            "IPAM": {
                "Config": [
                    {"Subnet": "2001:db8::/64"},
                    {"Subnet": "172.30.0.0/16"},
                    {"Subnet": "10.42.0.0/24"},
                    {"Subnet": "10.42.0.0/24"},
                ]
            },
        }
    ]
    result = CommandResult(0, json.dumps(value).encode("ascii"), b"")
    budget = NetworkInventoryBudget(
        network_ids=(NETWORK_ID,),
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )
    paths = _paths(tmp_path)
    build_network_inventory_inspect_request(_tools(), paths, NETWORK_ID, budget)
    assert parse_network_inventory_subnets(
        result,
        expected_network_id=NETWORK_ID,
        budget=budget,
    ) == ("10.42.0.0/24", "172.30.0.0/16")
    assert (budget.remaining_networks, budget.remaining_subnets) == (0, 4_092)

    value[0]["Id"] = "2" * 64
    secret = b"secret-daemon-detail"
    for invalid in (
        CommandResult(0, json.dumps(value).encode("ascii"), b""),
        CommandResult(0, b'[{"Id":', b""),
        CommandResult(0, b"[]", secret),
    ):
        failed_budget = NetworkInventoryBudget(
            network_ids=(NETWORK_ID,),
            absolute_deadline=110.0,
            clock=lambda: 100.0,
        )
        build_network_inventory_inspect_request(_tools(), paths, NETWORK_ID, failed_budget)
        with pytest.raises(
            CoturnDockerNetworkError,
            match=r"^Coturn Docker network IPAM is invalid$",
        ) as captured:
            parse_network_inventory_subnets(
                invalid,
                expected_network_id=NETWORK_ID,
                budget=failed_budget,
            )
        assert secret.decode("ascii") not in str(captured.value)


def test_network_inventory_budget_is_global_across_inspections_and_deadline_bound(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    identifiers = (NETWORK_ID, "2" * 64)
    now = [100.0]
    budget = NetworkInventoryBudget(
        network_ids=identifiers,
        absolute_deadline=101.0,
        clock=lambda: now[0],
    )
    first = [{"Id": identifiers[0], "IPAM": {"Config": [{"Subnet": "10.0.0.0/8"}] * 4_096}}]
    build_network_inventory_inspect_request(_tools(), paths, identifiers[0], budget)
    assert parse_network_inventory_subnets(
        CommandResult(0, json.dumps(first).encode("ascii"), b""),
        expected_network_id=identifiers[0],
        budget=budget,
    ) == ("10.0.0.0/8",)
    assert budget.remaining_subnets == 0

    with pytest.raises(
        CoturnDockerNetworkError,
        match=r"^Coturn Docker network inventory budget is invalid$",
    ):
        build_network_inventory_inspect_request(_tools(), paths, identifiers[1], budget)

    expired = NetworkInventoryBudget(
        network_ids=(NETWORK_ID,),
        absolute_deadline=101.0,
        clock=lambda: now[0],
    )
    build_network_inventory_inspect_request(_tools(), paths, NETWORK_ID, expired)
    now[0] = 101.01
    with pytest.raises(CoturnDockerNetworkError, match="inventory budget is invalid"):
        parse_network_inventory_subnets(
            CommandResult(
                0,
                json.dumps([{"Id": NETWORK_ID, "IPAM": {"Config": []}}]).encode("ascii"),
                b"",
            ),
            expected_network_id=NETWORK_ID,
            budget=expired,
        )


def test_network_inventory_completion_rejects_prefix_pending_failure_and_expiry(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    now = [100.0]
    prefix = NetworkInventoryBudget(
        network_ids=(NETWORK_ID, "2" * 64),
        absolute_deadline=101.0,
        clock=lambda: now[0],
    )
    build_network_inventory_inspect_request(_tools(), paths, NETWORK_ID, prefix)
    parse_network_inventory_subnets(
        CommandResult(
            0,
            json.dumps([{"Id": NETWORK_ID, "IPAM": {"Config": []}}]).encode("ascii"),
            b"",
        ),
        expected_network_id=NETWORK_ID,
        budget=prefix,
    )
    with pytest.raises(CoturnDockerNetworkError, match="inventory budget is invalid"):
        complete_network_inventory(prefix)

    pending = NetworkInventoryBudget(
        network_ids=(NETWORK_ID,),
        absolute_deadline=101.0,
        clock=lambda: now[0],
    )
    build_network_inventory_inspect_request(_tools(), paths, NETWORK_ID, pending)
    with pytest.raises(CoturnDockerNetworkError, match="inventory budget is invalid"):
        complete_network_inventory(pending)

    expired = NetworkInventoryBudget(
        network_ids=(),
        absolute_deadline=101.0,
        clock=lambda: now[0],
    )
    now[0] = 101.01
    with pytest.raises(CoturnDockerNetworkError, match="inventory budget is invalid"):
        complete_network_inventory(expired)


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


@pytest.mark.parametrize("surface", ["establish", "use", "cleanup"])
def test_network_validation_failures_discard_raw_inspection_and_plan_graphs(
    tmp_path: Path,
    surface: str,
) -> None:
    secret = "traceback-sentinel-network-inspect"
    plan_secret = "traceback-sentinel-network-plan"
    plan_root = tmp_path / plan_secret
    plan_root.mkdir()
    selected = plan(_paths(plan_root))
    valid = network_inspection(selected)
    authority = None
    if surface != "establish":
        authority = establish_network_cleanup_authority(
            plan=selected,
            network_id=NETWORK_ID,
            inspection=valid,
        )
    inspection = copy.deepcopy(valid)
    inspection[0]["RawSecret"] = secret
    if surface == "establish":
        inspection[0]["Labels"] = {"foreign": secret}
        with pytest.raises(CoturnDockerNetworkError) as captured:
            establish_network_cleanup_authority(
                plan=selected,
                network_id=NETWORK_ID,
                inspection=inspection,
            )
    elif surface == "use":
        inspection[0]["Internal"] = False
        inspection[0]["IPAM"] = {"Config": [{"Subnet": secret}]}
        with pytest.raises(CoturnDockerNetworkError) as captured:
            validate_network_for_container(authority, inspection)  # type: ignore[arg-type]
    else:
        inspection[0]["Containers"] = {"2" * 64: {"Name": secret}}
        with pytest.raises(CoturnDockerNetworkError) as captured:
            validate_network_cleanup_target(authority, inspection)  # type: ignore[arg-type]
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret, plan_secret)

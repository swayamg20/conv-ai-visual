"""Owned Docker bridge selection, inspection, and cleanup authority."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from bisect import bisect_right
from dataclasses import dataclass, field

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_docker import (
    RUN_DIR_FINGERPRINT_LABEL,
    docker_request,
    one_inspection,
    translate_created_id,
)
from scripts.voice_pipecat_e2e_coturn_docker_inventory import (
    MAX_NETWORK_INVENTORY_ITEMS,
    CompletedNetworkInventory,
    CoturnDockerNetworkError,
    NetworkInventoryBudget,
    complete_network_inventory,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandResult,
    CoturnRuntimePaths,
    HostIPv4Route,
    RuntimeIdentity,
    TrustedHostTools,
    require_full_resource_id,
)
from scripts.voice_pipecat_e2e_coturn_validation_boundary import (
    validate_without_raw_traceback,
)

_PRIVATE_POOLS = tuple(
    ipaddress.IPv4Network(value) for value in ("10.224.0.0/11", "172.28.0.0/14", "192.168.0.0/16")
)
_MAX_OCCUPIED_NETWORKS = MAX_NETWORK_INVENTORY_ITEMS
_MAX_INVENTORY_BYTES = _MAX_OCCUPIED_NETWORKS * 65
_MAX_INSPECTION_BYTES = 1_048_576
_RUN_DIR_FINGERPRINT_DOMAIN = b"murmur.voice-e2e.coturn.run-dir.v1\x00"
_AUTHORITY_TOKEN = object()
_VALIDATION_TOKEN = object()


def select_bridge_topology(
    *,
    owner_nonce: object,
    occupied_routes: tuple[HostIPv4Route, ...],
    completed_inventory: CompletedNetworkInventory,
) -> CoturnBridgeTopology:
    """Choose a deterministic private /29 with no pre-existing overlap."""

    if not isinstance(owner_nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_nonce):
        raise CoturnDockerNetworkError("Coturn bridge selection input is invalid")
    if (
        not isinstance(occupied_routes, tuple)
        or len(occupied_routes) > _MAX_OCCUPIED_NETWORKS
        or not all(isinstance(item, HostIPv4Route) for item in occupied_routes)
    ):
        raise CoturnDockerNetworkError("Coturn bridge collision input is invalid")
    if not isinstance(completed_inventory, CompletedNetworkInventory):
        raise CoturnDockerNetworkError("Coturn Docker IPAM input is invalid")
    occupied_docker_networks = completed_inventory.ipv4_subnets
    if len(occupied_routes) + len(occupied_docker_networks) > _MAX_OCCUPIED_NETWORKS:
        raise CoturnDockerNetworkError("Coturn Docker IPAM input is invalid")
    docker_networks = []
    for value in occupied_docker_networks:
        try:
            parsed = ipaddress.ip_network(value, strict=True)
        except (TypeError, ValueError):
            raise CoturnDockerNetworkError("Coturn Docker IPAM input is invalid") from None
        if not isinstance(parsed, ipaddress.IPv4Network) or parsed.prefixlen == 0:
            raise CoturnDockerNetworkError("Coturn Docker IPAM input is invalid")
        docker_networks.append(parsed)
    occupied = (
        *(route.network for route in occupied_routes if route.network.prefixlen),
        *docker_networks,
    )
    collapsed = tuple(ipaddress.collapse_addresses(occupied))
    interval_starts = tuple(int(network.network_address) for network in collapsed)
    interval_ends = tuple(int(network.broadcast_address) for network in collapsed)
    seed = int.from_bytes(hashlib.sha256(owner_nonce.encode("ascii")).digest()[:8], "big")
    pivot = seed % len(_PRIVATE_POOLS)
    for pool in _PRIVATE_POOLS[pivot:] + _PRIVATE_POOLS[:pivot]:
        count = 1 << (29 - pool.prefixlen)
        start = seed % count
        for offset in range(count):
            address = int(pool.network_address) + ((start + offset) % count) * 8
            candidate = ipaddress.IPv4Network((address, 29))
            candidate_start = int(candidate.network_address)
            candidate_end = int(candidate.broadcast_address)
            interval_index = bisect_right(interval_starts, candidate_end) - 1
            if interval_index >= 0 and interval_ends[interval_index] >= candidate_start:
                continue
            return CoturnBridgeTopology(
                network=candidate,
                gateway=candidate.network_address + 1,
                container=candidate.network_address + 2,
            )
    raise CoturnDockerNetworkError("No collision-free Coturn bridge is available")


@dataclass(frozen=True)
class NetworkPlan:
    identity: RuntimeIdentity = field(repr=False)
    paths: CoturnRuntimePaths = field(repr=False)
    topology: CoturnBridgeTopology = field(repr=False)
    _run_dir_fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RuntimeIdentity):
            raise CoturnDockerNetworkError("Coturn network plan is invalid")
        try:
            expected_identity = RuntimeIdentity.create(
                run_id=self.identity.run_id,
                owner_nonce=self.identity.owner_nonce,
            )
        except RuntimeError:
            raise CoturnDockerNetworkError("Coturn network plan is invalid") from None
        if (
            not isinstance(self.paths, CoturnRuntimePaths)
            or not isinstance(self.topology, CoturnBridgeTopology)
            or self.identity != expected_identity
            or self.paths.contract.run_id != self.identity.run_id
        ):
            raise CoturnDockerNetworkError("Coturn network plan is invalid")
        try:
            encoded_run_dir = os.fspath(self.paths.contract.run_dir).encode("utf-8")
        except UnicodeError:
            raise CoturnDockerNetworkError("Coturn network plan is invalid") from None
        object.__setattr__(
            self,
            "_run_dir_fingerprint",
            hashlib.sha256(_RUN_DIR_FINGERPRINT_DOMAIN + encoded_run_dir).hexdigest(),
        )

    @property
    def run_dir_fingerprint(self) -> str:
        return self._run_dir_fingerprint

    def labels(self, resource: str) -> dict[str, str]:
        result = self.identity.labels(resource)
        result[RUN_DIR_FINGERPRINT_LABEL] = self.run_dir_fingerprint
        return result

    @property
    def options(self) -> dict[str, str]:
        return {
            "com.docker.network.bridge.enable_icc": "false",
            "com.docker.network.bridge.enable_ip_masquerade": "false",
            "com.docker.network.bridge.name": self.identity.bridge_name,
        }


def validate_bridge_route_transition(
    *,
    plan: NetworkPlan,
    before: tuple[HostIPv4Route, ...],
    after: tuple[HostIPv4Route, ...],
    bridge_ipv4: object,
) -> None:
    """Allow exactly the new /29 route on the owned named interface."""

    if not all(isinstance(value, tuple) for value in (before, after)) or not all(
        isinstance(route, HostIPv4Route) for routes in (before, after) for route in routes
    ):
        raise CoturnDockerNetworkError("Coturn host route transition is invalid")
    topology = plan.topology
    if any(
        route.network.prefixlen and route.network.overlaps(topology.network) for route in before
    ):
        raise CoturnDockerNetworkError("Coturn bridge route collided before creation")
    overlaps = [
        route
        for route in after
        if route.network.prefixlen and route.network.overlaps(topology.network)
    ]
    if (
        len(overlaps) != 1
        or overlaps[0].network != topology.network
        or overlaps[0].interface != plan.identity.bridge_name
        or bridge_ipv4 != topology.gateway
    ):
        raise CoturnDockerNetworkError("Coturn owned bridge route is invalid")


class NetworkCleanupAuthority:
    __slots__ = ("_network_id", "_plan")

    def __init__(self, token: object, network_id: str, plan: NetworkPlan) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Network cleanup authority is factory-owned")
        self._network_id = network_id
        self._plan = plan

    @property
    def network_id(self) -> str:
        return self._network_id

    @property
    def plan(self) -> NetworkPlan:
        return self._plan

    def __repr__(self) -> str:
        return "NetworkCleanupAuthority()"


class ValidatedNetwork:
    __slots__ = ("_authority",)

    def __init__(self, token: object, authority: NetworkCleanupAuthority) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Validated network is factory-owned")
        self._authority = authority

    @property
    def authority(self) -> NetworkCleanupAuthority:
        return self._authority

    def __repr__(self) -> str:
        return "ValidatedNetwork()"


class ValidatedNetworkCleanup:
    __slots__ = ("_authority",)

    def __init__(self, token: object, authority: NetworkCleanupAuthority) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Validated network cleanup is factory-owned")
        self._authority = authority

    @property
    def network_id(self) -> str:
        return self._authority.network_id

    @property
    def plan(self) -> NetworkPlan:
        return self._authority.plan

    def __repr__(self) -> str:
        return "ValidatedNetworkCleanup()"


def establish_network_cleanup_authority(
    *,
    plan: NetworkPlan,
    network_id: object,
    inspection: object,
) -> NetworkCleanupAuthority:
    try:
        return validate_without_raw_traceback(
            lambda: _establish_network_cleanup_authority(
                plan=plan,
                network_id=network_id,
                inspection=inspection,
            ),
            error_type=CoturnDockerNetworkError,
            fallback="Coturn network ownership is invalid",
            allowed=_NETWORK_VALIDATION_ERRORS,
        )
    finally:
        plan = network_id = inspection = None


def _establish_network_cleanup_authority(
    *,
    plan: NetworkPlan,
    network_id: object,
    inspection: object,
) -> NetworkCleanupAuthority:
    identifier = translate_created_id(network_id)
    _validate_identity(plan, identifier, inspection)
    return NetworkCleanupAuthority(_AUTHORITY_TOKEN, identifier, plan)


def validate_network_for_container(
    authority: NetworkCleanupAuthority,
    inspection: object,
) -> ValidatedNetwork:
    try:
        return validate_without_raw_traceback(
            lambda: _validate_network_for_container(authority, inspection),
            error_type=CoturnDockerNetworkError,
            fallback="Coturn network is unsafe for container use",
            allowed=_NETWORK_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_network_for_container(
    authority: NetworkCleanupAuthority,
    inspection: object,
) -> ValidatedNetwork:
    _validate_identity(authority.plan, authority.network_id, inspection)
    network = one_inspection(inspection, "Coturn network inspection")
    ipam = network.get("IPAM")
    config = ipam.get("Config") if isinstance(ipam, dict) else None
    item = config[0] if isinstance(config, list) and len(config) == 1 else None
    topology = authority.plan.topology
    expected = {
        "Scope": "local",
        "Driver": "bridge",
        "Internal": True,
        "Attachable": False,
        "Ingress": False,
        "EnableIPv6": False,
        "ConfigOnly": False,
        "Containers": {},
        "Options": authority.plan.options,
    }
    if (
        any(network.get(key) != value for key, value in expected.items())
        or not isinstance(ipam, dict)
        or ipam.get("Driver") != "default"
        or ipam.get("Options") not in (None, {})
        or not isinstance(item, dict)
        or item.get("Subnet") != str(topology.network)
        or item.get("Gateway") != str(topology.gateway)
        or item.get("IPRange") not in {None, ""}
        or item.get("AuxiliaryAddresses") not in (None, {})
        or any(key not in {"Subnet", "Gateway", "IPRange", "AuxiliaryAddresses"} for key in item)
    ):
        raise CoturnDockerNetworkError("Coturn network is unsafe for container use")
    return ValidatedNetwork(_VALIDATION_TOKEN, authority)


def validate_network_cleanup_target(
    authority: NetworkCleanupAuthority,
    inspection: object,
) -> ValidatedNetworkCleanup:
    try:
        return validate_without_raw_traceback(
            lambda: _validate_network_cleanup_target(authority, inspection),
            error_type=CoturnDockerNetworkError,
            fallback="Coturn network cleanup has unknown attachments",
            allowed=_NETWORK_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_network_cleanup_target(
    authority: NetworkCleanupAuthority,
    inspection: object,
) -> ValidatedNetworkCleanup:
    _validate_identity(authority.plan, authority.network_id, inspection)
    if one_inspection(inspection, "Coturn network inspection").get("Containers") != {}:
        raise CoturnDockerNetworkError("Coturn network cleanup has unknown attachments")
    return ValidatedNetworkCleanup(_VALIDATION_TOKEN, authority)


def build_network_create_request(
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> CommandRequest:
    arguments = [
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "--subnet",
        str(plan.topology.network),
        "--gateway",
        str(plan.topology.gateway),
    ]
    for key, value in sorted(plan.options.items()):
        arguments.extend(("--opt", f"{key}={value}"))
    for key, value in sorted(plan.labels("network").items()):
        arguments.extend(("--label", f"{key}={value}"))
    return docker_request(tools, plan.paths, *arguments, plan.identity.network_name)


def build_network_inventory_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CommandRequest:
    """List bounded, non-truncated network IDs without accepting names."""

    request = docker_request(tools, paths, "network", "ls", "--quiet", "--no-trunc")
    return CommandRequest(
        argv=request.argv,
        timeout_seconds=request.timeout_seconds,
        maximum_output_bytes=_MAX_INVENTORY_BYTES,
    )


def parse_network_inventory_ids(result: CommandResult) -> tuple[str, ...]:
    """Parse at most 4096 exact full Docker network IDs."""

    parsed = _parse_network_inventory_ids(result)
    result = None  # type: ignore[assignment]
    if parsed is None:
        _raise_network_inventory_error()
    return parsed


def _parse_network_inventory_ids(result: object) -> tuple[str, ...] | None:
    if (
        not isinstance(result, CommandResult)
        or result.returncode != 0
        or result.stderr
        or len(result.stdout) > _MAX_INVENTORY_BYTES
    ):
        return None
    if not result.stdout:
        return ()
    try:
        value = result.stdout.decode("ascii")
    except UnicodeError:
        return None
    if value.endswith("\n"):
        value = value[:-1]
    identifiers = value.split("\n")
    if (
        not value
        or len(identifiers) > _MAX_OCCUPIED_NETWORKS
        or len(set(identifiers)) != len(identifiers)
    ):
        return None
    try:
        return tuple(require_full_resource_id(identifier) for identifier in identifiers)
    except RuntimeError:
        return None


def build_network_inventory_inspect_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    network_id: str,
    budget: NetworkInventoryBudget,
) -> CommandRequest:
    """Inspect exactly one full inventory ID so output remains bounded."""

    try:
        identifier = require_full_resource_id(network_id)
    except RuntimeError:
        identifier = ""
    timeout = (
        budget.begin_inspection(identifier) if isinstance(budget, NetworkInventoryBudget) else None
    )
    network_id = ""
    if not identifier or timeout is None:
        identifier = ""
        budget = None  # type: ignore[assignment]
        _raise_network_inventory_budget_error()
    return docker_request(
        tools,
        paths,
        "network",
        "inspect",
        identifier,
        timeout_seconds=timeout,
    )


def parse_network_inventory_subnets(
    result: CommandResult,
    *,
    expected_network_id: str,
    budget: NetworkInventoryBudget,
) -> tuple[str, ...]:
    """Return canonical IPv4 IPAM subnets from one exact-ID inspection."""

    try:
        identifier = require_full_resource_id(expected_network_id)
    except RuntimeError:
        identifier = ""
    parsed = _parse_network_inventory_subnets(result, identifier)
    result = None  # type: ignore[assignment]
    expected_network_id = ""
    if parsed is None or not isinstance(budget, NetworkInventoryBudget):
        if isinstance(budget, NetworkInventoryBudget):
            budget.abort()
        identifier = ""
        budget = None  # type: ignore[assignment]
        _raise_network_ipam_error()
    networks, entry_count = parsed
    parsed = None
    if not budget.commit_inspection(
        identifier,
        ipam_entries=entry_count,
        ipv4_subnets=networks,
    ):
        identifier = ""
        networks = ()
        budget = None  # type: ignore[assignment]
        _raise_network_inventory_budget_error()
    identifier = ""
    return networks


def _parse_network_inventory_subnets(
    result: object,
    identifier: str,
) -> tuple[tuple[str, ...], int] | None:
    if (
        not isinstance(result, CommandResult)
        or result.returncode != 0
        or result.stderr
        or not result.stdout
        or len(result.stdout) > _MAX_INSPECTION_BYTES
    ):
        return None
    try:
        decoded = json.loads(result.stdout.decode("ascii"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    item = decoded[0] if isinstance(decoded, list) and len(decoded) == 1 else None
    ipam = item.get("IPAM") if isinstance(item, dict) else None
    config = ipam.get("Config") if isinstance(ipam, dict) else None
    if (
        not isinstance(item, dict)
        or item.get("Id") != identifier
        or not isinstance(ipam, dict)
        or not isinstance(config, list)
        or len(config) > _MAX_OCCUPIED_NETWORKS
    ):
        return None
    networks: set[ipaddress.IPv4Network] = set()
    try:
        for entry in config:
            if not isinstance(entry, dict) or not isinstance(entry.get("Subnet"), str):
                raise ValueError
            network = ipaddress.ip_network(entry["Subnet"], strict=True)
            if isinstance(network, ipaddress.IPv4Network):
                if network.prefixlen == 0:
                    raise ValueError
                networks.add(network)
    except (TypeError, ValueError):
        return None
    return (
        tuple(
            str(network)
            for network in sorted(
                networks, key=lambda value: (int(value.network_address), value.prefixlen)
            )
        ),
        len(config),
    )


def _raise_network_inventory_error() -> None:
    raise CoturnDockerNetworkError("Coturn Docker network inventory is invalid") from None


def _raise_network_inventory_budget_error() -> None:
    raise CoturnDockerNetworkError("Coturn Docker network inventory budget is invalid") from None


def _raise_network_ipam_error() -> None:
    raise CoturnDockerNetworkError("Coturn Docker network IPAM is invalid") from None


def build_network_inspect_request(
    tools: TrustedHostTools,
    plan: NetworkPlan,
    network_id: str,
) -> CommandRequest:
    require_full_resource_id(network_id)
    return docker_request(tools, plan.paths, "network", "inspect", network_id)


def build_network_name_inspect_request(
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> CommandRequest:
    """Inspect only the exact generated name for missing-ID recovery."""

    return docker_request(tools, plan.paths, "network", "inspect", plan.identity.network_name)


def build_network_remove_request(
    tools: TrustedHostTools,
    target: ValidatedNetworkCleanup,
) -> CommandRequest:
    return docker_request(
        tools,
        target.plan.paths,
        "network",
        "rm",
        target.network_id,
    )


def build_network_absence_request(
    tools: TrustedHostTools,
    target: ValidatedNetworkCleanup,
) -> CommandRequest:
    """Query the exact validated full ID after removal."""

    return docker_request(
        tools,
        target.plan.paths,
        "network",
        "ls",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={target.network_id}",
    )


def _validate_identity(plan: NetworkPlan, identifier: str, inspection: object) -> None:
    network = one_inspection(inspection, "Coturn network inspection")
    if (
        network.get("Id") != identifier
        or network.get("Name") != plan.identity.network_name
        or network.get("Labels") != plan.labels("network")
    ):
        raise CoturnDockerNetworkError("Coturn network ownership is invalid")


_NETWORK_VALIDATION_ERRORS = frozenset(
    {
        "Coturn network ownership is invalid",
        "Coturn network is unsafe for container use",
        "Coturn network cleanup has unknown attachments",
    }
)


__all__ = [
    "CompletedNetworkInventory",
    "CoturnDockerNetworkError",
    "NetworkCleanupAuthority",
    "NetworkInventoryBudget",
    "NetworkPlan",
    "ValidatedNetwork",
    "ValidatedNetworkCleanup",
    "build_network_absence_request",
    "build_network_create_request",
    "build_network_inspect_request",
    "build_network_inventory_inspect_request",
    "build_network_inventory_request",
    "build_network_name_inspect_request",
    "build_network_remove_request",
    "complete_network_inventory",
    "establish_network_cleanup_authority",
    "parse_network_inventory_ids",
    "parse_network_inventory_subnets",
    "select_bridge_topology",
    "validate_bridge_route_transition",
    "validate_network_cleanup_target",
    "validate_network_for_container",
]

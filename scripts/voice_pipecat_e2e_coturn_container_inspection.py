"""Pure exact-shape predicates for owned Coturn container inspections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_IMAGE,
    COTURN_RELAY_MAX_PORT,
    COTURN_RELAY_MIN_PORT,
    COTURN_TLS_PORT,
)
from scripts.voice_pipecat_e2e_coturn_container_state import (
    cleanup_running_state,
    is_created_state,
    is_running_state,
)

COTURN_ENTRYPOINT = "/usr/bin/turnserver"
COTURN_CONTAINER_DIRECTORY = "/run/murmur-coturn"
COTURN_CONTAINER_CONFIG = f"{COTURN_CONTAINER_DIRECTORY}/turnserver.conf"
CONTAINER_MEMORY = 67_108_864
CONTAINER_SHM = 4_194_304
CONTAINER_NANO_CPUS = 500_000_000
CONTAINER_PIDS = 64

_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


@dataclass(frozen=True)
class ContainerInspectionContract:
    container_id: str = field(repr=False)
    container_name: str = field(repr=False)
    image_id: str = field(repr=False)
    labels: dict[str, str] = field(repr=False)
    environment: tuple[str, ...] = field(repr=False)
    working_directory: str = field(repr=False)
    network_name: str = field(repr=False)
    network_id: str = field(repr=False)
    subnet: str = field(repr=False)
    gateway: str = field(repr=False)
    container_ipv4: str = field(repr=False)
    coturn_dir: str = field(repr=False)
    uid: int = field(repr=False)
    gid: int = field(repr=False)


def container_identity_valid(contract: ContainerInspectionContract, inspection: object) -> bool:
    container = _one_container(inspection)
    config = container.get("Config") if container is not None else None
    return bool(
        container is not None
        and container.get("Id") == contract.container_id
        and container.get("Name") == f"/{contract.container_name}"
        and container.get("Image") == contract.image_id
        and isinstance(config, dict)
        and config.get("Image") == COTURN_IMAGE
        and config.get("Labels") == contract.labels
    )


def container_for_use_valid(
    contract: ContainerInspectionContract,
    inspection: object,
    *,
    running: bool,
) -> bool:
    container = _one_container(inspection)
    if container is None or not container_identity_valid(contract, inspection):
        return False
    network_settings = container.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    endpoint = networks.get(contract.network_name) if isinstance(networks, dict) else None
    state = container.get("State")
    state_pid = state.get("Pid") if isinstance(state, dict) else None
    return bool(
        container.get("Path") == COTURN_ENTRYPOINT
        and container.get("Args") == ["-c", COTURN_CONTAINER_CONFIG]
        and container.get("Platform") == "linux"
        and (is_running_state(state) if running else is_created_state(state))
        and ("Pid" not in container or container.get("Pid") == state_pid)
        and _safe_config(container.get("Config"), contract)
        and _safe_host_config(container.get("HostConfig"), contract)
        and _safe_mounts(container.get("Mounts"), contract)
        and isinstance(network_settings, dict)
        and network_settings.get("Ports") == coturn_port_bindings()
        and isinstance(networks, dict)
        and set(networks) == {contract.network_name}
        and isinstance(endpoint, dict)
        and endpoint.get("NetworkID") == contract.network_id
        and endpoint.get("IPAddress") == contract.container_ipv4
        and endpoint.get("IPPrefixLen") == 29
        and endpoint.get("Gateway") == contract.gateway
        and endpoint.get("GlobalIPv6Address") in {None, ""}
        and endpoint.get("GlobalIPv6PrefixLen") in {None, 0}
        and isinstance(endpoint.get("EndpointID"), str)
        and bool(_FULL_ID.fullmatch(endpoint["EndpointID"]))
        and isinstance(endpoint.get("MacAddress"), str)
        and bool(_MAC.fullmatch(endpoint["MacAddress"]))
    )


def container_cleanup_running(
    contract: ContainerInspectionContract,
    inspection: object,
) -> bool | None:
    container = _one_container(inspection)
    if container is None or not container_identity_valid(contract, inspection):
        return None
    return cleanup_running_state(container.get("State"))


def coturn_port_bindings() -> dict[str, list[dict[str, str]]]:
    result = {f"{COTURN_TLS_PORT}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(COTURN_TLS_PORT)}]}
    for port in range(COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT + 1):
        result[f"{port}/udp"] = [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    return result


def coturn_tmpfs_options(uid: int, gid: int) -> str:
    return f"rw,noexec,nosuid,nodev,size=16777216,mode=0700,uid={uid},gid={gid}"


def _one_container(inspection: object) -> dict[str, object] | None:
    if not isinstance(inspection, list) or len(inspection) != 1:
        return None
    value = inspection[0]
    return value if isinstance(value, dict) else None


def _safe_config(value: object, contract: ContainerInspectionContract) -> bool:
    expected = {
        "User": f"{contract.uid}:{contract.gid}",
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Env": list(contract.environment),
        "Cmd": ["-c", COTURN_CONTAINER_CONFIG],
        "Image": COTURN_IMAGE,
        "WorkingDir": contract.working_directory,
        "Entrypoint": [COTURN_ENTRYPOINT],
        "Labels": contract.labels,
        "ExposedPorts": {port: {} for port in coturn_port_bindings()},
        "Healthcheck": {"Test": ["NONE"]},
        "StopTimeout": 5,
    }
    return isinstance(value, dict) and all(value.get(key) == item for key, item in expected.items())


def _safe_host_config(value: object, contract: ContainerInspectionContract) -> bool:
    if not isinstance(value, dict):
        return False
    expected = {
        "NetworkMode": contract.network_id,
        "PortBindings": coturn_port_bindings(),
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "AutoRemove": False,
        "CapDrop": ["ALL"],
        "CgroupnsMode": "private",
        "PidMode": "",
        "UTSMode": "",
        "UsernsMode": "",
        "Privileged": False,
        "PublishAllPorts": False,
        "ReadonlyRootfs": True,
        "SecurityOpt": ["no-new-privileges:true"],
        "LogConfig": {"Type": "none", "Config": {}},
        "Memory": CONTAINER_MEMORY,
        "MemorySwap": CONTAINER_MEMORY,
        "MemorySwappiness": 0,
        "NanoCpus": CONTAINER_NANO_CPUS,
        "PidsLimit": CONTAINER_PIDS,
        "ShmSize": CONTAINER_SHM,
        "Ulimits": [
            {"Name": "core", "Hard": 0, "Soft": 0},
            {"Name": "nofile", "Hard": 1024, "Soft": 1024},
            {"Name": "nproc", "Hard": 64, "Soft": 64},
        ],
        "Tmpfs": {"/tmp": coturn_tmpfs_options(contract.uid, contract.gid)},
    }
    empty = (None, [])
    empty_keys = (
        "CapAdd",
        "Binds",
        "Devices",
        "DeviceRequests",
        "GroupAdd",
        "Dns",
        "DnsOptions",
        "DnsSearch",
        "ExtraHosts",
        "Links",
        "VolumesFrom",
    )
    return bool(
        all(value.get(key) == item for key, item in expected.items())
        and value.get("IpcMode") in {"private", ""}
        and all(value.get(key) in empty for key in empty_keys)
        and _safe_host_mount_specs(value.get("Mounts"), contract)
    )


def _safe_host_mount_specs(value: object, contract: ContainerInspectionContract) -> bool:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return False
    mount = value[0]
    options = mount.get("BindOptions")
    return bool(
        mount.get("Type") == "bind"
        and mount.get("Source") == contract.coturn_dir
        and mount.get("Target") == COTURN_CONTAINER_DIRECTORY
        and mount.get("ReadOnly") is True
        and mount.get("Consistency") in {None, "", "default"}
        and mount.get("VolumeOptions") is None
        and mount.get("TmpfsOptions") is None
        and isinstance(options, dict)
        and options.get("Propagation") == "rprivate"
        and options.get("NonRecursive") is True
    )


def _safe_mounts(value: object, contract: ContainerInspectionContract) -> bool:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, dict) for item in value)
        or not all(isinstance(item.get("Destination"), str) for item in value)
    ):
        return False
    expected = [
        {
            "Type": "bind",
            "Source": contract.coturn_dir,
            "Destination": COTURN_CONTAINER_DIRECTORY,
            "Mode": "ro",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "tmpfs",
            "Source": "",
            "Destination": "/tmp",
            "Mode": coturn_tmpfs_options(contract.uid, contract.gid),
            "RW": True,
            "Propagation": "",
        },
    ]
    return sorted(value, key=lambda item: item["Destination"]) == expected


__all__ = [
    "CONTAINER_MEMORY",
    "CONTAINER_NANO_CPUS",
    "CONTAINER_PIDS",
    "CONTAINER_SHM",
    "COTURN_CONTAINER_CONFIG",
    "COTURN_CONTAINER_DIRECTORY",
    "COTURN_ENTRYPOINT",
    "ContainerInspectionContract",
    "container_cleanup_running",
    "container_for_use_valid",
    "container_identity_valid",
    "coturn_port_bindings",
    "coturn_tmpfs_options",
]

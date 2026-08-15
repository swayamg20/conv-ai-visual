"""Owned Coturn container command, inspection, and cleanup contracts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Final

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_IMAGE,
    COTURN_PLATFORM,
    COTURN_RELAY_MAX_PORT,
    COTURN_RELAY_MIN_PORT,
    COTURN_TLS_PORT,
)
from scripts.voice_pipecat_e2e_coturn_docker import (
    CoturnDockerError,
    CoturnImageReceipt,
    docker_request,
    one_inspection,
    translate_created_id,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import ValidatedNetwork
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
    require_full_resource_id,
)

COTURN_ENTRYPOINT: Final = "/usr/bin/turnserver"
COTURN_CONTAINER_DIRECTORY: Final = "/run/murmur-coturn"
COTURN_CONTAINER_CONFIG: Final = f"{COTURN_CONTAINER_DIRECTORY}/turnserver.conf"

_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_MEMORY = 67_108_864
_SHM = 4_194_304
_NANO_CPUS = 500_000_000
_PIDS = 64
_AUTHORITY_TOKEN = object()
_VALIDATION_TOKEN = object()


class CoturnDockerContainerError(CoturnDockerError):
    """An owned Coturn container contract is malformed or unsafe."""


@dataclass(frozen=True)
class ContainerPlan:
    identity: RuntimeIdentity = field(repr=False)
    paths: CoturnRuntimePaths = field(repr=False)
    network: ValidatedNetwork = field(repr=False)
    image: CoturnImageReceipt = field(repr=False)
    uid: int = field(repr=False)
    gid: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.network, ValidatedNetwork)
            or not isinstance(self.image, CoturnImageReceipt)
            or self.identity != self.network.authority.plan.identity
            or self.paths != self.network.authority.plan.paths
            or self.paths.contract.run_id != self.identity.run_id
            or isinstance(self.uid, bool)
            or isinstance(self.gid, bool)
            or self.uid != os.geteuid()
            or self.gid != os.getegid()
            or self.uid <= 0
            or self.gid <= 0
        ):
            raise CoturnDockerContainerError("Coturn container plan is invalid")

    @property
    def labels(self) -> dict[str, str]:
        result = dict(self.image.labels)
        result.update(self.network.authority.plan.labels("container"))
        return result


class ContainerCleanupAuthority:
    __slots__ = ("_container_id", "_plan")

    def __init__(self, token: object, container_id: str, plan: ContainerPlan) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Container cleanup authority is factory-owned")
        self._container_id = container_id
        self._plan = plan

    @property
    def container_id(self) -> str:
        return self._container_id

    @property
    def plan(self) -> ContainerPlan:
        return self._plan

    def __repr__(self) -> str:
        return "ContainerCleanupAuthority()"


class ValidatedContainer:
    __slots__ = ("_authority",)

    def __init__(self, token: object, authority: ContainerCleanupAuthority) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Validated container is factory-owned")
        self._authority = authority

    @property
    def authority(self) -> ContainerCleanupAuthority:
        return self._authority

    def __repr__(self) -> str:
        return "ValidatedContainer()"


class ValidatedContainerCleanup:
    __slots__ = ("_authority", "_running")

    def __init__(
        self,
        token: object,
        authority: ContainerCleanupAuthority,
        running: bool,
    ) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Validated container cleanup is factory-owned")
        self._authority = authority
        self._running = running

    @property
    def container_id(self) -> str:
        return self._authority.container_id

    @property
    def running(self) -> bool:
        return self._running

    @property
    def plan(self) -> ContainerPlan:
        return self._authority.plan

    def __repr__(self) -> str:
        return "ValidatedContainerCleanup()"


def establish_container_cleanup_authority(
    *,
    plan: ContainerPlan,
    container_id: object,
    inspection: object,
) -> ContainerCleanupAuthority:
    identifier = translate_created_id(container_id)
    _validate_identity(plan, identifier, inspection)
    return ContainerCleanupAuthority(_AUTHORITY_TOKEN, identifier, plan)


def validate_container_for_start(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainer:
    plan = authority.plan
    container = one_inspection(inspection, "Coturn container inspection")
    _validate_identity(plan, authority.container_id, inspection)
    network_settings = container.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    endpoint = networks.get(plan.identity.network_name) if isinstance(networks, dict) else None
    topology = plan.network.authority.plan.topology
    if (
        container.get("Path") != COTURN_ENTRYPOINT
        or container.get("Args") != ["-c", COTURN_CONTAINER_CONFIG]
        or container.get("Platform") != "linux"
        or not _created_state(container.get("State"))
        or not _safe_config(container.get("Config"), plan)
        or not _safe_host_config(container.get("HostConfig"), plan)
        or not _safe_mounts(container.get("Mounts"), plan)
        or not isinstance(network_settings, dict)
        or network_settings.get("Ports") != _port_bindings()
        or not isinstance(networks, dict)
        or set(networks) != {plan.identity.network_name}
        or not isinstance(endpoint, dict)
        or endpoint.get("NetworkID") != plan.network.authority.network_id
        or endpoint.get("IPAddress") != str(topology.container)
        or endpoint.get("IPPrefixLen") != 29
        or endpoint.get("Gateway") != str(topology.gateway)
        or endpoint.get("GlobalIPv6Address") not in {None, ""}
        or endpoint.get("GlobalIPv6PrefixLen") not in {None, 0}
        or not isinstance(endpoint.get("EndpointID"), str)
        or not _FULL_ID.fullmatch(endpoint["EndpointID"])
        or not isinstance(endpoint.get("MacAddress"), str)
        or not _MAC.fullmatch(endpoint["MacAddress"])
    ):
        raise CoturnDockerContainerError("Coturn container is unsafe to start")
    return ValidatedContainer(_VALIDATION_TOKEN, authority)


def validate_container_cleanup_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerCleanup:
    _validate_identity(authority.plan, authority.container_id, inspection)
    state = one_inspection(inspection, "Coturn container inspection").get("State")
    if not isinstance(state, dict) or not isinstance(state.get("Running"), bool):
        raise CoturnDockerContainerError("Coturn container cleanup state is invalid")
    return ValidatedContainerCleanup(_VALIDATION_TOKEN, authority, state["Running"])


def build_container_create_request(
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> CommandRequest:
    topology = plan.network.authority.plan.topology
    arguments = [
        "container",
        "create",
        "--cidfile",
        os.fspath(plan.paths.cidfile),
        "--name",
        plan.identity.container_name,
        "--platform",
        COTURN_PLATFORM,
        "--pull",
        "never",
        "--network",
        plan.network.authority.network_id,
        "--ip",
        str(topology.container),
        "--user",
        f"{plan.uid}:{plan.gid}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--cgroupns",
        "private",
        "--pids-limit",
        str(_PIDS),
        "--memory",
        str(_MEMORY),
        "--memory-swap",
        str(_MEMORY),
        "--memory-swappiness",
        "0",
        "--cpus",
        "0.5",
        "--shm-size",
        str(_SHM),
        "--ulimit",
        "core=0:0",
        "--ulimit",
        "nofile=1024:1024",
        "--ulimit",
        "nproc=64:64",
        "--tmpfs",
        f"/tmp:{_tmpfs(plan)}",
        "--log-driver",
        "none",
        "--restart",
        "no",
        "--stop-timeout",
        "5",
        "--no-healthcheck",
        "--mount",
        (
            f"type=bind,src={plan.paths.contract.coturn_dir},"
            f"dst={COTURN_CONTAINER_DIRECTORY},readonly,bind-nonrecursive"
        ),
    ]
    for key, value in sorted(plan.network.authority.plan.labels("container").items()):
        arguments.extend(("--label", f"{key}={value}"))
    for port in _published_ports():
        arguments.extend(("--publish", port))
    arguments.extend(
        ("--entrypoint", COTURN_ENTRYPOINT, COTURN_IMAGE, "-c", COTURN_CONTAINER_CONFIG)
    )
    return docker_request(tools, plan.paths, *arguments, timeout_seconds=30.0)


def build_container_inspect_request(
    tools: TrustedHostTools,
    plan: ContainerPlan,
    container_id: str,
) -> CommandRequest:
    require_full_resource_id(container_id)
    return docker_request(tools, plan.paths, "container", "inspect", container_id)


def build_container_name_inspect_request(
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> CommandRequest:
    """Inspect only the exact generated name for missing-cidfile recovery."""

    return docker_request(
        tools,
        plan.paths,
        "container",
        "inspect",
        plan.identity.container_name,
    )


def build_container_start_attached_request(
    tools: TrustedHostTools,
    container: ValidatedContainer,
) -> CommandRequest:
    return docker_request(
        tools,
        container.authority.plan.paths,
        "container",
        "start",
        "--attach",
        "--sig-proxy=false",
        container.authority.container_id,
        timeout_seconds=60.0,
    )


def build_container_stop_request(
    tools: TrustedHostTools,
    target: ValidatedContainerCleanup,
) -> CommandRequest:
    return docker_request(
        tools,
        target.plan.paths,
        "container",
        "stop",
        "--time",
        "5",
        target.container_id,
    )


def build_container_remove_request(
    tools: TrustedHostTools,
    target: ValidatedContainerCleanup,
) -> CommandRequest:
    if target.running:
        raise CoturnDockerContainerError("Running Coturn container removal is refused")
    return docker_request(tools, target.plan.paths, "container", "rm", target.container_id)


def build_container_absence_request(
    tools: TrustedHostTools,
    target: ValidatedContainerCleanup,
) -> CommandRequest:
    """Query all containers for the exact validated full ID after removal."""

    return docker_request(
        tools,
        target.plan.paths,
        "container",
        "ls",
        "--all",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={target.container_id}",
    )


def _validate_identity(plan: ContainerPlan, identifier: str, inspection: object) -> None:
    container = one_inspection(inspection, "Coturn container inspection")
    config = container.get("Config")
    if (
        container.get("Id") != identifier
        or container.get("Name") != f"/{plan.identity.container_name}"
        or container.get("Image") != plan.image.image_id
        or not isinstance(config, dict)
        or config.get("Image") != COTURN_IMAGE
        or config.get("Labels") != plan.labels
    ):
        raise CoturnDockerContainerError("Coturn container ownership is invalid")


def _safe_config(value: object, plan: ContainerPlan) -> bool:
    expected = {
        "User": f"{plan.uid}:{plan.gid}",
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Env": list(plan.image.environment),
        "Cmd": ["-c", COTURN_CONTAINER_CONFIG],
        "Image": COTURN_IMAGE,
        "WorkingDir": plan.image.working_directory,
        "Entrypoint": [COTURN_ENTRYPOINT],
        "Labels": plan.labels,
        "ExposedPorts": {port: {} for port in _port_bindings()},
        "Healthcheck": {"Test": ["NONE"]},
        "StopTimeout": 5,
    }
    return isinstance(value, dict) and all(value.get(key) == item for key, item in expected.items())


def _safe_host_config(value: object, plan: ContainerPlan) -> bool:
    if not isinstance(value, dict):
        return False
    expected = {
        "NetworkMode": plan.network.authority.network_id,
        "PortBindings": _port_bindings(),
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
        "Memory": _MEMORY,
        "MemorySwap": _MEMORY,
        "MemorySwappiness": 0,
        "NanoCpus": _NANO_CPUS,
        "PidsLimit": _PIDS,
        "ShmSize": _SHM,
        "Ulimits": [
            {"Name": "core", "Hard": 0, "Soft": 0},
            {"Name": "nofile", "Hard": 1024, "Soft": 1024},
            {"Name": "nproc", "Hard": 64, "Soft": 64},
        ],
        "Tmpfs": {"/tmp": _tmpfs(plan)},
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
    return (
        all(value.get(key) == item for key, item in expected.items())
        and value.get("IpcMode") in {"private", ""}
        and all(value.get(key) in empty for key in empty_keys)
        and _safe_host_mount_specs(value.get("Mounts"), plan)
    )


def _safe_host_mount_specs(value: object, plan: ContainerPlan) -> bool:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return False
    mount = value[0]
    options = mount.get("BindOptions")
    return bool(
        mount.get("Type") == "bind"
        and mount.get("Source") == os.fspath(plan.paths.contract.coturn_dir)
        and mount.get("Target") == COTURN_CONTAINER_DIRECTORY
        and mount.get("ReadOnly") is True
        and mount.get("Consistency") in {None, "", "default"}
        and mount.get("VolumeOptions") is None
        and mount.get("TmpfsOptions") is None
        and isinstance(options, dict)
        and options.get("Propagation") == "rprivate"
        and options.get("NonRecursive") is True
    )


def _safe_mounts(value: object, plan: ContainerPlan) -> bool:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, dict) for item in value)
        or not all(isinstance(item.get("Destination"), str) for item in value)
    ):
        return False
    expected = [
        {
            "Type": "bind",
            "Source": os.fspath(plan.paths.contract.coturn_dir),
            "Destination": COTURN_CONTAINER_DIRECTORY,
            "Mode": "ro",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "tmpfs",
            "Source": "",
            "Destination": "/tmp",
            "Mode": _tmpfs(plan),
            "RW": True,
            "Propagation": "",
        },
    ]
    return sorted(value, key=lambda item: item["Destination"]) == expected


def _created_state(value: object) -> bool:
    expected = {
        "Status": "created",
        "Running": False,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "Pid": 0,
        "ExitCode": 0,
    }
    return isinstance(value, dict) and all(value.get(key) == item for key, item in expected.items())


def _port_bindings() -> dict[str, list[dict[str, str]]]:
    result = {f"{COTURN_TLS_PORT}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(COTURN_TLS_PORT)}]}
    for port in range(COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT + 1):
        result[f"{port}/udp"] = [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    return result


def _published_ports() -> tuple[str, ...]:
    return (
        f"127.0.0.1:{COTURN_TLS_PORT}:{COTURN_TLS_PORT}/tcp",
        *(
            f"127.0.0.1:{port}:{port}/udp"
            for port in range(COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT + 1)
        ),
    )


def _tmpfs(plan: ContainerPlan) -> str:
    return f"rw,noexec,nosuid,nodev,size=16777216,mode=0700,uid={plan.uid},gid={plan.gid}"


__all__ = [
    "COTURN_CONTAINER_CONFIG",
    "COTURN_CONTAINER_DIRECTORY",
    "COTURN_ENTRYPOINT",
    "ContainerCleanupAuthority",
    "ContainerPlan",
    "CoturnDockerContainerError",
    "ValidatedContainer",
    "ValidatedContainerCleanup",
    "build_container_absence_request",
    "build_container_create_request",
    "build_container_inspect_request",
    "build_container_name_inspect_request",
    "build_container_remove_request",
    "build_container_start_attached_request",
    "build_container_stop_request",
    "establish_container_cleanup_authority",
    "validate_container_cleanup_target",
    "validate_container_for_start",
]

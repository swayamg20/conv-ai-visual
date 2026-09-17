"""Owned Coturn container command, inspection, and cleanup contracts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_IMAGE,
    COTURN_PLATFORM,
    COTURN_RELAY_MAX_PORT,
    COTURN_RELAY_MIN_PORT,
    COTURN_TLS_PORT,
)
from scripts.voice_pipecat_e2e_coturn_container_inspection import (
    CONTAINER_MEMORY,
    CONTAINER_PIDS,
    CONTAINER_SHM,
    COTURN_CONTAINER_CONFIG,
    COTURN_CONTAINER_DIRECTORY,
    COTURN_ENTRYPOINT,
    ContainerInspectionContract,
    container_cleanup_running,
    container_for_use_valid,
    container_identity_valid,
    coturn_tmpfs_options,
)
from scripts.voice_pipecat_e2e_coturn_docker import (
    CoturnDockerError,
    CoturnImageReceipt,
    docker_request,
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
from scripts.voice_pipecat_e2e_coturn_validation_boundary import (
    validate_without_raw_traceback,
)

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


class ValidatedRunningContainer:
    """Factory-owned proof that the exact safe-use container is running."""

    __slots__ = ("_authority",)

    def __init__(self, token: object, authority: ContainerCleanupAuthority) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Validated running container is factory-owned")
        self._authority = authority

    @property
    def authority(self) -> ContainerCleanupAuthority:
        return self._authority

    def __repr__(self) -> str:
        return "ValidatedRunningContainer()"


class _ValidatedCleanupTarget:
    __slots__ = ("_authority",)

    def __init__(self, token: object, authority: ContainerCleanupAuthority) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Container cleanup target is factory-owned")
        self._authority = authority

    @property
    def container_id(self) -> str:
        return self._authority.container_id

    @property
    def plan(self) -> ContainerPlan:
        return self._authority.plan


class ValidatedContainerStop(_ValidatedCleanupTarget):
    """Factory-owned proof that the exact owned container may be stopped."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "ValidatedContainerStop()"


class ValidatedContainerRemoval(_ValidatedCleanupTarget):
    """Factory-owned proof that the exact owned container may be removed."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "ValidatedContainerRemoval()"


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
    try:
        return validate_without_raw_traceback(
            lambda: _establish_container_cleanup_authority(
                plan=plan,
                container_id=container_id,
                inspection=inspection,
            ),
            error_type=CoturnDockerContainerError,
            fallback="Coturn container ownership is invalid",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        plan = container_id = inspection = None


def _establish_container_cleanup_authority(
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
    try:
        return validate_without_raw_traceback(
            lambda: _validate_container_for_start(authority, inspection),
            error_type=CoturnDockerContainerError,
            fallback="Coturn container is unsafe to start",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_container_for_start(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainer:
    _validate_container_for_use(authority, inspection, running=False)
    return ValidatedContainer(_VALIDATION_TOKEN, authority)


def validate_container_running(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedRunningContainer:
    """Revalidate the full safe-use contract after attached start."""

    try:
        return validate_without_raw_traceback(
            lambda: _validate_container_running(authority, inspection),
            error_type=CoturnDockerContainerError,
            fallback="Coturn running container is invalid",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_container_running(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedRunningContainer:
    _validate_container_for_use(authority, inspection, running=True)
    return ValidatedRunningContainer(_VALIDATION_TOKEN, authority)


def _validate_container_for_use(
    authority: ContainerCleanupAuthority,
    inspection: object,
    *,
    running: bool,
) -> None:
    if not container_for_use_valid(
        _inspection_contract(authority.plan, authority.container_id),
        inspection,
        running=running,
    ):
        message = (
            "Coturn running container is invalid"
            if running
            else "Coturn container is unsafe to start"
        )
        raise CoturnDockerContainerError(message)


def validate_container_stop_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerStop:
    try:
        return validate_without_raw_traceback(
            lambda: _validate_container_stop_target(authority, inspection),
            error_type=CoturnDockerContainerError,
            fallback="Coturn running container is invalid",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_container_stop_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerStop:
    _validate_container_for_use(authority, inspection, running=True)
    return ValidatedContainerStop(_VALIDATION_TOKEN, authority)


def validate_container_removal_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerRemoval:
    try:
        return validate_without_raw_traceback(
            lambda: _validate_container_removal_target(authority, inspection),
            error_type=CoturnDockerContainerError,
            fallback="Coturn container cleanup state is invalid",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_container_removal_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerRemoval:
    if _validate_cleanup_state(authority, inspection):
        raise CoturnDockerContainerError("Running Coturn container removal is refused")
    return ValidatedContainerRemoval(_VALIDATION_TOKEN, authority)


def validate_container_cleanup_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerCleanup:
    """Compatibility wrapper; new owners should use the split validators."""

    try:
        return validate_without_raw_traceback(
            lambda: _validate_container_cleanup_target(authority, inspection),
            error_type=CoturnDockerContainerError,
            fallback="Coturn container cleanup state is invalid",
            allowed=_CONTAINER_VALIDATION_ERRORS,
        )
    finally:
        authority = inspection = None


def _validate_container_cleanup_target(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> ValidatedContainerCleanup:
    running = _validate_cleanup_state(authority, inspection)
    if running:
        _validate_container_for_use(authority, inspection, running=True)
    return ValidatedContainerCleanup(_VALIDATION_TOKEN, authority, running)


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
        str(CONTAINER_PIDS),
        "--memory",
        str(CONTAINER_MEMORY),
        "--memory-swap",
        str(CONTAINER_MEMORY),
        "--memory-swappiness",
        "0",
        "--cpus",
        "0.5",
        "--shm-size",
        str(CONTAINER_SHM),
        "--ulimit",
        "core=0:0",
        "--ulimit",
        "nofile=1024:1024",
        "--ulimit",
        "nproc=64:64",
        "--tmpfs",
        f"/tmp:{coturn_tmpfs_options(plan.uid, plan.gid)}",
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
    target: ValidatedContainerStop | ValidatedContainerCleanup,
) -> CommandRequest:
    if isinstance(target, ValidatedContainerCleanup):
        if not target.running:
            raise CoturnDockerContainerError("Coturn container is not running")
    elif not isinstance(target, ValidatedContainerStop):
        raise CoturnDockerContainerError("Coturn container stop target is invalid")
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
    target: ValidatedContainerRemoval | ValidatedContainerCleanup,
) -> CommandRequest:
    if isinstance(target, ValidatedContainerCleanup) and target.running:
        raise CoturnDockerContainerError("Running Coturn container removal is refused")
    if not isinstance(target, (ValidatedContainerRemoval, ValidatedContainerCleanup)):
        raise CoturnDockerContainerError("Coturn container removal target is invalid")
    return docker_request(tools, target.plan.paths, "container", "rm", target.container_id)


def build_container_absence_request(
    tools: TrustedHostTools,
    target: ValidatedContainerRemoval | ValidatedContainerCleanup,
) -> CommandRequest:
    """Query all containers for the exact validated full ID after removal."""

    if isinstance(target, ValidatedContainerCleanup) and target.running:
        raise CoturnDockerContainerError("Running Coturn container removal is refused")
    if not isinstance(target, (ValidatedContainerRemoval, ValidatedContainerCleanup)):
        raise CoturnDockerContainerError("Coturn container removal target is invalid")

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
    if not container_identity_valid(_inspection_contract(plan, identifier), inspection):
        raise CoturnDockerContainerError("Coturn container ownership is invalid")


def _validate_cleanup_state(
    authority: ContainerCleanupAuthority,
    inspection: object,
) -> bool:
    contract = _inspection_contract(authority.plan, authority.container_id)
    if not container_identity_valid(contract, inspection):
        raise CoturnDockerContainerError("Coturn container ownership is invalid")
    running = container_cleanup_running(contract, inspection)
    if running is None:
        raise CoturnDockerContainerError("Coturn container cleanup state is invalid")
    return running


def _inspection_contract(plan: ContainerPlan, identifier: str) -> ContainerInspectionContract:
    topology = plan.network.authority.plan.topology
    return ContainerInspectionContract(
        container_id=identifier,
        container_name=plan.identity.container_name,
        image_id=plan.image.image_id,
        labels=plan.labels,
        environment=plan.image.environment,
        working_directory=plan.image.working_directory,
        network_name=plan.identity.network_name,
        network_id=plan.network.authority.network_id,
        subnet=str(topology.network),
        gateway=str(topology.gateway),
        container_ipv4=str(topology.container),
        coturn_dir=os.fspath(plan.paths.contract.coturn_dir),
        uid=plan.uid,
        gid=plan.gid,
    )


def _published_ports() -> tuple[str, ...]:
    return (
        f"127.0.0.1:{COTURN_TLS_PORT}:{COTURN_TLS_PORT}/tcp",
        *(
            f"127.0.0.1:{port}:{port}/udp"
            for port in range(COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT + 1)
        ),
    )


_CONTAINER_VALIDATION_ERRORS = frozenset(
    {
        "Coturn container ownership is invalid",
        "Coturn container is unsafe to start",
        "Coturn running container is invalid",
        "Coturn container cleanup state is invalid",
        "Running Coturn container removal is refused",
    }
)


__all__ = [
    "COTURN_CONTAINER_CONFIG",
    "COTURN_CONTAINER_DIRECTORY",
    "COTURN_ENTRYPOINT",
    "ContainerCleanupAuthority",
    "ContainerPlan",
    "CoturnDockerContainerError",
    "ValidatedContainer",
    "ValidatedContainerCleanup",
    "ValidatedContainerRemoval",
    "ValidatedContainerStop",
    "ValidatedRunningContainer",
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
    "validate_container_removal_target",
    "validate_container_running",
    "validate_container_stop_target",
]

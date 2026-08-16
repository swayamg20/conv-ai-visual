"""Host identity and injected subprocess primitives for Coturn E2E."""

from __future__ import annotations

import fcntl
import ipaddress
import math
import os
import re
import socket
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from scripts.voice_pipecat_e2e_coturn import CoturnContractPaths

DOCKER_EXECUTABLE: Final = Path("/usr/bin/docker")
OPENSSL_EXECUTABLE: Final = Path("/usr/bin/openssl")
DOCKER_SOCKET: Final = Path("/run/docker.sock")
DOCKER_HOST: Final = "unix:///run/docker.sock"

_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NONCE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_MAX_COMMAND_OUTPUT = 1_048_576
_RUNTIME_ENVIRONMENT = (("LANG", "C"), ("LC_ALL", "C"))
_SIOCGIFADDR = 0x8915
_RECEIPT_TOKEN = object()


class CoturnHostError(RuntimeError):
    """A bounded host contract could not be established safely."""


@dataclass(frozen=True)
class CommandRequest:
    """One no-shell subprocess request whose sensitive fields are redacted."""

    argv: tuple[str, ...] = field(repr=False)
    environment: tuple[tuple[str, str], ...] = field(
        default=_RUNTIME_ENVIRONMENT,
        repr=False,
    )
    stdin: bytes = field(default=b"", repr=False)
    timeout_seconds: float = field(default=10.0, repr=False)
    maximum_output_bytes: int = field(default=_MAX_COMMAND_OUTPUT, repr=False)
    umask: int = field(default=0o077, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.argv) is not tuple
            or not self.argv
            or not all(type(value) is str and value and "\x00" not in value for value in self.argv)
            or type(self.environment) is not tuple
            or self.environment != _RUNTIME_ENVIRONMENT
            or any(
                type(item) is not tuple
                or len(item) != 2
                or any(type(value) is not str for value in item)
                for item in self.environment
            )
            or type(self.stdin) is not bytes
            or type(self.timeout_seconds) is not float
            or not math.isfinite(self.timeout_seconds)
            or not 0.1 <= self.timeout_seconds <= 60.0
            or type(self.maximum_output_bytes) is not int
            or not 1 <= self.maximum_output_bytes <= _MAX_COMMAND_OUTPUT
            or type(self.umask) is not int
            or self.umask != 0o077
        ):
            raise CoturnHostError("Coturn subprocess request is invalid")


@dataclass(frozen=True)
class CommandResult:
    """Bounded subprocess output whose byte content never appears in repr."""

    returncode: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.returncode) is not int
            or type(self.stdout) is not bytes
            or type(self.stderr) is not bytes
            or len(self.stdout) + len(self.stderr) > _MAX_COMMAND_OUTPUT
        ):
            raise CoturnHostError("Coturn subprocess result is invalid")


class AttachedCommandChunk(Protocol):
    """Structural bounded stream value validated again by the runtime."""

    stream: str
    data: bytes


class AttachedCommand(Protocol):
    """Opaque concurrent process; implementations must redact repr."""

    def poll(self) -> int | None: ...

    def read_chunk(self, *, timeout_seconds: float) -> AttachedCommandChunk | None: ...

    @property
    def drained(self) -> bool: ...

    def terminate(self) -> None: ...


class CommandRunner(Protocol):
    """Executor honoring replacement env, no shell, closed fds, and umask."""

    def run(self, request: CommandRequest) -> CommandResult: ...

    def start_attached(self, request: CommandRequest) -> AttachedCommand: ...

    def settle_owned(self) -> bool: ...


def execute_checked(
    runner: CommandRunner,
    request: CommandRequest,
    *,
    failure: str,
) -> CommandResult:
    if type(failure) is not str or not failure or len(failure) > 128:
        runner = request = None  # type: ignore[assignment]
        failure = ""
        raise CoturnHostError("Coturn command failure classification is invalid")
    if type(request) is not CommandRequest:
        runner = request = None  # type: ignore[assignment]
        failure = ""
        raise CoturnHostError("Coturn subprocess request is invalid")
    result: CommandResult | None = None
    control: type[KeyboardInterrupt] | type[SystemExit] | None = None
    exit_code: int | None = None
    try:
        result = _execute_checked_result(runner, request)
        runner = request = None  # type: ignore[assignment]
        return result
    except KeyboardInterrupt:
        result = None
        control = KeyboardInterrupt
    except SystemExit as error:
        result = None
        control = SystemExit
        exit_code = error.code if error.code is None or type(error.code) is int else 1
    except BaseException:
        result = None
    runner = request = None  # type: ignore[assignment]
    message = failure
    failure = ""
    if control is KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    if control is SystemExit:
        raise SystemExit(exit_code) from None
    raise CoturnHostError(message) from None


def _execute_checked_result(
    runner: CommandRunner,
    request: CommandRequest,
) -> CommandResult:
    result = runner.run(request)
    if (
        type(result) is not CommandResult
        or result.returncode != 0
        or len(result.stdout) + len(result.stderr) > request.maximum_output_bytes
    ):
        result = runner = request = None  # type: ignore[assignment]
        raise CoturnHostError("Coturn command result is invalid")
    return result


@dataclass(frozen=True)
class PathMetadata:
    mode: int
    uid: int
    gid: int
    nlink: int
    resolved: Path = field(repr=False)


class HostProbe(Protocol):
    def metadata(self, path: Path) -> PathMetadata: ...

    def unix_peer_uid(self, path: Path) -> int: ...


class LocalHostProbe:
    """Read-only local identity probe; construction has no side effects."""

    def metadata(self, path: Path) -> PathMetadata:
        details = path.stat(follow_symlinks=False)
        return PathMetadata(
            mode=details.st_mode,
            uid=details.st_uid,
            gid=details.st_gid,
            nlink=details.st_nlink,
            resolved=path.resolve(strict=True),
        )

    def unix_peer_uid(self, path: Path) -> int:
        if not hasattr(socket, "SO_PEERCRED"):
            raise CoturnHostError("Docker Unix peer identity is unavailable")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(1.0)
            client.connect(os.fspath(path))
            value = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        except OSError:
            raise CoturnHostError("Docker Unix peer identity is unavailable") from None
        finally:
            client.close()
        _pid, uid, _gid = struct.unpack("3i", value)
        return uid


class TrustedHostTools:
    __slots__ = ("_docker", "_docker_socket", "_openssl")

    def __init__(self, token: object, *, docker: Path, openssl: Path, docker_socket: Path) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Trusted host tools are factory-owned")
        self._docker = docker
        self._openssl = openssl
        self._docker_socket = docker_socket

    @property
    def docker(self) -> Path:
        return self._docker

    @property
    def openssl(self) -> Path:
        return self._openssl

    @property
    def docker_socket(self) -> Path:
        return self._docker_socket

    def __repr__(self) -> str:
        return "TrustedHostTools()"


def validate_trusted_host_tools(
    probe: HostProbe,
    *,
    docker: Path = DOCKER_EXECUTABLE,
    openssl: Path = OPENSSL_EXECUTABLE,
    docker_socket: Path = DOCKER_SOCKET,
) -> TrustedHostTools:
    """Require fixed root-owned tools and a root peer on the local socket."""

    if (docker, openssl, docker_socket) != (
        DOCKER_EXECUTABLE,
        OPENSSL_EXECUTABLE,
        DOCKER_SOCKET,
    ):
        raise CoturnHostError("Coturn host tool paths are invalid")
    try:
        for executable in (docker, openssl):
            details = probe.metadata(executable)
            if (
                details.resolved != executable
                or not stat.S_ISREG(details.mode)
                or details.uid != 0
                or details.nlink != 1
                or stat.S_IMODE(details.mode) & 0o022
                or not stat.S_IMODE(details.mode) & 0o111
            ):
                raise CoturnHostError("Coturn host executable is unsafe")
        for parent in (Path("/usr"), Path("/usr/bin"), Path("/run")):
            details = probe.metadata(parent)
            if (
                details.resolved != parent
                or not stat.S_ISDIR(details.mode)
                or details.uid != 0
                or stat.S_IMODE(details.mode) & 0o022
            ):
                raise CoturnHostError("Coturn host path is unsafe")
        details = probe.metadata(docker_socket)
        if (
            details.resolved != docker_socket
            or not stat.S_ISSOCK(details.mode)
            or details.uid != 0
            or details.nlink != 1
            or stat.S_IMODE(details.mode) & 0o002
            or probe.unix_peer_uid(docker_socket) != 0
        ):
            raise CoturnHostError("Docker Unix socket is unsafe")
    except CoturnHostError:
        raise
    except (OSError, ValueError):
        raise CoturnHostError("Coturn host tools are unavailable") from None
    return TrustedHostTools(
        _RECEIPT_TOKEN,
        docker=docker,
        openssl=openssl,
        docker_socket=docker_socket,
    )


@dataclass(frozen=True)
class CoturnRuntimePaths:
    contract: CoturnContractPaths = field(repr=False)
    control_dir: Path = field(repr=False)
    cidfile: Path = field(repr=False)
    container_absence_receipt: Path = field(repr=False)
    container_receipt: Path = field(repr=False)
    docker_config: Path = field(repr=False)
    network_absence_receipt: Path = field(repr=False)
    network_plan_receipt: Path = field(repr=False)
    network_receipt: Path = field(repr=False)

    def __post_init__(self) -> None:
        directory = self.contract.run_dir / "coturn-control"
        expected = {
            "control_dir": directory,
            "cidfile": directory / "container.cid",
            "container_absence_receipt": directory / "container-absence.json",
            "container_receipt": directory / "container-plan.json",
            "docker_config": directory / "docker-config",
            "network_absence_receipt": directory / "network-absence.json",
            "network_plan_receipt": directory / "network-plan.json",
            "network_receipt": directory / "network-recovery.json",
        }
        for name, path in expected.items():
            if getattr(self, name) != path:
                raise CoturnHostError("Coturn runtime path contract is invalid")
            require_safe_path(path)

    @classmethod
    def for_contract(cls, contract: CoturnContractPaths) -> CoturnRuntimePaths:
        directory = contract.run_dir / "coturn-control"
        return cls(
            contract=contract,
            control_dir=directory,
            cidfile=directory / "container.cid",
            container_absence_receipt=directory / "container-absence.json",
            container_receipt=directory / "container-plan.json",
            docker_config=directory / "docker-config",
            network_absence_receipt=directory / "network-absence.json",
            network_plan_receipt=directory / "network-plan.json",
            network_receipt=directory / "network-recovery.json",
        )


def prepare_runtime_directories(paths: CoturnRuntimePaths) -> None:
    """Require caller-owned exact 0700 run_dir, then create isolated children."""

    require_owned_directory(paths.contract.run_dir)
    if any(
        path.exists() or path.is_symlink()
        for path in (paths.contract.coturn_dir, paths.control_dir)
    ):
        raise CoturnHostError("Coturn runtime directory is unsafe")
    try:
        paths.contract.coturn_dir.mkdir(mode=0o700)
        paths.control_dir.mkdir(mode=0o700)
        paths.docker_config.mkdir(mode=0o700)
    except OSError:
        raise CoturnHostError("Coturn runtime directory is unavailable") from None
    require_owned_directory(paths.contract.coturn_dir)
    require_owned_directory(paths.control_dir)
    require_owned_directory(paths.docker_config)


@dataclass(frozen=True)
class RuntimeIdentity:
    run_id: str = field(repr=False)
    owner_nonce: str = field(repr=False)
    network_name: str = field(repr=False)
    container_name: str = field(repr=False)
    bridge_name: str = field(repr=False)

    @classmethod
    def create(cls, *, run_id: object, owner_nonce: object) -> RuntimeIdentity:
        if (
            type(run_id) is not str
            or not _SAFE_NAME_PATTERN.fullmatch(run_id)
            or type(owner_nonce) is not str
            or not _NONCE_PATTERN.fullmatch(owner_nonce)
        ):
            raise CoturnHostError("Coturn runtime identity is invalid")
        suffix = owner_nonce[:12]
        identity = cls(
            run_id=run_id,
            owner_nonce=owner_nonce,
            network_name=f"murmur-turn-net-{suffix}",
            container_name=f"murmur-turn-{suffix}",
            bridge_name=f"mtn{owner_nonce[:10]}",
        )
        if len(identity.bridge_name.encode("ascii")) > 15:
            raise CoturnHostError("Coturn bridge interface name is invalid")
        return identity

    def labels(self, resource: str) -> dict[str, str]:
        if resource not in {"network", "container"}:
            raise CoturnHostError("Coturn runtime resource is invalid")
        return {
            "com.murmur.voice-e2e.owner": "coturn-checkpoint-b-v1",
            "com.murmur.voice-e2e.nonce": self.owner_nonce,
            "com.murmur.voice-e2e.resource": resource,
        }


@dataclass(frozen=True)
class HostIPv4Route:
    network: ipaddress.IPv4Network = field(repr=False)
    interface: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.network) is not ipaddress.IPv4Network
            or type(self.interface) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", self.interface)
        ):
            raise CoturnHostError("Host IPv4 route is invalid")


class BridgeHostProbe(Protocol):
    def ipv4_routes(self) -> tuple[HostIPv4Route, ...]: ...

    def interface_ipv4(self, interface: str) -> ipaddress.IPv4Address: ...


class LinuxBridgeHostProbe:
    """Linux proc/ioctl probe used by the future executable runner."""

    def ipv4_routes(self) -> tuple[HostIPv4Route, ...]:
        try:
            lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()
            if not lines or lines[0].split()[:3] != ["Iface", "Destination", "Gateway"]:
                raise ValueError
            routes = []
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 8 or not int(fields[3], 16) & 1:
                    continue
                address = ipaddress.IPv4Address(struct.pack("<I", int(fields[1], 16)))
                mask = ipaddress.IPv4Address(struct.pack("<I", int(fields[7], 16)))
                routes.append(
                    HostIPv4Route(
                        ipaddress.IPv4Network((address, mask), strict=True),
                        fields[0],
                    )
                )
        except (OSError, UnicodeError, ValueError):
            raise CoturnHostError("Host IPv4 routes are unavailable") from None
        return tuple(routes)

    def interface_ipv4(self, interface: str) -> ipaddress.IPv4Address:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface):
            raise CoturnHostError("Host bridge address is invalid")
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            response = fcntl.ioctl(
                client.fileno(),
                _SIOCGIFADDR,
                struct.pack("256s", interface.encode("ascii")),
            )
            return ipaddress.IPv4Address(response[20:24])
        except (OSError, UnicodeError, ValueError):
            raise CoturnHostError("Host bridge address is unavailable") from None
        finally:
            client.close()


def require_owned_directory(path: Path) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except OSError:
        raise CoturnHostError("Coturn private directory is unavailable") from None
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise CoturnHostError("Coturn private directory is unsafe")


def require_safe_path(path: Path) -> None:
    value = os.fspath(path)
    if (
        not path.is_absolute()
        or "," in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CoturnHostError("Coturn runtime path is invalid")


def require_full_resource_id(value: object) -> str:
    if type(value) is not str or not _ID_PATTERN.fullmatch(value):
        raise CoturnHostError("Docker resource ID is invalid")
    return value


__all__ = [
    "DOCKER_EXECUTABLE",
    "DOCKER_HOST",
    "DOCKER_SOCKET",
    "OPENSSL_EXECUTABLE",
    "AttachedCommand",
    "AttachedCommandChunk",
    "BridgeHostProbe",
    "CommandRequest",
    "CommandResult",
    "CommandRunner",
    "CoturnHostError",
    "CoturnRuntimePaths",
    "HostIPv4Route",
    "HostProbe",
    "LinuxBridgeHostProbe",
    "LocalHostProbe",
    "PathMetadata",
    "RuntimeIdentity",
    "TrustedHostTools",
    "execute_checked",
    "prepare_runtime_directories",
    "require_full_resource_id",
    "require_owned_directory",
    "require_safe_path",
    "validate_trusted_host_tools",
]

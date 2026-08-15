"""Synthetic host/TLS ownership tests; no external command is executed."""

from __future__ import annotations

import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    CoturnBridgeTopology,
    CoturnContractPaths,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    DOCKER_EXECUTABLE,
    DOCKER_SOCKET,
    OPENSSL_EXECUTABLE,
    CommandRequest,
    CommandResult,
    CoturnHostError,
    CoturnRuntimePaths,
    HostIPv4Route,
    PathMetadata,
    RuntimeIdentity,
    TrustedHostTools,
    execute_checked,
    prepare_runtime_directories,
    validate_trusted_host_tools,
)

NONCE = "ab" * 32
TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.28.44.0/29",
    gateway="172.28.44.1",
    container="172.28.44.2",
)


@dataclass
class QueueRunner:
    values: list[object]
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, CommandResult)
        return value

    def start_attached(self, request: CommandRequest) -> object:
        raise AssertionError("host/TLS tests never start attached processes")


class FakeHostProbe:
    def __init__(self) -> None:
        self.values = {
            DOCKER_EXECUTABLE: PathMetadata(
                stat.S_IFREG | 0o755,
                0,
                0,
                1,
                DOCKER_EXECUTABLE,
            ),
            OPENSSL_EXECUTABLE: PathMetadata(
                stat.S_IFREG | 0o755,
                0,
                0,
                1,
                OPENSSL_EXECUTABLE,
            ),
            Path("/usr"): PathMetadata(stat.S_IFDIR | 0o755, 0, 0, 1, Path("/usr")),
            Path("/usr/bin"): PathMetadata(
                stat.S_IFDIR | 0o755,
                0,
                0,
                1,
                Path("/usr/bin"),
            ),
            Path("/run"): PathMetadata(stat.S_IFDIR | 0o755, 0, 0, 1, Path("/run")),
            DOCKER_SOCKET: PathMetadata(
                stat.S_IFSOCK | 0o660,
                0,
                999,
                1,
                DOCKER_SOCKET,
            ),
        }
        self.peer_uid = 0

    def metadata(self, path: Path) -> PathMetadata:
        return self.values[path]

    def unix_peer_uid(self, path: Path) -> int:
        assert path == DOCKER_SOCKET
        return self.peer_uid


def _tools() -> TrustedHostTools:
    return validate_trusted_host_tools(FakeHostProbe())


def _paths(tmp_path: Path, *, create_coturn: bool = True) -> CoturnRuntimePaths:
    run_dir = tmp_path / "relay-test"
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    contract = CoturnContractPaths.for_run_dir("relay-test", run_dir)
    paths = CoturnRuntimePaths.for_contract(contract)
    if create_coturn:
        contract.coturn_dir.mkdir(mode=0o700)
        contract.coturn_dir.chmod(0o700)
        paths.control_dir.mkdir(mode=0o700)
        paths.control_dir.chmod(0o700)
        paths.docker_config.mkdir(mode=0o700)
        paths.docker_config.chmod(0o700)
    return paths


def _result(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> CommandResult:
    return CommandResult(returncode, stdout, stderr)


def test_command_contract_is_tiny_redacted_and_fixed_failure_does_not_reflect() -> None:
    secret = b"private-key-material"
    request = CommandRequest(argv=("/usr/bin/openssl", "x509"), stdin=secret)
    assert request.environment == (("LANG", "C"), ("LC_ALL", "C"))
    assert request.umask == 0o077
    assert secret.decode() not in repr(request)
    assert "/usr/bin/openssl" not in repr(request)
    with pytest.raises(CoturnHostError, match="request is invalid"):
        CommandRequest(argv=("/bin/true",), environment=(("PATH", "/tmp/poison"),))

    runner = QueueRunner([RuntimeError("private-key-material")])
    with pytest.raises(CoturnHostError, match=r"^fixed failure$") as captured:
        execute_checked(runner, request, failure="fixed failure")
    assert "private-key-material" not in str(captured.value)


@pytest.mark.parametrize(
    ("target", "replacement"),
    [
        (DOCKER_EXECUTABLE, PathMetadata(stat.S_IFLNK | 0o777, 0, 0, 1, DOCKER_EXECUTABLE)),
        (OPENSSL_EXECUTABLE, PathMetadata(stat.S_IFREG | 0o777, 0, 0, 1, OPENSSL_EXECUTABLE)),
        (Path("/usr/bin"), PathMetadata(stat.S_IFDIR | 0o775, 0, 0, 1, Path("/usr/bin"))),
        (DOCKER_SOCKET, PathMetadata(stat.S_IFSOCK | 0o666, 0, 0, 1, DOCKER_SOCKET)),
    ],
)
def test_trusted_host_tools_reject_path_tampering(
    target: Path,
    replacement: PathMetadata,
) -> None:
    probe = FakeHostProbe()
    probe.values[target] = replacement
    with pytest.raises(CoturnHostError, match="unsafe"):
        validate_trusted_host_tools(probe)


def test_trusted_host_tools_require_root_socket_peer_and_factory_owned_receipt() -> None:
    probe = FakeHostProbe()
    probe.peer_uid = 1000
    with pytest.raises(CoturnHostError, match="unsafe"):
        validate_trusted_host_tools(probe)
    with pytest.raises(TypeError, match="factory-owned"):
        TrustedHostTools(  # type: ignore[call-arg]
            object(),
            docker=DOCKER_EXECUTABLE,
            openssl=OPENSSL_EXECUTABLE,
            docker_socket=DOCKER_SOCKET,
        )


def test_paths_identity_and_directory_creation_are_exact_and_redacted(tmp_path: Path) -> None:
    paths = _paths(tmp_path, create_coturn=False)
    assert paths.contract.private_key == paths.contract.coturn_dir / "key.pem"
    prepare_runtime_directories(paths)
    assert stat.S_IMODE(paths.contract.coturn_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.control_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.docker_config.stat().st_mode) == 0o700
    assert paths.control_dir.parent == paths.contract.run_dir
    assert paths.control_dir != paths.contract.coturn_dir
    identity = RuntimeIdentity.create(run_id="relay-test", owner_nonce=NONCE)
    assert identity.bridge_name == f"mtn{NONCE[:10]}"
    assert len(identity.bridge_name) <= 15
    assert identity.labels("network")["com.murmur.voice-e2e.nonce"] == NONCE
    assert NONCE not in repr(identity)
    with pytest.raises(CoturnHostError, match="identity is invalid"):
        RuntimeIdentity.create(run_id="UPPER", owner_nonce=NONCE)
    with pytest.raises(CoturnHostError, match="directory is unsafe"):
        prepare_runtime_directories(paths)


def test_host_route_value_rejects_noncanonical_interface_and_redacts() -> None:
    route = HostIPv4Route(TOPOLOGY.network, "mtn0123456789")
    assert "172.28.44.0" not in repr(route)
    with pytest.raises(CoturnHostError, match="route is invalid"):
        HostIPv4Route(TOPOLOGY.network, "interface-name-is-too-long")

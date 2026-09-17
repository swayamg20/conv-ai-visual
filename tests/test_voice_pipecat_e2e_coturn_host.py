"""Synthetic host/TLS ownership tests; no external command is executed."""

from __future__ import annotations

import inspect
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_host as host_module  # noqa: E402
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
    require_full_resource_id,
    validate_trusted_host_tools,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402

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
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    text_subclass = type("TextSubclass", (str,), {})
    untouched = QueueRunner([_result()])
    with pytest.raises(CoturnHostError, match="classification is invalid"):
        execute_checked(untouched, request, failure=text_subclass("fixed failure"))
    assert untouched.requests == []

    raw = b"traceback-sentinel-malformed-command-result"
    malformed = QueueRunner([_result(stderr=raw, returncode=1)])
    with pytest.raises(CoturnHostError, match=r"^fixed failure$") as rejected:
        execute_checked(malformed, request, failure="fixed failure")
    assert not traceback_contains(rejected.value, raw)


def test_execute_checked_discards_request_runner_context_and_control_graphs() -> None:
    raw = b"traceback-sentinel-execute-boundary"
    request = CommandRequest(argv=("/bin/false",), stdin=raw)

    @dataclass
    class RetainingRunner:
        value: object

        def run(self, _request: CommandRequest) -> CommandResult:
            if isinstance(self.value, BaseException):
                raise self.value
            assert type(self.value) is CommandResult
            return self.value

    for value in (
        RuntimeError(raw.decode("ascii")),
        CommandResult(1, b"", raw),
    ):
        with pytest.raises(CoturnHostError, match=r"^fixed failure$") as captured:
            execute_checked(RetainingRunner(value), request, failure="fixed failure")
        assert captured.value.__context__ is None
        assert not traceback_contains(captured.value, raw)

    for value, expected, code in (
        (KeyboardInterrupt(raw.decode("ascii")), KeyboardInterrupt, None),
        (SystemExit(23), SystemExit, 23),
        (SystemExit(raw.decode("ascii")), SystemExit, 1),
    ):
        with pytest.raises(expected) as captured:
            execute_checked(RetainingRunner(value), request, failure="fixed failure")
        assert captured.value.__context__ is None
        assert not traceback_contains(captured.value, raw)
        if expected is KeyboardInterrupt:
            assert str(captured.value) == ""
        else:
            assert captured.value.code == code  # type: ignore[attr-defined]


def test_execute_checked_rejects_invalid_request_before_dispatch() -> None:
    runner = QueueRunner([_result()])
    with pytest.raises(CoturnHostError, match=r"^Coturn subprocess request is invalid$"):
        execute_checked(
            runner,
            object(),  # type: ignore[arg-type]
            failure="fixed failure",
        )
    assert runner.requests == []


def test_execute_checked_scrubs_control_after_runner_return() -> None:
    raw = b"raw-post-run-validation-control"
    request = CommandRequest(argv=("/bin/true",), stdin=raw)
    runner = QueueRunner([CommandResult(0, raw, b"")])
    lines, first = inspect.getsourcelines(host_module._execute_checked_result)
    target = next(
        first + offset for offset, line in enumerate(lines) if "or result.returncode" in line
    )
    fired = False

    def trace(frame, event: str, _arg):
        nonlocal fired
        if (
            frame.f_code is host_module._execute_checked_result.__code__
            and event == "line"
            and frame.f_lineno == target
            and not fired
        ):
            fired = True
            raise KeyboardInterrupt(raw.decode("ascii"))
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt) as captured:
            execute_checked(runner, request, failure="fixed failure")
    finally:
        sys.settrace(None)

    assert fired
    assert str(captured.value) == ""
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, raw)


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
    assert paths.container_absence_receipt == paths.control_dir / "container-absence.json"
    identity = RuntimeIdentity.create(run_id="relay-test", owner_nonce=NONCE)
    assert identity.bridge_name == f"mtn{NONCE[:10]}"
    assert len(identity.bridge_name) <= 15
    assert identity.labels("network")["com.murmur.voice-e2e.nonce"] == NONCE
    assert NONCE not in repr(identity)
    with pytest.raises(CoturnHostError, match="identity is invalid"):
        RuntimeIdentity.create(run_id="UPPER", owner_nonce=NONCE)
    text_subclass = type("TextSubclass", (str,), {})
    with pytest.raises(CoturnHostError, match="identity is invalid"):
        RuntimeIdentity.create(run_id=text_subclass("relay-test"), owner_nonce=NONCE)
    with pytest.raises(CoturnHostError, match="identity is invalid"):
        RuntimeIdentity.create(run_id="relay-test", owner_nonce=text_subclass(NONCE))
    with pytest.raises(CoturnHostError, match="resource ID is invalid"):
        require_full_resource_id(text_subclass("a" * 64))
    with pytest.raises(CoturnHostError, match="directory is unsafe"):
        prepare_runtime_directories(paths)


def test_host_route_value_rejects_noncanonical_interface_and_redacts() -> None:
    route = HostIPv4Route(TOPOLOGY.network, "mtn0123456789")
    assert "172.28.44.0" not in repr(route)
    with pytest.raises(CoturnHostError, match="route is invalid"):
        HostIPv4Route(TOPOLOGY.network, "interface-name-is-too-long")
    text_subclass = type("TextSubclass", (str,), {})
    with pytest.raises(CoturnHostError, match="route is invalid"):
        HostIPv4Route(TOPOLOGY.network, text_subclass("mtn0123456789"))

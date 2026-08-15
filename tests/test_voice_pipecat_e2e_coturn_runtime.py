"""Synthetic lifecycle composition tests; no subprocess or service is started."""

from __future__ import annotations

import copy
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn_docker import validate_image_inspection  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    ContainerPlan,
    CoturnDockerContainerError,
    establish_container_cleanup_authority,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    CoturnDockerNetworkError,
    establish_network_cleanup_authority,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    CommandRequest,
    CommandResult,
    HostIPv4Route,
    RuntimeIdentity,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    AttachedCoturnProcess,
    CoturnRuntimeError,
    cleanup_owned_container,
    cleanup_owned_network,
    create_owned_container,
    create_owned_network,
    pull_and_validate_image,
    read_private_cidfile,
    recover_container_cleanup_authority,
    recover_network_cleanup_authority,
    start_owned_container_attached,
)
from tests.test_voice_pipecat_e2e_coturn_docker import image_inspection  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    NETWORK_ID,
    NONCE,
    TOPOLOGY,
    network_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    plan as make_network_plan,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402


@dataclass
class FakeAttached:
    result: CommandResult
    state: int | None = None
    collects: list[float] = field(default_factory=list)

    def poll(self) -> int | None:
        return self.state

    def collect(self, *, timeout_seconds: float) -> CommandResult:
        self.collects.append(timeout_seconds)
        return self.result

    def __repr__(self) -> str:
        return "FakeAttached()"


@dataclass
class RuntimeRunner:
    values: list[object]
    attached: object | None = None
    requests: list[CommandRequest] = field(default_factory=list)
    attached_requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        if value == "container-create":
            cidfile = Path(request.argv[request.argv.index("--cidfile") + 1])
            cidfile.write_text(CONTAINER_ID + "\n", encoding="ascii")
            cidfile.chmod(0o600)
            return CommandResult(0, (CONTAINER_ID + "\n").encode(), b"")
        assert isinstance(value, CommandResult)
        return value

    def start_attached(self, request: CommandRequest) -> object:
        self.attached_requests.append(request)
        if isinstance(self.attached, BaseException):
            raise self.attached
        return self.attached


@dataclass
class FakeBridgeProbe:
    before: tuple[HostIPv4Route, ...]
    after: tuple[HostIPv4Route, ...]
    calls: int = 0

    def ipv4_routes(self) -> tuple[HostIPv4Route, ...]:
        self.calls += 1
        return self.before if self.calls == 1 else self.after

    def interface_ipv4(self, interface: str):
        assert (
            interface
            == RuntimeIdentity.create(
                run_id="relay-test",
                owner_nonce=NONCE,
            ).bridge_name
        )
        return TOPOLOGY.gateway


def _json_result(value: object) -> CommandResult:
    return CommandResult(0, json.dumps(value).encode(), b"")


def _owned_network(paths):
    selected = make_network_plan(paths)
    inspection = network_inspection(selected)
    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    return selected, validate_network_for_container(authority, inspection)


def _container_plan(paths):
    network_plan, network = _owned_network(paths)
    return ContainerPlan(
        identity=network_plan.identity,
        paths=paths,
        network=network,
        image=validate_image_inspection(image_inspection()),
        uid=os.geteuid(),
        gid=os.getegid(),
    )


def test_pull_then_inspect_returns_only_validated_pinned_image(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runner = RuntimeRunner(
        [CommandResult(0, b"digest pull progress", b""), _json_result(image_inspection())]
    )
    receipt = pull_and_validate_image(runner=runner, tools=_tools(), paths=paths)
    assert repr(receipt) == "CoturnImageReceipt()"
    assert runner.requests[0].argv[-6:-3] == ("image", "pull", "--quiet")
    assert runner.requests[1].argv[-3:-1] == ("image", "inspect")


def test_network_lifecycle_writes_receipt_before_full_validation_and_binds_host_route(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = make_network_plan(paths)
    owned_route = HostIPv4Route(TOPOLOGY.network, selected.identity.bridge_name)
    probe = FakeBridgeProbe((), (owned_route,))
    runner = RuntimeRunner(
        [
            CommandResult(0, (NETWORK_ID + "\n").encode(), b""),
            _json_result(network_inspection(selected)),
        ]
    )
    owned = create_owned_network(
        runner=runner,
        bridge_probe=probe,
        tools=_tools(),
        plan=selected,
    )
    assert owned.validated.authority is owned.authority
    assert repr(owned) == "OwnedNetwork()"
    assert stat.S_IMODE(paths.network_receipt.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.network_plan_receipt.stat().st_mode) == 0o600
    for receipt_path in (paths.network_receipt, paths.network_plan_receipt):
        raw_receipt = receipt_path.read_text(encoding="ascii")
        assert json.loads(raw_receipt)["run_dir_fingerprint"] == selected.run_dir_fingerprint
        assert os.fspath(paths.contract.run_dir) not in raw_receipt
    assert (
        recover_network_cleanup_authority(
            runner=RuntimeRunner([_json_result(network_inspection(selected))]),
            tools=_tools(),
            plan=selected,
        ).network_id
        == NETWORK_ID
    )
    recovery_receipt = paths.network_receipt.read_bytes()
    tampered_receipt = json.loads(recovery_receipt)
    tampered_receipt["run_dir_fingerprint"] = "0" * 64
    paths.network_receipt.write_text(json.dumps(tampered_receipt), encoding="ascii")
    paths.network_receipt.chmod(0o600)
    with pytest.raises(CoturnRuntimeError, match="network receipt is invalid"):
        recover_network_cleanup_authority(
            runner=RuntimeRunner([]),
            tools=_tools(),
            plan=selected,
        )
    paths.network_receipt.write_bytes(recovery_receipt)
    paths.network_receipt.chmod(0o600)
    paths.network_receipt.unlink()
    name_runner = RuntimeRunner([_json_result(network_inspection(selected))])
    assert (
        recover_network_cleanup_authority(
            runner=name_runner,
            tools=_tools(),
            plan=selected,
        ).network_id
        == NETWORK_ID
    )
    assert name_runner.requests[0].argv[-1] == selected.identity.network_name


def test_failed_network_validation_with_unavailable_cleanup_is_fixed_and_retained(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = make_network_plan(paths)
    inspection = copy.deepcopy(network_inspection(selected))
    inspection[0]["Options"]["com.docker.network.bridge.enable_icc"] = "true"  # type: ignore[index]
    runner = RuntimeRunner(
        [CommandResult(0, (NETWORK_ID + "\n").encode(), b""), _json_result(inspection)]
    )
    with pytest.raises(CoturnRuntimeError, match="retained for explicit recovery"):
        create_owned_network(
            runner=runner,
            bridge_probe=FakeBridgeProbe((), ()),
            tools=_tools(),
            plan=selected,
        )
    assert paths.network_receipt.exists()
    assert paths.network_plan_receipt.exists()
    assert stat.S_IMODE(paths.network_receipt.stat().st_mode) == 0o600


def test_failed_network_validation_recovers_exact_owner_and_removes_resource(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = make_network_plan(paths)
    inspection = copy.deepcopy(network_inspection(selected))
    inspection[0]["Options"]["com.docker.network.bridge.enable_icc"] = "true"  # type: ignore[index]
    runner = RuntimeRunner(
        [
            CommandResult(0, (NETWORK_ID + "\n").encode(), b""),
            _json_result(inspection),
            _json_result(inspection),
            _json_result(inspection),
            CommandResult(0, NETWORK_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn network preparation failed$"):
        create_owned_network(
            runner=runner,
            bridge_probe=FakeBridgeProbe((), ()),
            tools=_tools(),
            plan=selected,
        )
    assert [request.argv[5:7] for request in runner.requests] == [
        ("network", "create"),
        ("network", "inspect"),
        ("network", "inspect"),
        ("network", "inspect"),
        ("network", "rm"),
        ("network", "ls"),
    ]
    assert not paths.network_receipt.exists()
    assert not paths.network_plan_receipt.exists()


def test_uncertain_network_create_failure_recovers_by_exact_owned_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    selected = make_network_plan(paths)
    runner = RuntimeRunner(
        [
            CommandResult(1, b"", b"untrusted daemon error"),
            _json_result(network_inspection(selected)),
            _json_result(network_inspection(selected)),
            CommandResult(0, NETWORK_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn network preparation failed$"):
        create_owned_network(
            runner=runner,
            bridge_probe=FakeBridgeProbe((), ()),
            tools=_tools(),
            plan=selected,
        )
    assert runner.requests[1].argv[-1] == selected.identity.network_name
    assert [request.argv[5:7] for request in runner.requests] == [
        ("network", "create"),
        ("network", "inspect"),
        ("network", "inspect"),
        ("network", "rm"),
        ("network", "ls"),
    ]
    assert not paths.network_plan_receipt.exists()


def test_container_two_phase_stdout_cidfile_inspect_and_start_attached_are_bounded(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    inspection = container_inspection(selected)
    runner = RuntimeRunner(["container-create", _json_result(inspection)])
    owned = create_owned_container(runner=runner, tools=_tools(), plan=selected)
    assert read_private_cidfile(paths) == CONTAINER_ID
    assert stat.S_IMODE(paths.cidfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.container_receipt.stat().st_mode) == 0o600
    raw_receipt = paths.container_receipt.read_text(encoding="ascii")
    assert json.loads(raw_receipt)["run_dir_fingerprint"] == (
        selected.network.authority.plan.run_dir_fingerprint
    )
    assert os.fspath(paths.contract.run_dir) not in raw_receipt
    assert owned.validated.authority is owned.authority

    raw = b"allocation line with ephemeral credentials"
    attached = FakeAttached(CommandResult(0, raw, b""))
    start_runner = RuntimeRunner([], attached=attached)
    handle = start_owned_container_attached(
        runner=start_runner,
        tools=_tools(),
        container=owned.validated,
    )
    assert isinstance(handle, AttachedCoturnProcess)
    assert handle.poll() is None
    assert handle.collect(timeout_seconds=1.0).stdout == raw
    assert raw.decode() not in repr(handle)
    assert start_runner.requests == []
    assert start_runner.attached_requests[0].argv[-5:] == (
        "container",
        "start",
        "--attach",
        "--sig-proxy=false",
        CONTAINER_ID,
    )
    assert all(
        raw not in path.read_bytes()
        for path in paths.contract.coturn_dir.iterdir()
        if path.is_file()
    )


def test_container_receipt_mismatch_refuses_inspect_and_preserves_cidfile(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)

    class MismatchRunner(RuntimeRunner):
        def run(self, request: CommandRequest) -> CommandResult:
            if "create" in request.argv:
                paths.cidfile.write_text("4" * 64 + "\n", encoding="ascii")
                paths.cidfile.chmod(0o600)
                self.requests.append(request)
                return CommandResult(0, (CONTAINER_ID + "\n").encode(), b"")
            raise AssertionError("inspect must not run after receipt mismatch")

    runner = MismatchRunner([])
    with pytest.raises(CoturnRuntimeError, match="retained for explicit recovery"):
        create_owned_container(runner=runner, tools=_tools(), plan=selected)
    assert paths.cidfile.exists() and len(runner.requests) == 1
    assert paths.container_receipt.exists()


def test_failed_container_validation_recovers_exact_owner_and_removes_resource(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    unsafe = container_inspection(selected)
    unsafe[0]["HostConfig"]["Privileged"] = True  # type: ignore[index]
    runner = RuntimeRunner(
        [
            "container-create",
            _json_result(unsafe),
            _json_result(unsafe),
            _json_result(unsafe),
            CommandResult(0, CONTAINER_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn container preparation failed$"):
        create_owned_container(runner=runner, tools=_tools(), plan=selected)
    assert [request.argv[5:7] for request in runner.requests] == [
        ("container", "create"),
        ("container", "inspect"),
        ("container", "inspect"),
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]
    assert not paths.cidfile.exists()
    assert not paths.container_receipt.exists()


def test_uncertain_container_create_failure_recovers_by_exact_owned_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    inspection = container_inspection(selected)
    runner = RuntimeRunner(
        [
            CommandResult(1, b"", b"untrusted daemon error"),
            _json_result(inspection),
            _json_result(inspection),
            CommandResult(0, CONTAINER_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn container preparation failed$"):
        create_owned_container(runner=runner, tools=_tools(), plan=selected)
    assert runner.requests[1].argv[-1] == selected.identity.container_name
    assert [request.argv[5:7] for request in runner.requests] == [
        ("container", "create"),
        ("container", "inspect"),
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]
    assert not paths.container_receipt.exists()


def test_container_recovery_uses_exact_plan_receipt_then_cid_or_owned_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    create_owned_container(
        runner=RuntimeRunner(["container-create", _json_result(container_inspection(selected))]),
        tools=_tools(),
        plan=selected,
    )
    recovered = recover_container_cleanup_authority(
        runner=RuntimeRunner([_json_result(container_inspection(selected))]),
        tools=_tools(),
        plan=selected,
    )
    assert recovered.container_id == CONTAINER_ID
    paths.cidfile.unlink()
    by_name_runner = RuntimeRunner([_json_result(container_inspection(selected))])
    by_name = recover_container_cleanup_authority(
        runner=by_name_runner,
        tools=_tools(),
        plan=selected,
    )
    assert by_name.container_id == CONTAINER_ID
    assert by_name_runner.requests[0].argv[-1] == selected.identity.container_name

    receipt = json.loads(paths.container_receipt.read_text())
    for key, value in (
        ("network_id", "9" * 64),
        ("run_dir_fingerprint", "0" * 64),
    ):
        tampered = {**receipt, key: value}
        paths.container_receipt.chmod(0o600)
        paths.container_receipt.write_text(json.dumps(tampered), encoding="ascii")
        paths.container_receipt.chmod(0o600)
        with pytest.raises(CoturnRuntimeError, match="plan receipt is invalid"):
            recover_container_cleanup_authority(
                runner=RuntimeRunner([]),
                tools=_tools(),
                plan=selected,
            )


def test_recovery_refuses_symlinked_optional_id_receipts_before_inspection(
    tmp_path: Path,
) -> None:
    network_root = tmp_path / "network"
    network_root.mkdir()
    network_paths = _paths(network_root)
    selected_network = make_network_plan(network_paths)
    create_owned_network(
        runner=RuntimeRunner(
            [
                CommandResult(0, (NETWORK_ID + "\n").encode(), b""),
                _json_result(network_inspection(selected_network)),
            ]
        ),
        bridge_probe=FakeBridgeProbe(
            (),
            (HostIPv4Route(TOPOLOGY.network, selected_network.identity.bridge_name),),
        ),
        tools=_tools(),
        plan=selected_network,
    )
    network_paths.network_receipt.unlink()
    network_paths.network_receipt.symlink_to(network_paths.network_plan_receipt)
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn network receipt is invalid$"):
        recover_network_cleanup_authority(
            runner=RuntimeRunner([]),
            tools=_tools(),
            plan=selected_network,
        )

    container_root = tmp_path / "container"
    container_root.mkdir()
    container_paths = _paths(container_root)
    selected_container = _container_plan(container_paths)
    create_owned_container(
        runner=RuntimeRunner(
            ["container-create", _json_result(container_inspection(selected_container))]
        ),
        tools=_tools(),
        plan=selected_container,
    )
    container_paths.cidfile.unlink()
    container_paths.cidfile.symlink_to(container_paths.container_receipt)
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn cidfile is invalid$"):
        recover_container_cleanup_authority(
            runner=RuntimeRunner([]),
            tools=_tools(),
            plan=selected_container,
        )


def test_cleanup_order_reinspects_stops_then_removes_container_before_network(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    created = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=created,
    )
    paths.cidfile.write_text(CONTAINER_ID + "\n", encoding="ascii")
    paths.cidfile.chmod(0o600)
    running = container_inspection(selected, running=True)
    runner = RuntimeRunner(
        [
            _json_result(running),
            CommandResult(0, CONTAINER_ID.encode(), b""),
            _json_result(created),
            CommandResult(0, CONTAINER_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    cleanup_owned_container(
        runner=runner,
        tools=_tools(),
        authority=authority,
    )
    operations = [request.argv[5:7] for request in runner.requests]
    assert operations == [
        ("container", "inspect"),
        ("container", "stop"),
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]
    assert not paths.cidfile.exists()

    network_selected = make_network_plan(paths)
    network_authority = establish_network_cleanup_authority(
        plan=network_selected,
        network_id=NETWORK_ID,
        inspection=network_inspection(network_selected),
    )
    paths.network_receipt.write_text(
        json.dumps(
            {
                "network_id": NETWORK_ID,
                "nonce": network_selected.identity.owner_nonce,
                "owner": "coturn-checkpoint-b-v1",
                "run_dir_fingerprint": network_selected.run_dir_fingerprint,
                "schema_version": 2,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    paths.network_receipt.chmod(0o600)
    network_runner = RuntimeRunner(
        [
            _json_result(network_inspection(network_selected)),
            CommandResult(0, NETWORK_ID.encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    cleanup_owned_network(
        runner=network_runner,
        tools=_tools(),
        authority=network_authority,
    )
    assert [request.argv[5:7] for request in network_runner.requests] == [
        ("network", "inspect"),
        ("network", "rm"),
        ("network", "ls"),
    ]
    assert not paths.network_receipt.exists()


def test_container_cross_root_recovery_and_cleanup_authority_are_path_domain_bound(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _paths(first_root)
    second = _paths(second_root)
    assert first.contract.run_id == second.contract.run_id
    assert first != second

    selected = _container_plan(first)
    other = _container_plan(second)
    inspection = container_inspection(selected)
    assert selected.identity == other.identity
    assert selected.identity.container_name == other.identity.container_name
    assert (
        selected.network.authority.plan.run_dir_fingerprint
        != other.network.authority.plan.run_dir_fingerprint
    )
    with pytest.raises(CoturnDockerContainerError, match="ownership is invalid"):
        establish_container_cleanup_authority(
            plan=other,
            container_id=CONTAINER_ID,
            inspection=inspection,
        )

    recovery_runner = RuntimeRunner(
        [CommandResult(1, b"", b"untrusted daemon error"), _json_result(inspection)]
    )
    with pytest.raises(CoturnRuntimeError, match="retained for explicit recovery"):
        create_owned_container(runner=recovery_runner, tools=_tools(), plan=other)
    assert [request.argv[5:7] for request in recovery_runner.requests] == [
        ("container", "create"),
        ("container", "inspect"),
    ]
    assert recovery_runner.requests[-1].argv[-1] == other.identity.container_name
    second_receipt = second.container_receipt.read_bytes()
    assert json.loads(second_receipt)["run_dir_fingerprint"] == (
        other.network.authority.plan.run_dir_fingerprint
    )
    assert os.fsencode(second.contract.run_dir) not in second_receipt

    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    for path, value in (
        (first.cidfile, CONTAINER_ID + "\n"),
        (first.container_receipt, "first receipt\n"),
        (second.cidfile, "second cid\n"),
    ):
        path.write_text(value, encoding="ascii")
        path.chmod(0o600)

    cleanup_owned_container(
        runner=RuntimeRunner(
            [
                _json_result(inspection),
                CommandResult(0, CONTAINER_ID.encode(), b""),
                CommandResult(0, b"", b""),
            ]
        ),
        tools=_tools(),
        authority=authority,
    )
    assert not first.cidfile.exists()
    assert not first.container_receipt.exists()
    assert second.cidfile.read_text(encoding="ascii") == "second cid\n"
    assert second.container_receipt.read_bytes() == second_receipt


def test_network_cross_root_recovery_and_cleanup_authority_are_path_domain_bound(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _paths(first_root)
    second = _paths(second_root)
    assert first.contract.run_id == second.contract.run_id
    assert first != second

    selected = make_network_plan(first)
    other = make_network_plan(second)
    inspection = network_inspection(selected)
    assert selected.identity == other.identity
    assert selected.identity.network_name == other.identity.network_name
    assert selected.run_dir_fingerprint != other.run_dir_fingerprint
    with pytest.raises(CoturnDockerNetworkError, match="ownership is invalid"):
        establish_network_cleanup_authority(
            plan=other,
            network_id=NETWORK_ID,
            inspection=inspection,
        )

    recovery_runner = RuntimeRunner(
        [CommandResult(1, b"", b"untrusted daemon error"), _json_result(inspection)]
    )
    with pytest.raises(CoturnRuntimeError, match="retained for explicit recovery"):
        create_owned_network(
            runner=recovery_runner,
            bridge_probe=FakeBridgeProbe((), ()),
            tools=_tools(),
            plan=other,
        )
    assert [request.argv[5:7] for request in recovery_runner.requests] == [
        ("network", "create"),
        ("network", "inspect"),
    ]
    assert recovery_runner.requests[-1].argv[-1] == other.identity.network_name
    second_plan_receipt = second.network_plan_receipt.read_bytes()
    assert json.loads(second_plan_receipt)["run_dir_fingerprint"] == other.run_dir_fingerprint
    assert os.fsencode(second.contract.run_dir) not in second_plan_receipt

    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    for path, value in (
        (first.network_receipt, "first receipt\n"),
        (first.network_plan_receipt, "first plan\n"),
        (second.network_receipt, "second receipt\n"),
    ):
        path.write_text(value, encoding="ascii")
        path.chmod(0o600)

    cleanup_owned_network(
        runner=RuntimeRunner(
            [
                _json_result(inspection),
                CommandResult(0, NETWORK_ID.encode(), b""),
                CommandResult(0, b"", b""),
            ]
        ),
        tools=_tools(),
        authority=authority,
    )
    assert not first.network_receipt.exists()
    assert not first.network_plan_receipt.exists()
    assert second.network_receipt.read_text(encoding="ascii") == "second receipt\n"
    assert second.network_plan_receipt.read_bytes() == second_plan_receipt


def test_container_cleanup_preserves_receipts_when_exact_id_still_exists(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    inspection = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    paths.cidfile.write_text(CONTAINER_ID + "\n", encoding="ascii")
    paths.cidfile.chmod(0o600)
    paths.container_receipt.write_text("private recovery receipt\n", encoding="ascii")
    paths.container_receipt.chmod(0o600)
    runner = RuntimeRunner(
        [
            _json_result(inspection),
            CommandResult(0, CONTAINER_ID.encode(), b""),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
        ]
    )
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn container removal was not confirmed$",
    ):
        cleanup_owned_container(
            runner=runner,
            tools=_tools(),
            authority=authority,
        )
    assert paths.cidfile.exists()
    assert paths.container_receipt.exists()
    assert runner.requests[-1].argv[-1] == f"id={CONTAINER_ID}"


def test_network_cleanup_preserves_receipts_when_absence_query_fails(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = make_network_plan(paths)
    inspection = network_inspection(selected)
    authority = establish_network_cleanup_authority(
        plan=selected,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    paths.network_receipt.write_text("private resource receipt\n", encoding="ascii")
    paths.network_receipt.chmod(0o600)
    paths.network_plan_receipt.write_text("private plan receipt\n", encoding="ascii")
    paths.network_plan_receipt.chmod(0o600)
    raw = b"untrusted daemon query failure"
    runner = RuntimeRunner(
        [
            _json_result(inspection),
            CommandResult(0, NETWORK_ID.encode(), b""),
            CommandResult(1, b"", raw),
        ]
    )
    with pytest.raises(RuntimeError, match=r"^Coturn network absence check failed$") as captured:
        cleanup_owned_network(
            runner=runner,
            tools=_tools(),
            authority=authority,
        )
    assert raw.decode() not in str(captured.value)
    assert paths.network_receipt.exists()
    assert paths.network_plan_receipt.exists()
    assert runner.requests[-1].argv[-1] == f"id={NETWORK_ID}"


def test_create_preserves_system_exceptions_when_best_effort_recovery_fails(
    tmp_path: Path,
) -> None:
    network_root = tmp_path / "network"
    network_root.mkdir()
    network_paths = _paths(network_root)
    network_plan = make_network_plan(network_paths)
    with pytest.raises(KeyboardInterrupt, match="network interrupt"):
        create_owned_network(
            runner=RuntimeRunner(
                [
                    KeyboardInterrupt("network interrupt"),
                    CommandResult(1, b"", b"cleanup failed"),
                ]
            ),
            bridge_probe=FakeBridgeProbe((), ()),
            tools=_tools(),
            plan=network_plan,
        )
    assert network_paths.network_plan_receipt.exists()

    container_root = tmp_path / "container"
    container_root.mkdir()
    container_paths = _paths(container_root)
    with pytest.raises(SystemExit) as captured:
        create_owned_container(
            runner=RuntimeRunner([SystemExit(91), KeyboardInterrupt("cleanup interrupt")]),
            tools=_tools(),
            plan=_container_plan(container_paths),
        )
    assert captured.value.code == 91
    assert container_paths.container_receipt.exists()


def test_cleanup_refuses_ownership_or_attachment_tamper_before_destructive_command(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    original = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=original,
    )
    tampered = copy.deepcopy(original)
    tampered[0]["Config"]["Labels"] = {"foreign": "true"}  # type: ignore[index]
    runner = RuntimeRunner([_json_result(tampered)])
    with pytest.raises(RuntimeError, match="ownership is invalid"):
        cleanup_owned_container(runner=runner, tools=_tools(), authority=authority)
    assert len(runner.requests) == 1

    network_selected = make_network_plan(paths)
    network_authority = establish_network_cleanup_authority(
        plan=network_selected,
        network_id=NETWORK_ID,
        inspection=network_inspection(network_selected),
    )
    attached = network_inspection(
        network_selected,
        containers={"9" * 64: {"Name": "foreign"}},
    )
    network_runner = RuntimeRunner([_json_result(attached)])
    with pytest.raises(RuntimeError, match="unknown attachments"):
        cleanup_owned_network(
            runner=network_runner,
            tools=_tools(),
            authority=network_authority,
        )
    assert len(network_runner.requests) == 1


def test_attached_failures_and_receipt_tamper_are_fixed_and_secret_free(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    selected = _container_plan(paths)
    inspection = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    validated = validate_container_for_start(authority, inspection)
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached start failed$") as captured:
        start_owned_container_attached(
            runner=RuntimeRunner([], attached=RuntimeError("raw-secret")),
            tools=_tools(),
            container=validated,
        )
    assert "raw-secret" not in str(captured.value)

    network_selected = make_network_plan(paths)
    create_owned_network(
        runner=RuntimeRunner(
            [
                CommandResult(0, (NETWORK_ID + "\n").encode(), b""),
                _json_result(network_inspection(network_selected)),
            ]
        ),
        bridge_probe=FakeBridgeProbe(
            (),
            (HostIPv4Route(TOPOLOGY.network, network_selected.identity.bridge_name),),
        ),
        tools=_tools(),
        plan=network_selected,
    )
    paths.network_receipt.write_text(bad := '{"nonce":"raw-secret"}\n', encoding="ascii")
    paths.network_receipt.chmod(0o600)
    with pytest.raises(
        CoturnRuntimeError, match=r"^Coturn network receipt is invalid$"
    ) as receipt_error:
        recover_network_cleanup_authority(
            runner=RuntimeRunner([]),
            tools=_tools(),
            plan=network_selected,
        )
    assert bad.strip() not in str(receipt_error.value)

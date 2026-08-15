"""Synthetic Coturn container ownership tests; no Docker is executed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import COTURN_IMAGE  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker import (  # noqa: E402
    RUN_DIR_FINGERPRINT_LABEL,
    validate_image_inspection,
)
from scripts.voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    COTURN_CONTAINER_CONFIG,
    COTURN_CONTAINER_DIRECTORY,
    COTURN_ENTRYPOINT,
    ContainerCleanupAuthority,
    ContainerPlan,
    CoturnDockerContainerError,
    build_container_absence_request,
    build_container_create_request,
    build_container_inspect_request,
    build_container_remove_request,
    build_container_start_attached_request,
    build_container_stop_request,
    establish_container_cleanup_authority,
    validate_container_cleanup_target,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    establish_network_cleanup_authority,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import RuntimeIdentity  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker import IMAGE_ID, image_inspection  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    NETWORK_ID,
    network_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    plan as network_plan,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402

CONTAINER_ID = "2" * 64
ENDPOINT_ID = "3" * 64


def container_plan(tmp_path: Path) -> ContainerPlan:
    paths = _paths(tmp_path)
    selected_network = network_plan(paths)
    inspection = network_inspection(selected_network)
    network_authority = establish_network_cleanup_authority(
        plan=selected_network,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    network = validate_network_for_container(network_authority, inspection)
    return ContainerPlan(
        identity=selected_network.identity,
        paths=paths,
        network=network,
        image=validate_image_inspection(image_inspection()),
        uid=os.geteuid(),
        gid=os.getegid(),
    )


def port_bindings() -> dict[str, list[dict[str, str]]]:
    result = {"5349/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5349"}]}
    for port in range(49160, 49170):
        result[f"{port}/udp"] = [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    return result


def container_inspection(selected: ContainerPlan, *, running: bool = False):
    ports = port_bindings()
    tmpfs = f"rw,noexec,nosuid,nodev,size=16777216,mode=0700,uid={selected.uid},gid={selected.gid}"
    state = {
        "Status": "running" if running else "created",
        "Running": running,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "Pid": 4242 if running else 0,
        "ExitCode": 0,
    }
    return [
        {
            "Id": CONTAINER_ID,
            "Name": f"/{selected.identity.container_name}",
            "Image": IMAGE_ID,
            "Path": COTURN_ENTRYPOINT,
            "Args": ["-c", COTURN_CONTAINER_CONFIG],
            "Platform": "linux",
            "State": state,
            "Config": {
                "User": f"{selected.uid}:{selected.gid}",
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "OpenStdin": False,
                "StdinOnce": False,
                "Env": list(selected.image.environment),
                "Cmd": ["-c", COTURN_CONTAINER_CONFIG],
                "Image": COTURN_IMAGE,
                "WorkingDir": selected.image.working_directory,
                "Entrypoint": [COTURN_ENTRYPOINT],
                "Labels": selected.labels,
                "ExposedPorts": {port: {} for port in ports},
                "Healthcheck": {"Test": ["NONE"]},
                "StopTimeout": 5,
            },
            "HostConfig": {
                "NetworkMode": NETWORK_ID,
                "PortBindings": ports,
                "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                "AutoRemove": False,
                "CapAdd": None,
                "Binds": None,
                "CapDrop": ["ALL"],
                "CgroupnsMode": "private",
                "IpcMode": "private",
                "PidMode": "",
                "UTSMode": "",
                "UsernsMode": "",
                "Privileged": False,
                "PublishAllPorts": False,
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "LogConfig": {"Type": "none", "Config": {}},
                "Memory": 67_108_864,
                "MemorySwap": 67_108_864,
                "MemorySwappiness": 0,
                "NanoCpus": 500_000_000,
                "PidsLimit": 64,
                "ShmSize": 4_194_304,
                "Ulimits": [
                    {"Name": "core", "Hard": 0, "Soft": 0},
                    {"Name": "nofile", "Hard": 1024, "Soft": 1024},
                    {"Name": "nproc", "Hard": 64, "Soft": 64},
                ],
                "Tmpfs": {"/tmp": tmpfs},
                "Devices": [],
                "DeviceRequests": None,
                "GroupAdd": None,
                "Dns": [],
                "DnsOptions": [],
                "DnsSearch": [],
                "ExtraHosts": None,
                "Links": None,
                "VolumesFrom": None,
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": os.fspath(selected.paths.contract.coturn_dir),
                        "Target": COTURN_CONTAINER_DIRECTORY,
                        "ReadOnly": True,
                        "Consistency": "default",
                        "BindOptions": {
                            "Propagation": "rprivate",
                            "NonRecursive": True,
                        },
                        "VolumeOptions": None,
                        "TmpfsOptions": None,
                    }
                ],
            },
            "Mounts": [
                {
                    "Type": "tmpfs",
                    "Source": "",
                    "Destination": "/tmp",
                    "Mode": tmpfs,
                    "RW": True,
                    "Propagation": "",
                },
                {
                    "Type": "bind",
                    "Source": os.fspath(selected.paths.contract.coturn_dir),
                    "Destination": COTURN_CONTAINER_DIRECTORY,
                    "Mode": "ro",
                    "RW": False,
                    "Propagation": "rprivate",
                },
            ],
            "NetworkSettings": {
                "Ports": ports,
                "Networks": {
                    selected.identity.network_name: {
                        "NetworkID": NETWORK_ID,
                        "EndpointID": ENDPOINT_ID,
                        "Gateway": "172.28.44.1",
                        "IPAddress": "172.28.44.2",
                        "IPPrefixLen": 29,
                        "GlobalIPv6Address": "",
                        "GlobalIPv6PrefixLen": 0,
                        "MacAddress": "02:42:ac:1c:2c:02",
                    }
                },
            },
        }
    ]


def test_container_create_command_is_exact_nonroot_readonly_bounded_and_loopback_only(
    tmp_path: Path,
) -> None:
    selected = container_plan(tmp_path)
    request = build_container_create_request(_tools(), selected)
    argv = request.argv
    assert argv[5:7] == ("container", "create")
    assert argv[argv.index("--user") + 1] == f"{os.geteuid()}:{os.getegid()}"
    assert all(flag in argv for flag in ("--read-only", "--cap-drop", "--security-opt"))
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges=true"
    assert argv[argv.index("--log-driver") + 1] == "none"
    assert argv[argv.index("--restart") + 1] == "no"
    assert argv[argv.index("--network") + 1] == NETWORK_ID
    assert argv[argv.index("--ip") + 1] == "172.28.44.2"
    fingerprint = selected.network.authority.plan.run_dir_fingerprint
    assert selected.labels[RUN_DIR_FINGERPRINT_LABEL] == fingerprint
    assert f"{RUN_DIR_FINGERPRINT_LABEL}={fingerprint}" in argv
    assert argv[argv.index("--mount") + 1] == (
        f"type=bind,src={selected.paths.contract.coturn_dir},"
        "dst=/run/murmur-coturn,readonly,bind-nonrecursive"
    )
    assert selected.paths.control_dir != selected.paths.contract.coturn_dir
    assert os.fspath(selected.paths.control_dir) not in argv[argv.index("--mount") + 1]
    published = [argv[index + 1] for index, item in enumerate(argv) if item == "--publish"]
    assert len(published) == 11
    assert published[0] == "127.0.0.1:5349:5349/tcp"
    assert published[-1] == "127.0.0.1:49169:49169/udp"
    assert all(item.startswith("127.0.0.1:") for item in published)
    assert argv[-5:] == (
        "--entrypoint",
        COTURN_ENTRYPOINT,
        COTURN_IMAGE,
        "-c",
        COTURN_CONTAINER_CONFIG,
    )
    assert request.umask == 0o077


def test_container_plan_rejects_mixed_network_identity_before_command_build(tmp_path: Path) -> None:
    selected = container_plan(tmp_path)
    with pytest.raises(CoturnDockerContainerError, match="container plan is invalid"):
        ContainerPlan(
            identity=RuntimeIdentity.create(run_id="relay-test", owner_nonce="cd" * 32),
            paths=selected.paths,
            network=selected.network,
            image=selected.image,
            uid=selected.uid,
            gid=selected.gid,
        )


def test_container_plan_rejects_same_identity_from_a_different_run_directory(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    selected = container_plan(first_root)
    with pytest.raises(CoturnDockerContainerError, match="container plan is invalid"):
        ContainerPlan(
            identity=selected.identity,
            paths=_paths(second_root),
            network=selected.network,
            image=selected.image,
            uid=selected.uid,
            gid=selected.gid,
        )


def test_container_full_start_validation_and_commands_are_factory_owned_and_redacted(
    tmp_path: Path,
) -> None:
    selected = container_plan(tmp_path)
    inspection = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID + "\n",
        inspection=inspection,
    )
    validated = validate_container_for_start(authority, inspection)
    cleanup = validate_container_cleanup_target(authority, inspection)
    assert repr(authority) == "ContainerCleanupAuthority()"
    assert CONTAINER_ID not in repr(authority)
    assert (
        build_container_inspect_request(_tools(), selected, CONTAINER_ID).argv[-1] == CONTAINER_ID
    )
    assert build_container_start_attached_request(_tools(), validated).argv[-5:] == (
        "container",
        "start",
        "--attach",
        "--sig-proxy=false",
        CONTAINER_ID,
    )
    assert build_container_remove_request(_tools(), cleanup).argv[-2:] == (
        "rm",
        CONTAINER_ID,
    )
    assert build_container_absence_request(_tools(), cleanup).argv[-7:] == (
        "container",
        "ls",
        "--all",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={CONTAINER_ID}",
    )
    with pytest.raises(TypeError, match="factory-owned"):
        ContainerCleanupAuthority(object(), CONTAINER_ID, selected)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ((0, "Path"), "/bin/sh"),
        ((0, "Config", "User"), "0:0"),
        ((0, "Config", "Entrypoint"), ["/bin/sh"]),
        ((0, "Config", "Healthcheck"), None),
        ((0, "Config", "StopTimeout"), 0),
        ((0, "HostConfig", "Privileged"), True),
        ((0, "HostConfig", "CapDrop"), []),
        ((0, "HostConfig", "ReadonlyRootfs"), False),
        ((0, "HostConfig", "LogConfig"), {"Type": "json-file", "Config": {}}),
        ((0, "HostConfig", "Memory"), 0),
        ((0, "HostConfig", "Mounts", 0, "BindOptions", "NonRecursive"), False),
        ((0, "NetworkSettings", "Ports", "5349/tcp", 0, "HostIp"), "0.0.0.0"),
        (
            (0, "NetworkSettings", "Networks", "murmur-turn-net-abababababab", "IPAddress"),
            "172.28.44.3",
        ),
        ((0, "Mounts", 1, "RW"), True),
    ],
)
def test_container_start_validation_rejects_command_security_resource_port_and_mount_tamper(
    tmp_path: Path,
    path: tuple[object, ...],
    value: object,
) -> None:
    selected = container_plan(tmp_path)
    inspection = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    target: object = inspection
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(CoturnDockerContainerError, match="unsafe to start"):
        validate_container_for_start(authority, inspection)


@pytest.mark.parametrize(
    "mounts",
    [
        [{"Type": "bind"}],
        [{"Destination": 1}],
        [{"Destination": {"private": "shape"}}],
    ],
)
def test_container_start_validation_rejects_malformed_mount_shape_with_fixed_error(
    tmp_path: Path,
    mounts: object,
) -> None:
    selected = container_plan(tmp_path)
    inspection = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    inspection[0]["Mounts"] = mounts
    with pytest.raises(CoturnDockerContainerError, match="Coturn container is unsafe to start"):
        validate_container_for_start(authority, inspection)


def test_cleanup_uses_identity_not_full_safe_use_and_refuses_running_remove(tmp_path: Path) -> None:
    selected = container_plan(tmp_path)
    original = container_inspection(selected)
    authority = establish_container_cleanup_authority(
        plan=selected,
        container_id=CONTAINER_ID,
        inspection=original,
    )
    damaged = container_inspection(selected, running=True)
    damaged[0]["HostConfig"]["Privileged"] = True  # type: ignore[index]
    target = validate_container_cleanup_target(authority, damaged)
    assert target.running is True
    assert build_container_stop_request(_tools(), target).argv[-1] == CONTAINER_ID
    with pytest.raises(CoturnDockerContainerError, match="removal is refused"):
        build_container_remove_request(_tools(), target)
    damaged[0]["Config"]["Labels"] = {"foreign": "true"}  # type: ignore[index]
    with pytest.raises(CoturnDockerContainerError, match="ownership is invalid"):
        validate_container_cleanup_target(authority, damaged)

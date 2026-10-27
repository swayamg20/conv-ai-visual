"""Synthetic Docker client/daemon/image contracts; no Docker is executed."""

from __future__ import annotations

import copy
import json
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
    CoturnDockerError,
    CoturnImageReceipt,
    build_docker_info_request,
    build_image_inspect_request,
    build_image_pull_request,
    decode_inspection_result,
    translate_created_id,
    validate_docker_info_result,
    validate_image_inspection,
)
from scripts.voice_pipecat_e2e_coturn_host import CommandResult  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402

IMAGE_ID = "sha256:" + "1" * 64


def image_inspection() -> list[dict[str, object]]:
    return [
        {
            "Id": IMAGE_ID,
            "RepoDigests": [COTURN_IMAGE],
            "Os": "linux",
            "Architecture": "amd64",
            "Variant": "",
            "Config": {
                "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"],
                "Labels": {"org.opencontainers.image.title": "coturn"},
                "WorkingDir": "",
            },
        }
    ]


def test_all_docker_requests_use_fixed_local_socket_config_env_digest_and_platform(
    tmp_path: Path,
) -> None:
    tools = _tools()
    paths = _paths(tmp_path)
    info = build_docker_info_request(tools, paths)
    pull = build_image_pull_request(tools, paths)
    inspect = build_image_inspect_request(tools, paths)
    prefix = (
        "/usr/bin/docker",
        "--config",
        os.fspath(paths.docker_config),
        "--host",
        "unix:///run/docker.sock",
    )
    assert all(request.argv[:5] == prefix for request in (info, pull, inspect))
    assert pull.argv[5:] == (
        "image",
        "pull",
        "--quiet",
        "--platform",
        "linux/amd64",
        COTURN_IMAGE,
    )
    assert inspect.argv[5:] == ("image", "inspect", COTURN_IMAGE)
    assert all(
        request.environment == (("LANG", "C"), ("LC_ALL", "C")) for request in (info, pull, inspect)
    )
    assert all("DOCKER_CONTEXT" not in repr(request) for request in (info, pull, inspect))


def test_daemon_identity_requires_linux_amd64_rootful_shape() -> None:
    valid = {
        "OSType": "linux",
        "Architecture": "x86_64",
        "OperatingSystem": "Ubuntu 24.04 LTS",
        "Driver": "overlay2",
        "SecurityOptions": [],
    }
    validate_docker_info_result(CommandResult(0, json.dumps(valid).encode(), b""))
    for mutation in (
        {**valid, "OSType": "darwin"},
        {**valid, "Architecture": "arm64"},
        {**valid, "OperatingSystem": "Docker Desktop"},
        {**valid, "Driver": ""},
        {**valid, "SecurityOptions": ["name=rootless"]},
    ):
        with pytest.raises(CoturnDockerError, match="daemon identity is invalid"):
            validate_docker_info_result(CommandResult(0, json.dumps(mutation).encode(), b""))


def test_image_receipt_is_exact_factory_owned_and_redacted() -> None:
    receipt = validate_image_inspection(image_inspection())
    assert receipt.image_id == IMAGE_ID
    assert receipt.environment == ("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",)
    assert repr(receipt) == "CoturnImageReceipt()"
    assert IMAGE_ID not in repr(receipt)
    with pytest.raises(TypeError, match="factory-owned"):
        CoturnImageReceipt(  # type: ignore[call-arg]
            object(),
            image_id=IMAGE_ID,
            environment=(),
            labels=(),
            working_directory="",
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ((0, "RepoDigests"), ["coturn/coturn@sha256:" + "2" * 64]),
        ((0, "Os"), "windows"),
        ((0, "Architecture"), "arm64"),
        ((0, "Config", "Env"), ["SSLKEYLOGFILE=/tmp/leak"]),
        ((0, "Config", "Env"), ["OPENSSL_CONF=/tmp/poison"]),
        ((0, "Config", "Env"), ["LD_PRELOAD=/tmp/poison.so"]),
        (
            (0, "Config", "Labels"),
            {"com.murmur.voice-e2e.owner": "forged"},
        ),
        (
            (0, "Config", "Labels"),
            {RUN_DIR_FINGERPRINT_LABEL: "0" * 64},
        ),
    ],
)
def test_image_inspection_rejects_digest_platform_env_and_reserved_label_tamper(
    path: tuple[object, ...],
    value: object,
) -> None:
    inspection = copy.deepcopy(image_inspection())
    target: object = inspection
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(CoturnDockerError, match="image inspection is invalid"):
        validate_image_inspection(inspection)


def test_json_and_id_decoders_are_bounded_exact_and_do_not_reflect() -> None:
    identifier = "a" * 64
    assert translate_created_id((identifier + "\n").encode()) == identifier
    for invalid in (identifier[:-1], "A" * 64, identifier + "\nextra", b"\xff"):
        with pytest.raises(CoturnDockerError, match=r"^Docker resource ID is invalid$"):
            translate_created_id(invalid)
    secret = b'{"secret":"do-not-reflect"}'
    with pytest.raises(
        CoturnDockerError, match=r"^Coturn image inspection is invalid$"
    ) as captured:
        decode_inspection_result(CommandResult(0, secret, b"stderr"), label="image")
    assert "do-not-reflect" not in str(captured.value)

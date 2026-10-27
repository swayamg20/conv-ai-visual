"""Docker client, daemon, and pinned-image contracts for Coturn E2E."""

from __future__ import annotations

import json
import os
import re

from scripts.voice_pipecat_e2e_coturn import COTURN_IMAGE, COTURN_PLATFORM
from scripts.voice_pipecat_e2e_coturn_host import (
    DOCKER_HOST,
    CommandRequest,
    CommandResult,
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
    require_full_resource_id,
)

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_VALIDATION_TOKEN = object()
RUN_DIR_FINGERPRINT_LABEL = "com.murmur.voice-e2e.run-dir-fingerprint"


class CoturnDockerError(RuntimeError):
    """A Docker client, daemon, or image contract is unsafe."""


class CoturnImageReceipt:
    __slots__ = ("_environment", "_image_id", "_labels", "_working_directory")

    def __init__(
        self,
        token: object,
        *,
        image_id: str,
        environment: tuple[str, ...],
        labels: tuple[tuple[str, str], ...],
        working_directory: str,
    ) -> None:
        if token is not _VALIDATION_TOKEN:
            raise TypeError("Coturn image receipt is factory-owned")
        self._image_id = image_id
        self._environment = environment
        self._labels = labels
        self._working_directory = working_directory

    @property
    def image_id(self) -> str:
        return self._image_id

    @property
    def environment(self) -> tuple[str, ...]:
        return self._environment

    @property
    def labels(self) -> tuple[tuple[str, str], ...]:
        return self._labels

    @property
    def working_directory(self) -> str:
        return self._working_directory

    def __repr__(self) -> str:
        return "CoturnImageReceipt()"


def validate_image_inspection(value: object) -> CoturnImageReceipt:
    image = one_inspection(value, "Coturn image inspection")
    config = image.get("Config")
    if not isinstance(config, dict):
        raise CoturnDockerError("Coturn image inspection is invalid")
    environment = config.get("Env")
    labels = config.get("Labels")
    image_id = image.get("Id")
    reserved = set(
        RuntimeIdentity.create(run_id="label-check", owner_nonce="0" * 64).labels("container")
    ) | {RUN_DIR_FINGERPRINT_LABEL}
    if (
        not isinstance(image_id, str)
        or not _IMAGE_ID.fullmatch(image_id)
        or image.get("RepoDigests") != [COTURN_IMAGE]
        or image.get("Os") != "linux"
        or image.get("Architecture") != "amd64"
        or image.get("Variant") not in {None, ""}
        or not isinstance(environment, list)
        or not all(_safe_environment(item) for item in environment)
        or (
            labels is not None
            and (
                not isinstance(labels, dict)
                or not all(
                    isinstance(key, str) and isinstance(item, str) and key not in reserved
                    for key, item in labels.items()
                )
            )
        )
        or not isinstance(config.get("WorkingDir", ""), str)
    ):
        raise CoturnDockerError("Coturn image inspection is invalid")
    return CoturnImageReceipt(
        _VALIDATION_TOKEN,
        image_id=image_id,
        environment=tuple(environment),
        labels=tuple(sorted((labels or {}).items())),
        working_directory=config.get("WorkingDir", ""),
    )


def build_docker_info_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CommandRequest:
    return docker_request(tools, paths, "info", "--format", "{{json .}}")


def validate_docker_info_result(result: CommandResult) -> None:
    value = decode_inspection_result(result, label="daemon")
    operating_system = value.get("OperatingSystem") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("OSType") != "linux"
        or value.get("Architecture") not in {"x86_64", "amd64"}
        or not isinstance(operating_system, str)
        or not operating_system
        or "docker desktop" in operating_system.casefold()
        or not isinstance(value.get("Driver"), str)
        or not value["Driver"]
        or not isinstance(value.get("SecurityOptions"), list)
        or any("rootless" in str(item).casefold() for item in value["SecurityOptions"])
    ):
        raise CoturnDockerError("Docker daemon identity is invalid")


def build_image_pull_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CommandRequest:
    return docker_request(
        tools,
        paths,
        "image",
        "pull",
        "--quiet",
        "--platform",
        COTURN_PLATFORM,
        COTURN_IMAGE,
        timeout_seconds=60.0,
    )


def build_image_inspect_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CommandRequest:
    return docker_request(tools, paths, "image", "inspect", COTURN_IMAGE)


def docker_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    *arguments: str,
    timeout_seconds: float = 10.0,
) -> CommandRequest:
    """Build every Docker command with a fixed config and local Unix host."""

    return CommandRequest(
        argv=(
            os.fspath(tools.docker),
            "--config",
            os.fspath(paths.docker_config),
            "--host",
            DOCKER_HOST,
            *arguments,
        ),
        timeout_seconds=timeout_seconds,
    )


def decode_inspection_result(result: CommandResult, *, label: str) -> object:
    if label not in {"daemon", "image", "network", "container"}:
        raise CoturnDockerError("Coturn inspection label is invalid")
    if result.returncode != 0 or result.stderr or not result.stdout:
        raise CoturnDockerError(f"Coturn {label} inspection is invalid")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise CoturnDockerError(f"Coturn {label} inspection is invalid") from None


def translate_created_id(value: object) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeError:
            raise CoturnDockerError("Docker resource ID is invalid") from None
    if not isinstance(value, str):
        raise CoturnDockerError("Docker resource ID is invalid")
    identifier = value[:-1] if value.endswith("\n") else value
    try:
        return require_full_resource_id(identifier)
    except RuntimeError:
        raise CoturnDockerError("Docker resource ID is invalid") from None


def one_inspection(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise CoturnDockerError(f"{label} is invalid")
    return value[0]


def _safe_environment(value: object) -> bool:
    if not isinstance(value, str) or "=" not in value or "\x00" in value:
        return False
    name, _, item = value.partition("=")
    upper = name.upper()
    forbidden = (
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "DOCKER_",
        "SSLKEYLOGFILE",
        "OPENSSL_",
        "LD_",
        "DYLD_",
    )
    return bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        and not any(marker in upper for marker in forbidden)
        and "\r" not in item
        and "\n" not in item
    )


__all__ = [
    "RUN_DIR_FINGERPRINT_LABEL",
    "CoturnDockerError",
    "CoturnImageReceipt",
    "build_docker_info_request",
    "build_image_inspect_request",
    "build_image_pull_request",
    "decode_inspection_result",
    "docker_request",
    "one_inspection",
    "translate_created_id",
    "validate_docker_info_result",
    "validate_image_inspection",
]

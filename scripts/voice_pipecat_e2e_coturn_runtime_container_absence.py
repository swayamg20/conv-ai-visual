"""Durable exact-ID container absence ownership and finalization."""

from __future__ import annotations

import json
import threading

from scripts.voice_pipecat_e2e_coturn_docker import docker_request
from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerPlan
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandRunner,
    CoturnHostError,
    CoturnRuntimePaths,
    TrustedHostTools,
    execute_checked,
    require_full_resource_id,
)
from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    CoturnDirectorySyncCleanupRequired,
    sync_owned_directory,
)
from scripts.voice_pipecat_e2e_coturn_runtime_private_cleanup import (
    _RuntimePrivateCleanupCapture,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import CoturnRuntimeError
from scripts.voice_pipecat_e2e_coturn_tls import read_owned_file, write_owned_file_exclusive

_ABSENCE_TOKEN = object()
_OWNER = "coturn-checkpoint-b-v1"


class ContainerAbsenceReceipt:
    """Opaque durable proof that one exact full container ID is absent."""

    __slots__ = ("_container_id", "_finalized", "_lock", "_plan")

    def __init__(self, token: object, *, plan: ContainerPlan, container_id: str) -> None:
        if token is not _ABSENCE_TOKEN:
            raise TypeError("Coturn container-removal receipt is factory-owned")
        self._plan = plan
        self._container_id = container_id
        self._finalized = False
        self._lock = threading.Lock()

    @property
    def finalization_complete(self) -> bool:
        with self._lock:
            return self._finalized

    def _matches_paths(self, paths: object) -> bool:
        return self._plan.paths == paths

    def _matches_container(self, paths: object, container_id: object) -> bool:
        return bool(
            self._plan.paths == paths
            and type(container_id) is str
            and self._container_id == container_id
        )

    def __repr__(self) -> str:
        return "ContainerAbsenceReceipt()"


RemovedContainerReceipt = ContainerAbsenceReceipt


def recover_container_absence(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> ContainerAbsenceReceipt:
    """Revalidate a durable marker with one fresh exact full-ID absence query."""

    receipt: ContainerAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    try:
        marker = _read_container_absence_marker(plan)
        container_id = marker.get("full_id")
        if type(container_id) is not str:
            raise CoturnRuntimeError("Coturn container absence marker is invalid")
        receipt = _confirm_and_persist_container_absence(
            runner=runner,
            tools=tools,
            plan=plan,
            container_id=container_id,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        capture.capture_error(error)
    runner = tools = plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if receipt is None:
        raise CoturnRuntimeError("Coturn container absence recovery failed") from None
    return receipt


def _recover_container_absence_from_id(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
    container_id: str,
) -> ContainerAbsenceReceipt:
    """Commit a marker after a valid private full ID outlives its inspect target."""

    receipt: ContainerAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    try:
        receipt = _confirm_and_persist_container_absence(
            runner=runner,
            tools=tools,
            plan=plan,
            container_id=require_full_resource_id(container_id),
        )
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        capture.capture_error(error)
    runner = tools = plan = None  # type: ignore[assignment]
    container_id = ""
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if receipt is None:
        raise CoturnRuntimeError("Coturn container absence recovery failed") from None
    return receipt


def _confirm_and_persist_container_absence(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
    container_id: str,
) -> ContainerAbsenceReceipt:
    result = execute_checked(
        runner,
        _container_absence_request(tools, plan.paths, container_id),
        failure="Coturn container absence recovery failed",
    )
    if result.stdout != b"" or result.stderr != b"":
        raise CoturnRuntimeError("Coturn container absence recovery failed")
    result = None
    return _persist_container_absence(plan=plan, container_id=container_id)


def _persist_container_absence(
    *,
    plan: ContainerPlan,
    container_id: str,
) -> ContainerAbsenceReceipt:
    container_id = require_full_resource_id(container_id)
    path = plan.paths.container_absence_receipt
    if path.exists() or path.is_symlink():
        marker = _read_container_absence_marker(plan)
        if marker.get("full_id") != container_id:
            raise CoturnRuntimeError("Coturn container absence marker is invalid")
    else:
        value = _container_absence_marker_value(plan, container_id=container_id)
        write_owned_file_exclusive(path, value, mode=0o600, maximum=768)
        value = b""
    sync_owned_directory(plan.paths.control_dir)
    return ContainerAbsenceReceipt(
        _ABSENCE_TOKEN,
        plan=plan,
        container_id=container_id,
    )


def finalize_container_absence(receipt: ContainerAbsenceReceipt) -> None:
    """Durably remove private recovery receipts, then the absence marker last."""

    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    failed = False
    try:
        if type(receipt) is not ContainerAbsenceReceipt:
            raise CoturnRuntimeError("Coturn container absence finalization failed")
        with receipt._lock:
            if receipt._finalized:
                return
            paths = receipt._plan.paths
            if container_absence_marker_exists(paths):
                marker = _read_container_absence_marker(receipt._plan)
                if marker.get("full_id") != receipt._container_id:
                    raise CoturnRuntimeError("Coturn container absence marker is invalid")
                paths.cidfile.unlink(missing_ok=True)
                paths.container_receipt.unlink(missing_ok=True)
                sync_owned_directory(paths.control_dir)
                paths.container_absence_receipt.unlink()
                sync_owned_directory(paths.control_dir)
            elif paths.cidfile.exists() or paths.cidfile.is_symlink():
                raise CoturnRuntimeError("Coturn container absence finalization failed")
            elif paths.container_receipt.exists() or paths.container_receipt.is_symlink():
                raise CoturnRuntimeError("Coturn container absence finalization failed")
            else:
                sync_owned_directory(paths.control_dir)
            receipt._finalized = True
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        failed = not capture.capture_error(error)
    receipt = None  # type: ignore[assignment]
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if failed:
        raise CoturnRuntimeError("Coturn container absence finalization failed") from None


def container_absence_marker_exists(paths: CoturnRuntimePaths) -> bool:
    return paths.container_absence_receipt.exists() or paths.container_absence_receipt.is_symlink()


def _read_container_absence_marker(plan: ContainerPlan) -> dict[str, object]:
    value = read_owned_file(
        plan.paths.container_absence_receipt,
        exact_mode=0o600,
        maximum=768,
    )
    try:
        decoded = json.loads(value.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        decoded = None
    value = b""
    expected = {
        "full_id",
        "nonce",
        "owner",
        "run_dir_fingerprint",
        "schema_version",
        "state",
    }
    if (
        type(decoded) is not dict
        or set(decoded) != expected
        or decoded.get("nonce") != plan.identity.owner_nonce
        or decoded.get("owner") != _OWNER
        or decoded.get("run_dir_fingerprint") != plan.network.authority.plan.run_dir_fingerprint
        or decoded.get("schema_version") != 1
        or decoded.get("state") != "absent"
    ):
        raise CoturnRuntimeError("Coturn container absence marker is invalid") from None
    try:
        require_full_resource_id(decoded.get("full_id"))
    except CoturnHostError:
        raise CoturnRuntimeError("Coturn container absence marker is invalid") from None
    return decoded


def _container_absence_marker_value(plan: ContainerPlan, *, container_id: str) -> bytes:
    return (
        json.dumps(
            {
                "full_id": container_id,
                "nonce": plan.identity.owner_nonce,
                "owner": _OWNER,
                "run_dir_fingerprint": plan.network.authority.plan.run_dir_fingerprint,
                "schema_version": 1,
                "state": "absent",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def _container_absence_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    container_id: str,
) -> CommandRequest:
    return docker_request(
        tools,
        paths,
        "container",
        "ls",
        "--all",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={require_full_resource_id(container_id)}",
    )


__all__ = [
    "ContainerAbsenceReceipt",
    "RemovedContainerReceipt",
    "container_absence_marker_exists",
    "finalize_container_absence",
    "recover_container_absence",
]

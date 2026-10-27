"""Sanitized private receipt persistence for the Coturn container owner."""

from __future__ import annotations

import json

from scripts.voice_pipecat_e2e_coturn_docker import translate_created_id
from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerPlan
from scripts.voice_pipecat_e2e_coturn_host import CoturnRuntimePaths
from scripts.voice_pipecat_e2e_coturn_runtime_directory import sync_owned_directory
from scripts.voice_pipecat_e2e_coturn_runtime_private_cleanup import (
    _RuntimePrivateCleanupCapture,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    read_owned_file,
    write_owned_file_exclusive,
)

_OWNER = "coturn-checkpoint-b-v1"


def _container_creation_paths(plan: ContainerPlan) -> CoturnRuntimePaths:
    """Validate the no-receipt precondition without leaking path failures."""

    paths: CoturnRuntimePaths | None = None
    receipt_exists = False
    control: ControlSignal | None = None
    try:
        if type(plan) is not ContainerPlan:
            raise CoturnRuntimeError("Coturn container plan is invalid")
        paths = plan.paths
        receipt_exists = any(
            path.exists() or path.is_symlink()
            for path in (
                paths.cidfile,
                paths.container_absence_receipt,
                paths.container_receipt,
            )
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        paths = None
    plan = None  # type: ignore[assignment]
    if control is not None:
        paths = None
        raise_control(control)
    if paths is None:
        raise CoturnRuntimeError("Coturn container plan is invalid") from None
    if receipt_exists:
        paths = None
        raise CoturnRuntimeError("Coturn container receipt already exists")
    return paths


def read_private_cidfile(paths: CoturnRuntimePaths) -> str:
    """Read one exact private cidfile without leaking file cleanup authority."""

    capture = _RuntimePrivateCleanupCapture()
    identifier: str | None = None
    value = b""
    try:
        if type(paths) is not CoturnRuntimePaths:
            raise CoturnRuntimeError("Coturn cidfile is invalid")
        value = read_owned_file(paths.cidfile, exact_mode=0o600, maximum=65)
        identifier = translate_created_id(value.decode("ascii"))
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except BaseException as error:
        capture.capture_error(error)
    value = b""
    paths = None  # type: ignore[assignment]
    capture.raise_captured()
    if identifier is None:
        raise CoturnRuntimeError("Coturn cidfile is invalid") from None
    return identifier


def _write_container_plan_receipt(plan: ContainerPlan) -> None:
    """Write the path-bound recovery plan while retaining private cleanup failures."""

    capture = _RuntimePrivateCleanupCapture()
    failed = False
    value = b""
    try:
        if type(plan) is not ContainerPlan:
            raise CoturnRuntimeError("Coturn container plan receipt is invalid")
        value = _container_plan_value(plan)
        write_owned_file_exclusive(
            plan.paths.container_receipt,
            value,
            mode=0o600,
            maximum=768,
        )
        value = b""
        sync_owned_directory(plan.paths.control_dir)
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except BaseException as error:
        failed = not capture.capture_error(error)
    value = b""
    plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if failed:
        raise CoturnRuntimeError("Coturn container plan receipt is invalid") from None


def _read_container_plan_receipt(plan: ContainerPlan) -> None:
    """Validate one exact path-bound recovery plan and scrub read failures."""

    capture = _RuntimePrivateCleanupCapture()
    failed = False
    value = b""
    decoded: object = None
    expected: object = None
    try:
        if type(plan) is not ContainerPlan:
            raise CoturnRuntimeError("Coturn container plan receipt is invalid")
        value = read_owned_file(
            plan.paths.container_receipt,
            exact_mode=0o600,
            maximum=768,
        )
        decoded = json.loads(value.decode("ascii"))
        expected = _container_plan_mapping(plan)
        if decoded != expected:
            raise CoturnRuntimeError("Coturn container plan receipt is invalid")
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except BaseException as error:
        failed = not capture.capture_error(error)
    value = b""
    decoded = expected = None
    plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if failed:
        raise CoturnRuntimeError("Coturn container plan receipt is invalid") from None


def _container_plan_value(plan: ContainerPlan) -> bytes:
    return (
        json.dumps(
            _container_plan_mapping(plan),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def _container_plan_mapping(plan: ContainerPlan) -> dict[str, object]:
    return {
        "container_name": plan.identity.container_name,
        "image_id": plan.image.image_id,
        "network_id": plan.network.authority.network_id,
        "nonce": plan.identity.owner_nonce,
        "owner": _OWNER,
        "run_dir_fingerprint": plan.network.authority.plan.run_dir_fingerprint,
        "schema_version": 2,
    }


__all__ = ["read_private_cidfile"]

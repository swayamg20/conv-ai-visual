"""Transactional network removal, persisted absence, and finalization."""

from __future__ import annotations

import json
import threading

from scripts.voice_pipecat_e2e_coturn_docker import (
    decode_inspection_result,
    docker_request,
    translate_created_id,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkCleanupAuthority,
    NetworkPlan,
    build_network_absence_request,
    build_network_inspect_request,
    build_network_name_inspect_request,
    build_network_remove_request,
    establish_network_cleanup_authority,
    validate_network_cleanup_target,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
    execute_checked,
    require_full_resource_id,
)
from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    CoturnDirectorySyncCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    sync_owned_directory as _sync_control_directory,
)
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

_ABSENCE_TOKEN = object()
_OWNER = "coturn-checkpoint-b-v1"
_RECOVERY_FAILURES = frozenset(
    {
        "Coturn network absence recovery failed",
        "Coturn network plan receipt is invalid",
        "Coturn network receipt is invalid",
        "Coturn network recovery inspection failed",
        "Coturn network recovery inspection is invalid",
    }
)


def _network_creation_paths(plan: NetworkPlan) -> CoturnRuntimePaths:
    """Validate the no-receipt precondition without leaking path failures."""

    paths: CoturnRuntimePaths | None = None
    receipt_exists = False
    control: ControlSignal | None = None
    try:
        if type(plan) is not NetworkPlan:
            raise CoturnRuntimeError("Coturn network plan is invalid")
        paths = plan.paths
        receipt_exists = any(
            path.exists() or path.is_symlink()
            for path in (
                paths.network_absence_receipt,
                paths.network_plan_receipt,
                paths.network_receipt,
            )
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        paths = None
    plan = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if paths is None:
        raise CoturnRuntimeError("Coturn network plan is invalid") from None
    if receipt_exists:
        raise CoturnRuntimeError("Coturn network receipt already exists")
    return paths


class NetworkAbsenceReceipt:
    """Opaque proof that one exact full network ID is absent."""

    __slots__ = ("_finalized", "_lock", "_network_id", "_plan")

    def __init__(self, token: object, *, plan: NetworkPlan, network_id: str) -> None:
        if token is not _ABSENCE_TOKEN:
            raise TypeError("Coturn network-absence receipt is factory-owned")
        self._plan = plan
        self._network_id = network_id
        self._finalized = False
        self._lock = threading.Lock()

    @property
    def finalization_complete(self) -> bool:
        with self._lock:
            return self._finalized

    def __copy__(self) -> NetworkAbsenceReceipt:
        raise TypeError("Coturn network absence receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> NetworkAbsenceReceipt:
        raise TypeError("Coturn network absence receipt cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn network absence receipt cannot be serialized")

    def __repr__(self) -> str:
        return "NetworkAbsenceReceipt()"


def recover_network_cleanup_authority(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> NetworkCleanupAuthority | NetworkAbsenceReceipt:
    """Recover exact ownership or reconcile a persisted exact-ID absence."""

    recovered: NetworkCleanupAuthority | NetworkAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    opaque_failure: CoturnDirectorySyncCleanupRequired | None = None
    message = "Coturn network recovery failed"
    try:
        recovered = _recover_network_cleanup_authority(runner, tools, plan)
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        opaque_failure = error
    except BaseException as error:
        if not capture.capture_error(error):
            arguments = error.args
            if (
                type(arguments) is tuple
                and len(arguments) == 1
                and type(arguments[0]) is str
                and arguments[0] in _RECOVERY_FAILURES
            ):
                message = arguments[0]
            arguments = None
    runner = tools = plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if opaque_failure is not None:
        failure = opaque_failure
        opaque_failure = None
        raise failure from None
    if type(recovered) not in {NetworkCleanupAuthority, NetworkAbsenceReceipt}:
        recovered = None
        raise CoturnRuntimeError(message) from None
    return recovered


def _recover_network_cleanup_authority(
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> NetworkCleanupAuthority | NetworkAbsenceReceipt:
    paths = plan.paths
    if network_absence_marker_exists(paths):
        return recover_network_absence(runner=runner, tools=tools, plan=plan)
    _read_network_plan_receipt(paths, plan=plan)
    if paths.network_receipt.is_symlink():
        raise CoturnRuntimeError("Coturn network receipt is invalid")
    if paths.network_receipt.exists():
        network_id = _read_network_receipt(paths, plan=plan)
        inspect_missing = False
        try:
            inspected = execute_checked(
                runner,
                build_network_inspect_request(tools, plan, network_id),
                failure="Coturn network recovery inspection failed",
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            inspected = None
            inspect_missing = True
        if inspect_missing:
            confirmed_network_id = _read_network_receipt(paths, plan=plan)
            if confirmed_network_id != network_id:
                raise CoturnRuntimeError("Coturn network receipt is invalid")
            return _recover_network_absence_from_id(
                runner=runner,
                tools=tools,
                plan=plan,
                network_id=confirmed_network_id,
            )
    else:
        inspected = execute_checked(
            runner,
            build_network_name_inspect_request(tools, plan),
            failure="Coturn network recovery inspection failed",
        )
    inspection = decode_inspection_result(inspected, label="network")
    item = inspection[0] if isinstance(inspection, list) and inspection else None
    if not isinstance(item, dict):
        raise CoturnRuntimeError("Coturn network recovery inspection is invalid")
    network_id = translate_created_id(item.get("Id"))
    return establish_network_cleanup_authority(
        plan=plan,
        network_id=network_id,
        inspection=inspection,
    )


def cleanup_owned_network_transaction(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: NetworkCleanupAuthority,
) -> NetworkAbsenceReceipt:
    """Remove or reconcile one exact network, then persist confirmed absence."""

    receipt: NetworkAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    opaque_failure: CoturnDirectorySyncCleanupRequired | None = None
    inspected: object = None
    target: object = None
    removed: object = None
    absence: object = None
    try:
        if type(authority) is not NetworkCleanupAuthority:
            raise CoturnRuntimeError("Coturn network cleanup failed")
        inspect_failed = False
        try:
            inspected = execute_checked(
                runner,
                build_network_inspect_request(
                    tools,
                    authority.plan,
                    authority.network_id,
                ),
                failure="Coturn network cleanup inspection failed",
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            inspect_failed = True
        if inspect_failed:
            absence_request = _network_absence_request(
                tools,
                authority.plan.paths,
                authority.network_id,
            )
        else:
            target = validate_network_cleanup_target(
                authority,
                decode_inspection_result(inspected, label="network"),
            )
            inspected = None
            removed = execute_checked(
                runner,
                build_network_remove_request(tools, target),
                failure="Coturn network removal failed",
            )
            if (
                removed.stderr != b""
                or translate_created_id(removed.stdout) != authority.network_id
            ):
                raise CoturnRuntimeError("Coturn network removal failed")
            removed = None
            absence_request = build_network_absence_request(tools, target)
        absence = execute_checked(
            runner,
            absence_request,
            failure="Coturn network absence check failed",
        )
        absence_request = None
        if absence.stdout != b"" or absence.stderr != b"":
            raise CoturnRuntimeError("Coturn network removal was not confirmed")
        absence = None
        _write_or_validate_absence_marker(
            authority.plan,
            network_id=authority.network_id,
        )
        receipt = _new_network_absence_receipt(
            plan=authority.plan,
            network_id=authority.network_id,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        opaque_failure = error
    except BaseException as error:
        capture.capture_error(error)
    inspected = target = removed = absence = None
    runner = tools = authority = None  # type: ignore[assignment]
    capture.raise_captured()
    if opaque_failure is not None:
        failure = opaque_failure
        opaque_failure = None
        raise failure from None
    if receipt is None:
        raise CoturnRuntimeError("Coturn network cleanup failed") from None
    return receipt


def recover_network_absence(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> NetworkAbsenceReceipt:
    """Trust no missing resource; require marker plus a fresh exact-ID absence query."""

    receipt: NetworkAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    opaque_failure: CoturnDirectorySyncCleanupRequired | None = None
    marker: dict[str, object] | None = None
    result: object = None
    try:
        marker = _read_absence_marker(plan)
        network_id = marker["full_id"]
        if type(network_id) is not str:
            raise CoturnRuntimeError("Coturn network absence marker is invalid")
        result = execute_checked(
            runner,
            _network_absence_request(tools, plan.paths, network_id),
            failure="Coturn network absence recovery failed",
        )
        if result.stdout != b"" or result.stderr != b"":
            raise CoturnRuntimeError("Coturn network absence recovery failed")
        result = None
        _write_or_validate_absence_marker(plan, network_id=network_id)
        receipt = _new_network_absence_receipt(plan=plan, network_id=network_id)
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        opaque_failure = error
    except BaseException as error:
        capture.capture_error(error)
    marker = None
    result = None
    runner = tools = plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if opaque_failure is not None:
        failure = opaque_failure
        opaque_failure = None
        raise failure from None
    if receipt is None:
        raise CoturnRuntimeError("Coturn network absence recovery failed") from None
    return receipt


def _recover_network_absence_from_id(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
    network_id: str,
) -> NetworkAbsenceReceipt:
    """Reconcile a valid persisted full ID after its inspect target disappeared."""

    receipt: NetworkAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    opaque_failure: CoturnDirectorySyncCleanupRequired | None = None
    result: object = None
    try:
        network_id = require_full_resource_id(network_id)
        result = execute_checked(
            runner,
            _network_absence_request(tools, plan.paths, network_id),
            failure="Coturn network absence recovery failed",
        )
        if result.stdout != b"" or result.stderr != b"":
            raise CoturnRuntimeError("Coturn network absence recovery failed")
        result = None
        _write_or_validate_absence_marker(plan, network_id=network_id)
        receipt = _new_network_absence_receipt(plan=plan, network_id=network_id)
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        opaque_failure = error
    except BaseException as error:
        capture.capture_error(error)
    result = None
    runner = tools = plan = None  # type: ignore[assignment]
    network_id = ""
    capture.raise_captured()
    if opaque_failure is not None:
        failure = opaque_failure
        opaque_failure = None
        raise failure from None
    if receipt is None:
        raise CoturnRuntimeError("Coturn network absence recovery failed") from None
    return receipt


def finalize_network_absence(receipt: NetworkAbsenceReceipt) -> None:
    """Remove recovery artifacts with the absence marker strictly last."""

    capture = _RuntimePrivateCleanupCapture()
    opaque_failure: CoturnDirectorySyncCleanupRequired | None = None
    failed = False
    try:
        if type(receipt) is not NetworkAbsenceReceipt:
            raise CoturnRuntimeError("Coturn network absence finalization failed")
        with receipt._lock:
            if receipt._finalized:
                return
            paths = receipt._plan.paths
            if network_absence_marker_exists(paths):
                marker = _read_absence_marker(receipt._plan)
                if marker.get("full_id") != receipt._network_id:
                    raise CoturnRuntimeError("Coturn network absence marker is invalid")
                marker = None
                paths.network_receipt.unlink(missing_ok=True)
                paths.network_plan_receipt.unlink(missing_ok=True)
                _sync_control_directory(paths.control_dir)
                paths.network_absence_receipt.unlink()
                _sync_control_directory(paths.control_dir)
            elif any(
                path.exists() or path.is_symlink()
                for path in (paths.network_receipt, paths.network_plan_receipt)
            ):
                raise CoturnRuntimeError("Coturn network absence finalization failed")
            else:
                _sync_control_directory(paths.control_dir)
            receipt._finalized = True
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        opaque_failure = error
    except BaseException as error:
        failed = not capture.capture_error(error)
    receipt = None  # type: ignore[assignment]
    capture.raise_captured()
    if opaque_failure is not None:
        failure = opaque_failure
        opaque_failure = None
        raise failure from None
    if failed:
        raise CoturnRuntimeError("Coturn network absence finalization failed") from None


def network_absence_marker_exists(paths: CoturnRuntimePaths) -> bool:
    return paths.network_absence_receipt.exists() or paths.network_absence_receipt.is_symlink()


def _new_network_absence_receipt(
    *,
    plan: NetworkPlan,
    network_id: str,
) -> NetworkAbsenceReceipt:
    return NetworkAbsenceReceipt(_ABSENCE_TOKEN, plan=plan, network_id=network_id)


def _network_absence_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    network_id: str,
) -> CommandRequest:
    return docker_request(
        tools,
        paths,
        "network",
        "ls",
        "--quiet",
        "--no-trunc",
        "--filter",
        f"id={network_id}",
    )


def _write_or_validate_absence_marker(plan: NetworkPlan, *, network_id: str) -> None:
    path = plan.paths.network_absence_receipt
    if path.exists() or path.is_symlink():
        marker = _read_absence_marker(plan)
        if marker.get("full_id") != network_id:
            raise CoturnRuntimeError("Coturn network absence marker is invalid")
        marker = None
    else:
        value = _marker_value(plan, network_id=network_id)
        write_owned_file_exclusive(path, value, mode=0o600, maximum=768)
        value = b""
    _sync_control_directory(plan.paths.control_dir)


def _read_absence_marker(plan: NetworkPlan) -> dict[str, object]:
    value = read_owned_file(
        plan.paths.network_absence_receipt,
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
        or decoded.get("run_dir_fingerprint") != plan.run_dir_fingerprint
        or decoded.get("schema_version") != 1
        or decoded.get("state") != "absent"
        or type(decoded.get("full_id")) is not str
        or len(decoded["full_id"]) != 64
        or any(character not in "0123456789abcdef" for character in decoded["full_id"])
    ):
        decoded = None
        raise CoturnRuntimeError("Coturn network absence marker is invalid") from None
    return decoded


def _marker_value(plan: NetworkPlan, *, network_id: str) -> bytes:
    return (
        json.dumps(
            {
                "full_id": network_id,
                "nonce": plan.identity.owner_nonce,
                "owner": _OWNER,
                "run_dir_fingerprint": plan.run_dir_fingerprint,
                "schema_version": 1,
                "state": "absent",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def _write_network_receipt(
    paths: CoturnRuntimePaths,
    *,
    plan: NetworkPlan,
    network_id: str,
) -> None:
    value = (
        json.dumps(
            {
                "network_id": network_id,
                "nonce": plan.identity.owner_nonce,
                "owner": _OWNER,
                "run_dir_fingerprint": plan.run_dir_fingerprint,
                "schema_version": 2,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    write_owned_file_exclusive(paths.network_receipt, value, mode=0o600, maximum=512)
    value = b""
    _sync_control_directory(paths.control_dir)


def _write_network_plan_receipt(paths: CoturnRuntimePaths, *, plan: NetworkPlan) -> None:
    value = (
        json.dumps(
            {
                "gateway": str(plan.topology.gateway),
                "network": str(plan.topology.network),
                "network_name": plan.identity.network_name,
                "nonce": plan.identity.owner_nonce,
                "owner": _OWNER,
                "run_dir_fingerprint": plan.run_dir_fingerprint,
                "schema_version": 2,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    write_owned_file_exclusive(paths.network_plan_receipt, value, mode=0o600, maximum=768)
    value = b""
    _sync_control_directory(paths.control_dir)


def _read_network_plan_receipt(paths: CoturnRuntimePaths, *, plan: NetworkPlan) -> None:
    value = read_owned_file(paths.network_plan_receipt, exact_mode=0o600, maximum=768)
    try:
        decoded = json.loads(value.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise CoturnRuntimeError("Coturn network plan receipt is invalid") from None
    expected = {
        "gateway": str(plan.topology.gateway),
        "network": str(plan.topology.network),
        "network_name": plan.identity.network_name,
        "nonce": plan.identity.owner_nonce,
        "owner": _OWNER,
        "run_dir_fingerprint": plan.run_dir_fingerprint,
        "schema_version": 2,
    }
    if decoded != expected:
        raise CoturnRuntimeError("Coturn network plan receipt is invalid")


def _read_network_receipt(paths: CoturnRuntimePaths, *, plan: NetworkPlan) -> str:
    value = read_owned_file(paths.network_receipt, exact_mode=0o600, maximum=512)
    try:
        decoded = json.loads(value.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise CoturnRuntimeError("Coturn network receipt is invalid") from None
    if (
        not isinstance(decoded, dict)
        or set(decoded)
        != {
            "network_id",
            "nonce",
            "owner",
            "run_dir_fingerprint",
            "schema_version",
        }
        or decoded.get("nonce") != plan.identity.owner_nonce
        or decoded.get("owner") != _OWNER
        or decoded.get("run_dir_fingerprint") != plan.run_dir_fingerprint
        or decoded.get("schema_version") != 2
    ):
        raise CoturnRuntimeError("Coturn network receipt is invalid")
    try:
        return translate_created_id(decoded.get("network_id"))
    except RuntimeError:
        raise CoturnRuntimeError("Coturn network receipt is invalid") from None


__all__ = [
    "NetworkAbsenceReceipt",
    "cleanup_owned_network_transaction",
    "finalize_network_absence",
    "network_absence_marker_exists",
    "recover_network_absence",
    "recover_network_cleanup_authority",
]

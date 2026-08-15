"""Small lifecycle composition helpers over the Coturn host/Docker contracts.

These helpers own command ordering and crash receipts, but are deliberately not
an integration-ready owner. They neither provide a concrete concurrently
draining executor nor qualify empirical inspect, bridge-ingress, or Coturn log
behavior, and therefore cannot qualify relay media by themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from scripts.voice_pipecat_e2e_coturn_docker import (
    CoturnImageReceipt,
    build_image_inspect_request,
    build_image_pull_request,
    decode_inspection_result,
    translate_created_id,
    validate_image_inspection,
)
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ContainerPlan,
    ValidatedContainer,
    build_container_absence_request,
    build_container_create_request,
    build_container_inspect_request,
    build_container_name_inspect_request,
    build_container_remove_request,
    build_container_start_attached_request,
    build_container_stop_request,
    establish_container_cleanup_authority,
    validate_container_cleanup_target,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkCleanupAuthority,
    NetworkPlan,
    ValidatedNetwork,
    build_network_absence_request,
    build_network_create_request,
    build_network_inspect_request,
    build_network_name_inspect_request,
    build_network_remove_request,
    establish_network_cleanup_authority,
    validate_bridge_route_transition,
    validate_network_cleanup_target,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    AttachedCommand,
    BridgeHostProbe,
    CommandRequest,
    CommandResult,
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
    execute_checked,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    read_owned_file,
    write_owned_file_exclusive,
)

_OWNER = "coturn-checkpoint-b-v1"


class CoturnRuntimeError(RuntimeError):
    """An owned lifecycle step could not complete safely."""


@dataclass(frozen=True)
class OwnedNetwork:
    authority: NetworkCleanupAuthority = field(repr=False)
    validated: ValidatedNetwork = field(repr=False)


@dataclass(frozen=True)
class OwnedContainer:
    authority: ContainerCleanupAuthority = field(repr=False)
    validated: ValidatedContainer = field(repr=False)


class AttachedCoturnProcess:
    """Opaque attached handle; executor drain/persistence behavior is unqualified."""

    __slots__ = ("_handle", "_request")

    def __init__(self, handle: AttachedCommand, request: CommandRequest) -> None:
        self._handle = handle
        self._request = request

    def poll(self) -> int | None:
        try:
            value = self._handle.poll()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CoturnRuntimeError("Coturn attached process poll failed") from None
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise CoturnRuntimeError("Coturn attached process state is invalid")
        return value

    def collect(self, *, timeout_seconds: float) -> CommandResult:
        if not 0.1 <= timeout_seconds <= 60.0:
            raise CoturnRuntimeError("Coturn attached collection timeout is invalid")
        try:
            result = self._handle.collect(timeout_seconds=timeout_seconds)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CoturnRuntimeError("Coturn attached process collection failed") from None
        if (
            not isinstance(result, CommandResult)
            or len(result.stdout) + len(result.stderr) > self._request.maximum_output_bytes
        ):
            raise CoturnRuntimeError("Coturn attached process result is invalid")
        return result

    def __repr__(self) -> str:
        return "AttachedCoturnProcess()"


def pull_and_validate_image(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CoturnImageReceipt:
    """Pull only the pinned digest/platform, then validate its exact child."""

    execute_checked(
        runner,
        build_image_pull_request(tools, paths),
        failure="Coturn image pull failed",
    )
    inspection = execute_checked(
        runner,
        build_image_inspect_request(tools, paths),
        failure="Coturn image inspection failed",
    )
    return validate_image_inspection(decode_inspection_result(inspection, label="image"))


def create_owned_network(
    *,
    runner: CommandRunner,
    bridge_probe: BridgeHostProbe,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> OwnedNetwork:
    """Create, receipt, inspect, and host-route-validate one owned network."""

    paths = plan.paths
    if any(
        path.exists() or path.is_symlink()
        for path in (paths.network_plan_receipt, paths.network_receipt)
    ):
        raise CoturnRuntimeError("Coturn network receipt already exists")
    before = bridge_probe.ipv4_routes()
    _write_network_plan_receipt(paths, plan=plan)
    try:
        result = execute_checked(
            runner,
            build_network_create_request(tools, plan),
            failure="Coturn network creation failed",
        )
        if result.stderr:
            raise CoturnRuntimeError("Coturn network creation failed")
        network_id = translate_created_id(result.stdout)
        _write_network_receipt(paths, plan=plan, network_id=network_id)
        inspection_result = execute_checked(
            runner,
            build_network_inspect_request(tools, plan, network_id),
            failure="Coturn network inspection failed",
        )
        inspection = decode_inspection_result(inspection_result, label="network")
        authority = establish_network_cleanup_authority(
            plan=plan,
            network_id=network_id,
            inspection=inspection,
        )
        validated = validate_network_for_container(authority, inspection)
        after = bridge_probe.ipv4_routes()
        bridge_ipv4 = bridge_probe.interface_ipv4(plan.identity.bridge_name)
        validate_bridge_route_transition(
            plan=plan,
            before=before,
            after=after,
            bridge_ipv4=bridge_ipv4,
        )
        return OwnedNetwork(authority=authority, validated=validated)
    except BaseException as failure:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            try:
                _attempt_network_recovery_cleanup(runner, tools, plan)
            except (KeyboardInterrupt, SystemExit):
                pass
            raise
        cleaned = _attempt_network_recovery_cleanup(runner, tools, plan)
        if cleaned:
            raise CoturnRuntimeError("Coturn network preparation failed") from None
        raise CoturnRuntimeError("Coturn network retained for explicit recovery") from None


def create_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> OwnedContainer:
    """Create through a 0077 cidfile phase, then inspect before start."""

    if any(
        path.exists() or path.is_symlink()
        for path in (plan.paths.cidfile, plan.paths.container_receipt)
    ):
        raise CoturnRuntimeError("Coturn container receipt already exists")
    _write_container_plan_receipt(plan)
    try:
        result = execute_checked(
            runner,
            build_container_create_request(tools, plan),
            failure="Coturn container creation failed",
        )
        if result.stderr:
            raise CoturnRuntimeError("Coturn container creation failed")
        stdout_id = translate_created_id(result.stdout)
        cidfile_id = read_private_cidfile(plan.paths)
        if stdout_id != cidfile_id:
            raise CoturnRuntimeError("Coturn container receipt mismatch")
        inspected = execute_checked(
            runner,
            build_container_inspect_request(tools, plan, cidfile_id),
            failure="Coturn container inspection failed",
        )
        inspection = decode_inspection_result(inspected, label="container")
        authority = establish_container_cleanup_authority(
            plan=plan,
            container_id=cidfile_id,
            inspection=inspection,
        )
        validated = validate_container_for_start(authority, inspection)
        return OwnedContainer(authority=authority, validated=validated)
    except BaseException as failure:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            try:
                _attempt_container_recovery_cleanup(runner, tools, plan)
            except (KeyboardInterrupt, SystemExit):
                pass
            raise
        cleaned = _attempt_container_recovery_cleanup(runner, tools, plan)
        if cleaned:
            raise CoturnRuntimeError("Coturn container preparation failed") from None
        raise CoturnRuntimeError("Coturn container retained for explicit recovery") from None


def start_owned_container_attached(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    container: ValidatedContainer,
) -> AttachedCoturnProcess:
    """Request an attached handle; concrete concurrent draining remains a gate."""

    request = build_container_start_attached_request(tools, container)
    try:
        handle = runner.start_attached(request)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise CoturnRuntimeError("Coturn attached start failed") from None
    if handle is None:
        raise CoturnRuntimeError("Coturn attached start failed")
    return AttachedCoturnProcess(handle, request)


def cleanup_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: ContainerCleanupAuthority,
) -> None:
    """Re-inspect exact ownership; stop if running; remove only when stopped."""

    paths = authority.plan.paths
    target = _inspect_container_cleanup(runner, tools, authority)
    if target.running:
        execute_checked(
            runner,
            build_container_stop_request(tools, target),
            failure="Coturn container stop failed",
        )
        target = _inspect_container_cleanup(runner, tools, authority)
        if target.running:
            raise CoturnRuntimeError("Coturn container remained running")
    execute_checked(
        runner,
        build_container_remove_request(tools, target),
        failure="Coturn container removal failed",
    )
    absence = execute_checked(
        runner,
        build_container_absence_request(tools, target),
        failure="Coturn container absence check failed",
    )
    _require_absent(absence, label="container")
    for path in (paths.cidfile, paths.container_receipt):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise CoturnRuntimeError("Coturn container receipt cleanup failed") from None


def cleanup_owned_network(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: NetworkCleanupAuthority,
) -> None:
    """Remove only the exact labeled, empty network and then its receipt."""

    paths = authority.plan.paths
    inspected = execute_checked(
        runner,
        build_network_inspect_request(tools, authority.plan, authority.network_id),
        failure="Coturn network cleanup inspection failed",
    )
    target = validate_network_cleanup_target(
        authority,
        decode_inspection_result(inspected, label="network"),
    )
    execute_checked(
        runner,
        build_network_remove_request(tools, target),
        failure="Coturn network removal failed",
    )
    absence = execute_checked(
        runner,
        build_network_absence_request(tools, target),
        failure="Coturn network absence check failed",
    )
    _require_absent(absence, label="network")
    for path in (paths.network_receipt, paths.network_plan_receipt):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise CoturnRuntimeError("Coturn network receipt cleanup failed") from None


def read_private_cidfile(paths: CoturnRuntimePaths) -> str:
    value = read_owned_file(paths.cidfile, exact_mode=0o600, maximum=65)
    try:
        decoded = value.decode("ascii")
        return translate_created_id(decoded)
    except (UnicodeError, RuntimeError):
        raise CoturnRuntimeError("Coturn cidfile is invalid") from None


def recover_network_cleanup_authority(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> NetworkCleanupAuthority:
    """Bind a private receipt back to current exact Docker labels and ID."""

    paths = plan.paths
    _read_network_plan_receipt(paths, plan=plan)
    if paths.network_receipt.is_symlink():
        raise CoturnRuntimeError("Coturn network receipt is invalid")
    if paths.network_receipt.exists():
        network_id = _read_network_receipt(paths, plan=plan)
        request = build_network_inspect_request(tools, plan, network_id)
    else:
        request = build_network_name_inspect_request(tools, plan)
    inspected = execute_checked(
        runner,
        request,
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


def recover_container_cleanup_authority(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> ContainerCleanupAuthority:
    """Recover exact container ownership from plan receipt plus ID or name."""

    _read_container_plan_receipt(plan)
    if plan.paths.cidfile.is_symlink():
        raise CoturnRuntimeError("Coturn cidfile is invalid")
    if plan.paths.cidfile.exists():
        identifier = read_private_cidfile(plan.paths)
        request = build_container_inspect_request(tools, plan, identifier)
    else:
        request = build_container_name_inspect_request(tools, plan)
    inspected = execute_checked(
        runner,
        request,
        failure="Coturn container recovery inspection failed",
    )
    inspection = decode_inspection_result(inspected, label="container")
    item = inspection[0] if isinstance(inspection, list) and inspection else None
    if not isinstance(item, dict):
        raise CoturnRuntimeError("Coturn container recovery inspection is invalid")
    identifier = translate_created_id(item.get("Id"))
    return establish_container_cleanup_authority(
        plan=plan,
        container_id=identifier,
        inspection=inspection,
    )


def _inspect_container_cleanup(
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: ContainerCleanupAuthority,
):
    result = execute_checked(
        runner,
        build_container_inspect_request(tools, authority.plan, authority.container_id),
        failure="Coturn container cleanup inspection failed",
    )
    return validate_container_cleanup_target(
        authority,
        decode_inspection_result(result, label="container"),
    )


def _attempt_network_recovery_cleanup(
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> bool:
    try:
        authority = recover_network_cleanup_authority(
            runner=runner,
            tools=tools,
            plan=plan,
        )
        cleanup_owned_network(
            runner=runner,
            tools=tools,
            authority=authority,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    return True


def _attempt_container_recovery_cleanup(
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> bool:
    try:
        authority = recover_container_cleanup_authority(
            runner=runner,
            tools=tools,
            plan=plan,
        )
        cleanup_owned_container(
            runner=runner,
            tools=tools,
            authority=authority,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    return True


def _require_absent(result: CommandResult, *, label: str) -> None:
    if label not in {"container", "network"} or result.stdout != b"" or result.stderr != b"":
        raise CoturnRuntimeError(f"Coturn {label} removal was not confirmed")


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


def _write_container_plan_receipt(plan: ContainerPlan) -> None:
    value = (
        json.dumps(
            {
                "container_name": plan.identity.container_name,
                "image_id": plan.image.image_id,
                "network_id": plan.network.authority.network_id,
                "nonce": plan.identity.owner_nonce,
                "owner": _OWNER,
                "run_dir_fingerprint": plan.network.authority.plan.run_dir_fingerprint,
                "schema_version": 2,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    write_owned_file_exclusive(plan.paths.container_receipt, value, mode=0o600, maximum=768)


def _read_container_plan_receipt(plan: ContainerPlan) -> None:
    value = read_owned_file(plan.paths.container_receipt, exact_mode=0o600, maximum=768)
    try:
        decoded = json.loads(value.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise CoturnRuntimeError("Coturn container plan receipt is invalid") from None
    expected = {
        "container_name": plan.identity.container_name,
        "image_id": plan.image.image_id,
        "network_id": plan.network.authority.network_id,
        "nonce": plan.identity.owner_nonce,
        "owner": _OWNER,
        "run_dir_fingerprint": plan.network.authority.plan.run_dir_fingerprint,
        "schema_version": 2,
    }
    if decoded != expected:
        raise CoturnRuntimeError("Coturn container plan receipt is invalid")


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
    "AttachedCoturnProcess",
    "CoturnRuntimeError",
    "OwnedContainer",
    "OwnedNetwork",
    "cleanup_owned_container",
    "cleanup_owned_network",
    "create_owned_container",
    "create_owned_network",
    "pull_and_validate_image",
    "read_private_cidfile",
    "recover_container_cleanup_authority",
    "recover_network_cleanup_authority",
    "start_owned_container_attached",
]

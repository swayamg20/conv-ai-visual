"""Small lifecycle composition helpers over the Coturn host/Docker contracts.

These helpers own command ordering and crash receipts, but are deliberately not
an integration-ready owner. They neither provide a concrete concurrently
draining executor nor qualify empirical inspect, bridge-ingress, or Coturn log
behavior, and therefore cannot qualify relay media by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scripts.voice_pipecat_e2e_coturn_docker import (
    decode_inspection_result,
    translate_created_id,
)
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ContainerPlan,
    ValidatedContainer,
    build_container_create_request,
    build_container_inspect_request,
    build_container_name_inspect_request,
    build_container_start_attached_request,
    establish_container_cleanup_authority,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkCleanupAuthority,
    NetworkPlan,
    ValidatedNetwork,
    build_network_create_request,
    build_network_inspect_request,
    establish_network_cleanup_authority,
    validate_bridge_route_transition,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    BridgeHostProbe,
    CommandRunner,
    TrustedHostTools,
    execute_checked,
)
from scripts.voice_pipecat_e2e_coturn_runtime_container_absence import (
    _recover_container_absence_from_id,
    container_absence_marker_exists,
    recover_container_absence,
)
from scripts.voice_pipecat_e2e_coturn_runtime_container_persistence import (
    _container_creation_paths,
    _read_container_plan_receipt,
    _write_container_plan_receipt,
    read_private_cidfile,
)
from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    CoturnDirectorySyncCleanupRequired,
    DirectorySyncCleanupAuthority,
    cleanup_directory_sync_authority,
    sync_owned_directory,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
    AttachedCoturnEvidencePump,
    CoturnProbeSummary,
    create_attached_coturn_evidence_pump,
)
from scripts.voice_pipecat_e2e_coturn_runtime_lifecycle import (
    ContainerAbsenceReceipt,
    RecoveredContainerCleanupAuthority,
    RemovedContainerReceipt,
    StoppedCoturnReceipt,
    _new_recovered_container_cleanup_authority,
    cleanup_recovered_owned_container,
    finalize_container_absence,
    remove_stopped_owned_container,
    stop_owned_container,
    validate_owned_container_running,
)
from scripts.voice_pipecat_e2e_coturn_runtime_network import (
    NetworkAbsenceReceipt,
    _network_creation_paths,
    _write_network_plan_receipt,
    _write_network_receipt,
    cleanup_owned_network_transaction,
    finalize_network_absence,
    recover_network_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_prerequisites import (
    DockerPrerequisites,
    prepare_docker_prerequisites,
    pull_and_validate_image,
)
from scripts.voice_pipecat_e2e_coturn_runtime_private_cleanup import (
    CoturnRuntimePrivateCleanupRequired,
    RuntimePrivateCleanupAuthority,
    _runtime_persistence_outcome,
    _RuntimePrivateCleanupCapture,
    cleanup_runtime_private_authority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
    CoturnAttachedCleanupRequired,
    CoturnAttachedProcessCleanupRequired,
    UnpublishedAttachedCleanupAuthority,
    _container_recovery_is_allowed,
    _new_unpublished_attached_cleanup_authority,
    cleanup_unpublished_attached,
    confirm_attached_coturn_clean_exit,
    new_attached_coturn_process,
)
from scripts.voice_pipecat_e2e_coturn_runtime_readiness import (
    RuntimeReadinessBudget,
    create_runtime_readiness_budget,
)
from scripts.voice_pipecat_e2e_coturn_runtime_tls import (
    CoturnRuntimeTlsCleanupRequired,
    RuntimeTlsMaterial,
    bind_runtime_tls_material_to_container,
    cleanup_runtime_tls_material,
    cleanup_unpublished_runtime_tls_material,
    execute_openssl_readiness,
    generate_runtime_tls_material,
    new_runtime_tls_material,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    CoturnRuntimeError,
    control_signal,
    raise_control,
)


@dataclass(frozen=True)
class OwnedNetwork:
    authority: NetworkCleanupAuthority = field(repr=False)
    validated: ValidatedNetwork = field(repr=False)


@dataclass(frozen=True)
class OwnedContainer:
    authority: ContainerCleanupAuthority = field(repr=False)
    validated: ValidatedContainer = field(repr=False)


def create_owned_network(
    *,
    runner: CommandRunner,
    bridge_probe: BridgeHostProbe,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> OwnedNetwork:
    """Create, receipt, inspect, and host-route-validate one owned network."""

    paths = _network_creation_paths(plan)
    try:
        before = bridge_probe.ipv4_routes()
        _write_network_plan_receipt(paths, plan=plan)
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
        first_control, first_authority = _runtime_persistence_outcome(failure)
        if first_authority is not None:
            failure = None
            result = inspection_result = inspection = authority = validated = None
            after = bridge_ipv4 = network_id = None
            if first_control is not None:
                raise_control(first_control, first_authority)
            if type(first_authority) is RuntimePrivateCleanupAuthority:
                raise CoturnRuntimePrivateCleanupRequired(first_authority) from None
            raise CoturnDirectorySyncCleanupRequired(first_authority) from None  # type: ignore[arg-type]
        recovery_control = None
        recovery_authority = None
        opaque_failure = None
        try:
            cleaned = _attempt_network_recovery_cleanup(runner, tools, plan)
        except (KeyboardInterrupt, SystemExit) as error:
            recovery_control, recovery_authority = _runtime_persistence_outcome(error)
            cleaned = False
        except (
            CoturnDirectorySyncCleanupRequired,
            CoturnRuntimePrivateCleanupRequired,
        ) as error:
            opaque_failure = error
            recovery_authority = error.cleanup_authority
            cleaned = False
        failure = None
        result = inspection_result = inspection = authority = validated = None
        after = bridge_ipv4 = network_id = None
        if first_control is not None or recovery_control is not None:
            opaque_failure = None
            raise_control(first_control or recovery_control, recovery_authority)  # type: ignore[arg-type]
        if opaque_failure is not None:
            raise opaque_failure from None
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

    paths = _container_creation_paths(plan)
    try:
        _write_container_plan_receipt(plan)
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
        sync_owned_directory(paths.control_dir)
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
        first_control, first_authority = _runtime_persistence_outcome(failure)
        if first_authority is not None:
            failure = None
            result = inspected = inspection = authority = validated = None
            stdout_id = cidfile_id = None
            if first_control is not None:
                raise_control(first_control, first_authority)
            if type(first_authority) is RuntimePrivateCleanupAuthority:
                raise CoturnRuntimePrivateCleanupRequired(first_authority) from None
            raise CoturnDirectorySyncCleanupRequired(first_authority) from None  # type: ignore[arg-type]
        recovery_control = None
        recovery_authority = None
        opaque_failure = None
        try:
            cleaned = _attempt_container_recovery_cleanup(runner, tools, plan)
        except (KeyboardInterrupt, SystemExit) as error:
            recovery_control = control_signal(error)
            recovery_authority = _runtime_control_authority(error)
            cleaned = False
        except (
            CoturnDirectorySyncCleanupRequired,
            CoturnRuntimePrivateCleanupRequired,
        ) as error:
            opaque_failure = error
            recovery_authority = error.cleanup_authority
            cleaned = False
        failure = None
        result = inspected = inspection = authority = validated = None
        stdout_id = cidfile_id = None
        if first_control is not None or recovery_control is not None:
            opaque_failure = None
            raise_control(first_control or recovery_control, recovery_authority)  # type: ignore[arg-type]
        if opaque_failure is not None:
            raise opaque_failure from None
        if cleaned:
            raise CoturnRuntimeError("Coturn container preparation failed") from None
        raise CoturnRuntimeError("Coturn container retained for explicit recovery") from None


def start_owned_container_attached(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    container: ValidatedContainer,
    process: AttachedCoturnProcess,
) -> None:
    """Populate a harmless caller-owned process before this call can return."""

    request = None
    handle: object = None
    authority: ContainerCleanupAuthority | None = None
    pending = _new_unpublished_attached_cleanup_authority(runner)
    control = None
    try:
        if type(container) is not ValidatedContainer:
            raise CoturnRuntimeError("Coturn attached start failed")
        authority = container.authority
        if (
            type(authority) is not ContainerCleanupAuthority
            or type(process) is not AttachedCoturnProcess
            or not process._matches_container(container)
        ):
            raise CoturnRuntimeError("Coturn attached start failed")
        request = build_container_start_attached_request(tools, container)
        handle = runner.start_attached(request)
        if handle is None:
            raise CoturnRuntimeError("Coturn attached start failed")
        if not pending._adopt(handle):
            raise CoturnRuntimeError("Coturn attached start failed")
        handle = None
        if not process._publish(
            handle=pending._handle,  # type: ignore[arg-type]
            runner=runner,
            request=request,
        ):
            raise CoturnRuntimeError("Coturn attached start failed")
        return None
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    cleanup_failed = False
    cleanup_control = None
    cleanup_authority: object | None = None
    if process._started:
        try:
            process.terminate()
        except CoturnAttachedProcessCleanupRequired as error:
            cleanup_failed = True
            cleanup_authority = error.cleanup_authority
        except (KeyboardInterrupt, SystemExit) as error:
            cleanup_control = control_signal(error)
            cleanup_authority = getattr(error, "cleanup_authority", None)
            cleanup_failed = cleanup_authority is not None
        except BaseException:
            cleanup_failed = True
            cleanup_authority = process
    elif pending._handle is None and handle is not None:
        try:
            if not pending._adopt(handle):
                cleanup_failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            cleanup_control = control_signal(error)
            cleanup_failed = True
        except BaseException:
            cleanup_failed = True
    if not process._started:
        try:
            observed_failed, observed_control = pending._settle()
            cleanup_failed = cleanup_failed or observed_failed
            cleanup_control = cleanup_control or observed_control
            if observed_failed:
                cleanup_authority = pending
        except (KeyboardInterrupt, SystemExit) as error:
            cleanup_failed = True
            cleanup_control = cleanup_control or control_signal(error)
            cleanup_authority = pending
        except BaseException:
            cleanup_failed = True
            cleanup_authority = pending
    handle = request = authority = None
    runner = tools = container = None  # type: ignore[assignment]
    if control is not None or cleanup_control is not None:
        selected_control = control or cleanup_control
        recovery = cleanup_authority if cleanup_failed else None
        process = pending = cleanup_authority = None  # type: ignore[assignment]
        raise_control(selected_control, recovery)
    if cleanup_failed:
        recovery = cleanup_authority
        process = pending = cleanup_authority = None  # type: ignore[assignment]
        if type(recovery) is UnpublishedAttachedCleanupAuthority:
            raise CoturnAttachedCleanupRequired(recovery) from None
        raise CoturnAttachedProcessCleanupRequired(recovery) from None  # type: ignore[arg-type]
    process = None  # type: ignore[assignment]
    pending = None  # type: ignore[assignment]
    raise CoturnRuntimeError("Coturn attached start failed") from None


def cleanup_owned_container(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: RecoveredContainerCleanupAuthority | ContainerAbsenceReceipt,
) -> ContainerAbsenceReceipt:
    """Run only the explicit persisted-recovery path and return absence proof."""

    if type(authority) is ContainerAbsenceReceipt:
        return authority
    return cleanup_recovered_owned_container(
        runner=runner,
        tools=tools,
        recovery=authority,
    )


def cleanup_owned_network(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    authority: NetworkCleanupAuthority,
) -> NetworkAbsenceReceipt:
    """Remove or reconcile one exact network and persist absence proof."""

    return cleanup_owned_network_transaction(
        runner=runner,
        tools=tools,
        authority=authority,
    )


def recover_container_cleanup_authority(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> RecoveredContainerCleanupAuthority | ContainerAbsenceReceipt:
    """Recover exact ownership without allowing inspection graphs to escape."""

    recovered: RecoveredContainerCleanupAuthority | ContainerAbsenceReceipt | None = None
    capture = _RuntimePrivateCleanupCapture()
    directory_failure: CoturnDirectorySyncCleanupRequired | None = None
    message = "Coturn container recovery failed"
    try:
        recovered = _recover_container_cleanup_authority(runner, tools, plan)
    except (KeyboardInterrupt, SystemExit) as error:
        capture.capture_control(error)
    except CoturnDirectorySyncCleanupRequired as error:
        directory_failure = error
    except BaseException as error:
        if not capture.capture_error(error):
            arguments = error.args
            allowed = {
                "Coturn cidfile is invalid",
                "Coturn container plan receipt is invalid",
                "Coturn container absence marker is invalid",
                "Coturn container absence recovery failed",
                "Coturn container recovery inspection failed",
                "Coturn container recovery inspection is invalid",
                "Coturn container recovery is unavailable",
            }
            if (
                type(arguments) is tuple
                and len(arguments) == 1
                and type(arguments[0]) is str
                and arguments[0] in allowed
            ):
                message = arguments[0]
            arguments = allowed = None
    runner = tools = plan = None  # type: ignore[assignment]
    capture.raise_captured()
    if directory_failure is not None:
        failure = directory_failure
        directory_failure = None
        raise failure from None
    if type(recovered) not in {RecoveredContainerCleanupAuthority, ContainerAbsenceReceipt}:
        recovered = None
        raise CoturnRuntimeError(message) from None
    return recovered


def _recover_container_cleanup_authority(
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: ContainerPlan,
) -> RecoveredContainerCleanupAuthority | ContainerAbsenceReceipt:
    """Bind a private plan receipt to current exact Docker labels and ID."""

    if container_absence_marker_exists(plan.paths):
        return recover_container_absence(runner=runner, tools=tools, plan=plan)
    _read_container_plan_receipt(plan)
    if plan.paths.cidfile.is_symlink():
        raise CoturnRuntimeError("Coturn cidfile is invalid")
    if plan.paths.cidfile.exists():
        identifier = read_private_cidfile(plan.paths)
        request = build_container_inspect_request(tools, plan, identifier)
    else:
        request = build_container_name_inspect_request(tools, plan)
    try:
        inspected = execute_checked(
            runner,
            request,
            failure="Coturn container recovery inspection failed",
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        if plan.paths.cidfile.exists() and not plan.paths.cidfile.is_symlink():
            confirmed_identifier = read_private_cidfile(plan.paths)
            if confirmed_identifier != identifier:
                raise CoturnRuntimeError("Coturn cidfile is invalid") from None
            return _recover_container_absence_from_id(
                runner=runner,
                tools=tools,
                plan=plan,
                container_id=confirmed_identifier,
            )
        raise
    inspection = decode_inspection_result(inspected, label="container")
    item = inspection[0] if isinstance(inspection, list) and inspection else None
    if not isinstance(item, dict):
        raise CoturnRuntimeError("Coturn container recovery inspection is invalid")
    identifier = translate_created_id(item.get("Id"))
    authority = establish_container_cleanup_authority(
        plan=plan,
        container_id=identifier,
        inspection=inspection,
    )
    if not _container_recovery_is_allowed(authority):
        raise CoturnRuntimeError("Coturn container recovery is unavailable")
    return _new_recovered_container_cleanup_authority(
        authority=authority,
        inspection=inspection,
    )


def _attempt_network_recovery_cleanup(
    runner: CommandRunner,
    tools: TrustedHostTools,
    plan: NetworkPlan,
) -> bool:
    try:
        recovered = recover_network_cleanup_authority(
            runner=runner,
            tools=tools,
            plan=plan,
        )
        absence = (
            recovered
            if type(recovered) is NetworkAbsenceReceipt
            else cleanup_owned_network(
                runner=runner,
                tools=tools,
                authority=recovered,
            )
        )
        finalize_network_absence(absence)
    except (
        CoturnDirectorySyncCleanupRequired,
        CoturnRuntimePrivateCleanupRequired,
    ):
        raise
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    return True


def _runtime_control_authority(error: KeyboardInterrupt | SystemExit) -> object | None:
    try:
        namespace = object.__getattribute__(error, "__dict__")
    except BaseException:
        namespace = None
    candidate = namespace.get("cleanup_authority") if type(namespace) is dict else None
    namespace = None
    if type(candidate) in {
        DirectorySyncCleanupAuthority,
        RuntimePrivateCleanupAuthority,
        UnpublishedAttachedCleanupAuthority,
        AttachedCoturnProcess,
    }:
        return candidate
    return None


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
        absence = cleanup_owned_container(
            runner=runner,
            tools=tools,
            authority=authority,
        )
        finalize_container_absence(absence)
    except (
        CoturnDirectorySyncCleanupRequired,
        CoturnRuntimePrivateCleanupRequired,
    ):
        raise
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    return True


__all__ = [
    "AttachedCoturnEvidencePump",
    "AttachedCoturnProcess",
    "CleanCoturnExitReceipt",
    "ContainerAbsenceReceipt",
    "CoturnAttachedCleanupRequired",
    "CoturnAttachedProcessCleanupRequired",
    "CoturnDirectorySyncCleanupRequired",
    "CoturnProbeSummary",
    "CoturnRuntimeError",
    "CoturnRuntimePrivateCleanupRequired",
    "CoturnRuntimeTlsCleanupRequired",
    "DirectorySyncCleanupAuthority",
    "DockerPrerequisites",
    "OwnedContainer",
    "OwnedNetwork",
    "RecoveredContainerCleanupAuthority",
    "RemovedContainerReceipt",
    "RuntimePrivateCleanupAuthority",
    "RuntimeReadinessBudget",
    "RuntimeTlsMaterial",
    "StoppedCoturnReceipt",
    "UnpublishedAttachedCleanupAuthority",
    "bind_runtime_tls_material_to_container",
    "cleanup_directory_sync_authority",
    "cleanup_owned_container",
    "cleanup_owned_network",
    "cleanup_runtime_private_authority",
    "cleanup_runtime_tls_material",
    "cleanup_unpublished_attached",
    "cleanup_unpublished_runtime_tls_material",
    "confirm_attached_coturn_clean_exit",
    "create_attached_coturn_evidence_pump",
    "create_owned_container",
    "create_owned_network",
    "create_runtime_readiness_budget",
    "execute_openssl_readiness",
    "finalize_container_absence",
    "generate_runtime_tls_material",
    "new_attached_coturn_process",
    "new_runtime_tls_material",
    "prepare_docker_prerequisites",
    "pull_and_validate_image",
    "read_private_cidfile",
    "recover_container_absence",
    "recover_container_cleanup_authority",
    "recover_network_cleanup_authority",
    "remove_stopped_owned_container",
    "start_owned_container_attached",
    "stop_owned_container",
    "validate_owned_container_running",
]

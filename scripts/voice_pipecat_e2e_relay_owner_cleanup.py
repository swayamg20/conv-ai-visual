"""Retryable reverse-order teardown for the relay B0 aggregate."""

from __future__ import annotations

from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerPlan
from scripts.voice_pipecat_e2e_coturn_docker_network import NetworkCleanupAuthority, NetworkPlan
from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_runtime import (
    AttachedCoturnEvidencePump,
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
    ContainerAbsenceReceipt,
    DirectorySyncCleanupAuthority,
    NetworkAbsenceReceipt,
    OwnedNetwork,
    RecoveredContainerCleanupAuthority,
    RuntimePrivateCleanupAuthority,
    RuntimeTlsMaterial,
    StoppedCoturnReceipt,
    UnpublishedAttachedCleanupAuthority,
    cleanup_directory_sync_authority,
    cleanup_owned_container,
    cleanup_owned_network,
    cleanup_runtime_private_authority,
    cleanup_runtime_tls_material,
    cleanup_unpublished_attached,
    cleanup_unpublished_runtime_tls_material,
    confirm_attached_coturn_clean_exit,
    finalize_container_absence,
    finalize_network_absence,
    recover_container_cleanup_authority,
    recover_network_cleanup_authority,
    remove_stopped_owned_container,
    stop_owned_container,
)
from scripts.voice_pipecat_e2e_coturn_runtime_drain import (
    AttachedCoturnEvidenceDrain,
    cleanup_attached_coturn_evidence_drain,
    finish_attached_coturn_evidence_drain,
)
from scripts.voice_pipecat_e2e_coturn_runtime_drain_registry import (
    CoturnEvidenceDrainCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    cleanup_tls_material_authority,
    cleanup_tls_private_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
)
from scripts.voice_pipecat_e2e_relay_browser_result import (
    RelayBrowserObservation,
    RelayBrowserResultOwner,
    cleanup_relay_browser_result_owner,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationCleanupAuthority,
    RelayInvocationOwner,
    cleanup_relay_invocation,
)
from scripts.voice_pipecat_e2e_relay_invocation_support import (
    _read_invocation_owner_destination,
)
from scripts.voice_pipecat_e2e_relay_owner_authority import (
    _PendingAuthorityQueue,
    _retain_runtime_persistence_authority,
)
from scripts.voice_pipecat_e2e_relay_owner_evidence import (
    _recover_canonical_drain,
    _recover_canonical_pump,
)
from scripts.voice_pipecat_e2e_relay_owner_state import RelayProbeOwner
from scripts.voice_pipecat_e2e_relay_owner_terminal import (
    _abandon_terminal_publication,
    _terminal_complete,
    _terminalize,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeRun

_PHASES = (
    "invocation",
    "artifacts",
    "stop-container",
    "drain",
    "clean-exit",
    "remove-container",
    "tls",
    "finalize-container",
    "remove-network",
    "finalize-network",
    "settle-runner",
    "complete",
)


def _drive_cleanup(owner: RelayProbeOwner, *, publish: bool) -> bool:
    """Advance until one retry boundary or complete terminal settlement."""

    with owner._operation_lock:
        with owner._lock:
            owner._cleanup_only = True
            owner._publish_requested = bool(owner._publish_requested or publish)
            if owner._state not in {"observed", "cleaned", "terminal-scrubbing", "publishing"}:
                owner._state = "cleanup-only"
            if owner._state in {"observed", "cleaned"}:
                return _terminal_complete(owner)
        for _ in range(len(_PHASES) + 2):
            phase = owner._cleanup_phase
            if not _settle_pending(owner, phase):
                return False
            operation = _phase_operation(phase)
            if operation is None:
                return False
            if not operation(owner):
                with owner._lock:
                    owner._publish_requested = False
                return False
            if phase == "complete":
                break
        if owner._cleanup_phase != "complete":
            return False
        try:
            return _terminalize(owner)
        except BaseException as error:
            owner._remember_exception(error)
            _abandon_terminal_publication(owner)
            return False


def _phase_operation(phase: str) -> Callable[[RelayProbeOwner], bool] | None:
    return {
        "invocation": _cleanup_invocation,
        "artifacts": _cleanup_artifacts,
        "stop-container": _stop_container,
        "drain": _settle_drain,
        "clean-exit": _confirm_clean_exit,
        "remove-container": _remove_container,
        "tls": _cleanup_tls,
        "finalize-container": _finalize_container,
        "remove-network": _remove_network,
        "finalize-network": _finalize_network,
        "settle-runner": _settle_runner,
        "complete": _complete,
    }.get(phase)


def _cleanup_invocation(owner: RelayProbeOwner) -> bool:
    invocation: RelayInvocationOwner | None = None

    def reconcile() -> None:
        nonlocal invocation
        invocation = owner._read("invocation", RelayInvocationOwner)
        run = owner._run
        driver = owner._invocation_driver
        tools = owner._invocation_tools
        destination = owner._invocation_destination
        if type(run) is not RelayProbeRun or driver is None or tools is None or destination is None:
            return
        candidate, _ready, control = _read_invocation_owner_destination(
            destination,  # type: ignore[arg-type]
            run,
            driver,
            tools,
        )
        owner._remember_control(control)
        if candidate is not None:
            invocation = owner._publish(
                "invocation",
                candidate,
                RelayInvocationOwner,
            )  # type: ignore[assignment]

    if not _attempt(owner, reconcile):
        return False
    if invocation is not None and not _attempt(owner, lambda: cleanup_relay_invocation(invocation)):
        return False
    return _advance(owner, "invocation", "artifacts")


def _cleanup_artifacts(owner: RelayProbeOwner) -> bool:
    run = owner._run
    artifact: RelayBrowserResultOwner | None = None

    def reconcile() -> None:
        nonlocal artifact
        artifact = owner._read("artifact_owner", RelayBrowserResultOwner)
        if type(run) is not RelayProbeRun:
            return
        candidate = run._browser_artifact_cleanup_owner()
        if candidate is not None:
            artifact = owner._publish(
                "artifact_owner",
                candidate,
                RelayBrowserResultOwner,
            )  # type: ignore[assignment]

    if not _attempt(owner, reconcile):
        return False
    observed = owner._read("browser_observation", RelayBrowserObservation)
    if artifact is not None and observed is None:
        if type(run) is not RelayProbeRun:
            return False
        if not _attempt(owner, lambda: cleanup_relay_browser_result_owner(run)):
            return False
    return _advance(owner, "artifacts", "stop-container")


def _stop_container(owner: RelayProbeOwner) -> bool:
    running = owner._read("running", _validated_running_type())
    process = owner._read("process", AttachedCoturnProcess)
    if running is not None and process is not None:
        runner = owner._runner
        tools = owner._tools
        if runner is None or tools is None:
            return False

        def operation() -> None:
            stopped = stop_owned_container(
                runner=runner,
                tools=tools,
                running=running,
                process=process,
            )
            owner._publish("stopped", stopped, StoppedCoturnReceipt)

        if not _attempt(owner, operation):
            return False
    return _advance(owner, "stop-container", "drain")


def _settle_drain(owner: RelayProbeOwner) -> bool:
    process = owner._read("process", AttachedCoturnProcess)
    pump = owner._read("pump", AttachedCoturnEvidencePump)
    drain = owner._read("drain", AttachedCoturnEvidenceDrain)
    if process is None:
        if pump is not None or drain is not None:
            return False
        return _advance(owner, "drain", "clean-exit")

    def reconcile() -> None:
        nonlocal pump, drain
        pump = _recover_canonical_pump(process, pump)
        if pump is not None:
            pump = owner._publish(
                "pump",
                pump,
                AttachedCoturnEvidencePump,
            )  # type: ignore[assignment]
            clock = owner._clock
            if not callable(clock):
                raise TypeError("Relay probe evidence recovery failed")
            drain = _recover_canonical_drain(
                process,
                pump,
                drain,
                absolute_deadline=owner._absolute_deadline,
                clock=clock,
            )
            if drain is not None:
                drain = owner._publish(
                    "drain",
                    drain,
                    AttachedCoturnEvidenceDrain,
                )  # type: ignore[assignment]
        elif drain is not None:
            raise TypeError("Relay probe evidence recovery failed")

    if not _attempt(owner, reconcile):
        return False
    if drain is None:
        if pump is not None:
            aborted = False

            def abort_pump() -> None:
                nonlocal aborted
                failed, control = pump._abort()
                owner._remember_control(control)
                aborted = not failed

            if not _attempt(owner, abort_pump) or not aborted:
                return False
        if not _attempt(owner, reconcile) or drain is not None:
            return False
        return _advance(owner, "drain", "clean-exit")
    stopped = owner._read("stopped", StoppedCoturnReceipt)
    observed = owner._read("browser_observation", RelayBrowserObservation)
    publish = bool(owner._publish_requested and stopped is not None and observed is not None)
    if publish:

        def finish() -> None:
            summary = finish_attached_coturn_evidence_drain(drain)
            if (
                type(summary) is not CoturnProbeSummary
                or summary.grammar_verified is not False
                or bool(summary)
            ):
                raise TypeError("Relay probe summary is invalid")
            owner._publish("summary", summary, CoturnProbeSummary)

        if not _attempt(owner, finish):
            return False
    elif not _attempt(owner, lambda: cleanup_attached_coturn_evidence_drain(drain)):
        return False
    if not _attempt(owner, reconcile) or drain is None:
        return False
    return _advance(owner, "drain", "clean-exit")


def _confirm_clean_exit(owner: RelayProbeOwner) -> bool:
    if owner._publish_requested:
        process = owner._read("process", AttachedCoturnProcess)
        summary = owner._read("summary", CoturnProbeSummary)
        if process is None or summary is None:
            return False

        def operation() -> None:
            receipt = confirm_attached_coturn_clean_exit(process)
            owner._publish("clean_exit", receipt, CleanCoturnExitReceipt)

        if not _attempt(owner, operation):
            return False
    return _advance(owner, "clean-exit", "remove-container")


def _remove_container(owner: RelayProbeOwner) -> bool:
    absence = owner._read("container_absence", ContainerAbsenceReceipt)
    if absence is not None:
        return _advance(owner, "remove-container", "tls")
    runner = owner._runner
    tools = owner._tools
    plan = owner._read("container_plan", ContainerPlan)
    if runner is None or tools is None:
        return False
    stopped = owner._read("stopped", StoppedCoturnReceipt)
    clean_exit = owner._read("clean_exit", CleanCoturnExitReceipt)
    recovery_required = False

    def operation() -> None:
        nonlocal recovery_required
        removed: ContainerAbsenceReceipt | None = None
        if stopped is not None and clean_exit is not None:
            removed = _runtime_persistence_call(
                owner,
                lambda: remove_stopped_owned_container(
                    runner=runner,
                    tools=tools,
                    stopped=stopped,
                    clean_exit=clean_exit,
                ),
            )
        elif plan is not None:
            recovery_required = _container_recovery_exists(plan)
        if plan is not None and recovery_required:
            recovered = _runtime_persistence_call(
                owner,
                lambda: recover_container_cleanup_authority(
                    runner=runner,
                    tools=tools,
                    plan=plan,
                ),
            )
            if type(recovered) not in {
                RecoveredContainerCleanupAuthority,
                ContainerAbsenceReceipt,
            }:
                raise TypeError("Relay probe container recovery is invalid")
            removed = _runtime_persistence_call(
                owner,
                lambda: cleanup_owned_container(
                    runner=runner,
                    tools=tools,
                    authority=recovered,
                ),
            )
        if removed is not None:
            owner._publish("container_absence", removed, ContainerAbsenceReceipt)

    if not _attempt(owner, operation):
        return False
    if recovery_required:
        if owner._read("container_absence", ContainerAbsenceReceipt) is None:
            return False
    return _advance(owner, "remove-container", "tls")


def _cleanup_tls(owner: RelayProbeOwner) -> bool:
    material = owner._read("tls_material", RuntimeTlsMaterial)
    if material is None or material.cleanup_complete:
        return _advance(owner, "tls", "finalize-container")
    try:
        state = object.__getattribute__(material, "_state")
    except BaseException:
        return False
    if state == "empty":
        return _advance(owner, "tls", "finalize-container")
    absence = owner._read("container_absence", ContainerAbsenceReceipt)
    if state in {"bound", "cleaning:bound"}:
        if absence is None:
            return False
        operation = lambda: cleanup_runtime_tls_material(  # noqa: E731
            material,
            container_removal=absence,
        )
    else:
        operation = lambda: cleanup_unpublished_runtime_tls_material(material)  # noqa: E731
    if not _attempt(owner, operation):
        return False
    if not material.cleanup_complete:
        return False
    return _advance(owner, "tls", "finalize-container")


def _finalize_container(owner: RelayProbeOwner) -> bool:
    absence = owner._read("container_absence", ContainerAbsenceReceipt)
    if absence is not None and not absence.finalization_complete:
        if not _attempt(
            owner,
            lambda: _runtime_persistence_call(
                owner,
                lambda: finalize_container_absence(absence),
            ),
        ):
            return False
    if absence is not None and not absence.finalization_complete:
        return False
    return _advance(owner, "finalize-container", "remove-network")


def _remove_network(owner: RelayProbeOwner) -> bool:
    absence = owner._read("network_absence", NetworkAbsenceReceipt)
    if absence is not None:
        return _advance(owner, "remove-network", "finalize-network")
    runner = owner._runner
    tools = owner._tools
    plan = owner._read("network_plan", NetworkPlan)
    owned = owner._read("network", OwnedNetwork)
    if runner is None or tools is None:
        return False
    recovery_required = False

    def operation() -> None:
        nonlocal recovery_required
        authority: NetworkCleanupAuthority | NetworkAbsenceReceipt | None = None
        if owned is not None:
            authority = owned.authority
        elif plan is not None:
            recovery_required = _network_recovery_exists(plan)
            if recovery_required:
                authority = _runtime_persistence_call(
                    owner,
                    lambda: recover_network_cleanup_authority(
                        runner=runner,
                        tools=tools,
                        plan=plan,
                    ),
                )
        if type(authority) is NetworkCleanupAuthority:
            removed = _runtime_persistence_call(
                owner,
                lambda: cleanup_owned_network(
                    runner=runner,
                    tools=tools,
                    authority=authority,
                ),
            )
        elif type(authority) is NetworkAbsenceReceipt:
            removed = authority
        else:
            removed = None
        if removed is not None:
            owner._publish("network_absence", removed, NetworkAbsenceReceipt)

    if not _attempt(owner, operation):
        return False
    if (owned is not None or recovery_required) and owner._read(
        "network_absence", NetworkAbsenceReceipt
    ) is None:
        return False
    return _advance(owner, "remove-network", "finalize-network")


def _finalize_network(owner: RelayProbeOwner) -> bool:
    absence = owner._read("network_absence", NetworkAbsenceReceipt)
    if absence is not None and not absence.finalization_complete:
        if not _attempt(
            owner,
            lambda: _runtime_persistence_call(
                owner,
                lambda: finalize_network_absence(absence),
            ),
        ):
            return False
    if absence is not None and not absence.finalization_complete:
        return False
    return _advance(owner, "finalize-network", "settle-runner")


def _settle_runner(owner: RelayProbeOwner) -> bool:
    if owner._runner_settled:
        return _advance(owner, "settle-runner", "complete")
    runner = owner._runner
    if runner is None:
        return False

    def operation() -> None:
        result = runner.settle_owned()
        if type(result) is not bool or result is not True:
            raise TypeError("Relay probe runner settlement failed")
        owner._runner_settled = True

    if not _attempt(owner, operation):
        return False
    return _advance(owner, "settle-runner", "complete")


def _complete(owner: RelayProbeOwner) -> bool:
    return True


def _settle_pending(owner: RelayProbeOwner, phase: str) -> bool:
    with owner._lock:
        retained = owner._pending_authority
        if type(retained) is _PendingAuthorityQueue:
            authority = next(
                (candidate for candidate in retained._items if _authority_due(candidate, phase)),
                None,
            )
        else:
            authority = retained
        if authority is not None and not _authority_due(authority, phase):
            return True
    if authority is None:
        return True
    if type(authority) is RelayInvocationCleanupAuthority:
        operation = lambda: cleanup_relay_invocation(authority)  # noqa: E731
    elif type(authority) is CoturnEvidenceDrainCleanupAuthority:
        operation = lambda: cleanup_attached_coturn_evidence_drain(authority)  # noqa: E731
    elif type(authority) is DirectorySyncCleanupAuthority:
        operation = lambda: cleanup_directory_sync_authority(authority)  # noqa: E731
    elif type(authority) is RuntimePrivateCleanupAuthority:
        operation = lambda: cleanup_runtime_private_authority(authority)  # noqa: E731
    elif type(authority) is UnpublishedAttachedCleanupAuthority:
        operation = lambda: cleanup_unpublished_attached(authority)  # noqa: E731
    elif type(authority) is AttachedCoturnProcess:
        operation = authority.terminate
    elif type(authority) is RuntimeTlsMaterial:
        return True
    elif type(authority) in {TlsCombinedCleanupAuthority, TlsMaterialLifetimeAuthority}:
        operation = lambda: cleanup_tls_material_authority(authority)  # noqa: E731
    elif type(authority) in {PrivateDescriptorCleanupAuthority, PrivateFileCleanupReceipt}:
        operation = lambda: cleanup_tls_private_authority(authority)  # noqa: E731
    else:
        return False
    if not _attempt(owner, operation):
        return False
    with owner._lock:
        current = owner._pending_authority
        if current is authority:
            owner._pending_authority = None
            return True
        if type(current) is _PendingAuthorityQueue and current._contains(authority):
            owner._pending_authority = current._remove(authority)
            return True
        return owner._pending_authority is None


def _authority_due(authority: object, phase: str) -> bool:
    minimum = {
        RelayInvocationCleanupAuthority: "invocation",
        CoturnEvidenceDrainCleanupAuthority: "drain",
        UnpublishedAttachedCleanupAuthority: "drain",
        AttachedCoturnProcess: "drain",
        DirectorySyncCleanupAuthority: "remove-container",
        RuntimePrivateCleanupAuthority: "remove-container",
        RuntimeTlsMaterial: "tls",
        TlsCombinedCleanupAuthority: "tls",
        TlsMaterialLifetimeAuthority: "tls",
        PrivateDescriptorCleanupAuthority: "tls",
        PrivateFileCleanupReceipt: "tls",
    }.get(type(authority))
    if minimum is None or phase not in _PHASES:
        return False
    return _PHASES.index(phase) >= _PHASES.index(minimum)


def _attempt(owner: RelayProbeOwner, operation: Callable[[], object]) -> bool:
    try:
        operation()
        return True
    except BaseException as error:
        owner._remember_exception(error)
        return False
    finally:
        operation = None  # type: ignore[assignment]


def _runtime_persistence_call(
    owner: RelayProbeOwner,
    operation: Callable[[], object],
) -> object:
    try:
        return operation()
    except BaseException as error:
        _retain_runtime_persistence_authority(owner, error)
        raise
    finally:
        operation = None  # type: ignore[assignment]


def _advance(owner: RelayProbeOwner, current: str, following: str) -> bool:
    with owner._lock:
        if owner._cleanup_phase == current:
            owner._cleanup_phase = following
        return owner._cleanup_phase == following


def _container_recovery_exists(plan: ContainerPlan) -> bool:
    paths = plan.paths
    return any(
        path.exists() or path.is_symlink()
        for path in (paths.cidfile, paths.container_receipt, paths.container_absence_receipt)
    )


def _network_recovery_exists(plan: NetworkPlan) -> bool:
    paths = plan.paths
    return any(
        path.exists() or path.is_symlink()
        for path in (
            paths.network_plan_receipt,
            paths.network_receipt,
            paths.network_absence_receipt,
        )
    )


def _validated_running_type() -> type[object]:
    from scripts.voice_pipecat_e2e_coturn_docker_container import ValidatedRunningContainer

    return ValidatedRunningContainer


__all__: list[str] = []

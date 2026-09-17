"""Retryable cleanup-only reconciliation for a relay artifact workspace."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_coturn_runtime_values import ControlSignal
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
    file_identity,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch
from scripts.voice_pipecat_e2e_relay_artifact_files import (
    ArtifactBudget,
    ArtifactNode,
    capture_optional_log,
    close_node_descriptor,
    close_unpublished_nodes,
    fstat,
    list_names,
    scrub_exception,
    stat_name_status,
    sync_directory,
    tree_has_ambiguous_missing,
)
from scripts.voice_pipecat_e2e_relay_artifact_owner import (
    ArtifactSnapshot,
    RelayArtifactWorkspace,
    _capture_snapshot,
    _discard_relay_artifact_contents,
    _hook,
    _open_binding_candidate,
    _recover_created_claim,
    _sink_value,
)
from scripts.voice_pipecat_e2e_relay_artifact_remove import remove_node

_FINAL_LOG_SCANS = 4


def _settle_relay_artifact_workspace(
    workspace: RelayArtifactWorkspace,
    *,
    initial_control: ControlSignal | None = None,
    control_latch: TlsControlLatch | None = None,
) -> tuple[bool, bool, ControlSignal | None]:
    latch = (
        control_latch
        if type(control_latch) is TlsControlLatch
        else TlsControlLatch(initial_control)
    )
    if control_latch is not None and initial_control is not None:
        latch.record(initial_control)
    if type(workspace) is not RelayArtifactWorkspace:
        return False, False, latch.value()
    with workspace._operation_lock:
        return _settle_locked(workspace, latch)


def _settle_locked(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> tuple[bool, bool, ControlSignal | None]:
    if workspace._complete:
        return True, workspace._publication_safe, latch.value()
    recovering = workspace._settlement_started
    workspace._settlement_started = True
    if recovering:
        workspace._publication_safe = False
    if workspace._invalid:
        workspace._publication_safe = False
    try:
        run = workspace._run_binding
        root = workspace._playwright_root
        if root is not None and root.removed:
            if not remove_node(root, latch):
                return False, False, latch.value()
            if run is not None and not _settle_late_logs(
                workspace,
                run.descriptor,
                latch,
            ):
                workspace._publication_safe = False
                return False, False, latch.value()
            if not _close_workspace_bindings(workspace, latch, complete=True):
                return False, False, latch.value()
            _hook("artifact-cleanup-complete", latch)
            return True, workspace._publication_safe, latch.value()
        if run is None:
            workspace._publication_safe = False
            complete = _publish_workspace_complete(workspace, latch)
            return complete, False, latch.value()
        if workspace._playwright_claim is None and workspace._playwright_created:
            recovery = _recover_created_claim(
                workspace,
                parent=run,
                name="playwright",
                claim_attribute="_playwright_claim",
                latch=latch,
            )
            if recovery == "error":
                return False, False, latch.value()
            if recovery == "absent":
                workspace._publication_safe = False
                return False, False, latch.value()
        if workspace._playwright_claim is None:
            workspace._publication_safe = False
            if not _close_workspace_bindings(workspace, latch, complete=True):
                return False, False, latch.value()
            return True, False, latch.value()
        if workspace._playwright_binding is None:
            status, _ = _open_binding_candidate(
                workspace,
                run.descriptor,
                "playwright",
                workspace._playwright_claim,
                "playwright-recovery",
                "_playwright_binding",
                latch,
            )
            if status == "absent":
                workspace._publication_safe = False
                if not _close_workspace_bindings(workspace, latch, complete=True):
                    return False, False, latch.value()
                return True, False, latch.value()
            if status != "opened" or workspace._playwright_binding is None:
                return False, False, latch.value()
        playwright = workspace._playwright_binding
        if playwright is None:
            return False, False, latch.value()
        if workspace._ephemeral_claim is None and workspace._ephemeral_created:
            recovery = _recover_created_claim(
                workspace,
                parent=playwright,
                name="relay-ephemeral-output",
                claim_attribute="_ephemeral_claim",
                latch=latch,
            )
            if recovery == "error":
                return False, False, latch.value()
            if recovery == "absent":
                workspace._publication_safe = False
                return False, False, latch.value()
        if workspace._ephemeral_claim is not None and workspace._ephemeral_binding is None:
            status, _ = _open_binding_candidate(
                workspace,
                playwright.descriptor,
                "relay-ephemeral-output",
                workspace._ephemeral_claim,
                "ephemeral-recovery",
                "_ephemeral_binding",
                latch,
            )
            if status == "error":
                return False, False, latch.value()
            if status == "absent":
                workspace._publication_safe = False
            elif status != "opened" or workspace._ephemeral_binding is None:
                return False, False, latch.value()
        if workspace._rescan_required:
            snapshot = workspace._snapshot
            ambiguous = bool(
                snapshot is not None
                and (
                    any(tree_has_ambiguous_missing(node) for node in snapshot.nodes)
                    or (snapshot.log is not None and tree_has_ambiguous_missing(snapshot.log))
                )
            )
            if ambiguous:
                workspace._rescan_required = False
            elif not _release_snapshot(workspace, latch):
                return False, False, latch.value()
        if workspace._snapshot is None and not _capture_snapshot(
            workspace,
            latch,
            validation=False,
        ):
            return False, False, latch.value()
        snapshot = workspace._snapshot
        if snapshot is None:
            return False, False, latch.value()
        if not _snapshot_retains_ephemeral_identity(workspace, snapshot):
            workspace._publication_safe = False
            if not _release_snapshot(workspace, latch):
                return False, False, latch.value()
            return False, False, latch.value()
        _discard_relay_artifact_contents(workspace)
        for node in snapshot.nodes:
            if not remove_node(node, latch):
                workspace._rescan_required = True
                return False, False, latch.value()
            _record_ephemeral_removal(workspace, node)
        if snapshot.log is not None and not remove_node(snapshot.log, latch):
            workspace._rescan_required = True
            return False, False, latch.value()
        if not _release_snapshot(workspace, latch):
            return False, False, latch.value()
        if list_names(playwright.descriptor, latch) != ():
            workspace._rescan_required = True
            return False, False, latch.value()
        if not sync_directory(playwright.descriptor, latch):
            return False, False, latch.value()
        if not _ensure_playwright_root(workspace, latch):
            return False, False, latch.value()
        root = workspace._playwright_root
        if root is None or not remove_node(root, latch):
            return False, False, latch.value()
        if not _settle_late_logs(workspace, run.descriptor, latch):
            workspace._publication_safe = False
            return False, False, latch.value()
        if not _close_workspace_bindings(workspace, latch, complete=True):
            return False, False, latch.value()
        _hook("artifact-cleanup-complete", latch)
        return True, workspace._publication_safe, latch.value()
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False, False, latch.value()
    except BaseException as error:
        scrub_exception(error)
        return False, False, latch.value()
    finally:
        if not workspace._complete:
            workspace._publication_safe = False


def _release_snapshot(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> bool:
    snapshot = workspace._snapshot
    if snapshot is None:
        workspace._rescan_required = False
        return True
    if any(tree_has_ambiguous_missing(node) for node in snapshot.nodes) or (
        snapshot.log is not None and tree_has_ambiguous_missing(snapshot.log)
    ):
        return False
    for node in snapshot.nodes:
        _record_ephemeral_removal(workspace, node)
    _discard_relay_artifact_contents(workspace)
    complete = close_unpublished_nodes(snapshot.nodes, latch)
    if snapshot.log is not None:
        complete = close_node_descriptor(snapshot.log, latch) and complete
    if not complete:
        return False
    workspace._snapshot = None
    workspace._rescan_required = False
    return True


def _snapshot_retains_ephemeral_identity(
    workspace: RelayArtifactWorkspace,
    snapshot: ArtifactSnapshot,
) -> bool:
    claim = workspace._ephemeral_claim
    if claim is None:
        return not workspace._ephemeral_created
    matches = [node for node in snapshot.nodes if node.identity == claim]
    return bool(len(matches) == 1 or (not matches and workspace._ephemeral_removed))


def _record_ephemeral_removal(
    workspace: RelayArtifactWorkspace,
    node: ArtifactNode,
) -> None:
    if node.identity == workspace._ephemeral_claim and node.removed:
        workspace._ephemeral_removed = True


def _capture_fresh_log(
    workspace: RelayArtifactWorkspace,
    run_fd: int,
    latch: TlsControlLatch,
) -> str:
    logs: list[ArtifactNode] = []
    snapshot: ArtifactSnapshot | None = None
    try:
        status = capture_optional_log(
            run_fd,
            ArtifactBudget(),
            latch,
            sink=lambda node: _append_log(logs, node),
        )
        if status == "error":
            return "error"
        if status == "absent":
            return "absent"
        log = logs[0]
        snapshot = ArtifactSnapshot(
            nodes=[],
            result=None,
            report=None,
            log=log,
            structural=False,
        )
        return (
            "found"
            if _sink_value(workspace, "_snapshot", snapshot, "log-rescan", latch)
            else "error"
        )
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return "found" if workspace._snapshot is snapshot and snapshot is not None else "error"
    except BaseException as error:
        scrub_exception(error)
        return "error"
    finally:
        if workspace._snapshot is not snapshot:
            close_unpublished_nodes(logs, latch)


def _settle_late_logs(
    workspace: RelayArtifactWorkspace,
    run_fd: int,
    latch: TlsControlLatch,
) -> bool:
    retained = workspace._snapshot
    if retained is not None:
        if retained.nodes or retained.log is None:
            return False
        if retained.log.valid is not True:
            workspace._publication_safe = False
        _discard_relay_artifact_contents(workspace)
        if not remove_node(retained.log, latch) or not _release_snapshot(
            workspace,
            latch,
        ):
            return False
    consecutive_absent = 0
    for _ in range(_FINAL_LOG_SCANS):
        playwright_status, _ = stat_name_status(run_fd, "playwright", latch)
        if playwright_status != "absent":
            return False
        log_status = _capture_fresh_log(workspace, run_fd, latch)
        if log_status == "error":
            return False
        if log_status == "found":
            consecutive_absent = 0
            snapshot = workspace._snapshot
            if snapshot is None or snapshot.log is None:
                return False
            if snapshot.log.valid is not True:
                workspace._publication_safe = False
            _discard_relay_artifact_contents(workspace)
            if not remove_node(snapshot.log, latch) or not _release_snapshot(
                workspace,
                latch,
            ):
                return False
            continue
        consecutive_absent += 1
        if not _hook("late-log-absence-observed", latch):
            return False
        if consecutive_absent == 2:
            return True
    return False


def _append_log(logs: list[ArtifactNode], node: ArtifactNode) -> bool:
    if all(current is not node for current in logs):
        logs.append(node)
    return True


def _ensure_playwright_root(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> bool:
    if workspace._playwright_root is not None:
        return True
    run = workspace._run_binding
    playwright = workspace._playwright_binding
    details = None if playwright is None else fstat(playwright.descriptor, latch)
    if (
        run is None
        or playwright is None
        or details is None
        or file_identity(details) != playwright.identity
    ):
        return False
    root = ArtifactNode(
        parent_descriptor=run.descriptor,
        name="playwright",
        details=details,
    )
    root.descriptor = playwright.descriptor
    root.borrowed_descriptor = True
    workspace._playwright_root = root
    return True


def _close_workspace_bindings(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
    *,
    complete: bool = False,
) -> bool:
    for attribute in (
        "_ephemeral_binding",
        "_playwright_binding",
        "_run_binding",
    ):
        while getattr(workspace, attribute) is not None:
            try:
                binding = getattr(workspace, attribute)
                if binding is None:
                    break
                if not close_owned_descriptor(binding.descriptor, binding.identity, latch):
                    return False
                setattr(workspace, attribute, None)
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
            except BaseException as error:
                scrub_exception(error)
                return False
    return not complete or _publish_workspace_complete(workspace, latch)


def _publish_workspace_complete(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> bool:
    published = workspace._complete
    while not published:
        try:
            workspace._complete = True
            published = True
            _artifact_cleanup_boundary_hook("workspace-complete-published")
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            published = workspace._complete
        except BaseException as error:
            scrub_exception(error)
            return workspace._complete
    return True


def _artifact_cleanup_boundary_hook(_phase: str) -> None:
    """Secret-free deterministic seam for terminal publication tests."""

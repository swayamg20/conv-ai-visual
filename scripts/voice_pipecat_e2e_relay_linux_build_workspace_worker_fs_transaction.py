"""Prepare, publish, hold, and clean one workspace on the exact worker stack."""

from __future__ import annotations

import os
import stat
import threading
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_binding import (
    _workspace_worker_claim_state,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_prestart import (
    _new_workspace_build_prestart_authority,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_transaction import (
    _run_workspace_build_transaction,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_cleanup import (
    _cleanup_empty_root,
    _cleanup_workspace_root,
    _EmptyRootCleanupState,
    _WorkspaceCleanupState,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _activate_workspace_prepared_receipt,
    _new_workspace_prepared_receipt,
    _publish_workspace_filesystem_settlement,
    _revoke_workspace_prepared_receipt,
    _workspace_filesystem_is_settled,
    _workspace_prepared_receipt_is_revoked,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspacePreparedReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_copy import (
    _copy_workspace_source,
    _manifest_digest,
    _snapshot_workspace_copy,
    _snapshot_workspace_source,
    _source_signature,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _bounded_names,
    _create_directory_at,
    _create_symlink_at,
    _open_absolute_directory,
    _open_directory_at,
    _open_regular_at,
    _require_cooperative_node,
    _require_named_identity,
    _require_private_parent,
    _stable_binding,
    _WorkspaceCreationIntent,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_workspace import (
    _snapshot_workspace_build_inputs,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_provenance import (
    _fingerprint,
    _open_absolute_regular,
    _open_relative_regular,
    _revalidate_named_anchors,
    _snapshot_tools,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerClaim,
)

_PUBLISH_SECONDS = 0.05
_HOLD_SECONDS = 0.01
_RETRY_MAX_SECONDS = 0.05


def _run_workspace_filesystem_transaction(claim: _WorkspaceWorkerClaim) -> bool:
    """Return only after exact cleanup; path and descriptor state stays local."""

    current = threading.current_thread()
    if (
        type(claim) is not _WorkspaceWorkerClaim
        or type(current) is not registry._WorkspaceWorkerThread
        or claim._paths_cleared is not False
        or claim._request is None
        or claim._prepared_destination is None
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    if not _claim_is_current(claim, current):
        raise _WorkspaceFilesystemError(_FAILURE)
    request = claim._request
    controller = claim._controller
    descriptors = _WorkspaceDescriptorSet()
    source_fd = run_parent_fd = run_root_fd = workspace_fd = None
    node_fd = node_modules_fd = next_fd = playwright_fd = None
    node_lock_fd = next_package_fd = playwright_package_fd = None
    run_identity = workspace_identity = None
    receipt: _WorkspacePreparedReceipt | None = None
    root_created = False
    workspace_created = False
    cleaned = False
    cleanup_state: _WorkspaceCleanupState | None = None
    empty_cleanup_state: _EmptyRootCleanupState | None = None
    run_intent: _WorkspaceCreationIntent | None = None
    workspace_intent: _WorkspaceCreationIntent | None = None
    try:
        if controller._cancellation_requested() is True:
            raise _WorkspaceFilesystemError(_FAILURE)
        source_fd = _open_absolute_directory(request._source_root, descriptors)
        source_identity = _require_cooperative_node(source_fd, directory=True)
        run_parent_fd = _open_absolute_directory(request._run_parent, descriptors)
        run_parent_identity = _require_private_parent(run_parent_fd)
        node_fd = _open_absolute_regular(request._node, descriptors, executable=True)
        node_modules_fd = _open_directory_at(source_fd, "node_modules", descriptors)
        node_modules_identity = _require_cooperative_node(node_modules_fd, directory=True)
        next_fd = _open_relative_regular(
            node_modules_fd,
            ("next", "dist", "bin", "next"),
            descriptors,
            executable=True,
        )
        playwright_fd = _open_relative_regular(
            node_modules_fd,
            ("@playwright", "test", "cli.js"),
            descriptors,
            executable=False,
        )
        node_lock_fd = _open_regular_at(
            node_modules_fd,
            ".package-lock.json",
            descriptors,
        )
        next_package_fd = _open_relative_regular(
            node_modules_fd,
            ("next", "package.json"),
            descriptors,
            executable=False,
        )
        playwright_package_fd = _open_relative_regular(
            node_modules_fd,
            ("@playwright", "test", "package.json"),
            descriptors,
            executable=False,
        )
        tool_values = _snapshot_tools(
            node_fd=node_fd,
            next_fd=next_fd,
            playwright_fd=playwright_fd,
            node_lock_fd=node_lock_fd,
            next_package_fd=next_package_fd,
            playwright_package_fd=playwright_package_fd,
            node_modules_identity=node_modules_identity,
            controller=controller,
        )
        if controller._cancellation_requested() is True:
            raise _WorkspaceFilesystemError(_FAILURE)
        run_intent = _WorkspaceCreationIntent(
            parent=run_parent_fd,
            name=request._run_root.name,
            kind="directory",
        )
        run_root_fd, run_identity = _create_directory_at(
            run_parent_fd,
            request._run_root.name,
            descriptors,
            run_intent,
        )
        if run_identity.device != run_parent_identity.device:
            raise _WorkspaceFilesystemError(_FAILURE)
        root_created = True
        workspace_intent = _WorkspaceCreationIntent(
            parent=run_root_fd,
            name=request._workspace.name,
            kind="directory",
        )
        workspace_fd, workspace_identity = _create_directory_at(
            run_root_fd,
            request._workspace.name,
            descriptors,
            workspace_intent,
        )
        if workspace_identity.device != run_identity.device:
            raise _WorkspaceFilesystemError(_FAILURE)
        workspace_created = True
        entries, directories, max_nodes, max_bytes, max_depth = request._copy_policy()
        first_source = _copy_workspace_source(
            source_fd=source_fd,
            workspace_fd=workspace_fd,
            entries=entries,
            directory_entries=directories,
            max_nodes=max_nodes,
            max_bytes=max_bytes,
            max_depth=max_depth,
            descriptors=descriptors,
            controller=controller,
        )
        symlink_intent = _WorkspaceCreationIntent(
            parent=workspace_fd,
            name="node_modules",
            kind="symlink",
        )
        node_modules_link = _create_symlink_at(
            workspace_fd,
            "node_modules",
            str(request._node_modules),
            symlink_intent,
        )
        if node_modules_link.device != workspace_identity.device:
            raise _WorkspaceFilesystemError(_FAILURE)
        os.fsync(workspace_fd)
        second_source = _snapshot_workspace_source(
            source_fd=source_fd,
            entries=entries,
            directory_entries=directories,
            max_nodes=max_nodes,
            max_bytes=max_bytes,
            max_depth=max_depth,
            descriptors=descriptors,
            controller=controller,
        )
        if second_source != first_source:
            raise _WorkspaceFilesystemError(_FAILURE)
        source_signature = _source_signature(first_source)
        copied_nodes = _snapshot_workspace_copy(
            workspace_fd=workspace_fd,
            expected=first_source,
            node_modules_target=str(request._node_modules),
            descriptors=descriptors,
            controller=controller,
        )
        if _source_signature(copied_nodes) != source_signature:
            raise _WorkspaceFilesystemError(_FAILURE)
        if _bounded_names(run_root_fd, 2) != (request._workspace.name,):
            raise _WorkspaceFilesystemError(_FAILURE)
        if (
            _snapshot_tools(
                node_fd=node_fd,
                next_fd=next_fd,
                playwright_fd=playwright_fd,
                node_lock_fd=node_lock_fd,
                next_package_fd=next_package_fd,
                playwright_package_fd=playwright_package_fd,
                node_modules_identity=node_modules_identity,
                controller=controller,
            )
            != tool_values
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        _revalidate_named_anchors(
            request=request,
            source_identity=source_identity,
            run_parent_identity=run_parent_identity,
            tool_values=tool_values,
            descriptors=descriptors,
            controller=controller,
        )
        if controller._cancellation_requested() is True:
            raise _WorkspaceFilesystemError(_FAILURE)
        baseline = _snapshot_workspace_build_inputs(
            workspace_fd=workspace_fd,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            run_id=request._run_id,
            expected_destination=copied_nodes,
            expected_node_modules=node_modules_link,
            node_modules_target=str(request._node_modules),
            descriptors=descriptors,
            controller=controller,
        )
        fingerprint = _fingerprint(_manifest_digest(source_signature), tool_values)
        receipt = _new_workspace_prepared_receipt(
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            fingerprint=fingerprint,
        )
        _publish_prepared_receipt(claim, request, receipt, current)
        prestart = _new_workspace_build_prestart_authority(
            claim=claim,
            current=current,
            prepared=receipt,
            source_fd=source_fd,
            source_identity=source_identity,
            source_nodes=first_source,
            run_parent_fd=run_parent_fd,
            run_parent_identity=run_parent_identity,
            run_root_fd=run_root_fd,
            run_identity=run_identity,
            workspace_fd=workspace_fd,
            workspace_identity=workspace_identity,
            node_fd=node_fd,
            next_fd=next_fd,
            playwright_fd=playwright_fd,
            node_lock_fd=node_lock_fd,
            next_package_fd=next_package_fd,
            playwright_package_fd=playwright_package_fd,
            node_modules_identity=node_modules_identity,
            tool_values=tool_values,
            baseline=baseline,
            descriptors=descriptors,
        )
        if not _run_workspace_build_transaction(
            claim=claim,
            prepared=receipt,
            baseline=baseline,
            prestart=prestart,
            run_parent_fd=run_parent_fd,
            run_root_fd=run_root_fd,
            run_identity=run_identity,
            workspace_fd=workspace_fd,
            workspace_identity=workspace_identity,
            descriptors=descriptors,
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
    except (KeyboardInterrupt, SystemExit) as control:
        controller._capture_control(control)
    except BaseException as error:
        _scrub_control_minimal(error)
        controller._request_cancel()
    finally:
        revoked = receipt is None
        retry_delay = _HOLD_SECONDS
        while not cleaned:
            try:
                if not revoked:
                    _revoke_workspace_prepared_receipt(
                        receipt,
                        claim._owner_token,
                        claim._record_token,
                    )
                    revoked = _workspace_prepared_receipt_is_revoked(
                        receipt,
                        claim._owner_token,
                        claim._record_token,
                    )
                    if not revoked:
                        raise _WorkspaceFilesystemError(_FAILURE)
                if cleanup_state is None and empty_cleanup_state is None:
                    _reconcile_pending_directory_intent(run_intent)
                    _reconcile_pending_directory_intent(workspace_intent)
                    if run_intent is not None and run_intent.returned:
                        run_root_fd, run_identity = _recover_created_directory(
                            run_parent_fd,
                            request._run_root.name,
                            run_intent,
                            run_root_fd,
                            run_identity,
                            descriptors,
                        )
                        root_created = True
                    if workspace_intent is not None and workspace_intent.returned:
                        if not root_created or run_root_fd is None:
                            raise _WorkspaceFilesystemError(_FAILURE)
                        workspace_fd, workspace_identity = _recover_created_directory(
                            run_root_fd,
                            request._workspace.name,
                            workspace_intent,
                            workspace_fd,
                            workspace_identity,
                            descriptors,
                        )
                        workspace_created = True
                if root_created and workspace_created:
                    if not all(
                        isinstance(value, int)
                        for value in (run_parent_fd, run_root_fd, workspace_fd)
                    ) or not all(
                        type(value) is _WorkspaceFilesystemIdentity
                        for value in (run_identity, workspace_identity)
                    ):
                        raise _WorkspaceFilesystemError(_FAILURE)
                    if cleanup_state is None:
                        cleanup_state = _WorkspaceCleanupState(
                            run_parent_fd=run_parent_fd,
                            run_name=request._run_root.name,
                            run_root_fd=run_root_fd,
                            run_identity=run_identity,
                            workspace_name=request._workspace.name,
                            workspace_fd=workspace_fd,
                            workspace_identity=workspace_identity,
                            node_modules_target=str(request._node_modules),
                            descriptors=descriptors,
                        )
                    if not _cleanup_workspace_root(cleanup_state):
                        raise _WorkspaceFilesystemError(_FAILURE)
                    root_created = workspace_created = False
                    run_intent = workspace_intent = None
                elif root_created:
                    if not isinstance(run_parent_fd, int) or not isinstance(run_root_fd, int):
                        raise _WorkspaceFilesystemError(_FAILURE)
                    if type(run_identity) is not _WorkspaceFilesystemIdentity:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    if empty_cleanup_state is None:
                        empty_cleanup_state = _EmptyRootCleanupState(
                            run_parent_fd=run_parent_fd,
                            run_name=request._run_root.name,
                            run_root_fd=run_root_fd,
                            run_identity=run_identity,
                            descriptors=descriptors,
                        )
                    if not _cleanup_empty_root(empty_cleanup_state):
                        raise _WorkspaceFilesystemError(_FAILURE)
                    root_created = False
                    run_intent = workspace_intent = None
                intents_resolved = all(
                    _creation_intent_is_resolved(intent)
                    for intent in (run_intent, workspace_intent)
                )
                cleaned = bool(revoked and intents_resolved and descriptors.close_all())
            except (KeyboardInterrupt, SystemExit) as control:
                controller._capture_control(control)
            except BaseException as error:
                _scrub_control_minimal(error)
            if not cleaned:
                controller._wait(retry_delay)
                retry_delay = min(_RETRY_MAX_SECONDS, retry_delay * 2.0)
    settled = bool(cleaned and descriptors.is_empty() and not root_created)
    if not settled:
        raise _WorkspaceFilesystemError(_FAILURE)
    retry_delay = _HOLD_SECONDS
    while True:
        try:
            if not _claim_is_current(claim, current):
                raise _WorkspaceFilesystemError(_FAILURE)
            _publish_workspace_filesystem_settlement(
                claim._owner_token,
                claim._record_token,
                claim._claim_token,
            )
            if _workspace_filesystem_is_settled(
                claim._owner_token,
                claim._record_token,
                claim._claim_token,
            ):
                return True
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        controller._wait(retry_delay)
        retry_delay = min(_RETRY_MAX_SECONDS, retry_delay * 2.0)


def _publish_prepared_receipt(
    claim: _WorkspaceWorkerClaim,
    request: object,
    receipt: _WorkspacePreparedReceipt,
    current: registry._WorkspaceWorkerThread,
) -> None:
    publish_error: BaseException | None = None
    publish_deadline = time.monotonic() + _PUBLISH_SECONDS
    try:
        claim._prepared_destination._publish_before(
            request,
            claim._owner_token,
            receipt,
            publish_deadline,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        publish_error = error
    stored = None
    acquired = False
    for _attempt in range(3):
        try:
            stored, acquired = claim._prepared_destination._read_before(
                request,
                time.monotonic() + _PUBLISH_SECONDS,
            )
        except (KeyboardInterrupt, SystemExit):
            if publish_error is not None:
                _scrub_control_minimal(publish_error)
            raise
        except BaseException as error:
            _scrub_control_minimal(error)
            continue
        if acquired:
            break
    if publish_error is not None:
        _scrub_control_minimal(publish_error)
    if (
        not acquired
        or stored is not receipt
        or claim._controller._cancellation_requested() is True
        or not _claim_is_current(claim, current)
        or not _activate_workspace_prepared_receipt(
            receipt,
            claim._owner_token,
            claim._record_token,
        )
        or not receipt._matches(
            claim._owner_token,
            claim._record_token,
            require_active=True,
        )
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _creation_intent_is_resolved(intent: _WorkspaceCreationIntent | None) -> bool:
    if intent is None or not intent.entered or intent.returned:
        return True
    if not intent.resolved_no_effect:
        return False
    if not intent.collision:
        return True
    parent = _WorkspaceFilesystemIdentity.from_stat(os.fstat(intent.parent))
    if _stable_binding(parent) != intent.parent_binding:
        raise _WorkspaceFilesystemError(_FAILURE)
    try:
        os.stat(intent.name, dir_fd=intent.parent, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


def _claim_is_current(
    claim: _WorkspaceWorkerClaim,
    current: registry._WorkspaceWorkerThread,
) -> bool:
    retry_delay = _HOLD_SECONDS
    while True:
        state = _workspace_worker_claim_state(
            claim,
            current,
            time.monotonic() + _PUBLISH_SECONDS,
        )
        if state is not None:
            return state
        claim._controller._wait(retry_delay)
        retry_delay = min(_RETRY_MAX_SECONDS, retry_delay * 2.0)


def _recover_created_directory(
    parent: int | None,
    name: str,
    intent: _WorkspaceCreationIntent,
    descriptor: int | None,
    identity: _WorkspaceFilesystemIdentity | None,
    descriptors: _WorkspaceDescriptorSet,
) -> tuple[int, _WorkspaceFilesystemIdentity]:
    if not isinstance(parent, int) or not intent.returned:
        raise _WorkspaceFilesystemError(_FAILURE)
    canonical = identity if type(identity) is _WorkspaceFilesystemIdentity else intent.identity
    if canonical is None:
        canonical = _WorkspaceFilesystemIdentity.from_stat(
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        )
        if not canonical.is_directory() or stat.S_IMODE(canonical.mode) != 0o700:
            raise _WorkspaceFilesystemError(_FAILURE)
        intent.bind_identity(canonical)
    candidate = descriptor
    if candidate is None:
        candidate = descriptors.find(canonical)
    if candidate is None:
        candidate = _open_directory_at(parent, name, descriptors)
    opened = _require_named_identity(parent, name, candidate, directory=True)
    if _stable_binding(opened) != _stable_binding(canonical):
        raise _WorkspaceFilesystemError(_FAILURE)
    return candidate, canonical


def _reconcile_pending_directory_intent(
    intent: _WorkspaceCreationIntent | None,
) -> None:
    if (
        intent is None
        or not intent.entered
        or intent.returned
        or intent.resolved_no_effect
        or intent.kind != "directory"
    ):
        return
    try:
        identity = _WorkspaceFilesystemIdentity.from_stat(
            os.stat(intent.name, dir_fd=intent.parent, follow_symlinks=False)
        )
    except FileNotFoundError:
        intent.mark_no_effect()
        return
    if not identity.is_directory() or stat.S_IMODE(identity.mode) != 0o700:
        raise _WorkspaceFilesystemError(_FAILURE)
    intent.reconcile_returned(identity)


__all__: list[str] = []

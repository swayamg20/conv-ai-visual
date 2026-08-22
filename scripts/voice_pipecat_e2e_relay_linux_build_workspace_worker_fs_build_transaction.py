"""Worker-local prepared-to-built transaction and ordered build cleanup."""

from __future__ import annotations

import os
import threading
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process import (
    _cancel_associated_workspace_build_process,
    _drive_workspace_build_process,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _complete_failed_workspace_build_process,
    _workspace_build_process_association_phase,
    _workspace_build_process_completed_zero,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_publication import (
    _publish_workspace_built_receipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_BY_COMMAND,
    _BUILT_LEASES,
    _revoke_workspace_built_receipt,
    _workspace_built_receipt_is_revoked,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMAND_CONTROLLERS,
    _COMMAND_GATES,
    _COMMANDS,
    _CONTROLLER_COMMANDS,
    _PROCESS_ASSOCIATIONS,
    _claim_workspace_build_command,
    _WorkspaceBuildCommand,
    _WorkspaceBuildCommandGate,
    _WorkspaceBuildHandoffError,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_claim_cleanup import (
    _reconcile_revoked_prepared_build_for_cleanup,
    _workspace_revoked_prepared_build_for_cleanup_matches,
    _workspace_revoked_prepared_lease_is_singleton,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_prestart import (
    _revalidate_workspace_build_postprocess,
    _WorkspaceBuildPrestartAuthority,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_scope_cleanup import (
    _cleanup_workspace_build_scope,
    _new_workspace_build_scope_cleanup_state,
    _WorkspaceBuildScopeCleanupState,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _PREPARED_BUILDS,
    _revoke_workspace_prepared_receipt,
    _workspace_prepared_receipt_is_revoked,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspacePreparedReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output import (
    _validate_workspace_build_output,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_cleanup import (
    _new_workspace_build_output_cleanup_state,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspacePreparedDestinationBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerClaim,
)

_SLOT_SECONDS = 0.05
_CLEANUP_SECONDS = 5.0
_RETRY_SECONDS = 0.01
_RETRY_MAX_SECONDS = 0.05


def _run_workspace_build_transaction(
    *,
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    baseline: _WorkspacePreparedDestinationBaseline,
    prestart: _WorkspaceBuildPrestartAuthority,
    run_parent_fd: int,
    run_root_fd: int,
    run_identity: _WorkspaceFilesystemIdentity,
    workspace_fd: int,
    workspace_identity: _WorkspaceFilesystemIdentity,
    descriptors: _WorkspaceDescriptorSet,
) -> bool:
    """Hold the built lease, then return only after build-scope cleanup."""

    if (
        type(claim) is not _WorkspaceWorkerClaim
        or type(prepared) is not _WorkspacePreparedReceipt
        or type(baseline) is not _WorkspacePreparedDestinationBaseline
        or type(prestart) is not _WorkspaceBuildPrestartAuthority
        or type(run_parent_fd) is not int
        or type(run_root_fd) is not int
        or type(run_identity) is not _WorkspaceFilesystemIdentity
        or type(workspace_fd) is not int
        or type(workspace_identity) is not _WorkspaceFilesystemIdentity
        or type(descriptors) is not _WorkspaceDescriptorSet
        or prestart.claim is not claim
        or prestart.prepared is not prepared
        or prestart.baseline is not baseline
        or prestart.run_parent_fd != run_parent_fd
        or prestart.run_root_fd != run_root_fd
        or prestart.run_identity is not run_identity
        or prestart.workspace_fd != workspace_fd
        or prestart.workspace_identity is not workspace_identity
        or prestart.descriptors is not descriptors
        or threading.current_thread() is not prestart.current
        or claim._request is None
        or claim._command_destination is not claim._bundle._command_destination
        or claim._built_destination is not claim._bundle._built_destination
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    command: _WorkspaceBuildCommand | None = None
    built: _WorkspaceBuiltReceipt | None = None
    process_receipt = None
    build_deadline = None
    scope_state: _WorkspaceBuildScopeCleanupState | None = None
    try:
        command = _wait_for_workspace_build_command(claim, prepared)
        if command is None:
            raise _WorkspaceBuildHandoffError(_FAILURE)
        build_deadline = _claim_workspace_build_command(
            command,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            prepared=prepared,
        )
        process_receipt = _drive_workspace_build_process(
            command=command,
            request=claim._request,
            controller=claim._controller,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            build_deadline=build_deadline,
            prestart_authority=prestart,
        )
        if not _workspace_build_process_completed_zero(
            command,
            process_receipt,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        if not _revalidate_workspace_build_postprocess(
            prestart,
            command=command,
            process_receipt=process_receipt,
            controller=claim._controller,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        first = _validate_workspace_build_output(
            workspace_fd=workspace_fd,
            workspace=claim._request._workspace,
            baseline=baseline,
            run_id=claim._request._run_id,
            descriptors=descriptors,
            controller=claim._controller,
        )
        second = _validate_workspace_build_output(
            workspace_fd=workspace_fd,
            workspace=claim._request._workspace,
            baseline=baseline,
            run_id=claim._request._run_id,
            descriptors=descriptors,
            controller=claim._controller,
        )
        if first != second or not _revalidate_workspace_build_postprocess(
            prestart,
            command=command,
            process_receipt=process_receipt,
            controller=claim._controller,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        built = _publish_workspace_built_receipt(
            bundle=claim._bundle,
            command=command,
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            output_digest=first.digest,
            process_receipt=process_receipt,
            operation_deadline=build_deadline,
        )
        _revoke_prepared_after_built(claim, prepared, built)
        while claim._controller._cancellation_requested() is False and built._matches(
            claim._owner_token,
            claim._record_token,
            require_active=True,
        ):
            claim._controller._wait(_RETRY_SECONDS)
        if claim._controller._cancellation_requested() is False:
            raise _WorkspaceBuildHandoffError(_FAILURE)
    except (KeyboardInterrupt, SystemExit) as control:
        claim._controller._capture_control(control)
    except BaseException as error:
        _scrub_control_minimal(error)
        claim._controller._request_cancel()
    command = _resolve_workspace_build_command(claim, prepared, command)
    return _settle_workspace_build_transaction(
        claim=claim,
        prepared=prepared,
        baseline=baseline,
        command=command,
        built=built,
        run_parent_fd=run_parent_fd,
        run_root_fd=run_root_fd,
        run_identity=run_identity,
        workspace_fd=workspace_fd,
        workspace_identity=workspace_identity,
        descriptors=descriptors,
        scope_state=scope_state,
    )


def _wait_for_workspace_build_command(
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
) -> _WorkspaceBuildCommand | None:
    while claim._controller._cancellation_requested() is False:
        deadline = time.monotonic() + _SLOT_SECONDS
        stored, acquired = claim._command_destination._read_before(
            claim._owner_token,
            deadline,
        )
        if not acquired:
            continue
        if stored is None:
            claim._controller._wait(_RETRY_SECONDS)
            continue
        if type(stored) is not _WorkspaceBuildCommand or not stored._matches(
            claim._owner_token,
            claim._record_token,
            prepared,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        return stored
    return None


def _revoke_prepared_after_built(
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    built: _WorkspaceBuiltReceipt,
) -> None:
    """Explicit ordering seam: built is active before prepared revocation."""

    if not built._matches(
        claim._owner_token,
        claim._record_token,
        require_active=True,
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    _revoke_workspace_prepared_receipt(
        prepared,
        claim._owner_token,
        claim._record_token,
    )
    if not (
        _workspace_prepared_receipt_is_revoked(
            prepared,
            claim._owner_token,
            claim._record_token,
        )
        and built._matches(
            claim._owner_token,
            claim._record_token,
            require_active=True,
        )
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _settle_workspace_build_transaction(
    *,
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    baseline: _WorkspacePreparedDestinationBaseline,
    command: _WorkspaceBuildCommand | None,
    built: _WorkspaceBuiltReceipt | None,
    run_parent_fd: int,
    run_root_fd: int,
    run_identity: _WorkspaceFilesystemIdentity,
    workspace_fd: int,
    workspace_identity: _WorkspaceFilesystemIdentity,
    descriptors: _WorkspaceDescriptorSet,
    scope_state: _WorkspaceBuildScopeCleanupState | None,
) -> bool:
    delay = _RETRY_SECONDS
    while True:
        try:
            if command is None:
                command = _resolve_workspace_build_command(claim, prepared, None)
            built = _resolve_workspace_built_receipt(claim, command, built)
            if command is not None:
                if not _cancel_associated_workspace_build_process(
                    command,
                    cleanup_deadline=time.monotonic() + _CLEANUP_SECONDS,
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
                if built is not None:
                    _revoke_workspace_built_receipt(
                        built,
                        claim._owner_token,
                        claim._record_token,
                        cleanup_deadline=time.monotonic() + _CLEANUP_SECONDS,
                    )
                    if not _workspace_built_receipt_is_revoked(
                        built,
                        claim._owner_token,
                        claim._record_token,
                    ):
                        raise _WorkspaceFilesystemError(_FAILURE)
                elif not _complete_failed_workspace_build_process(command):
                    raise _WorkspaceFilesystemError(_FAILURE)
            _revoke_workspace_prepared_receipt(
                prepared,
                claim._owner_token,
                claim._record_token,
            )
            if not _workspace_prepared_receipt_is_revoked(
                prepared,
                claim._owner_token,
                claim._record_token,
            ):
                raise _WorkspaceFilesystemError(_FAILURE)
            association_phase = (
                None if command is None else _workspace_build_process_association_phase(command)
            )
            if command is None:
                if not _workspace_build_state_is_absent(claim, prepared, built):
                    raise _WorkspaceFilesystemError(_FAILURE)
                if not _reserved_output_parent_is_absent(workspace_fd):
                    raise _WorkspaceFilesystemError(_FAILURE)
                return True
            if association_phase is None:
                state = _COMMANDS.get(command)
                if (
                    type(state) is not tuple
                    or len(state) != 6
                    or not _reconcile_revoked_prepared_build_for_cleanup(
                        prepared,
                        claim._owner_token,
                        claim._record_token,
                        command,
                        state[3],
                    )
                    or not _workspace_command_without_process_is_safe(
                        claim,
                        prepared,
                        command,
                        built,
                    )
                    or not _reserved_output_parent_is_absent(workspace_fd)
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
                return True
            if scope_state is None:
                output_state = _new_workspace_build_output_cleanup_state(
                    command=command,
                    prepared=prepared,
                    baseline=baseline,
                    owner_token=claim._owner_token,
                    record_token=claim._record_token,
                    request=claim._request,
                    workspace_fd=workspace_fd,
                    workspace_identity=workspace_identity,
                    run_id=claim._request._run_id,
                    descriptors=descriptors,
                )
                scope_state = _new_workspace_build_scope_cleanup_state(
                    output_state=output_state,
                    run_parent_fd=run_parent_fd,
                    run_name=claim._request._run_root.name,
                    run_root_fd=run_root_fd,
                    run_identity=run_identity,
                    workspace_name=claim._request._workspace.name,
                    descriptors=descriptors,
                )
            if _cleanup_workspace_build_scope(scope_state):
                return True
        except (KeyboardInterrupt, SystemExit) as control:
            claim._controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        _wait_for_cleanup_retry(claim, delay)
        delay = min(_RETRY_MAX_SECONDS, delay * 2.0)


def _resolve_workspace_build_command(
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    retained: _WorkspaceBuildCommand | None,
) -> _WorkspaceBuildCommand | None:
    delay = _RETRY_SECONDS
    while True:
        try:
            stored, acquired = claim._command_destination._read_before(
                claim._owner_token,
                time.monotonic() + _SLOT_SECONDS,
            )
            if acquired and stored is None and retained is None:
                return None
            if (
                acquired
                and type(stored) is _WorkspaceBuildCommand
                and stored._matches(
                    claim._owner_token,
                    claim._record_token,
                    prepared,
                )
                and (retained is None or stored is retained)
            ):
                return stored
            if acquired:
                raise _WorkspaceFilesystemError(_FAILURE)
        except (KeyboardInterrupt, SystemExit) as control:
            claim._controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        _wait_for_cleanup_retry(claim, delay)
        delay = min(_RETRY_MAX_SECONDS, delay * 2.0)


def _resolve_workspace_built_receipt(
    claim: _WorkspaceWorkerClaim,
    command: _WorkspaceBuildCommand | None,
    retained: _WorkspaceBuiltReceipt | None,
) -> _WorkspaceBuiltReceipt | None:
    delay = _RETRY_SECONDS
    while True:
        try:
            canonical = _BUILT_BY_COMMAND.get(command) if command is not None else None
            stored, acquired = claim._built_destination._read_before(
                claim._owner_token,
                time.monotonic() + _SLOT_SECONDS,
            )
            if acquired:
                candidates = tuple(
                    candidate
                    for candidate in (retained, canonical, stored)
                    if candidate is not None
                )
                if not candidates:
                    return None
                first = candidates[0]
                if type(first) is _WorkspaceBuiltReceipt and all(
                    value is first for value in candidates
                ):
                    return first
                raise _WorkspaceFilesystemError(_FAILURE)
        except (KeyboardInterrupt, SystemExit) as control:
            claim._controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        _wait_for_cleanup_retry(claim, delay)
        delay = min(_RETRY_MAX_SECONDS, delay * 2.0)


def _reserved_output_parent_is_absent(workspace_fd: int) -> bool:
    try:
        os.stat(".next-voice-e2e", dir_fd=workspace_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


def _workspace_build_state_is_absent(
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    built: _WorkspaceBuiltReceipt | None,
) -> bool:
    """Prove no command or process graph exists before no-effect cleanup."""

    return bool(
        built is None
        and not _COMMANDS
        and not _COMMAND_GATES
        and not _COMMAND_CONTROLLERS
        and not _CONTROLLER_COMMANDS
        and not _PROCESS_ASSOCIATIONS
        and not _PREPARED_BUILDS
        and _workspace_revoked_prepared_lease_is_singleton(
            prepared,
            claim._owner_token,
            claim._record_token,
        )
        and not _BUILT_BY_COMMAND
        and not _BUILT_LEASES
        and _build_process_registries_are_empty()
        and claim._controller._cancellation_requested()
    )


def _workspace_command_without_process_is_safe(
    claim: _WorkspaceWorkerClaim,
    prepared: _WorkspacePreparedReceipt,
    command: _WorkspaceBuildCommand,
    built: _WorkspaceBuiltReceipt | None,
) -> bool:
    """Prove one cancelled command never obtained process authority."""

    state = _COMMANDS.get(command)
    gate = _COMMAND_GATES.get(command)
    controller_reference = _COMMAND_CONTROLLERS.get(command)
    command_reference = _CONTROLLER_COMMANDS.get(claim._controller)
    bound_controller = controller_reference() if controller_reference is not None else None
    bound_command = command_reference() if command_reference is not None else None
    bindings_absent = controller_reference is None and command_reference is None
    bindings_exact = bound_controller is claim._controller and bound_command is command
    return bool(
        built is None
        and len(_COMMANDS) == 1
        and len(_COMMAND_GATES) == 1
        and next(iter(_COMMANDS)) is command
        and next(iter(_COMMAND_GATES)) is command
        and type(gate) is _WorkspaceBuildCommandGate
        and gate.cancel_requested
        and type(state) is tuple
        and len(state) == 6
        and state[4] in {"cancelled", "failed"}
        and _workspace_revoked_prepared_build_for_cleanup_matches(
            prepared,
            claim._owner_token,
            claim._record_token,
            command,
            state[3],
        )
        and not _PROCESS_ASSOCIATIONS
        and not _BUILT_BY_COMMAND
        and not _BUILT_LEASES
        and (
            (bindings_absent and not _COMMAND_CONTROLLERS and not _CONTROLLER_COMMANDS)
            or (
                bindings_exact and len(_COMMAND_CONTROLLERS) == 1 and len(_CONTROLLER_COMMANDS) == 1
            )
        )
        and _build_process_registries_are_empty()
    )


def _wait_for_cleanup_retry(claim: _WorkspaceWorkerClaim, delay: float) -> None:
    try:
        claim._controller._wait(delay)
    except (KeyboardInterrupt, SystemExit) as control:
        claim._controller._capture_control(control)
    except BaseException as error:
        _scrub_control_minimal(error)


__all__: list[str] = []

"""Deadline-bounded publication of one canonical workspace-built receipt."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _activate_workspace_built_receipt,
    _new_workspace_built_receipt_for_publication,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract import (
    _canonical_workspace_built_deadline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_reconcile import (
    _revoke_uncommitted_workspace_built_candidate,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _FAILURE,
    _acquire_command_publication_gate,
    _WorkspaceBuildCommand,
    _WorkspaceBuildHandoffError,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspaceBuiltRuntimeProof,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _DESTINATION_TOKEN,
    _WorkspaceWorkerBundle,
)


def _publish_workspace_built_receipt(
    *,
    bundle: _WorkspaceWorkerBundle,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    output_digest: bytes,
    runtime_proof: _WorkspaceBuiltRuntimeProof,
    process_receipt: object,
    operation_deadline: float,
) -> _WorkspaceBuiltReceipt:
    """Publish and activate only within the command's immutable deadline."""

    if type(bundle) is not _WorkspaceWorkerBundle or not bundle._built_destination._matches(
        owner_token, "built"
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    build_deadline = _canonical_workspace_built_deadline(
        command,
        owner_token,
        record_token,
        operation_deadline,
    )
    publication_gate = _acquire_command_publication_gate(command, build_deadline)
    try:
        receipt, already_active = _new_workspace_built_receipt_for_publication(
            command=command,
            owner_token=owner_token,
            record_token=record_token,
            output_digest=output_digest,
            runtime_proof=runtime_proof,
            process_receipt=process_receipt,
            operation_deadline=build_deadline,
        )
        try:
            if not receipt._matches(owner_token, record_token):
                raise _WorkspaceBuildHandoffError(_FAILURE)
            try:
                published, acquired = bundle._built_destination._publish_before(
                    _DESTINATION_TOKEN,
                    owner_token,
                    receipt,
                    build_deadline,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                published, acquired = None, False
            if (acquired and published is not receipt) or not receipt._matches(
                owner_token, record_token
            ):
                raise _WorkspaceBuildHandoffError(_FAILURE)
            try:
                stored, read = bundle._built_destination._read_before(
                    owner_token,
                    build_deadline,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if not receipt._matches(owner_token, record_token):
                    raise _WorkspaceBuildHandoffError(_FAILURE) from None
                stored, read = bundle._built_destination._read_before(
                    owner_token,
                    build_deadline,
                )
            if not read or stored is not receipt or not receipt._matches(owner_token, record_token):
                raise _WorkspaceBuildHandoffError(_FAILURE)
            if not _activate_workspace_built_receipt(
                receipt,
                owner_token,
                record_token,
                operation_deadline=build_deadline,
            ):
                raise _WorkspaceBuildHandoffError(_FAILURE)
            if not receipt._matches(owner_token, record_token, require_active=True):
                raise _WorkspaceBuildHandoffError(_FAILURE)
            return receipt
        except BaseException:
            if not already_active:
                _revoke_uncommitted_workspace_built_candidate(
                    receipt,
                    command,
                    owner_token,
                    record_token,
                )
            raise
    finally:
        publication_gate.publication_lock.release()


__all__: list[str] = []

"""Failure-only reconciliation for private workspace-built candidates."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _WorkspaceBuildCommand,
)


def _discard_unpublished_workspace_built_candidate(
    receipt: object,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
) -> None:
    """Retire a candidate that could not leave the command-gated factory."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
        _BUILT_BY_COMMAND,
        _BUILT_LEASES,
        _WorkspaceBuiltReceipt,
    )

    state = _BUILT_LEASES.get(receipt)
    if not (
        type(receipt) is _WorkspaceBuiltReceipt
        and type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[2] is command
        and state[5] == "pending"
    ):
        return
    if _BUILT_BY_COMMAND.get(command) is receipt:
        _BUILT_BY_COMMAND.pop(command, None)
    if command not in _BUILT_BY_COMMAND:
        _BUILT_LEASES.pop(receipt, None)


def _revoke_uncommitted_workspace_built_candidate(
    receipt: object,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
) -> None:
    """Revoke an exact candidate whenever built-slot handoff does not commit."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
        _BUILT_BY_COMMAND,
        _BUILT_LEASES,
        _WorkspaceBuiltReceipt,
    )

    state = _BUILT_LEASES.get(receipt)
    if (
        type(receipt) is _WorkspaceBuiltReceipt
        and type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[2] is command
        and state[5] in {"pending", "active"}
        and _BUILT_BY_COMMAND.get(command) is receipt
    ):
        _BUILT_LEASES[receipt] = (*state[:5], "revoked")


__all__: list[str] = []

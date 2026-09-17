"""Exact revoked-or-absent graph proof for one workspace built lease."""

from __future__ import annotations

import math

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspaceBuiltRuntimeProof,
)


def _workspace_built_lease_is_revoked_or_absent_impl(
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
) -> bool:
    """Prove a built lease never existed or its exact canonical lease is revoked."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
        _BUILT_BY_COMMAND,
        _BUILT_LEASES,
        _WorkspaceBuiltReceipt,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _WorkspacePreparedReceipt,
    )

    if (
        type(command) is not _WorkspaceBuildCommand
        or type(owner_token) is not object
        or type(record_token) is not object
    ):
        return False
    command_state = _COMMANDS.get(command)
    try:
        command_owner = object.__getattribute__(command, "_owner_token")
        command_record = object.__getattribute__(command, "_record_token")
        command_prepared = object.__getattribute__(command, "_prepared")
        command_deadline = object.__getattribute__(command, "_build_deadline")
        command_status = object.__getattribute__(command, "status")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    if (
        type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not owner_token
        or command_state[1] is not record_token
        or type(command_state[2]) is not _WorkspacePreparedReceipt
        or type(command_state[3]) is not float
        or not math.isfinite(command_state[3])
        or type(command_state[4]) is not str
        or type(command_state[5]) is not bytes
        or len(command_state[5]) != 32
        or command_owner is not owner_token
        or command_record is not record_token
        or command_prepared is not command_state[2]
        or type(command_deadline) is not float
        or not math.isfinite(command_deadline)
        or command_deadline != command_state[3]
        or type(command_status) is not str
        or command_status != "workspace-build-command"
    ):
        return False
    receipt = _BUILT_BY_COMMAND.get(command)
    if receipt is None:
        if command in _BUILT_BY_COMMAND or command_state[4] not in {"cancelled", "failed"}:
            return False
        return len(_BUILT_BY_COMMAND) == 0 and len(_BUILT_LEASES) == 0
    if type(receipt) is not _WorkspaceBuiltReceipt:
        return False
    state = _BUILT_LEASES.get(receipt)
    try:
        internal_owner = object.__getattribute__(receipt, "_owner_token")
        internal_record = object.__getattribute__(receipt, "_record_token")
        status = object.__getattribute__(receipt, "status")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    if not (
        type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[2] is command
        and type(state[3]) is _WorkspaceBuiltRuntimeProof
        and state[3]._matches_canonical(
            owner_token=owner_token,
            record_token=record_token,
        )
        and internal_owner is owner_token
        and internal_record is record_token
        and type(status) is str
        and status == "workspace-built"
        and _BUILT_BY_COMMAND.get(command) is receipt
        and type(state[5]) is str
        and state[5] == "revoked"
        and command_state[4] in {"built", "cancelled"}
    ):
        return False
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildCleanupAuthority,
        _RelayLinuxBuildProcessReceipt,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
        _PROCESS_ASSOCIATIONS,
    )

    process_receipt = state[4]
    association = _PROCESS_ASSOCIATIONS.get(command)
    if type(process_receipt) is not _RelayLinuxBuildProcessReceipt:
        return False
    try:
        process_owner = object.__getattribute__(process_receipt, "_owner_token")
        process_status = object.__getattribute__(process_receipt, "status")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    if not (
        type(association) is tuple
        and len(association) == 7
        and association[0] is owner_token
        and association[1] is record_token
        and type(association[2]) is object
        and type(association[3]) is _RelayLinuxBuildCleanupAuthority
        and association[3]._matches_owner_token(association[2])
        and type(association[4]) is bytes
        and len(association[4]) == 32
        and association[4] == command_state[5]
        and association[5] is process_receipt
        and type(association[6]) is str
        and association[6] == "released-zero"
        and process_owner is association[2]
        and type(process_status) is str
        and process_status == "build-process-exited-zero"
    ):
        return False
    if len(_BUILT_LEASES) != 1 or len(_BUILT_BY_COMMAND) != 1:
        return False
    reverse = [
        candidate
        for candidate, candidate_state in _BUILT_LEASES.items()
        if type(candidate_state) is tuple
        and len(candidate_state) >= 3
        and candidate_state[2] is command
    ]
    forward = [
        candidate
        for candidate, candidate_receipt in _BUILT_BY_COMMAND.items()
        if candidate_receipt is receipt
    ]
    return bool(
        len(reverse) == 1 and reverse[0] is receipt and len(forward) == 1 and forward[0] is command
    )


__all__: list[str] = []

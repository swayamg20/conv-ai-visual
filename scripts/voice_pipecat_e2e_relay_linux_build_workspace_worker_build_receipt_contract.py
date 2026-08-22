"""Canonical deadline proof shared by private workspace-built lease operations."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _FAILURE,
    _workspace_build_command_cancel_requested,
    _WorkspaceBuildCommand,
    _WorkspaceBuildHandoffError,
)


def _canonical_workspace_built_deadline(
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    operation_deadline: float,
) -> float:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _WorkspacePreparedReceipt,
    )

    if (
        type(command) is not _WorkspaceBuildCommand
        or type(owner_token) is not object
        or type(record_token) is not object
        or type(operation_deadline) is not float
        or not math.isfinite(operation_deadline)
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    state = _COMMANDS.get(command)
    try:
        command_owner = object.__getattribute__(command, "_owner_token")
        command_record = object.__getattribute__(command, "_record_token")
        command_prepared = object.__getattribute__(command, "_prepared")
        command_deadline = object.__getattribute__(command, "_build_deadline")
        command_status = object.__getattribute__(command, "status")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _WorkspaceBuildHandoffError(_FAILURE) from None
    if not (
        type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and type(state[2]) is _WorkspacePreparedReceipt
        and type(state[3]) is float
        and math.isfinite(state[3])
        and state[3] == operation_deadline
        and type(state[4]) is str
        and type(state[5]) is bytes
        and len(state[5]) == 32
        and command_owner is owner_token
        and command_record is record_token
        and command_prepared is state[2]
        and type(command_deadline) is float
        and math.isfinite(command_deadline)
        and command_deadline == state[3]
        and type(command_status) is str
        and command_status == "workspace-build-command"
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return state[3]


def _require_live_workspace_built_command(
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    build_deadline: float,
    *,
    allowed_phases: frozenset[str],
) -> tuple[object, object, object, float, str, bytes]:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _workspace_prepared_build_matches,
    )

    canonical_deadline = _canonical_workspace_built_deadline(
        command,
        owner_token,
        record_token,
        build_deadline,
    )
    state = _COMMANDS.get(command)
    if not (
        type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == canonical_deadline
        and state[4] in allowed_phases
        and _workspace_prepared_build_matches(
            state[2],
            owner_token,
            record_token,
            command,
            canonical_deadline,
        )
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    try:
        now = time.monotonic()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _WorkspaceBuildHandoffError(_FAILURE) from None
    if (
        type(now) is not float
        or not math.isfinite(now)
        or now >= canonical_deadline
        or _workspace_build_command_cancel_requested(command)
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return state


def _workspace_built_candidate_is_fresh(
    receipt: object,
    owner_token: object,
    record_token: object | None,
    *,
    require_active: bool,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
        _workspace_build_process_completed_zero,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
        _BUILT_BY_COMMAND,
        _BUILT_LEASES,
        _WorkspaceBuiltReceipt,
    )

    state = _BUILT_LEASES.get(receipt)
    if (
        type(receipt) is not _WorkspaceBuiltReceipt
        or type(state) is not tuple
        or len(state) != 6
        or state[0] is not owner_token
        or (record_token is not None and state[1] is not record_token)
        or type(state[2]) is not _WorkspaceBuildCommand
        or type(state[3]) is not bytes
        or len(state[3]) != 32
        or type(state[5]) is not str
        or state[5] not in {"pending", "active"}
        or (require_active and state[5] != "active")
        or _BUILT_BY_COMMAND.get(state[2]) is not receipt
    ):
        return False
    allowed_phases = frozenset({"running" if state[5] == "pending" else "built"})
    try:
        command_state = _require_live_workspace_built_command(
            state[2],
            state[0],
            state[1],
            object.__getattribute__(state[2], "_build_deadline"),
            allowed_phases=allowed_phases,
        )
        process_zero = _workspace_build_process_completed_zero(
            state[2],
            state[4],
            owner_token=state[0],
            record_token=state[1],
            build_deadline=command_state[3],
        )
        internal_owner = object.__getattribute__(receipt, "_owner_token")
        internal_record = object.__getattribute__(receipt, "_record_token")
        status = object.__getattribute__(receipt, "status")
        if not (
            process_zero
            and _BUILT_LEASES.get(receipt) is state
            and _BUILT_BY_COMMAND.get(state[2]) is receipt
            and internal_owner is state[0]
            and internal_record is state[1]
            and type(status) is str
            and status == "workspace-built"
        ):
            return False
        _require_live_workspace_built_command(
            state[2],
            state[0],
            state[1],
            command_state[3],
            allowed_phases=allowed_phases,
        )
        return True
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


__all__: list[str] = []

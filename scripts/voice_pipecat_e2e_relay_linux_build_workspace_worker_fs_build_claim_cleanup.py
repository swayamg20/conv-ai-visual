"""Cleanup-only reconciliation of an interrupted prepared build claim."""

from __future__ import annotations

import math

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _LEASES,
    _PREPARED_BUILDS,
    _store_prepared_build,
    _WorkspacePreparedReceipt,
)

_RETIREMENT_TOKEN = object()


def _reconcile_revoked_prepared_build_for_cleanup(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
    command: object,
    build_deadline: float,
) -> bool:
    """Finish only the inert association after an unstarted claim loss."""

    if (
        type(receipt) is not _WorkspacePreparedReceipt
        or type(command) is not _WorkspaceBuildCommand
        or type(build_deadline) is not float
        or not math.isfinite(build_deadline)
    ):
        return False
    state = _LEASES.get(receipt)
    command_state = _COMMANDS.get(command)
    candidate = (owner_token, record_token, command, build_deadline)
    association = _PREPARED_BUILDS.get(receipt)
    try:
        valid = bool(
            type(state) is tuple
            and len(state) == 4
            and state[0] is owner_token
            and state[1] is record_token
            and type(state[2]) is bytes
            and len(state[2]) == 32
            and state[3] == "revoked"
            and type(command_state) is tuple
            and len(command_state) == 6
            and command_state[0] is owner_token
            and command_state[1] is record_token
            and command_state[2] is receipt
            and command_state[3] == build_deadline
            and command_state[4] in {"cancelled", "failed"}
            and type(command_state[5]) is bytes
            and len(command_state[5]) == 32
            and len(_LEASES) == 1
            and len(_PREPARED_BUILDS) in {0, 1}
            and (not _PREPARED_BUILDS or next(iter(_PREPARED_BUILDS)) is receipt)
            and association
            in {
                None,
                (*candidate, "intended"),
                (*candidate, "building"),
            }
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    if not valid:
        return False
    bound = (*candidate, "building")
    if association != bound:
        _store_prepared_build(receipt, bound)
    return _workspace_revoked_prepared_build_for_cleanup_matches(
        receipt,
        owner_token,
        record_token,
        command,
        build_deadline,
    )


def _workspace_revoked_prepared_build_for_cleanup_matches(
    receipt: object,
    owner_token: object,
    record_token: object,
    command: object,
    build_deadline: float,
) -> bool:
    """Prove the canonical revoked association without caller-mutable fields."""

    if (
        type(receipt) is not _WorkspacePreparedReceipt
        or type(command) is not _WorkspaceBuildCommand
        or type(build_deadline) is not float
        or not math.isfinite(build_deadline)
        or len(_LEASES) != 1
        or len(_PREPARED_BUILDS) != 1
        or len(_COMMANDS) != 1
        or next(iter(_LEASES)) is not receipt
        or next(iter(_PREPARED_BUILDS)) is not receipt
        or next(iter(_COMMANDS)) is not command
    ):
        return False
    state = _LEASES.get(receipt)
    association = _PREPARED_BUILDS.get(receipt)
    command_state = _COMMANDS.get(command)
    return bool(
        type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and type(state[2]) is bytes
        and len(state[2]) == 32
        and state[3] == "revoked"
        and type(association) is tuple
        and len(association) == 5
        and association[0] is owner_token
        and association[1] is record_token
        and association[2] is command
        and association[3] == build_deadline
        and association[4] == "building"
        and type(command_state) is tuple
        and len(command_state) == 6
        and command_state[0] is owner_token
        and command_state[1] is record_token
        and command_state[2] is receipt
        and command_state[3] == build_deadline
        and command_state[4] in {"built", "cancelled", "failed"}
        and type(command_state[5]) is bytes
        and len(command_state[5]) == 32
    )


def _workspace_revoked_prepared_lease_is_singleton(
    receipt: object,
    owner_token: object,
    record_token: object,
) -> bool:
    """Prove canonical cleanup authority for the sole revoked prepared lease."""

    if len(_LEASES) != 1 or next(iter(_LEASES)) is not receipt:
        return False
    state = _LEASES.get(receipt)
    return bool(
        type(receipt) is _WorkspacePreparedReceipt
        and type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and type(state[2]) is bytes
        and len(state[2]) == 32
        and state[3] == "revoked"
    )


def _forget_workspace_prepared_lease(
    receipt: object,
    owner_token: object,
    record_token: object,
    *,
    retirement_committed: bool,
    retirement_fingerprint: bytes,
) -> bool:
    """Retire one exact revoked lease after its durable forget marker."""

    if (
        retirement_committed is not True
        or type(retirement_fingerprint) is not bytes
        or len(retirement_fingerprint) != 32
    ):
        return False
    if _LEASES:
        if not _workspace_revoked_prepared_lease_can_retire(
            receipt,
            owner_token,
            record_token,
        ):
            return False
        state = _LEASES[receipt]
        if state[2] != retirement_fingerprint:
            return False
        object.__setattr__(
            receipt,
            "_lease_retired",
            (_RETIREMENT_TOKEN, owner_token, record_token, state[2]),
        )
        _LEASES.pop(receipt, None)
        return not _LEASES
    return type(receipt) is _WorkspacePreparedReceipt


def _workspace_revoked_prepared_lease_can_retire(
    receipt: object,
    owner_token: object,
    record_token: object,
) -> bool:
    """Validate only canonical map evidence after filesystem settlement."""

    state = _LEASES.get(receipt)
    return bool(
        type(receipt) is _WorkspacePreparedReceipt
        and len(_LEASES) == 1
        and next(iter(_LEASES)) is receipt
        and type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and type(state[2]) is bytes
        and len(state[2]) == 32
        and state[3] == "revoked"
    )


__all__: list[str] = []

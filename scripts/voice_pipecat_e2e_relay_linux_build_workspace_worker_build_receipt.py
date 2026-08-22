"""Canonical revocable lease for one validated worker-owned build output."""

from __future__ import annotations

import math
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract import (
    _canonical_workspace_built_deadline,
    _require_live_workspace_built_command,
    _workspace_built_candidate_is_fresh,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_reconcile import (
    _discard_unpublished_workspace_built_candidate,
    _revoke_uncommitted_workspace_built_candidate,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _FAILURE,
    _acquire_command_gate,
    _store_command_state,
    _WorkspaceBuildCommand,
    _WorkspaceBuildHandoffError,
)

_BUILT_TOKEN = object()
_BUILT_LEASES: weakref.WeakKeyDictionary[
    _WorkspaceBuiltReceipt,
    tuple[object, object, _WorkspaceBuildCommand, bytes, object, str],
] = weakref.WeakKeyDictionary()
_BUILT_BY_COMMAND: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    _WorkspaceBuiltReceipt,
] = weakref.WeakKeyDictionary()

_BUILT_PHASES = frozenset({"built"})
_RUNNING_PHASES = frozenset({"running"})
_RUNNING_OR_BUILT_PHASES = frozenset({"running", "built"})


class _WorkspaceBuiltReceipt:
    """Opaque lease activated only after process release and output validation."""

    __slots__ = ("__weakref__", "_owner_token", "_record_token", "status")

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
    ) -> None:
        if (
            token is not _BUILT_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "status", "workspace-built")

    def _matches(
        self,
        owner_token: object,
        record_token: object | None = None,
        *,
        require_active: bool = False,
    ) -> bool:
        return _workspace_built_candidate_is_fresh(
            self,
            owner_token,
            record_token,
            require_active=require_active,
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceBuiltReceipt()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace built receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace built receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace built receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace built receipt cannot be serialized")


def _new_workspace_built_receipt(
    *,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    output_digest: bytes,
    process_receipt: object,
    operation_deadline: float,
) -> _WorkspaceBuiltReceipt:
    receipt, _already_active = _new_workspace_built_receipt_with_state(
        command=command,
        owner_token=owner_token,
        record_token=record_token,
        output_digest=output_digest,
        process_receipt=process_receipt,
        operation_deadline=operation_deadline,
    )
    return receipt


def _new_workspace_built_receipt_for_publication(
    *,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    output_digest: bytes,
    process_receipt: object,
    operation_deadline: float,
) -> tuple[_WorkspaceBuiltReceipt, bool]:
    """Return the candidate and its command-gated prior-active fact."""

    return _new_workspace_built_receipt_with_state(
        command=command,
        owner_token=owner_token,
        record_token=record_token,
        output_digest=output_digest,
        process_receipt=process_receipt,
        operation_deadline=operation_deadline,
    )


def _new_workspace_built_receipt_with_state(
    *,
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
    output_digest: bytes,
    process_receipt: object,
    operation_deadline: float,
) -> tuple[_WorkspaceBuiltReceipt, bool]:
    if type(output_digest) is not bytes or len(output_digest) != 32:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    build_deadline = _canonical_workspace_built_deadline(
        command,
        owner_token,
        record_token,
        operation_deadline,
    )
    gate = _acquire_command_gate(command, build_deadline)
    created_receipt: _WorkspaceBuiltReceipt | None = None
    try:
        state = _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=_RUNNING_OR_BUILT_PHASES,
        )
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
            _workspace_build_process_completed_zero,
        )

        if not _workspace_build_process_completed_zero(
            command,
            process_receipt,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        existing = _BUILT_BY_COMMAND.get(command)
        if existing is not None:
            existing_state = _BUILT_LEASES.get(existing)
            if (
                type(existing) is _WorkspaceBuiltReceipt
                and type(existing_state) is tuple
                and len(existing_state) == 6
                and existing_state[0] is owner_token
                and existing_state[1] is record_token
                and existing_state[2] is command
                and existing_state[3] == output_digest
                and existing_state[4] is process_receipt
                and existing_state[5] in {"pending", "active"}
                and state[4] == ("running" if existing_state[5] == "pending" else "built")
                and existing._matches(
                    owner_token,
                    record_token,
                    require_active=existing_state[5] == "active",
                )
            ):
                _require_live_workspace_built_command(
                    command,
                    owner_token,
                    record_token,
                    build_deadline,
                    allowed_phases=(
                        _RUNNING_PHASES if existing_state[5] == "pending" else _BUILT_PHASES
                    ),
                )
                return existing, existing_state[5] == "active"
            raise _WorkspaceBuildHandoffError(_FAILURE)
        if command in _BUILT_BY_COMMAND or state[4] != "running" or gate.cancel_requested:
            raise _WorkspaceBuildHandoffError(_FAILURE)
        receipt = _WorkspaceBuiltReceipt(
            _BUILT_TOKEN,
            owner_token=owner_token,
            record_token=record_token,
        )
        created_receipt = receipt
        pending = (
            owner_token,
            record_token,
            command,
            output_digest,
            process_receipt,
            "pending",
        )
        _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=_RUNNING_PHASES,
        )
        try:
            _store_built_lease(receipt, pending)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _BUILT_LEASES.get(receipt) != pending:
                raise
        _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=_RUNNING_PHASES,
        )
        try:
            _store_built_for_command(command, receipt)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _BUILT_BY_COMMAND.get(command) is not receipt:
                raise
        _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=_RUNNING_PHASES,
        )
        if _BUILT_BY_COMMAND.get(command) is not receipt or not receipt._matches(
            owner_token, record_token
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        return receipt, False
    except BaseException:
        if created_receipt is not None:
            _discard_unpublished_workspace_built_candidate(
                created_receipt,
                command,
                owner_token,
                record_token,
            )
        raise
    finally:
        gate.lock.release()


def _activate_workspace_built_receipt(
    receipt: _WorkspaceBuiltReceipt,
    owner_token: object,
    record_token: object,
    *,
    operation_deadline: float,
) -> bool:
    if type(receipt) is not _WorkspaceBuiltReceipt:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    initial = _BUILT_LEASES.get(receipt)
    if (
        type(initial) is not tuple
        or len(initial) != 6
        or initial[0] is not owner_token
        or initial[1] is not record_token
        or type(initial[2]) is not _WorkspaceBuildCommand
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    command = initial[2]
    build_deadline = _canonical_workspace_built_deadline(
        command,
        owner_token,
        record_token,
        operation_deadline,
    )
    gate = _acquire_command_gate(command, build_deadline)
    already_active = False
    try:
        state = _BUILT_LEASES.get(receipt)
        raw_command_state = _COMMANDS.get(command)
        if (
            type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[2] is not command
            or state[5] not in {"pending", "active"}
            or _BUILT_BY_COMMAND.get(command) is not receipt
            or gate.cancel_requested
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        already_active = bool(
            state[5] == "active"
            and type(raw_command_state) is tuple
            and len(raw_command_state) == 6
            and raw_command_state[0] is owner_token
            and raw_command_state[1] is record_token
            and raw_command_state[3] == build_deadline
            and raw_command_state[4] == "built"
        )
        expected_phases = _RUNNING_PHASES if state[5] == "pending" else _RUNNING_OR_BUILT_PHASES
        command_state = _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=expected_phases,
        )
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
            _workspace_build_process_completed_zero,
        )

        if not _workspace_build_process_completed_zero(
            command,
            state[4],
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        active = (*state[:5], "active")
        if state[5] == "pending":
            _require_live_workspace_built_command(
                command,
                owner_token,
                record_token,
                build_deadline,
                allowed_phases=_RUNNING_PHASES,
            )
            try:
                _store_built_lease(receipt, active)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _BUILT_LEASES.get(receipt) != active:
                    raise
            _require_live_workspace_built_command(
                command,
                owner_token,
                record_token,
                build_deadline,
                allowed_phases=_RUNNING_PHASES,
            )
        _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=(_RUNNING_PHASES if command_state[4] == "running" else _BUILT_PHASES),
        )
        built = (*command_state[:4], "built", command_state[5])
        if command_state[4] == "running":
            try:
                _store_command_state(command, built)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _COMMANDS.get(command) != built:
                    raise
        _require_live_workspace_built_command(
            command,
            owner_token,
            record_token,
            build_deadline,
            allowed_phases=_BUILT_PHASES,
        )
        if not receipt._matches(owner_token, record_token, require_active=True):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        return True
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
        gate.lock.release()


def _revoke_workspace_built_receipt(
    receipt: _WorkspaceBuiltReceipt,
    owner_token: object,
    record_token: object,
    *,
    cleanup_deadline: float,
) -> bool:
    initial = _BUILT_LEASES.get(receipt)
    if type(initial) is not tuple or len(initial) != 6:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    command = initial[2]
    gate = _acquire_command_gate(command, cleanup_deadline)
    try:
        state = _BUILT_LEASES.get(receipt)
        if (
            type(receipt) is not _WorkspaceBuiltReceipt
            or type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[2] is not command
            or _BUILT_BY_COMMAND.get(command) is not receipt
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        revoked = (*state[:5], "revoked")
        if state[5] != "revoked":
            try:
                _store_built_lease(receipt, revoked)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _BUILT_LEASES.get(receipt) != revoked:
                    raise
        return _workspace_built_receipt_is_revoked(receipt, owner_token, record_token)
    finally:
        gate.lock.release()


def _workspace_built_receipt_is_revoked(
    receipt: _WorkspaceBuiltReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    state = _BUILT_LEASES.get(receipt)
    return bool(
        type(receipt) is _WorkspaceBuiltReceipt
        and type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and _BUILT_BY_COMMAND.get(state[2]) is receipt
        and state[5] == "revoked"
    )


def _workspace_built_receipt_is_stable_handoff(
    receipt: _WorkspaceBuiltReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    """Expose active output only after the prepared lease is exactly revoked."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _workspace_revoked_prepared_build_matches,
    )

    state = _BUILT_LEASES.get(receipt)
    if (
        type(receipt) is not _WorkspaceBuiltReceipt
        or type(state) is not tuple
        or len(state) != 6
        or state[0] is not owner_token
        or state[1] is not record_token
        or state[5] != "active"
    ):
        return False
    command = state[2]
    command_state = _COMMANDS.get(command)
    return bool(
        type(command_state) is tuple
        and len(command_state) == 6
        and _workspace_revoked_prepared_build_matches(
            command_state[2],
            owner_token,
            record_token,
            command,
            command_state[3],
        )
        and receipt._matches(owner_token, record_token, require_active=True)
    )


def _workspace_built_lease_is_revoked_or_absent(
    command: _WorkspaceBuildCommand,
    owner_token: object,
    record_token: object,
) -> bool:
    """Prove a built lease never existed or its exact canonical lease is revoked."""

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
        and type(state[3]) is bytes
        and len(state[3]) == 32
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
    return (
        len(reverse) == 1 and reverse[0] is receipt and len(forward) == 1 and forward[0] is command
    )


def _forget_workspace_built_receipt(command: _WorkspaceBuildCommand) -> bool:
    """Drop the command lease only after exact built revocation."""

    receipt = _BUILT_BY_COMMAND.get(command)
    if receipt is None:
        return True
    state = _BUILT_LEASES.get(receipt)
    if (
        type(receipt) is not _WorkspaceBuiltReceipt
        or type(state) is not tuple
        or len(state) != 6
        or state[2] is not command
        or state[5] != "revoked"
    ):
        return False
    _BUILT_BY_COMMAND.pop(command, None)
    _BUILT_LEASES.pop(receipt, None)
    return command not in _BUILT_BY_COMMAND and receipt not in _BUILT_LEASES


def _store_built_lease(
    receipt: _WorkspaceBuiltReceipt,
    value: tuple[object, object, _WorkspaceBuildCommand, bytes, object, str],
) -> None:
    """Deterministic built-lease store cut for return-loss tests."""

    _BUILT_LEASES[receipt] = value


def _store_built_for_command(
    command: _WorkspaceBuildCommand,
    receipt: _WorkspaceBuiltReceipt,
) -> None:
    """Deterministic command-to-built publication cut."""

    existing = _BUILT_BY_COMMAND.setdefault(command, receipt)
    if existing is not receipt:
        raise _WorkspaceBuildHandoffError(_FAILURE)


__all__: list[str] = []

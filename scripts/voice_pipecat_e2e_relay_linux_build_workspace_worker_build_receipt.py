"""Canonical revocable lease for one validated worker-owned build output."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _FAILURE,
    _acquire_command_gate,
    _store_command_state,
    _workspace_build_command_cancel_requested,
    _workspace_build_command_phase,
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
        state = _BUILT_LEASES.get(self)
        status = object.__getattribute__(self, "status")
        return bool(
            type(state) is tuple
            and len(state) == 6
            and state[0] is owner_token
            and (record_token is None or state[1] is record_token)
            and type(state[3]) is bytes
            and len(state[3]) == 32
            and _BUILT_BY_COMMAND.get(state[2]) is self
            and type(status) is str
            and status == "workspace-built"
            and state[5] in {"pending", "active"}
            and (state[5] != "active" or _workspace_build_command_phase(state[2]) == "built")
            and not _workspace_build_command_cancel_requested(state[2])
            and (not require_active or state[5] == "active")
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
    gate = _acquire_command_gate(command, operation_deadline)
    try:
        state = _COMMANDS.get(command)
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
            ):
                return existing
            raise _WorkspaceBuildHandoffError(_FAILURE)
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
            _workspace_build_process_completed_zero,
        )

        if (
            type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[4] != "running"
            or gate.cancel_requested
            or type(output_digest) is not bytes
            or len(output_digest) != 32
            or not _workspace_build_process_completed_zero(command, process_receipt)
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        receipt = _WorkspaceBuiltReceipt(
            _BUILT_TOKEN,
            owner_token=owner_token,
            record_token=record_token,
        )
        pending = (
            owner_token,
            record_token,
            command,
            output_digest,
            process_receipt,
            "pending",
        )
        try:
            _store_built_lease(receipt, pending)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _BUILT_LEASES.get(receipt) != pending:
                raise
        try:
            _store_built_for_command(command, receipt)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _BUILT_BY_COMMAND.get(command) is not receipt:
                raise
        if _BUILT_BY_COMMAND.get(command) is not receipt:
            raise _WorkspaceBuildHandoffError(_FAILURE)
        return receipt
    finally:
        gate.lock.release()


def _activate_workspace_built_receipt(
    receipt: _WorkspaceBuiltReceipt,
    owner_token: object,
    record_token: object,
    *,
    operation_deadline: float,
) -> bool:
    initial = _BUILT_LEASES.get(receipt)
    if type(initial) is not tuple or len(initial) != 6:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    command = initial[2]
    gate = _acquire_command_gate(command, operation_deadline)
    try:
        state = _BUILT_LEASES.get(receipt)
        command_state = _COMMANDS.get(command)
        if (
            type(receipt) is not _WorkspaceBuiltReceipt
            or type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[2] is not command
            or state[5] not in {"pending", "active"}
            or type(command_state) is not tuple
            or command_state[4] not in {"running", "built"}
            or _BUILT_BY_COMMAND.get(command) is not receipt
            or gate.cancel_requested
        ):
            raise _WorkspaceBuildHandoffError(_FAILURE)
        active = (*state[:5], "active")
        if state[5] == "pending":
            try:
                _store_built_lease(receipt, active)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _BUILT_LEASES.get(receipt) != active:
                    raise
        built = (*command_state[:4], "built", command_state[5])
        if command_state[4] == "running":
            try:
                _store_command_state(command, built)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _COMMANDS.get(command) != built:
                    raise
        return receipt._matches(owner_token, record_token, require_active=True)
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

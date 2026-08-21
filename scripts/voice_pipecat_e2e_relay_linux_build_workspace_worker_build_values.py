"""Path-free command and lease values for one worker-owned Next build."""

from __future__ import annotations

import math
import threading
import time
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _claim_workspace_prepared_receipt_for_build,
    _workspace_prepared_build_matches,
    _WorkspacePreparedReceipt,
)

_COMMAND_TOKEN = object()
_FAILURE = "Relay Linux workspace build handoff is invalid"
_MAX_BUILD_SECONDS = 600.0
_COMMANDS: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    tuple[object, object, _WorkspacePreparedReceipt, float, str, bytes],
] = weakref.WeakKeyDictionary()
_PROCESS_ASSOCIATIONS: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    tuple[object, object, object, object, bytes, object | None, str],
] = weakref.WeakKeyDictionary()
_COMMAND_GATES: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    _WorkspaceBuildCommandGate,
] = weakref.WeakKeyDictionary()


class _WorkspaceBuildCommandGate:
    """Module-owned start/cancel linearization for one path-free command."""

    __slots__ = ("cancel_requested", "lock")

    def __init__(self) -> None:
        self.cancel_requested = False
        self.lock = threading.Lock()


class _WorkspaceBuildHandoffError(RuntimeError):
    """Fixed failure for the private prepared-to-built transition."""

    def __repr__(self) -> str:
        return "_WorkspaceBuildHandoffError()"


class _WorkspaceBuildCommand:
    """One immutable, path-free request to consume a prepared workspace."""

    __slots__ = (
        "__weakref__",
        "_build_deadline",
        "_owner_token",
        "_prepared",
        "_record_token",
        "status",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
        prepared: _WorkspacePreparedReceipt,
        build_deadline: float,
        expected_spawn_fingerprint: bytes,
    ) -> None:
        if (
            token is not _COMMAND_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(prepared) is not _WorkspacePreparedReceipt
            or type(build_deadline) is not float
            or not math.isfinite(build_deadline)
            or type(expected_spawn_fingerprint) is not bytes
            or len(expected_spawn_fingerprint) != 32
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_prepared", prepared)
        object.__setattr__(self, "_build_deadline", build_deadline)
        object.__setattr__(self, "status", "workspace-build-command")
        _COMMANDS[self] = (
            owner_token,
            record_token,
            prepared,
            build_deadline,
            "pending",
            expected_spawn_fingerprint,
        )
        _COMMAND_GATES[self] = _WorkspaceBuildCommandGate()

    def _matches(
        self,
        owner_token: object,
        record_token: object | None = None,
        prepared: _WorkspacePreparedReceipt | None = None,
    ) -> bool:
        state = _COMMANDS.get(self)
        status = object.__getattribute__(self, "status")
        return bool(
            type(state) is tuple
            and len(state) == 6
            and state[0] is owner_token
            and (record_token is None or state[1] is record_token)
            and (prepared is None or state[2] is prepared)
            and type(state[3]) is float
            and math.isfinite(state[3])
            and state[4]
            in {
                "pending",
                "claim-intended",
                "building",
                "start-intended",
                "running",
                "built",
                "cancelled",
                "failed",
            }
            and type(status) is str
            and status == "workspace-build-command"
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceBuildCommand()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace build command is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace build command cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace build command cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace build command cannot be serialized")


def _new_workspace_build_command(
    *,
    owner_token: object,
    record_token: object,
    prepared: _WorkspacePreparedReceipt,
    build_deadline: float,
    expected_spawn_fingerprint: bytes,
) -> _WorkspaceBuildCommand:
    now = time.monotonic()
    if (
        type(build_deadline) is not float
        or not math.isfinite(build_deadline)
        or build_deadline <= now
        or build_deadline - now > _MAX_BUILD_SECONDS
        or not prepared._matches(owner_token, record_token, require_active=True)
        or type(expected_spawn_fingerprint) is not bytes
        or len(expected_spawn_fingerprint) != 32
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return _WorkspaceBuildCommand(
        _COMMAND_TOKEN,
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=build_deadline,
        expected_spawn_fingerprint=expected_spawn_fingerprint,
    )


def _claim_workspace_build_command(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    prepared: _WorkspacePreparedReceipt,
) -> float:
    if type(command) is not _WorkspaceBuildCommand:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    state = _COMMANDS.get(command)
    if (
        type(state) is not tuple
        or len(state) != 6
        or state[0] is not owner_token
        or state[1] is not record_token
        or state[2] is not prepared
        or state[4] not in {"pending", "claim-intended", "building"}
        or type(state[3]) is not float
        or not math.isfinite(state[3])
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    if time.monotonic() >= state[3]:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    intended = (*state[:4], "claim-intended", state[5])
    if state[4] == "pending":
        try:
            _store_command_state(command, intended)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _COMMANDS.get(command) != intended:
                raise
    if state[4] != "building":
        try:
            claimed = _claim_workspace_prepared_receipt_for_build(
                prepared,
                owner_token,
                record_token,
                command,
                state[3],
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            try:
                claimed = _claim_workspace_prepared_receipt_for_build(
                    prepared,
                    owner_token,
                    record_token,
                    command,
                    state[3],
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                claimed = _workspace_prepared_build_matches(
                    prepared,
                    owner_token,
                    record_token,
                    command,
                    state[3],
                )
        if not claimed:
            raise _WorkspaceBuildHandoffError(_FAILURE)
        building = (*state[:4], "building", state[5])
        try:
            _store_command_state(command, building)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _COMMANDS.get(command) != building:
                raise
    if (
        _COMMANDS.get(command) != (*state[:4], "building", state[5])
        or not _workspace_prepared_build_matches(
            prepared,
            owner_token,
            record_token,
            command,
            state[3],
        )
        or time.monotonic() >= state[3]
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return state[3]


def _workspace_build_command_failed(command: _WorkspaceBuildCommand) -> None:
    state = _COMMANDS.get(command)
    if (
        type(state) is tuple
        and len(state) == 6
        and state[4]
        in {
            "pending",
            "claim-intended",
            "building",
            "start-intended",
            "running",
        }
    ):
        _COMMANDS[command] = (*state[:4], "failed", state[5])


def _workspace_build_command_authorizes_process(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> bool:
    state = _COMMANDS.get(command)
    return bool(
        type(command) is _WorkspaceBuildCommand
        and type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == build_deadline
        and state[4] == "building"
        and type(build_deadline) is float
        and math.isfinite(build_deadline)
        and time.monotonic() < build_deadline
        and not _workspace_build_command_cancel_requested(command)
    )


def _intend_workspace_build_process_start(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> bool:
    """Linearize the sole process start before the facade start operation."""

    gate = _acquire_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=build_deadline,
    )
    if gate is None:
        return False
    gate.lock.release()
    return True


def _acquire_workspace_build_process_start(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> _WorkspaceBuildCommandGate | None:
    """Return the held permit consumed across process-facade start."""

    gate = _acquire_command_gate(command, build_deadline)
    try:
        state = _COMMANDS.get(command)
        if (
            type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[3] != build_deadline
            or state[4] not in {"building", "start-intended"}
            or gate.cancel_requested
            or time.monotonic() >= build_deadline
        ):
            gate.lock.release()
            return None
        intended = (*state[:4], "start-intended", state[5])
        if state[4] == "building":
            try:
                _store_command_state(command, intended)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _COMMANDS.get(command) != intended:
                    raise
        if _COMMANDS.get(command) != intended or gate.cancel_requested:
            gate.lock.release()
            return None
        return gate
    except BaseException:
        gate.lock.release()
        raise


def _release_workspace_build_process_start(
    permit: object,
) -> None:
    if type(permit) is not _WorkspaceBuildCommandGate or not permit.lock.locked():
        raise _WorkspaceBuildHandoffError(_FAILURE)
    permit.lock.release()


def _complete_workspace_build_process_start(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> bool:
    gate = _acquire_command_gate(command, build_deadline)
    try:
        state = _COMMANDS.get(command)
        if (
            type(state) is not tuple
            or len(state) != 6
            or state[0] is not owner_token
            or state[1] is not record_token
            or state[3] != build_deadline
            or state[4] not in {"start-intended", "running"}
            or gate.cancel_requested
        ):
            return False
        running = (*state[:4], "running", state[5])
        if state[4] == "start-intended":
            try:
                _store_command_state(command, running)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _COMMANDS.get(command) != running:
                    raise
        return _COMMANDS.get(command) == running and not gate.cancel_requested
    finally:
        gate.lock.release()


def _request_workspace_build_command_cancel(
    command: _WorkspaceBuildCommand,
    *,
    cleanup_deadline: float,
) -> bool:
    """Latch cancellation before process release can race the sole start."""

    if type(command) is not _WorkspaceBuildCommand:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    gate = _COMMAND_GATES.get(command)
    if type(gate) is not _WorkspaceBuildCommandGate:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    gate.cancel_requested = True
    if type(cleanup_deadline) is not float or not math.isfinite(cleanup_deadline):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    if not gate.lock.acquire(blocking=False):
        return True
    try:
        state = _COMMANDS.get(command)
        if type(state) is tuple and len(state) == 6 and state[4] != "cancelled":
            cancelled = (*state[:4], "cancelled", state[5])
            try:
                _store_command_state(command, cancelled)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if _COMMANDS.get(command) != cancelled:
                    raise
        return _workspace_build_command_cancel_requested(command)
    finally:
        gate.lock.release()


def _workspace_build_command_cancel_requested(
    command: _WorkspaceBuildCommand,
) -> bool:
    gate = _COMMAND_GATES.get(command)
    return bool(
        type(command) is _WorkspaceBuildCommand
        and type(gate) is _WorkspaceBuildCommandGate
        and gate.cancel_requested is True
    )


def _workspace_build_command_phase(command: _WorkspaceBuildCommand) -> str | None:
    state = _COMMANDS.get(command)
    return state[4] if type(state) is tuple and len(state) == 6 else None


def _forget_workspace_build_command(
    command: _WorkspaceBuildCommand,
    record_token: object,
) -> bool:
    state = _COMMANDS.get(command)
    if state is None:
        return True
    if (
        type(command) is not _WorkspaceBuildCommand
        or type(state) is not tuple
        or len(state) != 6
        or state[1] is not record_token
        or state[4] not in {"cancelled", "failed", "built"}
        or command in _PROCESS_ASSOCIATIONS
    ):
        return False
    _COMMAND_GATES.pop(command, None)
    _COMMANDS.pop(command, None)
    return command not in _COMMANDS and command not in _COMMAND_GATES


def _workspace_build_command_matches_exact(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    prepared: _WorkspacePreparedReceipt,
    build_deadline: float,
    expected_spawn_fingerprint: bytes,
) -> bool:
    state = _COMMANDS.get(command)
    return bool(
        type(command) is _WorkspaceBuildCommand
        and type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and state[2] is prepared
        and state[3] == build_deadline
        and state[4]
        in {
            "pending",
            "claim-intended",
            "building",
            "start-intended",
            "running",
            "built",
            "cancelled",
        }
        and state[5] == expected_spawn_fingerprint
    )


def _store_command_state(
    command: _WorkspaceBuildCommand,
    value: tuple[object, object, _WorkspacePreparedReceipt, float, str, bytes],
) -> None:
    """Deterministic state-store cut used by return-loss tests."""

    _COMMANDS[command] = value


def _acquire_command_gate(
    command: _WorkspaceBuildCommand,
    deadline: float,
) -> _WorkspaceBuildCommandGate:
    if (
        type(command) is not _WorkspaceBuildCommand
        or type(deadline) is not float
        or not math.isfinite(deadline)
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    gate = _COMMAND_GATES.get(command)
    if type(gate) is not _WorkspaceBuildCommandGate:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    remaining = max(0.0, deadline - time.monotonic())
    acquired = (
        gate.lock.acquire(blocking=False)
        if remaining <= 0.0
        else gate.lock.acquire(timeout=remaining)
    )
    if not acquired:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return gate


__all__: list[str] = []

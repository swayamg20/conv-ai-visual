"""Path-free process association for one canonical workspace build command."""

from __future__ import annotations

import hashlib
import math

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _FAILURE,
    _PROCESS_ASSOCIATIONS,
    _WorkspaceBuildCommand,
    _WorkspaceBuildHandoffError,
)


def _associate_workspace_build_process(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    process_owner: object,
    expected_spec: object,
    expected_raw_destination: object,
) -> object:
    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _resolve_build_process_owner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildProcessOwner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
        _RawBuildProcessDestination,
        _RelayLinuxBuildSpec,
    )

    command_state = _COMMANDS.get(command)
    if (
        type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not owner_token
        or command_state[1] is not record_token
        or command_state[4] != "building"
        or type(process_owner) is not _RelayLinuxBuildProcessOwner
        or type(expected_spec) is not _RelayLinuxBuildSpec
        or type(expected_raw_destination) is not _RawBuildProcessDestination
        or process_owner._spec is not expected_spec
        or process_owner._raw_destination is not expected_raw_destination
        or not expected_spec._matches_destination(expected_raw_destination)
        or _resolve_build_process_owner(process_owner) is not process_owner
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    spec_fingerprint = _spawn_spec_fingerprint(expected_spec)
    if spec_fingerprint != command_state[5]:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    candidate = (
        owner_token,
        record_token,
        process_owner._owner_token,
        process_owner._cleanup_authority,
        spec_fingerprint,
        None,
        "associated",
    )
    intended = (*candidate[:6], "preown-intended")
    existing = _PROCESS_ASSOCIATIONS.get(command)
    if existing not in {intended, candidate}:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    _PROCESS_ASSOCIATIONS[command] = candidate
    if _PROCESS_ASSOCIATIONS.get(command) != candidate:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return process_owner._cleanup_authority


def _intend_workspace_build_process_association(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    process_owner: object,
    expected_spec: object,
    expected_raw_destination: object,
) -> object:
    """Publish cleanup authority before process-owner registration can begin."""

    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildProcessOwner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
        _RawBuildProcessDestination,
        _RelayLinuxBuildSpec,
    )

    command_state = _COMMANDS.get(command)
    if (
        type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not owner_token
        or command_state[1] is not record_token
        or command_state[4] != "building"
        or type(process_owner) is not _RelayLinuxBuildProcessOwner
        or type(expected_spec) is not _RelayLinuxBuildSpec
        or type(expected_raw_destination) is not _RawBuildProcessDestination
        or process_owner._spec is not expected_spec
        or process_owner._raw_destination is not expected_raw_destination
        or not expected_spec._matches_destination(expected_raw_destination)
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    spec_fingerprint = _spawn_spec_fingerprint(expected_spec)
    if spec_fingerprint != command_state[5]:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    intended = (
        owner_token,
        record_token,
        process_owner._owner_token,
        process_owner._cleanup_authority,
        spec_fingerprint,
        None,
        "preown-intended",
    )
    existing = _PROCESS_ASSOCIATIONS.setdefault(command, intended)
    if existing != intended or _PROCESS_ASSOCIATIONS.get(command) != intended:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return process_owner._cleanup_authority


def _workspace_build_process_association_matches(
    command: _WorkspaceBuildCommand,
    *,
    process_owner: object,
    expected_spec: object,
    expected_raw_destination: object,
    build_deadline: float,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _resolve_build_process_owner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildProcessOwner,
    )

    state = _PROCESS_ASSOCIATIONS.get(command)
    command_state = _COMMANDS.get(command)
    return bool(
        type(state) is tuple
        and len(state) == 7
        and type(command_state) is tuple
        and len(command_state) == 6
        and command_state[3] == build_deadline
        and command_state[4] == "building"
        and command_state[5] == state[4]
        and type(process_owner) is _RelayLinuxBuildProcessOwner
        and process_owner._owner_token is state[2]
        and process_owner._cleanup_authority is state[3]
        and process_owner._spec is expected_spec
        and process_owner._raw_destination is expected_raw_destination
        and _spawn_spec_fingerprint(expected_spec) == state[4]
        and _resolve_build_process_owner(process_owner) is process_owner
    )


def _observe_workspace_build_process_zero(
    command: _WorkspaceBuildCommand,
    *,
    process_owner: object,
    process_receipt: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _build_process_terminal,
        _resolve_build_process_owner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildProcessOwner,
        _RelayLinuxBuildProcessReceipt,
    )

    state = _PROCESS_ASSOCIATIONS.get(command)
    if (
        type(state) is not tuple
        or len(state) != 7
        or state[6] not in {"associated", "zero-observed"}
        or type(process_owner) is not _RelayLinuxBuildProcessOwner
        or process_owner._owner_token is not state[2]
        or process_owner._cleanup_authority is not state[3]
        or _resolve_build_process_owner(process_owner) is not process_owner
        or process_owner._facade_state._phase_value() != "joined"
        or type(process_receipt) is not _RelayLinuxBuildProcessReceipt
        or not process_receipt._matches(state[2])
        or process_owner._result_destination._read() is not process_receipt
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    terminal = _build_process_terminal(process_owner)
    if terminal is None or terminal.returncode != 0 or terminal.succeeded is not True:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    observed = (*state[:5], process_receipt, "zero-observed")
    _PROCESS_ASSOCIATIONS[command] = observed
    return _PROCESS_ASSOCIATIONS.get(command) == observed


def _complete_workspace_build_process(
    command: _WorkspaceBuildCommand,
    *,
    process_receipt: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _resolve_build_process_owner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildProcessReceipt,
    )

    state = _PROCESS_ASSOCIATIONS.get(command)
    if (
        type(state) is not tuple
        or len(state) != 7
        or state[6] not in {"zero-observed", "released-zero"}
        or type(process_receipt) is not _RelayLinuxBuildProcessReceipt
        or not process_receipt._matches(state[2])
        or state[5] is not process_receipt
        or _resolve_build_process_owner(state[3]) is not None
    ):
        raise _WorkspaceBuildHandoffError(_FAILURE)
    completed = (*state[:5], process_receipt, "released-zero")
    _PROCESS_ASSOCIATIONS[command] = completed
    return _PROCESS_ASSOCIATIONS.get(command) == completed


def _workspace_build_process_cleanup_authority(
    command: _WorkspaceBuildCommand,
) -> object | None:
    state = _PROCESS_ASSOCIATIONS.get(command)
    return state[3] if type(state) is tuple and len(state) == 7 else None


def _workspace_build_process_completed_zero(
    command: _WorkspaceBuildCommand,
    process_receipt: object,
) -> bool:
    state = _PROCESS_ASSOCIATIONS.get(command)
    return bool(
        type(state) is tuple
        and len(state) == 7
        and state[5] is process_receipt
        and state[6] == "released-zero"
    )


def _complete_failed_workspace_build_process(
    command: _WorkspaceBuildCommand,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _resolve_build_process_owner,
    )

    state = _PROCESS_ASSOCIATIONS.get(command)
    if state is None:
        return True
    if (
        type(state) is not tuple
        or len(state) != 7
        or state[6] not in {"preown-intended", "associated", "released-failed"}
        or _resolve_build_process_owner(state[3]) is not None
    ):
        return False
    released = (*state[:5], None, "released-failed")
    _PROCESS_ASSOCIATIONS[command] = released
    return _PROCESS_ASSOCIATIONS.get(command) == released


def _workspace_build_process_association_phase(
    command: _WorkspaceBuildCommand,
) -> str | None:
    state = _PROCESS_ASSOCIATIONS.get(command)
    return state[6] if type(state) is tuple and len(state) == 7 else None


def _workspace_build_process_released_for_cleanup(
    command: _WorkspaceBuildCommand,
    *,
    owner_token: object,
    record_token: object,
    prepared: object,
    build_deadline: float,
    expected_spawn_fingerprint: bytes,
) -> bool:
    """Prove the exact command-associated process graph is canonically absent."""

    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _build_process_registries_are_empty,
        _resolve_build_process_owner,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
        _RelayLinuxBuildCleanupAuthority,
        _RelayLinuxBuildProcessReceipt,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _WorkspacePreparedReceipt,
    )

    if (
        type(command) is not _WorkspaceBuildCommand
        or type(owner_token) is not object
        or type(record_token) is not object
        or type(prepared) is not _WorkspacePreparedReceipt
        or type(build_deadline) is not float
        or not math.isfinite(build_deadline)
        or type(expected_spawn_fingerprint) is not bytes
        or len(expected_spawn_fingerprint) != 32
    ):
        return False
    command_state = _COMMANDS.get(command)
    association = _PROCESS_ASSOCIATIONS.get(command)
    if (
        type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not owner_token
        or command_state[1] is not record_token
        or command_state[2] is not prepared
        or type(command_state[3]) is not float
        or not math.isfinite(command_state[3])
        or command_state[3] != build_deadline
        or type(command_state[4]) is not str
        or command_state[4] not in {"built", "cancelled", "failed"}
        or type(command_state[5]) is not bytes
        or len(command_state[5]) != 32
        or command_state[5] != expected_spawn_fingerprint
        or type(association) is not tuple
        or len(association) != 7
        or association[0] is not owner_token
        or association[1] is not record_token
        or type(association[2]) is not object
        or type(association[3]) is not _RelayLinuxBuildCleanupAuthority
        or not association[3]._matches_owner_token(association[2])
        or type(association[4]) is not bytes
        or len(association[4]) != 32
        or association[4] != expected_spawn_fingerprint
        or type(association[6]) is not str
        or association[6] not in {"released-zero", "released-failed"}
    ):
        return False
    if association[6] == "released-zero":
        receipt = association[5]
        if type(receipt) is not _RelayLinuxBuildProcessReceipt:
            return False
        try:
            receipt_owner = object.__getattribute__(receipt, "_owner_token")
            receipt_status = object.__getattribute__(receipt, "status")
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False
        if not (
            receipt_owner is association[2]
            and type(receipt_status) is str
            and receipt_status == "build-process-exited-zero"
        ):
            return False
    elif association[5] is not None:
        return False
    try:
        return bool(
            _resolve_build_process_owner(association[3]) is None
            and _build_process_registries_are_empty()
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _forget_workspace_build_process(command: _WorkspaceBuildCommand) -> bool:
    """Drop the association only after exact process-registry absence."""

    from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
        _resolve_build_process_owner,
    )

    state = _PROCESS_ASSOCIATIONS.get(command)
    if state is None:
        return True
    if type(state) is not tuple or len(state) != 7:
        return False
    try:
        absent = _resolve_build_process_owner(state[3]) is None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    if not absent:
        return False
    _PROCESS_ASSOCIATIONS.pop(command, None)
    return command not in _PROCESS_ASSOCIATIONS


def _spawn_spec_fingerprint(spec: object) -> bytes:
    from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
        _RelayLinuxBuildSpec,
    )

    if type(spec) is not _RelayLinuxBuildSpec:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    argv, cwd, environment = spec._spawn_values()
    return _spawn_values_fingerprint(argv, str(cwd), environment)


def _workspace_request_spawn_fingerprint(request: object) -> bytes:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
        _RelayLinuxBuildWorkspaceRequest,
    )

    if type(request) is not _RelayLinuxBuildWorkspaceRequest:
        raise _WorkspaceBuildHandoffError(_FAILURE)
    return _spawn_values_fingerprint(
        (str(request._node), str(request._next_cli), "build", "--webpack"),
        str(request._workspace),
        request._environment_values(),
    )


def _spawn_values_fingerprint(
    argv: tuple[str, ...],
    cwd: str,
    environment: dict[str, str],
) -> bytes:
    values = (*argv, cwd, *(f"{key}={environment[key]}" for key in sorted(environment)))
    digest = hashlib.sha256(b"murmur-relay-workspace-build-spec-v1\x00")
    for value in values:
        if type(value) is not str:
            raise _WorkspaceBuildHandoffError(_FAILURE)
        encoded = value.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


__all__: list[str] = []

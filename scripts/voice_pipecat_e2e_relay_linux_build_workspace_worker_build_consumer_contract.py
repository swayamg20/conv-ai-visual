"""Exact nonblocking graph predicates for one consumed workspace build."""

from __future__ import annotations

import math

from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildCleanupAuthority,
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
    _RelayLinuxBuildWorkspaceRequest,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values import (
    _BUILD_CONSUMERS,
    _BUILT_BY_CONSUMER,
    _CONSUMED_HISTORY,
    _TOKEN,
    _WorkspaceBuiltConsumerState,
    _WorkspaceBuiltConsumerToken,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _workspace_request_spawn_fingerprint,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_BY_COMMAND,
    _BUILT_LEASES,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMAND_CONTROLLERS,
    _COMMAND_GATES,
    _COMMANDS,
    _CONTROLLER_COMMANDS,
    _PROCESS_ASSOCIATIONS,
    _WorkspaceBuildCommand,
    _WorkspaceBuildCommandGate,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _workspace_revoked_prepared_build_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspaceBuiltRuntimeProof,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_PHASES = frozenset({"consume-intended", "in-use", "use-released", "revoked", "acknowledged"})


def _consumer_state(
    lease: tuple[object, ...],
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
    consumer_key: object,
    phase: str,
) -> _WorkspaceBuiltConsumerState | None:
    request = owner._request
    controller = bundle._controller
    proof = lease[3]
    try:
        fingerprint = _workspace_request_spawn_fingerprint(request)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None
    if (
        type(request) is not _RelayLinuxBuildWorkspaceRequest
        or type(controller) is not _WorkspaceWorkerController
        or type(fingerprint) is not bytes
        or len(fingerprint) != 32
        or type(proof) is not _WorkspaceBuiltRuntimeProof
        or (digest := proof._canonical_digest()) is None
        or type(lease[4]) is not _RelayLinuxBuildProcessReceipt
    ):
        return None
    return (
        lease[0],
        lease[1],
        lease[2],
        digest,
        lease[4],
        consumer,
        consumer_key,
        request,
        fingerprint,
        controller,
        owner,
        bundle,
        construction,
        phase,
    )


def _canonical_consumer_graph_matches(
    receipt: object,
    state: object,
    *,
    require_consumer_maps: bool = True,
) -> bool:
    if not _consumer_state_shape(state):
        return False
    command = state[2]
    command_state = _COMMANDS.get(command)
    association = _PROCESS_ASSOCIATIONS.get(command)
    gate = _COMMAND_GATES.get(command)
    controller_ref = _COMMAND_CONTROLLERS.get(command)
    command_ref = _CONTROLLER_COMMANDS.get(state[9])
    try:
        controller = controller_ref() if controller_ref is not None else None
        bound_command = command_ref() if command_ref is not None else None
        process_owner = object.__getattribute__(state[4], "_owner_token")
        process_status = object.__getattribute__(state[4], "status")
        request_fingerprint = _workspace_request_spawn_fingerprint(state[7])
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return bool(
        _consumer_authority_matches(receipt, state)
        and _command_graph_matches(command_state, state, controller, bound_command)
        and type(association) is tuple
        and len(association) == 7
        and association[0] is state[0]
        and association[1] is state[1]
        and type(association[2]) is object
        and type(association[3]) is _RelayLinuxBuildCleanupAuthority
        and association[3]._matches_owner_token(association[2])
        and type(association[4]) is bytes
        and association[4] == state[8]
        and association[5] is state[4]
        and type(association[6]) is str
        and association[6] == "released-zero"
        and process_owner is association[2]
        and type(process_status) is str
        and process_status == "build-process-exited-zero"
        and type(gate) is _WorkspaceBuildCommandGate
        and request_fingerprint == state[8]
        and len(_COMMANDS) == 1
        and len(_COMMAND_GATES) == 1
        and len(_COMMAND_CONTROLLERS) == 1
        and len(_CONTROLLER_COMMANDS) == 1
        and len(_PROCESS_ASSOCIATIONS) == 1
        and _BUILT_BY_COMMAND.get(command) is receipt
        and len(_BUILT_BY_COMMAND) == 1
        and len(_BUILT_LEASES) == 1
        and (state[13] == "consume-intended" or _CONSUMED_HISTORY.get(receipt) is state[5])
        and (
            not require_consumer_maps
            or (
                _BUILD_CONSUMERS.get(receipt) is state
                and _BUILT_BY_CONSUMER.get(state[5]) is receipt
                and len(_BUILD_CONSUMERS) == 1
                and len(_BUILT_BY_CONSUMER) == 1
            )
        )
    )


def _command_graph_matches(
    command_state: object,
    state: _WorkspaceBuiltConsumerState,
    controller: object,
    bound_command: object,
) -> bool:
    return bool(
        type(command_state) is tuple
        and len(command_state) == 6
        and command_state[0] is state[0]
        and command_state[1] is state[1]
        and type(command_state[3]) is float
        and math.isfinite(command_state[3])
        and type(command_state[4]) is str
        and command_state[4] in {"built", "cancelled"}
        and type(command_state[5]) is bytes
        and len(command_state[5]) == 32
        and command_state[5] == state[8]
        and controller is state[9]
        and bound_command is state[2]
        and _workspace_revoked_prepared_build_matches(
            command_state[2],
            state[0],
            state[1],
            state[2],
            command_state[3],
        )
    )


def _active_worker_handoff_matches(
    receipt: object,
    lease: object,
    owner_token: object,
    record_token: object,
    controller: object,
) -> bool:
    if not _active_lease_shape(receipt, lease):
        return False
    command = lease[2]
    command_state = _COMMANDS.get(command)
    controller_ref = _COMMAND_CONTROLLERS.get(command)
    command_ref = _CONTROLLER_COMMANDS.get(controller)
    try:
        internal_owner = object.__getattribute__(receipt, "_owner_token")
        internal_record = object.__getattribute__(receipt, "_record_token")
        status = object.__getattribute__(receipt, "status")
        bound_controller = controller_ref() if controller_ref is not None else None
        bound_command = command_ref() if command_ref is not None else None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return bool(
        lease[0] is owner_token
        and lease[1] is record_token
        and internal_owner is owner_token
        and internal_record is record_token
        and type(status) is str
        and status == "workspace-built"
        and type(command_state) is tuple
        and len(command_state) == 6
        and command_state[0] is owner_token
        and command_state[1] is record_token
        and command_state[4] == "built"
        and bound_controller is controller
        and bound_command is command
        and _workspace_revoked_prepared_build_matches(
            command_state[2], owner_token, record_token, command, command_state[3]
        )
    )


def _active_lease_shape(receipt: object, lease: object) -> bool:
    return bool(
        type(receipt) is _WorkspaceBuiltReceipt
        and type(lease) is tuple
        and len(lease) == 6
        and type(lease[0]) is object
        and type(lease[1]) is object
        and type(lease[2]) is _WorkspaceBuildCommand
        and type(lease[3]) is _WorkspaceBuiltRuntimeProof
        and lease[3]._matches_canonical(
            owner_token=lease[0],
            record_token=lease[1],
        )
        and type(lease[4]) is _RelayLinuxBuildProcessReceipt
        and type(lease[5]) is str
        and lease[5] == "active"
        and _BUILT_BY_COMMAND.get(lease[2]) is receipt
    )


def _consumed_lease_matches(receipt: object, state: _WorkspaceBuiltConsumerState) -> bool:
    lease = _BUILT_LEASES.get(receipt)
    return bool(_lease_identity_matches(receipt, lease, state) and lease[5] == "consumed")


def _active_consumer_lease_matches(
    receipt: object,
    state: _WorkspaceBuiltConsumerState,
) -> bool:
    lease = _BUILT_LEASES.get(receipt)
    return bool(_lease_identity_matches(receipt, lease, state) and lease[5] == "active")


def _revoked_lease_matches(receipt: object, state: _WorkspaceBuiltConsumerState) -> bool:
    lease = _BUILT_LEASES.get(receipt)
    return bool(_lease_identity_matches(receipt, lease, state) and lease[5] == "revoked")


def _lease_identity_matches(
    receipt: object,
    lease: object,
    state: _WorkspaceBuiltConsumerState,
) -> bool:
    return bool(
        type(lease) is tuple
        and len(lease) == 6
        and lease[0] is state[0]
        and lease[1] is state[1]
        and lease[2] is state[2]
        and type(lease[3]) is _WorkspaceBuiltRuntimeProof
        and lease[3]._matches(
            owner_token=state[0],
            record_token=state[1],
            output_digest=state[3],
        )
        and lease[4] is state[4]
        and type(lease[5]) is str
        and _BUILT_BY_COMMAND.get(state[2]) is receipt
    )


def _consumer_state_shape(state: object) -> bool:
    return bool(
        type(state) is tuple
        and len(state) == 14
        and type(state[0]) is object
        and type(state[1]) is object
        and type(state[2]) is _WorkspaceBuildCommand
        and type(state[3]) is bytes
        and len(state[3]) == 32
        and type(state[4]) is _RelayLinuxBuildProcessReceipt
        and type(state[5]) is _WorkspaceBuiltConsumerToken
        and state[5]._authentic is _TOKEN
        and state[0] is state[5]._owner_token
        and state[1] is state[5]._record_token
        and state[2] is state[5]._command
        and state[3] == state[5]._digest
        and state[4] is state[5]._process_receipt
        and state[6] is state[5]._consumer_key
        and type(state[7]) is _RelayLinuxBuildWorkspaceRequest
        and state[7] is state[5]._request
        and type(state[8]) is bytes
        and len(state[8]) == 32
        and type(state[9]) is _WorkspaceWorkerController
        and state[9] is state[5]._controller
        and type(state[10]) is _RelayLinuxBuildWorkspaceOwner
        and state[10] is state[5]._owner
        and type(state[11]) is _WorkspaceWorkerBundle
        and state[11] is state[5]._bundle
        and type(state[12]) is _WorkspaceWorkerThreadReceipt
        and state[12] is state[5]._construction
        and type(state[13]) is str
        and state[13] in _PHASES
    )


def _consumer_authority_matches(
    receipt: object,
    state: _WorkspaceBuiltConsumerState,
) -> bool:
    try:
        owner = state[10]
        bundle = state[11]
        construction = state[12]
        authority = owner._cleanup_authority
        return bool(
            state[5]._receipt is receipt
            and owner._request is state[7]
            and authority._request is state[7]
            and authority._key is state[0]
            and bundle._owner_token is state[0]
            and bundle._controller is state[9]
            and bundle._prepared_destination is owner._receipt_destination
            and construction._owner_token is state[0]
            and construction._record_token is state[1]
            and construction._coherent is True
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _consumer_state_matches(state: object, expected: object, phase: str) -> bool:
    return bool(
        _consumer_state_shape(state)
        and _consumer_state_shape(expected)
        and all(state[index] is expected[index] for index in (0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 12))
        and state[3] == expected[3]
        and state[8] == expected[8]
        and type(phase) is str
        and state[13] == phase
    )


def _consumer_command(receipt: object, consumer: object) -> _WorkspaceBuildCommand | None:
    state = _BUILD_CONSUMERS.get(receipt)
    return state[2] if _consumer_state_shape(state) and state[5] is consumer else None


def _tombstone_matches(state: object, expected: object) -> bool:
    return bool(
        type(state) is tuple
        and len(state) == 4
        and type(expected) is tuple
        and len(expected) == 4
        and state[0] is expected[0]
        and state[1] is expected[1]
        and type(state[2]) is bytes
        and len(state[2]) == 32
        and state[2] == expected[2]
        and type(state[3]) is str
        and state[3] == "forgotten"
    )


__all__: list[str] = []

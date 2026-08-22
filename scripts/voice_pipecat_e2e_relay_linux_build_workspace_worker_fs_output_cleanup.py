"""Process-absent cleanup of one exact build-owned Next output subtree."""

from __future__ import annotations

import math
import os
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceRequest,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _workspace_build_process_released_for_cleanup,
    _workspace_request_spawn_fingerprint,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _workspace_built_lease_is_revoked_or_absent,
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
    _FAILURE,
    _workspace_revoked_prepared_build_matches,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspacePreparedReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _open_directory_at,
    _require_named_identity,
    _stable_binding,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspacePreparedDestinationBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_CLEANUP_TOKEN = object()
_DIST_PARENT = ".next-voice-e2e"
_MAX_OUTPUT_CLEANUP_NODES = 8192
_OUTPUT_SCAN_BATCH = 256


class _OutputDirectoryCleanup:
    """One retained directory descriptor through rmdir and absence proof."""

    __slots__ = (
        "closed",
        "descriptor",
        "identity",
        "name",
        "parent_fd",
        "pending",
        "remove_entered",
        "removed",
    )

    def __init__(
        self,
        parent_fd: int,
        name: str,
        descriptor: int,
        identity: _WorkspaceFilesystemIdentity,
    ) -> None:
        self.parent_fd = parent_fd
        self.name = name
        self.descriptor = descriptor
        self.identity = identity
        self.pending: list[str] = []
        self.remove_entered = False
        self.removed = False
        self.closed = False


class _WorkspaceBuildOutputCleanupState:
    """Worker-local retry graph for the fresh reserved output namespace."""

    __slots__ = (
        "baseline",
        "build_deadline",
        "command",
        "complete",
        "controller",
        "descriptors",
        "expected_spawn_fingerprint",
        "owner_token",
        "prepared",
        "record_token",
        "removal_entered",
        "root_leaf_identity",
        "run_id",
        "scope_admitted",
        "stack",
        "synced",
        "workspace_fd",
        "workspace_identity",
    )

    def __init__(
        self,
        token: object,
        *,
        command: _WorkspaceBuildCommand,
        controller: _WorkspaceWorkerController,
        prepared: _WorkspacePreparedReceipt,
        baseline: _WorkspacePreparedDestinationBaseline,
        owner_token: object,
        record_token: object,
        build_deadline: float,
        expected_spawn_fingerprint: bytes,
        workspace_fd: int,
        workspace_identity: _WorkspaceFilesystemIdentity,
        run_id: str,
        descriptors: _WorkspaceDescriptorSet,
    ) -> None:
        if (
            token is not _CLEANUP_TOKEN
            or type(command) is not _WorkspaceBuildCommand
            or type(controller) is not _WorkspaceWorkerController
            or not controller._matches(owner_token)
            or type(prepared) is not _WorkspacePreparedReceipt
            or type(baseline) is not _WorkspacePreparedDestinationBaseline
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(build_deadline) is not float
            or type(expected_spawn_fingerprint) is not bytes
            or len(expected_spawn_fingerprint) != 32
            or type(workspace_fd) is not int
            or type(workspace_identity) is not _WorkspaceFilesystemIdentity
            or type(run_id) is not str
            or not run_id
            or "/" in run_id
            or "\x00" in run_id
            or type(descriptors) is not _WorkspaceDescriptorSet
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        self.command = command
        self.controller = controller
        self.prepared = prepared
        self.baseline = baseline
        self.owner_token = owner_token
        self.record_token = record_token
        self.build_deadline = build_deadline
        self.expected_spawn_fingerprint = expected_spawn_fingerprint
        self.workspace_fd = workspace_fd
        self.workspace_identity = workspace_identity
        self.run_id = run_id
        self.descriptors = descriptors
        self.stack: list[_OutputDirectoryCleanup] = []
        self.root_leaf_identity: _WorkspaceFilesystemIdentity | None = None
        self.scope_admitted = False
        self.removal_entered = False
        self.synced = False
        self.complete = False


def _new_workspace_build_output_cleanup_state(
    *,
    command: _WorkspaceBuildCommand,
    prepared: _WorkspacePreparedReceipt,
    baseline: _WorkspacePreparedDestinationBaseline,
    owner_token: object,
    record_token: object,
    request: _RelayLinuxBuildWorkspaceRequest,
    workspace_fd: int,
    workspace_identity: _WorkspaceFilesystemIdentity,
    run_id: str,
    descriptors: _WorkspaceDescriptorSet,
) -> _WorkspaceBuildOutputCleanupState:
    if (
        type(command) is not _WorkspaceBuildCommand
        or type(prepared) is not _WorkspacePreparedReceipt
        or type(baseline) is not _WorkspacePreparedDestinationBaseline
        or type(owner_token) is not object
        or type(record_token) is not object
        or type(request) is not _RelayLinuxBuildWorkspaceRequest
        or type(workspace_fd) is not int
        or type(workspace_identity) is not _WorkspaceFilesystemIdentity
        or type(run_id) is not str
        or not run_id
        or "/" in run_id
        or "\x00" in run_id
        or type(descriptors) is not _WorkspaceDescriptorSet
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    command_state = _COMMANDS.get(command)
    expected_spawn_fingerprint = _workspace_request_spawn_fingerprint(request)
    owned_workspace = descriptors.identity(workspace_fd)
    controller = _workspace_build_cleanup_controller(command, owner_token)
    if (
        type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not owner_token
        or command_state[1] is not record_token
        or command_state[2] is not prepared
        or type(command_state[3]) is not float
        or not math.isfinite(command_state[3])
        or type(command_state[5]) is not bytes
        or len(command_state[5]) != 32
        or command_state[5] != expected_spawn_fingerprint
        or controller is None
        or request._run_id != run_id
        or not baseline._matches(
            owner_token=owner_token,
            record_token=record_token,
            run_id=run_id,
            workspace_binding=_stable_binding(workspace_identity),
        )
        or _stable_binding(owned_workspace) != baseline.workspace_binding
        or _stable_binding(workspace_identity) != baseline.workspace_binding
        or not _workspace_build_process_released_for_cleanup(
            command,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
            build_deadline=command_state[3],
            expected_spawn_fingerprint=expected_spawn_fingerprint,
        )
        or not _workspace_revoked_prepared_build_matches(
            prepared,
            owner_token,
            record_token,
            command,
            command_state[3],
        )
        or not _workspace_built_lease_is_revoked_or_absent(
            command,
            owner_token,
            record_token,
        )
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return _WorkspaceBuildOutputCleanupState(
        _CLEANUP_TOKEN,
        command=command,
        controller=controller,
        prepared=prepared,
        baseline=baseline,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=command_state[3],
        expected_spawn_fingerprint=expected_spawn_fingerprint,
        workspace_fd=workspace_fd,
        workspace_identity=workspace_identity,
        run_id=run_id,
        descriptors=descriptors,
    )


def _cleanup_workspace_build_output(state: _WorkspaceBuildOutputCleanupState) -> bool:
    """Remove rejected output only after canonical process-registry absence."""

    if type(state) is not _WorkspaceBuildOutputCleanupState:
        raise _WorkspaceFilesystemError(_FAILURE)
    if not state.baseline._matches(
        owner_token=state.owner_token,
        record_token=state.record_token,
        run_id=state.run_id,
        workspace_binding=_stable_binding(state.workspace_identity),
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    if (
        _workspace_build_cleanup_controller(state.command, state.owner_token)
        is not state.controller
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    try:
        process_released = _workspace_build_process_released_for_cleanup(
            state.command,
            owner_token=state.owner_token,
            record_token=state.record_token,
            prepared=state.prepared,
            build_deadline=state.build_deadline,
            expected_spawn_fingerprint=state.expected_spawn_fingerprint,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _WorkspaceFilesystemError(_FAILURE) from None
    if not process_released:
        raise _WorkspaceFilesystemError(_FAILURE)
    if not _workspace_revoked_prepared_build_matches(
        state.prepared,
        state.owner_token,
        state.record_token,
        state.command,
        state.build_deadline,
    ) or not _workspace_built_lease_is_revoked_or_absent(
        state.command,
        state.owner_token,
        state.record_token,
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    current_workspace = _WorkspaceFilesystemIdentity.from_stat(os.fstat(state.workspace_fd))
    if _stable_binding(current_workspace) != _stable_binding(state.workspace_identity):
        raise _WorkspaceFilesystemError(_FAILURE)
    if state.complete:
        return True
    if not state.scope_admitted:
        _admit_reserved_output_parent(state)
        if not state.removal_entered and not state.stack and state.root_leaf_identity is None:
            state.complete = True
            return True
    budget = [_MAX_OUTPUT_CLEANUP_NODES]
    if state.root_leaf_identity is not None:
        if not _remove_reserved_output_leaf(state, budget):
            return False
    if state.stack and not _advance_output_cleanup(state, budget):
        return False
    if state.stack or state.root_leaf_identity is not None:
        return False
    if not _is_missing(state.workspace_fd, _DIST_PARENT):
        raise _WorkspaceFilesystemError(_FAILURE)
    if not state.synced:
        os.fsync(state.workspace_fd)
        state.synced = True
    state.complete = bool(state.synced and _is_missing(state.workspace_fd, _DIST_PARENT))
    return state.complete


def _workspace_build_cleanup_controller(
    command: _WorkspaceBuildCommand,
    owner_token: object,
) -> _WorkspaceWorkerController | None:
    """Prove the capacity-one command, association, gate, and controller graph."""

    if (
        type(command) is not _WorkspaceBuildCommand
        or type(owner_token) is not object
        or len(_COMMANDS) != 1
        or next(iter(_COMMANDS), None) is not command
        or len(_PROCESS_ASSOCIATIONS) != 1
        or next(iter(_PROCESS_ASSOCIATIONS), None) is not command
        or len(_COMMAND_GATES) != 1
        or next(iter(_COMMAND_GATES), None) is not command
        or type(_COMMAND_GATES.get(command)) is not _WorkspaceBuildCommandGate
    ):
        return None
    forward_reference = _COMMAND_CONTROLLERS.get(command)
    if (
        type(forward_reference) is not weakref.ReferenceType
        or len(_COMMAND_CONTROLLERS) != 1
        or next(iter(_COMMAND_CONTROLLERS), None) is not command
        or len(_CONTROLLER_COMMANDS) != 1
    ):
        return None
    controller = forward_reference()
    if type(controller) is not _WorkspaceWorkerController or not controller._matches(owner_token):
        return None
    reverse_reference = _CONTROLLER_COMMANDS.get(controller)
    if not (
        type(reverse_reference) is weakref.ReferenceType
        and reverse_reference() is command
        and next(iter(_CONTROLLER_COMMANDS), None) is controller
    ):
        return None
    return controller


def _admit_reserved_output_parent(state: _WorkspaceBuildOutputCleanupState) -> None:
    try:
        details = os.stat(
            _DIST_PARENT,
            dir_fd=state.workspace_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        state.scope_admitted = True
        return
    identity = _WorkspaceFilesystemIdentity.from_stat(details)
    _require_owned_output_identity(state, identity, details.st_uid)
    if identity.is_directory():
        state.stack.append(
            _open_output_directory(
                state,
                parent_fd=state.workspace_fd,
                name=_DIST_PARENT,
                identity=identity,
            )
        )
    else:
        state.root_leaf_identity = identity
    state.scope_admitted = True


def _remove_reserved_output_leaf(
    state: _WorkspaceBuildOutputCleanupState,
    budget: list[int],
) -> bool:
    identity = state.root_leaf_identity
    if identity is None:
        return True
    if budget[0] <= 0:
        return False
    budget[0] -= 1
    if _is_missing(state.workspace_fd, _DIST_PARENT):
        state.root_leaf_identity = None
        return True
    _require_same_output_node(state.workspace_fd, _DIST_PARENT, identity)
    state.removal_entered = True
    try:
        os.unlink(_DIST_PARENT, dir_fd=state.workspace_fd)
    finally:
        _require_missing(state.workspace_fd, _DIST_PARENT)
    state.root_leaf_identity = None
    return True


def _advance_output_cleanup(
    state: _WorkspaceBuildOutputCleanupState,
    budget: list[int],
) -> bool:
    while state.stack and budget[0] > 0:
        frame = state.stack[-1]
        if frame.remove_entered and _is_missing(frame.parent_fd, frame.name):
            frame.removed = True
        if frame.removed:
            if not frame.closed:
                frame.closed = state.descriptors.close(frame.descriptor)
                if not frame.closed:
                    raise _WorkspaceFilesystemError(_FAILURE)
            state.stack.pop()
            continue
        _require_output_stack(state)
        if not frame.pending:
            frame.pending.extend(
                reversed(
                    _scan_output_names(
                        frame.descriptor,
                        min(_OUTPUT_SCAN_BATCH, budget[0]),
                    )
                )
            )
        if frame.pending:
            name = frame.pending.pop()
            details = os.stat(name, dir_fd=frame.descriptor, follow_symlinks=False)
            identity = _WorkspaceFilesystemIdentity.from_stat(details)
            _require_owned_output_identity(state, identity, details.st_uid)
            budget[0] -= 1
            if identity.is_directory():
                frame.pending.clear()
                state.stack.append(
                    _open_output_directory(
                        state,
                        parent_fd=frame.descriptor,
                        name=name,
                        identity=identity,
                    )
                )
            else:
                _require_output_stack(state)
                _require_same_output_node(frame.descriptor, name, identity)
                try:
                    os.unlink(name, dir_fd=frame.descriptor)
                finally:
                    _require_missing(frame.descriptor, name)
            continue
        budget[0] -= 1
        _require_output_stack(state)
        frame.remove_entered = True
        if len(state.stack) == 1:
            state.removal_entered = True
        try:
            os.rmdir(frame.name, dir_fd=frame.parent_fd)
        finally:
            if _is_missing(frame.parent_fd, frame.name):
                frame.removed = True
        if not frame.removed:
            raise _WorkspaceFilesystemError(_FAILURE)
    return not state.stack


def _open_output_directory(
    state: _WorkspaceBuildOutputCleanupState,
    *,
    parent_fd: int,
    name: str,
    identity: _WorkspaceFilesystemIdentity,
) -> _OutputDirectoryCleanup:
    descriptor = state.descriptors.find(identity)
    if descriptor is None:
        descriptor = _open_directory_at(parent_fd, name, state.descriptors)
    opened = _require_named_identity(parent_fd, name, descriptor, directory=True)
    details = os.fstat(descriptor)
    if (
        _stable_binding(opened) != _stable_binding(identity)
        or opened.device != state.workspace_identity.device
        or details.st_uid != os.geteuid()
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return _OutputDirectoryCleanup(parent_fd, name, descriptor, opened)


def _require_owned_output_identity(
    state: _WorkspaceBuildOutputCleanupState,
    identity: _WorkspaceFilesystemIdentity,
    uid: int,
) -> None:
    if identity.device != state.workspace_identity.device or uid != os.geteuid():
        raise _WorkspaceFilesystemError(_FAILURE)


def _require_output_stack(state: _WorkspaceBuildOutputCleanupState) -> None:
    for frame in state.stack:
        if frame.removed:
            continue
        _require_same_output_node(frame.parent_fd, frame.name, frame.identity)
        current = state.descriptors.identity(frame.descriptor)
        if _stable_binding(current) != _stable_binding(frame.identity):
            raise _WorkspaceFilesystemError(_FAILURE)


def _scan_output_names(descriptor: int, limit: int) -> tuple[str, ...]:
    if type(descriptor) is not int or type(limit) is not int or limit <= 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    names: list[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            name = entry.name
            if (
                type(name) is not str
                or not name
                or name in {".", ".."}
                or "/" in name
                or "\x00" in name
            ):
                raise _WorkspaceFilesystemError(_FAILURE)
            names.append(name)
            if len(names) == limit:
                break
    return tuple(sorted(names))


def _require_same_output_node(
    parent_fd: int,
    name: str,
    identity: _WorkspaceFilesystemIdentity,
) -> None:
    current = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    )
    if _stable_binding(current) != _stable_binding(identity):
        raise _WorkspaceFilesystemError(_FAILURE)


def _require_missing(parent_fd: int, name: str) -> None:
    if not _is_missing(parent_fd, name):
        raise _WorkspaceFilesystemError(_FAILURE)


def _is_missing(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


__all__: list[str] = []

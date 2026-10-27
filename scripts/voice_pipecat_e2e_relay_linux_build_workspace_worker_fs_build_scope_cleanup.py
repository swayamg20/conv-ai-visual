"""Resumable cleanup of build mutations inside one fresh workspace/run root."""

from __future__ import annotations

import os

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _open_directory_at,
    _require_named_identity,
    _stable_binding,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_cleanup import (
    _cleanup_workspace_build_output,
    _WorkspaceBuildOutputCleanupState,
)

_SCOPE_TOKEN = object()
_CLEANUP_BUDGET = 8192
_SCAN_BATCH = 256
_OPEN_WINDOW = 8


class _ScopeDirectory:
    __slots__ = (
        "descriptor",
        "identity",
        "name",
        "pending",
        "remove_entered",
        "remove_self",
        "removed",
    )

    def __init__(
        self,
        *,
        name: str,
        descriptor: int | None,
        identity: _WorkspaceFilesystemIdentity,
        remove_self: bool,
    ) -> None:
        self.name = name
        self.descriptor = descriptor
        self.identity = identity
        self.remove_self = remove_self
        self.pending: list[str] = []
        self.remove_entered = False
        self.removed = False


class _WorkspaceBuildScopeCleanupState:
    """Authenticated retained DFS over only the worker-created scope."""

    __slots__ = (
        "_authentic",
        "complete",
        "descriptors",
        "output_state",
        "run_identity",
        "run_name",
        "run_parent_fd",
        "run_root_fd",
        "run_stack",
        "run_synced",
        "workspace_cleared",
        "workspace_fd",
        "workspace_identity",
        "workspace_name",
        "workspace_stack",
        "workspace_synced",
    )

    def __init__(
        self,
        token: object,
        *,
        output_state: _WorkspaceBuildOutputCleanupState,
        run_parent_fd: int,
        run_name: str,
        run_root_fd: int,
        run_identity: _WorkspaceFilesystemIdentity,
        workspace_name: str,
    ) -> None:
        if token is not _SCOPE_TOKEN:
            raise TypeError(_FAILURE)
        self._authentic = token
        self.output_state = output_state
        self.run_parent_fd = run_parent_fd
        self.run_name = run_name
        self.run_root_fd = run_root_fd
        self.run_identity = run_identity
        self.workspace_name = workspace_name
        self.workspace_fd = output_state.workspace_fd
        self.workspace_identity = output_state.workspace_identity
        self.descriptors = output_state.descriptors
        self.workspace_stack: list[_ScopeDirectory] = []
        self.run_stack: list[_ScopeDirectory] = []
        self.workspace_cleared = False
        self.workspace_synced = False
        self.run_synced = False
        self.complete = False


def _new_workspace_build_scope_cleanup_state(
    *,
    output_state: _WorkspaceBuildOutputCleanupState,
    run_parent_fd: int,
    run_name: str,
    run_root_fd: int,
    run_identity: _WorkspaceFilesystemIdentity,
    workspace_name: str,
    descriptors: _WorkspaceDescriptorSet,
) -> _WorkspaceBuildScopeCleanupState:
    if (
        type(output_state) is not _WorkspaceBuildOutputCleanupState
        or type(run_parent_fd) is not int
        or type(run_name) is not str
        or not run_name
        or "/" in run_name
        or "\x00" in run_name
        or type(run_root_fd) is not int
        or type(run_identity) is not _WorkspaceFilesystemIdentity
        or type(workspace_name) is not str
        or not workspace_name
        or "/" in workspace_name
        or "\x00" in workspace_name
        or type(descriptors) is not _WorkspaceDescriptorSet
        or output_state.descriptors is not descriptors
        or _stable_binding(descriptors.identity(run_root_fd)) != _stable_binding(run_identity)
        or _stable_binding(descriptors.identity(output_state.workspace_fd))
        != _stable_binding(output_state.workspace_identity)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    _require_scope_names(
        run_parent_fd,
        run_name,
        run_root_fd,
        run_identity,
        workspace_name,
        output_state.workspace_fd,
        output_state.workspace_identity,
    )
    return _WorkspaceBuildScopeCleanupState(
        _SCOPE_TOKEN,
        output_state=output_state,
        run_parent_fd=run_parent_fd,
        run_name=run_name,
        run_root_fd=run_root_fd,
        run_identity=run_identity,
        workspace_name=workspace_name,
    )


def _cleanup_workspace_build_scope(state: _WorkspaceBuildScopeCleanupState) -> bool:
    """Make bounded progress, preserving the two held root directory FDs."""

    if type(state) is not _WorkspaceBuildScopeCleanupState or state._authentic is not _SCOPE_TOKEN:
        raise _WorkspaceFilesystemError(_FAILURE)
    if not _cleanup_workspace_build_output(state.output_state):
        return False
    _require_scope_names(
        state.run_parent_fd,
        state.run_name,
        state.run_root_fd,
        state.run_identity,
        state.workspace_name,
        state.workspace_fd,
        state.workspace_identity,
    )
    if state.complete:
        return True
    budget = [_CLEANUP_BUDGET]
    if not state.workspace_cleared:
        if not state.workspace_stack:
            state.workspace_stack.append(_root_frame(state.workspace_fd, state.workspace_identity))
        if not _advance_scope(
            state.workspace_stack,
            state.workspace_fd,
            state.descriptors,
            state.workspace_identity.device,
            budget,
            skip_root_name=None,
        ):
            return False
        state.workspace_cleared = True
    if not state.workspace_synced:
        os.fsync(state.workspace_fd)
        state.workspace_synced = True
    if not state.run_stack:
        state.run_stack.append(_root_frame(state.run_root_fd, state.run_identity))
    if not _advance_scope(
        state.run_stack,
        state.run_root_fd,
        state.descriptors,
        state.run_identity.device,
        budget,
        skip_root_name=state.workspace_name,
    ):
        return False
    if not state.run_synced:
        os.fsync(state.run_root_fd)
        state.run_synced = True
    state.complete = bool(
        state.workspace_cleared
        and state.workspace_synced
        and state.run_synced
        and not state.workspace_stack
        and not state.run_stack
        and not _scan_names(state.workspace_fd, 1)
        and _scan_names(state.run_root_fd, 2) == (state.workspace_name,)
    )
    return state.complete


def _advance_scope(
    stack: list[_ScopeDirectory],
    root_fd: int,
    descriptors: _WorkspaceDescriptorSet,
    expected_device: int,
    budget: list[int],
    *,
    skip_root_name: str | None,
) -> bool:
    while stack and budget[0] > 0:
        frame = stack[-1]
        if frame.removed:
            if frame.descriptor is not None:
                raise _WorkspaceFilesystemError(_FAILURE)
            stack.pop()
            continue
        _require_root(root_fd, stack[0].identity, descriptors)
        if frame.remove_entered:
            if not _reconcile_scope_remove(
                stack,
                root_fd,
                descriptors,
                expected_device,
                budget,
            ):
                return False
            continue
        if not frame.pending:
            descriptor = _open_stack_descriptor(
                stack,
                root_fd,
                descriptors,
                expected_device,
                budget,
            )
            if descriptor is None:
                return False
            if budget[0] <= 0:
                return False
            names = _scan_names(descriptor, min(_SCAN_BATCH, budget[0]))
            if not frame.remove_self and skip_root_name is not None:
                names = tuple(name for name in names if name != skip_root_name)
            frame.pending.extend(reversed(names))
        if frame.pending:
            name = frame.pending.pop()
            descriptor = _open_stack_descriptor(
                stack,
                root_fd,
                descriptors,
                expected_device,
                budget,
            )
            if descriptor is None:
                frame.pending.append(name)
                return False
            if budget[0] <= 0:
                frame.pending.append(name)
                return False
            try:
                details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            identity = _WorkspaceFilesystemIdentity.from_stat(details)
            _require_owned(identity, details.st_uid, expected_device)
            budget[0] -= 1
            if identity.is_directory():
                child = _open_directory_at(descriptor, name, descriptors)
                try:
                    opened = _require_named_identity(
                        descriptor,
                        name,
                        child,
                        directory=True,
                    )
                    child_details = os.fstat(child)
                    if (
                        _stable_binding(opened) != _stable_binding(identity)
                        or opened.device != expected_device
                        or child_details.st_uid != os.geteuid()
                    ):
                        raise _WorkspaceFilesystemError(_FAILURE)
                except BaseException as error:
                    if not descriptors.close(child):
                        raise _WorkspaceFilesystemError(_FAILURE) from error
                    raise
                frame.pending.clear()
                stack.append(
                    _ScopeDirectory(
                        name=name,
                        descriptor=child,
                        identity=identity,
                        remove_self=True,
                    )
                )
                _trim_open_window(stack, root_fd, descriptors)
            else:
                _require_same(descriptor, name, identity)
                try:
                    os.unlink(name, dir_fd=descriptor)
                finally:
                    if not _missing(descriptor, name):
                        raise _WorkspaceFilesystemError(_FAILURE)
            continue
        if not frame.remove_self:
            stack.pop()
            continue
        frame.remove_entered = True
    return not stack


def _reconcile_scope_remove(
    stack: list[_ScopeDirectory],
    root_fd: int,
    descriptors: _WorkspaceDescriptorSet,
    expected_device: int,
    budget: list[int],
) -> bool:
    frame = stack[-1]
    if not frame.remove_self or not frame.remove_entered:
        raise _WorkspaceFilesystemError(_FAILURE)
    if frame.descriptor is not None:
        if not descriptors.close(frame.descriptor):
            raise _WorkspaceFilesystemError(_FAILURE)
        frame.descriptor = None
    parent = _open_stack_descriptor(
        stack[:-1],
        root_fd,
        descriptors,
        expected_device,
        budget,
    )
    if parent is None:
        return False
    if budget[0] <= 0:
        return False
    if _missing(parent, frame.name):
        frame.removed = True
        return True
    _require_same(parent, frame.name, frame.identity)
    if budget[0] <= 0:
        return False
    budget[0] -= 1
    try:
        os.rmdir(frame.name, dir_fd=parent)
    finally:
        frame.removed = _missing(parent, frame.name)
    if not frame.removed:
        raise _WorkspaceFilesystemError(_FAILURE)
    return True


def _root_frame(
    descriptor: int,
    identity: _WorkspaceFilesystemIdentity,
) -> _ScopeDirectory:
    return _ScopeDirectory(
        name="",
        descriptor=descriptor,
        identity=identity,
        remove_self=False,
    )


def _open_stack_descriptor(
    stack: list[_ScopeDirectory],
    root_fd: int,
    descriptors: _WorkspaceDescriptorSet,
    expected_device: int,
    budget: list[int],
) -> int | None:
    if not stack:
        raise _WorkspaceFilesystemError(_FAILURE)
    _require_root(root_fd, stack[0].identity, descriptors)
    start = 0
    current = root_fd
    for index in range(len(stack) - 1, -1, -1):
        candidate = stack[index].descriptor
        if candidate is not None:
            if _stable_binding(descriptors.identity(candidate)) != _stable_binding(
                stack[index].identity
            ):
                raise _WorkspaceFilesystemError(_FAILURE)
            start = index
            current = candidate
            break
    try:
        for frame in stack[start + 1 :]:
            if budget[0] <= 0:
                return None
            child = _open_directory_at(current, frame.name, descriptors)
            try:
                opened = _require_named_identity(
                    current,
                    frame.name,
                    child,
                    directory=True,
                )
                details = os.fstat(child)
                if (
                    _stable_binding(opened) != _stable_binding(frame.identity)
                    or opened.device != expected_device
                    or details.st_uid != os.geteuid()
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
            except BaseException:
                _close_transient(child, root_fd, descriptors)
                raise
            current = child
            frame.descriptor = child
            budget[0] -= 1
            _trim_open_window(stack, root_fd, descriptors)
        return current
    except BaseException:
        raise


def _require_root(
    root_fd: int,
    root_identity: _WorkspaceFilesystemIdentity,
    descriptors: _WorkspaceDescriptorSet,
) -> None:
    if _stable_binding(descriptors.identity(root_fd)) != _stable_binding(root_identity):
        raise _WorkspaceFilesystemError(_FAILURE)


def _close_transient(
    descriptor: int,
    root_fd: int,
    descriptors: _WorkspaceDescriptorSet,
) -> None:
    if descriptor == root_fd or not descriptors.close(descriptor):
        raise _WorkspaceFilesystemError(_FAILURE)


def _trim_open_window(
    stack: list[_ScopeDirectory],
    root_fd: int,
    descriptors: _WorkspaceDescriptorSet,
) -> None:
    opened = [frame for frame in stack[1:] if frame.descriptor is not None]
    while len(opened) > _OPEN_WINDOW:
        frame = opened.pop(0)
        descriptor = frame.descriptor
        if descriptor is None or descriptor == root_fd or not descriptors.close(descriptor):
            raise _WorkspaceFilesystemError(_FAILURE)
        frame.descriptor = None


def _require_scope_names(
    run_parent_fd: int,
    run_name: str,
    run_root_fd: int,
    run_identity: _WorkspaceFilesystemIdentity,
    workspace_name: str,
    workspace_fd: int,
    workspace_identity: _WorkspaceFilesystemIdentity,
) -> None:
    run = _require_named_identity(run_parent_fd, run_name, run_root_fd, directory=True)
    workspace = _require_named_identity(
        run_root_fd,
        workspace_name,
        workspace_fd,
        directory=True,
    )
    if (
        _stable_binding(run) != _stable_binding(run_identity)
        or _stable_binding(workspace) != _stable_binding(workspace_identity)
        or run.device != workspace.device
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _scan_names(descriptor: int, limit: int) -> tuple[str, ...]:
    if type(limit) is not int or limit <= 0:
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


def _require_owned(identity: _WorkspaceFilesystemIdentity, uid: int, device: int) -> None:
    if identity.device != device or uid != os.geteuid():
        raise _WorkspaceFilesystemError(_FAILURE)


def _require_same(
    parent_fd: int,
    name: str,
    identity: _WorkspaceFilesystemIdentity,
) -> None:
    current = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    )
    if _stable_binding(current) != _stable_binding(identity):
        raise _WorkspaceFilesystemError(_FAILURE)


def _missing(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


__all__: list[str] = []

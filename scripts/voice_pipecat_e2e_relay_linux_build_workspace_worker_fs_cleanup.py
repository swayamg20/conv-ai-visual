"""Exact bottom-up cleanup for one held relay build workspace."""

from __future__ import annotations

import os
import stat

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _bounded_names,
    _open_directory_at,
    _open_regular_at,
    _require_named_identity,
    _stable_binding,
    _WorkspaceDescriptorSet,
)

_MAX_CLEANUP_NODES = 16_384


class _WorkspaceCleanupState:
    """Worker-local retry phases retaining exact directory authority."""

    __slots__ = (
        "children_removed",
        "descriptors",
        "parent_synced",
        "root_closed",
        "root_identity",
        "root_name",
        "root_remove_entered",
        "root_removed",
        "run_parent_fd",
        "run_root_fd",
        "target",
        "workspace_closed",
        "workspace_fd",
        "workspace_identity",
        "workspace_name",
        "workspace_remove_entered",
        "workspace_removed",
    )

    def __init__(
        self,
        *,
        run_parent_fd: int,
        run_name: str,
        run_root_fd: int,
        run_identity: _WorkspaceFilesystemIdentity,
        workspace_name: str,
        workspace_fd: int,
        workspace_identity: _WorkspaceFilesystemIdentity,
        node_modules_target: str,
        descriptors: _WorkspaceDescriptorSet,
    ) -> None:
        self.run_parent_fd = run_parent_fd
        self.root_name = run_name
        self.run_root_fd = run_root_fd
        self.root_identity = run_identity
        self.workspace_name = workspace_name
        self.workspace_fd = workspace_fd
        self.workspace_identity = workspace_identity
        self.target = node_modules_target
        self.descriptors = descriptors
        self.children_removed = False
        self.workspace_removed = False
        self.workspace_remove_entered = False
        self.workspace_closed = False
        self.root_removed = False
        self.root_remove_entered = False
        self.root_closed = False
        self.parent_synced = False


class _EmptyRootCleanupState:
    """Retry phases for a root created before its workspace was adopted."""

    __slots__ = (
        "descriptors",
        "parent_synced",
        "root_closed",
        "root_identity",
        "root_name",
        "root_remove_entered",
        "root_removed",
        "run_parent_fd",
        "run_root_fd",
    )

    def __init__(
        self,
        *,
        run_parent_fd: int,
        run_name: str,
        run_root_fd: int,
        run_identity: _WorkspaceFilesystemIdentity,
        descriptors: _WorkspaceDescriptorSet,
    ) -> None:
        self.run_parent_fd = run_parent_fd
        self.root_name = run_name
        self.run_root_fd = run_root_fd
        self.root_identity = run_identity
        self.descriptors = descriptors
        self.root_removed = False
        self.root_remove_entered = False
        self.root_closed = False
        self.parent_synced = False


def _cleanup_workspace_root(state: _WorkspaceCleanupState) -> bool:
    if type(state) is not _WorkspaceCleanupState:
        raise _WorkspaceFilesystemError(_FAILURE)
    if not state.children_removed:
        _require_exact_named_directory(
            state.run_parent_fd,
            state.root_name,
            state.run_root_fd,
            state.root_identity,
        )
        _require_exact_named_directory(
            state.run_root_fd,
            state.workspace_name,
            state.workspace_fd,
            state.workspace_identity,
        )
        budget = [_MAX_CLEANUP_NODES]
        _remove_children(
            state.workspace_fd,
            descriptors=state.descriptors,
            budget=budget,
            expected_device=state.workspace_identity.device,
            root=True,
            node_modules_target=state.target,
        )
        state.children_removed = True
    if not state.workspace_removed:
        if state.workspace_remove_entered and _is_missing(
            state.run_root_fd,
            state.workspace_name,
        ):
            state.workspace_removed = True
        if not state.workspace_removed:
            _require_stable_named(
                state.run_root_fd,
                state.workspace_name,
                state.workspace_identity,
            )
            state.workspace_remove_entered = True
    if not state.workspace_removed:
        try:
            os.rmdir(state.workspace_name, dir_fd=state.run_root_fd)
        finally:
            if _is_missing(state.run_root_fd, state.workspace_name):
                state.workspace_removed = True
        if not state.workspace_removed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.workspace_closed:
        state.workspace_closed = state.descriptors.close(state.workspace_fd)
        if not state.workspace_closed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.root_removed:
        if state.root_remove_entered and _is_missing(
            state.run_parent_fd,
            state.root_name,
        ):
            state.root_removed = True
        if not state.root_removed:
            if _bounded_names(state.run_root_fd, 1):
                raise _WorkspaceFilesystemError(_FAILURE)
            _require_stable_named(
                state.run_parent_fd,
                state.root_name,
                state.root_identity,
            )
            state.root_remove_entered = True
    if not state.root_removed:
        try:
            os.rmdir(state.root_name, dir_fd=state.run_parent_fd)
        finally:
            if _is_missing(state.run_parent_fd, state.root_name):
                state.root_removed = True
        if not state.root_removed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.root_closed:
        state.root_closed = state.descriptors.close(state.run_root_fd)
        if not state.root_closed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.parent_synced:
        os.fsync(state.run_parent_fd)
        state.parent_synced = True
    return bool(
        state.children_removed
        and state.workspace_removed
        and state.workspace_closed
        and state.root_removed
        and state.root_closed
        and state.parent_synced
    )


def _cleanup_empty_root(state: _EmptyRootCleanupState) -> bool:
    if type(state) is not _EmptyRootCleanupState:
        raise _WorkspaceFilesystemError(_FAILURE)
    if not state.root_removed:
        if state.root_remove_entered and _is_missing(
            state.run_parent_fd,
            state.root_name,
        ):
            state.root_removed = True
        if not state.root_removed:
            if _bounded_names(state.run_root_fd, 1):
                raise _WorkspaceFilesystemError(_FAILURE)
            _require_exact_named_directory(
                state.run_parent_fd,
                state.root_name,
                state.run_root_fd,
                state.root_identity,
            )
            state.root_remove_entered = True
    if not state.root_removed:
        try:
            os.rmdir(state.root_name, dir_fd=state.run_parent_fd)
        finally:
            if _is_missing(state.run_parent_fd, state.root_name):
                state.root_removed = True
        if not state.root_removed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.root_closed:
        state.root_closed = state.descriptors.close(state.run_root_fd)
        if not state.root_closed:
            raise _WorkspaceFilesystemError(_FAILURE)
    if not state.parent_synced:
        os.fsync(state.run_parent_fd)
        state.parent_synced = True
    return state.root_removed and state.root_closed and state.parent_synced


def _remove_children(
    parent_fd: int,
    *,
    descriptors: _WorkspaceDescriptorSet,
    budget: list[int],
    expected_device: int,
    root: bool,
    node_modules_target: str,
) -> None:
    names = _bounded_names(parent_fd, budget[0] + 1)
    for name in names:
        if budget[0] <= 0:
            raise _WorkspaceFilesystemError(_FAILURE)
        budget[0] -= 1
        details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        identity = _WorkspaceFilesystemIdentity.from_stat(details)
        if identity.device != expected_device:
            raise _WorkspaceFilesystemError(_FAILURE)
        if stat.S_ISLNK(identity.mode):
            if (
                not root
                or name != "node_modules"
                or identity.links != 1
                or os.readlink(name, dir_fd=parent_fd) != node_modules_target
            ):
                raise _WorkspaceFilesystemError(_FAILURE)
            _require_stable_named(parent_fd, name, identity)
            os.unlink(name, dir_fd=parent_fd)
            _require_missing(parent_fd, name)
            continue
        if identity.is_directory():
            child = _open_directory_at(parent_fd, name, descriptors)
            try:
                opened = _require_named_identity(parent_fd, name, child, directory=True)
                if _stable_binding(opened) != _stable_binding(identity):
                    raise _WorkspaceFilesystemError(_FAILURE)
                _remove_children(
                    child,
                    descriptors=descriptors,
                    budget=budget,
                    expected_device=expected_device,
                    root=False,
                    node_modules_target=node_modules_target,
                )
            finally:
                if not descriptors.close(child):
                    raise _WorkspaceFilesystemError(_FAILURE)
            _require_stable_named(parent_fd, name, identity)
            os.rmdir(name, dir_fd=parent_fd)
            _require_missing(parent_fd, name)
            continue
        if identity.is_regular():
            child = _open_regular_at(parent_fd, name, descriptors)
            try:
                opened = _require_named_identity(parent_fd, name, child, directory=False)
                if _stable_binding(opened) != _stable_binding(identity):
                    raise _WorkspaceFilesystemError(_FAILURE)
            finally:
                if not descriptors.close(child):
                    raise _WorkspaceFilesystemError(_FAILURE)
            _require_stable_named(parent_fd, name, identity)
            os.unlink(name, dir_fd=parent_fd)
            _require_missing(parent_fd, name)
            continue
        raise _WorkspaceFilesystemError(_FAILURE)


def _require_exact_named_directory(
    parent_fd: int,
    name: str,
    descriptor: int,
    identity: _WorkspaceFilesystemIdentity,
) -> None:
    opened = _require_named_identity(parent_fd, name, descriptor, directory=True)
    if _stable_binding(opened) != _stable_binding(identity):
        raise _WorkspaceFilesystemError(_FAILURE)


def _require_stable_named(
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
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise _WorkspaceFilesystemError(_FAILURE)


def _is_missing(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


__all__: list[str] = []

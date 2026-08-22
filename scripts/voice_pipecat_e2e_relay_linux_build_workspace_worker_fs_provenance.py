"""Worker-local toolchain and named-anchor provenance for one workspace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_copy import (
    _hash_descriptor,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _open_absolute_directory,
    _open_directory_at,
    _open_regular_at,
    _require_cooperative_node,
    _require_private_parent,
    _stable_binding,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_NODE_FILE_LIMIT = 256 * 1024 * 1024
_NEXT_FILE_LIMIT = 32 * 1024 * 1024
_METADATA_FILE_LIMIT = 16 * 1024 * 1024
_FINGERPRINT_DOMAIN = b"murmur-relay-linux-workspace-prepared-v1\x00"


def _open_absolute_regular(
    path: Path,
    descriptors: _WorkspaceDescriptorSet,
    *,
    executable: bool,
) -> int:
    parent = _open_absolute_directory(path.parent, descriptors)
    try:
        child = _open_regular_at(parent, path.name, descriptors, executable=executable)
        _require_cooperative_node(child, directory=False, executable=executable)
        return child
    finally:
        if not descriptors.close(parent):
            raise _WorkspaceFilesystemError(_FAILURE)


def _open_relative_regular(
    root: int,
    components: tuple[str, ...],
    descriptors: _WorkspaceDescriptorSet,
    *,
    executable: bool,
) -> int:
    parent = root
    owned_parent = False
    try:
        for component in components[:-1]:
            child = _open_directory_at(parent, component, descriptors)
            _require_cooperative_node(child, directory=True)
            if owned_parent and not descriptors.close(parent):
                raise _WorkspaceFilesystemError(_FAILURE)
            parent = child
            owned_parent = True
        result = _open_regular_at(
            parent,
            components[-1],
            descriptors,
            executable=executable,
        )
        _require_cooperative_node(result, directory=False, executable=executable)
        return result
    finally:
        if owned_parent and not descriptors.close(parent):
            raise _WorkspaceFilesystemError(_FAILURE)


def _snapshot_tools(
    *,
    node_fd: int,
    next_fd: int,
    node_lock_fd: int,
    next_package_fd: int,
    node_modules_identity: _WorkspaceFilesystemIdentity,
    controller: _WorkspaceWorkerController,
) -> tuple[object, ...]:
    values: list[object] = [node_modules_identity]
    for descriptor, limit in zip(
        (node_fd, next_fd, node_lock_fd, next_package_fd),
        (
            _NODE_FILE_LIMIT,
            _NEXT_FILE_LIMIT,
            _METADATA_FILE_LIMIT,
            _METADATA_FILE_LIMIT,
        ),
        strict=True,
    ):
        identity = _WorkspaceFilesystemIdentity.from_stat(os.fstat(descriptor))
        if not identity.is_regular():
            raise _WorkspaceFilesystemError(_FAILURE)
        digest, size = _hash_descriptor(descriptor, limit, controller)
        if size != identity.size:
            raise _WorkspaceFilesystemError(_FAILURE)
        values.extend((identity, digest))
    return tuple(values)


def _revalidate_named_anchors(
    *,
    request: object,
    source_identity: _WorkspaceFilesystemIdentity,
    run_parent_identity: _WorkspaceFilesystemIdentity,
    tool_values: tuple[object, ...],
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> None:
    source_probe = run_parent_probe = node_probe = node_modules_probe = None
    next_probe = node_lock_probe = next_package_probe = None
    try:
        source_probe = _open_absolute_directory(request._source_root, descriptors)
        if _require_cooperative_node(source_probe, directory=True) != source_identity:
            raise _WorkspaceFilesystemError(_FAILURE)
        run_parent_probe = _open_absolute_directory(request._run_parent, descriptors)
        current_parent = _require_private_parent(run_parent_probe)
        if _stable_binding(current_parent) != _stable_binding(run_parent_identity):
            raise _WorkspaceFilesystemError(_FAILURE)
        node_probe = _open_absolute_regular(request._node, descriptors, executable=True)
        node_modules_probe = _open_directory_at(source_probe, "node_modules", descriptors)
        current_modules = _require_cooperative_node(node_modules_probe, directory=True)
        next_probe = _open_relative_regular(
            node_modules_probe,
            ("next", "dist", "bin", "next"),
            descriptors,
            executable=True,
        )
        node_lock_probe = _open_regular_at(
            node_modules_probe,
            ".package-lock.json",
            descriptors,
        )
        next_package_probe = _open_relative_regular(
            node_modules_probe,
            ("next", "package.json"),
            descriptors,
            executable=False,
        )
        if (
            _snapshot_tools(
                node_fd=node_probe,
                next_fd=next_probe,
                node_lock_fd=node_lock_probe,
                next_package_fd=next_package_probe,
                node_modules_identity=current_modules,
                controller=controller,
            )
            != tool_values
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
    finally:
        for descriptor in (
            next_package_probe,
            node_lock_probe,
            next_probe,
            node_modules_probe,
            node_probe,
            run_parent_probe,
            source_probe,
        ):
            if descriptor is not None and not descriptors.close(descriptor):
                raise _WorkspaceFilesystemError(_FAILURE)


def _fingerprint(manifest: bytes, tools: tuple[object, ...]) -> bytes:
    digest = hashlib.sha256(_FINGERPRINT_DOMAIN)
    digest.update(manifest)
    for value in tools:
        if type(value) is bytes:
            digest.update(value)
        elif type(value) is _WorkspaceFilesystemIdentity:
            for number in (
                value.device,
                value.inode,
                value.mode,
                value.links,
                value.size,
                value.modified_ns,
                value.changed_ns,
            ):
                digest.update(number.to_bytes(16, "big"))
        else:
            raise _WorkspaceFilesystemError(_FAILURE)
    return digest.digest()


__all__: list[str] = []

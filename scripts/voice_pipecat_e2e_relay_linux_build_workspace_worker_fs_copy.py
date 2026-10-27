"""Bounded source copying and two-pass manifest revalidation."""

from __future__ import annotations

import hashlib
import os
import stat

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspaceSourceNode,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _bounded_names,
    _create_directory_at,
    _create_regular_at,
    _open_directory_at,
    _open_regular_at,
    _require_cooperative_node,
    _require_named_identity,
    _WorkspaceCreationIntent,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_CHUNK_BYTES = 64 * 1024
_MANIFEST_DOMAIN = b"murmur-relay-linux-workspace-v1\x00"


def _copy_workspace_source(
    *,
    source_fd: int,
    workspace_fd: int,
    entries: tuple[str, ...],
    directory_entries: frozenset[str],
    max_nodes: int,
    max_bytes: int,
    max_depth: int,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> tuple[_WorkspaceSourceNode, ...]:
    _validate_policy(entries, directory_entries, max_nodes, max_bytes, max_depth)
    source_device = _WorkspaceFilesystemIdentity.from_stat(os.fstat(source_fd)).device
    destination_device = _WorkspaceFilesystemIdentity.from_stat(os.fstat(workspace_fd)).device
    counters = [0, 0]
    nodes: list[_WorkspaceSourceNode] = []
    for name in entries:
        _check_cancel(controller)
        _copy_node(
            source_fd=source_fd,
            destination_fd=workspace_fd,
            relative=(name,),
            expect_directory=name in directory_entries,
            depth=1,
            counters=counters,
            bounds=(max_nodes, max_bytes, max_depth),
            devices=(source_device, destination_device),
            nodes=nodes,
            descriptors=descriptors,
            controller=controller,
        )
    return tuple(nodes)


def _snapshot_workspace_source(
    *,
    source_fd: int,
    entries: tuple[str, ...],
    directory_entries: frozenset[str],
    max_nodes: int,
    max_bytes: int,
    max_depth: int,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> tuple[_WorkspaceSourceNode, ...]:
    _validate_policy(entries, directory_entries, max_nodes, max_bytes, max_depth)
    source_device = _WorkspaceFilesystemIdentity.from_stat(os.fstat(source_fd)).device
    counters = [0, 0]
    nodes: list[_WorkspaceSourceNode] = []
    for name in entries:
        _check_cancel(controller)
        _snapshot_source_node(
            parent_fd=source_fd,
            relative=(name,),
            expect_directory=name in directory_entries,
            depth=1,
            counters=counters,
            bounds=(max_nodes, max_bytes, max_depth),
            source_device=source_device,
            nodes=nodes,
            descriptors=descriptors,
            controller=controller,
        )
    return tuple(nodes)


def _snapshot_workspace_copy(
    *,
    workspace_fd: int,
    expected: tuple[_WorkspaceSourceNode, ...],
    node_modules_target: str,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> tuple[_WorkspaceSourceNode, ...]:
    workspace_device = _WorkspaceFilesystemIdentity.from_stat(os.fstat(workspace_fd)).device
    expected_names: dict[tuple[str, ...], set[str]] = {(): {"node_modules"}}
    by_path = {node.relative: node for node in expected}
    for node in expected:
        expected_names.setdefault(node.relative[:-1], set()).add(node.relative[-1])
        if node.kind == "directory":
            expected_names.setdefault(node.relative, set())
    observed: list[_WorkspaceSourceNode] = []

    def visit(parent_fd: int, relative: tuple[str, ...]) -> None:
        names = _bounded_names(parent_fd, len(expected_names.get(relative, ())) + 1)
        wanted = expected_names.get(relative, set())
        allowed = wanted if relative else wanted | {"node_modules"}
        if set(names) != allowed:
            raise _WorkspaceFilesystemError(_FAILURE)
        for name in names:
            _check_cancel(controller)
            path = (*relative, name)
            if path == ("node_modules",):
                details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISLNK(details.st_mode)
                    or details.st_nlink != 1
                    or details.st_dev != workspace_device
                    or os.readlink(name, dir_fd=parent_fd) != node_modules_target
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
                continue
            expected_node = by_path.get(path)
            if expected_node is None:
                raise _WorkspaceFilesystemError(_FAILURE)
            if expected_node.kind == "directory":
                child = _open_directory_at(parent_fd, name, descriptors)
                try:
                    identity = _require_named_identity(
                        parent_fd,
                        name,
                        child,
                        directory=True,
                    )
                    if identity.device != workspace_device:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    details = os.fstat(child)
                    if details.st_uid != os.geteuid() or stat.S_IMODE(identity.mode) != 0o700:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    observed.append(_WorkspaceSourceNode(path, "directory", identity, None))
                    visit(child, path)
                    if (
                        _require_named_identity(
                            parent_fd,
                            name,
                            child,
                            directory=True,
                        )
                        != identity
                    ):
                        raise _WorkspaceFilesystemError(_FAILURE)
                finally:
                    if not descriptors.close(child):
                        raise _WorkspaceFilesystemError(_FAILURE)
            else:
                child = _open_regular_at(parent_fd, name, descriptors)
                try:
                    identity = _require_named_identity(
                        parent_fd,
                        name,
                        child,
                        directory=False,
                    )
                    if identity.device != workspace_device:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    details = os.fstat(child)
                    if details.st_uid != os.geteuid() or stat.S_IMODE(identity.mode) != 0o600:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    digest, size = _hash_descriptor(
                        child,
                        expected_node.identity.size,
                        controller,
                    )
                    if (
                        _require_named_identity(
                            parent_fd,
                            name,
                            child,
                            directory=False,
                        )
                        != identity
                    ):
                        raise _WorkspaceFilesystemError(_FAILURE)
                    if size != identity.size:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    observed.append(_WorkspaceSourceNode(path, "file", identity, digest))
                finally:
                    if not descriptors.close(child):
                        raise _WorkspaceFilesystemError(_FAILURE)

    visit(workspace_fd, ())
    return tuple(observed)


def _source_signature(
    nodes: tuple[_WorkspaceSourceNode, ...],
) -> tuple[tuple[tuple[str, ...], str, int, bytes | None], ...]:
    return tuple(
        (node.relative, node.kind, node.identity.size if node.kind == "file" else 0, node.digest)
        for node in nodes
    )


def _manifest_digest(
    signature: tuple[tuple[tuple[str, ...], str, int, bytes | None], ...],
) -> bytes:
    digest = hashlib.sha256(_MANIFEST_DOMAIN)
    for relative, kind, size, content in signature:
        encoded = "/".join(relative).encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(b"d" if kind == "directory" else b"f")
        digest.update(size.to_bytes(8, "big"))
        digest.update(content or b"\x00" * 32)
    return digest.digest()


def _copy_node(
    *,
    source_fd: int,
    destination_fd: int,
    relative: tuple[str, ...],
    expect_directory: bool,
    depth: int,
    counters: list[int],
    bounds: tuple[int, int, int],
    devices: tuple[int, int],
    nodes: list[_WorkspaceSourceNode],
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> None:
    max_nodes, max_bytes, max_depth = bounds
    source_device, destination_device = devices
    if depth > max_depth or counters[0] >= max_nodes:
        raise _WorkspaceFilesystemError(_FAILURE)
    counters[0] += 1
    name = relative[-1]
    before = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=source_fd, follow_symlinks=False)
    )
    if before.device != source_device:
        raise _WorkspaceFilesystemError(_FAILURE)
    if expect_directory:
        if not before.is_directory():
            raise _WorkspaceFilesystemError(_FAILURE)
        source_child = _open_directory_at(source_fd, name, descriptors)
        destination_child = None
        try:
            source_identity = _require_cooperative_node(source_child, directory=True)
            if source_identity.device != source_device:
                raise _WorkspaceFilesystemError(_FAILURE)
            intent = _WorkspaceCreationIntent(
                parent=destination_fd,
                name=name,
                kind="directory",
            )
            destination_child, created = _create_directory_at(
                destination_fd,
                name,
                descriptors,
                intent,
            )
            if created.device != destination_device:
                raise _WorkspaceFilesystemError(_FAILURE)
            nodes.append(_WorkspaceSourceNode(relative, "directory", source_identity, None))
            names = _bounded_names(source_child, max_nodes - counters[0] + 1)
            for child_name in names:
                child_details = _WorkspaceFilesystemIdentity.from_stat(
                    os.stat(child_name, dir_fd=source_child, follow_symlinks=False)
                )
                _copy_node(
                    source_fd=source_child,
                    destination_fd=destination_child,
                    relative=(*relative, child_name),
                    expect_directory=child_details.is_directory(),
                    depth=depth + 1,
                    counters=counters,
                    bounds=bounds,
                    devices=devices,
                    nodes=nodes,
                    descriptors=descriptors,
                    controller=controller,
                )
            if _require_named_identity(source_fd, name, source_child, directory=True) != before:
                raise _WorkspaceFilesystemError(_FAILURE)
            os.fsync(destination_child)
        finally:
            if destination_child is not None and not descriptors.close(destination_child):
                raise _WorkspaceFilesystemError(_FAILURE)
            if not descriptors.close(source_child):
                raise _WorkspaceFilesystemError(_FAILURE)
        return
    if not before.is_regular():
        raise _WorkspaceFilesystemError(_FAILURE)
    source_child = _open_regular_at(source_fd, name, descriptors)
    destination_child = None
    try:
        source_identity = _require_cooperative_node(source_child, directory=False)
        if source_identity.device != source_device:
            raise _WorkspaceFilesystemError(_FAILURE)
        intent = _WorkspaceCreationIntent(parent=destination_fd, name=name, kind="file")
        destination_child = _create_regular_at(destination_fd, name, descriptors, intent)
        digest, size = _copy_descriptor(
            source_child,
            destination_child,
            max_bytes - counters[1],
            controller,
        )
        counters[1] += size
        if counters[1] > max_bytes:
            raise _WorkspaceFilesystemError(_FAILURE)
        if _require_named_identity(source_fd, name, source_child, directory=False) != before:
            raise _WorkspaceFilesystemError(_FAILURE)
        destination_identity = _require_named_identity(
            destination_fd,
            name,
            destination_child,
            directory=False,
        )
        if (
            destination_identity.device != destination_device
            or stat.S_IMODE(destination_identity.mode) != 0o600
            or destination_identity.size != size
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        os.fsync(destination_child)
        nodes.append(_WorkspaceSourceNode(relative, "file", source_identity, digest))
    finally:
        if destination_child is not None and not descriptors.close(destination_child):
            raise _WorkspaceFilesystemError(_FAILURE)
        if not descriptors.close(source_child):
            raise _WorkspaceFilesystemError(_FAILURE)


def _snapshot_source_node(
    *,
    parent_fd: int,
    relative: tuple[str, ...],
    expect_directory: bool,
    depth: int,
    counters: list[int],
    bounds: tuple[int, int, int],
    source_device: int,
    nodes: list[_WorkspaceSourceNode],
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> None:
    max_nodes, max_bytes, max_depth = bounds
    if depth > max_depth or counters[0] >= max_nodes:
        raise _WorkspaceFilesystemError(_FAILURE)
    counters[0] += 1
    name = relative[-1]
    if expect_directory:
        child = _open_directory_at(parent_fd, name, descriptors)
        try:
            identity = _require_cooperative_node(child, directory=True)
            if identity.device != source_device:
                raise _WorkspaceFilesystemError(_FAILURE)
            nodes.append(_WorkspaceSourceNode(relative, "directory", identity, None))
            names = _bounded_names(child, max_nodes - counters[0] + 1)
            for child_name in names:
                details = _WorkspaceFilesystemIdentity.from_stat(
                    os.stat(child_name, dir_fd=child, follow_symlinks=False)
                )
                _snapshot_source_node(
                    parent_fd=child,
                    relative=(*relative, child_name),
                    expect_directory=details.is_directory(),
                    depth=depth + 1,
                    counters=counters,
                    bounds=bounds,
                    source_device=source_device,
                    nodes=nodes,
                    descriptors=descriptors,
                    controller=controller,
                )
            if _require_named_identity(parent_fd, name, child, directory=True) != identity:
                raise _WorkspaceFilesystemError(_FAILURE)
        finally:
            if not descriptors.close(child):
                raise _WorkspaceFilesystemError(_FAILURE)
        return
    child = _open_regular_at(parent_fd, name, descriptors)
    try:
        identity = _require_cooperative_node(child, directory=False)
        if identity.device != source_device:
            raise _WorkspaceFilesystemError(_FAILURE)
        digest, size = _hash_descriptor(child, max_bytes - counters[1], controller)
        if _require_named_identity(parent_fd, name, child, directory=False) != identity:
            raise _WorkspaceFilesystemError(_FAILURE)
        counters[1] += size
        if counters[1] > max_bytes:
            raise _WorkspaceFilesystemError(_FAILURE)
        nodes.append(_WorkspaceSourceNode(relative, "file", identity, digest))
    finally:
        if not descriptors.close(child):
            raise _WorkspaceFilesystemError(_FAILURE)


def _copy_descriptor(
    source: int,
    destination: int,
    remaining: int,
    controller: _WorkspaceWorkerController,
) -> tuple[bytes, int]:
    if remaining < 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    os.lseek(source, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        _check_cancel(controller)
        chunk = os.read(source, min(_CHUNK_BYTES, remaining - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > remaining:
            raise _WorkspaceFilesystemError(_FAILURE)
        digest.update(chunk)
        offset = 0
        while offset < len(chunk):
            _check_cancel(controller)
            written = os.write(destination, chunk[offset:])
            if type(written) is not int or written <= 0:
                raise _WorkspaceFilesystemError(_FAILURE)
            offset += written
    return digest.digest(), total


def _hash_descriptor(
    descriptor: int,
    remaining: int,
    controller: _WorkspaceWorkerController,
) -> tuple[bytes, int]:
    if type(remaining) is not int or remaining < 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        _check_cancel(controller)
        chunk = os.read(descriptor, min(_CHUNK_BYTES, remaining - total + 1))
        if not chunk:
            return digest.digest(), total
        total += len(chunk)
        if total > remaining:
            raise _WorkspaceFilesystemError(_FAILURE)
        digest.update(chunk)


def _validate_policy(
    entries: tuple[str, ...],
    directory_entries: frozenset[str],
    max_nodes: int,
    max_bytes: int,
    max_depth: int,
) -> None:
    if (
        type(entries) is not tuple
        or entries != tuple(sorted(entries))
        or len(entries) != len(set(entries))
        or type(directory_entries) is not frozenset
        or not directory_entries.issubset(entries)
        or any(type(name) is not str or not name or "/" in name for name in entries)
        or type(max_nodes) is not int
        or not 1 <= max_nodes <= 4096
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= 64 * 1024 * 1024
        or type(max_depth) is not int
        or not 1 <= max_depth <= 32
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _check_cancel(controller: _WorkspaceWorkerController) -> None:
    if type(controller) is not _WorkspaceWorkerController:
        raise _WorkspaceFilesystemError(_FAILURE)
    if controller._cancellation_requested() is True:
        raise _WorkspaceFilesystemError(_FAILURE)


__all__: list[str] = []

"""Descriptor-relative bounded validation of one Next build workspace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspaceSourceNode,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _bounded_names,
    _open_directory_at,
    _require_named_identity,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_contract import (
    _DOCUMENT_NAMES,
    _JSON_BYTES,
    _MANDATORY_DIST_FILES,
    _validate_workspace_build_output_documents,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_open import (
    _check_output_cancel,
    _hash_output_regular,
    _read_output_regular,
    _record_output,
    _require_output_directory,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspaceBuildOutputSnapshot,
    _WorkspacePreparedDestinationBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_workspace import (
    _snapshot_workspace_after_build as _snapshot_prepared_workspace_after_build,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_DIST_PARENT = ".next-voice-e2e"
_MAX_DIST_NODES = 4096
_MAX_DIST_BYTES = 512 * 1024 * 1024
_MAX_DIST_DEPTH = 32
_MAX_DIST_FILE_BYTES = 256 * 1024 * 1024
_OUTPUT_DOMAIN = b"murmur-relay-linux-next-output-v1\x00"
_PATH_TYPE = type(Path("/"))


def _validate_workspace_build_output(
    *,
    workspace_fd: int,
    workspace: Path,
    baseline: _WorkspacePreparedDestinationBaseline,
    run_id: str,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> _WorkspaceBuildOutputSnapshot:
    if (
        type(workspace) is not _PATH_TYPE
        or not workspace.is_absolute()
        or type(baseline) is not _WorkspacePreparedDestinationBaseline
        or type(run_id) is not str
        or type(descriptors) is not _WorkspaceDescriptorSet
        or type(controller) is not _WorkspaceWorkerController
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    workspace_identity = _require_output_directory(
        workspace_fd,
        expected_device=None,
        private=True,
    )
    digest = hashlib.sha256(_OUTPUT_DOMAIN)
    workspace_nodes, node_modules_identity = _snapshot_prepared_workspace_after_build(
        workspace_fd=workspace_fd,
        baseline=baseline,
        run_id=run_id,
        descriptors=descriptors,
        controller=controller,
        digest=digest,
    )
    dist_parent = dist_root = None
    try:
        dist_parent = _open_directory_at(workspace_fd, _DIST_PARENT, descriptors)
        parent_identity = _require_named_identity(
            workspace_fd,
            _DIST_PARENT,
            dist_parent,
            directory=True,
        )
        _require_output_directory(
            dist_parent,
            expected_device=workspace_identity.device,
            private=False,
        )
        if _bounded_names(dist_parent, 2) != (run_id,):
            raise _WorkspaceFilesystemError(_FAILURE)
        dist_root = _open_directory_at(dist_parent, run_id, descriptors)
        root_identity = _require_named_identity(
            dist_parent,
            run_id,
            dist_root,
            directory=True,
        )
        _require_output_directory(
            dist_root,
            expected_device=workspace_identity.device,
            private=False,
        )
        documents: dict[str, bytes] = {}
        nonempty_paths: set[str] = set()
        regular_paths: set[str] = set()
        directory_paths: set[str] = set()
        dist_nodes: list[_WorkspaceSourceNode] = []
        counters = [0, 0]
        _snapshot_dist_tree(
            parent_fd=dist_root,
            relative=(),
            depth=0,
            expected_device=workspace_identity.device,
            counters=counters,
            regular_paths=regular_paths,
            nonempty_paths=nonempty_paths,
            directory_paths=directory_paths,
            documents=documents,
            nodes=dist_nodes,
            descriptors=descriptors,
            controller=controller,
            digest=digest,
        )
        if (
            _require_named_identity(
                dist_parent,
                run_id,
                dist_root,
                directory=True,
            )
            != root_identity
            or _require_named_identity(
                workspace_fd,
                _DIST_PARENT,
                dist_parent,
                directory=True,
            )
            != parent_identity
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        _validate_workspace_build_output_documents(
            documents,
            directory_paths=frozenset(directory_paths),
            nonempty_paths=frozenset(nonempty_paths),
            regular_paths=frozenset(regular_paths),
            run_id=run_id,
            workspace=str(workspace),
        )
        return _WorkspaceBuildOutputSnapshot(
            digest=digest.digest(),
            dist_parent_identity=parent_identity,
            dist_root_identity=root_identity,
            dist_nodes=tuple(dist_nodes),
            node_modules_identity=node_modules_identity,
            workspace_nodes=workspace_nodes,
        )
    finally:
        for descriptor in (dist_root, dist_parent):
            if descriptor is not None and not descriptors.close(descriptor):
                raise _WorkspaceFilesystemError(_FAILURE)


def _snapshot_dist_tree(
    *,
    parent_fd: int,
    relative: tuple[str, ...],
    depth: int,
    expected_device: int,
    counters: list[int],
    regular_paths: set[str],
    nonempty_paths: set[str],
    directory_paths: set[str],
    documents: dict[str, bytes],
    nodes: list[_WorkspaceSourceNode],
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
    digest: object,
) -> None:
    if depth > _MAX_DIST_DEPTH:
        raise _WorkspaceFilesystemError(_FAILURE)
    names = _bounded_names(parent_fd, _MAX_DIST_NODES - counters[0] + 1)
    for name in names:
        _check_output_cancel(controller)
        if counters[0] >= _MAX_DIST_NODES:
            raise _WorkspaceFilesystemError(_FAILURE)
        counters[0] += 1
        path = (*relative, name)
        if len(path) > _MAX_DIST_DEPTH:
            raise _WorkspaceFilesystemError(_FAILURE)
        path_text = "/".join(path)
        before = _WorkspaceFilesystemIdentity.from_stat(
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        )
        if before.device != expected_device:
            raise _WorkspaceFilesystemError(_FAILURE)
        if before.is_directory():
            child = _open_directory_at(parent_fd, name, descriptors)
            try:
                identity = _require_named_identity(
                    parent_fd,
                    name,
                    child,
                    directory=True,
                )
                _require_output_directory(
                    child,
                    expected_device=expected_device,
                    private=False,
                )
                directory_paths.add(path_text)
                _record_output(digest, path, "directory", 0, None)
                _snapshot_dist_tree(
                    parent_fd=child,
                    relative=path,
                    depth=depth + 1,
                    expected_device=expected_device,
                    counters=counters,
                    regular_paths=regular_paths,
                    nonempty_paths=nonempty_paths,
                    directory_paths=directory_paths,
                    documents=documents,
                    nodes=nodes,
                    descriptors=descriptors,
                    controller=controller,
                    digest=digest,
                )
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
                nodes.append(_WorkspaceSourceNode(path, "directory", identity, None))
            finally:
                if not descriptors.close(child):
                    raise _WorkspaceFilesystemError(_FAILURE)
            continue
        if not before.is_regular():
            raise _WorkspaceFilesystemError(_FAILURE)
        remaining = _MAX_DIST_BYTES - counters[1]
        limit = min(_MAX_DIST_FILE_BYTES, remaining)
        capture = path_text in _DOCUMENT_NAMES
        if capture:
            limit = min(limit, _JSON_BYTES)
        if capture:
            content, identity = _read_output_regular(
                parent_fd,
                name,
                limit=limit,
                expected_device=expected_device,
                private=False,
                descriptors=descriptors,
                controller=controller,
            )
            content_digest = hashlib.sha256(content).digest()
        else:
            content_digest, identity = _hash_output_regular(
                parent_fd,
                name,
                limit=limit,
                expected_device=expected_device,
                private=False,
                descriptors=descriptors,
                controller=controller,
            )
            content = None
        counters[1] += identity.size
        if counters[1] > _MAX_DIST_BYTES:
            raise _WorkspaceFilesystemError(_FAILURE)
        regular_paths.add(path_text)
        if identity.size > 0:
            nonempty_paths.add(path_text)
        if capture:
            if content is None:
                raise _WorkspaceFilesystemError(_FAILURE)
            documents[path_text] = content
        if path_text in _MANDATORY_DIST_FILES and identity.size == 0:
            raise _WorkspaceFilesystemError(_FAILURE)
        nodes.append(_WorkspaceSourceNode(path, "file", identity, content_digest))
        _record_output(digest, path, "file", identity.size, content_digest)


__all__: list[str] = []

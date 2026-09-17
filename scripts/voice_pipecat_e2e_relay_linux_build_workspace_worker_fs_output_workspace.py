"""Exact prepared-copy and post-build workspace tree snapshots."""

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
    _open_directory_at,
    _require_named_identity,
    _stable_binding,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_contract import (
    _NEXT_ENV_BYTES,
    _TSCONFIG_BYTES,
    _new_workspace_build_input_baseline,
    _validate_workspace_build_inputs,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_open import (
    _check_output_cancel,
    _hash_output_regular,
    _read_output_regular,
    _record_output,
    _require_output_directory,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _BASELINE_TOKEN,
    _WorkspacePreparedDestinationBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_DIST_PARENT = ".next-voice-e2e"
_MUTABLE_FILES = frozenset({("next-env.d.ts",), ("tsconfig.json",)})


def _snapshot_workspace_build_inputs(
    *,
    workspace_fd: int,
    owner_token: object,
    record_token: object,
    run_id: str,
    expected_destination: tuple[_WorkspaceSourceNode, ...],
    expected_node_modules: _WorkspaceFilesystemIdentity,
    node_modules_target: str,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> _WorkspacePreparedDestinationBaseline:
    """Prove the full prepared destination and reserved-parent absence."""

    if (
        type(owner_token) is not object
        or type(record_token) is not object
        or type(run_id) is not str
        or not run_id
        or "/" in run_id
        or "\x00" in run_id
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    workspace = _require_output_directory(
        workspace_fd,
        expected_device=None,
        private=True,
    )
    captures, observed, node_modules = _snapshot_workspace_tree(
        workspace_fd=workspace_fd,
        workspace_device=workspace.device,
        expected=expected_destination,
        expected_node_modules=expected_node_modules,
        node_modules_target=node_modules_target,
        allow_dist=False,
        descriptors=descriptors,
        controller=controller,
        digest=None,
    )
    if observed != expected_destination or node_modules != expected_node_modules:
        raise _WorkspaceFilesystemError(_FAILURE)
    inputs = _new_workspace_build_input_baseline(
        captures["next-env.d.ts"],
        captures["tsconfig.json"],
    )
    return _WorkspacePreparedDestinationBaseline(
        _authentic=_BASELINE_TOKEN,
        inputs=inputs,
        node_modules_identity=node_modules,
        node_modules_target=node_modules_target,
        nodes=observed,
        owner_token=owner_token,
        record_token=record_token,
        run_id=run_id,
        workspace_binding=_stable_binding(workspace),
    )


def _snapshot_workspace_after_build(
    *,
    workspace_fd: int,
    baseline: _WorkspacePreparedDestinationBaseline,
    run_id: str,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
    digest: object,
) -> tuple[tuple[_WorkspaceSourceNode, ...], _WorkspaceFilesystemIdentity]:
    """Prove all non-output names and return exact post-build identities."""

    if type(baseline) is not _WorkspacePreparedDestinationBaseline:
        raise _WorkspaceFilesystemError(_FAILURE)
    workspace = _require_output_directory(
        workspace_fd,
        expected_device=None,
        private=True,
    )
    if _stable_binding(workspace) != baseline.workspace_binding:
        raise _WorkspaceFilesystemError(_FAILURE)
    captures, observed, node_modules = _snapshot_workspace_tree(
        workspace_fd=workspace_fd,
        workspace_device=workspace.device,
        expected=baseline.nodes,
        expected_node_modules=baseline.node_modules_identity,
        node_modules_target=baseline.node_modules_target,
        allow_dist=True,
        descriptors=descriptors,
        controller=controller,
        digest=digest,
    )
    _validate_workspace_build_inputs(
        baseline.inputs,
        next_env=captures["next-env.d.ts"],
        tsconfig=captures["tsconfig.json"],
        run_id=run_id,
    )
    return observed, node_modules


def _snapshot_workspace_tree(
    *,
    workspace_fd: int,
    workspace_device: int,
    expected: tuple[_WorkspaceSourceNode, ...],
    expected_node_modules: _WorkspaceFilesystemIdentity,
    node_modules_target: str,
    allow_dist: bool,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
    digest: object | None,
) -> tuple[dict[str, bytes], tuple[_WorkspaceSourceNode, ...], _WorkspaceFilesystemIdentity]:
    if (
        type(expected) is not tuple
        or any(type(node) is not _WorkspaceSourceNode for node in expected)
        or type(expected_node_modules) is not _WorkspaceFilesystemIdentity
        or type(node_modules_target) is not str
        or not node_modules_target.startswith("/")
        or type(allow_dist) is not bool
        or type(descriptors) is not _WorkspaceDescriptorSet
        or type(controller) is not _WorkspaceWorkerController
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    expected_names: dict[tuple[str, ...], set[str]] = {(): {"node_modules"}}
    by_path = {node.relative: node for node in expected}
    if len(by_path) != len(expected):
        raise _WorkspaceFilesystemError(_FAILURE)
    for node in expected:
        if not node.relative or node.kind not in {"directory", "file"}:
            raise _WorkspaceFilesystemError(_FAILURE)
        expected_names.setdefault(node.relative[:-1], set()).add(node.relative[-1])
        if node.kind == "directory":
            expected_names.setdefault(node.relative, set())
    captures: dict[str, bytes] = {}
    observed: dict[tuple[str, ...], _WorkspaceSourceNode] = {}
    observed_node_modules = None

    def visit(parent_fd: int, relative: tuple[str, ...]) -> None:
        nonlocal observed_node_modules
        allowed = set(expected_names.get(relative, ()))
        if not relative and allow_dist:
            allowed.add(_DIST_PARENT)
        names = _bounded_names(parent_fd, len(allowed) + 1)
        if set(names) != allowed:
            raise _WorkspaceFilesystemError(_FAILURE)
        for name in names:
            _check_output_cancel(controller)
            path = (*relative, name)
            if path == ("node_modules",):
                identity = _WorkspaceFilesystemIdentity.from_stat(
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                )
                if (
                    identity != expected_node_modules
                    or not stat.S_ISLNK(identity.mode)
                    or identity.links != 1
                    or identity.device != workspace_device
                    or os.readlink(name, dir_fd=parent_fd) != node_modules_target
                    or _WorkspaceFilesystemIdentity.from_stat(
                        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    )
                    != identity
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
                observed_node_modules = identity
                continue
            if path == (_DIST_PARENT,) and allow_dist:
                identity = _WorkspaceFilesystemIdentity.from_stat(
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                )
                if not identity.is_directory() or identity.device != workspace_device:
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
                    _require_output_directory(
                        child,
                        expected_device=workspace_device,
                        private=True,
                    )
                    if identity != expected_node.identity:
                        raise _WorkspaceFilesystemError(_FAILURE)
                    if digest is not None:
                        _record_output(digest, path, "directory", 0, None)
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
                    observed[path] = _WorkspaceSourceNode(path, "directory", identity, None)
                finally:
                    if not descriptors.close(child):
                        raise _WorkspaceFilesystemError(_FAILURE)
                continue
            limit = _workspace_file_limit(path, expected_node)
            if path in _MUTABLE_FILES:
                content, identity = _read_output_regular(
                    parent_fd,
                    name,
                    limit=limit,
                    expected_device=workspace_device,
                    private=True,
                    descriptors=descriptors,
                    controller=controller,
                )
                content_digest = hashlib.sha256(content).digest()
                captures[name] = content
                if not allow_dist and (
                    identity != expected_node.identity or content_digest != expected_node.digest
                ):
                    raise _WorkspaceFilesystemError(_FAILURE)
            else:
                content_digest, identity = _hash_output_regular(
                    parent_fd,
                    name,
                    limit=limit,
                    expected_device=workspace_device,
                    private=True,
                    descriptors=descriptors,
                    controller=controller,
                )
                if identity != expected_node.identity or content_digest != expected_node.digest:
                    raise _WorkspaceFilesystemError(_FAILURE)
            if digest is not None:
                _record_output(digest, path, "file", identity.size, content_digest)
            observed[path] = _WorkspaceSourceNode(path, "file", identity, content_digest)

    visit(workspace_fd, ())
    if captures.keys() != {"next-env.d.ts", "tsconfig.json"}:
        raise _WorkspaceFilesystemError(_FAILURE)
    ordered = tuple(observed[node.relative] for node in expected)
    if observed_node_modules is None:
        raise _WorkspaceFilesystemError(_FAILURE)
    return captures, ordered, observed_node_modules


def _workspace_file_limit(
    path: tuple[str, ...],
    expected: _WorkspaceSourceNode,
) -> int:
    if path == ("next-env.d.ts",):
        return _NEXT_ENV_BYTES
    if path == ("tsconfig.json",):
        return _TSCONFIG_BYTES
    return expected.identity.size


__all__: list[str] = []

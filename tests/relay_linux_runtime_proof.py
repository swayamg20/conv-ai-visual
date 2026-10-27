"""Synthetic exact runtime proof shared by lower-level relay build tests."""

from __future__ import annotations

import stat
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _WorkspaceFilesystemIdentity,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_contract import (
    _WorkspaceBuildInputBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _BASELINE_TOKEN,
    _new_workspace_built_runtime_proof,
    _WorkspaceBuildOutputSnapshot,
    _WorkspaceBuiltRuntimeProof,
    _WorkspacePreparedDestinationBaseline,
)


def synthetic_runtime_proof(
    owner_token: object,
    record_token: object,
    *,
    digest: bytes,
) -> _WorkspaceBuiltRuntimeProof:
    """Build a factory-authentic path-free proof for receipt-only unit tests."""

    directory = _WorkspaceFilesystemIdentity(1, 1, stat.S_IFDIR | 0o700, 1, 0, 1, 1)
    symlink = _WorkspaceFilesystemIdentity(1, 2, stat.S_IFLNK | 0o777, 1, 12, 1, 1)
    baseline = _WorkspacePreparedDestinationBaseline(
        _authentic=_BASELINE_TOKEN,
        inputs=_WorkspaceBuildInputBaseline((), ()),
        node_modules_identity=symlink,
        node_modules_target="/synthetic/node_modules",
        nodes=(),
        owner_token=owner_token,
        record_token=record_token,
        run_id="synthetic-proof",
        workspace_binding=(1, 3, stat.S_IFDIR),
    )
    output = _WorkspaceBuildOutputSnapshot(
        digest=digest,
        dist_parent_identity=directory,
        dist_root_identity=directory,
        dist_nodes=(),
        node_modules_identity=symlink,
        workspace_nodes=(),
    )
    return synthetic_runtime_proof_from_snapshots(
        owner_token,
        record_token,
        baseline=baseline,
        output=output,
    )


def synthetic_runtime_proof_from_snapshots(
    owner_token: object,
    record_token: object,
    *,
    baseline: _WorkspacePreparedDestinationBaseline,
    output: _WorkspaceBuildOutputSnapshot,
) -> _WorkspaceBuiltRuntimeProof:
    return _new_workspace_built_runtime_proof(
        baseline=baseline,
        output=output,
        tool_values=_synthetic_tool_values(),
        owner_token=owner_token,
        record_token=record_token,
    )


def _synthetic_tool_values() -> tuple[object, ...]:
    directory = _WorkspaceFilesystemIdentity(1, 1, stat.S_IFDIR | 0o700, 1, 0, 1, 1)
    tools: list[object] = [directory]
    for inode in range(4, 10):
        tools.extend(
            (
                _WorkspaceFilesystemIdentity(
                    1,
                    inode,
                    stat.S_IFREG | 0o600,
                    1,
                    1,
                    1,
                    1,
                ),
                bytes([inode]) * 32,
            )
        )
    return tuple(tools)


_PROOFS: weakref.WeakKeyDictionary[object, tuple[object, object, bytes, object]] = (
    weakref.WeakKeyDictionary()
)


def synthetic_runtime_proof_for(
    command: object,
    owner_token: object,
    record_token: object,
    *,
    digest: bytes,
) -> _WorkspaceBuiltRuntimeProof:
    """Return the same proof identity for one command's replayed receipt call."""

    retained = _PROOFS.get(command)
    if (
        type(retained) is tuple
        and retained[0] is owner_token
        and retained[1] is record_token
        and retained[2] == digest
        and type(retained[3]) is _WorkspaceBuiltRuntimeProof
    ):
        return retained[3]
    proof = synthetic_runtime_proof(owner_token, record_token, digest=digest)
    _PROOFS[command] = (owner_token, record_token, digest, proof)
    return proof


__all__ = [
    "synthetic_runtime_proof",
    "synthetic_runtime_proof_for",
    "synthetic_runtime_proof_from_snapshots",
]

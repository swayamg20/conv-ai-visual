"""Worker-stack-only evidence for prepared and built workspace trees."""

from __future__ import annotations

from dataclasses import dataclass

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _WorkspaceFilesystemIdentity,
    _WorkspaceSourceNode,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_contract import (
    _WorkspaceBuildInputBaseline,
)

_BASELINE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _WorkspacePreparedDestinationBaseline:
    """Exact copied-tree authority retained only on the workspace-worker stack."""

    _authentic: object
    inputs: _WorkspaceBuildInputBaseline
    node_modules_identity: _WorkspaceFilesystemIdentity
    node_modules_target: str
    nodes: tuple[_WorkspaceSourceNode, ...]
    owner_token: object
    record_token: object
    run_id: str
    workspace_binding: tuple[int, int, int]

    def _matches(
        self,
        *,
        owner_token: object,
        record_token: object,
        run_id: str,
        workspace_binding: tuple[int, int, int],
    ) -> bool:
        return bool(
            self._authentic is _BASELINE_TOKEN
            and self.owner_token is owner_token
            and self.record_token is record_token
            and type(self.run_id) is str
            and self.run_id == run_id
            and type(self.workspace_binding) is tuple
            and self.workspace_binding == workspace_binding
        )


@dataclass(frozen=True, slots=True)
class _WorkspaceBuildOutputSnapshot:
    """Exact two-pass evidence; only its digest may enter a built receipt."""

    digest: bytes
    dist_parent_identity: _WorkspaceFilesystemIdentity
    dist_root_identity: _WorkspaceFilesystemIdentity
    dist_nodes: tuple[_WorkspaceSourceNode, ...]
    node_modules_identity: _WorkspaceFilesystemIdentity
    workspace_nodes: tuple[_WorkspaceSourceNode, ...]


__all__: list[str] = []

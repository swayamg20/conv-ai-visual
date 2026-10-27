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
_RUNTIME_PROOF_TOKEN = object()


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
    """Exact two-pass evidence retained only inside the private runtime proof."""

    digest: bytes
    dist_parent_identity: _WorkspaceFilesystemIdentity
    dist_root_identity: _WorkspaceFilesystemIdentity
    dist_nodes: tuple[_WorkspaceSourceNode, ...]
    node_modules_identity: _WorkspaceFilesystemIdentity
    workspace_nodes: tuple[_WorkspaceSourceNode, ...]


class _WorkspaceBuiltRuntimeProof:
    """Exact worker-observed inputs and output retained with one built lease."""

    __slots__ = ("_authentic", "baseline", "output", "tool_values")

    def __init__(
        self,
        token: object,
        *,
        baseline: _WorkspacePreparedDestinationBaseline,
        output: _WorkspaceBuildOutputSnapshot,
        tool_values: tuple[object, ...],
    ) -> None:
        if token is not _RUNTIME_PROOF_TOKEN:
            raise TypeError("Relay Linux workspace runtime proof is factory-owned")
        object.__setattr__(self, "_authentic", token)
        object.__setattr__(self, "baseline", baseline)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "tool_values", tool_values)

    def _matches(
        self,
        *,
        owner_token: object,
        record_token: object,
        output_digest: bytes,
    ) -> bool:
        try:
            return self._matches_unchecked(
                owner_token=owner_token,
                record_token=record_token,
                output_digest=output_digest,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False

    def _canonical_digest(self) -> bytes | None:
        try:
            output = object.__getattribute__(self, "output")
            digest = object.__getattribute__(output, "digest")
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return None
        return (
            digest
            if type(output) is _WorkspaceBuildOutputSnapshot and _digest_shape(digest)
            else None
        )

    def _matches_canonical(self, *, owner_token: object, record_token: object) -> bool:
        digest = self._canonical_digest()
        return bool(
            digest is not None
            and self._matches(
                owner_token=owner_token,
                record_token=record_token,
                output_digest=digest,
            )
        )

    def _matches_unchecked(
        self,
        *,
        owner_token: object,
        record_token: object,
        output_digest: bytes,
    ) -> bool:
        baseline = self.baseline
        output = self.output
        tools = self.tool_values
        return bool(
            self._authentic is _RUNTIME_PROOF_TOKEN
            and type(baseline) is _WorkspacePreparedDestinationBaseline
            and baseline._matches(
                owner_token=owner_token,
                record_token=record_token,
                run_id=baseline.run_id,
                workspace_binding=baseline.workspace_binding,
            )
            and type(baseline.inputs) is _WorkspaceBuildInputBaseline
            and type(baseline.node_modules_identity) is _WorkspaceFilesystemIdentity
            and type(baseline.node_modules_target) is str
            and baseline.node_modules_target.startswith("/")
            and type(baseline.nodes) is tuple
            and all(type(node) is _WorkspaceSourceNode for node in baseline.nodes)
            and type(baseline.run_id) is str
            and bool(baseline.run_id)
            and type(baseline.workspace_binding) is tuple
            and len(baseline.workspace_binding) == 3
            and all(type(value) is int for value in baseline.workspace_binding)
            and type(output) is _WorkspaceBuildOutputSnapshot
            and type(output_digest) is bytes
            and len(output_digest) == 32
            and output.digest == output_digest
            and type(output.dist_parent_identity) is _WorkspaceFilesystemIdentity
            and type(output.dist_root_identity) is _WorkspaceFilesystemIdentity
            and type(output.dist_nodes) is tuple
            and all(type(node) is _WorkspaceSourceNode for node in output.dist_nodes)
            and type(output.node_modules_identity) is _WorkspaceFilesystemIdentity
            and output.node_modules_identity == baseline.node_modules_identity
            and type(output.workspace_nodes) is tuple
            and all(type(node) is _WorkspaceSourceNode for node in output.workspace_nodes)
            and _tool_values_match(tools)
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceBuiltRuntimeProof()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace runtime proof is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace runtime proof cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace runtime proof cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace runtime proof cannot be serialized")


def _new_workspace_built_runtime_proof(
    *,
    baseline: _WorkspacePreparedDestinationBaseline,
    output: _WorkspaceBuildOutputSnapshot,
    tool_values: tuple[object, ...],
    owner_token: object,
    record_token: object,
) -> _WorkspaceBuiltRuntimeProof:
    proof = _WorkspaceBuiltRuntimeProof(
        _RUNTIME_PROOF_TOKEN,
        baseline=baseline,
        output=output,
        tool_values=tool_values,
    )
    digest = proof._canonical_digest()
    if digest is None or not proof._matches(
        owner_token=owner_token,
        record_token=record_token,
        output_digest=digest,
    ):
        raise TypeError("Relay Linux workspace runtime proof is invalid")
    return proof


def _tool_values_match(values: object) -> bool:
    """Validate Node, Next, Playwright, and installed-package proof shape."""

    return bool(
        type(values) is tuple
        and len(values) == 13
        and type(values[0]) is _WorkspaceFilesystemIdentity
        and values[0].is_directory()
        and all(
            type(values[index]) is _WorkspaceFilesystemIdentity
            and values[index].is_regular()
            and type(values[index + 1]) is bytes
            and len(values[index + 1]) == 32
            for index in range(1, 13, 2)
        )
    )


def _digest_shape(value: object) -> bool:
    return type(value) is bytes and len(value) == 32


__all__: list[str] = []

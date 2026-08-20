"""Synthetic tests for dormant relay Linux workspace preparation values."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import sys
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module

RUN_ID = "workspace-test"


def _destination(tmp_path: Path):
    source = (tmp_path / "missing-source-web").resolve()
    parent = (tmp_path / "missing-run-parent").resolve()
    node = (tmp_path / "missing-toolchain/node").resolve()
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=source,
        run_parent=parent,
        node=node,
        run_id=RUN_ID,
    )
    return source, parent, node, destination


def test_factory_preowns_exact_inert_graph_without_filesystem_effect(tmp_path: Path) -> None:
    source, parent, node, destination = _destination(tmp_path)
    request = destination._request
    owner = destination._read(request)

    assert not source.exists() and not parent.exists() and not node.exists()
    assert not destination and not request and not owner
    assert not owner._cleanup_authority and not owner._receipt_destination
    assert destination._owner is owner
    assert owner._request is request
    assert owner._cleanup_authority._matches(request)
    assert owner._receipt_destination._read(request) is None
    assert request._source_root == source
    assert request._run_parent == parent
    assert request._run_root == parent / f"relay-linux-{RUN_ID}"
    assert request._workspace == request._run_root / "web-workspace"
    assert request._node == node
    assert request._node_modules == source / "node_modules"
    assert request._next_cli == source / "node_modules/next/dist/bin/next"
    assert request._dist_path == request._workspace / ".next-voice-e2e" / RUN_ID


def test_request_carries_exact_positive_copy_policy_and_bounds(tmp_path: Path) -> None:
    _source, _parent, _node, destination = _destination(tmp_path)
    entries, directories, max_nodes, max_bytes, max_depth = destination._request._copy_policy()

    assert entries == (
        ".env.example",
        "e2e",
        "eslint.config.mjs",
        "next-env.d.ts",
        "next.config.mjs",
        "package-lock.json",
        "package.json",
        "playwright.config.ts",
        "postcss.config.mjs",
        "src",
        "tailwind.config.ts",
        "tsconfig.json",
        "vitest.config.mts",
    )
    assert directories == frozenset({"e2e", "src"})
    assert (max_nodes, max_bytes, max_depth) == (4096, 64 * 1024 * 1024, 32)
    assert "node_modules" not in entries
    assert ".next-voice-e2e" not in entries


def test_replacement_environment_is_build_only_and_defensive(tmp_path: Path) -> None:
    _source, _parent, _node, destination = _destination(tmp_path)
    request = destination._request
    environment = request._environment_values()

    assert environment.keys() == workspace_module._BUILD_ENVIRONMENT_NAMES
    assert environment["VOICE_E2E_NEXT_DIST_DIR"] == f".next-voice-e2e/{RUN_ID}"
    forbidden = {
        "VOICE_E2E_CALL_ID",
        "VOICE_E2E_NETWORK",
        "VOICE_E2E_ARTIFACT_DIR",
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE",
        "VOICE_E2E_RESULT_PATH",
        "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE",
        "MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID",
        "SSL_CERT_FILE",
        "TURN_USERNAME",
        "NODE_OPTIONS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    }
    assert forbidden.isdisjoint(environment)
    environment["CI"] = "0"
    assert request._environment_values()["CI"] == "1"


def test_all_graph_values_are_falsey_immutable_noncopyable_and_nonserializable(
    tmp_path: Path,
) -> None:
    _source, _parent, _node, destination = _destination(tmp_path)
    request = destination._request
    owner = destination._owner
    values = (
        request,
        destination,
        owner,
        owner._cleanup_authority,
        owner._receipt_destination,
    )

    for value in values:
        assert not value
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
    with pytest.raises(AttributeError):
        request._run_id = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        destination._owner = object()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        owner._cleanup_authority._key = object()  # type: ignore[misc]


def test_repr_and_module_exports_reveal_no_paths_or_policy_values(tmp_path: Path) -> None:
    source, parent, node, destination = _destination(tmp_path)
    values = (
        destination,
        destination._request,
        destination._owner,
        destination._owner._cleanup_authority,
        destination._owner._receipt_destination,
    )

    for value in values:
        rendered = repr(value)
        assert str(source) not in rendered
        assert str(parent) not in rendered
        assert str(node) not in rendered
        assert RUN_ID not in rendered
    assert workspace_module.__all__ == []


@pytest.mark.parametrize("run_id", ["", "UPPER", "space value", "../escape", "a" * 50])
def test_run_identifier_is_strict_and_creates_no_path(tmp_path: Path, run_id: str) -> None:
    source = (tmp_path / "source").resolve()
    parent = (tmp_path / "parent").resolve()
    node = (tmp_path / "node").resolve()

    with pytest.raises(
        workspace_module._RelayLinuxBuildWorkspaceContractError,
        match=r"preparation contract is invalid$",
    ):
        workspace_module._new_relay_linux_build_workspace_destination(
            source_root=source,
            run_parent=parent,
            node=node,
            run_id=run_id,
        )

    assert not source.exists() and not parent.exists() and not node.exists()


@pytest.mark.parametrize("field", ["source", "parent", "node"])
def test_nonabsolute_or_lexically_escaping_path_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    values = {
        "source": (tmp_path / "source").resolve(),
        "parent": (tmp_path / "parent").resolve(),
        "node": (tmp_path / "node").resolve(),
    }
    values[field] = Path("relative/../escape")
    with pytest.raises(workspace_module._RelayLinuxBuildWorkspaceContractError):
        workspace_module._new_relay_linux_build_workspace_destination(
            source_root=values["source"],
            run_parent=values["parent"],
            node=values["node"],
            run_id=RUN_ID,
        )


def test_cross_request_reads_and_external_constructor_tokens_are_rejected(
    tmp_path: Path,
) -> None:
    _source, _parent, _node, destination = _destination(tmp_path)
    other_root = (tmp_path / "other").resolve()
    other = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=other_root / "source",
        run_parent=other_root / "runs",
        node=other_root / "node",
        run_id=RUN_ID,
    )

    with pytest.raises(workspace_module._RelayLinuxBuildWorkspaceContractError):
        destination._read(other._request)
    with pytest.raises(workspace_module._RelayLinuxBuildWorkspaceContractError):
        destination._owner._receipt_destination._read(other._request)
    with pytest.raises(TypeError, match=r"request is factory-owned$"):
        workspace_module._RelayLinuxBuildWorkspaceRequest(
            object(),
            source_root=other_root,
            run_parent=other_root,
            node=other_root,
            run_id=RUN_ID,
            environment=(),
        )


def test_checkpoint_has_no_effect_revalidation_handoff_or_cleanup_api() -> None:
    for name in (
        "_prepare_relay_linux_build_workspace",
        "_revalidate_relay_linux_build_workspace",
        "_cleanup_prepared_relay_linux_build_workspace",
        "_spawn_relay_linux_build",
        "_publish_workspace_receipt",
    ):
        assert not hasattr(workspace_module, name)


def test_finite_controls_from_validation_are_not_reclassified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (tmp_path / "source").resolve()
    parent = (tmp_path / "parent").resolve()
    node = (tmp_path / "node").resolve()
    original = workspace_module._valid_absolute_path

    def interrupt(_path: object) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace_module, "_valid_absolute_path", interrupt)
    with pytest.raises(KeyboardInterrupt):
        workspace_module._new_relay_linux_build_workspace_destination(
            source_root=source,
            run_parent=parent,
            node=node,
            run_id=RUN_ID,
        )
    monkeypatch.setattr(workspace_module, "_valid_absolute_path", original)
    assert not source.exists() and not parent.exists() and not node.exists()

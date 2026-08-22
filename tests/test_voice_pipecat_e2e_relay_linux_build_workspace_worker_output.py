"""Focused synthetic tests for bounded Next workspace output validation."""
# ruff: noqa: E402

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry as process_facade_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as process_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as process_state
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract as process_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values as build_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open as fs_open
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output as fs_output
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_cleanup as fs_output_cleanup
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_workspace as fs_output_workspace
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspaceSourceNode,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_contract import (
    _DOCUMENT_NAMES,
    _MANDATORY_DIST_FILES,
    _REQUIRED_FILE_SUFFIXES,
    _STANDARD_NEXT_ENV,
    _expected_next_env,
    _load_json,
    _new_workspace_build_input_baseline,
    _validate_workspace_build_inputs,
    _validate_workspace_build_output_documents,
)

RUN_ID = "output-contract"
WORKSPACE = "/private/relay-linux-output-contract/web-workspace"
BUILD_ID = "Z_1234567890-BC_EFGHI"


@pytest.fixture(autouse=True)
def _isolate_workspace_build_canonical_state() -> Iterator[None]:
    mappings = (
        fs_contract._LEASES,
        fs_contract._PREPARED_BUILDS,
        build_values._COMMANDS,
        build_values._PROCESS_ASSOCIATIONS,
        build_values._COMMAND_GATES,
        build_values._COMMAND_CONTROLLERS,
        build_values._CONTROLLER_COMMANDS,
        build_receipt._BUILT_LEASES,
        build_receipt._BUILT_BY_COMMAND,
    )
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _source_tsconfig() -> bytes:
    return _json(
        {
            "compilerOptions": {"strict": True, "target": "ES2017"},
            "include": [
                "next-env.d.ts",
                "**/*.ts",
                ".next/types/**/*.ts",
                ".next/dev/types/**/*.ts",
            ],
            "exclude": ["node_modules"],
        }
    )


def _built_tsconfig() -> bytes:
    document = json.loads(_source_tsconfig())
    dist = f".next-voice-e2e/{RUN_ID}"
    document["include"].extend((f"{dist}/types/**/*.ts", f"{dist}/dev/types/**/*.ts"))
    return _json(document)


def _required_files() -> list[str]:
    prefix = f".next-voice-e2e/{RUN_ID}"
    return [f"{prefix}/{suffix}" for suffix in _REQUIRED_FILE_SUFFIXES]


def _regular_paths() -> frozenset[str]:
    static = f"static/{BUILD_ID}"
    return _MANDATORY_DIST_FILES | {
        f"{static}/_buildManifest.js",
        f"{static}/_ssgManifest.js",
    }


def _private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def _source_nodes(workspace: Path) -> tuple[_WorkspaceSourceNode, ...]:
    nodes: list[_WorkspaceSourceNode] = []

    def visit(parent: Path, relative: tuple[str, ...]) -> None:
        for child in sorted(parent.iterdir(), key=lambda path: path.name):
            if not relative and child.name == "node_modules":
                continue
            path = (*relative, child.name)
            details = child.stat(follow_symlinks=False)
            identity = _WorkspaceFilesystemIdentity.from_stat(details)
            if child.is_dir():
                nodes.append(_WorkspaceSourceNode(path, "directory", identity, None))
                visit(child, path)
            else:
                content = child.read_bytes()
                nodes.append(
                    _WorkspaceSourceNode(
                        path,
                        "file",
                        identity,
                        hashlib.sha256(content).digest(),
                    )
                )

    visit(workspace, ())
    return tuple(nodes)


def _write_dist(workspace: Path, *, extra: dict[str, bytes] | None = None) -> Path:
    parent = workspace / ".next-voice-e2e"
    parent.mkdir(mode=0o755, exist_ok=True)
    parent.chmod(0o755)
    root = parent / RUN_ID
    documents = _documents(workspace=str(workspace))
    values = {
        path: documents.get(path, b"export const sentinel = true;\n") for path in _regular_paths()
    }
    values.update(extra or {})
    directories = {root, root / "static", root / "static" / BUILD_ID}
    for relative in values:
        candidate = root / relative
        directories.update(candidate.parents)
    for directory in sorted(
        (path for path in directories if path == root or root in path.parents),
        key=lambda path: len(path.parts),
    ):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o755)
    for relative, content in values.items():
        path = root / relative
        path.write_bytes(content)
        path.chmod(0o644)
    return root


def _prepared_workspace_fixture(tmp_path: Path):
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=(tmp_path / "source").resolve(),
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id=RUN_ID,
    )
    request = destination._request
    owner_token = destination._owner._cleanup_authority._key
    record_token = object()
    workspace = request._workspace
    workspace.mkdir(mode=0o700, parents=True)
    workspace.chmod(0o700)
    _private_file(workspace / "next-env.d.ts", _STANDARD_NEXT_ENV)
    _private_file(workspace / "tsconfig.json", _source_tsconfig())
    _private_file(workspace / "package.json", b'{"scripts":{}}')
    source = workspace / "src"
    source.mkdir(mode=0o700)
    source.chmod(0o700)
    _private_file(source / "fixture.ts", b"export const fixture = true;\n")
    node_modules = request._node_modules
    node_modules.mkdir(mode=0o700, parents=True)
    node_modules.chmod(0o700)
    (workspace / "node_modules").symlink_to(node_modules)
    expected = _source_nodes(workspace)
    descriptors = fs_open._WorkspaceDescriptorSet()
    workspace_fd = fs_open._open_absolute_directory(workspace, descriptors)
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner_token,
    )
    return (
        workspace,
        node_modules,
        expected,
        _WorkspaceFilesystemIdentity.from_stat((workspace / "node_modules").lstat()),
        descriptors,
        workspace_fd,
        controller,
        owner_token,
        record_token,
        request,
    )


def _workspace_fixture(tmp_path: Path):
    (
        workspace,
        node_modules,
        expected,
        node_modules_identity,
        descriptors,
        workspace_fd,
        controller,
        owner_token,
        record_token,
        request,
    ) = _prepared_workspace_fixture(tmp_path)
    baseline = fs_output_workspace._snapshot_workspace_build_inputs(
        workspace_fd=workspace_fd,
        owner_token=owner_token,
        record_token=record_token,
        run_id=RUN_ID,
        expected_destination=expected,
        expected_node_modules=node_modules_identity,
        node_modules_target=str(node_modules),
        descriptors=descriptors,
        controller=controller,
    )
    _private_file(workspace / "next-env.d.ts", _expected_next_env(RUN_ID))
    _private_file(workspace / "tsconfig.json", _built_tsconfig())
    dist = _write_dist(workspace)
    return (
        workspace,
        node_modules,
        dist,
        expected,
        descriptors,
        workspace_fd,
        controller,
        baseline,
        owner_token,
        record_token,
        request,
    )


def _snapshot_prepared(values: tuple[object, ...]) -> object:
    (
        _workspace,
        node_modules,
        expected,
        node_modules_identity,
        descriptors,
        workspace_fd,
        controller,
        owner_token,
        record_token,
        _request,
    ) = values
    return fs_output_workspace._snapshot_workspace_build_inputs(
        workspace_fd=workspace_fd,
        owner_token=owner_token,
        record_token=record_token,
        run_id=RUN_ID,
        expected_destination=expected,
        expected_node_modules=node_modules_identity,
        node_modules_target=str(node_modules),
        descriptors=descriptors,
        controller=controller,
    )


def _validate_fixture(values: tuple[object, ...]) -> object:
    workspace = values[0]
    descriptors = values[4]
    workspace_fd = values[5]
    controller = values[6]
    baseline = values[7]
    return fs_output._validate_workspace_build_output(
        workspace_fd=workspace_fd,
        workspace=workspace,
        baseline=baseline,
        run_id=RUN_ID,
        descriptors=descriptors,
        controller=controller,
    )


def _documents(*, workspace: str = WORKSPACE) -> dict[str, bytes]:
    dist = f".next-voice-e2e/{RUN_ID}"
    documents = {
        "BUILD_ID": BUILD_ID.encode(),
        "package.json": b'{"type": "commonjs"}',
        "routes-manifest.json": _json(
            {
                "version": 3,
                "pages404": True,
                "caseSensitive": False,
                "basePath": "",
                "appType": "app",
                "staticRoutes": [
                    {
                        "page": "/e2e/voice",
                        "regex": "^/e2e/voice(?:/)?$",
                        "routeKeys": {},
                        "namedRegex": "^/e2e/voice(?:/)?$",
                    }
                ],
                "dynamicRoutes": [],
            }
        ),
        "app-path-routes-manifest.json": _json({"/e2e/voice/page": "/e2e/voice"}),
        "server/app-paths-manifest.json": _json({"/e2e/voice/page": "app/e2e/voice/page.js"}),
        "prerender-manifest.json": _json({"version": 4, "routes": {}, "dynamicRoutes": {}}),
        "required-server-files.json": _json(
            {
                "version": 1,
                "appDir": workspace,
                "relativeAppDir": "",
                "ignore": [],
                "files": _required_files(),
                "config": {
                    "distDir": dist,
                    "distDirRoot": dist,
                    "configOrigin": "next.config.mjs",
                    "configFileName": "next.config.mjs",
                    "typedRoutes": False,
                },
            }
        ),
        "server/middleware-manifest.json": _json(
            {"version": 3, "middleware": {}, "functions": {}, "sortedMiddleware": []}
        ),
    }
    assert documents.keys() == _DOCUMENT_NAMES
    return documents


def test_exact_next_input_mutations_are_accepted() -> None:
    baseline = _new_workspace_build_input_baseline(
        b'/// <reference types="next" />\n'
        b'/// <reference types="next/image-types/global" />\n'
        b'import "./.next/types/routes.d.ts";\n'
        b'import "./.next/types/root-params.d.ts";\n'
        b"\n"
        b"// NOTE: This file should not be edited\n"
        b"// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.\n",
        _source_tsconfig(),
    )

    _validate_workspace_build_inputs(
        baseline,
        next_env=_expected_next_env(RUN_ID),
        tsconfig=_built_tsconfig(),
        run_id=RUN_ID,
    )


@pytest.mark.parametrize("mutation", ["next-env", "tsconfig-order", "tsconfig-other"])
def test_any_other_input_mutation_is_rejected(mutation: str) -> None:
    baseline = _new_workspace_build_input_baseline(
        b'/// <reference types="next" />\n'
        b'/// <reference types="next/image-types/global" />\n'
        b'import "./.next/types/routes.d.ts";\n'
        b'import "./.next/types/root-params.d.ts";\n'
        b"\n"
        b"// NOTE: This file should not be edited\n"
        b"// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.\n",
        _source_tsconfig(),
    )
    next_env = _expected_next_env(RUN_ID)
    tsconfig = json.loads(_built_tsconfig())
    if mutation == "next-env":
        next_env += b"// changed\n"
    elif mutation == "tsconfig-order":
        tsconfig["include"][-2:] = reversed(tsconfig["include"][-2:])
    else:
        tsconfig["compilerOptions"]["strict"] = False

    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_inputs(
            baseline,
            next_env=next_env,
            tsconfig=_json(tsconfig),
            run_id=RUN_ID,
        )


def test_pinned_next_output_documents_are_accepted() -> None:
    assert _REQUIRED_FILE_SUFFIXES[7:11] == (
        "server/middleware-build-manifest.js",
        "server/middleware-react-loadable-manifest.js",
        "react-loadable-manifest.json",
        "server/app-paths-manifest.json",
    )


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("file", "unknown.bin"),
        ("file", "fallback-build-manifest.json"),
        ("directory", "turbopack"),
        ("file", "server"),
    ],
)
def test_output_rejects_unknown_or_wrong_kind_top_level_names(
    kind: str,
    name: str,
) -> None:
    directories = {"static", f"static/{BUILD_ID}"}
    regular = set(_regular_paths())
    (regular if kind == "file" else directories).add(name)
    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_output_documents(
            _documents(),
            directory_paths=frozenset(directories),
            nonempty_paths=frozenset(regular),
            regular_paths=frozenset(regular),
            run_id=RUN_ID,
            workspace=WORKSPACE,
        )
    assert "server/app/e2e/voice/page.js" in _MANDATORY_DIST_FILES
    assert "app/e2e/voice/page.js" not in _MANDATORY_DIST_FILES
    _validate_workspace_build_output_documents(
        _documents(),
        directory_paths=frozenset({"static", f"static/{BUILD_ID}"}),
        nonempty_paths=_regular_paths(),
        regular_paths=_regular_paths(),
        run_id=RUN_ID,
        workspace=WORKSPACE,
    )


@pytest.mark.parametrize(
    "value",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"\xff"],
)
def test_json_decoder_rejects_duplicates_nonfinite_and_invalid_utf8(value: bytes) -> None:
    with pytest.raises(_WorkspaceFilesystemError):
        _load_json(value, 1024)


def test_json_decoder_enforces_aggregate_array_member_limit() -> None:
    assert type(_load_json(_json([0] * 4096), 16 * 1024)) is list
    with pytest.raises(_WorkspaceFilesystemError):
        _load_json(_json([0] * 4097), 16 * 1024)


@pytest.mark.parametrize("mutation", ["build-id", "package", "route", "config", "sentinel"])
def test_pinned_output_mismatch_is_rejected(mutation: str) -> None:
    documents = _documents()
    directories = frozenset({"static", f"static/{BUILD_ID}"})
    regular = _regular_paths()
    if mutation == "build-id":
        documents["BUILD_ID"] = b"ad1234567890123456789"
    elif mutation == "package":
        documents["package.json"] = b'{"type":"commonjs"}'
    elif mutation == "route":
        routes = json.loads(documents["routes-manifest.json"])
        routes["staticRoutes"][0]["regex"] = "^/wrong$"
        documents["routes-manifest.json"] = _json(routes)
    elif mutation == "config":
        required = json.loads(documents["required-server-files.json"])
        required["config"]["distDirRoot"] = ".next"
        documents["required-server-files.json"] = _json(required)
    else:
        regular = frozenset(regular - {"types/validator.ts"})

    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_output_documents(
            documents,
            directory_paths=directories,
            nonempty_paths=regular,
            regular_paths=regular,
            run_id=RUN_ID,
            workspace=WORKSPACE,
        )


def test_output_requires_static_directory_bound_to_exact_build_id() -> None:
    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_output_documents(
            _documents(),
            directory_paths=frozenset({"static", "static/wrong-build-id"}),
            nonempty_paths=_regular_paths(),
            regular_paths=_regular_paths(),
            run_id=RUN_ID,
            workspace=WORKSPACE,
        )


@pytest.mark.parametrize("name", ["_buildManifest.js", "_ssgManifest.js"])
def test_output_requires_both_webpack_build_id_manifests(name: str) -> None:
    static = f"static/{BUILD_ID}"
    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_output_documents(
            _documents(),
            directory_paths=frozenset({"static", static}),
            nonempty_paths=frozenset(_regular_paths() - {f"{static}/{name}"}),
            regular_paths=frozenset(_regular_paths() - {f"{static}/{name}"}),
            run_id=RUN_ID,
            workspace=WORKSPACE,
        )


@pytest.mark.parametrize("name", ["_buildManifest.js", "_ssgManifest.js"])
def test_output_requires_nonempty_webpack_build_id_manifests(name: str) -> None:
    static = f"static/{BUILD_ID}"
    with pytest.raises(_WorkspaceFilesystemError):
        _validate_workspace_build_output_documents(
            _documents(),
            directory_paths=frozenset({"static", static}),
            nonempty_paths=frozenset(_regular_paths() - {f"{static}/{name}"}),
            regular_paths=_regular_paths(),
            run_id=RUN_ID,
            workspace=WORKSPACE,
        )


def test_descriptor_traversal_accepts_safe_0755_0644_output_and_is_stable(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    try:
        first = _validate_fixture(values)
        second = _validate_fixture(values)
        assert type(first.digest) is bytes and len(first.digest) == 32
        assert second == first
    finally:
        assert descriptors.close_all()


def test_prepared_snapshot_rejects_preexisting_reserved_dist_parent(
    tmp_path: Path,
) -> None:
    values = _prepared_workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    (workspace / ".next-voice-e2e").mkdir(mode=0o700)
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _snapshot_prepared(values)
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("replacement", ["file", "directory", "node-modules"])
def test_prepared_snapshot_rejects_same_content_node_replacement(
    tmp_path: Path,
    replacement: str,
) -> None:
    values = _prepared_workspace_fixture(tmp_path)
    workspace = values[0]
    node_modules = values[1]
    descriptors = values[4]
    if replacement == "file":
        target = workspace / "package.json"
        target.rename(tmp_path / "old-package.json")
        _private_file(target, b'{"scripts":{}}')
    elif replacement == "directory":
        target = workspace / "src"
        target.rename(tmp_path / "old-src")
        target.mkdir(mode=0o700)
        target.chmod(0o700)
        _private_file(target / "fixture.ts", b"export const fixture = true;\n")
    else:
        target = workspace / "node_modules"
        target.rename(tmp_path / "old-node-modules-link")
        target.symlink_to(node_modules)
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _snapshot_prepared(values)
    finally:
        assert descriptors.close_all()


def test_prepared_snapshot_rejects_metadata_change_even_after_mode_restore(
    tmp_path: Path,
) -> None:
    values = _prepared_workspace_fixture(tmp_path)
    target = values[0] / "package.json"
    descriptors = values[4]
    target.chmod(0o644)
    target.chmod(0o600)
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _snapshot_prepared(values)
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("name", ["next-env.d.ts", "tsconfig.json"])
def test_two_output_passes_detect_permitted_file_identity_replacement(
    tmp_path: Path,
    name: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    try:
        first = _validate_fixture(values)
        target = workspace / name
        content = target.read_bytes()
        target.rename(tmp_path / f"old-{name}")
        _private_file(target, content)
        second = _validate_fixture(values)
        assert second.digest == first.digest
        assert second != first
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("replacement", ["parent", "root"])
def test_two_output_passes_detect_dist_root_identity_replacement(
    tmp_path: Path,
    replacement: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    dist = values[2]
    descriptors = values[4]
    try:
        first = _validate_fixture(values)
        if replacement == "parent":
            (workspace / ".next-voice-e2e").rename(tmp_path / "old-dist-parent")
        else:
            dist.rename(tmp_path / "old-dist-root")
        _write_dist(workspace)
        second = _validate_fixture(values)
        assert second.digest == first.digest
        assert second != first
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_descriptor_traversal_rejects_group_world_writable_output(
    tmp_path: Path,
    kind: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    dist = values[2]
    descriptors = values[4]
    target = dist / "server" if kind == "directory" else dist / "types" / "routes.d.ts"
    target.chmod(0o777 if kind == "directory" else 0o666)
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _validate_fixture(values)
    finally:
        assert descriptors.close_all()


def test_descriptor_traversal_rejects_hardlinked_regular_output(tmp_path: Path) -> None:
    values = _workspace_fixture(tmp_path)
    dist = values[2]
    descriptors = values[4]
    os.link(dist / "types" / "routes.d.ts", tmp_path / "retained-hardlink")
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _validate_fixture(values)
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize(("depth", "accepted"), [(32, True), (33, False)])
def test_descriptor_traversal_enforces_exact_relative_depth_limit(
    tmp_path: Path,
    depth: int,
    accepted: bool,
) -> None:
    values = _workspace_fixture(tmp_path)
    dist = values[2]
    descriptors = values[4]
    parts = ("cache", *(f"d{index}" for index in range(depth - 2)))
    parent = dist.joinpath(*parts)
    parent.mkdir(mode=0o755, parents=True)
    leaf = parent / "leaf.bin"
    leaf.write_bytes(b"bounded")
    leaf.chmod(0o644)
    try:
        if accepted:
            assert len(_validate_fixture(values).digest) == 32
        else:
            with pytest.raises(_WorkspaceFilesystemError):
                _validate_fixture(values)
    finally:
        assert descriptors.close_all()


def test_non_document_output_is_stream_hashed_without_full_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _workspace_fixture(tmp_path)
    dist = values[2]
    descriptors = values[4]
    large = dist / "server" / "streamed.bin"
    large.write_bytes(b"x" * (2 * 1024 * 1024))
    large.chmod(0o644)
    captured: list[str] = []
    original = fs_output._read_output_regular

    def recording_read(parent_fd, name, **kwargs):
        captured.append(name)
        return original(parent_fd, name, **kwargs)

    monkeypatch.setattr(fs_output, "_read_output_regular", recording_read)
    try:
        assert len(_validate_fixture(values).digest) == 32
        assert "streamed.bin" not in captured
    finally:
        assert descriptors.close_all()


def _output_cleanup_state(
    values: tuple[object, ...],
    *,
    built_status: str | None = None,
    released: bool = True,
    revoke_prepared: bool = True,
) -> object:
    workspace_fd = values[5]
    baseline = values[7]
    owner_token = values[8]
    record_token = values[9]
    request = values[10]
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    build_deadline = float(time.monotonic() + 2.0)
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=build_deadline,
        expected_spawn_fingerprint=process_contract._workspace_request_spawn_fingerprint(request),
    )
    assert (
        build_values._claim_workspace_build_command(
            command,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
        )
        == build_deadline
    )
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner_token,
    )
    assert build_values._bind_workspace_build_command_controller(
        command,
        controller=controller,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=build_deadline,
    )
    spec = spawn_module._new_relay_linux_build_spec(
        node=request._node,
        next_cli=request._next_cli,
        workspace=request._workspace,
        run_id=request._run_id,
        environment=request._environment_values(),
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = process_registry._new_build_owner_destination(spec, raw)
    candidate = destination._read()
    process_contract._intend_workspace_build_process_association(
        command,
        owner_token=owner_token,
        record_token=record_token,
        process_owner=candidate,
        expected_spec=spec,
        expected_raw_destination=raw,
    )
    if built_status is not None:
        assert released
        process_receipt = process_state._RelayLinuxBuildProcessReceipt(
            process_state._RECEIPT_TOKEN,
            owner_token=candidate._owner_token,
        )
        command_state = build_values._COMMANDS[command]
        build_values._store_command_state(
            command,
            (*command_state[:4], "built", command_state[5]),
        )
        build_values._PROCESS_ASSOCIATIONS[command] = (
            owner_token,
            record_token,
            candidate._owner_token,
            candidate._cleanup_authority,
            command_state[5],
            process_receipt,
            "released-zero",
        )
    elif released:
        assert process_contract._complete_failed_workspace_build_process(command)
        build_values._workspace_build_command_failed(command)
    if built_status is not None:
        built = build_receipt._WorkspaceBuiltReceipt(
            build_receipt._BUILT_TOKEN,
            owner_token=owner_token,
            record_token=record_token,
        )
        build_receipt._BUILT_LEASES[built] = (
            owner_token,
            record_token,
            command,
            b"o" * 32,
            process_receipt,
            built_status,
        )
        build_receipt._BUILT_BY_COMMAND[command] = built
    if revoke_prepared:
        assert fs_contract._revoke_workspace_prepared_receipt(
            prepared,
            owner_token,
            record_token,
        )
    return fs_output_cleanup._new_workspace_build_output_cleanup_state(
        command=command,
        prepared=prepared,
        baseline=baseline,
        owner_token=owner_token,
        record_token=record_token,
        request=request,
        workspace_fd=workspace_fd,
        workspace_identity=_WorkspaceFilesystemIdentity.from_stat(os.fstat(workspace_fd)),
        run_id=RUN_ID,
        descriptors=values[4],
    )


def _repeat_output_cleanup_factory(values: tuple[object, ...], state: object) -> object:
    return fs_output_cleanup._new_workspace_build_output_cleanup_state(
        command=state.command,
        prepared=state.prepared,
        baseline=values[7],
        owner_token=values[8],
        record_token=values[9],
        request=values[10],
        workspace_fd=values[5],
        workspace_identity=_WorkspaceFilesystemIdentity.from_stat(os.fstat(values[5])),
        run_id=RUN_ID,
        descriptors=values[4],
    )


def test_rejected_output_cleanup_requires_canonical_process_absence(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _output_cleanup_state(values, released=False)
        assert (workspace / ".next-voice-e2e" / RUN_ID).is_dir()
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("lease", ["prepared", "built"])
def test_output_cleanup_factory_requires_all_canonical_leases_revoked(
    tmp_path: Path,
    lease: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _output_cleanup_state(
                values,
                built_status="active" if lease == "built" else None,
                revoke_prepared=lease != "prepared",
            )
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize("lease", ["prepared", "built"])
def test_output_cleanup_rechecks_lease_revocation_before_every_retry(
    tmp_path: Path,
    lease: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(
        values,
        built_status="revoked" if lease == "built" else None,
    )
    if lease == "prepared":
        lease_state = fs_contract._LEASES[state.prepared]
        fs_contract._LEASES[state.prepared] = (*lease_state[:3], "active")
        receipt = None
    else:
        receipt = build_receipt._BUILT_BY_COMMAND[state.command]
        lease_state = build_receipt._BUILT_LEASES[receipt]
        build_receipt._BUILT_LEASES[receipt] = (*lease_state[:5], "active")
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
        if lease == "prepared":
            fs_contract._LEASES[state.prepared] = lease_state
        else:
            assert receipt is not None
            build_receipt._BUILT_LEASES[receipt] = (*lease_state[:5], "revoked")
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
    finally:
        assert descriptors.close_all()


def test_output_cleanup_accepts_only_the_exact_revoked_built_mapping(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values, built_status="revoked")
    try:
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert not (values[0] / ".next-voice-e2e").exists()
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize(
    "corruption",
    [
        "status",
        "registry-fingerprint",
        "internal-fingerprint",
        "lease-active",
        "duplicate-association",
        "foreign-orphan",
    ],
)
def test_output_cleanup_rejects_malformed_prepared_revocation(
    tmp_path: Path,
    corruption: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    if corruption == "status":
        object.__setattr__(state.prepared, "status", "wrong")
    elif corruption == "registry-fingerprint":
        lease = fs_contract._LEASES[state.prepared]
        fs_contract._LEASES[state.prepared] = (*lease[:2], object(), lease[3])
    elif corruption == "internal-fingerprint":
        object.__setattr__(state.prepared, "_fingerprint", b"x" * 32)
    elif corruption == "lease-active":
        object.__setattr__(state.prepared, "_lease_active", True)
    elif corruption == "duplicate-association":
        duplicate = fs_contract._new_workspace_prepared_receipt(
            owner_token=state.owner_token,
            record_token=state.record_token,
            fingerprint=b"d" * 32,
        )
        fs_contract._PREPARED_BUILDS[duplicate] = fs_contract._PREPARED_BUILDS[state.prepared]
    else:
        orphan = fs_contract._new_workspace_prepared_receipt(
            owner_token=state.owner_token,
            record_token=state.record_token,
            fingerprint=b"f" * 32,
        )
        fs_contract._PREPARED_BUILDS[orphan] = (
            state.owner_token,
            state.record_token,
            object(),
            state.build_deadline,
            "intended",
        )
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


@pytest.mark.parametrize(
    "corruption",
    [
        "status",
        "digest",
        "process",
        "process-status",
        "released-failed",
        "duplicate-reverse",
        "orphan-reverse",
    ],
)
def test_output_cleanup_rejects_malformed_or_impossible_built_revocation(
    tmp_path: Path,
    corruption: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values, built_status="revoked")
    receipt = build_receipt._BUILT_BY_COMMAND[state.command]
    lease = build_receipt._BUILT_LEASES[receipt]
    association = build_values._PROCESS_ASSOCIATIONS[state.command]
    command_state = build_values._COMMANDS[state.command]
    if corruption == "status":
        object.__setattr__(receipt, "status", "wrong")
    elif corruption == "digest":
        build_receipt._BUILT_LEASES[receipt] = (*lease[:3], object(), *lease[4:])
    elif corruption == "process":
        build_receipt._BUILT_LEASES[receipt] = (*lease[:4], object(), lease[5])
    elif corruption == "process-status":
        object.__setattr__(lease[4], "status", "wrong")
    elif corruption == "released-failed":
        build_values._COMMANDS[state.command] = (*command_state[:4], "failed", command_state[5])
        build_values._PROCESS_ASSOCIATIONS[state.command] = (
            *association[:5],
            None,
            "released-failed",
        )
    elif corruption == "duplicate-reverse":
        duplicate = build_receipt._WorkspaceBuiltReceipt(
            build_receipt._BUILT_TOKEN,
            owner_token=state.owner_token,
            record_token=state.record_token,
        )
        build_receipt._BUILT_LEASES[duplicate] = lease
    else:
        build_receipt._BUILT_BY_COMMAND.pop(state.command)
        build_values._COMMANDS[state.command] = (*command_state[:4], "failed", command_state[5])
        build_values._PROCESS_ASSOCIATIONS[state.command] = (
            *association[:5],
            None,
            "released-failed",
        )
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_absent_built_proof_rejects_any_orphan_state(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    orphan = build_receipt._WorkspaceBuiltReceipt(
        build_receipt._BUILT_TOKEN,
        owner_token=state.owner_token,
        record_token=state.record_token,
    )
    build_receipt._BUILT_LEASES[orphan] = (
        state.owner_token,
        state.record_token,
        object(),
        object(),
        object(),
        "active",
    )
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_rejects_cross_wired_authentic_process_authority(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    association = build_values._PROCESS_ASSOCIATIONS[state.command]
    request = values[10]
    other_spec = spawn_module._new_relay_linux_build_spec(
        node=request._node,
        next_cli=request._next_cli,
        workspace=request._workspace,
        run_id=request._run_id,
        environment=request._environment_values(),
    )
    other_raw = spawn_module._new_raw_build_process_destination(other_spec)
    other_owner = process_registry._new_build_owner_destination(other_spec, other_raw)._read()
    build_values._PROCESS_ASSOCIATIONS[state.command] = (
        *association[:3],
        other_owner._cleanup_authority,
        *association[4:],
    )
    try:
        assert other_owner._cleanup_authority._is_authentic()
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_requires_the_sole_command_and_controller_graph(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    fingerprint = build_values._COMMANDS[state.command][5]
    other_command = build_values._WorkspaceBuildCommand(
        build_values._COMMAND_TOKEN,
        owner_token=state.owner_token,
        record_token=state.record_token,
        prepared=state.prepared,
        build_deadline=state.build_deadline,
        expected_spawn_fingerprint=fingerprint,
    )
    other_state = build_values._COMMANDS[other_command]
    build_values._COMMANDS[other_command] = (*other_state[:4], "building", fingerprint)
    other_controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=state.owner_token,
    )
    assert build_values._bind_workspace_build_command_controller(
        other_command,
        controller=other_controller,
        owner_token=state.owner_token,
        record_token=state.record_token,
        build_deadline=state.build_deadline,
    )
    request = values[10]
    other_spec = spawn_module._new_relay_linux_build_spec(
        node=request._node,
        next_cli=request._next_cli,
        workspace=request._workspace,
        run_id=request._run_id,
        environment=request._environment_values(),
    )
    other_raw = spawn_module._new_raw_build_process_destination(other_spec)
    other_owner = process_registry._new_build_owner_destination(other_spec, other_raw)._read()
    build_values._PROCESS_ASSOCIATIONS[other_command] = (
        state.owner_token,
        state.record_token,
        other_owner._owner_token,
        other_owner._cleanup_authority,
        fingerprint,
        None,
        "associated",
    )
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_requires_global_process_registry_absence(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    request = values[10]
    other_spec = spawn_module._new_relay_linux_build_spec(
        node=request._node,
        next_cli=request._next_cli,
        workspace=request._workspace,
        run_id=request._run_id,
        environment=request._environment_values(),
    )
    other_raw = spawn_module._new_raw_build_process_destination(other_spec)
    destination = process_registry._new_build_owner_destination(other_spec, other_raw)
    other_owner = process_registry._preown_build_process(
        spec=other_spec,
        raw_destination=other_raw,
        destination=destination,
    )
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            _repeat_output_cleanup_factory(values, state)
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert (values[0] / ".next-voice-e2e").is_dir()
    finally:
        assert process_facade_registry._release_build_process_registries(other_owner)
        assert descriptors.close_all()


@pytest.mark.parametrize(
    "mismatch",
    ["prepared", "baseline", "owner", "record", "request", "run", "workspace", "bool-fd"],
)
def test_output_cleanup_factory_rejects_cross_transaction_authority(
    tmp_path: Path,
    mismatch: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    other_descriptors = None
    workspace_identity = _WorkspaceFilesystemIdentity.from_stat(os.fstat(values[5]))
    kwargs = {
        "command": state.command,
        "prepared": state.prepared,
        "baseline": values[7],
        "owner_token": values[8],
        "record_token": values[9],
        "request": values[10],
        "workspace_fd": values[5],
        "workspace_identity": workspace_identity,
        "run_id": RUN_ID,
        "descriptors": descriptors,
    }
    if mismatch == "prepared":
        kwargs["prepared"] = fs_contract._new_workspace_prepared_receipt(
            owner_token=values[8],
            record_token=values[9],
            fingerprint=b"x" * 32,
        )
    elif mismatch == "baseline":
        other = _workspace_fixture(tmp_path / "other")
        other_descriptors = other[4]
        kwargs["baseline"] = other[7]
    elif mismatch == "owner":
        kwargs["owner_token"] = object()
    elif mismatch == "record":
        kwargs["record_token"] = object()
    elif mismatch == "request":
        kwargs["request"] = workspace_module._new_relay_linux_build_workspace_destination(
            source_root=(tmp_path / "other-source").resolve(),
            run_parent=(tmp_path / "other-runs").resolve(),
            node=(tmp_path / "other-node").resolve(),
            run_id="other-output",
        )._request
    elif mismatch == "run":
        kwargs["run_id"] = "other-output"
    elif mismatch == "workspace":
        kwargs["workspace_identity"] = _WorkspaceFilesystemIdentity(
            workspace_identity.device,
            workspace_identity.inode + 1,
            workspace_identity.mode,
            workspace_identity.links,
            workspace_identity.size,
            workspace_identity.modified_ns,
            workspace_identity.changed_ns,
        )
    else:
        kwargs["workspace_fd"] = True
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._new_workspace_build_output_cleanup_state(**kwargs)
    finally:
        if other_descriptors is not None:
            assert other_descriptors.close_all()
        assert descriptors.close_all()


def test_rejected_output_cleanup_budget_covers_validation_limit_plus_one() -> None:
    assert fs_output._MAX_DIST_NODES + 1 <= fs_output_cleanup._MAX_OUTPUT_CLEANUP_NODES < 16_384


def test_process_absent_cleanup_unlinks_rejected_leaf_types_without_following(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    dist = values[2]
    descriptors = values[4]
    outside = tmp_path / "outside"
    outside.write_bytes(b"retained")
    (dist / "server" / "rejected-link").symlink_to(outside)
    os.link(outside, dist / "server" / "rejected-hardlink")
    os.mkfifo(dist / "server" / "rejected-fifo", mode=0o600)
    socket_path = dist / "server" / "rejected-socket"
    with tempfile.TemporaryDirectory(dir="/tmp") as short:
        short_parent = Path(short) / "d"
        short_parent.symlink_to(dist / "server")
        endpoint = socket.socket(socket.AF_UNIX)
        endpoint.bind(str(short_parent / socket_path.name))
        endpoint.close()
    state = _output_cleanup_state(values)
    try:
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert not (workspace / ".next-voice-e2e").exists()
        assert outside.read_bytes() == b"retained"
        assert outside.stat().st_nlink == 1
    finally:
        assert descriptors.close_all()


def test_process_absent_cleanup_removes_all_build_owned_reserved_parent_siblings(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    sibling = workspace / ".next-voice-e2e" / "unexpected-sibling"
    sibling.mkdir(mode=0o755)
    (sibling / "owned.txt").write_bytes(b"build-owned")
    state = _output_cleanup_state(values)
    try:
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert not (workspace / ".next-voice-e2e").exists()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_makes_bounded_progress_beyond_one_attempt_budget(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    sibling = workspace / ".next-voice-e2e" / "unexpected-sibling"
    sibling.mkdir(mode=0o755)
    total = fs_output_cleanup._MAX_OUTPUT_CLEANUP_NODES + 16
    for index in range(total):
        (sibling / f"f{index:05d}").touch(mode=0o600)
    state = _output_cleanup_state(values)
    try:
        assert fs_output_cleanup._cleanup_workspace_build_output(state) is False
        remaining = sum(1 for _entry in sibling.iterdir())
        assert 0 < remaining < total
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert not (workspace / ".next-voice-e2e").exists()
    finally:
        assert descriptors.close_all()


def test_output_cleanup_iteratively_removes_tree_deeper_than_validation_limit(
    tmp_path: Path,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    current = workspace / ".next-voice-e2e" / "deep-sibling"
    current.mkdir(mode=0o755)
    for _depth in range(70):
        current /= "d"
        current.mkdir(mode=0o755)
    (current / "leaf").touch(mode=0o600)
    state = _output_cleanup_state(values)
    try:
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert not (workspace / ".next-voice-e2e").exists()
    finally:
        assert descriptors.close_all()


def test_rejected_output_cleanup_never_crosses_a_descendant_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _workspace_fixture(tmp_path)
    dist = values[2]
    descriptors = values[4]
    mounted = dist / "000-mounted"
    mounted.mkdir(mode=0o755)
    retained = mounted / "retained"
    retained.write_bytes(b"outside-device")
    original = fs_output_cleanup.os.stat

    def modeled_stat(path: object, *args: object, **kwargs: object) -> object:
        details = original(path, *args, **kwargs)
        if path != mounted.name:
            return details
        return SimpleNamespace(
            st_ctime_ns=details.st_ctime_ns,
            st_dev=details.st_dev + 1,
            st_ino=details.st_ino,
            st_mode=details.st_mode,
            st_mtime_ns=details.st_mtime_ns,
            st_nlink=details.st_nlink,
            st_size=details.st_size,
            st_uid=details.st_uid,
        )

    monkeypatch.setattr(fs_output_cleanup.os, "stat", modeled_stat)
    try:
        with pytest.raises(_WorkspaceFilesystemError):
            fs_output_cleanup._cleanup_workspace_build_output(_output_cleanup_state(values))
        assert retained.read_bytes() == b"outside-device"
    finally:
        assert descriptors.close_all()


def test_rejected_output_cleanup_never_retries_an_ambiguous_descriptor_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _workspace_fixture(tmp_path)
    descriptors = values[4]
    state = _output_cleanup_state(values)
    original = fs_open._OS_CLOSE
    failed_descriptor = None
    calls: dict[int, int] = {}

    def close_then_raise(descriptor: int) -> None:
        nonlocal failed_descriptor
        calls[descriptor] = calls.get(descriptor, 0) + 1
        if failed_descriptor is None:
            failed_descriptor = descriptor
            original(descriptor)
            raise OSError("synthetic ambiguous close return")
        original(descriptor)

    monkeypatch.setattr(fs_open, "_OS_CLOSE", close_then_raise)
    with pytest.raises(OSError):
        fs_output_cleanup._cleanup_workspace_build_output(state)
    with pytest.raises(_WorkspaceFilesystemError):
        fs_output_cleanup._cleanup_workspace_build_output(state)

    assert failed_descriptor is not None
    assert calls[failed_descriptor] == 1
    assert state.complete is False
    monkeypatch.undo()
    assert not descriptors.close_all()


@pytest.mark.parametrize("operation", ["unlink", "rmdir", "stat-readback", "fsync"])
def test_rejected_output_cleanup_reconciles_stored_effect_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    values = _workspace_fixture(tmp_path)
    workspace = values[0]
    descriptors = values[4]
    state = _output_cleanup_state(values)
    faulted = False
    readback = False
    if operation == "unlink":
        original = fs_output_cleanup.os.unlink

        def lose_unlink(*args: object, **kwargs: object) -> None:
            nonlocal faulted
            original(*args, **kwargs)
            if not faulted:
                faulted = True
                raise OSError("synthetic unlink return loss")

        monkeypatch.setattr(fs_output_cleanup.os, "unlink", lose_unlink)
    elif operation in {"rmdir", "stat-readback"}:
        original = fs_output_cleanup.os.rmdir

        def lose_rmdir(*args: object, **kwargs: object) -> None:
            nonlocal faulted, readback
            original(*args, **kwargs)
            if not faulted:
                faulted = True
                if operation == "stat-readback":
                    readback = True
                else:
                    raise OSError("synthetic rmdir return loss")

        monkeypatch.setattr(fs_output_cleanup.os, "rmdir", lose_rmdir)
        if operation == "stat-readback":
            original_stat = fs_output_cleanup.os.stat

            def lose_stat(*args: object, **kwargs: object) -> object:
                nonlocal readback
                if readback:
                    readback = False
                    raise OSError("synthetic absence-readback loss")
                return original_stat(*args, **kwargs)

            monkeypatch.setattr(fs_output_cleanup.os, "stat", lose_stat)
    else:
        original = fs_output_cleanup.os.fsync

        def lose_fsync(*args: object, **kwargs: object) -> None:
            nonlocal faulted
            original(*args, **kwargs)
            if not faulted:
                faulted = True
                raise OSError("synthetic fsync return loss")

        monkeypatch.setattr(fs_output_cleanup.os, "fsync", lose_fsync)
    try:
        with pytest.raises(OSError):
            fs_output_cleanup._cleanup_workspace_build_output(state)
        assert fs_output_cleanup._cleanup_workspace_build_output(state)
        assert faulted is True
        assert not (workspace / ".next-voice-e2e").exists()
    finally:
        assert descriptors.close_all()

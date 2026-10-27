"""Pure bounded Next 16.3 build-input and output-schema validation."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
)

_JSON_BYTES = 8 * 1024 * 1024
_JSON_DEPTH = 64
_JSON_MEMBERS = 4096
_JSON_STRING_BYTES = 1024 * 1024
_NEXT_ENV_BYTES = 4096
_TSCONFIG_BYTES = 64 * 1024
_VOICE_ROUTE = "/e2e/voice"
_VOICE_APP_PATH = "/e2e/voice/page"
_VOICE_SERVER_MANIFEST_VALUE = "app/e2e/voice/page.js"
_VOICE_SERVER_DIST_FILE = f"server/{_VOICE_SERVER_MANIFEST_VALUE}"
_VOICE_ROUTE_PATTERN = "^/e2e/voice(?:/)?$"
_BUILD_ID = re.compile(rb"^(?!.*ad)[A-Za-z0-9_-]{21}$", re.IGNORECASE)
_DIST_PARENT = ".next-voice-e2e"
_ALLOWED_ROOT_DIRECTORIES = frozenset({"cache", "diagnostics", "server", "static", "types"})
_ALLOWED_ROOT_FILES = frozenset(
    {
        "BUILD_ID",
        "app-path-routes-manifest.json",
        "build-manifest.json",
        "export-marker.json",
        "images-manifest.json",
        "next-minimal-server.js.nft.json",
        "next-server.js.nft.json",
        "package.json",
        "prerender-manifest.json",
        "react-loadable-manifest.json",
        "required-server-files.js",
        "required-server-files.json",
        "routes-manifest.json",
        "trace",
        "trace-build",
    }
)
_STANDARD_NEXT_ENV = (
    b'/// <reference types="next" />\n'
    b'/// <reference types="next/image-types/global" />\n'
    b'import "./.next/types/routes.d.ts";\n'
    b'import "./.next/types/root-params.d.ts";\n'
    b"\n"
    b"// NOTE: This file should not be edited\n"
    b"// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.\n"
)
_REQUIRED_FILE_SUFFIXES = (
    "package.json",
    "routes-manifest.json",
    "server/pages-manifest.json",
    "build-manifest.json",
    "prerender-manifest.json",
    "server/functions-config-manifest.json",
    "server/middleware-manifest.json",
    "server/middleware-build-manifest.js",
    "server/middleware-react-loadable-manifest.js",
    "react-loadable-manifest.json",
    "server/app-paths-manifest.json",
    "app-path-routes-manifest.json",
    "server/server-reference-manifest.js",
    "server/server-reference-manifest.json",
    "server/prefetch-hints.json",
    "BUILD_ID",
    "server/next-font-manifest.js",
    "server/next-font-manifest.json",
    "required-server-files.json",
)
_MANDATORY_DIST_FILES = frozenset(
    {
        *_REQUIRED_FILE_SUFFIXES,
        "react-loadable-manifest.json",
        "server/middleware-react-loadable-manifest.js",
        "types/cache-life.d.ts",
        "types/root-params.d.ts",
        "types/routes.d.ts",
        "types/validator.ts",
        _VOICE_SERVER_DIST_FILE,
    }
)
_DOCUMENT_NAMES = frozenset(
    {
        "BUILD_ID",
        "package.json",
        "routes-manifest.json",
        "prerender-manifest.json",
        "required-server-files.json",
        "app-path-routes-manifest.json",
        "server/app-paths-manifest.json",
        "server/middleware-manifest.json",
    }
)


@dataclass(frozen=True, slots=True)
class _WorkspaceBuildInputBaseline:
    """Worker-local semantic baseline for the two allowed Next mutations."""

    include: tuple[str, ...]
    rest: tuple[object, ...]


def _new_workspace_build_input_baseline(
    next_env: bytes,
    tsconfig: bytes,
) -> _WorkspaceBuildInputBaseline:
    if (
        type(next_env) is not bytes
        or len(next_env) > _NEXT_ENV_BYTES
        or next_env != _STANDARD_NEXT_ENV
        or type(tsconfig) is not bytes
        or len(tsconfig) > _TSCONFIG_BYTES
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    document = _load_json(tsconfig, _TSCONFIG_BYTES)
    if type(document) is not dict:
        raise _WorkspaceFilesystemError(_FAILURE)
    include = document.get("include")
    if (
        type(include) is not list
        or any(type(value) is not str for value in include)
        or len(include) != len(set(include))
        or any(value.startswith(f"{_DIST_PARENT}/") for value in include)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    rest = {key: value for key, value in document.items() if key != "include"}
    return _WorkspaceBuildInputBaseline(tuple(include), _freeze_json(rest))


def _validate_workspace_build_inputs(
    baseline: _WorkspaceBuildInputBaseline,
    *,
    next_env: bytes,
    tsconfig: bytes,
    run_id: str,
) -> None:
    _validate_run_id(run_id)
    if (
        type(baseline) is not _WorkspaceBuildInputBaseline
        or type(next_env) is not bytes
        or len(next_env) > _NEXT_ENV_BYTES
        or next_env != _expected_next_env(run_id)
        or type(tsconfig) is not bytes
        or len(tsconfig) > _TSCONFIG_BYTES
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    document = _load_json(tsconfig, _TSCONFIG_BYTES)
    if type(document) is not dict:
        raise _WorkspaceFilesystemError(_FAILURE)
    include = document.get("include")
    dist = f"{_DIST_PARENT}/{run_id}"
    expected = (*baseline.include, f"{dist}/types/**/*.ts", f"{dist}/dev/types/**/*.ts")
    rest = {key: value for key, value in document.items() if key != "include"}
    if (
        type(include) is not list
        or tuple(include) != expected
        or _freeze_json(rest) != baseline.rest
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _validate_workspace_build_output_documents(
    documents: dict[str, bytes],
    *,
    directory_paths: frozenset[str],
    nonempty_paths: frozenset[str],
    regular_paths: frozenset[str],
    run_id: str,
    workspace: str,
) -> None:
    _validate_run_id(run_id)
    if (
        type(documents) is not dict
        or documents.keys() != _DOCUMENT_NAMES
        or any(type(key) is not str or type(value) is not bytes for key, value in documents.items())
        or type(directory_paths) is not frozenset
        or any(type(path) is not str for path in directory_paths)
        or type(nonempty_paths) is not frozenset
        or any(type(path) is not str for path in nonempty_paths)
        or not nonempty_paths.issubset(regular_paths)
        or type(regular_paths) is not frozenset
        or any(type(path) is not str for path in regular_paths)
        or not _MANDATORY_DIST_FILES.issubset(regular_paths)
        or any(
            "/" not in path and path not in _ALLOWED_ROOT_DIRECTORIES for path in directory_paths
        )
        or any("/" not in path and path not in _ALLOWED_ROOT_FILES for path in regular_paths)
        or type(workspace) is not str
        or not workspace.startswith("/")
        or "\x00" in workspace
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    if documents["package.json"] != b'{"type": "commonjs"}':
        raise _WorkspaceFilesystemError(_FAILURE)
    if _BUILD_ID.fullmatch(documents["BUILD_ID"]) is None:
        raise _WorkspaceFilesystemError(_FAILURE)
    build_id = documents["BUILD_ID"].decode("ascii")
    static_root = f"static/{build_id}"
    webpack_manifests = {
        f"{static_root}/_buildManifest.js",
        f"{static_root}/_ssgManifest.js",
    }
    if (
        static_root not in directory_paths
        or not webpack_manifests.issubset(regular_paths)
        or not webpack_manifests.issubset(nonempty_paths)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    routes = _require_object(_load_json(documents["routes-manifest.json"], _JSON_BYTES))
    app_paths = _require_object(_load_json(documents["app-path-routes-manifest.json"], _JSON_BYTES))
    server_paths = _require_object(
        _load_json(documents["server/app-paths-manifest.json"], _JSON_BYTES)
    )
    prerender = _require_object(_load_json(documents["prerender-manifest.json"], _JSON_BYTES))
    required = _require_object(_load_json(documents["required-server-files.json"], _JSON_BYTES))
    middleware = _require_object(
        _load_json(documents["server/middleware-manifest.json"], _JSON_BYTES)
    )
    _validate_routes(routes)
    if app_paths.get(_VOICE_APP_PATH) != _VOICE_ROUTE:
        raise _WorkspaceFilesystemError(_FAILURE)
    if server_paths.get(_VOICE_APP_PATH) != _VOICE_SERVER_MANIFEST_VALUE:
        raise _WorkspaceFilesystemError(_FAILURE)
    _validate_prerender(prerender)
    _validate_required_server(required, run_id=run_id, workspace=workspace)
    _validate_middleware(middleware)


def _validate_routes(document: dict[str, object]) -> None:
    if not (
        type(document.get("version")) is int
        and document["version"] == 3
        and document.get("pages404") is True
        and document.get("caseSensitive") is False
        and type(document.get("basePath")) is str
        and document["basePath"] == ""
        and type(document.get("appType")) is str
        and document["appType"] == "app"
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    static_routes = document.get("staticRoutes")
    dynamic_routes = document.get("dynamicRoutes")
    if type(static_routes) is not list or type(dynamic_routes) is not list:
        raise _WorkspaceFilesystemError(_FAILURE)
    matches = [
        route
        for route in static_routes
        if type(route) is dict and route.get("page") == _VOICE_ROUTE
    ]
    expected = {
        "page": _VOICE_ROUTE,
        "regex": _VOICE_ROUTE_PATTERN,
        "routeKeys": {},
        "namedRegex": _VOICE_ROUTE_PATTERN,
    }
    if matches != [expected] or any(
        type(route) is dict and route.get("page") == _VOICE_ROUTE for route in dynamic_routes
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _validate_prerender(document: dict[str, object]) -> None:
    routes = document.get("routes")
    dynamic = document.get("dynamicRoutes")
    if (
        type(document.get("version")) is not int
        or document["version"] != 4
        or type(routes) is not dict
        or type(dynamic) is not dict
        or _VOICE_ROUTE in routes
        or _VOICE_ROUTE in dynamic
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _validate_required_server(
    document: dict[str, object],
    *,
    run_id: str,
    workspace: str,
) -> None:
    dist = f"{_DIST_PARENT}/{run_id}"
    files = document.get("files")
    expected_files = tuple(f"{dist}/{suffix}" for suffix in _REQUIRED_FILE_SUFFIXES)
    config = document.get("config")
    if not (
        type(document.get("version")) is int
        and document["version"] == 1
        and type(document.get("appDir")) is str
        and document["appDir"] == workspace
        and type(document.get("relativeAppDir")) is str
        and document["relativeAppDir"] == ""
        and type(document.get("ignore")) is list
        and document["ignore"] == []
        and type(files) is list
        and tuple(files) == expected_files
        and type(config) is dict
        and type(config.get("distDir")) is str
        and config["distDir"] == dist
        and type(config.get("distDirRoot")) is str
        and config["distDirRoot"] == dist
        and type(config.get("configOrigin")) is str
        and config["configOrigin"] == "next.config.mjs"
        and type(config.get("configFileName")) is str
        and config["configFileName"] == "next.config.mjs"
        and config.get("typedRoutes") is False
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    if any(not _safe_workspace_relative(path) for path in files):
        raise _WorkspaceFilesystemError(_FAILURE)


def _validate_middleware(document: dict[str, object]) -> None:
    if not (
        type(document.get("version")) is int
        and document["version"] == 3
        and type(document.get("middleware")) is dict
        and document["middleware"] == {}
        and type(document.get("functions")) is dict
        and document["functions"] == {}
        and type(document.get("sortedMiddleware")) is list
        and document["sortedMiddleware"] == []
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _load_json(value: bytes, limit: int) -> object:
    if type(value) is not bytes or len(value) > limit:
        raise _WorkspaceFilesystemError(_FAILURE)

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise _WorkspaceFilesystemError(_FAILURE)
            result[key] = item
        return result

    def reject_constant(_value: str) -> None:
        raise _WorkspaceFilesystemError(_FAILURE)

    try:
        document = json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except _WorkspaceFilesystemError:
        raise
    except BaseException:
        raise _WorkspaceFilesystemError(_FAILURE) from None
    _validate_json_value(document, 0, [0])
    return document


def _validate_json_value(value: object, depth: int, members: list[int]) -> None:
    if depth > _JSON_DEPTH:
        raise _WorkspaceFilesystemError(_FAILURE)
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _WorkspaceFilesystemError(_FAILURE)
        return
    if type(value) is str:
        if len(value.encode("utf-8")) > _JSON_STRING_BYTES:
            raise _WorkspaceFilesystemError(_FAILURE)
        return
    if type(value) is list:
        members[0] += len(value)
        if members[0] > _JSON_MEMBERS:
            raise _WorkspaceFilesystemError(_FAILURE)
        for item in value:
            _validate_json_value(item, depth + 1, members)
        return
    if type(value) is dict:
        members[0] += len(value)
        if members[0] > _JSON_MEMBERS:
            raise _WorkspaceFilesystemError(_FAILURE)
        for key, item in value.items():
            _validate_json_value(key, depth + 1, members)
            _validate_json_value(item, depth + 1, members)
        return
    raise _WorkspaceFilesystemError(_FAILURE)


def _freeze_json(value: object) -> tuple[object, ...]:
    _validate_json_value(value, 0, [0])
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        return ("float", value)
    if type(value) is str:
        return ("str", value)
    if type(value) is list:
        return ("list", tuple(_freeze_json(item) for item in value))
    if type(value) is dict:
        return (
            "dict",
            tuple(sorted((key, _freeze_json(item)) for key, item in value.items())),
        )
    raise _WorkspaceFilesystemError(_FAILURE)


def _expected_next_env(run_id: str) -> bytes:
    dist = f"{_DIST_PARENT}/{run_id}"
    return (
        b'/// <reference types="next" />\n'
        b'/// <reference types="next/image-types/global" />\n'
        + f'import "./{dist}/types/routes.d.ts";\n'.encode()
        + f'import "./{dist}/types/root-params.d.ts";\n'.encode()
        + b"\n"
        + b"// NOTE: This file should not be edited\n"
        + b"// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.\n"
    )


def _require_object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _WorkspaceFilesystemError(_FAILURE)
    return value


def _safe_workspace_relative(value: object) -> bool:
    return bool(
        type(value) is str
        and value
        and not value.startswith("/")
        and "\\" not in value
        and "\x00" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _validate_run_id(run_id: object) -> None:
    if type(run_id) is not str or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", run_id) is None:
        raise _WorkspaceFilesystemError(_FAILURE)


__all__: list[str] = []

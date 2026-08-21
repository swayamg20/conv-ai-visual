"""Dormant, syntactic workspace-preparation values for relay B0.

This checkpoint performs no filesystem operation and makes no provenance,
canonical-path, permissions, copy, revalidation, or cleanup claim.  It only
preowns the immutable graph a later descriptor-relative workspace transaction
must consume.  In particular, these values cannot authorize a process spawn.
"""

from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _BUILD_ENVIRONMENT_NAMES,
    _FIXED_BUILD_ENVIRONMENT,
)

_REQUEST_TOKEN = object()
_AUTHORITY_TOKEN = object()
_OWNER_TOKEN = object()
_DESTINATION_TOKEN = object()
_RECEIPT_DESTINATION_TOKEN = object()
_WORKER_BUNDLE_DESTINATION_TOKEN = object()
_FAILURE = "Relay Linux build workspace preparation contract is invalid"
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_PATH_TYPE = type(Path("/"))
_RUN_PREFIX = "relay-linux-"
_WORKSPACE_NAME = "web-workspace"
_DIST_PARENT = ".next-voice-e2e"
_NEXT_CLI_RELATIVE = ("next", "dist", "bin", "next")
_SOURCE_ENTRIES = (
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
_SOURCE_DIRECTORY_ENTRIES = frozenset({"e2e", "src"})
_MAX_SOURCE_NODES = 4096
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_DEPTH = 32


class _RelayLinuxBuildWorkspaceContractError(RuntimeError):
    """The dormant syntactic workspace graph was inconsistent."""

    def __repr__(self) -> str:
        return "_RelayLinuxBuildWorkspaceContractError()"


class _WorkspaceWorkerBundleDestination:
    """Owner-preowned single-assignment slot for one later worker bundle."""

    __slots__ = (
        "_lock",
        "_owner_token",
        "_prepared_destination",
        "_request",
        "_value",
    )

    def __init__(
        self,
        token: object,
        *,
        request: _RelayLinuxBuildWorkspaceRequest,
        owner_token: object,
        prepared_destination: _WorkspacePreparationReceiptDestination,
    ) -> None:
        if (
            token is not _WORKER_BUNDLE_DESTINATION_TOKEN
            or owner_token is None
            or prepared_destination is None
        ):
            raise TypeError("Relay Linux workspace worker bundle slot is factory-owned")
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_prepared_destination", prepared_destination)
        object.__setattr__(self, "_value", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _publish(self, request: _RelayLinuxBuildWorkspaceRequest, value: object) -> object:
        if request is not self._request or value is None:
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
            _WorkspaceWorkerBundle,
        )

        if type(value) is not _WorkspaceWorkerBundle or not value._matches(
            self._owner_token,
            self._prepared_destination,
        ):
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        with self._lock:
            if self._value is None:
                object.__setattr__(self, "_value", value)
            return self._value

    def _read(self, request: _RelayLinuxBuildWorkspaceRequest) -> object | None:
        if request is not self._request:
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        with self._lock:
            return self._value

    def _read_before(
        self,
        request: _RelayLinuxBuildWorkspaceRequest,
        deadline: float,
    ) -> tuple[object | None, bool]:
        if (
            request is not self._request
            or type(deadline) is not float
            or not math.isfinite(deadline)
        ):
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        remaining = max(0.0, deadline - time.monotonic())
        acquired = (
            self._lock.acquire(blocking=False)
            if remaining <= 0.0
            else self._lock.acquire(timeout=remaining)
        )
        if not acquired:
            return None, False
        try:
            return self._value, True
        finally:
            self._lock.release()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerBundleDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker bundle slot is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker bundle slot cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker bundle slot cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker bundle slot cannot be serialized")


class _RelayLinuxBuildWorkspaceRequest:
    """Immutable intended paths and positive policy, not provenance."""

    __slots__ = (
        "_dist_path",
        "_environment",
        "_next_cli",
        "_node",
        "_node_modules",
        "_run_id",
        "_run_parent",
        "_run_root",
        "_source_root",
        "_workspace",
    )

    def __init__(
        self,
        token: object,
        *,
        source_root: Path,
        run_parent: Path,
        node: Path,
        run_id: str,
        environment: tuple[tuple[str, str], ...],
    ) -> None:
        if token is not _REQUEST_TOKEN:
            raise TypeError("Relay Linux build workspace request is factory-owned")
        run_root = run_parent / f"{_RUN_PREFIX}{run_id}"
        workspace = run_root / _WORKSPACE_NAME
        node_modules = source_root / "node_modules"
        next_cli = node_modules.joinpath(*_NEXT_CLI_RELATIVE)
        dist_path = workspace / _DIST_PARENT / run_id
        if not all(
            _valid_absolute_path(path)
            for path in (
                source_root,
                run_parent,
                node,
                run_root,
                workspace,
                node_modules,
                next_cli,
                dist_path,
            )
        ):
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        object.__setattr__(self, "_source_root", source_root)
        object.__setattr__(self, "_run_parent", run_parent)
        object.__setattr__(self, "_run_root", run_root)
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_node_modules", node_modules)
        object.__setattr__(self, "_next_cli", next_cli)
        object.__setattr__(self, "_dist_path", dist_path)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_environment", environment)

    def _matches(self, candidate: object) -> bool:
        return candidate is self

    def _environment_values(self) -> dict[str, str]:
        """Return only the inert replacement-environment policy."""

        return dict(self._environment)

    def _copy_policy(self) -> tuple[tuple[str, ...], frozenset[str], int, int, int]:
        return (
            _SOURCE_ENTRIES,
            _SOURCE_DIRECTORY_ENTRIES,
            _MAX_SOURCE_NODES,
            _MAX_SOURCE_BYTES,
            _MAX_SOURCE_DEPTH,
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildWorkspaceRequest()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build workspace request is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build workspace request cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build workspace request cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build workspace request cannot be serialized")


class _RelayLinuxBuildWorkspaceCleanupAuthority:
    """Opaque preowned key; this checkpoint exposes no cleanup operation."""

    __slots__ = ("_authentic", "_key", "_request")

    def __init__(
        self,
        token: object,
        *,
        key: object,
        request: _RelayLinuxBuildWorkspaceRequest,
    ) -> None:
        if (
            token is not _AUTHORITY_TOKEN
            or key is None
            or type(request) is not _RelayLinuxBuildWorkspaceRequest
        ):
            raise TypeError("Relay Linux build workspace cleanup authority is factory-owned")
        object.__setattr__(self, "_authentic", _AUTHORITY_TOKEN)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_request", request)

    def _matches(self, request: _RelayLinuxBuildWorkspaceRequest) -> bool:
        return bool(self._authentic is _AUTHORITY_TOKEN and self._request is request)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildWorkspaceCleanupAuthority()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build workspace cleanup authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build workspace cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build workspace cleanup authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build workspace cleanup authority cannot be serialized")


class _WorkspacePreparationReceiptDestination:
    """Caller-preowned empty slot; there is deliberately no publisher yet."""

    __slots__ = ("_lock", "_receipt", "_request")

    def __init__(
        self,
        token: object,
        *,
        request: _RelayLinuxBuildWorkspaceRequest,
    ) -> None:
        if token is not _RECEIPT_DESTINATION_TOKEN:
            raise TypeError("Relay Linux build workspace receipt destination is factory-owned")
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_receipt", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _read(self, request: _RelayLinuxBuildWorkspaceRequest) -> None:
        if request is not self._request:
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        with self._lock:
            return self._receipt

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspacePreparationReceiptDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build workspace receipt destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build workspace receipt destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build workspace receipt destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build workspace receipt destination cannot be serialized")


class _RelayLinuxBuildWorkspaceOwner:
    """Preowned graph root; it owns no filesystem node in this checkpoint."""

    __slots__ = (
        "_cleanup_authority",
        "_receipt_destination",
        "_request",
        "_worker_bundle_destination",
    )

    def __init__(
        self,
        token: object,
        *,
        request: _RelayLinuxBuildWorkspaceRequest,
    ) -> None:
        if token is not _OWNER_TOKEN or type(request) is not _RelayLinuxBuildWorkspaceRequest:
            raise TypeError("Relay Linux build workspace owner is factory-owned")
        object.__setattr__(self, "_request", request)
        object.__setattr__(
            self,
            "_cleanup_authority",
            _RelayLinuxBuildWorkspaceCleanupAuthority(
                _AUTHORITY_TOKEN,
                key=object(),
                request=request,
            ),
        )
        object.__setattr__(
            self,
            "_receipt_destination",
            _WorkspacePreparationReceiptDestination(
                _RECEIPT_DESTINATION_TOKEN,
                request=request,
            ),
        )
        object.__setattr__(
            self,
            "_worker_bundle_destination",
            _WorkspaceWorkerBundleDestination(
                _WORKER_BUNDLE_DESTINATION_TOKEN,
                request=request,
                owner_token=self._cleanup_authority._key,
                prepared_destination=self._receipt_destination,
            ),
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildWorkspaceOwner()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build workspace owner is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build workspace owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build workspace owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build workspace owner cannot be serialized")


class _RelayLinuxBuildWorkspaceDestination:
    """Caller-preowned immutable destination for one exact request graph."""

    __slots__ = ("_owner", "_request")

    def __init__(
        self,
        token: object,
        *,
        request: _RelayLinuxBuildWorkspaceRequest,
    ) -> None:
        if token is not _DESTINATION_TOKEN:
            raise TypeError("Relay Linux build workspace destination is factory-owned")
        object.__setattr__(self, "_request", request)
        object.__setattr__(
            self,
            "_owner",
            _RelayLinuxBuildWorkspaceOwner(_OWNER_TOKEN, request=request),
        )

    def _read(self, request: _RelayLinuxBuildWorkspaceRequest) -> _RelayLinuxBuildWorkspaceOwner:
        if request is not self._request:
            raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
        return self._owner

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildWorkspaceDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build workspace destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build workspace destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build workspace destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build workspace destination cannot be serialized")


def _new_relay_linux_build_workspace_destination(
    *,
    source_root: Path,
    run_parent: Path,
    node: Path,
    run_id: str,
) -> _RelayLinuxBuildWorkspaceDestination:
    if (
        type(run_id) is not str
        or _RUN_ID.fullmatch(run_id) is None
        or not all(_valid_absolute_path(path) for path in (source_root, run_parent, node))
    ):
        raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
    environment = {
        **_FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f"{_DIST_PARENT}/{run_id}",
    }
    if environment.keys() != _BUILD_ENVIRONMENT_NAMES:
        raise _RelayLinuxBuildWorkspaceContractError(_FAILURE)
    request = _RelayLinuxBuildWorkspaceRequest(
        _REQUEST_TOKEN,
        source_root=source_root,
        run_parent=run_parent,
        node=node,
        run_id=run_id,
        environment=tuple(sorted(environment.items())),
    )
    return _RelayLinuxBuildWorkspaceDestination(
        _DESTINATION_TOKEN,
        request=request,
    )


def _valid_absolute_path(path: object) -> bool:
    return bool(
        type(path) is _PATH_TYPE
        and path.is_absolute()
        and ".." not in path.parts
        and "\x00" not in str(path)
    )


__all__: list[str] = []

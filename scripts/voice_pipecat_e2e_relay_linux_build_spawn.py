"""Dormant syntactic request and raw registration slot for a future B0 build."""

from __future__ import annotations

import re
import threading
from pathlib import Path

_SPEC_TOKEN = object()
_DESTINATION_TOKEN = object()
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_FAILURE = "Relay Linux build spawn contract is invalid"
_BUILD_ENVIRONMENT_NAMES = frozenset(
    {
        "CI",
        "LANG",
        "LC_ALL",
        "MURMUR_E2E_MODE",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_FIREBASE_API_KEY",
        "NEXT_PUBLIC_FIREBASE_APP_ID",
        "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
        "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
        "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET",
        "NEXT_PUBLIC_VOICE_RUNTIME",
        "NEXT_TELEMETRY_DISABLED",
        "NO_COLOR",
        "NO_PROXY",
        "PYTHON_DOTENV_DISABLED",
        "VOICE_E2E_NEXT_DIST_DIR",
        "no_proxy",
    }
)
_FIXED_BUILD_ENVIRONMENT = {
    "CI": "1",
    "LANG": "C",
    "LC_ALL": "C",
    "MURMUR_E2E_MODE": "1",
    "NEXT_PUBLIC_API_URL": "http://127.0.0.1:8101",
    "NEXT_PUBLIC_FIREBASE_API_KEY": "voice-pipecat-e2e-test-key",
    "NEXT_PUBLIC_FIREBASE_APP_ID": "1:1234567890:web:voice-pipecat-e2e",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": "voice-pipecat-e2e.firebaseapp.com",
    "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID": "1234567890",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "voice-pipecat-e2e",
    "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET": "voice-pipecat-e2e.appspot.com",
    "NEXT_PUBLIC_VOICE_RUNTIME": "voice_v2",
    "NEXT_TELEMETRY_DISABLED": "1",
    "NO_COLOR": "1",
    "NO_PROXY": "127.0.0.1,localhost,::1",
    "PYTHON_DOTENV_DISABLED": "1",
    "no_proxy": "127.0.0.1,localhost,::1",
}


class _RelayLinuxBuildSpawnError(RuntimeError):
    """The dormant syntactic spawn contract was inconsistent."""

    def __repr__(self) -> str:
        return "_RelayLinuxBuildSpawnError()"


class _RelayLinuxBuildSpec:
    """Factory-owned exact Node/Next build request; no provenance claim."""

    __slots__ = ("_argv", "_cwd", "_environment", "_run_id")

    def __init__(
        self,
        token: object,
        *,
        node: Path,
        next_cli: Path,
        workspace: Path,
        environment: tuple[tuple[str, str], ...],
        run_id: str,
    ) -> None:
        if token is not _SPEC_TOKEN:
            raise TypeError("Relay Linux build spec is factory-owned")
        object.__setattr__(
            self,
            "_argv",
            (str(node), str(next_cli), "build", "--webpack"),
        )
        object.__setattr__(self, "_cwd", workspace)
        object.__setattr__(self, "_environment", environment)
        object.__setattr__(self, "_run_id", run_id)

    def _spawn_values(self) -> tuple[tuple[str, ...], Path, dict[str, str]]:
        return self._argv, self._cwd, dict(self._environment)

    def _matches_destination(self, destination: object) -> bool:
        return bool(type(destination) is _RawBuildProcessDestination and destination._spec is self)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxBuildSpec()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build spec is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build spec cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build spec cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build spec cannot be serialized")


class _RawBuildProcessDestination:
    """Caller-preowned single-assignment slot that accepts a pre-init Popen."""

    __slots__ = ("_lock", "_process", "_spec")

    def __init__(self, token: object, *, spec: _RelayLinuxBuildSpec) -> None:
        if token is not _DESTINATION_TOKEN or type(spec) is not _RelayLinuxBuildSpec:
            raise TypeError("Relay Linux raw build destination is factory-owned")
        self._spec = spec
        self._process: object | None = None
        self._lock = threading.Lock()

    def publish(self, process: object) -> None:
        """Retain identity only; a pre-init Popen has no trustworthy fields yet."""

        if process is None:
            raise TypeError("Relay Linux raw build publication is invalid")
        with self._lock:
            if self._process is None:
                self._process = process
            elif self._process is not process:
                raise TypeError("Relay Linux raw build publication is invalid")

    def _read(self, spec: _RelayLinuxBuildSpec) -> object | None:
        if spec is not self._spec:
            raise _RelayLinuxBuildSpawnError(_FAILURE)
        with self._lock:
            return self._process

    def _clear(self, spec: _RelayLinuxBuildSpec, process: object) -> bool:
        if spec is not self._spec or process is None:
            raise _RelayLinuxBuildSpawnError(_FAILURE)
        with self._lock:
            if self._process is process:
                self._process = None
            elif self._process is not None:
                raise _RelayLinuxBuildSpawnError(_FAILURE)
            return self._process is None

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RawBuildProcessDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay Linux raw build destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux raw build destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux raw build destination cannot be serialized")


def _new_relay_linux_build_spec(
    *,
    node: Path,
    next_cli: Path,
    workspace: Path,
    run_id: str,
    environment: dict[str, str],
) -> _RelayLinuxBuildSpec:
    if (
        not isinstance(node, Path)
        or not isinstance(next_cli, Path)
        or not isinstance(workspace, Path)
        or not all(path.is_absolute() for path in (node, next_cli, workspace))
        or type(run_id) is not str
        or not _RUN_ID.fullmatch(run_id)
        or type(environment) is not dict
        or environment.keys() != _BUILD_ENVIRONMENT_NAMES
        or any(type(key) is not str or type(value) is not str for key, value in environment.items())
        or any("\x00" in value for value in (*environment.keys(), *environment.values()))
        or any(environment.get(key) != value for key, value in _FIXED_BUILD_ENVIRONMENT.items())
        or environment.get("VOICE_E2E_NEXT_DIST_DIR") != f".next-voice-e2e/{run_id}"
    ):
        raise _RelayLinuxBuildSpawnError(_FAILURE)
    return _RelayLinuxBuildSpec(
        _SPEC_TOKEN,
        node=node,
        next_cli=next_cli,
        workspace=workspace,
        environment=tuple(sorted(environment.items())),
        run_id=run_id,
    )


def _new_raw_build_process_destination(
    spec: _RelayLinuxBuildSpec,
) -> _RawBuildProcessDestination:
    if type(spec) is not _RelayLinuxBuildSpec:
        raise _RelayLinuxBuildSpawnError(_FAILURE)
    return _RawBuildProcessDestination(_DESTINATION_TOKEN, spec=spec)


__all__: list[str] = []

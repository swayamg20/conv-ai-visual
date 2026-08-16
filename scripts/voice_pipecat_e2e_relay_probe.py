"""Pre-owned identity and process environments for the relay B0 probe.

This module intentionally performs no Docker, Coturn, application, browser, or
network action.  It provides the high-level caller with one clean-source run
owner before effects, then separates backend authorization (needed before the
relay prebootstrap) from browser authorization (allowed only after the exact
running container and its path/topology-bound OpenSSL readiness are proven).
"""

from __future__ import annotations

import re
import threading
import uuid

from scripts.voice_pipecat_e2e_coturn import PipecatE2ENetworkMode
from scripts.voice_pipecat_e2e_coturn_docker_container import ValidatedRunningContainer
from scripts.voice_pipecat_e2e_coturn_host import CoturnRuntimePaths
from scripts.voice_pipecat_e2e_coturn_runtime_tls import RuntimeTlsMaterial
from scripts.voice_pipecat_e2e_coturn_runtime_values import control_signal, raise_control
from scripts.voice_pipecat_e2e_coturn_tls_readiness import OpenSslReadinessReceipt
from scripts.voice_pipecat_e2e_stack import (
    StackPaths,
    _read_source_provenance,
    _validate_source_provenance,
    build_environment,
    build_web_environment,
)

_RUN_TOKEN = object()
_SOURCE_TOKEN = object()
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_BACKEND_FAILURE = "Relay probe backend environment is unavailable"
_WEB_FAILURE = "Relay probe web environment is unavailable"
_BROWSER_FAILURE = "Relay probe browser environment is unavailable"
_BACKEND_AUTHORIZATION_FAILURE = "Relay probe backend authorization failed"
_BROWSER_AUTHORIZATION_FAILURE = "Relay probe browser authorization failed"
_WEB_ENVIRONMENT_NAMES = frozenset(
    {
        "CI",
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
        "VOICE_E2E_API_URL",
        "VOICE_E2E_CALL_ID",
        "VOICE_E2E_NETWORK",
        "VOICE_E2E_NEXT_DIST_DIR",
        "VOICE_E2E_WEB_URL",
        "no_proxy",
    }
)
_BROWSER_ENVIRONMENT_NAMES = frozenset(
    {
        "CI",
        "NO_COLOR",
        "NO_PROXY",
        "VOICE_E2E_API_URL",
        "VOICE_E2E_ARTIFACT_DIR",
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE",
        "VOICE_E2E_CALL_ID",
        "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4",
        "VOICE_E2E_COTURN_SPKI_SHA256_B64",
        "VOICE_E2E_NETWORK",
        "VOICE_E2E_RESULT_PATH",
        "VOICE_E2E_WEB_URL",
        "no_proxy",
    }
)


class RelayProbeError(RuntimeError):
    """The non-qualifying relay probe could not preserve its exact contract."""


class RelayProbeSource:
    """Factory-owned clean Git snapshot used to bind the B0 result."""

    __slots__ = ("_commit_sha",)

    def __init__(self, token: object, *, commit_sha: str) -> None:
        authorized = token is _SOURCE_TOKEN
        token = None
        if not authorized or type(commit_sha) is not str or not _SOURCE_SHA.fullmatch(commit_sha):
            raise TypeError("Relay probe source is factory-owned")
        object.__setattr__(self, "_commit_sha", commit_sha)

    @property
    def commit_sha(self) -> str:
        return self._commit_sha

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay probe source is immutable")

    def __repr__(self) -> str:
        return "RelayProbeSource()"

    def __copy__(self) -> None:
        raise TypeError("Relay probe source cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe source cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe source cannot be serialized")


class RelayProbeRun:
    """Caller-preowned identity plus staged backend/browser authorization sink."""

    __slots__ = (
        "_backend_environment",
        "_browser_environment",
        "_call_id",
        "_lock",
        "_owner_token",
        "_readiness",
        "_running",
        "_runtime_paths",
        "_source",
        "_stack_paths",
        "_state",
        "_tls_material",
        "_web_environment",
    )

    def __init__(
        self,
        token: object,
        *,
        stack_paths: StackPaths,
        runtime_paths: CoturnRuntimePaths,
        source: RelayProbeSource,
        call_id: str,
    ) -> None:
        if token is not _RUN_TOKEN:
            raise TypeError("Relay probe run is factory-owned")
        self._stack_paths = stack_paths
        self._runtime_paths = runtime_paths
        self._source = source
        self._call_id = call_id
        self._owner_token = object()
        self._running: ValidatedRunningContainer | None = None
        self._tls_material: RuntimeTlsMaterial | None = None
        self._readiness: OpenSslReadinessReceipt | None = None
        self._backend_environment: tuple[tuple[str, str], ...] | None = None
        self._web_environment: tuple[tuple[str, str], ...] | None = None
        self._browser_environment: tuple[tuple[str, str], ...] | None = None
        self._state = "created"
        self._lock = threading.Lock()

    @property
    def source_sha(self) -> str:
        return self._source.commit_sha

    def _claim_backend_material(self, tls_material: RuntimeTlsMaterial) -> None:
        with self._lock:
            if self._state == "created":
                if self._tls_material is not None and self._tls_material is not tls_material:
                    raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE)
                if self._tls_material is None:
                    self._tls_material = tls_material
                self._state = "backend-authorizing"
                return
            if not (
                self._state in {"backend-authorizing", "backend-ready", "browser-ready"}
                and self._tls_material is tls_material
            ):
                raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE)

    def _publish_backend_authorization(
        self,
        *,
        tls_material: RuntimeTlsMaterial,
        backend_environment: dict[str, str],
        web_environment: dict[str, str],
    ) -> None:
        frozen_backend = _freeze_environment(backend_environment)
        frozen_web = _freeze_environment(web_environment)
        with self._lock:
            if self._state == "backend-authorizing" and self._tls_material is tls_material:
                self._backend_environment = frozen_backend
                self._web_environment = frozen_web
                self._state = "backend-ready"
                return
            if not (
                self._state in {"backend-ready", "browser-ready"}
                and self._tls_material is tls_material
                and self._backend_environment == frozen_backend
                and self._web_environment == frozen_web
            ):
                raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE)

    def _publish_browser_authorization(
        self,
        *,
        running: ValidatedRunningContainer,
        tls_material: RuntimeTlsMaterial,
        readiness: OpenSslReadinessReceipt,
        environment: dict[str, str],
    ) -> None:
        frozen = _freeze_environment(environment)
        with self._lock:
            if (
                self._state == "backend-ready"
                and self._tls_material is tls_material
                and self._backend_environment is not None
            ):
                self._running = running
                self._readiness = readiness
                self._browser_environment = frozen
                self._state = "browser-ready"
                return
            if not (
                self._state == "browser-ready"
                and self._running is running
                and self._tls_material is tls_material
                and self._readiness is readiness
                and self._browser_environment == frozen
            ):
                raise RelayProbeError(_BROWSER_AUTHORIZATION_FAILURE)

    def _project_environment(self, target: str) -> dict[str, str]:
        with self._lock:
            tls_material = self._tls_material
            if (
                target == "backend"
                and self._state in {"backend-ready", "browser-ready"}
                and type(tls_material) is RuntimeTlsMaterial
                and tls_material._matches_probe_owner(self._owner_token)
                and tls_material._matches_generated(
                    self._runtime_paths,
                    tls_material._topology,
                )
            ):
                frozen = self._backend_environment
            elif (
                target == "web"
                and self._state in {"backend-ready", "browser-ready"}
                and type(tls_material) is RuntimeTlsMaterial
                and tls_material._matches_probe_owner(self._owner_token)
                and tls_material._matches_generated(
                    self._runtime_paths,
                    tls_material._topology,
                )
            ):
                frozen = self._web_environment
            elif target == "browser":
                running = self._running
                readiness = self._readiness
                if (
                    self._state != "browser-ready"
                    or type(running) is not ValidatedRunningContainer
                    or type(tls_material) is not RuntimeTlsMaterial
                    or type(readiness) is not OpenSslReadinessReceipt
                    or not tls_material._matches_probe_owner(self._owner_token)
                    or not tls_material._matches_readiness(running, readiness)
                ):
                    raise RelayProbeError(_BROWSER_FAILURE)
                frozen = self._browser_environment
            else:
                raise RelayProbeError(_projection_failure(target))
            if frozen is None:
                raise RelayProbeError(_projection_failure(target))
            return dict(frozen)

    def __repr__(self) -> str:
        return "RelayProbeRun()"

    def __copy__(self) -> None:
        raise TypeError("Relay probe run cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe run cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe run cannot be serialized")


def capture_relay_probe_source() -> RelayProbeSource:
    """Read one clean source snapshot through the existing guarded Git boundary."""

    source: RelayProbeSource | None = None
    control = None
    try:
        observed = _validate_source_provenance(_read_source_provenance())
        source = RelayProbeSource(
            _SOURCE_TOKEN,
            commit_sha=observed["commit_sha"],  # type: ignore[arg-type]
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    observed = None
    if control is not None:
        source = None
        raise_control(control)
    if source is None:
        raise RelayProbeError("Relay probe source is unavailable") from None
    return source


def new_relay_probe_run(
    *,
    runtime_paths: CoturnRuntimePaths,
    source: RelayProbeSource,
) -> RelayProbeRun:
    """Create the harmless run/call identity before any relay side effect."""

    run: RelayProbeRun | None = None
    control = None
    try:
        if type(runtime_paths) is not CoturnRuntimePaths or type(source) is not RelayProbeSource:
            raise RelayProbeError("Relay probe run is invalid")
        stack_paths = _derive_stack_paths(runtime_paths)
        call_id = str(uuid.uuid4())
        if uuid.UUID(call_id).version != 4:
            raise RelayProbeError("Relay probe run is invalid")
        run = RelayProbeRun(
            _RUN_TOKEN,
            stack_paths=stack_paths,
            runtime_paths=runtime_paths,
            source=source,
            call_id=call_id,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    runtime_paths = source = stack_paths = None  # type: ignore[assignment]
    call_id = ""
    if control is not None:
        run = None
        raise_control(control)
    if run is None:
        raise RelayProbeError("Relay probe run is invalid") from None
    return run


def authorize_relay_backend(
    run: RelayProbeRun,
    *,
    tls_material: RuntimeTlsMaterial,
) -> None:
    """Authorize the guarded backend before prebootstrap and Coturn start."""

    control = None
    failed = False
    backend_environment: dict[str, str] | None = None
    web_environment: dict[str, str] | None = None
    try:
        if (
            type(run) is not RelayProbeRun
            or type(tls_material) is not RuntimeTlsMaterial
            or not tls_material._matches_generated(
                run._runtime_paths,
                tls_material._topology,
            )
        ):
            raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE)
        run._claim_backend_material(tls_material)
        tls_material._bind_probe_owner(run._owner_token)
        backend_environment = _relay_backend_environment(run)
        web_environment = _relay_web_environment(run)
        run._publish_backend_authorization(
            tls_material=tls_material,
            backend_environment=backend_environment,
            web_environment=web_environment,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    backend_environment = web_environment = None
    tls_material = run = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if failed:
        raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE) from None


def authorize_relay_browser(
    run: RelayProbeRun,
    *,
    running: ValidatedRunningContainer,
    tls_material: RuntimeTlsMaterial,
    readiness: OpenSslReadinessReceipt,
) -> None:
    """Authorize browser projection only after exact runtime TLS readiness."""

    control = None
    failed = False
    environment: dict[str, str] | None = None
    try:
        if (
            type(run) is not RelayProbeRun
            or type(running) is not ValidatedRunningContainer
            or type(tls_material) is not RuntimeTlsMaterial
            or type(readiness) is not OpenSslReadinessReceipt
            or not tls_material._matches_probe_owner(run._owner_token)
            or not tls_material._matches_readiness(running, readiness)
            or running.authority.plan.paths != run._runtime_paths
            or running.authority.plan.network.authority.plan.topology != tls_material._topology
        ):
            raise RelayProbeError(_BROWSER_AUTHORIZATION_FAILURE)
        environment = _relay_browser_environment(
            run,
            running=running,
            tls_material=tls_material,
        )
        run._publish_browser_authorization(
            running=running,
            tls_material=tls_material,
            readiness=readiness,
            environment=environment,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    environment = None
    running = tls_material = readiness = run = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if failed:
        raise RelayProbeError(_BROWSER_AUTHORIZATION_FAILURE) from None


def replacement_relay_backend_environment(run: RelayProbeRun) -> dict[str, str]:
    """Return one clean backend environment from the same-run aggregate."""

    return _project_public_environment(run, "backend")


def replacement_relay_web_environment(run: RelayProbeRun) -> dict[str, str]:
    """Return one exact web build/server environment from the aggregate."""

    return _project_public_environment(run, "web")


def replacement_relay_playwright_environment(run: RelayProbeRun) -> dict[str, str]:
    """Return one Playwright-only environment from the same-run aggregate."""

    return _project_public_environment(run, "browser")


def _project_public_environment(run: RelayProbeRun, target: str) -> dict[str, str]:
    environment: dict[str, str] | None = None
    control = None
    try:
        if type(run) is not RelayProbeRun or target not in {"backend", "web", "browser"}:
            raise RelayProbeError(_projection_failure(target))
        environment = run._project_environment(target)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    run = None  # type: ignore[assignment]
    if control is not None:
        environment = None
        raise_control(control)
    if environment is None:
        raise RelayProbeError(_projection_failure(target)) from None
    return environment


def _relay_web_environment(run: RelayProbeRun) -> dict[str, str]:
    candidate = build_web_environment(run._stack_paths, {})
    candidate.update(
        {
            "VOICE_E2E_CALL_ID": run._call_id,
            "VOICE_E2E_NETWORK": PipecatE2ENetworkMode.RELAY_TLS.value,
        }
    )
    environment = {name: candidate[name] for name in _WEB_ENVIRONMENT_NAMES}
    candidate = {}
    if environment.keys() != _WEB_ENVIRONMENT_NAMES:
        raise RelayProbeError(_BACKEND_AUTHORIZATION_FAILURE)
    return environment


def _relay_backend_environment(run: RelayProbeRun) -> dict[str, str]:
    environment = build_environment(
        run._stack_paths,
        {},
        network=PipecatE2ENetworkMode.DIRECT,
    )
    environment.update(
        {
            "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE": str(run._runtime_paths.contract.config),
            "MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID": run._call_id,
            "MURMUR_PIPECAT_E2E_NETWORK": PipecatE2ENetworkMode.RELAY_TLS.value,
            "SSL_CERT_FILE": str(run._runtime_paths.contract.cert),
        }
    )
    return environment


def _relay_browser_environment(
    run: RelayProbeRun,
    *,
    running: ValidatedRunningContainer,
    tls_material: RuntimeTlsMaterial,
) -> dict[str, str]:
    web = build_web_environment(run._stack_paths, {})
    environment = {
        "CI": web["CI"],
        "NO_COLOR": web["NO_COLOR"],
        "NO_PROXY": web["NO_PROXY"],
        "VOICE_E2E_API_URL": web["VOICE_E2E_API_URL"],
        "VOICE_E2E_ARTIFACT_DIR": web["VOICE_E2E_ARTIFACT_DIR"],
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE": web["VOICE_E2E_BROWSER_AUDIO_FIXTURE"],
        "VOICE_E2E_CALL_ID": run._call_id,
        "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4": str(
            running.authority.plan.network.authority.plan.topology.gateway
        ),
        "VOICE_E2E_COTURN_SPKI_SHA256_B64": tls_material.chromium_spki_sha256_b64,
        "VOICE_E2E_NETWORK": PipecatE2ENetworkMode.RELAY_TLS.value,
        "VOICE_E2E_RESULT_PATH": web["VOICE_E2E_RESULT_PATH"],
        "VOICE_E2E_WEB_URL": web["VOICE_E2E_WEB_URL"],
        "no_proxy": web["no_proxy"],
    }
    if environment.keys() != _BROWSER_ENVIRONMENT_NAMES:
        raise RelayProbeError(_BROWSER_AUTHORIZATION_FAILURE)
    return environment


def _projection_failure(target: str) -> str:
    if target == "backend":
        return _BACKEND_FAILURE
    if target == "web":
        return _WEB_FAILURE
    return _BROWSER_FAILURE


def _derive_stack_paths(runtime_paths: CoturnRuntimePaths) -> StackPaths:
    run_id = runtime_paths.contract.run_id
    run_dir = runtime_paths.contract.run_dir
    if not run_dir.is_absolute() or run_dir.name != run_id:
        raise RelayProbeError("Relay probe run is invalid")
    return StackPaths(
        run_id=run_id,
        run_dir=run_dir,
        database=run_dir / "murmur.db",
        evidence=run_dir / "pipecat-evidence.jsonl",
        server_log=run_dir / "pipecat-asgi.log",
        proof=run_dir / "backend-checkpoint.json",
    )


def _freeze_environment(environment: object) -> tuple[tuple[str, str], ...]:
    if type(environment) is not dict or not all(
        type(key) is str and type(value) is str for key, value in environment.items()
    ):
        raise RelayProbeError(_BROWSER_AUTHORIZATION_FAILURE)
    return tuple(sorted(environment.items()))


__all__ = [
    "RelayProbeError",
    "RelayProbeRun",
    "RelayProbeSource",
    "authorize_relay_backend",
    "authorize_relay_browser",
    "capture_relay_probe_source",
    "new_relay_probe_run",
    "replacement_relay_backend_environment",
    "replacement_relay_playwright_environment",
    "replacement_relay_web_environment",
]

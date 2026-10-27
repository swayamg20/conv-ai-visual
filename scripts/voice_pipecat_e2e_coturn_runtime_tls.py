"""Pre-owned path-bound TLS readiness and post-absence cleanup."""

from __future__ import annotations

import threading
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerCleanupAuthority,
    ValidatedRunningContainer,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandResult,
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
)
from scripts.voice_pipecat_e2e_coturn_runtime_lifecycle import ContainerAbsenceReceipt
from scripts.voice_pipecat_e2e_coturn_runtime_readiness import RuntimeReadinessBudget
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    CoturnTlsCleanupRequired,
    CoturnTlsPrivateCleanupRequired,
    OpenSslReadinessReceipt,
    TlsMaterialGenerationSlot,
    build_openssl_readiness_request,
    cleanup_tls_material_generation_slot,
    generate_tls_and_config_material_into_slot,
    new_tls_material_generation_slot,
    validate_openssl_readiness_result,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
)

_TLS_TOKEN = object()
_USE_REVOKED = object()
_TLS_RECOVERY_TYPES = {
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
}


class CoturnRuntimeTlsCleanupRequired(CoturnRuntimeError):
    """Fixed retry signal retaining only the redacted runtime aggregate."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: RuntimeTlsMaterial) -> None:
        super().__init__("Runtime TLS cleanup requires retry")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> RuntimeTlsMaterial:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnRuntimeTlsCleanupRequired('Runtime TLS cleanup requires retry')"


class RuntimeTlsMaterial:
    """Caller-preowned aggregate for one exact run/path/topology TLS lifetime."""

    __slots__ = (
        "_cleanup_lock",
        "_container_id",
        "_lock",
        "_openssl_readiness",
        "_paths",
        "_probe_owner",
        "_slot",
        "_state",
        "_topology",
    )

    def __init__(
        self,
        token: object,
        *,
        paths: CoturnRuntimePaths,
        topology: CoturnBridgeTopology,
        slot: TlsMaterialGenerationSlot,
    ) -> None:
        if token is not _TLS_TOKEN:
            raise TypeError("Runtime TLS material is factory-owned")
        self._paths = paths
        self._topology = topology
        self._slot = slot
        self._container_id: str | None = None
        self._openssl_readiness: OpenSslReadinessReceipt | None = None
        self._probe_owner: object | None = None
        self._state = "empty"
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()

    @property
    def certificate_sha256(self) -> str:
        return self._retained_slot().certificate_sha256

    @property
    def chromium_spki_sha256_b64(self) -> str:
        return self._retained_slot().chromium_spki_sha256_b64

    @property
    def not_before(self) -> datetime:
        return self._retained_slot().not_before

    @property
    def not_after(self) -> datetime:
        return self._retained_slot().not_after

    @property
    def cleanup_complete(self) -> bool:
        with self._lock:
            return self._state == "cleaned"

    def _retained_slot(self) -> TlsMaterialGenerationSlot:
        with self._lock:
            if self._state not in {"generated", "bound"} or self._probe_owner is _USE_REVOKED:
                raise CoturnRuntimeError("Runtime TLS material is unavailable")
            return self._slot

    def _begin_generation(
        self,
        paths: CoturnRuntimePaths,
        topology: CoturnBridgeTopology,
    ) -> TlsMaterialGenerationSlot:
        with self._lock:
            if self._state != "empty" or paths != self._paths or topology != self._topology:
                raise CoturnRuntimeError("Runtime TLS material is invalid")
            self._state = "generating"
            return self._slot

    def _generation_failed(self) -> bool:
        with self._lock:
            if self._state in {"generating", "generated"}:
                self._state = "generation-failed"
            return self._state == "generation-failed"

    def _generation_complete(self) -> None:
        if self._slot.has_material is not True:
            raise CoturnRuntimeError("Runtime TLS material generation failed")
        with self._lock:
            if self._state != "generating":
                raise CoturnRuntimeError("Runtime TLS material generation failed")
            self._state = "generated"

    def _bind_container(self, authority: ContainerCleanupAuthority) -> None:
        with self._lock:
            if (
                self._state != "generated"
                or self._probe_owner is _USE_REVOKED
                or type(authority) is not ContainerCleanupAuthority
                or authority.plan.paths != self._paths
                or authority.plan.network.authority.plan.topology != self._topology
            ):
                raise CoturnRuntimeError("Runtime TLS container binding is invalid")
            self._container_id = authority.container_id
            self._state = "bound"

    def _matches_running(self, running: ValidatedRunningContainer) -> bool:
        with self._lock:
            retained = (
                self._state == "bound"
                and self._probe_owner is not _USE_REVOKED
                and self._container_id == running.authority.container_id
                and running.authority.plan.network.authority.plan.topology == self._topology
            )
        return bool(
            retained
            and running.authority.plan.paths == self._paths
            and self._slot.has_material is True
        )

    def _matches_generated(
        self,
        paths: CoturnRuntimePaths,
        topology: CoturnBridgeTopology,
    ) -> bool:
        with self._lock:
            retained = (
                self._state in {"generated", "bound"}
                and self._probe_owner is not _USE_REVOKED
                and paths == self._paths
                and topology == self._topology
            )
        return bool(retained and self._slot.has_material is True)

    def _bind_probe_owner(self, owner: object) -> None:
        if type(owner) is not object or self._slot.has_material is not True:
            raise CoturnRuntimeError("Runtime TLS probe ownership is invalid")
        with self._lock:
            if self._state not in {"generated", "bound"}:
                raise CoturnRuntimeError("Runtime TLS probe ownership is invalid")
            if self._probe_owner is None:
                self._probe_owner = owner
            elif self._probe_owner is not owner:
                raise CoturnRuntimeError("Runtime TLS probe ownership is invalid")

    def _matches_probe_owner(self, owner: object) -> bool:
        with self._lock:
            retained = bool(
                type(owner) is object
                and self._state in {"generated", "bound"}
                and self._probe_owner is owner
            )
        return bool(retained and self._slot.has_material is True)

    def _publish_readiness(
        self,
        running: ValidatedRunningContainer,
        receipt: OpenSslReadinessReceipt,
    ) -> OpenSslReadinessReceipt:
        if not self._matches_running(running) or type(receipt) is not OpenSslReadinessReceipt:
            raise CoturnRuntimeError("Runtime TLS readiness ownership is invalid")
        with self._lock:
            if self._state != "bound":
                raise CoturnRuntimeError("Runtime TLS readiness ownership is invalid")
            if self._openssl_readiness is None:
                self._openssl_readiness = receipt
            elif self._openssl_readiness is not receipt:
                raise CoturnRuntimeError("Runtime TLS readiness ownership is invalid")
            return self._openssl_readiness

    def _matches_readiness(
        self,
        running: ValidatedRunningContainer,
        receipt: OpenSslReadinessReceipt,
    ) -> bool:
        if not self._matches_running(running):
            return False
        with self._lock:
            return (
                self._state == "bound"
                and type(receipt) is OpenSslReadinessReceipt
                and self._openssl_readiness is receipt
            )

    def _cleanup_after_removal(self, removal: ContainerAbsenceReceipt) -> None:
        if not removal._matches_container(self._paths, self._container_id):
            raise CoturnRuntimeError("Runtime TLS cleanup receipt is invalid")
        self._cleanup_slot(allowed_state="bound")

    def _cleanup_unpublished(self) -> None:
        with self._cleanup_lock:
            with self._lock:
                state = self._state
            if state == "cleaned":
                return
            allowed_state = (
                state.removeprefix("cleaning:") if state.startswith("cleaning:") else state
            )
            if allowed_state not in {"generation-failed", "generated"}:
                raise CoturnRuntimeError("Runtime TLS unpublished cleanup is invalid")
            if self._slot.has_material is not True:
                with self._lock:
                    if self._state not in {allowed_state, f"cleaning:{allowed_state}"}:
                        raise CoturnRuntimeError("Runtime TLS unpublished cleanup is invalid")
                    self._openssl_readiness = None
                    self._probe_owner = _USE_REVOKED
                    self._state = "cleaned"
                return
            self._cleanup_slot_locked(allowed_state=allowed_state)

    def _cleanup_slot(self, *, allowed_state: str) -> None:
        with self._cleanup_lock:
            self._cleanup_slot_locked(allowed_state=allowed_state)

    def _cleanup_slot_locked(self, *, allowed_state: str) -> None:
        cleaning_state = f"cleaning:{allowed_state}"
        try:
            with self._lock:
                if self._state == "cleaned":
                    return
                if self._state not in {allowed_state, cleaning_state}:
                    raise CoturnRuntimeError("Runtime TLS cleanup authority is invalid")
                self._state = cleaning_state
                self._openssl_readiness = None
                self._probe_owner = _USE_REVOKED
            cleanup_tls_material_generation_slot(self._slot)
            with self._lock:
                if self._state != cleaning_state:
                    raise CoturnRuntimeError("Runtime TLS cleanup authority is invalid")
                self._state = "cleaned"
        except BaseException:
            with self._lock:
                if self._state == cleaning_state:
                    self._state = allowed_state
            raise

    def __repr__(self) -> str:
        return "RuntimeTlsMaterial()"

    def __copy__(self) -> None:
        raise TypeError("Runtime TLS material cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Runtime TLS material cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Runtime TLS material cannot be serialized")


def new_runtime_tls_material(
    *,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
) -> RuntimeTlsMaterial:
    """Create a harmless exact-path aggregate before generation can create files."""

    material: RuntimeTlsMaterial | None = None
    slot: TlsMaterialGenerationSlot | None = None
    control: ControlSignal | None = None
    try:
        if type(paths) is not CoturnRuntimePaths or type(topology) is not CoturnBridgeTopology:
            raise CoturnRuntimeError("Runtime TLS material is invalid")
        slot = new_tls_material_generation_slot(paths=paths, topology=topology)
        material = RuntimeTlsMaterial(
            _TLS_TOKEN,
            paths=paths,
            topology=topology,
            slot=slot,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    paths = topology = slot = None  # type: ignore[assignment]
    if control is not None:
        material = None
        raise_control(control)
    if material is None:
        raise CoturnRuntimeError("Runtime TLS material is invalid") from None
    return material


def generate_runtime_tls_material(
    *,
    material: RuntimeTlsMaterial,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
    static_auth_secret: object,
    now: datetime,
) -> None:
    """Generate into a preowned aggregate; no cleanup authority is returned."""

    slot: TlsMaterialGenerationSlot | None = None
    control: ControlSignal | None = None
    opaque_recovery: object | None = None
    opaque_failure: CoturnTlsCleanupRequired | CoturnTlsPrivateCleanupRequired | None = None
    failed = False
    generation_cleanup = False
    try:
        if type(material) is not RuntimeTlsMaterial:
            raise CoturnRuntimeError("Runtime TLS material is invalid")
        slot = material._begin_generation(paths, topology)
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=runner,
            tools=tools,
            paths=paths,
            topology=topology,
            static_auth_secret=static_auth_secret,
            now=now,
        )
        material._generation_complete()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        opaque_recovery = _tls_control_authority(error)
    except (CoturnTlsCleanupRequired, CoturnTlsPrivateCleanupRequired) as error:
        opaque_recovery = _tls_exception_authority(error)
        opaque_failure = error if opaque_recovery is not None else None
        failed = True
    except BaseException:
        failed = True
    if (control is not None or failed) and type(material) is RuntimeTlsMaterial:
        generation_cleanup = material._generation_failed()
    runner = tools = paths = topology = now = slot = None  # type: ignore[assignment]
    static_auth_secret = None
    if control is not None:
        recovery = opaque_recovery or (material if generation_cleanup else None)
        material = opaque_recovery = opaque_failure = None  # type: ignore[assignment]
        raise_control(control, recovery)
    if opaque_failure is not None:
        failure = opaque_failure
        material = opaque_recovery = opaque_failure = None  # type: ignore[assignment]
        raise failure from None
    if failed:
        recovery = material if generation_cleanup else None
        material = None  # type: ignore[assignment]
        if recovery is not None:
            raise CoturnRuntimeTlsCleanupRequired(recovery) from None
        raise CoturnRuntimeError("Runtime TLS material generation failed") from None


def bind_runtime_tls_material_to_container(
    material: RuntimeTlsMaterial,
    authority: ContainerCleanupAuthority,
) -> None:
    """Commit generated TLS only after exact container recovery authority exists."""

    control: ControlSignal | None = None
    failed = False
    try:
        if type(material) is not RuntimeTlsMaterial:
            raise CoturnRuntimeError("Runtime TLS container binding is invalid")
        material._bind_container(authority)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    authority = None  # type: ignore[assignment]
    if control is not None:
        recovery = material
        material = None  # type: ignore[assignment]
        raise_control(control, recovery)
    if failed:
        material = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Runtime TLS container binding is invalid") from None


def cleanup_unpublished_runtime_tls_material(material: RuntimeTlsMaterial) -> None:
    """Clean only a preowned aggregate whose generation did not publish success."""

    _cleanup_runtime_tls_boundary(material, unpublished=True)


def execute_openssl_readiness(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    running: ValidatedRunningContainer,
    tls_material: RuntimeTlsMaterial,
    readiness_budget: RuntimeReadinessBudget,
) -> OpenSslReadinessReceipt:
    """Retry only exact connection-refused results under the shared deadline."""

    receipt: OpenSslReadinessReceipt | None = None
    control: ControlSignal | None = None
    result: object = None
    request = None
    try:
        if (
            type(running) is not ValidatedRunningContainer
            or type(tls_material) is not RuntimeTlsMaterial
            or type(readiness_budget) is not RuntimeReadinessBudget
            or not tls_material._matches_running(running)
        ):
            raise CoturnRuntimeError("Coturn readiness ownership is invalid")
        cached = readiness_budget._cached_openssl(tls_material)
        if cached is not None:
            if type(cached) is not OpenSslReadinessReceipt:
                raise CoturnRuntimeError("Coturn OpenSSL readiness failed")
            receipt = tls_material._publish_readiness(running, cached)
        while receipt is None:
            request = readiness_budget._prepare_request(
                "openssl",
                build_openssl_readiness_request(tools, tls_material._paths),
            )
            result = runner.run(request)
            if (
                type(result) is not CommandResult
                or len(result.stdout) + len(result.stderr) > request.maximum_output_bytes
            ):
                raise CoturnRuntimeError("Coturn OpenSSL readiness failed")
            candidate: OpenSslReadinessReceipt | None = None
            try:
                candidate = validate_openssl_readiness_result(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                if not _openssl_connection_refused(result):
                    raise
                result = request = None
                readiness_budget._retry("openssl")
            if candidate is not None:
                published = readiness_budget._openssl_ready(tls_material, candidate)
                if type(published) is not OpenSslReadinessReceipt:
                    raise CoturnRuntimeError("Coturn OpenSSL readiness failed")
                receipt = tls_material._publish_readiness(running, published)
                published = None
                candidate = None
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    if receipt is None and control is None and type(readiness_budget) is RuntimeReadinessBudget:
        readiness_budget._fail()
    result = request = None
    runner = tools = running = tls_material = readiness_budget = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn OpenSSL readiness failed") from None
    return receipt


def cleanup_runtime_tls_material(
    material: RuntimeTlsMaterial,
    *,
    container_removal: ContainerAbsenceReceipt,
) -> None:
    """Clean exact TLS inodes once, only after same-path confirmed absence."""

    if (
        type(material) is not RuntimeTlsMaterial
        or type(container_removal) is not ContainerAbsenceReceipt
    ):
        material = container_removal = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Runtime TLS cleanup receipt is invalid")
    control: ControlSignal | None = None
    failed = False
    failure_message = "Runtime TLS cleanup failed"
    try:
        material._cleanup_after_removal(container_removal)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except CoturnRuntimeError as error:
        candidate = str(error)
        if candidate in {
            "Runtime TLS cleanup receipt is invalid",
            "Runtime TLS cleanup authority is invalid",
        }:
            failure_message = candidate
        failed = True
    except BaseException:
        failed = True
    container_removal = None  # type: ignore[assignment]
    if control is not None:
        recovery = material if not material.cleanup_complete else None
        material = None  # type: ignore[assignment]
        raise_control(control, recovery)
    if failed:
        if failure_message == "Runtime TLS cleanup failed":
            recovery = material
            material = None  # type: ignore[assignment]
            raise CoturnRuntimeTlsCleanupRequired(recovery) from None
        material = None  # type: ignore[assignment]
        raise CoturnRuntimeError(failure_message) from None


def _cleanup_runtime_tls_boundary(
    material: RuntimeTlsMaterial,
    *,
    unpublished: bool,
) -> None:
    if type(material) is not RuntimeTlsMaterial or unpublished is not True:
        material = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Runtime TLS unpublished cleanup is invalid")
    control: ControlSignal | None = None
    failed = False
    invalid = False
    try:
        material._cleanup_unpublished()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except CoturnRuntimeError as error:
        invalid = str(error) == "Runtime TLS unpublished cleanup is invalid"
        failed = not invalid
    except BaseException:
        failed = True
    if control is not None:
        recovery = material if not material.cleanup_complete else None
        material = None  # type: ignore[assignment]
        raise_control(control, recovery)
    if invalid:
        material = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Runtime TLS unpublished cleanup is invalid") from None
    if failed:
        recovery = material
        material = None  # type: ignore[assignment]
        raise CoturnRuntimeTlsCleanupRequired(recovery) from None


def _tls_exception_authority(
    error: CoturnTlsCleanupRequired | CoturnTlsPrivateCleanupRequired,
) -> object | None:
    try:
        candidate = error.cleanup_authority
    except BaseException:
        candidate = None
    return candidate if type(candidate) in _TLS_RECOVERY_TYPES else None


def _tls_control_authority(error: KeyboardInterrupt | SystemExit) -> object | None:
    try:
        namespace = object.__getattribute__(error, "__dict__")
    except BaseException:
        namespace = None
    candidate = namespace.get("cleanup_authority") if type(namespace) is dict else None
    namespace = None
    return candidate if type(candidate) in _TLS_RECOVERY_TYPES else None


def _openssl_connection_refused(result: CommandResult) -> bool:
    value = result.stderr
    matched = bool(
        result.returncode == 1
        and result.stdout == b""
        and value == b"BIO_connect:Connection refused\nconnect:errno=111\n"
    )
    value = b""
    return matched


__all__ = [
    "CoturnRuntimeTlsCleanupRequired",
    "RuntimeTlsMaterial",
    "bind_runtime_tls_material_to_container",
    "cleanup_runtime_tls_material",
    "cleanup_unpublished_runtime_tls_material",
    "execute_openssl_readiness",
    "generate_runtime_tls_material",
    "new_runtime_tls_material",
]

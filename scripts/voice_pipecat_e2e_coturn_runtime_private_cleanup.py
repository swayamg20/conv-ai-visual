"""Opaque retry ownership for runtime private-file persistence failures."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_runtime_directory import (
    CoturnDirectorySyncCleanupRequired,
    DirectorySyncCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    cleanup_tls_private_authority,
    tls_private_cleanup_authority,
)

_AUTHORITY_TOKEN = object()


class RuntimePrivateCleanupAuthority:
    """Factory-owned wrapper hiding one exact TLS private-file retry owner."""

    __slots__ = ("_authority", "_lock", "_state")

    def __init__(self, token: object, authority: object) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Coturn runtime private cleanup authority is factory-owned")
        self._authority: object | None = authority
        self._state = "retained"
        self._lock = threading.Lock()

    def _cleanup(self) -> tuple[bool, ControlSignal | None]:
        with self._lock:
            if self._state == "cleaned":
                self._authority = None
                return False, None
            if self._state not in {"retained", "cleaning"} or self._authority is None:
                return True, None
            authority = self._authority
            self._state = "cleaning"
            failed = True
            control: ControlSignal | None = None
            replacement: object | None = None
            result: object = object()
            try:
                result = cleanup_tls_private_authority(authority)
                failed = result is not None
            except (KeyboardInterrupt, SystemExit) as error:
                control = control_signal(error)
                replacement, extraction_control = _extract_tls_private_authority(error)
                control = control or extraction_control
            except BaseException as error:
                replacement, extraction_control = _extract_tls_private_authority(error)
                control = extraction_control
            result = None
            authority = None
            if failed:
                if replacement is not None:
                    self._authority = replacement
                self._state = "retained"
            else:
                self._state = "cleaned"
                self._authority = None
            replacement = None
        return failed, control

    def __repr__(self) -> str:
        return "RuntimePrivateCleanupAuthority()"


class CoturnRuntimePrivateCleanupRequired(CoturnRuntimeError):
    """Fixed failure carrying only an opaque runtime-private retry owner."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: RuntimePrivateCleanupAuthority) -> None:
        if type(authority) is not RuntimePrivateCleanupAuthority:
            raise TypeError("Coturn runtime private cleanup error is factory-owned")
        super().__init__("Coturn runtime private-file cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> RuntimePrivateCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnRuntimePrivateCleanupRequired('Coturn runtime private-file cleanup failed')"


class _RuntimePrivateCleanupCapture:
    """Retain only allowlisted opaque cleanup owners while scrubbing failures."""

    __slots__ = ("_control", "_directory", "_private")

    def __init__(self) -> None:
        self._control: ControlSignal | None = None
        self._directory: DirectorySyncCleanupAuthority | None = None
        self._private: RuntimePrivateCleanupAuthority | None = None

    def capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        self._control = self._control or control_signal(error)
        self._capture_private(error)
        if self._private is None:
            self._directory = _control_directory_authority(error)

    def capture_error(self, error: BaseException) -> bool:
        self._capture_private(error)
        if self._private is None:
            self._directory = _control_directory_authority(error)
        return self._private is not None or self._directory is not None

    def _capture_private(self, error: BaseException) -> None:
        try:
            candidate = _runtime_private_cleanup_authority(error)
        except (KeyboardInterrupt, SystemExit) as extraction_error:
            self._control = self._control or control_signal(extraction_error)
            candidate = _retained_runtime_authority(extraction_error)
        except BaseException:
            candidate = None
        if candidate is not None:
            self._private = candidate

    def raise_captured(self) -> None:
        if self._control is not None:
            control = self._control
            authority = self._private or self._directory
            self._control = None
            self._private = None
            self._directory = None
            raise_control(control, authority)
        if self._private is not None:
            authority = self._private
            self._private = None
            raise CoturnRuntimePrivateCleanupRequired(authority) from None
        if self._directory is not None:
            authority = self._directory
            self._directory = None
            raise CoturnDirectorySyncCleanupRequired(authority) from None


def cleanup_runtime_private_authority(authority: RuntimePrivateCleanupAuthority) -> None:
    """Retry the hidden private-file cleanup without exposing its concrete type."""

    if type(authority) is not RuntimePrivateCleanupAuthority:
        authority = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn runtime private cleanup authority is invalid")
    failed = True
    control: ControlSignal | None = None
    try:
        failed, control = authority._cleanup()
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        failed = True
    if control is not None:
        recovery = authority if failed else None
        authority = None  # type: ignore[assignment]
        raise_control(control, recovery)
    if failed:
        recovery = authority
        authority = None  # type: ignore[assignment]
        raise CoturnRuntimePrivateCleanupRequired(recovery) from None
    authority = None  # type: ignore[assignment]


def _runtime_persistence_cleanup_authority(error: BaseException) -> object | None:
    private = _runtime_private_cleanup_authority(error)
    return private if private is not None else _control_directory_authority(error)


def _runtime_persistence_outcome(
    error: BaseException,
) -> tuple[ControlSignal | None, object | None]:
    capture = _RuntimePrivateCleanupCapture()
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        capture.capture_control(error)
    else:
        capture.capture_error(error)
    control = capture._control
    authority = capture._private or capture._directory
    error = capture = None  # type: ignore[assignment]
    return control, authority


def _runtime_private_cleanup_authority(
    error: BaseException,
) -> RuntimePrivateCleanupAuthority | None:
    retained = _retained_runtime_authority(error)
    if retained is not None:
        return retained
    candidate, control = _extract_tls_private_authority(error)
    result = (
        RuntimePrivateCleanupAuthority(_AUTHORITY_TOKEN, candidate)
        if candidate is not None
        else None
    )
    error = candidate = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, result)
    return result


def _extract_tls_private_authority(
    error: BaseException,
) -> tuple[object | None, ControlSignal | None]:
    candidate: object | None = None
    pending: BaseException | None = error
    control: ControlSignal | None = None
    for _attempt in range(8):
        try:
            candidate = tls_private_cleanup_authority(pending)  # type: ignore[arg-type]
            pending = None
            break
        except (KeyboardInterrupt, SystemExit) as extraction_error:
            control = control or control_signal(extraction_error)
            pending = extraction_error
        except BaseException:
            pending = None
            break
    error = pending = None  # type: ignore[assignment]
    return candidate, control


def _retained_runtime_authority(error: BaseException) -> RuntimePrivateCleanupAuthority | None:
    candidate: object | None = None
    try:
        if type(error) is CoturnRuntimePrivateCleanupRequired:
            candidate = object.__getattribute__(error, "_cleanup_authority")
        elif type(error) in {KeyboardInterrupt, SystemExit}:
            namespace = object.__getattribute__(error, "__dict__")
            candidate = namespace.get("cleanup_authority") if type(namespace) is dict else None
    except BaseException:
        candidate = None
    return candidate if type(candidate) is RuntimePrivateCleanupAuthority else None


def _control_directory_authority(
    error: BaseException,
) -> DirectorySyncCleanupAuthority | None:
    if type(error) is CoturnDirectorySyncCleanupRequired:
        try:
            candidate = object.__getattribute__(error, "_cleanup_authority")
        except BaseException:
            candidate = None
        return candidate if type(candidate) is DirectorySyncCleanupAuthority else None
    try:
        namespace = object.__getattribute__(error, "__dict__")
    except BaseException:
        namespace = None
    candidate = namespace.get("cleanup_authority") if type(namespace) is dict else None
    namespace = None
    return candidate if type(candidate) is DirectorySyncCleanupAuthority else None


__all__ = [
    "CoturnRuntimePrivateCleanupRequired",
    "RuntimePrivateCleanupAuthority",
    "cleanup_runtime_private_authority",
]

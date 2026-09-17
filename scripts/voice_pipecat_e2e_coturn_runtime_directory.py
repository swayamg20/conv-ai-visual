"""Control-safe owner for durable Coturn control-directory commits."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_host import require_owned_directory
from scripts.voice_pipecat_e2e_coturn_runtime_values import CoturnRuntimeError, raise_control
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
    file_identity,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import (
    ControlSignal,
    TlsControlLatch,
    TlsOwnerService,
    sanitize_control,
    start_tls_owner_service,
)

_AUTHORITY_TOKEN = object()


class DirectorySyncCleanupAuthority:
    """Opaque retry owner for one exact, not-yet-proven-closed directory fd."""

    __slots__ = ("_descriptor", "_identity", "_lock", "_state")

    def __init__(self, token: object) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("Coturn directory cleanup authority is factory-owned")
        self._descriptor: int | None = None
        self._identity: tuple[int, int] | None = None
        self._state = "empty"
        self._lock = threading.Lock()

    def _publish(self, descriptor: int, identity: tuple[int, int] | None) -> bool:
        if (
            type(descriptor) is not int
            or descriptor < 0
            or (
                identity is not None
                and (
                    type(identity) is not tuple
                    or len(identity) != 2
                    or any(type(value) is not int or value < 0 for value in identity)
                )
            )
        ):
            return False
        with self._lock:
            if self._state != "empty":
                return False
            self._descriptor = descriptor
            self._identity = identity
            self._state = "owned"
            return True

    def _owns(self, descriptor: int) -> bool:
        with self._lock:
            return self._state == "owned" and self._descriptor == descriptor

    def _owned_descriptor(self) -> int | None:
        with self._lock:
            return self._descriptor if self._state == "owned" else None

    def _close(self, control: TlsControlLatch) -> bool:
        with self._lock:
            if self._state == "cleaned":
                self._descriptor = None
                self._identity = None
                return True
            if self._state == "closing" and self._descriptor is None:
                self._state = "cleaned"
                self._identity = None
                return True
            if self._state not in {"owned", "closing"} or self._descriptor is None:
                return False
            descriptor = self._descriptor
            identity = self._identity
            self._state = "closing"
            closed = close_owned_descriptor(descriptor, identity, control)
            if closed:
                self._state = "cleaned"
                self._descriptor = None
                self._identity = None
            else:
                self._state = "owned"
            descriptor = identity = None
        return closed

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state in {"owned", "closing"}

    def __repr__(self) -> str:
        return "DirectorySyncCleanupAuthority()"


class CoturnDirectorySyncCleanupRequired(CoturnRuntimeError):
    """Fixed failure carrying only an opaque descriptor retry owner."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: DirectorySyncCleanupAuthority) -> None:
        super().__init__("Coturn directory descriptor cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> DirectorySyncCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnDirectorySyncCleanupRequired('Coturn directory descriptor cleanup failed')"


class _DirectorySyncTask:
    __slots__ = ("authority", "control", "done", "failed", "path", "synced")

    def __init__(self, path: Path, authority: DirectorySyncCleanupAuthority) -> None:
        self.path = path
        self.authority = authority
        self.control = TlsControlLatch()
        self.done = threading.Event()
        self.failed = False
        self.synced = False

    def run(self) -> None:
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            require_owned_directory(self.path)
            before = self.path.stat(follow_symlinks=False)
            required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
            if any(not hasattr(os, name) for name in required_flags):
                raise OSError
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(self.path, flags)
            current = os.fstat(descriptor)
            identity = file_identity(current)
            if (
                identity != file_identity(before)
                or not stat.S_ISDIR(current.st_mode)
                or current.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) != 0o700
                or not self.authority._publish(descriptor, identity)
            ):
                raise OSError
            descriptor = None
            owned = self.authority._owned_descriptor()
            if owned is None:
                raise OSError
            os.fsync(owned)
            self.synced = True
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        if descriptor is not None:
            try:
                if self.authority._publish(descriptor, identity) or self.authority._owns(
                    descriptor
                ):
                    descriptor = None
            except (KeyboardInterrupt, SystemExit) as error:
                self.control.record_error(error)
            except BaseException:
                pass
        if descriptor is not None:
            if not close_owned_descriptor(descriptor, identity, self.control):
                self.failed = True
            descriptor = None
        try:
            if self.authority.active and not self.authority._close(self.control):
                self.failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        if not self.synced:
            self.failed = True
        self.path = None  # type: ignore[assignment]
        self.done.set()

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        if self.authority.active:
            self.authority._close(self.control)
        self.path = None  # type: ignore[assignment]
        self.done.set()


def sync_owned_directory(path: Path) -> None:
    """Durably sync an exact owned directory without lending its fd to the caller."""

    authority = DirectorySyncCleanupAuthority(_AUTHORITY_TOKEN)
    service = TlsOwnerService()
    task: _DirectorySyncTask | None = None
    control: ControlSignal | None = None
    failed = True
    try:
        started, control = start_tls_owner_service(service)
        if started:
            task = _DirectorySyncTask(path, authority)
            failed = not service.execute(task) or task.failed
            control = task.control.value() or control
        else:
            service = None  # type: ignore[assignment]
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or sanitize_control(error)
        if task is not None:
            task.control.record(control)
            failed = not task.done.is_set() and not service.execute(task)
            failed = bool(failed or task.failed)
            control = task.control.value() or control
        else:
            control = service.abort(control)
    except BaseException:
        if task is not None:
            failed = not task.done.is_set() and not service.execute(task)
            failed = bool(failed or task.failed)
            control = task.control.value() or control
        else:
            control = service.abort(control)
    recovery = authority if authority.active else None
    path = task = service = authority = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, recovery)
    if recovery is not None:
        raise CoturnDirectorySyncCleanupRequired(recovery) from None
    if failed:
        raise CoturnRuntimeError("Coturn network absence commit failed") from None


def cleanup_directory_sync_authority(authority: DirectorySyncCleanupAuthority) -> None:
    """Retry closure of one exact descriptor retained after an ambiguous close."""

    if type(authority) is not DirectorySyncCleanupAuthority:
        authority = None  # type: ignore[assignment]
        raise CoturnRuntimeError("Coturn directory cleanup authority is invalid")
    control = TlsControlLatch()
    closed = False
    try:
        closed = authority._close(control)
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
    except BaseException:
        closed = False
    observed = control.value()
    recovery = authority if not closed else None
    authority = None  # type: ignore[assignment]
    if observed is not None:
        raise_control(observed, recovery)
    if not closed:
        raise CoturnDirectorySyncCleanupRequired(recovery) from None  # type: ignore[arg-type]


__all__ = [
    "CoturnDirectorySyncCleanupRequired",
    "DirectorySyncCleanupAuthority",
    "cleanup_directory_sync_authority",
    "sync_owned_directory",
]

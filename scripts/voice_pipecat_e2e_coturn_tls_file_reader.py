"""Descriptor-owning reader for same-owner Coturn TLS private files."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_host import (
    require_owned_directory,
    require_safe_path,
)
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
)
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    file_identity as file_identity_of,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    new_private_descriptor_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import (
    ControlSignal,
    TlsControlLatch,
    TlsOwnerService,
    sanitize_control,
    start_tls_owner_service,
)


class _ReadTask:
    __slots__ = (
        "control",
        "descriptor_authority",
        "done",
        "exact_mode",
        "failed",
        "maximum",
        "path",
        "value",
    )

    def __init__(
        self,
        path: Path,
        exact_mode: int,
        maximum: int,
        descriptor_authority: PrivateDescriptorCleanupAuthority,
    ) -> None:
        self.path: Path | None = path
        self.exact_mode = exact_mode
        self.maximum = maximum
        self.control = TlsControlLatch()
        self.done = threading.Event()
        self.value = b""
        self.failed = False
        self.descriptor_authority = descriptor_authority

    def run(self) -> None:
        value = b""
        try:
            value, self.failed = _read_file(
                self.path,
                exact_mode=self.exact_mode,
                maximum=self.maximum,
                control=self.control,
                descriptor_authority=self.descriptor_authority,
            )
            if not self.failed:
                self.value = value
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        finally:
            self.descriptor_authority.mark_unsubmitted_empty()
            value = b""
            self.path = None
            self.done.set()

    def discard(self) -> None:
        self.value = b""
        self.path = None

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        self.descriptor_authority.mark_unsubmitted_empty()
        self.discard()
        self.done.set()


def read_private_file_owned(
    path: Path,
    *,
    exact_mode: int,
    maximum: int,
) -> tuple[
    bytes,
    bool,
    ControlSignal | None,
    PrivateDescriptorCleanupAuthority | None,
]:
    """Return bytes only after the descriptor-owning worker has terminated."""

    descriptor_authority = new_private_descriptor_cleanup_authority()
    service = TlsOwnerService()
    control: ControlSignal | None = None
    task: _ReadTask | None = None
    failed = False
    try:
        started, control = start_tls_owner_service(service)
        if not started:
            descriptor_authority.mark_unsubmitted_empty()
            service = None
            return b"", True, control, None
        task = _ReadTask(path, exact_mode, maximum, descriptor_authority)
        failed = not service.execute(task)
    except (KeyboardInterrupt, SystemExit) as error:
        if task is not None:
            task.control.record_error(error)
            failed = not task.done.is_set() and not service.execute(task)
        else:
            control = service.abort(sanitize_control(error))
            descriptor_authority.mark_unsubmitted_empty()
            service = None
            return b"", True, control, None
    except BaseException:
        failed = True
    if task is None:
        control = service.abort()
        descriptor_authority.mark_unsubmitted_empty()
        service = None
        return b"", True, control, None
    control = task.control.value()
    value = task.value if not (failed or task.failed or control is not None) else b""
    failed = bool(failed or task.failed)
    task.discard()
    recovery = descriptor_authority if descriptor_authority.active else None
    task = service = None
    return value, failed, control, recovery


def _read_file(
    path: Path | None,
    *,
    exact_mode: int,
    maximum: int,
    control: TlsControlLatch,
    descriptor_authority: PrivateDescriptorCleanupAuthority,
) -> tuple[bytes, bool]:
    if not descriptor_authority.begin():
        return b"", True
    if path is None or not _valid_read_policy(exact_mode, maximum):
        descriptor_authority.mark_inflight_empty()
        return b"", True
    directory_fd: int | None = None
    file_fd: int | None = None
    directory_identity: tuple[int, int] | None = None
    file_identity: tuple[int, int] | None = None
    chunks: list[bytes] = []
    chunk = b""
    value = b""
    failed = False
    try:
        require_safe_path(path)
        require_owned_directory(path.parent)
        directory_fd, directory_identity, directory_safe = _open_directory(
            path.parent,
            control,
        )
        if not directory_safe:
            raise OSError
        before = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        file_fd = os.open(path.name, _read_flags(), dir_fd=directory_fd)
        opened = os.fstat(file_fd)
        file_identity = file_identity_of(opened)
        if not _safe_file(before, exact_mode, maximum) or not _safe_file(
            opened, exact_mode, maximum
        ):
            raise OSError
        length = 0
        while True:
            chunk = os.read(file_fd, min(65_536, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum:
                raise OSError
        value = b"".join(chunks)
        after_fd = os.fstat(file_fd)
        after_name = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        metadata = (before, opened, after_fd, after_name)
        if (
            any(not _safe_file(item, exact_mode, maximum) for item in metadata)
            or len({file_identity_of(item) for item in metadata}) != 1
            or {item.st_size for item in metadata} != {len(value)}
        ):
            raise OSError
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
        failed = True
    except BaseException:
        failed = True
    unproved: list[tuple[int, tuple[int, int] | None]] = []
    if file_fd is not None and not close_owned_descriptor(file_fd, file_identity, control):
        failed = True
        unproved.append((file_fd, file_identity))
    if directory_fd is not None:
        if not close_owned_descriptor(directory_fd, directory_identity, control):
            failed = True
            unproved.append((directory_fd, directory_identity))
    if unproved:
        if not _publish_descriptor_authority(
            descriptor_authority,
            tuple(unproved),
            control,
        ):
            failed = True
    else:
        descriptor_authority.mark_inflight_empty()
    file_fd = directory_fd = None
    chunks.clear()
    chunk = b""
    if failed or control.value() is not None:
        value = b""
    return value, failed


def _publish_descriptor_authority(
    authority: PrivateDescriptorCleanupAuthority,
    descriptors: tuple[tuple[int, tuple[int, int] | None], ...],
    control: TlsControlLatch,
) -> bool:
    while True:
        try:
            published = authority.publish(descriptors)
            return bool(published or authority.owned)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            while True:
                try:
                    return authority.owned
                except (KeyboardInterrupt, SystemExit) as error:
                    control.record_error(error)
                except BaseException:
                    return False


def _open_directory(
    path: Path,
    control: TlsControlLatch,
) -> tuple[int, tuple[int, int] | None, bool]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    identity: tuple[int, int] | None = None
    try:
        opened = os.fstat(descriptor)
        identity = file_identity_of(opened)
        named = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or identity != file_identity_of(named)
        ):
            raise OSError
        return descriptor, identity, True
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
    except BaseException:
        pass
    return descriptor, identity, False


def _valid_read_policy(exact_mode: object, maximum: object) -> bool:
    return bool(
        type(exact_mode) is int
        and exact_mode in {0o400, 0o600}
        and type(maximum) is int
        and 1 <= maximum <= 1_048_576
    )


def _read_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _safe_file(value: os.stat_result, mode: int, maximum: int) -> bool:
    return bool(
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == mode
        and 0 <= value.st_size <= maximum
    )


__all__ = ["read_private_file_owned"]

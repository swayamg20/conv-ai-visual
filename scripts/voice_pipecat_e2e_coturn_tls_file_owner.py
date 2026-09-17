"""Worker-owned file descriptors for Coturn TLS private material."""

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
    remove_owned_inode,
    remove_owned_path,
)
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    file_identity as file_identity_of,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateFileCleanupReceipt,
    new_private_file_cleanup_receipt,
    release_private_file_receipt_owned,
    settle_private_file_receipt_in_owner,
    settle_private_file_receipts_owned,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import (
    ControlSignal,
    TlsControlLatch,
    TlsOwnerService,
    sanitize_control,
    start_tls_owner_service,
)


class _WriteTask:
    __slots__ = (
        "cleanup_failed",
        "cleanup_receipt",
        "control",
        "done",
        "failed",
        "maximum",
        "mode",
        "path",
        "value",
    )

    def __init__(
        self,
        path: Path,
        value: bytes,
        mode: int,
        maximum: int,
        cleanup_receipt: PrivateFileCleanupReceipt | None,
    ) -> None:
        self.path: Path | None = path
        self.value = value
        self.mode = mode
        self.maximum = maximum
        self.control = TlsControlLatch()
        self.done = threading.Event()
        self.failed = False
        self.cleanup_failed = False
        self.cleanup_receipt = cleanup_receipt

    def run(self) -> None:
        try:
            self.failed, self.cleanup_failed = _write_file(
                self.path,
                self.value,
                mode=self.mode,
                maximum=self.maximum,
                control=self.control,
                cleanup_receipt=self.cleanup_receipt,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
            self.cleanup_failed = True
        except BaseException:
            self.failed = True
            self.cleanup_failed = True
        finally:
            if self.cleanup_receipt is not None and self.cleanup_receipt.unsubmitted:
                self.cleanup_receipt.mark_unsubmitted_empty()
                self.cleanup_failed = False
            self.value = b""
            self.path = None
            self.cleanup_receipt = None
            self.done.set()

    def discard(self) -> None:
        self.value = b""
        self.path = None
        self.cleanup_receipt = None

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        self.cleanup_failed = bool(
            self.cleanup_receipt is not None and not self.cleanup_receipt.mark_unsubmitted_empty()
        )
        self.discard()
        self.done.set()


class _RemoveTask:
    __slots__ = ("control", "done", "failed", "paths")

    def __init__(
        self,
        paths: tuple[Path, ...],
        initial_control: ControlSignal | None,
    ) -> None:
        self.paths: tuple[Path, ...] = paths
        self.control = TlsControlLatch(initial_control)
        self.done = threading.Event()
        self.failed = False

    def run(self) -> None:
        paths = self.paths
        try:
            for path in paths:
                if not remove_owned_path(path, self.control):
                    self.failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        finally:
            paths = ()
            self.paths = ()
            self.done.set()

    def discard(self) -> None:
        self.paths = ()

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        self.discard()
        self.done.set()


def write_private_file_owned(
    path: Path,
    value: bytes,
    *,
    mode: int,
    maximum: int,
    cleanup_receipt: PrivateFileCleanupReceipt | None = None,
) -> tuple[
    bool,
    bool,
    ControlSignal | None,
    PrivateFileCleanupReceipt | None,
]:
    """Create one file, releasing no descriptor or path on caller control."""

    internal_receipt = cleanup_receipt is None
    if cleanup_receipt is None:
        try:
            cleanup_receipt = new_private_file_cleanup_receipt()
        except (KeyboardInterrupt, SystemExit) as error:
            value = b""
            return True, False, sanitize_control(error), None
        except BaseException:
            value = b""
            return True, False, None, None
    service = TlsOwnerService()
    control: ControlSignal | None = None
    task: _WriteTask | None = None
    failed = False
    try:
        started, control = start_tls_owner_service(service)
        if not started:
            if cleanup_receipt is not None:
                cleanup_receipt.mark_unsubmitted_empty()
            value = b""
            service = None
            return True, False, control, None
        task = _WriteTask(path, value, mode, maximum, cleanup_receipt)
        value = b""
        failed = not service.execute(task)
    except (KeyboardInterrupt, SystemExit) as error:
        value = b""
        if task is not None:
            task.control.record_error(error)
            failed = not task.done.is_set() and not service.execute(task)
        else:
            control = service.abort(sanitize_control(error))
            if cleanup_receipt is not None:
                cleanup_receipt.mark_unsubmitted_empty()
            service = None
            return True, False, control, None
    except BaseException:
        value = b""
        failed = True
    if task is None:
        control = service.abort()
        if cleanup_receipt is not None:
            cleanup_receipt.mark_unsubmitted_empty()
        service = None
        return True, False, control, None
    control = task.control.value()
    task_failed = task.failed
    failed = bool(failed or task_failed)
    cleanup_failed = task.cleanup_failed
    task.discard()
    task = service = None
    try:
        if control is not None or failed:
            if internal_receipt or not task_failed:
                receipt_failed, cleanup_control = _settle_after_handoff(
                    cleanup_receipt,
                    control,
                )
                cleanup_failed = bool(cleanup_failed or receipt_failed)
                control = control or cleanup_control
            failed = True
        elif internal_receipt:
            release_failed, release_control = release_private_file_receipt_owned(
                cleanup_receipt,
            )
            control = release_control
            failed = bool(release_failed or control is not None)
            cleanup_failed = release_failed
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or sanitize_control(error)
        failed = cleanup_failed = True
    except BaseException:
        failed = cleanup_failed = True
    if internal_receipt and failed and cleanup_receipt.owned and not cleanup_receipt.committed:
        receipt_failed, cleanup_control = _settle_after_handoff(cleanup_receipt, control)
        cleanup_failed = bool(cleanup_failed or receipt_failed)
        control = control or cleanup_control
    path = None  # type: ignore[assignment]
    recovery = (
        cleanup_receipt
        if internal_receipt and failed and (cleanup_receipt.owned or cleanup_receipt.committed)
        else None
    )
    return failed, cleanup_failed, control, recovery


def _settle_after_handoff(
    receipt: PrivateFileCleanupReceipt,
    control: ControlSignal | None,
) -> tuple[bool, ControlSignal | None]:
    while True:
        try:
            return settle_private_file_receipts_owned(
                (receipt,),
                initial_control=control,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or sanitize_control(error)
        except BaseException:
            return True, control


def remove_private_files_owned(
    paths: tuple[Path, ...],
    *,
    initial_control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    """Attempt every bounded removal in a worker and preserve first control."""

    if not paths:
        return False, initial_control
    if len(paths) > 8:
        return True, initial_control
    service = TlsOwnerService()
    control: ControlSignal | None = None
    task: _RemoveTask | None = None
    failed = False
    try:
        started, control = start_tls_owner_service(service)
        if not started:
            paths = ()
            service = None
            return True, initial_control or control
        task = _RemoveTask(paths, initial_control)
        paths = ()
        failed = not service.execute(task)
    except (KeyboardInterrupt, SystemExit) as error:
        paths = ()
        if task is not None:
            task.control.record_error(error)
            failed = not task.done.is_set() and not service.execute(task)
        else:
            control = service.abort(initial_control or sanitize_control(error))
            service = None
            return True, control
    except BaseException:
        paths = ()
        failed = True
    if task is None:
        control = service.abort(initial_control)
        service = None
        return True, control
    failed = bool(failed or task.failed)
    control = task.control.value()
    task.discard()
    task = service = None
    return failed, control


def _write_file(
    path: Path | None,
    value: bytes,
    *,
    mode: int,
    maximum: int,
    control: TlsControlLatch,
    cleanup_receipt: PrivateFileCleanupReceipt | None,
) -> tuple[bool, bool]:
    if path is None or (
        cleanup_receipt is not None
        and (
            type(cleanup_receipt) is not PrivateFileCleanupReceipt
            or not cleanup_receipt.begin_write()
        )
    ):
        return True, False
    directory_fd: int | None = None
    file_fd: int | None = None
    recovery_fd: int | None = None
    directory_identity: tuple[int, int] | None = None
    file_identity: tuple[int, int] | None = None
    created = False
    failed = False
    cleanup_failed = False
    expected_length = len(value)
    try:
        require_safe_path(path)
        require_owned_directory(path.parent)
        directory_fd, directory_identity, directory_safe = _open_directory(
            path.parent,
            control,
        )
        if not directory_safe:
            raise OSError
        file_fd = os.open(path.name, _write_flags(), mode, dir_fd=directory_fd)
        created = True
        opened = os.fstat(file_fd)
        file_identity = file_identity_of(opened)
        recovery_fd = os.dup(file_fd) if cleanup_receipt is not None else None
        if recovery_fd is not None and file_identity_of(os.fstat(recovery_fd)) != file_identity:
            raise OSError
        if cleanup_receipt is not None:
            published = _publish_exact_receipt(
                cleanup_receipt,
                directory_fd,
                recovery_fd,
                directory_identity,
                file_identity,
                path.name,
                control,
            )
            if not published or control.value() is not None:
                raise OSError
        offset = 0
        view = memoryview(value)
        while offset < expected_length:
            written = os.write(file_fd, view[offset:])
            if type(written) is not int or written <= 0:
                raise OSError
            offset += written
        view.release()
        os.fchmod(file_fd, mode)
        os.fsync(file_fd)
        written_details = os.fstat(file_fd)
        named_details = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not _safe_file(written_details, mode, maximum)
            or written_details.st_size != expected_length
            or file_identity_of(written_details) != file_identity
            or file_identity_of(named_details) != file_identity
        ):
            raise OSError
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
        failed = True
    except BaseException:
        failed = True
    value = b""
    publish_unknown = bool(
        cleanup_receipt is not None
        and cleanup_receipt.inflight
        and created
        and type(directory_fd) is int
        and directory_identity is not None
        and type(file_fd) is int
        and file_identity is None
    )
    while publish_unknown and cleanup_receipt is not None:
        try:
            published = cleanup_receipt.publish_unidentified_file(
                directory_fd,
                file_fd,
                directory_identity,
                path.name,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            continue
        except BaseException:
            published = False
        if published or cleanup_receipt.owned:
            directory_fd = None
            file_fd = None
        break
    if cleanup_receipt is not None and cleanup_receipt.owned:
        recovery_fd = None
    elif (
        cleanup_receipt is not None
        and cleanup_receipt.inflight
        and recovery_fd is None
        and file_fd is not None
        and file_identity is not None
    ):
        recovery_fd = file_fd
        file_fd = None
    if file_fd is not None:
        file_closed = close_owned_descriptor(file_fd, file_identity, control)
        if not file_closed:
            failed = True
            cleanup_failed = True
            if (
                cleanup_receipt is not None
                and cleanup_receipt.owned
                and file_identity is not None
                and cleanup_receipt.retain_file_descriptor(file_fd, file_identity)
            ):
                file_fd = None
        else:
            file_fd = None
    if not failed and control.value() is None and directory_fd is not None:
        try:
            final_details = os.stat(
                path.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if (
                file_identity is None
                or not _safe_file(final_details, mode, maximum)
                or final_details.st_size != expected_length
                or file_identity_of(final_details) != file_identity
            ):
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            failed = True
        except BaseException:
            failed = True
    if failed or control.value() is not None:
        failed = True
        if cleanup_receipt is not None and cleanup_receipt.owned:
            cleanup_failed = bool(
                cleanup_failed or not settle_private_file_receipt_in_owner(cleanup_receipt, control)
            )
            directory_fd = None
            recovery_fd = None
        elif created and directory_fd is not None and file_identity is not None:
            if cleanup_receipt is not None and cleanup_receipt.inflight:
                if _publish_exact_receipt(
                    cleanup_receipt,
                    directory_fd,
                    recovery_fd,
                    directory_identity,
                    file_identity,
                    path.name,
                    control,
                ):
                    cleanup_failed = bool(
                        cleanup_failed
                        or not settle_private_file_receipt_in_owner(
                            cleanup_receipt,
                            control,
                        )
                    )
                    directory_fd = None
                    recovery_fd = None
                else:
                    cleanup_failed = True
            else:
                cleanup_failed = bool(
                    cleanup_failed
                    or not remove_owned_inode(
                        directory_fd,
                        path.name,
                        file_identity,
                        control,
                    )
                )
        elif created:
            cleanup_failed = True
    if not failed and cleanup_receipt is not None and cleanup_receipt.owned:
        directory_fd = None
        recovery_fd = None
    if recovery_fd is not None:
        if not close_owned_descriptor(recovery_fd, file_identity, control):
            failed = True
            cleanup_failed = True
        recovery_fd = None
    if file_fd is not None:
        if not close_owned_descriptor(file_fd, file_identity, control):
            failed = True
            cleanup_failed = True
        file_fd = None
    if directory_fd is not None:
        if close_owned_descriptor(directory_fd, directory_identity, control):
            directory_fd = None
        else:
            failed = True
            cleanup_failed = True
            if (
                cleanup_receipt is not None
                and cleanup_receipt.inflight
                and not created
                and _publish_directory_receipt(
                    cleanup_receipt,
                    directory_fd,
                    directory_identity,
                    control,
                )
            ):
                directory_fd = None
    if (
        cleanup_receipt is not None
        and cleanup_receipt.inflight
        and not created
        and directory_fd is None
    ):
        cleanup_receipt.mark_inflight_empty()
    directory_fd = None
    return failed, cleanup_failed


def _publish_exact_receipt(
    receipt: PrivateFileCleanupReceipt,
    directory_fd: int,
    file_fd: int,
    directory_identity: tuple[int, int],
    file_identity: tuple[int, int],
    name: str,
    control: TlsControlLatch,
) -> bool:
    while True:
        try:
            published = receipt.publish_owned(
                directory_fd,
                file_fd,
                directory_identity,
                file_identity,
                name,
            )
            return bool(published or receipt.owned)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            while True:
                try:
                    return receipt.owned
                except (KeyboardInterrupt, SystemExit) as error:
                    control.record_error(error)
                except BaseException:
                    return False


def _publish_directory_receipt(
    receipt: PrivateFileCleanupReceipt,
    directory_fd: int,
    directory_identity: tuple[int, int] | None,
    control: TlsControlLatch,
) -> bool:
    while True:
        try:
            published = receipt.publish_directory_only(
                directory_fd,
                directory_identity,
            )
            return bool(published or receipt.owned)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            while True:
                try:
                    return receipt.owned
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


def _write_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    return flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _safe_file(value: os.stat_result, mode: int, maximum: int) -> bool:
    return bool(
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == mode
        and 0 <= value.st_size <= maximum
    )


__all__ = [
    "ControlSignal",
    "PrivateFileCleanupReceipt",
    "new_private_file_cleanup_receipt",
    "remove_private_files_owned",
    "settle_private_file_receipts_owned",
    "write_private_file_owned",
]

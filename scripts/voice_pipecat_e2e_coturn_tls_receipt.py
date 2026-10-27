"""Opaque exact-inode cleanup receipts for Coturn TLS private files."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
    destroy_owned_file,
    identify_owned_descriptor,
    remove_owned_inode,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import (
    ControlSignal,
    TlsControlLatch,
    TlsOwnerService,
    sanitize_control,
    start_tls_owner_service,
)

_RECEIPT_TOKEN = object()
_DESCRIPTOR_TOKEN = object()


class PrivateFileCleanupReceipt:
    """Opaque authority for the exact directory and inode created by a write."""

    __slots__ = (
        "_content_destroyed",
        "_directory_fd",
        "_directory_identity",
        "_extra_file_fds",
        "_file_fd",
        "_file_identity",
        "_lock",
        "_name",
        "_state",
    )

    def __init__(self, token: object) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("private-file cleanup receipt is factory-owned")
        self._content_destroyed = False
        self._directory_fd: int | None = None
        self._directory_identity: tuple[int, int] | None = None
        self._file_fd: int | None = None
        self._file_identity: tuple[int, int] | None = None
        self._extra_file_fds: tuple[int, ...] = ()
        self._lock = threading.RLock()
        self._name = ""
        self._state = "unsubmitted"

    @property
    def unsubmitted(self) -> bool:
        with self._lock:
            return self._state == "unsubmitted"

    @property
    def inflight(self) -> bool:
        with self._lock:
            return self._state == "inflight"

    @property
    def owned(self) -> bool:
        with self._lock:
            return self._state in {
                "committed-descriptor",
                "descriptor-owned",
                "unidentified-owned",
                "owned",
            }

    @property
    def committed(self) -> bool:
        with self._lock:
            return self._state in {"committed", "committed-descriptor"}

    def begin_write(self) -> bool:
        with self._lock:
            if self._state != "unsubmitted":
                return False
            self._state = "inflight"
            return True

    def publish_owned(
        self,
        directory_fd: int,
        file_fd: int,
        directory_identity: tuple[int, int],
        file_identity: tuple[int, int],
        name: str,
    ) -> bool:
        with self._lock:
            if (
                self._state != "inflight"
                or type(directory_fd) is not int
                or directory_fd < 0
                or type(file_fd) is not int
                or file_fd < 0
                or file_fd == directory_fd
                or not _valid_identity(directory_identity)
                or not _valid_identity(file_identity)
                or type(name) is not str
                or not name
                or "/" in name
                or name in {".", ".."}
            ):
                return False
            self._directory_fd = directory_fd
            self._directory_identity = directory_identity
            self._file_fd = file_fd
            self._file_identity = file_identity
            self._name = name
            self._state = "owned"
            return True

    def publish_directory_only(
        self,
        directory_fd: int,
        directory_identity: tuple[int, int] | None,
    ) -> bool:
        with self._lock:
            if (
                self._state != "inflight"
                or type(directory_fd) is not int
                or directory_fd < 0
                or (directory_identity is not None and not _valid_identity(directory_identity))
            ):
                return False
            self._directory_fd = directory_fd
            self._directory_identity = directory_identity
            self._state = "descriptor-owned"
            return True

    def publish_unidentified_file(
        self,
        directory_fd: int,
        file_fd: int,
        directory_identity: tuple[int, int],
        name: str,
    ) -> bool:
        with self._lock:
            if (
                self._state != "inflight"
                or type(directory_fd) is not int
                or directory_fd < 0
                or type(file_fd) is not int
                or file_fd < 0
                or file_fd == directory_fd
                or not _valid_identity(directory_identity)
                or type(name) is not str
                or not name
                or "/" in name
                or name in {".", ".."}
            ):
                return False
            self._directory_fd = directory_fd
            self._directory_identity = directory_identity
            self._file_fd = file_fd
            self._name = name
            self._state = "unidentified-owned"
            return True

    def retain_file_descriptor(
        self,
        descriptor: int,
        identity: tuple[int, int],
    ) -> bool:
        with self._lock:
            if (
                self._state != "owned"
                or type(descriptor) is not int
                or descriptor < 0
                or identity != self._file_identity
                or descriptor == self._file_fd
                or descriptor in self._extra_file_fds
                or len(self._extra_file_fds) >= 2
            ):
                return False
            self._extra_file_fds = (*self._extra_file_fds, descriptor)
            return True

    def mark_unsubmitted_empty(self) -> bool:
        with self._lock:
            if self._state != "unsubmitted":
                return False
            self._state = "proven-empty"
            return True

    def mark_inflight_empty(self) -> bool:
        with self._lock:
            if self._state != "inflight":
                return False
            self._state = "proven-empty"
            return True

    def _settled(self) -> None:
        self._content_destroyed = False
        self._directory_fd = None
        self._directory_identity = None
        self._file_fd = None
        self._file_identity = None
        self._extra_file_fds = ()
        self._name = ""
        self._state = "settled"

    def _committed(self) -> None:
        self._content_destroyed = False
        self._directory_fd = None
        self._directory_identity = None
        self._file_fd = None
        self._file_identity = None
        self._extra_file_fds = ()
        self._name = ""
        self._state = "committed"

    def __repr__(self) -> str:
        return "PrivateFileCleanupReceipt()"


class PrivateDescriptorCleanupAuthority:
    """Opaque retry authority for unproved private file descriptors."""

    __slots__ = ("_descriptors", "_lock", "_state")

    def __init__(self, token: object) -> None:
        if token is not _DESCRIPTOR_TOKEN:
            raise TypeError("private-descriptor cleanup authority is factory-owned")
        self._descriptors: tuple[tuple[int, tuple[int, int] | None], ...] = ()
        self._lock = threading.Lock()
        self._state = "unsubmitted"

    def begin(self) -> bool:
        with self._lock:
            if self._state != "unsubmitted":
                return False
            self._state = "inflight"
            return True

    def publish(
        self,
        descriptors: tuple[tuple[int, tuple[int, int] | None], ...],
    ) -> bool:
        if (
            not descriptors
            or len(descriptors) > 4
            or any(
                type(descriptor) is not int
                or descriptor < 0
                or (identity is not None and not _valid_identity(identity))
                for descriptor, identity in descriptors
            )
        ):
            return False
        with self._lock:
            if self._state != "inflight":
                return False
            self._descriptors = descriptors
            self._state = "owned"
            return True

    def mark_unsubmitted_empty(self) -> bool:
        with self._lock:
            if self._state != "unsubmitted":
                return False
            self._state = "proven-empty"
            return True

    def mark_inflight_empty(self) -> bool:
        with self._lock:
            if self._state != "inflight":
                return False
            self._state = "proven-empty"
            return True

    def cleanup(
        self,
        *,
        initial_control: ControlSignal | None = None,
    ) -> tuple[bool, ControlSignal | None]:
        with self._lock:
            if self._state in {"proven-empty", "cleaned"}:
                self._descriptors = ()
                self._state = "cleaned"
                return False, initial_control
            if self._state != "owned":
                return True, initial_control
            failed, control, remaining = _close_private_descriptors_owned(
                self._descriptors,
                initial_control=initial_control,
            )
            self._descriptors = remaining
            if not failed:
                self._state = "cleaned"
            return failed, control

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state in {"inflight", "owned"}

    @property
    def owned(self) -> bool:
        with self._lock:
            return self._state == "owned"

    def __repr__(self) -> str:
        return "PrivateDescriptorCleanupAuthority()"


class _ReceiptTask:
    __slots__ = ("control", "done", "failed", "receipts", "release")

    def __init__(
        self,
        receipts: tuple[PrivateFileCleanupReceipt, ...],
        *,
        initial_control: ControlSignal | None,
        release: bool,
    ) -> None:
        self.receipts = receipts
        self.control = TlsControlLatch(initial_control)
        self.done = threading.Event()
        self.failed = False
        self.release = release

    def run(self) -> None:
        receipts = self.receipts
        try:
            operation = _release_cleanup_receipt if self.release else _settle_cleanup_receipt
            for receipt in receipts:
                if not operation(receipt, self.control):
                    self.failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        finally:
            receipts = ()
            self.receipts = ()
            self.done.set()

    def discard(self) -> None:
        self.receipts = ()

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        self.discard()
        self.done.set()


class _DescriptorTask:
    __slots__ = ("control", "descriptors", "done", "failed")

    def __init__(
        self,
        descriptors: tuple[tuple[int, tuple[int, int] | None], ...],
        initial_control: ControlSignal | None,
    ) -> None:
        self.control = TlsControlLatch(initial_control)
        self.descriptors = descriptors
        self.done = threading.Event()
        self.failed = False

    def run(self) -> None:
        remaining = list(self.descriptors)
        try:
            index = 0
            while index < len(remaining):
                descriptor, identity = remaining[index]
                if close_owned_descriptor(descriptor, identity, self.control):
                    remaining.pop(index)
                else:
                    index += 1
        except (KeyboardInterrupt, SystemExit) as error:
            self.control.record_error(error)
            self.failed = True
        except BaseException:
            self.failed = True
        finally:
            self.descriptors = tuple(remaining)
            self.failed = bool(self.failed or remaining)
            self.done.set()

    def owner_failed(self, control: ControlSignal | None) -> None:
        if control is not None:
            self.control.record(control)
        self.failed = True
        self.done.set()


def new_private_file_cleanup_receipt() -> PrivateFileCleanupReceipt:
    """Pre-register an empty authority slot before publishing private bytes."""

    return PrivateFileCleanupReceipt(_RECEIPT_TOKEN)


def new_private_descriptor_cleanup_authority() -> PrivateDescriptorCleanupAuthority:
    return PrivateDescriptorCleanupAuthority(_DESCRIPTOR_TOKEN)


def settle_private_file_receipts_owned(
    receipts: tuple[PrivateFileCleanupReceipt, ...],
    *,
    initial_control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    """Remove exact-inode authorities in an owner worker."""

    return _operate_private_file_receipts_owned(
        receipts,
        initial_control=initial_control,
        release=False,
    )


def release_private_file_receipt_owned(
    receipt: PrivateFileCleanupReceipt,
    *,
    initial_control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    """Close one internal receipt after an exception-free write handoff."""

    return _operate_private_file_receipts_owned(
        (receipt,),
        initial_control=initial_control,
        release=True,
    )


def _operate_private_file_receipts_owned(
    receipts: tuple[PrivateFileCleanupReceipt, ...],
    *,
    initial_control: ControlSignal | None,
    release: bool,
) -> tuple[bool, ControlSignal | None]:

    if not receipts:
        return False, initial_control
    if len(receipts) > 8 or any(
        type(receipt) is not PrivateFileCleanupReceipt for receipt in receipts
    ):
        return True, initial_control
    service = TlsOwnerService()
    control: ControlSignal | None = None
    task: _ReceiptTask | None = None
    failed = False
    try:
        started, control = start_tls_owner_service(service)
        if not started:
            receipts = ()
            service = None
            return True, initial_control or control
        task = _ReceiptTask(
            receipts,
            initial_control=initial_control,
            release=release,
        )
        receipts = ()
        failed = not service.execute(task)
    except (KeyboardInterrupt, SystemExit) as error:
        receipts = ()
        if task is not None:
            task.control.record_error(error)
            failed = not task.done.is_set() and not service.execute(task)
        else:
            control = service.abort(initial_control or sanitize_control(error))
            service = None
            return True, control
    except BaseException:
        receipts = ()
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


def _close_private_descriptors_owned(
    descriptors: tuple[tuple[int, tuple[int, int] | None], ...],
    *,
    initial_control: ControlSignal | None,
) -> tuple[
    bool,
    ControlSignal | None,
    tuple[tuple[int, tuple[int, int] | None], ...],
]:
    service = TlsOwnerService()
    task: _DescriptorTask | None = None
    control: ControlSignal | None = None
    try:
        started, control = start_tls_owner_service(service)
        if not started:
            service = None
            return True, initial_control or control, descriptors
        task = _DescriptorTask(descriptors, initial_control)
        if not service.execute(task):
            task.failed = True
    except (KeyboardInterrupt, SystemExit) as error:
        if task is not None:
            task.control.record_error(error)
            if not task.done.is_set() and not service.execute(task):
                task.failed = True
        else:
            control = service.abort(initial_control or sanitize_control(error))
            service = None
            return True, control, descriptors
    except BaseException:
        if task is None:
            control = service.abort(initial_control)
            service = None
            return True, control, descriptors
        task.failed = True
    failed = task is None or task.failed
    if task is None:
        return True, control, descriptors
    remaining = task.descriptors
    control = task.control.value()
    task.descriptors = ()
    task = service = None
    return failed, control, remaining


def _release_cleanup_receipt(
    receipt: PrivateFileCleanupReceipt,
    control: TlsControlLatch,
) -> bool:
    with receipt._lock:
        if receipt._state != "owned" or control.value() is not None:
            return False
        directory_fd = receipt._directory_fd
        directory_identity = receipt._directory_identity
        file_fd = receipt._file_fd
        file_identity = receipt._file_identity
        extra_file_fds = receipt._extra_file_fds
        if (
            type(directory_fd) is not int
            or directory_identity is None
            or type(file_fd) is not int
            or file_identity is None
        ):
            return False
        remaining = tuple(
            descriptor
            for descriptor in extra_file_fds
            if not close_owned_descriptor(descriptor, file_identity, control)
        )
        receipt._extra_file_fds = remaining
        # Keep both exact authorities until auxiliary descriptors are gone.
        if remaining or control.value() is not None:
            return False
        if not close_owned_descriptor(file_fd, file_identity, control):
            return False
        # Untracked writes commit when their exact writable fd is proven closed.
        # Later control/dir-fd failures carry a committed marker, never promise rollback.
        receipt._file_fd = None
        receipt._state = "committed-descriptor"
        if control.value() is not None:
            return False
        if not close_owned_descriptor(directory_fd, directory_identity, control):
            return False
        receipt._directory_fd = None
        receipt._directory_identity = None
        receipt._committed()
        return True


def _settle_cleanup_receipt(
    receipt: PrivateFileCleanupReceipt,
    control: TlsControlLatch,
) -> bool:
    with receipt._lock:
        if receipt._state == "unsubmitted":
            if not receipt.mark_unsubmitted_empty():
                return False
        if receipt._state in {"proven-empty", "settled"}:
            receipt._settled()
            return True
        if receipt._state == "committed":
            receipt._settled()
            return True
        if receipt._state == "unidentified-owned":
            file_fd = receipt._file_fd
            if type(file_fd) is not int:
                return False
            file_identity = identify_owned_descriptor(file_fd, control)
            if file_identity is None:
                return False
            receipt._file_identity = file_identity
            receipt._state = "owned"
        if receipt._state in {"committed-descriptor", "descriptor-owned"}:
            directory_fd = receipt._directory_fd
            if type(directory_fd) is not int:
                return False
            if not close_owned_descriptor(
                directory_fd,
                receipt._directory_identity,
                control,
            ):
                return False
            receipt._settled()
            return True
        if receipt._state != "owned":
            return False
        directory_fd = receipt._directory_fd
        directory_identity = receipt._directory_identity
        content_destroyed = receipt._content_destroyed
        file_fd = receipt._file_fd
        file_identity = receipt._file_identity
        extra_file_fds = receipt._extra_file_fds
        name = receipt._name
        if file_identity is None or not name:
            return False
        destroyed = content_destroyed
        if not destroyed:
            if type(file_fd) is not int:
                return False
            destroyed = destroy_owned_file(file_fd, file_identity, control)
            if destroyed:
                receipt._content_destroyed = True
                receipt._file_fd = None
        file_fd = None
        if not destroyed:
            directory_fd = None
            directory_identity = file_identity = None
            name = ""
            return False
        remaining = tuple(
            descriptor
            for descriptor in extra_file_fds
            if not close_owned_descriptor(descriptor, file_identity, control)
        )
        receipt._extra_file_fds = remaining
        if remaining:
            return False
        if directory_fd is None and receipt._directory_fd is None:
            receipt._settled()
            return True
        if type(directory_fd) is not int or directory_identity is None:
            return False
        removed = remove_owned_inode(directory_fd, name, file_identity, control)
        if not removed:
            directory_fd = None
            directory_identity = file_identity = None
            name = ""
            return False
        closed = close_owned_descriptor(directory_fd, directory_identity, control)
        if removed and closed:
            receipt._settled()
        directory_fd = None
        directory_identity = file_identity = None
        name = ""
        return destroyed and removed and closed


def _valid_identity(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and all(type(part) is int and part >= 0 for part in value)
    )


def settle_private_file_receipt_in_owner(
    receipt: PrivateFileCleanupReceipt,
    control: TlsControlLatch,
) -> bool:
    """Settle one published receipt inside its current owner worker."""

    if type(receipt) is not PrivateFileCleanupReceipt:
        return False
    return _settle_cleanup_receipt(receipt, control)


__all__ = [
    "PrivateDescriptorCleanupAuthority",
    "PrivateFileCleanupReceipt",
    "new_private_descriptor_cleanup_authority",
    "new_private_file_cleanup_receipt",
    "release_private_file_receipt_owned",
    "settle_private_file_receipt_in_owner",
    "settle_private_file_receipts_owned",
]

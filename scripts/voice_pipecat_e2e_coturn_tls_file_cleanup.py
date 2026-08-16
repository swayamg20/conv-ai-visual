"""Identity-bound descriptor and path cleanup for Coturn TLS files."""

from __future__ import annotations

import errno
import os
import stat
import threading
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_host import (
    require_owned_directory,
    require_safe_path,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch

_MAX_DIRECTORY_ENTRIES = 256
_MAX_AMBIGUOUS_DESCRIPTORS = 64
_AMBIGUOUS_LOCK = threading.Lock()
_AMBIGUOUS_DESCRIPTORS: dict[int, tuple[int, int] | None] = {}
_AMBIGUOUS_POISONED = False
_MISSING = object()


def remove_owned_path(path: Path, control: TlsControlLatch) -> bool:
    """Remove one bound path without allowing controls to consume retries."""

    while True:
        try:
            return _remove_path_once(path, control)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return False


def remove_owned_inode(
    directory_fd: int,
    expected_name: str,
    identity: tuple[int, int],
    control: TlsControlLatch,
) -> bool:
    """Remove only the created inode, scanning a bounded bound directory."""

    removals = 0
    while removals <= _MAX_DIRECTORY_ENTRIES:
        names = _list_directory(directory_fd, control)
        if names is None or len(names) > _MAX_DIRECTORY_ENTRIES:
            return False
        ordered = (expected_name, *(name for name in names if name != expected_name))
        found = False
        removed = False
        for name in ordered:
            if type(name) is not str or not name or "/" in name or name in {".", ".."}:
                continue
            details = _stat_without_control(directory_fd, name, control)
            if details is None or file_identity(details) != identity:
                continue
            found = True
            if not _unlink_matching_name(directory_fd, name, identity, control):
                continue
            removals += 1
            removed = True
            break
        if not found:
            return True
        if not removed:
            return False
    return False


def close_owned_descriptor(
    descriptor: int,
    identity: tuple[int, int] | None,
    control: TlsControlLatch,
) -> bool:
    """Close once; quarantine ordinary ambiguous outcomes instead of retrying an fd."""

    quarantine = _quarantine_status(descriptor, identity, control)
    if quarantine == "resolved":
        return True
    if quarantine != "clear":
        return False
    state = _descriptor_state(descriptor, identity, control)
    if state in {"closed", "reused"}:
        return True
    if state != "owned":
        return False
    try:
        os.close(descriptor)
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
    except BaseException:
        _quarantine_descriptor(descriptor, identity, control)
        return _quarantine_status(descriptor, identity, control) == "resolved"
    else:
        return True
    state = _descriptor_state(descriptor, identity, control)
    if state in {"closed", "reused"}:
        return True
    if state != "owned":
        return False
    while True:
        try:
            os.closerange(descriptor, descriptor + 1)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            state = _descriptor_state(descriptor, identity, control)
            if state in {"closed", "reused"}:
                return True
            if state != "owned":
                return False
        except BaseException:
            return False
    return _descriptor_state(descriptor, identity, control) in {"closed", "reused"}


def _quarantine_descriptor(
    descriptor: int,
    identity: tuple[int, int] | None,
    control: TlsControlLatch,
) -> None:
    global _AMBIGUOUS_POISONED
    while True:
        try:
            with _AMBIGUOUS_LOCK:
                if descriptor in _AMBIGUOUS_DESCRIPTORS:
                    return
                if len(_AMBIGUOUS_DESCRIPTORS) >= _MAX_AMBIGUOUS_DESCRIPTORS:
                    _AMBIGUOUS_POISONED = True
                else:
                    _AMBIGUOUS_DESCRIPTORS[descriptor] = identity
                return
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)


def _quarantine_status(
    descriptor: int,
    identity: tuple[int, int] | None,
    control: TlsControlLatch,
) -> str:
    while True:
        try:
            with _AMBIGUOUS_LOCK:
                if _AMBIGUOUS_POISONED:
                    return "poisoned"
                expected = _AMBIGUOUS_DESCRIPTORS.get(descriptor, _MISSING)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
    if expected is _MISSING:
        return "clear"
    state = _descriptor_state(descriptor, expected, control)
    if state not in {"closed", "reused"}:
        return "ambiguous"
    while True:
        try:
            with _AMBIGUOUS_LOCK:
                if _AMBIGUOUS_DESCRIPTORS.get(descriptor, _MISSING) is expected:
                    _AMBIGUOUS_DESCRIPTORS.pop(descriptor, None)
            return "resolved" if identity == expected else "clear"
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)


def destroy_owned_file(
    descriptor: int,
    identity: tuple[int, int],
    control: TlsControlLatch,
) -> bool:
    """Destroy bytes through the retained exact file descriptor, then close it."""

    while True:
        try:
            before = os.fstat(descriptor)
            if file_identity(before) != identity:
                return False
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            if file_identity(after) != identity or after.st_size != 0:
                return False
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return False
    return close_owned_descriptor(descriptor, identity, control)


def identify_owned_descriptor(
    descriptor: int,
    control: TlsControlLatch,
) -> tuple[int, int] | None:
    """Acquire a stable inode identity without letting controls consume the attempt."""

    while True:
        try:
            return file_identity(os.fstat(descriptor))
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return None


def file_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _remove_path_once(path: Path, control: TlsControlLatch) -> bool:
    directory_fd: int | None = None
    directory_identity: tuple[int, int] | None = None
    removed = False
    try:
        require_safe_path(path)
        require_owned_directory(path.parent)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(path.parent, flags)
        details = os.fstat(directory_fd)
        directory_identity = file_identity(details)
        named_parent = path.parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o700
            or file_identity(named_parent) != directory_identity
        ):
            raise OSError
        try:
            named = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            removed = True
        else:
            removed = remove_owned_inode(
                directory_fd,
                path.name,
                file_identity(named),
                control,
            )
    except (KeyboardInterrupt, SystemExit):
        if directory_fd is not None:
            close_owned_descriptor(directory_fd, directory_identity, control)
        raise
    except BaseException:
        pass
    if directory_fd is not None and not close_owned_descriptor(
        directory_fd, directory_identity, control
    ):
        removed = False
    return removed


def _unlink_matching_name(
    directory_fd: int,
    name: str,
    identity: tuple[int, int],
    control: TlsControlLatch,
) -> bool:
    ordinary_failures = 0
    while ordinary_failures < 2:
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if file_identity(current) != identity:
                return False
            os.unlink(name, dir_fd=directory_fd)
            return True
        except FileNotFoundError:
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            ordinary_failures += 1
    return False


def _directory_contains_inode(
    directory_fd: int,
    identity: tuple[int, int],
    control: TlsControlLatch,
) -> bool:
    names = _list_directory(directory_fd, control)
    if names is None or len(names) > _MAX_DIRECTORY_ENTRIES:
        return True
    for name in names:
        details = _stat_without_control(directory_fd, name, control)
        if details is not None and file_identity(details) == identity:
            return True
    return False


def _list_directory(
    directory_fd: int,
    control: TlsControlLatch,
) -> tuple[str, ...] | None:
    iterator = None
    while True:
        try:
            iterator = os.scandir(directory_fd)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return None
    names: list[str] = []
    result: tuple[str, ...] | None = None
    while result is None:
        try:
            entry = next(iterator)
            name = entry.name
        except StopIteration:
            result = tuple(names)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            continue
        except BaseException:
            break
        if type(name) is not str:
            break
        names.append(name)
        if len(names) > _MAX_DIRECTORY_ENTRIES:
            result = tuple(names)
    while True:
        try:
            iterator.close()
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            result = None
            break
    names.clear()
    return result


def _stat_without_control(
    directory_fd: int,
    name: str,
    control: TlsControlLatch,
) -> os.stat_result | None:
    while True:
        try:
            return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return None


def _descriptor_state(
    descriptor: int,
    identity: tuple[int, int] | None,
    control: TlsControlLatch,
) -> str:
    while True:
        try:
            current = os.fstat(descriptor)
        except OSError as error:
            return "closed" if error.errno == errno.EBADF else "unknown"
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            continue
        except BaseException:
            return "unknown"
        if identity is None:
            return "owned"
        return "owned" if file_identity(current) == identity else "reused"


__all__ = [
    "close_owned_descriptor",
    "destroy_owned_file",
    "file_identity",
    "identify_owned_descriptor",
    "remove_owned_inode",
    "remove_owned_path",
]

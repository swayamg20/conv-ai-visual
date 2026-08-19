"""Bounded no-follow filesystem primitives for relay browser artifacts."""

from __future__ import annotations

import os
import stat
import traceback
from collections.abc import Callable
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
    file_identity,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch

MAX_ENTRIES = 256
MAX_DEPTH = 6
MAX_TOTAL_BYTES = 16 * 1_048_576
MAX_RESULT_BYTES = 1_048_576
MAX_REPORT_BYTES = 16_384
MAX_LOG_BYTES = 1_048_576
_READ_CHUNK = 65_536


class ArtifactBudget:
    __slots__ = ("bytes", "entries")

    def __init__(self) -> None:
        self.entries = 0
        self.bytes = 0

    def add(self, details: os.stat_result) -> bool:
        self.entries += 1
        if stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
            self.bytes += max(0, details.st_size)
        return self.entries <= MAX_ENTRIES and self.bytes <= MAX_TOTAL_BYTES


class ArtifactNode:
    __slots__ = (
        "borrowed_descriptor",
        "children",
        "closed",
        "content",
        "descriptor",
        "details",
        "identity",
        "missing_ambiguous",
        "name",
        "parent_descriptor",
        "parent_synced",
        "quarantine_name",
        "removal_started",
        "removed",
        "valid",
    )

    def __init__(
        self,
        *,
        parent_descriptor: int,
        name: str,
        details: os.stat_result,
    ) -> None:
        self.parent_descriptor = parent_descriptor
        self.name = name
        self.details = details
        self.identity = file_identity(details)
        self.missing_ambiguous = False
        self.descriptor: int | None = None
        self.borrowed_descriptor = False
        self.children: list[ArtifactNode] = []
        self.content = b""
        self.valid = True
        self.removed = False
        self.removal_started = False
        self.parent_synced = False
        self.quarantine_name: str | None = None
        self.closed = False


def capture_node(
    parent_fd: int,
    name: str,
    budget: ArtifactBudget,
    latch: TlsControlLatch,
    *,
    sink: Callable[[ArtifactNode], bool],
    depth: int,
    existing_directory_descriptor: int | None = None,
    read_limit: int | None = None,
) -> bool:
    node: ArtifactNode | None = None
    borrowed = False
    published = False
    try:
        if depth > MAX_DEPTH or not safe_name(name):
            return False
        details = stat_name(parent_fd, name, latch)
        if details is None or not budget.add(details):
            return False
        node = ArtifactNode(parent_descriptor=parent_fd, name=name, details=details)
        mode = details.st_mode
        node.valid = details.st_uid == os.geteuid()
        if stat.S_ISDIR(mode):
            borrowed = existing_directory_descriptor is not None
            if borrowed:
                if not verify_open_directory(existing_directory_descriptor, details, latch):
                    return False
                node.descriptor = existing_directory_descriptor
                node.borrowed_descriptor = True
            elif not open_child_directory(
                parent_fd,
                name,
                latch,
                exact_private=False,
                sink=lambda descriptor, identity: _adopt_node_descriptor(
                    node,
                    descriptor,
                    identity,
                ),
            ):
                return False
            node.valid = bool(node.valid and stat.S_IMODE(mode) & 0o022 == 0)
            names = list_names(node.descriptor, latch)
            if names is None:
                return False
            for child_name in names:
                if not capture_node(
                    node.descriptor,
                    child_name,
                    budget,
                    latch,
                    sink=lambda value: _append_node(node.children, value),
                    depth=depth + 1,
                ):
                    return False
                node.valid = node.valid and node.children[-1].valid
        elif stat.S_ISREG(mode):
            node.valid = bool(
                node.valid and details.st_nlink == 1 and stat.S_IMODE(mode) & 0o022 == 0
            )
            if not open_regular(
                parent_fd,
                name,
                details,
                latch,
                sink=lambda descriptor, identity: _adopt_node_descriptor(
                    node,
                    descriptor,
                    identity,
                ),
            ):
                return False
            if read_limit is not None:
                content = read_exact(node, read_limit, latch)
                if content is None:
                    return False
                node.content = content
        elif stat.S_ISLNK(mode):
            node.valid = False
        else:
            node.valid = False
        published = sink(node)
        return published
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False
    except BaseException as error:
        scrub_exception(error)
        return False
    finally:
        if not published:
            if node is not None:
                clear_tree_content(node)
                close_tree_descriptors(node, latch)


def _adopt_node_descriptor(
    node: ArtifactNode,
    descriptor: int,
    identity: tuple[int, int],
) -> bool:
    if identity != node.identity:
        return False
    if node.descriptor is None:
        node.descriptor = descriptor
        return True
    return node.descriptor == descriptor


def _append_node(nodes: list[ArtifactNode], node: ArtifactNode) -> bool:
    if all(current is not node for current in nodes):
        nodes.append(node)
    return True


def capture_optional_log(
    run_fd: int,
    budget: ArtifactBudget,
    latch: TlsControlLatch,
    *,
    sink: Callable[[ArtifactNode], bool],
) -> str:
    status, details = stat_name_status(run_fd, "playwright.log", latch)
    if status == "absent":
        return "absent"
    if status != "found" or details is None:
        return "error"
    captured: list[ArtifactNode] = []
    published = False
    try:
        if not capture_node(
            run_fd,
            "playwright.log",
            budget,
            latch,
            sink=lambda node: _append_node(captured, node),
            depth=0,
            read_limit=None,
        ):
            return "error"
        node = captured[0]
        mode = stat.S_IMODE(node.details.st_mode)
        node.valid = bool(
            node.valid
            and stat.S_ISREG(node.details.st_mode)
            and node.details.st_uid == os.geteuid()
            and node.details.st_nlink == 1
            and mode in {0o600, 0o640, 0o644}
            and node.details.st_size <= MAX_LOG_BYTES
        )
        published = sink(node)
        return "found" if published else "error"
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return "error"
    except BaseException as error:
        scrub_exception(error)
        return "error"
    finally:
        if not published:
            close_unpublished_nodes(captured, latch)


def read_exact(node: ArtifactNode, maximum: int, latch: TlsControlLatch) -> bytes | None:
    descriptor = node.descriptor
    if descriptor is None or not 1 <= maximum <= MAX_RESULT_BYTES:
        return None
    chunks: list[bytes] = []
    length = 0
    try:
        while True:
            try:
                chunk = os.read(descriptor, min(_READ_CHUNK, maximum + 1 - length))
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
                continue
            except BaseException as error:
                scrub_exception(error)
                return None
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum:
                return None
        after = fstat(descriptor, latch)
        named = stat_name(node.parent_descriptor, node.name, latch)
        if (
            after is None
            or named is None
            or file_identity(after) != node.identity
            or file_identity(named) != node.identity
            or after.st_size != length
            or named.st_size != length
        ):
            return None
        return b"".join(chunks)
    finally:
        for index in range(len(chunks)):
            chunks[index] = b""
        chunks.clear()


def close_unpublished_nodes(nodes: list[ArtifactNode], latch: TlsControlLatch) -> bool:
    complete = True
    for node in reversed(nodes):
        clear_tree_content(node)
        complete = close_tree_descriptors(node, latch) and complete
    return complete


def close_tree_descriptors(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    complete = True
    for child in node.children:
        complete = close_tree_descriptors(child, latch) and complete
    return close_node_descriptor(node, latch) and complete


def close_node_descriptor(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    if node.closed or node.descriptor is None:
        node.closed = True
        return True
    if node.borrowed_descriptor:
        return True
    if not close_owned_descriptor(node.descriptor, node.identity, latch):
        return False
    node.descriptor = None
    node.closed = True
    return True


def clear_tree_content(node: ArtifactNode) -> None:
    node.content = b""
    for child in node.children:
        clear_tree_content(child)


def tree_has_ambiguous_missing(node: ArtifactNode) -> bool:
    return node.missing_ambiguous or any(
        tree_has_ambiguous_missing(child) for child in node.children
    )


def exact_private_file(node: ArtifactNode) -> bool:
    return bool(
        stat.S_ISREG(node.details.st_mode)
        and node.details.st_uid == os.geteuid()
        and node.details.st_nlink == 1
        and stat.S_IMODE(node.details.st_mode) == 0o600
    )


def open_exact_directory(
    path: Path,
    latch: TlsControlLatch,
    *,
    sink: Callable[[int, tuple[int, int]], bool],
) -> bool:
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    adopted = False
    try:
        while descriptor is None:
            try:
                descriptor = os.open(path, directory_flags())
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
            except BaseException as error:
                scrub_exception(error)
                return False
        details = fstat(descriptor, latch)
        named = path_stat(path, latch)
        if not safe_private_directory(details) or not safe_private_directory(named):
            return False
        identity = file_identity(details)
        if identity != file_identity(named):
            return False
        adopted = sink(descriptor, identity)
        return adopted
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False
    except BaseException as error:
        scrub_exception(error)
        return False
    finally:
        if descriptor is not None and not adopted:
            close_owned_descriptor(descriptor, identity, latch)


def open_child_directory(
    parent_fd: int,
    name: str,
    latch: TlsControlLatch,
    *,
    exact_private: bool = True,
    sink: Callable[[int, tuple[int, int]], bool],
) -> bool:
    return _open_at_exact(
        parent_fd,
        name,
        directory_flags(),
        latch,
        directory=True,
        exact_private_directory=exact_private,
        expected=None,
        sink=sink,
    )


def verify_open_directory(
    descriptor: int,
    expected: os.stat_result,
    latch: TlsControlLatch,
) -> bool:
    details = fstat(descriptor, latch)
    if (
        details is None
        or not stat.S_ISDIR(details.st_mode)
        or file_identity(details) != file_identity(expected)
    ):
        return False
    return True


def open_regular(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    latch: TlsControlLatch,
    *,
    sink: Callable[[int, tuple[int, int]], bool],
) -> bool:
    return _open_at_exact(
        parent_fd,
        name,
        read_flags(),
        latch,
        directory=False,
        exact_private_directory=False,
        expected=expected,
        sink=sink,
    )


def _open_at_exact(
    parent_fd: int,
    name: str,
    flags: int,
    latch: TlsControlLatch,
    *,
    directory: bool,
    exact_private_directory: bool,
    expected: os.stat_result | None,
    sink: Callable[[int, tuple[int, int]], bool],
) -> bool:
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    adopted = False
    try:
        while descriptor is None:
            try:
                descriptor = os.open(name, flags, dir_fd=parent_fd)
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
            except BaseException as error:
                scrub_exception(error)
                return False
        details = fstat(descriptor, latch)
        named = stat_name(parent_fd, name, latch)
        if details is None or named is None:
            return False
        identity = file_identity(details)
        if identity != file_identity(named) or (
            expected is not None and identity != file_identity(expected)
        ):
            return False
        if directory:
            if not _safe_owned_directory(
                details,
                exact_private=exact_private_directory,
            ) or not _safe_owned_directory(
                named,
                exact_private=exact_private_directory,
            ):
                return False
        elif not stat.S_ISREG(details.st_mode):
            return False
        adopted = sink(descriptor, identity)
        return adopted
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False
    except BaseException as error:
        scrub_exception(error)
        return False
    finally:
        if descriptor is not None and not adopted:
            close_owned_descriptor(descriptor, identity, latch)


def mkdir_exact(
    parent_fd: int,
    name: str,
    latch: TlsControlLatch,
    *,
    created_sink: Callable[[], bool],
    identity_sink: Callable[[tuple[int, int]], bool],
) -> bool:
    created = False
    while True:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = created_sink()
            break
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            status, details = stat_name_status(parent_fd, name, latch)
            if status == "found" and safe_private_directory(details):
                created = created_sink()
                return bool(created and identity_sink(file_identity(details)))
            if status != "absent":
                return False
        except BaseException as error:
            scrub_exception(error)
            return False
    if not created:
        return False
    status, details = stat_name_status(parent_fd, name, latch)
    if status != "found" or not safe_private_directory(details):
        return False
    return identity_sink(file_identity(details))


def named_directory_matches(
    parent_fd: int,
    name: str,
    identity: tuple[int, int] | None,
    latch: TlsControlLatch,
) -> bool:
    details = stat_name(parent_fd, name, latch)
    return bool(
        identity is not None
        and safe_private_directory(details)
        and file_identity(details) == identity
    )


def name_absent(parent_fd: int, name: str, latch: TlsControlLatch) -> bool:
    while True:
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            continue
        except BaseException as error:
            scrub_exception(error)
            return False
        return False


def stat_name(
    parent_fd: int,
    name: str,
    latch: TlsControlLatch,
) -> os.stat_result | None:
    while True:
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return None


def stat_name_status(
    parent_fd: int,
    name: str,
    latch: TlsControlLatch,
) -> tuple[str, os.stat_result | None]:
    """Distinguish a proven absent entry from an unproved stat failure."""

    while True:
        try:
            return "found", os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent", None
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return "error", None


def path_stat(path: Path, latch: TlsControlLatch) -> os.stat_result | None:
    while True:
        try:
            return path.stat(follow_symlinks=False)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return None


def fstat(descriptor: int, latch: TlsControlLatch) -> os.stat_result | None:
    while True:
        try:
            return os.fstat(descriptor)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return None


def list_names(descriptor: int, latch: TlsControlLatch) -> tuple[str, ...] | None:
    while True:
        try:
            names: list[str] = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    name = entry.name
                    if len(names) == MAX_ENTRIES or not safe_name(name):
                        return None
                    names.append(name)
            return tuple(sorted(names))
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return None


def sync_directory(descriptor: int, latch: TlsControlLatch) -> bool:
    while True:
        try:
            os.fsync(descriptor)
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return False


def safe_private_directory(details: os.stat_result | None) -> bool:
    return bool(
        details is not None
        and stat.S_ISDIR(details.st_mode)
        and details.st_uid == os.geteuid()
        and stat.S_IMODE(details.st_mode) == 0o700
    )


def _safe_owned_directory(
    details: os.stat_result | None,
    *,
    exact_private: bool,
) -> bool:
    return bool(
        details is not None
        and stat.S_ISDIR(details.st_mode)
        and details.st_uid == os.geteuid()
        and (stat.S_IMODE(details.st_mode) == 0o700 if exact_private else True)
    )


def safe_name(name: object) -> bool:
    return bool(type(name) is str and name not in {"", ".", ".."} and "/" not in name)


def directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def read_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def scrub_exception(error: BaseException) -> None:
    traceback.clear_frames(error.__traceback__)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    error.__dict__.clear()
    error.args = ()

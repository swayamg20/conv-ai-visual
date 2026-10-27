"""Exact-inode removal transactions for captured relay browser artifacts.

Directory quarantine assumes no hostile same-UID process mutates the retained
private parent after the verified rename; Python/macOS expose no stronger
portable retained-directory unlink receipt.
"""

from __future__ import annotations

import os
import secrets
import stat

from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import (
    close_owned_descriptor,
    file_identity,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch
from scripts.voice_pipecat_e2e_relay_artifact_files import (
    ArtifactNode,
    close_node_descriptor,
    fstat,
    list_names,
    open_child_directory,
    safe_name,
    scrub_exception,
    stat_name_status,
    sync_directory,
)

_QUARANTINE_PREFIX = ".relay-owned-delete-"
_QUARANTINE_ATTEMPTS = 8


def remove_node(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    """Remove one captured graph without trusting a mutable pathname."""

    if node.removed:
        return _finish_removed_node(node, latch)
    if stat.S_ISDIR(node.details.st_mode):
        if _quarantined_entry_is_absent(node, latch):
            return _finish_removed_node(node, latch)
        if not _quarantine_directory(node, latch):
            return False
        for child in node.children:
            if not remove_node(child, latch):
                return False
        if node.descriptor is None or list_names(node.descriptor, latch) != ():
            return False
        if not _remove_quarantined_directory(node, latch):
            return False
    elif stat.S_ISREG(node.details.st_mode):
        for child in node.children:
            if not remove_node(child, latch):
                return False
        if not _remove_regular_file(node, latch):
            return False
    else:
        if not _remove_nonregular_entry(node, latch):
            return False
    return _finish_removed_node(node, latch)


def _finish_removed_node(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    if not node.parent_synced:
        if not sync_directory(node.parent_descriptor, latch):
            return False
        node.parent_synced = True
    return close_node_descriptor(node, latch)


def _remove_regular_file(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    while True:
        matches = _identity_matches(node.parent_descriptor, node.identity, latch)
        if matches is None:
            return False
        if not matches:
            return _prove_regular_unlinked(node, latch)
        if len(matches) != 1:
            return False
        name, current = matches[0]
        node.missing_ambiguous = False
        if not _regular_is_unchanged(node, current, latch):
            return False
        try:
            node.removal_started = True
            os.unlink(name, dir_fd=node.parent_descriptor)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            continue
        except BaseException as error:
            scrub_exception(error)
            return False
        return _prove_regular_unlinked(node, latch)


def _remove_nonregular_entry(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    while True:
        if _quarantined_entry_is_absent(node, latch):
            return True
        matches = _identity_matches(node.parent_descriptor, node.identity, latch)
        if matches is None or len(matches) != 1:
            node.missing_ambiguous = True
            return False
        current_name, _ = matches[0]
        quarantine = node.quarantine_name
        if quarantine is None:
            quarantine = _fresh_quarantine_name(node.parent_descriptor, latch)
            if quarantine is None:
                return False
            node.quarantine_name = quarantine
        if current_name != quarantine:
            status, details = stat_name_status(node.parent_descriptor, quarantine, latch)
            if status != "absent" or details is not None:
                return False
            try:
                os.rename(
                    current_name,
                    quarantine,
                    src_dir_fd=node.parent_descriptor,
                    dst_dir_fd=node.parent_descriptor,
                )
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
                continue
            except BaseException as error:
                scrub_exception(error)
                return False
        status, details = stat_name_status(node.parent_descriptor, quarantine, latch)
        original_status, _ = stat_name_status(node.parent_descriptor, node.name, latch)
        if (
            status != "found"
            or details is None
            or file_identity(details) != node.identity
            or (node.name != quarantine and original_status != "absent")
            or not sync_directory(node.parent_descriptor, latch)
        ):
            node.missing_ambiguous = True
            return False
        try:
            node.removal_started = True
            os.unlink(quarantine, dir_fd=node.parent_descriptor)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            continue
        except BaseException as error:
            scrub_exception(error)
            return False
        return _quarantined_entry_is_absent(node, latch)


def _prove_regular_unlinked(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    opened = None if node.descriptor is None else fstat(node.descriptor, latch)
    if opened is None or file_identity(opened) != node.identity or opened.st_nlink != 0:
        node.missing_ambiguous = True
        return False
    node.missing_ambiguous = False
    node.removed = True
    return True


def _regular_is_unchanged(
    node: ArtifactNode,
    current: os.stat_result,
    latch: TlsControlLatch,
) -> bool:
    opened = None if node.descriptor is None else fstat(node.descriptor, latch)
    return bool(
        opened is not None
        and file_identity(opened) == node.identity
        and _regular_metadata(opened) == _regular_metadata(node.details)
        and _regular_metadata(current) == _regular_metadata(node.details)
    )


def _quarantine_directory(node: ArtifactNode, latch: TlsControlLatch) -> bool:
    if node.descriptor is None:
        return False
    while True:
        matches = _identity_matches(node.parent_descriptor, node.identity, latch)
        if matches is None or len(matches) != 1:
            node.missing_ambiguous = True
            return False
        current_name, _ = matches[0]
        quarantine = node.quarantine_name
        if quarantine is None:
            quarantine = _fresh_quarantine_name(node.parent_descriptor, latch)
            if quarantine is None:
                return False
            node.quarantine_name = quarantine
        if current_name != quarantine:
            status, details = stat_name_status(node.parent_descriptor, quarantine, latch)
            if status != "absent" or details is not None:
                return False
            try:
                os.rename(
                    current_name,
                    quarantine,
                    src_dir_fd=node.parent_descriptor,
                    dst_dir_fd=node.parent_descriptor,
                )
            except (KeyboardInterrupt, SystemExit) as error:
                latch.record_error(error)
                scrub_exception(error)
                continue
            except BaseException as error:
                scrub_exception(error)
                return False
        status, details = stat_name_status(node.parent_descriptor, quarantine, latch)
        original_status, _ = stat_name_status(node.parent_descriptor, node.name, latch)
        if (
            status != "found"
            or details is None
            or file_identity(details) != node.identity
            or (node.name != quarantine and original_status != "absent")
            or not _verify_quarantine_descriptor(node, quarantine, latch)
        ):
            node.missing_ambiguous = True
            return False
        if not sync_directory(node.parent_descriptor, latch):
            return False
        node.missing_ambiguous = False
        return True


def _verify_quarantine_descriptor(
    node: ArtifactNode,
    quarantine: str,
    latch: TlsControlLatch,
) -> bool:
    opened: list[tuple[int, tuple[int, int]]] = []
    try:
        result = open_child_directory(
            node.parent_descriptor,
            quarantine,
            latch,
            exact_private=False,
            sink=lambda descriptor, identity: _retain_opened(
                opened,
                descriptor,
                identity,
            ),
        )
        if not result or len(opened) != 1:
            return False
        descriptor, identity = opened[0]
        retained = fstat(node.descriptor, latch)
        observed = fstat(descriptor, latch)
        return bool(
            identity == node.identity
            and retained is not None
            and observed is not None
            and file_identity(retained) == node.identity
            and file_identity(observed) == node.identity
        )
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False
    except BaseException as error:
        scrub_exception(error)
        return False
    finally:
        for descriptor, identity in opened:
            close_owned_descriptor(descriptor, identity, latch)


def _remove_quarantined_directory(
    node: ArtifactNode,
    latch: TlsControlLatch,
) -> bool:
    quarantine = node.quarantine_name
    if quarantine is None:
        return False
    while True:
        if _quarantined_entry_is_absent(node, latch):
            return True
        status, details = stat_name_status(node.parent_descriptor, quarantine, latch)
        if status != "found" or details is None or file_identity(details) != node.identity:
            node.missing_ambiguous = True
            return False
        try:
            node.removal_started = True
            os.rmdir(quarantine, dir_fd=node.parent_descriptor)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            continue
        except BaseException as error:
            scrub_exception(error)
            return False
        quarantine_status, _ = stat_name_status(
            node.parent_descriptor,
            quarantine,
            latch,
        )
        original_status, _ = stat_name_status(node.parent_descriptor, node.name, latch)
        if quarantine_status != "absent" or original_status != "absent":
            node.missing_ambiguous = True
            return False
        node.missing_ambiguous = False
        node.removed = True
        return True


def _quarantined_entry_is_absent(
    node: ArtifactNode,
    latch: TlsControlLatch,
) -> bool:
    quarantine = node.quarantine_name
    if quarantine is None or not node.removal_started:
        return False
    quarantine_status, _ = stat_name_status(node.parent_descriptor, quarantine, latch)
    original_status, _ = stat_name_status(node.parent_descriptor, node.name, latch)
    matches = _identity_matches(node.parent_descriptor, node.identity, latch)
    if quarantine_status != "absent" or original_status != "absent" or matches != []:
        return False
    node.missing_ambiguous = False
    node.removed = True
    return True


def _identity_matches(
    parent_fd: int,
    identity: tuple[int, int],
    latch: TlsControlLatch,
) -> list[tuple[str, os.stat_result]] | None:
    names = list_names(parent_fd, latch)
    if names is None:
        return None
    matches: list[tuple[str, os.stat_result]] = []
    for name in names:
        status, details = stat_name_status(parent_fd, name, latch)
        if status == "error":
            return None
        if details is not None and file_identity(details) == identity:
            matches.append((name, details))
    return matches


def _fresh_quarantine_name(parent_fd: int, latch: TlsControlLatch) -> str | None:
    for _ in range(_QUARANTINE_ATTEMPTS):
        name = f"{_QUARANTINE_PREFIX}{secrets.token_hex(16)}"
        if not safe_name(name):
            return None
        status, details = stat_name_status(parent_fd, name, latch)
        if status == "absent" and details is None:
            return name
        if status == "error":
            return None
    return None


def _retain_opened(
    opened: list[tuple[int, tuple[int, int]]],
    descriptor: int,
    identity: tuple[int, int],
) -> bool:
    if not opened:
        opened.append((descriptor, identity))
    return opened == [(descriptor, identity)]


def _regular_metadata(details: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        details.st_mode,
        details.st_uid,
        details.st_gid,
        details.st_nlink,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )

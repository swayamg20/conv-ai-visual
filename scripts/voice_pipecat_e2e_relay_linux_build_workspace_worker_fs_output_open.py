"""Private descriptor helpers shared by workspace build-output snapshots."""

from __future__ import annotations

import os
import stat

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_copy import (
    _hash_descriptor,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _open_regular_at,
    _require_named_identity,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerController,
)

_CHUNK_BYTES = 64 * 1024


def _read_output_regular(
    parent_fd: int,
    name: str,
    *,
    limit: int,
    expected_device: int,
    private: bool,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> tuple[bytes, _WorkspaceFilesystemIdentity]:
    if type(limit) is not int or limit < 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    child = _open_regular_at(parent_fd, name, descriptors)
    try:
        identity = _require_named_identity(parent_fd, name, child, directory=False)
        _require_output_regular(child, identity, expected_device, private=private)
        content = _read_descriptor(child, limit, controller)
        if len(content) != identity.size:
            raise _WorkspaceFilesystemError(_FAILURE)
        if _require_named_identity(parent_fd, name, child, directory=False) != identity:
            raise _WorkspaceFilesystemError(_FAILURE)
        return content, identity
    finally:
        if not descriptors.close(child):
            raise _WorkspaceFilesystemError(_FAILURE)


def _hash_output_regular(
    parent_fd: int,
    name: str,
    *,
    limit: int,
    expected_device: int,
    private: bool,
    descriptors: _WorkspaceDescriptorSet,
    controller: _WorkspaceWorkerController,
) -> tuple[bytes, _WorkspaceFilesystemIdentity]:
    if type(limit) is not int or limit < 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    child = _open_regular_at(parent_fd, name, descriptors)
    try:
        identity = _require_named_identity(parent_fd, name, child, directory=False)
        _require_output_regular(child, identity, expected_device, private=private)
        digest, size = _hash_descriptor(child, limit, controller)
        if size != identity.size:
            raise _WorkspaceFilesystemError(_FAILURE)
        if _require_named_identity(parent_fd, name, child, directory=False) != identity:
            raise _WorkspaceFilesystemError(_FAILURE)
        return digest, identity
    finally:
        if not descriptors.close(child):
            raise _WorkspaceFilesystemError(_FAILURE)


def _require_output_directory(
    descriptor: int,
    *,
    expected_device: int | None,
    private: bool,
) -> _WorkspaceFilesystemIdentity:
    details = os.fstat(descriptor)
    identity = _WorkspaceFilesystemIdentity.from_stat(details)
    mode = stat.S_IMODE(identity.mode)
    if (
        not identity.is_directory()
        or (expected_device is not None and identity.device != expected_device)
        or details.st_uid != os.geteuid()
        or (mode != 0o700 if private else mode & 0o022 or mode & 0o500 != 0o500)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return identity


def _record_output(
    digest: object,
    relative: tuple[str, ...],
    kind: str,
    size: int,
    content: bytes | None,
) -> None:
    encoded = "/".join(relative).encode("utf-8", errors="strict")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    digest.update(b"d" if kind == "directory" else b"f")
    digest.update(size.to_bytes(8, "big"))
    digest.update(content or b"\x00" * 32)


def _check_output_cancel(controller: _WorkspaceWorkerController) -> None:
    if (
        type(controller) is not _WorkspaceWorkerController
        or controller._cancellation_requested() is True
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _read_descriptor(
    descriptor: int,
    limit: int,
    controller: _WorkspaceWorkerController,
) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        _check_output_cancel(controller)
        chunk = os.read(descriptor, min(_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise _WorkspaceFilesystemError(_FAILURE)
        chunks.append(chunk)


def _require_output_regular(
    descriptor: int,
    identity: _WorkspaceFilesystemIdentity,
    expected_device: int,
    *,
    private: bool,
) -> None:
    details = os.fstat(descriptor)
    mode = stat.S_IMODE(identity.mode)
    if (
        identity.device != expected_device
        or identity.links != 1
        or details.st_uid != os.geteuid()
        or (mode != 0o600 if private else mode & 0o022 or mode & 0o400 == 0)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


__all__: list[str] = []

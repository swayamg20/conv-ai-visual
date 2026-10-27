"""Descriptor-relative primitives used only on the exact workspace worker."""

from __future__ import annotations

import os
import stat
import traceback
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
)

_OS_CLOSE = os.close
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class _WorkspaceCreationIntent:
    """Worker-local durable intent set before a fresh namespace effect."""

    __slots__ = (
        "collision",
        "entered",
        "identity",
        "kind",
        "name",
        "parent",
        "parent_binding",
        "resolved_no_effect",
        "returned",
    )

    def __init__(self, *, parent: int, name: str, kind: str) -> None:
        _valid_name(name)
        if type(parent) is not int or parent < 0 or kind not in {"directory", "file", "symlink"}:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.parent = parent
        parent_identity = _WorkspaceFilesystemIdentity.from_stat(os.fstat(parent))
        self.parent_binding = _stable_binding(parent_identity)
        self.name = name
        self.kind = kind
        self.entered = False
        self.returned = False
        self.resolved_no_effect = False
        self.collision = False
        self.identity: _WorkspaceFilesystemIdentity | None = None

    def enter(self) -> None:
        if self.entered or self.resolved_no_effect:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.entered = True

    def mark_no_effect(self) -> None:
        if not self.entered or self.returned:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.resolved_no_effect = True

    def mark_collision(self) -> None:
        if not self.entered or self.returned:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.collision = True
        self.resolved_no_effect = True

    def mark_returned(self, identity: _WorkspaceFilesystemIdentity) -> None:
        if not self.entered:
            raise _WorkspaceFilesystemError(_FAILURE)
        if (
            _stable_binding(_WorkspaceFilesystemIdentity.from_stat(os.fstat(self.parent)))
            != self.parent_binding
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        self.identity = identity
        self.returned = True

    def mark_effect_returned(self) -> None:
        if not self.entered or self.returned:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.returned = True

    def bind_identity(self, identity: _WorkspaceFilesystemIdentity) -> None:
        if not self.returned or self.identity is not None:
            raise _WorkspaceFilesystemError(_FAILURE)
        self.identity = identity

    def reconcile_returned(self, identity: _WorkspaceFilesystemIdentity) -> None:
        if not self.entered or self.returned:
            raise _WorkspaceFilesystemError(_FAILURE)
        if (
            _stable_binding(_WorkspaceFilesystemIdentity.from_stat(os.fstat(self.parent)))
            != self.parent_binding
        ):
            raise _WorkspaceFilesystemError(_FAILURE)
        self.identity = identity
        self.returned = True


class _WorkspaceDescriptorSet:
    """Worker-stack-only descriptor owner with one-shot close semantics."""

    __slots__ = ("_descriptors", "_uncertain")

    def __init__(self) -> None:
        self._descriptors: dict[int, tuple[int, int, int] | None] = {}
        self._uncertain: set[int] = set()

    def adopt(self, descriptor: int) -> int:
        if type(descriptor) is not int or descriptor < 0 or descriptor in self._descriptors:
            raise _WorkspaceFilesystemError(_FAILURE)
        self._descriptors[descriptor] = None
        try:
            identity = _WorkspaceFilesystemIdentity.from_stat(os.fstat(descriptor))
        except BaseException:
            raise
        self._descriptors[descriptor] = _stable_binding(identity)
        return descriptor

    def identity(self, descriptor: int) -> _WorkspaceFilesystemIdentity:
        binding = self._descriptors.get(descriptor)
        if type(binding) is not tuple:
            raise _WorkspaceFilesystemError(_FAILURE)
        current = _WorkspaceFilesystemIdentity.from_stat(os.fstat(descriptor))
        if _stable_binding(current) != binding:
            raise _WorkspaceFilesystemError(_FAILURE)
        return current

    def close(self, descriptor: int) -> bool:
        if descriptor not in self._descriptors:
            return descriptor not in self._uncertain
        if descriptor in self._uncertain:
            return False
        try:
            _OS_CLOSE(descriptor)
        except BaseException:
            self._uncertain.add(descriptor)
            raise
        del self._descriptors[descriptor]
        return True

    def close_all(self) -> bool:
        complete = True
        first_control: KeyboardInterrupt | SystemExit | None = None
        for descriptor in tuple(reversed(self._descriptors)):
            try:
                if not self.close(descriptor):
                    complete = False
            except (KeyboardInterrupt, SystemExit) as control:
                if first_control is None:
                    first_control = control
                else:
                    _scrub_exception(control)
                complete = False
            except BaseException as error:
                _scrub_exception(error)
                complete = False
        if first_control is not None:
            raise first_control
        return bool(complete and not self._descriptors and not self._uncertain)

    def is_empty(self) -> bool:
        return not self._descriptors and not self._uncertain

    def find(self, identity: _WorkspaceFilesystemIdentity) -> int | None:
        binding = _stable_binding(identity)
        matches = [
            descriptor
            for descriptor, candidate in self._descriptors.items()
            if candidate == binding and descriptor not in self._uncertain
        ]
        if len(matches) > 1:
            raise _WorkspaceFilesystemError(_FAILURE)
        return matches[0] if matches else None


def _stable_binding(identity: _WorkspaceFilesystemIdentity) -> tuple[int, int, int]:
    return identity.device, identity.inode, stat.S_IFMT(identity.mode)


def _open_absolute_directory(
    path: Path,
    descriptors: _WorkspaceDescriptorSet,
) -> int:
    if type(path) is not type(Path("/")) or not path.is_absolute() or ".." in path.parts:
        raise _WorkspaceFilesystemError(_FAILURE)
    current = _open_owned(descriptors, "/", _directory_flags())
    try:
        for component in path.parts[1:]:
            _valid_name(component)
            child = _open_owned(
                descriptors,
                component,
                _directory_flags(),
                dir_fd=current,
            )
            _require_named_identity(current, component, child, directory=True)
            if not descriptors.close(current):
                raise _WorkspaceFilesystemError(_FAILURE)
            current = child
        return current
    except BaseException:
        try:
            descriptors.close(current)
        except BaseException:
            pass
        raise


def _open_directory_at(
    parent: int,
    name: str,
    descriptors: _WorkspaceDescriptorSet,
) -> int:
    _valid_name(name)
    child = _open_owned(descriptors, name, _directory_flags(), dir_fd=parent)
    try:
        _require_named_identity(parent, name, child, directory=True)
        return child
    except BaseException:
        descriptors.close(child)
        raise


def _open_regular_at(
    parent: int,
    name: str,
    descriptors: _WorkspaceDescriptorSet,
    *,
    executable: bool = False,
) -> int:
    _valid_name(name)
    before = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    )
    if not before.is_regular():
        raise _WorkspaceFilesystemError(_FAILURE)
    child = _open_owned(descriptors, name, _read_flags(), dir_fd=parent)
    try:
        identity = _require_named_identity(parent, name, child, directory=False)
        if _stable_binding(identity) != _stable_binding(before):
            raise _WorkspaceFilesystemError(_FAILURE)
        if executable and identity.mode & 0o111 == 0:
            raise _WorkspaceFilesystemError(_FAILURE)
        return child
    except BaseException:
        descriptors.close(child)
        raise


def _create_directory_at(
    parent: int,
    name: str,
    descriptors: _WorkspaceDescriptorSet,
    intent: _WorkspaceCreationIntent,
) -> tuple[int, _WorkspaceFilesystemIdentity]:
    _valid_name(name)
    if intent.parent != parent or intent.name != name or intent.kind != "directory":
        raise _WorkspaceFilesystemError(_FAILURE)
    _require_absent(parent, name)
    intent.enter()
    try:
        os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent)
    except FileExistsError:
        intent.mark_collision()
        raise
    except BaseException:
        try:
            reconciled = _WorkspaceFilesystemIdentity.from_stat(
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            )
            if reconciled.is_directory() and stat.S_IMODE(reconciled.mode) == _DIRECTORY_MODE:
                intent.reconcile_returned(reconciled)
        except FileNotFoundError:
            intent.mark_no_effect()
        except BaseException as error:
            _scrub_exception(error)
        raise
    intent.mark_effect_returned()
    created = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    )
    if not created.is_directory():
        raise _WorkspaceFilesystemError(_FAILURE)
    intent.bind_identity(created)
    child = _open_directory_at(parent, name, descriptors)
    opened = descriptors.identity(child)
    if _stable_binding(opened) != _stable_binding(created):
        raise _WorkspaceFilesystemError(_FAILURE)
    os.fchmod(child, _DIRECTORY_MODE)
    identity = _require_named_identity(parent, name, child, directory=True)
    if stat.S_IMODE(identity.mode) != _DIRECTORY_MODE:
        raise _WorkspaceFilesystemError(_FAILURE)
    return child, identity


def _create_regular_at(
    parent: int,
    name: str,
    descriptors: _WorkspaceDescriptorSet,
    intent: _WorkspaceCreationIntent,
) -> int:
    _valid_name(name)
    if intent.parent != parent or intent.name != name or intent.kind != "file":
        raise _WorkspaceFilesystemError(_FAILURE)
    _require_absent(parent, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    intent.enter()
    child = _open_owned(descriptors, name, flags, _FILE_MODE, dir_fd=parent)
    created = _require_named_identity(parent, name, child, directory=False)
    intent.mark_returned(created)
    try:
        os.fchmod(child, _FILE_MODE)
        identity = _require_named_identity(parent, name, child, directory=False)
        if stat.S_IMODE(identity.mode) != _FILE_MODE:
            raise _WorkspaceFilesystemError(_FAILURE)
        return child
    except BaseException:
        descriptors.close(child)
        raise


def _create_symlink_at(
    parent: int,
    name: str,
    target: str,
    intent: _WorkspaceCreationIntent,
) -> _WorkspaceFilesystemIdentity:
    _valid_name(name)
    if (
        intent.parent != parent
        or intent.name != name
        or intent.kind != "symlink"
        or type(target) is not str
        or not target.startswith("/")
        or "\x00" in target
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    _require_absent(parent, name)
    intent.enter()
    os.symlink(target, name, dir_fd=parent)
    identity = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    )
    if (
        not stat.S_ISLNK(identity.mode)
        or identity.links != 1
        or os.readlink(name, dir_fd=parent) != target
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    intent.mark_returned(identity)
    return identity


def _require_named_identity(
    parent: int,
    name: str,
    descriptor: int,
    *,
    directory: bool,
) -> _WorkspaceFilesystemIdentity:
    named = _WorkspaceFilesystemIdentity.from_stat(
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    )
    opened = _WorkspaceFilesystemIdentity.from_stat(os.fstat(descriptor))
    valid = named == opened and (opened.is_directory() if directory else opened.is_regular())
    if not valid:
        raise _WorkspaceFilesystemError(_FAILURE)
    return opened


def _require_absent(parent: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise _WorkspaceFilesystemError(_FAILURE)


def _require_private_parent(descriptor: int) -> _WorkspaceFilesystemIdentity:
    identity = _WorkspaceFilesystemIdentity.from_stat(os.fstat(descriptor))
    if (
        not identity.is_directory()
        or stat.S_IMODE(identity.mode) != _DIRECTORY_MODE
        or os.fstat(descriptor).st_uid != os.geteuid()
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return identity


def _require_cooperative_node(
    descriptor: int,
    *,
    directory: bool,
    executable: bool = False,
) -> _WorkspaceFilesystemIdentity:
    details = os.fstat(descriptor)
    identity = _WorkspaceFilesystemIdentity.from_stat(details)
    if (
        details.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(identity.mode) & 0o022
        or (directory and not identity.is_directory())
        or (not directory and not identity.is_regular())
        or (executable and identity.mode & 0o111 == 0)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return identity


def _bounded_names(descriptor: int, limit: int) -> tuple[str, ...]:
    if type(limit) is not int or limit < 0:
        raise _WorkspaceFilesystemError(_FAILURE)
    names: list[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if len(names) >= limit:
                raise _WorkspaceFilesystemError(_FAILURE)
            name = entry.name
            _valid_name(name)
            names.append(name)
    return tuple(sorted(names))


def _open_owned(
    descriptors: _WorkspaceDescriptorSet,
    path: str,
    flags: int,
    mode: int | None = None,
    *,
    dir_fd: int | None = None,
) -> int:
    descriptor = (
        os.open(path, flags, dir_fd=dir_fd)
        if mode is None
        else os.open(path, flags, mode, dir_fd=dir_fd)
    )
    try:
        return descriptors.adopt(descriptor)
    except BaseException as original:
        close_error: BaseException | None = None
        if descriptor in descriptors._descriptors:
            try:
                descriptors.close(descriptor)
            except BaseException as error:
                close_error = error
        else:
            try:
                _OS_CLOSE(descriptor)
            except BaseException as error:
                # An untracked ambiguous descriptor cannot be claimed clean.
                descriptors._descriptors[descriptor] = None
                descriptors._uncertain.add(descriptor)
                close_error = error
        if isinstance(close_error, (KeyboardInterrupt, SystemExit)) and not isinstance(
            original,
            (KeyboardInterrupt, SystemExit),
        ):
            _scrub_exception(original)
            raise close_error from None
        if close_error is not None:
            _scrub_exception(close_error)
        raise original


def _scrub_exception(error: BaseException) -> None:
    try:
        trace = BaseException.__getattribute__(error, "__traceback__")
        BaseException.__setattr__(error, "__traceback__", None)
        BaseException.__setattr__(error, "__cause__", None)
        BaseException.__setattr__(error, "__context__", None)
        BaseException.__setattr__(error, "__suppress_context__", True)
        if trace is not None:
            traceback.clear_frames(trace)
    except BaseException:
        pass


def _valid_name(name: object) -> None:
    if (
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\x00" in name
        or os.fsencode(name).decode(errors="strict") != name
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, name) for name in required):
        raise _WorkspaceFilesystemError(_FAILURE)
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _read_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_CLOEXEC"):
        raise _WorkspaceFilesystemError(_FAILURE)
    return os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC


__all__: list[str] = []

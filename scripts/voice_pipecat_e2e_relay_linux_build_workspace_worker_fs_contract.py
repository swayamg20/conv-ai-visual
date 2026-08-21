"""Path-free values and exact identities for the workspace worker transaction."""

from __future__ import annotations

import stat
import weakref
from dataclasses import dataclass

_PREPARED_TOKEN = object()
_FAILURE = "Relay Linux build workspace filesystem transaction is invalid"
_LEASES: weakref.WeakKeyDictionary[
    _WorkspacePreparedReceipt,
    tuple[object, object, bytes, str],
] = weakref.WeakKeyDictionary()
_PREPARED_BUILDS: weakref.WeakKeyDictionary[
    _WorkspacePreparedReceipt,
    tuple[object, object, object, float, str],
] = weakref.WeakKeyDictionary()
_SETTLEMENTS: dict[object, tuple[object, object]] = {}
_CLAIMS: dict[object, tuple[object, object]] = {}


class _WorkspaceFilesystemError(RuntimeError):
    """A fixed, non-reflective workspace filesystem failure."""

    def __repr__(self) -> str:
        return "_WorkspaceFilesystemError()"


@dataclass(frozen=True, slots=True)
class _WorkspaceFilesystemIdentity:
    """The fields used to bind one descriptor to one named local node."""

    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, details: object) -> _WorkspaceFilesystemIdentity:
        try:
            values = (
                details.st_dev,
                details.st_ino,
                details.st_mode,
                details.st_nlink,
                details.st_size,
                details.st_mtime_ns,
                details.st_ctime_ns,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            raise _WorkspaceFilesystemError(_FAILURE) from None
        if any(type(value) is not int or value < 0 for value in values):
            raise _WorkspaceFilesystemError(_FAILURE)
        return cls(*values)

    def is_directory(self) -> bool:
        return stat.S_ISDIR(self.mode) and self.links >= 1

    def is_regular(self) -> bool:
        return stat.S_ISREG(self.mode) and self.links == 1


@dataclass(frozen=True, slots=True)
class _WorkspaceSourceNode:
    """Worker-local source/copy evidence; it never leaves the worker stack."""

    relative: tuple[str, ...]
    kind: str
    identity: _WorkspaceFilesystemIdentity
    digest: bytes | None


class _WorkspacePreparedReceipt:
    """Opaque, path-free evidence published only after exact revalidation."""

    __slots__ = (
        "__weakref__",
        "_fingerprint",
        "_lease_active",
        "_owner_token",
        "_record_token",
        "status",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
        fingerprint: bytes,
    ) -> None:
        if (
            token is not _PREPARED_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(fingerprint) is not bytes
            or len(fingerprint) != 32
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_fingerprint", fingerprint)
        object.__setattr__(self, "_lease_active", False)
        object.__setattr__(self, "status", "workspace-prepared")
        _LEASES[self] = (owner_token, record_token, fingerprint, "pending")

    def _matches(
        self,
        owner_token: object,
        record_token: object | None = None,
        *,
        require_active: bool = False,
    ) -> bool:
        state = _LEASES.get(self)
        return bool(
            type(state) is tuple
            and len(state) == 4
            and state[0] is owner_token
            and (record_token is None or state[1] is record_token)
            and type(state[2]) is bytes
            and len(state[2]) == 32
            and type(self.status) is str
            and self.status == "workspace-prepared"
            and type(state[3]) is str
            and state[3] in {"pending", "active"}
            and (not require_active or state[3] == "active")
        )

    def _revoke(self, owner_token: object, record_token: object) -> bool:
        return _revoke_workspace_prepared_receipt(self, owner_token, record_token)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspacePreparedReceipt()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace prepared receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace prepared receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace prepared receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace prepared receipt cannot be serialized")


def _new_workspace_prepared_receipt(
    *,
    owner_token: object,
    record_token: object,
    fingerprint: bytes,
) -> _WorkspacePreparedReceipt:
    return _WorkspacePreparedReceipt(
        _PREPARED_TOKEN,
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=fingerprint,
    )


def _revoke_workspace_prepared_receipt(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    if type(receipt) is not _WorkspacePreparedReceipt:
        raise _WorkspaceFilesystemError(_FAILURE)
    state = _LEASES.get(receipt)
    if (
        type(state) is not tuple
        or len(state) != 4
        or state[0] is not owner_token
        or state[1] is not record_token
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    object.__setattr__(receipt, "_lease_active", False)
    _LEASES[receipt] = (state[0], state[1], state[2], "revoked")
    return _workspace_prepared_receipt_is_revoked(receipt, owner_token, record_token)


def _activate_workspace_prepared_receipt(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    if type(receipt) is not _WorkspacePreparedReceipt:
        raise _WorkspaceFilesystemError(_FAILURE)
    state = _LEASES.get(receipt)
    if (
        type(state) is not tuple
        or len(state) != 4
        or state[0] is not owner_token
        or state[1] is not record_token
        or state[3] not in {"pending", "active"}
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    object.__setattr__(receipt, "_lease_active", True)
    _LEASES[receipt] = (state[0], state[1], state[2], "active")
    return receipt._matches(owner_token, record_token, require_active=True)


def _claim_workspace_prepared_receipt_for_build(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
    command: object,
    build_deadline: float,
) -> bool:
    """Consume the active lease without yet authorizing filesystem cleanup."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
        _WorkspaceBuildCommand,
    )

    if (
        type(receipt) is not _WorkspacePreparedReceipt
        or type(command) is not _WorkspaceBuildCommand
        or type(build_deadline) is not float
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    state = _LEASES.get(receipt)
    association = _PREPARED_BUILDS.get(receipt)
    candidate = (owner_token, record_token, command, build_deadline)
    if (
        type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == "building"
        and type(association) is tuple
        and len(association) == 5
        and association[:4] == candidate
        and association[4] == "building"
    ):
        return True
    if (
        type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == "building"
        and type(association) is tuple
        and len(association) == 5
        and association[:4] == candidate
        and association[4] == "intended"
    ):
        bound = (*candidate, "building")
        try:
            _store_prepared_build(receipt, bound)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            if _PREPARED_BUILDS.get(receipt) != bound:
                raise
        return _workspace_prepared_build_matches(
            receipt,
            owner_token,
            record_token,
            command,
            build_deadline,
        )
    if (
        type(state) is not tuple
        or len(state) != 4
        or state[0] is not owner_token
        or state[1] is not record_token
        or state[3] != "active"
        or (
            association is not None
            and (
                type(association) is not tuple
                or len(association) != 5
                or association[:4] != candidate
                or association[4] != "intended"
            )
        )
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    if association is None:
        _store_prepared_build(receipt, (*candidate, "intended"))
    object.__setattr__(receipt, "_lease_active", False)
    _store_prepared_lease(receipt, (state[0], state[1], state[2], "building"))
    bound = (*candidate, "building")
    try:
        _store_prepared_build(receipt, bound)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        if _PREPARED_BUILDS.get(receipt) != bound:
            raise
    return _workspace_prepared_build_matches(
        receipt,
        owner_token,
        record_token,
        command,
        build_deadline,
    )


def _workspace_prepared_receipt_is_building(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    state = _LEASES.get(receipt)
    return bool(
        type(receipt) is _WorkspacePreparedReceipt
        and type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == "building"
    )


def _workspace_prepared_build_matches(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
    command: object,
    build_deadline: float,
) -> bool:
    association = _PREPARED_BUILDS.get(receipt)
    return bool(
        _workspace_prepared_receipt_is_building(receipt, owner_token, record_token)
        and type(association) is tuple
        and len(association) == 5
        and association[0] is owner_token
        and association[1] is record_token
        and association[2] is command
        and association[3] == build_deadline
        and association[4] == "building"
    )


def _store_prepared_lease(
    receipt: _WorkspacePreparedReceipt,
    value: tuple[object, object, bytes, str],
) -> None:
    """Deterministic state-store cut used by return-loss tests."""

    _LEASES[receipt] = value


def _store_prepared_build(
    receipt: _WorkspacePreparedReceipt,
    value: tuple[object, object, object, float, str],
) -> None:
    """Deterministic association-store cut used by return-loss tests."""

    _PREPARED_BUILDS[receipt] = value


def _workspace_prepared_receipt_is_revoked(
    receipt: _WorkspacePreparedReceipt,
    owner_token: object,
    record_token: object,
) -> bool:
    state = _LEASES.get(receipt)
    return bool(
        type(receipt) is _WorkspacePreparedReceipt
        and type(state) is tuple
        and len(state) == 4
        and state[0] is owner_token
        and state[1] is record_token
        and state[3] == "revoked"
    )


def _forget_workspace_prepared_build(
    receipt: _WorkspacePreparedReceipt,
    command: object,
) -> bool:
    """Forget only an exact revoked prepared-to-build association."""

    state = _LEASES.get(receipt)
    association = _PREPARED_BUILDS.get(receipt)
    if association is None:
        return True
    if (
        type(receipt) is not _WorkspacePreparedReceipt
        or type(state) is not tuple
        or len(state) != 4
        or state[3] != "revoked"
        or type(association) is not tuple
        or len(association) != 5
        or association[0] is not state[0]
        or association[1] is not state[1]
        or association[2] is not command
        or association[4] != "building"
    ):
        return False
    _PREPARED_BUILDS.pop(receipt, None)
    return receipt not in _PREPARED_BUILDS


def _publish_workspace_filesystem_settlement(
    owner_token: object,
    record_token: object,
    claim_token: object,
) -> bool:
    if not all(type(value) is object for value in (owner_token, record_token, claim_token)):
        raise _WorkspaceFilesystemError(_FAILURE)
    candidate = (owner_token, claim_token)
    existing = _SETTLEMENTS.setdefault(record_token, candidate)
    return existing == candidate and _SETTLEMENTS.get(record_token) == candidate


def _publish_workspace_filesystem_claim(
    owner_token: object,
    record_token: object,
    claim_token: object,
) -> bool:
    if not all(type(value) is object for value in (owner_token, record_token, claim_token)):
        raise _WorkspaceFilesystemError(_FAILURE)
    candidate = (owner_token, claim_token)
    existing = _CLAIMS.setdefault(record_token, candidate)
    return existing == candidate and _CLAIMS.get(record_token) == candidate


def _workspace_filesystem_claim_matches(
    owner_token: object,
    record_token: object,
    claim_token: object,
) -> bool:
    return bool(
        type(record_token) is object and _CLAIMS.get(record_token) == (owner_token, claim_token)
    )


def _workspace_filesystem_was_claimed(record_token: object) -> bool:
    return bool(type(record_token) is object and record_token in _CLAIMS)


def _workspace_filesystem_is_settled(
    owner_token: object,
    record_token: object,
    claim_token: object,
) -> bool:
    return bool(
        type(owner_token) is object
        and type(record_token) is object
        and type(claim_token) is object
        and _SETTLEMENTS.get(record_token) == (owner_token, claim_token)
    )


def _workspace_filesystem_record_is_settled(record_token: object) -> bool:
    return bool(type(record_token) is object and record_token in _SETTLEMENTS)


def _workspace_filesystem_state_is_forgotten(record_token: object) -> bool:
    return bool(
        type(record_token) is object
        and record_token not in _SETTLEMENTS
        and record_token not in _CLAIMS
    )


def _forget_workspace_filesystem_settlement(record_token: object) -> None:
    if type(record_token) is object:
        _SETTLEMENTS.pop(record_token, None)
        _CLAIMS.pop(record_token, None)


__all__: list[str] = []

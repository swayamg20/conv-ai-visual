"""Atomic admission registry for the dormant relay B0 build controller.

The registry only admits one exact owner and pre-reserves one exact worker
kernel.  It has no take, completion, terminal publication, or release API;
those operations require the dedicated worker and settlement predicates that
are intentionally outside this checkpoint.
"""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _new_build_process_owner,
    _RelayLinuxBuildCleanupAuthority,
    _RelayLinuxBuildProcessError,
    _RelayLinuxBuildProcessOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _RawBuildProcessDestination,
    _RelayLinuxBuildSpec,
)

_DESTINATION_TOKEN = object()
_KERNEL_TOKEN = object()
_LOCK = threading.RLock()
_OWNERS: dict[object, _RelayLinuxBuildProcessOwner] = {}
_KERNELS: dict[object, _BuildWorkerKernel] = {}
_FAILURE = "Relay Linux build process registry is unavailable"


class _BuildWorkerKernel:
    """Opaque registry record reserved before any future thread construction."""

    __slots__ = ("_owner", "_token")

    def __init__(self, token: object, *, owner: _RelayLinuxBuildProcessOwner) -> None:
        if token is not _KERNEL_TOKEN:
            raise TypeError("Relay Linux build worker kernel is factory-owned")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_token", object())

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerKernel()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker kernel is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker kernel cannot be serialized")


class _BuildOwnerDestination:
    """Caller-preowned exact owner graph for one exact spec/raw pair."""

    __slots__ = ("_owner", "_raw_destination", "_spec")

    def __init__(
        self,
        token: object,
        *,
        spec: _RelayLinuxBuildSpec,
        raw_destination: _RawBuildProcessDestination,
    ) -> None:
        if token is not _DESTINATION_TOKEN or not spec._matches_destination(raw_destination):
            raise TypeError("Relay Linux build owner destination is factory-owned")
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_raw_destination", raw_destination)
        object.__setattr__(
            self,
            "_owner",
            _new_build_process_owner(
                spec=spec,
                raw_destination=raw_destination,
                cleanup_key=object(),
            ),
        )

    def _read(self) -> _RelayLinuxBuildProcessOwner:
        return self._owner

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildOwnerDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build owner destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build owner destination cannot be serialized")


def _new_build_owner_destination(
    spec: _RelayLinuxBuildSpec,
    raw_destination: _RawBuildProcessDestination,
) -> _BuildOwnerDestination:
    if (
        type(spec) is not _RelayLinuxBuildSpec
        or type(raw_destination) is not _RawBuildProcessDestination
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    return _BuildOwnerDestination(
        _DESTINATION_TOKEN,
        spec=spec,
        raw_destination=raw_destination,
    )


def _preown_build_process(
    *,
    spec: _RelayLinuxBuildSpec,
    raw_destination: _RawBuildProcessDestination,
    destination: _BuildOwnerDestination,
) -> _RelayLinuxBuildProcessOwner:
    if (
        type(spec) is not _RelayLinuxBuildSpec
        or type(raw_destination) is not _RawBuildProcessDestination
        or type(destination) is not _BuildOwnerDestination
        or destination._spec is not spec
        or destination._raw_destination is not raw_destination
    ):
        raise _RelayLinuxBuildProcessError(_FAILURE)
    owner = destination._owner
    if owner._spec is not spec or owner._raw_destination is not raw_destination:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _LOCK:
        key = owner._cleanup_authority._key
        registered = _OWNERS.get(key)
        if registered is None:
            if _OWNERS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            _OWNERS[key] = owner
        elif registered is not owner:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return owner


def _reserve_worker_kernel(owner: _RelayLinuxBuildProcessOwner) -> _BuildWorkerKernel:
    if type(owner) is not _RelayLinuxBuildProcessOwner:
        raise _RelayLinuxBuildProcessError(_FAILURE)
    with _LOCK:
        if not _owner_registered_unlocked(owner):
            raise _RelayLinuxBuildProcessError(_FAILURE)
        existing = owner._kernel_destination._read()
        if existing is None:
            if _KERNELS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            existing = _BuildWorkerKernel(_KERNEL_TOKEN, owner=owner)
            # Retain the candidate in the preowned graph before admitting it.
            # A control after this store is reconciled by the retry below.
            owner._kernel_destination._publish(existing)
        if type(existing) is not _BuildWorkerKernel or existing._owner is not owner:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        kernel = existing
        registered = _KERNELS.get(kernel._token)
        if registered is None:
            if _KERNELS:
                raise _RelayLinuxBuildProcessError(_FAILURE)
            _KERNELS[kernel._token] = kernel
        elif registered is not kernel:
            raise _RelayLinuxBuildProcessError(_FAILURE)
        return kernel


def _resolve_cleanup_authority(
    authority: _RelayLinuxBuildCleanupAuthority,
) -> _RelayLinuxBuildProcessOwner | None:
    if type(authority) is not _RelayLinuxBuildCleanupAuthority or not authority._is_authentic():
        return None
    with _LOCK:
        return _OWNERS.get(authority._key)


def _owner_registered_unlocked(owner: _RelayLinuxBuildProcessOwner) -> bool:
    authority = owner._cleanup_authority
    return _OWNERS.get(authority._key) is owner


__all__: list[str] = []

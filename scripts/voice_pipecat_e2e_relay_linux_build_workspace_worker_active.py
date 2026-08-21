"""Private strong ownership root while a started worker has not taken its claim."""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)

_ROOT_TOKEN = object()
_ROOTS_LOCK = threading.Lock()
_OWNERSHIP_LOCK = threading.Lock()
_ACTIVE_ROOT: _WorkspaceWorkerActiveRoot | None = None
_ROOT_WAIT_SECONDS = 0.05
_FAILURE = "Relay Linux workspace worker active root changed"


class _WorkspaceWorkerActiveRoot:
    """Redacted nonexported root; its bundle is cleared at exact transfer."""

    __slots__ = (
        "_bundle",
        "_controller",
        "_owner_token",
        "_record",
        "_record_token",
        "_terminal_destination",
    )

    def __init__(self, token: object, *, record_token: object, bundle: object) -> None:
        if (
            token is not _ROOT_TOKEN
            or type(record_token) is not object
            or type(bundle) is not _WorkspaceWorkerBundle
        ):
            raise TypeError("Relay Linux workspace worker active root is private")
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_bundle", bundle)
        object.__setattr__(self, "_record", None)
        object.__setattr__(self, "_terminal_destination", None)
        object.__setattr__(self, "_controller", bundle._controller)
        object.__setattr__(self, "_owner_token", bundle._owner_token)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerActiveRoot()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker active root is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker active root cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker active root cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker active root cannot be serialized")


def _pin_workspace_worker_bundle(
    record_token: object,
    bundle: _WorkspaceWorkerBundle,
    deadline: float | None = None,
) -> None:
    global _ACTIVE_ROOT
    with _root_locked(_bundle_deadline(bundle, deadline)):
        root = _ACTIVE_ROOT
        if root is None:
            root = _WorkspaceWorkerActiveRoot(
                _ROOT_TOKEN,
                record_token=record_token,
                bundle=bundle,
            )
            _ACTIVE_ROOT = root
        if root._record_token is not record_token or root._bundle is not bundle:
            raise TypeError(_FAILURE)


def _workspace_worker_bundle_is_pinned(
    record_token: object,
    bundle: _WorkspaceWorkerBundle,
    deadline: float | None = None,
) -> bool:
    with _root_locked(_bundle_deadline(bundle, deadline)):
        root = _ACTIVE_ROOT
        return bool(
            root is not None and root._record_token is record_token and root._bundle is bundle
        )


def _transfer_workspace_worker_bundle(
    record_token: object,
    bundle: _WorkspaceWorkerBundle,
    deadline: float | None = None,
) -> None:
    global _ACTIVE_ROOT
    with _root_locked(_bundle_deadline(bundle, deadline)):
        root = _ACTIVE_ROOT
        if root is None:
            return
        if root._record_token is not record_token or root._bundle is not bundle:
            if root._record_token is record_token and root._bundle is None:
                object.__setattr__(root, "_record", None)
                object.__setattr__(root, "_terminal_destination", None)
                object.__setattr__(root, "_controller", None)
                object.__setattr__(root, "_owner_token", None)
                _ACTIVE_ROOT = None
                return
            raise TypeError(_FAILURE)
        object.__setattr__(root, "_bundle", None)
        _ACTIVE_ROOT = None


def _transfer_workspace_worker_claim(
    record_token: object,
    bundle: _WorkspaceWorkerBundle,
    *,
    record: object,
    terminal_destination: object,
    controller: object,
    owner_token: object,
    deadline: float | None = None,
) -> None:
    with _root_locked(_deadline(deadline)):
        root = _ACTIVE_ROOT
        if (
            root is None
            or root._record_token is not record_token
            or root._bundle is not bundle
            or root._record is not record
            or root._terminal_destination is not terminal_destination
            or root._controller is not controller
            or root._owner_token is not owner_token
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(root, "_bundle", None)


def _attach_workspace_worker_active_record(
    record_token: object,
    bundle: _WorkspaceWorkerBundle,
    *,
    record: object,
    terminal_destination: object,
    controller: object,
    owner_token: object,
    deadline: float,
) -> None:
    with _root_locked(_deadline(deadline)):
        root = _ACTIVE_ROOT
        if (
            root is None
            or root._record_token is not record_token
            or root._bundle is not bundle
            or root._controller is not controller
            or root._owner_token is not owner_token
            or record is None
            or terminal_destination is None
        ):
            raise TypeError(_FAILURE)
        if root._record is not None and root._record is not record:
            raise TypeError(_FAILURE)
        if (
            root._terminal_destination is not None
            and root._terminal_destination is not terminal_destination
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(root, "_record", record)
        object.__setattr__(root, "_terminal_destination", terminal_destination)


def _resolve_workspace_worker_active_record(
    record_token: object,
    deadline: float | None = None,
) -> tuple[object, object, object, object] | None:
    with _root_locked(_deadline(deadline)):
        root = _ACTIVE_ROOT
        if root is None or root._record_token is not record_token or root._record is None:
            return None
        return (
            root._record,
            root._terminal_destination,
            root._controller,
            root._owner_token,
        )


def _workspace_worker_active_capacity_occupied(deadline: float | None = None) -> bool:
    with _root_locked(_deadline(deadline)):
        return _ACTIVE_ROOT is not None


def _workspace_worker_active_root_occupied(
    record_token: object,
    deadline: float | None = None,
) -> bool:
    with _root_locked(_deadline(deadline)):
        root = _ACTIVE_ROOT
        return bool(root is not None and root._record_token is record_token)


def _bundle_deadline(bundle: object, deadline: float | None) -> float:
    if deadline is not None:
        return _deadline(deadline)
    coordinator = getattr(bundle, "_lifecycle", None)
    candidate = getattr(coordinator, "_release_deadline", None)
    if type(candidate) is not float:
        candidate = getattr(coordinator, "_start_deadline", None)
    return _deadline(candidate if type(candidate) is float else None)


def _deadline(deadline: float | None) -> float:
    if deadline is None:
        return time.monotonic() + _ROOT_WAIT_SECONDS
    if type(deadline) is not float or not math.isfinite(deadline):
        raise TypeError(_FAILURE)
    return deadline


@contextmanager
def _root_locked(deadline: float) -> Iterator[None]:
    remaining = max(0.0, deadline - time.monotonic())
    acquired = (
        _ROOTS_LOCK.acquire(blocking=False)
        if remaining <= 0.0
        else _ROOTS_LOCK.acquire(timeout=remaining)
    )
    if not acquired:
        raise RuntimeError("Relay Linux workspace worker active root deadline expired")
    try:
        yield
    finally:
        _ROOTS_LOCK.release()


@contextmanager
def _workspace_worker_ownership_locked(deadline: float) -> Iterator[None]:
    deadline = _deadline(deadline)
    remaining = max(0.0, deadline - time.monotonic())
    acquired = (
        _OWNERSHIP_LOCK.acquire(blocking=False)
        if remaining <= 0.0
        else _OWNERSHIP_LOCK.acquire(timeout=remaining)
    )
    if not acquired:
        raise RuntimeError("Relay Linux workspace worker ownership deadline expired")
    try:
        yield
    finally:
        _OWNERSHIP_LOCK.release()


__all__: list[str] = []

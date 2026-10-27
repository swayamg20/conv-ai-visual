"""Deadline-bounded owner-to-worker binding resolution."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_BINDING_WAIT_SECONDS = 0.05
_BINDING_DEADLINE: ContextVar[float | None] = ContextVar(
    "relay_linux_workspace_worker_binding_deadline",
    default=None,
)


def _resolve_workspace_worker_thread_binding(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    deadline: float | None = None,
) -> registry._WorkspaceWorkerThreadBinding:
    try:
        if (
            type(owner) is not _RelayLinuxBuildWorkspaceOwner
            or type(bundle) is not _WorkspaceWorkerBundle
            or not owner._cleanup_authority._matches(owner._request)
        ):
            raise TypeError
        owner_token = owner._cleanup_authority._key
        deadline = _workspace_worker_operation_deadline(deadline)
        stored_bundle, acquired = owner._worker_bundle_destination._read_before(
            owner._request,
            deadline,
        )
        if (
            type(owner_token) is not object
            or not acquired
            or stored_bundle is not bundle
            or not bundle._matches(owner_token, owner._receipt_destination)
            or type(bundle._controller) is not _WorkspaceWorkerController
        ):
            raise TypeError
        return registry._WorkspaceWorkerThreadBinding(
            registry._BINDING_TOKEN,
            owner_token=owner_token,
            controller=bundle._controller,
            bundle=bundle,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        _scrub_control_minimal(error)
        raise TypeError("Relay Linux workspace worker owner binding is invalid") from None


@contextmanager
def _workspace_worker_binding_deadline(deadline: float) -> Iterator[None]:
    deadline = _workspace_worker_operation_deadline(deadline)
    token = _BINDING_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _BINDING_DEADLINE.reset(token)


def _current_workspace_worker_binding_deadline() -> float | None:
    return _BINDING_DEADLINE.get()


def _workspace_worker_operation_deadline(deadline: float | None) -> float:
    if deadline is None:
        deadline = _BINDING_DEADLINE.get()
    if deadline is None:
        deadline = time.monotonic() + _BINDING_WAIT_SECONDS
    if type(deadline) is not float or not math.isfinite(deadline):
        raise TypeError("Relay Linux workspace worker binding deadline is invalid")
    return deadline


@contextmanager
def _workspace_worker_locked_before(lock: object, deadline: float) -> Iterator[None]:
    deadline = _workspace_worker_operation_deadline(deadline)
    remaining = max(0.0, deadline - time.monotonic())
    acquired = lock.acquire(blocking=False) if remaining <= 0.0 else lock.acquire(timeout=remaining)
    if not acquired:
        raise RuntimeError("Relay Linux workspace worker construction deadline expired")
    try:
        yield
    finally:
        lock.release()


__all__: list[str] = []

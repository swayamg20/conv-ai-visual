"""Bounded live-worker proof for the private disposable executor."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_raw import (
    _WorkspaceWorkerThread,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _INITIALIZED,
    _RECORDS,
    _REGISTRY_LOCK,
    _resolve_workspace_worker_thread_binding,
    _WorkspaceWorkerThreadReceipt,
    _WorkspaceWorkerThreadRecord,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)


def _workspace_worker_receipt_is_current(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    receipt: _WorkspaceWorkerThreadReceipt,
    deadline: float,
) -> str | None:
    """Prove one exact live registry entry without an outer-state lock."""

    if (
        type(owner) is not _RelayLinuxBuildWorkspaceOwner
        or type(bundle) is not _WorkspaceWorkerBundle
        or type(receipt) is not _WorkspaceWorkerThreadReceipt
        or type(deadline) is not float
        or not math.isfinite(deadline)
    ):
        return "invalid"
    try:
        binding = _resolve_workspace_worker_thread_binding(owner, bundle, deadline)
        remaining = max(0.0, deadline - time.monotonic())
        acquired = (
            _REGISTRY_LOCK.acquire(blocking=False)
            if remaining <= 0.0
            else _REGISTRY_LOCK.acquire(timeout=remaining)
        )
        if not acquired:
            return None
        try:
            record = _RECORDS.get(bundle)
            if record is None:
                return "absent" if not _RECORDS else "invalid"
            if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(binding):
                return "invalid"
            remaining = max(0.0, deadline - time.monotonic())
            acquired_record = (
                record._lock.acquire(blocking=False)
                if remaining <= 0.0
                else record._lock.acquire(timeout=remaining)
            )
            if not acquired_record:
                return None
            try:
                entry = record._entry
                return (
                    "current"
                    if (
                        type(entry) is tuple
                        and len(entry) == 3
                        and type(entry[0]) is str
                        and entry[0] == _INITIALIZED
                        and type(entry[1]) is _WorkspaceWorkerThread
                        and entry[2] is receipt
                        and receipt._coherent is True
                    )
                    else "invalid"
                )
            finally:
                record._lock.release()
        finally:
            _REGISTRY_LOCK.release()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


__all__: list[str] = []

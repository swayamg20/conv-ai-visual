"""Tri-state exact worker/claim binding used by the filesystem transaction."""

from __future__ import annotations

import math
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _workspace_filesystem_claim_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerClaim,
    _WorkspaceWorkerCoordinator,
)


def _workspace_worker_claim_state(
    claim: object,
    candidate: object,
    deadline: float,
) -> bool | None:
    """Return true/current, false/mismatch, or none/transient lock timeout."""

    if (
        type(claim) is not _WorkspaceWorkerClaim
        or type(candidate) is not registry._WorkspaceWorkerThread
        or type(deadline) is not float
        or not math.isfinite(deadline)
    ):
        return False
    remaining = max(0.0, deadline - time.monotonic())
    acquired = (
        registry._REGISTRY_LOCK.acquire(blocking=False)
        if remaining <= 0.0
        else registry._REGISTRY_LOCK.acquire(timeout=remaining)
    )
    if not acquired:
        return None
    try:
        for _bundle, record in tuple(registry._RECORDS.items()):
            if (
                type(record) is registry._WorkspaceWorkerThreadRecord
                and record._record_token is claim._record_token
                and record._owner_token is claim._owner_token
                and record._entry is not None
                and record._entry[1] is candidate
            ):
                remaining = max(0.0, deadline - time.monotonic())
                locked = (
                    record._lock.acquire(blocking=False)
                    if remaining <= 0.0
                    else record._lock.acquire(timeout=remaining)
                )
                if not locked:
                    return None
                try:
                    coordinator = record._lifecycle
                    return bool(
                        type(coordinator) is _WorkspaceWorkerCoordinator
                        and coordinator is claim._coordinator
                        and coordinator._phase == "claimed"
                        and coordinator._claim_token is claim._claim_token
                        and coordinator._settlement_token is claim._claim_token
                        and _workspace_filesystem_claim_matches(
                            record._owner_token,
                            record._record_token,
                            claim._claim_token,
                        )
                    )
                finally:
                    record._lock.release()
        return False
    finally:
        registry._REGISTRY_LOCK.release()


__all__: list[str] = []

"""Path-free contract checks shared by the workspace worker lifecycle."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _DESTINATION_TOKEN,
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _COORDINATOR_TOKEN,
    _TERMINAL_TOKEN,
    _WorkspaceWorkerCoordinator,
    _WorkspaceWorkerStartReceipt,
    _WorkspaceWorkerTerminalReceipt,
)

_FAILURE = "Relay Linux workspace worker lifecycle is invalid"
_EVENT_IS_SET = threading.Event.is_set
_THREAD_IS_ALIVE = threading.Thread.is_alive
_TAKE_WAIT_SECONDS = 0.05


def _workspace_worker_bundle_allows_record(
    bundle: _WorkspaceWorkerBundle,
    owner_token: object,
) -> bool:
    """Reject reconstruction once bundle-owned release intent is durable."""

    if type(bundle) is not _WorkspaceWorkerBundle:
        return False
    coordinator = bundle._lifecycle
    return bool(
        coordinator is None
        or (
            type(coordinator) is _WorkspaceWorkerCoordinator
            and coordinator._owner_token is owner_token
            and coordinator._release_phase == "none"
        )
    )


def _workspace_worker_destination_value_matches(
    value: object,
    owner_token: object,
    kind: str,
) -> bool:
    if kind == "thread":
        return bool(
            type(value) is _WorkspaceWorkerStartReceipt and value._owner_token is owner_token
        )
    if kind == "terminal":
        return bool(
            type(value) is _WorkspaceWorkerTerminalReceipt and value._owner_token is owner_token
        )
    return False


def _record_parts_locked(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    *,
    allow_failed: bool = False,
) -> tuple[
    _WorkspaceWorkerBundle,
    registry._WorkspaceWorkerThreadRecord,
    registry._WorkspaceWorkerThread | None,
    _WorkspaceWorkerCoordinator,
]:
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        raise TypeError(_FAILURE)
    record = registry._RECORDS.get(bundle)
    if type(record) is not registry._WorkspaceWorkerThreadRecord or not record._matches(binding):
        raise TypeError(_FAILURE)
    state, raw, canonical = record._entry
    initialized = bool(state == registry._INITIALIZED and construction._coherent is True)
    failed = bool(allow_failed and state == registry._FAILED and construction._coherent is False)
    poisoned = bool(
        allow_failed
        and state == registry._POISONED
        and type(construction._coherent) is bool
        and (
            raw is None
            or (type(raw) is registry._WorkspaceWorkerThread and vars(raw).get("_target") is None)
        )
    )
    if (
        not (initialized or failed or poisoned)
        or (not poisoned and type(raw) is not registry._WorkspaceWorkerThread)
        or canonical is not construction
        or not construction._matches(record._owner_token, record._record_token)
    ):
        raise TypeError(_FAILURE)
    coordinator = bundle._lifecycle
    if coordinator is None:
        coordinator = _WorkspaceWorkerCoordinator(
            _COORDINATOR_TOKEN,
            owner_token=record._owner_token,
            record_token=record._record_token,
        )
        object.__setattr__(bundle, "_lifecycle", coordinator)
        object.__setattr__(record, "_lifecycle", coordinator)
    if (
        type(coordinator) is not _WorkspaceWorkerCoordinator
        or not coordinator._matches(record._owner_token, record._record_token)
        or record._lifecycle is not coordinator
    ):
        raise TypeError(_FAILURE)
    return bundle, record, raw, coordinator


def _stage_settled_worker_terminal_locked(
    record: registry._WorkspaceWorkerThreadRecord,
    raw: registry._WorkspaceWorkerThread | None,
    coordinator: _WorkspaceWorkerCoordinator,
) -> _WorkspaceWorkerTerminalReceipt | None:
    """Allocate the fixed terminal only from a dead, exactly settled worker."""

    terminal = coordinator._terminal
    if terminal is not None:
        return terminal
    claim_token = coordinator._claim_token
    settlement_token = coordinator._settlement_token
    entry = record._entry
    started = vars(raw).get("_started") if raw is not None else None
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _workspace_filesystem_was_claimed,
    )

    if (
        coordinator._phase == "start-intended"
        and coordinator._claim_token is None
        and not _workspace_filesystem_was_claimed(record._record_token)
        and type(raw) is registry._WorkspaceWorkerThread
        and type(started) is threading.Event
        and _EVENT_IS_SET(started)
        and not _THREAD_IS_ALIVE(raw)
    ):
        object.__setattr__(coordinator, "_workspace_settled", True)
    if coordinator._phase == "claimed" and type(coordinator._claim_token) is object:
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
            _workspace_filesystem_is_settled,
        )

        canonical_workspace_settled = _workspace_filesystem_is_settled(
            record._owner_token,
            record._record_token,
            coordinator._claim_token,
        )
    else:
        canonical_workspace_settled = bool(
            coordinator._phase == "start-intended"
            and coordinator._claim_token is None
            and coordinator._workspace_settled is True
            and not _workspace_filesystem_was_claimed(record._record_token)
        )
    if (
        coordinator._phase not in {"start-intended", "claimed"}
        or coordinator._effect_phase not in {"entered", "returned"}
        or not canonical_workspace_settled
        or coordinator._release_phase != "none"
        or type(settlement_token) is not object
        or (coordinator._phase == "start-intended" and claim_token is not None)
        or (coordinator._phase == "claimed" and claim_token is not settlement_token)
        or type(raw) is not registry._WorkspaceWorkerThread
        or entry is None
        or entry[0] != registry._INITIALIZED
        or entry[1] is not raw
        or record._lifecycle is not coordinator
        or {"is_alive", "join", "run", "start"}.intersection(vars(raw))
        or type(started) is not threading.Event
        or not _EVENT_IS_SET(started)
        or _THREAD_IS_ALIVE(raw)
    ):
        return None
    terminal = _WorkspaceWorkerTerminalReceipt(
        _TERMINAL_TOKEN,
        owner_token=record._owner_token,
        record_token=record._record_token,
        started=True,
    )
    object.__setattr__(coordinator, "_terminal", terminal)
    object.__setattr__(coordinator, "_phase", "terminal-pending")
    return terminal


def _workspace_worker_no_effect_is_proven(
    record: registry._WorkspaceWorkerThreadRecord,
    raw: registry._WorkspaceWorkerThread | None,
    coordinator: _WorkspaceWorkerCoordinator,
    terminal: _WorkspaceWorkerTerminalReceipt,
) -> bool:
    if (
        terminal.started is not False
        or coordinator._effect_phase not in {"none", "rejected"}
        or coordinator._joined is not True
    ):
        return False
    state = record._entry[0]
    if state in {registry._FAILED, registry._POISONED}:
        return True
    if state != registry._INITIALIZED or type(raw) is not registry._WorkspaceWorkerThread:
        return False
    started = vars(raw).get("_started")
    return bool(type(started) is threading.Event and not _EVENT_IS_SET(started))


def _publish_start_candidate(
    destination: object,
    owner_token: object,
    receipt: _WorkspaceWorkerStartReceipt,
    deadline: float,
) -> bool:
    published, acquired = destination._publish_before(
        _DESTINATION_TOKEN,
        owner_token,
        receipt,
        deadline,
    )
    if not acquired or published is not receipt:
        return False
    stored, acquired = destination._read_before(owner_token, deadline)
    return bool(acquired and stored is receipt)


def _exact_start_rejection(raw: object) -> bool:
    try:
        if type(raw) is not registry._WorkspaceWorkerThread:
            return False
        values = vars(raw)
        started = values.get("_started")
        with threading._active_limbo_lock:
            active = raw in threading._active.values()
            limbo = raw in threading._limbo
        return bool(
            values.get("_initialized") is True
            and values.get("_ident") is None
            and values.get("_native_id") is None
            and type(started) is threading.Event
            and not started.is_set()
            and values.get("_is_stopped", False) is False
            and values.get("_tstate_lock") is None
            and not active
            and not limbo
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _wait_for_worker_handoff(
    coordinator: _WorkspaceWorkerCoordinator,
    deadline: float,
    prepared_destination: object,
    request: object,
    owner_token: object,
    record_token: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _WorkspacePreparedReceipt,
    )

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return False
        if coordinator._phase == "terminal":
            return True
        receipt, acquired = prepared_destination._read_before(
            request,
            min(deadline, time.monotonic() + _TAKE_WAIT_SECONDS),
        )
        if (
            acquired
            and type(receipt) is _WorkspacePreparedReceipt
            and receipt._matches(owner_token, record_token, require_active=True)
        ):
            return True
        time.sleep(min(_TAKE_WAIT_SECONDS, remaining))


@contextmanager
def _locked_before(lock: object, deadline: float) -> Iterator[None]:
    remaining = max(0.0, deadline - time.monotonic())
    acquired = lock.acquire(blocking=False) if remaining <= 0.0 else lock.acquire(timeout=remaining)
    if not acquired:
        raise RuntimeError("Relay Linux workspace worker lifecycle deadline expired")
    try:
        yield
    finally:
        lock.release()


__all__: list[str] = []

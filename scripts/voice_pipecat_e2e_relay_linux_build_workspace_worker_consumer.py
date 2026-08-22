"""Canonical cap-one pin preventing release of a consumed workspace worker."""

from __future__ import annotations

import math
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _workspace_worker_ownership_locked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
    _workspace_worker_binding_deadline,
    _workspace_worker_locked_before,
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

_FAILURE = "Relay Linux workspace worker consumer pin is invalid"
_CONSUMERS: weakref.WeakKeyDictionary[
    _WorkspaceWorkerBundle,
    tuple[_WorkspaceWorkerThreadReceipt, object],
] = weakref.WeakKeyDictionary()


def _pin_workspace_worker_consumer(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    receipt: _WorkspaceWorkerThreadReceipt,
    consumer_key: object,
    deadline: float,
) -> bool:
    """Pin one exact initialized record before publishing an outer association."""

    if not _valid_inputs(owner, bundle, receipt, consumer_key, deadline):
        return False
    with _workspace_worker_binding_deadline(deadline):
        binding = _resolve_workspace_worker_thread_binding(owner, bundle)
        with _workspace_worker_ownership_locked(deadline):
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                record = _RECORDS.get(bundle)
                if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(binding):
                    return False
                with _workspace_worker_locked_before(record._lock, deadline):
                    if not _record_matches(record, receipt):
                        return False
                    expected = (receipt, consumer_key)
                    current = _CONSUMERS.get(bundle)
                    if current is None:
                        if _CONSUMERS:
                            return False
                        _store_workspace_worker_consumer(bundle, expected)
                    elif not _consumer_state_matches(current, receipt, consumer_key):
                        return False
                    return _consumer_state_matches(
                        _CONSUMERS.get(bundle),
                        receipt,
                        consumer_key,
                    )


def _workspace_worker_consumer_matches(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    receipt: _WorkspaceWorkerThreadReceipt,
    consumer_key: object,
    deadline: float,
) -> bool | None:
    """Return exact pin truth, or None when bounded lock admission is unresolved."""

    if not _valid_inputs(owner, bundle, receipt, consumer_key, deadline):
        return False
    try:
        with _workspace_worker_binding_deadline(deadline):
            binding = _resolve_workspace_worker_thread_binding(owner, bundle)
            with _workspace_worker_ownership_locked(deadline):
                with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                    record = _RECORDS.get(bundle)
                    if record is None:
                        return False if not _RECORDS else None
                    if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(
                        binding
                    ):
                        return False
                    with _workspace_worker_locked_before(record._lock, deadline):
                        return bool(
                            _record_matches(record, receipt)
                            and _consumer_state_matches(
                                _CONSUMERS.get(bundle),
                                receipt,
                                consumer_key,
                            )
                            and len(_CONSUMERS) == 1
                        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _clear_workspace_worker_consumer(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    receipt: _WorkspaceWorkerThreadReceipt,
    consumer_key: object,
    deadline: float,
) -> bool:
    """Clear only the exact outer pin immediately before deliberate release."""

    if not _valid_inputs(owner, bundle, receipt, consumer_key, deadline):
        return False
    with _workspace_worker_binding_deadline(deadline):
        binding = _resolve_workspace_worker_thread_binding(owner, bundle)
        with _workspace_worker_ownership_locked(deadline):
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                record = _RECORDS.get(bundle)
                if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(binding):
                    return False
                with _workspace_worker_locked_before(record._lock, deadline):
                    if (
                        not _record_matches(record, receipt)
                        or not _consumer_state_matches(
                            _CONSUMERS.get(bundle),
                            receipt,
                            consumer_key,
                        )
                        or len(_CONSUMERS) != 1
                    ):
                        return False
                    _pop_workspace_worker_consumer(bundle)
                    return bundle not in _CONSUMERS


def _workspace_worker_consumer_is_absent(
    bundle: _WorkspaceWorkerBundle,
    deadline: float,
) -> bool | None:
    if (
        type(bundle) is not _WorkspaceWorkerBundle
        or type(deadline) is not float
        or not math.isfinite(deadline)
    ):
        return False
    try:
        with _workspace_worker_ownership_locked(deadline):
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                return not _CONSUMERS
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _workspace_worker_graph_is_empty(
    bundle: _WorkspaceWorkerBundle,
    deadline: float,
) -> bool | None:
    """Prove the cap-one worker registry and consumer registry empty together."""

    if (
        type(bundle) is not _WorkspaceWorkerBundle
        or type(deadline) is not float
        or not math.isfinite(deadline)
    ):
        return False
    try:
        with _workspace_worker_ownership_locked(deadline):
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                return bool(not _RECORDS and not _CONSUMERS)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _workspace_worker_registries_are_empty(deadline: float) -> bool | None:
    """Prove global cap-one worker and consumer absence under one lock epoch."""

    if type(deadline) is not float or not math.isfinite(deadline):
        return False
    try:
        with _workspace_worker_ownership_locked(deadline):
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                return bool(not _RECORDS and not _CONSUMERS)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _workspace_worker_consumer_blocks_release_locked(
    bundle: _WorkspaceWorkerBundle,
    receipt: _WorkspaceWorkerThreadReceipt,
) -> bool:
    """Called only while the worker registry and exact record are locked."""

    del bundle, receipt
    return bool(_CONSUMERS)


def _record_matches(
    record: _WorkspaceWorkerThreadRecord,
    receipt: _WorkspaceWorkerThreadReceipt,
) -> bool:
    entry = record._entry
    return bool(
        type(entry) is tuple
        and len(entry) == 3
        and type(entry[0]) is str
        and entry[0] == _INITIALIZED
        and type(entry[1]) is _WorkspaceWorkerThread
        and entry[2] is receipt
        and receipt._coherent is True
    )


def _consumer_state_matches(
    state: object,
    receipt: _WorkspaceWorkerThreadReceipt,
    consumer_key: object,
) -> bool:
    return bool(
        type(state) is tuple
        and len(state) == 2
        and state[0] is receipt
        and state[1] is consumer_key
    )


def _valid_inputs(
    owner: object,
    bundle: object,
    receipt: object,
    consumer_key: object,
    deadline: object,
) -> bool:
    return bool(
        type(owner) is _RelayLinuxBuildWorkspaceOwner
        and type(bundle) is _WorkspaceWorkerBundle
        and type(receipt) is _WorkspaceWorkerThreadReceipt
        and consumer_key is not None
        and type(deadline) is float
        and math.isfinite(deadline)
    )


def _store_workspace_worker_consumer(
    bundle: _WorkspaceWorkerBundle,
    state: tuple[_WorkspaceWorkerThreadReceipt, object],
) -> None:
    _CONSUMERS[bundle] = state


def _pop_workspace_worker_consumer(bundle: _WorkspaceWorkerBundle) -> None:
    _CONSUMERS.pop(bundle, None)


__all__: list[str] = []

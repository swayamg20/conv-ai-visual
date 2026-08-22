"""Exact workspace-worker pin owned by the private relay Linux executor."""

from __future__ import annotations

import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer import (
    _clear_workspace_worker_consumer,
    _pin_workspace_worker_consumer,
    _workspace_worker_consumer_is_absent,
    _workspace_worker_consumer_matches,
    _workspace_worker_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _AUTHORITY_KEYS,
    _DESTINATION_KEYS,
    _EXECUTORS,
    _FAILURE,
    _LOCK,
    _OWNER_KEYS,
    _WORKSPACE_RELEASES,
    _canonical_executor_key,
    _executor_record_matches,
    _executor_value_matches,
    _RelayLinuxExecutorCleanupAuthority,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
    _store_executor_record,
    _store_workspace_release_evidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_worker_contract import (
    _workspace_worker_receipt_is_current,
)


def _bind_relay_linux_executor_workspace(
    owner: _RelayLinuxExecutorOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
) -> bool:
    """Pin the exact worker before publishing the outer workspace binding."""

    if not _executor_value_matches(owner, owner._destination):
        raise _RelayLinuxExecutorError(_FAILURE)
    owner_token = owner._workspace_owner._cleanup_authority._key
    if (
        type(bundle) is not _WorkspaceWorkerBundle
        or not bundle._matches(owner_token, owner._workspace_owner._receipt_destination)
        or type(construction) is not _WorkspaceWorkerThreadReceipt
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    try:
        record_token = object.__getattribute__(construction, "_record_token")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _RelayLinuxExecutorError(_FAILURE) from None
    if not construction._matches(owner_token, record_token):
        raise _RelayLinuxExecutorError(_FAILURE)
    key = _canonical_executor_key(owner, owner._destination)
    if key is None:
        raise _RelayLinuxExecutorError(_FAILURE)
    phase = _intend_workspace_pin(owner, bundle, construction, key)
    deadline = time.monotonic() + 0.05
    if phase == "workspace-bound":
        return (
            _workspace_worker_consumer_matches(
                owner._workspace_owner,
                bundle,
                construction,
                key,
                deadline,
            )
            is True
        )
    if not _pin_workspace_worker_consumer(
        owner._workspace_owner,
        bundle,
        construction,
        key,
        deadline,
    ):
        return False
    with _LOCK:
        record = _EXECUTORS.get(key)
        if not _executor_record_matches(
            record,
            owner,
            owner._destination,
            "workspace-pin-intended",
        ):
            return False
        bound = (*record[:5], "workspace-bound")
        _store_executor_record(key, bound)
        return _bound_record_matches(
            _EXECUTORS.get(key),
            owner,
            owner._destination,
        )


def _resolve_relay_linux_executor_workspace(
    owner: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
) -> tuple[_WorkspaceWorkerBundle, _WorkspaceWorkerThreadReceipt] | None:
    """Resolve one exact bound worker graph without retaining either lock."""

    if not _executor_value_matches(owner, destination):
        return None
    key = _canonical_executor_key(owner, destination)
    if key is None:
        return None
    with _LOCK:
        initial = _EXECUTORS.get(key)
        if not _bound_record_matches(initial, owner, destination):
            return None
        bundle, construction = initial[2], initial[3]
    if (
        _workspace_worker_consumer_matches(
            owner._workspace_owner,
            bundle,
            construction,
            key,
            time.monotonic() + 0.05,
        )
        is not True
    ):
        return None
    with _LOCK:
        record = _EXECUTORS.get(key)
        return (record[2], record[3]) if record is initial else None


def _intend_relay_linux_executor_workspace_release(
    authority: _RelayLinuxExecutorCleanupAuthority,
) -> (
    tuple[
        _RelayLinuxBuildWorkspaceOwner,
        _WorkspaceWorkerBundle,
        _WorkspaceWorkerThreadReceipt,
    ]
    | None
):
    """Clear the exact worker pin before a deliberate outer-owned release."""

    resolved = _resolve_releasing_record(authority)
    if resolved is None:
        return None
    key, owner, bundle, construction = resolved
    pinned = _workspace_worker_consumer_matches(
        owner._workspace_owner,
        bundle,
        construction,
        key,
        time.monotonic() + 0.05,
    )
    if pinned is True:
        if not _clear_workspace_worker_consumer(
            owner._workspace_owner,
            bundle,
            construction,
            key,
            time.monotonic() + 0.05,
        ):
            return None
    elif (
        _workspace_worker_consumer_is_absent(
            bundle,
            time.monotonic() + 0.05,
        )
        is not True
    ):
        return None
    return owner._workspace_owner, bundle, construction


def _complete_relay_linux_executor_workspace_release(
    authority: _RelayLinuxExecutorCleanupAuthority,
) -> bool:
    """Restore unstarted-release eligibility only after exact worker absence."""

    if type(authority) is not _RelayLinuxExecutorCleanupAuthority:
        return False
    key = _AUTHORITY_KEYS.get(authority)
    if type(key) is not _RelayLinuxExecutorKey:
        return False
    with _LOCK:
        record = _EXECUTORS.get(key)
        if (
            type(record) is not tuple
            or len(record) != 6
            or type(record[5]) is not str
            or record[5]
            not in {
                "workspace-releasing",
                "workspace-released",
            }
        ):
            return False
        owner, destination, bundle, construction = record[:4]
        if not (
            type(owner) is _RelayLinuxExecutorOwner
            and type(destination) is _RelayLinuxExecutorDestination
            and type(bundle) is _WorkspaceWorkerBundle
            and type(construction) is _WorkspaceWorkerThreadReceipt
            and (
                (record[5] == "workspace-releasing" and record[4] is None)
                or (record[5] == "workspace-released" and type(record[4]) is object)
            )
            and _OWNER_KEYS.get(owner) is key
            and _DESTINATION_KEYS.get(destination) is key
            and _AUTHORITY_KEYS.get(authority) is key
        ):
            return False
    if (
        _workspace_worker_receipt_is_current(
            owner._workspace_owner,
            bundle,
            construction,
            time.monotonic() + 0.05,
        )
        != "absent"
        or _workspace_worker_registries_are_empty(time.monotonic() + 0.05) is not True
    ):
        return False
    with _LOCK:
        if _EXECUTORS.get(key) is not record:
            return False
        evidence = _WORKSPACE_RELEASES.get(key)
        if record[5] == "workspace-releasing":
            if evidence is None:
                token = object()
                evidence = (bundle, construction, token)
                _store_workspace_release_evidence(key, evidence)
            if not _release_evidence_matches(evidence, bundle, construction):
                return False
        elif not _release_evidence_matches(evidence, bundle, construction):
            return False
        if record[5] == "workspace-released":
            released = record
        else:
            released = (*record[:4], evidence[2], "workspace-released")
            _store_executor_record(key, released)
        return bool(_EXECUTORS.get(key) is released and _WORKSPACE_RELEASES.get(key) is evidence)


def _intend_workspace_pin(
    owner: _RelayLinuxExecutorOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    key: _RelayLinuxExecutorKey,
) -> str:
    with _LOCK:
        record = _EXECUTORS.get(key)
        for phase in ("workspace-bound", "workspace-pin-intended"):
            if _executor_record_matches(record, owner, owner._destination, phase):
                if (
                    record[2] is not bundle
                    or record[3] is not construction
                    or record[4] is not None
                ):
                    raise _RelayLinuxExecutorError(_FAILURE)
                return phase
        if not _executor_record_matches(record, owner, owner._destination, "preowned"):
            raise _RelayLinuxExecutorError(_FAILURE)
        intended = (owner, owner._destination, bundle, construction, None, "workspace-pin-intended")
        _store_executor_record(key, intended)
        if _EXECUTORS.get(key) is not intended:
            raise _RelayLinuxExecutorError(_FAILURE)
        return "workspace-pin-intended"


def _bound_record_matches(
    record: object,
    owner: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
) -> bool:
    return bool(
        _executor_record_matches(record, owner, destination, "workspace-bound")
        and type(record[2]) is _WorkspaceWorkerBundle
        and type(record[3]) is _WorkspaceWorkerThreadReceipt
        and record[4] is None
    )


def _release_evidence_matches(
    evidence: object,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
) -> bool:
    return bool(
        type(evidence) is tuple
        and len(evidence) == 3
        and evidence[0] is bundle
        and evidence[1] is construction
        and type(evidence[2]) is object
    )


def _resolve_releasing_record(
    authority: _RelayLinuxExecutorCleanupAuthority,
) -> (
    tuple[
        _RelayLinuxExecutorKey,
        _RelayLinuxExecutorOwner,
        _WorkspaceWorkerBundle,
        _WorkspaceWorkerThreadReceipt,
    ]
    | None
):
    if type(authority) is not _RelayLinuxExecutorCleanupAuthority:
        return None
    key = _AUTHORITY_KEYS.get(authority)
    if type(key) is not _RelayLinuxExecutorKey:
        return None
    with _LOCK:
        record = _EXECUTORS.get(key)
        if (
            type(record) is not tuple
            or len(record) != 6
            or type(record[5]) is not str
            or record[5]
            not in {
                "workspace-pin-intended",
                "workspace-bound",
                "workspace-releasing",
            }
        ):
            return None
        owner, destination, bundle, construction = record[:4]
        if not (
            type(owner) is _RelayLinuxExecutorOwner
            and type(destination) is _RelayLinuxExecutorDestination
            and type(bundle) is _WorkspaceWorkerBundle
            and type(construction) is _WorkspaceWorkerThreadReceipt
            and _OWNER_KEYS.get(owner) is key
            and _DESTINATION_KEYS.get(destination) is key
            and _AUTHORITY_KEYS.get(authority) is key
        ):
            return None
        if record[5] == "workspace-releasing":
            releasing = record
        else:
            releasing = (*record[:5], "workspace-releasing")
            _store_executor_record(key, releasing)
        if _EXECUTORS.get(key) is not releasing:
            return None
        return key, owner, bundle, construction


__all__: list[str] = []

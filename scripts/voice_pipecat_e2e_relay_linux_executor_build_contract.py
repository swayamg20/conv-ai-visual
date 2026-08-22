"""Exact private graph predicates for one consumed executor build."""

from __future__ import annotations

import time
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_absence_reservation_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _DIST_PARENT,
    _RUN_ID,
    _RUN_PREFIX,
    _WORKSPACE_NAME,
    _RelayLinuxBuildWorkspaceRequest,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _retire_workspace_built_consumer_tombstone,
    _workspace_built_consumer_all_state_is_empty,
    _workspace_built_consumer_is_forgotten,
    _workspace_built_consumer_is_in_use,
    _workspace_built_consumer_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values import (
    _WorkspaceBuiltConsumerToken,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _workspace_request_spawn_fingerprint,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_BY_COMMAND,
    _BUILT_LEASES,
    _workspace_built_receipt_is_stable_handoff,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract import (
    _canonical_workspace_built_deadline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer import (
    _workspace_worker_consumer_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _BINDING_TOKEN,
    _BINDINGS_BY_BUILT,
    _BUILD_RETIREMENTS,
    _EVIDENCE_BY_KEY,
    _KEYS_BY_BINDING,
    _RELEASE_BINDINGS,
    _pop_executor_build_release,
    _pop_executor_build_retirement,
    _pop_executor_built_evidence,
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
    _store_binding_by_built,
    _store_evidence_by_key,
    _store_executor_build_retirement,
    _store_key_by_binding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_reconcile import (
    _reconcile,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _AUTHORITY_KEYS,
    _DESTINATION_KEYS,
    _EXECUTORS,
    _FIXED_PORTS,
    _LOCK,
    _OWNER_KEYS,
    _PORT_RESERVATIONS,
    _canonical_executor_key,
    _executor_record_matches,
    _executor_source_evidence_graph_matches,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
    _store_executor_record,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT

_LOCK_SLICE_SECONDS = 0.05
_PATH_TYPE = type(Path("/"))
_FAILURE = "Relay Linux executor built consumption is invalid"


def _store_outer_phase(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    expected: str,
    target: str,
    *,
    clear: bool = False,
) -> bool:
    with _LOCK:
        record = _EXECUTORS.get(evidence.key)
        if _outer_record_matches(evidence, record, target):
            return True
        if not _outer_record_matches(evidence, record, expected):
            return False
        stored = (*record[:4], None if clear else evidence.binding, target)
        _store_executor_record(evidence.key, stored)
        return _outer_record_matches(evidence, _EXECUTORS.get(evidence.key), target)


def _consumed_binding_matches(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return bool(
        _evidence_matches(evidence, evidence.executor, evidence.destination, evidence.built)
        and _outer_phase_matches(evidence, "build-consumed")
        and _workspace_built_consumer_is_in_use(evidence.built, evidence.consumer)
    )


def _pinned_workspace_matches(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    if not _outer_phase_matches(evidence, "consume-intended"):
        return False
    return (
        _workspace_worker_consumer_matches(
            evidence.executor._workspace_owner,
            evidence.bundle,
            evidence.construction,
            evidence.key,
            time.monotonic() + _LOCK_SLICE_SECONDS,
        )
        is True
    )


def _outer_phase_matches(evidence: _RelayLinuxExecutorBuiltEvidence, phase: str) -> bool:
    with _LOCK:
        return _outer_record_matches(evidence, _EXECUTORS.get(evidence.key), phase)


def _outer_record_matches(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    record: object,
    phase: str,
) -> bool:
    expected_binding = None if phase == "workspace-bound" else evidence.binding
    return bool(
        _executor_record_matches(record, evidence.executor, evidence.destination, phase)
        and record[2] is evidence.bundle
        and record[3] is evidence.construction
        and record[4] is expected_binding
        and _binding_maps_match(evidence)
    )


def _evidence_for_binding(binding: object) -> _RelayLinuxExecutorBuiltEvidence | None:
    if type(binding) is not _RelayLinuxExecutorBuiltBinding:
        return None
    key = _KEYS_BY_BINDING.get(binding)
    evidence = _EVIDENCE_BY_KEY.get(key) if key is not None else None
    return evidence if type(evidence) is _RelayLinuxExecutorBuiltEvidence else None


def _evidence_matches(
    evidence: object,
    executor: object,
    destination: object,
    built: object,
) -> bool:
    return bool(
        _evidence_shape_matches(evidence, executor, destination, built)
        and _binding_maps_match(evidence)
    )


def _evidence_shape_matches(
    evidence: object,
    executor: object,
    destination: object,
    built: object,
) -> bool:
    if type(evidence) is not _RelayLinuxExecutorBuiltEvidence:
        return False
    try:
        return bool(
            type(evidence.binding) is _RelayLinuxExecutorBuiltBinding
            and evidence.binding._authentic is _BINDING_TOKEN
            and evidence.executor is executor
            and evidence.destination is destination
            and evidence.built is built
            and type(evidence.key) is _RelayLinuxExecutorKey
            and type(evidence.bundle) is _WorkspaceWorkerBundle
            and type(evidence.construction) is _WorkspaceWorkerThreadReceipt
            and type(evidence.command) is _WorkspaceBuildCommand
            and type(evidence.consumer) is _WorkspaceBuiltConsumerToken
            and type(evidence.owner_token) is object
            and type(evidence.record_token) is object
            and type(evidence.digest) is bytes
            and len(evidence.digest) == 32
            and type(evidence.process_receipt) is _RelayLinuxBuildProcessReceipt
            and type(evidence.source) is RelayProbeSource
            and type(evidence.source_commit) is str
            and _build_process_absence_reservation_matches(
                evidence.reservation,
                evidence.key,
            )
            and evidence.request is evidence.executor._workspace_owner._request
            and _request_values_match(evidence.request, evidence.request_values)
            and evidence.authority is evidence.executor._cleanup_authority
            and _AUTHORITY_KEYS.get(evidence.authority) is evidence.key
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _binding_maps_match(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return bool(
        _EVIDENCE_BY_KEY.get(evidence.key) is evidence
        and _KEYS_BY_BINDING.get(evidence.binding) is evidence.key
        and _BINDINGS_BY_BUILT.get(evidence.built) is evidence.binding
        and len(_EVIDENCE_BY_KEY) == 1
        and len(_KEYS_BY_BINDING) == 1
        and len(_BINDINGS_BY_BUILT) == 1
    )


def _cleanup_evidence_matches(evidence: object) -> bool:
    if type(evidence) is not _RelayLinuxExecutorBuiltEvidence:
        return False
    record = _EXECUTORS.get(evidence.key)
    return bool(
        type(evidence.binding) is _RelayLinuxExecutorBuiltBinding
        and type(evidence.key) is _RelayLinuxExecutorKey
        and type(evidence.executor) is _RelayLinuxExecutorOwner
        and type(evidence.destination) is _RelayLinuxExecutorDestination
        and type(evidence.bundle) is _WorkspaceWorkerBundle
        and type(evidence.construction) is _WorkspaceWorkerThreadReceipt
        and type(evidence.consumer) is _WorkspaceBuiltConsumerToken
        and evidence.authority is not None
        and _AUTHORITY_KEYS.get(evidence.authority) is evidence.key
        and _OWNER_KEYS.get(evidence.executor) is evidence.key
        and _DESTINATION_KEYS.get(evidence.destination) is evidence.key
        and type(record) is tuple
        and len(record) == 6
        and record[0] is evidence.executor
        and record[1] is evidence.destination
        and record[2] is evidence.bundle
        and record[3] is evidence.construction
        and record[4] is evidence.binding
        and type(record[5]) is str
        and record[5] in {"build-consumed", "use-release-intended", "build-revoked-acknowledged"}
        and len(_EXECUTORS) == 1
        and len(_PORT_RESERVATIONS) == 1
        and next(iter(_PORT_RESERVATIONS)) is _FIXED_PORTS
        and _PORT_RESERVATIONS.get(_FIXED_PORTS) is evidence.key
        and _executor_source_evidence_graph_matches(
            evidence.key,
            evidence.executor,
            evidence.destination,
        )
        and evidence.source is evidence.executor._source
        and evidence.source_commit is object.__getattribute__(evidence.source, "_commit_sha")
        and _binding_maps_match(evidence)
    )


def _repair_binding_maps(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    failures: list[BaseException | None],
) -> bool:
    if (
        any(candidate is not evidence.key for candidate in _EVIDENCE_BY_KEY)
        or any(candidate is not evidence.binding for candidate in _KEYS_BY_BINDING)
        or any(candidate is not evidence.built for candidate in _BINDINGS_BY_BUILT)
        or (
            _EVIDENCE_BY_KEY.get(evidence.key) is not None
            and _EVIDENCE_BY_KEY.get(evidence.key) is not evidence
        )
        or (
            _KEYS_BY_BINDING.get(evidence.binding) is not None
            and _KEYS_BY_BINDING.get(evidence.binding) is not evidence.key
        )
        or (
            _BINDINGS_BY_BUILT.get(evidence.built) is not None
            and _BINDINGS_BY_BUILT.get(evidence.built) is not evidence.binding
        )
    ):
        return False
    return bool(
        _reconcile(
            lambda: _store_and_match(
                _store_evidence_by_key,
                evidence.key,
                evidence,
                lambda: _EVIDENCE_BY_KEY.get(evidence.key) is evidence,
            ),
            failures,
        )
        and _reconcile(
            lambda: _store_and_match(
                _store_key_by_binding,
                evidence.binding,
                evidence.key,
                lambda: _KEYS_BY_BINDING.get(evidence.binding) is evidence.key,
            ),
            failures,
        )
        and _reconcile(
            lambda: _store_and_match(
                _store_binding_by_built,
                evidence.built,
                evidence.binding,
                lambda: _BINDINGS_BY_BUILT.get(evidence.built) is evidence.binding,
            ),
            failures,
        )
        and _binding_maps_match(evidence)
    )


def _store_and_match(store: object, key: object, value: object, matches: object) -> bool:
    if not callable(store) or not callable(matches):
        return False
    store(key, value)
    return bool(matches())


def _existing_evidence(
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    built: _WorkspaceBuiltReceipt,
    failures: list[BaseException | None],
) -> _RelayLinuxExecutorBuiltEvidence | None:
    key = _canonical_executor_key(executor, destination)
    if key is None:
        return None
    with _LOCK:
        evidence = _EVIDENCE_BY_KEY.get(key)
        if not _evidence_shape_matches(evidence, executor, destination, built):
            return None
        return evidence if _repair_binding_maps(evidence, failures) else None


def _retire_unconsumed_evidence(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    if not (
        _outer_phase_matches(evidence, "workspace-bound")
        and _workspace_built_consumer_all_state_is_empty()
    ):
        return False
    _pop_executor_built_evidence(evidence)
    return bool(
        evidence.key not in _EVIDENCE_BY_KEY
        and evidence.binding not in _KEYS_BY_BINDING
        and evidence.built not in _BINDINGS_BY_BUILT
    )


def _unconsumed_attempt_is_restored(
    executor: object,
    destination: object,
    key: object,
) -> bool:
    if (
        type(executor) is not _RelayLinuxExecutorOwner
        or type(destination) is not _RelayLinuxExecutorDestination
        or type(key) is not _RelayLinuxExecutorKey
    ):
        return False
    with _LOCK:
        record = _EXECUTORS.get(key)
        return bool(
            _executor_record_matches(record, executor, destination, "workspace-bound")
            and type(record[2]) is _WorkspaceWorkerBundle
            and type(record[3]) is _WorkspaceWorkerThreadReceipt
            and record[4] is None
            and not _EVIDENCE_BY_KEY
            and not _KEYS_BY_BINDING
            and not _BINDINGS_BY_BUILT
            and not _RELEASE_BINDINGS
            and not _BUILD_RETIREMENTS
            and _workspace_built_consumer_all_state_is_empty()
        )


def _retire_released_executor_built_state(key: object) -> bool:
    if type(key) is not _RelayLinuxExecutorKey:
        return False
    marker = _BUILD_RETIREMENTS.get(key)
    released = _RELEASE_BINDINGS.get(key)
    if marker is None and released is None:
        return bool(
            not _BUILD_RETIREMENTS
            and not _RELEASE_BINDINGS
            and not _EVIDENCE_BY_KEY
            and not _KEYS_BY_BINDING
            and not _BINDINGS_BY_BUILT
        )
    evidence = marker if marker is not None else released
    if not _released_retirement_evidence_matches(key, evidence, marker, released):
        return False
    if marker is None:
        if not (
            _binding_maps_match(evidence)
            and _workspace_built_consumer_is_forgotten(
                evidence.built,
                evidence.consumer,
            )
            and _workspace_built_consumer_registries_are_empty()
        ):
            return False
        _store_executor_build_retirement(key, evidence)
        if not _released_retirement_evidence_matches(
            key,
            evidence,
            _BUILD_RETIREMENTS.get(key),
            _RELEASE_BINDINGS.get(key),
        ):
            return False
    _pop_executor_built_evidence(evidence)
    if (
        evidence.key in _EVIDENCE_BY_KEY
        or evidence.binding in _KEYS_BY_BINDING
        or evidence.built in _BINDINGS_BY_BUILT
    ):
        return False
    if not _retire_workspace_built_consumer_tombstone(evidence.built, evidence.consumer):
        return False
    _pop_executor_build_release(evidence.key)
    if _RELEASE_BINDINGS:
        return False
    _pop_executor_build_retirement(evidence.key)
    return bool(
        not _BUILD_RETIREMENTS
        and not _RELEASE_BINDINGS
        and not _EVIDENCE_BY_KEY
        and not _KEYS_BY_BINDING
        and not _BINDINGS_BY_BUILT
    )


def _released_retirement_evidence_matches(
    key: _RelayLinuxExecutorKey,
    evidence: object,
    marker: object,
    released: object,
) -> bool:
    return bool(
        type(evidence) is _RelayLinuxExecutorBuiltEvidence
        and evidence.key is key
        and (marker is None or marker is evidence)
        and (released is None or released is evidence)
        and all(candidate is key for candidate in _BUILD_RETIREMENTS)
        and all(candidate is key for candidate in _RELEASE_BINDINGS)
        and all(candidate is key for candidate in _EVIDENCE_BY_KEY)
        and all(candidate is evidence.binding for candidate in _KEYS_BY_BINDING)
        and all(candidate is evidence.built for candidate in _BINDINGS_BY_BUILT)
        and _workspace_built_consumer_registries_are_empty()
    )


def _request_values(request: object) -> tuple[object, ...] | None:
    if type(request) is not _RelayLinuxBuildWorkspaceRequest:
        return None
    try:
        values = (
            request._source_root,
            request._run_parent,
            request._run_root,
            request._workspace,
            request._node,
            request._node_modules,
            request._next_cli,
            request._dist_path,
            request._run_id,
            request._environment,
            _workspace_request_spawn_fingerprint(request),
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None
    paths = values[:8]
    run_id = values[8]
    return (
        values
        if all(type(path) is _PATH_TYPE for path in paths)
        and values[0] is WEB_ROOT
        and type(run_id) is str
        and _RUN_ID.fullmatch(run_id) is not None
        and values[2] == values[1] / f"{_RUN_PREFIX}{run_id}"
        and values[3] == values[2] / _WORKSPACE_NAME
        and values[5] == values[0] / "node_modules"
        and values[7] == values[3] / _DIST_PARENT / run_id
        and type(values[9]) is tuple
        and type(values[10]) is bytes
        and len(values[10]) == 32
        else None
    )


def _request_values_match(request: object, expected: object) -> bool:
    current = _request_values(request)
    return bool(
        type(current) is tuple
        and len(current) == 11
        and type(expected) is tuple
        and len(expected) == 11
        and all(current[index] is expected[index] for index in range(10))
        and type(current[10]) is bytes
        and type(expected[10]) is bytes
        and current[10] == expected[10]
    )


def _active_lease_matches(
    executor: _RelayLinuxExecutorOwner,
    construction: _WorkspaceWorkerThreadReceipt,
    built: _WorkspaceBuiltReceipt,
    state: object,
) -> bool:
    try:
        record_token = object.__getattribute__(construction, "_record_token")
        owner_token = executor._workspace_owner._cleanup_authority._key
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return bool(
        type(state) is tuple
        and len(state) == 6
        and state[0] is owner_token
        and state[1] is record_token
        and type(state[2]) is _WorkspaceBuildCommand
        and type(state[3]) is bytes
        and len(state[3]) == 32
        and type(state[4]) is _RelayLinuxBuildProcessReceipt
        and type(state[5]) is str
        and state[5] == "active"
        and _BUILT_BY_COMMAND.get(state[2]) is built
        and construction._matches(owner_token, record_token)
    )


def _active_evidence_lease_matches(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    lease: object,
) -> bool:
    return bool(
        type(lease) is tuple
        and len(lease) == 6
        and lease[0] is evidence.owner_token
        and lease[1] is evidence.record_token
        and lease[2] is evidence.command
        and type(lease[3]) is bytes
        and lease[3] == evidence.digest
        and lease[4] is evidence.process_receipt
        and type(lease[5]) is str
        and lease[5] == "active"
        and _BUILT_BY_COMMAND.get(evidence.command) is evidence.built
    )


def _fresh_active_consumption_deadline(
    executor: _RelayLinuxExecutorOwner,
    built: _WorkspaceBuiltReceipt,
    operation_deadline: float,
) -> float:
    lease = _BUILT_LEASES.get(built)
    try:
        if not (
            type(lease) is tuple
            and len(lease) == 6
            and lease[0] is executor._workspace_owner._cleanup_authority._key
            and type(lease[1]) is object
            and type(lease[2]) is _WorkspaceBuildCommand
            and type(lease[3]) is bytes
            and len(lease[3]) == 32
            and type(lease[4]) is _RelayLinuxBuildProcessReceipt
            and type(lease[5]) is str
            and lease[5] == "active"
            and _workspace_built_receipt_is_stable_handoff(
                built,
                lease[0],
                lease[1],
            )
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        return _canonical_workspace_built_deadline(
            lease[2],
            lease[0],
            lease[1],
            operation_deadline,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _RelayLinuxExecutorError(_FAILURE) from None


def _consumed_lease_matches(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    lease = _BUILT_LEASES.get(evidence.built)
    return bool(
        type(lease) is tuple
        and len(lease) == 6
        and lease[0] is evidence.owner_token
        and lease[1] is evidence.record_token
        and lease[2] is evidence.command
        and type(lease[3]) is bytes
        and lease[3] == evidence.digest
        and lease[4] is evidence.process_receipt
        and type(lease[5]) is str
        and lease[5] == "consumed"
        and _BUILT_BY_COMMAND.get(evidence.command) is evidence.built
    )


__all__: list[str] = []

"""One-shot outer consumption of a validated workspace build lease."""

from __future__ import annotations

import math
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_absence_reservation_matches,
    _release_build_process_absence,
    _reserve_build_process_absence,
    _resolve_build_process_absence_reservation,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _activate_workspace_built_consumption,
    _intend_workspace_built_consumption,
    _reject_workspace_built_consumption,
    _release_workspace_built_consumer_use,
    _workspace_built_consumer_is_acknowledged,
    _workspace_built_consumer_is_forgotten,
    _workspace_built_consumer_is_in_use,
    _workspace_built_consumer_is_revoked,
    _workspace_built_consumer_is_use_released,
    _workspace_built_consumer_registries_are_empty,
    _workspace_built_consumption_effect_is_reconcilable,
    _workspace_built_consumption_intent_is_rejectable,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values import (
    _new_workspace_built_consumer_token,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_LEASES,
    _workspace_built_receipt_is_revoked,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract import (
    _canonical_workspace_built_deadline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_forget import (
    _RETIRED_RECEIPT_EVIDENCE,
    _RETIREMENT_AUTHORITIES,
    _RETIREMENTS,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _acquire_command_gate,
    _acquire_command_publication_gate,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _BINDINGS_BY_BUILT,
    _BUILD_RETIREMENTS,
    _EVIDENCE_BY_KEY,
    _KEYS_BY_BINDING,
    _RELEASE_BINDINGS,
    _new_executor_built_binding,
    _new_executor_built_evidence,
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
    _store_executor_build_release,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _active_lease_matches,
    _binding_maps_match,
    _cleanup_evidence_matches,
    _consumed_binding_matches,
    _consumed_lease_matches,
    _evidence_for_binding,
    _evidence_shape_matches,
    _existing_evidence,
    _fresh_active_consumption_deadline,
    _outer_phase_matches,
    _outer_record_matches,
    _pinned_workspace_matches,
    _repair_binding_maps,
    _request_values,
    _retire_unconsumed_evidence,
    _store_outer_phase,
    _unconsumed_attempt_is_restored,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _retire_released_executor_built_state as _retire_released_build_state,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_linearize import (
    _store_consumed_workspace_built_lease,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_reconcile import (
    _raise_consume_failure,
    _reconcile,
    _retain_consume_failure,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _EXECUTORS,
    _LOCK,
    _SOURCE_EVIDENCE,
    _canonical_executor_key,
    _executor_record_matches,
    _executor_value_matches,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import WEB_ROOT

_FAILURE = "Relay Linux executor built consumption is invalid"
_LOCK_SLICE_SECONDS = 0.05
_PATH_TYPE = type(Path("/"))
_RECONCILE_ATTEMPTS = 4


def _consume_relay_linux_executor_built_lease(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    built: _WorkspaceBuiltReceipt,
    operation_deadline: float,
) -> _RelayLinuxExecutorBuiltBinding:
    """Consume exactly once while fresh and bind liveness to the outer owner."""

    if (
        type(executor) is not _RelayLinuxExecutorOwner
        or type(destination) is not _RelayLinuxExecutorDestination
        or type(built) is not _WorkspaceBuiltReceipt
        or type(operation_deadline) is not float
        or not math.isfinite(operation_deadline)
        or not _executor_value_matches(executor, destination)
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    failures: list[BaseException | None] = [None, None]
    key = _canonical_executor_key(executor, destination)
    if key is None:
        raise _RelayLinuxExecutorError(_FAILURE)
    replay = _reconcile_existing_consumption(
        executor,
        destination,
        built,
        key,
        failures,
        reject_unconsumed=False,
    )
    if replay is not None:
        return replay
    if (
        _RETIRED_RECEIPT_EVIDENCE.get(built) is not None
        or _RETIREMENTS
        or _RETIREMENT_AUTHORITIES
        or _RELEASE_BINDINGS
        or _BUILD_RETIREMENTS
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    lease = _BUILT_LEASES.get(built)
    if type(lease) is not tuple or len(lease) != 6 or type(lease[2]) is not _WorkspaceBuildCommand:
        raise _RelayLinuxExecutorError(_FAILURE)
    try:
        deadline = _canonical_workspace_built_deadline(
            lease[2],
            lease[0],
            lease[1],
            operation_deadline,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _RelayLinuxExecutorError(_FAILURE) from None
    evidence: _RelayLinuxExecutorBuiltEvidence | None = None
    consumed = False
    try:
        publication_gate = _acquire_command_publication_gate(lease[2], deadline)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _RelayLinuxExecutorError(_FAILURE) from None
    reservation = None
    try:
        try:
            replay = _reconcile_existing_consumption(
                executor,
                destination,
                built,
                key,
                failures,
                reject_unconsumed=True,
            )
            if replay is not None:
                return replay
            reservation = _resolve_build_process_absence_reservation(key)
            deadline = _fresh_active_consumption_deadline(
                executor,
                built,
                operation_deadline,
            )
            if reservation is None:
                try:
                    reservation = _reserve_build_process_absence(key)
                except BaseException as error:
                    _retain_consume_failure(failures, error)
                    reservation = _resolve_build_process_absence_reservation(key)
            if reservation is None:
                raise _RelayLinuxExecutorError(_FAILURE)
            evidence = _resolve_or_intend_binding(
                executor,
                destination,
                built,
                reservation,
                failures,
            )
            if not _pinned_workspace_matches(evidence):
                raise _RelayLinuxExecutorError(_FAILURE)
            gate = _acquire_command_gate(evidence.command, deadline)
            try:
                if not _intend_workspace_built_consumption(
                    receipt=evidence.built,
                    owner=evidence.executor._workspace_owner,
                    bundle=evidence.bundle,
                    construction=evidence.construction,
                    consumer=evidence.consumer,
                    consumer_key=evidence.key,
                    admission=evidence.reservation,
                ):
                    raise _RelayLinuxExecutorError(_FAILURE)
                try:
                    _store_consumed_workspace_built_lease(evidence, gate, deadline)
                except BaseException as error:
                    _retain_consume_failure(failures, error)
                consumed = _consumed_lease_matches(evidence)
                if consumed:
                    _reconcile(
                        lambda: _activate_workspace_built_consumption(
                            evidence.built,
                            evidence.consumer,
                        ),
                        failures,
                    )
                else:
                    _reconcile(
                        lambda: _reject_workspace_built_consumption(
                            evidence.built,
                            evidence.consumer,
                        ),
                        failures,
                    )
            finally:
                gate.lock.release()
        finally:
            publication_gate.publication_lock.release()
    except BaseException as error:
        _retain_consume_failure(failures, error)
    if evidence is not None:
        if consumed or _consumed_lease_matches(evidence):
            consumed = True
            _reconcile_consumed_effect(evidence, failures)
        else:
            _reconcile_unconsumed_abort(evidence, failures)
    if reservation is not None and (
        (evidence is not None and consumed and _consumed_binding_matches(evidence))
        or _unconsumed_attempt_is_restored(executor, destination, key)
    ):
        _reconcile(lambda: _release_build_process_absence(reservation), failures)
    elif reservation is not None:
        _retain_consume_failure(failures, _RelayLinuxExecutorError(_FAILURE))
    _raise_consume_failure(failures)
    if evidence is None or not consumed or not _consumed_binding_matches(evidence):
        raise _RelayLinuxExecutorError(_FAILURE)
    return evidence.binding


def _release_relay_linux_executor_built_use(
    binding: _RelayLinuxExecutorBuiltBinding,
    *,
    cleanup_deadline: float,
) -> bool:
    """Durably end inner use before workspace cancellation may begin."""

    evidence = _evidence_for_binding(binding)
    if (
        evidence is None
        or type(cleanup_deadline) is not float
        or not math.isfinite(cleanup_deadline)
    ):
        return False
    if not _cleanup_evidence_matches(evidence):
        return False
    from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
        _inner_settlement_matches_build,
    )

    if not _inner_settlement_matches_build(evidence):
        return False
    if _workspace_built_consumer_is_in_use(evidence.built, evidence.consumer):
        if not _release_workspace_built_consumer_use(
            evidence.built,
            evidence.consumer,
            cleanup_deadline=cleanup_deadline,
        ):
            return False
    if not _consumer_use_has_ended(evidence):
        return False
    if _outer_phase_matches(evidence, "build-consumed") and not _store_outer_phase(
        evidence,
        "build-consumed",
        "use-release-intended",
    ):
        return False
    return bool(
        _outer_phase_matches(evidence, "use-release-intended")
        or _outer_phase_matches(evidence, "build-revoked-acknowledged")
    )


def _acknowledge_relay_linux_executor_built_revoked(
    binding: _RelayLinuxExecutorBuiltBinding,
    *,
    cleanup_deadline: float,
) -> bool:
    """Acknowledge worker revocation before its filesystem deletion gate."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
        _acknowledge_workspace_built_consumer_revoked,
    )

    evidence = _evidence_for_binding(binding)
    if (
        evidence is None
        or type(cleanup_deadline) is not float
        or not math.isfinite(cleanup_deadline)
    ):
        return False
    if not _cleanup_evidence_matches(evidence):
        return False
    acknowledged = _workspace_built_consumer_is_acknowledged(
        evidence.built,
        evidence.consumer,
    ) or _workspace_built_consumer_is_forgotten(evidence.built, evidence.consumer)
    if not acknowledged and not (
        _workspace_built_receipt_is_revoked(
            evidence.built,
            evidence.owner_token,
            evidence.record_token,
        )
        and _workspace_built_consumer_is_revoked(evidence.built, evidence.consumer)
    ):
        return False
    if _outer_phase_matches(evidence, "use-release-intended") and not _store_outer_phase(
        evidence,
        "use-release-intended",
        "build-revoked-acknowledged",
    ):
        return False
    if not acknowledged:
        acknowledged = _acknowledge_workspace_built_consumer_revoked(
            evidence.built,
            evidence.consumer,
            cleanup_deadline=cleanup_deadline,
        )
    return bool(acknowledged and _outer_phase_matches(evidence, "build-revoked-acknowledged"))


def _consumed_workspace_built_revocation_matches(receipt: object) -> bool:
    binding = _BINDINGS_BY_BUILT.get(receipt)
    evidence = _evidence_for_binding(binding)
    if evidence is None or evidence.built is not receipt:
        return False
    return bool(
        _outer_phase_matches(evidence, "build-revoked-acknowledged")
        and (
            _workspace_built_consumer_is_acknowledged(receipt, evidence.consumer)
            or _workspace_built_consumer_is_forgotten(receipt, evidence.consumer)
        )
    )


def _executor_consumed_build_allows_workspace_release(binding: object) -> bool:
    """Prove revocation acknowledgement before clearing the worker pin."""

    evidence = _evidence_for_binding(binding)
    allowed = bool(
        evidence is not None
        and not _BUILD_RETIREMENTS
        and _consumed_workspace_built_revocation_matches(evidence.built)
        and (
            _workspace_built_consumer_is_acknowledged(
                evidence.built,
                evidence.consumer,
            )
            or _workspace_built_consumer_is_forgotten(
                evidence.built,
                evidence.consumer,
            )
        )
    )
    if not allowed:
        return False
    _store_executor_build_release(evidence.key, evidence)
    return bool(_RELEASE_BINDINGS.get(evidence.key) is evidence and len(_RELEASE_BINDINGS) == 1)


def _executor_consumed_build_is_forgotten(binding: object) -> bool:
    evidence = _evidence_for_binding(binding)
    return bool(
        evidence is not None
        and not _BUILD_RETIREMENTS
        and _binding_maps_match(evidence)
        and _RELEASE_BINDINGS.get(evidence.key) is evidence
        and len(_RELEASE_BINDINGS) == 1
        and _workspace_built_consumer_is_forgotten(evidence.built, evidence.consumer)
        and _workspace_built_consumer_registries_are_empty()
    )


def _executor_released_build_is_forgotten(key: object) -> bool:
    if type(key) is not _RelayLinuxExecutorKey:
        return False
    evidence = _RELEASE_BINDINGS.get(key)
    return bool(
        (
            evidence is None
            and not _BUILD_RETIREMENTS
            and not _RELEASE_BINDINGS
            and not _EVIDENCE_BY_KEY
            and not _KEYS_BY_BINDING
            and not _BINDINGS_BY_BUILT
            and _workspace_built_consumer_registries_are_empty()
        )
        or (
            type(evidence) is _RelayLinuxExecutorBuiltEvidence
            and evidence.key is key
            and not _BUILD_RETIREMENTS
            and len(_RELEASE_BINDINGS) == 1
            and _binding_maps_match(evidence)
            and _workspace_built_consumer_is_forgotten(
                evidence.built,
                evidence.consumer,
            )
            and _workspace_built_consumer_registries_are_empty()
        )
    )


def _retire_released_executor_built_state(key: object) -> bool:
    """Retire private consume evidence after exact worker release."""

    return _retire_released_build_state(key)


def _reconcile_existing_consumption(
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    built: _WorkspaceBuiltReceipt,
    key: _RelayLinuxExecutorKey,
    failures: list[BaseException | None],
    *,
    reject_unconsumed: bool,
) -> _RelayLinuxExecutorBuiltBinding | None:
    existing = _existing_evidence(executor, destination, built, failures)
    if existing is None:
        if _EVIDENCE_BY_KEY or _KEYS_BY_BINDING or _BINDINGS_BY_BUILT:
            _retain_consume_failure(failures, _RelayLinuxExecutorError(_FAILURE))
        _raise_consume_failure(failures)
        return None
    if _workspace_built_consumption_effect_is_reconcilable(
        existing.built,
        existing.consumer,
    ):
        _reconcile_consumed_effect(existing, failures)
        _raise_consume_failure(failures)
        return existing.binding
    if not _workspace_built_consumption_intent_is_rejectable(
        existing.built,
        existing.consumer,
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    if not reject_unconsumed:
        return None
    _reconcile_unconsumed_abort(existing, failures)
    if _unconsumed_attempt_is_restored(executor, destination, key):
        _reconcile(
            lambda: _release_build_process_absence(existing.reservation),
            failures,
        )
    else:
        _retain_consume_failure(failures, _RelayLinuxExecutorError(_FAILURE))
    _raise_consume_failure(failures)
    return None


def _resolve_or_intend_binding(
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    built: _WorkspaceBuiltReceipt,
    reservation: object,
    failures: list[BaseException | None],
) -> _RelayLinuxExecutorBuiltEvidence:
    key = _canonical_executor_key(executor, destination)
    if key is None or not _build_process_absence_reservation_matches(reservation, key):
        raise _RelayLinuxExecutorError(_FAILURE)
    with _LOCK:
        existing = _EVIDENCE_BY_KEY.get(key)
        if existing is not None:
            if not (
                _evidence_shape_matches(existing, executor, destination, built)
                and existing.reservation is reservation
                and _repair_binding_maps(existing, failures)
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            record = _EXECUTORS.get(key)
            if _outer_record_matches(existing, record, "workspace-bound"):
                if not _reconcile(
                    lambda: _store_outer_phase(
                        existing,
                        "workspace-bound",
                        "consume-intended",
                    ),
                    failures,
                ):
                    raise _RelayLinuxExecutorError(_FAILURE)
            elif not any(
                _outer_record_matches(existing, record, phase)
                for phase in {
                    "consume-intended",
                    "build-consumed",
                    "use-release-intended",
                    "build-revoked-acknowledged",
                }
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            return existing
        if _EVIDENCE_BY_KEY or _KEYS_BY_BINDING or _BINDINGS_BY_BUILT:
            raise _RelayLinuxExecutorError(_FAILURE)
        record = _EXECUTORS.get(key)
        if not (
            _executor_record_matches(record, executor, destination, "workspace-bound")
            and type(record[2]) is _WorkspaceWorkerBundle
            and type(record[3]) is _WorkspaceWorkerThreadReceipt
            and record[4] is None
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        evidence = _new_evidence(
            executor,
            destination,
            key,
            record[2],
            record[3],
            built,
            reservation,
        )
        if not _repair_binding_maps(evidence, failures):
            raise _RelayLinuxExecutorError(_FAILURE)
        if not _reconcile(
            lambda: _store_outer_phase(
                evidence,
                "workspace-bound",
                "consume-intended",
            ),
            failures,
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        return evidence


def _new_evidence(
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    key: _RelayLinuxExecutorKey,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    built: _WorkspaceBuiltReceipt,
    reservation: object,
) -> _RelayLinuxExecutorBuiltEvidence:
    lease = _BUILT_LEASES.get(built)
    source = _SOURCE_EVIDENCE.get(key)
    request = executor._workspace_owner._request
    if not (
        _build_process_absence_reservation_matches(reservation, key)
        and _active_lease_matches(executor, construction, built, lease)
        and type(source) is tuple
        and len(source) == 3
        and type(source[0]) is RelayProbeSource
        and type(source[1]) is str
        and source[2] is WEB_ROOT
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    request_values = _request_values(request)
    command_state = _COMMANDS.get(lease[2])
    if (
        request_values is None
        or type(command_state) is not tuple
        or len(command_state) != 6
        or command_state[0] is not lease[0]
        or command_state[1] is not lease[1]
        or command_state[4] != "built"
        or type(command_state[5]) is not bytes
        or command_state[5] != request_values[10]
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    binding = _new_executor_built_binding()
    consumer = _new_workspace_built_consumer_token(
        receipt=built,
        command=lease[2],
        digest=lease[3],
        process_receipt=lease[4],
        consumer_key=key,
        owner_token=lease[0],
        record_token=lease[1],
        request=request,
        controller=bundle._controller,
        owner=executor._workspace_owner,
        bundle=bundle,
        construction=construction,
    )
    return _new_executor_built_evidence(
        binding=binding,
        authority=executor._cleanup_authority,
        executor=executor,
        destination=destination,
        key=key,
        bundle=bundle,
        construction=construction,
        built=built,
        command=lease[2],
        consumer=consumer,
        owner_token=lease[0],
        record_token=lease[1],
        reservation=reservation,
        digest=lease[3],
        process_receipt=lease[4],
        request=request,
        request_values=request_values,
        source=source[0],
        source_commit=source[1],
    )


def _reconcile_consumed_effect(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    failures: list[BaseException | None],
) -> None:
    _reconcile(
        lambda: _activate_workspace_built_consumption(evidence.built, evidence.consumer),
        failures,
    )
    _reconcile(
        lambda: _store_outer_phase(evidence, "consume-intended", "build-consumed"),
        failures,
    )
    _reconcile(lambda: _release_build_process_absence(evidence.reservation), failures)


def _reconcile_unconsumed_abort(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    failures: list[BaseException | None],
) -> None:
    _reconcile(
        lambda: _reject_workspace_built_consumption(evidence.built, evidence.consumer),
        failures,
    )
    _reconcile(
        lambda: _store_outer_phase(
            evidence,
            "consume-intended",
            "workspace-bound",
            clear=True,
        ),
        failures,
    )
    _reconcile(lambda: _retire_unconsumed_evidence(evidence), failures)


def _consumer_use_has_ended(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return bool(
        _workspace_built_consumer_is_use_released(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_revoked(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_acknowledged(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_forgotten(evidence.built, evidence.consumer)
    )


__all__: list[str] = []

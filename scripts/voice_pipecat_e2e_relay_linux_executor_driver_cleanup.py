"""Failure settlement for the private full-lifecycle executor driver."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_LEASES,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _acquire_command_publication_gate,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _new_relay_linux_build_workspace_worker_bundle,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread import (
    _cancel_relay_linux_build_workspace_worker,
    _join_relay_linux_build_workspace_worker,
    _new_relay_linux_build_workspace_worker_thread,
    _release_relay_linux_build_workspace_worker,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_consume import (
    _reconcile_existing_consumption,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_cleanup import (
    _final_outer_absence,
    _retain_failure,
    _settle_relay_linux_executor_outer,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_driver_state import (
    _abandon_driver_attempt,
    _advance_driver_record,
    _driver_attempt_is_abandoned,
    _driver_record,
    _RelayLinuxExecutorDriverAttempt,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_contract import (
    _resolve_or_intend_inner_evidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
    _RelayLinuxExecutorInnerEvidence,
    _settle_inner_owner,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _EXECUTORS,
    _LOCK,
    _PORT_RESERVATIONS,
    _RETIRED_KEYS,
    _WORKSPACE_RELEASES,
    _release_unstarted_relay_linux_executor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_workspace import (
    _complete_relay_linux_executor_workspace_release,
    _intend_relay_linux_executor_workspace_release,
)
from scripts.voice_pipecat_e2e_relay_owner_settlement import (
    _relay_probe_destination_and_registry_are_empty,
)

_WAIT_SECONDS = 0.05
_CONSUME_RECONCILE_ATTEMPTS = 3


def _settle_failed_driver_attempt(
    attempt: _RelayLinuxExecutorDriverAttempt,
    failures: list[BaseException | None],
    *,
    cleanup_deadline: float,
) -> bool:
    """Classify the consume cut, then settle without creating an inner owner."""

    if type(cleanup_deadline) is not float or not math.isfinite(cleanup_deadline):
        return False
    if _driver_attempt_is_abandoned(attempt) and _driver_record(attempt) is None:
        return True
    if _final_outer_absence(attempt.executor, attempt.destination, attempt.key):
        return True
    record = _driver_record(attempt)
    if record is None:
        return _final_outer_absence(attempt.executor, attempt.destination, attempt.key)
    if record[6] == "intended" and _driver_preownership_has_no_effect(attempt):
        return _abandon_driver_attempt(attempt)
    if record[6] == "consume-intended":
        consume_outcome = _reconcile_consume_cut(
            attempt,
            failures,
            cleanup_deadline=cleanup_deadline,
        )
        record = _driver_record(attempt)
        if type(consume_outcome) is _RelayLinuxExecutorBuiltBinding and record is not None:
            record = _advance_consumed_driver_record(
                attempt,
                consume_outcome,
                failures,
            )
            if record is None:
                return False
        elif consume_outcome is not False:
            return False
    if record is not None and record[6] == "consumed":
        return _settle_consumed_without_inner_owner(
            attempt,
            record[5],
            cleanup_deadline=cleanup_deadline,
            failures=failures,
        )
    return _settle_preconsume_attempt(
        attempt,
        cleanup_deadline=cleanup_deadline,
        failures=failures,
    )


def _advance_consumed_driver_record(
    attempt: _RelayLinuxExecutorDriverAttempt,
    binding: _RelayLinuxExecutorBuiltBinding,
    failures: list[BaseException | None],
) -> tuple[object, ...] | None:
    for _attempt in range(_CONSUME_RECONCILE_ATTEMPTS):
        record = _driver_record(attempt)
        if record is None:
            return None
        if record[6] == "consumed" and record[5] is binding:
            return record
        if record[6] != "consume-intended":
            return None
        try:
            _advance_driver_record(
                attempt,
                expected_phase="consume-intended",
                phase="consumed",
                bundle=record[1],
                construction=record[2],
                prepared=record[3],
                built=record[4],
                binding=binding,
            )
        except BaseException as error:
            _retain_failure(failures, error)
    record = _driver_record(attempt)
    return (
        record if record is not None and record[6] == "consumed" and record[5] is binding else None
    )


def _reconcile_consume_cut(
    attempt: _RelayLinuxExecutorDriverAttempt,
    failures: list[BaseException | None],
    *,
    cleanup_deadline: float,
) -> _RelayLinuxExecutorBuiltBinding | bool | None:
    """Help only a prior consume effect; reject an exact no-effect intent."""

    for _attempt in range(_CONSUME_RECONCILE_ATTEMPTS):
        if time.monotonic() >= cleanup_deadline:
            return None
        record = _driver_record(attempt)
        if record is None or record[6] != "consume-intended" or record[4] is None:
            return None
        lease = _BUILT_LEASES.get(record[4])
        if (
            type(lease) is not tuple
            or len(lease) != 6
            or type(lease[2]) is not _WorkspaceBuildCommand
        ):
            return None
        publication_gate = None
        try:
            publication_gate = _acquire_command_publication_gate(
                lease[2],
                min(cleanup_deadline, time.monotonic() + _WAIT_SECONDS),
            )
            local_failures: list[BaseException | None] = [None, None]
            binding = _reconcile_existing_consumption(
                attempt.executor,
                attempt.destination,
                record[4],
                attempt.key,
                local_failures,
                reject_unconsumed=True,
            )
            if type(binding) is _RelayLinuxExecutorBuiltBinding:
                return binding
            if binding is None:
                return False
        except BaseException as error:
            _retain_failure(failures, error)
        finally:
            if publication_gate is not None:
                publication_gate.publication_lock.release()
    return None


def _settle_consumed_without_inner_owner(
    attempt: _RelayLinuxExecutorDriverAttempt,
    binding: object,
    *,
    cleanup_deadline: float,
    failures: list[BaseException | None],
) -> bool:
    """Publish an exact failed inner terminal without invoking its factory."""

    if type(binding) is not _RelayLinuxExecutorBuiltBinding:
        return False
    evidence: _RelayLinuxExecutorInnerEvidence | None = None
    while time.monotonic() < cleanup_deadline:
        try:
            evidence = _resolve_or_intend_inner_evidence(
                executor=attempt.executor,
                destination=attempt.destination,
                binding=binding,
                runner=attempt.runner,
                bridge_probe=attempt.bridge_probe,
                tools=attempt.tools,
                invocation_selection=attempt.invocation_selection,
                static_auth_secret=attempt.static_auth_secret,
                now=attempt.now,
                browser_timeout_seconds=attempt.browser_timeout_seconds,
                runtime_timeout_seconds=attempt.runtime_timeout_seconds,
                cleanup_timeout_seconds=attempt.cleanup_timeout_seconds,
                clock=attempt.clock,
                wait=attempt.wait,
                epoch_clock=attempt.epoch_clock,
            )
            if not _relay_probe_destination_and_registry_are_empty(
                attempt.executor._relay_owner_destination
            ):
                return False
            if _settle_inner_owner(evidence, None, None):
                break
        except BaseException as error:
            _retain_failure(failures, error)
        _wait_safely(attempt, cleanup_deadline, failures)
    if type(evidence) is not _RelayLinuxExecutorInnerEvidence:
        return False
    return _settle_relay_linux_executor_outer(
        evidence.build,
        binding,
        inner_evidence=evidence,
        cleanup_deadline=cleanup_deadline,
        failures=failures,
    )


def _settle_preconsume_attempt(
    attempt: _RelayLinuxExecutorDriverAttempt,
    *,
    cleanup_deadline: float,
    failures: list[BaseException | None],
) -> bool:
    """Clear a bound pin, cancel/release its worker, then retire outer state."""

    while time.monotonic() < cleanup_deadline:
        try:
            if _final_outer_absence(attempt.executor, attempt.destination, attempt.key):
                return True
            record = _driver_record(attempt)
            bundle = record[1] if record is not None else None
            construction = record[2] if record is not None else None
            if (
                record is not None
                and record[6] == "outer-preowned"
                and _release_unstarted_relay_linux_executor(attempt.executor._cleanup_authority)
                and _final_outer_absence(attempt.executor, attempt.destination, attempt.key)
            ):
                return True
            if record is not None and record[6] != "intended" and bundle is None:
                bundle = _new_relay_linux_build_workspace_worker_bundle(
                    attempt.executor._workspace_owner
                )
            if bundle is not None and construction is None:
                construction, _coherent = _new_relay_linux_build_workspace_worker_thread(
                    attempt.executor._workspace_owner,
                    bundle,
                )
            graph = _intend_relay_linux_executor_workspace_release(
                attempt.executor._cleanup_authority
            )
            if graph is not None:
                workspace_owner, graph_bundle, graph_construction = graph
                if bundle is not None and graph_bundle is not bundle:
                    return False
                if construction is not None and graph_construction is not construction:
                    return False
                bundle = graph_bundle
                construction = graph_construction
            else:
                workspace_owner = attempt.executor._workspace_owner
            if bundle is not None and construction is not None:
                _cancel_relay_linux_build_workspace_worker(
                    workspace_owner,
                    bundle,
                    construction,
                )
                terminal, joined = _join_relay_linux_build_workspace_worker(
                    workspace_owner,
                    bundle,
                    construction,
                    min(_WAIT_SECONDS, max(0.0, cleanup_deadline - time.monotonic())),
                )
                if not joined or terminal is None:
                    _wait_safely(attempt, cleanup_deadline, failures)
                    continue
                if not _release_relay_linux_build_workspace_worker(
                    workspace_owner,
                    bundle,
                    construction,
                    terminal,
                ):
                    _wait_safely(attempt, cleanup_deadline, failures)
                    continue
                _complete_relay_linux_executor_workspace_release(
                    attempt.executor._cleanup_authority
                )
            if _release_unstarted_relay_linux_executor(
                attempt.executor._cleanup_authority
            ) and _final_outer_absence(attempt.executor, attempt.destination, attempt.key):
                return True
        except BaseException as error:
            _retain_failure(failures, error)
        _wait_safely(attempt, cleanup_deadline, failures)
    try:
        return _final_outer_absence(attempt.executor, attempt.destination, attempt.key)
    except BaseException as error:
        _retain_failure(failures, error)
        return False


def _wait_safely(
    attempt: _RelayLinuxExecutorDriverAttempt,
    cleanup_deadline: float,
    failures: list[BaseException | None],
) -> None:
    remaining = max(0.0, cleanup_deadline - time.monotonic())
    if remaining <= 0.0:
        return
    try:
        record = _driver_record(attempt)
        if record is not None and record[1] is not None:
            record[1]._controller._wait(min(_WAIT_SECONDS, remaining))
        else:
            time.sleep(min(_WAIT_SECONDS, remaining))
    except BaseException as error:
        _retain_failure(failures, error)


def _driver_preownership_has_no_effect(
    attempt: _RelayLinuxExecutorDriverAttempt,
) -> bool:
    """Prove this key owns no outer state without constraining a foreign owner."""

    with _LOCK:
        return bool(
            attempt.key not in _EXECUTORS
            and attempt.key not in _RETIRED_KEYS
            and attempt.key not in _WORKSPACE_RELEASES
            and all(candidate is not attempt.key for candidate in _PORT_RESERVATIONS.values())
        )


__all__: list[str] = []

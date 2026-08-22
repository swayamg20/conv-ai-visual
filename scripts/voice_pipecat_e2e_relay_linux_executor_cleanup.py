"""Process-first teardown for one settled inner relay executor."""

from __future__ import annotations

import math
import os
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _workspace_built_consumer_all_state_is_empty,
    _workspace_built_consumer_is_revoked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_forget import (
    _workspace_build_graph_is_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer import (
    _workspace_worker_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread import (
    _join_relay_linux_build_workspace_worker,
    _release_relay_linux_build_workspace_worker,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_consume import (
    _acknowledge_relay_linux_executor_built_revoked,
    _executor_consumed_build_allows_workspace_release,
    _release_relay_linux_executor_built_use,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _executor_built_registries_are_empty,
    _outer_phase_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
    _inner_live_evidence_is_absent,
    _inner_result,
    _RelayLinuxExecutorInnerEvidence,
    _retire_settled_inner,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _EXECUTORS,
    _LOCK,
    _PORT_RESERVATIONS,
    _RETIRED_KEYS,
    _WORKSPACE_RELEASES,
    _executor_record_matches,
    _executor_value_matches,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
    _release_unstarted_relay_linux_executor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_workspace import (
    _complete_relay_linux_executor_workspace_release,
    _intend_relay_linux_executor_workspace_release,
)
from scripts.voice_pipecat_e2e_relay_owner_settlement import (
    _relay_probe_destination_and_registry_are_empty,
)

_WAIT_SLICE_SECONDS = 0.05


def _settle_relay_linux_executor_outer(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    binding: _RelayLinuxExecutorBuiltBinding,
    *,
    inner_evidence: _RelayLinuxExecutorInnerEvidence | None,
    cleanup_deadline: float,
    failures: list[BaseException | None],
) -> bool:
    """Release inner use, worker/build/root state, fixed ports, then outer state."""

    if (
        type(evidence) is not _RelayLinuxExecutorBuiltEvidence
        or evidence.binding is not binding
        or type(cleanup_deadline) is not float
        or not math.isfinite(cleanup_deadline)
        or type(failures) is not list
        or len(failures) != 2
    ):
        return False
    while time.monotonic() < cleanup_deadline:
        try:
            if _outer_is_absent(evidence):
                return True
            if not _inner_live_evidence_is_absent(evidence.key):
                if (
                    type(inner_evidence) is not _RelayLinuxExecutorInnerEvidence
                    or inner_evidence.build is not evidence
                    or not _release_relay_linux_executor_built_use(
                        binding,
                        cleanup_deadline=_slice_deadline(cleanup_deadline),
                    )
                    or not _retire_settled_inner(inner_evidence)
                ):
                    _wait(evidence, cleanup_deadline)
                    continue
            elif not (
                _inner_result(evidence.key) is not None
                and _outer_cleanup_phase(evidence) is not None
            ):
                _wait(evidence, cleanup_deadline)
                continue
            phase = _outer_cleanup_phase(evidence)
            if phase is None:
                _wait(evidence, cleanup_deadline)
                continue
            evidence.bundle._controller._request_cancel()
            if phase == "use-release-intended":
                if not _workspace_built_consumer_is_revoked(
                    evidence.built,
                    evidence.consumer,
                ):
                    _wait(evidence, cleanup_deadline)
                    continue
                if not _acknowledge_relay_linux_executor_built_revoked(
                    binding,
                    cleanup_deadline=_slice_deadline(cleanup_deadline),
                ):
                    _wait(evidence, cleanup_deadline)
                    continue
                phase = _outer_cleanup_phase(evidence)
            if phase == "build-revoked-acknowledged" and not (
                _executor_consumed_build_allows_workspace_release(binding)
            ):
                _wait(evidence, cleanup_deadline)
                continue
            graph = _intend_relay_linux_executor_workspace_release(
                evidence.authority,
            )
            if graph is None:
                if _complete_and_release_outer(evidence):
                    return _outer_is_absent(evidence)
                _wait(evidence, cleanup_deadline)
                continue
            workspace_owner, bundle, construction = graph
            terminal, joined = _join_relay_linux_build_workspace_worker(
                workspace_owner,
                bundle,
                construction,
                min(_WAIT_SLICE_SECONDS, max(0.0, cleanup_deadline - time.monotonic())),
            )
            if not joined or terminal is None:
                _wait(evidence, cleanup_deadline)
                continue
            if not _release_relay_linux_build_workspace_worker(
                workspace_owner,
                bundle,
                construction,
                terminal,
            ):
                _wait(evidence, cleanup_deadline)
                continue
            if _complete_and_release_outer(evidence):
                return _outer_is_absent(evidence)
        except (KeyboardInterrupt, SystemExit) as error:
            _retain_failure(failures, error)
        except BaseException as error:
            _retain_failure(failures, error)
        _wait_safely(evidence, cleanup_deadline, failures)
    try:
        return _outer_is_absent(evidence)
    except BaseException as error:
        _retain_failure(failures, error)
        return False


def _complete_and_release_outer(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return bool(
        _complete_relay_linux_executor_workspace_release(evidence.authority)
        and _release_unstarted_relay_linux_executor(evidence.authority)
    )


def _outer_cleanup_phase(evidence: _RelayLinuxExecutorBuiltEvidence) -> str | None:
    for phase in ("use-release-intended", "build-revoked-acknowledged"):
        if _outer_phase_matches(evidence, phase):
            return phase
    with _LOCK:
        record = _EXECUTORS.get(evidence.key)
        if not (
            type(record) is tuple
            and len(record) == 6
            and record[0] is evidence.executor
            and record[1] is evidence.destination
            and record[2] is evidence.bundle
            and record[3] is evidence.construction
            and type(record[5]) is str
            and record[5] in {"workspace-releasing", "workspace-released"}
            and _executor_record_matches(
                record,
                evidence.executor,
                evidence.destination,
                record[5],
            )
            and (
                (record[5] == "workspace-releasing" and record[4] is None)
                or (record[5] == "workspace-released" and type(record[4]) is object)
            )
        ):
            return None
        return record[5]


def _outer_is_absent(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return _final_outer_absence(evidence.executor, evidence.destination, evidence.key)


def _final_outer_absence(
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
    try:
        value_matches = _executor_value_matches(executor, destination)
        owner_destination = executor._relay_owner_destination
        run_root = executor._workspace_owner._request._run_root
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return bool(
        not _EXECUTORS
        and not _PORT_RESERVATIONS
        and not _WORKSPACE_RELEASES
        and key in _RETIRED_KEYS
        and value_matches
        and _inner_live_evidence_is_absent(key)
        and _relay_probe_destination_and_registry_are_empty(owner_destination)
        and _executor_built_registries_are_empty()
        and _workspace_worker_registries_are_empty(time.monotonic() + _WAIT_SLICE_SECONDS) is True
        and _workspace_build_graph_is_empty()
        and _workspace_built_consumer_all_state_is_empty()
        and _build_process_registries_are_empty()
        and not os.path.lexists(run_root)
    )


def _slice_deadline(cleanup_deadline: float) -> float:
    return min(cleanup_deadline, time.monotonic() + _WAIT_SLICE_SECONDS)


def _wait(evidence: _RelayLinuxExecutorBuiltEvidence, cleanup_deadline: float) -> None:
    remaining = max(0.0, cleanup_deadline - time.monotonic())
    if remaining <= 0.0:
        return
    evidence.bundle._controller._wait(min(_WAIT_SLICE_SECONDS, remaining))


def _wait_safely(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    cleanup_deadline: float,
    failures: list[BaseException | None],
) -> None:
    try:
        _wait(evidence, cleanup_deadline)
    except BaseException as error:
        _retain_failure(failures, error)


def _retain_failure(
    failures: list[BaseException | None],
    error: BaseException,
) -> None:
    slot = 0 if isinstance(error, (KeyboardInterrupt, SystemExit)) else 1
    if failures[slot] is None:
        failures[slot] = error


__all__: list[str] = []

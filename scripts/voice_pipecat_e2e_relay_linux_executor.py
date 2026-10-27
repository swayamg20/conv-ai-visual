"""Private synthetic outer composition above one consumed build lease."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import TrustedHostTools
from scripts.voice_pipecat_e2e_relay_invocation import RelayInvocationDriver
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _RelayConcreteInvocationSelection,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _evidence_for_binding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_cleanup import (
    _final_outer_absence,
    _retain_failure,
    _settle_relay_linux_executor_outer,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_contract import (
    _resolve_or_intend_inner_evidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
    _inner_evidence_authority_matches,
    _inner_record,
    _inner_replay_inputs_match,
    _inner_result,
    _RelayLinuxExecutorInnerEvidence,
    _retain_inner_owner,
    _settle_inner_owner,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _canonical_executor_key,
    _executor_value_matches,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)
from scripts.voice_pipecat_e2e_relay_owner import (
    cleanup_relay_probe,
    new_relay_probe_owner,
    run_relay_probe,
)
from scripts.voice_pipecat_e2e_relay_owner_settlement import (
    _relay_probe_destination_and_registry_are_empty,
    _relay_probe_owner_settlement_matches,
)
from scripts.voice_pipecat_e2e_relay_owner_state import RelayProbeOwner
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation

_FAILURE = "Relay Linux executor run failed"
_OWNER_ATTEMPTS = 3


def _run_consumed_relay_linux_executor(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    binding: _RelayLinuxExecutorBuiltBinding,
    runner: object,
    bridge_probe: object,
    tools: TrustedHostTools,
    invocation_selection: RelayInvocationDriver | _RelayConcreteInvocationSelection,
    static_auth_secret: object,
    now: datetime,
    browser_timeout_seconds: float,
    runtime_timeout_seconds: float,
    cleanup_timeout_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
    epoch_clock: Callable[[], float] = time.time,
) -> RelayProbeObservation:
    """Run the exact inner owner, then withhold its result through full teardown."""

    key = _canonical_executor_key(executor, destination)
    if type(key) is not _RelayLinuxExecutorKey or not _executor_value_matches(
        executor,
        destination,
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    if (
        type(binding) is not _RelayLinuxExecutorBuiltBinding
        or type(now) is not datetime
        or type(browser_timeout_seconds) is not float
        or not math.isfinite(browser_timeout_seconds)
        or browser_timeout_seconds <= 0.0
        or type(runtime_timeout_seconds) is not float
        or not math.isfinite(runtime_timeout_seconds)
        or runtime_timeout_seconds <= 0.0
        or type(cleanup_timeout_seconds) is not float
        or not math.isfinite(cleanup_timeout_seconds)
        or cleanup_timeout_seconds <= 0.0
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    terminal = _terminal_result_if_outer_absent(
        executor,
        destination,
        key,
        binding=binding,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_selection=invocation_selection,
        static_auth_secret=static_auth_secret,
        now=now,
        browser_timeout_seconds=browser_timeout_seconds,
        runtime_timeout_seconds=runtime_timeout_seconds,
        clock=clock,
        wait=wait,
        epoch_clock=epoch_clock,
    )
    if terminal is not None:
        if type(terminal) is RelayProbeObservation:
            return terminal
        raise _RelayLinuxExecutorError(_FAILURE)
    failures: list[BaseException | None] = [None, None]
    existing_result = _inner_result(key)
    if existing_result is not None:
        if not _inner_replay_inputs_match(
            key,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
            require_terminal=False,
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        build = _evidence_for_binding(binding)
        if type(build) is not _RelayLinuxExecutorBuiltEvidence:
            raise _RelayLinuxExecutorError(_FAILURE)
        retained_record = _inner_record(key)
        retained_inner = (
            retained_record[0]
            if type(retained_record) is tuple
            and len(retained_record) == 3
            and type(retained_record[0]) is _RelayLinuxExecutorInnerEvidence
            and retained_record[0].build is build
            and retained_record[2] == "inner-settled"
            else None
        )
        settled = _settle_relay_linux_executor_outer(
            build,
            binding,
            inner_evidence=retained_inner,
            cleanup_deadline=time.monotonic() + cleanup_timeout_seconds,
            failures=failures,
        )
        if not settled:
            _raise_retained(failures)
            raise _RelayLinuxExecutorError(_FAILURE)
        _raise_retained(failures)
        if (
            type(existing_result) is tuple
            and len(existing_result) == 2
            and type(existing_result[0]) is RelayProbeObservation
            and existing_result[1] == "observed"
        ):
            return existing_result[0]
        raise _RelayLinuxExecutorError(_FAILURE)
    evidence: _RelayLinuxExecutorInnerEvidence | None = None
    observation: RelayProbeObservation | None = None
    try:
        evidence = _resolve_or_intend_inner_evidence(
            executor=executor,
            destination=destination,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )
        owner = _resolve_or_construct_inner_owner(evidence, failures)
        if type(owner) is RelayProbeOwner:
            observation = _run_or_cleanup_inner_owner(
                evidence,
                owner,
                failures=failures,
            )
        elif not _settle_inner_owner(evidence, None, None):
            raise _RelayLinuxExecutorError(_FAILURE)
    except BaseException as error:
        _retain_failure(failures, error)
    if evidence is None:
        _raise_retained(failures)
        raise _RelayLinuxExecutorError(_FAILURE)
    cleanup_deadline = time.monotonic() + cleanup_timeout_seconds
    settled = _settle_relay_linux_executor_outer(
        evidence.build,
        binding,
        inner_evidence=evidence,
        cleanup_deadline=cleanup_deadline,
        failures=failures,
    )
    result = _inner_result(evidence.key)
    evidence = None
    static_auth_secret = now = runner = bridge_probe = tools = invocation_selection = None
    if not settled:
        _raise_retained(failures)
        raise _RelayLinuxExecutorError(_FAILURE)
    _raise_retained(failures)
    if (
        type(result) is tuple
        and len(result) == 2
        and type(result[0]) is RelayProbeObservation
        and result[1] == "observed"
        and result[0] is observation
    ):
        return result[0]
    raise _RelayLinuxExecutorError(_FAILURE)


def _resolve_or_construct_inner_owner(
    evidence: _RelayLinuxExecutorInnerEvidence,
    failures: list[BaseException | None],
) -> RelayProbeOwner | None:
    if not _inner_evidence_authority_matches(evidence):
        return None
    record = _inner_record(evidence.key)
    if (
        type(record) is tuple
        and len(record) == 3
        and record[0] is evidence
        and type(record[1]) is RelayProbeOwner
        and record[2] in {"inner-owned", "inner-settled"}
    ):
        return record[1]
    if not (
        type(record) is tuple
        and len(record) == 3
        and record[0] is evidence
        and record[1] is None
        and record[2] == "inner-intended"
    ):
        return None
    owner: RelayProbeOwner | None = None
    try:
        owner = new_relay_probe_owner(
            destination=evidence.owner_destination,
            paths=evidence.paths,
            identity=evidence.identity,
            source=evidence.build.source,
            runner=evidence.runner,
            bridge_probe=evidence.bridge_probe,
            tools=evidence.tools,
            invocation_driver=evidence.effective_invocation_driver,
            invocation_tools=evidence.effective_invocation_tools,
            absolute_deadline=evidence.runtime_deadline,
            clock=evidence.clock,
            wait=evidence.wait,
        )
    except BaseException as error:
        _retain_failure(failures, error)
    try:
        recovered = evidence.owner_destination._read(evidence.owner_binding)
        if type(recovered) is RelayProbeOwner:
            if owner is not None and recovered is not owner:
                return None
            owner = recovered
    except BaseException as error:
        _retain_failure(failures, error)
    if type(owner) is RelayProbeOwner and _retain_inner_owner(evidence, owner):
        return owner
    if owner is None and _relay_probe_destination_and_registry_are_empty(
        evidence.owner_destination
    ):
        return None
    return None


def _run_or_cleanup_inner_owner(
    evidence: _RelayLinuxExecutorInnerEvidence,
    owner: RelayProbeOwner,
    *,
    failures: list[BaseException | None],
) -> RelayProbeObservation | None:
    if not _inner_evidence_authority_matches(evidence):
        return None
    observation: RelayProbeObservation | None = None
    for _attempt in range(_OWNER_ATTEMPTS):
        try:
            candidate = run_relay_probe(
                owner,
                static_auth_secret=evidence.static_auth_secret,
                now=evidence.now,
                browser_timeout_seconds=evidence.browser_timeout_seconds,
            )
            if type(candidate) is RelayProbeObservation:
                observation = candidate
                break
        except BaseException as error:
            _retain_failure(failures, error)
        if _relay_probe_owner_settlement_matches(
            owner,
            evidence.owner_destination,
            observation,
        ):
            break
    if observation is None:
        for _attempt in range(_OWNER_ATTEMPTS):
            if _relay_probe_owner_settlement_matches(
                owner,
                evidence.owner_destination,
                None,
            ):
                break
            try:
                cleanup_relay_probe(owner)
            except BaseException as error:
                _retain_failure(failures, error)
    if not _relay_probe_owner_settlement_matches(
        owner,
        evidence.owner_destination,
        observation,
    ):
        return None
    if not _settle_inner_owner(evidence, owner, observation):
        return None
    return observation


def _terminal_result_if_outer_absent(
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    key: _RelayLinuxExecutorKey,
    *,
    binding: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_selection: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
) -> RelayProbeObservation | bool | None:
    result = _inner_result(key)
    if (
        result is None
        or not _inner_replay_inputs_match(
            key,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
            require_terminal=True,
        )
        or not _final_outer_absence(executor, destination, key)
    ):
        return None
    if (
        type(result) is tuple
        and len(result) == 2
        and result[1] == "observed"
        and type(result[0]) is RelayProbeObservation
    ):
        return result[0]
    if result == (None, "failed"):
        return False
    return None


def _raise_retained(failures: list[BaseException | None]) -> None:
    control, ordinary = failures
    if isinstance(control, (KeyboardInterrupt, SystemExit)):
        raise control
    if ordinary is not None:
        raise _RelayLinuxExecutorError(_FAILURE) from None


__all__: list[str] = []

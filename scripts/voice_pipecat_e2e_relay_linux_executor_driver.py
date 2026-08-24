"""Private end-to-end driver for one caller-preowned relay Linux executor.

This boundary owns the dormant workspace worker, build handoff, consumed lease,
inner relay aggregate, and process-first teardown.  It remains synthetic and is
not connected to the public voice workflow.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import TrustedHostTools
from scripts.voice_pipecat_e2e_relay_invocation import RelayInvocationDriver
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_facade import (
    _build_relay_linux_workspace,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _WorkspacePreparedReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _new_relay_linux_build_workspace_worker_bundle,
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread import (
    _new_relay_linux_build_workspace_worker_thread,
    _start_relay_linux_build_workspace_worker,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerStartReceipt,
    _WorkspaceWorkerTerminalReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_executor import (
    _run_consumed_relay_linux_executor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_consume import (
    _consume_relay_linux_executor_built_lease,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_cleanup import (
    _final_outer_absence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_driver_cleanup import (
    _settle_failed_driver_attempt,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_driver_state import (
    _advance_driver_record,
    _driver_attempt_is_abandoned,
    _driver_attempt_is_retired,
    _driver_cleanup_is_latched,
    _driver_record,
    _latch_driver_cleanup,
    _publish_driver_terminal,
    _recover_driver_attempt_for_call,
    _RelayLinuxExecutorDriverAttempt,
    _resolve_or_intend_driver_attempt,
    _retire_driver_attempt,
    _terminal_binding_for_driver_call,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _preown_relay_linux_executor,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_workspace import (
    _bind_relay_linux_executor_workspace,
)
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation

_FAILURE = "Relay Linux executor driver failed"
_WAIT_SECONDS = 0.05


def _run_preowned_relay_linux_executor(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    runner: object,
    bridge_probe: object,
    tools: TrustedHostTools,
    invocation_driver: RelayInvocationDriver,
    static_auth_secret: object,
    now: datetime,
    start_timeout_seconds: float,
    build_timeout_seconds: float,
    browser_timeout_seconds: float,
    runtime_timeout_seconds: float,
    cleanup_timeout_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
    epoch_clock: Callable[[], float] = time.time,
) -> RelayProbeObservation:
    """Drive one exact private lifecycle and withhold its result through teardown."""

    failure: tuple[object, object] | None = None
    try:
        return _run_preowned_relay_linux_executor_inner(
            executor=executor,
            destination=destination,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_driver=invocation_driver,
            static_auth_secret=static_auth_secret,
            now=now,
            start_timeout_seconds=start_timeout_seconds,
            build_timeout_seconds=build_timeout_seconds,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )
    except BaseException as error:
        failure = _sanitized_driver_failure(error)
        _scrub_control_minimal(error)
        del (
            executor,
            destination,
            runner,
            bridge_probe,
            tools,
            invocation_driver,
            static_auth_secret,
            now,
            start_timeout_seconds,
            build_timeout_seconds,
            browser_timeout_seconds,
            runtime_timeout_seconds,
            cleanup_timeout_seconds,
            clock,
            wait,
            epoch_clock,
        )
    if failure is not None:
        _raise_sanitized_driver_failure(failure)
    raise _RelayLinuxExecutorError(_FAILURE)


def _sanitized_driver_failure(error: BaseException) -> tuple[object, object]:
    if isinstance(error, KeyboardInterrupt):
        return (KeyboardInterrupt, None)
    if isinstance(error, SystemExit):
        code = SystemExit.__dict__["code"].__get__(error, SystemExit)
        return (SystemExit, code if code is None or type(code) is int else 1)
    return (None, None)


def _raise_sanitized_driver_failure(failure: tuple[object, object]) -> None:
    if (
        type(failure) is tuple
        and len(failure) == 2
        and failure[0] is KeyboardInterrupt
        and failure[1] is None
    ):
        raise KeyboardInterrupt from None
    if (
        type(failure) is tuple
        and len(failure) == 2
        and failure[0] is SystemExit
        and (failure[1] is None or type(failure[1]) is int)
    ):
        raise SystemExit(failure[1]) from None
    raise _RelayLinuxExecutorError(_FAILURE) from None


def _run_preowned_relay_linux_executor_inner(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    runner: object,
    bridge_probe: object,
    tools: TrustedHostTools,
    invocation_driver: RelayInvocationDriver,
    static_auth_secret: object,
    now: datetime,
    start_timeout_seconds: float,
    build_timeout_seconds: float,
    browser_timeout_seconds: float,
    runtime_timeout_seconds: float,
    cleanup_timeout_seconds: float,
    clock: Callable[[], float],
    wait: Callable[[float], None],
    epoch_clock: Callable[[], float],
) -> RelayProbeObservation:

    terminal_binding = _terminal_binding_for_driver_call(
        executor=executor,
        destination=destination,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_driver=invocation_driver,
        static_auth_secret=static_auth_secret,
        now=now,
        browser_timeout_seconds=browser_timeout_seconds,
        runtime_timeout_seconds=runtime_timeout_seconds,
        start_timeout_seconds=start_timeout_seconds,
        build_timeout_seconds=build_timeout_seconds,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        clock=clock,
        wait=wait,
        epoch_clock=epoch_clock,
    )
    if type(terminal_binding) is _RelayLinuxExecutorBuiltBinding:
        return _run_consumed_relay_linux_executor(
            executor=executor,
            destination=destination,
            binding=terminal_binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_driver=invocation_driver,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )
    attempt_arguments = {
        "executor": executor,
        "destination": destination,
        "runner": runner,
        "bridge_probe": bridge_probe,
        "tools": tools,
        "invocation_driver": invocation_driver,
        "static_auth_secret": static_auth_secret,
        "now": now,
        "start_timeout_seconds": start_timeout_seconds,
        "build_timeout_seconds": build_timeout_seconds,
        "browser_timeout_seconds": browser_timeout_seconds,
        "runtime_timeout_seconds": runtime_timeout_seconds,
        "cleanup_timeout_seconds": cleanup_timeout_seconds,
        "clock": clock,
        "wait": wait,
        "epoch_clock": epoch_clock,
    }
    try:
        attempt = _resolve_or_intend_driver_attempt(**attempt_arguments)
    except BaseException as error:
        recovered = _recover_driver_attempt_for_call(**attempt_arguments)
        if type(recovered) is _RelayLinuxExecutorDriverAttempt:
            with recovered.operation_lock:
                _latch_driver_cleanup(recovered, error)
                _settle_finalize_and_raise(recovered)
        terminal_binding = _terminal_binding_for_driver_call(
            executor=executor,
            destination=destination,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_driver=invocation_driver,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            start_timeout_seconds=start_timeout_seconds,
            build_timeout_seconds=build_timeout_seconds,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )
        if type(terminal_binding) is _RelayLinuxExecutorBuiltBinding:
            return _run_consumed_relay_linux_executor(
                executor=executor,
                destination=destination,
                binding=terminal_binding,
                runner=runner,
                bridge_probe=bridge_probe,
                tools=tools,
                invocation_driver=invocation_driver,
                static_auth_secret=static_auth_secret,
                now=now,
                browser_timeout_seconds=browser_timeout_seconds,
                runtime_timeout_seconds=runtime_timeout_seconds,
                cleanup_timeout_seconds=cleanup_timeout_seconds,
                clock=clock,
                wait=wait,
                epoch_clock=epoch_clock,
            )
        raise error
    with attempt.operation_lock:
        if _driver_cleanup_is_latched(attempt):
            _settle_finalize_and_raise(attempt)
        historical_observation = _historical_driver_terminal_observation(attempt)
        if type(historical_observation) is RelayProbeObservation:
            return historical_observation
        terminal_binding = _terminal_binding_for_attempt(attempt)
        if type(terminal_binding) is _RelayLinuxExecutorBuiltBinding:
            return _run_consumed_relay_linux_executor(
                executor=attempt.executor,
                destination=attempt.destination,
                binding=terminal_binding,
                runner=attempt.runner,
                bridge_probe=attempt.bridge_probe,
                tools=attempt.tools,
                invocation_driver=attempt.invocation_driver,
                static_auth_secret=attempt.static_auth_secret,
                now=attempt.now,
                browser_timeout_seconds=attempt.browser_timeout_seconds,
                runtime_timeout_seconds=attempt.runtime_timeout_seconds,
                cleanup_timeout_seconds=attempt.cleanup_timeout_seconds,
                clock=attempt.clock,
                wait=attempt.wait,
                epoch_clock=attempt.epoch_clock,
            )
        try:
            observation = _drive_attempt(attempt)
            if not (
                type(observation) is RelayProbeObservation
                and _final_outer_absence(executor, destination, attempt.key)
                and _bind_driver_terminal_observation(attempt, observation)
                and _publish_driver_terminal(attempt)
                and _complete_driver_terminal_observation(attempt, observation)
                and _retire_driver_attempt(attempt)
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            return observation
        except BaseException as error:
            _latch_driver_cleanup(attempt, error)
            _settle_finalize_and_raise(attempt)
    raise _RelayLinuxExecutorError(_FAILURE)


def _terminal_binding_for_attempt(
    attempt: _RelayLinuxExecutorDriverAttempt,
) -> _RelayLinuxExecutorBuiltBinding | None:
    return _terminal_binding_for_driver_call(
        executor=attempt.executor,
        destination=attempt.destination,
        runner=attempt.runner,
        bridge_probe=attempt.bridge_probe,
        tools=attempt.tools,
        invocation_driver=attempt.invocation_driver,
        static_auth_secret=attempt.static_auth_secret,
        now=attempt.now,
        browser_timeout_seconds=attempt.browser_timeout_seconds,
        runtime_timeout_seconds=attempt.runtime_timeout_seconds,
        start_timeout_seconds=attempt.start_timeout_seconds,
        build_timeout_seconds=attempt.build_timeout_seconds,
        cleanup_timeout_seconds=attempt.cleanup_timeout_seconds,
        clock=attempt.clock,
        wait=attempt.wait,
        epoch_clock=attempt.epoch_clock,
    )


def _bind_driver_terminal_observation(
    attempt: _RelayLinuxExecutorDriverAttempt,
    observation: RelayProbeObservation,
) -> bool:
    return attempt._bind_terminal_observation(observation)


def _complete_driver_terminal_observation(
    attempt: _RelayLinuxExecutorDriverAttempt,
    observation: RelayProbeObservation,
) -> bool:
    return attempt._complete_terminal_observation(observation)


def _historical_driver_terminal_observation(
    attempt: _RelayLinuxExecutorDriverAttempt,
) -> RelayProbeObservation | None:
    return attempt._terminal_observation()


def _drive_attempt(
    attempt: _RelayLinuxExecutorDriverAttempt,
) -> RelayProbeObservation:
    while True:
        record = _driver_record(attempt)
        if record is None:
            raise _RelayLinuxExecutorError(_FAILURE)
        bundle, construction, prepared, built, binding, phase = record[1:]
        owner = attempt.executor._workspace_owner
        if phase == "intended":
            if _preown_relay_linux_executor(attempt.destination) is not attempt.executor:
                raise _RelayLinuxExecutorError(_FAILURE)
            if not _advance_driver_record(
                attempt,
                expected_phase="intended",
                phase="outer-preowned",
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "outer-preowned":
            bundle = _new_relay_linux_build_workspace_worker_bundle(owner)
            construction, coherent = _new_relay_linux_build_workspace_worker_thread(
                owner,
                bundle,
            )
            if type(construction) is _WorkspaceWorkerThreadReceipt:
                if not _advance_driver_record(
                    attempt,
                    expected_phase="outer-preowned",
                    phase="worker-created",
                    bundle=bundle,
                    construction=construction,
                ):
                    raise _RelayLinuxExecutorError(_FAILURE)
            if type(construction) is not _WorkspaceWorkerThreadReceipt or coherent is not True:
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "worker-created":
            if not _bind_relay_linux_executor_workspace(
                attempt.executor,
                _require_bundle(bundle),
                _require_construction(construction),
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            if not _advance_driver_record(
                attempt,
                expected_phase="worker-created",
                phase="workspace-bound",
                bundle=bundle,
                construction=construction,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "workspace-bound":
            start, coherent = _start_relay_linux_build_workspace_worker(
                owner,
                _require_bundle(bundle),
                _require_construction(construction),
                attempt.start_deadline,
            )
            owner_token = owner._cleanup_authority._key
            record_token = _require_construction(construction)._record_token
            if not (
                type(start) is _WorkspaceWorkerStartReceipt
                and start._matches(owner_token, record_token)
                and coherent is True
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            if not _advance_driver_record(
                attempt,
                expected_phase="workspace-bound",
                phase="worker-started",
                bundle=bundle,
                construction=construction,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "worker-started":
            prepared = _await_prepared_receipt(
                attempt,
                _require_bundle(bundle),
                _require_construction(construction),
            )
            if not _advance_driver_record(
                attempt,
                expected_phase="worker-started",
                phase="prepared",
                bundle=bundle,
                construction=construction,
                prepared=prepared,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "prepared":
            built, handed_off = _build_relay_linux_workspace(
                owner,
                _require_bundle(bundle),
                _require_construction(construction),
                _require_prepared(prepared),
                build_deadline=attempt.build_deadline,
            )
            if type(built) is not _WorkspaceBuiltReceipt or handed_off is not True:
                raise _RelayLinuxExecutorError(_FAILURE)
            if not _advance_driver_record(
                attempt,
                expected_phase="prepared",
                phase="built",
                bundle=bundle,
                construction=construction,
                prepared=prepared,
                built=built,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "built":
            if not _advance_driver_record(
                attempt,
                expected_phase="built",
                phase="consume-intended",
                bundle=bundle,
                construction=construction,
                prepared=prepared,
                built=built,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "consume-intended":
            binding = _consume_relay_linux_executor_built_lease(
                executor=attempt.executor,
                destination=attempt.destination,
                built=_require_built(built),
                operation_deadline=attempt.build_deadline,
            )
            if type(binding) is not _RelayLinuxExecutorBuiltBinding:
                raise _RelayLinuxExecutorError(_FAILURE)
            if not _advance_driver_record(
                attempt,
                expected_phase="consume-intended",
                phase="consumed",
                bundle=bundle,
                construction=construction,
                prepared=prepared,
                built=built,
                binding=binding,
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            continue
        if phase == "consumed":
            return _run_consumed_relay_linux_executor(
                executor=attempt.executor,
                destination=attempt.destination,
                binding=_require_binding(binding),
                runner=attempt.runner,
                bridge_probe=attempt.bridge_probe,
                tools=attempt.tools,
                invocation_driver=attempt.invocation_driver,
                static_auth_secret=attempt.static_auth_secret,
                now=attempt.now,
                browser_timeout_seconds=attempt.browser_timeout_seconds,
                runtime_timeout_seconds=attempt.runtime_timeout_seconds,
                cleanup_timeout_seconds=attempt.cleanup_timeout_seconds,
                clock=attempt.clock,
                wait=attempt.wait,
                epoch_clock=attempt.epoch_clock,
            )
        raise _RelayLinuxExecutorError(_FAILURE)


def _await_prepared_receipt(
    attempt: _RelayLinuxExecutorDriverAttempt,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
) -> _WorkspacePreparedReceipt:
    owner = attempt.executor._workspace_owner
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token
    while time.monotonic() < attempt.build_deadline:
        deadline = min(attempt.build_deadline, time.monotonic() + _WAIT_SECONDS)
        prepared, acquired = owner._receipt_destination._read_before(
            owner._request,
            deadline,
        )
        if (
            acquired
            and type(prepared) is _WorkspacePreparedReceipt
            and prepared._matches(owner_token, record_token, require_active=True)
        ):
            return prepared
        terminal, acquired = bundle._terminal_destination._read_before(
            owner_token,
            deadline,
        )
        if acquired and type(terminal) is _WorkspaceWorkerTerminalReceipt:
            break
        bundle._controller._wait(
            min(_WAIT_SECONDS, max(0.0, attempt.build_deadline - time.monotonic()))
        )
    raise _RelayLinuxExecutorError(_FAILURE)


def _settle_finalize_and_raise(attempt: _RelayLinuxExecutorDriverAttempt) -> None:
    if _driver_attempt_is_retired(attempt) and _driver_cleanup_is_latched(attempt):
        _raise_retained_driver_failure(attempt)
    cleanup_deadline: float | None = None
    for _attempt in range(3):
        try:
            sampled = time.monotonic()
            candidate = sampled + attempt.cleanup_timeout_seconds
            if not (
                type(sampled) is float
                and type(candidate) is float
                and math.isfinite(sampled)
                and math.isfinite(candidate)
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            cleanup_deadline = candidate
            break
        except BaseException as error:
            _latch_driver_cleanup(attempt, error)
    if cleanup_deadline is None:
        _raise_retained_driver_failure(attempt)
    settled = False
    for _attempt in range(3):
        local_failures = attempt.failures
        escaped: BaseException | None = None
        try:
            settled = _settle_failed_driver_attempt(
                attempt,
                local_failures,
                cleanup_deadline=cleanup_deadline,
            )
        except BaseException as error:
            escaped = error
        finally:
            if not attempt._merge_failures(local_failures):
                _latch_driver_cleanup(attempt, _RelayLinuxExecutorError(_FAILURE))
        if escaped is not None:
            _latch_driver_cleanup(attempt, escaped)
            continue
        break
    finalized = False
    if settled:
        for _attempt in range(3):
            try:
                if _driver_attempt_is_abandoned(attempt):
                    finalized = bool(
                        _driver_attempt_is_retired(attempt) or _retire_driver_attempt(attempt)
                    )
                else:
                    finalized = bool(
                        _publish_driver_terminal(attempt) and _retire_driver_attempt(attempt)
                    )
                if finalized:
                    break
            except BaseException as error:
                _latch_driver_cleanup(attempt, error)
    if not finalized:
        raise _RelayLinuxExecutorError(_FAILURE)
    _raise_retained_driver_failure(attempt)


def _raise_retained_driver_failure(attempt: _RelayLinuxExecutorDriverAttempt) -> None:
    control, ordinary = attempt.failures
    retained = control if isinstance(control, (KeyboardInterrupt, SystemExit)) else ordinary
    if retained is not None:
        _scrub_control_minimal(retained)
        raise retained
    raise _RelayLinuxExecutorError(_FAILURE)


def _require_bundle(value: object) -> _WorkspaceWorkerBundle:
    if type(value) is not _WorkspaceWorkerBundle:
        raise _RelayLinuxExecutorError(_FAILURE)
    return value


def _require_construction(value: object) -> _WorkspaceWorkerThreadReceipt:
    if type(value) is not _WorkspaceWorkerThreadReceipt:
        raise _RelayLinuxExecutorError(_FAILURE)
    return value


def _require_prepared(value: object) -> _WorkspacePreparedReceipt:
    if type(value) is not _WorkspacePreparedReceipt:
        raise _RelayLinuxExecutorError(_FAILURE)
    return value


def _require_built(value: object) -> _WorkspaceBuiltReceipt:
    if type(value) is not _WorkspaceBuiltReceipt:
        raise _RelayLinuxExecutorError(_FAILURE)
    return value


def _require_binding(value: object) -> _RelayLinuxExecutorBuiltBinding:
    if type(value) is not _RelayLinuxExecutorBuiltBinding:
        raise _RelayLinuxExecutorError(_FAILURE)
    return value


__all__: list[str] = []

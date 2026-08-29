"""Canonical private state for one full relay Linux executor drive.

The caller retains the inert executor and destination, while this module binds
one exact call and its absolute workspace deadlines before any port, thread, or
filesystem effect.  Only the driver modules may observe these records.
"""

from __future__ import annotations

import math
import threading
import time
import weakref
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import TrustedHostTools
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
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_driver_values import (
    _driver_attempt_authority_matches,
    _driver_attempt_inputs_match,
    _driver_inputs_have_shape,
    _new_driver_attempt,
    _RelayLinuxExecutorDriverAttempt,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_anchor import (
    _executor_inner_authority_anchor,
    _RelayLinuxExecutorInnerAuthorityAnchor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
    _INNER_AUTHORITIES,
    _inner_replay_inputs_match,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _LOCK as _EXECUTOR_LOCK,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _RETIRED_KEYS,
    _canonical_executor_key,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)

_FAILURE = "Relay Linux executor driver state is invalid"
_PHASES = (
    "intended",
    "outer-preowned",
    "worker-created",
    "workspace-bound",
    "worker-started",
    "prepared",
    "built",
    "consume-intended",
    "consumed",
)
_DRIVER_LOCK = threading.RLock()


def _new_driver_terminal_authority() -> tuple[Callable[..., object], Callable[..., bool]]:
    retained: weakref.WeakKeyDictionary[_RelayLinuxExecutorKey, object] = (
        weakref.WeakKeyDictionary()
    )
    lock = threading.RLock()

    def bind(key: object, candidate: object) -> object | None:
        if type(key) is not _RelayLinuxExecutorKey or type(candidate) is not tuple:
            return None
        with lock:
            existing = retained.get(key)
            if existing is None:
                retained[key] = candidate
                existing = retained.get(key)
            return existing

    def matches(key: object, candidate: object) -> bool:
        if type(key) is not _RelayLinuxExecutorKey:
            return False
        with lock:
            return retained.get(key) is candidate

    return bind, matches


(
    _bind_driver_terminal_authority,
    _driver_terminal_authority_matches,
) = _new_driver_terminal_authority()


_DriverRecord = tuple[
    _RelayLinuxExecutorDriverAttempt,
    _WorkspaceWorkerBundle | None,
    _WorkspaceWorkerThreadReceipt | None,
    _WorkspacePreparedReceipt | None,
    _WorkspaceBuiltReceipt | None,
    _RelayLinuxExecutorBuiltBinding | None,
    str,
]

_DRIVER_RECORDS: weakref.WeakKeyDictionary[_RelayLinuxExecutorKey, _DriverRecord] = (
    weakref.WeakKeyDictionary()
)
_DRIVER_TERMINALS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[float, float, float, float, float, _RelayLinuxExecutorInnerAuthorityAnchor],
] = weakref.WeakKeyDictionary()


def _resolve_or_intend_driver_attempt(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    runner: object,
    bridge_probe: object,
    tools: TrustedHostTools,
    invocation_selection: object,
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
) -> _RelayLinuxExecutorDriverAttempt:
    """Bind one exact call and fixed workspace deadlines before preownership."""

    key = _canonical_executor_key(executor, destination)
    if type(key) is not _RelayLinuxExecutorKey or not _driver_inputs_have_shape(
        executor=executor,
        destination=destination,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_selection=invocation_selection,
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
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    with _DRIVER_LOCK:
        existing = _DRIVER_RECORDS.get(key)
        if existing is not None:
            if type(existing) is not tuple or len(existing) != 7:
                raise _RelayLinuxExecutorError(_FAILURE)
            attempt = existing[0]
            if not (
                _driver_record_registry_is_exact(key)
                and _driver_terminal_state_is_capacity_neutral()
                and type(existing[6]) is str
                and _driver_record_matches(existing, attempt, existing[6])
                and _driver_attempt_inputs_match(
                    attempt,
                    executor=executor,
                    destination=destination,
                    runner=runner,
                    bridge_probe=bridge_probe,
                    tools=tools,
                    invocation_selection=invocation_selection,
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
            ):
                raise _RelayLinuxExecutorError(_FAILURE)
            return attempt
        if (
            _DRIVER_RECORDS
            or key in _RETIRED_KEYS
            or not _driver_terminal_state_is_capacity_neutral()
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        sampled = time.monotonic()
        start_deadline = sampled + start_timeout_seconds
        build_deadline = sampled + build_timeout_seconds
        if not all(math.isfinite(value) for value in (sampled, start_deadline, build_deadline)):
            raise _RelayLinuxExecutorError(_FAILURE)
        attempt = _new_driver_attempt(
            executor=executor,
            destination=destination,
            key=key,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
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
            start_deadline=start_deadline,
            build_deadline=build_deadline,
        )
        record: _DriverRecord = (attempt, None, None, None, None, None, "intended")
        if not (
            _driver_record_has_shape(record, attempt, "intended")
            and attempt._bind_driver_record(record)
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        try:
            _store_driver_record(key, record)
            if not _driver_record_matches(_DRIVER_RECORDS.get(key), attempt, "intended"):
                raise _RelayLinuxExecutorError(_FAILURE)
        except BaseException as error:
            current = _DRIVER_RECORDS.get(key)
            if _same_driver_record(current, record):
                attempt._latch_cleanup(error)
                return attempt
            elif key not in _DRIVER_RECORDS and not _DRIVER_RECORDS:
                attempt._clear_driver_record(record)
                attempt._mark_abandoned()
                attempt._mark_retired()
            raise
        return attempt


def _driver_record(attempt: _RelayLinuxExecutorDriverAttempt) -> _DriverRecord | None:
    with _DRIVER_LOCK:
        return _canonical_driver_record_locked(attempt, repair=True)


def _recover_driver_attempt_for_call(
    **values: object,
) -> _RelayLinuxExecutorDriverAttempt | None:
    """Recover only an exact intent whose first record store took effect."""

    if not _driver_inputs_have_shape(**values):
        return None
    executor = values["executor"]
    destination = values["destination"]
    key = _canonical_executor_key(executor, destination)
    if type(key) is not _RelayLinuxExecutorKey:
        return None
    with _DRIVER_LOCK:
        record = _DRIVER_RECORDS.get(key)
        if not _driver_record_registry_is_exact(key) or not (
            type(record) is tuple and len(record) == 7
        ):
            return None
        attempt = record[0]
        return (
            attempt
            if _driver_record_matches(record, attempt, record[6])
            and _driver_attempt_inputs_match(attempt, **values)
            else None
        )


def _abandon_driver_attempt(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    """Retire only driver intent after canonical proof that preownership had no effect."""

    with _DRIVER_LOCK:
        record = _DRIVER_RECORDS.get(attempt.key)
        if not (
            len(_DRIVER_RECORDS) == 1
            and _driver_record_matches(record, attempt, "intended")
            and attempt._mark_abandoned()
        ):
            return False
        return _driver_record_matches(_DRIVER_RECORDS.get(attempt.key), attempt, "intended")


def _driver_attempt_is_abandoned(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    return attempt._is_abandoned()


def _driver_attempt_is_retired(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    return attempt._is_retired()


def _advance_driver_record(
    attempt: _RelayLinuxExecutorDriverAttempt,
    *,
    expected_phase: str,
    phase: str,
    bundle: _WorkspaceWorkerBundle | None = None,
    construction: _WorkspaceWorkerThreadReceipt | None = None,
    prepared: _WorkspacePreparedReceipt | None = None,
    built: _WorkspaceBuiltReceipt | None = None,
    binding: _RelayLinuxExecutorBuiltBinding | None = None,
) -> bool:
    if expected_phase not in _PHASES or phase not in _PHASES:
        return False
    candidate: _DriverRecord = (
        attempt,
        bundle,
        construction,
        prepared,
        built,
        binding,
        phase,
    )
    if not _driver_record_has_shape(candidate, attempt, phase):
        return False
    with _DRIVER_LOCK:
        current = _canonical_driver_record_locked(attempt, repair=True)
        if _same_driver_record(current, candidate):
            return True
        if not _driver_record_matches(current, attempt, expected_phase):
            return False
        if not attempt._advance_driver_record(current, candidate):
            return False
        _store_driver_record(attempt.key, candidate)
        return _driver_record_matches(_DRIVER_RECORDS.get(attempt.key), attempt, phase)


def _retire_driver_attempt(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    with _DRIVER_LOCK:
        current = _DRIVER_RECORDS.get(attempt.key)
        if current is None:
            authority = _closure_driver_record(attempt)
            if _DRIVER_RECORDS:
                return False
            if authority is not None and not attempt._clear_driver_record(authority):
                return False
            return attempt._mark_retired()
        if not (
            type(current) is tuple
            and len(current) == 7
            and type(current[6]) is str
            and _driver_record_matches(current, attempt, current[6])
        ) or any(candidate is not attempt.key for candidate in _DRIVER_RECORDS):
            return False
        first_error: BaseException | None = None
        try:
            _pop_driver_record(attempt.key)
        except BaseException as error:
            attempt._latch_cleanup(error)
            first_error = error
        completed = False
        if attempt.key not in _DRIVER_RECORDS and not _DRIVER_RECORDS:
            for _attempt in range(3):
                try:
                    completed = bool(
                        attempt._clear_driver_record(current) and attempt._mark_retired()
                    )
                except BaseException as error:
                    attempt._latch_cleanup(error)
                    if first_error is None:
                        first_error = error
                if completed:
                    break
        if first_error is not None:
            raise first_error
        return completed


def _publish_driver_terminal(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    anchor = _executor_inner_authority_anchor(attempt.key)
    if type(anchor) is not _RelayLinuxExecutorInnerAuthorityAnchor:
        return False
    candidate = (
        attempt.start_timeout_seconds,
        attempt.build_timeout_seconds,
        attempt.cleanup_timeout_seconds,
        attempt.start_deadline,
        attempt.build_deadline,
        anchor,
    )
    terminal = _bind_driver_terminal_authority(attempt.key, candidate)
    if type(terminal) is not tuple:
        return False
    with _DRIVER_LOCK:
        if not _driver_terminal_state_is_capacity_neutral():
            return False
        existing = _DRIVER_TERMINALS.get(attempt.key)
        if existing is None:
            _store_driver_terminal(attempt.key, terminal)
            existing = _DRIVER_TERMINALS.get(attempt.key)
        return _driver_terminal_matches(
            existing,
            attempt.key,
            start_timeout_seconds=attempt.start_timeout_seconds,
            build_timeout_seconds=attempt.build_timeout_seconds,
            cleanup_timeout_seconds=attempt.cleanup_timeout_seconds,
        )


def _terminal_binding_for_driver_call(
    *,
    executor: object,
    destination: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_selection: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    start_timeout_seconds: object,
    build_timeout_seconds: object,
    cleanup_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
) -> _RelayLinuxExecutorBuiltBinding | None:
    if not _driver_inputs_have_shape(
        executor=executor,
        destination=destination,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_selection=invocation_selection,
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
    ):
        return None
    key = _canonical_executor_key(executor, destination)
    if type(key) is not _RelayLinuxExecutorKey:
        return None
    with _DRIVER_LOCK:
        if (
            _DRIVER_RECORDS
            or key not in _RETIRED_KEYS
            or not _driver_terminal_state_is_capacity_neutral()
            or not _driver_terminal_matches(
                _DRIVER_TERMINALS.get(key),
                key,
                start_timeout_seconds=start_timeout_seconds,
                build_timeout_seconds=build_timeout_seconds,
                cleanup_timeout_seconds=cleanup_timeout_seconds,
            )
        ):
            return None
    with _EXECUTOR_LOCK:
        authority = _INNER_AUTHORITIES.get(key)
        values = (
            authority[1]
            if type(authority) is tuple
            and len(authority) == 4
            and type(authority[2]) is str
            and authority[2] == "terminal"
            and type(authority[1]) is tuple
            and len(authority[1]) == 14
            else None
        )
        binding = values[0] if values is not None else None
    if type(binding) is not _RelayLinuxExecutorBuiltBinding:
        return None
    return (
        binding
        if _inner_replay_inputs_match(
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
        else None
    )


def _driver_state_is_empty() -> bool:
    with _DRIVER_LOCK:
        return not _DRIVER_RECORDS


def _driver_terminal_state_is_capacity_neutral() -> bool:
    with _DRIVER_LOCK:
        return all(
            type(key) is _RelayLinuxExecutorKey
            and key in _RETIRED_KEYS
            and type(terminal) is tuple
            and len(terminal) == 6
            and _driver_terminal_authority_matches(key, terminal)
            and _driver_terminal_matches(
                terminal,
                key,
                start_timeout_seconds=terminal[0],
                build_timeout_seconds=terminal[1],
                cleanup_timeout_seconds=terminal[2],
            )
            for key, terminal in _DRIVER_TERMINALS.items()
        )


def _latch_driver_cleanup(
    attempt: _RelayLinuxExecutorDriverAttempt,
    error: BaseException,
) -> None:
    attempt._latch_cleanup(error)


def _driver_cleanup_is_latched(attempt: _RelayLinuxExecutorDriverAttempt) -> bool:
    return attempt._cleanup_is_latched()


def _driver_record_matches(
    record: object,
    attempt: _RelayLinuxExecutorDriverAttempt,
    phase: str,
) -> bool:
    if not _driver_record_has_shape(record, attempt, phase):
        return False
    authority = _closure_driver_record(attempt)
    return authority is not None and _same_driver_record(record, authority)


def _driver_record_has_shape(
    record: object,
    attempt: _RelayLinuxExecutorDriverAttempt,
    phase: str,
) -> bool:
    if not (
        type(record) is tuple
        and len(record) == 7
        and record[0] is attempt
        and _driver_attempt_authority_matches(attempt)
        and type(record[6]) is str
        and record[6] == phase
        and phase in _PHASES
    ):
        return False
    required = _PHASES.index(phase)
    expected_types = (
        _WorkspaceWorkerBundle,
        _WorkspaceWorkerThreadReceipt,
        _WorkspacePreparedReceipt,
        _WorkspaceBuiltReceipt,
        _RelayLinuxExecutorBuiltBinding,
    )
    thresholds = (2, 2, 5, 6, 8)
    return all(
        type(record[index + 1]) is expected if required >= threshold else record[index + 1] is None
        for index, (expected, threshold) in enumerate(zip(expected_types, thresholds, strict=True))
    )


def _closure_driver_record(
    attempt: _RelayLinuxExecutorDriverAttempt,
) -> _DriverRecord | None:
    values = attempt._driver_record_values()
    if type(values) is not tuple or len(values) != 6 or type(values[5]) is not str:
        return None
    record = (attempt, *values)
    return record if _driver_record_has_shape(record, attempt, values[5]) else None


def _canonical_driver_record_locked(
    attempt: _RelayLinuxExecutorDriverAttempt,
    *,
    repair: bool,
) -> _DriverRecord | None:
    if not (
        _driver_record_registry_is_exact(attempt.key)
        and _driver_terminal_state_is_capacity_neutral()
    ):
        return None
    authority = _closure_driver_record(attempt)
    if authority is None:
        return None
    current = _DRIVER_RECORDS.get(attempt.key)
    if _same_driver_record(current, authority):
        return current
    if not repair or not _driver_record_is_predecessor(current, authority):
        return None
    _store_driver_record(attempt.key, authority)
    repaired = _DRIVER_RECORDS.get(attempt.key)
    return repaired if _same_driver_record(repaired, authority) else None


def _driver_record_registry_is_exact(key: _RelayLinuxExecutorKey) -> bool:
    return bool(
        len(_DRIVER_RECORDS) == 1
        and all(
            type(candidate) is _RelayLinuxExecutorKey and candidate is key
            for candidate in _DRIVER_RECORDS
        )
    )


def _driver_record_is_predecessor(current: object, authority: _DriverRecord) -> bool:
    if not (
        type(current) is tuple
        and len(current) == 7
        and current[0] is authority[0]
        and type(current[6]) is str
        and current[6] in _PHASES
        and authority[6] in _PHASES
        and _PHASES.index(authority[6]) == _PHASES.index(current[6]) + 1
        and _driver_record_has_shape(current, authority[0], current[6])
    ):
        return False
    return all(
        current[index] is None or current[index] is authority[index] for index in range(1, 6)
    )


def _same_driver_record(left: object, right: _DriverRecord) -> bool:
    return bool(
        type(left) is tuple
        and len(left) == len(right)
        and all(left[index] is right[index] for index in range(6))
        and type(left[6]) is str
        and left[6] == right[6]
    )


def _driver_terminal_matches(
    terminal: object,
    key: _RelayLinuxExecutorKey,
    *,
    start_timeout_seconds: object,
    build_timeout_seconds: object,
    cleanup_timeout_seconds: object,
) -> bool:
    anchor = _executor_inner_authority_anchor(key)
    return bool(
        type(terminal) is tuple
        and len(terminal) == 6
        and _driver_terminal_authority_matches(key, terminal)
        and all(type(terminal[index]) is float for index in range(5))
        and terminal[0] == start_timeout_seconds
        and terminal[1] == build_timeout_seconds
        and terminal[2] == cleanup_timeout_seconds
        and math.isfinite(terminal[3])
        and math.isfinite(terminal[4])
        and type(anchor) is _RelayLinuxExecutorInnerAuthorityAnchor
        and terminal[5] is anchor
    )


def _store_driver_record(key: object, record: object) -> None:
    _DRIVER_RECORDS[key] = record  # type: ignore[index,assignment]


def _pop_driver_record(key: object) -> None:
    _DRIVER_RECORDS.pop(key, None)


def _store_driver_terminal(key: object, terminal: object) -> None:
    _DRIVER_TERMINALS[key] = terminal  # type: ignore[index,assignment]


__all__: list[str] = []

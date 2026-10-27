"""Closure-backed values for one private executor-driver call."""

from __future__ import annotations

import math
import threading
import weakref
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import TrustedHostTools
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _synthetic_invocation_driver_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _is_concrete_invocation_selection,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _MAX_BUILD_SECONDS,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _executor_value_matches,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation

_ATTEMPT_TOKEN = object()
_MAX_CLEANUP_SECONDS = 60.0
_MAX_SECRET_BYTES = 4096
_MAX_START_SECONDS = 30.0


def _new_attempt_type() -> tuple[type, Callable[..., object]]:
    retained: weakref.WeakKeyDictionary[object, tuple[object, ...]] = weakref.WeakKeyDictionary()
    originals: weakref.WeakKeyDictionary[object, weakref.ReferenceType[object]] = (
        weakref.WeakKeyDictionary()
    )
    operation_locks: weakref.WeakKeyDictionary[object, threading.RLock] = (
        weakref.WeakKeyDictionary()
    )
    failure_holders: weakref.WeakKeyDictionary[object, weakref.ReferenceType[object]] = (
        weakref.WeakKeyDictionary()
    )
    failure_states: weakref.WeakKeyDictionary[object, tuple[object, ...]] = (
        weakref.WeakKeyDictionary()
    )
    abandoned: weakref.WeakKeyDictionary[object, bool] = weakref.WeakKeyDictionary()
    retired: weakref.WeakKeyDictionary[object, bool] = weakref.WeakKeyDictionary()
    driver_records: weakref.WeakKeyDictionary[object, tuple[object, ...]] = (
        weakref.WeakKeyDictionary()
    )
    terminal_observations: weakref.WeakKeyDictionary[object, RelayProbeObservation] = (
        weakref.WeakKeyDictionary()
    )
    terminal_completed: weakref.WeakKeyDictionary[object, bool] = weakref.WeakKeyDictionary()
    registry_lock = threading.RLock()
    fields = {
        "executor": 0,
        "destination": 1,
        "key": 2,
        "runner": 3,
        "bridge_probe": 4,
        "tools": 5,
        "invocation_selection": 6,
        "static_auth_secret": 7,
        "now": 8,
        "start_timeout_seconds": 9,
        "build_timeout_seconds": 10,
        "browser_timeout_seconds": 11,
        "runtime_timeout_seconds": 12,
        "cleanup_timeout_seconds": 13,
        "clock": 14,
        "wait": 15,
        "epoch_clock": 16,
        "start_deadline": 17,
        "build_deadline": 18,
    }

    failure_holder_token = object()

    class _FailureHolder:
        __slots__ = ("__weakref__", "_lock", "_state", "_token")

        def __init__(self, token: object) -> None:
            if token is not failure_holder_token:
                raise TypeError("Relay Linux executor driver failure state is factory-owned")
            object.__setattr__(self, "_token", token)
            object.__setattr__(self, "_lock", threading.Lock())
            state = (None, None, False)
            object.__setattr__(self, "_state", state)
            with registry_lock:
                failure_states[self] = state

        def _matches(self) -> bool:
            with registry_lock, self._lock:
                return bool(
                    self._token is failure_holder_token and self._state_values_locked() is not None
                )

        def _latch(self, error: BaseException) -> None:
            with registry_lock, self._lock:
                values = self._state_values_locked()
                if values is None:
                    return
                self._merge_error_locked(values, error)

        def _is_latched(self) -> bool:
            with registry_lock, self._lock:
                values = self._state_values_locked()
                return bool(values is not None and (values[0] is not None or values[2] is True))

        def _failure_values(self) -> list[BaseException | None]:
            with registry_lock, self._lock:
                values = self._state_values_locked()
                if values is None:
                    raise AttributeError("Relay Linux executor driver failure state is invalid")
                kind, code, ordinary = values
                control: KeyboardInterrupt | SystemExit | None
                if kind is KeyboardInterrupt:
                    control = KeyboardInterrupt()
                elif kind is SystemExit:
                    control = SystemExit(code)
                else:
                    control = None
                failure = RuntimeError("Relay Linux executor driver failed") if ordinary else None
                return [control, failure]

        def _merge_failure_values(self, values: object) -> bool:
            if not (
                type(values) is list
                and len(values) == 2
                and all(value is None or isinstance(value, BaseException) for value in values)
            ):
                return False
            with registry_lock, self._lock:
                current = self._state_values_locked()
                if current is None:
                    return False
                for error in values:
                    if error is None:
                        continue
                    current = self._merge_error_locked(current, error)
                return True

        def _merge_error_locked(
            self,
            values: tuple[object, object, bool],
            error: BaseException,
        ) -> tuple[object, object, bool]:
            kind, code, ordinary = values
            if isinstance(error, KeyboardInterrupt):
                if kind is not None:
                    return values
                candidate = (KeyboardInterrupt, None, ordinary)
            elif isinstance(error, SystemExit):
                if kind is not None:
                    return values
                exit_code = SystemExit.__dict__["code"].__get__(error, SystemExit)
                candidate = (
                    SystemExit,
                    exit_code if exit_code is None or type(exit_code) is int else 1,
                    ordinary,
                )
            elif ordinary:
                return values
            else:
                candidate = (kind, code, True)
            object.__setattr__(self, "_state", candidate)
            failure_states[self] = candidate
            return candidate

        def _state_values_locked(
            self,
        ) -> tuple[object, object, bool] | None:
            state = self._state
            return (
                state
                if type(state) is tuple
                and len(state) == 3
                and failure_states.get(self) is state
                and (state[0] is None or state[0] is KeyboardInterrupt or state[0] is SystemExit)
                and (
                    state[1] is None
                    if state[0] is None or state[0] is KeyboardInterrupt
                    else state[1] is None or type(state[1]) is int
                )
                and type(state[2]) is bool
                else None
            )

        def __setattr__(self, _name: str, _value: object) -> None:
            raise AttributeError("Relay Linux executor driver failure state is immutable")

    def failure_holder_for(owner: object) -> _FailureHolder | None:
        try:
            holder = object.__getattribute__(owner, "_failure_holder")
        except (AttributeError, TypeError):
            return None
        with registry_lock:
            reference = failure_holders.get(owner)
            return (
                holder
                if type(holder) is _FailureHolder
                and reference is not None
                and reference() is holder
                and holder._matches()
                else None
            )

    class _DriverAttempt:
        __slots__ = ("__weakref__", "_authentic", "_failure_holder")

        def __init__(self, token: object, values: tuple[object, ...]) -> None:
            if token is not _ATTEMPT_TOKEN or len(values) != len(fields):
                raise TypeError("Relay Linux executor driver attempt is factory-owned")
            key = values[2]
            if type(key) is not _RelayLinuxExecutorKey:
                raise TypeError("Relay Linux executor driver attempt is factory-owned")
            object.__setattr__(self, "_authentic", _ATTEMPT_TOKEN)
            holder = _FailureHolder(failure_holder_token)
            object.__setattr__(self, "_failure_holder", holder)
            with registry_lock:
                original = originals.get(key)
                retained_original = original() if original is not None else None
                if (
                    retained_original is not None
                    and retained_original is not self
                    and not retained_original._is_retired()
                ):
                    raise TypeError("Relay Linux executor driver call changed")
                retained[self] = values
                originals[key] = weakref.ref(self)
                operation_locks[self] = threading.RLock()
                failure_holders[self] = weakref.ref(holder)
                abandoned[self] = False
                retired[self] = False
                terminal_completed[self] = False

        def __getattribute__(self, name: str) -> object:
            index = fields.get(name)
            if index is not None:
                with registry_lock:
                    values = retained.get(self)
                    if values is None:
                        raise AttributeError(name)
                    return values[index]
            if name == "operation_lock":
                with registry_lock:
                    value = operation_locks.get(self)
                    if value is None:
                        raise AttributeError(name)
                    return value
            if name == "failures":
                holder = failure_holder_for(self)
                if holder is None:
                    raise AttributeError(name)
                return holder._failure_values()
            return object.__getattribute__(self, name)

        def _authority_matches(self, supplied: tuple[object, ...] | None = None) -> bool:
            with registry_lock:
                values = retained.get(self)
                key = values[2] if values is not None else None
                original = originals.get(key)
                return bool(
                    self._authentic is _ATTEMPT_TOKEN
                    and type(values) is tuple
                    and len(values) == len(fields)
                    and type(key) is _RelayLinuxExecutorKey
                    and original is not None
                    and original() is self
                    and (supplied is None or _same_call_values(values, supplied))
                )

        def _latch_cleanup(self, error: BaseException) -> None:
            holder = failure_holder_for(self)
            if holder is not None:
                holder._latch(error)

        def _cleanup_is_latched(self) -> bool:
            holder = failure_holder_for(self)
            return bool(holder is not None and holder._is_latched())

        def _merge_failures(self, values: object) -> bool:
            holder = failure_holder_for(self)
            return bool(holder is not None and holder._merge_failure_values(values))

        def _mark_abandoned(self) -> bool:
            with registry_lock:
                if not self._authority_matches():
                    return False
                if abandoned.get(self) is True:
                    return True
                if abandoned.get(self) is not False:
                    return False
                abandoned[self] = True
                return abandoned.get(self) is True

        def _is_abandoned(self) -> bool:
            with registry_lock:
                values = retained.get(self)
                return bool(
                    self._authentic is _ATTEMPT_TOKEN
                    and _driver_call_values_have_shape(values)
                    and abandoned.get(self) is True
                )

        def _mark_retired(self) -> bool:
            with registry_lock:
                if not self._authority_matches():
                    return False
                if retired.get(self) is True:
                    return True
                if retired.get(self) is not False:
                    return False
                retired[self] = True
                return retired.get(self) is True

        def _is_retired(self) -> bool:
            with registry_lock:
                values = retained.get(self)
                return bool(
                    self._authentic is _ATTEMPT_TOKEN
                    and _driver_call_values_have_shape(values)
                    and retired.get(self) is True
                )

        def _bind_driver_record(self, record: object) -> bool:
            values = _driver_record_values(self, record)
            if values is None or not self._authority_matches():
                return False
            with registry_lock:
                current = driver_records.get(self)
                if current is None:
                    driver_records[self] = values
                    current = driver_records.get(self)
                return _same_driver_record_values(current, values)

        def _advance_driver_record(self, expected: object, candidate: object) -> bool:
            expected_values = _driver_record_values(self, expected)
            candidate_values = _driver_record_values(self, candidate)
            if expected_values is None or candidate_values is None or not self._authority_matches():
                return False
            with registry_lock:
                current = driver_records.get(self)
                if _same_driver_record_values(current, candidate_values):
                    return True
                if not _same_driver_record_values(current, expected_values):
                    return False
                driver_records[self] = candidate_values
                return _same_driver_record_values(driver_records.get(self), candidate_values)

        def _driver_record_values(self) -> tuple[object, ...] | None:
            with registry_lock:
                values = driver_records.get(self)
                return (
                    values
                    if self._authority_matches() and type(values) is tuple and len(values) == 6
                    else None
                )

        def _clear_driver_record(self, expected: object) -> bool:
            expected_values = _driver_record_values(self, expected)
            if expected_values is None:
                return False
            with registry_lock:
                current = driver_records.get(self)
                if current is None:
                    return True
                if not _same_driver_record_values(current, expected_values):
                    return False
                driver_records.pop(self, None)
                return self not in driver_records

        def _bind_terminal_observation(self, observation: object) -> bool:
            if type(observation) is not RelayProbeObservation:
                return False
            with registry_lock:
                if not self._authority_matches() or self._cleanup_is_latched():
                    return False
                existing = terminal_observations.get(self)
                if existing is None:
                    terminal_observations[self] = observation
                    existing = terminal_observations.get(self)
                return existing is observation

        def _complete_terminal_observation(self, observation: object) -> bool:
            with registry_lock:
                if not (
                    self._authority_matches()
                    and abandoned.get(self) is False
                    and not self._cleanup_is_latched()
                    and terminal_observations.get(self) is observation
                    and type(observation) is RelayProbeObservation
                ):
                    return False
                terminal_completed[self] = True
                return terminal_completed.get(self) is True

        def _terminal_observation(self) -> RelayProbeObservation | None:
            with registry_lock:
                observation = terminal_observations.get(self)
                return (
                    observation
                    if self._is_retired()
                    and abandoned.get(self) is False
                    and not self._cleanup_is_latched()
                    and terminal_completed.get(self) is True
                    and type(observation) is RelayProbeObservation
                    else None
                )

        def __setattr__(self, _name: str, _value: object) -> None:
            raise AttributeError("Relay Linux executor driver attempt is immutable")

    def new(**values: object) -> object:
        call_values = _call_values(values)
        if call_values is None:
            raise TypeError("Relay Linux executor driver call is invalid")
        return _DriverAttempt(_ATTEMPT_TOKEN, call_values)

    return _DriverAttempt, new


def _driver_record_values(owner: object, record: object) -> tuple[object, ...] | None:
    if type(record) is not tuple or len(record) != 7 or record[0] is not owner:
        return None
    return record[1:]


def _same_driver_record_values(left: object, right: object) -> bool:
    return bool(
        type(left) is tuple
        and type(right) is tuple
        and len(left) == len(right) == 6
        and all(left[index] is right[index] for index in range(5))
        and type(left[5]) is str
        and type(right[5]) is str
        and left[5] == right[5]
    )


def _call_values(values: dict[str, object]) -> tuple[object, ...] | None:
    names = (
        "executor",
        "destination",
        "key",
        "runner",
        "bridge_probe",
        "tools",
        "invocation_selection",
        "static_auth_secret",
        "now",
        "start_timeout_seconds",
        "build_timeout_seconds",
        "browser_timeout_seconds",
        "runtime_timeout_seconds",
        "cleanup_timeout_seconds",
        "clock",
        "wait",
        "epoch_clock",
        "start_deadline",
        "build_deadline",
    )
    try:
        result = tuple(values[name] for name in names)
    except KeyError:
        return None
    return result if _driver_call_values_have_shape(result) else None


def _driver_call_values_have_shape(values: object) -> bool:
    if type(values) is not tuple or len(values) != 19:
        return False
    durations = values[9:14]
    return bool(
        type(values[0]) is _RelayLinuxExecutorOwner
        and type(values[1]) is _RelayLinuxExecutorDestination
        and type(values[2]) is _RelayLinuxExecutorKey
        and _executor_value_matches(values[0], values[1])
        and values[0]._cleanup_authority._key is values[2]
        and values[3] is not None
        and values[4] is not None
        and type(values[5]) is TrustedHostTools
        and (
            _synthetic_invocation_driver_matches(values[6])
            or _is_concrete_invocation_selection(values[6])
        )
        and _replay_inputs_can_be_weakly_bound(values)
        and type(values[8]) is datetime
        and all(
            type(value) is float and math.isfinite(value) and value > 0.0 for value in durations
        )
        and durations[0] <= _MAX_START_SECONDS
        and durations[1] <= _MAX_BUILD_SECONDS
        and durations[3] <= 60.0
        and durations[4] <= _MAX_CLEANUP_SECONDS
        and all(callable(value) for value in values[14:17])
        and all(type(value) is float and math.isfinite(value) for value in values[17:19])
    )


def _replay_inputs_can_be_weakly_bound(values: tuple[object, ...]) -> bool:
    try:
        secret = values[7]
        if type(secret) is not str or not 0 < len(secret.encode("utf-8")) <= _MAX_SECRET_BYTES:
            return False
        for index in (3, 4, 5, 14, 15, 16):
            weakref.ref(values[index])
        if not _is_concrete_invocation_selection(values[6]):
            weakref.ref(values[6])
        return True
    except BaseException:
        return False


def _same_call_values(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    return bool(
        _driver_call_values_have_shape(left)
        and _driver_call_values_have_shape(right)
        and all(left[index] is right[index] for index in range(8))
        and left[8] == right[8]
        and all(left[index] == right[index] for index in range(9, 14))
        and all(left[index] is right[index] for index in range(14, 17))
        and left[17] == right[17]
        and left[18] == right[18]
    )


(
    _RelayLinuxExecutorDriverAttempt,
    _new_driver_attempt,
) = _new_attempt_type()


def _driver_inputs_have_shape(**values: object) -> bool:
    complete = {
        **values,
        "key": values["executor"]._cleanup_authority._key
        if type(values.get("executor")) is _RelayLinuxExecutorOwner
        else None,
        "start_deadline": 1.0,
        "build_deadline": 1.0,
    }
    return _call_values(complete) is not None


def _driver_attempt_inputs_match(
    attempt: object,
    *,
    start_deadline: object | None = None,
    build_deadline: object | None = None,
    **values: object,
) -> bool:
    if type(attempt) is not _RelayLinuxExecutorDriverAttempt:
        return False
    complete = {
        **values,
        "key": attempt.key,
        "start_deadline": attempt.start_deadline if start_deadline is None else start_deadline,
        "build_deadline": attempt.build_deadline if build_deadline is None else build_deadline,
    }
    supplied = _call_values(complete)
    return bool(supplied is not None and attempt._authority_matches(supplied))


def _driver_attempt_authority_matches(attempt: object) -> bool:
    return bool(type(attempt) is _RelayLinuxExecutorDriverAttempt and attempt._authority_matches())


__all__: list[str] = []

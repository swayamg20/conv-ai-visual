"""Canonical effect-free concrete invocation grant and capability pair."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _CONCRETE_ADAPTER_SEAL,
    RelayInvocationDriver,
    RelayInvocationTools,
    _RelayChildAuthorityDestination,
    _RelayChildStartDestination,
    _RelayChildStopDestination,
    _RelayInvocationOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_callback import (
    _new_inert_concrete_driver,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_tools import (
    _live_pair_capabilities_match,
    _new_concrete_tools,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
    _DESTINATION_TOKEN,
    _GRANT_TOKEN,
    _live_consumed_build_matches,
    _RelayConcreteInvocationGrant,
    _RelayConcreteInvocationPairDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _is_concrete_invocation_selection,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    _STOP_TOKEN,
    RelayStopRequest,
)

_FAILURE = "Relay concrete invocation pair is invalid"
_MAX_RUNTIME_SECONDS = 60.0
_MAX_CLEANUP_SECONDS = 60.0
_ROLES = ("app", "web", "browser")
_LOCK = threading.RLock()


_PairRecord = tuple[
    _RelayConcreteInvocationGrant,
    RelayInvocationDriver,
    RelayInvocationTools,
    _RelayConcreteInvocationPairDestination,
    tuple[tuple[str, object], ...],
]
_PAIR_ENTRIES: dict[
    object,
    _RelayConcreteInvocationPairDestination | _PairRecord,
] = {}


def _resolve_or_preown_concrete_pair_destination(
    **values: object,
) -> _RelayConcreteInvocationPairDestination:
    if not _pair_inputs_have_shape(values):
        raise TypeError(_FAILURE)
    build = values["build"]
    key = build.key  # type: ignore[attr-defined]
    with _LOCK:
        existing = _PAIR_ENTRIES.get(key)
        if _pair_record_has_shape(existing):
            pair_key = existing[0]._pair_key
            if not _pair_record_matches(pair_key, existing, existing[3], values):
                raise TypeError(_FAILURE)
            return existing[3]
        if type(existing) is _RelayConcreteInvocationPairDestination:
            if len(_PAIR_ENTRIES) != 1 or not existing._matches(**values):
                raise TypeError(_FAILURE)
            return existing
        if _PAIR_ENTRIES:
            raise TypeError(_FAILURE)
        destination = _RelayConcreteInvocationPairDestination(
            _DESTINATION_TOKEN,
            **values,  # type: ignore[arg-type]
        )
        _store_pair_entry(key, destination)
        if _PAIR_ENTRIES.get(key) is not destination:
            raise TypeError(_FAILURE)
        return destination


def _recover_concrete_pair_destination(
    **values: object,
) -> _RelayConcreteInvocationPairDestination | None:
    expected = {
        "build",
        "binding",
        "selection",
        "runtime_timeout_seconds",
        "cleanup_timeout_seconds",
        "clock",
        "wait",
        "epoch_clock",
    }
    if set(values) != expected:
        raise TypeError(_FAILURE)
    requested_cleanup = values["cleanup_timeout_seconds"]
    if not (
        type(requested_cleanup) is float
        and math.isfinite(requested_cleanup)
        and 0.0 < requested_cleanup <= _MAX_CLEANUP_SECONDS
    ):
        raise TypeError(_FAILURE)
    build = values["build"]
    key = getattr(build, "key", None)
    with _LOCK:
        if not _PAIR_ENTRIES:
            return None
        if len(_PAIR_ENTRIES) != 1:
            raise TypeError(_FAILURE)
        entry = _PAIR_ENTRIES.get(key)
        if type(entry) is _RelayConcreteInvocationPairDestination:
            destination = entry
        elif _pair_record_has_shape(entry) and type(entry[3]) is (
            _RelayConcreteInvocationPairDestination
        ):
            destination = entry[3]
        else:
            raise TypeError(_FAILURE)
        completed = {
            **values,
            "runtime_deadline": destination._runtime_deadline,
            "cleanup_timeout_seconds": destination._cleanup_timeout_seconds,
        }
        if not _pair_inputs_have_shape(completed) or not destination._matches(**completed):
            raise TypeError(_FAILURE)
        if _pair_record_has_shape(entry) and not _pair_record_matches(
            getattr(entry[0], "_pair_key", None),
            entry,
            destination,
            completed,
        ):
            raise TypeError(_FAILURE)
        return destination


def _resolve_or_mint_concrete_invocation_pair(
    destination: _RelayConcreteInvocationPairDestination,
    **values: object,
) -> tuple[_RelayConcreteInvocationGrant, RelayInvocationDriver, RelayInvocationTools]:
    if not _pair_inputs_have_shape(values):
        raise TypeError(_FAILURE)
    build = values["build"]
    key = build.key  # type: ignore[attr-defined]
    with _LOCK:
        existing = _PAIR_ENTRIES.get(key)
        if not destination._matches(**values):
            raise TypeError(_FAILURE)
        if _pair_record_has_shape(existing):
            pair_key = existing[0]._pair_key
            if not _pair_record_matches(pair_key, existing, destination, values):
                raise TypeError(_FAILURE)
            candidate = existing[:3]
        else:
            if existing is not destination or len(_PAIR_ENTRIES) != 1:
                raise TypeError(_FAILURE)
            pair_key = object()
            grant = _RelayConcreteInvocationGrant(
                _GRANT_TOKEN,
                destination=destination,
                pair_key=pair_key,
                **values,  # type: ignore[arg-type]
            )
            tools = _new_concrete_tools(build, values["epoch_clock"], pair_key)
            driver = _new_inert_concrete_driver(pair_key)
            roles = tuple((role, object()) for role in _ROLES)
            record = (grant, driver, tools, destination, roles)
            _store_pair_entry(key, record)
            if not _pair_record_matches(pair_key, record, destination, values):
                raise TypeError(_FAILURE)
            candidate = record[:3]
    error: BaseException | None = None
    try:
        destination._publish(*candidate)
    except BaseException as escaped:
        error = escaped
    recovered = destination._read()
    if not (
        type(recovered) is tuple
        and len(recovered) == 3
        and all(recovered[index] is candidate[index] for index in range(3))
        and _canonical_concrete_invocation_pair_matches(recovered[1], recovered[2])
    ):
        if error is not None:
            raise error
        raise TypeError(_FAILURE)
    if error is not None:
        raise error
    return recovered


def _canonical_concrete_invocation_pair_matches(driver: object, tools: object) -> bool:
    try:
        pair_key = getattr(driver, "_pair_key", None)
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            return bool(
                type(pair_key) is object
                and type(driver) is RelayInvocationDriver
                and type(tools) is RelayInvocationTools
                and record is not None
                and record[1] is driver
                and record[2] is tools
                and _pair_record_is_canonical(pair_key, record)
                and _pair_publication_matches(record)
                and _live_pair_capabilities_match(record)
            )
    except BaseException:
        return False


def _concrete_invocation_pair_matches_inputs(
    driver: object,
    tools: object,
    **values: object,
) -> bool:
    try:
        if not _pair_inputs_have_shape(values):
            return False
        pair_key = getattr(driver, "_pair_key", None)
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            return bool(
                record is not None
                and record[1] is driver
                and record[2] is tools
                and _pair_record_matches(pair_key, record, record[3], values)
                and _pair_publication_matches(record)
            )
    except BaseException:
        return False


def _concrete_invocation_pair_member_matches(
    member: object,
    pair_key: object,
    role: str,
) -> bool:
    try:
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            index = 1 if role == "driver" else 2 if role == "tools" else -1
            return bool(
                index >= 0
                and record is not None
                and record[index] is member
                and _pair_record_is_canonical(pair_key, record)
                and _pair_publication_matches(record)
                and _live_pair_capabilities_match(record)
            )
    except BaseException:
        return False


def _concrete_invocation_cleanup_contract(
    driver: object,
    tools: object,
) -> tuple[object, float, Callable[[], float]] | None:
    try:
        if not _canonical_concrete_invocation_pair_matches(driver, tools):
            return None
        pair_key = getattr(driver, "_pair_key", None)
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            if (
                record is None
                or not _pair_record_is_canonical(pair_key, record)
                or not _pair_publication_matches(record)
                or not _live_pair_capabilities_match(record)
            ):
                return None
            grant = record[0]
            return (pair_key, grant._cleanup_timeout_seconds, grant._clock)
    except BaseException:
        return None


def _resolve_or_mint_concrete_invocation_stop_request(
    driver: object,
    tools: object,
    owner_destination: object,
) -> RelayStopRequest | None:
    pair_key = getattr(driver, "_pair_key", None)
    with _LOCK:
        record = _record_for_pair_key(pair_key)
        if not _cleanup_record_matches(record, driver, tools, owner_destination):
            return None
        existing = record[3]._read_stop_request()
        if existing is not None:
            return (
                existing
                if existing._matches(pair_key) and record[3]._bind_stop_request(existing)
                else None
            )
        grant = record[0]
    sampled = grant._clock()
    deadline = sampled + grant._cleanup_timeout_seconds if type(sampled) is float else None
    if not (
        type(sampled) is float
        and math.isfinite(sampled)
        and type(deadline) is float
        and math.isfinite(deadline)
        and deadline > sampled
    ):
        return None
    candidate = RelayStopRequest(
        _STOP_TOKEN,
        pair_key=pair_key,
        absolute_deadline=deadline,
    )
    with _LOCK:
        current = _record_for_pair_key(pair_key)
        if current is not record or not _cleanup_record_matches(
            current,
            driver,
            tools,
            owner_destination,
        ):
            return None
        bound = current[3]._bind_stop_request(candidate)
        resolved = current[3]._read_stop_request()
        return resolved if bound and resolved is not None and resolved._matches(pair_key) else None


def _cleanup_record_matches(
    record: _PairRecord | None,
    driver: object,
    tools: object,
    owner_destination: object,
) -> bool:
    try:
        pair_key = getattr(driver, "_pair_key", None)
        return bool(
            type(owner_destination) is _RelayInvocationOwnerDestination
            and record is not None
            and record[1] is driver
            and record[2] is tools
            and _pair_record_is_canonical(pair_key, record)
            and _pair_publication_matches(record)
            and _live_pair_capabilities_match(record)
            and record[3]._owner_destination_matches(owner_destination)
        )
    except BaseException:
        return False


def _bind_concrete_invocation_owner_destination(
    driver: object,
    tools: object,
    owner_destination: object,
) -> bool:
    if type(owner_destination) is not _RelayInvocationOwnerDestination:
        return False
    pair_destination = _live_pair_destination(driver, tools)
    if pair_destination is None:
        return False
    with owner_destination._lock, pair_destination._effect_lock, _LOCK:
        record = _record_for_pair_key(getattr(driver, "_pair_key", None))
        return bool(
            record is not None
            and record[3] is pair_destination
            and record[1] is driver
            and record[2] is tools
            and _pair_record_is_canonical(getattr(driver, "_pair_key", None), record)
            and _pair_publication_matches(record)
            and _live_pair_capabilities_match(record)
            and pair_destination._bind_owner_destination(owner_destination)
        )


def _bind_concrete_invocation_owner_destinations(
    driver: object,
    tools: object,
    owner_destination: object,
    owner: object,
    owner_token: object,
    authority_destinations: tuple[object, ...],
    start_destinations: tuple[object, ...],
    stop_destinations: tuple[object, ...],
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_owner_values import RelayInvocationOwner

    if not (
        type(owner_destination) is _RelayInvocationOwnerDestination
        and type(owner) is RelayInvocationOwner
        and type(owner_token) is object
        and type(authority_destinations) is tuple
        and type(start_destinations) is tuple
        and type(stop_destinations) is tuple
        and len(authority_destinations)
        == len(start_destinations)
        == len(stop_destinations)
        == len(_ROLES)
        and all(
            type(destination) is _RelayChildAuthorityDestination
            and getattr(destination, "_role", None) == role
            for destination, role in zip(authority_destinations, _ROLES, strict=True)
        )
        and all(
            type(destination) is _RelayChildStartDestination
            and getattr(destination, "_role", None) == role
            and getattr(destination, "_owner_token", None) is owner_token
            for destination, role in zip(start_destinations, _ROLES, strict=True)
        )
        and all(
            type(destination) is _RelayChildStopDestination
            and getattr(destination, "_role", None) == role
            and getattr(destination, "_owner_token", None) is owner_token
            for destination, role in zip(stop_destinations, _ROLES, strict=True)
        )
    ):
        return False
    pair_destination = _live_pair_destination(driver, tools)
    if pair_destination is None:
        return False
    with owner_destination._lock, pair_destination._effect_lock, _LOCK:
        pair_key = getattr(driver, "_pair_key", None)
        record = _record_for_pair_key(pair_key)
        return bool(
            record is not None
            and record[3] is pair_destination
            and record[1] is driver
            and record[2] is tools
            and _pair_record_is_canonical(pair_key, record)
            and _pair_publication_matches(record)
            and _live_pair_capabilities_match(record)
            and pair_destination._bind_owner_graph(
                (
                    owner_destination,
                    owner,
                    owner_token,
                    *authority_destinations,
                    *start_destinations,
                    *stop_destinations,
                ),
                owner_token,
            )
        )


def _retire_concrete_invocation_pair(
    driver: object,
    tools: object,
    owner_destination: object | None = None,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_pair_retirement import (
        _retire_concrete_invocation_pair as retire,
    )

    return retire(driver, tools, owner_destination)


def _live_pair_destination(
    driver: object,
    tools: object,
) -> _RelayConcreteInvocationPairDestination | None:
    try:
        pair_key = getattr(driver, "_pair_key", None)
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            if not (
                record is not None
                and record[1] is driver
                and record[2] is tools
                and _pair_record_is_canonical(pair_key, record)
                and _pair_publication_matches(record)
                and _live_pair_capabilities_match(record)
            ):
                return None
            return record[3]
    except BaseException:
        return None


def _concrete_invocation_pair_is_absent(build: object) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
        _RelayLinuxExecutorBuiltEvidence,
    )

    if type(build) is not _RelayLinuxExecutorBuiltEvidence:
        return False
    with _LOCK:
        return not _PAIR_ENTRIES


def _concrete_invocation_pair_registries_are_empty() -> bool:
    with _LOCK:
        return not _PAIR_ENTRIES


def _pair_inputs_have_shape(values: dict[str, object]) -> bool:
    expected = {
        "build",
        "binding",
        "selection",
        "runtime_deadline",
        "runtime_timeout_seconds",
        "cleanup_timeout_seconds",
        "clock",
        "wait",
        "epoch_clock",
    }
    try:
        if set(values) != expected:
            return False
        build = values["build"]
        durations = (
            values["runtime_deadline"],
            values["runtime_timeout_seconds"],
            values["cleanup_timeout_seconds"],
        )
        return bool(
            _live_consumed_build_matches(build)
            and getattr(build, "binding", None) is values["binding"]
            and _is_concrete_invocation_selection(values["selection"])
            and all(
                type(value) is float and math.isfinite(value) and value > 0.0 for value in durations
            )
            and values["runtime_timeout_seconds"] <= _MAX_RUNTIME_SECONDS
            and values["cleanup_timeout_seconds"] <= _MAX_CLEANUP_SECONDS
            and all(callable(values[name]) for name in ("clock", "wait", "epoch_clock"))
        )
    except BaseException:
        return False


def _pair_record_matches(
    pair_key: object,
    record: tuple[object, ...],
    destination: _RelayConcreteInvocationPairDestination,
    values: dict[str, object],
) -> bool:
    try:
        return bool(
            _pair_record_is_canonical(pair_key, record)
            and record[3] is destination
            and destination._phase_matches("live")
            and record[0]._matches(destination=destination, **values)
            and _live_pair_capabilities_match(record)
        )
    except BaseException:
        return False


def _pair_record_is_canonical(pair_key: object, record: object) -> bool:
    try:
        if not (
            type(pair_key) is object
            and type(record) is tuple
            and len(record) == 5
            and type(record[0]) is _RelayConcreteInvocationGrant
            and type(record[1]) is RelayInvocationDriver
            and type(record[2]) is RelayInvocationTools
            and type(record[3]) is _RelayConcreteInvocationPairDestination
            and type(record[4]) is tuple
            and len(record[4]) == len(_ROLES)
            and record[0]._pair_key is pair_key
            and record[1]._pair_key is pair_key
            and record[2]._pair_key is pair_key
            and record[1]._adapter_seal is _CONCRETE_ADAPTER_SEAL
            and record[2]._adapter_seal is _CONCRETE_ADAPTER_SEAL
            and record[0]._destination is record[3]
            and _grant_matches_destination(record[0], record[3])
            and len(_PAIR_ENTRIES) == 1
            and _PAIR_ENTRIES.get(record[0]._build.key) is record
            and _live_consumed_build_matches(record[0]._build)
        ):
            return False
        return bool(
            all(
                type(entry) is tuple
                and len(entry) == 2
                and entry[0] == role
                and type(entry[1]) is object
                for entry, role in zip(record[4], _ROLES, strict=True)
            )
            and len({entry[1] for entry in record[4]}) == len(_ROLES)
        )
    except BaseException:
        return False


def _grant_matches_destination(
    grant: _RelayConcreteInvocationGrant,
    destination: _RelayConcreteInvocationPairDestination,
) -> bool:
    try:
        return grant._matches(
            build=destination._build,
            binding=destination._binding,
            selection=destination._selection,
            destination=destination,
            runtime_deadline=destination._runtime_deadline,
            runtime_timeout_seconds=destination._runtime_timeout_seconds,
            cleanup_timeout_seconds=destination._cleanup_timeout_seconds,
            clock=destination._clock,
            wait=destination._wait,
            epoch_clock=destination._epoch_clock,
        )
    except BaseException:
        return False


def _record_for_pair_key(pair_key: object) -> _PairRecord | None:
    if type(pair_key) is not object or len(_PAIR_ENTRIES) != 1:
        return None
    entry = next(iter(_PAIR_ENTRIES.values()))
    return (
        entry
        if _pair_record_has_shape(entry) and getattr(entry[0], "_pair_key", None) is pair_key
        else None
    )


def _pair_record_has_shape(entry: object) -> bool:
    return bool(
        type(entry) is tuple
        and len(entry) == 5
        and type(entry[0]) is _RelayConcreteInvocationGrant
        and type(entry[3]) is _RelayConcreteInvocationPairDestination
    )


def _store_pair_entry(
    key: object,
    entry: _RelayConcreteInvocationPairDestination | _PairRecord,
) -> None:
    _PAIR_ENTRIES[key] = entry


def _pop_pair_entry(key: object) -> None:
    _PAIR_ENTRIES.pop(key)


def _pair_publication_matches(record: _PairRecord) -> bool:
    try:
        published = record[3]._read()
        return bool(
            type(published) is tuple
            and len(published) == 3
            and all(published[index] is record[index] for index in range(3))
        )
    except BaseException:
        return False


__all__: list[str] = []

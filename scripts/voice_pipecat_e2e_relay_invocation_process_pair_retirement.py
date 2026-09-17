"""Lock-ordered retirement for one concrete invocation capability pair."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _RelayChildAuthorityDestination,
    _RelayChildStartDestination,
    _RelayChildStopDestination,
    _RelayInvocationOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _LOCK,
    _PAIR_ENTRIES,
    _ROLES,
    _live_pair_capabilities_match,
    _pair_publication_matches,
    _pair_record_is_canonical,
    _PairRecord,
    _pop_pair_entry,
    _record_for_pair_key,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_callback import (
    _retired_pair_matches,
    _scrub_pair_capabilities,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
    _RelayConcreteInvocationPairDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayStopRequest


def _retire_concrete_invocation_pair(
    driver: object,
    tools: object,
    owner_destination: object | None = None,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_owner_values import RelayInvocationOwner

    pair_key = getattr(driver, "_pair_key", None)
    if type(pair_key) is not object:
        return False
    with _LOCK:
        record = _record_for_pair_key(pair_key)
        if record is None:
            return _retired_pair_matches(driver, tools) and not _PAIR_ENTRIES
        if record[1] is not driver or record[2] is not tools:
            return False
        destination = record[3]
        tombstoned = destination._retirement_owner_matches(owner_destination)
        state = destination._read_retirement_state()
    if tombstoned:
        if owner_destination is not None and type(owner_destination) is not (
            _RelayInvocationOwnerDestination
        ):
            return False
        bound = owner_destination
        owner = None
    else:
        if not _retirement_state_has_shape(state):
            return False
        bound = state[1]
        graph = state[2]
        owner = graph[1] if type(graph) is tuple and len(graph) == 12 else None
        if graph is not None and type(owner) is not RelayInvocationOwner:
            return False
        if bound is None:
            if owner_destination is not None or owner is not None:
                return False
        elif type(bound) is _RelayInvocationOwnerDestination:
            if owner is not None and owner_destination is not bound:
                return False
            if owner_destination is not None and owner_destination is not bound:
                return False
        else:
            return False
    if type(bound) is _RelayInvocationOwnerDestination:
        if type(owner) is RelayInvocationOwner:
            if getattr(owner, "_construction_lock", None) is not bound._construction_lock:
                return False
            with bound._construction_lock, owner._operation_lock, bound._lock:
                return _retire_pair_serialized(
                    pair_key,
                    record,
                    destination,
                    driver,
                    tools,
                    owner_destination,
                    bound,
                    owner,
                )
        with bound._construction_lock, bound._lock:
            return _retire_pair_serialized(
                pair_key,
                record,
                destination,
                driver,
                tools,
                owner_destination,
                bound,
                None,
            )
    return _retire_pair_serialized(
        pair_key,
        record,
        destination,
        driver,
        tools,
        owner_destination,
        None,
        None,
    )


def _retire_pair_serialized(
    pair_key: object,
    record: _PairRecord,
    destination: _RelayConcreteInvocationPairDestination,
    driver: object,
    tools: object,
    owner_destination: object | None,
    locked_owner_destination: _RelayInvocationOwnerDestination | None,
    locked_owner: object | None,
) -> bool:
    expected = record[:3]
    with destination._effect_lock:
        with _LOCK:
            build_key = getattr(getattr(record[0], "_build", None), "key", None)
            if _PAIR_ENTRIES.get(build_key) is not record:
                return False
            if destination._retirement_owner_matches(owner_destination):
                return _scrub_and_pop_retired_pair(record, driver, tools)
            in_progress = destination._retirement_in_progress(expected)
            if not in_progress and not (
                _record_for_pair_key(pair_key) is record
                and _pair_record_is_canonical(pair_key, record)
                and _pair_publication_matches(record)
                and _live_pair_capabilities_match(record)
            ):
                return False
            state = destination._read_retirement_state()
        if locked_owner_destination is not None and not (
            locked_owner_destination._is_sealed_empty() or locked_owner_destination._seal_empty()
        ):
            return False
        if not _retirement_state_can_advance(
            pair_key,
            state,
            locked_owner_destination,
            locked_owner,
            driver,
            tools,
            allow_retiring=in_progress,
        ):
            return False
        if not in_progress:
            error: BaseException | None = None
            with _LOCK:
                current_state = destination._read_retirement_state()
                if not (
                    _PAIR_ENTRIES.get(build_key) is record
                    and _record_for_pair_key(pair_key) is record
                    and _pair_record_is_canonical(pair_key, record)
                    and _pair_publication_matches(record)
                    and _same_state(current_state, state)
                ):
                    return False
                try:
                    destination._begin_retirement(expected)
                except BaseException as escaped:
                    error = escaped
                in_progress = destination._retirement_in_progress(expected)
            if error is not None:
                raise error
            if not in_progress:
                return False
        if not _reconcile_and_seal_graph(pair_key, destination):
            return False
        error = None
        with _LOCK:
            if not (
                _PAIR_ENTRIES.get(build_key) is record
                and destination._retirement_in_progress(expected)
                and _retirement_internal_state_is_final(pair_key, destination)
            ):
                return False
            try:
                destination._clear(expected, owner_destination)
            except BaseException as escaped:
                error = escaped
            cleared = destination._retirement_owner_matches(owner_destination)
        if error is not None:
            raise error
        if not cleared:
            return False
        with _LOCK:
            return _scrub_and_pop_retired_pair(record, driver, tools)


def _retirement_state_can_advance(
    pair_key: object,
    state: tuple[object, ...],
    owner_destination: _RelayInvocationOwnerDestination | None,
    owner: object | None,
    driver: object,
    tools: object,
    *,
    allow_retiring: bool,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_owner_values import RelayInvocationOwner

    phases = {"live", "cleanup", "retiring"} if allow_retiring else {"live", "cleanup"}
    if not (
        _retirement_state_has_shape(state)
        and state[0] in phases
        and state[1] is owner_destination
        and state[8] is None
    ):
        return False
    phase, _bound, graph, owner_token, intended, preowned, stopped, stop_request = state[:8]
    if graph is None:
        return bool(
            phase in ({"live", "retiring"} if allow_retiring else {"live"})
            and owner is None
            and owner_token is None
            and not any(intended)
            and not any(preowned)
            and not any(stopped)
            and stop_request is None
            and (owner_destination is None or owner_destination._is_sealed_empty())
        )
    if not (
        phase in ({"cleanup", "retiring"} if allow_retiring else {"cleanup"})
        and type(owner_destination) is _RelayInvocationOwnerDestination
        and owner_destination._is_sealed_empty()
        and type(graph) is tuple
        and len(graph) == 12
        and graph[0] is owner_destination
        and type(owner) is RelayInvocationOwner
        and graph[1] is owner
        and type(owner_token) is object
        and graph[2] is owner_token
        and owner._cleanup_phase == "terminal"
        and owner._destination is owner_destination
        and owner._owner_token is owner_token
        and owner._driver is driver
        and owner._tools is tools
        and all(getattr(owner, f"_{role}", None) is None for role in _ROLES)
        and type(stop_request) is RelayStopRequest
        and stop_request._matches(pair_key)
        and all(not preowned[index] or intended[index] for index in range(len(_ROLES)))
        and all(not stopped[index] or preowned[index] for index in range(len(_ROLES)))
    ):
        return False
    authority_destinations = graph[3:6]
    start_destinations = graph[6:9]
    stop_destinations = graph[9:12]
    return bool(
        all(
            type(authority) is _RelayChildAuthorityDestination
            and getattr(authority, "_role", None) == role
            and authority._is_sealed_empty()
            for authority, role in zip(authority_destinations, _ROLES, strict=True)
        )
        and all(
            type(start) is _RelayChildStartDestination
            and getattr(start, "_role", None) == role
            and (start._is_sealed() or getattr(start, "_owner_token", None) is owner_token)
            for start, role in zip(start_destinations, _ROLES, strict=True)
        )
        and all(
            type(stop) is _RelayChildStopDestination
            and getattr(stop, "_role", None) == role
            and (stop._is_sealed() or getattr(stop, "_owner_token", None) is owner_token)
            for stop, role in zip(stop_destinations, _ROLES, strict=True)
        )
    )


def _reconcile_and_seal_graph(
    pair_key: object,
    destination: _RelayConcreteInvocationPairDestination,
) -> bool:
    state = destination._read_retirement_state()
    if not _retirement_state_has_shape(state) or state[0] != "retiring":
        return False
    graph, owner_token, intended, preowned, stopped = state[2:7]
    if graph is None:
        return owner_token is None
    if not (type(graph) is tuple and len(graph) == 12 and type(owner_token) is object):
        return False
    for authority in graph[3:6]:
        if not authority._is_sealed_empty():
            return False
    for start, role in zip(graph[6:9], _ROLES, strict=True):
        if not start._is_sealed() and not start._seal(owner_token, role):
            return False
    for index, (stop, role) in enumerate(zip(graph[9:12], _ROLES, strict=True)):
        if stop._is_sealed():
            if not (
                (not intended[index] and not preowned[index] and not stopped[index])
                or (intended[index] and not preowned[index] and not stopped[index])
                or (intended[index] and preowned[index] and stopped[index])
            ):
                return False
            continue
        try:
            stop._read(owner_token, role)
            receipt = True
        except TypeError:
            receipt = False
        if not intended[index]:
            if receipt or preowned[index] or stopped[index]:
                return False
        elif receipt:
            if not preowned[index] and not destination._mark_role_preowned(index):
                return False
            if not destination._mark_role_stopped(index):
                return False
        elif preowned[index] or stopped[index]:
            return False
        if not stop._seal(owner_token, role):
            return False
    return _retirement_internal_state_is_final(pair_key, destination)


def _retirement_internal_state_is_final(
    pair_key: object,
    destination: _RelayConcreteInvocationPairDestination,
) -> bool:
    state = destination._read_retirement_state()
    if not (_retirement_state_has_shape(state) and state[0] == "retiring" and state[8] is None):
        return False
    graph, owner_token, intended, preowned, stopped, stop_request = state[2:8]
    if graph is None:
        return bool(
            owner_token is None
            and not any(intended)
            and not any(preowned)
            and not any(stopped)
            and stop_request is None
        )
    return bool(
        type(owner_token) is object
        and type(stop_request) is RelayStopRequest
        and stop_request._matches(pair_key)
        and all(
            (not intended[index] and not preowned[index] and not stopped[index])
            or (intended[index] and not preowned[index] and not stopped[index])
            or (intended[index] and preowned[index] and stopped[index])
            for index in range(len(_ROLES))
        )
    )


def _retirement_state_has_shape(state: object) -> bool:
    return bool(
        type(state) is tuple
        and len(state) == 9
        and type(state[0]) is str
        and type(state[4]) is tuple
        and type(state[5]) is tuple
        and type(state[6]) is tuple
        and len(state[4]) == len(state[5]) == len(state[6]) == len(_ROLES)
        and all(type(flag) is bool for flag in state[4] + state[5] + state[6])
    )


def _same_state(first: tuple[object, ...], second: tuple[object, ...]) -> bool:
    return bool(
        len(first) == len(second)
        and all(first[index] is second[index] for index in range(len(first)))
    )


def _scrub_and_pop_retired_pair(
    record: _PairRecord,
    driver: object,
    tools: object,
) -> bool:
    error: BaseException | None = None
    try:
        _scrub_pair_capabilities(driver, tools)
    except BaseException as escaped:
        error = escaped
    scrubbed = _retired_pair_matches(driver, tools)
    if error is not None:
        raise error
    if not scrubbed:
        return False
    build_key = getattr(getattr(record[0], "_build", None), "key", None)
    try:
        if _PAIR_ENTRIES.get(build_key) is not record:
            return False
        _pop_pair_entry(build_key)
    except BaseException as escaped:
        error = escaped
    complete = not _PAIR_ENTRIES and _retired_pair_matches(driver, tools)
    if error is not None:
        raise error
    return complete


__all__: list[str] = []

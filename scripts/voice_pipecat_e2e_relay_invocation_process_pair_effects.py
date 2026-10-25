"""Serialized outward effects for one concrete invocation capability pair."""

from __future__ import annotations

import math

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _RelayChildAuthorityDestination,
    _RelayChildStopDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _LOCK,
    _ROLES,
    _live_pair_capabilities_match,
    _pair_publication_matches,
    _pair_record_is_canonical,
    _record_for_pair_key,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
    _RelayConcreteInvocationGrant,
    _RelayConcreteInvocationPairDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_state import (
    _stop_unstarted_process_role,
    _transactional_record_process_owner_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    RelayInvocationError,
    RelayStopRequest,
)

_FAILURE = "Relay concrete invocation pair is invalid"


def _inert_preown(
    pair_key: object,
    role: str,
    destination: _RelayChildAuthorityDestination,
) -> None:
    role_index = _ROLES.index(role) if role in _ROLES else -1
    if type(destination) is not _RelayChildAuthorityDestination:
        raise RelayInvocationError(_FAILURE)
    with _LOCK:
        record = _record_for_pair_key(pair_key)
        pair_destination = record[3] if record is not None else None
    if type(pair_destination) is not _RelayConcreteInvocationPairDestination:
        raise RelayInvocationError(_FAILURE)
    with pair_destination._effect_lock:
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            authority = (
                next((entry[1] for entry in record[4] if entry[0] == role), None)
                if (
                    record is not None
                    and record[3] is pair_destination
                    and _pair_record_is_canonical(pair_key, record)
                    and _pair_publication_matches(record)
                    and _live_pair_capabilities_match(record)
                    and _transactional_record_process_owner_matches(pair_key, record)
                    and pair_destination._phase_matches("live")
                    and pair_destination._owner_graph_role_matches(
                        role_index,
                        authority_destination=destination,
                    )
                    and pair_destination._intend_role_preown(role_index)
                )
                else None
            )
        if authority is None:
            raise RelayInvocationError(_FAILURE)
        error: BaseException | None = None
        try:
            destination.publish(authority)
        except BaseException as escaped:
            error = escaped
        try:
            published = destination._peek() is authority
        except BaseException as escaped:
            if error is None:
                error = escaped
            published = False
        marked = False
        if published:
            try:
                with _LOCK:
                    current = _record_for_pair_key(pair_key)
                    marked = bool(
                        current is record
                        and current[3] is pair_destination
                        and _pair_record_is_canonical(pair_key, current)
                        and _pair_publication_matches(current)
                        and _live_pair_capabilities_match(current)
                        and _transactional_record_process_owner_matches(pair_key, current)
                        and pair_destination._phase_matches("live")
                        and pair_destination._owner_graph_role_matches(
                            role_index,
                            authority_destination=destination,
                        )
                        and pair_destination._mark_role_preowned(role_index)
                    )
            except BaseException as escaped:
                if error is None:
                    error = escaped
        if error is not None:
            raise error
        if not marked:
            raise RelayInvocationError(_FAILURE)


def _inert_forward(
    pair_key: object,
    authority: object,
    _request: object,
    _destination: object,
) -> None:
    if _role_for_authority(pair_key, authority) is None:
        raise RelayInvocationError(_FAILURE)
    raise RelayInvocationError("Relay concrete process adapter is not enabled")


def _inert_stop(
    pair_key: object,
    authority: object,
    request: RelayStopRequest,
    destination: _RelayChildStopDestination,
) -> None:
    with _LOCK:
        record = _record_for_pair_key(pair_key)
        pair_destination = record[3] if record is not None else None
    if type(pair_destination) is not _RelayConcreteInvocationPairDestination:
        raise RelayInvocationError(_FAILURE)
    with pair_destination._effect_lock:
        with _LOCK:
            record = _record_for_pair_key(pair_key)
            canonical = bool(
                record is not None
                and record[3] is pair_destination
                and _pair_record_is_canonical(pair_key, record)
                and _pair_publication_matches(record)
                and _live_pair_capabilities_match(record)
                and _transactional_record_process_owner_matches(pair_key, record)
            )
            role = (
                next((entry[0] for entry in record[4] if entry[1] is authority), None)
                if canonical
                else None
            )
            grant = record[0] if canonical else None
            role_index = _ROLES.index(role) if role in _ROLES else -1
            request_bound = bool(
                canonical
                and pair_destination._phase_matches("cleanup")
                and pair_destination._stop_request_matches(request)
                and pair_destination._owner_graph_role_matches(
                    role_index,
                    stop_destination=destination,
                )
            )
            owner_token = pair_destination._read_owner_token() if canonical else None
        if (
            role is None
            or type(request) is not RelayStopRequest
            or not request._matches(pair_key)
            or type(destination) is not _RelayChildStopDestination
            or type(grant) is not _RelayConcreteInvocationGrant
            or type(owner_token) is not object
            or not request_bound
        ):
            raise RelayInvocationError(_FAILURE)
        _reconcile_requested_preownership(
            pair_key,
            record,
            pair_destination,
            authority,
            request,
            destination,
            role,
            role_index,
        )
        process_destination = pair_destination._read_process_owner_destination()
        process_roles = record[4]
        suffix = _uncommitted_absent_suffix(
            pair_destination,
            process_destination,
            grant,
            process_roles,
            request,
            role_index,
        )
        stop_committed = _stop_unstarted_process_role(
            process_destination,
            grant,
            process_roles,
            authority,
            observe_only=True,
        )
        if suffix or not stop_committed:
            sampled = grant._clock()
            if (
                type(sampled) is not float
                or not math.isfinite(sampled)
                or sampled >= request.absolute_deadline
            ):
                _revalidate_stop(
                    pair_key,
                    record,
                    pair_destination,
                    authority,
                    request,
                    destination,
                    role,
                    role_index,
                )
                suffix = _uncommitted_absent_suffix(
                    pair_destination,
                    process_destination,
                    grant,
                    process_roles,
                    request,
                    role_index,
                )
                stop_committed = _stop_unstarted_process_role(
                    process_destination,
                    grant,
                    process_roles,
                    authority,
                    observe_only=True,
                )
                if suffix or not stop_committed:
                    raise RelayInvocationError(_FAILURE)
        _revalidate_stop(
            pair_key,
            record,
            pair_destination,
            authority,
            request,
            destination,
            role,
            role_index,
        )
        suffix = _uncommitted_absent_suffix(
            pair_destination,
            process_destination,
            grant,
            process_roles,
            request,
            role_index,
        )
        for later_authority in suffix:
            if not _stop_unstarted_process_role(
                process_destination,
                grant,
                process_roles,
                later_authority,
            ):
                raise RelayInvocationError(_FAILURE)
        if not _stop_unstarted_process_role(
            process_destination,
            grant,
            process_roles,
            authority,
        ):
            raise RelayInvocationError(_FAILURE)
        error: BaseException | None = None
        try:
            destination.publish(True)
        except BaseException as escaped:
            error = escaped
        try:
            destination._read(owner_token, role)
            stopped = True
        except TypeError:
            stopped = False
        except BaseException as escaped:
            if error is None:
                error = escaped
            stopped = False
        marked = False
        if stopped:
            try:
                with _LOCK:
                    current = _record_for_pair_key(pair_key)
                    marked = bool(
                        current is record
                        and current[3] is pair_destination
                        and _pair_record_is_canonical(pair_key, current)
                        and _pair_publication_matches(current)
                        and _live_pair_capabilities_match(current)
                        and _transactional_record_process_owner_matches(pair_key, current)
                        and pair_destination._phase_matches("cleanup")
                        and pair_destination._stop_request_matches(request)
                        and pair_destination._owner_graph_role_matches(
                            role_index,
                            stop_destination=destination,
                        )
                        and pair_destination._mark_role_stopped(role_index)
                    )
            except BaseException as escaped:
                if error is None:
                    error = escaped
        if error is not None:
            raise error
        if not marked:
            raise RelayInvocationError(_FAILURE)


def _reconcile_requested_preownership(
    pair_key: object,
    record: tuple[object, ...],
    pair_destination: _RelayConcreteInvocationPairDestination,
    authority: object,
    request: RelayStopRequest,
    stop_destination: _RelayChildStopDestination,
    role: str,
    role_index: int,
) -> None:
    state = pair_destination._read_retirement_state()
    graph = state[2] if type(state) is tuple and len(state) == 9 else None
    authority_destination = (
        graph[3 + role_index]
        if type(graph) is tuple and len(graph) == 12 and 0 <= role_index < len(_ROLES)
        else None
    )
    if type(authority_destination) is not _RelayChildAuthorityDestination:
        raise RelayInvocationError(_FAILURE)
    try:
        published = authority_destination._peek() is authority
    except BaseException:
        raise
    if not published:
        raise RelayInvocationError(_FAILURE)
    intended, preowned = state[4:6]
    if not (
        state[0] == "cleanup"
        and state[7] is request
        and type(intended) is tuple
        and type(preowned) is tuple
        and len(intended) == len(preowned) == len(_ROLES)
        and intended[role_index]
    ):
        raise RelayInvocationError(_FAILURE)
    if preowned[role_index]:
        return
    error: BaseException | None = None
    marked = False
    try:
        with _LOCK:
            current = _record_for_pair_key(pair_key)
            current_state = pair_destination._read_retirement_state()
            marked = bool(
                current is record
                and current[3] is pair_destination
                and _pair_record_is_canonical(pair_key, current)
                and _pair_publication_matches(current)
                and _live_pair_capabilities_match(current)
                and _transactional_record_process_owner_matches(pair_key, current)
                and _same_state(current_state, state)
                and pair_destination._owner_graph_role_matches(
                    role_index,
                    authority_destination=authority_destination,
                    stop_destination=stop_destination,
                )
                and pair_destination._mark_role_preowned(role_index)
            )
    except BaseException as escaped:
        error = escaped
    try:
        current_state = pair_destination._read_retirement_state()
    except BaseException as escaped:
        if error is None:
            error = escaped
        current_state = None
    reconciled = bool(
        type(current_state) is tuple
        and len(current_state) == 9
        and current_state[0] == "cleanup"
        and current_state[7] is request
        and type(current_state[4]) is tuple
        and type(current_state[5]) is tuple
        and len(current_state[4]) == len(current_state[5]) == len(_ROLES)
        and type(current_state[4][role_index]) is bool
        and type(current_state[5][role_index]) is bool
        and current_state[4][role_index]
        and current_state[5][role_index]
    )
    if error is not None:
        raise error
    if not (marked and reconciled):
        raise RelayInvocationError(_FAILURE)


def _uncommitted_absent_suffix(
    pair_destination: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    grant: _RelayConcreteInvocationGrant,
    process_roles: tuple[tuple[str, object], ...],
    request: RelayStopRequest,
    role_index: int,
) -> tuple[object, ...]:
    state = pair_destination._read_retirement_state()
    if not (
        type(state) is tuple
        and len(state) == 9
        and state[0] == "cleanup"
        and state[7] is request
        and type(state[2]) is tuple
        and len(state[2]) == 12
        and type(state[4]) is tuple
        and type(state[5]) is tuple
        and type(state[6]) is tuple
        and len(state[4]) == len(state[5]) == len(state[6]) == len(_ROLES)
        and type(process_roles) is tuple
        and len(process_roles) == len(_ROLES)
    ):
        raise RelayInvocationError(_FAILURE)
    graph, intended, preowned, stopped = state[2], state[4], state[5], state[6]
    pending: list[object] = []
    for later_index in reversed(range(role_index + 1, len(_ROLES))):
        entry = process_roles[later_index]
        generic_destination = graph[3 + later_index]
        if not (
            type(entry) is tuple
            and len(entry) == 2
            and entry[0] == _ROLES[later_index]
            and type(generic_destination) is _RelayChildAuthorityDestination
            and type(intended[later_index]) is bool
            and type(preowned[later_index]) is bool
            and type(stopped[later_index]) is bool
            and (not preowned[later_index] or intended[later_index])
            and (not stopped[later_index] or preowned[later_index])
        ):
            raise RelayInvocationError(_FAILURE)
        process_stopped = _stop_unstarted_process_role(
            process_destination,
            grant,
            process_roles,
            entry[1],
            observe_only=True,
        )
        if process_stopped:
            continue
        if (
            preowned[later_index]
            or stopped[later_index]
            or not generic_destination._is_sealed_empty()
        ):
            raise RelayInvocationError(_FAILURE)
        pending.append(entry[1])
    return tuple(pending)


def _same_state(first: tuple[object, ...], second: tuple[object, ...]) -> bool:
    return bool(
        len(first) == len(second)
        and all(first[index] is second[index] for index in range(len(first)))
    )


def _revalidate_stop(
    pair_key: object,
    record: tuple[object, ...],
    pair_destination: _RelayConcreteInvocationPairDestination,
    authority: object,
    request: RelayStopRequest,
    destination: _RelayChildStopDestination,
    role: str,
    role_index: int,
) -> None:
    with _LOCK:
        current = _record_for_pair_key(pair_key)
        if (
            current is not record
            or current[3] is not pair_destination
            or not _pair_record_is_canonical(pair_key, current)
            or not _pair_publication_matches(current)
            or not _live_pair_capabilities_match(current)
            or not _transactional_record_process_owner_matches(pair_key, current)
            or not pair_destination._phase_matches("cleanup")
            or not pair_destination._stop_request_matches(request)
            or next((entry[0] for entry in current[4] if entry[1] is authority), None) != role
            or not pair_destination._owner_graph_role_matches(
                role_index,
                stop_destination=destination,
            )
        ):
            raise RelayInvocationError(_FAILURE)


def _role_for_authority(pair_key: object, authority: object) -> str | None:
    with _LOCK:
        record = _record_for_pair_key(pair_key)
        if (
            record is None
            or not _pair_record_is_canonical(pair_key, record)
            or not _pair_publication_matches(record)
            or not _live_pair_capabilities_match(record)
            or not _transactional_record_process_owner_matches(pair_key, record)
            or not record[3]._phase_matches("live")
        ):
            return None
        role = next((entry[0] for entry in record[4] if entry[1] is authority), None)
        role_index = _ROLES.index(role) if role in _ROLES else -1
        return role if record[3]._owner_graph_role_matches(role_index) else None


__all__: list[str] = []

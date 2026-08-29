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
            error = error or escaped
            published = False
        marked = False
        if published:
            with _LOCK:
                current = _record_for_pair_key(pair_key)
                marked = bool(
                    current is record
                    and current[3] is pair_destination
                    and _pair_record_is_canonical(pair_key, current)
                    and _pair_publication_matches(current)
                    and _live_pair_capabilities_match(current)
                    and pair_destination._phase_matches("live")
                    and pair_destination._owner_graph_role_matches(
                        role_index,
                        authority_destination=destination,
                    )
                    and pair_destination._mark_role_preowned(role_index)
                )
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
        sampled = grant._clock()
        if (
            type(sampled) is not float
            or not math.isfinite(sampled)
            or sampled >= request.absolute_deadline
        ):
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
            error = error or escaped
            stopped = False
        marked = False
        if stopped:
            with _LOCK:
                current = _record_for_pair_key(pair_key)
                marked = bool(
                    current is record
                    and current[3] is pair_destination
                    and _pair_record_is_canonical(pair_key, current)
                    and _pair_publication_matches(current)
                    and _live_pair_capabilities_match(current)
                    and pair_destination._phase_matches("cleanup")
                    and pair_destination._stop_request_matches(request)
                    and pair_destination._owner_graph_role_matches(
                        role_index,
                        stop_destination=destination,
                    )
                    and pair_destination._mark_role_stopped(role_index)
                )
        if error is not None:
            raise error
        if not marked:
            raise RelayInvocationError(_FAILURE)


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
            or not record[3]._phase_matches("live")
        ):
            return None
        role = next((entry[0] for entry in record[4] if entry[1] is authority), None)
        role_index = _ROLES.index(role) if role in _ROLES else -1
        return role if record[3]._owner_graph_role_matches(role_index) else None


__all__: list[str] = []

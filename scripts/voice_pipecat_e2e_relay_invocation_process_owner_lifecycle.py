"""Admission, replay, and retirement for one exact concrete process owner."""

from __future__ import annotations

import scripts.voice_pipecat_e2e_relay_invocation_process_state as process_state
from scripts.voice_pipecat_e2e_relay_invocation_process_owner_admission import (
    _read_live_process_owner_admission,
    _read_retirement_process_owner_admission,
)


def _resolve_or_preown_process_owner(
    destination: process_state._RelayProcessOwnerDestination,
    grant: object,
    roles: tuple[tuple[str, process_state._RelayProcessRoleAuthority], ...],
) -> process_state._RelayProcessOwner:
    claim = process_state._authorized_process_claim(destination, grant, roles)
    pair_key = getattr(grant, "_pair_key", None)
    parent = getattr(grant, "_destination", None)
    if type(claim) is not object or not destination._matches_roles(pair_key, roles):
        raise TypeError(process_state._FAILURE)
    owner = _read_live_process_owner_admission(
        parent,
        destination,
        claim,
        grant,
        roles,
    )
    with process_state._LOCK:
        registered = process_state._PROCESS_OWNERS.get(pair_key)
        published = destination._read_owner()
        if owner is None:
            owner = _read_live_process_owner_admission(
                parent,
                destination,
                claim,
                grant,
                roles,
            )
        if owner is None:
            if (
                process_state._PROCESS_OWNERS
                or registered is not None
                or published is not None
                or destination._owner_was_published()
            ):
                raise TypeError(process_state._FAILURE)
            owner = parent._resolve_or_admit_process_owner(
                destination,
                claim,
                grant,
                roles,
                allow_dead_replacement=True,
            )
        if (
            type(owner) is not process_state._RelayProcessOwner
            or (process_state._PROCESS_OWNERS and registered is None)
            or (registered is not None and registered is not owner)
            or (published is not None and published is not owner)
            or (published is None and destination._owner_was_published())
        ):
            raise TypeError(process_state._FAILURE)
        error: BaseException | None = None
        if registered is None:
            try:
                process_state._store_process_owner(pair_key, owner)
            except BaseException as escaped:
                error = escaped
            registered = process_state._PROCESS_OWNERS.get(pair_key)
        if registered is owner and published is None:
            try:
                destination._publish_owner(owner, grant)
            except BaseException as escaped:
                if error is None:
                    error = escaped
            try:
                published = destination._read_owner()
            except BaseException as escaped:
                if error is None:
                    error = escaped
                published = None
        try:
            valid = bool(
                process_state._PROCESS_OWNERS.get(pair_key) is owner
                and len(process_state._PROCESS_OWNERS) == 1
                and published is owner
                and owner._matches(destination, grant, pair_key, roles)
            )
        except BaseException as escaped:
            if error is None:
                error = escaped
            valid = False
        if error is not None:
            raise error
        if not valid:
            raise TypeError(process_state._FAILURE)
        return owner


def _process_owner_matches(destination: object, grant: object, roles: object) -> bool:
    try:
        return _transactional_process_owner_matches(destination, grant, roles)
    except BaseException:
        return False


def _transactional_process_owner_matches(
    destination: object,
    grant: object,
    roles: object,
) -> bool:
    try:
        claim = process_state._authorized_process_claim(destination, grant, roles)
        pair_key = getattr(grant, "_pair_key", None)
        parent = getattr(grant, "_destination", None)
        if (
            type(claim) is not object
            or type(destination) is not process_state._RelayProcessOwnerDestination
        ):
            return False
        admitted = _read_live_process_owner_admission(
            parent,
            destination,
            claim,
            grant,
            roles,
        )
        with process_state._LOCK:
            owner = process_state._PROCESS_OWNERS.get(pair_key)
            return bool(
                len(process_state._PROCESS_OWNERS) == 1
                and type(owner) is process_state._RelayProcessOwner
                and owner is admitted
                and destination._read_owner() is owner
                and owner._matches(destination, grant, pair_key, roles)
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _retire_childless_process_owner(
    destination: object,
    grant: object,
    roles: object,
) -> bool:
    claim = process_state._authorized_process_claim(
        destination,
        grant,
        roles,
        allow_terminal=True,
    )
    pair_key = getattr(grant, "_pair_key", None)
    parent = getattr(grant, "_destination", None)
    if (
        type(claim) is not object
        or type(destination) is not process_state._RelayProcessOwnerDestination
    ):
        return False
    admitted = _read_retirement_process_owner_admission(
        parent,
        destination,
        claim,
        grant,
        roles,
    )
    with process_state._LOCK:
        owner = destination._read_owner()
        existing = process_state._PROCESS_OWNERS.get(pair_key)
        if owner is None:
            if (existing is None and process_state._PROCESS_OWNERS) or (
                existing is not None
                and not (
                    len(process_state._PROCESS_OWNERS) == 1
                    and existing is admitted
                    and type(existing) is process_state._RelayProcessOwner
                )
            ):
                return False
            error: BaseException | None = None
            try:
                destination._seal_unpublished(claim, grant, pair_key, roles)
            except BaseException as escaped:
                error = escaped
            try:
                sealed = destination._is_sealed_empty()
            except BaseException as escaped:
                if error is None:
                    error = escaped
                sealed = False
            if sealed and existing is not None:
                try:
                    process_state._pop_process_owner(pair_key)
                except BaseException as escaped:
                    if error is None:
                        error = escaped
            try:
                complete, observation_error = _observe_total_absence(destination)
            except BaseException as escaped:
                complete, observation_error = False, escaped
            if error is None:
                error = observation_error
            if error is not None:
                raise error
            return complete
        if not (
            existing is owner and owner is admitted and len(process_state._PROCESS_OWNERS) == 1
        ):
            return False
        if not destination._retirement_matches(claim, owner, grant, pair_key, roles) and not (
            owner._matches(destination, grant, pair_key, roles)
            and destination._begin_retirement(claim, owner, grant, pair_key, roles)
        ):
            return False
        error = None
        try:
            destination._seal_retired(claim, owner, grant, pair_key, roles)
        except BaseException as escaped:
            error = escaped
        try:
            sealed = destination._is_sealed_empty()
        except BaseException as escaped:
            if error is None:
                error = escaped
            sealed = False
        if sealed and process_state._PROCESS_OWNERS.get(pair_key) is owner:
            try:
                process_state._pop_process_owner(pair_key)
            except BaseException as escaped:
                if error is None:
                    error = escaped
        try:
            complete, observation_error = _observe_total_absence(destination)
        except BaseException as escaped:
            complete, observation_error = False, escaped
        if error is None:
            error = observation_error
        if error is not None:
            raise error
        return complete


def _observe_total_absence(destination: object) -> tuple[bool, BaseException | None]:
    try:
        return bool(not process_state._PROCESS_OWNERS and destination._is_sealed_empty()), None
    except BaseException as error:
        return False, error


__all__: list[str] = []

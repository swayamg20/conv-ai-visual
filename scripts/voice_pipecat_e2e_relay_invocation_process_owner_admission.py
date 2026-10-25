"""Exact parent-owned birth identity for one concrete invocation process owner."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_invocation_process_grant_admission_values import (
    _process_grant_admission_has_shape,
    _terminal_process_grant_admission_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
    _FAILURE,
    _RelayConcreteInvocationPairDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_state import (
    _OWNER_TOKEN,
    _RelayProcessOwner,
)


def _resolve_or_admit_process_owner(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
    *,
    allow_dead_replacement: bool,
) -> _RelayProcessOwner:
    with parent._lock:
        pair_key = getattr(grant, "_pair_key", None)
        admission = parent._process_grant_admission
        if not (
            type(allow_dead_replacement) is bool
            and allow_dead_replacement
            and parent._phase == "live"
            and _process_grant_admission_has_shape(
                admission,
                parent,
                process_destination,
                claim,
                pair_key,
                roles,
            )
            and admission[4] is grant
        ):
            raise TypeError(_FAILURE)
        reference = admission[5]
        owner = reference() if type(reference) is weakref.ReferenceType else None
        if owner is None:
            owner = _RelayProcessOwner(
                _OWNER_TOKEN,
                destination=process_destination,
                grant=grant,
                roles=roles,
            )
            reference = weakref.ref(owner)
            admission = (*admission[:5], reference)
            parent._process_grant_admission = admission
        if not _live_process_owner_admission_matches_locked(
            parent,
            process_destination,
            claim,
            grant,
            roles,
            owner,
        ):
            raise TypeError(_FAILURE)
        return owner


def _read_live_process_owner_admission(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
) -> _RelayProcessOwner | None:
    with parent._lock:
        return _read_process_owner_admission_locked(
            parent,
            process_destination,
            claim,
            grant,
            roles,
            phases={"live", "cleanup", "retiring"},
            allow_terminal=False,
        )


def _observe_live_process_owner_admission(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
) -> _RelayProcessOwner | None:
    try:
        return _read_live_process_owner_admission(
            parent,
            process_destination,
            claim,
            grant,
            roles,
        )
    except BaseException:
        return None


def _read_retirement_process_owner_admission(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
) -> _RelayProcessOwner | None:
    with parent._lock:
        return _read_process_owner_admission_locked(
            parent,
            process_destination,
            claim,
            grant,
            roles,
            phases={"live", "cleanup", "retiring", "cleared"},
            allow_terminal=True,
        )


def _read_process_owner_admission_locked(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
    *,
    phases: set[str],
    allow_terminal: bool,
) -> _RelayProcessOwner | None:
    if parent._phase not in phases:
        return None
    admission = parent._process_grant_admission
    pair_key = getattr(grant, "_pair_key", None)
    live = bool(
        _process_grant_admission_has_shape(
            admission,
            parent,
            process_destination,
            claim,
            pair_key,
            roles,
        )
        and admission[4] is grant
    )
    terminal = bool(
        allow_terminal
        and _terminal_process_grant_admission_matches(
            admission,
            parent,
            process_destination,
            claim,
            grant,
            roles,
        )
    )
    if not (live or terminal):
        return None
    reference = admission[5]
    owner = reference() if type(reference) is weakref.ReferenceType else None
    if type(owner) is not _RelayProcessOwner:
        return None
    return owner


def _live_process_owner_admission_matches_locked(
    parent: _RelayConcreteInvocationPairDestination,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
    owner: object,
) -> bool:
    return (
        _read_process_owner_admission_locked(
            parent,
            process_destination,
            claim,
            grant,
            roles,
            phases={"live"},
            allow_terminal=False,
        )
        is owner
    )


__all__: list[str] = []

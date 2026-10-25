"""Immutable snapshots and identity checks for concrete process ownership."""

from __future__ import annotations

from typing import NamedTuple

from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _RelayProcessRoleAuthority,
)

_ROLES = ("app", "web", "browser")
_PREOWNED_PHASES = ("preowned", "preowned", "preowned")
_RETIRED_PHASES = ("retired", "retired", "retired")
_OWNED_ROLE_PHASES = frozenset(
    {
        _PREOWNED_PHASES,
        ("preowned", "preowned", "stopped"),
        ("preowned", "stopped", "stopped"),
        ("stopped", "stopped", "stopped"),
    }
)


class _ProcessSnapshot(NamedTuple):
    phase: str
    claim: object
    pair_key: object | None
    roles: tuple[tuple[str, _RelayProcessRoleAuthority], ...]
    grant: object | None
    owner: object | None
    role_phases: tuple[str, ...]


def _empty_snapshot(claim: object) -> _ProcessSnapshot:
    return _ProcessSnapshot("empty", claim, None, (), None, None, ())


def _sealed_snapshot(claim: object) -> _ProcessSnapshot:
    return _ProcessSnapshot("sealed", claim, None, (), None, None, _RETIRED_PHASES)


def _snapshot_roles_have_shape(destination: object, state: _ProcessSnapshot) -> bool:
    return bool(
        type(state) is _ProcessSnapshot
        and state.phase in {"preowned", "owned", "retiring"}
        and type(state.pair_key) is object
        and _roles_bear_claim(destination, state.claim, state.roles)
        and (
            (state.phase == "preowned" and state.role_phases == _PREOWNED_PHASES)
            or (state.phase == "owned" and state.role_phases in _OWNED_ROLE_PHASES)
            or (state.phase == "retiring" and state.role_phases == _RETIRED_PHASES)
        )
    )


def _sealed_snapshot_has_shape(state: object) -> bool:
    return bool(
        type(state) is _ProcessSnapshot
        and state.phase == "sealed"
        and type(state.claim) is object
        and state.pair_key is None
        and not state.roles
        and state.grant is None
        and state.owner is None
        and state.role_phases == _RETIRED_PHASES
    )


def _roles_have_shape(pair_key: object, roles: object) -> bool:
    try:
        return bool(
            type(pair_key) is object
            and type(roles) is tuple
            and len(roles) == len(_ROLES)
            and all(
                type(entry) is tuple
                and len(entry) == 2
                and entry[0] == role
                and type(entry[1]) is _RelayProcessRoleAuthority
                and entry[1]._matches(pair_key, role)
                for entry, role in zip(roles, _ROLES, strict=True)
            )
            and len({entry[1] for entry in roles}) == len(_ROLES)
        )
    except BaseException:
        return False


def _roles_bear_claim(destination: object, claim: object, roles: object) -> bool:
    try:
        return bool(
            type(claim) is object
            and type(roles) is tuple
            and len(roles) == len(_ROLES)
            and all(
                type(entry) is tuple
                and len(entry) == 2
                and entry[0] == role
                and type(entry[1]) is _RelayProcessRoleAuthority
                and entry[1]._destination is destination
                and entry[1]._claim is claim
                and entry[1]._role_name == role
                for entry, role in zip(roles, _ROLES, strict=True)
            )
            and len({entry[1] for entry in roles}) == len(_ROLES)
        )
    except BaseException:
        return False


def _same_roles(first: object, second: object) -> bool:
    try:
        return bool(
            type(first) is tuple
            and type(second) is tuple
            and len(first) == len(second) == len(_ROLES)
            and all(
                first[index][0] == second[index][0] and first[index][1] is second[index][1]
                for index in range(len(_ROLES))
            )
        )
    except BaseException:
        return False


def _retiring_snapshot_matches(
    destination: object,
    state: _ProcessSnapshot,
    claim: object,
    owner: object,
    grant: object,
    pair_key: object,
    roles: object,
) -> bool:
    return bool(
        _snapshot_roles_have_shape(destination, state)
        and state.phase == "retiring"
        and state.claim is claim
        and state.owner is owner
        and state.grant is grant
        and state.pair_key is pair_key
        and _same_roles(state.roles, roles)
        and state.role_phases == _RETIRED_PHASES
    )


def _authority_index(pair_key: object, roles: object, authority: object) -> int:
    if not _roles_have_shape(pair_key, roles):
        return -1
    return next(
        (index for index, (_role, value) in enumerate(roles) if value is authority),
        -1,
    )


__all__: list[str] = []

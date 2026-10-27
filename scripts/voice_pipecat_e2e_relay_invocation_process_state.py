"""Cap-one ownership state for concrete app, web, and browser processes."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_invocation_process_state_values import (
    _RETIRED_PHASES,
    _ROLES,
    _authority_index,
    _empty_snapshot,
    _ProcessSnapshot,
    _retiring_snapshot_matches,
    _roles_bear_claim,
    _same_roles,
    _sealed_snapshot,
    _sealed_snapshot_has_shape,
    _snapshot_roles_have_shape,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _new_process_role_authority,
    _RelayProcessRoleAuthority,
)

_DESTINATION_TOKEN = object()
_OWNER_TOKEN = object()
_FAILURE = "Relay concrete process owner is invalid"
_LOCK = threading.RLock()


class _RelayProcessOwner:
    """Immutable view of one destination-owned app/web/browser process graph."""

    __slots__ = ("__weakref__", "_claim", "_destination")

    def __init__(
        self,
        token: object,
        *,
        destination: _RelayProcessOwnerDestination,
        grant: object,
        roles: tuple[tuple[str, _RelayProcessRoleAuthority], ...],
    ) -> None:
        claim = _authorized_process_claim(destination, grant, roles)
        if token is not _OWNER_TOKEN or type(claim) is not object:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_destination", destination)
        object.__setattr__(self, "_claim", claim)

    def _matches(
        self,
        destination: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        return bool(
            destination is self._destination
            and self._destination._owner_matches(
                self._claim,
                self,
                grant,
                pair_key,
                roles,
            )
        )

    def _stop_unstarted(self, authority: object, *, observe_only: bool = False) -> bool:
        return self._destination._stop_unstarted(
            self._claim,
            self,
            authority,
            observe_only=observe_only,
        )

    def _role_phases(self) -> tuple[str, ...]:
        return self._destination._owner_role_phases(self._claim, self)

    def _is_retired(self) -> bool:
        return self._destination._owner_is_retired(self._claim)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayProcessOwner()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay concrete process owner is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay concrete process owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete process owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete process owner cannot be serialized")


class _RelayProcessOwnerDestination:
    """Sole mutable anchor for one process graph and its terminal claim."""

    __slots__ = ("_lock", "_state")

    def __init__(self, token: object) -> None:
        if token is not _DESTINATION_TOKEN:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_state", _empty_snapshot(object()))

    def _resolve_or_preown_roles(
        self,
        candidate_key: object,
    ) -> tuple[object, tuple[tuple[str, _RelayProcessRoleAuthority], ...]]:
        if type(candidate_key) is not object:
            raise TypeError(_FAILURE)
        with self._lock:
            state = self._state
            if state.phase == "empty":
                roles = tuple(
                    (
                        role,
                        _new_process_role_authority(
                            self,
                            state.claim,
                            candidate_key,
                            role,
                        ),
                    )
                    for role in _ROLES
                )
                candidate = _ProcessSnapshot(
                    "preowned",
                    state.claim,
                    candidate_key,
                    roles,
                    None,
                    None,
                    ("preowned", "preowned", "preowned"),
                )
                _commit_process_snapshot(self, candidate)
                state = self._state
            if state.phase not in {"preowned", "owned"} or not _snapshot_roles_have_shape(
                self,
                state,
            ):
                raise TypeError(_FAILURE)
            return state.pair_key, state.roles  # type: ignore[return-value]

    def _claim_for_roles(self, pair_key: object, roles: object) -> object | None:
        with self._lock:
            state = self._state
            if (
                state.phase in {"preowned", "owned", "retiring"}
                and state.pair_key is pair_key
                and _same_roles(state.roles, roles)
                and _snapshot_roles_have_shape(self, state)
            ):
                return state.claim
            if _sealed_snapshot_has_shape(state) and _roles_bear_claim(
                self,
                state.claim,
                roles,
            ):
                return state.claim
            return None

    def _matches_roles(self, pair_key: object, roles: object) -> bool:
        with self._lock:
            state = self._state
            return bool(
                state.phase in {"preowned", "owned"}
                and state.pair_key is pair_key
                and _same_roles(state.roles, roles)
                and _snapshot_roles_have_shape(self, state)
            )

    def _publish_owner(self, owner: _RelayProcessOwner, grant: object) -> None:
        with self._lock:
            state = self._state
            if state.phase == "owned":
                if state.owner is owner and state.grant is grant:
                    return
                raise TypeError(_FAILURE)
            if (
                state.phase != "preowned"
                or owner._destination is not self
                or owner._claim is not state.claim
                or getattr(grant, "_pair_key", None) is not state.pair_key
                or not _snapshot_roles_have_shape(self, state)
            ):
                raise TypeError(_FAILURE)
            _commit_process_snapshot(
                self,
                state._replace(phase="owned", grant=grant, owner=owner),
            )

    def _seal_unpublished(
        self,
        claim: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        with self._lock:
            state = self._state
            if _sealed_snapshot_has_shape(state):
                return state.claim is claim and _roles_bear_claim(self, claim, roles)
            if not (
                state.phase == "preowned"
                and state.claim is claim
                and state.pair_key is pair_key
                and getattr(grant, "_pair_key", None) is pair_key
                and state.grant is None
                and state.owner is None
                and _same_roles(state.roles, roles)
                and _snapshot_roles_have_shape(self, state)
                and state.role_phases == ("preowned", "preowned", "preowned")
            ):
                return False
            _commit_process_snapshot(self, _sealed_snapshot(state.claim))
            return self._is_sealed_empty()

    def _read_owner(self) -> _RelayProcessOwner | None:
        with self._lock:
            return self._state.owner

    def _owner_was_published(self) -> bool:
        with self._lock:
            return self._state.phase in {"owned", "retiring", "sealed"}

    def _owner_matches(
        self,
        claim: object,
        owner: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        with self._lock:
            state = self._state
            return bool(
                state.phase == "owned"
                and state.claim is claim
                and state.owner is owner
                and state.grant is grant
                and state.pair_key is pair_key
                and _same_roles(state.roles, roles)
                and _snapshot_roles_have_shape(self, state)
            )

    def _stop_unstarted(
        self,
        claim: object,
        owner: object,
        authority: object,
        *,
        observe_only: bool = False,
    ) -> bool:
        with self._lock:
            state = self._state
            if (
                state.phase != "owned"
                or state.claim is not claim
                or state.owner is not owner
                or not _snapshot_roles_have_shape(self, state)
            ):
                return False
            index = _authority_index(state.pair_key, state.roles, authority)
            if index < 0 or state.role_phases[index] not in {"preowned", "stopped"}:
                return False
            if state.role_phases[index] == "stopped":
                return True
            if observe_only:
                return False
            if state.role_phases[index] == "preowned" and any(
                phase != "stopped" for phase in state.role_phases[index + 1 :]
            ):
                return False
            phases = list(state.role_phases)
            phases[index] = "stopped"
            _commit_process_snapshot(self, state._replace(role_phases=tuple(phases)))
            return True

    def _owner_role_phases(self, claim: object, owner: object) -> tuple[str, ...]:
        with self._lock:
            state = self._state
            if _sealed_snapshot_has_shape(state) and state.claim is claim:
                return _RETIRED_PHASES
            if (
                state.claim is claim
                and state.owner is owner
                and _snapshot_roles_have_shape(self, state)
            ):
                return state.role_phases
            return ()

    def _begin_retirement(
        self,
        claim: object,
        owner: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        with self._lock:
            state = self._state
            if state.phase == "retiring":
                return _retiring_snapshot_matches(
                    self,
                    state,
                    claim,
                    owner,
                    grant,
                    pair_key,
                    roles,
                )
            if (
                state.phase != "owned"
                or state.claim is not claim
                or state.owner is not owner
                or state.grant is not grant
                or state.pair_key is not pair_key
                or not _same_roles(state.roles, roles)
                or not _snapshot_roles_have_shape(self, state)
                or any(phase not in {"preowned", "stopped"} for phase in state.role_phases)
            ):
                return False
            _commit_process_snapshot(
                self,
                state._replace(phase="retiring", role_phases=_RETIRED_PHASES),
            )
            return True

    def _retirement_matches(
        self,
        claim: object,
        owner: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        with self._lock:
            return _retiring_snapshot_matches(
                self,
                self._state,
                claim,
                owner,
                grant,
                pair_key,
                roles,
            )

    def _seal_retired(
        self,
        claim: object,
        owner: object,
        grant: object,
        pair_key: object,
        roles: object,
    ) -> bool:
        with self._lock:
            state = self._state
            if _sealed_snapshot_has_shape(state):
                return state.claim is claim and _roles_bear_claim(self, claim, roles)
            if not _retiring_snapshot_matches(
                self,
                state,
                claim,
                owner,
                grant,
                pair_key,
                roles,
            ):
                return False
            _commit_process_snapshot(self, _sealed_snapshot(state.claim))
            return True

    def _owner_is_retired(self, claim: object) -> bool:
        with self._lock:
            state = self._state
            return _sealed_snapshot_has_shape(state) and state.claim is claim

    def _is_sealed_empty(self) -> bool:
        with self._lock:
            return _sealed_snapshot_has_shape(self._state)

    def _role_pair_key(
        self,
        claim: object,
        authority: object,
        role: str,
    ) -> object | None:
        with self._lock:
            state = self._state
            if (
                state.phase in {"preowned", "owned", "retiring"}
                and state.claim is claim
                and _snapshot_roles_have_shape(self, state)
                and any(name == role and value is authority for name, value in state.roles)
            ):
                return state.pair_key
            return None

    def _role_authority_matches(
        self,
        claim: object,
        pair_key: object,
        role: str,
        authority: object,
    ) -> bool:
        with self._lock:
            state = self._state
            return bool(
                state.phase in {"preowned", "owned", "retiring"}
                and state.claim is claim
                and state.pair_key is pair_key
                and _snapshot_roles_have_shape(self, state)
                and any(name == role and value is authority for name, value in state.roles)
            )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayProcessOwnerDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay concrete process owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete process owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete process owner destination cannot be serialized")


_PROCESS_OWNERS: dict[object, _RelayProcessOwner] = {}


def _new_process_owner_destination() -> _RelayProcessOwnerDestination:
    return _RelayProcessOwnerDestination(_DESTINATION_TOKEN)


def _resolve_or_preown_process_roles(
    destination: _RelayProcessOwnerDestination,
) -> tuple[object, tuple[tuple[str, _RelayProcessRoleAuthority], ...]]:
    if type(destination) is not _RelayProcessOwnerDestination:
        raise TypeError(_FAILURE)
    return destination._resolve_or_preown_roles(object())


def _resolve_or_preown_process_owner(
    destination: _RelayProcessOwnerDestination,
    grant: object,
    roles: tuple[tuple[str, _RelayProcessRoleAuthority], ...],
) -> _RelayProcessOwner:
    from scripts.voice_pipecat_e2e_relay_invocation_process_owner_lifecycle import (
        _resolve_or_preown_process_owner as resolve,
    )

    return resolve(destination, grant, roles)


def _process_owner_matches(
    destination: object,
    grant: object,
    roles: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_owner_lifecycle import (
        _process_owner_matches as matches,
    )

    return matches(destination, grant, roles)


def _transactional_process_owner_matches(
    destination: object,
    grant: object,
    roles: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_owner_lifecycle import (
        _transactional_process_owner_matches as matches,
    )

    return matches(destination, grant, roles)


def _record_process_owner_matches(pair_key: object, record: object) -> bool:
    try:
        return bool(
            type(record) is tuple
            and len(record) == 5
            and getattr(record[0], "_pair_key", None) is pair_key
            and _process_owner_matches(
                record[3]._read_process_owner_destination(),
                record[0],
                record[4],
            )
        )
    except BaseException:
        return False


def _transactional_record_process_owner_matches(pair_key: object, record: object) -> bool:
    try:
        return bool(
            type(record) is tuple
            and len(record) == 5
            and getattr(record[0], "_pair_key", None) is pair_key
            and _transactional_process_owner_matches(
                record[3]._read_process_owner_destination(),
                record[0],
                record[4],
            )
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _stop_unstarted_process_role(
    destination: object,
    grant: object,
    roles: object,
    authority: object,
    *,
    observe_only: bool = False,
) -> bool:
    if not _transactional_process_owner_matches(destination, grant, roles):
        return False
    pair_key = getattr(grant, "_pair_key", None)
    with _LOCK:
        owner = _PROCESS_OWNERS.get(pair_key)
        return type(owner) is _RelayProcessOwner and owner._stop_unstarted(
            authority,
            observe_only=observe_only,
        )


def _retire_childless_process_owner(
    destination: object,
    grant: object,
    roles: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_owner_lifecycle import (
        _retire_childless_process_owner as retire,
    )

    return retire(destination, grant, roles)


def _process_owner_total_absence(destination: object) -> bool:
    try:
        if type(destination) is not _RelayProcessOwnerDestination:
            return False
        with _LOCK:
            return not _PROCESS_OWNERS and destination._is_sealed_empty()
    except BaseException:
        return False


def _process_owner_registries_are_empty() -> bool:
    with _LOCK:
        return not _PROCESS_OWNERS


def _authorized_process_claim(
    destination: object,
    grant: object,
    roles: object,
    *,
    allow_terminal: bool = False,
) -> object | None:
    """Authenticate the exact factory grant and parent-child destination link."""

    try:
        from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
            _RelayConcreteInvocationGrant,
            _RelayConcreteInvocationPairDestination,
        )

        if (
            type(destination) is not _RelayProcessOwnerDestination
            or type(grant) is not _RelayConcreteInvocationGrant
        ):
            return None
        parent = grant._destination
        pair_key = grant._pair_key
        if (
            type(parent) is not _RelayConcreteInvocationPairDestination
            or type(pair_key) is not object
            or parent._read_process_owner_destination() is not destination
        ):
            return None
        claim = destination._claim_for_roles(pair_key, roles)
        if (
            type(claim) is not object
            or (not allow_terminal and not destination._matches_roles(pair_key, roles))
            or not parent._process_grant_admission_matches(
                destination,
                claim,
                grant,
                roles,
                allow_terminal=allow_terminal,
            )
        ):
            return None
        return claim
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _commit_process_snapshot(
    destination: _RelayProcessOwnerDestination,
    snapshot: _ProcessSnapshot,
) -> None:
    """Single durable publication seam used by deterministic phase-cut tests."""

    object.__setattr__(destination, "_state", snapshot)


def _store_process_owner(pair_key: object, owner: _RelayProcessOwner) -> None:
    _PROCESS_OWNERS[pair_key] = owner


def _pop_process_owner(pair_key: object) -> None:
    _PROCESS_OWNERS.pop(pair_key)


__all__: list[str] = []

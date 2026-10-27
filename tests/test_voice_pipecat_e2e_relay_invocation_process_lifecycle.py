"""Effect-free process-owner lifecycle for the private concrete adapter."""
# ruff: noqa: E402

from __future__ import annotations

import copy
import gc
import pickle
import sys
import threading
import weakref
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation_process_grant_admission_values as grant_admission_values
import scripts.voice_pipecat_e2e_relay_invocation_process_owner_lifecycle as owner_lifecycle
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_values as pair_values
import scripts.voice_pipecat_e2e_relay_invocation_process_state as process_state
import scripts.voice_pipecat_e2e_relay_invocation_process_values as process_values
import scripts.voice_pipecat_e2e_relay_invocation_values as invocation_values


@pytest.fixture(autouse=True)
def _isolated_process_owner_state() -> None:
    process_state._PROCESS_OWNERS.clear()
    yield
    process_state._PROCESS_OWNERS.clear()


class _ForgedGrant:
    def __init__(self, pair_key: object) -> None:
        self._pair_key = pair_key


class _PoisonAdmission:
    def __init__(self) -> None:
        self.calls = 0

    def __getitem__(self, _index: object) -> object:
        self.calls += 1
        raise AssertionError("malformed admission was executed")

    def __len__(self) -> int:
        self.calls += 1
        raise AssertionError("malformed admission was executed")

    def __call__(self) -> object:
        self.calls += 1
        raise AssertionError("malformed admission was executed")


class _FalseyError(BaseException):
    def __bool__(self) -> bool:
        return False


def _no_wait(_seconds: float) -> None:
    return None


def _new_parent() -> object:
    return pair_values._RelayConcreteInvocationPairDestination(
        pair_values._DESTINATION_TOKEN,
        build=object(),
        binding=object(),
        selection=process_values._concrete_invocation_selection(),
        runtime_deadline=105.0,
        runtime_timeout_seconds=5.0,
        cleanup_timeout_seconds=15.0,
        clock=lambda: 100.0,
        wait=_no_wait,
        epoch_clock=lambda: 1_786_982_400.0,
    )


def _authenticated_graph() -> tuple[object, object, object, tuple[tuple[str, object], ...]]:
    parent = _new_parent()
    destination = parent._read_process_owner_destination()
    pair_key, roles = process_state._resolve_or_preown_process_roles(destination)
    grant = parent._resolve_or_mint_process_grant(
        pair_values._GRANT_FACTORY_TOKEN,
        pair_key,
        roles,
    )
    return parent, destination, grant, roles


def _clone_grant(grant: object, *, parent: object | None = None) -> object:
    return pair_values._RelayConcreteInvocationGrant(
        pair_values._GRANT_CONSTRUCTOR_TOKEN,
        build=grant._build,
        binding=grant._binding,
        selection=grant._selection,
        destination=grant._destination if parent is None else parent,
        pair_key=grant._pair_key,
        runtime_deadline=grant._runtime_deadline,
        runtime_timeout_seconds=grant._runtime_timeout_seconds,
        cleanup_timeout_seconds=grant._cleanup_timeout_seconds,
        clock=grant._clock,
        wait=grant._wait,
        epoch_clock=grant._epoch_clock,
    )


def _cut_snapshot_once(
    monkeypatch: pytest.MonkeyPatch,
    predicate,
    *,
    after: bool,
    error: BaseException,
) -> list[bool]:
    original = process_state._commit_process_snapshot
    fired: list[bool] = []

    def commit(destination, snapshot):
        if not fired and predicate(snapshot):
            fired.append(True)
            if not after:
                raise error
            original(destination, snapshot)
            raise error
        original(destination, snapshot)

    monkeypatch.setattr(process_state, "_commit_process_snapshot", commit)
    return fired


def _preowned_owner() -> tuple[object, object, object, tuple[tuple[str, object], ...]]:
    _parent, destination, grant, roles = _authenticated_graph()
    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    return destination, owner, grant, roles


def test_admission_shape_checks_reject_malformed_values_without_execution() -> None:
    poison = _PoisonAdmission()
    values = (object(),) * 5

    assert not grant_admission_values._process_grant_admission_has_shape(poison, *values)
    assert not grant_admission_values._terminal_process_grant_admission_matches(poison, *values)
    assert poison.calls == 0


def test_process_owner_preowns_three_exact_roles_before_registry_admission() -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    pair_key = grant._pair_key

    assert tuple(role for role, _authority in roles) == ("app", "web", "browser")
    assert len({authority for _role, authority in roles}) == 3
    assert process_state._PROCESS_OWNERS == {}
    assert all(
        type(authority) is process_values._RelayProcessRoleAuthority
        and authority._matches(pair_key, role)
        and not authority
        and repr(authority) == "_RelayProcessRoleAuthority()"
        for role, authority in roles
    )

    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)

    assert process_state._PROCESS_OWNERS == {pair_key: owner}
    assert destination._read_owner() is owner
    assert owner._role_phases() == ("preowned", "preowned", "preowned")
    assert process_state._process_owner_matches(destination, grant, roles)
    assert not owner
    assert repr(owner) == "_RelayProcessOwner()"
    for value in (owner, destination, *(authority for _role, authority in roles)):
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(value)


def test_same_key_lookalike_and_unadmitted_exact_grants_cannot_claim_owner() -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    forged = _ForgedGrant(grant._pair_key)
    cloned = _clone_grant(grant)

    for rejected in (forged, cloned):
        with pytest.raises(TypeError, match="process owner is invalid"):
            process_state._resolve_or_preown_process_owner(destination, rejected, roles)
        with pytest.raises(TypeError, match="process owner is invalid"):
            process_state._RelayProcessOwner(
                process_state._OWNER_TOKEN,
                destination=destination,
                grant=rejected,
                roles=roles,
            )
        assert process_state._PROCESS_OWNERS == {}
        assert destination._read_owner() is None

    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert process_state._PROCESS_OWNERS == {grant._pair_key: owner}


def test_parent_birth_admission_cannot_adopt_a_caller_selected_exact_owner() -> None:
    parent, destination, grant, roles = _authenticated_graph()
    claim = destination._claim_for_roles(grant._pair_key, roles)
    forged = process_state._RelayProcessOwner(
        process_state._OWNER_TOKEN,
        destination=destination,
        grant=grant,
        roles=roles,
    )

    with pytest.raises(TypeError):
        parent._resolve_or_admit_process_owner(
            destination,
            claim,
            grant,
            roles,
            forged,
            allow_dead_replacement=True,
        )

    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    admission = parent._process_grant_admission
    assert owner is not forged
    assert type(admission[5]) is weakref.ReferenceType
    assert admission[5]() is owner
    assert process_state._PROCESS_OWNERS == {grant._pair_key: owner}
    assert destination._read_owner() is owner


@pytest.mark.parametrize("cut", ["before", "after"])
@pytest.mark.parametrize(
    ("error_type", "error_argument"),
    [(KeyboardInterrupt, "parent-admission-cut"), (SystemExit, 71)],
)
def test_parent_birth_admission_cuts_replay_only_the_factory_owner(
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
    error_type: type[BaseException],
    error_argument: object,
) -> None:
    parent, destination, grant, roles = _authenticated_graph()
    original = pair_values._RelayConcreteInvocationPairDestination._resolve_or_admit_process_owner
    retained: list[object] = []

    def interrupt(candidate_parent, *args, **kwargs):
        if candidate_parent is parent and cut == "before":
            raise error_type(error_argument)
        owner = original(candidate_parent, *args, **kwargs)
        if candidate_parent is parent:
            retained.append(owner)
            raise error_type(error_argument)
        return owner

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_admit_process_owner",
        interrupt,
    )
    with pytest.raises(error_type) as captured:
        process_state._resolve_or_preown_process_owner(destination, grant, roles)
    if error_type is KeyboardInterrupt:
        assert str(captured.value) == error_argument
    else:
        assert captured.value.code == error_argument
    assert process_state._PROCESS_OWNERS == {}
    assert destination._read_owner() is None

    admission = parent._process_grant_admission
    if cut == "before":
        assert admission[5] is None
    else:
        assert type(admission[5]) is weakref.ReferenceType
        assert admission[5]() is retained[0]
    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_admit_process_owner",
        original,
    )

    recovered = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    if cut == "after":
        assert recovered is retained[0]
    assert parent._process_grant_admission[5]() is recovered
    assert process_state._PROCESS_OWNERS == {grant._pair_key: recovered}
    assert destination._read_owner() is recovered


def test_parent_birth_admission_replaces_a_dead_weak_factory_return_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, destination, grant, roles = _authenticated_graph()
    original = pair_values._RelayConcreteInvocationPairDestination._resolve_or_admit_process_owner

    def admit_then_interrupt(candidate_parent, *args, **kwargs):
        owner = original(candidate_parent, *args, **kwargs)
        if candidate_parent is parent:
            raise KeyboardInterrupt("dead weak admission cut")
        return owner

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_admit_process_owner",
        admit_then_interrupt,
    )

    try:
        process_state._resolve_or_preown_process_owner(destination, grant, roles)
    except KeyboardInterrupt as error:
        assert str(error) == "dead weak admission cut"
        error.__traceback__ = None
    else:
        raise AssertionError("parent admission cut did not fire")

    reference = parent._process_grant_admission[5]
    assert type(reference) is weakref.ReferenceType
    assert process_state._PROCESS_OWNERS == {}
    assert destination._read_owner() is None
    gc.collect()
    assert reference() is None

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_admit_process_owner",
        original,
    )
    recovered = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert parent._process_grant_admission[5]() is recovered
    assert process_state._PROCESS_OWNERS == {grant._pair_key: recovered}
    assert destination._read_owner() is recovered


@pytest.mark.parametrize("retain_owner", [False, True], ids=["dead", "live"])
def test_cleanup_seals_exact_unpublished_process_owner_admission(
    monkeypatch: pytest.MonkeyPatch,
    retain_owner: bool,
) -> None:
    parent, destination, grant, roles = _authenticated_graph()
    original = pair_values._RelayConcreteInvocationPairDestination._resolve_or_admit_process_owner
    retained: list[object] = []

    def admit_then_interrupt(candidate_parent, *args, **kwargs):
        owner = original(candidate_parent, *args, **kwargs)
        if candidate_parent is parent:
            if retain_owner:
                retained.append(owner)
            raise KeyboardInterrupt("cleanup admission cut")
        return owner

    monkeypatch.setattr(
        pair_values._RelayConcreteInvocationPairDestination,
        "_resolve_or_admit_process_owner",
        admit_then_interrupt,
    )
    try:
        process_state._resolve_or_preown_process_owner(destination, grant, roles)
    except KeyboardInterrupt as error:
        assert str(error) == "cleanup admission cut"
        error.__traceback__ = None
    else:
        raise AssertionError("parent admission cut did not fire")

    reference = parent._process_grant_admission[5]
    assert type(reference) is weakref.ReferenceType
    gc.collect()
    assert (reference() is not None) is retain_owner
    request = invocation_values.RelayStopRequest(
        invocation_values._STOP_TOKEN,
        pair_key=grant._pair_key,
        absolute_deadline=115.0,
    )
    assert parent._bind_stop_request(request)
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert process_state._process_owner_total_absence(destination)
    assert process_state._PROCESS_OWNERS == {}
    if retained:
        assert retained[0]._is_retired()
    assert parent._seal_process_grant_admission(grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)
    terminal = parent._process_grant_admission
    assert terminal[0] == "sealed"
    assert terminal[5] is reference


def test_never_born_terminal_process_admission_is_exact_and_replay_safe() -> None:
    parent, destination, grant, roles = _authenticated_graph()
    assert parent._process_grant_admission[5] is None
    request = invocation_values.RelayStopRequest(
        invocation_values._STOP_TOKEN,
        pair_key=grant._pair_key,
        absolute_deadline=115.0,
    )
    assert parent._bind_stop_request(request)

    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)
    terminal = parent._process_grant_admission
    assert terminal[0] == "sealed" and terminal[5] is None
    assert (
        process_state._authorized_process_claim(
            destination,
            grant,
            roles,
            allow_terminal=True,
        )
        is destination._state.claim
    )

    process_state._PROCESS_OWNERS[grant._pair_key] = object()
    assert not process_state._retire_childless_process_owner(destination, grant, roles)
    process_state._PROCESS_OWNERS.clear()
    assert process_state._retire_childless_process_owner(destination, grant, roles)


def test_never_born_terminal_admission_preserves_sealed_observation_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, destination, grant, roles = _authenticated_graph()
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)
    assert parent._process_grant_admission[5] is None
    original = process_state._RelayProcessOwnerDestination._is_sealed_empty
    raised = False

    def interrupt_sealed_observation(candidate_destination):
        nonlocal raised
        if candidate_destination is destination and not raised:
            raised = True
            raise KeyboardInterrupt("terminal sealed observation control")
        return original(candidate_destination)

    monkeypatch.setattr(
        process_state._RelayProcessOwnerDestination,
        "_is_sealed_empty",
        interrupt_sealed_observation,
    )
    with pytest.raises(KeyboardInterrupt, match="terminal sealed observation control"):
        process_state._authorized_process_claim(
            destination,
            grant,
            roles,
            allow_terminal=True,
        )

    assert raised
    monkeypatch.setattr(
        process_state._RelayProcessOwnerDestination,
        "_is_sealed_empty",
        original,
    )
    assert (
        process_state._authorized_process_claim(
            destination,
            grant,
            roles,
            allow_terminal=True,
        )
        is destination._state.claim
    )


def test_sealed_admission_retains_only_weak_exact_graph_identity() -> None:
    parent, destination, grant, roles = _authenticated_graph()
    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    for _role, authority in reversed(roles):
        assert process_state._stop_unstarted_process_role(
            destination,
            grant,
            roles,
            authority,
        )
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert owner._is_retired()
    assert process_state._authorized_process_claim(destination, grant, roles) is None
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._RelayProcessOwner(
            process_state._OWNER_TOKEN,
            destination=destination,
            grant=grant,
            roles=roles,
        )
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)

    cloned = _clone_grant(grant)
    assert process_state._authorized_process_claim(destination, cloned, roles) is None
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._RelayProcessOwner(
            process_state._OWNER_TOKEN,
            destination=destination,
            grant=cloned,
            roles=roles,
        )
    assert not process_state._retire_childless_process_owner(destination, cloned, roles)
    assert not parent._seal_process_grant_admission(cloned, roles)

    fresh_roles = tuple(
        (
            role,
            process_values._new_process_role_authority(
                destination,
                destination._state.claim,
                grant._pair_key,
                role,
            ),
        )
        for role, _authority in roles
    )
    assert process_state._authorized_process_claim(destination, grant, fresh_roles) is None
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._RelayProcessOwner(
            process_state._OWNER_TOKEN,
            destination=destination,
            grant=grant,
            roles=fresh_roles,
        )
    assert not process_state._retire_childless_process_owner(
        destination,
        grant,
        fresh_roles,
    )
    assert not parent._seal_process_grant_admission(grant, fresh_roles)

    assert process_state._authorized_process_claim(destination, grant, roles) is None
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._RelayProcessOwner(
            process_state._OWNER_TOKEN,
            destination=destination,
            grant=grant,
            roles=roles,
        )
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert parent._seal_process_grant_admission(grant, roles)


@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize(
    ("error_type", "error_argument"),
    [(KeyboardInterrupt, "snapshot-cut"), (SystemExit, 71)],
)
def test_role_preownership_snapshot_is_atomic_and_replay_safe(
    monkeypatch: pytest.MonkeyPatch,
    after: bool,
    error_type: type[BaseException],
    error_argument: object,
) -> None:
    parent = _new_parent()
    destination = parent._read_process_owner_destination()
    error = error_type(error_argument)
    fired = _cut_snapshot_once(
        monkeypatch,
        lambda snapshot: snapshot.phase == "preowned",
        after=after,
        error=error,
    )

    with pytest.raises(error_type):
        process_state._resolve_or_preown_process_roles(destination)

    pair_key, roles = process_state._resolve_or_preown_process_roles(destination)
    replayed_key, replayed_roles = process_state._resolve_or_preown_process_roles(destination)
    grant = parent._resolve_or_mint_process_grant(
        pair_values._GRANT_FACTORY_TOKEN,
        pair_key,
        roles,
    )
    assert fired == [True]
    assert replayed_key is pair_key
    assert replayed_roles is roles
    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert type(owner) is process_state._RelayProcessOwner
    assert destination._read_owner() is owner


@pytest.mark.parametrize("phase", ["owned", "retiring", "sealed"])
@pytest.mark.parametrize("after", [False, True])
def test_owner_lifecycle_snapshot_cuts_converge_without_foreign_adoption(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    after: bool,
) -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    owner = None
    if phase != "owned":
        owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    fired = _cut_snapshot_once(
        monkeypatch,
        lambda snapshot: snapshot.phase == phase,
        after=after,
        error=KeyboardInterrupt(f"{phase}-snapshot-cut"),
    )

    with pytest.raises(KeyboardInterrupt, match=f"{phase}-snapshot-cut"):
        if phase == "owned":
            process_state._resolve_or_preown_process_owner(destination, grant, roles)
        else:
            process_state._retire_childless_process_owner(destination, grant, roles)

    assert fired == [True]
    if phase == "owned":
        owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    else:
        _foreign_parent, _foreign_destination, _foreign_grant, foreign_roles = (
            _authenticated_graph()
        )
        assert not process_state._retire_childless_process_owner(
            destination,
            _ForgedGrant(grant._pair_key),
            roles,
        )
        assert not process_state._retire_childless_process_owner(
            destination,
            grant,
            foreign_roles,
        )
        assert process_state._retire_childless_process_owner(destination, grant, roles)
        assert owner._is_retired()
        assert process_state._process_owner_total_absence(destination)


def test_two_destination_race_admits_only_one_process_graph() -> None:
    graphs = [_authenticated_graph(), _authenticated_graph()]
    barrier = threading.Barrier(3)
    owners: list[object] = []
    errors: list[BaseException] = []

    def resolve(graph) -> None:
        _parent, destination, grant, roles = graph
        barrier.wait()
        try:
            owners.append(process_state._resolve_or_preown_process_owner(destination, grant, roles))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=resolve, args=(graph,)) for graph in graphs]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(owners) == len(errors) == 1
    assert type(errors[0]) is TypeError
    assert list(process_state._PROCESS_OWNERS.values()) == owners
    assert sum(graph[1]._read_owner() is owners[0] for graph in graphs) == 1
    assert all(
        graph[0]._process_grant_admission[5] is None
        or type(graph[0]._process_grant_admission[5]) is weakref.ReferenceType
        for graph in graphs
    )


def test_process_graph_has_one_mutable_snapshot_anchor() -> None:
    assert process_state._RelayProcessOwnerDestination.__slots__ == ("_lock", "_state")
    assert process_state._RelayProcessOwner.__slots__ == (
        "__weakref__",
        "_claim",
        "_destination",
    )
    assert process_values._RelayProcessRoleAuthority.__slots__ == (
        "__weakref__",
        "_claim",
        "_destination",
        "_role_name",
    )


def test_malformed_role_phases_never_look_retired_or_release_capacity() -> None:
    destination, owner, grant, roles = _preowned_owner()
    object.__setattr__(
        destination,
        "_state",
        destination._state._replace(role_phases=()),
    )

    assert not process_state._process_owner_matches(destination, grant, roles)
    assert owner._role_phases() == ()
    assert all(not authority._matches(grant._pair_key, role) for role, authority in roles)
    assert not process_state._stop_unstarted_process_role(
        destination,
        grant,
        roles,
        roles[2][1],
    )
    assert not process_state._retire_childless_process_owner(destination, grant, roles)
    assert process_state._PROCESS_OWNERS == {grant._pair_key: owner}
    assert destination._read_owner() is owner
    assert not process_state._process_owner_total_absence(destination)


@pytest.mark.parametrize("cut", ["registry-before", "registry-after", "destination"])
def test_process_owner_store_return_loss_recovers_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    pair_key = grant._pair_key
    losses = 0

    if cut.startswith("registry"):
        original = process_state._store_process_owner

        def store_then_lose(key, owner):
            nonlocal losses
            if losses == 0:
                losses += 1
                if cut == "registry-after":
                    original(key, owner)
                raise OSError("synthetic process owner registry return loss")
            original(key, owner)

        monkeypatch.setattr(process_state, "_store_process_owner", store_then_lose)
    else:
        original = process_state._RelayProcessOwnerDestination._publish_owner

        def publish_then_lose(self, owner, candidate_grant):
            nonlocal losses
            original(self, owner, candidate_grant)
            if losses == 0:
                losses += 1
                raise OSError("synthetic process owner destination return loss")

        monkeypatch.setattr(
            process_state._RelayProcessOwnerDestination,
            "_publish_owner",
            publish_then_lose,
        )

    with pytest.raises(OSError, match="return loss"):
        process_state._resolve_or_preown_process_owner(destination, grant, roles)

    retained = destination._read_owner()
    if cut == "registry-before":
        assert retained is None
        assert process_state._PROCESS_OWNERS == {}
    recovered = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert losses == 1
    if cut != "registry-before":
        assert recovered is retained
    assert process_state._PROCESS_OWNERS[pair_key] is recovered
    assert destination._read_owner() is recovered
    assert process_state._process_owner_matches(destination, grant, roles)


def test_interrupted_capacity_reservation_cannot_leave_a_second_live_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _authenticated_graph()
    second = _authenticated_graph()
    original_store = process_state._store_process_owner

    def interrupt_before_store(_pair_key, _owner):
        raise KeyboardInterrupt("capacity reservation interrupted")

    monkeypatch.setattr(process_state, "_store_process_owner", interrupt_before_store)
    with pytest.raises(KeyboardInterrupt, match="capacity reservation interrupted"):
        process_state._resolve_or_preown_process_owner(first[1], first[2], first[3])

    assert process_state._PROCESS_OWNERS == {}
    assert first[1]._read_owner() is None
    monkeypatch.setattr(process_state, "_store_process_owner", original_store)

    admitted = process_state._resolve_or_preown_process_owner(second[1], second[2], second[3])
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._resolve_or_preown_process_owner(first[1], first[2], first[3])

    assert first[1]._read_owner() is None
    assert second[1]._read_owner() is admitted
    assert process_state._PROCESS_OWNERS == {second[2]._pair_key: admitted}


def test_owner_admission_preserves_first_control_across_store_and_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    original_store = process_state._store_process_owner
    original_commit = process_state._commit_process_snapshot
    cuts: list[str] = []

    def store_then_interrupt(pair_key, owner):
        original_store(pair_key, owner)
        cuts.append("store")
        raise KeyboardInterrupt("first admission control")

    def publish_then_exit(candidate_destination, snapshot):
        original_commit(candidate_destination, snapshot)
        if snapshot.phase == "owned":
            cuts.append("publish")
            raise SystemExit(71)

    monkeypatch.setattr(process_state, "_store_process_owner", store_then_interrupt)
    monkeypatch.setattr(process_state, "_commit_process_snapshot", publish_then_exit)

    with pytest.raises(KeyboardInterrupt, match="first admission control"):
        process_state._resolve_or_preown_process_owner(destination, grant, roles)

    retained = destination._read_owner()
    assert cuts == ["store", "publish"]
    assert process_state._PROCESS_OWNERS == {grant._pair_key: retained}
    assert process_state._resolve_or_preown_process_owner(destination, grant, roles) is retained


def test_owner_admission_preserves_a_falsey_first_error() -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    original_store = process_state._store_process_owner
    original_commit = process_state._commit_process_snapshot
    first = _FalseyError("falsey admission error")
    cuts: list[str] = []

    def store_then_error(pair_key, owner):
        original_store(pair_key, owner)
        cuts.append("store")
        raise first

    def publish_then_exit(candidate_destination, snapshot):
        original_commit(candidate_destination, snapshot)
        if snapshot.phase == "owned":
            cuts.append("publish")
            raise SystemExit(71)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(process_state, "_store_process_owner", store_then_error)
        patch.setattr(process_state, "_commit_process_snapshot", publish_then_exit)
        with pytest.raises(_FalseyError) as escaped:
            process_state._resolve_or_preown_process_owner(destination, grant, roles)

    assert escaped.value is first
    assert cuts == ["store", "publish"]
    retained = destination._read_owner()
    assert process_state._PROCESS_OWNERS == {grant._pair_key: retained}
    assert process_state._resolve_or_preown_process_owner(destination, grant, roles) is retained


def test_process_owner_concurrent_replay_admits_one_exact_owner() -> None:
    _parent, destination, grant, roles = _authenticated_graph()
    pair_key = grant._pair_key
    barrier = threading.Barrier(3)
    owners: list[object] = []
    errors: list[BaseException] = []

    def resolve() -> None:
        try:
            barrier.wait()
            owners.append(process_state._resolve_or_preown_process_owner(destination, grant, roles))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=resolve) for _index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(owners) == 2 and owners[0] is owners[1]
    assert process_state._PROCESS_OWNERS == {pair_key: owners[0]}


def test_process_owner_cap_one_crosswire_and_primary_loss_fail_closed() -> None:
    destination, owner, grant, roles = _preowned_owner()
    pair_key = grant._pair_key
    _foreign_parent, foreign_destination, foreign_grant, foreign_roles = _authenticated_graph()

    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._resolve_or_preown_process_owner(
            foreign_destination,
            foreign_grant,
            foreign_roles,
        )
    replayed_grant = _ForgedGrant(pair_key)
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._resolve_or_preown_process_owner(destination, replayed_grant, roles)
    assert not process_state._process_owner_matches(destination, replayed_grant, roles)
    assert not process_state._process_owner_matches(destination, grant, foreign_roles)
    assert not process_state._stop_unstarted_process_role(
        destination,
        grant,
        roles,
        foreign_roles[0][1],
    )

    assert process_state._PROCESS_OWNERS.pop(pair_key) is owner
    assert not process_state._process_owner_matches(destination, grant, roles)
    assert not process_state._process_owner_total_absence(destination)
    assert not process_state._retire_childless_process_owner(destination, grant, roles)
    assert process_state._resolve_or_preown_process_owner(destination, grant, roles) is owner
    assert process_state._PROCESS_OWNERS.pop(pair_key) is owner
    object.__setattr__(destination, "_state", destination._state._replace(owner=None))
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert not process_state._process_owner_total_absence(destination)
    assert not process_state._retire_childless_process_owner(destination, grant, roles)


def test_exact_registry_clone_cannot_replace_the_parent_admitted_live_owner() -> None:
    parent, destination, grant, roles = _authenticated_graph()
    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    clone = process_state._RelayProcessOwner(
        process_state._OWNER_TOKEN,
        destination=destination,
        grant=grant,
        roles=roles,
    )
    assert clone is not owner
    assert parent._process_grant_admission[5]() is owner

    process_state._PROCESS_OWNERS[grant._pair_key] = clone
    assert not process_state._process_owner_matches(destination, grant, roles)
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._resolve_or_preown_process_owner(destination, grant, roles)
    assert not process_state._retire_childless_process_owner(destination, grant, roles)
    assert destination._read_owner() is owner
    assert parent._process_grant_admission[5]() is owner


def test_terminal_admission_keeps_exact_owner_through_sealed_pop_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, destination, grant, roles = _authenticated_graph()
    owner = process_state._resolve_or_preown_process_owner(destination, grant, roles)
    clone = process_state._RelayProcessOwner(
        process_state._OWNER_TOKEN,
        destination=destination,
        grant=grant,
        roles=roles,
    )
    for _role, authority in reversed(roles):
        assert process_state._stop_unstarted_process_role(
            destination,
            grant,
            roles,
            authority,
        )
    original_pop = process_state._pop_process_owner

    def interrupt_before_pop(_pair_key):
        raise OSError("synthetic process owner pop-before loss")

    monkeypatch.setattr(process_state, "_pop_process_owner", interrupt_before_pop)
    with pytest.raises(OSError, match="pop-before loss"):
        process_state._retire_childless_process_owner(destination, grant, roles)
    assert destination._is_sealed_empty()
    assert process_state._PROCESS_OWNERS == {grant._pair_key: owner}
    assert parent._seal_process_grant_admission(grant, roles)
    admission = parent._process_grant_admission
    assert len(admission) == 6 and admission[0] == "sealed"
    assert type(admission[5]) is weakref.ReferenceType and admission[5]() is owner

    process_state._PROCESS_OWNERS[grant._pair_key] = clone
    monkeypatch.setattr(process_state, "_pop_process_owner", original_pop)
    assert not process_state._retire_childless_process_owner(destination, grant, roles)
    assert process_state._PROCESS_OWNERS == {grant._pair_key: clone}
    assert admission[5]() is owner

    process_state._PROCESS_OWNERS[grant._pair_key] = owner
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert process_state._process_owner_total_absence(destination)


@pytest.mark.parametrize("seal_cut", ["before", "after"])
def test_retirement_seal_publication_holds_capacity_until_total_absence(
    monkeypatch: pytest.MonkeyPatch,
    seal_cut: str,
) -> None:
    first = _authenticated_graph()
    first_owner = process_state._resolve_or_preown_process_owner(first[1], first[2], first[3])
    second = _authenticated_graph()
    original_commit = process_state._commit_process_snapshot

    def seal_then_interrupt(destination, snapshot):
        if snapshot.phase == "sealed" and seal_cut == "before":
            raise KeyboardInterrupt("sealed publication interrupted")
        original_commit(destination, snapshot)
        if snapshot.phase == "sealed" and seal_cut == "after":
            raise KeyboardInterrupt("sealed publication interrupted")

    monkeypatch.setattr(process_state, "_commit_process_snapshot", seal_then_interrupt)
    with pytest.raises(KeyboardInterrupt, match="sealed publication interrupted"):
        process_state._retire_childless_process_owner(first[1], first[2], first[3])

    if seal_cut == "before":
        assert first[1]._state.phase == "retiring"
        assert process_state._PROCESS_OWNERS == {first[2]._pair_key: first_owner}
        assert not process_state._process_owner_total_absence(first[1])
        with pytest.raises(TypeError, match="process owner is invalid"):
            process_state._resolve_or_preown_process_owner(second[1], second[2], second[3])
        monkeypatch.setattr(process_state, "_commit_process_snapshot", original_commit)
        assert process_state._retire_childless_process_owner(first[1], first[2], first[3])
    else:
        assert first[1]._state.phase == "sealed"
        assert process_state._process_owner_total_absence(first[1])

    second_owner = process_state._resolve_or_preown_process_owner(second[1], second[2], second[3])
    assert process_state._PROCESS_OWNERS == {second[2]._pair_key: second_owner}


@pytest.mark.parametrize("pop_cut", ["before", "after"])
def test_reverse_prestart_stop_and_retirement_are_exact_and_replay_safe(
    monkeypatch: pytest.MonkeyPatch,
    pop_cut: str,
) -> None:
    destination, owner, grant, roles = _preowned_owner()

    app = next(value for name, value in roles if name == "app")
    assert not process_state._stop_unstarted_process_role(
        destination,
        grant,
        roles,
        app,
    )
    assert owner._role_phases() == ("preowned", "preowned", "preowned")

    for role in ("browser", "web", "app"):
        authority = next(value for name, value in roles if name == role)
        assert process_state._stop_unstarted_process_role(
            destination,
            grant,
            roles,
            authority,
        )
        assert process_state._stop_unstarted_process_role(
            destination,
            grant,
            roles,
            authority,
        )
    assert owner._role_phases() == ("stopped", "stopped", "stopped")

    original = process_state._pop_process_owner
    losses = 0

    def pop_then_lose(key):
        nonlocal losses
        if losses == 0:
            losses += 1
            if pop_cut == "after":
                original(key)
            raise OSError("synthetic process owner pop return loss")
        original(key)

    monkeypatch.setattr(process_state, "_pop_process_owner", pop_then_lose)
    with pytest.raises(OSError, match="pop return loss"):
        process_state._retire_childless_process_owner(destination, grant, roles)

    assert losses == 1
    assert process_state._authorized_process_claim(destination, grant, roles) is None
    with pytest.raises(TypeError, match="process owner is invalid"):
        process_state._RelayProcessOwner(
            process_state._OWNER_TOKEN,
            destination=destination,
            grant=grant,
            roles=roles,
        )
    assert owner._role_phases() == ("retired", "retired", "retired")
    assert owner._is_retired()
    assert (grant._pair_key in process_state._PROCESS_OWNERS) is (pop_cut == "before")
    if pop_cut == "before":
        assert not process_state._retire_childless_process_owner(
            destination,
            _ForgedGrant(grant._pair_key),
            roles,
        )
    assert process_state._retire_childless_process_owner(destination, grant, roles)
    assert owner._is_retired()
    assert process_state._process_owner_total_absence(destination)
    assert process_state._process_owner_registries_are_empty()
    assert all(authority._pair_key is None for _role, authority in roles)


def test_retirement_preserves_first_control_across_pop_and_seal_return_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, owner, grant, roles = _preowned_owner()
    original_pop = process_state._pop_process_owner
    original_commit = process_state._commit_process_snapshot
    cuts: list[str] = []

    def seal_then_interrupt(candidate_destination, snapshot):
        original_commit(candidate_destination, snapshot)
        if snapshot.phase == "sealed":
            cuts.append("seal")
            raise KeyboardInterrupt("first retirement control")

    def pop_then_exit(pair_key):
        original_pop(pair_key)
        cuts.append("pop")
        raise SystemExit(71)

    monkeypatch.setattr(process_state, "_commit_process_snapshot", seal_then_interrupt)
    monkeypatch.setattr(process_state, "_pop_process_owner", pop_then_exit)

    with pytest.raises(KeyboardInterrupt, match="first retirement control"):
        process_state._retire_childless_process_owner(destination, grant, roles)

    assert cuts == ["seal", "pop"]
    assert owner._is_retired()
    assert process_state._process_owner_total_absence(destination)
    assert process_state._retire_childless_process_owner(destination, grant, roles)


def test_retirement_preserves_a_falsey_pop_error_after_total_absence() -> None:
    destination, owner, grant, roles = _preowned_owner()
    original_pop = process_state._pop_process_owner
    first = _FalseyError("falsey retirement error")

    def pop_then_error(pair_key):
        original_pop(pair_key)
        raise first

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(process_state, "_pop_process_owner", pop_then_error)
        with pytest.raises(_FalseyError) as escaped:
            process_state._retire_childless_process_owner(destination, grant, roles)

    assert escaped.value is first
    assert owner._is_retired()
    assert process_state._process_owner_total_absence(destination)
    assert process_state._retire_childless_process_owner(destination, grant, roles)


def test_retirement_observation_return_loss_cannot_replace_first_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, owner, grant, roles = _preowned_owner()
    original_commit = process_state._commit_process_snapshot
    original_observe = owner_lifecycle._observe_total_absence
    cuts: list[str] = []

    def seal_then_interrupt(candidate_destination, snapshot):
        original_commit(candidate_destination, snapshot)
        if snapshot.phase == "sealed":
            cuts.append("seal")
            raise KeyboardInterrupt("first retirement observation control")

    def observe_then_exit(candidate_destination):
        original_observe(candidate_destination)
        cuts.append("observe")
        raise SystemExit(73)

    monkeypatch.setattr(process_state, "_commit_process_snapshot", seal_then_interrupt)
    monkeypatch.setattr(owner_lifecycle, "_observe_total_absence", observe_then_exit)
    with pytest.raises(KeyboardInterrupt, match="first retirement observation control"):
        process_state._retire_childless_process_owner(destination, grant, roles)

    assert cuts == ["seal", "observe"]
    assert owner._is_retired()
    assert process_state._process_owner_total_absence(destination)

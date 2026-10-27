"""Context-bound child-authority recovery tests for the relay aggregate."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_owner_authority as authority_module
import scripts.voice_pipecat_e2e_relay_owner_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_owner_forward as forward_module
from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerPlan
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    NetworkCleanupAuthority,
    NetworkPlan,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (
    CleanCoturnExitReceipt,
    ContainerAbsenceReceipt,
    CoturnAttachedCleanupRequired,
    CoturnDirectorySyncCleanupRequired,
    CoturnRuntimePrivateCleanupRequired,
    DirectorySyncCleanupAuthority,
    NetworkAbsenceReceipt,
    OwnedNetwork,
    RecoveredContainerCleanupAuthority,
    RuntimePrivateCleanupAuthority,
    StoppedCoturnReceipt,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    _new_unpublished_attached_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    CoturnTlsCleanupRequired,
    CoturnTlsPrivateCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    TlsMaterialLifetimeAuthority,
    new_tls_material_lifetime_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    new_private_descriptor_cleanup_authority,
)
from scripts.voice_pipecat_e2e_relay_owner import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeOwnerError,
    cleanup_relay_probe,
    new_relay_probe_owner,
    run_relay_probe,
)
from tests.test_voice_pipecat_e2e_relay_owner import (
    SECRET,
    _install_synthetic_lifecycle,
    _object,
)
from tests.test_voice_pipecat_e2e_relay_owner_recovery import _factory_context


class _Handle:
    def __init__(self) -> None:
        self.terminations = 0

    def terminate(self) -> None:
        self.terminations += 1


def _injected_failure(kind: str) -> None:
    if kind == "ordinary":
        raise RuntimeError("raw-pending-publication")
    if kind == "keyboard":
        raise KeyboardInterrupt("raw-pending-publication")
    raise SystemExit(73)


@pytest.mark.parametrize("position", ["before-store", "after-store", "return"])
@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_contextual_authority_publication_reconciles_every_finite_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: str,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = _new_unpublished_attached_cleanup_authority(owner._runner)
    handle = _Handle()
    assert authority._adopt(handle)
    original = CoturnAttachedCleanupRequired(authority)
    remaining = 1 if position == "after-store" else 19

    def hook(observed: str) -> None:
        nonlocal remaining
        if observed == position and remaining:
            remaining -= 1
            _injected_failure(kind)

    monkeypatch.setattr(authority_module, "_pending_publication_hook", hook)
    authority_module._retain_attached_cleanup_authority(
        owner,
        original,
        process=object(),  # type: ignore[arg-type]
        runner=owner._runner,
    )
    assert remaining == 0
    assert owner._pending_authority is authority
    owner._remember_exception(original)
    owner._cleanup_phase = "drain"
    expected = {
        "ordinary": None,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    if expected is None:
        cleanup_relay_probe(owner)
    else:
        with pytest.raises(expected) as captured:
            cleanup_relay_probe(owner)
        if kind == "system-exit":
            assert captured.value.code == 73  # type: ignore[attr-defined]
        assert not hasattr(captured.value, "cleanup_authority")
    assert authority._state == "settled"
    assert handle.terminations == 1
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("original_kind", ["keyboard", "system-exit"])
def test_original_control_wins_over_later_authority_publication_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = _new_unpublished_attached_cleanup_authority(owner._runner)
    handle = _Handle()
    assert authority._adopt(handle)
    original: BaseException
    if original_kind == "keyboard":
        original = KeyboardInterrupt("raw-original")
    else:
        original = SystemExit(41)
    original.cleanup_authority = authority  # type: ignore[attr-defined]
    remaining = 19

    def hook(position: str) -> None:
        nonlocal remaining
        if position == "before-store" and remaining:
            remaining -= 1
            raise SystemExit(73)

    monkeypatch.setattr(authority_module, "_pending_publication_hook", hook)
    authority_module._retain_attached_cleanup_authority(
        owner,
        original,
        process=object(),  # type: ignore[arg-type]
        runner=owner._runner,
    )
    assert remaining == 0
    owner._remember_exception(original)
    owner._cleanup_phase = "drain"
    expected = KeyboardInterrupt if original_kind == "keyboard" else SystemExit
    with pytest.raises(expected) as captured:
        cleanup_relay_probe(owner)
    if original_kind == "system-exit":
        assert captured.value.code == 41  # type: ignore[attr-defined]
    assert authority._state == "settled"
    assert handle.terminations == 1
    assert destination._record is None  # type: ignore[attr-defined]


def _failure_with_authority(
    kind: str,
    wrapper: type[BaseException],
    authority: object,
) -> BaseException:
    if kind == "ordinary":
        return wrapper(authority)  # type: ignore[call-arg]
    if kind == "keyboard":
        result: BaseException = KeyboardInterrupt("raw-contextual-child")
    else:
        result = SystemExit(73)
    result.cleanup_authority = authority  # type: ignore[attr-defined]
    return result


@pytest.mark.parametrize("boundary", ["network", "container"])
@pytest.mark.parametrize("authority_kind", ["directory", "private"])
@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "system-exit"])
def test_runtime_creation_retains_only_its_contextual_persistence_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    authority_kind: str,
    failure_kind: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    if authority_kind == "directory":
        authority = _object(
            DirectorySyncCleanupAuthority,
            _descriptor=7,
            _identity=None,
            _state="owned",
            _lock=threading.Lock(),
        )
        wrapper = CoturnDirectorySyncCleanupRequired
        cleanup_name = "cleanup_directory_sync_authority"
    else:
        authority = _object(
            RuntimePrivateCleanupAuthority,
            _authority=object(),
            _state="retained",
            _lock=threading.Lock(),
        )
        wrapper = CoturnRuntimePrivateCleanupRequired
        cleanup_name = "cleanup_runtime_private_authority"
    failure = _failure_with_authority(failure_kind, wrapper, authority)
    target = "create_owned_network" if boundary == "network" else "create_owned_container"
    monkeypatch.setattr(
        forward_module,
        target,
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )
    settled: list[object] = []

    def settle(candidate: object) -> None:
        assert candidate is authority
        object.__setattr__(candidate, "_state", "cleaned")
        settled.append(candidate)

    monkeypatch.setattr(cleanup_module, cleanup_name, settle)
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_unpublished_runtime_tls_material",
        lambda material: object.__setattr__(material, "_state", "cleaned"),
    )
    expected = {
        "ordinary": RelayProbeOwnerError,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[failure_kind]
    with pytest.raises(expected) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=SECRET,
            now=datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    if failure_kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    assert settled == [authority]
    assert destination._record is None  # type: ignore[attr-defined]
    assert owner._terminal_roots_empty()


@pytest.mark.parametrize(
    "boundary",
    [
        "remove-stopped-container",
        "recover-container",
        "cleanup-container",
        "finalize-container",
        "recover-network",
        "cleanup-network",
        "finalize-network",
    ],
)
def test_each_cleanup_persistence_boundary_retains_its_exact_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = _object(
        DirectorySyncCleanupAuthority,
        _descriptor=7,
        _identity=None,
        _state="owned",
        _lock=threading.Lock(),
    )
    failure = CoturnDirectorySyncCleanupRequired(authority)
    if boundary == "remove-stopped-container":
        phase = "remove-container"
        owner._publish("stopped", _object(StoppedCoturnReceipt), StoppedCoturnReceipt)
        owner._publish("clean_exit", _object(CleanCoturnExitReceipt), CleanCoturnExitReceipt)
        monkeypatch.setattr(
            cleanup_module,
            "remove_stopped_owned_container",
            lambda **_kwargs: (_ for _ in ()).throw(failure),
        )
    elif boundary in {"recover-container", "cleanup-container"}:
        phase = "remove-container"
        owner._publish(
            "container_plan",
            _object(ContainerPlan, paths=owner._paths),
            ContainerPlan,
        )
        monkeypatch.setattr(cleanup_module, "_container_recovery_exists", lambda _plan: True)
        if boundary == "recover-container":
            monkeypatch.setattr(
                cleanup_module,
                "recover_container_cleanup_authority",
                lambda **_kwargs: (_ for _ in ()).throw(failure),
            )
        else:
            recovered = _object(RecoveredContainerCleanupAuthority)
            monkeypatch.setattr(
                cleanup_module,
                "recover_container_cleanup_authority",
                lambda **_kwargs: recovered,
            )
            monkeypatch.setattr(
                cleanup_module,
                "cleanup_owned_container",
                lambda **_kwargs: (_ for _ in ()).throw(failure),
            )
    elif boundary == "finalize-container":
        phase = "finalize-container"
        absence = _object(
            ContainerAbsenceReceipt,
            _container_id="a" * 64,
            _finalized=False,
            _lock=threading.Lock(),
            _plan=object(),
        )
        owner._publish("container_absence", absence, ContainerAbsenceReceipt)
        monkeypatch.setattr(
            cleanup_module,
            "finalize_container_absence",
            lambda _receipt: (_ for _ in ()).throw(failure),
        )
    elif boundary in {"recover-network", "cleanup-network"}:
        phase = "remove-network"
        if boundary == "recover-network":
            owner._publish(
                "network_plan",
                _object(NetworkPlan, paths=owner._paths),
                NetworkPlan,
            )
            monkeypatch.setattr(cleanup_module, "_network_recovery_exists", lambda _plan: True)
            monkeypatch.setattr(
                cleanup_module,
                "recover_network_cleanup_authority",
                lambda **_kwargs: (_ for _ in ()).throw(failure),
            )
        else:
            network = OwnedNetwork(
                authority=_object(NetworkCleanupAuthority),  # type: ignore[arg-type]
                validated=object(),  # type: ignore[arg-type]
            )
            owner._publish("network", network, OwnedNetwork)
            monkeypatch.setattr(
                cleanup_module,
                "cleanup_owned_network",
                lambda **_kwargs: (_ for _ in ()).throw(failure),
            )
    else:
        phase = "finalize-network"
        absence = _object(
            NetworkAbsenceReceipt,
            _network_id="b" * 64,
            _finalized=False,
            _lock=threading.Lock(),
            _plan=object(),
        )
        owner._publish("network_absence", absence, NetworkAbsenceReceipt)
        monkeypatch.setattr(
            cleanup_module,
            "finalize_network_absence",
            lambda _receipt: (_ for _ in ()).throw(failure),
        )
    owner._cleanup_phase = phase
    operation = cleanup_module._phase_operation(phase)
    assert operation is not None
    assert operation(owner) is False
    assert owner._cleanup_phase == phase
    assert owner._pending_authority is authority
    assert not owner._terminal_roots_empty()
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_directory_sync_authority",
        lambda candidate: object.__setattr__(candidate, "_state", "cleaned"),
    )
    assert cleanup_module._settle_pending(owner, phase)
    assert owner._pending_authority is None
    owner._cleanup_phase = "settle-runner"
    cleanup_relay_probe(owner)
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "system-exit"])
def test_pending_child_cleanup_failure_keeps_same_root_until_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    authority = _object(
        DirectorySyncCleanupAuthority,
        _descriptor=7,
        _identity=None,
        _state="owned",
        _lock=threading.Lock(),
    )
    authority_module._retain_runtime_persistence_authority(
        owner,
        CoturnDirectorySyncCleanupRequired(authority),
    )
    owner._cleanup_phase = "remove-container"
    calls = 0

    def settle(candidate: object) -> None:
        nonlocal calls
        calls += 1
        assert candidate is authority
        assert owner._pending_authority is authority
        if calls == 1:
            raise _failure_with_authority(
                failure_kind,
                CoturnDirectorySyncCleanupRequired,
                authority,
            )
        object.__setattr__(candidate, "_state", "cleaned")

    monkeypatch.setattr(cleanup_module, "cleanup_directory_sync_authority", settle)
    expected = {
        "ordinary": RelayProbeCleanupRequired,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[failure_kind]
    with pytest.raises(expected) as captured:
        cleanup_relay_probe(owner)
    aggregate = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(aggregate) is RelayProbeCleanupAuthority
    assert owner._pending_authority is authority
    cleanup_relay_probe(aggregate)
    assert calls == 2
    assert owner._pending_authority is None
    assert destination._record is None  # type: ignore[attr-defined]


def test_conflicting_contextual_children_are_queued_without_dropping_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    children = tuple(
        _object(
            DirectorySyncCleanupAuthority,
            _descriptor=descriptor,
            _identity=None,
            _state="owned",
            _lock=threading.Lock(),
        )
        for descriptor in (7, 8)
    )
    for child in children:
        authority_module._retain_runtime_persistence_authority(
            owner,
            CoturnDirectorySyncCleanupRequired(child),
        )
    queued = owner._pending_authority
    assert type(queued) is authority_module._PendingAuthorityQueue
    assert all(queued._contains(child) for child in children)
    settled: list[object] = []

    def settle(candidate: object) -> None:
        object.__setattr__(candidate, "_state", "cleaned")
        settled.append(candidate)

    monkeypatch.setattr(cleanup_module, "cleanup_directory_sync_authority", settle)
    owner._cleanup_phase = "remove-container"
    cleanup_relay_probe(owner)
    assert settled == list(children)
    assert owner._pending_authority is None
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("authority_kind", ["lifetime", "private"])
@pytest.mark.parametrize("failure_kind", ["ordinary", "keyboard", "system-exit"])
def test_tls_generation_retains_only_its_contextual_raw_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_kind: str,
    failure_kind: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    if authority_kind == "lifetime":
        authority: TlsMaterialLifetimeAuthority | PrivateDescriptorCleanupAuthority = (
            new_tls_material_lifetime_authority()
        )
        wrapper = CoturnTlsCleanupRequired
        cleanup_name = "cleanup_tls_material_authority"
    else:
        authority = new_private_descriptor_cleanup_authority()
        assert authority.begin()
        wrapper = CoturnTlsPrivateCleanupRequired
        cleanup_name = "cleanup_tls_private_authority"
    failure = _failure_with_authority(failure_kind, wrapper, authority)
    monkeypatch.setattr(
        forward_module,
        "generate_runtime_tls_material",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )
    settled: list[object] = []

    def settle(candidate: object) -> None:
        assert candidate is authority
        object.__setattr__(candidate, "_state", "cleaned")
        settled.append(candidate)

    monkeypatch.setattr(cleanup_module, cleanup_name, settle)
    expected = {
        "ordinary": RelayProbeOwnerError,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[failure_kind]
    with pytest.raises(expected) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=SECRET,
            now=datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    if failure_kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    assert settled == [authority]
    assert destination._record is None  # type: ignore[attr-defined]
    assert owner._terminal_roots_empty()

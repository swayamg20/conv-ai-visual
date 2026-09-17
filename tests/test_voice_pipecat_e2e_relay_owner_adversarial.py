"""Adversarial public-boundary and capability tests for the relay owner."""
# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_owner as owner_module
import scripts.voice_pipecat_e2e_relay_owner_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_owner_forward as forward_module
import scripts.voice_pipecat_e2e_relay_owner_state as state_module
from scripts.voice_pipecat_e2e_coturn import CoturnContractPaths
from scripts.voice_pipecat_e2e_coturn_host import (
    CoturnRuntimePaths,
    RuntimeIdentity,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (
    AttachedCoturnProcess,
    CoturnAttachedCleanupRequired,
    OwnedContainer,
    RuntimeTlsMaterial,
    cleanup_unpublished_attached,
)
from scripts.voice_pipecat_e2e_coturn_runtime_drain import AttachedCoturnEvidenceDrain
from scripts.voice_pipecat_e2e_coturn_runtime_drain_registry import (
    CoturnEvidenceDrainCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import AttachedCoturnEvidencePump
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    _new_unpublished_attached_cleanup_authority,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationCleanupAuthority,
    RelayInvocationOwner,
)
from scripts.voice_pipecat_e2e_relay_owner import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeOwnerError,
    cleanup_relay_probe,
    new_relay_probe_owner,
    new_relay_probe_owner_destination,
    run_relay_probe,
)
from tests.coturn_traceback_helpers import traceback_contains
from tests.test_voice_pipecat_e2e_relay_owner import (
    SECRET,
    _install_synthetic_lifecycle,
    _object,
)
from tests.test_voice_pipecat_e2e_relay_owner_recovery import _factory_context


def _raise_failure(kind: str) -> None:
    if kind == "ordinary":
        raise RuntimeError("raw-public-boundary")
    if kind == "keyboard":
        raise KeyboardInterrupt("raw-public-boundary")
    raise SystemExit(73)


@pytest.mark.parametrize("boundary", ["resolve-entry", "resolve-return", "lock-entry"])
@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_run_public_entry_is_sanitized_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    original_resolve = owner_module._resolve_owner
    original_lock = owner._operation_lock
    if boundary.startswith("resolve"):

        def resolve(value: object) -> object:
            if boundary == "resolve-return":
                original_resolve(value)
            _raise_failure(kind)

        monkeypatch.setattr(owner_module, "_resolve_owner", resolve)
    else:

        class HostileLock:
            def __enter__(self) -> None:
                _raise_failure(kind)

            def __exit__(self, *_args: object) -> None:
                return None

        object.__setattr__(owner, "_operation_lock", HostileLock())

    expected = {
        "ordinary": RelayProbeCleanupRequired,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=f"{SECRET}-TOP-SECRET-SENTINEL",
            now=__import__("datetime").datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    assert state_module._resolve_owner(authority) is owner
    if kind == "keyboard":
        assert str(captured.value) == ""
    elif kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    assert not traceback_contains(
        captured.value,
        f"{SECRET}-TOP-SECRET-SENTINEL",
        str(values["paths"].control_dir),  # type: ignore[union-attr]
    )
    monkeypatch.setattr(owner_module, "_resolve_owner", original_resolve)
    object.__setattr__(owner, "_operation_lock", original_lock)
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_cleanup_public_resolution_is_sanitized_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    original = owner_module._resolve_owner
    monkeypatch.setattr(owner_module, "_resolve_owner", lambda _value: _raise_failure(kind))
    expected = {
        "ordinary": RelayProbeCleanupRequired,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        cleanup_relay_probe(owner)
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    monkeypatch.setattr(owner_module, "_resolve_owner", original)
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("api", ["run", "cleanup"])
@pytest.mark.parametrize("boundary", ["resolve-entry", "resolve-return", "lock-entry"])
@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_keyword_owner_public_entry_keeps_exact_cleanup_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: str,
    boundary: str,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    original_resolve = owner_module._resolve_owner
    original_lock = owner._operation_lock
    if boundary.startswith("resolve"):

        def resolve(value: object) -> object:
            if boundary == "resolve-return":
                original_resolve(value)
            _raise_failure(kind)

        monkeypatch.setattr(owner_module, "_resolve_owner", resolve)
    else:

        class HostileLock:
            def __enter__(self) -> None:
                _raise_failure(kind)

            def __exit__(self, *_args: object) -> None:
                return None

        object.__setattr__(owner, "_operation_lock", HostileLock())
    expected = {
        "ordinary": RelayProbeCleanupRequired,
        "keyboard": KeyboardInterrupt,
        "system-exit": SystemExit,
    }[kind]
    with pytest.raises(expected) as captured:
        if api == "run":
            run_relay_probe(
                owner=owner,
                static_auth_secret=f"{SECRET}-KEYWORD-SENTINEL",
                now=__import__("datetime").datetime(2026, 8, 19),
                browser_timeout_seconds=5.0,
            )
        else:
            cleanup_relay_probe(owner=owner)
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    assert state_module._resolve_owner(authority) is owner
    if kind == "system-exit":
        assert captured.value.code == 73  # type: ignore[attr-defined]
    assert not traceback_contains(
        captured.value,
        f"{SECRET}-KEYWORD-SENTINEL",
        str(values["paths"].control_dir),  # type: ignore[union-attr]
    )
    monkeypatch.setattr(owner_module, "_resolve_owner", original_resolve)
    object.__setattr__(owner, "_operation_lock", original_lock)
    cleanup_relay_probe(authority)
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("position", ["before-store", "after-store"])
@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_factory_registration_failure_never_strands_destination_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: str,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    baseline = len(state_module._REGISTRY)
    original = state_module._register_owner

    def register(*args: object) -> None:
        if position == "before-store":
            _raise_failure(kind)
        original(*args)  # type: ignore[arg-type]
        _raise_failure(kind)

    monkeypatch.setattr(state_module, "_register_owner", register)
    expected = {
        ("before-store", "ordinary"): RelayProbeOwnerError,
        ("after-store", "ordinary"): RelayProbeCleanupRequired,
    }.get((position, kind), KeyboardInterrupt if kind == "keyboard" else SystemExit)
    with pytest.raises(expected) as captured:
        new_relay_probe_owner(**values)  # type: ignore[arg-type]
    assert destination._record is None  # type: ignore[attr-defined]
    if position == "before-store":
        assert len(state_module._REGISTRY) == baseline
        assert not hasattr(captured.value, "cleanup_authority")
        return
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    retained = state_module._resolve_owner(authority)
    assert retained is not None and retained._cleanup_only
    monkeypatch.setattr(state_module, "_register_owner", original)
    cleanup_relay_probe(authority)
    assert len(state_module._REGISTRY) == baseline


@pytest.mark.parametrize("different_callbacks", [False, True])
def test_active_resource_identity_is_unique_across_destinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    different_callbacks: bool,
) -> None:
    events: list[str] = []
    _destination, values = _factory_context(tmp_path, monkeypatch, events)
    first = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    second_values = dict(values)
    second_destination = new_relay_probe_owner_destination()
    second_values["destination"] = second_destination
    if different_callbacks:
        second_values["wait"] = lambda _seconds: None
    with pytest.raises(RelayProbeOwnerError):
        new_relay_probe_owner(**second_values)  # type: ignore[arg-type]
    assert second_destination._record is None  # type: ignore[attr-defined]
    assert first._state == "created"
    assert not first._cleanup_only
    cleanup_relay_probe(first)


def test_active_docker_identity_is_unique_across_different_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_events: list[str] = []
    _destination, first_values = _factory_context(first_root, monkeypatch, first_events)
    first = new_relay_probe_owner(**first_values)  # type: ignore[arg-type]
    second_events: list[str] = []
    second_destination, second_values = _factory_context(
        second_root,
        monkeypatch,
        second_events,
    )
    second_values["identity"] = first_values["identity"]
    with pytest.raises(RelayProbeOwnerError):
        new_relay_probe_owner(**second_values)  # type: ignore[arg-type]
    assert second_destination._record is None  # type: ignore[attr-defined]
    cleanup_relay_probe(first)


def test_active_docker_name_prefix_collision_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_events: list[str] = []
    _destination, first_values = _factory_context(first_root, monkeypatch, first_events)
    first = new_relay_probe_owner(**first_values)  # type: ignore[arg-type]
    second_events: list[str] = []
    second_destination, second_values = _factory_context(
        second_root,
        monkeypatch,
        second_events,
    )
    paths = second_values["paths"]
    second_values["identity"] = RuntimeIdentity.create(
        run_id=paths.contract.run_id,  # type: ignore[union-attr]
        owner_nonce="ab" * 6 + "cd" * 26,
    )
    with pytest.raises(RelayProbeOwnerError):
        new_relay_probe_owner(**second_values)  # type: ignore[arg-type]
    assert second_destination._record is None  # type: ignore[attr-defined]
    cleanup_relay_probe(first)


@pytest.mark.parametrize("alias_kind", ["dotdot", "symlink-parent"])
def test_active_physical_control_directory_alias_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    events: list[str] = []
    _destination, values = _factory_context(shared, monkeypatch, events)
    first = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    if alias_kind == "dotdot":
        lexical_parent = tmp_path / "unused"
        lexical_parent.mkdir()
        alias_run_dir = lexical_parent / ".." / "shared" / "relay-test"
    else:
        lexical_parent = tmp_path / "shared-link"
        lexical_parent.symlink_to(shared, target_is_directory=True)
        alias_run_dir = lexical_parent / "relay-test"
    contract = CoturnContractPaths.for_run_dir("relay-test", alias_run_dir)
    alias_paths = CoturnRuntimePaths.for_contract(contract)
    assert alias_paths.control_dir.resolve(strict=True) == values["paths"].control_dir.resolve(  # type: ignore[union-attr]
        strict=True
    )
    second_destination = new_relay_probe_owner_destination()
    second = dict(values)
    second["destination"] = second_destination
    second["paths"] = alias_paths
    second["identity"] = RuntimeIdentity.create(
        run_id=contract.run_id,
        owner_nonce="cd" * 32,
    )
    with pytest.raises(RelayProbeOwnerError):
        new_relay_probe_owner(**second)  # type: ignore[arg-type]
    assert second_destination._record is None  # type: ignore[attr-defined]
    assert first._state == "created"
    assert not first._cleanup_only
    cleanup_relay_probe(first)


@pytest.mark.parametrize(
    "authority_type",
    [
        AttachedCoturnProcess,
        RelayInvocationCleanupAuthority,
        CoturnEvidenceDrainCleanupAuthority,
        RuntimeTlsMaterial,
    ],
)
def test_foreign_child_authority_on_runner_error_is_never_adopted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_type: type[object],
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    foreign = _object(authority_type)
    error = RuntimeError("raw-hostile-runner")
    error.cleanup_authority = foreign  # type: ignore[attr-defined]
    runner = owner._runner
    original_settle = runner.settle_owned  # type: ignore[union-attr]
    runner.settle_owned = lambda: (_ for _ in ()).throw(error)  # type: ignore[method-assign,union-attr]
    called: list[object] = []
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_relay_invocation",
        lambda candidate: called.append(candidate),
    )
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_attached_coturn_evidence_drain",
        lambda candidate: called.append(candidate),
    )
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_runtime_tls_material",
        lambda candidate, **_kwargs: called.append(candidate),
    )
    monkeypatch.setattr(
        AttachedCoturnProcess,
        "terminate",
        lambda candidate: called.append(candidate),
    )
    with pytest.raises(RelayProbeCleanupRequired) as captured:
        cleanup_relay_probe(owner)
    assert owner._pending_authority is None
    assert foreign not in called
    runner.settle_owned = original_settle  # type: ignore[method-assign,union-attr]
    cleanup_relay_probe(captured.value.cleanup_authority)
    assert destination._record is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system-exit"])
def test_attached_start_adopts_only_its_context_bound_unpublished_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    events: list[str] = []
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    process = _object(AttachedCoturnProcess)
    pump = _object(AttachedCoturnEvidencePump)
    drain = _object(AttachedCoturnEvidenceDrain)
    container = _object(OwnedContainer, validated=object())
    owner._publish("process", process, AttachedCoturnProcess)
    owner._publish("pump", pump, AttachedCoturnEvidencePump)
    owner._publish("drain", drain, AttachedCoturnEvidenceDrain)
    owner._publish("container", container, OwnedContainer)
    runner = owner._runner
    authority = _new_unpublished_attached_cleanup_authority(runner)

    class Handle:
        terminations = 0

        def terminate(self) -> None:
            self.terminations += 1

    handle = Handle()
    assert authority._adopt(handle)
    failure = CoturnAttachedCleanupRequired(authority)
    if kind == "keyboard":
        failure = KeyboardInterrupt("raw-attached-start")  # type: ignore[assignment]
        failure.cleanup_authority = authority  # type: ignore[attr-defined]
    elif kind == "system-exit":
        failure = SystemExit(73)  # type: ignore[assignment]
        failure.cleanup_authority = authority  # type: ignore[attr-defined]

    monkeypatch.setattr(forward_module, "_adopt_expected_turn_username", lambda *_args: None)
    monkeypatch.setattr(
        forward_module,
        "start_owned_container_attached",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(type(failure)) as captured:
        forward_module._adopt_username_and_start_coturn(
            owner,
            _object(RelayInvocationOwner),  # type: ignore[arg-type]
        )
    assert owner._pending_authority is authority
    owner._remember_exception(captured.value)
    owner._cleanup_phase = "drain"
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_pump",
        lambda _process, retained: retained,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_drain",
        lambda _process, _pump, retained, **_kwargs: retained,
    )
    monkeypatch.setattr(
        cleanup_module,
        "cleanup_attached_coturn_evidence_drain",
        lambda _drain: None,
    )
    if kind == "ordinary":
        cleanup_relay_probe(owner)
    else:
        with pytest.raises(KeyboardInterrupt if kind == "keyboard" else SystemExit):
            cleanup_relay_probe(owner)
    assert authority._state == "settled"
    assert handle.terminations == 1
    assert owner._pending_authority is None
    assert destination._record is None  # type: ignore[attr-defined]
    cleanup_unpublished_attached(authority)


@pytest.mark.parametrize("kind", ["keyboard", "system-exit"])
def test_source_control_publishes_nothing_and_leaves_no_retry_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    destination, values = _factory_context(tmp_path, monkeypatch, events)
    owner = new_relay_probe_owner(**values)  # type: ignore[arg-type]
    calls = 0

    def source_control(_run: object) -> None:
        nonlocal calls
        calls += 1
        _raise_failure(kind)

    monkeypatch.setattr(state_module, "revalidate_relay_probe_source", source_control)
    expected = KeyboardInterrupt if kind == "keyboard" else SystemExit
    with pytest.raises(expected) as captured:
        run_relay_probe(
            owner,
            static_auth_secret=SECRET,
            now=__import__("datetime").datetime(2026, 8, 19),
            browser_timeout_seconds=5.0,
        )
    assert calls == 1
    assert not hasattr(captured.value, "cleanup_authority")
    assert destination._record is None  # type: ignore[attr-defined]
    assert owner._state == "cleaned"
    assert owner._observation is None
    assert owner._terminal_roots_empty()

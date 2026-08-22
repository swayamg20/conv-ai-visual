"""Deterministic deadline and result-loss cuts for the private build handoff."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_facade as process_facade
import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as process_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as process_state
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process as build_process
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract as process_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_publication as build_publication
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_contract as receipt_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values as build_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module


@pytest.fixture(autouse=True)
def _isolated_deadline_contract() -> None:
    mappings = (
        build_values._COMMANDS,
        build_values._COMMAND_CONTROLLERS,
        build_values._COMMAND_GATES,
        build_values._CONTROLLER_COMMANDS,
        build_values._PROCESS_ASSOCIATIONS,
        build_receipt._BUILT_LEASES,
        build_receipt._BUILT_BY_COMMAND,
        fs_contract._LEASES,
        fs_contract._PREPARED_BUILDS,
        process_registry._OWNERS,
        process_registry._KERNELS,
        process_state._AUTHORITY_BINDINGS,
    )
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _command(*, owner_token: object | None = None, record_token: object | None = None):
    owner = object() if owner_token is None else owner_token
    record = object() if record_token is None else record_token
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner,
        record_token=record,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(prepared, owner, record)
    deadline = float(time.monotonic() + 30.0)
    command = build_values._new_workspace_build_command(
        owner_token=owner,
        record_token=record,
        prepared=prepared,
        build_deadline=deadline,
        expected_spawn_fingerprint=b"s" * 32,
    )
    assert (
        build_values._claim_workspace_build_command(
            command,
            owner_token=owner,
            record_token=record,
            prepared=prepared,
        )
        == deadline
    )
    return owner, record, prepared, command, deadline


def _running_command(*, owner_token: object | None = None, record_token: object | None = None):
    owner, record, prepared, command, deadline = _command(
        owner_token=owner_token,
        record_token=record_token,
    )
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner,
    )
    assert build_values._bind_workspace_build_command_controller(
        command,
        controller=controller,
        owner_token=owner,
        record_token=record,
        build_deadline=deadline,
    )
    assert build_values._intend_workspace_build_process_start(
        command,
        owner_token=owner,
        record_token=record,
        build_deadline=deadline,
    )
    assert build_values._complete_workspace_build_process_start(
        command,
        owner_token=owner,
        record_token=record,
        build_deadline=deadline,
    )
    process_owner_token = object()
    authority = process_state._RelayLinuxBuildCleanupAuthority(
        process_state._AUTHORITY_TOKEN,
        key=object(),
        owner_token=process_owner_token,
    )
    process_receipt = process_state._RelayLinuxBuildProcessReceipt(
        process_state._RECEIPT_TOKEN,
        owner_token=process_owner_token,
    )
    build_values._PROCESS_ASSOCIATIONS[command] = (
        owner,
        record,
        process_owner_token,
        authority,
        b"s" * 32,
        process_receipt,
        "released-zero",
    )
    return (
        owner,
        record,
        prepared,
        command,
        deadline,
        controller,
        authority,
        process_receipt,
    )


def _driver_harness(monkeypatch: pytest.MonkeyPatch):
    owner, record, _prepared, command, deadline = _command()
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner,
    )
    process_owner_token = object()
    authority = process_state._RelayLinuxBuildCleanupAuthority(
        process_state._AUTHORITY_TOKEN,
        key=object(),
        owner_token=process_owner_token,
    )
    process_receipt = process_state._RelayLinuxBuildProcessReceipt(
        process_state._RECEIPT_TOKEN,
        owner_token=process_owner_token,
    )

    class Candidate:
        _cleanup_authority = authority

    candidate = Candidate()

    class Destination:
        def _read(self) -> object:
            return candidate

    class Request:
        _next_cli = object()
        _node = object()
        _run_id = "deadline-cut"
        _workspace = object()

        def _environment_values(self) -> dict[str, str]:
            return {}

    present = [True]
    calls: list[str] = []
    clock = _Clock(deadline - 10.0)
    monkeypatch.setattr(build_process, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(build_process, "_new_relay_linux_build_spec", lambda **_kw: object())
    monkeypatch.setattr(build_process, "_new_raw_build_process_destination", lambda _s: object())
    monkeypatch.setattr(
        build_process,
        "_new_build_owner_destination",
        lambda _s, _r: Destination(),
    )

    def intend(*_args: object, **_kwargs: object) -> object:
        build_values._PROCESS_ASSOCIATIONS[command] = (
            owner,
            record,
            process_owner_token,
            authority,
            b"s" * 32,
            None,
            "preown-intended",
        )
        return authority

    def associate(*_args: object, **_kwargs: object) -> object:
        build_values._PROCESS_ASSOCIATIONS[command] = (
            owner,
            record,
            process_owner_token,
            authority,
            b"s" * 32,
            None,
            "associated",
        )
        return authority

    def observe(*_args: object, **_kwargs: object) -> bool:
        calls.append("observe")
        state = build_values._PROCESS_ASSOCIATIONS[command]
        build_values._PROCESS_ASSOCIATIONS[command] = (*state[:5], process_receipt, "zero-observed")
        return True

    def release(_value: object, *, cleanup_deadline: float) -> None:
        assert cleanup_deadline > clock.now
        calls.append("release")
        present[0] = False

    monkeypatch.setattr(build_process, "_intend_workspace_build_process_association", intend)
    monkeypatch.setattr(build_process, "_preown_build_process", lambda **_kw: candidate)
    monkeypatch.setattr(build_process, "_associate_workspace_build_process", associate)
    monkeypatch.setattr(
        build_process,
        "_workspace_build_process_association_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(build_process, "_start_relay_linux_build_process", lambda *_a, **_k: None)
    monkeypatch.setattr(build_process, "_build_process_worker_status", lambda _o: "settled")
    monkeypatch.setattr(
        build_process,
        "_join_relay_linux_build_process",
        lambda *_a, **_k: calls.append("join"),
    )
    monkeypatch.setattr(
        build_process,
        "_relay_linux_build_process_result",
        lambda _o: calls.append("result") or process_receipt,
    )
    monkeypatch.setattr(build_process, "_observe_workspace_build_process_zero", observe)
    monkeypatch.setattr(build_process, "_release_relay_linux_build_process", release)
    monkeypatch.setattr(
        build_process,
        "_resolve_build_process_owner",
        lambda _value: candidate if present[0] else None,
    )
    return {
        "authority": authority,
        "calls": calls,
        "candidate": candidate,
        "clock": clock,
        "command": command,
        "controller": controller,
        "deadline": deadline,
        "owner": owner,
        "present": present,
        "process_receipt": process_receipt,
        "record": record,
        "request": Request(),
    }


@pytest.mark.parametrize("liveness_cut", range(1, 8))
def test_driver_rejects_each_late_result_boundary_and_releases_process(
    monkeypatch: pytest.MonkeyPatch,
    liveness_cut: int,
) -> None:
    context = _driver_harness(monkeypatch)
    original = build_process._require_live_workspace_build_result
    seen = 0

    def expire(*args: object, **kwargs: object) -> float:
        nonlocal seen
        seen += 1
        if seen == liveness_cut:
            context["clock"].now = context["deadline"]
        return original(*args, **kwargs)

    monkeypatch.setattr(build_process, "_require_live_workspace_build_result", expire)
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_process._drive_workspace_build_process(
            command=context["command"],
            request=context["request"],
            controller=context["controller"],
            owner_token=context["owner"],
            record_token=context["record"],
            build_deadline=context["deadline"],
        )

    association = build_values._PROCESS_ASSOCIATIONS[context["command"]]
    assert context["present"] == [False]
    assert association[5] is None and association[6] == "released-failed"
    assert seen == liveness_cut
    assert "join" in context["calls"] if liveness_cut >= 3 else "join" not in context["calls"]
    assert "observe" in context["calls"] if liveness_cut >= 5 else "observe" not in context["calls"]


def test_cancellation_after_zero_observation_clears_receipt_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _driver_harness(monkeypatch)
    original = build_process._require_live_workspace_build_result
    seen = 0

    def cancel_after_zero(*args: object, **kwargs: object) -> float:
        nonlocal seen
        seen += 1
        if seen == 5:
            context["controller"]._request_cancel()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        build_process,
        "_require_live_workspace_build_result",
        cancel_after_zero,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_process._drive_workspace_build_process(
            command=context["command"],
            request=context["request"],
            controller=context["controller"],
            owner_token=context["owner"],
            record_token=context["record"],
            build_deadline=context["deadline"],
        )

    association = build_values._PROCESS_ASSOCIATIONS[context["command"]]
    assert context["present"] == [False]
    assert association[5] is None and association[6] == "released-failed"


@pytest.mark.parametrize("late", ["deadline", "cancel"])
def test_completion_return_crossing_final_boundary_never_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    late: str,
) -> None:
    context = _driver_harness(monkeypatch)
    original = build_process._complete_workspace_build_process

    def complete_then_late(*args: object, **kwargs: object) -> bool:
        result = original(*args, **kwargs)
        if late == "deadline":
            context["clock"].now = context["deadline"]
        else:
            context["controller"]._request_cancel()
        return result

    monkeypatch.setattr(
        build_process,
        "_complete_workspace_build_process",
        complete_then_late,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_process._drive_workspace_build_process(
            command=context["command"],
            request=context["request"],
            controller=context["controller"],
            owner_token=context["owner"],
            record_token=context["record"],
            build_deadline=context["deadline"],
        )

    association = build_values._PROCESS_ASSOCIATIONS[context["command"]]
    assert context["present"] == [False]
    assert association[5] is None and association[6] == "released-failed"


def test_process_release_stored_absence_then_raised_still_settles_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _driver_harness(monkeypatch)
    faulted = False

    def release_then_raise(_value: object, *, cleanup_deadline: float) -> None:
        nonlocal faulted
        assert cleanup_deadline > context["clock"].now
        context["present"][0] = False
        if not faulted:
            faulted = True
            raise OSError("synthetic process-release return loss")

    monkeypatch.setattr(build_process, "_release_relay_linux_build_process", release_then_raise)
    with pytest.raises(OSError):
        build_process._drive_workspace_build_process(
            command=context["command"],
            request=context["request"],
            controller=context["controller"],
            owner_token=context["owner"],
            record_token=context["record"],
            build_deadline=context["deadline"],
        )

    association = build_values._PROCESS_ASSOCIATIONS[context["command"]]
    assert faulted is True and context["present"] == [False]
    assert association[5] is None and association[6] == "released-failed"


@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt, SystemExit])
def test_released_zero_store_return_loss_converges_failed_and_preserves_first_control(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException],
) -> None:
    context = _driver_harness(monkeypatch)
    original_store = process_contract._store_workspace_build_process_association
    faulted = False

    def store_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal faulted
        original_store(*args, **kwargs)
        value = args[1]
        if not faulted and value[6] == "released-zero":
            faulted = True
            raise fault("synthetic released-zero return loss")

    later_control = False
    original_failed = build_process._complete_failed_workspace_build_process

    def interrupt_cleanup_once(command: object) -> bool:
        nonlocal later_control
        if not later_control and fault in {KeyboardInterrupt, SystemExit}:
            later_control = True
            raise SystemExit(99) if fault is KeyboardInterrupt else KeyboardInterrupt()
        return original_failed(command)

    monkeypatch.setattr(
        process_contract,
        "_store_workspace_build_process_association",
        store_then_raise,
    )
    monkeypatch.setattr(
        build_process,
        "_complete_failed_workspace_build_process",
        interrupt_cleanup_once,
    )
    expected = (
        build_values._WorkspaceBuildHandoffError
        if fault in {KeyboardInterrupt, SystemExit}
        else OSError
    )
    with pytest.raises(expected):
        build_process._drive_workspace_build_process(
            command=context["command"],
            request=context["request"],
            controller=context["controller"],
            owner_token=context["owner"],
            record_token=context["record"],
            build_deadline=context["deadline"],
        )

    association = build_values._PROCESS_ASSOCIATIONS[context["command"]]
    assert faulted is True and context["present"] == [False]
    assert association[5] is None and association[6] == "released-failed"
    if fault in {KeyboardInterrupt, SystemExit}:
        signal = context["controller"]._control_value()
        assert signal is not None
        assert signal.kind == ("keyboard" if fault is KeyboardInterrupt else "system-exit")


@pytest.mark.parametrize("phase", ["zero-observed", "released-zero"])
@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt, SystemExit])
def test_failed_result_store_return_loss_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    fault: type[BaseException],
) -> None:
    context = _running_command()
    command, process_receipt = context[3], context[7]
    state = build_values._PROCESS_ASSOCIATIONS[command]
    build_values._PROCESS_ASSOCIATIONS[command] = (*state[:5], process_receipt, phase)
    original = process_contract._store_workspace_build_process_association
    faulted = False

    def store_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal faulted
        original(*args, **kwargs)
        if not faulted:
            faulted = True
            raise fault("synthetic failed-result return loss")

    monkeypatch.setattr(
        process_contract,
        "_store_workspace_build_process_association",
        store_then_raise,
    )
    with pytest.raises(fault):
        process_contract._complete_failed_workspace_build_process(command)
    assert process_contract._complete_failed_workspace_build_process(command)
    final = build_values._PROCESS_ASSOCIATIONS[command]
    assert faulted is True
    assert final[5] is None and final[6] == "released-failed"


def test_failed_result_reconciliation_requires_global_process_absence(
    tmp_path: Path,
) -> None:
    context = _running_command()
    command, process_receipt = context[3], context[7]
    state = build_values._PROCESS_ASSOCIATIONS[command]
    build_values._PROCESS_ASSOCIATIONS[command] = (
        *state[:5],
        process_receipt,
        "zero-observed",
    )
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "foreign-node").resolve(),
        next_cli=(tmp_path / "foreign-next").resolve(),
        workspace=(tmp_path / "foreign-workspace").resolve(),
        run_id="foreign-owner",
        environment={
            **spawn_module._FIXED_BUILD_ENVIRONMENT,
            "VOICE_E2E_NEXT_DIST_DIR": ".next-voice-e2e/foreign-owner",
        },
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = process_registry._new_build_owner_destination(spec, raw)
    foreign = process_registry._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )

    assert not process_contract._complete_failed_workspace_build_process(command)
    assert build_values._PROCESS_ASSOCIATIONS[command][6] == "zero-observed"
    process_facade._release_relay_linux_build_process(
        foreign,
        cleanup_deadline=float(time.monotonic() + 2.0),
    )
    assert process_contract._complete_failed_workspace_build_process(command)
    assert build_values._PROCESS_ASSOCIATIONS[command][6] == "released-failed"


def _built_context():
    context = _running_command()
    owner, record, _prepared, command, deadline, _controller, _authority, receipt = context
    return context, _Clock(deadline - 10.0), owner, record, command, deadline, receipt


def _patch_built_clock(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    fake = SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(receipt_contract, "time", fake)


@pytest.mark.parametrize(
    "tamper",
    [
        "association-command",
        "association-deadline",
        "association-phase",
        "internal-active",
        "internal-fingerprint",
        "internal-owner",
        "internal-record",
        "internal-status",
        "lease-fingerprint",
        "orphan-prepared",
    ],
)
def test_built_creation_requires_exact_single_prepared_association(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    context, clock, owner, record, command, deadline, process_receipt = _built_context()
    prepared = context[2]
    _patch_built_clock(monkeypatch, clock)
    association = fs_contract._PREPARED_BUILDS[prepared]
    if tamper == "association-command":
        fs_contract._PREPARED_BUILDS[prepared] = (*association[:2], object(), *association[3:])
    elif tamper == "association-deadline":
        fs_contract._PREPARED_BUILDS[prepared] = (*association[:3], deadline + 1.0, "building")
    elif tamper == "association-phase":
        fs_contract._PREPARED_BUILDS[prepared] = (*association[:4], "intended")
    elif tamper == "internal-active":
        object.__setattr__(prepared, "_lease_active", True)
    elif tamper == "internal-fingerprint":
        object.__setattr__(prepared, "_fingerprint", b"x" * 32)
    elif tamper == "internal-owner":
        object.__setattr__(prepared, "_owner_token", object())
    elif tamper == "internal-record":
        object.__setattr__(prepared, "_record_token", object())
    elif tamper == "internal-status":
        object.__setattr__(prepared, "status", "tampered")
    elif tamper == "lease-fingerprint":
        fs_contract._LEASES[prepared] = (owner, record, b"x" * 32, "building")
    else:
        orphan = fs_contract._new_workspace_prepared_receipt(
            owner_token=object(),
            record_token=object(),
            fingerprint=b"x" * 32,
        )
        fs_contract._PREPARED_BUILDS[orphan] = (
            object(),
            object(),
            object(),
            deadline,
            "building",
        )

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
    assert command not in build_receipt._BUILT_BY_COMMAND
    assert not build_receipt._BUILT_LEASES


def test_active_built_receipt_fails_closed_after_prepared_association_drift() -> None:
    context, _clock, owner, record, command, deadline, process_receipt = _built_context()
    prepared = context[2]
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    assert build_receipt._activate_workspace_built_receipt(
        built,
        owner,
        record,
        operation_deadline=deadline,
    )
    association = fs_contract._PREPARED_BUILDS[prepared]
    fs_contract._PREPARED_BUILDS[prepared] = (
        *association[:3],
        deadline + 1.0,
        "building",
    )

    assert not built._matches(owner, record, require_active=True)
    assert build_receipt._BUILT_LEASES[built][5] == "active"


def _expire_after_armed_process_proof(
    monkeypatch: pytest.MonkeyPatch,
    *,
    armed: list[bool],
    clock: _Clock,
    deadline: float,
) -> None:
    original = process_contract._workspace_build_process_completed_zero

    def prove_then_expire(*args: object, **kwargs: object) -> bool:
        result = original(*args, **kwargs)
        if result and armed[0]:
            clock.now = deadline
        return result

    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        prove_then_expire,
    )


def test_built_create_rejects_process_proof_crossing_canonical_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    armed = [False]
    original_store = build_receipt._store_built_for_command

    def store_and_arm(*args: object, **kwargs: object) -> None:
        original_store(*args, **kwargs)
        armed[0] = True

    monkeypatch.setattr(build_receipt, "_store_built_for_command", store_and_arm)
    _expire_after_armed_process_proof(
        monkeypatch,
        armed=armed,
        clock=clock,
        deadline=deadline,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )

    assert clock.now == deadline
    assert command not in build_receipt._BUILT_BY_COMMAND
    assert not build_receipt._BUILT_LEASES


def test_built_activation_rejects_process_proof_crossing_canonical_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    armed = [False]
    original_store = build_receipt._store_command_state

    def store_and_arm(*args: object, **kwargs: object) -> None:
        original_store(*args, **kwargs)
        armed[0] = True

    monkeypatch.setattr(build_receipt, "_store_command_state", store_and_arm)
    _expire_after_armed_process_proof(
        monkeypatch,
        armed=armed,
        clock=clock,
        deadline=deadline,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._activate_workspace_built_receipt(
            built,
            owner,
            record,
            operation_deadline=deadline,
        )

    assert clock.now == deadline
    assert build_receipt._BUILT_LEASES[built][5] == "revoked"
    assert not built._matches(owner, record, require_active=True)


def test_built_activation_rejects_controller_cancel_latched_by_final_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _clock, owner, record, command, deadline, process_receipt = _built_context()
    controller = context[5]
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )

    class CancelClock:
        armed = False
        cancelled = False

        def monotonic(self) -> float:
            if self.armed and not self.cancelled:
                self.cancelled = True
                controller._request_cancel()
            return deadline - 1.0

    cancel_clock = CancelClock()
    monkeypatch.setattr(
        receipt_contract,
        "time",
        SimpleNamespace(monotonic=cancel_clock.monotonic),
    )
    original_zero = process_contract._workspace_build_process_completed_zero

    def prove_and_arm(*args: object, **kwargs: object) -> bool:
        result = original_zero(*args, **kwargs)
        if result and build_values._COMMANDS[command][4] == "built":
            cancel_clock.armed = True
        return result

    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        prove_and_arm,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._activate_workspace_built_receipt(
            built,
            owner,
            record,
            operation_deadline=deadline,
        )

    assert cancel_clock.cancelled is True
    assert controller._cancellation_requested() is True
    assert build_values._COMMAND_GATES[command].cancel_requested is True
    assert build_receipt._BUILT_LEASES[built][5] == "revoked"
    assert not built._matches(owner, record, require_active=True)


def test_built_publication_rejects_final_proof_crossing_canonical_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    clock = _Clock(deadline - 10.0)
    _patch_built_clock(monkeypatch, clock)
    armed = [False]
    original_activate = build_publication._activate_workspace_built_receipt

    def activate_and_arm(*args: object, **kwargs: object) -> bool:
        result = original_activate(*args, **kwargs)
        armed[0] = True
        return result

    monkeypatch.setattr(
        build_publication,
        "_activate_workspace_built_receipt",
        activate_and_arm,
    )
    _expire_after_armed_process_proof(
        monkeypatch,
        armed=armed,
        clock=clock,
        deadline=deadline,
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_publication._publish_workspace_built_receipt(
            bundle=bundle,
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )

    built = build_receipt._BUILT_BY_COMMAND[command]
    assert clock.now == deadline
    assert build_receipt._BUILT_LEASES[built][5] == "revoked"
    assert not built._matches(owner, record, require_active=True)


def test_built_create_and_existing_replay_reject_canonical_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    clock.now = deadline

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
    assert not built._matches(owner, record)
    assert build_receipt._BUILT_LEASES[built][5] == "pending"


def test_built_create_after_canonical_expiry_has_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    clock.now = deadline
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
    assert command not in build_receipt._BUILT_BY_COMMAND
    assert not build_receipt._BUILT_LEASES


@pytest.mark.parametrize("fault", [None, OSError, KeyboardInterrupt, SystemExit])
def test_built_create_store_crossing_deadline_discards_unpublished_candidate(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException] | None,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    original = build_receipt._store_built_lease
    retained: list[object] = []

    def store_then_expire(receipt: object, value: object) -> None:
        original(receipt, value)
        retained.append(receipt)
        clock.now = deadline
        if fault is not None:
            raise fault("synthetic pending-store return loss")

    monkeypatch.setattr(build_receipt, "_store_built_lease", store_then_expire)
    expected = (
        fault
        if fault in {KeyboardInterrupt, SystemExit}
        else build_values._WorkspaceBuildHandoffError
    )
    with pytest.raises(expected):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
    candidate = retained[0]
    assert not candidate._matches(owner, record, require_active=True)
    assert build_receipt._BUILT_BY_COMMAND.get(command) is None
    assert candidate not in build_receipt._BUILT_LEASES
    assert not build_receipt._BUILT_LEASES


@pytest.mark.parametrize("cut", ["store", "read", "activate"])
def test_built_publication_each_slot_boundary_rejects_expiry(
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    clock = _Clock(deadline - 10.0)
    _patch_built_clock(monkeypatch, clock)
    if cut in {"store", "read"}:
        name = "_publish_before" if cut == "store" else "_read_before"
        original = getattr(bundle._built_destination, name)

        def expire_slot(*args: object, **kwargs: object):
            result = original(*args, **kwargs)
            clock.now = deadline
            return result

        monkeypatch.setattr(
            state_module._WorkspaceWorkerDestination,
            name,
            lambda self, *args, **kwargs: (
                expire_slot(*args, **kwargs)
                if self is bundle._built_destination
                else original(*args, **kwargs)
            ),
        )
    else:
        original_store = build_receipt._store_built_lease

        def expire_activation(receipt: object, value: tuple[object, ...]) -> None:
            original_store(receipt, value)
            if value[5] == "active":
                clock.now = deadline

        monkeypatch.setattr(build_receipt, "_store_built_lease", expire_activation)

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_publication._publish_workspace_built_receipt(
            bundle=bundle,
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )

    candidate = build_receipt._BUILT_BY_COMMAND[command]
    assert build_receipt._BUILT_LEASES[candidate][5] == "revoked"
    assert not candidate._matches(owner, record, require_active=True)


@pytest.mark.parametrize("stage", ["publish", "read"])
@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt, SystemExit])
def test_built_slot_failure_revokes_candidate_and_preserves_fault(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    fault: type[BaseException],
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    clock = _Clock(deadline - 10.0)
    _patch_built_clock(monkeypatch, clock)
    name = "_publish_before" if stage == "publish" else "_read_before"
    original = getattr(state_module._WorkspaceWorkerDestination, name)

    def fail_exact_slot(self: object, *args: object, **kwargs: object):
        if self is bundle._built_destination:
            raise fault("synthetic built-slot failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(state_module._WorkspaceWorkerDestination, name, fail_exact_slot)
    expected = (
        build_values._WorkspaceBuildHandoffError
        if stage == "publish" and fault is OSError
        else fault
    )
    with pytest.raises(expected):
        build_publication._publish_workspace_built_receipt(
            bundle=bundle,
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )

    candidate = build_receipt._BUILT_BY_COMMAND[command]
    assert build_receipt._BUILT_LEASES[candidate][5] == "revoked"
    assert not candidate._matches(owner, record, require_active=True)


@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt, SystemExit])
def test_built_read_stored_effect_return_loss_is_reconciled_or_revoked(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException],
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    clock = _Clock(deadline - 10.0)
    _patch_built_clock(monkeypatch, clock)
    original = state_module._WorkspaceWorkerDestination._read_before
    faulted = False

    def read_then_raise(self: object, *args: object, **kwargs: object):
        nonlocal faulted
        result = original(self, *args, **kwargs)
        if self is bundle._built_destination and not faulted:
            faulted = True
            raise fault("synthetic built-read return loss")
        return result

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_read_before",
        read_then_raise,
    )
    if fault is OSError:
        built = build_publication._publish_workspace_built_receipt(
            bundle=bundle,
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
        assert built._matches(owner, record, require_active=True)
    else:
        with pytest.raises(fault):
            build_publication._publish_workspace_built_receipt(
                bundle=bundle,
                command=command,
                owner_token=owner,
                record_token=record,
                output_digest=b"o" * 32,
                process_receipt=process_receipt,
                operation_deadline=deadline,
            )
        built = build_receipt._BUILT_BY_COMMAND[command]
        assert build_receipt._BUILT_LEASES[built][5] == "revoked"
        assert not built._matches(owner, record, require_active=True)
    assert faulted is True


@pytest.mark.parametrize("fault", [OSError, KeyboardInterrupt, SystemExit])
def test_active_publication_replay_fault_never_revokes_committed_receipt(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException],
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    built = build_publication._publish_workspace_built_receipt(
        bundle=bundle,
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    original = state_module._WorkspaceWorkerDestination._read_before

    def fail_exact_slot(self: object, *args: object, **kwargs: object):
        if self is bundle._built_destination:
            raise fault("synthetic active-replay read failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_read_before",
        fail_exact_slot,
    )
    with pytest.raises(fault):
        build_publication._publish_workspace_built_receipt(
            bundle=bundle,
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )

    assert build_receipt._BUILT_BY_COMMAND[command] is built
    assert build_receipt._BUILT_LEASES[built][5] == "active"
    assert built._matches(owner, record, require_active=True)


def test_overlapping_publication_replay_cannot_rollback_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = object()
    record = object()
    bundle = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=owner,
        prepared_destination=object(),
    )
    context = _running_command(owner_token=owner, record_token=record)
    command, deadline, process_receipt = context[3], context[4], context[7]
    first_factory = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_factory = threading.Event()
    original_factory = build_publication._new_workspace_built_receipt_for_publication
    original_read = state_module._WorkspaceWorkerDestination._read_before
    results: list[object] = []
    errors: list[BaseException] = []

    def gated_factory(*args: object, **kwargs: object):
        result = original_factory(*args, **kwargs)
        if threading.current_thread().name == "built-publisher-first":
            first_factory.set()
            assert release_first.wait(2.0)
        else:
            second_factory.set()
        return result

    def fail_second_read(self: object, *args: object, **kwargs: object):
        if (
            self is bundle._built_destination
            and threading.current_thread().name == "built-publisher-second"
        ):
            raise OSError("synthetic overlapping replay read failure")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(
        build_publication,
        "_new_workspace_built_receipt_for_publication",
        gated_factory,
    )
    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_read_before",
        fail_second_read,
    )

    def publish() -> None:
        if threading.current_thread().name == "built-publisher-second":
            second_started.set()
        try:
            results.append(
                build_publication._publish_workspace_built_receipt(
                    bundle=bundle,
                    command=command,
                    owner_token=owner,
                    record_token=record,
                    output_digest=b"o" * 32,
                    process_receipt=process_receipt,
                    operation_deadline=deadline,
                )
            )
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=publish, name="built-publisher-first")
    second = threading.Thread(target=publish, name="built-publisher-second")
    first.start()
    assert first_factory.wait(2.0)
    second.start()
    assert second_started.wait(2.0)
    assert not second_factory.wait(0.05)
    release_first.set()
    first.join(2.0)
    second.join(2.0)

    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 1
    assert len(errors) == 1 and type(errors[0]) is OSError
    built = results[0]
    assert second_factory.is_set()
    assert build_receipt._BUILT_BY_COMMAND[command] is built
    assert build_receipt._BUILT_LEASES[built][5] == "active"
    assert built._matches(owner, record, require_active=True)


@pytest.mark.parametrize("fault", [KeyboardInterrupt, SystemExit])
def test_direct_activation_control_return_loss_revokes_unconfirmed_candidate(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException],
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    original = build_receipt._store_command_state

    def store_then_raise(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise fault("synthetic activation control return loss")

    monkeypatch.setattr(build_receipt, "_store_command_state", store_then_raise)
    with pytest.raises(fault):
        build_receipt._activate_workspace_built_receipt(
            built,
            owner,
            record,
            operation_deadline=deadline,
        )

    assert build_receipt._COMMANDS[command][4] == "built"
    assert build_receipt._BUILT_LEASES[built][5] == "revoked"
    assert not built._matches(owner, record, require_active=True)


@pytest.mark.parametrize("fault", [KeyboardInterrupt, SystemExit])
def test_direct_active_replay_control_preserves_committed_receipt(
    monkeypatch: pytest.MonkeyPatch,
    fault: type[BaseException],
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner,
        record_token=record,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    assert build_receipt._activate_workspace_built_receipt(
        built,
        owner,
        record,
        operation_deadline=deadline,
    )
    original_zero = process_contract._workspace_build_process_completed_zero

    def fail_zero(*_args: object, **_kwargs: object) -> bool:
        raise fault("synthetic active-replay control")

    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        fail_zero,
    )
    with pytest.raises(fault):
        build_receipt._activate_workspace_built_receipt(
            built,
            owner,
            record,
            operation_deadline=deadline,
        )

    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        original_zero,
    )
    assert build_receipt._BUILT_LEASES[built][5] == "active"
    assert built._matches(owner, record, require_active=True)


def test_built_creation_rejects_caller_deadline_beyond_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context, clock, owner, record, command, deadline, process_receipt = _built_context()
    _patch_built_clock(monkeypatch, clock)
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner,
            record_token=record,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline + 1.0,
        )
    assert command not in build_receipt._BUILT_BY_COMMAND

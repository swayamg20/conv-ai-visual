"""Focused synthetic tests for the prepared-to-built worker contract."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import sys
import threading
import time
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_facade as process_facade
import scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry as process_facade_registry
import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as process_registry
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_facade as build_facade
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_forget as build_forget
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process as build_process
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract as process_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt as build_receipt
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values as build_values
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_release as release_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as thread_module


@pytest.fixture(autouse=True)
def _isolated_build_contract() -> None:
    mappings = (
        build_values._COMMANDS,
        build_values._COMMAND_GATES,
        build_values._PROCESS_ASSOCIATIONS,
        build_receipt._BUILT_LEASES,
        build_receipt._BUILT_BY_COMMAND,
        build_forget._FORGOTTEN_RECORDS,
        fs_contract._LEASES,
        fs_contract._PREPARED_BUILDS,
        process_registry._OWNERS,
        process_registry._KERNELS,
    )
    for mapping in mappings:
        mapping.clear()
    yield
    for mapping in mappings:
        mapping.clear()


def _future(seconds: float = 2.0) -> float:
    return float(time.monotonic() + seconds)


def _prepared_command(*, deadline: float | None = None):
    owner_token = object()
    record_token = object()
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    build_deadline = _future() if deadline is None else deadline
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=build_deadline,
        expected_spawn_fingerprint=b"s" * 32,
    )
    return owner_token, record_token, prepared, command, build_deadline


def _claim(values: tuple[object, object, object, object, float]) -> float:
    owner_token, record_token, prepared, command, _deadline = values
    return build_values._claim_workspace_build_command(
        command,
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
    )


def _bundle(tmp_path: Path):
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=(tmp_path / "source").resolve(),
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id="build-contract",
    )
    owner = destination._read(destination._request)
    bundle = state_module._new_relay_linux_build_workspace_worker_bundle(owner)
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    assert construction is not None and coherent is True
    return owner, bundle, construction


def test_command_and_built_values_are_opaque_and_nonserializable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    assert not command
    assert repr(command) == "_WorkspaceBuildCommand()"
    for operation in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(command)

    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        lambda _command, _receipt: True,
    )
    assert build_values._intend_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    assert build_values._complete_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner_token,
        record_token=record_token,
        output_digest=b"o" * 32,
        process_receipt=object(),
        operation_deadline=deadline,
    )
    assert not built
    assert repr(built) == "_WorkspaceBuiltReceipt()"
    for operation in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(built)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (build_values, "_store_command_state"),
        (fs_contract, "_store_prepared_lease"),
        (fs_contract, "_store_prepared_build"),
    ],
)
@pytest.mark.parametrize("control", [False, True])
def test_command_claim_repairs_every_stored_effect_return_loss(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    name: str,
    control: bool,
) -> None:
    values = _prepared_command()
    original = getattr(module, name)
    lost = False

    def store_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal lost
        original(*args, **kwargs)
        if not lost:
            lost = True
            if control:
                raise KeyboardInterrupt()
            raise OSError("synthetic stored-effect return loss")

    monkeypatch.setattr(module, name, store_then_raise)
    if control:
        with pytest.raises(KeyboardInterrupt):
            _claim(values)
    deadline = _claim(values)

    owner_token, record_token, prepared, command, expected = values
    assert lost is True
    assert deadline == expected
    assert fs_contract._workspace_prepared_build_matches(
        prepared,
        owner_token,
        record_token,
        command,
        expected,
    )


def test_expired_command_cannot_consume_the_prepared_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepared_command()
    owner_token, record_token, prepared, _command, deadline = values
    monkeypatch.setattr(build_values.time, "monotonic", lambda: deadline)

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        _claim(values)

    assert prepared._matches(owner_token, record_token, require_active=True)


def test_cancellation_linearizes_before_process_start() -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline

    assert build_values._request_workspace_build_command_cancel(
        command,
        cleanup_deadline=_future(),
    )
    assert not build_values._workspace_build_command_authorizes_process(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    assert not build_values._intend_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )


@pytest.mark.parametrize("wrong", ["controller", "deadline"])
def test_driver_rejects_wrong_controller_or_deadline_before_process_construction(
    monkeypatch: pytest.MonkeyPatch,
    wrong: str,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    controller_owner = object() if wrong == "controller" else owner_token
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=controller_owner,
    )
    constructed = 0

    def forbidden_spec(**_kwargs: object) -> None:
        nonlocal constructed
        constructed += 1

    monkeypatch.setattr(build_process, "_new_relay_linux_build_spec", forbidden_spec)
    supplied_deadline = deadline + 1.0 if wrong == "deadline" else deadline

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_process._drive_workspace_build_process(
            command=command,
            request=object(),
            controller=controller,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=supplied_deadline,
        )

    assert constructed == 0


def test_losing_built_candidate_never_matches_or_replaces_canonical_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    assert build_values._intend_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    assert build_values._complete_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        lambda _command, _receipt: True,
    )
    retained: list[object] = []

    def reject_before_store(command_value: object, receipt: object) -> None:
        assert command_value is command
        retained.append(receipt)
        raise OSError("synthetic pre-store failure")

    monkeypatch.setattr(build_receipt, "_store_built_for_command", reject_before_store)
    with pytest.raises(OSError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner_token,
            record_token=record_token,
            output_digest=b"a" * 32,
            process_receipt=object(),
            operation_deadline=deadline,
        )

    losing = retained[0]
    assert type(losing) is build_receipt._WorkspaceBuiltReceipt
    assert not losing._matches(owner_token, record_token)
    assert build_receipt._BUILT_BY_COMMAND.get(command) is None


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (build_receipt, "_store_built_lease"),
        (build_receipt, "_store_command_state"),
    ],
)
@pytest.mark.parametrize("control", [False, True])
def test_built_activation_repairs_stored_effect_return_loss(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    name: str,
    control: bool,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    assert build_values._intend_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    assert build_values._complete_workspace_build_process_start(
        command,
        owner_token=owner_token,
        record_token=record_token,
        build_deadline=deadline,
    )
    monkeypatch.setattr(
        process_contract,
        "_workspace_build_process_completed_zero",
        lambda _command, _receipt: True,
    )
    process_receipt = object()
    built = build_receipt._new_workspace_built_receipt(
        command=command,
        owner_token=owner_token,
        record_token=record_token,
        output_digest=b"o" * 32,
        process_receipt=process_receipt,
        operation_deadline=deadline,
    )
    assert (
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner_token,
            record_token=record_token,
            output_digest=b"o" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
        is built
    )
    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_receipt._new_workspace_built_receipt(
            command=command,
            owner_token=owner_token,
            record_token=record_token,
            output_digest=b"x" * 32,
            process_receipt=process_receipt,
            operation_deadline=deadline,
        )
    original = getattr(module, name)
    lost = False

    def store_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal lost
        original(*args, **kwargs)
        if not lost:
            lost = True
            if control:
                raise SystemExit(17)
            raise OSError("synthetic activation return loss")

    monkeypatch.setattr(module, name, store_then_raise)
    if control:
        with pytest.raises(SystemExit):
            build_receipt._activate_workspace_built_receipt(
                built,
                owner_token,
                record_token,
                operation_deadline=deadline,
            )
    assert build_receipt._activate_workspace_built_receipt(
        built,
        owner_token,
        record_token,
        operation_deadline=deadline,
    )
    assert lost is True
    assert built._matches(owner_token, record_token, require_active=True)


@pytest.mark.parametrize("preown_control", [False, True])
@pytest.mark.parametrize("resolver_control", [False, True])
def test_preown_return_loss_retains_cleanup_authority_through_resolver_faults(
    monkeypatch: pytest.MonkeyPatch,
    preown_control: bool,
    resolver_control: bool,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=owner_token,
    )
    authority = object()

    class Candidate:
        _cleanup_authority = authority

    candidate = Candidate()

    class Destination:
        def _read(self) -> object:
            return candidate

    class Request:
        _next_cli = object()
        _node = object()
        _run_id = "synthetic"
        _workspace = object()

        def _environment_values(self) -> dict[str, str]:
            return {}

    registry_present = False
    resolver_faulted = False
    released: list[object] = []

    def preown(**_kwargs: object) -> None:
        nonlocal registry_present
        registry_present = True
        if preown_control:
            raise KeyboardInterrupt()
        raise OSError("synthetic preown return loss")

    def resolve(value: object) -> object | None:
        nonlocal resolver_faulted
        assert value is authority
        if not resolver_faulted:
            resolver_faulted = True
            if resolver_control:
                raise SystemExit(23)
            raise OSError("synthetic resolver fault")
        return candidate if registry_present else None

    def release(value: object, *, cleanup_deadline: float) -> None:
        nonlocal registry_present
        assert value is authority
        assert cleanup_deadline > time.monotonic()
        released.append(value)
        registry_present = False

    monkeypatch.setattr(build_process, "_new_relay_linux_build_spec", lambda **_kw: object())
    monkeypatch.setattr(build_process, "_new_raw_build_process_destination", lambda _s: object())
    monkeypatch.setattr(
        build_process,
        "_new_build_owner_destination",
        lambda _s, _r: Destination(),
    )
    monkeypatch.setattr(
        build_process,
        "_intend_workspace_build_process_association",
        lambda *args, **kwargs: authority,
    )
    monkeypatch.setattr(build_process, "_preown_build_process", preown)
    monkeypatch.setattr(build_process, "_resolve_build_process_owner", resolve)
    monkeypatch.setattr(build_process, "_release_relay_linux_build_process", release)

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        build_process._drive_workspace_build_process(
            command=command,
            request=Request(),
            controller=controller,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=deadline,
        )

    assert resolver_faulted is True
    assert released == [authority]
    assert registry_present is False
    if preown_control:
        signal = controller._control_value()
        assert signal is not None and signal.kind == "keyboard"


def test_wrong_canonical_spawn_fingerprint_rejects_registered_owner(
    tmp_path: Path,
) -> None:
    values = _prepared_command()
    owner_token, record_token, _prepared, command, deadline = values
    assert _claim(values) == deadline
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "wrong-node").resolve(),
        next_cli=(tmp_path / "wrong-next").resolve(),
        workspace=(tmp_path / "wrong-workspace").resolve(),
        run_id="wrong-workspace",
        environment={
            **spawn_module._FIXED_BUILD_ENVIRONMENT,
            "VOICE_E2E_NEXT_DIST_DIR": ".next-voice-e2e/wrong-workspace",
        },
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = process_registry._new_build_owner_destination(spec, raw)
    process_owner = process_registry._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )

    with pytest.raises(build_values._WorkspaceBuildHandoffError):
        process_contract._associate_workspace_build_process(
            command,
            owner_token=owner_token,
            record_token=record_token,
            process_owner=process_owner,
            expected_spec=spec,
            expected_raw_destination=raw,
        )

    assert build_values._PROCESS_ASSOCIATIONS.get(command) is None
    process_facade._release_relay_linux_build_process(
        process_owner,
        cleanup_deadline=_future(),
    )


def _associated_process(tmp_path: Path):
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id="associated-build",
        environment={
            **spawn_module._FIXED_BUILD_ENVIRONMENT,
            "VOICE_E2E_NEXT_DIST_DIR": ".next-voice-e2e/associated-build",
        },
    )
    owner_token = object()
    record_token = object()
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    deadline = _future()
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=deadline,
        expected_spawn_fingerprint=process_contract._spawn_spec_fingerprint(spec),
    )
    assert (
        build_values._claim_workspace_build_command(
            command,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
        )
        == deadline
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = process_registry._new_build_owner_destination(spec, raw)
    candidate = destination._read()
    assert (
        process_contract._intend_workspace_build_process_association(
            command,
            owner_token=owner_token,
            record_token=record_token,
            process_owner=candidate,
            expected_spec=spec,
            expected_raw_destination=raw,
        )
        is candidate._cleanup_authority
    )
    process_owner = process_registry._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )
    assert (
        process_contract._associate_workspace_build_process(
            command,
            owner_token=owner_token,
            record_token=record_token,
            process_owner=process_owner,
            expected_spec=spec,
            expected_raw_destination=raw,
        )
        is candidate._cleanup_authority
    )
    return command, process_owner


@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("control", [False, True])
def test_cancel_store_fault_still_releases_exact_associated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored: bool,
    control: bool,
) -> None:
    command, process_owner = _associated_process(tmp_path)
    original = build_values._store_command_state
    faulted = False

    def fail_cancel_store(*args: object, **kwargs: object) -> None:
        nonlocal faulted
        if stored:
            original(*args, **kwargs)
        if not faulted:
            faulted = True
            if control:
                raise KeyboardInterrupt()
            raise OSError("synthetic cancel-store fault")
        original(*args, **kwargs)

    monkeypatch.setattr(build_values, "_store_command_state", fail_cancel_store)
    if control:
        with pytest.raises(KeyboardInterrupt):
            build_process._cancel_associated_workspace_build_process(
                command,
                cleanup_deadline=_future(),
            )
    else:
        assert build_process._cancel_associated_workspace_build_process(
            command,
            cleanup_deadline=_future(),
        )

    assert faulted is True
    assert build_values._workspace_build_command_cancel_requested(command)
    assert (
        process_contract._workspace_build_process_cleanup_authority(command)
        is process_owner._cleanup_authority
    )
    assert (
        process_facade_registry._resolve_build_process_owner(
            process_owner._cleanup_authority,
        )
        is None
    )


def test_cancel_clock_control_still_releases_associated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, process_owner = _associated_process(tmp_path)
    deadline = _future()
    original = build_process.time.monotonic
    interrupted = False

    def interrupt_once() -> float:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original()

    monkeypatch.setattr(build_process.time, "monotonic", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        build_process._cancel_associated_workspace_build_process(
            command,
            cleanup_deadline=deadline,
        )

    assert interrupted is True
    assert (
        process_facade_registry._resolve_build_process_owner(
            process_owner._cleanup_authority,
        )
        is None
    )


def test_held_start_permit_and_process_release_linearize_cancellation(
    tmp_path: Path,
) -> None:
    command, process_owner = _associated_process(tmp_path)
    state = build_values._COMMANDS[command]
    permit = build_values._acquire_workspace_build_process_start(
        command,
        owner_token=state[0],
        record_token=state[1],
        build_deadline=state[3],
    )
    assert permit is not None

    assert build_process._cancel_associated_workspace_build_process(
        command,
        cleanup_deadline=_future(),
    )
    build_values._release_workspace_build_process_start(permit)

    assert build_values._workspace_build_command_cancel_requested(command)
    assert not build_values._complete_workspace_build_process_start(
        command,
        owner_token=state[0],
        record_token=state[1],
        build_deadline=state[3],
    )
    assert (
        process_facade_registry._resolve_build_process_owner(
            process_owner._cleanup_authority,
        )
        is None
    )


def test_cancel_during_preown_return_window_never_claims_stable_absence(
    tmp_path: Path,
) -> None:
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id="preown-window",
        environment={
            **spawn_module._FIXED_BUILD_ENVIRONMENT,
            "VOICE_E2E_NEXT_DIST_DIR": ".next-voice-e2e/preown-window",
        },
    )
    owner_token = object()
    record_token = object()
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=_future(),
        expected_spawn_fingerprint=process_contract._spawn_spec_fingerprint(spec),
    )
    build_values._claim_workspace_build_command(
        command,
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = process_registry._new_build_owner_destination(spec, raw)
    candidate = destination._read()
    process_contract._intend_workspace_build_process_association(
        command,
        owner_token=owner_token,
        record_token=record_token,
        process_owner=candidate,
        expected_spec=spec,
        expected_raw_destination=raw,
    )
    registered = threading.Event()
    resume = threading.Event()

    def preown_then_hold() -> None:
        process_registry._preown_build_process(
            spec=spec,
            raw_destination=raw,
            destination=destination,
        )
        registered.set()
        assert resume.wait(2.0)

    worker = threading.Thread(target=preown_then_hold)
    worker.start()
    assert registered.wait(1.0)

    completed = build_process._cancel_associated_workspace_build_process(
        command,
        cleanup_deadline=_future(),
    )
    resume.set()
    worker.join(2.0)

    assert completed is False
    assert not worker.is_alive()
    assert (
        process_facade_registry._resolve_build_process_owner(
            candidate._cleanup_authority,
        )
        is None
    )


def test_publish_return_loss_and_read_timeout_still_cancel_exact_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _bundle(tmp_path)
    owner_token = owner._cleanup_authority._key
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=construction._record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        construction._record_token,
    )
    original_publish = state_module._WorkspaceWorkerDestination._publish_before

    def publish_then_raise(self: object, *args: object):
        result = original_publish(self, *args)
        if self is bundle._command_destination:
            raise OSError("synthetic publish return loss")
        return result

    original_read = state_module._WorkspaceWorkerDestination._read_before

    def command_read_timeout(self: object, *args: object):
        if self is bundle._command_destination:
            return None, False
        return original_read(self, *args)

    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_publish_before",
        publish_then_raise,
    )
    monkeypatch.setattr(
        state_module._WorkspaceWorkerDestination,
        "_read_before",
        command_read_timeout,
    )

    built, coherent = build_facade._build_relay_linux_workspace(
        owner,
        bundle,
        construction,
        prepared,
        build_deadline=_future(),
    )
    stored = bundle._command_destination._read(owner_token)

    assert built is None and coherent is False
    assert type(stored) is build_values._WorkspaceBuildCommand
    assert build_values._workspace_build_command_cancel_requested(stored)
    assert bundle._controller._cancellation_requested() is True


def test_cleanup_control_is_latched_on_the_workspace_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _prepared_command()
    _owner_token, _record_token, _prepared, command, _deadline = values
    controller = state_module._WorkspaceWorkerController(
        state_module._CONTROLLER_TOKEN,
        owner_token=object(),
    )

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        build_process,
        "_cancel_associated_workspace_build_process",
        interrupt,
    )
    build_facade._cleanup_associated_process(command, controller)

    signal = controller._control_value()
    assert signal is not None and signal.kind == "keyboard"


def _published_cancelled_command(tmp_path: Path):
    owner, bundle, construction = _bundle(tmp_path)
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token
    prepared = fs_contract._new_workspace_prepared_receipt(
        owner_token=owner_token,
        record_token=record_token,
        fingerprint=b"p" * 32,
    )
    assert fs_contract._activate_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    deadline = _future()
    command = build_values._new_workspace_build_command(
        owner_token=owner_token,
        record_token=record_token,
        prepared=prepared,
        build_deadline=deadline,
        expected_spawn_fingerprint=b"s" * 32,
    )
    bundle._command_destination._publish(
        state_module._DESTINATION_TOKEN,
        owner_token,
        command,
    )
    assert (
        build_values._claim_workspace_build_command(
            command,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
        )
        == deadline
    )
    assert build_values._request_workspace_build_command_cancel(
        command,
        cleanup_deadline=_future(),
    )
    return owner, bundle, construction, prepared, command


def _settle_cancelled_command(
    owner: object,
    construction: object,
    prepared: object,
) -> None:
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token
    assert fs_contract._revoke_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    assert fs_contract._publish_workspace_filesystem_settlement(
        owner_token,
        record_token,
        object(),
    )


def test_build_state_forget_requires_revocation_and_filesystem_settlement(
    tmp_path: Path,
) -> None:
    owner, bundle, construction, prepared, command = _published_cancelled_command(
        tmp_path,
    )
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token

    assert not build_forget._forget_workspace_build_state(
        command,
        owner_token=owner_token,
        record_token=record_token,
    )
    assert fs_contract._revoke_workspace_prepared_receipt(
        prepared,
        owner_token,
        record_token,
    )
    assert not build_forget._forget_workspace_build_state(
        command,
        owner_token=owner_token,
        record_token=record_token,
    )
    assert fs_contract._publish_workspace_filesystem_settlement(
        owner_token,
        record_token,
        object(),
    )
    assert release_module._forget_filesystem_state(
        owner_token,
        bundle,
        record_token,
        _future(),
    )

    assert bundle._command_destination._read(owner_token) is None
    assert command not in build_values._COMMANDS
    assert command not in build_values._COMMAND_GATES
    assert command not in build_values._PROCESS_ASSOCIATIONS
    assert command not in build_receipt._BUILT_BY_COMMAND
    assert prepared not in fs_contract._PREPARED_BUILDS
    assert record_token not in build_forget._FORGOTTEN_RECORDS
    assert fs_contract._workspace_filesystem_state_is_forgotten(record_token)


def test_forget_retries_after_filesystem_forget_return_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction, prepared, command = _published_cancelled_command(
        tmp_path,
    )
    _settle_cancelled_command(owner, construction, prepared)
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token
    original = fs_contract._forget_workspace_filesystem_settlement
    lost = False

    def forget_then_raise(value: object) -> None:
        nonlocal lost
        original(value)
        if not lost:
            lost = True
            raise OSError("synthetic filesystem forget return loss")

    monkeypatch.setattr(
        fs_contract,
        "_forget_workspace_filesystem_settlement",
        forget_then_raise,
    )
    with pytest.raises(OSError):
        release_module._forget_filesystem_state(
            owner_token,
            bundle,
            record_token,
            _future(),
        )
    assert command not in build_values._COMMANDS
    assert bundle._command_destination._read(owner_token) is command

    assert release_module._forget_filesystem_state(
        owner_token,
        bundle,
        record_token,
        _future(),
    )
    assert lost is True
    assert bundle._command_destination._read(owner_token) is None
    assert record_token not in build_forget._FORGOTTEN_RECORDS


@pytest.mark.parametrize("kind", ["command", "built", "post-forget-command"])
def test_forget_obeys_slot_deadline_and_retries_after_map_forget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    owner, bundle, construction, prepared, _command = _published_cancelled_command(
        tmp_path,
    )
    _settle_cancelled_command(owner, construction, prepared)
    owner_token = owner._cleanup_authority._key
    record_token = construction._record_token
    destination = bundle._built_destination if kind == "built" else bundle._command_destination
    if kind == "post-forget-command":
        original = fs_contract._forget_workspace_filesystem_settlement

        def hold_after_map_forget(value: object) -> None:
            original(value)
            assert destination._lock.acquire(blocking=False)

        monkeypatch.setattr(
            fs_contract,
            "_forget_workspace_filesystem_settlement",
            hold_after_map_forget,
        )
    else:
        assert destination._lock.acquire(blocking=False)

    started = time.monotonic()
    assert not release_module._forget_filesystem_state(
        owner_token,
        bundle,
        record_token,
        float(started + 0.02),
    )
    assert time.monotonic() - started < 0.2
    destination._lock.release()
    monkeypatch.undo()

    assert release_module._forget_filesystem_state(
        owner_token,
        bundle,
        record_token,
        _future(),
    )
    assert bundle._command_destination._read(owner_token) is None
    assert record_token not in build_forget._FORGOTTEN_RECORDS

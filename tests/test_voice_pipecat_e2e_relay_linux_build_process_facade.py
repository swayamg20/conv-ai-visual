"""Synthetic tests for the private relay build process facade."""
# ruff: noqa: E402

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_facade as facade_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry as facade_registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker as worker_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_local as local_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_process_local as process_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state as worker_state_module
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module

RUN_ID = "relay-b0-process-facade"


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "_OWNERS", {})
    monkeypatch.setattr(registry_module, "_KERNELS", {})


def _environment() -> dict[str, str]:
    return {
        **spawn_module._FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{RUN_ID}",
    }


def _owner(tmp_path: Path):
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id=RUN_ID,
        environment=_environment(),
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = registry_module._new_build_owner_destination(spec, raw)
    owner = registry_module._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )
    return spec, raw, owner


class _FakeProcess:
    def __init__(self, returncode: int = 0) -> None:
        self.pid = 4311
        self.returncode = returncode
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.poll_calls = 0
        self.wait_calls: list[float] = []

    def poll(self) -> int:
        self.poll_calls += 1
        return self.returncode

    def wait(self, timeout: float) -> int:
        self.wait_calls.append(timeout)
        return self.returncode


class _ControlledProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.returncode = None  # type: ignore[assignment]
        self.exited = threading.Event()

    def poll(self) -> int | None:
        self.poll_calls += 1
        if self.exited.is_set():
            self.returncode = -15  # type: ignore[assignment]
        return self.returncode  # type: ignore[return-value]

    def wait(self, timeout: float) -> int:
        self.wait_calls.append(timeout)
        assert type(self.returncode) is int
        return self.returncode


def _install_process(
    monkeypatch: pytest.MonkeyPatch,
    process: _FakeProcess,
) -> list[object]:
    spawned: list[object] = []

    def spawn(_spec: object, destination: object) -> None:
        spawned.append(process)
        destination.publish(process)  # type: ignore[attr-defined]

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(process_module, "_local_process_group", lambda pid: pid)
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)
    return spawned


def _future(seconds: float = 5.0) -> float:
    return float(time.monotonic() + seconds)


def test_start_join_zero_result_and_release_are_one_private_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, owner = _owner(tmp_path)
    process = _FakeProcess()
    spawned = _install_process(monkeypatch, process)
    run_deadline = _future()

    facade_module._start_relay_linux_build_process(owner, run_deadline=run_deadline)
    facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
    receipt = facade_module._relay_linux_build_process_result(owner)

    assert not receipt
    assert receipt.status == "build-process-exited-zero"
    assert raw._read(spec) is None
    assert spawned == [process]
    assert process.wait_calls == [0.0]
    assert owner._facade_state._phase_value() == "joined"

    facade_module._release_relay_linux_build_process(
        owner,
        cleanup_deadline=_future(),
    )

    assert registry_module._OWNERS == {}
    assert registry_module._KERNELS == {}
    assert owner._facade_state._phase_value() == "released"
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=-1.0,
    )
    assert threading.enumerate()


def test_result_store_return_loss_reconciles_one_canonical_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    _install_process(monkeypatch, _FakeProcess())
    facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
    facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
    calls = 0

    def lose_return() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("result-return-loss")

    monkeypatch.setattr(state_module, "_result_receipt_published", lose_return)

    first = facade_module._relay_linux_build_process_result(owner)
    second = facade_module._relay_linux_build_process_result(owner)

    assert first is second
    assert calls == 1
    facade_module._release_relay_linux_build_process(owner, cleanup_deadline=_future())


def test_start_clock_control_returns_only_opaque_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    real_clock = time.monotonic
    calls = 0

    def interrupted_clock() -> float:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return float(real_clock())

    monkeypatch.setattr(facade_module, "_local_monotonic", interrupted_clock)

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    assert owner._facade_state._release_was_requested()
    facade_module._release_relay_linux_build_process(
        captured.value.cleanup_authority,  # type: ignore[attr-defined]
        cleanup_deadline=_future(),
    )
    assert registry_module._OWNERS == {}


def test_precontroller_first_control_survives_nested_normalizer_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    real_clock = time.monotonic
    clock_calls = 0
    normalize_calls = 0
    original_normalize = worker_state_module._control_signal

    def interrupted_clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            raise KeyboardInterrupt
        return float(real_clock())

    def interrupted_normalize(error: KeyboardInterrupt | SystemExit):
        nonlocal normalize_calls
        normalize_calls += 1
        if normalize_calls == 1:
            raise SystemExit(7)
        return original_normalize(error)

    monkeypatch.setattr(facade_module, "_local_monotonic", interrupted_clock)
    monkeypatch.setattr(worker_state_module, "_control_signal", interrupted_normalize)

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    assert owner._facade_state._release_was_requested()
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=_future(),
    )


def test_precontroller_first_control_survives_control_holder_constructor_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    real_clock = time.monotonic
    clock_calls = 0
    constructor_calls = 0
    original_constructor = facade_module._new_build_worker_controller

    def interrupted_clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            raise KeyboardInterrupt
        return float(real_clock())

    def interrupted_constructor(*, owner_token: object, run_deadline: float):
        nonlocal constructor_calls
        constructor_calls += 1
        if constructor_calls == 1:
            raise SystemExit(7)
        return original_constructor(
            owner_token=owner_token,
            run_deadline=run_deadline,
        )

    monkeypatch.setattr(facade_module, "_local_monotonic", interrupted_clock)
    monkeypatch.setattr(
        facade_module,
        "_new_build_worker_controller",
        interrupted_constructor,
    )

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    assert constructor_calls == 2
    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    assert owner._facade_state._release_was_requested()
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=_future(),
    )


def test_late_kernel_take_after_start_timeout_never_restarts_or_releases_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    spawned = _install_process(monkeypatch, _FakeProcess())
    take_gate = threading.Event()
    original_take = worker_module._take_worker_kernel
    take_calls = 0

    def delayed_take(token: object):
        nonlocal take_calls
        take_calls += 1
        take_gate.wait(2.0)
        return original_take(token)

    monkeypatch.setattr(worker_module, "_take_worker_kernel", delayed_take)
    monkeypatch.setattr(facade_module, "_START_CONFIRM_SECONDS", 0.02)
    run_deadline = _future()

    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired) as initial:
        facade_module._start_relay_linux_build_process(owner, run_deadline=run_deadline)
    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired):
        facade_module._start_relay_linux_build_process(owner, run_deadline=run_deadline)
    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired) as blocked:
        facade_module._release_relay_linux_build_process(
            initial.value.cleanup_authority,
            cleanup_deadline=_future(0.03),
        )

    assert blocked.value.cleanup_authority is owner._cleanup_authority
    assert registry_module._OWNERS
    assert take_calls == 1
    assert spawned == []

    take_gate.set()
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=_future(),
    )
    assert registry_module._OWNERS == {}


def test_exact_base_start_rejection_proves_no_thread_and_can_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)

    def reject_os_thread(*_args: object) -> None:
        raise RuntimeError("synthetic exact OS-thread rejection")

    monkeypatch.setattr(threading, "_start_new_thread", reject_os_thread)

    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired) as captured:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    thread = owner._thread_destination._read()
    assert type(thread) is threading.Thread
    assert thread.ident is None and not thread.is_alive()
    assert owner._facade_state._start_effect_was_entered() is False
    facade_module._release_relay_linux_build_process(
        captured.value.cleanup_authority,
        cleanup_deadline=_future(),
    )
    assert registry_module._OWNERS == {}


def test_partial_thread_init_control_is_prestart_scrubbed_and_released(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    control = KeyboardInterrupt()

    def interrupted_init(
        candidate: threading.Thread,
        *,
        target: object,
        args: tuple[object, ...],
        name: str,
        daemon: bool,
    ) -> None:
        del name, daemon
        candidate._target = target
        candidate._args = args
        candidate._kwargs = {"partial": owner}
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", interrupted_init)

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    raw_thread = owner._thread_destination._read()
    assert type(raw_thread) is threading.Thread
    assert owner._facade_state._start_effect_was_entered() is False
    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    facade_module._release_relay_linux_build_process(
        captured.value.cleanup_authority,  # type: ignore[attr-defined]
        cleanup_deadline=_future(),
    )

    assert vars(raw_thread)["_target"] is None
    assert vars(raw_thread)["_args"] == ()
    assert vars(raw_thread)["_kwargs"] == {}
    assert registry_module._OWNERS == {}
    assert registry_module._KERNELS == {}


def test_prestart_readback_control_is_preserved_through_completed_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)

    def reject_os_thread(*_args: object) -> None:
        raise RuntimeError("synthetic exact OS-thread rejection")

    monkeypatch.setattr(threading, "_start_new_thread", reject_os_thread)
    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired) as start_failure:
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    raw_thread = owner._thread_destination._read()
    assert type(raw_thread) is threading.Thread
    real_vars = vars
    readbacks = 0

    def interrupted_vars(value: object) -> dict[str, object]:
        nonlocal readbacks
        readbacks += 1
        if readbacks == 1 and value is raw_thread:
            raise KeyboardInterrupt
        return real_vars(value)

    monkeypatch.setattr(
        facade_registry_module,
        "vars",
        interrupted_vars,
        raising=False,
    )

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._release_relay_linux_build_process(
            start_failure.value.cleanup_authority,
            cleanup_deadline=_future(),
        )

    assert readbacks >= 2
    assert not hasattr(captured.value, "cleanup_authority")
    assert registry_module._OWNERS == {}
    assert registry_module._KERNELS == {}


def test_join_timeout_hands_the_same_authority_to_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    process = _ControlledProcess()
    _install_process(monkeypatch, process)
    monkeypatch.setattr(
        process_module,
        "_local_group_exists",
        lambda _pgid: not process.exited.is_set(),
    )

    def signal_group(_pgid: int, _signal: int) -> bool:
        process.exited.set()
        return True

    monkeypatch.setattr(process_module, "_local_signal_group", signal_group)
    facade_module._start_relay_linux_build_process(owner, run_deadline=_future())

    with pytest.raises(state_module._RelayLinuxBuildCleanupRequired) as captured:
        facade_module._join_relay_linux_build_process(
            owner,
            join_deadline=_future(0.02),
        )

    facade_module._release_relay_linux_build_process(
        captured.value.cleanup_authority,
        cleanup_deadline=_future(),
    )
    assert process.exited.is_set()
    assert registry_module._OWNERS == {}


@pytest.mark.parametrize("control", [False, True])
def test_owner_delete_return_loss_is_terminal_cleanup_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: bool,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    _install_process(monkeypatch, _FakeProcess())
    facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
    facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
    calls = 0

    def lose_owner_delete_return() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            if control:
                raise KeyboardInterrupt
            raise RuntimeError("owner-delete-return-loss")

    monkeypatch.setattr(
        facade_registry_module,
        "_owner_registry_released",
        lose_owner_delete_return,
    )

    if control:
        with pytest.raises(KeyboardInterrupt) as captured:
            facade_module._release_relay_linux_build_process(
                owner,
                cleanup_deadline=_future(),
            )
        assert not hasattr(captured.value, "cleanup_authority")
    else:
        facade_module._release_relay_linux_build_process(
            owner,
            cleanup_deadline=_future(),
        )

    assert calls == 1
    assert registry_module._OWNERS == {}
    assert registry_module._KERNELS == {}


def test_first_controller_control_wins_over_later_join_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    _install_process(monkeypatch, _FakeProcess())
    facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
    deadline = time.monotonic() + 2.0
    while facade_registry_module._build_process_worker_status(owner) != "settled":
        assert time.monotonic() < deadline
        time.sleep(0.001)
    controller = owner._controller_destination._read()
    controller._capture_control(KeyboardInterrupt())  # type: ignore[attr-defined]
    monkeypatch.setattr(
        facade_module,
        "_process_join_committed",
        lambda: (_ for _ in ()).throw(SystemExit(7)),
    )

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._join_relay_linux_build_process(owner, join_deadline=_future())

    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    facade_module._release_relay_linux_build_process(
        captured.value.cleanup_authority,  # type: ignore[attr-defined]
        cleanup_deadline=_future(),
    )


def test_concurrent_start_and_release_calls_are_single_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    spawned = _install_process(monkeypatch, _FakeProcess())
    run_deadline = _future()
    errors: list[BaseException] = []

    def start() -> None:
        try:
            facade_module._start_relay_linux_build_process(
                owner,
                run_deadline=run_deadline,
            )
        except BaseException as error:
            errors.append(error)

    callers = [threading.Thread(target=start) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()

    assert errors == []
    assert spawned == [spawned[0]]
    facade_module._join_relay_linux_build_process(owner, join_deadline=_future())

    releasers = [
        threading.Thread(
            target=lambda: facade_module._release_relay_linux_build_process(
                owner,
                cleanup_deadline=_future(),
            )
        )
        for _ in range(2)
    ]
    for releaser in releasers:
        releaser.start()
    for releaser in releasers:
        releaser.join()
    assert registry_module._OWNERS == {}


@pytest.mark.parametrize("stage", ["start", "join", "result", "release"])
def test_operation_lock_acquire_return_loss_never_leaks_the_rlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    _install_process(monkeypatch, _FakeProcess())
    if stage != "start":
        facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
    if stage in {"result", "release"}:
        facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
    calls = 0

    def lose_acquire_return() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(facade_module, "_operation_lock_acquired", lose_acquire_return)

    with pytest.raises(KeyboardInterrupt) as captured:
        if stage == "start":
            facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
        elif stage == "join":
            facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
        elif stage == "result":
            facade_module._relay_linux_build_process_result(owner)
        else:
            facade_module._release_relay_linux_build_process(
                owner,
                cleanup_deadline=_future(),
            )

    assert owner._facade_state._operation_lock._is_owned() is False
    if stage == "release":
        assert not hasattr(captured.value, "cleanup_authority")
        assert registry_module._OWNERS == {}
        return
    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=_future(),
    )
    assert registry_module._OWNERS == {}


def test_release_bounds_repeated_controls_and_preserves_the_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, owner = _owner(tmp_path)
    _install_process(monkeypatch, _FakeProcess())
    facade_module._start_relay_linux_build_process(owner, run_deadline=_future())
    facade_module._join_relay_linux_build_process(owner, join_deadline=_future())
    original_step = facade_module._release_step
    calls = 0
    failing = True

    def interrupted_step(candidate: object, deadline: float) -> bool:
        nonlocal calls
        calls += 1
        if failing:
            if calls == 1:
                raise KeyboardInterrupt
            raise SystemExit(7)
        return original_step(candidate, deadline)  # type: ignore[arg-type]

    monkeypatch.setattr(facade_module, "_release_step", interrupted_step)
    monkeypatch.setattr(facade_module, "_WAIT_SECONDS", 0.0)

    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module._release_relay_linux_build_process(
            owner,
            cleanup_deadline=_future(),
        )

    assert calls == facade_module._MAX_RELEASE_FAULTS
    assert captured.value.cleanup_authority is owner._cleanup_authority  # type: ignore[attr-defined]
    assert owner._facade_state._operation_lock._is_owned() is False
    failing = False
    facade_module._release_relay_linux_build_process(
        owner._cleanup_authority,
        cleanup_deadline=_future(),
    )

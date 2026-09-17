"""Synthetic tests for the private sole-worker build lifecycle."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import signal
import sys
import threading
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_thread as thread_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker as worker_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_local as local_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_process_local as process_module
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module

RUN_ID = "relay-b0-sole-worker"


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "_OWNERS", {})
    monkeypatch.setattr(registry_module, "_KERNELS", {})


def _environment() -> dict[str, str]:
    return {
        **spawn_module._FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{RUN_ID}",
    }


def _owner_graph(tmp_path: Path):
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
    controller = registry_module._preown_worker_controller(owner, 100.0)
    kernel = registry_module._reserve_worker_kernel(owner)
    return spec, raw, owner, controller, kernel


def _registered_worker(tmp_path: Path):
    spec, raw, owner, controller, kernel = _owner_graph(tmp_path)
    thread, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=worker_module._relay_linux_build_worker_entry,
        kernel_token=kernel._token,
    )
    assert type(thread) is threading.Thread
    assert coherent is True
    return spec, raw, owner, controller, kernel, thread


def _take_on_current_thread(tmp_path: Path):
    spec, raw, owner, controller, kernel = _owner_graph(tmp_path)
    worker = threading.current_thread()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)
    take = registry_module._take_worker_kernel(kernel._token)
    assert take is not None and take.status == "claimed"
    return spec, raw, owner, controller, kernel, take


class _FakeHandle:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class _FakeProcess:
    def __init__(self, polls: list[int | None], *, handles: bool = False) -> None:
        self.pid = 4311
        self.returncode: int | None = None
        self.polls = list(polls)
        self.poll_calls = 0
        self.wait_calls: list[float] = []
        self.stdin = _FakeHandle() if handles else None
        self.stdout = _FakeHandle() if handles else None
        self.stderr = _FakeHandle() if handles else None

    def poll(self) -> int | None:
        self.poll_calls += 1
        value = self.polls.pop(0) if self.polls else self.returncode
        if type(value) is int:
            self.returncode = value
        return value

    def wait(self, timeout: float) -> int:
        self.wait_calls.append(timeout)
        assert type(self.returncode) is int
        return self.returncode


class _MalformedProcess:
    pid = None


def _bound_process_driver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: _FakeProcess,
):
    spec, raw, _owner, controller, kernel, take = _take_on_current_thread(tmp_path)

    def spawn(_spec: object, destination: object) -> None:
        destination.publish(process)  # type: ignore[attr-defined]

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(process_module, "_local_process_group", lambda pid: pid)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)
    assert driver._step() == "active"
    assert driver._step() == "active"
    assert driver._state == "running"
    return spec, raw, controller, kernel, driver


def _drive_to_stop(driver: object, *, limit: int = 50) -> list[str]:
    outcomes: list[str] = []
    for _ in range(limit):
        outcome = driver._step()  # type: ignore[attr-defined]
        outcomes.append(outcome)
        if outcome == "waiting":
            driver._wait_completed()  # type: ignore[attr-defined]
        if outcome in {"terminal", "quarantined", "invalid"}:
            break
    return outcomes


def test_data_only_driver_is_constructed_before_any_kernel_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original = local_module._new_local_build_worker_driver

    def driver_factory():
        events.append("driver")
        driver = original()
        assert driver._kernel is None
        assert driver._claim is None
        assert driver._controller is None
        assert driver._spec is None
        assert driver._raw_destination is None
        assert driver._process is None
        return driver

    def take(_token: object):
        events.append("take")
        return None

    monkeypatch.setattr(worker_module, "_new_local_build_worker_driver", driver_factory)
    monkeypatch.setattr(worker_module, "_take_worker_kernel", take)

    worker_module._relay_linux_build_worker_entry(object())

    assert events == ["driver", "take"]


def test_driver_refuses_copy_serialization_and_structural_mutation() -> None:
    driver = local_module._new_local_build_worker_driver()

    assert not driver
    assert repr(driver) == "_LocalBuildWorkerDriver()"
    with pytest.raises(AttributeError):
        driver._state = "ready"  # type: ignore[misc]
    with pytest.raises(TypeError):
        copy(driver)
    with pytest.raises(TypeError):
        deepcopy(driver)
    with pytest.raises(TypeError):
        pickle.dumps(driver)


def test_bound_driver_revalidates_current_registered_thread_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, _owner, _controller, kernel, take = _take_on_current_thread(tmp_path)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)
    calls = 0

    def spawn(_spec: object, _destination: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(local_module.threading, "current_thread", lambda: object())

    assert driver._step() == "invalid"
    assert calls == 0
    assert driver._spawn_intended is False
    assert kernel._transition.phase == "claimed"


def test_unregistered_direct_entry_never_spawns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, raw, _owner, _controller, kernel = _owner_graph(tmp_path)
    called = False

    def spawn(_spec: object, _destination: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)

    worker_module._relay_linux_build_worker_entry(kernel._token)

    assert called is False
    assert raw._read(_spec) is None
    assert kernel._transition.phase == "available"


def test_spawn_failure_before_registration_reports_and_settles_exact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, thread = _registered_worker(tmp_path)
    calls = 0

    def spawn(_spec: object, _destination: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private spawn failure")

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)

    thread.start()
    thread.join(2.0)

    assert not thread.is_alive()
    assert calls == 1
    assert raw._read(spec) is None
    assert kernel._transition.phase == "settled"
    assert kernel._terminal is not None
    assert kernel._terminal.returncode is None
    assert kernel._terminal.succeeded is False
    assert controller._failed() is True
    assert controller._phase_value() == "settled"


@pytest.mark.parametrize(
    ("hook_name", "control", "kind", "code"),
    [
        ("_kernel_report_published", KeyboardInterrupt(), "keyboard", None),
        ("_kernel_terminal_published", SystemExit(68), "system-exit", 68),
    ],
)
def test_terminal_store_return_loss_rebinds_and_converges(
    tmp_path: Path,
    hook_name: str,
    control: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, thread = _registered_worker(tmp_path)
    calls = 0
    cut = False

    def spawn(_spec: object, _destination: object) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private spawn failure")

    def interrupt_after_store() -> None:
        nonlocal cut
        if not cut:
            cut = True
            raise control

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(registry_module, hook_name, interrupt_after_store)

    thread.start()
    thread.join(2.0)

    retained = controller._control_value()
    assert not thread.is_alive()
    assert calls == 1
    assert raw._read(spec) is None
    assert kernel._transition.phase == "settled"
    assert kernel._terminal is not None
    assert kernel._terminal.returncode is None
    assert kernel._terminal.succeeded is False
    assert controller._phase_value() == "settled"
    assert retained is not None
    assert retained.kind == kind
    assert retained.code == code


@pytest.mark.parametrize("operation", ["report", "settle"])
def test_terminal_registry_refusal_waits_before_retrying(
    tmp_path: Path,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, _owner, controller, kernel, thread = _registered_worker(tmp_path)
    original_report = registry_module._publish_worker_terminal
    original_settle = registry_module._settle_worker_kernel
    attempts = 0
    waits: list[float] = []

    def spawn(_spec: object, _destination: object) -> None:
        raise RuntimeError("private spawn failure")

    def report(*args: object, **kwargs: object) -> bool:
        nonlocal attempts
        if operation == "report":
            attempts += 1
            if attempts <= 2:
                return False
        return original_report(*args, **kwargs)  # type: ignore[arg-type]

    def settle(*args: object, **kwargs: object) -> bool:
        nonlocal attempts
        if operation == "settle":
            attempts += 1
            if attempts <= 2:
                return False
        return original_settle(*args, **kwargs)  # type: ignore[arg-type]

    def wait(_controller: object, timeout: float) -> None:
        waits.append(timeout)

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(worker_module, "_publish_worker_terminal", report)
    monkeypatch.setattr(worker_module, "_settle_worker_kernel", settle)
    monkeypatch.setattr(type(controller), "_wait", wait)

    thread.start()
    thread.join(2.0)

    assert not thread.is_alive()
    assert attempts == 3
    assert waits == [0.05, 0.05, 0.05]
    assert kernel._transition.phase == "settled"
    assert controller._phase_value() == "settled"


@pytest.mark.parametrize(
    ("control", "kind", "code"),
    [
        (KeyboardInterrupt(), "keyboard", None),
        (SystemExit(67), "system-exit", 67),
    ],
)
def test_first_spawn_control_without_registered_raw_is_latched_and_settled(
    tmp_path: Path,
    control: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, thread = _registered_worker(tmp_path)

    def spawn(_spec: object, _destination: object) -> None:
        raise control

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)

    thread.start()
    thread.join(2.0)

    retained = controller._control_value()
    assert not thread.is_alive()
    assert raw._read(spec) is None
    assert kernel._transition.phase == "settled"
    assert retained is not None
    assert retained.kind == kind
    assert retained.code == code


def test_registered_raw_return_loss_is_reconciled_once_and_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, take = _take_on_current_thread(tmp_path)
    process = _MalformedProcess()
    calls = 0

    def spawn(_spec: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        destination.publish(process)  # type: ignore[attr-defined]
        raise KeyboardInterrupt

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)

    with pytest.raises(KeyboardInterrupt):
        driver._step()
    controller._capture_control(KeyboardInterrupt())
    driver._note_failure()

    assert driver._step() == "waiting"
    driver._wait_completed()
    assert driver._step() == "active"
    assert driver._step() == "quarantined"
    assert calls == 1
    assert raw._read(spec) is process
    assert driver._process is process
    assert driver._terminal_values() is None
    assert kernel._transition.phase == "claimed"
    assert kernel._terminal is None
    assert controller._phase_value() == "quarantined"


def test_prestart_cancelled_registered_worker_exits_without_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, raw, _owner, _controller, kernel, thread = _registered_worker(tmp_path)
    calls = 0

    def spawn(_spec: object, _destination: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    assert registry_module._cancel_unstarted_worker_kernel(kernel)

    thread.start()
    thread.join(2.0)

    assert not thread.is_alive()
    assert calls == 0
    assert raw._read(_spec) is None
    assert kernel._transition.phase == "settled"
    assert kernel._worker is None


def test_successful_registered_spawn_is_not_misreported_as_build_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, take = _take_on_current_thread(tmp_path)
    process = _MalformedProcess()
    calls = 0

    def spawn(_spec: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        destination.publish(process)  # type: ignore[attr-defined]

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)

    assert driver._step() == "active"
    assert driver._step() == "quarantined"

    assert calls == 1
    assert raw._read(spec) is process
    assert controller._failed() is True
    assert kernel._transition.phase == "claimed"
    assert kernel._terminal is None


def test_worker_modules_remain_private_and_have_no_facade_surface() -> None:
    assert worker_module.__all__ == []
    assert local_module.__all__ == []
    for name in (
        "start",
        "join",
        "result",
        "release",
        "run_build",
        "cleanup_build",
    ):
        assert not hasattr(worker_module, name)
        assert not hasattr(local_module, name)


@pytest.mark.parametrize(("returncode", "succeeded"), [(0, True), (17, False)])
def test_exact_exit_reap_group_absence_handles_and_raw_clear(
    tmp_path: Path,
    returncode: int,
    succeeded: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([returncode], handles=True)
    spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)

    outcomes = _drive_to_stop(driver)

    assert outcomes[-1] == "terminal"
    assert driver._terminal_values() == (returncode, succeeded)
    assert process.wait_calls == [0.0]
    assert process.poll_calls == 1
    for handle in (process.stdin, process.stdout, process.stderr):
        assert type(handle) is _FakeHandle
        assert handle.closed is True
        assert handle.close_calls == 1
    assert driver._reaped is True
    assert driver._group_absent is True
    assert driver._signal_revoked is True
    assert driver._pid is None
    assert driver._pgid is None
    assert driver._process is None
    assert raw._read(spec) is None
    assert controller._phase_value() == "verifying"


def test_malformed_pid_quarantines_exact_raw_without_clear_or_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, _controller, kernel, take = _take_on_current_thread(tmp_path)
    process = _MalformedProcess()

    def spawn(_spec: object, destination: object) -> None:
        destination.publish(process)  # type: ignore[attr-defined]

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)

    assert driver._step() == "active"
    assert driver._step() == "quarantined"
    assert raw._read(spec) is process
    assert driver._process is process
    assert driver._terminal_values() is None
    assert kernel._transition.phase == "claimed"


def test_termination_uses_fresh_validation_then_one_term_and_one_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([None, None, None, None, -9])
    _spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    clocks = iter([0.0, 3.0])
    groups = iter([True, True, True, False])
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(process_module, "_local_monotonic", lambda: next(clocks))
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: next(groups))
    monkeypatch.setattr(
        process_module,
        "_local_signal_group",
        lambda pgid, value: signals.append((pgid, value)) or True,
    )
    controller._request_termination()

    outcomes = _drive_to_stop(driver)

    assert outcomes[-1] == "terminal"
    assert signals == [(4311, signal.SIGTERM), (4311, signal.SIGKILL)]
    assert driver._term_intended is driver._term_sent is True
    assert driver._kill_intended is driver._kill_sent is True
    assert driver._term_deadline == 2.0
    assert driver._kill_deadline == 5.0
    assert driver._terminal_values() == (-9, False)
    assert raw._read(_spec) is None


@pytest.mark.parametrize("stage", ["term", "kill"])
def test_reap_during_fresh_pre_signal_poll_never_signals_that_stage(
    tmp_path: Path,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polls = [None, 0] if stage == "term" else [None, None, None, 0]
    process = _FakeProcess(polls)
    _spec, _raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    clocks = iter([0.0] if stage == "term" else [0.0, 3.0])
    groups = iter([False] if stage == "term" else [True, True, False])
    signals: list[int] = []
    monkeypatch.setattr(process_module, "_local_monotonic", lambda: next(clocks))
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: next(groups))
    monkeypatch.setattr(
        process_module,
        "_local_signal_group",
        lambda _pgid, value: signals.append(value) or True,
    )
    controller._request_termination()

    outcomes = _drive_to_stop(driver)

    assert outcomes[-1] == "terminal"
    assert signals == ([] if stage == "term" else [signal.SIGTERM])
    assert driver._signal_revoked is True
    assert driver._terminal_values() == (0, False)


def test_fresh_pre_signal_process_group_drift_quarantines_without_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([None, None])
    spec, raw, controller, kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    group_calls = 0
    signals: list[int] = []

    def process_group(pid: int) -> int:
        nonlocal group_calls
        group_calls += 1
        return pid + 1

    monkeypatch.setattr(process_module, "_local_process_group", process_group)
    monkeypatch.setattr(process_module, "_local_monotonic", lambda: 0.0)
    monkeypatch.setattr(
        process_module,
        "_local_signal_group",
        lambda _pgid, value: signals.append(value) or True,
    )
    controller._request_termination()

    assert driver._step() == "quarantined"

    assert signals == []
    assert group_calls == 1
    assert driver._signal_revoked is True
    assert raw._read(spec) is process
    assert driver._terminal_values() is None
    assert kernel._transition.phase == "claimed"


def test_raw_clear_return_loss_reconciles_empty_identity_before_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([0])
    spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)
    original = spawn_module._RawBuildProcessDestination._clear
    cut = False

    def clear_then_interrupt(
        destination: object, exact_spec: object, exact_process: object
    ) -> bool:
        nonlocal cut
        cleared = original(destination, exact_spec, exact_process)  # type: ignore[arg-type]
        if not cut:
            cut = True
            raise KeyboardInterrupt
        return cleared

    monkeypatch.setattr(spawn_module._RawBuildProcessDestination, "_clear", clear_then_interrupt)
    outcomes: list[str] = []
    for _ in range(30):
        try:
            outcome = driver._step()
        except KeyboardInterrupt as error:
            controller._capture_control(error)
            driver._note_failure()
            continue
        outcomes.append(outcome)
        if outcome == "waiting":
            driver._wait_completed()
        if outcome == "terminal":
            break

    assert outcomes[-1] == "terminal"
    assert raw._read(spec) is None
    assert driver._process is None
    assert driver._raw_cleared is True
    assert driver._query_inflight is None
    assert driver._terminal_values() == (0, False)


@pytest.mark.parametrize(
    ("hook_name", "control"),
    [
        ("_kernel_report_published", KeyboardInterrupt()),
        ("_kernel_terminal_published", SystemExit(72)),
    ],
)
def test_zero_success_terminal_is_latched_across_registry_store_return_loss(
    tmp_path: Path,
    hook_name: str,
    control: KeyboardInterrupt | SystemExit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, _owner, controller, kernel, thread = _registered_worker(tmp_path)
    process = _FakeProcess([0])
    cut = False

    def spawn(_spec: object, destination: object) -> None:
        destination.publish(process)  # type: ignore[attr-defined]

    def interrupt_after_store() -> None:
        nonlocal cut
        if not cut:
            cut = True
            raise control

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    monkeypatch.setattr(process_module, "_local_process_group", lambda pid: pid)
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)
    monkeypatch.setattr(registry_module, hook_name, interrupt_after_store)

    thread.start()
    thread.join(2.0)

    assert not thread.is_alive()
    assert raw._read(spec) is None
    assert kernel._transition.phase == "settled"
    assert kernel._terminal is not None
    assert kernel._terminal.returncode == 0
    assert kernel._terminal.succeeded is True
    assert controller._control_value() is not None
    assert controller._phase_value() == "settled"


class _InvalidWaitProcess(_FakeProcess):
    def wait(self, timeout: float) -> object:
        self.wait_calls.append(timeout)
        return True


def test_persistent_invalid_wait_requires_one_yield_before_each_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _InvalidWaitProcess([7])
    spec, raw, _controller, kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )

    assert driver._step() == "active"
    assert process.wait_calls == [0.0]
    assert driver._step() == "waiting"
    assert process.wait_calls == [0.0]
    driver._wait_completed()
    assert driver._step() == "active"
    assert process.wait_calls == [0.0, 0.0]
    assert driver._step() == "waiting"

    assert raw._read(spec) is process
    assert driver._terminal_values() is None
    assert driver._signal_revoked is True
    assert kernel._transition.phase == "claimed"


@pytest.mark.parametrize("stage", ["closed-bit", "name-cleared", "handle-cleared"])
def test_handle_completion_store_loss_reconciles_without_second_close(
    tmp_path: Path,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([0], handles=True)
    spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    cut = False

    def checkpoint(name: str, current: str) -> None:
        nonlocal cut
        if name == "stdin" and current == stage and not cut:
            cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)
    monkeypatch.setattr(process_module, "_handle_checkpoint", checkpoint)
    outcomes: list[str] = []
    for _ in range(40):
        try:
            outcome = driver._step()
        except KeyboardInterrupt as error:
            controller._capture_control(error)
            driver._note_failure()
            continue
        outcomes.append(outcome)
        if outcome == "waiting":
            driver._wait_completed()
        if outcome == "terminal":
            break

    assert outcomes[-1] == "terminal"
    assert type(process.stdin) is _FakeHandle
    assert process.stdin.close_calls == 1
    assert raw._read(spec) is None
    assert driver._closed_handles == process_module._ALL_HANDLES
    assert driver._terminal_values() == (0, False)


def test_ambiguous_open_handle_close_is_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([0], handles=True)
    spec, raw, controller, kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    handle = process.stdin
    assert type(handle) is _FakeHandle

    def interrupt_before_close(_handle: object) -> None:
        handle.close_calls += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: False)
    monkeypatch.setattr(process_module, "_local_close_handle", interrupt_before_close)
    while driver._state != "closing":
        driver._step()
    with pytest.raises(KeyboardInterrupt) as raised:
        while True:
            driver._step()
    controller._capture_control(raised.value)
    driver._note_failure()

    assert driver._step() == "quarantined"
    assert handle.close_calls == 1
    assert handle.closed is False
    assert raw._read(spec) is process
    assert driver._terminal_values() is None
    assert kernel._transition.phase == "claimed"


@pytest.mark.parametrize(
    "stage",
    ["intent", "deadline", "phase", "completion", "state", "marker-cleared"],
)
def test_term_store_loss_reconciles_without_replaying_term(
    tmp_path: Path,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([None, None, 0])
    _spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    groups = iter([True, False])
    signals: list[int] = []
    cut = False

    def checkpoint(kind: str, current: str) -> None:
        nonlocal cut
        if kind == "term" and current == stage and not cut:
            cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(process_module, "_local_monotonic", lambda: 0.0)
    monkeypatch.setattr(process_module, "_local_group_exists", lambda _pgid: next(groups))
    monkeypatch.setattr(
        process_module,
        "_local_signal_group",
        lambda _pgid, value: signals.append(value) or True,
    )
    monkeypatch.setattr(process_module, "_signal_checkpoint", checkpoint)
    controller._request_termination()
    outcomes: list[str] = []
    for _ in range(40):
        try:
            outcome = driver._step()
        except KeyboardInterrupt as error:
            controller._capture_control(error)
            driver._note_failure()
            continue
        outcomes.append(outcome)
        if outcome == "waiting":
            driver._wait_completed()
        if outcome == "terminal":
            break

    assert outcomes[-1] == "terminal"
    expected = [] if stage in {"intent", "deadline", "phase"} else [signal.SIGTERM]
    assert signals == expected
    assert driver._term_intended is True
    assert driver._term_sent is bool(expected)
    assert driver._kill_intended is False
    assert raw._read(_spec) is None
    assert driver._terminal_values() == (0, False)


@pytest.mark.parametrize(
    "stage",
    ["intent", "deadline", "phase", "completion", "state", "marker-cleared"],
)
def test_kill_store_loss_reconciles_without_replaying_kill(
    tmp_path: Path,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess([None, None, None, None, 0])
    _spec, raw, controller, _kernel, driver = _bound_process_driver(
        tmp_path,
        monkeypatch,
        process,
    )
    signals: list[int] = []
    cut = False
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls == 1 else 3.0

    def checkpoint(kind: str, current: str) -> None:
        nonlocal cut
        if kind == "kill" and current == stage and not cut:
            cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(process_module, "_local_monotonic", clock)
    monkeypatch.setattr(
        process_module,
        "_local_group_exists",
        lambda _pgid: type(process.returncode) is not int,
    )
    monkeypatch.setattr(
        process_module,
        "_local_signal_group",
        lambda _pgid, value: signals.append(value) or True,
    )
    monkeypatch.setattr(process_module, "_signal_checkpoint", checkpoint)
    controller._request_termination()
    outcomes: list[str] = []
    for _ in range(50):
        try:
            outcome = driver._step()
        except KeyboardInterrupt as error:
            controller._capture_control(error)
            driver._note_failure()
            continue
        outcomes.append(outcome)
        if outcome == "waiting":
            driver._wait_completed()
        if outcome == "terminal":
            break

    assert outcomes[-1] == "terminal"
    expected = [signal.SIGTERM]
    if stage in {"completion", "state", "marker-cleared"}:
        expected.append(signal.SIGKILL)
    assert signals == expected
    assert driver._term_sent is True
    assert driver._kill_intended is True
    assert driver._kill_sent is (len(expected) == 2)
    assert raw._read(_spec) is None
    assert driver._terminal_values() == (0, False)

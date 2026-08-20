"""Synthetic tests for the private sole-worker build lifecycle."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
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
    assert waits == [0.05, 0.05]
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
    process = object()
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

    assert driver._step() == "quarantined"
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
    process = object()
    calls = 0

    def spawn(_spec: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        destination.publish(process)  # type: ignore[attr-defined]

    monkeypatch.setattr(local_module, "_spawn_registered_relay_linux_build", spawn)
    driver = local_module._new_local_build_worker_driver()
    assert driver._bind(take)

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

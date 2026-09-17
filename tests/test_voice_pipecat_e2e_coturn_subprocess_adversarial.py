"""Adversarial synthetic ownership cuts; this module never starts a real process."""

# ruff: noqa: E402

from __future__ import annotations

import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import TracebackType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_subprocess as facade_module
from scripts import voice_pipecat_e2e_coturn_subprocess_process as process_module
from scripts import voice_pipecat_e2e_coturn_subprocess_process_io as io_module
from scripts import voice_pipecat_e2e_coturn_subprocess_quarantine as quarantine_module
from scripts import voice_pipecat_e2e_coturn_subprocess_supervisor as supervisor_module
from scripts import voice_pipecat_e2e_coturn_subprocess_values as values_module
from scripts.voice_pipecat_e2e_coturn_subprocess import (
    CoturnSubprocessError,
    StreamingAttachedCommand,
    SubprocessChunk,
    SubprocessCommandRunner,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import (
    OWNERSHIP_GUARANTEE_SCOPE,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_state import (
    ControllerState,
    Lifecycle,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_supervisor import SupervisorSlot
from scripts.voice_pipecat_e2e_coturn_subprocess_values import raise_subprocess_error
from tests.coturn_traceback_helpers import traceback_contains
from tests.test_voice_pipecat_e2e_coturn_subprocess import (
    ControlPlan,
    FakeProcess,
    FakeSelector,
    Harness,
    docker_request,
    harness_with_tracking,
)


@pytest.fixture(autouse=True)
def _fast_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_module, "TERMINATION_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(process_module, "KILL_VERIFICATION_SECONDS", 0.02)
    monkeypatch.setattr(process_module, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(process_module, "_QUARANTINE_RETRY_SECONDS", 0.002)
    monkeypatch.setattr(quarantine_module, "TERMINATION_GRACE_SECONDS", 0.01)


def _wait_until(predicate: object, *, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        threading.Event().wait(0.001)
    return bool(callable(predicate) and predicate())


def _exception_reaches(error: BaseException, *targets: object) -> bool:
    target_ids = {id(target) for target in targets}
    seen: set[int] = set()
    pending: deque[object] = deque()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        pending.extend(current.args)
        frame: TracebackType | None = current.__traceback__
        while frame is not None:
            if "/tests/" not in frame.tb_frame.f_code.co_filename:
                pending.extend(tuple(frame.tb_frame.f_locals.values()))
            frame = frame.tb_next
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    while pending and len(seen) < 8_192:
        value = pending.popleft()
        identifier = id(value)
        if identifier in target_ids:
            return True
        if identifier in seen:
            continue
        seen.add(identifier)
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (deque, list, tuple, set, frozenset)):
            pending.extend(value)
        elif type(value).__module__.startswith("scripts.voice_pipecat_e2e_coturn"):
            namespace = getattr(value, "__dict__", None)
            if isinstance(namespace, dict):
                pending.append(namespace)
            slots = getattr(type(value), "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            pending.extend(
                getattr(value, slot)
                for slot in slots
                if isinstance(slot, str) and hasattr(value, slot)
            )
    return False


def _assert_fresh_scrubbed(
    error: BaseException,
    *targets: object,
    secrets: tuple[str | bytes, ...] = (),
) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not traceback_contains(error, *secrets)
    assert not _exception_reaches(error, *targets)


_SELECTOR_PHASES = [
    "selector-factory",
    *[
        f"{cut}-{stream}"
        for stream in ("stdout", "stderr", "stdin")
        for cut in ("set-blocking", "post-set-blocking", "register", "post-register")
    ],
]


@pytest.mark.parametrize("phase", _SELECTOR_PHASES)
@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("count", [1, 65])
def test_selector_setup_controls_always_reconcile_to_clean(
    phase: str,
    kind: type[BaseException],
    count: int,
) -> None:
    plan = ControlPlan()
    plan.add(phase, kind, count=count, code=-7)
    process = FakeProcess(auto_exit=False, pipes_eof=False, plan=plan)
    harness = harness_with_tracking(process)
    runner = harness.runner()
    expected = CoturnSubprocessError if kind is RuntimeError else kind
    with pytest.raises(expected) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-selector"))
    if kind is SystemExit:
        assert captured.value.code == -7
    _assert_fresh_scrubbed(captured.value, runner, secrets=("traceback-sentinel-selector",))
    if runner._slots:
        if kind is RuntimeError:
            assert runner.recover_quarantined(timeout_seconds=1.0)
        else:
            with pytest.raises(kind) as recovered:
                runner.recover_quarantined(timeout_seconds=1.0)
            if kind is SystemExit:
                assert recovered.value.code == -7
            _assert_fresh_scrubbed(recovered.value, runner)
    assert process.stdin is None and process.stdout is None and process.stderr is None
    assert runner._slots == []
    # A retained post-register mapping is reconciled on the next get_key. Any
    # interrupted stdin setup is rolled back by closing stdin at cleanup entry;
    # neither correct transaction repeats the already-completed vulnerable cut.
    if "post-register" not in phase and not phase.endswith("-stdin"):
        assert plan.calls[phase] >= count + 1
    assert all(selector.closed and selector.mapping == {} for selector in harness.selectors)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_selector_return_is_retained_before_control(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[BaseException],
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)

    def interrupt() -> None:
        if kind is SystemExit:
            raise SystemExit(256)
        raise KeyboardInterrupt

    monkeypatch.setattr(io_module, "_selector_acquired", interrupt)
    runner = harness.runner()
    with pytest.raises(kind) as captured:
        runner.start_attached(docker_request())
    if kind is SystemExit:
        assert captured.value.code == 256
    assert len(harness.selectors) == 1
    assert harness.selectors[0].closed
    assert harness.selectors[0].mapping == {}
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize(
    "seam",
    ["slot-reserved", "raw-kernel", "launch-returned", "handle-constructed"],
)
@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_all_admission_handoff_seams_cleanup_or_cancel(
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
    kind: type[BaseException],
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    target, attribute = {
        "slot-reserved": (facade_module, "_slot_reserved"),
        "raw-kernel": (supervisor_module, "_raw_kernel_registered"),
        "launch-returned": (facade_module, "_launch_returned"),
        "handle-constructed": (facade_module, "_handle_constructed"),
    }[seam]

    def interrupt() -> None:
        if kind is SystemExit:
            raise SystemExit(-7)
        raise kind()

    monkeypatch.setattr(target, attribute, interrupt)
    runner = harness.runner()
    expected = CoturnSubprocessError if kind is RuntimeError else kind
    with pytest.raises(expected) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-handoff"))
    if kind is SystemExit:
        assert captured.value.code == -7
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-handoff", "traceback-sentinel-raw-args"),
    )
    assert supervisor_module._KERNELS == {}
    assert runner._slots == []
    if seam in {"launch-returned", "handle-constructed"}:
        assert process.stdin is None and process.stdout is None and process.stderr is None
    else:
        assert harness.calls == []


def test_three_concurrent_starts_reserve_atomically_before_factory() -> None:
    processes = [FakeProcess(auto_exit=False, pipes_eof=False) for _ in range(3)]
    harness = harness_with_tracking(*processes)
    factory_lock = threading.Lock()
    factory_entered = 0
    two_entered = threading.Event()
    release = threading.Event()

    def hold_factory(_process: FakeProcess) -> None:
        nonlocal factory_entered
        with factory_lock:
            factory_entered += 1
            if factory_entered >= 2:
                two_entered.set()
        release.wait(1.0)

    harness.factory_entered = hold_factory
    runner = harness.runner()
    caller_barrier = threading.Barrier(3)
    handles: list[StreamingAttachedCommand] = []
    errors: list[BaseException] = []

    def start() -> None:
        caller_barrier.wait()
        try:
            handles.append(runner.start_attached(docker_request()))
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=start) for _ in range(3)]
    for worker in workers:
        worker.start()
    assert two_entered.wait(1.0)
    threading.Event().wait(0.02)
    release.set()
    for worker in workers:
        worker.join(1.0)
    assert factory_entered == 2
    assert len(harness.calls) == 2
    assert len(handles) == 2
    assert len(errors) == 1
    assert isinstance(errors[0], CoturnSubprocessError)
    assert str(errors[0]).endswith("command limit exceeded")
    for handle in handles:
        handle.terminate()


def test_spawn_deadline_quarantines_while_spawn_owner_retains_authority() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    entered = threading.Event()
    release = threading.Event()
    factory_calls = 0

    def hold_factory(_process: FakeProcess) -> None:
        nonlocal factory_calls
        factory_calls += 1
        entered.set()
        release.wait(2.0)

    harness.factory_entered = hold_factory
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls == 1 else 1.0

    runner = harness.runner(clock=clock)
    started = time.monotonic()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.start_attached(docker_request(timeout_seconds=0.1))
    assert time.monotonic() - started < 0.5
    assert entered.is_set() and factory_calls == 1
    assert runner._slots[0].controller.lifecycle() is Lifecycle.QUARANTINED
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request())
    assert factory_calls == 1
    release.set()
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_unresolved_first_contradictory_candidate_does_not_block_second_signal() -> None:
    first = FakeProcess(auto_exit=False, pipes_eof=False)
    first.pid_available = False
    second = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(first, second)

    def contradictory(argv: tuple[str, ...], **options: object) -> FakeProcess:
        register = options.pop("owner_register")
        assert callable(register)
        register(first)
        harness.calls.append((argv, options))
        return second

    runner = harness.runner(popen_factory=contradictory)
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.start_attached(docker_request())
    assert _wait_until(lambda: any(pid == second._pid for pid, _value in harness.signals))
    assert not any(pid == first._pid for pid, _value in harness.signals)
    first.pid_available = True
    assert runner.recover_quarantined(timeout_seconds=1.0)
    for process in (first, second):
        assert process.stdin is None and process.stdout is None and process.stderr is None


def test_poll_waits_for_terminal_after_worker_control() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    release_select = threading.Event()
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()

    class ControlledSelector(FakeSelector):
        def __init__(self, plan: ControlPlan) -> None:
            super().__init__(plan)
            self.first = True

        def select(self, timeout: float | None = None) -> list[tuple[object, int]]:
            if self.first:
                self.first = False
                release_select.wait(1.0)
                raise KeyboardInterrupt
            return super().select(timeout)

    def selector_factory() -> ControlledSelector:
        selector = ControlledSelector(harness.plan)
        harness.selectors.append(selector)
        return selector

    def blocking_signal(pid: int, value: int) -> None:
        if value == signal.SIGTERM:
            cleanup_entered.set()
            release_cleanup.wait(1.0)
        harness.signal_group(pid, value)

    handle = harness.runner(
        selector_factory=selector_factory,
        signal_process_group=blocking_signal,
    ).start_attached(docker_request())
    release_select.set()
    assert _wait_until(lambda: handle._slot.controller.control() is not None)
    errors: list[BaseException] = []

    def poll() -> None:
        try:
            handle.poll()
        except BaseException as error:
            errors.append(error)

    caller = threading.Thread(target=poll)
    caller.start()
    assert cleanup_entered.wait(1.0)
    assert caller.is_alive() and errors == []
    release_cleanup.set()
    caller.join(1.0)
    assert len(errors) == 1 and isinstance(errors[0], KeyboardInterrupt)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_public_boundary_preserves_worker_first_control_over_later_facade_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    handle = harness_with_tracking(process).runner().start_attached(docker_request())
    handle._slot.controller.capture_control(KeyboardInterrupt())

    def later_control(_handle: StreamingAttachedCommand) -> None:
        raise SystemExit(256)

    monkeypatch.setattr(
        StreamingAttachedCommand,
        "_synchronize_worker_control",
        later_control,
    )
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.poll()
    _assert_fresh_scrubbed(captured.value, handle)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("method", ["read_chunk", "drained", "poll", "collect", "terminate"])
@pytest.mark.parametrize("failure_kind", ["error", "control"])
def test_every_handle_boundary_scrubs_queued_chunks_self_and_old_exception_graphs(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    failure_kind: str,
) -> None:
    sentinel = b"traceback-sentinel-public-chunk"
    process = FakeProcess(stdout=sentinel, auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()
    handle = runner.start_attached(docker_request())
    assert _wait_until(lambda: handle._slot.controller.chunk_count() == 1)
    raw_chunk = handle._slot.controller._chunks[0]

    def fail(_handle: StreamingAttachedCommand) -> None:
        if failure_kind == "control":
            raise SystemExit(256)
        raise_subprocess_error("Coturn subprocess execution failed")

    monkeypatch.setattr(StreamingAttachedCommand, "_synchronize_worker_control", fail)
    expected = SystemExit if failure_kind == "control" else CoturnSubprocessError
    with pytest.raises(expected) as captured:
        if method == "read_chunk":
            handle.read_chunk(timeout_seconds=0.1)
        elif method == "drained":
            _ = handle.drained
        elif method == "poll":
            handle.poll()
        elif method == "collect":
            handle.collect(timeout_seconds=0.1)
        else:
            handle.terminate()
    if failure_kind == "control":
        assert captured.value.code == 256
    _assert_fresh_scrubbed(
        captured.value,
        handle,
        runner,
        raw_chunk,
        secrets=(sentinel,),
    )
    assert handle._slot.controller.chunk_count() == 0
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("method", ["read_chunk", "drained", "poll", "collect", "terminate"])
def test_every_handle_boundary_freshly_rejects_own_poison_with_queued_sentinel(
    method: str,
) -> None:
    controller = ControllerState()
    for state in (
        Lifecycle.CLEANUP_READY,
        Lifecycle.SPAWNING,
        Lifecycle.OWNED,
        Lifecycle.ACTIVE,
    ):
        assert controller.transition(state)
    raw_chunk = SubprocessChunk("stdout", b"traceback-sentinel-poison-chunk")
    assert controller.publish_chunk(raw_chunk)
    assert controller.transition(Lifecycle.QUARANTINED)
    runner = Harness().runner()
    slot = SupervisorSlot(controller=controller)
    runner._slots.append(slot)
    handle = StreamingAttachedCommand(
        facade_module._HANDLE_TOKEN,
        runner=runner,
        slot=slot,
        timeout_seconds=0.1,
        maximum_output_bytes=4_096,
    )
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$") as captured:
        if method == "read_chunk":
            handle.read_chunk(timeout_seconds=0.1)
        elif method == "drained":
            _ = handle.drained
        elif method == "poll":
            handle.poll()
        elif method == "collect":
            handle.collect(timeout_seconds=0.1)
        else:
            handle.terminate()
    _assert_fresh_scrubbed(
        captured.value,
        handle,
        runner,
        raw_chunk,
        secrets=("traceback-sentinel-poison-chunk",),
    )
    assert controller.chunk_count() == 0


def test_runner_recovery_validation_control_is_fresh_and_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    process.pid_available = False
    runner = harness_with_tracking(process).runner()
    with pytest.raises(CoturnSubprocessError):
        runner.start_attached(docker_request())

    def interrupt(*_args: object, **_kwargs: object) -> bool:
        raise SystemExit("traceback-sentinel-unsafe-exit")

    original_validator = facade_module.valid_seconds
    monkeypatch.setattr(facade_module, "valid_seconds", interrupt)
    with pytest.raises(SystemExit) as captured:
        runner.recover_quarantined(timeout_seconds=0.1)
    assert captured.value.code == 1
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-unsafe-exit", "traceback-sentinel-raw-args"),
    )
    process.pid_available = True
    monkeypatch.setattr(facade_module, "valid_seconds", original_validator)
    with pytest.raises(SystemExit) as recovered:
        runner.recover_quarantined(timeout_seconds=1.0)
    assert recovered.value.code == 1
    _assert_fresh_scrubbed(recovered.value, runner)


def test_runner_run_boundary_scrubs_built_result_request_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = b"traceback-sentinel-run-result"
    runner = harness_with_tracking(FakeProcess(stdout=sentinel)).runner()

    def interrupt() -> None:
        raise SystemExit("traceback-sentinel-run-exit")

    monkeypatch.setattr(facade_module, "_result_constructed", interrupt)
    with pytest.raises(SystemExit) as captured:
        runner.run(docker_request(stdin=b"traceback-sentinel-run-request"))
    assert captured.value.code == 1
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=(sentinel, "traceback-sentinel-run-exit", "traceback-sentinel-run-request"),
    )


def test_runner_constructor_control_clears_backend_and_self_before_fresh_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = harness_with_tracking(FakeProcess()).factory

    def interrupt(**_values: object) -> object:
        raise SystemExit("traceback-sentinel-constructor")

    monkeypatch.setattr(facade_module, "SupervisorSeams", interrupt)
    with pytest.raises(SystemExit) as captured:
        SubprocessCommandRunner(popen_factory=backend)
    assert captured.value.code == 1
    _assert_fresh_scrubbed(
        captured.value,
        backend,
        secrets=("traceback-sentinel-constructor", "traceback-sentinel-raw-args"),
    )


def test_chunk_validation_control_raises_fresh_without_raw_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExitOnComparison:
        def __ge__(self, _value: object) -> bool:
            raise SystemExit("traceback-sentinel-chunk-validation")

    malicious_bound = ExitOnComparison()
    monkeypatch.setattr(values_module, "CHUNK_BYTES", malicious_bound)
    with pytest.raises(SystemExit) as captured:
        SubprocessChunk("stdout", b"traceback-sentinel-chunk-value")
    assert captured.value.code == 1
    _assert_fresh_scrubbed(
        captured.value,
        malicious_bound,
        secrets=("traceback-sentinel-chunk-validation", "traceback-sentinel-chunk-value"),
    )


def test_stdin_offers_bounded_memoryviews_with_linear_total_copy_work() -> None:
    stdin = b"x" * 1_048_576
    process = FakeProcess(partial_stdin=1_024)
    runner = harness_with_tracking(process).runner()
    assert runner.run(docker_request(stdin=stdin)).returncode == 0
    assert bytes(process.input_pipe.data) == stdin
    assert process.input_pipe.offered_lengths
    assert set(process.input_pipe.offered_types) == {memoryview}
    assert max(process.input_pipe.offered_lengths) <= 4_096
    assert sum(process.input_pipe.offered_lengths) <= 4 * len(stdin) + 4_096


def test_guarantee_scope_explicitly_excludes_interpreter_and_parent_death() -> None:
    assert "live CPython interpreter" in OWNERSHIP_GUARANTEE_SCOPE
    assert "finite handled KeyboardInterrupt/SystemExit" in OWNERSHIP_GUARANTEE_SCOPE
    assert "interpreter finalization" in OWNERSHIP_GUARANTEE_SCOPE
    assert "abrupt parent death" in OWNERSHIP_GUARANTEE_SCOPE
    assert "not a parent-death guarantee" in OWNERSHIP_GUARANTEE_SCOPE

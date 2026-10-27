"""Synthetic ownership tests; no external process is ever launched."""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_subprocess as facade_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_subprocess_process as process_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_subprocess_spawn as spawn_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_host import CommandRequest  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_subprocess import (  # noqa: E402
    CoturnSubprocessError,
    StreamingAttachedCommand,
    SubprocessChunk,
    SubprocessCommandRunner,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_state import (  # noqa: E402
    CleanReceipt,
    ControllerState,
    Lifecycle,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_synthetic_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_module, "TERMINATION_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(process_module, "KILL_VERIFICATION_SECONDS", 0.02)
    monkeypatch.setattr(process_module, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(process_module, "_QUARANTINE_RETRY_SECONDS", 0.002)


class ControlPlan:
    def __init__(self) -> None:
        self._items: dict[str, deque[tuple[type[BaseException], object]]] = {}
        self.calls: dict[str, int] = {}

    def add(
        self,
        phase: str,
        kind: type[BaseException],
        *,
        count: int = 1,
        code: object = None,
    ) -> None:
        self._items.setdefault(phase, deque()).extend((kind, code) for _ in range(count))

    def fire(self, phase: str) -> None:
        self.calls[phase] = self.calls.get(phase, 0) + 1
        items = self._items.get(phase)
        if not items:
            return
        kind, code = items.popleft()
        if kind is SystemExit:
            raise SystemExit(code)
        raise kind()


class ReadPipe:
    def __init__(
        self,
        value: bytes = b"",
        *,
        eof: bool = True,
        bytewise: bool = False,
        plan: ControlPlan | None = None,
        phase: str = "read",
    ) -> None:
        self._value = bytearray(value)
        self.eof = eof
        self.bytewise = bytewise
        self.plan = plan or ControlPlan()
        self.phase = phase
        self.label = phase
        self.closed = False
        self.read_count = 0

    @property
    def ready(self) -> bool:
        return not self.closed and (bool(self._value) or self.eof)

    def read(self, maximum: int) -> bytes | None:
        self.plan.fire(self.phase)
        self.read_count += 1
        if self.closed:
            return b""
        if self._value:
            count = 1 if self.bytewise else min(maximum, len(self._value))
            result = bytes(self._value[:count])
            del self._value[:count]
            return result
        return b"" if self.eof else None

    def close(self) -> None:
        self.plan.fire(f"close-{self.phase}")
        self.closed = True


class WritePipe:
    def __init__(self, *, partial: int = 1_048_576, plan: ControlPlan | None = None) -> None:
        self.partial = partial
        self.plan = plan or ControlPlan()
        self.data = bytearray()
        self.closed = False
        self.label = "stdin"
        self.offered_lengths: list[int] = []
        self.offered_types: list[type[object]] = []

    @property
    def ready(self) -> bool:
        return not self.closed

    def write(self, value: object) -> int:
        self.plan.fire("write")
        assert isinstance(value, (bytes, bytearray, memoryview))
        self.offered_lengths.append(len(value))
        self.offered_types.append(type(value))
        count = min(self.partial, len(value))
        self.data.extend(value[:count])
        return count

    def flush(self) -> None:
        self.plan.fire("flush")

    def close(self) -> None:
        self.plan.fire("close-stdin")
        self.closed = True


class FakeSelector:
    def __init__(self, plan: ControlPlan) -> None:
        self.plan = plan
        self.mapping: dict[BinaryIO, tuple[int, str]] = {}
        self.registration_order: list[str] = []
        self.closed = False

    def register(self, fileobj: BinaryIO, events: int, data: object = None) -> object:
        self.plan.fire(f"register-{data}")
        name = str(data)
        self.mapping[fileobj] = events, name
        self.registration_order.append(name)
        self.plan.fire(f"post-register-{data}")
        return object()

    def unregister(self, fileobj: BinaryIO) -> object:
        phase = self.mapping.get(fileobj, (0, "unknown"))[1]
        self.plan.fire(f"unregister-{phase}")
        if fileobj not in self.mapping:
            raise KeyError
        del self.mapping[fileobj]
        self.plan.fire(f"post-unregister-{phase}")
        return object()

    def get_key(self, fileobj: BinaryIO) -> object:
        self.plan.fire("get-key")
        if fileobj not in self.mapping:
            raise KeyError
        events, name = self.mapping[fileobj]
        return SimpleNamespace(fileobj=fileobj, events=events, data=name)

    def select(self, timeout: float | None = None) -> list[tuple[object, int]]:
        self.plan.fire("select")
        events: list[tuple[object, int]] = []
        for stream, (mask, name) in tuple(self.mapping.items()):
            if bool(getattr(stream, "ready", False)):
                events.append((SimpleNamespace(fileobj=stream, data=name, events=mask), mask))
        if not events and timeout:
            threading.Event().wait(min(timeout, 0.001))
        return events

    def close(self) -> None:
        self.plan.fire("selector-close")
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        auto_exit: bool = True,
        group_alive: bool | None = None,
        pipes_eof: bool = True,
        bytewise: bool = False,
        partial_stdin: int = 1_048_576,
        plan: ControlPlan | None = None,
    ) -> None:
        self.plan = plan or ControlPlan()
        self._child_created = True
        self._pid = 4242
        self.pid_available = True
        self.args: object = ("traceback-sentinel-raw-args",)
        self.input_pipe = WritePipe(partial=partial_stdin, plan=self.plan)
        self.stdout_pipe = ReadPipe(
            stdout,
            eof=pipes_eof,
            bytewise=bytewise,
            plan=self.plan,
            phase="stdout",
        )
        self.stderr_pipe = ReadPipe(
            stderr,
            eof=pipes_eof,
            bytewise=bytewise,
            plan=self.plan,
            phase="stderr",
        )
        self.stdin = self.input_pipe
        self.stdout = self.stdout_pipe
        self.stderr = self.stderr_pipe
        self.planned_returncode = returncode
        self.returncode: int | None = None
        self.auto_exit = auto_exit
        self.group_alive = (not auto_exit) if group_alive is None else group_alive
        self.term_exits = True

    @property
    def pid(self) -> int:
        self.plan.fire("pid")
        if not self.pid_available:
            raise AttributeError("traceback-sentinel-pid-gap")
        return self._pid

    def poll(self) -> int | None:
        self.plan.fire("poll")
        if self.auto_exit and self.returncode is None:
            self.returncode = self.planned_returncode
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.plan.fire("wait")
        if self.returncode is None:
            raise subprocess.TimeoutExpired("redacted", timeout or 0.0)
        return self.returncode

    def exit(self, returncode: int) -> None:
        self.returncode = returncode
        self.group_alive = False
        self.stdout_pipe.eof = True
        self.stderr_pipe.eof = True


class Harness:
    def __init__(self, *processes: FakeProcess, plan: ControlPlan | None = None) -> None:
        self.processes = deque(processes)
        self.plan = plan or (processes[0].plan if processes else ControlPlan())
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.selectors: list[FakeSelector] = []
        self.signals: list[tuple[int, int]] = []
        self.factory_entered: Callable[[FakeProcess], None] | None = None

    def factory(self, argv: tuple[str, ...], **options: object) -> FakeProcess:
        self.plan.fire("factory")
        process = self.processes.popleft()
        register = options.pop("owner_register")
        assert callable(register)
        register(process)
        if self.factory_entered is not None:
            self.factory_entered(process)
        self.calls.append((argv, options))
        return process

    def signal_group(self, pid: int, value: int) -> None:
        self.plan.fire("signal-term" if value == signal.SIGTERM else "signal-kill")
        self.signals.append((pid, value))
        process = self._by_pid(pid)
        if value == signal.SIGKILL or process.term_exits:
            process.exit(-value)

    def group_exists(self, pid: int) -> bool:
        self.plan.fire("group-exists")
        return self._by_pid(pid).group_alive

    def group_identity(self, pid: int) -> int:
        self.plan.fire("group-id")
        return pid

    def selector_factory(self) -> FakeSelector:
        self.plan.fire("selector-factory")
        selector = FakeSelector(self.plan)
        self.selectors.append(selector)
        return selector

    def set_blocking(self, _stream: BinaryIO, blocking: bool) -> None:
        label = getattr(_stream, "label", "unknown")
        self.plan.fire(f"set-blocking-{label}")
        assert blocking is False
        self.plan.fire(f"post-set-blocking-{label}")

    def runner(self, **overrides: object) -> SubprocessCommandRunner:
        values = {
            "popen_factory": self.factory,
            "signal_process_group": self.signal_group,
            "process_group_exists": self.group_exists,
            "process_group_id": self.group_identity,
            "selector_factory": self.selector_factory,
            "set_blocking": self.set_blocking,
        }
        values.update(overrides)
        return SubprocessCommandRunner(**values)  # type: ignore[arg-type]

    def _by_pid(self, pid: int) -> FakeProcess:
        candidates = [*self.processes]
        for process in candidates:
            if process._pid == pid:
                return process
        # Factories remove processes from the queue, so tests attach this list lazily.
        return self.active[pid]

    @property
    def active(self) -> dict[int, FakeProcess]:
        return getattr(self, "_active", {})


def harness_with_tracking(*processes: FakeProcess) -> Harness:
    for index, process in enumerate(processes):
        process._pid = 4242 + index
    harness = Harness(*processes)
    harness._active = {process._pid: process for process in processes}  # type: ignore[attr-defined]
    return harness


def docker_request(**values: object) -> CommandRequest:
    options: dict[str, object] = {"argv": ("/usr/bin/docker", "version")}
    options.update(values)
    return CommandRequest(**options)  # type: ignore[arg-type]


def test_exact_spawn_options_replacement_environment_and_selector_order() -> None:
    process = FakeProcess(stdout=b"stdout", stderr=b"stderr", partial_stdin=2)
    harness = harness_with_tracking(process)
    result = harness.runner().run(docker_request(stdin=b"private-input"))
    assert (result.returncode, result.stdout, result.stderr) == (0, b"stdout", b"stderr")
    assert bytes(process.input_pipe.data) == b"private-input"
    argv, options = harness.calls[0]
    assert argv == ("/usr/bin/docker", "version")
    assert options == {
        "executable": "/usr/bin/docker",
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": "/",
        "env": {"LANG": "C", "LC_ALL": "C"},
        "shell": False,
        "close_fds": True,
        "start_new_session": True,
        "umask": 0o077,
        "bufsize": 0,
    }
    assert harness.selectors[0].registration_order == ["stdout", "stderr", "stdin"]
    assert process.args == ()
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_spawn_has_exactly_two_workers_then_only_the_supervisor() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    during_spawn: list[str] = []

    def inspect_threads(_process: FakeProcess) -> None:
        during_spawn.extend(
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("coturn-subprocess-")
        )

    harness.factory_entered = inspect_threads
    handle = harness.runner().start_attached(docker_request())
    assert sorted(during_spawn) == [
        "coturn-subprocess-spawn-owner",
        "coturn-subprocess-supervisor",
    ]
    assert [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("coturn-subprocess-")
    ] == ["coturn-subprocess-supervisor"]
    handle.terminate()


def test_no_handle_is_published_before_active() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    entered = threading.Event()
    release = threading.Event()

    def hold_factory(_process: FakeProcess) -> None:
        entered.set()
        release.wait(1.0)

    harness.factory_entered = hold_factory
    runner = harness.runner()
    result: list[StreamingAttachedCommand] = []
    worker = threading.Thread(target=lambda: result.append(runner.start_attached(docker_request())))
    worker.start()
    assert entered.wait(1.0)
    assert result == []
    release.set()
    worker.join(1.0)
    assert len(result) == 1
    result[0].terminate()


def test_simultaneous_streams_partial_stdin_and_memory_only_chunks() -> None:
    process = FakeProcess(
        stdout=b"alpha",
        stderr=b"beta",
        partial_stdin=1,
    )
    harness = harness_with_tracking(process)
    handle = harness.runner().start_attached(docker_request(stdin=b"12345"))
    chunks: dict[str, bytes] = {}
    while not handle.drained:
        chunk = handle.read_chunk(timeout_seconds=0.1)
        if chunk is not None:
            chunks[chunk.stream] = chunks.get(chunk.stream, b"") + chunk.data
            assert chunk.data.decode() not in repr(chunk)
    assert chunks == {"stdout": b"alpha", "stderr": b"beta"}
    assert bytes(process.input_pipe.data) == b"12345"
    assert handle.collect(timeout_seconds=0.1).returncode == 0
    assert repr(handle) == "StreamingAttachedCommand()"


def test_output_byte_chunk_and_queue_caps_are_independent() -> None:
    overflow = FakeProcess(stdout=b"secret-output")
    with pytest.raises(CoturnSubprocessError, match=r"output limit exceeded$"):
        harness_with_tracking(overflow).runner().run(docker_request(maximum_output_bytes=4))

    chunked = FakeProcess(stdout=b"x" * 4_097, bytewise=True)
    with pytest.raises(CoturnSubprocessError, match=r"chunk limit exceeded$"):
        harness_with_tracking(chunked).runner().run(docker_request(maximum_output_bytes=8_192))

    queued = FakeProcess(
        stdout=b"x" * 1_000,
        auto_exit=False,
        pipes_eof=False,
        bytewise=True,
    )
    queued.term_exits = True
    harness = harness_with_tracking(queued)
    handle = harness.runner().start_attached(docker_request(maximum_output_bytes=2_000))
    deadline = time.monotonic() + 1.0
    while handle._slot.controller.chunk_count() < 256 and time.monotonic() < deadline:
        threading.Event().wait(0.002)
    assert handle._slot.controller.chunk_count() == 256
    assert queued.stdout_pipe.read_count <= 256
    handle.terminate()


def test_timeout_kills_descendants_and_requires_both_pipe_eofs() -> None:
    process = FakeProcess(auto_exit=False, group_alive=True, pipes_eof=False)
    process.term_exits = False
    harness = harness_with_tracking(process)
    handle = harness.runner().start_attached(docker_request(timeout_seconds=0.1))
    with pytest.raises(CoturnSubprocessError, match=r"subprocess timed out$"):
        handle.collect(timeout_seconds=1.0)
    assert [value for _pid, value in harness.signals] == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode == -signal.SIGKILL
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_natural_leader_exit_quarantines_unproven_surviving_group() -> None:
    process = FakeProcess(stdout=b"complete", group_alive=True)
    process.term_exits = False
    harness = harness_with_tracking(process)
    runner = harness.runner()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.run(docker_request())
    assert harness.signals == []
    assert len(runner._slots) == 1
    assert runner._slots[0].controller.lifecycle() is Lifecycle.QUARANTINED
    process.group_alive = False
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize(
    "phase",
    [
        "clock",
        "select",
        "poll",
        "wait",
        "signal-term",
        "signal-kill",
        "group-exists",
        "stdout",
        "close-stdout",
        "unregister-stdout",
        "selector-close",
    ],
)
def test_controls_in_every_lifecycle_phase_defer_until_clean(phase: str) -> None:
    plan = ControlPlan()
    plan.add(phase, KeyboardInterrupt)
    process = FakeProcess(auto_exit=False, pipes_eof=False, plan=plan)
    process.term_exits = phase != "signal-kill"
    if phase == "signal-kill":
        process.term_exits = False
    harness = harness_with_tracking(process)
    if phase == "clock":
        clock_calls = 0

        def clock() -> float:
            nonlocal clock_calls
            clock_calls += 1
            if clock_calls == 1:
                raise KeyboardInterrupt
            return time.monotonic()

        runner = harness.runner(clock=clock)
    else:
        runner = harness.runner()
    with pytest.raises(KeyboardInterrupt) as captured:
        handle = runner.start_attached(docker_request())
        handle.terminate()
    assert captured.value.__context__ is None
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_more_than_sixty_four_controls_have_no_retry_count_escape() -> None:
    plan = ControlPlan()
    plan.add("signal-term", KeyboardInterrupt, count=80)
    process = FakeProcess(auto_exit=False, pipes_eof=False, plan=plan)
    harness = harness_with_tracking(process)
    handle = harness.runner().start_attached(docker_request())
    with pytest.raises(KeyboardInterrupt):
        handle.terminate()
    assert plan.calls["signal-term"] == 81
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize(
    ("first", "code"),
    [(KeyboardInterrupt, None), (SystemExit, -7), (SystemExit, 256), (SystemExit, None)],
)
def test_fork_to_pid_gap_absorbs_many_facade_controls_and_preserves_first(
    monkeypatch: pytest.MonkeyPatch,
    first: type[BaseException],
    code: int | None,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    process.pid_available = False
    harness = harness_with_tracking(process)
    entered = threading.Event()
    release = threading.Event()

    def hold(_process: FakeProcess) -> None:
        entered.set()
        release.wait(1.0)
        process.pid_available = True

    harness.factory_entered = hold
    original = ControllerState.wait_change
    calls = 0

    def interrupt_wait(self: ControllerState, timeout_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first is SystemExit:
                raise SystemExit(code)
            raise KeyboardInterrupt
        if calls <= 80:
            raise KeyboardInterrupt
        release.set()
        original(self, timeout_seconds)

    monkeypatch.setattr(ControllerState, "wait_change", interrupt_wait)
    expected = SystemExit if first is SystemExit else KeyboardInterrupt
    with pytest.raises(expected) as captured:
        harness.runner().start_attached(docker_request(stdin=b"traceback-sentinel-gap"))
    if first is SystemExit:
        assert captured.value.code == code
    assert calls >= 80
    assert not traceback_contains(captured.value, "traceback-sentinel-gap")
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("payload", ["unsafe-exit-secret", object(), True])
def test_unsafe_system_exit_payload_is_reduced_to_one_without_retention(payload: object) -> None:
    def fail_factory(*_args: object, **_kwargs: object) -> FakeProcess:
        raw_payload = payload
        raise SystemExit(raw_payload)

    runner = SubprocessCommandRunner(popen_factory=fail_factory)
    with pytest.raises(SystemExit) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-input"))
    assert captured.value.code == 1
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, "unsafe-exit-secret", "traceback-sentinel")


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_prepublished_no_child_partial_init_retries_cleanup_before_control(
    control: type[BaseException],
) -> None:
    plan = ControlPlan()
    plan.add("close-stdout", KeyboardInterrupt)
    process = FakeProcess(plan=plan)
    process._child_created = False

    def fail_after_registration(_argv: tuple[str, ...], **options: object) -> FakeProcess:
        register = options["owner_register"]
        assert callable(register)
        register(process)
        if control is SystemExit:
            raise SystemExit(-7)
        raise KeyboardInterrupt

    runner = SubprocessCommandRunner(popen_factory=fail_after_registration)
    with pytest.raises(control) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-partial"))
    if control is SystemExit:
        assert captured.value.code == -7
    assert not traceback_contains(captured.value, "traceback-sentinel")
    assert plan.calls["close-stdout"] == 2
    assert process.stdin is None and process.stdout is None and process.stderr is None
    assert process.args == ()
    assert runner.recover_quarantined(timeout_seconds=0.1)


def test_unknown_pid_quarantines_poison_before_backend_then_recovers() -> None:
    first = FakeProcess(auto_exit=False, pipes_eof=False)
    first.pid_available = False
    second = FakeProcess()
    harness = harness_with_tracking(first, second)
    runner = harness.runner()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.start_attached(docker_request())
    calls = len(harness.calls)
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request())
    assert len(harness.calls) == calls

    controller = runner._slots[0].controller
    assert controller.lifecycle() is Lifecycle.QUARANTINED
    assert controller.poisoned()
    first.pid_available = True
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert controller.lifecycle() is Lifecycle.CLEAN
    assert controller.clean_joined() and not controller.poisoned()
    assert runner.run(docker_request()).returncode == 0


def test_contradictory_factory_candidates_are_both_retained_and_cleaned() -> None:
    registered = FakeProcess(auto_exit=False, pipes_eof=False)
    returned = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(registered, returned)

    def contradictory_factory(argv: tuple[str, ...], **options: object) -> FakeProcess:
        register = options.pop("owner_register")
        assert callable(register)
        register(registered)
        harness.calls.append((argv, options))
        return returned

    runner = harness.runner(popen_factory=contradictory_factory)
    with pytest.raises(CoturnSubprocessError, match=r"start identity is invalid$"):
        runner.start_attached(docker_request())
    assert runner.recover_quarantined(timeout_seconds=1.0)
    for process in (registered, returned):
        assert process.stdin is None and process.stdout is None and process.stderr is None
        assert process.args == ()


def test_quarantine_blocks_other_command_artifacts_until_same_runner_recovers() -> None:
    active = FakeProcess(stdout=b"must-not-escape", auto_exit=False, pipes_eof=False)
    unknown = FakeProcess(auto_exit=False, pipes_eof=False)
    unknown.pid_available = False
    harness = harness_with_tracking(active, unknown)
    runner = harness.runner()
    handle = runner.start_attached(docker_request())
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.start_attached(docker_request())
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        handle.read_chunk(timeout_seconds=0.1)
    handle.terminate()
    unknown.pid_available = True
    assert runner.recover_quarantined(timeout_seconds=1.0)


def test_hard_maximum_two_live_commands_is_checked_before_factory() -> None:
    processes = [FakeProcess(auto_exit=False, pipes_eof=False) for _ in range(3)]
    harness = harness_with_tracking(*processes)
    runner = harness.runner()
    first = runner.start_attached(docker_request())
    second = runner.start_attached(docker_request())
    with pytest.raises(CoturnSubprocessError, match=r"command limit exceeded$"):
        runner.start_attached(docker_request())
    assert len(harness.calls) == 2
    first.terminate()
    second.terminate()


def test_invalid_requests_and_tampering_fail_before_factory() -> None:
    process = FakeProcess()
    harness = harness_with_tracking(process)
    runner = harness.runner()
    with pytest.raises(CoturnSubprocessError, match=r"request is invalid$"):
        runner.start_attached(CommandRequest(argv=("docker", "version")))
    request = docker_request()
    object.__setattr__(request, "stdin", b"x" * 1_048_577)
    with pytest.raises(CoturnSubprocessError, match=r"request is invalid$"):
        runner.start_attached(request)
    assert harness.calls == []


def test_controller_rejects_forged_clean_and_retains_no_raw_exception_graph() -> None:
    controller = ControllerState()
    assert not controller.accept_clean_receipt(object())
    with pytest.raises(TypeError, match="supervisor-owned"):
        CleanReceipt(  # type: ignore[call-arg]
            object(),
            nonce=controller.nonce,
            reaped_returncodes=(),
            result_index=None,
        )

    secret = b"traceback-sentinel-controller"
    process = FakeProcess(stdout=secret)
    harness = harness_with_tracking(process)
    runner = harness.runner()
    with pytest.raises(CoturnSubprocessError, match=r"output limit exceeded$") as captured:
        runner.run(docker_request(maximum_output_bytes=1))
    assert not traceback_contains(captured.value, secret)

    unknown = FakeProcess(
        stdout=b"traceback-sentinel-private-controller",
        auto_exit=False,
        pipes_eof=False,
    )
    unknown.pid_available = False
    quarantined_runner = harness_with_tracking(unknown).runner()
    with pytest.raises(CoturnSubprocessError):
        quarantined_runner.start_attached(docker_request())
    retained_controller = quarantined_runner._slots[0].controller
    namespace: dict[str, object] = {}
    exec(
        compile(
            "def expose(controller):\n    raise RuntimeError('synthetic')\n",
            "/synthetic/controller_scan.py",
            "exec",
        ),
        namespace,
    )
    with pytest.raises(RuntimeError) as controller_error:
        namespace["expose"](retained_controller)  # type: ignore[operator]
    assert not traceback_contains(controller_error.value, "traceback-sentinel-private-controller")
    unknown.pid_available = True
    assert quarantined_runner.recover_quarantined(timeout_seconds=1.0)


def test_result_construction_control_scrubs_result_and_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"traceback-sentinel-built-result"
    process = FakeProcess(stdout=secret)
    harness = harness_with_tracking(process)
    monkeypatch.setattr(
        facade_module,
        "_result_constructed",
        lambda: (_ for _ in ()).throw(SystemExit(256)),
    )
    with pytest.raises(SystemExit) as captured:
        harness.runner().run(docker_request())
    assert captured.value.code == 256
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)


def test_parent_signal_handlers_and_masks_are_never_mutated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("parent signal state must not be mutated")

    monkeypatch.setattr(process_module.signal, "signal", forbidden)
    monkeypatch.setattr(process_module.signal, "pthread_sigmask", forbidden)
    result = harness_with_tracking(FakeProcess()).runner().run(docker_request())
    assert result.returncode == 0
    assert signal.getsignal(signal.SIGINT) == previous_int
    assert signal.getsignal(signal.SIGTERM) == previous_term


def test_default_factory_prepublishes_object_before_synthetic_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []
    initialized: list[dict[str, object]] = []

    class SyntheticPopen:
        def __init__(self, _argv: tuple[str, ...], **options: object) -> None:
            assert published == [self]
            initialized.append(options)

    monkeypatch.setattr(spawn_module.subprocess, "Popen", SyntheticPopen)
    process = spawn_module.registered_popen_factory(
        ("/usr/bin/docker",),
        owner_register=published.append,
        start_new_session=True,
    )
    assert process is published[0]
    assert initialized == [{"start_new_session": True}]


def test_public_repr_and_chunk_constructor_never_reflect_raw_values() -> None:
    runner = harness_with_tracking(FakeProcess()).runner()
    assert repr(runner) == "SubprocessCommandRunner()"
    with pytest.raises(CoturnSubprocessError, match=r"chunk is invalid$") as captured:
        SubprocessChunk("stdout", b"traceback-sentinel" + b"x" * 4_096)
    assert not traceback_contains(captured.value, "traceback-sentinel")

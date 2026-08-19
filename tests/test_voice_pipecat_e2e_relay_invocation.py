# ruff: noqa: E402

from __future__ import annotations

import copy
import inspect
import os
import pickle
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation as facade
import scripts.voice_pipecat_e2e_relay_invocation_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_invocation_driver as driver_module
import scripts.voice_pipecat_e2e_relay_invocation_lifecycle as lifecycle_module
import scripts.voice_pipecat_e2e_relay_invocation_prebootstrap as prebootstrap_module
import scripts.voice_pipecat_e2e_relay_invocation_support as support_module
import scripts.voice_pipecat_e2e_relay_invocation_values as values_module
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeRun
from scripts.voice_pipecat_e2e_stack import E2E_SESSION_ID

CALL_ID = "123e4567-e89b-42d3-a456-426614174000"
NOW = 1_786_982_400.0
EXPIRY = int(NOW) + 60
FIXED_FAILURE = "Relay invocation failed"


class _Harness:
    def __init__(self) -> None:
        self.authorities = {role: object() for role in ("app", "web", "browser")}
        self.events: list[str] = []
        self.requests: list[tuple[str, str, str]] = []
        self.cuts: dict[tuple[str, str], list[BaseException]] = {}
        self.bare_returns: set[str] = set()
        self.exit_returncode = 0
        self._lock = threading.Lock()

    def cut(self, label: str, position: str, error: BaseException) -> None:
        self.cuts.setdefault((label, position), []).append(error)

    def count(self, event: str) -> int:
        with self._lock:
            return self.events.count(event)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.events)

    def _record(self, event: str) -> None:
        with self._lock:
            self.events.append(event)

    def _cut(self, label: str, position: str) -> None:
        error: BaseException | None = None
        with self._lock:
            pending = self.cuts.get((label, position))
            if pending:
                error = pending.pop(0)
        if error is not None:
            raise error

    def preown(self, role: str, destination: object) -> None:
        label = f"preown:{role}"
        self._record(label)
        self._cut(label, "before")
        if label in self.bare_returns:
            return self.authorities[role]  # type: ignore[return-value]
        destination.publish(self.authorities[role])  # type: ignore[attr-defined]
        self._cut(label, "after")

    def start(self, authority: object, request: object, destination: object) -> None:
        role = request.role  # type: ignore[attr-defined]
        label = f"start:{role}"
        assert authority is self.authorities[role]
        assert request.output_policy == "discard"  # type: ignore[attr-defined]
        with self._lock:
            self.requests.append(
                (role, request.completion, os.fspath(request.cwd))  # type: ignore[attr-defined]
            )
        self._record(label)
        self._cut(label, "before")
        if label in self.bare_returns:
            return True  # type: ignore[return-value]
        destination.publish(True)  # type: ignore[attr-defined]
        self._cut(label, "after")

    def prebootstrap(self, authority: object, request: object, destination: object) -> None:
        label = "prebootstrap"
        assert authority is self.authorities["app"]
        assert request.body == {  # type: ignore[attr-defined]
            "session_id": E2E_SESSION_ID,
            "voice_call_id": CALL_ID,
        }
        self._record(label)
        self._cut(label, "before")
        destination.publish(  # type: ignore[attr-defined]
            {
                "schema_version": 1,
                "status": "prepared",
                "expires_at_epoch_seconds": EXPIRY,
            }
        )
        self._cut(label, "after")

    def finish(self, authority: object, request: object, destination: object) -> None:
        label = "finish:browser"
        assert authority is self.authorities["browser"]
        assert request.absolute_deadline == 105.0  # type: ignore[attr-defined]
        self._record(label)
        self._cut(label, "before")
        destination.publish(  # type: ignore[attr-defined]
            {"status": "exited", "returncode": self.exit_returncode}
        )
        self._cut(label, "after")

    def stop(self, authority: object, destination: object) -> None:
        role = next(role for role, current in self.authorities.items() if current is authority)
        label = f"stop:{role}"
        self._record(label)
        self._cut(label, "before")
        if label in self.bare_returns:
            return True  # type: ignore[return-value]
        destination.publish(True)  # type: ignore[attr-defined]
        self._cut(label, "after")


class _UsernameSink:
    def __init__(
        self,
        *,
        cut_before_publication: BaseException | None = None,
        cut_after_publication: BaseException | None = None,
    ) -> None:
        self._username: bytearray | None = None
        self.calls = 0
        self._cut = cut_before_publication
        self._after_cut = cut_after_publication

    def _accept_relay_turn_username(self, username: str, destination: object) -> None:
        self.calls += 1
        candidate = username.encode("ascii")
        if self._username is None:
            self._username = bytearray(candidate)
        else:
            assert self._username == candidate
        if self._cut is not None:
            error = self._cut
            self._cut = None
            raise error
        destination.publish(True)  # type: ignore[attr-defined]
        if self._after_cut is not None:
            error = self._after_cut
            self._after_cut = None
            raise error

    def wipe(self) -> None:
        assert self._username is not None
        self._username[:] = b"\x00" * len(self._username)
        self._username.clear()


@pytest.fixture
def synthetic_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        support_module,
        "_canonical_file",
        lambda value, *, executable=False: Path(value),
    )
    monkeypatch.setattr(support_module, "_canonical_directory", lambda value: Path(value))
    monkeypatch.setattr(support_module, "_app_command", lambda: ("/synthetic/python", "app"))
    monkeypatch.setattr(
        support_module,
        "replacement_relay_backend_environment",
        lambda _run: {
            "MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID": CALL_ID,
            "SYNTHETIC_BACKEND": "1",
        },
    )
    monkeypatch.setattr(
        support_module,
        "replacement_relay_web_environment",
        lambda _run: {"SYNTHETIC_WEB": "1"},
    )
    monkeypatch.setattr(
        lifecycle_module,
        "replacement_relay_playwright_environment",
        lambda _run: {"SYNTHETIC_BROWSER": "1"},
    )
    monkeypatch.setattr(lifecycle_module.time, "monotonic", lambda: 100.0)
    yield
    assert not support_module._SECRET_RECORDS
    assert not cleanup_module._REGISTRY


def _components(harness: _Harness) -> tuple[RelayProbeRun, object, object, object]:
    run = object.__new__(RelayProbeRun)
    tools = support_module.new_synthetic_relay_invocation_tools(
        node_executable=Path("/synthetic/node"),
        epoch_clock=lambda: NOW,
    )
    driver = support_module.new_relay_invocation_driver(
        preown=harness.preown,
        start=harness.start,
        prebootstrap=harness.prebootstrap,
        finish=harness.finish,
        stop=harness.stop,
    )
    destination = support_module._new_relay_invocation_owner_destination()
    return run, driver, tools, destination


def _new_owner(parts: tuple[RelayProbeRun, object, object, object]) -> object:
    run, driver, tools, destination = parts
    return lifecycle_module._new_relay_invocation_owner(
        run,
        driver=driver,
        tools=tools,
        destination=destination,
    )


def _drive_to_finished(owner: object, sink: _UsernameSink | None = None) -> tuple[object, object]:
    receipt = lifecycle_module.stage_relay_backend(owner)  # type: ignore[arg-type]
    sink = sink or _UsernameSink()
    lifecycle_module._adopt_expected_turn_username(owner, sink)  # type: ignore[arg-type]
    lifecycle_module.stage_relay_web(owner)  # type: ignore[arg-type]
    lifecycle_module.start_relay_playwright(owner)  # type: ignore[arg-type]
    exit_receipt = lifecycle_module.finish_relay_playwright(  # type: ignore[arg-type]
        owner,
        timeout_seconds=5.0,
    )
    return receipt, exit_receipt


@contextmanager
def _control_on_return(function: Callable[..., object], error: BaseException) -> Iterator[None]:
    target = function.__code__
    previous = sys.gettrace()
    fired = False

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal fired
        if not fired and event == "return" and frame.f_code is target:  # type: ignore[attr-defined]
            fired = True
            sys.settrace(None)
            raise error
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)
    assert fired


@contextmanager
def _control_before_line(
    function: Callable[..., object],
    line: int,
    error: BaseException,
) -> Iterator[None]:
    target = function.__code__
    previous = sys.gettrace()
    fired = False

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal fired
        if (
            not fired
            and event == "line"
            and frame.f_code is target  # type: ignore[attr-defined]
            and frame.f_lineno == line  # type: ignore[attr-defined]
        ):
            fired = True
            sys.settrace(None)
            raise error
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)
    assert fired


def _source_line(function: Callable[..., object], marker: str, *, occurrence: int = 0) -> int:
    lines, first = inspect.getsourcelines(function)
    matches = [index for index, line in enumerate(lines) if marker in line]
    assert matches
    return first + matches[occurrence]


def _code_source_line(function: Callable[..., object], marker: str, *, occurrence: int = 0) -> int:
    path = Path(function.__code__.co_filename)
    lines = path.read_text().splitlines()
    code_lines = [line for _start, _end, line in function.__code__.co_lines() if line]
    matches = [
        number
        for number in range(min(code_lines), max(code_lines) + 1)
        if marker in lines[number - 1]
    ]
    assert matches
    return matches[occurrence]


@contextmanager
def _control_on_nth_return(
    function: Callable[..., object],
    error: BaseException,
    *,
    occurrence: int,
) -> Iterator[None]:
    target = function.__code__
    previous = sys.gettrace()
    returns = 0

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal returns
        if event == "return" and frame.f_code is target:  # type: ignore[attr-defined]
            returns += 1
            if returns == occurrence:
                sys.settrace(None)
                raise error
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)
    assert returns >= occurrence


@contextmanager
def _control_on_nth_line(
    function: Callable[..., object],
    line: int,
    error: BaseException,
    *,
    occurrence: int,
) -> Iterator[None]:
    target = function.__code__
    previous = sys.gettrace()
    hits = 0

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal hits
        if (
            event == "line"
            and frame.f_code is target  # type: ignore[attr-defined]
            and frame.f_lineno == line  # type: ignore[attr-defined]
        ):
            hits += 1
            if hits == occurrence:
                sys.settrace(None)
                raise error
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)
    assert hits >= occurrence


def _parallel(operation: Callable[[], object]) -> list[object]:
    barrier = threading.Barrier(3)
    results: list[object] = []
    errors: list[BaseException] = []

    def invoke() -> None:
        barrier.wait()
        try:
            results.append(operation())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    assert not errors
    return results


def _value_contains_identity(
    value: object,
    targets: set[int],
    seen: set[int],
    depth: int,
) -> bool:
    if id(value) in targets:
        return True
    if depth > 12 or len(seen) > 4_096 or id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, dict):
        return any(
            _value_contains_identity(item, targets, seen, depth + 1)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_value_contains_identity(item, targets, seen, depth + 1) for item in value)
    if isinstance(value, BaseException):
        return any(
            _value_contains_identity(item, targets, seen, depth + 1)
            for item in (*value.args, value.__cause__, value.__context__, value.__dict__)
            if item is not None
        )
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    return any(
        _value_contains_identity(getattr(value, name), targets, seen, depth + 1)
        for name in slots
        if isinstance(name, str) and hasattr(value, name)
    )


def _public_failure_reaches(error: BaseException, *targets: object) -> bool:
    identities = {id(target) for target in targets}
    if _value_contains_identity(error, identities, set(), 0):
        return True
    trace = error.__traceback__
    while trace is not None:
        if "/tests/" not in trace.tb_frame.f_code.co_filename and any(
            _value_contains_identity(value, identities, set(), 0)
            for value in trace.tb_frame.f_locals.values()
        ):
            return True
        trace = trace.tb_next
    return False


def _traceback_contains_text(error: BaseException, needle: str) -> bool:
    raw = needle.encode()
    trace = error.__traceback__
    while trace is not None:
        if "/tests/" not in trace.tb_frame.f_code.co_filename:
            for value in trace.tb_frame.f_locals.values():
                if _contains_text(value, raw, set(), 0):
                    return True
        trace = trace.tb_next
    return False


def _contains_text(value: object, needle: bytes, seen: set[int], depth: int) -> bool:
    if depth > 12 or len(seen) > 4_096 or id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, bytes):
        return needle in value
    if isinstance(value, str | os.PathLike):
        try:
            return needle in os.fspath(value).encode()
        except TypeError:
            return False
    if isinstance(value, dict):
        return any(
            _contains_text(item, needle, seen, depth + 1) for pair in value.items() for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_text(item, needle, seen, depth + 1) for item in value)
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    return any(
        _contains_text(getattr(value, name), needle, seen, depth + 1)
        for name in slots
        if isinstance(name, str) and hasattr(value, name)
    )


def test_exact_staged_lifecycle_is_idempotent_and_scrubs_private_username(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    assert _new_owner(parts) is owner
    secrets = support_module._load_secrets(owner._secret_key)

    receipt = lifecycle_module.stage_relay_backend(owner)
    username_buffer = secrets.prebootstrap_destination._expected_username
    assert isinstance(username_buffer, bytearray) and username_buffer
    assert lifecycle_module.relay_prebootstrap_result(owner) is receipt
    assert receipt.prepared is True and type(receipt.prepared) is bool
    assert receipt.reservation_bound is True and type(receipt.reservation_bound) is bool
    assert not receipt

    sink = _UsernameSink()
    lifecycle_module._adopt_expected_turn_username(owner, sink)
    assert sink.calls == 1
    assert username_buffer == bytearray()
    assert secrets.prebootstrap_destination._expected_username is None
    assert secrets.prebootstrap_destination._call_id == ""
    lifecycle_module.stage_relay_web(owner)
    lifecycle_module.start_relay_playwright(owner)
    exit_receipt = lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)
    assert exit_receipt.exited_successfully is True
    assert type(exit_receipt.exited_successfully) is bool and not exit_receipt

    lifecycle_module.cleanup_relay_invocation(owner)
    lifecycle_module.cleanup_relay_invocation(owner)
    sink.wipe()
    assert sink._username == bytearray()
    assert harness.snapshot() == [
        "preown:app",
        "preown:web",
        "preown:browser",
        "start:app",
        "prebootstrap",
        "start:web",
        "start:browser",
        "finish:browser",
        "stop:browser",
        "stop:web",
        "stop:app",
    ]
    assert [role for role, _completion, _cwd in harness.requests] == ["app", "web", "browser"]
    assert [completion for _role, completion, _cwd in harness.requests] == [
        "ready",
        "ready",
        "started",
    ]
    assert owner._state == "cleaned"
    assert owner._driver is owner._tools is owner._destination is None


def test_concurrent_retries_share_one_owner_and_each_stage_commits_once(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owners = _parallel(lambda: _new_owner(parts))
    assert owners[0] is owners[1]
    owner = owners[0]
    assert harness.snapshot() == ["preown:app", "preown:web", "preown:browser"]

    backend = _parallel(lambda: lifecycle_module.stage_relay_backend(owner))
    assert backend[0] is backend[1]
    sink = _UsernameSink()
    _parallel(lambda: lifecycle_module._adopt_expected_turn_username(owner, sink))
    _parallel(lambda: lifecycle_module.stage_relay_web(owner))
    _parallel(lambda: lifecycle_module.start_relay_playwright(owner))
    exits = _parallel(lambda: lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0))
    assert exits[0] is exits[1]
    _parallel(lambda: lifecycle_module.cleanup_relay_invocation(owner))
    sink.wipe()

    for event in (
        "start:app",
        "prebootstrap",
        "start:web",
        "start:browser",
        "finish:browser",
        "stop:browser",
        "stop:web",
        "stop:app",
    ):
        assert harness.count(event) == 1


def test_callback_return_loss_reconciles_publication_and_preserves_first_control(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    harness.cut("preown:app", "after", RuntimeError("lost preown return"))
    harness.cut("start:app", "after", RuntimeError("lost start return"))
    harness.cut("prebootstrap", "after", SystemExit(23))
    harness.cut("start:web", "after", RuntimeError("lost web return"))
    harness.cut("start:browser", "after", SystemExit(25))
    harness.cut("finish:browser", "after", SystemExit(26))
    harness.cut("stop:browser", "after", SystemExit(27))
    harness.cut("stop:web", "after", RuntimeError("lost stop return"))
    owner = _new_owner(_components(harness))

    with pytest.raises(SystemExit) as backend_cut:
        lifecycle_module.stage_relay_backend(owner)
    assert backend_cut.value.code == 23
    receipt = lifecycle_module.stage_relay_backend(owner)
    assert receipt is lifecycle_module.relay_prebootstrap_result(owner)
    assert harness.count("start:app") == harness.count("prebootstrap") == 1

    sink = _UsernameSink(cut_before_publication=SystemExit(24))
    with pytest.raises(SystemExit) as adoption_cut:
        lifecycle_module._adopt_expected_turn_username(owner, sink)
    assert adoption_cut.value.code == 24 and sink.calls == 2
    lifecycle_module._adopt_expected_turn_username(owner, sink)

    lifecycle_module.stage_relay_web(owner)
    with pytest.raises(SystemExit) as browser_cut:
        lifecycle_module.start_relay_playwright(owner)
    assert browser_cut.value.code == 25
    lifecycle_module.start_relay_playwright(owner)
    with pytest.raises(SystemExit) as finish_cut:
        lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)
    assert finish_cut.value.code == 26
    lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)

    with pytest.raises(SystemExit) as cleanup_cut:
        lifecycle_module.cleanup_relay_invocation(owner)
    assert cleanup_cut.value.code == 27
    lifecycle_module.cleanup_relay_invocation(owner)
    sink.wipe()
    for event in ("start:web", "start:browser", "finish:browser"):
        assert harness.count(event) == 1
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]


@pytest.mark.parametrize("publication", ["registration", "owner"])
def test_constructor_assignment_cut_recovers_owner_without_duplicate_preown(
    synthetic_runtime: None,
    publication: str,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    target = (
        lifecycle_module._register_cleanup_owner
        if publication == "registration"
        else driver_module._RelayInvocationOwnerDestination._publish_owner
    )
    with _control_on_return(target, SystemExit(31)):
        with pytest.raises(SystemExit) as cut:
            _new_owner(parts)
    assert cut.value.code == 31
    run, driver, tools, destination = parts
    assert destination._read(run, driver, tools) == (None, False)
    assert harness.count("preown:app") == 0

    owner = _new_owner(parts)
    assert harness.snapshot()[:3] == ["preown:app", "preown:web", "preown:browser"]
    lifecycle_module.cleanup_relay_invocation(owner)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]


def test_ready_and_final_return_cuts_retry_the_same_canonical_owner(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    with _control_on_return(
        driver_module._RelayInvocationOwnerDestination._publish_ready,
        SystemExit(32),
    ):
        with pytest.raises(SystemExit) as ready_cut:
            _new_owner(parts)
    assert ready_cut.value.code == 32
    ready_owner = _new_owner(parts)
    assert harness.snapshot() == ["preown:app", "preown:web", "preown:browser"]

    with _control_on_return(lifecycle_module._new_relay_invocation_owner, SystemExit(33)):
        with pytest.raises(SystemExit) as return_cut:
            _new_owner(parts)
    assert return_cut.value.code == 33
    assert _new_owner(parts) is ready_owner
    assert harness.snapshot() == ["preown:app", "preown:web", "preown:browser"]
    lifecycle_module.cleanup_relay_invocation(ready_owner)


def test_atomic_owner_record_survives_publish_and_clear_store_cuts(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    publish_line = _source_line(
        driver_module._RelayInvocationOwnerDestination._publish_owner,
        "return None",
    )
    with _control_before_line(
        driver_module._RelayInvocationOwnerDestination._publish_owner,
        publish_line,
        SystemExit(39),
    ):
        with pytest.raises(SystemExit) as publish_cut:
            _new_owner(parts)
    assert publish_cut.value.code == 39
    assert harness.count("preown:app") == 0
    run, driver, tools, destination = parts
    assert destination._read(run, driver, tools) == (None, False)

    owner = _new_owner(parts)
    clear_line = _source_line(
        driver_module._RelayInvocationOwnerDestination._clear,
        "return True",
        occurrence=-1,
    )
    with _control_before_line(
        driver_module._RelayInvocationOwnerDestination._clear,
        clear_line,
        SystemExit(40),
    ):
        with pytest.raises(SystemExit) as clear_cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert clear_cut.value.code == 40
    assert destination._read(run, driver, tools) == (None, False)
    lifecycle_module.cleanup_relay_invocation(owner)
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1


def test_three_consecutive_owner_reads_preserve_each_first_control_then_construct_once(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    original = driver_module._RelayInvocationOwnerDestination._read
    codes = [41, 42, 43]

    def interrupted(destination: object, run: object, driver: object, tools: object) -> object:
        if codes:
            raise SystemExit(codes.pop(0))
        return original(destination, run, driver, tools)  # type: ignore[arg-type]

    monkeypatch.setattr(driver_module._RelayInvocationOwnerDestination, "_read", interrupted)
    with pytest.raises(SystemExit) as first:
        _new_owner(parts)
    assert first.value.code == 41
    with pytest.raises(SystemExit) as second:
        _new_owner(parts)
    assert second.value.code == 43
    owner = _new_owner(parts)
    assert harness.snapshot() == ["preown:app", "preown:web", "preown:browser"]
    lifecycle_module.cleanup_relay_invocation(owner)


@pytest.mark.parametrize("boundary", ["child-clear", "owner-clear"])
def test_cleanup_reconciles_exact_clear_return_cut_once(
    synthetic_runtime: None,
    boundary: str,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    target = (
        driver_module._RelayChildAuthorityDestination._clear
        if boundary == "child-clear"
        else driver_module._RelayInvocationOwnerDestination._clear
    )
    with _control_on_return(target, SystemExit(34)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.code == 34
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    lifecycle_module.cleanup_relay_invocation(owner)
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1
    assert owner._state == "cleaned"


def test_four_consecutive_pre_stop_receipt_controls_halt_without_stopping(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    original = driver_module._RelayChildStopDestination._read
    codes = [44, 45, 46, 47]

    def interrupted(destination: object, owner_token: object, role: str) -> object:
        if role == "browser" and codes:
            raise SystemExit(codes.pop(0))
        return original(destination, owner_token, role)  # type: ignore[arg-type]

    monkeypatch.setattr(driver_module._RelayChildStopDestination, "_read", interrupted)
    with pytest.raises(SystemExit) as first:
        lifecycle_module.cleanup_relay_invocation(owner)
    assert first.value.code == 44
    authority = first.value.cleanup_authority  # type: ignore[attr-defined]
    assert authority is owner._cleanup_authority
    assert harness.count("stop:browser") == 0
    assert harness.count("stop:web") == harness.count("stop:app") == 0

    with pytest.raises(SystemExit) as second:
        lifecycle_module.cleanup_relay_invocation(authority)
    assert second.value.code == 46
    assert second.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert harness.count("stop:browser") == 0

    lifecycle_module.cleanup_relay_invocation(authority)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    assert harness.count("stop:browser") == 1
    lifecycle_module.cleanup_relay_invocation(authority)


def test_one_interrupted_pre_stop_observation_never_authorizes_stop(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    original = driver_module._RelayChildStopDestination._read
    interrupted = True

    def read(destination: object, owner_token: object, role: str) -> object:
        nonlocal interrupted
        if role == "browser" and interrupted:
            interrupted = False
            raise SystemExit(59)
        return original(destination, owner_token, role)  # type: ignore[arg-type]

    monkeypatch.setattr(driver_module._RelayChildStopDestination, "_read", read)
    with pytest.raises(SystemExit) as cut:
        lifecycle_module.cleanup_relay_invocation(owner)
    authority = cut.value.cleanup_authority  # type: ignore[attr-defined]
    assert cut.value.code == 59
    assert harness.count("stop:browser") == 0
    assert authority is owner._cleanup_authority

    lifecycle_module.cleanup_relay_invocation(authority)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1


@pytest.mark.parametrize(
    ("boundary", "occurrence"),
    [("field", index) for index in range(1, 12)] + [("state", 1), ("phase", 1), ("return", 1)],
)
def test_every_terminal_owner_scrub_cut_resumes_from_durable_phase(
    synthetic_runtime: None,
    boundary: str,
    occurrence: int,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    authority = owner._cleanup_authority
    secret_key = owner._secret_key
    if boundary == "field":
        line = _source_line(cleanup_module._scrub_terminal_owner, "setattr(owner")
        context = _control_on_nth_line(
            cleanup_module._scrub_terminal_owner,
            line,
            SystemExit(60 + occurrence),
            occurrence=occurrence,
        )
    elif boundary == "return":
        context = _control_on_return(cleanup_module._scrub_terminal_owner, SystemExit(73))
    else:
        marker = 'owner._state = "cleaned"' if boundary == "state" else "scrubbed"
        line = _source_line(cleanup_module._scrub_terminal_owner, marker)
        context = _control_before_line(
            cleanup_module._scrub_terminal_owner,
            line,
            SystemExit(72 if boundary == "state" else 73),
        )

    with context:
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert owner._cleanup_phase in {"terminal", "scrubbed"}
    assert cleanup_module._REGISTRY.get(authority._key) is owner
    assert secret_key not in support_module._SECRET_RECORDS

    lifecycle_module.cleanup_relay_invocation(authority)
    assert owner._cleanup_phase == "scrubbed" and owner._state == "cleaned"
    assert all(getattr(owner, name) is None for name in cleanup_module._TERMINAL_OWNER_FIELDS)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    lifecycle_module.cleanup_relay_invocation(authority)


def test_secret_scrub_return_loss_retains_capacity_until_concurrent_retry(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup_module, "_MAX_ACTIVE_INVOCATIONS", 1)
    monkeypatch.setattr(support_module, "_MAX_ACTIVE_INVOCATIONS", 1)
    first_harness, second_harness = _Harness(), _Harness()
    first = _new_owner(_components(first_harness))
    second_parts = _components(second_harness)
    authority = first._cleanup_authority
    secret_key = first._secret_key
    secrets = support_module._load_secrets(secret_key)

    with _control_on_return(support_module._InvocationSecrets.scrub, SystemExit(74)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(first)
    assert cut.value.code == 74
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert support_module._SECRET_RECORDS.get(secret_key) is secrets
    assert cleanup_module._REGISTRY.get(authority._key) is first
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
        _new_owner(second_parts)

    _parallel(lambda: lifecycle_module.cleanup_relay_invocation(authority))
    assert secret_key not in support_module._SECRET_RECORDS
    second = _new_owner(second_parts)
    lifecycle_module.cleanup_relay_invocation(second)
    for role in ("browser", "web", "app"):
        assert first_harness.count(f"stop:{role}") == 1


def test_secret_delete_cut_retains_record_until_scrubbed_retry(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    authority = owner._cleanup_authority
    secret_key = owner._secret_key
    line = _source_line(cleanup_module._drop_secrets, "del _SECRET_RECORDS[key]")
    with _control_before_line(cleanup_module._drop_secrets, line, SystemExit(75)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.code == 75
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert secret_key in support_module._SECRET_RECORDS
    lifecycle_module.cleanup_relay_invocation(authority)
    assert secret_key not in support_module._SECRET_RECORDS


def test_first_cleanup_control_precedes_registry_release_control(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    original_clear = driver_module._RelayChildAuthorityDestination._clear
    original_release = cleanup_module._release_cleanup_owner
    clear_pending = True
    release_pending = True
    release_calls = 0

    def clear(destination: object, child: object) -> bool:
        nonlocal clear_pending
        committed = original_clear(destination, child)  # type: ignore[arg-type]
        if clear_pending:
            clear_pending = False
            raise SystemExit(76)
        return committed

    def release(cleanup_authority: object, candidate: object) -> None:
        nonlocal release_calls, release_pending
        release_calls += 1
        original_release(cleanup_authority, candidate)  # type: ignore[arg-type]
        if release_pending:
            release_pending = False
            raise SystemExit(77)

    monkeypatch.setattr(driver_module._RelayChildAuthorityDestination, "_clear", clear)
    monkeypatch.setattr(cleanup_module, "_release_cleanup_owner", release)

    with pytest.raises(SystemExit) as first:
        lifecycle_module.cleanup_relay_invocation(owner)
    assert first.value.code == 76
    assert first.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert release_calls == 0
    assert cleanup_module._REGISTRY.get(authority._key) is owner
    assert not _public_failure_reaches(first.value, harness, parts[1], owner)

    with pytest.raises(SystemExit) as second:
        lifecycle_module.cleanup_relay_invocation(authority)
    assert second.value.code == 77
    assert second.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert release_calls == 1 and authority._key not in cleanup_module._REGISTRY
    lifecycle_module.cleanup_relay_invocation(authority)


def test_stored_cleanup_control_precedes_later_cleanup_return_control(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    original_clear = driver_module._RelayChildAuthorityDestination._clear
    clear_pending = True

    def clear(destination: object, child: object) -> bool:
        nonlocal clear_pending
        committed = original_clear(destination, child)  # type: ignore[arg-type]
        if clear_pending:
            clear_pending = False
            raise SystemExit(81)
        return committed

    monkeypatch.setattr(driver_module._RelayChildAuthorityDestination, "_clear", clear)
    with _control_on_return(cleanup_module._cleanup_invocation_owner, SystemExit(82)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.code == 81
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert cleanup_module._REGISTRY.get(authority._key) is owner
    assert owner._control is None
    assert not _public_failure_reaches(cut.value, harness, parts[1], owner)

    lifecycle_module.cleanup_relay_invocation(authority)
    assert authority._key not in cleanup_module._REGISTRY
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1


def test_stored_cleanup_control_survives_its_guarded_clear_cut(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    authority = owner._cleanup_authority
    original_clear = driver_module._RelayChildAuthorityDestination._clear
    clear_pending = True

    def clear(destination: object, child: object) -> bool:
        nonlocal clear_pending
        committed = original_clear(destination, child)  # type: ignore[arg-type]
        if clear_pending:
            clear_pending = False
            raise SystemExit(83)
        return committed

    monkeypatch.setattr(driver_module._RelayChildAuthorityDestination, "_clear", clear)
    line = _code_source_line(
        lifecycle_module.cleanup_relay_invocation,
        "boundary_owner._control = None",
    )
    with _control_before_line(
        lifecycle_module.cleanup_relay_invocation,
        line,
        SystemExit(84),
    ):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.code == 83
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert owner._control is None
    assert cleanup_module._REGISTRY.get(authority._key) is owner

    lifecycle_module.cleanup_relay_invocation(authority)
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1


def test_successful_cleanup_releases_aggregate_registry_once(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    original_release = cleanup_module._release_cleanup_owner
    release_calls = 0

    def release(cleanup_authority: object, candidate: object) -> None:
        nonlocal release_calls
        release_calls += 1
        original_release(cleanup_authority, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(cleanup_module, "_release_cleanup_owner", release)
    monkeypatch.setattr(lifecycle_module, "_release_cleanup_owner", release, raising=False)
    lifecycle_module.cleanup_relay_invocation(owner)
    assert release_calls == 1


@pytest.mark.parametrize("position", ["before", "after"])
def test_registry_release_failure_returns_only_the_retry_authority(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
    position: str,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    original_release = cleanup_module._release_cleanup_owner
    pending = True

    def release(cleanup_authority: object, candidate: object) -> None:
        nonlocal pending
        if pending and position == "before":
            pending = False
            raise RuntimeError("private release failure")
        original_release(cleanup_authority, candidate)  # type: ignore[arg-type]
        if pending:
            pending = False
            raise RuntimeError("private release failure")

    monkeypatch.setattr(cleanup_module, "_release_cleanup_owner", release)
    with pytest.raises(facade.RelayInvocationCleanupRequired) as captured:
        lifecycle_module.cleanup_relay_invocation(owner)
    assert captured.value.cleanup_authority is authority
    assert str(captured.value) == "Relay invocation cleanup failed"
    assert not _public_failure_reaches(captured.value, harness, parts[1], owner)

    lifecycle_module.cleanup_relay_invocation(authority)
    assert authority._key not in cleanup_module._REGISTRY


def test_decorator_resolution_failure_retains_the_input_retry_authority(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority

    def fail_resolution(candidate: object, owner_type: type[object]) -> object:
        assert candidate is authority and owner_type is lifecycle_module.RelayInvocationOwner
        raise RuntimeError("private resolution failure")

    monkeypatch.setattr(cleanup_module, "_resolve_cleanup_owner", fail_resolution)
    with pytest.raises(facade.RelayInvocationCleanupRequired) as captured:
        lifecycle_module.cleanup_relay_invocation(authority)
    assert captured.value.cleanup_authority is authority
    assert not _public_failure_reaches(captured.value, harness, parts[1], owner)
    assert authority._key not in cleanup_module._REGISTRY


def test_stage_cleanup_release_failure_returns_the_retry_authority(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    harness.bare_returns.add("start:app")
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    original_release = cleanup_module._release_cleanup_owner
    pending = True

    def release(cleanup_authority: object, candidate: object) -> None:
        nonlocal pending
        if pending:
            pending = False
            raise RuntimeError("private release failure")
        original_release(cleanup_authority, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(cleanup_module, "_release_cleanup_owner", release)
    with pytest.raises(facade.RelayInvocationCleanupRequired) as captured:
        lifecycle_module.stage_relay_backend(owner)
    assert captured.value.cleanup_authority is authority
    assert cleanup_module._REGISTRY.get(authority._key) is owner
    assert not _public_failure_reaches(captured.value, harness, parts[1], owner)

    harness.bare_returns.clear()
    lifecycle_module.cleanup_relay_invocation(authority)
    assert authority._key not in cleanup_module._REGISTRY


@pytest.mark.parametrize(
    "marker",
    [
        "first = args[0]",
        'stored = getattr(boundary_owner, "_control", None)',
        "args = ()",
        "kwargs = {}",
        "candidate = first = boundary_owner = None",
    ],
)
def test_decorator_finalization_line_controls_scrub_raw_argument_graph(
    synthetic_runtime: None,
    marker: str,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    raw = {"owner": owner, "driver": parts[1], "authorities": harness.authorities}
    line = _code_source_line(lifecycle_module.cleanup_relay_invocation, marker)
    with _control_before_line(
        lifecycle_module.cleanup_relay_invocation,
        line,
        SystemExit(78),
    ):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(raw)  # type: ignore[arg-type]
    assert cut.value.code == 78
    assert not _public_failure_reaches(
        cut.value, raw, owner, parts[1], *harness.authorities.values()
    )
    assert not _traceback_contains_text(cut.value, CALL_ID)
    lifecycle_module.cleanup_relay_invocation(owner)


def test_decorator_finalization_resolve_control_scrubs_recovered_owner_graph(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    harness.bare_returns.add("stop:browser")
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    with _control_on_nth_return(
        cleanup_module._resolve_cleanup_owner,
        SystemExit(80),
        occurrence=2,
    ):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(authority)
    assert cut.value.code == 80
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not _public_failure_reaches(cut.value, owner, parts[1], *harness.authorities.values())
    harness.bare_returns.clear()
    lifecycle_module.cleanup_relay_invocation(authority)


def test_decorator_exception_scrub_return_control_drops_raw_argument_graph(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    raw = {"owner": owner, "driver": parts[1], "authorities": harness.authorities}
    with _control_on_return(cleanup_module._scrub_exception, SystemExit(79)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(raw)  # type: ignore[arg-type]
    assert cut.value.code == 79
    assert not _public_failure_reaches(
        cut.value, raw, owner, parts[1], *harness.authorities.values()
    )
    lifecycle_module.cleanup_relay_invocation(owner)


def test_repeated_exception_scrub_controls_preserve_first_and_drop_raw_graph(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    raw = {"owner": owner, "driver": parts[1], "authorities": harness.authorities}
    original_scrub = cleanup_module._scrub_exception
    codes = [85, 86]

    def interrupted(error: BaseException) -> None:
        original_scrub(error)
        if codes:
            raise SystemExit(codes.pop(0))

    monkeypatch.setattr(cleanup_module, "_scrub_exception", interrupted)
    with pytest.raises(SystemExit) as cut:
        lifecycle_module.cleanup_relay_invocation(raw)  # type: ignore[arg-type]
    assert cut.value.code == 85
    assert not _public_failure_reaches(
        cut.value, raw, owner, parts[1], *harness.authorities.values()
    )
    monkeypatch.setattr(cleanup_module, "_scrub_exception", original_scrub)
    lifecycle_module.cleanup_relay_invocation(owner)


@pytest.mark.parametrize(("interrupted_read", "prebootstrap_calls"), [(1, 0), (2, 1)])
def test_control_from_start_and_reconciliation_reads_is_not_bool_collapsed(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_read: int,
    prebootstrap_calls: int,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    original_start_read = driver_module._RelayChildStartDestination._read
    reads = 0

    def interrupted_start_read(destination: object, owner_token: object, role: str) -> object:
        nonlocal reads
        receipt = original_start_read(destination, owner_token, role)  # type: ignore[arg-type]
        if role == "app":
            reads += 1
        if role == "app" and reads == interrupted_read:
            raise SystemExit(35)
        return receipt

    monkeypatch.setattr(
        driver_module._RelayChildStartDestination,
        "_read",
        interrupted_start_read,
    )
    with pytest.raises(SystemExit) as cut:
        lifecycle_module.stage_relay_backend(owner)
    assert cut.value.code == 35 and reads >= interrupted_read
    assert harness.count("start:app") == 1
    assert harness.count("prebootstrap") == prebootstrap_calls
    monkeypatch.setattr(
        driver_module._RelayChildStartDestination,
        "_read",
        original_start_read,
    )
    lifecycle_module.stage_relay_backend(owner)
    assert harness.count("start:app") == harness.count("prebootstrap") == 1
    lifecycle_module.cleanup_relay_invocation(owner)


def test_child_authority_read_controls_preserve_first_signal_and_cleanup_exact_children(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    original = driver_module._RelayChildAuthorityDestination._read
    codes = [56, 57]

    def interrupted(destination: object, role: str) -> object:
        if role == "app" and codes:
            raise SystemExit(codes.pop(0))
        return original(destination, role)  # type: ignore[arg-type]

    monkeypatch.setattr(driver_module._RelayChildAuthorityDestination, "_read", interrupted)
    with pytest.raises(SystemExit) as cut:
        lifecycle_module.stage_relay_backend(owner)
    assert cut.value.code == 56
    assert harness.count("start:app") == 0
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    assert not _public_failure_reaches(cut.value, harness, parts[1], owner)


def test_prebootstrap_adoption_and_exit_reconciliation_preserve_controls(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    original_prebootstrap_read = prebootstrap_module.RelayPrebootstrapDestination._read
    prebootstrap_fired = False

    def interrupted_prebootstrap_read(destination: object, owner_token: object) -> object:
        nonlocal prebootstrap_fired
        receipt = original_prebootstrap_read(destination, owner_token)  # type: ignore[arg-type]
        if not prebootstrap_fired:
            prebootstrap_fired = True
            raise SystemExit(36)
        return receipt

    monkeypatch.setattr(
        prebootstrap_module.RelayPrebootstrapDestination,
        "_read",
        interrupted_prebootstrap_read,
    )
    with pytest.raises(SystemExit) as prebootstrap_cut:
        lifecycle_module.stage_relay_backend(owner)
    assert prebootstrap_cut.value.code == 36
    lifecycle_module.stage_relay_backend(owner)
    monkeypatch.setattr(
        prebootstrap_module.RelayPrebootstrapDestination,
        "_read",
        original_prebootstrap_read,
    )

    original_reconcile = prebootstrap_module.RelayPrebootstrapDestination._reconcile_adoption
    adoption_fired = False

    def interrupted_adoption(destination: object, owner_token: object) -> bool:
        nonlocal adoption_fired
        committed = original_reconcile(destination, owner_token)  # type: ignore[arg-type]
        if not adoption_fired:
            adoption_fired = True
            raise SystemExit(37)
        return committed

    monkeypatch.setattr(
        prebootstrap_module.RelayPrebootstrapDestination,
        "_reconcile_adoption",
        interrupted_adoption,
    )
    sink = _UsernameSink()
    with pytest.raises(SystemExit) as adoption_cut:
        lifecycle_module._adopt_expected_turn_username(owner, sink)
    assert adoption_cut.value.code == 37 and sink.calls == 1
    lifecycle_module._adopt_expected_turn_username(owner, sink)
    lifecycle_module.stage_relay_web(owner)
    lifecycle_module.start_relay_playwright(owner)

    original_exit_read = values_module.RelayPlaywrightExitDestination._read
    exit_fired = False

    def interrupted_exit_read(destination: object, owner_token: object) -> object:
        nonlocal exit_fired
        receipt = original_exit_read(destination, owner_token)  # type: ignore[arg-type]
        if not exit_fired:
            exit_fired = True
            raise SystemExit(38)
        return receipt

    monkeypatch.setattr(
        values_module.RelayPlaywrightExitDestination,
        "_read",
        interrupted_exit_read,
    )
    with pytest.raises(SystemExit) as exit_cut:
        lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)
    assert exit_cut.value.code == 38
    lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)
    assert harness.count("finish:browser") == 1
    lifecycle_module.cleanup_relay_invocation(owner)
    sink.wipe()


def test_every_public_stage_reconciles_helper_return_control_without_duplicate_work(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    with _control_on_return(lifecycle_module._forward_backend, SystemExit(48)):
        with pytest.raises(SystemExit) as backend_cut:
            lifecycle_module.stage_relay_backend(owner)
    assert backend_cut.value.code == 48
    assert not hasattr(backend_cut.value, "cleanup_authority")
    lifecycle_module.stage_relay_backend(owner)

    sink = _UsernameSink()
    lifecycle_module._adopt_expected_turn_username(owner, sink)
    with _control_on_return(lifecycle_module._start_stage, SystemExit(49)):
        with pytest.raises(SystemExit) as web_cut:
            lifecycle_module.stage_relay_web(owner)
    assert web_cut.value.code == 49
    lifecycle_module.stage_relay_web(owner)
    with _control_on_return(lifecycle_module._forward_browser, SystemExit(50)):
        with pytest.raises(SystemExit) as browser_cut:
            lifecycle_module.start_relay_playwright(owner)
    assert browser_cut.value.code == 50
    lifecycle_module.start_relay_playwright(owner)
    with _control_on_return(lifecycle_module._finish_browser, SystemExit(51)):
        with pytest.raises(SystemExit) as finish_cut:
            lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)
    assert finish_cut.value.code == 51
    lifecycle_module.finish_relay_playwright(owner, timeout_seconds=5.0)

    for error in (backend_cut.value, web_cut.value, browser_cut.value, finish_cut.value):
        assert not _public_failure_reaches(error, harness, owner)
        assert not _traceback_contains_text(error, CALL_ID)
    for event in ("start:app", "prebootstrap", "start:web", "start:browser", "finish:browser"):
        assert harness.count(event) == 1
    lifecycle_module.cleanup_relay_invocation(owner)
    sink.wipe()


def test_stage_helper_return_control_keeps_key_when_owner_is_cleanup_required(
    synthetic_runtime: None,
) -> None:
    harness = _Harness()
    harness.bare_returns.update({"start:app", "stop:browser"})
    parts = _components(harness)
    owner = _new_owner(parts)
    with pytest.raises(facade.RelayInvocationCleanupRequired) as initial:
        lifecycle_module.stage_relay_backend(owner)
    authority = initial.value.cleanup_authority
    harness.bare_returns.remove("start:app")

    with _control_on_return(lifecycle_module._forward_backend, SystemExit(58)):
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.stage_relay_backend(owner)
    assert cut.value.code == 58
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not _public_failure_reaches(cut.value, harness, parts[1], owner)
    assert harness.count("stop:web") == harness.count("stop:app") == 0
    harness.bare_returns.clear()
    lifecycle_module.cleanup_relay_invocation(authority)


@pytest.mark.parametrize("boundary", ["resolve", "cleanup", "release"])
def test_public_cleanup_control_boundaries_emit_only_recoverable_opaque_key(
    synthetic_runtime: None,
    boundary: str,
) -> None:
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    authority = owner._cleanup_authority
    if boundary == "resolve":
        context = _control_on_return(lifecycle_module._resolve_cleanup_owner, SystemExit(52))
    elif boundary == "cleanup":
        context = _control_on_return(lifecycle_module._cleanup_invocation_owner, SystemExit(53))
    else:
        context = _control_on_nth_return(
            cleanup_module._release_cleanup_owner,
            SystemExit(54),
            occurrence=1,
        )
    with context:
        with pytest.raises(SystemExit) as cut:
            lifecycle_module.cleanup_relay_invocation(owner)
    assert cut.value.code == {"resolve": 52, "cleanup": 53, "release": 54}[boundary]
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not _public_failure_reaches(
        cut.value,
        harness,
        parts[1],
        owner,
        *harness.authorities.values(),
    )
    lifecycle_module.cleanup_relay_invocation(authority)
    if boundary == "resolve":
        assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    for role in ("browser", "web", "app"):
        assert harness.count(f"stop:{role}") == 1


@pytest.mark.parametrize(
    ("position", "expected_calls"),
    [("before", 2), ("after", 1)],
)
def test_username_sink_reconciles_control_before_or_after_private_publication(
    synthetic_runtime: None,
    position: str,
    expected_calls: int,
) -> None:
    harness = _Harness()
    owner = _new_owner(_components(harness))
    lifecycle_module.stage_relay_backend(owner)
    secrets = support_module._load_secrets(owner._secret_key)
    username = bytes(secrets.prebootstrap_destination._expected_username).decode()
    sink = _UsernameSink(
        cut_before_publication=SystemExit(55) if position == "before" else None,
        cut_after_publication=SystemExit(55) if position == "after" else None,
    )
    with pytest.raises(SystemExit) as cut:
        lifecycle_module._adopt_expected_turn_username(owner, sink)
    assert cut.value.code == 55
    assert sink.calls == expected_calls
    assert secrets.prebootstrap_destination._expected_username is None
    assert not _traceback_contains_text(cut.value, username)
    assert not _public_failure_reaches(cut.value, harness, owner, sink)
    lifecycle_module._adopt_expected_turn_username(owner, sink)
    assert sink.calls == expected_calls
    lifecycle_module.cleanup_relay_invocation(owner)
    sink.wipe()


def test_unpublished_start_return_is_fixed_failure_and_sanitized(
    synthetic_runtime: None,
) -> None:
    secret = "callback-secret-must-not-escape"
    harness = _Harness()
    harness.cut("start:app", "before", RuntimeError(secret))
    parts = _components(harness)
    owner = _new_owner(parts)
    with pytest.raises(facade.RelayInvocationError) as captured:
        lifecycle_module.stage_relay_backend(owner)

    error = captured.value
    assert type(error) is facade.RelayInvocationError
    assert error.args == (FIXED_FAILURE,)
    assert error.__cause__ is error.__context__ is None
    assert not hasattr(error, "cleanup_authority")
    assert not _public_failure_reaches(
        error,
        harness,
        parts[1],
        owner,
        *harness.authorities.values(),
    )
    assert not _traceback_contains_text(error, secret)
    assert not _traceback_contains_text(error, CALL_ID)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    lifecycle_module.cleanup_relay_invocation(owner)


def test_cleanup_retry_failure_exposes_only_opaque_noncopyable_key(
    synthetic_runtime: None,
) -> None:
    secret = "stop-secret-must-not-escape"
    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    sink = _UsernameSink()
    _receipt, _exit = _drive_to_finished(owner, sink)
    username = bytes(sink._username).decode()
    harness.cut("stop:browser", "before", RuntimeError(secret))

    with pytest.raises(facade.RelayInvocationCleanupRequired) as captured:
        lifecycle_module.cleanup_relay_invocation(owner)
    error = captured.value
    authority = error.cleanup_authority
    assert error.args == ("Relay invocation cleanup failed",)
    assert repr(authority) == "RelayInvocationCleanupAuthority()"
    assert not _public_failure_reaches(
        error,
        harness,
        parts[1],
        owner,
        sink,
        *harness.authorities.values(),
    )
    assert not _traceback_contains_text(error, secret)
    assert not _traceback_contains_text(error, CALL_ID)
    assert not _traceback_contains_text(error, username)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(AttributeError):
        authority._key = object()

    assert harness.count("stop:browser") == 1
    assert harness.count("stop:web") == harness.count("stop:app") == 0
    lifecycle_module.cleanup_relay_invocation(authority)
    lifecycle_module.cleanup_relay_invocation(authority)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    assert harness.count("stop:browser") == 2
    assert harness.count("stop:web") == harness.count("stop:app") == 1
    sink.wipe()


def test_finish_requires_exact_float_deadline_and_exact_zero_exit(
    synthetic_runtime: None,
) -> None:
    first_harness = _Harness()
    first_owner = _new_owner(_components(first_harness))
    first_sink = _UsernameSink()
    lifecycle_module.stage_relay_backend(first_owner)
    lifecycle_module._adopt_expected_turn_username(first_owner, first_sink)
    lifecycle_module.stage_relay_web(first_owner)
    lifecycle_module.start_relay_playwright(first_owner)
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
        lifecycle_module.finish_relay_playwright(first_owner, timeout_seconds=5)  # type: ignore[arg-type]
    assert first_harness.count("finish:browser") == 0
    assert first_harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    first_sink.wipe()

    second_harness = _Harness()
    second_harness.exit_returncode = 9
    second_owner = _new_owner(_components(second_harness))
    second_sink = _UsernameSink()
    lifecycle_module.stage_relay_backend(second_owner)
    lifecycle_module._adopt_expected_turn_username(second_owner, second_sink)
    lifecycle_module.stage_relay_web(second_owner)
    lifecycle_module.start_relay_playwright(second_owner)
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$") as captured:
        lifecycle_module.finish_relay_playwright(second_owner, timeout_seconds=5.0)
    assert second_harness.count("finish:browser") == 1
    assert second_harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    assert not _public_failure_reaches(captured.value, second_harness, second_owner)
    second_sink.wipe()


@pytest.mark.parametrize("boundary", ["preown", "start", "stop"])
def test_bare_callback_return_never_substitutes_for_destination_receipt(
    synthetic_runtime: None,
    boundary: str,
) -> None:
    harness = _Harness()
    harness.bare_returns.add(
        {"preown": "preown:app", "start": "start:app", "stop": "stop:browser"}[boundary]
    )
    parts = _components(harness)
    if boundary == "preown":
        with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
            _new_owner(parts)
        return
    owner = _new_owner(parts)
    if boundary == "start":
        with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
            lifecycle_module.stage_relay_backend(owner)
        return
    with pytest.raises(facade.RelayInvocationCleanupRequired) as captured:
        lifecycle_module.cleanup_relay_invocation(owner)
    assert harness.count("stop:web") == harness.count("stop:app") == 0
    harness.bare_returns.clear()
    lifecycle_module.cleanup_relay_invocation(captured.value.cleanup_authority)


@pytest.mark.parametrize("boundary", ["child", "prebootstrap"])
def test_callback_request_tampering_fails_closed_and_only_cleans_owned_children(
    synthetic_runtime: None,
    boundary: str,
) -> None:
    harness = _Harness()

    def tampered_start(authority: object, request: object, destination: object) -> None:
        harness._record("start:app")
        request._command = ("/attacker",)  # type: ignore[attr-defined]

    def tampered_prebootstrap(authority: object, request: object, destination: object) -> None:
        harness._record("prebootstrap")
        request._authorization = "attacker"  # type: ignore[attr-defined]

    driver = support_module.new_relay_invocation_driver(
        preown=harness.preown,
        start=tampered_start if boundary == "child" else harness.start,
        prebootstrap=(
            tampered_prebootstrap if boundary == "prebootstrap" else harness.prebootstrap
        ),
        finish=harness.finish,
        stop=harness.stop,
    )
    run, _unused_driver, tools, destination = _components(_Harness())
    parts = (run, driver, tools, destination)
    owner = _new_owner(parts)
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$") as captured:
        lifecycle_module.stage_relay_backend(owner)
    assert harness.snapshot()[-3:] == ["stop:browser", "stop:web", "stop:app"]
    assert not _public_failure_reaches(captured.value, harness, driver, owner)
    assert not _traceback_contains_text(captured.value, CALL_ID)


def test_active_invocation_cap_rejects_then_reuses_released_capacity(
    synthetic_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup_module, "_MAX_ACTIVE_INVOCATIONS", 2)
    monkeypatch.setattr(support_module, "_MAX_ACTIVE_INVOCATIONS", 2)
    first_harness, second_harness, third_harness = _Harness(), _Harness(), _Harness()
    first_parts = _components(first_harness)
    second_parts = _components(second_harness)
    third_parts = _components(third_harness)
    first = _new_owner(first_parts)
    second = _new_owner(second_parts)
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
        _new_owner(third_parts)
    assert len(cleanup_module._REGISTRY) == len(support_module._SECRET_RECORDS) == 2
    assert third_harness.snapshot() == []

    lifecycle_module.cleanup_relay_invocation(first)
    third = _new_owner(third_parts)
    assert len(cleanup_module._REGISTRY) == len(support_module._SECRET_RECORDS) == 2
    lifecycle_module.cleanup_relay_invocation(second)
    lifecycle_module.cleanup_relay_invocation(third)


class _Text(str):
    pass


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": True, "status": "prepared", "expires_at_epoch_seconds": EXPIRY},
        {"schema_version": 1, "status": _Text("prepared"), "expires_at_epoch_seconds": EXPIRY},
        {"schema_version": 1, "status": "prepared", "expires_at_epoch_seconds": True},
        {_Text("schema_version"): 1, "status": "prepared", "expires_at_epoch_seconds": EXPIRY},
    ],
)
def test_prebootstrap_publication_rejects_equality_spoofed_schema(
    payload: dict[str, object],
) -> None:
    destination = prebootstrap_module.RelayPrebootstrapDestination(
        prebootstrap_module._DESTINATION_TOKEN,
        owner_token=object(),
        call_id=CALL_ID,
        clock=lambda: NOW,
    )
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
        destination.publish(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "exited", "returncode": False},
        {"status": _Text("exited"), "returncode": 0},
        {_Text("status"): "exited", "returncode": 0},
    ],
)
def test_exit_publication_rejects_false_and_equality_spoofs(payload: dict[str, object]) -> None:
    destination = values_module.RelayPlaywrightExitDestination(
        values_module._EXIT_DESTINATION_TOKEN,
        owner_token=object(),
    )
    with pytest.raises(facade.RelayInvocationError, match=f"^{FIXED_FAILURE}$"):
        destination.publish(payload)


def test_facade_receipts_capabilities_and_cleanup_key_are_immutable_and_private(
    synthetic_runtime: None,
) -> None:
    expected = {
        "RelayInvocationCleanupAuthority",
        "RelayInvocationCleanupRequired",
        "RelayInvocationDriver",
        "RelayInvocationError",
        "RelayInvocationOwner",
        "RelayInvocationTools",
        "RelayPlaywrightExitReceipt",
        "RelayPrebootstrapReceipt",
        "cleanup_relay_invocation",
        "finish_relay_playwright",
        "new_relay_invocation_driver",
        "new_synthetic_relay_invocation_tools",
        "relay_prebootstrap_result",
        "stage_relay_backend",
        "stage_relay_web",
        "start_relay_playwright",
    }
    assert set(facade.__all__) == expected
    for private_name in (
        "RelayChildRequest",
        "RelayFinishRequest",
        "RelayPrebootstrapDestination",
        "RelayPrebootstrapRequest",
        "RelayPlaywrightExitDestination",
        "new_relay_invocation_owner",
    ):
        assert not hasattr(facade, private_name)
    assert driver_module.__all__ == ["RelayInvocationDriver", "RelayInvocationTools"]
    assert prebootstrap_module.__all__ == ["RelayPrebootstrapReceipt"]
    assert values_module.__all__ == ["RelayInvocationError", "RelayPlaywrightExitReceipt"]

    harness = _Harness()
    parts = _components(harness)
    owner = _new_owner(parts)
    receipt, exit_receipt = _drive_to_finished(owner)
    authority = owner._cleanup_authority
    destination = parts[3]
    for value in (parts[1], parts[2], owner, receipt, exit_receipt, authority):
        assert not value
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(value)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(destination)
    for value in (parts[1], parts[2], receipt, exit_receipt, authority):
        with pytest.raises(AttributeError):
            value._forged = True
    assert parts[1].concrete_adapter is False and parts[2].concrete_adapter is False
    lifecycle_module.cleanup_relay_invocation(owner)


def test_every_production_invocation_module_stays_below_line_cap() -> None:
    paths = Path(lifecycle_module.__file__).parent.glob("voice_pipecat_e2e_relay_invocation*.py")
    counts = {path.name: len(path.read_text().splitlines()) for path in paths}
    assert counts
    assert all(count < 700 for count in counts.values()), counts

"""Synthetic attached-process adapter tests; no process is started."""

from __future__ import annotations

import copy
import inspect
import pickle
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_runtime_process as process_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    establish_container_cleanup_authority,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_host import CommandRequest  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
    CoturnAttachedCleanupRequired,
    CoturnAttachedProcessCleanupRequired,
    CoturnRuntimeError,
    UnpublishedAttachedCleanupAuthority,
    cleanup_unpublished_attached,
    confirm_attached_coturn_clean_exit,
    new_attached_coturn_process,
    start_owned_container_attached,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime import _container_plan  # noqa: E402


@dataclass
class RawChunk:
    stream: object
    data: object


@dataclass
class FakeAttached:
    chunks: list[object] = field(default_factory=list)
    returncode: object = None
    drain_state: object = False
    reads: list[float] = field(default_factory=list)
    polls: int = 0
    terminations: int = 0
    terminate_result: object = None

    def read_chunk(self, *, timeout_seconds: float) -> object | None:
        self.reads.append(timeout_seconds)
        if not self.chunks:
            return None
        value = self.chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    @property
    def drained(self) -> object:
        return self.drain_state and not self.chunks

    def poll(self) -> object:
        self.polls += 1
        return self.returncode

    def terminate(self) -> object:
        self.terminations += 1
        if isinstance(self.terminate_result, BaseException):
            raise self.terminate_result
        return self.terminate_result


@dataclass
class StartRunner:
    attached: object
    requests: list[CommandRequest] = field(default_factory=list)
    settle_result: object = True
    settlements: int = 0

    def run(self, request: CommandRequest) -> object:
        raise AssertionError("attached-process tests do not run commands")

    def start_attached(self, request: CommandRequest) -> object:
        self.requests.append(request)
        return self.attached

    def settle_owned(self) -> object:
        self.settlements += 1
        if isinstance(self.settle_result, BaseException):
            raise self.settle_result
        return self.settle_result


@dataclass
class OwnedSlotRunner:
    attached: FakeAttached
    settle_result: object = True
    slot: FakeAttached | None = None
    settlements: int = 0
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> object:
        raise AssertionError("attached-process tests do not run commands")

    def start_attached(self, request: CommandRequest) -> object:
        self.requests.append(request)
        self.slot = self.attached
        return self.attached

    def settle_owned(self) -> object:
        self.settlements += 1
        if isinstance(self.settle_result, BaseException):
            raise self.settle_result
        if self.settle_result is True:
            if self.slot is not None and self.slot.terminations == 0:
                self.slot.terminate()
            self.slot = None
        return self.settle_result


def _process(tmp_path: Path, attached: FakeAttached) -> AttachedCoturnProcess:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = _paths(tmp_path)
    plan = _container_plan(paths)
    inspection = container_inspection(plan)
    authority = establish_container_cleanup_authority(
        plan=plan,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    validated = validate_container_for_start(authority, inspection)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=StartRunner(attached),
        tools=_tools(),
        container=validated,
        process=process,
    )
    return process


def _validated(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan = _container_plan(_paths(tmp_path))
    inspection = container_inspection(plan)
    authority = establish_container_cleanup_authority(
        plan=plan,
        container_id=CONTAINER_ID,
        inspection=inspection,
    )
    return validate_container_for_start(authority, inspection)


def _interrupt_on_return(*, target_code, operation) -> None:
    fired = False

    def trace(frame, event: str, _arg):
        nonlocal fired
        if frame.f_code is target_code and event == "return" and not fired:
            fired = True
            raise KeyboardInterrupt("untrusted-return-publication-cut")
        return trace

    sys.settrace(trace)
    try:
        operation()
    finally:
        sys.settrace(None)
    assert fired


def _source_line(function: object, marker: str, *, occurrence: int = 0) -> int:
    lines, first = inspect.getsourcelines(function)
    matches = [first + offset for offset, line in enumerate(lines) if marker in line]
    assert matches
    return matches[occurrence]


def _interrupt_before_line(*, target_code, line_number: int, operation) -> None:
    fired = False

    def trace(frame, event: str, _arg):
        nonlocal fired
        if (
            frame.f_code is target_code
            and event == "line"
            and frame.f_lineno == line_number
            and not fired
        ):
            fired = True
            raise KeyboardInterrupt("untrusted-line-publication-cut")
        return trace

    sys.settrace(trace)
    try:
        operation()
    finally:
        sys.settrace(None)
    assert fired


def test_structural_stdout_chunks_are_split_without_transcript_aggregation(
    tmp_path: Path,
) -> None:
    first = b"first-fragment-"
    second = b"second-fragment\n"
    attached = FakeAttached(
        chunks=[RawChunk("stdout", first), RawChunk("stdout", second)],
        returncode=0,
        drain_state=True,
    )
    process = _process(tmp_path, attached)

    assert process.read_chunk(timeout_seconds=0.1) is first
    assert process.read_chunk(timeout_seconds=0.1) is second
    assert process.read_chunk(timeout_seconds=0.1) is None
    assert process.drained is True
    receipt = confirm_attached_coturn_clean_exit(process)
    assert repr(receipt) == "CleanCoturnExitReceipt()"
    assert first.decode() not in repr(process)
    assert second.decode().strip() not in repr(receipt)
    assert attached.polls == 1
    assert not hasattr(process, "collect")


def test_clean_exit_settles_runner_and_releases_handle_graph_before_receipt(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    runner = StartRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=runner,
        tools=_tools(),
        container=validated,
        process=process,
    )

    receipt = confirm_attached_coturn_clean_exit(process)
    assert confirm_attached_coturn_clean_exit(process) is receipt
    assert runner.settlements == 1
    assert process._handle is None
    assert process._runner is None


def test_clean_exit_runner_settlement_return_cut_is_retryable(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    runner = StartRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=runner,
        tools=_tools(),
        container=validated,
        process=process,
    )

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=runner.settle_owned.__func__.__code__,
            operation=lambda: confirm_attached_coturn_clean_exit(process),
        )
    assert str(error.value) == ""
    assert repr(confirm_attached_coturn_clean_exit(process)) == "CleanCoturnExitReceipt()"
    assert runner.settlements == 2


def test_clean_exit_receipt_publication_cut_reuses_settlement_proof(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    runner = StartRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=runner,
        tools=_tools(),
        container=validated,
        process=process,
    )
    receipt_line = _source_line(
        AttachedCoturnProcess._confirm_clean_exit,
        "self._clean_receipt = CleanCoturnExitReceipt(",
    )

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=AttachedCoturnProcess._confirm_clean_exit.__code__,
            line_number=receipt_line,
            operation=lambda: confirm_attached_coturn_clean_exit(process),
        )

    assert str(error.value) == ""
    assert repr(confirm_attached_coturn_clean_exit(process)) == "CleanCoturnExitReceipt()"
    assert runner.settlements == 1
    assert attached.polls == 1


@pytest.mark.parametrize("timeout", [True, 0, 1, 0.009, 60.1, float("inf"), float("nan")])
def test_read_timeout_is_bounded_before_touching_handle(tmp_path: Path, timeout: object) -> None:
    raw = b"traceback-sentinel-invalid-timeout-queued"
    attached = FakeAttached(chunks=[RawChunk("stdout", raw)])
    process = _process(tmp_path, attached)
    with pytest.raises(CoturnRuntimeError, match="read timeout is invalid") as error:
        process.read_chunk(timeout_seconds=timeout)  # type: ignore[arg-type]
    assert attached.reads == []
    assert not traceback_contains(error.value, raw)


@pytest.mark.parametrize(
    "chunk",
    [
        RawChunk("other", b"x"),
        RawChunk("stdout", b""),
        RawChunk("stdout", b"x" * 4_097),
        RawChunk("stdout", bytearray(b"x")),
        object(),
    ],
)
def test_malformed_raw_chunks_are_rejected_at_runtime_boundary(
    tmp_path: Path,
    chunk: object,
) -> None:
    process = _process(tmp_path, FakeAttached(chunks=[chunk]))
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached chunk is invalid$"):
        process.read_chunk(timeout_seconds=0.1)


def test_total_attached_output_cap_fails_closed_without_aggregation(tmp_path: Path) -> None:
    chunk = b"x" * 4_096
    attached = FakeAttached(chunks=[RawChunk("stdout", chunk) for _ in range(257)])
    process = _process(tmp_path, attached)
    for _ in range(256):
        assert process.read_chunk(timeout_seconds=0.1) is chunk
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached output is oversized$"):
        process.read_chunk(timeout_seconds=0.1)
    assert process.drained is False


def test_stderr_and_raw_handle_failures_are_fixed_and_scrubbed(tmp_path: Path) -> None:
    stderr = b"traceback-sentinel-attached-stderr"
    process = _process(tmp_path / "stderr", FakeAttached(chunks=[RawChunk("stderr", stderr)]))
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached stderr is forbidden$") as error:
        process.read_chunk(timeout_seconds=0.1)
    assert not traceback_contains(error.value, stderr)

    raw = "traceback-sentinel-attached-reader"
    process = _process(tmp_path / "reader", FakeAttached(chunks=[RuntimeError(raw)]))
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached chunk is invalid$") as error:
        process.read_chunk(timeout_seconds=0.1)
    assert raw not in str(error.value)
    assert not traceback_contains(error.value, raw)


def test_clean_exit_requires_drain_before_poll_and_exact_zero(tmp_path: Path) -> None:
    not_drained = FakeAttached(returncode=0, drain_state=False)
    process = _process(tmp_path / "pending", not_drained)
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)
    assert not_drained.polls == 0

    nonzero = FakeAttached(returncode=17, drain_state=True)
    process = _process(tmp_path / "nonzero", nonzero)
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)
    assert nonzero.polls == 1

    boolean = FakeAttached(returncode=True, drain_state=True)
    process = _process(tmp_path / "boolean", boolean)
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)


def test_stderr_can_never_become_a_clean_exit_proof(tmp_path: Path) -> None:
    attached = FakeAttached(
        chunks=[RawChunk("stderr", b"fixed-forbidden")],
        returncode=0,
        drain_state=True,
    )
    process = _process(tmp_path, attached)
    with pytest.raises(CoturnRuntimeError, match="stderr is forbidden"):
        process.read_chunk(timeout_seconds=0.1)
    assert process.drained is True
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)
    assert attached.polls == 0


def test_terminate_is_idempotent_and_preserves_separate_crash_recovery(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    process = _process(tmp_path, attached)
    process.terminate()
    process.terminate()
    assert attached.terminations == 1
    with pytest.raises(CoturnRuntimeError, match="process is unavailable"):
        _ = process.drained
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)

    raw = "traceback-sentinel-terminate"
    failing = FakeAttached(terminate_result=RuntimeError(raw))
    process = _process(tmp_path / "failure", failing)
    with pytest.raises(CoturnAttachedProcessCleanupRequired) as error:
        process.terminate()
    assert raw not in str(error.value)
    assert not traceback_contains(error.value, raw)


def test_terminate_return_cut_permanently_poisoned_clean_exit(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    process = _process(tmp_path, attached)
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=attached.terminate.__func__.__code__,
            operation=process.terminate,
        )
    assert str(error.value) == ""
    assert attached.terminations == 1
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)


def test_terminate_state_publication_cut_releases_active_run_on_retry(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached(returncode=0, drain_state=True))
    release_line = _source_line(
        AttachedCoturnProcess.terminate,
        "_release_active_run(self._authority, self._identity)",
        occurrence=-1,
    )

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=AttachedCoturnProcess.terminate.__code__,
            line_number=release_line,
            operation=process.terminate,
        )

    assert str(error.value) == ""
    assert error.value.cleanup_authority is process  # type: ignore[attr-defined]
    assert not process_module._container_recovery_is_allowed(process._authority)
    process.terminate()
    assert process_module._container_recovery_is_allowed(process._authority)


def test_clean_exit_state_publication_cut_releases_active_run_on_retry(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(returncode=0, drain_state=True)
    process = _process(tmp_path, attached)
    release_line = _source_line(
        AttachedCoturnProcess._confirm_clean_exit,
        "_release_active_run(self._authority, self._identity)",
        occurrence=-1,
    )

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=AttachedCoturnProcess._confirm_clean_exit.__code__,
            line_number=release_line,
            operation=lambda: confirm_attached_coturn_clean_exit(process),
        )

    assert str(error.value) == ""
    assert not process_module._container_recovery_is_allowed(process._authority)
    receipt = confirm_attached_coturn_clean_exit(process)
    assert repr(receipt) == "CleanCoturnExitReceipt()"
    assert process_module._container_recovery_is_allowed(process._authority)
    assert attached.polls == 1


@pytest.mark.parametrize("local_name", ["handle", "process"])
def test_start_return_publication_cuts_settle_every_runner_owned_slot(
    tmp_path: Path,
    local_name: str,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached)
    validated = _validated(tmp_path / local_name)
    process = new_attached_coturn_process(validated)
    target_code = (
        runner.start_attached.__func__.__code__
        if local_name == "handle"
        else AttachedCoturnProcess._publish.__code__
    )

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=target_code,
            operation=lambda: start_owned_container_attached(
                runner=runner,
                tools=_tools(),
                container=validated,
                process=process,
            ),
        )

    assert str(error.value) == ""
    assert runner.slot is None
    assert runner.settlements == 1
    assert attached.terminations == 1
    assert not traceback_contains(error.value, "untrusted-return-publication-cut")


@pytest.mark.parametrize(
    ("cut", "expected"),
    [
        (MemoryError("untrusted-post-register-cut"), CoturnRuntimeError),
        (KeyboardInterrupt("untrusted-post-register-cut"), KeyboardInterrupt),
        (SystemExit(23), SystemExit),
    ],
)
def test_post_register_publication_cuts_release_slot_and_allow_capacity_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: BaseException,
    expected: type[BaseException],
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    real_register = process_module._register_active_run
    active_before = dict(process_module._ACTIVE_RUNS)
    interrupted = False

    def register_then_cut(authority: object, identity: object) -> bool:
        nonlocal interrupted
        registered = real_register(authority, identity)  # type: ignore[arg-type]
        if registered and not interrupted:
            interrupted = True
            raise cut
        return registered

    monkeypatch.setattr(process_module, "_register_active_run", register_then_cut)
    with pytest.raises(expected) as caught:
        start_owned_container_attached(
            runner=runner,
            tools=_tools(),
            container=validated,
            process=process,
        )

    assert interrupted
    if type(cut) is SystemExit:
        assert caught.value.code == 23  # type: ignore[attr-defined]
    elif type(cut) is MemoryError:
        assert str(caught.value) == "Coturn attached start failed"
    else:
        assert str(caught.value) == ""
    assert runner.slot is None
    assert runner.settlements == 1
    assert attached.terminations == 1
    assert process._handle is None
    assert process._runner is None
    assert process_module._container_recovery_is_allowed(process._authority)
    assert process_module._ACTIVE_RUNS == active_before
    assert not traceback_contains(caught.value, "untrusted-post-register-cut")

    monkeypatch.setattr(process_module, "_register_active_run", real_register)
    next_attached = FakeAttached()
    next_runner = OwnedSlotRunner(next_attached)
    next_process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=next_runner,
        tools=_tools(),
        container=validated,
        process=next_process,
    )
    next_process.terminate()
    assert next_runner.slot is None
    assert process_module._ACTIVE_RUNS == active_before


def test_attached_publication_control_reconciliation_is_finitely_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    registrations = 0

    def always_interrupt(_authority: object, _identity: object) -> bool:
        nonlocal registrations
        registrations += 1
        raise KeyboardInterrupt("untrusted-repeated-publication-control")

    monkeypatch.setattr(process_module, "_register_active_run", always_interrupt)
    with pytest.raises(KeyboardInterrupt) as captured:
        start_owned_container_attached(
            runner=runner,
            tools=_tools(),
            container=validated,
            process=process,
        )

    assert str(captured.value) == ""
    assert registrations == 8
    assert attached.terminations == 1
    assert runner.settlements == 1
    assert runner.slot is None
    assert not traceback_contains(captured.value, "untrusted-repeated-publication-control")


def test_concurrent_process_termination_serializes_handle_and_runner_cleanup(
    tmp_path: Path,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=runner,
        tools=_tools(),
        container=validated,
        process=process,
    )
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    errors: list[BaseException] = []

    def settle_once() -> bool:
        runner.settlements += 1
        entered.set()
        assert release.wait(1.0)
        runner.slot = None
        return True

    def terminate(*, second: bool = False) -> None:
        if second:
            second_started.set()
        try:
            process.terminate()
        except BaseException as error:
            errors.append(error)

    runner.settle_owned = settle_once  # type: ignore[method-assign]
    first = threading.Thread(target=terminate)
    second = threading.Thread(target=lambda: terminate(second=True))
    first.start()
    assert entered.wait(1.0)
    second.start()
    assert second_started.wait(1.0)
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert attached.terminations == 1
    assert runner.settlements == 1
    assert runner.slot is None


def test_concurrent_unpublished_cleanup_serializes_handle_and_runner() -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached, slot=attached)
    authority = process_module._new_unpublished_attached_cleanup_authority(runner)
    assert authority._adopt(attached)
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    errors: list[BaseException] = []

    def settle_once() -> bool:
        runner.settlements += 1
        entered.set()
        assert release.wait(1.0)
        runner.slot = None
        return True

    def cleanup(*, second: bool = False) -> None:
        if second:
            second_started.set()
        try:
            cleanup_unpublished_attached(authority)
        except BaseException as error:
            errors.append(error)

    runner.settle_owned = settle_once  # type: ignore[method-assign]
    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=lambda: cleanup(second=True))
    first.start()
    assert entered.wait(1.0)
    second.start()
    assert second_started.wait(1.0)
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert attached.terminations == 1
    assert runner.settlements == 1
    assert runner.slot is None


def test_outer_start_return_cut_keeps_process_in_caller_owned_aggregate(
    tmp_path: Path,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=start_owned_container_attached.__code__,
            operation=lambda: start_owned_container_attached(
                runner=runner,
                tools=_tools(),
                container=validated,
                process=process,
            ),
        )

    assert str(error.value) == "untrusted-return-publication-cut"
    assert runner.slot is attached
    process.terminate()
    assert runner.slot is None
    assert attached.terminations == 1


def test_lost_start_return_retains_opaque_retry_authority_until_runner_settles(
    tmp_path: Path,
) -> None:
    attached = FakeAttached(chunks=[RawChunk("stdout", b"opaque-stream-secret")])
    runner = OwnedSlotRunner(attached, settle_result=False)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)

    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=runner.start_attached.__func__.__code__,
            operation=lambda: start_owned_container_attached(
                runner=runner,
                tools=_tools(),
                container=validated,
                process=process,
            ),
        )

    authority = error.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is UnpublishedAttachedCleanupAuthority
    assert repr(authority) == "UnpublishedAttachedCleanupAuthority()"
    assert runner.slot is attached
    assert attached.terminations == 0

    runner.settle_result = True
    cleanup_unpublished_attached(authority)
    assert runner.slot is None
    assert attached.terminations == 1


def test_unpublished_settlement_commit_cut_reconciles_terminating_state(
    tmp_path: Path,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached, settle_result=False)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    with pytest.raises(KeyboardInterrupt) as start_error:
        _interrupt_on_return(
            target_code=runner.start_attached.__func__.__code__,
            operation=lambda: start_owned_container_attached(
                runner=runner,
                tools=_tools(),
                container=validated,
                process=process,
            ),
        )
    authority = start_error.value.cleanup_authority  # type: ignore[attr-defined]

    def settle_then_cut() -> bool:
        runner.settlements += 1
        runner.slot = None
        raise KeyboardInterrupt("untrusted-settle-publication-cut")

    runner.settle_owned = settle_then_cut  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt) as cleanup_error:
        cleanup_unpublished_attached(authority)
    assert str(cleanup_error.value) == ""
    assert cleanup_error.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(cleanup_error.value, "untrusted-settle-publication-cut")

    runner.settle_owned = lambda: True  # type: ignore[method-assign]
    cleanup_unpublished_attached(authority)


def test_unpublished_settled_state_cut_clears_retained_references_on_retry(
    tmp_path: Path,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached, settle_result=False)
    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)
    with pytest.raises(KeyboardInterrupt) as start_error:
        _interrupt_on_return(
            target_code=runner.start_attached.__func__.__code__,
            operation=lambda: start_owned_container_attached(
                runner=runner,
                tools=_tools(),
                container=validated,
                process=process,
            ),
        )
    authority = start_error.value.cleanup_authority  # type: ignore[attr-defined]
    runner.settle_result = True
    clear_line = _source_line(
        type(authority)._settle,
        "self._handle = None",
        occurrence=-1,
    )

    with pytest.raises(KeyboardInterrupt) as cleanup_error:
        _interrupt_before_line(
            target_code=type(authority)._settle.__code__,
            line_number=clear_line,
            operation=lambda: cleanup_unpublished_attached(authority),
        )

    assert str(cleanup_error.value) == ""
    assert cleanup_error.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert attached.terminations == 1
    cleanup_unpublished_attached(authority)
    assert attached.terminations == 1


def test_wrapper_constructor_failure_settles_handle_and_runner_without_raw_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "traceback-sentinel-wrapper-construction"
    attached = FakeAttached(chunks=[RawChunk("stdout", raw.encode("ascii"))])
    runner = OwnedSlotRunner(attached)

    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)

    def fail_publish(*_arguments: object, **_keywords: object) -> object:
        raise MemoryError(raw)

    monkeypatch.setattr(AttachedCoturnProcess, "_publish", fail_publish)
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn attached start failed$") as error:
        start_owned_container_attached(
            runner=runner,
            tools=_tools(),
            container=validated,
            process=process,
        )
    assert runner.slot is None
    assert attached.terminations == 1
    assert runner.settlements == 1
    assert not traceback_contains(error.value, raw)


def test_wrapper_failure_with_unsettled_runner_returns_only_opaque_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached = FakeAttached()
    runner = OwnedSlotRunner(attached, settle_result=None)

    validated = _validated(tmp_path)
    process = new_attached_coturn_process(validated)

    def fail_publish(*_arguments: object, **_keywords: object) -> object:
        raise RuntimeError("untrusted-constructor-failure")

    monkeypatch.setattr(AttachedCoturnProcess, "_publish", fail_publish)
    with pytest.raises(CoturnAttachedCleanupRequired) as error:
        start_owned_container_attached(
            runner=runner,
            tools=_tools(),
            container=validated,
            process=process,
        )
    assert repr(error.value.cleanup_authority) == "UnpublishedAttachedCleanupAuthority()"
    assert "untrusted-constructor-failure" not in repr(error.value)
    assert runner.slot is attached

    runner.settle_result = True
    cleanup_unpublished_attached(error.value.cleanup_authority)
    assert runner.slot is None


def test_clean_exit_receipt_has_no_public_constructor(tmp_path: Path) -> None:
    process = _process(tmp_path, FakeAttached(returncode=0, drain_state=True))
    receipt = confirm_attached_coturn_clean_exit(process)
    with pytest.raises(TypeError, match="factory-owned"):
        CleanCoturnExitReceipt(  # type: ignore[call-arg]
            object(),
            authority=receipt._authority,
            process_identity=object(),
        )


def test_process_and_clean_exit_receipt_reject_copy_and_serialization(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path, FakeAttached(returncode=0, drain_state=True))
    for operation in (
        lambda: copy.copy(process),
        lambda: copy.deepcopy(process),
        lambda: pickle.dumps(process),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    receipt = confirm_attached_coturn_clean_exit(process)
    for operation in (
        lambda: copy.copy(receipt),
        lambda: copy.deepcopy(receipt),
        lambda: pickle.dumps(receipt),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()

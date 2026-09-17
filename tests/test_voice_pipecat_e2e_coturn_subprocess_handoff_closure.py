"""Focused synthetic regressions for control-closed ownership handoffs."""

# ruff: noqa: E402

from __future__ import annotations

import selectors
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_subprocess as facade_module
from scripts import voice_pipecat_e2e_coturn_subprocess_boundary as boundary_module
from scripts import voice_pipecat_e2e_coturn_subprocess_process as process_module
from scripts import voice_pipecat_e2e_coturn_subprocess_quarantine as quarantine_module
from scripts import voice_pipecat_e2e_coturn_subprocess_request as request_module
from scripts import voice_pipecat_e2e_coturn_subprocess_spawn as spawn_module
from scripts import voice_pipecat_e2e_coturn_subprocess_state as state_module
from scripts import voice_pipecat_e2e_coturn_subprocess_supervisor as supervisor_module
from scripts import voice_pipecat_e2e_coturn_subprocess_values as values_module
from scripts.voice_pipecat_e2e_coturn_subprocess import (
    CoturnSubprocessError,
    StreamingAttachedCommand,
    SubprocessChunk,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_request import validate_request
from scripts.voice_pipecat_e2e_coturn_subprocess_state import ControllerState, Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_supervisor import (
    SupervisorSeams,
    SupervisorSlot,
    cancel_supervisor_slot,
    prepare_supervisor,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_values import MAX_QUEUED_CHUNKS
from tests.test_voice_pipecat_e2e_coturn_subprocess import (
    ControlPlan,
    FakeProcess,
    FakeSelector,
    Harness,
    docker_request,
    harness_with_tracking,
)
from tests.test_voice_pipecat_e2e_coturn_subprocess_adversarial import (
    _assert_fresh_scrubbed,
    _wait_until,
)


@pytest.fixture(autouse=True)
def _fast_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_module, "TERMINATION_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(process_module, "KILL_VERIFICATION_SECONDS", 0.02)
    monkeypatch.setattr(process_module, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(process_module, "_QUARANTINE_RETRY_SECONDS", 0.002)
    monkeypatch.setattr(quarantine_module, "TERMINATION_GRACE_SECONDS", 0.01)


class FiniteControls:
    def __init__(
        self,
        kind: type[KeyboardInterrupt] | type[SystemExit],
        count: int,
        *,
        code: int | None = -7,
    ) -> None:
        self.kind = kind
        self.remaining = count
        self.code = code
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.remaining <= 0:
            return
        self.remaining -= 1
        if self.kind is SystemExit:
            raise SystemExit(self.code)
        raise KeyboardInterrupt


def _assert_expected_control(
    captured: pytest.ExceptionInfo[BaseException],
    kind: type[KeyboardInterrupt] | type[SystemExit],
    *,
    code: int | None = -7,
) -> None:
    assert type(captured.value) is kind
    if kind is SystemExit:
        assert captured.value.code == code  # type: ignore[union-attr]


def _active_handle() -> tuple[
    FakeProcess, Harness, object, StreamingAttachedCommand, SubprocessChunk
]:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    runner = harness.runner()
    handle = runner.start_attached(docker_request())
    chunk = SubprocessChunk("stdout", b"traceback-sentinel-boundary-chunk")
    assert handle._slot.controller.publish_chunk(chunk)
    return process, harness, runner, handle, chunk


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("count", [1, 65])
def test_boundary_entry_controls_scrub_request_before_fresh_first_control(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[KeyboardInterrupt] | type[SystemExit],
    count: int,
) -> None:
    controls = FiniteControls(kind, count)
    monkeypatch.setattr(boundary_module, "_boundary_entry", controls)
    harness = Harness()
    runner = harness.runner()
    request = docker_request(stdin=b"traceback-sentinel-boundary-entry")
    with pytest.raises(kind) as captured:
        runner.start_attached(request)
    _assert_expected_control(captured, kind)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-boundary-entry",),
    )
    assert controls.calls == count + 1
    assert harness.calls == [] and runner._slots == []
    assert supervisor_module._KERNELS == {}


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("count", [1, 65])
def test_boundary_success_controls_abort_active_child_before_delivery(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[KeyboardInterrupt] | type[SystemExit],
    count: int,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    controls = FiniteControls(kind, count)
    monkeypatch.setattr(boundary_module, "_boundary_success", controls)
    with pytest.raises(kind) as captured:
        handle.poll()
    _assert_expected_control(captured, kind)
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert controls.calls == count + 1
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert handle._slot.controller.chunk_count() == 0
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("count", [1, 65])
def test_boundary_final_scrub_controls_preserve_origin_and_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[KeyboardInterrupt] | type[SystemExit],
    count: int,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()

    def origin(_handle: StreamingAttachedCommand) -> None:
        if kind is SystemExit:
            raise SystemExit(-7)
        raise KeyboardInterrupt

    controls = FiniteControls(kind, count)
    monkeypatch.setattr(StreamingAttachedCommand, "_synchronize_worker_control", origin)
    monkeypatch.setattr(boundary_module, "_boundary_final_scrub", controls)
    with pytest.raises(kind) as captured:
        handle.poll()
    _assert_expected_control(captured, kind)
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert controls.calls == count + 1
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_boundary_abort_recontrols_cannot_replace_first_or_escape_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    original_abort = StreamingAttachedCommand._boundary_abort
    later = FiniteControls(SystemExit, 65, code=7)

    def origin(_handle: StreamingAttachedCommand) -> None:
        raise KeyboardInterrupt

    def recontrolled_abort(self: StreamingAttachedCommand, **values: object) -> object:
        later()
        return original_abort(self, **values)  # type: ignore[arg-type]

    monkeypatch.setattr(StreamingAttachedCommand, "_synchronize_worker_control", origin)
    monkeypatch.setattr(StreamingAttachedCommand, "_boundary_abort", recontrolled_abort)
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.poll()
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert later.calls == 66
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert handle._slot.controller.control() == (KeyboardInterrupt, None)
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_first_line_traced_wrapper_control_is_fresh_and_scrubs_request() -> None:
    harness = Harness()
    runner = harness.runner()
    request = docker_request(stdin=b"traceback-sentinel-line-trace-entry")
    wrapped_code = runner.start_attached.__code__
    line_events = 0

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal line_events
        if getattr(frame, "f_code", None) is wrapped_code and event == "line":
            line_events += 1
            if line_events == 1:
                raise SystemExit("traceback-sentinel-unsafe-line-control")
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(SystemExit) as captured:
            runner.start_attached(request)
    finally:
        sys.settrace(previous)
    assert captured.value.code == 1
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=(
            "traceback-sentinel-line-trace-entry",
            "traceback-sentinel-unsafe-line-control",
        ),
    )
    assert harness.calls == [] and runner._slots == []


def test_line_traced_recontrol_at_abort_entry_preserves_first_and_cleans() -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    handle._slot.controller.capture_control(KeyboardInterrupt())
    abort_code = StreamingAttachedCommand._boundary_abort.__code__
    fired = False

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal fired
        if not fired and getattr(frame, "f_code", None) is abort_code and event == "line":
            fired = True
            raise SystemExit(7)
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt) as captured:
            handle.poll()
    finally:
        sys.settrace(previous)
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert fired
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert handle._slot.controller.control() == (KeyboardInterrupt, None)
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_boundary_normalization_recontrols_preserve_first_and_abort_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    original_normalizer = boundary_module.control_signal
    handler_controls = FiniteControls(SystemExit, 65, code=7)
    calls = 0

    def nested_normalizer(error: KeyboardInterrupt | SystemExit) -> object:
        nonlocal calls
        calls += 1
        if calls <= 65:
            raise SystemExit(7)
        return original_normalizer(error)

    def first_control(_handle: StreamingAttachedCommand) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(boundary_module, "control_signal", nested_normalizer)
    monkeypatch.setattr(
        boundary_module,
        "_boundary_control_handler_entry",
        handler_controls,
    )
    monkeypatch.setattr(
        StreamingAttachedCommand,
        "_synchronize_worker_control",
        first_control,
    )
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.poll()
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert handler_controls.calls == 66 and calls == 66
    assert handle._slot.controller.control() == (KeyboardInterrupt, None)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_controller_normalization_recontrols_preserve_first_and_settle_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    original_normalizer = state_module.control_signal
    publication_controls = FiniteControls(SystemExit, 65, code=7)
    calls = 0

    def nested_normalizer(error: KeyboardInterrupt | SystemExit) -> object:
        nonlocal calls
        calls += 1
        if calls <= 65:
            raise SystemExit(7)
        return original_normalizer(error)

    monkeypatch.setattr(state_module, "control_signal", nested_normalizer)
    monkeypatch.setattr(
        state_module,
        "_control_publication_entry",
        publication_controls,
    )
    handle._slot.controller.capture_control(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.poll()
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert calls == 66 and publication_controls.calls == 67
    assert handle._slot.controller.control() == (KeyboardInterrupt, None)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


def _supervisor_seams(harness: Harness) -> SupervisorSeams:
    return SupervisorSeams(
        factory=harness.factory,
        signal_group=harness.signal_group,
        group_exists=harness.group_exists,
        group_identity=harness.group_identity,
        selector_factory=harness.selector_factory,
        set_blocking=harness.set_blocking,
        clock=time.monotonic,
        thread_factory=threading.Thread,
    )


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_kernel_pre_return_control_cancels_registry_and_scrubs_raw_request(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[KeyboardInterrupt] | type[SystemExit],
) -> None:
    controls = FiniteControls(kind, 1)
    monkeypatch.setattr(supervisor_module, "_kernel_pre_return", controls)
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-kernel-return"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    with pytest.raises(kind) as captured:
        prepare_supervisor(
            request=request,
            controller=controller,
            seams=_supervisor_seams(harness),
            slot=slot,
        )
    _assert_expected_control(captured, kind)
    _assert_fresh_scrubbed(
        captured.value,
        secrets=("traceback-sentinel-kernel-return",),
    )
    assert request.stdin == bytearray()
    assert slot.pending_launch() is None
    assert supervisor_module._KERNELS == {}


def test_concurrent_cancel_cannot_cross_slot_to_kernel_publication_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-publication-race"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    midpoint = threading.Event()
    release = threading.Event()
    launches: list[object] = []
    errors: list[BaseException] = []

    def hold_midpoint() -> None:
        midpoint.set()
        release.wait(1.0)

    def prepare() -> None:
        try:
            launches.append(
                prepare_supervisor(
                    request=request,
                    controller=controller,
                    seams=_supervisor_seams(harness),
                    slot=slot,
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(supervisor_module, "_kernel_publication_midpoint", hold_midpoint)
    publisher = threading.Thread(target=prepare)
    publisher.start()
    assert midpoint.wait(1.0)
    assert supervisor_module._KERNELS == {}
    canceller = threading.Thread(target=lambda: cancel_supervisor_slot(slot))
    canceller.start()
    threading.Event().wait(0.02)
    assert canceller.is_alive()
    release.set()
    publisher.join(1.0)
    canceller.join(1.0)
    assert not publisher.is_alive() and not canceller.is_alive()
    assert errors == [] and len(launches) == 1
    assert slot.pending_launch() is None
    assert request.stdin == bytearray()
    assert supervisor_module._KERNELS == {}


@pytest.mark.parametrize(
    "seam",
    ["_kernel_request_scrubbed", "_cancel_token_observed", "_supervisor_launch_cancelled"],
)
def test_cancel_recontrols_retain_token_until_kernel_is_scrubbed_and_removed(
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-kernel-cancel"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    launch = prepare_supervisor(
        request=request,
        controller=controller,
        seams=_supervisor_seams(harness),
        slot=slot,
    )
    controls = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(supervisor_module, seam, controls)
    cancel_supervisor_slot(slot)
    assert controls.calls == 66
    assert controller.control() == (SystemExit, 7)
    assert launch.token is None
    assert slot.pending_launch() is None
    assert request.stdin == bytearray()
    assert supervisor_module._KERNELS == {}


@pytest.mark.parametrize(
    "target_name",
    ["cancel_supervisor_slot", "cancel_supervisor_launch", "_cancel_kernel"],
)
def test_first_line_cancel_controls_retain_slot_authority_until_kernel_is_scrubbed(
    target_name: str,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-cancel-first-line"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    launch = prepare_supervisor(
        request=request,
        controller=controller,
        seams=_supervisor_seams(harness),
        slot=slot,
    )
    target_code = getattr(supervisor_module, target_name).__code__
    fired = False

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal fired
        if not fired and getattr(frame, "f_code", None) is target_code and event == "line":
            fired = True
            raise KeyboardInterrupt
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        cancel_supervisor_slot(slot)
    finally:
        sys.settrace(previous)
    assert fired
    assert controller.control() == (KeyboardInterrupt, None)
    assert launch.token is None and slot.pending_launch() is None
    assert request.stdin == bytearray()
    assert supervisor_module._KERNELS == {}


def test_registry_lookup_controls_latch_first_until_raw_kernel_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-lookup-control"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    launch = prepare_supervisor(
        request=request,
        controller=controller,
        seams=_supervisor_seams(harness),
        slot=slot,
    )
    calls = 0

    def lookup_control() -> None:
        nonlocal calls
        calls += 1
        if calls <= 66:
            assert controller.control() is None
            assert supervisor_module._KERNELS
            if calls == 1:
                raise KeyboardInterrupt
            raise SystemExit(7)

    monkeypatch.setattr(supervisor_module, "_kernel_lookup_entry", lookup_control)
    cancel_supervisor_slot(slot)
    assert calls == 67
    assert controller.control() == (KeyboardInterrupt, None)
    assert launch.token is None and slot.pending_launch() is None
    assert request.stdin == bytearray()
    assert supervisor_module._KERNELS == {}


def test_launch_token_clear_control_is_lossless_after_kernel_authority_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = FiniteControls(SystemExit, 1, code=7)
    monkeypatch.setattr(supervisor_module, "_launch_token_cleared", controls)
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()
    with pytest.raises(SystemExit) as captured:
        runner.start_attached(docker_request())
    assert captured.value.code == 7
    _assert_fresh_scrubbed(captured.value, runner)
    assert controls.calls == 1
    assert supervisor_module._KERNELS == {}
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_token_clear_and_slot_release_recontrols_keep_first_after_kernel_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-clear-release"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    launch = prepare_supervisor(
        request=request,
        controller=controller,
        seams=_supervisor_seams(harness),
        slot=slot,
    )
    token_clear = FiniteControls(KeyboardInterrupt, 1)
    slot_release = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(supervisor_module, "_launch_token_cleared", token_clear)
    monkeypatch.setattr(supervisor_module, "_slot_launch_released", slot_release)
    cancel_supervisor_slot(slot)
    assert token_clear.calls == 1 and slot_release.calls == 66
    assert controller.control() == (KeyboardInterrupt, None)
    assert launch.token is None and slot.pending_launch() is None
    assert request.stdin == bytearray()
    assert supervisor_module._KERNELS == {}


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_append_before_publication_control_rolls_back_registered_slot_atomically(
    monkeypatch: pytest.MonkeyPatch,
    kind: type[KeyboardInterrupt] | type[SystemExit],
) -> None:
    controls = FiniteControls(kind, 1)
    monkeypatch.setattr(facade_module, "_reservation_appended", controls)
    harness = Harness()
    runner = harness.runner()
    with pytest.raises(kind) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-reservation"))
    _assert_expected_control(captured, kind)
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-reservation",),
    )
    assert harness.calls == []
    assert runner._slots == []
    assert supervisor_module._KERNELS == {}


def test_first_line_recontrol_during_reservation_rollback_preserves_first_and_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def first_control() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(facade_module, "_reservation_appended", first_control)
    harness = Harness()
    runner = harness.runner()
    rollback_code = runner._drop_reservation.__code__
    fired = False

    def trace(frame: object, event: str, _argument: object) -> object:
        nonlocal fired
        if not fired and getattr(frame, "f_code", None) is rollback_code and event == "line":
            fired = True
            raise SystemExit(7)
        return trace

    request = docker_request(stdin=b"traceback-sentinel-rollback-first-line")
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt) as captured:
            runner.start_attached(request)
    finally:
        sys.settrace(previous)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-rollback-first-line",),
    )
    assert fired
    assert harness.calls == [] and runner._slots == []
    assert supervisor_module._KERNELS == {}


def test_many_recontrols_during_reservation_rollback_eventually_remove_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def first_control() -> None:
        raise KeyboardInterrupt

    later = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(facade_module, "_reservation_appended", first_control)
    monkeypatch.setattr(facade_module, "_reservation_rollback_entry", later)
    harness = Harness()
    runner = harness.runner()
    with pytest.raises(KeyboardInterrupt):
        runner.start_attached(docker_request())
    assert later.calls == 66
    assert harness.calls == [] and runner._slots == []
    assert supervisor_module._KERNELS == {}


def test_public_abort_tombstones_inflight_admission_until_starter_acknowledges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    runner = harness.runner()
    reserved = threading.Event()
    release = threading.Event()
    success_calls = 0
    reservation_calls = 0

    def hold_reservation() -> None:
        nonlocal reservation_calls
        reservation_calls += 1
        if reservation_calls != 1:
            return
        reserved.set()
        release.wait(1.0)

    def fail_first_success_boundary() -> None:
        nonlocal success_calls
        success_calls += 1
        if success_calls == 1:
            raise RuntimeError("synthetic recovery publication failure")

    monkeypatch.setattr(facade_module, "_slot_reserved", hold_reservation)
    monkeypatch.setattr(boundary_module, "_boundary_success", fail_first_success_boundary)
    request = docker_request(stdin=b"traceback-sentinel-admission-tombstone")
    starter_errors: list[BaseException] = []

    def start(start_request: object) -> None:
        try:
            runner.start_attached(start_request)  # type: ignore[arg-type]
        except BaseException as error:
            start_request = None
            starter_errors.append(error)

    starter = threading.Thread(target=start, args=(request,))
    starter.start()
    assert reserved.wait(1.0)
    assert len(runner._slots) == 1 and harness.calls == []
    with pytest.raises(CoturnSubprocessError) as recovery_error:
        runner.recover_quarantined()
    _assert_fresh_scrubbed(recovery_error.value, runner)
    assert runner._slots[0].admission_cancelled()
    unsettled = runner.settle_owned()
    assert type(unsettled) is bool and unsettled is False
    assert starter.is_alive() and len(runner._slots) == 1
    assert harness.calls == [] and supervisor_module._KERNELS == {}
    assert spawn_module._JOBS == {}
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request())
    assert harness.calls == [] and len(runner._slots) == 1

    release.set()
    starter.join(1.0)
    assert not starter.is_alive() and len(starter_errors) == 1
    assert type(starter_errors[0]) is CoturnSubprocessError
    _assert_fresh_scrubbed(
        starter_errors[0],
        request,
        runner,
        secrets=("traceback-sentinel-admission-tombstone",),
    )
    assert runner._slots == []
    assert supervisor_module._KERNELS == {}
    assert spawn_module._JOBS == {}
    assert runner.settle_owned() is True
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request())
    assert harness.calls == [] and runner._slots == []


def test_failed_launch_cancellation_entry_recontrols_retain_and_scrub_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_entry = FiniteControls(KeyboardInterrupt, 1)
    kernel_entry = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(supervisor_module, "_cancel_launch_entry", launch_entry)
    monkeypatch.setattr(supervisor_module, "_cancel_kernel_entry", kernel_entry)

    def failed_thread_factory(**_values: object) -> threading.Thread:
        raise RuntimeError("synthetic thread start rejection")

    harness = Harness()
    runner = harness.runner(thread_factory=failed_thread_factory)
    request = docker_request(stdin=b"traceback-sentinel-failed-launch-cancel")
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(request)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-failed-launch-cancel",),
    )
    assert launch_entry.calls == 67
    assert kernel_entry.calls == 66
    assert harness.calls == []
    assert runner._slots == []
    assert supervisor_module._KERNELS == {}


def test_handle_is_constructed_during_stable_active_before_io_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    observed: list[tuple[Lifecycle, bool]] = []

    def inspect() -> None:
        controller = runner._slots[0].controller
        observed.append((controller.lifecycle(), controller.active_io_ready()))

    monkeypatch.setattr(facade_module, "_handle_constructed", inspect)
    handle = runner.start_attached(docker_request())
    assert observed == [(Lifecycle.ACTIVE, False)]
    assert handle.collect(timeout_seconds=1.0).returncode == 0
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_draining_deadline_quarantines_reaped_group_without_reuse_signal() -> None:
    process = FakeProcess(auto_exit=True, group_alive=True, pipes_eof=False)
    process.term_exits = False
    harness = harness_with_tracking(process)
    runner = harness.runner()
    handle = runner.start_attached(docker_request(timeout_seconds=0.1))
    assert _wait_until(
        lambda: handle._slot.controller.lifecycle() is Lifecycle.DRAINING,
        timeout=0.5,
    )
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        handle.collect(timeout_seconds=1.0)
    assert harness.signals == []
    assert handle._slot.controller.failure() == "Coturn subprocess timed out"
    assert handle._slot.controller.lifecycle() is Lifecycle.QUARANTINED
    process.exit(0)
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_spawn_clock_quarantines_blocked_owner_and_recovers(invalid: float) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def hold_factory(_process: FakeProcess) -> None:
        entered.set()
        release.wait(1.0)

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else invalid

    harness.factory_entered = hold_factory
    runner = harness.runner(clock=clock)
    started = time.monotonic()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.start_attached(docker_request(timeout_seconds=0.1))
    assert time.monotonic() - started < 0.5
    assert entered.is_set()
    assert runner._slots[0].controller.lifecycle() is Lifecycle.QUARANTINED
    unsettled = runner.settle_owned()
    assert type(unsettled) is bool and unsettled is False
    assert len(runner._slots) == 1 and runner._slots[0].controller.poisoned()
    release.set()
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_active_clock_fails_closed_and_settles_owned_child(invalid: float) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    invalid_now = threading.Event()

    def clock() -> float:
        return invalid if invalid_now.is_set() else time.monotonic()

    handle = harness.runner(clock=clock).start_attached(docker_request())
    invalid_now.set()
    with pytest.raises(CoturnSubprocessError, match=r"clock failed$"):
        handle.collect(timeout_seconds=1.0)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_draining_clock_quarantines_reaped_group_without_signal(
    invalid: float,
) -> None:
    process = FakeProcess(auto_exit=True, group_alive=True, pipes_eof=False)
    process.term_exits = False
    harness = harness_with_tracking(process)
    invalid_now = threading.Event()

    def clock() -> float:
        return invalid if invalid_now.is_set() else time.monotonic()

    runner = harness.runner(clock=clock)
    handle = runner.start_attached(docker_request())
    assert _wait_until(
        lambda: handle._slot.controller.lifecycle() is Lifecycle.DRAINING,
        timeout=0.5,
    )
    invalid_now.set()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        handle.collect(timeout_seconds=1.0)
    assert harness.signals == []
    assert handle._slot.controller.failure() == "Coturn subprocess clock failed"
    assert handle._slot.controller.lifecycle() is Lifecycle.QUARANTINED
    invalid_now.clear()
    process.exit(0)
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_int_subclass_returncode_normalizes_before_clean_and_releases_capacity() -> None:
    class DerivedReturncode(int):
        pass

    first = FakeProcess(returncode=DerivedReturncode(7))
    second = FakeProcess(returncode=0)
    harness = harness_with_tracking(first, second)
    runner = harness.runner()
    result = runner.run(docker_request())
    assert type(result.returncode) is int and result.returncode == 7
    assert runner._slots == []
    assert runner.run(docker_request()).returncode == 0
    assert runner._slots == []


def test_two_ready_streams_at_queue_255_never_read_and_drop_the_second_chunk() -> None:
    process = FakeProcess(
        stdout=b"A",
        stderr=b"B",
        auto_exit=False,
        pipes_eof=False,
    )
    harness = harness_with_tracking(process)
    select_entered = threading.Event()
    release_select = threading.Event()

    class HeldSelector(FakeSelector):
        def __init__(self, plan: ControlPlan) -> None:
            super().__init__(plan)
            self.held = False

        def select(self, timeout: float | None = None) -> list[tuple[object, int]]:
            if not self.held:
                self.held = True
                select_entered.set()
                release_select.wait(1.0)
            return super().select(timeout)

    def selector_factory() -> HeldSelector:
        selector = HeldSelector(harness.plan)
        harness.selectors.append(selector)
        return selector

    handle = harness.runner(selector_factory=selector_factory).start_attached(docker_request())
    assert select_entered.wait(1.0)
    for _ in range(MAX_QUEUED_CHUNKS - 1):
        assert handle._slot.controller.publish_chunk(SubprocessChunk("stdout", b"x"))
    release_select.set()
    assert _wait_until(lambda: process.stdout_pipe.read_count == 1)
    assert handle._slot.controller.chunk_count() == MAX_QUEUED_CHUNKS
    assert process.stderr_pipe.read_count == 0
    assert handle._slot.controller.pop_chunk() is not None
    assert _wait_until(lambda: process.stderr_pipe.read_count == 1)
    chunks: list[SubprocessChunk] = []
    while (chunk := handle._slot.controller.pop_chunk()) is not None:
        chunks.append(chunk)
    assert [chunk.data for chunk in chunks].count(b"A") == 1
    assert [chunk.data for chunk in chunks].count(b"B") == 1
    handle.terminate()


def test_join_controls_before_and_after_receipt_publication_retain_thread_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    handle = runner.start_attached(docker_request())
    assert _wait_until(lambda: handle._slot.controller.lifecycle() is Lifecycle.CLEAN)
    slot = handle._slot
    retained_thread = slot.thread
    assert retained_thread is not None
    original_mark = ControllerState.mark_joined
    before = FiniteControls(KeyboardInterrupt, 65)
    after = FiniteControls(SystemExit, 1, code=7)

    def controlled_mark(self: ControllerState) -> bool:
        before()
        return original_mark(self)

    monkeypatch.setattr(ControllerState, "mark_joined", controlled_mark)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", after)
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.collect(timeout_seconds=1.0)
    _assert_fresh_scrubbed(captured.value, handle, runner, retained_thread)
    assert before.calls == 66 and after.calls == 1
    assert slot.thread is None
    assert slot.controller.clean_joined()
    assert slot.controller.control() == (KeyboardInterrupt, None)
    assert runner._slots == []


def test_blocked_supervisor_factory_and_concurrent_abort_cannot_start_stale_token() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    factory_entered = threading.Event()
    release_factory = threading.Event()

    def held_thread_factory(**values: object) -> threading.Thread:
        if values.get("name") == "coturn-subprocess-supervisor":
            factory_entered.set()
            release_factory.wait(1.0)
        return threading.Thread(**values)  # type: ignore[arg-type]

    runner = harness.runner(thread_factory=held_thread_factory)
    starter_errors: list[BaseException] = []
    abort_results: list[object] = []

    def start() -> None:
        try:
            runner.start_attached(docker_request(stdin=b"traceback-sentinel-start-cancel-race"))
        except BaseException as error:
            starter_errors.append(error)

    starter = threading.Thread(target=start)
    starter.start()
    assert factory_entered.wait(1.0)
    assert len(runner._slots) == 1 and supervisor_module._KERNELS
    aborter = threading.Thread(
        target=lambda: abort_results.append(
            runner._boundary_abort(
                control=(KeyboardInterrupt, None),
                failure=None,
                uncertain=True,
            )
        )
    )
    aborter.start()
    threading.Event().wait(0.02)
    assert aborter.is_alive()
    release_factory.set()
    starter.join(2.0)
    aborter.join(2.0)
    assert not starter.is_alive() and not aborter.is_alive()
    assert len(starter_errors) == 1 and type(starter_errors[0]) is KeyboardInterrupt
    assert abort_results == [(KeyboardInterrupt, None)]
    assert runner._slots == [] and supervisor_module._KERNELS == {}
    assert spawn_module._JOBS == {}
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_cancel_claim_blocks_concurrent_start_until_kernel_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    request = validate_request(docker_request(stdin=b"traceback-sentinel-cancel-start-race"))
    assert request is not None
    controller = ControllerState()
    slot = SupervisorSlot(controller=controller)
    launch = prepare_supervisor(
        request=request,
        controller=controller,
        seams=_supervisor_seams(harness),
        slot=slot,
    )
    cancel_entered = threading.Event()
    release_cancel = threading.Event()
    start_results: list[bool] = []

    def hold_cancel() -> None:
        cancel_entered.set()
        release_cancel.wait(1.0)

    monkeypatch.setattr(supervisor_module, "_cancel_kernel_entry", hold_cancel)
    canceller = threading.Thread(target=lambda: cancel_supervisor_slot(slot))
    canceller.start()
    assert cancel_entered.wait(1.0)
    starter = threading.Thread(
        target=lambda: start_results.append(
            supervisor_module.start_supervisor_thread(launch, slot, lambda _token: None)
        )
    )
    starter.start()
    threading.Event().wait(0.02)
    assert starter.is_alive()
    release_cancel.set()
    canceller.join(1.0)
    starter.join(1.0)
    assert not canceller.is_alive() and not starter.is_alive()
    assert start_results == [False]
    assert harness.calls == [] and request.stdin == bytearray()
    assert launch.token is None and slot.pending_launch() is None
    assert slot.thread is None and supervisor_module._KERNELS == {}


def test_spawn_job_publication_control_cancels_raw_job_and_settles_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = FiniteControls(SystemExit, 1, code=7)
    scrub_controls = FiniteControls(SystemExit, 65, code=9)
    monkeypatch.setattr(spawn_module, "_spawn_job_published", controls)
    monkeypatch.setattr(spawn_module, "_spawn_request_scrubbed", scrub_controls)
    harness = Harness()
    runner = harness.runner()
    request = docker_request(stdin=b"traceback-sentinel-spawn-publication")
    with pytest.raises(SystemExit) as captured:
        runner.start_attached(request)
    assert captured.value.code == 7
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-spawn-publication",),
    )
    assert controls.calls == 1 and scrub_controls.calls == 66 and harness.calls == []
    assert runner._slots == [] and spawn_module._JOBS == {}
    assert supervisor_module._KERNELS == {}


def test_spawn_control_normalization_recontrols_preserve_first_and_scrub_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ControlPlan()
    plan.add("factory", KeyboardInterrupt)
    controls = FiniteControls(SystemExit, 65, code=7)
    publication_controls = FiniteControls(SystemExit, 65, code=9)
    monkeypatch.setattr(values_module, "_control_normalization_entry", controls)
    monkeypatch.setattr(spawn_module, "_spawn_control_published", publication_controls)
    harness = Harness(FakeProcess(plan=plan), plan=plan)
    runner = harness.runner()
    request = docker_request(stdin=b"traceback-sentinel-spawn-normalizer")
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(request)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-spawn-normalizer",),
    )
    assert controls.calls >= 66
    assert publication_controls.calls == 66
    assert runner._slots == [] and spawn_module._JOBS == {}
    assert supervisor_module._KERNELS == {}


def test_chunk_normalization_recontrols_scrub_raw_inputs_before_fresh_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FiniteControls(KeyboardInterrupt, 1)
    nested = FiniteControls(SystemExit, 65, code=7)
    scrub_controls = FiniteControls(SystemExit, 65, code=9)
    monkeypatch.setattr(values_module, "_chunk_validation_entry", first)
    monkeypatch.setattr(values_module, "_control_normalization_entry", nested)
    monkeypatch.setattr(values_module, "_chunk_scrub_entry", scrub_controls)
    raw = b"traceback-sentinel-chunk-normalizer"
    with pytest.raises(KeyboardInterrupt) as captured:
        SubprocessChunk("stdout", raw)
    _assert_fresh_scrubbed(
        captured.value,
        raw,
        secrets=("traceback-sentinel-chunk-normalizer",),
    )
    assert first.calls == 1 and nested.calls == 66 and scrub_controls.calls == 66


@pytest.mark.parametrize("phase", ["classification", "publication"])
def test_normal_failure_controls_abort_active_child_before_fresh_delivery(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    process, _harness, runner, handle, chunk = _active_handle()
    controls = FiniteControls(SystemExit, 65, code=7)

    def fail_normally(_handle: StreamingAttachedCommand) -> None:
        raise CoturnSubprocessError("Coturn subprocess execution failed")

    monkeypatch.setattr(
        StreamingAttachedCommand,
        "_synchronize_worker_control",
        fail_normally,
    )
    if phase == "classification":
        original = boundary_module._safe_failure

        def controlled_classification(
            error: CoturnSubprocessError,
            fallback: str,
        ) -> str:
            controls()
            return original(error, fallback)

        monkeypatch.setattr(boundary_module, "_safe_failure", controlled_classification)
    else:
        monkeypatch.setattr(boundary_module, "_boundary_failure_published", controls)
    with pytest.raises(SystemExit) as captured:
        handle.poll()
    assert captured.value.code == 7
    _assert_fresh_scrubbed(captured.value, handle, runner, chunk)
    assert controls.calls == 66
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_argv_tuple_cap_precedes_iteration(monkeypatch: pytest.MonkeyPatch) -> None:
    helper_called = False

    def forbidden_iteration(_argv: tuple[object, ...]) -> bool:
        nonlocal helper_called
        helper_called = True
        raise AssertionError

    monkeypatch.setattr(request_module, "_valid_argv", forbidden_iteration)
    request = docker_request()
    object.__setattr__(request, "argv", ("/usr/bin/docker", *("x" for _ in range(256))))
    assert validate_request(request) is None
    assert not helper_called


def test_argv_utf8_validation_stops_at_fixed_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = 0

    def count_character() -> None:
        nonlocal checked
        checked += 1

    monkeypatch.setattr(request_module, "_argument_character_checked", count_character)
    unit = "\U0001f642"
    assert ord(unit) == 0x1F642 and unit.encode("utf-8") == b"\xf0\x9f\x99\x82"
    value = unit * 20_000
    request = docker_request()
    object.__setattr__(request, "argv", ("/usr/bin/docker", value))
    assert validate_request(request) is None
    executable = "/usr/bin/docker"
    remaining = request_module._MAX_ARGUMENT_BYTES - len(executable.encode("utf-8")) - 1
    first_over_budget = (remaining - 1) // len(unit.encode("utf-8")) + 1
    assert checked == len(executable) + first_over_budget


def test_settle_owned_closes_active_slot_when_returned_handle_was_not_retained() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()

    def lose_returned_handle() -> None:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-unclaimed-handle"))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        lose_returned_handle()
    assert len(runner._slots) == 1
    assert runner._slots[0].controller.lifecycle() is Lifecycle.ACTIVE
    settled = runner.settle_owned()
    assert type(settled) is bool and settled is True
    assert runner._slots == [] and supervisor_module._KERNELS == {}
    assert spawn_module._JOBS == {}
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_recovery_resamples_join_control_before_removing_clean_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    handle = runner.start_attached(docker_request())
    slot = handle._slot
    assert _wait_until(lambda: slot.controller.lifecycle() is Lifecycle.CLEAN)
    slot.controller._quarantined = True
    controls = FiniteControls(KeyboardInterrupt, 1)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", controls)
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.recover_quarantined(timeout_seconds=1.0)
    _assert_fresh_scrubbed(captured.value, runner, slot)
    assert controls.calls == 1
    assert slot.controller.control() == (KeyboardInterrupt, None)
    assert slot.controller.clean_joined() and runner._slots == []


def test_recovery_rethrows_known_control_after_clean_slot_removal() -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    slot = runner.start_attached(docker_request())._slot
    assert _wait_until(lambda: slot.controller.lifecycle() is Lifecycle.CLEAN)
    slot.controller._quarantined = True
    slot.controller.capture_control_signal((KeyboardInterrupt, None))
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.recover_quarantined(timeout_seconds=1.0)
    _assert_fresh_scrubbed(captured.value, runner, slot)
    assert slot.controller.clean_joined() and runner._slots == []
    assert runner._control_latch is None


def test_clean_recovery_join_failure_honors_deadline_without_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    slot = runner.start_attached(docker_request())._slot
    assert _wait_until(lambda: slot.controller.lifecycle() is Lifecycle.CLEAN)
    slot.controller._quarantined = True
    original_join = SupervisorSlot.join_if_clean
    calls = 0

    def refuse_join(self: SupervisorSlot) -> bool:
        nonlocal calls
        if self is slot:
            calls += 1
            return False
        return original_join(self)

    monkeypatch.setattr(SupervisorSlot, "join_if_clean", refuse_join)
    started = time.monotonic()
    assert runner.recover_quarantined(timeout_seconds=0.1) is False
    elapsed = time.monotonic() - started
    assert elapsed >= 0.08 and calls <= 4
    assert original_join(slot)
    runner._settled(slot)
    assert runner._slots == []


def test_reservation_rollback_resamples_control_caught_after_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = FiniteControls(KeyboardInterrupt, 1)
    monkeypatch.setattr(
        facade_module,
        "_reservation_appended",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic admission failure")),
    )
    monkeypatch.setattr(facade_module, "_reservation_dropped", controls)
    runner = Harness().runner()
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-rollback-control"))
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-rollback-control",),
    )
    assert controls.calls == 1 and runner._slots == []


def test_settle_owned_resamples_control_caught_by_reservation_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Harness().runner()
    slot = SupervisorSlot(controller=ControllerState())
    slot.close_admission()
    runner._slots.append(slot)
    controls = FiniteControls(SystemExit, 1, code=7)
    monkeypatch.setattr(facade_module, "_reservation_dropped", controls)
    with pytest.raises(SystemExit) as captured:
        runner.settle_owned()
    assert captured.value.code == 7
    _assert_fresh_scrubbed(captured.value, runner, slot)
    assert controls.calls == 1 and runner._slots == []


def test_settle_owned_resamples_control_caught_by_clean_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = harness_with_tracking(FakeProcess()).runner()
    slot = runner.start_attached(docker_request())._slot
    assert _wait_until(lambda: slot.controller.lifecycle() is Lifecycle.CLEAN)
    controls = FiniteControls(SystemExit, 1, code=7)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", controls)
    with pytest.raises(SystemExit) as captured:
        runner.settle_owned()
    assert captured.value.code == 7
    _assert_fresh_scrubbed(captured.value, runner, slot)
    assert controls.calls == 1 and slot.controller.clean_joined()
    assert runner._slots == []


def test_failed_start_cleanup_resamples_drop_control_before_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_preparation(**_values: object) -> object:
        raise RuntimeError("synthetic preparation failure")

    controls = FiniteControls(KeyboardInterrupt, 1)
    monkeypatch.setattr(facade_module, "prepare_supervisor", reject_preparation)
    monkeypatch.setattr(facade_module, "_reservation_dropped", controls)
    runner = Harness().runner()
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-failed-start-drop"))
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-failed-start-drop",),
    )
    assert controls.calls == 1 and runner._slots == []


def test_failed_start_cleanup_resamples_join_control_before_normal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()
    controls = FiniteControls(KeyboardInterrupt, 1)

    def reject_launch_return() -> None:
        raise RuntimeError("synthetic launch return failure")

    monkeypatch.setattr(facade_module, "_launch_returned", reject_launch_return)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", controls)
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-failed-start-join"))
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-failed-start-join",),
    )
    assert controls.calls == 1 and runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_runner_abort_resamples_join_control_before_authority_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()
    slot = runner.start_attached(docker_request())._slot
    controls = FiniteControls(KeyboardInterrupt, 1)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", controls)
    authoritative = runner._boundary_abort(control=None, failure=None, uncertain=True)
    assert authoritative == (KeyboardInterrupt, None)
    assert controls.calls == 1 and slot.controller.clean_joined()
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_runner_abort_resamples_concurrent_first_control_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    runner = harness_with_tracking(process).runner()
    slot = runner.start_attached(docker_request())._slot
    original_capture = ControllerState.capture_control_signal
    raced = False

    def concurrent_first(self: ControllerState, offered: object) -> None:
        nonlocal raced
        if self is slot.controller and not raced:
            raced = True
            original_capture(self, (KeyboardInterrupt, None))
        original_capture(self, offered)  # type: ignore[arg-type]

    monkeypatch.setattr(ControllerState, "capture_control_signal", concurrent_first)
    authoritative = runner._boundary_abort(
        control=(SystemExit, 7),
        failure=None,
        uncertain=True,
    )
    assert raced and authoritative == (KeyboardInterrupt, None)
    assert slot.controller.control() == (KeyboardInterrupt, None)
    assert slot.controller.clean_joined() and runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize(
    "malformation",
    ["object", "list-pair", "too-many", "duplicate", "wrong-stream", "bool-mask"],
)
def test_malformed_selector_events_fail_closed_and_settle_owned_child(
    malformation: str,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    armed = threading.Event()

    class MalformedSelector(FakeSelector):
        def __init__(self, plan: ControlPlan) -> None:
            super().__init__(plan)
            self.fired = False

        def select(self, timeout: float | None = None) -> list[object]:
            if armed.is_set() and not self.fired:
                self.fired = True
                stdout = process.stdout_pipe
                stderr = process.stderr_pipe
                key = SimpleNamespace(
                    fileobj=stdout,
                    data="stdout",
                    events=selectors.EVENT_READ,
                )
                event: object = (key, selectors.EVENT_READ)
                if malformation == "object":
                    return [object()]
                if malformation == "list-pair":
                    return [[key, selectors.EVENT_READ]]
                if malformation == "too-many":
                    return [event, event, event, event]
                if malformation == "duplicate":
                    return [event, event]
                if malformation == "wrong-stream":
                    key.fileobj = stderr
                    return [event]
                return [(key, True)]
            return super().select(timeout)  # type: ignore[return-value]

    def selector_factory() -> MalformedSelector:
        selector = MalformedSelector(harness.plan)
        harness.selectors.append(selector)
        return selector

    runner = harness.runner(selector_factory=selector_factory)
    handle = runner.start_attached(docker_request())
    armed.set()
    with pytest.raises(CoturnSubprocessError, match=r"selector failed$"):
        handle.collect(timeout_seconds=1.0)
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("attribute", ["data", "fileobj", "events"])
@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
def test_throwing_selector_event_key_is_classified_inside_supervisor_boundary(
    attribute: str,
    kind: type[BaseException],
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    armed = threading.Event()

    class ThrowingKey:
        @property
        def data(self) -> str:
            if attribute == "data":
                raise kind
            return "stdout"

        @property
        def fileobj(self) -> object:
            if attribute == "fileobj":
                raise kind
            return process.stdout_pipe

        @property
        def events(self) -> int:
            if attribute == "events":
                raise kind
            return selectors.EVENT_READ

    class ThrowingEventSelector(FakeSelector):
        def __init__(self, plan: ControlPlan) -> None:
            super().__init__(plan)
            self.fired = False

        def select(self, timeout: float | None = None) -> list[tuple[object, int]]:
            if armed.is_set() and not self.fired:
                self.fired = True
                return [(ThrowingKey(), selectors.EVENT_READ)]
            return super().select(timeout)

    def selector_factory() -> ThrowingEventSelector:
        selector = ThrowingEventSelector(harness.plan)
        harness.selectors.append(selector)
        return selector

    handle = harness.runner(selector_factory=selector_factory).start_attached(docker_request())
    armed.set()
    expected = KeyboardInterrupt if kind is KeyboardInterrupt else CoturnSubprocessError
    with pytest.raises(expected) as captured:
        handle.collect(timeout_seconds=1.0)
    if kind is RuntimeError:
        assert str(captured.value).endswith("selector failed")
    assert handle._slot.controller.lifecycle() is Lifecycle.CLEAN
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("attribute", ["data", "fileobj", "events"])
def test_throwing_registered_selector_key_forces_control_cleanup(
    attribute: str,
) -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)

    class ThrowingRegisteredKey:
        def __init__(self, key: object) -> None:
            self.key = key

        @property
        def data(self) -> object:
            if attribute == "data":
                raise KeyboardInterrupt
            return self.key.data

        @property
        def fileobj(self) -> object:
            if attribute == "fileobj":
                raise KeyboardInterrupt
            return self.key.fileobj

        @property
        def events(self) -> object:
            if attribute == "events":
                raise KeyboardInterrupt
            return self.key.events

    class ThrowingKeySelector(FakeSelector):
        def __init__(self, plan: ControlPlan) -> None:
            super().__init__(plan)
            self.fired = False

        def get_key(self, fileobj: object) -> object:
            key = super().get_key(fileobj)  # type: ignore[arg-type]
            if not self.fired:
                self.fired = True
                return ThrowingRegisteredKey(key)
            return key

    def selector_factory() -> ThrowingKeySelector:
        selector = ThrowingKeySelector(harness.plan)
        harness.selectors.append(selector)
        return selector

    runner = harness.runner(selector_factory=selector_factory)
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-throwing-key"))
    _assert_fresh_scrubbed(
        captured.value,
        runner,
        secrets=("traceback-sentinel-throwing-key",),
    )
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("count", [1, 65])
def test_spawn_thread_scrub_publishes_finite_controls_and_clears_raw_target(
    kind: type[KeyboardInterrupt] | type[SystemExit],
    count: int,
) -> None:
    controls = FiniteControls(kind, count, code=7)

    class ScrubThread:
        def __init__(self) -> None:
            self._args: tuple[object, ...] = (b"traceback-sentinel-thread-arg",)
            self._kwargs: dict[str, object] = {"raw": b"traceback-sentinel-thread-kwarg"}
            self._target: object = b"traceback-sentinel-thread-target"

        @property
        def ident(self) -> int:
            controls()
            return 1

        def is_alive(self) -> bool:
            return False

    thread = ScrubThread()
    controller = ControllerState()
    spawn_module._scrub_thread(thread, controller)  # type: ignore[arg-type]
    assert controls.calls == count + 1
    expected = (kind, 7 if kind is SystemExit else None)
    assert controller.control() == expected
    assert thread._args == () and thread._kwargs == {} and thread._target is None


def test_candidate_swap_zeroes_original_moved_stdin_before_overwrite() -> None:
    class CandidateProbe(quarantine_module.CandidateCleanupMixin):
        pass

    probe = CandidateProbe()
    retained = bytearray(b"traceback-sentinel-candidate-stdin")
    original = quarantine_module._CandidateAuthority(FakeProcess())
    replacement = quarantine_module._CandidateAuthority(FakeProcess())
    original.input = retained
    replacement.input = bytearray(b"replacement")
    probe._input = original.input
    probe._load_candidate(replacement)
    assert retained == bytearray() and original.input == bytearray()
    assert probe._input is replacement.input and probe._input == bytearray(b"replacement")


def test_environment_pair_subclass_cannot_spoof_canonical_safe_values() -> None:
    class SpoofedPair(tuple[str, str]):
        def __new__(cls) -> SpoofedPair:
            return tuple.__new__(cls, ("LD_PRELOAD", "/tmp/attacker.so"))

        def __eq__(self, other: object) -> bool:
            return other == ("LANG", "C")

        def __hash__(self) -> int:
            return hash(("LANG", "C"))

    spoofed = SpoofedPair()
    environment = (spoofed, ("LC_ALL", "C"))
    assert tuple(spoofed) == ("LD_PRELOAD", "/tmp/attacker.so")
    assert environment == (("LANG", "C"), ("LC_ALL", "C"))
    assert hash(spoofed) == hash(("LANG", "C"))
    request = docker_request()
    object.__setattr__(request, "environment", environment)
    assert validate_request(request) is None
    harness = Harness()
    with pytest.raises(CoturnSubprocessError, match=r"request is invalid$"):
        harness.runner().start_attached(request)
    assert harness.calls == []


def test_main_group_absence_revokes_numeric_pgid_before_reuse() -> None:
    process = FakeProcess()
    harness = harness_with_tracking(process)
    answers = iter((False, True, True, False))
    probes: list[int] = []

    def recycled_group(pgid: int) -> bool:
        probes.append(pgid)
        return next(answers)

    runner = harness.runner(process_group_exists=recycled_group)
    assert runner.run(docker_request()).returncode == 0
    assert probes == [process._pid]
    assert harness.signals == [] and runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_contradictory_candidates_revoke_each_absent_numeric_pgid() -> None:
    registered = FakeProcess()
    returned = FakeProcess()
    returned._pid = 4343
    harness = harness_with_tracking(registered, returned)
    answers = {
        registered._pid: iter((False, True, True, False)),
        returned._pid: iter((False, True)),
    }
    probes: list[int] = []

    def recycled_group(pgid: int) -> bool:
        probes.append(pgid)
        return next(answers[pgid])

    def contradictory(argv: tuple[str, ...], **options: object) -> FakeProcess:
        register = options.pop("owner_register")
        assert callable(register)
        register(registered)
        harness.calls.append((argv, options))
        return returned

    runner = harness.runner(
        popen_factory=contradictory,
        process_group_exists=recycled_group,
    )
    with pytest.raises(CoturnSubprocessError, match=r"start identity is invalid$"):
        runner.start_attached(docker_request())
    assert probes.count(registered._pid) == 1
    assert probes.count(returned._pid) == 1
    assert harness.signals == [] and runner._slots == []
    for process in (registered, returned):
        assert process.stdin is None and process.stdout is None and process.stderr is None


def test_closed_selector_retry_reopens_state_and_closes_every_factory_result() -> None:
    process = FakeProcess(auto_exit=False, pipes_eof=False)
    harness = harness_with_tracking(process)
    calls = 0

    def selector_factory() -> FakeSelector:
        nonlocal calls
        calls += 1
        selector = FakeSelector(harness.plan)
        if calls == 1:
            selector.register = None  # type: ignore[method-assign]
        harness.selectors.append(selector)
        return selector

    runner = harness.runner(selector_factory=selector_factory)
    with pytest.raises(CoturnSubprocessError, match=r"start validation failed$"):
        runner.start_attached(docker_request())
    assert calls == 2 and len(harness.selectors) == 2
    assert all(selector.closed for selector in harness.selectors)
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_settle_true_closes_admission_before_public_return_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness()
    runner = harness.runner()
    at_return = threading.Event()
    release = threading.Event()
    settler: threading.Thread | None = None
    results: list[bool] = []

    def hold_settle_success() -> None:
        if threading.current_thread() is settler:
            at_return.set()
            release.wait(1.0)

    monkeypatch.setattr(boundary_module, "_boundary_success", hold_settle_success)
    settler = threading.Thread(target=lambda: results.append(runner.settle_owned()))
    settler.start()
    assert at_return.wait(1.0)
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request(stdin=b"traceback-sentinel-post-settle-start"))
    assert harness.calls == [] and runner._slots == []
    release.set()
    settler.join(1.0)
    assert not settler.is_alive() and results == [True]
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        runner.start_attached(docker_request())
    assert harness.calls == [] and runner._slots == []


def test_reaped_leader_never_signals_first_reused_group_observation() -> None:
    process = FakeProcess(auto_exit=True, group_alive=True, pipes_eof=True)
    process.term_exits = False
    harness = harness_with_tracking(process)
    answers = iter((True, False))
    probes: list[int] = []

    def recycled_group(pgid: int) -> bool:
        probes.append(pgid)
        return next(answers)

    runner = harness.runner(process_group_exists=recycled_group)
    assert runner.run(docker_request()).returncode == 0
    assert probes == [process._pid, process._pid]
    assert harness.signals == [] and runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_reaped_contradictory_candidates_never_signal_reused_groups() -> None:
    registered = FakeProcess()
    returned = FakeProcess()
    returned._pid = 4343
    harness = harness_with_tracking(registered, returned)
    answers = {
        registered._pid: iter((True, False)),
        returned._pid: iter((True, False)),
    }
    probes: list[int] = []

    def recycled_group(pgid: int) -> bool:
        probes.append(pgid)
        return next(answers[pgid])

    def contradictory(argv: tuple[str, ...], **options: object) -> FakeProcess:
        register = options.pop("owner_register")
        assert callable(register)
        register(registered)
        harness.calls.append((argv, options))
        return returned

    runner = harness.runner(
        popen_factory=contradictory,
        process_group_exists=recycled_group,
    )
    with pytest.raises(CoturnSubprocessError, match=r"start identity is invalid$"):
        runner.start_attached(docker_request())
    assert probes.count(registered._pid) == 2
    assert probes.count(returned._pid) == 2
    assert harness.signals == [] and runner._slots == []
    for process in (registered, returned):
        assert process.stdin is None and process.stdout is None and process.stderr is None


@pytest.mark.parametrize("path", ["child", "no-child", "candidates"])
def test_clean_receipt_is_staged_atomically_before_every_clean_path(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    registered = FakeProcess()
    returned = FakeProcess()
    harness = harness_with_tracking(registered, returned)
    if path == "no-child":
        registered._child_created = False
    if path == "candidates":

        def contradictory(argv: tuple[str, ...], **options: object) -> FakeProcess:
            register = options.pop("owner_register")
            assert callable(register)
            register(registered)
            harness.calls.append((argv, options))
            return returned

        runner = harness.runner(popen_factory=contradictory)
    else:
        runner = harness.runner()
    controls = FiniteControls(SystemExit, 65, code=7)
    observed: list[tuple[ControllerState, Lifecycle, object]] = []

    def inspect_staged_receipt() -> None:
        controller = runner._slots[0].controller
        observed.append((controller, controller._state, controller._receipt))
        controls()

    monkeypatch.setattr(state_module, "_clean_receipt_staged", inspect_staged_receipt)
    if path == "child":
        handle = runner.start_attached(docker_request())
        with pytest.raises(SystemExit) as captured:
            handle.collect(timeout_seconds=1.0)
    else:
        with pytest.raises(SystemExit) as captured:
            runner.start_attached(docker_request())
    assert captured.value.code == 7
    assert controls.calls == 66 and observed
    assert all(
        state is not Lifecycle.CLEAN and receipt is not None for _, state, receipt in observed
    )
    controller = observed[0][0]
    assert controller.lifecycle() is Lifecycle.CLEAN and controller.clean_joined()
    assert runner._slots == [] and harness.signals == []
    for process in (registered, returned):
        if path != "candidates" and process is returned:
            continue
        assert process.stdin is None and process.stdout is None and process.stderr is None


def test_terminal_controller_control_survives_latch_clear_recontrols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    handle = runner.start_attached(docker_request())
    assert _wait_until(lambda: handle._slot.controller.lifecycle() is Lifecycle.CLEAN)
    first = FiniteControls(KeyboardInterrupt, 1)
    later = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(supervisor_module, "_joined_receipt_published", first)
    monkeypatch.setattr(boundary_module, "_control_latch_cleared", later)
    with pytest.raises(KeyboardInterrupt) as captured:
        handle.poll()
    _assert_fresh_scrubbed(captured.value, handle, runner)
    assert first.calls == 1 and later.calls == 66
    assert handle._slot.controller.control() == (KeyboardInterrupt, None)
    assert handle._slot.controller.clean_joined()
    assert runner._slots == [] and runner._control_latch is None


def test_start_final_request_scrub_preserves_prior_controller_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Harness().runner()
    later = FiniteControls(SystemExit, 65, code=7)
    original_scrub = request_module.SupervisorRequest.scrub_all

    def fail_after_first_control(**values: object) -> object:
        controller = values["controller"]
        assert type(controller) is ControllerState
        controller.capture_control_signal((KeyboardInterrupt, None))
        raise RuntimeError("synthetic preparation failure")

    def controlled_scrub(self: request_module.SupervisorRequest) -> None:
        later()
        original_scrub(self)

    monkeypatch.setattr(facade_module, "prepare_supervisor", fail_after_first_control)
    monkeypatch.setattr(request_module.SupervisorRequest, "scrub_all", controlled_scrub)
    request = docker_request(stdin=b"traceback-sentinel-start-final-scrub")
    with pytest.raises(KeyboardInterrupt) as captured:
        runner.start_attached(request)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-start-final-scrub",),
    )
    assert later.calls == 66 and runner._slots == []


def test_constructor_cleanup_recontrols_preserve_first_and_scrub_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = harness_with_tracking(FakeProcess()).factory
    later = FiniteControls(SystemExit, 65, code=7)

    def first_control(**_values: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(facade_module, "SupervisorSeams", first_control)
    monkeypatch.setattr(facade_module, "_constructor_cleanup_entry", later)
    with pytest.raises(KeyboardInterrupt) as captured:
        facade_module.SubprocessCommandRunner(popen_factory=backend)
    _assert_fresh_scrubbed(captured.value, backend)
    assert later.calls == 66


def test_valid_chunk_publication_control_scrubs_raw_bytes_and_preserves_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FiniteControls(KeyboardInterrupt, 1)
    later = FiniteControls(SystemExit, 65, code=7)
    monkeypatch.setattr(values_module, "_chunk_stream_published", first)
    monkeypatch.setattr(values_module, "_chunk_scrub_entry", later)
    raw = b"traceback-sentinel-valid-chunk-publication"
    with pytest.raises(KeyboardInterrupt) as captured:
        SubprocessChunk("stdout", raw)
    _assert_fresh_scrubbed(
        captured.value,
        raw,
        secrets=("traceback-sentinel-valid-chunk-publication",),
    )
    assert first.calls == 1 and later.calls == 66


def test_collect_rechecks_sibling_quarantine_before_result_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    runner = harness_with_tracking(process).runner()
    handle = runner.start_attached(docker_request())
    sibling = SupervisorSlot(controller=ControllerState())
    assert sibling.controller.transition(Lifecycle.CLEANUP_READY)
    assert sibling.controller.transition(Lifecycle.SPAWNING)
    assert sibling.controller.transition(Lifecycle.QUARANTINED)

    def poison_at_publication() -> None:
        with runner._lock:
            runner._slots.append(sibling)

    monkeypatch.setattr(facade_module, "_collection_result_ready", poison_at_publication)
    with pytest.raises(CoturnSubprocessError, match=r"runner is poisoned$"):
        handle.collect(timeout_seconds=1.0)
    assert handle._slot.controller.clean_joined()
    assert handle._slot not in runner._slots and sibling in runner._slots
    sibling.close_admission()
    assert sibling.controller.transition(Lifecycle.VERIFYING)
    assert sibling.controller.complete_clean((), None)
    assert sibling.controller.mark_joined()
    runner._settled(sibling)
    assert runner._slots == []


def test_eof_streams_unregister_and_close_once_while_group_is_quarantined() -> None:
    process = FakeProcess(auto_exit=True, group_alive=True, pipes_eof=True)
    process.term_exits = False
    harness = harness_with_tracking(process)
    replacement_gone = threading.Event()

    def group_exists(_pgid: int) -> bool:
        return not replacement_gone.is_set()

    runner = harness.runner(process_group_exists=group_exists)
    handle = runner.start_attached(docker_request())
    assert _wait_until(
        lambda: handle._slot.controller.lifecycle() is Lifecycle.QUARANTINED,
        timeout=1.0,
    )
    assert process.stdout_pipe.read_count == 1
    assert process.stderr_pipe.read_count == 1
    threading.Event().wait(0.05)
    assert process.stdout_pipe.read_count == 1
    assert process.stderr_pipe.read_count == 1
    assert process.stdout is None and process.stderr is None
    assert all(not selector.mapping for selector in harness.selectors)
    assert harness.signals == []
    replacement_gone.set()
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == [] and all(selector.closed for selector in harness.selectors)


@pytest.mark.parametrize("payload_kind", ["empty-bytearray", "bytearray", "hostile-eq"])
def test_output_requires_exact_bytes_before_any_eof_comparison(payload_kind: str) -> None:
    class HostileEquality:
        compared = False

        def __eq__(self, _other: object) -> bool:
            self.compared = True
            return True

    hostile = HostileEquality()
    payload: object
    if payload_kind == "empty-bytearray":
        payload = bytearray()
    elif payload_kind == "bytearray":
        payload = bytearray(b"not-exact-bytes")
    else:
        payload = hostile
    process = FakeProcess(stdout=b"ready")
    process.stdout_pipe.read = lambda _maximum: payload  # type: ignore[method-assign]
    runner = harness_with_tracking(process).runner()
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$"):
        runner.run(docker_request())
    assert not hostile.compared
    assert len(runner._slots) == 1
    assert runner._slots[0].controller.failure() == "Coturn subprocess stream failed"
    process.stdout_pipe.read = lambda _maximum: b""  # type: ignore[method-assign]
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == []
    assert process.stdin is None and process.stdout is None and process.stderr is None


def test_spoofed_started_thread_cannot_clear_unconsumed_kernel_or_capacity() -> None:
    class SpoofedThread:
        def __init__(self, **values: object) -> None:
            self.target = values.get("target")
            self.args = values.get("args")

        @property
        def ident(self) -> int:
            return 1

        def start(self) -> None:
            return None

        def join(self, _timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    process = FakeProcess()
    harness = harness_with_tracking(process)
    calls = 0
    spoofed: list[SpoofedThread] = []

    def thread_factory(**values: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            thread = SpoofedThread(**values)
            spoofed.append(thread)
            return thread
        return threading.Thread(**values)  # type: ignore[arg-type]

    runner = harness.runner(thread_factory=thread_factory)
    request = docker_request(stdin=b"traceback-sentinel-spoofed-supervisor")
    with pytest.raises(CoturnSubprocessError, match=r"supervisor start failed$") as captured:
        runner.start_attached(request)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-spoofed-supervisor",),
    )
    assert calls == 1 and len(spoofed) == 1
    assert harness.calls == [] and runner._slots == []
    assert supervisor_module._KERNELS == {}
    assert runner.run(docker_request()).returncode == 0
    assert calls == 3 and len(harness.calls) == 1
    assert runner._slots == [] and supervisor_module._KERNELS == {}


def test_consume_after_receipt_timeout_retains_worker_until_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    harness = harness_with_tracking(process)
    permit_consume = threading.Event()
    kernel_taken = threading.Event()
    release_worker = threading.Event()
    wrapped_supervisor = False
    scrubbed: list[request_module.SupervisorRequest] = []
    original_scrub = request_module.SupervisorRequest.scrub_all

    def thread_factory(**values: object) -> threading.Thread:
        nonlocal wrapped_supervisor
        if values.get("name") == "coturn-subprocess-supervisor" and not wrapped_supervisor:
            wrapped_supervisor = True
            target = values["target"]
            args = values["args"]
            assert callable(target) and type(args) is tuple

            def delayed_target() -> None:
                permit_consume.wait(1.0)
                target(*args)

            values["target"] = delayed_target
            values["args"] = ()
        return threading.Thread(**values)  # type: ignore[arg-type]

    def race_before_cancel() -> None:
        permit_consume.set()
        assert kernel_taken.wait(1.0)

    def hold_consumed_kernel() -> None:
        kernel_taken.set()
        release_worker.wait(1.0)

    def record_scrub(self: request_module.SupervisorRequest) -> None:
        original_scrub(self)
        scrubbed.append(self)

    monkeypatch.setattr(supervisor_module, "_KERNEL_TAKE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        supervisor_module,
        "_supervisor_receipt_unproven",
        race_before_cancel,
    )
    monkeypatch.setattr(supervisor_module, "_kernel_taken", hold_consumed_kernel)
    monkeypatch.setattr(request_module.SupervisorRequest, "scrub_all", record_scrub)
    runner = harness.runner(thread_factory=thread_factory)
    request = docker_request(stdin=b"traceback-sentinel-consume-after-timeout")
    with pytest.raises(CoturnSubprocessError, match=r"cleanup is quarantined$") as captured:
        runner.start_attached(request)
    _assert_fresh_scrubbed(
        captured.value,
        request,
        runner,
        secrets=("traceback-sentinel-consume-after-timeout",),
    )
    assert kernel_taken.is_set() and len(runner._slots) == 1
    slot = runner._slots[0]
    assert slot.thread is not None and slot.thread.is_alive()
    assert slot.controller.lifecycle() is Lifecycle.QUARANTINED
    assert supervisor_module._KERNELS == {} and harness.calls == []
    assert scrubbed == []
    release_worker.set()
    assert runner.recover_quarantined(timeout_seconds=1.0)
    assert runner._slots == [] and supervisor_module._KERNELS == {}
    assert scrubbed and scrubbed[0].argv == () and scrubbed[0].environment == ()
    assert scrubbed[0].stdin == bytearray()
    assert runner.run(docker_request()).returncode == 0
    assert len(harness.calls) == 1 and runner._slots == []

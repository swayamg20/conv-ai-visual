"""Facade for the two-worker Coturn subprocess ownership protocol."""

from __future__ import annotations

import os
import selectors
import threading
import time

from scripts.voice_pipecat_e2e_coturn_host import CommandRequest, CommandResult
from scripts.voice_pipecat_e2e_coturn_subprocess_boundary import (
    RunnerBoundaryMixin,
    public_boundary,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_process import (
    Clock,
    GroupExists,
    GroupIdentity,
    GroupSignal,
    PopenFactory,
    SelectorFactory,
    SetBlocking,
    SupervisorSeams,
    SupervisorSlot,
    cancel_supervisor_slot,
    launch_supervisor,
    local_group_exists,
    local_group_identity,
    local_set_blocking,
    prepare_supervisor,
    registered_popen_factory,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_request import (
    valid_seconds,
    validate_request,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import ThreadFactory
from scripts.voice_pipecat_e2e_coturn_subprocess_state import ControllerState, Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    ControlSignal,
    CoturnSubprocessError,
    SubprocessChunk,
    control_signal,
    raise_control,
    raise_subprocess_error,
)

_READ_POLL_SECONDS = 0.05
_HANDLE_TOKEN = object()


class StreamingAttachedCommand:
    """Sanitized handle; raw process authority stays on the supervisor stack."""

    __slots__ = ("_maximum_output_bytes", "_runner", "_slot", "_timeout_seconds")

    def __init__(
        self,
        token: object,
        *,
        runner: SubprocessCommandRunner,
        slot: SupervisorSlot,
        timeout_seconds: float,
        maximum_output_bytes: int,
    ) -> None:
        if token is not _HANDLE_TOKEN:
            raise TypeError("Coturn attached subprocess is factory-owned")
        self._runner = runner
        self._slot = slot
        self._timeout_seconds = timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes

    @public_boundary("Coturn subprocess execution failed")
    def read_chunk(self, *, timeout_seconds: float) -> SubprocessChunk | None:
        self._synchronize_worker_control()
        if not valid_seconds(timeout_seconds, minimum=0.01):
            self._slot.controller.fail("Coturn subprocess read timeout is invalid")
            self._await_terminal()
            self._raise_outcome()
        deadline = time.monotonic() + timeout_seconds
        while True:
            self._runner._assert_result_allowed(self._slot)
            chunk = self._slot.controller.pop_chunk()
            if chunk is not None:
                return chunk
            state = self._slot.controller.lifecycle()
            if state in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
                self._raise_outcome()
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._wait_change(min(_READ_POLL_SECONDS, remaining))

    @property
    @public_boundary("Coturn subprocess execution failed")
    def drained(self) -> bool:
        self._synchronize_worker_control()
        self._runner._assert_result_allowed(self._slot)
        state = self._slot.controller.lifecycle()
        if state is Lifecycle.QUARANTINED:
            self._raise_outcome()
        if state is Lifecycle.CLEAN:
            self._join_clean()
            self._raise_failure_or_control()
            return self._slot.controller.chunk_count() == 0
        return False

    @public_boundary("Coturn subprocess execution failed")
    def poll(self) -> int | None:
        self._synchronize_worker_control()
        self._runner._assert_result_allowed(self._slot)
        state = self._slot.controller.lifecycle()
        if state is Lifecycle.QUARANTINED:
            self._raise_outcome()
        if state is Lifecycle.CLEAN:
            self._join_clean()
            self._raise_failure_or_control()
            return self._slot.controller.clean_returncode()
        return self._slot.controller.observed_returncode()

    @public_boundary("Coturn subprocess execution failed")
    def collect(self, *, timeout_seconds: float) -> CommandResult:
        self._synchronize_worker_control()
        self._runner._assert_result_allowed(self._slot)
        if not valid_seconds(timeout_seconds, minimum=0.1):
            self._slot.controller.fail("Coturn subprocess collection timeout is invalid")
            self._await_terminal()
            self._raise_outcome()
        deadline = time.monotonic() + timeout_seconds
        while self._slot.controller.lifecycle() not in {
            Lifecycle.CLEAN,
            Lifecycle.QUARANTINED,
        }:
            self._runner._assert_result_allowed(self._slot)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._slot.controller.fail("Coturn subprocess collection timed out")
                self._await_terminal()
                self._raise_outcome()
            self._wait_change(min(_READ_POLL_SECONDS, remaining))
        self._raise_outcome()
        returncode = self._slot.controller.clean_returncode()
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            raise_subprocess_error("Coturn subprocess result is invalid")
        result = CommandResult(returncode, b"", b"")
        _collection_result_ready()
        self._runner._assert_result_allowed(self._slot)
        return result

    @public_boundary("Coturn subprocess cleanup proof is invalid")
    def terminate(self) -> None:
        self._synchronize_worker_control()
        state = self._slot.controller.lifecycle()
        if state is Lifecycle.CLEAN:
            self._join_clean()
            self._raise_failure_or_control()
            return
        self._slot.controller.request_termination()
        self._await_terminal()
        self._raise_outcome(allow_termination=True)

    def __repr__(self) -> str:
        return "StreamingAttachedCommand()"

    def _await_terminal(self) -> None:
        while self._slot.controller.lifecycle() not in {
            Lifecycle.CLEAN,
            Lifecycle.QUARANTINED,
        }:
            self._wait_change(_READ_POLL_SECONDS)

    def _synchronize_worker_control(self) -> None:
        if self._slot.controller.control() is None:
            return
        state = self._slot.controller.lifecycle()
        if state not in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
            self._slot.controller.request_termination()
            self._await_terminal()
        self._raise_outcome(allow_termination=True)

    def _boundary_abort(
        self,
        *,
        control: ControlSignal | None,
        failure: str | None,
        uncertain: bool,
    ) -> ControlSignal | None:
        controller = self._slot.controller
        if control is not None:
            controller.capture_control_signal(control)
        del failure, uncertain
        if controller.lifecycle() not in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
            controller.request_termination()
            self._await_terminal()
        controller.clear_chunks()
        if controller.lifecycle() is Lifecycle.CLEAN:
            self._join_clean()
        stored = controller.control()
        return self._runner._consume_control_latch(stored)

    def _wait_change(self, timeout_seconds: float) -> None:
        try:
            self._slot.controller.wait_change(timeout_seconds)
        except (KeyboardInterrupt, SystemExit) as error:
            self._slot.controller.capture_control(error)
        except BaseException:
            self._slot.controller.fail("Coturn subprocess synchronization failed")

    def _join_clean(self) -> None:
        if not self._slot.join_if_clean():
            raise_subprocess_error("Coturn subprocess cleanup proof is invalid")
        self._runner._settled(self._slot)

    def _raise_outcome(self, *, allow_termination: bool = False) -> None:
        state = self._slot.controller.lifecycle()
        if state is Lifecycle.QUARANTINED or (
            state is not Lifecycle.CLEAN and self._slot.controller.poisoned()
        ):
            self._slot.controller.clear_chunks()
            self._raise_control_if_present()
            raise_subprocess_error("Coturn subprocess cleanup is quarantined")
        if state is not Lifecycle.CLEAN:
            raise_subprocess_error("Coturn subprocess cleanup proof is invalid")
        self._join_clean()
        self._raise_failure_or_control(allow_termination=allow_termination)

    def _raise_failure_or_control(self, *, allow_termination: bool = False) -> None:
        self._raise_control_if_present()
        failure = self._slot.controller.failure()
        if failure is not None:
            raise_subprocess_error(failure)
        if not allow_termination and not self._slot.controller.started():
            raise_subprocess_error("Coturn subprocess start failed")

    def _raise_control_if_present(self) -> None:
        control = self._slot.controller.control()
        if control is not None:
            raise_control(control)


class SubprocessCommandRunner(RunnerBoundaryMixin):
    """Own at most two controllers; settlement permanently closes admission."""

    __slots__ = ("_admission_open", "_control_latch", "_lock", "_seams", "_slots")

    def __init__(
        self,
        *,
        popen_factory: PopenFactory = registered_popen_factory,
        signal_process_group: GroupSignal = os.killpg,
        process_group_exists: GroupExists = local_group_exists,
        process_group_id: GroupIdentity = local_group_identity,
        thread_factory: ThreadFactory = threading.Thread,
        selector_factory: SelectorFactory = selectors.DefaultSelector,
        set_blocking: SetBlocking = local_set_blocking,
        clock: Clock = time.monotonic,
    ) -> None:
        control: ControlSignal | None = None
        failed = False
        seams: SupervisorSeams | None = None
        try:
            seams = SupervisorSeams(
                factory=popen_factory,
                signal_group=signal_process_group,
                group_exists=process_group_exists,
                group_identity=process_group_id,
                selector_factory=selector_factory,
                set_blocking=set_blocking,
                clock=clock,
                thread_factory=thread_factory,
            )
            lock = threading.Lock()
            slots: list[SupervisorSlot] = []
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
            failed = True
        except BaseException:
            failed = True
        if not failed and seams is not None:
            self._seams = seams
            self._lock = lock
            self._slots = slots
            self._admission_open = True
            self._control_latch = None
            return
        while True:
            try:
                _constructor_cleanup_entry()
                seams = None
                popen_factory = None  # type: ignore[assignment]
                signal_process_group = None  # type: ignore[assignment]
                process_group_exists = None  # type: ignore[assignment]
                process_group_id = None  # type: ignore[assignment]
                thread_factory = None  # type: ignore[assignment]
                selector_factory = None  # type: ignore[assignment]
                set_blocking = None  # type: ignore[assignment]
                clock = None  # type: ignore[assignment]
                self = None  # type: ignore[assignment]
                break
            except (KeyboardInterrupt, SystemExit) as error:
                if control is None:
                    control = control_signal(error)
            except BaseException:
                continue
        if control is not None:
            raise_control(control)
        raise_subprocess_error("Coturn subprocess executor is invalid")

    @public_boundary("Coturn subprocess execution failed")
    def run(self, request: CommandRequest) -> CommandResult:
        handle: StreamingAttachedCommand | None = None
        control: ControlSignal | None = None
        failure: str | None = None
        stdout = bytearray()
        stderr = bytearray()
        chunk: SubprocessChunk | None = None
        status: CommandResult | None = None
        result: CommandResult | None = None
        try:
            handle = self.start_attached(request)
            request = None  # type: ignore[assignment]
            while not handle.drained:
                chunk = handle.read_chunk(timeout_seconds=_READ_POLL_SECONDS)
                if chunk is not None:
                    (stdout if chunk.stream == "stdout" else stderr).extend(chunk.data)
            status = handle.collect(timeout_seconds=handle._timeout_seconds)
            result = CommandResult(status.returncode, bytes(stdout), bytes(stderr))
            _result_constructed()
            self._assert_result_allowed(handle._slot)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except CoturnSubprocessError as error:
            failure = str(error)
        except BaseException:
            failure = "Coturn subprocess execution failed"
        request = None  # type: ignore[assignment]
        if (control is not None or failure is not None) and handle is not None:
            try:
                handle.terminate()
            except (KeyboardInterrupt, SystemExit) as error:
                if control is None:
                    control = control_signal(error)
            except BaseException:
                pass
        stdout.clear()
        stderr.clear()
        chunk = None
        status = None
        handle = None
        if control is not None:
            result = None
            raise_control(control)
        if failure is not None or result is None:
            result = None
            raise_subprocess_error(failure or "Coturn subprocess result is invalid")
        return result

    @public_boundary("Coturn subprocess supervisor start failed")
    def start_attached(self, request: CommandRequest) -> StreamingAttachedCommand:
        owned_request = None
        slot: SupervisorSlot | None = None
        launch = None
        handle: StreamingAttachedCommand | None = None
        try:
            owned_request = validate_request(request)
            request = None  # type: ignore[assignment]
            if owned_request is None:
                raise_subprocess_error("Coturn subprocess request is invalid")
            timeout = owned_request.timeout_seconds
            maximum = owned_request.maximum_output_bytes
            controller = ControllerState()
            slot = SupervisorSlot(controller=controller)
            self._reserve_slot(slot)
            launch = prepare_supervisor(
                request=owned_request,
                controller=controller,
                seams=self._seams,
                slot=slot,
            )
            owned_request = None
            launched = launch_supervisor(launch, slot)
            launch = None
            _launch_returned()
            if not launched:
                control = controller.control()
                if control is not None:
                    raise_control(control)
                raise_subprocess_error("Coturn subprocess supervisor start failed")
            while slot.controller.lifecycle() not in {
                Lifecycle.ACTIVE,
                Lifecycle.CLEAN,
                Lifecycle.QUARANTINED,
            }:
                self._wait_slot(slot)
            if (
                slot.controller.control() is not None
                and slot.controller.lifecycle() is Lifecycle.ACTIVE
            ):
                slot.controller.request_termination()
                self._await_slot_terminal(slot)
            if slot.controller.lifecycle() is not Lifecycle.ACTIVE:
                self._raise_slot_outcome(slot)
            handle = StreamingAttachedCommand(
                _HANDLE_TOKEN,
                runner=self,
                slot=slot,
                timeout_seconds=timeout,
                maximum_output_bytes=maximum,
            )
            _handle_constructed()
            if not controller.allow_active_io():
                controller.request_termination()
                self._await_slot_terminal(slot)
                self._raise_slot_outcome(slot)
            _active_io_released()
            return handle
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
            self._latch_control(control)
            if slot is not None:
                slot.controller.capture_control_signal(control)
            stored = self._cleanup_failed_start(slot)
            self._latch_control(stored)
            raise_control(stored or control)
        except BaseException:
            stored = self._cleanup_failed_start(slot)
            if stored is not None:
                self._latch_control(stored)
                raise_control(stored)
            if slot is not None and slot.controller.poisoned():
                raise_subprocess_error("Coturn subprocess cleanup is quarantined")
            raise
        finally:
            request = None  # type: ignore[assignment]
            handle = None
            while owned_request is not None:
                try:
                    owned_request.scrub_all()
                    owned_request = None
                except (KeyboardInterrupt, SystemExit) as error:
                    self._latch_control(control_signal(error))
                except BaseException:
                    continue
            owned_request = None
            launch = None

    @public_boundary("Coturn subprocess synchronization failed")
    def recover_quarantined(self, *, timeout_seconds: float = 2.0) -> bool:
        if not valid_seconds(timeout_seconds, minimum=0.1):
            raise_subprocess_error("Coturn subprocess recovery timeout is invalid")
        deadline = time.monotonic() + timeout_seconds
        first_control: ControlSignal | None = None
        pending: list[SupervisorSlot] = []
        while True:
            with self._lock:
                pending.extend(
                    slot
                    for slot in self._slots
                    if slot.controller.poisoned() and slot not in pending
                )
            if not pending:
                break
            for slot in tuple(pending):
                known_control = slot.controller.control()
                first_control = first_control or known_control
                if slot.controller.lifecycle() is Lifecycle.CLEAN:
                    joined = slot.join_if_clean()
                    if known_control is None:
                        first_control = first_control or slot.controller.control()
                    if joined:
                        self._settled(slot)
                        if known_control is None:
                            first_control = first_control or slot.controller.control()
                        pending.remove(slot)
                        continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if first_control is not None:
                        raise_control(first_control)
                    return False
                try:
                    slot.controller.wait_change(min(_READ_POLL_SECONDS, remaining))
                except (KeyboardInterrupt, SystemExit) as error:
                    signal_value = control_signal(error)
                    slot.controller.capture_control_signal(signal_value)
                    first_control = first_control or slot.controller.control() or signal_value
            if time.monotonic() >= deadline:
                if first_control is not None:
                    raise_control(first_control)
                return False
        if first_control is not None:
            raise_control(first_control)
        return True

    @public_boundary("Coturn subprocess cleanup proof is invalid")
    def settle_owned(self) -> bool:
        first_control: ControlSignal | None = None
        with self._lock:
            self._admission_open = False
            slots = tuple(self._slots)
        for slot in slots:
            first_control = first_control or slot.controller.control()
            if slot.thread is None and slot.cancel_admission():
                first_control = first_control or slot.controller.control()
                continue
            cancel_supervisor_slot(slot)
            first_control = first_control or slot.controller.control()
            if slot.thread is None:
                if slot.controller.clean_joined():
                    self._settled(slot)
                elif (
                    slot.controller.lifecycle() is Lifecycle.REGISTERED
                    and slot.pending_launch() is None
                ):
                    self._drop_reservation(slot)
                first_control = first_control or slot.controller.control()
                continue
            state = slot.controller.lifecycle()
            if state not in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
                slot.controller.request_termination()
                self._await_slot_terminal(slot)
            first_control = first_control or slot.controller.control()
            if slot.controller.lifecycle() is Lifecycle.CLEAN:
                joined = slot.join_if_clean()
                first_control = first_control or slot.controller.control()
                if joined:
                    self._settled(slot)
            first_control = first_control or slot.controller.control()
        with self._lock:
            settled = len(self._slots) == 0
        if first_control is not None:
            raise_control(first_control)
        return settled

    def _reserve_slot(self, slot: SupervisorSlot) -> None:
        controller = slot.controller
        try:
            with self._lock:
                prior_control = self._discard_joined_locked()
                if prior_control is not None:
                    raise_control(prior_control)
                if not self._admission_open:
                    raise_subprocess_error("Coturn subprocess runner is poisoned")
                if any(item.controller.poisoned() for item in self._slots):
                    raise_subprocess_error("Coturn subprocess runner is poisoned")
                if len(self._slots) >= 2:
                    raise_subprocess_error("Coturn subprocess command limit exceeded")
                self._slots.append(slot)
                _reservation_appended()
            _slot_reserved()
            if slot.admission_cancelled():
                stored = controller.control()
                if stored is not None:
                    raise_control(stored)
                raise_subprocess_error("Coturn subprocess start failed")
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
            slot.close_admission()
            self._drop_reservation(slot)
            stored = controller.control()
            raise_control(stored or control_signal(error))
        except BaseException:
            slot.close_admission()
            self._drop_reservation(slot)
            stored = controller.control()
            if stored is not None:
                raise_control(stored)
            raise

    def _drop_reservation(self, slot: SupervisorSlot) -> None:
        try: return self._drop_reservation_authority(slot, _reservation_rollback_entry, _reservation_dropped)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            slot.controller.capture_control(error)
        except BaseException:
            pass
        self._drop_reservation_authority(
            slot,
            _reservation_rollback_entry,
            _reservation_dropped,
        )

    def _cleanup_failed_start(self, slot: SupervisorSlot | None) -> ControlSignal | None:
        if slot is None:
            return None
        with self._lock:
            if slot not in self._slots:
                return slot.controller.control()
        slot.close_admission()
        cancel_supervisor_slot(slot)
        if slot.thread is None:
            self._drop_reservation(slot)
            return slot.controller.control()
        slot.controller.request_termination()
        self._await_slot_terminal(slot)
        if slot.controller.lifecycle() is Lifecycle.CLEAN:
            joined = slot.join_if_clean()
            stored = slot.controller.control()
            if joined:
                self._settled(slot)
            return stored or slot.controller.control()
        return slot.controller.control()

    def _wait_slot(self, slot: SupervisorSlot) -> None:
        try:
            slot.controller.wait_change(_READ_POLL_SECONDS)
        except (KeyboardInterrupt, SystemExit) as error:
            slot.controller.capture_control(error)
        except BaseException:
            slot.controller.fail("Coturn subprocess synchronization failed")

    def _await_slot_terminal(self, slot: SupervisorSlot) -> None:
        while slot.controller.lifecycle() not in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
            self._wait_slot(slot)

    def _raise_slot_outcome(self, slot: SupervisorSlot) -> None:
        controller = slot.controller
        state = controller.lifecycle()
        if state is Lifecycle.QUARANTINED or (
            state is not Lifecycle.CLEAN and controller.poisoned()
        ):
            controller.clear_chunks()
            control = controller.control()
            if control is not None:
                raise_control(control)
            raise_subprocess_error("Coturn subprocess cleanup is quarantined")
        if state is not Lifecycle.CLEAN or not slot.join_if_clean():
            raise_subprocess_error("Coturn subprocess cleanup proof is invalid")
        self._settled(slot)
        control = controller.control()
        if control is not None:
            raise_control(control)
        failure = controller.failure()
        if failure is not None:
            raise_subprocess_error(failure)
        if not controller.started():
            raise_subprocess_error("Coturn subprocess start failed")

    def __repr__(self) -> str:
        return "SubprocessCommandRunner()"


def _noop_seam() -> None: ...  # fmt: skip


_result_constructed = _noop_seam
_slot_reserved = _noop_seam
_reservation_appended = _noop_seam
_reservation_dropped = _noop_seam
_reservation_rollback_entry = _noop_seam
_launch_returned = _noop_seam
_handle_constructed = _noop_seam
_active_io_released = _noop_seam
_collection_result_ready = _noop_seam
_constructor_cleanup_entry = _noop_seam


__all__ = [
    "CoturnSubprocessError",
    "StreamingAttachedCommand",
    "SubprocessChunk",
    "SubprocessCommandRunner",
]

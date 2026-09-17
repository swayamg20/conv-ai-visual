"""Sole post-spawn owner for one bounded Coturn helper process."""

from __future__ import annotations

import math
import signal
import threading
from typing import BinaryIO

from scripts.voice_pipecat_e2e_coturn_subprocess_process_io import (
    SelectorFactory,
    SelectorLike,
    SupervisorIOMixin,
    local_group_exists,
    local_group_identity,
    local_set_blocking,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_quarantine import (
    CandidateCleanupMixin,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_request import SupervisorRequest
from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import (
    PopenFactory,
    ProcessLike,
    registered_popen_factory,
    run_spawn_owner,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_state import Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_supervisor import (
    Clock,
    GroupExists,
    GroupIdentity,
    GroupSignal,
    SetBlocking,
    SupervisorKernel,
    SupervisorLaunch,
    SupervisorSeams,
    SupervisorSlot,
    cancel_supervisor_launch,
    cancel_supervisor_slot,
    prepare_supervisor,
    start_supervisor_thread,
    take_supervisor_kernel,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    KILL_VERIFICATION_SECONDS,
    TERMINATION_GRACE_SECONDS,
)

_POLL_SECONDS = 0.02
_QUARANTINE_RETRY_SECONDS = 0.05


def _supervisor_entry(token: object) -> None:
    kernel = take_supervisor_kernel(token)
    if kernel is None:
        return
    supervisor = _Supervisor(kernel)
    kernel = None  # type: ignore[assignment]
    supervisor.run()


def launch_supervisor(launch: SupervisorLaunch, slot: SupervisorSlot) -> bool:
    return start_supervisor_thread(launch, slot, _supervisor_entry)


class _Supervisor(CandidateCleanupMixin, SupervisorIOMixin):
    """All raw child authority remains on this sole worker's object graph."""

    __slots__ = (
        "_controller",
        "_deadline",
        "_eof",
        "_group_absent",
        "_input",
        "_input_offset",
        "_lifetime_chunks",
        "_maximum_output",
        "_output_bytes",
        "_pgid",
        "_pid",
        "_poll_seconds",
        "_process",
        "_quarantine_retry_seconds",
        "_registered",
        "_request",
        "_returncode",
        "_seams",
        "_selector",
        "_selector_closed",
        "_start_time",
        "_streams",
        "_timeout",
    )

    def __init__(self, kernel: SupervisorKernel) -> None:
        self._controller = kernel.controller
        self._seams = kernel.seams
        self._request = kernel.request
        kernel.request = None
        self._process: ProcessLike | None = None
        self._pid: int | None = None
        self._pgid: int | None = None
        self._group_absent = False
        self._poll_seconds = _POLL_SECONDS
        self._quarantine_retry_seconds = _QUARANTINE_RETRY_SECONDS
        self._registered: set[str] = set()
        self._streams: dict[str, BinaryIO | None] = {
            "stdin": None,
            "stdout": None,
            "stderr": None,
        }
        self._selector: SelectorLike | None = None
        self._selector_closed = False
        self._eof: set[str] = set()
        self._input = bytearray()
        self._input_offset = 0
        self._timeout = 0.1
        self._maximum_output = 1
        self._output_bytes = 0
        self._lifetime_chunks = 0
        self._returncode: int | None = None
        self._start_time: float | None = None
        self._deadline: float | None = None

    def run(self) -> None:
        if not self._controller.transition(Lifecycle.CLEANUP_READY):
            if self._controller.lifecycle() is Lifecycle.QUARANTINED:
                self._finish_no_child()
                return
            self._controller.fail("Coturn subprocess supervisor state is invalid")
            self._scrub_request()
            return
        request = self._request
        if request is None or not self._controller.transition(Lifecycle.SPAWNING):
            self._controller.fail("Coturn subprocess start failed")
            self._finish_no_child()
            return
        spawn_start = self._now()
        if spawn_start is None:
            self._enter_quarantine()
            spawn_deadline = 0.0
        else:
            spawn_deadline = spawn_start + request.timeout_seconds
        mailbox = run_spawn_owner(
            request=request,
            factory=self._seams.factory,
            controller=self._controller,
            thread_factory=self._seams.thread_factory,
            deadline=spawn_deadline,
            clock=self._seams.clock,
        )
        spawn_start = None
        spawn_deadline = None
        self._take_runtime_request(request)
        self._request = None
        if mailbox.control is not None:
            self._controller.capture_control_signal(mailbox.control)
        coherent = (
            mailbox.registered is not None
            and mailbox.returned is mailbox.registered
            and not mailbox.failed
            and mailbox.control is None
        )
        candidates = mailbox.take_candidates()
        mailbox.scrub()
        mailbox = None  # type: ignore[assignment]
        request = None
        if not candidates:
            if self._controller.control() is None:
                self._controller.fail("Coturn subprocess start failed")
            self._finish_no_child()
            return
        if len(candidates) != 1:
            self._controller.fail("Coturn subprocess start identity is invalid")
            self._quarantine_unowned(candidates)
            return
        self._process = candidates[0]
        candidates = ()
        if self._known_no_child():
            self._controller.fail("Coturn subprocess start failed")
            if self._close_partial_process():
                self._finish_no_child()
            else:
                self._enter_quarantine()
                self._partial_no_child_loop()
            return
        if not coherent:
            self._controller.fail("Coturn subprocess start failed")
        if not self._inspect_process():
            self._enter_quarantine()
            self._quarantine_loop()
            return
        if not self._controller.transition(Lifecycle.OWNED):
            self._enter_quarantine()
            self._quarantine_loop()
            return
        if not self._prepare_io(require_stdin=True):
            self._controller.fail("Coturn subprocess start validation failed")
            self._begin_cleanup()
            self._cleanup_loop()
            return
        if not coherent or self._controller.termination_requested():
            self._begin_cleanup()
            self._cleanup_loop()
            return
        self._start_time = self._now()
        if self._start_time is None or not self._controller.transition(Lifecycle.ACTIVE):
            self._controller.fail("Coturn subprocess start validation failed")
            self._begin_cleanup()
            self._cleanup_loop()
            return
        if not self._await_active_io():
            if self._controller.lifecycle() is Lifecycle.ACTIVE:
                self._begin_cleanup()
            self._cleanup_loop()
            return
        self._active_loop()

    def _await_active_io(self) -> bool:
        while self._controller.lifecycle() is Lifecycle.ACTIVE:
            if self._controller.termination_requested():
                return False
            if self._controller.active_io_ready():
                return True
            current = self._now()
            if current is None or self._start_time is None:
                self._controller.fail("Coturn subprocess clock failed")
                return False
            if current - self._start_time >= self._timeout:
                self._controller.fail("Coturn subprocess timed out")
                return False
            try:
                self._controller.wait_change(self._poll_seconds)
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
            except BaseException:
                self._controller.fail("Coturn subprocess synchronization failed")
        return False

    def _active_loop(self) -> None:
        while self._controller.lifecycle() is Lifecycle.ACTIVE:
            if self._controller.termination_requested():
                self._begin_cleanup()
                break
            current = self._now()
            if current is None or self._start_time is None:
                self._controller.fail("Coturn subprocess clock failed")
                self._begin_cleanup()
                break
            if current - self._start_time >= self._timeout:
                self._controller.fail("Coturn subprocess timed out")
                self._begin_cleanup()
                break
            self._io_step(publish=True)
            self._reap_step()
            if self._returncode is not None:
                if not self._controller.transition(Lifecycle.DRAINING):
                    self._controller.fail("Coturn subprocess supervisor state is invalid")
                    self._begin_cleanup()
                break
        if self._controller.lifecycle() is Lifecycle.DRAINING:
            self._draining_loop()
        elif self._controller.lifecycle() is not Lifecycle.CLEAN:
            self._cleanup_loop()

    def _draining_loop(self) -> None:
        while self._controller.lifecycle() is Lifecycle.DRAINING:
            if self._controller.termination_requested():
                self._begin_cleanup()
                break
            current = self._now()
            if current is None or self._start_time is None:
                self._controller.fail("Coturn subprocess clock failed")
                self._begin_cleanup()
                break
            if current - self._start_time >= self._timeout:
                self._controller.fail("Coturn subprocess timed out")
                self._begin_cleanup()
                break
            self._io_step(publish=True)
            self._reap_step()
            if self._io_drained():
                exists = self._group_exists()
                if exists is False:
                    self._controller.transition(Lifecycle.VERIFYING)
                    self._verification_loop()
                    return
                self._begin_cleanup()
                break
        self._cleanup_loop()

    def _begin_cleanup(self) -> None:
        state = self._controller.lifecycle()
        if state in {Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
            return
        if self._controller.termination_requested() or self._controller.failure() is not None:
            self._controller.clear_chunks()
        self._close_stream("stdin", require_eof=False)
        self._reap_step()
        if state is not Lifecycle.TERM_GRACE:
            self._controller.transition(Lifecycle.TERM_GRACE)
        self._signal(signal.SIGTERM)
        current = self._now()
        self._deadline = None if current is None else current + TERMINATION_GRACE_SECONDS

    def _cleanup_loop(self) -> None:
        if self._controller.lifecycle() not in {
            Lifecycle.TERM_GRACE,
            Lifecycle.KILLING,
            Lifecycle.VERIFYING,
            Lifecycle.QUARANTINED,
        }:
            self._begin_cleanup()
        while self._controller.lifecycle() is Lifecycle.TERM_GRACE:
            self._io_step(publish=False)
            self._reap_step()
            current = self._now()
            if current is None or self._deadline is None or current >= self._deadline:
                exists = self._group_exists()
                if exists is False:
                    self._controller.transition(Lifecycle.VERIFYING)
                    self._deadline = current
                    break
                self._controller.transition(Lifecycle.KILLING)
                self._signal(signal.SIGKILL)
                current = self._now()
                self._deadline = None if current is None else current + KILL_VERIFICATION_SECONDS
                self._controller.transition(Lifecycle.VERIFYING)
                break
        self._verification_loop()

    def _verification_loop(self) -> None:
        while self._controller.lifecycle() is Lifecycle.VERIFYING:
            if self._verification_step():
                self._finish_child()
                return
            current = self._now()
            if current is None or self._deadline is None or current >= self._deadline:
                self._enter_quarantine()
                break
        if self._controller.lifecycle() is Lifecycle.QUARANTINED:
            self._quarantine_loop()

    def _quarantine_loop(self) -> None:
        while self._controller.lifecycle() is Lifecycle.QUARANTINED:
            if self._verification_step():
                self._finish_child()
                return
            threading.Event().wait(_QUARANTINE_RETRY_SECONDS)

    def _verification_step(self) -> bool:
        if (
            self._controller.termination_requested()
            or self._controller.failure() is not None
            or self._controller.lifecycle() is Lifecycle.QUARANTINED
        ):
            self._controller.clear_chunks()
        if self._pgid is None and not self._group_absent:
            if not self._inspect_process():
                return False
        if not self._prepare_io(require_stdin=self._streams["stdin"] is not None):
            return False
        self._close_stream("stdin", require_eof=False)
        self._reap_step()
        exists = self._group_exists()
        if exists is not False:
            self._signal(signal.SIGKILL)
        self._io_step(publish=False)
        self._reap_step()
        exists = self._group_exists()
        if exists is not False or self._returncode is None or not self._io_drained():
            return False
        close_ok = all(self._close_stream(name, require_eof=True) for name in ("stdout", "stderr"))
        close_ok = self._close_stream("stdin", require_eof=False) and close_ok
        close_ok = self._close_selector() and close_ok
        if not close_ok or self._group_exists() is not False:
            return False
        close_ok = self._scrub_process()
        self._input.clear()
        return close_ok

    def _signal(self, value: int) -> bool | None:
        pgid = self._pgid
        if self._returncode is not None or self._group_absent or pgid is None:
            return False
        while True:
            try:
                self._seams.signal_group(pgid, value)
                return True
            except ProcessLookupError:
                self._group_absent = True
                self._pgid = None
                return None
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
            except BaseException:
                return False

    def _io_drained(self) -> bool:
        return (
            self._returncode is not None
            and self._streams["stdin"] is None
            and {"stdout", "stderr"}.issubset(self._eof)
        )

    def _close_partial_process(self) -> bool:
        process = self._process
        if process is None:
            return True
        success = True
        for name in ("stdin", "stdout", "stderr"):
            stream = self._control_retry(lambda name=name: getattr(process, name, None), None)
            closed = stream is None
            if stream is not None:
                try:
                    stream.close()
                    closed = True
                except (KeyboardInterrupt, SystemExit) as error:
                    self._controller.capture_control(error)
                    success = False
                except BaseException:
                    success = False
            if closed:
                try:
                    setattr(process, name, None)
                except (KeyboardInterrupt, SystemExit) as error:
                    self._controller.capture_control(error)
                    success = False
                except BaseException:
                    success = False
        try:
            process.args = ()
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            success = False
        except BaseException:
            success = False
        if success:
            self._process = None
        return success

    def _partial_no_child_loop(self) -> None:
        while self._controller.lifecycle() is Lifecycle.QUARANTINED:
            if self._close_partial_process():
                self._finish_no_child()
                return
            threading.Event().wait(_QUARANTINE_RETRY_SECONDS)

    def _enter_quarantine(self) -> None:
        self._controller.clear_chunks()
        if self._controller.lifecycle() is not Lifecycle.QUARANTINED:
            self._controller.transition(Lifecycle.QUARANTINED)

    def _finish_no_child(self) -> None:
        self._scrub_request()
        if self._controller.lifecycle() is Lifecycle.QUARANTINED:
            self._controller.transition(Lifecycle.VERIFYING)
        if not self._controller.complete_clean((), None):
            self._enter_quarantine()

    def _finish_child(self) -> None:
        returncode = self._returncode
        if type(returncode) is not int:
            self._enter_quarantine()
            return
        self._scrub_request()
        if self._controller.lifecycle() is Lifecycle.QUARANTINED:
            self._controller.transition(Lifecycle.VERIFYING)
        if not self._controller.complete_clean((returncode,), 0):
            self._enter_quarantine()

    def _take_runtime_request(self, request: SupervisorRequest) -> None:
        self._input = request.stdin
        request.stdin = bytearray()
        self._timeout = request.timeout_seconds
        self._maximum_output = request.maximum_output_bytes
        request.scrub_all()

    def _scrub_request(self) -> None:
        request = self._request
        self._request = None
        if request is not None:
            request.scrub_all()
        self._input.clear()

    def _now(self) -> float | None:
        while True:
            try:
                value = self._seams.clock()
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
                continue
            except BaseException:
                return None
            if type(value) not in {int, float}:
                return None
            try:
                normalized = float(value)
                finite = math.isfinite(normalized)
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
                continue
            except BaseException:
                return None
            return normalized if finite else None


__all__ = [
    "Clock",
    "GroupExists",
    "GroupIdentity",
    "GroupSignal",
    "PopenFactory",
    "SelectorFactory",
    "SelectorLike",
    "SetBlocking",
    "SupervisorLaunch",
    "SupervisorSeams",
    "SupervisorSlot",
    "cancel_supervisor_launch",
    "cancel_supervisor_slot",
    "launch_supervisor",
    "local_group_exists",
    "local_group_identity",
    "local_set_blocking",
    "prepare_supervisor",
    "registered_popen_factory",
]

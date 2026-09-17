"""Fixed local process operations for the sole relay Linux build worker."""

from __future__ import annotations

import math
import os
import signal
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerController,
)

_TERM_GRACE_SECONDS = 2.0
_KILL_VERIFY_SECONDS = 2.0
_HANDLE_BITS = {"stdin": 1, "stdout": 2, "stderr": 4}
_ALL_HANDLES = 7


def _local_monotonic() -> float:
    return time.monotonic()


def _local_process_pid(process: object) -> object:
    return process.pid  # type: ignore[attr-defined]


def _local_process_poll(process: object) -> object:
    return process.poll()  # type: ignore[attr-defined]


def _local_process_returncode(process: object) -> object:
    return process.returncode  # type: ignore[attr-defined]


def _local_process_wait(process: object) -> object:
    return process.wait(timeout=0.0)  # type: ignore[attr-defined]


def _local_process_group(pid: int) -> object:
    return os.getpgid(pid)


def _local_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _local_signal_group(pgid: int, value: int) -> bool:
    try:
        os.killpg(pgid, value)
    except ProcessLookupError:
        return False
    return True


def _local_process_handle(process: object, name: str) -> object:
    return getattr(process, name)


def _local_close_handle(handle: object) -> object:
    return handle.close()  # type: ignore[attr-defined]


def _local_handle_closed(handle: object) -> object:
    return handle.closed  # type: ignore[attr-defined]


def _signal_checkpoint(_kind: str, _stage: str) -> None:
    """Deterministic cut seam around durable signal facts."""


def _handle_checkpoint(_name: str, _stage: str) -> None:
    """Deterministic cut seam around durable close facts."""


class _LocalBuildProcessMixin:
    """Raw process state machine; the exact registered driver supplies state."""

    __slots__ = ()

    def _process_step(self) -> str:
        if self._state == "identity":
            return self._acquire_process_identity()
        if self._state == "closing":
            return self._close_process()
        if self._state not in {"running", "term-grace", "kill-wait", "observe"}:
            return "invalid"
        if self._returncode is None:
            observation = self._poll_process()
            if observation == "invalid":
                return self._quarantine()
            if observation == "running":
                return self._running_process_step()
        if not self._reaped and not self._wait_process():
            return "active"
        if not self._group_absent:
            exists = self._observe_group()
            if exists is None:
                return self._quarantine()
            if exists:
                object.__setattr__(self, "_state", "observe")
                return "waiting"
        return self._begin_close()

    def _acquire_process_identity(self) -> str:
        process = self._process
        if process is None:
            return self._quarantine()
        if self._pid is None:
            marker = self._begin_query("pid")
            value = _local_process_pid(process)
            if type(value) is not int or value <= 0:
                return self._quarantine()
            object.__setattr__(self, "_pid", value)
            self._complete_query(marker)
        if self._pgid is None:
            marker = self._begin_query("pgid")
            value = _local_process_group(self._pid)
            if type(value) is not int or value <= 0 or value != self._pid:
                return self._quarantine()
            object.__setattr__(self, "_pgid", value)
            self._complete_query(marker)
        controller = self._controller
        if type(controller) is not _BuildWorkerController:
            return self._quarantine()
        if controller._phase_value() == "spawning" and not controller._transition("running"):
            return self._quarantine()
        object.__setattr__(self, "_state", "running")
        return "active"

    def _poll_process(self) -> str:
        process = self._process
        if process is None:
            return "invalid"
        marker = self._begin_query("poll-returncode")
        polled = _local_process_poll(process)
        stored = _local_process_returncode(process)
        valid_running = polled is None and stored is None
        valid_exit = type(polled) is int and type(stored) is int and stored == polled
        if not (valid_running or valid_exit):
            return "invalid"
        if valid_exit:
            object.__setattr__(self, "_returncode", polled)
            object.__setattr__(self, "_pid", None)
            object.__setattr__(self, "_signal_revoked", True)
        self._complete_query(marker)
        return "exited" if valid_exit else "running"

    def _wait_process(self) -> bool:
        process = self._process
        expected = self._returncode
        if process is None or type(expected) is not int:
            return False
        marker = self._begin_query("wait-returncode")
        waited = _local_process_wait(process)
        stored = _local_process_returncode(process)
        if (
            type(waited) is not int
            or type(stored) is not int
            or waited != expected
            or stored != expected
        ):
            return False
        object.__setattr__(self, "_reaped", True)
        object.__setattr__(self, "_pid", None)
        object.__setattr__(self, "_signal_revoked", True)
        self._complete_query(marker)
        return True

    def _running_process_step(self) -> str:
        controller = self._controller
        if type(controller) is not _BuildWorkerController:
            return self._quarantine()
        now = self._clock_value()
        if now is None:
            return self._quarantine()
        cleanup = bool(
            controller._termination_requested()
            or controller._failed()
            or now >= controller._run_deadline
        )
        if not cleanup:
            return "waiting"
        if now >= controller._run_deadline:
            controller._fail()
        if self._state == "running" and not self._term_intended:
            return self._send_signal("term", now)
        if self._state == "term-grace":
            exists = self._observe_group()
            if exists is None:
                return self._quarantine()
            if not exists:
                object.__setattr__(self, "_state", "observe")
                return "waiting"
            deadline = self._term_deadline
            if type(deadline) is not float or now < deadline:
                return "waiting"
            if self._term_ambiguous or self._signal_revoked or not self._term_sent:
                object.__setattr__(self, "_state", "observe")
                return "waiting"
            return self._send_signal("kill", now)
        if self._state in {"kill-wait", "observe"}:
            exists = self._observe_group()
            if exists is None:
                return self._quarantine()
            if not exists:
                return "waiting"
            deadline = self._kill_deadline
            if self._state == "kill-wait" and type(deadline) is float and now >= deadline:
                controller._fail()
                controller._transition("quarantined")
                object.__setattr__(self, "_state", "observe")
            return "waiting"
        return "waiting"

    def _clock_value(self) -> float | None:
        marker = self._begin_query("clock")
        value = _local_monotonic()
        if type(value) is not float or not math.isfinite(value) or value < 0.0:
            object.__setattr__(self, "_signal_revoked", True)
            return None
        self._complete_query(marker)
        return value

    def _observe_group(self) -> bool | None:
        if self._group_absent:
            return False
        pgid = self._pgid
        if type(pgid) is not int or pgid <= 0:
            return None
        marker = self._begin_query("group-exists")
        exists = _local_group_exists(pgid)
        if type(exists) is not bool:
            return None
        if not exists:
            object.__setattr__(self, "_group_absent", True)
            object.__setattr__(self, "_pgid", None)
            object.__setattr__(self, "_signal_revoked", True)
        self._complete_query(marker)
        return exists

    def _send_signal(self, kind: str, now: float) -> str:
        pgid = self._pgid
        controller = self._controller
        if (
            kind not in {"term", "kill"}
            or type(pgid) is not int
            or pgid <= 0
            or type(controller) is not _BuildWorkerController
            or self._signal_revoked
        ):
            object.__setattr__(self, "_state", "observe")
            return "waiting"
        if kind == "kill" and (self._term_ambiguous or not self._term_sent):
            object.__setattr__(self, "_state", "observe")
            return "waiting"
        intended_name = "_term_intended" if kind == "term" else "_kill_intended"
        if getattr(self, intended_name):
            object.__setattr__(self, "_state", "observe")
            return "waiting"
        deadline = now + (_TERM_GRACE_SECONDS if kind == "term" else _KILL_VERIFY_SECONDS)
        if not math.isfinite(deadline):
            object.__setattr__(self, "_signal_revoked", True)
            return self._quarantine()
        object.__setattr__(self, intended_name, True)
        _signal_checkpoint(kind, "intent")
        object.__setattr__(self, f"_{kind}_deadline", deadline)
        _signal_checkpoint(kind, "deadline")
        phase = "term-grace" if kind == "term" else "killing"
        current = controller._phase_value()
        if current != phase and not controller._transition(phase):
            object.__setattr__(self, "_signal_revoked", True)
            return self._quarantine()
        _signal_checkpoint(kind, "phase")
        validation = self._pre_signal_validation()
        if validation != "running":
            object.__setattr__(self, "_state", "observe")
            return "active" if validation in {"exited", "absent"} else self._quarantine()
        marker = self._begin_query(f"{kind}-signal")
        sent = _local_signal_group(pgid, signal.SIGTERM if kind == "term" else signal.SIGKILL)
        if type(sent) is not bool:
            return self._quarantine()
        if sent:
            object.__setattr__(self, f"_{kind}_sent", True)
            state = "term-grace" if kind == "term" else "kill-wait"
        else:
            object.__setattr__(self, "_group_absent", True)
            object.__setattr__(self, "_pgid", None)
            object.__setattr__(self, "_signal_revoked", True)
            state = "observe"
        _signal_checkpoint(kind, "completion")
        object.__setattr__(self, "_state", state)
        _signal_checkpoint(kind, "state")
        self._complete_query(marker)
        _signal_checkpoint(kind, "marker-cleared")
        return "active"

    def _pre_signal_validation(self) -> str:
        observation = self._poll_process()
        if observation != "running":
            return observation
        pid = self._pid
        pgid = self._pgid
        if type(pid) is not int or pid <= 0 or type(pgid) is not int or pgid != pid:
            return "invalid"
        marker = self._begin_query("pre-signal-pgid")
        observed = _local_process_group(pid)
        if type(observed) is not int or observed <= 0 or observed != pid or observed != pgid:
            return "invalid"
        self._complete_query(marker)
        exists = self._observe_group()
        if exists is None:
            return "invalid"
        return "running" if exists else "absent"

    def _begin_close(self) -> str:
        controller = self._controller
        if type(controller) is not _BuildWorkerController:
            return self._quarantine()
        current = controller._phase_value()
        if current != "verifying" and not controller._transition("verifying"):
            return self._quarantine()
        object.__setattr__(self, "_state", "closing")
        return "active"

    def _close_process(self) -> str:
        if not self._reaped or not self._group_absent or self._process is None:
            return self._quarantine()
        if self._closed_handles != _ALL_HANDLES:
            return self._close_next_handle()
        return self._clear_raw_process()

    def _close_next_handle(self) -> str:
        process = self._process
        if process is None:
            return self._quarantine()
        if self._handle is not None and type(self._handle_name) is str:
            name = self._handle_name
            marker = self._begin_query(f"handle-status:{name}")
            closed = _local_handle_closed(self._handle)
            if type(closed) is not bool:
                return self._quarantine()
            if closed:
                self._finish_handle(name, marker)
                return "active"
            self._complete_query(marker)
            return self._close_active_handle(name)
        for name, bit in _HANDLE_BITS.items():
            if self._closed_handles & bit:
                continue
            marker = self._begin_query(f"handle-read:{name}")
            handle = _local_process_handle(process, name)
            if handle is None:
                object.__setattr__(self, "_closed_handles", self._closed_handles | bit)
                self._complete_query(marker)
                return "active"
            object.__setattr__(self, "_handle_name", name)
            object.__setattr__(self, "_handle", handle)
            self._complete_query(marker)
            marker = self._begin_query(f"handle-status:{name}")
            closed = _local_handle_closed(handle)
            if type(closed) is not bool:
                return self._quarantine()
            if closed:
                self._finish_handle(name, marker)
                return "active"
            self._complete_query(marker)
            return self._close_active_handle(name)
        return "active"

    def _close_active_handle(self, name: str) -> str:
        handle = self._handle
        bit = _HANDLE_BITS.get(name)
        if handle is None or bit is None or self._handle_name != name:
            return self._quarantine()
        object.__setattr__(self, "_close_intents", self._close_intents | bit)
        marker = self._begin_query(f"handle-close:{name}")
        returned = _local_close_handle(handle)
        closed = _local_handle_closed(handle)
        if returned is not None or type(closed) is not bool or not closed:
            return self._quarantine()
        self._finish_handle(name, marker)
        return "active"

    def _finish_handle(self, name: str, marker: tuple[int, str]) -> None:
        bit = _HANDLE_BITS[name]
        object.__setattr__(self, "_closed_handles", self._closed_handles | bit)
        _handle_checkpoint(name, "closed-bit")
        object.__setattr__(self, "_handle_name", None)
        _handle_checkpoint(name, "name-cleared")
        object.__setattr__(self, "_handle", None)
        _handle_checkpoint(name, "handle-cleared")
        self._complete_query(marker)

    def _clear_raw_process(self) -> str:
        process = self._process
        if process is None:
            return self._quarantine()
        object.__setattr__(self, "_raw_clear_intended", True)
        marker = self._begin_query("raw-clear")
        cleared = self._raw_destination._clear(self._spec, process)
        retained = self._raw_destination._read(self._spec)
        if type(cleared) is not bool or not cleared or retained is not None:
            return self._quarantine()
        object.__setattr__(self, "_raw_cleared", True)
        object.__setattr__(self, "_state", "terminal")
        object.__setattr__(self, "_process", None)
        self._complete_query(marker)
        return self._state

    def _begin_query(self, kind: str) -> tuple[int, str]:
        if self._query_inflight is not None:
            raise RuntimeError("Relay Linux build worker query overlap")
        epoch = self._query_epoch + 1
        marker = (epoch, kind)
        object.__setattr__(self, "_query_epoch", epoch)
        object.__setattr__(self, "_query_inflight", marker)
        return marker

    def _complete_query(self, marker: tuple[int, str]) -> None:
        if self._query_inflight != marker:
            raise RuntimeError("Relay Linux build worker query mismatch")
        object.__setattr__(self, "_query_inflight", None)

    def _recover_inflight(self) -> bool:
        marker = self._query_inflight
        if marker is None:
            return True
        _epoch, kind = marker
        object.__setattr__(self, "_yield_required", True)
        object.__setattr__(self, "_signal_revoked", True)
        if kind in {"pid", "pgid"}:
            self._quarantine()
            return False
        if kind == "term-signal":
            if self._term_sent:
                object.__setattr__(self, "_state", "term-grace")
            elif self._group_absent:
                object.__setattr__(self, "_state", "observe")
            else:
                object.__setattr__(self, "_term_ambiguous", True)
                object.__setattr__(self, "_state", "observe")
        elif kind == "kill-signal":
            object.__setattr__(self, "_state", "kill-wait" if self._kill_sent else "observe")
        elif kind.startswith("handle-close:"):
            return self._recover_handle_close(marker, kind.partition(":")[2])
        elif kind == "raw-clear":
            return self._recover_raw_clear(marker)
        object.__setattr__(self, "_query_inflight", None)
        return True

    def _reconcile_signal_state(self) -> None:
        if self._state not in {"running", "term-grace", "killing", "kill-wait", "observe"}:
            return
        if self._kill_intended:
            if self._kill_sent:
                object.__setattr__(self, "_state", "kill-wait")
            elif not self._group_absent:
                object.__setattr__(self, "_signal_revoked", True)
                object.__setattr__(self, "_state", "observe")
        elif self._term_intended:
            if self._term_sent:
                if self._state == "running":
                    object.__setattr__(self, "_state", "term-grace")
            elif not self._group_absent:
                object.__setattr__(self, "_term_ambiguous", True)
                object.__setattr__(self, "_signal_revoked", True)
                object.__setattr__(self, "_state", "observe")

    def _recover_handle_close(self, marker: tuple[int, str], name: str) -> bool:
        bit = _HANDLE_BITS.get(name)
        if bit is not None and self._closed_handles & bit:
            object.__setattr__(self, "_handle_name", None)
            object.__setattr__(self, "_handle", None)
            self._complete_query(marker)
            return True
        handle = self._handle
        if handle is None or bit is None or self._handle_name != name:
            self._quarantine()
            return False
        closed = _local_handle_closed(handle)
        if type(closed) is not bool:
            self._quarantine()
            return False
        if closed:
            self._finish_handle(name, marker)
            return True
        self._quarantine()
        return False

    def _recover_raw_clear(self, marker: tuple[int, str]) -> bool:
        process = self._process
        if self._raw_cleared and self._state == "terminal" and process is None:
            self._complete_query(marker)
            return True
        if process is None:
            self._quarantine()
            return False
        retained = self._raw_destination._read(self._spec)
        if retained is None:
            object.__setattr__(self, "_raw_cleared", True)
            object.__setattr__(self, "_state", "terminal")
            object.__setattr__(self, "_process", None)
            self._complete_query(marker)
            return True
        if retained is not process:
            self._quarantine()
            return False
        object.__setattr__(self, "_query_inflight", None)
        return True

    def _quarantine(self) -> str:
        controller = self._controller
        object.__setattr__(self, "_signal_revoked", True)
        object.__setattr__(self, "_state", "quarantined")
        if type(controller) is _BuildWorkerController:
            controller._fail()
            if controller._phase_value() != "quarantined":
                controller._transition("quarantined")
        return self._state


__all__: list[str] = []

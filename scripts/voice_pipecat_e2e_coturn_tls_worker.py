"""One-shot TLS I/O owners for CPython, finite controls, and non-wedged local FS."""

from __future__ import annotations

import threading
from typing import Protocol

ControlSignal = tuple[type[KeyboardInterrupt] | type[SystemExit], int | None]
_EVENT_TYPE = type(threading.Event())
_LIMBO_LOCK_TYPE = type(threading.RLock())


class TlsControlLatch:
    """Preserve the first sanitized caller or owner-worker control request."""

    __slots__ = ("_lock", "_value")

    def __init__(self, initial: ControlSignal | None = None) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def record_error(self, error: KeyboardInterrupt | SystemExit) -> None:
        self.record(sanitize_control(error))

    def record(self, value: ControlSignal) -> None:
        while True:
            try:
                with self._lock:
                    if self._value is None:
                        self._value = value
                return
            except (KeyboardInterrupt, SystemExit):
                continue

    def value(self) -> ControlSignal | None:
        while True:
            try:
                with self._lock:
                    return self._value
            except (KeyboardInterrupt, SystemExit) as error:
                self.record_error(error)


class TlsOwnerTask(Protocol):
    control: TlsControlLatch
    done: threading.Event

    def run(self) -> None: ...

    def owner_failed(self, control: ControlSignal | None) -> None: ...


class TlsOwnerService:
    """A secret-free worker that accepts exactly one task, exits, and is joined."""

    __slots__ = (
        "_accepted",
        "_condition",
        "_entered",
        "_finished",
        "_owner_control",
        "_start_call_entered",
        "_stop",
        "_task",
        "_thread",
    )

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._entered = threading.Event()
        self._finished = threading.Event()
        self._owner_control = TlsControlLatch()
        self._stop = False
        self._start_call_entered = False
        self._task: TlsOwnerTask | None = None
        self._accepted: TlsOwnerTask | None = None
        self._thread = threading.Thread(
            target=self._serve,
            name="coturn-tls-private-owner",
            daemon=False,
        )

    def start(self) -> tuple[bool, ControlSignal | None]:
        """Start and prove entry before any secret task can be constructed."""

        control = TlsControlLatch()
        supported = self._thread_runtime_supported(control)
        if not supported or control.value() is not None:
            self._scrub_unstarted(control)
            return False, control.value()
        failed = False
        try:
            self._start_call_entered = True
            self._thread.start()
            self._entered.wait()
            if self._finished.is_set():
                owner_control = self._owner_control.value()
                if owner_control is not None:
                    control.record(owner_control)
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            failed = True
        except BaseException:
            failed = True
        if control.value() is not None:
            failed = True
        if failed:
            self._request_stop(control)
            if self._definitely_unstarted(control):
                self._scrub_unstarted(control)
            else:
                self._wait_for_delayed_entry(control)
                self._join(control)
            return False, control.value()
        return True, None

    def execute(self, task: TlsOwnerTask) -> bool:
        """Publish one task and retain service authority through worker exit."""

        phase = 0
        infrastructure_failed = False
        while phase < 3:
            try:
                if phase == 0:
                    with self._condition:
                        if self._task is None and self._accepted is None:
                            self._task = task
                        elif self._task is not task and self._accepted is not task:
                            infrastructure_failed = True
                            task.owner_failed(None)
                            self._stop = True
                        self._condition.notify_all()
                    phase = 1
                elif phase == 1:
                    if not self._wait_for_task(task):
                        infrastructure_failed = True
                    phase = 2
                else:
                    if not self._join(task.control):
                        infrastructure_failed = True
                    phase = 3
            except (KeyboardInterrupt, SystemExit) as error:
                task.control.record_error(error)
            except BaseException:
                infrastructure_failed = True
                if phase == 0:
                    task.owner_failed(None)
                    self._request_stop(task.control)
                    phase = 1
        return not infrastructure_failed and task.done.is_set()

    def abort(self, initial: ControlSignal | None = None) -> ControlSignal | None:
        """Stop and join an entered service that received no secret task."""

        control = TlsControlLatch(initial)
        self._request_stop(control)
        if self._definitely_unstarted(control):
            self._scrub_unstarted(control)
        else:
            self._wait_for_delayed_entry(control)
            self._join(control)
        owner_control = self._owner_control.value()
        if owner_control is not None:
            control.record(owner_control)
        return control.value()

    def _serve(self) -> None:
        task: TlsOwnerTask | None = None
        try:
            # Prove the condition-wait primitive once before publishing entry.
            with self._condition:
                self._condition.wait(0)
            self._entered.set()
            with self._condition:
                while self._task is None and not self._stop:
                    self._condition.wait()
                if self._stop and self._task is None:
                    return
                task = self._task
                self._accepted = task
                self._task = None
            if task is None:
                return
            try:
                task.run()
            except (KeyboardInterrupt, SystemExit) as error:
                task.owner_failed(sanitize_control(error))
            except BaseException:
                task.owner_failed(None)
        except (KeyboardInterrupt, SystemExit) as error:
            self._owner_control.record_error(error)
        except BaseException:
            pass
        finally:
            self._finalize_owner(task)
            task = None

    def _finalize_owner(self, task: TlsOwnerTask | None) -> None:
        """Publish terminal ownership proof despite finite worker controls."""

        orphan = task
        cleared = False
        while not cleared:
            try:
                with self._condition:
                    if orphan is None:
                        orphan = self._accepted or self._task
                    self._accepted = None
                    self._task = None
                cleared = True
            except (KeyboardInterrupt, SystemExit) as error:
                self._owner_control.record_error(error)
            except BaseException:
                continue
        task = None
        while orphan is not None:
            try:
                if not orphan.done.is_set():
                    orphan.owner_failed(self._owner_control.value())
                if orphan.done.is_set():
                    orphan = None
            except (KeyboardInterrupt, SystemExit) as error:
                self._owner_control.record_error(error)
            except BaseException:
                continue
        published = False
        while not published:
            try:
                with self._condition:
                    self._finished.set()
                    self._entered.set()
                    self._condition.notify_all()
                    published = self._finished.is_set() and self._entered.is_set()
            except (KeyboardInterrupt, SystemExit) as error:
                self._owner_control.record_error(error)
            except BaseException:
                continue

    def _wait_for_task(self, task: TlsOwnerTask) -> bool:
        while True:
            try:
                with self._condition:
                    while not task.done.is_set() and not self._finished.is_set():
                        self._condition.wait()
                    if task.done.is_set():
                        return True
                    task.owner_failed(self._owner_control.value())
                    return False
            except (KeyboardInterrupt, SystemExit) as error:
                task.control.record_error(error)
            except BaseException:
                task.owner_failed(self._owner_control.value())
                return False

    def _request_stop(self, control: TlsControlLatch) -> bool:
        while True:
            try:
                with self._condition:
                    self._stop = True
                    self._condition.notify_all()
                return True
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                return False

    def _definitely_unstarted(self, control: TlsControlLatch) -> bool:
        """Distinguish a pre-start failure from CPython's publication window."""

        while True:
            try:
                if not self._start_call_entered:
                    return True
                if self._thread.ident is not None or self._thread._started.is_set():
                    return False
                lock = threading._active_limbo_lock  # type: ignore[attr-defined]
                with lock:
                    return self._thread not in threading._limbo  # type: ignore[attr-defined]
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                # Unknown thread state retains authority and waits for proof.
                return False

    def _thread_runtime_supported(self, control: TlsControlLatch) -> bool:
        while True:
            try:
                started = getattr(self._thread, "_started", None)
                limbo_lock = getattr(threading, "_active_limbo_lock", None)
                limbo = getattr(threading, "_limbo", None)
                return bool(
                    type(started) is _EVENT_TYPE
                    and type(limbo_lock) is _LIMBO_LOCK_TYPE
                    and type(limbo) is dict
                )
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                return False

    def _wait_for_delayed_entry(self, control: TlsControlLatch) -> None:
        while True:
            try:
                self._entered.wait()
                return
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                continue

    def _join(self, control: TlsControlLatch) -> bool:
        _wait_event(self._finished, control)
        while True:
            try:
                self._thread.join()
                return not self._thread.is_alive()
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                try:
                    return not self._thread.is_alive()
                except BaseException:
                    return False

    def _scrub_unstarted(self, control: TlsControlLatch) -> bool:
        while True:
            try:
                self._thread._target = None  # type: ignore[attr-defined]
                self._thread._args = ()  # type: ignore[attr-defined]
                self._thread._kwargs = {}  # type: ignore[attr-defined]
                return True
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                return False

    def __repr__(self) -> str:
        return "TlsOwnerService()"


def start_tls_owner_service(
    service: TlsOwnerService,
) -> tuple[bool, ControlSignal | None]:
    """Start a caller-owned service without transferring live authority."""

    if type(service) is not TlsOwnerService:
        return False, None
    try:
        started, control = service.start()
        if not started:
            control = service.abort(control)
        return started, control
    except (KeyboardInterrupt, SystemExit) as error:
        control = sanitize_control(error)
        return False, service.abort(control)
    except BaseException:
        return False, service.abort()


def _wait_event(event: threading.Event, control: TlsControlLatch) -> None:
    while True:
        try:
            event.wait()
            return
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            continue


def sanitize_control(error: KeyboardInterrupt | SystemExit) -> ControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt, None
    code = error.code
    if code is not None and type(code) is not int:
        code = 1
    return SystemExit, code


__all__ = [
    "ControlSignal",
    "TlsControlLatch",
    "TlsOwnerService",
    "sanitize_control",
    "start_tls_owner_service",
]

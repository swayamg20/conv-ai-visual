"""Short-lived spawn owner for the Coturn subprocess supervisor."""

from __future__ import annotations

import math
import subprocess
import threading
from collections.abc import Callable
from typing import BinaryIO, Protocol

from scripts.voice_pipecat_e2e_coturn_subprocess_request import SupervisorRequest
from scripts.voice_pipecat_e2e_coturn_subprocess_state import ControllerState, Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    ControlSignal,
    control_signal,
)


class ProcessLike(Protocol):
    _child_created: bool
    args: object
    pid: int
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


PopenFactory = Callable[..., ProcessLike]
ThreadFactory = Callable[..., threading.Thread]
Clock = Callable[[], float]

OWNERSHIP_GUARANTEE_SCOPE = (
    "This ownership protocol assumes a live CPython interpreter, finite handled "
    "KeyboardInterrupt/SystemExit delivery, functioning POSIX primitives, and trusted "
    "fixed Docker/OpenSSL children. It excludes interpreter finalization, abrupt parent "
    "death, SIGKILL, and host death; daemon workers are not a parent-death guarantee."
)


def registered_popen_factory(
    argv: tuple[str, ...],
    *,
    owner_register: Callable[[ProcessLike], None],
    **options: object,
) -> ProcessLike:
    """Prepublish CPython's Popen object before its fork/exec implementation."""

    process = subprocess.Popen.__new__(subprocess.Popen)
    owner_register(process)
    subprocess.Popen.__init__(process, argv, **options)  # type: ignore[arg-type]
    return process


class SpawnMailbox:
    """Raw handoff reachable only from the two worker stacks."""

    __slots__ = (
        "control",
        "deadline_expired",
        "failed",
        "registered",
        "request",
        "returned",
    )

    def __init__(self, request: SupervisorRequest) -> None:
        self.request: SupervisorRequest | None = request
        self.registered: ProcessLike | None = None
        self.returned: ProcessLike | None = None
        self.control: ControlSignal | None = None
        self.failed = False
        self.deadline_expired = False

    def register(self, process: ProcessLike) -> None:
        if self.registered is not None or process is None:
            raise RuntimeError
        self.registered = process

    def take_candidates(self) -> tuple[ProcessLike, ...]:
        first = self.registered
        second = self.returned
        self.registered = None
        self.returned = None
        if first is None:
            return () if second is None else (second,)
        if second is None or second is first:
            return (first,)
        return first, second

    def capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        """Latch the first worker control across any finite nested delivery."""

        while True:
            try:
                signal_value = control_signal(error)
                if self.control is None:
                    self.control = signal_value
                _spawn_control_published()
                return
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                continue

    def scrub(self) -> None:
        while self.request is not None:
            request = self.request
            try:
                request.scrub_all()
                _spawn_request_scrubbed()
                self.request = None
            except (KeyboardInterrupt, SystemExit) as error:
                self.capture_control(error)
            except BaseException:
                continue

    def __repr__(self) -> str:
        return "SpawnMailbox()"


class _SpawnJob:
    __slots__ = ("factory", "mailbox")

    def __init__(self, *, factory: PopenFactory, mailbox: SpawnMailbox) -> None:
        self.factory = factory
        self.mailbox = mailbox


_JOBS: dict[object, _SpawnJob] = {}
_JOBS_LOCK = threading.Lock()


def run_spawn_owner(
    *,
    request: SupervisorRequest,
    factory: PopenFactory,
    controller: ControllerState,
    thread_factory: ThreadFactory,
    deadline: float,
    clock: Clock,
) -> SpawnMailbox:
    """Retain the transient worker, quarantining when startup exceeds its deadline."""

    try: return _run_spawn_owner(request, factory, controller, thread_factory, deadline, clock)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit) as error:
        controller.capture_control(error)
    except BaseException:
        controller.fail("Coturn subprocess start failed")
    return _failed_spawn_mailbox(request, controller)


def _run_spawn_owner(
    request: SupervisorRequest,
    factory: PopenFactory,
    controller: ControllerState,
    thread_factory: ThreadFactory,
    deadline: float,
    clock: Clock,
) -> SpawnMailbox:
    mailbox = SpawnMailbox(request)
    token = object()
    job = _SpawnJob(factory=factory, mailbox=mailbox)
    thread: threading.Thread | None = None
    try:
        with _JOBS_LOCK:
            _JOBS[token] = job
        _spawn_job_published()
        job = None  # type: ignore[assignment]
        thread = thread_factory(
            target=_spawn_entry,
            args=(token,),
            name="coturn-subprocess-spawn-owner",
            daemon=True,
        )
        if not all(callable(getattr(thread, name, None)) for name in ("start", "join", "is_alive")):
            raise TypeError
        thread.start()
    except (KeyboardInterrupt, SystemExit) as error:
        controller.capture_control(error)
        if thread is None or not _thread_alive(thread, controller):
            _cancel_job(token, controller)
            mailbox.failed = True
    except BaseException:
        if thread is None or not _thread_alive(thread, controller):
            _cancel_job(token, controller)
            mailbox.failed = True
    job = None  # type: ignore[assignment]
    if thread is not None and thread_started(thread, controller):
        while _thread_alive(thread, controller):
            current = _clock_value(clock, controller)
            if current is None or current >= deadline:
                mailbox.deadline_expired = True
                if controller.lifecycle() is Lifecycle.SPAWNING:
                    controller.transition(Lifecycle.QUARANTINED)
            try:
                thread.join(0.05)
            except (KeyboardInterrupt, SystemExit) as error:
                controller.capture_control(error)
            except BaseException:
                mailbox.failed = True
        _scrub_thread(thread, controller)
    else:
        _cancel_job(token, controller)
        mailbox.failed = True
    thread = None
    orphan = _take_orphan_job(token, controller)
    if orphan is not None:
        orphan.mailbox.failed = True
        orphan.mailbox.scrub()
    return mailbox


def _failed_spawn_mailbox(
    request: SupervisorRequest,
    controller: ControllerState,
) -> SpawnMailbox:
    while True:
        try:
            mailbox = SpawnMailbox(request)
            mailbox.failed = True
            stored = controller.control()
            if stored is not None:
                mailbox.control = stored
            mailbox.scrub()
            return mailbox
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            controller.fail("Coturn subprocess start failed")


def _spawn_entry(token: object) -> None:
    with _JOBS_LOCK:
        job = _JOBS.pop(token, None)
    if job is None:
        return
    mailbox = job.mailbox
    factory = job.factory
    job = None  # type: ignore[assignment]
    request = mailbox.request
    if request is None:
        mailbox.failed = True
        return
    argv = request.argv
    environment = dict(request.environment)
    try:
        mailbox.returned = factory(
            argv,
            owner_register=mailbox.register,
            executable=argv[0],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=environment.copy(),
            shell=False,
            close_fds=True,
            start_new_session=True,
            umask=request.umask,
            bufsize=0,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        mailbox.capture_control(error)
    except BaseException:
        mailbox.failed = True
    finally:
        argv = ()
        environment.clear()
        request.scrub_spawn_fields()
        request = None
        factory = None  # type: ignore[assignment]


def _cancel_job(token: object, controller: ControllerState) -> None:
    job = _take_orphan_job(token, controller)
    if job is not None:
        job.mailbox.scrub()


def _take_orphan_job(token: object, controller: ControllerState) -> _SpawnJob | None:
    retained: _SpawnJob | None = None
    while True:
        try:
            with _JOBS_LOCK:
                if retained is None:
                    retained = _JOBS.get(token)
                if retained is None:
                    return None
                if _JOBS.get(token) is retained:
                    del _JOBS[token]
            return retained
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            continue


def thread_started(thread: threading.Thread, controller: ControllerState) -> bool:
    while True:
        try:
            return thread.ident is not None or thread.is_alive()
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            return False


def _thread_alive(thread: threading.Thread, controller: ControllerState) -> bool:
    while True:
        try:
            return bool(thread.is_alive())
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            return False


def wait_thread_receipt(
    thread: threading.Thread,
    receipt: threading.Event,
    controller: ControllerState,
    timeout_seconds: float,
) -> bool:
    """Require target-consumption proof, not spoofable thread metadata."""

    while True:
        try:
            if receipt.is_set():
                return True
            if not thread.is_alive():
                return receipt.is_set()
            return receipt.wait(timeout_seconds) is True
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            return False


def join_thread_bounded(
    thread: threading.Thread,
    controller: ControllerState,
    timeout_seconds: float,
) -> bool:
    """Best-effort join after its raw registry authority is revoked."""

    while True:
        try:
            thread.join(timeout_seconds)
            return not thread.is_alive()
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            return False


def resolve_uncommitted_thread(
    thread: threading.Thread | None,
    receipt: threading.Event,
    controller: ControllerState,
    cancel: Callable[[], None],
    detach: Callable[[], None],
    timeout_seconds: float,
) -> None:
    """Retain a consumed worker unless its controller has terminal proof."""

    while True:
        try:
            cancel()
            consumed = receipt.is_set()
            if thread is not None:
                joined = join_thread_bounded(thread, controller, timeout_seconds)
                if not consumed:
                    detach()
                elif not joined or controller.lifecycle() is not Lifecycle.CLEAN:
                    controller.quarantine_retained_owner()
            return
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            controller.fail("Coturn subprocess supervisor start failed")


def _scrub_thread(thread: threading.Thread, controller: ControllerState) -> None:
    while True:
        try:
            if thread.ident is not None and not thread.is_alive():
                thread._args = ()  # type: ignore[attr-defined]
                thread._kwargs = {}  # type: ignore[attr-defined]
                thread._target = None  # type: ignore[attr-defined]
            return
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
        except BaseException:
            return


def _clock_value(clock: Clock, controller: ControllerState) -> float | None:
    while True:
        try:
            value = clock()
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
            continue
        except BaseException:
            return None
        if type(value) not in {int, float}:
            return None
        try:
            normalized = float(value)
            finite = math.isfinite(normalized)
        except (KeyboardInterrupt, SystemExit) as error:
            controller.capture_control(error)
            continue
        except BaseException:
            return None
        return normalized if finite else None


def _spawn_job_published() -> None:
    """Deterministic control seam while the registry owns the raw spawn job."""


def _spawn_control_published() -> None:
    """Deterministic nested-control seam after first spawn control publication."""


def _spawn_request_scrubbed() -> None:
    """Deterministic control seam before releasing a scrubbed request authority."""


__all__ = [
    "OWNERSHIP_GUARANTEE_SCOPE",
    "Clock",
    "PopenFactory",
    "ProcessLike",
    "SpawnMailbox",
    "ThreadFactory",
    "join_thread_bounded",
    "registered_popen_factory",
    "resolve_uncommitted_thread",
    "run_spawn_owner",
    "thread_started",
    "wait_thread_receipt",
]

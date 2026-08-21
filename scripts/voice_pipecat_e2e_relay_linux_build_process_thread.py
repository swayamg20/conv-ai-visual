"""Registered-before-init thread primitive for the private build worker."""

from __future__ import annotations

import math
import threading
import traceback
from collections.abc import Callable

from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _BuildWorkerKernel,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildProcessOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerController,
)

_FAILURE = "Relay Linux build worker thread start failed"
_START_OUTCOME_TOKEN = object()
_THREAD_START = threading.Thread.start


class _BuildThreadStartOutcome:
    """Factory-owned proof of start return or exact pre-OS rejection."""

    __slots__ = ("status",)

    def __init__(self, token: object, *, status: str) -> None:
        if token is not _START_OUTCOME_TOKEN or status not in {"returned", "rejected"}:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "status", status)

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build thread start outcome is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build thread start outcome cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build thread start outcome cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build thread start outcome cannot be serialized")


def _registered_thread_factory(
    *,
    owner_register: Callable[[threading.Thread], None],
    target: Callable[..., None],
    args: tuple[object, ...],
    name: str,
    daemon: bool,
) -> threading.Thread:
    """Prepublish CPython's Thread object before running its initializer."""

    thread = threading.Thread.__new__(threading.Thread)
    owner_register(thread)
    threading.Thread.__init__(
        thread,
        target=target,
        args=args,
        name=name,
        daemon=daemon,
    )
    return thread


def _construct_registered_build_thread(
    owner: _RelayLinuxBuildProcessOwner,
    *,
    controller: _BuildWorkerController,
    target: Callable[[object], None],
    kernel_token: object,
) -> tuple[object | None, bool]:
    """Return only sanitized status while the owner retains every candidate."""

    kernel = (
        owner._kernel_destination._read() if type(owner) is _RelayLinuxBuildProcessOwner else None
    )
    if (
        type(owner) is not _RelayLinuxBuildProcessOwner
        or type(controller) is not _BuildWorkerController
        or not controller._matches(owner._owner_token)
        or owner._controller_destination._read() is not controller
        or type(kernel) is not _BuildWorkerKernel
        or kernel._owner is not owner
        or kernel._token is not kernel_token
        or not callable(target)
    ):
        return None, False
    returned: object | None = None
    failed = False
    try:
        returned = _registered_thread_factory(
            owner_register=owner._thread_destination._publish,
            target=target,
            args=(kernel_token,),
            name="relay-linux-build-worker",
            daemon=True,
        )
    except KeyboardInterrupt as error:
        controller._capture_control(error)
        failed = True
    except SystemExit as error:
        controller._capture_control(error)
        failed = True
    except BaseException as error:
        _scrub_exception_retry(error, controller)
        failed = True
    registered: object | None = None
    while True:
        try:
            registered = owner._thread_destination._read()
            break
        except (KeyboardInterrupt, SystemExit) as error:
            controller._capture_control(error)
            failed = True
        except BaseException as error:
            _scrub_exception_retry(error, controller)
            failed = True
    if registered is not None:
        while True:
            try:
                if not controller._publish_thread(registered):
                    failed = True
                break
            except (KeyboardInterrupt, SystemExit) as error:
                controller._capture_control(error)
                failed = True
            except BaseException as error:
                _scrub_exception_retry(error, controller)
                failed = True
    coherent = bool(not failed and registered is not None and returned is registered)
    returned = None
    return registered, coherent


def _start_registered_build_thread(
    owner: _RelayLinuxBuildProcessOwner,
    controller: _BuildWorkerController,
    thread: object,
) -> _BuildThreadStartOutcome:
    """Start the exact retained thread once; caller owns intent/reconciliation."""

    if (
        type(owner) is not _RelayLinuxBuildProcessOwner
        or type(controller) is not _BuildWorkerController
        or type(thread) is not threading.Thread
        or owner._controller_destination._read() is not controller
        or owner._thread_destination._read() is not thread
        or controller._thread() is not thread
    ):
        raise TypeError(_FAILURE)
    try:
        returned = _THREAD_START(thread)
    except RuntimeError as error:
        if type(error) is not RuntimeError or not _thread_is_exactly_never_started(thread):
            raise
        _scrub_exception_retry(error, controller)
        return _BuildThreadStartOutcome(_START_OUTCOME_TOKEN, status="rejected")
    if returned is not None:
        raise TypeError(_FAILURE)
    _thread_start_returned()
    return _BuildThreadStartOutcome(_START_OUTCOME_TOKEN, status="returned")


def _thread_is_exactly_never_started(thread: object) -> bool:
    """Recognize the complete CPython state before any OS-thread admission."""

    try:
        if type(thread) is not threading.Thread:
            return False
        values = vars(thread)
        started = values.get("_started")
        with threading._active_limbo_lock:
            active = thread in threading._active.values()
            limbo = thread in threading._limbo
        return bool(
            values.get("_initialized") is True
            and values.get("_ident") is None
            and values.get("_native_id") is None
            and type(started) is threading.Event
            and not started.is_set()
            and values.get("_is_stopped", False) is False
            and values.get("_tstate_lock") is None
            and ("_tstate_lock" in values or "_os_thread_handle" in values)
            and thread in threading._dangling
            and not active
            and not limbo
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _registered_build_thread_started(thread: object) -> bool:
    """Report only CPython start evidence; kernel take remains authoritative."""

    if type(thread) is not threading.Thread:
        raise TypeError(_FAILURE)
    identity = thread.ident
    alive = thread.is_alive()
    if type(alive) is not bool or (
        identity is not None and (type(identity) is not int or identity <= 0)
    ):
        raise TypeError(_FAILURE)
    if alive and identity is None:
        raise TypeError(_FAILURE)
    return bool(identity is not None or alive)


def _join_registered_build_thread(thread: object, timeout_seconds: float) -> bool:
    """Perform one bounded join and return exact dead-thread observation."""

    if (
        type(thread) is not threading.Thread
        or type(timeout_seconds) is not float
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0.0
        or not _registered_build_thread_started(thread)
    ):
        raise TypeError(_FAILURE)
    returned = thread.join(timeout_seconds)
    if returned is not None:
        raise TypeError(_FAILURE)
    _thread_join_returned()
    alive = thread.is_alive()
    if type(alive) is not bool:
        raise TypeError(_FAILURE)
    return not alive


def _scrub_registered_build_thread(thread: object) -> None:
    """Drop target/token references only after exact dead-thread proof."""

    if type(thread) is not threading.Thread or not _registered_build_thread_started(thread):
        raise TypeError(_FAILURE)
    alive = thread.is_alive()
    if type(alive) is not bool or alive:
        raise TypeError(_FAILURE)
    object.__setattr__(thread, "_target", None)
    object.__setattr__(thread, "_args", ())
    object.__setattr__(thread, "_kwargs", {})
    _thread_scrubbed()


def _scrub_unstarted_registered_build_thread(thread: object) -> None:
    """Drop callable state when the facade proves start was never entered."""

    if type(thread) is not threading.Thread:
        raise TypeError(_FAILURE)
    object.__setattr__(thread, "_target", None)
    object.__setattr__(thread, "_args", ())
    object.__setattr__(thread, "_kwargs", {})
    _thread_prestart_scrubbed()


def _scrub_exception_retry(
    error: BaseException,
    controller: _BuildWorkerController,
) -> None:
    while True:
        try:
            trace = BaseException.__getattribute__(error, "__traceback__")
            BaseException.__setattr__(error, "__traceback__", None)
            BaseException.__setattr__(error, "__cause__", None)
            BaseException.__setattr__(error, "__context__", None)
            BaseException.__setattr__(error, "__suppress_context__", True)
            if trace is not None:
                traceback.clear_frames(trace)
            return
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException:
            controller._fail()
            return


def _thread_start_returned() -> None:
    """Deterministic cut after the exact Thread.start call returned."""


def _thread_join_returned() -> None:
    """Deterministic cut after one bounded Thread.join call returned."""


def _thread_scrubbed() -> None:
    """Deterministic cut after dead-thread target references were dropped."""


def _thread_prestart_scrubbed() -> None:
    """Deterministic cut after a proven-never-started candidate was scrubbed."""


__all__: list[str] = []

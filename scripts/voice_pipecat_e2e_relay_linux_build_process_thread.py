"""Registered-before-init thread primitive for the private build worker."""

from __future__ import annotations

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


__all__: list[str] = []

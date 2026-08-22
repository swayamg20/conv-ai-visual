"""Sole registered-thread entry for the private relay Linux build process."""

from __future__ import annotations

import traceback

from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _BuildWorkerKernelTake,
    _controller_for_worker_token,
    _publish_worker_terminal,
    _settle_worker_kernel,
    _take_worker_kernel,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_local import (
    _LocalBuildWorkerDriver,
    _new_local_build_worker_driver,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state import (
    _BuildWorkerController,
)


def _relay_linux_build_worker_entry(kernel_token: object) -> None:
    """Run only after registered-thread construction; never expose raw authority."""

    try:
        _drive_relay_linux_build_worker(kernel_token)
    except (KeyboardInterrupt, SystemExit) as error:
        _capture_outer_control(kernel_token, error)
    except BaseException as error:
        _capture_outer_failure(kernel_token, error)


def _drive_relay_linux_build_worker(kernel_token: object) -> None:
    driver: _LocalBuildWorkerDriver | None = None
    take: _BuildWorkerKernelTake | None = None
    reported = False
    while True:
        try:
            if driver is None:
                # This must remain the first constructed worker value.  Its
                # factory is data-only and cannot reach the owner/raw graph.
                driver = _new_local_build_worker_driver()
            if take is None:
                candidate = _take_worker_kernel(kernel_token)
                if candidate is None or candidate.status == "cancelled":
                    return
                take = candidate
            if not driver._bind(take):
                _fail_controller(kernel_token)
                return
            outcome = driver._step()
            if outcome == "active":
                continue
            if outcome in {"waiting", "quarantined"}:
                controller = driver._controller_value()
                if controller is None:
                    return
                controller._wait(0.05)
                driver._wait_completed()
                continue
            terminal = driver._terminal_values()
            ownership = driver._kernel_claim()
            if outcome != "terminal" or terminal is None or ownership is None:
                driver._note_failure()
                return
            kernel, claim = ownership
            returncode, succeeded = terminal
            if not reported:
                reported = _publish_worker_terminal(
                    kernel,
                    claim,
                    returncode=returncode,
                    succeeded=succeeded,
                )
                if not reported:
                    driver._note_failure()
                    _wait_retry(driver)
                    continue
            if not _settle_worker_kernel(kernel, claim):
                driver._note_failure()
                _wait_retry(driver)
                continue
            controller = driver._controller_value()
            if controller is not None and controller._phase_value() != "settled":
                controller._transition("settled")
            return
        except (KeyboardInterrupt, SystemExit) as error:
            controller = _controller_for_worker_token(kernel_token)
            if type(controller) is _BuildWorkerController:
                controller._capture_control(error)
            if driver is not None:
                driver._note_failure()
        except BaseException as error:
            controller = _controller_for_worker_token(kernel_token)
            if type(controller) is _BuildWorkerController:
                controller._fail()
            if driver is not None:
                driver._note_failure()
            _scrub_exception(error)


def _wait_retry(driver: _LocalBuildWorkerDriver) -> None:
    controller = driver._controller_value()
    if controller is not None:
        controller._wait(0.05)
        driver._wait_completed()


def _capture_outer_control(
    kernel_token: object,
    error: KeyboardInterrupt | SystemExit,
) -> None:
    controller = _controller_for_worker_token(kernel_token)
    if type(controller) is _BuildWorkerController:
        controller._capture_control(error)


def _capture_outer_failure(kernel_token: object, error: BaseException) -> None:
    controller = _controller_for_worker_token(kernel_token)
    if type(controller) is _BuildWorkerController:
        controller._fail()
    _scrub_exception(error)


def _fail_controller(kernel_token: object) -> None:
    controller = _controller_for_worker_token(kernel_token)
    if type(controller) is _BuildWorkerController:
        controller._fail()


def _scrub_exception(error: BaseException) -> None:
    try:
        trace = BaseException.__getattribute__(error, "__traceback__")
        BaseException.__setattr__(error, "__traceback__", None)
        BaseException.__setattr__(error, "__cause__", None)
        BaseException.__setattr__(error, "__context__", None)
        BaseException.__setattr__(error, "__suppress_context__", True)
        if trace is not None:
            traceback.clear_frames(trace)
    except BaseException:
        pass


__all__: list[str] = []

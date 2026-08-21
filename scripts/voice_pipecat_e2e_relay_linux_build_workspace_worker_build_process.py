"""Drive one exact relay build process from the workspace-worker stack."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade import (
    _join_relay_linux_build_process,
    _relay_linux_build_process_result,
    _release_relay_linux_build_process,
    _start_relay_linux_build_process,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_worker_status,
    _resolve_build_process_owner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_process_registry import (
    _new_build_owner_destination,
    _preown_build_process,
)
from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _new_raw_build_process_destination,
    _new_relay_linux_build_spec,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _associate_workspace_build_process,
    _complete_failed_workspace_build_process,
    _complete_workspace_build_process,
    _intend_workspace_build_process_association,
    _observe_workspace_build_process_zero,
    _workspace_build_process_association_matches,
    _workspace_build_process_association_phase,
    _workspace_build_process_cleanup_authority,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _acquire_workspace_build_process_start,
    _bind_workspace_build_command_controller,
    _complete_workspace_build_process_start,
    _release_workspace_build_process_start,
    _request_workspace_build_command_cancel,
    _workspace_build_command_authorizes_process,
    _WorkspaceBuildCommand,
    _WorkspaceBuildHandoffError,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerController,
)

_WAIT_SECONDS = 0.05
_CLEANUP_SECONDS = 5.0


def _drive_workspace_build_process(
    *,
    command: _WorkspaceBuildCommand,
    request: object,
    controller: _WorkspaceWorkerController,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> object:
    """Return the canonical zero receipt only after process-registry absence."""

    process_owner = None
    cleanup_value = None
    process_receipt = None
    start_permit = None
    succeeded = False
    try:
        if (
            type(command) is not _WorkspaceBuildCommand
            or type(controller) is not _WorkspaceWorkerController
            or not controller._matches(owner_token)
            or type(build_deadline) is not float
            or not math.isfinite(build_deadline)
            or time.monotonic() >= build_deadline
            or controller._cancellation_requested() is True
            or not _workspace_build_command_authorizes_process(
                command,
                owner_token=owner_token,
                record_token=record_token,
                build_deadline=build_deadline,
            )
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        if not _bind_workspace_build_command_controller(
            command,
            controller=controller,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        spec = _new_relay_linux_build_spec(
            node=request._node,
            next_cli=request._next_cli,
            workspace=request._workspace,
            run_id=request._run_id,
            environment=request._environment_values(),
        )
        raw_destination = _new_raw_build_process_destination(spec)
        destination = _new_build_owner_destination(spec, raw_destination)
        candidate = destination._read()
        cleanup_value = candidate._cleanup_authority
        _intend_workspace_build_process_association(
            command,
            owner_token=owner_token,
            record_token=record_token,
            process_owner=candidate,
            expected_spec=spec,
            expected_raw_destination=raw_destination,
        )
        try:
            process_owner = _preown_build_process(
                spec=spec,
                raw_destination=raw_destination,
                destination=destination,
            )
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
            controller._request_cancel()
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid") from None
        except BaseException as error:
            _scrub_control_minimal(error)
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid") from None
        if process_owner is None:
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        _associate_workspace_build_process(
            command,
            owner_token=owner_token,
            record_token=record_token,
            process_owner=process_owner,
            expected_spec=spec,
            expected_raw_destination=raw_destination,
        )
        cleanup_value = process_owner._cleanup_authority
        if (
            time.monotonic() >= build_deadline
            or controller._cancellation_requested() is True
            or not _workspace_build_process_association_matches(
                command,
                process_owner=process_owner,
                expected_spec=spec,
                expected_raw_destination=raw_destination,
                build_deadline=build_deadline,
            )
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        start_permit = _acquire_workspace_build_process_start(
            command,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        )
        if start_permit is None:
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        try:
            _start_relay_linux_build_process(process_owner, run_deadline=build_deadline)
        finally:
            _release_workspace_build_process_start(start_permit)
            start_permit = None
        if not _complete_workspace_build_process_start(
            command,
            owner_token=owner_token,
            record_token=record_token,
            build_deadline=build_deadline,
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        while True:
            status = _build_process_worker_status(process_owner)
            if status == "settled":
                break
            if status != "started":
                raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
            remaining = build_deadline - time.monotonic()
            if remaining <= 0.0 or controller._cancellation_requested() is True:
                raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
            controller._wait(min(_WAIT_SECONDS, remaining))
        if controller._cancellation_requested() is True:
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        join_deadline = min(build_deadline, time.monotonic() + _CLEANUP_SECONDS)
        _join_relay_linux_build_process(process_owner, join_deadline=join_deadline)
        process_receipt = _relay_linux_build_process_result(process_owner)
        if not _observe_workspace_build_process_zero(
            command,
            process_owner=process_owner,
            process_receipt=process_receipt,
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        _release_relay_linux_build_process(
            cleanup_value,
            cleanup_deadline=time.monotonic() + _CLEANUP_SECONDS,
        )
        if not _complete_workspace_build_process(
            command,
            process_receipt=process_receipt,
        ):
            raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
        succeeded = True
        return process_receipt
    except (KeyboardInterrupt, SystemExit) as control:
        controller._capture_control(control)
        controller._request_cancel()
        raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid") from None
    finally:
        if not succeeded:
            if start_permit is not None:
                try:
                    _release_workspace_build_process_start(start_permit)
                except (KeyboardInterrupt, SystemExit) as control:
                    controller._capture_control(control)
                except BaseException as error:
                    _scrub_control_minimal(error)
            try:
                _request_workspace_build_command_cancel(
                    command,
                    cleanup_deadline=time.monotonic() + _CLEANUP_SECONDS,
                )
            except (KeyboardInterrupt, SystemExit) as control:
                controller._capture_control(control)
            except BaseException as error:
                _scrub_control_minimal(error)
            controller._request_cancel()
            value = cleanup_value if cleanup_value is not None else process_owner
            if value is not None:
                _settle_workspace_build_process(value, controller)
            _settle_workspace_build_process_association(command, controller)


def _cancel_associated_workspace_build_process(
    command: _WorkspaceBuildCommand,
    *,
    cleanup_deadline: float,
) -> bool:
    """Boundedly request process-first cleanup from a path-free command."""

    if type(cleanup_deadline) is not float or not math.isfinite(cleanup_deadline):
        raise _WorkspaceBuildHandoffError("Relay Linux workspace build is invalid")
    first_control: KeyboardInterrupt | SystemExit | None = None

    def retain_control(control: KeyboardInterrupt | SystemExit) -> None:
        nonlocal first_control
        if first_control is None:
            first_control = control
        else:
            _scrub_control_minimal(control)

    try:
        _request_workspace_build_command_cancel(
            command,
            cleanup_deadline=cleanup_deadline,
        )
    except (KeyboardInterrupt, SystemExit) as control:
        retain_control(control)
    except BaseException as error:
        _scrub_control_minimal(error)
    authority = _workspace_build_process_cleanup_authority(command)
    if authority is None:
        completed = True
    else:
        completed = False
        attempts = 0
        while attempts < 64:
            attempts += 1
            try:
                now = time.monotonic()
            except (KeyboardInterrupt, SystemExit) as control:
                retain_control(control)
                continue
            except BaseException as error:
                _scrub_control_minimal(error)
                continue
            if now >= cleanup_deadline:
                break
            try:
                _release_relay_linux_build_process(
                    authority,
                    cleanup_deadline=cleanup_deadline,
                )
            except (KeyboardInterrupt, SystemExit) as control:
                retain_control(control)
            except BaseException as error:
                _scrub_control_minimal(error)
            try:
                completed = _resolve_build_process_owner(authority) is None
            except (KeyboardInterrupt, SystemExit) as control:
                retain_control(control)
            except BaseException as error:
                _scrub_control_minimal(error)
            if completed:
                break
            try:
                now = time.monotonic()
                time.sleep(min(_WAIT_SECONDS, max(0.0, cleanup_deadline - now)))
            except (KeyboardInterrupt, SystemExit) as control:
                retain_control(control)
            except BaseException as error:
                _scrub_control_minimal(error)
    phase = _workspace_build_process_association_phase(command)
    if phase == "preown-intended":
        completed = False
    if first_control is not None:
        raise first_control from None
    return completed


def _settle_workspace_build_process(
    value: object,
    controller: _WorkspaceWorkerController,
) -> None:
    delay = 0.01
    while True:
        try:
            if _resolve_build_process_owner(value) is None:
                return
            _release_relay_linux_build_process(
                value,
                cleanup_deadline=time.monotonic() + _CLEANUP_SECONDS,
            )
            if _resolve_build_process_owner(value) is None:
                return
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        try:
            controller._wait(delay)
            delay = min(_WAIT_SECONDS, delay * 2.0)
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)


def _settle_workspace_build_process_association(
    command: _WorkspaceBuildCommand,
    controller: _WorkspaceWorkerController,
) -> None:
    delay = 0.01
    while True:
        try:
            if _complete_failed_workspace_build_process(command):
                return
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)
        try:
            controller._wait(delay)
            delay = min(_WAIT_SECONDS, delay * 2.0)
        except (KeyboardInterrupt, SystemExit) as control:
            controller._capture_control(control)
        except BaseException as error:
            _scrub_control_minimal(error)


__all__: list[str] = []

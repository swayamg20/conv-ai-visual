"""Private caller handoff from one prepared workspace to one built lease."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _workspace_request_spawn_fingerprint,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _MAX_BUILD_SECONDS,
    _new_workspace_build_command,
    _workspace_build_command_matches_exact,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _WorkspacePreparedReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _DESTINATION_TOKEN,
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerTerminalReceipt,
)

_WAIT_SECONDS = 0.05


def _build_relay_linux_workspace(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    prepared: _WorkspacePreparedReceipt,
    *,
    build_deadline: float,
) -> tuple[_WorkspaceBuiltReceipt | None, bool]:
    """Publish one command and wait for its canonical active built lease."""

    command: _WorkspaceBuildCommand | None = None
    publication_entered = False
    cleanup_attempted = False
    succeeded = False
    try:
        if (
            type(owner) is not _RelayLinuxBuildWorkspaceOwner
            or type(bundle) is not _WorkspaceWorkerBundle
            or type(construction) is not _WorkspaceWorkerThreadReceipt
            or type(prepared) is not _WorkspacePreparedReceipt
            or type(build_deadline) is not float
            or not math.isfinite(build_deadline)
        ):
            return None, False
        now = time.monotonic()
        owner_token = owner._cleanup_authority._key
        record_token = construction._record_token
        if (
            build_deadline <= now
            or build_deadline - now > _MAX_BUILD_SECONDS
            or not bundle._matches(owner_token, owner._receipt_destination)
            or not construction._matches(owner_token, record_token)
            or not prepared._matches(owner_token, record_token, require_active=True)
        ):
            return None, False
        expected_spawn_fingerprint = _workspace_request_spawn_fingerprint(owner._request)
        command = _new_workspace_build_command(
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
            build_deadline=build_deadline,
            expected_spawn_fingerprint=expected_spawn_fingerprint,
        )
        publication_entered = True
        try:
            stored, acquired = bundle._command_destination._publish_before(
                _DESTINATION_TOKEN,
                owner_token,
                command,
                min(build_deadline, time.monotonic() + _WAIT_SECONDS),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            _scrub_control_minimal(error)
            stored, acquired = bundle._command_destination._read_before(
                owner_token,
                min(build_deadline, time.monotonic() + _WAIT_SECONDS),
            )
        if not acquired or type(stored) is not _WorkspaceBuildCommand:
            return None, False
        if not _workspace_build_command_matches_exact(
            stored,
            owner_token=owner_token,
            record_token=record_token,
            prepared=prepared,
            build_deadline=build_deadline,
            expected_spawn_fingerprint=expected_spawn_fingerprint,
        ):
            return None, False
        command = stored
        while time.monotonic() < build_deadline:
            remaining = max(0.0, build_deadline - time.monotonic())
            if remaining <= 0.0:
                break
            built, acquired = bundle._built_destination._read_before(
                owner_token,
                min(build_deadline, time.monotonic() + min(_WAIT_SECONDS, remaining)),
            )
            if (
                acquired
                and type(built) is _WorkspaceBuiltReceipt
                and built._matches(owner_token, record_token, require_active=True)
            ):
                succeeded = True
                return built, True
            terminal, acquired = bundle._terminal_destination._read_before(
                owner_token,
                min(build_deadline, time.monotonic() + min(_WAIT_SECONDS, remaining)),
            )
            if acquired and type(terminal) is _WorkspaceWorkerTerminalReceipt:
                return None, False
            remaining = max(0.0, build_deadline - time.monotonic())
            if remaining > 0.0:
                bundle._controller._wait(min(_WAIT_SECONDS, remaining))
        return None, False
    except (KeyboardInterrupt, SystemExit) as control:
        if type(bundle) is _WorkspaceWorkerBundle:
            bundle._controller._capture_control(control)
            if publication_entered and type(command) is _WorkspaceBuildCommand:
                _cleanup_associated_process(command, bundle._controller)
                cleanup_attempted = True
        else:
            _scrub_control_minimal(control)
        return None, False
    except BaseException as error:
        _scrub_control_minimal(error)
        return None, False
    finally:
        if (
            not succeeded
            and publication_entered
            and not cleanup_attempted
            and type(command) is _WorkspaceBuildCommand
            and type(bundle) is _WorkspaceWorkerBundle
        ):
            _cleanup_associated_process(command, bundle._controller)
            bundle._controller._request_cancel()


def _cleanup_associated_process(
    command: _WorkspaceBuildCommand,
    controller: object,
) -> None:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process import (
        _cancel_associated_workspace_build_process,
    )

    try:
        _cancel_associated_workspace_build_process(
            command,
            cleanup_deadline=time.monotonic() + 5.0,
        )
    except (KeyboardInterrupt, SystemExit) as control:
        if type(controller) is _WorkspaceWorkerController:
            controller._capture_control(control)
        else:
            _scrub_control_minimal(control)
    except BaseException as error:
        _scrub_control_minimal(error)


__all__: list[str] = []

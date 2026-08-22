"""Canonical terminal publication for the exact workspace worker."""

from __future__ import annotations

import threading
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _resolve_workspace_worker_active_record,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_contract import (
    _locked_before,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _workspace_filesystem_is_settled,
    _workspace_filesystem_was_claimed,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _TERMINAL_TOKEN,
    _WorkspaceWorkerClaim,
    _WorkspaceWorkerCoordinator,
    _WorkspaceWorkerTerminalReceipt,
)

_DESTINATION_WAIT_SECONDS = 0.05


def _publish_workspace_worker_terminal_for_token_impl(
    record_token: object,
    claim: _WorkspaceWorkerClaim | None,
) -> _WorkspaceWorkerTerminalReceipt | None:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle import (
        _publish_terminal_candidate,
        _workspace_worker_terminal_published,
    )

    current = threading.current_thread()
    if (
        type(record_token) is not object
        or type(current) is not registry._WorkspaceWorkerThread
        or (claim is not None and claim._paths_cleared is not True)
        or (claim is not None and claim._coordinator._settlement_token is not claim._claim_token)
    ):
        return None
    settlement_deadline = time.monotonic() + _DESTINATION_WAIT_SECONDS
    destination = None
    controller = None
    owner_token = None
    terminal = None
    exact_record = None
    with _locked_before(registry._REGISTRY_LOCK, settlement_deadline):
        for bundle, record in tuple(registry._RECORDS.items()):
            if record._record_token is record_token and record._entry[1] is current:
                with _locked_before(record._lock, settlement_deadline):
                    coordinator = record._lifecycle
                    if (
                        claim is None
                        and type(coordinator) is _WorkspaceWorkerCoordinator
                        and coordinator._phase == "start-intended"
                        and coordinator._claim_token is None
                        and not _workspace_filesystem_was_claimed(record._record_token)
                    ):
                        object.__setattr__(coordinator, "_workspace_settled", True)
                    filesystem_settled = bool(
                        coordinator._workspace_settled is True
                        and (
                            (
                                claim is None
                                and coordinator._claim_token is None
                                and not _workspace_filesystem_was_claimed(record._record_token)
                            )
                            or (
                                claim is not None
                                and _workspace_filesystem_is_settled(
                                    record._owner_token,
                                    record._record_token,
                                    claim._claim_token,
                                )
                            )
                        )
                    )
                    if (
                        type(coordinator) is not _WorkspaceWorkerCoordinator
                        or type(coordinator._settlement_token) is not object
                        or not filesystem_settled
                        or coordinator._phase
                        not in {"start-intended", "claimed", "terminal-pending", "terminal"}
                        or (
                            claim is not None
                            and (
                                coordinator._claim_token is not claim._claim_token
                                or record._owner_token is not claim._owner_token
                            )
                        )
                    ):
                        return None
                    terminal = coordinator._terminal
                    if terminal is None:
                        terminal = _WorkspaceWorkerTerminalReceipt(
                            _TERMINAL_TOKEN,
                            owner_token=record._owner_token,
                            record_token=record._record_token,
                            started=True,
                        )
                        object.__setattr__(coordinator, "_terminal", terminal)
                        object.__setattr__(coordinator, "_phase", "terminal-pending")
                    destination = bundle._terminal_destination
                    controller = bundle._controller
                    owner_token = record._owner_token
                    exact_record = record
                    break
    if destination is None:
        active = _resolve_workspace_worker_active_record(record_token, settlement_deadline)
        if active is None:
            return None
        record, destination, controller, owner_token = active
        if (
            type(record) is not registry._WorkspaceWorkerThreadRecord
            or record._entry is None
            or record._entry[1] is not current
        ):
            return None
        with _locked_before(record._lock, settlement_deadline):
            coordinator = record._lifecycle
            if (
                claim is None
                and type(coordinator) is _WorkspaceWorkerCoordinator
                and coordinator._phase == "start-intended"
                and coordinator._claim_token is None
                and not _workspace_filesystem_was_claimed(record_token)
            ):
                object.__setattr__(coordinator, "_workspace_settled", True)
            filesystem_settled = bool(
                coordinator._workspace_settled is True
                and (
                    (
                        claim is None
                        and coordinator._claim_token is None
                        and not _workspace_filesystem_was_claimed(record_token)
                    )
                    or (
                        claim is not None
                        and _workspace_filesystem_is_settled(
                            owner_token,
                            record_token,
                            claim._claim_token,
                        )
                    )
                )
            )
            if (
                type(coordinator) is not _WorkspaceWorkerCoordinator
                or type(coordinator._settlement_token) is not object
                or not filesystem_settled
            ):
                return None
            terminal = coordinator._terminal
            if terminal is None:
                terminal = _WorkspaceWorkerTerminalReceipt(
                    _TERMINAL_TOKEN,
                    owner_token=owner_token,
                    record_token=record_token,
                    started=True,
                )
                object.__setattr__(coordinator, "_terminal", terminal)
                object.__setattr__(coordinator, "_phase", "terminal-pending")
            exact_record = record
    if destination is None or controller is None or terminal is None:
        return None
    committed = _publish_terminal_candidate(
        destination,
        owner_token,
        terminal,
        controller,
        settlement_deadline,
    )
    if not committed:
        return None
    active_commit = _resolve_workspace_worker_active_record(record_token, settlement_deadline)
    with _locked_before(registry._REGISTRY_LOCK, settlement_deadline):
        finished = False
        for _bundle, record in tuple(registry._RECORDS.items()):
            if record is exact_record and record._record_token is record_token:
                with _locked_before(record._lock, settlement_deadline):
                    coordinator = record._lifecycle
                    if coordinator._terminal is not terminal:
                        return None
                    object.__setattr__(coordinator, "_phase", "terminal")
                    coordinator._notify()
                    finished = True
                    break
        if not finished:
            if active_commit is None or active_commit[0] is not exact_record:
                return None
            with _locked_before(exact_record._lock, settlement_deadline):
                coordinator = exact_record._lifecycle
                if coordinator._terminal is not terminal:
                    return None
                object.__setattr__(coordinator, "_phase", "terminal")
                coordinator._notify()
                finished = True
        if not finished:
            return None
    try:
        _workspace_worker_terminal_published()
    except (KeyboardInterrupt, SystemExit) as control:
        registry._capture_workspace_worker_control(controller, control)
    except BaseException as error:
        _scrub_control_minimal(error)
    return terminal


__all__: list[str] = []

"""Bounded join and exact release for the inert workspace worker record."""

from __future__ import annotations

import math
import threading
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _transfer_workspace_worker_bundle as _release_workspace_worker_pin,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _workspace_worker_active_root_occupied,
    _workspace_worker_ownership_locked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_contract import (
    _record_parts_locked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_control import (
    _clear_workspace_worker_control_bridge,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle import (
    _locked_before,
    _reconcile_workspace_worker_terminal,
    _WorkspaceWorkerCoordinator,
    _WorkspaceWorkerTerminalReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _DESTINATION_TOKEN,
    _WorkspaceWorkerBundle,
)

_THREAD_JOIN = threading.Thread.join
_THREAD_IS_ALIVE = threading.Thread.is_alive
_FAILURE = "Relay Linux workspace worker lifecycle is invalid"


def _join_workspace_worker_thread(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    deadline: float,
) -> tuple[_WorkspaceWorkerTerminalReceipt | None, bool]:
    if type(deadline) is not float or not math.isfinite(deadline):
        raise TypeError(_FAILURE)
    if not _acquire_before(registry._REGISTRY_LOCK, deadline):
        return None, False
    record_locked = False
    try:
        bundle, record, raw, coordinator = _record_parts_locked(
            binding,
            construction,
            allow_failed=True,
        )
        record_locked = _acquire_before(record._lock, deadline)
        if not record_locked:
            return None, False
        pending = coordinator._phase == "terminal-pending"
        no_effect = coordinator._effect_phase in {"none", "rejected"}
        already_joined = coordinator._joined
    finally:
        if record_locked:
            record._lock.release()
        registry._REGISTRY_LOCK.release()
    if pending:
        terminal = _reconcile_workspace_worker_terminal(
            binding,
            construction,
            deadline,
        )
        if terminal is None:
            return None, False
    if no_effect:
        terminal = coordinator._terminal
        return terminal, type(terminal) is _WorkspaceWorkerTerminalReceipt
    if already_joined:
        terminal = coordinator._terminal
        return terminal, type(terminal) is _WorkspaceWorkerTerminalReceipt
    remaining = max(0.0, deadline - time.monotonic())
    if not coordinator._join_lock.acquire(timeout=remaining):
        return coordinator._terminal, False
    try:
        remaining = max(0.0, deadline - time.monotonic())
        started = vars(raw).get("_started")
        if type(started) is not threading.Event or not started.wait(remaining):
            return coordinator._terminal, False
        remaining = max(0.0, deadline - time.monotonic())
        returned = _THREAD_JOIN(raw, remaining)
        if returned is not None:
            raise TypeError(_FAILURE)
        if not _raw_lifecycle_is_unshadowed(raw) or _THREAD_IS_ALIVE(raw):
            return coordinator._terminal, False
        terminal = _reconcile_workspace_worker_terminal(
            binding,
            construction,
            deadline,
        )
        if terminal is None:
            return None, False
        if not _acquire_before(registry._REGISTRY_LOCK, deadline):
            return coordinator._terminal, False
        record_locked = False
        try:
            bundle = binding._bundle_ref()
            if registry._RECORDS.get(bundle) is not record:
                raise TypeError(_FAILURE)
            record_locked = _acquire_before(record._lock, deadline)
            if not record_locked:
                return coordinator._terminal, False
            if coordinator._terminal is not terminal or coordinator._phase != "terminal":
                return None, False
            object.__setattr__(coordinator, "_joined", True)
            coordinator._notify()
        finally:
            if record_locked:
                record._lock.release()
            registry._REGISTRY_LOCK.release()
        _workspace_worker_join_returned()
        return terminal, True
    finally:
        coordinator._join_lock.release()


def _acquire_before(lock: object, deadline: float) -> bool:
    remaining = max(0.0, deadline - time.monotonic())
    if remaining <= 0.0:
        return bool(lock.acquire(blocking=False))
    return bool(lock.acquire(timeout=remaining))


def _release_workspace_worker_thread(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    terminal: _WorkspaceWorkerTerminalReceipt,
    deadline: float,
) -> bool:
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        raise TypeError(_FAILURE)
    with _locked_before(registry._REGISTRY_LOCK, deadline):
        record_exists = registry._RECORDS.get(bundle) is not None
    if record_exists:
        reconciled = _reconcile_workspace_worker_terminal(
            binding,
            construction,
            deadline,
        )
        if reconciled is None:
            return False
        if reconciled is not terminal:
            raise TypeError(_FAILURE)
    coordinator = bundle._lifecycle
    if type(coordinator) is not _WorkspaceWorkerCoordinator:
        raise TypeError(_FAILURE)
    with _locked_before(coordinator._release_lock, deadline):
        object.__setattr__(coordinator, "_release_deadline", deadline)
        call_scrub_hook = False
        missing_record = False
        with _locked_before(registry._REGISTRY_LOCK, deadline):
            record = registry._RECORDS.get(bundle)
            if record is None:
                missing_record = True
            else:
                with _locked_before(record._lock, deadline):
                    _bundle, exact, raw, coordinator = _record_parts_locked(
                        binding,
                        construction,
                        allow_failed=True,
                    )
                    if (
                        exact is not record
                        or coordinator._terminal is not terminal
                        or coordinator._phase != "terminal"
                        or not terminal._matches(
                            record._owner_token,
                            record._record_token,
                        )
                        or not _release_liveness_is_coherent(
                            record,
                            raw,
                            coordinator,
                            terminal,
                        )
                    ):
                        return False
                    if coordinator._release_phase == "none":
                        object.__setattr__(coordinator, "_release_phase", "intended")
                        object.__setattr__(terminal, "_release_intended", True)
                    if coordinator._release_phase == "intended":
                        _clear_workspace_worker_control_bridge(
                            record._control_bridge,
                            record._record_token,
                            record._control_token,
                            raw,
                        )
                        if type(raw) is registry._WorkspaceWorkerThread:
                            object.__setattr__(raw, "_target", None)
                            object.__setattr__(raw, "_args", ())
                            object.__setattr__(raw, "_kwargs", {})
                            object.__setattr__(raw, "_workspace_control_token", None)
                        object.__setattr__(coordinator, "_release_phase", "scrubbed")
                        call_scrub_hook = True
        if not missing_record:
            if call_scrub_hook:
                _workspace_worker_thread_scrubbed()
            with _workspace_worker_ownership_locked(deadline):
                with _locked_before(registry._REGISTRY_LOCK, deadline):
                    record = registry._RECORDS.get(bundle)
                    if record is None:
                        missing_record = True
                    else:
                        with _locked_before(record._lock, deadline):
                            if (
                                record._lifecycle is not coordinator
                                or coordinator._release_phase != "scrubbed"
                            ):
                                raise TypeError(_FAILURE)
                            del registry._RECORDS[bundle]
                if not missing_record:
                    _release_workspace_worker_pin(record._record_token, bundle)
                    if _workspace_worker_active_root_occupied(
                        record._record_token,
                        deadline,
                    ):
                        return False
                    object.__setattr__(coordinator, "_release_phase", "complete")
                    coordinator._notify()
    if missing_record:
        return _complete_missing_record_release(
            binding,
            construction,
            terminal,
            coordinator,
            deadline,
        )
    _workspace_worker_record_released()
    return _forget_filesystem_state(
        binding._owner_token,
        bundle,
        construction._record_token,
        deadline,
    )


def _complete_missing_record_release(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    terminal: _WorkspaceWorkerTerminalReceipt,
    coordinator: object,
    deadline: float,
) -> bool:
    bundle = binding._bundle_ref()
    if (
        type(bundle) is not _WorkspaceWorkerBundle
        or type(coordinator) is not _WorkspaceWorkerCoordinator
    ):
        raise TypeError(_FAILURE)
    with _locked_before(coordinator._release_lock, deadline):
        if (
            not coordinator._matches(binding._owner_token, construction._record_token)
            or coordinator._terminal is not terminal
            or terminal._release_intended is not True
            or coordinator._release_phase not in {"intended", "scrubbed", "complete"}
        ):
            raise TypeError(_FAILURE)
        _release_workspace_worker_pin(construction._record_token, bundle)
        if _workspace_worker_active_root_occupied(construction._record_token, deadline):
            return False
        object.__setattr__(coordinator, "_release_phase", "complete")
        coordinator._notify()
    return _forget_filesystem_state(
        binding._owner_token,
        bundle,
        construction._record_token,
        deadline,
    )


def _release_liveness_is_coherent(
    record: registry._WorkspaceWorkerThreadRecord,
    raw: registry._WorkspaceWorkerThread | None,
    coordinator: _WorkspaceWorkerCoordinator,
    terminal: _WorkspaceWorkerTerminalReceipt,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _workspace_filesystem_is_settled,
        _workspace_filesystem_was_claimed,
    )

    was_claimed = _workspace_filesystem_was_claimed(record._record_token)
    if terminal.started is True and was_claimed:
        if not _workspace_filesystem_is_settled(
            record._owner_token,
            record._record_token,
            coordinator._claim_token,
        ):
            return False
    elif terminal.started is False and was_claimed:
        return False
    state = record._entry[0]
    if state == registry._INITIALIZED:
        if (
            type(raw) is not registry._WorkspaceWorkerThread
            or not _raw_lifecycle_is_unshadowed(raw)
            or _THREAD_IS_ALIVE(raw)
        ):
            return False
        started = vars(raw).get("_started")
        if type(started) is not threading.Event:
            return False
        if started.is_set():
            return bool(
                terminal.started is True
                and coordinator._effect_phase in {"entered", "returned"}
                and coordinator._joined
            )
        return bool(
            terminal.started is False
            and coordinator._effect_phase in {"none", "rejected"}
            and coordinator._joined
        )
    return bool(
        state in {registry._FAILED, registry._POISONED}
        and terminal.started is False
        and coordinator._effect_phase in {"none", "rejected"}
        and coordinator._joined
    )


def _forget_filesystem_state(
    owner_token: object,
    bundle: _WorkspaceWorkerBundle,
    record_token: object,
    deadline: float,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_forget import (
        _complete_workspace_build_state_forget,
        _forget_workspace_build_state,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
        _forget_workspace_filesystem_settlement,
        _workspace_filesystem_state_is_forgotten,
    )

    command, acquired = bundle._command_destination._read_before(owner_token, deadline)
    if not acquired:
        return False
    built, acquired = bundle._built_destination._read_before(owner_token, deadline)
    if not acquired:
        return False
    prepared_destination = bundle._prepared_destination
    prepared, acquired = prepared_destination._read_before(
        prepared_destination._request,
        deadline,
    )
    if (
        not acquired
        or (command is None and built is not None)
        or not _forget_workspace_build_state(
            command,
            prepared=prepared,
            owner_token=owner_token,
            record_token=record_token,
        )
    ):
        return False
    _forget_workspace_filesystem_settlement(record_token)
    if not _workspace_filesystem_state_is_forgotten(record_token):
        return False
    return bool(
        bundle._built_destination._retire_before(
            _DESTINATION_TOKEN,
            owner_token,
            built,
            deadline,
        )
        and bundle._command_destination._retire_before(
            _DESTINATION_TOKEN,
            owner_token,
            command,
            deadline,
        )
        and _complete_workspace_build_state_forget(
            command,
            prepared=prepared,
            owner_token=owner_token,
            record_token=record_token,
        )
    )


def _workspace_worker_join_returned() -> None:
    pass


def _workspace_worker_thread_scrubbed() -> None:
    pass


def _workspace_worker_record_released() -> None:
    pass


def _raw_lifecycle_is_unshadowed(raw: object) -> bool:
    return bool(
        type(raw) is registry._WorkspaceWorkerThread
        and not {"is_alive", "join", "run", "start"}.intersection(vars(raw))
    )


__all__: list[str] = []

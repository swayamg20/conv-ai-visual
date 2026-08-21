"""Registry-owned, path-scrubbing, filesystem-inert worker lifecycle."""

from __future__ import annotations

import math
import threading
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _attach_workspace_worker_active_record,
    _pin_workspace_worker_bundle,
    _resolve_workspace_worker_active_record,
    _transfer_workspace_worker_bundle,
    _transfer_workspace_worker_claim,
    _workspace_worker_bundle_is_pinned,
    _workspace_worker_ownership_locked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_contract import (
    _exact_start_rejection,
    _locked_before,
    _publish_start_candidate,
    _record_parts_locked,
    _stage_settled_worker_terminal_locked,
    _wait_for_worker_handoff,
    _workspace_worker_no_effect_is_proven,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_control import (
    _capture_worker_control,
    _workspace_worker_control_bridge_record_token,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _DESTINATION_TOKEN,
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _CLAIM_TOKEN,
    _START_TOKEN,
    _TERMINAL_TOKEN,
    _WorkspaceWorkerClaim,
    _WorkspaceWorkerCoordinator,
    _WorkspaceWorkerStartReceipt,
    _WorkspaceWorkerTerminalReceipt,
)

_THREAD_START = threading.Thread.start
_DESTINATION_WAIT_SECONDS = 0.05
_FAILURE = "Relay Linux workspace worker lifecycle is invalid"


def _start_workspace_worker_thread(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    start_deadline: float,
) -> tuple[_WorkspaceWorkerStartReceipt | None, bool]:
    """Cross one durable start intent and wait until the worker terminal pins it."""

    if type(start_deadline) is not float or not math.isfinite(start_deadline):
        raise TypeError(_FAILURE)

    start_returned_now = False
    needs_pin = False
    with _locked_before(registry._REGISTRY_LOCK, start_deadline):
        bundle, record, raw, coordinator = _record_parts_locked(binding, construction)
        with _locked_before(record._lock, start_deadline):
            if coordinator._start_deadline is None:
                object.__setattr__(coordinator, "_start_deadline", start_deadline)
            deadline = coordinator._start_deadline
            if type(deadline) is not float or not math.isfinite(deadline):
                raise TypeError(_FAILURE)
            if coordinator._phase == "terminal":
                receipt = coordinator._start_receipt
                return receipt, bool(
                    type(receipt) is _WorkspaceWorkerStartReceipt
                    and coordinator._effect_phase == "returned"
                    and coordinator._handoff_expired is False
                )
            if coordinator._effect_phase == "none":
                if coordinator._pin_phase == "none":
                    if (
                        binding._controller._cancellation_requested()
                        or time.monotonic() >= deadline
                    ):
                        object.__setattr__(coordinator, "_handoff_expired", True)
                        _terminalize_no_effect_locked(bundle, record, raw, coordinator)
                        return None, False
                    receipt = _WorkspaceWorkerStartReceipt(
                        _START_TOKEN,
                        owner_token=record._owner_token,
                        record_token=record._record_token,
                    )
                    object.__setattr__(coordinator, "_start_receipt", receipt)
                    object.__setattr__(coordinator, "_phase", "pin-intended")
                    object.__setattr__(coordinator, "_pin_phase", "intended")
                    coordinator._notify()
                needs_pin = True
            receipt = coordinator._start_receipt
            coherent = coordinator._effect_phase == "returned"
            destination = bundle._thread_destination
            owner_token = record._owner_token
    if needs_pin:
        clear_pin = False
        terminalized = False
        with _workspace_worker_ownership_locked(deadline):
            pinned = _workspace_worker_bundle_is_pinned(
                record._record_token,
                bundle,
                deadline,
            )
            if not pinned:
                if binding._controller._cancellation_requested() or time.monotonic() >= deadline:
                    with _locked_before(registry._REGISTRY_LOCK, deadline):
                        with _locked_before(record._lock, deadline):
                            _terminalize_no_effect_locked(bundle, record, raw, coordinator)
                    return None, False
                _pin_workspace_worker_bundle(record._record_token, bundle)
            if not _workspace_worker_bundle_is_pinned(
                record._record_token,
                bundle,
                deadline,
            ):
                raise TypeError(_FAILURE)
            _attach_workspace_worker_active_record(
                record._record_token,
                bundle,
                record=record,
                terminal_destination=bundle._terminal_destination,
                controller=binding._controller,
                owner_token=owner_token,
                deadline=deadline,
            )
            with _locked_before(registry._REGISTRY_LOCK, deadline):
                with _locked_before(record._lock, deadline):
                    exact_bundle, exact_record, exact_raw, exact_coordinator = _record_parts_locked(
                        binding, construction
                    )
                    if (
                        exact_bundle is not bundle
                        or exact_record is not record
                        or exact_raw is not raw
                        or exact_coordinator is not coordinator
                    ):
                        raise TypeError(_FAILURE)
                    if coordinator._effect_phase == "none":
                        if time.monotonic() >= deadline or not (
                            registry._thread_initialization_is_complete(
                                raw,
                                record._record_token,
                                record._control_bridge,
                                record._control_token,
                                binding._controller,
                            )
                        ):
                            _terminalize_no_effect_locked(bundle, record, raw, coordinator)
                            clear_pin = terminalized = True
                        else:
                            settlement_token = coordinator._settlement_token
                            if settlement_token is None:
                                settlement_token = object()
                                object.__setattr__(
                                    coordinator,
                                    "_settlement_token",
                                    settlement_token,
                                )
                            elif type(settlement_token) is not object:
                                raise TypeError(_FAILURE)
                            object.__setattr__(coordinator, "_pin_phase", "pinned")
                            object.__setattr__(coordinator, "_phase", "start-intended")
                            object.__setattr__(coordinator, "_effect_phase", "entered")
                            coordinator._notify()
                            try:
                                returned = _THREAD_START(raw)
                            except RuntimeError as error:
                                if type(error) is not RuntimeError or not (
                                    _exact_start_rejection(raw)
                                ):
                                    raise
                                _scrub_control_minimal(error)
                                object.__setattr__(
                                    coordinator,
                                    "_effect_phase",
                                    "rejected",
                                )
                                _terminalize_no_effect_locked(
                                    bundle,
                                    record,
                                    raw,
                                    coordinator,
                                )
                                clear_pin = terminalized = True
                            else:
                                if returned is not None:
                                    raise TypeError(_FAILURE)
                                object.__setattr__(
                                    coordinator,
                                    "_effect_phase",
                                    "returned",
                                )
                                start_returned_now = True
                                coordinator._notify()
                    receipt = coordinator._start_receipt
                    coherent = coordinator._effect_phase == "returned"
            if clear_pin:
                _transfer_workspace_worker_bundle(
                    record._record_token,
                    bundle,
                    deadline,
                )
        if terminalized:
            return None, False
    if type(receipt) is not _WorkspaceWorkerStartReceipt or not _publish_start_candidate(
        destination,
        owner_token,
        receipt,
        deadline,
    ):
        object.__setattr__(coordinator, "_handoff_expired", True)
        binding._controller._request_cancel()
        return receipt, False
    if start_returned_now:
        _workspace_worker_start_returned()
    terminal = _wait_for_worker_handoff(coordinator, deadline)
    if not terminal:
        object.__setattr__(coordinator, "_handoff_expired", True)
        binding._controller._request_cancel()
    return receipt, bool(coherent and terminal and coordinator._handoff_expired is False)


def _cancel_workspace_worker_thread_before_start(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    deadline: float,
) -> _WorkspaceWorkerTerminalReceipt | None:
    with _locked_before(registry._REGISTRY_LOCK, deadline):
        bundle, record, raw, coordinator = _record_parts_locked(
            binding,
            construction,
            allow_failed=True,
        )
        with _locked_before(record._lock, deadline):
            if coordinator._effect_phase == "none":
                _terminalize_no_effect_locked(bundle, record, raw, coordinator)
            terminal = coordinator._terminal
            pending = coordinator._phase == "terminal-pending"
    if pending:
        terminal = _reconcile_workspace_worker_terminal(
            binding,
            construction,
            deadline,
        )
    return terminal


def _take_workspace_worker_claim(record_token: object) -> _WorkspaceWorkerClaim | None:
    """Allow only the exact current registered thread to take its one claim."""

    current = threading.current_thread()
    if type(record_token) is not object or type(current) is not registry._WorkspaceWorkerThread:
        return None
    bundle = None
    record = None
    coordinator = None
    receipt = None
    lookup_deadline = time.monotonic() + _DESTINATION_WAIT_SECONDS
    with _locked_before(registry._REGISTRY_LOCK, lookup_deadline):
        for bundle, record in tuple(registry._RECORDS.items()):
            if (
                type(record) is registry._WorkspaceWorkerThreadRecord
                and record._record_token is record_token
                and record._entry is not None
                and record._entry[1] is current
            ):
                with _locked_before(record._lock, lookup_deadline):
                    coordinator = record._lifecycle
                    if (
                        type(coordinator) is not _WorkspaceWorkerCoordinator
                        or coordinator._phase != "start-intended"
                        or coordinator._effect_phase not in {"entered", "returned"}
                    ):
                        return None
                    receipt = coordinator._start_receipt
                    if type(receipt) is not _WorkspaceWorkerStartReceipt:
                        return None
                    deadline = coordinator._start_deadline
                    if (
                        type(deadline) is not float
                        or time.monotonic() >= deadline
                        or bundle._controller._cancellation_requested()
                    ):
                        object.__setattr__(coordinator, "_handoff_expired", True)
                        coordinator._notify()
                        return None
                    destination = bundle._thread_destination
                    owner_token = record._owner_token
                    break
    if (
        type(bundle) is not _WorkspaceWorkerBundle
        or type(record) is not registry._WorkspaceWorkerThreadRecord
        or type(coordinator) is not _WorkspaceWorkerCoordinator
        or type(receipt) is not _WorkspaceWorkerStartReceipt
        or not _publish_start_candidate(
            destination,
            owner_token,
            receipt,
            deadline,
        )
    ):
        if type(coordinator) is _WorkspaceWorkerCoordinator:
            object.__setattr__(coordinator, "_handoff_expired", True)
        return None
    with _workspace_worker_ownership_locked(deadline):
        with _locked_before(registry._REGISTRY_LOCK, deadline):
            if registry._RECORDS.get(bundle) is not record:
                return None
            with _locked_before(record._lock, deadline):
                settlement_token = coordinator._settlement_token
                if (
                    record._entry is None
                    or record._entry[1] is not current
                    or record._lifecycle is not coordinator
                    or coordinator._phase != "start-intended"
                    or coordinator._start_receipt is not receipt
                    or type(settlement_token) is not object
                    or time.monotonic() >= deadline
                    or bundle._controller._cancellation_requested()
                ):
                    object.__setattr__(coordinator, "_handoff_expired", True)
                    coordinator._notify()
                    return None
                claim = _WorkspaceWorkerClaim(
                    _CLAIM_TOKEN,
                    owner_token=record._owner_token,
                    record_token=record._record_token,
                    claim_token=settlement_token,
                    coordinator=coordinator,
                    controller=bundle._controller,
                    bundle=bundle,
                    request=bundle._prepared_destination._request,
                    prepared_destination=bundle._prepared_destination,
                )
                object.__setattr__(coordinator, "_claim_token", settlement_token)
                object.__setattr__(coordinator, "_phase", "claimed")
                coordinator._notify()
        _transfer_workspace_worker_claim(
            record._record_token,
            bundle,
            record=record,
            terminal_destination=bundle._terminal_destination,
            controller=bundle._controller,
            owner_token=record._owner_token,
            deadline=deadline,
        )
        return claim


def _run_inert_workspace_worker(control_bridge: object) -> None:
    """Take, scrub, and publish only the fixed synthetic worker terminal."""

    claim: _WorkspaceWorkerClaim | None = None
    record_token = _workspace_worker_control_bridge_record_token(control_bridge)
    try:
        _workspace_worker_before_take(record_token)
        claim = _take_workspace_worker_claim(record_token)
        if claim is not None:
            try:
                _workspace_worker_claim_taken(claim)
            except (KeyboardInterrupt, SystemExit) as control:
                registry._capture_workspace_worker_control(claim._controller, control)
            except BaseException as error:
                _scrub_control_minimal(error)
    except (KeyboardInterrupt, SystemExit) as control:
        _capture_worker_control(control_bridge, control)
    except BaseException as error:
        _scrub_control_minimal(error)
    finally:
        if claim is not None:
            if claim._paths_cleared is not True:
                claim._scrub_paths()
            try:
                _workspace_worker_claim_scrubbed(claim)
            except (KeyboardInterrupt, SystemExit) as control:
                registry._capture_workspace_worker_control(claim._controller, control)
            except BaseException as error:
                _scrub_control_minimal(error)
        _publish_workspace_worker_terminal_for_token(record_token, claim)


def _publish_workspace_worker_terminal(
    claim: _WorkspaceWorkerClaim,
) -> _WorkspaceWorkerTerminalReceipt | None:
    if type(claim) is not _WorkspaceWorkerClaim:
        return None
    return _publish_workspace_worker_terminal_for_token(claim._record_token, claim)


def _publish_workspace_worker_terminal_for_token(
    record_token: object,
    claim: _WorkspaceWorkerClaim | None,
) -> _WorkspaceWorkerTerminalReceipt | None:
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
                        type(coordinator) is not _WorkspaceWorkerCoordinator
                        or type(coordinator._settlement_token) is not object
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
        active = _resolve_workspace_worker_active_record(
            record_token,
            settlement_deadline,
        )
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
                type(coordinator) is not _WorkspaceWorkerCoordinator
                or type(coordinator._settlement_token) is not object
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
    active_commit = _resolve_workspace_worker_active_record(
        record_token,
        settlement_deadline,
    )
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


def _reconcile_workspace_worker_terminal(
    binding: registry._WorkspaceWorkerThreadBinding,
    construction: registry._WorkspaceWorkerThreadReceipt,
    deadline: float | None = None,
) -> _WorkspaceWorkerTerminalReceipt | None:
    if deadline is None:
        deadline = time.monotonic() + _DESTINATION_WAIT_SECONDS
    if type(deadline) is not float or not math.isfinite(deadline):
        raise TypeError(_FAILURE)
    needs_synthesis = False
    with _locked_before(registry._REGISTRY_LOCK, deadline):
        bundle, record, raw, coordinator = _record_parts_locked(
            binding,
            construction,
            allow_failed=True,
        )
        with _locked_before(record._lock, deadline):
            terminal = coordinator._terminal
            if terminal is None:
                needs_synthesis = True
            destination = bundle._terminal_destination
            owner_token = record._owner_token
            controller = bundle._controller
            terminal_phase = coordinator._phase == "terminal"
    if needs_synthesis:
        active = _resolve_workspace_worker_active_record(record._record_token, deadline)
        if active is None:
            return None
        active_record, active_destination, active_controller, active_owner = active
        with _locked_before(registry._REGISTRY_LOCK, deadline):
            exact_bundle = binding._bundle_ref()
            if exact_bundle is not bundle or registry._RECORDS.get(bundle) is not record:
                return None
            with _locked_before(record._lock, deadline):
                exact_bundle, exact_record, exact_raw, exact_coordinator = _record_parts_locked(
                    binding, construction, allow_failed=True
                )
                if (
                    exact_bundle is not bundle
                    or exact_record is not record
                    or exact_raw is not raw
                    or exact_coordinator is not coordinator
                    or active_record is not record
                    or active_destination is not destination
                    or active_controller is not controller
                    or active_owner is not owner_token
                ):
                    return None
                terminal = _stage_settled_worker_terminal_locked(
                    record,
                    raw,
                    coordinator,
                )
                if terminal is None:
                    return None
                terminal_phase = False
    if terminal_phase:
        stored, acquired = destination._read_before(owner_token, deadline)
        if acquired and stored is terminal:
            return terminal
    if not _publish_terminal_candidate(
        destination,
        owner_token,
        terminal,
        controller,
        deadline,
    ):
        return None
    with _locked_before(registry._REGISTRY_LOCK, deadline):
        exact_bundle = binding._bundle_ref()
        if exact_bundle is not bundle or registry._RECORDS.get(bundle) is not record:
            return None
        with _locked_before(record._lock, deadline):
            if coordinator._terminal is not terminal:
                return None
            object.__setattr__(coordinator, "_phase", "terminal")
            coordinator._notify()
            clear_pin = _workspace_worker_no_effect_is_proven(
                record,
                raw,
                coordinator,
                terminal,
            )
    if clear_pin:
        _transfer_workspace_worker_bundle(record._record_token, bundle, deadline)
    return terminal


def _publish_terminal_candidate(
    destination: object,
    owner_token: object,
    terminal: _WorkspaceWorkerTerminalReceipt,
    controller: _WorkspaceWorkerController,
    deadline: float | None = None,
) -> bool:
    if deadline is None:
        deadline = time.monotonic() + _DESTINATION_WAIT_SECONDS
    for _attempt in range(3):
        try:
            published, acquired = destination._publish_before(
                _DESTINATION_TOKEN,
                owner_token,
                terminal,
                deadline,
            )
            if acquired and published is terminal:
                stored, acquired = destination._read_before(owner_token, deadline)
                if acquired and stored is terminal:
                    return True
        except (KeyboardInterrupt, SystemExit) as control:
            registry._capture_workspace_worker_control(controller, control)
        except BaseException as error:
            _scrub_control_minimal(error)
        if time.monotonic() >= deadline:
            return False
    return False


def _terminalize_no_effect_locked(
    bundle: _WorkspaceWorkerBundle,
    record: registry._WorkspaceWorkerThreadRecord,
    raw: registry._WorkspaceWorkerThread | None,
    coordinator: _WorkspaceWorkerCoordinator,
) -> _WorkspaceWorkerTerminalReceipt:
    terminal = coordinator._terminal
    if terminal is None:
        if coordinator._effect_phase not in {"none", "rejected"}:
            raise TypeError(_FAILURE)
        registry._scrub_workspace_worker_no_effect(record, raw)
        terminal = _WorkspaceWorkerTerminalReceipt(
            _TERMINAL_TOKEN,
            owner_token=record._owner_token,
            record_token=record._record_token,
            started=False,
        )
        object.__setattr__(coordinator, "_terminal", terminal)
        object.__setattr__(coordinator, "_phase", "terminal-pending")
        object.__setattr__(coordinator, "_joined", True)
    coordinator._notify()
    return terminal


def _workspace_worker_start_returned() -> None:
    pass


def _workspace_worker_before_take(_record_token: object) -> None:
    pass


def _workspace_worker_claim_taken(_claim: _WorkspaceWorkerClaim) -> None:
    pass


def _workspace_worker_claim_scrubbed(_claim: _WorkspaceWorkerClaim) -> None:
    pass


def _workspace_worker_terminal_published() -> None:
    pass


__all__: list[str] = []

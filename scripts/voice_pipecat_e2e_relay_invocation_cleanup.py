"""Graph-opaque retry authority for one staged relay invocation owner."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup_authority import (
    _REGISTRY as _REGISTRY,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup_authority import (
    RelayInvocationCleanupAuthority,
    RelayInvocationCleanupRequired,
    _release_cleanup_owner,
    _resolve_cleanup_owner,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup_authority import (
    _register_cleanup_owner as _register_cleanup_owner_record,
)
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    RelayInvocationDriver,
    _RelayChildAuthorityDestination,
    _RelayChildStartDestination,
    _RelayChildStopDestination,
    _RelayInvocationOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _concrete_invocation_cleanup_contract,
    _resolve_or_mint_concrete_invocation_stop_request,
    _retire_concrete_invocation_pair,
)
from scripts.voice_pipecat_e2e_relay_invocation_support import (
    _SECRET_LOCK,
    _SECRET_RECORDS,
    _scrub_exception,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    _FAILURE,
    RelayInvocationError,
    RelayStopRequest,
)

_CLEANUP_FAILURE = "Relay invocation cleanup failed"
_MISSING = object()
_MAX_ACTIVE_INVOCATIONS = 32
_CLEANUP_PHASES = frozenset({"active", "terminal", "scrubbed"})
_TERMINAL_OWNER_FIELDS = (
    "_secret_key",
    "_prebootstrap_receipt",
    "_app_start",
    "_web_start",
    "_browser_start",
    "_app_stop",
    "_web_stop",
    "_browser_stop",
    "_owner_token",
    "_cleanup_clock",
    "_cleanup_timeout_seconds",
    "_stop_request",
    "_driver",
    "_destination",
    "_tools",
)


def _register_cleanup_owner(
    authority: RelayInvocationCleanupAuthority,
    owner: object,
) -> None:
    _register_cleanup_owner_record(
        authority,
        owner,
        max_active_invocations=_MAX_ACTIVE_INVOCATIONS,
    )


def _recover_invocation_owner_publication(
    existing: object | None,
    ready: bool,
    control: ControlSignal | None,
    owner_type: type[object],
) -> object | None:
    if existing is None:
        if control is not None:
            raise_control(control)
        return None
    if type(existing) is not owner_type:
        raise RelayInvocationError(_FAILURE)
    if getattr(existing, "_state", None) == "cleanup-required":
        recovery = getattr(existing, "_cleanup_authority", None)
        if control is not None:
            raise_control(control, recovery)
        raise RelayInvocationCleanupRequired(recovery)
    if ready and getattr(existing, "_state", None) != "cleaned":
        if control is not None:
            raise_control(control)
        return existing
    cleanup_failed, cleanup_control = _cleanup_invocation_owner(existing)
    control = control or cleanup_control
    if not cleanup_failed and control is None:
        return None
    recovery = _invocation_recovery(existing, cleanup_failed)
    _raise_invocation_outcome(True, recovery, control)
    return None


def _sanitize_invocation_boundary(
    owner_type: type[object],
    *,
    always_recovery: bool = False,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorate(function: Callable[..., object]) -> Callable[..., object]:
        @wraps(function)
        def guarded(*args: object, **kwargs: object) -> object:
            operation: Callable[..., object] | None = function
            result: object = _MISSING
            control: ControlSignal | None = None
            recovery: RelayInvocationCleanupAuthority | None = None
            candidate: object | None = None
            first: object | None = None
            boundary_owner: object | None = None
            boundary_recovery: RelayInvocationCleanupAuthority | None = None
            failed = False
            try:
                try:
                    result = operation(*args, **kwargs)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                    candidate = getattr(error, "cleanup_authority", None)
                    if type(candidate) is RelayInvocationCleanupAuthority:
                        recovery = candidate
                    _scrub_exception(error)
                except RelayInvocationCleanupRequired as error:
                    recovery = error.cleanup_authority
                    _scrub_exception(error)
                except BaseException as error:
                    failed = True
                    _scrub_exception(error)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                failed = True
            finalized = False
            recover_boundary = True
            while not finalized:
                try:
                    if recover_boundary and args:
                        first = args[0]
                        if type(first) is owner_type:
                            boundary_owner = first
                        elif type(first) is RelayInvocationCleanupAuthority:
                            boundary_recovery = first
                            boundary_owner = _resolve_cleanup_owner(first, owner_type)
                        stored = getattr(boundary_owner, "_control", None)
                        if type(stored) is tuple and len(stored) == 2:
                            # Cleanup records controls before returning. A later
                            # return-boundary control must not replace that
                            # chronologically earlier signal.
                            control = stored
                            boundary_owner._control = None
                        if (
                            (control is not None or failed)
                            and recovery is None
                            and type(first) is RelayInvocationCleanupAuthority
                        ):
                            recovery = first
                        if (
                            (control is not None or failed)
                            and recovery is None
                            and type(first) is owner_type
                            and (
                                always_recovery
                                or getattr(first, "_state", None) == "cleanup-required"
                                or (
                                    getattr(first, "_cleanup_phase", None)
                                    in {"terminal", "scrubbed"}
                                    and _cleanup_registry_retains(first, owner_type)
                                )
                            )
                        ):
                            candidate = getattr(first, "_cleanup_authority", None)
                            if type(candidate) is RelayInvocationCleanupAuthority:
                                recovery = candidate
                    if (
                        (control is not None or failed)
                        and recovery is None
                        and type(boundary_recovery) is RelayInvocationCleanupAuthority
                    ):
                        recovery = boundary_recovery
                    args = ()
                    kwargs = {}
                    operation = None
                    result = None if result is _MISSING else result
                    candidate = first = boundary_owner = None
                    boundary_recovery = None
                    finalized = True
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    failed = True
                    recover_boundary = False
            if control is not None:
                result = None
                raise_control(control, recovery)
            if recovery is not None:
                result = None
                raise RelayInvocationCleanupRequired(recovery) from None
            if failed or result is _MISSING:
                result = None
                raise RelayInvocationError(_FAILURE) from None
            return result

        return guarded

    return decorate


def _cleanup_registry_retains(owner: object, owner_type: type[object]) -> bool:
    authority = getattr(owner, "_cleanup_authority", None)
    return (
        type(authority) is RelayInvocationCleanupAuthority
        and _resolve_cleanup_owner(authority, owner_type) is owner
    )


def _drop_secrets(key: object) -> tuple[bool, ControlSignal | None]:
    """Scrub in place before releasing the retained capacity record."""

    with _SECRET_LOCK:
        secrets = _SECRET_RECORDS.get(key)
    if secrets is None:
        return True, None
    control: ControlSignal | None = None
    scrubbed = False
    try:
        secrets.scrub()
        scrubbed = True
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    if not scrubbed:
        return False, control
    try:
        with _SECRET_LOCK:
            if _SECRET_RECORDS.get(key) is secrets:
                del _SECRET_RECORDS[key]
            dropped = key not in _SECRET_RECORDS
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or control_signal(error)
        _scrub_exception(error)
        with _SECRET_LOCK:
            dropped = key not in _SECRET_RECORDS
    except BaseException as error:
        _scrub_exception(error)
        dropped = False
    secrets = None
    return dropped, control


def _cleanup_invocation_owner(
    owner: object,
    control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    operation_lock = getattr(owner, "_operation_lock", None)
    if operation_lock is None:
        return True, None
    with _locked_invocation_operation(owner):
        return _cleanup_invocation_locked(owner, control)


@contextmanager
def _locked_invocation_operation(owner: object) -> Iterator[None]:
    operation_lock = getattr(owner, "_operation_lock", None)
    construction_lock = getattr(owner, "_construction_lock", None)
    if operation_lock is None or construction_lock is None:
        raise RelayInvocationError(_CLEANUP_FAILURE)
    with construction_lock, operation_lock:
        yield


def _cleanup_invocation_locked(
    owner: object,
    incoming_control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    phase = getattr(owner, "_cleanup_phase", None)
    if phase not in _CLEANUP_PHASES:
        return True, None
    owner._control = None
    _remember_cleanup_control(owner, incoming_control)
    if phase == "scrubbed":
        pending = getattr(owner, "_control", None)
        if pending is not None:
            return True, pending
        _release_cleanup_owner(owner._cleanup_authority, owner)
        return False, None
    owner._state = "cleanup-required"
    failed = False
    if phase == "active":
        try:
            failed = not _latch_stop_request(owner)
        except (KeyboardInterrupt, SystemExit) as error:
            _remember_cleanup_control(owner, control_signal(error))
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
    if phase == "active":
        if not failed:
            failed = not _settle_children(owner)
        if not failed:
            destination = getattr(owner, "_destination", None)
            cleared, clear_control = _clear_owner_destination(destination, owner)
            _remember_cleanup_control(owner, clear_control)
            failed = not cleared
        if not failed:
            dropped, secret_control = _drop_secrets(getattr(owner, "_secret_key", None))
            _remember_cleanup_control(owner, secret_control)
            failed = not dropped
        if not failed:
            owner._cleanup_phase = phase = "terminal"
    if not failed and phase == "terminal":
        try:
            _scrub_terminal_owner(owner)
        except (KeyboardInterrupt, SystemExit) as error:
            _remember_cleanup_control(owner, control_signal(error))
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
    pending = getattr(owner, "_control", None)
    if pending is not None:
        return True, pending
    if failed or getattr(owner, "_cleanup_phase", None) != "scrubbed":
        return True, None
    _release_cleanup_owner(owner._cleanup_authority, owner)
    return False, None


def _settle_children(owner: object) -> bool:
    driver = getattr(owner, "_driver", None)
    if type(driver) is not RelayInvocationDriver:
        return False
    stopped: list[object] = []
    settled = True
    for role, attribute, stop_name in (
        ("browser", "_browser", "_browser_stop"),
        ("web", "_web", "_web_stop"),
        ("app", "_app", "_app_stop"),
    ):
        destination = getattr(owner, attribute, None)
        if destination is None:
            continue
        if type(destination) is not _RelayChildAuthorityDestination:
            settled = False
            break
        observed, authority, peek_control = _peek_child_authority(destination)
        _remember_cleanup_control(owner, peek_control)
        if not observed:
            settled = False
            break
        if authority is None:
            sealed, seal_control = _seal_child_authority(destination)
            _remember_cleanup_control(owner, seal_control)
            if not sealed:
                settled = False
                break
            setattr(owner, attribute, None)
            continue
        if any(authority is current for current in stopped):
            cleared, clear_control = _clear_child_authority(destination, authority)
            _remember_cleanup_control(owner, clear_control)
            if cleared:
                setattr(owner, attribute, None)
                continue
            settled = False
            break
        stop_destination = getattr(owner, stop_name, None)
        if type(stop_destination) is not _RelayChildStopDestination:
            settled = False
            break
        observed, committed, receipt_control = _read_stop_receipt(owner, stop_destination, role)
        _remember_cleanup_control(owner, receipt_control)
        if not observed or (receipt_control is not None and not committed):
            settled = False
            break
        if not committed:
            try:
                driver._stop(authority, getattr(owner, "_stop_request", None), stop_destination)
            except (KeyboardInterrupt, SystemExit) as error:
                _remember_cleanup_control(owner, control_signal(error))
                _scrub_exception(error)
            except BaseException as error:
                _scrub_exception(error)
            observed, committed, receipt_control = _read_stop_receipt(owner, stop_destination, role)
            _remember_cleanup_control(owner, receipt_control)
        cleared = False
        if observed and committed:
            cleared, clear_control = _clear_child_authority(destination, authority)
            _remember_cleanup_control(owner, clear_control)
        if observed and committed and cleared:
            stopped.append(authority)
            setattr(owner, attribute, None)
        else:
            settled = False
        authority = destination = stop_destination = None
        if not settled:
            break
    stopped.clear()
    driver = None
    return settled and not any(
        getattr(owner, name, None) is not None for name in ("_app", "_web", "_browser")
    )


def _remember_cleanup_control(owner: object, control: ControlSignal | None) -> None:
    if control is not None and getattr(owner, "_control", None) is None:
        owner._control = control


def _scrub_terminal_owner(owner: object) -> None:
    driver = getattr(owner, "_driver", None)
    tools = getattr(owner, "_tools", None)
    concrete_configured = bool(
        type(getattr(driver, "_pair_key", None)) is object
        or type(getattr(tools, "_pair_key", None)) is object
        or getattr(owner, "_cleanup_timeout_seconds", None) is not None
        or getattr(owner, "_cleanup_clock", None) is not None
        or getattr(owner, "_stop_request", None) is not None
    )
    if concrete_configured and not _retire_concrete_invocation_pair(
        driver,
        tools,
        getattr(owner, "_destination", None),
    ):
        raise RelayInvocationError(_CLEANUP_FAILURE)
    if not _seal_owner_token_destinations(owner):
        raise RelayInvocationError(_CLEANUP_FAILURE)
    for attribute in _TERMINAL_OWNER_FIELDS:
        setattr(owner, attribute, None)
    owner._state = "cleaned"
    owner._cleanup_phase = "scrubbed"


def _latch_stop_request(owner: object) -> bool:
    timeout = getattr(owner, "_cleanup_timeout_seconds", None)
    clock = getattr(owner, "_cleanup_clock", None)
    driver = getattr(owner, "_driver", None)
    tools = getattr(owner, "_tools", None)
    current = getattr(owner, "_stop_request", None)
    if timeout is None and clock is None and current is None:
        return True
    contract = _concrete_invocation_cleanup_contract(driver, tools)
    if not (
        type(contract) is tuple
        and len(contract) == 3
        and timeout == contract[1]
        and clock is contract[2]
    ):
        return False
    resolved = _resolve_or_mint_concrete_invocation_stop_request(
        driver,
        tools,
        getattr(owner, "_destination", None),
    )
    if type(resolved) is not RelayStopRequest or (current is not None and current is not resolved):
        return False
    owner._stop_request = resolved
    return owner._stop_request is resolved


def _read_stop_receipt(
    owner: object,
    destination: _RelayChildStopDestination,
    role: str,
) -> tuple[bool, bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            destination._read(getattr(owner, "_owner_token", None), role)
            return True, True, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except TypeError as error:
            _scrub_exception(error)
            return True, False, control
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, False, control


def _peek_child_authority(
    destination: _RelayChildAuthorityDestination,
) -> tuple[bool, object | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return True, destination._peek(), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, None, control


def _clear_owner_destination(
    destination: object,
    owner: object,
) -> tuple[bool, ControlSignal | None]:
    if type(destination) is not _RelayInvocationOwnerDestination:
        return False, None
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._clear(owner), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _clear_child_authority(
    destination: _RelayChildAuthorityDestination,
    authority: object,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            cleared = destination._clear(authority)
            return bool(cleared and destination._seal_empty()), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _seal_child_authority(
    destination: _RelayChildAuthorityDestination,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._seal_empty(), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _seal_owner_token_destinations(owner: object) -> bool:
    owner_token = getattr(owner, "_owner_token", None)
    for role in ("app", "web", "browser"):
        for suffix, destination_type in (
            ("start", _RelayChildStartDestination),
            ("stop", _RelayChildStopDestination),
        ):
            destination = getattr(owner, f"_{role}_{suffix}", None)
            if destination is None:
                continue
            if type(destination) is not destination_type or not destination._seal(
                owner_token, role
            ):
                return False
    return True


def _raise_invocation_outcome(
    failed: bool,
    recovery: RelayInvocationCleanupAuthority | None,
    control: ControlSignal | None,
) -> None:
    if control is not None:
        raise_control(control, recovery)
    if recovery is not None:
        raise RelayInvocationCleanupRequired(recovery) from None
    if failed:
        raise RelayInvocationError(_FAILURE) from None


def _invocation_recovery(
    owner: object,
    cleanup_failed: bool,
) -> RelayInvocationCleanupAuthority | None:
    authority = getattr(owner, "_cleanup_authority", None)
    if cleanup_failed and type(authority) is RelayInvocationCleanupAuthority:
        return authority
    return None


__all__ = [
    "RelayInvocationCleanupAuthority",
    "RelayInvocationCleanupRequired",
]

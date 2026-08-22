"""Fresh public-entry recovery boundary for the relay aggregate."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from scripts import voice_pipecat_e2e_relay_owner_state as _state
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_owner_state import RelayProbeOwner
from scripts.voice_pipecat_e2e_relay_owner_values import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeOwnerError,
)

_MISSING = object()


def _sanitize_owner_boundary(
    failure: str,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Catch call, lock, return, and argument-scrub cuts behind one fresh boundary."""

    def decorate(function: Callable[..., object]) -> Callable[..., object]:
        @wraps(function)
        def guarded(*args: object, **kwargs: object) -> object:
            operation: Callable[..., object] | None = function
            result: object = _MISSING
            public_owner: object = _public_owner_argument(args, kwargs)
            control: ControlSignal | None = None
            failed = False
            try:
                try:
                    result = operation(*args, **kwargs)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                    _state._scrub_exception(error)
                except BaseException as error:
                    failed = True
                    _state._scrub_exception(error)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                _state._scrub_exception(error)
            except BaseException:
                failed = True
            finalized = False
            recovered: RelayProbeOwner | None = None
            authority: RelayProbeCleanupAuthority | None = None
            while not finalized:
                try:
                    if (control is not None or failed) and public_owner is not _MISSING:
                        recovered = _recover_public_owner(public_owner)
                        authority = _registered_public_authority(recovered)
                    args = ()
                    kwargs = {}
                    operation = None
                    public_owner = None
                    if control is not None or failed:
                        result = None
                    recovered = None
                    finalized = True
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                    _state._scrub_exception(error)
                except BaseException:
                    failed = True
            if control is not None:
                result = None
                raise_control(control, authority)
            if authority is not None:
                result = None
                raise RelayProbeCleanupRequired(authority) from None
            if failed or result is _MISSING:
                result = None
                raise RelayProbeOwnerError(failure) from None
            return result

        return guarded

    return decorate


def _public_owner_argument(args: tuple[object, ...], kwargs: dict[str, object]) -> object:
    if args and type(args[0]) in {RelayProbeOwner, RelayProbeCleanupAuthority}:
        return args[0]
    candidate = kwargs.get("owner", _MISSING)
    return (
        candidate if type(candidate) in {RelayProbeOwner, RelayProbeCleanupAuthority} else _MISSING
    )


def _recover_public_owner(value: object) -> RelayProbeOwner | None:
    """Resolve and poison a live exact root without reusing the public resolver."""

    owner: object | None = value if type(value) is RelayProbeOwner else None
    authority: object | None = None
    if type(owner) is RelayProbeOwner:
        try:
            if object.__getattribute__(owner, "_factory_token") is not _state._OWNER_TOKEN:
                return None
            authority = object.__getattribute__(owner, "_cleanup_authority")
        except BaseException:
            return None
    elif type(value) is RelayProbeCleanupAuthority:
        try:
            if not value._is_authentic():
                return None
            authority = value
        except BaseException:
            return None
    if type(authority) is not RelayProbeCleanupAuthority:
        return None
    try:
        key = object.__getattribute__(authority, "_key")
    except BaseException:
        return None
    with _state._REGISTRY_LOCK:
        record = _state._REGISTRY.get(key)
        registered = record[0] if record is not None else None
    if type(registered) is RelayProbeOwner:
        try:
            canonical = object.__getattribute__(registered, "_cleanup_authority")
        except BaseException:
            return None
        if canonical is not authority or (owner is not None and owner is not registered):
            return None
        if not _state._terminal_owner_valid(registered):
            with registered._lock:
                registered._cleanup_only = True
                registered._publish_requested = False
                registered._state = "cleanup-only"
        return registered
    if type(owner) is RelayProbeOwner and _state._terminal_owner_valid(owner):
        return owner
    return None


def _registered_public_authority(
    owner: RelayProbeOwner | None,
) -> RelayProbeCleanupAuthority | None:
    if type(owner) is not RelayProbeOwner:
        return None
    try:
        authority = object.__getattribute__(owner, "_cleanup_authority")
        key = object.__getattribute__(authority, "_key")
    except BaseException:
        return None
    with _state._REGISTRY_LOCK:
        record = _state._REGISTRY.get(key)
    return (
        authority
        if type(authority) is RelayProbeCleanupAuthority
        and record is not None
        and record[0] is owner
        else None
    )


__all__: list[str] = []

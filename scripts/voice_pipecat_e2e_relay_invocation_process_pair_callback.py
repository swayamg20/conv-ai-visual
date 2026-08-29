"""Sealed callbacks for one inert concrete invocation capability pair."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _CONCRETE_ADAPTER_SEAL,
    _CONCRETE_DRIVER_TOKEN,
    RelayInvocationDriver,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayInvocationError

_CALLBACK_TOKEN = object()
_OPERATIONS = frozenset({"preown", "start", "prebootstrap", "finish", "stop"})
_FAILURE = "Relay concrete invocation pair is invalid"


class _RelayConcreteInvocationCallback:
    __slots__ = ("_operation", "_pair_key")

    def __init__(self, token: object, pair_key: object, operation: str) -> None:
        if (
            token is not _CALLBACK_TOKEN
            or type(pair_key) is not object
            or operation not in _OPERATIONS
        ):
            raise TypeError("Relay concrete invocation callback is factory-owned")
        object.__setattr__(self, "_pair_key", pair_key)
        object.__setattr__(self, "_operation", operation)

    def _matches(self, pair_key: object, operation: str) -> bool:
        return self._pair_key is pair_key and self._operation == operation

    def __call__(self, *args: object) -> None:
        from scripts.voice_pipecat_e2e_relay_invocation_process_pair_effects import (
            _inert_forward,
            _inert_preown,
            _inert_stop,
        )

        if self._operation == "preown" and len(args) == 2:
            _inert_preown(self._pair_key, args[0], args[1])
            return
        if self._operation in {"start", "prebootstrap", "finish"} and len(args) == 3:
            _inert_forward(self._pair_key, args[0], args[1], args[2])
            return
        if self._operation == "stop" and len(args) == 3:
            _inert_stop(self._pair_key, args[0], args[1], args[2])
            return
        raise TypeError("Relay concrete invocation callback arguments are invalid")

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay concrete invocation callback is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay concrete invocation callback cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete invocation callback cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete invocation callback cannot be serialized")


def _new_concrete_invocation_callback(
    pair_key: object,
    operation: str,
) -> _RelayConcreteInvocationCallback:
    return _RelayConcreteInvocationCallback(_CALLBACK_TOKEN, pair_key, operation)


def _new_inert_concrete_driver(pair_key: object) -> RelayInvocationDriver:
    return RelayInvocationDriver(
        _CONCRETE_DRIVER_TOKEN,
        preown=_new_concrete_invocation_callback(pair_key, "preown"),
        start=_new_concrete_invocation_callback(pair_key, "start"),
        prebootstrap=_new_concrete_invocation_callback(pair_key, "prebootstrap"),
        finish=_new_concrete_invocation_callback(pair_key, "finish"),
        stop=_new_concrete_invocation_callback(pair_key, "stop"),
        pair_key=pair_key,
    )


def _retired_callback(*_args: object, **_kwargs: object) -> None:
    raise RelayInvocationError(_FAILURE)


def _scrub_pair_capabilities(driver: object, tools: object) -> None:
    if type(driver) is RelayInvocationDriver:
        for name in ("_preown", "_start", "_prebootstrap", "_finish", "_stop"):
            object.__setattr__(driver, name, _retired_callback)
    if type(tools) is RelayInvocationTools:
        for name in ("_node", "_web_root", "_next_cli", "_playwright_cli", "_epoch_clock"):
            object.__setattr__(tools, name, None)


def _retired_pair_matches(driver: object, tools: object) -> bool:
    try:
        pair_key = getattr(driver, "_pair_key", None)
        return bool(
            type(driver) is RelayInvocationDriver
            and type(tools) is RelayInvocationTools
            and type(pair_key) is object
            and tools._pair_key is pair_key
            and driver._adapter_seal is _CONCRETE_ADAPTER_SEAL
            and tools._adapter_seal is _CONCRETE_ADAPTER_SEAL
            and all(
                getattr(driver, name, None) is _retired_callback
                for name in ("_preown", "_start", "_prebootstrap", "_finish", "_stop")
            )
            and all(
                getattr(tools, name, object()) is None
                for name in (
                    "_node",
                    "_web_root",
                    "_next_cli",
                    "_playwright_cli",
                    "_epoch_clock",
                )
            )
        )
    except BaseException:
        return False


__all__: list[str] = []

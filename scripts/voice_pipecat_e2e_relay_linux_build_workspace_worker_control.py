"""Lock-free, path-free control handoff for the exact inert worker."""

from __future__ import annotations

import threading
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerController,
)

_BRIDGE_TOKEN = object()
_CAPTURE_CONTROL = _WorkspaceWorkerController._capture_control
_RAW_CLEARED = object()


class _WorkspaceWorkerControlBridge:
    """Sealed worker argument retaining only exact path-free control state."""

    __slots__ = ("_identity", "_raw_ref", "_state")

    def __init__(
        self,
        token: object,
        *,
        record_token: object,
        bridge_token: object,
        owner_token: object,
        controller: _WorkspaceWorkerController,
    ) -> None:
        if (
            token is not _BRIDGE_TOKEN
            or type(record_token) is not object
            or type(bridge_token) is not object
            or type(owner_token) is not object
            or type(controller) is not _WorkspaceWorkerController
            or not controller._matches(owner_token)
        ):
            raise TypeError("Relay Linux workspace worker control bridge is private")
        object.__setattr__(self, "_identity", (record_token, bridge_token))
        object.__setattr__(self, "_raw_ref", None)
        object.__setattr__(self, "_state", (owner_token, controller, object()))

    def _belongs(self, record_token: object, bridge_token: object) -> bool:
        identity = self._identity
        return bool(
            type(identity) is tuple
            and len(identity) == 2
            and identity[0] is record_token
            and identity[1] is bridge_token
        )

    def _matches(
        self,
        record_token: object,
        bridge_token: object,
        controller: _WorkspaceWorkerController,
    ) -> bool:
        state = self._state
        return bool(
            self._belongs(record_token, bridge_token)
            and type(state) is tuple
            and len(state) == 3
            and type(state[0]) is object
            and state[1] is controller
            and type(controller) is _WorkspaceWorkerController
            and controller._matches(state[0])
            and type(state[2]) is object
        )

    def _matches_current_worker(self) -> bool:
        identity = self._identity
        state = self._state
        raw_ref = self._raw_ref
        if (
            type(identity) is not tuple
            or len(identity) != 2
            or type(identity[0]) is not object
            or type(identity[1]) is not object
            or type(state) is not tuple
            or len(state) != 3
            or type(state[0]) is not object
            or type(state[1]) is not _WorkspaceWorkerController
            or not state[1]._matches(state[0])
            or type(state[2]) is not object
            or type(raw_ref) is not weakref.ReferenceType
        ):
            return False
        current = threading.current_thread()
        try:
            values = vars(current)
            return bool(
                raw_ref() is current
                and values.get("_workspace_control_token") is state[2]
                and values.get("_workspace_sealed") is True
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False

    def _bind_raw(self, raw: object) -> None:
        current = self._raw_ref
        if current is None:
            object.__setattr__(self, "_raw_ref", weakref.ref(raw))
            return
        if type(current) is not weakref.ReferenceType or current() is not raw:
            raise TypeError("Relay Linux workspace worker control bridge changed")

    def _clear_raw(self, raw: object | None) -> None:
        current = self._raw_ref
        if current is _RAW_CLEARED:
            return
        if current is not None and (
            type(current) is not weakref.ReferenceType or current() is not raw
        ):
            raise TypeError("Relay Linux workspace worker control bridge changed")
        if current is None and raw is not None:
            raise TypeError("Relay Linux workspace worker control bridge changed")
        object.__setattr__(self, "_raw_ref", _RAW_CLEARED)

    def _clear(self, raw: object | None) -> None:
        self._clear_raw(raw)
        object.__setattr__(self, "_state", None)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerControlBridge()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker control bridge is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker control bridge cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker control bridge cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker control bridge cannot be serialized")


def _new_workspace_worker_control_bridge(
    record_token: object,
    bridge_token: object,
    owner_token: object,
    controller: _WorkspaceWorkerController,
) -> _WorkspaceWorkerControlBridge:
    return _WorkspaceWorkerControlBridge(
        _BRIDGE_TOKEN,
        record_token=record_token,
        bridge_token=bridge_token,
        owner_token=owner_token,
        controller=controller,
    )


def _workspace_worker_control_bridge_belongs(
    bridge: object,
    record_token: object,
    bridge_token: object,
) -> bool:
    return bool(
        type(bridge) is _WorkspaceWorkerControlBridge
        and bridge._belongs(record_token, bridge_token)
    )


def _workspace_worker_control_bridge_matches(
    bridge: object,
    record_token: object,
    bridge_token: object,
    controller: _WorkspaceWorkerController,
) -> bool:
    return bool(
        type(bridge) is _WorkspaceWorkerControlBridge
        and bridge._matches(record_token, bridge_token, controller)
    )


def _workspace_worker_control_bridge_worker_token(
    bridge: object,
    record_token: object,
    bridge_token: object,
    controller: _WorkspaceWorkerController,
) -> object:
    if not _workspace_worker_control_bridge_matches(
        bridge,
        record_token,
        bridge_token,
        controller,
    ):
        raise TypeError("Relay Linux workspace worker control bridge is invalid")
    return bridge._state[2]


def _bind_workspace_worker_control_bridge(
    bridge: object,
    record_token: object,
    bridge_token: object,
    controller: _WorkspaceWorkerController,
    raw: object,
) -> object:
    worker_token = _workspace_worker_control_bridge_worker_token(
        bridge,
        record_token,
        bridge_token,
        controller,
    )
    bridge._bind_raw(raw)
    _workspace_worker_control_bridge_bound()
    return worker_token


def _workspace_worker_control_bridge_controller(
    bridge: object,
    record_token: object,
    bridge_token: object,
) -> _WorkspaceWorkerController:
    if (
        type(bridge) is not _WorkspaceWorkerControlBridge
        or not bridge._belongs(record_token, bridge_token)
        or type(bridge._state) is not tuple
        or type(bridge._state[1]) is not _WorkspaceWorkerController
    ):
        raise TypeError("Relay Linux workspace worker control bridge is invalid")
    return bridge._state[1]


def _workspace_worker_control_bridge_record_token(bridge: object) -> object:
    if type(bridge) is not _WorkspaceWorkerControlBridge or not bridge._matches_current_worker():
        raise TypeError("Relay Linux workspace worker control bridge is invalid")
    return bridge._identity[0]


def _capture_worker_control(
    bridge: object,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    """Latch and scrub a control only for the exact bound current worker."""

    if (
        type(bridge) is not _WorkspaceWorkerControlBridge
        or not bridge._matches_current_worker()
        or not isinstance(control, (KeyboardInterrupt, SystemExit))
    ):
        if isinstance(control, BaseException):
            _scrub_control_minimal(control)
        return
    _CAPTURE_CONTROL(bridge._state[1], control)


def _clear_workspace_worker_control_bridge(
    bridge: object,
    record_token: object,
    bridge_token: object,
    raw: object | None,
) -> None:
    if not _workspace_worker_control_bridge_belongs(
        bridge,
        record_token,
        bridge_token,
    ):
        raise TypeError("Relay Linux workspace worker control bridge is invalid")
    bridge._clear(raw)


def _clear_workspace_worker_control_bridge_raw(
    bridge: object,
    record_token: object,
    bridge_token: object,
    raw: object | None,
) -> None:
    if not _workspace_worker_control_bridge_belongs(
        bridge,
        record_token,
        bridge_token,
    ):
        raise TypeError("Relay Linux workspace worker control bridge is invalid")
    bridge._clear_raw(raw)


def _workspace_worker_thread_configuration_is_complete(
    candidate: object,
    *,
    thread_type: type,
    target: object,
    name: str,
    record_token: object,
    bridge_token: object,
    control_bridge: object,
    controller: _WorkspaceWorkerController,
) -> bool:
    """Recognize the exact sealed CPython Thread configuration."""

    try:
        if type(candidate) is not thread_type:
            return False
        values = vars(candidate)
        args = values.get("_args")
        started = values.get("_started")
        worker_token = _workspace_worker_control_bridge_worker_token(
            control_bridge,
            record_token,
            bridge_token,
            controller,
        )
        return bool(
            values.get("_initialized") is True
            and values.get("_workspace_sealed") is True
            and not {"is_alive", "join", "run", "start"}.intersection(values)
            and values.get("_target") is target
            and type(values.get("_name")) is str
            and values.get("_name") == name
            and type(args) is tuple
            and len(args) == 1
            and args[0] is control_bridge
            and type(control_bridge._raw_ref) is weakref.ReferenceType
            and control_bridge._raw_ref() is candidate
            and values.get("_workspace_control_token") is worker_token
            and type(values.get("_kwargs")) is dict
            and not values["_kwargs"]
            and values.get("_daemonic") is False
            and values.get("_ident") is None
            and values.get("_native_id") is None
            and type(started) is threading.Event
            and not started.is_set()
            and values.get("_is_stopped", False) is False
            and values.get("_tstate_lock") is None
            and ("_tstate_lock" in values or "_os_thread_handle" in values)
            and "_stderr" in values
            and callable(values.get("_invoke_excepthook"))
            and candidate in threading._dangling
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _inert_workspace_worker_target(bridge: object) -> None:
    """Run the lifecycle only from the bridge's exact current worker."""

    if type(bridge) is not _WorkspaceWorkerControlBridge or not bridge._matches_current_worker():
        return
    try:
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle import (
            _run_inert_workspace_worker,
        )

        _run_inert_workspace_worker(bridge)
    except (KeyboardInterrupt, SystemExit) as control:
        _capture_worker_control(bridge, control)
    except BaseException as failure:
        _scrub_control_minimal(failure)


def _workspace_worker_control_bridge_bound() -> None:
    pass


__all__: list[str] = []

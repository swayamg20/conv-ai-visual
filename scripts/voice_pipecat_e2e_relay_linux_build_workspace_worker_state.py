"""Inert caller-preowned state for the future relay build workspace worker.

This module performs no filesystem or thread operation.  It only binds one
existing workspace owner to sanitized cancellation state and empty publication
destinations which later reviewed worker slices may consume.
"""

from __future__ import annotations

import threading
import traceback

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)

_BUNDLE_TOKEN = object()
_CONTROL_TOKEN = object()
_CONTROLLER_TOKEN = object()
_DESTINATION_TOKEN = object()


class _WorkspaceWorkerControlSignal:
    """Immutable first-control value with no retained exception graph."""

    __slots__ = ("code", "kind")

    def __init__(self, token: object, *, kind: str, code: int | None) -> None:
        valid = bool(
            token is _CONTROL_TOKEN
            and kind in {"keyboard", "system-exit"}
            and (kind != "keyboard" or code is None)
            and (kind != "system-exit" or code is None or type(code) is int)
        )
        if not valid:
            raise TypeError("Relay Linux workspace worker control is factory-owned")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "code", code)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerControlSignal()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker control is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker control cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker control cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker control cannot be serialized")


class _WorkspaceWorkerController:
    """Sanitized cancellation state carrying no path or filesystem authority."""

    __slots__ = ("_cancel_requested", "_condition", "_control", "_owner_token")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _CONTROLLER_TOKEN or owner_token is None:
            raise TypeError("Relay Linux workspace worker controller is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_condition", threading.Condition())
        object.__setattr__(self, "_cancel_requested", False)
        object.__setattr__(self, "_control", None)

    def _matches(self, owner_token: object) -> bool:
        return owner_token is self._owner_token

    def _request_cancel(self) -> None:
        with self._condition:
            object.__setattr__(self, "_cancel_requested", True)
            self._condition.notify_all()

    def _cancellation_requested(self) -> bool:
        with self._condition:
            return self._cancel_requested

    def _capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        retained: list[BaseException] = [error]
        signal: _WorkspaceWorkerControlSignal | None = None
        fallback = False
        while signal is None:
            try:
                signal = _fallback_control_signal(error) if fallback else _control_signal(error)
            except (KeyboardInterrupt, SystemExit) as nested:
                retained.append(nested)
                fallback = True
            except BaseException as failure:
                _scrub_control_minimal(failure)
                if fallback:
                    signal = _default_control_signal(error)
                else:
                    fallback = True
        latched = False
        while True:
            try:
                if not latched:
                    with self._condition:
                        if self._control is None:
                            object.__setattr__(self, "_control", signal)
                        object.__setattr__(self, "_cancel_requested", True)
                        self._condition.notify_all()
                    latched = True
                if not retained:
                    return
                current = retained.pop()
                try:
                    _scrub_control(current)
                except (KeyboardInterrupt, SystemExit) as nested:
                    retained.append(nested)
                    _scrub_control_minimal(current)
                except BaseException:
                    _scrub_control_minimal(current)
            except (KeyboardInterrupt, SystemExit) as nested:
                retained.append(nested)
            except BaseException as failure:
                _scrub_control_minimal(failure)
                if retained:
                    _scrub_control_minimal(retained.pop())

    def _control_value(self) -> _WorkspaceWorkerControlSignal | None:
        with self._condition:
            return self._control

    def _wait(self, timeout: float) -> None:
        with self._condition:
            self._condition.wait(timeout)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerController()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker controller is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker controller cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker controller cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker controller cannot be serialized")


class _WorkspaceWorkerDestination:
    """Empty exact-purpose slot; this checkpoint exposes no publisher."""

    __slots__ = ("_kind", "_owner_token", "_value")

    def __init__(
        self,
        token: object,
        *,
        kind: str,
        owner_token: object,
    ) -> None:
        if (
            token is not _DESTINATION_TOKEN
            or kind not in {"thread", "built", "terminal"}
            or owner_token is None
        ):
            raise TypeError("Relay Linux workspace worker destination is factory-owned")
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_value", None)

    def _read(self, owner_token: object) -> None:
        if owner_token is not self._owner_token:
            raise TypeError("Relay Linux workspace worker destination is invalid")
        return self._value

    def _matches(self, owner_token: object, kind: str) -> bool:
        return self._owner_token is owner_token and self._kind == kind

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker destination cannot be serialized")


class _WorkspaceWorkerBundle:
    """One canonical inert graph; only its prepared slot retains the request."""

    __slots__ = (
        "_built_destination",
        "_controller",
        "_owner_token",
        "_prepared_destination",
        "_terminal_destination",
        "_thread_destination",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        prepared_destination: object,
    ) -> None:
        if token is not _BUNDLE_TOKEN or owner_token is None or prepared_destination is None:
            raise TypeError("Relay Linux workspace worker bundle is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(
            self,
            "_controller",
            _WorkspaceWorkerController(_CONTROLLER_TOKEN, owner_token=owner_token),
        )
        object.__setattr__(self, "_prepared_destination", prepared_destination)
        for field, kind in (
            ("_thread_destination", "thread"),
            ("_built_destination", "built"),
            ("_terminal_destination", "terminal"),
        ):
            object.__setattr__(
                self,
                field,
                _WorkspaceWorkerDestination(
                    _DESTINATION_TOKEN,
                    kind=kind,
                    owner_token=owner_token,
                ),
            )

    def _matches(self, owner_token: object, prepared_destination: object) -> bool:
        return bool(
            owner_token is self._owner_token
            and prepared_destination is self._prepared_destination
            and self._controller._matches(owner_token)
            and self._thread_destination._matches(owner_token, "thread")
            and self._built_destination._matches(owner_token, "built")
            and self._terminal_destination._matches(owner_token, "terminal")
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerBundle()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker bundle is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker bundle cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker bundle cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker bundle cannot be serialized")


def _new_relay_linux_build_workspace_worker_bundle(
    owner: _RelayLinuxBuildWorkspaceOwner,
) -> _WorkspaceWorkerBundle:
    """Return the owner's canonical inert bundle without performing an effect."""

    try:
        if type(
            owner
        ) is not _RelayLinuxBuildWorkspaceOwner or not owner._cleanup_authority._matches(
            owner._request
        ):
            raise TypeError
        owner_token = owner._cleanup_authority._key
        destination = owner._worker_bundle_destination
        existing = destination._read(owner._request)
        if existing is None:
            candidate = _WorkspaceWorkerBundle(
                _BUNDLE_TOKEN,
                owner_token=owner_token,
                prepared_destination=owner._receipt_destination,
            )
            existing = destination._publish(owner._request, candidate)
        if type(existing) is not _WorkspaceWorkerBundle or not existing._matches(
            owner_token,
            owner._receipt_destination,
        ):
            raise TypeError
        return existing
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        _scrub_control_minimal(error)
        raise TypeError("Relay Linux workspace worker owner is invalid") from None


def _control_signal(
    error: KeyboardInterrupt | SystemExit,
) -> _WorkspaceWorkerControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="keyboard", code=None)
    if not isinstance(error, SystemExit):
        raise TypeError("Relay Linux workspace worker control is invalid")
    raw_code = BaseException.__getattribute__(error, "code")
    code = raw_code if raw_code is None or type(raw_code) is int else 1
    return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="system-exit", code=code)


def _fallback_control_signal(
    error: KeyboardInterrupt | SystemExit,
) -> _WorkspaceWorkerControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="keyboard", code=None)
    try:
        raw_code = BaseException.__getattribute__(error, "code")
    except BaseException:
        raw_code = 1
    code = raw_code if raw_code is None or type(raw_code) is int else 1
    return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="system-exit", code=code)


def _default_control_signal(
    error: KeyboardInterrupt | SystemExit,
) -> _WorkspaceWorkerControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="keyboard", code=None)
    return _WorkspaceWorkerControlSignal(_CONTROL_TOKEN, kind="system-exit", code=1)


def _scrub_control(error: BaseException) -> None:
    trace = BaseException.__getattribute__(error, "__traceback__")
    BaseException.__setattr__(error, "__traceback__", None)
    BaseException.__setattr__(error, "__cause__", None)
    BaseException.__setattr__(error, "__context__", None)
    BaseException.__setattr__(error, "__suppress_context__", True)
    if trace is not None:
        traceback.clear_frames(trace)


def _scrub_control_minimal(error: BaseException) -> None:
    try:
        trace = BaseException.__getattribute__(error, "__traceback__")
        BaseException.__setattr__(error, "__traceback__", None)
        BaseException.__setattr__(error, "__cause__", None)
        BaseException.__setattr__(error, "__context__", None)
        BaseException.__setattr__(error, "__suppress_context__", True)
        if trace is not None:
            try:
                traceback.clear_frames(trace)
            except BaseException:
                pass
    except BaseException:
        pass


__all__: list[str] = []

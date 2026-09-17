"""Sanitized cross-thread state for the private relay B0 build worker."""

from __future__ import annotations

import math
import threading
import traceback

_CONTROLLER_TOKEN = object()
_CLAIM_TOKEN = object()
_CONTROL_TOKEN = object()
_TERMINAL_TOKEN = object()
_ALLOWED_PHASES: dict[str, frozenset[str]] = {
    "registered": frozenset({"spawning", "settled"}),
    "spawning": frozenset({"running", "term-grace", "quarantined", "settled"}),
    "running": frozenset({"term-grace", "verifying", "quarantined"}),
    "term-grace": frozenset({"killing", "verifying", "quarantined"}),
    "killing": frozenset({"verifying", "quarantined"}),
    "quarantined": frozenset({"verifying"}),
    "verifying": frozenset({"settled", "quarantined"}),
    "settled": frozenset(),
}


class _BuildControlSignal:
    """Immutable first-control value; never retains an exception traceback."""

    __slots__ = ("code", "kind")

    def __init__(self, token: object, *, kind: str, code: int | None) -> None:
        if token is not _CONTROL_TOKEN or kind not in {"keyboard", "system-exit"}:
            raise TypeError("Relay Linux build control signal is factory-owned")
        if kind == "keyboard" and code is not None:
            raise TypeError("Relay Linux build control signal is invalid")
        if kind == "system-exit" and code is not None and type(code) is not int:
            raise TypeError("Relay Linux build control signal is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "code", code)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildControlSignal()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build control signal is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build control signal cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build control signal cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build control signal cannot be serialized")


class _BuildWorkerClaim:
    """Immutable capability minted only for the exact current registered worker."""

    __slots__ = ("_owner_token", "_worker")

    def __init__(self, token: object, *, owner_token: object, worker: object) -> None:
        if token is not _CLAIM_TOKEN or worker is None:
            raise TypeError("Relay Linux build worker claim is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_worker", worker)

    def _matches(self, owner_token: object, worker: object) -> bool:
        return self._owner_token is owner_token and self._worker is worker

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerClaim()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker claim is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker claim cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker claim cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker claim cannot be serialized")


class _BuildWorkerTerminal:
    """Sanitized proof that the sole worker has dropped all child authority."""

    __slots__ = ("_owner_token", "returncode", "succeeded")

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        returncode: int | None,
        succeeded: bool,
    ) -> None:
        valid = bool(
            token is _TERMINAL_TOKEN
            and type(succeeded) is bool
            and (returncode is None or type(returncode) is int)
            and (not succeeded or returncode == 0)
        )
        if not valid:
            raise TypeError("Relay Linux build worker terminal is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "returncode", returncode)
        object.__setattr__(self, "succeeded", succeeded)

    def _matches(self, owner_token: object) -> bool:
        return self._owner_token is owner_token

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerTerminal()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker terminal is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker terminal cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker terminal cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker terminal cannot be serialized")


class _BuildWorkerController:
    """Sanitized state only; this slice admits no child or numeric authority."""

    __slots__ = (
        "_condition",
        "_control",
        "_failure",
        "_owner_token",
        "_phase",
        "_run_deadline",
        "_terminate_requested",
        "_thread_identity",
    )

    def __init__(self, token: object, *, owner_token: object, run_deadline: float) -> None:
        if (
            token is not _CONTROLLER_TOKEN
            or type(run_deadline) is not float
            or not math.isfinite(run_deadline)
            or run_deadline <= 0.0
        ):
            raise TypeError("Relay Linux build worker controller is factory-owned")
        object.__setattr__(self, "_condition", threading.Condition())
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_run_deadline", run_deadline)
        object.__setattr__(self, "_phase", "registered")
        object.__setattr__(self, "_terminate_requested", False)
        object.__setattr__(self, "_failure", False)
        object.__setattr__(self, "_control", None)
        object.__setattr__(self, "_thread_identity", None)

    def _matches(self, owner_token: object, run_deadline: float | None = None) -> bool:
        with self._condition:
            return self._owner_token is owner_token and (
                run_deadline is None or self._run_deadline == run_deadline
            )

    def _publish_thread(self, thread: object) -> bool:
        if thread is None:
            return False
        with self._condition:
            if self._thread_identity is None:
                object.__setattr__(self, "_thread_identity", thread)
            valid = self._thread_identity is thread
            self._condition.notify_all()
            return valid

    def _thread(self) -> object | None:
        with self._condition:
            return self._thread_identity

    def _transition(self, phase: str) -> bool:
        with self._condition:
            if phase not in _ALLOWED_PHASES.get(self._phase, frozenset()):
                return False
            object.__setattr__(self, "_phase", phase)
            self._condition.notify_all()
            return True

    def _phase_value(self) -> str:
        with self._condition:
            return self._phase

    def _request_termination(self) -> None:
        with self._condition:
            object.__setattr__(self, "_terminate_requested", True)
            self._condition.notify_all()

    def _termination_requested(self) -> bool:
        with self._condition:
            return self._terminate_requested

    def _fail(self) -> None:
        with self._condition:
            object.__setattr__(self, "_failure", True)
            object.__setattr__(self, "_terminate_requested", True)
            self._condition.notify_all()

    def _failed(self) -> bool:
        with self._condition:
            return self._failure

    def _capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        retained = [error]
        signal: _BuildControlSignal | None = None
        while signal is None:
            try:
                signal = _control_signal(error)
            except (KeyboardInterrupt, SystemExit) as nested:
                retained.append(nested)
            except BaseException:
                self._fail()
                retained.clear()
                return
        self._capture_control_signal_retry(signal, retained)

    def _capture_control_signal(self, signal: _BuildControlSignal) -> None:
        if type(signal) is not _BuildControlSignal:
            self._fail()
            return
        self._capture_control_signal_retry(signal, [])

    def _capture_control_signal_retry(
        self,
        signal: _BuildControlSignal,
        retained: list[BaseException],
    ) -> None:
        latched = False
        while True:
            try:
                if not latched:
                    with self._condition:
                        if self._control is None:
                            object.__setattr__(self, "_control", signal)
                        object.__setattr__(self, "_terminate_requested", True)
                        self._condition.notify_all()
                    latched = True
                if not retained:
                    return
                error = retained[-1]
                _scrub_control(error)
                retained.pop()
            except (KeyboardInterrupt, SystemExit) as nested:
                retained.append(nested)
            except BaseException:
                continue

    def _control_value(self) -> _BuildControlSignal | None:
        with self._condition:
            return self._control

    def _wait(self, timeout: float) -> None:
        with self._condition:
            self._condition.wait(timeout)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_BuildWorkerController()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux build worker controller is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux build worker controller cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux build worker controller cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux build worker controller cannot be serialized")


def _new_build_worker_controller(
    *, owner_token: object, run_deadline: float
) -> _BuildWorkerController:
    return _BuildWorkerController(
        _CONTROLLER_TOKEN,
        owner_token=owner_token,
        run_deadline=run_deadline,
    )


def _new_build_worker_claim(*, owner_token: object, worker: object) -> _BuildWorkerClaim:
    return _BuildWorkerClaim(
        _CLAIM_TOKEN,
        owner_token=owner_token,
        worker=worker,
    )


def _new_build_worker_terminal(
    *,
    owner_token: object,
    returncode: int | None,
    succeeded: bool,
) -> _BuildWorkerTerminal:
    return _BuildWorkerTerminal(
        _TERMINAL_TOKEN,
        owner_token=owner_token,
        returncode=returncode,
        succeeded=succeeded,
    )


def _control_signal(error: KeyboardInterrupt | SystemExit) -> _BuildControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return _BuildControlSignal(_CONTROL_TOKEN, kind="keyboard", code=None)
    if not isinstance(error, SystemExit):
        raise TypeError("Relay Linux build control is invalid")
    raw_code = BaseException.__getattribute__(error, "code")
    code = raw_code if raw_code is None or type(raw_code) is int else 1
    return _BuildControlSignal(_CONTROL_TOKEN, kind="system-exit", code=code)


def _scrub_control(error: BaseException) -> None:
    trace = BaseException.__getattribute__(error, "__traceback__")
    BaseException.__setattr__(error, "__traceback__", None)
    BaseException.__setattr__(error, "__cause__", None)
    BaseException.__setattr__(error, "__context__", None)
    BaseException.__setattr__(error, "__suppress_context__", True)
    if trace is not None:
        traceback.clear_frames(trace)


__all__: list[str] = []

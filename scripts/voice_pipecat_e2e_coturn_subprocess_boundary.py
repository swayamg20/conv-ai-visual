"""Control-closed public exception boundaries for the subprocess facade."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from scripts.voice_pipecat_e2e_coturn_subprocess_state import Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_supervisor import (
    SupervisorSlot,
    cancel_supervisor_slot,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    ControlSignal,
    CoturnSubprocessError,
    control_signal,
    raise_control,
    raise_subprocess_error,
)

_P = ParamSpec("_P")
_R = TypeVar("_R")

_SAFE_FAILURES = frozenset(
    {
        "Coturn subprocess chunk limit exceeded",
        "Coturn subprocess cleanup is quarantined",
        "Coturn subprocess cleanup proof is invalid",
        "Coturn subprocess clock failed",
        "Coturn subprocess collection timed out",
        "Coturn subprocess collection timeout is invalid",
        "Coturn subprocess command limit exceeded",
        "Coturn subprocess execution failed",
        "Coturn subprocess executor is invalid",
        "Coturn subprocess input failed",
        "Coturn subprocess output limit exceeded",
        "Coturn subprocess read timeout is invalid",
        "Coturn subprocess recovery timeout is invalid",
        "Coturn subprocess request is invalid",
        "Coturn subprocess result is invalid",
        "Coturn subprocess runner is poisoned",
        "Coturn subprocess selector failed",
        "Coturn subprocess start failed",
        "Coturn subprocess start identity is invalid",
        "Coturn subprocess start validation failed",
        "Coturn subprocess stream failed",
        "Coturn subprocess supervisor start failed",
        "Coturn subprocess supervisor state is invalid",
        "Coturn subprocess synchronization failed",
        "Coturn subprocess timed out",
    }
)


class RunnerBoundaryMixin:
    """Control latch and terminal slot removal shared by runner boundaries."""

    def _latch_control(self, control: ControlSignal | None) -> None:
        try: return self._latch_control_retry(control)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit):
            self._latch_control_retry(control)
        except BaseException:
            self._latch_control_retry(control)

    def _latch_control_retry(self, control: ControlSignal | None) -> None:
        if control is None:
            return
        while True:
            try:
                with self._lock:
                    if self._control_latch is None:
                        self._control_latch = control
                return
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                continue

    def _drop_reservation_authority(
        self,
        slot: SupervisorSlot,
        entry: Callable[[], None],
        dropped: Callable[[], None],
    ) -> None:
        slot.drop_reservation(self._lock, self._slots, entry, dropped)
        self._latch_control(slot.controller.control())

    def _consume_control_latch(
        self,
        fallback: ControlSignal | None = None,
    ) -> ControlSignal | None:
        try: return self._consume_control_latch_retry(fallback)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            self._latch_control(control_signal(error))
        except BaseException:
            pass
        return self._consume_control_latch_retry(fallback)

    def _consume_control_latch_retry(
        self,
        fallback: ControlSignal | None,
    ) -> ControlSignal | None:
        retained: ControlSignal | None = None
        while True:
            try:
                with self._lock:
                    retained = retained or self._control_latch or fallback
                    if retained is not None and self._control_latch is None:
                        self._control_latch = retained
                    _control_latch_ready()
                    self._control_latch = None
                    _control_latch_cleared()
                    return retained
            except (KeyboardInterrupt, SystemExit) as error:
                self._latch_control(retained or control_signal(error))
            except BaseException:
                self._latch_control(retained)

    def _boundary_abort(
        self,
        *,
        control: ControlSignal | None,
        failure: str | None,
        uncertain: bool,
    ) -> ControlSignal | None:
        del failure
        with self._lock:
            slots = tuple(self._slots)
        authoritative = self._consume_control_latch()
        if control is None and not uncertain and authoritative is None:
            return None
        for slot in slots:
            authoritative = authoritative or slot.controller.control()
        for slot in slots:
            if control is not None:
                slot.controller.capture_control_signal(control)
            authoritative = authoritative or slot.controller.control()
        authoritative = authoritative or control
        for slot in slots:
            if slot.thread is None:
                if slot.cancel_admission():
                    authoritative = authoritative or slot.controller.control()
                    continue
                cancel_supervisor_slot(slot)
                authoritative = authoritative or slot.controller.control()
                if (
                    slot.controller.lifecycle() is Lifecycle.REGISTERED
                    and slot.pending_launch() is None
                ):
                    self._drop_reservation(slot)
                    authoritative = authoritative or slot.controller.control()
                    continue
            if control is None:
                slot.controller.request_termination()
            self._await_slot_terminal(slot)
            authoritative = authoritative or slot.controller.control()
            if slot.controller.lifecycle() is Lifecycle.CLEAN:
                joined = slot.join_if_clean()
                authoritative = authoritative or slot.controller.control()
                if joined:
                    self._settled(slot)
            authoritative = authoritative or slot.controller.control()
        return self._consume_control_latch(authoritative)

    def _assert_result_allowed(self, own_slot: SupervisorSlot) -> None:
        with self._lock:
            if any(slot is not own_slot and slot.controller.poisoned() for slot in self._slots):
                raise_subprocess_error("Coturn subprocess runner is poisoned")
        if own_slot.controller.poisoned():
            raise_subprocess_error("Coturn subprocess cleanup is quarantined")

    def _settled(self, slot: SupervisorSlot) -> None:
        with self._lock:
            control = slot.controller.control()
            if control is not None and self._control_latch is None:
                self._control_latch = control
            if slot.controller.clean_joined() and slot in self._slots:
                self._slots.remove(slot)

    def _discard_joined_locked(self) -> ControlSignal | None:
        discarded = [slot for slot in self._slots if slot.controller.clean_joined()]
        control = next(
            (slot.controller.control() for slot in discarded if slot.controller.control()),
            None,
        )
        if control is not None and self._control_latch is None:
            self._control_latch = control
        self._slots[:] = [slot for slot in self._slots if slot not in discarded]
        return control

    _control_latch: ControlSignal | None
    _lock: Any
    _slots: list[SupervisorSlot]


class _BoundaryCall:
    """Retain abort authority until a success return or terminal failure."""

    __slots__ = (
        "abort_complete",
        "args",
        "control",
        "entry_complete",
        "failure",
        "fallback",
        "invoked",
        "kwargs",
        "method",
        "owner",
        "result",
        "succeeded",
        "success_complete",
    )

    def __init__(
        self,
        method: Callable[..., object],
        args: tuple[object, ...],
        kwargs: dict[str, object],
        fallback: str,
    ) -> None:
        self.method: Callable[..., object] | None = method
        self.args = args
        self.kwargs = dict(kwargs)
        self.fallback = fallback
        self.owner: Any = args[0] if args else None
        self.result: object = None
        self.control: ControlSignal | None = None
        self.failure: str | None = None
        self.entry_complete = False
        self.invoked = False
        self.succeeded = False
        self.success_complete = False
        self.abort_complete = False

    def advance(self) -> bool:
        if not self.entry_complete:
            _boundary_entry()
            self.entry_complete = True
        if self.succeeded and not self.success_complete:
            _boundary_success()
            self.success_complete = True
        if self.control is not None or self.failure is not None:
            self._abort()
            return False
        if not self.invoked:
            self.invoked = True
            method = self.method
            if method is None:
                self.failure = self.fallback
            else:
                self.result = method(*self.args, **self.kwargs)
                self.succeeded = True
        if self.succeeded and not self.success_complete:
            _boundary_success()
            self.success_complete = True
        if self.control is None and self.failure is None and self.succeeded:
            return True
        self._abort()
        return False

    def capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        try: return self._capture_control_retry(error)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit):
            self._capture_control_retry(error)
        except BaseException:
            self._capture_control_retry(error)

    def _capture_control_retry(self, error: KeyboardInterrupt | SystemExit) -> None:
        while True:
            try:
                self.capture_signal(control_signal(error))
                return
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                continue

    def capture_signal(self, signal: ControlSignal) -> None:
        if self.control is None:
            self.control = signal

    def capture_failure(self, failure: str) -> None:
        if self.failure is None:
            self.failure = failure
        _boundary_failure_published()

    def scrub(self) -> None:
        self.args = ()
        self.kwargs.clear()
        self.owner = None
        self.result = None
        self.method = None

    def _abort(self) -> None:
        if self.abort_complete:
            return
        abort = getattr(self.owner, "_boundary_abort", None)
        if callable(abort):
            authoritative = abort(
                control=self.control,
                failure=self.failure,
                uncertain=self.succeeded,
            )
            if authoritative is not None:
                self.control = authoritative
        self.abort_complete = True

    def __repr__(self) -> str:
        return "_BoundaryCall()"


def public_boundary(
    fallback: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Drop all raw graphs and finish abort before emitting a fresh exception."""

    def decorate(method: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(method)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try: call = _BoundaryCall(method, args, kwargs, fallback)  # noqa: E701  # fmt: skip
            except (KeyboardInterrupt, SystemExit) as error:
                try: call = _make_control_boundary_call(method, args, kwargs, fallback, error)  # noqa: E701  # fmt: skip
                except (KeyboardInterrupt, SystemExit):
                    call = _make_control_boundary_call(method, args, kwargs, fallback, error)
                except BaseException:
                    call = _make_control_boundary_call(method, args, kwargs, fallback, error)
            except BaseException:
                call = _make_boundary_call(
                    method,
                    args,  # type: ignore[arg-type]
                    kwargs,  # type: ignore[arg-type]
                    fallback,
                    failure=fallback,
                )
            pending_failure: CoturnSubprocessError | None = None
            pending_generic_failure = False
            while True:
                try:
                    if pending_failure is not None:
                        _capture_boundary_failure(call, pending_failure, fallback)
                        pending_failure = None
                        continue
                    if pending_generic_failure:
                        _capture_boundary_failure(call, None, fallback)
                        pending_generic_failure = False
                        continue
                    args = ()  # type: ignore[assignment]
                    kwargs.clear()
                    if call.advance():
                        return call.result  # type: ignore[return-value]
                    _boundary_final_scrub()
                    final_control = call.control
                    final_failure = call.failure or fallback
                    call.scrub()
                    break
                except (KeyboardInterrupt, SystemExit) as error:
                    try: _capture_boundary_control(call, error)  # noqa: E701  # fmt: skip
                    except (KeyboardInterrupt, SystemExit):
                        _capture_boundary_control(call, error)
                    except BaseException:
                        _capture_boundary_control(call, error)
                except CoturnSubprocessError as error:
                    pending_failure = error
                except BaseException:
                    pending_generic_failure = True
            if final_control is not None:
                raise_control(final_control)
            raise_subprocess_error(final_failure)

        return wrapped

    return decorate


def _make_boundary_call(
    method: Callable[..., object],
    args: tuple[object, ...],
    kwargs: dict[str, object],
    fallback: str,
    *,
    control_error: KeyboardInterrupt | SystemExit | None = None,
    failure: str | None = None,
) -> _BoundaryCall:
    first_control_error = control_error
    first_failure = failure
    while True:
        try:
            call = _BoundaryCall(method, args, kwargs, fallback)
            if first_control_error is not None:
                call.capture_control(first_control_error)
            if first_failure is not None:
                call.capture_failure(first_failure)
            return call
        except (KeyboardInterrupt, SystemExit) as error:
            if first_control_error is None:
                first_control_error = error
        except BaseException:
            if first_failure is None:
                first_failure = fallback


def _make_control_boundary_call(
    method: Callable[..., object],
    args: tuple[object, ...],
    kwargs: dict[str, object],
    fallback: str,
    error: KeyboardInterrupt | SystemExit,
) -> _BoundaryCall:
    try: return _make_boundary_call(method, args, kwargs, fallback, control_error=error)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit):
        return _make_boundary_call(method, args, kwargs, fallback, control_error=error)
    except BaseException:
        return _make_boundary_call(method, args, kwargs, fallback, control_error=error)


def _capture_boundary_control(
    call: _BoundaryCall,
    error: KeyboardInterrupt | SystemExit,
) -> None:
    try: return _capture_boundary_control_loop(call, error)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit):
        _capture_boundary_control_loop(call, error)
    except BaseException:
        _capture_boundary_control_loop(call, error)


def _capture_boundary_control_loop(
    call: _BoundaryCall,
    error: KeyboardInterrupt | SystemExit,
) -> None:
    while True:
        try:
            _boundary_control_handler_entry()
            call.capture_control(error)
            return
        except (KeyboardInterrupt, SystemExit):
            continue
        except BaseException:
            continue


def _capture_boundary_failure(
    call: _BoundaryCall,
    error: CoturnSubprocessError | None,
    fallback: str,
) -> None:
    try: return _capture_boundary_failure_loop(call, error, fallback)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit) as nested:
        _capture_boundary_failure_loop(call, error, fallback, first_control=nested)
    except BaseException:
        _capture_boundary_failure_loop(call, None, fallback)


def _capture_boundary_failure_loop(
    call: _BoundaryCall,
    error: CoturnSubprocessError | None,
    fallback: str,
    *,
    first_control: KeyboardInterrupt | SystemExit | None = None,
) -> None:
    pending_control = first_control
    pending_error = error
    while True:
        try:
            if pending_control is not None:
                call.capture_control(pending_control)
                pending_control = None
            _boundary_failure_handler_entry()
            failure = fallback if pending_error is None else _safe_failure(pending_error, fallback)
            pending_error = None
            call.capture_failure(failure)
            return
        except (KeyboardInterrupt, SystemExit) as nested:
            if pending_control is None and call.control is None:
                pending_control = nested
        except BaseException:
            pending_error = None


def _safe_failure(error: CoturnSubprocessError, fallback: str) -> str:
    if type(error) is not CoturnSubprocessError:
        return fallback
    arguments = error.args
    if (
        type(arguments) is tuple
        and len(arguments) == 1
        and type(arguments[0]) is str
        and arguments[0] in _SAFE_FAILURES
    ):
        return arguments[0]
    return fallback


def _boundary_entry() -> None:
    """Deterministic control seam before the public method body."""


def _boundary_success() -> None:
    """Deterministic control seam after a result exists but before publication."""


def _boundary_final_scrub() -> None:
    """Deterministic control seam before dropping terminal raw facade graphs."""


def _boundary_control_handler_entry() -> None:
    """Deterministic nested-control seam while the first raw control is retained."""


def _boundary_failure_handler_entry() -> None:
    """Deterministic control seam while a normal failure remains retained."""


def _boundary_failure_published() -> None:
    """Deterministic control seam after sanitized failure publication."""


def _control_latch_ready() -> None:
    """Control seam while the earliest sanitized public signal is retained."""


def _control_latch_cleared() -> None:
    """Control seam after local authority survives runner-latch clearing."""


__all__ = ["public_boundary"]

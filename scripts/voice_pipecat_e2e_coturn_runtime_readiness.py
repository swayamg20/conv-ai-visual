"""One bounded deadline shared by post-start and OpenSSL readiness."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_host import CommandRequest
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_BUDGET_TOKEN = object()
_MAX_ATTEMPTS = 16
_MAX_WINDOW_SECONDS = 60.0
_MIN_COMMAND_SECONDS = 0.1
_RETRY_INTERVAL_SECONDS = 0.05


class RuntimeReadinessBudget:
    """Factory-owned serial budget spanning container and TLS readiness."""

    __slots__ = (
        "_attempts",
        "_clock",
        "_container_record",
        "_deadline",
        "_lock",
        "_openssl_record",
        "_state",
        "_wait",
    )

    def __init__(
        self,
        token: object,
        *,
        absolute_deadline: float,
        clock: Callable[[], float],
        wait: Callable[[float], None],
    ) -> None:
        if token is not _BUDGET_TOKEN:
            raise TypeError("Coturn runtime readiness budget is factory-owned")
        now = _read_clock(clock)
        if (
            not callable(wait)
            or type(absolute_deadline) is not float
            or not math.isfinite(absolute_deadline)
            or not _MIN_COMMAND_SECONDS <= float(absolute_deadline) - now <= _MAX_WINDOW_SECONDS
        ):
            raise CoturnRuntimeError("Coturn runtime readiness budget is invalid")
        self._deadline = float(absolute_deadline)
        self._clock = clock
        self._wait = wait
        self._attempts = 0
        self._container_record: tuple[object, object] | None = None
        self._openssl_record: tuple[object, object] | None = None
        self._state = "container"
        self._lock = threading.Lock()

    def _prepare_request(self, phase: str, request: CommandRequest) -> CommandRequest:
        if type(request) is not CommandRequest:
            self._fail()
            raise CoturnRuntimeError("Coturn runtime readiness budget is invalid")
        now = self._now()
        with self._lock:
            remaining = self._deadline - now
            if (
                self._state != phase
                or self._attempts >= _MAX_ATTEMPTS
                or remaining < _MIN_COMMAND_SECONDS
            ):
                self._state = "failed"
                raise CoturnRuntimeError("Coturn runtime readiness budget is exhausted")
            self._attempts += 1
            timeout_seconds = min(request.timeout_seconds, remaining)
        return CommandRequest(
            argv=request.argv,
            environment=request.environment,
            stdin=request.stdin,
            timeout_seconds=timeout_seconds,
            maximum_output_bytes=request.maximum_output_bytes,
            umask=request.umask,
        )

    def _retry(self, phase: str) -> None:
        now = self._now()
        with self._lock:
            remaining = self._deadline - now
            if (
                self._state != phase
                or self._attempts >= _MAX_ATTEMPTS
                or remaining <= _MIN_COMMAND_SECONDS
            ):
                self._state = "failed"
                raise CoturnRuntimeError("Coturn runtime readiness budget is exhausted")
            delay = min(_RETRY_INTERVAL_SECONDS, remaining - _MIN_COMMAND_SECONDS)
            waiter = self._wait
        result: object = object()
        try:
            result = waiter(delay)
        except (KeyboardInterrupt, SystemExit):
            self._fail()
            raise
        except BaseException:
            self._fail()
            raise CoturnRuntimeError("Coturn runtime readiness wait failed") from None
        if result is not None:
            self._fail()
            raise CoturnRuntimeError("Coturn runtime readiness wait failed")
        result = None
        if self._deadline - self._now() < _MIN_COMMAND_SECONDS:
            self._fail()
            raise CoturnRuntimeError("Coturn runtime readiness budget is exhausted")

    def _cached_container(self, owner: object) -> object | None:
        with self._lock:
            record = self._container_record
            if self._state == "container" and record is not None:
                if record[0] is not owner:
                    self._state = "failed"
                    raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
                self._state = "openssl"
            if self._state == "openssl" and record is not None and record[0] is owner:
                return record[1]
            return None

    def _container_ready(self, owner: object, proof: object) -> object:
        with self._lock:
            record = self._container_record
            if self._state == "container":
                if record is None:
                    self._container_record = (owner, proof)
                    record = self._container_record
                elif record[0] is not owner:
                    self._state = "failed"
                    raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
                self._state = "openssl"
            elif self._state != "openssl" or record is None or record[0] is not owner:
                self._state = "failed"
                raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
            return record[1]

    def _cached_openssl(self, owner: object) -> object | None:
        with self._lock:
            record = self._openssl_record
            if self._state == "openssl" and record is not None:
                if record[0] is not owner:
                    self._state = "failed"
                    raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
                self._state = "complete"
            if self._state == "complete" and record is not None and record[0] is owner:
                return record[1]
            return None

    def _openssl_ready(self, owner: object, proof: object) -> object:
        with self._lock:
            record = self._openssl_record
            if self._state == "openssl":
                if record is None:
                    self._openssl_record = (owner, proof)
                    record = self._openssl_record
                elif record[0] is not owner:
                    self._state = "failed"
                    raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
                self._state = "complete"
            elif self._state != "complete" or record is None or record[0] is not owner:
                self._state = "failed"
                raise CoturnRuntimeError("Coturn runtime readiness order is invalid")
            return record[1]

    def _fail(self) -> None:
        with self._lock:
            self._state = "failed"

    def _now(self) -> float:
        try:
            return _read_clock(self._clock)
        except BaseException:
            self._fail()
            raise

    def __repr__(self) -> str:
        return "RuntimeReadinessBudget()"


def create_runtime_readiness_budget(
    *,
    absolute_deadline: float,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> RuntimeReadinessBudget:
    """Validate and create one deadline owner before post-start inspection."""

    budget: RuntimeReadinessBudget | None = None
    control: ControlSignal | None = None
    try:
        budget = RuntimeReadinessBudget(
            _BUDGET_TOKEN,
            absolute_deadline=absolute_deadline,
            clock=clock,
            wait=wait,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    absolute_deadline = 0.0
    clock = wait = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if budget is None:
        raise CoturnRuntimeError("Coturn runtime readiness budget is invalid") from None
    return budget


def _read_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if type(value) is not float or not math.isfinite(value):
        raise CoturnRuntimeError("Coturn runtime readiness clock is invalid")
    return value


__all__ = ["RuntimeReadinessBudget", "create_runtime_readiness_budget"]

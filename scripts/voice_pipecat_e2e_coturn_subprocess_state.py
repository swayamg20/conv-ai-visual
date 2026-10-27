"""Sanitized cross-thread state for the Coturn subprocess supervisor."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    MAX_QUEUED_CHUNKS,
    ControlSignal,
    SubprocessChunk,
    control_signal,
)


class Lifecycle(Enum):
    REGISTERED = auto()
    CLEANUP_READY = auto()
    SPAWNING = auto()
    OWNED = auto()
    ACTIVE = auto()
    DRAINING = auto()
    TERM_GRACE = auto()
    KILLING = auto()
    VERIFYING = auto()
    CLEAN = auto()
    QUARANTINED = auto()


_ALLOWED: dict[Lifecycle, frozenset[Lifecycle]] = {
    Lifecycle.REGISTERED: frozenset({Lifecycle.CLEANUP_READY}),
    Lifecycle.CLEANUP_READY: frozenset({Lifecycle.SPAWNING, Lifecycle.CLEAN}),
    Lifecycle.SPAWNING: frozenset({Lifecycle.OWNED, Lifecycle.CLEAN, Lifecycle.QUARANTINED}),
    Lifecycle.OWNED: frozenset({Lifecycle.ACTIVE, Lifecycle.TERM_GRACE, Lifecycle.QUARANTINED}),
    Lifecycle.ACTIVE: frozenset({Lifecycle.DRAINING, Lifecycle.TERM_GRACE, Lifecycle.QUARANTINED}),
    Lifecycle.DRAINING: frozenset(
        {Lifecycle.TERM_GRACE, Lifecycle.VERIFYING, Lifecycle.QUARANTINED}
    ),
    Lifecycle.TERM_GRACE: frozenset(
        {Lifecycle.KILLING, Lifecycle.VERIFYING, Lifecycle.QUARANTINED}
    ),
    Lifecycle.KILLING: frozenset({Lifecycle.VERIFYING, Lifecycle.QUARANTINED}),
    Lifecycle.VERIFYING: frozenset({Lifecycle.CLEAN, Lifecycle.QUARANTINED}),
    Lifecycle.QUARANTINED: frozenset({Lifecycle.VERIFYING}),
    Lifecycle.CLEAN: frozenset(),
}

_RECEIPT_TOKEN = object()


@dataclass(frozen=True, init=False)
class CleanReceipt:
    """Unforgeable sanitized proof produced only by the sole supervisor."""

    nonce: object
    reaped_returncodes: tuple[int, ...]
    result_index: int | None

    def __init__(
        self,
        token: object,
        *,
        nonce: object,
        reaped_returncodes: tuple[int, ...],
        result_index: int | None,
    ) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Coturn clean receipt is supervisor-owned")
        object.__setattr__(self, "nonce", nonce)
        object.__setattr__(self, "reaped_returncodes", reaped_returncodes)
        object.__setattr__(self, "result_index", result_index)


def new_clean_receipt(
    *,
    nonce: object,
    reaped_returncodes: tuple[int, ...],
    result_index: int | None,
) -> CleanReceipt:
    if not _valid_receipt_fields(reaped_returncodes, result_index):
        raise TypeError("Coturn clean receipt return code is invalid")
    return CleanReceipt(
        _RECEIPT_TOKEN,
        nonce=nonce,
        reaped_returncodes=reaped_returncodes,
        result_index=result_index,
    )


def _valid_receipt_fields(reaped_returncodes: object, result_index: object) -> bool:
    return (
        type(reaped_returncodes) is tuple
        and len(reaped_returncodes) <= 2
        and all(type(value) is int for value in reaped_returncodes)
        and (result_index is None or (type(result_index) is int and result_index == 0))
        and (result_index is None or result_index < len(reaped_returncodes))
    )


class ControllerState:
    """Contains no Popen, pipe, selector, or raw request authority."""

    __slots__ = (
        "_active_io_ready",
        "_chunks",
        "_condition",
        "_control",
        "_failure",
        "_joined",
        "_leader_returncode",
        "_nonce",
        "_quarantined",
        "_receipt",
        "_started",
        "_state",
        "_terminate_requested",
    )

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._nonce = object()
        self._state = Lifecycle.REGISTERED
        self._active_io_ready = False
        self._control: ControlSignal | None = None
        self._failure: str | None = None
        self._leader_returncode: int | None = None
        self._terminate_requested = False
        self._chunks: deque[SubprocessChunk] = deque()
        self._receipt: CleanReceipt | None = None
        self._quarantined = False
        self._joined = False
        self._started = False

    @property
    def nonce(self) -> object:
        return self._nonce

    def transition(self, state: Lifecycle) -> bool:
        with self._condition:
            if state not in _ALLOWED[self._state]:
                return False
            self._state = state
            if state is Lifecycle.QUARANTINED:
                self._quarantined = True
            if state is Lifecycle.ACTIVE:
                self._started = True
            if state in {Lifecycle.ACTIVE, Lifecycle.CLEAN, Lifecycle.QUARANTINED}:
                self._condition.notify_all()
            return True

    def quarantine_retained_owner(self) -> bool:
        """Poison admission while preserving the legal pre-spawn state path."""

        with self._condition:
            if self._state is Lifecycle.CLEAN:
                return False
            if self._state is Lifecycle.REGISTERED:
                self._state = Lifecycle.CLEANUP_READY
            if self._state is Lifecycle.CLEANUP_READY:
                self._state = Lifecycle.SPAWNING
            if self._state is not Lifecycle.QUARANTINED:
                if Lifecycle.QUARANTINED not in _ALLOWED[self._state]:
                    return False
                self._state = Lifecycle.QUARANTINED
            self._quarantined = True
            self._terminate_requested = True
            self._chunks.clear()
            self._condition.notify_all()
            return True

    def lifecycle(self) -> Lifecycle:
        with self._condition:
            return self._state

    def wait_change(self, timeout_seconds: float) -> None:
        with self._condition:
            self._condition.wait(timeout_seconds)

    def allow_active_io(self) -> bool:
        """Acknowledge that the facade retained its sanitized handle."""

        with self._condition:
            if self._state is not Lifecycle.ACTIVE or self._terminate_requested:
                return False
            self._active_io_ready = True
            self._condition.notify_all()
            return True

    def active_io_ready(self) -> bool:
        with self._condition:
            return self._state is Lifecycle.ACTIVE and self._active_io_ready

    def capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        try: return self._capture_control_retry(error)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit):
            self._capture_control_retry(error)
        except BaseException:
            self._capture_control_retry(error)

    def _capture_control_retry(self, error: KeyboardInterrupt | SystemExit) -> None:
        while True:
            try:
                signal = control_signal(error)
                self.capture_control_signal(signal)
                return
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                continue

    def capture_control_signal(self, signal: ControlSignal) -> None:
        try: return self._capture_control_signal_retry(signal)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit):
            self._capture_control_signal_retry(signal)
        except BaseException:
            self._capture_control_signal_retry(signal)

    def _capture_control_signal_retry(self, signal: ControlSignal) -> None:
        while True:
            try:
                _control_publication_entry()
                with self._condition:
                    if self._control is None:
                        self._control = signal
                    self._terminate_requested = True
                    self._chunks.clear()
                    self._condition.notify_all()
                return
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                continue

    def control(self) -> ControlSignal | None:
        with self._condition:
            return self._control

    def fail(self, message: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = message
            self._terminate_requested = True
            self._chunks.clear()
            self._condition.notify_all()

    def failure(self) -> str | None:
        with self._condition:
            return self._failure

    def request_termination(self) -> None:
        with self._condition:
            self._terminate_requested = True
            self._chunks.clear()
            self._condition.notify_all()

    def termination_requested(self) -> bool:
        with self._condition:
            return self._terminate_requested

    def publish_chunk(self, chunk: SubprocessChunk) -> bool:
        with self._condition:
            if (
                self._terminate_requested
                or self._state is not Lifecycle.ACTIVE
                or len(self._chunks) >= MAX_QUEUED_CHUNKS
            ):
                return False
            self._chunks.append(chunk)
            self._condition.notify_all()
            return True

    def queue_full(self) -> bool:
        with self._condition:
            return len(self._chunks) >= MAX_QUEUED_CHUNKS

    def pop_chunk(self) -> SubprocessChunk | None:
        with self._condition:
            if not self._chunks:
                return None
            chunk = self._chunks.popleft()
            self._condition.notify_all()
            return chunk

    def clear_chunks(self) -> None:
        with self._condition:
            self._chunks.clear()
            self._condition.notify_all()

    def chunk_count(self) -> int:
        with self._condition:
            return len(self._chunks)

    def observe_returncode(self, returncode: int) -> None:
        if type(returncode) is not int:
            return
        with self._condition:
            self._leader_returncode = returncode
            self._condition.notify_all()

    def observed_returncode(self) -> int | None:
        with self._condition:
            return self._leader_returncode

    def accept_clean_receipt(self, receipt: object) -> bool:
        with self._condition:
            if (
                type(receipt) is not CleanReceipt
                or receipt.nonce is not self._nonce
                or self._state is not Lifecycle.CLEAN
            ):
                return False
            if (
                type(receipt.reaped_returncodes) is not tuple
                or len(receipt.reaped_returncodes) > 2
                or not all(type(value) is int for value in receipt.reaped_returncodes)
                or (
                    receipt.result_index is not None
                    and (type(receipt.result_index) is not int or receipt.result_index != 0)
                )
                or (
                    receipt.result_index is not None
                    and receipt.result_index >= len(receipt.reaped_returncodes)
                )
            ):
                return False
            self._receipt = receipt
            self._condition.notify_all()
            return True

    def complete_clean(
        self,
        reaped_returncodes: tuple[int, ...],
        result_index: int | None,
    ) -> bool:
        """Publish receipt before CLEAN so no observable CLEAN state lacks proof."""

        try: return self._complete_clean_retry(reaped_returncodes, result_index)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            self.capture_control(error)
        except BaseException:
            pass
        return self._complete_clean_retry(reaped_returncodes, result_index)

    def _complete_clean_retry(
        self,
        reaped_returncodes: tuple[int, ...],
        result_index: int | None,
    ) -> bool:
        if not _valid_receipt_fields(reaped_returncodes, result_index):
            return False
        while True:
            try:
                with self._condition:
                    if self._state is Lifecycle.CLEAN:
                        return self._receipt is not None
                    if Lifecycle.CLEAN not in _ALLOWED[self._state]:
                        return False
                    receipt = CleanReceipt(
                        _RECEIPT_TOKEN,
                        nonce=self._nonce,
                        reaped_returncodes=reaped_returncodes,
                        result_index=result_index,
                    )
                    self._receipt = receipt
                    _clean_receipt_staged()
                    self._state = Lifecycle.CLEAN
                    self._condition.notify_all()
                    return True
            except (KeyboardInterrupt, SystemExit) as error:
                self.capture_control(error)
            except BaseException:
                continue

    def mark_joined(self) -> bool:
        with self._condition:
            if self._state is not Lifecycle.CLEAN or self._receipt is None:
                return False
            self._joined = True
            self._condition.notify_all()
            return True

    def clean_joined(self) -> bool:
        with self._condition:
            return self._state is Lifecycle.CLEAN and self._receipt is not None and self._joined

    def clean_returncode(self) -> int | None:
        with self._condition:
            if not self._joined or self._receipt is None:
                return None
            index = self._receipt.result_index
            return None if index is None else self._receipt.reaped_returncodes[index]

    def poisoned(self) -> bool:
        with self._condition:
            return self._quarantined and not self._joined

    def started(self) -> bool:
        with self._condition:
            return self._started

    def __repr__(self) -> str:
        return "ControllerState()"


def _control_publication_entry() -> None:
    """Deterministic nested-control seam before the first signal is published."""


def _clean_receipt_staged() -> None:
    """Control seam after proof publication while lifecycle is still pre-CLEAN."""


__all__ = [
    "CleanReceipt",
    "ControllerState",
    "Lifecycle",
    "new_clean_receipt",
]

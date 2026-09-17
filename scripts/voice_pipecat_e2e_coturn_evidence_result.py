"""Caller-preowned publication slot for one sanitized Coturn probe result."""

from __future__ import annotations

import threading
import traceback
from typing import Callable, NoReturn

from scripts.voice_pipecat_e2e_coturn_evidence_state import CoturnStateProbe
from scripts.voice_pipecat_e2e_coturn_log_grammar import CoturnLogCategory

_SLOT_TOKEN = object()
_SUMMARY_TOKEN = object()
_ControlSignal = tuple[type[KeyboardInterrupt] | type[SystemExit], int | None]


class CoturnEvidenceError(RuntimeError):
    """The attached Coturn stream cannot support qualification evidence."""

    def __repr__(self) -> str:
        return "CoturnEvidenceError()"


class CoturnProbeSummary:
    """Sanitized discovery output that can never represent qualification."""

    __slots__ = (
        "_allocation_count",
        "_grammar_verified",
        "_grammar_violation_records",
        "_observed_categories",
        "_total_records",
        "_unknown_info_records",
    )

    def __init__(
        self,
        *,
        grammar_verified: bool,
        allocation_count: int,
        observed_categories: frozenset[CoturnLogCategory],
        unknown_info_records: int,
        grammar_violation_records: int,
        total_records: int,
        _token: object = None,
    ) -> None:
        if _token is not _SUMMARY_TOKEN or grammar_verified is not False:
            raise TypeError("Coturn probe summary is factory-owned")
        object.__setattr__(self, "_grammar_verified", grammar_verified)
        object.__setattr__(self, "_allocation_count", allocation_count)
        object.__setattr__(self, "_observed_categories", observed_categories)
        object.__setattr__(self, "_unknown_info_records", unknown_info_records)
        object.__setattr__(self, "_grammar_violation_records", grammar_violation_records)
        object.__setattr__(self, "_total_records", total_records)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Coturn probe summary is immutable")

    @property
    def grammar_verified(self) -> bool:
        return self._grammar_verified

    @property
    def allocation_count(self) -> int:
        return self._allocation_count

    @property
    def observed_categories(self) -> frozenset[CoturnLogCategory]:
        return self._observed_categories

    @property
    def unknown_info_records(self) -> int:
        return self._unknown_info_records

    @property
    def grammar_violation_records(self) -> int:
        return self._grammar_violation_records

    @property
    def total_records(self) -> int:
        return self._total_records

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> CoturnProbeSummary:
        raise TypeError("Coturn probe summary cannot be copied")

    def __deepcopy__(self, _memo: object) -> CoturnProbeSummary:
        raise TypeError("Coturn probe summary cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn probe summary cannot be serialized")

    def __repr__(self) -> str:
        return "CoturnProbeSummary(grammar_verified=False)"


class CoturnProbeResultSlot:
    """Retain one sanitized result across a lost finalization return."""

    __slots__ = ("_finisher", "_lock", "_owner", "_publication")

    def __init__(self, token: object) -> None:
        authorized = token is _SLOT_TOKEN
        token = None
        if not authorized:
            raise TypeError("Coturn probe result slot is factory-owned")
        self._lock = threading.Condition()
        self._finisher: object | None = None
        self._owner: object | None = None
        self._publication: tuple[object, object] | None = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._publication is not None

    def _claim(self, owner: object) -> bool:
        with self._lock:
            if self._owner is None:
                self._owner = owner
                return True
            return self._owner is owner

    def _publish(self, owner: object, summary: object) -> bool:
        with self._lock:
            if self._owner is not owner:
                return False
            current = self._publication
            if current is None:
                self._publication = (owner, summary)
                return True
            return current[0] is owner

    def _claim_finish(self, owner: object, operation: object) -> str:
        with self._lock:
            if self._owner is not owner:
                return "invalid"
            while self._publication is None and self._finisher not in (None, operation):
                self._lock.wait()
            if self._publication is not None:
                return "published" if self._publication[0] is owner else "invalid"
            if self._finisher is None:
                self._finisher = operation
            return "claimed" if self._finisher is operation else "invalid"

    def _release_finish(self, owner: object, operation: object) -> bool:
        with self._lock:
            if self._owner is not owner:
                return False
            if self._finisher is operation:
                self._finisher = None
            if self._finisher is None:
                self._lock.notify_all()
                return True
            return False

    def _finish_status(self, owner: object, operation: object) -> str:
        with self._lock:
            if self._owner is not owner:
                return "invalid"
            if self._publication is not None:
                return "published" if self._publication[0] is owner else "invalid"
            if self._finisher is operation:
                return "claimed"
            return "open" if self._finisher is None else "busy"

    def _read(self) -> object | None:
        with self._lock:
            return None if self._publication is None else self._publication[1]

    def _owned_by(self, owner: object) -> bool:
        with self._lock:
            return self._publication is not None and self._publication[0] is owner

    def __copy__(self) -> CoturnProbeResultSlot:
        raise TypeError("Coturn probe result slot cannot be copied")

    def __deepcopy__(self, _memo: object) -> CoturnProbeResultSlot:
        raise TypeError("Coturn probe result slot cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn probe result slot cannot be serialized")

    def __repr__(self) -> str:
        return "CoturnProbeResultSlot()"


def new_coturn_probe_result_slot() -> CoturnProbeResultSlot:
    """Create one harmless empty result owner before parser finalization."""

    return CoturnProbeResultSlot(_SLOT_TOKEN)


def coturn_probe_summary_from_slot(slot: CoturnProbeResultSlot) -> CoturnProbeSummary:
    """Read a published summary without consuming the caller-owned slot."""

    control: _ControlSignal | None = None
    failed = False
    result: object | None = None
    phase = 0
    while phase < 2:
        try:
            if phase == 0:
                result, current_failed, control = _read_probe_result_slot(
                    slot,
                    CoturnProbeSummary,
                    _probe_result_boundary_hook,
                    control,
                )
                failed = failed or current_failed
                phase = 1
                _probe_result_boundary_hook("summary-read-returned")
            else:
                slot = None  # type: ignore[assignment]
                phase = 2
        except (KeyboardInterrupt, SystemExit) as error:
            if control is None:
                control = _control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            failed = True
            _scrub_exception(error)
    if control is not None:
        result = None
        _raise_control(control)
    if failed:
        result = None
        _raise_public("Coturn probe result read failed")
    if type(result) is not CoturnProbeSummary:
        result = None
        _raise_public("Coturn probe result is unavailable")
    return result


def _make_probe(state: CoturnStateProbe, *, total_records: int) -> CoturnProbeSummary:
    return CoturnProbeSummary(
        grammar_verified=False,
        allocation_count=state.allocation_count,
        observed_categories=state.observed_categories,
        unknown_info_records=state.unknown_info_records,
        grammar_violation_records=state.grammar_violation_records,
        total_records=total_records,
        _token=_SUMMARY_TOKEN,
    )


def _read_probe_result_slot(
    slot: object,
    expected_type: type[object],
    hook: Callable[[str], None],
    control: _ControlSignal | None,
) -> tuple[object | None, bool, _ControlSignal | None]:
    """Read once behind a sanitized, non-consuming control boundary."""

    failed = False
    result: object | None = None
    phase = 0
    while phase < 2:
        try:
            if phase == 0:
                if type(slot) is CoturnProbeResultSlot:
                    result = slot._read()
                phase = 1
                hook("summary-read")
            else:
                slot = None
                hook = _noop_hook
                phase = 2
        except (KeyboardInterrupt, SystemExit) as error:
            if control is None:
                control = _control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            failed = True
            phase = 1
            _scrub_exception(error)
    if type(result) is not expected_type:
        result = None
    return result, failed, control


def _control_signal(error: KeyboardInterrupt | SystemExit) -> _ControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt, None
    code = error.code if type(error.code) is int or error.code is None else 1
    return SystemExit, code


def _scrub_exception(error: BaseException) -> None:
    traceback.clear_frames(error.__traceback__)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    error.__dict__.clear()
    error.args = ()


def _raise_control(control: _ControlSignal) -> NoReturn:
    kind, code = control
    if kind is KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    raise SystemExit(code) from None


def _noop_hook(_phase: str) -> None:
    pass


def _probe_result_boundary_hook(_phase: str) -> None:
    """Secret-free deterministic seam for publication control tests."""


def _raise_public(message: str) -> NoReturn:
    raise CoturnEvidenceError(message) from None


__all__ = [
    "CoturnEvidenceError",
    "CoturnProbeResultSlot",
    "CoturnProbeSummary",
    "coturn_probe_summary_from_slot",
    "new_coturn_probe_result_slot",
]

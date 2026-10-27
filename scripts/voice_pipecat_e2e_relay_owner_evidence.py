"""Friend-only canonical evidence-owner recovery for the relay aggregate."""

from __future__ import annotations

from collections.abc import Callable

from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as _drain_registry
from scripts import voice_pipecat_e2e_coturn_runtime_drain_terminal as _drain_terminal
from scripts import voice_pipecat_e2e_coturn_runtime_process_claims as _process_claims
from scripts.voice_pipecat_e2e_coturn_runtime_drain import AttachedCoturnEvidenceDrain
from scripts.voice_pipecat_e2e_coturn_runtime_drain_registry import (
    CoturnEvidenceDrainCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
    AttachedCoturnEvidencePump,
    _DrainClaim,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_coturn_runtime_process_claims import PumpClaim

_FAILURE = "Relay probe evidence recovery failed"


def _recover_canonical_pump(
    process: AttachedCoturnProcess,
    retained: AttachedCoturnEvidencePump | None,
) -> AttachedCoturnEvidencePump | None:
    """Return only the exact process-owned pump, or prove no live claim exists."""

    if type(process) is not AttachedCoturnProcess or (
        retained is not None and type(retained) is not AttachedCoturnEvidencePump
    ):
        raise TypeError(_FAILURE)
    with process._pump_operation_lock:
        claim = process._pump_claim
        if type(claim) is not PumpClaim or type(claim.state) is not str:
            raise TypeError(_FAILURE)
        if claim.state == "empty":
            if claim.key is not None or claim.owner is not None or retained is not None:
                raise TypeError(_FAILURE)
            return None
        if claim.state == "terminal":
            if (
                claim.key is None
                or claim.owner is not process._pump_owner
                or not _terminal_pump_matches(retained)
            ):
                raise TypeError(_FAILURE)
            return retained
        if (
            claim.state != "published"
            or claim.key is None
            or claim.owner is not process._pump_owner
        ):
            raise TypeError(_FAILURE)
        candidate = _process_claims.return_canonical_pump(claim.key)
    if (
        type(candidate) is not AttachedCoturnEvidencePump
        or (retained is not None and retained is not candidate)
        or not candidate._matches_process(process)
    ):
        raise TypeError(_FAILURE)
    return candidate


def _recover_canonical_drain(
    process: AttachedCoturnProcess,
    pump: AttachedCoturnEvidencePump,
    retained: AttachedCoturnEvidenceDrain | None,
    *,
    absolute_deadline: float,
    clock: Callable[[], float],
) -> AttachedCoturnEvidenceDrain | None:
    """Return only the exact pump-owned drain, or prove its claim is empty."""

    if (
        type(process) is not AttachedCoturnProcess
        or type(pump) is not AttachedCoturnEvidencePump
        or (retained is not None and type(retained) is not AttachedCoturnEvidenceDrain)
        or type(absolute_deadline) is not float
        or not callable(clock)
    ):
        raise TypeError(_FAILURE)
    if retained is not None and _terminal_drain_matches(
        retained,
        process,
        pump,
        absolute_deadline=absolute_deadline,
        clock=clock,
    ):
        return retained
    key = pump._claimed_drain_key(process, absolute_deadline, clock)
    if key is None:
        with pump._lock:
            claim = pump._drain_claim
            empty = bool(
                type(claim) is _DrainClaim
                and claim.owner is None
                and claim.process is None
                and claim.drain is None
                and claim.absolute_deadline is None
                and claim.clock is None
                and claim.return_key is None
            )
        if not empty or retained is not None:
            raise TypeError(_FAILURE)
        return None
    candidate = _drain_registry.return_canonical_drain(key)
    if type(candidate) is not AttachedCoturnEvidenceDrain or (
        retained is not None and retained is not candidate
    ):
        raise TypeError(_FAILURE)
    with candidate._lock:
        exact = bool(
            candidate._process is process
            and candidate._pump is pump
            and type(candidate._deadline) is float
            and candidate._deadline == absolute_deadline
            and candidate._clock is clock
        )
        owner = candidate._owner_token
    if not exact or not pump._matches_drain(process, owner):
        raise TypeError(_FAILURE)
    return candidate


def _terminal_pump_matches(pump: object) -> bool:
    if pump is None:
        return True
    if type(pump) is not AttachedCoturnEvidencePump:
        return False
    try:
        with object.__getattribute__(pump, "_lock"):
            return bool(
                (
                    object.__getattribute__(pump, "_failed")
                    or object.__getattribute__(pump, "_finished")
                )
                and object.__getattribute__(pump, "_claim_process") is None
                and object.__getattribute__(pump, "_process") is None
                and object.__getattribute__(pump, "_parser") is None
                and object.__getattribute__(pump, "_result_slot") is None
            )
    except AttributeError:
        return False


def _terminal_drain_matches(
    drain: object,
    process: AttachedCoturnProcess,
    pump: AttachedCoturnEvidencePump,
    *,
    absolute_deadline: float,
    clock: Callable[[], float],
) -> bool:
    if type(drain) is not AttachedCoturnEvidenceDrain:
        return False
    try:
        with object.__getattribute__(drain, "_lock"):
            state = object.__getattribute__(drain, "_state")
            transition = object.__getattribute__(drain, "_terminal_transition")
            final = bool(
                state in {"complete", "cleaned"}
                and transition is None
                and object.__getattribute__(drain, "_process") is None
                and object.__getattribute__(drain, "_pump") is None
                and object.__getattribute__(drain, "_thread") is None
                and object.__getattribute__(drain, "_clock") is None
            )
            if final:
                return True
            if (
                type(transition) is not _drain_terminal.DrainTerminalTransition
                or state != f"terminalizing-{transition.target}"
                or transition.phase not in {"owned", "released", "empty"}
                or type(object.__getattribute__(drain, "_deadline")) is not float
                or object.__getattribute__(drain, "_deadline") != absolute_deadline
                or type(object.__getattribute__(drain, "_cleanup_authority"))
                is not CoturnEvidenceDrainCleanupAuthority
            ):
                return False
            owner = object.__getattribute__(drain, "_owner_token")
            authority = object.__getattribute__(drain, "_cleanup_authority")
            if transition.phase in {"owned", "released"}:
                snapshot_matches = bool(
                    transition.process is process
                    and transition.pump is pump
                    and transition.clock is clock
                )
            else:
                snapshot_matches = bool(
                    transition.process is None
                    and transition.pump is None
                    and transition.thread is None
                    and transition.clock is None
                    and transition.summary is None
                    and object.__getattribute__(drain, "_process") is None
                    and object.__getattribute__(drain, "_pump") is None
                    and object.__getattribute__(drain, "_thread") is None
                    and object.__getattribute__(drain, "_clock") is None
                )
    except AttributeError:
        return False
    if not snapshot_matches or _drain_registry.resolve_cleanup_authority(authority) is not drain:
        return False
    try:
        with object.__getattribute__(pump, "_lock"):
            claim = object.__getattribute__(pump, "_drain_claim")
            empty_claim = bool(
                type(claim) is _DrainClaim
                and claim.owner is None
                and claim.process is None
                and claim.drain is None
                and claim.absolute_deadline is None
                and claim.clock is None
                and claim.return_key is None
            )
            exact_claim = bool(
                type(claim) is _DrainClaim
                and claim.owner is owner
                and claim.process is process
                and claim.drain is drain
                and claim.absolute_deadline == absolute_deadline
                and claim.clock is clock
                and claim.return_key is not None
            )
            return_key = claim.return_key if exact_claim else None
    except AttributeError:
        return False
    if transition.phase == "released" and not empty_claim:
        return False
    if transition.phase == "empty" and not empty_claim:
        return False
    if transition.phase == "owned" and not (empty_claim or exact_claim):
        return False
    if exact_claim and _drain_registry.return_canonical_drain(return_key) is not drain:
        return False
    return True


__all__: list[str] = []

"""Finite recovery helpers for committed Coturn drain handoffs."""

from __future__ import annotations

import math

from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as _registry
from scripts import voice_pipecat_e2e_coturn_runtime_drain_terminal as _terminal
from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_runtime_drain_registry import (
    CoturnEvidenceDrainCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import AttachedCoturnEvidencePump
from scripts.voice_pipecat_e2e_coturn_runtime_process import (
    AttachedCoturnProcess,
    CleanCoturnExitReceipt,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
)

_MAX_RECOVERY_ATTEMPTS = 8
_MAX_DRAIN_WINDOW_SECONDS = 120.0


def read_clock(clock: object) -> float:
    value = clock() if callable(clock) else None
    if type(value) is not float or not math.isfinite(value):
        raise CoturnRuntimeError("Coturn evidence drain clock is invalid")
    return value


def recover_claimed_drain(
    pump: AttachedCoturnEvidencePump,
    process: AttachedCoturnProcess,
    deadline: float,
    clock: object,
) -> tuple[object | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _ in range(_MAX_RECOVERY_ATTEMPTS):
        try:
            candidate = pump._claimed_drain_key(process, deadline, clock)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            continue
        except BaseException:
            continue
        return candidate, control
    return None, control


def recover_clean_exit(
    process: object,
) -> tuple[CleanCoturnExitReceipt | None, ControlSignal | None]:
    control: ControlSignal | None = None
    if type(process) is AttachedCoturnProcess:
        for _ in range(_MAX_RECOVERY_ATTEMPTS):
            try:
                receipt = process._confirm_clean_exit()
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                continue
            except BaseException:
                continue
            if type(receipt) is CleanCoturnExitReceipt and process._matches_exit(receipt):
                return receipt, control
            return None, control
    return None, control


def recover_unstarted_process(
    process: object,
) -> tuple[bool, bool, ControlSignal | None]:
    """Distinguish exact no-effect state from running and uncertain state."""

    control: ControlSignal | None = None
    if type(process) is not AttachedCoturnProcess:
        return False, True, None
    for _ in range(_MAX_RECOVERY_ATTEMPTS):
        try:
            return process._retire_unstarted_for_drain_cleanup(), False, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
        except BaseException:
            pass
    return False, True, control


def recover_pump_summary(
    pump: object,
    process: object,
    drain: object,
    owner: object,
    receipt: CleanCoturnExitReceipt,
) -> tuple[CoturnProbeSummary | None, ControlSignal | None]:
    control: ControlSignal | None = None
    if type(pump) is AttachedCoturnEvidencePump and type(process) is AttachedCoturnProcess:
        for _ in range(_MAX_RECOVERY_ATTEMPTS):
            try:
                summary = pump._reconcile_finalization(process, owner, drain, receipt)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                continue
            except BaseException:
                continue
            return summary, control
    return None, control


def release_drain_claim(drain: object) -> tuple[bool, ControlSignal | None]:
    process, pump, owner = _terminal.release_resources(drain)
    if process is None and pump is None:
        return False, None
    released = False
    control: ControlSignal | None = None
    if type(process) is AttachedCoturnProcess and type(pump) is AttachedCoturnEvidencePump:
        for _ in range(_MAX_RECOVERY_ATTEMPTS):
            try:
                released = pump._release_drain_claim(process, owner, drain)
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                pass
            if released:
                break
    process = pump = owner = None
    return not released, control


def valid_summary(candidate: object) -> bool:
    return bool(
        type(candidate) is CoturnProbeSummary
        and candidate.grammar_verified is False
        and not candidate
    )


def valid_drain_input(
    process: object,
    pump: object,
    deadline: object,
    clock: object,
) -> bool:
    now = clock() if callable(clock) else None
    return bool(
        type(process) is AttachedCoturnProcess
        and type(pump) is AttachedCoturnEvidencePump
        and pump._matches_process(process)
        and type(deadline) is float
        and math.isfinite(deadline)
        and type(now) is float
        and math.isfinite(now)
        and 0.01 <= deadline - now <= _MAX_DRAIN_WINDOW_SECONDS
    )


def retain_cleanup_authority(
    drain: object,
) -> tuple[CoturnEvidenceDrainCleanupAuthority | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _ in range(_MAX_RECOVERY_ATTEMPTS):
        try:
            authority = _registry.retain_cleanup_authority(drain)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            continue
        except BaseException:
            continue
        if type(authority) is CoturnEvidenceDrainCleanupAuthority:
            return authority, control
        try:
            state = object.__getattribute__(drain, "_state")
        except BaseException:
            state = None
        if state in {"complete", "cleaned"}:
            return None, control
    return None, control


def resolve_cleanup_authority(
    authority: object,
) -> tuple[object | None, bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _ in range(_MAX_RECOVERY_ATTEMPTS):
        try:
            return _registry.resolve_cleanup_authority(authority), False, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
        except BaseException:
            pass
    return None, True, control


def release_cleanup_authority(drain: object) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    authority = object.__getattribute__(drain, "_cleanup_authority")
    for _ in range(_MAX_RECOVERY_ATTEMPTS):
        try:
            _registry.release_cleanup_authority(drain)
            retained = _registry.resolve_cleanup_authority(authority)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            continue
        except BaseException:
            continue
        if retained is None:
            return False, control
    return True, control

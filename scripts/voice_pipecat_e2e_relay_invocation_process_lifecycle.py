"""Exact process settlement for one owned role of the concrete adapter.

This module owns only raw child effects and their settlement proofs: registered
spawn, alive identity, terminal-zero observation before reap, live-descendant
absence, single reap, final group absence, and TERM/KILL under fresh liveness.
Ambiguous facts quarantine; they never fabricate an exit or a stop. All state is
a private per-(pair, role) record guarded by one lock; nothing here publishes
outward, retains secrets in repr, or holds a lock across an effect.
"""

from __future__ import annotations

import math
import signal
import threading

from scripts.voice_pipecat_e2e_relay_invocation_process_local import (
    _group_exists,
    _live_group_members,
    _members_include_only,
    _observe_terminal_unreaped,
    _process_group,
    _process_pid,
    _reap_once,
    _registered_spawn,
    _signal_group,
    _terminal_status_is_zero,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    RelayChildRequest,
    RelayInvocationError,
)

_FAILURE = "Relay concrete process settlement is invalid"
_ROLES = ("app", "web", "browser")
_TERM_GRACE_SECONDS = 2.0
_TICK_SECONDS = 0.05
_SPIN_LIMIT = 4096

_LOCK = threading.RLock()
_RECORDS: dict[tuple[object, str], list] = {}


def _spawn_checkpoint(_stage: str) -> None:
    """Deterministic cut seam around durable settlement facts."""


def _now(clock, deadline: float) -> float | None:
    value = clock()
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        return None
    return value if value < deadline else None


def _registered_identity(request: RelayChildRequest) -> list | None:
    """Spawn through the registered factory and bind the exact leader identity."""

    registered: list[object] = []
    process = _registered_spawn(
        request.command,
        cwd=request.cwd,
        environment=request.environment,
        owner_register=registered.append,
    )
    if not registered or registered[0] is not process:
        return None
    pid = _process_pid(process)
    if type(pid) is not int or pid <= 1:
        return None
    pgid = _process_group(pid)
    if type(pgid) is not int or pgid != pid:
        return None
    identity = _process_group(pid)
    if type(identity) is not int or identity != pgid:
        return None
    return [pid, pgid, process]


def _alive_identity(pid: object, pgid: object) -> tuple[int, int] | None:
    if type(pid) is not int or pid <= 1 or type(pgid) is not int or pgid != pid:
        return None
    observed = _process_group(pgid)
    if type(observed) is not int or observed != pgid or not _group_exists(pgid):
        return None
    return pid, pgid


def _reap_zero(pid: object) -> bool:
    if type(pid) is not int:
        return False
    record = _reap_once(pid)
    return type(record) is tuple and len(record) == 2 and record == (0, None)


def _settle_terminal_zero(record: list, clock, wait, deadline: float) -> bool:
    """Observe zero unreaped, prove no live descendants, reap once, prove absence."""

    for _attempt in range(_SPIN_LIMIT):
        current = _now(clock, deadline)
        if current is None or _alive_identity(record[0], record[1]) is None:
            return _quarantine(record)
        observation = _observe_terminal_unreaped(record[0])
        if observation is None or not _terminal_status_is_zero(observation):
            wait(_TICK_SECONDS)
            continue
        _spawn_checkpoint("terminal-zero-unreaped")
        members = _live_group_members(record[1])
        if type(members) is not list or not _members_include_only(members, record[0]):
            return _quarantine(record)
        _spawn_checkpoint("live-descendants-absent")
        if not _reap_zero(record[0]):
            return _quarantine(record)
        _spawn_checkpoint("reaped")
        if _group_exists(record[1]):
            return _quarantine(record)
        _spawn_checkpoint("group-absent")
        record[0] = None
        return True
    return _quarantine(record)


def _settle_stop(record: list, clock, wait, deadline: float) -> bool:
    """TERM then KILL only while freshly live; observe-only otherwise."""

    for _attempt in range(_SPIN_LIMIT):
        identity = _alive_identity(record[0], record[1])
        if identity is None:
            now = _now(clock, deadline)
            if now is None:
                return _quarantine(record)
            if not _group_exists(record[1]):
                if _reap_zero(record[0]) or record[0] is None:
                    record[0] = None
                    return True
                return _quarantine(record)
            wait(_TICK_SECONDS)
            continue
        _pid, pgid = identity
        now = _now(clock, deadline)
        if now is None:
            return _quarantine(record)
        if not record[5]:
            if not _signal_group(pgid, signal.SIGTERM):
                wait(_TICK_SECONDS)
                continue
            record[5] = True
            record[7] = now
            _spawn_checkpoint("term-sent")
            wait(_TICK_SECONDS)
            continue
        if not record[6] and now - record[7] >= _TERM_GRACE_SECONDS:
            if not _signal_group(pgid, signal.SIGKILL):
                wait(_TICK_SECONDS)
                continue
            record[6] = True
            _spawn_checkpoint("kill-sent")
            wait(_TICK_SECONDS)
            continue
        wait(_TICK_SECONDS)
    return _quarantine(record)


def _quarantine(record: list) -> bool:
    record[3] = True
    record[4] = True
    record[5] = True
    record[6] = True
    _spawn_checkpoint("quarantined")
    return False


def _intend_start(pair_key: object, role: str, request: RelayChildRequest, clock, wait) -> bool:
    """Intend one start, spawn registered, and bind the identity durably."""

    if (
        type(pair_key) is not object
        or role not in _ROLES
        or type(request) is not RelayChildRequest
        or not callable(clock)
        or not callable(wait)
    ):
        raise TypeError(_FAILURE)
    with _LOCK:
        current = _RECORDS.get((pair_key, role))
        if current is not None:
            return False
        _RECORDS[(pair_key, role)] = None
    try:
        identity = _registered_identity(request)
    except BaseException:
        with _LOCK:
            if _RECORDS.get((pair_key, role)) is None:
                del _RECORDS[(pair_key, role)]
        raise
    with _LOCK:
        placeholder = _RECORDS.get((pair_key, role))
        if placeholder is not None or identity is None:
            if _RECORDS.get((pair_key, role)) is None:
                del _RECORDS[(pair_key, role)]
            raise RelayInvocationError(_FAILURE)
        record = [identity[0], identity[1], identity[2], False, False, False, False, None]
        _RECORDS[(pair_key, role)] = record
        _spawn_checkpoint("registered")
        return True


def _role_settled_absent(pair_key: object, role: str) -> bool:
    with _LOCK:
        record = _RECORDS.get((pair_key, role))
        return record is not None and record[0] is None and not record[3]


def _role_has_live_record(pair_key: object, role: str) -> bool:
    """True when a started role still owns an unsettled raw child."""

    with _LOCK:
        record = _RECORDS.get((pair_key, role))
        return type(record) is list and record[0] is not None


def _finish_started_role(pair_key: object, role: str, clock, wait, deadline: float) -> bool:
    """Settle the browser child to a terminal-zero proof or quarantine."""

    with _LOCK:
        record = _RECORDS.get((pair_key, role))
        if record is None or type(record) is not list or record[0] is None:
            raise RelayInvocationError(_FAILURE)
    return _settle_terminal_zero(record, clock, wait, deadline)


def _stop_started_role(pair_key: object, role: str, clock, wait, deadline: float) -> bool:
    with _LOCK:
        record = _RECORDS.get((pair_key, role))
        if record is None or type(record) is not list or record[0] is None:
            raise RelayInvocationError(_FAILURE)
    settled = _settle_stop(record, clock, wait, deadline)
    if settled:
        with _LOCK:
            del _RECORDS[(pair_key, role)]
    return settled


def _record_total_absence(pair_key: object) -> bool:
    with _LOCK:
        return not any(key[0] is pair_key for key in _RECORDS)


def _scrub_records(pair_key: object) -> bool:
    with _LOCK:
        for key in [key for key in _RECORDS if key[0] is pair_key]:
            record = _RECORDS[key]
            if record is not None and record[0] is not None:
                return False
            del _RECORDS[key]
        return True


__all__: list[str] = []

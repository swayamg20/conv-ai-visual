# ruff: noqa: E402
"""Deterministic settlement tests with fake local seams and no real child."""

from __future__ import annotations

import math
import signal
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation_process_lifecycle as settlement
from scripts.voice_pipecat_e2e_relay_invocation_support import _child_request
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayInvocationError

_NOW = 100.0


def _request(role: str, deadline: float | None = None) -> object:
    completion = "started" if role == "browser" else "ready"
    return _child_request(
        role=role,
        command=("/python", "entry.py"),
        cwd=Path("/run/workspace"),
        environment={"HOME": "/run"},
        completion=completion,
        absolute_deadline=deadline,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = _NOW

    def __call__(self) -> float:
        return self.now


class _Wait:
    def __init__(self) -> None:
        self.ticks: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.ticks.append(seconds)


class _Process:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


@pytest.fixture(autouse=True)
def _isolated_records(monkeypatch):
    monkeypatch.setattr(settlement, "_RECORDS", {})
    monkeypatch.setattr(settlement, "_TICK_SECONDS", 0.0)
    yield


def _spawn_fakes(monkeypatch, *, pid=4242, pgid=None, fail=None):
    order: list[str] = []

    def fake_registered_spawn(argv, *, cwd, environment, owner_register):
        order.append("register")
        process = _Process(pid)

        def register(child):
            order.append("callback")
            owner_register(child)

        monkeypatch.setattr(
            settlement,
            "_process_pid",
            lambda _process: pid,
        )
        monkeypatch.setattr(
            settlement,
            "_process_group",
            lambda _pid: pgid if pgid is not None else pid,
        )
        register(process)
        order.append("init")
        if fail is not None:
            fail(order)
        return process

    monkeypatch.setattr(settlement, "_registered_spawn", fake_registered_spawn)
    return order


def test_intend_start_binds_the_exact_registered_identity(monkeypatch):
    order = _spawn_fakes(monkeypatch)
    assert settlement._intend_start(object(), "app", _request("app"), _Clock(), _Wait())
    assert order[:3] == ["register", "callback", "init"]
    assert settlement._role_settled_absent(object(), "app") is False


def test_intend_start_rejects_a_foreign_group_leader(monkeypatch):
    _spawn_fakes(monkeypatch, pgid=777)
    with pytest.raises(RelayInvocationError):
        settlement._intend_start(object(), "web", _request("web"), _Clock(), _Wait())
    assert settlement._record_total_absence(object())


def test_intend_start_is_cap_one_per_role_and_leaves_no_registry_on_loss(monkeypatch):
    _spawn_fakes(monkeypatch)
    key = object()
    assert settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())
    assert not settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())

    def fail(_order):
        raise KeyboardInterrupt()

    _spawn_fakes(monkeypatch, fail=fail)
    with pytest.raises(KeyboardInterrupt):
        settlement._intend_start(key, "browser", _request("browser"), _Clock(), _Wait())
    assert settlement._role_settled_absent(key, "browser") is False
    assert settlement._record_total_absence(key) is False


def test_finish_requires_terminal_zero_before_descendants_before_reap(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "browser", _request("browser"), _Clock(), _Wait())
    events: list[str] = []
    observation = type("Result", (), {"si_pid": 4242})
    state = {"reaped": False}
    monkeypatch.setattr(
        settlement,
        "_observe_terminal_unreaped",
        lambda pid: events.append("observe") or observation,
    )
    monkeypatch.setattr(
        settlement,
        "_terminal_status_is_zero",
        lambda result: events.append("zero") or True,
    )
    monkeypatch.setattr(
        settlement,
        "_live_group_members",
        lambda pgid: events.append("members") or [4242],
    )
    monkeypatch.setattr(
        settlement,
        "_reap_once",
        lambda pid: state.__setitem__("reaped", True) or (events.append("reap") or (0, None)),
    )
    monkeypatch.setattr(
        settlement,
        "_group_exists",
        lambda pgid: events.append("group") or not state["reaped"],
    )
    wait = _Wait()
    assert settlement._finish_started_role(key, "browser", _Clock(), wait, _NOW + 10.0)
    assert events == ["group", "observe", "zero", "members", "reap", "group"]
    assert settlement._role_settled_absent(key, "browser")


def test_finish_quarantines_on_nonzero_or_live_descendant(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "browser", _request("browser"), _Clock(), _Wait())
    observation = type("Result", (), {"si_pid": 4242})
    monkeypatch.setattr(settlement, "_observe_terminal_unreaped", lambda pid: observation)
    monkeypatch.setattr(settlement, "_terminal_status_is_zero", lambda result: False)
    wait = _Wait()
    clock = _Clock()
    clock.now = _NOW + 60.0
    assert not settlement._finish_started_role(key, "browser", clock, wait, _NOW + 30.0)
    assert not settlement._role_settled_absent(key, "browser")
    assert settlement._scrub_records(key) is False


def test_stop_terms_then_kills_then_observes_only_after_revocation(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())
    signals: list[int] = []
    exists = {"alive": True}
    monkeypatch.setattr(
        settlement,
        "_signal_group",
        lambda pgid, value: signals.append(value) or True,
    )
    monkeypatch.setattr(settlement, "_group_exists", lambda pgid: not exists["gone"])
    observation = type("Result", (), {"si_pid": 4242})
    monkeypatch.setattr(settlement, "_observe_terminal_unreaped", lambda pid: observation)
    monkeypatch.setattr(settlement, "_terminal_status_is_zero", lambda result: True)
    monkeypatch.setattr(settlement, "_live_group_members", lambda pgid: [4242])
    monkeypatch.setattr(settlement, "_reap_once", lambda pid: (0, None))
    wait = _Wait()
    exists["gone"] = True
    assert settlement._stop_started_role(key, "app", _Clock(), wait, _NOW + 30.0)
    assert not signals


def test_stop_quarantines_on_deadline_before_clean_absence(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())
    monkeypatch.setattr(settlement, "_group_exists", lambda pgid: True)
    counter = {"sent": 0}
    monkeypatch.setattr(
        settlement,
        "_signal_group",
        lambda pgid, value: counter.__setitem__("sent", counter["sent"] + 1) or True,
    )
    wait = _Wait()
    clock = _Clock()
    clock.now = _NOW + 120.0
    assert not settlement._stop_started_role(key, "app", clock, wait, _NOW + 1.0)
    assert not settlement._role_settled_absent(key, "app")
    assert counter["sent"] == 0


def test_stop_escalates_to_kill_only_after_term_grace(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())
    signals: list[int] = []
    monkeypatch.setattr(
        settlement,
        "_signal_group",
        lambda pgid, value: signals.append(value) or True,
    )
    monkeypatch.setattr(settlement, "_group_exists", lambda pgid: True)
    monkeypatch.setattr(settlement, "_reap_once", lambda pid: (0, None))

    class _StepClock:
        def __init__(self, start: float, step: float) -> None:
            self.now = start
            self.step = step

        def __call__(self) -> float:
            value = self.now
            self.now += self.step
            return value

    settlement._TERM_GRACE_SECONDS = 2.0
    settlement._TICK_SECONDS = 0.0
    try:
        assert not settlement._settle_stop(
            settlement._RECORDS[(key, "app")],
            _StepClock(_NOW, 0.5),
            _Wait(),
            _NOW + 60.0,
        )
    finally:
        settlement._TERM_GRACE_SECONDS = 2.0
        settlement._TICK_SECONDS = 0.05
    assert signals[0] is signal.SIGTERM
    assert signal.SIGKILL in signals
    assert signals.index(signal.SIGKILL) > signals.index(signal.SIGTERM)


def test_scrub_releases_capacity_only_after_total_absence(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "app", _request("app"), _Clock(), _Wait())
    assert settlement._scrub_records(key) is False
    record = settlement._RECORDS[(key, "app")]
    record[0] = None
    assert settlement._scrub_records(key)
    assert settlement._record_total_absence(key)


def test_invalid_inputs_fail_closed(monkeypatch):
    with pytest.raises(TypeError):
        settlement._intend_start("pair", "app", _request("app"), _Clock(), _Wait())
    with pytest.raises(TypeError):
        settlement._intend_start(object(), "boss", "not-a-request", _Clock(), _Wait())
    with pytest.raises((TypeError, RelayInvocationError)):
        settlement._intend_start(object(), "app", 42, _Clock(), _Wait())


def test_deadline_is_inclusive_and_clock_failure_quarantines(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "app", _request("app", 30.0), _Clock(), _Wait())

    class BrokenClock:
        def __call__(self):
            return math.nan

    assert not settlement._stop_started_role(key, "app", BrokenClock(), _Wait(), 60.0)


def test_unavailable_waitid_quarantines_finish_without_raising(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "browser", _request("browser"), _Clock(), _Wait())

    import scripts.voice_pipecat_e2e_relay_invocation_process_local as local_mod

    monkeypatch.setattr(
        settlement, "_observe_terminal_unreaped", lambda pid: local_mod._UNAVAILABLE
    )
    monkeypatch.setattr(settlement, "_reap_once", lambda pid: (0, None))
    monkeypatch.setattr(settlement, "_group_exists", lambda pgid: True)
    assert not settlement._finish_started_role(key, "browser", _Clock(), _Wait(), _NOW + 10.0)
    record = settlement._RECORDS[(key, "browser")]
    assert record[3] is True


def test_absent_proc_membership_quarantines_finish_without_raising(monkeypatch):
    key = object()
    _spawn_fakes(monkeypatch)
    settlement._intend_start(key, "browser", _request("browser"), _Clock(), _Wait())

    import scripts.voice_pipecat_e2e_relay_invocation_process_local as local_mod

    observation = type("Result", (), {"si_pid": 4242})
    monkeypatch.setattr(settlement, "_observe_terminal_unreaped", lambda pid: observation)
    monkeypatch.setattr(settlement, "_terminal_status_is_zero", lambda result: True)
    monkeypatch.setattr(settlement, "_live_group_members", lambda pgid: local_mod._UNAVAILABLE)
    monkeypatch.setattr(settlement, "_reap_once", lambda pid: (0, None))
    monkeypatch.setattr(settlement, "_group_exists", lambda pgid: False)
    wait = _Wait()
    clock = _Clock()
    assert not settlement._finish_started_role(key, "browser", clock, wait, _NOW + 10.0)
    record = settlement._RECORDS[(key, "browser")]
    assert record[3] is True

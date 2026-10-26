# ruff: noqa: E402
"""Deterministic dispatch tests with fake settlement seams and no real child."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation_process_lifecycle as settlement_state
import scripts.voice_pipecat_e2e_relay_invocation_process_pair_effects as effects
from scripts.voice_pipecat_e2e_relay_invocation_support import _child_request
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayInvocationError

_NOW = 100.0


class _FinishRequest:
    absolute_deadline = _NOW + 10.0


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


class _Destination:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, value: object) -> None:
        if value is not True and value != {"status": "exited", "returncode": 0}:
            raise RelayInvocationError("unexpected publication")
        self.published.append(value)


class _Grant:
    def __init__(self) -> None:
        self._pair_key = None
        self._clock = lambda: _NOW
        self._wait = lambda seconds: None
        self._runtime_deadline = _NOW + 60.0


@pytest.fixture(autouse=True)
def _isolated_records(monkeypatch):
    monkeypatch.setattr(settlement_state, "_RECORDS", {})
    yield


def _wire(monkeypatch, grant_instance=_Grant()):
    outcomes: dict[str, list[bool | BaseException]] = {}

    def intend_start(pair_key, role, request, clock, wait):
        result = outcomes.get("start", [True])
        raise_or_return = result.pop(0) if result else True
        if isinstance(raise_or_return, BaseException):
            raise raise_or_return
        return raise_or_return

    def finish_started(pair_key, role, clock, wait, deadline):
        result = outcomes.get("finish", [True])
        raise_or_return = result.pop(0) if result else True
        if isinstance(raise_or_return, BaseException):
            raise raise_or_return
        return raise_or_return

    def stop_started(pair_key, role, clock, wait, deadline):
        result = outcomes.get("stop", [True])
        raise_or_return = result.pop(0) if result else True
        if isinstance(raise_or_return, BaseException):
            raise raise_or_return
        return raise_or_return

    monkeypatch.setattr(
        effects,
        "_settlement_module",
        lambda: (intend_start, finish_started, stop_started),
    )
    return outcomes, grant_instance


def _bind_role(monkeypatch, role="app"):
    grant_instance = _Grant()
    grant_instance._pair_key = object()
    pair_key = grant_instance._pair_key
    monkeypatch.setattr(
        effects,
        "_record_for_pair_key",
        lambda key: (
            (grant_instance, None, None, object(), ((role, object()),)) if key is pair_key else None
        ),
    )
    monkeypatch.setattr(
        effects,
        "_role_for_authority",
        lambda key, authority: role if key is pair_key else None,
    )
    monkeypatch.setattr(effects, "_RelayConcreteInvocationGrant", _Grant)
    return pair_key, grant_instance


def test_start_publishes_only_after_settlement(monkeypatch):
    pair_key, _grant = _bind_role(monkeypatch)
    _outcomes, _ = _wire(monkeypatch)
    destination = _Destination()
    effects._concrete_forward(pair_key, object(), _request("app"), destination)
    assert destination.published == [True]


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("settlement"), KeyboardInterrupt(), SystemExit(71)],
)
def test_start_settlement_failure_never_publishes(monkeypatch, failure):
    pair_key, _grant = _bind_role(monkeypatch)
    outcomes, _wired = _wire(monkeypatch)
    outcomes["start"] = [failure]
    destination = _Destination()
    with pytest.raises(BaseException) as caught:
        effects._concrete_forward(pair_key, object(), _request("app"), destination)
    assert type(caught.value) is type(failure)
    assert destination.published == []


def test_start_publication_failure_keeps_settlement_visible(monkeypatch):
    pair_key, _grant = _bind_role(monkeypatch)
    _outcomes, _wired = _wire(monkeypatch)

    class BrokenDestination:
        def publish(self, value: object) -> None:
            raise RuntimeError("publication")

    with pytest.raises(RuntimeError):
        effects._concrete_forward(pair_key, object(), _request("app"), BrokenDestination())


def test_finish_publishes_exact_zero_exit_only_after_settlement(monkeypatch):
    pair_key, _grant = _bind_role(monkeypatch, role="browser")
    _outcomes, _wired = _wire(monkeypatch)
    destination = _Destination()

    monkeypatch.setattr(effects, "RelayFinishRequest", _FinishRequest)
    effects._concrete_forward(pair_key, object(), _FinishRequest(), destination)
    assert destination.published == [{"status": "exited", "returncode": 0}]


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("settlement"), KeyboardInterrupt(), SystemExit(71)],
)
def test_finish_settlement_failure_never_publishes(monkeypatch, failure):
    pair_key, _grant = _bind_role(monkeypatch, role="browser")
    outcomes, _wired = _wire(monkeypatch)
    outcomes["finish"] = [failure]
    monkeypatch.setattr(effects, "RelayFinishRequest", _FinishRequest)
    destination = _Destination()
    with pytest.raises(BaseException) as caught:
        effects._concrete_forward(pair_key, object(), _FinishRequest(), destination)
    assert type(caught.value) is type(failure)
    assert destination.published == []


def test_foreign_request_shapes_fail_closed_without_settlement(monkeypatch):
    pair_key, _grant = _bind_role(monkeypatch)
    _outcomes, _wired = _wire(monkeypatch)
    destination = _Destination()
    with pytest.raises(RelayInvocationError):
        effects._concrete_forward(pair_key, object(), {"role": "app"}, destination)
    with pytest.raises(RelayInvocationError):
        effects._concrete_forward(pair_key, object(), None, destination)
    assert destination.published == []


def test_unmatched_role_request_pair_fails_closed(monkeypatch):
    pair_key, _grant = _bind_role(monkeypatch, role="app")
    _outcomes, _wired = _wire(monkeypatch)
    destination = _Destination()
    with pytest.raises(RelayInvocationError):
        effects._concrete_forward(pair_key, object(), _request("browser"), destination)
    assert destination.published == []


class _PrebootstrapRequest:
    url = "http://127.0.0.1:8101/_e2e/pipecat/prebootstrap"
    authorization = "Bearer voice-e2e"

    @property
    def body(self) -> dict[str, str]:
        return {"session_id": "session", "voice_call_id": "call"}


class _PrebootstrapDestination:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, value: object) -> None:
        self.published.append(value)


def _wire_prebootstrap(monkeypatch, pair_key: object, live: bool, response):
    calls: list[dict[str, object]] = []

    class _Transport:
        def request(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return response

    import scripts.voice_pipecat_e2e_relay_invocation_prebootstrap as prebootstrap_module
    import scripts.voice_pipecat_e2e_relay_invocation_process_lifecycle as settle_mod
    import scripts.voice_pipecat_e2e_relay_invocation_process_local as local_mod

    monkeypatch.setattr(prebootstrap_module, "RelayPrebootstrapRequest", _PrebootstrapRequest)
    monkeypatch.setattr(local_mod, "_new_loopback_transport", lambda host: _Transport())
    monkeypatch.setattr(
        settle_mod,
        "_role_has_live_record",
        lambda key, role: live and key is pair_key,
    )
    return calls


def test_prebootstrap_publishes_exact_prepared_response(monkeypatch):
    pair_key, _grant_instance = _bind_role(monkeypatch, role="app")
    _outcomes, _wired = _wire(monkeypatch)
    response = (
        200,
        {},
        b'{"schema_version": 1, "status": "prepared", "expires_at_epoch_seconds": 8100000100}',
    )
    calls = _wire_prebootstrap(monkeypatch, pair_key, True, response)
    destination = _PrebootstrapDestination()
    effects._concrete_forward(pair_key, object(), _PrebootstrapRequest(), destination)
    assert destination.published == [
        {
            "schema_version": 1,
            "status": "prepared",
            "expires_at_epoch_seconds": 8100000100,
        }
    ]
    assert calls[0]["port"] == 8101
    assert calls[0]["method"] == "POST"


@pytest.mark.parametrize(
    "response",
    [
        (302, {}, b"redirect"),
        (500, {}, b"server error"),
        (200, {}, b"not json"),
        (200, {}, b'{"status": "prepared"}'),
    ],
)
def test_prebootstrap_foreign_responses_never_publish(monkeypatch, response):
    pair_key, _grant_instance = _bind_role(monkeypatch, role="app")
    _outcomes, _wired = _wire(monkeypatch)
    _wire_prebootstrap(monkeypatch, pair_key, True, response)
    destination = _PrebootstrapDestination()
    with pytest.raises(RelayInvocationError):
        effects._concrete_forward(pair_key, object(), _PrebootstrapRequest(), destination)
    assert destination.published == []


def test_prebootstrap_requires_a_live_app_child(monkeypatch):
    pair_key, _grant_instance = _bind_role(monkeypatch, role="app")
    _outcomes, _wired = _wire(monkeypatch)
    response = (
        200,
        {},
        b'{"schema_version": 1, "status": "prepared", "expires_at_epoch_seconds": 8100000100}',
    )
    _wire_prebootstrap(monkeypatch, pair_key, False, response)
    destination = _PrebootstrapDestination()
    with pytest.raises(RelayInvocationError):
        effects._concrete_forward(pair_key, object(), _PrebootstrapRequest(), destination)
    assert destination.published == []

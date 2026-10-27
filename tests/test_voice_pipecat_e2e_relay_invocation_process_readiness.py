# ruff: noqa: E402
"""Deterministic readiness tests with fake transport and no real child."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation_process_lifecycle as settlement_state
import scripts.voice_pipecat_e2e_relay_invocation_process_readiness as readiness
from scripts.voice_pipecat_e2e_relay_invocation_support import _child_request

_NOW = 100.0
_NONCE = "ab" * 32
_DOMAIN = b"murmur-relay-process-ready:v1:"


def _digest(nonce: str = _NONCE) -> str:
    return hashlib.sha256(_DOMAIN + nonce.encode("ascii")).hexdigest()


class _Grant:
    def __init__(self) -> None:
        self._clock = lambda: _NOW
        self._wait = lambda seconds: None
        self._runtime_deadline = _NOW + 60.0


def _request(role: str) -> object:
    completion = "started" if role == "browser" else "ready"
    name = (
        "MURMUR_PIPECAT_E2E_PROCESS_READY_NONCE"
        if role == "app"
        else "VOICE_E2E_PROCESS_READY_NONCE"
    )
    return _child_request(
        role=role,
        command=("/python", "entry.py"),
        cwd=Path("/run/workspace"),
        environment={name: _NONCE},
        completion=completion,
        absolute_deadline=_NOW + 60.0,
    )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(settlement_state, "_RECORDS", {})
    monkeypatch.setattr(readiness, "_TICK_SECONDS", 0.0)
    yield


def _transport(monkeypatch, responses: list[object]):
    calls: list[dict[str, object]] = []

    class _Transport:
        def request(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return responses.pop(0) if responses else (200, {}, b"{}")

    monkeypatch.setattr(readiness, "_new_loopback_transport", lambda host: _Transport())
    return calls


def _live(monkeypatch, pair_key: object, live: bool = True):
    monkeypatch.setattr(
        settlement_state, "_role_has_live_record", lambda key, role: live and key is pair_key
    )
    monkeypatch.setattr(
        readiness,
        "_role_is_live",
        lambda key, role: live and key is pair_key,
    )


def test_exact_digest_with_live_child_satisfies_readiness(monkeypatch):
    pair_key = object()
    calls = _transport(
        monkeypatch, [(200, {}, ('{"process_ready_digest": "%s"}' % _digest()).encode())]
    )
    _live(monkeypatch, pair_key)
    assert readiness._wait_role_ready(pair_key, "app", _request("app"), _Grant(), _NOW + 60.0)
    assert calls[0]["port"] == 8101
    assert calls[0]["path"] == "/_e2e/health"


def test_web_readiness_uses_the_web_route(monkeypatch):
    pair_key = object()
    calls = _transport(
        monkeypatch, [(200, {}, ('{"process_ready_digest": "%s"}' % _digest()).encode())]
    )
    _live(monkeypatch, pair_key)
    assert readiness._wait_role_ready(pair_key, "web", _request("web"), _Grant(), _NOW + 60.0)
    assert calls[0]["port"] == 3100
    assert calls[0]["path"] == "/_ready"


def test_foreign_200_never_satisfies_readiness(monkeypatch):
    pair_key = object()
    _transport(
        monkeypatch,
        [
            (200, {}, b'{"process_ready_digest": "123"}'),
            (200, {}, b'{"ok": true}'),
            (404, {}, b"not found"),
        ],
    )
    _live(monkeypatch, pair_key)
    assert not readiness._wait_role_ready(pair_key, "app", _request("app"), _Grant(), _NOW + 60.5)


def test_dead_child_never_satisfies_readiness(monkeypatch):
    pair_key = object()
    calls = []  # transport must never be reached after death

    class _Transport:
        def request(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return 200, {}, ('{"process_ready_digest": "%s"}' % _digest()).encode()

    monkeypatch.setattr(readiness, "_new_loopback_transport", lambda host: _Transport())
    _live(monkeypatch, pair_key, live=False)
    assert not readiness._wait_role_ready(pair_key, "app", _request("app"), _Grant(), _NOW + 60.0)
    assert calls == []


def test_child_death_after_response_blocks_readiness(monkeypatch):
    pair_key = object()
    _transport(monkeypatch, [(200, {}, ('{"process_ready_digest": "%s"}' % _digest()).encode())])
    monkeypatch.setattr(
        readiness,
        "_role_is_live",
        lambda key, role: False,
    )
    assert not readiness._wait_role_ready(pair_key, "app", _request("app"), _Grant(), _NOW + 60.0)


def test_missing_or_malformed_nonce_never_polls(monkeypatch):
    pair_key = object()
    calls = _transport(monkeypatch, [])
    _live(monkeypatch, pair_key)
    request = _child_request(
        role="app",
        command=("/python",),
        cwd=Path("/run"),
        environment={},
        completion="ready",
        absolute_deadline=_NOW + 60.0,
    )
    assert not readiness._wait_role_ready(pair_key, "app", request, _Grant(), _NOW + 60.0)
    request_bad = _child_request(
        role="app",
        command=("/python",),
        cwd=Path("/run"),
        environment={"MURMUR_PIPECAT_E2E_PROCESS_READY_NONCE": "XYZ"},
        completion="ready",
        absolute_deadline=_NOW + 60.0,
    )
    assert not readiness._wait_role_ready(pair_key, "app", request_bad, _Grant(), _NOW + 60.0)
    assert calls == []


def test_expired_deadline_never_polls(monkeypatch):
    pair_key = object()
    calls = _transport(monkeypatch, [])
    _live(monkeypatch, pair_key)
    assert not readiness._wait_role_ready(pair_key, "app", _request("app"), _Grant(), _NOW - 1.0)
    assert calls == []

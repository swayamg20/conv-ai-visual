"""Exact child-bound readiness for the private concrete process adapter.

Readiness is proved only by the exact health digest of the per-role nonce that
the caller injected into the replacement environment, over the private literal
loopback transport, with the owned child revalidated as freshly live before and
after the response. A foreign 200 that cannot present the exact digest never
satisfies readiness. Secrets never leave this module except as digests already
published by the guarded children.
"""

from __future__ import annotations

import hashlib
import json
import math

from scripts.voice_pipecat_e2e_relay_invocation_process_local import (
    _new_loopback_transport,
)

_FAILURE = "Relay concrete process readiness is invalid"
_NONCE_ENV_NAMES = {
    "app": "MURMUR_PIPECAT_E2E_PROCESS_READY_NONCE",
    "web": "VOICE_E2E_PROCESS_READY_NONCE",
}
_HEALTH_PORT = {"app": 8101, "web": 3100}
_HEALTH_PATH = {"app": "/_e2e/health", "web": "/_ready"}
_PROCESS_READY_DOMAIN = b"murmur-relay-process-ready:v1:"
_TICK_SECONDS = 0.05
_SPIN_LIMIT = 512


def _readiness_checkpoint(_role: str, _stage: str) -> None:
    """Deterministic cut seam around durable readiness facts."""


def _expected_digest(role: str, request: object) -> str | None:
    environment = request.environment
    name = _NONCE_ENV_NAMES.get(role)
    if type(environment) is not dict or name is None:
        return None
    nonce = environment.get(name)
    if type(nonce) is not str or len(nonce) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in nonce):
        return None
    return hashlib.sha256(_PROCESS_READY_DOMAIN + nonce.encode("ascii")).hexdigest()


def _parse_health_response(payload: object) -> str | None:
    if type(payload) is not bytes or not payload:
        return None
    try:
        value = json.loads(payload)
    except BaseException:
        return None
    if type(value) is not dict:
        return None
    digest = value.get("process_ready_digest")
    if type(digest) is not str:
        return None
    return digest


def _role_is_live(pair_key: object, role: str) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_lifecycle import (
        _role_has_live_record,
    )

    return _role_has_live_record(pair_key, role)


def _wait_role_ready(
    pair_key: object, role: str, request: object, grant: object, deadline: float
) -> bool:
    """Poll the exact child-bound health digest until it matches."""

    if role not in _NONCE_ENV_NAMES:
        return False
    expected = _expected_digest(role, request)
    remaining = deadline - grant._clock()
    if (
        expected is None
        or type(remaining) is not float
        or not math.isfinite(remaining)
        or remaining <= 0.0
    ):
        return False
    transport = _new_loopback_transport("127.0.0.1")
    port = _HEALTH_PORT[role]
    path = _HEALTH_PATH[role]
    for _attempt in range(_SPIN_LIMIT):
        if not _role_is_live(pair_key, role):
            return False
        now = grant._clock()
        if type(now) is not float or not math.isfinite(now) or now >= deadline:
            return False
        budget = deadline - now
        if type(budget) is not float or budget <= 0.0:
            return False
        _readiness_checkpoint(role, "request")
        status, _headers, payload = transport.request(
            port=port,
            method="GET",
            path=path,
            headers={"Host": f"127.0.0.1:{port}"},
            body=None,
            timeout_seconds=budget,
        )
        _readiness_checkpoint(role, "response")
        digest = _parse_health_response(payload)
        if (
            type(status) is int
            and status == 200
            and digest == expected
            and _role_is_live(pair_key, role)
        ):
            _readiness_checkpoint(role, "digest-bound")
            return True
        grant._wait(_TICK_SECONDS)
    return False


__all__: list[str] = []

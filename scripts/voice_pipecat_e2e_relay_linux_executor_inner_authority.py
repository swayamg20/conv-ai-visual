"""Exact call authority for the private consumed-build inner relay owner."""

from __future__ import annotations

from datetime import datetime

from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_anchor import (
    _executor_inner_authority_anchor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_values import (
    _RelayLinuxExecutorInnerEvidence,
    _RelayLinuxExecutorInnerResultDestination,
)
from scripts.voice_pipecat_e2e_relay_owner_state import _same_owner_binding


def _inner_authority_values(
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> tuple[object, ...]:
    return (
        evidence.build.binding,
        evidence.runner,
        evidence.bridge_probe,
        evidence.tools,
        evidence.invocation_driver,
        evidence.static_auth_secret,
        evidence.now,
        evidence.browser_timeout_seconds,
        evidence.runtime_timeout_seconds,
        evidence.clock,
        evidence.wait,
        evidence.epoch_clock,
        evidence.owner_binding,
        evidence.result_destination,
    )


def _authority_values_have_shape(values: object) -> bool:
    return bool(
        type(values) is tuple
        and len(values) == 14
        and type(values[0]) is _RelayLinuxExecutorBuiltBinding
        and type(values[6]) is datetime
        and type(values[7]) is float
        and type(values[8]) is float
        and callable(values[9])
        and callable(values[10])
        and callable(values[11])
        and type(values[12]) is tuple
        and len(values[12]) == 11
        and type(values[13]) is _RelayLinuxExecutorInnerResultDestination
    )


def _authority_values_match_evidence(
    values: object,
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> bool:
    if not _authority_values_have_shape(values):
        return False
    expected = _inner_authority_values(evidence)
    return bool(
        all(values[index] is expected[index] for index in range(7))
        and values[7] == expected[7]
        and values[8] == expected[8]
        and all(values[index] is expected[index] for index in range(9, 14))
        and _same_owner_binding(values[12], expected[12])
    )


def _live_inner_authority_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
    authority: object,
    *,
    allow_terminal: bool = False,
) -> bool:
    anchor = _executor_inner_authority_anchor(evidence.key)
    if anchor is None or not (
        type(authority) is tuple and len(authority) in {3, 4} and anchor._matches(authority[1])
    ):
        return False
    if _live_inner_authority_core_matches(
        evidence,
        authority,
    ) and evidence.result_destination._replay_values_are(authority[1]):
        return True
    return bool(
        allow_terminal
        and type(authority) is tuple
        and len(authority) == 4
        and authority[0] is evidence.result_destination
        and type(authority[2]) is str
        and authority[2] == "terminal"
        and authority[3] is evidence.result_destination._terminal_token
        and evidence.result_destination._key_ref() is evidence.key
        and evidence.result_destination._replay_values_are(authority[1])
        and _authority_values_match_evidence(authority[1], evidence)
    )


def _live_inner_authority_core_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
    authority: object,
) -> bool:
    return bool(
        type(authority) is tuple
        and len(authority) == 3
        and authority[0] is evidence
        and type(authority[2]) is str
        and authority[2] == "live"
        and evidence.result_destination._key_ref() is evidence.key
        and _authority_values_match_evidence(authority[1], evidence)
    )


def _call_values_match(
    values: object,
    *,
    binding: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_driver: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
) -> bool:
    return bool(
        _authority_values_have_shape(values)
        and values[0] is binding
        and values[1] is runner
        and values[2] is bridge_probe
        and values[3] is tools
        and values[4] is invocation_driver
        and values[5] is static_auth_secret
        and type(now) is datetime
        and now == values[6]
        and type(browser_timeout_seconds) is float
        and browser_timeout_seconds == values[7]
        and type(runtime_timeout_seconds) is float
        and runtime_timeout_seconds == values[8]
        and values[9] is clock
        and values[10] is wait
        and values[11] is epoch_clock
    )


__all__: list[str] = []

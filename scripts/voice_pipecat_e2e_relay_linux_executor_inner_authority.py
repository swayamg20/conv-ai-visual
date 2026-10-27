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
    _RelayLinuxExecutorInnerReplayDescriptor,
    _RelayLinuxExecutorInnerResultDestination,
)
from scripts.voice_pipecat_e2e_relay_owner_state import _same_owner_binding


def _inner_authority_values(
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> tuple[object, ...]:
    """Return replay-safe call values without the live owner capability graph."""

    return evidence.replay_values


def _authority_values_have_shape(values: object) -> bool:
    return bool(
        type(values) is tuple
        and len(values) == 14
        and type(values[0]) is _RelayLinuxExecutorBuiltBinding
        and type(values[1]) is _RelayLinuxExecutorInnerReplayDescriptor
        and all(values[index] is None for index in range(2, 6))
        and type(values[6]) is datetime
        and type(values[7]) is float
        and type(values[8]) is float
        and all(values[index] is None for index in range(9, 13))
        and type(values[13]) is _RelayLinuxExecutorInnerResultDestination
    )


def _authority_values_match_evidence(
    values: object,
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> bool:
    try:
        if not _authority_values_have_shape(values):
            return False
        expected = _inner_authority_values(evidence)
        return bool(
            values is expected
            and values[0] is evidence.build.binding
            and values[1] is evidence.replay_descriptor
            and evidence.replay_descriptor._matches(
                (
                    evidence.runner,
                    evidence.bridge_probe,
                    evidence.tools,
                    evidence.invocation_selection,
                    evidence.static_auth_secret,
                    evidence.clock,
                    evidence.wait,
                    evidence.epoch_clock,
                )
            )
            and all(values[index] is expected[index] for index in range(2, 7))
            and values[7] == expected[7]
            and values[8] == expected[8]
            and all(values[index] is expected[index] for index in range(9, 14))
        )
    except BaseException:
        return False


def _preparing_inner_authority_matches(key: object, authority: object) -> bool:
    try:
        return bool(
            type(authority) is tuple
            and len(authority) == 4
            and type(authority[0]) is _RelayLinuxExecutorInnerResultDestination
            and _authority_values_have_shape(authority[1])
            and type(authority[2]) is str
            and authority[2] == "preparing"
            and type(getattr(authority[0], "_preparing_token", None)) is object
            and authority[3] is authority[0]._preparing_token
            and authority[0]._key_ref() is key
            and authority[0]._replay_values_are(authority[1])
            and (anchor := _executor_inner_authority_anchor(key)) is not None
            and anchor._matches(authority[1])
        )
    except BaseException:
        return False


def _live_inner_authority_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
    authority: object,
    *,
    allow_terminal: bool = False,
) -> bool:
    try:
        anchor = _executor_inner_authority_anchor(evidence.key)
        if anchor is None or not (
            type(authority) is tuple and len(authority) == 4 and anchor._matches(authority[1])
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
            and type(getattr(evidence.result_destination, "_terminal_token", None)) is object
            and authority[3] is getattr(evidence.result_destination, "_terminal_token", None)
            and evidence.result_destination._key_ref() is evidence.key
            and evidence.result_destination._replay_values_are(authority[1])
            and _authority_values_match_evidence(authority[1], evidence)
        )
    except BaseException:
        return False


def _live_inner_authority_core_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
    authority: object,
) -> bool:
    try:
        return bool(
            type(authority) is tuple
            and len(authority) == 4
            and authority[0] is evidence
            and type(authority[2]) is str
            and authority[2] == "live"
            and type(authority[3]) is tuple
            and authority[3] is evidence.owner_binding
            and _same_owner_binding(authority[3], evidence.owner_binding)
            and evidence.result_destination._key_ref() is evidence.key
            and _authority_values_match_evidence(authority[1], evidence)
        )
    except BaseException:
        return False


def _call_values_match(
    values: object,
    *,
    binding: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_selection: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
) -> bool:
    try:
        return bool(
            _authority_values_have_shape(values)
            and values[0] is binding
            and values[1]._matches(
                (
                    runner,
                    bridge_probe,
                    tools,
                    invocation_selection,
                    static_auth_secret,
                    clock,
                    wait,
                    epoch_clock,
                )
            )
            and type(now) is datetime
            and now == values[6]
            and type(browser_timeout_seconds) is float
            and browser_timeout_seconds == values[7]
            and type(runtime_timeout_seconds) is float
            and runtime_timeout_seconds == values[8]
        )
    except BaseException:
        return False


__all__: list[str] = []

"""Replay-safe preauthority committed before concrete adapter construction."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_executor_inner_anchor import (
    _executor_inner_authority_anchor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_authority import (
    _call_values_match,
    _preparing_inner_authority_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_values import (
    _new_inner_replay_descriptor,
    _RelayLinuxExecutorInnerReplayDescriptor,
    _RelayLinuxExecutorInnerResultDestination,
)


def _resolve_or_preown_inner_preparation(
    key: object,
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
) -> (
    tuple[
        _RelayLinuxExecutorInnerResultDestination,
        _RelayLinuxExecutorInnerReplayDescriptor,
        tuple[object, ...],
    ]
    | None
):
    """Bind the exact replay-safe call before any concrete pair can exist."""

    replay_inputs = (
        runner,
        bridge_probe,
        tools,
        invocation_selection,
        static_auth_secret,
        clock,
        wait,
        epoch_clock,
    )
    try:
        candidate_descriptor = _new_inner_replay_descriptor(replay_inputs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None

    from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
        _INNER_AUTHORITIES,
        _LOCK,
        _new_inner_result_destination,
        _store_inner_authority,
    )

    with _LOCK:
        replay_prefix = (
            binding,
            candidate_descriptor,
            None,
            None,
            None,
            None,
            now,
            browser_timeout_seconds,
            runtime_timeout_seconds,
            None,
            None,
            None,
            None,
        )
        result_destination = _new_inner_result_destination(key, replay_prefix)
        authority = _INNER_AUTHORITIES.get(key)
        if authority is not None:
            values = (
                authority[1]
                if _preparing_inner_authority_matches(key, authority)
                and authority[0] is result_destination
                else None
            )
            return _resolved_preparation(
                result_destination,
                values,
                binding=binding,
                runner=runner,
                bridge_probe=bridge_probe,
                tools=tools,
                invocation_selection=invocation_selection,
                static_auth_secret=static_auth_secret,
                now=now,
                browser_timeout_seconds=browser_timeout_seconds,
                runtime_timeout_seconds=runtime_timeout_seconds,
                clock=clock,
                wait=wait,
                epoch_clock=epoch_clock,
            )
        values = result_destination._read_replay_values()
        resolved = _resolved_preparation(
            result_destination,
            values,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )
        if resolved is None:
            return None
        anchor = _executor_inner_authority_anchor(key)
        if anchor is None or anchor._bind(values) is not values:
            return None
        preparing = (
            result_destination,
            values,
            "preparing",
            result_destination._preparing_token,
        )
        _store_inner_authority(key, preparing)
        authority = _INNER_AUTHORITIES.get(key)
        return resolved if _preparing_inner_authority_matches(key, authority) else None


def _resolved_preparation(
    destination: _RelayLinuxExecutorInnerResultDestination,
    values: object,
    **call: object,
) -> (
    tuple[
        _RelayLinuxExecutorInnerResultDestination,
        _RelayLinuxExecutorInnerReplayDescriptor,
        tuple[object, ...],
    ]
    | None
):
    if not (
        type(values) is tuple
        and len(values) == 14
        and type(values[1]) is _RelayLinuxExecutorInnerReplayDescriptor
        and values[13] is destination
        and destination._replay_values_are(values)
        and _call_values_match(values, **call)
    ):
        return None
    return destination, values[1], values


__all__: list[str] = []

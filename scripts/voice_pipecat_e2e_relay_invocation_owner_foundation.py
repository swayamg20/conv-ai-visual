"""Replay-safe publication foundation for one staged invocation owner."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup import (
    _register_cleanup_owner,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup_authority import (
    _resolve_cleanup_owner,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _bind_concrete_invocation_owner_destinations,
    _concrete_invocation_cleanup_contract,
    _live_pair_destination,
)
from scripts.voice_pipecat_e2e_relay_invocation_support import _scrub_exception


def _reconcile_invocation_owner_foundation(
    owner: object,
    driver: object,
    tools: object,
    owner_destination: object,
) -> tuple[bool, ControlSignal | None]:
    from scripts.voice_pipecat_e2e_relay_invocation_owner_values import RelayInvocationOwner

    if not (
        type(owner) is RelayInvocationOwner
        and owner._driver is driver
        and owner._tools is tools
        and owner._destination is owner_destination
    ):
        return False, None
    registered, control = _reconcile_cleanup_registration(owner)
    if not registered:
        return False, control
    contract = _concrete_invocation_cleanup_contract(driver, tools)
    concrete = bool(
        owner._cleanup_timeout_seconds is not None
        or owner._cleanup_clock is not None
        or type(getattr(driver, "_pair_key", None)) is object
    )
    if contract is None:
        return not concrete, control
    if not (owner._cleanup_timeout_seconds == contract[1] and owner._cleanup_clock is contract[2]):
        return False, control
    bound, bind_control = _reconcile_concrete_owner_graph(
        owner,
        driver,
        tools,
        owner_destination,
    )
    return bound, control or bind_control


def _reconcile_cleanup_registration(owner: object) -> tuple[bool, ControlSignal | None]:
    from scripts.voice_pipecat_e2e_relay_invocation_owner_values import RelayInvocationOwner

    authority = owner._cleanup_authority
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            if _resolve_cleanup_owner(authority, RelayInvocationOwner) is owner:
                return True, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
        try:
            _register_cleanup_owner(authority, owner)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
    try:
        return _resolve_cleanup_owner(authority, RelayInvocationOwner) is owner, control
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    return False, control


def _reconcile_concrete_owner_graph(
    owner: object,
    driver: object,
    tools: object,
    owner_destination: object,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        matched, match_control = _concrete_owner_graph_matches(
            owner,
            driver,
            tools,
            owner_destination,
        )
        control = control or match_control
        if matched:
            return True, control
        try:
            _bind_concrete_invocation_owner_destinations(
                driver,
                tools,
                owner_destination,
                owner,
                owner._owner_token,
                (owner._app, owner._web, owner._browser),
                (owner._app_start, owner._web_start, owner._browser_start),
                (owner._app_stop, owner._web_stop, owner._browser_stop),
            )
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
    matched, match_control = _concrete_owner_graph_matches(
        owner,
        driver,
        tools,
        owner_destination,
    )
    return matched, control or match_control


def _concrete_owner_graph_matches(
    owner: object,
    driver: object,
    tools: object,
    owner_destination: object,
) -> tuple[bool, ControlSignal | None]:
    try:
        pair_destination = _live_pair_destination(driver, tools)
        if pair_destination is None:
            return False, None
        state = pair_destination._read_retirement_state()
        graph = state[2] if type(state) is tuple and len(state) == 9 else None
        expected = (
            owner_destination,
            owner,
            owner._owner_token,
            owner._app,
            owner._web,
            owner._browser,
            owner._app_start,
            owner._web_start,
            owner._browser_start,
            owner._app_stop,
            owner._web_stop,
            owner._browser_stop,
        )
        return bool(
            state[0] in {"live", "cleanup"}
            and type(graph) is tuple
            and len(graph) == 12
            and type(graph[2]) is object
            and all(graph[index] is expected[index] for index in range(len(expected)))
        ), None
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
        return False, control
    except BaseException as error:
        _scrub_exception(error)
        return False, None


__all__: list[str] = []

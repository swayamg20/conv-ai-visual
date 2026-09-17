"""Recoverable terminal publication for one Coturn evidence drain."""

from __future__ import annotations

from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
)

_MAX_TERMINAL_TRANSITION_ATTEMPTS = 64
_TERMINAL_TARGETS = frozenset({"complete", "cleaned"})


class DrainTerminalTransition:
    """Immutable phase plus the complete resource tuple needed for recovery."""

    __slots__ = ("clock", "phase", "process", "pump", "summary", "target", "thread")

    def __init__(
        self,
        *,
        target: str,
        phase: str,
        process: object,
        pump: object,
        thread: object,
        clock: object,
        summary: object,
    ) -> None:
        if target not in _TERMINAL_TARGETS or phase not in {"owned", "released", "empty"}:
            raise TypeError("Coturn drain terminal transition is invalid")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "process", process)
        object.__setattr__(self, "pump", pump)
        object.__setattr__(self, "thread", thread)
        object.__setattr__(self, "clock", clock)
        object.__setattr__(self, "summary", summary)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("Coturn drain terminal transition is immutable")

    def __copy__(self) -> DrainTerminalTransition:
        raise TypeError("Coturn drain terminal transition cannot be copied")

    def __deepcopy__(self, _memo: object) -> DrainTerminalTransition:
        raise TypeError("Coturn drain terminal transition cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Coturn drain terminal transition cannot be serialized")


def transition_target(drain: object) -> str | None:
    lock = object.__getattribute__(drain, "_lock")
    with lock:
        transition = object.__getattribute__(drain, "_terminal_transition")
        if type(transition) is DrainTerminalTransition:
            return transition.target
        state = object.__getattribute__(drain, "_state")
    if state == "terminalizing-complete":
        return "complete"
    if state == "terminalizing-cleaned":
        return "cleaned"
    return None


def release_resources(drain: object) -> tuple[object, object, object]:
    """Read a complete recovery tuple even between legacy field clears."""

    lock = object.__getattribute__(drain, "_lock")
    with lock:
        transition = object.__getattribute__(drain, "_terminal_transition")
        if type(transition) is DrainTerminalTransition and transition.phase in {
            "owned",
            "released",
        }:
            process = transition.process
            pump = transition.pump
        else:
            process = object.__getattribute__(drain, "_process")
            pump = object.__getattribute__(drain, "_pump")
        owner = object.__getattribute__(drain, "_owner_token")
    return process, pump, owner


def settle_terminal_transition(
    drain: object,
    *,
    target: str,
    release_claim: Callable[[object], tuple[bool, ControlSignal | None]],
) -> tuple[bool, ControlSignal | None]:
    """Drive a terminal transaction while retaining the first control signal."""

    if target not in _TERMINAL_TARGETS:
        return True, None
    control: ControlSignal | None = None
    for _ in range(_MAX_TERMINAL_TRANSITION_ATTEMPTS):
        try:
            done, failed, step_control = _terminal_step(
                drain,
                target=target,
                release_claim=release_claim,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _persist_control(drain, control)
            continue
        except BaseException:
            continue
        control = control or step_control
        if control is not None:
            _persist_control(drain, control)
        if done:
            return False, control
        if failed:
            return True, control
    return True, control


def _terminal_step(
    drain: object,
    *,
    target: str,
    release_claim: Callable[[object], tuple[bool, ControlSignal | None]],
) -> tuple[bool, bool, ControlSignal | None]:
    lock = object.__getattribute__(drain, "_lock")
    with lock:
        transition = object.__getattribute__(drain, "_terminal_transition")
        state = object.__getattribute__(drain, "_state")
        if transition is None:
            if state == target and _terminal_graph_is_empty_locked(drain, target):
                return True, False, None
            snapshot = DrainTerminalTransition(
                target=target,
                phase="owned",
                process=object.__getattribute__(drain, "_process"),
                pump=object.__getattribute__(drain, "_pump"),
                thread=object.__getattribute__(drain, "_thread"),
                clock=object.__getattribute__(drain, "_clock"),
                summary=object.__getattribute__(drain, "_summary"),
            )
            object.__setattr__(drain, "_state", f"terminalizing-{target}")
            object.__setattr__(drain, "_terminal_transition", snapshot)
            return False, False, None
        if type(transition) is not DrainTerminalTransition or transition.target != target:
            return False, True, None
        phase = transition.phase

    if phase == "owned":
        failed, control = release_claim(drain)
        if failed:
            return False, True, control
        replacement = DrainTerminalTransition(
            target=target,
            phase="released",
            process=transition.process,
            pump=transition.pump,
            thread=transition.thread,
            clock=transition.clock,
            summary=transition.summary,
        )
        with lock:
            current = object.__getattribute__(drain, "_terminal_transition")
            if current is transition:
                object.__setattr__(drain, "_terminal_transition", replacement)
        return False, False, control

    if phase == "released":
        return _clear_one_terminal_resource(drain, transition, target)

    with lock:
        if object.__getattribute__(drain, "_state") != target:
            object.__setattr__(drain, "_state", target)
            return False, False, None
        object.__setattr__(drain, "_terminal_transition", None)
    return False, False, None


def _clear_one_terminal_resource(
    drain: object,
    transition: DrainTerminalTransition,
    target: str,
) -> tuple[bool, bool, ControlSignal | None]:
    lock = object.__getattribute__(drain, "_lock")
    with lock:
        if object.__getattribute__(drain, "_process") is not None:
            object.__setattr__(drain, "_process", None)
            return False, False, None
        if object.__getattribute__(drain, "_pump") is not None:
            object.__setattr__(drain, "_pump", None)
            return False, False, None
        if object.__getattribute__(drain, "_thread") is not None:
            object.__setattr__(drain, "_thread", None)
            return False, False, None
        if object.__getattribute__(drain, "_clock") is not None:
            object.__setattr__(drain, "_clock", None)
            return False, False, None
        if target == "cleaned" and object.__getattribute__(drain, "_summary") is not None:
            object.__setattr__(drain, "_summary", None)
            return False, False, None
        empty = DrainTerminalTransition(
            target=target,
            phase="empty",
            process=None,
            pump=None,
            thread=None,
            clock=None,
            summary=None,
        )
        if object.__getattribute__(drain, "_terminal_transition") is transition:
            object.__setattr__(drain, "_terminal_transition", empty)
    return False, False, None


def _terminal_graph_is_empty_locked(drain: object, target: str) -> bool:
    resources_empty = all(
        object.__getattribute__(drain, name) is None
        for name in ("_process", "_pump", "_thread", "_clock")
    )
    summary_empty = target != "cleaned" or object.__getattribute__(drain, "_summary") is None
    return bool(resources_empty and summary_empty)


def _persist_control(drain: object, control: ControlSignal) -> None:
    try:
        lock = object.__getattribute__(drain, "_lock")
        with lock:
            if object.__getattribute__(drain, "_control") is None:
                object.__setattr__(drain, "_control", control)
    except (KeyboardInterrupt, SystemExit):
        pass
    except BaseException:
        pass

"""Durable terminal graph scrub and observation publication for relay B0."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_runtime import (
    ContainerAbsenceReceipt,
    NetworkAbsenceReceipt,
    RuntimeTlsMaterial,
)
from scripts.voice_pipecat_e2e_relay_browser_result import RelayBrowserObservation
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwner,
    _RelayTerminalTransition,
)
from scripts.voice_pipecat_e2e_relay_owner_values import (
    RelayProbeObservation,
    _new_observation,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeRun

_TERMINAL_ROOT_NAMES = (
    "_runner",
    "_bridge_probe",
    "_tools",
    "_identity",
    "_paths",
    "_invocation_driver",
    "_invocation_tools",
    "_invocation_destination",
    "_owner_destination",
    "_clock",
    "_wait",
    "_pending_authority",
    "_run",
)


def _terminalize(owner: RelayProbeOwner) -> bool:
    with owner._lock:
        transition = owner._terminal_transition
        if (
            type(transition) is _RelayTerminalTransition
            and transition.phase in {"preparing", "scrubbed"}
            and transition.publish
            and not owner._publish_requested
        ):
            owner._terminal_transition = _RelayTerminalTransition(
                phase=transition.phase,
                publish=False,
                run=None,
                facts_valid=True,
            )
            transition = owner._terminal_transition
        publish = bool(owner._publish_requested and owner._control is None)
        if transition is None:
            facts_valid = _terminal_facts_valid(owner) if publish else True
            if publish and not facts_valid:
                owner._publish_requested = False
                publish = False
            transition = _RelayTerminalTransition(
                phase="preparing",
                publish=publish,
                run=owner._run if publish else None,
                facts_valid=facts_valid,
            )
            owner._terminal_transition = transition
            owner._state = "terminal-scrubbing"
        elif transition.phase == "published":
            if type(transition.observation) is RelayProbeObservation:
                owner._observation = transition.observation
                owner._state = "observed"
                owner._cleanup_complete = True
                return _terminal_complete(owner)
            return False
    if transition.phase == "preparing":
        _scrub_terminal_roots(owner)
        with owner._lock:
            if not owner._terminal_roots_empty():
                return False
            current = owner._terminal_transition
            if current is not transition:
                return False
            owner._terminal_transition = _RelayTerminalTransition(
                phase="scrubbed",
                publish=transition.publish,
                run=transition.run,
                facts_valid=transition.facts_valid,
            )
            transition = owner._terminal_transition
            owner._cleanup_complete = True
    with owner._lock:
        transition = owner._terminal_transition
        if type(transition) is not _RelayTerminalTransition or transition.phase != "scrubbed":
            return False
        if owner._terminal_roots_empty():
            owner._cleanup_complete = True
        if owner._control is not None or not transition.publish:
            owner._terminal_transition = _RelayTerminalTransition(
                phase="scrubbed",
                publish=False,
                run=None,
                facts_valid=True,
            )
            owner._observation = None
            owner._state = "cleaned"
            return _terminal_complete(owner)
        owner._state = "publishing"
    observation = owner._observation or _new_observation(owner._publish_observation)
    if type(observation) is not RelayProbeObservation:
        return False
    with owner._lock:
        transition = owner._terminal_transition
        if (
            type(transition) is _RelayTerminalTransition
            and transition.phase == "published"
            and type(transition.observation) is RelayProbeObservation
        ):
            owner._observation = transition.observation
            owner._state = "observed"
    return _terminal_complete(owner)


def _terminal_facts_valid(owner: RelayProbeOwner) -> bool:
    browser = owner._read("browser_observation", RelayBrowserObservation)
    summary = owner._read("summary", CoturnProbeSummary)
    material = owner._read("tls_material", RuntimeTlsMaterial)
    container = owner._read("container_absence", ContainerAbsenceReceipt)
    network = owner._read("network_absence", NetworkAbsenceReceipt)
    return bool(
        type(browser) is RelayBrowserObservation
        and browser.artifacts_deleted
        and not browser
        and type(summary) is CoturnProbeSummary
        and summary.grammar_verified is False
        and not summary
        and type(material) is RuntimeTlsMaterial
        and material.cleanup_complete
        and type(container) is ContainerAbsenceReceipt
        and container.finalization_complete
        and type(network) is NetworkAbsenceReceipt
        and network.finalization_complete
        and owner._runner_settled
        and type(owner._run) is RelayProbeRun
    )


def _scrub_terminal_roots(owner: RelayProbeOwner) -> None:
    for name, slot in owner._slots.items():
        _terminal_scrub_hook(f"slot:{name}", "before")
        slot.clear()
        _terminal_scrub_hook(f"slot:{name}", "after")
    for name in _TERMINAL_ROOT_NAMES:
        _terminal_scrub_hook(f"root:{name}", "before")
        if name == "_owner_destination":
            destination = owner._owner_destination
            if destination is not None and not destination._clear(owner):
                raise TypeError("Relay probe owner destination cleanup failed")
            owner._owner_destination_cleared = True
            owner._owner_destination = None
            destination = None
        else:
            setattr(owner, name, None)
        _terminal_scrub_hook(f"root:{name}", "after")


def _abandon_terminal_publication(owner: RelayProbeOwner) -> None:
    with owner._lock:
        owner._publish_requested = False
        transition = owner._terminal_transition
        if type(transition) is _RelayTerminalTransition and transition.phase in {
            "preparing",
            "scrubbed",
        }:
            owner._terminal_transition = _RelayTerminalTransition(
                phase=transition.phase,
                publish=False,
                run=None,
                facts_valid=True,
            )


def _terminal_scrub_hook(_name: str, _position: str) -> None:
    """Deterministic phase-cut seam; production behavior is intentionally empty."""


def _terminal_complete(owner: RelayProbeOwner) -> bool:
    with owner._lock:
        transition = owner._terminal_transition
        if not owner._terminal_roots_empty() or not owner._cleanup_complete:
            return False
        if owner._state == "observed":
            return bool(
                type(transition) is _RelayTerminalTransition
                and transition.phase == "published"
                and type(transition.observation) is RelayProbeObservation
                and owner._observation is transition.observation
                and transition.run is None
            )
        if owner._state == "cleaned":
            return bool(
                type(transition) is _RelayTerminalTransition
                and transition.phase == "scrubbed"
                and not transition.publish
                and transition.run is None
                and transition.observation is None
                and owner._observation is None
            )
        return False


__all__: list[str] = []

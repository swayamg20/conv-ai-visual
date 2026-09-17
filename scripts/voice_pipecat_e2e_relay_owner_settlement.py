"""Exact terminal absence proofs for the private outer relay executor."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_owner_state import (
    _REGISTRY,
    _REGISTRY_LOCK,
    RelayProbeOwner,
    RelayProbeOwnerDestination,
    _terminal_owner_valid,
)
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation


def _relay_probe_destination_and_registry_are_empty(destination: object) -> bool:
    if type(destination) is not RelayProbeOwnerDestination:
        return False
    with destination._lock:
        if destination._record is not None:
            return False
        with _REGISTRY_LOCK:
            return not _REGISTRY


def _relay_probe_owner_settlement_matches(
    owner: object,
    destination: object,
    observation: object,
) -> bool:
    """Prove the terminal owner and exact destination/global registry absence."""

    if (
        type(owner) is not RelayProbeOwner
        or type(destination) is not RelayProbeOwnerDestination
        or (observation is not None and type(observation) is not RelayProbeObservation)
        or not _relay_probe_destination_and_registry_are_empty(destination)
    ):
        return False
    with owner._lock:
        state = owner._state
        retained = owner._observation
        return bool(
            _terminal_owner_valid(owner)
            and (
                (state == "observed" and retained is observation)
                if type(observation) is RelayProbeObservation
                else (state == "cleaned" and retained is None)
            )
        )


__all__: list[str] = []

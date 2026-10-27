"""Final command-gated linearization for one executor build consumer."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_absence_reservation_is_active,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _workspace_built_consumption_intent_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_LEASES,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_forget import (
    _RETIRED_RECEIPT_EVIDENCE,
    _RETIREMENT_AUTHORITIES,
    _RETIREMENTS,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _WorkspaceBuildCommandGate,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer import (
    _workspace_worker_consumer_is_active,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _BUILD_RETIREMENTS,
    _RELEASE_BINDINGS,
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _active_evidence_lease_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _executor_source_evidence_graph_matches,
    _RelayLinuxExecutorError,
)

_FAILURE = "Relay Linux executor built consumption is invalid"


def _store_consumed_workspace_built_lease(
    evidence: _RelayLinuxExecutorBuiltEvidence,
    gate: _WorkspaceBuildCommandGate,
    deadline: float,
) -> None:
    """Fence freshness and cancellation immediately before the consume effect."""

    state = _COMMANDS.get(evidence.command)
    lease = _BUILT_LEASES.get(evidence.built)
    if not (
        _active_evidence_lease_matches(evidence, lease)
        and type(state) is tuple
        and len(state) == 6
        and state[0] is evidence.owner_token
        and state[1] is evidence.record_token
        and state[3] == deadline
        and type(state[4]) is str
        and state[4] == "built"
        and _workspace_built_consumption_intent_matches(
            evidence.built,
            evidence.consumer,
            evidence.key,
        )
        and _build_process_absence_reservation_is_active(
            evidence.reservation,
            evidence.key,
        )
        and _workspace_worker_consumer_is_active(
            evidence.bundle,
            evidence.construction,
            evidence.key,
        )
        and _executor_source_evidence_graph_matches(
            evidence.key,
            evidence.executor,
            evidence.destination,
        )
        and not _RETIREMENTS
        and not _RETIREMENT_AUTHORITIES
        and _RETIRED_RECEIPT_EVIDENCE.get(evidence.built) is None
        and not _RELEASE_BINDINGS
        and not _BUILD_RETIREMENTS
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    consumed_lease = (*lease[:5], "consumed")
    now = time.monotonic()
    if (
        type(now) is not float
        or not math.isfinite(now)
        or now >= deadline
        or evidence.bundle._controller._cancellation_requested() is not False
        or gate.cancel_requested is not False
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    _BUILT_LEASES[evidence.built] = consumed_lease


__all__: list[str] = []

"""Exact proof-bound release and revocation acknowledgment for one consumed build."""

from __future__ import annotations

import math

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _acknowledge_workspace_built_consumer_revoked,
    _release_workspace_built_consumer_use,
    _workspace_built_consumer_is_acknowledged,
    _workspace_built_consumer_is_forgotten,
    _workspace_built_consumer_is_in_use,
    _workspace_built_consumer_is_revoked,
    _workspace_built_consumer_is_use_released,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _workspace_built_receipt_is_revoked,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _cleanup_evidence_matches,
    _consumed_lease_matches,
    _evidence_for_binding,
    _outer_phase_matches,
    _revoked_lease_matches,
    _store_outer_phase,
)


def _release_relay_linux_executor_built_use(
    binding: _RelayLinuxExecutorBuiltBinding,
    *,
    cleanup_deadline: float,
) -> bool:
    """Durably end inner use before workspace cancellation may begin."""

    evidence = _evidence_for_binding(binding)
    if (
        evidence is None
        or type(cleanup_deadline) is not float
        or not math.isfinite(cleanup_deadline)
        or not _cleanup_evidence_matches(evidence)
        or not _consumed_lease_matches(evidence)
    ):
        return False
    from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
        _inner_settlement_matches_build,
    )

    if not _inner_settlement_matches_build(evidence):
        return False
    if _workspace_built_consumer_is_in_use(evidence.built, evidence.consumer):
        if not _release_workspace_built_consumer_use(
            evidence.built,
            evidence.consumer,
            cleanup_deadline=cleanup_deadline,
        ):
            return False
    if not _consumer_use_has_ended(evidence):
        return False
    if _outer_phase_matches(evidence, "build-consumed") and not _store_outer_phase(
        evidence,
        "build-consumed",
        "use-release-intended",
    ):
        return False
    return bool(
        _outer_phase_matches(evidence, "use-release-intended")
        or _outer_phase_matches(evidence, "build-revoked-acknowledged")
    )


def _acknowledge_relay_linux_executor_built_revoked(
    binding: _RelayLinuxExecutorBuiltBinding,
    *,
    cleanup_deadline: float,
) -> bool:
    """Acknowledge worker revocation before its filesystem deletion gate."""

    evidence = _evidence_for_binding(binding)
    if (
        evidence is None
        or type(cleanup_deadline) is not float
        or not math.isfinite(cleanup_deadline)
        or not _cleanup_evidence_matches(evidence)
    ):
        return False
    forgotten = _workspace_built_consumer_is_forgotten(evidence.built, evidence.consumer)
    acknowledged = (
        _workspace_built_consumer_is_acknowledged(
            evidence.built,
            evidence.consumer,
        )
        or forgotten
    )
    if not forgotten and not _revoked_lease_matches(evidence):
        return False
    if not acknowledged and not (
        _workspace_built_receipt_is_revoked(
            evidence.built,
            evidence.owner_token,
            evidence.record_token,
        )
        and _workspace_built_consumer_is_revoked(evidence.built, evidence.consumer)
    ):
        return False
    if _outer_phase_matches(evidence, "use-release-intended") and not _store_outer_phase(
        evidence,
        "use-release-intended",
        "build-revoked-acknowledged",
    ):
        return False
    if not acknowledged:
        acknowledged = _acknowledge_workspace_built_consumer_revoked(
            evidence.built,
            evidence.consumer,
            cleanup_deadline=cleanup_deadline,
        )
    return bool(acknowledged and _outer_phase_matches(evidence, "build-revoked-acknowledged"))


def _consumer_use_has_ended(evidence: _RelayLinuxExecutorBuiltEvidence) -> bool:
    return bool(
        _workspace_built_consumer_is_use_released(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_revoked(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_acknowledged(evidence.built, evidence.consumer)
        or _workspace_built_consumer_is_forgotten(evidence.built, evidence.consumer)
    )


__all__: list[str] = []

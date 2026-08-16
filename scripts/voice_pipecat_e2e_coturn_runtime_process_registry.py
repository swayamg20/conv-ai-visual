"""Bounded same-process registry for one attached Coturn run per container."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_docker_container import ContainerCleanupAuthority

_RUN_LOCK = threading.Lock()
_ACTIVE_RUNS: dict[tuple[object, ...], object] = {}
_MAX_ACTIVE_RUNS = 64


def _run_key(authority: ContainerCleanupAuthority) -> tuple[object, ...]:
    plan = authority.plan
    return (
        plan.paths,
        plan.identity.owner_nonce,
        plan.identity.container_name,
        authority.container_id,
    )


def _register_active_run(
    authority: ContainerCleanupAuthority,
    process_identity: object,
) -> bool:
    key = _run_key(authority)
    with _RUN_LOCK:
        current = _ACTIVE_RUNS.get(key)
        if current is not None and current is not process_identity:
            return False
        if current is None and len(_ACTIVE_RUNS) >= _MAX_ACTIVE_RUNS:
            return False
        _ACTIVE_RUNS[key] = process_identity
        return True


def _release_active_run(
    authority: ContainerCleanupAuthority,
    process_identity: object,
) -> None:
    key = _run_key(authority)
    with _RUN_LOCK:
        if _ACTIVE_RUNS.get(key) is process_identity:
            _ACTIVE_RUNS.pop(key, None)


def _active_run_matches(
    authority: ContainerCleanupAuthority,
    process_identity: object,
) -> bool:
    with _RUN_LOCK:
        return _ACTIVE_RUNS.get(_run_key(authority)) is process_identity


def _container_recovery_is_allowed(authority: ContainerCleanupAuthority) -> bool:
    """Refuse same-process recovery while a published child remains live."""

    with _RUN_LOCK:
        return _run_key(authority) not in _ACTIVE_RUNS

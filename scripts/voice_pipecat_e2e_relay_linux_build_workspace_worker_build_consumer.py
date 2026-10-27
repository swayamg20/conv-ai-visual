"""Worker-owned lifetime handshake for one consumed workspace build."""

from __future__ import annotations

import math
import time

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_absence_reservation_is_active,
    _BuildProcessAbsenceReservation,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_contract import (
    _active_consumer_lease_matches,
    _active_lease_shape,
    _active_worker_handoff_matches,
    _canonical_consumer_graph_matches,
    _consumed_lease_matches,
    _consumer_command,
    _consumer_state,
    _consumer_state_matches,
    _consumer_state_shape,
    _revoked_lease_matches,
    _tombstone_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values import (
    _BUILD_CONSUMERS,
    _BUILT_BY_CONSUMER,
    _CONSUMED_HISTORY,
    _CONSUMER_TOMBSTONES,
    _TOKEN,
    _pop_build_consumer,
    _pop_built_by_consumer,
    _pop_consumed_history,
    _pop_consumer_tombstone,
    _store_build_consumer,
    _store_built_by_consumer,
    _store_consumed_history,
    _store_consumer_tombstone,
    _WorkspaceBuiltConsumerToken,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_LEASES,
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _acquire_command_gate,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)


def _intend_workspace_built_consumption(
    *,
    receipt: _WorkspaceBuiltReceipt,
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
    construction: _WorkspaceWorkerThreadReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
    consumer_key: object,
    admission: _BuildProcessAbsenceReservation,
) -> bool:
    """Publish exact intent while the caller holds the command gate."""

    lease = _BUILT_LEASES.get(receipt)
    if not _active_lease_shape(receipt, lease) or not _valid_outer_inputs(
        receipt,
        owner,
        bundle,
        construction,
        consumer,
        consumer_key,
        admission,
    ):
        return False
    state = _consumer_state(
        lease,
        owner,
        bundle,
        construction,
        consumer,
        consumer_key,
        "consume-intended",
    )
    if state is None or not _canonical_consumer_graph_matches(
        receipt,
        state,
        require_consumer_maps=False,
    ):
        return False
    existing = _BUILD_CONSUMERS.get(receipt)
    reverse = _BUILT_BY_CONSUMER.get(consumer)
    if existing is not None or reverse is not None:
        if not (
            (existing is None or _consumer_state_matches(existing, state, "consume-intended"))
            and (reverse is None or reverse is receipt)
            and all(candidate is receipt for candidate in _BUILD_CONSUMERS)
            and all(candidate is consumer for candidate in _BUILT_BY_CONSUMER)
            and not _CONSUMED_HISTORY
        ):
            return False
        if existing is None:
            _store_build_consumer(receipt, state)
        if reverse is None:
            _store_built_by_consumer(consumer, receipt)
        return bool(
            _consumer_state_matches(
                _BUILD_CONSUMERS.get(receipt),
                state,
                "consume-intended",
            )
            and _BUILT_BY_CONSUMER.get(consumer) is receipt
            and len(_BUILD_CONSUMERS) == 1
            and len(_BUILT_BY_CONSUMER) == 1
        )
    if _BUILD_CONSUMERS or _BUILT_BY_CONSUMER or _CONSUMED_HISTORY:
        return False
    _store_build_consumer(receipt, state)
    _store_built_by_consumer(consumer, receipt)
    return bool(
        _BUILD_CONSUMERS.get(receipt) is state and _BUILT_BY_CONSUMER.get(consumer) is receipt
    )


def _activate_workspace_built_consumption(
    receipt: _WorkspaceBuiltReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
) -> bool:
    """Commit timeless in-use state after the consumed lease assignment."""

    state = _BUILD_CONSUMERS.get(receipt)
    if not (
        _consumer_state_shape(state)
        and state[5] is consumer
        and state[13] in {"consume-intended", "in-use"}
        and _consumed_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
        and (_CONSUMED_HISTORY.get(receipt) is None or _CONSUMED_HISTORY.get(receipt) is consumer)
    ):
        return False
    _store_consumed_history(receipt, consumer)
    active = state if state[13] == "in-use" else (*state[:13], "in-use")
    if state is not active:
        _store_build_consumer(receipt, active)
    return bool(
        _consumer_state_matches(_BUILD_CONSUMERS.get(receipt), active, "in-use")
        and _CONSUMED_HISTORY.get(receipt) is consumer
    )


def _workspace_built_consumption_intent_matches(
    receipt: object,
    consumer: object,
    consumer_key: object,
) -> bool:
    """Prove one exact active intent without entering another registry."""

    state = _BUILD_CONSUMERS.get(receipt)
    return bool(
        _consumer_state_matches(state, state, "consume-intended")
        and state[5] is consumer
        and state[6] is consumer_key
        and _active_consumer_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
        and not _CONSUMED_HISTORY
        and not _CONSUMER_TOMBSTONES
    )


def _reject_workspace_built_consumption(
    receipt: _WorkspaceBuiltReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
) -> bool:
    """Retire intent only when the active-to-consumed effect never occurred."""

    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        reverse = _BUILT_BY_CONSUMER.get(consumer)
        if reverse is not None and reverse is not receipt:
            return False
        if reverse is receipt:
            _pop_built_by_consumer(consumer)
        return bool(not _BUILD_CONSUMERS and not _BUILT_BY_CONSUMER and not _CONSUMED_HISTORY)
    lease = _BUILT_LEASES.get(receipt)
    if not (
        _consumer_state_matches(state, state, "consume-intended")
        and state[5] is consumer
        and _active_lease_shape(receipt, lease)
        and _canonical_consumer_graph_matches(
            receipt,
            state,
            require_consumer_maps=False,
        )
        and all(candidate is receipt for candidate in _BUILD_CONSUMERS)
        and all(candidate is consumer for candidate in _BUILT_BY_CONSUMER)
        and (
            _BUILT_BY_CONSUMER.get(consumer) is None or _BUILT_BY_CONSUMER.get(consumer) is receipt
        )
        and receipt not in _CONSUMED_HISTORY
    ):
        return False
    _pop_build_consumer(receipt)
    _pop_built_by_consumer(consumer)
    return bool(
        receipt not in _BUILD_CONSUMERS
        and consumer not in _BUILT_BY_CONSUMER
        and receipt not in _CONSUMED_HISTORY
    )


def _workspace_built_consumer_is_in_use(
    receipt: object,
    consumer: object,
) -> bool:
    state = _BUILD_CONSUMERS.get(receipt)
    return bool(
        _consumer_state_matches(state, state, "in-use")
        and state[5] is consumer
        and _consumed_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
        and _CONSUMED_HISTORY.get(receipt) is consumer
    )


def _workspace_built_consumption_effect_is_reconcilable(
    receipt: object,
    consumer: object,
) -> bool:
    """Recognize the durable consumed cut before outer phase completion."""

    state = _BUILD_CONSUMERS.get(receipt)
    if not (
        _consumer_state_shape(state)
        and state[5] is consumer
        and state[13] in {"consume-intended", "in-use"}
        and _consumed_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    ):
        return False
    history = _CONSUMED_HISTORY.get(receipt)
    return bool(history is consumer or (state[13] == "consume-intended" and history is None))


def _workspace_built_consumption_intent_is_rejectable(
    receipt: object,
    consumer: object,
) -> bool:
    """Recognize an exact no-effect intent that cleanup may retire."""

    lease = _BUILT_LEASES.get(receipt)
    if not _active_lease_shape(receipt, lease) or _CONSUMED_HISTORY or _CONSUMER_TOMBSTONES:
        return False
    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        reverse = _BUILT_BY_CONSUMER.get(consumer)
        return bool(
            not _BUILD_CONSUMERS
            and all(candidate is consumer for candidate in _BUILT_BY_CONSUMER)
            and (reverse is None or reverse is receipt)
        )
    reverse = _BUILT_BY_CONSUMER.get(consumer)
    return bool(
        _consumer_state_matches(state, state, "consume-intended")
        and state[5] is consumer
        and _canonical_consumer_graph_matches(
            receipt,
            state,
            require_consumer_maps=False,
        )
        and all(candidate is receipt for candidate in _BUILD_CONSUMERS)
        and all(candidate is consumer for candidate in _BUILT_BY_CONSUMER)
        and (reverse is None or reverse is receipt)
    )


def _workspace_built_consumer_is_use_released(
    receipt: object,
    consumer: object,
) -> bool:
    state = _BUILD_CONSUMERS.get(receipt)
    return bool(
        _consumer_state_matches(state, state, "use-released")
        and state[5] is consumer
        and _consumed_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    )


def _workspace_built_consumer_is_revoked(
    receipt: object,
    consumer: object,
) -> bool:
    state = _BUILD_CONSUMERS.get(receipt)
    return bool(
        _consumer_state_matches(state, state, "revoked")
        and state[5] is consumer
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    )


def _workspace_built_consumer_is_acknowledged(
    receipt: object,
    consumer: object,
) -> bool:
    state = _BUILD_CONSUMERS.get(receipt)
    return bool(
        _consumer_state_matches(state, state, "acknowledged")
        and state[5] is consumer
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    )


def _workspace_built_consumer_holds_worker(
    receipt: object,
    owner_token: object,
    record_token: object,
    *,
    controller: _WorkspaceWorkerController,
    lock_deadline: float,
) -> bool:
    """Serialize the worker's active-or-consumed hold decision."""

    lease = _BUILT_LEASES.get(receipt)
    if (
        type(receipt) is not _WorkspaceBuiltReceipt
        or type(lease) is not tuple
        or len(lease) != 6
        or lease[0] is not owner_token
        or lease[1] is not record_token
        or type(lease[2]) is not _WorkspaceBuildCommand
        or type(controller) is not _WorkspaceWorkerController
    ):
        return False
    gate = _acquire_command_gate(lease[2], lock_deadline)
    try:
        current = _BUILT_LEASES.get(receipt)
        if _active_lease_shape(receipt, current):
            command_state = _COMMANDS.get(current[2])
            stable = _active_worker_handoff_matches(
                receipt,
                current,
                owner_token,
                record_token,
                controller,
            )
            now = time.monotonic()
            return bool(
                stable
                and type(command_state) is tuple
                and len(command_state) == 6
                and type(command_state[3]) is float
                and math.isfinite(command_state[3])
                and type(now) is float
                and math.isfinite(now)
                and now < command_state[3]
                and controller._cancellation_requested() is False
                and gate.cancel_requested is False
            )
        state = _BUILD_CONSUMERS.get(receipt)
        return bool(
            _consumer_state_matches(state, state, "in-use")
            and _consumed_lease_matches(receipt, state)
            and _canonical_consumer_graph_matches(receipt, state)
        )
    finally:
        gate.lock.release()


def _release_workspace_built_consumer_use(
    receipt: _WorkspaceBuiltReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
    *,
    cleanup_deadline: float,
) -> bool:
    """End outer use before workspace cancellation is allowed to cascade."""

    command = _consumer_command(receipt, consumer)
    if command is None:
        return False
    gate = _acquire_command_gate(command, cleanup_deadline)
    try:
        state = _BUILD_CONSUMERS.get(receipt)
        if _consumer_state_matches(state, state, "use-released"):
            return state[5] is consumer and _canonical_consumer_graph_matches(receipt, state)
        if not (
            _consumer_state_matches(state, state, "in-use")
            and state[5] is consumer
            and _consumed_lease_matches(receipt, state)
            and _canonical_consumer_graph_matches(receipt, state)
        ):
            return False
        released = (*state[:13], "use-released")
        _store_build_consumer(receipt, released)
        return _consumer_state_matches(
            _BUILD_CONSUMERS.get(receipt),
            released,
            "use-released",
        )
    finally:
        gate.lock.release()


def _workspace_built_consumer_allows_revocation(receipt: object) -> bool:
    """Reject worker revocation until an exact consumer releases use."""

    lease = _BUILT_LEASES.get(receipt)
    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        return bool(
            type(lease) is tuple
            and len(lease) == 6
            and type(lease[5]) is str
            and lease[5] in {"pending", "active", "revoked"}
            and not _BUILD_CONSUMERS
            and not _BUILT_BY_CONSUMER
            and not _CONSUMED_HISTORY
            and not _CONSUMER_TOMBSTONES
        )
    if _consumer_state_matches(state, state, "use-released"):
        lease_matches = _consumed_lease_matches(receipt, state) or _revoked_lease_matches(
            receipt,
            state,
        )
    elif any(_consumer_state_matches(state, state, phase) for phase in {"revoked", "acknowledged"}):
        lease_matches = _revoked_lease_matches(receipt, state)
    else:
        return False
    return bool(lease_matches and _canonical_consumer_graph_matches(receipt, state))


def _record_workspace_built_consumer_revoked(receipt: object) -> bool:
    """Record exact built revocation before outer acknowledgement."""

    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        return _workspace_built_consumer_all_state_is_empty()
    if any(_consumer_state_matches(state, state, phase) for phase in {"revoked", "acknowledged"}):
        return _revoked_lease_matches(receipt, state)
    if not (
        _consumer_state_matches(state, state, "use-released")
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    ):
        return False
    revoked = (*state[:13], "revoked")
    _store_build_consumer(receipt, revoked)
    return _consumer_state_matches(_BUILD_CONSUMERS.get(receipt), revoked, "revoked")


def _acknowledge_workspace_built_consumer_revoked(
    receipt: _WorkspaceBuiltReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
    *,
    cleanup_deadline: float,
) -> bool:
    """Allow worker filesystem cleanup only after outer revocation is durable."""

    command = _consumer_command(receipt, consumer)
    if command is None:
        return False
    gate = _acquire_command_gate(command, cleanup_deadline)
    try:
        state = _BUILD_CONSUMERS.get(receipt)
        if _consumer_state_matches(state, state, "acknowledged"):
            return state[5] is consumer and _revoked_lease_matches(receipt, state)
        if not (
            _consumer_state_matches(state, state, "revoked")
            and state[5] is consumer
            and _revoked_lease_matches(receipt, state)
            and _canonical_consumer_graph_matches(receipt, state)
        ):
            return False
        acknowledged = (*state[:13], "acknowledged")
        _store_build_consumer(receipt, acknowledged)
        return _consumer_state_matches(
            _BUILD_CONSUMERS.get(receipt),
            acknowledged,
            "acknowledged",
        )
    finally:
        gate.lock.release()


def _workspace_built_consumer_cleanup_is_acknowledged(receipt: object) -> bool:
    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        return _workspace_built_consumer_all_state_is_empty()
    return bool(
        _consumer_state_matches(state, state, "acknowledged")
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    )


def _forget_acknowledged_workspace_built_consumer(receipt: object) -> bool:
    """Retire exact acknowledged state before built-map removal."""

    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        resolved = _forgotten_consumer(receipt)
        if resolved is None:
            return _workspace_built_consumer_all_state_is_empty()
        if _BUILT_BY_CONSUMER.get(resolved) is receipt:
            _pop_built_by_consumer(resolved)
        if _CONSUMED_HISTORY.get(receipt) is resolved:
            _pop_consumed_history(receipt)
        return bool(
            not _BUILD_CONSUMERS
            and not _BUILT_BY_CONSUMER
            and not _CONSUMED_HISTORY
            and _workspace_built_consumer_is_forgotten(receipt, resolved)
        )
    if not (
        _consumer_state_matches(state, state, "acknowledged")
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    ):
        return False
    token = state[5]
    tombstone = (receipt, state[2], state[3], "forgotten")
    _store_consumer_tombstone(token, tombstone)
    if not _tombstone_matches(_CONSUMER_TOMBSTONES.get(token), tombstone):
        return False
    _pop_build_consumer(receipt)
    _pop_built_by_consumer(token)
    _pop_consumed_history(receipt)
    return bool(
        receipt not in _BUILD_CONSUMERS
        and token not in _BUILT_BY_CONSUMER
        and receipt not in _CONSUMED_HISTORY
        and _tombstone_matches(_CONSUMER_TOMBSTONES.get(token), tombstone)
    )


def _workspace_built_consumer_is_forgotten(
    receipt: object,
    consumer: object,
) -> bool:
    tombstone = _CONSUMER_TOMBSTONES.get(consumer)
    return bool(
        _consumer_tombstone_matches(receipt, consumer, tombstone)
        and len(_CONSUMER_TOMBSTONES) == 1
        and next(iter(_CONSUMER_TOMBSTONES)) is consumer
        and receipt not in _BUILD_CONSUMERS
        and consumer not in _BUILT_BY_CONSUMER
        and receipt not in _CONSUMED_HISTORY
    )


def _workspace_built_consumer_retirement_authority(
    receipt: object,
) -> tuple[bool, _WorkspaceBuiltConsumerToken | None]:
    """Resolve exact no-consumer or acknowledged/forgotten retirement authority."""

    state = _BUILD_CONSUMERS.get(receipt)
    if state is None:
        consumer = _forgotten_consumer(receipt)
        if consumer is not None:
            return _workspace_built_consumer_is_forgotten(receipt, consumer), consumer
        return _workspace_built_consumer_all_state_is_empty(), None
    if not (
        _consumer_state_matches(state, state, "acknowledged")
        and _revoked_lease_matches(receipt, state)
        and _canonical_consumer_graph_matches(receipt, state)
    ):
        return False, None
    return True, state[5]


def _workspace_built_consumer_registries_are_empty() -> bool:
    if _BUILD_CONSUMERS or _BUILT_BY_CONSUMER or _CONSUMED_HISTORY:
        return False
    if not _CONSUMER_TOMBSTONES:
        return True
    if len(_CONSUMER_TOMBSTONES) != 1:
        return False
    consumer, tombstone = next(iter(_CONSUMER_TOMBSTONES.items()))
    return bool(
        _consumer_tombstone_matches(tombstone[0], consumer, tombstone)
        and tombstone[0] not in _BUILT_LEASES
    )


def _workspace_built_consumer_all_state_is_empty() -> bool:
    return bool(
        not _BUILD_CONSUMERS
        and not _BUILT_BY_CONSUMER
        and not _CONSUMED_HISTORY
        and not _CONSUMER_TOMBSTONES
    )


def _retire_workspace_built_consumer_tombstone(
    receipt: object,
    consumer: object,
) -> bool:
    if not _workspace_built_consumer_is_forgotten(receipt, consumer):
        return not _CONSUMER_TOMBSTONES
    _pop_consumer_tombstone(consumer)
    return not _CONSUMER_TOMBSTONES


def _forgotten_consumer(receipt: object) -> _WorkspaceBuiltConsumerToken | None:
    matches = [
        consumer
        for consumer, tombstone in _CONSUMER_TOMBSTONES.items()
        if _consumer_tombstone_matches(receipt, consumer, tombstone)
    ]
    return matches[0] if len(matches) == 1 else None


def _consumer_tombstone_matches(
    receipt: object,
    consumer: object,
    tombstone: object,
) -> bool:
    return bool(
        type(receipt) is _WorkspaceBuiltReceipt
        and type(consumer) is _WorkspaceBuiltConsumerToken
        and consumer._authentic is _TOKEN
        and consumer._receipt is receipt
        and type(consumer._command) is _WorkspaceBuildCommand
        and type(consumer._digest) is bytes
        and len(consumer._digest) == 32
        and type(tombstone) is tuple
        and len(tombstone) == 4
        and tombstone[0] is receipt
        and tombstone[1] is consumer._command
        and type(tombstone[2]) is bytes
        and tombstone[2] == consumer._digest
        and type(tombstone[3]) is str
        and tombstone[3] == "forgotten"
    )


def _valid_outer_inputs(
    receipt: object,
    owner: object,
    bundle: object,
    construction: object,
    consumer: object,
    consumer_key: object,
    admission: object,
) -> bool:
    return bool(
        type(receipt) is _WorkspaceBuiltReceipt
        and type(owner) is _RelayLinuxBuildWorkspaceOwner
        and type(bundle) is _WorkspaceWorkerBundle
        and type(construction) is _WorkspaceWorkerThreadReceipt
        and type(consumer) is _WorkspaceBuiltConsumerToken
        and consumer._authentic is _TOKEN
        and consumer_key is not None
        and _build_process_absence_reservation_is_active(admission, consumer_key)
    )


__all__: list[str] = []

"""Identity-checked retirement of one path-free workspace build graph."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_build_process_facade_registry import (
    _build_process_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
    _workspace_built_consumer_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _forget_workspace_build_process,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _BUILT_BY_COMMAND,
    _BUILT_LEASES,
    _forget_workspace_built_receipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt_forget import (
    _RETIREMENT_AUTHORITIES,
    _RETIREMENTS,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMAND_CONTROLLERS,
    _COMMAND_GATES,
    _COMMANDS,
    _CONTROLLER_COMMANDS,
    _PROCESS_ASSOCIATIONS,
    _forget_workspace_build_command,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_build_claim_cleanup import (
    _forget_workspace_prepared_lease,
    _workspace_revoked_prepared_lease_can_retire,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _LEASES,
    _PREPARED_BUILDS,
    _forget_workspace_prepared_build,
    _workspace_filesystem_record_is_settled,
    _workspace_prepared_receipt_is_revoked,
)

_FORGOTTEN_RECORDS: dict[
    object,
    tuple[object, object | None, object, bytes, str],
] = {}


def _forget_workspace_build_state(
    command: object | None,
    *,
    prepared: object,
    owner_token: object,
    record_token: object,
) -> bool:
    """Forget only after process absence, lease revocation, and FS settlement."""

    marker = _FORGOTTEN_RECORDS.get(record_token)
    if marker is not None:
        return _resume_workspace_build_forget(
            marker,
            command=command,
            prepared=prepared,
            owner_token=owner_token,
            record_token=record_token,
        )
    if command is None:
        if prepared is None:
            return bool(not _FORGOTTEN_RECORDS and _workspace_build_graph_is_empty())
        if not _workspace_filesystem_record_is_settled(record_token):
            return False
        if not _workspace_build_graph_has_only_prepared(
            prepared,
            owner_token,
            record_token,
        ):
            return False
        fingerprint = _LEASES[prepared][2]
        marker = (owner_token, None, prepared, fingerprint, "no-command")
        _store_forgotten_workspace_build(record_token, marker)
        return _resume_workspace_build_forget(
            marker,
            command=None,
            prepared=prepared,
            owner_token=owner_token,
            record_token=record_token,
        )
    state = _COMMANDS.get(command)
    if (
        type(command) is not _WorkspaceBuildCommand
        or type(state) is not tuple
        or len(state) != 6
        or state[0] is not owner_token
        or state[1] is not record_token
        or state[2] is not prepared
        or not _workspace_filesystem_record_is_settled(record_token)
    ):
        return False
    prepared = state[2]
    if not _forget_workspace_built_receipt(command):
        return False
    if not _forget_workspace_build_process(command):
        return False
    if not _workspace_prepared_receipt_is_revoked(
        prepared,
        owner_token,
        record_token,
    ):
        return False
    if not _forget_workspace_prepared_build(prepared, command):
        return False
    if not _workspace_revoked_prepared_lease_can_retire(
        prepared,
        owner_token,
        record_token,
    ):
        return False
    fingerprint = _LEASES[prepared][2]
    marker = (owner_token, command, prepared, fingerprint, "command")
    _store_forgotten_workspace_build(
        record_token,
        marker,
    )
    return _resume_workspace_build_forget(
        marker,
        command=command,
        prepared=prepared,
        owner_token=owner_token,
        record_token=record_token,
    )


def _store_forgotten_workspace_build(
    record_token: object,
    value: tuple[object, object | None, object, bytes, str],
) -> None:
    """Deterministic tombstone-store cut for release return-loss tests."""

    existing = _FORGOTTEN_RECORDS.setdefault(record_token, value)
    if existing != value:
        raise TypeError("Relay Linux workspace build forget state is invalid")


def _resume_workspace_build_forget(
    marker: object,
    *,
    command: object | None,
    prepared: object,
    owner_token: object,
    record_token: object,
) -> bool:
    if (
        type(marker) is not tuple
        or len(marker) != 5
        or marker[0] is not owner_token
        or marker[2] is not prepared
        or type(marker[3]) is not bytes
        or len(marker[3]) != 32
        or marker[4] not in {"command", "no-command"}
        or (marker[4] == "no-command" and (marker[1] is not None or command is not None))
        or (
            marker[4] == "command"
            and (
                type(marker[1]) is not _WorkspaceBuildCommand
                or (command is not None and command is not marker[1])
            )
        )
    ):
        return False
    if not _forget_workspace_prepared_lease(
        prepared,
        owner_token,
        record_token,
        retirement_committed=True,
        retirement_fingerprint=marker[3],
    ):
        return False
    target = marker[1] if marker[4] == "command" else None
    if target is not None and target in _COMMANDS:
        if not _forget_workspace_build_command(target, record_token):
            return False
    return _workspace_build_graph_is_empty()


def _complete_workspace_build_state_forget(
    command: object | None,
    *,
    prepared: object,
    owner_token: object,
    record_token: object,
) -> bool:
    marker = _FORGOTTEN_RECORDS.get(record_token)
    if marker is None:
        return bool(
            command is None
            and prepared is None
            and not _FORGOTTEN_RECORDS
            and _workspace_build_graph_is_empty()
        )
    if (
        type(marker) is not tuple
        or len(marker) != 5
        or marker[0] is not owner_token
        or marker[2] is not prepared
        or type(marker[3]) is not bytes
        or len(marker[3]) != 32
        or marker[4] not in {"command", "no-command"}
        or (command is not None and marker[1] is not command)
        or (marker[4] == "no-command" and marker[1] is not None)
        or not _workspace_build_graph_is_empty()
    ):
        return False
    _FORGOTTEN_RECORDS.pop(record_token, None)
    return record_token not in _FORGOTTEN_RECORDS


def _workspace_build_graph_is_empty() -> bool:
    return bool(
        not _COMMANDS
        and not _PROCESS_ASSOCIATIONS
        and not _COMMAND_GATES
        and not _COMMAND_CONTROLLERS
        and not _CONTROLLER_COMMANDS
        and not _BUILT_LEASES
        and not _BUILT_BY_COMMAND
        and not _RETIREMENTS
        and not _RETIREMENT_AUTHORITIES
        and _workspace_built_consumer_registries_are_empty()
        and not _PREPARED_BUILDS
        and not _LEASES
        and _build_process_registries_are_empty()
    )


def _workspace_build_graph_has_only_prepared(
    prepared: object,
    owner_token: object,
    record_token: object,
) -> bool:
    return bool(
        not _FORGOTTEN_RECORDS
        and not _COMMANDS
        and not _PROCESS_ASSOCIATIONS
        and not _COMMAND_GATES
        and not _COMMAND_CONTROLLERS
        and not _CONTROLLER_COMMANDS
        and not _BUILT_LEASES
        and not _BUILT_BY_COMMAND
        and not _RETIREMENTS
        and not _RETIREMENT_AUTHORITIES
        and _workspace_built_consumer_registries_are_empty()
        and not _PREPARED_BUILDS
        and _workspace_revoked_prepared_lease_can_retire(
            prepared,
            owner_token,
            record_token,
        )
        and _build_process_registries_are_empty()
    )


__all__: list[str] = []

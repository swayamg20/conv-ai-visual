"""Identity-checked retirement of one path-free workspace build graph."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _forget_workspace_build_process,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _forget_workspace_built_receipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _forget_workspace_build_command,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _forget_workspace_prepared_build,
    _workspace_filesystem_record_is_settled,
    _workspace_prepared_receipt_is_revoked,
)

_FORGOTTEN_RECORDS: dict[
    object,
    tuple[object, weakref.ReferenceType[_WorkspaceBuildCommand]],
] = {}


def _forget_workspace_build_state(
    command: object | None,
    *,
    owner_token: object,
    record_token: object,
) -> bool:
    """Forget only after process absence, lease revocation, and FS settlement."""

    if command is None:
        marker = _FORGOTTEN_RECORDS.get(record_token)
        return marker is None or marker[0] is owner_token
    state = _COMMANDS.get(command)
    marker = _FORGOTTEN_RECORDS.get(record_token)
    if (
        type(marker) is tuple
        and len(marker) == 2
        and marker[0] is owner_token
        and marker[1]() is command
    ):
        if state is not None and not _forget_workspace_build_command(
            command,
            record_token,
        ):
            return False
        return command not in _COMMANDS
    if (
        type(command) is not _WorkspaceBuildCommand
        or type(state) is not tuple
        or len(state) != 6
        or state[0] is not owner_token
        or state[1] is not record_token
        or not _workspace_filesystem_record_is_settled(record_token)
    ):
        return False
    prepared = state[2]
    if not _forget_workspace_build_process(command):
        return False
    if not _forget_workspace_built_receipt(command):
        return False
    if not _workspace_prepared_receipt_is_revoked(
        prepared,
        owner_token,
        record_token,
    ):
        return False
    if not _forget_workspace_prepared_build(prepared, command):
        return False
    _store_forgotten_workspace_build(
        record_token,
        (owner_token, weakref.ref(command)),
    )
    return bool(
        _FORGOTTEN_RECORDS.get(record_token) == (owner_token, weakref.ref(command))
        and _forget_workspace_build_command(command, record_token)
    )


def _store_forgotten_workspace_build(
    record_token: object,
    value: tuple[object, weakref.ReferenceType[_WorkspaceBuildCommand]],
) -> None:
    """Deterministic tombstone-store cut for release return-loss tests."""

    existing = _FORGOTTEN_RECORDS.setdefault(record_token, value)
    if existing != value:
        raise TypeError("Relay Linux workspace build forget state is invalid")


def _complete_workspace_build_state_forget(
    command: object | None,
    *,
    owner_token: object,
    record_token: object,
) -> bool:
    marker = _FORGOTTEN_RECORDS.get(record_token)
    if marker is None:
        return command is None
    if (
        type(marker) is not tuple
        or len(marker) != 2
        or marker[0] is not owner_token
        or (command is not None and marker[1]() is not command)
    ):
        return False
    _FORGOTTEN_RECORDS.pop(record_token, None)
    return record_token not in _FORGOTTEN_RECORDS


__all__: list[str] = []

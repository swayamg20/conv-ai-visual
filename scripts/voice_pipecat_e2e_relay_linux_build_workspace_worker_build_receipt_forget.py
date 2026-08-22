"""Return-loss-safe retirement of one revoked workspace built lease."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer_values import (
    _WorkspaceBuiltConsumerToken,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _WorkspaceBuildCommand,
)

_RETIREMENTS: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    tuple[object, bytes, object, object | None, object],
] = weakref.WeakKeyDictionary()
_RETIREMENT_AUTHORITIES: weakref.WeakKeyDictionary[
    _WorkspaceBuildCommand,
    object,
] = weakref.WeakKeyDictionary()
_RETIRED_RECEIPT_EVIDENCE: weakref.WeakKeyDictionary[
    _WorkspaceBuiltReceipt,
    object,
] = weakref.WeakKeyDictionary()
_RETIREMENT_TOKEN = object()


class _WorkspaceBuiltRetiredEvidence:
    __slots__ = ("command", "digest", "process_receipt")

    def __init__(
        self,
        token: object,
        command: _WorkspaceBuildCommand,
        digest: bytes,
        process_receipt: _RelayLinuxBuildProcessReceipt,
    ) -> None:
        if token is not _RETIREMENT_TOKEN:
            raise TypeError("Relay Linux workspace built retirement is invalid")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "process_receipt", process_receipt)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("workspace built retired evidence is immutable")


class _WorkspaceBuiltRetirementAuthority:
    """Immutable duplicate authority surviving either built-map pop."""

    __slots__ = ("command", "consumer", "digest", "evidence", "process_receipt", "receipt")

    def __init__(
        self,
        token: object,
        *,
        command: _WorkspaceBuildCommand,
        receipt: _WorkspaceBuiltReceipt,
        digest: bytes,
        process_receipt: _RelayLinuxBuildProcessReceipt,
        consumer: _WorkspaceBuiltConsumerToken | None,
        evidence: object | None = None,
    ) -> None:
        if token is not _RETIREMENT_TOKEN:
            raise TypeError("Relay Linux workspace built retirement is invalid")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "process_receipt", process_receipt)
        object.__setattr__(self, "consumer", consumer)
        object.__setattr__(self, "evidence", evidence)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("workspace built retirement authority is immutable")


def _forget_workspace_built_receipt_state(command: _WorkspaceBuildCommand) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_consumer import (
        _forget_acknowledged_workspace_built_consumer,
        _workspace_built_consumer_registries_are_empty,
        _workspace_built_consumer_retirement_authority,
    )
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
        _BUILT_BY_COMMAND,
        _BUILT_LEASES,
        _WorkspaceBuiltReceipt,
    )

    authority = _RETIREMENT_AUTHORITIES.get(command)
    marker = _RETIREMENTS.get(command)
    receipt = _BUILT_BY_COMMAND.get(command)
    if marker is not None:
        if not _retirement_shape(marker, command, authority):
            return False
        if receipt is not None and receipt is not marker[0]:
            return False
        receipt = marker[0]
    if receipt is None:
        candidates = [
            candidate
            for candidate, state in _BUILT_LEASES.items()
            if type(state) is tuple and len(state) == 6 and state[2] is command
        ]
        if len(candidates) > 1:
            return False
        receipt = candidates[0] if candidates else None
    if receipt is None:
        if authority is not None:
            valid, consumer = _workspace_built_consumer_retirement_authority(authority.receipt)
            if not _authority_matches(authority, command, valid, consumer):
                return False
            _RETIREMENT_AUTHORITIES.pop(command, None)
            return command not in _RETIREMENT_AUTHORITIES
        return bool(
            command not in _RETIREMENTS
            and command not in _RETIREMENT_AUTHORITIES
            and command not in _BUILT_BY_COMMAND
            and not _BUILT_LEASES
            and _workspace_built_consumer_registries_are_empty()
        )
    state = _BUILT_LEASES.get(receipt)
    if marker is None:
        if not (
            type(receipt) is _WorkspaceBuiltReceipt
            and type(state) is tuple
            and len(state) == 6
            and state[2] is command
            and type(state[3]) is bytes
            and len(state[3]) == 32
            and type(state[5]) is str
            and state[5] == "revoked"
        ):
            return False
        valid_authority, consumer = _workspace_built_consumer_retirement_authority(receipt)
        if not valid_authority:
            return False
        retired = _RETIRED_RECEIPT_EVIDENCE.get(receipt)
        if retired is None:
            retired = _WorkspaceBuiltRetiredEvidence(
                _RETIREMENT_TOKEN,
                command,
                state[3],
                state[4],
            )
            _RETIRED_RECEIPT_EVIDENCE[receipt] = retired
        if not _retired_evidence_matches(retired, command, state[3], state[4]):
            return False
        if authority is None:
            authority = _WorkspaceBuiltRetirementAuthority(
                _RETIREMENT_TOKEN,
                command=command,
                receipt=receipt,
                digest=state[3],
                process_receipt=state[4],
                consumer=consumer,
                evidence=retired,
            )
            _RETIREMENT_AUTHORITIES[command] = authority
        if not (
            _authority_matches(authority, command, valid_authority, consumer)
            and authority.receipt is receipt
            and authority.digest == state[3]
            and authority.process_receipt is state[4]
        ):
            return False
        marker = (receipt, state[3], state[4], consumer, authority)
        _RETIREMENTS[command] = marker
        if not _retirement_shape(_RETIREMENTS.get(command), command, authority):
            return False
    elif state is not None:
        if not _state_matches_marker(state, command, marker):
            return False
        valid_authority, consumer = _workspace_built_consumer_retirement_authority(receipt)
        if not _marker_authority_matches(marker, command, valid_authority, consumer):
            return False
    else:
        valid_authority, consumer = _workspace_built_consumer_retirement_authority(receipt)
        if not _marker_authority_matches(marker, command, valid_authority, consumer):
            return False
    if not _forget_acknowledged_workspace_built_consumer(receipt):
        return False
    if _BUILT_BY_COMMAND.get(command) is receipt:
        _BUILT_BY_COMMAND.pop(command, None)
    if _BUILT_LEASES.get(receipt) is state or state is None:
        _BUILT_LEASES.pop(receipt, None)
    if command in _BUILT_BY_COMMAND or receipt in _BUILT_LEASES:
        return False
    _RETIREMENTS.pop(command, None)
    _RETIREMENT_AUTHORITIES.pop(command, None)
    return bool(
        command not in _RETIREMENTS
        and command not in _RETIREMENT_AUTHORITIES
        and command not in _BUILT_BY_COMMAND
        and receipt not in _BUILT_LEASES
    )


def _retirement_shape(marker: object, command: object, authority: object) -> bool:
    return bool(
        type(command) is _WorkspaceBuildCommand
        and type(marker) is tuple
        and len(marker) == 5
        and type(marker[0]) is _WorkspaceBuiltReceipt
        and type(marker[1]) is bytes
        and len(marker[1]) == 32
        and type(marker[2]) is _RelayLinuxBuildProcessReceipt
        and (marker[3] is None or type(marker[3]) is _WorkspaceBuiltConsumerToken)
        and type(marker[4]) is _WorkspaceBuiltRetirementAuthority
        and marker[4] is authority
        and marker[4].command is command
        and marker[4].receipt is marker[0]
        and marker[4].digest is marker[1]
        and marker[4].process_receipt is marker[2]
        and marker[4].consumer is marker[3]
    )


def _state_matches_marker(
    state: object,
    command: _WorkspaceBuildCommand,
    marker: tuple[object, bytes, object, object | None, object],
) -> bool:
    return bool(
        type(state) is tuple
        and len(state) == 6
        and state[2] is command
        and type(state[3]) is bytes
        and state[3] == marker[1]
        and state[4] is marker[2]
        and type(state[5]) is str
        and state[5] == "revoked"
    )


def _marker_authority_matches(
    marker: tuple[object, bytes, object, object | None, object],
    command: _WorkspaceBuildCommand,
    valid: bool,
    consumer: _WorkspaceBuiltConsumerToken | None,
) -> bool:
    return _authority_matches(marker[4], command, valid, consumer)


def _authority_matches(
    authority: object,
    command: _WorkspaceBuildCommand,
    valid: bool,
    consumer: _WorkspaceBuiltConsumerToken | None,
) -> bool:
    return bool(
        valid
        and type(authority) is _WorkspaceBuiltRetirementAuthority
        and authority.command is command
        and type(authority.receipt) is _WorkspaceBuiltReceipt
        and type(authority.digest) is bytes
        and len(authority.digest) == 32
        and type(authority.process_receipt) is _RelayLinuxBuildProcessReceipt
        and authority.consumer is consumer
        and _RETIRED_RECEIPT_EVIDENCE.get(authority.receipt) is authority.evidence
        and _retired_evidence_matches(
            authority.evidence,
            command,
            authority.digest,
            authority.process_receipt,
        )
        and (
            consumer is None
            or (
                consumer._command is command
                and consumer._digest == authority.digest
                and consumer._process_receipt is authority.process_receipt
            )
        )
    )


def _retired_evidence_matches(
    evidence: object,
    command: _WorkspaceBuildCommand,
    digest: object,
    process_receipt: object,
) -> bool:
    return bool(
        type(evidence) is _WorkspaceBuiltRetiredEvidence
        and evidence.command is command
        and type(evidence.digest) is bytes
        and evidence.digest == digest
        and evidence.process_receipt is process_receipt
    )


__all__: list[str] = []

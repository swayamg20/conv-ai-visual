"""Path-free values and canonical maps for one consumed workspace build."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_process_state import (
    _RelayLinuxBuildProcessReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
    _RelayLinuxBuildWorkspaceRequest,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_receipt import (
    _WorkspaceBuiltReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_TOKEN = object()
_FAILURE = "Relay Linux workspace built consumer is invalid"


class _WorkspaceBuiltConsumerToken:
    """Opaque factory-owned token binding one outer user to one built lease."""

    __slots__ = (
        "__weakref__",
        "_authentic",
        "_bundle",
        "_command",
        "_construction",
        "_consumer_key",
        "_controller",
        "_digest",
        "_owner",
        "_owner_token",
        "_process_receipt",
        "_receipt",
        "_record_token",
        "_request",
    )

    def __init__(
        self,
        token: object,
        *,
        receipt: _WorkspaceBuiltReceipt,
        command: _WorkspaceBuildCommand,
        digest: bytes,
        process_receipt: _RelayLinuxBuildProcessReceipt,
        consumer_key: object,
        owner_token: object,
        record_token: object,
        request: _RelayLinuxBuildWorkspaceRequest,
        controller: _WorkspaceWorkerController,
        owner: _RelayLinuxBuildWorkspaceOwner,
        bundle: _WorkspaceWorkerBundle,
        construction: _WorkspaceWorkerThreadReceipt,
    ) -> None:
        if (
            token is not _TOKEN
            or type(receipt) is not _WorkspaceBuiltReceipt
            or type(command) is not _WorkspaceBuildCommand
            or type(digest) is not bytes
            or len(digest) != 32
            or type(process_receipt) is not _RelayLinuxBuildProcessReceipt
            or consumer_key is None
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(request) is not _RelayLinuxBuildWorkspaceRequest
            or type(controller) is not _WorkspaceWorkerController
            or type(owner) is not _RelayLinuxBuildWorkspaceOwner
            or type(bundle) is not _WorkspaceWorkerBundle
            or type(construction) is not _WorkspaceWorkerThreadReceipt
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_authentic", _TOKEN)
        object.__setattr__(self, "_receipt", receipt)
        object.__setattr__(self, "_command", command)
        object.__setattr__(self, "_digest", digest)
        object.__setattr__(self, "_process_receipt", process_receipt)
        object.__setattr__(self, "_consumer_key", consumer_key)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_controller", controller)
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_bundle", bundle)
        object.__setattr__(self, "_construction", construction)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceBuiltConsumerToken()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("workspace built consumer token is immutable")

    def __copy__(self) -> None:
        raise TypeError("workspace built consumer token cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("workspace built consumer token cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("workspace built consumer token cannot be serialized")


_WorkspaceBuiltConsumerState = tuple[
    object,
    object,
    _WorkspaceBuildCommand,
    bytes,
    _RelayLinuxBuildProcessReceipt,
    _WorkspaceBuiltConsumerToken,
    object,
    _RelayLinuxBuildWorkspaceRequest,
    bytes,
    _WorkspaceWorkerController,
    _RelayLinuxBuildWorkspaceOwner,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerThreadReceipt,
    str,
]

_BUILD_CONSUMERS: weakref.WeakKeyDictionary[
    _WorkspaceBuiltReceipt,
    _WorkspaceBuiltConsumerState,
] = weakref.WeakKeyDictionary()
_BUILT_BY_CONSUMER: weakref.WeakKeyDictionary[
    _WorkspaceBuiltConsumerToken,
    _WorkspaceBuiltReceipt,
] = weakref.WeakKeyDictionary()
_CONSUMED_HISTORY: weakref.WeakKeyDictionary[
    _WorkspaceBuiltReceipt,
    _WorkspaceBuiltConsumerToken,
] = weakref.WeakKeyDictionary()
_CONSUMER_TOMBSTONES: weakref.WeakKeyDictionary[
    _WorkspaceBuiltConsumerToken,
    tuple[_WorkspaceBuiltReceipt, _WorkspaceBuildCommand, bytes, str],
] = weakref.WeakKeyDictionary()


def _new_workspace_built_consumer_token(**values: object) -> _WorkspaceBuiltConsumerToken:
    return _WorkspaceBuiltConsumerToken(_TOKEN, **values)  # type: ignore[arg-type]


def _store_build_consumer(
    receipt: _WorkspaceBuiltReceipt,
    state: _WorkspaceBuiltConsumerState,
) -> None:
    _BUILD_CONSUMERS[receipt] = state


def _store_built_by_consumer(
    consumer: _WorkspaceBuiltConsumerToken,
    receipt: _WorkspaceBuiltReceipt,
) -> None:
    _BUILT_BY_CONSUMER[consumer] = receipt


def _store_consumed_history(
    receipt: _WorkspaceBuiltReceipt,
    consumer: _WorkspaceBuiltConsumerToken,
) -> None:
    _CONSUMED_HISTORY[receipt] = consumer


def _store_consumer_tombstone(
    consumer: _WorkspaceBuiltConsumerToken,
    tombstone: tuple[_WorkspaceBuiltReceipt, _WorkspaceBuildCommand, bytes, str],
) -> None:
    _CONSUMER_TOMBSTONES[consumer] = tombstone


def _pop_build_consumer(receipt: _WorkspaceBuiltReceipt) -> None:
    _BUILD_CONSUMERS.pop(receipt, None)


def _pop_built_by_consumer(consumer: _WorkspaceBuiltConsumerToken) -> None:
    _BUILT_BY_CONSUMER.pop(consumer, None)


def _pop_consumed_history(receipt: _WorkspaceBuiltReceipt) -> None:
    _CONSUMED_HISTORY.pop(receipt, None)


def _pop_consumer_tombstone(consumer: _WorkspaceBuiltConsumerToken) -> None:
    _CONSUMER_TOMBSTONES.pop(consumer, None)


__all__: list[str] = []

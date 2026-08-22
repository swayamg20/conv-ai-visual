"""Opaque caller binding and private evidence for one consumed build."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceRequest,
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
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)

_BINDING_TOKEN = object()
_EVIDENCE_TOKEN = object()
_FAILURE = "Relay Linux executor built consumption is invalid"


class _RelayLinuxExecutorBuiltBinding:
    """Path-free and digest-free caller receipt for one exact outer use."""

    __slots__ = ("__weakref__", "_authentic")

    def __init__(self, token: object) -> None:
        if token is not _BINDING_TOKEN:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_authentic", _BINDING_TOKEN)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorBuiltBinding()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor built binding is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor built binding cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor built binding cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor built binding cannot be serialized")


class _RelayLinuxExecutorBuiltEvidence:
    """Canonical private path authority retained outside the caller receipt."""

    __slots__ = (
        "authority",
        "binding",
        "built",
        "bundle",
        "command",
        "construction",
        "consumer",
        "destination",
        "digest",
        "executor",
        "key",
        "owner_token",
        "process_receipt",
        "record_token",
        "request",
        "request_values",
        "reservation",
        "source",
        "source_commit",
    )

    def __init__(
        self,
        token: object,
        *,
        binding: _RelayLinuxExecutorBuiltBinding,
        authority: object,
        executor: _RelayLinuxExecutorOwner,
        destination: _RelayLinuxExecutorDestination,
        key: _RelayLinuxExecutorKey,
        bundle: _WorkspaceWorkerBundle,
        construction: _WorkspaceWorkerThreadReceipt,
        built: _WorkspaceBuiltReceipt,
        command: _WorkspaceBuildCommand,
        consumer: _WorkspaceBuiltConsumerToken,
        owner_token: object,
        record_token: object,
        reservation: object,
        digest: bytes,
        process_receipt: object,
        request: _RelayLinuxBuildWorkspaceRequest,
        request_values: tuple[object, ...],
        source: object,
        source_commit: str,
    ) -> None:
        if token is not _EVIDENCE_TOKEN:
            raise TypeError(_FAILURE)
        values = locals().copy()
        for name in self.__slots__:
            object.__setattr__(self, name, values[name])

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor built evidence is immutable")


_EVIDENCE_BY_KEY: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorBuiltEvidence,
] = weakref.WeakKeyDictionary()
_KEYS_BY_BINDING: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorKey,
] = weakref.WeakKeyDictionary()
_BINDINGS_BY_BUILT: weakref.WeakKeyDictionary[
    _WorkspaceBuiltReceipt,
    _RelayLinuxExecutorBuiltBinding,
] = weakref.WeakKeyDictionary()
_RELEASE_BINDINGS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorBuiltEvidence,
] = weakref.WeakKeyDictionary()
_BUILD_RETIREMENTS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorBuiltEvidence,
] = weakref.WeakKeyDictionary()


def _new_executor_built_binding() -> _RelayLinuxExecutorBuiltBinding:
    return _RelayLinuxExecutorBuiltBinding(_BINDING_TOKEN)


def _new_executor_built_evidence(**values: object) -> _RelayLinuxExecutorBuiltEvidence:
    return _RelayLinuxExecutorBuiltEvidence(_EVIDENCE_TOKEN, **values)  # type: ignore[arg-type]


def _store_executor_built_evidence(evidence: _RelayLinuxExecutorBuiltEvidence) -> None:
    _store_evidence_by_key(evidence.key, evidence)
    _store_key_by_binding(evidence.binding, evidence.key)
    _store_binding_by_built(evidence.built, evidence.binding)


def _store_evidence_by_key(key: object, evidence: object) -> None:
    _EVIDENCE_BY_KEY[key] = evidence  # type: ignore[index,assignment]


def _store_key_by_binding(binding: object, key: object) -> None:
    _KEYS_BY_BINDING[binding] = key  # type: ignore[index,assignment]


def _store_binding_by_built(built: object, binding: object) -> None:
    _BINDINGS_BY_BUILT[built] = binding  # type: ignore[index,assignment]


def _pop_executor_built_evidence(evidence: _RelayLinuxExecutorBuiltEvidence) -> None:
    if _BINDINGS_BY_BUILT.get(evidence.built) is evidence.binding:
        _BINDINGS_BY_BUILT.pop(evidence.built, None)
    if _KEYS_BY_BINDING.get(evidence.binding) is evidence.key:
        _KEYS_BY_BINDING.pop(evidence.binding, None)
    if _EVIDENCE_BY_KEY.get(evidence.key) is evidence:
        _EVIDENCE_BY_KEY.pop(evidence.key, None)


def _store_executor_build_release(
    key: _RelayLinuxExecutorKey,
    evidence: _RelayLinuxExecutorBuiltEvidence,
) -> None:
    _RELEASE_BINDINGS[key] = evidence


def _pop_executor_build_release(key: _RelayLinuxExecutorKey) -> None:
    _RELEASE_BINDINGS.pop(key, None)


def _store_executor_build_retirement(
    key: _RelayLinuxExecutorKey,
    evidence: _RelayLinuxExecutorBuiltEvidence,
) -> None:
    _BUILD_RETIREMENTS[key] = evidence


def _pop_executor_build_retirement(key: _RelayLinuxExecutorKey) -> None:
    _BUILD_RETIREMENTS.pop(key, None)


__all__: list[str] = []

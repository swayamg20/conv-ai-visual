"""Opaque values for the filesystem-inert workspace worker lifecycle."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceRequest,
    _WorkspacePreparationReceiptDestination,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_COORDINATOR_TOKEN = object()
_START_TOKEN = object()
_TERMINAL_TOKEN = object()
_CLAIM_TOKEN = object()
_FAILURE = "Relay Linux workspace worker lifecycle is invalid"


class _WorkspaceWorkerStartReceipt:
    """Opaque path-free proof of one durable start intent."""

    __slots__ = ("_owner_token", "_record_token", "status")

    def __init__(self, token: object, *, owner_token: object, record_token: object) -> None:
        if (
            token is not _START_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "status", "workspace-worker-start-intended")

    def _matches(self, owner_token: object, record_token: object) -> bool:
        return self._owner_token is owner_token and self._record_token is record_token

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker start receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker start receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker start receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker start receipt cannot be serialized")


class _WorkspaceWorkerTerminalReceipt:
    """Canonical path-free synthetic terminal, including prestart no-effect."""

    __slots__ = (
        "_owner_token",
        "_record_token",
        "_release_intended",
        "started",
        "status",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
        started: bool,
    ) -> None:
        if (
            token is not _TERMINAL_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(started) is not bool
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_release_intended", False)
        object.__setattr__(self, "started", started)
        object.__setattr__(
            self,
            "status",
            "workspace-worker-synthetic-terminal"
            if started
            else "workspace-worker-cancelled-before-start",
        )

    def _matches(self, owner_token: object, record_token: object) -> bool:
        return bool(
            self._owner_token is owner_token
            and self._record_token is record_token
            and type(self.started) is bool
        )

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker terminal is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker terminal cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker terminal cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker terminal cannot be serialized")


class _WorkspaceWorkerCoordinator:
    """Bundle-owned path-free lifecycle and release tombstone."""

    __slots__ = (
        "_claim_token",
        "_condition",
        "_effect_phase",
        "_handoff_expired",
        "_join_lock",
        "_joined",
        "_owner_token",
        "_phase",
        "_pin_phase",
        "_record_token",
        "_release_deadline",
        "_release_lock",
        "_release_phase",
        "_settlement_token",
        "_start_deadline",
        "_start_receipt",
        "_terminal",
        "_workspace_settled",
    )

    def __init__(self, token: object, *, owner_token: object, record_token: object) -> None:
        if token is not _COORDINATOR_TOKEN:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_condition", threading.Condition())
        object.__setattr__(self, "_join_lock", threading.Lock())
        object.__setattr__(self, "_phase", "dormant")
        object.__setattr__(self, "_pin_phase", "none")
        object.__setattr__(self, "_effect_phase", "none")
        object.__setattr__(self, "_handoff_expired", False)
        object.__setattr__(self, "_claim_token", None)
        object.__setattr__(self, "_settlement_token", None)
        object.__setattr__(self, "_start_receipt", None)
        object.__setattr__(self, "_start_deadline", None)
        object.__setattr__(self, "_terminal", None)
        object.__setattr__(self, "_workspace_settled", False)
        object.__setattr__(self, "_joined", False)
        object.__setattr__(self, "_release_phase", "none")
        object.__setattr__(self, "_release_deadline", None)
        object.__setattr__(self, "_release_lock", threading.Lock())

    def _matches(self, owner_token: object, record_token: object) -> bool:
        return self._owner_token is owner_token and self._record_token is record_token

    def _notify(self) -> None:
        return

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker coordinator is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker coordinator cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker coordinator cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker coordinator cannot be serialized")


class _WorkspaceWorkerClaim:
    """Exact worker-local path claim, scrubbed before terminal publication."""

    __slots__ = (
        "_bundle",
        "_claim_token",
        "_controller",
        "_coordinator",
        "_owner_token",
        "_paths_cleared",
        "_prepared_destination",
        "_record_token",
        "_request",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
        claim_token: object,
        coordinator: _WorkspaceWorkerCoordinator,
        controller: _WorkspaceWorkerController,
        bundle: _WorkspaceWorkerBundle,
        request: _RelayLinuxBuildWorkspaceRequest,
        prepared_destination: _WorkspacePreparationReceiptDestination,
    ) -> None:
        if (
            token is not _CLAIM_TOKEN
            or type(claim_token) is not object
            or type(coordinator) is not _WorkspaceWorkerCoordinator
            or not coordinator._matches(owner_token, record_token)
            or type(controller) is not _WorkspaceWorkerController
            or type(bundle) is not _WorkspaceWorkerBundle
            or type(request) is not _RelayLinuxBuildWorkspaceRequest
            or type(prepared_destination) is not _WorkspacePreparationReceiptDestination
            or bundle._prepared_destination is not prepared_destination
            or prepared_destination._request is not request
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_claim_token", claim_token)
        object.__setattr__(self, "_coordinator", coordinator)
        object.__setattr__(self, "_controller", controller)
        object.__setattr__(self, "_bundle", bundle)
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_prepared_destination", prepared_destination)
        object.__setattr__(self, "_paths_cleared", False)

    def _scrub_paths(self) -> None:
        object.__setattr__(self, "_bundle", None)
        object.__setattr__(self, "_request", None)
        object.__setattr__(self, "_prepared_destination", None)
        object.__setattr__(self, "_paths_cleared", True)

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker claim is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker claim cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker claim cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker claim cannot be serialized")


__all__: list[str] = []

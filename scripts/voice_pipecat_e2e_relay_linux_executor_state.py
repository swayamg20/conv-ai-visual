"""Private preowned state for one disposable relay Linux executor.

This module reserves only in-process ownership.  It performs no filesystem,
socket, child-process, Docker, network, or browser effect.
"""

from __future__ import annotations

import threading
import time
import weakref
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_RELAY_MAX_PORT,
    COTURN_RELAY_MIN_PORT,
    COTURN_TLS_PORT,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _new_relay_linux_build_workspace_destination,
    _RelayLinuxBuildWorkspaceDestination,
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_consumer import (
    _workspace_worker_registries_are_empty,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry import (
    _WorkspaceWorkerThreadReceipt,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_worker_contract import (
    _workspace_worker_receipt_is_current,
)
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwnerDestination,
    new_relay_probe_owner_destination,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeSource
from scripts.voice_pipecat_e2e_stack import PIPECAT_PORT, WEB_PORT, WEB_ROOT

_DESTINATION_TOKEN = object()
_OWNER_TOKEN = object()
_AUTHORITY_TOKEN = object()
_FAILURE = "Relay Linux executor ownership is invalid"
_LOCK = threading.RLock()
_FIXED_PORTS = (
    PIPECAT_PORT,
    WEB_PORT,
    COTURN_TLS_PORT,
    *range(COTURN_RELAY_MIN_PORT, COTURN_RELAY_MAX_PORT + 1),
)
_EXECUTORS: dict[
    object,
    tuple[
        _RelayLinuxExecutorOwner,
        _RelayLinuxExecutorDestination,
        _WorkspaceWorkerBundle | None,
        _WorkspaceWorkerThreadReceipt | None,
        object | None,
        str,
    ],
] = {}
_PORT_RESERVATIONS: dict[tuple[int, ...], object] = {}
_RETIRED_KEYS: weakref.WeakSet["_RelayLinuxExecutorKey"] = weakref.WeakSet()
_AUTHORITY_KEYS: weakref.WeakKeyDictionary[object, _RelayLinuxExecutorKey] = (
    weakref.WeakKeyDictionary()
)
_DESTINATION_KEYS: weakref.WeakKeyDictionary[object, _RelayLinuxExecutorKey] = (
    weakref.WeakKeyDictionary()
)
_OWNER_KEYS: weakref.WeakKeyDictionary[object, _RelayLinuxExecutorKey] = weakref.WeakKeyDictionary()
_SOURCE_EVIDENCE: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[RelayProbeSource, str, Path],
] = weakref.WeakKeyDictionary()
_WORKSPACE_RELEASES: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[_WorkspaceWorkerBundle, _WorkspaceWorkerThreadReceipt, object],
] = weakref.WeakKeyDictionary()


class _RelayLinuxExecutorError(RuntimeError):
    """The private disposable-executor ownership graph was inconsistent."""


class _RelayLinuxExecutorKey:
    __slots__ = ("__weakref__",)


class _RelayLinuxExecutorCleanupAuthority:
    """Opaque recovery key preowned before any worker can be constructed."""

    __slots__ = ("__weakref__", "_authentic", "_key")

    def __init__(
        self,
        token: object,
        *,
        key: object,
    ) -> None:
        if token is not _AUTHORITY_TOKEN or type(key) is not _RelayLinuxExecutorKey:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_authentic", _AUTHORITY_TOKEN)
        object.__setattr__(self, "_key", key)

    def _is_authentic(self) -> bool:
        return bool(
            self._authentic is _AUTHORITY_TOKEN and type(self._key) is _RelayLinuxExecutorKey
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorCleanupAuthority()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor cleanup authority is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor cleanup authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor cleanup authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor cleanup authority cannot be serialized")


class _RelayLinuxExecutorOwner:
    """Preowned outer aggregate root, still inert until a later facade drives it."""

    __slots__ = (
        "__weakref__",
        "_cleanup_authority",
        "_destination",
        "_relay_owner_destination",
        "_source",
        "_workspace_destination",
        "_workspace_owner",
    )

    def __init__(
        self,
        token: object,
        *,
        destination: _RelayLinuxExecutorDestination,
        source: RelayProbeSource,
        workspace_destination: _RelayLinuxBuildWorkspaceDestination,
        relay_owner_destination: RelayProbeOwnerDestination,
    ) -> None:
        if (
            token is not _OWNER_TOKEN
            or type(destination) is not _RelayLinuxExecutorDestination
            or type(source) is not RelayProbeSource
            or type(workspace_destination) is not _RelayLinuxBuildWorkspaceDestination
            or type(relay_owner_destination) is not RelayProbeOwnerDestination
        ):
            raise TypeError(_FAILURE)
        workspace_owner = workspace_destination._read(workspace_destination._request)
        if type(workspace_owner) is not _RelayLinuxBuildWorkspaceOwner:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_destination", destination)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_workspace_destination", workspace_destination)
        object.__setattr__(self, "_workspace_owner", workspace_owner)
        object.__setattr__(self, "_relay_owner_destination", relay_owner_destination)
        object.__setattr__(
            self,
            "_cleanup_authority",
            _RelayLinuxExecutorCleanupAuthority(
                _AUTHORITY_TOKEN,
                key=_RelayLinuxExecutorKey(),
            ),
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorOwner()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor owner is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor owner cannot be serialized")


class _RelayLinuxExecutorDestination:
    """Caller-preowned destination holding the exact inert outer owner."""

    __slots__ = ("__weakref__", "_owner", "_source", "_workspace_destination")

    def __init__(
        self,
        token: object,
        *,
        source: RelayProbeSource,
        workspace_destination: _RelayLinuxBuildWorkspaceDestination,
    ) -> None:
        if (
            token is not _DESTINATION_TOKEN
            or type(source) is not RelayProbeSource
            or type(workspace_destination) is not _RelayLinuxBuildWorkspaceDestination
        ):
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_workspace_destination", workspace_destination)
        object.__setattr__(
            self,
            "_owner",
            _RelayLinuxExecutorOwner(
                _OWNER_TOKEN,
                destination=self,
                source=source,
                workspace_destination=workspace_destination,
                relay_owner_destination=new_relay_probe_owner_destination(),
            ),
        )
        owner = self._owner
        key = owner._cleanup_authority._key
        with _LOCK:
            _DESTINATION_KEYS[self] = key
            _OWNER_KEYS[owner] = key
            _AUTHORITY_KEYS[owner._cleanup_authority] = key
            _SOURCE_EVIDENCE[key] = (source, source.commit_sha, WEB_ROOT)

    def _read(self) -> _RelayLinuxExecutorOwner:
        return self._owner

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorDestination()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor destination is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor destination cannot be serialized")


def _new_relay_linux_executor_destination(
    *,
    source_root: Path,
    run_parent: Path,
    node: Path,
    run_id: str,
    source: RelayProbeSource,
) -> _RelayLinuxExecutorDestination:
    """Preown the complete inert outer/workspace/inner destination graph."""

    if type(source) is not RelayProbeSource:
        raise _RelayLinuxExecutorError(_FAILURE)
    if type(source_root) is not type(WEB_ROOT) or source_root != WEB_ROOT:
        raise _RelayLinuxExecutorError(_FAILURE)
    workspace_destination = _new_relay_linux_build_workspace_destination(
        source_root=WEB_ROOT,
        run_parent=run_parent,
        node=node,
        run_id=run_id,
    )
    return _RelayLinuxExecutorDestination(
        _DESTINATION_TOKEN,
        source=source,
        workspace_destination=workspace_destination,
    )


def _preown_relay_linux_executor(
    destination: _RelayLinuxExecutorDestination,
) -> _RelayLinuxExecutorOwner:
    """Atomically reserve the cap-one aggregate and every fixed host port."""

    if type(destination) is not _RelayLinuxExecutorDestination:
        raise _RelayLinuxExecutorError(_FAILURE)
    owner = destination._read()
    if not _executor_value_matches(owner, destination):
        raise _RelayLinuxExecutorError(_FAILURE)
    key = _canonical_executor_key(owner, destination)
    if key is None:
        raise _RelayLinuxExecutorError(_FAILURE)
    record = (owner, destination, None, None, None, "preowned")
    with _LOCK:
        if key in _RETIRED_KEYS:
            raise _RelayLinuxExecutorError(_FAILURE)
        existing = _EXECUTORS.get(key)
        reservation = _PORT_RESERVATIONS.get(_FIXED_PORTS)
        if _preowned_record_matches(existing, owner, destination, key) and reservation is key:
            return owner
        if existing is not None or _EXECUTORS or reservation is not None or _PORT_RESERVATIONS:
            raise _RelayLinuxExecutorError("Relay Linux executor capacity is exhausted")
        try:
            _store_port_reservation(_FIXED_PORTS, key)
            _store_executor_record(key, record)
        except BaseException:
            if (
                _preowned_record_matches(_EXECUTORS.get(key), owner, destination, key)
                and _PORT_RESERVATIONS.get(_FIXED_PORTS) is key
            ):
                raise
            if _EXECUTORS.get(key) is None:
                _PORT_RESERVATIONS.pop(_FIXED_PORTS, None)
            raise
        if (
            not _preowned_record_matches(_EXECUTORS.get(key), owner, destination, key)
            or _PORT_RESERVATIONS.get(_FIXED_PORTS) is not key
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        return owner


def _release_unstarted_relay_linux_executor(
    authority: _RelayLinuxExecutorCleanupAuthority,
) -> bool:
    """Release an outer aggregate that never bound or fully released its worker."""

    if type(authority) is not _RelayLinuxExecutorCleanupAuthority:
        return False
    key = _AUTHORITY_KEYS.get(authority)
    if type(key) is not _RelayLinuxExecutorKey:
        return False
    released_graph: (
        tuple[
            _RelayLinuxBuildWorkspaceOwner,
            _WorkspaceWorkerBundle,
            _WorkspaceWorkerThreadReceipt,
        ]
        | None
    ) = None
    with _LOCK:
        record = _EXECUTORS.get(key)
        reservation = _PORT_RESERVATIONS.get(_FIXED_PORTS)
        retired = key in _RETIRED_KEYS
        if (
            any(candidate is not key for candidate in _EXECUTORS)
            or any(
                ports is not _FIXED_PORTS or candidate is not key
                for ports, candidate in _PORT_RESERVATIONS.items()
            )
            or any(candidate is not key for candidate in _WORKSPACE_RELEASES)
        ):
            return False
        if retired:
            if record is not None and not _unstarted_retirement_record_matches(
                record,
                key,
                authority,
            ):
                return False
            if reservation is not None and reservation is not key:
                return False
        else:
            if (
                not _unstarted_retirement_record_matches(record, key, authority)
                or reservation is not key
            ):
                return False
        if record is not None and record[5] == "workspace-released":
            released_graph = (record[0]._workspace_owner, record[2], record[3])
        snapshot = record
    if released_graph is not None:
        workspace_owner, bundle, construction = released_graph
        if (
            _workspace_worker_receipt_is_current(
                workspace_owner,
                bundle,
                construction,
                time.monotonic() + 0.05,
            )
            != "absent"
            or _workspace_worker_registries_are_empty(time.monotonic() + 0.05) is not True
        ):
            return False
        from scripts.voice_pipecat_e2e_relay_linux_executor_build_consume import (
            _retire_released_executor_built_state,
        )

        if not _retire_released_executor_built_state(key):
            return False
    elif _workspace_worker_registries_are_empty(time.monotonic() + 0.05) is not True:
        return False
    with _LOCK:
        if _EXECUTORS.get(key) is not snapshot:
            return False
        record = snapshot
        reservation = _PORT_RESERVATIONS.get(_FIXED_PORTS)
        retired = key in _RETIRED_KEYS
        if (
            any(candidate is not key for candidate in _EXECUTORS)
            or any(
                ports is not _FIXED_PORTS or candidate is not key
                for ports, candidate in _PORT_RESERVATIONS.items()
            )
            or any(candidate is not key for candidate in _WORKSPACE_RELEASES)
        ):
            return False
        if not retired:
            if (
                not _unstarted_retirement_record_matches(record, key, authority)
                or reservation is not key
            ):
                return False
            _store_retired_key(key)
        if record is not None:
            _pop_executor_record(key)
        if reservation is key:
            _pop_port_reservation(_FIXED_PORTS)
        if key in _WORKSPACE_RELEASES:
            _pop_workspace_release_evidence(key)
        return (
            key in _RETIRED_KEYS
            and key not in _EXECUTORS
            and _FIXED_PORTS not in _PORT_RESERVATIONS
            and key not in _WORKSPACE_RELEASES
        )


def _executor_value_matches(
    owner: object,
    destination: object,
) -> bool:
    key = _canonical_executor_key(owner, destination)
    if (
        type(owner) is not _RelayLinuxExecutorOwner
        or type(destination) is not _RelayLinuxExecutorDestination
        or destination._read() is not owner
        or owner._destination is not destination
        or owner._source is not destination._source
        or owner._workspace_destination is not destination._workspace_destination
        or owner._workspace_owner
        is not destination._workspace_destination._read(destination._workspace_destination._request)
        or type(owner._relay_owner_destination) is not RelayProbeOwnerDestination
        or not owner._cleanup_authority._is_authentic()
        or key is None
        or owner._cleanup_authority._key is not key
        or not _source_evidence_matches(key, owner, destination)
    ):
        return False
    request = owner._workspace_owner._request
    return bool(request is destination._workspace_destination._request)


def _source_evidence_matches(
    key: _RelayLinuxExecutorKey,
    owner: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
) -> bool:
    try:
        source = object.__getattribute__(owner, "_source")
        destination_source = object.__getattribute__(destination, "_source")
        commit_sha = object.__getattribute__(source, "_commit_sha")
        evidence = _SOURCE_EVIDENCE.get(key)
    except BaseException:
        return False
    return bool(
        type(evidence) is tuple
        and len(evidence) == 3
        and type(source) is RelayProbeSource
        and destination_source is source
        and type(commit_sha) is str
        and evidence[0] is source
        and type(evidence[1]) is str
        and evidence[1] == commit_sha
        and evidence[2] is WEB_ROOT
    )


def _executor_source_evidence_graph_matches(
    key: object,
    owner: object,
    destination: object,
) -> bool:
    """Reject any source entry not owned by this executor or a retired key."""

    if (
        type(key) is not _RelayLinuxExecutorKey
        or type(owner) is not _RelayLinuxExecutorOwner
        or type(destination) is not _RelayLinuxExecutorDestination
        or not _source_evidence_matches(key, owner, destination)
    ):
        return False
    try:
        return all(
            type(candidate) is _RelayLinuxExecutorKey
            and (candidate is key or candidate in _RETIRED_KEYS)
            and type(evidence) is tuple
            and len(evidence) == 3
            and type(evidence[0]) is RelayProbeSource
            and type(evidence[1]) is str
            and evidence[1] is object.__getattribute__(evidence[0], "_commit_sha")
            and evidence[2] is WEB_ROOT
            for candidate, evidence in _SOURCE_EVIDENCE.items()
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _executor_record_matches(
    record: object,
    owner: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    phase: str,
) -> bool:
    key = _canonical_executor_key(owner, destination)
    return bool(
        type(record) is tuple
        and len(record) == 6
        and record[0] is owner
        and record[1] is destination
        and type(record[5]) is str
        and record[5] == phase
        and _executor_value_matches(owner, destination)
        and key is not None
        and len(_EXECUTORS) == 1
        and _EXECUTORS.get(key) is record
        and _port_reservation_matches(key)
    )


def _preowned_record_matches(
    record: object,
    owner: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    key: _RelayLinuxExecutorKey,
) -> bool:
    return bool(
        _executor_record_matches(record, owner, destination, "preowned")
        and record[2] is None
        and record[3] is None
        and record[4] is None
        and _canonical_executor_key(owner, destination) is key
    )


def _port_reservation_matches(key: _RelayLinuxExecutorKey) -> bool:
    return bool(
        len(_PORT_RESERVATIONS) == 1
        and next(iter(_PORT_RESERVATIONS)) is _FIXED_PORTS
        and _PORT_RESERVATIONS.get(_FIXED_PORTS) is key
    )


def _unstarted_retirement_record_matches(
    record: object,
    key: object,
    authority: _RelayLinuxExecutorCleanupAuthority,
) -> bool:
    if type(record) is not tuple or len(record) != 6:
        return False
    owner, destination = record[:2]
    evidence = _WORKSPACE_RELEASES.get(key)
    preowned = bool(
        record[2] is None
        and record[3] is None
        and record[4] is None
        and type(record[5]) is str
        and record[5] == "preowned"
        and evidence is None
    )
    released = bool(
        type(record[2]) is _WorkspaceWorkerBundle
        and type(record[3]) is _WorkspaceWorkerThreadReceipt
        and type(record[4]) is object
        and type(record[5]) is str
        and record[5] == "workspace-released"
        and type(evidence) is tuple
        and len(evidence) == 3
        and evidence[0] is record[2]
        and evidence[1] is record[3]
        and evidence[2] is record[4]
    )
    return bool(
        type(owner) is _RelayLinuxExecutorOwner
        and type(destination) is _RelayLinuxExecutorDestination
        and (preowned or released)
        and _OWNER_KEYS.get(owner) is key
        and _DESTINATION_KEYS.get(destination) is key
        and _AUTHORITY_KEYS.get(authority) is key
    )


def _canonical_executor_key(
    owner: object,
    destination: object,
) -> _RelayLinuxExecutorKey | None:
    if (
        type(owner) is not _RelayLinuxExecutorOwner
        or type(destination) is not _RelayLinuxExecutorDestination
    ):
        return None
    owner_key = _OWNER_KEYS.get(owner)
    destination_key = _DESTINATION_KEYS.get(destination)
    try:
        authority = owner._cleanup_authority
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None
    if type(authority) is not _RelayLinuxExecutorCleanupAuthority:
        return None
    authority_key = _AUTHORITY_KEYS.get(authority)
    if (
        type(owner_key) is _RelayLinuxExecutorKey
        and destination_key is owner_key
        and authority_key is owner_key
    ):
        return owner_key
    return None


def _store_executor_record(key: object, record: tuple[object, ...]) -> None:
    _EXECUTORS[key] = record  # type: ignore[assignment]


def _store_port_reservation(ports: tuple[int, ...], key: object) -> None:
    _PORT_RESERVATIONS[ports] = key


def _store_retired_key(key: _RelayLinuxExecutorKey) -> None:
    _RETIRED_KEYS.add(key)


def _store_workspace_release_evidence(
    key: _RelayLinuxExecutorKey,
    evidence: tuple[_WorkspaceWorkerBundle, _WorkspaceWorkerThreadReceipt, object],
) -> None:
    _WORKSPACE_RELEASES[key] = evidence


def _pop_executor_record(key: object) -> None:
    _EXECUTORS.pop(key, None)


def _pop_port_reservation(ports: tuple[int, ...]) -> None:
    _PORT_RESERVATIONS.pop(ports, None)


def _pop_workspace_release_evidence(key: _RelayLinuxExecutorKey) -> None:
    _WORKSPACE_RELEASES.pop(key, None)


__all__: list[str] = []

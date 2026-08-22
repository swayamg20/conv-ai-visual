"""Worker-stack-only provenance fence immediately before one build start."""

from __future__ import annotations

import math
import threading
import time

import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _resolve_workspace_worker_active_record,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
    _spawn_spec_fingerprint,
    _workspace_request_spawn_fingerprint,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_values import (
    _COMMANDS,
    _workspace_build_command_authorizes_process,
    _WorkspaceBuildCommand,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract import (
    _FAILURE,
    _workspace_filesystem_claim_matches,
    _workspace_prepared_build_matches,
    _WorkspaceFilesystemError,
    _WorkspaceFilesystemIdentity,
    _WorkspacePreparedReceipt,
    _WorkspaceSourceNode,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_copy import (
    _snapshot_workspace_source,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open import (
    _bounded_names,
    _require_named_identity,
    _require_private_parent,
    _stable_binding,
    _WorkspaceDescriptorSet,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_values import (
    _WorkspacePreparedDestinationBaseline,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_output_workspace import (
    _snapshot_workspace_build_inputs,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_provenance import (
    _revalidate_named_anchors,
    _snapshot_tools,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_values import (
    _WorkspaceWorkerClaim,
    _WorkspaceWorkerCoordinator,
)

_AUTHORITY_TOKEN = object()
_LOCK_SLICE_SECONDS = 0.05


class _WorkspaceBuildPrestartAuthority:
    """Exact filesystem and lifecycle evidence retained only by the worker."""

    __slots__ = (
        "_authentic",
        "baseline",
        "claim",
        "current",
        "descriptors",
        "next_fd",
        "next_package_fd",
        "node_fd",
        "node_lock_fd",
        "node_modules_identity",
        "prepared",
        "run_identity",
        "run_parent_fd",
        "run_parent_identity",
        "run_root_fd",
        "source_fd",
        "source_identity",
        "source_nodes",
        "tool_values",
        "workspace_fd",
        "workspace_identity",
    )

    def __init__(
        self,
        token: object,
        **values: object,
    ) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_authentic", token)
        for name in self.__slots__[1:]:
            object.__setattr__(self, name, values[name])

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace prestart authority is immutable")


def _new_workspace_build_prestart_authority(
    *,
    claim: _WorkspaceWorkerClaim,
    current: registry._WorkspaceWorkerThread,
    prepared: _WorkspacePreparedReceipt,
    source_fd: int,
    source_identity: _WorkspaceFilesystemIdentity,
    source_nodes: tuple[_WorkspaceSourceNode, ...],
    run_parent_fd: int,
    run_parent_identity: _WorkspaceFilesystemIdentity,
    run_root_fd: int,
    run_identity: _WorkspaceFilesystemIdentity,
    workspace_fd: int,
    workspace_identity: _WorkspaceFilesystemIdentity,
    node_fd: int,
    next_fd: int,
    node_lock_fd: int,
    next_package_fd: int,
    node_modules_identity: _WorkspaceFilesystemIdentity,
    tool_values: tuple[object, ...],
    baseline: _WorkspacePreparedDestinationBaseline,
    descriptors: _WorkspaceDescriptorSet,
) -> _WorkspaceBuildPrestartAuthority:
    values = locals()
    if (
        type(claim) is not _WorkspaceWorkerClaim
        or type(current) is not registry._WorkspaceWorkerThread
        or threading.current_thread() is not current
        or type(prepared) is not _WorkspacePreparedReceipt
        or any(
            type(values[name]) is not int
            for name in (
                "source_fd",
                "run_parent_fd",
                "run_root_fd",
                "workspace_fd",
                "node_fd",
                "next_fd",
                "node_lock_fd",
                "next_package_fd",
            )
        )
        or any(
            type(values[name]) is not _WorkspaceFilesystemIdentity
            for name in (
                "source_identity",
                "run_parent_identity",
                "run_identity",
                "workspace_identity",
                "node_modules_identity",
            )
        )
        or type(source_nodes) is not tuple
        or any(type(node) is not _WorkspaceSourceNode for node in source_nodes)
        or type(tool_values) is not tuple
        or type(baseline) is not _WorkspacePreparedDestinationBaseline
        or type(descriptors) is not _WorkspaceDescriptorSet
        or claim._request is None
        or not baseline._matches(
            owner_token=claim._owner_token,
            record_token=claim._record_token,
            run_id=claim._request._run_id,
            workspace_binding=_stable_binding(workspace_identity),
        )
        or _stable_binding(descriptors.identity(workspace_fd)) != baseline.workspace_binding
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    authority = _WorkspaceBuildPrestartAuthority(
        _AUTHORITY_TOKEN,
        **{name: value for name, value in values.items() if name != "values"},
    )
    if not _wait_for_workspace_build_claim(
        authority,
        claim._controller,
        time.monotonic() + _LOCK_SLICE_SECONDS,
    ):
        raise _WorkspaceFilesystemError(_FAILURE)
    return authority


def _revalidate_workspace_build_prestart(
    authority: object,
    *,
    command: _WorkspaceBuildCommand,
    expected_spec: object,
    controller: _WorkspaceWorkerController,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> bool:
    if not _prestart_shape_matches(
        authority,
        command,
        controller,
        owner_token,
        record_token,
        build_deadline,
    ):
        return False
    request = authority.claim._request
    try:
        command_state = _COMMANDS.get(command)
        if not (
            type(command_state) is tuple
            and len(command_state) == 6
            and command_state[0] is owner_token
            and command_state[1] is record_token
            and command_state[2] is authority.prepared
            and command_state[3] == build_deadline
            and command_state[4] == "building"
            and command_state[5] == _workspace_request_spawn_fingerprint(request)
            and command_state[5] == _spawn_spec_fingerprint(expected_spec)
            and _workspace_prepared_build_matches(
                authority.prepared,
                owner_token,
                record_token,
                command,
                build_deadline,
            )
            and _wait_for_workspace_build_claim(authority, controller, build_deadline)
        ):
            return False
        _require_held_bindings(authority)
        entries, directories, max_nodes, max_bytes, max_depth = request._copy_policy()
        if (
            _snapshot_workspace_source(
                source_fd=authority.source_fd,
                entries=entries,
                directory_entries=directories,
                max_nodes=max_nodes,
                max_bytes=max_bytes,
                max_depth=max_depth,
                descriptors=authority.descriptors,
                controller=controller,
            )
            != authority.source_nodes
        ):
            return False
        if (
            _snapshot_tools(
                node_fd=authority.node_fd,
                next_fd=authority.next_fd,
                node_lock_fd=authority.node_lock_fd,
                next_package_fd=authority.next_package_fd,
                node_modules_identity=authority.node_modules_identity,
                controller=controller,
            )
            != authority.tool_values
        ):
            return False
        _revalidate_named_anchors(
            request=request,
            source_identity=authority.source_identity,
            run_parent_identity=authority.run_parent_identity,
            tool_values=authority.tool_values,
            descriptors=authority.descriptors,
            controller=controller,
        )
        observed = _snapshot_workspace_build_inputs(
            workspace_fd=authority.workspace_fd,
            owner_token=owner_token,
            record_token=record_token,
            run_id=request._run_id,
            expected_destination=authority.baseline.nodes,
            expected_node_modules=authority.baseline.node_modules_identity,
            node_modules_target=authority.baseline.node_modules_target,
            descriptors=authority.descriptors,
            controller=controller,
        )
        stored, acquired = _read_workspace_build_command_before(
            authority,
            command,
            controller,
            build_deadline,
        )
        if (
            not acquired
            or stored is not command
            or observed != authority.baseline
            or not _wait_for_workspace_build_claim(authority, controller, build_deadline)
            or not _workspace_build_command_authorizes_process(
                command,
                owner_token=owner_token,
                record_token=record_token,
                build_deadline=build_deadline,
            )
        ):
            return False
        now = time.monotonic()
        return bool(
            type(now) is float
            and math.isfinite(now)
            and now < build_deadline
            and controller._cancellation_requested() is False
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _revalidate_workspace_build_postprocess(
    authority: object,
    *,
    command: _WorkspaceBuildCommand,
    process_receipt: object,
    controller: _WorkspaceWorkerController,
    owner_token: object,
    record_token: object,
    build_deadline: float,
) -> bool:
    """Reprove process absence and every named input around output passes."""

    if not _prestart_shape_matches(
        authority,
        command,
        controller,
        owner_token,
        record_token,
        build_deadline,
    ):
        return False
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_build_process_contract import (
        _workspace_build_process_completed_zero,
    )

    request = authority.claim._request
    try:
        if not (
            _workspace_build_process_completed_zero(
                command,
                process_receipt,
                owner_token=owner_token,
                record_token=record_token,
                build_deadline=build_deadline,
            )
            and _wait_for_workspace_build_claim(authority, controller, build_deadline)
        ):
            return False
        _require_held_bindings(authority)
        entries, directories, max_nodes, max_bytes, max_depth = request._copy_policy()
        if (
            _snapshot_workspace_source(
                source_fd=authority.source_fd,
                entries=entries,
                directory_entries=directories,
                max_nodes=max_nodes,
                max_bytes=max_bytes,
                max_depth=max_depth,
                descriptors=authority.descriptors,
                controller=controller,
            )
            != authority.source_nodes
        ):
            return False
        if (
            _snapshot_tools(
                node_fd=authority.node_fd,
                next_fd=authority.next_fd,
                node_lock_fd=authority.node_lock_fd,
                next_package_fd=authority.next_package_fd,
                node_modules_identity=authority.node_modules_identity,
                controller=controller,
            )
            != authority.tool_values
        ):
            return False
        _revalidate_named_anchors(
            request=request,
            source_identity=authority.source_identity,
            run_parent_identity=authority.run_parent_identity,
            tool_values=authority.tool_values,
            descriptors=authority.descriptors,
            controller=controller,
        )
        if not (
            _workspace_build_process_completed_zero(
                command,
                process_receipt,
                owner_token=owner_token,
                record_token=record_token,
                build_deadline=build_deadline,
            )
            and _wait_for_workspace_build_claim(authority, controller, build_deadline)
        ):
            return False
        now = time.monotonic()
        return bool(
            type(now) is float
            and math.isfinite(now)
            and now < build_deadline
            and controller._cancellation_requested() is False
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _prestart_shape_matches(
    authority: object,
    command: object,
    controller: object,
    owner_token: object,
    record_token: object,
    build_deadline: object,
) -> bool:
    return bool(
        type(authority) is _WorkspaceBuildPrestartAuthority
        and authority._authentic is _AUTHORITY_TOKEN
        and type(command) is _WorkspaceBuildCommand
        and type(controller) is _WorkspaceWorkerController
        and controller is authority.claim._controller
        and controller._matches(owner_token)
        and owner_token is authority.claim._owner_token
        and record_token is authority.claim._record_token
        and type(build_deadline) is float
        and math.isfinite(build_deadline)
        and threading.current_thread() is authority.current
    )


def _require_held_bindings(authority: _WorkspaceBuildPrestartAuthority) -> None:
    for descriptor, identity in (
        (authority.source_fd, authority.source_identity),
        (authority.run_parent_fd, authority.run_parent_identity),
        (authority.run_root_fd, authority.run_identity),
        (authority.workspace_fd, authority.workspace_identity),
    ):
        if _stable_binding(authority.descriptors.identity(descriptor)) != _stable_binding(identity):
            raise _WorkspaceFilesystemError(_FAILURE)
    request = authority.claim._request
    current_run = _require_named_identity(
        authority.run_parent_fd,
        request._run_root.name,
        authority.run_root_fd,
        directory=True,
    )
    current_workspace = _require_named_identity(
        authority.run_root_fd,
        request._workspace.name,
        authority.workspace_fd,
        directory=True,
    )
    if (
        _stable_binding(current_run) != _stable_binding(authority.run_identity)
        or _stable_binding(current_workspace) != _stable_binding(authority.workspace_identity)
        or _stable_binding(_require_private_parent(authority.run_root_fd))
        != _stable_binding(authority.run_identity)
        or _bounded_names(authority.run_root_fd, 2) != (request._workspace.name,)
    ):
        raise _WorkspaceFilesystemError(_FAILURE)


def _workspace_build_claim_state(
    authority: _WorkspaceBuildPrestartAuthority,
    deadline: float,
) -> bool | None:
    claim = authority.claim
    bundle = claim._bundle
    if (
        type(deadline) is not float
        or not math.isfinite(deadline)
        or type(bundle) is not _WorkspaceWorkerBundle
        or not bundle._matches(claim._owner_token, claim._prepared_destination)
    ):
        return False
    try:
        active = _resolve_workspace_worker_active_record(claim._record_token, deadline)
    except (KeyboardInterrupt, SystemExit):
        raise
    except RuntimeError:
        return None
    except BaseException:
        return False
    remaining = max(0.0, deadline - time.monotonic())
    acquired = (
        registry._REGISTRY_LOCK.acquire(blocking=False)
        if remaining <= 0.0
        else registry._REGISTRY_LOCK.acquire(timeout=remaining)
    )
    if not acquired:
        return None
    try:
        record = registry._RECORDS.get(bundle)
        if (
            len(registry._RECORDS) != 1
            or type(record) is not registry._WorkspaceWorkerThreadRecord
            or active
            != (
                record,
                bundle._terminal_destination,
                claim._controller,
                claim._owner_token,
            )
        ):
            return False
        remaining = max(0.0, deadline - time.monotonic())
        locked = (
            record._lock.acquire(blocking=False)
            if remaining <= 0.0
            else record._lock.acquire(timeout=remaining)
        )
        if not locked:
            return None
        try:
            entry = record._entry
            coordinator = record._lifecycle
            return bool(
                type(entry) is tuple
                and len(entry) == 3
                and entry[0] == registry._INITIALIZED
                and entry[1] is authority.current
                and type(entry[2]) is registry._WorkspaceWorkerThreadReceipt
                and entry[2]._matches(claim._owner_token, claim._record_token)
                and _running_workspace_thread_matches(authority.current, record)
                and record._owner_token is claim._owner_token
                and record._record_token is claim._record_token
                and type(coordinator) is _WorkspaceWorkerCoordinator
                and coordinator is claim._coordinator
                and coordinator._phase == "claimed"
                and coordinator._claim_token is claim._claim_token
                and coordinator._settlement_token is claim._claim_token
                and claim._controller is bundle._controller
                and claim._command_destination is bundle._command_destination
                and claim._built_destination is bundle._built_destination
                and claim._request is claim._prepared_destination._request
                and _workspace_filesystem_claim_matches(
                    claim._owner_token,
                    claim._record_token,
                    claim._claim_token,
                )
            )
        finally:
            record._lock.release()
    finally:
        registry._REGISTRY_LOCK.release()


def _wait_for_workspace_build_claim(
    authority: _WorkspaceBuildPrestartAuthority,
    controller: _WorkspaceWorkerController,
    build_deadline: float,
) -> bool:
    while True:
        now = time.monotonic()
        if now >= build_deadline or controller._cancellation_requested() is True:
            return False
        state = _workspace_build_claim_state(
            authority,
            min(build_deadline, now + _LOCK_SLICE_SECONDS),
        )
        if state is not None:
            return state
        now = time.monotonic()
        if now >= build_deadline or controller._cancellation_requested() is True:
            return False
        controller._wait(min(0.01, build_deadline - now))


def _running_workspace_thread_matches(
    current: registry._WorkspaceWorkerThread,
    record: registry._WorkspaceWorkerThreadRecord,
) -> bool:
    try:
        values = vars(current)
        args = values.get("_args")
        started = values.get("_started")
        return bool(
            threading.current_thread() is current
            and values.get("_initialized") is True
            and values.get("_workspace_sealed") is True
            and values.get("_target") is registry._inert_workspace_worker_target
            and values.get("_name") == registry._THREAD_NAME
            and type(args) is tuple
            and len(args) == 1
            and args[0] is record._control_bridge
            and record._control_bridge._matches_current_worker()
            and values.get("_workspace_control_token") is record._control_bridge._state[2]
            and type(values.get("_kwargs")) is dict
            and not values["_kwargs"]
            and values.get("_daemonic") is False
            and type(started) is threading.Event
            and started.is_set()
            and values.get("_is_stopped", False) is False
            and values.get("_ident") == threading.get_ident()
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _read_workspace_build_command_before(
    authority: _WorkspaceBuildPrestartAuthority,
    command: _WorkspaceBuildCommand,
    controller: _WorkspaceWorkerController,
    build_deadline: float,
) -> tuple[object | None, bool]:
    while True:
        now = time.monotonic()
        if now >= build_deadline or controller._cancellation_requested() is True:
            return None, False
        stored, acquired = authority.claim._command_destination._read_before(
            authority.claim._owner_token,
            min(build_deadline, now + _LOCK_SLICE_SECONDS),
        )
        if acquired:
            return stored, stored is command
        now = time.monotonic()
        if now >= build_deadline or controller._cancellation_requested() is True:
            return None, False
        controller._wait(min(0.01, build_deadline - now))


__all__: list[str] = []

"""Preowned, identity-bound filesystem transaction for relay browser artifacts."""

from __future__ import annotations

import stat
import threading
from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_runtime_values import ControlSignal
from scripts.voice_pipecat_e2e_coturn_tls_file_cleanup import file_identity
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch
from scripts.voice_pipecat_e2e_relay_artifact_files import (
    MAX_REPORT_BYTES,
    MAX_RESULT_BYTES,
    ArtifactBudget,
    ArtifactNode,
    capture_node,
    capture_optional_log,
    clear_tree_content,
    close_unpublished_nodes,
    exact_private_file,
    list_names,
    mkdir_exact,
    name_absent,
    named_directory_matches,
    open_child_directory,
    open_exact_directory,
    safe_private_directory,
    scrub_exception,
    stat_name_status,
    sync_directory,
)
from scripts.voice_pipecat_e2e_stack import StackPaths

_WORKSPACE_TOKEN = object()


class DirectoryBinding:
    __slots__ = ("descriptor", "identity")

    def __init__(self, descriptor: int, identity: tuple[int, int]) -> None:
        self.descriptor = descriptor
        self.identity = identity


class ArtifactSnapshot:
    __slots__ = ("log", "nodes", "report", "result", "structural")

    def __init__(
        self,
        *,
        nodes: list[ArtifactNode],
        result: ArtifactNode | None,
        report: ArtifactNode | None,
        log: ArtifactNode | None,
        structural: bool,
    ) -> None:
        self.nodes = nodes
        self.result = result
        self.report = report
        self.log = log
        self.structural = structural


class RelayArtifactWorkspace:
    __slots__ = (
        "_complete",
        "_ephemeral_binding",
        "_ephemeral_claim",
        "_ephemeral_created",
        "_ephemeral_removed",
        "_invalid",
        "_operation_lock",
        "_paths",
        "_playwright_binding",
        "_playwright_claim",
        "_playwright_created",
        "_playwright_root",
        "_prepared",
        "_publication_safe",
        "_rescan_required",
        "_run_binding",
        "_settlement_started",
        "_snapshot",
    )

    def __init__(self, token: object, paths: StackPaths) -> None:
        authorized = token is _WORKSPACE_TOKEN
        token = None
        if not authorized or type(paths) is not StackPaths or not _valid_paths(paths):
            raise TypeError("Relay artifact workspace is factory-owned")
        self._paths = paths
        self._run_binding: DirectoryBinding | None = None
        self._playwright_claim: tuple[int, int] | None = None
        self._playwright_created = False
        self._playwright_binding: DirectoryBinding | None = None
        self._ephemeral_claim: tuple[int, int] | None = None
        self._ephemeral_created = False
        self._ephemeral_removed = False
        self._ephemeral_binding: DirectoryBinding | None = None
        self._playwright_root: ArtifactNode | None = None
        self._snapshot: ArtifactSnapshot | None = None
        self._operation_lock = threading.Lock()
        self._publication_safe = True
        self._prepared = False
        self._rescan_required = False
        self._settlement_started = False
        self._complete = False
        self._invalid = False

    def __repr__(self) -> str:
        return "RelayArtifactWorkspace()"

    def __copy__(self) -> None:
        raise TypeError("Relay artifact workspace cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay artifact workspace cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay artifact workspace cannot be serialized")


def _new_relay_artifact_workspace(paths: StackPaths) -> RelayArtifactWorkspace:
    return RelayArtifactWorkspace(_WORKSPACE_TOKEN, paths)


def _prepare_relay_artifact_workspace(
    workspace: RelayArtifactWorkspace,
    *,
    control_latch: TlsControlLatch,
) -> bool:
    if type(workspace) is not RelayArtifactWorkspace or type(control_latch) is not TlsControlLatch:
        return False
    with workspace._operation_lock:
        return _prepare_locked(workspace, control_latch)


def _prepare_locked(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> bool:
    if workspace._complete or workspace._invalid:
        return False
    if workspace._prepared:
        return True
    try:
        if not _ensure_run_binding(workspace, latch):
            return False
        run = workspace._run_binding
        if run is None:
            return False
        if workspace._playwright_claim is None and (
            not name_absent(run.descriptor, "playwright", latch)
            or not name_absent(run.descriptor, "playwright.log", latch)
        ):
            workspace._invalid = True
            workspace._publication_safe = False
            return False
        if not _ensure_child_binding(
            workspace,
            parent=run,
            name="playwright",
            claim_attribute="_playwright_claim",
            binding_attribute="_playwright_binding",
            created_attribute="_playwright_created",
            phase="playwright",
            latch=latch,
        ):
            return False
        playwright = workspace._playwright_binding
        if playwright is None:
            return False
        if workspace._ephemeral_claim is None and any(
            not name_absent(playwright.descriptor, name, latch)
            for name in (
                "voice-pipecat-rtc-result.json",
                "report.json",
                "relay-ephemeral-output",
            )
        ):
            workspace._invalid = True
            workspace._publication_safe = False
            return False
        if not _ensure_child_binding(
            workspace,
            parent=playwright,
            name="relay-ephemeral-output",
            claim_attribute="_ephemeral_claim",
            binding_attribute="_ephemeral_binding",
            created_attribute="_ephemeral_created",
            phase="ephemeral",
            latch=latch,
        ):
            return False
        workspace._prepared = True
        return True
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return False
    except BaseException as error:
        scrub_exception(error)
        return False


def _ensure_run_binding(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
) -> bool:
    if workspace._run_binding is not None:
        return True
    opened = False
    hook_ok = True
    try:
        opened = open_exact_directory(
            workspace._paths.run_dir,
            latch,
            sink=lambda descriptor, identity: _retain_directory_binding(
                workspace,
                "_run_binding",
                descriptor,
                identity,
                latch,
            ),
        )
        hook_ok = _hook("run-open-returned", latch)
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
    except BaseException as error:
        scrub_exception(error)
        hook_ok = False
    return bool(opened and hook_ok and workspace._run_binding is not None)


def _ensure_child_binding(
    workspace: RelayArtifactWorkspace,
    *,
    parent: DirectoryBinding,
    name: str,
    claim_attribute: str,
    binding_attribute: str,
    created_attribute: str,
    phase: str,
    latch: TlsControlLatch,
) -> bool:
    claim = getattr(workspace, claim_attribute)
    if claim is None:
        if (
            getattr(workspace, created_attribute)
            and _recover_created_claim(
                workspace,
                parent=parent,
                name=name,
                claim_attribute=claim_attribute,
                latch=latch,
            )
            != "claimed"
        ):
            return False
        claim = getattr(workspace, claim_attribute)
    if claim is None:
        created = False
        hook_ok = True
        try:
            created = mkdir_exact(
                parent.descriptor,
                name,
                latch,
                created_sink=lambda: _retain_created(
                    workspace,
                    created_attribute,
                    latch,
                ),
                identity_sink=lambda identity: _retain_value(
                    workspace,
                    claim_attribute,
                    identity,
                    latch,
                ),
            )
            hook_ok = _hook(f"{phase}-mkdir-returned", latch)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            hook_ok = False
        if not created:
            return False
        claim = getattr(workspace, claim_attribute)
        if claim is None:
            return False
        if not hook_ok:
            return False
    if getattr(workspace, binding_attribute) is not None:
        return True
    status, hook_ok = _open_binding_candidate(
        workspace,
        parent.descriptor,
        name,
        claim,
        phase,
        binding_attribute,
        latch,
    )
    if status != "opened" or getattr(workspace, binding_attribute) is None:
        return False
    if not sync_directory(parent.descriptor, latch):
        return False
    return bool(hook_ok and _hook(f"{phase}-directory-owned", latch))


def _recover_created_claim(
    workspace: RelayArtifactWorkspace,
    *,
    parent: DirectoryBinding,
    name: str,
    claim_attribute: str,
    latch: TlsControlLatch,
) -> str:
    status, details = stat_name_status(parent.descriptor, name, latch)
    if status == "absent":
        return "absent"
    if status != "found" or not safe_private_directory(details):
        return "error"
    return (
        "claimed"
        if _retain_value(
            workspace,
            claim_attribute,
            file_identity(details),
            latch,
        )
        else "error"
    )


def _retain_created(
    workspace: RelayArtifactWorkspace,
    attribute: str,
    latch: TlsControlLatch,
) -> bool:
    while True:
        try:
            setattr(workspace, attribute, True)
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
            if getattr(workspace, attribute):
                return True
        except BaseException as error:
            scrub_exception(error)
            return bool(getattr(workspace, attribute))


def _retain_directory_binding(
    workspace: RelayArtifactWorkspace,
    attribute: str,
    descriptor: int,
    identity: tuple[int, int],
    latch: TlsControlLatch,
) -> bool:
    binding: DirectoryBinding | None = None
    while True:
        try:
            current = getattr(workspace, attribute)
            if current is None:
                binding = binding or DirectoryBinding(descriptor, identity)
                setattr(workspace, attribute, binding)
                current = binding
            return bool(
                type(current) is DirectoryBinding
                and current.descriptor == descriptor
                and current.identity == identity
            )
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return False


def _retain_value(
    workspace: RelayArtifactWorkspace,
    attribute: str,
    value: object,
    latch: TlsControlLatch,
) -> bool:
    while True:
        try:
            current = getattr(workspace, attribute)
            if current is None:
                setattr(workspace, attribute, value)
                current = value
            return bool(current is value or current == value)
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            scrub_exception(error)
        except BaseException as error:
            scrub_exception(error)
            return False


def _sink_value(
    workspace: RelayArtifactWorkspace,
    attribute: str,
    value: object,
    phase: str,
    latch: TlsControlLatch,
) -> bool:
    return bool(
        _retain_value(workspace, attribute, value, latch) and _hook(f"{phase}-published", latch)
    )


def _capture_relay_artifacts(
    workspace: RelayArtifactWorkspace,
    *,
    initial_control: ControlSignal | None = None,
    control_latch: TlsControlLatch | None = None,
) -> tuple[bytes, bytes, bool, bool, ControlSignal | None]:
    latch = (
        control_latch
        if type(control_latch) is TlsControlLatch
        else TlsControlLatch(initial_control)
    )
    if control_latch is not None and initial_control is not None:
        latch.record(initial_control)
    if type(workspace) is not RelayArtifactWorkspace:
        return b"", b"", False, False, latch.value()
    with workspace._operation_lock:
        if not workspace._prepared or workspace._invalid or workspace._complete:
            return b"", b"", False, False, latch.value()
        if workspace._snapshot is None and not _capture_snapshot(
            workspace,
            latch,
            validation=True,
        ):
            return b"", b"", False, False, latch.value()
        if not _hook("artifact-graph-captured", latch):
            return b"", b"", False, False, latch.value()
        return (*_captured_values(workspace), True, latch.value())


def _capture_snapshot(
    workspace: RelayArtifactWorkspace,
    latch: TlsControlLatch,
    *,
    validation: bool,
) -> bool:
    playwright = workspace._playwright_binding
    run = workspace._run_binding
    if playwright is None or run is None:
        return False
    names = list_names(playwright.descriptor, latch)
    if names is None:
        return False
    structural = bool(
        validation
        and named_directory_matches(
            run.descriptor,
            "playwright",
            workspace._playwright_claim,
            latch,
        )
        and set(names)
        == {
            "relay-ephemeral-output",
            "report.json",
            "voice-pipecat-rtc-result.json",
        }
    )
    budget = ArtifactBudget()
    nodes: list[ArtifactNode] = []
    result: ArtifactNode | None = None
    report: ArtifactNode | None = None
    log_nodes: list[ArtifactNode] = []
    snapshot: ArtifactSnapshot | None = None
    try:
        for name in names:
            borrowed = None
            ephemeral = workspace._ephemeral_binding
            if (
                name == "relay-ephemeral-output"
                and ephemeral is not None
                and named_directory_matches(
                    playwright.descriptor,
                    name,
                    ephemeral.identity,
                    latch,
                )
            ):
                borrowed = ephemeral.descriptor
            limit = None
            if validation and name == "voice-pipecat-rtc-result.json":
                limit = MAX_RESULT_BYTES
            elif validation and name == "report.json":
                limit = MAX_REPORT_BYTES
            if not capture_node(
                playwright.descriptor,
                name,
                budget,
                latch,
                sink=lambda value: _append_captured_node(nodes, value),
                depth=0,
                existing_directory_descriptor=borrowed,
                read_limit=limit,
            ):
                return False
            node = nodes[-1]
            structural = bool(structural and node.valid)
            if name == "voice-pipecat-rtc-result.json":
                result = node
                structural = bool(structural and exact_private_file(node))
            elif name == "report.json":
                report = node
                structural = bool(structural and exact_private_file(node))
            elif name == "relay-ephemeral-output":
                structural = bool(structural and stat.S_ISDIR(node.details.st_mode))
            else:
                structural = False
        log_status = capture_optional_log(
            run.descriptor,
            budget,
            latch,
            sink=lambda value: _append_captured_node(log_nodes, value),
        )
        if log_status == "error":
            return False
        log = log_nodes[0] if log_status == "found" else None
        if log is not None:
            structural = bool(structural and log.valid)
        structural = bool(structural and result is not None and report is not None)
        snapshot = ArtifactSnapshot(
            nodes=nodes,
            result=result,
            report=report,
            log=log,
            structural=structural,
        )
        if not _sink_value(workspace, "_snapshot", snapshot, "snapshot", latch):
            return False
        workspace._rescan_required = False
        return True
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return workspace._snapshot is snapshot and snapshot is not None
    except BaseException as error:
        scrub_exception(error)
        return False
    finally:
        if snapshot is None or workspace._snapshot is not snapshot:
            close_unpublished_nodes(nodes, latch)
            close_unpublished_nodes(log_nodes, latch)


def _discard_relay_artifact_contents(workspace: RelayArtifactWorkspace) -> None:
    snapshot = workspace._snapshot if type(workspace) is RelayArtifactWorkspace else None
    if snapshot is None:
        return
    for node in snapshot.nodes:
        clear_tree_content(node)
    if snapshot.log is not None:
        clear_tree_content(snapshot.log)


def _append_captured_node(nodes: list[ArtifactNode], node: ArtifactNode) -> bool:
    if all(current is not node for current in nodes):
        nodes.append(node)
    return True


def _open_binding_candidate(
    workspace: RelayArtifactWorkspace,
    parent_fd: int,
    name: str,
    identity: tuple[int, int],
    phase: str,
    binding_attribute: str,
    latch: TlsControlLatch,
) -> tuple[str, bool]:
    status = "error"
    hook_ok = True
    try:
        status = _open_owned_directory(
            parent_fd,
            name,
            identity,
            latch,
            sink=lambda descriptor, observed: _retain_directory_binding(
                workspace,
                binding_attribute,
                descriptor,
                observed,
                latch,
            ),
        )
        hook_ok = _hook(f"{phase}-open-returned", latch)
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
    except BaseException as error:
        scrub_exception(error)
        hook_ok = False
    return status, hook_ok


def _open_owned_directory(
    parent_fd: int,
    expected_name: str,
    identity: tuple[int, int],
    latch: TlsControlLatch,
    *,
    sink: Callable[[int, tuple[int, int]], bool],
) -> str:
    names = list_names(parent_fd, latch)
    if names is None:
        return "error"
    ordered = (expected_name, *(name for name in names if name != expected_name))
    matches: list[str] = []
    for name in ordered:
        status, details = stat_name_status(parent_fd, name, latch)
        if status == "error":
            return "error"
        if details is not None and file_identity(details) == identity:
            matches.append(name)
    if not matches:
        return "absent"
    if len(matches) != 1:
        return "error"
    opened = open_child_directory(
        parent_fd,
        matches[0],
        latch,
        sink=sink,
    )
    return "opened" if opened else "error"


def _captured_values(workspace: RelayArtifactWorkspace) -> tuple[bytes, bytes, bool]:
    snapshot = workspace._snapshot
    if snapshot is None:
        return b"", b"", False
    return (
        b"" if snapshot.result is None else snapshot.result.content,
        b"" if snapshot.report is None else snapshot.report.content,
        snapshot.structural,
    )


def _hook(phase: str, latch: TlsControlLatch) -> bool:
    try:
        _artifact_boundary_hook(phase)
        return True
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        scrub_exception(error)
        return True
    except BaseException as error:
        scrub_exception(error)
        return False


def _valid_paths(paths: StackPaths) -> bool:
    run_dir = paths.run_dir
    return bool(
        run_dir.is_absolute()
        and paths.playwright_dir == run_dir / "playwright"
        and paths.browser_result == paths.playwright_dir / "voice-pipecat-rtc-result.json"
        and paths.playwright_report == paths.playwright_dir / "report.json"
    )


def _artifact_boundary_hook(_phase: str) -> None:
    """Secret-free deterministic seam for lifecycle control tests."""

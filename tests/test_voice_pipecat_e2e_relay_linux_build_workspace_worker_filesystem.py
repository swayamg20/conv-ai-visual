"""Focused local-filesystem tests for the private workspace worker transaction."""
# ruff: noqa: E402

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active as active_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_cleanup as fs_cleanup
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_contract as fs_contract
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_copy as fs_copy
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_open as fs_open
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_fs_transaction as fs_transaction
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_lifecycle as lifecycle_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_release as release_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as thread_module


def _valid_graph(tmp_path: Path, run_id: str = "filesystem-worker"):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    for name in workspace_module._SOURCE_ENTRIES:
        target = source / name
        if name in workspace_module._SOURCE_DIRECTORY_ENTRIES:
            target.mkdir(mode=0o700)
            (target / "fixture.txt").write_bytes(f"{name}\n".encode())
        else:
            target.write_bytes(f"{name}\n".encode())
    node_modules = source / "node_modules"
    (node_modules / "next" / "dist" / "bin").mkdir(parents=True, mode=0o700)
    (node_modules / "next" / "package.json").write_bytes(b'{"name":"next"}\n')
    next_cli = node_modules / "next" / "dist" / "bin" / "next"
    next_cli.write_bytes(b"#!/usr/bin/env node\n")
    next_cli.chmod(0o700)
    (node_modules / ".package-lock.json").write_bytes(b'{"lockfileVersion":3}\n')
    node = tmp_path / "node"
    node.write_bytes(b"synthetic-node\n")
    node.chmod(0o700)
    run_parent = tmp_path / "runs"
    run_parent.mkdir(mode=0o700)
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=source.resolve(),
        run_parent=run_parent.resolve(),
        node=node.resolve(),
        run_id=run_id,
    )
    owner = destination._read(destination._request)
    bundle = state_module._new_relay_linux_build_workspace_worker_bundle(owner)
    construction, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    assert construction is not None and coherent is True
    return owner, bundle, construction


def _start_prepared(owner: object, bundle: object, construction: object):
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    receipt = owner._receipt_destination._read(owner._request)
    assert start is not None and coherent is True
    assert type(receipt) is fs_contract._WorkspacePreparedReceipt
    assert receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    return receipt


def _cancel_join_release(owner: object, bundle: object, construction: object):
    thread_module._cancel_relay_linux_build_workspace_worker(owner, bundle, construction)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    return terminal


def test_worker_prepares_holds_and_cleans_exact_workspace(tmp_path: Path) -> None:
    owner, bundle, construction = _valid_graph(tmp_path)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    receipt = owner._receipt_destination._read(owner._request)

    assert start is not None and coherent is True
    assert type(receipt) is fs_contract._WorkspacePreparedReceipt
    assert receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    assert owner._request._run_root.is_dir()
    assert stat.S_IMODE(owner._request._run_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(owner._request._workspace.stat().st_mode) == 0o700
    copied = owner._request._workspace / "package.json"
    assert copied.read_bytes() == (owner._request._source_root / "package.json").read_bytes()
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    link = owner._request._workspace / "node_modules"
    assert link.is_symlink() and os.readlink(link) == str(owner._request._node_modules)

    thread_module._cancel_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )

    assert terminal is not None and terminal.started is True and joined is True
    assert not owner._request._run_root.exists()
    assert not receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("kind", ["fifo", "symlink", "hardlink"])
def test_unsafe_source_node_fails_without_a_prepared_receipt(
    tmp_path: Path,
    kind: str,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, f"reject-{kind}")
    target = owner._request._source_root / "src" / "fixture.txt"
    target.unlink()
    if kind == "fifo":
        os.mkfifo(target, 0o600)
    elif kind == "symlink":
        target.symlink_to(owner._request._source_root / "package.json")
    else:
        os.link(owner._request._source_root / "package.json", target)

    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 1.0,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        1.0,
    )

    assert start is not None and coherent is True
    assert terminal is not None and joined is True
    assert owner._receipt_destination._read(owner._request) is None
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_prepared_publish_store_return_loss_recovers_to_stable_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "prepared-store-loss")
    original = workspace_module._WorkspacePreparationReceiptDestination._publish_before
    lost = False

    def publish_then_raise(self: object, *args: object):
        nonlocal lost
        result = original(self, *args)
        if not lost:
            lost = True
            raise OSError("synthetic prepared publication return loss")
        return result

    monkeypatch.setattr(
        workspace_module._WorkspacePreparationReceiptDestination,
        "_publish_before",
        publish_then_raise,
    )
    receipt = _start_prepared(owner, bundle, construction)
    time.sleep(0.05)

    assert lost is True
    assert owner._request._run_root.is_dir()
    assert receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    _cancel_join_release(owner, bundle, construction)
    assert not owner._request._run_root.exists()


def test_prepared_publish_control_never_exposes_an_active_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "prepared-control-loss")
    original = workspace_module._WorkspacePreparationReceiptDestination._publish_before
    lost = False

    def publish_then_interrupt(self: object, *args: object):
        nonlocal lost
        result = original(self, *args)
        if not lost:
            lost = True
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(
        workspace_module._WorkspacePreparationReceiptDestination,
        "_publish_before",
        publish_then_interrupt,
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    stored = owner._receipt_destination._read(owner._request)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )

    assert start is not None and coherent is True and lost is True
    assert type(stored) is fs_contract._WorkspacePreparedReceipt
    assert not stored._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("store_first", [False, True], ids=("pre-store", "post-store"))
@pytest.mark.parametrize("control", [False, True], ids=("ordinary", "control"))
def test_settlement_publication_loss_retries_to_canonical_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_first: bool,
    control: bool,
) -> None:
    owner, bundle, construction = _valid_graph(
        tmp_path,
        f"settle-{int(store_first)}-{int(control)}",
    )
    _start_prepared(owner, bundle, construction)
    original = fs_transaction._publish_workspace_filesystem_settlement
    calls = 0

    def lose_once(*args: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            if store_first:
                original(*args)
            if control:
                raise SystemExit(71)
            raise OSError("synthetic settlement publication loss")
        return original(*args)

    monkeypatch.setattr(
        fs_transaction,
        "_publish_workspace_filesystem_settlement",
        lose_once,
    )
    terminal = _cancel_join_release(owner, bundle, construction)

    assert calls >= 2
    assert terminal.started is True
    assert not owner._request._run_root.exists()
    assert fs_contract._workspace_filesystem_state_is_forgotten(construction._record_token)


@pytest.mark.parametrize("stage", ["pre", "mid", "post"])
@pytest.mark.parametrize("control", [False, True], ids=("ordinary", "control"))
def test_release_forget_loss_retries_through_missing_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    control: bool,
) -> None:
    owner, bundle, construction = _valid_graph(
        tmp_path,
        f"forget-{stage}-{'c' if control else 'o'}",
    )
    _start_prepared(owner, bundle, construction)
    thread_module._cancel_relay_linux_build_workspace_worker(owner, bundle, construction)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    original = fs_contract._forget_workspace_filesystem_settlement
    calls = 0

    def lose_once(record_token: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            if stage == "mid":
                fs_contract._SETTLEMENTS.pop(record_token, None)
            elif stage == "post":
                original(record_token)
            if control:
                raise KeyboardInterrupt()
            raise OSError("synthetic settlement-forget loss")
        original(record_token)

    monkeypatch.setattr(fs_contract, "_forget_workspace_filesystem_settlement", lose_once)

    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert calls >= 2
    assert bundle not in registry_module._RECORDS
    assert fs_contract._workspace_filesystem_state_is_forgotten(construction._record_token)


@pytest.mark.parametrize("level", ["run", "workspace"])
def test_directory_helper_return_loss_still_removes_exact_created_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    level: str,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, f"mkdir-loss-{level}")
    original = fs_transaction._create_directory_at
    lost = False

    def create_then_raise(*args: object, **kwargs: object):
        nonlocal lost
        result = original(*args, **kwargs)
        name = args[1]
        target = owner._request._run_root.name if level == "run" else owner._request._workspace.name
        if not lost and name == target:
            lost = True
            raise OSError("synthetic create return loss")
        return result

    monkeypatch.setattr(fs_transaction, "_create_directory_at", create_then_raise)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )

    assert start is not None and coherent is True and lost is True
    assert terminal is not None and joined is True
    assert owner._receipt_destination._read(owner._request) is None
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("level", ["run", "workspace"])
def test_eexist_collision_is_never_adopted_or_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    level: str,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, f"eexist-{level}")
    original = fs_open.os.mkdir
    target = owner._request._run_root.name if level == "run" else owner._request._workspace.name
    raced = False

    def collide(name: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        nonlocal raced
        if not raced and name == target:
            raced = True
            original(name, mode, dir_fd=dir_fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
            try:
                marker = os.open(
                    "foreign-marker",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=child,
                )
                os.close(marker)
            finally:
                os.close(child)
            raise FileExistsError(17, "synthetic collision", name)
        original(name, mode, dir_fd=dir_fd)

    monkeypatch.setattr(fs_open.os, "mkdir", collide)
    _start, _coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.3,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.1,
    )
    collided = owner._request._run_root
    if level == "workspace":
        collided = owner._request._workspace

    assert raced is True and terminal is None and joined is False
    assert (collided / "foreign-marker").is_file()
    (collided / "foreign-marker").unlink()
    collided.rmdir()
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("level", ["workspace", "run"])
def test_directory_remove_return_loss_reconciles_exact_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    level: str,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, f"rmdir-loss-{level}")
    _start_prepared(owner, bundle, construction)
    original = fs_cleanup.os.rmdir
    target = (
        owner._request._workspace.name if level == "workspace" else owner._request._run_root.name
    )
    lost = False

    def remove_then_raise(name: str, *, dir_fd: int | None = None) -> None:
        nonlocal lost
        original(name, dir_fd=dir_fd)
        if not lost and name == target:
            lost = True
            raise OSError("synthetic rmdir return loss")

    monkeypatch.setattr(fs_cleanup.os, "rmdir", remove_then_raise)
    _cancel_join_release(owner, bundle, construction)
    assert lost is True
    assert not owner._request._run_root.exists()


def test_parent_fsync_failure_retries_after_exact_root_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "parent-fsync-loss")
    _start_prepared(owner, bundle, construction)
    original = fs_cleanup.os.fsync
    calls = 0

    def fail_once(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic parent fsync loss")
        original(descriptor)

    monkeypatch.setattr(fs_cleanup.os, "fsync", fail_once)
    _cancel_join_release(owner, bundle, construction)
    assert calls >= 2
    assert not owner._request._run_root.exists()


def test_receipt_tamper_cannot_resurrect_or_skip_cleanup(tmp_path: Path) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "receipt-tamper")
    receipt = _start_prepared(owner, bundle, construction)
    object.__setattr__(receipt, "_lease_active", True)
    object.__setattr__(receipt, "_owner_token", object())
    _cancel_join_release(owner, bundle, construction)

    assert fs_contract._workspace_prepared_receipt_is_revoked(
        receipt,
        owner._cleanup_authority._key,
        construction._record_token,
    )
    assert not owner._request._run_root.exists()


def test_fake_revoke_success_cannot_clean_or_terminalize_active_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "fake-revoke")
    receipt = _start_prepared(owner, bundle, construction)
    original = fs_transaction._revoke_workspace_prepared_receipt
    monkeypatch.setattr(
        fs_transaction,
        "_revoke_workspace_prepared_receipt",
        lambda *_args: True,
    )
    thread_module._cancel_relay_linux_build_workspace_worker(owner, bundle, construction)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.15,
    )

    assert terminal is None and joined is False
    assert receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    )
    assert owner._request._run_root.is_dir()
    monkeypatch.setattr(fs_transaction, "_revoke_workspace_prepared_receipt", original)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


@pytest.mark.parametrize("store_first", [False, True], ids=("pre-store", "post-store"))
@pytest.mark.parametrize("control", [False, True], ids=("ordinary", "control"))
def test_claim_marker_publication_loss_is_reconciled_before_worker_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_first: bool,
    control: bool,
) -> None:
    owner, bundle, construction = _valid_graph(
        tmp_path,
        f"claim-{int(store_first)}-{int(control)}",
    )
    original = fs_contract._publish_workspace_filesystem_claim
    calls = 0

    def lose_once(*args: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            if store_first:
                original(*args)
            if control:
                raise KeyboardInterrupt()
            raise OSError("synthetic claim publication loss")
        return original(*args)

    monkeypatch.setattr(fs_contract, "_publish_workspace_filesystem_claim", lose_once)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    receipt = owner._receipt_destination._read(owner._request)
    if type(receipt) is fs_contract._WorkspacePreparedReceipt and receipt._matches(
        owner._cleanup_authority._key,
        construction._record_token,
        require_active=True,
    ):
        thread_module._cancel_relay_linux_build_workspace_worker(
            owner,
            bundle,
            construction,
        )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )

    assert start is not None and type(coherent) is bool and calls == 1
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_claim_transfer_retries_transient_active_root_lock_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "claim-root-lock")
    original = fs_contract._publish_workspace_filesystem_claim
    held = threading.Event()
    release = threading.Event()
    holder: threading.Thread | None = None

    def publish_and_hold(*args: object) -> bool:
        nonlocal holder

        def hold_root() -> None:
            with active_module._ROOTS_LOCK:
                held.set()
                assert release.wait(1.0)

        holder = threading.Thread(target=hold_root)
        holder.start()
        assert held.wait(1.0)
        threading.Timer(0.12, release.set).start()
        return original(*args)

    monkeypatch.setattr(fs_contract, "_publish_workspace_filesystem_claim", publish_and_hold)
    _start_prepared(owner, bundle, construction)
    assert holder is not None
    holder.join(1.0)
    assert not holder.is_alive()
    _cancel_join_release(owner, bundle, construction)


def test_claim_validation_retries_transient_registry_lock_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "claim-registry-lock")
    held = threading.Event()
    release = threading.Event()
    holder: threading.Thread | None = None

    def claim_taken(_claim: object) -> None:
        nonlocal holder

        def hold_registry() -> None:
            with registry_module._REGISTRY_LOCK:
                held.set()
                assert release.wait(1.0)

        holder = threading.Thread(target=hold_registry)
        holder.start()
        assert held.wait(1.0)
        threading.Timer(0.12, release.set).start()

    monkeypatch.setattr(lifecycle_module, "_workspace_worker_claim_taken", claim_taken)
    _start_prepared(owner, bundle, construction)
    assert holder is not None
    holder.join(1.0)
    assert not holder.is_alive()
    _cancel_join_release(owner, bundle, construction)


def test_source_recursive_device_crossing_is_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "source-device")
    original = fs_copy._require_cooperative_node
    changed = False

    def model_cross_device(descriptor: int, *, directory: bool, executable: bool = False):
        nonlocal changed
        identity = original(descriptor, directory=directory, executable=executable)
        if directory and not changed:
            changed = True
            return fs_contract._WorkspaceFilesystemIdentity(
                identity.device + 1,
                identity.inode,
                identity.mode,
                identity.links,
                identity.size,
                identity.modified_ns,
                identity.changed_ns,
            )
        return identity

    monkeypatch.setattr(fs_copy, "_require_cooperative_node", model_cross_device)
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 2.0,
    )
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )

    assert start is not None and coherent is True and changed is True
    assert owner._receipt_destination._read(owner._request) is None
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_cleanup_recursive_device_crossing_retains_authority_until_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "cleanup-device")
    _start_prepared(owner, bundle, construction)
    target_inode = (owner._request._workspace / "package.json").stat().st_ino
    original_identity = fs_contract._WorkspaceFilesystemIdentity

    class ModeledIdentity:
        @classmethod
        def from_stat(cls, details: object):
            identity = original_identity.from_stat(details)
            if identity.inode == target_inode:
                return original_identity(
                    identity.device + 1,
                    identity.inode,
                    identity.mode,
                    identity.links,
                    identity.size,
                    identity.modified_ns,
                    identity.changed_ns,
                )
            return identity

    monkeypatch.setattr(fs_cleanup, "_WorkspaceFilesystemIdentity", ModeledIdentity)
    thread_module._cancel_relay_linux_build_workspace_worker(owner, bundle, construction)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.15,
    )
    assert terminal is None and joined is False
    assert owner._request._run_root.is_dir()
    monkeypatch.setattr(fs_cleanup, "_WorkspaceFilesystemIdentity", original_identity)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_ambiguous_close_never_retries_reused_same_inode_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "descriptor"
    target.write_bytes(b"same inode\n")
    descriptors = fs_open._WorkspaceDescriptorSet()
    descriptor = descriptors.adopt(os.open(target, os.O_RDONLY))
    original_close = fs_open._OS_CLOSE
    reused: int | None = None
    calls = 0

    def close_reopen_raise(candidate: int) -> None:
        nonlocal calls, reused
        calls += 1
        original_close(candidate)
        reused = os.open(target, os.O_RDONLY)
        assert reused == candidate
        raise OSError("synthetic ambiguous close")

    monkeypatch.setattr(fs_open, "_OS_CLOSE", close_reopen_raise)
    with pytest.raises(OSError, match="ambiguous close"):
        descriptors.close(descriptor)

    assert descriptors.close_all() is False
    assert calls == 1 and reused == descriptor
    assert os.fstat(reused).st_ino == target.stat().st_ino
    original_close(reused)


def test_persistent_cleanup_failure_uses_capped_backoff_without_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "cleanup-backoff")
    _start_prepared(owner, bundle, construction)
    original_cleanup = fs_transaction._cleanup_workspace_root
    original_wait = state_module._WorkspaceWorkerController._wait
    waits: list[float] = []
    armed = True

    def fail_cleanup(_state: object) -> bool:
        raise OSError("synthetic persistent cleanup failure")

    def record_wait(self: object, timeout: float) -> None:
        if armed:
            waits.append(timeout)
        original_wait(self, timeout)

    monkeypatch.setattr(fs_transaction, "_cleanup_workspace_root", fail_cleanup)
    monkeypatch.setattr(state_module._WorkspaceWorkerController, "_wait", record_wait)
    thread_module._cancel_relay_linux_build_workspace_worker(owner, bundle, construction)
    time.sleep(0.22)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        0.01,
    )

    assert terminal is None and joined is False
    assert owner._request._run_root.is_dir()
    assert waits[:4] == [0.01, 0.02, 0.04, 0.05]
    assert len(waits) <= 8 and max(waits) == 0.05
    armed = False
    monkeypatch.setattr(fs_transaction, "_cleanup_workspace_root", original_cleanup)
    terminal, joined = thread_module._join_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        2.0,
    )
    assert terminal is not None and joined is True
    assert not owner._request._run_root.exists()
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )


def test_installed_terminal_cannot_release_without_canonical_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle, construction = _valid_graph(tmp_path, "terminal-tamper")
    monkeypatch.setattr(
        fs_transaction,
        "_run_workspace_filesystem_transaction",
        lambda _claim: False,
    )
    start, coherent = thread_module._start_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        time.monotonic() + 0.3,
    )
    raw = registry_module._RECORDS[bundle]._entry[1]
    release_module._THREAD_JOIN(raw, 1.0)
    assert start is not None and coherent is False
    assert not release_module._THREAD_IS_ALIVE(raw)
    coordinator = bundle._lifecycle
    terminal = lifecycle_module._WorkspaceWorkerTerminalReceipt(
        lifecycle_module._TERMINAL_TOKEN,
        owner_token=owner._cleanup_authority._key,
        record_token=construction._record_token,
        started=True,
    )
    bundle._terminal_destination._publish(
        state_module._DESTINATION_TOKEN,
        owner._cleanup_authority._key,
        terminal,
    )
    object.__setattr__(coordinator, "_terminal", terminal)
    object.__setattr__(coordinator, "_phase", "terminal")
    object.__setattr__(coordinator, "_joined", True)
    object.__setattr__(coordinator, "_workspace_settled", True)

    assert not fs_contract._workspace_filesystem_is_settled(
        owner._cleanup_authority._key,
        construction._record_token,
        coordinator._claim_token,
    )
    assert not thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )
    assert bundle in registry_module._RECORDS

    assert fs_contract._publish_workspace_filesystem_settlement(
        owner._cleanup_authority._key,
        construction._record_token,
        coordinator._claim_token,
    )
    assert thread_module._release_relay_linux_build_workspace_worker(
        owner,
        bundle,
        construction,
        terminal,
    )

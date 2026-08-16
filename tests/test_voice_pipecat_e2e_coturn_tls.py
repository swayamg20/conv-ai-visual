"""Synthetic exclusive-file and TLS/SPKI tests; no OpenSSL is executed."""

from __future__ import annotations

import base64
import errno
import hashlib
import os
import signal
import stat
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_tls as tls_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_file_cleanup as cleanup_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_file_owner as owner_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_file_reader as reader_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_lifetime as lifetime_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_private as private_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_readiness as readiness_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_receipt as receipt_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_worker as worker_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    COTURN_FIXTURE_PATH,
    CoturnBridgeTopology,
    read_private_coturn_configuration_receipt,
)
from scripts.voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CoturnTlsError,
    CoturnTlsPrivateCleanupRequired,
    OpenSslReadinessReceipt,
    TlsMaterialReceipt,
    build_openssl_readiness_request,
    cleanup_tls_material,
    cleanup_tls_material_authority,
    cleanup_tls_private_authority,
    generate_tls_and_config_material,
    read_owned_file,
    validate_openssl_readiness_result,
    validate_tls_material,
    write_owned_file_exclusive,
)
from tests.coturn_tls_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn import TEST_CERTIFICATE_PEM  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_host import (  # noqa: E402
    QueueRunner,
    _paths,
    _result,
    _tools,
)

SECRET = "0123456789abcdef" * 4
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
TOPOLOGY = CoturnBridgeTopology.parse(
    network="172.28.44.0/29",
    gateway="172.28.44.1",
    container="172.28.44.2",
)
PRIVATE_KEY = b"-----BEGIN PRIVATE KEY-----\nQUJD\n-----END PRIVATE KEY-----\n"
PUBLIC_KEY = b"-----BEGIN PUBLIC KEY-----\nQUJD\n-----END PUBLIC KEY-----\n"
CERTIFICATE = TEST_CERTIFICATE_PEM.encode("ascii")
DER_SPKI = b"\x30\x05murmur-spki-vector"
DATES = b"notBefore=Aug 16 11:59:00 2026 GMT\nnotAfter=Aug 17 11:59:00 2026 GMT\n"
SAN = b"X509v3 Subject Alternative Name: critical\n    IP Address:127.0.0.1\n"


def _clear_synthetic_close_quarantine(*descriptors: int) -> None:
    """Test-only proof that a synthetic close exception happened before close(2)."""

    with cleanup_module._AMBIGUOUS_LOCK:
        for descriptor in descriptors:
            cleanup_module._AMBIGUOUS_DESCRIPTORS.pop(descriptor, None)


def _readiness_transcript(
    protocol: str,
    cipher: str,
    *,
    final_newline: bool = True,
) -> bytes:
    lines = [
        "CONNECTION ESTABLISHED",
        f"Protocol version: {protocol}",
        f"Ciphersuite: {cipher}",
        "Peer certificate: CN = murmur-coturn-loopback.invalid",
        "Hash used: SHA256",
        "Signature type: RSA-PSS",
        "Verification: OK",
    ]
    if protocol == "TLSv1.2":
        lines.append("Supported Elliptic Curve Point Formats: uncompressed")
    lines.extend(("Server Temp Key: X25519, 253 bits", "DONE"))
    return ("\n".join(lines) + ("\n" if final_newline else "")).encode("ascii")


def _tls_results(*, private_public: bytes = PUBLIC_KEY):
    return [
        _result(DATES),
        _result(SAN),
        _result(PUBLIC_KEY),
        _result(private_public),
        _result(DER_SPKI),
    ]


def test_owned_file_io_rejects_modes_symlinks_and_hardlinks(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    target = paths.contract.coturn_dir / "owned.bin"
    write_owned_file_exclusive(target, b"bounded", mode=0o400, maximum=16)
    assert read_owned_file(target, exact_mode=0o400, maximum=16) == b"bounded"
    target.chmod(0o600)
    with pytest.raises(CoturnTlsError, match="unavailable"):
        read_owned_file(target, exact_mode=0o400, maximum=16)
    target.chmod(0o400)
    os.link(target, paths.contract.coturn_dir / "hardlink.bin")
    with pytest.raises(CoturnTlsError, match="unavailable"):
        read_owned_file(target, exact_mode=0o400, maximum=16)
    target.unlink()
    target.symlink_to(paths.contract.cert)
    with pytest.raises(CoturnTlsError, match="creation failed"):
        write_owned_file_exclusive(target, b"x", mode=0o400, maximum=16)


def test_exclusive_write_removes_final_validation_failure_and_surfaces_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "final-check.bin"
    real_stat = os.stat
    named_stats = 0

    def unsafe_final_stat(path: object, *args: object, **kwargs: object) -> object:
        nonlocal named_stats
        details = real_stat(path, *args, **kwargs)
        if path == target.name and kwargs.get("dir_fd") is not None:
            named_stats += 1
        if path == target.name and named_stats == 2:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=7,
                st_dev=details.st_dev,
                st_ino=details.st_ino,
            )
        return details

    monkeypatch.setattr(os, "stat", unsafe_final_stat)
    with pytest.raises(CoturnTlsError, match="creation failed"):
        write_owned_file_exclusive(target, b"bounded", mode=0o400, maximum=16)
    assert not target.exists()

    leftover = paths.control_dir / "write-failure.bin"
    real_unlink = os.unlink
    monkeypatch.setattr(os, "write", lambda _fd, _value: 0)

    def refuse_unlink(path: object, *args: object, **kwargs: object) -> None:
        if path == leftover.name and kwargs.get("dir_fd") is not None:
            raise OSError("refused")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse_unlink)
    try:
        with pytest.raises(CoturnTlsError, match=r"^Coturn private file cleanup failed$"):
            write_owned_file_exclusive(leftover, b"bounded", mode=0o400, maximum=16)
        assert leftover.exists()
    finally:
        try:
            real_unlink(leftover)
        except FileNotFoundError:
            pass


def test_exclusive_write_rejects_swap_and_preserves_replacement_victim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "inode-bound.bin"
    replacement = paths.control_dir / "replacement.bin"
    replacement.write_bytes(b"swapped")
    replacement.chmod(0o400)
    real_close = os.close
    swapped = False

    def swap_after_close(descriptor: int) -> None:
        nonlocal swapped
        real_close(descriptor)
        if not swapped:
            swapped = True
            os.replace(replacement, target)

    monkeypatch.setattr(os, "close", swap_after_close)
    with pytest.raises(CoturnTlsError, match=r"^Coturn private file creation failed$"):
        write_owned_file_exclusive(target, b"bounded", mode=0o400, maximum=16)
    assert swapped
    assert target.read_bytes() == b"swapped"
    assert not replacement.exists()


def test_generation_writes_0400_material_and_derives_chromium_der_spki(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runner = QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()])
    receipt = generate_tls_and_config_material(
        runner=runner,
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    authority = receipt._lifetime
    assert authority is not None
    directory_fds = tuple(
        slot._directory_fd for slot in authority._receipts if type(slot._directory_fd) is int
    )
    file_fds = tuple(slot._file_fd for slot in authority._receipts if type(slot._file_fd) is int)
    try:
        expected_pin = base64.b64encode(hashlib.sha256(DER_SPKI).digest()).decode("ascii")
        assert receipt.chromium_spki_sha256_b64 == expected_pin
        assert len(expected_pin) == 44 and expected_pin.endswith("=")
        assert receipt.certificate_sha256 == hashlib.sha256(CERTIFICATE).hexdigest()
        assert repr(receipt) == "TlsMaterialReceipt()" and expected_pin not in repr(receipt)
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o400
            for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
        )
        config = read_private_coturn_configuration_receipt(
            paths.contract.config,
            expected_run_dir=paths.contract.run_dir,
        )
        assert (config.static_auth_secret, config.topology) == (SECRET, TOPOLOGY)
        assert runner.requests[-1].argv[-3:] == ("-pubin", "-outform", "DER")
        assert all(request.stdin == b"" for request in runner.requests)
        assert all(
            request.environment == (("LANG", "C"), ("LC_ALL", "C")) for request in runner.requests
        )
        fixture = COTURN_FIXTURE_PATH.read_text()
        assert fixture.count("verbose") == fixture.count("log-min-level=info") == 1
        assert receipt.has_cleanup_authority
        assert len(directory_fds) == 3 and len(set(directory_fds)) == 3
        assert len(file_fds) == 3 and len(set((*directory_fds, *file_fds))) == 6
        probe = RuntimeError("fixed TLS receipt graph")
        probe.receipt = receipt  # type: ignore[attr-defined]
        probe.runner = runner  # type: ignore[attr-defined]
        assert not traceback_contains(probe, SECRET, PRIVATE_KEY, CERTIFICATE)
    finally:
        if receipt.has_cleanup_authority:
            cleanup_tls_material(receipt)
    assert not receipt.has_cleanup_authority
    for descriptor in (*directory_fds, *file_fds):
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS cleanup failed$"):
        cleanup_tls_material(receipt)


def test_genpkey_is_quiet_and_unexpected_progress_stderr_is_rejected_without_reflection(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    progress = b"..+...+private-looking-progress"
    runner = QueueRunner([_result(PRIVATE_KEY, stderr=progress)])
    with pytest.raises(CoturnTlsError, match=r"^Coturn private-key generation failed$") as captured:
        generate_tls_and_config_material(
            runner=runner,
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert "-quiet" in runner.requests[0].argv
    assert progress.decode() not in str(captured.value)
    assert progress.decode() not in repr(runner.requests[0])


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_caller_control_during_success_handoff_rolls_back_exact_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    paths = _paths(tmp_path)
    original = TlsMaterialReceipt.has_cleanup_authority.fget
    injected = False

    def interrupt_handoff(receipt: TlsMaterialReceipt) -> bool:
        nonlocal injected
        available = original(receipt)
        if available and not injected:
            injected = True
            if control is KeyboardInterrupt:
                raise KeyboardInterrupt
            raise SystemExit(exit_code)
        return available

    monkeypatch.setattr(
        TlsMaterialReceipt,
        "has_cleanup_authority",
        property(interrupt_handoff),
    )
    with pytest.raises(control) as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert injected and getattr(captured.value, "code", None) == exit_code
    assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
@pytest.mark.parametrize(
    "phase",
    ("slot-return", "writer-entry", "owner-start", "task-constructor"),
)
def test_precreate_control_settles_transactional_slot_without_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
    phase: str,
) -> None:
    paths = _paths(tmp_path)

    def interrupt() -> None:
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    if phase == "slot-return":
        original_slot = tls_module.TlsMaterialLifetimeAuthority.new_slot

        def append_then_interrupt(authority: object) -> object:
            slot = original_slot(authority)  # type: ignore[arg-type]
            assert slot.unsubmitted
            interrupt()
            return slot

        monkeypatch.setattr(
            tls_module.TlsMaterialLifetimeAuthority,
            "new_slot",
            append_then_interrupt,
        )
    elif phase == "writer-entry":

        def interrupt_before_writer(*_args: object, **_kwargs: object) -> object:
            interrupt()

        monkeypatch.setattr(
            tls_module,
            "write_owned_file_exclusive_tracked",
            interrupt_before_writer,
        )
    elif phase == "owner-start":

        def return_start_control(_service: object) -> tuple[bool, object]:
            return False, (control, exit_code)

        monkeypatch.setattr(owner_module, "start_tls_owner_service", return_start_control)
    else:

        def interrupt_task_constructor(*_args: object, **_kwargs: object) -> object:
            interrupt()

        monkeypatch.setattr(owner_module, "_WriteTask", interrupt_task_constructor)

    with pytest.raises(control) as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert getattr(captured.value, "code", None) == exit_code
    assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_created_file_removal_failure_retains_exact_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    real_unlink = os.unlink
    real_write = os.write
    write_failed = False
    authority = None
    moved = paths.contract.coturn_dir / "failed-write-original.pem"
    victim = b"replacement-victim"

    def fail_first_write(descriptor: int, value: object) -> int:
        nonlocal write_failed
        if not write_failed:
            write_failed = True
            raise OSError
        return real_write(descriptor, value)  # type: ignore[arg-type]

    def fail_key_removal(path: object, *args: object, **kwargs: object) -> None:
        if path == paths.contract.private_key.name and kwargs.get("dir_fd") is not None:
            raise OSError
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "write", fail_first_write)
    monkeypatch.setattr(os, "unlink", fail_key_removal)
    try:
        with pytest.raises(tls_module.CoturnTlsCleanupRequired) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        authority = captured.value.cleanup_authority
        slot = authority._receipts[0]
        directory_fd = slot._directory_fd
        assert write_failed and slot.owned and type(directory_fd) is int
        os.fstat(directory_fd)
        original = paths.contract.private_key.stat(follow_symlinks=False)
        original_identity = (original.st_dev, original.st_ino)
        paths.contract.private_key.rename(moved)
        paths.contract.private_key.write_bytes(victim)
        paths.contract.private_key.chmod(0o400)
        monkeypatch.setattr(os, "unlink", real_unlink)
        cleanup_tls_material_authority(authority)
        assert paths.contract.private_key.read_bytes() == victim
        assert not moved.exists()
        with pytest.raises(OSError) as closed:
            os.fstat(directory_fd)
        assert closed.value.errno == errno.EBADF
        assert all(
            (item.st_dev, item.st_ino) != original_identity
            for item in (
                path.stat(follow_symlinks=False) for path in paths.contract.coturn_dir.iterdir()
            )
        )
    finally:
        monkeypatch.setattr(os, "unlink", real_unlink)
        if authority is not None and authority.active:
            cleanup_tls_material_authority(authority)
        moved.unlink(missing_ok=True)
        paths.contract.private_key.unlink(missing_ok=True)


def test_retained_lifetime_cleanup_removes_renamed_inode_and_preserves_victim(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    receipt = generate_tls_and_config_material(
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    moved = paths.contract.coturn_dir / "retained-renamed-key.pem"
    victim = b"replacement-victim"
    details = paths.contract.private_key.stat(follow_symlinks=False)
    original_identity = (details.st_dev, details.st_ino)
    paths.contract.private_key.rename(moved)
    paths.contract.private_key.write_bytes(victim)
    paths.contract.private_key.chmod(0o400)
    cleanup_tls_material(receipt)
    assert paths.contract.private_key.read_bytes() == victim
    assert not moved.exists()
    assert not paths.contract.cert.exists() and not paths.contract.config.exists()
    assert not receipt.has_cleanup_authority
    assert all(
        (item.st_dev, item.st_ino) != original_identity
        for item in (
            path.stat(follow_symlinks=False) for path in paths.contract.coturn_dir.iterdir()
        )
    )


def test_retained_lifetime_destroys_bytes_moved_outside_bound_directory(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    sibling = paths.contract.run_dir / "same-owner-sibling"
    sibling.mkdir(mode=0o700)
    moved = sibling / "moved-private-key.pem"
    victim = b"replacement-victim"
    receipt = generate_tls_and_config_material(
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    try:
        paths.contract.private_key.rename(moved)
        paths.contract.private_key.write_bytes(victim)
        paths.contract.private_key.chmod(0o400)
        cleanup_tls_material(receipt)
        assert paths.contract.private_key.read_bytes() == victim
        assert moved.exists() and moved.read_bytes() == b""
        assert not paths.contract.cert.exists() and not paths.contract.config.exists()
        assert not receipt.has_cleanup_authority
    finally:
        if receipt.has_cleanup_authority:
            cleanup_tls_material(receipt)
        moved.unlink(missing_ok=True)
        paths.contract.private_key.unlink(missing_ok=True)


def test_cleanup_failure_keeps_success_receipt_authority_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    receipt = generate_tls_and_config_material(
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    real_unlink = os.unlink

    def fail_config_unlink(path: object, *args: object, **kwargs: object) -> None:
        if path == paths.contract.config.name and kwargs.get("dir_fd") is not None:
            raise OSError
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_config_unlink)
    with pytest.raises(tls_module.CoturnTlsCleanupRequired) as captured:
        cleanup_tls_material(receipt)
    assert receipt.has_cleanup_authority
    assert captured.value.cleanup_authority is receipt._lifetime
    monkeypatch.setattr(os, "unlink", real_unlink)
    cleanup_tls_material(receipt)
    assert not receipt.has_cleanup_authority
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


def test_concurrent_cleanup_serializes_receipts_and_preserves_reused_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    receipt = generate_tls_and_config_material(
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    authority = receipt._lifetime
    assert authority is not None
    directory_fds = tuple(slot._directory_fd for slot in authority._receipts)
    unrelated = paths.contract.run_dir / "unrelated-reused-fd.bin"
    unrelated.write_bytes(b"unrelated")
    entered = threading.Event()
    release = threading.Event()
    real_settle = lifetime_module.settle_private_file_receipts_owned
    calls = 0
    reused_fd: int | None = None

    def settle_then_reuse(*args: object, **kwargs: object) -> object:
        nonlocal calls, reused_fd
        calls += 1
        result = real_settle(*args, **kwargs)
        source = os.open(unrelated, os.O_RDONLY)
        target = directory_fds[0]
        if source != target:
            os.dup2(source, target)
            os.close(source)
        reused_fd = target
        entered.set()
        assert release.wait(2)
        return result

    monkeypatch.setattr(
        lifetime_module,
        "settle_private_file_receipts_owned",
        settle_then_reuse,
    )
    outcomes: list[str] = []

    def cleanup() -> None:
        try:
            cleanup_tls_material(receipt)
            outcomes.append("cleaned")
        except CoturnTlsError:
            outcomes.append("already-consumed")

    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=cleanup)
    try:
        first.start()
        assert entered.wait(2)
        second.start()
        second.join(0.05)
        assert second.is_alive()
        release.set()
        first.join(2)
        second.join(2)
        assert not first.is_alive() and not second.is_alive()
        assert calls == 1 and sorted(outcomes) == ["already-consumed", "cleaned"]
        assert reused_fd is not None and os.fstat(reused_fd)
    finally:
        release.set()
        first.join(2)
        second.join(2)
        if reused_fd is not None:
            os.close(reused_fd)
        if receipt.has_cleanup_authority:
            cleanup_tls_material(receipt)


def test_partial_success_handoff_failure_rolls_back_exact_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original = TlsMaterialReceipt._bind_lifetime

    def bind_then_fail(receipt: TlsMaterialReceipt, authority: object) -> bool:
        assert original(receipt, authority)  # type: ignore[arg-type]
        raise RuntimeError("synthetic handoff failure")

    monkeypatch.setattr(TlsMaterialReceipt, "_bind_lifetime", bind_then_fail)
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS material is invalid$") as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


def test_partial_handoff_cleanup_failure_returns_retryable_exact_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original_bind = TlsMaterialReceipt._bind_lifetime
    real_unlink = os.unlink

    def bind_then_fail(receipt: TlsMaterialReceipt, authority: object) -> bool:
        assert original_bind(receipt, authority)  # type: ignore[arg-type]
        raise RuntimeError("synthetic handoff failure")

    def fail_config_unlink(path: object, *args: object, **kwargs: object) -> None:
        if path == paths.contract.config.name and kwargs.get("dir_fd") is not None:
            raise OSError
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(TlsMaterialReceipt, "_bind_lifetime", bind_then_fail)
    monkeypatch.setattr(os, "unlink", fail_config_unlink)
    with pytest.raises(tls_module.CoturnTlsCleanupRequired) as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    authority = captured.value.cleanup_authority
    assert repr(authority) == "TlsMaterialLifetimeAuthority()"
    assert paths.contract.config.exists()
    assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
    monkeypatch.setattr(os, "unlink", real_unlink)
    cleanup_tls_material_authority(authority)
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


def test_validation_rejects_key_mismatch_and_browser_pin_is_not_pem_hash(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_owned_file_exclusive(paths.contract.private_key, PRIVATE_KEY, mode=0o400, maximum=16_384)
    write_owned_file_exclusive(paths.contract.cert, CERTIFICATE, mode=0o400, maximum=65_536)
    mismatch = PUBLIC_KEY.replace(b"QUJD", b"REVG")
    with pytest.raises(CoturnTlsError, match="material is invalid"):
        validate_tls_material(
            runner=QueueRunner(_tls_results(private_public=mismatch)),
            tools=_tools(),
            paths=paths,
            now=NOW,
        )
    pem_hash = base64.b64encode(hashlib.sha256(PUBLIC_KEY).digest()).decode("ascii")
    der_hash = base64.b64encode(hashlib.sha256(DER_SPKI).digest()).decode("ascii")
    assert pem_hash != der_hash
    with pytest.raises(TypeError, match="factory-owned"):
        TlsMaterialReceipt(  # type: ignore[call-arg]
            object(),
            certificate_sha256="0" * 64,
            chromium_spki_sha256_b64=der_hash,
            not_before=NOW,
            not_after=NOW.replace(day=17),
        )


def test_generation_failure_removes_every_created_secret(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    values = [_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]
    values[5] = _result(PUBLIC_KEY.replace(b"QUJD", b"REVG"))
    with pytest.raises(CoturnTlsError):
        generate_tls_and_config_material(
            runner=QueueRunner(values),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


def test_readiness_request_verifies_exact_loopback_ip_with_private_ca(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    request = build_openssl_readiness_request(_tools(), paths)
    assert request.argv == (
        "/usr/bin/openssl",
        "s_client",
        "-connect",
        "127.0.0.1:5349",
        "-CAfile",
        os.fspath(paths.contract.cert),
        "-verify_ip",
        "127.0.0.1",
        "-verify_return_error",
        "-brief",
    )


@pytest.mark.parametrize("protocol", ["TLSv1.2", "TLSv1.3"])
def test_readiness_result_accepts_one_verified_modern_tls_session(protocol: str) -> None:
    cipher = "TLS_AES_256_GCM_SHA384" if protocol == "TLSv1.3" else "ECDHE-RSA-AES256-GCM-SHA384"
    receipt = validate_openssl_readiness_result(
        _result(b"", stderr=_readiness_transcript(protocol, cipher))
    )
    assert (receipt.protocol, receipt.cipher_suite) == (protocol, cipher)
    assert repr(receipt) == "OpenSslReadinessReceipt()"
    assert cipher not in repr(receipt)
    with pytest.raises(TypeError, match="factory-owned"):
        OpenSslReadinessReceipt(object(), protocol=protocol, cipher_suite=cipher)


@pytest.mark.parametrize(
    "result",
    [
        _result(b"", stderr=b"CONNECTION ESTABLISHED\n", returncode=1),
        _result(b"attacker payload", stderr=b"CONNECTION ESTABLISHED\n"),
        _result(b"Verification: OK\n", stderr=b"Protocol version: TLSv1.3\n"),
        _result(
            b"",
            stderr=(
                b"CONNECTION ESTABLISHED\nProtocol version: TLSv1.3\n"
                b"Ciphersuite: TLS_AES_256_GCM_SHA384\n"
                b"Peer certificate: CN = murmur-coturn-loopback.invalid\n"
                b"Hash used: SHA256\nSignature type: RSA-PSS\nVerification: OK\n"
                b"Verification: OK\nServer Temp Key: X25519, 253 bits\nDONE\n"
            ),
        ),
        _result(b"", stderr=b"CONNECTION ESTABLISHED\nsecret printable suffix\n"),
        _result(b"", stderr=b"CONNECTION ESTABLISHED\n\xff"),
        _result(b"", stderr=b"A" * 65_537),
        _result(
            b"",
            stderr=_readiness_transcript(
                "TLSv1.1",
                "ECDHE-RSA-AES256-GCM-SHA384",
            ),
        ),
        _result(
            b"",
            stderr=_readiness_transcript(
                "TLSv1.3",
                "ECDHE-RSA-AES256-GCM-SHA384",
            ),
        ),
        _result(
            b"",
            stderr=_readiness_transcript(
                "TLSv1.3",
                "TLS_AES_256_GCM_SHA384",
            )
            .replace(b"Protocol version: ", b"")
            .replace(b"Ciphersuite: ", b""),
        ),
        _result(
            b"",
            stderr=_readiness_transcript(
                "TLSv1.3",
                "TLS_AES_256_GCM_SHA384",
                final_newline=False,
            ),
        ),
        _result(
            b"",
            stderr=_readiness_transcript(
                "TLSv1.3",
                "TLS_AES_256_GCM_SHA384",
            ).replace(b"\n", b"\r\n"),
        ),
    ],
)
def test_readiness_result_rejects_unverified_legacy_unbounded_or_non_ascii_output(
    result: object,
) -> None:
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn OpenSSL readiness is invalid$",
    ) as captured:
        validate_openssl_readiness_result(result)  # type: ignore[arg-type]
    assert "Ciphersuite" not in str(captured.value)


def test_readiness_failure_scrubs_arbitrary_stdout_and_stderr_from_traceback() -> None:
    stdout = b"traceback-sentinel-readiness-stdout"
    stderr = b"traceback-sentinel-readiness-stderr\n"
    with pytest.raises(CoturnTlsError) as captured:
        validate_openssl_readiness_result(_result(stdout, stderr=stderr))
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, stdout, stderr)


def test_readiness_receipt_has_no_unchecked_public_factory() -> None:
    assert "new_openssl_readiness_receipt" not in readiness_module.__all__
    assert not hasattr(readiness_module, "new_openssl_readiness_receipt")
    with pytest.raises(
        TypeError,
        match=r"^OpenSSL readiness receipt is factory-owned$",
    ):
        OpenSslReadinessReceipt(
            object(),
            protocol="SSLv3",
            cipher_suite="NULL",
        )


def test_invalid_private_write_scrubs_value_before_fixed_error(tmp_path: Path) -> None:
    secret = b"traceback-sentinel-private-write"
    target = _paths(tmp_path).control_dir / "invalid-mode.bin"
    with pytest.raises(CoturnTlsError, match=r"content is invalid$") as captured:
        write_owned_file_exclusive(target, secret, mode=0o777, maximum=1_024)
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_private_write_preserves_control_and_scrubs_value_across_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    cleanup_fails: bool,
) -> None:
    secret = b"traceback-sentinel-private-write-control"
    target = _paths(tmp_path).control_dir / "interrupted.bin"
    namespace = {"CONTROL": control}
    exec(
        compile(
            "def interrupt_write(_fd, value):\n    private = value\n    raise CONTROL()\n",
            "/synthetic/tls_private_write.py",
            "exec",
        ),
        namespace,
    )
    monkeypatch.setattr(os, "write", namespace["interrupt_write"])
    real_unlink = os.unlink
    if cleanup_fails:

        def refuse(path: object, *args: object, **kwargs: object) -> None:
            if path == target.name and kwargs.get("dir_fd") is not None:
                raise OSError
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", refuse)
    try:
        with pytest.raises(control) as captured:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        assert not traceback_contains(captured.value, secret)
        assert target.exists() is cleanup_fails
    finally:
        try:
            real_unlink(target)
        except FileNotFoundError:
            pass


@pytest.mark.parametrize("control", [None, KeyboardInterrupt, SystemExit])
def test_private_read_scrubs_partial_chunks_on_failure_or_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit] | None,
) -> None:
    secret = b"traceback-sentinel-private-read"
    target = _paths(tmp_path).control_dir / "read.bin"
    target.write_bytes(secret)
    target.chmod(0o400)
    namespace = {"SECRET": secret, "CONTROL": control, "calls": 0}
    exec(
        compile(
            "def hostile_read(_fd, _maximum):\n"
            "    global calls\n"
            "    calls += 1\n"
            "    if calls == 1:\n"
            "        chunk = SECRET\n"
            "        return chunk\n"
            "    if CONTROL is not None:\n"
            "        raise CONTROL()\n"
            "    raise OSError('synthetic')\n",
            "/synthetic/tls_private_read.py",
            "exec",
        ),
        namespace,
    )
    monkeypatch.setattr(os, "read", namespace["hostile_read"])
    expected = control or CoturnTlsError
    with pytest.raises(expected) as captured:
        read_owned_file(target, exact_mode=0o400, maximum=1_024)
    assert not traceback_contains(captured.value, secret)


@pytest.mark.parametrize("control", [None, KeyboardInterrupt, SystemExit])
def test_openssl_stdin_discards_runner_traceback_and_private_input(
    control: type[KeyboardInterrupt] | type[SystemExit] | None,
) -> None:
    secret = b"traceback-sentinel-openssl-stdin"
    namespace = {"CONTROL": control}
    exec(
        compile(
            "class HostileRunner:\n"
            "    def run(self, request):\n"
            "        private = request.stdin\n"
            "        if CONTROL is not None:\n"
            "            raise CONTROL()\n"
            "        raise RuntimeError('synthetic')\n",
            "/synthetic/tls_runner.py",
            "exec",
        ),
        namespace,
    )
    expected = control or CoturnTlsError
    with pytest.raises(expected) as captured:
        tls_module._openssl_stdin(
            namespace["HostileRunner"](),
            _tools(),
            "pkey",
            secret,
            "-pubout",
        )
    assert not traceback_contains(captured.value, secret)


def test_validation_discards_certificate_key_and_runner_failure_tracebacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = b"traceback-sentinel-validation-certificate"
    private_key = b"traceback-sentinel-validation-private-key"
    values = iter((certificate, private_key))
    monkeypatch.setattr(tls_module, "validate_turn_tls_ca_file", lambda *_a, **_k: None)
    monkeypatch.setattr(tls_module, "read_owned_file", lambda *_a, **_k: next(values))
    namespace: dict[str, object] = {}
    exec(
        compile(
            "class HostileRunner:\n"
            "    def run(self, request):\n"
            "        private = request.stdin\n"
            "        raise RuntimeError('synthetic')\n",
            "/synthetic/tls_validation_runner.py",
            "exec",
        ),
        namespace,
    )
    with pytest.raises(CoturnTlsError, match=r"TLS validation failed$") as captured:
        validate_tls_material(
            runner=namespace["HostileRunner"](),  # type: ignore[arg-type]
            tools=_tools(),
            paths=_paths(tmp_path),
            now=NOW,
        )
    assert not traceback_contains(captured.value, certificate, private_key)


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "exit"])
def test_validation_preserves_opaque_private_descriptor_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "validation-recovery.bin"
    secret = b"traceback-sentinel-validation-recovery"
    target.write_bytes(secret)
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    authority = receipt_module.new_private_descriptor_cleanup_authority()
    assert authority.begin()
    assert authority.publish(((descriptor, (details.st_dev, details.st_ino)),))

    def fail_read(*_args: object, **_kwargs: object) -> object:
        if kind == "ordinary":
            raise CoturnTlsPrivateCleanupRequired(authority)
        if kind == "keyboard":
            error: KeyboardInterrupt | SystemExit = KeyboardInterrupt()
        else:
            error = SystemExit(23)
        error.cleanup_authority = authority  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(tls_module, "validate_turn_tls_ca_file", lambda *_a, **_k: None)
    monkeypatch.setattr(tls_module, "read_owned_file", fail_read)
    expected = (
        CoturnTlsPrivateCleanupRequired
        if kind == "ordinary"
        else (KeyboardInterrupt if kind == "keyboard" else SystemExit)
    )
    try:
        with pytest.raises(expected) as captured:
            validate_tls_material(
                runner=QueueRunner([]),
                tools=_tools(),
                paths=paths,
                now=NOW,
            )
        assert captured.value.__context__ is None
        assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
        assert not traceback_contains(captured.value, secret)
        cleanup_tls_private_authority(authority)
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


def test_generation_adopts_persistent_validation_descriptor_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "generation-validation-recovery.bin"
    target.write_bytes(b"private validation recovery")
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    descriptor_identity = (details.st_dev, details.st_ino)
    private_authority = receipt_module.new_private_descriptor_cleanup_authority()
    assert private_authority.begin()
    assert private_authority.publish(((descriptor, descriptor_identity),))
    real_close_owned = receipt_module.close_owned_descriptor

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise CoturnTlsPrivateCleanupRequired(private_authority)

    def refuse_descriptor_close(
        candidate: int,
        identity: tuple[int, int] | None,
        control: object,
    ) -> bool:
        if candidate == descriptor:
            return False
        return real_close_owned(candidate, identity, control)  # type: ignore[arg-type]

    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    monkeypatch.setattr(receipt_module, "close_owned_descriptor", refuse_descriptor_close)
    lifetime = None
    try:
        with pytest.raises(tls_module.CoturnTlsCleanupRequired) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        lifetime = captured.value.cleanup_authority
        assert repr(lifetime) == "TlsMaterialLifetimeAuthority()"
        assert os.fstat(descriptor)
        assert not any(
            path.exists()
            for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
        )
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_close_owned)
        cleanup_tls_material_authority(lifetime)
        lifetime = None
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    finally:
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_close_owned)
        if lifetime is not None and lifetime.active:
            cleanup_tls_material_authority(lifetime)
        if private_authority.active:
            cleanup_tls_private_authority(private_authority)


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "exit"])
def test_generation_adoption_refusal_preserves_private_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "generation-adoption-refusal.bin"
    secret = b"traceback-sentinel-generation-adoption-refusal"
    target.write_bytes(secret)
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    authority = receipt_module.new_private_descriptor_cleanup_authority()
    assert authority.begin()
    assert authority.publish(((descriptor, (details.st_dev, details.st_ino)),))

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        if kind == "ordinary":
            raise CoturnTlsPrivateCleanupRequired(authority)
        if kind == "keyboard":
            error: KeyboardInterrupt | SystemExit = KeyboardInterrupt()
        else:
            error = SystemExit(23)
        error.cleanup_authority = authority  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    monkeypatch.setattr(
        tls_module.TlsMaterialLifetimeAuthority,
        "retain_private_authority",
        lambda *_args: False,
    )
    expected = (
        CoturnTlsPrivateCleanupRequired
        if kind == "ordinary"
        else (KeyboardInterrupt if kind == "keyboard" else SystemExit)
    )
    try:
        with pytest.raises(expected) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        if kind == "exit":
            assert captured.value.code == 23
        assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
        assert authority.active and os.fstat(descriptor)
        assert not traceback_contains(captured.value, secret, SECRET, PRIVATE_KEY, CERTIFICATE)
        assert not any(
            path.exists()
            for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
        )
        cleanup_tls_private_authority(authority)
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "exit"])
def test_generation_combines_unadopted_private_and_failed_lifetime_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "combined-generation-recovery.bin"
    secret = b"traceback-sentinel-combined-generation-recovery"
    target.write_bytes(secret)
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    private = receipt_module.new_private_descriptor_cleanup_authority()
    assert private.begin()
    assert private.publish(((descriptor, (details.st_dev, details.st_ino)),))
    real_remove = receipt_module.remove_owned_inode

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        if kind == "ordinary":
            raise CoturnTlsPrivateCleanupRequired(private)
        error: KeyboardInterrupt | SystemExit
        error = KeyboardInterrupt() if kind == "keyboard" else SystemExit(23)
        error.cleanup_authority = private  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    monkeypatch.setattr(
        tls_module.TlsMaterialLifetimeAuthority,
        "retain_private_authority",
        lambda *_args: False,
    )
    monkeypatch.setattr(receipt_module, "remove_owned_inode", lambda *_args: False)
    combined = None
    try:
        expected = (
            tls_module.CoturnTlsCleanupRequired
            if kind == "ordinary"
            else (KeyboardInterrupt if kind == "keyboard" else SystemExit)
        )
        with pytest.raises(expected) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        combined = captured.value.cleanup_authority
        if kind == "exit":
            assert captured.value.code == 23
        assert type(combined) is lifetime_module.TlsCombinedCleanupAuthority
        assert combined.active and private.active and os.fstat(descriptor)
        assert not traceback_contains(captured.value, secret, SECRET, PRIVATE_KEY, CERTIFICATE)
        monkeypatch.setattr(receipt_module, "remove_owned_inode", real_remove)
        cleanup_tls_material_authority(combined)
        combined = None
        assert not private.active
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
        assert not any(
            path.exists()
            for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
        )
    finally:
        monkeypatch.setattr(receipt_module, "remove_owned_inode", real_remove)
        if combined is not None and combined.active:
            cleanup_tls_material_authority(combined)
        if private.active:
            cleanup_tls_private_authority(private)


def test_generation_discards_static_secret_and_command_output_tracebacks(tmp_path: Path) -> None:
    progress = b"traceback-sentinel-key-progress"
    with pytest.raises(CoturnTlsError, match=r"private-key generation failed$") as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY, stderr=progress)]),
            tools=_tools(),
            paths=_paths(tmp_path),
            topology=TOPOLOGY,
            static_auth_secret="traceback-sentinel-static-auth-secret",
            now=NOW,
        )
    assert not traceback_contains(captured.value, progress, "traceback-sentinel-static-auth-secret")


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_generation_pre_registers_created_path_before_writer_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
) -> None:
    paths = _paths(tmp_path)
    real_write = tls_module.write_owned_file_exclusive_tracked

    def write_then_interrupt(
        path: Path,
        value: bytes,
        *,
        mode: int,
        maximum: int,
        cleanup_receipt: object,
    ) -> object:
        real_write(
            path,
            value,
            mode=mode,
            maximum=maximum,
            cleanup_receipt=cleanup_receipt,
        )
        raise control()

    monkeypatch.setattr(
        tls_module,
        "write_owned_file_exclusive_tracked",
        write_then_interrupt,
    )
    with pytest.raises(control) as captured:
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert not traceback_contains(captured.value, SECRET)
    assert not any(
        path.exists()
        for path in (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    )


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_generation_cleanup_failure_never_replaces_original_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
) -> None:
    paths = _paths(tmp_path)
    real_write = tls_module.write_owned_file_exclusive_tracked
    real_unlink = os.unlink
    attempted: list[str] = []

    def interrupt_after_config(
        path: Path,
        value: bytes,
        *,
        mode: int,
        maximum: int,
        cleanup_receipt: object,
    ) -> object:
        receipt = real_write(
            path,
            value,
            mode=mode,
            maximum=maximum,
            cleanup_receipt=cleanup_receipt,
        )
        if path == paths.contract.config:
            raise control()
        return receipt

    def fail_one_unlink(path: object, *args: object, **kwargs: object) -> None:
        if isinstance(path, str) and kwargs.get("dir_fd") is not None:
            attempted.append(path)
        if path == paths.contract.config.name and kwargs.get("dir_fd") is not None:
            raise OSError
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        tls_module,
        "write_owned_file_exclusive_tracked",
        interrupt_after_config,
    )
    monkeypatch.setattr(os, "unlink", fail_one_unlink)
    try:
        with pytest.raises(control) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
        assert attempted == [
            paths.contract.config.name,
            paths.contract.config.name,
            paths.contract.cert.name,
            paths.contract.private_key.name,
            paths.contract.config.name,
            paths.contract.config.name,
        ]
        assert paths.contract.config.exists()
        assert not paths.contract.cert.exists() and not paths.contract.private_key.exists()
        authority = captured.value.cleanup_authority
        assert repr(authority) == "TlsMaterialLifetimeAuthority()"
        monkeypatch.setattr(os, "unlink", real_unlink)
        cleanup_tls_material_authority(authority)
        assert not paths.contract.config.exists()
    finally:
        try:
            real_unlink(paths.contract.config)
        except FileNotFoundError:
            pass


@pytest.mark.parametrize(("original_code", "expected_code"), [(None, -7), (256, 256)])
def test_generation_preserves_first_exact_control_across_all_cleanup_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_code: int | None,
    expected_code: int,
) -> None:
    paths = _paths(tmp_path)
    real_write = tls_module.write_owned_file_exclusive_tracked
    real_unlink = os.unlink
    attempted: list[str] = []

    def write_or_interrupt(
        path: Path,
        value: bytes,
        *,
        mode: int,
        maximum: int,
        cleanup_receipt: object,
    ) -> object:
        receipt = real_write(
            path,
            value,
            mode=mode,
            maximum=maximum,
            cleanup_receipt=cleanup_receipt,
        )
        if original_code is not None and path == paths.contract.config:
            raise SystemExit(original_code)
        return receipt

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise CoturnTlsError("synthetic")

    def interrupt_cleanup(path: object, *args: object, **kwargs: object) -> None:
        if isinstance(path, str) and kwargs.get("dir_fd") is not None:
            attempted.append(path)
        if path == paths.contract.config.name and attempted.count(path) == 1:
            raise SystemExit(-7)
        if path == paths.contract.cert.name and attempted.count(path) == 1:
            raise SystemExit(23)
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        tls_module,
        "write_owned_file_exclusive_tracked",
        write_or_interrupt,
    )
    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    monkeypatch.setattr(os, "unlink", interrupt_cleanup)
    try:
        with pytest.raises(SystemExit) as captured:
            generate_tls_and_config_material(
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert captured.value.code == expected_code
        assert attempted == [
            paths.contract.config.name,
            paths.contract.config.name,
            paths.contract.cert.name,
            paths.contract.cert.name,
            paths.contract.private_key.name,
        ]
        assert not traceback_contains(captured.value, SECRET, PRIVATE_KEY, CERTIFICATE)
    finally:
        for path in (paths.contract.config, paths.contract.cert, paths.contract.private_key):
            try:
                real_unlink(path)
            except FileNotFoundError:
                pass


def test_generation_failure_removes_renamed_original_inode_and_preserves_victim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    moved = paths.contract.coturn_dir / "renamed-generated-key.pem"
    victim = b"replacement-victim"
    original_identity: tuple[int, int] | None = None
    real_write = tls_module.write_owned_file_exclusive_tracked

    def write_and_swap_key(
        path: Path,
        value: bytes,
        *,
        mode: int,
        maximum: int,
        cleanup_receipt: object,
    ) -> object:
        nonlocal original_identity
        receipt = real_write(
            path,
            value,
            mode=mode,
            maximum=maximum,
            cleanup_receipt=cleanup_receipt,
        )
        if path == paths.contract.private_key:
            details = path.stat(follow_symlinks=False)
            original_identity = (details.st_dev, details.st_ino)
            path.rename(moved)
            path.write_bytes(victim)
            path.chmod(0o400)
        return receipt

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise CoturnTlsError("synthetic")

    monkeypatch.setattr(
        tls_module,
        "write_owned_file_exclusive_tracked",
        write_and_swap_key,
    )
    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS material is invalid$"):
        generate_tls_and_config_material(
            runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert original_identity is not None
    assert paths.contract.private_key.read_bytes() == victim
    assert not moved.exists()
    assert not paths.contract.cert.exists() and not paths.contract.config.exists()
    assert all(
        (details.st_dev, details.st_ino) != original_identity
        for details in (
            path.stat(follow_symlinks=False) for path in paths.contract.coturn_dir.iterdir()
        )
    )


@pytest.mark.parametrize(
    ("exit_code", "expected_code"),
    [
        (None, None),
        (23, 23),
        (-7, -7),
        (256, 256),
        ("traceback-sentinel-unsafe-exit-code", 1),
    ],
)
@pytest.mark.parametrize("surface", ["openssl", "read", "write", "generation"])
def test_system_exit_semantics_survive_private_cleanup_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: object,
    expected_code: int | None,
    surface: str,
) -> None:
    secret = b"traceback-sentinel-system-exit-private"
    paths = _paths(tmp_path)
    target = paths.control_dir / f"{surface}.bin"
    namespace = {"CODE": exit_code}
    real_unlink = Path.unlink
    real_tracked_write = tls_module.write_owned_file_exclusive_tracked
    try:
        if surface == "openssl":
            exec(
                compile(
                    "class Runner:\n"
                    "    def run(self, request):\n"
                    "        private = request.stdin\n"
                    "        raise SystemExit(CODE)\n",
                    "/synthetic/tls_exit_runner.py",
                    "exec",
                ),
                namespace,
            )
        elif surface == "read":
            target.write_bytes(secret)
            target.chmod(0o400)
            exec(
                compile(
                    "def exit_read(_fd, _maximum):\n"
                    "    private = CODE\n"
                    "    raise SystemExit(CODE)\n",
                    "/synthetic/tls_exit_read.py",
                    "exec",
                ),
                namespace,
            )
            monkeypatch.setattr(os, "read", namespace["exit_read"])
        elif surface == "write":
            exec(
                compile(
                    "def exit_write(_fd, value):\n"
                    "    private = value\n"
                    "    raise SystemExit(CODE)\n",
                    "/synthetic/tls_exit_write.py",
                    "exec",
                ),
                namespace,
            )
            monkeypatch.setattr(os, "write", namespace["exit_write"])
        else:

            def exit_after_config(
                path: Path,
                value: bytes,
                *,
                mode: int,
                maximum: int,
                cleanup_receipt: object,
            ) -> object:
                receipt = real_tracked_write(
                    path,
                    value,
                    mode=mode,
                    maximum=maximum,
                    cleanup_receipt=cleanup_receipt,
                )
                if path == paths.contract.config:
                    raise SystemExit(exit_code)
                return receipt

            def fail_config_unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path == paths.contract.config:
                    raise OSError
                real_unlink(path, *args, **kwargs)

            monkeypatch.setattr(
                tls_module,
                "write_owned_file_exclusive_tracked",
                exit_after_config,
            )
            monkeypatch.setattr(Path, "unlink", fail_config_unlink)
        with pytest.raises(SystemExit) as captured:
            if surface == "openssl":
                tls_module._openssl_stdin(
                    namespace["Runner"](), _tools(), "pkey", secret, "-pubout"
                )
            elif surface == "read":
                read_owned_file(target, exact_mode=0o400, maximum=1_024)
            elif surface == "write":
                write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
            else:
                generate_tls_and_config_material(
                    runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                    tools=_tools(),
                    paths=paths,
                    topology=TOPOLOGY,
                    static_auth_secret=SECRET,
                    now=NOW,
                )
        assert captured.value.code == expected_code
        assert not traceback_contains(
            captured.value,
            secret,
            "traceback-sentinel-unsafe-exit-code",
        )
    finally:
        real_unlink(target, missing_ok=True)
        real_unlink(paths.contract.config, missing_ok=True)


@pytest.mark.parametrize(
    ("control", "expected_code"),
    [(KeyboardInterrupt, None), (SystemExit, 23)],
)
def test_secret_free_service_start_control_waits_past_old_timeout_and_joins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    expected_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "delayed-owner.bin"
    secret = b"traceback-sentinel-delayed-owner"
    release = threading.Event()
    finished = threading.Event()
    outcomes: list[tuple[type[BaseException], object, BaseException | None]] = []
    real_start = threading.Thread.start
    real_serve = worker_module.TlsOwnerService._serve
    original_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    original_mask = (
        signal.pthread_sigmask(signal.SIG_BLOCK, set())
        if hasattr(signal, "pthread_sigmask")
        else None
    )

    def delayed_serve(service: object) -> None:
        release.wait()
        real_serve(service)  # type: ignore[arg-type]

    def start_then_interrupt(thread: threading.Thread) -> None:
        if thread.name != "coturn-tls-private-owner":
            real_start(thread)
            return
        real_start(thread)
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(23)

    def invoke() -> None:
        try:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        except BaseException as error:
            outcomes.append((type(error), getattr(error, "code", None), error.__context__))
        finally:
            finished.set()

    caller = threading.Thread(target=invoke, name="synthetic-tls-caller")
    monkeypatch.setattr(worker_module.TlsOwnerService, "_serve", delayed_serve)
    monkeypatch.setattr(threading.Thread, "start", start_then_interrupt)
    real_start(caller)
    assert not finished.wait(1.1)
    assert not target.exists()
    release.set()
    caller.join(timeout=5)
    assert not caller.is_alive()
    assert outcomes == [(control, expected_code, None)]
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == original_handlers
    if original_mask is not None:
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == original_mask
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )
    assert not target.exists()


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_definite_prestart_control_returns_without_waiting_for_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "prestart-owner.bin"
    secret = b"traceback-sentinel-prestart-owner"
    captured_threads: list[threading.Thread] = []
    real_start = threading.Thread.start
    original_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    original_mask = (
        signal.pthread_sigmask(signal.SIG_BLOCK, set())
        if hasattr(signal, "pthread_sigmask")
        else None
    )

    def interrupt_before_start(thread: threading.Thread) -> None:
        if thread.name != "coturn-tls-private-owner":
            real_start(thread)
            return
        captured_threads.append(thread)
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(threading.Thread, "start", interrupt_before_start)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == exit_code
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    assert captured_threads and all(thread.ident is None for thread in captured_threads)
    assert all(getattr(thread, "_target", None) is None for thread in captured_threads)
    assert not target.exists()
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == original_handlers
    if original_mask is not None:
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == original_mask


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_control_at_service_start_entry_aborts_definitely_unstarted_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "start-entry-control.bin"
    secret = b"traceback-sentinel-start-entry-control"
    captured_services: list[object] = []

    def interrupt_at_entry(service: object) -> tuple[bool, object]:
        captured_services.append(service)
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(worker_module.TlsOwnerService, "start", interrupt_at_entry)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == exit_code
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    assert captured_services
    thread = captured_services[0]._thread
    assert thread.ident is None and getattr(thread, "_target", None) is None
    assert not target.exists()


def test_limbo_membership_is_not_classified_as_definitely_unstarted() -> None:
    service = worker_module.TlsOwnerService()
    service._start_call_entered = True
    latch = worker_module.TlsControlLatch()
    lock = threading._active_limbo_lock
    with lock:
        threading._limbo[service._thread] = service._thread
    try:
        assert not service._definitely_unstarted(latch)
    finally:
        with lock:
            threading._limbo.pop(service._thread, None)
        service._scrub_unstarted(latch)


@pytest.mark.parametrize("attribute", ["_limbo", "_active_limbo_lock"])
def test_malformed_thread_runtime_is_rejected_before_secret_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
) -> None:
    target = _paths(tmp_path).control_dir / "malformed-thread-runtime.bin"
    secret = b"traceback-sentinel-malformed-thread-runtime"
    monkeypatch.setattr(worker_module.threading, attribute, object())
    with pytest.raises(CoturnTlsError, match=r"^Coturn private file creation failed$") as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


def test_malformed_thread_started_event_is_rejected_before_secret_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "malformed-started-event.bin"
    secret = b"traceback-sentinel-malformed-started-event"
    real_thread = threading.Thread

    def malformed_thread(*args: object, **kwargs: object) -> threading.Thread:
        thread = real_thread(*args, **kwargs)
        if thread.name == "coturn-tls-private-owner":
            thread._started = object()
        return thread

    monkeypatch.setattr(worker_module.threading, "Thread", malformed_thread)
    with pytest.raises(CoturnTlsError, match=r"^Coturn private file creation failed$") as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


@pytest.mark.parametrize("phase", ["runtime-check", "unstarted-scrub"])
@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_prestart_runtime_and_scrub_controls_reach_first_control_latch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / f"{phase}-control.bin"
    secret = b"traceback-sentinel-runtime-scrub-control"

    def raise_selected() -> None:
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    class ControlledThread(threading.Thread):
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.control_armed = False
            super().__init__(*args, **kwargs)
            self.control_armed = True

        def __getattribute__(self, name: str) -> object:
            if (
                name == "_started"
                and object.__getattribute__(self, "control_armed")
                and phase == "runtime-check"
            ):
                object.__setattr__(self, "control_armed", False)
                raise_selected()
            return super().__getattribute__(name)

        def __setattr__(self, name: str, value: object) -> None:
            if (
                name == "_target"
                and value is None
                and getattr(self, "control_armed", False)
                and phase == "unstarted-scrub"
            ):
                object.__setattr__(self, "control_armed", False)
                raise_selected()
            super().__setattr__(name, value)

        def start(self) -> None:
            if phase == "unstarted-scrub":
                raise RuntimeError("synthetic start failure")
            super().start()

    monkeypatch.setattr(worker_module.threading, "Thread", ControlledThread)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == exit_code
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, 23)],
)
def test_stop_publication_records_first_control_after_ordinary_start_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "stop-control.bin"
    secret = b"traceback-sentinel-stop-control"
    real_start = threading.Thread.start
    real_notify = threading.Condition.notify_all
    injected = False

    def fail_owner_start(thread: threading.Thread) -> None:
        if thread.name == "coturn-tls-private-owner":
            raise RuntimeError("synthetic")
        real_start(thread)

    def interrupt_notify(condition: threading.Condition) -> None:
        nonlocal injected
        if not injected:
            injected = True
            if control is KeyboardInterrupt:
                raise KeyboardInterrupt
            raise SystemExit(23)
        real_notify(condition)

    monkeypatch.setattr(threading.Thread, "start", fail_owner_start)
    monkeypatch.setattr(threading.Condition, "notify_all", interrupt_notify)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert injected and getattr(captured.value, "code", None) == exit_code
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


@pytest.mark.parametrize("surface", ["read", "write", "remove", "receipt"])
@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_task_constructor_failure_propagates_abort_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / f"abort-control-{surface}.bin"
    secret = b"traceback-sentinel-abort-control"
    signal_value = (control, exit_code)
    real_abort = worker_module.TlsOwnerService.abort

    def abort_with_control(
        service: worker_module.TlsOwnerService,
        initial: object = None,
    ) -> object:
        selected = initial if initial is not None else signal_value
        return real_abort(service, selected)  # type: ignore[arg-type]

    def fail_task(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic constructor failure")

    monkeypatch.setattr(worker_module.TlsOwnerService, "abort", abort_with_control)
    if surface == "read":
        target.write_bytes(secret)
        target.chmod(0o400)
        monkeypatch.setattr(reader_module, "_ReadTask", fail_task)
        with pytest.raises(control) as captured:
            read_owned_file(target, exact_mode=0o400, maximum=1_024)
        assert getattr(captured.value, "code", None) == exit_code
        assert not traceback_contains(captured.value, secret)
    elif surface == "write":
        monkeypatch.setattr(owner_module, "_WriteTask", fail_task)
        with pytest.raises(control) as captured:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        assert getattr(captured.value, "code", None) == exit_code
        assert not traceback_contains(captured.value, secret)
    elif surface == "remove":
        monkeypatch.setattr(owner_module, "_RemoveTask", fail_task)
        failed, observed = owner_module.remove_private_files_owned((target,))
        assert failed and observed == signal_value
    else:
        cleanup_receipt = receipt_module.new_private_file_cleanup_receipt()
        monkeypatch.setattr(receipt_module, "_ReceiptTask", fail_task)
        failed, observed = receipt_module.settle_private_file_receipts_owned(
            (cleanup_receipt,),
        )
        assert failed and observed == signal_value
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_caller_owned_service_aborts_post_start_handoff_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "post-start-handoff.bin"
    secret = b"traceback-sentinel-post-start-handoff"
    real_start = owner_module.start_tls_owner_service

    def interrupt_after_start(service: object) -> object:
        started, observed = real_start(service)  # type: ignore[arg-type]
        assert started and observed is None
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(owner_module, "start_tls_owner_service", interrupt_after_start)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == exit_code
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("missing", ["_active_limbo_lock", "_limbo"])
def test_worker_import_and_start_fail_closed_without_cpython_private(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    source = Path(worker_module.__file__).read_text(encoding="utf-8")
    monkeypatch.delattr(threading, missing)
    namespace = {"__name__": "/synthetic/coturn_tls_worker.py"}
    exec(compile(source, "/synthetic/coturn_tls_worker.py", "exec"), namespace)
    service = namespace["TlsOwnerService"]()
    started, control = service.start()
    assert not started and control is None
    assert not service._thread.is_alive()


def test_worker_start_fails_closed_without_thread_started_event() -> None:
    service = worker_module.TlsOwnerService()
    del service._thread._started
    started, control = service.start()
    assert not started and control is None


def test_owner_wait_control_cannot_report_success_after_worker_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = worker_module.TlsOwnerService()
    real_wait = service._condition.wait
    injected = False

    def interrupt_wait(timeout: float | None = None) -> bool:
        nonlocal injected
        if not injected:
            injected = True
            raise SystemExit(23)
        return real_wait(timeout)

    monkeypatch.setattr(service._condition, "wait", interrupt_wait)
    started, control = service.start()
    assert not started and control == (SystemExit, 23)
    assert injected and service._finished.is_set()
    assert not service._thread.is_alive()


def test_owner_finalization_retries_control_until_terminal_event_is_published() -> None:
    class OneShotControlEvent:
        def __init__(self) -> None:
            self.inner = threading.Event()
            self.injected = False

        def set(self) -> None:
            if not self.injected:
                self.injected = True
                raise SystemExit(23)
            self.inner.set()

        def is_set(self) -> bool:
            return self.inner.is_set()

        def wait(self, timeout: float | None = None) -> bool:
            return self.inner.wait(timeout)

    service = worker_module.TlsOwnerService()
    finished = OneShotControlEvent()
    service._finished = finished  # type: ignore[assignment]
    started, control = service.start()
    assert started and control is None
    observed = service.abort()
    assert observed == (SystemExit, 23)
    assert finished.injected and finished.is_set() and service._entered.is_set()
    assert not service._thread.is_alive()


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, 23)],
)
def test_untracked_write_control_uses_exact_receipt_not_rebound_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "untracked-exact.bin"
    moved = paths.control_dir / "untracked-original.bin"
    secret = b"traceback-sentinel-untracked-exact"
    victim = b"replacement-victim"
    real_execute = worker_module.TlsOwnerService.execute
    injected = False

    def execute_then_swap(service: object, task: object) -> bool:
        nonlocal injected
        completed = real_execute(service, task)  # type: ignore[arg-type]
        if not injected and type(task) is owner_module._WriteTask:
            injected = True
            target.rename(moved)
            target.write_bytes(victim)
            target.chmod(0o400)
            if control is KeyboardInterrupt:
                raise KeyboardInterrupt
            raise SystemExit(exit_code)
        return completed

    monkeypatch.setattr(worker_module.TlsOwnerService, "execute", execute_then_swap)
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert injected and getattr(captured.value, "code", None) == exit_code
    assert target.read_bytes() == victim
    assert not moved.exists()
    assert not traceback_contains(captured.value, secret)


def test_primary_release_failure_rolls_back_after_directory_would_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "primary-release-failure.bin"
    secret = b"traceback-sentinel-primary-release-failure"
    real_close_owned = receipt_module.close_owned_descriptor
    injected = False
    file_fd: int | None = None

    def fail_first_file_close(
        descriptor: int,
        identity: tuple[int, int] | None,
        control: object,
    ) -> bool:
        nonlocal file_fd, injected
        if stat.S_ISREG(os.fstat(descriptor).st_mode) and not injected:
            injected = True
            file_fd = descriptor
            return False
        return real_close_owned(descriptor, identity, control)  # type: ignore[arg-type]

    monkeypatch.setattr(receipt_module, "close_owned_descriptor", fail_first_file_close)
    with pytest.raises(CoturnTlsError, match=r"^Coturn private file cleanup failed$") as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert injected and file_fd is not None
    assert not target.exists() and not traceback_contains(captured.value, secret)
    with pytest.raises(OSError) as closed:
        os.fstat(file_fd)
    assert closed.value.errno == errno.EBADF


def test_post_create_identity_failure_retains_unknown_descriptor_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "unknown-file-descriptor.bin"
    secret = b"traceback-sentinel-unknown-file-descriptor"
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    real_closerange = os.closerange
    created_fd: int | None = None
    identity_failed = False

    def record_created_fd(path: object, *args: object, **kwargs: object) -> int:
        nonlocal created_fd
        descriptor = real_open(path, *args, **kwargs)  # type: ignore[arg-type]
        if path == target.name:
            created_fd = descriptor
        return descriptor

    def fail_initial_file_identity(descriptor: int) -> os.stat_result:
        nonlocal identity_failed
        if descriptor == created_fd and not identity_failed:
            identity_failed = True
            raise OSError("synthetic identity failure")
        return real_fstat(descriptor)

    def refuse_created_close(descriptor: int) -> None:
        if descriptor == created_fd:
            raise OSError("synthetic close failure")
        real_close(descriptor)

    def refuse_created_closerange(first: int, last: int) -> None:
        if created_fd is not None and first <= created_fd < last:
            raise OSError("synthetic closerange failure")
        real_closerange(first, last)

    monkeypatch.setattr(owner_module.os, "open", record_created_fd)
    monkeypatch.setattr(owner_module.os, "fstat", fail_initial_file_identity)
    monkeypatch.setattr(cleanup_module.os, "close", refuse_created_close)
    monkeypatch.setattr(cleanup_module.os, "closerange", refuse_created_closerange)
    authority = None
    try:
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as captured:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        authority = captured.value.cleanup_authority
        assert identity_failed and created_fd is not None
        assert type(authority) is receipt_module.PrivateFileCleanupReceipt
        assert authority.owned and os.fstat(created_fd)
        assert target.exists() and not traceback_contains(captured.value, secret)
        monkeypatch.setattr(owner_module.os, "open", real_open)
        monkeypatch.setattr(owner_module.os, "fstat", real_fstat)
        monkeypatch.setattr(cleanup_module.os, "close", real_close)
        monkeypatch.setattr(cleanup_module.os, "closerange", real_closerange)
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as quarantined:
            cleanup_tls_private_authority(authority)
        assert quarantined.value.cleanup_authority is authority
        _clear_synthetic_close_quarantine(created_fd)
        cleanup_tls_private_authority(authority)
        authority = None
        assert not target.exists()
        with pytest.raises(OSError) as closed:
            os.fstat(created_fd)
        assert closed.value.errno == errno.EBADF
    finally:
        monkeypatch.setattr(owner_module.os, "open", real_open)
        monkeypatch.setattr(owner_module.os, "fstat", real_fstat)
        monkeypatch.setattr(cleanup_module.os, "close", real_close)
        monkeypatch.setattr(cleanup_module.os, "closerange", real_closerange)
        if authority is not None:
            if created_fd is not None:
                _clear_synthetic_close_quarantine(created_fd)
            cleanup_tls_private_authority(authority)
        target.unlink(missing_ok=True)


def test_repeated_exact_receipt_publication_controls_never_lose_created_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "receipt-publication-controls.bin"
    secret = b"traceback-sentinel-receipt-publication-controls"
    real_publish = receipt_module.PrivateFileCleanupReceipt.publish_owned
    real_owned = receipt_module.PrivateFileCleanupReceipt.owned.fget
    assert real_owned is not None
    real_open = os.open
    real_dup = os.dup
    tracked: set[int] = set()
    calls = 0
    state_controls = 0

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)  # type: ignore[arg-type]
        if path in {target.parent, target.name}:
            tracked.add(descriptor)
        return descriptor

    def track_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in tracked:
            tracked.add(duplicate)
        return duplicate

    def interrupt_twice(
        receipt: object,
        *args: object,
        **kwargs: object,
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise SystemExit(23)
        if calls == 3:
            return False
        return real_publish(receipt, *args, **kwargs)  # type: ignore[arg-type]

    def interrupt_first_owned_probe(receipt: object) -> bool:
        nonlocal state_controls
        if calls == 3 and state_controls == 0:
            state_controls += 1
            raise SystemExit(24)
        return real_owned(receipt)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "dup", track_dup)
    monkeypatch.setattr(
        receipt_module.PrivateFileCleanupReceipt,
        "publish_owned",
        interrupt_twice,
    )
    monkeypatch.setattr(
        receipt_module.PrivateFileCleanupReceipt,
        "owned",
        property(interrupt_first_owned_probe),
    )
    with pytest.raises(SystemExit) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert captured.value.code == 23 and calls == 4 and state_controls == 1
    assert not target.exists() and not traceback_contains(captured.value, secret)
    assert tracked
    for descriptor in tracked:
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF


def test_directory_release_failure_reports_committed_write_and_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "committed-release.bin"
    secret = b"traceback-sentinel-committed-release"
    real_close_owned = receipt_module.close_owned_descriptor
    directory_fd: int | None = None

    def refuse_directory_close(
        descriptor: int,
        identity: tuple[int, int] | None,
        control: object,
    ) -> bool:
        nonlocal directory_fd
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fd = descriptor
            return False
        return real_close_owned(descriptor, identity, control)  # type: ignore[arg-type]

    monkeypatch.setattr(receipt_module, "close_owned_descriptor", refuse_directory_close)
    authority = None
    try:
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as captured:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        authority = captured.value.cleanup_authority
        assert captured.value.material_committed
        assert target.read_bytes() == secret
        assert directory_fd is not None and os.fstat(directory_fd)
        assert not traceback_contains(captured.value, secret)
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_close_owned)
        cleanup_tls_private_authority(authority)
        authority = None
        with pytest.raises(OSError) as closed:
            os.fstat(directory_fd)
        assert closed.value.errno == errno.EBADF
        assert target.read_bytes() == secret
    finally:
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_close_owned)
        if authority is not None:
            cleanup_tls_private_authority(authority)
        target.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_untracked_post_commit_control_reports_committed_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "post-commit-control.bin"
    secret = b"traceback-sentinel-post-commit-control"
    real_release = owner_module.release_private_file_receipt_owned

    def release_then_interrupt(*args: object, **kwargs: object) -> object:
        real_release(*args, **kwargs)
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(
        owner_module,
        "release_private_file_receipt_owned",
        release_then_interrupt,
    )
    authority = None
    try:
        with pytest.raises(control) as captured:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        authority = getattr(captured.value, "cleanup_authority", None)
        assert getattr(captured.value, "code", None) == exit_code
        assert type(authority) is receipt_module.PrivateFileCleanupReceipt
        assert authority.committed and not authority.owned
        assert captured.value.material_committed  # type: ignore[attr-defined]
        assert target.read_bytes() == secret
        assert not traceback_contains(captured.value, secret)
        cleanup_tls_private_authority(authority)
        authority = None
        assert target.read_bytes() == secret
    finally:
        if authority is not None:
            cleanup_tls_private_authority(authority)
        target.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_untracked_release_handoff_control_rolls_back_exact_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / "release-handoff-control.bin"
    secret = b"traceback-sentinel-release-handoff-control"

    def interrupt_release(*_args: object, **_kwargs: object) -> object:
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(
        owner_module,
        "release_private_file_receipt_owned",
        interrupt_release,
    )
    with pytest.raises(control) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == exit_code
    assert not traceback_contains(captured.value, secret)
    assert not target.exists()


def test_concurrent_private_receipt_cleanup_owns_exact_fd_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    target = paths.control_dir / "concurrent-private-receipt.bin"
    unrelated = paths.control_dir / "concurrent-unrelated.bin"
    secret = b"traceback-sentinel-concurrent-private-receipt"
    receipt = receipt_module.new_private_file_cleanup_receipt()
    failed, cleanup_failed, control, recovery = owner_module.write_private_file_owned(
        target,
        secret,
        mode=0o400,
        maximum=1_024,
        cleanup_receipt=receipt,
    )
    assert not failed and not cleanup_failed and control is None and recovery is None
    assert receipt.owned and type(receipt._file_fd) is int
    exact_fd = receipt._file_fd
    unrelated.write_bytes(b"unrelated")
    entered = threading.Event()
    release = threading.Event()
    real_destroy = receipt_module.destroy_owned_file
    calls = 0
    reused_fd: int | None = None

    def destroy_then_reuse(
        descriptor: int,
        identity: tuple[int, int],
        latch: object,
    ) -> bool:
        nonlocal calls, reused_fd
        calls += 1
        destroyed = real_destroy(descriptor, identity, latch)  # type: ignore[arg-type]
        source = os.open(unrelated, os.O_RDONLY)
        if source != exact_fd:
            os.dup2(source, exact_fd)
            os.close(source)
        reused_fd = exact_fd
        entered.set()
        assert release.wait(2)
        return destroyed

    monkeypatch.setattr(receipt_module, "destroy_owned_file", destroy_then_reuse)
    outcomes: list[tuple[bool, object]] = []

    def cleanup() -> None:
        outcomes.append(receipt_module.settle_private_file_receipts_owned((receipt,)))

    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=cleanup)
    try:
        first.start()
        assert entered.wait(2)
        second.start()
        second.join(0.05)
        assert second.is_alive()
        release.set()
        first.join(2)
        second.join(2)
        assert not first.is_alive() and not second.is_alive()
        assert calls == 1 and outcomes == [(False, None), (False, None)]
        assert reused_fd is not None and os.fstat(reused_fd)
        assert not target.exists()
    finally:
        release.set()
        first.join(2)
        second.join(2)
        if reused_fd is not None:
            os.close(reused_fd)


@pytest.mark.parametrize("surface", ["read", "write"])
@pytest.mark.parametrize(
    ("control", "expected_code"),
    [(KeyboardInterrupt, None), (SystemExit, 23)],
)
def test_real_open_then_caller_control_closes_fd_and_joins_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    control: type[KeyboardInterrupt] | type[SystemExit],
    expected_code: int | None,
) -> None:
    target = _paths(tmp_path).control_dir / f"open-control-{surface}.bin"
    secret = b"traceback-sentinel-real-open-control"
    if surface == "read":
        target.write_bytes(secret)
        target.chmod(0o400)
    opened = threading.Event()
    tracked: list[int] = []
    real_open = os.open
    real_wait = worker_module._wait_event
    injected = False
    caller = threading.current_thread()

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if path == target.name and kwargs.get("dir_fd") is not None and not tracked:
            tracked.append(descriptor)
            opened.set()
        return descriptor

    def interrupt_wait(event: threading.Event, latch: object) -> None:
        nonlocal injected
        if threading.current_thread() is caller and not injected:
            assert opened.wait(2)
            injected = True
            if control is KeyboardInterrupt:
                raise KeyboardInterrupt
            raise SystemExit(23)
        real_wait(event, latch)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(worker_module, "_wait_event", interrupt_wait)
    with pytest.raises(control) as captured:
        if surface == "read":
            read_owned_file(target, exact_mode=0o400, maximum=1_024)
        else:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert getattr(captured.value, "code", None) == expected_code
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)
    assert injected and len(tracked) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(tracked[0])
    assert closed.value.errno == errno.EBADF
    assert target.exists() is (surface == "read")
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("surface", ["read", "write"])
def test_close_control_before_close_is_retried_and_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    target = _paths(tmp_path).control_dir / f"close-control-{surface}.bin"
    secret = b"traceback-sentinel-close-control"
    if surface == "read":
        target.write_bytes(secret)
        target.chmod(0o400)
    tracked: list[int] = []
    real_open = os.open
    real_close = os.close
    injected = False

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if path == target.name and kwargs.get("dir_fd") is not None and not tracked:
            tracked.append(descriptor)
        return descriptor

    def interrupt_close(descriptor: int) -> None:
        nonlocal injected
        if tracked and descriptor == tracked[0] and not injected:
            injected = True
            raise SystemExit(23)
        real_close(descriptor)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "close", interrupt_close)
    with pytest.raises(SystemExit) as captured:
        if surface == "read":
            read_owned_file(target, exact_mode=0o400, maximum=1_024)
        else:
            write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert captured.value.code == 23
    assert injected and not traceback_contains(captured.value, secret)
    with pytest.raises(OSError) as closed:
        os.fstat(tracked[0])
    assert closed.value.errno == errno.EBADF
    assert target.exists() is (surface == "read")


@pytest.mark.parametrize("surface", ["read", "write"])
def test_permanent_close_failure_returns_retryable_descriptor_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    target = _paths(tmp_path).control_dir / f"persistent-close-{surface}.bin"
    secret = b"traceback-sentinel-persistent-close"
    if surface == "read":
        target.write_bytes(secret)
        target.chmod(0o400)
    tracked: set[int] = set()
    real_open = os.open
    real_dup = os.dup
    real_close = os.close
    real_closerange = os.closerange

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if path in {target.parent, target.name}:
            tracked.add(descriptor)
        return descriptor

    def track_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in tracked:
            tracked.add(duplicate)
        return duplicate

    def fail_close(descriptor: int) -> None:
        if descriptor in tracked:
            raise OSError
        real_close(descriptor)

    def fail_closerange(first: int, last: int) -> None:
        if any(first <= descriptor < last for descriptor in tracked):
            raise OSError
        real_closerange(first, last)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "dup", track_dup)
    monkeypatch.setattr(os, "close", fail_close)
    monkeypatch.setattr(os, "closerange", fail_closerange)
    authority = None
    try:
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as captured:
            if surface == "read":
                read_owned_file(target, exact_mode=0o400, maximum=1_024)
            else:
                write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        authority = captured.value.cleanup_authority
        assert "Authority()" in repr(authority) or "Receipt()" in repr(authority)
        assert tracked and all(os.fstat(descriptor) for descriptor in tracked)
        assert not traceback_contains(captured.value, secret)
        monkeypatch.setattr(os, "close", real_close)
        monkeypatch.setattr(os, "closerange", real_closerange)
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as quarantined:
            cleanup_tls_private_authority(authority)
        assert quarantined.value.cleanup_authority is authority
        _clear_synthetic_close_quarantine(*tracked)
        cleanup_tls_private_authority(authority)
        for descriptor in tracked:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
        assert target.exists() is (surface == "read")
    finally:
        monkeypatch.setattr(os, "close", real_close)
        monkeypatch.setattr(os, "closerange", real_closerange)
        if authority is not None:
            _clear_synthetic_close_quarantine(*tracked)
            try:
                cleanup_tls_private_authority(authority)
            except CoturnTlsPrivateCleanupRequired:
                pass
        target.unlink(missing_ok=True)


def test_reader_retries_control_during_descriptor_authority_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "reader-publication-control.bin"
    secret = b"traceback-sentinel-reader-publication-control"
    target.write_bytes(secret)
    target.chmod(0o400)
    real_open = os.open
    real_publish = receipt_module.PrivateDescriptorCleanupAuthority.publish
    real_owned = receipt_module.PrivateDescriptorCleanupAuthority.owned.fget
    assert real_owned is not None
    tracked: list[int] = []
    calls = 0
    state_controls = 0

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)  # type: ignore[arg-type]
        if path in {target.parent, target.name}:
            tracked.append(descriptor)
        return descriptor

    def refuse_local_close(
        _descriptor: int,
        _identity: tuple[int, int] | None,
        _control: object,
    ) -> bool:
        return False

    def interrupt_publish(
        authority: object,
        descriptors: object,
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit(23)
        if calls == 2:
            return False
        return real_publish(authority, descriptors)  # type: ignore[arg-type]

    def interrupt_first_owned_probe(authority: object) -> bool:
        nonlocal state_controls
        if calls == 2 and state_controls == 0:
            state_controls += 1
            raise SystemExit(24)
        return real_owned(authority)  # type: ignore[arg-type]

    monkeypatch.setattr(reader_module.os, "open", track_open)
    monkeypatch.setattr(reader_module, "close_owned_descriptor", refuse_local_close)
    monkeypatch.setattr(
        receipt_module.PrivateDescriptorCleanupAuthority,
        "publish",
        interrupt_publish,
    )
    monkeypatch.setattr(
        receipt_module.PrivateDescriptorCleanupAuthority,
        "owned",
        property(interrupt_first_owned_probe),
    )
    with pytest.raises(SystemExit) as captured:
        read_owned_file(target, exact_mode=0o400, maximum=1_024)
    assert captured.value.code == 23 and calls == 3 and state_controls == 1
    assert tracked and not traceback_contains(captured.value, secret)
    for descriptor in tracked:
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_writer_retries_control_during_directory_authority_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "writer-directory-publication-control.bin"
    secret = b"traceback-sentinel-writer-directory-publication-control"
    real_open_directory = owner_module._open_directory
    real_publish = receipt_module.PrivateFileCleanupReceipt.publish_directory_only
    real_owned = receipt_module.PrivateFileCleanupReceipt.owned.fget
    assert real_owned is not None
    directory_fd: int | None = None
    calls = 0
    state_controls = 0

    def open_then_reject(path: Path, control: object) -> tuple[int, object, bool]:
        nonlocal directory_fd
        descriptor, identity, safe = real_open_directory(path, control)  # type: ignore[arg-type]
        assert safe
        directory_fd = descriptor
        return descriptor, identity, False

    def refuse_directory_close(
        descriptor: int,
        _identity: tuple[int, int] | None,
        _control: object,
    ) -> bool:
        return descriptor != directory_fd

    def interrupt_publish(
        receipt: object,
        descriptor: int,
        identity: tuple[int, int] | None,
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit(23)
        if calls == 2:
            return False
        return real_publish(receipt, descriptor, identity)  # type: ignore[arg-type]

    def interrupt_first_owned_probe(receipt: object) -> bool:
        nonlocal state_controls
        if calls == 2 and state_controls == 0:
            state_controls += 1
            raise SystemExit(24)
        return real_owned(receipt)  # type: ignore[arg-type]

    monkeypatch.setattr(owner_module, "_open_directory", open_then_reject)
    monkeypatch.setattr(owner_module, "close_owned_descriptor", refuse_directory_close)
    monkeypatch.setattr(
        receipt_module.PrivateFileCleanupReceipt,
        "publish_directory_only",
        interrupt_publish,
    )
    monkeypatch.setattr(
        receipt_module.PrivateFileCleanupReceipt,
        "owned",
        property(interrupt_first_owned_probe),
    )
    with pytest.raises(SystemExit) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert captured.value.code == 23 and calls == 3 and state_controls == 1
    assert directory_fd is not None and not target.exists()
    assert not traceback_contains(captured.value, secret)
    with pytest.raises(OSError) as closed:
        os.fstat(directory_fd)
    assert closed.value.errno == errno.EBADF
    assert not any(
        thread.name == "coturn-tls-private-owner" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("surface", ["read", "write"])
def test_precreate_directory_close_failure_retains_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    target = _paths(tmp_path).control_dir / f"precreate-directory-{surface}.bin"
    secret = b"traceback-sentinel-precreate-directory"
    module = reader_module if surface == "read" else owner_module
    if surface == "read":
        target.write_bytes(secret)
        target.chmod(0o400)
    real_open_directory = module._open_directory
    real_local_close = module.close_owned_descriptor
    real_receipt_close = receipt_module.close_owned_descriptor
    directory_fd: int | None = None

    def open_then_reject(path: Path, control: object) -> tuple[int, object, bool]:
        nonlocal directory_fd
        descriptor, identity, safe = real_open_directory(path, control)  # type: ignore[arg-type]
        assert safe
        directory_fd = descriptor
        return descriptor, identity, False

    def refuse_directory_close(
        descriptor: int,
        identity: tuple[int, int] | None,
        control: object,
    ) -> bool:
        if descriptor == directory_fd:
            return False
        return real_receipt_close(descriptor, identity, control)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_open_directory", open_then_reject)
    monkeypatch.setattr(module, "close_owned_descriptor", refuse_directory_close)
    monkeypatch.setattr(receipt_module, "close_owned_descriptor", refuse_directory_close)
    authority = None
    try:
        with pytest.raises(CoturnTlsPrivateCleanupRequired) as captured:
            if surface == "read":
                read_owned_file(target, exact_mode=0o400, maximum=1_024)
            else:
                write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
        authority = captured.value.cleanup_authority
        assert directory_fd is not None and os.fstat(directory_fd)
        assert not traceback_contains(captured.value, secret)
        monkeypatch.setattr(module, "close_owned_descriptor", real_local_close)
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_receipt_close)
        cleanup_tls_private_authority(authority)
        authority = None
        with pytest.raises(OSError) as closed:
            os.fstat(directory_fd)
        assert closed.value.errno == errno.EBADF
        assert target.exists() is (surface == "read")
    finally:
        monkeypatch.setattr(module, "close_owned_descriptor", real_local_close)
        monkeypatch.setattr(receipt_module, "close_owned_descriptor", real_receipt_close)
        if authority is not None:
            cleanup_tls_private_authority(authority)
        target.unlink(missing_ok=True)


def test_more_than_two_controls_do_not_consume_close_or_unlink_retry_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "repeated-controls.bin"
    secret = b"traceback-sentinel-repeated-controls"
    tracked: list[int] = []
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    real_closerange = os.closerange
    real_stat = os.stat
    real_unlink = os.unlink
    close_started = False
    fstat_controls = 4
    closerange_controls = 4
    stat_controls = 4
    unlink_controls = 4

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = real_open(path, *args, **kwargs)
        if path == target.name and kwargs.get("dir_fd") is not None and not tracked:
            tracked.append(descriptor)
        return descriptor

    def interrupt_close(descriptor: int) -> None:
        nonlocal close_started
        if tracked and descriptor == tracked[0]:
            close_started = True
            raise KeyboardInterrupt
        real_close(descriptor)

    def interrupt_fstat(descriptor: int) -> os.stat_result:
        nonlocal fstat_controls
        if close_started and tracked and descriptor == tracked[0] and fstat_controls:
            fstat_controls -= 1
            if fstat_controls % 2:
                raise SystemExit(23)
            raise KeyboardInterrupt
        return real_fstat(descriptor)

    def interrupt_closerange(low: int, high: int) -> None:
        nonlocal closerange_controls
        if tracked and low == tracked[0] and closerange_controls:
            closerange_controls -= 1
            if closerange_controls % 2:
                raise SystemExit(23)
            raise KeyboardInterrupt
        real_closerange(low, high)

    def fail_write(_descriptor: int, _value: object) -> int:
        return 0

    def interrupt_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal stat_controls
        if path == target.name and kwargs.get("dir_fd") is not None and stat_controls:
            stat_controls -= 1
            if stat_controls % 2:
                raise SystemExit(23)
            raise KeyboardInterrupt
        return real_stat(path, *args, **kwargs)

    def interrupt_unlink(path: object, *args: object, **kwargs: object) -> None:
        nonlocal unlink_controls
        if path == target.name and kwargs.get("dir_fd") is not None and unlink_controls:
            unlink_controls -= 1
            if unlink_controls % 2:
                raise SystemExit(23)
            raise KeyboardInterrupt
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "close", interrupt_close)
    monkeypatch.setattr(os, "fstat", interrupt_fstat)
    monkeypatch.setattr(os, "closerange", interrupt_closerange)
    monkeypatch.setattr(os, "write", fail_write)
    monkeypatch.setattr(os, "stat", interrupt_stat)
    monkeypatch.setattr(os, "unlink", interrupt_unlink)
    with pytest.raises(KeyboardInterrupt) as captured:
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert not traceback_contains(captured.value, secret)
    assert (fstat_controls, closerange_controls, stat_controls, unlink_controls) == (0, 0, 0, 0)
    with pytest.raises(OSError) as closed:
        real_fstat(tracked[0])
    assert closed.value.errno == errno.EBADF
    assert not target.exists()


def test_rename_away_cleanup_removes_original_inode_and_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _paths(tmp_path).control_dir
    target = directory / "rename-target.bin"
    moved = directory / "renamed-secret.bin"
    secret = b"traceback-sentinel-renamed-secret"
    victim = b"replacement-victim"
    real_open = os.open
    real_close = os.close
    file_fd: int | None = None
    directory_fd: int | None = None
    original_inode: tuple[int, int] | None = None
    swapped = False

    def track_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal directory_fd, file_fd, original_inode
        descriptor = real_open(path, *args, **kwargs)
        if path == directory:
            directory_fd = descriptor
        elif path == target.name and kwargs.get("dir_fd") is not None and file_fd is None:
            file_fd = descriptor
            details = os.fstat(descriptor)
            original_inode = (details.st_dev, details.st_ino)
        return descriptor

    def swap_after_file_close(descriptor: int) -> None:
        nonlocal swapped
        real_close(descriptor)
        if descriptor != file_fd or swapped:
            return
        assert directory_fd is not None
        swapped = True
        os.rename(
            target.name,
            moved.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replacement = real_open(
            target.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
            dir_fd=directory_fd,
        )
        os.write(replacement, victim)
        os.fchmod(replacement, 0o400)
        real_close(replacement)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "close", swap_after_file_close)
    with pytest.raises(CoturnTlsError, match=r"^Coturn private file creation failed$"):
        write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert swapped and original_inode is not None
    assert target.read_bytes() == victim
    assert not moved.exists()
    assert all(
        (details.st_dev, details.st_ino) != original_inode
        for details in (path.stat(follow_symlinks=False) for path in directory.iterdir())
    )


@pytest.mark.parametrize("kind", ["unexpected", "keyboard", "exit"])
def test_readiness_parser_failures_discard_result_exception_graphs(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    secret = b"traceback-sentinel-readiness-parser-result"
    namespace = {"KIND": kind}
    exec(
        compile(
            "def hostile(result):\n"
            "    private = result.stdout + result.stderr\n"
            "    if KIND == 'keyboard':\n"
            "        raise KeyboardInterrupt\n"
            "    if KIND == 'exit':\n"
            "        raise SystemExit(23)\n"
            "    raise RuntimeError('synthetic')\n",
            "/synthetic/tls_readiness_parser.py",
            "exec",
        ),
        namespace,
    )
    monkeypatch.setattr(tls_module, "_parse_openssl_readiness_result", namespace["hostile"])
    expected = (
        CoturnTlsError
        if kind == "unexpected"
        else (KeyboardInterrupt if kind == "keyboard" else SystemExit)
    )
    with pytest.raises(expected) as captured:
        validate_openssl_readiness_result(_result(secret, stderr=secret))
    if kind == "exit":
        assert captured.value.code == 23
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)


def test_tls_exception_scanner_traverses_nested_exception_and_source_graphs() -> None:
    secret = b"traceback-sentinel-nested-exception-graph"
    inner = RuntimeError(secret)
    grouped = ExceptionGroup("fixed", [inner])
    outer = RuntimeError("fixed")
    outer.__cause__ = grouped
    outer.source = SimpleNamespace(payload=secret)  # type: ignore[attr-defined]
    assert traceback_contains(outer, secret)


def test_completed_owner_thread_and_task_graphs_retain_no_private_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _paths(tmp_path).control_dir / "owner-scrub.bin"
    secret = b"traceback-sentinel-owner-thread-state"
    real_thread = threading.Thread
    captured_threads: list[threading.Thread] = []

    def record_thread(*args: object, **kwargs: object) -> threading.Thread:
        thread = real_thread(*args, **kwargs)
        if thread.name == "coturn-tls-private-owner":
            captured_threads.append(thread)
        return thread

    monkeypatch.setattr(worker_module.threading, "Thread", record_thread)
    write_owned_file_exclusive(target, secret, mode=0o400, maximum=1_024)
    assert captured_threads and all(not thread.is_alive() for thread in captured_threads)
    assert all(getattr(thread, "_target", None) is None for thread in captured_threads)
    assert all(getattr(thread, "_args", ()) == () for thread in captured_threads)
    assert all(getattr(thread, "_kwargs", {}) == {} for thread in captured_threads)
    probe = RuntimeError("fixed")
    probe.owner_threads = tuple(captured_threads)  # type: ignore[attr-defined]
    assert not traceback_contains(probe, secret)


def test_invalid_private_cleanup_authority_is_dropped_before_fixed_error() -> None:
    secret = b"traceback-sentinel-invalid-private-authority"
    hostile = SimpleNamespace(payload=secret)

    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn private file cleanup failed$",
    ) as captured:
        cleanup_tls_private_authority(hostile)
    assert type(captured.value) is CoturnTlsError
    assert not traceback_contains(captured.value, secret)


def test_directory_listing_stops_at_bounded_cap_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EndlessDirectory:
        def __init__(self) -> None:
            self.closed = False
            self.consumed = 0

        def __next__(self) -> SimpleNamespace:
            self.consumed += 1
            return SimpleNamespace(name=f"entry-{self.consumed}")

        def close(self) -> None:
            self.closed = True

    directory = EndlessDirectory()
    monkeypatch.setattr(cleanup_module.os, "scandir", lambda _fd: directory)
    names = cleanup_module._list_directory(123, worker_module.TlsControlLatch())
    assert names is not None and len(names) == 257
    assert directory.consumed == 257 and directory.closed


def test_successful_descriptor_close_does_not_reprobe_reused_same_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _paths(tmp_path).control_dir
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    identity = cleanup_module.file_identity(os.fstat(descriptor))
    real_close = os.close
    reused = False

    def close_then_reuse(candidate: int) -> None:
        nonlocal reused
        real_close(candidate)
        if candidate != descriptor or reused:
            return
        source = os.open(directory, flags)
        if source != descriptor:
            os.dup2(source, descriptor)
            real_close(source)
        reused = True

    monkeypatch.setattr(cleanup_module.os, "close", close_then_reuse)
    try:
        assert cleanup_module.close_owned_descriptor(
            descriptor,
            identity,
            worker_module.TlsControlLatch(),
        )
        assert reused
        assert cleanup_module.file_identity(os.fstat(descriptor)) == identity
    finally:
        monkeypatch.setattr(cleanup_module.os, "close", real_close)
        try:
            real_close(descriptor)
        except OSError:
            pass


def test_ambiguous_descriptor_close_quarantines_same_inode_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _paths(tmp_path).control_dir
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    identity = cleanup_module.file_identity(os.fstat(descriptor))
    real_close = os.close
    reused = False

    def close_reuse_then_fail(candidate: int) -> None:
        nonlocal reused
        real_close(candidate)
        source = os.open(directory, flags)
        if source != candidate:
            os.dup2(source, candidate)
            real_close(source)
        reused = True
        raise OSError("synthetic ambiguous close")

    monkeypatch.setattr(cleanup_module.os, "close", close_reuse_then_fail)
    try:
        assert not cleanup_module.close_owned_descriptor(
            descriptor,
            identity,
            worker_module.TlsControlLatch(),
        )
        assert reused and cleanup_module.file_identity(os.fstat(descriptor)) == identity
        monkeypatch.setattr(cleanup_module.os, "close", real_close)
        assert not cleanup_module.close_owned_descriptor(
            descriptor,
            identity,
            worker_module.TlsControlLatch(),
        )
        assert os.fstat(descriptor)
        real_close(descriptor)
        assert cleanup_module.close_owned_descriptor(
            descriptor,
            identity,
            worker_module.TlsControlLatch(),
        )
    finally:
        monkeypatch.setattr(cleanup_module.os, "close", real_close)
        try:
            real_close(descriptor)
        except OSError:
            pass
        cleanup_module.close_owned_descriptor(
            descriptor,
            identity,
            worker_module.TlsControlLatch(),
        )


@pytest.mark.parametrize(
    ("control", "exit_code"),
    [(KeyboardInterrupt, None), (SystemExit, None), (SystemExit, 23)],
)
def test_successful_runner_request_scrub_retries_and_propagates_control(
    monkeypatch: pytest.MonkeyPatch,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    secret = b"traceback-sentinel-runner-request-scrub"
    request = private_module.CommandRequest(
        argv=("/usr/bin/openssl", "version"),
        stdin=secret,
    )
    runner = QueueRunner([_result(b"ok")])
    real_object = object

    class OneShotObject:
        injected = False

        @staticmethod
        def __setattr__(target: object, name: str, value: object) -> None:
            if not OneShotObject.injected:
                OneShotObject.injected = True
                if control is KeyboardInterrupt:
                    raise KeyboardInterrupt
                raise SystemExit(exit_code)
            real_object.__setattr__(target, name, value)

    monkeypatch.setattr(private_module, "object", OneShotObject, raising=False)
    with pytest.raises(control) as captured:
        private_module.execute_tls_checked(
            runner,
            request,
            failure="Coturn synthetic runner failed",
        )
    assert OneShotObject.injected
    assert getattr(captured.value, "code", None) == exit_code
    assert runner.requests == [request]
    assert request.stdin == b"" and request.argv == ()
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, secret)


def test_successful_runner_request_scrub_failure_never_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"traceback-sentinel-runner-request-scrub-failure"
    request = private_module.CommandRequest(
        argv=("/usr/bin/openssl", "version"),
        stdin=secret,
    )
    runner = QueueRunner([_result(b"ok")])

    class RefusingObject:
        @staticmethod
        def __setattr__(_target: object, _name: str, _value: object) -> None:
            raise RuntimeError("synthetic")

    monkeypatch.setattr(private_module, "object", RefusingObject, raising=False)
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn synthetic runner failed$",
    ) as captured:
        private_module.execute_tls_checked(
            runner,
            request,
            failure="Coturn synthetic runner failed",
        )
    assert request.stdin == secret
    assert not traceback_contains(captured.value, secret)

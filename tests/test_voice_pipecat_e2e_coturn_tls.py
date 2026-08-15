"""Synthetic exclusive-file and TLS/SPKI tests; no OpenSSL is executed."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn import (  # noqa: E402
    COTURN_FIXTURE_PATH,
    CoturnBridgeTopology,
    read_private_coturn_configuration_receipt,
)
from scripts.voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CoturnTlsError,
    TlsMaterialReceipt,
    build_openssl_readiness_request,
    generate_tls_and_config_material,
    read_owned_file,
    validate_tls_material,
    write_owned_file_exclusive,
)
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
    real_stat = Path.stat
    injected = False

    def unsafe_final_stat(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal injected
        if path == target and not injected:
            injected = True
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=7,
            )
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unsafe_final_stat)
    with pytest.raises(CoturnTlsError, match="creation failed"):
        write_owned_file_exclusive(target, b"bounded", mode=0o400, maximum=16)
    assert not target.exists()

    leftover = paths.control_dir / "write-failure.bin"
    real_unlink = Path.unlink
    monkeypatch.setattr(os, "write", lambda _fd, _value: 0)

    def refuse_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == leftover:
            raise OSError("refused")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    try:
        with pytest.raises(CoturnTlsError, match=r"^Coturn private file cleanup failed$"):
            write_owned_file_exclusive(leftover, b"bounded", mode=0o400, maximum=16)
        assert leftover.exists()
    finally:
        real_unlink(leftover, missing_ok=True)


def test_exclusive_write_rejects_same_owner_swap_after_descriptor_close(
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
    assert not target.exists()
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
    assert runner.requests[-1].stdin == PUBLIC_KEY
    assert all(
        request.environment == (("LANG", "C"), ("LC_ALL", "C")) for request in runner.requests
    )
    fixture = COTURN_FIXTURE_PATH.read_text()
    assert fixture.count("verbose") == fixture.count("log-min-level=info") == 1


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

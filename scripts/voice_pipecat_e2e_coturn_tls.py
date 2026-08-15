"""Exclusive private-file and TLS/SPKI contracts for Coturn E2E."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_FIXTURE_PATH,
    COTURN_TLS_PORT,
    CoturnBridgeTopology,
    render_coturn_configuration,
    validate_turn_tls_ca_file,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandResult,
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
    execute_checked,
    require_owned_directory,
    require_safe_path,
)

_PEM_PRIVATE_KEY = re.compile(
    rb"-----BEGIN PRIVATE KEY-----\n(?:[A-Za-z0-9+/=]{1,64}\n)+"
    rb"-----END PRIVATE KEY-----\n?"
)
_PEM_CERTIFICATE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\n(?:[A-Za-z0-9+/=]{1,64}\n)+"
    rb"-----END CERTIFICATE-----\n?"
)
_PEM_PUBLIC_KEY = re.compile(
    rb"-----BEGIN PUBLIC KEY-----\n(?:[A-Za-z0-9+/=]{1,64}\n)+"
    rb"-----END PUBLIC KEY-----\n?"
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SPKI_B64 = re.compile(r"^[A-Za-z0-9+/]{43}=$")
_MAX_KEY_BYTES = 16_384
_MAX_CERTIFICATE_BYTES = 65_536
_RECEIPT_TOKEN = object()


class CoturnTlsError(RuntimeError):
    """Private material or its TLS relation is malformed or unsafe."""


class TlsMaterialReceipt:
    __slots__ = ("_certificate_sha256", "_not_after", "_not_before", "_pin")

    def __init__(
        self,
        token: object,
        *,
        certificate_sha256: str,
        chromium_spki_sha256_b64: str,
        not_before: datetime,
        not_after: datetime,
    ) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Coturn TLS receipt is factory-owned")
        if (
            not _SHA256_HEX.fullmatch(certificate_sha256)
            or not _SPKI_B64.fullmatch(chromium_spki_sha256_b64)
            or not_before.tzinfo is None
            or not_after.tzinfo is None
            or not_before >= not_after
        ):
            raise CoturnTlsError("Coturn TLS receipt is invalid")
        self._certificate_sha256 = certificate_sha256
        self._pin = chromium_spki_sha256_b64
        self._not_before = not_before
        self._not_after = not_after

    @property
    def certificate_sha256(self) -> str:
        return self._certificate_sha256

    @property
    def chromium_spki_sha256_b64(self) -> str:
        return self._pin

    @property
    def not_before(self) -> datetime:
        return self._not_before

    @property
    def not_after(self) -> datetime:
        return self._not_after

    def __repr__(self) -> str:
        return "TlsMaterialReceipt()"


def generate_tls_and_config_material(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
    static_auth_secret: object,
    now: datetime,
) -> TlsMaterialReceipt:
    """Generate owner-only key/cert/config and validate their exact relation."""

    _require_utc(now)
    require_owned_directory(paths.contract.run_dir)
    require_owned_directory(paths.contract.coturn_dir)
    material = (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    if any(path.exists() or path.is_symlink() for path in material):
        raise CoturnTlsError("Coturn TLS material already exists")
    key = execute_checked(
        runner,
        CommandRequest(
            argv=(
                os.fspath(tools.openssl),
                "genpkey",
                "-quiet",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
            ),
            timeout_seconds=15.0,
            maximum_output_bytes=_MAX_KEY_BYTES,
        ),
        failure="Coturn private-key generation failed",
    )
    if key.stderr or not _PEM_PRIVATE_KEY.fullmatch(key.stdout):
        raise CoturnTlsError("Coturn private-key generation failed")
    certificate = execute_checked(
        runner,
        CommandRequest(
            argv=(
                os.fspath(tools.openssl),
                "req",
                "-new",
                "-x509",
                "-key",
                "/dev/stdin",
                "-sha256",
                "-days",
                "1",
                "-subj",
                "/CN=murmur-coturn-loopback.invalid",
                "-addext",
                "subjectAltName=critical,IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:FALSE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "-addext",
                "extendedKeyUsage=serverAuth",
            ),
            stdin=key.stdout,
            timeout_seconds=15.0,
            maximum_output_bytes=_MAX_CERTIFICATE_BYTES,
        ),
        failure="Coturn certificate generation failed",
    )
    if certificate.stderr or not _PEM_CERTIFICATE.fullmatch(certificate.stdout):
        raise CoturnTlsError("Coturn certificate generation failed")
    created: list[Path] = []
    try:
        for path, value, maximum in (
            (paths.contract.private_key, key.stdout, _MAX_KEY_BYTES),
            (paths.contract.cert, certificate.stdout, _MAX_CERTIFICATE_BYTES),
            (
                paths.contract.config,
                render_coturn_configuration(
                    COTURN_FIXTURE_PATH.read_text(encoding="utf-8"),
                    static_auth_secret,
                    topology,
                ).encode("utf-8"),
                8_192,
            ),
        ):
            write_owned_file_exclusive(path, value, mode=0o400, maximum=maximum)
            created.append(path)
        return validate_tls_material(runner=runner, tools=tools, paths=paths, now=now)
    except BaseException:
        cleanup_failed = False
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                cleanup_failed = True
        if cleanup_failed or any(path.exists() or path.is_symlink() for path in created):
            raise CoturnTlsError("Coturn TLS cleanup failed") from None
        raise


def validate_tls_material(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    now: datetime,
) -> TlsMaterialReceipt:
    """Validate SAN, validity, key relation, and Chromium's DER-SPKI pin."""

    _require_utc(now)
    validate_turn_tls_ca_file(paths.contract.cert, expected_run_dir=paths.contract.run_dir)
    certificate = read_owned_file(
        paths.contract.cert,
        exact_mode=0o400,
        maximum=_MAX_CERTIFICATE_BYTES,
    )
    key = read_owned_file(
        paths.contract.private_key,
        exact_mode=0o400,
        maximum=_MAX_KEY_BYTES,
    )
    dates = _openssl_stdin(runner, tools, "x509", certificate, "-noout", "-dates")
    san = _openssl_stdin(
        runner,
        tools,
        "x509",
        certificate,
        "-noout",
        "-ext",
        "subjectAltName",
    )
    certificate_public = _openssl_stdin(
        runner,
        tools,
        "x509",
        certificate,
        "-noout",
        "-pubkey",
    )
    private_public = _openssl_stdin(runner, tools, "pkey", key, "-pubout")
    if (
        any(result.stderr for result in (dates, san, certificate_public, private_public))
        or san.stdout != b"X509v3 Subject Alternative Name: critical\n    IP Address:127.0.0.1\n"
        or not _PEM_PUBLIC_KEY.fullmatch(certificate_public.stdout)
        or certificate_public.stdout != private_public.stdout
    ):
        raise CoturnTlsError("Coturn TLS material is invalid")
    der = _openssl_stdin(
        runner,
        tools,
        "pkey",
        certificate_public.stdout,
        "-pubin",
        "-outform",
        "DER",
    )
    if der.stderr or not 1 <= len(der.stdout) <= 4_096:
        raise CoturnTlsError("Coturn TLS material is invalid")
    not_before, not_after = _parse_openssl_dates(dates.stdout)
    if (
        not now - timedelta(minutes=5) <= not_before <= now + timedelta(minutes=1)
        or not now + timedelta(minutes=5) <= not_after <= now + timedelta(hours=25)
        or not_after - not_before > timedelta(hours=25)
    ):
        raise CoturnTlsError("Coturn TLS validity is invalid")
    return TlsMaterialReceipt(
        _RECEIPT_TOKEN,
        certificate_sha256=hashlib.sha256(certificate).hexdigest(),
        chromium_spki_sha256_b64=base64.b64encode(hashlib.sha256(der.stdout).digest()).decode(
            "ascii"
        ),
        not_before=not_before,
        not_after=not_after,
    )


def build_openssl_readiness_request(
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CommandRequest:
    return CommandRequest(
        argv=(
            os.fspath(tools.openssl),
            "s_client",
            "-connect",
            f"127.0.0.1:{COTURN_TLS_PORT}",
            "-CAfile",
            os.fspath(paths.contract.cert),
            "-verify_ip",
            "127.0.0.1",
            "-verify_return_error",
            "-brief",
        ),
        timeout_seconds=5.0,
        maximum_output_bytes=65_536,
    )


def read_owned_file(path: Path, *, exact_mode: int, maximum: int) -> bytes:
    """Read a same-owner file while binding path and fd metadata."""

    require_safe_path(path)
    try:
        before = path.stat(follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError:
        raise CoturnTlsError("Coturn private file is unavailable") from None
    try:
        opened = os.fstat(descriptor)
        if not _safe_file(before, exact_mode, maximum) or not _safe_file(
            opened,
            exact_mode,
            maximum,
        ):
            raise OSError("unsafe file")
        chunks: list[bytes] = []
        length = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum:
                raise OSError("oversized file")
        value = b"".join(chunks)
        metadata = (before, opened, os.fstat(descriptor), path.stat(follow_symlinks=False))
        if (
            any(not _safe_file(item, exact_mode, maximum) for item in metadata)
            or len({(item.st_dev, item.st_ino) for item in metadata}) != 1
            or {item.st_size for item in metadata} != {len(value)}
        ):
            raise OSError("unsafe file")
        return value
    except OSError:
        raise CoturnTlsError("Coturn private file is unavailable") from None
    finally:
        os.close(descriptor)


def write_owned_file_exclusive(
    path: Path,
    value: bytes,
    *,
    mode: int,
    maximum: int,
) -> None:
    """Create one owner-only file without following links."""

    if not value or len(value) > maximum or mode not in {0o400, 0o600}:
        raise CoturnTlsError("Coturn private file content is invalid")
    require_safe_path(path)
    require_owned_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
    except OSError:
        raise CoturnTlsError("Coturn private file creation failed") from None
    failure: BaseException | None = None
    written_details: os.stat_result | None = None
    try:
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        written_details = os.fstat(descriptor)
        if not _safe_file(written_details, mode, maximum):
            raise OSError("unsafe file")
    except BaseException as exc:
        failure = exc
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            failure = exc
    if failure is not None:
        try:
            path.unlink()
        except OSError:
            raise CoturnTlsError("Coturn private file cleanup failed") from None
        if path.exists() or path.is_symlink():
            raise CoturnTlsError("Coturn private file cleanup failed") from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise failure
        raise CoturnTlsError("Coturn private file creation failed") from None
    try:
        details = path.stat(follow_symlinks=False)
        if (
            written_details is None
            or not _safe_file(details, mode, maximum)
            or details.st_size != len(value)
            or (details.st_dev, details.st_ino) != (written_details.st_dev, written_details.st_ino)
        ):
            raise OSError("unsafe file")
    except OSError:
        try:
            path.unlink()
        except OSError:
            raise CoturnTlsError("Coturn private file cleanup failed") from None
        if path.exists() or path.is_symlink():
            raise CoturnTlsError("Coturn private file cleanup failed") from None
        raise CoturnTlsError("Coturn private file creation failed") from None


def _safe_file(value: os.stat_result, mode: int, maximum: int) -> bool:
    return bool(
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == mode
        and 0 <= value.st_size <= maximum
    )


def _openssl_stdin(
    runner: CommandRunner,
    tools: TrustedHostTools,
    command: str,
    stdin: bytes,
    *arguments: str,
) -> CommandResult:
    if command not in {"x509", "pkey"}:
        raise CoturnTlsError("Coturn OpenSSL request is invalid")
    return execute_checked(
        runner,
        CommandRequest(
            argv=(os.fspath(tools.openssl), command, "-in", "/dev/stdin", *arguments),
            stdin=stdin,
            maximum_output_bytes=65_536,
        ),
        failure="Coturn TLS validation failed",
    )


def _parse_openssl_dates(value: bytes) -> tuple[datetime, datetime]:
    try:
        lines = value.decode("ascii").splitlines()
        if (
            len(lines) != 2
            or not lines[0].startswith("notBefore=")
            or not lines[1].startswith("notAfter=")
        ):
            raise ValueError
        format_value = "%b %d %H:%M:%S %Y GMT"
        before = datetime.strptime(lines[0].removeprefix("notBefore="), format_value).replace(
            tzinfo=UTC
        )
        after = datetime.strptime(lines[1].removeprefix("notAfter="), format_value).replace(
            tzinfo=UTC
        )
    except (UnicodeError, ValueError):
        raise CoturnTlsError("Coturn TLS validity is invalid") from None
    return before, after


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CoturnTlsError("Coturn TLS clock is invalid")


__all__ = [
    "CoturnTlsError",
    "TlsMaterialReceipt",
    "build_openssl_readiness_request",
    "generate_tls_and_config_material",
    "read_owned_file",
    "validate_tls_material",
    "write_owned_file_exclusive",
]

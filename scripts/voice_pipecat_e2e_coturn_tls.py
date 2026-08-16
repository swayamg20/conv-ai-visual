"""Exclusive private-file and TLS/SPKI contracts for Coturn E2E."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
from datetime import UTC, datetime, timedelta

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
    require_owned_directory,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    CoturnTlsCleanupRequired,
    TlsCleanupAuthority,
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
    combine_tls_cleanup_authorities,
    new_tls_material_lifetime_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_private import (
    ControlSignal,
    CoturnTlsError,
    CoturnTlsPrivateCleanupRequired,
    PrivateFileCleanupReceipt,
    cleanup_tls_private_authority,
    control_signal,
    execute_tls_checked,
    private_cleanup_authority,
    raise_control,
    raise_tls,
    read_owned_file,
    write_owned_file_exclusive,
    write_owned_file_exclusive_tracked,
)
from scripts.voice_pipecat_e2e_coturn_tls_readiness import (
    OpenSslReadinessReceipt,
)
from scripts.voice_pipecat_e2e_coturn_tls_readiness import (
    parse_openssl_readiness_result as _parse_openssl_readiness_result,
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
_SAFE_TLS_FAILURES = {
    "Coturn TLS material already exists",
    "Coturn private-key generation failed",
    "Coturn certificate generation failed",
    "Coturn TLS cleanup failed",
    "Coturn TLS material is invalid",
    "Coturn TLS validity is invalid",
    "Coturn private file is unavailable",
    "Coturn TLS validation failed",
}


class TlsMaterialReceipt:
    __slots__ = (
        "_certificate_sha256",
        "_lifetime",
        "_lifetime_lock",
        "_not_after",
        "_not_before",
        "_pin",
    )

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
        self._lifetime: TlsMaterialLifetimeAuthority | None = None
        self._lifetime_lock = threading.Lock()

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

    @property
    def has_cleanup_authority(self) -> bool:
        with self._lifetime_lock:
            return self._lifetime is not None and self._lifetime.retained

    def _bind_lifetime(self, lifetime: TlsMaterialLifetimeAuthority) -> bool:
        with self._lifetime_lock:
            if (
                self._lifetime is not None
                or type(lifetime) is not TlsMaterialLifetimeAuthority
                or not lifetime.retain()
            ):
                return False
            self._lifetime = lifetime
            return True

    def _cleanup_lifetime(
        self,
    ) -> tuple[bool, ControlSignal | None, TlsMaterialLifetimeAuthority | None]:
        with self._lifetime_lock:
            if self._lifetime is None:
                return True, None, None
            authority = self._lifetime
            failed, control = authority.cleanup()
            if not failed:
                self._lifetime = None
                authority = None
            return failed, control, authority

    def _retained_lifetime(self) -> TlsMaterialLifetimeAuthority | None:
        with self._lifetime_lock:
            return self._lifetime

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

    receipt: TlsMaterialReceipt | None = None
    message: str | None = None
    control: ControlSignal | None = None
    lifetime: TlsMaterialLifetimeAuthority | None = None
    private_recovery: object | None = None
    recovery_adopted = False
    try:
        lifetime = new_tls_material_lifetime_authority()
        receipt = _generate_tls_and_config_material(
            runner=runner,
            tools=tools,
            paths=paths,
            topology=topology,
            static_auth_secret=static_auth_secret,
            now=now,
            lifetime=lifetime,
        )
        if not receipt.has_cleanup_authority:
            raise CoturnTlsError("Coturn TLS cleanup failed")
        return receipt
    except (KeyboardInterrupt, SystemExit) as exc:
        private_recovery = private_cleanup_authority(exc)
        control = control_signal(exc)
    except BaseException as exc:
        private_recovery = private_cleanup_authority(exc)
        message = _safe_tls_failure(exc, "Coturn TLS material is invalid")
    cleanup_failed = False
    cleanup_control: ControlSignal | None = None
    if lifetime is not None and lifetime.active:
        if private_recovery is not None:
            recovery_adopted = lifetime.retain_private_authority(private_recovery)
        while True:
            try:
                cleanup_failed, cleanup_control = lifetime.cleanup(
                    initial_control=control or cleanup_control,
                )
                break
            except (KeyboardInterrupt, SystemExit) as cleanup_exc:
                cleanup_control = cleanup_control or control_signal(cleanup_exc)
            except BaseException:
                cleanup_failed = True
                break
    runner = tools = paths = topology = None  # type: ignore[assignment]
    static_auth_secret = None
    unadopted = private_recovery if not recovery_adopted else None
    if cleanup_failed and lifetime is not None and unadopted is not None:
        recovery = combine_tls_cleanup_authorities(lifetime, unadopted)
    elif cleanup_failed:
        recovery = lifetime
    else:
        recovery = unadopted
    private_recovery = unadopted = None
    receipt = lifetime = None
    if control is not None or cleanup_control is not None:
        raise_control(control or cleanup_control, recovery)
    if cleanup_failed:
        if recovery is not None:
            raise CoturnTlsCleanupRequired(recovery) from None
        raise_tls("Coturn TLS cleanup failed")
    if recovery is not None:
        raise CoturnTlsPrivateCleanupRequired(recovery) from None
    raise_tls(message or "Coturn TLS material is invalid")


def _generate_tls_and_config_material(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
    static_auth_secret: object,
    now: datetime,
    lifetime: TlsMaterialLifetimeAuthority,
) -> TlsMaterialReceipt:
    _require_utc(now)
    require_owned_directory(paths.contract.run_dir)
    require_owned_directory(paths.contract.coturn_dir)
    material = (paths.contract.private_key, paths.contract.cert, paths.contract.config)
    if any(path.exists() or path.is_symlink() for path in material):
        raise CoturnTlsError("Coturn TLS material already exists")
    key = execute_tls_checked(
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
    certificate = execute_tls_checked(
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
    cleanup_slot: PrivateFileCleanupReceipt | None = None
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
            cleanup_slot = lifetime.new_slot()
            write_owned_file_exclusive_tracked(
                path,
                value,
                mode=0o400,
                maximum=maximum,
                cleanup_receipt=cleanup_slot,
            )
            cleanup_slot = None
        receipt = validate_tls_material(runner=runner, tools=tools, paths=paths, now=now)
        if not receipt._bind_lifetime(lifetime):
            raise CoturnTlsError("Coturn TLS cleanup failed")
        return receipt
    except BaseException as exc:
        private_recovery = private_cleanup_authority(exc)
        recovery_adopted = False
        original_control = (
            control_signal(exc) if isinstance(exc, (KeyboardInterrupt, SystemExit)) else None
        )
        failure = _safe_tls_failure(exc, "Coturn TLS material is invalid")
        exc = None
        if private_recovery is not None:
            recovery_adopted = lifetime.retain_private_authority(private_recovery)
        if private_recovery is not None and not recovery_adopted:
            failure = "Coturn TLS cleanup failed"
        cleanup_control: ControlSignal | None = None
        while True:
            try:
                cleanup_failed, observed_control = lifetime.cleanup(
                    initial_control=original_control or cleanup_control,
                )
                cleanup_control = cleanup_control or observed_control
                break
            except (KeyboardInterrupt, SystemExit) as cleanup_exc:
                cleanup_control = cleanup_control or control_signal(cleanup_exc)
            except BaseException:
                cleanup_failed = True
                break
        key = certificate = None  # type: ignore[assignment]
        value = b""
        cleanup_slot = None
        recovery = private_recovery if not recovery_adopted else None
        private_recovery = None
        static_auth_secret = None
        runner = tools = paths = topology = None  # type: ignore[assignment]
        lifetime = None  # type: ignore[assignment]
        material = ()
        if original_control is not None or cleanup_control is not None:
            raise_control(
                original_control or cleanup_control,
                recovery,
            )
        if recovery is not None:
            raise CoturnTlsPrivateCleanupRequired(recovery) from None
        if cleanup_failed:
            raise CoturnTlsError("Coturn TLS cleanup failed") from None
        raise CoturnTlsError(failure) from None


def cleanup_tls_material(receipt: TlsMaterialReceipt) -> None:
    """Consume one generated bundle's exact-inode lifetime authority."""

    control: ControlSignal | None = None
    failed = True
    authority: TlsMaterialLifetimeAuthority | None = None
    try:
        if type(receipt) is TlsMaterialReceipt:
            failed, control, authority = receipt._cleanup_lifetime()
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
    except BaseException:
        failed = True
    if authority is None and failed and type(receipt) is TlsMaterialReceipt:
        authority = receipt._retained_lifetime()
    receipt = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, authority if failed else None)
    if failed:
        if authority is not None:
            raise CoturnTlsCleanupRequired(authority) from None
        raise_tls("Coturn TLS cleanup failed")


def cleanup_tls_material_authority(authority: TlsCleanupAuthority) -> None:
    """Retry opaque recovery authority from an interrupted or failed handoff."""

    control: ControlSignal | None = None
    failed = True
    try:
        if (
            type(authority)
            in {
                TlsCombinedCleanupAuthority,
                TlsMaterialLifetimeAuthority,
            }
            and authority.active
        ):
            failed, control = authority.cleanup()
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
    except BaseException:
        failed = True
    retained = (
        authority
        if failed and type(authority) in {TlsCombinedCleanupAuthority, TlsMaterialLifetimeAuthority}
        else None
    )
    authority = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, retained)
    if failed:
        if retained is not None:
            raise CoturnTlsCleanupRequired(retained) from None
        raise_tls("Coturn TLS cleanup failed")


def validate_tls_material(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    now: datetime,
) -> TlsMaterialReceipt:
    """Validate SAN, validity, key relation, and Chromium's DER-SPKI pin."""

    receipt: TlsMaterialReceipt | None = None
    message: str | None = None
    control: ControlSignal | None = None
    recovery: object | None = None
    try:
        receipt = _validate_tls_material(runner=runner, tools=tools, paths=paths, now=now)
    except (KeyboardInterrupt, SystemExit) as exc:
        recovery = private_cleanup_authority(exc)
        control = control_signal(exc)
    except BaseException as exc:
        recovery = private_cleanup_authority(exc)
        message = _safe_tls_failure(exc, "Coturn TLS material is invalid")
    runner = tools = paths = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, recovery)
    if recovery is not None:
        raise CoturnTlsPrivateCleanupRequired(recovery) from None
    if receipt is None:
        raise_tls(message or "Coturn TLS material is invalid")
    return receipt


def _validate_tls_material(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    now: datetime,
) -> TlsMaterialReceipt:
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


def validate_openssl_readiness_result(result: CommandResult) -> OpenSslReadinessReceipt:
    """Validate only bounded, allowlisted facts from ``s_client -brief``."""

    parsed: OpenSslReadinessReceipt | None = None
    control: ControlSignal | None = None
    failed = False
    try:
        parsed = _parse_openssl_readiness_result(result)
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
        failed = True
    except BaseException:
        failed = True
    result = None  # type: ignore[assignment]
    if control is not None:
        parsed = None
        raise_control(control)
    if failed or type(parsed) is not OpenSslReadinessReceipt:
        parsed = None
        _raise_openssl_readiness_error()
    receipt = parsed
    parsed = None
    return receipt


def _raise_openssl_readiness_error() -> None:
    raise CoturnTlsError("Coturn OpenSSL readiness is invalid") from None


def _openssl_stdin(
    runner: CommandRunner,
    tools: TrustedHostTools,
    command: str,
    stdin: bytes,
    *arguments: str,
) -> CommandResult:
    if command not in {"x509", "pkey"}:
        stdin = b""
        arguments = ()
        raise_tls("Coturn OpenSSL request is invalid")
    request = CommandRequest(
        argv=(os.fspath(tools.openssl), command, "-in", "/dev/stdin", *arguments),
        stdin=stdin,
        maximum_output_bytes=65_536,
    )
    stdin = b""
    arguments = ()
    return execute_tls_checked(
        runner,
        request,
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


def _safe_tls_failure(error: BaseException, default: str) -> str:
    try:
        arguments = object.__getattribute__(error, "args")
    except BaseException:
        return default
    if (
        type(error) is CoturnTlsError
        and type(arguments) is tuple
        and len(arguments) == 1
        and type(arguments[0]) is str
        and arguments[0] in _SAFE_TLS_FAILURES
    ):
        return arguments[0]
    return default


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CoturnTlsError("Coturn TLS clock is invalid")


__all__ = [
    "CoturnTlsCleanupRequired",
    "CoturnTlsError",
    "CoturnTlsPrivateCleanupRequired",
    "OpenSslReadinessReceipt",
    "TlsMaterialReceipt",
    "build_openssl_readiness_request",
    "cleanup_tls_material",
    "cleanup_tls_material_authority",
    "cleanup_tls_private_authority",
    "generate_tls_and_config_material",
    "read_owned_file",
    "validate_openssl_readiness_result",
    "validate_tls_material",
    "write_owned_file_exclusive",
]

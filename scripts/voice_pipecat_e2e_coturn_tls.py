"""Exclusive private-file and TLS/SPKI contracts for Coturn E2E."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime, timedelta

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
from scripts.voice_pipecat_e2e_coturn_tls_generation import bind_tls_material_slot_generator
from scripts.voice_pipecat_e2e_coturn_tls_generation import (
    private_cleanup_candidate as _private_cleanup_candidate,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    CoturnTlsCleanupRequired,
    TlsCleanupAuthority,
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
    combine_tls_cleanup_authorities,
    new_tls_material_lifetime_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_material import (
    TlsMaterialGenerationReservation,
    TlsMaterialGenerationSlot,
    TlsMaterialReceipt,
    adopt_tls_material_generation_slot,
    cleanup_tls_material_generation_slot,
    new_tls_material_generation_slot,
    new_tls_material_receipt,
    tls_material_generation_slot_owns_receipt,
)
from scripts.voice_pipecat_e2e_coturn_tls_private import (
    ControlSignal,
    CoturnTlsError,
    CoturnTlsPrivateCleanupRequired,
    PrivateDescriptorCleanupAuthority,
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
from scripts.voice_pipecat_e2e_coturn_tls_values import TlsPrivateControlPublication
from scripts.voice_pipecat_e2e_coturn_tls_values import (
    parse_openssl_dates as _parse_openssl_dates,
)
from scripts.voice_pipecat_e2e_coturn_tls_values import require_utc as _require_utc
from scripts.voice_pipecat_e2e_coturn_tls_values import (
    retry_tls_private_candidate as _retry_tls_private_candidate,
)
from scripts.voice_pipecat_e2e_coturn_tls_values import safe_tls_failure as _safe_tls_failure
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch

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
_MAX_KEY_BYTES = 16_384
_MAX_CERTIFICATE_BYTES = 65_536


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
    generation_slot: TlsMaterialGenerationSlot | None = None,
    generation_reservation: TlsMaterialGenerationReservation | None = None,
) -> TlsMaterialReceipt | None:
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
        if generation_slot is not None:
            adopted, adoption_control = adopt_tls_material_generation_slot(
                generation_slot,
                receipt,
                generation_reservation,  # type: ignore[arg-type]
            )
            if adoption_control is not None and not adopted:
                raise_control(adoption_control)
            if not adopted:
                raise CoturnTlsError("Coturn TLS cleanup failed")
            key = certificate = None  # type: ignore[assignment]
            value = b""
            cleanup_slot = None
            static_auth_secret = None
            runner = tools = paths = topology = None  # type: ignore[assignment]
            lifetime = None  # type: ignore[assignment]
            material = ()
            receipt = None  # type: ignore[assignment]
            if adoption_control is not None:
                raise_control(adoption_control)
            return None
        return receipt
    except BaseException as exc:
        slot_ownership = "unowned" if generation_slot is None else "unknown"
        slot_control: ControlSignal | None = None
        while slot_ownership == "unknown":
            slot_ownership, observed_slot_control = tls_material_generation_slot_owns_receipt(
                generation_slot  # type: ignore[arg-type]
            )
            slot_control = slot_control or observed_slot_control
        original_control = (
            control_signal(exc) if isinstance(exc, (KeyboardInterrupt, SystemExit)) else None
        )
        original_control = original_control or slot_control
        if slot_ownership == "owned":
            failure = _safe_tls_failure(exc, "Coturn TLS material is invalid")
            key = certificate = None  # type: ignore[assignment]
            value = b""
            cleanup_slot = None
            static_auth_secret = None
            runner = tools = paths = topology = generation_slot = None  # type: ignore[assignment]
            lifetime = receipt = None  # type: ignore[assignment]
            material = ()
            exc = None
            if original_control is not None:
                raise_control(original_control)
            raise CoturnTlsError(failure) from None
        private_recovery = private_cleanup_authority(exc)
        recovery_adopted = False
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


generate_tls_and_config_material_into_slot = bind_tls_material_slot_generator(
    _generate_tls_and_config_material
)


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


def tls_private_cleanup_authority(error: BaseException) -> object | None:
    """Extract an exact private authority across unbounded modeled controls.

    Arbitrary caller-line tracing is not masked; a later signal may win there,
    but its effective chain retains the same opaque cleanup authority.
    """

    authority: object | None = None
    candidate: object | None = None
    control = TlsControlLatch()
    try:
        candidate = _retry_tls_private_candidate(  # guarded extractor entry
            error, _private_cleanup_candidate, control
        )
    except (KeyboardInterrupt, SystemExit) as observed:
        control.record_error(observed)
        candidate = _retry_tls_private_candidate(error, _private_cleanup_candidate, control)
    except BaseException:
        candidate = None
    if type(candidate) in {
        PrivateDescriptorCleanupAuthority,
        PrivateFileCleanupReceipt,
    }:
        authority = candidate
    error = candidate = None  # type: ignore[assignment]
    publication: TlsPrivateControlPublication | None = None
    committed = bool(type(authority) is PrivateFileCleanupReceipt and authority.committed)
    try: publication = TlsPrivateControlPublication(authority, committed, control); return publication.publish()  # protected extractor-publication transition  # noqa: E701, E702  # fmt: skip
    except (KeyboardInterrupt, SystemExit) as observed:
        if publication is not None and publication.is_terminal(observed):
            raise
        if authority is not None:
            observed.cleanup_authority = authority  # type: ignore[attr-defined]
            observed.material_committed = committed  # type: ignore[attr-defined]
        control.record_error(observed)
        publication = publication or TlsPrivateControlPublication(authority, committed, control)
        return publication.publish()


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
    return new_tls_material_receipt(
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


__all__ = [
    "CoturnTlsCleanupRequired",
    "CoturnTlsError",
    "CoturnTlsPrivateCleanupRequired",
    "OpenSslReadinessReceipt",
    "TlsMaterialGenerationSlot",
    "TlsMaterialReceipt",
    "build_openssl_readiness_request",
    "cleanup_tls_material",
    "cleanup_tls_material_authority",
    "cleanup_tls_material_generation_slot",
    "cleanup_tls_private_authority",
    "generate_tls_and_config_material",
    "generate_tls_and_config_material_into_slot",
    "new_tls_material_generation_slot",
    "read_owned_file",
    "tls_private_cleanup_authority",
    "validate_openssl_readiness_result",
    "validate_tls_material",
    "write_owned_file_exclusive",
]

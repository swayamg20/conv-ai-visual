"""Private TLS helpers under finite recoverable controls, not fatal async injection."""

from __future__ import annotations

from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandResult,
    CommandRunner,
    execute_checked,
)
from scripts.voice_pipecat_e2e_coturn_tls_file_owner import (
    PrivateFileCleanupReceipt,
    new_private_file_cleanup_receipt,
    remove_private_files_owned,
    write_private_file_owned,
)
from scripts.voice_pipecat_e2e_coturn_tls_file_reader import read_private_file_owned
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    settle_private_file_receipts_owned,
)


class CoturnTlsError(RuntimeError):
    """Private material or its TLS relation is malformed or unsafe."""


class CoturnTlsPrivateCleanupRequired(CoturnTlsError):
    """Fixed failure carrying opaque recovery or an explicit committed outcome."""

    __slots__ = ("_cleanup_authority", "_material_committed")

    def __init__(self, authority: object) -> None:
        super().__init__("Coturn private file cleanup failed")
        self._cleanup_authority = authority
        self._material_committed = bool(
            type(authority) is PrivateFileCleanupReceipt and authority.committed
        )

    @property
    def cleanup_authority(self) -> object:
        return self._cleanup_authority

    @property
    def material_committed(self) -> bool:
        return self._material_committed

    def __repr__(self) -> str:
        return "CoturnTlsPrivateCleanupRequired('Coturn private file cleanup failed')"


ControlSignal = tuple[type[KeyboardInterrupt] | type[SystemExit], int | None]
PrivateCleanupAuthority = PrivateDescriptorCleanupAuthority | PrivateFileCleanupReceipt


def execute_tls_checked(
    runner: CommandRunner,
    request: CommandRequest,
    *,
    failure: str,
) -> CommandResult:
    result: CommandResult | None = None
    control: ControlSignal | None = None
    failed = False
    try:
        result = execute_checked(runner, request, failure=failure)
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
    except BaseException:
        failed = True
    full_scrub = control is not None or failed or result is None
    scrub_failed, scrub_control = _scrub_request(request, clear_argv=full_scrub)
    if not full_scrub and (scrub_failed or scrub_control is not None):
        extra_failed, extra_control = _scrub_request(request, clear_argv=True)
        scrub_failed = bool(scrub_failed or extra_failed)
        scrub_control = scrub_control or extra_control
    control = control or scrub_control
    failed = bool(failed or scrub_failed)
    if control is not None or failed or result is None:
        request = None  # type: ignore[assignment]
        runner = None  # type: ignore[assignment]
        result = None
        if control is not None:
            raise_control(control)
        raise_tls(failure)
    request = None  # type: ignore[assignment]
    runner = None  # type: ignore[assignment]
    return result


def read_owned_file(path: Path, *, exact_mode: int, maximum: int) -> bytes:
    """Read a same-owner file while binding path and fd metadata."""

    value = b""
    control: ControlSignal | None = None
    try:
        value, failed, control, recovery = read_private_file_owned(
            path,
            exact_mode=exact_mode,
            maximum=maximum,
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
        failed = True
        recovery = None
    except BaseException:
        failed = True
        recovery = None
    cleanup_failed = False
    if recovery is not None:
        cleanup_failed, cleanup_control = recovery.cleanup(initial_control=control)
        control = control or cleanup_control
        if not cleanup_failed:
            recovery = None
    if control is not None or failed:
        value = b""
        path = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control, recovery)
        if cleanup_failed and recovery is not None:
            raise CoturnTlsPrivateCleanupRequired(recovery) from None
        raise_tls("Coturn private file is unavailable")
    return value


def write_owned_file_exclusive(
    path: Path,
    value: bytes,
    *,
    mode: int,
    maximum: int,
) -> None:
    """Create one owner-only file without following links."""

    valid = (
        type(value) is bytes
        and bool(value)
        and type(maximum) is int
        and 1 <= len(value) <= maximum
        and type(mode) is int
        and mode in {0o400, 0o600}
    )
    if not valid:
        value = b""
        path = None  # type: ignore[assignment]
        raise_tls("Coturn private file content is invalid")
    control: ControlSignal | None = None
    cleanup_failed = False
    try:
        failed, cleanup_failed, control, recovery = write_private_file_owned(
            path,
            value,
            mode=mode,
            maximum=maximum,
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
        failed = True
        recovery = None
    except BaseException:
        failed = True
        recovery = None
    value = b""
    path = None  # type: ignore[assignment]
    if control is not None or failed:
        if control is not None:
            raise_control(control, recovery)
        if cleanup_failed and recovery is not None:
            raise CoturnTlsPrivateCleanupRequired(recovery) from None
        if cleanup_failed:
            raise_tls("Coturn private file cleanup failed")
        raise_tls("Coturn private file creation failed")


def write_owned_file_exclusive_tracked(
    path: Path,
    value: bytes,
    *,
    mode: int,
    maximum: int,
    cleanup_receipt: PrivateFileCleanupReceipt,
) -> PrivateFileCleanupReceipt:
    """Create a file and publish exact cleanup authority into a prior slot."""

    valid = (
        type(cleanup_receipt) is PrivateFileCleanupReceipt
        and type(value) is bytes
        and bool(value)
        and type(maximum) is int
        and 1 <= len(value) <= maximum
        and type(mode) is int
        and mode in {0o400, 0o600}
    )
    if not valid:
        value = b""
        path = cleanup_receipt = None  # type: ignore[assignment]
        raise_tls("Coturn private file content is invalid")
    control: ControlSignal | None = None
    cleanup_failed = False
    try:
        failed, cleanup_failed, control, _recovery = write_private_file_owned(
            path,
            value,
            mode=mode,
            maximum=maximum,
            cleanup_receipt=cleanup_receipt,
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
        failed = True
    except BaseException:
        failed = True
    value = b""
    path = None  # type: ignore[assignment]
    if control is not None or failed:
        cleanup_receipt = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control)
        if cleanup_failed:
            raise_tls("Coturn private file cleanup failed")
        raise_tls("Coturn private file creation failed")
    return cleanup_receipt


def cleanup_tls_private_authority(authority: object) -> None:
    """Retry opaque cleanup, or acknowledge an irreversible committed outcome."""

    control: ControlSignal | None = None
    failed = True
    retained = (
        authority
        if type(authority) in {PrivateDescriptorCleanupAuthority, PrivateFileCleanupReceipt}
        else None
    )
    try:
        if type(retained) is PrivateDescriptorCleanupAuthority:
            failed, control = retained.cleanup()
        elif type(retained) is PrivateFileCleanupReceipt:
            failed, control = settle_private_file_receipts_owned((retained,))
    except (KeyboardInterrupt, SystemExit) as exc:
        control = control_signal(exc)
    except BaseException:
        failed = True
    authority = None
    if control is not None:
        raise_control(control, retained if failed else None)
    if failed:
        if retained is not None:
            raise CoturnTlsPrivateCleanupRequired(retained) from None
        raise_tls("Coturn private file cleanup failed")


def remove_owned_files(
    paths: tuple[Path, ...],
    *,
    initial_control: ControlSignal | None = None,
) -> tuple[bool, ControlSignal | None]:
    return remove_private_files_owned(paths, initial_control=initial_control)


def control_signal(error: KeyboardInterrupt | SystemExit) -> ControlSignal:
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt, None
    code = error.code
    if code is not None and type(code) is not int:
        code = 1
    return SystemExit, code


def private_cleanup_authority(
    error: BaseException,
) -> PrivateCleanupAuthority | None:
    """Extract only a factory-owned recovery receipt from a caught failure."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _depth in range(4):
        if current is None:
            break
        identifier = id(current)
        if identifier in visited:
            break
        visited.add(identifier)
        candidate: object | None = None
        if type(current) is CoturnTlsPrivateCleanupRequired:
            candidate = object.__getattribute__(current, "_cleanup_authority")
        elif type(current) in {KeyboardInterrupt, SystemExit}:
            namespace = object.__getattribute__(current, "__dict__")
            if type(namespace) is dict:
                candidate = namespace.get("cleanup_authority")
        if type(candidate) in {
            PrivateDescriptorCleanupAuthority,
            PrivateFileCleanupReceipt,
        }:
            return candidate
        cause = object.__getattribute__(current, "__cause__")
        if isinstance(cause, BaseException):
            current = cause
            continue
        if cause is not None or object.__getattribute__(current, "__suppress_context__"):
            break
        context = object.__getattribute__(current, "__context__")
        current = context if isinstance(context, BaseException) else None
    return None


def raise_control(control: ControlSignal, cleanup_authority: object | None = None) -> None:
    kind, code = control
    if kind is KeyboardInterrupt:
        error: KeyboardInterrupt | SystemExit = KeyboardInterrupt()
    else:
        error = SystemExit(code)
    if cleanup_authority is not None:
        error.cleanup_authority = cleanup_authority  # type: ignore[attr-defined]
        error.material_committed = bool(  # type: ignore[attr-defined]
            type(cleanup_authority) is PrivateFileCleanupReceipt and cleanup_authority.committed
        )
    raise error from None


def raise_tls(message: str) -> None:
    raise CoturnTlsError(message) from None


def _scrub_request(
    request: CommandRequest,
    *,
    clear_argv: bool,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    fields = (("stdin", b""), ("argv", ())) if clear_argv else (("stdin", b""),)
    for field, replacement in fields:
        while True:
            try:
                object.__setattr__(request, field, replacement)
                scrubbed = getattr(request, field) == replacement
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                continue
            except BaseException:
                return True, control
            if not scrubbed:
                return True, control
            break
    return False, control


__all__ = [
    "ControlSignal",
    "CoturnTlsError",
    "CoturnTlsPrivateCleanupRequired",
    "PrivateFileCleanupReceipt",
    "cleanup_tls_private_authority",
    "control_signal",
    "execute_tls_checked",
    "new_private_file_cleanup_receipt",
    "private_cleanup_authority",
    "raise_control",
    "raise_tls",
    "read_owned_file",
    "remove_owned_files",
    "write_owned_file_exclusive",
    "write_owned_file_exclusive_tracked",
]

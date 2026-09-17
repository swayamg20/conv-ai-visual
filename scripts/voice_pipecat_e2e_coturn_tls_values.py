"""Sanitized value validation shared by the Coturn TLS boundary."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology, CoturnContractPaths
from scripts.voice_pipecat_e2e_coturn_host import CoturnRuntimePaths
from scripts.voice_pipecat_e2e_coturn_tls_private import ControlSignal, CoturnTlsError
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch

_LINEAGE_KEY = os.urandom(32)
_SAFE_TLS_FAILURES = frozenset(
    {
        "Coturn TLS generation slot is invalid",
        "Coturn TLS material already exists",
        "Coturn private-key generation failed",
        "Coturn certificate generation failed",
        "Coturn TLS cleanup failed",
        "Coturn TLS material is invalid",
        "Coturn TLS validity is invalid",
        "Coturn private file is unavailable",
        "Coturn TLS validation failed",
    }
)


class LinearTlsAuthority:
    __slots__ = ()

    def __copy__(self) -> None:
        raise TypeError("Coturn TLS authority is linear")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Coturn TLS authority is linear")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("Coturn TLS authority is linear")


class TlsPrivateControlPublication:
    """Own authority while retrying every modeled finite publication control."""

    __slots__ = ("authority", "committed", "control", "terminal_error", "terminal_ready")

    def __init__(self, authority: object | None, committed: bool, control: TlsControlLatch) -> None:
        self.authority = authority
        self.committed = committed
        self.control = control
        self.terminal_error: KeyboardInterrupt | SystemExit | None = None
        self.terminal_ready = False

    def is_terminal(self, error: BaseException) -> bool:
        return error is self.terminal_error and self.terminal_ready

    def publish(self) -> object | None:
        try: return self._publish_loop()  # protected private-publication trampoline  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as observed:
            if self.is_terminal(observed):
                raise
            self.control.record_error(observed)
            return self.publish()
        except BaseException:
            self.terminal_error = None
            self.terminal_ready = False
            return self.publish()

    def _publish_loop(self) -> object | None:
        while True:
            try:
                return self._attempt()
            except (KeyboardInterrupt, SystemExit) as observed:
                while True:
                    try:
                        self.control.record_error(observed)  # terminal publication control
                        break
                    except (KeyboardInterrupt, SystemExit):
                        continue
                while True:
                    try:
                        replay_terminal = self.is_terminal(observed)
                        break
                    except (KeyboardInterrupt, SystemExit) as later:
                        self.control.record_error(later)
                try:
                    if replay_terminal:
                        raise self.terminal_error.with_traceback(None) from None
                except (KeyboardInterrupt, SystemExit) as later:
                    if later is self.terminal_error:
                        raise
                    self.control.record_error(later)
            except BaseException:
                self.terminal_error = None
                self.terminal_ready = False

    def _attempt(self) -> object | None:
        observed_control = self.control.value()
        if observed_control is None:
            return self.authority
        if self.terminal_error is None:
            self.terminal_error = new_tls_control_error(observed_control)
        if not self.terminal_ready:
            if self.authority is not None:
                self.terminal_error.cleanup_authority = self.authority  # type: ignore[attr-defined]
                self.terminal_error.material_committed = self.committed  # type: ignore[attr-defined]
            self.terminal_ready = True
        raise self.terminal_error from None

    def __repr__(self) -> str:
        return "TlsPrivateControlPublication()"


def parse_openssl_dates(value: bytes) -> tuple[datetime, datetime]:
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


def safe_tls_failure(error: BaseException, default: str) -> str:
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


def new_tls_control_error(control: ControlSignal) -> KeyboardInterrupt | SystemExit:
    kind, code = control
    return KeyboardInterrupt() if kind is KeyboardInterrupt else SystemExit(code)


def retry_tls_private_candidate(
    error: BaseException,
    extractor: Callable[[BaseException], object | None],
    control: TlsControlLatch,
) -> object | None:
    try:
        while True:
            try:
                return extractor(error)
            except (KeyboardInterrupt, SystemExit) as observed:
                control.record_error(observed)
            except BaseException:
                return None
    except (KeyboardInterrupt, SystemExit) as observed:
        control.record_error(observed)
        return retry_tls_private_candidate(error, extractor, control)
    except BaseException:
        return None


def require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CoturnTlsError("Coturn TLS clock is invalid")


def lineage_fingerprint(paths: CoturnRuntimePaths, topology: CoturnBridgeTopology) -> bytes:
    if (
        type(paths) is not CoturnRuntimePaths
        or type(paths.contract) is not CoturnContractPaths
        or type(topology) is not CoturnBridgeTopology
    ):
        raise CoturnTlsError("Coturn TLS generation slot is invalid")
    values = (
        paths.contract.run_id,
        os.fspath(paths.contract.run_dir),
        os.fspath(paths.contract.coturn_dir),
        os.fspath(paths.contract.config),
        os.fspath(paths.contract.cert),
        os.fspath(paths.contract.private_key),
        os.fspath(paths.control_dir),
        os.fspath(paths.cidfile),
        os.fspath(paths.container_receipt),
        os.fspath(paths.docker_config),
        os.fspath(paths.network_absence_receipt),
        os.fspath(paths.network_plan_receipt),
        os.fspath(paths.network_receipt),
        str(topology.network),
        str(topology.gateway),
        str(topology.container),
    )
    payload = b"".join(
        len(value.encode("utf-8")).to_bytes(4, "big") + value.encode("utf-8") for value in values
    )
    return hmac.new(_LINEAGE_KEY, payload, hashlib.sha256).digest()


__all__ = [
    "LinearTlsAuthority",
    "lineage_fingerprint",
    "new_tls_control_error",
    "parse_openssl_dates",
    "require_utc",
    "retry_tls_private_candidate",
    "safe_tls_failure",
]

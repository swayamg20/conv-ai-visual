"""Credential-free control plane for protected Voice V2 qualification.

This module validates static topology, source, budget, and artifact contracts.
It deliberately cannot start a browser, contact a provider, or read credentials.
The executable live adapter remains a separate, not-yet-implemented boundary.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PAID_SERVICES_ACK = "I_UNDERSTAND_THIS_CALLS_PAID_SERVICES"
QUALIFICATION_ENVIRONMENT = "qualification"
MIN_RUN_BUDGET_USD = 0.25
MAX_RUN_BUDGET_USD = 2.00
CAMPAIGN_CAP_USD = 25.00

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SECRET_NAME_PATTERN = re.compile(
    r"(?i)(?:^|_)(?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token|"
    r"password|private[_-]?key|credential|secret|token)(?:$|_)"
)
_TEXT_SECRET_PATTERN = re.compile(
    r"(?i)(?P<name>[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS?))"
    r"(?P<separator>\s*[:=]\s*)(?P<value>[^\s,;]+)"
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>[?&](?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|"
    r"password|secret|token)=)[^&#\s]*"
)
_AUTH_VALUE_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b(?:authorization\s*[:=]\s*)?(?:bearer|token|basic)\s+)\S+"
)

_COMMON_SECRET_ENV = frozenset(
    {
        "DEEPGRAM_KEY",
        "ELEVENLABS_API_KEY",
        "GROQ_API_KEY",
        "MURMUR_QUALIFICATION_FIREBASE_STORAGE_STATE_PATH",
    }
)
_RUNTIME_SECRET_ENV: Mapping[str, frozenset[str]] = {
    "livekit_v2": frozenset(
        {
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "VOICE_V2_SIGNING_SECRET",
        }
    ),
    "pipecat_smallwebrtc_v1": frozenset(
        {
            "MURMUR_PIPECAT_SIGNALING_SECRET",
            "MURMUR_TURN_PASSWORD",
            "MURMUR_TURN_USERNAME",
        }
    ),
}
_ALL_SECRET_ENV = frozenset().union(_COMMON_SECRET_ENV, *_RUNTIME_SECRET_ENV.values())
_EXPLICIT_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_])(?P<name>"
    + "|".join(re.escape(name) for name in sorted(_ALL_SECRET_ENV, key=len, reverse=True))
    + r")(?P<separator>\s*[:=]\s*)(?P<value>[^\s,;]+)"
)


class LiveQualificationError(RuntimeError):
    """A protected qualification contract refused the requested operation."""


class QualificationRuntime(StrEnum):
    LIVEKIT_V2 = "livekit_v2"
    PIPECAT_SMALLWEBRTC_V1 = "pipecat_smallwebrtc_v1"


class QualificationNetwork(StrEnum):
    DIRECT = "direct"
    RELAY_TLS = "relay-tls"
    DISCONNECT = "disconnect"


@dataclass(frozen=True)
class SourceState:
    sha: str
    dirty: bool


@dataclass(frozen=True, kw_only=True)
class LiveQualificationSettings:
    """Strict, credential-free inputs for one protected qualification run."""

    run_id: str
    runtime: QualificationRuntime
    network: QualificationNetwork
    environment: str
    paid_services_ack: str
    max_cost_usd: float
    campaign_cap_usd: float
    max_calls: int
    max_turns: int
    max_audio_seconds: int
    max_wall_seconds: int
    source_sha: str
    source_dirty: bool
    control_plane_url: str
    runtime_url: str
    turn_url: str | None
    suite_path: Path
    gates_path: Path
    output_root: Path
    ledger_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, QualificationRuntime):
            raise LiveQualificationError("qualification runtime is invalid")
        if not isinstance(self.network, QualificationNetwork):
            raise LiveQualificationError("qualification network is invalid")
        if not _RUN_ID_PATTERN.fullmatch(self.run_id):
            raise LiveQualificationError("qualification run_id is invalid")
        if self.environment != QUALIFICATION_ENVIRONMENT:
            raise LiveQualificationError("live qualification requires environment=qualification")
        if self.paid_services_ack != PAID_SERVICES_ACK:
            raise LiveQualificationError("exact paid-services acknowledgement is required")
        _validate_money("max_cost_usd", self.max_cost_usd, MIN_RUN_BUDGET_USD, MAX_RUN_BUDGET_USD)
        if self.campaign_cap_usd != CAMPAIGN_CAP_USD:
            raise LiveQualificationError("qualification campaign cap must equal USD 25.00")
        _validate_positive_int("max_calls", self.max_calls, maximum=4)
        _validate_positive_int("max_turns", self.max_turns, maximum=20)
        _validate_positive_int("max_audio_seconds", self.max_audio_seconds, maximum=180)
        _validate_positive_int("max_wall_seconds", self.max_wall_seconds, maximum=600)
        if self.max_turns < self.max_calls:
            raise LiveQualificationError("max_turns cannot be below max_calls")
        if not _SHA_PATTERN.fullmatch(self.source_sha):
            raise LiveQualificationError("qualification requires an exact 40-character Git SHA")
        if self.source_dirty:
            raise LiveQualificationError("live qualification refuses a dirty source tree")
        _validate_runtime_urls(self)
        _validate_input_file("suite", self.suite_path)
        _validate_input_file("gates", self.gates_path)
        _validate_output_paths(self.output_root, self.ledger_path)

    def public_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "runtime": self.runtime.value,
            "network": self.network.value,
            "environment": self.environment,
            "max_cost_usd": self.max_cost_usd,
            "campaign_cap_usd": self.campaign_cap_usd,
            "limits": {
                "calls": self.max_calls,
                "turns": self.max_turns,
                "audio_seconds": self.max_audio_seconds,
                "wall_seconds": self.max_wall_seconds,
            },
            "source_sha": self.source_sha,
            "source_dirty": self.source_dirty,
            "control_plane_url": redact_text(self.control_plane_url),
            "runtime_url": redact_text(self.runtime_url),
            "turn_url": redact_text(self.turn_url) if self.turn_url else None,
            "suite_path": str(self.suite_path),
            "suite_sha256": file_sha256(self.suite_path),
            "gates_path": str(self.gates_path),
            "gates_sha256": file_sha256(self.gates_path),
            "output_root": str(self.output_root),
            "ledger_path": str(self.ledger_path),
            "credentials_read": False,
            "network_attempted": False,
            "provider_calls": False,
        }


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    run_id: str
    reserved_usd: float
    created_at: str


class BudgetLedger:
    """Atomic append-only campaign budget ledger with reserve/reconcile pairs."""

    def __init__(self, path: Path, *, campaign_cap_usd: float = CAMPAIGN_CAP_USD) -> None:
        self.path = path
        if campaign_cap_usd != CAMPAIGN_CAP_USD:
            raise LiveQualificationError("budget ledger campaign cap must equal USD 25.00")
        self.campaign_cap_usd = campaign_cap_usd

    def reserve(
        self,
        *,
        run_id: str,
        amount_usd: float,
        reservation_id: str,
        now: datetime | None = None,
    ) -> BudgetReservation:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise LiveQualificationError("budget reservation run_id is invalid")
        if not _RUN_ID_PATTERN.fullmatch(reservation_id):
            raise LiveQualificationError("budget reservation_id is invalid")
        _validate_money("reservation amount", amount_usd, MIN_RUN_BUDGET_USD, MAX_RUN_BUDGET_USD)
        with self._exclusive_lock():
            document = self._read()
            if any(event["reservation_id"] == reservation_id for event in document["events"]):
                raise LiveQualificationError("budget reservation_id already exists")
            if any(
                event["kind"] == "reserve" and event["run_id"] == run_id
                for event in document["events"]
            ):
                raise LiveQualificationError("budget run_id already has a reservation")
            available = self.campaign_cap_usd - _campaign_committed_or_reserved(document["events"])
            if amount_usd > available + 1e-9:
                raise LiveQualificationError("budget reservation exceeds the campaign cap")
            created_at = _iso_timestamp(now)
            document["events"].append(
                {
                    "sequence": len(document["events"]) + 1,
                    "kind": "reserve",
                    "reservation_id": reservation_id,
                    "run_id": run_id,
                    "amount_usd": amount_usd,
                    "created_at": created_at,
                }
            )
            self._write(document)
        return BudgetReservation(reservation_id, run_id, amount_usd, created_at)

    def reconcile(
        self,
        *,
        reservation_id: str,
        actual_cost_usd: float,
        outcome: str,
        now: datetime | None = None,
    ) -> None:
        if not isinstance(actual_cost_usd, int | float) or isinstance(actual_cost_usd, bool):
            raise LiveQualificationError("actual cost must be a number")
        if not math.isfinite(actual_cost_usd) or actual_cost_usd < 0:
            raise LiveQualificationError("actual cost must be finite and non-negative")
        if outcome not in {"passed", "failed", "cancelled", "infrastructure_failed"}:
            raise LiveQualificationError("budget reconciliation outcome is invalid")
        with self._exclusive_lock():
            document = self._read()
            reservations = [
                event
                for event in document["events"]
                if event["kind"] == "reserve" and event["reservation_id"] == reservation_id
            ]
            if len(reservations) != 1:
                raise LiveQualificationError("budget reservation does not exist")
            if any(
                event["kind"] == "reconcile" and event["reservation_id"] == reservation_id
                for event in document["events"]
            ):
                raise LiveQualificationError("budget reservation is already reconciled")
            reservation = reservations[0]
            if actual_cost_usd > reservation["amount_usd"] + 1e-9:
                raise LiveQualificationError("actual cost exceeds the per-run reservation")
            document["events"].append(
                {
                    "sequence": len(document["events"]) + 1,
                    "kind": "reconcile",
                    "reservation_id": reservation_id,
                    "run_id": reservation["run_id"],
                    "actual_cost_usd": actual_cost_usd,
                    "outcome": outcome,
                    "created_at": _iso_timestamp(now),
                }
            )
            self._write(document)

    def summary(self) -> dict[str, float]:
        document = self._read()
        events = document["events"]
        committed_or_reserved = _campaign_committed_or_reserved(events)
        committed = sum(
            event["actual_cost_usd"] for event in events if event["kind"] == "reconcile"
        )
        return {
            "campaign_cap_usd": self.campaign_cap_usd,
            "committed_usd": round(committed, 6),
            "committed_or_reserved_usd": round(committed_or_reserved, 6),
            "remaining_usd": round(self.campaign_cap_usd - committed_or_reserved, 6),
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "campaign_cap_usd": self.campaign_cap_usd, "events": []}
        _assert_private_file(self.path)
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiveQualificationError("budget ledger is unreadable") from exc
        _validate_ledger_document(document, self.campaign_cap_usd)
        return document

    def _write(self, document: Mapping[str, Any]) -> None:
        _validate_ledger_document(document, self.campaign_cap_usd)
        atomic_write_json(self.path, document)

    @contextmanager
    def _exclusive_lock(self) -> Any:
        import fcntl

        lock_path = self.path.with_name(f".{self.path.name}.lock")
        _ensure_private_directory(lock_path.parent)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def build_static_report(settings: LiveQualificationSettings) -> dict[str, object]:
    """Build a credential-free static preflight report with no side effects."""

    report = settings.public_dict()
    report.update(
        {
            "schema_version": 1,
            "mode": "dry-run",
            "status": "configured_static_only",
            "paid_services_acknowledged": True,
            "required_secret_env": sorted(required_secret_env_names(settings.runtime)),
            "limitations": [
                "No credential value was read or validated.",
                "No DNS, TLS, RTC, TURN, browser, provider, or Cloud request was made.",
                "Static topology and source validation do not prove live readiness, media, cost, or cleanup.",
                "The live run adapter is not implemented and will refuse before network activity.",
            ],
        }
    )
    return report


def inspect_source_state(project_root: Path) -> SourceState:
    """Read the exact Git SHA and dirty state without exposing ambient secrets."""

    safe_environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "LANG": "C",
    }
    try:
        sha_result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=project_root,
            env=safe_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status_result = subprocess.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=project_root,
            env=safe_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveQualificationError(
            "qualification could not inspect the Git source state"
        ) from exc
    sha = sha_result.stdout.strip()
    if not _SHA_PATTERN.fullmatch(sha):
        raise LiveQualificationError("qualification Git SHA is invalid")
    return SourceState(sha=sha, dirty=bool(status_result.stdout.strip()))


def required_secret_env_names(runtime: QualificationRuntime) -> frozenset[str]:
    """Return exact names a future protected runner may read, never their values."""

    return _COMMON_SECRET_ENV | _RUNTIME_SECRET_ENV[runtime.value]


def select_secret_environment(
    runtime: QualificationRuntime,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Select only explicit secret names for a future child process.

    Dry-run must not call this function. It exists so the eventual executable
    adapter cannot accidentally inherit unrelated ambient credentials.
    """

    allowed = required_secret_env_names(runtime)
    return {name: environment[name] for name in sorted(allowed) if name in environment}


def redact_text(value: str | None, *, secret_values: Sequence[str] = ()) -> str:
    """Redact known secrets, credential-like assignments, and URL userinfo."""

    if value is None:
        return ""
    redacted = value
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        redacted = redacted.replace(secret, "<redacted>")
    redacted = _EXPLICIT_SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}<redacted>",
        redacted,
    )
    redacted = _TEXT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}<redacted>",
        redacted,
    )
    redacted = _URL_CREDENTIAL_PATTERN.sub(r"\g<scheme><redacted>@", redacted)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\g<prefix><redacted>", redacted)
    redacted = _AUTH_VALUE_PATTERN.sub(r"\g<prefix><redacted>", redacted)
    return redacted


def redact_mapping(
    values: Mapping[str, object],
    *,
    secret_values: Sequence[str] = (),
) -> dict[str, object]:
    """Recursively redact secret-keyed fields before serialization or logging."""

    redacted: dict[str, object] = {}
    for key, value in values.items():
        if key.upper() in _ALL_SECRET_ENV or _SECRET_NAME_PATTERN.search(key):
            redacted[key] = "<redacted>"
        elif isinstance(value, Mapping):
            redacted[key] = redact_mapping(value, secret_values=secret_values)
        elif isinstance(value, list):
            redacted[key] = [
                redact_mapping(item, secret_values=secret_values)
                if isinstance(item, Mapping)
                else redact_text(item, secret_values=secret_values)
                if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            redacted[key] = redact_text(value, secret_values=secret_values)
        else:
            redacted[key] = value
    return redacted


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically write private JSON using 0700 parent directories and mode 0600."""

    _ensure_private_directory(path.parent)
    serialized = json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def write_private_report(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_json(path, redact_mapping(value))


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_live_qualification(settings: LiveQualificationSettings) -> None:
    """Refuse honestly until the protected runtime adapter exists.

    Settings validation is static. This function intentionally refuses before
    reading credentials, reserving budget, creating artifacts, or making any
    network/provider/Cloud request.
    """

    del settings
    raise LiveQualificationError(
        "live qualification adapter is not implemented; no credentials were read, "
        "no budget was reserved, and no network or provider call was made"
    )


def _validate_runtime_urls(settings: LiveQualificationSettings) -> None:
    _validate_public_url("control_plane_url", settings.control_plane_url, schemes={"https"})
    if settings.runtime == QualificationRuntime.LIVEKIT_V2:
        parsed = _validate_public_url("runtime_url", settings.runtime_url, schemes={"wss"})
        if not parsed.hostname or not parsed.hostname.lower().endswith(".livekit.cloud"):
            raise LiveQualificationError("LiveKit qualification requires a livekit.cloud WSS host")
        if settings.turn_url is not None:
            raise LiveQualificationError("LiveKit qualification must not accept a custom turn_url")
    else:
        _validate_public_url("runtime_url", settings.runtime_url, schemes={"https"})
        if settings.network == QualificationNetwork.RELAY_TLS:
            if settings.turn_url is None:
                raise LiveQualificationError("Pipecat relay-tls qualification requires turn_url")
            _validate_public_url("turn_url", settings.turn_url, schemes={"turns"})
        elif settings.turn_url is not None:
            raise LiveQualificationError("Pipecat direct/disconnect qualification forbids turn_url")


def _validate_public_url(name: str, value: str, *, schemes: set[str]) -> Any:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in schemes or not parsed.hostname:
        raise LiveQualificationError(f"{name} must use {sorted(schemes)} with an explicit host")
    if parsed.username is not None or parsed.password is not None:
        raise LiveQualificationError(f"{name} must not embed credentials")
    if parsed.query or parsed.fragment:
        raise LiveQualificationError(f"{name} must not contain a query or fragment")
    host = parsed.hostname.lower().rstrip(".")
    if _is_loopback_or_private_host(host):
        raise LiveQualificationError(f"{name} must use a public non-loopback host")
    return parsed


def _is_loopback_or_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    if host.endswith((".local", ".internal", ".test", ".example", ".invalid")):
        return True
    try:
        import ipaddress

        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return not address.is_global


def _validate_input_file(name: str, path: Path) -> None:
    if not path.is_absolute() or not path.is_file():
        raise LiveQualificationError(f"qualification {name} path must be an existing absolute file")


def _validate_output_paths(output_root: Path, ledger_path: Path) -> None:
    if not output_root.is_absolute() or not ledger_path.is_absolute():
        raise LiveQualificationError("qualification output and ledger paths must be absolute")
    if output_root == Path(output_root.anchor) or ledger_path == Path(ledger_path.anchor):
        raise LiveQualificationError("qualification output paths must not target a filesystem root")
    if ledger_path.is_dir():
        raise LiveQualificationError("qualification ledger path must be a file")


def _validate_positive_int(name: str, value: int, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise LiveQualificationError(f"{name} must be between 1 and {maximum}")


def _validate_money(name: str, value: float, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise LiveQualificationError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise LiveQualificationError(f"{name} must be between USD {minimum:.2f} and {maximum:.2f}")


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor == cursor.parent:
            raise LiveQualificationError("qualification private directory is invalid")
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise LiveQualificationError("qualification private directory parent is invalid")

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            # A concurrent creator must satisfy the same private-directory contract.
            pass
        _assert_private_directory(directory)

    _assert_private_directory(path)


def _assert_private_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise LiveQualificationError("qualification private directory is invalid")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LiveQualificationError("qualification private directory permissions must be 0700")


def _assert_private_file(path: Path) -> None:
    if path.is_symlink():
        raise LiveQualificationError("budget ledger must not be a symbolic link")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LiveQualificationError("budget ledger permissions must be 0600")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _iso_timestamp(value: datetime | None) -> str:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise LiveQualificationError("budget timestamp must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_ledger_document(document: Any, campaign_cap_usd: float) -> None:
    if not isinstance(document, dict):
        raise LiveQualificationError("budget ledger must be a JSON object")
    if document.get("schema_version") != 1 or document.get("campaign_cap_usd") != campaign_cap_usd:
        raise LiveQualificationError("budget ledger schema or campaign cap is invalid")
    events = document.get("events")
    if not isinstance(events, list):
        raise LiveQualificationError("budget ledger events must be a list")
    expected_sequence = 1
    reservations: dict[str, dict[str, Any]] = {}
    reconciled: set[str] = set()
    reserved_run_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or event.get("sequence") != expected_sequence:
            raise LiveQualificationError("budget ledger sequence is invalid")
        expected_sequence += 1
        kind = event.get("kind")
        reservation_id = event.get("reservation_id")
        if not isinstance(reservation_id, str) or not _RUN_ID_PATTERN.fullmatch(reservation_id):
            raise LiveQualificationError("budget ledger reservation ID is invalid")
        if kind == "reserve":
            if reservation_id in reservations:
                raise LiveQualificationError("budget ledger contains a duplicate reservation")
            amount = event.get("amount_usd")
            _validate_money(
                "ledger reservation amount", amount, MIN_RUN_BUDGET_USD, MAX_RUN_BUDGET_USD
            )
            run_id = event.get("run_id")
            if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
                raise LiveQualificationError("budget ledger run ID is invalid")
            if run_id in reserved_run_ids:
                raise LiveQualificationError("budget ledger contains a duplicate run ID")
            _validate_timestamp(event.get("created_at"))
            reservations[reservation_id] = event
            reserved_run_ids.add(run_id)
        elif kind == "reconcile":
            if reservation_id not in reservations or reservation_id in reconciled:
                raise LiveQualificationError("budget ledger reconciliation is invalid")
            cost = event.get("actual_cost_usd")
            if (
                isinstance(cost, bool)
                or not isinstance(cost, int | float)
                or not math.isfinite(cost)
                or cost < 0
                or cost > reservations[reservation_id]["amount_usd"]
            ):
                raise LiveQualificationError("budget ledger actual cost is invalid")
            if event.get("run_id") != reservations[reservation_id].get("run_id"):
                raise LiveQualificationError("budget ledger reconciliation run ID is invalid")
            if event.get("outcome") not in {
                "passed",
                "failed",
                "cancelled",
                "infrastructure_failed",
            }:
                raise LiveQualificationError("budget ledger reconciliation outcome is invalid")
            _validate_timestamp(event.get("created_at"))
            reconciled.add(reservation_id)
        else:
            raise LiveQualificationError("budget ledger event kind is invalid")
    if _campaign_committed_or_reserved(events) > campaign_cap_usd + 1e-9:
        raise LiveQualificationError("budget ledger exceeds its campaign cap")


def _campaign_committed_or_reserved(events: Sequence[Mapping[str, Any]]) -> float:
    reconciled = {
        event["reservation_id"]: event["actual_cost_usd"]
        for event in events
        if event["kind"] == "reconcile"
    }
    return sum(
        reconciled.get(event["reservation_id"], event["amount_usd"])
        for event in events
        if event["kind"] == "reserve"
    )


def _validate_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LiveQualificationError("budget ledger timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise LiveQualificationError("budget ledger timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise LiveQualificationError("budget ledger timestamp is invalid")

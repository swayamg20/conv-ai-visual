#!/usr/bin/env python3
"""Deploy and verify the isolated Murmur Azure pilot without printing secrets.

The deployment path deliberately builds from ``git archive`` rather than the
working tree.  That both binds the images to the accepted commit and prevents
ignored dotenv files from entering an ACR build context.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_TEMPLATE = PROJECT_ROOT / "infra" / "azure" / "foundation.bicep"
APPS_TEMPLATE = PROJECT_ROOT / "infra" / "azure" / "apps.bicep"

DEFAULT_RESOURCE_GROUP = "murmur-pilot-rg"
DEFAULT_LOCATION = "centralindia"
DEFAULT_BACKEND_APP = "murmur-api"
DEFAULT_FRONTEND_APP = "murmur-web"
FOUNDATION_DEPLOYMENT = "murmur-foundation"
APPS_DEPLOYMENT = "murmur-apps"

AZURE_KEY_SECRET_NAME = "azure-openai-api-key"
FIREBASE_SECRET_NAME = "firebase-runtime-service-account-json"
LEGACY_FIREBASE_SECRET_NAME = "firebase-service-account-json"
KEY_VAULT_WRITER_ROLE = "Key Vault Secrets Officer"
KEY_VAULT_ROTATION_TAG = "murmurRotationId"
KEY_VAULT_SECRETS_USER_ROLE_ID = "4633458b-17de-408a-b874-0445c86b69e6"
KEY_VAULT_SECRETS_OFFICER_ROLE_ID = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
KEY_VAULT_WRITER_ASSIGNMENT_NAMESPACE = uuid.UUID("c95619d1-bf33-4f50-a725-c7679f69307d")
KEY_VAULT_WRITER_DESCRIPTION = "Temporary Murmur deployment secret writer"
DEPLOYMENT_LEASE_SECONDS = 60
DEPLOYMENT_LEASE_RENEW_SECONDS = 20
IDENTITY_TOOLKIT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
IDENTITY_TOOLKIT_CONFIG_ROOT = "https://identitytoolkit.googleapis.com/admin/v2"
RESOURCE_MANAGER_ROOT = "https://cloudresourcemanager.googleapis.com/v1"
FIREBASE_AUTH_PERMISSION_UNIVERSE = (
    "firebaseauth.configs.create",
    "firebaseauth.configs.get",
    "firebaseauth.configs.getHashConfig",
    "firebaseauth.configs.getSecret",
    "firebaseauth.configs.update",
    "firebaseauth.users.create",
    "firebaseauth.users.createSession",
    "firebaseauth.users.delete",
    "firebaseauth.users.get",
    "firebaseauth.users.sendEmail",
    "firebaseauth.users.update",
)
EXPECTED_FIREBASE_RUNTIME_AUTH_PERMISSIONS = frozenset(
    ("firebaseauth.configs.get", "firebaseauth.users.get")
)

REQUIRED_PROVIDERS = (
    "Microsoft.App",
    "Microsoft.Authorization",
    "Microsoft.ContainerRegistry",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.OperationalInsights",
)

REQUIRED_FRONTEND_KEYS = (
    "NEXT_PUBLIC_FIREBASE_API_KEY",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
    "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET",
    "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
    "NEXT_PUBLIC_FIREBASE_APP_ID",
)
OPTIONAL_FRONTEND_KEYS = (
    "NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID",
    "NEXT_PUBLIC_VOICE_RUNTIME",
)
REQUIRED_SERVICE_ACCOUNT_FIELDS = (
    "type",
    "project_id",
    "private_key",
    "client_email",
    "token_uri",
)

_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_AZURE_RESOURCE_NAME = re.compile(r"[a-zA-Z0-9._()\-]{1,90}\Z")
_CONTAINER_APP_NAME = re.compile(r"[a-z][a-z0-9-]{0,30}[a-z0-9]\Z")
_AZURE_LOCATION = re.compile(r"[a-z0-9]{2,32}\Z")
_REGISTRY_NAME = re.compile(r"[a-z0-9]{5,50}\Z")
_ROLE_DEFINITION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_AZURE_GUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_KEY_VAULT_SECRET_VERSION = re.compile(r"[0-9a-f]{32}\Z", re.IGNORECASE)
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_MAX_DOTENV_BYTES = 256 * 1024
_MAX_FIREBASE_JSON_BYTES = 24 * 1024


class DeploymentRefusal(RuntimeError):
    """A safe, operator-actionable reason not to continue."""


@dataclass(frozen=True)
class SourceRevision:
    sha: str
    branch: str
    remote: str
    remote_ref: str


@dataclass(frozen=True, repr=False)
class DeploymentInputs:
    azure_openai_key: str
    azure_openai_endpoint: str
    azure_openai_deployment: str
    firebase_project_id: str
    firebase_runtime_service_account: Mapping[str, object]
    firebase_domain_admin_service_account: Mapping[str, object] | None
    frontend_public: Mapping[str, str]

    def firebase_runtime_json_bytes(self) -> bytes:
        return json.dumps(
            self.firebase_runtime_service_account,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class FirebaseDomainResult:
    status: str
    hostname: str


@dataclass(frozen=True)
class KeyVaultSecretVersion:
    version: str
    enabled: bool
    rotation_id: str | None


@dataclass(frozen=True)
class RoleAssignmentMetadata:
    id: str
    scope: str
    principal_id: str
    role_definition_id: str
    description: str | None


class DeploymentLease:
    def __init__(
        self, *, account_name: str, container_name: str, blob_name: str, lease_id: str
    ) -> None:
        self.account_name = account_name
        self.container_name = container_name
        self.blob_name = blob_name
        self.lease_id = lease_id
        self._lock = threading.Lock()
        self._last_renewal = time.monotonic()
        self._failure: str | None = None

    def mark_renewed(self) -> None:
        with self._lock:
            self._last_renewal = time.monotonic()

    def mark_lost(self) -> None:
        with self._lock:
            self._failure = "Azure deployment lease renewal failed"

    def assert_healthy(self) -> None:
        with self._lock:
            failure = self._failure
            age = time.monotonic() - self._last_renewal
        if failure is not None or age >= DEPLOYMENT_LEASE_SECONDS:
            raise DeploymentRefusal(failure or "Azure deployment lease renewal is stale")


@dataclass(frozen=True)
class AppInspection:
    name: str
    url: str
    image: str
    image_digest: str
    registry_server: str
    release_sha: str
    latest_revision: str
    latest_ready_revision: str | None
    provisioning_state: str
    running_status: str
    min_replicas: int
    max_replicas: int
    probe_types: tuple[str, ...]
    key_vault_name: str | None = None
    key_vault_secret_versions: tuple[tuple[str, str], ...] = ()
    identity_id: str | None = None


def _operation_label(command: Sequence[str]) -> str:
    if not command:
        return "external command"
    if command[0] == "az" and len(command) >= 3:
        return f"az {command[1]} {command[2]}"
    if command[0] == "git" and len(command) >= 2:
        return f"git {command[1]}"
    return Path(command[0]).name


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path = PROJECT_ROOT,
    timeout_seconds: float = 600,
    operation: str | None = None,
) -> str:
    """Run a command while containing stdout, stderr, and exception text."""

    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        label = operation or _operation_label(command)
        raise DeploymentRefusal(f"{label} could not complete ({type(exc).__name__})") from None

    if completed.returncode != 0:
        label = operation or _operation_label(command)
        raise DeploymentRefusal(f"{label} failed with exit code {completed.returncode}")
    return completed.stdout


def _run_json(
    command: Sequence[str],
    *,
    timeout_seconds: float = 600,
    operation: str | None = None,
) -> Any:
    raw = _run_command(command, timeout_seconds=timeout_seconds, operation=operation)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        label = operation or _operation_label(command)
        raise DeploymentRefusal(f"{label} returned invalid JSON") from None


def _validate_storage_name(value: str, label: str, pattern: str) -> str:
    if re.fullmatch(pattern, value) is None:
        raise DeploymentRefusal(f"foundation returned an invalid {label}")
    return value


def _blob_args(
    *, account_name: str, container_name: str, blob_name: str, subscription_id: str
) -> list[str]:
    return [
        "--account-name",
        account_name,
        "--container-name",
        container_name,
        "--blob-name",
        blob_name,
        "--subscription",
        subscription_id,
        "--auth-mode",
        "login",
        "--only-show-errors",
    ]


def _show_deployment_lock_blob(
    *, account_name: str, container_name: str, blob_name: str, subscription_id: str
) -> None:
    observed = _run_json(
        [
            "az",
            "storage",
            "blob",
            "show",
            "--account-name",
            account_name,
            "--container-name",
            container_name,
            "--name",
            blob_name,
            "--subscription",
            subscription_id,
            "--auth-mode",
            "login",
            "--only-show-errors",
            "--query",
            "{name:name,type:properties.blobType}",
            "--output",
            "json",
        ],
        timeout_seconds=30,
        operation="inspect Azure deployment lock blob",
    )
    if observed != {"name": blob_name, "type": "BlockBlob"}:
        raise DeploymentRefusal("Azure deployment lock anchor is not a block blob")


def _ensure_deployment_lock_blob(
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    subscription_id: str,
    attempts: int = 12,
) -> None:
    if attempts < 1:
        raise DeploymentRefusal("Azure deployment lock checks require positive attempts")
    for attempt in range(attempts):
        try:
            _run_command(
                [
                    "az",
                    "storage",
                    "blob",
                    "upload",
                    "--account-name",
                    account_name,
                    "--container-name",
                    container_name,
                    "--name",
                    blob_name,
                    "--subscription",
                    subscription_id,
                    "--data",
                    "{}",
                    "--type",
                    "block",
                    "--overwrite",
                    "false",
                    "--auth-mode",
                    "login",
                    "--only-show-errors",
                    "--output",
                    "none",
                ],
                timeout_seconds=30,
                operation="create Azure deployment lock blob",
            )
        except DeploymentRefusal:
            pass
        try:
            # Existing anchors and accepted-write timeouts are safe only after exact readback.
            _show_deployment_lock_blob(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                subscription_id=subscription_id,
            )
            return
        except DeploymentRefusal:
            if attempt + 1 == attempts:
                raise DeploymentRefusal(
                    "Azure deployment lock blob setup did not become available"
                ) from None
            time.sleep(min(2**attempt, 10))


def _lease_command(
    action: str,
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    lease_id: str,
    subscription_id: str,
) -> list[str]:
    command = ["az", "storage", "blob", "lease", action]
    command.extend(
        _blob_args(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            subscription_id=subscription_id,
        )
    )
    if action == "acquire":
        command.extend(
            ["--lease-duration", str(DEPLOYMENT_LEASE_SECONDS), "--proposed-lease-id", lease_id]
        )
    else:
        command.extend(["--lease-id", lease_id])
    command.extend(["--output", "none" if action == "release" else "tsv"])
    return command


def _renew_deployment_lease(
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    lease_id: str,
    subscription_id: str,
) -> None:
    returned = _run_command(
        _lease_command(
            "renew",
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            lease_id=lease_id,
            subscription_id=subscription_id,
        ),
        timeout_seconds=20,
        operation="renew Azure deployment lease",
    ).strip()
    if returned != lease_id:
        raise DeploymentRefusal("Azure deployment lease renewal returned a different lease")


def _acquire_deployment_lease(
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    lease_id: str,
    subscription_id: str,
) -> None:
    try:
        returned = _run_command(
            _lease_command(
                "acquire",
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                lease_id=lease_id,
                subscription_id=subscription_id,
            ),
            timeout_seconds=20,
            operation="acquire Azure deployment lease",
        ).strip()
    except DeploymentRefusal:
        # A timeout may be accepted. Repeating acquire with the same proposed ID
        # is the only safe ownership reconciliation and never breaks another lease.
        try:
            returned = _run_command(
                _lease_command(
                    "acquire",
                    account_name=account_name,
                    container_name=container_name,
                    blob_name=blob_name,
                    lease_id=lease_id,
                    subscription_id=subscription_id,
                ),
                timeout_seconds=20,
                operation="reconcile Azure deployment lease acquisition",
            ).strip()
        except DeploymentRefusal:
            raise DeploymentRefusal(
                "Azure deployment lease is unavailable or held by another deployment"
            ) from None
        if returned != lease_id:
            raise DeploymentRefusal(
                "Azure deployment lease reconciliation returned a different lease"
            ) from None
        return
    if returned != lease_id:
        raise DeploymentRefusal("Azure deployment lease acquisition returned a different lease")


def _release_deployment_lease(
    *,
    account_name: str,
    container_name: str,
    blob_name: str,
    lease_id: str,
    subscription_id: str,
) -> None:
    _run_command(
        _lease_command(
            "release",
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            lease_id=lease_id,
            subscription_id=subscription_id,
        ),
        timeout_seconds=20,
        operation="release Azure deployment lease",
    )


@contextmanager
def _deployment_blob_lease(
    *, account_name: str, container_name: str, blob_name: str, subscription_id: str
) -> Iterator[DeploymentLease]:
    account_name = _validate_storage_name(
        account_name, "deployment-lock account", r"[a-z0-9]{3,24}"
    )
    container_name = _validate_storage_name(
        container_name, "deployment-lock container", r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?"
    )
    blob_name = _validate_storage_name(blob_name, "deployment-lock blob", r"[A-Za-z0-9._-]{1,128}")
    _ensure_deployment_lock_blob(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        subscription_id=subscription_id,
    )
    lease_id = str(uuid.uuid4())
    _acquire_deployment_lease(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        lease_id=lease_id,
        subscription_id=subscription_id,
    )
    lease = DeploymentLease(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        lease_id=lease_id,
    )
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(DEPLOYMENT_LEASE_RENEW_SECONDS):
            try:
                _renew_deployment_lease(
                    account_name=account_name,
                    container_name=container_name,
                    blob_name=blob_name,
                    lease_id=lease_id,
                    subscription_id=subscription_id,
                )
            except DeploymentRefusal:
                lease.mark_lost()
                return
            lease.mark_renewed()

    worker = threading.Thread(target=renew, name="murmur-azure-lease", daemon=True)
    worker.start()
    body_failed = False
    try:
        yield lease
        lease.assert_healthy()
    except BaseException:
        body_failed = True
        raise
    finally:
        stop.set()
        worker.join(timeout=25)
        try:
            _release_deployment_lease(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                lease_id=lease_id,
                subscription_id=subscription_id,
            )
        except DeploymentRefusal:
            if not body_failed:
                raise


def _require_safe_resource_name(value: str, label: str) -> str:
    if not _AZURE_RESOURCE_NAME.fullmatch(value):
        raise DeploymentRefusal(f"{label} is not a safe Azure resource name")
    return value


def _require_container_app_name(value: str, label: str) -> str:
    if not _CONTAINER_APP_NAME.fullmatch(value) or "--" in value:
        raise DeploymentRefusal(f"{label} is not a valid Container App name")
    return value


def _require_location(value: str) -> str:
    if not _AZURE_LOCATION.fullmatch(value):
        raise DeploymentRefusal("location is not a safe Azure region name")
    return value


def _same_azure_resource_id(left: object, right: object) -> bool:
    """Compare Azure resource IDs using Azure's case-insensitive semantics."""

    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.rstrip("/").casefold() == right.rstrip("/").casefold()
    )


def _validate_https_url(value: str, label: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise DeploymentRefusal(f"{label} is not a valid HTTPS URL") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DeploymentRefusal(f"{label} is not a valid HTTPS URL")
    return value.rstrip("/")


def _validate_container_app_url(value: str, label: str) -> str:
    normalized = _validate_https_url(value, label)
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.path not in {"", "/"} or not parsed.hostname.endswith(".azurecontainerapps.io"):
        raise DeploymentRefusal(f"{label} is not an Azure Container Apps origin")
    return normalized


def _validate_azure_openai_endpoint(value: str) -> str:
    normalized = _validate_https_url(value.strip(), "AZURE_OPENAI_ENDPOINT")
    parsed = urllib.parse.urlsplit(normalized)
    assert parsed.hostname is not None
    if not parsed.hostname.endswith((".openai.azure.com", ".services.ai.azure.com")):
        raise DeploymentRefusal("AZURE_OPENAI_ENDPOINT is not an Azure OpenAI resource")
    if parsed.path not in {"", "/", "/openai/v1"}:
        raise DeploymentRefusal("AZURE_OPENAI_ENDPOINT has an unsupported path")
    return normalized


def validate_source_revision() -> SourceRevision:
    """Return HEAD only when the worktree is clean and its remote branch matches."""

    status = _run_command(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        operation="inspect git worktree",
    )
    if status:
        raise DeploymentRefusal("git worktree is not clean")

    sha = _run_command(
        ["git", "rev-parse", "--verify", "HEAD"], operation="resolve git HEAD"
    ).strip()
    if not _FULL_SHA.fullmatch(sha):
        raise DeploymentRefusal("git HEAD is not a full SHA")

    branch = _run_command(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        operation="resolve git branch",
    ).strip()
    if not branch:
        raise DeploymentRefusal("deployment requires a named git branch")

    upstream_sha = _run_command(
        ["git", "rev-parse", "--verify", "@{upstream}"],
        operation="resolve git upstream",
    ).strip()
    if upstream_sha != sha:
        raise DeploymentRefusal("git HEAD does not match its pushed upstream")

    remote = _run_command(
        ["git", "config", "--get", f"branch.{branch}.remote"],
        operation="resolve git remote",
    ).strip()
    remote_ref = _run_command(
        ["git", "config", "--get", f"branch.{branch}.merge"],
        operation="resolve git remote branch",
    ).strip()
    if not remote or remote == "." or not remote_ref.startswith("refs/heads/"):
        raise DeploymentRefusal("git branch does not track a pushed remote branch")

    remote_line = _run_command(
        ["git", "ls-remote", "--exit-code", remote, remote_ref],
        timeout_seconds=60,
        operation="verify pushed git revision",
    ).strip()
    remote_sha = remote_line.split(maxsplit=1)[0] if remote_line else ""
    if remote_sha != sha:
        raise DeploymentRefusal("remote branch does not contain the exact local HEAD")

    return SourceRevision(sha=sha, branch=branch, remote=remote, remote_ref=remote_ref)


def _load_dotenv(path: Path, label: str) -> dict[str, str]:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise DeploymentRefusal(f"{label} dotenv file must not be a symlink")
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise DeploymentRefusal(f"{label} dotenv file does not exist")
    if resolved.stat().st_size > _MAX_DOTENV_BYTES:
        raise DeploymentRefusal(f"{label} dotenv file is unexpectedly large")
    if label == "backend" and stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise DeploymentRefusal("backend dotenv permissions must be owner-only")
    try:
        parsed = dotenv_values(resolved, interpolate=False)
    except (OSError, UnicodeError):
        raise DeploymentRefusal(f"{label} dotenv file could not be parsed") from None

    values: dict[str, str] = {}
    for key, value in parsed.items():
        if not _ENV_KEY.fullmatch(key):
            raise DeploymentRefusal(f"{label} dotenv contains an invalid key")
        if value is None:
            raise DeploymentRefusal(f"{label} dotenv contains a key without a value")
        values[key] = value
    return values


def _required_value(values: Mapping[str, str], key: str, label: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise DeploymentRefusal(f"{label} is missing {key}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DeploymentRefusal(f"{label} contains an invalid value for {key}")
    return value


def _load_service_account(
    raw_path: str,
    *,
    relative_to: Path,
    expected_project_id: str,
) -> Mapping[str, object]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    if candidate.is_symlink():
        raise DeploymentRefusal("Firebase service-account path must not be a symlink")
    path = candidate.resolve()
    if not path.is_file():
        raise DeploymentRefusal("Firebase service-account path is not a regular file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise DeploymentRefusal("Firebase service-account permissions must be owner-only")
    if path.stat().st_size > _MAX_FIREBASE_JSON_BYTES:
        raise DeploymentRefusal("Firebase service-account JSON is too large for Key Vault")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DeploymentRefusal("Firebase service-account JSON is invalid") from None
    if not isinstance(document, dict):
        raise DeploymentRefusal("Firebase service-account JSON must be an object")
    for field in REQUIRED_SERVICE_ACCOUNT_FIELDS:
        if not isinstance(document.get(field), str) or not document[field]:
            raise DeploymentRefusal("Firebase service-account JSON is incomplete")
    if document["type"] != "service_account":
        raise DeploymentRefusal("Firebase credential is not a service account")
    if document["project_id"] != expected_project_id:
        raise DeploymentRefusal(
            "Firebase service-account project does not match FIREBASE_PROJECT_ID"
        )
    if not str(document["private_key"]).startswith("-----BEGIN PRIVATE KEY-----"):
        raise DeploymentRefusal("Firebase service-account private key is invalid")
    return document


def load_deployment_inputs(backend_env_path: Path, frontend_env_path: Path) -> DeploymentInputs:
    backend = _load_dotenv(backend_env_path, "backend")
    frontend = _load_dotenv(frontend_env_path, "frontend")

    firebase_project_id = _required_value(backend, "FIREBASE_PROJECT_ID", "backend dotenv")
    frontend_project_id = _required_value(
        frontend, "NEXT_PUBLIC_FIREBASE_PROJECT_ID", "frontend dotenv"
    )
    if frontend_project_id != firebase_project_id:
        raise DeploymentRefusal("frontend and backend Firebase project IDs do not match")

    runtime_account_path = _required_value(
        backend,
        "FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH",
        "backend dotenv",
    )
    runtime_service_account = _load_service_account(
        runtime_account_path,
        relative_to=backend_env_path.expanduser().resolve().parent,
        expected_project_id=firebase_project_id,
    )
    domain_admin_path = backend.get("FIREBASE_DOMAIN_ADMIN_SERVICE_ACCOUNT_PATH", "").strip()
    domain_admin_service_account = (
        _load_service_account(
            domain_admin_path,
            relative_to=backend_env_path.expanduser().resolve().parent,
            expected_project_id=firebase_project_id,
        )
        if domain_admin_path
        else None
    )
    if domain_admin_service_account is not None:
        same_principal = (
            domain_admin_service_account["client_email"] == runtime_service_account["client_email"]
        )
        runtime_key_id = runtime_service_account.get("private_key_id")
        admin_key_id = domain_admin_service_account.get("private_key_id")
        same_key = bool(runtime_key_id) and runtime_key_id == admin_key_id
        same_private_key = (
            domain_admin_service_account["private_key"] == runtime_service_account["private_key"]
        )
        if same_principal or same_key or same_private_key:
            raise DeploymentRefusal(
                "Firebase runtime and domain-admin credentials must be different principals and keys"
            )

    frontend_public = {
        key: _required_value(frontend, key, "frontend dotenv") for key in REQUIRED_FRONTEND_KEYS
    }
    for key in OPTIONAL_FRONTEND_KEYS:
        value = frontend.get(key, "").strip()
        if value:
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise DeploymentRefusal(f"frontend dotenv contains an invalid value for {key}")
            frontend_public[key] = value

    return DeploymentInputs(
        azure_openai_key=_required_value(backend, "AZURE_OPENAI_API_KEY", "backend dotenv"),
        azure_openai_endpoint=_validate_azure_openai_endpoint(
            _required_value(backend, "AZURE_OPENAI_ENDPOINT", "backend dotenv")
        ),
        azure_openai_deployment=_required_value(
            backend, "AZURE_OPENAI_DEPLOYMENT", "backend dotenv"
        ),
        firebase_project_id=firebase_project_id,
        firebase_runtime_service_account=runtime_service_account,
        firebase_domain_admin_service_account=domain_admin_service_account,
        frontend_public=frontend_public,
    )


def _validate_azure_session(*, subscription_id: str, tenant_id: str) -> None:
    if shutil.which("az") is None:
        raise DeploymentRefusal("Azure CLI is not installed")
    if not _AZURE_GUID.fullmatch(subscription_id) or not _AZURE_GUID.fullmatch(tenant_id):
        raise DeploymentRefusal("expected Azure subscription and tenant IDs must be GUIDs")
    account = _run_json(
        ["az", "account", "show", "--output", "json", "--only-show-errors"],
        timeout_seconds=30,
        operation="validate Azure login",
    )
    if not isinstance(account, dict) or account.get("state") != "Enabled":
        raise DeploymentRefusal("active Azure subscription is not enabled")
    if (
        str(account.get("id", "")).casefold() != subscription_id.casefold()
        or str(account.get("tenantId", "")).casefold() != tenant_id.casefold()
    ):
        raise DeploymentRefusal("active Azure account does not match the pinned deployment target")
    _run_command(
        ["az", "bicep", "version", "--only-show-errors"],
        timeout_seconds=60,
        operation="validate Azure Bicep CLI",
    )


def _register_providers() -> None:
    for namespace in REQUIRED_PROVIDERS:
        _run_command(
            [
                "az",
                "provider",
                "register",
                "--namespace",
                namespace,
                "--wait",
                "--only-show-errors",
                "--output",
                "none",
            ],
            timeout_seconds=900,
            operation=f"register Azure provider {namespace}",
        )


def _create_resource_group(resource_group: str, location: str, release_sha: str) -> None:
    exists = _run_command(
        ["az", "group", "exists", "--name", resource_group, "--output", "tsv"],
        timeout_seconds=30,
        operation="check Murmur resource group",
    ).strip()
    if exists not in {"true", "false"}:
        raise DeploymentRefusal("Azure returned an invalid resource-group existence result")
    if exists == "true":
        current = _run_json(
            [
                "az",
                "group",
                "show",
                "--name",
                resource_group,
                "--query",
                "{location:location,tags:tags}",
                "--output",
                "json",
                "--only-show-errors",
            ],
            timeout_seconds=30,
            operation="inspect existing Murmur resource group",
        )
        if not isinstance(current, dict):
            raise DeploymentRefusal("existing Murmur resource group metadata is invalid")
        tags = current.get("tags")
        if (
            str(current.get("location", "")).casefold() != location.casefold()
            or not isinstance(tags, dict)
            or tags.get("product") != "murmur"
            or tags.get("environment") != "pilot"
        ):
            raise DeploymentRefusal(
                "existing resource group is not the isolated Central India Murmur pilot"
            )
        return
    _run_command(
        [
            "az",
            "group",
            "create",
            "--name",
            resource_group,
            "--location",
            location,
            "--tags",
            "product=murmur",
            "environment=pilot",
            "managed-by=deploy-driver",
            f"release-sha={release_sha}",
            "--only-show-errors",
            "--output",
            "none",
        ],
        operation="create isolated Murmur resource group",
    )


def _deployment_outputs(
    *,
    resource_group: str,
    deployment_name: str,
    template: Path,
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    command = [
        "az",
        "deployment",
        "group",
        "create",
        "--resource-group",
        resource_group,
        "--name",
        deployment_name,
        "--template-file",
        str(template),
        "--only-show-errors",
        "--query",
        "properties.outputs",
        "--output",
        "json",
    ]
    if parameters:
        command.append("--parameters")
        command.extend(
            f"{key}={'true' if value is True else 'false' if value is False else value}"
            for key, value in parameters.items()
        )
    outputs = _run_json(command, timeout_seconds=1800, operation=f"deploy {deployment_name}")
    if not isinstance(outputs, dict):
        raise DeploymentRefusal(f"{deployment_name} returned invalid outputs")
    return outputs


def _existing_deployment_outputs(
    *, resource_group: str, deployment_name: str
) -> Mapping[str, object]:
    outputs = _run_json(
        [
            "az",
            "deployment",
            "group",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            deployment_name,
            "--query",
            "properties.outputs",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect {deployment_name}",
    )
    if not isinstance(outputs, dict):
        raise DeploymentRefusal(f"{deployment_name} returned invalid outputs")
    return outputs


def _output_value(outputs: Mapping[str, object], name: str) -> str:
    item = outputs.get(name)
    if not isinstance(item, dict) or not isinstance(item.get("value"), str) or not item["value"]:
        raise DeploymentRefusal(f"Azure deployment omitted output {name}")
    return item["value"]


def _current_principal() -> tuple[str, str]:
    account = _run_json(
        ["az", "account", "show", "--query", "user", "--output", "json"],
        timeout_seconds=30,
        operation="inspect Azure principal type",
    )
    if not isinstance(account, dict):
        raise DeploymentRefusal("Azure principal metadata is unavailable")
    principal_type = str(account.get("type", "")).casefold()
    if principal_type == "user":
        object_id = _run_command(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "tsv"],
            timeout_seconds=60,
            operation="resolve Azure user object ID",
        ).strip()
        azure_type = "User"
    elif principal_type == "serviceprincipal":
        client_id = account.get("name")
        if not isinstance(client_id, str) or not client_id:
            raise DeploymentRefusal("Azure service principal metadata is incomplete")
        object_id = _run_command(
            ["az", "ad", "sp", "show", "--id", client_id, "--query", "id", "--output", "tsv"],
            timeout_seconds=60,
            operation="resolve Azure service-principal object ID",
        ).strip()
        azure_type = "ServicePrincipal"
    else:
        raise DeploymentRefusal("unsupported Azure principal type")
    if not object_id:
        raise DeploymentRefusal("Azure principal object ID is unavailable")
    return object_id, azure_type


def _writer_assignment_name(key_vault_id: str, object_id: str) -> str:
    return str(
        uuid.uuid5(
            KEY_VAULT_WRITER_ASSIGNMENT_NAMESPACE,
            f"{key_vault_id.rstrip('/').casefold()}|{object_id.casefold()}",
        )
    )


def _role_definition_guid(value: object) -> str:
    if not isinstance(value, str):
        raise DeploymentRefusal("Azure returned invalid role-assignment metadata")
    role_id = value.rstrip("/").rsplit("/", 1)[-1].casefold()
    if not _ROLE_DEFINITION_ID.fullmatch(role_id):
        raise DeploymentRefusal("Azure returned invalid role-assignment metadata")
    return role_id


def _direct_key_vault_writer_assignments(key_vault_id: str) -> tuple[RoleAssignmentMetadata, ...]:
    raw = _run_json(
        [
            "az",
            "role",
            "assignment",
            "list",
            "--scope",
            key_vault_id,
            "--fill-principal-name",
            "false",
            "--query",
            "[].{id:id,scope:scope,principalId:principalId,roleDefinitionId:roleDefinitionId,description:description}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect deployer Key Vault secret-write access",
    )
    if not isinstance(raw, list):
        raise DeploymentRefusal("Azure returned invalid Key Vault role-assignment metadata")
    assignments: list[RoleAssignmentMetadata] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DeploymentRefusal("Azure returned invalid Key Vault role-assignment metadata")
        role_id = _role_definition_guid(item.get("roleDefinitionId"))
        scope = item.get("scope")
        if role_id != KEY_VAULT_SECRETS_OFFICER_ROLE_ID or not _same_azure_resource_id(
            scope, key_vault_id
        ):
            continue
        assignment_id = item.get("id")
        principal_id = item.get("principalId")
        description = item.get("description")
        if (
            not isinstance(assignment_id, str)
            or not isinstance(principal_id, str)
            or not _AZURE_GUID.fullmatch(principal_id)
            or (description is not None and not isinstance(description, str))
        ):
            raise DeploymentRefusal("Azure returned invalid Key Vault role-assignment metadata")
        assignments.append(
            RoleAssignmentMetadata(
                id=assignment_id,
                scope=scope,
                principal_id=principal_id,
                role_definition_id=role_id,
                description=description,
            )
        )
    return tuple(assignments)


def _expected_writer_assignment(key_vault_id: str, *, object_id: str) -> RoleAssignmentMetadata:
    assignment_name = _writer_assignment_name(key_vault_id, object_id)
    return RoleAssignmentMetadata(
        id=(
            f"{key_vault_id.rstrip('/')}/providers/Microsoft.Authorization/"
            f"roleAssignments/{assignment_name}"
        ),
        scope=key_vault_id.rstrip("/"),
        principal_id=object_id,
        role_definition_id=KEY_VAULT_SECRETS_OFFICER_ROLE_ID,
        description=KEY_VAULT_WRITER_DESCRIPTION,
    )


def _same_writer_assignment(
    actual: RoleAssignmentMetadata, expected: RoleAssignmentMetadata
) -> bool:
    return (
        _same_azure_resource_id(actual.id, expected.id)
        and _same_azure_resource_id(actual.scope, expected.scope)
        and actual.principal_id.casefold() == expected.principal_id.casefold()
        and actual.role_definition_id == expected.role_definition_id
        and actual.description == expected.description
    )


def _await_writer_assignment(
    key_vault_id: str,
    *,
    expected: RoleAssignmentMetadata,
    present: bool,
    attempts: int = 8,
) -> None:
    for attempt in range(attempts):
        matches = [
            assignment
            for assignment in _direct_key_vault_writer_assignments(key_vault_id)
            if _same_azure_resource_id(assignment.id, expected.id)
        ]
        if not matches and not present:
            return
        if len(matches) == 1 and present:
            if not _same_writer_assignment(matches[0], expected):
                raise DeploymentRefusal("temporary Key Vault writer metadata is ambiguous")
            return
        if len(matches) > 1:
            raise DeploymentRefusal("temporary Key Vault writer metadata is ambiguous")
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 8))
    state = "appear" if present else "disappear"
    raise DeploymentRefusal(f"temporary Key Vault writer assignment did not {state}")


def _grant_key_vault_write(
    key_vault_id: str,
    *,
    principal: tuple[str, str] | None = None,
) -> RoleAssignmentMetadata:
    object_id, principal_type = principal or _current_principal()
    if _direct_key_vault_writer_assignments(key_vault_id):
        raise DeploymentRefusal(
            "deployer already has a direct Key Vault Secrets Officer assignment; "
            "remove or separately review it before deployment"
        )
    expected = _expected_writer_assignment(key_vault_id, object_id=object_id)
    try:
        _run_command(
            [
                "az",
                "role",
                "assignment",
                "create",
                "--name",
                expected.id.rsplit("/", 1)[-1],
                "--assignee-object-id",
                object_id,
                "--assignee-principal-type",
                principal_type,
                "--role",
                KEY_VAULT_WRITER_ROLE,
                "--scope",
                key_vault_id,
                "--description",
                KEY_VAULT_WRITER_DESCRIPTION,
                "--only-show-errors",
                "--output",
                "none",
            ],
            timeout_seconds=60,
            operation="grant deployer Key Vault secret-write access",
        )
    except DeploymentRefusal:
        # A create timeout can be accepted; the exact deterministic assignment is proof.
        _await_writer_assignment(key_vault_id, expected=expected, present=True)
        return expected
    _await_writer_assignment(key_vault_id, expected=expected, present=True)
    return expected


def _revoke_key_vault_write(
    key_vault_id: str,
    assignment: RoleAssignmentMetadata,
    *,
    attempts: int = 8,
) -> None:
    _await_writer_assignment(key_vault_id, expected=assignment, present=True, attempts=1)
    for attempt in range(attempts):
        try:
            _run_command(
                [
                    "az",
                    "role",
                    "assignment",
                    "delete",
                    "--ids",
                    assignment.id,
                    "--only-show-errors",
                    "--output",
                    "none",
                ],
                timeout_seconds=60,
                operation="revoke temporary deployer Key Vault secret-write access",
            )
        except DeploymentRefusal:
            pass
        try:
            _await_writer_assignment(key_vault_id, expected=assignment, present=False, attempts=1)
            return
        except DeploymentRefusal:
            current = [
                item
                for item in _direct_key_vault_writer_assignments(key_vault_id)
                if _same_azure_resource_id(item.id, assignment.id)
            ]
            if len(current) != 1 or not _same_writer_assignment(current[0], assignment):
                raise DeploymentRefusal(
                    "temporary Key Vault writer metadata is ambiguous"
                ) from None
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 8))
    raise DeploymentRefusal("temporary Key Vault writer assignment remains after cleanup")


@contextmanager
def _temporary_key_vault_write(
    key_vault_id: str,
    *,
    principal: tuple[str, str] | None = None,
    mutation_guard: Callable[[], None] | None = None,
) -> Iterator[None]:
    if mutation_guard is not None:
        mutation_guard()
    assignment = _grant_key_vault_write(key_vault_id, principal=principal)
    try:
        yield
    finally:
        _revoke_key_vault_write(key_vault_id, assignment)


@contextmanager
def _secure_temp_file(payload: bytes, *, suffix: str) -> Iterator[Path]:
    fd, raw_path = tempfile.mkstemp(prefix="murmur-deploy-", suffix=suffix)
    path = Path(raw_path)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise DeploymentRefusal("secure temporary file does not have mode 600")
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_key_vault_secret(
    *,
    vault_name: str,
    secret_name: str,
    payload: bytes,
    attempts: int = 32,
    mutation_guard: Callable[[], None] | None = None,
) -> str:
    if attempts < 1:
        raise DeploymentRefusal("Key Vault reconciliation attempts must be positive")
    _await_key_vault_data_plane_access(vault_name=vault_name, attempts=attempts)
    rotation_id = secrets.token_hex(16)
    secret_id: str | None = None
    with _secure_temp_file(payload, suffix=".secret") as path:
        if mutation_guard is not None:
            mutation_guard()
        try:
            secret_id = _run_command(
                [
                    "az",
                    "keyvault",
                    "secret",
                    "set",
                    "--vault-name",
                    vault_name,
                    "--name",
                    secret_name,
                    "--file",
                    str(path),
                    "--encoding",
                    "utf-8",
                    "--tags",
                    f"{KEY_VAULT_ROTATION_TAG}={rotation_id}",
                    "--only-show-errors",
                    "--query",
                    "id",
                    "--output",
                    "tsv",
                ],
                timeout_seconds=60,
                operation=f"write Key Vault secret {secret_name}",
            ).strip()
        except DeploymentRefusal:
            # The service may have accepted the write even when the CLI timed out.
            # Never issue a second write blindly: reconcile by the unique, non-secret tag.
            secret_id = None

    try:
        returned_version = (
            _key_vault_secret_version(
                secret_id,
                vault_name=vault_name,
                secret_name=secret_name,
            )
            if secret_id
            else None
        )
    except DeploymentRefusal:
        # A malformed or truncated CLI response is reconciled from metadata using
        # the same unique tag; it never triggers a second write.
        returned_version = None
    return _await_staged_key_vault_secret(
        vault_name=vault_name,
        secret_name=secret_name,
        rotation_id=rotation_id,
        returned_version=returned_version,
        attempts=attempts,
    )


def _key_vault_secret_version(
    secret_id: str,
    *,
    vault_name: str,
    secret_name: str,
) -> str:
    parsed = urllib.parse.urlsplit(secret_id)
    path_parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != f"{vault_name}.vault.azure.net"
        or parsed.query
        or parsed.fragment
        or len(path_parts) != 3
        or path_parts[:2] != ["secrets", secret_name]
        or not _KEY_VAULT_SECRET_VERSION.fullmatch(path_parts[2])
    ):
        raise DeploymentRefusal("Azure returned an invalid Key Vault secret version")
    return path_parts[2]


def _parse_key_vault_secret_version(
    item: object,
    *,
    vault_name: str,
    secret_name: str,
) -> KeyVaultSecretVersion:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    enabled = item.get("enabled")
    if not isinstance(enabled, bool):
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    raw_tags = item.get("tags")
    if raw_tags is None:
        tags: Mapping[str, object] = {}
    elif isinstance(raw_tags, dict):
        tags = raw_tags
    else:
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    rotation_id = tags.get(KEY_VAULT_ROTATION_TAG)
    if rotation_id is not None and not isinstance(rotation_id, str):
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    return KeyVaultSecretVersion(
        version=_key_vault_secret_version(
            item["id"],
            vault_name=vault_name,
            secret_name=secret_name,
        ),
        enabled=enabled,
        rotation_id=rotation_id,
    )


def _list_key_vault_secret_versions(
    *, vault_name: str, secret_name: str
) -> tuple[KeyVaultSecretVersion, ...]:
    versions = _run_json(
        [
            "az",
            "keyvault",
            "secret",
            "list-versions",
            "--vault-name",
            vault_name,
            "--name",
            secret_name,
            "--query",
            "[].{id:id,enabled:attributes.enabled,tags:tags}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect Key Vault secret versions for {secret_name}",
    )
    if not isinstance(versions, list) or not versions:
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    parsed = tuple(
        sorted(
            (
                _parse_key_vault_secret_version(
                    item,
                    vault_name=vault_name,
                    secret_name=secret_name,
                )
                for item in versions
            ),
            key=lambda item: item.version.casefold(),
        )
    )
    normalized = [item.version.casefold() for item in parsed]
    if len(normalized) != len(set(normalized)):
        raise DeploymentRefusal("Azure returned duplicate Key Vault secret versions")
    return parsed


def _current_key_vault_secret_version(
    *, vault_name: str, secret_name: str
) -> KeyVaultSecretVersion:
    item = _run_json(
        [
            "az",
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            vault_name,
            "--name",
            secret_name,
            "--query",
            "{id:id,enabled:attributes.enabled,tags:tags}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect current Key Vault secret version for {secret_name}",
    )
    return _parse_key_vault_secret_version(
        item,
        vault_name=vault_name,
        secret_name=secret_name,
    )


def _await_key_vault_data_plane_access(*, vault_name: str, attempts: int) -> None:
    for attempt in range(attempts):
        try:
            ids = _run_json(
                [
                    "az",
                    "keyvault",
                    "secret",
                    "list",
                    "--vault-name",
                    vault_name,
                    "--maxresults",
                    "1",
                    "--query",
                    "[].id",
                    "--output",
                    "json",
                    "--only-show-errors",
                ],
                timeout_seconds=60,
                operation="confirm deployer Key Vault data-plane access",
            )
            if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                raise DeploymentRefusal("Azure returned invalid Key Vault access metadata")
            return
        except DeploymentRefusal:
            if attempt + 1 == attempts:
                raise DeploymentRefusal(
                    "deployer Key Vault data-plane access did not become available"
                ) from None
            time.sleep(min(2**attempt, 10))


def _await_staged_key_vault_secret(
    *,
    vault_name: str,
    secret_name: str,
    rotation_id: str,
    returned_version: str | None,
    attempts: int,
) -> str:
    for attempt in range(attempts):
        try:
            versions = _list_key_vault_secret_versions(
                vault_name=vault_name,
                secret_name=secret_name,
            )
            matches = [item for item in versions if item.rotation_id == rotation_id]
            if len(matches) > 1:
                raise DeploymentRefusal(
                    "Key Vault contains multiple versions for one rotation attempt"
                )
            if len(matches) == 1:
                selected = matches[0]
                if returned_version is not None and (
                    selected.version.casefold() != returned_version.casefold()
                ):
                    raise DeploymentRefusal("Key Vault write response does not match readback")
                current = _current_key_vault_secret_version(
                    vault_name=vault_name,
                    secret_name=secret_name,
                )
                if (
                    current.version.casefold() == selected.version.casefold()
                    and current.enabled
                    and current.rotation_id == rotation_id
                    and selected.enabled
                ):
                    return selected.version
        except DeploymentRefusal:
            if attempt + 1 == attempts:
                raise
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    raise DeploymentRefusal(
        "Key Vault secret write could not be reconciled; no duplicate write was attempted"
    )


def _stable_key_vault_rotation_snapshot(
    *,
    vault_name: str,
    secret_name: str,
    current_version: str,
    attempts: int,
) -> tuple[KeyVaultSecretVersion, ...]:
    for attempt in range(attempts):
        before = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=secret_name,
        )
        current = _current_key_vault_secret_version(
            vault_name=vault_name,
            secret_name=secret_name,
        )
        after = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=secret_name,
        )
        if current.version.casefold() != current_version.casefold():
            raise DeploymentRefusal("Key Vault current secret changed before rotation finalization")
        if not current.enabled:
            raise DeploymentRefusal("selected Key Vault secret version is not enabled")
        selected = [item for item in after if item.version.casefold() == current_version.casefold()]
        if len(selected) != 1 or not selected[0].enabled:
            raise DeploymentRefusal("selected Key Vault secret version is missing or disabled")
        if before == after:
            return after
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    raise DeploymentRefusal("Key Vault secret-version listing did not stabilize")


def _disable_key_vault_secret_version(
    *,
    vault_name: str,
    secret_name: str,
    version: str,
    mutation_guard: Callable[[], None] | None = None,
) -> None:
    if mutation_guard is not None:
        mutation_guard()
    try:
        _run_command(
            [
                "az",
                "keyvault",
                "secret",
                "set-attributes",
                "--vault-name",
                vault_name,
                "--name",
                secret_name,
                "--version",
                version,
                "--enabled",
                "false",
                "--output",
                "none",
                "--only-show-errors",
            ],
            timeout_seconds=60,
            operation=f"disable retired Key Vault secret version for {secret_name}",
        )
    except DeploymentRefusal:
        # A timeout may follow an accepted mutation. Exact readback in the caller
        # determines whether the version was actually disabled.
        pass


def _finalize_key_vault_secret_rotation(
    *,
    vault_name: str,
    secret_name: str,
    current_version: str,
    attempts: int = 8,
    mutation_guard: Callable[[], None] | None = None,
) -> None:
    if attempts < 1:
        raise DeploymentRefusal("Key Vault reconciliation attempts must be positive")
    snapshot = _stable_key_vault_rotation_snapshot(
        vault_name=vault_name,
        secret_name=secret_name,
        current_version=current_version,
        attempts=attempts,
    )
    expected_versions = {item.version.casefold() for item in snapshot}
    for item in snapshot:
        if item.version.casefold() == current_version.casefold() or not item.enabled:
            continue
        _disable_key_vault_secret_version(
            vault_name=vault_name,
            secret_name=secret_name,
            version=item.version,
            mutation_guard=mutation_guard,
        )

    for attempt in range(attempts):
        final = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=secret_name,
        )
        current = _current_key_vault_secret_version(
            vault_name=vault_name,
            secret_name=secret_name,
        )
        if {item.version.casefold() for item in final} != expected_versions:
            raise DeploymentRefusal(
                "Key Vault secret versions changed during rotation finalization"
            )
        selected = [item for item in final if item.version.casefold() == current_version.casefold()]
        if (
            current.version.casefold() != current_version.casefold()
            or not current.enabled
            or len(selected) != 1
            or not selected[0].enabled
        ):
            raise DeploymentRefusal("selected Key Vault secret version changed during finalization")
        if all(
            not item.enabled
            for item in final
            if item.version.casefold() != current_version.casefold()
        ):
            return
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    raise DeploymentRefusal("retired Key Vault secret versions remain enabled")


def _legacy_key_vault_secret_exists(*, vault_name: str) -> bool:
    names = _run_json(
        [
            "az",
            "keyvault",
            "secret",
            "list",
            "--vault-name",
            vault_name,
            "--query",
            f"[?name=='{LEGACY_FIREBASE_SECRET_NAME}'].name",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect legacy Firebase Key Vault secret",
    )
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise DeploymentRefusal("Azure returned invalid legacy secret metadata")
    normalized = [name.casefold() for name in names]
    expected = LEGACY_FIREBASE_SECRET_NAME.casefold()
    if any(name != expected for name in normalized) or len(normalized) > 1:
        raise DeploymentRefusal("Azure returned invalid legacy secret metadata")
    return normalized == [expected]


def _retire_legacy_firebase_secret(
    *,
    vault_name: str,
    attempts: int = 8,
    mutation_guard: Callable[[], None] | None = None,
) -> str:
    if attempts < 1:
        raise DeploymentRefusal("Key Vault reconciliation attempts must be positive")
    if not _legacy_key_vault_secret_exists(vault_name=vault_name):
        return "not_present"

    snapshot: tuple[KeyVaultSecretVersion, ...] | None = None
    for attempt in range(attempts):
        before = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=LEGACY_FIREBASE_SECRET_NAME,
        )
        after = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=LEGACY_FIREBASE_SECRET_NAME,
        )
        if before == after:
            snapshot = after
            break
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    if snapshot is None:
        raise DeploymentRefusal("legacy Firebase secret-version listing did not stabilize")

    expected_versions = {item.version.casefold() for item in snapshot}
    for item in snapshot:
        if item.enabled:
            _disable_key_vault_secret_version(
                vault_name=vault_name,
                secret_name=LEGACY_FIREBASE_SECRET_NAME,
                version=item.version,
                mutation_guard=mutation_guard,
            )

    for attempt in range(attempts):
        final = _list_key_vault_secret_versions(
            vault_name=vault_name,
            secret_name=LEGACY_FIREBASE_SECRET_NAME,
        )
        if {item.version.casefold() for item in final} != expected_versions:
            raise DeploymentRefusal("legacy Firebase secret versions changed during retirement")
        if all(not item.enabled for item in final):
            return "disabled_recoverable"
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    raise DeploymentRefusal("legacy Firebase secret versions remain enabled")


def _extract_git_archive(archive_path: Path, destination: Path) -> None:
    """Extract a trusted git archive without permitting filesystem escapes."""

    destination_root = destination.resolve()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            for member in members:
                if not (member.isdir() or member.isfile()):
                    raise DeploymentRefusal("git build archive contains an unsupported entry")
                target = (destination_root / member.name).resolve()
                if target != destination_root and destination_root not in target.parents:
                    raise DeploymentRefusal("git build archive contains an unsafe path")
            archive.extractall(destination_root, members=members)
    except (OSError, tarfile.TarError):
        raise DeploymentRefusal("git build archive could not be extracted") from None


@contextmanager
def _git_build_context(
    revision: str,
    *,
    paths: Sequence[str] = (),
    subtree: str | None = None,
) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="murmur-build-") as directory:
        temporary_root = Path(directory)
        archive = temporary_root / "context.tar"
        context = temporary_root / "context"
        context.mkdir(mode=0o700)
        treeish = f"{revision}:{subtree}" if subtree else revision
        command = ["git", "archive", "--format=tar", f"--output={archive}", treeish]
        if paths:
            command.extend(("--", *paths))
        _run_command(command, operation="create secret-free git build archive")
        archive.chmod(0o600)
        _extract_git_archive(archive, context)
        archive.unlink()
        yield context


def _acr_build(
    *,
    registry_name: str,
    repository: str,
    release_sha: str,
    dockerfile: str,
    context: Path,
    build_args: Mapping[str, str],
) -> str:
    build_tag = f"{release_sha}-{secrets.token_hex(8)}"
    command = [
        "az",
        "acr",
        "build",
        "--registry",
        registry_name,
        "--image",
        f"{repository}:{build_tag}",
        "--file",
        dockerfile,
        "--platform",
        "linux/amd64",
        "--only-show-errors",
        "--output",
        "none",
    ]
    for key, value in build_args.items():
        command.extend(("--build-arg", f"{key}={value}"))
    if not context.is_dir():
        raise DeploymentRefusal("ACR build context is not a local directory")
    command.append(".")
    _run_command(
        command,
        cwd=context,
        timeout_seconds=3600,
        operation=f"build ACR image {repository}",
    )
    digest = ""
    for attempt in range(5):
        try:
            digest = _run_command(
                [
                    "az",
                    "acr",
                    "repository",
                    "show",
                    "--name",
                    registry_name,
                    "--image",
                    f"{repository}:{build_tag}",
                    "--query",
                    "digest",
                    "--output",
                    "tsv",
                    "--only-show-errors",
                ],
                timeout_seconds=60,
                operation=f"resolve ACR image digest {repository}",
            ).strip()
            break
        except DeploymentRefusal:
            if attempt == 4:
                raise
            time.sleep(2**attempt)
    if not _IMAGE_DIGEST.fullmatch(digest):
        raise DeploymentRefusal(f"ACR returned an invalid image digest for {repository}")
    return digest


def _build_backend(registry_name: str, release_sha: str) -> str:
    with _git_build_context(
        release_sha,
        paths=("backend", "main.py", "requirements.txt", "deploy/backend.Dockerfile"),
    ) as context:
        return _acr_build(
            registry_name=registry_name,
            repository="murmur-api",
            release_sha=release_sha,
            dockerfile="deploy/backend.Dockerfile",
            context=context,
            build_args={"MURMUR_RELEASE_SHA": release_sha},
        )


def _build_frontend(
    registry_name: str,
    release_sha: str,
    backend_url: str,
    frontend_public: Mapping[str, str],
) -> str:
    build_args = {
        "NEXT_PUBLIC_API_URL": backend_url,
        "MURMUR_RELEASE_SHA": release_sha,
        **frontend_public,
    }
    with _git_build_context(release_sha, subtree="web") as context:
        return _acr_build(
            registry_name=registry_name,
            repository="murmur-web",
            release_sha=release_sha,
            dockerfile="Dockerfile",
            context=context,
            build_args=build_args,
        )


def _firebase_access_token(service_account: Mapping[str, object]) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_info(
            dict(service_account), scopes=(IDENTITY_TOOLKIT_SCOPE,)
        )
        credentials.refresh(Request())
        token = credentials.token
    except Exception:
        raise DeploymentRefusal("Firebase Admin API authentication failed") from None
    if not isinstance(token, str) or not token:
        raise DeploymentRefusal("Firebase Admin API returned no access token")
    return token


def _resource_manager_request(
    url: str,
    token: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(512 * 1024)
    except urllib.error.HTTPError as exc:
        raise DeploymentRefusal(f"Google Cloud Resource Manager returned HTTP {exc.code}") from None
    except (OSError, urllib.error.URLError):
        raise DeploymentRefusal("Google Cloud Resource Manager was unavailable") from None
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise DeploymentRefusal("Google Cloud Resource Manager returned invalid JSON") from None
    if not isinstance(result, dict):
        raise DeploymentRefusal("Google Cloud Resource Manager returned invalid data")
    return result


def _verify_firebase_runtime_authority(
    service_account: Mapping[str, object],
    project_id: str,
) -> None:
    token = _firebase_access_token(service_account)
    project = urllib.parse.quote(project_id, safe="")
    result = _resource_manager_request(
        f"{RESOURCE_MANAGER_ROOT}/projects/{project}:testIamPermissions",
        token,
        {"permissions": list(FIREBASE_AUTH_PERMISSION_UNIVERSE)},
    )
    permissions = result.get("permissions")
    if not isinstance(permissions, list) or any(
        not isinstance(permission, str) for permission in permissions
    ):
        raise DeploymentRefusal("Firebase runtime permission attestation is invalid")
    if len(permissions) != len(set(permissions)):
        raise DeploymentRefusal("Firebase runtime permission attestation is invalid")
    if frozenset(permissions) != EXPECTED_FIREBASE_RUNTIME_AUTH_PERMISSIONS:
        raise DeploymentRefusal(
            "Firebase runtime principal does not have the exact required Firebase "
            "Authentication permission subset; non-Authentication authority is not attested"
        )


def _identity_toolkit_request(
    method: str,
    url: str,
    token: str,
    payload: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(256 * 1024)
    except urllib.error.HTTPError as exc:
        raise DeploymentRefusal(
            f"Firebase Identity Toolkit Admin API returned HTTP {exc.code}"
        ) from None
    except (OSError, urllib.error.URLError):
        raise DeploymentRefusal("Firebase Identity Toolkit Admin API was unavailable") from None
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise DeploymentRefusal(
            "Firebase Identity Toolkit Admin API returned invalid JSON"
        ) from None
    if not isinstance(result, dict):
        raise DeploymentRefusal("Firebase Identity Toolkit Admin API returned invalid data")
    return result


def _configure_firebase_domain(
    runtime_service_account: Mapping[str, object],
    project_id: str,
    frontend_url: str,
    *,
    domain_admin_service_account: Mapping[str, object] | None = None,
    mutation_guard: Callable[[], None] | None = None,
) -> FirebaseDomainResult:
    parsed = urllib.parse.urlsplit(_validate_container_app_url(frontend_url, "frontend URL"))
    assert parsed.hostname is not None
    hostname = parsed.hostname
    runtime_token = _firebase_access_token(runtime_service_account)
    project = urllib.parse.quote(project_id, safe="")
    config_url = f"{IDENTITY_TOOLKIT_CONFIG_ROOT}/projects/{project}/config"
    current = _identity_toolkit_request("GET", config_url, runtime_token)
    raw_domains = current.get("authorizedDomains", [])
    if not isinstance(raw_domains, list) or any(not isinstance(item, str) for item in raw_domains):
        raise DeploymentRefusal("Firebase authorized-domain configuration is invalid")
    if hostname in raw_domains:
        return FirebaseDomainResult(status="already_present", hostname=hostname)
    if domain_admin_service_account is None:
        raise DeploymentRefusal(
            "Firebase frontend domain is missing and no deploy-only domain administrator was provided"
        )
    admin_token = _firebase_access_token(domain_admin_service_account)
    domains = [*raw_domains, hostname]
    update_url = f"{config_url}?{urllib.parse.urlencode({'updateMask': 'authorizedDomains'})}"
    if mutation_guard is not None:
        mutation_guard()
    updated = _identity_toolkit_request(
        "PATCH",
        update_url,
        admin_token,
        {"authorizedDomains": domains},
    )
    updated_domains = updated.get("authorizedDomains")
    if not isinstance(updated_domains, list) or hostname not in updated_domains:
        raise DeploymentRefusal("Firebase did not confirm the authorized frontend domain")
    return FirebaseDomainResult(status="configured", hostname=hostname)


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
) -> HttpResult:
    request = urllib.request.Request(url, method=method, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return HttpResult(
                status=response.status,
                headers={key.casefold(): value for key, value in response.headers.items()},
                body=response.read(64 * 1024),
            )
    except urllib.error.HTTPError as exc:
        return HttpResult(
            status=exc.code,
            headers={key.casefold(): value for key, value in exc.headers.items()},
            body=exc.read(64 * 1024),
        )
    except (OSError, urllib.error.URLError):
        raise DeploymentRefusal("HTTPS verification endpoint was unavailable") from None


def _wait_for_json_health(
    url: str,
    *,
    expected_sha: str | None,
    timeout_seconds: float,
) -> Mapping[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            result = _http_request(url)
            payload = json.loads(result.body) if result.body else None
        except (DeploymentRefusal, json.JSONDecodeError):
            result = None
            payload = None
        if result is not None and result.status == 200 and isinstance(payload, dict):
            if expected_sha is None or payload.get("release_sha") == expected_sha:
                return payload
        if time.monotonic() >= deadline:
            raise DeploymentRefusal("HTTPS health verification timed out")
        time.sleep(5)


def _verify_https(
    backend_url: str,
    frontend_url: str,
    *,
    expected_sha: str | None,
    timeout_seconds: float = 300,
) -> None:
    backend = _validate_container_app_url(backend_url, "backend URL")
    frontend = _validate_container_app_url(frontend_url, "frontend URL")
    backend_health = _wait_for_json_health(
        f"{backend}/healthz", expected_sha=expected_sha, timeout_seconds=timeout_seconds
    )
    if backend_health.get("status") != "ok":
        raise DeploymentRefusal("backend liveness did not report ok")
    readiness = _wait_for_json_health(
        f"{backend}/readyz", expected_sha=expected_sha, timeout_seconds=timeout_seconds
    )
    if readiness.get("status") != "ready":
        raise DeploymentRefusal("backend readiness did not report ready")
    checks = readiness.get("checks")
    expected_checks = {"database", "firebase", "azure_openai"}
    if (
        not isinstance(checks, dict)
        or set(checks) != expected_checks
        or any(value != "ready" for value in checks.values())
    ):
        raise DeploymentRefusal("backend readiness dependency checks did not pass")
    frontend_health = _wait_for_json_health(
        f"{frontend}/healthz", expected_sha=expected_sha, timeout_seconds=timeout_seconds
    )
    if frontend_health.get("status") != "ok":
        raise DeploymentRefusal("frontend liveness did not report ok")

    allowed = _http_request(
        f"{backend}/healthz",
        method="OPTIONS",
        headers={
            "Origin": frontend,
            "Access-Control-Request-Method": "GET",
        },
    )
    if (
        allowed.status not in {200, 204}
        or allowed.headers.get("access-control-allow-origin") != frontend
    ):
        raise DeploymentRefusal("backend CORS did not allow the exact frontend origin")

    untrusted = _http_request(
        f"{backend}/healthz",
        method="OPTIONS",
        headers={
            "Origin": "https://untrusted.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )
    if "access-control-allow-origin" in untrusted.headers:
        raise DeploymentRefusal("backend CORS allowed an unrelated origin")


def _single_container(app: Mapping[str, object]) -> Mapping[str, object]:
    properties = app.get("properties")
    if not isinstance(properties, dict):
        raise DeploymentRefusal("Container App properties are missing")
    template = properties.get("template")
    if not isinstance(template, dict):
        raise DeploymentRefusal("Container App template is missing")
    containers = template.get("containers")
    if (
        not isinstance(containers, list)
        or len(containers) != 1
        or not isinstance(containers[0], dict)
    ):
        raise DeploymentRefusal("Container App must have exactly one container")
    return containers[0]


def _env_map(container: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw_env = container.get("env", [])
    if not isinstance(raw_env, list):
        raise DeploymentRefusal("Container App environment is invalid")
    result: dict[str, Mapping[str, object]] = {}
    for item in raw_env:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise DeploymentRefusal("Container App environment is invalid")
        result[item["name"]] = item
    return result


def _probe_types(
    container: Mapping[str, object],
    expected_paths: Mapping[str, str],
    expected_port: int,
) -> tuple[str, ...]:
    probes = container.get("probes")
    if not isinstance(probes, list):
        raise DeploymentRefusal("Container App probes are missing")
    seen: dict[str, str] = {}
    for probe in probes:
        if not isinstance(probe, dict) or not isinstance(probe.get("type"), str):
            raise DeploymentRefusal("Container App probe is invalid")
        http_get = probe.get("httpGet")
        if (
            not isinstance(http_get, dict)
            or not isinstance(http_get.get("path"), str)
            or http_get.get("port") != expected_port
        ):
            raise DeploymentRefusal("Container App HTTP probe is invalid")
        seen[probe["type"]] = http_get["path"]
    if seen != dict(expected_paths):
        raise DeploymentRefusal("Container App probes do not match the deployment contract")
    return tuple(sorted(seen))


def _image_metadata(image: str, expected_repository: str) -> tuple[str, str]:
    try:
        registry_and_repository, digest = image.rsplit("@", 1)
        registry_server, repository = registry_and_repository.split("/", 1)
    except ValueError:
        raise DeploymentRefusal("Container App image is not pinned by digest") from None
    if (
        not _REGISTRY_NAME.fullmatch(registry_server.removesuffix(".azurecr.io"))
        or not registry_server.endswith(".azurecr.io")
        or repository != expected_repository
        or not _IMAGE_DIGEST.fullmatch(digest)
    ):
        raise DeploymentRefusal("Container App image does not match the ACR repository contract")
    return registry_server, digest


def _inspect_app(
    resource_group: str,
    name: str,
    *,
    backend: bool,
) -> AppInspection:
    app = _run_json(
        [
            "az",
            "containerapp",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect Container App {name}",
    )
    if not isinstance(app, dict):
        raise DeploymentRefusal(f"Container App {name} returned invalid data")
    if app.get("name") != name:
        raise DeploymentRefusal(f"Container App {name} returned mismatched resource data")
    identity = app.get("identity")
    if not isinstance(identity, dict) or identity.get("type") != "UserAssigned":
        raise DeploymentRefusal(f"Container App {name} has no user-assigned identity")
    raw_identities = identity.get("userAssignedIdentities")
    if not isinstance(raw_identities, dict) or len(raw_identities) != 1:
        raise DeploymentRefusal(f"Container App {name} identity assignment is invalid")
    identity_id = next(iter(raw_identities))
    if not isinstance(identity_id, str) or not identity_id.casefold().startswith("/subscriptions/"):
        raise DeploymentRefusal(f"Container App {name} identity resource ID is invalid")
    properties = app.get("properties")
    if not isinstance(properties, dict):
        raise DeploymentRefusal(f"Container App {name} has no properties")
    configuration = properties.get("configuration")
    template = properties.get("template")
    if not isinstance(configuration, dict) or not isinstance(template, dict):
        raise DeploymentRefusal(f"Container App {name} configuration is incomplete")
    ingress = configuration.get("ingress")
    if not isinstance(ingress, dict) or not isinstance(ingress.get("fqdn"), str):
        raise DeploymentRefusal(f"Container App {name} has no HTTPS ingress")
    expected_port = 8000 if backend else 3000
    if (
        configuration.get("activeRevisionsMode") != "Single"
        or ingress.get("external") is not True
        or ingress.get("allowInsecure") is not False
        or ingress.get("targetPort") != expected_port
    ):
        raise DeploymentRefusal(f"Container App {name} ingress is outside the pilot contract")
    if properties.get("provisioningState") != "Succeeded":
        raise DeploymentRefusal(f"Container App {name} provisioning has not succeeded")
    url = _validate_container_app_url(f"https://{ingress['fqdn']}", f"{name} URL")
    scale = template.get("scale")
    if not isinstance(scale, dict):
        raise DeploymentRefusal(f"Container App {name} scale settings are missing")
    min_replicas = scale.get("minReplicas")
    max_replicas = scale.get("maxReplicas")
    if min_replicas != 0 or max_replicas != 1:
        raise DeploymentRefusal(f"Container App {name} is outside the pilot scale bounds")

    container = _single_container(app)
    image = container.get("image")
    if not isinstance(image, str):
        raise DeploymentRefusal(f"Container App {name} image is missing")
    expected_repository = "murmur-api" if backend else "murmur-web"
    registry_server, image_digest = _image_metadata(image, expected_repository)
    registries = configuration.get("registries")
    if not isinstance(registries, list) or len(registries) != 1:
        raise DeploymentRefusal(f"Container App {name} registry configuration is invalid")
    registry = registries[0]
    if (
        not isinstance(registry, dict)
        or registry.get("server") != registry_server
        or not _same_azure_resource_id(registry.get("identity"), identity_id)
        or registry.get("username") not in {None, ""}
        or registry.get("passwordSecretRef") not in {None, ""}
    ):
        raise DeploymentRefusal(f"Container App {name} does not use identity-based ACR pull")
    env = _env_map(container)
    release_env = env.get("MURMUR_RELEASE_SHA")
    release_sha = release_env.get("value") if isinstance(release_env, dict) else None
    if not isinstance(release_sha, str) or not _FULL_SHA.fullmatch(release_sha):
        raise DeploymentRefusal(f"Container App {name} release metadata is not a full git SHA")

    key_vault_name: str | None = None
    key_vault_secret_versions: tuple[tuple[str, str], ...] = ()
    if backend:
        expected_paths = {"Startup": "/healthz", "Liveness": "/healthz", "Readiness": "/readyz"}
        for env_name, secret_ref in (
            ("AZURE_OPENAI_API_KEY", AZURE_KEY_SECRET_NAME),
            ("FIREBASE_SERVICE_ACCOUNT_JSON", FIREBASE_SECRET_NAME),
        ):
            item = env.get(env_name)
            if not isinstance(item, dict) or item.get("secretRef") != secret_ref or "value" in item:
                raise DeploymentRefusal(
                    f"Container App {name} does not use secretRef for {env_name}"
                )
        secrets = configuration.get("secrets")
        if not isinstance(secrets, list):
            raise DeploymentRefusal(f"Container App {name} Key Vault references are missing")
        by_name = {
            item.get("name"): item
            for item in secrets
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        expected_secret_names = {AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME}
        if set(by_name) != expected_secret_names:
            raise DeploymentRefusal(
                f"Container App {name} Key Vault references are outside the deployment contract"
            )
        vault_names: set[str] = set()
        observed_versions: list[tuple[str, str]] = []
        for secret_name in (AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME):
            item = by_name.get(secret_name)
            if not isinstance(item, dict) or "value" in item:
                raise DeploymentRefusal(f"Container App {name} has an inline credential")
            key_vault_url = item.get("keyVaultUrl")
            identity = item.get("identity")
            if (
                not isinstance(key_vault_url, str)
                or not isinstance(identity, str)
                or not _same_azure_resource_id(identity, identity_id)
            ):
                raise DeploymentRefusal(f"Container App {name} Key Vault reference is incomplete")
            parsed = urllib.parse.urlsplit(key_vault_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.hostname.endswith(".vault.azure.net")
                or len(parsed.path.strip("/").split("/")) != 3
                or parsed.query
                or parsed.fragment
            ):
                raise DeploymentRefusal(
                    f"Container App {name} Key Vault reference is not version-pinned"
                )
            path_parts = parsed.path.strip("/").split("/")
            if path_parts[:2] != [
                "secrets",
                secret_name,
            ] or not _KEY_VAULT_SECRET_VERSION.fullmatch(path_parts[2]):
                raise DeploymentRefusal(
                    f"Container App {name} Key Vault reference is not version-pinned"
                )
            observed_versions.append((secret_name, path_parts[2].casefold()))
            vault_names.add(parsed.hostname.removesuffix(".vault.azure.net"))
        if len(vault_names) != 1:
            raise DeploymentRefusal(f"Container App {name} uses inconsistent Key Vaults")
        key_vault_name = vault_names.pop()
        key_vault_secret_versions = tuple(observed_versions)
        if env.get("MURMUR_DATA_DIR", {}).get("value") != "/home/murmur/data":
            raise DeploymentRefusal(
                f"Container App {name} is not using isolated local pilot storage"
            )
        if env.get("MURMUR_SQLITE_JOURNAL_MODE", {}).get("value") != "WAL":
            raise DeploymentRefusal(f"Container App {name} local SQLite is not using WAL")
        if container.get("volumeMounts"):
            raise DeploymentRefusal(f"Container App {name} unexpectedly mounts shared storage")
    else:
        expected_paths = {"Startup": "/healthz", "Liveness": "/healthz", "Readiness": "/healthz"}
        frontend_secrets = configuration.get("secrets")
        if frontend_secrets is not None and frontend_secrets != []:
            raise DeploymentRefusal(f"Container App {name} unexpectedly has secret configuration")
        if any("secretRef" in item for item in env.values()):
            raise DeploymentRefusal(f"Container App {name} unexpectedly consumes a secret")

    probe_types = _probe_types(container, expected_paths, expected_port)
    latest_revision = properties.get("latestRevisionName")
    if not isinstance(latest_revision, str) or not latest_revision:
        raise DeploymentRefusal(f"Container App {name} has no active revision")
    latest_ready_revision = properties.get("latestReadyRevisionName")
    if not isinstance(latest_ready_revision, str) or not latest_ready_revision:
        latest_ready_revision = None
    return AppInspection(
        name=name,
        url=url,
        image=image,
        image_digest=image_digest,
        registry_server=registry_server,
        release_sha=release_sha,
        latest_revision=latest_revision,
        latest_ready_revision=latest_ready_revision,
        provisioning_state=str(properties.get("provisioningState", "unknown")),
        running_status=str(properties.get("runningStatus", "unknown")),
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        probe_types=probe_types,
        key_vault_name=key_vault_name,
        key_vault_secret_versions=key_vault_secret_versions,
        identity_id=identity_id,
    )


def _inspect_managed_identity(identity_id: str) -> str:
    identity = _run_json(
        [
            "az",
            "identity",
            "show",
            "--ids",
            identity_id,
            "--query",
            "{id:id,principalId:principalId}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect frontend managed identity",
    )
    if not isinstance(identity, dict) or not _same_azure_resource_id(
        identity.get("id"), identity_id
    ):
        raise DeploymentRefusal("frontend managed identity metadata is invalid")
    principal_id = identity.get("principalId")
    if not isinstance(principal_id, str) or not _AZURE_GUID.fullmatch(principal_id):
        raise DeploymentRefusal("frontend managed identity principal is invalid")
    return principal_id


def _verify_key_vault_metadata(vault_name: str) -> str:
    vault = _run_json(
        [
            "az",
            "keyvault",
            "show",
            "--name",
            vault_name,
            "--query",
            "{id:id,name:name,rbac:properties.enableRbacAuthorization}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect Key Vault authorization mode",
    )
    if not isinstance(vault, dict) or vault.get("name") != vault_name:
        raise DeploymentRefusal("backend Key Vault metadata is invalid")
    vault_id = vault.get("id")
    if not isinstance(vault_id, str) or not vault_id.casefold().startswith("/subscriptions/"):
        raise DeploymentRefusal("backend Key Vault resource ID is invalid")
    if vault.get("rbac") is not True:
        raise DeploymentRefusal("Key Vault is not using Azure RBAC authorization")
    metadata = _run_json(
        [
            "az",
            "keyvault",
            "secret",
            "list",
            "--vault-name",
            vault_name,
            "--query",
            "[].{name:name,enabled:attributes.enabled}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect Key Vault secret metadata",
    )
    if not isinstance(metadata, list):
        raise DeploymentRefusal("Key Vault secret metadata is invalid")
    enabled = {
        item.get("name")
        for item in metadata
        if isinstance(item, dict) and item.get("enabled") is not False
    }
    required = {AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME}
    if not required.issubset(enabled):
        raise DeploymentRefusal("Key Vault is missing required enabled secrets")
    return vault_id


def _permission_grants(permission: Mapping[str, object], action: str, *, data_plane: bool) -> bool:
    grant_key = "dataActions" if data_plane else "actions"
    deny_key = "notDataActions" if data_plane else "notActions"
    grants = permission.get(grant_key, [])
    denies = permission.get(deny_key, [])
    if not isinstance(grants, list) or not isinstance(denies, list):
        raise DeploymentRefusal("Azure role definition permissions are invalid")
    if any(not isinstance(item, str) for item in [*grants, *denies]):
        raise DeploymentRefusal("Azure role definition permissions are invalid")
    normalized_action = action.casefold()
    granted = any(fnmatch.fnmatchcase(normalized_action, pattern.casefold()) for pattern in grants)
    denied = any(fnmatch.fnmatchcase(normalized_action, pattern.casefold()) for pattern in denies)
    return granted and not denied


def _key_vault_assignments_at_secret(
    *, principal_id: str, key_vault_id: str, secret_name: str
) -> tuple[tuple[str, str], ...]:
    secret_scope = f"{key_vault_id.rstrip('/')}/secrets/{secret_name}"
    result = _run_json(
        [
            "az",
            "role",
            "assignment",
            "list",
            "--assignee-object-id",
            principal_id,
            "--scope",
            secret_scope,
            "--include-inherited",
            "--include-groups",
            "--fill-principal-name",
            "false",
            "--fill-role-definition-name",
            "false",
            "--query",
            "[].{scope:scope,roleDefinitionId:roleDefinitionId}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect managed-identity access to Key Vault secret {secret_name}",
    )
    if not isinstance(result, list):
        raise DeploymentRefusal("managed-identity Key Vault role assignments are invalid")
    assignments: list[tuple[str, str]] = []
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("scope"), str):
            raise DeploymentRefusal("managed-identity Key Vault role assignments are invalid")
        assignments.append((item["scope"], _role_definition_guid(item.get("roleDefinitionId"))))
    return tuple(assignments)


def _role_permissions(role_id: str) -> tuple[Mapping[str, object], ...]:
    permissions = _run_json(
        [
            "az",
            "role",
            "definition",
            "list",
            "--name",
            role_id,
            "--query",
            "[0].permissions",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect managed-identity Key Vault role definition",
    )
    if not isinstance(permissions, list) or any(not isinstance(item, dict) for item in permissions):
        raise DeploymentRefusal("managed-identity Key Vault role definition is invalid")
    return tuple(permissions)


def _security_relevant_key_vault_assignments(
    assignments: Sequence[tuple[str, str]],
    *,
    permission_cache: dict[str, tuple[Mapping[str, object], ...]],
) -> tuple[tuple[str, str], ...]:
    secret_read = "Microsoft.KeyVault/vaults/secrets/getSecret/action"
    role_assignment_write = "Microsoft.Authorization/roleAssignments/write"
    relevant: list[tuple[str, str]] = []
    for scope, role_id in assignments:
        permissions = permission_cache.get(role_id)
        if permissions is None:
            permissions = _role_permissions(role_id)
            permission_cache[role_id] = permissions
        if any(
            _permission_grants(item, secret_read, data_plane=True)
            or _permission_grants(item, role_assignment_write, data_plane=False)
            for item in permissions
        ):
            relevant.append((scope, role_id))
    return tuple(relevant)


def _verify_frontend_key_vault_boundary(*, frontend_principal_id: str, key_vault_id: str) -> None:
    permission_cache: dict[str, tuple[Mapping[str, object], ...]] = {}
    for secret_name in (
        AZURE_KEY_SECRET_NAME,
        FIREBASE_SECRET_NAME,
        LEGACY_FIREBASE_SECRET_NAME,
    ):
        assignments = _key_vault_assignments_at_secret(
            principal_id=frontend_principal_id,
            key_vault_id=key_vault_id,
            secret_name=secret_name,
        )
        if _security_relevant_key_vault_assignments(assignments, permission_cache=permission_cache):
            raise DeploymentRefusal("frontend identity can access or grant Key Vault secrets")


def _verify_backend_key_vault_boundary(*, backend_principal_id: str, key_vault_id: str) -> None:
    permission_cache: dict[str, tuple[Mapping[str, object], ...]] = {}
    for secret_name in (AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME):
        secret_scope = f"{key_vault_id.rstrip('/')}/secrets/{secret_name}"
        assignments = _key_vault_assignments_at_secret(
            principal_id=backend_principal_id,
            key_vault_id=key_vault_id,
            secret_name=secret_name,
        )
        relevant = _security_relevant_key_vault_assignments(
            assignments, permission_cache=permission_cache
        )
        if (
            len(relevant) != 1
            or not _same_azure_resource_id(relevant[0][0], secret_scope)
            or relevant[0][1] != KEY_VAULT_SECRETS_USER_ROLE_ID
        ):
            raise DeploymentRefusal(
                "backend Key Vault access is not limited to the intended secret scope"
            )

    legacy_assignments = _key_vault_assignments_at_secret(
        principal_id=backend_principal_id,
        key_vault_id=key_vault_id,
        secret_name=LEGACY_FIREBASE_SECRET_NAME,
    )
    if _security_relevant_key_vault_assignments(
        legacy_assignments, permission_cache=permission_cache
    ):
        raise DeploymentRefusal("backend identity can access the legacy Firebase secret")


def _retire_legacy_backend_vault_read(
    *,
    backend_principal_id: str,
    key_vault_id: str,
    mutation_guard: Callable[[], None],
    attempts: int = 8,
) -> None:
    def legacy_assignments() -> tuple[str, ...]:
        raw = _run_json(
            [
                "az",
                "role",
                "assignment",
                "list",
                "--assignee-object-id",
                backend_principal_id,
                "--scope",
                key_vault_id,
                "--fill-principal-name",
                "false",
                "--query",
                "[].{id:id,scope:scope,roleDefinitionId:roleDefinitionId,description:description}",
                "--output",
                "json",
                "--only-show-errors",
            ],
            timeout_seconds=60,
            operation="inspect legacy backend Key Vault role assignment",
        )
        if not isinstance(raw, list):
            raise DeploymentRefusal("legacy backend Key Vault role metadata is invalid")
        ids: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                raise DeploymentRefusal("legacy backend Key Vault role metadata is invalid")
            if (
                _same_azure_resource_id(item.get("scope"), key_vault_id)
                and _role_definition_guid(item.get("roleDefinitionId"))
                == KEY_VAULT_SECRETS_USER_ROLE_ID
            ):
                assignment_id = item.get("id")
                if not isinstance(assignment_id, str) or item.get("description") not in {None, ""}:
                    raise DeploymentRefusal("legacy backend Key Vault role metadata is ambiguous")
                ids.append(assignment_id)
        return tuple(ids)

    current = legacy_assignments()
    if not current:
        return
    if len(current) != 1:
        raise DeploymentRefusal("legacy backend Key Vault role metadata is ambiguous")
    assignment_id = current[0]
    for attempt in range(attempts):
        mutation_guard()
        try:
            _run_command(
                [
                    "az",
                    "role",
                    "assignment",
                    "delete",
                    "--ids",
                    assignment_id,
                    "--only-show-errors",
                    "--output",
                    "none",
                ],
                timeout_seconds=60,
                operation="remove legacy vault-wide backend secret access",
            )
        except DeploymentRefusal:
            pass
        current = legacy_assignments()
        if not current:
            return
        if current != (assignment_id,):
            raise DeploymentRefusal("legacy backend Key Vault role metadata changed during removal")
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 8))
    raise DeploymentRefusal("legacy vault-wide backend secret access remains assigned")


def _verify_old_revisions_inactive(
    *,
    resource_group: str,
    app_name: str,
    current_revision: str,
    attempts: int = 30,
) -> None:
    if attempts < 1:
        raise DeploymentRefusal("Container App revision checks require positive attempts")
    expected = current_revision.casefold()
    for attempt in range(attempts):
        revisions = _run_json(
            [
                "az",
                "containerapp",
                "revision",
                "list",
                "--resource-group",
                resource_group,
                "--name",
                app_name,
                "--all",
                "--query",
                (
                    "[].{name:name,active:properties.active,replicas:properties.replicas,"
                    "healthState:properties.healthState,"
                    "provisioningState:properties.provisioningState,"
                    "runningState:properties.runningState}"
                ),
                "--output",
                "json",
                "--only-show-errors",
            ],
            timeout_seconds=60,
            operation=f"inspect Container App revisions for {app_name}",
        )
        if not isinstance(revisions, list) or not revisions:
            raise DeploymentRefusal("Azure returned invalid Container App revision metadata")
        states: dict[str, Mapping[str, object]] = {}
        for item in revisions:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not isinstance(item.get("active"), bool)
                or not isinstance(item.get("replicas"), int)
                or isinstance(item.get("replicas"), bool)
                or item["replicas"] < 0
            ):
                raise DeploymentRefusal("Azure returned invalid Container App revision metadata")
            normalized = item["name"].casefold()
            if normalized in states:
                raise DeploymentRefusal("Azure returned duplicate Container App revisions")
            states[normalized] = item
        current = states.get(expected)
        if current is None:
            raise DeploymentRefusal("verified backend revision is missing")
        current_ready = (
            current["active"] is True
            and current["replicas"] >= 1
            and current.get("healthState") == "Healthy"
            and current.get("provisioningState") == "Provisioned"
            and current.get("runningState") == "Running"
        )
        old_ready = all(
            item["active"] is False
            and item["replicas"] == 0
            # Fully inactive ACA revisions may omit these fields. Azure also
            # reports Provisioned + Stopped after deactivation; replicas==0 and
            # runningState==Stopped are the terminal workload proof in that case.
            and item.get("provisioningState") in {None, "Deprovisioned", "Provisioned"}
            and item.get("runningState") in {None, "Stopped"}
            for name, item in states.items()
            if name != expected
        )
        if current_ready and old_ready:
            return
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 10))
    raise DeploymentRefusal("Container App revisions did not reach a safe terminal cutover state")


def verify_live(
    *,
    resource_group: str,
    backend_app: str,
    frontend_app: str,
    expected_backend_identity_id: str,
    expected_backend_identity_principal_id: str,
    expected_frontend_identity_id: str,
    expected_frontend_identity_principal_id: str,
    key_vault_id: str,
    subscription_id: str,
    tenant_id: str,
    expected_secret_versions: Mapping[str, str],
    expected_sha: str | None = None,
    health_timeout_seconds: float = 300,
) -> tuple[AppInspection, AppInspection]:
    if set(expected_secret_versions) != {AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME} or any(
        not isinstance(version, str) or not _KEY_VAULT_SECRET_VERSION.fullmatch(version)
        for version in expected_secret_versions.values()
    ):
        raise DeploymentRefusal("expected Key Vault secret versions are invalid")
    _validate_azure_session(subscription_id=subscription_id, tenant_id=tenant_id)
    _run_command(
        [
            "az",
            "group",
            "show",
            "--name",
            resource_group,
            "--query",
            "name",
            "--output",
            "none",
            "--only-show-errors",
        ],
        timeout_seconds=30,
        operation="inspect Murmur resource group",
    )
    backend = _inspect_app(resource_group, backend_app, backend=True)
    frontend = _inspect_app(resource_group, frontend_app, backend=False)
    if _same_azure_resource_id(backend.identity_id, frontend.identity_id):
        raise DeploymentRefusal("frontend and backend must use separate managed identities")
    if not _same_azure_resource_id(backend.identity_id, expected_backend_identity_id):
        raise DeploymentRefusal("backend does not use the foundation managed identity")
    if dict(backend.key_vault_secret_versions) != {
        name: version.casefold() for name, version in expected_secret_versions.items()
    }:
        raise DeploymentRefusal("backend does not reference the expected secret versions")
    if not _same_azure_resource_id(frontend.identity_id, expected_frontend_identity_id):
        raise DeploymentRefusal("frontend does not use the foundation managed identity")
    if not isinstance(frontend.identity_id, str):
        raise DeploymentRefusal("frontend managed identity resource ID is unavailable")
    frontend_principal_id = _inspect_managed_identity(frontend.identity_id)
    if frontend_principal_id.casefold() != expected_frontend_identity_principal_id.casefold():
        raise DeploymentRefusal(
            "frontend identity principal does not match the foundation deployment"
        )
    if backend.release_sha != frontend.release_sha:
        raise DeploymentRefusal("frontend and backend image revisions do not match")
    if expected_sha is not None and backend.release_sha != expected_sha:
        raise DeploymentRefusal("live applications do not match the expected release SHA")
    if not backend.key_vault_name:
        raise DeploymentRefusal("backend has no verified Key Vault reference")
    live_key_vault_id = _verify_key_vault_metadata(backend.key_vault_name)
    if not _same_azure_resource_id(live_key_vault_id, key_vault_id):
        raise DeploymentRefusal("backend Key Vault does not match the foundation deployment")
    _verify_frontend_key_vault_boundary(
        frontend_principal_id=frontend_principal_id,
        key_vault_id=live_key_vault_id,
    )
    _verify_backend_key_vault_boundary(
        backend_principal_id=expected_backend_identity_principal_id,
        key_vault_id=live_key_vault_id,
    )
    _verify_https(
        backend.url,
        frontend.url,
        expected_sha=backend.release_sha,
        timeout_seconds=health_timeout_seconds,
    )
    backend = _inspect_app(resource_group, backend_app, backend=True)
    frontend = _inspect_app(resource_group, frontend_app, backend=False)
    if _same_azure_resource_id(backend.identity_id, frontend.identity_id):
        raise DeploymentRefusal("frontend and backend must use separate managed identities")
    if not _same_azure_resource_id(backend.identity_id, expected_backend_identity_id):
        raise DeploymentRefusal("backend does not use the foundation managed identity")
    if dict(backend.key_vault_secret_versions) != {
        name: version.casefold() for name, version in expected_secret_versions.items()
    }:
        raise DeploymentRefusal("backend secret versions changed during health verification")
    if not _same_azure_resource_id(frontend.identity_id, expected_frontend_identity_id):
        raise DeploymentRefusal("frontend does not use the foundation managed identity")
    for app in (backend, frontend):
        if app.latest_ready_revision != app.latest_revision:
            raise DeploymentRefusal(
                f"Container App {app.name} latest revision is not reported ready"
            )
    if backend.release_sha != frontend.release_sha:
        raise DeploymentRefusal("frontend and backend image revisions do not match")
    if expected_sha is not None and backend.release_sha != expected_sha:
        raise DeploymentRefusal("live applications do not match the expected release SHA")
    return backend, frontend


def _print_verification(backend: AppInspection, frontend: AppInspection) -> None:
    print("Murmur Azure verification: passed")
    print(f"release_sha: {backend.release_sha}")
    print(f"backend_url: {backend.url}")
    print(f"backend_revision: {backend.latest_revision}")
    print(f"backend_scale: {backend.min_replicas}..{backend.max_replicas}")
    print(f"backend_probes: {','.join(backend.probe_types)}")
    print(f"frontend_url: {frontend.url}")
    print(f"frontend_revision: {frontend.latest_revision}")
    print(f"frontend_scale: {frontend.min_replicas}..{frontend.max_replicas}")
    print(f"frontend_probes: {','.join(frontend.probe_types)}")
    print("key_vault_references: version-pinned and enabled")
    print("database_configuration: local ephemeral SQLite; PostgreSQL required for durability")
    print("persistence_restart_proof: not_applicable_ephemeral_pilot")
    print("paid_model_calls: 0")


def _verify_rotation_postcondition(
    *, vault_name: str, secret_name: str, current_version: str, attempts: int = 8
) -> None:
    snapshot = _stable_key_vault_rotation_snapshot(
        vault_name=vault_name,
        secret_name=secret_name,
        current_version=current_version,
        attempts=attempts,
    )
    if any(
        item.enabled and item.version.casefold() != current_version.casefold() for item in snapshot
    ):
        raise DeploymentRefusal("retired Key Vault secret versions remain enabled")


def _verify_legacy_secret_postcondition(*, vault_name: str) -> str:
    if not _legacy_key_vault_secret_exists(vault_name=vault_name):
        return "not_present"
    before = _list_key_vault_secret_versions(
        vault_name=vault_name, secret_name=LEGACY_FIREBASE_SECRET_NAME
    )
    after = _list_key_vault_secret_versions(
        vault_name=vault_name, secret_name=LEGACY_FIREBASE_SECRET_NAME
    )
    if before != after or any(item.enabled for item in after):
        raise DeploymentRefusal("legacy Firebase secret is not stably disabled")
    return "disabled_recoverable"


def _assert_no_key_vault_writer(key_vault_id: str) -> None:
    if _direct_key_vault_writer_assignments(key_vault_id):
        raise DeploymentRefusal("temporary Key Vault writer assignment remains present")


def deploy(args: argparse.Namespace) -> int:
    resource_group = _require_safe_resource_name(args.resource_group, "resource group")
    backend_app = _require_container_app_name(args.backend_app, "backend app")
    frontend_app = _require_container_app_name(args.frontend_app, "frontend app")
    location = _require_location(args.location)
    if location != DEFAULT_LOCATION:
        raise DeploymentRefusal("the isolated pilot must be deployed in Central India")
    revision = validate_source_revision()
    inputs = load_deployment_inputs(args.backend_env, args.frontend_env)
    _validate_azure_session(
        subscription_id=args.subscription_id,
        tenant_id=args.tenant_id,
    )
    _verify_firebase_runtime_authority(
        inputs.firebase_runtime_service_account,
        inputs.firebase_project_id,
    )
    deployment_principal = _current_principal()

    print(f"Deploying immutable release {revision.sha}")
    print("Registering required Azure providers")
    _register_providers()
    _create_resource_group(resource_group, location, revision.sha)

    print("Deploying isolated Azure foundation")
    foundation = _deployment_outputs(
        resource_group=resource_group,
        deployment_name=FOUNDATION_DEPLOYMENT,
        template=FOUNDATION_TEMPLATE,
        parameters={
            "location": location,
            "backendAppName": backend_app,
            "frontendAppName": frontend_app,
            "deploymentPrincipalObjectId": deployment_principal[0],
            "deploymentPrincipalType": deployment_principal[1],
            "grantBackendSecretRead": False,
        },
    )
    registry_name = _output_value(foundation, "registryName")
    registry_login_server = _output_value(foundation, "registryLoginServer")
    if not _REGISTRY_NAME.fullmatch(registry_name):
        raise DeploymentRefusal("foundation returned an invalid registry name")
    if registry_login_server != f"{registry_name}.azurecr.io":
        raise DeploymentRefusal("foundation returned an inconsistent registry login server")
    key_vault_name = _output_value(foundation, "keyVaultName")
    key_vault_id = _output_value(foundation, "keyVaultId")
    backend_identity_id = _output_value(foundation, "identityId")
    backend_identity_principal_id = _output_value(foundation, "identityPrincipalId")
    frontend_identity_id = _output_value(foundation, "frontendIdentityId")
    frontend_identity_principal_id = _output_value(foundation, "frontendIdentityPrincipalId")
    default_domain = _output_value(foundation, "environmentDefaultDomain")
    backend_url = _validate_container_app_url(
        f"https://{backend_app}.{default_domain}", "derived backend URL"
    )
    expected_foundation_url = _validate_container_app_url(
        _output_value(foundation, "backendUrl"), "foundation backend URL"
    )
    if backend_url != expected_foundation_url:
        raise DeploymentRefusal("derived backend URL does not match foundation output")
    frontend_url_from_domain = _validate_container_app_url(
        f"https://{frontend_app}.{default_domain}", "derived frontend URL"
    )
    expected_foundation_frontend_url = _validate_container_app_url(
        _output_value(foundation, "frontendUrl"), "foundation frontend URL"
    )
    if frontend_url_from_domain != expected_foundation_frontend_url:
        raise DeploymentRefusal("derived frontend URL does not match foundation output")
    lock_account = _output_value(foundation, "deploymentLockStorageAccountName")
    lock_container = _output_value(foundation, "deploymentLockContainerName")
    lock_blob = _output_value(foundation, "deploymentLockBlobName")

    with (
        _deployment_blob_lease(
            account_name=lock_account,
            container_name=lock_container,
            blob_name=lock_blob,
            subscription_id=args.subscription_id,
        ) as lease,
        _temporary_key_vault_write(
            key_vault_id,
            principal=deployment_principal,
            mutation_guard=lease.assert_healthy,
        ),
    ):
        print("Staging server credentials in Azure Key Vault")
        azure_key_version = _write_key_vault_secret(
            vault_name=key_vault_name,
            secret_name=AZURE_KEY_SECRET_NAME,
            payload=inputs.azure_openai_key.encode("utf-8"),
            mutation_guard=lease.assert_healthy,
        )
        firebase_version = _write_key_vault_secret(
            vault_name=key_vault_name,
            secret_name=FIREBASE_SECRET_NAME,
            payload=inputs.firebase_runtime_json_bytes(),
            mutation_guard=lease.assert_healthy,
        )
        lease.assert_healthy()
        secured_foundation = _deployment_outputs(
            resource_group=resource_group,
            deployment_name=FOUNDATION_DEPLOYMENT,
            template=FOUNDATION_TEMPLATE,
            parameters={
                "location": location,
                "backendAppName": backend_app,
                "frontendAppName": frontend_app,
                "deploymentPrincipalObjectId": deployment_principal[0],
                "deploymentPrincipalType": deployment_principal[1],
                "grantBackendSecretRead": True,
            },
        )
        for output_name, expected_value in (
            ("keyVaultId", key_vault_id),
            ("identityId", backend_identity_id),
            ("identityPrincipalId", backend_identity_principal_id),
            ("deploymentLockStorageAccountName", lock_account),
            ("deploymentLockContainerName", lock_container),
            ("deploymentLockBlobName", lock_blob),
        ):
            if (
                _output_value(secured_foundation, output_name).casefold()
                != expected_value.casefold()
            ):
                raise DeploymentRefusal("foundation outputs changed during secured deployment")
        _retire_legacy_backend_vault_read(
            backend_principal_id=backend_identity_principal_id,
            key_vault_id=key_vault_id,
            mutation_guard=lease.assert_healthy,
        )
        _verify_backend_key_vault_boundary(
            backend_principal_id=backend_identity_principal_id,
            key_vault_id=key_vault_id,
        )

        lease.assert_healthy()
        print("Building backend image from the accepted git archive")
        backend_digest = _build_backend(registry_name, revision.sha)
        lease.assert_healthy()
        print("Building frontend image against the derived backend origin")
        frontend_digest = _build_frontend(
            registry_name, revision.sha, backend_url, inputs.frontend_public
        )
        lease.assert_healthy()
        backend_image = f"{registry_login_server}/murmur-api@{backend_digest}"
        frontend_image = f"{registry_login_server}/murmur-web@{frontend_digest}"
        print("Deploying immutable Container Apps revisions")
        app_outputs = _deployment_outputs(
            resource_group=resource_group,
            deployment_name=APPS_DEPLOYMENT,
            template=APPS_TEMPLATE,
            parameters={
                "location": location,
                "backendAppName": backend_app,
                "frontendAppName": frontend_app,
                "backendImage": backend_image,
                "frontendImage": frontend_image,
                "releaseSha": revision.sha,
                "azureOpenAiEndpoint": inputs.azure_openai_endpoint,
                "azureOpenAiDeployment": inputs.azure_openai_deployment,
                "firebaseProjectId": inputs.firebase_project_id,
                "azureOpenAiSecretVersion": azure_key_version,
                "firebaseRuntimeSecretVersion": firebase_version,
            },
        )
        live_backend_url = _validate_container_app_url(
            _output_value(app_outputs, "backendUrl"), "deployed backend URL"
        )
        frontend_url = _validate_container_app_url(
            _output_value(app_outputs, "frontendUrl"), "deployed frontend URL"
        )
        if _output_value(app_outputs, "azureOpenAiSecretVersion") != azure_key_version or (
            _output_value(app_outputs, "firebaseRuntimeSecretVersion") != firebase_version
        ):
            raise DeploymentRefusal("apps deployment changed the staged secret versions")
        if live_backend_url != backend_url:
            raise DeploymentRefusal("deployed backend URL changed after the frontend build")
        if frontend_url != frontend_url_from_domain:
            raise DeploymentRefusal("deployed frontend URL changed after foundation provisioning")

        lease.assert_healthy()
        try:
            firebase_domain = _configure_firebase_domain(
                inputs.firebase_runtime_service_account,
                inputs.firebase_project_id,
                frontend_url,
                domain_admin_service_account=inputs.firebase_domain_admin_service_account,
                mutation_guard=lease.assert_healthy,
            )
        except DeploymentRefusal:
            firebase_domain = FirebaseDomainResult(
                status="not_configured_check_service_account_iam",
                hostname=urllib.parse.urlsplit(frontend_url).hostname or "unavailable",
            )
        print(f"firebase_authorized_domain: {firebase_domain.status}")

        backend, frontend = verify_live(
            resource_group=resource_group,
            backend_app=backend_app,
            frontend_app=frontend_app,
            expected_backend_identity_id=backend_identity_id,
            expected_backend_identity_principal_id=backend_identity_principal_id,
            expected_frontend_identity_id=frontend_identity_id,
            expected_frontend_identity_principal_id=frontend_identity_principal_id,
            key_vault_id=key_vault_id,
            subscription_id=args.subscription_id,
            tenant_id=args.tenant_id,
            expected_secret_versions={
                AZURE_KEY_SECRET_NAME: azure_key_version,
                FIREBASE_SECRET_NAME: firebase_version,
            },
            expected_sha=revision.sha,
            health_timeout_seconds=args.health_timeout_seconds,
        )
        _verify_old_revisions_inactive(
            resource_group=resource_group,
            app_name=backend_app,
            current_revision=backend.latest_revision,
        )
        print("Finalizing Key Vault rotation after backend health and revision retirement")
        _finalize_key_vault_secret_rotation(
            vault_name=key_vault_name,
            secret_name=AZURE_KEY_SECRET_NAME,
            current_version=azure_key_version,
            mutation_guard=lease.assert_healthy,
        )
        _finalize_key_vault_secret_rotation(
            vault_name=key_vault_name,
            secret_name=FIREBASE_SECRET_NAME,
            current_version=firebase_version,
            mutation_guard=lease.assert_healthy,
        )
        legacy_firebase_secret = _retire_legacy_firebase_secret(
            vault_name=key_vault_name,
            mutation_guard=lease.assert_healthy,
        )
    _assert_no_key_vault_writer(key_vault_id)
    print(f"legacy_firebase_secret: {legacy_firebase_secret}")
    _print_verification(backend, frontend)
    if firebase_domain.status.startswith("not_configured"):
        print(f"firebase_hostname_requiring_manual_authorization: {firebase_domain.hostname}")
        print("deployment_status: manual_action_required")
        return 3
    return 0


def verify(args: argparse.Namespace) -> int:
    resource_group = _require_safe_resource_name(args.resource_group, "resource group")
    backend_app = _require_container_app_name(args.backend_app, "backend app")
    frontend_app = _require_container_app_name(args.frontend_app, "frontend app")
    expected_sha = args.expected_sha
    if expected_sha is None:
        expected_sha = _run_command(
            ["git", "rev-parse", "--verify", "HEAD"], operation="resolve expected git HEAD"
        ).strip()
    if not _FULL_SHA.fullmatch(expected_sha):
        raise DeploymentRefusal("expected release SHA is not a full git SHA")
    foundation = _existing_deployment_outputs(
        resource_group=resource_group,
        deployment_name=FOUNDATION_DEPLOYMENT,
    )
    apps = _existing_deployment_outputs(
        resource_group=resource_group,
        deployment_name=APPS_DEPLOYMENT,
    )
    secret_versions = {
        AZURE_KEY_SECRET_NAME: _output_value(apps, "azureOpenAiSecretVersion"),
        FIREBASE_SECRET_NAME: _output_value(apps, "firebaseRuntimeSecretVersion"),
    }
    key_vault_id = _output_value(foundation, "keyVaultId")
    key_vault_name = _output_value(foundation, "keyVaultName")
    deployment_principal = _current_principal()
    with (
        _deployment_blob_lease(
            account_name=_output_value(foundation, "deploymentLockStorageAccountName"),
            container_name=_output_value(foundation, "deploymentLockContainerName"),
            blob_name=_output_value(foundation, "deploymentLockBlobName"),
            subscription_id=args.subscription_id,
        ) as lease,
        _temporary_key_vault_write(
            key_vault_id,
            principal=deployment_principal,
            mutation_guard=lease.assert_healthy,
        ),
    ):
        backend, frontend = verify_live(
            resource_group=resource_group,
            backend_app=backend_app,
            frontend_app=frontend_app,
            expected_backend_identity_id=_output_value(foundation, "identityId"),
            expected_backend_identity_principal_id=_output_value(foundation, "identityPrincipalId"),
            expected_frontend_identity_id=_output_value(foundation, "frontendIdentityId"),
            expected_frontend_identity_principal_id=_output_value(
                foundation, "frontendIdentityPrincipalId"
            ),
            key_vault_id=key_vault_id,
            subscription_id=args.subscription_id,
            tenant_id=args.tenant_id,
            expected_secret_versions=secret_versions,
            expected_sha=expected_sha,
            health_timeout_seconds=args.health_timeout_seconds,
        )
        _verify_old_revisions_inactive(
            resource_group=resource_group,
            app_name=backend_app,
            current_revision=backend.latest_revision,
        )
        for secret_name, version in secret_versions.items():
            _verify_rotation_postcondition(
                vault_name=key_vault_name,
                secret_name=secret_name,
                current_version=version,
            )
        _verify_legacy_secret_postcondition(vault_name=key_vault_name)
        lease.assert_healthy()
    _assert_no_key_vault_writer(key_vault_id)
    _print_verification(backend, frontend)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy and verify the isolated Murmur Azure pilot."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_live_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--resource-group", default=DEFAULT_RESOURCE_GROUP)
        command.add_argument("--backend-app", default=DEFAULT_BACKEND_APP)
        command.add_argument("--frontend-app", default=DEFAULT_FRONTEND_APP)
        command.add_argument("--health-timeout-seconds", type=float, default=300)
        command.add_argument(
            "--subscription-id",
            default=os.getenv("MURMUR_AZURE_SUBSCRIPTION_ID"),
            required=not bool(os.getenv("MURMUR_AZURE_SUBSCRIPTION_ID")),
            help="Exact Azure subscription GUID expected for this deployment.",
        )
        command.add_argument(
            "--tenant-id",
            default=os.getenv("MURMUR_AZURE_TENANT_ID"),
            required=not bool(os.getenv("MURMUR_AZURE_TENANT_ID")),
            help="Exact Azure tenant GUID expected for this deployment.",
        )

    deploy_parser = subparsers.add_parser("deploy", help="Provision and verify the pilot.")
    add_live_arguments(deploy_parser)
    deploy_parser.add_argument("--location", default=DEFAULT_LOCATION)
    deploy_parser.add_argument("--backend-env", type=Path, required=True)
    deploy_parser.add_argument("--frontend-env", type=Path, required=True)
    deploy_parser.set_defaults(handler=deploy)

    verify_parser = subparsers.add_parser(
        "verify", help="Read and probe the live pilot without provider calls."
    )
    add_live_arguments(verify_parser)
    verify_parser.add_argument(
        "--expected-sha",
        help="Full release SHA to require; defaults to the current local HEAD.",
    )
    verify_parser.set_defaults(handler=verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not math.isfinite(args.health_timeout_seconds) or args.health_timeout_seconds <= 0:
        print("Deployment refused: health timeout must be positive", file=sys.stderr)
        return 2
    try:
        return int(args.handler(args))
    except DeploymentRefusal as exc:
        print(f"Deployment refused: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Deployment interrupted; no credential values were printed", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            f"Deployment failed safely ({type(exc).__name__}); no credential values were printed",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

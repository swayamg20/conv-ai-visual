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
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
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
FIREBASE_SECRET_NAME = "firebase-service-account-json"
KEY_VAULT_WRITER_ROLE = "Key Vault Secrets Officer"
IDENTITY_TOOLKIT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
IDENTITY_TOOLKIT_CONFIG_ROOT = "https://identitytoolkit.googleapis.com/admin/v2"

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
    parameters: Mapping[str, str],
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
        command.extend(f"{key}={value}" for key, value in parameters.items())
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


def _grant_key_vault_write(key_vault_id: str) -> str | None:
    object_id, principal_type = _current_principal()
    existing = _run_json(
        [
            "az",
            "role",
            "assignment",
            "list",
            "--assignee-object-id",
            object_id,
            "--role",
            KEY_VAULT_WRITER_ROLE,
            "--scope",
            key_vault_id,
            "--fill-principal-name",
            "false",
            "--query",
            "[].id",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation="inspect deployer Key Vault secret-write access",
    )
    if not isinstance(existing, list):
        raise DeploymentRefusal("Azure returned invalid Key Vault role-assignment metadata")
    if existing:
        return None
    assignment_id = _run_command(
        [
            "az",
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            object_id,
            "--assignee-principal-type",
            principal_type,
            "--role",
            KEY_VAULT_WRITER_ROLE,
            "--scope",
            key_vault_id,
            "--only-show-errors",
            "--query",
            "id",
            "--output",
            "tsv",
        ],
        operation="grant deployer Key Vault secret-write access",
    ).strip()
    expected_fragment = "/providers/microsoft.authorization/roleassignments/"
    if expected_fragment not in assignment_id.casefold():
        raise DeploymentRefusal("Azure returned an invalid Key Vault role-assignment ID")
    return assignment_id


def _revoke_key_vault_write(assignment_id: str) -> None:
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
        operation="revoke temporary deployer Key Vault secret-write access",
    )


@contextmanager
def _temporary_key_vault_write(key_vault_id: str) -> Iterator[None]:
    assignment_id = _grant_key_vault_write(key_vault_id)
    try:
        yield
    finally:
        if assignment_id is not None:
            _revoke_key_vault_write(assignment_id)


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
) -> str:
    secret_id = ""
    with _secure_temp_file(payload, suffix=".secret") as path:
        for attempt in range(attempts):
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
                        "--only-show-errors",
                        "--query",
                        "id",
                        "--output",
                        "tsv",
                    ],
                    timeout_seconds=60,
                    operation=f"write Key Vault secret {secret_name}",
                )
                break
            except DeploymentRefusal:
                if attempt + 1 == attempts:
                    raise
                time.sleep(min(2**attempt, 10))
    current_version = _key_vault_secret_version(
        secret_id.strip(),
        vault_name=vault_name,
        secret_name=secret_name,
    )
    _disable_previous_secret_versions(
        vault_name=vault_name,
        secret_name=secret_name,
        current_version=current_version,
    )
    return current_version


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


def _disable_previous_secret_versions(
    *,
    vault_name: str,
    secret_name: str,
    current_version: str,
) -> None:
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
            "[].{id:id,enabled:attributes.enabled}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        timeout_seconds=60,
        operation=f"inspect Key Vault secret versions for {secret_name}",
    )
    if not isinstance(versions, list):
        raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
    for item in versions:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise DeploymentRefusal("Azure returned invalid Key Vault secret-version metadata")
        version = _key_vault_secret_version(
            item["id"],
            vault_name=vault_name,
            secret_name=secret_name,
        )
        if version.casefold() == current_version.casefold() or item.get("enabled") is False:
            continue
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
        vault_names: set[str] = set()
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
                or parsed.path != f"/secrets/{secret_name}"
                or parsed.query
                or parsed.fragment
            ):
                raise DeploymentRefusal(
                    f"Container App {name} Key Vault reference is not versionless"
                )
            vault_names.add(parsed.hostname.removesuffix(".vault.azure.net"))
        if len(vault_names) != 1:
            raise DeploymentRefusal(f"Container App {name} uses inconsistent Key Vaults")
        key_vault_name = vault_names.pop()
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


def _verify_frontend_key_vault_boundary(*, frontend_principal_id: str, key_vault_id: str) -> None:
    role_ids: set[str] = set()
    for secret_name in (AZURE_KEY_SECRET_NAME, FIREBASE_SECRET_NAME):
        secret_scope = f"{key_vault_id.rstrip('/')}/secrets/{secret_name}"
        result = _run_json(
            [
                "az",
                "role",
                "assignment",
                "list",
                "--assignee-object-id",
                frontend_principal_id,
                "--scope",
                secret_scope,
                "--include-inherited",
                "--include-groups",
                "--fill-principal-name",
                "false",
                "--fill-role-definition-name",
                "false",
                "--query",
                "[].roleDefinitionId",
                "--output",
                "json",
                "--only-show-errors",
            ],
            timeout_seconds=60,
            operation=f"inspect frontend access to Key Vault secret {secret_name}",
        )
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise DeploymentRefusal("frontend Key Vault role assignments are invalid")
        role_ids.update(result)

    secret_read = "Microsoft.KeyVault/vaults/secrets/getSecret/action"
    role_assignment_write = "Microsoft.Authorization/roleAssignments/write"
    for raw_role_id in sorted(role_ids):
        role_id = raw_role_id.rsplit("/", 1)[-1].casefold()
        if not _ROLE_DEFINITION_ID.fullmatch(role_id):
            raise DeploymentRefusal("frontend Key Vault role assignment is invalid")
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
            operation="inspect frontend Key Vault role definition",
        )
        if not isinstance(permissions, list) or any(
            not isinstance(item, dict) for item in permissions
        ):
            raise DeploymentRefusal("frontend Key Vault role definition is invalid")
        if any(
            _permission_grants(item, secret_read, data_plane=True)
            or _permission_grants(item, role_assignment_write, data_plane=False)
            for item in permissions
        ):
            raise DeploymentRefusal("frontend identity can access or grant Key Vault secrets")


def verify_live(
    *,
    resource_group: str,
    backend_app: str,
    frontend_app: str,
    expected_backend_identity_id: str,
    expected_frontend_identity_id: str,
    expected_frontend_identity_principal_id: str,
    key_vault_id: str,
    subscription_id: str,
    tenant_id: str,
    expected_sha: str | None = None,
    health_timeout_seconds: float = 300,
) -> tuple[AppInspection, AppInspection]:
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
    print("key_vault_references: versionless and enabled")
    print("database_configuration: local ephemeral SQLite; PostgreSQL required for durability")
    print("persistence_restart_proof: not_applicable_ephemeral_pilot")
    print("paid_model_calls: 0")


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

    print("Importing server credentials into Azure Key Vault")
    with _temporary_key_vault_write(key_vault_id):
        _write_key_vault_secret(
            vault_name=key_vault_name,
            secret_name=AZURE_KEY_SECRET_NAME,
            payload=inputs.azure_openai_key.encode("utf-8"),
        )
        _write_key_vault_secret(
            vault_name=key_vault_name,
            secret_name=FIREBASE_SECRET_NAME,
            payload=inputs.firebase_runtime_json_bytes(),
        )

    print("Building backend image from the accepted git archive")
    backend_digest = _build_backend(registry_name, revision.sha)
    print("Building frontend image against the derived backend origin")
    frontend_digest = _build_frontend(
        registry_name,
        revision.sha,
        backend_url,
        inputs.frontend_public,
    )

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
        },
    )
    live_backend_url = _validate_container_app_url(
        _output_value(app_outputs, "backendUrl"), "deployed backend URL"
    )
    frontend_url = _validate_container_app_url(
        _output_value(app_outputs, "frontendUrl"), "deployed frontend URL"
    )
    if live_backend_url != backend_url:
        raise DeploymentRefusal("deployed backend URL changed after the frontend build")
    if frontend_url != frontend_url_from_domain:
        raise DeploymentRefusal("deployed frontend URL changed after foundation provisioning")

    try:
        firebase_domain = _configure_firebase_domain(
            inputs.firebase_runtime_service_account,
            inputs.firebase_project_id,
            frontend_url,
            domain_admin_service_account=inputs.firebase_domain_admin_service_account,
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
        expected_frontend_identity_id=frontend_identity_id,
        expected_frontend_identity_principal_id=frontend_identity_principal_id,
        key_vault_id=key_vault_id,
        subscription_id=args.subscription_id,
        tenant_id=args.tenant_id,
        expected_sha=revision.sha,
        health_timeout_seconds=args.health_timeout_seconds,
    )
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
    backend, frontend = verify_live(
        resource_group=resource_group,
        backend_app=backend_app,
        frontend_app=frontend_app,
        expected_backend_identity_id=_output_value(foundation, "identityId"),
        expected_frontend_identity_id=_output_value(foundation, "frontendIdentityId"),
        expected_frontend_identity_principal_id=_output_value(
            foundation, "frontendIdentityPrincipalId"
        ),
        key_vault_id=_output_value(foundation, "keyVaultId"),
        subscription_id=args.subscription_id,
        tenant_id=args.tenant_id,
        expected_sha=expected_sha,
        health_timeout_seconds=args.health_timeout_seconds,
    )
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

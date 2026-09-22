"""Redaction and orchestration contracts for the Azure pilot driver."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import io
import json
import stat
import subprocess
import sys
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts" / "deploy_azure.py"
MODULE_SPEC = importlib.util.spec_from_file_location("deploy_azure", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
deploy = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = deploy
MODULE_SPEC.loader.exec_module(deploy)

SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = f"sha256:{'a' * 64}"
FRONTEND_DIGEST = f"sha256:{'b' * 64}"
AZURE_KEY = "azure-secret-must-not-escape"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nprivate-secret\n-----END PRIVATE KEY-----\n"
RUNTIME_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nruntime-private-secret\n-----END PRIVATE KEY-----\n"
)
BACKEND_URL = "https://murmur-api.example.centralindia.azurecontainerapps.io"
FRONTEND_URL = "https://murmur-web.example.centralindia.azurecontainerapps.io"
BACKEND_IDENTITY_ID = "/subscriptions/sub/resourceGroups/rg/providers/backend"
FRONTEND_IDENTITY_ID = "/subscriptions/sub/resourceGroups/rg/providers/frontend"
FRONTEND_IDENTITY_PRINCIPAL_ID = "11111111-1111-1111-1111-111111111111"
KEY_VAULT_ID = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/vault"
SUBSCRIPTION_ID = "22222222-2222-2222-2222-222222222222"
TENANT_ID = "33333333-3333-3333-3333-333333333333"
BACKEND_IDENTITY_PRINCIPAL_ID = "44444444-4444-4444-4444-444444444444"
AZURE_KEY_VERSION = "a" * 32
FIREBASE_VERSION = "b" * 32
SECRET_VERSIONS = {
    deploy.AZURE_KEY_SECRET_NAME: AZURE_KEY_VERSION,
    deploy.FIREBASE_SECRET_NAME: FIREBASE_VERSION,
}
EXPECTED_IMAGE_ARGS = {
    "expected_registry_server": "murmurregistry.azurecr.io",
    "expected_backend_image_digest": DIGEST,
    "expected_frontend_image_digest": FRONTEND_DIGEST,
}


def _service_account(
    project_id: str = "firebase-project",
    *,
    purpose: str = "runtime",
) -> dict[str, str]:
    private_key = RUNTIME_PRIVATE_KEY if purpose == "runtime" else PRIVATE_KEY
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": f"{purpose}-private-key-id",
        "private_key": private_key,
        "client_email": f"firebase-{purpose}@{project_id}.iam.gserviceaccount.com",
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    runtime_account = tmp_path / "firebase-runtime-service-account.json"
    runtime_account.write_text(json.dumps(_service_account()), encoding="utf-8")
    runtime_account.chmod(0o600)
    domain_admin_account = tmp_path / "firebase-domain-admin-service-account.json"
    domain_admin_account.write_text(
        json.dumps(_service_account(purpose="domain-admin")), encoding="utf-8"
    )
    domain_admin_account.chmod(0o600)

    backend = tmp_path / ".env"
    backend.write_text(
        "\n".join(
            (
                f"AZURE_OPENAI_API_KEY={AZURE_KEY}",
                "AZURE_OPENAI_ENDPOINT=https://murmur-resource.services.ai.azure.com",
                "AZURE_OPENAI_DEPLOYMENT=murmur-gpt-oss-120b",
                "VOICE_RUNTIME=websocket_v1",
                "FIREBASE_PROJECT_ID=firebase-project",
                f"FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH={runtime_account}",
                f"FIREBASE_DOMAIN_ADMIN_SERVICE_ACCOUNT_PATH={domain_admin_account}",
            )
        ),
        encoding="utf-8",
    )
    backend.chmod(0o600)
    frontend = tmp_path / ".env.local"
    frontend.write_text(
        "\n".join(
            (
                "NEXT_PUBLIC_API_URL=http://localhost:8000",
                "NEXT_PUBLIC_FIREBASE_API_KEY=browser-public-key",
                "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=firebase-project.firebaseapp.com",
                "NEXT_PUBLIC_FIREBASE_PROJECT_ID=firebase-project",
                "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=firebase-project.appspot.com",
                "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=123456789",
                "NEXT_PUBLIC_FIREBASE_APP_ID=1:123456789:web:abcdef",
                "NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID=G-EXAMPLE",
                "NEXT_PUBLIC_VOICE_RUNTIME=disabled",
            )
        ),
        encoding="utf-8",
    )
    return backend, frontend


def _output(value: str) -> dict[str, object]:
    return {"type": "String", "value": value}


def _secret_version(
    secret_name: str,
    version: str,
    *,
    enabled: bool,
    rotation_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": f"https://murmur-vault.vault.azure.net/secrets/{secret_name}/{version}",
        "enabled": enabled,
        "tags": ({deploy.KEY_VAULT_ROTATION_TAG: rotation_id} if rotation_id is not None else None),
    }


def _container_app(*, backend: bool, inline_secret: bool = False) -> dict[str, object]:
    name = "murmur-api" if backend else "murmur-web"
    fqdn = (
        "murmur-api.example.centralindia.azurecontainerapps.io"
        if backend
        else "murmur-web.example.centralindia.azurecontainerapps.io"
    )
    port = 8000 if backend else 3000
    probes = [
        {"type": "Startup", "httpGet": {"path": "/healthz", "port": port}},
        {"type": "Liveness", "httpGet": {"path": "/healthz", "port": port}},
        {
            "type": "Readiness",
            "httpGet": {"path": "/readyz" if backend else "/healthz", "port": port},
        },
    ]
    env: list[dict[str, str]]
    secrets: list[dict[str, str]] = []
    volumes: list[dict[str, str]] = []
    mounts: list[dict[str, str]] = []
    containers: list[dict[str, object]]
    identity_name = "backend-identity" if backend else "frontend-identity"
    identity_id = f"/subscriptions/sub/resourcegroups/rg/providers/{identity_name}"
    reference_identity_id = f"/subscriptions/sub/resourceGroups/rg/providers/{identity_name}"
    if backend:
        plain_values = {
            env_name: "test-value"
            for env_name in deploy.BACKEND_ENV_NAMES
            if env_name not in {"AZURE_OPENAI_API_KEY", "FIREBASE_SERVICE_ACCOUNT_JSON"}
        }
        plain_values.update(
            {
                "MURMUR_RELEASE_SHA": SHA,
                "MURMUR_DATA_DIR": "/home/murmur/data",
                "MURMUR_SQLITE_JOURNAL_MODE": "WAL",
                "ALLOWED_CORS_ORIGINS": FRONTEND_URL,
                "VOICE_RUNTIME": "websocket_v1",
            }
        )
        env = [{"name": key, "value": value} for key, value in plain_values.items()]
        mounts = [{"mountPath": "/home/murmur/data", "volumeName": "murmur-data"}]
        volumes = [{"name": "murmur-data", "storageType": "EmptyDir"}]
        env.extend(
            (
                {"name": "AZURE_OPENAI_API_KEY", "secretRef": "azure-openai-api-key"},
                {
                    "name": "FIREBASE_SERVICE_ACCOUNT_JSON",
                    "secretRef": deploy.FIREBASE_SECRET_NAME,
                },
            )
        )
        secrets = [
            {
                "name": secret_name,
                "keyVaultUrl": (
                    f"https://murmur-vault.vault.azure.net/secrets/{secret_name}/"
                    f"{SECRET_VERSIONS[secret_name]}"
                ),
                "identity": reference_identity_id,
                **({"value": AZURE_KEY} if inline_secret and index == 0 else {}),
            }
            for index, secret_name in enumerate(deploy.ACTIVE_SECRET_NAMES)
        ]
        containers = [
            {
                "name": "api",
                "image": f"murmurregistry.azurecr.io/{name}@{DIGEST}",
                "env": env,
                "probes": probes,
                "volumeMounts": mounts,
            },
        ]
    else:
        env = [
            {"name": "HOSTNAME", "value": "0.0.0.0"},
            {"name": "PORT", "value": "3000"},
            {"name": "MURMUR_RELEASE_SHA", "value": SHA},
        ]
        containers = [
            {
                "name": "web",
                "image": f"murmurregistry.azurecr.io/{name}@{FRONTEND_DIGEST}",
                "env": env,
                "probes": probes,
                "volumeMounts": mounts,
            }
        ]
    return {
        "name": name,
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {identity_id: {}},
        },
        "properties": {
            "provisioningState": "Succeeded",
            "runningStatus": "Running",
            "latestRevisionName": f"{name}--revision",
            "latestReadyRevisionName": f"{name}--revision",
            "configuration": {
                "activeRevisionsMode": "Single",
                "identitySettings": [{"identity": reference_identity_id, "lifecycle": "None"}],
                "registries": [
                    {
                        "server": "murmurregistry.azurecr.io",
                        "identity": reference_identity_id,
                        "username": "",
                        "passwordSecretRef": "",
                    }
                ],
                "ingress": {
                    "fqdn": fqdn,
                    "external": True,
                    "allowInsecure": False,
                    "targetPort": port,
                },
                "secrets": secrets,
            },
            "template": {
                "scale": {"minReplicas": 1, "maxReplicas": 1},
                "containers": containers,
                "volumes": volumes,
            },
        },
    }


def _inspection(name: str, url: str, *, backend: bool) -> deploy.AppInspection:
    digest = DIGEST if backend else FRONTEND_DIGEST
    return deploy.AppInspection(
        name=name,
        url=url,
        image=f"murmurregistry.azurecr.io/{name}@{digest}",
        image_digest=digest,
        registry_server="murmurregistry.azurecr.io",
        release_sha=SHA,
        latest_revision=f"{name}--revision",
        latest_ready_revision=f"{name}--revision",
        provisioning_state="Succeeded",
        running_status="Running",
        min_replicas=1,
        max_replicas=1,
        probe_types=("Liveness", "Readiness", "Startup"),
        key_vault_name="murmur-vault" if backend else None,
        key_vault_secret_versions=(tuple(SECRET_VERSIONS.items()) if backend else ()),
        identity_id=BACKEND_IDENTITY_ID if backend else FRONTEND_IDENTITY_ID,
    )


def test_load_deployment_inputs_validates_projects_without_printing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backend, frontend = _write_inputs(tmp_path)

    inputs = deploy.load_deployment_inputs(backend, frontend)

    assert inputs.azure_openai_key == AZURE_KEY
    assert inputs.azure_openai_endpoint == "https://murmur-resource.services.ai.azure.com"
    assert inputs.azure_openai_deployment == "murmur-gpt-oss-120b"
    assert inputs.firebase_project_id == "firebase-project"
    assert json.loads(inputs.firebase_runtime_json_bytes())["private_key"] == RUNTIME_PRIVATE_KEY
    assert (
        inputs.firebase_domain_admin_service_account["private_key"] == PRIVATE_KEY  # type: ignore[index]
    )
    assert inputs.frontend_public["NEXT_PUBLIC_FIREBASE_API_KEY"] == "browser-public-key"
    assert "NEXT_PUBLIC_API_URL" not in inputs.frontend_public
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert AZURE_KEY not in repr(inputs)
    assert PRIVATE_KEY not in repr(inputs)
    assert RUNTIME_PRIVATE_KEY not in repr(inputs)


@pytest.mark.parametrize(
    ("filename", "old", "new", "message"),
    (
        ("backend", "VOICE_RUNTIME=websocket_v1", "VOICE_RUNTIME=legacy", "VOICE_RUNTIME"),
        (
            "frontend",
            "NEXT_PUBLIC_VOICE_RUNTIME=disabled",
            "NEXT_PUBLIC_VOICE_RUNTIME=legacy",
            "NEXT_PUBLIC_VOICE_RUNTIME",
        ),
    ),
)
def test_load_deployment_inputs_rejects_non_voice_v2_runtime(
    tmp_path: Path, filename: str, old: str, new: str, message: str
) -> None:
    backend, frontend = _write_inputs(tmp_path)
    target = backend if filename == "backend" else frontend
    target.write_text(target.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    with pytest.raises(deploy.DeploymentRefusal, match=message):
        deploy.load_deployment_inputs(backend, frontend)


def test_load_deployment_inputs_rejects_mismatched_firebase_project(tmp_path: Path) -> None:
    backend, frontend = _write_inputs(tmp_path)
    frontend.write_text(
        frontend.read_text(encoding="utf-8").replace(
            "NEXT_PUBLIC_FIREBASE_PROJECT_ID=firebase-project",
            "NEXT_PUBLIC_FIREBASE_PROJECT_ID=other-project",
        ),
        encoding="utf-8",
    )

    with pytest.raises(deploy.DeploymentRefusal, match="project IDs do not match"):
        deploy.load_deployment_inputs(backend, frontend)


def test_load_deployment_inputs_rejects_reused_firebase_runtime_credential(
    tmp_path: Path,
) -> None:
    backend, frontend = _write_inputs(tmp_path)
    lines = backend.read_text(encoding="utf-8").splitlines()
    runtime_path = next(
        line.split("=", 1)[1]
        for line in lines
        if line.startswith("FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH=")
    )
    backend.write_text(
        "\n".join(
            f"FIREBASE_DOMAIN_ADMIN_SERVICE_ACCOUNT_PATH={runtime_path}"
            if line.startswith("FIREBASE_DOMAIN_ADMIN_SERVICE_ACCOUNT_PATH=")
            else line
            for line in lines
        ),
        encoding="utf-8",
    )

    with pytest.raises(deploy.DeploymentRefusal, match="must be different"):
        deploy.load_deployment_inputs(backend, frontend)


def test_command_failure_never_includes_captured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["az"], returncode=1, stdout=AZURE_KEY, stderr=PRIVATE_KEY
    )
    monkeypatch.setattr(deploy.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(deploy.DeploymentRefusal) as raised:
        deploy._run_command(["az", "keyvault", "secret", "set"])

    assert AZURE_KEY not in str(raised.value)
    assert PRIVATE_KEY not in str(raised.value)


def test_validate_azure_session_refuses_a_different_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy.shutil, "which", lambda _command: "/opt/homebrew/bin/az")
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: {
            "state": "Enabled",
            "id": "44444444-4444-4444-4444-444444444444",
            "tenantId": TENANT_ID,
        },
    )

    with pytest.raises(deploy.DeploymentRefusal, match="pinned deployment target"):
        deploy._validate_azure_session(
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
        )


def test_temporary_key_vault_role_is_revoked_after_a_failed_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assignment_id = (
        "/subscriptions/sub/providers/Microsoft.Authorization/roleAssignments/"
        "55555555-5555-5555-5555-555555555555"
    )
    assignment = deploy.RoleAssignmentMetadata(
        id=assignment_id,
        scope=KEY_VAULT_ID,
        principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
        role_definition_id=deploy.KEY_VAULT_SECRETS_OFFICER_ROLE_ID,
        description=deploy.KEY_VAULT_WRITER_DESCRIPTION,
    )
    revoked: list[tuple[str, deploy.RoleAssignmentMetadata]] = []
    monkeypatch.setattr(
        deploy,
        "_grant_key_vault_write",
        lambda _vault, **_kwargs: assignment,
    )
    monkeypatch.setattr(
        deploy,
        "_revoke_key_vault_write",
        lambda vault, value, **_kwargs: revoked.append((vault, value)),
    )

    with pytest.raises(RuntimeError, match="write failed"):
        with deploy._temporary_key_vault_write(KEY_VAULT_ID):
            raise RuntimeError("write failed")

    assert revoked == [(KEY_VAULT_ID, assignment)]


def test_key_vault_writer_refuses_a_preexisting_direct_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy, "_current_principal", lambda: (BACKEND_IDENTITY_PRINCIPAL_ID, "User")
    )
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: [
            {
                "id": (
                    "/subscriptions/sub/providers/Microsoft.Authorization/roleAssignments/"
                    "55555555-5555-5555-5555-555555555555"
                ),
                "scope": KEY_VAULT_ID,
                "principalId": BACKEND_IDENTITY_PRINCIPAL_ID,
                "roleDefinitionId": deploy.KEY_VAULT_SECRETS_OFFICER_ROLE_ID,
                "description": None,
            }
        ],
    )

    with pytest.raises(deploy.DeploymentRefusal, match="already has a direct"):
        deploy._grant_key_vault_write(KEY_VAULT_ID)


def test_key_vault_writer_reconciles_an_accepted_create_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = (BACKEND_IDENTITY_PRINCIPAL_ID, "User")
    expected = deploy._expected_writer_assignment(
        KEY_VAULT_ID, object_id=BACKEND_IDENTITY_PRINCIPAL_ID
    )
    snapshots: list[object] = [
        [],
        [
            {
                "id": expected.id,
                "scope": expected.scope,
                "principalId": expected.principal_id,
                "roleDefinitionId": expected.role_definition_id,
                "description": expected.description,
            }
        ],
    ]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: snapshots.pop(0))
    monkeypatch.setattr(
        deploy,
        "_run_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(deploy.DeploymentRefusal("timeout")),
    )

    assert deploy._grant_key_vault_write(KEY_VAULT_ID, principal=principal) == expected


def test_key_vault_writer_reconciles_an_accepted_delete_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assignment = deploy._expected_writer_assignment(
        KEY_VAULT_ID, object_id=BACKEND_IDENTITY_PRINCIPAL_ID
    )
    present = {
        "id": assignment.id,
        "scope": assignment.scope,
        "principalId": assignment.principal_id,
        "roleDefinitionId": assignment.role_definition_id,
        "description": assignment.description,
    }
    snapshots: list[object] = [[present], []]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: snapshots.pop(0))
    monkeypatch.setattr(
        deploy,
        "_run_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(deploy.DeploymentRefusal("timeout")),
    )

    deploy._revoke_key_vault_write(KEY_VAULT_ID, assignment, attempts=1)


def test_deployment_lock_show_uses_the_blob_show_name_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def fake_run(command: list[str], **_kwargs: Any) -> object:
        observed.extend(command)
        return {"name": "azure-pilot.lock", "type": "BlockBlob"}

    monkeypatch.setattr(deploy, "_run_json", fake_run)

    deploy._show_deployment_lock_blob(
        account_name="murmurlockaccount",
        container_name="deployment-locks",
        blob_name="azure-pilot.lock",
        subscription_id=SUBSCRIPTION_ID,
    )

    assert "--name" in observed
    assert observed[observed.index("--name") + 1] == "azure-pilot.lock"
    assert "--blob-name" not in observed


def test_deployment_lease_reconciles_accepted_acquire_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_id = "55555555-5555-5555-5555-555555555555"
    calls = 0

    def fake_run(*_args: Any, **_kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise deploy.DeploymentRefusal("timeout")
        return lease_id

    monkeypatch.setattr(deploy, "_run_command", fake_run)

    deploy._acquire_deployment_lease(
        account_name="murmurlockaccount",
        container_name="deployment-locks",
        blob_name="azure-pilot.lock",
        lease_id=lease_id,
        subscription_id=SUBSCRIPTION_ID,
    )
    assert calls == 2


def test_deployment_lease_refuses_an_unconfirmed_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(deploy.DeploymentRefusal("conflict")),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="held by another deployment"):
        deploy._acquire_deployment_lease(
            account_name="murmurlockaccount",
            container_name="deployment-locks",
            blob_name="azure-pilot.lock",
            lease_id="55555555-5555-5555-5555-555555555555",
            subscription_id=SUBSCRIPTION_ID,
        )


def test_key_vault_secret_uses_mode_600_file_and_suppresses_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    current_version = "a" * 32
    previous_version = "b" * 32
    rotation_id = "rotation-id"

    def fake_run(command: Any, **kwargs: Any) -> str:
        path = Path(command[command.index("--file") + 1])
        observed["path"] = path
        observed["payload"] = path.read_text(encoding="utf-8")
        observed["mode"] = stat.S_IMODE(path.stat().st_mode)
        observed["command"] = tuple(command)
        assert kwargs["operation"] == "write Key Vault secret azure-openai-api-key"
        return (
            f"https://murmur-vault.vault.azure.net/secrets/azure-openai-api-key/{current_version}\n"
        )

    def fake_json(command: Any, **_kwargs: Any) -> object:
        if "list-versions" in command:
            return [
                _secret_version(
                    "azure-openai-api-key",
                    current_version,
                    enabled=True,
                    rotation_id=rotation_id,
                ),
                _secret_version(
                    "azure-openai-api-key",
                    previous_version,
                    enabled=True,
                ),
            ]
        if "show" in command:
            return _secret_version(
                "azure-openai-api-key",
                current_version,
                enabled=True,
                rotation_id=rotation_id,
            )
        if "list" in command:
            return []
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_command", fake_run)
    monkeypatch.setattr(deploy, "_run_json", fake_json)
    monkeypatch.setattr(deploy.secrets, "token_hex", lambda _length: rotation_id)

    written_version = deploy._write_key_vault_secret(
        vault_name="murmur-vault",
        secret_name="azure-openai-api-key",
        payload=AZURE_KEY.encode(),
        attempts=1,
    )

    command = observed["command"]
    assert observed["mode"] == 0o600
    assert observed["payload"] == AZURE_KEY
    assert AZURE_KEY not in command
    assert command[-4:] == ("--query", "id", "--output", "tsv")
    assert command[command.index("--tags") + 1] == (
        f"{deploy.KEY_VAULT_ROTATION_TAG}={rotation_id}"
    )
    assert not observed["path"].exists()
    assert written_version == current_version
    assert "disabled_version" not in observed


def test_key_vault_secret_reconciles_an_accepted_write_timeout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_version = "a" * 32
    rotation_id = "rotation-id"
    writes = 0

    def fake_run(command: Any, **_kwargs: Any) -> str:
        nonlocal writes
        assert "secret" in command and "set" in command
        writes += 1
        raise deploy.DeploymentRefusal("simulated timeout")

    def fake_json(command: Any, **_kwargs: Any) -> object:
        if "list-versions" in command:
            return [
                _secret_version(
                    "azure-openai-api-key",
                    current_version,
                    enabled=True,
                    rotation_id=rotation_id,
                )
            ]
        if "show" in command:
            return _secret_version(
                "azure-openai-api-key",
                current_version,
                enabled=True,
                rotation_id=rotation_id,
            )
        if "list" in command:
            return []
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_command", fake_run)
    monkeypatch.setattr(deploy, "_run_json", fake_json)
    monkeypatch.setattr(deploy.secrets, "token_hex", lambda _length: rotation_id)

    written_version = deploy._write_key_vault_secret(
        vault_name="murmur-vault",
        secret_name="azure-openai-api-key",
        payload=AZURE_KEY.encode(),
        attempts=1,
    )

    assert written_version == current_version
    assert writes == 1


def test_key_vault_secret_rejects_multiple_versions_for_one_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rotation_id = "rotation-id"
    versions = [
        _secret_version(
            "azure-openai-api-key",
            "a" * 32,
            enabled=True,
            rotation_id=rotation_id,
        ),
        _secret_version(
            "azure-openai-api-key",
            "b" * 32,
            enabled=True,
            rotation_id=rotation_id,
        ),
    ]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: versions)

    with pytest.raises(deploy.DeploymentRefusal, match="multiple versions"):
        deploy._await_staged_key_vault_secret(
            vault_name="murmur-vault",
            secret_name="azure-openai-api-key",
            rotation_id=rotation_id,
            returned_version=None,
            attempts=1,
        )


def test_key_vault_version_listing_rejects_empty_and_missing_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: [])

    with pytest.raises(deploy.DeploymentRefusal, match=r"invalid.*metadata"):
        deploy._list_key_vault_secret_versions(
            vault_name="murmur-vault",
            secret_name="azure-openai-api-key",
        )


def test_key_vault_rotation_rejects_listing_without_selected_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_version = "a" * 32
    previous_version = "b" * 32

    def fake_json(command: Any, **_kwargs: Any) -> object:
        if "list-versions" in command:
            return [_secret_version("azure-openai-api-key", previous_version, enabled=True)]
        if "show" in command:
            return _secret_version("azure-openai-api-key", selected_version, enabled=True)
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_json", fake_json)

    with pytest.raises(deploy.DeploymentRefusal, match="missing or disabled"):
        deploy._stable_key_vault_rotation_snapshot(
            vault_name="murmur-vault",
            secret_name="azure-openai-api-key",
            current_version=selected_version,
            attempts=1,
        )


def test_key_vault_rotation_rejects_an_unstable_version_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_version = "a" * 32
    previous_version = "b" * 32
    list_calls = 0

    def fake_json(command: Any, **_kwargs: Any) -> object:
        nonlocal list_calls
        if "list-versions" in command:
            list_calls += 1
            result = [_secret_version("azure-openai-api-key", selected_version, enabled=True)]
            if list_calls == 2:
                result.append(
                    _secret_version("azure-openai-api-key", previous_version, enabled=True)
                )
            return result
        if "show" in command:
            return _secret_version("azure-openai-api-key", selected_version, enabled=True)
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_json", fake_json)

    with pytest.raises(deploy.DeploymentRefusal, match="did not stabilize"):
        deploy._stable_key_vault_rotation_snapshot(
            vault_name="murmur-vault",
            secret_name="azure-openai-api-key",
            current_version=selected_version,
            attempts=1,
        )


def test_key_vault_rotation_finalizes_only_after_exact_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_version = "a" * 32
    previous_version = "b" * 32
    state = {current_version: True, previous_version: True}
    disabled: list[str] = []

    def versions() -> list[dict[str, object]]:
        return [
            _secret_version("azure-openai-api-key", version, enabled=enabled)
            for version, enabled in state.items()
        ]

    def fake_json(command: Any, **_kwargs: Any) -> object:
        if "list-versions" in command:
            return versions()
        if "show" in command:
            return _secret_version(
                "azure-openai-api-key", current_version, enabled=state[current_version]
            )
        raise AssertionError(command)

    def fake_run(command: Any, **_kwargs: Any) -> str:
        version = command[command.index("--version") + 1]
        disabled.append(version)
        state[version] = False
        return ""

    monkeypatch.setattr(deploy, "_run_json", fake_json)
    monkeypatch.setattr(deploy, "_run_command", fake_run)

    deploy._finalize_key_vault_secret_rotation(
        vault_name="murmur-vault",
        secret_name="azure-openai-api-key",
        current_version=current_version,
        attempts=1,
    )

    assert state == {current_version: True, previous_version: False}
    assert disabled == [previous_version]


def test_key_vault_rotation_refuses_concurrent_new_version_without_disabling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_version = "a" * 32
    previous_version = "b" * 32
    concurrent_version = "c" * 32
    list_calls = 0
    show_calls = 0
    disabled: list[str] = []

    def fake_json(command: Any, **_kwargs: Any) -> object:
        nonlocal list_calls, show_calls
        if "list-versions" in command:
            list_calls += 1
            result = [
                _secret_version("azure-openai-api-key", selected_version, enabled=True),
                _secret_version("azure-openai-api-key", previous_version, enabled=True),
            ]
            if list_calls >= 3:
                result.append(
                    _secret_version("azure-openai-api-key", concurrent_version, enabled=True)
                )
            return result
        if "show" in command:
            show_calls += 1
            version = selected_version if show_calls == 1 else concurrent_version
            return _secret_version("azure-openai-api-key", version, enabled=True)
        raise AssertionError(command)

    def fake_run(command: Any, **_kwargs: Any) -> str:
        disabled.append(command[command.index("--version") + 1])
        return ""

    monkeypatch.setattr(deploy, "_run_json", fake_json)
    monkeypatch.setattr(deploy, "_run_command", fake_run)

    with pytest.raises(deploy.DeploymentRefusal, match="versions changed"):
        deploy._finalize_key_vault_secret_rotation(
            vault_name="murmur-vault",
            secret_name="azure-openai-api-key",
            current_version=selected_version,
            attempts=1,
        )

    assert disabled == [previous_version]
    assert concurrent_version not in disabled


def test_legacy_firebase_secret_is_disabled_but_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_version = "d" * 32
    second_version = "e" * 32
    state = {first_version: True, second_version: True}
    disabled: list[str] = []

    def fake_json(command: Any, **_kwargs: Any) -> object:
        if "list-versions" in command:
            return [
                _secret_version(
                    deploy.LEGACY_FIREBASE_SECRET_NAME,
                    version,
                    enabled=enabled,
                )
                for version, enabled in state.items()
            ]
        if "list" in command:
            return [deploy.LEGACY_FIREBASE_SECRET_NAME]
        raise AssertionError(command)

    def fake_run(command: Any, **_kwargs: Any) -> str:
        assert "delete" not in command and "purge" not in command
        version = command[command.index("--version") + 1]
        disabled.append(version)
        state[version] = False
        return ""

    monkeypatch.setattr(deploy, "_run_json", fake_json)
    monkeypatch.setattr(deploy, "_run_command", fake_run)

    status = deploy._retire_legacy_firebase_secret(
        vault_name="murmur-vault",
        attempts=1,
    )

    assert status == "disabled_recoverable"
    assert state == {first_version: False, second_version: False}
    assert disabled == [first_version, second_version]


def test_legacy_firebase_secret_absence_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: [])

    assert (
        deploy._retire_legacy_firebase_secret(vault_name="murmur-vault", attempts=1)
        == "not_present"
    )


def test_validate_source_revision_requires_clean_exact_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        ("git", "status", "--porcelain=v1", "--untracked-files=all"): "",
        ("git", "rev-parse", "--verify", "HEAD"): f"{SHA}\n",
        ("git", "symbolic-ref", "--quiet", "--short", "HEAD"): "codex/deploy\n",
        ("git", "rev-parse", "--verify", "@{upstream}"): f"{SHA}\n",
        ("git", "config", "--get", "branch.codex/deploy.remote"): "origin\n",
        ("git", "config", "--get", "branch.codex/deploy.merge"): ("refs/heads/codex/deploy\n"),
        ("git", "ls-remote", "--exit-code", "origin", "refs/heads/codex/deploy"): (
            f"{SHA}\trefs/heads/codex/deploy\n"
        ),
    }

    def fake_run(command: Any, **_kwargs: Any) -> str:
        return responses[tuple(command)]

    monkeypatch.setattr(deploy, "_run_command", fake_run)

    revision = deploy.validate_source_revision()

    assert revision == deploy.SourceRevision(
        sha=SHA,
        branch="codex/deploy",
        remote="origin",
        remote_ref="refs/heads/codex/deploy",
    )


def test_validate_source_revision_refuses_dirty_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "?? secret.env\n")

    with pytest.raises(deploy.DeploymentRefusal, match="not clean"):
        deploy.validate_source_revision()


def test_existing_owned_resource_group_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(command: Any, **_kwargs: Any) -> str:
        commands.append(tuple(command))
        return "true\n"

    monkeypatch.setattr(deploy, "_run_command", fake_run)
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: {
            "location": "centralindia",
            "tags": {"product": "murmur", "environment": "pilot"},
        },
    )

    deploy._create_resource_group("murmur-pilot-rg", "centralindia", SHA)

    assert len(commands) == 1
    assert commands[0][:3] == ("az", "group", "exists")


def test_existing_foreign_resource_group_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "true\n")
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: {
            "location": "centralindia",
            "tags": {"product": "another-product", "environment": "pilot"},
        },
    )

    with pytest.raises(deploy.DeploymentRefusal, match="not the isolated"):
        deploy._create_resource_group("murmur-pilot-rg", "centralindia", SHA)


def test_git_build_context_contains_only_requested_committed_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, ...]] = []

    def fake_run(command: Any, **_kwargs: Any) -> str:
        observed.append(tuple(command))
        output_argument = next(item for item in command if item.startswith("--output="))
        archive_path = Path(output_argument.removeprefix("--output="))
        with tarfile.open(archive_path, mode="w") as archive:
            content = b"tracked content"
            member = tarfile.TarInfo("main.py")
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
        return ""

    monkeypatch.setattr(deploy, "_run_command", fake_run)

    with deploy._git_build_context(SHA, paths=("backend", "main.py")) as context:
        assert context.is_dir()
        assert (context / "main.py").read_bytes() == b"tracked content"
        assert not (context.parent / "context.tar").exists()
        observed_context = context

    assert not observed_context.exists()

    assert observed == [
        (
            "git",
            "archive",
            "--format=tar",
            observed[0][3],
            SHA,
            "--",
            "backend",
            "main.py",
        )
    ]


def test_backend_image_provisions_a_non_root_writable_home() -> None:
    dockerfile = (PROJECT_ROOT / "deploy" / "backend.Dockerfile").read_text(encoding="utf-8")

    assert "HOME=/home/murmur" in dockerfile
    assert "--create-home --home-dir /home/murmur" in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_backend_image_bounds_websocket_buffering_before_application_decode() -> None:
    dockerfile = (PROJECT_ROOT / "deploy" / "backend.Dockerfile").read_text(encoding="utf-8")

    assert '"--ws", "websockets-sansio"' in dockerfile
    assert '"--ws-max-size", "8192"' in dockerfile


def test_acr_build_waits_for_amd64_manifest_and_does_not_use_secret_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    observed: list[str] = []
    digest_attempts = 0

    def fake_run(command: Any, **kwargs: Any) -> str:
        nonlocal digest_attempts
        if command[1:3] == ["acr", "build"]:
            observed.extend(command)
            assert kwargs["operation"] == "build ACR image murmur-api"
            assert kwargs["cwd"] == context
            return ""
        assert command[1:4] == ["acr", "repository", "show"]
        digest_attempts += 1
        if digest_attempts == 1:
            raise deploy.DeploymentRefusal("transient registry lookup")
        return f"{DIGEST}\n"

    monkeypatch.setattr(deploy, "_run_command", fake_run)
    monkeypatch.setattr(deploy.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(deploy.secrets, "token_hex", lambda _length: "unique1234567890")

    digest = deploy._acr_build(
        registry_name="murmurregistry",
        repository="murmur-api",
        release_sha=SHA,
        dockerfile="deploy/backend.Dockerfile",
        context=context,
        build_args={"MURMUR_RELEASE_SHA": SHA},
    )

    assert "--no-logs" not in observed
    assert "--no-wait" not in observed
    assert observed[observed.index("--platform") + 1] == "linux/amd64"
    assert observed[observed.index("--image") + 1] == f"murmur-api:{SHA}-unique1234567890"
    assert observed[-1] == "."
    assert digest == DIGEST
    assert digest_attempts == 2
    assert AZURE_KEY not in observed
    assert PRIVATE_KEY not in observed


def test_firebase_runtime_authority_requires_exact_known_auth_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "runtime-token")

    def fake_request(url: str, token: str, payload: Mapping[str, object]) -> object:
        observed.update(url=url, token=token, payload=payload)
        return {"permissions": sorted(deploy.EXPECTED_FIREBASE_RUNTIME_AUTH_PERMISSIONS)}

    monkeypatch.setattr(deploy, "_resource_manager_request", fake_request)

    deploy._verify_firebase_runtime_authority(_service_account(), "firebase-project")

    assert observed["url"] == (
        "https://cloudresourcemanager.googleapis.com/v1/projects/"
        "firebase-project:testIamPermissions"
    )
    assert observed["token"] == "runtime-token"
    assert observed["payload"] == {"permissions": list(deploy.FIREBASE_AUTH_PERMISSION_UNIVERSE)}


@pytest.mark.parametrize(
    "permissions",
    (
        ["firebaseauth.users.get"],
        [
            "firebaseauth.configs.get",
            "firebaseauth.users.get",
            "firebaseauth.users.update",
        ],
    ),
)
def test_firebase_runtime_authority_rejects_missing_or_extra_permissions(
    monkeypatch: pytest.MonkeyPatch,
    permissions: list[str],
) -> None:
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "runtime-token")
    monkeypatch.setattr(
        deploy,
        "_resource_manager_request",
        lambda *_args, **_kwargs: {"permissions": permissions},
    )

    with pytest.raises(deploy.DeploymentRefusal, match="exact required Firebase"):
        deploy._verify_firebase_runtime_authority(_service_account(), "firebase-project")


def test_firebase_runtime_authority_fails_closed_when_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "runtime-token")

    def fail(*_args: Any, **_kwargs: Any) -> object:
        raise deploy.DeploymentRefusal("Google Cloud Resource Manager was unavailable")

    monkeypatch.setattr(deploy, "_resource_manager_request", fail)

    with pytest.raises(deploy.DeploymentRefusal, match="was unavailable"):
        deploy._verify_firebase_runtime_authority(_service_account(), "firebase-project")


def test_firebase_domain_update_preserves_existing_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, object]] = []
    monkeypatch.setattr(
        deploy,
        "_firebase_access_token",
        lambda account: f"{account['client_email']}-token",
    )

    def fake_request(method: str, url: str, token: str, payload: Any = None) -> dict[str, object]:
        calls.append((method, url, token, payload))
        if method == "GET":
            return {"authorizedDomains": ["localhost", "firebase-project.firebaseapp.com"]}
        return {"authorizedDomains": payload["authorizedDomains"]}

    monkeypatch.setattr(deploy, "_identity_toolkit_request", fake_request)

    result = deploy._configure_firebase_domain(
        _service_account(),
        "firebase-project",
        FRONTEND_URL,
        domain_admin_service_account=_service_account(purpose="domain-admin"),
    )

    assert result.status == "configured"
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"
    assert calls[0][2].startswith("firebase-runtime@")
    assert calls[1][2].startswith("firebase-domain-admin@")
    assert calls[1][3] == {
        "authorizedDomains": [
            "localhost",
            "firebase-project.firebaseapp.com",
            "murmur-web.example.centralindia.azurecontainerapps.io",
        ]
    }
    assert "updateMask=authorizedDomains" in calls[1][1]


def test_firebase_domain_update_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "oauth-token")
    calls: list[str] = []

    def fake_request(method: str, *_args: Any, **_kwargs: Any) -> dict[str, object]:
        calls.append(method)
        return {"authorizedDomains": ["murmur-web.example.centralindia.azurecontainerapps.io"]}

    monkeypatch.setattr(deploy, "_identity_toolkit_request", fake_request)

    result = deploy._configure_firebase_domain(_service_account(), "firebase-project", FRONTEND_URL)

    assert result.status == "already_present"
    assert calls == ["GET"]


def test_firebase_domain_update_without_deploy_admin_requires_manual_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "runtime-token")
    calls: list[str] = []

    def fake_request(method: str, *_args: Any, **_kwargs: Any) -> dict[str, object]:
        calls.append(method)
        return {"authorizedDomains": ["localhost"]}

    monkeypatch.setattr(deploy, "_identity_toolkit_request", fake_request)

    with pytest.raises(deploy.DeploymentRefusal, match="no deploy-only domain administrator"):
        deploy._configure_firebase_domain(
            _service_account(),
            "firebase-project",
            FRONTEND_URL,
        )

    assert calls == ["GET"]


def test_https_verification_checks_readiness_and_both_cors_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_urls: list[str] = []
    cors_origins: list[str] = []

    def fake_wait(url: str, **_kwargs: Any) -> dict[str, object]:
        health_urls.append(url)
        if url.endswith("/readyz"):
            return {
                "status": "ready",
                "release_sha": SHA,
                "checks": {
                    "database": "ready",
                    "firebase": "ready",
                    "azure_openai": "ready",
                },
            }
        return {
            "status": "ok",
            "release_sha": SHA,
            **({"voice_experience": "disabled"} if url.startswith(FRONTEND_URL) else {}),
        }

    def fake_http(_url: str, *, headers: Any, **_kwargs: Any) -> deploy.HttpResult:
        origin = headers["Origin"]
        cors_origins.append(origin)
        response_headers = (
            {"access-control-allow-origin": FRONTEND_URL} if origin == FRONTEND_URL else {}
        )
        return deploy.HttpResult(status=200, headers=response_headers, body=b"")

    monkeypatch.setattr(deploy, "_wait_for_json_health", fake_wait)
    monkeypatch.setattr(deploy, "_http_request", fake_http)

    deploy._verify_https(BACKEND_URL, FRONTEND_URL, expected_sha=SHA)

    assert health_urls == [
        f"{BACKEND_URL}/healthz",
        f"{BACKEND_URL}/readyz",
        f"{FRONTEND_URL}/healthz",
    ]
    assert cors_origins == [FRONTEND_URL, "https://untrusted.invalid"]
    assert all("openai" not in url for url in health_urls)


def test_inspect_backend_validates_scale_probes_local_database_and_key_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: _container_app(backend=True))

    inspected = deploy._inspect_app(
        "murmur-pilot-rg",
        "murmur-api",
        backend=True,
        expected_frontend_url=FRONTEND_URL,
    )

    assert inspected.release_sha == SHA
    assert inspected.key_vault_name == "murmur-vault"
    assert inspected.min_replicas == 1
    assert inspected.max_replicas == 1
    assert inspected.probe_types == ("Liveness", "Readiness", "Startup")


@pytest.mark.parametrize(
    ("min_replicas", "max_replicas"),
    ((0, 1), (1, 2)),
)
def test_inspect_app_refuses_scale_drift(
    monkeypatch: pytest.MonkeyPatch,
    min_replicas: int,
    max_replicas: int,
) -> None:
    app = _container_app(backend=True)
    scale = app["properties"]["template"]["scale"]  # type: ignore[index]
    scale["minReplicas"] = min_replicas  # type: ignore[index]
    scale["maxReplicas"] = max_replicas  # type: ignore[index]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(
        deploy.DeploymentRefusal,
        match="always-on single-replica bounds",
    ):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


def test_inspect_backend_refuses_inline_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: _container_app(backend=True, inline_secret=True),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="inline credential") as raised:
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )

    assert AZURE_KEY not in str(raised.value)


def test_inspect_backend_refuses_versionless_key_vault_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=True)
    secret = app["properties"]["configuration"]["secrets"][0]  # type: ignore[index]
    secret["keyVaultUrl"] = (  # type: ignore[index]
        f"https://murmur-vault.vault.azure.net/secrets/{deploy.AZURE_KEY_SECRET_NAME}"
    )
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="not version-pinned"):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


def test_inspect_backend_refuses_registry_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=True)
    registry = app["properties"]["configuration"]["registries"][0]  # type: ignore[index]
    registry["username"] = "unexpected-user"  # type: ignore[index]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="identity-based ACR pull"):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


@pytest.mark.parametrize("backend", (True, False))
def test_inspect_app_refuses_identity_available_to_main_containers(
    monkeypatch: pytest.MonkeyPatch, backend: bool
) -> None:
    app = _container_app(backend=backend)
    app["properties"]["configuration"]["identitySettings"][0]["lifecycle"] = "All"  # type: ignore[index]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="exposes its platform identity"):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api" if backend else "murmur-web",
            backend=backend,
            expected_frontend_url=FRONTEND_URL if backend else None,
        )


def test_inspect_backend_refuses_shared_database_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=True)
    container = app["properties"]["template"]["containers"][0]  # type: ignore[index]
    data_dir = next(  # type: ignore[union-attr]
        item for item in container["env"] if item.get("name") == "MURMUR_DATA_DIR"
    )
    data_dir["value"] = "/data"
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="MURMUR_DATA_DIR"):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("extra-container", "containers are outside"),
        ("legacy-runtime", "VOICE_RUNTIME"),
        ("wrong-cors", "ALLOWED_CORS_ORIGINS"),
        ("retired-env", "retired voice environment variables"),
        ("retired-secret", "Key Vault references are outside"),
    ),
)
def test_inspect_backend_refuses_websocket_topology_drift(
    monkeypatch: pytest.MonkeyPatch, mutation: str, message: str
) -> None:
    app = _container_app(backend=True)
    template = app["properties"]["template"]  # type: ignore[index]
    containers = template["containers"]  # type: ignore[index]
    api = containers[0]  # type: ignore[index]
    if mutation == "extra-container":
        containers.append({"name": "voice-worker"})  # type: ignore[union-attr]
    elif mutation == "legacy-runtime":
        next(item for item in api["env"] if item["name"] == "VOICE_RUNTIME")["value"] = (  # type: ignore[index]
            "legacy"
        )
    elif mutation == "wrong-cors":
        next(item for item in api["env"] if item["name"] == "ALLOWED_CORS_ORIGINS")[  # type: ignore[index]
            "value"
        ] = "https://wrong.example"
    elif mutation == "retired-env":
        api["env"].append({"name": "LIVEKIT_URL", "value": "wss://retired.invalid"})  # type: ignore[index]
    else:
        app["properties"]["configuration"]["secrets"].append(  # type: ignore[index]
            {
                "name": deploy.LIVEKIT_API_KEY_SECRET_NAME,
                "keyVaultUrl": (
                    "https://murmur-vault.vault.azure.net/secrets/"
                    f"{deploy.LIVEKIT_API_KEY_SECRET_NAME}/{'c' * 32}"
                ),
                "identity": BACKEND_IDENTITY_ID,
            }
        )
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match=message):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


def test_build_frontend_refuses_silent_legacy_runtime() -> None:
    with pytest.raises(deploy.DeploymentRefusal, match="NEXT_PUBLIC_VOICE_RUNTIME=disabled"):
        deploy._build_frontend(
            "murmurregistry",
            SHA,
            BACKEND_URL,
            {"NEXT_PUBLIC_VOICE_RUNTIME": "legacy"},
        )


def test_inspect_frontend_refuses_secret_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=False)
    app["properties"]["configuration"]["secrets"] = [  # type: ignore[index]
        {"name": "unexpected", "value": "not-allowed"}
    ]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="unexpectedly has secret configuration"):
        deploy._inspect_app("murmur-pilot-rg", "murmur-web", backend=False)


def test_bicep_keeps_frontend_identity_out_of_key_vault() -> None:
    foundation = (PROJECT_ROOT / "infra/azure/foundation.bicep").read_text(encoding="utf-8")
    apps = (PROJECT_ROOT / "infra/azure/apps.bicep").read_text(encoding="utf-8")

    key_vault_assignment = foundation.split("resource backendSecretReads", 1)[1].split(
        "output environmentId", 1
    )[0]
    assert "principalId: identity.properties.principalId" in key_vault_assignment
    assert "frontendIdentity.properties.principalId" not in key_vault_assignment
    assert "scope: backendSecrets[index]" in key_vault_assignment
    for secret_name in deploy.ACTIVE_SECRET_NAMES:
        assert f"'{secret_name}'" in foundation
    lock_assignment = foundation.split("resource deploymentLockAccess", 1)[1].split(
        "output environmentId", 1
    )[0]
    assert "scope: deploymentLockContainer" in lock_assignment
    assert "deploymentPrincipalObjectId" in lock_assignment.split("properties:", 1)[0]
    assert "principalId: deploymentPrincipalObjectId" in lock_assignment

    backend_app = apps.split("resource backend 'Microsoft.App/containerApps", 1)[1].split(
        "resource frontend 'Microsoft.App/containerApps", 1
    )[0]
    frontend_app = apps.split("resource frontend 'Microsoft.App/containerApps", 1)[1]
    assert "'${identity.id}': {}" in backend_app
    assert "identity: identity.id" in backend_app
    assert deploy.FIREBASE_SECRET_NAME in backend_app
    assert deploy.LEGACY_FIREBASE_SECRET_NAME not in backend_app
    assert backend_app.count("minReplicas: 1") == 1
    assert backend_app.count("maxReplicas: 1") == 1
    assert "'${frontendIdentity.id}': {}" in frontend_app
    assert "identity: frontendIdentity.id" in frontend_app
    assert "secrets:" not in frontend_app
    assert frontend_app.count("minReplicas: 1") == 1
    assert frontend_app.count("maxReplicas: 1") == 1


def test_https_verification_refuses_empty_readiness_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_wait(url: str, **_kwargs: Any) -> dict[str, object]:
        if url.endswith("/readyz"):
            return {"status": "ready", "release_sha": SHA, "checks": {}}
        return {"status": "ok", "release_sha": SHA}

    monkeypatch.setattr(deploy, "_wait_for_json_health", fake_wait)

    with pytest.raises(deploy.DeploymentRefusal, match="dependency checks"):
        deploy._verify_https(BACKEND_URL, FRONTEND_URL, expected_sha=SHA)


@pytest.mark.parametrize(("firebase_configured", "expected_result"), ((True, 0), (False, 3)))
def test_deploy_orchestrates_backend_before_frontend_and_uses_bicep_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    firebase_configured: bool,
    expected_result: int,
) -> None:
    backend_env, frontend_env = _write_inputs(tmp_path)
    inputs = deploy.load_deployment_inputs(backend_env, frontend_env)
    calls: list[tuple[str, object]] = []
    foundation = {
        "registryName": _output("murmurregistry"),
        "registryLoginServer": _output("murmurregistry.azurecr.io"),
        "keyVaultName": _output("murmur-vault"),
        "keyVaultId": _output("/subscriptions/sub/resourceGroups/rg/providers/keyvault"),
        "identityId": _output(BACKEND_IDENTITY_ID),
        "identityPrincipalId": _output(BACKEND_IDENTITY_PRINCIPAL_ID),
        "frontendIdentityId": _output(FRONTEND_IDENTITY_ID),
        "frontendIdentityPrincipalId": _output(FRONTEND_IDENTITY_PRINCIPAL_ID),
        "environmentDefaultDomain": _output("example.centralindia.azurecontainerapps.io"),
        "backendUrl": _output(BACKEND_URL),
        "frontendUrl": _output(FRONTEND_URL),
        "deploymentLockStorageAccountName": _output("murmurlockaccount"),
        "deploymentLockContainerName": _output("deployment-locks"),
        "deploymentLockBlobName": _output("azure-pilot.lock"),
    }
    app_outputs = {
        "backendUrl": _output(BACKEND_URL),
        "frontendUrl": _output(FRONTEND_URL),
        "backendLatestRevisionName": _output("murmur-api--revision"),
        **{
            parameter_name: _output(SECRET_VERSIONS[secret_name])
            for secret_name, parameter_name in deploy.SECRET_VERSION_PARAMETERS.items()
        },
    }
    deployments = iter((foundation, foundation, app_outputs))

    monkeypatch.setattr(
        deploy,
        "validate_source_revision",
        lambda: deploy.SourceRevision(SHA, "codex/deploy", "origin", "refs/heads/codex/deploy"),
    )
    monkeypatch.setattr(deploy, "load_deployment_inputs", lambda *_args: inputs)
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(
        deploy,
        "_verify_firebase_runtime_authority",
        lambda *_args: calls.append(("firebase-authority", None)),
    )
    monkeypatch.setattr(deploy, "_register_providers", lambda: calls.append(("providers", None)))
    monkeypatch.setattr(
        deploy,
        "_create_resource_group",
        lambda *args: calls.append(("group", args)),
    )

    def fake_deployment_outputs(**kwargs: Any) -> Mapping[str, object]:
        calls.append(("bicep", kwargs))
        return next(deployments)

    monkeypatch.setattr(deploy, "_deployment_outputs", fake_deployment_outputs)
    monkeypatch.setattr(
        deploy,
        "_current_principal",
        lambda: (BACKEND_IDENTITY_PRINCIPAL_ID, "User"),
    )

    class FakeLease:
        def assert_healthy(self) -> None:
            calls.append(("lease-healthy", None))

    @deploy.contextmanager
    def fake_lease(**_kwargs: Any) -> Any:
        calls.append(("lease", None))
        yield FakeLease()

    monkeypatch.setattr(deploy, "_deployment_blob_lease", fake_lease)
    assignment_id = (
        "/subscriptions/sub/providers/Microsoft.Authorization/roleAssignments/"
        "55555555-5555-5555-5555-555555555555"
    )

    assignment = deploy.RoleAssignmentMetadata(
        id=assignment_id,
        scope=KEY_VAULT_ID,
        principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
        role_definition_id=deploy.KEY_VAULT_SECRETS_OFFICER_ROLE_ID,
        description=deploy.KEY_VAULT_WRITER_DESCRIPTION,
    )

    def fake_grant(value: str, **_kwargs: Any) -> deploy.RoleAssignmentMetadata:
        calls.append(("kv-role", value))
        return assignment

    monkeypatch.setattr(deploy, "_grant_key_vault_write", fake_grant)
    monkeypatch.setattr(
        deploy,
        "_revoke_key_vault_write",
        lambda vault, value, **_kwargs: calls.append(("kv-role-revoked", (vault, value))),
    )

    def fake_write_secret(**kwargs: Any) -> str:
        calls.append(("secret", kwargs["secret_name"]))
        return SECRET_VERSIONS[kwargs["secret_name"]]

    monkeypatch.setattr(deploy, "_write_key_vault_secret", fake_write_secret)
    monkeypatch.setattr(deploy, "_verify_backend_key_vault_boundary", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_retire_legacy_backend_vault_read", lambda **_kwargs: None)
    retained_versions = {deploy.LIVEKIT_API_KEY_SECRET_NAME: "c" * 32}
    monkeypatch.setattr(
        deploy,
        "_existing_retired_voice_secret_versions",
        lambda **_kwargs: retained_versions,
    )
    monkeypatch.setattr(deploy, "_assert_no_key_vault_writer", lambda _vault: None)
    monkeypatch.setattr(
        deploy,
        "_build_backend",
        lambda registry, sha: calls.append(("backend-build", (registry, sha))) or DIGEST,
    )
    monkeypatch.setattr(
        deploy,
        "_build_frontend",
        lambda registry, sha, backend_url, frontend_public: (
            calls.append(("frontend-build", (registry, sha, backend_url, frontend_public)))
            or FRONTEND_DIGEST
        ),
    )

    def fake_configure_firebase(*_args: Any, **_kwargs: Any) -> deploy.FirebaseDomainResult:
        if not firebase_configured:
            raise deploy.DeploymentRefusal("IAM unavailable")
        return deploy.FirebaseDomainResult(
            "configured", "murmur-web.example.centralindia.azurecontainerapps.io"
        )

    monkeypatch.setattr(deploy, "_configure_firebase_domain", fake_configure_firebase)
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)

    def fake_verify_live(**kwargs: Any) -> tuple[deploy.AppInspection, deploy.AppInspection]:
        calls.append(("verify-live", kwargs))
        return backend_inspection, frontend_inspection

    monkeypatch.setattr(deploy, "verify_live", fake_verify_live)
    monkeypatch.setattr(
        deploy,
        "_verify_old_revisions_inactive",
        lambda **_kwargs: calls.append(("old-revisions-inactive", None)),
    )
    monkeypatch.setattr(
        deploy,
        "_retire_backend_voice_secret_access",
        lambda **_kwargs: calls.append(("voice-secret-access-retired", None)),
    )
    monkeypatch.setattr(
        deploy,
        "_finalize_key_vault_secret_rotation",
        lambda **kwargs: calls.append(("finalize-secret", kwargs["secret_name"])),
    )
    monkeypatch.setattr(
        deploy,
        "_retire_voice_key_vault_secrets",
        lambda **_kwargs: (
            calls.append(("voice-secrets-retired", None))
            or {name: "disabled_recoverable" for name in deploy.RETIRED_VOICE_SECRET_NAMES}
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_retire_legacy_firebase_secret",
        lambda **_kwargs: calls.append(("legacy-secret-retired", None)) or "disabled_recoverable",
    )
    monkeypatch.setattr(deploy, "_print_verification", lambda *_args: None)

    args = argparse.Namespace(
        resource_group="murmur-pilot-rg",
        backend_app="murmur-api",
        frontend_app="murmur-web",
        location="centralindia",
        backend_env=backend_env,
        frontend_env=frontend_env,
        health_timeout_seconds=1,
        subscription_id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
    )
    assert deploy.deploy(args) == expected_result

    labels = [item[0] for item in calls]
    assert labels.index("firebase-authority") < labels.index("group")
    assert labels.index("backend-build") < labels.index("frontend-build")
    assert labels.index("verify-live") < labels.index("old-revisions-inactive")
    assert labels.index("old-revisions-inactive") < labels.index("finalize-secret")
    assert labels.index("finalize-secret") < labels.index("legacy-secret-retired")
    assert labels.count("finalize-secret") == len(deploy.ACTIVE_SECRET_NAMES)
    assert "voice-secret-access-retired" not in labels
    assert "voice-secrets-retired" not in labels
    assert labels.count("kv-role") == 1
    assert labels.count("kv-role-revoked") == 1
    assert "restart" not in labels
    frontend_build = next(item[1] for item in calls if item[0] == "frontend-build")
    assert frontend_build[2] == BACKEND_URL
    apps_parameters = next(
        item[1]["parameters"]
        for item in calls
        if item[0] == "bicep" and item[1]["deployment_name"] == deploy.APPS_DEPLOYMENT
    )
    assert apps_parameters["releaseSha"] == SHA
    assert apps_parameters["backendImage"].endswith(f"@{DIGEST}")
    assert apps_parameters["frontendImage"].endswith(f"@{FRONTEND_DIGEST}")
    assert json.loads(apps_parameters["retainedRetiredVoiceSecretVersions"]) == retained_versions
    verify_arguments = next(item[1] for item in calls if item[0] == "verify-live")
    assert verify_arguments["expected_registry_server"] == "murmurregistry.azurecr.io"
    assert verify_arguments["expected_backend_image_digest"] == DIGEST
    assert verify_arguments["expected_frontend_image_digest"] == FRONTEND_DIGEST
    assert (
        verify_arguments["expected_retained_retired_voice_secret_versions"]
        == retained_versions
    )
    assert apps_parameters["azureOpenAiSecretVersion"] == AZURE_KEY_VERSION
    assert apps_parameters["firebaseRuntimeSecretVersion"] == FIREBASE_VERSION
    for secret_name, parameter_name in deploy.SECRET_VERSION_PARAMETERS.items():
        assert apps_parameters[parameter_name] == SECRET_VERSIONS[secret_name]
    serialized_parameters = json.dumps(apps_parameters)
    for secret in (AZURE_KEY, PRIVATE_KEY, RUNTIME_PRIVATE_KEY):
        assert secret not in serialized_parameters
    foundation_parameters = next(
        item[1]["parameters"]
        for item in calls
        if item[0] == "bicep" and item[1]["deployment_name"] == deploy.FOUNDATION_DEPLOYMENT
    )
    assert foundation_parameters == {
        "location": "centralindia",
        "backendAppName": "murmur-api",
        "frontendAppName": "murmur-web",
        "deploymentPrincipalObjectId": BACKEND_IDENTITY_PRINCIPAL_ID,
        "deploymentPrincipalType": "User",
        "grantBackendSecretRead": False,
    }
    output = capsys.readouterr().out
    assert "websocket_canary_required: true" in output
    assert "voice_retirement_status: pending_finalization" in output


def test_verify_live_performs_only_metadata_and_health_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    observed: list[str] = []
    boundary: dict[str, str] = {}
    monkeypatch.setattr(
        deploy, "_validate_azure_session", lambda **_kwargs: observed.append("azure")
    )
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_verify_key_vault_metadata",
        lambda _vault: observed.append("key-vault-metadata") or KEY_VAULT_ID,
    )
    monkeypatch.setattr(
        deploy,
        "_inspect_managed_identity",
        lambda _identity: FRONTEND_IDENTITY_PRINCIPAL_ID,
    )
    monkeypatch.setattr(
        deploy,
        "_verify_frontend_key_vault_boundary",
        lambda **kwargs: (boundary.update(kwargs), observed.append("identity-boundary")),
    )
    monkeypatch.setattr(
        deploy,
        "_verify_backend_key_vault_boundary",
        lambda **_kwargs: observed.append("backend-boundary"),
    )
    monkeypatch.setattr(
        deploy,
        "_verify_https",
        lambda *_args, **_kwargs: observed.append("https"),
    )

    live_backend, live_frontend = deploy.verify_live(
        resource_group="murmur-pilot-rg",
        backend_app="murmur-api",
        frontend_app="murmur-web",
        expected_backend_identity_id=BACKEND_IDENTITY_ID,
        expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
        expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
        expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
        key_vault_id=KEY_VAULT_ID,
        subscription_id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
        expected_secret_versions=SECRET_VERSIONS,
        **EXPECTED_IMAGE_ARGS,
    )

    assert live_backend == backend_inspection
    assert live_frontend == frontend_inspection
    assert observed == [
        "azure",
        "key-vault-metadata",
        "identity-boundary",
        "backend-boundary",
        "https",
    ]
    assert boundary == {
        "frontend_principal_id": FRONTEND_IDENTITY_PRINCIPAL_ID,
        "key_vault_id": KEY_VAULT_ID,
    }


def test_old_revision_check_requires_only_verified_revision_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def revisions(command: list[str], **_kwargs: Any) -> object:
        assert "--all" in command
        return [
            {
                "name": "murmur-api--current",
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            },
            {
                "name": "murmur-api--previous",
                "active": False,
                "replicas": 0,
                "healthState": None,
                "provisioningState": "Provisioned",
                "runningState": "Stopped",
            },
        ]

    monkeypatch.setattr(
        deploy,
        "_run_json",
        revisions,
    )

    deploy._verify_old_revisions_inactive(
        resource_group="murmur-pilot-rg",
        app_name="murmur-api",
        current_revision="murmur-api--current",
    )


def test_old_revision_check_accepts_healthy_current_revision_at_max_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: [
            {
                "name": "murmur-api--current",
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "RunningAtMaxScale",
            },
            {
                "name": "murmur-api--previous",
                "active": False,
                "replicas": 0,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Stopped",
            },
        ],
    )

    deploy._verify_old_revisions_inactive(
        resource_group="murmur-pilot-rg",
        app_name="murmur-api",
        current_revision="murmur-api--current",
        attempts=1,
    )


def test_old_revision_check_refuses_an_active_previous_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: [
            {
                "name": "murmur-api--current",
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            },
            {
                "name": "murmur-api--previous",
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            },
        ],
    )

    with pytest.raises(deploy.DeploymentRefusal, match="safe terminal cutover"):
        deploy._verify_old_revisions_inactive(
            resource_group="murmur-pilot-rg",
            app_name="murmur-api",
            current_revision="murmur-api--current",
            attempts=1,
        )


def test_old_revision_check_refuses_inactive_revision_with_replicas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: [
            {
                "name": "murmur-api--current",
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            },
            {
                "name": "murmur-api--previous",
                "active": False,
                "replicas": 1,
                "healthState": None,
                "provisioningState": "Deprovisioning",
                "runningState": "Running",
            },
        ],
    )

    with pytest.raises(deploy.DeploymentRefusal, match="safe terminal cutover"):
        deploy._verify_old_revisions_inactive(
            resource_group="murmur-pilot-rg",
            app_name="murmur-api",
            current_revision="murmur-api--current",
            attempts=1,
        )


def test_verify_live_rejects_latest_revision_that_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = dataclasses.replace(
        _inspection("murmur-api", BACKEND_URL, backend=True),
        latest_ready_revision="murmur-api--previous",
    )
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )
    monkeypatch.setattr(deploy, "_verify_key_vault_metadata", lambda _vault: KEY_VAULT_ID)
    monkeypatch.setattr(
        deploy,
        "_inspect_managed_identity",
        lambda _identity: FRONTEND_IDENTITY_PRINCIPAL_ID,
    )
    monkeypatch.setattr(deploy, "_verify_frontend_key_vault_boundary", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_backend_key_vault_boundary", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_https", lambda *_args, **_kwargs: None)

    with pytest.raises(deploy.DeploymentRefusal, match="latest revision is not reported ready"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
            expected_secret_versions=SECRET_VERSIONS,
            **EXPECTED_IMAGE_ARGS,
        )


def test_verify_live_rejects_shared_frontend_and_backend_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = dataclasses.replace(
        _inspection("murmur-web", FRONTEND_URL, backend=False),
        identity_id=backend_inspection.identity_id,
    )
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="separate managed identities"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
            expected_secret_versions=SECRET_VERSIONS,
            **EXPECTED_IMAGE_ARGS,
        )


def test_verify_live_rejects_unexpected_distinct_frontend_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = dataclasses.replace(
        _inspection("murmur-web", FRONTEND_URL, backend=False),
        identity_id="/subscriptions/sub/resourceGroups/rg/providers/rogue",
    )
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="foundation managed identity"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
            expected_secret_versions=SECRET_VERSIONS,
            **EXPECTED_IMAGE_ARGS,
        )


@pytest.mark.parametrize(
    "protected_secret",
    (
        *deploy.ACTIVE_SECRET_NAMES,
        *deploy.RETIRED_VOICE_SECRET_NAMES,
        deploy.LEGACY_FIREBASE_SECRET_NAME,
    ),
)
def test_frontend_key_vault_boundary_rejects_secret_read_role_at_each_secret_scope(
    monkeypatch: pytest.MonkeyPatch,
    protected_secret: str,
) -> None:
    role_id = "4633458b-17de-408a-b874-0445c86b69e6"
    observed_scopes: list[str] = []

    def fake_run_json(command: list[str], **_kwargs: Any) -> object:
        if command[1:4] == ["role", "assignment", "list"]:
            scope = command[command.index("--scope") + 1]
            observed_scopes.append(scope)
            assert "--include-inherited" in command
            assert "--include-groups" in command
            if scope.endswith(f"/secrets/{protected_secret}"):
                return [
                    {
                        "scope": scope,
                        "roleDefinitionId": (
                            "/subscriptions/sub/providers/Microsoft.Authorization/"
                            f"roleDefinitions/{role_id}"
                        ),
                    }
                ]
            return []
        if command[1:4] == ["role", "definition", "list"]:
            return [
                {
                    "actions": [],
                    "notActions": [],
                    "dataActions": ["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
                    "notDataActions": [],
                }
            ]
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_json", fake_run_json)

    with pytest.raises(deploy.DeploymentRefusal, match="access or grant Key Vault secrets"):
        deploy._verify_frontend_key_vault_boundary(
            frontend_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )

    ordered = (
        *deploy.ACTIVE_SECRET_NAMES,
        *deploy.RETIRED_VOICE_SECRET_NAMES,
        deploy.LEGACY_FIREBASE_SECRET_NAME,
    )
    assert observed_scopes == [
        f"{KEY_VAULT_ID}/secrets/{secret_name}"
        for secret_name in ordered[: ordered.index(protected_secret) + 1]
    ]


@pytest.mark.parametrize("vault_wide", (False, True))
def test_backend_key_vault_boundary_requires_exact_secret_scopes(
    monkeypatch: pytest.MonkeyPatch,
    vault_wide: bool,
) -> None:
    def fake_run_json(command: list[str], **_kwargs: Any) -> object:
        if command[1:4] == ["role", "assignment", "list"]:
            scope = command[command.index("--scope") + 1]
            if any(
                scope.endswith(f"/secrets/{secret_name}")
                for secret_name in (
                    *deploy.RETIRED_VOICE_SECRET_NAMES,
                    deploy.LEGACY_FIREBASE_SECRET_NAME,
                )
            ):
                return []
            return [
                {
                    "scope": KEY_VAULT_ID if vault_wide else scope,
                    "roleDefinitionId": deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
                }
            ]
        if command[1:4] == ["role", "definition", "list"]:
            return [
                {
                    "actions": [],
                    "notActions": [],
                    "dataActions": ["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
                    "notDataActions": [],
                }
            ]
        raise AssertionError(command)

    monkeypatch.setattr(deploy, "_run_json", fake_run_json)
    if vault_wide:
        with pytest.raises(deploy.DeploymentRefusal, match="intended secret scope"):
            deploy._verify_backend_key_vault_boundary(
                backend_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
                key_vault_id=KEY_VAULT_ID,
            )
    else:
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )


def test_verify_live_rejects_stale_frontend_principal_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_inspect_managed_identity",
        lambda _identity: "66666666-6666-6666-6666-666666666666",
    )

    with pytest.raises(deploy.DeploymentRefusal, match="principal does not match"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
            expected_secret_versions=SECRET_VERSIONS,
            **EXPECTED_IMAGE_ARGS,
        )


def test_verify_live_rejects_stale_key_vault_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend, **_kwargs: (
            backend_inspection if backend else frontend_inspection
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_inspect_managed_identity",
        lambda _identity: FRONTEND_IDENTITY_PRINCIPAL_ID,
    )
    monkeypatch.setattr(
        deploy,
        "_verify_key_vault_metadata",
        lambda _vault: f"{KEY_VAULT_ID}-different",
    )

    with pytest.raises(deploy.DeploymentRefusal, match="Key Vault does not match"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_IDENTITY_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_IDENTITY_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id=SUBSCRIPTION_ID,
            tenant_id=TENANT_ID,
            expected_secret_versions=SECRET_VERSIONS,
            **EXPECTED_IMAGE_ARGS,
        )


def test_main_redacts_unexpected_exception_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_args: argparse.Namespace) -> int:
        raise RuntimeError(f"unexpected {AZURE_KEY} {PRIVATE_KEY}")

    monkeypatch.setattr(deploy, "verify", fail)

    assert (
        deploy.main(
            [
                "verify",
                "--health-timeout-seconds",
                "1",
                "--subscription-id",
                SUBSCRIPTION_ID,
                "--tenant-id",
                TENANT_ID,
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert AZURE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.err
    assert "no credential values were printed" in captured.err

"""No-LiveKit production-deployer contract tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "deploy_azure_no_livekit", PROJECT_ROOT / "scripts" / "deploy_azure.py"
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
deploy = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = deploy
MODULE_SPEC.loader.exec_module(deploy)

SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = f"sha256:{'a' * 64}"
BACKEND_IDENTITY_ID = "/subscriptions/sub/resourceGroups/rg/providers/backend-identity"
BACKEND_PRINCIPAL_ID = "11111111-1111-1111-1111-111111111111"
KEY_VAULT_ID = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/vault"
FRONTEND_URL = "https://murmur-web.example.centralindia.azurecontainerapps.io"
VERSIONS = {
    deploy.AZURE_KEY_SECRET_NAME: "a" * 32,
    deploy.FIREBASE_SECRET_NAME: "b" * 32,
}


def _service_account() -> dict[str, str]:
    return {
        "type": "service_account",
        "project_id": "firebase-project",
        "private_key": "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\n",
        "client_email": "runtime@firebase-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    account = tmp_path / "firebase-runtime.json"
    account.write_text(json.dumps(_service_account()), encoding="utf-8")
    account.chmod(0o600)
    backend = tmp_path / ".env"
    backend.write_text(
        "\n".join(
            (
                "AZURE_OPENAI_API_KEY=azure-key",
                "AZURE_OPENAI_ENDPOINT=https://murmur.services.ai.azure.com",
                "AZURE_OPENAI_DEPLOYMENT=murmur-model",
                "VOICE_RUNTIME=websocket_v1",
                "FIREBASE_PROJECT_ID=firebase-project",
                f"FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH={account}",
            )
        ),
        encoding="utf-8",
    )
    backend.chmod(0o600)
    frontend = tmp_path / ".env.local"
    frontend.write_text(
        "\n".join(
            (
                "NEXT_PUBLIC_FIREBASE_API_KEY=browser-key",
                "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=firebase-project.firebaseapp.com",
                "NEXT_PUBLIC_FIREBASE_PROJECT_ID=firebase-project",
                "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=firebase-project.appspot.com",
                "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=123",
                "NEXT_PUBLIC_FIREBASE_APP_ID=1:123:web:abc",
                "NEXT_PUBLIC_VOICE_RUNTIME=voice_v2",
            )
        ),
        encoding="utf-8",
    )
    return backend, frontend


def _backend_app() -> dict[str, object]:
    env = [
        {"name": "MURMUR_RELEASE_SHA", "value": SHA},
        {"name": "MURMUR_DATA_DIR", "value": "/home/murmur/data"},
        {"name": "MURMUR_SQLITE_JOURNAL_MODE", "value": "WAL"},
        {"name": "ALLOWED_CORS_ORIGINS", "value": FRONTEND_URL},
        {"name": "VOICE_RUNTIME", "value": "websocket_v1"},
        {"name": "AZURE_OPENAI_API_KEY", "secretRef": deploy.AZURE_KEY_SECRET_NAME},
        {"name": "FIREBASE_SERVICE_ACCOUNT_JSON", "secretRef": deploy.FIREBASE_SECRET_NAME},
    ]
    secrets = [
        {
            "name": name,
            "keyVaultUrl": f"https://murmur-vault.vault.azure.net/secrets/{name}/{version}",
            "identity": BACKEND_IDENTITY_ID,
        }
        for name, version in VERSIONS.items()
    ]
    probes = [
        {"type": "Startup", "httpGet": {"path": "/healthz", "port": 8000}},
        {"type": "Liveness", "httpGet": {"path": "/healthz", "port": 8000}},
        {"type": "Readiness", "httpGet": {"path": "/readyz", "port": 8000}},
    ]
    return {
        "name": "murmur-api",
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {BACKEND_IDENTITY_ID: {}},
        },
        "properties": {
            "provisioningState": "Succeeded",
            "runningStatus": "Running",
            "latestRevisionName": "murmur-api--revision",
            "latestReadyRevisionName": "murmur-api--revision",
            "configuration": {
                "activeRevisionsMode": "Single",
                "identitySettings": [{"identity": BACKEND_IDENTITY_ID, "lifecycle": "None"}],
                "registries": [
                    {
                        "server": "murmurregistry.azurecr.io",
                        "identity": BACKEND_IDENTITY_ID,
                        "username": "",
                        "passwordSecretRef": "",
                    }
                ],
                "ingress": {
                    "fqdn": "murmur-api.example.centralindia.azurecontainerapps.io",
                    "external": True,
                    "allowInsecure": False,
                    "targetPort": 8000,
                },
                "secrets": secrets,
            },
            "template": {
                "scale": {"minReplicas": 1, "maxReplicas": 1},
                "containers": [
                    {
                        "name": "api",
                        "image": f"murmurregistry.azurecr.io/murmur-api@{DIGEST}",
                        "env": env,
                        "probes": probes,
                        "volumeMounts": [
                            {"mountPath": "/home/murmur/data", "volumeName": "murmur-data"}
                        ],
                    }
                ],
                "volumes": [{"name": "murmur-data", "storageType": "EmptyDir"}],
            },
        },
    }


def test_active_inputs_and_parameters_contain_no_voice_provider_credentials(
    tmp_path: Path,
) -> None:
    backend, frontend = _write_inputs(tmp_path)
    inputs = deploy.load_deployment_inputs(backend, frontend)

    assert deploy.ACTIVE_SECRET_NAMES == (
        deploy.AZURE_KEY_SECRET_NAME,
        deploy.FIREBASE_SECRET_NAME,
    )
    assert set(deploy.SECRET_VERSION_PARAMETERS) == set(deploy.ACTIVE_SECRET_NAMES)
    assert set(inputs.secret_payloads()) == set(deploy.ACTIVE_SECRET_NAMES)
    assert not any(
        hasattr(inputs, name)
        for name in (
            "livekit_url",
            "livekit_api_key",
            "voice_v2_signing_secret",
            "deepgram_key",
            "groq_api_key",
            "elevenlabs_api_key",
        )
    )
    parameters = deploy._apps_deployment_parameters(
        location="centralindia",
        backend_app="murmur-api",
        frontend_app="murmur-web",
        backend_image=f"registry/murmur-api@{DIGEST}",
        frontend_image=f"registry/murmur-web@{DIGEST}",
        release_sha=SHA,
        inputs=inputs,
        secret_versions=VERSIONS,
    )
    assert set(parameters) == {
        "location",
        "backendAppName",
        "frontendAppName",
        "backendImage",
        "frontendImage",
        "releaseSha",
        "azureOpenAiEndpoint",
        "azureOpenAiDeployment",
        "firebaseProjectId",
        "azureOpenAiSecretVersion",
        "firebaseRuntimeSecretVersion",
    }


def test_inputs_require_websocket_runtime(tmp_path: Path) -> None:
    backend, frontend = _write_inputs(tmp_path)
    backend.write_text(
        backend.read_text(encoding="utf-8").replace(
            "VOICE_RUNTIME=websocket_v1", "VOICE_RUNTIME=livekit_v2"
        ),
        encoding="utf-8",
    )

    with pytest.raises(deploy.DeploymentRefusal, match="websocket_v1"):
        deploy.load_deployment_inputs(backend, frontend)


def test_inspector_accepts_only_api_websocket_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: _backend_app())

    inspection = deploy._inspect_app(
        "murmur-pilot-rg",
        "murmur-api",
        backend=True,
        expected_frontend_url=FRONTEND_URL,
    )

    assert dict(inspection.key_vault_secret_versions) == VERSIONS


@pytest.mark.parametrize("drift", ("worker", "livekit_env", "provider_secret", "cors"))
def test_inspector_rejects_retired_voice_or_origin_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    app = _backend_app()
    properties = app["properties"]
    assert isinstance(properties, dict)
    template = properties["template"]
    configuration = properties["configuration"]
    assert isinstance(template, dict) and isinstance(configuration, dict)
    containers = template["containers"]
    assert isinstance(containers, list) and isinstance(containers[0], dict)
    env = containers[0]["env"]
    assert isinstance(env, list)
    if drift == "worker":
        containers.append({"name": "voice-worker"})
    elif drift == "livekit_env":
        env.append({"name": "LIVEKIT_URL", "value": "wss://example.livekit.cloud"})
    elif drift == "provider_secret":
        secrets = configuration["secrets"]
        assert isinstance(secrets, list)
        secrets.append(
            {
                "name": deploy.DEEPGRAM_API_KEY_SECRET_NAME,
                "keyVaultUrl": (
                    "https://murmur-vault.vault.azure.net/secrets/"
                    f"{deploy.DEEPGRAM_API_KEY_SECRET_NAME}/{'c' * 32}"
                ),
                "identity": BACKEND_IDENTITY_ID,
            }
        )
    else:
        next(item for item in env if item["name"] == "ALLOWED_CORS_ORIGINS")["value"] = (
            "https://wrong.example.azurecontainerapps.io"
        )
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
        )


def test_retired_voice_scope_grant_is_revoked_and_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_name = deploy.RETIRED_VOICE_SECRET_NAMES[0]
    scope = f"{KEY_VAULT_ID}/secrets/{secret_name}"
    assignment = deploy.RoleAssignmentMetadata(
        id=f"{scope}/providers/Microsoft.Authorization/roleAssignments/assignment",
        scope=scope,
        principal_id=BACKEND_PRINCIPAL_ID,
        role_definition_id=deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
        description=None,
    )
    present = {secret_name: True}
    deleted: list[str] = []

    def direct(*, secret_name: str, **_kwargs: Any) -> tuple[Any, ...]:
        return (assignment,) if present.get(secret_name, False) else ()

    def run(command: list[str], **_kwargs: Any) -> str:
        deleted.append(command[command.index("--ids") + 1])
        present[secret_name] = False
        return ""

    monkeypatch.setattr(deploy, "_direct_backend_secret_assignments", direct)
    monkeypatch.setattr(
        deploy,
        "_role_permissions",
        lambda _role: (
            {
                "actions": [],
                "notActions": [],
                "dataActions": ["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
                "notDataActions": [],
            },
        ),
    )
    monkeypatch.setattr(deploy, "_key_vault_assignments_at_secret", lambda **_kwargs: ())
    monkeypatch.setattr(deploy, "_run_command", run)

    deploy._retire_backend_voice_secret_access(
        backend_principal_id=BACKEND_PRINCIPAL_ID,
        key_vault_id=KEY_VAULT_ID,
        mutation_guard=lambda: None,
        attempts=1,
    )

    assert deleted == [assignment.id]


def test_backend_boundary_rejects_retired_voice_secret_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def assignments(*, secret_name: str, **_kwargs: Any) -> tuple[tuple[str, str], ...]:
        if secret_name in deploy.ACTIVE_SECRET_NAMES:
            scope = f"{KEY_VAULT_ID}/secrets/{secret_name}"
            return ((scope, deploy.KEY_VAULT_SECRETS_USER_ROLE_ID),)
        if secret_name == deploy.RETIRED_VOICE_SECRET_NAMES[0]:
            scope = f"{KEY_VAULT_ID}/secrets/{secret_name}"
            return ((scope, deploy.KEY_VAULT_SECRETS_USER_ROLE_ID),)
        return ()

    monkeypatch.setattr(deploy, "_key_vault_assignments_at_secret", assignments)
    monkeypatch.setattr(
        deploy,
        "_role_permissions",
        lambda _role: (
            {
                "actions": [],
                "notActions": [],
                "dataActions": ["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
                "notDataActions": [],
            },
        ),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="retired Key Vault secret"):
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )

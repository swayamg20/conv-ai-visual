"""No-LiveKit production-deployer contract tests."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import replace
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
FRONTEND_DIGEST = f"sha256:{'b' * 64}"
BACKEND_IDENTITY_ID = "/subscriptions/sub/resourceGroups/rg/providers/backend-identity"
BACKEND_PRINCIPAL_ID = "11111111-1111-1111-1111-111111111111"
FRONTEND_IDENTITY_ID = "/subscriptions/sub/resourceGroups/rg/providers/frontend-identity"
FRONTEND_PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"
KEY_VAULT_ID = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/vault"
FRONTEND_URL = "https://murmur-web.example.centralindia.azurecontainerapps.io"
VERSIONS = {
    deploy.AZURE_KEY_SECRET_NAME: "a" * 32,
    deploy.FIREBASE_SECRET_NAME: "b" * 32,
}
RETAINED_VERSIONS = {deploy.LIVEKIT_API_KEY_SECRET_NAME: "c" * 32}
LEGACY_ASSIGNMENT_ID = (
    f"{KEY_VAULT_ID}/providers/Microsoft.Authorization/roleAssignments/"
    "33333333-3333-3333-3333-333333333333"
)


def _role_assignment(
    *,
    scope: str,
    principal_id: str = BACKEND_PRINCIPAL_ID,
    role_definition_id: str = deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
    assignment_id: str | None = None,
    condition: str | None = None,
    condition_version: str | None = None,
) -> deploy.RoleAssignmentMetadata:
    return deploy.RoleAssignmentMetadata(
        id=assignment_id
        or (
            f"{scope}/providers/Microsoft.Authorization/roleAssignments/"
            "44444444-4444-4444-4444-444444444444"
        ),
        scope=scope,
        principal_id=principal_id,
        role_definition_id=role_definition_id,
        description=None,
        condition=condition,
        condition_version=condition_version,
    )


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
                "NEXT_PUBLIC_VOICE_RUNTIME=disabled",
            )
        ),
        encoding="utf-8",
    )
    return backend, frontend


def _backend_app() -> dict[str, object]:
    plain_values = {
        name: "test-value"
        for name in deploy.BACKEND_ENV_NAMES
        if name not in {"AZURE_OPENAI_API_KEY", "FIREBASE_SERVICE_ACCOUNT_JSON"}
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
    env = [{"name": name, "value": value} for name, value in plain_values.items()]
    env.extend(
        (
            {"name": "AZURE_OPENAI_API_KEY", "secretRef": deploy.AZURE_KEY_SECRET_NAME},
            {"name": "FIREBASE_SERVICE_ACCOUNT_JSON", "secretRef": deploy.FIREBASE_SECRET_NAME},
        )
    )
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


def _inspection(name: str, *, backend: bool, digest: str) -> deploy.AppInspection:
    return deploy.AppInspection(
        name=name,
        url=(
            "https://murmur-api.example.centralindia.azurecontainerapps.io"
            if backend
            else FRONTEND_URL
        ),
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
        key_vault_secret_versions=(tuple(VERSIONS.items()) if backend else ()),
        identity_id=BACKEND_IDENTITY_ID if backend else FRONTEND_IDENTITY_ID,
    )


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
        "retainedRetiredVoiceSecretVersions",
        "azureOpenAiSecretVersion",
        "firebaseRuntimeSecretVersion",
    }
    assert parameters["retainedRetiredVoiceSecretVersions"] == "{}"


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


def test_existing_alias_discovery_uses_list_then_exact_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _backend_app()
    properties = app["properties"]
    assert isinstance(properties, dict)
    configuration = properties["configuration"]
    assert isinstance(configuration, dict)
    secrets = configuration["secrets"]
    assert isinstance(secrets, list)
    secrets.append(
        {
            "name": deploy.LIVEKIT_API_KEY_SECRET_NAME,
            "keyVaultUrl": (
                "https://murmur-vault.vault.azure.net/secrets/"
                f"{deploy.LIVEKIT_API_KEY_SECRET_NAME}/{RETAINED_VERSIONS[deploy.LIVEKIT_API_KEY_SECRET_NAME]}"
            ),
            "identity": BACKEND_IDENTITY_ID,
        }
    )
    commands: list[str] = []

    def run(command: list[str], **_kwargs: Any) -> object:
        commands.append(command[2])
        return ["murmur-api"] if command[2] == "list" else app

    monkeypatch.setattr(deploy, "_run_json", run)

    assert deploy._existing_retired_voice_secret_versions(
        resource_group="murmur-pilot-rg",
        backend_app="murmur-api",
        expected_identity_id=BACKEND_IDENTITY_ID,
        expected_key_vault_name="murmur-vault",
    ) == RETAINED_VERSIONS
    assert commands == ["list", "show"]


def test_existing_alias_discovery_allows_a_missing_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: ["murmur-web"])

    assert deploy._existing_retired_voice_secret_versions(
        resource_group="murmur-pilot-rg",
        backend_app="murmur-api",
        expected_identity_id=BACKEND_IDENTITY_ID,
        expected_key_vault_name="murmur-vault",
    ) == {}


@pytest.mark.parametrize("drift", ("identity", "vault", "unpinned", "inline"))
def test_existing_alias_discovery_rejects_ambiguous_rollback_aliases(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    app = _backend_app()
    properties = app["properties"]
    assert isinstance(properties, dict)
    configuration = properties["configuration"]
    assert isinstance(configuration, dict)
    secrets = configuration["secrets"]
    assert isinstance(secrets, list)
    alias: dict[str, str] = {
        "name": deploy.LIVEKIT_API_KEY_SECRET_NAME,
        "keyVaultUrl": (
            "https://murmur-vault.vault.azure.net/secrets/"
            f"{deploy.LIVEKIT_API_KEY_SECRET_NAME}/{'c' * 32}"
        ),
        "identity": BACKEND_IDENTITY_ID,
    }
    if drift == "identity":
        alias["identity"] = FRONTEND_IDENTITY_ID
    elif drift == "vault":
        alias["keyVaultUrl"] = alias["keyVaultUrl"].replace("murmur-vault", "other-vault")
    elif drift == "unpinned":
        alias["keyVaultUrl"] = alias["keyVaultUrl"].rsplit("/", 1)[0]
    else:
        alias["value"] = "must-not-be-accepted"
    secrets.append(alias)
    responses = iter((["murmur-api"], app))
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(deploy.DeploymentRefusal):
        deploy._existing_retired_voice_secret_versions(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            expected_identity_id=BACKEND_IDENTITY_ID,
            expected_key_vault_name="murmur-vault",
        )


def test_inspector_accepts_exact_unreferenced_retained_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _backend_app()
    properties = app["properties"]
    assert isinstance(properties, dict)
    configuration = properties["configuration"]
    assert isinstance(configuration, dict)
    secrets = configuration["secrets"]
    assert isinstance(secrets, list)
    secrets.append(
        {
            "name": deploy.LIVEKIT_API_KEY_SECRET_NAME,
            "keyVaultUrl": (
                "https://murmur-vault.vault.azure.net/secrets/"
                f"{deploy.LIVEKIT_API_KEY_SECRET_NAME}/{'c' * 32}"
            ),
            "identity": BACKEND_IDENTITY_ID,
        }
    )
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: app)

    inspection = deploy._inspect_app(
        "murmur-pilot-rg",
        "murmur-api",
        backend=True,
        expected_frontend_url=FRONTEND_URL,
        expected_retained_retired_voice_secret_versions=RETAINED_VERSIONS,
    )

    assert dict(inspection.retained_retired_voice_secret_versions) == RETAINED_VERSIONS


def test_inspector_rejects_retained_alias_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _backend_app()
    properties = app["properties"]
    assert isinstance(properties, dict)
    configuration = properties["configuration"]
    assert isinstance(configuration, dict)
    secrets = configuration["secrets"]
    assert isinstance(secrets, list)
    secrets.append(
        {
            "name": deploy.LIVEKIT_API_KEY_SECRET_NAME,
            "keyVaultUrl": (
                "https://murmur-vault.vault.azure.net/secrets/"
                f"{deploy.LIVEKIT_API_KEY_SECRET_NAME}/{'d' * 32}"
            ),
            "identity": BACKEND_IDENTITY_ID,
        }
    )
    monkeypatch.setattr(deploy, "_run_json", lambda *_args, **_kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="changed a retained"):
        deploy._inspect_app(
            "murmur-pilot-rg",
            "murmur-api",
            backend=True,
            expected_frontend_url=FRONTEND_URL,
            expected_retained_retired_voice_secret_versions=RETAINED_VERSIONS,
        )


@pytest.mark.parametrize(
    "drift",
    (
        "worker",
        "livekit_env",
        "provider_secret",
        "cors",
        "unknown_env",
        "init_container",
        "process_override",
    ),
)
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
    elif drift == "cors":
        next(item for item in env if item["name"] == "ALLOWED_CORS_ORIGINS")["value"] = (
            "https://wrong.example.azurecontainerapps.io"
        )
    elif drift == "unknown_env":
        env.append({"name": "UNREVIEWED_RUNTIME_SWITCH", "value": "true"})
    elif drift == "init_container":
        template["initContainers"] = [{"name": "bootstrap", "image": "unexpected"}]
    else:
        containers[0]["command"] = ["sh", "-c"]
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
    assignments = tuple(
        deploy.RoleAssignmentMetadata(
            id=f"{scope}/providers/Microsoft.Authorization/roleAssignments/assignment-{index}",
            scope=scope,
            principal_id=BACKEND_PRINCIPAL_ID,
            role_definition_id=deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
            description=None,
        )
        for index in range(2)
    )
    present = {assignment.id for assignment in assignments}
    events: list[str] = []

    def direct(*, secret_name: str, **_kwargs: Any) -> tuple[Any, ...]:
        return tuple(assignment for assignment in assignments if assignment.id in present)

    def run(command: list[str], **_kwargs: Any) -> str:
        assignment_id = command[command.index("--ids") + 1]
        events.append(f"delete:{assignment_id}")
        present.remove(assignment_id)
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
        mutation_guard=lambda: events.append("guard"),
        attempts=1,
    )

    assert events == [
        "guard",
        f"delete:{assignments[0].id}",
        "guard",
        f"delete:{assignments[1].id}",
    ]


def test_verify_live_binds_both_images_before_and_after_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _inspection("murmur-api", backend=True, digest=DIGEST)
    frontend = _inspection("murmur-web", backend=False, digest=FRONTEND_DIGEST)
    changed_digest = f"sha256:{'c' * 64}"
    changed_frontend = replace(
        frontend,
        image=f"murmurregistry.azurecr.io/murmur-web@{changed_digest}",
        image_digest=changed_digest,
    )
    inspections = iter((frontend, backend, changed_frontend, backend))

    monkeypatch.setattr(deploy, "_validate_azure_session", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_run_command", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        deploy, "_inspect_app", lambda *_args, **_kwargs: next(inspections)
    )
    monkeypatch.setattr(deploy, "_inspect_managed_identity", lambda _identity: FRONTEND_PRINCIPAL_ID)
    monkeypatch.setattr(deploy, "_verify_key_vault_metadata", lambda _vault: KEY_VAULT_ID)
    monkeypatch.setattr(deploy, "_verify_frontend_key_vault_boundary", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_backend_key_vault_boundary", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_https", lambda *_args, **_kwargs: None)

    with pytest.raises(deploy.DeploymentRefusal, match=r"frontend.*immutable image"):
        deploy.verify_live(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_backend_identity_id=BACKEND_IDENTITY_ID,
            expected_backend_identity_principal_id=BACKEND_PRINCIPAL_ID,
            expected_frontend_identity_id=FRONTEND_IDENTITY_ID,
            expected_frontend_identity_principal_id=FRONTEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            subscription_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
            expected_secret_versions=VERSIONS,
            expected_registry_server="murmurregistry.azurecr.io",
            expected_backend_image_digest=DIGEST,
            expected_frontend_image_digest=FRONTEND_DIGEST,
            expected_sha=SHA,
        )


def test_retained_voice_version_preflight_reads_the_exact_enabled_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    secret_name, version = next(iter(RETAINED_VERSIONS.items()))

    def run(command: list[str], **_kwargs: Any) -> object:
        commands.append(command)
        return {
            "id": f"https://vault.vault.azure.net/secrets/{secret_name}/{version}",
            "enabled": True,
            "tags": {},
        }

    monkeypatch.setattr(deploy, "_run_json", run)

    deploy._verify_retained_voice_secret_versions_enabled(
        vault_name="vault",
        versions=RETAINED_VERSIONS,
    )

    assert len(commands) == 1
    assert commands[0][commands[0].index("--name") + 1] == secret_name
    assert commands[0][commands[0].index("--version") + 1] == version


def test_key_vault_data_plane_wait_retries_transient_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    sleeps: list[int] = []

    def run(*_args: Any, **_kwargs: Any) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise deploy.DeploymentRefusal("authorization has not propagated")
        return []

    monkeypatch.setattr(deploy, "_run_json", run)
    monkeypatch.setattr(deploy.time, "sleep", sleeps.append)

    deploy._await_key_vault_data_plane_access(vault_name="vault", attempts=2)

    assert attempts == 2
    assert sleeps == [1]


@pytest.mark.parametrize(
    ("observed_version", "enabled"),
    (("d" * 32, True), ("c" * 32, False)),
)
def test_retained_voice_version_preflight_rejects_wrong_or_disabled_version(
    monkeypatch: pytest.MonkeyPatch,
    observed_version: str,
    enabled: bool,
) -> None:
    secret_name, expected_version = next(iter(RETAINED_VERSIONS.items()))
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *_args, **_kwargs: {
            "id": (
                "https://vault.vault.azure.net/secrets/"
                f"{secret_name}/{observed_version}"
            ),
            "enabled": enabled,
            "tags": {},
        },
    )

    with pytest.raises(deploy.DeploymentRefusal, match="missing or disabled"):
        deploy._verify_enabled_key_vault_secret_version(
            vault_name="vault",
            secret_name=secret_name,
            version=expected_version,
        )


@pytest.mark.parametrize(
    ("observed_vault", "observed_secret"),
    (("other-vault", None), ("vault", "wrong-secret")),
)
def test_retained_voice_version_preflight_rejects_wrong_vault_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    observed_vault: str,
    observed_secret: str | None,
) -> None:
    secret_name, version = next(iter(RETAINED_VERSIONS.items()))
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *_args, **_kwargs: {
            "id": (
                f"https://{observed_vault}.vault.azure.net/secrets/"
                f"{observed_secret or secret_name}/{version}"
            ),
            "enabled": True,
            "tags": {},
        },
    )

    with pytest.raises(deploy.DeploymentRefusal, match="invalid Key Vault secret version"):
        deploy._verify_enabled_key_vault_secret_version(
            vault_name="vault",
            secret_name=secret_name,
            version=version,
        )


def test_empty_retained_voice_version_preflight_makes_no_azure_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *_args, **_kwargs: pytest.fail("empty preflight must not call Azure"),
    )

    deploy._verify_retained_voice_secret_versions_enabled(
        vault_name="vault",
        versions={},
    )


def test_backend_boundary_rejects_retired_voice_secret_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def assignments(
        *, secret_name: str, **_kwargs: Any
    ) -> tuple[deploy.RoleAssignmentMetadata, ...]:
        if secret_name in deploy.ACTIVE_SECRET_NAMES:
            scope = f"{KEY_VAULT_ID}/secrets/{secret_name}"
            return (_role_assignment(scope=scope),)
        if secret_name == deploy.RETIRED_VOICE_SECRET_NAMES[0]:
            scope = f"{KEY_VAULT_ID}/secrets/{secret_name}"
            return (_role_assignment(scope=scope),)
        return ()

    monkeypatch.setattr(deploy, "_effective_key_vault_assignments_at_secret", assignments)
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

    with pytest.raises(deploy.DeploymentRefusal, match="exact accepted assignments"):
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )


def test_pending_canary_accepts_only_the_pinned_direct_legacy_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _role_assignment(scope=KEY_VAULT_ID, assignment_id=LEGACY_ASSIGNMENT_ID)

    def assignments(
        *, secret_name: str, **_kwargs: Any
    ) -> tuple[deploy.RoleAssignmentMetadata, ...]:
        effective = [legacy]
        if secret_name in (*deploy.ACTIVE_SECRET_NAMES, *RETAINED_VERSIONS):
            effective.append(
                _role_assignment(scope=f"{KEY_VAULT_ID}/secrets/{secret_name}")
            )
        return tuple(effective)

    monkeypatch.setattr(deploy, "_effective_key_vault_assignments_at_secret", assignments)
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

    deploy._verify_backend_key_vault_boundary(
        backend_principal_id=BACKEND_PRINCIPAL_ID,
        key_vault_id=KEY_VAULT_ID,
        allowed_retired_voice_secret_names=tuple(RETAINED_VERSIONS),
        expected_legacy_vault_read=legacy,
    )

    with pytest.raises(deploy.DeploymentRefusal, match="rollback aliases"):
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            expected_legacy_vault_read=legacy,
        )


def test_pending_canary_rejects_group_derived_or_broader_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _role_assignment(scope=KEY_VAULT_ID, assignment_id=LEGACY_ASSIGNMENT_ID)
    group_legacy = _role_assignment(
        scope=KEY_VAULT_ID,
        principal_id="55555555-5555-5555-5555-555555555555",
        assignment_id=(
            f"{KEY_VAULT_ID}/providers/Microsoft.Authorization/roleAssignments/"
            "66666666-6666-6666-6666-666666666666"
        ),
    )

    def assignments(
        *, secret_name: str, **_kwargs: Any
    ) -> tuple[deploy.RoleAssignmentMetadata, ...]:
        effective = [group_legacy]
        if secret_name in (*deploy.ACTIVE_SECRET_NAMES, *RETAINED_VERSIONS):
            effective.append(
                _role_assignment(scope=f"{KEY_VAULT_ID}/secrets/{secret_name}")
            )
        return tuple(effective)

    monkeypatch.setattr(deploy, "_effective_key_vault_assignments_at_secret", assignments)
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

    with pytest.raises(deploy.DeploymentRefusal, match="exact accepted assignments"):
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            allowed_retired_voice_secret_names=tuple(RETAINED_VERSIONS),
            expected_legacy_vault_read=legacy,
        )


@pytest.mark.parametrize("danger", ("broader-read", "grant-writer"))
def test_pending_canary_rejects_broader_read_and_grant_permissions(
    monkeypatch: pytest.MonkeyPatch,
    danger: str,
) -> None:
    legacy = _role_assignment(scope=KEY_VAULT_ID, assignment_id=LEGACY_ASSIGNMENT_ID)
    dangerous_role = (
        deploy.KEY_VAULT_SECRETS_USER_ROLE_ID
        if danger == "broader-read"
        else "77777777-7777-7777-7777-777777777777"
    )
    broad_scope = "/subscriptions/sub/resourceGroups/rg"
    dangerous = _role_assignment(
        scope=broad_scope,
        role_definition_id=dangerous_role,
        assignment_id=(
            f"{broad_scope}/providers/Microsoft.Authorization/roleAssignments/"
            "88888888-8888-8888-8888-888888888888"
        ),
    )

    def assignments(
        *, secret_name: str, **_kwargs: Any
    ) -> tuple[deploy.RoleAssignmentMetadata, ...]:
        effective = [legacy, dangerous]
        if secret_name in (*deploy.ACTIVE_SECRET_NAMES, *RETAINED_VERSIONS):
            effective.append(
                _role_assignment(scope=f"{KEY_VAULT_ID}/secrets/{secret_name}")
            )
        return tuple(effective)

    def permissions(role_id: str) -> tuple[dict[str, list[str]], ...]:
        return (
            {
                "actions": (
                    ["Microsoft.Authorization/roleAssignments/write"]
                    if role_id == dangerous_role and danger == "grant-writer"
                    else []
                ),
                "notActions": [],
                "dataActions": (
                    ["Microsoft.KeyVault/vaults/secrets/getSecret/action"]
                    if role_id == deploy.KEY_VAULT_SECRETS_USER_ROLE_ID
                    else []
                ),
                "notDataActions": [],
            },
        )

    monkeypatch.setattr(deploy, "_effective_key_vault_assignments_at_secret", assignments)
    monkeypatch.setattr(deploy, "_role_permissions", permissions)

    with pytest.raises(deploy.DeploymentRefusal, match="exact accepted assignments"):
        deploy._verify_backend_key_vault_boundary(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
            allowed_retired_voice_secret_names=tuple(RETAINED_VERSIONS),
            expected_legacy_vault_read=legacy,
        )


def test_direct_legacy_discovery_excludes_inherited_and_group_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> object:
        commands.append(command)
        return [
            {
                "id": LEGACY_ASSIGNMENT_ID,
                "scope": KEY_VAULT_ID,
                "principalId": BACKEND_PRINCIPAL_ID,
                "roleDefinitionId": deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
                "description": None,
            }
        ]

    monkeypatch.setattr(deploy, "_run_json", run)

    assignment = deploy._direct_legacy_backend_vault_read_assignment(
        backend_principal_id=BACKEND_PRINCIPAL_ID,
        key_vault_id=KEY_VAULT_ID,
    )

    assert assignment is not None
    assert assignment.id == LEGACY_ASSIGNMENT_ID
    assert "--include-inherited" not in commands[0]
    assert "--include-groups" not in commands[0]
    query = commands[0][commands[0].index("--query") + 1]
    assert "condition:condition" in query
    assert "conditionVersion:conditionVersion" in query


def test_direct_legacy_discovery_rejects_a_different_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *_args, **_kwargs: [
            {
                "id": LEGACY_ASSIGNMENT_ID,
                "scope": KEY_VAULT_ID,
                "principalId": "55555555-5555-5555-5555-555555555555",
                "roleDefinitionId": deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
                "description": None,
            }
        ],
    )

    with pytest.raises(deploy.DeploymentRefusal, match="ambiguous"):
        deploy._direct_legacy_backend_vault_read_assignment(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )


def test_direct_legacy_discovery_rejects_a_conditional_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *_args, **_kwargs: [
            {
                "id": LEGACY_ASSIGNMENT_ID,
                "scope": KEY_VAULT_ID,
                "principalId": BACKEND_PRINCIPAL_ID,
                "roleDefinitionId": deploy.KEY_VAULT_SECRETS_USER_ROLE_ID,
                "description": None,
                "condition": "@Resource[Microsoft.KeyVault/vaults/secrets:name] StringNotEquals 'rollback'",
                "conditionVersion": "2.0",
            }
        ],
    )

    with pytest.raises(deploy.DeploymentRefusal, match="ambiguous"):
        deploy._direct_legacy_backend_vault_read_assignment(
            backend_principal_id=BACKEND_PRINCIPAL_ID,
            key_vault_id=KEY_VAULT_ID,
        )


def test_deployment_parameter_round_trips_pending_canary_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> object:
        observed.append(command)
        return RETAINED_VERSIONS

    monkeypatch.setattr(deploy, "_run_json", run)

    assert deploy._existing_deployment_retained_retired_voice_secret_versions(
        resource_group="murmur-pilot-rg",
        deployment_name=deploy.APPS_DEPLOYMENT,
    ) == RETAINED_VERSIONS
    assert observed[0][observed[0].index("--query") + 1] == (
        "properties.parameters.retainedRetiredVoiceSecretVersions.value"
    )


def test_standalone_verify_accepts_pending_canary_without_voice_retirement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def output(value: str) -> dict[str, object]:
        return {"type": "String", "value": value}

    foundation = {
        "registryLoginServer": output("murmurregistry.azurecr.io"),
        "keyVaultId": output(KEY_VAULT_ID),
        "keyVaultName": output("murmur-vault"),
        "identityId": output(BACKEND_IDENTITY_ID),
        "identityPrincipalId": output(BACKEND_PRINCIPAL_ID),
        "frontendIdentityId": output(FRONTEND_IDENTITY_ID),
        "frontendIdentityPrincipalId": output(FRONTEND_PRINCIPAL_ID),
        "deploymentLockStorageAccountName": output("murmurlockaccount"),
        "deploymentLockContainerName": output("deployment-locks"),
        "deploymentLockBlobName": output("azure-pilot.lock"),
    }
    apps = {
        parameter_name: output(VERSIONS[secret_name])
        for secret_name, parameter_name in deploy.SECRET_VERSION_PARAMETERS.items()
    }
    live_arguments: dict[str, object] = {}

    monkeypatch.setattr(
        deploy,
        "_existing_deployment_outputs",
        lambda *, deployment_name, **_kwargs: (
            foundation if deployment_name == deploy.FOUNDATION_DEPLOYMENT else apps
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_existing_deployment_images",
        lambda **_kwargs: (
            f"murmurregistry.azurecr.io/murmur-api@{DIGEST}",
            f"murmurregistry.azurecr.io/murmur-web@{FRONTEND_DIGEST}",
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_existing_deployment_retained_retired_voice_secret_versions",
        lambda **_kwargs: RETAINED_VERSIONS,
    )
    monkeypatch.setattr(deploy, "_current_principal", lambda: (BACKEND_PRINCIPAL_ID, "User"))

    class Lease:
        def assert_healthy(self) -> None:
            return None

    @deploy.contextmanager
    def lease(**_kwargs: Any) -> Any:
        yield Lease()

    @deploy.contextmanager
    def writer(*_args: Any, **_kwargs: Any) -> Any:
        yield None

    monkeypatch.setattr(deploy, "_deployment_blob_lease", lease)
    monkeypatch.setattr(deploy, "_temporary_key_vault_write", writer)
    monkeypatch.setattr(
        deploy,
        "_await_key_vault_data_plane_access",
        lambda **_kwargs: None,
    )
    legacy_vault_read = _role_assignment(
        scope=KEY_VAULT_ID, assignment_id=LEGACY_ASSIGNMENT_ID
    )
    monkeypatch.setattr(
        deploy,
        "_direct_legacy_backend_vault_read_assignment",
        lambda **_kwargs: legacy_vault_read,
    )
    monkeypatch.setattr(deploy, "_verify_backend_key_vault_boundary", lambda **_kwargs: None)

    backend = replace(
        _inspection("murmur-api", backend=True, digest=DIGEST),
        retained_retired_voice_secret_versions=tuple(RETAINED_VERSIONS.items()),
    )
    frontend = _inspection("murmur-web", backend=False, digest=FRONTEND_DIGEST)

    def verify_live(**kwargs: Any) -> tuple[deploy.AppInspection, deploy.AppInspection]:
        live_arguments.update(kwargs)
        return backend, frontend

    monkeypatch.setattr(deploy, "verify_live", verify_live)
    monkeypatch.setattr(deploy, "_verify_old_revisions_inactive", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_rotation_postcondition", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "_verify_legacy_secret_postcondition", lambda **_kwargs: None)
    monkeypatch.setattr(
        deploy,
        "_verify_retired_voice_secret_postconditions",
        lambda **_kwargs: pytest.fail("pending canary must not require voice-secret retirement"),
    )
    monkeypatch.setattr(deploy, "_assert_no_key_vault_writer", lambda _vault: None)
    monkeypatch.setattr(deploy, "_print_verification", lambda *_args: None)

    result = deploy.verify(
        argparse.Namespace(
            resource_group="murmur-pilot-rg",
            backend_app="murmur-api",
            frontend_app="murmur-web",
            expected_sha=SHA,
            subscription_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
            health_timeout_seconds=1,
        )
    )

    assert result == 0
    assert (
        live_arguments["expected_retained_retired_voice_secret_versions"]
        == RETAINED_VERSIONS
    )
    assert live_arguments["expected_legacy_backend_vault_read"] == legacy_vault_read
    output_text = capsys.readouterr().out
    assert "websocket_canary_required: true" in output_text
    assert "voice_retirement_status: pending_finalization" in output_text

"""Redaction and orchestration contracts for the Azure pilot driver."""

from __future__ import annotations

import argparse
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
BACKEND_URL = "https://murmur-api.example.centralindia.azurecontainerapps.io"
FRONTEND_URL = "https://murmur-web.example.centralindia.azurecontainerapps.io"


def _service_account(project_id: str = "firebase-project") -> dict[str, str]:
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "private-key-id",
        "private_key": PRIVATE_KEY,
        "client_email": f"firebase-adminsdk@{project_id}.iam.gserviceaccount.com",
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    service_account = tmp_path / "firebase-service-account.json"
    service_account.write_text(json.dumps(_service_account()), encoding="utf-8")
    service_account.chmod(0o600)

    backend = tmp_path / ".env"
    backend.write_text(
        "\n".join(
            (
                f"AZURE_OPENAI_API_KEY={AZURE_KEY}",
                "AZURE_OPENAI_ENDPOINT=https://murmur-resource.services.ai.azure.com",
                "AZURE_OPENAI_DEPLOYMENT=murmur-gpt-oss-120b",
                "FIREBASE_PROJECT_ID=firebase-project",
                f"FIREBASE_SERVICE_ACCOUNT_PATH={service_account}",
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
            )
        ),
        encoding="utf-8",
    )
    return backend, frontend


def _output(value: str) -> dict[str, object]:
    return {"type": "String", "value": value}


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
    env: list[dict[str, str]] = [{"name": "MURMUR_RELEASE_SHA", "value": SHA}]
    secrets: list[dict[str, str]] = []
    volumes: list[dict[str, str]] = []
    mounts: list[dict[str, str]] = []
    identity_id = "/subscriptions/sub/resourcegroups/rg/providers/identity"
    reference_identity_id = "/subscriptions/sub/resourceGroups/rg/providers/identity"
    if backend:
        env.extend(
            (
                {"name": "AZURE_OPENAI_API_KEY", "secretRef": "azure-openai-api-key"},
                {
                    "name": "FIREBASE_SERVICE_ACCOUNT_JSON",
                    "secretRef": "firebase-service-account-json",
                },
                {"name": "MURMUR_DATA_DIR", "value": "/data"},
                {"name": "MURMUR_SQLITE_JOURNAL_MODE", "value": "DELETE"},
            )
        )
        secrets = [
            {
                "name": secret_name,
                "keyVaultUrl": f"https://murmur-vault.vault.azure.net/secrets/{secret_name}",
                "identity": reference_identity_id,
                **({"value": AZURE_KEY} if inline_secret and index == 0 else {}),
            }
            for index, secret_name in enumerate(
                ("azure-openai-api-key", "firebase-service-account-json")
            )
        ]
        mounts = [{"mountPath": "/data", "volumeName": "murmur-data"}]
        volumes = [
            {"name": "murmur-data", "storageName": "murmur-data", "storageType": "AzureFile"}
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
                "scale": {"minReplicas": 0, "maxReplicas": 1},
                "containers": [
                    {
                        "name": "api" if backend else "web",
                        "image": f"murmurregistry.azurecr.io/{name}@{DIGEST}",
                        "env": env,
                        "probes": probes,
                        "volumeMounts": mounts,
                    }
                ],
                "volumes": volumes,
            },
        },
    }


def _inspection(name: str, url: str, *, backend: bool) -> deploy.AppInspection:
    return deploy.AppInspection(
        name=name,
        url=url,
        image=f"murmurregistry.azurecr.io/{name}@{DIGEST}",
        image_digest=DIGEST,
        registry_server="murmurregistry.azurecr.io",
        release_sha=SHA,
        latest_revision=f"{name}--revision",
        provisioning_state="Succeeded",
        running_status="Running",
        min_replicas=0,
        max_replicas=1,
        probe_types=("Liveness", "Readiness", "Startup"),
        key_vault_name="murmur-vault" if backend else None,
    )


def test_load_deployment_inputs_validates_projects_without_printing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backend, frontend = _write_inputs(tmp_path)

    inputs = deploy.load_deployment_inputs(backend, frontend)

    assert inputs.azure_openai_key == AZURE_KEY
    assert inputs.firebase_project_id == "firebase-project"
    assert json.loads(inputs.firebase_json_bytes())["private_key"] == PRIVATE_KEY
    assert inputs.frontend_public["NEXT_PUBLIC_FIREBASE_API_KEY"] == "browser-public-key"
    assert "NEXT_PUBLIC_API_URL" not in inputs.frontend_public
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert AZURE_KEY not in repr(inputs)
    assert PRIVATE_KEY not in repr(inputs)


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


def test_key_vault_secret_uses_mode_600_file_and_suppresses_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: Any, **kwargs: Any) -> str:
        path = Path(command[command.index("--file") + 1])
        observed["path"] = path
        observed["payload"] = path.read_text(encoding="utf-8")
        observed["mode"] = stat.S_IMODE(path.stat().st_mode)
        observed["command"] = tuple(command)
        assert kwargs["operation"] == "write Key Vault secret azure-openai-api-key"
        return ""

    monkeypatch.setattr(deploy, "_run_command", fake_run)

    deploy._write_key_vault_secret(
        vault_name="murmur-vault",
        secret_name="azure-openai-api-key",
        payload=AZURE_KEY.encode(),
        attempts=1,
    )

    command = observed["command"]
    assert observed["mode"] == 0o600
    assert observed["payload"] == AZURE_KEY
    assert AZURE_KEY not in command
    assert command[-2:] == ("--output", "none")
    assert not observed["path"].exists()


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


def test_acr_build_waits_for_amd64_manifest_and_does_not_use_secret_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    observed: list[str] = []

    def fake_run(command: Any, **kwargs: Any) -> str:
        if command[1:3] == ["acr", "build"]:
            observed.extend(command)
            assert kwargs["operation"] == "build ACR image murmur-api"
            assert kwargs["cwd"] == context
            return ""
        assert command[1:4] == ["acr", "repository", "show"]
        return f"{DIGEST}\n"

    monkeypatch.setattr(deploy, "_run_command", fake_run)

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
    assert observed[-1] == "."
    assert digest == DIGEST
    assert AZURE_KEY not in observed
    assert PRIVATE_KEY not in observed


def test_firebase_domain_update_preserves_existing_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, object]] = []
    monkeypatch.setattr(deploy, "_firebase_access_token", lambda _account: "oauth-token")

    def fake_request(method: str, url: str, token: str, payload: Any = None) -> dict[str, object]:
        calls.append((method, url, token, payload))
        if method == "GET":
            return {"authorizedDomains": ["localhost", "firebase-project.firebaseapp.com"]}
        return {"authorizedDomains": payload["authorizedDomains"]}

    monkeypatch.setattr(deploy, "_identity_toolkit_request", fake_request)

    result = deploy._configure_firebase_domain(_service_account(), "firebase-project", FRONTEND_URL)

    assert result.status == "configured"
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"
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
        return {"status": "ok", "release_sha": SHA}

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


def test_inspect_backend_validates_scale_probes_mount_and_key_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: _container_app(backend=True))

    inspected = deploy._inspect_app("murmur-pilot-rg", "murmur-api", backend=True)

    assert inspected.release_sha == SHA
    assert inspected.key_vault_name == "murmur-vault"
    assert inspected.min_replicas == 0
    assert inspected.max_replicas == 1
    assert inspected.probe_types == ("Liveness", "Readiness", "Startup")


def test_inspect_backend_refuses_inline_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deploy,
        "_run_json",
        lambda *args, **kwargs: _container_app(backend=True, inline_secret=True),
    )

    with pytest.raises(deploy.DeploymentRefusal, match="inline credential") as raised:
        deploy._inspect_app("murmur-pilot-rg", "murmur-api", backend=True)

    assert AZURE_KEY not in str(raised.value)


def test_inspect_backend_refuses_registry_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=True)
    registry = app["properties"]["configuration"]["registries"][0]  # type: ignore[index]
    registry["username"] = "unexpected-user"  # type: ignore[index]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="identity-based ACR pull"):
        deploy._inspect_app("murmur-pilot-rg", "murmur-api", backend=True)


def test_inspect_backend_refuses_ephemeral_database_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _container_app(backend=True)
    container = app["properties"]["template"]["containers"][0]  # type: ignore[index]
    container["env"] = [  # type: ignore[index]
        item
        for item in container["env"]
        if item.get("name") != "MURMUR_DATA_DIR"  # type: ignore[index,union-attr]
    ]
    monkeypatch.setattr(deploy, "_run_json", lambda *args, **kwargs: app)

    with pytest.raises(deploy.DeploymentRefusal, match="not rooted on /data"):
        deploy._inspect_app("murmur-pilot-rg", "murmur-api", backend=True)


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
        "environmentDefaultDomain": _output("example.centralindia.azurecontainerapps.io"),
        "backendUrl": _output(BACKEND_URL),
        "frontendUrl": _output(FRONTEND_URL),
    }
    app_outputs = {
        "backendUrl": _output(BACKEND_URL),
        "frontendUrl": _output(FRONTEND_URL),
        "backendLatestRevisionName": _output("murmur-api--revision"),
    }
    deployments = iter((foundation, app_outputs))

    monkeypatch.setattr(
        deploy,
        "validate_source_revision",
        lambda: deploy.SourceRevision(SHA, "codex/deploy", "origin", "refs/heads/codex/deploy"),
    )
    monkeypatch.setattr(deploy, "load_deployment_inputs", lambda *_args: inputs)
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda: None)
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
        deploy, "_grant_key_vault_write", lambda value: calls.append(("kv-role", value))
    )
    monkeypatch.setattr(
        deploy,
        "_write_key_vault_secret",
        lambda **kwargs: calls.append(("secret", kwargs["secret_name"])),
    )
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

    def fake_configure_firebase(*_args: Any) -> deploy.FirebaseDomainResult:
        if not firebase_configured:
            raise deploy.DeploymentRefusal("IAM unavailable")
        return deploy.FirebaseDomainResult(
            "configured", "murmur-web.example.centralindia.azurecontainerapps.io"
        )

    monkeypatch.setattr(deploy, "_configure_firebase_domain", fake_configure_firebase)
    monkeypatch.setattr(
        deploy,
        "_restart_revision",
        lambda *args: calls.append(("restart", args)),
    )
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    monkeypatch.setattr(
        deploy,
        "verify_live",
        lambda **_kwargs: (backend_inspection, frontend_inspection),
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
    )
    assert deploy.deploy(args) == expected_result

    labels = [item[0] for item in calls]
    assert labels.index("backend-build") < labels.index("frontend-build")
    assert labels.index("frontend-build") < labels.index("restart")
    assert next(item[1] for item in calls if item[0] == "restart") == (
        "murmur-pilot-rg",
        "murmur-api",
        "murmur-api--revision",
    )
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
    foundation_parameters = next(
        item[1]["parameters"]
        for item in calls
        if item[0] == "bicep" and item[1]["deployment_name"] == deploy.FOUNDATION_DEPLOYMENT
    )
    assert foundation_parameters == {
        "location": "centralindia",
        "backendAppName": "murmur-api",
        "frontendAppName": "murmur-web",
    }


def test_verify_live_performs_only_metadata_and_health_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_inspection = _inspection("murmur-api", BACKEND_URL, backend=True)
    frontend_inspection = _inspection("murmur-web", FRONTEND_URL, backend=False)
    observed: list[str] = []
    monkeypatch.setattr(deploy, "_validate_azure_session", lambda: observed.append("azure"))
    monkeypatch.setattr(deploy, "_run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        deploy,
        "_inspect_app",
        lambda _rg, _name, *, backend: backend_inspection if backend else frontend_inspection,
    )
    monkeypatch.setattr(
        deploy,
        "_verify_key_vault_metadata",
        lambda _vault: observed.append("key-vault-metadata"),
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
    )

    assert live_backend == backend_inspection
    assert live_frontend == frontend_inspection
    assert observed == ["azure", "key-vault-metadata", "https"]


def test_main_redacts_unexpected_exception_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_args: argparse.Namespace) -> int:
        raise RuntimeError(f"unexpected {AZURE_KEY} {PRIVATE_KEY}")

    monkeypatch.setattr(deploy, "verify", fail)

    assert deploy.main(["verify", "--health-timeout-seconds", "1"]) == 1
    captured = capsys.readouterr()
    assert AZURE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.err
    assert "no credential values were printed" in captured.err

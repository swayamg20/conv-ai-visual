from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPS = (PROJECT_ROOT / "infra/azure/apps.bicep").read_text(encoding="utf-8")
FOUNDATION = (PROJECT_ROOT / "infra/azure/foundation.bicep").read_text(encoding="utf-8")

BACKEND = APPS.split("resource backend 'Microsoft.App/containerApps", 1)[1].split(
    "resource frontend 'Microsoft.App/containerApps", 1
)[0]
FRONTEND = APPS.split("resource frontend 'Microsoft.App/containerApps", 1)[1]

def test_apps_deploys_one_first_party_websocket_api_container() -> None:
    assert BACKEND.count("containers: [") == 1
    assert BACKEND.count("image: backendImage") == 1
    assert BACKEND.count("name: 'api'") == 1
    assert BACKEND.count("name: 'VOICE_RUNTIME'") == 1
    assert "name: 'VOICE_RUNTIME'\n              value: 'websocket_v1'" in BACKEND
    assert "name: 'ALLOWED_CORS_ORIGINS'\n              value: frontendUrl" in BACKEND
    assert "var frontendUrl = 'https://${frontendAppName}.${environment.properties.defaultDomain}'" in APPS
    assert BACKEND.count("minReplicas: 1") == 1
    assert BACKEND.count("maxReplicas: 1") == 1


def test_apps_limits_runtime_secrets_to_openai_and_firebase() -> None:
    secret_refs = set(re.findall(r"secretRef: '([^']+)'", BACKEND))
    key_vault_refs = set(re.findall(r"secrets/([^/$]+?)/\$\{", BACKEND))

    expected = {"azure-openai-api-key", "firebase-runtime-service-account-json"}
    assert secret_refs == expected
    assert key_vault_refs == expected
    assert "param azureOpenAiSecretVersion string" in APPS
    assert "param firebaseRuntimeSecretVersion string" in APPS

    assert "name: 'voice-worker'" not in BACKEND
    assert "livekit.agents" not in BACKEND


def test_apps_retains_old_secret_aliases_only_as_unreferenced_canary_rollback() -> None:
    assert "param retainedRetiredVoiceSecretVersions object = {}" in APPS
    assert "contains(retainedRetiredVoiceSecretVersions, secretName)" in APPS
    assert "concat([" in BACKEND
    assert "], retainedRetiredVoiceSecrets)" in BACKEND
    for secret_name in (
        "livekit-api-key",
        "livekit-api-secret",
        "voice-v2-signing-secret",
        "deepgram-api-key",
        "groq-api-key",
        "elevenlabs-api-key",
    ):
        assert f"'{secret_name}'" in APPS
        assert f"secretRef: '{secret_name}'" not in BACKEND


def test_foundation_grants_key_vault_access_only_to_active_secrets() -> None:
    secret_names = FOUNDATION.split("var backendSecretNames = [", 1)[1].split("]", 1)[0]
    assert re.findall(r"'([^']+)'", secret_names) == [
        "azure-openai-api-key",
        "firebase-runtime-service-account-json",
    ]

    assignment = FOUNDATION.split("resource backendSecretReads", 1)[1].split(
        "resource deploymentLockStorage", 1
    )[0]
    assert "for (secretName, index) in backendSecretNames" in assignment
    assert "scope: backendSecrets[index]" in assignment
    assert "principalId: identity.properties.principalId" in assignment


def test_apps_preserves_chat_scene_frontend_and_data_volume_contracts() -> None:
    for name, value in (
        ("LLM_PROVIDER", "azure_openai"),
        ("LLM_MAX_TOKENS", "1024"),
        ("MURMUR_CHAT_GLOBAL_CONCURRENCY", "1"),
        ("MURMUR_CHAT_PER_USER_CONCURRENCY", "1"),
        ("MURMUR_CHAT_REQUESTS_PER_MINUTE", "2"),
        ("MURMUR_CHAT_MAX_TOOL_ROUNDS", "2"),
        ("MURMUR_CHAT_LLM_TRANSPORT_MAX_RETRIES", "0"),
        ("MURMUR_SCENE_ENABLED", "true"),
        ("MURMUR_SCENE_LLM_PROVIDER", "azure_openai"),
        ("MURMUR_SCENE_LLM_MAX_TOKENS", "2048"),
    ):
        assert f"name: '{name}'\n              value: '{value}'" in BACKEND

    assert "name: 'MURMUR_SCENE_LLM_MODEL'\n              value: azureOpenAiDeployment" in BACKEND
    assert "mountPath: '/home/murmur/data'\n              volumeName: 'murmur-data'" in BACKEND
    assert "name: 'murmur-data'\n          storageType: 'EmptyDir'" in BACKEND

    assert FRONTEND.count("image: frontendImage") == 1
    assert FRONTEND.count("name: 'web'") == 1
    assert "secrets:" not in FRONTEND
    assert FRONTEND.count("minReplicas: 1") == 1
    assert FRONTEND.count("maxReplicas: 1") == 1

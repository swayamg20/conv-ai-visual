"""Non-billable deployment health and readiness contracts."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from murmur.api.routers import health


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


def _production_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health.config, "MURMUR_ENVIRONMENT", "production")
    monkeypatch.setattr(health.config, "FIREBASE_PROJECT_ID", "firebase-project")
    monkeypatch.setattr(health.config, "FIREBASE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setattr(health.config, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    monkeypatch.setattr(health.config, "LLM_PROVIDER", "azure_openai")
    monkeypatch.setattr(health.config, "MURMUR_SCENE_ENABLED", True)
    monkeypatch.setattr(health.config, "MURMUR_SCENE_LLM_PROVIDER", "azure_openai")
    monkeypatch.setattr(health.config, "MURMUR_SCENE_LLM_MODEL", "murmur-gpt-oss-120b")
    monkeypatch.setattr(health.config, "AZURE_OPENAI_API_KEY", "server-only-key")
    monkeypatch.setattr(
        health.config,
        "AZURE_OPENAI_ENDPOINT",
        "https://murmur-resource.openai.azure.com",
    )
    monkeypatch.setattr(health.config, "AZURE_OPENAI_DEPLOYMENT", "murmur-gpt-oss-120b")


def test_liveness_is_public_cache_disabled_and_independent_of_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(health.config, "MURMUR_RELEASE_SHA", release_sha)
    monkeypatch.setattr(
        health.persistence_database.engine,
        "connect",
        Mock(side_effect=RuntimeError("database-secret")),
    )

    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "release_sha": release_sha}
    assert response.headers["cache-control"] == "no-store"


def test_development_readiness_checks_database_without_requiring_cloud_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health.config, "MURMUR_ENVIRONMENT", "development")
    monkeypatch.setattr(health.config, "MURMUR_RELEASE_SHA", None)

    response = _client().get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "release_sha": None,
        "checks": {
            "database": "ready",
            "firebase": "not_required",
            "azure_openai": "not_required",
        },
    }
    assert response.headers["cache-control"] == "no-store"


def test_production_readiness_validates_local_configuration_without_remote_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_configuration(monkeypatch)
    credential_loader = Mock(return_value=object())
    monkeypatch.setattr(health, "create_firebase_credential", credential_loader)

    response = _client().get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "database": "ready",
        "firebase": "ready",
        "azure_openai": "ready",
    }
    credential_loader.assert_called_once_with()


@pytest.mark.parametrize(
    "missing_attribute",
    [
        "FIREBASE_PROJECT_ID",
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    ],
)
def test_production_readiness_fails_closed_on_missing_required_configuration(
    missing_attribute: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_configuration(monkeypatch)
    monkeypatch.setattr(health.config, missing_attribute, "")
    monkeypatch.setattr(health, "create_firebase_credential", Mock(return_value=object()))

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "server-only-key" not in response.text


def test_readiness_reports_database_failure_without_exposing_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "database-secret-must-not-escape"
    monkeypatch.setattr(health.config, "MURMUR_ENVIRONMENT", "development")
    monkeypatch.setattr(
        health.persistence_database.engine,
        "connect",
        Mock(side_effect=RuntimeError(secret)),
    )

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"
    assert secret not in response.text

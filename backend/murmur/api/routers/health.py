"""Public liveness and non-billable dependency-readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from murmur.api.firebase_credentials import (
    FirebaseCredentialConfigurationError,
    create_firebase_credential,
    has_explicit_firebase_credential,
)
from murmur.core.config import config, normalize_azure_openai_endpoint
from murmur.persistence import database as persistence_database

router = APIRouter(tags=["health"])

_READY = "ready"
_UNAVAILABLE = "unavailable"
_NOT_REQUIRED = "not_required"


def _database_status() -> str:
    try:
        with persistence_database.engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                return _UNAVAILABLE
    except Exception:
        return _UNAVAILABLE
    return _READY


def _firebase_status() -> str:
    if config.MURMUR_ENVIRONMENT.casefold() != "production":
        return _NOT_REQUIRED
    if not config.FIREBASE_PROJECT_ID or not has_explicit_firebase_credential():
        return _UNAVAILABLE
    try:
        credential = create_firebase_credential()
    except FirebaseCredentialConfigurationError:
        return _UNAVAILABLE
    return _READY if credential is not None else _UNAVAILABLE


def _uses_azure_openai() -> bool:
    return config.LLM_PROVIDER.casefold() == "azure_openai" or (
        config.MURMUR_SCENE_ENABLED
        and config.MURMUR_SCENE_LLM_PROVIDER.casefold() == "azure_openai"
    )


def _azure_openai_status() -> str:
    if config.MURMUR_ENVIRONMENT.casefold() != "production" or not _uses_azure_openai():
        return _NOT_REQUIRED
    if not config.AZURE_OPENAI_API_KEY or not config.AZURE_OPENAI_DEPLOYMENT:
        return _UNAVAILABLE
    if (
        config.MURMUR_SCENE_ENABLED
        and config.MURMUR_SCENE_LLM_PROVIDER.casefold() == "azure_openai"
        and not config.MURMUR_SCENE_LLM_MODEL
    ):
        return _UNAVAILABLE
    try:
        normalize_azure_openai_endpoint(config.AZURE_OPENAI_ENDPOINT)
    except ValueError:
        return _UNAVAILABLE
    return _READY


def readiness_payload() -> tuple[dict[str, object], int]:
    """Return a closed readiness report without contacting Firebase or a model."""

    checks = {
        "database": _database_status(),
        "firebase": _firebase_status(),
        "azure_openai": _azure_openai_status(),
    }
    ready = _UNAVAILABLE not in checks.values()
    return (
        {
            "status": _READY if ready else "not_ready",
            "release_sha": config.MURMUR_RELEASE_SHA,
            "checks": checks,
        },
        200 if ready else 503,
    )


@router.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    """Prove only that the ASGI process can serve an HTTP response."""

    return JSONResponse(
        {"status": "ok", "release_sha": config.MURMUR_RELEASE_SHA},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Check local dependencies and required production configuration."""

    payload, status_code = readiness_payload()
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["healthz", "readiness_payload", "readyz", "router"]

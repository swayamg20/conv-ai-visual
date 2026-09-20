"""Secret-safe Firebase Admin credential construction shared by HTTP runtimes."""

from __future__ import annotations

import json
from typing import Any

from firebase_admin import credentials

from murmur.core.config import config


class FirebaseCredentialConfigurationError(RuntimeError):
    """The configured Firebase service-account source cannot be used safely."""


def create_firebase_credential() -> credentials.Base | None:
    """Build a Firebase credential locally without logging secret material.

    Inline JSON takes precedence over the legacy file path. Returning ``None``
    preserves Firebase Admin's Application Default Credentials behavior for
    local development; production readiness requires an explicit source.
    """

    inline_json = config.FIREBASE_SERVICE_ACCOUNT_JSON
    credential_source: str | dict[str, Any] | None
    if inline_json:
        try:
            parsed = json.loads(inline_json)
        except (TypeError, ValueError):
            raise FirebaseCredentialConfigurationError(
                "Firebase service account configuration is invalid"
            ) from None
        if not isinstance(parsed, dict):
            raise FirebaseCredentialConfigurationError(
                "Firebase service account configuration is invalid"
            ) from None
        credential_source = parsed
    else:
        credential_source = config.FIREBASE_SERVICE_ACCOUNT_PATH

    if credential_source is None:
        return None

    try:
        return credentials.Certificate(credential_source)
    except Exception:
        raise FirebaseCredentialConfigurationError(
            "Firebase service account configuration is invalid"
        ) from None


def has_explicit_firebase_credential() -> bool:
    """Return whether an inline or file-backed service account was configured."""

    return bool(config.FIREBASE_SERVICE_ACCOUNT_JSON or config.FIREBASE_SERVICE_ACCOUNT_PATH)


__all__ = [
    "FirebaseCredentialConfigurationError",
    "create_firebase_credential",
    "has_explicit_firebase_credential",
]

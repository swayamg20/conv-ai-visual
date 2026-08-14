"""Firebase authentication and trusted user resolution."""

import logging
import threading
from collections.abc import Mapping

import firebase_admin
from fastapi import HTTPException, Request
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from murmur.core.config import config
from murmur.persistence.repositories.identities import UserRepo

logger = logging.getLogger(__name__)

_MAX_FIREBASE_UID_LENGTH = 128
_MAX_FIREBASE_EMAIL_LENGTH = 320
_MAX_FIREBASE_NAME_LENGTH = 256
_MAX_FIREBASE_TOKEN_LENGTH = 16_000
_FIREBASE_HTTP_TIMEOUT_SECONDS = 2.0
_firebase_lock = threading.Lock()
_firebase_app: firebase_admin.App | None = None
_firebase_init_attempted: bool = False


class FirebaseAuthenticationUnavailable(RuntimeError):
    """Firebase verification or trusted identity resolution is unavailable."""


def _ensure_firebase() -> firebase_admin.App:
    """Initialise the bounded Firebase Admin app exactly once."""
    global _firebase_app, _firebase_init_attempted
    with _firebase_lock:
        if _firebase_init_attempted:
            if _firebase_app is None:
                raise FirebaseAuthenticationUnavailable("Authentication is unavailable") from None
            return _firebase_app

        _firebase_init_attempted = True
        options: dict[str, object] = {
            "httpTimeout": _FIREBASE_HTTP_TIMEOUT_SECONDS,
        }
        if config.FIREBASE_PROJECT_ID:
            options["projectId"] = config.FIREBASE_PROJECT_ID

        try:
            if config.FIREBASE_SERVICE_ACCOUNT_PATH:
                cred = credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT_PATH)
                _firebase_app = firebase_admin.initialize_app(cred, options=options)
            elif config.FIREBASE_PROJECT_ID:
                # Explicitly pin the Firebase project for local/dev token verification.
                _firebase_app = firebase_admin.initialize_app(options=options)
            else:
                logger.warning(
                    "Firebase project is not configured; set FIREBASE_PROJECT_ID for local auth verification"
                )
                _firebase_app = firebase_admin.initialize_app(options=options)
        except Exception:
            logger.warning("Firebase Admin SDK init failed; authentication is unavailable")
            _firebase_app = None
            raise FirebaseAuthenticationUnavailable("Authentication is unavailable") from None

        logger.info("Firebase Admin SDK initialised successfully")
        return _firebase_app


def verify_firebase_token(token: str) -> dict | None:
    """Verify an ID token, returning None only for rejected credentials."""
    app = _ensure_firebase()
    try:
        decoded = firebase_auth.verify_id_token(
            token,
            app=app,
            check_revoked=True,
        )
        return decoded
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.UserDisabledError,
        firebase_auth.UserNotFoundError,
    ):
        return None
    except Exception:
        logger.warning("Firebase token verification is unavailable")
        raise FirebaseAuthenticationUnavailable("Authentication is unavailable") from None


def _firebase_bearer(request: Request) -> str | None:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        return None
    token = values[0][7:]
    if (
        not token
        or len(token) > _MAX_FIREBASE_TOKEN_LENGTH
        or token != token.strip()
        or any(character.isspace() for character in token)
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        return None
    return token


def _firebase_uid(claims: Mapping[str, object]) -> str | None:
    raw_uid = claims.get("uid") or claims.get("user_id")
    if (
        not isinstance(raw_uid, str)
        or not raw_uid
        or len(raw_uid) > _MAX_FIREBASE_UID_LENGTH
        or raw_uid != raw_uid.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_uid)
    ):
        return None
    return raw_uid


def _firebase_name(claims: Mapping[str, object]) -> str | None:
    raw_name = claims.get("name")
    if (
        not isinstance(raw_name, str)
        or len(raw_name) > _MAX_FIREBASE_NAME_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_name)
    ):
        return None
    return raw_name


def _verified_firebase_email(claims: Mapping[str, object]) -> str | None:
    raw_email = claims.get("email")
    if claims.get("email_verified") is not True or not isinstance(raw_email, str):
        return None
    if (
        not raw_email
        or raw_email != raw_email.strip()
        or len(raw_email) > _MAX_FIREBASE_EMAIL_LENGTH
        or raw_email.count("@") != 1
        or any(character.isspace() for character in raw_email)
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_email)
    ):
        return None
    local_part, domain = raw_email.rsplit("@", 1)
    return raw_email if local_part and domain else None


def get_current_user(request: Request) -> dict | None:
    """
    FastAPI dependency — verifies the Bearer token via Firebase.
    Returns a dict with keys {id, email, name} or None.
    Verified email claims may provision or link a user. Unverified claims
    authenticate only an already-existing exact Firebase UID.
    """
    token = _firebase_bearer(request)
    if token is None:
        return None
    claims = verify_firebase_token(token)
    if claims is None:
        return None

    uid = _firebase_uid(claims)
    if uid is None:
        return None

    name = _firebase_name(claims)
    verified_email = _verified_firebase_email(claims)
    try:
        if verified_email is None:
            user = UserRepo.get_by_id(uid)
            if user is None:
                return None
            if user.id != uid:
                logger.warning("Firebase exact-UID lookup returned a mismatched identity")
                return None
        else:
            existing = UserRepo.get_by_id(uid)
            user = UserRepo.get_or_create(
                uid=uid,
                email=verified_email,
                name=name,
            )
            if existing is not None and user.id != uid:
                logger.warning("Firebase UID provisioning returned a mismatched identity")
                return None
    except Exception:
        logger.warning("Firebase user resolution is unavailable")
        raise FirebaseAuthenticationUnavailable("Authentication is unavailable") from None
    return {"id": user.id, "email": user.email, "name": user.name}


def require_auth(request: Request) -> str:
    """FastAPI Depends returning a UID with fixed 401/503 failures."""
    try:
        user = get_current_user(request)
    except FirebaseAuthenticationUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Authentication is unavailable",
        ) from None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user["id"]

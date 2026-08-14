"""Firebase authentication and trusted user resolution."""

import logging
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
_firebase_app: firebase_admin.App | None = None
_firebase_init_attempted: bool = False


def _ensure_firebase() -> firebase_admin.App | None:
    """Initialise the Firebase Admin SDK once.  Returns the app or None."""
    global _firebase_app, _firebase_init_attempted
    if _firebase_init_attempted:
        return _firebase_app
    _firebase_init_attempted = True
    try:
        if config.FIREBASE_SERVICE_ACCOUNT_PATH:
            cred = credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT_PATH)
            _firebase_app = firebase_admin.initialize_app(cred)
        elif config.FIREBASE_PROJECT_ID:
            # Explicitly pin the Firebase project for local/dev token verification.
            _firebase_app = firebase_admin.initialize_app(
                options={"projectId": config.FIREBASE_PROJECT_ID}
            )
        else:
            logger.warning(
                "Firebase project is not configured; set FIREBASE_PROJECT_ID for local auth verification"
            )
            _firebase_app = firebase_admin.initialize_app()
        logger.info(
            "Firebase Admin SDK initialised successfully (project_id=%s)",
            config.FIREBASE_PROJECT_ID or "<default>",
        )
    except Exception:
        logger.warning("Firebase Admin SDK init failed — auth will be unavailable")
        _firebase_app = None
    return _firebase_app


def verify_firebase_token(token: str) -> dict | None:
    """Verify a Firebase ID token.  Returns decoded claims dict or None."""
    app = _ensure_firebase()
    if app is None:
        logger.warning("Firebase not initialised — cannot verify token")
        return None
    try:
        decoded = firebase_auth.verify_id_token(token, app=app)
        return decoded
    except Exception:
        logger.warning("Firebase token verification failed")
        return None


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
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
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
        logger.warning("Firebase user provisioning failed")
        return None
    return {"id": user.id, "email": user.email, "name": user.name}


def require_auth(request: Request) -> str:
    """FastAPI Depends — raises 401 if not authenticated.  Returns user uid."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user["id"]

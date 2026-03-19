"""
Authentication and user identity middleware.
Uses Firebase Admin SDK for token verification.
"""
import logging

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials
from fastapi import HTTPException, Request

from .config import config
from .models import UserRepo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Firebase Admin SDK initialisation
# ---------------------------------------------------------------------------
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
            # Application Default Credentials (Cloud Run, etc.)
            _firebase_app = firebase_admin.initialize_app(options={"projectId": config.FIREBASE_PROJECT_ID})
        else:
            # Try default credentials (useful in GCP environments)
            _firebase_app = firebase_admin.initialize_app()
        logger.info("Firebase Admin SDK initialised successfully")
    except Exception as exc:
        logger.warning("Firebase Admin SDK init failed — auth will be unavailable: %s", exc)
        _firebase_app = None
    return _firebase_app


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def verify_firebase_token(token: str) -> dict | None:
    """Verify a Firebase ID token.  Returns decoded claims dict or None."""
    if _ensure_firebase() is None:
        logger.warning("Firebase not initialised — cannot verify token")
        return None
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded
    except Exception as exc:
        logger.debug("Firebase token verification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def get_current_user(request: Request) -> dict | None:
    """
    FastAPI dependency — verifies the Bearer token via Firebase.
    Returns a dict with keys {id, email, name} or None.
    On first valid token, auto-provisions the user in the DB.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    claims = verify_firebase_token(token)
    if claims is None:
        return None

    uid = claims.get("uid") or claims.get("user_id") or ""
    email = claims.get("email", "")
    name = claims.get("name")

    if not uid:
        return None

    # Auto-provision user in DB
    user = UserRepo.get_or_create(uid=uid, email=email, name=name)
    return {"id": user.id, "email": user.email, "name": user.name}


def get_current_user_id(request: Request) -> str:
    """
    Backwards-compatible helper.
    Returns the authenticated user's ID, falling back to header/query/default.
    """
    user = get_current_user(request)
    if user:
        return user["id"]

    # Legacy fallbacks
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return user_id
    user_id = request.query_params.get("user_id")
    if user_id:
        return user_id
    return "default_user"


def require_auth(request: Request) -> str:
    """FastAPI Depends — raises 401 if not authenticated.  Returns user uid."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user["id"]

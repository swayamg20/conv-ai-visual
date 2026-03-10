"""
Authentication and user identity middleware.
"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt

from .config import config
from .models import UserModel, UserRepo


def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    """Create a JWT access token with an expiry claim."""
    expire = datetime.utcnow() + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def _extract_user_from_request(request: Request) -> Optional[UserModel]:
    """Parse the Authorization header and return the user, or None."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        user_id: str = payload.get("sub", "")
        if not user_id:
            return None
    except JWTError:
        return None
    return UserRepo.get_by_id(user_id)


def get_current_user(request: Request) -> UserModel:
    """
    FastAPI dependency — requires a valid JWT.
    Returns the authenticated UserModel or a 401 JSONResponse.
    """
    user = _extract_user_from_request(request)
    if user is None:
        return None  # Caller checks and returns 401
    return user


def get_current_user_optional(request: Request) -> Optional[UserModel]:
    """
    FastAPI dependency — returns the user if a valid token is present, else None.
    """
    return _extract_user_from_request(request)


def get_current_user_id(request: Request) -> str:
    """
    Backwards-compatible helper.
    Returns the authenticated user's ID, falling back to header/query/default.
    """
    user = _extract_user_from_request(request)
    if user:
        return user.id

    # Legacy fallbacks
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return user_id
    user_id = request.query_params.get("user_id")
    if user_id:
        return user_id
    return "default_user"

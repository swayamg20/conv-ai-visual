"""
Authentication and user identity middleware.
"""
from fastapi import Request


def get_current_user_id(request: Request) -> str:
    """
    Extract user ID from request.
    Currently returns a constant - replace with auth logic later.
    
    Priority:
    1. X-User-ID header
    2. user_id query parameter
    3. Default fallback
    
    Future implementations:
    - JWT token: decode token from Authorization header
    - Session: lookup session cookie
    - API key: map API key to user
    
    Example JWT implementation:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            return payload["user_id"]
    """
    # Check for user_id in headers first
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return user_id
    
    # Check query params
    user_id = request.query_params.get("user_id")
    if user_id:
        return user_id
    
    # Default fallback - replace with auth later
    return "default_user"


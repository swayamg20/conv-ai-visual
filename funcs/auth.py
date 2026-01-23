from fastapi import Request

def get_current_user_id(request: Request) -> str:
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return user_id
    user_id = request.query_params.get("user_id")
    if user_id:
        return user_id
    return "default_user" # Default fallback - replace with auth later


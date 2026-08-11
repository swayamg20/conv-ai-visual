"""Authenticated identity routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from murmur.api.dependencies import CurrentUserDependency

router = APIRouter(tags=["auth"])


@router.get("/api/auth/me")
async def auth_me(user: CurrentUserDependency) -> JSONResponse:
    return JSONResponse(
        {
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
            }
        }
    )

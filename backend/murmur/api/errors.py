"""Stable JSON error responses for API dependencies and routers."""

from fastapi import Request
from fastapi.responses import JSONResponse

from murmur.core import (
    InvalidRequestError,
    MurmurError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceInitializationError,
)


class ApiError(Exception):
    """An expected API failure with the legacy ``{"error": ...}`` response shape."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status_code)


async def domain_error_handler(_request: Request, exc: MurmurError) -> JSONResponse:
    status_codes = {
        ResourceNotFoundError: 404,
        PermissionDeniedError: 403,
        InvalidRequestError: 400,
        ServiceInitializationError: 500,
    }
    status_code = status_codes.get(type(exc), 500)
    return JSONResponse({"error": str(exc)}, status_code=status_code)

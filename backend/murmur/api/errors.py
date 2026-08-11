"""Stable JSON error responses for API dependencies and routers."""

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An expected API failure with the legacy ``{"error": ...}`` response shape."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status_code)

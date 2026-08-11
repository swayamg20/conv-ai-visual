"""FastAPI application factory and lifecycle composition."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from funcs.config import config
from funcs.search import register_web_search_tool
from murmur.api.errors import ApiError, api_error_handler, domain_error_handler
from murmur.api.routers import api_router
from murmur.chat import ChatService
from murmur.core import MurmurError
from murmur.persistence import init_db
from murmur.runtime import RuntimeRegistry

logger = logging.getLogger(__name__)


def _cors_origins() -> list[str]:
    configured = getattr(config, "ALLOWED_CORS_ORIGINS", None)
    if configured:
        raw_origins = configured.split(",") if isinstance(configured, str) else list(configured)
    else:
        raw_origins = os.getenv("ALLOWED_CORS_ORIGINS", "http://localhost:3000").split(",")

    origins = [origin.strip() for origin in raw_origins if origin and origin.strip()]
    return origins or ["http://localhost:3000"]


def create_application(
    *,
    runtime: RuntimeRegistry,
    session_sweeper: Callable[[], Awaitable[None]],
    chat_service: ChatService | None = None,
) -> FastAPI:
    """Build the HTTP application around an explicit runtime owner."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        try:
            register_web_search_tool()
        except Exception as exc:
            logger.warning("Failed to register web_search tool: %s", exc)

        if runtime.sweeper_task is None or runtime.sweeper_task.done():
            runtime.sweeper_task = asyncio.create_task(session_sweeper())
            logger.info("Session sweeper started")

        try:
            yield
        finally:
            await runtime.shutdown()
            logger.info("Application runtime shut down")

    app = FastAPI(lifespan=lifespan)
    app.state.runtime = runtime
    app.state.chat_service = chat_service or ChatService(runtime)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(MurmurError, domain_error_handler)
    app.include_router(api_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app

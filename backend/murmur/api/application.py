"""FastAPI application factory and lifecycle composition."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from murmur.api.errors import ApiError, api_error_handler, domain_error_handler
from murmur.api.routers import api_router
from murmur.canvas.state import register_canvas_tool
from murmur.chat import ChatService
from murmur.core import MurmurError
from murmur.core.config import config
from murmur.live_scene import SceneAuthoringAdmission, SceneAuthoringService
from murmur.live_scene.provider import scene_model_client_options
from murmur.llm.factory import create_llm_client
from murmur.persistence import init_db
from murmur.runtime import RuntimeRegistry
from murmur.runtime.supervisor import SessionSupervisor
from murmur.tools.search import register_web_search_tool
from murmur.voice import VoiceService
from murmur.voice.bootstrap import VoiceBootstrapper
from murmur.voice.livekit_control import create_default_voice_bootstrap_service

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
    runtime: RuntimeRegistry | None = None,
    chat_service: ChatService | None = None,
    voice_service: VoiceService | None = None,
    voice_bootstrap_service: VoiceBootstrapper | None = None,
    scene_authoring_service: SceneAuthoringService | None = None,
    scene_authoring_admission: SceneAuthoringAdmission | None = None,
    scene_authoring_enabled: bool | None = None,
) -> FastAPI:
    """Build the HTTP application around an explicit runtime owner."""
    runtime = runtime or RuntimeRegistry()
    chat_service = chat_service or ChatService(runtime)
    voice_service = voice_service or VoiceService(runtime)
    voice_bootstrap_service = voice_bootstrap_service or create_default_voice_bootstrap_service()
    if scene_authoring_service is None:
        scene_provider = config.MURMUR_SCENE_LLM_PROVIDER
        scene_model = config.MURMUR_SCENE_LLM_MODEL

        def create_scene_client():
            return create_llm_client(
                scene_provider,
                model=scene_model,
                **scene_model_client_options(scene_provider, scene_model),
            )

        scene_authoring_service = SceneAuthoringService(
            client_factory=create_scene_client,
            temperature=config.MURMUR_SCENE_LLM_TEMPERATURE,
            max_tokens=config.MURMUR_SCENE_LLM_MAX_TOKENS,
            timeout_seconds=config.MURMUR_SCENE_LLM_TIMEOUT_SECONDS,
        )
    scene_authoring_admission = scene_authoring_admission or SceneAuthoringAdmission(
        global_limit=config.MURMUR_SCENE_GLOBAL_CONCURRENCY,
        per_user_limit=config.MURMUR_SCENE_PER_USER_CONCURRENCY,
        requests_per_minute=config.MURMUR_SCENE_REQUESTS_PER_MINUTE,
    )
    if scene_authoring_enabled is None:
        scene_authoring_enabled = config.MURMUR_SCENE_ENABLED
    supervisor = SessionSupervisor(chat_service, voice_service)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        voice_service.start()
        try:
            register_web_search_tool()
            register_canvas_tool()
        except Exception as exc:
            logger.warning("Failed to register built-in tools: %s", exc)

        if runtime.sweeper_task is None or runtime.sweeper_task.done():
            runtime.sweeper_task = asyncio.create_task(supervisor.run())
            logger.info("Session supervisor started")

        try:
            yield
        finally:
            shutdown_results = await asyncio.gather(
                runtime.shutdown(),
                voice_bootstrap_service.aclose(),
                return_exceptions=True,
            )
            for result in shutdown_results:
                if isinstance(result, BaseException):
                    logger.error(
                        "Application component shutdown failed",
                        exc_info=(type(result), result, result.__traceback__),
                    )
            logger.info("Application runtime shut down")

    app = FastAPI(lifespan=lifespan)
    app.state.runtime = runtime
    app.state.chat_service = chat_service
    app.state.voice_service = voice_service
    app.state.voice_bootstrap_service = voice_bootstrap_service
    app.state.scene_authoring_service = scene_authoring_service
    app.state.scene_authoring_admission = scene_authoring_admission
    app.state.scene_authoring_enabled = scene_authoring_enabled
    app.state.session_supervisor = supervisor
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

"""HTTP routers for the Murmur API."""

from fastapi import APIRouter

from murmur.api.routers import agents, auth, chat, observability, resources, sessions, voice

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(agents.router)
api_router.include_router(resources.router)
api_router.include_router(sessions.router)
api_router.include_router(observability.router)
api_router.include_router(chat.router)
api_router.include_router(voice.router)

__all__ = ["api_router"]

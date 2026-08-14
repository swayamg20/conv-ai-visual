"""FastAPI dependencies for trusted identity and owned resources."""

from typing import Annotated, TypedDict, cast

from fastapi import Depends, Request

from murmur.api.authentication import FirebaseAuthenticationUnavailable, get_current_user
from murmur.api.errors import ApiError
from murmur.chat import ChatService
from murmur.persistence.models import AgentModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.runtime import RuntimeRegistry
from murmur.voice import VoiceService
from murmur.voice.bootstrap import VoiceBootstrapper


class CurrentUser(TypedDict):
    id: str
    email: str
    name: str | None


def get_authenticated_user(request: Request) -> CurrentUser:
    """Resolve the bearer token to the server-provisioned user identity."""
    try:
        user = get_current_user(request)
    except FirebaseAuthenticationUnavailable:
        raise ApiError(503, "Authentication is unavailable") from None
    if user is None:
        raise ApiError(401, "Not authenticated")
    return cast(CurrentUser, user)


CurrentUserDependency = Annotated[CurrentUser, Depends(get_authenticated_user)]


def require_owned_agent(agent_id: str, user: CurrentUser) -> AgentModel:
    """Resolve an agent and enforce ownership."""
    agent = AgentRepo.get_by_id(agent_id)
    if not agent:
        raise ApiError(404, "Agent not found")
    if agent.user_id != user["id"]:
        raise ApiError(403, "Forbidden")
    return agent


def get_owned_agent(agent_id: str, user: CurrentUserDependency) -> AgentModel:
    """FastAPI adapter for owned path agents."""
    return require_owned_agent(agent_id, user)


OwnedAgentDependency = Annotated[AgentModel, Depends(get_owned_agent)]


def get_runtime(request: Request) -> RuntimeRegistry:
    return cast(RuntimeRegistry, request.app.state.runtime)


RuntimeDependency = Annotated[RuntimeRegistry, Depends(get_runtime)]


def get_chat_service(request: Request) -> ChatService:
    return cast(ChatService, request.app.state.chat_service)


ChatServiceDependency = Annotated[ChatService, Depends(get_chat_service)]


def get_voice_service(request: Request) -> VoiceService:
    return cast(VoiceService, request.app.state.voice_service)


VoiceServiceDependency = Annotated[VoiceService, Depends(get_voice_service)]


def get_voice_bootstrap_service(request: Request) -> VoiceBootstrapper:
    return cast(VoiceBootstrapper, request.app.state.voice_bootstrap_service)


VoiceBootstrapServiceDependency = Annotated[
    VoiceBootstrapper,
    Depends(get_voice_bootstrap_service),
]

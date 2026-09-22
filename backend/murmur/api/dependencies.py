"""FastAPI dependencies for trusted identity and owned resources."""

from typing import Annotated, TypedDict, cast

from fastapi import Depends, Request

from murmur.api.authentication import FirebaseAuthenticationUnavailable, get_current_user
from murmur.api.errors import ApiError
from murmur.chat import ChatAdmission, ChatService
from murmur.live_scene import SceneAuthoringAdmission, SceneAuthoringService
from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.runtime import RuntimeRegistry
from murmur.voice import VoiceService
from murmur.voice.bootstrap import VoiceBootstrapper
from murmur.voice.websocket_ticket import WebSocketVoiceBootstrapService


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


def require_owned_session(session_id: str, user: CurrentUser) -> SessionModel:
    """Resolve a persistent session and its agent from server-owned records."""
    session = SessionRepo.get_by_id(session_id)
    if not session:
        raise ApiError(404, "Session not found")
    if session.user_id != user["id"]:
        raise ApiError(403, "Forbidden")

    # The persistent session is the authority for the agent binding. Never
    # accept an agent identity from a request body at this boundary.
    agent = require_owned_agent(session.agent_id, user)
    if agent.id != session.agent_id:
        raise ApiError(403, "Forbidden")
    return session


def get_owned_session(session_id: str, user: CurrentUserDependency) -> SessionModel:
    """FastAPI adapter for an authenticated, persistent session path."""
    return require_owned_session(session_id, user)


OwnedSessionDependency = Annotated[SessionModel, Depends(get_owned_session)]


def get_runtime(request: Request) -> RuntimeRegistry:
    return cast(RuntimeRegistry, request.app.state.runtime)


RuntimeDependency = Annotated[RuntimeRegistry, Depends(get_runtime)]


def get_chat_service(request: Request) -> ChatService:
    return cast(ChatService, request.app.state.chat_service)


ChatServiceDependency = Annotated[ChatService, Depends(get_chat_service)]


def get_chat_admission(request: Request) -> ChatAdmission:
    return cast(ChatAdmission, request.app.state.chat_admission)


ChatAdmissionDependency = Annotated[ChatAdmission, Depends(get_chat_admission)]


def get_scene_authoring_service(request: Request) -> SceneAuthoringService:
    return cast(SceneAuthoringService, request.app.state.scene_authoring_service)


SceneAuthoringServiceDependency = Annotated[
    SceneAuthoringService,
    Depends(get_scene_authoring_service),
]


def get_scene_authoring_admission(request: Request) -> SceneAuthoringAdmission:
    if not bool(getattr(request.app.state, "scene_authoring_enabled", False)):
        raise ApiError(503, "Live scene generation is not enabled")
    return cast(SceneAuthoringAdmission, request.app.state.scene_authoring_admission)


SceneAuthoringAdmissionDependency = Annotated[
    SceneAuthoringAdmission,
    Depends(get_scene_authoring_admission),
]


def get_voice_service(request: Request) -> VoiceService:
    return cast(VoiceService, request.app.state.voice_service)


VoiceServiceDependency = Annotated[VoiceService, Depends(get_voice_service)]


def get_voice_bootstrap_service(request: Request) -> VoiceBootstrapper:
    return cast(VoiceBootstrapper, request.app.state.voice_bootstrap_service)


VoiceBootstrapServiceDependency = Annotated[
    VoiceBootstrapper,
    Depends(get_voice_bootstrap_service),
]


def get_websocket_voice_service(request: Request) -> WebSocketVoiceBootstrapService:
    return cast(WebSocketVoiceBootstrapService, request.app.state.websocket_voice_service)


WebSocketVoiceServiceDependency = Annotated[
    WebSocketVoiceBootstrapService,
    Depends(get_websocket_voice_service),
]

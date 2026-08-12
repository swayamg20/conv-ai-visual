"""Request schemas shared by Murmur API routers."""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.types import StringConstraints

from murmur.voice.contracts import ContractId

UUID4String = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ),
]


class Offer(BaseModel):
    sdp: str
    type: str
    canvas_mode: bool = False
    session_id: str | None = None
    agent_id: str | None = None


class ChatMessage(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str | None = None
    canvas_mode: bool | None = None
    agent_id: str | None = None


class CanvasModeRequest(BaseModel):
    enabled: bool
    custom_prompt: str | None = None


class CreateSessionRequest(BaseModel):
    agent_id: str


class CreateAgentRequest(BaseModel):
    name: str
    description: str | None = None
    persona: dict[str, Any] | None = None
    capabilities: list[str] = Field(default_factory=lambda: ["canvas"])
    icon: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    persona: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    icon: str | None = None
    is_default: bool | None = None


class VoiceSessionBootstrapRequest(BaseModel):
    """Retry-stable request for one existing, owned persistent session."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID4String
    voice_call_id: UUID4String


class VoiceSessionEndRequest(BaseModel):
    """Exact retry-safe assignment scope to release."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID4String
    voice_call_id: UUID4String


class VoiceSessionBootstrapResponse(BaseModel):
    """Browser-safe LiveKit assignment; server secrets are never represented."""

    model_config = ConfigDict(extra="forbid")

    runtime: Literal["livekit_v2"]
    profile_id: ContractId
    server_url: str
    room_name: ContractId
    participant_token: str
    participant_identity: ContractId
    agent_participant_identity: ContractId
    session_id: UUID4String
    agent_id: UUID4String
    voice_call_id: UUID4String
    dispatch_id: ContractId
    worker_name: ContractId
    event_topic: ContractId
    trace_id: UUID4String
    expires_at: AwareDatetime

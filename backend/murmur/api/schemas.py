"""Request schemas shared by Murmur API routers."""

from typing import Any

from pydantic import BaseModel, Field


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

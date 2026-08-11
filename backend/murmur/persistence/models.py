"""SQLModel table declarations for Murmur persistence."""

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlmodel import Field, SQLModel

from murmur.persistence.clock import utc_now


def _load_json(value: str | None, default: Any = None) -> Any:
    """Deserialize a JSON string field, returning ``default`` for an empty value."""
    if not value:
        return default
    return json.loads(value)


class EpisodicMemoryModel(SQLModel, table=True):
    """Layer 2 conversation summaries."""

    __tablename__ = "episodic_memory"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: str | None = None
    summary: str
    turn_count: int | None = None
    meta_json: str | None = Field(default=None, sa_column_kwargs={"name": "metadata"})
    created_at: datetime = Field(default_factory=utc_now)

    def get_meta(self) -> dict:
        """Return metadata as a dictionary."""
        return _load_json(self.meta_json, {})

    def set_meta(self, value: dict) -> None:
        """Serialize metadata into its database field."""
        self.meta_json = json.dumps(value) if value else None


class UserProfileModel(SQLModel, table=True):
    """Layer 4 user profile and canonical identity."""

    __tablename__ = "user_profile"

    user_id: str = Field(primary_key=True)
    name: str | None = None
    timezone: str | None = None
    preferences_json: str | None = Field(default="{}", sa_column_kwargs={"name": "preferences"})
    facts_json: str | None = Field(default="{}", sa_column_kwargs={"name": "facts"})
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def preferences(self) -> dict:
        return _load_json(self.preferences_json, {})

    @preferences.setter
    def preferences(self, value: dict) -> None:
        self.preferences_json = json.dumps(value) if value else "{}"

    @property
    def facts(self) -> dict:
        return _load_json(self.facts_json, {})

    @facts.setter
    def facts(self, value: dict) -> None:
        self.facts_json = json.dumps(value) if value else "{}"


class DecisionMemoryModel(SQLModel, table=True):
    """Agentic decision tracking."""

    __tablename__ = "decision_memory"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: str | None = None
    action: str
    tool_used: str | None = None
    success: bool = True
    context: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class UserModel(SQLModel, table=True):
    """Registered user account keyed by Firebase UID."""

    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    # Legacy SQLite schemas still require this column. Firebase users store a sentinel.
    password_hash: str = Field(default="__firebase_auth__")
    name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    is_active: bool = True


class AgentModel(SQLModel, table=True):
    """User-created AI agent with a compiled system prompt."""

    __tablename__ = "agents"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    name: str
    description: str | None = None
    system_prompt: str
    persona_json: str | None = None
    capabilities_json: str = '["canvas"]'
    icon: str | None = None
    is_default: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def get_persona(self) -> dict:
        return _load_json(self.persona_json, {})

    def get_capabilities(self) -> list[str]:
        return _load_json(self.capabilities_json, ["canvas"])


class SessionModel(SQLModel, table=True):
    """Persistent chat session for cross-session memory."""

    __tablename__ = "sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    title: str | None = None
    summary: str | None = None
    message_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessageModel(SQLModel, table=True):
    """Persisted conversation message for replay and context."""

    __tablename__ = "conversation_messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str
    content: str
    tool_calls_json: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class LLMCallLogModel(SQLModel, table=True):
    """End-to-end log for an LLM API call."""

    __tablename__ = "llm_call_log"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    user_message: str
    llm_provider: str
    llm_model: str
    tool_calls_json: str | None = None
    response_text: str | None = None
    latency_total_ms: float | None = None
    latency_llm_ms: float | None = None
    latency_tool_ms: float | None = None
    latency_stream_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def get_tool_calls(self) -> list:
        return _load_json(self.tool_calls_json, [])


class VoicePipelineLogModel(SQLModel, table=True):
    """End-to-end log for a voice-pipeline turn."""

    __tablename__ = "voice_pipeline_log"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    mode: str = Field(default="voice")
    user_message: str = ""
    response_text: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    latency_vad_ms: float | None = None
    latency_stt_ms: float | None = None
    latency_turn_detection_ms: float | None = None
    latency_stt_to_llm_ms: float | None = None
    latency_llm_ms: float | None = None
    latency_llm_first_token_ms: float | None = None
    latency_tool_ms: float | None = None
    latency_tts_ms: float | None = None
    latency_tts_first_chunk_ms: float | None = None
    latency_total_ms: float | None = None
    tool_calls_json: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tts_chunks_sent: int | None = None
    tts_interrupted: bool = False
    smart_turn_used: bool = False
    smart_turn_result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def get_tool_calls(self) -> list:
        return _load_json(self.tool_calls_json, [])


class TTSResilienceLogModel(SQLModel, table=True):
    """Sidecar retry and fallback metadata for a voice log."""

    __tablename__ = "tts_resilience_log"

    id: int | None = Field(default=None, primary_key=True)
    voice_log_id: int = Field(index=True, foreign_key="voice_pipeline_log.id")
    provider_used: str | None = None
    retry_count: int = 0
    fallback_used: bool = False
    fallback_provider: str | None = None
    final_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ResourceModel(SQLModel, table=True):
    """Uploaded resource attached to an agent."""

    __tablename__ = "resources"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    agent_id: str = Field(index=True, foreign_key="agents.id")
    user_id: str = Field(index=True, foreign_key="users.id")
    name: str
    resource_type: str
    content_text: str = ""
    chunk_count: int = 0
    size_bytes: int | None = None
    status: str = "processing"
    created_at: datetime = Field(default_factory=utc_now)


class ResourceChunkModel(SQLModel, table=True):
    """Chunked resource text used for retrieval."""

    __tablename__ = "resource_chunks"

    id: int | None = Field(default=None, primary_key=True)
    resource_id: str = Field(index=True, foreign_key="resources.id")
    chunk_index: int
    content: str
    page_number: int | None = None


class TopicMasteryModel(SQLModel, table=True):
    """Per-session topic mastery signal for the struggle heatmap."""

    __tablename__ = "topic_mastery"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    session_id: str = Field(index=True, foreign_key="sessions.id")
    topic: str
    chapter: str | None = None
    signal_type: str
    details: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ToolModel(SQLModel, table=True):
    """Tool definition used for model function calling."""

    __tablename__ = "tools"

    name: str = Field(primary_key=True)
    description: str
    parameters_json: str = Field(sa_column_kwargs={"name": "parameters"})
    handler_module: str | None = None
    handler_function: str | None = None
    code: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def parameters(self) -> dict:
        return _load_json(self.parameters_json, {})

    @parameters.setter
    def parameters(self, value: dict) -> None:
        self.parameters_json = json.dumps(value)

    def to_openai_schema(self) -> dict:
        """Return the OpenAI function-calling representation."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict:
        """Return the Anthropic tool representation."""
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}

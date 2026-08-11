"""
SQLModel database models.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Field, Session, SQLModel, select

from funcs.database import get_session, init_db

logger = logging.getLogger(__name__)
_RESOURCE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_RESOURCE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "please",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
}


def _load_json(value: str | None, default: Any = None) -> Any:
    """Deserialize a JSON string field, returning `default` if None or empty."""
    if not value:
        return default
    return json.loads(value)


# ============== Models ==============


class EpisodicMemoryModel(SQLModel, table=True):
    """Layer 2: Conversation summaries."""

    __tablename__ = "episodic_memory"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: str | None = None
    summary: str
    turn_count: int | None = None
    meta_json: str | None = Field(default=None, sa_column_kwargs={"name": "metadata"})
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_meta(self) -> dict:
        """Get metadata dict."""
        return _load_json(self.meta_json, {})

    def set_meta(self, value: dict) -> None:
        """Set metadata dict."""
        self.meta_json = json.dumps(value) if value else None


class UserProfileModel(SQLModel, table=True):
    """Layer 4: User profile - canonical identity."""

    __tablename__ = "user_profile"

    user_id: str = Field(primary_key=True)
    name: str | None = None
    timezone: str | None = None
    preferences_json: str | None = Field(default="{}", sa_column_kwargs={"name": "preferences"})
    facts_json: str | None = Field(default="{}", sa_column_kwargs={"name": "facts"})
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserModel(SQLModel, table=True):
    """Registered user accounts.  id is the Firebase uid."""

    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    # Legacy SQLite schemas still require password_hash to be present.
    # Firebase-auth users do not use local passwords, so we persist a sentinel.
    password_hash: str = Field(default="__firebase_auth__")
    name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class AgentModel(SQLModel, table=True):
    """User-created AI agents with compiled system prompts."""

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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def get_persona(self) -> dict:
        return _load_json(self.persona_json, {})

    def get_capabilities(self) -> list[str]:
        return _load_json(self.capabilities_json, ["canvas"])


class SessionModel(SQLModel, table=True):
    """Persistent chat sessions for cross-session memory."""

    __tablename__ = "sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    title: str | None = None
    summary: str | None = None
    message_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessageModel(SQLModel, table=True):
    """Persisted conversation messages for session replay and cross-session context."""

    __tablename__ = "conversation_messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls_json: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LLMCallLogModel(SQLModel, table=True):
    """End-to-end logging for LLM API calls."""

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
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_tool_calls(self) -> list:
        return _load_json(self.tool_calls_json, [])


class VoicePipelineLogModel(SQLModel, table=True):
    """End-to-end logging for voice pipeline turns.

    Captures every stage: speech detection → STT → turn detection → LLM → tool calls → TTS → playback.
    """

    __tablename__ = "voice_pipeline_log"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    mode: str = Field(default="voice")  # "voice" or "chat"

    user_message: str = ""
    response_text: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None

    # Stage latencies (milliseconds)
    latency_vad_ms: float | None = None  # VAD speech detection time
    latency_stt_ms: float | None = None  # Time from audio start to final transcript
    latency_turn_detection_ms: float | None = None  # Smart Turn analysis time
    latency_stt_to_llm_ms: float | None = None  # Gap between STT final and LLM call start
    latency_llm_ms: float | None = None  # LLM inference (incl tool call rounds)
    latency_llm_first_token_ms: float | None = None  # Time to first LLM token (TTFT)
    latency_tool_ms: float | None = None  # Total tool execution time
    latency_tts_ms: float | None = None  # TTS generation time (all chunks)
    latency_tts_first_chunk_ms: float | None = None  # Time to first TTS audio chunk (TTFB)
    latency_total_ms: float | None = None  # End-to-end: speech start → last TTS chunk

    # Pipeline metadata
    tool_calls_json: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tts_chunks_sent: int | None = None
    tts_interrupted: bool = False
    smart_turn_used: bool = False
    smart_turn_result: str | None = None  # "complete", "incomplete", "fallback"

    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_tool_calls(self) -> list:
        return _load_json(self.tool_calls_json, [])


class TTSResilienceLogModel(SQLModel, table=True):
    """Sidecar metadata for retries and fallback without altering existing voice log rows."""

    __tablename__ = "tts_resilience_log"

    id: int | None = Field(default=None, primary_key=True)
    voice_log_id: int = Field(index=True, foreign_key="voice_pipeline_log.id")
    provider_used: str | None = None
    retry_count: int = 0
    fallback_used: bool = False
    fallback_provider: str | None = None
    final_error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ResourceModel(SQLModel, table=True):
    """Uploaded resources (PDFs, URLs, text) attached to an agent."""

    __tablename__ = "resources"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    agent_id: str = Field(index=True, foreign_key="agents.id")
    user_id: str = Field(index=True, foreign_key="users.id")
    name: str  # Original filename or URL
    resource_type: str  # "pdf", "url", "text"
    content_text: str = ""  # Extracted full text
    chunk_count: int = 0
    size_bytes: int | None = None
    status: str = "processing"  # "processing", "ready", "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ResourceChunkModel(SQLModel, table=True):
    """Chunked text from a resource for retrieval."""

    __tablename__ = "resource_chunks"

    id: int | None = Field(default=None, primary_key=True)
    resource_id: str = Field(index=True, foreign_key="resources.id")
    chunk_index: int
    content: str  # ~500 token chunk (~2000 chars)
    page_number: int | None = None


class TopicMasteryModel(SQLModel, table=True):
    """Per-session topic mastery signals for the struggle heatmap."""

    __tablename__ = "topic_mastery"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    session_id: str = Field(index=True, foreign_key="sessions.id")
    topic: str  # "Newton's Second Law"
    chapter: str | None = None  # "Laws of Motion"
    signal_type: str  # "understood", "struggled", "unclear"
    details: str | None = None  # Brief note on what happened
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ToolModel(SQLModel, table=True):
    """Tool definitions for function calling."""

    __tablename__ = "tools"

    name: str = Field(primary_key=True)
    description: str
    parameters_json: str = Field(sa_column_kwargs={"name": "parameters"})
    handler_module: str | None = None
    handler_function: str | None = None
    code: str | None = None  # Python code for the function (alternative to module/function)
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def parameters(self) -> dict:
        return _load_json(self.parameters_json, {})

    @parameters.setter
    def parameters(self, value: dict) -> None:
        self.parameters_json = json.dumps(value)

    def to_openai_schema(self) -> dict:
        """OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict:
        """Anthropic tool format."""
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}


def _resource_query_terms(query: str) -> list[str]:
    """Normalize a resource search query into lexical terms."""
    terms: list[str] = []
    for token in _RESOURCE_TOKEN_RE.findall(query.lower()):
        if len(token) < 2 or token in _RESOURCE_STOPWORDS or token in terms:
            continue
        terms.append(token)
    return terms


def _score_resource_chunk(content: str, query_terms: list[str], normalized_query: str) -> float:
    """Score a resource chunk using lightweight lexical signals."""
    if not content or not query_terms:
        return 0.0

    content_lower = content.lower()
    token_counts = Counter(_RESOURCE_TOKEN_RE.findall(content_lower))
    if not token_counts:
        return 0.0

    matched_terms = 0
    term_hits = 0.0
    for term in query_terms:
        occurrences = token_counts.get(term, 0)
        if occurrences:
            matched_terms += 1
            term_hits += min(occurrences, 3)

    if not matched_terms:
        return 0.0

    coverage = matched_terms / len(query_terms)
    density = term_hits / max(1.0, len(token_counts) ** 0.5)
    score = (coverage * 4.0) + (density * 2.0)

    if normalized_query and normalized_query in content_lower:
        score += 2.5

    lead_window = content_lower[:400]
    lead_hits = sum(1 for term in query_terms if term in lead_window)
    score += min(lead_hits, 3) * 0.35

    if len(content) < 400:
        score += 0.25

    return score


# ============== Repository Classes ==============


class EpisodicMemoryRepo:
    """Repository for episodic memory operations."""

    @staticmethod
    def save(
        user_id: str,
        summary: str,
        session_id: str | None = None,
        turn_count: int = 0,
        metadata: dict | None = None,
    ) -> EpisodicMemoryModel:
        with get_session() as session:
            record = EpisodicMemoryModel(
                user_id=user_id,
                session_id=session_id,
                summary=summary,
                turn_count=turn_count,
                meta_json=json.dumps(metadata) if metadata else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(user_id: str, limit: int = 5) -> list[EpisodicMemoryModel]:
        with get_session() as session:
            stmt = (
                select(EpisodicMemoryModel)
                .where(EpisodicMemoryModel.user_id == user_id)
                .order_by(EpisodicMemoryModel.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())


class UserProfileRepo:
    """Repository for user profile operations."""

    @staticmethod
    def get_or_create(user_id: str) -> UserProfileModel:
        with get_session() as session:
            profile = session.get(UserProfileModel, user_id)
            if not profile:
                profile = UserProfileModel(user_id=user_id)
                session.add(profile)
                session.commit()
                session.refresh(profile)
            return profile

    @staticmethod
    def get(user_id: str) -> UserProfileModel | None:
        with get_session() as session:
            return session.get(UserProfileModel, user_id)

    @staticmethod
    def update(user_id: str, **kwargs) -> UserProfileModel | None:
        with get_session() as session:
            profile = session.get(UserProfileModel, user_id)
            if not profile:
                return None

            for key, value in kwargs.items():
                if key == "preferences":
                    profile.preferences_json = json.dumps(value)
                elif key == "facts":
                    profile.facts_json = json.dumps(value)
                elif hasattr(profile, key):
                    setattr(profile, key, value)

            profile.updated_at = datetime.utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile


class DecisionMemoryRepo:
    """Repository for decision memory operations."""

    @staticmethod
    def log(
        user_id: str,
        action: str,
        session_id: str | None = None,
        tool_used: str | None = None,
        success: bool = True,
        context: str | None = None,
    ) -> DecisionMemoryModel:
        with get_session() as session:
            record = DecisionMemoryModel(
                user_id=user_id,
                session_id=session_id,
                action=action,
                tool_used=tool_used,
                success=success,
                context=context,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent_failures(user_id: str, limit: int = 5) -> list[DecisionMemoryModel]:
        with get_session() as session:
            stmt = (
                select(DecisionMemoryModel)
                .where(DecisionMemoryModel.user_id == user_id)
                .where(DecisionMemoryModel.success.is_(False))
                .order_by(DecisionMemoryModel.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def has_recent_failure(user_id: str, action: str, within_minutes: int = 5) -> bool:
        with get_session() as session:
            cutoff = datetime.utcnow()
            stmt = (
                select(DecisionMemoryModel)
                .where(DecisionMemoryModel.user_id == user_id)
                .where(DecisionMemoryModel.action == action)
                .where(DecisionMemoryModel.success.is_(False))
                .order_by(DecisionMemoryModel.created_at.desc())
                .limit(1)
            )
            result = session.exec(stmt).first()
            if not result:
                return False
            # Check if within time window
            diff = (cutoff - result.created_at).total_seconds() / 60
            return diff <= within_minutes


class ToolRepo:
    """Repository for tool operations."""

    _openai_schema_cache: list[dict] | None = None
    _openai_schema_cache_key: tuple[int, datetime | None] | None = None

    @staticmethod
    def _invalidate_schema_cache():
        ToolRepo._openai_schema_cache = None
        ToolRepo._openai_schema_cache_key = None

    @staticmethod
    def _get_enabled_schema_cache_key() -> tuple[int, datetime | None]:
        with get_session() as session:
            stmt = select(
                func.count(ToolModel.name),
                func.max(ToolModel.updated_at),
            ).where(ToolModel.enabled.is_(True))
            count, latest_updated_at = session.exec(stmt).one()
            return int(count or 0), latest_updated_at

    @staticmethod
    def upsert(
        name: str,
        description: str,
        parameters: dict,
        handler_module: str | None = None,
        handler_function: str | None = None,
        code: str | None = None,
        enabled: bool = True,
    ) -> ToolModel:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if tool:
                tool.description = description
                tool.parameters_json = json.dumps(parameters)
                tool.handler_module = handler_module
                tool.code = code
                tool.handler_function = handler_function
                tool.enabled = enabled
                tool.updated_at = datetime.utcnow()
            else:
                tool = ToolModel(
                    name=name,
                    description=description,
                    parameters_json=json.dumps(parameters),
                    handler_module=handler_module,
                    handler_function=handler_function,
                    code=code,
                    enabled=enabled,
                )
            session.add(tool)
            session.commit()
            session.refresh(tool)
            ToolRepo._invalidate_schema_cache()
            return tool

    @staticmethod
    def get(name: str) -> ToolModel | None:
        with get_session() as session:
            return session.get(ToolModel, name)

    @staticmethod
    def get_enabled(name: str) -> ToolModel | None:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            return tool if tool and tool.enabled else None

    @staticmethod
    def list_all(enabled_only: bool = True) -> list[ToolModel]:
        with get_session() as session:
            stmt = select(ToolModel).order_by(ToolModel.name)
            if enabled_only:
                stmt = stmt.where(ToolModel.enabled.is_(True))
            return list(session.exec(stmt).all())

    @staticmethod
    def delete(name: str) -> bool:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if tool:
                session.delete(tool)
                session.commit()
                ToolRepo._invalidate_schema_cache()
                return True
            return False

    @staticmethod
    def set_enabled(name: str, enabled: bool) -> bool:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if tool:
                tool.enabled = enabled
                tool.updated_at = datetime.utcnow()
                session.add(tool)
                session.commit()
                ToolRepo._invalidate_schema_cache()
                return True
            return False

    @staticmethod
    def to_openai_format() -> list[dict]:
        """
        Get all enabled tools in OpenAI format.
        Uses an in-memory cache invalidated on tool mutations.
        """
        cache_key = ToolRepo._get_enabled_schema_cache_key()
        if (
            ToolRepo._openai_schema_cache is not None
            and ToolRepo._openai_schema_cache_key == cache_key
        ):
            return ToolRepo._openai_schema_cache

        tools = ToolRepo.list_all(enabled_only=True)
        formatted = [t.to_openai_schema() for t in tools]
        ToolRepo._openai_schema_cache = formatted
        ToolRepo._openai_schema_cache_key = cache_key
        return formatted

    @staticmethod
    def to_anthropic_format() -> list[dict]:
        """Get all enabled tools in Anthropic format."""
        tools = ToolRepo.list_all(enabled_only=True)
        return [t.to_anthropic_schema() for t in tools]


class UserRepo:
    """Repository for user account operations."""

    @staticmethod
    def get_or_create(uid: str, email: str, name: str | None = None) -> UserModel:
        """Return existing user by uid, or create a new one (Firebase auto-provision)."""
        with get_session() as session:
            user = session.get(UserModel, uid)
            if user:
                updated = False
                if email and user.email != email:
                    user.email = email
                    updated = True
                if name != user.name:
                    user.name = name
                    updated = True
                if updated:
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                return user

            if email:
                stmt = select(UserModel).where(UserModel.email == email)
                existing_by_email = session.exec(stmt).first()
                if existing_by_email:
                    if name != existing_by_email.name:
                        existing_by_email.name = name
                        session.add(existing_by_email)
                        session.commit()
                        session.refresh(existing_by_email)
                    logger.info(
                        "Reusing legacy user row %s for Firebase login %s based on email match",
                        existing_by_email.id,
                        uid,
                    )
                    return existing_by_email

            user = UserModel(
                id=uid,
                email=email,
                name=name,
                password_hash="__firebase_auth__",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    @staticmethod
    def get_by_email(email: str) -> UserModel | None:
        with get_session() as session:
            stmt = select(UserModel).where(UserModel.email == email)
            return session.exec(stmt).first()

    @staticmethod
    def get_by_id(user_id: str) -> UserModel | None:
        with get_session() as session:
            return session.get(UserModel, user_id)


class AgentRepo:
    """Repository for agent CRUD operations."""

    @staticmethod
    def create(**kwargs) -> AgentModel:
        with get_session() as session:
            agent = AgentModel(**kwargs)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None:
        with get_session() as session:
            return session.get(AgentModel, agent_id)

    @staticmethod
    def list_by_user(user_id: str) -> list[AgentModel]:
        with get_session() as session:
            stmt = (
                select(AgentModel)
                .where(AgentModel.user_id == user_id)
                .order_by(AgentModel.created_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def update(agent_id: str, **kwargs) -> AgentModel | None:
        with get_session() as session:
            agent = session.get(AgentModel, agent_id)
            if not agent:
                return None
            for key, value in kwargs.items():
                if hasattr(agent, key):
                    setattr(agent, key, value)
            agent.updated_at = datetime.utcnow()
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

    @staticmethod
    def delete(agent_id: str) -> bool:
        with get_session() as session:
            agent = session.get(AgentModel, agent_id)
            if not agent:
                return False
            session.delete(agent)
            session.commit()
            return True

    @staticmethod
    def set_default(agent_id: str, user_id: str) -> None:
        """Unset all other defaults for this user, then set the given agent as default."""
        with get_session() as session:
            # Unset all defaults for this user
            stmt = (
                select(AgentModel)
                .where(AgentModel.user_id == user_id)
                .where(AgentModel.is_default.is_(True))
            )
            for agent in session.exec(stmt).all():
                agent.is_default = False
                session.add(agent)
            # Set the target agent as default
            target = session.get(AgentModel, agent_id)
            if target and target.user_id == user_id:
                target.is_default = True
                target.updated_at = datetime.utcnow()
                session.add(target)
            session.commit()


class SessionRepo:
    """Repository for session operations."""

    @staticmethod
    def create(user_id: str, agent_id: str) -> SessionModel:
        with get_session() as session:
            record = SessionModel(user_id=user_id, agent_id=agent_id)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None:
        with get_session() as session:
            return session.get(SessionModel, session_id)

    @staticmethod
    def list_by_agent(user_id: str, agent_id: str) -> list[SessionModel]:
        with get_session() as session:
            stmt = (
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .where(SessionModel.agent_id == agent_id)
                .order_by(SessionModel.updated_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def list_by_user(user_id: str, agent_id: str | None = None) -> list[SessionModel]:
        with get_session() as session:
            stmt = (
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .order_by(SessionModel.updated_at.desc())
            )
            if agent_id:
                stmt = stmt.where(SessionModel.agent_id == agent_id)
            return list(session.exec(stmt).all())

    @staticmethod
    def update_summary(session_id: str, summary: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.summary = summary
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()

    @staticmethod
    def update_title(session_id: str, title: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.title = title
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()

    @staticmethod
    def increment_message_count(session_id: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.message_count += 1
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()


class ConversationMessageRepo:
    """Repository for conversation message operations."""

    @staticmethod
    def save(
        session_id: str,
        agent_id: str,
        user_id: str,
        role: str,
        content: str,
        tool_calls_json: str | None = None,
    ) -> ConversationMessageModel:
        with get_session() as session:
            record = ConversationMessageModel(
                session_id=session_id,
                agent_id=agent_id,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls_json=tool_calls_json,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(session_id: str, limit: int = 20) -> list[ConversationMessageModel]:
        with get_session() as session:
            stmt = (
                select(ConversationMessageModel)
                .where(ConversationMessageModel.session_id == session_id)
                .order_by(ConversationMessageModel.created_at.desc())
                .limit(limit)
            )
            results = list(session.exec(stmt).all())
            # Return in chronological order
            results.reverse()
            return results

    @staticmethod
    def count_by_session(session_id: str) -> int:
        with get_session() as session:
            stmt = select(func.count(ConversationMessageModel.id)).where(
                ConversationMessageModel.session_id == session_id
            )
            return session.exec(stmt).one() or 0


class LLMCallLogRepo:
    """Repository for LLM call log operations."""

    @staticmethod
    def save(
        session_id: str,
        user_message: str,
        llm_provider: str,
        llm_model: str,
        user_id: str | None = None,
        tool_calls_json: str | None = None,
        response_text: str | None = None,
        latency_total_ms: float | None = None,
        latency_llm_ms: float | None = None,
        latency_tool_ms: float | None = None,
        latency_stream_ms: float | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        error: str | None = None,
    ) -> LLMCallLogModel:
        with get_session() as session:
            record = LLMCallLogModel(
                session_id=session_id,
                user_id=user_id,
                user_message=user_message,
                llm_provider=llm_provider,
                llm_model=llm_model,
                tool_calls_json=tool_calls_json,
                response_text=response_text,
                latency_total_ms=latency_total_ms,
                latency_llm_ms=latency_llm_ms,
                latency_tool_ms=latency_tool_ms,
                latency_stream_ms=latency_stream_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=error,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(user_id: str, limit: int = 50, offset: int = 0) -> list[LLMCallLogModel]:
        with get_session() as session:
            stmt = (
                select(LLMCallLogModel)
                .where(LLMCallLogModel.user_id == user_id)
                .order_by(LLMCallLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def get_stats(user_id: str) -> dict:
        """Get aggregate LLM stats scoped to one user."""
        with get_session() as db:
            user_scope = LLMCallLogModel.user_id == user_id
            total = db.exec(select(func.count(LLMCallLogModel.id)).where(user_scope)).one()
            avg_total = db.exec(
                select(func.avg(LLMCallLogModel.latency_total_ms)).where(user_scope)
            ).one()
            avg_llm = db.exec(
                select(func.avg(LLMCallLogModel.latency_llm_ms)).where(user_scope)
            ).one()
            avg_tool = db.exec(
                select(func.avg(LLMCallLogModel.latency_tool_ms)).where(user_scope)
            ).one()
            error_count = db.exec(
                select(func.count(LLMCallLogModel.id))
                .where(user_scope)
                .where(LLMCallLogModel.error.isnot(None))
            ).one()

            def _percentile(col, p: float) -> float | None:
                values = [
                    v
                    for v in db.exec(select(col).where(user_scope).where(col.isnot(None))).all()
                    if v is not None
                ]
                if not values:
                    return None
                values.sort()
                rank = round((p / 100.0) * (len(values) - 1))
                rank = min(max(rank, 0), len(values) - 1)
                return round(values[rank], 2)

            return {
                "total_calls": total or 0,
                "avg_latency_ms": round(avg_total or 0, 2),
                "avg_llm_latency_ms": round(avg_llm or 0, 2),
                "avg_tool_latency_ms": round(avg_tool or 0, 2),
                "p50_llm_latency_ms": _percentile(LLMCallLogModel.latency_llm_ms, 50),
                "p95_llm_latency_ms": _percentile(LLMCallLogModel.latency_llm_ms, 95),
                # For chat logs this is currently "stream phase" latency.
                "p50_stream_latency_ms": _percentile(LLMCallLogModel.latency_stream_ms, 50),
                "p95_stream_latency_ms": _percentile(LLMCallLogModel.latency_stream_ms, 95),
                "error_count": error_count or 0,
                "error_rate": round((error_count or 0) / total * 100, 2) if total else 0,
            }


class VoicePipelineLogRepo:
    """Repository for voice pipeline log operations."""

    @staticmethod
    def save(**kwargs) -> VoicePipelineLogModel:
        with get_session() as session:
            record = VoicePipelineLogModel(**kwargs)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(
        user_id: str, limit: int = 50, offset: int = 0, mode: str | None = None
    ) -> list[VoicePipelineLogModel]:
        with get_session() as session:
            stmt = (
                select(VoicePipelineLogModel)
                .where(VoicePipelineLogModel.user_id == user_id)
                .order_by(VoicePipelineLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            if mode:
                stmt = stmt.where(VoicePipelineLogModel.mode == mode)
            return list(session.exec(stmt).all())

    @staticmethod
    def get_stats(user_id: str, mode: str | None = None) -> dict:
        """Get aggregate voice-pipeline stats scoped to one user."""
        with get_session() as db:
            user_scope = VoicePipelineLogModel.user_id == user_id

            def _scope(stmt):
                stmt = stmt.where(user_scope)
                if mode:
                    stmt = stmt.where(VoicePipelineLogModel.mode == mode)
                return stmt

            base = select(func.count(VoicePipelineLogModel.id))
            if mode:
                base = base.where(VoicePipelineLogModel.mode == mode)
            base = base.where(user_scope)

            total = db.exec(base).one() or 0

            def _avg(col):
                q = _scope(select(func.avg(col)))
                return round(db.exec(q).one() or 0, 2)

            def _percentile(col, p: float) -> float | None:
                q = _scope(select(col).where(col.isnot(None)))
                values = [v for v in db.exec(q).all() if v is not None]
                if not values:
                    return None
                values.sort()
                rank = round((p / 100.0) * (len(values) - 1))
                rank = min(max(rank, 0), len(values) - 1)
                return round(values[rank], 2)

            error_q = _scope(
                select(func.count(VoicePipelineLogModel.id)).where(
                    VoicePipelineLogModel.error.isnot(None)
                )
            )
            error_count = db.exec(error_q).one() or 0

            interrupted_q = _scope(
                select(func.count(VoicePipelineLogModel.id)).where(
                    VoicePipelineLogModel.tts_interrupted.is_(True)
                )
            )
            interrupted_count = db.exec(interrupted_q).one() or 0

            resilience = TTSResilienceLogRepo.get_stats_for_voice_logs(
                db, user_id=user_id, mode=mode
            )

            return {
                "total_turns": total,
                "avg_total_ms": _avg(VoicePipelineLogModel.latency_total_ms),
                "avg_vad_ms": _avg(VoicePipelineLogModel.latency_vad_ms),
                "avg_stt_ms": _avg(VoicePipelineLogModel.latency_stt_ms),
                "avg_turn_detection_ms": _avg(VoicePipelineLogModel.latency_turn_detection_ms),
                "avg_llm_ms": _avg(VoicePipelineLogModel.latency_llm_ms),
                "avg_llm_ttft_ms": _avg(VoicePipelineLogModel.latency_llm_first_token_ms),
                "p50_llm_ttft_ms": _percentile(
                    VoicePipelineLogModel.latency_llm_first_token_ms, 50
                ),
                "p95_llm_ttft_ms": _percentile(
                    VoicePipelineLogModel.latency_llm_first_token_ms, 95
                ),
                "avg_tool_ms": _avg(VoicePipelineLogModel.latency_tool_ms),
                "avg_tts_ms": _avg(VoicePipelineLogModel.latency_tts_ms),
                "avg_tts_ttfb_ms": _avg(VoicePipelineLogModel.latency_tts_first_chunk_ms),
                "error_count": error_count,
                "error_rate": round(error_count / total * 100, 2) if total else 0,
                "interrupted_count": interrupted_count,
                "interrupt_rate": round(interrupted_count / total * 100, 2) if total else 0,
                "retry_turns": resilience["retry_turns"],
                "avg_tts_retry_count": resilience["avg_tts_retry_count"],
                "fallback_count": resilience["fallback_count"],
                "fallback_rate": resilience["fallback_rate"],
            }


class TTSResilienceLogRepo:
    """Repository for TTS retry/fallback metadata."""

    @staticmethod
    def save(**kwargs) -> TTSResilienceLogModel:
        with get_session() as session:
            record = TTSResilienceLogModel(**kwargs)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_by_voice_log_ids(voice_log_ids: list[int]) -> dict[int, TTSResilienceLogModel]:
        if not voice_log_ids:
            return {}
        with get_session() as session:
            stmt = select(TTSResilienceLogModel).where(
                TTSResilienceLogModel.voice_log_id.in_(voice_log_ids)
            )
            rows = list(session.exec(stmt).all())
            return {row.voice_log_id: row for row in rows}

    @staticmethod
    def get_stats_for_voice_logs(
        db: Session, user_id: str, mode: str | None = None
    ) -> dict[str, float]:
        log_ids_stmt = select(VoicePipelineLogModel.id).where(
            VoicePipelineLogModel.user_id == user_id
        )
        if mode:
            log_ids_stmt = log_ids_stmt.where(VoicePipelineLogModel.mode == mode)
        voice_log_ids = list(db.exec(log_ids_stmt).all())
        if not voice_log_ids:
            return {
                "retry_turns": 0,
                "avg_tts_retry_count": 0.0,
                "fallback_count": 0,
                "fallback_rate": 0.0,
            }

        rows_stmt = select(TTSResilienceLogModel).where(
            TTSResilienceLogModel.voice_log_id.in_(voice_log_ids)
        )
        rows = list(db.exec(rows_stmt).all())
        if not rows:
            return {
                "retry_turns": 0,
                "avg_tts_retry_count": 0.0,
                "fallback_count": 0,
                "fallback_rate": 0.0,
            }

        retry_turns = sum(1 for row in rows if row.retry_count > 0)
        fallback_count = sum(1 for row in rows if row.fallback_used)
        avg_retry_count = round(sum(row.retry_count for row in rows) / len(rows), 2)
        return {
            "retry_turns": retry_turns,
            "avg_tts_retry_count": avg_retry_count,
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_count / len(rows) * 100, 2),
        }


class ResourceRepo:
    """Repository for resource operations."""

    @staticmethod
    def create(**kwargs) -> ResourceModel:
        with get_session() as session:
            resource = ResourceModel(**kwargs)
            session.add(resource)
            session.commit()
            session.refresh(resource)
            return resource

    @staticmethod
    def get_by_id(resource_id: str) -> ResourceModel | None:
        with get_session() as session:
            return session.get(ResourceModel, resource_id)

    @staticmethod
    def list_by_agent(agent_id: str) -> list[ResourceModel]:
        with get_session() as session:
            stmt = (
                select(ResourceModel)
                .where(ResourceModel.agent_id == agent_id)
                .order_by(ResourceModel.created_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def update_status(resource_id: str, status: str, **kwargs) -> ResourceModel | None:
        with get_session() as session:
            resource = session.get(ResourceModel, resource_id)
            if not resource:
                return None
            resource.status = status
            for key, value in kwargs.items():
                if hasattr(resource, key):
                    setattr(resource, key, value)
            session.add(resource)
            session.commit()
            session.refresh(resource)
            return resource

    @staticmethod
    def delete(resource_id: str) -> bool:
        with get_session() as session:
            resource = session.get(ResourceModel, resource_id)
            if not resource:
                return False
            # Delete associated chunks first
            chunks_stmt = select(ResourceChunkModel).where(
                ResourceChunkModel.resource_id == resource_id
            )
            for chunk in session.exec(chunks_stmt).all():
                session.delete(chunk)
            session.delete(resource)
            session.commit()
            return True


class ResourceChunkRepo:
    """Repository for resource chunk operations."""

    @staticmethod
    def create_batch(chunks: list[ResourceChunkModel]) -> None:
        with get_session() as session:
            for chunk in chunks:
                session.add(chunk)
            session.commit()

    @staticmethod
    def search(agent_id: str, query: str, limit: int = 5) -> list[ResourceChunkModel]:
        """Lexical search with SQL candidate filtering and Python reranking."""
        with get_session() as session:
            # Get resource IDs for this agent
            resource_ids_stmt = select(ResourceModel.id).where(
                ResourceModel.agent_id == agent_id,
                ResourceModel.status == "ready",
            )
            resource_ids = list(session.exec(resource_ids_stmt).all())
            if not resource_ids:
                logger.debug(
                    "resource search skipped for agent_id=%s: no ready resources", agent_id
                )
                return []

            query_terms = _resource_query_terms(query)
            if not query_terms:
                logger.debug(
                    "resource search skipped for agent_id=%s: no meaningful query terms",
                    agent_id,
                )
                return []

            from sqlalchemy import or_

            conditions = [
                func.lower(ResourceChunkModel.content).like(f"%{term}%") for term in query_terms
            ]
            normalized_query = " ".join(query_terms)
            if len(query_terms) > 1:
                conditions.append(
                    func.lower(ResourceChunkModel.content).like(f"%{normalized_query}%")
                )

            candidate_limit = max(limit * 20, 100)
            stmt = (
                select(ResourceChunkModel)
                .where(
                    ResourceChunkModel.resource_id.in_(resource_ids),
                    or_(*conditions),
                )
                .order_by(ResourceChunkModel.resource_id, ResourceChunkModel.chunk_index)
                .limit(candidate_limit)
            )

            candidates = list(session.exec(stmt).all())
            if not candidates:
                logger.debug(
                    "resource search returned no candidates for agent_id=%s terms=%d",
                    agent_id,
                    len(query_terms),
                )
                return []

            ranked = sorted(
                (
                    (_score_resource_chunk(chunk.content, query_terms, normalized_query), chunk)
                    for chunk in candidates
                ),
                key=lambda item: (-item[0], item[1].resource_id, item[1].chunk_index),
            )
            results = [chunk for score, chunk in ranked if score > 0][:limit]
            logger.debug(
                "resource search agent_id=%s terms=%d candidates=%d returned=%d",
                agent_id,
                len(query_terms),
                len(candidates),
                len(results),
            )
            return results


class TopicMasteryRepo:
    """Repository for topic mastery / struggle heatmap operations."""

    @staticmethod
    def save_batch(entries: list[TopicMasteryModel]) -> None:
        with get_session() as session:
            for entry in entries:
                session.add(entry)
            session.commit()

    @staticmethod
    def get_by_agent(user_id: str, agent_id: str) -> list[TopicMasteryModel]:
        with get_session() as session:
            stmt = (
                select(TopicMasteryModel)
                .where(
                    TopicMasteryModel.user_id == user_id,
                    TopicMasteryModel.agent_id == agent_id,
                )
                .order_by(TopicMasteryModel.created_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def get_summary(user_id: str, agent_id: str) -> dict:
        """Aggregate mastery data by topic and chapter."""
        entries = TopicMasteryRepo.get_by_agent(user_id, agent_id)
        if not entries:
            return {"topics": [], "chapters": []}

        # Latest signal per topic + session count
        topic_latest: dict[str, dict] = {}
        topic_sessions: dict[str, set] = {}
        for e in entries:
            topic_sessions.setdefault(e.topic, set()).add(e.session_id)
            # Keep the most recent signal (entries are desc by created_at)
            if e.topic not in topic_latest:
                topic_latest[e.topic] = {
                    "topic": e.topic,
                    "chapter": e.chapter,
                    "signal_type": e.signal_type,
                }

        topics = []
        for topic, info in topic_latest.items():
            topics.append(
                {
                    **info,
                    "session_count": len(topic_sessions[topic]),
                }
            )

        # Aggregate by chapter
        chapter_stats: dict[str, dict[str, int]] = {}
        for t in topics:
            ch = t.get("chapter") or "Uncategorized"
            stats = chapter_stats.setdefault(ch, {"understood": 0, "struggled": 0, "unclear": 0})
            sig = t["signal_type"]
            if sig in stats:
                stats[sig] += 1

        chapters = [{"name": name, **counts} for name, counts in chapter_stats.items()]

        return {"topics": topics, "chapters": chapters}

    @staticmethod
    def get_tutoring_context(
        user_id: str,
        agent_id: str,
        max_topics: int = 5,
        max_chapters: int = 5,
    ) -> dict:
        """Return a compact, deterministic tutoring context for prompts."""
        summary = TopicMasteryRepo.get_summary(user_id, agent_id)
        topics = summary.get("topics", [])
        chapters = summary.get("chapters", [])

        if not topics and not chapters:
            return {"topics": [], "chapters": [], "prompt": ""}

        signal_order = {
            "struggled": 0,
            "unclear": 1,
            "understood": 2,
        }

        ordered_topics = sorted(
            topics,
            key=lambda item: (
                signal_order.get(item.get("signal_type"), 99),
                -int(item.get("session_count") or 0),
                (item.get("topic") or "").lower(),
            ),
        )[:max_topics]

        ordered_chapters = sorted(
            chapters,
            key=lambda item: (
                -(item.get("struggled") or 0),
                -(item.get("unclear") or 0),
                -(item.get("understood") or 0),
                (item.get("name") or "").lower(),
            ),
        )[:max_chapters]

        topic_lines = [
            (
                f"{item.get('topic')} "
                f"[{item.get('signal_type', 'unknown')}, "
                f"sessions={item.get('session_count', 0)}, "
                f"chapter={item.get('chapter') or 'Uncategorized'}]"
            )
            for item in ordered_topics
        ]
        chapter_lines = [
            (
                f"{item.get('name')} "
                f"[U={item.get('understood', 0)}, "
                f"S={item.get('struggled', 0)}, "
                f"C={item.get('unclear', 0)}]"
            )
            for item in ordered_chapters
        ]

        prompt_parts: list[str] = []
        if topic_lines:
            prompt_parts.append("Recent topics: " + "; ".join(topic_lines))
        if chapter_lines:
            prompt_parts.append("Chapter balance: " + "; ".join(chapter_lines))

        return {
            "topics": ordered_topics,
            "chapters": ordered_chapters,
            "prompt": "\n".join(prompt_parts),
        }


# Initialize DB on import
init_db()

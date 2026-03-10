"""
SQLModel database models.
"""
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from uuid import uuid4
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON, func

# Database path - use absolute path to avoid readonly issues
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "memory.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Create engine (sync for simplicity - can switch to async later)
engine = create_engine(DATABASE_URL, echo=False)


# ============== Models ==============

class EpisodicMemoryModel(SQLModel, table=True):
    """Layer 2: Conversation summaries."""
    __tablename__ = "episodic_memory"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: Optional[str] = None
    summary: str
    turn_count: Optional[int] = None
    meta_json: Optional[str] = Field(default=None, sa_column_kwargs={"name": "metadata"})
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def get_meta(self) -> Dict:
        """Get metadata dict."""
        return json.loads(self.meta_json) if self.meta_json else {}
    
    def set_meta(self, value: Dict):
        """Set metadata dict."""
        self.meta_json = json.dumps(value) if value else None


class UserProfileModel(SQLModel, table=True):
    """Layer 4: User profile - canonical identity."""
    __tablename__ = "user_profile"
    
    user_id: str = Field(primary_key=True)
    name: Optional[str] = None
    timezone: Optional[str] = None
    preferences_json: Optional[str] = Field(default="{}", sa_column_kwargs={"name": "preferences"})
    facts_json: Optional[str] = Field(default="{}", sa_column_kwargs={"name": "facts"})
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @property
    def preferences(self) -> Dict:
        return json.loads(self.preferences_json) if self.preferences_json else {}
    
    @preferences.setter
    def preferences(self, value: Dict):
        self.preferences_json = json.dumps(value) if value else "{}"
    
    @property
    def facts(self) -> Dict:
        return json.loads(self.facts_json) if self.facts_json else {}
    
    @facts.setter
    def facts(self, value: Dict):
        self.facts_json = json.dumps(value) if value else "{}"


class DecisionMemoryModel(SQLModel, table=True):
    """Agentic decision tracking."""
    __tablename__ = "decision_memory"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    session_id: Optional[str] = None
    action: str
    tool_used: Optional[str] = None
    success: bool = True
    context: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserModel(SQLModel, table=True):
    """Registered user accounts."""
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class AgentModel(SQLModel, table=True):
    """User-created AI agents with compiled system prompts."""
    __tablename__ = "agents"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    name: str
    description: Optional[str] = None
    system_prompt: str
    persona_json: Optional[str] = None
    capabilities_json: str = '["canvas"]'
    icon: Optional[str] = None
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def get_persona(self) -> Dict:
        return json.loads(self.persona_json) if self.persona_json else {}

    def get_capabilities(self) -> List[str]:
        return json.loads(self.capabilities_json) if self.capabilities_json else ["canvas"]


class SessionModel(SQLModel, table=True):
    """Persistent chat sessions for cross-session memory."""
    __tablename__ = "sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    title: Optional[str] = None
    summary: Optional[str] = None
    message_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessageModel(SQLModel, table=True):
    """Persisted conversation messages for session replay and cross-session context."""
    __tablename__ = "conversation_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls_json: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LLMCallLogModel(SQLModel, table=True):
    """End-to-end logging for LLM API calls."""
    __tablename__ = "llm_call_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    user_message: str
    llm_provider: str
    llm_model: str
    tool_calls_json: Optional[str] = None
    response_text: Optional[str] = None
    latency_total_ms: Optional[float] = None
    latency_llm_ms: Optional[float] = None
    latency_tool_ms: Optional[float] = None
    latency_stream_ms: Optional[float] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_tool_calls(self) -> list:
        return json.loads(self.tool_calls_json) if self.tool_calls_json else []


class VoicePipelineLogModel(SQLModel, table=True):
    """End-to-end logging for voice pipeline turns.

    Captures every stage: speech detection → STT → turn detection → LLM → tool calls → TTS → playback.
    """
    __tablename__ = "voice_pipeline_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    mode: str = Field(default="voice")  # "voice" or "chat"

    user_message: str = ""
    response_text: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    # Stage latencies (milliseconds)
    latency_vad_ms: Optional[float] = None           # VAD speech detection time
    latency_stt_ms: Optional[float] = None            # Time from audio start to final transcript
    latency_turn_detection_ms: Optional[float] = None # Smart Turn analysis time
    latency_stt_to_llm_ms: Optional[float] = None     # Gap between STT final and LLM call start
    latency_llm_ms: Optional[float] = None             # LLM inference (incl tool call rounds)
    latency_llm_first_token_ms: Optional[float] = None # Time to first LLM token (TTFT)
    latency_tool_ms: Optional[float] = None            # Total tool execution time
    latency_tts_ms: Optional[float] = None             # TTS generation time (all chunks)
    latency_tts_first_chunk_ms: Optional[float] = None # Time to first TTS audio chunk (TTFB)
    latency_total_ms: Optional[float] = None           # End-to-end: speech start → last TTS chunk

    # Pipeline metadata
    tool_calls_json: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tts_chunks_sent: Optional[int] = None
    tts_interrupted: bool = False
    smart_turn_used: bool = False
    smart_turn_result: Optional[str] = None  # "complete", "incomplete", "fallback"

    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def get_tool_calls(self) -> list:
        return json.loads(self.tool_calls_json) if self.tool_calls_json else []


class ResourceModel(SQLModel, table=True):
    """Uploaded resources (PDFs, URLs, text) attached to an agent."""
    __tablename__ = "resources"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    agent_id: str = Field(index=True, foreign_key="agents.id")
    user_id: str = Field(index=True, foreign_key="users.id")
    name: str                # Original filename or URL
    resource_type: str       # "pdf", "url", "text"
    content_text: str = ""   # Extracted full text
    chunk_count: int = 0
    size_bytes: Optional[int] = None
    status: str = "processing"  # "processing", "ready", "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ResourceChunkModel(SQLModel, table=True):
    """Chunked text from a resource for retrieval."""
    __tablename__ = "resource_chunks"

    id: Optional[int] = Field(default=None, primary_key=True)
    resource_id: str = Field(index=True, foreign_key="resources.id")
    chunk_index: int
    content: str             # ~500 token chunk (~2000 chars)
    page_number: Optional[int] = None


class TopicMasteryModel(SQLModel, table=True):
    """Per-session topic mastery signals for the struggle heatmap."""
    __tablename__ = "topic_mastery"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, foreign_key="users.id")
    agent_id: str = Field(index=True, foreign_key="agents.id")
    session_id: str = Field(index=True, foreign_key="sessions.id")
    topic: str               # "Newton's Second Law"
    chapter: Optional[str] = None  # "Laws of Motion"
    signal_type: str         # "understood", "struggled", "unclear"
    details: Optional[str] = None  # Brief note on what happened
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ToolModel(SQLModel, table=True):
    """Tool definitions for function calling."""
    __tablename__ = "tools"
    
    name: str = Field(primary_key=True)
    description: str
    parameters_json: str = Field(sa_column_kwargs={"name": "parameters"})
    handler_module: Optional[str] = None
    handler_function: Optional[str] = None
    code: Optional[str] = None  # Python code for the function (alternative to module/function)
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @property
    def parameters(self) -> Dict:
        return json.loads(self.parameters_json)
    
    @parameters.setter
    def parameters(self, value: Dict):
        self.parameters_json = json.dumps(value)
    
    def to_openai_schema(self) -> Dict:
        """OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }
    
    def to_anthropic_schema(self) -> Dict:
        """Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters
        }


# ============== Database Operations ==============

def init_db():
    """Create all tables."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Get a database session."""
    return Session(engine)


# ============== Repository Classes ==============

class EpisodicMemoryRepo:
    """Repository for episodic memory operations."""
    
    @staticmethod
    def save(user_id: str, summary: str, session_id: Optional[str] = None,
             turn_count: int = 0, metadata: Optional[Dict] = None) -> EpisodicMemoryModel:
        with get_session() as session:
            record = EpisodicMemoryModel(
                user_id=user_id,
                session_id=session_id,
                summary=summary,
                turn_count=turn_count,
                meta_json=json.dumps(metadata) if metadata else None
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record
    
    @staticmethod
    def get_recent(user_id: str, limit: int = 5) -> List[EpisodicMemoryModel]:
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
    def get(user_id: str) -> Optional[UserProfileModel]:
        with get_session() as session:
            return session.get(UserProfileModel, user_id)
    
    @staticmethod
    def update(user_id: str, **kwargs) -> Optional[UserProfileModel]:
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
    def log(user_id: str, action: str, session_id: Optional[str] = None,
            tool_used: Optional[str] = None, success: bool = True,
            context: Optional[str] = None) -> DecisionMemoryModel:
        with get_session() as session:
            record = DecisionMemoryModel(
                user_id=user_id,
                session_id=session_id,
                action=action,
                tool_used=tool_used,
                success=success,
                context=context
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record
    
    @staticmethod
    def get_recent_failures(user_id: str, limit: int = 5) -> List[DecisionMemoryModel]:
        with get_session() as session:
            stmt = (
                select(DecisionMemoryModel)
                .where(DecisionMemoryModel.user_id == user_id)
                .where(DecisionMemoryModel.success == False)
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
                .where(DecisionMemoryModel.success == False)
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
    _openai_schema_cache: Optional[List[Dict]] = None
    _openai_schema_cache_key: Optional[Tuple[int, Optional[datetime]]] = None

    @staticmethod
    def _invalidate_schema_cache():
        ToolRepo._openai_schema_cache = None
        ToolRepo._openai_schema_cache_key = None

    @staticmethod
    def _get_enabled_schema_cache_key() -> Tuple[int, Optional[datetime]]:
        with get_session() as session:
            stmt = select(
                func.count(ToolModel.name),
                func.max(ToolModel.updated_at),
            ).where(ToolModel.enabled == True)
            count, latest_updated_at = session.exec(stmt).one()
            return int(count or 0), latest_updated_at
    
    @staticmethod
    def upsert(name: str, description: str, parameters: Dict,
               handler_module: Optional[str] = None,
               handler_function: Optional[str] = None,
               code: Optional[str] = None,
               enabled: bool = True) -> ToolModel:
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
                    enabled=enabled
                )
            session.add(tool)
            session.commit()
            session.refresh(tool)
            ToolRepo._invalidate_schema_cache()
            return tool
    
    @staticmethod
    def get(name: str) -> Optional[ToolModel]:
        with get_session() as session:
            return session.get(ToolModel, name)
    
    @staticmethod
    def get_enabled(name: str) -> Optional[ToolModel]:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            return tool if tool and tool.enabled else None
    
    @staticmethod
    def list_all(enabled_only: bool = True) -> List[ToolModel]:
        with get_session() as session:
            stmt = select(ToolModel).order_by(ToolModel.name)
            if enabled_only:
                stmt = stmt.where(ToolModel.enabled == True)
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
    def to_openai_format() -> List[Dict]:
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
    def to_anthropic_format() -> List[Dict]:
        """Get all enabled tools in Anthropic format."""
        tools = ToolRepo.list_all(enabled_only=True)
        return [t.to_anthropic_schema() for t in tools]


class UserRepo:
    """Repository for user account operations."""

    @staticmethod
    def create(email: str, password_hash: str, name: Optional[str] = None) -> UserModel:
        with get_session() as session:
            user = UserModel(
                email=email,
                password_hash=password_hash,
                name=name,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    @staticmethod
    def get_by_email(email: str) -> Optional[UserModel]:
        with get_session() as session:
            stmt = select(UserModel).where(UserModel.email == email)
            return session.exec(stmt).first()

    @staticmethod
    def get_by_id(user_id: str) -> Optional[UserModel]:
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
    def get_by_id(agent_id: str) -> Optional[AgentModel]:
        with get_session() as session:
            return session.get(AgentModel, agent_id)

    @staticmethod
    def list_by_user(user_id: str) -> List[AgentModel]:
        with get_session() as session:
            stmt = (
                select(AgentModel)
                .where(AgentModel.user_id == user_id)
                .order_by(AgentModel.created_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def update(agent_id: str, **kwargs) -> Optional[AgentModel]:
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
                .where(AgentModel.is_default == True)
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
    def get_by_id(session_id: str) -> Optional[SessionModel]:
        with get_session() as session:
            return session.get(SessionModel, session_id)

    @staticmethod
    def list_by_agent(user_id: str, agent_id: str) -> List[SessionModel]:
        with get_session() as session:
            stmt = (
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .where(SessionModel.agent_id == agent_id)
                .order_by(SessionModel.updated_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def list_by_user(user_id: str, agent_id: Optional[str] = None) -> List[SessionModel]:
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
        tool_calls_json: Optional[str] = None,
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
    def get_recent(session_id: str, limit: int = 20) -> List[ConversationMessageModel]:
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
            stmt = (
                select(func.count(ConversationMessageModel.id))
                .where(ConversationMessageModel.session_id == session_id)
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
        user_id: Optional[str] = None,
        tool_calls_json: Optional[str] = None,
        response_text: Optional[str] = None,
        latency_total_ms: Optional[float] = None,
        latency_llm_ms: Optional[float] = None,
        latency_tool_ms: Optional[float] = None,
        latency_stream_ms: Optional[float] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        error: Optional[str] = None
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
                error=error
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(limit: int = 50, offset: int = 0) -> List[LLMCallLogModel]:
        with get_session() as session:
            stmt = (
                select(LLMCallLogModel)
                .order_by(LLMCallLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def get_stats() -> Dict:
        """Get aggregated stats."""
        with get_session() as db:
            total = db.exec(
                select(func.count(LLMCallLogModel.id))
            ).one()
            avg_total = db.exec(
                select(func.avg(LLMCallLogModel.latency_total_ms))
            ).one()
            avg_llm = db.exec(
                select(func.avg(LLMCallLogModel.latency_llm_ms))
            ).one()
            avg_tool = db.exec(
                select(func.avg(LLMCallLogModel.latency_tool_ms))
            ).one()
            error_count = db.exec(
                select(func.count(LLMCallLogModel.id)).where(LLMCallLogModel.error.isnot(None))
            ).one()

            def _percentile(col, p: float) -> Optional[float]:
                values = [v for v in db.exec(select(col).where(col.isnot(None))).all() if v is not None]
                if not values:
                    return None
                values.sort()
                rank = int(round((p / 100.0) * (len(values) - 1)))
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
                "error_rate": round((error_count or 0) / total * 100, 2) if total else 0
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
    def get_recent(limit: int = 50, offset: int = 0, mode: Optional[str] = None) -> List[VoicePipelineLogModel]:
        with get_session() as session:
            stmt = (
                select(VoicePipelineLogModel)
                .order_by(VoicePipelineLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            if mode:
                stmt = stmt.where(VoicePipelineLogModel.mode == mode)
            return list(session.exec(stmt).all())

    @staticmethod
    def get_stats(mode: Optional[str] = None) -> Dict:
        """Get aggregated pipeline stats."""
        with get_session() as db:
            base = select(func.count(VoicePipelineLogModel.id))
            if mode:
                base = base.where(VoicePipelineLogModel.mode == mode)

            total = db.exec(base).one() or 0

            def _avg(col):
                q = select(func.avg(col))
                if mode:
                    q = q.where(VoicePipelineLogModel.mode == mode)
                return round(db.exec(q).one() or 0, 2)

            def _percentile(col, p: float) -> Optional[float]:
                q = select(col).where(col.isnot(None))
                if mode:
                    q = q.where(VoicePipelineLogModel.mode == mode)
                values = [v for v in db.exec(q).all() if v is not None]
                if not values:
                    return None
                values.sort()
                rank = int(round((p / 100.0) * (len(values) - 1)))
                rank = min(max(rank, 0), len(values) - 1)
                return round(values[rank], 2)

            error_q = select(func.count(VoicePipelineLogModel.id)).where(
                VoicePipelineLogModel.error.isnot(None)
            )
            if mode:
                error_q = error_q.where(VoicePipelineLogModel.mode == mode)
            error_count = db.exec(error_q).one() or 0

            interrupted_q = select(func.count(VoicePipelineLogModel.id)).where(
                VoicePipelineLogModel.tts_interrupted == True
            )
            if mode:
                interrupted_q = interrupted_q.where(VoicePipelineLogModel.mode == mode)
            interrupted_count = db.exec(interrupted_q).one() or 0

            return {
                "total_turns": total,
                "avg_total_ms": _avg(VoicePipelineLogModel.latency_total_ms),
                "avg_vad_ms": _avg(VoicePipelineLogModel.latency_vad_ms),
                "avg_stt_ms": _avg(VoicePipelineLogModel.latency_stt_ms),
                "avg_turn_detection_ms": _avg(VoicePipelineLogModel.latency_turn_detection_ms),
                "avg_llm_ms": _avg(VoicePipelineLogModel.latency_llm_ms),
                "avg_llm_ttft_ms": _avg(VoicePipelineLogModel.latency_llm_first_token_ms),
                "p50_llm_ttft_ms": _percentile(VoicePipelineLogModel.latency_llm_first_token_ms, 50),
                "p95_llm_ttft_ms": _percentile(VoicePipelineLogModel.latency_llm_first_token_ms, 95),
                "avg_tool_ms": _avg(VoicePipelineLogModel.latency_tool_ms),
                "avg_tts_ms": _avg(VoicePipelineLogModel.latency_tts_ms),
                "avg_tts_ttfb_ms": _avg(VoicePipelineLogModel.latency_tts_first_chunk_ms),
                "error_count": error_count,
                "error_rate": round(error_count / total * 100, 2) if total else 0,
                "interrupted_count": interrupted_count,
                "interrupt_rate": round(interrupted_count / total * 100, 2) if total else 0,
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
    def get_by_id(resource_id: str) -> Optional[ResourceModel]:
        with get_session() as session:
            return session.get(ResourceModel, resource_id)

    @staticmethod
    def list_by_agent(agent_id: str) -> List[ResourceModel]:
        with get_session() as session:
            stmt = (
                select(ResourceModel)
                .where(ResourceModel.agent_id == agent_id)
                .order_by(ResourceModel.created_at.desc())
            )
            return list(session.exec(stmt).all())

    @staticmethod
    def update_status(resource_id: str, status: str, **kwargs) -> Optional[ResourceModel]:
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
    def create_batch(chunks: List[ResourceChunkModel]) -> None:
        with get_session() as session:
            for chunk in chunks:
                session.add(chunk)
            session.commit()

    @staticmethod
    def search(agent_id: str, query: str, limit: int = 5) -> List[ResourceChunkModel]:
        """Simple keyword search using LIKE across chunks for the given agent."""
        with get_session() as session:
            # Get resource IDs for this agent
            resource_ids_stmt = select(ResourceModel.id).where(
                ResourceModel.agent_id == agent_id,
                ResourceModel.status == "ready",
            )
            resource_ids = list(session.exec(resource_ids_stmt).all())
            if not resource_ids:
                return []

            # Search chunks with LIKE for each query word
            stmt = select(ResourceChunkModel).where(
                ResourceChunkModel.resource_id.in_(resource_ids)
            )
            # Match any word from the query
            words = [w.strip() for w in query.split() if len(w.strip()) > 2]
            if words:
                from sqlalchemy import or_
                conditions = [
                    ResourceChunkModel.content.contains(word)
                    for word in words
                ]
                stmt = stmt.where(or_(*conditions))

            stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())


class TopicMasteryRepo:
    """Repository for topic mastery / struggle heatmap operations."""

    @staticmethod
    def save_batch(entries: List[TopicMasteryModel]) -> None:
        with get_session() as session:
            for entry in entries:
                session.add(entry)
            session.commit()

    @staticmethod
    def get_by_agent(user_id: str, agent_id: str) -> List[TopicMasteryModel]:
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
    def get_summary(user_id: str, agent_id: str) -> Dict:
        """Aggregate mastery data by topic and chapter."""
        entries = TopicMasteryRepo.get_by_agent(user_id, agent_id)
        if not entries:
            return {"topics": [], "chapters": []}

        # Latest signal per topic + session count
        topic_latest: Dict[str, Dict] = {}
        topic_sessions: Dict[str, set] = {}
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
            topics.append({
                **info,
                "session_count": len(topic_sessions[topic]),
            })

        # Aggregate by chapter
        chapter_stats: Dict[str, Dict[str, int]] = {}
        for t in topics:
            ch = t.get("chapter") or "Uncategorized"
            stats = chapter_stats.setdefault(ch, {"understood": 0, "struggled": 0, "unclear": 0})
            sig = t["signal_type"]
            if sig in stats:
                stats[sig] += 1

        chapters = [
            {"name": name, **counts}
            for name, counts in chapter_stats.items()
        ]

        return {"topics": topics, "chapters": chapters}


# Initialize DB on import
init_db()


"""
SQLModel database models.
"""
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "memory.db")
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
                return True
            return False
    
    @staticmethod
    def to_openai_format() -> List[Dict]:
        """Get all enabled tools in OpenAI format."""
        tools = ToolRepo.list_all(enabled_only=True)
        return [t.to_openai_schema() for t in tools]
    
    @staticmethod
    def to_anthropic_format() -> List[Dict]:
        """Get all enabled tools in Anthropic format."""
        tools = ToolRepo.list_all(enabled_only=True)
        return [t.to_anthropic_schema() for t in tools]


# Initialize DB on import
init_db()


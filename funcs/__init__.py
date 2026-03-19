"""
Voice AI function modules.
"""
from .llm_pipeline import LLMPipeline
from .tts_pipeline import TTSPipeline
from .vad_gate import SileroVADGate
from .config import config, Config
from .auth import (
    get_current_user_id,
    get_current_user,
    require_auth,
)
from .llm_clients import (
    LLMClient,
    OpenAIClient,
    GeminiClient,
    create_llm_client,
)
from .models import (
    init_db,
    get_session,
    EpisodicMemoryModel,
    UserProfileModel,
    DecisionMemoryModel,
    ToolModel,
    UserModel,
    AgentModel,
    SessionModel,
    ConversationMessageModel,
    ResourceModel,
    ResourceChunkModel,
    TopicMasteryModel,
    EpisodicMemoryRepo,
    UserProfileRepo,
    DecisionMemoryRepo,
    ToolRepo,
    UserRepo,
    AgentRepo,
    SessionRepo,
    ConversationMessageRepo,
    ResourceRepo,
    ResourceChunkRepo,
    TopicMasteryRepo,
)
from .agents import compile_agent_prompt, get_agent_tools, append_resource_context
from .resources import ingest_pdf, ingest_url, search_chunks
from .search import web_search, register_web_search_tool
from .memory import (
    MemoryManager,
    ConversationContext,
    EpisodicMemory,
    SemanticMemory,
    UserProfile,
    DecisionMemory,
)
from .tools import (
    ToolRegistry,
    ToolStore,
    Tool,
    ToolCall,
    ToolResult,
    OpenAIAdapter,
    AnthropicAdapter,
    ModelAdapter,
    tool,
    default_registry,
    default_store,
)
from .tool_executor import (
    ToolExecutor,
    SandboxConfig,
    default_executor,
)
from .interruption import (
    InterruptionState,
    InterruptionManager,
    interruption_manager,
)
from .smart_turn import (
    SmartTurnAnalyzer,
    SmartTurnSession,
    TurnAudioBuffer,
)

__all__ = [
    "LLMPipeline",
    "TTSPipeline",
    "SileroVADGate",
    "config",
    "Config",
    "get_current_user_id",
    "get_current_user",
    "require_auth",
    # LLM Clients
    "LLMClient",
    "OpenAIClient",
    "GeminiClient",
    "create_llm_client",
    # ORM Models & Repos
    "init_db",
    "get_session",
    "EpisodicMemoryModel",
    "UserProfileModel",
    "DecisionMemoryModel",
    "ToolModel",
    "UserModel",
    "EpisodicMemoryRepo",
    "UserProfileRepo",
    "DecisionMemoryRepo",
    "ToolRepo",
    "UserRepo",
    "AgentModel",
    "AgentRepo",
    "SessionModel",
    "ConversationMessageModel",
    "SessionRepo",
    "ConversationMessageRepo",
    "ResourceModel",
    "ResourceChunkModel",
    "TopicMasteryModel",
    "ResourceRepo",
    "ResourceChunkRepo",
    "TopicMasteryRepo",
    # Agents
    "compile_agent_prompt",
    "get_agent_tools",
    "append_resource_context",
    # Resources
    "ingest_pdf",
    "ingest_url",
    "search_chunks",
    # Search
    "web_search",
    "register_web_search_tool",
    # Memory
    "MemoryManager",
    "ConversationContext",
    "EpisodicMemory",
    "SemanticMemory",
    "UserProfile",
    "DecisionMemory",
    # Tools
    "ToolRegistry",
    "ToolStore",
    "Tool",
    "ToolCall",
    "ToolResult",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "ModelAdapter",
    "tool",
    "default_registry",
    "default_store",
    # Tool Executor
    "ToolExecutor",
    "SandboxConfig",
    "default_executor",
    # Interruption
    "InterruptionState",
    "InterruptionManager",
    "interruption_manager",
    # Smart Turn
    "SmartTurnAnalyzer",
    "SmartTurnSession",
    "TurnAudioBuffer",
]


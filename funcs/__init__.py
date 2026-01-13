"""
Voice AI function modules.
"""
from .llm_pipeline import LLMPipeline
from .tts_pipeline import TTSPipeline
from .vad_gate import SileroVADGate
from .config import config, Config
from .auth import get_current_user_id
from .models import (
    init_db,
    get_session,
    EpisodicMemoryModel,
    UserProfileModel,
    DecisionMemoryModel,
    ToolModel,
    EpisodicMemoryRepo,
    UserProfileRepo,
    DecisionMemoryRepo,
    ToolRepo,
)
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

__all__ = [
    "LLMPipeline",
    "TTSPipeline",
    "SileroVADGate",
    "config",
    "Config",
    "get_current_user_id",
    # ORM Models & Repos
    "init_db",
    "get_session",
    "EpisodicMemoryModel",
    "UserProfileModel",
    "DecisionMemoryModel",
    "ToolModel",
    "EpisodicMemoryRepo",
    "UserProfileRepo",
    "DecisionMemoryRepo",
    "ToolRepo",
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
]


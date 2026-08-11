"""
Voice AI function modules.

Keep package imports lazy so importing a single submodule does not force-load
optional heavy dependencies such as Torch-backed VAD or Smart Turn models.
"""

from importlib import import_module

_EXPORTS = {
    "LLMPipeline": "funcs.llm_pipeline",
    "TTSPipeline": "funcs.tts_pipeline",
    "SileroVADGate": "funcs.vad_gate",
    "config": "funcs.config",
    "Config": "funcs.config",
    "get_current_user_id": "funcs.auth",
    "get_current_user": "funcs.auth",
    "require_auth": "funcs.auth",
    "LLMClient": "funcs.llm_clients",
    "OpenAIClient": "funcs.llm_clients",
    "GeminiClient": "funcs.llm_clients",
    "create_llm_client": "funcs.llm_clients",
    "init_db": "murmur.persistence",
    "get_session": "murmur.persistence",
    "EpisodicMemoryModel": "murmur.persistence.models",
    "UserProfileModel": "murmur.persistence.models",
    "DecisionMemoryModel": "murmur.persistence.models",
    "ToolModel": "murmur.persistence.models",
    "UserModel": "murmur.persistence.models",
    "AgentModel": "murmur.persistence.models",
    "SessionModel": "murmur.persistence.models",
    "ConversationMessageModel": "murmur.persistence.models",
    "ResourceModel": "murmur.persistence.models",
    "ResourceChunkModel": "murmur.persistence.models",
    "TopicMasteryModel": "murmur.persistence.models",
    "EpisodicMemoryRepo": "murmur.persistence.repositories.memory",
    "UserProfileRepo": "murmur.persistence.repositories.memory",
    "DecisionMemoryRepo": "murmur.persistence.repositories.memory",
    "ToolRepo": "murmur.persistence.repositories.tools",
    "UserRepo": "murmur.persistence.repositories.identities",
    "AgentRepo": "murmur.persistence.repositories.identities",
    "SessionRepo": "murmur.persistence.repositories.sessions",
    "ConversationMessageRepo": "murmur.persistence.repositories.sessions",
    "ResourceRepo": "murmur.persistence.repositories.resources",
    "ResourceChunkRepo": "murmur.persistence.repositories.resources",
    "TopicMasteryRepo": "murmur.persistence.repositories.sessions",
    "compile_agent_prompt": "funcs.agents",
    "get_agent_tools": "funcs.agents",
    "append_resource_context": "funcs.agents",
    "ingest_pdf": "funcs.resources",
    "ingest_url": "funcs.resources",
    "search_chunks": "funcs.resources",
    "web_search": "funcs.search",
    "register_web_search_tool": "funcs.search",
    "MemoryManager": "funcs.memory",
    "ConversationContext": "funcs.memory",
    "EpisodicMemory": "funcs.memory",
    "SemanticMemory": "funcs.memory",
    "UserProfile": "funcs.memory",
    "DecisionMemory": "funcs.memory",
    "ToolRegistry": "funcs.tools",
    "ToolStore": "funcs.tools",
    "Tool": "funcs.tools",
    "ToolCall": "funcs.tools",
    "ToolResult": "funcs.tools",
    "OpenAIAdapter": "funcs.tools",
    "AnthropicAdapter": "funcs.tools",
    "ModelAdapter": "funcs.tools",
    "tool": "funcs.tools",
    "default_registry": "funcs.tools",
    "default_store": "funcs.tools",
    "ToolExecutor": "funcs.tool_executor",
    "SandboxConfig": "funcs.tool_executor",
    "default_executor": "funcs.tool_executor",
    "InterruptionState": "funcs.interruption",
    "InterruptionManager": "funcs.interruption",
    "interruption_manager": "funcs.interruption",
    "SmartTurnAnalyzer": "funcs.smart_turn",
    "SmartTurnSession": "funcs.smart_turn",
    "TurnAudioBuffer": "funcs.smart_turn",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'funcs' has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

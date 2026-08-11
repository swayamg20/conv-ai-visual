import copy
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any, ClassVar

from funcs.canvas import ANIMATION_TOOLS, CANVAS_TOOL_SCHEMA, get_canvas_state
from funcs.config import config
from funcs.memory import MemoryManager
from funcs.tool_executor import ToolExecutor, default_executor
from funcs.tools import ModelAdapter, OpenAIAdapter, ToolRegistry, ToolStore
from murmur.llm.base import LLMClient
from murmur.llm.factory import create_llm_client
from murmur.llm.tool_runtime import ToolConversationMixin

# Number of recent messages used when generating a session summary
_SUMMARY_RECENT_MESSAGES = 10

logger = logging.getLogger("llm-pipeline")


class LLMPipeline(ToolConversationMixin):
    """
    LLM conversation pipeline with 4-layer memory architecture.

    Memory Layers:
    1. Conversation Context (short-term) - sliding window
    2. Episodic Memory - conversation summaries
    3. Semantic Memory - vector search via Mem0
    4. User Profile - canonical identity facts
    """

    MUTATING_TOOL_NAMES: ClassVar[set[str]] = {
        "canvas_update",
        "teach_with_visuals",
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        system_prompt: str | None = None,
        max_context_messages: int = 20,
        user_id: str = "default_user",
        session_id: str | None = None,
        agent_id: str | None = None,
        enable_memory: bool = True,
        canvas_mode: bool = False,
        canvas_system_prompt: str | None = None,
    ):
        """
        Initialize LLM pipeline with memory.

        Args:
            api_key: API key (defaults to provider-specific env var)
            model: Model name (defaults to provider-specific env var)
            provider: LLM provider ("openai" or "gemini", defaults to LLM_PROVIDER env var)
            system_prompt: Base system prompt for the assistant
            max_context_messages: Max messages in context window
            user_id: User ID for memory isolation
            session_id: Session ID for episodic tracking
            agent_id: Agent ID for cross-session memory scoping
            enable_memory: Whether to enable memory layers
            canvas_mode: Whether canvas visual mode is enabled (uses canvas for all responses)
            canvas_system_prompt: Custom canvas mode prompt (uses default if not provided)
        """
        self.provider = provider or config.LLM_PROVIDER
        self.api_key = api_key  # Can be None, factory will get from config
        self.model = model  # Can be None, factory will get from config

        # Create LLM client using factory
        self.client: LLMClient = create_llm_client(
            provider=self.provider, api_key=self.api_key, model=self.model
        )

        self.user_id = user_id
        self.agent_id = agent_id
        self.enable_memory = enable_memory

        # Canvas mode settings
        self.canvas_mode = canvas_mode
        self._canvas_system_prompt = canvas_system_prompt

        self.base_system_prompt = system_prompt or (
            "You are a helpful voice assistant. Provide concise, natural responses "
            "suitable for voice interaction. Keep answers brief unless more detail is requested."
        )

        # Initialize unified memory manager (all 4 layers)
        self.memory: MemoryManager | None = None
        if enable_memory:
            try:
                self.memory = MemoryManager(
                    user_id=user_id, session_id=session_id, agent_id=agent_id
                )
                self.memory.context.max_messages = max_context_messages
                logger.info("4-layer memory initialized")
            except Exception as e:
                logger.warning("Failed to initialize memory: %s. Continuing without.", e)
                self.enable_memory = False

        # Tool calling support
        self.tools = ToolRegistry()
        self.tool_store: ToolStore | None = None
        self.tool_executor: ToolExecutor = default_executor

        # Set adapter based on provider (for tool schema formatting)
        if self.provider == "openai":
            self.adapter: ModelAdapter = OpenAIAdapter()
        elif self.provider == "gemini":
            # Will add GeminiAdapter later, for now use OpenAI (compatible format)
            self.adapter: ModelAdapter = OpenAIAdapter()
        else:
            self.adapter: ModelAdapter = OpenAIAdapter()

        # Canvas support
        self.session_id = session_id or "default"
        self.canvas_callback: Callable[[list[dict]], Any] | None = None
        self.animation_callback: Callable[[dict], Any] | None = None

        # Call metrics (populated after each chat_with_tools_stream call)
        self._last_call_timing: dict | None = None
        self._last_call_tool_calls: list[dict] = []
        self._last_call_response: str | None = None
        self._last_call_error: str | None = None

        logger.info(
            "LLM pipeline initialized: provider=%s, model=%s, memory=%s, canvas_mode=%s",
            self.provider,
            self.model or "default",
            enable_memory,
            canvas_mode,
        )

    @property
    def active_system_prompt(self) -> str:
        """Get the currently active system prompt based on canvas mode."""
        if self.canvas_mode:
            if self._canvas_system_prompt:
                return self._canvas_system_prompt
            # Default to math tutor prompt from config
            return config.LLM_MATH_TUTOR_PROMPT
        return self.base_system_prompt

    def set_canvas_mode(self, enabled: bool, custom_prompt: str | None = None):
        """
        Toggle canvas mode on/off.

        Args:
            enabled: Whether to enable canvas mode
            custom_prompt: Optional custom canvas prompt
        """
        self.canvas_mode = enabled
        if custom_prompt:
            self._canvas_system_prompt = custom_prompt
        logger.info("Canvas mode %s", "enabled" if enabled else "disabled")

    async def get_context(
        self, current_query: str | None = None, include_canvas: bool = True
    ) -> list[dict[str, str]]:
        """
        Get conversation context enriched with all memory layers.

        Args:
            current_query: Current user query for semantic search
            include_canvas: Whether to include canvas state in context
        """
        # Use canvas prompt when canvas mode is enabled
        system_prompt = self.active_system_prompt

        if self.memory and current_query:
            if config.LLM_ASYNC_CONTEXT:
                context = await self.memory.build_context(current_query, system_prompt)
            else:
                context = self.memory.build_context_sync(current_query, system_prompt)
        else:
            # Fallback: basic context without memory
            context = [{"role": "system", "content": system_prompt}]

        # Add canvas state to system prompt if elements exist
        if include_canvas:
            canvas_summary = self.get_canvas_context()
            if canvas_summary and "empty" not in canvas_summary.lower():
                # Append canvas state to system message
                if context and context[0]["role"] == "system":
                    context[0]["content"] += f"\n\n[Current Canvas State]\n{canvas_summary}"

        return context

    async def get_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Get completion from LLM using provider-agnostic client."""
        try:
            return await self.client.complete(
                messages=messages, temperature=temperature, max_tokens=max_tokens
            )
        except Exception:
            logger.exception("LLM completion error")
            raise

    async def chat(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        save_to_memory: bool = True,
    ) -> str:
        """
        Simple chat interface - handles context automatically.

        Args:
            user_message: User's message
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            save_to_memory: Whether to save to long-term memory

        Returns:
            Assistant's response
        """
        # Build context with all memory layers
        context = await self.get_context(user_message)

        # Add user message to context
        context.append({"role": "user", "content": user_message})

        # Get response
        response = await self.get_completion(context, temperature, max_tokens)

        # Process for memory (all layers)
        if self.memory:
            self.memory.process_for_memory(user_message, response, save_semantic=save_to_memory)

        return response

    async def chat_stream(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        save_to_memory: bool = True,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat interface using provider-agnostic client.

        Yields:
            Text chunks as they arrive
        """
        # Build context with all memory layers
        context = await self.get_context(user_message)
        context.append({"role": "user", "content": user_message})

        full_response = ""

        try:
            async for chunk in self.client.stream(
                messages=context, temperature=temperature, max_tokens=max_tokens
            ):
                full_response += chunk
                yield chunk

        except Exception:
            logger.exception("LLM stream error")
            raise

        # Process for memory after stream completes
        if self.memory:
            self.memory.process_for_memory(
                user_message, full_response, save_semantic=save_to_memory
            )

    # ========== Memory Access Methods ==========

    def set_user_profile(self, **kwargs):
        """
        Set user profile (Layer 4).

        Example:
            pipeline.set_user_profile(name="Swayam", timezone="IST")
        """
        if self.memory:
            self.memory.profile.update(**kwargs)

    def add_user_fact(self, key: str, value):
        """Add a fact to user profile."""
        if self.memory:
            self.memory.profile.add_fact(key, value)

    def add_user_preference(self, key: str, value):
        """Add a preference to user profile."""
        if self.memory:
            self.memory.profile.add_preference(key, value)

    def get_user_profile(self) -> dict:
        """Get full user profile."""
        if self.memory:
            return self.memory.profile.get()
        return {}

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        """Search semantic memory (Layer 3)."""
        if self.memory:
            return self.memory.semantic.search(query, limit)
        return []

    def get_conversation_summaries(self, limit: int = 5) -> list[dict]:
        """Get episodic summaries (Layer 2)."""
        if self.memory:
            return self.memory.episodic.get_recent(limit)
        return []

    def end_session(self, summary: str | None = None):
        """
        End conversation session.
        Optionally provide a summary to save to episodic memory.
        """
        if self.memory:
            self.memory.end_session(summary)

    async def generate_session_summary(self) -> str:
        """
        Use LLM to generate a summary of the current session.
        Call this before end_session() for automatic summarization.
        """
        if not self.memory:
            return ""

        recent_text = self.memory.context.get_recent_text(n=_SUMMARY_RECENT_MESSAGES)
        if not recent_text:
            return ""

        summary_prompt = [
            {
                "role": "system",
                "content": "Summarize this conversation in 1-2 sentences. Focus on key topics, decisions, and user preferences revealed.",
            },
            {"role": "user", "content": recent_text},
        ]

        return await self.get_completion(summary_prompt, temperature=0.3, max_tokens=100)

    def log_decision(self, action: str, tool: str | None = None, success: bool = True):
        """Log a decision/action for agentic memory."""
        if self.memory:
            try:
                self.memory.decisions.log_decision(action, tool, success)
            except Exception as e:
                logger.warning("Failed to log decision (non-fatal): %s", e)

    def check_recent_failure(self, action: str) -> bool:
        """Check if action failed recently (prevents loops)."""
        if self.memory:
            return self.memory.decisions.has_recent_failure(action)
        return False

    # ========== Tool Calling Methods ==========

    def register_tool(
        self, name: str, description: str, parameters: dict, func: Callable
    ) -> "LLMPipeline":
        """
        Register a tool for function calling.

        Args:
            name: Function name
            description: What the function does
            parameters: JSON Schema for parameters
            func: The actual function to call (sync or async)

        Returns:
            self for chaining
        """
        self.tools.register(name, description, parameters, func)
        return self

    def tool(self, name: str, description: str, parameters: dict):
        """Decorator to register a tool."""
        return self.tools.tool(name, description, parameters)

    def set_adapter(self, adapter: ModelAdapter):
        """Set model adapter (OpenAI, Anthropic, etc)."""
        self.adapter = adapter

    def load_tools_from_db(self):
        """
        Load tool schemas from database for LLM.
        Execution happens via ToolExecutor which fetches from DB at runtime.
        """
        self.tool_store = ToolStore()
        tools = self.tool_store.list_all()
        logger.info("Loaded %d tool schemas from DB", len(tools))
        for t in tools:
            logger.info("  - Tool: %s (enabled=%s)", t.name, t.enabled)

    def set_executor(self, executor: ToolExecutor):
        """Set custom tool executor with sandbox config."""
        self.tool_executor = executor

    def set_canvas_callback(self, callback: Callable[[list[dict]], Any]):
        """
        Set callback for canvas events.
        Called whenever canvas_update tool is executed.

        Args:
            callback: Function that receives list of canvas operations
        """
        self.canvas_callback = callback

    def set_animation_callback(self, callback: Callable[[dict], Any]):
        """
        Set callback for animation events (teach_with_visuals SDL tool).

        Args:
            callback: Function that receives animation data dict
        """
        self.animation_callback = callback

    def switch_provider(self, provider: str, api_key: str | None = None, model: str | None = None):
        """Hot-swap LLM provider for this pipeline. Used for model routing."""
        if provider == self.provider and model is None:
            return  # No change needed
        self.provider = provider
        self.client = create_llm_client(provider, api_key, model)

    def get_last_call_metrics(self) -> dict | None:
        """Get timing and tool call data from the most recent chat_with_tools_stream call."""
        if not self._last_call_timing:
            return None
        return {
            **self._last_call_timing,
            "tool_calls": self._last_call_tool_calls,
            "response_text": self._last_call_response,
            "error": self._last_call_error,
        }

    def get_canvas_context(self) -> str:
        """Get current canvas state summary for LLM context."""
        state = get_canvas_state(self.session_id)
        return state.get_context_summary()

    def get_tools_schema(self, include_canvas: bool | None = None) -> list[dict]:
        """
        Get tools schema for LLM.
        Prefers DB tools, falls back to in-memory registry.

        Args:
            include_canvas: Whether to include canvas tool. If None, uses canvas_mode setting.
        """
        tools: list[dict] = []

        if self.tool_store:
            if config.LLM_TOOL_SCHEMA_CACHE:
                db_tools = self.tool_store.to_openai_format()
            else:
                db_tools = [
                    t.to_openai_schema() for t in self.tool_store.list_all(enabled_only=True)
                ]
            # Deep-copy so canvas/animation appends don't mutate shared objects.
            tools = copy.deepcopy(db_tools)
        elif self.tools.list_names():
            tools = self.adapter.format_tools(self.tools)

        # Include canvas tool based on mode or explicit parameter
        should_include_canvas = include_canvas if include_canvas is not None else self.canvas_mode

        if should_include_canvas:
            # Check if already in tools
            has_canvas = any(t.get("function", {}).get("name") == "canvas_update" for t in tools)
            if not has_canvas:
                tools.append(CANVAS_TOOL_SCHEMA)

            # Include animation tools when canvas mode is enabled
            existing_tool_names = {t.get("function", {}).get("name") for t in tools}
            for anim_tool in ANIMATION_TOOLS:
                tool_name = anim_tool.get("function", {}).get("name")
                if tool_name and tool_name not in existing_tool_names:
                    tools.append(anim_tool)
                    logger.debug("Added animation tool: %s", tool_name)

        return tools

import os
import logging
from typing import List, Dict, Optional, AsyncGenerator, Callable, Any
from funcs.memory import MemoryManager
from funcs.tools import ToolRegistry, ToolStore, OpenAIAdapter, AnthropicAdapter, ModelAdapter, ToolCall
from funcs.tool_executor import ToolExecutor, SandboxConfig, default_executor
from funcs.canvas import get_canvas_state, CANVAS_TOOL_SCHEMA
from funcs.llm_clients import LLMClient, create_llm_client
from funcs.config import config

logger = logging.getLogger("llm-pipeline")


class LLMPipeline:
    """
    LLM conversation pipeline with 4-layer memory architecture.
    
    Memory Layers:
    1. Conversation Context (short-term) - sliding window
    2. Episodic Memory - conversation summaries
    3. Semantic Memory - vector search via Mem0
    4. User Profile - canonical identity facts
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_context_messages: int = 20,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        enable_memory: bool = True,
        canvas_mode: bool = False,
        canvas_system_prompt: Optional[str] = None
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
            enable_memory: Whether to enable memory layers
            canvas_mode: Whether canvas visual mode is enabled (uses canvas for all responses)
            canvas_system_prompt: Custom canvas mode prompt (uses default if not provided)
        """
        self.provider = provider or config.LLM_PROVIDER
        self.api_key = api_key  # Can be None, factory will get from config
        self.model = model  # Can be None, factory will get from config

        # Create LLM client using factory
        self.client: LLMClient = create_llm_client(
            provider=self.provider,
            api_key=self.api_key,
            model=self.model
        )

        self.user_id = user_id
        self.enable_memory = enable_memory
        
        # Canvas mode settings
        self.canvas_mode = canvas_mode
        self._canvas_system_prompt = canvas_system_prompt
        
        self.base_system_prompt = system_prompt or (
            "You are a helpful voice assistant. Provide concise, natural responses "
            "suitable for voice interaction. Keep answers brief unless more detail is requested."
        )
        
        # Initialize unified memory manager (all 4 layers)
        self.memory: Optional[MemoryManager] = None
        if enable_memory:
            try:
                self.memory = MemoryManager(user_id=user_id, session_id=session_id)
                self.memory.context.max_messages = max_context_messages
                logger.info("4-layer memory initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize memory: {e}. Continuing without.")
                self.enable_memory = False
        
        # Tool calling support
        self.tools = ToolRegistry()
        self.tool_store: Optional[ToolStore] = None
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
        self.canvas_callback: Optional[Callable[[List[Dict]], Any]] = None
        self.animation_callback: Optional[Callable[[Dict], Any]] = None

        # Call metrics (populated after each chat_with_tools_stream call)
        self._last_call_timing: Optional[Dict] = None
        self._last_call_tool_calls: List[Dict] = []
        self._last_call_response: Optional[str] = None
        self._last_call_error: Optional[str] = None

        logger.info(f"LLM pipeline initialized: provider={self.provider}, model={self.model or 'default'}, memory={enable_memory}, canvas_mode={canvas_mode}")
    
    @property
    def active_system_prompt(self) -> str:
        """Get the currently active system prompt based on canvas mode."""
        if self.canvas_mode:
            if self._canvas_system_prompt:
                return self._canvas_system_prompt
            # Default to math tutor prompt from config
            return config.LLM_MATH_TUTOR_PROMPT
        return self.base_system_prompt
    
    def set_canvas_mode(self, enabled: bool, custom_prompt: Optional[str] = None):
        """
        Toggle canvas mode on/off.
        
        Args:
            enabled: Whether to enable canvas mode
            custom_prompt: Optional custom canvas prompt
        """
        self.canvas_mode = enabled
        if custom_prompt:
            self._canvas_system_prompt = custom_prompt
        logger.info(f"Canvas mode {'enabled' if enabled else 'disabled'}")
    
    def get_context(self, current_query: Optional[str] = None, include_canvas: bool = True) -> List[Dict[str, str]]:
        """
        Get conversation context enriched with all memory layers.
        
        Args:
            current_query: Current user query for semantic search
            include_canvas: Whether to include canvas state in context
        """
        # Use canvas prompt when canvas mode is enabled
        system_prompt = self.active_system_prompt
        
        if self.memory and current_query:
            context = self.memory.build_context(current_query, system_prompt)
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
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """Get completion from LLM using provider-agnostic client."""
        try:
            return await self.client.complete(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
        except Exception as e:
            logger.exception(f"LLM completion error: {e}")
            raise
    
    async def chat(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True
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
        context = self.get_context(user_message)
        
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
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat interface using provider-agnostic client.

        Yields:
            Text chunks as they arrive
        """
        # Build context with all memory layers
        context = self.get_context(user_message)
        context.append({"role": "user", "content": user_message})

        full_response = ""

        try:
            async for chunk in self.client.stream(
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens
            ):
                full_response += chunk
                yield chunk

        except Exception as e:
            logger.exception(f"LLM stream error: {e}")
            raise

        # Process for memory after stream completes
        if self.memory:
            self.memory.process_for_memory(user_message, full_response, save_semantic=save_to_memory)
    
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
    
    def get_user_profile(self) -> Dict:
        """Get full user profile."""
        if self.memory:
            return self.memory.profile.get()
        return {}
    
    def search_memories(self, query: str, limit: int = 5) -> List[Dict]:
        """Search semantic memory (Layer 3)."""
        if self.memory:
            return self.memory.semantic.search(query, limit)
        return []
    
    def get_conversation_summaries(self, limit: int = 5) -> List[Dict]:
        """Get episodic summaries (Layer 2)."""
        if self.memory:
            return self.memory.episodic.get_recent(limit)
        return []
    
    def end_session(self, summary: Optional[str] = None):
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
        
        recent_text = self.memory.context.get_recent_text(n=10)
        if not recent_text:
            return ""
        
        summary_prompt = [
            {"role": "system", "content": "Summarize this conversation in 1-2 sentences. Focus on key topics, decisions, and user preferences revealed."},
            {"role": "user", "content": recent_text}
        ]
        
        return await self.get_completion(summary_prompt, temperature=0.3, max_tokens=100)
    
    def log_decision(self, action: str, tool: Optional[str] = None, success: bool = True):
        """Log a decision/action for agentic memory."""
        if self.memory:
            self.memory.decisions.log_decision(action, tool, success)
    
    def check_recent_failure(self, action: str) -> bool:
        """Check if action failed recently (prevents loops)."""
        if self.memory:
            return self.memory.decisions.has_recent_failure(action)
        return False
    
    # ========== Tool Calling Methods ==========
    
    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict,
        func: Callable
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
    
    def tool(self, name: str, description: str, parameters: Dict):
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
        logger.info(f"Loaded {len(tools)} tool schemas from DB")
        for t in tools:
            logger.info(f"  - Tool: {t.name} (enabled={t.enabled})")
    
    def set_executor(self, executor: ToolExecutor):
        """Set custom tool executor with sandbox config."""
        self.tool_executor = executor
    
    def set_canvas_callback(self, callback: Callable[[List[Dict]], Any]):
        """
        Set callback for canvas events.
        Called whenever canvas_update tool is executed.

        Args:
            callback: Function that receives list of canvas operations
        """
        self.canvas_callback = callback

    def set_animation_callback(self, callback: Callable[[Dict], Any]):
        """
        Set callback for animation events.
        Called whenever animation tools (animate_element, render_latex, etc.) are executed.

        Args:
            callback: Function that receives animation data dict
        """
        self.animation_callback = callback

    def get_last_call_metrics(self) -> Optional[Dict]:
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
    
    def get_tools_schema(self, include_canvas: Optional[bool] = None) -> List[Dict]:
        """
        Get tools schema for LLM.
        Prefers DB tools, falls back to in-memory registry.
        
        Args:
            include_canvas: Whether to include canvas tool. If None, uses canvas_mode setting.
        """
        tools = []
        
        if self.tool_store:
            tools = self.tool_store.to_openai_format()
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
            try:
                from funcs.canvas import ANIMATION_TOOLS

                # Add animation tools if not already present
                existing_tool_names = {t.get("function", {}).get("name") for t in tools}
                for anim_tool in ANIMATION_TOOLS:
                    tool_name = anim_tool.get("function", {}).get("name")
                    if tool_name and tool_name not in existing_tool_names:
                        tools.append(anim_tool)
                        logger.debug(f"Added animation tool: {tool_name}")
            except ImportError:
                logger.warning("Animation tools not available")

        return tools
    
    async def chat_with_tools(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10
    ) -> str:
        """
        Chat with automatic tool calling.
        
        Flow:
        1. Send message to LLM with tool schemas
        2. If LLM returns tool_call → fetch tool from DB → execute in sandbox
        3. Return result to LLM
        4. Repeat until LLM returns text response
        
        Args:
            user_message: User's message
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            save_to_memory: Whether to save to long-term memory
            max_tool_rounds: Max number of tool call rounds (prevents infinite loops)
            
        Returns:
            Final assistant response after tool execution
        """
        context = self.get_context(user_message)
        context.append({"role": "user", "content": user_message})
        
        # Get tool schemas (from DB or in-memory registry)
        tools_schema = self.get_tools_schema() or None
        
        # Debug: Log tools being sent
        if tools_schema:
            logger.info(f"Sending {len(tools_schema)} tools to LLM: {[t['function']['name'] for t in tools_schema]}")
        else:
            logger.warning("No tools available to send to LLM")
        
        for round_num in range(max_tool_rounds):
            response = await self.client.complete_with_tools(
                messages=context,
                tools=tools_schema,
                temperature=temperature,
                max_tokens=max_tokens
            )

            # No tool calls - we're done
            if not self.client.has_tool_calls(response):
                result = self.client.get_response_content(response)
                if self.memory:
                    self.memory.process_for_memory(user_message, result, save_semantic=save_to_memory)
                return result

            # LLM wants to call tools
            tool_calls = self.client.parse_tool_calls(response)
            context.append(self.client.get_assistant_message(response))
            
            logger.info(f"Round {round_num + 1}: LLM requested {len(tool_calls)} tool call(s)")
            
            for tc in tool_calls:
                logger.info(f"Executing tool: {tc.name} with args: {tc.arguments}")
                
                # Special handling for canvas_update - inject session_id and broadcast
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations, session_id=self.session_id)

                    # Broadcast to client via callback
                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            # Sync callback
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )
                # Special handling for animation tools
                elif tc.name in ["animate_element", "render_latex", "create_teaching_sequence", "plot_function"]:
                    from funcs.animation_pipeline import (
                        animate_element,
                        render_latex,
                        create_teaching_sequence,
                        plot_function
                    )
                    from funcs.tools import ToolResult

                    # Execute animation tool with session_id
                    tool_handlers = {
                        "animate_element": animate_element,
                        "render_latex": render_latex,
                        "create_teaching_sequence": create_teaching_sequence,
                        "plot_function": plot_function
                    }

                    handler = tool_handlers[tc.name]
                    result = handler(**tc.arguments, session_id=self.session_id)

                    # Broadcast to client via animation callback
                    if self.animation_callback and result.get("success"):
                        animation_data = {
                            "tool": tc.name,
                            **result
                        }
                        try:
                            await self.animation_callback(animation_data)
                        except TypeError:
                            # Sync callback
                            self.animation_callback(animation_data)

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=f"{tc.name} executed successfully",
                        success=result.get("success", True)
                    )
                else:
                    # Execute via executor (fetches from DB, runs in sandbox)
                    tool_result = await self.tool_executor.execute_tool_call(tc)
                
                logger.info(f"Tool {tc.name} result: {tool_result.content[:100]}...")

                # Add result to context for LLM
                context.append(self.client.format_tool_result(tool_result))
                
                # Log for decision memory
                self.log_decision(
                    action=f"tool:{tc.name}",
                    tool=tc.name,
                    success=tool_result.success
                )
        
        # Exceeded max rounds
        logger.warning(f"Exceeded max tool rounds ({max_tool_rounds})")
        return "I encountered an issue processing your request. Please try again."
    
    async def chat_with_tools_stream(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat with tools.

        Note: Tool calls are executed non-streaming, only final response streams.

        Yields:
            Text chunks of final response
        """
        import time as _time

        t_start = _time.perf_counter()
        t_llm_total = 0.0
        t_tool_total = 0.0
        all_tool_calls: List[Dict] = []
        tokens_in = None
        tokens_out = None
        error_msg = None

        context = self.get_context(user_message)
        context.append({"role": "user", "content": user_message})

        tools_schema = self.get_tools_schema() or None
        tool_names = [t.get("function", {}).get("name") for t in (tools_schema or [])]
        logger.info(f"chat_with_tools_stream: {len(tool_names)} tools passed to LLM: {tool_names}")

        # Execute tool calls (non-streaming) until we get a text response
        for _ in range(max_tool_rounds):
            t_llm_start = _time.perf_counter()
            response = await self.client.complete_with_tools(
                messages=context,
                tools=tools_schema,
                temperature=temperature,
                max_tokens=max_tokens
            )
            t_llm_total += _time.perf_counter() - t_llm_start

            # Extract token usage if available
            if hasattr(response, 'usage') and response.usage:
                tokens_in = getattr(response.usage, 'prompt_tokens', None)
                tokens_out = getattr(response.usage, 'completion_tokens', None)

            if not self.client.has_tool_calls(response):
                logger.info("LLM returned no tool calls — streaming final text response")
                break

            tool_calls = self.client.parse_tool_calls(response)
            logger.info(f"LLM called {len(tool_calls)} tools: {[tc.name for tc in tool_calls]}")
            context.append(self.client.get_assistant_message(response))

            for tc in tool_calls:
                all_tool_calls.append({"name": tc.name, "arguments": tc.arguments})
                t_tool_start = _time.perf_counter()

                # Special handling for canvas_update
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations, session_id=self.session_id)

                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )
                # Special handling for animation tools
                elif tc.name in ["animate_element", "render_latex", "create_teaching_sequence", "plot_function"]:
                    from funcs.animation_pipeline import (
                        animate_element,
                        render_latex,
                        create_teaching_sequence,
                        plot_function
                    )
                    from funcs.tools import ToolResult

                    tool_handlers = {
                        "animate_element": animate_element,
                        "render_latex": render_latex,
                        "create_teaching_sequence": create_teaching_sequence,
                        "plot_function": plot_function
                    }

                    handler = tool_handlers[tc.name]
                    result = handler(**tc.arguments, session_id=self.session_id)

                    if self.animation_callback and result.get("success"):
                        animation_data = {"tool": tc.name, **result}
                        try:
                            await self.animation_callback(animation_data)
                        except TypeError:
                            self.animation_callback(animation_data)

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=f"{tc.name} executed successfully",
                        success=result.get("success", True)
                    )
                else:
                    tool_result = await self.tool_executor.execute_tool_call(tc)

                t_tool_total += _time.perf_counter() - t_tool_start
                context.append(self.client.format_tool_result(tool_result))
                self.log_decision(action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success)

        # Now stream the final response
        t_stream_start = _time.perf_counter()
        full_response = ""

        async for chunk in self.client.stream(
            messages=context,
            temperature=temperature,
            max_tokens=max_tokens
        ):
            full_response += chunk
            yield chunk

        t_stream_end = _time.perf_counter()
        t_end = _time.perf_counter()

        if self.memory:
            self.memory.process_for_memory(user_message, full_response, save_semantic=save_to_memory)

        # Store metrics for caller to retrieve
        self._last_call_timing = {
            "latency_total_ms": round((t_end - t_start) * 1000, 2),
            "latency_llm_ms": round(t_llm_total * 1000, 2),
            "latency_tool_ms": round(t_tool_total * 1000, 2),
            "latency_stream_ms": round((t_stream_end - t_stream_start) * 1000, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        self._last_call_tool_calls = all_tool_calls
        self._last_call_response = full_response
        self._last_call_error = error_msg
        logger.info(f"Call metrics: total={self._last_call_timing['latency_total_ms']}ms, llm={self._last_call_timing['latency_llm_ms']}ms, tools={self._last_call_timing['latency_tool_ms']}ms, stream={self._last_call_timing['latency_stream_ms']}ms")

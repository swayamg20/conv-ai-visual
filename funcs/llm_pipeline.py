import os
import json
import time
import copy
import asyncio
import logging
from typing import List, Dict, Optional, AsyncGenerator, Callable, Any, Tuple
from funcs.memory import MemoryManager
from funcs.tools import ToolRegistry, ToolStore, OpenAIAdapter, AnthropicAdapter, ModelAdapter, ToolCall
from funcs.tool_executor import ToolExecutor, SandboxConfig, default_executor
from funcs.canvas import get_canvas_state, CANVAS_TOOL_SCHEMA, ANIMATION_TOOLS
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
    MUTATING_TOOL_NAMES = {
        "canvas_update",
        "teach_with_visuals",
    }
    
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
    
    async def get_context(self, current_query: Optional[str] = None, include_canvas: bool = True) -> List[Dict[str, str]]:
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
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True
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
        Set callback for animation events (teach_with_visuals SDL tool).

        Args:
            callback: Function that receives animation data dict
        """
        self.animation_callback = callback

    def switch_provider(self, provider: str, api_key: Optional[str] = None, model: Optional[str] = None):
        """Hot-swap LLM provider for this pipeline. Used for model routing."""
        if provider == self.provider and model is None:
            return  # No change needed
        self.provider = provider
        self.client = create_llm_client(provider, api_key, model)

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
        tools: List[Dict] = []

        if self.tool_store:
            if config.LLM_TOOL_SCHEMA_CACHE:
                db_tools = self.tool_store.to_openai_format()
            else:
                db_tools = [t.to_openai_schema() for t in self.tool_store.list_all(enabled_only=True)]
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
                    logger.debug(f"Added animation tool: {tool_name}")

        return tools

    @staticmethod
    def _tool_calls_to_assistant_message(tool_calls: List[ToolCall], content: Optional[str] = None) -> Dict[str, Any]:
        """Build OpenAI-style assistant message containing tool calls."""
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        }

    def _is_mutating_tool(self, tool_name: str) -> bool:
        return tool_name in self.MUTATING_TOOL_NAMES

    async def _execute_single_tool_call(self, tc: ToolCall):
        """Execute one tool call, including canvas/animation side effects."""
        from funcs.tools import ToolResult

        if tc.name == "canvas_update":
            from funcs.canvas import canvas_update

            operations = tc.arguments.get("operations", [])
            result = canvas_update(operations, session_id=self.session_id)

            if self.canvas_callback and result.get("operations"):
                try:
                    await self.canvas_callback(result["operations"])
                except TypeError:
                    self.canvas_callback(result["operations"])

            return ToolResult(
                tool_call_id=tc.id,
                content=result.get("canvas_summary", "Canvas updated"),
                success=True,
            )

        # SDL v2 tool — teach_with_visuals
        if tc.name == "teach_with_visuals":
            from funcs.animation_pipeline import teach_with_visuals

            result = teach_with_visuals(**tc.arguments, session_id=self.session_id)

            if self.animation_callback and result.get("success"):
                animation_data = {"tool": "teach_with_visuals", "sdl": result["sdl"]}
                try:
                    await self.animation_callback(animation_data)
                except TypeError:
                    self.animation_callback(animation_data)

            return ToolResult(
                tool_call_id=tc.id,
                content=result.get("speech_text", "Visual explanation rendered."),
                success=True,
            )

        return await self.tool_executor.execute_tool_call(tc)

    async def _execute_tool_calls_with_policy(self, tool_calls: List[ToolCall]) -> Tuple[List[Any], float]:
        """
        Execute tool calls with deterministic policy:
        - mutating tools execute sequentially
        - non-mutating tools may run in parallel
        """
        t_start = time.perf_counter()
        ordered_results: List[Any] = [None] * len(tool_calls)

        if not tool_calls:
            return [], 0.0

        if not config.LLM_PARALLEL_TOOLS or len(tool_calls) == 1:
            for idx, tc in enumerate(tool_calls):
                ordered_results[idx] = await self._execute_single_tool_call(tc)
            return ordered_results, time.perf_counter() - t_start

        pending_indices: List[int] = []
        pending_calls: List[ToolCall] = []

        async def _flush_parallel_batch():
            if not pending_calls:
                return
            batch_results = await self.tool_executor.execute_batch(pending_calls)
            for result_idx, tool_result in enumerate(batch_results):
                ordered_results[pending_indices[result_idx]] = tool_result
            pending_indices.clear()
            pending_calls.clear()

        for idx, tc in enumerate(tool_calls):
            if self._is_mutating_tool(tc.name):
                await _flush_parallel_batch()
                ordered_results[idx] = await self._execute_single_tool_call(tc)
            else:
                pending_indices.append(idx)
                pending_calls.append(tc)

        await _flush_parallel_batch()
        return ordered_results, time.perf_counter() - t_start
    
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
        context = await self.get_context(user_message)
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

            tool_results, _ = await self._execute_tool_calls_with_policy(tool_calls)
            for tc, tool_result in zip(tool_calls, tool_results):
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
    
    @staticmethod
    def _extract_speech_cues(tool_calls_data: List[Dict]) -> Optional[str]:
        """Extract speech text from teach_with_visuals tool call arguments.

        Concatenates 'say' fields into a single TTS-ready string, letting us
        skip the second LLM round entirely.

        Returns None if no speech cues found.
        """
        cues = []
        for tc in tool_calls_data:
            args = tc.get("arguments", {})
            tool_name = tc.get("name", "")
            if tool_name == "teach_with_visuals":
                for step in args.get("steps", []):
                    cue = step.get("say", "")
                    if cue and cue.strip():
                        cues.append(cue.strip())
        if cues:
            return " ".join(cues)
        return None

    async def _chat_with_tools_stream_legacy(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat with tools.

        Optimisation: if tool calls contain speech_cue fields (teaching sequences),
        the spoken explanation is extracted directly and yielded as the TTS text,
        skipping the second LLM inference round entirely. This saves ~3-5s.

        Yields:
            Text chunks of final response
        """
        t_start = time.perf_counter()
        t_llm_total = 0.0
        t_tool_total = 0.0
        all_tool_calls: List[Dict] = []
        tokens_in = None
        tokens_out = None
        error_msg = None
        speech_cue_text = None  # extracted from tool calls if available
        t_first_provider_chunk = None
        t_first_text_yield = None
        t_context_start = time.perf_counter()
        t_context_end = None
        t_tools_schema_end = None

        context = await self.get_context(user_message)
        t_context_end = time.perf_counter()
        context.append({"role": "user", "content": user_message})

        tools_schema = self.get_tools_schema() or None
        t_tools_schema_end = time.perf_counter()
        tool_names = [t.get("function", {}).get("name") for t in (tools_schema or [])]
        logger.info(f"chat_with_tools_stream: {len(tool_names)} tools passed to LLM: {tool_names}")

        # Execute tool calls (non-streaming) until we get a text response
        for round_num in range(max_tool_rounds):
            t_llm_start = time.perf_counter()
            response = await self.client.complete_with_tools(
                messages=context,
                tools=tools_schema,
                temperature=temperature,
                max_tokens=max_tokens
            )
            t_llm_total += time.perf_counter() - t_llm_start

            # Extract token usage if available
            if hasattr(response, 'usage') and response.usage:
                tokens_in = getattr(response.usage, 'prompt_tokens', None)
                tokens_out = getattr(response.usage, 'completion_tokens', None)

            if not self.client.has_tool_calls(response):
                logger.info("LLM returned no tool calls — streaming final text response")
                break

            tool_calls = self.client.parse_tool_calls(response)
            logger.info(f"Round {round_num+1}: LLM called {len(tool_calls)} tools: {[tc.name for tc in tool_calls]}")
            context.append(self.client.get_assistant_message(response))

            round_tool_data = []
            tool_results, tool_elapsed = await self._execute_tool_calls_with_policy(tool_calls)
            t_tool_total += tool_elapsed
            for tc, tool_result in zip(tool_calls, tool_results):
                tc_data = {"name": tc.name, "arguments": tc.arguments}
                all_tool_calls.append(tc_data)
                round_tool_data.append(tc_data)
                context.append(self.client.format_tool_result(tool_result))
                self.log_decision(action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success)

            # Check if we can extract speech cues from this round's tool calls
            extracted = self._extract_speech_cues(round_tool_data)
            if extracted:
                speech_cue_text = extracted

        # ── Yield the response text ──
        t_stream_start = time.perf_counter()
        full_response = ""

        if speech_cue_text:
            # We have speech cues from tool calls — use them directly as TTS text.
            # This skips the entire second LLM inference round.
            logger.info(f"Using speech_cues as response ({len(speech_cue_text)} chars), skipping 2nd LLM round")
            full_response = speech_cue_text
            if t_first_text_yield is None:
                t_first_text_yield = time.perf_counter()
            yield speech_cue_text
        else:
            # No speech cues — fall back to streaming the LLM's text response
            async for chunk in self.client.stream(
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens
            ):
                if t_first_provider_chunk is None:
                    t_first_provider_chunk = time.perf_counter()
                full_response += chunk
                if t_first_text_yield is None:
                    t_first_text_yield = time.perf_counter()
                yield chunk

        t_stream_end = time.perf_counter()
        t_end = time.perf_counter()

        if self.memory:
            self.memory.process_for_memory(user_message, full_response, save_semantic=save_to_memory)

        self._last_call_timing = {
            "latency_total_ms": round((t_end - t_start) * 1000, 2),
            "latency_llm_ms": round(t_llm_total * 1000, 2),
            "latency_tool_ms": round(t_tool_total * 1000, 2),
            "latency_context_ms": round(((t_context_end or t_start) - t_context_start) * 1000, 2),
            "latency_tools_schema_ms": round(((t_tools_schema_end or t_start) - (t_context_end or t_start)) * 1000, 2),
            "latency_first_provider_chunk_ms": (
                round((t_first_provider_chunk - t_start) * 1000, 2)
                if t_first_provider_chunk else None
            ),
            "latency_llm_first_token_ms": (
                round((t_first_text_yield - t_start) * 1000, 2)
                if t_first_text_yield else None
            ),
            "latency_stream_ms": round((t_stream_end - t_stream_start) * 1000, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        self._last_call_tool_calls = all_tool_calls
        self._last_call_response = full_response
        self._last_call_error = error_msg
        used_cues = "speech_cues" if speech_cue_text else "llm_stream"
        logger.info(f"Call metrics ({used_cues}): total={self._last_call_timing['latency_total_ms']}ms, llm={self._last_call_timing['latency_llm_ms']}ms, tools={self._last_call_timing['latency_tool_ms']}ms, stream={self._last_call_timing['latency_stream_ms']}ms")

    async def chat_with_tools_stream(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat with tool support.

        - Legacy path: non-streaming planner rounds + final stream.
        - New path (flagged): stream_with_tools orchestration so text can be emitted earlier.
        """
        if not config.LLM_STREAM_TOOL_ORCHESTRATION:
            async for chunk in self._chat_with_tools_stream_legacy(
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
                save_to_memory=save_to_memory,
                max_tool_rounds=max_tool_rounds,
            ):
                yield chunk
            return

        t_start = time.perf_counter()
        t_context_start = t_start
        t_context_end = None
        t_tools_schema_end = None
        t_llm_total = 0.0
        t_tool_total = 0.0
        t_stream_start = None
        t_first_provider_chunk = None
        t_first_text_yield = None
        tokens_in = None
        tokens_out = None
        error_msg = None
        speech_cue_text = None
        all_tool_calls: List[Dict] = []
        full_response = ""

        try:
            context = await self.get_context(user_message)
            t_context_end = time.perf_counter()
            context.append({"role": "user", "content": user_message})

            tools_schema = self.get_tools_schema() or None
            t_tools_schema_end = time.perf_counter()
            tool_names = [t.get("function", {}).get("name") for t in (tools_schema or [])]
            logger.info(
                "chat_with_tools_stream(orchestrated): %d tools passed to LLM: %s",
                len(tool_names),
                tool_names,
            )

            exhausted_rounds = True
            for round_num in range(max_tool_rounds):
                round_tool_calls: List[ToolCall] = []

                t_llm_start = time.perf_counter()
                provider_stream = self.client.stream_with_tools(
                    messages=context,
                    tools=tools_schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                async for event in self.client.iter_stream_tool_events(provider_stream):
                    if t_first_provider_chunk is None:
                        t_first_provider_chunk = time.perf_counter()

                    event_type = event.get("type")
                    if event_type == "text_delta":
                        text = event.get("text") or ""
                        if not text:
                            continue
                        full_response += text
                        if t_first_text_yield is None:
                            t_first_text_yield = time.perf_counter()
                            t_stream_start = t_first_text_yield
                        yield text
                    elif event_type == "tool_call_done":
                        round_tool_calls = event.get("tool_calls") or []
                    elif event_type == "usage":
                        tokens_in = event.get("tokens_in", tokens_in)
                        tokens_out = event.get("tokens_out", tokens_out)

                t_llm_total += time.perf_counter() - t_llm_start

                if not round_tool_calls:
                    exhausted_rounds = False
                    logger.info("Round %d produced no tool calls — response streaming complete", round_num + 1)
                    break

                logger.info(
                    "Round %d (streamed) called %d tools: %s",
                    round_num + 1,
                    len(round_tool_calls),
                    [tc.name for tc in round_tool_calls],
                )
                context.append(
                    self._tool_calls_to_assistant_message(
                        round_tool_calls,
                        content=None,
                    )
                )

                round_tool_data = []
                for tc in round_tool_calls:
                    tc_data = {"name": tc.name, "arguments": tc.arguments}
                    round_tool_data.append(tc_data)
                    all_tool_calls.append(tc_data)

                tool_results, tool_elapsed = await self._execute_tool_calls_with_policy(round_tool_calls)
                t_tool_total += tool_elapsed
                for tc, tool_result in zip(round_tool_calls, tool_results):
                    context.append(self.client.format_tool_result(tool_result))
                    self.log_decision(action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success)

                extracted = self._extract_speech_cues(round_tool_data)
                if extracted:
                    exhausted_rounds = False
                    speech_cue_text = extracted
                    break

            if exhausted_rounds and not speech_cue_text:
                logger.warning(
                    "Exceeded max tool rounds (%d) in orchestrated path; falling back to final text stream",
                    max_tool_rounds,
                )
                async for chunk in self.client.stream(
                    messages=context,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if t_first_provider_chunk is None:
                        t_first_provider_chunk = time.perf_counter()
                    full_response += chunk
                    if t_first_text_yield is None:
                        t_first_text_yield = time.perf_counter()
                        t_stream_start = t_first_text_yield
                    yield chunk

            if speech_cue_text:
                if full_response and not full_response.endswith(" "):
                    full_response += " "
                full_response += speech_cue_text
                if t_first_text_yield is None:
                    t_first_text_yield = time.perf_counter()
                    t_stream_start = t_first_text_yield
                yield speech_cue_text

            if self.memory:
                self.memory.process_for_memory(user_message, full_response, save_semantic=save_to_memory)

        except Exception as e:
            error_msg = str(e)
            logger.exception("chat_with_tools_stream(orchestrated) error: %s", e)
            raise
        finally:
            t_end = time.perf_counter()
            stream_end = t_end if t_stream_start else t_end
            self._last_call_timing = {
                "latency_total_ms": round((t_end - t_start) * 1000, 2),
                "latency_llm_ms": round(t_llm_total * 1000, 2),
                "latency_tool_ms": round(t_tool_total * 1000, 2),
                "latency_context_ms": round(((t_context_end or t_start) - t_context_start) * 1000, 2),
                "latency_tools_schema_ms": round(((t_tools_schema_end or t_start) - (t_context_end or t_start)) * 1000, 2),
                "latency_first_provider_chunk_ms": (
                    round((t_first_provider_chunk - t_start) * 1000, 2)
                    if t_first_provider_chunk else None
                ),
                "latency_llm_first_token_ms": (
                    round((t_first_text_yield - t_start) * 1000, 2)
                    if t_first_text_yield else None
                ),
                "latency_stream_ms": (
                    round((stream_end - t_stream_start) * 1000, 2)
                    if t_stream_start else 0.0
                ),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
            self._last_call_tool_calls = all_tool_calls
            self._last_call_response = full_response
            self._last_call_error = error_msg
            logger.info(
                "Call metrics (orchestrated): total=%sms, llm=%sms, tools=%sms, ttft=%sms",
                self._last_call_timing["latency_total_ms"],
                self._last_call_timing["latency_llm_ms"],
                self._last_call_timing["latency_tool_ms"],
                self._last_call_timing.get("latency_llm_first_token_ms"),
            )

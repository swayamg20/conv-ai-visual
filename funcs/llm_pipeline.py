import os
import logging
from typing import List, Dict, Optional, AsyncGenerator, Callable, Any
from openai import AsyncOpenAI
from funcs.memory import MemoryManager
from funcs.tools import ToolRegistry, ToolStore, OpenAIAdapter, AnthropicAdapter, ModelAdapter, ToolCall
from funcs.tool_executor import ToolExecutor, SandboxConfig, default_executor

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
        model: str = "gpt-4o-mini",
        system_prompt: Optional[str] = None,
        max_context_messages: int = 20,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        enable_memory: bool = True
    ):
        """
        Initialize LLM pipeline with memory.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use for completions
            system_prompt: Base system prompt for the assistant
            max_context_messages: Max messages in context window
            user_id: User ID for memory isolation
            session_id: Session ID for episodic tracking
            enable_memory: Whether to enable memory layers
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")
        
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.model = model
        self.user_id = user_id
        self.enable_memory = enable_memory
        
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
        self.adapter: ModelAdapter = OpenAIAdapter()
        
        logger.info(f"LLM pipeline initialized: model={model}, memory={enable_memory}")
    
    def get_context(self, current_query: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Get conversation context enriched with all memory layers.
        
        Args:
            current_query: Current user query for semantic search
        """
        if self.memory and current_query:
            return self.memory.build_context(current_query, self.base_system_prompt)
        
        # Fallback: basic context without memory
        return [{"role": "system", "content": self.base_system_prompt}]
    
    async def get_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """Get completion from LLM."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()
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
        Streaming chat interface.
        
        Yields:
            Text chunks as they arrive
        """
        # Build context with all memory layers
        context = self.get_context(user_message)
        context.append({"role": "user", "content": user_message})
        
        full_response = ""
        
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_response += text
                    yield text
                    
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
    
    def get_tools_schema(self) -> List[Dict]:
        """
        Get tools schema for LLM.
        Prefers DB tools, falls back to in-memory registry.
        """
        if self.tool_store:
            return self.tool_store.to_openai_format()
        elif self.tools.list_names():
            return self.adapter.format_tools(self.tools)
        return []
    
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
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools_schema if tools_schema else None
            )
            
            # No tool calls - we're done
            if not self.adapter.has_tool_calls(response):
                result = self.adapter.get_response_content(response)
                if self.memory:
                    self.memory.process_for_memory(user_message, result, save_semantic=save_to_memory)
                return result
            
            # LLM wants to call tools
            tool_calls = self.adapter.parse_tool_calls(response)
            context.append(self.adapter.get_assistant_message(response))
            
            logger.info(f"Round {round_num + 1}: LLM requested {len(tool_calls)} tool call(s)")
            
            for tc in tool_calls:
                logger.info(f"Executing tool: {tc.name} with args: {tc.arguments}")
                
                # Execute via executor (fetches from DB, runs in sandbox)
                tool_result = await self.tool_executor.execute_tool_call(tc)
                
                logger.info(f"Tool {tc.name} result: {tool_result.content[:100]}...")
                
                # Add result to context for LLM
                context.append(self.adapter.format_tool_result(tool_result))
                
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
        context = self.get_context(user_message)
        context.append({"role": "user", "content": user_message})
        
        tools_schema = self.get_tools_schema() or None
        
        # Execute tool calls (non-streaming) until we get a text response
        for _ in range(max_tool_rounds):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools_schema
            )
            
            if not self.adapter.has_tool_calls(response):
                break
            
            tool_calls = self.adapter.parse_tool_calls(response)
            context.append(self.adapter.get_assistant_message(response))
            
            for tc in tool_calls:
                # Execute via executor (fetches from DB, runs in sandbox)
                tool_result = await self.tool_executor.execute_tool_call(tc)
                context.append(self.adapter.format_tool_result(tool_result))
                self.log_decision(action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success)
        
        # Now stream the final response
        full_response = ""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=context,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True
        )
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                full_response += text
                yield text
        
        if self.memory:
            self.memory.process_for_memory(user_message, full_response, save_semantic=save_to_memory)

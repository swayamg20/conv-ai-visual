import os
import logging
from typing import List, Dict, Optional, AsyncGenerator
from openai import AsyncOpenAI
from funcs.memory import MemoryManager

logger = logging.getLogger("llm-pipeline")


class LLMPipeline:
    """
    Handles LLM conversation pipeline with context management and memory.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        system_prompt: Optional[str] = None,
        max_context_messages: int = 20,
        user_id: str = "default_user",
        enable_memory: bool = True
    ):
        """
        Initialize LLM pipeline.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use for completions
            system_prompt: System prompt for the assistant
            max_context_messages: Maximum number of messages to keep in context
            user_id: User ID for memory isolation
            enable_memory: Whether to enable Mem0 memory
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")
        
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.model = model
        self.max_context_messages = max_context_messages
        self.user_id = user_id
        self.enable_memory = enable_memory
        
        self.base_system_prompt = system_prompt or (
            "You are a helpful voice assistant. Provide concise, natural responses "
            "suitable for voice interaction. Keep answers brief unless more detail is requested."
        )
        self.system_prompt = self.base_system_prompt
        
        # Initialize memory manager
        self.memory: Optional[MemoryManager] = None
        if enable_memory:
            try:
                self.memory = MemoryManager(user_id=user_id)
                logger.info("Memory manager initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize memory: {e}. Continuing without memory.")
                self.enable_memory = False
        
        logger.info(f"LLM pipeline initialized with model: {model}, memory: {enable_memory}")
    
    def create_conversation_context(self, initial_query: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Create a new conversation context with system prompt.
        Optionally enriches with relevant memories.
        
        Args:
            initial_query: Optional first user query to fetch relevant memories
        """
        system_content = self.base_system_prompt
        
        # Inject relevant memories into system prompt
        if self.memory and initial_query:
            memory_context = self.memory.get_context_for_prompt(initial_query)
            if memory_context:
                system_content = f"{self.base_system_prompt}\n\n{memory_context}"
        
        return [{"role": "system", "content": system_content}]
    
    def add_user_message(self, context: List[Dict[str, str]], message: str) -> List[Dict[str, str]]:
        """Add user message to context and trim if needed."""
        context.append({"role": "user", "content": message})
        return self._trim_context(context)
    
    def add_assistant_message(self, context: List[Dict[str, str]], message: str) -> List[Dict[str, str]]:
        """Add assistant message to context and trim if needed."""
        context.append({"role": "assistant", "content": message})
        return self._trim_context(context)
    
    def _trim_context(self, context: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Trim context to max_context_messages, preserving system prompt."""
        if len(context) <= self.max_context_messages + 1:  # +1 for system prompt
            return context
        
        # Keep system prompt + last N messages
        return [context[0]] + context[-(self.max_context_messages):]
    
    async def get_completion(
        self,
        context: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Get completion from LLM.
        
        Args:
            context: Conversation context
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            
        Returns:
            Assistant's response text
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=context,
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.exception(f"Error getting LLM completion: {e}")
            raise
    
    async def process_user_input(
        self,
        context: List[Dict[str, str]],
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True
    ) -> tuple[List[Dict[str, str]], str]:
        """
        Process user input and get assistant response.
        
        Args:
            context: Current conversation context
            user_message: User's message
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            save_to_memory: Whether to save this exchange to long-term memory
            
        Returns:
            Tuple of (updated_context, assistant_response)
        """
        # Enrich context with relevant memories if this is early in conversation
        if self.memory and len(context) <= 3:
            memory_context = self.memory.get_context_for_prompt(user_message)
            if memory_context and memory_context not in context[0]["content"]:
                context[0]["content"] = f"{self.base_system_prompt}\n\n{memory_context}"
        
        # Add user message
        context = self.add_user_message(context, user_message)
        
        # Get completion
        assistant_response = await self.get_completion(
            context,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        # Add assistant response to context
        context = self.add_assistant_message(context, assistant_response)
        
        # Save to long-term memory (async would be better but keeping simple)
        if self.memory and save_to_memory:
            try:
                self.memory.add([
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_response}
                ])
            except Exception as e:
                logger.warning(f"Failed to save to memory: {e}")
        
        return context, assistant_response

    async def get_completion_stream(
        self,
        context: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """
        Get streaming completion from LLM.
        Yields text chunks as they arrive.
        """
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
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.exception(f"Error getting LLM stream: {e}")
            raise

    async def process_user_input_stream(
        self,
        context: List[Dict[str, str]],
        user_message: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        save_to_memory: bool = True
    ) -> AsyncGenerator[tuple[str, bool, List[Dict[str, str]]], None]:
        """
        Process user input and stream assistant response.
        
        Yields:
            Tuples of (text_chunk, is_done, updated_context)
            is_done=True on final yield with full context
        """
        # Enrich context with relevant memories if this is early in conversation
        if self.memory and len(context) <= 3:
            memory_context = self.memory.get_context_for_prompt(user_message)
            if memory_context and memory_context not in context[0]["content"]:
                context[0]["content"] = f"{self.base_system_prompt}\n\n{memory_context}"
        
        context = self.add_user_message(context, user_message)
        full_response = ""
        
        async for chunk in self.get_completion_stream(context, temperature, max_tokens):
            full_response += chunk
            yield chunk, False, context
        
        # Add complete response to context
        context = self.add_assistant_message(context, full_response)
        
        # Save to long-term memory
        if self.memory and save_to_memory:
            try:
                self.memory.add([
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_response}
                ])
            except Exception as e:
                logger.warning(f"Failed to save to memory: {e}")
        
        yield "", True, context

    # Memory helper methods
    def get_memories(self, query: Optional[str] = None, limit: int = 10) -> List[Dict]:
        """Get memories - all or search by query."""
        if not self.memory:
            return []
        if query:
            return self.memory.search(query, limit=limit)
        return self.memory.get_all()
    
    def add_memory(self, text: str) -> Dict:
        """Manually add a memory."""
        if not self.memory:
            return {"error": "Memory not enabled"}
        return self.memory.add_text(text)
    
    def clear_memories(self) -> bool:
        """Clear all memories for this user."""
        if not self.memory:
            return False
        return self.memory.delete_all()

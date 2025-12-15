import os
import logging
from typing import List, Dict, Optional
from openai import AsyncOpenAI

logger = logging.getLogger("llm-pipeline")


class LLMPipeline:
    """
    Handles LLM conversation pipeline with context management.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        system_prompt: Optional[str] = None,
        max_context_messages: int = 20
    ):
        """
        Initialize LLM pipeline.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use for completions
            system_prompt: System prompt for the assistant
            max_context_messages: Maximum number of messages to keep in context
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")
        
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.model = model
        self.max_context_messages = max_context_messages
        
        self.system_prompt = system_prompt or (
            "You are a helpful voice assistant. Provide concise, natural responses "
            "suitable for voice interaction. Keep answers brief unless more detail is requested."
        )
        
        logger.info(f"LLM pipeline initialized with model: {model}")
    
    def create_conversation_context(self) -> List[Dict[str, str]]:
        """Create a new conversation context with system prompt."""
        return [{"role": "system", "content": self.system_prompt}]
    
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
        max_tokens: Optional[int] = None
    ) -> tuple[List[Dict[str, str]], str]:
        """
        Process user input and get assistant response.
        
        Args:
            context: Current conversation context
            user_message: User's message
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            
        Returns:
            Tuple of (updated_context, assistant_response)
        """
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
        
        return context, assistant_response


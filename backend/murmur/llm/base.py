"""Provider-neutral LLM client contract."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class LLMClient(ABC):
    """Abstract base class for LLM provider clients."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        """
        Non-streaming completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens in response
            **kwargs: Provider-specific parameters

        Returns:
            Response text as string
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion.

        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Provider-specific parameters

        Yields:
            Text chunks as they arrive
        """
        pass

    @abstractmethod
    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Any:
        """
        Non-streaming completion with tool support.

        Args:
            messages: List of message dicts
            tools: Tool schemas in provider format
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Provider-specific parameters

        Returns:
            Raw provider response object
        """
        pass

    @abstractmethod
    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[Any, None]:
        """
        Streaming completion with tool support.

        Args:
            messages: List of message dicts
            tools: Tool schemas in provider format
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Provider-specific parameters

        Yields:
            Raw response chunks
        """
        pass

    @abstractmethod
    async def iter_stream_tool_events(
        self, stream: AsyncGenerator[Any, None]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Normalize provider stream chunks into tool-aware events.

        Yields dict events:
          - {"type": "text_delta", "text": "..."}
          - {"type": "tool_call_delta", ...}
          - {"type": "tool_call_done", "tool_calls": [ToolCall, ...]}
          - {"type": "usage", "tokens_in": int|None, "tokens_out": int|None}
          - {"type": "done"}
        """
        pass

    @abstractmethod
    def has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls."""
        pass

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> list[Any]:
        """
        Extract tool calls from response.

        Returns:
            List of ToolCall objects
        """
        pass

    @abstractmethod
    def get_response_content(self, response: Any) -> str | None:
        """
        Get text content from response.

        Returns:
            Response text, or None if response contains tool calls
        """
        pass

    @abstractmethod
    def get_assistant_message(self, response: Any) -> dict:
        """
        Get assistant message to append to context.

        Returns:
            Message dict suitable for adding to conversation context
        """
        pass

    @abstractmethod
    def format_tool_result(self, result: Any) -> dict:
        """
        Format tool result for this provider.

        Args:
            result: ToolResult object

        Returns:
            Message dict in provider format
        """
        pass

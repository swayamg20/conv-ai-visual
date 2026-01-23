import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, AsyncGenerator, Any

logger = logging.getLogger("llm-clients")


class LLMClient(ABC):    
    @abstractmethod
    async def complete(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        pass
    @abstractmethod
    async def stream(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        pass

    @abstractmethod
    async def complete_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Any:
        pass

    @abstractmethod
    async def stream_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[Any, None]:
        pass

    @abstractmethod
    def has_tool_calls(self, response: Any) -> bool:
        pass

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> List[Any]:
        pass

    @abstractmethod
    def get_response_content(self, response: Any) -> Optional[str]:
        pass

    @abstractmethod
    def get_assistant_message(self, response: Any) -> Dict:
        pass

    @abstractmethod
    def format_tool_result(self, result: Any) -> Dict:
        pass

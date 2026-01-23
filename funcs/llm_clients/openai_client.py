import json
import logging
from typing import List, Dict, Optional, AsyncGenerator, Any
from openai import AsyncOpenAI
from .base import LLMClient
from funcs.tools import ToolCall

logger = logging.getLogger("llm-clients")

class OpenAIClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        **default_params
    ):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.default_params = default_params
        logger.info(f"OpenAI client initialized: model={model}")

    async def complete(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **{**self.default_params, **kwargs}
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.exception(f"OpenAI completion error: {e}")
            raise

    async def stream(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **{**self.default_params, **kwargs}
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.exception(f"OpenAI stream error: {e}")
            raise

    async def complete_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Any:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools if tools else None,
                **{**self.default_params, **kwargs}
            )
            return response
        except Exception as e:
            logger.exception(f"OpenAI completion with tools error: {e}")
            raise

    async def stream_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[Any, None]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools if tools else None,
                stream=True,
                **{**self.default_params, **kwargs}
            )

            async for chunk in stream:
                yield chunk
        except Exception as e:
            logger.exception(f"OpenAI stream with tools error: {e}")
            raise

    def has_tool_calls(self, response: Any) -> bool:
        return bool(response.choices[0].message.tool_calls)

    def parse_tool_calls(self, response: Any) -> List[Any]:
        message = response.choices[0].message
        if not message.tool_calls:
            return []

        return [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments)
            )
            for tc in message.tool_calls
        ]

    def get_response_content(self, response: Any) -> Optional[str]:
        message = response.choices[0].message
        if message.tool_calls:
            return None
        return message.content.strip() if message.content else ""

    def get_assistant_message(self, response: Any) -> Dict:
        return response.choices[0].message

    def format_tool_result(self, result: Any) -> Dict:
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": result.content
        }

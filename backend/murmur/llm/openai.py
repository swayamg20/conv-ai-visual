"""OpenAI and OpenAI-compatible provider implementation."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Literal

from murmur.core.async_cleanup import close_async_resource
from murmur.llm.base import LLMClient
from murmur.tools.contracts import ToolCall

logger = logging.getLogger(__name__)


async def _close_provider_resource(resource: object | None) -> None:
    if not await close_async_resource(resource):
        logger.warning("OpenAI provider resource cleanup did not finish cleanly")


class OpenAIClient(LLMClient):
    """LLM client for OpenAI API (and compatible APIs like Groq, Together, etc.)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
        **default_params,
    ):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key (or Groq/Together key for compatible APIs)
            model: Model name (e.g., "gpt-4o-mini", "llama-3.3-70b-versatile")
            base_url: Optional base URL override (e.g., "https://api.groq.com/openai/v1")
            max_tokens_parameter: Provider request field used for the output-token limit
            **default_params: Default parameters to include in all requests
        """
        from openai import AsyncOpenAI

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = AsyncOpenAI(**client_kwargs)
        self.model = model
        self.max_tokens_parameter = max_tokens_parameter
        self.default_params = default_params
        try:
            provider_label = base_url.split("//")[1].split("/")[0] if base_url else "openai"
        except (IndexError, AttributeError):
            provider_label = base_url or "openai"
        logger.info(
            f"OpenAI-compatible client initialized: model={model}, endpoint={provider_label}"
        )

    def _request_params(self, max_tokens: int | None, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Merge provider parameters and translate the shared output-token abstraction."""
        request_params = {**self.default_params, **kwargs}
        alternate_parameter = (
            "max_completion_tokens"
            if self.max_tokens_parameter == "max_tokens"
            else "max_tokens"
        )
        request_params.pop(alternate_parameter, None)
        if max_tokens is not None:
            request_params[self.max_tokens_parameter] = max_tokens
        return request_params

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        """Non-streaming completion."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                **self._request_params(max_tokens, kwargs),
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.exception(f"OpenAI completion error: {e}")
            raise

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion."""
        stream = None
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
                **self._request_params(max_tokens, kwargs),
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.exception(f"OpenAI stream error: {e}")
            raise
        finally:
            await _close_provider_resource(stream)

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Any:
        """Non-streaming completion with tools."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                tools=tools if tools else None,
                **self._request_params(max_tokens, kwargs),
            )
            return response
        except Exception as e:
            logger.exception(f"OpenAI completion with tools error: {e}")
            raise

    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[Any, None]:
        """Streaming completion with tools."""
        stream = None
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                tools=tools if tools else None,
                stream=True,
                **self._request_params(max_tokens, kwargs),
            )

            async for chunk in stream:
                yield chunk
        except Exception as e:
            logger.exception(f"OpenAI stream with tools error: {e}")
            raise
        finally:
            await _close_provider_resource(stream)

    async def aclose(self) -> None:
        """Close the owned OpenAI-compatible HTTP client."""
        await _close_provider_resource(self.client)

    async def iter_stream_tool_events(
        self, stream: AsyncGenerator[Any, None]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Normalize OpenAI-compatible streamed chunks into events."""
        tool_acc: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                yield {
                    "type": "usage",
                    "tokens_in": getattr(usage, "prompt_tokens", None),
                    "tokens_out": getattr(usage, "completion_tokens", None),
                }

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue

            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta and getattr(delta, "content", None):
                yield {"type": "text_delta", "text": delta.content}

            delta_tool_calls = getattr(delta, "tool_calls", None) if delta else None
            if delta_tool_calls:
                for tc in delta_tool_calls:
                    idx = getattr(tc, "index", 0) or 0
                    if idx not in tool_acc:
                        tool_acc[idx] = {"id": "", "name": "", "arguments": ""}

                    if getattr(tc, "id", None):
                        tool_acc[idx]["id"] = tc.id

                    fn = getattr(tc, "function", None)
                    if fn and getattr(fn, "name", None):
                        tool_acc[idx]["name"] = fn.name
                    if fn and getattr(fn, "arguments", None):
                        tool_acc[idx]["arguments"] += fn.arguments

                    yield {
                        "type": "tool_call_delta",
                        "index": idx,
                        "name": tool_acc[idx]["name"],
                    }

            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "tool_calls":
                tool_calls = []
                for idx in sorted(tool_acc.keys()):
                    tc_data = tool_acc[idx]
                    raw_args = tc_data["arguments"] or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except Exception:
                        logger.warning(
                            "Failed to parse streamed tool args for '%s': %s",
                            tc_data["name"],
                            raw_args[:200],
                        )
                        arguments = {}

                    tool_calls.append(
                        ToolCall(
                            id=tc_data["id"] or f"call_stream_{idx}",
                            name=tc_data["name"] or "unknown_tool",
                            arguments=arguments,
                        )
                    )

                tool_acc = {}
                yield {"type": "tool_call_done", "tool_calls": tool_calls}
            elif finish_reason == "stop":
                yield {"type": "done"}

    def has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls."""
        return bool(response.choices[0].message.tool_calls)

    def parse_tool_calls(self, response: Any) -> list[Any]:
        """Extract tool calls from OpenAI response."""
        message = response.choices[0].message
        if not message.tool_calls:
            return []

        tool_calls = []
        for tc in message.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Malformed tool call arguments for %s: %s",
                    tc.function.name,
                    tc.function.arguments,
                )
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))
        return tool_calls

    def get_response_content(self, response: Any) -> str | None:
        """Get text content from response."""
        message = response.choices[0].message
        if message.tool_calls:
            return None
        return message.content.strip() if message.content else ""

    def get_assistant_message(self, response: Any) -> dict:
        """Get assistant message for context."""
        return response.choices[0].message

    def format_tool_result(self, result: Any) -> dict:
        """Format tool result for OpenAI."""
        return {"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content}

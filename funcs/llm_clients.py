"""
LLM client abstraction layer for multiple providers.

This module provides a unified interface for interacting with different LLM providers
(OpenAI, Gemini, etc.) through the LLMClient abstract base class.
"""
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, AsyncGenerator, Any

logger = logging.getLogger("llm-clients")


class LLMClient(ABC):
    """Abstract base class for LLM provider clients."""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
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
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
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
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
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
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
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
    def has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls."""
        pass

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> List[Any]:
        """
        Extract tool calls from response.

        Returns:
            List of ToolCall objects
        """
        pass

    @abstractmethod
    def get_response_content(self, response: Any) -> Optional[str]:
        """
        Get text content from response.

        Returns:
            Response text, or None if response contains tool calls
        """
        pass

    @abstractmethod
    def get_assistant_message(self, response: Any) -> Dict:
        """
        Get assistant message to append to context.

        Returns:
            Message dict suitable for adding to conversation context
        """
        pass

    @abstractmethod
    def format_tool_result(self, result: Any) -> Dict:
        """
        Format tool result for this provider.

        Args:
            result: ToolResult object

        Returns:
            Message dict in provider format
        """
        pass


class OpenAIClient(LLMClient):
    """LLM client for OpenAI API (and compatible APIs like Groq, Together, etc.)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        **default_params
    ):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            model: Model name (e.g., "gpt-4o-mini")
            **default_params: Default parameters to include in all requests
        """
        from openai import AsyncOpenAI

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
        """Non-streaming completion."""
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
        """Streaming completion."""
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
        """Non-streaming completion with tools."""
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
        """Streaming completion with tools."""
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
        """Check if response contains tool calls."""
        return bool(response.choices[0].message.tool_calls)

    def parse_tool_calls(self, response: Any) -> List[Any]:
        """Extract tool calls from OpenAI response."""
        from funcs.tools import ToolCall

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
        """Get text content from response."""
        message = response.choices[0].message
        if message.tool_calls:
            return None
        return message.content.strip() if message.content else ""

    def get_assistant_message(self, response: Any) -> Dict:
        """Get assistant message for context."""
        return response.choices[0].message

    def format_tool_result(self, result: Any) -> Dict:
        """Format tool result for OpenAI."""
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": result.content
        }


class GeminiClient(LLMClient):
    """LLM client for Google Gemini API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        **default_params
    ):
        """
        Initialize Gemini client.

        Args:
            api_key: Google AI API key
            model: Model name (e.g., "gemini-2.0-flash-exp")
            **default_params: Default parameters
        """
        try:
            import google.generativeai as genai
            self.genai = genai
        except ImportError:
            raise ImportError(
                "google-generativeai package is required for Gemini support. "
                "Install it with: pip install google-generativeai"
            )

        self.genai.configure(api_key=api_key)
        self.model_name = model
        self.model = self.genai.GenerativeModel(model)
        self.default_params = default_params
        logger.info(f"Gemini client initialized: model={model}")

    def _convert_messages(self, messages: List[Dict]) -> tuple:
        """
        Convert OpenAI-style messages to Gemini format.

        Returns:
            Tuple of (system_prompt, history) where history is list of Gemini message dicts
        """
        system_prompt = ""
        history = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            # Ensure content is a string
            if content is None:
                content = ""
            elif not isinstance(content, str):
                # Handle list of content parts (OpenAI format)
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and "text" in part:
                            text_parts.append(part["text"])
                        elif isinstance(part, str):
                            text_parts.append(part)
                    content = "\n".join(text_parts)
                else:
                    content = str(content)

            if role == "system":
                system_prompt = content
            elif role == "user":
                if content:  # Skip empty messages
                    history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                if content:  # Skip empty messages (e.g., tool call only responses)
                    history.append({"role": "model", "parts": [content]})
            elif role == "tool":
                # Gemini handles tool results differently
                # For now, append as user message with tool result context
                if content:
                    history.append({
                        "role": "user",
                        "parts": [f"Tool result: {content}"]
                    })

        return system_prompt, history

    async def complete(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Non-streaming completion."""
        try:
            system_prompt, history = self._convert_messages(messages)

            # Gemini uses generation_config for parameters
            generation_config = {
                "temperature": temperature,
                **self.default_params,
                **kwargs
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            # If there's a system prompt, we need to include it
            if system_prompt:
                # Prepend system prompt to first user message
                if history and history[0]["role"] == "user":
                    history[0]["parts"][0] = f"{system_prompt}\n\n{history[0]['parts'][0]}"

            # Extract the last user message for generation
            if not history:
                raise ValueError("No messages to process")

            # Build the conversation
            chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])

            # Send the last message
            last_message = history[-1]["parts"][0] if history else ""
            response = await chat.send_message_async(last_message, generation_config=generation_config)

            return response.text
        except Exception as e:
            logger.exception(f"Gemini completion error: {e}")
            raise

    async def stream(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Streaming completion."""
        try:
            system_prompt, history = self._convert_messages(messages)

            generation_config = {
                "temperature": temperature,
                **self.default_params,
                **kwargs
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            if system_prompt and history and history[0]["role"] == "user":
                history[0]["parts"][0] = f"{system_prompt}\n\n{history[0]['parts'][0]}"

            if not history:
                raise ValueError("No messages to process")

            chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])
            last_message = history[-1]["parts"][0] if history else ""

            response = await chat.send_message_async(
                last_message,
                generation_config=generation_config,
                stream=True
            )

            async for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logger.exception(f"Gemini stream error: {e}")
            raise

    async def complete_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Any:
        """Non-streaming completion with tools."""
        try:
            system_prompt, history = self._convert_messages(messages)

            generation_config = {
                "temperature": temperature,
                **self.default_params,
                **kwargs
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            # Convert tools to Gemini format
            gemini_tools = None
            if tools:
                gemini_tools = self._convert_tools_to_gemini_format(tools)

            if system_prompt and history and history[0]["role"] == "user":
                history[0]["parts"][0] = f"{system_prompt}\n\n{history[0]['parts'][0]}"

            if not history:
                raise ValueError("No messages to process")

            # Debug: log history structure
            history_to_pass = history[:-1] if len(history) > 1 else []
            logger.info(f"History length: {len(history)}, passing {len(history_to_pass)} messages to chat")
            for i, h in enumerate(history_to_pass):
                if isinstance(h, dict):
                    parts_info = [f"type={type(p)}, len={len(p) if isinstance(p, str) else 'N/A'}" for p in h.get("parts", [])]
                    logger.info(f"  History[{i}]: role={h.get('role')}, parts=[{', '.join(parts_info)}]")
                else:
                    logger.info(f"  History[{i}]: UNEXPECTED type={type(h)}")

            # Create model with tools if provided
            if gemini_tools:
                model_with_tools = self.genai.GenerativeModel(
                    self.model_name,
                    tools=gemini_tools
                )
                chat = model_with_tools.start_chat(history=history_to_pass)
            else:
                chat = self.model.start_chat(history=history_to_pass)

            last_message = history[-1]["parts"][0] if history else ""
            response = await chat.send_message_async(last_message, generation_config=generation_config)

            return response
        except Exception as e:
            logger.exception(f"Gemini completion with tools error: {e}")
            raise

    async def stream_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[Any, None]:
        """Streaming completion with tools."""
        try:
            system_prompt, history = self._convert_messages(messages)

            generation_config = {
                "temperature": temperature,
                **self.default_params,
                **kwargs
            }
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            gemini_tools = None
            if tools:
                gemini_tools = self._convert_tools_to_gemini_format(tools)

            if system_prompt and history and history[0]["role"] == "user":
                history[0]["parts"][0] = f"{system_prompt}\n\n{history[0]['parts'][0]}"

            if not history:
                raise ValueError("No messages to process")

            if gemini_tools:
                model_with_tools = self.genai.GenerativeModel(
                    self.model_name,
                    tools=gemini_tools
                )
                chat = model_with_tools.start_chat(history=history[:-1] if len(history) > 1 else [])
            else:
                chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])

            last_message = history[-1]["parts"][0] if history else ""
            response = await chat.send_message_async(
                last_message,
                generation_config=generation_config,
                stream=True
            )

            async for chunk in response:
                yield chunk
        except Exception as e:
            logger.exception(f"Gemini stream with tools error: {e}")
            raise

    def _convert_tools_to_gemini_format(self, tools: List[Dict]) -> List:
        """
        Convert OpenAI tool format to Gemini function declarations.

        Gemini uses the high-level API which accepts dict-based function declarations.
        """
        gemini_tools = []

        for tool in tools:
            if "function" in tool:
                func = tool["function"]
                params = func.get("parameters", {})

                # Convert JSON Schema to Gemini-compatible format
                gemini_schema = self._convert_json_schema_to_gemini(params)

                # Gemini's high-level API accepts dicts with specific structure
                gemini_tool = {
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "parameters": gemini_schema
                }
                gemini_tools.append(gemini_tool)
                
                # Debug log the converted schema
                logger.debug(f"Converted tool {func['name']} to Gemini format:\n{json.dumps(gemini_tool, indent=2)}")

        return gemini_tools

    def _convert_json_schema_to_gemini(self, json_schema: Dict) -> Dict:
        """
        Convert JSON Schema to Gemini-compatible schema format.

        Gemini uses uppercase type names (STRING, NUMBER, etc.) instead of
        lowercase (string, number, etc.) used in standard JSON Schema.
        """
        # Type mapping from JSON Schema to Gemini type names
        type_mapping = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        schema_dict = {}

        # Map the type
        if "type" in json_schema:
            json_type = json_schema["type"]
            if json_type in type_mapping:
                schema_dict["type_"] = type_mapping[json_type]

        # Map description
        if "description" in json_schema:
            schema_dict["description"] = json_schema["description"]

        # Map properties for object types
        if "properties" in json_schema:
            properties = {}
            for prop_name, prop_schema in json_schema["properties"].items():
                properties[prop_name] = self._convert_json_schema_to_gemini(prop_schema)
            schema_dict["properties"] = properties

        # Map items for array types
        if "items" in json_schema:
            schema_dict["items"] = self._convert_json_schema_to_gemini(json_schema["items"])

        # Map required fields
        if "required" in json_schema:
            schema_dict["required"] = json_schema["required"]

        # Map enum values
        if "enum" in json_schema:
            schema_dict["enum"] = json_schema["enum"]

        return schema_dict

    def has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls."""
        if not response.candidates:
            return False

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    return True
        return False

    def parse_tool_calls(self, response: Any) -> List[Any]:
        """Extract tool calls from Gemini response."""
        from funcs.tools import ToolCall

        tool_calls = []
        if not response.candidates:
            return tool_calls

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    fc = part.function_call

                    # Convert protobuf args to regular dict
                    args_dict = {}
                    if hasattr(fc, 'args') and fc.args:
                        args_dict = self._convert_protobuf_to_dict(fc.args)

                    # Convert Gemini's function call to ToolCall
                    tool_calls.append(ToolCall(
                        id=f"call_{hash(fc.name)}_{len(tool_calls)}",  # Generate ID
                        name=fc.name,
                        arguments=args_dict
                    ))

        return tool_calls

    def get_response_content(self, response: Any) -> Optional[str]:
        """Get text content from response."""
        if self.has_tool_calls(response):
            return None

        try:
            return response.text
        except Exception:
            return ""

    def _convert_protobuf_to_dict(self, protobuf_struct) -> Dict:
        """Convert protobuf Struct to regular Python dict."""
        from google.protobuf.json_format import MessageToDict

        try:
            # Convert protobuf Struct to dict
            return MessageToDict(protobuf_struct, preserving_proto_field_name=True)
        except Exception:
            # Fallback: try to access the fields directly
            try:
                result = {}
                for key, value in protobuf_struct.items():
                    result[key] = value
                return result
            except Exception:
                return {}

    def get_assistant_message(self, response: Any) -> Dict:
        """Get assistant message for context."""
        # For Gemini, we need to convert back to OpenAI format
        # This is tricky because Gemini's response structure is different

        if self.has_tool_calls(response):
            # Extract tool calls for context
            tool_calls_data = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        fc = part.function_call

                        # Convert protobuf args to regular dict
                        args_dict = {}
                        if hasattr(fc, 'args') and fc.args:
                            args_dict = self._convert_protobuf_to_dict(fc.args)

                        tool_calls_data.append({
                            "id": f"call_{hash(fc.name)}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": json.dumps(args_dict)
                            }
                        })

            return {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_data
            }
        else:
            return {
                "role": "assistant",
                "content": response.text if hasattr(response, 'text') else ""
            }

    def format_tool_result(self, result: Any) -> Dict:
        """Format tool result for Gemini."""
        # Gemini doesn't use the same tool result format as OpenAI
        # We'll format it as a user message with tool result context
        return {
            "role": "user",
            "content": f"Tool '{result.tool_call_id}' result: {result.content}"
        }


def create_llm_client(
    provider: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> LLMClient:
    """
    Factory function to create LLM client based on provider.

    Args:
        provider: Provider name ("openai" or "gemini")
        api_key: API key (fetched from config if not provided)
        model: Model name (fetched from config if not provided)
        **kwargs: Additional parameters to pass to client

    Returns:
        LLMClient instance

    Raises:
        ValueError: If provider is unsupported
    """
    from funcs.config import config

    provider = provider.lower()

    if provider == "openai":
        return OpenAIClient(
            api_key=api_key or config.OPENAI_API_KEY,
            model=model or config.OPENAI_MODEL,
            **kwargs
        )
    elif provider == "gemini":
        return GeminiClient(
            api_key=api_key or config.GEMINI_API_KEY,
            model=model or config.GEMINI_MODEL,
            **kwargs
        )
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported providers: openai, gemini"
        )

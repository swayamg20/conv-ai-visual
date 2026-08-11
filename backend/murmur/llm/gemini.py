"""Google Gemini provider implementation."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from funcs.tools import ToolCall
from murmur.llm.base import LLMClient

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    """LLM client for Google Gemini API."""

    def __init__(self, api_key: str, model: str, **default_params):
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
        except ImportError as exc:
            raise ImportError(
                "google-generativeai package is required for Gemini support. "
                "Install it with: pip install google-generativeai"
            ) from exc

        self.genai.configure(api_key=api_key)
        self.model_name = model
        self.model = self.genai.GenerativeModel(model)
        self.default_params = default_params
        logger.info(f"Gemini client initialized: model={model}")

    def _convert_messages(self, messages: list[dict]) -> tuple:
        """
        Convert OpenAI-style messages to Gemini format.

        Returns:
            Tuple of (system_prompt, history) where history is list of Gemini message dicts
        """
        system_prompt = ""
        history = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "system":
                system_prompt = content or ""
            elif role == "user":
                history.append({"role": "user", "parts": [content or ""]})
            elif role == "assistant":
                # Tool call responses have content=None — reconstruct as text summary
                if content is None and "tool_calls" in msg:
                    tool_names = [
                        tc.get("function", {}).get("name", "tool")
                        for tc in msg.get("tool_calls", [])
                    ]
                    summary = f"[Called tools: {', '.join(tool_names)}]"
                    history.append({"role": "model", "parts": [summary]})
                elif content:
                    history.append({"role": "model", "parts": [content]})
                # Skip if content is None and no tool_calls (empty message)
            elif role == "tool":
                # Gemini handles tool results differently
                # Append as user message with tool result context
                history.append({"role": "user", "parts": [f"Tool result: {content or ''}"]})

        return system_prompt, history

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        """Non-streaming completion."""
        try:
            system_prompt, history = self._convert_messages(messages)

            # Gemini uses generation_config for parameters
            generation_config = {"temperature": temperature, **self.default_params, **kwargs}
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
            response = await chat.send_message_async(
                last_message, generation_config=generation_config
            )

            return response.text
        except Exception as e:
            logger.exception(f"Gemini completion error: {e}")
            raise

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion."""
        try:
            system_prompt, history = self._convert_messages(messages)

            generation_config = {"temperature": temperature, **self.default_params, **kwargs}
            if max_tokens:
                generation_config["max_output_tokens"] = max_tokens

            if system_prompt and history and history[0]["role"] == "user":
                history[0]["parts"][0] = f"{system_prompt}\n\n{history[0]['parts'][0]}"

            if not history:
                raise ValueError("No messages to process")

            chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])
            last_message = history[-1]["parts"][0] if history else ""

            response = await chat.send_message_async(
                last_message, generation_config=generation_config, stream=True
            )

            async for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logger.exception(f"Gemini stream error: {e}")
            raise

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
            system_prompt, history = self._convert_messages(messages)

            generation_config = {"temperature": temperature, **self.default_params, **kwargs}
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

            # Create model with tools if provided
            if gemini_tools:
                model_with_tools = self.genai.GenerativeModel(self.model_name, tools=gemini_tools)
                chat = model_with_tools.start_chat(history=history[:-1] if len(history) > 1 else [])
            else:
                chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])

            last_message = history[-1]["parts"][0] if history else ""
            try:
                response = await chat.send_message_async(
                    last_message, generation_config=generation_config
                )
            except Exception as send_err:
                # Handle MALFORMED_FUNCTION_CALL — Gemini generated bad tool call JSON
                # Retry without tools so it returns a plain text response
                if "MALFORMED_FUNCTION_CALL" in str(send_err):
                    logger.warning("Gemini MALFORMED_FUNCTION_CALL — retrying without tools")
                    chat_no_tools = self.model.start_chat(
                        history=history[:-1] if len(history) > 1 else []
                    )
                    response = await chat_no_tools.send_message_async(
                        last_message, generation_config=generation_config
                    )
                else:
                    raise

            return response
        except Exception as e:
            logger.exception(f"Gemini completion with tools error: {e}")
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
        try:
            system_prompt, history = self._convert_messages(messages)

            generation_config = {"temperature": temperature, **self.default_params, **kwargs}
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
                model_with_tools = self.genai.GenerativeModel(self.model_name, tools=gemini_tools)
                chat = model_with_tools.start_chat(history=history[:-1] if len(history) > 1 else [])
            else:
                chat = self.model.start_chat(history=history[:-1] if len(history) > 1 else [])

            last_message = history[-1]["parts"][0] if history else ""
            response = await chat.send_message_async(
                last_message, generation_config=generation_config, stream=True
            )

            async for chunk in response:
                yield chunk
        except Exception as e:
            logger.exception(f"Gemini stream with tools error: {e}")
            raise

    async def iter_stream_tool_events(
        self, stream: AsyncGenerator[Any, None]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Normalize Gemini streamed chunks into tool-aware events."""
        pending_tool_calls: dict[str, ToolCall] = {}

        async for chunk in stream:
            usage = getattr(chunk, "usage_metadata", None)
            if usage:
                yield {
                    "type": "usage",
                    "tokens_in": getattr(usage, "prompt_token_count", None),
                    "tokens_out": getattr(usage, "candidates_token_count", None),
                }

            chunk_text = ""
            try:
                chunk_text = getattr(chunk, "text", "") or ""
            except Exception:
                chunk_text = ""
            if chunk_text:
                yield {"type": "text_delta", "text": chunk_text}

            finish_reason = None
            candidates = getattr(chunk, "candidates", None) or []
            for candidate in candidates:
                if getattr(candidate, "finish_reason", None) is not None:
                    finish_reason = candidate.finish_reason
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) if content else None
                if not parts:
                    continue

                for part_idx, part in enumerate(parts):
                    function_call = getattr(part, "function_call", None)
                    if not function_call:
                        continue

                    args_dict = {}
                    if hasattr(function_call, "args") and function_call.args:
                        args_dict = self._convert_protobuf_to_dict(function_call.args)

                    call_id = f"call_{hash(function_call.name)}_{part_idx}"
                    pending_tool_calls[call_id] = ToolCall(
                        id=call_id,
                        name=function_call.name,
                        arguments=args_dict,
                    )
                    yield {
                        "type": "tool_call_delta",
                        "name": function_call.name,
                    }

            if pending_tool_calls and finish_reason is not None:
                yield {
                    "type": "tool_call_done",
                    "tool_calls": list(pending_tool_calls.values()),
                }
                pending_tool_calls = {}

            if finish_reason is not None:
                yield {"type": "done"}

        if pending_tool_calls:
            yield {
                "type": "tool_call_done",
                "tool_calls": list(pending_tool_calls.values()),
            }

    def _convert_tools_to_gemini_format(self, tools: list[dict]) -> list:
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
                gemini_tools.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "parameters": gemini_schema,
                    }
                )

        return gemini_tools

    def _convert_json_schema_to_gemini(self, json_schema: dict) -> dict:
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
                if hasattr(part, "function_call") and part.function_call:
                    return True
        return False

    def parse_tool_calls(self, response: Any) -> list[Any]:
        """Extract tool calls from Gemini response."""
        tool_calls = []
        if not response.candidates:
            return tool_calls

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call

                    # Convert protobuf args to regular dict
                    args_dict = {}
                    if hasattr(fc, "args") and fc.args:
                        args_dict = self._convert_protobuf_to_dict(fc.args)

                    # Convert Gemini's function call to ToolCall
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{hash(fc.name)}_{len(tool_calls)}",  # Generate ID
                            name=fc.name,
                            arguments=args_dict,
                        )
                    )

        return tool_calls

    def get_response_content(self, response: Any) -> str | None:
        """Get text content from response."""
        if self.has_tool_calls(response):
            return None

        try:
            return response.text
        except Exception:
            return ""

    def _convert_protobuf_to_dict(self, protobuf_struct) -> dict:
        """Convert protobuf Struct to regular Python dict with native types."""

        def _to_native(val):
            """Recursively convert protobuf/proto-plus values to native Python."""
            if isinstance(val, dict):
                return {k: _to_native(v) for k, v in val.items()}
            if isinstance(val, (list, tuple)):
                return [_to_native(v) for v in val]
            # Handle proto-plus RepeatedComposite / MapComposite
            type_name = type(val).__name__
            if "RepeatedComposite" in type_name or "Repeated" in type_name:
                return [_to_native(v) for v in val]
            if "MapComposite" in type_name:
                return {k: _to_native(v) for k, v in val.items()}
            if hasattr(val, "items"):
                return {k: _to_native(v) for k, v in val.items()}
            if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
                return [_to_native(v) for v in val]
            return val

        try:
            from google.protobuf.json_format import MessageToDict

            result = MessageToDict(protobuf_struct, preserving_proto_field_name=True)
            return _to_native(result)
        except Exception:
            try:
                result = {}
                for key, value in protobuf_struct.items():
                    result[key] = _to_native(value)
                return result
            except Exception:
                return {}

    def get_assistant_message(self, response: Any) -> dict:
        """Get assistant message for context."""
        # For Gemini, we need to convert back to OpenAI format
        # This is tricky because Gemini's response structure is different

        if self.has_tool_calls(response):
            # Extract tool calls for context
            tool_calls_data = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call

                        # Convert protobuf args to regular dict
                        args_dict = {}
                        if hasattr(fc, "args") and fc.args:
                            args_dict = self._convert_protobuf_to_dict(fc.args)

                        tool_calls_data.append(
                            {
                                "id": f"call_{hash(fc.name)}",
                                "type": "function",
                                "function": {"name": fc.name, "arguments": json.dumps(args_dict)},
                            }
                        )

            return {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
        else:
            return {
                "role": "assistant",
                "content": response.text if hasattr(response, "text") else "",
            }

    def format_tool_result(self, result: Any) -> dict:
        """Format tool result for Gemini."""
        # Gemini doesn't use the same tool result format as OpenAI
        # We'll format it as a user message with tool result context
        return {"role": "user", "content": f"Tool '{result.tool_call_id}' result: {result.content}"}

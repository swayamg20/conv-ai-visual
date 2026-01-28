import json
import logging
from typing import List, Dict, Optional, AsyncGenerator, Any
import google.generativeai as genai
from google.protobuf.json_format import MessageToDict
from .base import LLMClient
from funcs.tools import ToolCall

logger = logging.getLogger("llm-clients")


class GeminiClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        **default_params
    ):
        genai.configure(api_key=api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model)
        self.default_params = default_params
        logger.info(f"Gemini client initialized: model={model}")

    def _convert_messages(self, messages: List[Dict]) -> tuple:
        system_prompt = ""
        history = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if content is None:
                content = ""
            elif not isinstance(content, str):
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
                if content:
                    history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                if content:
                    history.append({"role": "model", "parts": [content]})
            elif role == "tool":
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
            last_message = history[-1]["parts"][0] if history else ""
            history_to_pass = history[:-1] if len(history) > 1 else []
            if gemini_tools:
                msg_lower = last_message.lower() if last_message else ""
                should_force_tools = any(kw in msg_lower for kw in [
                    'draw', 'diagram', 'hld', 'lld', 'architecture', 'design',
                    'show', 'visualize', 'sketch', 'illustrate', 'rate limiter',
                    'system design', 'teach', 'explain'
                ])
                mode = "ANY" if should_force_tools else "AUTO"
                tool_config = {"function_calling_config": {"mode": mode}}

                model_with_tools = genai.GenerativeModel(
                    self.model_name,
                    tools=gemini_tools,
                    tool_config=tool_config
                )
                logger.info(f"Created Gemini model with {len(gemini_tools)} tools, tool_config={mode}")
                chat = model_with_tools.start_chat(history=history_to_pass)
            else:
                chat = self.model.start_chat(history=history_to_pass)
            response = await chat.send_message_async(last_message, generation_config=generation_config)
            if response.candidates:
                candidate = response.candidates[0]
                parts_info = []
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        parts_info.append(f"function_call:{part.function_call.name}")
                    elif hasattr(part, 'text') and part.text:
                        parts_info.append(f"text:{len(part.text)} chars")
                logger.info(f"Gemini response parts: {parts_info}")
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
                model_with_tools = genai.GenerativeModel(
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
        gemini_tools = []
        for tool in tools:
            if "function" in tool:
                func = tool["function"]
                params = func.get("parameters", {})
                gemini_schema = self._convert_json_schema_to_gemini(params)
                gemini_tool = {
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "parameters": gemini_schema
                }
                gemini_tools.append(gemini_tool)
        return gemini_tools

    def _convert_json_schema_to_gemini(self, json_schema: Dict) -> Dict:
        type_mapping = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        schema_dict = {}
        if "type" in json_schema:
            json_type = json_schema["type"]
            if json_type in type_mapping:
                schema_dict["type_"] = type_mapping[json_type]
        if "description" in json_schema:
            schema_dict["description"] = json_schema["description"]
        if "properties" in json_schema:
            properties = {}
            for prop_name, prop_schema in json_schema["properties"].items():
                properties[prop_name] = self._convert_json_schema_to_gemini(prop_schema)
            schema_dict["properties"] = properties
        if "items" in json_schema:
            schema_dict["items"] = self._convert_json_schema_to_gemini(json_schema["items"])
        if "required" in json_schema:
            schema_dict["required"] = json_schema["required"]
        if "enum" in json_schema:
            schema_dict["enum"] = json_schema["enum"]
        return schema_dict

    def has_tool_calls(self, response: Any) -> bool:
        if not response.candidates:
            return False
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    return True
        return False

    def parse_tool_calls(self, response: Any) -> List[Any]:
        tool_calls = []
        if not response.candidates:
            return tool_calls
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    fc = part.function_call

                    args_dict = {}
                    if hasattr(fc, 'args') and fc.args:
                        args_dict = self._convert_protobuf_to_dict(fc.args)

                    tool_calls.append(ToolCall(
                        id=f"call_{hash(fc.name)}_{len(tool_calls)}",
                        name=fc.name,
                        arguments=args_dict
                    ))
        return tool_calls

    def get_response_content(self, response: Any) -> Optional[str]:
        if self.has_tool_calls(response):
            return None

        try:
            return response.text
        except Exception:
            return ""

    def _convert_protobuf_to_dict(self, protobuf_struct) -> Dict:
        try:
            return MessageToDict(protobuf_struct, preserving_proto_field_name=True)
        except Exception:
            try:
                result = {}
                for key, value in protobuf_struct.items():
                    result[key] = value
                return result
            except Exception:
                return {}

    def get_assistant_message(self, response: Any) -> Dict:
        if self.has_tool_calls(response):
            tool_calls_data = []
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        fc = part.function_call

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
        return {
            "role": "user",
            "content": f"Tool '{result.tool_call_id}' result: {result.content}"
        }

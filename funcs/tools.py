"""
Tool calling infrastructure - provider agnostic.
Supports both in-memory and SQLite-backed (via SQLModel) tool registries.
"""

import asyncio
import importlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from funcs.models import ToolModel, ToolRepo

logger = logging.getLogger("tools")


@dataclass
class ToolCall:
    """Represents a tool call from the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a tool."""

    tool_call_id: str
    content: str
    success: bool = True


@dataclass
class Tool:
    """Tool definition."""

    name: str
    description: str
    parameters: dict  # JSON Schema
    func: Callable

    def to_openai_schema(self) -> dict:
        """OpenAI/standard format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict:
        """Anthropic format."""
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}


class ToolRegistry:
    """In-memory registry for tools with execution support."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self, name: str, description: str, parameters: dict, func: Callable
    ) -> "ToolRegistry":
        """
        Register a tool.

        Args:
            name: Function name (snake_case)
            description: What it does (be specific for better LLM routing)
            parameters: JSON Schema for params
            func: Sync or async callable

        Returns:
            self for chaining
        """
        self._tools[name] = Tool(name, description, parameters, func)
        logger.info(f"Registered tool: {name}")
        return self

    def tool(self, name: str, description: str, parameters: dict) -> Callable:
        """Decorator for registering tools."""

        def decorator(func: Callable) -> Callable:
            self.register(name, description, parameters, func)
            return func

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_format(self) -> list[dict]:
        """Get all tools in OpenAI format."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def to_anthropic_format(self) -> list[dict]:
        """Get all tools in Anthropic format."""
        return [t.to_anthropic_schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name."""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        func = tool.func
        if asyncio.iscoroutinefunction(func):
            return await func(**arguments)
        return func(**arguments)

    async def execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Execute a ToolCall and return ToolResult."""
        try:
            result = await self.execute(tool_call.name, tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(result) if not isinstance(result, str) else result,
                success=True,
            )
        except Exception as e:
            logger.error(f"Tool {tool_call.name} failed: {e}")
            return ToolResult(tool_call_id=tool_call.id, content=f"Error: {e}", success=False)


class ModelAdapter(ABC):
    """Abstract adapter for different model providers."""

    @abstractmethod
    def format_tools(self, registry: ToolRegistry) -> Any:
        """Format tools for this provider."""
        pass

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract tool calls from model response."""
        pass

    @abstractmethod
    def format_tool_result(self, result: ToolResult) -> dict:
        """Format tool result for this provider."""
        pass

    @abstractmethod
    def get_response_content(self, response: Any) -> str | None:
        """Get text content from response (None if tool call)."""
        pass

    @abstractmethod
    def has_tool_calls(self, response: Any) -> bool:
        """Check if response contains tool calls."""
        pass


class OpenAIAdapter(ModelAdapter):
    """Adapter for OpenAI API format (also works for Groq, Together, etc)."""

    def format_tools(self, registry: ToolRegistry) -> list[dict]:
        return registry.to_openai_format()

    def parse_tool_calls(self, response) -> list[ToolCall]:
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

    def format_tool_result(self, result: ToolResult) -> dict:
        return {"role": "tool", "tool_call_id": result.tool_call_id, "content": result.content}

    def get_response_content(self, response) -> str | None:
        message = response.choices[0].message
        if message.tool_calls:
            return None
        return message.content.strip() if message.content else ""

    def has_tool_calls(self, response) -> bool:
        return bool(response.choices[0].message.tool_calls)

    def get_assistant_message(self, response) -> dict:
        """Get the raw message to append to context."""
        return response.choices[0].message


class AnthropicAdapter(ModelAdapter):
    """Adapter for Anthropic API format."""

    def format_tools(self, registry: ToolRegistry) -> list[dict]:
        return registry.to_anthropic_format()

    def parse_tool_calls(self, response) -> list[ToolCall]:
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return tool_calls

    def format_tool_result(self, result: ToolResult) -> dict:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                }
            ],
        }

    def get_response_content(self, response) -> str | None:
        for block in response.content:
            if block.type == "text":
                return block.text
            if block.type == "tool_use":
                return None
        return ""

    def has_tool_calls(self, response) -> bool:
        return any(block.type == "tool_use" for block in response.content)

    def get_assistant_message(self, response) -> dict:
        """Get the raw message to append to context."""
        return {"role": "assistant", "content": response.content}


# Default registry instance
default_registry = ToolRegistry()


def tool(name: str, description: str, parameters: dict):
    """Convenience decorator using default registry."""
    return default_registry.tool(name, description, parameters)


class ToolStore:
    """
    SQLite-backed tool storage via SQLModel.
    Load tool definitions from DB, resolve handlers at runtime.
    """

    def __init__(self):
        self._handler_cache: dict[str, Callable] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler_module: str | None = None,
        handler_function: str | None = None,
        enabled: bool = True,
    ) -> ToolModel:
        """
        Register a tool in the database.

        Args:
            name: Tool name (snake_case)
            description: What the tool does
            parameters: JSON Schema for parameters
            handler_module: Python module path, e.g. 'myapp.tools.weather'
            handler_function: Function name in that module, e.g. 'get_weather'
            enabled: Whether tool is active
        """
        return ToolRepo.upsert(
            name=name,
            description=description,
            parameters=parameters,
            handler_module=handler_module,
            handler_function=handler_function,
            enabled=enabled,
        )

    def get(self, name: str) -> ToolModel | None:
        """Get a tool definition by name."""
        return ToolRepo.get_enabled(name)

    def list_all(self, enabled_only: bool = True) -> list[ToolModel]:
        """List all tools."""
        return ToolRepo.list_all(enabled_only)

    def delete(self, name: str) -> bool:
        """Delete a tool."""
        self._handler_cache.pop(name, None)
        return ToolRepo.delete(name)

    def enable(self, name: str, enabled: bool = True) -> bool:
        """Enable or disable a tool."""
        return ToolRepo.set_enabled(name, enabled)

    def _resolve_handler(self, tool: ToolModel) -> Callable | None:
        """Dynamically import and resolve the handler function."""
        name = tool.name

        # Check cache first
        if name in self._handler_cache:
            return self._handler_cache[name]

        module_path = tool.handler_module
        func_name = tool.handler_function

        if not module_path or not func_name:
            logger.warning(f"Tool {name} has no handler configured")
            return None

        try:
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            self._handler_cache[name] = func
            return func
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to resolve handler for {name}: {e}")
            return None

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name."""
        tool = self.get(name)
        if not tool:
            raise ValueError(f"Unknown or disabled tool: {name}")

        handler = self._resolve_handler(tool)
        if not handler:
            raise ValueError(f"No handler for tool: {name}")

        if asyncio.iscoroutinefunction(handler):
            return await handler(**arguments)
        return handler(**arguments)

    async def execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Execute a ToolCall and return ToolResult."""
        try:
            result = await self.execute(tool_call.name, tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(result) if not isinstance(result, str) else result,
                success=True,
            )
        except Exception as e:
            logger.error(f"Tool {tool_call.name} failed: {e}")
            return ToolResult(tool_call_id=tool_call.id, content=f"Error: {e}", success=False)

    def to_registry(self, with_handlers: bool = True) -> ToolRegistry:
        """
        Convert DB tools to a ToolRegistry.
        Useful for loading tools at startup.
        """
        registry = ToolRegistry()
        for tool in self.list_all():
            handler = self._resolve_handler(tool) if with_handlers else lambda **_: "No handler"
            if handler:
                registry.register(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    func=handler,
                )
        return registry

    def to_openai_format(self) -> list[dict]:
        """Get all enabled tools in OpenAI format."""
        return ToolRepo.to_openai_format()

    def to_anthropic_format(self) -> list[dict]:
        """Get all enabled tools in Anthropic format."""
        return ToolRepo.to_anthropic_format()


# Default store instance
default_store = ToolStore()

"""Database-backed module and restricted-inline tool execution."""

import asyncio
import base64
import datetime
import hashlib
import importlib
import json
import logging
import math
import re
import traceback
import urllib.parse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence, safer_getattr

from murmur.persistence.models import ToolModel
from murmur.persistence.repositories.tools import ToolRepo
from murmur.tools.contracts import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SandboxConfig:
    """Limits and capabilities for trusted inline-tool execution."""

    timeout_seconds: float = 30.0
    max_workers: int = 4
    allow_network: bool = True

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_workers <= 0:
            raise ValueError("max_workers must be positive")


class ToolExecutor:
    """Execute trusted database tools with restricted globals and bounded waits.

    Flow:
    1. LLM returns tool_call (name + args)
    2. Executor fetches tool definition from DB
    3. Resolves handler function
    4. Executes through RestrictedPython or a module handler
    5. Returns result
    """

    def __init__(
        self,
        sandbox_config: SandboxConfig | None = None,
        handler_cache: dict[str, Callable] | None = None,
    ) -> None:
        self.config = sandbox_config or SandboxConfig()
        self._handler_cache = handler_cache if handler_cache is not None else {}
        self._executor = ThreadPoolExecutor(
            max_workers=self.config.max_workers,
            thread_name_prefix="murmur-tool",
        )

    def get_tool(self, name: str) -> ToolModel | None:
        """Fetch tool from database."""
        tool = ToolRepo.get_enabled(name)
        if not tool:
            logger.warning("Tool not found or disabled: %s", name)
        return tool

    def get_all_tools_schema(self) -> list[dict]:
        """Get all enabled tools in OpenAI format for LLM."""
        return ToolRepo.to_openai_format()

    def resolve_handler(self, tool: ToolModel) -> Callable | None:
        """
        Resolve the handler function for a tool.
        Priority: 1) Cached handler, 2) Inline code from DB, 3) Module import
        """
        name = tool.name

        # Check cache
        if name in self._handler_cache:
            return self._handler_cache[name]

        # Option 1: Inline code stored in DB
        if tool.code:
            handler = self._compile_inline_code(tool)
            if handler:
                self._handler_cache[name] = handler
                return handler

        # Option 2: Module-based handler
        module_path = tool.handler_module
        func_name = tool.handler_function

        if not module_path or not func_name:
            logger.error("Tool %s has no code or handler_module/handler_function", name)
            return None

        try:
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            self._handler_cache[name] = func
            logger.debug("Resolved handler: %s.%s", module_path, func_name)
            return func
        except ImportError as e:
            logger.error("Failed to import module %s: %s", module_path, e)
            return None
        except AttributeError as e:
            logger.error("Function %s not found in %s: %s", func_name, module_path, e)
            return None

    def _compile_inline_code(self, tool: ToolModel) -> Callable | None:
        """
        Compile inline Python code from DB into a callable.
        Uses RestrictedPython for sandboxing.
        """
        name = tool.name
        code = tool.code

        if not code:
            return None

        try:
            # Compile with RestrictedPython
            compile_result = compile_restricted(code, filename=f"<tool:{name}>", mode="exec")

            # Handle both old and new RestrictedPython API
            if hasattr(compile_result, "errors") and compile_result.errors:
                logger.error("Compilation errors for %s: %s", name, compile_result.errors)
                return None

            # Get the actual code object
            byte_code = compile_result.code if hasattr(compile_result, "code") else compile_result

            if byte_code is None:
                logger.error("Failed to compile code for %s", name)
                return None

            # Build restricted globals
            restricted_globals = self._build_restricted_globals()

            # Execute to define functions
            exec(byte_code, restricted_globals)

            # The code should define a function with the tool name or 'handler'
            handler = restricted_globals.get(name) or restricted_globals.get("handler")

            if not handler or not callable(handler):
                logger.error(
                    "Tool %s code must define a function named '%s' or 'handler'",
                    name,
                    name,
                )
                return None

            logger.info("Compiled inline handler for tool: %s", name)
            return handler

        except Exception as e:
            logger.error("Failed to compile inline code for %s: %s", name, e)
            return None

    def _build_restricted_globals(self) -> dict[str, Any]:
        """Build restricted globals for sandboxed execution."""
        restricted_globals = {
            "__builtins__": safe_builtins,
            "_getiter_": default_guarded_getiter,
            "_getitem_": default_guarded_getitem,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_getattr_": safer_getattr,
            # Safe modules
            "json": json,
            "datetime": datetime,
            "re": re,
            "math": math,
            "base64": base64,
            "hashlib": hashlib,
            "urllib_parse": urllib.parse,
            # Helpers
            "print": lambda *args: None,  # No-op print
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "sorted": sorted,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
            "round": round,
        }

        if self.config.allow_network:
            restricted_globals["httpx"] = httpx

        return restricted_globals

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Execute a tool by name with given arguments.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            ToolResult with success/failure and content
        """
        # 1. Fetch tool from DB
        tool = self.get_tool(name)
        if not tool:
            return ToolResult(
                tool_call_id="",
                content=f"Error: Tool '{name}' not found or disabled",
                success=False,
            )

        # 2. Resolve handler
        handler = self.resolve_handler(tool)
        if not handler:
            return ToolResult(
                tool_call_id="",
                content=f"Error: No handler configured for tool '{name}'",
                success=False,
            )

        # 3. Execute in sandbox
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(
                    handler(**arguments), timeout=self.config.timeout_seconds
                )
            else:
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, partial(handler, **arguments)),
                    timeout=self.config.timeout_seconds,
                )

            return ToolResult(
                tool_call_id="",
                content=str(result) if not isinstance(result, str) else result,
                success=True,
            )

        except asyncio.TimeoutError:
            logger.error("Tool %s timed out after %.2fs", name, self.config.timeout_seconds)
            return ToolResult(
                tool_call_id="", content="Error: Tool execution timed out", success=False
            )
        except Exception as exc:
            logger.error("Tool %s failed: %s\n%s", name, exc, traceback.format_exc())
            return ToolResult(tool_call_id="", content=f"Error: {exc!s}", success=False)

    async def execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        """
        Execute a ToolCall object (from LLM response).

        Args:
            tool_call: ToolCall with id, name, arguments

        Returns:
            ToolResult with tool_call_id set
        """
        result = await self.execute(tool_call.name, tool_call.arguments)
        result.tool_call_id = tool_call.id
        return result

    async def execute_batch(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """
        Execute multiple tool calls in parallel.

        Args:
            tool_calls: List of ToolCall objects

        Returns:
            List of ToolResult objects (same order)
        """
        tasks = [self.execute_tool_call(tc) for tc in tool_calls]
        return await asyncio.gather(*tasks)

    def clear_cache(self) -> None:
        """Clear handler cache (useful for hot-reloading)."""
        self._handler_cache.clear()
        logger.info("Handler cache cleared")

    def shutdown(self) -> None:
        """Shutdown executor thread pool."""
        self._executor.shutdown(wait=False, cancel_futures=True)


# Default executor instance
default_executor = ToolExecutor()

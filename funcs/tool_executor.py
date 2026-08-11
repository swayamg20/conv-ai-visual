"""
Tool executor with sandbox isolation.
Fetches tools from DB and executes them safely.
Supports both module-based handlers and inline code stored in DB.
"""

import asyncio
import importlib
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from murmur.persistence.models import ToolModel
from murmur.persistence.repositories.tools import ToolRepo
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence, safer_getattr

from funcs.tools import ToolCall, ToolResult

logger = logging.getLogger("tool-executor")


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""

    timeout_seconds: float = 30.0
    max_memory_mb: Optional[int] = None  # Not enforced yet, placeholder
    allowed_modules: Optional[List[str]] = None  # Whitelist for inline code
    allow_network: bool = True  # Allow network calls in inline code

    def __post_init__(self):
        # Default allowed modules for inline code execution
        if self.allowed_modules is None:
            self.allowed_modules = [
                "json",
                "datetime",
                "re",
                "math",
                "urllib.parse",
                "base64",
                "hashlib",
                "hmac",
                "time",
            ]
            if self.allow_network:
                self.allowed_modules.extend(["httpx", "aiohttp"])


class ToolExecutor:
    """
    Executes tools fetched from DB in a sandboxed environment.

    Flow:
    1. LLM returns tool_call (name + args)
    2. Executor fetches tool definition from DB
    3. Resolves handler function
    4. Executes in sandbox (timeout, isolation)
    5. Returns result
    """

    def __init__(
        self,
        sandbox_config: Optional[SandboxConfig] = None,
        handler_cache: Optional[Dict[str, Callable]] = None,
    ):
        self.config = sandbox_config or SandboxConfig()
        self._handler_cache: Dict[str, Callable] = handler_cache or {}
        self._executor = ThreadPoolExecutor(max_workers=4)

    def get_tool(self, name: str) -> Optional[ToolModel]:
        """Fetch tool from database."""
        tool = ToolRepo.get_enabled(name)
        if not tool:
            logger.warning(f"Tool not found or disabled: {name}")
        return tool

    def get_all_tools_schema(self) -> List[Dict]:
        """Get all enabled tools in OpenAI format for LLM."""
        return ToolRepo.to_openai_format()

    def resolve_handler(self, tool: ToolModel) -> Optional[Callable]:
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
            logger.error(f"Tool {name} has no code or handler_module/handler_function")
            return None

        try:
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            self._handler_cache[name] = func
            logger.debug(f"Resolved handler: {module_path}.{func_name}")
            return func
        except ImportError as e:
            logger.error(f"Failed to import module {module_path}: {e}")
            return None
        except AttributeError as e:
            logger.error(f"Function {func_name} not found in {module_path}: {e}")
            return None

    def _compile_inline_code(self, tool: ToolModel) -> Optional[Callable]:
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
                logger.error(f"Compilation errors for {name}: {compile_result.errors}")
                return None

            # Get the actual code object
            byte_code = compile_result.code if hasattr(compile_result, "code") else compile_result

            if byte_code is None:
                logger.error(f"Failed to compile code for {name}")
                return None

            # Build restricted globals
            restricted_globals = self._build_restricted_globals()

            # Execute to define functions
            exec(byte_code, restricted_globals)

            # The code should define a function with the tool name or 'handler'
            handler = restricted_globals.get(name) or restricted_globals.get("handler")

            if not handler or not callable(handler):
                logger.error(f"Tool {name} code must define a function named '{name}' or 'handler'")
                return None

            logger.info(f"Compiled inline handler for tool: {name}")
            return handler

        except Exception as e:
            logger.error(f"Failed to compile inline code for {name}: {e}")
            return None

    def _build_restricted_globals(self) -> Dict[str, Any]:
        """Build restricted globals for sandboxed execution."""
        import base64
        import datetime
        import hashlib
        import json
        import math
        import re
        import urllib.parse

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

        # Add httpx for network calls if allowed
        if self.config.allow_network:
            try:
                import httpx

                restricted_globals["httpx"] = httpx
            except ImportError:
                pass

        return restricted_globals

    def _run_sync_in_thread(self, func: Callable, kwargs: Dict) -> Any:
        """Run sync function in thread pool with timeout."""
        future = self._executor.submit(func, **kwargs)
        try:
            return future.result(timeout=self.config.timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"Tool execution timed out after {self.config.timeout_seconds}s"
            ) from exc

    async def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
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
                # Async handler - run with timeout
                result = await asyncio.wait_for(
                    handler(**arguments), timeout=self.config.timeout_seconds
                )
            else:
                # Sync handler - run in thread pool with timeout
                result = await asyncio.get_event_loop().run_in_executor(
                    self._executor, lambda: handler(**arguments)
                )

            return ToolResult(
                tool_call_id="",
                content=str(result) if not isinstance(result, str) else result,
                success=True,
            )

        except asyncio.TimeoutError:
            logger.error(f"Tool {name} timed out after {self.config.timeout_seconds}s")
            return ToolResult(
                tool_call_id="", content="Error: Tool execution timed out", success=False
            )
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}\n{traceback.format_exc()}")
            return ToolResult(tool_call_id="", content=f"Error: {e!s}", success=False)

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

    async def execute_batch(self, tool_calls: List[ToolCall]) -> List[ToolResult]:
        """
        Execute multiple tool calls in parallel.

        Args:
            tool_calls: List of ToolCall objects

        Returns:
            List of ToolResult objects (same order)
        """
        tasks = [self.execute_tool_call(tc) for tc in tool_calls]
        return await asyncio.gather(*tasks)

    def clear_cache(self):
        """Clear handler cache (useful for hot-reloading)."""
        self._handler_cache.clear()
        logger.info("Handler cache cleared")

    def shutdown(self):
        """Shutdown executor thread pool."""
        self._executor.shutdown(wait=False)


# Default executor instance
default_executor = ToolExecutor()

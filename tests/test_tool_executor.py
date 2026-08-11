import time

import pytest
from murmur.persistence.repositories.tools import ToolRepo
from murmur.tools.executor import SandboxConfig, ToolExecutor


@pytest.mark.asyncio
async def test_inline_tool_executes_in_restricted_globals() -> None:
    ToolRepo.upsert(
        name="double",
        description="Double a number.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
        },
        code="""
def double(value):
    return value * 2
""",
    )
    executor = ToolExecutor(SandboxConfig(allow_network=False))

    try:
        result = await executor.execute("double", {"value": 21})
    finally:
        executor.shutdown()

    assert result.success is True
    assert result.content == "42"


@pytest.mark.asyncio
async def test_sync_tool_wait_is_bounded_by_timeout() -> None:
    def slow_tool() -> str:
        time.sleep(0.1)
        return "too late"

    ToolRepo.upsert(
        name="slow_tool",
        description="A deliberately slow test tool.",
        parameters={"type": "object", "properties": {}},
        handler_module="unused",
        handler_function="slow_tool",
    )
    executor = ToolExecutor(
        SandboxConfig(timeout_seconds=0.01),
        handler_cache={"slow_tool": slow_tool},
    )

    try:
        result = await executor.execute("slow_tool", {})
    finally:
        executor.shutdown()

    assert result.success is False
    assert result.content == "Error: Tool execution timed out"


def test_sandbox_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        SandboxConfig(timeout_seconds=0)

    with pytest.raises(ValueError, match="max_workers"):
        SandboxConfig(max_workers=0)

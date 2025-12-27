"""
Test the tool calling system.

To use:
1. Register your tools in DB via ToolRepo.upsert() or default_store.register()
2. Create handler functions in your own module
3. Run tests
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcs import (
    LLMPipeline, 
    ToolExecutor, 
    SandboxConfig, 
    ToolRepo,
    default_executor,
    default_store,
)
from funcs.tools import ToolCall


async def test_executor():
    """Test ToolExecutor directly."""
    print("=" * 50)
    print("Testing ToolExecutor")
    print("=" * 50)
    
    executor = ToolExecutor()
    
    # List available tools
    tools = ToolRepo.list_all()
    print(f"\nAvailable tools: {len(tools)}")
    for t in tools:
        print(f"  - {t.name}: {t.description[:50]}...")
    
    if not tools:
        print("\nNo tools registered. Register tools first:")
        print("  from funcs import ToolRepo")
        print("  ToolRepo.upsert(name='...', description='...', parameters={...}, handler_module='...', handler_function='...')")
        return
    
    # Try executing first tool
    first_tool = tools[0]
    print(f"\nTrying to execute: {first_tool.name}")
    result = await executor.execute(first_tool.name, {})
    print(f"Result: {result.content}")
    print(f"Success: {result.success}")


async def test_pipeline():
    """Test with LLMPipeline."""
    print("\n" + "=" * 50)
    print("Testing LLMPipeline with tools")
    print("=" * 50)
    
    pipeline = LLMPipeline()
    pipeline.load_tools_from_db()
    
    schemas = pipeline.get_tools_schema()
    print(f"\nLoaded {len(schemas)} tool schemas")
    
    if not schemas:
        print("No tools available. Skipping pipeline test.")
        return
    
    print("\nTesting chat_with_tools...")
    # This will make an actual LLM call
    # response = await pipeline.chat_with_tools("Hello, what can you help me with?")
    # print(f"Response: {response}")
    print("(Uncomment the actual LLM call in test to run)")


def show_tool_registration_example():
    """Show how to register a tool."""
    print("\n" + "=" * 50)
    print("How to register a tool")
    print("=" * 50)
    
    print("""
# 1. Create a handler module (e.g., myapp/tools/weather.py):

async def get_weather(location: str, unit: str = "celsius") -> str:
    # Your implementation here
    return f"Weather in {location}: 24°C"

# 2. Register the tool in DB:

from funcs import ToolRepo

ToolRepo.upsert(
    name="get_weather",
    description="Get current weather for a location",
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        },
        "required": ["location"]
    },
    handler_module="myapp.tools.weather",
    handler_function="get_weather"
)

# 3. Use with pipeline:

pipeline = LLMPipeline()
pipeline.load_tools_from_db()
response = await pipeline.chat_with_tools("What's the weather in Delhi?")
""")


if __name__ == "__main__":
    show_tool_registration_example()
    asyncio.run(test_executor())
    asyncio.run(test_pipeline())


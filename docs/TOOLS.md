# Tool Calling System

## Overview

The tool calling system allows the LLM to invoke external functions. Tools are stored in SQLite and executed in a sandboxed environment.

**Two ways to define handlers:**
1. **Inline code** - Store Python code directly in DB (easy, remote-friendly)
2. **Module-based** - Reference a Python module/function (for complex logic)

## Architecture

```
User Message → LLM (with tool schemas) → Tool Call Decision
                                              ↓
                                      ToolExecutor
                                              ↓
                                    Fetch from DB → Resolve Handler → Execute in Sandbox
                                              ↓
                                      Return Result to LLM
                                              ↓
                                      Final Response
```

## Method 1: Inline Code (Recommended for Simple Tools)

Store the function code directly in the `code` column. The code runs in a restricted sandbox.

```python
from funcs import ToolRepo

ToolRepo.upsert(
    name="get_weather",
    description="Get current weather for a location",
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name"}
        },
        "required": ["location"]
    },
    code='''
def get_weather(location):
    # httpx is available for HTTP calls
    response = httpx.get(f"https://api.weather.com/{location}")
    data = response.json()
    return f"Weather in {location}: {data['temp']}°C"
'''
)
```

**Available in sandbox:**
- `json`, `datetime`, `re`, `math`, `base64`, `hashlib`, `urllib_parse`
- `httpx` (for HTTP requests)
- Basic builtins: `str`, `int`, `list`, `dict`, `len`, `range`, etc.

**Not available (security):**
- `os`, `subprocess`, `open`, `eval`, `exec`, `__import__`
- File system access
- Arbitrary imports

### SQL Example (direct DB insert):

```sql
INSERT INTO tools (name, description, parameters, code, enabled, created_at, updated_at)
VALUES (
    'get_weather',
    'Get current weather for a location',
    '{"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}',
    'def get_weather(location):
    return f"Weather in {location}: 24°C, Sunny"',
    1,
    datetime('now'),
    datetime('now')
);
```

## Method 2: Module-Based (For Complex Tools)

### 1. Create a handler module

```python
# myapp/tools/weather.py

async def get_weather(location: str, unit: str = "celsius") -> str:
    """Fetch weather from API."""
    # Your implementation
    response = await weather_api.get(location)
    return f"Weather in {location}: {response.temp}°{unit[0].upper()}"

def calculate(expression: str) -> str:
    """Evaluate math expression."""
    return str(eval(expression))  # Use proper parser in production
```

### 2. Register in database

```python
from funcs import ToolRepo

# Using ToolRepo directly
ToolRepo.upsert(
    name="get_weather",
    description="Get current weather for a location. Use when user asks about weather.",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name, e.g. 'Delhi', 'New York'"
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "Temperature unit"
            }
        },
        "required": ["location"]
    },
    handler_module="myapp.tools.weather",
    handler_function="get_weather",
    enabled=True
)

# Or using ToolStore
from funcs import default_store

default_store.register(
    name="calculate",
    description="Evaluate a mathematical expression",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string"}
        },
        "required": ["expression"]
    },
    handler_module="myapp.tools.weather",
    handler_function="calculate"
)
```

### 3. Use with LLMPipeline

```python
from funcs import LLMPipeline

pipeline = LLMPipeline()
pipeline.load_tools_from_db()

response = await pipeline.chat_with_tools("What's the weather in Mumbai?")
# LLM will call get_weather tool, executor fetches from DB, runs handler, returns result
```

## Tool Schema (JSON Schema)

Tools use JSON Schema to define parameters:

```python
{
    "type": "object",
    "properties": {
        "param_name": {
            "type": "string",  # string, number, boolean, array, object
            "description": "What this parameter is for",
            "enum": ["option1", "option2"]  # Optional: restrict values
        }
    },
    "required": ["param_name"]  # Required parameters
}
```

## Sandbox Configuration

```python
from funcs import ToolExecutor, SandboxConfig

executor = ToolExecutor(
    sandbox_config=SandboxConfig(
        timeout_seconds=30.0,  # Kill if exceeds
        blocked_modules=["subprocess", "os.system"]  # Security blacklist
    )
)

pipeline = LLMPipeline()
pipeline.set_executor(executor)
```

## Managing Tools

```python
from funcs import ToolRepo

# List all tools
tools = ToolRepo.list_all()

# Get specific tool
tool = ToolRepo.get("get_weather")

# Disable a tool (won't be sent to LLM)
ToolRepo.set_enabled("get_weather", False)

# Delete a tool
ToolRepo.delete("old_tool")

# Get tools in OpenAI format
schemas = ToolRepo.to_openai_format()
```

## Direct Executor Usage

```python
from funcs import default_executor
from funcs.tools import ToolCall

# Execute by name
result = await default_executor.execute("get_weather", {"location": "Delhi"})
print(result.content)  # "Weather in Delhi: 24°C"
print(result.success)  # True

# Execute ToolCall object (from LLM response)
tool_call = ToolCall(id="call_123", name="get_weather", arguments={"location": "NYC"})
result = await default_executor.execute_tool_call(tool_call)
```

## Database Schema

```sql
CREATE TABLE tools (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    parameters TEXT NOT NULL,      -- JSON Schema
    handler_module TEXT,           -- e.g. 'myapp.tools.weather' (optional)
    handler_function TEXT,         -- e.g. 'get_weather' (optional)
    code TEXT,                     -- Inline Python code (optional, preferred)
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

**Priority:** If `code` is set, it's used. Otherwise falls back to `handler_module`/`handler_function`.

## Best Practices

1. **Description matters**: LLM uses description to decide when to call tools. Be specific.

2. **Handle errors gracefully**: Return error messages, don't raise exceptions.

3. **Async preferred**: Use async handlers for I/O operations.

4. **Keep handlers focused**: One tool = one action.

5. **Validate in handler**: Don't trust LLM arguments blindly.


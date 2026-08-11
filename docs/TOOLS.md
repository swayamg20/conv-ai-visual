# LLM tools

Murmur stores enabled tool definitions in SQLite and exposes them to supported model providers through a provider-neutral registry.

## Tool kinds

### Module handlers

Use a module handler for application-owned behavior. The database stores a stable import path and function name:

```python
from murmur.persistence.repositories.tools import ToolRepo

ToolRepo.upsert(
    name="lookup_course",
    description="Look up one course by code.",
    parameters={
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
    handler_module="murmur.tools.course",
    handler_function="lookup_course",
    enabled=True,
)
```

The handler may be synchronous or asynchronous. Keep production handlers under the `murmur` package so installed wheels and database paths agree.

### Inline handlers

For trusted local experiments, a tool row may contain RestrictedPython source defining a function with the same name:

```python
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
    enabled=True,
)
```

Inline code is intended for trusted operators. RestrictedPython and a timeout reduce accidental capability, but they are not operating-system process isolation. Do not let untrusted users write tool code. Use a separate sandbox service before exposing arbitrary code creation.

## Built-ins

Application startup upserts two database-backed tools:

- `web_search` resolves `murmur.tools.search.web_search` and uses Tavily when `TAVILY_API_KEY` is set.
- `canvas_update` resolves `murmur.canvas.state.canvas_update` and applies validated operations to session canvas state.

Visual teaching through SDL uses the typed `teach_with_visuals` schema alongside the database-backed tool registry. The browser compiles its semantic steps into canvas operations.

Because handler paths are persisted, source moves must update the registration definition. Startup upserts repair existing local rows; `tests/test_architecture_boundaries.py` proves built-in paths are canonical and importable.

## Execution flow

```text
provider tool call
  -> ToolCall
  -> enabled database definition
  -> inline compile or module resolution
  -> bounded execution
  -> ToolResult
  -> provider-specific result message
```

The LLM tool runtime executes read-only calls in parallel when configured, but preserves order for mutating calls. It limits tool rounds and converts failures into explicit result messages rather than abandoning the whole conversation.

## Add a module tool

1. Put the handler in a focused `backend/murmur/...` module.
2. Define a strict JSON Schema with required fields and narrow enums.
3. Upsert its database definition during application startup or an explicit maintenance command.
4. Add a provider-free test for resolution, success, invalid arguments, and failure behavior.
5. If it mutates state, ensure the runtime classifies it as ordered rather than parallel work.

The optional `scripts/add_sample_tool.py` command initializes the configured database and registers a clock plus basic calculator example:

```bash
uv run python scripts/add_sample_tool.py
```

## Relevant modules

- `murmur.tools.contracts` — schemas, registry, provider adapters, and database store
- `murmur.tools.executor` — inline compilation, handler invocation, and timeouts
- `murmur.llm.tool_runtime` — multi-round orchestration and ordering policy
- `murmur.persistence.repositories.tools` — tool definitions and provider schemas
- `murmur.tools.search` — built-in web search
- `murmur.canvas.state` — built-in canvas update handler

#!/usr/bin/env python3
"""Register two optional sample tools in the configured Murmur database."""

from murmur.persistence import init_db
from murmur.persistence.repositories.tools import ToolRepo


def register_sample_tools() -> None:
    """Create or update the sample tools without duplicating database rows."""
    init_db()
    ToolRepo.upsert(
        name="get_current_time",
        description="Get the current date and time. Use when user asks what time or date it is.",
        parameters={"type": "object", "properties": {}, "required": []},
        code="""
def get_current_time():
    now = datetime.datetime.now()
    return f"Current time is {now.strftime('%H:%M:%S')} on {now.strftime('%B %d, %Y')}"
""",
    )
    print("Added: get_current_time")

    ToolRepo.upsert(
        name="calculate",
        description="Apply one basic arithmetic operation to two numbers.",
        parameters={
            "type": "object",
            "properties": {
                "left": {"type": "number"},
                "right": {"type": "number"},
                "operation": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide"],
                },
            },
            "required": ["left", "right", "operation"],
        },
        code="""
def calculate(left, right, operation):
    if operation == "add":
        return str(left + right)
    if operation == "subtract":
        return str(left - right)
    if operation == "multiply":
        return str(left * right)
    if operation == "divide":
        return "Error: division by zero" if right == 0 else str(left / right)
    return "Error: unsupported operation"
""",
    )
    print("Added: calculate")

    print("\nAll tools in database:")
    for tool in ToolRepo.list_all():
        print(f"  - {tool.name}: {tool.description[:50]}...")
        print(f"    Has code: {bool(tool.code)}")
        print(f"    Enabled: {tool.enabled}")


if __name__ == "__main__":
    register_sample_tools()

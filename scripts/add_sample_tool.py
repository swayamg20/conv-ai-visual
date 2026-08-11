#!/usr/bin/env python3
"""
Add a sample tool to the database.
Run this script to test tool calling.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.persistence.repositories.tools import ToolRepo

# Add get_current_time tool
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

# Add simple calculator
ToolRepo.upsert(
    name="calculate",
    description="Evaluate a mathematical expression. Use for any math calculations.",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate, e.g. '2 + 2', '15 * 7'",
            }
        },
        "required": ["expression"],
    },
    code="""
def calculate(expression):
    # Safe eval for basic math
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expression):
        return "Error: Invalid characters"
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"Error: {e}"
""",
)
print("Added: calculate")

# List all tools
print("\nAll tools in database:")
for tool in ToolRepo.list_all():
    print(f"  - {tool.name}: {tool.description[:50]}...")
    print(f"    Has code: {bool(tool.code)}")
    print(f"    Enabled: {tool.enabled}")

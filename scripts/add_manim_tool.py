#!/usr/bin/env python3
"""
Register the manim_animate tool in the database.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcs import ToolRepo
from funcs.manim_bridge import MANIM_TOOL_SCHEMA, MANIM_TOOL_DEFINITION

# Register in DB
ToolRepo.upsert(
    name=MANIM_TOOL_DEFINITION["name"],
    description=MANIM_TOOL_DEFINITION["description"],
    parameters=MANIM_TOOL_DEFINITION["parameters"],
    handler_module=MANIM_TOOL_DEFINITION["handler_module"],
    handler_function=MANIM_TOOL_DEFINITION["handler_function"],
    enabled=True
)

print("Registered: manim_animate")

# Show all tools
print("\nAll tools in database:")
for tool in ToolRepo.list_all():
    status = "+" if tool.enabled else "-"
    print(f"  [{status}] {tool.name}")

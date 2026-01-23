#!/usr/bin/env python3
"""
Register the canvas_update tool in the database.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcs import ToolRepo

# Canvas tool schema - simplified for Gemini compatibility
CANVAS_PARAMETERS = {
    "type": "object",
    "properties": {
        "operations_json": {
            "type": "string",
            "description": "JSON array of drawing operations as a string"
        }
    },
    "required": ["operations_json"]
}

CANVAS_DESCRIPTION = """Draw on the shared Excalidraw canvas to illustrate concepts visually.
Pass a JSON string of operations. Each operation has: action (rect/circle/text/arrow/line/clear), x, y, width, height, text, color, label.

Example: '[{"action":"rect","x":100,"y":100,"width":200,"height":100,"color":"#3b82f6","label":"box1"},{"action":"text","x":150,"y":140,"text":"Hello"}]'

Actions: rect, circle, ellipse, line, arrow, text, clear, delete, highlight
For arrow/line, use points: '[{"action":"arrow","points":"[[100,100],[300,100]]"}]'
Canvas is 800x600. Colors: #3b82f6 (blue), #ef4444 (red), #10b981 (green), #f59e0b (orange)."""

# Register in DB
ToolRepo.upsert(
    name="canvas_update",
    description=CANVAS_DESCRIPTION,
    parameters=CANVAS_PARAMETERS,
    handler_module="funcs.canvas",
    handler_function="canvas_update",
    enabled=True
)

print("✓ Registered: canvas_update")

# Show all tools
print("\nAll tools in database:")
for tool in ToolRepo.list_all():
    status = "✓" if tool.enabled else "✗"
    print(f"  [{status}] {tool.name}")


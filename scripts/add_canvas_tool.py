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

CANVAS_DESCRIPTION = """Draw diagrams on the shared canvas.

Operations JSON format - each object has:
- action: "rect", "circle", "ellipse", "arrow", "text"
- x, y: position (canvas is 800x600)
- width, height: size (use 150+ width for boxes with text)
- label: text to show INSIDE the shape (auto-centered)
- color: hex color

Example: '[{"action":"rect","x":350,"y":50,"width":150,"height":50,"color":"#6b7280","label":"Clients"},{"action":"rect","x":350,"y":150,"width":150,"height":50,"color":"#3b82f6","label":"LB"},{"action":"arrow","points":"[[425,100],[425,150]]","color":"#000000"}]'

For arrows: use "points" as JSON string like "[[x1,y1],[x2,y2]]"
Colors: #3b82f6 blue, #10b981 green, #f59e0b orange, #ef4444 red, #6b7280 gray"""

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


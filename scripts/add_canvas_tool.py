#!/usr/bin/env python3
"""
Register the canvas_update tool in the database.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.persistence.repositories.tools import ToolRepo

# Canvas tool schema
CANVAS_PARAMETERS = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "description": "List of drawing operations to apply in order",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "rect",
                            "circle",
                            "ellipse",
                            "line",
                            "arrow",
                            "text",
                            "path",
                            "clear",
                            "delete",
                            "highlight",
                        ],
                        "description": "Type of drawing operation",
                    },
                    "x": {
                        "type": "number",
                        "description": "X position (left edge for shapes, anchor for text)",
                    },
                    "y": {"type": "number", "description": "Y position (top edge for shapes)"},
                    "width": {
                        "type": "number",
                        "description": "Width of shape (or diameter for circle)",
                    },
                    "height": {"type": "number", "description": "Height of shape"},
                    "text": {"type": "string", "description": "Text content (for text action)"},
                    "color": {"type": "string", "description": "Stroke/text color (hex)"},
                    "fill": {
                        "type": "string",
                        "description": "Fill color (hex, empty for no fill)",
                    },
                    "stroke_width": {"type": "number", "description": "Line thickness"},
                    "font_size": {"type": "number", "description": "Font size for text"},
                    "points": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                        "description": "Array of [x,y] points for line/arrow/path",
                    },
                    "label": {
                        "type": "string",
                        "description": "Semantic label for referencing this element later",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "ID of element to delete/update/highlight",
                    },
                },
                "required": ["action"],
            },
        }
    },
    "required": ["operations"],
}

CANVAS_DESCRIPTION = """Draw on the shared canvas to illustrate concepts visually while explaining.
Use this to create diagrams, flowcharts, highlight relationships, or sketch ideas.
The canvas is visible to the user in real-time.

IMPORTANT: Send all related drawings in a single call to minimize latency.
Use semantic labels to reference elements later.

Coordinate system: (0,0) is top-left. Canvas is 800x600 by default.
Colors: Use hex codes like "#3b82f6" (blue), "#ef4444" (red), "#10b981" (green), "#f59e0b" (orange)."""

# Register in DB
ToolRepo.upsert(
    name="canvas_update",
    description=CANVAS_DESCRIPTION,
    parameters=CANVAS_PARAMETERS,
    handler_module="murmur.canvas.state",
    handler_function="canvas_update",
    enabled=True,
)

print("✓ Registered: canvas_update")

# Show all tools
print("\nAll tools in database:")
for tool in ToolRepo.list_all():
    status = "✓" if tool.enabled else "✗"
    print(f"  [{status}] {tool.name}")

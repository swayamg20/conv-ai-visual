"""Backend canvas state and structured teaching tools."""

from murmur.canvas.animation import TEACH_WITH_VISUALS_SCHEMA, teach_with_visuals
from murmur.canvas.state import CanvasState, canvas_update, register_canvas_tool

__all__ = [
    "TEACH_WITH_VISUALS_SCHEMA",
    "CanvasState",
    "canvas_update",
    "register_canvas_tool",
    "teach_with_visuals",
]

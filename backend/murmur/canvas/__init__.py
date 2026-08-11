"""Backend canvas state and structured teaching tools."""

from murmur.canvas.animation import TEACH_WITH_VISUALS_SCHEMA, teach_with_visuals
from murmur.canvas.state import canvas_update, get_canvas_state, register_canvas_tool

__all__ = [
    "TEACH_WITH_VISUALS_SCHEMA",
    "canvas_update",
    "get_canvas_state",
    "register_canvas_tool",
    "teach_with_visuals",
]

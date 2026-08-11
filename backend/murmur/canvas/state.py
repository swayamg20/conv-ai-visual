"""
Real-time canvas state management for AI-driven drawing.
Tracks canvas elements with positions for AI context awareness.

Frontend integration uses the typed SVG canvas feature and supports rect,
circle, ellipse, line, arrow, text, path, and teaching-sequence operations.
"""

import logging
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum

from murmur.canvas.animation import TEACH_WITH_VISUALS_SCHEMA
from murmur.persistence.repositories.tools import ToolRepo

logger = logging.getLogger(__name__)


class CanvasAction(str, Enum):
    """Supported canvas operations."""

    RECT = "rect"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    TEXT = "text"
    PATH = "path"  # freeform drawing
    CLEAR = "clear"
    DELETE = "delete"
    HIGHLIGHT = "highlight"


@dataclass
class CanvasElement:
    """A single element on the canvas."""

    id: str
    action: str
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    text: str = ""
    color: str = "#000000"
    fill: str = ""
    stroke_width: float = 2
    font_size: float = 16
    font_family: str = "Arial"
    points: list[list[float]] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def get_bounds(self) -> dict[str, float]:
        """Get bounding box for position awareness."""
        if self.action in [CanvasAction.LINE, CanvasAction.ARROW, CanvasAction.PATH]:
            valid_points: list[tuple[float, float]] = []
            for point in self.points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    valid_points.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    continue

            if valid_points:
                xs = [point[0] for point in valid_points]
                ys = [point[1] for point in valid_points]
                return {
                    "min_x": min(xs),
                    "min_y": min(ys),
                    "max_x": max(xs),
                    "max_y": max(ys),
                }

        return {
            "min_x": self.x,
            "min_y": self.y,
            "max_x": self.x + self.width,
            "max_y": self.y + self.height,
        }


class CanvasState:
    """
    Manages canvas state for a session.
    Tracks all elements for AI position awareness.
    """

    def __init__(self, width: float = 800, height: float = 600):
        self.width = width
        self.height = height
        self.elements: dict[str, CanvasElement] = {}

    def _resolve_reference(self, reference: str | None) -> str | None:
        """Resolve either a concrete element ID or a unique semantic label."""
        if not reference:
            return None
        if reference in self.elements:
            return reference
        return next(
            (
                element_id
                for element_id, element in self.elements.items()
                if element.label == reference
            ),
            None,
        )

    def apply_operations(self, operations: list[dict]) -> list[dict]:
        """
        Apply a batch of operations and return normalized ops for client.
        """
        results = []

        for op in operations:
            action = op.get("action", "")
            try:
                CanvasAction(action)
            except ValueError:
                logger.warning("Ignoring unsupported canvas action: %s", action)
                continue

            if action == "clear":
                self.elements.clear()
                results.append({"action": "clear"})

            elif action == "delete":
                target = self._resolve_reference(op.get("target_id") or op.get("id"))
                if target:
                    del self.elements[target]
                    results.append({"action": "delete", "id": target})

            elif action == "highlight":
                target = self._resolve_reference(op.get("target_id") or op.get("id"))
                if target:
                    results.append({"action": "highlight", "id": target})

            else:
                elem_id = op.get("id") or f"el_{uuid.uuid4().hex[:8]}"
                if elem_id in self.elements:
                    logger.warning("Ignoring duplicate canvas element ID: %s", elem_id)
                    continue

                elem = CanvasElement(
                    id=elem_id,
                    action=action,
                    x=op.get("x", 0),
                    y=op.get("y", 0),
                    width=op.get("width", 0),
                    height=op.get("height", 0),
                    text=op.get("text", ""),
                    color=op.get("color", "#3b82f6"),
                    fill=op.get("fill", ""),
                    stroke_width=op.get("stroke_width", 2),
                    font_size=op.get("font_size", 16),
                    font_family=op.get("font_family", "Arial"),
                    points=op.get("points", []),
                    label=op.get("label", ""),
                )

                self.elements[elem_id] = elem
                results.append({"action": action, **elem.to_dict()})

        return results

    def get_context_summary(self) -> str:
        """
        Generate a text summary of canvas state for LLM context.
        Helps AI know what's on the canvas and where.
        """
        if not self.elements:
            return "Canvas is empty."

        lines = [f"Canvas ({self.width}x{self.height}) contains {len(self.elements)} elements:"]

        for elem in self.elements.values():
            bounds = elem.get_bounds()
            pos = f"at ({bounds['min_x']:.0f}, {bounds['min_y']:.0f})"

            if elem.action == CanvasAction.TEXT:
                desc = f"- Text '{elem.text}' {pos}"
            elif elem.action == CanvasAction.RECT:
                desc = f"- Rectangle {pos}, {elem.width:.0f}x{elem.height:.0f}"
            elif elem.action == CanvasAction.CIRCLE:
                desc = f"- Circle {pos}, radius {elem.width / 2:.0f}"
            elif elem.action == CanvasAction.ARROW:
                if elem.points and len(elem.points) >= 2:
                    start, end = elem.points[0], elem.points[-1]
                    desc = f"- Arrow from ({start[0]:.0f},{start[1]:.0f}) to ({end[0]:.0f},{end[1]:.0f})"
                else:
                    desc = f"- Arrow {pos}"
            elif elem.action == CanvasAction.LINE:
                desc = f"- Line {pos}"
            else:
                desc = f"- {elem.action} {pos}"

            if elem.label:
                desc += f" (label: {elem.label})"

            lines.append(desc)

        return "\n".join(lines)


def canvas_update(
    operations: list[dict],
    session_id: str = "default",
    *,
    state: CanvasState | None = None,
) -> dict:
    """
    Tool handler for canvas updates.
    Called by LLM via tool system.

    Args:
        operations: List of drawing operations
            Each operation: {
                "action": "rect|circle|text|arrow|line|clear|delete|highlight",
                "x": float, "y": float,
                "width": float, "height": float,
                "text": str,
                "color": str,
                "fill": str,
                "points": [[x,y], ...],  # for arrow/line
                "label": str,  # semantic label
                "target_id": str  # for delete/highlight
            }
        session_id: Session identifier retained for the model-tool contract
        state: Pipeline-owned canvas state. Direct calls use a transient state.

    Returns:
        Dict with applied operations and canvas summary
    """
    del session_id
    state = state or CanvasState()
    applied = state.apply_operations(operations)

    return {
        "success": True,
        "applied_count": len(applied),
        "operations": applied,
        "canvas_summary": state.get_context_summary(),
    }


# Canvas tool schema for LLM
CANVAS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "canvas_update",
        "description": """Draw on the shared SVG canvas to illustrate concepts visually while explaining.
Use this to create diagrams, flowcharts, highlight relationships, or sketch ideas.
The canvas renders with a hand-drawn aesthetic and is visible to the user in real-time.

IMPORTANT:
- Send all related drawings in a single call to minimize latency
- Use semantic labels (via 'label' field) to reference elements later
- Only draw NEW elements; delete an old element before replacing it
- Check [Current Canvas State] in your context to see what's already drawn

Coordinate system: (0,0) is top-left. Canvas is 800x600 by default.
Colors: Use hex codes like "#3b82f6" (blue), "#ef4444" (red), "#10b981" (green), "#f59e0b" (orange).
Rendered style: Hand-drawn SVG strokes via Rough.js.
""",
        "parameters": {
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
                            "y": {
                                "type": "number",
                                "description": "Y position (top edge for shapes)",
                            },
                            "width": {
                                "type": "number",
                                "description": "Width of shape (or diameter for circle)",
                            },
                            "height": {"type": "number", "description": "Height of shape"},
                            "text": {
                                "type": "string",
                                "description": "Text content (for text action)",
                            },
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
                                "description": "ID or semantic label of an element to delete/highlight",
                            },
                        },
                        "required": ["action"],
                    },
                }
            },
            "required": ["operations"],
        },
    },
}


# Tool definition for registration in DB
CANVAS_TOOL_DEFINITION = {
    "name": "canvas_update",
    "description": CANVAS_TOOL_SCHEMA["function"]["description"],
    "parameters": CANVAS_TOOL_SCHEMA["function"]["parameters"],
    "handler_module": "murmur.canvas.state",
    "handler_function": "canvas_update",
}


def register_canvas_tool() -> None:
    """Upsert the built-in canvas tool and repair legacy handler paths."""
    ToolRepo.upsert(**CANVAS_TOOL_DEFINITION, enabled=True)
    logger.info("Registered canvas_update tool in DB")


ANIMATION_TOOLS = [TEACH_WITH_VISUALS_SCHEMA]

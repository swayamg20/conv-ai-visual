import json
import logging
import uuid
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger("canvas")

class CanvasAction(str, Enum):
    """Supported canvas operations."""
    RECT = "rect"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    TEXT = "text"
    PATH = "path"
    CLEAR = "clear"
    DELETE = "delete"
    UPDATE = "update"
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
    points: List[List[float]] = field(default_factory=list)
    label: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def get_bounds(self) -> Dict[str, float]:
        """Get bounding box for position awareness."""
        if self.action in [CanvasAction.LINE, CanvasAction.ARROW, CanvasAction.PATH]:
            if self.points:
                try:
                    valid_points = []
                    for p in self.points:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            try:
                                x, y = float(p[0]), float(p[1])
                                valid_points.append((x, y))
                            except (ValueError, TypeError, IndexError):
                                continue

                    if valid_points:
                        xs = [p[0] for p in valid_points]
                        ys = [p[1] for p in valid_points]
                        return {
                            "min_x": min(xs),
                            "min_y": min(ys),
                            "max_x": max(xs),
                            "max_y": max(ys)
                        }
                except (TypeError, IndexError, ValueError, KeyError) as e:
                    pass

        return {
            "min_x": self.x,
            "min_y": self.y,
            "max_x": self.x + self.width,
            "max_y": self.y + self.height
        }


@dataclass 
class CanvasOperation:
    action: str
    id: Optional[str] = None
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    text: str = ""
    color: str = "#3b82f6"
    fill: str = ""
    stroke_width: float = 2
    font_size: float = 16
    font_family: str = "Arial"
    points: List[List[float]] = field(default_factory=list)
    label: str = ""
    target_id: str = ""
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        if not d["id"] and d["action"] not in ["clear", "delete", "update", "highlight"]:
            d["id"] = f"el_{uuid.uuid4().hex[:8]}"
        return d


class CanvasState:
    def __init__(self, width: float = 800, height: float = 600):
        self.width = width
        self.height = height
        self.elements: Dict[str, CanvasElement] = {}
        self._history: List[Dict] = []  # for undo
    
    def apply_operations(self, operations: List[Dict]) -> List[Dict]:
        results = []
        
        for op in operations:
            action = op.get("action", "")
            
            if action == "clear":
                self._history.append({"elements": self.elements.copy()})
                self.elements.clear()
                results.append({"action": "clear"})
                
            elif action == "delete":
                target = op.get("target_id") or op.get("id")
                if target and target in self.elements:
                    self._history.append({"deleted": self.elements[target].to_dict()})
                    del self.elements[target]
                    results.append({"action": "delete", "id": target})
                    
            elif action == "highlight":
                target = op.get("target_id") or op.get("id")
                if target and target in self.elements:
                    results.append({"action": "highlight", "id": target})
                    
            elif action == "update":
                target = op.get("target_id") or op.get("id")
                if target and target in self.elements:
                    elem = self.elements[target]
                    for k, v in op.items():
                        if k not in ["action", "target_id"] and hasattr(elem, k):
                            setattr(elem, k, v)
                    results.append({"action": "update", **elem.to_dict()})
                    
            else:
                elem_id = op.get("id") or f"el_{uuid.uuid4().hex[:8]}"                
                points = op.get("points", [])
                if isinstance(points, str):
                    try:
                        points = json.loads(points)
                    except json.JSONDecodeError:
                        points = []
                
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
                    points=points,
                    label=op.get("label", "")
                )
                
                self.elements[elem_id] = elem
                results.append({"action": action, **elem.to_dict()})
        
        return results
    
    def get_context_summary(self) -> str:
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
                desc = f"- Circle {pos}, radius {elem.width/2:.0f}"
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
    
    def to_json(self) -> str:
        """Serialize full state for persistence/transfer."""
        return json.dumps({
            "width": self.width,
            "height": self.height,
            "elements": [e.to_dict() for e in self.elements.values()]
        })
    
    @classmethod
    def from_json(cls, data: str) -> "CanvasState":
        """Deserialize from JSON."""
        d = json.loads(data)
        state = cls(width=d.get("width", 800), height=d.get("height", 600))
        for elem_data in d.get("elements", []):
            elem = CanvasElement(**elem_data)
            state.elements[elem.id] = elem
        return state

_canvas_sessions: Dict[str, CanvasState] = {}


def get_canvas_state(session_id: str) -> CanvasState:
    """Get or create canvas state for a session."""
    if session_id not in _canvas_sessions:
        _canvas_sessions[session_id] = CanvasState()
    return _canvas_sessions[session_id]


def clear_canvas_session(session_id: str):
    """Remove canvas state for a session."""
    _canvas_sessions.pop(session_id, None)

def canvas_update(operations_json: str = None, operations: List[Dict] = None, session_id: str = "default") -> Dict:
    if operations_json:
        try:
            operations = json.loads(operations_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse operations_json: {e}")
            return {"success": False, "error": f"Invalid JSON: {e}"}
    
    if not operations:
        return {"success": False, "error": "No operations provided"}
    
    state = get_canvas_state(session_id)
    applied = state.apply_operations(operations)
    
    return {
        "success": True,
        "applied_count": len(applied),
        "operations": applied,
        "canvas_summary": state.get_context_summary()
    }

CANVAS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "canvas_update",
        "description": """Draw diagrams on the shared canvas.

Operations JSON format - each object has:
- action: "rect", "circle", "ellipse", "arrow", "text"
- x, y: position (canvas is 800x600)
- width, height: size (use 150+ width for boxes with text)
- label: text to show INSIDE the shape (auto-centered)
- color: hex color

Example: '[{"action":"rect","x":350,"y":50,"width":150,"height":50,"color":"#6b7280","label":"Clients"},{"action":"rect","x":350,"y":150,"width":150,"height":50,"color":"#3b82f6","label":"LB"},{"action":"arrow","points":"[[425,100],[425,150]]","color":"#000000"}]'

For arrows: use "points" as JSON string like "[[x1,y1],[x2,y2]]"
Colors: #3b82f6 blue, #10b981 green, #f59e0b orange, #ef4444 red, #6b7280 gray""",
        "parameters": {
            "type": "object",
            "properties": {
                "operations_json": {
                    "type": "string",
                    "description": "JSON array of drawing operations"
                }
            },
            "required": ["operations_json"]
        }
    }
}


# Tool definition for registration in DB
CANVAS_TOOL_DEFINITION = {
    "name": "canvas_update",
    "description": CANVAS_TOOL_SCHEMA["function"]["description"],
    "parameters": CANVAS_TOOL_SCHEMA["function"]["parameters"],
    "handler_module": "funcs.canvas",
    "handler_function": "canvas_update"
}


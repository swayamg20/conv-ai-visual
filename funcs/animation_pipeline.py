"""
Animation Pipeline for Math Tutor

Manages animation orchestration, teaching sequences, and GSAP-compatible
animation command generation for the frontend SVG canvas.
"""

import logging
import uuid
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
import math

logger = logging.getLogger("animation_pipeline")


@dataclass
class AnimationCommand:
    """Single animation command for frontend GSAP."""
    type: str  # "animation", "latex", "teaching_sequence"
    target_id: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.5
    ease: str = "power2.out"
    delay: float = 0

    def to_dict(self) -> Dict:
        return asdict(self)


class AnimationPipeline:
    """
    Manages animation orchestration for teaching sequences.

    Converts backend animation operations to GSAP-compatible commands
    for the frontend.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.timelines: Dict[str, List[Dict]] = {}
        self.animation_queue: List[Dict] = []

    def apply_animation(self, operation: Dict) -> Dict:
        """
        Convert animation operation to GSAP-compatible format.

        Args:
            operation: {
                "target_id": str,
                "properties": {"x": 100, "opacity": 1, "scale": 1.2},
                "duration": float,
                "ease": str,
                "delay": float
            }

        Returns:
            Animation command dict for frontend
        """
        target_id = operation.get("target_id")
        if not target_id:
            raise ValueError("Animation operation requires target_id")

        properties = operation.get("properties", {})
        duration = operation.get("duration", 0.5)
        ease = operation.get("ease", "power2.out")
        delay = operation.get("delay", 0)

        command = AnimationCommand(
            type="animation",
            target_id=target_id,
            properties=properties,
            duration=duration,
            ease=ease,
            delay=delay
        )

        logger.info(f"Animation command created for {target_id}: {properties}")
        return command.to_dict()

    def create_teaching_sequence(self, steps: List[Dict]) -> Dict:
        """
        Create multi-step animation sequence for teaching.

        Args:
            steps: List of step dicts with:
                - action: "draw" | "animate" | "latex" | "highlight" | "morph" | "pause"
                - element: CanvasOperation (for draw)
                - target_id: str (for animate/highlight)
                - properties: dict (for animate)
                - latex: str (for latex)
                - x, y: float (for latex)
                - duration: float
                - speech_cue: str (optional)

        Returns:
            Timeline specification for frontend GSAP timeline
        """
        timeline_id = f"timeline_{uuid.uuid4().hex[:8]}"

        timeline_steps = []
        for step in steps:
            action = step.get("action")

            if action == "draw":
                # Drawing step
                timeline_steps.append({
                    "action": "draw",
                    "element": step.get("element"),
                    "speech_cue": step.get("speech_cue")
                })

            elif action == "animate":
                # Animation step
                timeline_steps.append({
                    "action": "animate",
                    "target_id": step.get("target_id"),
                    "properties": step.get("properties", {}),
                    "duration": step.get("duration", 0.5),
                    "speech_cue": step.get("speech_cue")
                })

            elif action == "latex":
                # LaTeX rendering step
                timeline_steps.append({
                    "action": "latex",
                    "latex": step.get("latex"),
                    "x": step.get("x"),
                    "y": step.get("y"),
                    "font_size": step.get("font_size", 20),
                    "color": step.get("color", "#000000"),
                    "speech_cue": step.get("speech_cue")
                })

            elif action == "text":
                # Text step (direct text rendering without wrapping in element)
                timeline_steps.append({
                    "action": "text",
                    "text": step.get("text", ""),
                    "x": step.get("x"),
                    "y": step.get("y"),
                    "color": step.get("color", "#000000"),
                    "font_size": step.get("font_size", 16),
                    "font_family": step.get("font_family"),
                    "target_id": step.get("target_id"),
                    "speech_cue": step.get("speech_cue")
                })

            elif action == "highlight":
                # Highlight step
                timeline_steps.append({
                    "action": "highlight",
                    "target_id": step.get("target_id"),
                    "duration": step.get("duration", 0.3),
                    "speech_cue": step.get("speech_cue")
                })

            elif action == "pause":
                # Pause step
                timeline_steps.append({
                    "action": "pause",
                    "duration": step.get("duration", 0.5)
                })

            elif action == "morph":
                # Morph step (future enhancement)
                logger.warning("Morph action not yet implemented")

        self.timelines[timeline_id] = timeline_steps

        logger.info(f"Teaching sequence created: {timeline_id} with {len(timeline_steps)} steps")

        return {
            "type": "teaching_sequence",
            "timeline_id": timeline_id,
            "steps": timeline_steps
        }

    def generate_function_points(
        self,
        function_str: str,
        x_range: List[float],
        y_range: List[float],
        num_points: int = 100
    ) -> List[List[float]]:
        """
        Generate points for plotting a mathematical function.

        Args:
            function_str: Math expression (e.g., "x**2", "sin(x)")
            x_range: [min_x, max_x]
            y_range: [min_y, max_y]
            num_points: Number of points to generate

        Returns:
            List of [x, y] points
        """
        min_x, max_x = x_range
        min_y, max_y = y_range

        points = []
        step = (max_x - min_x) / (num_points - 1)

        # Safe eval with math functions
        safe_dict = {
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "sqrt": math.sqrt,
            "exp": math.exp,
            "log": math.log,
            "abs": abs,
            "pi": math.pi,
            "e": math.e
        }

        for i in range(num_points):
            x = min_x + i * step
            try:
                # Evaluate function
                safe_dict["x"] = x
                y = eval(function_str, {"__builtins__": {}}, safe_dict)

                # Clamp to y_range
                y = max(min_y, min(max_y, y))

                points.append([x, y])
            except Exception as e:
                logger.warning(f"Error evaluating function at x={x}: {e}")
                continue

        return points


# Per-session animation pipelines
_animation_sessions: Dict[str, AnimationPipeline] = {}


def get_animation_pipeline(session_id: str) -> AnimationPipeline:
    """Get or create animation pipeline for a session."""
    if session_id not in _animation_sessions:
        _animation_sessions[session_id] = AnimationPipeline(session_id)
    return _animation_sessions[session_id]


def clear_animation_session(session_id: str):
    """Remove animation pipeline for a session."""
    _animation_sessions.pop(session_id, None)


# ============= Tool Handlers =============

def animate_element(
    target_id: str,
    properties: Dict[str, Any],
    duration: float = 0.5,
    ease: str = "power2.out",
    delay: float = 0,
    session_id: str = "default"
) -> Dict:
    """
    Tool handler for animate_element.

    Args:
        target_id: ID of element to animate
        properties: Animation properties (x, y, opacity, scale, rotation)
        duration: Animation duration in seconds
        ease: GSAP easing function
        delay: Delay before starting
        session_id: Session identifier

    Returns:
        Success dict with animation command
    """
    pipeline = get_animation_pipeline(session_id)

    command = pipeline.apply_animation({
        "target_id": target_id,
        "properties": properties,
        "duration": duration,
        "ease": ease,
        "delay": delay
    })

    return {
        "success": True,
        "animation_command": command
    }


def render_latex(
    latex: str,
    x: float,
    y: float,
    font_size: float = 20,
    color: str = "#000000",
    label: str = "",
    session_id: str = "default"
) -> Dict:
    """
    Tool handler for render_latex.

    Args:
        latex: LaTeX expression (e.g., "\\frac{x^2}{2}")
        x: X position
        y: Y position
        font_size: Font size in pixels
        color: Text color (hex)
        label: Optional ID for referencing
        session_id: Session identifier

    Returns:
        Success dict with latex element
    """
    elem_id = label or f"latex_{uuid.uuid4().hex[:8]}"

    element = {
        "type": "latex",
        "id": elem_id,
        "latex": latex,
        "x": x,
        "y": y,
        "font_size": font_size,
        "color": color
    }

    logger.info(f"LaTeX element created: {elem_id} at ({x}, {y})")

    return {
        "success": True,
        "element": element
    }


def create_teaching_sequence(
    steps: List[Dict],
    session_id: str = "default"
) -> Dict:
    """
    Tool handler for create_teaching_sequence.

    Args:
        steps: List of teaching steps
        session_id: Session identifier

    Returns:
        Success dict with timeline
    """
    pipeline = get_animation_pipeline(session_id)
    timeline = pipeline.create_teaching_sequence(steps)

    return {
        "success": True,
        "timeline": timeline
    }


def plot_function(
    function: str,
    x_range: List[float],
    y_range: List[float],
    color: str = "#3b82f6",
    animate: bool = True,
    show_axes: bool = True,
    session_id: str = "default"
) -> Dict:
    """
    Tool handler for plot_function.

    Args:
        function: Math expression (e.g., "x**2", "sin(x)")
        x_range: [min_x, max_x]
        y_range: [min_y, max_y]
        color: Line color
        animate: Whether to animate drawing
        show_axes: Whether to show axes
        session_id: Session identifier

    Returns:
        Success dict with graph data
    """
    pipeline = get_animation_pipeline(session_id)

    # Generate points for the function
    points = pipeline.generate_function_points(function, x_range, y_range)

    logger.info(f"Function plot generated: {len(points)} points for {function}")

    return {
        "success": True,
        "graph": {
            "type": "function_plot",
            "function": function,
            "points": points,
            "x_range": x_range,
            "y_range": y_range,
            "color": color,
            "animate": animate,
            "show_axes": show_axes
        }
    }


# ============= Tool Schemas =============

ANIMATE_ELEMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "animate_element",
        "description": """Animate a canvas element smoothly using GSAP.

Use this to:
- Make elements appear (opacity: 0 → 1)
- Move elements (x, y coordinates)
- Scale for emphasis (scale: 1 → 1.2 → 1)
- Rotate shapes (rotation in degrees)

Synchronize animations with your speech for maximum impact.
Example: "As we move this term..." → animate_element(target_id="term1", properties={"x": 200})
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "string",
                    "description": "ID of the element to animate"
                },
                "properties": {
                    "type": "object",
                    "description": "Properties to animate",
                    "properties": {
                        "x": {"type": "number", "description": "X position"},
                        "y": {"type": "number", "description": "Y position"},
                        "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                        "scale": {"type": "number", "description": "Scale factor (1 = normal)"},
                        "rotation": {"type": "number", "description": "Rotation in degrees"}
                    }
                },
                "duration": {
                    "type": "number",
                    "default": 0.5,
                    "description": "Animation duration in seconds"
                },
                "ease": {
                    "type": "string",
                    "enum": ["power2.out", "back.out", "elastic.out", "bounce.out"],
                    "default": "power2.out",
                    "description": "Easing function"
                },
                "delay": {
                    "type": "number",
                    "default": 0,
                    "description": "Delay before starting (seconds)"
                }
            },
            "required": ["target_id", "properties"]
        }
    }
}

RENDER_LATEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "render_latex",
        "description": """Render LaTeX mathematical equations on the canvas.

Use this for:
- Formulas: \\\\frac{a}{b}, x^2 + y^2 = r^2
- Equations: E = mc^2, 2x + 3 = 7
- Expressions: \\\\sum_{i=1}^{n} i, \\\\int x^2 dx

The equation will render with proper mathematical typography.
IMPORTANT: Escape backslashes (use \\\\ instead of \\)
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "latex": {
                    "type": "string",
                    "description": "LaTeX expression (e.g., '\\\\\\\\frac{x}{y}')"
                },
                "x": {"type": "number", "description": "X position on canvas"},
                "y": {"type": "number", "description": "Y position on canvas"},
                "font_size": {
                    "type": "number",
                    "default": 20,
                    "description": "Font size in pixels"
                },
                "color": {
                    "type": "string",
                    "default": "#000000",
                    "description": "Text color (hex)"
                },
                "label": {
                    "type": "string",
                    "description": "ID for referencing this equation later"
                }
            },
            "required": ["latex", "x", "y"]
        }
    }
}

CREATE_TEACHING_SEQUENCE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_teaching_sequence",
        "description": """Create a step-by-step animation sequence for teaching math concepts.

Perfect for:
- Solving equations step-by-step
- Geometric transformations (rotate, scale, translate)
- Building diagrams incrementally
- Showing mathematical relationships

Each step animates as you explain, creating synchronized teaching.
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered list of teaching steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["draw", "animate", "latex", "text", "highlight", "morph", "pause"],
                                "description": "Step action type. Use 'text' for labels/annotations, 'latex' for math equations, 'draw' for shapes."
                            },
                            "text": {
                                "type": "string",
                                "description": "Text content (for 'text' action)"
                            },
                            "element": {
                                "type": "object",
                                "description": "Canvas element to draw (for 'draw' action)"
                            },
                            "target_id": {
                                "type": "string",
                                "description": "Element ID (for 'animate' or 'highlight')"
                            },
                            "properties": {
                                "type": "object",
                                "description": "Animation properties (for 'animate')"
                            },
                            "latex": {
                                "type": "string",
                                "description": "LaTeX expression (for 'latex' action)"
                            },
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "font_size": {"type": "number"},
                            "color": {"type": "string"},
                            "duration": {
                                "type": "number",
                                "default": 0.5,
                                "description": "Step duration in seconds"
                            },
                            "speech_cue": {
                                "type": "string",
                                "description": "What you're saying during this step"
                            }
                        },
                        "required": ["action"]
                    }
                }
            },
            "required": ["steps"]
        }
    }
}

PLOT_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plot_function",
        "description": """Plot a mathematical function on a coordinate system.

Use for:
- Graphing functions: y = x^2, y = sin(x), y = log(x)
- Showing derivatives/integrals
- Comparing multiple functions

The graph will animate drawing from left to right.
Supported functions: sin, cos, tan, sqrt, exp, log, abs, x**n
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "Math expression (e.g., 'x**2', 'sin(x)', '2*x + 3')"
                },
                "x_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[min_x, max_x] for plotting"
                },
                "y_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[min_y, max_y] for plotting"
                },
                "color": {
                    "type": "string",
                    "default": "#3b82f6",
                    "description": "Line color (hex)"
                },
                "animate": {
                    "type": "boolean",
                    "default": True,
                    "description": "Animate the drawing of the graph"
                },
                "show_axes": {
                    "type": "boolean",
                    "default": True,
                    "description": "Show coordinate axes"
                }
            },
            "required": ["function", "x_range", "y_range"]
        }
    }
}

"""
Bridge between LLM tool calls and Manim web export.

Converts LLM-generated manim_animate instructions into
compact commands for the ManimStreamPlayer on the frontend.
"""

import json
import logging
import re
import sys
from typing import Dict

logger = logging.getLogger("manim_bridge")


def _fix_json(raw: str) -> str:
    """
    Attempt to fix common JSON errors from LLM output:
    1. LaTeX backslashes: \frac -> \\frac
    2. Double closing braces: }}, -> },
    3. Trailing commas before ] or }
    """
    fixed = raw
    # Fix double closing braces: }}, -> },
    fixed = re.sub(r'\}\}(\s*[,\]])', r'}\1', fixed)
    # Fix LaTeX backslashes (single \ followed by letter, not already escaped)
    fixed = re.sub(r'(?<!\\)\\([a-zA-Z])', r'\\\\\\1', fixed)
    # Fix trailing commas before ] or }
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    return fixed

# Lazy singleton - avoid importing manimlib at module level
# because manimlib/__init__.py runs argparse which hijacks sys.argv
_manim_service = None


def _get_service():
    global _manim_service
    if _manim_service is None:
        # Temporarily clear sys.argv so manimlib's argparse doesn't crash
        saved_argv = sys.argv
        sys.argv = [sys.argv[0]]
        try:
            from manimlib.web.api import ManimService
            _manim_service = ManimService(width=800, height=450, background_color="#1a1a2e")
        finally:
            sys.argv = saved_argv
    return _manim_service


def manim_animate(instructions_json: str, session_id: str = "default") -> Dict:
    """
    Tool function called by LLM.

    Accepts a JSON array of Manim instructions and returns
    compact commands for the frontend ManimStreamPlayer.

    Instructions format:
    [
        {"action": "add", "type": "circle", "id": "c1", "props": {"radius": 1, "color": "#3b82f6"}},
        {"action": "play", "animation": "create", "target": "c1", "duration": 1.0},
        {"action": "add", "type": "tex", "id": "eq1", "content": "E = mc^2", "props": {"color": "#ffffff"}},
        {"action": "play", "animation": "write", "target": "eq1", "duration": 1.5},
        {"action": "wait", "duration": 0.5},
        {"action": "play", "animation": "fade_out", "target": "c1", "duration": 0.5},
        {"action": "add", "type": "text", "id": "t1", "content": "Hello!", "props": {"color": "#ffffff"}},
        {"action": "play", "animation": "fade_in", "target": "t1", "duration": 0.5}
    ]
    """
    try:
        instructions = json.loads(instructions_json)
    except json.JSONDecodeError as e:
        # Try fixing LaTeX backslashes and retry
        logger.warning(f"JSON parse failed, attempting LaTeX fix: {e}")
        logger.debug(f"Raw input: {instructions_json[:500]}")
        try:
            fixed = _fix_json(instructions_json)
            instructions = json.loads(fixed)
            logger.info("JSON parsed successfully after LaTeX fix")
        except json.JSONDecodeError as e2:
            logger.error(f"Failed to parse even after fix: {e2}")
            logger.error(f"Raw input: {instructions_json[:500]}")
            return {"success": False, "error": f"Invalid JSON: {e2}"}

    if not instructions:
        return {"success": False, "error": "No instructions provided"}

    try:
        commands = _get_service().execute(instructions)
        return {
            "success": True,
            "command_count": len(commands),
            "commands": commands,
        }
    except Exception as e:
        logger.exception(f"Manim execution failed: {e}")
        return {"success": False, "error": str(e)}


def manim_animate_streaming(instructions_json: str):
    """
    Generator version for streaming commands over DataChannel.
    Yields one command dict at a time.
    """
    try:
        instructions = json.loads(instructions_json)
    except json.JSONDecodeError:
        try:
            instructions = json.loads(_fix_json(instructions_json))
        except json.JSONDecodeError:
            return

    for cmd in _get_service().execute_streaming(instructions):
        yield cmd


MANIM_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "manim_animate",
        "description": """Create animated math visualizations on the canvas using Manim.

Send a JSON array of instructions. Each instruction has an "action" field.

ACTIONS:

1. "add" - Add a shape/object to the scene
   Required: "type", "id"
   Optional: "props" (object with color, radius, side_length, etc.), "content" (for tex/text)

   Types: "circle", "square", "rectangle", "line", "arrow", "tex" (LaTeX), "text", "axes", "number_line"

   Examples:
   {"action": "add", "type": "circle", "id": "c1", "props": {"radius": 1.5, "color": "#3b82f6"}}
   {"action": "add", "type": "tex", "id": "eq1", "content": "E = mc^2", "props": {"color": "#ffffff"}}
   {"action": "add", "type": "square", "id": "s1", "props": {"side_length": 2, "color": "#ef4444", "fill_color": "#ef4444", "fill_opacity": 0.3}}
   {"action": "add", "type": "text", "id": "t1", "content": "Hello World", "props": {"color": "#10b981"}}
   {"action": "add", "type": "line", "id": "l1", "props": {"start": [-3, 0, 0], "end": [3, 0, 0], "color": "#f59e0b"}}
   {"action": "add", "type": "arrow", "id": "a1", "props": {"start": [0, -2, 0], "end": [0, 2, 0], "color": "#ffffff"}}
   {"action": "add", "type": "axes", "id": "ax1", "props": {"x_range": [-5, 5, 1], "y_range": [-3, 3, 1]}}

2. "play" - Animate an object
   Required: "animation", "target"
   Optional: "duration" (seconds, default 1.0)

   Animations: "create" (draw stroke), "write" (for tex/text), "fade_in", "fade_out", "transform"

   For "transform": also provide "to" (id of target shape) or "to_type" + "to_props"

   Examples:
   {"action": "play", "animation": "create", "target": "c1", "duration": 1.0}
   {"action": "play", "animation": "write", "target": "eq1", "duration": 1.5}
   {"action": "play", "animation": "fade_in", "target": "t1", "duration": 0.5}
   {"action": "play", "animation": "transform", "target": "c1", "to": "s1", "duration": 1.0}

3. "wait" - Pause between animations
   {"action": "wait", "duration": 0.5}

4. "remove" - Remove an object
   {"action": "remove", "target": "c1"}

5. "clear" - Clear entire canvas
   {"action": "clear"}

COORDINATE SYSTEM:
- Origin (0,0,0) is center of canvas
- x: left=-7 to right=7
- y: bottom=-4 to top=4
- Use [x, y, 0] for positions (z is always 0 for 2D)

COLORS: Use hex strings
- #3b82f6 (blue), #10b981 (green), #f59e0b (orange)
- #ef4444 (red), #8b5cf6 (purple), #ffffff (white)

LATEX: Use raw LaTeX strings for the "content" field of "tex" type
- Simple: "x^2 + y^2 = r^2"
- Fractions: "\\frac{a}{b}"
- Greek: "\\alpha, \\beta, \\gamma"
- Aligned: "\\begin{align} a &= b \\\\ c &= d \\end{align}"

TEACHING STYLE:
- Build diagrams step by step (add then animate one at a time)
- Use "create" animation to draw shapes (shows the stroke being drawn)
- Use "write" animation for equations and text
- Add short waits (0.3-0.5s) between animations
- Transform shapes to show relationships
- Keep it simple: 3-5 objects per explanation""",
        "parameters": {
            "type": "object",
            "properties": {
                "instructions_json": {
                    "type": "string",
                    "description": "JSON array of Manim animation instructions"
                }
            },
            "required": ["instructions_json"]
        }
    }
}

MANIM_TOOL_DEFINITION = {
    "name": "manim_animate",
    "description": MANIM_TOOL_SCHEMA["function"]["description"],
    "parameters": MANIM_TOOL_SCHEMA["function"]["parameters"],
    "handler_module": "funcs.manim_bridge",
    "handler_function": "manim_animate"
}

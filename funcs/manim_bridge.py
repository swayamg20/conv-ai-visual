"""
Bridge between LLM tool calls and Manim web export.

Converts LLM-generated manim_animate instructions into
compact commands for the ManimStreamPlayer on the frontend.
"""

import json
import logging
import re
import signal
import sys
from typing import Dict

logger = logging.getLogger("manim_bridge")

# ---- Color normalization ----
# LLMs love sending named colors instead of hex. Map them all.
_COLOR_NAME_TO_HEX = {
    # 3b1b palette
    "blue": "#58C4DD",
    "teal": "#5CD0B3",
    "green": "#83C167",
    "yellow": "#FFFF00",
    "gold": "#F0AC5F",
    "orange": "#FF862F",
    "red": "#FC6255",
    "maroon": "#C55F73",
    "purple": "#9A72AC",
    "pink": "#D147BD",
    "grey": "#888888",
    "gray": "#888888",
    "white": "#FFFFFF",
    "black": "#000000",
    "light_blue": "#9CDCEB",
    "light_green": "#ACEAD7",
    "light_brown": "#CD853F",
    # CSS-ish names the LLM might hallucinate
    "cyan": "#58C4DD",
    "magenta": "#D147BD",
    "lime": "#83C167",
    "brown": "#8B4513",
    "navy": "#1C758A",
    "indigo": "#6C5B7B",
    "violet": "#9A72AC",
    "aqua": "#5CD0B3",
    "coral": "#FC6255",
    "salmon": "#FF8080",
    "beige": "#F5F0E3",
    "ivory": "#FFFFF0",
    "silver": "#BBBBBB",
    "dark_blue": "#1C758A",
    "dark_red": "#CF5044",
    "cream": "#F5F0E3",
}


def _normalize_color(value: str) -> str:
    """
    Convert any color representation to a valid hex string.
    Handles: named colors (WHITE, blue), missing # prefix, etc.
    """
    if not isinstance(value, str) or not value:
        return value
    v = value.strip()
    # Already valid hex
    if re.match(r'^#[0-9a-fA-F]{6}$', v):
        return v
    # Hex without # prefix
    if re.match(r'^[0-9a-fA-F]{6}$', v):
        return f"#{v}"
    # Named color lookup (case-insensitive)
    key = v.lower().replace(" ", "_").replace("-", "_")
    if key in _COLOR_NAME_TO_HEX:
        return _COLOR_NAME_TO_HEX[key]
    # 3-char hex shorthand
    if re.match(r'^#?[0-9a-fA-F]{3}$', v):
        h = v.lstrip("#")
        return f"#{h[0]*2}{h[1]*2}{h[2]*2}"
    # Fallback — return white and log
    logger.warning(f"Unknown color '{value}', falling back to white")
    return "#FFFFFF"


def _normalize_instruction_colors(instr: dict) -> dict:
    """Recursively normalize all color fields in an instruction dict."""
    color_keys = {"color", "stroke_color", "fill_color", "background_color"}
    if "props" in instr and isinstance(instr["props"], dict):
        for key in color_keys:
            if key in instr["props"]:
                instr["props"][key] = _normalize_color(instr["props"][key])
    # Top-level color (some LLMs put it here)
    for key in color_keys:
        if key in instr:
            instr[key] = _normalize_color(instr[key])
    return instr


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


class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("Operation timed out")


def _get_service():
    global _manim_service
    if _manim_service is None:
        # Temporarily clear sys.argv so manimlib's argparse doesn't crash
        saved_argv = sys.argv
        sys.argv = [sys.argv[0]]
        try:
            logger.info("Loading ManimService (first call, may take a moment)...")
            from manimlib.web.api import ManimService
            import numpy as np
            logger.info("ManimService imported successfully")

            class PositionAwareManimService(ManimService):
                """Extends ManimService with position/scale + safe tex/text."""

                def _create_mobject(self, instr):
                    mob_type = instr.get("type", "")
                    # Tex/Text can hang if LaTeX/Pango not installed — wrap with timeout
                    if mob_type in ("tex", "text"):
                        try:
                            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                            signal.alarm(10)  # 10s timeout for LaTeX
                            try:
                                mob = super()._create_mobject(instr)
                            finally:
                                signal.alarm(0)
                                signal.signal(signal.SIGALRM, old_handler)
                        except _TimeoutError:
                            logger.error(f"Timeout creating {mob_type} mobject — LaTeX/Pango may not be installed")
                            return None
                        except Exception as e:
                            logger.error(f"Failed to create {mob_type}: {e}")
                            return None
                    else:
                        mob = super()._create_mobject(instr)

                    if mob is not None:
                        props = instr.get("props", {})
                        if "position" in props:
                            pos = np.array(props["position"], dtype=float)
                            mob.move_to(pos)
                        if "scale" in props:
                            mob.scale(props["scale"])
                    return mob

            _manim_service = PositionAwareManimService(
                width=1920, height=1080, background_color="#1a1a2e"
            )
            logger.info("ManimService ready")
        except Exception as e:
            logger.exception(f"Failed to initialize ManimService: {e}")
            raise
        finally:
            sys.argv = saved_argv
    return _manim_service


def manim_animate(instructions_json: str, session_id: str = "default") -> Dict:
    """
    Tool function called by LLM.

    Accepts a JSON array of Manim instructions and returns
    compact commands for the frontend ManimStreamPlayer.
    """
    try:
        instructions = json.loads(instructions_json)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed, attempting fix: {e}")
        logger.debug(f"Raw input: {instructions_json[:500]}")
        try:
            fixed = _fix_json(instructions_json)
            instructions = json.loads(fixed)
            logger.info("JSON parsed successfully after fix")
        except json.JSONDecodeError as e2:
            logger.error(f"Failed to parse even after fix: {e2}")
            logger.error(f"Raw input: {instructions_json[:500]}")
            return {"success": False, "error": f"Invalid JSON: {e2}"}

    if not instructions:
        return {"success": False, "error": "No instructions provided"}

    # Normalize all color values before hitting manim
    instructions = [_normalize_instruction_colors(instr) for instr in instructions]

    logger.info(f"Executing {len(instructions)} manim instructions")
    for i, instr in enumerate(instructions):
        logger.debug(f"  [{i}] {instr.get('action', '?')} {instr.get('type', instr.get('animation', ''))}")

    try:
        service = _get_service()
        logger.info("Service ready, executing instructions...")
        commands = service.execute(instructions)
        logger.info(f"Execution complete: {len(commands)} commands generated")
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

    instructions = [_normalize_instruction_colors(instr) for instr in instructions]
    for cmd in _get_service().execute_streaming(instructions):
        yield cmd


MANIM_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "manim_animate",
        "description": """Create animated math visualizations like 3Blue1Brown using Manim.

Send a JSON array of instructions. Each instruction has an "action" field.

ACTIONS:

1. "add" — Add an object to the scene
   Required: "type", "id"
   Optional: "props" (color, radius, position, scale, fill_color, fill_opacity, etc.), "content" (for tex/text)

   Types: "circle", "square", "rectangle", "line", "arrow", "text" (labels/titles), "tex" (LaTeX math — requires LaTeX installed), "axes", "number_line"
   PREFER "text" over "tex" for non-math labels and titles. Use "tex" ONLY for math formulas.

2. "play" — Animate an object
   Required: "animation", "target"
   Optional: "duration" (seconds, default 1.0)
   Animations: "create" (stroke draw), "write" (text/tex reveal), "fade_in", "fade_out", "transform"
   For "transform": provide "to" (id of existing mobject) or "to_type" + "to_props"

3. "wait" — Pause ({"action": "wait", "duration": 0.5})
4. "remove" — Remove object ({"action": "remove", "target": "c1"})
5. "clear" — Clear scene ({"action": "clear"})

COLORS — MUST be hex strings (NEVER use named colors like WHITE, BLUE, RED):
  #58C4DD (3b1b blue), #83C167 (green), #FFFF00 (yellow)
  #FF862F (orange), #FC6255 (red), #9A72AC (purple)
  #FFFFFF (white), #F0AC5F (gold), #5CD0B3 (teal)

POSITIONING — Canvas: x ∈ [-7, 7], y ∈ [-4, 4]. ALWAYS set "position": [x, y, 0].
  Title: [0, 3.5, 0] | Left item: [-4, 0, 0] | Right item: [4, 0, 0]
  Center: [0, 0, 0] | Bottom equation: [0, -3, 0]
  NEVER stack two objects at the same position.

LATEX: Raw LaTeX for "content" of "tex" type.
  "x^2 + y^2 = r^2",  "\\\\frac{a}{b}",  "\\\\int_0^1 f(x)\\\\,dx"

EXAMPLE — Pythagorean theorem (3b1b style):
[
  {"action": "clear"},
  {"action": "add", "type": "text", "id": "title", "content": "The Pythagorean Theorem", "props": {"color": "#FFFFFF", "position": [0, 3.5, 0], "scale": 1.2}},
  {"action": "play", "animation": "write", "target": "title", "duration": 1.0},
  {"action": "wait", "duration": 0.3},
  {"action": "add", "type": "line", "id": "a", "props": {"start": [-2, -1, 0], "end": [2, -1, 0], "color": "#58C4DD"}},
  {"action": "play", "animation": "create", "target": "a", "duration": 0.8},
  {"action": "add", "type": "line", "id": "b", "props": {"start": [2, -1, 0], "end": [2, 2, 0], "color": "#83C167"}},
  {"action": "play", "animation": "create", "target": "b", "duration": 0.8},
  {"action": "add", "type": "line", "id": "c", "props": {"start": [2, 2, 0], "end": [-2, -1, 0], "color": "#FC6255"}},
  {"action": "play", "animation": "create", "target": "c", "duration": 0.8},
  {"action": "wait", "duration": 0.5},
  {"action": "add", "type": "tex", "id": "eq", "content": "a^2 + b^2 = c^2", "props": {"color": "#FFFFFF", "position": [0, -3, 0]}},
  {"action": "play", "animation": "write", "target": "eq", "duration": 1.5}
]

TEACHING STYLE (like 3Blue1Brown):
- Build step by step: add one object, animate it, short wait, then next
- Start with a title, build the visual, reveal the equation last
- Use "create" for geometry (stroke draws on), "write" for text/equations
- Add 0.3–0.5s waits between steps for the student to follow
- Use color to distinguish concepts (blue=primary, green=secondary, red=emphasis)
- Transform shapes to show relationships
- Keep scenes focused: 4–8 objects max, spacious layout
- Each animation should tell part of the story""",
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

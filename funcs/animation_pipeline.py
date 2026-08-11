"""
Animation Pipeline — SDL tool for visual teaching.

The teach_with_visuals tool accepts a Scene Description Language (SDL)
payload from the LLM. The frontend's scene-kit compiler handles layout
and rendering — the backend only relays the SDL and extracts speech text.
"""

import logging
from typing import Dict, List

logger = logging.getLogger("animation_pipeline")


def teach_with_visuals(steps: List[Dict], session_id: str = "default") -> Dict:
    """
    SDL tool handler. Returns the SDL steps directly — the frontend's
    scene-kit compiler handles layout and rendering.
    """
    speech_cues = [s.get("say", "").strip() for s in steps if s.get("say", "").strip()]

    return {
        "success": True,
        "sdl": {"steps": steps},
        "speech_text": " ".join(speech_cues),
    }


TEACH_WITH_VISUALS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "teach_with_visuals",
        "description": """Create a synchronized teaching explanation with voice narration and visual components.

Each step has a 'say' (spoken narration) and optional visual directives.
The frontend automatically handles layout, sizing, and animation.

Components available:
- right_triangle: props { sides: ["a", "b", "c"] }
- equation: props { latex: "c^2 = a^2 + b^2" }
- coordinate_plane: props { x_range, y_range, points: [{x, y, label}], grid: true }
- function_plot: props { x_range, y_range, points: [{x, y}, ...], color: "#38bdf8", grid: true } — for curves like y=x^2. Provide 15-25 evenly spaced points.
- number_line: props { min, max, marks: [3, 7], highlight: 5 }
- bar_chart: props { data: [{label: "A", value: 10}], title: "Scores" }
- flowchart: props { nodes: [{id: "start", text: "Begin"}], edges: [["start", "process"]] }
- tree: props { root: "CEO", children: {"CEO": ["VP1", "VP2"]} }
- venn_diagram: props { sets: [{label: "Mammals"}, {label: "Pets"}], intersectionLabel: "Dogs" }
- circle_diagram: props { radius_label: "r", diameter_label: "d" }
- label: props { text: "Important!", fontSize: 20, color: "#ef4444" }

Position hints (optional): "center", "top", "bottom", { below: "id" }, { rightOf: "id" }
""",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered steps, each with narration and optional visuals",
                    "items": {
                        "type": "object",
                        "properties": {
                            "say": {
                                "type": "string",
                                "description": "Spoken narration for this step",
                            },
                            "show": {
                                "type": "object",
                                "description": "Component to render",
                                "properties": {
                                    "component": {
                                        "type": "string",
                                        "enum": [
                                            "right_triangle",
                                            "equation",
                                            "coordinate_plane",
                                            "function_plot",
                                            "number_line",
                                            "bar_chart",
                                            "flowchart",
                                            "tree",
                                            "venn_diagram",
                                            "circle_diagram",
                                            "label",
                                        ],
                                    },
                                    "props": {"type": "object"},
                                    "position": {
                                        "description": "Position hint: 'center', 'top', 'bottom', {below: 'id'}, {rightOf: 'id'}"
                                    },
                                    "id": {
                                        "type": "string",
                                        "description": "ID for referencing this component later",
                                    },
                                },
                                "required": ["component", "props"],
                            },
                            "highlight": {"description": "Element ID(s) to highlight"},
                            "clear": {
                                "type": "boolean",
                                "description": "Clear canvas before this step",
                            },
                        },
                        "required": ["say"],
                    },
                }
            },
            "required": ["steps"],
        },
    },
}

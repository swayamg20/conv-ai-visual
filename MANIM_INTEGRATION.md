# Manim Integration into VoiceAI

## Overview

Replace the Excalidraw canvas with Manim-powered animated visualizations for an AI teacher that explains topics with 3b1b-style visuals.

**Source of Manim web export module:** `/Users/swayam.gupta/Documents/GitHub/manim` (branch: `feature/web-export`)

## Architecture Change

```
CURRENT:  LLM → canvas_update tool → Excalidraw operations → DataChannel → Excalidraw renders
NEW:      LLM → manim_animate tool → ManimService → compact commands → DataChannel → ManimStreamPlayer
```

---

## STEP 1: Install Manim Fork as Dependency

**File:** `requirements.txt`

Add this line at the end:
```
manimgl @ git+https://github.com/swayamgupta/manim.git@feature/web-export
```

> NOTE: If the repo is not pushed yet, use a local editable install instead. In that case add this line:
> ```
> -e /Users/swayam.gupta/Documents/GitHub/manim
> ```

---

## STEP 2: Create `funcs/manim_bridge.py` (NEW FILE)

This bridges the ManimService with the existing tool calling system. Create this file:

```python
"""
Bridge between LLM tool calls and Manim web export.

Converts LLM-generated manim_animate instructions into
compact commands for the ManimStreamPlayer on the frontend.
"""

import json
import logging
from typing import Dict, List, Any, Optional, Callable, Awaitable

from manimlib.web.api import ManimService

logger = logging.getLogger("manim_bridge")

# Singleton service (reused across requests)
_manim_service = ManimService(width=800, height=450, background_color="#1a1a2e")


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
        logger.error(f"Failed to parse instructions_json: {e}")
        return {"success": False, "error": f"Invalid JSON: {e}"}

    if not instructions:
        return {"success": False, "error": "No instructions provided"}

    try:
        commands = _manim_service.execute(instructions)
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
        return

    for cmd in _manim_service.execute_streaming(instructions):
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
```

---

## STEP 3: Modify `funcs/config.py`

Replace the `LLM_CANVAS_SYSTEM_PROMPT` with a Manim-oriented teaching prompt.

**Find this block** (around line 64-103):
```python
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """You are a senior systems architect teaching through voice and visual diagrams.
...
NEVER include canvas_update(), Rectangle(), or any code in your response text."""
    )
```

**Replace the entire default string with:**
```python
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """You are an expert math and science teacher who explains concepts using animated visuals, like 3Blue1Brown.

You have a manim_animate tool available. Use it to create animated diagrams - DO NOT write code or show the tool call in your response.

RULES:
1. Use the manim_animate tool to draw animated diagrams (the user sees the animation, not the tool call)
2. Your text response should be brief and conversational (2-4 sentences)
3. Don't describe what you're drawing in detail - the animation speaks for itself
4. Never output code, function calls, or technical syntax in your spoken response
5. Build visuals step by step - add objects one at a time with animations between them

TEACHING STYLE:
- Start with the simplest visual, then build complexity
- Use "create" animation to draw shapes (the stroke appears progressively)
- Use "write" animation for equations
- Add brief pauses (0.3-0.5s) between steps so the student can follow
- Transform shapes to show relationships (e.g., circle → square)
- Use color to distinguish concepts: blue for primary, green for secondary, red for emphasis

YOUR RESPONSE should sound like a teacher narrating:
"Let me show you. See how the circle's area relates to pi times the radius squared..."

NEVER include manim_animate(), instructions, JSON, or any code in your response text."""
    )
```

---

## STEP 4: No changes needed to `funcs/canvas.py`

The existing canvas system stays intact. The Manim tool is imported directly in `llm_pipeline.py` (Step 5). The canvas_update tool still works as a fallback.

---

## STEP 5: Modify `funcs/llm_pipeline.py` - Wire in Manim tool

There are 5 precise changes to make in this file:

### 5a. Add import (line 8)

**Find:**
```python
from funcs.canvas import get_canvas_state, CANVAS_TOOL_SCHEMA
```

**Replace with:**
```python
from funcs.canvas import get_canvas_state, CANVAS_TOOL_SCHEMA
from funcs.manim_bridge import MANIM_TOOL_SCHEMA, manim_animate
```

### 5b. Add manim patterns to the tool syntax cleaner (line 15-19)

**Find:**
```python
TOOL_CALL_PATTERN = re.compile(
    r'canvas_update\s*\([^)]*\{[\s\S]*?\}\s*\)|'  # canvas_update({...})
    r'canvas_update\s*\(\s*\[[\s\S]*?\]\s*\)|'     # canvas_update([...])
    r'```[\s\S]*?canvas_update[\s\S]*?```|'        # code blocks with canvas_update
    r'\{"action":\s*"rect"[\s\S]*?\}',              # raw JSON operations
    re.MULTILINE
)
```

**Replace with:**
```python
TOOL_CALL_PATTERN = re.compile(
    r'canvas_update\s*\([^)]*\{[\s\S]*?\}\s*\)|'  # canvas_update({...})
    r'canvas_update\s*\(\s*\[[\s\S]*?\]\s*\)|'     # canvas_update([...])
    r'```[\s\S]*?canvas_update[\s\S]*?```|'        # code blocks with canvas_update
    r'manim_animate\s*\([^)]*\{[\s\S]*?\}\s*\)|'   # manim_animate({...})
    r'manim_animate\s*\(\s*\[[\s\S]*?\]\s*\)|'     # manim_animate([...])
    r'```[\s\S]*?manim_animate[\s\S]*?```|'        # code blocks with manim_animate
    r'\{"action":\s*"rect"[\s\S]*?\}|'              # raw JSON canvas operations
    r'\{"action":\s*"add"[\s\S]*?\}',               # raw JSON manim operations
    re.MULTILINE
)
```

### 5c. Add MANIM_TOOL_SCHEMA to get_tools_schema (around line 448-454)

**Find:**
```python
        if should_include_canvas:
            # Check if already in tools
            has_canvas = any(t.get("function", {}).get("name") == "canvas_update" for t in tools)
            if not has_canvas:
                tools.append(CANVAS_TOOL_SCHEMA)

        return tools
```

**Replace with:**
```python
        if should_include_canvas:
            # Add manim tool (preferred for animated visualizations)
            has_manim = any(t.get("function", {}).get("name") == "manim_animate" for t in tools)
            if not has_manim:
                tools.append(MANIM_TOOL_SCHEMA)

        return tools
```

> NOTE: This replaces `canvas_update` with `manim_animate` when canvas mode is on. If you want BOTH tools available, append both schemas instead.

### 5d. Add manim_animate handler in chat_with_tools (around line 521-534)

**Find:**
```python
                # Special handling for canvas_update - inject session_id and broadcast
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations, session_id=self.session_id)

                    # Broadcast to client via callback
                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            # Sync callback
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )
                else:
```

**Replace with:**
```python
                # Special handling for canvas_update - inject session_id and broadcast
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations, session_id=self.session_id)

                    # Broadcast to client via callback
                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )

                # Special handling for manim_animate - generate and broadcast commands
                elif tc.name == "manim_animate":
                    from funcs.tools import ToolResult
                    instructions_json = tc.arguments.get("instructions_json", "[]")
                    result = manim_animate(instructions_json, session_id=self.session_id)

                    if self.canvas_callback and result.get("commands"):
                        try:
                            await self.canvas_callback(result["commands"], "manim")
                        except TypeError:
                            self.canvas_callback(result["commands"], "manim")

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=f"Animation created with {result.get('command_count', 0)} commands" if result.get("success") else result.get("error", "Failed"),
                        success=result.get("success", False)
                    )
                else:
```

### 5e. Add manim_animate handler in chat_with_tools_stream (around line 607-620)

**Find:**
```python
            for tc in tool_calls:
                # Special handling for canvas_update
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    # Support both old format (operations) and new format (operations_json)
                    operations_json = tc.arguments.get("operations_json")
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations_json=operations_json, operations=operations, session_id=self.session_id)

                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )
                else:
                    tool_result = await self.tool_executor.execute_tool_call(tc)
```

**Replace with:**
```python
            for tc in tool_calls:
                # Special handling for canvas_update
                if tc.name == "canvas_update":
                    from funcs.canvas import canvas_update
                    from funcs.tools import ToolResult
                    operations_json = tc.arguments.get("operations_json")
                    operations = tc.arguments.get("operations", [])
                    result = canvas_update(operations_json=operations_json, operations=operations, session_id=self.session_id)

                    if self.canvas_callback and result.get("operations"):
                        try:
                            await self.canvas_callback(result["operations"])
                        except TypeError:
                            self.canvas_callback(result["operations"])

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=result.get("canvas_summary", "Canvas updated"),
                        success=True
                    )

                elif tc.name == "manim_animate":
                    from funcs.tools import ToolResult
                    instructions_json = tc.arguments.get("instructions_json", "[]")
                    result = manim_animate(instructions_json, session_id=self.session_id)

                    if self.canvas_callback and result.get("commands"):
                        try:
                            await self.canvas_callback(result["commands"], "manim")
                        except TypeError:
                            self.canvas_callback(result["commands"], "manim")

                    tool_result = ToolResult(
                        tool_call_id=tc.id,
                        content=f"Animation created with {result.get('command_count', 0)} commands" if result.get("success") else result.get("error", "Failed"),
                        success=result.get("success", False)
                    )
                else:
                    tool_result = await self.tool_executor.execute_tool_call(tc)
```

---

## STEP 6: Modify `main.py` - Send Manim commands over DataChannel

### Voice mode (around line 248)

**Find the canvas_broadcast function:**
```python
async def canvas_broadcast(operations):
    ch = datachannels.get(pc_id)
    if ch and ch.readyState == "open":
        ch.send(json.dumps({
            "type": "canvas_update",
            "operations": operations
        }))
```

**Replace with** (or add alongside, depending on whether you keep Excalidraw support):
```python
async def canvas_broadcast(data, tool_type="canvas"):
    ch = datachannels.get(pc_id)
    if ch and ch.readyState == "open":
        if tool_type == "manim":
            # Send each Manim command individually for streaming
            for cmd in data:
                ch.send(json.dumps({
                    "type": "manim_command",
                    "command": cmd
                }))
        else:
            ch.send(json.dumps({
                "type": "canvas_update",
                "operations": data
            }))
```

### Chat mode (around line 654)

**Find the canvas_callback in the /chat endpoint:**
```python
def canvas_callback(operations):
    canvas_events.append(operations)
```

**Replace with:**
```python
def canvas_callback(data, tool_type="canvas"):
    canvas_events.append({"data": data, "tool_type": tool_type})
```

**And update the SSE streaming section** (around line 670-679) to handle both types:
```python
while canvas_events:
    event = canvas_events.pop(0)
    if event.get("tool_type") == "manim":
        for cmd in event["data"]:
            yield f"data: {json.dumps({'type': 'manim_command', 'command': cmd})}\n\n"
    else:
        yield f"data: {json.dumps({'type': 'canvas_update', 'operations': event['data']})}\n\n"
```

---

## STEP 7: Frontend - Install manim-react

Copy the React package from the manim repo:

```bash
cp -r /Users/swayam.gupta/Documents/GitHub/manim/packages/manim-react /Users/swayam.gupta/Documents/GitHub/voiceai/web/src/components/manim-react
```

Or if you want it as a proper package, copy it to `web/` and install:
```bash
cp -r /Users/swayam.gupta/Documents/GitHub/manim/packages/manim-react /Users/swayam.gupta/Documents/GitHub/voiceai/web/packages/manim-react
cd /Users/swayam.gupta/Documents/GitHub/voiceai/web
npm install ./packages/manim-react
```

**Simplest approach:** Just copy the source files directly into the project. The key files needed are:
- `ManimStreamPlayer.tsx` → copy to `web/src/components/manim-stream-player.tsx`

---

## STEP 8: Create `web/src/components/manim-canvas.tsx` (NEW FILE)

This wraps `ManimStreamPlayer` with the same interface as `ExcalidrawCanvas`:

```tsx
"use client";

import React, { useRef, useImperativeHandle, forwardRef, useCallback, useState, useEffect } from "react";

// ---- Inline types & player (from manim-react package) ----

interface CompactStyle {
  f?: string;
  fo?: number;
  s?: string;
  sw?: number;
  so?: number;
}

type ManimCommand =
  | { cmd: "init"; w: number; h: number; bg: string }
  | { cmd: "add"; id: string; d?: string; s?: CompactStyle; children?: Array<{ d: string; s: CompactStyle }> }
  | { cmd: "anim"; id: string; type: "create" | "fadeIn" | "fadeOut" | "transform" | "morph"; dur: number; d?: string; s?: CompactStyle; from?: string; to?: string }
  | { cmd: "wait"; dur: number }
  | { cmd: "remove"; id: string }
  | { cmd: "clear" };

export interface ManimCanvasHandle {
  /** Process a single Manim command (from DataChannel) */
  processCommand: (command: ManimCommand) => void;
  /** Clear the canvas */
  clear: () => void;
}

interface ManimCanvasProps {
  width?: number;
  height?: number;
  className?: string;
  backgroundColor?: string;
}

export const ManimCanvas = forwardRef<ManimCanvasHandle, ManimCanvasProps>(
  ({ width = 800, height = 450, className, backgroundColor = "#1a1a2e" }, ref) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const elementsRef = useRef<Map<string, SVGElement>>(new Map());
    const commandQueueRef = useRef<ManimCommand[]>([]);
    const isProcessingRef = useRef(false);

    const applyStyle = useCallback((el: SVGElement, s: CompactStyle) => {
      if (s.f) el.setAttribute("fill", s.f);
      else el.setAttribute("fill", "none");
      if (s.fo !== undefined) el.setAttribute("fill-opacity", String(s.fo));
      if (s.s) el.setAttribute("stroke", s.s);
      if (s.sw !== undefined) el.setAttribute("stroke-width", String(s.sw));
      if (s.so !== undefined) el.setAttribute("stroke-opacity", String(s.so));
      el.setAttribute("stroke-linecap", "round");
      el.setAttribute("stroke-linejoin", "round");
    }, []);

    const createPathElement = useCallback((d: string, s: CompactStyle): SVGPathElement => {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      applyStyle(path, s);
      return path;
    }, [applyStyle]);

    const animateCreate = useCallback((el: SVGElement, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const length = path.getTotalLength?.() || 1000;
        path.style.strokeDasharray = String(length);
        path.style.strokeDashoffset = String(length);
        path.style.opacity = "1";
        const startTime = performance.now();
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          path.style.strokeDashoffset = String(length * (1 - eased));
          if (progress < 1) requestAnimationFrame(animate);
          else { path.style.strokeDasharray = ""; path.style.strokeDashoffset = ""; resolve(); }
        };
        requestAnimationFrame(animate);
      });
    }, []);

    const animateFade = useCallback((el: SVGElement, duration: number, fadeIn: boolean): Promise<void> => {
      return new Promise((resolve) => {
        const startTime = performance.now();
        const startOp = fadeIn ? 0 : 1;
        const endOp = fadeIn ? 1 : 0;
        el.style.opacity = String(startOp);
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          el.style.opacity = String(startOp + (endOp - startOp) * eased);
          if (progress < 1) requestAnimationFrame(animate);
          else resolve();
        };
        requestAnimationFrame(animate);
      });
    }, []);

    const animateTransform = useCallback((el: SVGElement, targetPath: string, targetStyle: CompactStyle | undefined, duration: number): Promise<void> => {
      return new Promise((resolve) => {
        const path = el as SVGPathElement;
        const startTime = performance.now();
        const startPath = path.getAttribute("d") || "";
        const animate = (timestamp: number) => {
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / (duration * 1000), 1);
          const eased = progress < 0.5 ? 2 * progress * progress : 1 - Math.pow(-2 * progress + 2, 2) / 2;
          if (eased < 0.5) {
            path.style.opacity = String(1 - eased);
          } else {
            if (path.getAttribute("d") === startPath) {
              path.setAttribute("d", targetPath);
              if (targetStyle) applyStyle(path, targetStyle);
            }
            path.style.opacity = String(eased);
          }
          if (progress < 1) requestAnimationFrame(animate);
          else { path.style.opacity = "1"; resolve(); }
        };
        requestAnimationFrame(animate);
      });
    }, [applyStyle]);

    const processCommand = useCallback(async (cmd: ManimCommand) => {
      const svg = svgRef.current;
      if (!svg) return;
      const contentGroup = svg.querySelector(".manim-content");
      if (!contentGroup) return;

      switch (cmd.cmd) {
        case "init":
          // Config already set via props
          break;

        case "add": {
          if (cmd.d && cmd.s) {
            const path = createPathElement(cmd.d, cmd.s);
            path.id = cmd.id;
            path.style.opacity = "0";
            contentGroup.appendChild(path);
            elementsRef.current.set(cmd.id, path);
          } else if (cmd.children) {
            const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
            group.id = cmd.id;
            group.style.opacity = "0";
            for (const child of cmd.children) {
              group.appendChild(createPathElement(child.d, child.s));
            }
            contentGroup.appendChild(group);
            elementsRef.current.set(cmd.id, group);
          }
          break;
        }

        case "anim": {
          const el = elementsRef.current.get(cmd.id);
          if (!el) break;
          if (el.style.opacity === "0" && cmd.type !== "fadeOut") el.style.opacity = "1";

          switch (cmd.type) {
            case "create":
              if (el.tagName === "g") {
                const paths = el.querySelectorAll("path");
                await Promise.all(Array.from(paths).map((p) => animateCreate(p, cmd.dur)));
              } else {
                await animateCreate(el, cmd.dur);
              }
              break;
            case "fadeIn":
              await animateFade(el, cmd.dur, true);
              break;
            case "fadeOut":
              await animateFade(el, cmd.dur, false);
              break;
            case "transform":
              if (cmd.d) await animateTransform(el, cmd.d, cmd.s, cmd.dur);
              break;
            case "morph":
              if (cmd.to) await animateTransform(el, cmd.to, cmd.s, cmd.dur);
              break;
          }
          break;
        }

        case "wait":
          await new Promise((r) => setTimeout(r, cmd.dur * 1000));
          break;

        case "remove": {
          const el = elementsRef.current.get(cmd.id);
          if (el) { el.remove(); elementsRef.current.delete(cmd.id); }
          break;
        }

        case "clear":
          if (contentGroup) contentGroup.innerHTML = "";
          elementsRef.current.clear();
          break;
      }
    }, [createPathElement, animateCreate, animateFade, animateTransform]);

    const processQueue = useCallback(async () => {
      if (isProcessingRef.current) return;
      isProcessingRef.current = true;
      while (commandQueueRef.current.length > 0) {
        const cmd = commandQueueRef.current.shift()!;
        await processCommand(cmd);
      }
      isProcessingRef.current = false;
    }, [processCommand]);

    useImperativeHandle(ref, () => ({
      processCommand: (command: ManimCommand) => {
        commandQueueRef.current.push(command);
        processQueue();
      },
      clear: () => {
        commandQueueRef.current = [];
        isProcessingRef.current = false;
        const svg = svgRef.current;
        if (svg) {
          const content = svg.querySelector(".manim-content");
          if (content) content.innerHTML = "";
        }
        elementsRef.current.clear();
      },
    }));

    return (
      <div className={className}>
        <svg
          ref={svgRef}
          xmlns="http://www.w3.org/2000/svg"
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: "100%", height: "100%", borderRadius: "12px" }}
        >
          <rect width="100%" height="100%" fill={backgroundColor} />
          <g className="manim-content" />
        </svg>
      </div>
    );
  }
);

ManimCanvas.displayName = "ManimCanvas";
```

---

## STEP 9: Modify `web/src/hooks/use-webrtc.ts` - Handle `manim_command` messages

**Find the DataChannel message handler** (the `switch (data.type)` block around line 235).

**Add a new case** after the `case "canvas_update":` block:

```typescript
case "manim_command":
  log(`Manim: ${data.command.cmd}`);
  onManimCommand?.(data.command);
  break;
```

**Add `onManimCommand` to the hook's options interface.** Find the `useWebRTC` function parameters and add:

```typescript
onManimCommand?: (command: any) => void;
```

---

## STEP 10: Modify `web/src/hooks/use-chat.ts` - Handle `manim_command` SSE events

**Find the event handling section** (where `data.type === "canvas_update"` is checked).

**Add:**
```typescript
if (data.type === "manim_command") {
  onManimCommand?.(data.command);
}
```

**Add `onManimCommand` to the hook options interface:**
```typescript
onManimCommand?: (command: any) => void;
```

---

## STEP 11: Modify `web/src/app/page.tsx` - Swap canvas component

### Import changes

**Replace:**
```typescript
import { ExcalidrawCanvas, type ExcalidrawCanvasHandle } from "@/components/excalidraw-canvas";
```

**With:**
```typescript
import { ManimCanvas, type ManimCanvasHandle } from "@/components/manim-canvas";
```

### Ref changes

**Replace:**
```typescript
const canvasRef = useRef<ExcalidrawCanvasHandle>(null);
```

**With:**
```typescript
const canvasRef = useRef<ManimCanvasHandle>(null);
```

### Callback changes

**Replace the `handleCanvasUpdate` callback:**
```typescript
const handleCanvasUpdate = useCallback((operations: CanvasOperation[]) => {
    canvasRef.current?.render(operations);
}, []);
```

**With:**
```typescript
const handleManimCommand = useCallback((command: any) => {
    canvasRef.current?.processCommand(command);
}, []);
```

### Hook wiring

**Add `onManimCommand` to the useWebRTC hook call:**
```typescript
const { ... } = useWebRTC({
    canvasMode,
    onTranscript: handleTranscript,
    onLLMResponse: handleLLMResponse,
    onCanvasUpdate: handleCanvasUpdate,  // keep for backwards compat if needed
    onManimCommand: handleManimCommand,  // ADD THIS
    onError: handleError,
    onLog: handleLog,
    onStateChange: handleStateChange,
});
```

**Add `onManimCommand` to the useChat hook call:**
```typescript
const { ... } = useChat({
    canvasMode,
    onCanvasUpdate: handleCanvasUpdate,  // keep for backwards compat
    onManimCommand: handleManimCommand,  // ADD THIS
});
```

### JSX changes

**Find the `<ExcalidrawCanvas>` component in the JSX and replace with:**
```tsx
<ManimCanvas
  ref={canvasRef}
  width={800}
  height={450}
  backgroundColor="#1a1a2e"
  className="w-full h-full"
/>
```

### Clean up canvas toolbar buttons

The Excalidraw-specific buttons (export to PNG, zoom to fit) can be simplified. Keep the clear button:
```tsx
const handleClearCanvas = useCallback(() => {
    canvasRef.current?.clear();
}, []);
```

Remove `handleExportCanvas` and `handleZoomToFit` if they reference Excalidraw-specific APIs.

---

## STEP 12: Remove Excalidraw dependency (OPTIONAL)

If you're fully replacing Excalidraw:

```bash
cd /Users/swayam.gupta/Documents/GitHub/voiceai/web
npm uninstall @excalidraw/excalidraw
```

And delete:
- `web/src/components/excalidraw-canvas.tsx`
- `web/src/types/excalidraw.ts` (if it exists)

---

## Summary of all files to modify

### Backend (Python)
| File | Action |
|------|--------|
| `requirements.txt` | Add manimgl dependency |
| `funcs/manim_bridge.py` | **CREATE** - Manim tool + schema |
| `funcs/config.py` | Update `LLM_CANVAS_SYSTEM_PROMPT` |
| `funcs/canvas.py` | Add import of manim tool (bottom of file) |
| `funcs/llm_pipeline.py` | Register `manim_animate` tool, handle its results |
| `main.py` | Update `canvas_broadcast` and SSE to handle manim commands |

### Frontend (Next.js)
| File | Action |
|------|--------|
| `web/src/components/manim-canvas.tsx` | **CREATE** - Manim SVG player component |
| `web/src/hooks/use-webrtc.ts` | Add `manim_command` case + `onManimCommand` prop |
| `web/src/hooks/use-chat.ts` | Add `manim_command` case + `onManimCommand` prop |
| `web/src/app/page.tsx` | Swap ExcalidrawCanvas → ManimCanvas, wire callbacks |
| `web/src/components/excalidraw-canvas.tsx` | **DELETE** (optional) |

---

## Testing

1. Start the backend: `uvicorn main:app --reload`
2. Start the frontend: `cd web && npm run dev`
3. Enable canvas mode in the UI
4. Ask: "Explain the Pythagorean theorem with a diagram"
5. The LLM should call `manim_animate` with instructions to draw a right triangle and show the equation

Expected DataChannel messages:
```json
{"type": "manim_command", "command": {"cmd": "init", "w": 800, "h": 450, "bg": "#1a1a2e"}}
{"type": "manim_command", "command": {"cmd": "add", "id": "m1", "d": "M ...", "s": {"s": "#3b82f6", "sw": 2.25}}}
{"type": "manim_command", "command": {"cmd": "anim", "id": "m1", "type": "create", "dur": 1.0}}
```

Each command is 50-1200 bytes, well within DataChannel limits.

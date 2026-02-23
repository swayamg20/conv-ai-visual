# Voice AI — Architecture v2

The next evolution of Voice AI. This document is the single source of truth for what we're building, why, and how.

---

## The Problem With v1

The LLM is doing too much in one call. It simultaneously decides *what to teach*, *what to draw*, *where to draw it* (exact pixel coordinates), *how to animate it*, and *what to say*. Consequences:

- Hundreds of tokens just for coordinates = **latency**
- Coordinates are often wrong = grid-snapping workarounds
- Voice and visuals are disconnected = TTS starts only after ALL tool calls finish
- Fixed 800x600 grid = LLM must memorize `col1=80, col2=240...`

This is like asking a teacher to lecture while calculating exact pixel positions for every stroke on the whiteboard. No teacher does that.

---

## The Core Architecture: Separate WHAT from HOW

Three layers, each doing one thing well:

```
Layer 1: LLM (Semantic Intent)
   "explain pythagorean theorem with a right triangle"
         | lightweight structured output (~80 tokens)
         v
Layer 2: Scene Compiler (Layout + Timing)  <-- THE MOAT
   deterministic, <5ms, runs client-side
         | concrete render commands
         v
Layer 3: Renderer (Rough.js + GSAP)
   pixels on screen, synchronized with voice
```

---

## Layer 1: Scene Description Language (SDL)

### What the LLM generates today (~400 tokens)

```json
{
  "tool": "create_teaching_sequence",
  "arguments": {
    "steps": [
      {
        "action": "rect", "x": 80, "y": 120, "width": 200, "height": 150,
        "color": "#4ECDC4", "speech_cue": "Let's draw a right triangle"
      },
      {
        "action": "line", "x": 80, "y": 270, "x2": 280, "y2": 270,
        "color": "#FFE66D", "speech_cue": "This is side a"
      }
    ]
  }
}
```

### What the LLM generates in v2 (~80 tokens)

```json
{
  "steps": [
    {
      "say": "Let's look at a right triangle",
      "show": { "component": "right_triangle", "props": { "sides": ["a", "b", "c"] } }
    },
    {
      "say": "The square of the hypotenuse",
      "highlight": "c",
      "show": { "component": "equation", "props": { "latex": "c^2 = a^2 + b^2" } }
    },
    {
      "say": "equals the sum of the other two sides",
      "highlight": ["a", "b"]
    }
  ]
}
```

### Why this matters

| Metric | v1 | v2 |
|--------|----|----|
| Output tokens per scene | ~400 | ~80 |
| LLM generation time | 1-2s | 200-400ms |
| Coordinate errors | frequent | impossible (no coordinates) |
| Voice-visual sync | batch (all-or-nothing) | per-step (true sync) |
| Screen-size dependency | hardcoded 800x600 | responsive (client computes) |

---

## Layer 2: Scene Compiler

A deterministic, client-side layout engine. Not AI — well-engineered code.

```
Input: { component: "right_triangle", props: { sides: ["a", "b", "c"] } }
                    |
                    v
Component Library lookup -> "right_triangle" template
                    |
                    v
Layout Engine (constraint-based positioning)
  - viewport-aware (responsive)
  - auto-spacing, collision avoidance
  - relative positioning ("equation below triangle, centered")
                    |
                    v
Output: Concrete Rough.js draw commands + GSAP animation timeline
```

### Component Library

Start with these 15 components. Each one unlocks a category of explanations.

**Geometry & Shapes**

| Component | Renders | Built-in Animations |
|-----------|---------|-------------------|
| `right_triangle` | Triangle with labeled sides, right-angle marker | Stroke draw-on, side highlight, angle mark |
| `circle_diagram` | Circle with radius/diameter/chord labels | Stroke draw-on, arc highlight |
| `coordinate_plane` | X/Y axes with gridlines, origin label | Draw axes, fade grid, plot points |
| `polygon` | Any n-sided polygon with labeled vertices | Sequential edge draw-on |

**Math & Equations**

| Component | Renders | Built-in Animations |
|-----------|---------|-------------------|
| `equation` | LaTeX via KaTeX, handwritten font | Fade-in, term-by-term reveal, highlight terms |
| `number_line` | Horizontal line with tick marks and labels | Draw line, place markers, slide indicator |
| `matrix` | m x n grid with values | Cell-by-cell reveal, row/column highlight |
| `fraction_bar` | Visual fraction with numerator/denominator | Split animation, simplification |

**Data & Visualization**

| Component | Renders | Built-in Animations |
|-----------|---------|-------------------|
| `bar_chart` | Vertical/horizontal bars with labels | Grow-up/grow-right per bar |
| `line_graph` | Points connected by line on coordinate plane | Progressive path draw |
| `pie_chart` | Segments with labels and percentages | Sweep-in per segment |
| `function_plot` | f(x) plotted continuously | Progressive curve draw |

**Structure & Flow**

| Component | Renders | Built-in Animations |
|-----------|---------|-------------------|
| `flowchart` | Connected boxes with arrows | Sequential box draw + arrow draw |
| `tree` | Hierarchical nodes with edges | Top-down level reveal |
| `venn_diagram` | 2-3 overlapping circles with labels | Draw circles, fade labels, highlight intersection |

**Helpers**

| Component | Renders | Built-in Animations |
|-----------|---------|-------------------|
| `label` | Styled text at a position | Fade-in, typewriter |
| `arrow` | Curved or straight arrow between elements | Draw-on with head |
| `highlight_box` | Semi-transparent overlay on an area | Pulse, fade |
| `divider` | Horizontal/vertical separator | Draw-on |

### Layout Engine

Principles:
- **No absolute coordinates from the LLM.** Ever. The LLM says "show equation below triangle" and the compiler figures out where.
- **Relative positioning.** Components placed relative to the viewport or to other components: `below`, `right-of`, `centered`, `grid(2x2)`.
- **Auto-sizing.** Components calculate their own bounding box. The layout engine handles spacing.
- **Collision avoidance.** If two components overlap, nudge the later one.

The layout engine is a simple constraint solver, not a CSS engine. ~300 lines of TypeScript.

### Location: `web/src/lib/scene-kit/`

```
scene-kit/
  index.ts              # public API
  compiler.ts           # SDL -> render commands
  layout.ts             # constraint-based positioning
  components/
    index.ts            # component registry
    right-triangle.ts
    equation.ts
    coordinate-plane.ts
    bar-chart.ts
    flowchart.ts
    ...
  renderer/
    rough-renderer.ts   # Rough.js shape drawing
    gsap-animator.ts    # GSAP timeline builder
    latex-renderer.ts   # KaTeX integration
  types.ts              # SDL types, component interfaces
```

Clean boundary: the only export is `compileScene(sdl, viewport) -> RenderPlan`. This makes future npm extraction trivial — just move the directory and publish.

---

## Layer 3: Voice-Visual Synchronization

### The current flow (batch)

```
LLM generates ALL steps
  -> extract ALL speech cues
  -> concatenate into one TTS request
  -> play audio + render all visuals
```

Voice and visuals are decoupled. The user hears everything while seeing everything. No synchronization.

### The v2 flow (step-pipelined)

```
LLM streams step 1:
  -> TTS("Let's look at a right triangle")  --parallel-->  render(right_triangle)
  -> audio plays                             --sync-->      stroke animation plays

LLM streams step 2:
  -> TTS("The square of the hypotenuse")    --parallel-->  highlight(c) + render(equation)
  -> audio plays                             --sync-->      animations play

...
```

Each step has a `say` and a visual action. They start together. The canvas animation duration matches the TTS audio duration for that phrase. This creates the illusion of a person drawing while talking.

### Implementation

1. Stream SDL steps as they arrive from the LLM (partial JSON parsing).
2. For each step, fire TTS and compile+render in parallel.
3. TTS audio chunk arrives -> start canvas animation for that step.
4. When audio for step N finishes, step N+1 begins (if TTS is ready).
5. If TTS for step N+1 isn't ready yet, hold a brief pause (better than choppy overlap).

---

## Latency Architecture

### Target: <1s for simple queries, <2s for visual explanations

### The latency stack

```
Current e2e: ~2000-3000ms
  STT finalization:     ~500ms (Deepgram endpointing)
  Turn detection:       ~60ms  (Smart Turn ONNX)
  LLM first token:      ~500ms (OpenAI gpt-4o-mini)
  LLM full generation:  ~1000ms (400 tokens for coordinates)
  TTS first chunk:      ~250ms (ElevenLabs API)

Target e2e: ~700-1200ms
  STT finalization:     ~300ms (tune endpointing to 700ms)
  Turn detection:       ~60ms  (unchanged, already fast)
  LLM first token:      ~80ms  (Groq as default fast provider)
  LLM full generation:  ~200ms (80 tokens SDL, not 400)
  TTS first chunk:      ~60ms  (Kokoro local TTS)
```

### Model routing: fast by default

Not every query needs GPT-4o. The default path uses the fastest available model. Escalate only when needed.

```
User speaks
  |
  v
Fast model (Groq llama-3.3-70b, ~80ms TTFT)
  |
  +--> Simple response? -> done
  |
  +--> Needs deeper reasoning? -> escalate to GPT-4o-mini (~500ms TTFT)
  |
  +--> Complex visual explanation? -> GPT-4o (~800ms TTFT)
```

Escalation signal: the fast model can output a `"needs_escalation": true` flag, or we detect it heuristically (e.g., math/science topics, multi-step explanations).

Simpler v1 of routing: keyword/intent classifier (regex + embeddings). No ML model needed.

### TTS strategy: Kokoro local, ElevenLabs optional

| Provider | TTFB | Quality | Use when |
|----------|------|---------|----------|
| Kokoro (local) | ~50ms | Good, natural | Default — every response |
| ElevenLabs | ~250ms | Excellent, expressive | User opts into "quality mode" |

Kokoro runs locally via ONNX, same as Smart Turn. No network hop. This alone saves ~200ms per response.

### Speculative scene pre-loading

For common educational topics, pre-compile the visual scene:

```python
SCENE_CACHE = {
    "pythagorean_theorem": PrecompiledScene(...),
    "quadratic_formula": PrecompiledScene(...),
    "linear_equations": PrecompiledScene(...),
    ...
}
```

When STT transcript partially matches a known topic, load the scene speculatively. If the LLM confirms, rendering is instant. If not, discard. Cost of wrong speculation: zero.

---

## Hand-Drawn Visual Quality

### What we keep
- **Rough.js** for shapes — the hand-drawn aesthetic is core to the product identity
- **GSAP** for animations — industry standard, no reason to replace

### What we improve

**1. Progressive stroke reveal (draw-on effect)**

Every shape should appear as if drawn by a hand, not pop into existence. Use `animateProgressivePath()` (already in `gsap-setup.ts`) as the default for all shapes. Stroke dasharray + dashoffset animation gives the "watching someone draw" effect.

**2. Handwritten font for text and equations**

Use **Virgil** (Excalidraw's font) or **Caveat** for all canvas text. KaTeX supports custom fonts — render equations in a handwritten style. This makes the whole canvas feel cohesive.

**3. Pressure-sensitive strokes**

Use **perfect-freehand** (the library behind tldraw) for paths, arrows, and underlines. It takes point arrays and produces beautiful variable-width strokes. Small detail, high impact.

**4. Smooth scene transitions**

Don't hard-clear the canvas between explanations. Fade out old elements, slide in new ones. The canvas should feel like a continuous workspace, not a slideshow.

---

## What We Do NOT Build

Equally important as what we build:

1. **No custom SLM for layout.** A rule-based constraint engine is faster, deterministic, and debuggable. ML for layout is over-engineering.
2. **No custom STT.** Deepgram is excellent. Months of work to match their quality.
3. **No general-purpose canvas.** We're not building Figma. 20 well-crafted educational components beat infinite flexibility.
4. **No universal LLM abstraction.** OpenAI + Gemini + Groq is enough. Don't abstract for 10 providers.
5. **No npm package yet.** Start as `web/src/lib/scene-kit/`. Extract when the API stabilizes (~4 weeks).

---

## Implementation Phases

### Phase 1: SDL + Scene Compiler (1-2 weeks)

The highest-leverage change. Simultaneously reduces latency, improves quality, enables sync.

**Deliverables:**
- SDL type definitions (`types.ts`)
- Scene compiler (`compiler.ts`)
- Layout engine (`layout.ts`)
- 8-10 core components (right_triangle, equation, coordinate_plane, number_line, bar_chart, flowchart, tree, venn_diagram)
- Rough.js renderer integration
- GSAP timeline builder per component
- Updated LLM system prompt (generate SDL, not coordinates)
- Step-pipelined rendering in `use-chat.ts` and `use-webrtc.ts`

**Verification:**
- LLM generates valid SDL for 10 test prompts
- Components render correctly at different viewport sizes
- Animation timelines play smoothly
- Backend import check: `python -c "from main import app"`
- Frontend type check: `cd web && npx tsc --noEmit`

### Phase 2: Latency Optimization (1 week)

**Deliverables:**
- Groq provider integration (OpenAI-compatible, minimal code)
- Model routing: fast model default, escalation to capable model
- Kokoro TTS integration (local ONNX inference)
- Deepgram endpointing tuned to 700ms
- Enable `LLM_STREAM_TOOL_ORCHESTRATION`
- Enable `LLM_PARALLEL_TOOLS`
- Step-pipelined TTS (per-step, not per-response)

**Verification:**
- Measure e2e latency for 10 test queries
- Target: <1s for simple, <2s for visual
- No regression in response quality

### Phase 3: Visual Polish (1 week)

**Deliverables:**
- Progressive stroke reveal as default animation for all shapes
- Virgil/Caveat handwritten font integration
- perfect-freehand for paths and arrows
- Smooth scene transitions (fade out old, slide in new)
- Ink-like stroke width variation on paths

**Verification:**
- Visual comparison: v1 vs v2 screenshots for 5 test scenes
- Animation smoothness at 60fps
- No layout jank or flickering

### Phase 4: Hardening (1 week)

**Deliverables:**
- Remove all dead code from v1 canvas system (old coordinate-based tool schemas, hardcoded grid positions)
- Clean up unused config flags
- Integration tests for SDL -> render pipeline
- Error boundaries: malformed SDL gracefully degrades
- Scene cache for common topics

**Verification:**
- Full project audit: no unused imports, no dead code paths
- `python -c "from main import app"` clean
- `cd web && npx tsc --noEmit` clean
- All tests pass

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| E2E latency (simple query) | ~2000ms | <1000ms |
| E2E latency (visual explanation) | ~3000ms | <2000ms |
| LLM output tokens per scene | ~400 | <100 |
| Voice-visual sync | batch | per-step |
| Canvas viewport | fixed 800x600 | responsive |
| Visual components available | raw primitives | 15+ pre-built |
| Hand-drawn quality | basic Rough.js | Rough.js + progressive stroke + handwritten font |

---

## The Moat

Anyone can plug an LLM into a canvas. The moat is the **Scene Compiler + Component Library** — a rendering engine that turns 80 tokens of semantic intent into a beautiful, synchronized, hand-drawn educational explanation in under a second.

The LLM is commoditized. The rendering pipeline is not.

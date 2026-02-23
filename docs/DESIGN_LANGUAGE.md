# Murmur — Design Language

A voice becomes a drawing. A thought becomes a diagram. This is the design language for a product where AI thinks visually, in real time, on a shared surface.

---

## Philosophy

**"Drawn, not placed."**

Everything in Murmur should feel like it was sketched into existence — not dropped from a template. The interface is a blackboard, not a dashboard. Elements arrive like strokes, not like cards loading. The imperfection is deliberate: wobbly lines carry more warmth than pixel-perfect vectors.

Three principles guide every design decision:

1. **The surface is alive.** Nothing sits still unless it's done being thought about. Elements enter, breathe, and settle. The UI should feel like you walked in on someone mid-thought.

2. **Darkness is the canvas.** The background is not "dark mode" — it's the blackboard. Everything drawn on it should feel like chalk, light, luminous. The dark isn't absence of color — it's the material the work is made on.

3. **Voice is invisible, its traces are not.** You never see the voice. But you see what it leaves behind: shapes appearing, labels fading in, arrows connecting ideas. The interface is the residue of a conversation.

---

## Color

### The Blackboard

| Token | Value | Usage |
|---|---|---|
| `--void` | `#08080C` | Page background. The deepest layer. |
| `--slate` | `#111116` | Elevated surfaces. Cards, panels, drawers. |
| `--graphite` | `#1A1A21` | Interactive surface hover states. Subtle lift. |

Not pure black. There's a faint blue undertone — like a well-used blackboard that's been written on and erased a thousand times. It has memory in it.

### Chalk

| Token | Value | Usage |
|---|---|---|
| `--chalk` | `#E8E4DC` | Primary text. The default "ink." Warm off-white. |
| `--chalk-soft` | `#9B9790` | Secondary text. Labels, metadata, timestamps. |
| `--chalk-faint` | `#4A4843` | Tertiary. Borders, dividers, subtle structure. |

Never use pure white (`#FFFFFF`). Chalk is always warm — closer to parchment than snow. Pure white reads as digital. Chalk reads as human.

### Signal Colors

| Token | Value | Meaning |
|---|---|---|
| `--amber` | `#F5A623` | Warmth. Active thought. "Just drawn." The glow of a fresh stroke. |
| `--lavender` | `#8B7EC8` | AI presence. System states. The machine's color. |
| `--sage` | `#6BCB77` | Listening. The system is paying attention. Ears open. |
| `--ember` | `#EF4444` | Error. Interruption. Something broke. |

Amber is the hero accent. It's the color of a freshly chalked line catching the light. Use it for elements that just appeared, active states, and the voice orb when it's thinking. Lavender is cooler, more recessive — it's for system chrome, AI-generated labels, and metadata.

### Glow

Elements that are "alive" (recently drawn, currently speaking, mid-animation) get a soft ambient glow:

```css
/* Fresh stroke glow — amber */
box-shadow: 0 0 20px rgba(245, 166, 35, 0.15), 0 0 40px rgba(245, 166, 35, 0.05);

/* AI presence glow — lavender */
box-shadow: 0 0 24px rgba(139, 126, 200, 0.2), 0 0 48px rgba(139, 126, 200, 0.08);

/* Listening glow — sage */
box-shadow: 0 0 30px rgba(107, 203, 119, 0.25);
```

Glow is not decoration — it's information. It tells you something is active, warm, recent. It fades as elements "settle."

---

## Typography

Two typefaces. That's it.

### Monospace — The Voice of the System

Used for: labels, metadata, timestamps, the wordmark, canvas annotations, code, anything the system says about itself.

```css
font-family: 'JetBrains Mono', 'SF Mono', 'Fira Code', monospace;
```

Monospace says "this was typed, not spoken." It's the handwriting of the machine. Always lowercase for labels. Letter-spacing slightly wide (`0.02em–0.05em`).

### Sans — The Voice of Content

Used for: body text, chat messages, user-facing prose, explanations, headings.

```css
font-family: 'Inter', 'SF Pro', system-ui, sans-serif;
```

Clean, legible, invisible. The content typeface should never draw attention to itself. It's the glass you read through, not the thing you read.

### Scale

| Name | Size | Weight | Tracking | Use |
|---|---|---|---|---|
| `display` | 2.5rem | 500 | -0.01em | Hero moments. Page titles. Rare. |
| `title` | 1.25rem | 500 | -0.005em | Section headers. Card titles. |
| `body` | 0.9375rem | 400 | 0 | Default reading size. Chat messages. |
| `caption` | 0.8125rem | 400 | 0.01em | Secondary info. Timestamps. |
| `mono-label` | 0.6875rem | 500 | 0.05em | System labels. Tags. Canvas annotations. Monospace. |
| `mono-data` | 0.8125rem | 400 | 0.02em | Data values. Metrics. Code. Monospace. |

---

## Surfaces & Containers

### Glass Panels

The primary container. A frosted pane floating over the blackboard.

```css
background: rgba(255, 255, 255, 0.03);
backdrop-filter: blur(24px) saturate(150%);
border: 1px solid rgba(255, 255, 255, 0.06);
border-radius: 16px;
```

Glass panels are **quiet**. They don't compete with the canvas. Their job is to hold UI controls without covering the blackboard. Keep opacity as low as possible — you should almost see through them.

### Grain Texture

The blackboard has texture. Apply a subtle noise overlay to the body:

```css
body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.03;
  background-image: url('/grain.svg'); /* tiled noise pattern */
  z-index: 9999;
}
```

Grain is barely perceptible. It adds materiality. The screen should feel like a surface, not a void. If you consciously notice the grain, it's too strong.

### Depth Model

Three layers, not more:

1. **Blackboard** — `--void`. The canvas lives here. Most of the screen.
2. **Panels** — glass surfaces. Chat sidebar, control bar, drawers. Float above.
3. **Popovers** — tooltips, menus, modals. Highest z-index. Slightly more opaque glass.

No drop shadows between layers. Depth is communicated through blur and opacity, not shadow. Shadows are reserved for glow effects on active elements.

---

## The Canvas

The canvas is the product's soul. It takes up the majority of the screen and follows different rules than the UI chrome.

### Drawing Style

All shapes rendered via Rough.js with these defaults:

```js
{
  roughness: 1.2,        // hand-drawn wobble
  strokeWidth: 1.5,      // thin chalk lines
  stroke: '#E8E4DC',     // chalk color
  fill: 'none',          // outlines only, by default
  fillStyle: 'hachure',  // when filled, use cross-hatch
  fillWeight: 0.8,       // light hachure strokes
  bowing: 1,             // line curve randomness
}
```

Shapes on the canvas should look like a professor drew them while explaining. Not sloppy, but human. The wobble is confidence, not error.

### Canvas Color Palette

Chalk white is the default. But the canvas supports a limited teaching palette — like having 5 chalk colors in a tray:

| Color | Hex | Use |
|---|---|---|
| Chalk | `#E8E4DC` | Default. Outlines, text, structure. |
| Amber | `#F5A623` | Emphasis. Highlights. "Look here." |
| Blue | `#6C9CEB` | Secondary structures. Connections. Calm. |
| Sage | `#6BCB77` | Positive. Correct. Growth. |
| Rose | `#E87B7B` | Negative. Warning. Attention. |

Five colors max on canvas at any time. More than that becomes noise. Constraint is clarity.

### Canvas Labels

Labels on the canvas use monospace at `mono-label` size, in `--chalk-soft`. They appear via a typewriter fade — letters materialize left to right, as if being written.

---

## Components

### Voice Orb

The orb is the product's face. A glowing sphere that breathes, listens, and thinks.

| State | Orb Color | Glow | Behavior |
|---|---|---|---|
| Idle | `--graphite` gradient | None | Dashed border. Slow pulse. "Tap to connect." |
| Connecting | `--lavender` | Soft lavender | Rotating conic gradient border. |
| Listening | `--sage` | Sage glow | Waveform bars inside. Alive. |
| Thinking | `--amber` | Warm amber glow | Three dots pulse. The mind is working. |
| Speaking | `--lavender` → `--amber` gradient | Strong glow | Waveform bars. The voice is here. |
| Interrupted | `--amber` | Flashing | Brief flash, then settles to listening. |
| Error | `--ember` | Red glow | Mic-off icon. |

The orb should feel like a living thing. Not a button. It breathes (scale oscillation), it reacts (glow intensity tracks audio level), it settles (glow fades when idle). Transitions between states should take 300–400ms — fast enough to feel responsive, slow enough to feel organic.

### Buttons

```
┌──────────────────┐
│   label text     │    ← mono-label, uppercase tracking
└──────────────────┘
```

- **Primary**: `--amber` background, `--void` text. For the single most important action.
- **Glass**: transparent glass surface, `--chalk` text. For secondary actions. The default.
- **Ghost**: no background, `--chalk-soft` text. For tertiary actions. Hover reveals `--graphite` fill.

Border radius: `10px`. Not fully rounded (too bubbly), not sharp (too corporate). Just enough to feel approachable.

Active state: scale down to `0.97`. No color change needed — the physical response is enough.

### Cards

Cards on the canvas (product cards, info cards, etc.) use a combination of Rough.js borders and glass fills:

```
╭─ ─ ─ ─ ─ ─ ─ ─ ─╮
│                   │  ← rough.js sketched border
│  Title            │  ← sans, title size
│  description...   │  ← sans, body size, chalk-soft
│                   │
│  tag  tag  tag    │  ← mono-label, lavender
╰─ ─ ─ ─ ─ ─ ─ ─ ─╯
```

Background: `rgba(255, 255, 255, 0.02)`. Just enough to register as a surface. The Rough.js border does the heavy lifting.

### Input Fields

Chat input and text fields follow an underline pattern — no full border box:

```
  Type a message...
 ─────────────────────   ← 1px chalk-faint line
```

On focus, the underline transitions to `--amber` and the placeholder fades out. The simplicity says: "just talk." Don't over-decorate the place where words are born.

### Chat Bubbles

- **User messages**: aligned right, glass surface, subtle border. Sans text.
- **AI messages**: aligned left, no background (just text on the blackboard). Amber left-border accent (2px).

AI messages shouldn't feel like they're in containers. They should feel written directly on the blackboard.

---

## Motion

### Entrance: "Being Drawn"

The signature motion. Elements don't fade in or slide in — they are **drawn into existence**.

- **Lines and shapes** animate their stroke with `stroke-dashoffset` (SVG path drawing).
- **Text** appears via a typewriter effect — characters materialize left-to-right with slight opacity stagger.
- **Cards and panels** scale from `0.95` to `1.0` with opacity `0` to `1` over 300ms, with a slight blur clearing. Like something coming into focus.

```css
@keyframes draw-in {
  from { stroke-dashoffset: var(--path-length); opacity: 0.3; }
  to   { stroke-dashoffset: 0; opacity: 1; }
}

@keyframes type-in {
  from { opacity: 0; transform: translateX(-2px); }
  to   { opacity: 1; transform: translateX(0); }
}
```

### Settling: "Chalk Drying"

After appearing, elements "settle" — their glow fades from `--amber` to nothing over 2–3 seconds. This creates a natural recency signal: warm elements are new, cool elements are established. No extra UI needed to show "what just changed."

### Transitions

All state transitions use `cubic-bezier(0.16, 1, 0.3, 1)` — a fast start with a long, gentle ease-out. Things arrive with intention and settle slowly. Never use linear easing. Nothing in nature moves linearly.

Duration guide:
- Micro-interactions (button press, hover): **150ms**
- State changes (orb transitions, mode switches): **300ms**
- Canvas entrances (shapes being drawn): **400–800ms**
- Settling effects (glow fade): **2000–3000ms**

### What Never Moves

Some things are anchors. They don't animate, ever:
- The background
- The canvas grid (if visible)
- The wordmark
- Scrollbar chrome

Stillness is as important as motion. If everything moves, nothing communicates.

---

## Spacing & Layout

### Grid

8px base unit. All spacing is multiples of 8.

| Token | Value | Use |
|---|---|---|
| `xs` | 4px | Inside tight components. Icon gaps. |
| `sm` | 8px | Between related items. |
| `md` | 16px | Component internal padding. |
| `lg` | 24px | Between sections. Card padding. |
| `xl` | 32px | Major section gaps. |
| `2xl` | 48px | Page-level spacing. |

### Layout Zones

```
┌──────────────────────────────────────────────────────┐
│                                          [controls]  │
│                                                      │
│                                                      │
│              C A N V A S                             │
│           (80%+ of viewport)                         │
│                                                      │
│                                                      │
│  ┌──────────┐                      ┌──────────────┐  │
│  │  voice   │                      │    chat       │  │
│  │   orb    │                      │   panel       │  │
│  └──────────┘                      └──────────────┘  │
└──────────────────────────────────────────────────────┘
```

The canvas is the primary citizen. UI chrome hugs the edges and stays out of the way. Panels are collapsible. In voice-only mode, the orb floats center-bottom and the canvas takes everything else.

---

## Iconography

Use [Lucide](https://lucide.dev/) icons exclusively. Stroke width: `1.5px` (matches the Rough.js stroke weight). Size: `18px` default, `16px` in tight spaces.

Icons are always `--chalk-soft` by default, `--chalk` on hover/active. Never use filled icons — outlines only, consistent with the hand-drawn language.

---

## Sound Design (Future)

When the product has audio feedback beyond TTS:

- **Connection established**: a soft, low-pitched tone. Like a match striking.
- **Element drawn on canvas**: faint pencil-scratch texture. Barely audible.
- **Error**: a gentle double-tap. Not alarming. Informative.
- **Interruption acknowledged**: a quick descending note. "I heard you."

Sound should feel like it belongs in a quiet room. If it would be annoying in a library, it's too loud.

---

## What This Is Not

- **Not a dashboard.** No data-heavy tables, metric grids, or admin panels. If it feels like analytics software, something went wrong.
- **Not Material Design.** No FABs, no snackbars, no elevated cards with drop shadows. Too corporate.
- **Not skeuomorphic.** The blackboard metaphor is conceptual, not literal. No chalk dust particles, no wooden frame texture, no eraser tool icon.
- **Not monochrome.** The palette is restrained, but color is used with intention. Amber and lavender carry meaning.

---

*The best interface for a thinking tool is one that looks like thinking itself — messy, alive, mid-process, and warm.*

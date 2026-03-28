# Murmur — UI Funnel Design Spec

> This document is the single source of truth for how every screen in the Murmur funnel should look, feel, and behave. It is written as if orchestrating a Figma file from first principles — not describing what exists, but what *should* exist. Every pixel, every transition, every copy choice is intentional.

---

## Mental Model: The Funnel as a Journey

The user travels through five emotional phases:

```
CURIOUS → CONVINCED → COMMITTED → ORIENTED → IMMERSED
   ↓           ↓           ↓           ↓          ↓
Landing    Register     Wizard     Dashboard   Session
```

Each screen has one job. The design must not let that job bleed.

---

## Screen 0 — Marketing Nav (persistent, top of stack)

**Height:** 64px fixed.
**Background:** `rgba(8, 8, 12, 0.85)` + `backdrop-filter: blur(20px)`. Loses opacity on scroll past hero, gaining it back as user scrolls down — a breathing border.
**Border bottom:** `1px solid rgba(255,255,255,0.05)`.

### Layout (3-column)

```
[Logo + wordmark]        [nav links — optional]        [Sign in | Get Started →]
```

- **Logo:** MurmurLogoMark at 24px. Followed by `murmur` in `font-mono`, `0.9375rem`, `--chalk-soft`. All lowercase. No uppercase wordmark.
- **Nav links** (optional, only show on wider viewports `>1024px`): "How it works" · "Features". Ghost style, `--chalk-soft`, no underlines. Fade in/out gracefully on narrow screens — don't reflow, just disappear.
- **Sign in:** Ghost button, 36px height, `--chalk-soft` text. Hover: `--chalk`.
- **Get Started →:** Primary amber button, 36px height. Includes `ArrowRight` icon. On mobile, collapse "Sign in" to a ghost icon (User icon) and keep "Get Started".

**Scroll behavior:** Nav is always visible. It never hides. It is an anchor. When user is in `/session/`, nav is replaced entirely by session chrome.

---

## Screen 1 — Landing Page `/`

**Emotional job:** Create the "I've never seen this before" moment in 4 seconds.

### 1A — Hero Section

**Full viewport height.** No scrolling required to grasp the premise.

```
──────────────────────────────────────────────────────────
                  [nav: 64px]

        [subtle badge: ● Voice-first AI learning]


          Your AI tutor that draws
          while it talks.


     Speak naturally. Murmur listens, understands,
     and sketches diagrams, equations, and graphs on
     a live canvas — just like the best teacher you
     ever had.


         [Get Started →]     [▷ Watch Demo]


          ┌─────────────────────────────────┐
          │ ~~~waveform~~~  →  □◇△  (sketch)│
          └─────────────────────────────────┘


                        ↓ (scroll indicator)
──────────────────────────────────────────────────────────
```

**Background sketch layer (SVG, pointer-events: none):**
Faint hand-drawn waveform arcs at `opacity: 0.10`, a circle sketch `opacity: 0.07`, a triangle `opacity: 0.07`, and scattered `F=ma`, `∫f(x)dx` annotations at `opacity: 0.10`. These are barely perceived — they prime the brain for "this is a thinking product."

**Badge:**
Glass pill, `px-4 py-2`, `border-radius: 9999px`. A `6px` pulsing `--sage` dot on the left. Text: `font-mono`, `0.6875rem`, uppercase, `letter-spacing: 0.1em`, `--chalk-soft`. Copy: `Voice-first AI learning`.

**Headline:**
`font-size: 4rem` (desktop), `2.75rem` (tablet), `2.25rem` (mobile). Weight `700`. Tracking `-0.02em`. Line height `1.05`. Two-line max.
`draws` → `--amber`.
`talks` → `--lavender`.
The color contrast is the hook. Amber = warmth/output. Lavender = AI/system. The split communicates the whole product concept in one headline.

**Sub-headline:**
`1.125rem`, weight `400`, `--chalk-soft`, `max-width: 560px`, centered. `line-height: 1.65`. This is the explanation. It doesn't need to work very hard — the headline did the job.

**CTAs:**
Two. One primary, one ghost. Side by side (`gap: 16px`, `flex-wrap` on mobile → stack vertically).
- Primary: `bg-amber`, `text-void`, `px-8 py-4`, `border-radius: 12px`, `font-size: 1.125rem`, `font-weight: 600`. `box-shadow: 0 0 24px rgba(245,166,35,0.3)`. Hover: `brightness(1.1)`. Active: `scale(0.97)`.
- Ghost: Glass card style, same sizing. `▷ Watch Demo`. Scroll-anchors to `#demo`.

**Waveform → Sketch illustration:**
A horizontal SVG strip (~320px wide, ~80px tall). Left half: animated waveform bars (5 bars, varying heights, `--chalk-soft`, `opacity: 0.6`). Center: a thin `→` arrow, amber. Right half: a hand-drawn `△` triangle and a `○` circle, chalk, drawn via `stroke-dashoffset` animation. This is the product explained without words.

**Entrance animation sequence:**
```
0ms    badge fades up
120ms  headline fades up
240ms  sub-headline fades up
360ms  CTAs fade up
480ms  illustration fades up
600ms  background sketch draws in (1.4s duration)
```
All use `cubic-bezier(0.16, 1, 0.3, 1)`, `y: 30 → 0`.

---

### 1B — How It Works

**Padding:** `py-32 px-6`. Max-width `960px`, centered.

```
      how it works          ← mono, amber, uppercase, tracked
  Three steps to a smarter study session

  [speech bubble]    [gear/sparkle]    [canvas + waveform]
       1                  2                    3
  Describe yourself   AI builds agent    Learn with voice+canvas
  "I'm JEE Physics    Murmur creates     Ask a question out loud.
  struggling with     a personalized     Your agent speaks while
  rotational mech."   tutor tailored     drawing diagrams in
                      to you.            real time.
```

**Step numbers:** `32px` circle, `border: 1px solid {accent}/30`, text in `{accent}` color, `font-mono`. Each step gets its accent: `--amber` → `--lavender` → `--sage`. This creates gentle color progression that mirrors the user journey (warmth → intelligence → growth).

**Icons:** SVG sketches, `64px`, drawn with `stroke-dashoffset` animation on scroll-entry. Not icon-library icons — hand-drawn SVG sketches matching the Rough.js aesthetic.

**Copy direction:** The step-1 copy is in first person (`"I'm preparing for..."`) — it should literally read like what the user would say. This is mirroring the onboarding flow that comes later.

---

### 1C — Feature Highlights

**Padding:** `py-32 px-6`. Max-width `960px`.

```
        features            ← mono, lavender
  Built for how you actually learn

  ┌─────────────────┐  ┌─────────────────┐
  │ 🎙 Voice-first  │  │ ✏ Live canvas   │
  │  interaction    │  │                 │
  │                 │  │                 │
  └─────────────────┘  └─────────────────┘
  ┌─────────────────┐  ┌─────────────────┐
  │ 👤 Personalized │  │ ⚡ Struggle      │
  │  agents         │  │  detection      │
  │                 │  │                 │
  └─────────────────┘  └─────────────────┘
```

**Cards:** `glass-card`, `p-8`, `border-radius: 16px`. Icon in `48px` square with `bg-{accent}/10`, `color-{accent}`. Title `1.125rem`, weight `600`. Body `0.875rem`, `--chalk-soft`, `line-height: 1.7`.

**Hover state:** Subtle `scale(1.01)` + a whisper of the accent color leaking into the card border (`border-color: rgba({accent}/0.15)`). Nothing dramatic. Just alive.

---

### 1D — Demo Section

**Padding:** `py-32 px-6`. `scroll-margin-top: 96px`.

```
       see it in action     ← mono, sage
    A real tutoring session

  ┌──────────────────────────────────────────────────┐
  │                                            ◤   ◥ │  ← bracket corners
  │                                                  │
  │                    [▷]                           │
  │              Demo video coming soon              │
  │     Watch Murmur explain projectile motion       │
  │     with real-time diagrams                      │
  │                                            ◣   ◢ │
  └──────────────────────────────────────────────────┘
```

**Video container:** `aspect-ratio: 16/9`, `border-radius: 16px`, glass card background. The bracket corner SVGs are `chalk-faint/30`, `stroke-width: 1.5`. They give the frame a film/viewfinder feel without being literal.

**Play button:** `80px` circle, glass, `▷` in amber. Hover: scale(1.05).

**When video becomes available:** Replace the placeholder with an `<video>` autoplay on hover, controls on click. The video should start playing a few seconds in (not from the first frame of a loading screen).

---

### 1E — CTA Footer

**Full bleed.** Radial amber glow at `opacity: 0.04` centered.

```
    Ready to learn differently?

    Stop staring at textbooks. Start having
    conversations with an AI that draws,
    explains, and adapts to you.

         [Create your agent →]

         Free to start. No credit card required.
```

**Headline:** `display` size, weight `700`. Short. Punchy.
**Body:** `1.125rem`, `--chalk-soft`. Two sentences, each earns its place.
**CTA:** Single amber button, `px-10 py-4`, size `1.125rem`. Full amber glow shadow.
**Fine print:** `0.75rem`, `--chalk-faint`. Not legal — reassurance.

---

### 1F — Footer Strip

`64px` height. `border-top: 1px solid rgba(255,255,255,0.06)`.

```
[logo + murmur]                              Voice AI that thinks visually.
```

No links. No social. No legal. Clean.

---

## Screen 2 — Register `/register`

**Emotional job:** Commitment with minimum friction. Zero intimidation.

### Layout

**Split layout on desktop (≥1024px):**

```
┌────────────────────────┬───────────────────────────────┐
│                        │                               │
│   Left panel (40%)     │   Right panel (60%)           │
│   ────────────────     │   ──────────────────────      │
│   The "why" side       │   The form side               │
│                        │                               │
│   [Murmur logo]        │   Create your account         │
│                        │                               │
│   "An AI tutor         │   [Name field]                │
│    that teaches        │   [Email field]               │
│    by drawing."        │   [Password field]            │
│                        │                               │
│   [sketched canvas     │   [Create account →]          │
│    illustration —      │                               │
│    faint, animated]    │   Already have an account?    │
│                        │   Sign in                     │
│                        │                               │
└────────────────────────┴───────────────────────────────┘
```

**Mobile:** Stack vertically. Left panel collapses to just the logo + tagline at the top, `40px` tall. Form takes the rest.

**Left panel:**
`background: rgba(255,255,255,0.015)`, `border-right: 1px solid rgba(255,255,255,0.04)`.
Logo: centered vertically, with `murmur` wordmark below.
Tagline: `1rem`, `--chalk-soft`, italic. `max-width: 240px`, centered.
Illustration: A faint SVG canvas sketch animating slowly — shapes being drawn at ~30% opacity. Like seeing the product while signing up.

**Right panel:**
Pure `--void` background. Vertically and horizontally centered form. `max-width: 400px`.

**Form heading:** `1.75rem`, weight `600`, tracking `-0.01em`. `"Create your account"`. Not `"Sign Up"`. Not `"Register"`. The phrasing implies ownership from the start.

**Fields:**
No full-border box inputs. Underline pattern only:
```
  Your name
 ─────────────────   ← 1px --chalk-faint
```
On focus: underline transitions to `--amber`. Placeholder text opacity `0.4`. Label floats up on focus (`transform: translateY(-20px) scale(0.85)`), transitions from `--chalk-soft` to `--amber`. All transitions `200ms`.

Field order: Name → Email → Password. Name first — it makes it feel personal, not transactional.

**Submit button:** Full-width amber. `py-3.5`. `border-radius: 10px`. `font-weight: 600`. Loading state: replace text with `<Loader2>` spinner, amber. Never disable the button — the spinner is enough feedback.

**Sign in link:** `0.875rem`, `--chalk-soft`. `"Already have an account? Sign in"` — inline, centered below the button. `"Sign in"` is underlined on hover, amber on hover. No separate line. No visual hierarchy fighting the CTA.

**Error state:** Under the relevant field, a `0.8125rem` `--ember` message fades in with `y: -4 → 0`. The field underline turns `--ember`. No toast, no modal.

**Success / redirect:** After account creation, brief 400ms delay → redirect to `/agents/new`. The transition should feel like opening a door, not clicking a button. Consider a full-screen amber flash at 10% opacity for 150ms before the route change.

---

## Screen 3 — Login `/login`

**Identical structure to Register** but simplified.

- No name field.
- Heading: `"Welcome back."` (period intentional — warm, not corporate).
- Sub-heading: `"Sign in to your account"`, `--chalk-soft`.
- No illustration on mobile.
- "Don't have an account? Get started" link below form.

**Forgot password:** Ghost text below password field, `"Forgot password?"`, `--chalk-soft`, 0.8125rem. Right-aligned. Tap target 44px.

---

## Screen 4 — Agent Creation Wizard `/agents/new`

**Emotional job:** Make the user feel understood, not interviewed.

### Structural Philosophy

This is not a form wizard. It's a **conversation**. The UI should feel like the product is asking you questions, not collecting data. Each step reveals one question at a time, full screen, with enormous breathing room.

### Progress Bar

```
───●──────────────────   step 1 of 7 (or however many)
```

Fixed top, `4px` height bar, `--chalk-faint/20` track, `--amber` fill, `border-radius: 2px`. Width fills based on progress. Transitions with `cubic-bezier(0.16, 1, 0.3, 1)`, `600ms`. A faint `font-mono` step counter at the right: `01 / 07`, `0.6875rem`, `--chalk-faint`.

### Per-Step Layout

```
──────────────────────────────────────────────────────────
  [progress bar]

                  ↑ previous (ghost, top-left)


        step label     ← mono, amber, uppercase, 0.6875rem


        Big question text


        [input / choice / etc]


        Continue →              ← primary amber

                 Skip ›         ← ghost, for skippable steps
──────────────────────────────────────────────────────────
```

**Step label:** `font-mono`, `0.6875rem`, uppercase, `letter-spacing: 0.1em`, `--amber`. Example: `STEP 1 · WHO ARE YOU STUDYING FOR`. This functions as navigation context — where am I in the wizard.

**Question text:** `display` size, weight `500`, `max-width: 560px`, centered. Conversational. Ends with no punctuation for statements, `?` for questions.

**Step transition:** Previous step exits `x: 0 → -60px, opacity: 1 → 0`. New step enters `x: 60px → 0, opacity: 0 → 1`. Duration `300ms`, `cubic-bezier(0.16, 1, 0.3, 1)`. Feels like a book page turning.

### Steps (7 total — all current)

---

**Step 1 — "Who are you studying for?"**

```
     STEP 1 · YOUR IDENTITY

     Who are you?

  ┌───────────────────────────────────────────────────┐
  │ I'm a student preparing for...                    │
  │                                                   │
  └───────────────────────────────────────────────────┘
  (free text area, 3 rows, underline style)

                                          Continue →
                                          Skip ›
```

The text area is a freeform field. Placeholder: `"e.g. I'm preparing for JEE Physics, struggling with rotational mechanics and waves."` The placeholder is an example answer, not a label — it shows them what good looks like. `font-size: 1.125rem`, `line-height: 1.7`.

On mobile, the textarea expands with content. No scroll.

---

**Step 2 — "Give your agent a name"**

```
     STEP 2 · NAME YOUR TUTOR

     What should we call it?

  ┌──────────────────────────────┐
  │                              │  underline input, large
  └──────────────────────────────┘
  (e.g. "Physics Coach", "Arjun's Tutor")

  [suggested: "JEE Physics Tutor"]   ← tap to populate, amber chip

                                          Continue →
```

Suggestion chip: `glass-card`, `px-3 py-1.5`, `border-radius: 8px`. `--chalk-soft` text, `--amber` on hover. On tap: populates field with a `200ms` type-in animation.

---

**Step 3 — "What subject?"**

```
     STEP 3 · SUBJECT FOCUS

     What are you studying?

  [Physics]  [Mathematics]  [Chemistry]  [Biology]
  [Computer Science]  [History]  [Other ›]
```

**Chip grid.** Each chip: `glass-card`, `px-5 py-3`, `border-radius: 10px`, `font-size: 0.9375rem`. Hover: `bg-graphite`. Selected: `bg-amber/15`, `border-color: amber/30`, `text-amber`. Allow multi-select. Chips animate in with a staggered `y: 10 → 0` entrance.

"Other ›" chip: expands an inline text input below the grid when clicked, 200ms.

---

**Step 4 — "What level?"**

```
     STEP 4 · LEARNING LEVEL

     What level are you at?

  [High School]   [JEE/NEET]   [College]   [Self-study]
```

Same chip pattern. Single-select. Selected chip gets a subtle amber glow `box-shadow: 0 0 12px rgba(245,166,35,0.2)`.

---

**Step 5 — "Upload resources (optional)"**

```
     STEP 5 · LEARNING MATERIALS

     Got notes or textbooks? (optional)

  ┌──────────────────────────────────────────────────┐
  │                                                  │
  │       ↑  Drop PDFs, docs, or links here          │
  │                                                  │
  │       or  [Browse files]                         │
  │                                                  │
  └──────────────────────────────────────────────────┘

  or paste a link:  ─────────────────────  [Add]

  [uploaded files appear as small dismissible chips below]

                                          Continue →
                                          Skip ›
```

**Dropzone:** `border: 1.5px dashed --chalk-faint/30`, `border-radius: 12px`, `height: 160px`. Drag-over state: border transitions to `--amber`, background fills to `rgba(245,166,35,0.04)`. The upload icon (UploadCloud) is `--chalk-faint`, transitions to `--amber` on drag.

**Uploaded file chips:** Glass, `px-3 py-2`, filename truncated at 24 chars + `...`, file type icon, `✕` to remove. Stack vertically with `gap: 8px`.

---

**Step 6 — "Teaching style"**

```
     STEP 6 · HOW YOU LEARN

     How should your tutor teach?

  [Step-by-step      ]  [Example-heavy     ]
  [Challenge me      ]  [Eli5 (simple)     ]
```

Each chip is slightly larger (`80px × 80px`) and stacked in a 2×2 grid on mobile, 4-column on desktop. Each chip has a tiny SVG icon (5 rough-drawn lines, a question mark shape, etc.) and a label below.

---

**Step 7 — "Pick a voice"**

```
     STEP 7 · YOUR TUTOR'S VOICE

     What should it sound like?

  [● Conversational]  [● Precise]  [● Energetic]

  (preview button plays 5s sample when clicked)

                                          Create Agent →
```

Voice chips include a `▷` play icon. On click: the icon becomes a `■` stop, and a faint waveform animation plays inside the chip border. Audio plays. The "Create Agent" CTA replaces "Continue" on the last step.

**Creating state:** After submit, the button text becomes `"Building your agent..."`, with a slow `--lavender` glow pulsing. Full-screen overlay fades in at `opacity: 0.6`, `--void`. A centered animation plays: a simple SVG of the robot sketch from the dashboard empty state, drawing itself. Duration ~2s. Then redirect to dashboard.

---

## Screen 5 — Dashboard `/dashboard`

**Emotional job:** Clarity. Know where you are. Get to work fast.

### Layout Anatomy

```
┌──────────────────────────────────────────────────────────┐
│ [nav: 64px]                                              │
├──────────────────────────────────────────────────────────┤
│ pt-24                                                    │
│                                                          │
│ Your Agents                     [+ Create Agent]         │  ← Title row
│ Create and manage your personalized AI tutors.           │
│                                                          │
│ ┌────────────┐  ┌────────────┐  ┌────────────┐          │  ← Agent grid
│ │ 🤖         │  │ 📐         │  │ ⚗️          │          │
│ │ Physics    │  │ Maths      │  │ Chemistry  │          │
│ │ Coach      │  │ Tutor      │  │ Agent      │          │
│ │            │  │            │  │ DEFAULT    │          │
│ │ description│  │ description│  │ description│          │
│ │            │  │            │  │            │          │
│ │ physics/jee│  │ maths/jee  │  │ chem/neet  │          │
│ │            │  │            │  │            │          │
│ │[▷ Session ][⌛][📁][✏][🗑]│  │[▷ Session ]...        │
│ └────────────┘  └────────────┘  └────────────┘          │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### Title Row

Left: `"Your Agents"` in `1.75rem`, weight `700`, tight tracking. Sub-text: `"Create and manage your personalized AI tutors."` in `0.875rem`, `--chalk-soft`. Stack vertical, `gap: 4px`.

Right: `[+ Create Agent]` amber primary button, `height: 40px`, `px-5`, `border-radius: 10px`, `font-size: 0.875rem`. Plus icon precedes the label.

### Agent Card

```
┌───────────────────────────────────┐
│                                   │  ← glass-card, p-6, border-radius:16px
│  🤖   ← 36px emoji                │
│                                   │
│  Physics Coach   [DEFAULT]         │  ← name, badge
│  Personalized for JEE Physics...  │  ← description, 2 lines max
│                                   │
│  physics / jee advanced           │  ← mono-label, chalk-faint
│                                   │
│  ┌──────────────┐  [⌛] [📁] [✏] [🗑]│  ← action row
│  │ ▷ Start      │                 │
│  └──────────────┘                 │
└───────────────────────────────────┘
```

**Icon:** 36px emoji, `mb-4`. This is the fastest character recognition — a human picks their agent by visual identity before reading the name.

**Name:** `1.0625rem`, weight `600`. Followed by optional "Default" badge: `px-2 py-0.5`, `border-radius: 9999px`, `bg-amber/15`, `border: 1px solid amber/20`, `text-amber`, `font-mono`, `0.625rem`, uppercase.

**Description:** `0.875rem`, `--chalk-soft`, `line-clamp-2`. The agent's personality in 20 words.

**Meta line:** `font-mono`, `0.6875rem`, `--chalk-faint`. Subject and level, pipe-separated. Truncated with ellipsis.

**Action row:**
- `Start Session` button: fills available width (flex-1). Amber, `height: 40px`, `border-radius: 10px`. `▷` icon + label.
- Icon buttons: History `⌛`, Resources `📁`, Edit `✏`, Delete `🗑`. Each: `40px × 40px`, `border-radius: 10px`. Default: `--chalk-soft` icon. Hover: `bg-graphite`, icon brightens. The four fit in a row with `gap: 8px`.
- Delete: icon is `--ember/70`. Hover: `bg-ember/10`. Click reveals an inline confirmation: the icon button transforms into `[Delete] [✕]` chips — no modal, no browser confirm. `Delete` is amber on `ember/15`, `✕` is ghost.

**Card hover:** `scale(1.02)`, `box-shadow: 0 0 30px rgba(245,166,35,0.08)`. Duration `200ms`.

**Card entrance (staggered):** Each card enters `y: 20 → 0, opacity: 0 → 1`. Stagger `50ms` per card.

### Empty State

Centered in the grid area. Max-width `460px`, centered.

```
    [robot SVG sketch, 160px]

    No agents yet

    Create your first AI agent to get started.
    It only takes a minute to set up a personalized tutor.

         [✦ Create your first agent]
```

The robot SVG draws itself on entrance. Sparkle `*` symbols animate in and out. This is not a sad empty state — it's an invitation.

### Session History Panel

Slides in from the right as an overlay panel (not a page change). Width `400px` on desktop, full-width bottom sheet on mobile.

```
┌────────────────────────────────────────┐
│ [✕]  Session History                  │  ← header
│ [agent name and icon]                 │
├────────────────────────────────────────┤
│                                        │
│ Today                                  │
│                                        │
│  [session card]                        │
│  [session card]                        │
│                                        │
│ Last week                              │
│                                        │
│  [session card]                        │
│                                        │
└────────────────────────────────────────┘
```

**Panel:** `background: rgba(17,17,22,0.96)`, `backdrop-filter: blur(32px)`, `border-left: 1px solid rgba(255,255,255,0.06)`. Enters `x: 100% → 0`, exits `x: 0 → 100%`. Duration `350ms`.

**Session cards:** Each shows: date+time (mono-label), first message snippet (1 line, body), session duration (mono). Clickable — opens the session (read-only replay, future feature).

---

## Screen 6 — Session Page `/session/[agentId]`

**Emotional job:** Disappear. The UI should stop existing. The student should forget they're using an app.

### Layout Anatomy

```
┌──────────────────────────────────────────────────────────┐
│ [session nav: agent name · [⌛ history] [✕ exit]]        │  64px
├──────────────────────────────────────────────────────────┤
│                                                          │
│                                                          │
│                                                          │
│                   C A N V A S                            │  ~80% height
│            (SVGCanvas — Rough.js + GSAP)                 │
│                                                          │
│                                                          │
│                                                          │
├──────────────────────────────────────────────────────────┤
│        [orb]             [chat panel toggle]             │  bottom strip
└──────────────────────────────────────────────────────────┘
```

### Session Nav (top strip)

`height: 64px`, glass, `backdrop-filter: blur(20px)`, very subtle `border-bottom`.

Left: Back arrow (ghost, `--chalk-faint`) + agent emoji + agent name (`font-mono`, `0.8125rem`, `--chalk-soft`). This is the only navigation back to the dashboard.

Right: two icon buttons — Session history (clock icon), Exit (X icon). Ghost buttons, `32px`, subtle hover.

**During active session:** The nav is `opacity: 0.5` unless hovered. It retreats. The canvas owns the screen.

### Canvas

Full remaining viewport height minus nav. `background: --void`. No frame, no border — it is the screen.

The canvas has no UI chrome of its own. Elements appear directly on it: shapes, labels, arrows, equations. They are drawn in with path animations. They breathe when active (amber glow settling to nothing over 2-3s).

**Placeholder (before first session start):** A faint `opacity: 0.1` grid of tiny dots, `32px` spacing. Like graph paper, but barely there. Signals "this is a workspace."

**Canvas is not scrollable** within the session. The AI manages placement spatially, always targeting visible area.

### Voice Orb

Positioned bottom-left, `24px` from each edge. `96px × 96px` circle.

| State | Interior | Border | Glow | Label |
|---|---|---|---|---|
| **Idle** | `--void` gradient | `1.5px dashed --chalk-faint/30` | None | `"Tap to start"` mono-label below |
| **Connecting** | Conic gradient rotating | `--lavender` | Soft lavender | None |
| **Listening** | `--sage` gradient | `--sage` solid | Sage glow | Waveform bars inside |
| **Thinking** | `--amber` → `--void` gradient | `--amber` | Amber glow | Three-dot pulse |
| **Speaking** | `--lavender → --amber` gradient | Mixed | Strong glow | Waveform bars |
| **Error** | `--ember` dim | `--ember` | Red pulse | Mic-off icon |

The orb **breathes**: a subtle `scale(1.0) → scale(1.03) → scale(1.0)` oscillation at 3s cycle when idle. Audio level modulates scale during listening/speaking — louder = larger.

Tap: toggle connect/disconnect. On first tap, browser mic permission prompt appears. If denied: orb snaps to error state, label below reads `"Microphone access needed"` in `--ember`, `0.75rem`.

### Chat Panel

Bottom-right, collapsible. Default: collapsed to a `40px × 40px` glass icon button (MessageCircle icon). On expand: slides up as a `320px × 480px` panel, `border-radius: 16px 16px 16px 0`, glass with `opacity: 0.92`.

```
┌────────────────────────────────────────┐
│ Conversation                    [−]    │
├────────────────────────────────────────┤
│                                        │
│  ← What is Newton's second law?        │  user message, right-aligned
│                                        │
│  Force equals mass times acceleration  │  AI message, left-aligned, no bg
│  ...let me draw it out.                │  amber left-border accent
│                                        │
├────────────────────────────────────────┤
│  [type a message...]                   │
│  ─────────────────────────────────     │
└────────────────────────────────────────┘
```

Chat bubble rules:
- User: right-aligned, glass card, `px-4 py-2.5`, `border-radius: 12px 12px 0 12px`.
- AI: left-aligned, NO background, amber `2px` left-border, `pl-3`. Text directly on "blackboard." Reads as drawn, not printed.
- Font: `0.875rem`, `line-height: 1.65`. No avatars.

Text input: underline style, `placeholder: "Type a message..."`. On focus, underline glows amber. Enter / Cmd+Enter to send.

### State: No session started yet

The canvas shows the placeholder grid. The orb pulses idle. No chat panel visible. A centered hint floats over the canvas:

```
    ┌─────────────────────────────────────────────┐
    │                                             │
    │   👁  Physics Coach is ready.               │
    │                                             │
    │   Tap the orb to begin your session         │
    │   or type a question in chat.               │
    │                                             │
    └─────────────────────────────────────────────┘
```

Glass card, `border-radius: 16px`, `max-width: 360px`, centered. Fades out as soon as the session starts.

---

## Screen 7 — Resources Page `/agents/[id]/resources`

**Emotional job:** Simple file cabinet. Upload and forget.

### Layout

```
┌──────────────────────────────────────────────────────────┐
│ [← Back to dashboard]        [agent emoji + name]        │  nav
├──────────────────────────────────────────────────────────┤
│ pt-24                                                    │
│                                                          │
│ Learning Materials                                       │
│ Files and links your agent uses when answering.          │
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │                                                      │ │  dropzone
│ │   ↑   Drop files here or  [Browse]                   │ │
│ │       PDF, DOCX, TXT — max 25MB per file             │ │
│ │                                                      │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ or paste a URL:  [──────────────────────────]  [+ Add]  │
│                                                          │
│ ──────── Resources (3) ────────────────────────────      │
│                                                          │
│ [📄 HC Verma Chapter 7.pdf]   Processing · 3 chunks  [✕]│
│ [🔗 youtube.com/...]          Ready · 12 chunks      [✕]│
│ [📄 Rotational Mechanics.pdf] Ready · 47 chunks      [✕]│
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**Dropzone:** `border: 1.5px dashed rgba(255,255,255,0.1)`, `border-radius: 12px`, `min-height: 140px`. `UploadCloud` icon, `--chalk-faint`. Full drag-and-drop. Drag-over: amber border + faint amber fill.

**Resource row:** `glass-card`, `px-4 py-3`, flex row. File icon (amber for PDF, lavender for URL). Filename, `0.875rem`. Status badge: `font-mono`, `0.6875rem`, `--chalk-soft`. "Processing" gets a spinner, "Ready" gets a `--sage` dot. Chunk count. `✕` delete button right-aligned, `--chalk-faint`, `ember` on hover.

**Processing animation:** While a file is chunking, a subtle `--amber` progress bar fills under the row. Width interpolates from 0 to 100% over the expected processing time (estimated from file size). Not a fake progress bar — the backend should stream chunk count so it's real.

---

## Interaction Principles (cross-screen)

### 1. Every empty state is an invitation.

Empty states don't apologize for being empty. They make you want to fill them. Copy like "No agents yet" is always followed by a direct action — never just a description of emptiness.

### 2. Destructive actions require confirmation, inline.

Never browser `confirm()`. Never a modal. The delete button transforms into a mini confirmation inline within the same space. This is more respectful of context.

### 3. Loading is not waiting — it's anticipation.

Skeleton screens are always shown on initial load. Pulse animations use `opacity: 0.15 → 0.08 → 0.15`, duration `1.4s`, easing `ease-in-out`. They match the shape of what's coming — not generic gray bars.

### 4. Errors are human.

Error messages never say "An error occurred." They say `"Couldn't load your agents — tap to try again"`. Always one sentence. Always with an action. In `--ember`, not a red toast bar.

### 5. Focus states are real.

Every interactive element has a visible focus state (not just for accessibility, but because focus state IS hover state for keyboard users). Use `ring: 2px --amber/50` offset `2px` on all buttons and inputs when focused via keyboard. Mouse focus: no ring (use `:focus-visible`).

### 6. Touch targets are 44px minimum.

Every tappable element on mobile has a minimum 44×44 tap target, even if the visual is smaller. Use padding to extend the hit area.

### 7. Transitions are 300ms, never longer for navigation.

Route transitions: `300ms`, opacity + slight y. Canvas animations: `400–800ms`. Settling glow: `2000–3000ms`. Micro-interactions: `150ms`. Never animate something for longer than it needs to take.

---

## Responsive Strategy

| Breakpoint | Layout |
|---|---|
| `< 640px` (mobile) | Single column. Wizard is full-screen per step. Dashboard grid is 1-col. Session is full-screen orb + swipe-up chat. |
| `640–1024px` (tablet) | 2-col grid on dashboard. Wizard same as mobile. Session chat panel is overlay. |
| `> 1024px` (desktop) | 3-col grid. Side-by-side on register. Persistent chat panel on session. |

**Mobile-first priority:** The session experience must work perfectly on a phone. A student is likely lying in bed with a textbook and talking to Murmur. The orb should be thumb-reachable (bottom-center on mobile, not bottom-left). The canvas is above it. Chat is a swipe-up sheet.

---

## Copy Tone Reference

Murmur's voice is **warm, direct, and slightly poetic**. Not corporate. Not startup-bro. Not over-casual.

| ✓ Use | ✗ Don't use |
|---|---|
| "Welcome back." | "Welcome back to Murmur!" |
| "Create your agent" | "Create New Agent" |
| "Your agent is ready." | "Agent successfully created!" |
| "Couldn't load — try again" | "Error 500: Failed to fetch agents" |
| "What should we call it?" | "Agent Name" |
| "Got notes or textbooks?" | "Upload Resources (Optional)" |
| "Tap to start" | "Click here to begin your session" |

---

## Color Usage Cheat Sheet

| Color | CSS var | Use in funnel |
|---|---|---|
| `#08080C` | `--void` | Page backgrounds, primary button text |
| `#111116` | `--slate` | Cards, panels |
| `#1A1A21` | `--graphite` | Hover states on interactive surfaces |
| `#E8E4DC` | `--chalk` | Primary text |
| `#9B9790` | `--chalk-soft` | Secondary text, labels, hints |
| `#4A4843` | `--chalk-faint` | Borders, dividers, placeholder underlines |
| `#F5A623` | `--amber` | Primary CTA, active states, "just drawn" glow |
| `#8B7EC8` | `--lavender` | AI presence, system labels, secondary elements |
| `#6BCB77` | `--sage` | Listening, success, growth |
| `#EF4444` | `--ember` | Errors, delete confirms, interruptions |

---

## Type Usage Cheat Sheet

| Token | Size | Weight | Font | Use |
|---|---|---|---|---|
| `display` | `2.5rem` | `500` | Inter | Page hero headlines. Use sparingly. |
| `title` | `1.25rem` | `500` | Inter | Section headers, card titles |
| `body` | `0.9375rem` | `400` | Inter | Default reading copy |
| `caption` | `0.8125rem` | `400` | Inter | Timestamps, secondary info |
| `mono-label` | `0.6875rem` | `500` | JetBrains Mono | Step labels, badges, tags, system text |
| `mono-data` | `0.8125rem` | `400` | JetBrains Mono | Metrics, chunk counts, data values |

---

*A great interface for a thinking tool must itself look like it was thoughtfully assembled — every decision visible, none of them shouting.*

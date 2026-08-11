# Murmur — UX Requirements & Page Design Spec
**Last updated:** 2026-03-25
**Purpose:** Requirements for each page and component. No hardcoded design values. Describes what the user experiences and why — not how it looks in pixels.

---

## Vision

Conversational AI today is invisible. You talk to it, it talks back, and nothing persists. The entire interaction vanishes the moment it ends. There is no workspace, no artifact, no shared surface where you and the AI can look at the same thing at the same time.

Murmur exists because conversation alone is not enough. The core idea is not education, not tutoring, not any single vertical — it is the belief that conversational AI needs a **visual layer**. A persistent, spatial surface where the AI can place, arrange, animate, and build things as it speaks. Where the output of a conversation is not just words that scroll off screen, but a living canvas that accumulates meaning.

Education is the first proof of this idea because it makes the gap most obvious — a student asking "how does a pulley system work" gets 10x more value from a drawn, animated explanation than from a paragraph of text. But the principle is universal. A financial advisor explaining a portfolio. A doctor walking through a diagnosis. An engineer reasoning through a system design. A team planning a product. Every one of these is a conversation that would be better if the AI could draw while it talked.

The product is not a tutor with a canvas bolted on. It is a **canvas-native conversational AI** — an interface primitive where voice is the input, the canvas is the workspace, and the AI is an agent that can talk, draw, animate, fetch, compute, and remember, all at once, all in real time.

Education is where we land. The visual layer is what we're building.

---

## Overall User Journey

There are three primary journeys:

**New user:**
Landing → Login/Register → Dashboard (empty state) → Create Agent (wizard) → Add Resources → Start Session

**Returning user:**
Login → Dashboard → Pick an agent → Start Session → View session history

**Power user:**
Dashboard → Edit agent → Manage resources → Session → Review mastery heatmap → Repeat

---

## Pages

---

### 1. Landing Page `/`

**Route group:** `(marketing)`

**Purpose:** Convert a visitor into a signed-up user. Must communicate the core value proposition in under 10 seconds without requiring any interaction.

**User enters from:** Direct URL, search, referral link.

**User leaves to:** Login / Register (via CTA), or bounces.

---

#### Layout

The page has a top navigation bar, a hero section, a feature breakdown section, and a footer.

---

#### Nav Bar

**Requirement:** Always visible at the top. Contains the product name/logo on the left. On the right: a "Sign in" link (for existing users) and a primary "Get started" button (for new users). The nav should not reorder or collapse to a hamburger on desktop — it stays simple.

On mobile, the nav collapses the right-side links into a minimal state, keeping only the primary CTA visible.

---

#### Hero Section

**Requirement:** The hero must communicate one idea: *you speak, the AI draws and explains in real time.*

- A headline that states the core value in one short sentence. No jargon. No feature list.
- A single supporting line that clarifies who this is for (e.g. students stuck on hard concepts).
- A primary CTA button that takes the user directly to sign-up.
- An optional secondary action that lets the user see a demo without signing up (could be an inline preview or a short autoplay loop).
- The hero should work with or without a visual. If a visual is present, it should illustrate the canvas + voice experience — not a generic stock image.

**User flow:** User reads headline → understands it immediately → clicks "Get started" or watches demo.

---

#### Features Section

**Requirement:** Explain the three things that make Murmur different from ChatGPT with a mic:

1. The AI draws as it talks — visuals are synchronized to explanation, not pasted afterward.
2. You can interrupt — the conversation is natural, not turn-taking.
3. It remembers — the tutor knows what you've struggled with across sessions.

Each feature should be a short description. No bullet lists of bullet lists. The user should read this section in under 60 seconds.

---

#### Footer

**Requirement:** Minimal. Product name, links to privacy/terms if needed. No heavy footer nav.

---

---

### 2. Login Page `/login`

**Route:** `/login` (not inside a route group — accessible always)

**Purpose:** Authenticate an existing user or create a new account. Single page handles both sign-in and sign-up to reduce friction.

**User enters from:** Landing page CTA, direct URL, dashboard redirect when unauthenticated.

**User leaves to:** Dashboard (on success), stays on page (on error).

---

#### Layout

Centered card on a minimal background. No nav, no footer — the only job of this page is authentication.

---

#### Auth Form

**Requirement:**

- A mode toggle at the top: "Sign in" / "Create account." Switching modes updates the form fields (sign-up shows a name field, sign-in doesn't). The URL does not need to change.
- Email field. Password field. For sign-up: confirm password or a name field.
- A "Continue with Google" button above or below the form, separated visually to show it's an alternative path, not a related option.
- A primary action button: "Sign in" or "Create account" depending on mode.
- Inline error messages directly beneath the relevant field — not toast notifications that disappear.
- A forgotten password link visible in sign-in mode.

**User flow:** User lands → selects mode if needed → fills form or clicks Google → redirected to dashboard.

---

#### Error States

**Requirement:** Errors must be specific:
- "No account found with this email" not "Invalid credentials"
- "Incorrect password" not "Invalid credentials"
- Network/server errors: "Something went wrong, please try again."

---

---

### 3. Register Page `/register`

**Route group:** `(auth)`

**Status:** Currently a separate page. Consider whether this should be unified with `/login` or remain separate. If separate, the UX requirements below apply.

**Purpose:** Collect user details for account creation.

**Requirement:** Name, email, password. Google signup option. Link back to login. Functionally identical to the sign-up mode of the login page — evaluate whether this page should be removed in favor of the unified `/login` flow.

---

---

### 4. Dashboard `/dashboard`

**Route group:** `(app)` — requires authentication.

**Purpose:** The user's home base. See all agents, start a session with one, create new agents, manage existing ones.

**User enters from:** Post-login redirect, nav link.

**User leaves to:** Session page (to start chatting), agent creation wizard (to create), resources page (to manage), or logout.

---

#### Layout

A header at the top. Below: a grid of agent cards. The grid scales based on number of agents — fewer agents = larger cards, more agents = denser grid (or fixed card size with scrolling).

---

#### Header

**Requirement:** Product name/logo on the left. On the right: the logged-in user's name or avatar, and a sign-out option. No other navigation clutter — the dashboard IS the navigation hub.

---

#### Agent Grid

**Requirement:** Each agent is represented as a card. The card must surface:

- Agent name (the primary label — must be large and readable at a glance)
- A short description or subject area
- Last active timestamp (relative: "2 days ago", not an absolute timestamp)
- A primary action to start a session immediately (clicking the card body opens the session)
- A secondary action set (kebab menu or hover controls) for: Edit, Manage Resources, Delete

**Empty state:** When no agents exist, the grid shows a single prominent card that says "Create your first agent" with a brief line about what an agent is. This is the only CTA shown — no empty grid with a separate button elsewhere.

---

#### Create Agent Card / Button

**Requirement:** Always visible in the grid, typically as the first card or a persistently visible button. Must not compete with existing agent cards — it should feel like a natural part of the grid, not a modal trigger.

---

#### Delete Confirmation

**Requirement:** Deleting an agent requires a confirmation step. This can be an inline confirmation (the card state changes to show "Are you sure?") or a modal. Either way: show the agent name in the confirmation, explain that sessions and resources are also deleted, require an explicit second click.

---

---

### 5. Agent Creation Wizard `/agents/new`

**Route group:** `(app)`

**Purpose:** Walk the user through defining their agent — who it is, what it knows, how it behaves. Produces an agent with a name, description, subject area, teaching style, and preferences.

**User enters from:** Dashboard "Create agent" action.

**User leaves to:** Dashboard (on completion or cancel), Resources page (optional post-creation redirect to upload materials).

---

#### Layout

Full-screen wizard. A progress indicator at the top shows which step the user is on. The current step occupies the center of the screen. Navigation: Back and Next buttons at the bottom.

---

#### Progress Indicator

**Requirement:** Shows numbered steps or a progress bar. The user can see how many steps remain. Clicking a completed step should navigate back to it (not forward). Clicking a future step should NOT be allowed — steps must be completed in order.

---

#### Step 1 — Agent Name & Description

**Requirement:**
- A text input for the agent's name. Required — cannot proceed without it.
- A text area for a description ("What is this agent for?"). Optional but encouraged with a helpful placeholder example.

---

#### Step 2 — Subject / Domain

**Requirement:**
- A selection of subject areas (Physics, Chemistry, Math, Biology, etc.) as selectable chips or a dropdown.
- An "Other / Custom" option with a text field.
- Multiple subjects can be selected.
- This field shapes the agent's system prompt and canvas tool selection.

---

#### Step 3 — Teaching Style

**Requirement:**
- A small set of style options the user can choose from. Examples: "Step-by-step", "Socratic (asks questions back)", "Concise answers", "Detailed explanations."
- Show a one-line preview of what each style means.
- Only one style selected at a time.

---

#### Step 4 — Student Context (About You)

**Requirement:**
- Optional step — user can skip.
- Fields: grade/year level, exam target (JEE, NEET, CBSE, A-Levels, etc.), specific topics they struggle with.
- This populates the agent's initial student model.

---

#### Step 5 — Voice Preferences

**Requirement:**
- Optional step.
- Preferred voice type (from available TTS voices) — ideally a short audio preview button next to each option.
- Speech speed preference (normal, slightly slower).

---

#### Step 6 — Canvas Preferences

**Requirement:**
- Optional step.
- Toggle: "Always draw diagrams" vs "Draw only when needed."
- Preferred diagram style (simple/minimal vs. detailed).

---

#### Step 7 — Review & Create

**Requirement:**
- Summary of all selected settings, displayed as readable sentences (not a raw JSON dump).
- User can click any setting to jump back and edit it.
- A prominent "Create Agent" button.
- A "Go back to dashboard" link for cancellation without creating.

---

#### Skippable Steps

**Requirement:** Steps 4, 5, and 6 must be skippable with a "Skip for now" link. Skipped steps use sensible defaults. The user can always change these later from the edit agent page.

---

---

### 6. Agent Edit `/agents/[agentId]/edit`

**Route group:** `(app)`

**Purpose:** Let the user modify an existing agent's configuration.

**Status:** Currently a placeholder — no form content yet.

**Requirement:** Mirrors the creation wizard but pre-filled with existing values. Does not need to be step-by-step — can be a single scrollable page with all settings visible at once (a "settings page" pattern rather than a wizard). A "Save changes" button at the top and bottom of the page. Changes take effect immediately on the next session.

---

---

### 7. Resources Page `/agents/[agentId]/resources`

**Route group:** `(app)`

**Purpose:** Let the user upload learning materials that the agent will use to ground its explanations — study notes, textbooks, past papers, etc.

**User enters from:** Dashboard (via agent card kebab menu), post-creation redirect.

**User leaves to:** Dashboard, or back to agent settings.

---

#### Layout

Two-panel or two-section layout: left/top for uploading new resources, right/bottom for viewing existing resources.

---

#### Upload Section

**Requirement:**
- Supports two input types: PDF file upload and URL.
- PDF upload: a drag-and-drop zone that also has a "Browse files" button. Shows file name and size before confirming upload.
- URL input: a text field and an "Add URL" button. The system fetches and processes the URL content.
- One resource at a time or batched? For simplicity, one at a time is fine for now.
- After upload starts, show a processing state on the resource card — not a spinner on the whole page.

---

#### Resource List

**Requirement:**
- Each resource displayed as a row or card: file name (or domain for URLs), upload date, status (Processing / Ready / Failed).
- "Processing" state: shows a loading indicator. User does not need to wait — they can leave the page and come back.
- "Ready" state: resource is indexed and available to the agent.
- "Failed" state: shows a short error message and a retry option.
- Delete option on each resource.

---

#### Empty State

**Requirement:** When no resources exist, show a message explaining what resources do ("Your agent will use these materials to answer questions more accurately") and surface the upload options prominently.

---

---

### 8. Session Page `/session/[agentId]`

**Route group:** `(app)`

**Purpose:** The core product experience. The user talks to their agent, the agent draws on a canvas and speaks back.

**User enters from:** Clicking an agent card on the dashboard.

**User leaves to:** Dashboard (via back button), or browser close.

---

#### Layout

The session page has two primary regions:

1. **Canvas region** — takes the majority of the screen. The AI draws here during sessions.
2. **Interaction region** — a compact area (bottom or side) that contains the voice orb, controls, mode toggle, and chat interface.

The canvas should feel like the dominant surface. The controls should be minimal and unobtrusive — they exist to support the canvas, not compete with it.

---

#### Canvas Region (`svg-canvas.tsx`)

**Requirement:**
- Full-height canvas that the AI populates during session.
- Supports: drawing shapes, placing labels and equations, animating sequences step-by-step.
- The canvas should be empty at session start and fill as the conversation progresses.
- The student cannot draw on the canvas (it's the AI's workspace, not a collaborative whiteboard).
- Canvas clears between major topic shifts, or accumulates — this is a product decision. Recommend: AI controls clearing via a tool call, not automatic.
- Canvas must render correctly on mobile (no horizontal scroll — the drawing area scales to fit the viewport).

---

#### Voice Orb (`voice-orb.tsx`)

**Requirement:**
- The primary visual indicator of session state. Has four visible states:
  1. **Idle / not connected** — static, neutral appearance.
  2. **Listening** — animated to show it's capturing the user's speech.
  3. **Processing** — animated to show the AI is thinking.
  4. **Speaking** — animated to show the AI is talking back.
- Tapping/clicking the orb is the primary way to start/stop listening on mobile.
- The orb should be large enough to be a clear focal point, but not so large it competes with the canvas.

---

#### Waveform Visualizer (`waveform-visualizer.tsx`)

**Requirement:**
- Appears when the microphone is active.
- Visually represents the user's voice input in real time.
- Disappears or collapses when the mic is not active.
- Does not require explicit placement — should appear near the orb or within the control area.

---

#### Status Indicator (`status-indicator.tsx`)

**Requirement:**
- A small, always-visible indicator that shows the current connection state: Connecting / Connected / Disconnected / Error.
- Should not take up prominent screen space — a pill or badge near the top or near the controls.
- Color-coded (but colors must be accessible — not only red/green) with a text label.

---

#### Control Buttons (`control-buttons.tsx`)

**Requirement:**
- Mic toggle: mute/unmute the microphone. State must be visually unambiguous — user should always know if the mic is on.
- TTS toggle: enable/disable the AI's spoken voice output. When TTS is off, the AI still responds but only in text.
- Both controls are icon buttons. They must have accessible labels.
- Optionally: an end session / disconnect button.

---

#### Mode Toggle (`mode-toggle.tsx`)

**Requirement:**
- Switches between Voice mode and Chat mode.
- Voice mode: WebRTC active, mic live, orb visible.
- Chat mode: No WebRTC, text input visible, orb hidden or minimized.
- Switching mode does NOT end the session or clear the canvas — it only changes the input/output method.
- The toggle must be visually clear about the current mode.

---

#### Chat Interface (`chat-interface.tsx`)

**Requirement:**
- Visible only in chat mode (or as a secondary panel in voice mode).
- Message list: shows alternating student and AI messages in chronological order. Newest message at the bottom.
- Input field at the bottom with a send button.
- AI messages stream in word-by-word (not all at once). The streaming should feel natural, not jarring.
- Canvas updates still happen during chat mode — the AI can still draw based on text conversation.
- The message list scrolls independently of the canvas.

---

#### Session History Panel (`session-history-panel.tsx`)

**Requirement:**
- A panel (collapsible sidebar or slide-in drawer) that shows past sessions with this agent.
- Each past session listed as a row: date, duration, brief summary of topics discussed.
- Clicking a past session shows the summary (not a full replay).
- The mastery heatmap lives here too — a visual representation of which topics the student has practiced and at what depth.
- This panel is secondary — it should be collapsed by default on mobile.

---

#### Mastery Heatmap (`mastery-heatmap.tsx`)

**Requirement:**
- A visual grid or map that represents topic coverage and confidence level.
- Topics the student has discussed appear with some indication of depth/confidence.
- Topics not yet covered are visible but grayed out (so the student knows what's ahead).
- This does not need to be interactive — it's a progress visualization, not a navigation tool.
- Should be scannable at a glance — too much detail defeats the purpose.

---

#### Transcript List (`transcript-list.tsx`)

**Requirement:**
- Shows a real-time running transcript of the spoken conversation.
- Displayed inside the Technical Drawer (not on the main session page).
- Useful for reviewing what was said, especially when audio was unclear.

---

#### Technical Drawer (`technical-drawer.tsx`)

**Requirement:**
- A slide-in panel (from the bottom or side) accessible via a small icon button.
- Contains: transcript, pipeline logs, latency metrics.
- This is a power-user/developer tool. It must not be discoverable by accident for a student.
- Hidden by default, requires deliberate action to open.
- Three tabs inside: Transcript / Logs / Metrics.

---

---

### 9. Canvas Page `/canvas`

**Route group:** `(app)`

**Purpose:** Legacy standalone canvas session — no specific agent, no auth-gated resources. Useful for quick demos and development testing.

**Requirement:** Retains the same session page layout (canvas + controls) but without agent context, session history panel, or mastery heatmap. The user can speak and the AI draws on the canvas. No personalization.

**Status:** This may be deprecated once the agent-backed session is reliable. For now, keep it as a fallback/demo mode.

---

---

### 10. Observatory `/obs`

**Purpose:** Internal development dashboard for monitoring the pipeline.

**Requirement:**
- Shows real-time latency metrics for each stage of the pipeline: STT, VAD, LLM, TTS, WebRTC.
- Shows current session state (voice sessions active, chat sessions active).
- Not linked from any user-facing page. Accessed by direct URL.
- No authentication required (localhost only in production).

**Components used:** `metrics-grid.tsx`

---

---

## Components — Summary of Requirements

These are the shared components. Their requirements are described in context above within the pages that use them, but listed here for reference.

| Component | Used On | Core Requirement |
|---|---|---|
| `voice-orb` | Session | Visual state machine for session status. Four states: idle, listening, processing, speaking. |
| `svg-canvas` | Session, Canvas | AI-controlled drawing surface. Student cannot draw. Scales to viewport. |
| `chat-interface` | Session | Streaming message list with input. Used in chat mode. |
| `control-buttons` | Session | Mic on/off, TTS on/off. State must always be unambiguous. |
| `mode-toggle` | Session | Voice ↔ Chat switch. Doesn't reset session state. |
| `status-indicator` | Session | Connection status badge. Small, always visible, accessible. |
| `waveform-visualizer` | Session | Real-time mic input visualization. Only visible when mic is active. |
| `transcript-list` | Technical Drawer | Running spoken transcript. Not on main UI. |
| `technical-drawer` | Session | Slide-in developer panel with transcript, logs, metrics. Hidden by default. |
| `metrics-grid` | Observatory, Technical Drawer | Latency metrics for each pipeline stage. |
| `session-history-panel` | Session | Collapsible past-session list + mastery heatmap. Collapsed by default on mobile. |
| `mastery-heatmap` | Session History Panel | Topic coverage grid. Read-only, scannable, not interactive. |
| `canvas-renderer` | Session | Translates canvas operation events into SVG draw calls. Not a visible component — logic layer. |
| `murmur-doodles` | Landing, Dashboard | Decorative hand-drawn SVG elements for visual identity. Not interactive. |
| `glassmorphic-card` | Dashboard, Wizard | Card container with frosted-glass aesthetic. Contains agent cards, wizard steps. |
| `floating-button` | Session | Fixed-position button for secondary actions (e.g., opening the technical drawer). |
| `mode-toggle` | Session | Segmented control for voice/chat switch. |
| `theme-toggle` | Nav | Dark/light mode toggle. Persists preference. |

---

## Shared Layout Requirements

### `(marketing)` Layout

**Requirement:** Contains the top nav (logo + sign in + get started). No sidebar. Page content renders below the nav. The nav persists across all marketing pages.

### `(app)` Layout

**Requirement:** Wraps all authenticated pages. Checks auth on mount — if no valid session, redirects to `/login`. Does not show a nav bar by default (each page manages its own header). Provides a consistent auth context to all child pages.

### `(auth)` Layout

**Requirement:** Minimal wrapper. No nav, no footer. Just centers the auth form.

---

## User Flow Gaps to Address

These are places where the current page structure leaves the user without a clear path:

1. **Post-session flow:** When a session ends (user disconnects or closes browser), there's no confirmation or summary shown. The user should see a brief session summary before returning to the dashboard.

2. **Agent onboarding context:** After creating an agent, there's no prompt to add resources. A first-time user creates an agent and lands back on the dashboard with no indication that adding study materials would improve the experience.

3. **Resource processing feedback:** When a user uploads a PDF, they're not told how long processing takes. If they start a session before processing completes, the agent won't use the material. A clear "Your resource is still processing" notice in the session page would prevent confusion.

4. **Empty canvas explanation:** When a student first opens a session, the canvas is blank and the agent is waiting. There's no hint about how to start (speak a question, or type in chat mode). A one-time prompt like "Ask me anything — try 'explain Newton's third law'" would reduce the blank-canvas paralysis.

5. **Mobile voice session:** On mobile, the canvas is small after the controls take space. Consider collapsing the controls into a minimal bottom bar that expands on tap, giving the canvas maximum screen space during active tutoring.

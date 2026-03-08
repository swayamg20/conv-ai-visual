# Voice AI Canvas — MVP Spec
## For: 5-10 real students (first users the founder knows personally)
## Date: March 2026
## Status: CANONICAL — build from this

---

## The One-Line Hypothesis

"A student talks to a canvas-powered AI tutor, the tutor draws while it explains, and the next time they return it remembers where they left off."

If those 10 students come back for a second session, the MVP worked.

---

## Decision Point 0: Templates vs Custom Creation (MUST DECIDE BEFORE BUILDING)

For 5-10 known students, skip the creation wizard entirely.

**Reasoning:**
- You know who these students are. You know their subject and grade. You can hand-configure 5 agents in an afternoon.
- The creation wizard (user describes themselves -> meta-LLM call -> prompt generation -> resource selection) is 3-4 weeks of work and has high failure modes (bad prompt generation = bad agent).
- These 10 students don't need to "create" anything — they need a tutor that works right now.
- The "magic" they'll experience is the canvas + voice + memory, not the creation flow.

**Recommendation: Hand-configured agent templates per student. Zero creation wizard in MVP.**

The founder configures each agent directly in the database (or a simple admin page). Students just log in and their agent is ready.

---

## What Is IN the MVP

### 1. Minimal Auth
**What:** Google OAuth via NextAuth.js. One button. No email/password. No user settings page.
**Why:** You need a stable user_id to scope memory per agent per user. That's the only reason auth exists in MVP.
**Scope:** Login screen -> redirect to agent dashboard. That's it.
**What's NOT built:** User profile editing, password reset, email verification, sessions management UI.

### 2. Agent Entity + Per-Student Assignment
**What:** An `Agent` table in the DB. Each agent has: id, name, system_prompt, owner_user_id, created_at. The founder manually inserts 5-10 agent rows (one per student or subject). Each student's user account is linked to 1-2 agents.
**Why:** Agents need identity to scope memory. The system prompt on each agent IS the personalization — a well-crafted prompt for "CBSE 10th Physics student preparing for boards" does more than any wizard.
**What's NOT built:** Agent creation UI, agent editing UI, multi-agent management. All done directly in DB by founder.

### 3. Agent Dashboard (Simple List)
**What:** After login, student sees a list of their agents (likely just 1 or 2). Each shows: agent name, last session date (or "No sessions yet"). One button: "Start Session".
**Why:** Students need to navigate to their agent. This is a single screen with minimal UI.
**What's NOT built:** Agent settings, resource management, progress stats, session history detail.

### 4. Voice + Canvas Session (ALREADY BUILT — minimal wiring needed)
**What:** "Start Session" button opens the existing voice+canvas interface. The session is initialized with the agent's system_prompt instead of the current hardcoded LLM_CANVAS_SYSTEM_PROMPT.
**Why:** The entire voice pipeline, canvas, Scene Kit, TTS, memory system already works. This is just route/prompt injection.
**Changes needed:**
- Pass agent_id + agent's system_prompt to the session initialization endpoint
- Session URL: /session/[agent_id] or just open modal/drawer on the dashboard
**What's NOT built:** New session UI, multi-turn session management, session controls beyond what exists.

### 5. Cross-Session Memory Per Agent
**What:** The existing 4-layer memory system (ConversationContext, EpisodicMemory, SemanticMemory via Mem0, UserProfile) is currently scoped to user_id. Add agent_id as a second scope key.
**Why:** "The tutor remembers what we covered last session" is the core retention hook. Without this, every session starts cold.
**Changes needed:**
- `EpisodicMemoryModel`: add `agent_id` column (nullable, indexed)
- `EpisodicMemoryRepo.get_recent()`: filter by (user_id, agent_id)
- `SemanticMemory.add()` and `.search()`: pass `agent_id` to Mem0 (Mem0 supports this natively via `agent_id` param)
- `MemoryManager.__init__()`: accept agent_id, pass to all layers
- `LLMPipeline.__init__()`: accept agent_id, pass to MemoryManager
- Session endpoint: receive agent_id from frontend, initialize pipeline with it
**What's NOT built:** Memory visualization for students, memory editing, forgetting mechanism.

### 6. Session End Summary (Lightweight)
**What:** When a voice session ends (disconnect), automatically save an episodic summary of what was covered. The summary is LLM-generated from the conversation context at session close. It persists in EpisodicMemory under (user_id, agent_id).
**Why:** This is what makes the agent "remember" — the episodic context is injected into the system prompt on next session start.
**Changes needed:**
- On WebRTC disconnect, trigger session-end endpoint
- Server summarizes the last N messages: one LLM call, ~50-100 words, "Student covered [topics], struggled with [concept], understood [concept]."
- Save to EpisodicMemoryRepo with agent_id
**What's NOT built:** Showing this summary to the student, export, download.

---

## What Is EXPLICITLY OUT of MVP

These all sound important. None of them are needed for 10 students to find this valuable.

| Feature | Why it's OUT |
|---|---|
| Agent creation wizard | You configure agents manually for 10 students. The wizard is a growth-phase feature. |
| Resource ingestion / RAG | A well-crafted system prompt + web search is sufficient for proof of concept. RAG is v1.1. |
| PDF upload | Out. |
| Web search tool | Out for MVP — not wired in yet and adds complexity. Students will ask, agent will acknowledge limits. |
| Assessment / quiz mode | Separate product feature. Out. |
| PDF export | Out. |
| Background agent | Undefined scope. Out. |
| Session history view | Out — students don't need to browse sessions, they just need the agent to remember. |
| Share functionality | Out. |
| Agent settings / editing | Founder edits DB directly. |
| Multiple agents per student | Each student gets 1 agent. Adding a second is DB work by founder. |
| Notifications | Out. |
| Mobile | Out — desktop browser only. |
| User profile editing | Out. |
| Admin dashboard | Use existing /dashboard for logs. No new admin UI. |

---

## The Student User Journey (Step by Step)

**Preconditions:** Founder has created the student's Google account link and their agent row in DB.

**Step 1 — First visit**
Student goes to the URL. Sees a minimal landing/login screen. One button: "Sign in with Google". They sign in.

**Step 2 — Agent dashboard**
After sign-in, they see their agent card. Example: "CBSE 10th Physics" with subtitle "Your personal physics tutor". Last session: "Never". One button: "Start Session".

**Step 3 — Session starts**
Canvas + voice interface loads. Voice orb appears. The agent's system prompt is loaded silently. Student taps the voice orb (or clicks "Connect"). The session begins.
On first session: "Hi, I'm your physics tutor. What would you like to explore today?" (driven by system prompt)
On return session: The agent has episodic context injected. It opens with: "Welcome back. Last time we worked on Newton's second law — want to continue from there or try something new?"

**Step 4 — Session**
Student talks. Agent responds. Canvas draws diagrams (Scene Kit does its job). Student can interrupt. Voice orb changes states. This is the existing product — zero new work here beyond system prompt injection.

**Step 5 — Session ends**
Student clicks disconnect (or closes tab). Server detects WebRTC disconnect. Summary job runs: last N messages -> LLM call -> 80-word summary -> saved to EpisodicMemory with agent_id.
Student sees: "Session saved." (optional — even this is optional for 10 students).

**Step 6 — Return visit**
Student logs in again. Same dashboard. Clicks agent. Session starts. Agent greets them with context from last session. Student says "wow, it remembered." MVP validated.

---

## Technical Scope — What Gets Built vs What Exists

### Already Built (zero new work)
- Voice pipeline (WebRTC, STT, Smart Turn, interruption)
- LLM pipeline with 4-layer memory
- Canvas (Rough.js + GSAP + Scene Kit)
- Tool calling
- SSE streaming
- Chat interface
- Observability dashboard

### Net New Backend (Python / FastAPI)
1. **Auth integration** — NextAuth on frontend calls a `/api/auth/session` endpoint or uses JWT. Backend validates and extracts user_id from token. ~1 day.
2. **Agent model** — New `AgentModel` SQLModel table. Fields: id (uuid), name, system_prompt, owner_user_id, created_at. `AgentRepo` with get_by_owner, get_by_id. ~2 hours.
3. **Agent API** — `GET /api/agents` (list for current user), `GET /api/agents/{id}`. No create/update/delete in MVP (founder does that via DB). ~2 hours.
4. **Memory scoping** — Add agent_id to EpisodicMemoryModel and EpisodicMemoryRepo. Add agent_id param to SemanticMemory.add() and .search() (Mem0 already supports this). Update MemoryManager to accept agent_id. Update LLMPipeline to accept agent_id. ~4 hours.
5. **Session endpoint update** — Existing `/chat` and `/voice/offer` endpoints accept optional agent_id. If provided, load agent's system_prompt and pass agent_id to memory manager. ~2 hours.
6. **Session-end summary** — New endpoint `POST /api/sessions/{session_id}/end`. Takes session_id, looks up conversation context, calls LLM for 80-word summary, saves to EpisodicMemory. Call this from frontend on WebRTC disconnect. ~3 hours.

Total backend estimate: ~2 days of focused work.

### Net New Frontend (Next.js)
1. **Login screen** — `/login` route. Google OAuth via NextAuth. Minimal UI matching Murmur design. ~2 hours.
2. **Agent dashboard** — `/` redirects to `/agents` after auth. Single screen: agent cards with "Start Session" button. Pull from `/api/agents`. ~3 hours.
3. **Session route** — `/session/[agentId]` loads the existing voice+canvas interface but passes agentId to the WebRTC hook and backend. The existing `page.tsx` gets a thin wrapper that injects agentId into the useWebRTC/useChat hook options. ~2 hours.
4. **Auth gate** — Middleware protecting `/agents` and `/session/*`. Redirect to `/login` if unauthenticated. ~1 hour.
5. **Session-end call** — In `use-webrtc.ts` onDisconnect handler, fire `POST /api/sessions/{id}/end`. ~1 hour.

Total frontend estimate: ~1.5 days of focused work.

### Database Changes
- New table: `agents` (id, name, system_prompt, owner_user_id, created_at)
- Schema change: `episodic_memory` adds `agent_id` column (nullable, indexed)
- All existing data unaffected (existing sessions have agent_id = null, memory queries for null agent_id still work)

---

## Success Criteria (How You Know MVP Worked)

**Hard criteria — all must be true:**
1. A student logs in with Google and sees their agent within 10 seconds.
2. A student completes a voice+canvas session where the agent draws at least one diagram.
3. A student returns the next day and the agent references something from the previous session in its opening message.
4. At least 5 of the 10 students complete a second session within 7 days of the first.

**Soft criteria — signals to watch:**
- Students ask follow-up questions that only make sense if they're engaged ("wait, can you draw that again?")
- Students share screenshots of the canvas (they would only do this if it produced something impressive)
- Founder gets a message from a student outside the session ("can you add X topic?")

**The one metric that matters:** Did they come back?

---

## Decisions the Founder Must Make Before Building

### Decision 1: How do you manage agent configuration?
Two options:
- (A) Direct DB inserts. Founder writes SQL or uses a DB client. Fast to start, no UI needed.
- (B) Simple admin API endpoint: `POST /api/admin/agents` with a hardcoded admin token. One API call to create an agent.

Recommendation: Option B. Takes 1 extra hour and removes the need to touch the DB directly every time you want to tweak a prompt.

### Decision 2: What system prompts will you use for the initial 5-10 agents?
This is the most important product decision and it's NOT a tech decision. Before writing a line of code, write the 5-10 system prompts for the initial student agents. A prompt for "CBSE 10th Physics" needs to:
- Know the curriculum structure (chapters, topics, exam pattern)
- Have a teaching style that matches the student's level
- Know to use the canvas tools for specific topic types (draw force diagrams, graph velocity-time, etc.)

This is 2-3 hours of prompt engineering per agent. It determines whether students find the tutor useful. Do not skip it.

### Decision 3: Does each student get their own dedicated agent instance, or do multiple students share one agent template?
Two models:
- (A) One agent row per student. "Swayam's CBSE Physics". Memory is isolated per student naturally because it scopes to (user_id, agent_id).
- (B) One shared agent per subject. "CBSE 10th Physics". Memory must scope by (user_id, agent_id) to avoid students seeing each other's memory (already the case with the scoping changes above).

Recommendation: Model A for MVP. It's simpler mentally. If 5 students all study CBSE 10th Physics, create 5 identical agent rows, one per student. No shared-agent complexity.

### Decision 4: Where does this run during the test period?
Options:
- Local machine, students connect via ngrok tunnel
- Deploy to a VPS (DigitalOcean/Railway/Fly.io)

For 5-10 students: ngrok is fine during testing. SQLite + FastAPI on local machine handles it easily. Deploy only when you're ready to expand. Don't spend time on deployment infrastructure now.

---

## Build Order (Strict)

Follow this order. Do not parallelize — each step depends on the previous.

1. Write the 5-10 agent system prompts (before any code)
2. NextAuth Google OAuth — login works, user_id is stable
3. Agent table + repo + admin endpoint (B above)
4. Insert initial agent rows for test students
5. Agent dashboard UI (list + start session button)
6. Session route + agent_id injection into existing voice pipeline
7. Memory scoping changes (agent_id in episodic + semantic)
8. Session-end summary job
9. Test with one real student end-to-end

Total estimated time for solo dev: 5-7 focused days.

---

## The Thing That Will Make or Break This

It is not the auth. It is not the memory scoping. It is not the session-end summary.

**It is the quality of the system prompts.**

If a student asks "explain Newton's third law" and the agent gives a generic textbook answer with a generic diagram — they won't come back. If the agent gives a tight, board-exam-aware explanation, draws a force diagram that's clearly labeled for a 10th class student, and says "this is exactly the type of question that appeared in the 2023 CBSE board exam" — they will.

Invest disproportionately in the prompts. Everything else is plumbing.

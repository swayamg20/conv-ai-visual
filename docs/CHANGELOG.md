# Changelog

## 2026-03-28 — Firebase Auth Hardening, Tutoring Reliability, and Frontend Cleanup

**Branch:** `codex-multi-agent-execplan-2026-03-27`

This branch consolidated the active education-first workstream on top of the full-platform launch baseline, with a focus on authentication correctness, tutoring continuity, and shipping the tutoring UX without the old duplicate auth paths.

### Auth + Session Reliability
- Switched the web app to a single active Firebase auth flow and removed duplicate `/login` routing
- Removed unused frontend auth provider/context plumbing and kept the app on the hook-based Firebase path
- Required Firebase-backed identity on protected backend routes, including the voice `/offer` path
- Fixed frontend auth hydration races so protected requests wait for the Firebase user before calling the backend
- Moved frontend Firebase config out of hardcoded source into `web/.env.local` / `NEXT_PUBLIC_FIREBASE_*`
- Added backend Firebase project configuration and improved token-verification logging
- Made Firebase auto-provisioning compatible with the legacy `users` table by handling `password_hash` and duplicate-email reuse

### Tutoring + Memory Improvements
- Raised the default context window and added token-budget trimming for conversation history
- Hardened cross-session continuity by propagating `agent_id` into memory/pipeline state and validating resumed sessions
- Added mastery-aware tutoring context so prior weak areas inform later sessions
- Added tutoring-oriented session summaries and mastery extraction prompts
- Improved tutoring retrieval with SQL candidate filtering plus lexical reranking
- Added more explicit physics/canvas prompting for structured visual explanations

### Voice + Runtime Hardening
- Added session TTL cleanup, better disconnect cleanup, and best-effort summary persistence on abrupt ends
- Added ElevenLabs retry/backoff, Kokoro fallback, and observability metadata for TTS resilience
- Hardened SQLite for local concurrent usage with WAL, timeout, busy-timeout, and foreign-key pragmas

### Frontend + UX Cleanup
- Removed duplicate/unused auth pages and providers
- Fixed the session history / mastery panel auth path and layout issues
- Improved mastery tooltip behavior, readability, clipping, and edge-aware placement
- Added a shareable product brief and multiple ExecPlan documents for the ongoing roadmap

### Docs + Planning
- Added `AGENTS.md`, `.agent/PLANS.md`, and `plans/` ExecPlan scaffolding
- Rewrote `docs/NEXT_PLAN.md` around the education-first roadmap
- Updated setup and environment documentation for backend + frontend Firebase configuration

### Verification
- `python3 -m py_compile main.py funcs/*.py`
- `cd web && npx tsc --noEmit`

## 2026-03-11 — Post-Build Fixes + Next Plan

**Branch:** `feat/full-platform-launch`

After the initial build, fixed several integration issues discovered during testing:

### Bug Fixes
- **pymupdf import conflict** — `import fitz` conflicted with `frontend` package; changed to `import pymupdf`
- **passlib + bcrypt incompatibility** — dropped passlib, use `bcrypt` directly (hashpw/checkpw)
- **API base URL** — all frontend fetch calls were hitting Next.js at :3000 instead of FastAPI at :8000; added `API_BASE` constant (`NEXT_PUBLIC_API_URL` env var)pa
- **Token key mismatch** — backend returns `token` but frontend checked `access_token`; fixed in login + register pages
- **Route conflicts** — `(app)/page.tsx` + `(marketing)/page.tsx` both resolved to `/`; moved dashboard to `/dashboard`. Old observability dashboard moved from `/dashboard` to `/obs`
- **Agent creation payload** — frontend sent flat fields but backend expected `persona: {...}` wrapper; fixed payload structure
- **fetchAgents response parsing** — backend returns `{ agents: [...] }` but frontend expected bare array; now unwraps correctly
- **Error field handling** — backend returns `error` not `detail`; auth pages now check both

### Improvements
- Wizard steps 2-6 now skippable (only agent name is required)
- "Skip" button appears when a step has no selection

### Planning
- Created `docs/NEXT_PLAN.md` — 4-phase plan for core engineering & AI/ML quality
- CTO assessment identified 20 issues (5 P0, 7 P1, 8 P2)
- PM prioritized: context window, embedding search, adaptive difficulty, physics-specific prompts
- Creative feature: **Adaptive Difficulty Detection** — agent detects confusion in real-time and silently adjusts pace, vocabulary, and examples

---

## 2026-03-11 — Full Platform Launch Build

**Branch:** `feat/full-platform-launch`

Executed the entire launch roadmap in a single session using a 5-agent team (PM, CTO, Backend Engineer, Frontend Engineer, Voice Engineer). The PM added "Struggle Heatmap" as a creative differentiating feature.

### New Features

#### Auth System
- JWT auth with `python-jose` + `passlib`
- `UserModel` + `UserRepo` in `funcs/models.py`
- Endpoints: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- Rewritten `funcs/auth.py` with `get_current_user()` dependency injection
- Backward-compatible `get_current_user_id()` for existing endpoints

#### Agent CRUD
- `AgentModel` + `AgentRepo` in `funcs/models.py`
- New `funcs/agents.py` — prompt compiler (`compile_agent_prompt`) + capability-to-tool mapping
- Endpoints: `POST/GET/PUT/DELETE /api/agents`, `GET /api/agents/{id}`
- Agents wired into chat session — `agent_id` param loads system prompt + filters tools

#### Cross-Session Memory
- `SessionModel` + `ConversationMessageModel` + repos
- Messages persisted to DB on every turn
- Session summaries generated by LLM on session end
- Cross-session context: last 3 session summaries injected into new sessions
- Endpoints: `POST/GET /api/sessions`, `GET /api/sessions/{id}`, `POST /api/sessions/{id}/end`

#### Web Search
- Tavily integration in `funcs/search.py`
- Registered as LLM tool — agents with `web_search` capability get access
- Config: `TAVILY_API_KEY` in `funcs/config.py`

#### Resource Ingestion
- `ResourceModel` + `ResourceChunkModel` + repos
- `funcs/resources.py` — PDF parsing (pymupdf), URL extraction (httpx+BS4), text chunking
- Keyword search across chunks (SQLite LIKE)
- `search_resources` tool dynamically registered for agents with resources
- Endpoints: `POST/GET/DELETE /api/agents/{id}/resources`
- File uploads saved to `data/uploads/{user_id}/`

#### Struggle Heatmap (Creative Feature)
- `TopicMasteryModel` + `TopicMasteryRepo`
- Session-end flow extracts struggle signals via LLM (understood/struggled/unclear per topic)
- Endpoint: `GET /api/agents/{id}/mastery` — aggregated by topic + chapter
- Frontend: `mastery-heatmap.tsx` — color-coded concept map with chapter grouping

#### Landing Page
- `(marketing)/page.tsx` — hero, how-it-works (3 steps), feature highlights (4 cards), demo section, CTA
- Murmur design system: dark glassmorphic theme, hand-drawn SVG decorations, scroll-reveal animations
- `(marketing)/layout.tsx` — minimal nav with logo + auth buttons

#### Auth UI
- `(auth)/login/page.tsx` — email + password, glassmorphic card
- `(auth)/register/page.tsx` — name + email + password

#### Agent Dashboard
- `(app)/dashboard/page.tsx` — responsive card grid, skeleton loading, animated empty state
- Session history panel with tabbed interface (Sessions + Mastery Map)
- Agent cards with session count, meta info, action buttons

#### Agent Creation Wizard
- `(app)/agents/new/page.tsx` — 7-step flow with slide transitions
- Steps: name → subject → level → goals → learning style → icon → preview+confirm
- Subject/level/style have preset options + custom input

#### Session Routing
- `(app)/session/[agentId]/page.tsx` — agent-contextual voice+canvas
- Creates/resumes sessions via API
- Fires session-end on navigation/tab close (sendBeacon)

#### Resource Upload UI
- `(app)/agents/[agentId]/resources/page.tsx` — drag-drop + URL input
- Resource list with status badges, delete, size info

#### Visual Polish
- Skeleton loading states across dashboard, sessions, mastery
- Micro-interactions (hover scale, glow effects)
- Handwritten fonts (Caveat) for educational content
- Framer Motion page transitions
- `btn-glow`, `node-pulse`, `skeleton-shimmer` CSS utilities

### Route Structure

```
/                           Landing page (marketing)
/login                      Login form
/register                   Registration form
/dashboard                  Agent dashboard (auth required)
/agents/new                 Agent creation wizard
/agents/[id]/resources      Resource management
/agents/[id]/edit           Agent edit (placeholder)
/session/[agentId]          Voice+canvas session
/canvas                     Legacy canvas page
/obs                        Observability dashboard
```

### New Files (22)
- `.claude/agents/backend-engineer.md`
- `.claude/agents/frontend-engineer.md`
- `.claude/agents/voice-engineer.md`
- `funcs/agents.py`
- `funcs/resources.py`
- `funcs/search.py`
- `web/src/app/(app)/dashboard/page.tsx`
- `web/src/app/(app)/layout.tsx`
- `web/src/app/(app)/canvas/page.tsx`
- `web/src/app/(app)/agents/new/page.tsx`
- `web/src/app/(app)/agents/[agentId]/edit/page.tsx`
- `web/src/app/(app)/agents/[agentId]/resources/page.tsx`
- `web/src/app/(app)/session/[agentId]/page.tsx`
- `web/src/app/(auth)/login/page.tsx`
- `web/src/app/(auth)/register/page.tsx`
- `web/src/app/(marketing)/layout.tsx`
- `web/src/app/(marketing)/page.tsx`
- `web/src/components/mastery-heatmap.tsx`
- `web/src/components/session-history-panel.tsx`
- `web/src/hooks/use-auth.ts`
- `web/src/lib/api.ts`
- `web/src/lib/types.ts`

### Modified Files (9)
- `funcs/__init__.py` — exports for all new modules
- `funcs/auth.py` — full rewrite (JWT)
- `funcs/config.py` — JWT + Tavily config
- `funcs/memory.py` — message persistence, cross-session context
- `funcs/models.py` — 7 new tables + repos
- `main.py` — ~15 new endpoints, agent/session/resource integration
- `requirements.txt` — 5 new deps
- `web/src/app/globals.css` — new utility classes
- `web/tailwind.config.ts` — fade-in-up animation
- `web/src/hooks/use-chat.ts` — session_id support

### Deleted Docs (5)
- `docs/CLIENT_CHANGES_SUMMARY.md` — outdated interruption snapshot
- `docs/logo.md` — orphaned branding artifact
- `docs/decision_intelligence.md` — duplicate of ARTICLE version
- `docs/INTERRUPTION_CLIENT_EXAMPLE.md` — merged into CLIENT.md conceptually
- `docs/next_steps.md` — superseded by LAUNCH_STATUS.md

### New Dependencies
- `python-jose[cryptography]` — JWT
- `passlib[bcrypt]` — password hashing
- `tavily-python` — web search
- `pymupdf` — PDF parsing
- `beautifulsoup4` — HTML text extraction

### New DB Tables (7)
- `users` — user accounts
- `agents` — agent definitions + compiled prompts
- `sessions` — session tracking + summaries
- `conversation_messages` — persistent message history
- `resources` — uploaded PDFs/URLs metadata
- `resource_chunks` — chunked text for search
- `topic_mastery` — struggle heatmap signals

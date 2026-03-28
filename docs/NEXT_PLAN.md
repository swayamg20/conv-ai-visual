# Next Plan — Education-First Hardening

**Original created:** 2026-03-11
**Rewritten:** 2026-03-22
**Branch:** `feat/gstack-review`
**Product stance:** Stay education-first. Product-positioning experiments for engineers belong on a separate branch later.
**Timeline:** ~2 weeks of focused hardening + tutoring quality work

---

## Working Principle

No new product branches inside this branch until the current student experience is trustworthy.

That means:

1. A student can sign in and start a session reliably
2. The tutor does not forget the conversation after a few turns
3. The voice path is authenticated and safe
4. Sessions clean up properly and preserve memory even on disconnect
5. The tutor becomes visibly better at teaching physics before it becomes broader

---

## Current Reality

### Already in better shape

- Auth/login flow on the web app has been cleaned up on `feat/gstack-review`
- The duplicate `/login` route is gone
- Frontend auth is now using one active Firebase path instead of two competing ones
- The voice `/offer` path now requires Firebase-backed identity
- The branch already uses explicit CORS origins instead of a wildcard
- The conversation window is now larger than the old `5`-message cap
- Idle-session cleanup and abrupt-disconnect summaries now exist for chat and voice sessions
- SQLite has WAL-oriented connection hardening, and prompt assembly now applies a token budget
- The repo already has an education-oriented roadmap: NCERT/resource retrieval, mastery, physics visuals, adaptive tutoring

### Still blocking real student usage

- Session state is still kept in module-level dicts, so lifecycle behavior still deserves validation
- Abrupt disconnect handling exists, but it should be validated end-to-end
- SQLite is still sync and remains a scale/latency risk under concurrent usage
- Token-budget trimming exists, but end-to-end cross-session and prompt-budget verification still needs validation

---

## Goal For This Branch

Make Murmur good enough for real physics tutoring sessions with a small number of students:

- 10-20 minute sessions
- uploaded study material actually helps answers
- the tutor can draw the right kinds of diagrams for physics problems
- students do not hit obvious trust failures in auth, memory, or session stability

---

## Phase 1 — Must Fix Now

These are the items to do before adding more tutoring sophistication.

Status note: the branch already includes the core Phase 1 hardening work; the items below are retained as the original checklist, not as the next active queue.

### 1.1 Raise Context Window
- **Severity:** P0
- **Effort:** human ~1 hour / CC ~10 min
- Change `LLM_MAX_CONTEXT_MESSAGES` from `5` to `20`
- **Why:** This is the fastest, highest-leverage tutoring fix in the codebase. The agent forgetting after 3 exchanges invalidates nearly every student session.
- **File:** `funcs/config.py`
> Status: already landed on `feat/gstack-review`; keep this as historical context only.

### 1.2 Enforce Auth On WebRTC Voice Path
- **Severity:** P0
- **Effort:** human ~1 day / CC ~30-45 min
- Remove the unauthenticated `"default_user"` fallback from the `/offer` voice flow
- Require Firebase-backed user identity on the voice path, not just the REST path
- **Why:** Any unauthenticated client getting full LLM/TTS access is the wrong trust boundary
- **Files:** `main.py`, `funcs/auth.py`

### 1.3 Fix CORS For Actual Frontend Origins
- **Severity:** P0
- **Effort:** human ~30 min / CC ~10 min
- Replace wildcard CORS with explicit allowed origins from env
- Start with localhost and a configurable production origin list
- **Why:** The current config is not production-correct and should be fixed before broader testing
- **File:** `main.py`
> Status: already landed on `feat/gstack-review`; keep this as historical context only.

### 1.4 Add Session TTL Cleanup + Disconnect Cleanup
- **Severity:** P0
- **Effort:** human ~1 day / CC ~45-60 min
- Add idle-session eviction for in-memory voice/chat state
- Ensure WebRTC disconnect paths clear peer/session/task state consistently
- **Why:** Without cleanup, stability and memory behavior degrade over time
- **File:** `main.py`
> Status: already landed on `feat/gstack-review`; validate behavior end to end rather than re-implementing it.

### 1.5 Save Summaries On Abrupt Disconnect
- **Severity:** P0
- **Effort:** human ~1 day / CC ~20-30 min
- On disconnect/eviction, generate a lightweight summary if the session has enough messages
- Treat this as dependent on cleanup hooks from 1.4
- **Why:** Cross-session tutoring memory is not reliable if only explicit session-end saves state
- **Files:** `main.py`, `funcs/memory.py`
> Status: already landed on `feat/gstack-review`; the next task is verifying that those summaries are actually reused well.

### 1.6 SQLite WAL + Connection Hardening
- **Severity:** P0
- **Effort:** human ~1 day / CC ~45-60 min
- Enable WAL mode and reduce lock contention
- Keep this scoped to “safe enough for a small student pilot,” not a full async DB migration yet
- **Why:** This is the main persistence bottleneck before any small real-user rollout
- **File:** `funcs/models.py`
> Status: already landed on `feat/gstack-review`; the larger async DB migration remains deferred.

---

## Phase 2 — Improve Tutoring Quality

Do these after Phase 1 makes the product trustworthy.

### 2.1 Verify `agent_id` And Cross-Session Context End To End
- **Severity:** P1
- **Effort:** human ~2-4 hours / CC ~30-45 min
- Trace: session creation -> pipeline init -> memory init -> summary retrieval
- Add logs showing how much prior tutoring context was loaded
- **Why:** Memory is only useful if the right agent/session context actually reaches the tutor
- **Files:** `main.py`, `funcs/memory.py`, `funcs/llm_pipeline.py`

### 2.2 Add Token-Budget Enforcement
- **Severity:** P1
- **Effort:** human ~1 day / CC ~30 min
- Add real token trimming to `ConversationContext._trim()`
- **Why:** Raising the message count without a token guard trades one failure mode for another
- **Files:** `funcs/memory.py`, `funcs/llm_pipeline.py`
> Status: the first implementation is already in the branch; validate prompt assembly end to end before treating it as finished.

### 2.3 Physics-Specific Canvas Prompting
- **Severity:** P1
- **Effort:** human ~1 day / CC ~20-30 min
- Improve prompts for:
  - free-body style breakdowns
  - inclined planes
  - projectile motion
  - wave/function plots
  - step-by-step problem solving
- **Why:** Better visual pedagogy is higher value than adding more generic components right now
- **File:** `funcs/agents.py`

### 2.4 Better Resource Retrieval
- **Severity:** P1
- **Effort:** human ~3-4 days / CC ~2-3 hours
- Move beyond keyword search toward embedding-based retrieval for uploaded materials
- **Why:** Education quality depends heavily on the tutor actually using uploaded class resources
- **Files:** `funcs/resources.py`, `funcs/models.py`

### 2.5 TTS Retry + Fallback
- **Severity:** P1
- **Effort:** human ~1 day / CC ~30 min
- Add retry/backoff and graceful fallback when ElevenLabs fails
- **Why:** Voice tutoring should degrade gracefully, not die on a TTS provider error
- **File:** `funcs/tts_pipeline.py`

---

## Phase 3 — Make It Feel Like A Real Tutor

Do these after the system is stable and teaching quality is materially improved.

### 3.1 Adaptive Difficulty Detection
- Detect confusion signals during the session
- Slow speech slightly, simplify examples, and adjust explanation style
- **Files:** `main.py`, `funcs/llm_pipeline.py`, `funcs/tts_pipeline.py`

### 3.2 Mastery-Aware Prompting
- Inject prior mastery/struggle data when a tutoring session starts
- **Files:** `funcs/agents.py`, `main.py`

### 3.3 Structured Session Summaries
- Store machine-usable tutoring summaries, not just free-form text
- **File:** `main.py`

### 3.4 Session Quality Metrics
- Track session duration, canvas usage, tool usage, response patterns
- **Files:** `main.py`, `web/src/app/obs/page.tsx`

---

## Phase 4 — Codebase Cleanup After Product Fit Improves

These matter, but they are not the next thing to do.

### 4.1 Async DB Layer
- Full async migration across repo classes
- **Why deferred:** important, but larger blast radius than the hardening fixes above
- **Reference:** `TODOS.md`

### 4.2 Split `main.py`
- Extract routers and voice pipeline modules
- **Why deferred:** helpful for maintainability, but not the fastest path to better tutoring sessions

### 4.3 Input Validation And Guardrails
- Message length limits, upload size limits, sanitization

### 4.4 Dead Code Cleanup
- Finish removing obsolete pathways after the branch stabilizes

---

## Explicitly Deferred

These are valid ideas, but not for this branch right now.

- Engineer/architect positioning experiments
- Copy rebrand away from education
- New broad feature bets unrelated to tutoring quality
- Share/export features
- Postgres migration
- Large architecture refactors before the tutoring loop is stable

If explored, do them in a separate branch.

---

## What To Pick Up Right Now

### Recommended Next Task

Pick up a single **P1 tutoring-quality batch**:

1. `agent_id` / cross-session context verification
2. prompt-budget validation
3. physics-specific canvas prompting

**Why this batch first:**
- It checks whether the memory work is actually reaching the tutor
- It turns the already-landed hardening into visible tutoring quality
- It sets up the next batch cleanly: resource retrieval and fallback polish

### Recommended Order After That

1. Better resource retrieval
2. TTS retry + fallback
3. Mastery-aware prompting
4. Structured session summaries
5. Session quality metrics

---

## Success Criteria

This branch is in good shape when all of these are true:

1. A student can sign in, start voice tutoring, and stay authenticated throughout the session
2. The tutor can sustain a useful 10-20 minute conversation without obvious forgetting
3. Disconnects do not silently destroy all tutoring memory
4. Uploaded learning material improves responses in a noticeable way
5. The tutor produces appropriate diagrams for common physics problems
6. A small student pilot can run without obvious auth, stability, or memory failures

---

## Notes

- This plan supersedes the older March 11 version where it conflicts with the current Firebase-based auth flow
- `TODOS.md` remains the parking lot for larger follow-up items that should not block immediate education-first hardening

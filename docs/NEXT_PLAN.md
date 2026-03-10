# Next Plan — Core Engineering & AI/ML Quality

**Created:** 2026-03-11
**Focus:** Fix core engineering, harden for real usage, improve AI/ML quality
**Timeline:** ~2.5 weeks
**Principle:** No new features until the existing ones work properly for 10 real students

---

## Phase 1 — Critical Fixes (Days 1-3)

These will break under real multi-user usage. Fix before any student touches it.

### 1.1 JWT Secret Validation
- **Severity:** P0 | **Effort:** 30 min
- `JWT_SECRET` defaults to `""` — trivially forgeable
- Add startup validation: raise if JWT_SECRET is empty or < 32 chars
- **File:** `funcs/config.py`

### 1.2 CORS Lockdown
- **Severity:** P0 | **Effort:** 30 min
- `allow_origins=["*"]` with `allow_credentials=True` is a spec violation
- Lock to actual frontend origins (`http://localhost:3000`, production URL)
- **File:** `main.py` lines 87-93

### 1.3 Raise Context Window
- **Severity:** P0 | **Effort:** 1 hour
- `LLM_MAX_CONTEXT_MESSAGES = 5` — agent forgets after 3 exchanges
- Raise to 20. This is the highest-leverage 1-line fix in the codebase.
- **File:** `funcs/config.py`

### 1.4 SQLite WAL Mode + Connection Fix
- **Severity:** P0 | **Effort:** 1 day
- Every repo opens a new `Session(engine)` per operation — "database is locked" under 10 users
- Enable WAL mode: `engine.execute("PRAGMA journal_mode=WAL")`
- Use connection pooling with `StaticPool` or single-writer pattern
- **File:** `funcs/models.py`

### 1.5 Verify agent_id Flows End-to-End
- **Severity:** P0 | **Effort:** 2-4 hours
- Trace: session creation → LLMPipeline init → MemoryManager init
- Verify cross-session context (session summaries) is actually injected
- Add logging: "Cross-session context loaded: N sessions, M characters"
- **Files:** `main.py`, `funcs/memory.py`, `funcs/llm_pipeline.py`

### 1.6 Session Cleanup + TTL Eviction
- **Severity:** P0 | **Effort:** 1 day
- `chat_sessions`, `voice_sessions`, `datachannels` etc. are module-level dicts that never get cleaned up
- Add TTL-based eviction (e.g., 2 hours idle) and proper cleanup on WebRTC disconnect errors
- **File:** `main.py`

### 1.7 Auth on WebRTC Voice Path
- **Severity:** P0 | **Effort:** 1 day
- `/offer` endpoint uses `peer_user_ids.get(pc_id, "default_user")` — any unauthenticated client gets full LLM/TTS access
- Require JWT validation on the offer endpoint
- **File:** `main.py`

---

## Phase 2 — AI/ML Quality (Days 4-10)

These transform the product from "demo" to "usable for real students."

### 2.1 Embedding-Based Resource Search
- **Severity:** P1 | **Effort:** 3-4 days
- Replace SQLite LIKE with vector similarity search
- On ingest: embed each chunk with `text-embedding-3-small` (OpenAI) or local model
- Store vectors as JSON in `resource_chunks` table (fine at small scale)
- At search time: embed query, cosine similarity in Python
- **Why:** Student asks "conservation of momentum" → NCERT chunk says "linear momentum is conserved in an isolated system" → keyword search misses, embedding search finds it
- **Files:** `funcs/resources.py`, `funcs/models.py`

### 2.2 Physics-Specific Canvas Prompt Engineering
- **Severity:** P1 | **Effort:** 1 day
- Current canvas prompt is domain-agnostic
- Add explicit guidance for physics:
  - `right_triangle` for inclined plane problems
  - `coordinate_plane` for projectile motion (label axes)
  - `function_plot` for wave functions
  - `flowchart` for problem-solving steps
- Include 3-4 worked examples in the system prompt
- **File:** `funcs/agents.py` (compile_agent_prompt)

### 2.3 Smarter Model Router
- **Severity:** P1 | **Effort:** 1-2 days
- Current regex patterns miss most complex queries and false-positive on simple ones
- Options: (a) expand regex patterns for physics/math, (b) LLM-based routing call on Groq (fast enough), (c) SDL complexity signal (>4 steps → GPT-4o)
- **File:** `funcs/model_router.py`

### 2.4 TTS Retry + Fallback
- **Severity:** P1 | **Effort:** 1 day
- No retry on ElevenLabs 429/5xx — exception kills the turn
- Add retry with exponential backoff
- Auto-fallback to Kokoro local when cloud fails
- **File:** `funcs/tts_pipeline.py`

### 2.5 Async Memory Writes
- **Severity:** P1 | **Effort:** 1 day
- `semantic.add()` in `process_for_memory()` is sync — blocks event loop
- Wrap with `asyncio.to_thread()` or fire-and-forget background task
- **File:** `funcs/memory.py`

---

## Phase 3 — Adaptive Intelligence (Days 11-17)

The features that make this feel like a real tutor, not just a chatbot.

### 3.1 Adaptive Difficulty Detection
- **Severity:** P1 | **Effort:** 3-4 days
- Detect struggle signals in real-time (not just at session end):
  - Student asks same concept rephrased
  - Short responses ("wait, what?", "hm", "can you repeat that")
  - Long pauses before responding
- When detected:
  - Slow TTS speed by 15% (ElevenLabs parameter)
  - Inject system prompt: "Student seems confused. Simplify your next response. Use a concrete numerical example."
  - Drop vocabulary complexity
- **Why:** A good human tutor reads the room. This is the AI equivalent. No one else has it.
- **Files:** `main.py` (voice pipeline), `funcs/llm_pipeline.py`, `funcs/tts_pipeline.py`

### 3.2 Mastery-Aware Prompting
- **Severity:** P1 | **Effort:** 2 days
- Inject the student's mastery data into the system prompt at session start
- "This student has struggled with: Free Body Diagrams, Friction on Inclined Planes. They have mastered: Newton's First Law, Newton's Third Law. Proactively revisit weak areas when relevant."
- **Files:** `funcs/agents.py`, `main.py` (session creation)

### 3.3 Structured Session Summaries
- **Severity:** P2 | **Effort:** 1 day
- Current summary is free-form text — hard to parse for cross-session context
- Use structured extraction: topics covered, mastery signals, unresolved questions, next session suggestions
- Store as JSON alongside the text summary
- **File:** `main.py` (end_session endpoint)

### 3.4 Session Quality Metrics
- **Severity:** P2 | **Effort:** half day
- After every session, auto-log: canvas usage count, average response length, tool call count, session duration
- Feed into observability dashboard
- **File:** `main.py`, `web/src/app/obs/page.tsx`

---

## Phase 4 — Code Quality (Ongoing)

### 4.1 Split main.py
- **Effort:** 3-5 days
- Extract into: `api/auth.py`, `api/agents.py`, `api/sessions.py`, `api/resources.py`, `voice/pipeline.py`, `voice/webrtc.py`
- Use FastAPI routers

### 4.2 Async DB Layer
- **Effort:** 2-3 days
- Wrap all repo calls with `run_in_executor` or switch to aiosqlite + async_session

### 4.3 Input Validation
- **Effort:** 1-2 days
- Max length on chat messages, file upload size limits, type validation, XSS sanitization on user-generated content

### 4.4 Dead Code Cleanup
- **Effort:** 1 day
- `InterruptionManager` in `funcs/interruption.py` is unused (actual mechanism uses `tts_interrupt_flags` dict)
- `datetime.utcnow()` → `datetime.now(timezone.utc)` across all models

---

## What to SKIP Right Now

| Feature | Why Skip |
|---------|----------|
| Assessment/quiz mode | Separate product. Fix the tutor first. |
| PDF export | No users asking for it yet. |
| Agent creation wizard improvements | Hand-configure agents for 10 students. |
| Multi-TTS failover engineering | Kokoro backup is enough for now. |
| PostgreSQL migration | SQLite is fine for 10 users (with WAL mode). |
| Share functionality | Students need to value it themselves first. |
| New canvas components (circuits, ray diagrams) | Nice-to-have, but prompt engineering has more impact first. |

---

## Success Criteria

After completing Phases 1-3, these should be true:

1. A student can have a 20-minute physics session without the agent forgetting earlier context
2. Uploaded NCERT PDFs actually get used when a student asks about a topic
3. The agent draws appropriate diagrams for physics problems (not generic shapes)
4. Hard questions route to a capable model, easy questions stay fast
5. If TTS fails, voice gracefully falls back to local TTS
6. Sessions clean up properly — no memory leaks after 100 sessions
7. The agent subtly adapts when a student is confused
8. The mastery map reflects the student's actual understanding after 3+ sessions

---

*Source: CTO assessment + PM analysis, 2026-03-11*

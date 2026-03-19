# TODOS

Deferred items from engineering review, 2026-03-19.
Branch at time of review: `feat/full-platform-launch`

---

## TODO-1: Async DB Layer
**What:** Replace synchronous SQLAlchemy sessions with aiosqlite + async SQLModel sessions across all repo classes.
**Why:** Every DB call currently blocks the async event loop. Fine for 10 users. Will cause cascading latency under real load (50+ concurrent sessions). Symptoms: slow LLM responses, piling up async tasks.
**Pros:** Fully non-blocking I/O; correct async architecture.
**Cons:** Multi-file refactor across all repo classes (~15 static methods). Risk of introducing subtle transaction scope bugs during migration.
**Context:** `funcs/models.py:16` creates a sync engine. `get_session()` returns a sync `Session`. All repo methods use `with get_session()` pattern. The WAL mode fix (this PR) reduces locking issues but doesn't fix the event loop blocking.
**Depends on / blocked by:** WAL mode PR (this sprint). Effort: human ~3 days / CC ~2 hours.

---

## TODO-2: Token-Budget Enforcement in Context Window
**What:** Add token counting to `ConversationContext._trim()` so that long sessions don't silently exceed LLM token limits.
**Why:** `ConversationContext` trims by message count (now 20) but not by tokens. System prompt + semantic/episodic context injections + 20 messages can exceed model limits on long sessions. When exceeded, the LLM API returns an error; the pipeline catches it silently and the user gets no response.
**Pros:** Prevents silent failures on long sessions; makes context management deterministic.
**Cons:** Token counting requires a tokenizer (e.g., `tiktoken`), adds a dependency and per-message overhead.
**Context:** `funcs/memory.py:27` — `ConversationContext` has `max_tokens=4000` as a field but never uses it (no token counting logic). `funcs/llm_pipeline.py:163` builds context without token guard. Groq's Llama-3.3-70b context window is 128k tokens, so this is low risk at current scale but a real failure mode at scale.
**Depends on / blocked by:** None. Effort: human ~1 day / CC ~30 min.

---

## TODO-3: Session Summary on Abrupt Disconnect
**What:** Trigger a lightweight session summary when a chat or voice session ends unexpectedly (browser crash, network loss, WebRTC disconnect) — not just on explicit `DELETE /session/{id}`.
**Why:** Cross-session memory depends on summaries. Currently, summaries only generate on clean session close. Any dropped tab, crashed browser, or network failure loses the entire session's context permanently.
**Pros:** Memory becomes reliable; agent can reference previous sessions even if the user never clicked "end session."
**Cons:** Adds async LLM call on session teardown. Edge case: session with no messages shouldn't trigger a summary call.
**Context:** `main.py:1519` — `end_session` endpoint is the only summary trigger. WebRTC disconnect cleanup at `main.py:1744-1754` and TTL eviction (this PR) are the natural hooks. Best approach: fire-and-forget background task on eviction/disconnect, only if `len(messages) > 2`.
**Depends on / blocked by:** TTL eviction implementation (this PR). Effort: human ~1 day / CC ~20 min.

---

## TODO-4: CORS Origin Lockdown
**What:** Replace `allow_origins=["*"]` with explicit allowed origins once the production URL is known.
**Why:** Wildcard CORS without `allow_credentials` is safe for a public API, but best practice is to enumerate allowed origins. Prevents any web page from making credentialed calls if `allow_credentials` is accidentally re-added.
**Pros:** Defense-in-depth; explicit is better than implicit.
**Cons:** Requires knowing the prod URL. Must be updated on every domain change.
**Context:** `main.py:99-105` — CORS middleware. The `allow_credentials=True` bug is fixed in this PR. This TODO is for the full lockdown once deployed.
**Depends on / blocked by:** Needs prod deployment URL. Effort: human ~15 min / CC ~2 min.

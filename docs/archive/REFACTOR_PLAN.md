# Refactor Plan

**Date:** 2026-03-12
**Branch:** feat/full-platform-launch
**Scope:** Full repo — Python (FastAPI/SQLModel) + TypeScript/Next.js
**Status:** Planning

---

## Progress Legend
- `[ ]` Not started
- `[~]` In progress
- `[x]` Done
- `[skip]` Skipped (reason noted)

---

## File-by-File Plan

---

### 1. `funcs/models.py` — High Impact ✅ Done (2026-03-12)

**Issues:**
- [x] Replace `Optional[X]` with `X | None` throughout (~20+ occurrences)
- [x] Extract reusable `_load_json()` helper — `json.dumps/loads` duplicated on 10+ fields
- [x] Remove unused `Tuple`, `Dict`, `List`, `Optional` imports — replaced with built-in generics
- [skip] Repo pattern standardization — already all static-method, no mixed pattern found

**Verified:** `python -c "import funcs.models"` passes on Python 3.11 (conda voiceai env)

---

### 2-bis. Phase 2 Small Files ✅ Done (2026-03-12)

- [x] `funcs/tools.py` — removed `typing` imports, added `collections.abc.Callable`, fixed malformed JSON parse in `OpenAIAdapter.parse_tool_calls()` (try/except per tool call), all `Dict/List/Optional` → built-ins
- [x] `funcs/auth.py` — removed `typing` + unused `JSONResponse` import, fixed `get_current_user()` return type to `UserModel | None`, clarified `"default_user"` fallback comment
- [x] `funcs/resources.py` — moved `import os` to module top, removed `typing`, `List[X]` → `list[X]`
- [x] `funcs/agents.py` — removed unused `import json` + `typing`, all signatures updated
- [x] `funcs/search.py` — moved `ToolRepo` import to module top, fixed `logger.error()` to structured logging with `exc_info=True`

**Verified:** all 5 files import cleanly on Python 3.11 (conda voiceai env)

---

### 3. `main.py` — Biggest Structural Debt ✅ Done (2026-03-12)

**Issues:**
- [x] All `Dict[X, Y]` / `Optional[X]` → built-in generics + `X | None` throughout (Pydantic models, global dicts, route signatures, local vars)
- [x] `import time as _time` (mid-file) → hoisted to top; all `_time.perf_counter()` → `time.perf_counter()`
- [x] Hoisted 8 in-method imports: `import re`, `import base64`, `import uuid`, `from uuid import uuid4`, `from collections.abc import Sequence`, `from funcs.model_router import route_model`, `from funcs.models import LLMCallLogRepo, VoicePipelineLogRepo`, `from funcs.llm_clients import create_llm_client`
- [x] `logger.error(f"...")` → lazy `%s` formatting
- [skip] Extract global state dicts into `SessionManager` — high blast radius, all 15 route handlers reference them; separate initiative
- [skip] Split into FastAPI routers — depends on SessionManager being stable first; separate initiative
- [skip] Return type annotations on all route handlers — would not affect runtime behavior; deferred

**Verified:** `python -c "from main import app"` passes on Python 3.11 (conda voiceai env)

---

### 3. `funcs/llm_pipeline.py` — Logic Complexity ✅ Done (2026-03-12)

**Issues:**
- [x] Hoisted `ToolResult`, `canvas_update`, `teach_with_visuals` from method bodies to module top
- [x] Extracted `_build_context_and_tools()` — removes duplicated preamble from both stream methods
- [x] `_SUMMARY_RECENT_MESSAGES = 10` constant replaces magic `n=10` in `generate_session_summary()`
- [x] All `List/Dict/Optional/Tuple/Callable/AsyncGenerator` → built-in generics + `collections.abc`
- [x] All 15 `logger.info(f"...")` f-strings → lazy `%s` formatting
- [skip] `self.memory.context.max_messages` setter — no API for it; direct assignment is safe
- [skip] Further stream method breakup — already delegated via `_execute_tool_calls_with_policy`

**Verified:** `python -m py_compile` clean. Pre-existing `funcs/__init__.py` ↔ `funcs.memory` circular import is unaffected.

---

### 4. `funcs/tools.py` — Type Safety ✅ Done (2026-03-12)

**Issues:**
- [x] Replace `from typing import Dict, List, Callable, Any, Optional` with built-in generics (`dict`, `list`, `collections.abc.Callable`) — line 9
- [x] Add try/except around `json.loads(tool_call.function.arguments)` for malformed LLM output — line 31
- [skip] Add explicit return types to all functions — deferred (no correctness risk)

---

### 5. `funcs/llm_clients.py` — Robustness ✅ Done (2026-03-12)

**Issues:**
- [x] `(content or "").strip()` — None guard for tool-call responses
- [x] `base_url.split("//")` wrapped in try/except for malformed URLs
- [x] `OpenAIClient.parse_tool_calls()` — bare `json.loads` → per-call try/except + warning log
- [x] `from funcs.tools import ToolCall` and `from funcs.config import config` hoisted to module top (removed 4 deferred imports)
- [x] `List/Dict/Optional` → built-in generics + `collections.abc.AsyncGenerator`

**Verified:** imports cleanly on Python 3.11 (conda voiceai env)

---

### 6. `funcs/auth.py` — Correctness ✅ Done (2026-03-12)

**Issues:**
- [x] Change `get_current_user()` return type from `Optional[UserModel]` to `UserModel | None` — line 56
- [x] Document (or remove) `"default_user"` silent fallback in `get_current_user_id()` — line 83

---

### 7. `funcs/resources.py` — Minor ✅ Done (2026-03-12)

**Issues:**
- [x] Move `import os` from inside function body to module top — line 61
- [skip] Add type hints to `_chunk_text()` and `ingest_*()` return values — deferred

---

### 8. `funcs/agents.py` — Minor ✅ Done (2026-03-12)

**Issues:**
- [skip] Add missing return type annotation to `compile_prompt()` — deferred (no correctness risk)

---

### 9. `funcs/search.py` — Minor ✅ Done (2026-03-12)

**Issues:**
- [x] Replace f-string in `logger.error(f"...")` with `logger.error("...", exc_info=True)` for structured logging — line 29

---

### 10. `web/lib/api.ts` — High DRY Impact ✅ Done (2026-03-12)

**Issues:**
- [x] Extracted `request<T>(path, opts)` base function — eliminated 9 repeated `fetch → if (!res.ok) throw → .json()` patterns
- [x] Fixed `.json().catch(() => ({}))` anti-pattern — `request()` now tries to parse error body and falls back to status code message; `uploadResource` uses same pattern inline (multipart, kept separate)
- [skip] TypeScript return types on exported functions — already present on all signatures

**Verified:** `cd web && npx tsc --noEmit` clean

---

### 11. `web/hooks/use-chat.ts` — Type Safety + Correctness ✅ Done (2026-03-12)

**Issues:**
- [x] Defined `SSEEvent` discriminated union — all 6 event types typed; `JSON.parse()` result cast to `SSEEvent`; `onSDLScene` typed with `SDLScene` (imported from `scene-kit`)
- [x] Silent `catch { // Ignore parse errors }` → `console.warn("[Chat] Failed to parse SSE event:", line)`

---

### 12. `web/hooks/use-audio.ts` — Cleanup ✅ Done (2026-03-12)

**Issues:**
- [x] Removed all 11 `console.log()` debug calls; kept only `console.error` for actual errors; removed orphaned `t0`/`sessionId` variables
- [x] Added `useEffect` cleanup on unmount — stops all scheduled sources and closes `AudioContext` to prevent memory leak

---

### 13. `web/hooks/use-auth.ts` — Minor ✅ Done (2026-03-12)

**Issues:**
- [x] Added `console.warn` for missing `NEXT_PUBLIC_API_URL` (client-side only, wrapped in `typeof window !== "undefined"`)
- [x] Fixed `/api/auth/me` response type — backend returns `{ user: User }` not `User` directly; was `setUser(data)` → `setUser(data.user)`

---

### 14. `web/components/svg-canvas.tsx` — Size ✅ Done (2026-03-12)

**Issues:**
- [x] Extracted pure utility functions to `lib/canvas-utils.ts` (109 lines): `getCanvasPalette`, `snap`, `snapPoints`, `GRID_SNAP`, `GRID_EXTENT`, `renderGrid` — no React dependencies, safe extraction; component reduced 1611 → 1520 lines
- [x] Added GSAP cleanup `useEffect` on unmount — kills all timelines in `timelinesRef` and `sequenceQueueRef` (memory leak fix)
- [skip] `createSequence`/`createPausedSequence` dedup (~170 lines each, near-identical switch) — behavior risk too high without test coverage; separate initiative
- [skip] `normalizeSteps`/`normalizeOp` extraction — depend on `TeachingStep`/`CanvasOperation` types that would require type file restructuring
- [verified] Untyped sections: `AnimationOperation.properties: { [key: string]: any }` is intentional (GSAP accepts arbitrary CSS); `SVGElementData.data: any` is heterogeneous storage; `(op as any)._centered` is a private flag — all are acceptable

**Verified:** `cd web && npx tsc --noEmit` clean

---

### 15. `web/app/session/[agentId]/page.tsx` — Minor ✅ Done (2026-03-12)

**Issues:**
- [x] Added cancellation flag to agent fetch `useEffect` — prevents `setAgent`/`setAgentLoading` firing after unmount if `agentId` changes
- [x] Added GSAP timeline cleanup `useEffect` — kills all timelines in `stepTimelinesRef` on unmount (memory leak fix)
- [skip] Error handling for agent fetch — already present (renders error card with "Back to Dashboard" link)

---

### 16. `web/app/dashboard/page.tsx` — Minor ✅ Done (2026-03-12)

**Issues:**
- [x] Replaced `confirm()` with inline confirmation state (`confirmDeleteId`) — first click shows "Delete / ✕" buttons on the card; no browser dialog

---

## Suggested Execution Order

| Phase | Files | Rationale |
|-------|-------|-----------|
| 1 | `funcs/models.py` | Foundation — other files import from it |
| 2 | `funcs/tools.py`, `funcs/auth.py`, `funcs/resources.py`, `funcs/agents.py`, `funcs/search.py` | Small, isolated fixes |
| 3 | `funcs/llm_clients.py` | Depends on models being clean |
| 4 | `funcs/llm_pipeline.py` | Depends on tools + clients being clean |
| 5 | `main.py` | Biggest change — do after backend modules are stable |
| 6 | `web/lib/api.ts` | Frontend foundation — hooks depend on it |
| 7 | `web/hooks/use-chat.ts`, `use-audio.ts`, `use-auth.ts` | After api.ts is refactored |
| 8 | `web/app/session/page.tsx`, `dashboard/page.tsx` | Page-level cleanups |
| 9 | `web/components/svg-canvas.tsx` | Last — highest risk |

---

## Skipped / Out of Scope

- `funcs/canvas.py`, `funcs/tts_pipeline.py`, `funcs/vad_gate.py`, `funcs/smart_turn.py`, `funcs/model_router.py`, `funcs/kokoro_tts.py`, `funcs/audio_to_base64.py` — not yet audited; revisit after Phase 1-5
- Test coverage — separate initiative, not part of this refactor
- Logging service infrastructure — would require new tooling decision

---

## Notes

- All Python type changes target Python 3.10+ syntax (`X | None`, built-in generics)
- No behavior changes — this is purely structural/type/DRY refactoring
- Verify with `python -c "from funcs.[module] import *"` after each Python file
- Verify TypeScript with `cd web && npx tsc --noEmit` after each TS batch

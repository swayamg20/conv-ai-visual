# CLAUDE.md - Voice AI

Real-time voice assistant: WebRTC → Deepgram STT → Silero VAD → LLM (OpenAI/Gemini) → ElevenLabs TTS → Audio Response

## Launch Status

**The product has NOT launched.** See `docs/LAUNCH_STATUS.md` for the full launch readiness tracker — what's built, what's missing, launch criteria, and blockers. All agents (CEO, CTO, PM) must read it before making roadmap or prioritization decisions.

## Working Style

- **Clarify before building.** When a feature can be implemented multiple ways, present 2-3 approaches in bullet points and wait for approval before writing code. Prefer the simplest approach unless told otherwise. Do not assume complex solutions (git-based sync, static generation, extra abstraction layers) when a direct API or DB call would work.
- **Incremental over monolithic.** Break multi-file changes into small verified steps. After each step, confirm imports resolve and the server starts before moving on.

## Integration Guidelines

- **Before integrating a new library**, check for known conflicts with the existing stack: argparse hijacking (breaks uvicorn), `__bool__`/`__len__` overrides (breaks conditionals), signal handler conflicts, and global state mutations. Run a minimal import smoke test first.
- **Check system dependencies** (LaTeX, ffmpeg, etc.) before assuming code will run. If a dep is missing, flag it immediately rather than debugging cryptic errors.
- **Manim specifically** — imports hijack argparse; must be isolated (lazy import or subprocess). Its Mobject classes override `__bool__`/`__len__`, so never use `if mob:` — use explicit checks instead.

## Code Style Rules

- **Python imports at the top of the file.** Never add import statements in the middle of a function or file. All imports go at the top, grouped: stdlib → third-party → local.
- **Async everywhere.** All I/O operations use `async def`. Use `httpx` not `requests`.
- **Errors:** `logger.error()` for logging. Return error messages from tools, don't raise. Graceful degradation over hard failures.
- **Config:** All env vars go in `funcs/config.py` (pydantic-settings). Never hardcode API keys.
- **New modules** go in `funcs/`, follow existing patterns, update `funcs/__init__.py` exports.
- **DB models** in `funcs/models.py` (SQLModel). Repos use static methods.
- **Tools** use `ToolRepo.upsert()` — see `docs/TOOLS.md`. Two modes: inline code (DB) or module-based handlers.
- **Tool execution** is sandboxed via RestrictedPython (`funcs/tool_executor.py`). No fs/os/subprocess access.

## Non-Obvious Architecture

- **Session dicts:** `voice_sessions` (peer ID → LLMPipeline), `chat_sessions` (session ID → LLMPipeline) in `main.py`
- **SSE streaming:** Generator yields `data: {json}\n\n` with types: session, chunk, canvas_update, animation_event, done, error
- **Callbacks:** `canvas_callback` and `animation_callback` set on pipeline in `main.py` before streaming
- **Smart Turn:** pipecat-ai/smart-turn-v3 ONNX model alongside Deepgram endpointing. Config: `SMART_TURN_ENABLED`, `SMART_TURN_THRESHOLD`, `SMART_TURN_STOP_SECS`
- **Interruption:** `InterruptionState` per session, VAD detects speech during TTS → cancels TTS → sends `tts_cancelled` to client
- **Animation pipeline:** `funcs/animation_pipeline.py` → SSE `animation_event` → frontend `use-chat.ts` → `SVGCanvas`
- **LLM providers:** Unified interface in `funcs/llm_clients.py`, configured via `LLM_PROVIDER` env var

## Known Gotchas

- **Gemini protobuf types** (RepeatedComposite, MapComposite) need recursive `_to_native()` before `json.dumps`
- **Gemini content=None** for tool call messages — convert to `[Called tools: ...]` text summaries
- **GSAP timelines paused by default** — must call `.play()`
- **GSAP element lookup must be deferred** — wrap in `tl.add(() => {...})` so lookup happens at playback time
- **canvas_mode** — frontend must send `canvas_mode: true` explicitly or tools won't be included
- **LLM sends shape primitives directly** — handle `action: "circle"` etc. as pass-through in both `animation_pipeline.py` and `svg-canvas.tsx`
- **Tool schema enum must match LLM output** — if enum doesn't include shape primitives, unknown actions get silently dropped

## Code Quality — Non-Negotiable

Every line of code written in this project must meet these standards. No exceptions.

- **No dead code.** After completing any feature or refactor, audit every file you touched. Remove unused imports, unreachable branches, commented-out blocks, and orphaned functions. If something is no longer called, delete it — don't comment it out, don't rename it with an underscore.
- **Consistency over cleverness.** Match the patterns already in the codebase. If existing code uses `static methods` on repo classes, new repos use static methods. If existing hooks return `[data, actions]`, new hooks do the same. Never introduce a new pattern when an existing one works.
- **One way to do each thing.** Don't add a second way to achieve something that already works. No wrapper functions around wrapper functions. No "helper" that duplicates existing logic with a slightly different API.
- **Naming is architecture.** File names, function names, variable names — they should make the code readable without comments. `compileScene` not `processData`. `stepPipelinedTTS` not `handleAudio2`. If you can't name it clearly, the abstraction is wrong.
- **Post-completion cleanup.** After finishing any multi-file change, run a full audit:
  1. Search for unused exports from every modified module
  2. Check for config flags that no longer gate anything
  3. Remove any scaffolding, temporary logging, or debug code
  4. Verify no file has imports it doesn't use
  5. Run `cd web && npx tsc --noEmit` and `python -c "from main import app"` — both must be clean
- **Minimum viable code.** Write the least amount of code that solves the problem correctly. Three similar lines are better than a premature abstraction. A direct function call is better than a registry pattern with one entry. Add complexity only when the current code genuinely can't handle a real (not hypothetical) requirement.

## Verification

After implementing multi-file changes, always verify before reporting completion:
- **Backend:** `python -c "from main import app"` — catches import errors and startup crashes
- **Frontend:** `cd web && npx tsc --noEmit` — catches type errors
- **Full start:** `uvicorn main:app` — confirm no runtime errors on startup

## Running

```bash
cp .env.example .env  # add API keys
uvicorn main:app --reload
python -m pytest test/
```

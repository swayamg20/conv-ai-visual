# CLAUDE.md - Voice AI

Real-time voice assistant: WebRTC → Deepgram STT → Silero VAD → LLM (OpenAI/Gemini) → ElevenLabs TTS → Audio Response

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

## Running

```bash
cp .env.example .env  # add API keys
uvicorn main:app --reload
python -m pytest test/
```

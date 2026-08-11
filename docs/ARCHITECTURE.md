# Murmur architecture

This document describes the active system. For product intent, read [VISION.md](VISION.md); for older design explorations, use [archive/](archive/).

## System shape

Murmur is a modular monolith: one FastAPI backend, one Next.js frontend, and one SQLite database by default. Provider APIs sit at explicit edges; application behavior remains local and testable with fakes.

```text
                              Firebase
                                 |
Browser ---- Bearer token -------+
   |                             |
   | SSE chat                    v
   | WebRTC audio/signaling  FastAPI routers
   |                             |
   |                 +-----------+-----------+
   |                 |                       |
   |             ChatService             VoiceService
   |                 |                       |
   |                 +---- RuntimeRegistry --+
   |                             |
   |                 LLM pipeline + tool policy
   |                    /        |         \
   |               memory     tools      canvas events
   |                  |          |             |
   +<---- audio/events-+------ persistence     |
   |                                             |
   +--> SDL compiler --> timeline --> SVG renderer <+
```

## Backend boundaries

| Package | Responsibility |
| --- | --- |
| `murmur.api` | Application composition, authentication dependencies, request schemas, and thin routers |
| `murmur.chat` | Chat session resolution, SSE event production, persistence, and finalization |
| `murmur.voice` | WebRTC negotiation, audio transport, transcription, turn confirmation, speech synthesis, and cleanup |
| `murmur.runtime` | Typed process-local session records, activity tracking, supervision, and shutdown |
| `murmur.llm` | Provider-neutral client contract, OpenAI-compatible and Gemini adapters, routing, streaming, and tool rounds |
| `murmur.memory` | Token-bounded conversation context, durable memory adapters, and cross-session prompt assembly |
| `murmur.tools` | Model-facing schemas, database handler resolution, inline-code restrictions, and execution policy |
| `murmur.canvas` | Backend canvas state, operation validation, and built-in visual tool registration |
| `murmur.resources` | PDF/URL ingestion, text extraction, chunking, and agent-scoped retrieval |
| `murmur.persistence` | Engine creation, table declarations, and focused repositories |
| `murmur.agents` | Agent prompt construction and agent-specific runtime helpers |
| `murmur.core` | Configuration and transport-independent domain errors |

`main.py` deliberately exports only the constructed application and the compatibility `uvicorn` launcher.

## Application lifecycle

`create_application()` constructs the registry and services, attaches them to `app.state`, and includes the API router. Its lifespan owns:

1. table creation for the configured database;
2. voice-provider initialization;
3. upserting canonical built-in tool handler paths;
4. starting the idle-session supervisor;
5. idempotent runtime shutdown.

Importing a model or repository does not create tables or start provider clients.

## Identity and ownership

All product routes require a Firebase ID token. The backend verifies the token and auto-provisions the user record. Agent, resource, session, chat-runtime, voice-runtime, and observability reads resolve ownership from trusted server-side state or the database.

Client-provided `user_id` values are compatibility input only and never override authenticated identity. The OpenAPI and documentation routes remain public.

## Chat flow

1. The router verifies identity and maps JSON to `ChatTurnRequest`.
2. `ChatService` resolves or creates an owned runtime session.
3. `LLMPipeline` assembles agent instructions, resources, memory, and available tools.
4. Provider text and tool rounds stream as transport-neutral events.
5. The router encodes those events as SSE.
6. Finalization persists summaries and evicts the typed runtime record exactly once.

One lock per active chat session prevents overlapping turns from mutating the same context.

## Voice flow

1. The voice router authenticates and verifies session/agent ownership before allocating a peer.
2. `VoiceService` negotiates WebRTC and registers one `VoiceRuntimeSession`.
3. `VoiceTranscriber` streams audio to Deepgram and combines endpointing with optional Smart Turn analysis.
4. Confirmed turns enter the same LLM and memory pipeline used by chat.
5. `SpeechSynthesizer` streams sentence audio, applying bounded ElevenLabs retries and optional Kokoro fallback.
6. Typed data-channel events synchronize speech, canvas steps, interruption, and metrics.
7. Disconnect, negotiation failure, idle eviction, and shutdown share idempotent cleanup paths.

## Memory and persistence

The short-term window is bounded by both message count and estimated tokens. Durable memory is split into episodic summaries, optional Mem0 facts, explicit profile data, and decision history. Cross-session summaries are scoped through the authenticated user and selected agent.

SQLite defaults to `var/murmur.db`; tests replace the engine with a shared in-memory database. Domain repositories keep SQLModel calls out of routers and provider code. The current repository intentionally remains synchronous at this scale; moving database I/O off the event loop is tracked in [`TODOS.md`](../TODOS.md).

## Canvas architecture

The LLM emits semantic SDL steps or validated canvas operations. The browser owns deterministic layout and rendering:

```text
semantic step
  -> scene-kit compiler
  -> normalized CanvasOperation
  -> shared timeline plan
  -> Rough.js / SVG primitives
  -> viewport and image export
```

The canonical TypeScript contract lives in `web/src/features/canvas/types.ts`. Chat, WebRTC, the SDL compiler, and the renderer all depend on that feature type; the transport hooks do not own visual data.

## Extension rules

- Add HTTP behavior through a focused router and an application/domain service.
- Add database access through the relevant repository, not from a page or provider adapter.
- Add LLM providers by implementing `LLMClient`; keep wire-format translation inside the adapter.
- Add module tools with stable `murmur.*` handler paths and a database definition.
- Add canvas primitives behind the canonical operation type and pure geometry tests.
- Add external integrations behind injectable edges so default tests remain offline.

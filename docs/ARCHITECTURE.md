# Murmur architecture

This document describes the active system. For product intent, read [VISION.md](VISION.md); for older design explorations, use [archive/](archive/).

## System shape

Murmur is a modular monolith: one FastAPI control plane, one Next.js frontend, and one SQLite database by default. The default voice runtime remains the in-process legacy path. An explicitly selected Voice V2 path assigns a restricted LiveKit room to a separate named worker process. Provider APIs sit at explicit edges; application behavior remains local and testable with fakes.

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

Optional Voice V2 path:

Browser -- authenticated bootstrap --> FastAPI control plane
   |                                  | room/token/dispatch metadata
   +-- restricted LiveKit RTC -------+------> named Voice V2 worker
              audio + reliable events          | STT / LLM / TTS profile
                                               +-- canonical event channel
```

## Backend boundaries

| Package | Responsibility |
| --- | --- |
| `murmur.api` | Application composition, authentication dependencies, request schemas, and thin routers |
| `murmur.chat` | Chat session resolution, SSE event production, persistence, and finalization |
| `murmur.voice` | Legacy WebRTC runtime plus Voice V2 bootstrap contracts, LiveKit control adapter, worker authorization, provider profiles, event bridge, and cleanup |
| `murmur.runtime` | Typed process-local session records, activity tracking, supervision, and shutdown |
| `murmur.llm` | Provider-neutral client contract, OpenAI-compatible and Gemini adapters, routing, streaming, and tool rounds |
| `murmur.live_scene` | Bounded visual-act routing, deterministic scene compilation and verification, admission, and semantic SSE contracts |
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

### Legacy default

1. The voice router authenticates and verifies session/agent ownership before allocating a peer.
2. `VoiceService` negotiates WebRTC and registers one `VoiceRuntimeSession`.
3. `VoiceTranscriber` streams audio to Deepgram and combines endpointing with optional Smart Turn analysis.
4. Confirmed turns enter the same LLM and memory pipeline used by chat.
5. `SpeechSynthesizer` streams sentence audio, applying bounded ElevenLabs retries and optional Kokoro fallback.
6. Typed data-channel events synchronize speech, canvas steps, interruption, and metrics.
7. Disconnect, negotiation failure, idle eviction, and shutdown share idempotent cleanup paths.

### Optional Voice V2

1. The authenticated browser requests `/api/voice/session` with an owned session and fresh call ID.
2. FastAPI validates ownership, creates one room with one named `JRP_NEVER` dispatch, signs exact job metadata, and returns a restricted participant token. Browser tokens can publish microphone media but not data.
3. The browser joins through `LiveKitVoiceTransport`, creates then mutes and publishes its exact microphone track, and keeps it muted until a canonical `agent_ready` event arrives from the assigned agent identity on the fixed reliable topic.
4. A separately started LiveKit Agents worker re-authorizes the signed assignment, waits for the exact participant microphone, prepares one provider profile, starts `AgentSession`, waits for public RoomIO readiness, and then publishes Ready.
5. `VoiceEventChannel` is the sole serialized writer for server-to-browser semantic events. A terminal session or event-channel failure shuts down the job; exact-agent departure fails and tears down the browser call.
6. Browser disconnect calls the authenticated release route. The control plane removes the named dispatch and room, while the worker's idempotent owner closes session and provider resources.

The checked-in `fake-rtc-v1` profile is guarded to loopback test mode and is never a production fallback. `scripts/voice_e2e_stack.py` proves the path with a digest-pinned local SFU, isolated production frontend build, deterministic media, and Chromium; it does not qualify Cloud/TURN or real-provider quality and cost.

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

### Verified live-scene flow

The authenticated `/canvas/generate` surface uses a separate semantic live-scene path:

```text
browser + Firebase bearer
  -> POST /api/live-scenes/semantic/stream
  -> trusted-user admission
  -> start / continue / abstain routing
  -> server-owned teaching beat
  -> deterministic compile + verification
  -> semantic SSE atom
  -> browser presentation barrier
  -> paired low-level + semantic frontier
```

The first bounded turn builds the right triangle and its three side squares through the area identity. A later supported “why are those areas equal?” turn continues the same component into a server-owned altitude-projection dissection: the altitude partitions the `c²` square into regions independently verified to have areas `a²` and `b²`. The model chooses only the terminal semantic stage; it never authors geometry, labels, equations, styles, or patch operations.

The browser advances the frontier only after an atom is presented, so interruption and replay retain the exact visible prefix. Browser-observed presentation metrics begin at submit and settle only after the post-paint barrier; provider timing remains a separate server measurement. That frontier and those measurements are ephemeral and held in memory by the mounted browser runtime; they are not server-persisted proof across refreshes, devices, or sessions. The authenticated raw `/api/live-scenes/stream` remains an explicit rollback path. The auth-free lab routes are excluded from OpenAPI and remain available only when the server is in development mode, `MURMUR_SCENE_LAB=1`, and the request originates from loopback.

## Extension rules

- Add HTTP behavior through a focused router and an application/domain service.
- Add database access through the relevant repository, not from a page or provider adapter.
- Add LLM providers by implementing `LLMClient`; keep wire-format translation inside the adapter.
- Add module tools with stable `murmur.*` handler paths and a database definition.
- Add canvas primitives behind the canonical operation type and pure geometry tests.
- Add external integrations behind injectable edges so default tests remain offline.

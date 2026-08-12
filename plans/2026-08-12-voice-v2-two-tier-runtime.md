# Rebuild Murmur Voice as a Low-Latency Two-Tier Runtime

## Purpose / Big Picture

Murmur should feel like one present, interruptible thinking partner: it listens accurately, responds quickly enough to keep conversational rhythm, and can continue a deeper piece of reasoning or visualization without freezing the conversation. The current product idea remains sound. The raw realtime implementation does not.

This ExecPlan replaces the realtime voice boundary while preserving Murmur's authentication, agents, memory, resources, tools, persistent sessions, text fallback, and semantic canvas. The target uses open-source LiveKit Agents in a Murmur-owned worker and LiveKit Cloud only for WebRTC transport at first. Murmur calls STT, LLM, and TTS providers directly. It does not depend on LiveKit's managed agent hosting or managed inference. The transport-specific code is kept narrow enough that LiveKit Cloud can later be replaced by self-hosted LiveKit or the runtime by Pipecat without rewriting Murmur's task and canvas contracts.

The conversational architecture has two latency tiers but one public personality:

* The **Conversation Conductor** owns turn-taking, the audio floor, interruptions, clarification, short answers, and honest acknowledgements.
* The **Deep Reasoner** runs tools, retrieval, research, and visualization asynchronously. It cannot speak to the user or mutate the canvas directly.
* A deterministic task and artifact control plane connects them. The Conductor may narrate only verified results, and the canvas may apply only current, revision-compatible artifacts.

This is not a big-bang rewrite. The existing `/offer` runtime stays available behind a session-sticky feature flag until Voice V2 proves itself. The first visible success is deliberately small: an authenticated browser joins a LiveKit room, the self-hosted worker becomes genuinely ready, one utterance produces one audible reply, interruption stops it, and all resources are cleaned up. Canvas and the second tier are integrated only after that slice passes.

The completed product is observable in a credentialed browser session. The UI must never say Ready while required providers are unavailable; a no-tool turn must begin audible playback within the latency gate; an interruption must silence playback promptly; a deep request must allow the user to keep speaking while work continues; cancellation or correction must prevent stale speech and stale canvas updates; and every completion statement must correspond to an authoritative task result or rendered-canvas acknowledgement.

This plan supersedes the runtime and fallback assumptions in `plans/2026-03-23-voice-reliability-batch-1.md`. That document remains useful history, but retrying ElevenLabs and falling back to an optional local package is no longer the core reliability strategy.

## Progress

- [x] 2026-08-12 04:30 IST: Reproduced and classified the failed manual session: Smart Turn was unavailable, ElevenLabs rejected its key, Kokoro was not installed, and the application repeatedly attempted a path that could not emit audio.
- [x] 2026-08-12 04:30 IST: Mapped the current backend, frontend, persistence, canvas, session, and provider boundaries at commit `cdedc5d`.
- [x] 2026-08-12 04:30 IST: Reviewed current LiveKit, Pipecat, MCP, A2A, deployment, cost, and validation options and selected the initial technical direction recorded below.
- [x] 2026-08-12 04:30 IST: Wrote this ExecPlan without changing the runtime. The pre-existing `web/next-env.d.ts` modification remains outside this plan.
- [x] 2026-08-12 04:53 IST: Pushed the reviewed execution baseline as `1cc2edc`; GitHub Actions run `31545742737` passed both backend and frontend jobs.
- [x] 2026-08-12 12:32 IST: Completed Milestone 0 contracts, deterministic replay, truthful legacy readiness, correct final-segment/EOT assembly, and offline acceptance. The credentialed legacy baseline failed closed at preflight before any provider call because the selected TTS and Smart Turn paths were unavailable.
- [x] 2026-08-12 13:22 IST: Selected and compatibility-tested the Milestone 1 SDK boundary: `livekit-agents==1.6.9`, `livekit-api==1.2.0`, `livekit-client==2.21.0`, and OpenAI Python 2.x. The full 124-test backend suite passed against `openai==2.54.0` plus the pinned LiveKit packages before the lockfile changed.
- [x] 2026-08-12 13:30 IST: Built the partial Milestone 1 foundation: authenticated ownership-checked bootstrap, HMAC-derived rooms, explicit named dispatch, restricted browser tokens, expiring signed job metadata with exact browser/worker identities, a fail-closed native `AgentServer` worker seam, and a strict LiveKit browser adapter behind the legacy-default flag. This is a checkpoint, not the vertical-slice exit: the default worker profile deliberately refuses jobs, and no real RTC audio turn or interruption has passed yet.
- [x] 2026-08-12 13:45 IST: Hardened the partial Milestone 1 foundation before its checkpoint push: bounded repository and startup work, exact microphone-input readiness, explicit one-job worker defaults, expiring retry rejection, honest retry/cancel/text-fallback UX, authenticated call release, and process-local one-active-call admission with safe stale-room reclamation. The local-SFU browser media slice remains next.
- [x] 2026-08-12 15:05 IST: Completed the foundation hardening and repository-wide offline gates. Voice V2 now has a bounded unique-call registry, cancellation intent recorded before per-call reconciliation, independent bounded release admission, release retries with per-attempt browser deadlines, exact remote cleanup confirmation, no stale-room/dispatch deletion, no automatic LiveKit job restart, a fixed event topic, and server-to-browser-only data grants. Evidence: 237 backend tests, 90 frontend tests, Ruff lint/format, ESLint, TypeScript, Next production build, app import, lock validation, and a fresh wheel all passed. This still does not satisfy the Milestone 1 media exit.
- [x] 2026-08-12 15:43 IST: Completed a behavior-preserving Voice V2 boundary refactor before adding real media. Bootstrap contracts and lock ownership, worker authorization/session/runtime composition, assignment-release scheduling, and browser LiveKit transport now have focused modules. The stateful assignment/tombstone machine remains intact in one service. Independent comparison against `35b5835` found byte-identical signed metadata, identical bootstrap/retry/release traces, no missing public imports, an acyclic backend dependency graph, and unchanged LiveKit CLI discovery. Evidence: 237 backend tests, 96 frontend tests, Ruff lint/format, ESLint, TypeScript, Next production build, application import, lock validation, and fresh-wheel content inspection passed. Milestone 1 remains open because these are structure and contract proofs, not audible RTC proof.
- [x] Milestone 0: established truthful readiness, event contracts, deterministic replay, and an explicitly failed legacy baseline.
- [ ] Milestone 1: prove an authenticated LiveKit Voice V2 vertical slice with one self-hosted Murmur worker.
- [ ] Milestone 2: reconnect Voice V2 to Murmur's existing agent, session, memory, chat, and canvas product layers.
- [ ] Milestone 3: implement the Conversation Conductor, Deep Reasoner, durable task ledger, and revisioned artifact authority.
- [ ] Milestone 4: qualify cascaded and native-realtime provider profiles on the same corpus and select one default.
- [ ] Milestone 5: add failure injection, browser audio tests, cost accounting, production persistence, canary controls, and rollback.
- [ ] Milestone 6: cut over only after the gates pass, then remove the legacy realtime path and obsolete dependencies.

## Surprises & Discoveries

The failed session did not isolate one bad vendor. It exposed an invalid readiness and degradation contract. `backend/murmur/voice/smart_turn.py` could not load its optional model dependency, ElevenLabs returned an authentication error, and the Kokoro fallback package was absent. The browser could nevertheless appear connected and listening because `web/src/hooks/use-webrtc.ts` equates an open data channel with voice readiness. Repeated TTS attempts then guaranteed a silent and confusing experience.

The current transcript tests preserve a turn-taking bug. In `tests/test_voice_transcription.py`, a Deepgram segment with `is_final=true` and `speech_final=false` is expected to dispatch a complete user turn. `is_final` finalizes a transcript segment; it is not proof that the user has finished the whole turn. Voice V2 must assemble segments and commit only on an explicit end-of-turn signal. The legacy baseline fix is capped to this correctness issue; this plan does not authorize polishing the rest of the old pipeline indefinitely.

The current timing fields cannot explain the user's experience. The backend starts `speech_start_ts` on the first non-empty transcript rather than acoustic speech onset, measures the first TTS chunk before client playout, and uses `latency_total_ms` for a mixed interval that is not user-end-to-first-audio. There is no complete timestamp chain for connect click, transport, worker assignment, provider readiness, end of turn, first audible frame, interruption silence, or canvas render acknowledgement.

The browser currently owns more of the voice pipeline than it should. `web/src/hooks/use-webrtc.ts` uses one public Google STUN server with no TURN relay, waits up to two seconds for ICE gathering, sends base64 audio through a data channel, and runs a second VAD for interruption. The server simultaneously owns Deepgram endpointing, Smart Turn, generation, TTS, and peer state. This split creates competing sources of truth.

The session page already contains an idempotency warning. It creates a persistent session in a React effect while the legacy voice route can also create or attach session state. The manual log showed duplicate session requests. The page also calls an authenticated end-session route with `navigator.sendBeacon`, which cannot attach the Firebase bearer token.

The product layers are substantially more reusable than the realtime path. Firebase ownership, agent prompts, resources, memory, tools, provider-neutral LLM clients, session history, text chat, scene compilation, and the SVG canvas all have useful boundaries. Starting the entire repository again would throw away the cleanest parts.

The existing test baseline is green but narrow: 46 backend tests and 12 frontend tests passed at `cdedc5d`; there is no browser/RTC test; default voice tests use fakes; and CI explicitly avoids credentialed providers. Those tests prove local contracts, not a working voice product.

The first machine-readable legacy preflight on 2026-08-12 found Deepgram, the configured Groq LLM, and Firebase locally configured, but the ElevenLabs credential was missing or a placeholder. Kokoro fallback and Smart Turn download dependencies were also absent. Because the user's local configuration explicitly selected Smart Turn, that profile was blocked rather than silently changed to Deepgram endpointing. No provider call was made. This is now an explicit failed baseline rather than a UI that claims Ready.

The first adversarial pass caught three failures hidden behind superficially green assertions: duplicate `speech_final` events could dispatch twice, transcript teardown could retain an active `recv()` task, and terminal readiness delivery could wait forever for a data channel. The final implementation coalesces duplicate segments/boundaries, awaits every owned task under asyncio debug checks, keeps pending Ready discoverable until peer/channel teardown, and bounds best-effort delivery of a terminal failure. The browser now treats any explicit `recoverable=false` backend error as terminal instead of matching one error code.

The post-push shutdown audit found that the legacy audio consumer itself was only captured by a track callback, not by `VoiceRuntimeSession`. Peer finalization could therefore remove the registry entry before the provider/audio task ended. The session now owns `audio_task`; finalize and process shutdown cancel and await it with a self-task guard, and task exceptions are retrieved and logged.

The deterministic evaluator initially duplicated the runtime's transcript state machine and only checked that WAV paths existed. It now imports the same `TranscriptAccumulator` used in production, advances it with fixture `at_ms` as a virtual clock, and validates non-empty mono 16 kHz 16-bit PCM WAV metadata. This still does not exercise media transport or provider recognition; those remain Milestone 1 browser/RTC obligations.

The event reducer currently assumes its input has already been ordered by transport or durable replay. A producer-sequence gap is not buffered inside the reducer. Milestone 3 reconnect work must introduce gap detection plus complete ledger replay or an authoritative snapshot before subsequent deltas are reduced.

The pinned LiveKit Agents CLI treats its positional entrypoint as a filesystem path, not an import string. The source-tree command works for Milestone 1, but the deployment command must be proven inside the built image. LiveKit reliable data packets are not replayed after a receiver disconnects; until the Milestone 3 re-handshake/snapshot protocol exists, a reconnected browser explicitly ends the old call and offers a fresh-call retry instead of waiting for an impossible second Ready event.

LiveKit Agents and LiveKit RTC are not competing per-minute products in the chosen deployment. LiveKit Agents is the open-source worker framework that Murmur self-hosts; LiveKit Cloud supplies media transport, signaling, NAT traversal, and TURN. This avoids the separate managed-agent minute charge. The main costs remain RTC usage, Murmur worker compute, and direct STT/LLM/TTS usage. Current pricing must be rechecked before any paid rollout because plan allowances can change.

The first control-plane implementation exposed several lifecycle assumptions that the LiveKit server does not guarantee. Deleting a dispatch terminates its attached job, listing dispatches after a room disappears returns room-not-found, and the dispatch API defaults to restarting failed jobs unless the request explicitly selects `JRP_NEVER`. Therefore stale local assignments never delete a room or dispatch while the room exists; they are reclaimed only after LiveKit reports the room absent. Explicit user release alone deletes the exact dispatch and room, tolerates the room disappearing between confirmation reads, and retains local capacity whenever cleanup is uncertain.

An apparently harmless configurable event topic was incompatible with the strict browser decoder. The wire topic is now the fixed protocol constant `murmur.voice.v2.events`, not an environment knob. Likewise, the browser participant cannot publish LiveKit data and the worker disables `RoomOptions.text_input`; microphone audio is the only browser-to-agent realtime input in this foundation.

The final adversarial pass reproduced a cancellation resurrection rather than inferring one: with 30-second token/job TTLs, a bootstrap blocked in repository authorization could outlive a 30-second release marker and later create its room and dispatch. Release tombstones now use a fixed 900-second horizon, matching the maximum supported credential TTL and comfortably exceeding every bounded already-admitted bootstrap stage. The gated-auth regression advances past the short credential TTL and proves zero room, dispatch, or token creation.

The first maintainability audit found that landing the real-media slice on top of the 1,294-line bootstrap module, 713-line worker module, and 1,067-line browser hook would make later lifecycle review materially harder. A behavior-preserving extraction reduced the bootstrap orchestrator to 903 lines, the worker entrypoint to 111 lines, and the hook to 836 lines. The difficult assignment/tombstone state machine was deliberately not split: pure contracts, the keyed-lock primitive, worker authorization/session/runtime boundaries, release scheduling, and LiveKit browser transport moved; local lifecycle transitions stayed together. Golden comparisons against the pushed foundation protected signed metadata and control-plane behavior during this change.

SQLite plus `SQLModel.metadata.create_all()` is acceptable for today's local prototype but not for evolving durable task state across multiple processes or hosts. A versioned migration mechanism is required before adding Voice V2 tables. SQLite and an in-process async Reasoner may remain for the first single-host prototype; Postgres and a durable queue become mandatory before more than one production worker host or automatic task recovery is claimed.

## Decision Log

2026-08-12, Codex: Preserve the Murmur product and domain layers; replace the realtime voice boundary. The user's failed test does not justify deleting authentication, memory, agent configuration, chat, resources, or canvas code.

2026-08-12, Codex: Keep WebRTC but stop owning raw production media. Begin with open-source LiveKit Agents in a self-hosted Murmur worker and LiveKit Cloud for RTC only. Use direct provider accounts. Do not buy LiveKit managed-agent hosting or inference as a hidden dependency.

2026-08-12, Codex: Use one realtime owner. LiveKit `AgentSession` owns media, STT turn input, TTS output, and interruption for the initial implementation. Do not combine it with a Pipecat pipeline in the same call. Pipecat remains the primary runtime exit if LiveKit Agents fails Murmur's gates.

2026-08-12, Codex: Implement two latency tiers, not two independent personalities. Only the Conductor speaks. The Reasoner returns typed task results and artifact proposals; it never publishes audio or canvas commands directly.

2026-08-12, Codex: An acknowledgement is not a completed assistant answer. Persist acknowledgement/progress events separately and persist the verified final answer as the canonical assistant result for memory and replay.

2026-08-12, Codex: Partial transcripts may start speculative, cancellable preparation, but no irreversible tool, persistence, speech, or canvas side effect may escape until a turn is committed. `is_final` alone never commits a turn.

2026-08-12, Codex: Use transport-neutral event, task, and canvas contracts, but do not build a universal transport framework. LiveKit imports must be confined to the worker/runtime module and frontend connection adapter. A future implementation should be replaceable through those narrow seams, not through speculative abstraction everywhere.

2026-08-12, Codex: MCP and A2A stay outside the hot audio path. The first Reasoner calls the existing Murmur tools directly behind a small `ToolGateway` protocol. Add an MCP adapter only for external tool portability. Use A2A only if Murmur later delegates a task to a separately deployed specialist with its own identity and lifecycle.

2026-08-12, Codex: The first prototype uses SQLite and one in-process async Reasoner loop to keep infrastructure and cost low. Active tasks found after a restart are marked failed/recoverable; the system must not pretend they resumed. Before multi-host canary, move the ledger to Postgres and work delivery to a durable queue.

2026-08-12, Codex: Runtime assignment is sticky for the full session. Never switch a caller from legacy to Voice V2 or between provider architectures mid-call. Rollback affects only new sessions while active sessions drain.

2026-08-12, Codex: Text mode is the safe user-facing fallback when Voice V2 is unavailable. The already-broken legacy voice path is a benchmark and temporary rollback option only while it remains proven for the relevant release; it is not an automatic silent fallback.

2026-08-12, Codex: No paid plan upgrade, managed-agent deployment, or unbounded provider evaluation is allowed. Live evaluation is opt-in with a hard dollar cap. Cost is measured per successful audio minute and includes retries, cancelled speculation, warm idle, and failed turns.

2026-08-12, Codex: Deepgram endpointing is the default legacy turn detector. Smart Turn is an explicit optional profile installed through the `smart-turn` extra; if selected but unavailable it fails closed rather than silently changing the turn detector.

2026-08-12, Codex: Milestone 0 classifies a required-provider preflight failure as a failed legacy baseline. It does not bypass unavailable TTS/turn detection, make paid calls without a budget, or invent live latency telemetry to satisfy the word “credentialed.”

2026-08-12, Codex: Pin the first Voice V2 integration to `livekit-agents==1.6.9`, `livekit-api==1.2.0`, and `livekit-client==2.21.0`. Current LiveKit Agents requires OpenAI Python 2.x, so raise Murmur's supported OpenAI floor to 2 while retaining the `<3` major-version bound. Murmur's existing `AsyncOpenAI().chat.completions` wrapper is supported by the current official SDK and the repository's full backend suite passed against `openai==2.54.0` before adopting the lock change. The resolver exposed an incompatible `mem0ai==0.1.118` OpenAI `<1.110` cap; upgrade Mem0 to its current 2.x major and migrate Murmur's narrow hosted-client calls to the documented `filters` and `top_k` form rather than silently downgrading memory support. Provider plugins remain separate and are added only when a qualified profile uses them.

2026-08-12, Codex: Land the control-plane, worker, and browser seams as a partial Milestone 1 checkpoint without claiming working voice. The default worker remains unavailable until a direct provider or deterministic fake-RTC profile passes preflight. Assignment state is still process-local and non-durable. Reconnect is a bounded fresh-call retry until durable replay exists. The next acceptance target is the checked-in local LiveKit/Playwright topology with actual inbound and non-zero outbound audio, turn commit, interruption, and cleanup.

2026-08-12, Codex: Cap the process-local Milestone 1 control plane and worker to one active call. The browser explicitly releases its authenticated assignment on cancellation, terminal failure, disconnect, end, disable, and unmount. A release deletes and confirms the exact signed dispatch and room before freeing capacity. After the later lifecycle refinement below, an expired assignment is reclaimed automatically only after the remote room is absent; an active or merely empty room is never deleted because local metadata expired. Durable multi-process admission remains Milestone 3 work.

2026-08-12, Codex: Refine the process-local lifecycle after adversarial review. Registry capacity counts unique call IDs across active assignments and release tombstones. Exact cancellation intent is recorded before waiting on the per-call lock, release has independent bounded admission, and uncertain cleanup keeps the assignment unavailable. An expired assignment is never reclaimed by deleting its remote room or dispatch; LiveKit's configured empty-room expiry owns that teardown, and local capacity is reclaimed only after the room is observably absent.

2026-08-12, Codex: Explicit dispatches use restart policy `JRP_NEVER`; browser failures recover through an authenticated fresh-call flow instead of automatic worker-job relaunch. The worker remains capped to one job, zero idle prewarmed processes, and a deliberately small framework retry surface.

2026-08-12, Codex: Fix `murmur.voice.v2.events` as a versioned wire-protocol topic. Remove the environment override because the strict frontend decoder must not accept an arbitrary server-selected channel. Browser tokens may publish microphone media but not data, and LiveKit text input is disabled to prevent an unaudited second route into the model.

2026-08-12, Codex: Retain release tombstones for a fixed 900 seconds rather than deriving their lifetime from the selected 30-900 second token/profile TTL. This is a bounded process-local safety horizon for bootstraps that already passed admission before cancellation; it prevents a delayed authorization from resurrecting the call. Durable cancellation state replaces this timer in Milestone 3.

2026-08-12, Codex: Establish focused module boundaries before implementing RTC media, but do not decompose the assignment/tombstone state machine merely to reduce line count. Pure bootstrap contracts, worker authorization/session/runtime composition, browser assignment release, and LiveKit transport are independently testable; the concurrency-sensitive local registry remains one owner until a later behavior-driven persistence boundary replaces it.

## Outcomes & Retrospective

Milestone 0 completed on 2026-08-12. Voice V2 now has strict Python and TypeScript event/task/artifact contracts, a fail-closed frontend state model, named clock-domain-safe metric spans, a provider-free replay harness, and a shared production/evaluation transcript accumulator. Seven replay scenarios passed twice with identical combined trace hash `8a1cdfbf49acedeb4ef1f6cb516ff15f670ed6009ad303dc13f6530e275edff9`. Offline evidence was 124 backend tests, 52 frontend tests, Ruff, ESLint, TypeScript, Next production build, app import, and a fresh wheel containing the new contract/evaluation packages.

The exact live result is **failed before session start**, not measured: legacy preflight found Deepgram, Groq, and Firebase configured; ElevenLabs missing/placeholder; Kokoro fallback absent; and Smart Turn selected locally without its model/download dependencies. Exit status was 1, network verification was false, no provider call was made, and all live latency/reliability/cost gates remain `unmeasured`. `StageRecorder` is deliberately a contract in this milestone, not yet production wiring; the legacy client has no acoustic-end, first-audible, or interruption-to-silence timestamp and reports those spans as unavailable. Therefore there are no honest p50/p95, metrics-completeness, or cost-per-minute numbers for Milestone 0. The decision is to continue to the fake-provider local-RTC slice in Milestone 1, where the browser and worker timestamp chain becomes executable, while retaining this failed legacy result as the comparison baseline.

The partial Milestone 1 foundation now defines and enforces the trust/lifecycle boundaries around that slice, but it has intentionally not crossed the media acceptance gate. No audible V2 turn, interruption, RTC reconnect restoration, provider latency, or cost result exists yet. Those remain open and Milestone 1 stays unchecked.

The final partial-foundation evidence after the boundary refactor is 237 backend tests and 96 frontend tests, plus clean Ruff lint/format, ESLint, TypeScript, Next production build, `uv lock --check`, application import, and a fresh source/wheel build. These tests cover authenticated bootstrap/release, exact scope and signed metadata, bounded repository/startup/release work, cancellation races, registry saturation, LiveKit room-not-found behavior, fixed dispatch restart policy, strict browser assignment/event identity, microphone readiness, retry/timeout UX, teardown, release scheduling, and isolated transport ownership. They use fakes for media and providers, so they prove the control boundary rather than audible voice.

The exact next proof is now fixed: a loopback-only E2E application, pinned LiveKit Server `1.13.1`, a deterministic provider profile that reacts to real non-zero microphone frames, and Chromium fake-media input. This must prove named dispatch, genuine Ready, Opus/RTC transport, non-zero decoded remote PCM, interruption-to-silence, and cleanup. LiveKit's text-only agent test harness is useful for logic but cannot satisfy this media boundary.

If LiveKit Agents does not pass the same functional, latency, interruption, browser, and cost gates as the alternatives, record that result here and exercise the Pipecat/LiveKit transport exit. If the two-tier design improves acknowledgement latency but worsens task correctness, cancellation, or user trust, remove the split rather than defending the architecture.

## Context and Orientation

### Current product boundaries

`main.py` creates the FastAPI application from `backend/murmur/api/application.py`. The application currently constructs one `RuntimeRegistry`, `ChatService`, and `VoiceService`; starts voice services in the FastAPI lifespan; registers built-in tools; and owns process-local cleanup.

`backend/murmur/api/routers/voice.py` exposes authenticated SDP negotiation at `/offer`. `backend/murmur/voice/service.py` and the other files under `backend/murmur/voice/` own `aiortc`, Deepgram, Smart Turn, LLM/TTS orchestration, data-channel events, and teardown. `backend/murmur/runtime/registry.py` stores `RTCPeerConnection`, `RTCDataChannel`, Smart Turn state, one turn task, and timing dictionaries in the same `VoiceRuntimeSession`.

`backend/murmur/chat/service.py`, `backend/murmur/voice/pipeline.py`, and `backend/murmur/llm/pipeline.py` construct agent-aware LLM pipelines. This logic must be shared before creating Conductor and Reasoner roles so voice and text do not drift.

`backend/murmur/persistence/models.py` and `backend/murmur/persistence/repositories/` store users, agents, sessions, messages, memories, resources, tool definitions, and aggregate observability. `backend/murmur/persistence/database.py` creates tables with `create_all()`. There are no versioned migrations.

`web/src/app/(app)/session/[agentId]/page.tsx` and `web/src/app/(app)/canvas/page.tsx` coordinate the current voice hook and the canvas. `web/src/hooks/use-webrtc.ts`, `use-audio.ts`, and `use-vad.ts` own the raw browser transport, chunk playback, and local interruption path. `web/src/features/canvas/`, `web/src/lib/scene-kit/`, and `web/src/components/svg-canvas.tsx` are the semantic rendering layer and should remain.

### Terms used in this plan

**Transport-connected** means the browser has joined the room and has a viable media path. It does not mean the agent can understand or answer.

**Voice-ready** means the room, worker, microphone path, required model/provider connections, and event channel have all passed readiness checks for the selected profile. Only this state may trigger the Ready UI and sound.

**Turn** is one semantically committed user contribution. A provider may emit several final transcript segments inside one turn.

**Task** is a durable unit of deep work requested by a committed turn. Its lifecycle is `queued`, `working`, `needs_input`, `verified`, `failed`, `cancelled`, or `superseded`.

**Artifact proposal** is the Reasoner's typed suggestion for canvas state. It includes a base canvas revision and provenance. It is not visible state until the canvas authority accepts it and the browser acknowledges rendering.

**Canonical answer** is the verified assistant result stored for memory and replay. Acknowledgements and progress speech are delivery events, not separate canonical answers.

**Profile** is a versioned combination of runtime, STT, turn detection, LLM, TTS, prompts, and configuration. Examples are `legacy`, `livekit-agents-cascade`, and `livekit-agents-realtime`.

### Target architecture and ownership

The initial deployed shape is:

    Browser
      |  authenticated HTTP: create/resume session, receive short-lived room token
      v
    FastAPI control plane ---------------------> SQL database
      |                                           sessions, messages, tasks,
      | signed room/session metadata              events, artifacts, usage
      v
    LiveKit Cloud RTC <---------------------- self-hosted Murmur voice worker
      ^       audio + typed data                    |
      |                                             | owns Conductor and one audio floor
      +---------------------------------------------+
                                                    |
                                                    v
                                             async Deep Reasoner
                                             existing tools/resources
                                             artifact proposals only

FastAPI remains the authority for Firebase identity, agent ownership, and persistent-session creation. It returns a short-lived participant token from a new authenticated `/api/voice/session` endpoint. The request carries an existing `session_id` and a client-generated `voice_call_id` reused for retries/reconnects. FastAPI derives a non-guessable room name by HMACing environment, authoritative `user_id`, `session_id`, and `voice_call_id`; a client-controlled call ID alone never defines a room. It creates or reads the room, fixes the server-selected runtime profile in room metadata, and dispatches the named `murmur-voice-v2` worker.

During the single-process Milestone 1 prototype, a per-call async lock surrounds dispatch lookup/create. If the room already exists, FastAPI queries/reuses the existing dispatch rather than relying on token-embedded dispatch, which is ignored for an existing room. This proves retry idempotency only for the explicitly single-process control plane; it does not claim cross-process exactly-once dispatch. After the Voice V2 call-assignment table lands in Milestone 3, a unique database record plus reconciled dispatch ID makes the contract restart- and replica-safe. Any ambiguous dispatch state fails closed and is reconciled rather than starting a second agent. LiveKit API secrets never reach the browser.

The participant token has the minimum grants required to join that room, publish microphone audio, and subscribe to the agent. It cannot publish data, create arbitrary rooms, or act as the worker. Signed job metadata carries `user_id`, `session_id`, `agent_id`, `voice_call_id`, runtime/profile version, and trace ID. The worker treats those values as locators, reloads authoritative ownership/configuration, validates the fixed server-to-browser event topic, and refuses a mismatch.

The LiveKit worker is a separate entrypoint, `backend/murmur/voice/worker.py`. With the pinned LiveKit Agents 1.6.9 CLI, the positional entrypoint is a filesystem path rather than a Python module string: execute `uv run python -m livekit.agents start backend/murmur/voice/worker.py --dev` locally and omit `--dev` in deployment. It connects outbound to LiveKit. FastAPI must not own media peers. The source-tree path is valid for Milestone 1; the packaged container entrypoint remains unresolved until Milestone 6 and must be proven against the built wheel/image rather than copied as an assumed module command.

The Conductor owns all assistant speech. For a small conversational request it may answer directly. For deep work it may clarify or say that it is working, then enqueue a typed task. While the Reasoner runs, the Conductor remains interruptible and can answer status questions, accept corrections, or cancel/supersede the task. When a verified result arrives, the Conductor presents it only when it owns the audio floor and the result still belongs to the current session/task generation.

The Reasoner receives an immutable request snapshot: user identity, session, agent configuration revision, committed turn text, relevant memory/resource references, tool policy, task generation, and current canvas revision. It emits progress facts, a final typed result, optional artifact proposals, usage records, and errors. It cannot call TTS or publish LiveKit data.

The canvas authority accepts a proposal only when its `base_revision` matches the current authoritative revision and its task is not cancelled or superseded. Accepted patches receive a new revision and are sent to the browser. Because `SVGCanvas.render()` schedules GSAP work and returns before animation completes, the browser emits distinct acknowledgements: `canvas_apply_ack` after validation and insertion/scheduling into the scene, `canvas_first_visible` after the first meaningful rendered frame, and optional `canvas_animation_complete` when a teaching timeline ends. Spoken language such as "I have drawn it" waits for `canvas_first_visible`, not the entire animation. State convergence may rely on `canvas_apply_ack`; the 700 ms visible-render gate uses `canvas_first_visible`.

### Event contracts

Create `backend/murmur/voice/contracts.py` and the matching `web/src/features/voice/events.ts`. Every event envelope contains:

    schema_version
    event_id
    event_type
    trace_id
    voice_call_id
    session_id
    turn_id                 optional before a turn exists
    task_id                 optional before a task exists
    producer_id
    producer_sequence       monotonically increasing for that producer
    causation_id            optional source event
    correlation_id          optional cross-process flow identifier
    ledger_sequence         assigned only after durable ingestion
    task_generation         optional
    canvas_base_revision    optional
    canvas_result_revision  optional
    emitted_at              wall-clock timestamp for audit only
    payload

Ordering and idempotency use event IDs, producer-local sequence, causation/correlation IDs, task generation, and canvas revision, never wall-clock ordering. The browser tracks each producer independently; it does not invent one total order across browser, FastAPI, LiveKit worker, and Reasoner. The authoritative ledger assigns a canonical `ledger_sequence` after ingestion for the durable subset. The initial vocabulary includes transport/agent readiness, transcript segment, turn committed/resumed, assistant speech started/stopped, task queued/working/needs-input/verified/failed/cancelled/superseded, artifact proposed/accepted/rejected, canvas patch/apply-ack/first-visible/animation-complete/render-failed, usage recorded, and session ending/ended.

One `EventAppender` repository method is the only way to append durable events. It allocates the next session ledger sequence and enforces unique `event_id`, unique `(session_id, ledger_sequence)`, and unique `(session_id, producer_id, producer_sequence)` constraints. A task transition and its corresponding event append occur in the same database transaction. On SQLite, the appender uses one short serialized write transaction; on Postgres, it uses row locking or a database sequence. Audio frames, interim text, and telemetry never wait on this appender.

Create `backend/murmur/reasoning/contracts.py` for `ReasoningRequest`, `ReasoningProgress`, `ReasoningResult`, `ArtifactProposal`, and enumerated task transitions. Use Pydantic models for wire/storage validation and TypeScript discriminated unions at the browser boundary. Unknown event versions or types must fail closed with an observable compatibility error; they must not mutate the canvas.

Reconnect uses state convergence, not hope that ordered delivery covered the gap. The browser reconnect handshake sends `voice_call_id`, last applied durable `ledger_sequence`, each producer's last sequence, and current canvas revision. The worker stops any disconnected-call audio, reloads current task/call state, and either replays retained durable events after the acknowledged ledger sequence or sends an authoritative snapshot containing canonical transcript/history references, active task generations/statuses, and the latest accepted canvas artifact/revision. The client applies the snapshot idempotently and acknowledges its revision before new deltas. Ephemeral interim transcripts and unheard audio are never replayed.

### Persistence and process stages

Use Alembic before adding Voice V2 tables. The baseline migration represents the existing SQLModel schema. An existing database without `alembic_version` may be stamped only after a schema-fingerprint check proves it matches the baseline; otherwise the migration command refuses and prints backup/recovery instructions. Production deploys run `alembic upgrade head` as an explicit step, not concurrently from every web/worker process. At that point, replace the `SQLModel.metadata.create_all()` call in `backend/murmur/persistence/database.py` and the unconditional startup use in `backend/murmur/api/application.py` with a schema-current assertion. `create_all()` may remain only in isolated unit-test fixtures; application and worker startup must never bypass migration history.

Add minimal first-version tables for:

* a voice-call assignment with unique client bootstrap key/call ID, user/session/agent, non-guessable room name, LiveKit dispatch ID, sticky runtime/profile version, status, and lifecycle timestamps;
* a durable task ledger with task ID, idempotency key, user/session/turn, task generation, status, request/result/error payloads, canvas revisions, provider/profile provenance, and lifecycle timestamps;
* an append-only session event ledger with event ID, ledger sequence, producer identity/sequence, causation/correlation IDs, type, identifiers, schema version, payload, and timestamp;
* accepted canvas revisions/artifact provenance;
* usage records with provider, model, pricing version, billable unit/quantity, estimated USD, environment, build SHA, room/job identifiers, and success/cancel/failure attribution.

Persist committed turns and task/artifact/control transitions in the durable ledger. Interim transcript segments, audio frames, WebRTC samples, and high-frequency telemetry belong in trace/eval storage; writing them synchronously to SQLite would put storage in the hot audio path.

The local single-call prototype can keep SQLite and a Reasoner loop in the LiveKit job process. Because LiveKit jobs are separate processes, cap this mode to one concurrent V2 call and do not call it production durable execution. From their first use, Voice V2 repository operations invoked by async realtime code must use bounded thread offloading or async database access. This does not require converting every existing repository. Task rows include `owner_job_id`, `worker_id`, `attempt`, `heartbeat_at`, and `lease_expires_at`. In the SQLite prototype, an expired lease marks work failed/recoverable; it is never silently resumed.

Before any multi-session production canary, switch the same repositories to Postgres and use a durable delivery mechanism with leases, heartbeats, retries, idempotency, and a dead-letter state. The voice job then owns only ephemeral realtime state; the Reasoner execution survives a voice-job reconnect or drain according to explicit policy. Redis is not required by the product contract; select a queue only when that milestone is reached and record the operational reason in this plan.

### Files to preserve, adapt, and eventually remove

Preserve the public entrypoint, Firebase auth and ownership dependencies, agent configuration/prompting, provider-neutral LLM clients, resources, memory, tools, session/message repositories, chat fallback, scene compiler, and canvas renderer.

Adapt:

* `backend/murmur/api/application.py` for explicit Voice V2 control-plane dependencies, not media ownership;
* `backend/murmur/api/routers/voice.py`, `backend/murmur/api/schemas.py`, and `backend/murmur/api/dependencies.py` for authenticated Voice V2 session bootstrap and ownership;
* `backend/murmur/agents/` with a shared immutable context builder and separate role-specific pipeline factories for chat, Conductor, and Reasoner;
* `backend/murmur/memory/manager.py` for acknowledgement/progress versus canonical-result semantics;
* `backend/murmur/canvas/state.py` through a new revisioned `backend/murmur/canvas/authority.py`;
* persistence models/repositories and the observability API/UI for task, event, stage latency, WebRTC, and usage truth;
* both app pages through one shared `web/src/hooks/use-voice-session.ts` and one reducer in `web/src/features/voice/`.

Add for Milestones 0-2:

    backend/murmur/voice/contracts.py
    backend/murmur/voice/livekit_runtime.py
    backend/murmur/voice/worker.py
    backend/murmur/reasoning/__init__.py
    backend/murmur/reasoning/contracts.py
    scripts/voice_eval.py
    evals/voice/smoke.jsonl
    evals/voice/qualification.jsonl
    evals/voice/gates.json
    tests/fixtures/voice/
    web/src/features/voice/events.ts
    web/src/features/voice/event-reducer.ts
    web/src/features/voice/session-machine.ts
    web/src/hooks/use-voice-session.ts
    web/e2e/voice-session.spec.ts
    web/e2e/interruption.spec.ts
    web/e2e/provider-failure.spec.ts
    web/playwright.config.ts

Add at Milestone 3, only after the transport/product slice passes:

    backend/murmur/voice/conductor.py
    backend/murmur/reasoning/service.py
    backend/murmur/canvas/authority.py
    backend/murmur/persistence/repositories/voice_runtime.py
    backend/murmur/persistence/migrations/
    scripts/migrate.py

Add during production hardening:

    deploy/voice-worker.Dockerfile
    deploy/README.md

Remove only after cutover: the raw `aiortc` service and models, direct Deepgram socket orchestration, Smart Turn/Kokoro fallback code if no selected profile uses it, base64 TTS/data-channel playback, the legacy WebRTC/audio/VAD hooks, `/offer`, and now-unused dependencies such as `aiortc`, direct `websockets`, `onnxruntime`, and `@ricky0123/vad-react`.

### External assumptions to reverify during execution

Use only official documentation for version-sensitive implementation choices. Recheck [LiveKit explicit agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/), [job lifecycle](https://docs.livekit.io/agents/server/job/), [self-hosted agent deployment](https://docs.livekit.io/deploy/custom/deployments/), [observability](https://docs.livekit.io/deploy/observability/data/), [tracing](https://docs.livekit.io/deploy/observability/tracing/), [pricing](https://livekit.com/pricing), and [billing units](https://docs.livekit.io/deploy/admin/billing/) before locking versions or estimating production cost. LiveKit's text-mode agent tests do not exercise a room or audio pipeline, so follow the official [agent testing documentation](https://docs.livekit.io/agents/start/testing/) but retain Murmur's browser and live-audio suites.

## Plan of Work

### Milestone 0: truthful baseline, contracts, and measurement

First make failure visible and measurements meaningful. Add a provider/runtime preflight that distinguishes configured, reachable, and ready states. The browser must display transport-connected separately from voice-ready. Invalid keys, missing required packages/models, or provider timeouts must prevent Ready and offer an actionable text fallback.

Define the event, task, artifact, lifecycle, and metric contracts before integrating LiveKit. Add the deterministic replay harness and initial synthetic audio/provider-event corpus. Replace the test that treats `is_final` as end of turn with segment accumulation, explicit EOT, resumed-speech, duplicate, missing-EOT, and long-pause cases. Make the corresponding bounded fixes in `backend/murmur/voice/transcription.py` and make `backend/murmur/voice/service.py` publish Ready only after the selected legacy provider path is usable. Use an injected monotonic clock and virtual scheduler; do not use real sleeps in contract tests.

Instrument the current browser and legacy runtime just enough to establish a credentialed baseline with valid provider credentials. Record connection phases, word/turn boundaries, first audible client frame, interruption silence, provider usage, and failure. Do not spend this milestone optimizing the old transport. If the legacy path cannot produce a valid session after the bounded correctness/readiness work, record it as a failed baseline rather than broadening the milestone.

Exit when offline replay is deterministic, a bad credential can never produce Ready, the legacy result is explicitly classified as passing or failed, and the corpus/gates are checked into the repository.

### Milestone 1: minimum LiveKit vertical slice

Add LiveKit Python packages under a temporary `voice-v2` optional dependency group and `livekit-client` to the web app. Pin versions in `uv.lock` and `web/package-lock.json`. Do not add managed-inference packages not used by the chosen direct providers.

Add an authenticated `/api/voice/session` bootstrap route. In this milestone it requires an existing owned `session_id` plus a client-generated `voice_call_id` reused on retries; it does not create another persistent session. It verifies that the session and agent belong to the Firebase user, HMAC-derives the room from trusted ownership plus the call ID, reads or fixes the server-controlled runtime profile in room metadata, and creates/reuses explicit named-agent dispatch under a per-call lock in the single FastAPI process. The same call ID must return the same active room, profile, and dispatch in that bounded prototype. Never return API key/secret material or accept a user-selected runtime profile. Keep `/offer` available behind `VOICE_RUNTIME=legacy|livekit_v2`; assignment is sticky for the voice call. Cross-process/restart-safe dispatch idempotency is an explicit Milestone 3 acceptance item.

Add the standalone worker entrypoint and the smallest Conductor path: join, publish readiness only after required components are usable, receive one committed utterance, produce one simple response through an audio track, handle interruption, and close cleanly. Do not integrate tools, memory, or canvas yet. Use a direct STT/LLM/TTS profile and a test-profile factory so providers can be faked deterministically.

Add the initial Playwright offline RTC project in this milestone, not later. It starts a pinned local LiveKit server, fake providers/worker, test-auth FastAPI, and Next.js through one stack runner and verifies real browser media frames without external provider credentials.

Replace raw WebRTC usage on one internal route with `use-voice-session.ts` and the session state machine. The browser must render room/transport state, agent readiness, listening, thinking, speaking, reconnecting, unavailable, and ended from events rather than ad hoc callbacks. Do not run a second semantic VAD in the browser. If server interruption cannot meet the silence gate, add local audio ducking only as a non-authoritative UX optimization; semantic cancellation still comes from the worker.

Exit when a credentialed Chromium session proves token ownership, real RTC connection through both direct UDP and TURN/TLS, genuine readiness, one audible reply, interruption, reconnect or bounded failure, and complete cleanup. No canvas or Deep Reasoner work may begin merely because a data channel opened.

### Milestone 2: reconnect the existing Murmur product

Extract one shared immutable session-context builder from duplicated chat/voice pipeline construction. It loads the owned agent, prompt/persona, session history, relevant memory, resources, model policy, and canvas capability without binding to a transport. Build separate mutable pipeline instances for text chat, Conductor, and Reasoner. Never share one `LLMPipeline`: it owns memory, tool callbacks, and canvas state. Disable Reasoner-side direct memory persistence and canvas publication; the coordinator alone commits the verified canonical answer. Text chat must continue using its own role-specific factory.

Connect committed Voice V2 turns to existing persistent sessions and agent configuration. Make session creation idempotent with one authoritative call and one stable client-generated session UUID used as the database primary key; a retry returns the existing owned row and a conflicting owner/agent fails. Remove the competing effect/offer behavior. Replace unauthenticated `sendBeacon` finalization with an authenticated explicit end action plus server-side disconnect/expiry cleanup. Do not rely on tab-close delivery for correctness.

Create a compatibility adapter from typed Voice V2 canvas events to the existing scene/canvas renderer. Define one `CanvasArtifact` discriminated union with at least `operations_v1` for normalized `CanvasOperation[]` and `sdl_scene_v2` for semantic SDL compiled in the browser. At this milestone, preserve visible behavior and prove both typed variants; do not add revision authority or completion acknowledgements yet.

Integrate Voice V2 only into `web/src/app/(app)/session/[agentId]/page.tsx` in this milestone. The generic `web/src/app/(app)/canvas/page.tsx` has neither an agent nor persistent session identity, so leave it on the legacy developer/demo path temporarily. Before cutover, redirect it to an owned/default agent session or remove it; do not create unauthenticated or transient V2 rooms to preserve that route.

Define memory semantics: one committed user turn, zero or more ephemeral acknowledgement/progress delivery events, and one canonical verified assistant result. If a task fails or is cancelled, persist that terminal fact without inventing a completed assistant answer.

Exit when one authenticated voice-plus-canvas scenario uses the existing agent/resource configuration, creates only one persistent session, stores correct canonical history, renders both supported typed artifact variants with parity, and remains usable in text mode if voice is unavailable.

### Milestone 3: add the two-tier brain and authoritative artifacts

Before adding task/event/artifact tables, introduce Alembic, the baseline/adoption schema guard, and explicit migration commands described above. This is intentionally deferred until the cheap transport and product slices pass.

Persist voice-call assignment before any percentage canary. The unique call ID/bootstrap key, room, dispatch ID, and selected profile make bootstrap transactional and reconnectable even after FastAPI restarts. Add the single authoritative `EventAppender`; task transitions and their durable events must commit atomically.

Implement the Conductor with a deliberately small policy surface. It can answer safe, short conversational turns; ask clarification; call only `start_task`, `cancel_task`, and `get_task_status`; acknowledge work without claiming completion; and present verified results. It owns exactly one generation/audio floor and cancels it idempotently on interruption.

Implement `ReasoningService` as an asynchronous loop for the single-host prototype. It receives immutable `ReasoningRequest` snapshots, invokes the existing full LLM pipeline, retrieval, search, and canvas-generation tools, and emits typed progress/result/proposal records. It writes every lifecycle transition through the repository. A correction increments task generation and supersedes the older task. Late results from a cancelled or superseded generation are retained for audit but cannot be published.

Implement `CanvasAuthority` around the `CanvasArtifact` union, so revisions apply to the semantic artifact envelope rather than pipeline-local `CanvasState` or compiled animation commands. It validates schema, task state, ownership, idempotency, and `base_revision`; assigns a new revision; publishes the patch; and waits for `canvas_apply_ack`. A lost acknowledgement leaves the result unverified and retryable by event ID. Narration claiming the artifact is visible additionally waits for `canvas_first_visible`; it never waits for the full GSAP timeline unless the claim is specifically about completed animation. A revision conflict rejects the proposal and asks the Reasoner to rebase or the Conductor to clarify; it never blindly overwrites visible state.

Implement the reconnect handshake and replay-or-snapshot convergence contract. Reconnect always stops orphaned audio, preserves or explicitly fails task execution according to its lease, restores the latest accepted canvas artifact/revision, and resumes only with new deltas after the browser acknowledges the snapshot.

Keep conversation available while the Reasoner works. Queue verified-result presentation if the user is speaking. Allow status/cancel/correction turns without blocking on the task. Ensure a synchronous tool call can never freeze audio input.

Exit when a 5-10 second deep task emits at least three observable progress/state transitions, the user can interrupt and converse during it, cancellation prevents all stale speech/canvas updates, a correction supersedes the old generation, and final narration occurs only after the durable result and any required render acknowledgement.

### Milestone 4: select the media/model profile empirically

Run the same audio, prompt, task, region, network profiles, and acceptance gates against at least:

* the measured legacy result;
* `livekit-agents-cascade`, using explicit STT, LLM, and TTS providers;
* `livekit-agents-realtime`, using one native speech-to-speech candidate only if it can emit/consume Murmur's transcript, task, cancellation, and canvas contracts.

Do not assume native speech-to-speech is better because its first audio is faster. Score recognition and critical entities, premature endpointing, interruption relevance, task success, canvas correctness, controllability, transcript/audit quality, and cost. Likewise, do not build a full RTC-only custom cascade just to justify LiveKit Agents. Self-hosted Agents is the default implementation; create a bounded `livekit-rtc-custom-cascade` spike only if Agents itself causes a measured failure or unacceptable compute/cost.

First tune profile configuration, prompts, and provider choices within the existing contract. If no profile passes, use the failure evidence to choose between a Pipecat runtime spike, provider replacement, or product-scope revision. Do not move the gates to make a preferred vendor win.

Exit with a written decision in this plan containing sample size, p50/p95 latency, endpoint and interruption errors, critical-entity and Hinglish results, task/canvas correctness, user preference if tested, cost per successful audio minute, and known limitations.

### Milestone 5: production hardening, cost, canary, and rollback

Add deterministic failure injection for missing dependencies, invalid credentials, provider 401/429/timeouts, empty or partial streams, duplicate/reordered STT segments, missing EOT, resumed speech, worker crash, duplicate delivery, late results, canvas conflicts, lost acknowledgements, reconnect, shutdown, and concurrent-session isolation. Every failure must end in a bounded explicit state; no retry storm or silent success is allowed.

Complete two distinct Playwright environments. Offline RTC E2E runs a pinned local LiveKit server, FastAPI with a test-process Firebase dependency override and seeded owned session, a fake Murmur worker that consumes real inbound audio frames and publishes a deterministic audio fixture, and Next.js. It uses no provider credentials and runs on every PR. Credentialed live E2E uses LiveKit Cloud and real providers, is scheduled/manual, and enforces the dollar budget. Browser tests must cover genuine readiness, one RTC turn, actual non-zero remote audio frames, long pauses, interruption during playback, correction during background work, artifact acknowledgement, provider-unavailable UI, reconnect/snapshot convergence, and cleanup. Never add a production auth bypass.

Persist stage spans and usage records. Server metrics use one monotonic clock within the process. Browser metrics use `performance.now()` and are reported as browser intervals; do not subtract browser and server clock readings. Record WebRTC RTT, jitter, packet loss, selected candidate type, and reconnect count. Replace undefined aggregate `latency_total_ms` with derived named spans.

For the first one-call internal test, SQLite is allowed. Before any multi-session production canary, migrate the ledger to Postgres and introduce durable task delivery. Test upgrade from the oldest supported schema, backup/restore, lease expiry, duplicate delivery, worker drain, and dead-letter handling.

Add `deploy/voice-worker.Dockerfile` and `deploy/README.md`. The image runs as a non-root user, installs only the locked production and `voice-v2` dependencies, includes the new `murmur.reasoning` and migration packages/data in the wheel, and starts through the pinned LiveKit Agents CLI using an entrypoint path proven to exist inside the built image. Do not assume `python -m murmur.voice.worker start`: LiveKit Agents 1.6.9 treats its positional entrypoint as a filesystem path. The worker registers outbound without public ingress, exposes a loopback-only liveness/readiness surface or equivalent platform check, reports successful LiveKit worker registration, and drains jobs on termination. Document required secrets, resource/concurrency limits, one warm replica, log/trace export, and the hosted deployment command. Update `.env.example`, `web/.env.example`, `.github/workflows/ci.yml`, `docs/ARCHITECTURE.md`, `docs/SETUP.md`, `docs/DEVELOPMENT.md`, `docs/DATABASE.md`, `docs/CHANGELOG.md`, and the root `README.md` as their behavior changes.

Canary new sessions only. First shadow the Reasoner without exposing results, then use an internal allowlist, then 5%, 25%, 50%, and 100% gates. Disable Voice V2 for new sessions and drain active calls on rollback. Provider-specific kill switches may disable one STT/TTS/model profile without reverting the whole architecture.

Exit after the selected profile clears all hard gates in live staging, cost records reconcile with provider usage, the canary holds for the required windows, rollback is exercised, and the operator runbook is complete.

### Milestone 6: cutover and deletion

Make Voice V2 the default only after Milestone 5. Retain the legacy path for two stable release windows, then remove `/offer`, `aiortc`, raw peer state, direct Deepgram socket code no longer used, data-channel PCM playback, duplicated frontend VAD/audio hooks, obsolete Smart Turn/Kokoro code, unused dependencies, old tests, and stale documentation.

Build a fresh wheel and a clean frontend artifact after deletion to prove no stale generated module remains. Run from a clean checkout with only documented environment variables. Perform one final credentialed browser session from connect through deep task, canvas render, interruption, end, and authoritative persistence read-back.

### Work sequencing and parallel lanes

One integrator owns contracts, schema, and milestone acceptance. After the event/state contracts land, work may proceed in bounded parallel lanes:

* backend transport lane: bootstrap route, LiveKit runtime, worker, readiness, cleanup;
* frontend lane: token bootstrap, session machine, audio/event connection, renderer acknowledgement, Playwright;
* orchestration lane: Conductor, Reasoner, task repository, canvas authority;
* evidence lane: replay corpus, instrumentation, cost ledger, live qualification, dashboards.

Do not parallelize competing edits to the same session lifecycle before the contracts are merged. Each lane rebases on the integration branch, runs its focused tests, and hands off an event-contract version. A milestone is integrated only when the whole repository gates pass.

### Explicitly not in scope for the first passing Voice V2

Do not self-host the LiveKit SFU, introduce A2A, expose user-authored tools, add a general agent marketplace, build a universal event bus, add Redis by default, rewrite the canvas renderer, migrate every repository to async, support multiple simultaneous assistant voices, or remove text chat. These may become separate plans after evidence shows they are necessary.

## Concrete Steps

The commands below assume the repository root is `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual` and Python 3.11 or 3.12.

1. Record the execution baseline and protect unrelated work.

       git status --short --branch
       git rev-parse --short HEAD
       uv sync --locked --extra dev
       uv run pytest -q
       (cd web && npm ci && npm run test -- --reporter=dot)

   Expected initial evidence at plan creation is commit `cdedc5d`, 46 backend tests, and 12 frontend tests. `web/next-env.d.ts` is already modified and must not be included in Voice V2 commits unless the user explicitly classifies it.

2. Add Voice V2 contracts, readiness, metrics, and replay fixtures.

   Create the Python and TypeScript event contracts, `murmur.reasoning` contract package, state machines, fixture folders, `scripts/voice_eval.py`, and `evals/voice/gates.json`. Add `murmur.reasoning` to the explicit setuptools package list immediately so contracts work from an installed wheel, not only the source tree. Add focused tests:

       tests/test_voice_v2_readiness.py
       tests/test_voice_v2_turn_assembly.py
       tests/test_voice_v2_metrics.py
       web/src/features/voice/events.test.ts
       web/src/features/voice/session-machine.test.ts

   Replace the incorrect immediate-turn assertion in `tests/test_voice_transcription.py` with explicit segment/EOT behavior. Make the matching bounded implementation fixes in `backend/murmur/voice/transcription.py` and `backend/murmur/voice/service.py`. Register the `live_provider` pytest marker now so offline and live suites cannot be mixed accidentally.

       uv run pytest tests/test_voice_transcription.py tests/test_voice_v2_readiness.py tests/test_voice_v2_turn_assembly.py -q
       (cd web && npm run test -- --reporter=dot)
       uv run python scripts/voice_eval.py replay --suite evals/voice/smoke.jsonl --profile fake --gates evals/voice/gates.json --assert-gates

   Run the replay twice and assert the normalized transition-trace hash is identical.

3. Capture a bounded credentialed legacy baseline.

   Add preflight and browser markers without redesigning the legacy transport. Use only valid, explicitly provided credentials. Store generated evidence under ignored `var/evals/<run-id>/`, never in source control.

       uv run python scripts/voice_eval.py preflight --profile legacy
       uv run python scripts/voice_eval.py live --suite evals/voice/smoke.jsonl --profile legacy --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --gates evals/voice/gates.json

   Record whether legacy passes or fails each gate. Stop legacy work after readiness, turn-commit correctness, and measurement are truthful.

4. Add the LiveKit dependency and authenticated bootstrap.

   Add a `voice-v2` optional dependency set in `pyproject.toml`, regenerate `uv.lock`, add `livekit-client` in `web/package.json`, and regenerate the lockfile. Add request/response schemas and ownership tests for `/api/voice/session`. The request requires an existing owned `session_id` and retry-stable `voice_call_id`. Test HMAC-derived room naming from trusted user/session/call scope, room-metadata profile stickiness, single-process per-call dispatch locking and lookup/create, restricted participant grants, and safe signed job metadata. Record that restart/replica-safe exactly-once dispatch is deferred to the Milestone 3 assignment table.

       uv sync --extra dev --extra voice-v2
       uv run pytest tests/test_voice_v2_bootstrap.py tests/test_api_security.py tests/test_authenticated_session_continuity.py -q
       (cd web && npm ci && npm run typecheck)

   Test unauthorized agent access, another user's session, expired token, duplicate bootstrap, duplicate dispatch, room-metadata mismatch, worker metadata mismatch, and secret non-disclosure.

5. Implement the minimal worker and production browser adapter.

   Add the standalone worker entrypoint, `livekit_runtime.py`, the fake/direct profile factory, `web/src/hooks/use-voice-session.ts`, event reducer, state machine, error UI, and audio-track handling. Integrate this production adapter in `web/src/app/(app)/session/[agentId]/page.tsx`. Keep the generic canvas page on its legacy developer/demo path until it is redirected or removed. Keep the legacy hook behind runtime assignment until cutover.

       uv run pytest tests/test_voice_v2_bootstrap.py tests/test_voice_v2_readiness.py -q
       (cd web && npm run lint && npm run typecheck && npm run test && npm run build)

6. Build and run a real offline RTC topology through that adapter.

   Add `@playwright/test`, `web/playwright.config.ts`, an initial `test:e2e:offline` script/project, `scripts/voice_e2e_stack.py`, and a `fake-rtc` profile. The stack runner starts a pinned local LiveKit server on loopback, FastAPI with a test-process auth override and seeded owned agent/session, the fake Murmur worker, and Next.js; waits for each readiness condition; runs the requested Playwright project; captures logs; and tears everything down. The fake worker must consume actual inbound audio frames, commit a deterministic transcript after known fixture audio, publish a deterministic remote audio fixture, and cancel that publication on a second speech fixture. It must not call real providers.

       uv run python scripts/voice_e2e_stack.py run -- npm --prefix web run test:e2e:offline

   In CI, install a pinned/checksummed LiveKit server binary or pinned local-server container and Chromium. The test-process auth override must be impossible unless the explicit E2E environment is active.

7. Run the minimal credentialed Cloud slice manually in three terminals.

       # Terminal 1, repository root
       uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000

       # Terminal 2, repository root
       uv run python -m livekit.agents start backend/murmur/voice/worker.py --dev

       # Terminal 3, repository root
       npm --prefix web run dev

   Use a credentialed internal account and a positive evaluation budget. Verify transport-connected and voice-ready separately, one spoken turn, actual non-zero remote audio frames, interruption, explicit dispatch, token refresh/reconnect, and cleanup. Record voice-call/room/dispatch/job/build/profile IDs in the trace.

8. Reconnect existing agent/session/memory/canvas behavior without new Voice V2 tables.

   Extract the immutable context builder and separate role-specific pipeline factories. Make persistent session creation idempotent with a client-generated session UUID primary key. Replace the unauthenticated beacon end path. Add the typed `CanvasArtifact` compatibility union and prove `operations_v1` plus `sdl_scene_v2` renderer parity. Do not add revision authority yet. Prove canonical-answer persistence and ensure Reasoner-style pipelines cannot persist or publish directly.

       uv run pytest tests/test_authenticated_session_continuity.py tests/test_memory_context.py tests/test_persistence_repositories.py tests/test_voice_v2_integration.py -q
       (cd web && npm run test -- --reporter=dot)

9. Add migrations, then the call/task/event ledger, Conductor, Reasoner, and canvas authority.

   Add Alembic, a baseline revision, existing-schema fingerprint/adoption, fresh-database upgrade, backup/refusal tests, and the Voice V2 schema migration. Replace application startup `create_all()` with a schema-current assertion; allow `create_all()` only through an explicit helper used from `tests/conftest.py`. Update `pyproject.toml` so migration packages and migration data are present in the wheel; `murmur.reasoning` was already added with the contract package in Step 2.

       uv run python scripts/migrate.py check
       uv run alembic upgrade head
       uv run pytest tests/test_migrations.py -q

   Add the durable voice-call assignment, task/lease, event, artifact-revision, and usage models/repositories. Enforce unique bootstrap/event/sequence constraints and atomic task-transition/event append. Add Conductor, Reasoner, and CanvasAuthority with an injected clock and virtual scheduler. All Voice V2 synchronous repository calls are bounded/offloaded from first use. Cap SQLite mode to one concurrent V2 call. Required focused suites are:

       tests/test_voice_v2_conductor.py
       tests/test_voice_v2_task_lifecycle.py
       tests/test_voice_v2_canvas_authority.py
       tests/test_voice_v2_cancellation.py
       tests/test_voice_v2_session_isolation.py
       tests/test_voice_v2_integration.py

       uv run alembic upgrade head
       uv run pytest tests/test_voice_v2_conductor.py tests/test_voice_v2_task_lifecycle.py tests/test_voice_v2_canvas_authority.py tests/test_voice_v2_cancellation.py tests/test_voice_v2_session_isolation.py tests/test_voice_v2_integration.py -q

10. Add reconnect convergence and split Playwright/CI jobs.

    Implement the reconnect handshake, durable replay, authoritative snapshot, canvas revision acknowledgement, and post-snapshot delta behavior. Extend the Playwright configuration with Chromium fake-media flags and checked-in synthetic/consented audio fixtures. Define separate scripts/projects:

        (cd web && npx playwright install chromium)
        uv run python scripts/voice_e2e_stack.py run -- npm --prefix web run test:e2e:offline

    `test:e2e:offline` runs only through `scripts/voice_e2e_stack.py` with local RTC and fake providers. `test:e2e:live` refuses to run without protected credentials and a positive budget. Update `.github/workflows/ci.yml` with offline unit/contracts and local RTC/browser jobs; add a protected scheduled/workflow-dispatch live-provider job. Test direct UDP and TURN/TLS in live qualification. For impaired networks, use `tc netem`, a controlled hotspot, or an equivalent transport-level tool outside ordinary PR CI.

11. Run profile qualification with a hard budget.

        uv run python scripts/voice_eval.py preflight --profile livekit-agents-cascade
        uv run python scripts/voice_eval.py live --suite evals/voice/qualification.jsonl --profile livekit-agents-cascade --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --gates evals/voice/gates.json --assert-gates

    Repeat for the native-realtime challenger only after it passes preflight and contract compatibility. No command may run live providers when `MURMUR_EVAL_BUDGET_USD` is unset or non-positive. The first qualification campaign has a cumulative cap of USD 25; increasing it requires explicit user approval.

        npm --prefix web run test:e2e:live

12. Move production state and package/deploy the worker before canary.

    Migrate the Voice V2 ledger to Postgres and introduce the selected durable delivery mechanism. Prove leases, heartbeat expiry, idempotent redelivery, dead-letter handling, worker drain, and task behavior across voice-job reconnect. Add `deploy/voice-worker.Dockerfile`, `deploy/README.md`, configuration examples, registration/readiness reporting, graceful termination, resource/concurrency limits, and observability export. Build and inspect the wheel and container before deployment.

        uv build
        python -m zipfile -l dist/*.whl
        docker build -f deploy/voice-worker.Dockerfile -t murmur-voice-worker:local .

13. Run full local gates after every integrated milestone.

        uv run ruff check .
        uv run ruff format --check .
        uv run pytest -m "not live_provider"
        uv run python -c "from main import app; print(app.title)"
        (cd web && npm run lint && npm run typecheck && npm run test && npm run build)
        uv run python scripts/voice_e2e_stack.py run -- npm --prefix web run test:e2e:offline

    Live-provider, load, and impaired-network suites remain separately triggered and budgeted. A green offline suite must never be reported as proof of the real voice experience.

14. Canary, roll back deliberately, then remove legacy code.

    Record internal, 5%, 25%, 50%, and 100% results in this plan. Exercise rollback before full rollout. After two stable release windows, remove the legacy files/dependencies and verify from a clean checkout and fresh wheel.

        uv build
        python -m zipfile -l dist/*.whl
        uv run pytest
        (cd web && npm ci && npm run check)
        uv run python scripts/voice_e2e_stack.py run -- npm --prefix web run test:e2e:offline

## Validation and Acceptance

Validation has two independent layers. Offline tests prove deterministic orchestration, isolation, and failure behavior. Credentialed browser tests prove the actual RTC/provider experience. Neither substitutes for the other.

### Required functional invariants

* The UI never reports voice-ready until the transport, worker, required provider/model path, and event channel are usable.
* Invalid credentials and missing required dependencies are discovered before Ready and produce an actionable unavailable state with text fallback.
* Repeating bootstrap with the same owned session/call ID returns the same active room, named-worker dispatch, and server-selected profile; a conflicting identity fails closed.
* Final transcript segments accumulate; only committed EOT dispatches a turn. Resumed speech cancels speculation before side effects.
* A turn commits at most once despite retries, reconnects, and duplicate events.
* Exactly one Conductor owns assistant speech. Interruption and cancellation are idempotent, and audio already heard is never replayed after a TTS retry.
* Task transitions follow the declared lifecycle. Cancelled and superseded generations cannot speak or mutate visible state.
* Canvas application requires the expected base revision, is idempotent by event ID, rejects stale patches, records `canvas_apply_ack` after scene insertion/scheduling, and records `canvas_first_visible` after the first meaningful rendered frame. Full animation completion is a separate optional event.
* Reconnect either replays the complete durable gap or applies one authoritative snapshot before new deltas; orphaned audio and stale interim transcripts never replay.
* No completion claim is spoken or stored before authoritative task verification. A claim that an artifact is visible additionally requires `canvas_first_visible`; it does not wait for the entire teaching animation.
* Session finalization leaves no active provider stream, worker task, room reference, or process-local registry state.
* No event, audio, task, memory, or canvas state crosses users or sessions.
* Application and worker startup refuse a stale schema; production code never calls `create_all()` or mutates schema outside the explicit migration step.

### Initial qualification gates

These are hypotheses to calibrate with the bounded baseline, not vendor promises. Report p50, p95, sample count, network profile, region, provider/model versions, and confidence intervals where useful.

Functional hard gates:

* zero false-ready sessions;
* zero silent successful turns in 100 qualification turns;
* zero cross-session events, duplicate side effects, stale spoken results, or stale canvas patches;
* 100% task/artifact schema validity and completion claims backed by authoritative state;
* premature turn split rate at or below 2% and incorrect turn merge rate at or below 2%;
* critical names, dates, numbers, and domain entities retained at or above 95%;
* Hinglish semantic-slot accuracy at or above 90% on the declared corpus;
* metrics completeness at or above 99%.

Latency targets on the declared qualification network:

* connect click to genuine agent-ready: p50 at or below 1.2 seconds and p95 at or below 2.0 seconds;
* acoustic speech end to committed turn: p50 at or below 350 ms and p95 at or below 700 ms for clearly complete turns;
* acoustic speech end to first audible browser frame for no-tool turns: target p50 at or below 800 ms and p95 at or below 1.2 seconds; hard rejection above 1.5 seconds p95;
* interruption speech start to local output silence: p95 at or below 250 ms with at least 95% true-interruption recall and at most 2% false interruption;
* accepted simple canvas patch received to visibly rendered: p95 at or below 700 ms;
* no unexplained silence longer than two seconds during deep work; acknowledgement/progress must remain truthful.

Reliability and load gates:

* connection success at or above 99.5% and unexpected session disconnects below 0.5% for canary windows;
* provider-failed turns below 1%; no unbounded retries or queue growth;
* at the declared initial concurrency, p95 response latency degrades by less than 20% from a single-session baseline;
* zero residual tasks or room/session references after cleanup;
* direct UDP, TURN/TLS, 150 ms RTT plus 2% packet loss, reconnect, and worker drain are explicitly exercised before production qualification.

Task-quality gates:

* the two-tier profile is non-inferior to the best single-tier profile within two percentage points on task completion;
* task context/handoff accuracy is at least 98%; conflicting progress/completion claims and duplicate tool side effects are zero;
* cancellation is observed by the Reasoner within 500 ms when it is not blocked inside an uncancellable external call;
* paired user testing is directional only at small sample sizes, but promotion should require at least 70% preference with no objective regression.

Cost gates:

* no live evaluation runs without a positive explicit budget; the initial campaign cap is USD 25;
* define `MAX_COST_PER_SUCCESSFUL_AUDIO_MINUTE_USD` from the intended price/usage model before canary;
* include RTC, downstream traffic, STT connection time, LLM tokens/cache, TTS characters/audio, worker compute and warm idle, Reasoner/tools, observability, retries, failures, and cancelled speculation;
* during profile selection, reject a profile more than 10% costlier than an equally good passing profile;
* during canary, roll back or pause expansion if cost per successful minute rises more than 20% after at least 100 completed sessions;
* alert at 70%, 85%, and 95% of any LiveKit/provider allowance; never auto-upgrade a plan.

### Failure-injection acceptance

The deterministic suite must inject readiness failure, transport timeout/reconnect/duplicate events, STT 401/429/disconnect/reordering/missing EOT/resumed speech, slow or partial LLM output, TTS 401/429/zero-byte/partial stream, worker queue delay/crash/duplicate delivery/late result, canvas revision conflict/lost acknowledgement/render exception, concurrent users, and shutdown during work.

Every case must end in a documented terminal or recoverable state. There must be no false Ready, silent success, stuck speaking/processing indicator, retry storm, repeated heard audio, stale result, canvas corruption, cross-session leak, or false completion statement.

### Evidence artifacts

Each eval writes only ignored artifacts under `var/evals/<run-id>/`:

    events.jsonl
    summary.json
    normalized-transcript.json
    state-trace.json
    artifact-diff.json
    cost.json
    failures.json

Every report names the commit SHA, runtime/profile version, provider/model versions, region, browser, network profile, session/turn counts, gate file hash, and whether providers were fake or real. Only synthetic or explicitly consented audio may be checked into the repository.

### Canary and rollback acceptance

Begin with a shadow Reasoner, then require at least 30 completed internal sessions and 300 turns over two days. The 5% stage requires at least 500 turns over 48 hours; the 25% stage requires at least 2,000 turns over 72 hours. Advance only while hard gates remain green. Add a 50% stage before full rollout.

Immediately stop new Voice V2 assignment for an ownership/security violation, cross-session leak, stale canvas corruption, false verified-completion claim, or persistence corruption. Automatically roll back new sessions after two consecutive monitoring windows with voice-ready failure above 2%, silent-turn or fatal-turn rate above 1%, p95 speech-end-to-playback above two seconds, metrics completeness below 99%, or the cost regression gate above. Active sessions drain on their existing sticky profile.

The plan is complete only when Voice V2 passes the offline and live gates, a provider/runtime profile is selected with measured cost, the two-tier task/canvas invariants hold, rollback is exercised, the selected runtime is the default, legacy code is removed after two stable release windows, documentation matches the shipped commands, and a fresh-checkout credentialed browser run proves the complete user journey.

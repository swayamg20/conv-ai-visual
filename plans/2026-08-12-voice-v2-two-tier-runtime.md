# Rebuild Murmur Voice, Qualify Two Runtime Arms, and Add a Two-Tier Brain

## Purpose / Big Picture

Murmur should feel like one present, interruptible thinking partner: it listens accurately, responds quickly enough to keep conversational rhythm, and can continue a deeper piece of reasoning or visualization without freezing the conversation. The current product idea remains sound. The raw realtime implementation does not.

This ExecPlan replaces the realtime voice boundary while preserving Murmur's authentication, agents, memory, resources, tools, persistent sessions, text fallback, and semantic canvas. It does not select a realtime runtime on architectural preference. Milestone 1 qualifies two concrete, mutually exclusive arms against the same Murmur contracts and evidence schema:

* **Milestone 1A, `livekit_v2`:** open-source LiveKit Agents in a Murmur-owned worker, LiveKit Cloud for signaling/media/NAT traversal, and Murmur's direct STT, LLM, and TTS provider accounts. It does not use LiveKit managed-agent hosting or managed inference.
* **Milestone 1B, `pipecat_smallwebrtc_v1`:** a Murmur-owned Pipecat pipeline, Pipecat SmallWebRTC for the browser-to-worker peer connection, and Murmur-operated Coturn for production NAT traversal, using the same direct-provider manifest and prompt revision. No LiveKit component participates in this arm.

Milestone 1C selects exactly one arm from comparable deterministic and credentialed live-equivalent evidence. Both arms may exist in the repository long enough to be evaluated, but one call has one realtime owner and one transport. No LiveKit/Pipecat hybrid is allowed. Product-layer reconnection, the two-tier brain, persistence, and canvas authority remain blocked until that selection is recorded, so product logic is implemented once against the selected runtime-neutral boundary rather than twice inside two SDKs.

The conversational architecture has two latency tiers but one public personality:

* The **Conversation Conductor** owns turn-taking, the audio floor, interruptions, clarification, short answers, and honest acknowledgements.
* The **Deep Reasoner** runs tools, retrieval, research, and visualization asynchronously. It cannot speak to the user or mutate the canvas directly.
* A deterministic task and artifact control plane connects them. The Conductor may narrate only verified results, and the canvas may apply only current, revision-compatible artifacts.

This is not a big-bang rewrite. The existing `/offer` runtime stays available behind a session-sticky feature flag until the selected Voice V2 arm proves itself. The first visible success is deliberately small and identical for each challenger: an authenticated browser establishes its arm's real RTC path, its self-hosted worker becomes genuinely ready, one utterance produces one audible reply, interruption stops it, bounded disconnect behavior is explicit, and all resources are cleaned up. Canvas and the second tier are integrated only after one arm wins that gate.

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
- [x] 2026-08-12 17:20 IST: Passed Milestone 1's provider-free real-media gate twice through an isolated production-shaped topology: digest-pinned LiveKit Server 1.13.1, the pinned LiveKit Agents worker running with process spawning, authenticated FastAPI bootstrap/release, production Next.js, and Chromium fake microphone capture. Run I sent 33,760 RTP bytes in 234 packets, decoded non-zero remote PCM, committed exactly two user turns, cancelled the first six-second reply, reached sustained browser silence 128.4 ms after the second acoustic onset, completed the second reply, then proved the exact room and dispatch absent, zero active worker jobs, and one matching profile close. The new independent CI job runs this same stack without provider credentials. This completes the local RTC sub-gate, not the credentialed Cloud/provider/TURN qualification, so Milestone 1 remains open.
- [x] 2026-08-12 18:18 IST: Closed the local RTC checkpoint's terminal-ordering audit before landing it. A post-Ready event-channel or AgentSession failure now ends the worker job; a pre-Ready session close cannot publish Ready; committed turns that fail before their first audio frame emit a terminal speech event; stale preemptive handles cannot steal a later turn; and canonical `agent_unavailable` or exact-agent departure always releases and rotates the browser call, including while microphone activation is pending. The final unshimmed Run J also proved that the exact microphone track was published disabled and LiveKit-muted before authenticated Ready, then carried 33,842 outbound RTP bytes in 234 packets, decoded non-zero remote PCM, silenced the interrupted reply in 128.6 ms, and completed authoritative cleanup. Final local gates are 300 backend tests and 117 frontend tests plus clean Ruff lint/format, ESLint, TypeScript, lock validation, and the isolated production browser stack.
- [x] 2026-08-12 18:55 IST: Clarified the runtime comparison scope after the local LiveKit checkpoint. The second implementation is not the old hand-built `/offer` path and not merely a written escape hatch: it will be a Pipecat plus SmallWebRTC challenger with no LiveKit component in that call. LiveKit Agents and Pipecat remain mutually exclusive realtime owners, while both consume the same Murmur profile, event, readiness, lifecycle, and evaluation contracts. Self-hosted SmallWebRTC avoids a managed RTC per-minute charge but still incurs Murmur compute, bandwidth, direct-provider, and production TURN costs. No claim is made that this challenger exists yet.
- [x] 2026-08-12 19:10 IST: Audited the post-`cf1ddcd` Milestone 1 exit. The local LiveKit media proof is complete, but at that checkpoint the production worker still installed `UnavailableVoiceProfileProvider`, `scripts/voice_eval.py live` still refused rather than driving a browser/provider run, the browser proof did not yet record a selected ICE candidate pair, and no Pipecat/SmallWebRTC/Coturn implementation existed. Reframed Milestone 1 as executable arms 1A and 1B plus a mandatory 1C selection gate; preserved every completed LiveKit checkpoint and moved product/two-tier work behind selection. Current uncommitted provider-factory work remains in-progress rather than completed evidence.
- [x] 2026-08-12 20:54 IST: Pushed four bounded follow-up checkpoints through `d11de75`. The plan and guarded dual-runtime qualification contracts are committed; the LiveKit worker now constructs an explicit direct Deepgram/Groq/ElevenLabs profile with one authoritative bounded metadata preflight and enriched Ready evidence; and the locked optional Pipecat 1.7/SmallWebRTC dependencies plus exact browser packages are installed. GitHub Actions passed backend, frontend production build, and the credential-free LiveKit browser RTC proof at every checkpoint. No paid provider, Cloud, or public TURN call has been made, so those gates remain unmeasured.
- [x] 2026-08-13 22:29 IST: Pushed the bounded internal Pipecat challenger checkpoints through `db6e881`. Commits `7e34f26` and `67d19ed` add authenticated single-use signaling ownership and bounded automatic terminal cleanup; `9569dbb` and `b962f0d` add explicit Daily ownership plus a Ready-gated SmallWebRTC browser adapter; `4b9559c` adds the bounded Pipecat direct-provider pipeline and canonical event bridge; `df575ff` adds exact trusted-scope release; and `db6e881` adds claim-bound ICE leases plus the only authenticated browser bearer projection. The latest ICE/projection slice passed 54 focused tests and an independent P0/P1 review. This is an internal ownership checkpoint, not the Milestone 1B media exit: the authenticated HTTP composition, retry-stable bootstrap owner, deterministic Pipecat/Coturn browser stack, direct and forced-relay evidence, live qualification, and runtime selection remain open. No paid provider, Cloud, or public TURN call was made.
- [x] 2026-08-14 01:13 IST: Pushed `10fd3c3`, the retry-stable Pipecat bootstrap and signaling lifecycle checkpoint. The process-local bootstrap owner now authenticates exact session and agent ownership, reuses one immutable assignment only while signaling is nonterminal, makes release intent win over cached retries, transfers cancelled cleanup to bounded owned reconciliation, retains both bootstrap and signaling capacity until `cleanup_complete=true`, and makes failed shutdown retryable instead of reporting false success. Two independent adversarial reviews found no P0/P1 blocker. Evidence: 94 focused lifecycle tests, 665 backend tests, 138 frontend tests, Ruff lint/format, ESLint, TypeScript, and the Next production build passed. This remains an internal control-plane checkpoint: claim-scoped ICE must be bound before handler construction, and no Pipecat RTC, public Coturn, provider, or cost result is claimed.
- [x] Milestone 0: established truthful readiness, event contracts, deterministic replay, and an explicitly failed legacy baseline.
- [ ] Milestone 1A: qualify the completed LiveKit direct-provider factory through authenticated LiveKit Cloud direct UDP and forced TURN/TLS with explicit bounded fresh-call failure.
- [ ] Milestone 1B: implement Pipecat plus SmallWebRTC plus Coturn and qualify both deterministic direct/forced-relay paths and a credentialed live-equivalent path.
- [ ] Milestone 1C: compare both complete evidence bundles and select exactly one runtime arm; if neither bundle is complete or neither passes, stop before product integration.
- [ ] Milestone 2: reconnect Voice V2 to Murmur's existing agent, session, memory, chat, and canvas product layers.
- [ ] Milestone 3: implement the Conversation Conductor, Deep Reasoner, durable task ledger, and revisioned artifact authority.
- [ ] Milestone 4: optimize cascaded and native-realtime provider profiles inside the already-selected runtime without reopening the runtime decision casually.
- [ ] Milestone 5: harden the selected runtime and integrated product with failure injection, full browser scenarios, cost accounting, production persistence, canary controls, and rollback.
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

The first real local-SFU runs exposed integration behavior that unit fakes could not prove. Creating a room before attaching the intended agent caused the pinned server to create an unnamed default dispatch, so room creation now carries the exact named dispatch, signed metadata, and `JRP_NEVER` policy atomically. A nested worker entrypoint passed unit tests but could not be pickled by the production process executor, so LiveKit callbacks are module-level callable objects that construct process-owned resources in the child. With zero idle processes, the first child subscribed only after the browser fixture had spoken both turns; the worker now keeps one process warm and the browser publishes its microphone muted, unmuting exactly once only after authenticated Ready. This trades one bounded idle process for a usable first call while the one-active-job gate still caps provider work.

Playwright launches Chromium with `--mute-audio` by default. That made a correctly subscribed and published agent track measure as silence at the media element even though the worker emitted non-zero PCM. The offline project removes only that default flag and samples the decoded remote `MediaStreamTrack` while preserving actual element playback. The passing proof also found that the pinned server returns 503 from room-scoped `ListDispatch` after the room actor has disappeared. The cleanup verifier now treats an exact successful `ListRooms` absence as authoritative absence of the room-scoped dispatch and never issues the invalid follow-up query.

SQLite plus `SQLModel.metadata.create_all()` is acceptable for today's local prototype but not for evolving durable task state across multiple processes or hosts. A versioned migration mechanism is required before adding Voice V2 tables. SQLite and an in-process async Reasoner may remain for the first single-host prototype; Postgres and a durable queue become mandatory before more than one production worker host or automatic task recovery is claimed.

The `cf1ddcd` checkpoint did not contain LiveKit direct-provider plugin pins or a usable production profile. Commit `c15b6e8` closed that code gap with explicit Deepgram, Groq, and ElevenLabs plugin objects, static admission, one authoritative bounded metadata preflight, job-owned cleanup, and provider/model/config readiness evidence. This proves construction and failure behavior without spending money; it does not prove streaming credentials, quota, recognition, generation, synthesized audio, provider latency, or Cloud transport. Those remain live qualification gates.

The evaluator and browser harness stop at different boundaries. `scripts/voice_eval.py live` currently rejects the request rather than orchestrating a credentialed browser session, while `scripts/voice_e2e_stack.py` proves real local media with fake providers but does not capture the selected ICE candidate pair. The qualification work therefore needs one evidence schema and runtime-specific stack drivers; neither the replay evaluator nor an RTP byte counter alone proves direct UDP, relay, or TLS.

Pipecat's default development runner cannot accept a custom TURN server. The challenger must own a small authenticated signaling route that constructs `SmallWebRTCConnection` with explicit ICE servers. Coturn is not optional production polish: direct peer-to-peer reachability is topology-dependent, so both direct and forced `turns:` relay cases are Milestone 1B gates. This also means “open source” and “no managed RTC line item” are not synonyms for zero infrastructure cost or production equivalence.

The Pipecat implementation exposed two additional ownership boundaries that must stay explicit. First, a retry-stable bootstrap cache cannot return an otherwise valid cached bearer after signaling has already terminalized the peer; it must inspect a safe internal lifecycle snapshot and require a fresh call ID. Second, TURN credentials can remain redacted and immutable in the retained control-plane lease, but the fresh aiortc object handed to SmallWebRTC necessarily contains the plaintext transport values. That third-party SDK object is an ephemeral sink and must never be logged, generically serialized, or retained as assignment state; pretending a string subclass made it serialization-safe was rejected during review.

The two runtime SDKs cannot share constructed media/provider objects. LiveKit `AgentSession` expects LiveKit plugin objects; a Pipecat pipeline expects Pipecat services and frame processors. The reusable boundary is the authoritative call claims, provider/model manifest, Murmur event semantics, readiness result, lifecycle result, browser control surface, corpus, and evidence format. Factories and media lifecycles stay runtime-specific.

## Decision Log

2026-08-12, Codex: Preserve the Murmur product and domain layers; replace the realtime voice boundary. The user's failed test does not justify deleting authentication, memory, agent configuration, chat, resources, or canvas code.

2026-08-12, Codex: At plan creation, begin the first implementation with open-source LiveKit Agents in a self-hosted Murmur worker and LiveKit Cloud for RTC only. Use direct provider accounts. Do not buy LiveKit managed-agent hosting or inference as a hidden dependency. The later two-arm decision below supersedes “first implementation” as an implicit production selection while preserving the completed LiveKit work as Milestone 1A evidence.

2026-08-12, Codex: Use one realtime owner per call. LiveKit `AgentSession` owns media, STT turn input, TTS output, and interruption in `livekit_v2`; the Pipecat pipeline and `SmallWebRTCTransport` own them in `pipecat_smallwebrtc_v1`. Do not combine both runtimes in one call or let one runtime's provider objects enter the other.

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

2026-08-12, Codex: Explicit dispatches use restart policy `JRP_NEVER`; browser failures recover through an authenticated fresh-call flow instead of automatic worker-job relaunch. The worker remains capped to one active job and a deliberately small framework retry surface. The real first-call trace supersedes the earlier zero-idle choice: keep exactly one process warm, and keep the published browser microphone muted until canonical Ready, so process startup cannot discard speech that the UI appeared ready to accept.

2026-08-12, Codex: Fix `murmur.voice.v2.events` as a versioned wire-protocol topic. Remove the environment override because the strict frontend decoder must not accept an arbitrary server-selected channel. Browser tokens may publish microphone media but not data, and LiveKit text input is disabled to prevent an unaudited second route into the model.

2026-08-12, Codex: Retain release tombstones for a fixed 900 seconds rather than deriving their lifetime from the selected 30-900 second token/profile TTL. This is a bounded process-local safety horizon for bootstraps that already passed admission before cancellation; it prevents a delayed authorization from resurrecting the call. Durable cancellation state replaces this timer in Milestone 3.

2026-08-12, Codex: Establish focused module boundaries before implementing RTC media, but do not decompose the assignment/tombstone state machine merely to reduce line count. Pure bootstrap contracts, worker authorization/session/runtime composition, browser assignment release, and LiveKit transport are independently testable; the concurrency-sensitive local registry remains one owner until a later behavior-driven persistence boundary replaces it.

2026-08-12, Codex: Create a new LiveKit room and its sole named worker dispatch in one control-plane request, with exact signed job metadata and `JRP_NEVER`. The pinned server otherwise creates a backward-compatible unnamed dispatch before Murmur can reconcile the room. Keep the browser participant unable to publish data, and continue treating microphone media as the only realtime client input.

2026-08-12, Codex: Qualify the local RTC slice with one guarded, provider-free stack command that owns dedicated loopback ports, a digest-pinned SFU, a seeded file-backed database, a production-spawn worker, an isolated Next build, and Chromium. Acceptance requires real inbound frames, outbound RTP, decoded browser PCM, canonical turn and speech events, interruption-to-silence at or below 250 ms, no stale first-reply audio before the second canonical reply, and authoritative teardown. CI uploads the evidence on success or failure. This proof does not substitute for credentialed provider, Cloud/TURN, impaired-network, or cost qualification.

2026-08-12, Codex: Promote Pipecat plus SmallWebRTC from an emergency exit sentence to the explicit non-LiveKit challenger. Do not use the legacy raw-WebRTC runtime as the comparison arm and do not stack Pipecat underneath LiveKit Agents in one call. Extract only the transport/runtime-neutral Murmur contracts needed by both owners, then run both against the same provider profile, browser corpus, readiness rules, failure injection, and cost accounting. SmallWebRTC may be self-hosted, but production NAT coverage still requires an explicitly costed TURN service and must not be described as free infrastructure.

2026-08-12, Codex: Split the open runtime work into Milestone 1A (`livekit_v2`), Milestone 1B (`pipecat_smallwebrtc_v1`), and Milestone 1C (selection). Both arms must pass provider-free deterministic media and credentialed live-equivalent qualification before a like-for-like winner can be declared. If external credentials or reachable infrastructure prevent one arm's live run, mark that arm `unmeasured`; do not award the other arm a comparative win merely because it was easier to provision. A production default may still be selected only from complete passing evidence, otherwise stop at the gate.

2026-08-12, Codex: Runtime selection precedes agent/memory/canvas integration and precedes the Conductor/Reasoner split. Shared product logic may depend on `VoiceRuntimeKind`, authoritative call claims, a discriminated browser assignment, canonical Murmur events, and a narrow `VoiceTransport` interface. It may not depend on LiveKit rooms/dispatches or Pipecat peers/pipelines. Once Milestone 1C chooses an arm, the rejected arm receives only security/cleanup fixes needed to keep its evaluation branch safe; it does not receive duplicated product features.

2026-08-12, Codex: Milestone 1 proves only bounded disconnect behavior: stop audio, close the exact runtime resources, release the assignment, rotate `voice_call_id`, and offer an authenticated fresh call or text mode. Durable reconnect convergence, replay, authoritative snapshots, and task survival are Milestone 3 work and must not be claimed by the Milestone 1 browser test.

2026-08-12, Codex: Compare total cost per successful audio minute, not one vendor invoice line. The LiveKit arm includes Cloud RTC/TURN, Murmur worker compute and warm idle, direct providers, traffic, observability, retries, cancelled work, and failed turns. The Pipecat arm includes Murmur signaling/pipeline compute and warm capacity, Coturn compute and relay bandwidth/egress, certificates/load balancing, the same direct providers, observability, retries, cancelled work, and failed turns. Provider and shared product costs may be common, but they remain measured. Fake/local runs produce no production cost claim.

2026-08-13, Codex: Keep Pipecat's retained ICE lease as the sole immutable, redacted source of truth and make browser/aiortc projection deliberate authorization or transport sinks. Public signaling requires HTTPS plus explicit TURN material; empty/direct ICE is a loopback-only qualification mode, including when an issuer is injected. Never claim that an SDK object containing functional TURN credentials is safe for generic logging or serialization.

2026-08-13, Codex: A Pipecat bootstrap retry may reuse the exact assignment only while the signaling owner reports that reservation as nonterminal. Terminal media/runtime state requires a fresh `voice_call_id`, even while the local assignment TTL remains valid. Use a safe process-internal lifecycle snapshot rather than exposing the opaque bearer or repository/runtime objects to the bootstrap cache.

2026-08-14, Codex: Treat Pipecat terminal publication and resource cleanup as distinct lifecycle facts. A terminal result does not free bootstrap or signaling capacity until the signaling owner reports `cleanup_complete=true`; transient release, status, cancellation, or shutdown failure transfers to one bounded owned reconciliation path, and a close operation remains retryable while any exact call resource is retained. This prevents a second assignment from being admitted while the first peer, handler, or provider pipeline is still alive.

## Outcomes & Retrospective

Milestone 0 completed on 2026-08-12. Voice V2 now has strict Python and TypeScript event/task/artifact contracts, a fail-closed frontend state model, named clock-domain-safe metric spans, a provider-free replay harness, and a shared production/evaluation transcript accumulator. Seven replay scenarios passed twice with identical combined trace hash `8a1cdfbf49acedeb4ef1f6cb516ff15f670ed6009ad303dc13f6530e275edff9`. Offline evidence was 124 backend tests, 52 frontend tests, Ruff, ESLint, TypeScript, Next production build, app import, and a fresh wheel containing the new contract/evaluation packages.

The exact live result is **failed before session start**, not measured: legacy preflight found Deepgram, Groq, and Firebase configured; ElevenLabs missing/placeholder; Kokoro fallback absent; and Smart Turn selected locally without its model/download dependencies. Exit status was 1, network verification was false, no provider call was made, and all live latency/reliability/cost gates remain `unmeasured`. `StageRecorder` is deliberately a contract in this milestone, not yet production wiring; the legacy client has no acoustic-end, first-audible, or interruption-to-silence timestamp and reports those spans as unavailable. Therefore there are no honest p50/p95, metrics-completeness, or cost-per-minute numbers for Milestone 0. The decision is to continue to the fake-provider local-RTC slice in Milestone 1, where the browser and worker timestamp chain becomes executable, while retaining this failed legacy result as the comparison baseline.

The partial Milestone 1 implementation now crosses the provider-free local media gate. The final unshimmed Run J used the production browser hook, restricted token, exact participant and worker identities, named dispatch, real LiveKit/Opus path, spawned worker, deterministic STT/LLM/TTS profile, and browser audio element. It proved the exact microphone track was disabled and LiveKit-muted before authenticated Ready, then observed local PCM peak `0.578475`, remote decoded PCM peak `0.089542`, 33,842 outbound RTP bytes in 234 packets, two committed turns, 11 canonical events, and interruption-to-sustained-silence of `128.6 ms`. The first speech stopped as interrupted, the second completed, and no active PCM from the first reply appeared in the guarded interval before the canonical second reply.

The same run completed the lifecycle proof: the browser released its exact microphone track and assignment; fake-provider evidence contained 859 input frames, exactly two speech onsets and ends, two final transcripts, two TTS starts, one cancellation, one completion, and one matching `profile_closed`; the exact LiveKit room and room-scoped dispatch were absent; and worker health reported zero active jobs. The repeat followed earlier browser-media passes at `146 ms` and `128.4 ms`, so the result is not a one-off successful sample. The repository-wide checkpoint evidence is 300 backend tests and 117 frontend tests plus clean Ruff lint/format, ESLint, TypeScript, lock validation, and an isolated production Next/browser build.

Milestone 1A is still open. Its completed sub-gates are the LiveKit control plane, direct-provider factory, guarded live-qualification contracts, and provider-free local RTC proof above. Its remaining deliverables are an executable credentialed browser evaluator, measured LiveKit Cloud direct-UDP and forced-TURN/TLS runs, selected-candidate evidence, budgeted cost evidence, and the bounded fresh-call failure scenario. No provider quality, provider latency, Cloud transport, or production cost result is claimed by construction tests or the local deterministic proof.

Milestone 1B now has reviewed internal components but has not crossed a Pipecat media gate. Commit `d11de75` pins Pipecat 1.7, its direct-provider integrations, SmallWebRTC, and the exact browser SDK pair; `de068ff` defines common claims, assignment, lifecycle, and guarded qualification boundaries. Commits through `db6e881` add the bounded signaling owner and cleanup retry, direct-provider pipeline and readiness gates, canonical event mapping, Ready-gated browser transport with explicit Daily lifecycle ownership, trusted exact-call release, immutable claim-bound ICE leases, and authenticated browser assignment projection. Commit `10fd3c3` adds the retry-stable authenticated bootstrap owner and explicit cleanup-completion coupling between bootstrap and signaling capacity. The existing legacy `/offer` code remains outside the challenger, and none of these construction/lifecycle tests proves an audible SmallWebRTC call. The open work is claim-scoped ICE binding before handler construction, authenticated HTTP composition, the runtime-neutral browser controller, deterministic fake media, production-shaped Coturn configuration, direct and forced-relay browser paths, authoritative end-to-end cleanup, credentialed live-equivalent qualification, and the complete cost record.

Milestone 1C is therefore also open, and there is currently no selected Voice V2 production runtime. Record the final runtime decision here only after both evidence bundles are comparable. The decision must name any failed hard gate, sample size, latency and interruption distributions, recognition/task correctness, selected candidate types, cleanup, operational limits, and total cost per successful audio minute. If neither arm passes, preserve text mode and stop rather than integrating product logic into an unqualified runtime.

After a runtime is selected, if the two-tier design improves acknowledgement latency but worsens task correctness, cancellation, or user trust, remove the split rather than defending the architecture.

## Context and Orientation

### Current product boundaries

`main.py` creates the FastAPI application from `backend/murmur/api/application.py`. The application currently constructs one `RuntimeRegistry`, `ChatService`, and `VoiceService`; starts voice services in the FastAPI lifespan; registers built-in tools; and owns process-local cleanup.

`backend/murmur/api/routers/voice.py` exposes authenticated SDP negotiation at `/offer`. `backend/murmur/voice/service.py` and the other files under `backend/murmur/voice/` own `aiortc`, Deepgram, Smart Turn, LLM/TTS orchestration, data-channel events, and teardown. `backend/murmur/runtime/registry.py` stores `RTCPeerConnection`, `RTCDataChannel`, Smart Turn state, one turn task, and timing dictionaries in the same `VoiceRuntimeSession`.

`backend/murmur/chat/service.py`, `backend/murmur/voice/pipeline.py`, and `backend/murmur/llm/pipeline.py` construct agent-aware LLM pipelines. This logic must be shared before creating Conductor and Reasoner roles so voice and text do not drift.

`backend/murmur/persistence/models.py` and `backend/murmur/persistence/repositories/` store users, agents, sessions, messages, memories, resources, tool definitions, and aggregate observability. `backend/murmur/persistence/database.py` creates tables with `create_all()`. There are no versioned migrations.

`web/src/app/(app)/session/[agentId]/page.tsx` and `web/src/app/(app)/canvas/page.tsx` coordinate the current voice hook and the canvas. `web/src/hooks/use-webrtc.ts`, `use-audio.ts`, and `use-vad.ts` own the raw browser transport, chunk playback, and local interruption path. `web/src/features/canvas/`, `web/src/lib/scene-kit/`, and `web/src/components/svg-canvas.tsx` are the semantic rendering layer and should remain.

### Terms used in this plan

**Transport-connected** means the browser has established the selected arm's RTC path: joined the exact LiveKit room or completed the exact SmallWebRTC peer negotiation. It does not mean the agent can understand or answer.

**Voice-ready** means the assignment, worker/pipeline, microphone path, required model/provider connections, and event channel have all passed readiness checks for the selected profile. Only this state may trigger the Ready UI and sound.

**Turn** is one semantically committed user contribution. A provider may emit several final transcript segments inside one turn.

**Task** is a durable unit of deep work requested by a committed turn. Its lifecycle is `queued`, `working`, `needs_input`, `verified`, `failed`, `cancelled`, or `superseded`.

**Artifact proposal** is the Reasoner's typed suggestion for canvas state. It includes a base canvas revision and provenance. It is not visible state until the canvas authority accepts it and the browser acknowledges rendering.

**Canonical answer** is the verified assistant result stored for memory and replay. Acknowledgements and progress speech are delivery events, not separate canonical answers.

**Runtime arm** is one complete and exclusive media/orchestration owner. The only Milestone 1 candidates are `livekit_v2` and `pipecat_smallwebrtc_v1`. The legacy `/offer` path is a rollback baseline, not a candidate.

**Provider manifest** is the runtime-neutral, versioned declaration of STT, turn policy, LLM, TTS, model IDs, prompt revision, and readiness requirements. Each runtime has its own factory that turns the manifest into SDK-specific objects. Constructed SDK objects are never shared across arms.

**Profile** is a provider manifest bound to one runtime and one configuration revision. Milestone 1 compares `livekit-agents-cascade-v1` with `pipecat-direct-cascade-v1` using the same direct provider accounts, models, prompt, and turn policy. Later examples include a native-realtime profile inside the selected runtime.

**Deterministic qualification** uses the production browser adapter and real RTC media path with checked-in audio and deterministic fake STT/LLM/TTS components. It requires no external provider credentials. It proves lifecycle and media contracts, not recognition quality, global network behavior, or production cost.

**Live-equivalent qualification** uses the production adapter, direct provider accounts, HTTPS/WSS, the intended worker process/container shape, and a real cross-network transport path. LiveKit runs through Cloud; Pipecat runs through a reachable Murmur host and production-shaped Coturn. “Equivalent” means the same corpus, provider manifest, browser, region, network shaping, evidence schema, and gates, not that the two infrastructures offer identical global operations.

**Coturn** is the explicit STUN/TURN service for the Pipecat arm. The forced-relay case uses authenticated `turns:` over TLS and must prove the selected relay candidate, not merely that a TURN URL was configured.

### Target architecture and ownership

The pre-selection shape is:

    Browser
      | authenticated `/api/voice/session` bootstrap
      v
    FastAPI control plane --------------------------> existing ownership/session data
      | returns one discriminated, sticky assignment
      +-------------------------------+-------------------------------+
      | `livekit_v2`                  | `pipecat_smallwebrtc_v1`       |
      v                               v                               |
    LiveKit Cloud RTC              Murmur Pipecat RTC service         |
      ^                               ^                               |
      | room media + events           | SmallWebRTC + events           |
      |                               | direct ICE or Coturn relay     |
      v                               v                               |
    LiveKit Agents worker           Pipecat pipeline worker           |
      +-------------------------------+-------------------------------+
                                      |
                                      v
                         selected Voice Runtime Port
                         (M2 product context; M3 Conductor/Reasoner)

FastAPI remains the authority for Firebase identity, agent ownership, persistent-session lookup, runtime selection, and the stable `session_id` plus client-generated `voice_call_id`. `VOICE_RUNTIME=legacy|livekit_v2|pipecat_smallwebrtc_v1` is a deployment-owned server setting; the bootstrap request never contains a runtime. `NEXT_PUBLIC_VOICE_RUNTIME=legacy|voice_v2` only decides whether the page uses the old route or asks for a V2 assignment. When it asks for V2, the response discriminant, not a second public flag, selects the browser adapter. A mismatch fails unavailable rather than starting a different runtime.

The common bootstrap response contains `runtime`, `profile_id`, `trace_id`, `session_id`, `agent_id`, `voice_call_id`, `event_protocol`, and `expires_at`. Its `livekit_v2` variant additionally contains `server_url`, `room_name`, `participant_token`, `participant_identity`, `agent_participant_identity`, `dispatch_id`, `worker_name`, and `event_topic`. Its `pipecat_smallwebrtc_v1` variant additionally contains one short-lived, single-use, opaque `webrtc_url`, `peer_reservation_id`, and `event_protocol="rtvi-murmur-v2"`. The URL contains no identity or provider secret, expires within the same 30-900 second policy, is redacted from access logs and referrers, and can create only its exact reserved call. Reuse, expiry, owner mismatch, runtime mismatch, or a second active peer fails closed.

The existing LiveKit implementation keeps its HMAC-derived room, per-call lock, exact named dispatch, restricted participant token, signed job metadata, and process-local one-call cap. Its participant cannot publish data or create arbitrary rooms. With LiveKit Agents 1.6.9, run `backend/murmur/voice/worker.py` through the pinned CLI path. The LiveKit worker connects outbound; the FastAPI control plane does not own its media peer.

The Pipecat arm runs a separate ASGI/media entrypoint, `backend/murmur/voice/pipecat_app.py`, rather than adding a peer to the main control-plane lifespan. Its authenticated bootstrap creates only a bounded process-local reservation. The single-use signaling URL reaches `backend/murmur/voice/pipecat_signaling.py`, which validates the reservation and authoritative call claims, constructs one `SmallWebRTCConnection` with explicit ICE servers, starts one Pipecat pipeline, and owns peer/pipeline teardown. The default Pipecat development runner is not used for qualification because it cannot inject Murmur's custom TURN configuration.

### Shared and runtime-specific contracts

Create `backend/murmur/voice/runtime_contracts.py` and matching discriminated unions in `web/src/features/voice/session-api.ts`. `VoiceRuntimeKind`, `VoiceCallClaims`, the common assignment fields, `VoiceRuntimeTerminalResult`, and lifecycle reason codes are shared. Preserve `backend/murmur/voice/contracts.py` and `web/src/features/voice/events.ts` as the only Murmur event vocabulary. LiveKit reliable data and Pipecat RTVI app messages are transport encodings of that vocabulary, not separate product event models.

Keep the shared parts of `backend/murmur/voice/profile.py` runtime-neutral: `VoiceProfileScope`, provider/model manifest, session/media policy, and `ProfilePreflight`. Runtime-specific prepared objects and factories live under `backend/murmur/voice/provider_profiles/`: `livekit_cascade.py` produces LiveKit plugin objects and `pipecat_cascade.py` produces Pipecat services/frame processors. Preserve compatibility exports from `profile.py` while Milestone 1A work is in flight, then make imports explicit before 1B. Both factories consume the same manifest and readiness requirements, but produce different SDK objects and close them independently. The common preflight result names configured, reachable, and ready components without leaking credentials.

Add a deliberately small `VoiceTransport` interface in `web/src/features/voice/voice-transport.ts`: prime browser audio, connect the matching assignment, activate the already-created microphone only after canonical Ready, enable/disable microphone, enable/disable output, resume blocked playback, expose runtime-neutral callbacks, and close idempotently. `livekit-transport.ts` and `pipecat-transport.ts` implement it. Session state and event reduction remain shared; room, participant, dispatch, peer, ICE, RTVI, and pipeline objects never cross the adapter boundary.

Runtime-specific ownership remains explicit. LiveKit owns Cloud room/dispatch/job lifecycle, grants, data topic, and `AgentSession`. Pipecat owns one-time signaling, `SmallWebRTCConnection`, `SmallWebRTCTransport`, frame processors, RTVI mapping, Coturn credentials, and peer/pipeline cleanup. Shared code owns identity, selection, provider manifest, readiness semantics, event semantics, user-visible state, the corpus, measurements, and evidence comparison. This is a narrow port, not a universal transport framework.

The Conductor owns all assistant speech. For a small conversational request it may answer directly. For deep work it may clarify or say that it is working, then enqueue a typed task. While the Reasoner runs, the Conductor remains interruptible and can answer status questions, accept corrections, or cancel/supersede the task. When a verified result arrives, the Conductor presents it only when it owns the audio floor and the result still belongs to the current session/task generation.

The Reasoner receives an immutable request snapshot: user identity, session, agent configuration revision, committed turn text, relevant memory/resource references, tool policy, task generation, and current canvas revision. It emits progress facts, a final typed result, optional artifact proposals, usage records, and errors. It cannot call TTS or publish runtime transport data directly.

The canvas authority accepts a proposal only when its `base_revision` matches the current authoritative revision and its task is not cancelled or superseded. Accepted patches receive a new revision and are sent to the browser. Because `SVGCanvas.render()` schedules GSAP work and returns before animation completes, the browser emits distinct acknowledgements: `canvas_apply_ack` after validation and insertion/scheduling into the scene, `canvas_first_visible` after the first meaningful rendered frame, and optional `canvas_animation_complete` when a teaching timeline ends. Spoken language such as "I have drawn it" waits for `canvas_first_visible`, not the entire animation. State convergence may rely on `canvas_apply_ack`; the 700 ms visible-render gate uses `canvas_first_visible`.

### Event contracts

The completed Milestone 0 files `backend/murmur/voice/contracts.py` and `web/src/features/voice/events.ts` define the shared envelope. Every event contains:

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

Ordering and idempotency use event IDs, producer-local sequence, causation/correlation IDs, task generation, and canvas revision, never wall-clock ordering. The browser tracks each producer independently; it does not invent one total order across browser, FastAPI, the selected realtime worker, and Reasoner. The authoritative ledger assigns a canonical `ledger_sequence` after ingestion for the durable subset. The vocabulary includes transport/agent readiness, transcript segment, turn committed/resumed, assistant speech started/stopped, task queued/working/needs-input/verified/failed/cancelled/superseded, artifact proposed/accepted/rejected, canvas patch/apply-ack/first-visible/animation-complete/render-failed, usage recorded, and session ending/ended.

One `EventAppender` repository method is the only way to append durable events. It allocates the next session ledger sequence and enforces unique `event_id`, unique `(session_id, ledger_sequence)`, and unique `(session_id, producer_id, producer_sequence)` constraints. A task transition and its corresponding event append occur in the same database transaction. On SQLite, the appender uses one short serialized write transaction; on Postgres, it uses row locking or a database sequence. Audio frames, interim text, and telemetry never wait on this appender.

Create `backend/murmur/reasoning/contracts.py` for `ReasoningRequest`, `ReasoningProgress`, `ReasoningResult`, `ArtifactProposal`, and enumerated task transitions. Use Pydantic models for wire/storage validation and TypeScript discriminated unions at the browser boundary. Unknown event versions or types must fail closed with an observable compatibility error; they must not mutate the canvas.

Reconnect uses state convergence, not hope that ordered delivery covered the gap. The browser reconnect handshake sends `voice_call_id`, last applied durable `ledger_sequence`, each producer's last sequence, and current canvas revision. The worker stops any disconnected-call audio, reloads current task/call state, and either replays retained durable events after the acknowledged ledger sequence or sends an authoritative snapshot containing canonical transcript/history references, active task generations/statuses, and the latest accepted canvas artifact/revision. The client applies the snapshot idempotently and acknowledges its revision before new deltas. Ephemeral interim transcripts and unheard audio are never replayed.

### Persistence and process stages

Use Alembic before adding Voice V2 tables. The baseline migration represents the existing SQLModel schema. An existing database without `alembic_version` may be stamped only after a schema-fingerprint check proves it matches the baseline; otherwise the migration command refuses and prints backup/recovery instructions. Production deploys run `alembic upgrade head` as an explicit step, not concurrently from every web/worker process. At that point, replace the `SQLModel.metadata.create_all()` call in `backend/murmur/persistence/database.py` and the unconditional startup use in `backend/murmur/api/application.py` with a schema-current assertion. `create_all()` may remain only in isolated unit-test fixtures; application and worker startup must never bypass migration history.

Add minimal first-version tables for:

* a voice-call assignment with unique client bootstrap key/call ID, user/session/agent, sticky runtime/profile version, runtime-specific locator payload (LiveKit room/dispatch or Pipecat peer reservation), status, and lifecycle timestamps;
* a durable task ledger with task ID, idempotency key, user/session/turn, task generation, status, request/result/error payloads, canvas revisions, provider/profile provenance, and lifecycle timestamps;
* an append-only session event ledger with event ID, ledger sequence, producer identity/sequence, causation/correlation IDs, type, identifiers, schema version, payload, and timestamp;
* accepted canvas revisions/artifact provenance;
* usage records with provider, model, pricing version, billable unit/quantity, estimated USD, environment, build SHA, runtime plus room/job or peer/pipeline/TURN identifiers, and success/cancel/failure attribution.

Persist committed turns and task/artifact/control transitions in the durable ledger. Interim transcript segments, audio frames, WebRTC samples, and high-frequency telemetry belong in trace/eval storage; writing them synchronously to SQLite would put storage in the hot audio path.

The local single-call prototype can keep SQLite and a Reasoner loop in the selected realtime process. Cap this mode to one concurrent V2 call and do not call it production durable execution. From their first use, Voice V2 repository operations invoked by async realtime code must use bounded thread offloading or async database access. This does not require converting every existing repository. Task rows include `owner_job_id`, `worker_id`, `attempt`, `heartbeat_at`, and `lease_expires_at`. In the SQLite prototype, an expired lease marks work failed/recoverable; it is never silently resumed.

Before any multi-session production canary, switch the same repositories to Postgres and use a durable delivery mechanism with leases, heartbeats, retries, idempotency, and a dead-letter state. The voice job then owns only ephemeral realtime state; the Reasoner execution survives a voice-job reconnect or drain according to explicit policy. Redis is not required by the product contract; select a queue only when that milestone is reached and record the operational reason in this plan.

### Files to preserve, adapt, and eventually remove

Preserve the public entrypoint, Firebase auth and ownership dependencies, agent configuration/prompting, provider-neutral LLM clients, resources, memory, tools, session/message repositories, chat fallback, scene compiler, and canvas renderer.

Adapt:

* `backend/murmur/api/application.py` for runtime selection and explicit Voice V2 control-plane dependencies, never Pipecat peer or LiveKit job ownership;
* `backend/murmur/api/routers/voice.py`, `backend/murmur/api/schemas.py`, and `backend/murmur/api/dependencies.py` for the runtime-neutral authenticated bootstrap and ownership check;
* `backend/murmur/voice/bootstrap.py`, `bootstrap_contracts.py`, and `livekit_control.py` so the existing LiveKit assignment becomes one implementation of a discriminated runtime assignment rather than the universal response;
* `backend/murmur/voice/profile.py` so its manifest, policy, scope, and readiness result are truly runtime-neutral while constructed LiveKit and Pipecat objects move to their arm-specific factories;
* `backend/murmur/agents/` with a shared immutable context builder and separate role-specific pipeline factories for chat, Conductor, and Reasoner;
* `backend/murmur/memory/manager.py` for acknowledgement/progress versus canonical-result semantics;
* `backend/murmur/canvas/state.py` through a new revisioned `backend/murmur/canvas/authority.py`;
* persistence models/repositories and the observability API/UI for task, event, stage latency, WebRTC, and usage truth;
* `web/src/features/voice/session-api.ts`, `session-runtime-controller.tsx`, `session-view.ts`, and `web/src/hooks/use-voice-session.ts` so the server response selects one adapter while state/event reduction stays shared;
* both app pages through that shared hook and reducer only after Milestone 1C.

Already added and preserved from Milestones 0 and the LiveKit local checkpoint:

    backend/murmur/voice/contracts.py
    backend/murmur/voice/bootstrap.py
    backend/murmur/voice/livekit_control.py
    backend/murmur/voice/profile.py
    backend/murmur/voice/worker.py
    backend/murmur/voice/worker_runtime.py
    backend/murmur/reasoning/__init__.py
    backend/murmur/reasoning/contracts.py
    scripts/voice_eval.py
    scripts/voice_e2e_stack.py
    evals/voice/smoke.jsonl
    evals/voice/qualification.jsonl
    evals/voice/gates.json
    tests/fixtures/voice/
    web/src/features/voice/events.ts
    web/src/features/voice/event-reducer.ts
    web/src/features/voice/session-machine.ts
    web/src/features/voice/livekit-transport.ts
    web/src/hooks/use-voice-session.ts
    web/e2e/voice-rtc.spec.ts
    web/playwright.config.ts

Add or complete before Milestone 1C selection:

    backend/murmur/voice/runtime_contracts.py
    backend/murmur/voice/provider_profiles/livekit_cascade.py
    backend/murmur/voice/provider_profiles/pipecat_cascade.py
    backend/murmur/voice/pipecat_runtime.py
    backend/murmur/voice/pipecat_signaling.py
    backend/murmur/voice/pipecat_app.py
    scripts/voice_live_stack.py
    scripts/voice_pipecat_e2e_stack.py
    scripts/voice_runtime_compare.py
    tests/fixtures/voice/coturn/turnserver.conf
    tests/test_voice_runtime_contracts.py
    tests/test_voice_livekit_profile.py
    tests/test_voice_pipecat_profile.py
    tests/test_voice_pipecat_signaling.py
    tests/test_voice_pipecat_runtime.py
    web/src/features/voice/voice-transport.ts
    web/src/features/voice/pipecat-transport.ts
    web/e2e/voice-livekit-live.spec.ts
    web/e2e/voice-pipecat-rtc.spec.ts
    deploy/voice-livekit.Dockerfile
    deploy/voice-pipecat.Dockerfile
    deploy/coturn/turnserver.conf
    deploy/coturn/README.md

Add at Milestone 3, only after one runtime is selected and the product slice passes:

    backend/murmur/voice/conductor.py
    backend/murmur/reasoning/service.py
    backend/murmur/canvas/authority.py
    backend/murmur/persistence/repositories/voice_runtime.py
    backend/murmur/persistence/migrations/
    scripts/migrate.py

During production hardening, delete the rejected arm's deployment image/config rather than shipping dormant operational surface. Rename the selected qualification Dockerfile to `deploy/voice-worker.Dockerfile`, add `deploy/README.md`, and retain `deploy/coturn/` only when Pipecat wins.

Remove only after cutover: the legacy raw-`aiortc` service and models, direct Deepgram socket orchestration, Smart Turn/Kokoro fallback code if no selected profile uses it, base64 TTS/data-channel playback, the legacy WebRTC/audio/VAD hooks, `/offer`, and genuinely unused dependencies such as direct `websockets`, `onnxruntime`, and `@ricky0123/vad-react`. Do not list `aiortc` as removable if the selected Pipecat SmallWebRTC extra still requires it.

### External assumptions to reverify during execution

Use only official documentation for version-sensitive implementation choices. Recheck [LiveKit explicit agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/), [job lifecycle](https://docs.livekit.io/agents/server/job/), [self-hosted agent deployment](https://docs.livekit.io/deploy/custom/deployments/), [observability](https://docs.livekit.io/deploy/observability/data/), [tracing](https://docs.livekit.io/deploy/observability/tracing/), [pricing](https://livekit.com/pricing), and [billing units](https://docs.livekit.io/deploy/admin/billing/) before locking versions or estimating production cost. LiveKit's text-mode agent tests do not exercise a room or audio pipeline, so follow the official [agent testing documentation](https://docs.livekit.io/agents/start/testing/) but retain Murmur's browser and live-audio suites.

For the challenger, recheck the official [Pipecat SmallWebRTC server transport](https://docs.pipecat.ai/api-reference/server/services/transport/small-webrtc), [JavaScript SmallWebRTC client](https://docs.pipecat.ai/api-reference/client/js/transports/small-webrtc), and [transport-selection guidance](https://docs.pipecat.ai/client/concepts/choosing-a-transport), plus Coturn's official [server documentation](https://github.com/coturn/coturn/wiki/turnserver) and [example configuration](https://github.com/coturn/coturn/blob/master/examples/etc/turnserver.conf). As of this plan update, Pipecat documents that its development runner cannot inject custom TURN and that a production custom-TURN deployment needs a manually owned signaling server. Its client guidance also presents SmallWebRTC as a lightweight/self-hosted option with geographic and scale limits. Treat those limits as hypotheses to measure and possible rejection reasons, not as proof that the arm is production-equivalent.

## Plan of Work

### Milestone 0: truthful baseline, contracts, and measurement

First make failure visible and measurements meaningful. Add a provider/runtime preflight that distinguishes configured, reachable, and ready states. The browser must display transport-connected separately from voice-ready. Invalid keys, missing required packages/models, or provider timeouts must prevent Ready and offer an actionable text fallback.

Define the event, task, artifact, lifecycle, and metric contracts before integrating LiveKit. Add the deterministic replay harness and initial synthetic audio/provider-event corpus. Replace the test that treats `is_final` as end of turn with segment accumulation, explicit EOT, resumed-speech, duplicate, missing-EOT, and long-pause cases. Make the corresponding bounded fixes in `backend/murmur/voice/transcription.py` and make `backend/murmur/voice/service.py` publish Ready only after the selected legacy provider path is usable. Use an injected monotonic clock and virtual scheduler; do not use real sleeps in contract tests.

Instrument the current browser and legacy runtime just enough to establish a credentialed baseline with valid provider credentials. Record connection phases, word/turn boundaries, first audible client frame, interruption silence, provider usage, and failure. Do not spend this milestone optimizing the old transport. If the legacy path cannot produce a valid session after the bounded correctness/readiness work, record it as a failed baseline rather than broadening the milestone.

Exit when offline replay is deterministic, a bad credential can never produce Ready, the legacy result is explicitly classified as passing or failed, and the corpus/gates are checked into the repository.

### Milestone 1A: qualify LiveKit Agents plus direct providers plus Cloud

Preserve the completed `cf1ddcd` foundation as evidence, not work to repeat: pinned LiveKit packages, authenticated ownership-checked bootstrap, HMAC room, exact named dispatch, restricted token, signed job metadata, one-active-call lifecycle, production browser adapter, deterministic profile, real local SFU media, interruption, terminal ordering, and cleanup. The local Run J metrics recorded above satisfy the provider-free deterministic media sub-gate. They do not satisfy the direct-provider or Cloud/network sub-gates.

Finish the runtime-specific provider boundary in the in-progress `backend/murmur/voice/provider_profiles/livekit_cascade.py` and `backend/murmur/voice/provider_probe.py`. Compatibility-test and lock the direct provider plugins before calling them pinned. Implement the server-selected `livekit-agents-cascade-v1` factory against the common provider manifest. Preflight must distinguish configuration from a successful bounded provider readiness probe, build only explicit SDK objects rather than managed-inference strings, and close every partial resource on failure. Replace the production `UnavailableVoiceProfileProvider` only when this factory passes its focused invalid-key, timeout, partial-construction, cancellation, and close-once tests; an uncommitted worktree is not an exit result.

Turn the live evaluator into an executable protected browser stack. `scripts/voice_live_stack.py --runtime livekit_v2` starts the built FastAPI and worker processes, points the production frontend at the credentialed LiveKit Cloud project, drives `web/e2e/voice-livekit-live.spec.ts`, collects provider usage and WebRTC stats, and tears down the exact room/dispatch/job. It refuses unless the selected runtime, all required provider/Cloud variables, an explicitly positive budget, an output directory, and a non-production test identity are present. It never prints tokens or keys.

Run two separate Cloud network cases with the same fixture and profile. `--network direct` accepts only a selected host/srflx UDP path; `--network relay-tls` configures the browser to require relay and accepts only a selected relay candidate through the Cloud TURN/TLS path. In both cases evidence must contain the selected local/remote candidate types and protocols, bytes/packets, non-zero decoded remote PCM, one canonical turn, one completed reply, one interrupted reply, provider readiness/usage, and exact cleanup. A configured TURN URL or successful room join is insufficient. The disconnect case deliberately severs the RTC path and must stop audio, end/release the exact call, rotate `voice_call_id`, and expose fresh-call/text choices within the bounded UI deadline; it does not claim replay.

Milestone 1A exits only when the deterministic checkpoint remains green and the credentialed direct, relay/TLS, and bounded-failure cases all pass under one declared provider manifest and budget. If Cloud credentials are unavailable, record the remaining cases as `unmeasured` and keep 1A open.

### Milestone 1B: qualify Pipecat plus SmallWebRTC plus Coturn

Add a separate locked `voice-pipecat` Python extra containing a compatibility-tested pin of `pipecat-ai[webrtc]` and only the direct service integrations used by the manifest. Add pinned `@pipecat-ai/client-js` and `@pipecat-ai/small-webrtc-transport` packages to the web app. Do not replace or wrap the legacy `/offer` implementation, use the Pipecat development runner as a production server, or put a Pipecat pipeline under LiveKit.

Implement `backend/murmur/voice/pipecat_app.py`, `pipecat_signaling.py`, `pipecat_runtime.py`, and `provider_profiles/pipecat_cascade.py`. The control plane returns a single-use opaque signaling URL for the already-owned call; the separate Pipecat ASGI process validates it, creates one `SmallWebRTCConnection` with explicit ICE configuration, creates one runtime-specific direct-provider pipeline, maps canonical Murmur events onto RTVI app messages, and owns cancellation and exact peer/pipeline/provider cleanup. It remains process-local and one-call for this milestone. Ready is impossible before transport input/output, the event path, VAD/turn policy, and every required direct provider are ready.

Implement `web/src/features/voice/pipecat-transport.ts` against the same narrow `VoiceTransport` interface and reuse the existing state machine and event reducer. Microphone media is prepared but semantically gated until canonical Ready just as in the LiveKit arm. The adapter must expose selected ICE candidate statistics, decoded remote PCM, output muting, microphone control, and idempotent close. Any Pipecat/RTVI event that cannot be converted to the existing strict Murmur envelope fails unavailable; it does not create a second product event model.

Create a deterministic stack runner, not a unit-only pipeline test. `scripts/voice_pipecat_e2e_stack.py` owns loopback ports, the separate control-plane and Pipecat ASGI processes, production Next.js, a digest-pinned Coturn container/config, generated test-only TLS material, and Chromium. It runs the same two-turn synthetic fixture and fake STT/LLM/TTS behavior as the LiveKit checkpoint in both `direct` and forced `relay-tls` modes. Relay acceptance requires the selected pair to be relay/TLS and Coturn allocation/byte evidence to match the call. It proves no LiveKit process/package is active in the call topology, canonical events are identical in meaning, the interruption silence gate passes, no stale first reply leaks into the second, and the exact peer, pipeline, provider, TURN allocation, reservation, microphone, and browser audio objects close.

Then run `scripts/voice_live_stack.py --runtime pipecat_smallwebrtc_v1` on a reachable HTTPS Murmur host with valid public TLS, the same direct provider accounts/models/prompt, and production-shaped Coturn. Use the same browser, corpus, region, network shaping, evidence schema, direct and forced-relay cases, disconnect case, and budget policy as 1A. Include Pipecat process/peer IDs, Coturn allocation/traffic, selected candidate pair, worker compute/warm time, and provider usage. Passing local loopback is not live-equivalent proof, and a live direct path does not waive the forced relay path.

Milestone 1B exits only when focused security/lifecycle tests, deterministic direct and forced-relay browser runs, credentialed live-equivalent direct and forced-relay runs, and bounded fresh-call failure all pass. If provider credentials, public HTTPS, DNS/certificates, or reachable Coturn are absent, identify the missing external prerequisite and keep the corresponding result `unmeasured`; do not call the arm production-equivalent.

### Milestone 1C: select exactly one realtime runtime

Normalize both arms with `scripts/voice_runtime_compare.py`. It refuses to compare different commit SHAs, manifest/gate/corpus hashes, browser versions, regions, network profiles, sample definitions, or incomplete hard-gate evidence. It produces a matrix for readiness truth, audible-turn success, turn/entity accuracy, p50/p95 latency, interruption, direct/relay behavior, disconnect cleanup, residual resources, process limits, and total cost per successful audio minute. Architecture preference, already-written code, and a lower isolated vendor fee are not scoring criteria.

Reject any arm that fails ownership isolation, false-ready/silent-success, canonical event validity, non-zero audio, interruption, bounded failure, relay/TLS, or cleanup. Among passing arms, select the lower operational risk and total cost when latency/quality are materially equivalent; select the better latency/quality arm only with the measured tradeoff written here. Explicitly record SmallWebRTC's observed geographic/concurrency limitations and LiveKit Cloud dependence/allowance limits. If evidence is tied, prefer the smaller operational surface only after both are passing; do not keep both active “just in case.”

Write the winning `runtime`, profile, evidence run IDs, reasons, rejected-arm limitations, rollback boundary, and cost basis in the Decision Log and Outcomes. Set the deployment-owned default for new V2 calls to the winner while retaining `legacy` as the pre-cutover rollback. Remove the rejected runtime from frontend selection and new-call routing before Milestone 2; keep its evidence and minimal tests until the final deletion milestone. No agent, memory, tool, canvas, Conductor, Reasoner, or durable reconnect integration begins before this record exists.

### Milestone 2: reconnect the existing Murmur product

Start only from the Milestone 1C winner. Delete the rejected arm from active new-call routing and do not implement the following adapters twice.

Extract one shared immutable session-context builder from duplicated chat/voice pipeline construction. It loads the owned agent, prompt/persona, session history, relevant memory, resources, model policy, and canvas capability without binding to a transport. Build separate mutable pipeline instances for text chat, Conductor, and Reasoner. Never share one `LLMPipeline`: it owns memory, tool callbacks, and canvas state. Disable Reasoner-side direct memory persistence and canvas publication; the coordinator alone commits the verified canonical answer. Text chat must continue using its own role-specific factory.

Connect committed Voice V2 turns to existing persistent sessions and agent configuration. Make session creation idempotent with one authoritative call and one stable client-generated session UUID used as the database primary key; a retry returns the existing owned row and a conflicting owner/agent fails. Remove the competing effect/offer behavior. Replace unauthenticated `sendBeacon` finalization with an authenticated explicit end action plus server-side disconnect/expiry cleanup. Do not rely on tab-close delivery for correctness.

Create a compatibility adapter from typed Voice V2 canvas events to the existing scene/canvas renderer. Define one `CanvasArtifact` discriminated union with at least `operations_v1` for normalized `CanvasOperation[]` and `sdl_scene_v2` for semantic SDL compiled in the browser. At this milestone, preserve visible behavior and prove both typed variants; do not add revision authority or completion acknowledgements yet.

Integrate Voice V2 only into `web/src/app/(app)/session/[agentId]/page.tsx` in this milestone. The generic `web/src/app/(app)/canvas/page.tsx` has neither an agent nor persistent session identity, so leave it on the legacy developer/demo path temporarily. Before cutover, redirect it to an owned/default agent session or remove it; do not create unauthenticated or transient V2 rooms to preserve that route.

Define memory semantics: one committed user turn, zero or more ephemeral acknowledgement/progress delivery events, and one canonical verified assistant result. If a task fails or is cancelled, persist that terminal fact without inventing a completed assistant answer.

Exit when one authenticated voice-plus-canvas scenario uses the existing agent/resource configuration, creates only one persistent session, stores correct canonical history, renders both supported typed artifact variants with parity, and remains usable in text mode if voice is unavailable.

### Milestone 3: add the two-tier brain and authoritative artifacts

Before adding task/event/artifact tables, introduce Alembic, the baseline/adoption schema guard, and explicit migration commands described above. This is intentionally deferred until the cheap transport and product slices pass.

Persist voice-call assignment before any percentage canary. The unique call ID/bootstrap key, selected runtime/profile, and runtime-specific locator (LiveKit room/dispatch or Pipecat peer reservation) make bootstrap transactional and recoverable after FastAPI restarts. Add the single authoritative `EventAppender`; task transitions and their durable events must commit atomically.

Implement the Conductor with a deliberately small policy surface. It can answer safe, short conversational turns; ask clarification; call only `start_task`, `cancel_task`, and `get_task_status`; acknowledge work without claiming completion; and present verified results. It owns exactly one generation/audio floor and cancels it idempotently on interruption.

Implement `ReasoningService` as an asynchronous loop for the single-host prototype. It receives immutable `ReasoningRequest` snapshots, invokes the existing full LLM pipeline, retrieval, search, and canvas-generation tools, and emits typed progress/result/proposal records. It writes every lifecycle transition through the repository. A correction increments task generation and supersedes the older task. Late results from a cancelled or superseded generation are retained for audit but cannot be published.

Implement `CanvasAuthority` around the `CanvasArtifact` union, so revisions apply to the semantic artifact envelope rather than pipeline-local `CanvasState` or compiled animation commands. It validates schema, task state, ownership, idempotency, and `base_revision`; assigns a new revision; publishes the patch; and waits for `canvas_apply_ack`. A lost acknowledgement leaves the result unverified and retryable by event ID. Narration claiming the artifact is visible additionally waits for `canvas_first_visible`; it never waits for the full GSAP timeline unless the claim is specifically about completed animation. A revision conflict rejects the proposal and asks the Reasoner to rebase or the Conductor to clarify; it never blindly overwrites visible state.

Implement the reconnect handshake and replay-or-snapshot convergence contract. Reconnect always stops orphaned audio, preserves or explicitly fails task execution according to its lease, restores the latest accepted canvas artifact/revision, and resumes only with new deltas after the browser acknowledges the snapshot.

Keep conversation available while the Reasoner works. Queue verified-result presentation if the user is speaking. Allow status/cancel/correction turns without blocking on the task. Ensure a synchronous tool call can never freeze audio input.

Exit when a 5-10 second deep task emits at least three observable progress/state transitions, the user can interrupt and converse during it, cancellation prevents all stale speech/canvas updates, a correction supersedes the old generation, and final narration occurs only after the durable result and any required render acknowledgement.

### Milestone 4: optimize media/model profiles inside the selected runtime

The runtime decision is already closed. Run the same audio, prompt, task, region, network profiles, and acceptance gates inside that runtime against at least:

* the measured legacy result;
* the selected runtime's Milestone 1 direct-provider cascade;
* one native speech-to-speech candidate only if the selected runtime can emit and consume Murmur's transcript, task, cancellation, usage, and canvas contracts without weakening auditability.

Do not assume native speech-to-speech is better because its first audio is faster. Score recognition and critical entities, premature endpointing, interruption relevance, task success, canvas correctness, controllability, transcript/audit quality, and total cost. Do not reopen LiveKit versus Pipecat because a provider profile underperforms; first tune or replace the provider within the selected runtime. Reopen Milestone 1 only when new evidence shows that the selected runtime itself violates a hard contract and record that as a new decision, not an informal fallback.

If no profile passes, use the evidence to choose provider replacement, a bounded native-profile rejection, product-scope revision, or an explicit runtime-decision reopening. Do not move the gates to make a preferred provider win.

Exit with a written decision in this plan containing sample size, p50/p95 latency, endpoint and interruption errors, critical-entity and Hinglish results, task/canvas correctness, user preference if tested, cost per successful audio minute, and known limitations.

### Milestone 5: production hardening, cost, canary, and rollback

Add deterministic failure injection for missing dependencies, invalid credentials, provider 401/429/timeouts, empty or partial streams, duplicate/reordered STT segments, missing EOT, resumed speech, worker crash, duplicate delivery, late results, canvas conflicts, lost acknowledgements, reconnect, shutdown, and concurrent-session isolation. Every failure must end in a bounded explicit state; no retry storm or silent success is allowed.

Extend, rather than redefine, the selected arm's two Milestone 1 Playwright environments. Its credential-free deterministic project runs on every PR with the selected real RTC path and fake providers. Its protected live project uses the selected Cloud or HTTPS/Coturn topology plus real providers, is scheduled/manual, and enforces the dollar budget. Add full-product cases for long pauses, correction during background work, artifact acknowledgement, provider-unavailable UI, durable reconnect/snapshot convergence, worker drain, and cleanup. Milestone 1's bounded fresh-call failure remains historical evidence; the reconnect/snapshot test here proves the Milestone 3 contract. Never add a production auth bypass.

Persist stage spans and usage records. Server metrics use one monotonic clock within the process. Browser metrics use `performance.now()` and are reported as browser intervals; do not subtract browser and server clock readings. Record WebRTC RTT, jitter, packet loss, selected candidate type, and reconnect count. Replace undefined aggregate `latency_total_ms` with derived named spans.

For the first one-call internal test, SQLite is allowed. Before any multi-session production canary, migrate the ledger to Postgres and introduce durable task delivery. Test upgrade from the oldest supported schema, backup/restore, lease expiry, duplicate delivery, worker drain, and dead-letter handling.

Promote only the selected qualification image to `deploy/voice-worker.Dockerfile` and add `deploy/README.md`. The non-root image installs only the selected locked runtime extra and direct-provider adapters, includes `murmur.reasoning` plus migration packages/data, and proves its entrypoint inside the built image. When LiveKit wins, use the pinned Agents CLI filesystem entrypoint and prove outbound registration/drain. When Pipecat wins, expose only the authenticated signaling/media ingress through the documented proxy, validate HTTPS and Coturn REST credentials, and prove peer/pipeline/TURN drain. In either case document secrets, resource/concurrency limits, warm capacity, log/trace export, and hosted commands. Update `.env.example`, `web/.env.example`, `.github/workflows/ci.yml`, `docs/ARCHITECTURE.md`, `docs/SETUP.md`, `docs/DEVELOPMENT.md`, `docs/DATABASE.md`, `docs/CHANGELOG.md`, and the root `README.md` as behavior changes.

Canary new sessions only. First shadow the Reasoner without exposing results, then use an internal allowlist, then 5%, 25%, 50%, and 100% gates. Disable Voice V2 for new sessions and drain active calls on rollback. Provider-specific kill switches may disable one STT/TTS/model profile without reverting the whole architecture.

Exit after the selected profile clears all hard gates in live staging, cost records reconcile with provider usage, the canary holds for the required windows, rollback is exercised, and the operator runbook is complete.

### Milestone 6: cutover and deletion

Make Voice V2 the default only after Milestone 5. Retain the legacy path for two stable release windows, then remove `/offer`, legacy raw peer state, direct Deepgram socket code no longer used, data-channel PCM playback, duplicated frontend VAD/audio hooks, obsolete Smart Turn/Kokoro code, unused dependencies, old tests, and stale documentation. Remove `aiortc` only if the selected runtime does not require it; Pipecat SmallWebRTC currently does.

Build a fresh wheel and a clean frontend artifact after deletion to prove no stale generated module remains. Run from a clean checkout with only documented environment variables. Perform one final credentialed browser session from connect through deep task, canvas render, interruption, end, and authoritative persistence read-back.

### Work sequencing and parallel lanes

One integrator owns runtime-neutral contracts, the evidence schema, and milestone acceptance. Before selection, work may proceed in bounded parallel lanes only after `runtime_contracts.py`, the assignment union, provider manifest, `VoiceTransport`, and canonical event version are fixed:

* Milestone 1A lane: LiveKit direct-provider factory, Cloud direct/relay qualification, and exact cleanup;
* Milestone 1B lane: Pipecat signaling/pipeline, SmallWebRTC browser adapter, Coturn direct/relay qualification, and exact cleanup;
* shared evidence lane: unchanged corpus/gates, selected-candidate capture, cost normalization, and the comparison refusal rules.

The product/orchestration lane is deliberately closed until Milestone 1C. Do not parallelize competing edits to the shared session lifecycle, make one arm import the other's SDK, or allow an arm to change common gates unilaterally. After selection, open one product lane, one Conductor/Reasoner/persistence lane, and one evidence/operations lane against the winner only. A milestone is integrated only when whole-repository gates pass.

### Explicitly not in scope for the first passing Voice V2

Do not self-host the LiveKit SFU, combine LiveKit and Pipecat in one call, turn the narrow runtime port into a universal media framework, introduce A2A, expose user-authored tools, add a general agent marketplace, build a universal event bus, add Redis by default, rewrite the canvas renderer, migrate every repository to async, support multiple simultaneous assistant voices, or remove text chat. Operating Pipecat SmallWebRTC and Coturn for the bounded challenger is in scope; building a general-purpose SFU or global RTC network is not. These may become separate plans after evidence shows they are necessary.

## Concrete Steps

The commands below assume the repository root is `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual` and Python 3.11 or 3.12. Steps 1-6 are the preserved historical path through `cf1ddcd`; Run J is their authoritative local-media result. Steps 7 onward are the executable remaining sequence. Do not reinterpret an in-progress worktree change as completion: record the commit SHA in every new evidence bundle.

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

5. Implement the minimal LiveKit worker and production browser adapter.

   Add the standalone worker entrypoint, `worker_runtime.py`, deterministic profile seam, `web/src/hooks/use-voice-session.ts`, event reducer, state machine, error UI, and audio-track handling. Integrate this production adapter in `web/src/app/(app)/session/[agentId]/page.tsx`. Keep the generic canvas page on its legacy developer/demo path until it is redirected or removed. Keep the legacy hook behind runtime assignment until cutover. At this historical step the direct-provider factory was still unfinished; step 8 and commit `c15b6e8` later completed it.

       uv run pytest tests/test_voice_v2_bootstrap.py tests/test_voice_v2_readiness.py -q
       (cd web && npm run lint && npm run typecheck && npm run test && npm run build)

6. Build and run a real offline RTC topology through that adapter.

   Add `@playwright/test`, `web/playwright.config.ts`, an initial `test:e2e:offline` script/project, `scripts/voice_e2e_stack.py`, and a `fake-rtc` profile. The stack runner starts a pinned local LiveKit server on loopback, FastAPI with a test-process auth override and seeded owned agent/session, the fake Murmur worker, and Next.js; waits for each readiness condition; runs the requested Playwright project; captures logs; and tears everything down. The fake worker must consume actual inbound audio frames, commit a deterministic transcript after known fixture audio, publish a deterministic remote audio fixture, and cancel that publication on a second speech fixture. It must not call real providers.

       uv run python scripts/voice_e2e_stack.py

   In CI, install Chromium and run the digest-pinned local-server container through the same stack runner. The test-process auth override must be impossible unless the explicit E2E environment is active. Evidence is written under ignored `var/voice-e2e/` and `var/evals/` and uploaded even when the job fails.

7. Freeze the shared runtime contracts before either remaining arm changes product state.

   Add `runtime_contracts.py`, the bootstrap response union, provider manifest/readiness result, `VoiceTransport`, and strict assignment decoders. Make server selection authoritative and test that one response can instantiate exactly one adapter. Preserve byte-for-byte canonical Murmur event semantics across both encodings. This step contains no provider call.

       uv run pytest tests/test_voice_runtime_contracts.py tests/test_voice_v2_contracts.py tests/test_voice_v2_bootstrap.py -q
       (cd web && npm run test -- --run src/features/voice/session-api.test.ts src/features/voice/session-runtime-controller.test.tsx src/features/voice/event-reducer.test.ts)
       (cd web && npm run typecheck)

   Acceptance: a request has no runtime field; `VOICE_RUNTIME` is the only new-call arm selector; LiveKit-only fields are rejected on Pipecat assignments and vice versa; the legacy/V2 frontend switch cannot pick a V2 arm; secrets are absent; and neither adapter imports the other SDK.

8. Complete Milestone 1A's direct-provider factory and retain the deterministic LiveKit proof.

   `provider_probe.py` and `provider_profiles/livekit_cascade.py`, the compatibility-tested direct plugin pins, production registry, and static evaluator preflight are complete in `c15b6e8`. The executable credentialed browser evaluator remains part of step 9 rather than construction evidence.

       uv sync --locked --extra dev --extra voice-v2
       uv run pytest tests/test_voice_livekit_profile.py tests/test_voice_v2_worker.py tests/test_voice_v2_worker_events.py -q
       uv run python scripts/voice_eval.py preflight --runtime livekit_v2 --profile livekit-agents-cascade-v1 --assert-ready
       uv run python scripts/voice_e2e_stack.py

   Acceptance: wrong/missing credentials, invisible model/voice, timeout, partial construction, cancellation, and duplicate close fail before Ready; explicit direct provider objects reach `AgentSession`; the deterministic Run J scenario remains reproducible; and every opened provider resource closes once. Preflight may make only the documented bounded metadata/readiness calls and must label what those calls do not prove.

9. Run Milestone 1A's credentialed Cloud matrix with a cumulative budget ledger.

       uv run python scripts/voice_live_stack.py run --runtime livekit_v2 --network direct --suite evals/voice/qualification.jsonl --gates evals/voice/gates.json --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json --assert-gates
       uv run python scripts/voice_live_stack.py run --runtime livekit_v2 --network relay-tls --suite evals/voice/qualification.jsonl --gates evals/voice/gates.json --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json --assert-gates
       uv run python scripts/voice_live_stack.py disconnect --runtime livekit_v2 --network relay-tls --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json

   Each command refuses unset/non-positive budget, missing protected credentials, a dirty or unidentified build, or a production account. Acceptance is selected direct UDP in the first run, selected relay/TLS in the second, non-zero browser audio and real providers in both, interruption at or below the hard gate, explicit fresh-call/text behavior after disconnect, exact room/dispatch/job/provider cleanup, and a cost record. This is bounded failure, not durable reconnect.

10. Implement and deterministically qualify Milestone 1B.

    Add the locked `voice-pipecat` extra, pinned Pipecat JS packages, Pipecat ASGI/signaling/runtime/profile files, browser adapter, Coturn config, and deterministic runner/tests named above. The runner generates ephemeral test TLS assets outside source control and owns every process/container/port.

        uv sync --locked --extra dev --extra voice-pipecat
        (cd web && npm ci && npx playwright install chromium)
        uv run pytest tests/test_voice_runtime_contracts.py tests/test_voice_pipecat_profile.py tests/test_voice_pipecat_signaling.py tests/test_voice_pipecat_runtime.py -q
        (cd web && npm run test -- --run src/features/voice/pipecat-transport.test.ts src/features/voice/session-runtime-controller.test.tsx)
        uv run python scripts/voice_pipecat_e2e_stack.py --network direct --assert-gates
        uv run python scripts/voice_pipecat_e2e_stack.py --network relay-tls --assert-gates

    Acceptance: the production adapter exchanges real Opus media with the Pipecat pipeline; fake components produce the same canonical two-turn/interruption semantics; forced relay proves a relay/TLS selected pair plus matching Coturn allocation/bytes; direct proves the declared non-relay path; no LiveKit process participates; and peer, pipeline, provider, reservation, TURN allocation, media tracks, and browser elements are absent after cleanup.

11. Run Milestone 1B's credentialed live-equivalent matrix under the same campaign cap.

        uv run python scripts/voice_live_stack.py run --runtime pipecat_smallwebrtc_v1 --network direct --suite evals/voice/qualification.jsonl --gates evals/voice/gates.json --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json --assert-gates
        uv run python scripts/voice_live_stack.py run --runtime pipecat_smallwebrtc_v1 --network relay-tls --suite evals/voice/qualification.jsonl --gates evals/voice/gates.json --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json --assert-gates
        uv run python scripts/voice_live_stack.py disconnect --runtime pipecat_smallwebrtc_v1 --network relay-tls --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --campaign-cap-usd 25 --budget-ledger var/evals/m1-runtime-budget.json

    Run on the declared reachable HTTPS host with public TLS and production-shaped Coturn. Acceptance mirrors Step 9 and additionally reconciles Coturn allocation/traffic, host/pipeline compute and warm time, and peer/pipeline cleanup. Missing DNS, certificate, host, Coturn, or provider credentials yields `unmeasured`; it is not a passing local substitute.

12. Select the runtime, or stop.

        uv run python scripts/voice_runtime_compare.py --livekit-evidence "$M1_LIVEKIT_EVIDENCE_DIR" --pipecat-evidence "$M1_PIPECAT_EVIDENCE_DIR" --gates evals/voice/gates.json --require-comparable --output var/evals/m1-runtime-decision.json

    Verify the comparison refusal cases in `tests/test_voice_runtime_compare.py`. Copy the resulting run IDs and decision fields into this plan's Decision Log and Outcomes, set the V2 server default to exactly one winner, remove the rejected arm from new-call/frontend routing, and run both ownership tests plus the winner's deterministic test. Do not continue when either bundle is incomplete or neither arm passes. Any increase above the cumulative USD 25 campaign cap requires explicit user approval.

13. Reconnect only the selected runtime to existing agent/session/memory/canvas behavior.

    Extract the immutable context builder and separate role-specific pipeline factories. Make persistent session creation idempotent, replace the unauthenticated beacon end path, add the typed `CanvasArtifact` compatibility union, and prove canonical-answer persistence. No rejected-runtime product adapter is allowed.

        uv run pytest tests/test_authenticated_session_continuity.py tests/test_memory_context.py tests/test_persistence_repositories.py tests/test_voice_v2_integration.py -q
        (cd web && npm run test -- --reporter=dot)

14. Add migrations, durable call/task/event state, the two-tier brain, and real reconnect convergence.

    Add Alembic and schema guards first, then the runtime-neutral assignment locator, task/lease/event/artifact/usage repositories, Conductor, Reasoner, and CanvasAuthority. Only here implement durable replay, authoritative snapshot, canvas acknowledgement, post-snapshot deltas, and task behavior across reconnect.

        uv run python scripts/migrate.py check
        uv run alembic upgrade head
        uv run pytest tests/test_migrations.py tests/test_voice_v2_conductor.py tests/test_voice_v2_task_lifecycle.py tests/test_voice_v2_canvas_authority.py tests/test_voice_v2_cancellation.py tests/test_voice_v2_session_isolation.py tests/test_voice_v2_integration.py -q

    Acceptance includes a selected-runtime browser fault test that reconnects, stops orphaned audio, applies one complete replay or snapshot before new deltas, preserves or explicitly fails leased deep work, and never repeats unheard/interim data. This Step 14 proof supersedes the bounded fresh-call behavior only for the integrated runtime.

15. Optimize provider profiles inside the selected runtime.

        uv run python scripts/voice_eval.py live --runtime "$M1_SELECTED_RUNTIME" --suite evals/voice/qualification.jsonl --profile "$M1_CASCADE_PROFILE" --max-cost-usd "$MURMUR_EVAL_BUDGET_USD" --gates evals/voice/gates.json --assert-gates

    Repeat for one native-realtime profile only after contract preflight. Keep this campaign separately budgeted and record sample size, p50/p95, endpoint/interruption errors, critical entities, Hinglish, task/canvas correctness, auditability, and total cost. This chooses a provider profile inside the winner; it is not Steps 9-12 repeated and does not silently reopen runtime selection.

16. Move selected production state and package the selected worker before canary.

    Migrate the ledger to Postgres, introduce durable delivery, promote only the winning qualification image/config, and prove leases, redelivery, dead letter, drain, and task behavior across runtime reconnect.

        uv build
        python -m zipfile -l dist/*.whl
        docker build -f deploy/voice-worker.Dockerfile -t murmur-voice-worker:local .

17. Run full local gates after every integrated milestone.

        uv run ruff check .
        uv run ruff format --check .
        uv run pytest -m "not live_provider"
        uv run python -c "from main import app; print(app.title)"
        (cd web && npm run lint && npm run typecheck && npm run test && npm run build)

    Also run the selected runtime's deterministic stack command from Step 8 or 10. Live-provider, load, and impaired-network suites remain separately triggered and budgeted. A green offline suite must never be reported as real-provider, geographic, TURN, or production-cost proof.

18. Canary, roll back deliberately, then remove legacy and rejected-runtime code.

    Record internal, 5%, 25%, 50%, and 100% results in this plan. Exercise rollback before full rollout. After two stable release windows, remove legacy and rejected-arm files/dependencies and verify from a clean checkout, fresh wheel, selected deterministic stack, and selected credentialed browser journey.

        uv build
        python -m zipfile -l dist/*.whl
        uv run pytest
        (cd web && npm ci && npm run check)

## Validation and Acceptance

Validation has three ordered layers. Runtime-neutral unit/replay tests prove contracts, isolation, and deterministic orchestration. Each arm's credential-free browser topology proves its real RTC/media/lifecycle path. Each arm's credentialed live-equivalent browser matrix proves direct providers, cross-network direct/relay behavior, and measurable cost. No layer substitutes for another, and product/two-tier validation begins only after the two arm bundles are compared and one runtime is selected.

### Required functional invariants

* The UI never reports voice-ready until the transport, worker, required provider/model path, and event channel are usable.
* Invalid credentials and missing required dependencies are discovered before Ready and produce an actionable unavailable state with text fallback.
* The bootstrap request cannot select a runtime. Repeating it with the same owned session/call ID returns the same active runtime assignment and server-selected profile: exact room/dispatch for LiveKit or exact unconsumed/active peer reservation for Pipecat. A conflicting identity or runtime fails closed.
* One call constructs exactly one runtime adapter. No LiveKit object enters a Pipecat call, no Pipecat object enters a LiveKit call, and product code sees neither SDK.
* Final transcript segments accumulate; only committed EOT dispatches a turn. Resumed speech cancels speculation before side effects.
* A turn commits at most once despite retries, reconnects, and duplicate events.
* Exactly one Conductor owns assistant speech. Interruption and cancellation are idempotent, and audio already heard is never replayed after a TTS retry.
* Task transitions follow the declared lifecycle. Cancelled and superseded generations cannot speak or mutate visible state.
* Canvas application requires the expected base revision, is idempotent by event ID, rejects stale patches, records `canvas_apply_ack` after scene insertion/scheduling, and records `canvas_first_visible` after the first meaningful rendered frame. Full animation completion is a separate optional event.
* Reconnect either replays the complete durable gap or applies one authoritative snapshot before new deltas; orphaned audio and stale interim transcripts never replay.
* No completion claim is spoken or stored before authoritative task verification. A claim that an artifact is visible additionally requires `canvas_first_visible`; it does not wait for the entire teaching animation.
* Session finalization leaves no active provider stream, worker/pipeline task, room/dispatch or peer/reservation reference, TURN allocation, media track, or process-local registry state.
* No event, audio, task, memory, or canvas state crosses users or sessions.
* Application and worker startup refuse a stale schema; production code never calls `create_all()` or mutates schema outside the explicit migration step.

### Initial qualification gates

These are hypotheses to calibrate with the bounded baseline, not vendor promises. Report p50, p95, sample count, network profile, region, provider/model versions, and confidence intervals where useful.

Functional hard gates:

* zero false-ready sessions;
* zero silent successful turns in 100 qualification turns;
* one real selected ICE path per required network case: declared direct UDP for `direct` and relay/TLS plus matching relay evidence for `relay-tls`;
* non-zero decoded browser PCM, canonical speech terminal state, and exact runtime/provider cleanup in every Milestone 1 smoke run;
* bounded disconnect always stops audio and offers an authenticated fresh call or text fallback; durable replay/snapshot is not a Milestone 1 requirement;
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
* zero residual tasks, providers, workers/pipelines, room/dispatches, peer/reservations, TURN allocations, media tracks, or session references after cleanup;
* both Milestone 1 arms explicitly exercise their direct and forced TURN/TLS paths plus bounded fresh-call failure before selection; 150 ms RTT plus 2% packet loss, durable reconnect/snapshot, and worker drain are explicitly exercised for the selected integrated runtime before production canary.

Task-quality gates:

* the two-tier profile is non-inferior to the best single-tier profile within two percentage points on task completion;
* task context/handoff accuracy is at least 98%; conflicting progress/completion claims and duplicate tool side effects are zero;
* cancellation is observed by the Reasoner within 500 ms when it is not blocked inside an uncancellable external call;
* paired user testing is directional only at small sample sizes, but promotion should require at least 70% preference with no objective regression.

Cost gates:

* no live evaluation runs without a positive explicit per-run budget; Steps 9 and 11 share one append-only budget ledger and one cumulative initial runtime-selection campaign cap of USD 25;
* define `MAX_COST_PER_SUCCESSFUL_AUDIO_MINUTE_USD` from the intended price/usage model before canary;
* include direct-provider STT connection time, LLM tokens/cache, TTS characters/audio, worker/pipeline compute and warm idle, downstream traffic, observability, retries, failures, and cancelled speculation for both arms;
* for LiveKit, also include Cloud RTC/TURN usage and allowances; for Pipecat, include signaling/host compute, Coturn compute, relay bandwidth/egress, certificates and load-balancer/proxy cost. No-managed-RTC fee and open-source licensing are not a zero-cost claim;
* deterministic fake/local runs report production cost as `unmeasured`, never zero;
* during profile selection, reject a profile more than 10% costlier than an equally good passing profile;
* during canary, roll back or pause expansion if cost per successful minute rises more than 20% after at least 100 completed sessions;
* alert at 70%, 85%, and 95% of any LiveKit, hosting, TURN, or provider allowance; never auto-upgrade a plan.

### Failure-injection acceptance

Before selection, both deterministic arm suites must inject readiness failure, invalid/reused/expired assignment, transport timeout/disconnect/duplicate events, selected-path mismatch, STT 401/429/disconnect/reordering/missing EOT/resumed speech, slow or partial LLM output, TTS 401/429/zero-byte/partial stream, worker/pipeline crash, concurrent users, and shutdown during work. The Pipecat suite additionally injects signaling reservation races and Coturn failure; the LiveKit suite retains dispatch/job failure cases. After selection, add durable reconnect/replay, queue duplicate delivery/late result, canvas revision conflict/lost acknowledgement/render exception, and Reasoner shutdown cases.

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
    webrtc-stats.json
    lifecycle.json
    topology.json

Every report names the commit SHA and dirty-state refusal, runtime/profile/manifest version, provider/model versions, region, browser, network profile, session/turn counts, corpus and gate hashes, whether providers were fake or real, selected candidate types/protocols, runtime-specific resource IDs, and cleanup result. `cost.json` states the price date/source, shared and runtime-specific categories, billable quantities, failed/cancelled attribution, and whether production cost is measured or unmeasured. Only synthetic or explicitly consented audio may be checked into the repository.

### Canary and rollback acceptance

Begin with a shadow Reasoner, then require at least 30 completed internal sessions and 300 turns over two days. The 5% stage requires at least 500 turns over 48 hours; the 25% stage requires at least 2,000 turns over 72 hours. Advance only while hard gates remain green. Add a 50% stage before full rollout.

Immediately stop new Voice V2 assignment for an ownership/security violation, cross-session leak, stale canvas corruption, false verified-completion claim, or persistence corruption. Automatically roll back new sessions after two consecutive monitoring windows with voice-ready failure above 2%, silent-turn or fatal-turn rate above 1%, p95 speech-end-to-playback above two seconds, metrics completeness below 99%, or the cost regression gate above. Active sessions drain on their existing sticky profile.

The plan is complete only when both Milestone 1 runtime arms have comparable deterministic and live-equivalent evidence, Milestone 1C selects exactly one passing runtime with measured total cost, product and two-tier work exists only against that winner, the task/canvas invariants hold, rollback is exercised, the selected runtime is the default, legacy and rejected-runtime code are removed after the declared stable windows, documentation matches the shipped commands, and a fresh-checkout credentialed browser run proves the selected complete user journey. If an arm remains unmeasured because credentials or reachable infrastructure are unavailable, Milestone 1C and this plan remain open rather than silently treating the other arm as a comparative winner.

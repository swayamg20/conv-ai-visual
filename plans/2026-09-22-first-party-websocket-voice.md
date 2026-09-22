# Replace LiveKit with first-party WebSocket voice

## Purpose / Big Picture

Murmur's deployed Voice button must establish a usable, interruptible conversation without requiring a LiveKit account or creating a separate LiveKit bill. A signed-in user will open one authenticated WebSocket to the existing Azure FastAPI application. The browser will send bounded PCM microphone frames; the server will stream them through Deepgram speech-to-text, Murmur's existing Groq text/tool pipeline, and ElevenLabs text-to-speech; the browser will play returned PCM and apply the same authoritative transcript and canvas events it already understands. LiveKit must not be present in the deployed image, Azure template, browser bundle, or runtime configuration after the migration is accepted.

The first milestone is intentionally provider-free. Azure Container Apps documents WebSocket support and also documents a 240-second ingress request timeout without clearly saying whether upgraded WebSockets are exempt. Before provider work is wired or paid calls are made, a source-pinned canary will hold the exact authenticated production WebSocket open for more than five minutes and prove bidirectional heartbeats, one-use authentication, bounded queues, and cleanup. If that canary cannot survive, this plan stops before committing to this transport and records the result rather than hiding it with reconnects.

## Progress

- [x] 2026-09-22 02:39 IST: Confirmed that the already merged LiveKit deployment work has not been deployed and that Azure is still serving the prior single-API revision.
- [x] 2026-09-22 02:39 IST: Recorded the product constraint that the MVP must not depend on LiveKit Cloud or a separately billed managed media service.
- [x] 2026-09-22 02:39 IST: Selected a first-party browser-to-FastAPI WebSocket as the primary candidate and isolated work on `codex/azure-voice-websocket` from current `origin/main`.
- [x] 2026-09-22 04:05 IST: Implemented the reusable authenticated one-use WebSocket ticket, exact-origin/subprotocol admission, generation-fenced PCM framing, provider-free echo/heartbeat canary, explicit close behavior, and lifecycle cleanup. Independent review exposed and drove fixes for cancel/connect ordering, pre-decode WebSocket buffering, echo sample-rate truth, bounded outbound writes, child-task cleanup, and closed-service behavior. The resulting focused/deployment/Voice V2 regression selection passes 173 tests without provider traffic.
- [x] 2026-09-22 12:27 IST: Added the source-pinned provider-free canary client. A formal remote run now requires at least 310 seconds, a clean local HEAD equal to the expected SHA and current origin branch tip, an exact deployed health/readiness SHA, one socket with zero reconnects, explicit `provider_free_echo` server attestation, active plus idempotent release, and secret-safe non-overwriting evidence. Independent security review passed and the focused foundation/canary selection passes 27 tests.
- [x] 2026-09-22 13:35 IST: Removed the dormant LiveKit worker from the Azure application topology and migrated the guarded deployer to an API-only `websocket_v1` contract. Active deployment now stages only Azure OpenAI and Firebase secrets; after the new revision is healthy and every older backend revision is inactive, it revokes old voice-secret access and disables those secret versions recoverably. Both Bicep templates compile and the combined deployment contract suite passes 95 tests.
- [ ] Prove the canary locally and for more than five minutes through Azure Container Apps ingress, with zero provider calls.
- [ ] Implement browser PCM capture/playback, generation fencing, backpressure, and deterministic interruption.
- [ ] Bridge the server session to Deepgram, Murmur's Groq/tool/canvas pipeline, and ElevenLabs while preserving one authoritative call owner.
- [ ] Remove the remaining dormant LiveKit packages and browser imports after the replacement adapter passes equivalent acceptance. Infrastructure, active secret configuration, and the worker sidecar are already removed from the candidate deployment.
- [ ] Deploy the exact pushed replacement commit and run one explicitly budgeted authenticated live call.

## Surprises & Discoveries

- The current merged `main` contains a complete but dormant LiveKit Cloud deployment path. It is not active in Azure. The candidate branch now removes its worker, configuration, Key Vault access, and secret versions from the deployment topology without deleting the recoverable Key Vault records.
- Azure Container Apps' official ingress documentation explicitly supports WebSockets but lists a 240-second request timeout. The documentation does not clearly establish whether the limit applies after an HTTP upgrade, so a real five-minute canary is a release prerequisite.
- Browser WebSocket constructors cannot attach the Firebase `Authorization` header used by Murmur's HTTP API. Passing the long-lived Firebase token in a query string would expose it to common URL logging paths, so the browser needs an authenticated HTTP bootstrap that returns a short-lived, one-use connection ticket.
- Murmur already has strict, transport-neutral Voice V2 event decoding and a runtime-specific adapter seam in `web/src/features/voice/voice-transport.ts`. The replacement should add one adapter rather than fork the product session state machine.
- The existing Pipecat runtime is already parameterized by a generic transport and already composes the exact Deepgram, Groq, ElevenLabs, RTVI event, interruption, and cleanup stages. Pipecat now ships a matching FastAPI WebSocket transport and browser package, so Murmur should reuse that conductor instead of implementing a second provider pipeline.
- Pipecat's own transport-selection guide still warns that TCP WebSockets are weaker than WebRTC for browser voice because of head-of-line blocking, jitter, and reconnection behavior. Its WebSocket package describes itself as a lightweight development/testing transport. This is acceptable only as a measured MVP hypothesis, not as an assumed production-grade replacement.
- Raw browser audio commonly arrives from an `AudioContext` at 48 kHz even when a lower rate is requested. Input and output resampling therefore have to be explicit and tested; requested sample rate is not an adequate contract.
- An HTTP release can race the WebSocket upgrade after its one-use ticket is consumed. Treating an active call as a release conflict loses the user's cancel, so release intent must be recorded under the same registry lock and signal the exact active connection; both operation orders now have regression tests.
- Application-level frame validation runs only after Uvicorn buffers a complete WebSocket message. The production server therefore also needs a small pre-decode message limit and bounded protocol buffering; an application frame cap alone is not a memory bound.
- Uvicorn's SansIO WebSocket implementation honors the message-size limit but self-pauses its input rather than using the legacy `ws-max-queue` setting. Pin the SansIO implementation and assert the real 8 KiB pre-decode bound instead of claiming an ignored queue flag is protection.
- ASGI sends and closes can stall on a non-reading peer. Every outbound operation now has a two-second deadline, ordinary writes race exact-call release, and every locally owned send/receive/release task is cancelled and joined on exit.
- A canary can be technically green yet operationally dishonest. The first runner draft allowed short remote runs, trusted a caller-provided SHA, overwrote evidence, and claimed provider usage it could not observe. The accepted runner refuses those states and records provider usage as unverified while requiring a source-pinned server to attest its provider-free echo mode.
- Retiring configuration is itself a cutover operation. The deployer must temporarily tolerate only the known retired voice-secret grants while it proves the new API-only revision, then prove every older revision inactive before revoking those grants and disabling old versions. Disabling rather than deleting keeps rollback material recoverable without leaving it usable.

## Decision Log

- 2026-09-22, Swayam Gupta: Do not use LiveKit because the separate service cost is not acceptable for the MVP.
- 2026-09-22, Codex: Use the existing Azure FastAPI application as the sole realtime transport owner. This avoids a new media vendor while retaining the current direct Deepgram, Groq, and ElevenLabs provider accounts.
- 2026-09-22, Codex: Authenticate WebSocket upgrades with a 15-second, one-use ticket minted by an ordinary Firebase-authenticated POST. Send the ticket as a non-echoed WebSocket subprotocol value, validate the exact browser origin, bind it to user/session/agent/call, and consume it atomically before accepting the socket. Never place a Firebase token or provider credential in the WebSocket URL or browser bundle.
- 2026-09-22, Codex: Treat reconnect as a fresh call during the MVP. A dropped socket cancels the current provider work and requires a new authenticated ticket; it must not silently replay or continue a partially spoken turn.
- 2026-09-22, Codex: Fence audio, model output, tool effects, and canvas events with a monotonically increasing generation. A local browser interruption flushes playback immediately, tells the server to cancel the old generation, and causes both sides to reject late frames or effects from that generation.
- 2026-09-22, Codex: Use signed 16-bit mono PCM at 16 kHz for browser input and signed 16-bit mono PCM at 24 kHz for ElevenLabs output. Prefer correctness and exact interruption boundaries over compressed-media complexity for the one-call pilot.
- 2026-09-22, Codex: Fail visibly under sustained backpressure. Browser and server queues remain bounded to approximately 500 milliseconds; neither side may accumulate seconds of stale conversational audio.
- 2026-09-22, Codex: Do not claim Azure suitability until the exact authenticated canary stays bidirectional for at least 310 seconds. No automatic reconnect may disguise an ingress timeout during this proof.
- 2026-09-22, Codex: Persist a bounded, expiring release intent for every exact call scope. Release removes a pending ticket or signals the exact active socket, remains idempotent, and prevents that call ID from being resurrected by the opposite side of a race.
- 2026-09-22, Codex: Keep short-lived ticket capacity small, but give the unified pending/active/release-intent registry a separate 10,000-call bound. If cancellation state ever saturates, block new calls until the intent horizon expires instead of evicting an authoritative cancel.
- 2026-09-22, Codex: Pin Uvicorn's `websockets-sansio` implementation and bound messages to 8 KiB before enabling the route. Bound each application send to two seconds and race non-terminal output against release. The raw echo advertises 16 kHz output because it returns the 16 kHz input unchanged; 24 kHz becomes authoritative only when ElevenLabs output is attached.
- 2026-09-22, Codex: Reuse the existing transport-neutral Pipecat runtime with `FastAPIWebsocketTransport` and its Protobuf frame serializer after the raw-ingress canary passes. This keeps Murmur's current Deepgram/Groq/ElevenLabs cascade and canonical RTVI event projection rather than rebuilding provider orchestration.
- 2026-09-22, Codex: Treat the WebSocket approach as a cost-first MVP experiment. The final acceptance includes a ten-minute idle/heartbeat soak, a ten-minute active-audio soak, shaped latency/loss, real browser echo behavior, and interruption measurements. Failure sends the project back to a self-hosted WebRTC hosting decision; it does not justify silently adding a managed media vendor.
- 2026-09-22, Codex: Cut over the existing `murmur-api` app instead of adding a second Container App. The provider-free candidate remains one warm API replica, removes the LiveKit worker and runtime credentials, and therefore adds no managed-media topology or LiveKit bill. Secret versions are disabled only after revision retirement so rollback remains deliberate and recoverable.

## Outcomes & Retrospective

Work is in progress. The expected outcome is a one-call Azure MVP with no LiveKit dependency and the same direct model cascade. The primary unresolved infrastructure question is whether Azure Container Apps preserves an upgraded WebSocket beyond its documented 240-second request timeout. The provider-free canary is designed to answer that before the migration incurs provider usage or broad frontend work.

## Context and Orientation

`backend/murmur/api/application.py` composes the FastAPI application and owns process-lifetime services. `backend/murmur/api/routers/voice.py` currently exposes Voice V2 bootstrap and release. `backend/murmur/api/authentication.py` verifies Firebase bearer tokens for normal HTTP requests. The replacement adds a small WebSocket ticket owner to application state, an authenticated bootstrap/release route, and a WebSocket upgrade route. The ticket owner is process-local for the one-replica pilot; increasing Azure replicas requires moving ticket consumption and call ownership to a shared store.

`web/src/features/voice/session-api.ts` strictly decodes the server-selected runtime assignment. `web/src/features/voice/voice-transport.ts` dynamically loads exactly one runtime-specific adapter while preserving the shared voice event reducer and session state machine. A new `websocket_v1` assignment and adapter will live beside the existing adapters during qualification. `web/src/hooks/use-voice-session.ts` remains the product-level owner for bootstrap, explicit audio activation, canonical readiness, teardown, and visible failure.

The server-side provider path must reuse transport-neutral Murmur behavior rather than LiveKit plugin objects. `backend/murmur/voice/pipecat_runtime.py` already builds the complete pipeline from a generic Pipecat `BaseTransport`, while `backend/murmur/voice/provider_profiles/pipecat_cascade.py` already fixes Deepgram Nova-3, Groq `openai/gpt-oss-120b`, and ElevenLabs Flash v2.5. The implementation will add a FastAPI WebSocket transport factory and retain the existing Pipecat event channel, interruption processors, authorization, provider preflight, and cleanup. It will not introduce another session conductor.

`infra/azure/apps.bicep` and `scripts/deploy_azure.py` now describe the provider-free candidate: one API container, `VOICE_RUNTIME=websocket_v1`, exact frontend CORS, two active Key Vault references, and no voice worker. `deploy/backend.Dockerfile`, `requirements.txt`, `pyproject.toml`, the frontend package graph, and browser runtime selection still contain dormant LiveKit code until the replacement browser adapter passes acceptance. Those dormant packages do not create a LiveKit service bill, but they remain explicit cleanup work and must not be described as already removed.

## Plan of Work

First, implement a provider-free foundation. Add an application-owned ticket registry with bounded capacity, monotonic expiry, exact scope matching, atomic one-use consumption, and idempotent release. Add an authenticated bootstrap response for `websocket_v1` and a WebSocket route that validates origin and subprotocol before acceptance. The canary protocol will exchange versioned JSON control frames and bounded binary frames, report sequence/generation errors explicitly, emit periodic heartbeats, and close all tasks and tickets on disconnect. Unit and ASGI integration tests will cover token replay, expiry, wrong origin, wrong scope, malformed subprotocols, oversized frames, excess rate, stale generation, queue overflow, and shutdown.

Second, add a source-pinned canary client and a minimal browser adapter that can establish the authenticated socket without opening provider connections. Local tests will run against the real FastAPI application. The exact committed backend image will then be deployed through the guarded Azure driver with provider dispatch disabled, and the client will hold one connection for at least 310 seconds while checking ordered bidirectional heartbeats. A disconnect near 240 seconds rejects the current architecture and triggers a documented alternative evaluation; it is not papered over with reconnect logic.

Third, implement the browser audio boundary. An `AudioWorklet` will capture microphone samples off the main thread, resample deterministically to 16 kHz PCM16, frame them in 20-millisecond packets, and send only after canonical ready plus the user's explicit activation gesture. A separate worklet/ring buffer will resample and play 24 kHz PCM16 output. The adapter monitors `WebSocket.bufferedAmount`, enforces a bounded queue, flushes output immediately on local voice activity, and rejects frames whose generation is no longer current.

Fourth, attach the accepted socket to the existing Pipecat conductor. `FastAPIWebsocketTransport` with the Protobuf serializer becomes the injected transport in `build_pipecat_runtime`; the existing Deepgram, Groq/tool, ElevenLabs, canonical event, interruption, and cleanup components remain authoritative. Murmur-specific admission still owns user/session/agent/call scope and exact-once release around the Pipecat task. No provider retry is hidden inside the acceptance path.

Finally, qualify the path and remove the dormant LiveKit deployment surface. Provider-free tests, frontend tests, container checks, Azure canary evidence, and one explicitly authorized live call must pass first. Then remove LiveKit packages, secrets, Bicep worker container, deployer inputs, and production build selection while keeping historical plans and test evidence intact. Deploy only a pushed, clean, immutable SHA and verify the user-visible Voice flow plus correlated, secret-safe Azure logs.

## Concrete Steps

Run commands from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-azure-voice-websocket` unless a command changes directories.

Implement and validate the provider-free backend foundation:

    .venv/bin/python -m pytest tests/test_websocket_voice_ticket.py tests/test_websocket_voice_api.py -q
    .venv/bin/ruff check backend/murmur/voice/websocket_ticket.py backend/murmur/api/routers/websocket_voice.py tests/test_websocket_voice_ticket.py tests/test_websocket_voice_api.py
    python3 -m py_compile main.py backend/murmur/voice/*.py backend/murmur/api/routers/*.py

Implement and validate the strict browser assignment and transport:

    cd web
    npm test -- --run src/features/voice/session-api.test.ts src/features/voice/voice-transport.test.ts src/features/voice/websocket-transport.test.ts
    npx tsc --noEmit

Run the local provider-free WebSocket proof against the real ASGI application, then build the exact backend container and repeat against the container. The proof must report one connection, at least 310 seconds of elapsed time in the long form, ordered heartbeats, zero reconnects, zero provider calls, and clean server teardown. A short-duration mode is used in regular CI; the 310-second form is a release gate.

Before an Azure canary, stage only substantive implementation plus this living plan, inspect the staged diff, commit a coherent milestone, and push it. Deploy only that pushed SHA through `scripts/deploy_azure.py`. Keep provider dispatch disabled and do not add or rotate LiveKit credentials. Capture the exact Azure revision, image digest, start/end timestamps, close code, heartbeat count, and logs proving cleanup without persisting tickets or user audio.

After provider integration, run the focused voice suites, full backend suite, frontend TypeScript/tests/build, Bicep compilation, guarded deployer tests, and image smoke checks. Run a paid live call only after the user supplies a fresh explicit budget and call-count authorization.

## Validation and Acceptance

The transport gate passes only if a Firebase-authenticated session receives a `websocket_v1` assignment containing no long-lived credential, the ticket cannot be replayed or used from another origin/scope, and the exact Azure WebSocket remains bidirectional for at least 310 seconds with no reconnect. Malformed or abusive input must close with a stable, non-reflective error; queues and tasks must return to zero after every failure and disconnect.

The media gate passes only if real browser microphone samples reach Deepgram, a complete turn reaches the existing Groq/tool/canvas pipeline, ElevenLabs PCM is audible, transcripts and visuals remain ordered, and speaking over the assistant clears old audio and prevents stale model, tool, audio, or canvas effects. Readiness must mean microphone input, provider cascade, output playback, and event channel are all usable; otherwise the UI must terminate within a fixed deadline with an actionable error.

The deployment gate passes only when the exact pushed SHA is running with one warm API replica, no LiveKit worker, no LiveKit secrets, no LiveKit browser import, and no LiveKit runtime package in the production image. Azure logs must correlate bootstrap, socket accept, turn generations, provider stages, interruption, and cleanup without logging Firebase tokens, one-use tickets, provider keys, raw audio, or full user transcripts. A single accepted live call is an MVP proof, not durability or horizontal-scale proof; moving beyond one replica requires shared ticket/session state and a new scale acceptance run.

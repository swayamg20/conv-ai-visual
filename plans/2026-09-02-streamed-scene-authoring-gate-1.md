# Stream model-authored scene patches into the live board

## Purpose / Big Picture

Gate 0 proved that Murmur can evolve a semantic SVG board smoothly, retain visible ink during interruption, reject scheduled visual work from an abandoned branch, and replay accepted scene revisions. It did not prove that a language model can author those revisions progressively. The Gate 0 lesson is still hand-written TypeScript.

This plan delivers Gate 1: a user types a visual teaching prompt, a real model-compatible service emits several bounded scene patches as text arrives, and the browser validates and draws each accepted patch before the complete model response exists. A learner can interrupt the stream, retain the materialized board, and start a new generation from that exact revision. Invalid, oversized, stale, duplicate, or out-of-order output must leave the last accepted board unchanged. Replay must use accepted semantic revisions and make no model request.

A **scene patch** is one atomic change to the current scene document. Version 1 has only `put`, which creates or fully replaces one node by stable semantic ID, and `remove`, which deletes an existing ID. A **generation** is one user intent; interruption or correction starts a new generation. An **attempt** is the initial model stream or the one allowed repair stream for the same generation. The service, not the model, assigns generation, attempt, sequence, and scene revisions. **NDJSON** means one complete JSON object per newline, which lets a plain text model stream yield independently valid patches without waiting for one large JSON document or a completed tool call.

Gate 1 deliberately does not change the SVG/GSAP renderer, connect to voice, persist SceneDocs, or generalize the primitive vocabulary beyond Gate 0. It also does not replace the existing SDL tools. Those decisions belong after model validity, first-visible latency, interruption safety, and visual usefulness are measured.

## Progress

- [x] 2026-09-02 03:25 IST: Re-read `.agent/PLANS.md`, confirmed the Gate 0 branch is clean and pushed at `b99b269`, and reviewed the Gate 0 scene state, planner, executor, demo, and tests.
- [x] 2026-09-02 03:25 IST: Traced current backend model streaming, chat SSE, SDL tool completion, Voice V2 revision contracts, frontend SSE parsing, auth boundaries, and live-provider cost controls.
- [x] 2026-09-02 03:25 IST: Chose a dedicated bounded NDJSON patch stream, server-authoritative lifecycle metadata, one repair attempt, a provider-fake lab, and no voice or persistence coupling for Gate 1.
- [x] 2026-09-02 03:36 IST: Implemented and pushed strict backend/frontend scene contracts, exact model NDJSON parsing, stateful SSE framing, and atomic patch application in `f7349ee`.
- [x] 2026-09-02 03:45 IST: Implemented and pushed the injectable authoring service and authenticated SSE route with authoritative revisions, one repair, timing, timeouts, redacted failures, and exact upstream cleanup in `d90cf54`.
- [x] 2026-09-02 03:58 IST: Implemented and pushed the single-flight browser runtime, bounded queue, materialized interruption, stale-token rejection, replay ancestry handling, model transport, and regression tests in `e6e4cfe`.
- [x] 2026-09-02 03:58 IST: Added the authenticated `/canvas/generate` product route and development-only, environment-gated `/labs/live-scene` fixture route without importing Firebase into the lab bundle.
- [x] 2026-09-02 04:00 IST: Passed five real-Chromium scenarios for progressive rendering, repair, terminal failure, interruption with late output, replay, and 320-pixel no-overflow; visually reviewed desktop and mobile screenshots and corrected LaTeX placement.
- [x] 2026-09-03 00:00 IST: Closed adversarial provider-boundary findings in `4f16419`: bounded every disconnect cleanup hop, separated the 64 KiB model frame from the 96 KiB canonical SSE envelope, aligned Unicode code-point limits, capped output tokens, and made the paid endpoint default-off with authenticated per-user/global/rate admission.
- [x] 2026-09-03 00:01 IST: Pushed deterministic acceptance evidence in `05623ae`: 20 repeated interruptions with zero stale nodes, 1.857 ms max-patch local p95, 22.2 ms accepted-patch-to-visible browser p95, and desktop, 375×812, and 320×568 coverage.
- [x] 2026-09-03 00:02 IST: Pushed Strict Mode, mobile hierarchy, equation placement, and non-retryable reset behavior in `f7b5e61`.
- [x] 2026-09-03 00:02 IST: Added the explicitly budgeted, redacted, server-only provider probe in `e785964`; its cost ceiling uses UTF-8 bytes plus framing reserves and cannot be mistaken for browser/usefulness evidence.
- [x] 2026-09-03 00:03 IST: Bounded long-lecture replay history to 32 semantic snapshots while preserving the authoritative revision after pruned replay in `8540130`.
- [x] 2026-09-03 00:07 IST: Pushed ASGI send-disconnect ownership and cancellation-safe admission release in `0465c67`; the regression proves provider events close and the account can reacquire capacity after a real `ClientDisconnect`.
- [x] 2026-09-03 00:10 IST: Pushed authoritative revision normalization for public interruption during pruned replay in `093c56e`; the next generation resumes from retained revision 5 rather than the synthetic display revision 1.
- [x] 2026-09-03 00:14 IST: Pushed scene/chat output-budget isolation in `3f6d4e7`; a valid existing `LLM_MAX_TOKENS=8192` no longer crashes application startup while paid scenes are default-off.
- [x] 2026-09-03 00:16 IST: Pushed constant-backed and blank-safe scene-budget defaults in `8b0e915`; an explicitly blank scene override remains equivalent to unset rather than failing during import.
- [ ] Run a small live-provider corpus only after the user supplies the repository-required explicit cost budget.
- [x] 2026-09-03 00:16 IST: Passed the full repository gates: 1,198 backend tests and Ruff at `3f6d4e7`, followed by all 11 focused config/API tests and Ruff at `8b0e915`; 368 frontend tests, exact-delta lint/typecheck, and all seven Chromium scenarios at `093c56e`; the production build generated all 11 pages at `8540130`.
- [x] 2026-09-03 00:17 IST: Independent adversarial review returned **SHIP** for deterministic Gate 1 at pushed HEAD `8b0e915`, with no remaining P0-P2 findings and an independently verified production 404 for the lab route. The separate paid live-model product gate remains pending.
- [x] 2026-09-04 03:10 IST: Connected Azure `gpt-oss-120b`, verified custom stack and queue prompts through canonical server/browser paths, and pushed provider, lab, truncation, low-reasoning, and server patch-target fixes through `9089b13`.
- [x] 2026-09-04 03:13 IST: Re-ran the complete regression at `9089b13`: 1,234 backend tests and Ruff passed; 369 frontend tests, ESLint, TypeScript, and all eight Chromium scenarios passed with 20 ms deterministic first-visible p95.

## Surprises & Discoveries

- Existing streamed tool calls cannot provide progressive visuals. `backend/murmur/llm/openai.py` accumulates tool-call arguments until `tool_call_done`, and `teach_with_visuals` executes only after the complete SDL tool payload exists. Gate 1 therefore needs plain provider text streaming rather than another canvas tool.
- `web/src/hooks/use-chat.ts` splits each received network chunk on newline without retaining an incomplete line. JSON or SSE data split across transport chunks can be lost. Gate 1 needs its own stateful decoder and must not copy this parser.
- The authenticated Gate 0 route was hard to test because both Firebase sign-in providers are disabled in the configured project. A committed lab must be explicitly environment-gated and unavailable by default, but it must not require Firebase once enabled.
- The repository marks real-provider tests as requiring an explicit cost budget. Gate 1 can implement and exhaustively test the provider boundary with fakes while waiting for that authorization; it cannot honestly pass the live-model quality gate without the bounded real call.
- Voice V2 already has useful lifecycle vocabulary—task generation, base/result revision, acknowledgement, first-visible timing, and stale-generation rejection—but no active producer-to-renderer path. Gate 1 will mirror the semantics internally without importing the voice transport.
- React Strict Mode's development setup/cleanup/setup cycle exposed premature runtime disposal that unit tests did not reproduce. The UI now defers disposal one microtask and cancels it when the replacement setup reclaims ownership.
- Adversarial runtime review found that interrupting replay must truncate ledger ancestry before a new model branch starts. Keeping abandoned future replay records would make the next replay reconstruct a history the learner never saw.
- Gate 0's LaTeX primitive uses a fixed 500-pixel `foreignObject` and centers KaTeX inside it, so fixture coordinates describe that viewport's origin rather than the formula's visual left edge. The Gate 1 fixture offsets and sizes the equation accordingly without changing the renderer in this gate.
- Next 16 development commands generate `web/AGENTS.md`, `web/CLAUDE.md`, and development-only `next-env.d.ts` paths. These are local dev artifacts for this worktree and are excluded from Gate 1 checkpoints.
- Closing only the public SSE generator does not automatically close nested async iterators. The route, service attempt, provider adapter, and factory-owned HTTP client each need an explicit bounded close hop; a stuck closer must not replace cancellation or hang a request.
- A valid 64 KiB model frame can grow when canonicalized and wrapped with server-owned lifecycle metadata. Model-input and browser-wire budgets must be separate, and the browser/server wire cap must be identical.
- JavaScript string length counts UTF-16 code units while Python/Pydantic counts Unicode code points. Spreading the normalized JavaScript string aligns the 512-character contract for emoji and other astral characters.
- Starlette can lose an ASGI client while sending a response body without closing the body iterator. The live-scene response must own and bounded-close its iterator in `__call__`, and an admission lease must remain retry-closeable if cancellation arrives while it waits for the admission lock.
- Pruned replay uses adjacent synthetic revisions only to satisfy the motion planner. Any interrupted materialization must be normalized back to the retained record's authoritative revision before it becomes the next generation's base.
- A scene-specific 4,096-token safety cap cannot inherit the general chat output budget. Because the scene service is composed during application construction even while its paid route is default-off, that inheritance allowed a valid `LLM_MAX_TOKENS=8192` chat configuration to take down startup.
- Environment variables may be present but blank in deployment systems. Scene-budget fallback must use the protocol constant and treat a blank value as unset, preserving the repository's previous configuration semantics.
- Azure GPT-OSS exhausted the 4,096 completion-token cap while beginning an extra NDJSON frame; a metadata-only reproduction confirmed `finish_reason=length`. Hidden reasoning and visible JSON share this cap, so lowering reasoning effort and bounding authored frames is safer than raising the scene budget.
- A prompt-only patch count is advisory. The server must stop after the same target so a useful accepted prefix cannot be reclassified as invalid because the model begins an unrequested trailing frame.

## Decision Log

- 2026-09-02, Codex: Use a dedicated `POST /api/live-scenes/stream` authenticated SSE endpoint. Extending chat canvas tools would delay every visual until complete tool arguments arrive, and connecting Voice V2 would mix two unproven systems.
- 2026-09-02, Codex: Ask the model for strict NDJSON patch drafts containing full Gate 0 scene nodes and only `put` or `remove` operations. Do not use JSON Patch, partial property merges, `clear`, camera commands, model-authored CSS declarations, or ephemeral highlight operations in V1.
- 2026-09-02, Codex: Keep lifecycle metadata out of model output. The request supplies the user generation and base scene; the service stamps attempt, sequence, base revision, and result revision after atomic validation.
- 2026-09-02, Codex: Treat each patch atomically and render patches single-flight. Patch N+1 may be buffered, but it cannot plan or animate until patch N has completed or materialized on cancellation.
- 2026-09-02, Codex: Permit exactly one model repair attempt after the first malformed, invalid, or empty stream. The repair starts from the last server-accepted snapshot. A second failure terminates the generation while preserving that snapshot.
- 2026-09-02, Codex: Keep the product page authenticated, and add a server-environment-gated `/labs/live-scene` fixture route that returns 404 unless `MURMUR_SCENE_LAB=1` and the Next server is running in development. This permits deterministic browser QA without weakening production auth.
- 2026-09-02, Codex: Require a positive user-authorized cost cap before any real provider probe. Fake-provider and fixture coverage remain part of the normal test suite and make no paid calls.
- 2026-09-02, Codex: On replay interruption, retain only the materialized replay prefix as the authoritative ledger. A later generation and subsequent replay must branch from exactly what was visible, not from abandoned accepted future revisions.
- 2026-09-02, Codex: Keep the Gate 1 interface centered on a large persistent board, a narrow teacher's desk, visible prompt/status/ledger, and restrained Murmur chalk, amber, lavender, and sage signals. Do not introduce a second card-heavy design system for the lab.
- 2026-09-03, Codex: Keep paid scene authoring disabled by default. When explicitly enabled, allow one concurrent generation per authenticated user, four per process, ten starts per user per rolling minute, and at most 4,096 output tokens per attempt.
- 2026-09-03, Codex: Use a 64 KiB model-authored NDJSON frame limit and a separately enforced 96 KiB canonical SSE event limit. Reject an oversized canonical patch before scene mutation; never let the browser discover a server/client budget mismatch after acceptance.
- 2026-09-03, Codex: Retain at most 32 accepted replay snapshots in the browser. When older history is pruned, render the first retained snapshot as a rebased visual checkpoint, then restore its authoritative server revision before any new generation.
- 2026-09-03, Codex: Treat the manual live probe as provider-to-server protocol evidence only. It stores hashes and timings rather than scene bodies; browser first-visible timing and visual usefulness require a separate UI run and human review.
- 2026-09-03, Codex: Wrap Starlette's streaming response with an explicit body owner so an exception from ASGI `send()` still closes the route encoder, service/provider chain, and admission lease. Lease closure is serialized, idempotent, and marked complete only after capacity release succeeds.
- 2026-09-03, Codex: Synthetic adjacent revisions exist only inside pruned replay playback. Both the awaited playback outcome and the synchronous public interrupt path convert retained nodes back to the accepted record's authoritative revision.
- 2026-09-03, Codex: The scene authoring output budget defaults directly to `MAX_SCENE_MODEL_OUTPUT_TOKENS` and never inherits `LLM_MAX_TOKENS`. Provider, model, and temperature may retain their compatible selection defaults, but a broader chat budget must not violate the bounded scene protocol or break default-off startup.
- 2026-09-04, Codex: Azure GPT-OSS scene generation uses low reasoning, scene-owned temperature 0.2, three initial patches, and one repair patch. The service enforces the same patch targets and closes upstream immediately after they are reached.

## Outcomes & Retrospective

The deterministic Gate 1 implementation is complete through the hardened browser checkpoint. Commits `f7349ee`, `d90cf54`, `e6e4cfe`, `4f16419`, `05623ae`, `f7b5e61`, `e785964`, `8540130`, `0465c67`, `093c56e`, `3f6d4e7`, and `8b0e915` are pushed to `origin/codex/realtime-scene-core` with verified matching remote SHAs. The final full backend run passed 1,198 tests with Ruff clean; the exact final config delta passed all 11 focused tests plus Ruff. The final frontend run passed 368 tests with lint and typecheck clean, the production build generated all 11 pages, and all seven dedicated Chromium scenarios passed in 36.6 seconds; the exact final runtime delta passed all 16 focused tests. The browser suite demonstrates that patch 1 becomes visible before terminal completion, a repair completes on attempt 2, a double failure preserves revision 0, late post-interrupt output does not land, accepted revisions replay, and desktop plus 375- and 320-pixel layouts remain reachable without horizontal overflow. Twenty repeated interruptions admitted zero stale nodes. The final measured local max-patch decode/apply/plan was 1.361 ms p95 against a 16 ms target, and deterministic complete-frame-to-first-visible was 18.5 ms p95 against a 100 ms target.

The desktop and mobile review confirmed the intended teaching-workstation hierarchy: a dominant persistent board, prompt and interruption controls in a narrow teacher's desk, narration adjacent to generation state, and a visible accepted-patch ledger. The frontend-design guidance materially kept the UI asymmetrical, low-card, responsive, keyboard-focused, and restrained to Murmur's existing signal colors. The review also caught and corrected the renderer-specific LaTeX offset rather than hiding it behind fixture assertions.

Independent adversarial review found no remaining P0-P2 issues and returned **SHIP** for the deterministic implementation at pushed HEAD `8b0e915`. It separately confirmed that a production server returns 404 for `/labs/live-scene` even when the development lab flag is set.

Paid Azure integration evidence now exists for two custom prompts. A direct stack request completed three first-attempt patches at 2,468 ms first patch / 2,762 ms total. A browser queue request completed revision 3 with semantically correct queue, enqueue, and dequeue ink at 2,614 ms first patch / 4,947 ms total and no console errors; its bounded repair retained the two valid initial patches and added one repair patch. This proves connectivity, real prompt influence, progressive rendering, safe repair, and one warm browser sample within the 3-second p95 ceiling.

The overall Gate 1 product verdict remains **pending the required ten-prompt live corpus**. Two successful prompts cannot establish the 90% first-attempt-validity, 1.5-second median, or 3-second p95 requirements. Run the redacted corpus only after an explicit positive cost budget and then review the same outputs for visual usefulness in the browser.

## Context and Orientation

`web/src/lib/live-scene/types.ts`, `state.ts`, and `planner.ts` define Gate 0's immutable scene snapshots and deterministic transitions. `web/src/features/live-scene/svg-motion-executor.ts` applies a plan through the concrete SVG/GSAP renderer and reports which node IDs materialized before cancellation. `web/src/features/live-scene/pythagoras-demo.tsx` demonstrates interruption and replay with hand-authored snapshots.

`backend/murmur/llm/base.py` exposes provider-neutral `stream()` and `complete()` methods. `backend/murmur/llm/factory.py` builds OpenAI-compatible, Groq, or Gemini clients from configuration. `backend/murmur/chat/service.py` and `backend/murmur/api/routers/chat.py` show the existing service and SSE composition, but Gate 1 must not reuse the chat pipeline's tools, memory, persistence, or fragile frontend parser.

The new backend modules will live under `backend/murmur/live_scene/`. `contracts.py` will define strict Pydantic request, SceneNode, draft-patch, and server-event models with forbidden unknown keys and hard size/coordinate/style bounds. `stream_parser.py` will buffer arbitrary provider chunks and emit complete NDJSON records. `prompt.py` will contain the compact fixed schema, board constraints, current-scene context, and repair instructions. `service.py` will own model attempts, atomic patch application, revision stamping, limits, cancellation cleanup, and timing events. `backend/murmur/api/routers/live_scenes.py` will expose the authenticated SSE route through an injected service registered in `backend/murmur/api/application.py` and `dependencies.py`.

The new frontend protocol code will live beside Gate 0 under `web/src/lib/live-scene/`. `patch.ts` will decode exact server events and atomically apply `put` and `remove` operations to a temporary snapshot before creating the next frozen `SceneState`. `sse.ts` will reconstruct SSE data across arbitrary byte boundaries. `web/src/features/live-scene/stream-runtime.ts` will own the exact generation/attempt token, bounded patch queue, one active playback, committed versus provisional state, interruption, materialization, and replay ledger. React UI will live in a separate `model-scene-demo.tsx` so the Gate 0 reference remains unchanged.

`web/src/app/(app)/canvas/generate/page.tsx` will be the authenticated product route. `web/src/app/labs/live-scene/page.tsx` will expose the same component with deterministic fixture streams only when the explicit development lab gate is enabled. A dedicated Playwright configuration will avoid the voice suite's audio-fixture requirements.

## Plan of Work

First, define and test the protocol without React, HTTP, or a real provider. The backend and frontend will both reject unknown fields, unsupported node kinds, invalid IDs, unsafe colors, excessive coordinates or points, text/LaTeX overflow, duplicate operation targets, absent removals, empty or no-op patches, scenes over 128 nodes, patches over 16 operations, and frames over 64 KiB. The accepted paint grammar will be a small set of theme-safe colors plus six-digit hex values; model-authored font families and CSS functions such as `url(...)` will be rejected. A patch applies to a temporary map and becomes visible only after the complete candidate SceneState and its motion plan validate.

Second, implement the backend service around an injected `LLMClient`. For each request it will validate the current snapshot, build a fixed model prompt, emit a `started` event, incrementally parse provider chunks, atomically accept up to eight patches, and emit server-stamped `scene_patch` events. The first invalid frame closes that provider stream and starts one repair attempt using the last accepted snapshot and a bounded error description. A second invalid/empty stream emits a friendly terminal error. Provider streams are closed in `finally` so a disconnected or aborted browser does not leave a generation running. Unit tests will use fake async clients and will not import credentials.

Third, expose the service as authenticated SSE and add route-level tests through FastAPI dependency overrides. The endpoint will derive identity from the verified Firebase user and ignore any client identity claim. It will not create sessions, mutate persistent canvas state, or share runtime objects across requests. The API contract test will be updated intentionally for the new route.

Fourth, implement the browser protocol and runtime. SSE parsing will use a streaming `TextDecoder` and retain incomplete event lines. Every server event must match the active generation and attempt. Accepted patches queue behind the current animation. On playback completion, the target becomes committed and the next patch begins. On interruption, the runtime aborts fetch, invalidates the token, clears parser and queue state, cancels motion, materializes only executor-reported IDs, commits that partial revision, and ignores every late old-generation chunk or promise completion. Replay clears the renderer and re-plans accepted snapshots without calling fetch.

Fifth, build the Gate 1 interface and fixture lab. The UI will show prompt, generation/revision status, accepted-patch ledger, narration, first-visible and patch timing, generate, interrupt, retry, replay, and reset controls. Deterministic fixture modes will cover normal progressive construction, one invalid draft repaired successfully, repair failure, and a late stale frame after interruption. The fixture will deliberately split JSON and SSE at awkward byte boundaries so the visible demo exercises the real parser.

Finally, run focused Python and frontend tests, full static/unit gates, the production build, and dedicated browser scenarios at desktop, 375×812, and 320×568. If the user authorizes a positive budget, run a small manual live-provider corpus against the same service, saving only redacted patch/latency reports under ignored `var/`. Gate 1 passes only if the real model progressively produces useful accepted patches within the validity and latency thresholds below; a polished fixture alone is not a pass.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` unless a command changes directory.

Inspect source state throughout implementation:

    git status --short
    git diff --check

Run focused backend tests:

    python3 -m pytest -q tests/test_live_scene_contracts.py tests/test_live_scene_stream_parser.py tests/test_live_scene_service.py tests/test_api_contract.py

Run focused frontend tests:

    cd web
    npm test -- --run src/lib/live-scene src/features/live-scene

Run full repository gates appropriate to the touched code:

    python3 -m pytest -q
    python3 -m ruff check backend/murmur/live_scene backend/murmur/api tests/test_live_scene_*.py
    cd web
    npm run check

Run the auth-independent fixture lab:

    cd web
    MURMUR_SCENE_LAB=1 npm run dev -- --hostname 127.0.0.1 --port 3102

Then open `http://127.0.0.1:3102/labs/live-scene` and exercise normal, repair, failure, interruption, and replay modes. Run the dedicated Playwright suite with its documented local web-server command.

The manual live-provider probe will live under `scripts/manual/` and refuse to run unless an explicit positive budget flag is supplied. It will load provider credentials without printing them, cap prompt count and output tokens, redact error bodies, write its report under ignored `var/`, and report estimated maximum spend before starting.

## Validation and Acceptance

Protocol tests must prove that every character-by-character and multi-frame chunk split reconstructs the same patch sequence; CRLF, UTF-8 boundaries, multiple events per network chunk, and a final frame without a trailing newline must work. Prose, code fences, malformed JSON, unknown fields, oversized input, invalid nodes, duplicate targets, absent removals, no-op patches, stale generations, wrong attempts, stale base revisions, skipped revisions, and queue overflow must cause zero mutation.

The backend must stamp consecutive revisions and never trust lifecycle metadata from model text. It must permit at most eight accepted patches and one repair attempt. Cancellation or disconnect must close the exact provider stream. Auth tests must show that the live endpoint rejects an unauthenticated caller and derives user identity only from the verified dependency.

The browser runtime must draw patch 1 before the provider or fixture stream finishes. Patch N+1 must not animate concurrently with patch N. An interrupted enter, replace, or remove must commit exactly the executor-reported materialized IDs; queued patches and incomplete old-generation text must be discarded. Late chunks and callbacks from the old token must cause no executor call or ledger mutation. Replay must make zero network calls and reproduce the same final revision, node IDs, node values, and canonical scene hash.

At desktop, 375×812, and 320×568, the board must remain visible, controls must remain reachable, interrupt must have at least a 44-pixel touch target, and status or errors must not cover the scene. Normal, repaired, terminal-failure, interrupted, and replay states must be announced through `aria-live`. The primary failure text must say that the last board is safe and must not expose raw provider or schema details.

Local patch decode, validation, application, and planning should remain under 16 ms p95 for a maximum allowed patch. Accepted-patch-to-first-visible should remain under 100 ms p95 in deterministic browser runs. Twenty repeated fixture interruptions must admit zero stale nodes.

Under an explicit live-provider budget, use at least ten warm prompts including Pythagorean geometry and unseen education/system-diagram prompts. At least 90% must produce a valid first attempt, 100% must either complete within the one repair or terminate without corrupting the board, median prompt-to-first-visible must be at most 1.5 seconds, p95 must be at most 3 seconds, and subsequent accepted-patch gaps should be at most 1.5 seconds p95. Report provider/network time separately from local validation and renderer time.

Gate 1 passes only when the deterministic safety/UI requirements and the budgeted live-model validity/latency requirements both pass. If model validity or latency misses the threshold, keep the implementation and evidence, mark the gate failed, and improve the schema or prompt before connecting voice.

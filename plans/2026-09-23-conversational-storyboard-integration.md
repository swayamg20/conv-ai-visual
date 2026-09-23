# Make the verified storyboard part of normal Murmur conversation

## Purpose / Big Picture

This change turns Gate 1.8 from a separate studio into a real Murmur capability. A signed-in learner can open an ordinary agent session, type a request such as “Explain projectile motion by comparing 30 degrees and 60 degrees,” and the conversational model can choose a closed visual-lesson tool. That tool emits a typed storyboard command. The session then replaces its ordinary canvas surface with the existing certified Gate 1.8 board, starts the provider-free anchor, hands the exact prompt and problem to the Azure-backed Director, and presents each verified animated beat as it settles. The conversational response remains available beside the board, and unsupported or non-projectile turns continue through the existing SDL/canvas path.

This milestone deliberately reuses the Gate 1.8 compiler, verifier, choreography runtime, interruption, continuation, and replay boundaries. It does not create a second animation implementation, infer visual intent with frontend keyword matching, or claim arbitrary-topic support. The first conversational visual lesson remains the closed `projectile_comparison_storyboard_v1` domain. The public Azure deployment is currently chat-first while the separate first-party voice transport is under provider-free validation; this work must not reintroduce the retired LiveKit deployment topology.

In this plan, a **storyboard command** is the small server-validated message emitted when the conversational LLM selects the projectile lesson tool. It contains the protocol version, a supported speed and ascending angle pair, and the learner’s bounded instruction. An **embedded storyboard** is the Gate 1.8 runtime rendered inside the ordinary agent session’s visual workspace rather than as the full-page `/canvas/storyboard` studio. The **legacy canvas** is the existing SDL/SVG canvas retained for all non-storyboard turns.

## Progress

- [x] 2026-09-23 00:33 IST: Verified the dirty primary checkout, created clean worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-conversation-storyboard`, and fast-forwarded branch `codex/conversation-storyboard` to pushed candidate `65e803ed819cd2865eeba046f8c6c4a44c967608`, which contains merged Gate 1.8 plus the first-party Azure deployment hardening.
- [x] 2026-09-23 00:33 IST: Traced normal chat, legacy voice, Voice V2, SDL, Gate 1.8, session ownership, and Azure deployment boundaries. Confirmed that normal chat currently has no Gate 1.8 command and that the live voice product is intentionally disabled during its canary.
- [x] 2026-09-23 01:03 IST: Added the strict `start_projectile_storyboard` tool, server-owned versioned command, certified problem validation, serialized visual mutation policy, dedicated chat SSE command, and runtime guidance for both new and stored canvas agents. The focused command/tool/chat slice passes 33 tests and Ruff.
- [x] 2026-09-23 01:03 IST: Fixed a pre-existing callback dispatch defect exposed by the new command tests: synchronous canvas and animation callbacks were invoked twice because their `None` result was awaited and caught as `TypeError`. All three visual callbacks now execute once and await only genuinely awaitable results.
- [x] 2026-09-23 01:32 IST: Added a strict browser command decoder, byte-safe incremental SSE consumption, authoritative chat-session notification, stale-request suppression, and a validated storyboard callback. Malformed commands are isolated without disturbing ordinary chat, SDL, or canvas events.
- [x] 2026-09-23 01:32 IST: Added an embedded, one-shot auto-starting Gate 1.8 presentation and mounted it in the normal agent session. The legacy SVG canvas stays mounted underneath; closing or clearing the lesson returns to it, while a new command key replaces and disposes the prior Gate lifecycle.
- [x] 2026-09-23 01:32 IST: Bound embedded Director transport to the server-owned tutoring-session path and added focused frontend coverage. The integrated browser slice passes 36 tests, TypeScript, focused ESLint, and whitespace validation.
- [x] 2026-09-23 01:48 IST: Added `POST /api/sessions/{session_id}/storyboard/stream`, resolving authentication, persistent session ownership, and the stored agent binding before admission or provider dispatch. Endpoint coverage includes malformed hostile bodies, missing/foreign sessions and agents, admission rejection, exact SSE transport, disconnect cleanup, and synchronous construction failure.
- [x] 2026-09-23 02:10 IST: Closed pre-release review gaps: the storyboard tool is now advertised only after a storyboard callback is installed, so legacy voice cannot report a visual handoff it cannot deliver; repeated commands have a keyed unmount/remount proof; prompt routing has explicit supported/fallback coverage; and fatal chat SSE decoding now cancels and releases its held-open response.
- [x] 2026-09-23 12:26 IST: Qualified pushed commit `b0df7c068e358f48905bd3234a30fd80c3071341`: 7,479 backend tests; 1,345 frontend tests; full Ruff lint/format; frontend lint, generated-route TypeScript, and production build; 15/15 verified-scene browser proofs; Gate 1.8 artifact-contract tests, 6/6 accelerated proofs, normal-speed capture, 3/3 authenticated product-smoke proofs, and finalized artifact validation all pass. The first accelerated attempt was invalidated by macOS clamshell sleep; unchanged caffeine-protected focused and full reruns passed.
- [ ] Push every coherent code milestone, open and merge the reviewed branch, deploy the exact pushed commit to Azure, and verify the authenticated live session plus `/healthz` release identity.

## Surprises & Discoveries

- The existing conversational path already lets the LLM call `teach_with_visuals`, but that event enters the older SDL compiler and cannot produce Gate 1.8’s certified frontier, interruption, or deterministic projectile choreography.
- Gate 1.8’s `LiveSemanticStoryboard` owns a full-page header, Director sidebar, controller, runtime, and renderer. It cannot be safely nested unchanged in the session canvas; it needs an explicit embedded presentation mode while keeping the same lifecycle owner.
- Gate 1.8 intentionally anchors a bound projectile problem before asking its Director. Therefore the browser must not send every transcript directly into Gate 1.8: the conversational model must first select the closed lesson tool, otherwise unrelated prompts would draw a projectile anchor before eventually abstaining.
- The existing tool runtime's sync-callback fallback called synchronous canvas and SDL callbacks twice. `inspect.isawaitable` now distinguishes sync and async results without treating callback-body `TypeError` as a transport signal.
- The clean Azure candidate is ten commits ahead of `origin/main`, removes LiveKit from the deployment topology, and deploys a chat-first product with voice disabled while the provider-free WebSocket canary is validated. The current public release still reports older commit `72d1d83f75e11c34ec45754ac516152d66fa3146`.
- Because animation schemas were appended as one global canvas group, the first implementation also advertised the storyboard tool to legacy voice pipelines that had no storyboard callback. Tool exposure now depends on both canvas mode and an installed storyboard delivery path.
- Fatal byte-level SSE decoder errors originally escaped without cancelling or releasing the chat response reader. A malformed held-open stream could therefore keep server/provider work alive after the UI had abandoned it; the chat transport now mirrors the Gate stream's cancellation discipline.
- A local Gate 1.8 run appeared to exceed 300-second and 90-second timeouts by 985 and 1,027 seconds. `pmset` proved matching clamshell-sleep intervals; the supposedly stuck snapshots were already safe terminal frontiers. Sleep-protected reruns on the unchanged commit passed 2/2 focused and 6/6 full, so no product timeout or runtime assertion was weakened.

## Decision Log

- 2026-09-23, Codex: Use a model-selected closed tool rather than regex or frontend intent detection. This preserves the product claim that the conversation decides when a visual lesson is appropriate while keeping unsupported requests on the existing canvas.
- 2026-09-23, Codex: Emit only a typed problem specification and instruction from the conversation layer. Gate 1.8 remains the sole owner of lesson beat selection, physics, equations, geometry, camera, timing, verification, and replay.
- 2026-09-23, Codex: Embed the existing Gate 1.8 controller/runtime instead of copying its compiler or rendering code. A compact presentation variant may change chrome, but not protocol or trust boundaries.
- 2026-09-23, Codex: Land and deploy from the clean first-party Azure candidate lineage. Do not touch the dirty main worktree and do not restore the paid LiveKit topology.
- 2026-09-23, Codex: Ship the currently deployable chat conversation path as the live acceptance surface. Carry the typed command through shared legacy voice callbacks where possible, but do not describe provider-free WebSocket echo as working conversational audio.

## Outcomes & Retrospective

Implementation and live acceptance are still in progress. This section will record the final pushed commits, tests, Azure revision, live URL, observed request-to-board behavior, and any remaining domain limitation after deployment.

## Context and Orientation

`backend/murmur/llm/pipeline.py` assembles model tools whenever canvas mode is enabled. `backend/murmur/llm/tool_runtime.py` executes the built-in canvas tools and publishes animation side effects through one callback. `backend/murmur/chat/service.py` drains those side effects as `animation_event` records on the authenticated `/chat` SSE stream. `backend/murmur/agents/prompting.py` and `backend/murmur/core/config.py` tell default and user-created agents when to use visual tools.

The browser receives chat events in `web/src/hooks/use-chat.ts`. The normal agent product is `web/src/app/(app)/session/[agentId]/page.tsx`, which owns the trusted `agentId`, persistent `sessionId`, voice/chat mode, shutdown, conversational messages, and the existing `SVGCanvas` ref. The visual workspace appears in both its voice and chat layouts.

Gate 1.8’s full product UI is `web/src/features/live-scene/live-semantic-storyboard.tsx`. It builds a `ChoreographyCanvasBridge`, `SemanticStoryboardStreamRuntime`, and `SemanticStoryboardSessionController`; it uses `runAuthenticatedSemanticStoryboardStream` to post Firebase-authenticated requests to `/api/live-scenes/choreography/stream`. The backend semantic contracts, Director, compiler, independent verifier, certificate chain, service, and wire live in `backend/murmur/live_scene/semantic_storyboard_*.py`. Those existing correctness boundaries must remain unchanged unless a failing integration test proves a necessary additive change.

The Azure release driver is `scripts/deploy_azure.py`. It requires a clean pushed source commit, builds immutable frontend/backend images, deploys them to Azure Container Apps, and verifies image digests and release identity. `infra/azure/apps.bicep` contains the chat-first canary configuration and must remain free of LiveKit resources.

## Plan of Work

First, add a focused backend command module in the Gate 1.8 namespace. It will expose one exact `start_projectile_storyboard` tool schema and handler. The handler will validate prompt length and Unicode through the existing storyboard prompt contract, validate speed and angle values through `PairedProjectileComparisonSpecV1`, and return one JSON-safe versioned command. The LLM runtime will treat this as a mutating visual tool and publish the command through its existing animation callback. Agent and default tutor prompts will state that this tool is for supported same-height, no-drag, same-speed projectile comparisons, while `teach_with_visuals` remains the fallback for other topics.

Second, extend the browser conversation boundary. Add a strict decoder for the exact command shape and make `useChat` expose an `onStoryboardCommand` callback. Replace the current line-local SSE parsing with a small incremental buffer so a command split across network chunks cannot be lost. Unknown tools and malformed storyboard commands must not alter the board.

Third, give `LiveSemanticStoryboard` an explicit embedded and auto-starting mode. The component will still create exactly one controller/runtime/renderer lifecycle, but embedded mode will omit page navigation and the large authoring sidebar, show a compact status/control rail, and render the same certified stage. Auto-start will execute once after the canvas bridge is attached, even under React Strict Mode. Stop, Continue, Replay, Reset, error, decline, and accepted-prefix behavior will stay backed by the existing controller rather than new local state.

Fourth, mount that component in the session visual workspace. A decoded command creates a new session-scoped launch generation, causing a fresh embedded storyboard to mount with the exact validated prompt/problem. The existing `SVGCanvas` stays mounted behind the storyboard so its state is retained for later non-storyboard turns. The learner can close the visual lesson and return to that canvas. Repeated commands replace the old storyboard lifecycle cleanly. Session shutdown or navigation disposes the storyboard through React unmount.

Finally, qualify and release. Run focused backend and frontend tests after each milestone, then the complete relevant regressions, static checks, optimized frontend build with safe test Firebase values, and no-LiveKit/deployment tests. Push each coherent milestone. After review, merge, deploy the exact pushed commit with the existing private environment files, and verify the public health identity plus an authenticated browser scenario in which a normal chat turn launches and completes the certified projectile board.

## Concrete Steps

Work only in the clean integration worktree:

    cd /Users/swayam.gupta/Documents/GitHub/conv-ai-visual-conversation-storyboard

For the backend command milestone:

    uv run pytest -q tests/test_conversation_storyboard.py tests/test_llm_tool_runtime.py tests/test_chat_service.py
    uv run ruff check backend/murmur tests/test_conversation_storyboard.py tests/test_llm_tool_runtime.py tests/test_chat_service.py
    uv run ruff format --check backend/murmur tests/test_conversation_storyboard.py tests/test_llm_tool_runtime.py tests/test_chat_service.py

For the browser milestone:

    cd web
    npm ci
    npm test -- --run src/hooks/use-chat.test.ts src/features/live-scene/live-semantic-storyboard.test.tsx 'src/app/(app)/session/[agentId]/page.test.tsx'
    npm run lint
    npx tsc --noEmit

For complete qualification, return to the repository root and run the backend regression slices for chat, tool runtime, semantic storyboard, API, deployment, and no-LiveKit invariants. Then run the frontend unit suite, Gate 1.8 browser proof appropriate to the unchanged protocol, lint, TypeScript, formatting, and optimized build. Record exact commands and results in `Progress`.

At every coherent milestone:

    git diff --check
    git status --short
    git add -- <explicit files>
    git commit -m "<meaningful milestone message>"
    git push -u origin codex/conversation-storyboard

The final deployment uses the existing private backend and frontend environment files and subscription/tenant selection without printing secrets:

    uv run python scripts/deploy_azure.py deploy --backend-env <private-backend-env> --frontend-env <private-frontend-env>

After deployment, verify `/healthz`, backend `/readyz`, the immutable image release labels, the absence of LiveKit resources, and one authenticated normal-session projectile lesson in the browser.

## Validation and Acceptance

The backend boundary passes when the model sees the new tool only in canvas mode; valid defaults and all supported speed/angle pairs produce the exact versioned command; unsupported angles, speeds, reversed/equal pairs, oversized prompts, coercions, unknown keys, and unsupported problem assumptions fail without publishing a side effect; and the tool is serialized with other mutating visual operations.

The conversation boundary passes when a fragmented SSE event is decoded exactly once, a valid `start_projectile_storyboard` event invokes the typed callback, malformed or unknown animation events do nothing, ordinary chunks and existing SDL events behave unchanged, and closing or replacing a chat request cannot publish a stale storyboard command.

The embedded runtime passes when one normal-session command automatically paints the provider-free anchor and dispatches the Director only after post-paint settlement; the model-selected verified beats produce the same program/certificate semantics as `/canvas/storyboard`; Stop retains the exact settled frontier; Continue uses the learner’s next instruction; Replay makes zero network requests; Reset clears the embedded board; repeated commands dispose stale generations; and closing the storyboard reveals the unchanged legacy canvas.

The product acceptance scenario is: sign in, open a normal physics agent session, switch to Chat, type “Explain projectile motion visually by comparing launches at 30 degrees and 60 degrees,” observe the assistant select the visual lesson and the right-hand workspace automatically become the Gate 1.8 living board, observe certified animated beats without pressing a separate Start button, interrupt or replay them, then close the lesson and continue chatting. A non-projectile request must not open the projectile board.

The release is complete only when all relevant local checks pass on a clean pushed commit, the branch is reviewed and merged, Azure reports the exact deployed release and immutable image digests, the authenticated live acceptance scenario passes, and the public URL remains usable without LiveKit. The milestone must be described honestly as a conversational projectile-storyboard capability, not arbitrary-topic generative animation or completed production voice.

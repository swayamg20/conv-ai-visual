# Prove an interruptible real-time scene core

## Purpose / Big Picture

Murmur currently accepts complete Scene Description Language (SDL) lessons, compiles them into SVG drawing commands, and schedules those commands alongside speech. The visible result is still a one-shot storyboard: object identities are sometimes generated from the wall clock, the browser DOM effectively owns the scene, and an interrupted lesson can play visual steps that the learner has already superseded.

This plan delivers Gate 0 of the real-time visual-teacher direction. A developer will be able to open a deterministic Pythagorean teaching replay, watch a semantic board state evolve through stable object identities, interrupt it while motion is active, and ask for a focused explanation without stale future steps appearing. The prototype deliberately uses hand-authored scene states. It does not add an LLM streaming format, change the Voice V2 event protocol, or publish a reusable package. Those later decisions depend on this visual/runtime proof.

In this plan, a **scene document** means the committed semantic description of what belongs on the board at one revision. A **motion plan** is the deterministic set of creates, updates, removals, focus changes, and presentation hints needed to move from one scene document to the next. An **interruption** stops current and scheduled presentation work while retaining all elements that have already become visible.

## Progress

- [x] 2026-09-02 01:17 IST: Read `.agent/PLANS.md`, inspected the current SDL/compiler/renderer/interruption paths, and confirmed the existing dirty voice checkout must remain untouched.
- [x] 2026-09-02 01:17 IST: Created clean worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` on branch `codex/realtime-scene-core` from clean `main` at `471bbe0`.
- [x] 2026-09-02 01:28 IST: Separated normal SDL completion from interruption, added canvas-owned motion cancellation, and pushed regression coverage in commit `73a4103`.
- [x] 2026-09-02 01:36 IST: Added the pure TypeScript scene document, validation, deterministic scene-to-scene planner, stable semantic IDs, and replay tests in commit `7cc0c13`.
- [x] 2026-09-02 02:02 IST: Added the SVG/GSAP motion-plan executor with explicit playback outcomes, materialized-step reporting, rollback-safe cancellation, stable in-place transforms, and CSS-variable-safe emphasis.
- [x] 2026-09-02 02:02 IST: Added the hand-authored Pythagorean prototype with normal completion, manual and 700 ms automatic interruption, focused-question branching, accepted-revision replay, and synchronous reset.
- [x] 2026-09-02 02:13 IST: Passed 41 focused tests and the full frontend check (lint, typecheck, 318 tests, and production build), then inspected normal completion, interruption, replay, rapid reset/interruption, desktop, and 375 px mobile behavior in a real browser.
- [x] 2026-09-02 02:07 IST: Recorded the measured outcomes, remaining limitations, and a passing Gate 0 verdict below.

## Surprises & Discoveries

- The current legacy completion callback has no completion reason. `web/src/hooks/use-webrtc.ts` routes `tts_interrupted` through `onSDLComplete`, while both canvas pages play every timeline that has not started. An interruption can therefore reveal future explanation steps instead of invalidating them.
- `createTeachingTimeline` schedules callbacks that call `render`, and `render` starts independent GSAP tweens. Killing the parent teaching timeline prevents future callbacks but does not necessarily stop a stroke or pulse already running. Canvas-level tween ownership is required for honest interruption behavior.
- Several semantic component renderers use `Date.now()` to create child IDs. The same SDL compiled twice can therefore produce different render commands, preventing deterministic replay and stable morph targets.
- The Voice V2 envelope and reducer already define task generations, canvas revisions, apply acknowledgements, first-visible events, and animation completion. Gate 0 should not redesign that protocol; it should produce a scene core that can be connected to it later.
- Cancelling an animation is not enough to keep the semantic scene and visible DOM consistent. The executor must return the exact step IDs that became materialized, while interrupted updates and removals need cancellation-specific rollback instead of normal completion finalizers.
- GSAP's color interpolator cannot parse unresolved CSS custom-property expressions such as `hsl(var(--amber))`. The real-browser run exposed a repeated `splitColor` failure that unit tests had missed; resolving the variables to concrete browser colors before starting the emphasis tween removed the exception.
- A synchronous reset is important even when the exit effect is visually desirable. An asynchronous fade-out can race the next playback and later remove newly-created nodes with the same semantic IDs.

## Decision Log

- 2026-09-02, Codex: Keep Gate 0 frontend-only and hand-authored. This isolates the product hypothesis—whether a semantic scene plus an interruptible browser renderer feels like live teaching—from model-format and transport uncertainty.
- 2026-09-02, Codex: Build library-shaped internal modules but no public package, renderer abstraction, or external compatibility layer. A second consumer and stable product evidence are required before extraction.
- 2026-09-02, Codex: Treat the scene document as client-derived state only. Do not put it on the wire or into durable storage in Gate 0 because that would prematurely freeze the most expensive contract.
- 2026-09-02, Codex: Reuse SVG and GSAP. Gate 0 must explicitly fail if this substrate cannot achieve convincing visual continuity at reasonable complexity; switching renderers is not hidden inside this plan.
- 2026-09-02, Codex: Separate normal sequence completion from interruption instead of weakening normal completion semantics. Interruption pauses/kills owned animation and retains visible DOM; normal completion may finish its planned sequence.
- 2026-09-02, Codex: Define cancellation as retaining only actually materialized steps. Enters become materialized when their visible node is installed; compatible transforms apply in place; incomplete crossfades and removals roll back to their previous node. The resulting partial scene is committed as an accepted revision before branching.
- 2026-09-02, Codex: Keep reset synchronous and replay from accepted scene documents rather than recorded animation commands. This makes rapid reset/play deterministic and lets replay re-plan motion while preserving semantic identity.
- 2026-09-02, Codex: Pass Gate 0 and proceed to Gate 1 model authorship. SVG plus GSAP produced a credible board experience at desktop and narrow viewports without requiring a video renderer, so the next uncertainty is incremental model output and validation rather than rendering feasibility.

## Outcomes & Retrospective

Gate 0 passed. The branch now contains a small immutable scene model, deterministic transition planner, concrete SVG motion executor, and a Pythagorean live-scene lab. The normal branch progressively constructs the triangle and then reveals `a² + b² = c²`. The interruption branch cancels the queued theorem, materializes only the ink that was actually installed, fills missing foundational geometry, and evolves the retained board into a focused right-angle explanation. Replay re-plans the accepted revisions and reaches the same semantic object set.

The focused validation finished with 41 passing tests. The complete frontend `npm run check` passed lint, generated route/type checks, all 318 unit tests, and a Next.js production build. Browser inspection covered 1280×900 and 375×812 viewports. Automatic interruption remained theorem-free beyond the original reveal deadline; immediate reset → play → manual interrupt produced no duplicate semantic IDs; replay reproduced the same 12-node interrupted scene; and the final post-fix browser runs recorded zero application errors. The browser pass also caught and drove the CSS-variable emphasis fix that the simulated DOM did not expose.

The remaining limitations are intentional. Scene revisions are still hand-authored, the format is not streamed or persisted, no model validity/repair loop exists, no latency budget has been measured, and the demo is not connected to the voice generation protocol. The primitive vocabulary is deliberately small and SVG/GSAP has only one real lesson as evidence. Gate 1 should therefore add bounded incremental scene-patch authorship and evaluation without changing the renderer substrate or coupling it to voice yet.

## Context and Orientation

`web/src/lib/scene-kit/` is the existing semantic compiler. `types.ts` describes the SDL that the model emits, `compiler.ts` turns each SDL step into low-level render commands, `layout.ts` resolves simple relative positions, and `components/` expands semantic objects such as equations and right triangles.

`web/src/components/svg-canvas.tsx` is the concrete browser renderer. It creates SVG and Rough.js elements, stores them in `elementsRef`, and starts GSAP animations. Its public imperative surface is `SVGCanvasHandle` in `web/src/features/canvas/types.ts`. `web/src/features/canvas/timeline.ts` converts teaching steps into paused GSAP timelines.

`web/src/hooks/use-webrtc.ts` decodes legacy voice data-channel messages and invokes SDL lifecycle callbacks. `web/src/app/(app)/canvas/page.tsx` and `web/src/app/(app)/session/[agentId]/page.tsx` compile complete SDL scenes, create per-step timelines, and play them when audio starts. The session page reaches the legacy voice hook through `web/src/features/voice/session-runtime-controller.tsx`.

The prototype will live under an explicit development/evaluation route and will use the production SVG renderer without requiring voice, authentication state, or a backend. Pure scene state and planning logic will live under `web/src/lib/live-scene/`; concrete React and canvas integration will live under `web/src/features/live-scene/`. This keeps the semantic core free of React, DOM, GSAP, voice, transport, and provider dependencies.

## Plan of Work

First, repair interruption semantics independently of the new scene model. Add an interruption-specific callback to the legacy voice hook and session controller. Centralize per-sequence timeline cleanup so normal completion and cancellation cannot accidentally share behavior. Add canvas-handle methods that pause and cancel canvas-owned timelines and descendant tweens without deleting visible SVG elements. Update both legacy canvas consumers and add focused tests for unstarted and already-running animations.

Second, introduce a closed, minimal scene model for the prototype. The initial node vocabulary will cover the existing renderer primitives needed by the Pythagorean replay: line/path geometry, rectangles, text, and LaTeX equations. Each node has a caller-supplied semantic ID. A pure planner will compare two revisions and emit a stable motion plan containing create, update/replace, remove, emphasize, and camera-focus work in deterministic order. Repeated planning from the same inputs must produce deeply equal output.

Third, add a concrete motion-plan executor to `SVGCanvas`. It will create prepared SVG nodes, apply presentation transitions through canvas-owned GSAP animations, replace changed text or equations without changing semantic IDs, move compatible nodes where possible, remove deleted nodes, and focus the viewport. Pause and cancellation will operate over all animation objects owned by the canvas, including tweens created inside scheduled callbacks. Cancellation will never clear or rewind the committed scene.

Fourth, build a hand-authored Pythagorean replay. The lesson will progressively create a right triangle and its semantic subparts, introduce area/equation objects, and demonstrate a revision rather than a redraw. The route will expose play, interrupt, answer-the-interruption, resume/replay, and reset controls together with the current committed revision. The interruption branch will focus the right-angle object and add an explanation while stale future lesson frames remain cancelled.

Finally, validate pure determinism, renderer behavior, interruption cleanup, and the user-visible route. Visual inspection must judge whether the experience feels like a teacher working at a board; passing type checks is necessary but not sufficient. If the visual result is clearly limited by SVG/Rough.js/GSAP rather than the implementation, record Gate 0 as failed and do not proceed to an LLM streaming protocol.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` unless a command explicitly changes directory.

Inspect the working tree before and after each milestone:

    git status --short
    git diff --check

Run focused frontend tests while implementing:

    cd web
    npm test -- --run src/lib/live-scene src/features/live-scene src/features/canvas src/features/voice/session-runtime-controller.test.tsx

Run the complete frontend static and unit gates:

    cd web
    npm run lint
    npm run typecheck
    npm test

Run a production build when the required local environment permits it:

    cd web
    npm run build

Start the frontend for visual inspection:

    cd web
    npm run dev

Open the documented prototype route, play the lesson, interrupt it mid-stroke, invoke the focused right-angle explanation, and confirm that no later equation or shape from the cancelled branch appears.

## Validation and Acceptance

The plan is accepted only when all of the following are demonstrated.

The same pair of scene documents produces a deeply equal motion plan across repeated runs, and all Pythagorean scene IDs remain stable across every revision. A changed equation or moved object is planned as an update/replace against the same ID rather than as unrelated deletion and creation.

Normal SDL completion and interruption have different tested behavior. Normal completion retains its existing ability to finish pending work. Interruption pauses or kills running work, kills unstarted timelines, and never invokes `play` for a future step. Already-visible SVG elements remain on the board.

The prototype progressively renders at least one geometric construction, one equation, one semantic highlight/focus, and one scene revision. Interrupting it prevents every later frame from the original branch. Applying the interruption response evolves the retained board rather than clearing it.

Focused tests, full frontend type checking, lint, and unit tests pass. The production build either passes or its environment-specific failure is recorded with evidence and a narrower successful validation. Browser inspection confirms controls, animation, retained state, and interruption behavior at both a desktop viewport and a narrow viewport.

Gate 0 passes only if the resulting replay is visually and behaviorally credible enough to justify Gate 1 model-authorship work. The numerical latency and model-validity targets discussed for later gates are explicitly not acceptance criteria for this frontend-only prototype.

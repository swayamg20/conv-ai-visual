# Turn the Pythagorean construction into an adaptive visual proof

## Purpose / Big Picture

Gate 1.3 lets a signed-in learner progressively draw, interrupt, continue, and replay one verified Pythagorean construction. It still stops at a labeled picture: the board states `a² + b² = c²` but does not visually establish why the areas are equal, and it reports provider timing rather than the moment verified ink actually settles in the browser.

Gate 1.4 makes that interaction a proof. A learner first presents the familiar triangle and three side squares. When they follow up with a question such as “I do not understand why the areas are equal,” Murmur preserves the accepted board and appends a deterministic altitude-projection dissection of the `c²` square. The altitude divides the hypotenuse into lengths `a²/c` and `b²/c`; extending that division across the square creates two regions with areas `a²` and `b²`. Each new mark is still one server-owned, independently verified visual atom, so interruption and exact replay retain their existing trust boundary.

The product also exposes **presentation latency**, meaning elapsed time from the learner's submit action until verified ink crosses the browser's post-paint settlement barrier. This is separate from server/provider timing. The browser records first-presented, fully-settled, interruption-settlement, and replay duration without claiming persistent telemetry or making a network request during Replay.

This gate remains one Pythagorean component, text-directed, ephemeral to the mounted browser, and voice-independent. It does not add arbitrary model-authored geometry, cross-session persistence, new subjects, or paid live-model evaluation.

## Progress

- [x] 2026-09-06 04:05 IST: Merged Gate 1.3 qualification record through PR #28 at `303b7c0` and created clean branch `codex/gate14-adaptive-visual-proof` without touching the dirty voice worktree.
- [x] 2026-09-06 04:09 IST: Audited the compiler, verifier, router, browser runtime, SVG executor, fixture, and browser suite; selected an eight-atom altitude dissection that preserves the one-put/one-presentation invariant and existing per-generation limit.
- [x] 2026-09-06 04:19 IST: Extended the server-owned contract, compiler, verifier, router, and narration through an independently checked eight-atom altitude-projection proof in `47e725a`; 328 focused backend tests and both Ruff checks passed.
- [x] 2026-09-06 04:25 IST: Mirrored the versioned sixteen-role contract in the browser and split the compiler-certified fixture into two bounded eight-atom turns in `dd6048a`; 32 focused semantic, fixture, and live-model tests passed.
- [x] 2026-09-06 04:35 IST: Added immutable browser presentation metrics and truthful server/browser diagnostic boundaries in `2e8819d` and `95c0d38`; 43 focused tests, lint, and type-check passed after independent race review.
- [x] 2026-09-06 04:49 IST: Added zero-cost browser evidence for adaptation, interruption, suffix continuation, stale-output rejection, exact canonical Replay, and full-proof 320/375px layouts in `5ae73ee`, `abc0fb8`, and `aea0ca3`; the provider-free suite passed 15/15 and the targeted narrow suite passed 2/2.
- [x] 2026-09-06 05:12 IST: Qualified pushed head `eaf54c1`: 1,683 backend tests, 470 frontend tests, Ruff, formatting, lint, type-check, production build, and 15 Playwright scenarios passed; independent re-review confirmed both discovered blockers closed with no remaining material findings.
- [x] 2026-09-06 05:23 IST: Merged PR #29 as `0e1c106`; all five required jobs on merged `main` passed in CI run `33999674729` (backend, frontend, verified scene, offline RTC, and Pipecat RTC).

## Surprises & Discoveries

- The existing semantic runtime intentionally accepts only one new node per atom. This is an advantage for Gate 1.4: an altitude-projection proof can be expressed as eight independently meaningful additions, so no update/remove protocol or multi-node atomic receipt is required.
- The low-level motion executor already provides draw, fade, and scale entrances. A proof can feel progressive and responsive without weakening canonical replay by introducing transient, unrecorded highlighting.
- `SceneStreamCompletedEvent.firstPatchMs` and `totalMs` are server-side production timings. They do not include browser motion or post-paint settlement, so displaying them as user-perceived semantic latency would be incorrect.
- The full semantic role history will grow from eight to sixteen, but the new proof is deliberately reachable only after the eight-role identity boundary. Each request therefore continues to emit at most eight atoms and stays inside the existing completion-event and queue budgets.
- Generic `LineSceneNode` marks are rendered through RoughJS without a stable seed, so replaying them can produce different SVG path data even when the canonical scene is unchanged. Proof-critical strokes therefore use deterministic open semantic paths; exact replay remains a browser-observable invariant instead of a state-only claim.
- Raw SVG `innerHTML` can differ after Replay solely because the browser changes attribute insertion order while preserving the same elements, attributes, values, and child order. The browser test therefore canonicalizes only attribute ordering before demanding exact SVG DOM equality; it does not normalize coordinates, paths, styles, or structure.
- At 375px, the canvas zoom/download toolbar originally floated over the proof equation. Rendering those controls in normal flow below the board on touch-sized viewports removes the collision while retaining the desktop hover/focus overlay.
- The fixture's raw animation-frame-to-first-visible p95 was 35.4 ms in the full browser run. This is renderer scheduling evidence, not end-to-end request latency; the visible browser metric correctly reported the much longer request-to-first-presented and request-to-settled durations.
- Extending the closed semantic/router vocabularies grew the conservative dry-run prompts without changing either evaluation corpus. Exact reserved input moved to 177,456 tokens / $0.075770400 for the semantic probe and 119,112 / $0.042442800 for the router probe; the pinned dry-run tests and human router ceiling now reflect those deterministic bounds, with no provider request made.
- Independent pre-merge review found that the first proof draft skipped cross-group label-collision checks while issuing an unscoped `LABEL_SEPARATION` receipt, and that direct compiler callers could construct a non-wireable sixteen-atom beat. Moving the Region B label and enforcing the shared eight-atom cap in both compiler and contract closed those trust-boundary gaps before PR creation.

## Decision Log

- 2026-09-06, Codex: Use the altitude-projection dissection rather than a decorative second diagram or a simulated piece rearrangement. It is a real proof, fits the current board, and remains honest under interruption after every atom.
- 2026-09-06, Codex: Preserve the first eight Gate 1.3 roles as the exact prefix and append eight proof roles. Existing accepted-state semantics remain monotonic and the follow-up is visibly additive.
- 2026-09-06, Codex: Represent adaptive intent with the existing closed `continue_visual` decision targeting a new `proof` stage. A second model-authored response-mode vocabulary would add no behavior in this gate; the server-owned stage already captures the only supported reframe.
- 2026-09-06, Codex: Require an accepted identity before the proof stage. A direct empty-board proof request is routed through the identity boundary first, ensuring no generation exceeds eight atoms.
- 2026-09-06, Codex: Keep browser presentation metrics separate from server completion metrics. Measure only at synchronous submit, verified post-paint acceptance, settled interruption, and replay completion boundaries.
- 2026-09-06, Codex: No paid Azure request is authorized by this plan. Provider-free contract, service, component, and browser evidence must fully qualify the implementation; a later live model smoke requires a new explicit dollar cap.
- 2026-09-06, Codex: Encode the altitude, partition, and concluding emphasis as two-point open `PathSceneNode` marks. This keeps the change local to the verified proof vocabulary and avoids changing the visual character of every existing rough-drawn canvas line merely to make this proof replay deterministic.
- 2026-09-06, Codex: Define exact Replay as equality of the canonical SVG DOM and semantic frontier, with attribute order treated as non-semantic browser serialization detail. All attribute values, node order, paths, styles, and semantic receipts remain strict.
- 2026-09-06, Codex: Keep canvas controls below the SVG on mobile and as a hover/focus overlay on desktop. This preserves the drawing area without hiding controls from touch users or covering mathematical content.
- 2026-09-06, Codex: A `LABEL_SEPARATION` receipt means separation from every other verified area label, including labels introduced in an earlier beat. The verifier therefore checks all unordered pairs rather than treating identity and proof labels as separate groups.
- 2026-09-06, Codex: Make the eight-atom generation limit a compiler and data-contract invariant, not merely a routed-service preflight. Full proof construction is always represented and tested as an eight-atom identity beat followed by an eight-atom proof beat with one contiguous certificate chain.

## Outcomes & Retrospective

Gate 1.4 is complete. PR #29 merged as `0e1c106`, and all five required jobs on the resulting `main` passed in CI run `33999674729`. Before merge, the final local suite passed 1,683 backend tests, 470 frontend tests, and 15 provider-free Playwright scenarios; Ruff, formatting, lint, type-check, the CI-equivalent production build, and diff checks also passed. The final browser run measured complete-patch-frame to first-visible p95 at 33.6 ms. Fresh desktop and 375px evidence show the five labels separated, the proof regions contained, and mobile controls below the board. Independent review initially found two material verifier/compiler boundary issues; `eaf54c1` fixed both and the re-review returned PASS with no material findings. No paid Azure request was made.

The shipped boundary remains deliberate: this is one text-directed, ephemeral Pythagorean proof with a closed server-owned vocabulary. Arbitrary subjects, persistent scenes, voice synchronization, and paid live-model quality qualification belong to later gates.

## Context and Orientation

`backend/murmur/live_scene/semantic_contracts.py` defines the model-visible Pythagorean stages and the server-owned ordered role prefix. `visual_act_router.py` resolves a model's small `start_visual`, `continue_visual`, or `abstain` decision against the accepted semantic scene. `visual_act_lowering.py` turns the resolved target into server-owned narration and a teaching beat. `semantic_compiler.py` owns every coordinate, style, label, equation, node ID, and patch; `semantic_verifier.py` independently parses the serialized nodes and issues structural receipts only after checking the geometry. `service.py` preflights the entire missing suffix, validates the certificate chain, then streams one atom at a time.

The first eight roles form the existing identity construction. Gate 1.4 appends `altitude`, `partition`, `region_a`, `region_a_label`, `region_b`, `region_b_label`, `projection_identity`, and `proof_conclusion`. The altitude meets the hypotenuse at `H`; its continuation crosses the outer edge of the `c²` square at `K`. The quadrilaterals on either side of `HK` exactly cover that square. Similarity gives `AH = a²/c` and `HB = b²/c`, so the two region areas are `c·AH = a²` and `c·HB = b²`.

`web/src/lib/live-scene/semantic.ts` independently decodes the server's semantic events and applies only exact certificate-bound transitions. `web/src/features/live-scene/stream-runtime.ts` queues motion, accepts an atom only after the renderer's presentation barrier, owns interruption, and replays the paired low-level/semantic ledger without calling its stream runner. A small `runtime-presentation-metrics.ts` module will own timing state so the already-large runtime remains focused. `model-scene-demo.tsx` renders controls, the board, trust-boundary evidence, and diagnostics. `semantic-scene-stream-fixture.ts` replays a checked-in backend-generated transcript for zero-cost browser tests, and `web/e2e/live-scene.spec.ts` supplies action-level evidence.

## Plan of Work

First, extend the backend contracts with a `proof` stage and the eight ordered proof roles while retaining the existing compiler version's trust semantics through an explicit version bump. Keep the maximum compiled suffix at eight atoms rather than equating it with total role history. The router must never start directly at proof; from an empty scene, a “why” request stops at identity, and only a follow-up on the accepted identity may target proof. Lower the proof target into exact server-owned explanatory narration.

Second, add deterministic altitude, partition, colored region overlays, contained labels, a projection identity, and the concluding equality. The verifier must independently derive the altitude foot and far-edge intersection from the accepted triangle and `c²` square, prove perpendicularity, prove the two quadrilaterals are bounded/non-overlapping and cover the square, compare their areas to the leg-square areas, and validate exact labels and equations. It must reject corrupt coordinates, areas, IDs, order, labels, or equations before any atom is emitted.

Third, mirror the version, stages, roles, and obligations in the TypeScript decoder. Regenerate the golden transcript as two certificate-chained eight-atom beats: identity from the empty scene and proof from the exact identity frontier. The fixture runner will stop at identity on the first request and emit only the missing proof suffix on the next, preserving exact base matching and the per-generation limit.

Fourth, add a pure browser presentation-metrics state machine with an injectable monotonic clock. Integrate it into the runtime at submit, first post-paint semantic acceptance, true terminal settlement after animations drain, interruption click and settlement, and Replay start/settlement. Reset and stale callbacks must not mutate a newer measurement. Present these values as browser-observed timings and retain server metrics under a distinct diagnostic label.

Finally, extend browser coverage around the real interaction: first turn reaches identity, a learner follow-up reaches proof without wiping the first frontier, interruption during proof retains the exact atom prefix, another follow-up resumes only the missing suffix, and Replay produces identical board markup/frontier with an action-bracketed request count that does not increase. Run all tests, review the complete diff independently, fix material findings, create and merge a pull request, and verify `main` CI.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product` unless noted.

After each backend slice:

    uv run pytest -q tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py tests/test_live_scene_semantic_integrity.py tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_lowering.py tests/test_live_scene_routed_semantic_service.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_lowering.py tests/test_live_scene_routed_semantic_service.py
    uv run ruff format --check backend/murmur/live_scene tests

After each frontend slice, from `web/`:

    npm test -- src/lib/live-scene/semantic.test.ts src/features/live-scene/semantic-scene-stream-fixture.test.ts src/features/live-scene/semantic-stream-runtime.test.ts src/features/live-scene/model-scene-demo.test.tsx
    npm run lint
    npm run typecheck

Run browser and full gates before review:

    cd web && npm run e2e:scene
    cd web && npm test
    cd web && npm run build
    uv run pytest -q
    uv run ruff check .
    uv run ruff format --check .

Before every commit:

    git diff --check
    git status --short

Push each coherent milestone and prove parity:

    git push -u origin codex/gate14-adaptive-visual-proof
    git rev-parse HEAD
    git rev-parse @{upstream}

## Validation and Acceptance

The proof contract passes when the first eight roles remain the exact Gate 1.3 prefix; identity-to-proof produces exactly eight additional roles; empty-to-proof cannot exceed the per-generation budget; every serialized prefix is valid; and unknown fields, invalid stages, backward targets, extra components, or namespace collisions fail without mutation.

The mathematical verifier passes when it independently proves the altitude is perpendicular to the hypotenuse, the partition reaches the opposite square edge, the two colored quadrilaterals exactly cover `c²` without overlap, their areas equal the original `a²` and `b²` squares, labels are contained, and the projection and conclusion equations are exact. Corrupting any claimed relationship must produce no streamed semantic atom.

The browser runtime passes when request-to-first-presented begins at submit and ends only after a verified atom's post-paint settlement; request-to-settled ends only after the terminal event and all queued motion settle; interruption latency spans click through the presentation barrier; Replay duration is measured independently; stale generations and reset cannot overwrite newer metrics; and server/provider timings remain separately labeled.

The interaction passes when a zero-cost browser run reaches the eight-act identity frontier, the follow-up “I do not understand why the areas are equal; dissect the large square” preserves those nodes and progressively reaches sixteen acts, stopping mid-proof retains only settled atoms, continuation emits the exact remaining suffix, and Replay restores an attribute-order-independent canonical SVG DOM plus the same semantic frontier without increasing the action-bracketed semantic request count. The final board must remain usable at desktop, 375 by 812, and 320 by 568.

Gate 1.4 is done only after focused and full backend tests, frontend tests, Ruff, formatting, lint, type-check, production build, the scene Playwright suite, and independent review pass at the pushed head; its pull request is merged; and the merged `main` checks are green. Live Azure model quality and dollar-backed latency remain a separate, explicitly authorized qualification rather than an implicit part of this merge.

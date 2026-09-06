# Make one live mathematical explanation feel authored, continuous, and interruptible

## Purpose / Big Picture

Gate 1.4 proves that Murmur can stream a mathematically verified Pythagorean proof, stop on a settled visual frontier, resume the exact suffix, and replay without a provider request. Its visual language is still closer to a progressively populated diagram than to a deliberately directed mathematical animation. Objects mostly appear and remain where they were created. A same-kind update called a transform currently replaces the rendered contents and adds a subtle scale tween; changed equations crossfade as monolithic LaTeX blocks; camera movement and emphasis are outside the replay ledger. Those mechanics are safe, but they do not yet communicate one idea becoming another.

Gate 1.5, called Live Visual Choreography, will prove a different product capability: Murmur can construct one original, shareable mathematical explanation whose motion carries the reasoning. The quality reference is the continuity, focus, pacing, and conceptual clarity associated with excellent mathematical animation, including work such as 3Blue1Brown, without copying another creator's palette, compositions, assets, scenes, or branding. Murmur's advantage must be native to a live medium: the learner can stop the explanation, ask one supported clarification, and continue from the exact visible state instead of restarting a prerecorded sequence.

The flagship explains why completing the square works by solving:

    x² + 6x = 7

It uses eight settled main checkpoints:

1. Pose the equation as a visual problem.
2. Materialize x² as an x by x square and 6x as two already-identifiable 3x strips.
3. Reveal that the two strips split the linear term equally.
4. Move those same strip objects onto adjacent sides of the x² square.
5. Focus on the missing 3 by 3 corner.
6. Add 9 to both sides and fill the corner, producing 16 on the right.
7. Transform the completed geometry and tokenized equation into (x + 3)² = 16.
8. Solve x + 3 = ±4 and then x = 1 or x = -7, while stating honestly that the area picture visualizes the nonnegative-length branch and the algebra recovers both roots.

There is one bounded adaptive detour. If the learner stops at the missing-corner checkpoint and asks, “Why is that corner 9?”, Murmur keeps the board, moves focus to the corner, reveals both length-3 dimensions and 3 × 3 = 9, then returns to the remaining main checkpoints. This is not a general clarification graph. It is the smallest proof that live re-choreography can preserve context and respond to an interruption.

At normal speed the main explanation should last between 60 and 90 seconds. Routine tests use an accelerated deterministic clock, while one real-speed run produces the artifact reviewed for pacing and motion quality. The first meaningful change still begins quickly; cinematic pacing must never be confused with request latency.

The gate is intentionally narrow. It does not build a general Manim replacement, arbitrary-topic animation generation, voice synchronization, cross-session persistence, multiplayer canvas state, unrestricted SVG morphing, or 4K export. It builds one reusable choreography contract and one high-quality renderer path, then proves them through a single demanding lesson.

## Progress

- [x] 2026-09-06 23:11 IST: Audited the merged Gate 1.4 compiler, verifier, semantic service, browser runtime, motion executor, viewport, metrics, and Playwright proof; selected completing the square as the flagship and checkpoint-level presentation as the core abstraction.
- [x] 2026-09-06 23:22 IST: Wrote the self-contained Gate 1.5 execution plan and passed independent backend, frontend/runtime, and visual-quality review after resolving interruption, trust-boundary, responsive-camera, evidence-provenance, and CI-artifact findings.
- [ ] Define and test the versioned backend checkpoint, choreography, certificate, router, and independent verification contracts while preserving the complete Gate 1.4 path.
- [ ] Compile all eight completing-square checkpoints and the bounded missing-corner clarification with stable object identities, exact mathematics, server-owned layout, and deterministic choreography.
- [ ] Implement true compatible geometry/token transforms, camera focus, deterministic emphasis, authored phase timing, atomic settlement, reduced motion, and exact Replay in the browser.
- [ ] Ship an uncluttered stage-only capture surface plus accelerated interruption/replay tests, a real-speed video, a settled-checkpoint contact sheet, and a machine-readable evidence manifest.
- [ ] Complete independent engineering and visual review, meet the human quality rubric, merge the pull request, and verify the resulting main branch checks.

## Surprises & Discoveries

- Gate 1.4's semantic atom is structurally one new role, one fresh node, and one enter motion. The compiler, service admission, browser decoder, and presentation receipt all enforce that assumption. True choreography cannot be added only inside GSAP; it requires an additive checkpoint contract that can update several existing component-owned nodes atomically.
- The browser planner already distinguishes enter, update, and remove, retains stable IDs, and can materialize a partially applied plan. The current executor's update named transform does not interpolate geometry. It replaces an element's children immediately and animates scale from 0.98 to 1, so the visual geometry jumps even though the outer DOM ID survives.
- The motion executor already has valuable transactional machinery: commit, rollback, cancellation, queue ownership, cleanup, and a two-animation-frame presentation barrier. Gate 1.5 should retain these invariants and change the unit of work from a node motion plan to a checkpoint choreography.
- Camera state currently lives in the canvas viewport hook rather than canonical scene or presentation state. Cancelling its existing tween can leave the SVG viewBox between positions while the React ref points at the destination. A certified presentation checkpoint must therefore own its terminal viewport.
- Emphasis is currently a fire-and-forget color pulse. It is neither certified nor replayed. In this gate, emphasis becomes a bounded cue in the choreography plan and must remove all transient residue on completion or interruption.
- RoughJS line and rectangle output is not byte-deterministic without an explicit seed. Moving flagship pieces will use direct, equal-topology semantic paths or seeded primitives so true interpolation and exact Replay do not depend on regenerated rough paths.
- A LaTeX node is currently one 500 by 120 foreignObject. Meaningful equation continuity requires a small set of stable, individually identified equation tokens for this lesson, not a general symbolic-LaTeX morphing engine.
- The existing final-state screenshots prove layout after animation, not motion craft. They also include a debug-heavy lab shell. Gate 1.5 needs a stage-only real-speed recording and settled-checkpoint contact sheet so reviewers judge the explanation rather than the development controls.
- The current browser runtime allows one active playback plus as many as eight queued items. The eight-checkpoint generation limit is retained as an intentional service and evaluation bound, not because the browser queue requires it. The clarification detour is requested only after interruption and therefore begins a later generation rather than increasing the initial burst.

## Decision Log

- 2026-09-06, Codex: Optimize Gate 1.5 for one exceptional explanation rather than more supported subjects or primitives. A flagship that exposes weak continuity is a stronger engine test than a broad catalog of static diagrams.
- 2026-09-06, Codex: Use x² + 6x = 7. Splitting 6x into two 3x strips, supplying a 3 by 3 corner, reaching 16, and then recovering two algebraic roots creates a coherent visual story while exercising geometry, token motion, focus, and mathematical domain honesty.
- 2026-09-06, Codex: Treat one semantic revision as one settled checkpoint, not one newly entered node. A checkpoint may contain several ordered put/remove operations and one certified choreography plan, but it is acknowledged as a whole only after its terminal scene and viewport cross the browser presentation barrier.
- 2026-09-06, Codex: Give each V1 checkpoint exactly one visible motion phase plus an optional reading hold. This prevents a stop during early motion from revealing later unseen sub-phases. More complex sequences are expressed as additional semantic checkpoints.
- 2026-09-06, Codex: On interruption, never serialize arbitrary mid-tween CSS or SVG state. If the first cue has not crossed a browser presentation barrier, retain the previous checkpoint. Once that first cue has been presented, immediately materialize and present the target checkpoint. Reject all later events from that generation.
- 2026-09-06, Codex: Add a V2 compiler certificate for choreographed checkpoints instead of silently changing the V1 Pythagorean certificate. V2 binds the exact base and result scenes, checkpoint identity, generalized verification receipt, choreography digest, and prior certificate.
- 2026-09-06, Codex: Keep coordinates, node IDs, equations, narration, durations, easing, camera, and choreography server-owned. A model may only choose a closed component/stage or the one supported clarification. It never emits raw coordinates, unrestricted animation properties, or timing.
- 2026-09-06, Codex: Tokenize only the flagship equations. Preserve stable IDs for terms that truly correspond, move unchanged tokens with FLIP/attribute interpolation, and allow a certified crossfade only when mathematical syntax genuinely introduces or replaces a token. Do not attempt arbitrary LaTeX AST morphing in this gate.
- 2026-09-06, Codex: Use a single parallel cue phase plus an optional hold as the V1 choreography DSL. Durations, holds, easing names, target counts, and total plan duration are strictly bounded.
- 2026-09-06, Codex: Make focus deterministic through certified base and result viewport maps keyed by the closed layout classes cinematic and compact. The browser selects and locks one class for the session; the independent verifier checks every named target against each certified viewBox and its safe padding.
- 2026-09-06, Codex: Support exactly one atomic missing-corner clarification checkpoint, legal only when the main frontier is missing_corner and not previously clarified. It proves live adaptation without turning the component state into an arbitrary branching lesson graph.
- 2026-09-06, Codex: Qualify visual quality with a real-speed video and human rubric in addition to structural tests. Pixel-perfect screenshots are not the quality oracle, and an end-state screenshot cannot prove continuity or pacing.
- 2026-09-06, Codex: Keep Gate 1.5 offline and voice-independent. No Azure request or paid provider evaluation is authorized by this plan; any later live-model qualification requires a fresh explicit dollar cap.

## Outcomes & Retrospective

No implementation has shipped yet. This section will be updated after every milestone with the pushed commit, measured evidence, independent findings, and any scope or architectural changes. At completion it must state the merged commit, CI run, real-speed artifact hashes, rubric result, and remaining product boundary.

## Context and Orientation

All work for this gate happens in the clean visual worktree:

    /Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product

The implementation branch is:

    codex/gate15-live-visual-choreography

The separate worktree at /Users/swayam.gupta/Documents/GitHub/conv-ai-visual contains ongoing voice work on codex/pipecat-relay-b0. This plan must not edit, clean, switch, commit, or merge that worktree.

The backend live-scene protocol begins in backend/murmur/live_scene/contracts.py. It defines an 800 by 600 canonical board, immutable scene snapshots, put/remove patches, and bounded streaming events. Put already means insert or replace by stable ID, so the low-level patch format can express checkpoint transitions without adding unrestricted animation data to the raw model-authored protocol.

backend/murmur/live_scene/semantic_contracts.py defines the current Pythagorean-only semantic vocabulary, ordered role prefix, verifier receipts, compiler certificates, and compiled atoms. semantic_compiler.py creates exact server-owned nodes; semantic_verifier.py independently validates their geometry; semantic_integrity.py hashes the contracts; semantic_service_contracts.py and semantic_wire.py place them on the wire. service.py preflights the entire semantic batch before emitting atoms, but its present admission logic assumes every atom adds one globally fresh node. visual_act_router.py, visual_act_lowering.py, and semantic_prompt.py constrain the model-visible decision and turn it into the compiler input.

The frontend mirrors low-level scene contracts in web/src/lib/live-scene/types.ts, patch.ts, state.ts, and planner.ts. planSceneTransition already produces deterministic enter, update, and remove steps from two consecutive snapshots. web/src/lib/live-scene/semantic.ts independently decodes semantic events and currently requires exactly one enter motion for the expected new role.

web/src/features/live-scene/stream-runtime.ts owns generation tokens, provisional and presented state, the motion queue, post-paint acknowledgement, interruption, stale-event rejection, and the Replay ledger. It is already large and must remain the lifecycle authority rather than gaining low-level animation math. web/src/features/live-scene/svg-motion-executor.ts owns DOM mutation and transactional motion. web/src/components/svg-canvas.tsx exposes it through SVGCanvasHandle and owns viewport controls through web/src/features/canvas/viewport.ts. model-scene-demo.tsx is the existing debug/product demonstration shell.

The existing zero-cost semantic transcript lives at web/src/features/live-scene/fixtures/pythagorean-area-identity.v1.json. web/e2e/live-scene.spec.ts proves Gate 1.4 behavior and writes artifacts under var/scene-e2e. web/playwright.scene.config.ts and the Verified scene browser proof job in .github/workflows/ci.yml run that suite and upload its artifacts.

For Gate 1.5, a main checkpoint is a mathematically meaningful terminal state. Each checkpoint has exactly one visible motion phase plus an optional reading hold. A cue is a closed instruction such as transform these known targets, draw this newly added target, emphasize these targets, or focus the viewport. Scene state remains mathematical/canonical; presentation checkpoint state contains the routed-only caption, certified base/result viewport maps, and transient-free visual requirements; choreography describes how the browser moves from one to the other.

The stable flagship object vocabulary should include, at minimum, separate IDs for the x² square, the two 3x strips, the missing/filled corner, the two dimension labels, and the equation tokens that persist across checkpoints. The two strips must exist as separate objects before they move, even while they are visually read as one 6x term. That avoids pretending one DOM object can split into two while still claiming identity preservation.

## Plan of Work

First, introduce a closed, versioned choreography contract without changing the Gate 1.4 wire interpretation. Add backend/murmur/live_scene/choreography_contracts.py for a server-owned RoutedChoreographyBeatV2 compiler input, ChoreographyPlanV1, PresentationCheckpointV1, one parallel cue phase, bounded timing, named easing, target IDs, a routed-only checkpointNarration field, certified base/result viewport maps for cinematic and compact layouts, and canonical hashing. RoutedChoreographyBeatV2 contains only a deterministic beat ID, completing-square component kind and ID, and either a closed target stage or clarify_corner intent. It contains no narration, nodes, coordinates, presentation, or model-authored free text. Cues are a discriminated union of enter, exit, transform, emphasize, and focus. They contain target IDs and approved intent only; canonical source and target geometry come from the base and result scene snapshots. The phase holds one or more parallel cues, one bounded duration, one easing token, and one bounded hold-after duration. The contract rejects multiple phases, arbitrary GSAP fields, CSS selectors, coordinates in node cues, unknown easing strings, duplicate targets, missing targets, unbounded holds, an empty phase, and plans whose total duration exceeds the gate budget.

Second, add a completing-square semantic component alongside the existing Pythagorean component. Keep the current Pythagorean classes, VerificationReceipt, CompiledVisualAtom, SemanticAtomMetadata, serialization, and V1 certificate untouched. Define CompletingSquareStage values setup, split, complete, and solve. Their authoritative prefixes are setup through checkpoints 1–2, split through 1–4, complete through 1–6, and solve through 1–8. The main checkpoint roles are problem, area_model, split_linear_term, rearrange_halves, missing_corner, balance_and_complete, factor_square, and solve_roots. Component state records the last settled main checkpoint and one cornerClarified boolean because the clarification is exactly one atomic checkpoint. Role values and component namespaces must remain unambiguous across component kinds.

Introduce separate V2 types rather than widening the V1 records: CheckpointVerificationReceiptV2, CompiledCheckpointV2, CheckpointSemanticMetadataV2, and their discriminated wire event. A checkpoint patch may contain between one and sixteen ordered operations. It may put a new component-owned ID, update an existing component-owned ID, or remove an existing component-owned ID. It may never touch another component's namespace. The V2 receipt binds the ordered target tuple and independently verified transition obligations. The V2 certificate binds the canonical RoutedChoreographyBeatV2 hash, component kind, checkpoint or clarification ID, full patch, receipt, complete PresentationCheckpointV1, choreography digest, exact low-level base and result scene hashes, semantic base and result hashes, and previous certificate hash. patch.narration must equal the server-owned checkpointNarration rather than one beat-level line. Identical compiler input and base state must produce byte-for-byte identical JSON.

Third, implement the fixed lesson in new focused modules rather than growing the Pythagorean compiler into a multi-subject file. backend/murmur/live_scene/completing_square_compiler.py owns the node vocabulary, exact 800 by 600 layout, token positions, styles, checkpoint patches, viewport maps, cues, timing, captions, and stable IDs. A small generic polygon or arithmetic utility may be shared, but no shared module may contain expected completing-square checkpoint states, equations, dimensions, or compiler transition tables. Add a separate compile_checkpoint_beat entry point that accepts only RoutedChoreographyBeatV2; keep compile_teaching_beat and its TeachingBeatDraft adapter as the V1 path. Common certificate/batch helpers may be reused only when their types preserve that separation.

Use deterministic direct paths for the moving square, strips, and corner. Give equal-topology versions the same point count and winding order at every checkpoint. Preserve each strip's ID from its first reveal through the rearrangement. Add an additive latex_token scene-node kind with explicit x, y, width, height, anchor, LaTeX, and style fields; implement its Python/TypeScript decoding, immutable cloning, rendering, hashing, and old-fixture compatibility. Tokenize the flagship equation into these measured nodes. Retain token IDs only when the symbol has the same mathematical referent; introduce and retire operator or grouping tokens explicitly when syntax changes.

Fourth, build a verifier that shares contracts and generic math utilities but does not import the compiler, compiler-authored layout values, output, or any shared expected-realization table. backend/murmur/live_scene/completing_square_verifier.py reconstructs each serialized base-to-result transition and independently derives namespace ownership, exact checkpoint order, board bounds, safe label/equation boxes, stable strip identity, equal 3x strip dimensions, gap-free/non-overlapping placement around x², a 3 by 3 missing corner, equal +9 operations on both equation sides, the result 16, the factorization (x + 3)², both roots, and the geometry-domain qualification. It also checks that every transform cue refers to an actually changed compatible target, every enter/exit cue matches the patch, every focus target exists, and every certified layout viewBox contains its declared targets with padding. AST tests enforce that the verifier imports neither the compiler nor an expected checkpoint table.

Fifth, revise semantic service admission around whole checkpoint application rather than atom-count shortcuts. Preflight the complete requested suffix on copies of both scene states, apply every multi-operation patch, run independent verification, validate node count after each result, validate certificate continuity, serialize every event under the existing 64 KiB frame ceiling, and only then emit the first checkpoint. A corrupt later checkpoint must therefore yield zero checkpoint events. Continue to cap one generation at eight checkpoints. The empty-to-solve main path is exactly eight. The clarification is requested only after a settled interruption and uses a later generation.

The router remains a tiny trust boundary. A start decision carries only closed componentKind plus targetStage. A continue decision carries an exact componentId and targetStage, with component kind resolved from accepted state. A separate clarify_corner decision carries only the exact componentId. Cross-kind stages, unknown components, backward targets, clarification anywhere except the unclarified missing_corner frontier, and repeated clarification fail closed. The authoritative stage mapping is setup to checkpoints 1–2, split to 1–4, complete to 1–6, and solve to 1–8. visual_act_lowering.py turns only a resolved V2 route into RoutedChoreographyBeatV2 and supplies its deterministic beat ID. Completing-square V2 is routed-only: do not add its directive to the legacy model-authored TeachingBeatDraft, semantic stream parser/adapter, or stream_semantic_events path, and those surfaces must reject attempts to construct or decode the V2 compiler input. The model cannot supply presentation, caption, or mathematical content. The fixture route deterministically recognizes the flagship and clarification prompts without network access. A production provider may later choose the same closed decisions, but it is not evaluated or required here.

Sixth, mirror the choreography and V2 semantic contracts independently in TypeScript. Add web/src/lib/live-scene/choreography.ts for strict decoding, freezing, budgets, relational bindings, and choreography-planner.ts for deterministic checkpoint planning. Preserve the existing Gate 1.4 decoder path exactly. A V2 event is accepted only if its patch materializes the declared result scene, its operation-target receipt agrees, its choreography refers only to allowed scene-diff targets or certified transient cues, its selected viewport variant joins exactly from the prior presented variant, and its certificate chain advances from the paired presented state. As in Gate 1.4, the browser treats the same-origin backend certificate as a server claim: it validates digest syntax, relational fields, target bindings, and chain-head equality, but does not claim compiler authenticity by recomputing cryptographic hashes at runtime. Add fixed Python-to-TypeScript test vectors for canonical patch, presentation-checkpoint, choreography, low-level scene, receipt, and V2 certificate-body serialization, including Unicode, float, and field-order cases; these are compatibility tests, not a browser authenticity boundary.

Seventh, implement actual visual continuity in small frontend modules. Extract element creation, snapshots, deterministic DOM ordering, rollback, and terminal reconciliation from svg-motion-executor.ts into web/src/features/live-scene/svg-node-reconciler.ts. Add choreography-executor.ts to own one GSAP timeline per checkpoint. It interpolates equal-topology path data and safe numeric attributes, uses FLIP-style translation/scale for unchanged text and LaTeX tokens, draws and fades explicit enters/exits, runs deterministic emphasis, and moves the camera. It must never accept arbitrary property bags from the wire.

The executor owns the complete transaction. Define a separate discriminated ChoreographyPlaybackOutcome with completed, cancelled_before_presented, cancelled_to_checkpoint, and failed rather than widening Gate 1.4's shared MotionPlaybackOutcome. On normal completion it reconciles exact canonical node attributes and order, clears transform/dash/filter residue, applies the exact terminal viewport for the locked layout class, and waits for the presentation barrier. The executor records firstCuePresented only after the first content mutation crosses an animation-frame presentation barrier; GSAP onStart or DOM mutation alone is insufficient. On interruption after that receipt it kills the timeline, materializes the entire target checkpoint without animation, clears transient state, applies its terminal viewport, and crosses the same barrier within the stop-latency budget. Before that receipt it rolls back to the prior scene and base viewport. A failed cue rolls back nodes and viewport together.

Extend viewport.ts and SVGCanvasHandle with exact read, animate, materialize, and reset operations for a versioned viewport pose. Each checkpoint binds both base and result poses for cinematic and compact variants, and consecutive checkpoints must join exactly for both. The stage locks its layout class for the full choreography session: cinematic for the 1280 by 720 capture and normal desktop stage, compact for the small-screen stage. CSS may fit the selected SVG responsively, but it may not substitute an uncertified viewBox. Manual pan and zoom are disabled for the full flagship session so they cannot change the next camera base or make Replay nondeterministic. The cinematic variant may begin with a 16:9 crop inside the existing 800 by 600 board, such as viewBox 0 75 800 450, without changing backend geometry bounds.

Eighth, integrate checkpoint playback with the existing stream lifecycle. stream-runtime.ts remains owner of generation tokens, queue limits, stale rejection, provisional state, presented state, narration, and Replay. Put choreography execution behind a narrow renderer method and move any new bookkeeping into a focused choreography-playback helper. The runtime advances both semantic and low-level frontiers only after one whole checkpoint and its selected result viewport receive a post-paint acknowledgement. The ledger stores the exact patch, choreography, presentation checkpoint, certificate, and selected layout class. Replay starts from an empty scene and certified initial viewport, uses the stored checkpoint sequence without a transport call, and reproduces captions, focus, emphasis, checkpoint hashes, node order, and terminal board. A runtime-owned test/evidence trace records cueStarted, firstCuePresented, checkpointSettled, and focus/emphasis cue order using only closed cue IDs; no model-controlled log payload is accepted.

Ninth, create the user-visible flagship and capture surface without expanding the debug shell into another mode matrix. Add web/src/features/live-scene/live-choreography-demo.tsx for the interactive lesson and a stage-only child that contains only the board, minimal caption, and progress affordance. Wire it into the development lab behind the existing lab flag and add a dedicated capture route used only by Playwright. The full debug view may show checkpoint and trust-boundary evidence below the stage, but the recorded frame must exclude form controls, diagnostics, and the act ledger.

Implement the bounded clarification as a separate generation from the missing_corner checkpoint. The learner stops, asks why the corner is 9, and receives one certified corner_detail checkpoint whose single phase reveals both length-3 dimensions and 3 × 3 = 9 while every existing node remains. That checkpoint sets cornerClarified once and is a legal base for balance_and_complete. It is rejected before or after missing_corner and when already presented. No other free-form branch is accepted in this gate; unsupported reframes abstain without mutation.

Tenth, add a deterministic fixture generated from the backend compiler and checked into web/src/features/live-scene/fixtures/completing-the-square.v1.json. Add fixture consistency tests so hand-editing or drift fails. Add a dedicated Playwright suite and config. Routine cases use an accelerated injectable clock. One capture case uses 1x timing and records a stage-only WebM, one PNG per settled checkpoint, a contact-sheet PNG, and a JSON manifest containing the commit SHA, compiler/fixture versions, locked layout class, base/result viewport sequence, checkpoint timings, stable-ID observations, cue trace, interruption cases, first-visible samples, long-frame diagnostics, reduced-motion result, request count, and SHA-256 hashes of generated artifacts.

Add an explicit provider-free Live choreography browser proof job to .github/workflows/ci.yml. It installs Chromium, runs the accelerated choreography suite, runs the 1x capture, validates the evidence manifest, and always uploads var/live-choreography. The required branch and merged-main checks must therefore produce the artifact central to this gate rather than relying on an unrecorded local run. If a human evaluation note exists, the job validates that it names the reviewed implementation commit and manifest digest, that the commit is an ancestor of the tested head, and that no choreography implementation or fixture file changed between the reviewed commit and the note without a newer review. CI generates and validates technical artifacts; it does not generate human scores.

Finally, qualify both correctness and craft. Run focused and full backend/frontend suites, static checks, production build, the existing Gate 1.4 browser suite, the new choreography suite, and independent engineering review. Push the implementation commit and retain its CI run, artifact manifest, and manifest SHA-256. Review that exact real-speed artifact with the product owner and at least two additional viewers. Record the reviewed implementation SHA, CI artifact/run reference, manifest digest, scores, and comprehension answers in a later checked-in evaluation note. If review reveals a blocking issue, fix the implementation, generate a new artifact, and repeat the review before writing the final record. The review-note commit may follow the implementation commit; it must not conceal runtime drift. Then push the final head, open and merge the pull request, and verify all required checks on the resulting main commit.

## Concrete Steps

All commands run from /Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product unless a command begins with cd web.

Before each implementation milestone:

    git status --short --branch
    git rev-parse HEAD

After backend choreography contracts and compiler/verifier changes:

    uv run pytest -q tests/test_live_scene_choreography_contracts.py tests/test_live_scene_completing_square_compiler.py tests/test_live_scene_completing_square_verifier.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_integrity.py
    uv run pytest -q tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_lowering.py tests/test_live_scene_routed_semantic_service.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_choreography_contracts.py tests/test_live_scene_completing_square_compiler.py tests/test_live_scene_completing_square_verifier.py
    uv run ruff format --check backend/murmur/live_scene tests

After frontend contract, planner, executor, viewport, and runtime changes:

    cd web && npm test -- src/lib/live-scene/choreography.test.ts src/lib/live-scene/choreography-planner.test.ts src/features/live-scene/svg-node-reconciler.test.ts src/features/live-scene/choreography-executor.test.ts src/features/live-scene/semantic-stream-runtime.test.ts
    cd web && npm test -- src/features/live-scene/live-choreography-demo.test.tsx src/features/live-scene/semantic-scene-stream-fixture.test.ts
    cd web && npm run lint
    cd web && npm run typecheck

Generate the checked-in fixture only through a repository script with deterministic output. The implementation must add and document the exact script command here before its first use. After generation, run it again into a temporary path and compare SHA-256 values to prove reproducibility.

Run provider-free browser evidence:

    cd web && npm run e2e:scene
    cd web && npm run e2e:choreography

Run and validate the one real-speed capture separately from the accelerated cases:

    cd web && npm run capture:choreography
    cd web && npm run validate:choreography-artifacts

Inspect the video, contact sheet, manifest, and all settled checkpoint screenshots under var/live-choreography before declaring visual review ready. The manifest validator must fail on a missing checkpoint, duplicate ID, unexpected request, absent artifact, hash mismatch, or missing timing sample.

Before every commit:

    git diff --check
    git status --short

Commit and push each coherent milestone to:

    git push -u origin codex/gate15-live-visual-choreography
    git rev-parse HEAD
    git rev-parse @{upstream}

Before opening the pull request, run the complete local gate:

    uv run pytest -q
    uv run ruff check .
    uv run ruff format --check .
    cd web && npm test
    cd web && npm run lint
    cd web && npm run typecheck
    cd web && npm run build
    cd web && npm run e2e:scene
    cd web && npm run e2e:choreography
    cd web && npm run capture:choreography
    cd web && npm run validate:choreography-artifacts
    git diff --check

Record the exact pushed SHA, commands, counts, timing percentiles, artifact hashes, review findings, and fixes in Progress and Outcomes & Retrospective. After merge, verify the required GitHub Actions jobs on the merge commit rather than reporting only branch-local results.

## Validation and Acceptance

The backend contract passes when the existing Pythagorean V1 fixtures, VerificationReceipt, CompiledVisualAtom, SemanticAtomMetadata, certificate, and tests remain byte-compatible; RoutedChoreographyBeatV2 is constructible only from the resolved routed-lowering path and contains no free text or presentation fields; every completing-square stage and checkpoint decodes with exact keys and strict budgets; arbitrary motion properties, raw selectors, coordinates in cues, multiple visible phases, unknown easing, invalid target order, foreign IDs, and oversized timing fail closed; and every prefix or allowed clarification state round-trips immutably. patch.narration must equal the V2 server-authored checkpointNarration, while the legacy TeachingBeatDraft adapter, semantic parser, and stream_semantic_events path must reject completing-square V2 input.

The compiler passes when setup maps to checkpoints 1–2, split to 1–4, complete to 1–6, and solve to 1–8; empty-to-solve emits exactly eight main checkpoints; every resume point from zero through seven emits only the missing suffix; and exactly one corner_detail checkpoint is legal from an unclarified missing_corner frontier. Clarification at every other frontier and repeated clarification must fail without mutation. Every patch remains under sixteen operations and 64 KiB serialized size, stable strip and persistent equation-token IDs survive their transformations, no entropy source is imported, and repeated compilation from the same paired state is byte-identical.

The independent verifier passes when AST checks prove it imports neither a compiler nor a shared expected-checkpoint table, and when it rejects unequal strips, a wrong 3 by 3 corner, gap or overlap in the completed square, unequal balance operations, an incorrect 16, invalid factorization, missing or wrong roots, a false geometry-domain claim, out-of-bounds focus in either layout variant, collisions, incompatible morph cues, or a transition touching foreign nodes. Corrupting any later checkpoint during service preflight must produce zero emitted semantic checkpoint events.

Certificate integrity passes when changing the RoutedChoreographyBeatV2 input or hash, an operation target or order, node value, checkpoint narration, receipt obligation, choreography cue or timing, either layout's base or result viewport, component/checkpoint identity, low-level base or result scene, semantic base or result state, or previous certificate invalidates V2. V1 Pythagorean certificate verification must remain unchanged. Fixed Python-to-TypeScript vectors must agree for canonical routed beat, patch, presentation-checkpoint, choreography, low-level scene, receipt, and V2 certificate-body serialization across field-order, Unicode, and finite-float cases. The browser still treats authenticity as a same-origin server boundary rather than claiming runtime cryptographic verification.

True-motion tests pass when the x² square, both 3x strips, corner, and required persistent equation tokens retain the same canonical DOM elements through their supported moves; samples at 25, 50, and 75 percent lie geometrically between source and destination rather than already at the destination; and required correspondences never silently fall back to crossfade. At those same trajectory samples, moving pieces, tokens, and camera focal targets must remain inside the safe stage with no unintended collision, clipping, or unreadable text. Crossfade is allowed only for the explicitly listed syntax tokens whose mathematical referent changes.

Transactional presentation passes when firstCuePresented is issued only after the first mutated visual crosses an animation-frame presentation boundary; cancellation while that barrier is pending rolls back to the exact prior scene and base viewport; and interruption after that receipt during a move, token morph, camera focus, emphasis, or hold settles to the complete target checkpoint within 150 ms p95 over twenty fixture trials. No checkpoint has a second unseen motion phase, and no incoming/outgoing duplicate, transform, clip path, dash, filter, active tween, or half-updated viewBox remains. No stale event may alter DOM, viewport, caption, semantic state, or ledger during a two-second post-interruption observation window.

Replay passes when it makes zero live-scene requests and reproduces the same checkpoint sequence, stable IDs, canonical SVG node order and attributes, locked layout class, base/result viewport sequence, captions, cue-trace order, semantic frontier, and checkpoint hashes. Reduced-motion mode must materialize those same checkpoints and mathematical states without nonessential tweening.

Latency and pacing pass when complete first-checkpoint frame to first meaningful visible change is below 100 ms p95 over twenty fixture runs; the normal-speed main explanation lasts between 60 and 90 seconds; every hold is explicitly authored and tied to readable narration; and no unexplained visual dead interval exceeds 1.2 seconds. Long-frame and long-task data are recorded in the manifest but are initially diagnostic rather than a brittle shared-runner CI threshold.

Composition passes at the 1280 by 720 capture viewport when every settled checkpoint and every 25, 50, and 75 percent motion/camera sample keeps its declared focal objects inside the safe stage, equations remain readable, labels do not collide, object correspondence is visually traceable, and controls or diagnostics do not enter the recording. At 375 by 812 and 320 by 568, the certified compact viewport must keep the lesson reachable, readable, interruptible, continuable, and free of horizontal page overflow; identical cinematic framing is not required.

The adaptive interaction passes when the learner can stop on the missing-corner checkpoint, ask why the corner is 9, receive exactly one corner_detail checkpoint that re-focuses the existing scene and reveals both length-3 dimensions plus 3 × 3 = 9, and then continue to the same correct final solution without a wipe, duplicated object, skipped certificate, or provider request. The same decision before checkpoint 5, after checkpoint 5 has advanced, or after cornerClarified is true must fail closed.

The human quality gate uses the real-speed stage-only recording from one named reviewed implementation commit. The product owner and at least two additional viewers score conceptual clarity, visual continuity, composition/focus, motion craft, pacing/rhythm, and delight/originality from 1 to 10. The mean across all scores must be at least 8.0, no category median may be below 7.0, and every reviewer must correctly explain why the missing corner is 9 after at most one replay. Any unreadable equation, lost object correspondence, clipping, unexplained jump, broken animation, deceptive mathematical claim, or obvious imitation is blocking regardless of the average. The checked-in evaluation note must reference that implementation SHA and exact manifest digest; CI must fail if relevant runtime or fixture files have drifted since the reviewed commit.

Gate 1.5 is done only when a named pushed implementation commit and its dedicated CI job produce the stage-only real-speed WebM, settled-checkpoint contact sheet, and valid evidence manifest; all deterministic contract, verification, stable-identity, true-motion, interruption, stale-output, Replay, layout, reduced-motion, latency, and existing Gate 1.4 tests pass at that implementation commit; and a later checked-in rubric record binds its passing human review to that exact implementation SHA and manifest digest with no relevant implementation drift. Independent engineering and design review must have no material findings, the pull request must be merged, and all required checks on merged main must be green. No paid Azure call is required or authorized.

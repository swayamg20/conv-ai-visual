# Turn a typed equation into a live verified visual explanation

## Purpose / Big Picture

Gate 1.5 proves that Murmur can play one polished, interruptible completing-square lesson for the fixed equation `x² + 6x = 7`. Gate 1.6, called Live Parametric Choreography, turns that authored example into a real product capability. A signed-in learner opens `/canvas/generate`, types a supported equation such as `x² + 8x = 20`, and sees the correct square, half-coefficient, missing corner, balanced equation, factorization, and roots animate on the board. The learner can stop at a settled checkpoint, ask why the corner is 16, and continue from the exact visible state without restarting or changing the problem.

This gate deliberately combines two speeds. The Visual Reflex is a deterministic, zero-provider path for an exact supported command whose requested action is unambiguous. The Director is a small model-routed path for a less rigid pedagogical request. Both paths end at the same server-owned route, deterministic compiler, independent verifier, certificate chain, complete-suffix preflight, browser decoder, and checkpoint renderer. Neither the browser nor the model may author coefficients, derived mathematics, SVG coordinates, narration, timing, viewport poses, node identifiers, or integrity evidence.

The supported family is finite and structural rather than a list of showcase equations. The server accepts one monic equation `x² + bx = c`, written with either `x²` or `x^2`, when `b` is an even integer from 2 through 16, `c` is a positive integer no greater than 80, and `c + (b/2)²` is a perfect square whose positive root is at most 9. Equivalently, the family is `x² + 2hx = m² - h²` for integers `1 <= h < m <= 9`, which contains 36 equations. These bounds keep every derived quantity, label, and camera composition visually qualifiable while proving that the engine is actually parametric. The compiler and verifier each derive `h`, `h²`, `m²`, `m`, `m-h`, and `-(m+h)` independently.

The visual quality bar rises with the generalization. The main path should settle in 45 to 55 seconds at normal speed rather than Gate 1.5's roughly 64 seconds. Every checkpoint must remain understandable with audio muted: the equation tokens, geometric dimensions, balance operation, factorization, and two roots carry the reasoning directly, while narration reinforces rather than supplies missing logic. Motion should keep the same visual objects through each transformation, use the camera only to focus attention, and preserve the interruption semantics already proven in Gate 1.5.

Gate 1.6 does not attempt arbitrary algebra, arbitrary functions, model-authored animation code, general-purpose Manim compatibility, a new voice pipeline, or cross-session persistence. It establishes one trustworthy parameterized visual grammar and exposes it through the authenticated product surface. Voice remains a parallel track and is not changed in this worktree.

## Progress

- [x] 2026-09-09 05:58 IST: Created clean branch `codex/gate16-live-parametric-choreography` from exact `origin/main` commit `a2604b4`; confirmed the separate main worktree contains voice work and will remain untouched.
- [x] 2026-09-09 06:05 IST: Audited the Gate 1.5 compiler, independent verifier, V2 state and certificate contracts, full-suffix service preflight, authenticated choreography endpoint, strict browser decoder, fixture runner, playback runtime, and current `/canvas/generate` product route.
- [x] 2026-09-09 06:08 IST: Chose an additive V3 protocol, a 36-equation bounded family, and deterministic problem binding before provider resolution; preserved the exact Gate 1.5 V2 fixture, hashes, node IDs, and compiler path as compatibility surfaces.
- [ ] Implement and exhaustively test the bounded problem contract, parser, derived mathematics, V3 routed beat, and V3 semantic state.
- [ ] Implement the parametric compiler, independently authored verifier, problem-bound V3 receipt and certificate, and all-prefix resume/clarification coverage.
- [ ] Implement the zero-provider Visual Reflex and the choreography-only Director, then integrate both with the existing authenticated service while retaining full-suffix atomic preflight.
- [ ] Mirror V3 contracts in the browser, expose editable equation input on `/canvas/generate`, and preserve exact checkpoint interruption, continuation, replay, and error behavior.
- [ ] Shorten and visually qualify the authored pacing, add mute-first evidence, generate independent parametric fixtures, and retain the Gate 1.5 fixture byte-for-byte.
- [ ] Run focused and full backend/frontend checks, an explicitly capped Azure routing corpus, browser evidence, independent review, and CI; record exact evidence before requesting merge.

## Surprises & Discoveries

- The authenticated choreography HTTP route already exists at `POST /api/live-scenes/choreography/stream`, and the browser transport already supports it. The product route still mounts the older Pythagorean `LiveModelScene`, while the Gate 1.5 completing-square UI defaults to a provider-free fixture. Gate 1.6 therefore needs product wiring and V3 decoding, not a new transport stack.
- The current shared visual-router prompt advertises only Pythagorean decisions even though its parser union contains choreography decisions. Sending the Gate 1.5 choreography service to a real provider is therefore likely to produce a valid Pythagorean decision that the service safely declines. Gate 1.6 needs a choreography-only model boundary rather than another prompt branch in the shared system prompt.
- Gate 1.5's fixed values appear independently throughout both `completing_square_compiler.py` and `completing_square_verifier.py`, and several node IDs embed those values. Reinterpreting the V2 types would silently invalidate their semantic meaning and hashes. The parametric path must use value-neutral IDs and new V3 integrity envelopes.
- The existing service preflights the entire requested checkpoint suffix before emitting checkpoint one. That stronger atomicity is more valuable than speculative first paint: a later corrupt checkpoint cannot leave a learner with a certified but incomplete misleading prefix. Visual Reflex will reduce routing latency without weakening that guarantee.

## Decision Log

- Decision: implement Gate 1.6 in the isolated worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product` and leave `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual` untouched. Rationale: the latter contains concurrent voice work, and the user explicitly separated the visual and voice tracks. Date/Author: 2026-09-09 / Codex.
- Decision: retain all V2 contracts and add V3 siblings instead of adding optional problem fields to V2. Rationale: V2 routed-beat hashes, certificates, semantic state, strict browser decoders, and the checked-in Gate 1.5 fixture are already compatibility surfaces. Date/Author: 2026-09-09 / Codex.
- Decision: support the complete bounded family `1 <= h < m <= 9`, expressed as `x² + 2hx = m² - h²`, rather than only three catalog entries. Rationale: a structural finite domain proves real parameterization while keeping the layout, label sizes, mathematical outputs, and exhaustive tests bounded. Date/Author: 2026-09-09 / Codex.
- Decision: parse and bind the equation deterministically before resolving a provider. On an empty frontier exactly one supported equation is required; on a V3 continuation no equation reuses the committed spec, the same equation is allowed, and a different or multiple equation declines without provider dispatch. Rationale: the model may choose pedagogical direction but must never choose the mathematics. Date/Author: 2026-09-09 / Codex.
- Decision: make Visual Reflex a deterministic route for a closed exact command grammar, not a speculative checkpoint. All output still waits for complete compile, independent verification, certificate construction, and wire preflight. Rationale: this removes avoidable provider latency while preserving all-or-nothing correctness. Date/Author: 2026-09-09 / Codex.
- Decision: use a choreography-only decision schema and prompt for Director requests. The only model choices are start, continue, clarify-corner, target stage, or abstain. Rationale: domain separation prevents Pythagorean decisions and excludes equations, narration, geometry, timing, and integrity data from the model boundary. Date/Author: 2026-09-09 / Codex.
- Decision: preserve the eight main checkpoint identities and single `corner_detail` detour while shortening their authored holds to a 45-to-55-second main path. Rationale: these are useful semantic boundaries and replay units; the user feedback identified pacing, not checkpoint meaning, as the weakness. Date/Author: 2026-09-09 / Codex.
- Decision: do not run a paid model corpus until provider-free correctness, a clean pushed commit, a dry-run cost reservation, and an exact prompt snapshot all pass. Cap the live qualification campaign at USD 0.07, well below the previously authorized USD 0.50. Date/Author: 2026-09-09 / Codex.

## Outcomes & Retrospective

Implementation is in progress. This section will record the shipped branch and merge commits, exact test counts, V2 compatibility digests, V3 fixture digests, live-provider spend and latency, browser evidence, product-owner findings, and remaining limitations. Gate 1.6 is not complete merely because the parametric compiler works; it is complete only when a signed-in user can edit the equation on `/canvas/generate`, receive the correct live lesson, interrupt and clarify the matching corner, continue and replay, and the full proof below passes.

## Context and Orientation

The low-level scene protocol lives in `backend/murmur/live_scene/contracts.py`. It defines immutable SVG-oriented nodes, scene patches, scene revisions, and lifecycle events. `backend/murmur/live_scene/semantic_contracts.py` adds server-owned semantic component state. Gate 1.5's fixed completing-square frontier is `CompletingSquareState` in `backend/murmur/live_scene/completing_square_contracts.py`; it stores only the component identity, last main checkpoint, and whether the corner clarification has happened.

`backend/murmur/live_scene/choreography_contracts.py` defines the V2 routed beat and the reusable V1 presentation vocabulary: stages, checkpoint captions, exact cinematic and compact viewport poses, and one bounded parallel cue phase. `backend/murmur/live_scene/completing_square_compiler.py` turns a routed V2 beat into a deterministic sequence of checkpoint patches. `backend/murmur/live_scene/completing_square_verifier.py` independently reconstructs the expected mathematics and visual obligations without importing compiler snapshots. `backend/murmur/live_scene/checkpoint_contracts.py` binds the routed beat, patch, independent receipt, presentation, choreography, low-level and semantic state hashes, and previous certificate head into a V2 checkpoint certificate.

`backend/murmur/live_scene/service.py` currently routes a choreography request through `VisualActRoutingEngine`, lowers the model decision to a V2 beat, compiles it, and preflights the complete suffix through `_prepare_choreography_batch` before emitting a checkpoint. The service must retain that ordering. `backend/murmur/api/routers/live_scenes.py` exposes the authenticated product endpoint and a separately guarded loopback lab endpoint, both using `SemanticLiveSceneRequest` and the 64 KiB choreography wire encoder.

In the browser, `web/src/lib/live-scene/choreography.ts` and `web/src/lib/live-scene/checkpoint.ts` strictly decode V2 compiler claims. `web/src/features/live-scene/choreography-model-stream.ts` decodes the complete stream envelope and calls the product endpoint. `choreography-playback.ts`, `choreography-stream-runtime.ts`, `choreography-executor.ts`, and `svg-node-reconciler.ts` validate, queue, animate, settle, interrupt, and replay checkpoint transactions. `live-choreography-demo.tsx` is the Gate 1.5 fixture-oriented UI. The user-facing page `web/src/app/(app)/canvas/generate/page.tsx` currently renders `LiveModelScene`, which calls the older semantic Pythagorean endpoint.

A presentation checkpoint is a complete settled teaching unit: its patch changes the board, its choreography describes permitted motion over that patch, its caption explains that step, and its certificate binds the transition. A frontier is the last accepted low-level and semantic state plus its certificate-chain head. Full-suffix preflight means the server validates every checkpoint requested by one action before it emits any of them. These definitions remain unchanged in V3.

## Plan of Work

First, add a dependency-leaf problem module rather than creating a state/beat import cycle. `backend/murmur/live_scene/completing_square_problem_contracts.py` will define exact-wire `CompletingSquareProblemSpecV1` with only `v`, `linearCoefficient`, and `rightHandSide`, validate the bounded family, expose pure derived values, and hash the spec under `murmur:completing-square-problem:v1`. `completing_square_problem_parser.py` will locate exactly one canonical symbolic equation in an initial prompt and distinguish absent, multiple, malformed, conflicting, and unsupported cases without retaining arbitrary prompt text. Its exhaustive test iterates the full 36-case family and every neighboring invalid boundary.

Second, add V3 state and routing contracts without changing V2. `ParametricCompletingSquareStateV1` uses kind `completing_square_parametric` and stores the exact problem spec with the existing checkpoint frontier and clarification bit. `RoutedChoreographyBeatV3` uses hash domain `murmur:routed-choreography-beat:v3`, includes the problem spec, and reuses the existing closed advance/clarify route vocabulary. `SemanticSceneState` accepts both V2 and V3 component kinds. Continuation can never auto-upgrade or splice a V2 frontier into V3.

Third, implement a parametric compiler path using value-neutral node IDs such as `eq_linear`, `eq_half_a`, `eq_half_b`, `eq_corner_value`, `eq_completed_rhs`, `root_positive`, and `root_negative`. Extract only genuinely shared checkpoint-order, patch-diff, viewport, and choreography helpers from the V2 compiler; leave every V2 constant, ID, hash, and serialized output unchanged. Derive display strings and bounded token widths from the spec. Scale the two strip widths and corner consistently within certified geometry bounds, and keep both cinematic and compact focus poses unclipped for all 36 problems. Replace narration-only reasoning with visible dimension labels, an explicit `h x + h x = 2hx` split, `h x h = h²`, `c + h² = m²`, `(x+h)² = m²`, and the two exact roots.

Fourth, add an independently authored V3 verifier and integrity envelope. `CheckpointVerificationReceiptV3`, `CheckpointCompilerCertificateBodyV3`, `CheckpointCompilerCertificateV3`, and `CompiledCheckpointV3` use V3-specific hash domains and compiler version `murmur.completing_square_choreography.v2`. Both receipt and certificate body contain `problemSpecSha256`; validators prove that beat spec, accepted component spec, result component spec, receipt digest, and certificate digest all agree. The verifier independently derives the allowed equations, geometry, operation targets, camera containment, factorization, and roots and must retain the existing AST test forbidding compiler imports or a shared expected-checkpoint table. Tests transplant receipts and certificates across problems and corrupt later checkpoints to prove zero events escape preflight.

Fifth, separate routing into the Visual Reflex and Director. A small `choreography_routing` module will recognize only exact server-documented commands for start/continue/clarify and return the same closed resolved route with zero provider calls. All other supported-problem prompts go through a choreography-only prompt, strict single-record parser, and one-repair engine. The Director sees untrusted prompt text and the semantic frontier but can emit only start, continue, clarify, stage, or abstain; the parsed problem stays outside its output. Unsupported, missing, multiple, or conflicting equations decline before the client factory and provider admission. Request admission still happens first so malformed use remains rate bounded. Route-specific capacity errors become non-retryable for an unchanged request.

Sixth, integrate V3 into the authenticated choreography service without replacing the endpoint. The service order is authentication and request admission, started event, paired-base/capacity validation, deterministic problem binding, reflex-or-provider routing, V3 lowering, compile, independent verify, certificate binding, and complete wire preflight before checkpoint one. V2 fixtures and direct V2 compiler tests remain available, but new product starts create V3. Cancellation and disconnect close exactly the owned provider stream and request lease. Service tests assert zero client-factory and provider calls on pre-provider declines and mismatches.

Seventh, mirror V3 at the browser boundary. Add exact-key problem, beat, component, receipt, certificate, compiled-checkpoint, and stream decoders selected by `beat.v`; do not loosen the V2 decoder. Generalize the runtime's internal checkpoint union only where both versions have the same safe presentation behavior. The browser validates all cleartext joins and chain heads, treats server hashes as opaque commitments, and retains the last accepted frontier on any V3 decode or playback failure.

Eighth, add a focused authenticated product wrapper instead of turning `live-choreography-demo.tsx` into a god component. The wrapper owns editable equation text, examples, auth headers, product transport, reset-on-problem-change behavior, and user-facing parser/decline messages. The existing stage/runtime remains responsible for playback. `web/src/app/(app)/canvas/generate/page.tsx` renders this wrapper. A problem may be edited before starting or after an explicit reset, never while a certified frontier exists. Buttons issue the exact reflex grammar; free-form pedagogical text exercises Director. The corner button displays the derived value, and captions/progress update from V3 state rather than fixed constants.

Ninth, qualify speed and comprehension. Reduce the main authored timing to 45 through 55 seconds without changing checkpoint settlement semantics. Generate separate deterministic fixtures for representative lower, middle, and upper cases while leaving `completing-the-square.v1.json` byte-identical at SHA-256 `1df951328558d537347625419f9ccf16b454d06f45bd108fc79e86f89ee98887`. Extend Playwright to cover equation editing, all eight checkpoints, corner clarification for 16, mid-motion interruption, exact continuation, zero-request replay, compact layout, reduced motion, unsupported input, and a mute-first capture whose checkpoint screenshots are independently readable.

Finally, qualify the model boundary and release. Provider-free tests must pass on a clean pushed commit before any paid request. The live Azure corpus will contain supported starts, resumes, clarification, no-progress, and unsupported-intent decisions that intentionally avoid the exact reflex grammar. It allows at most 15 cases, 30 dispatches including repair, 2,048 output tokens per dispatch, SDK retries disabled, at least 6.1 seconds between dispatches, and a conservative USD 0.07 pre-dispatch reservation cap. No secrets or raw prompts enter committed artifacts. After the full local matrix and CI pass, inspect the real-speed video and settled screenshots, record findings in this plan, and present the branch for product-owner testing before merge.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product` unless they begin with `cd web`.

Before and after every milestone, prove the branch and worktree:

    git status --short --branch
    git rev-parse HEAD
    git rev-parse @{upstream}

After the bounded problem and V3 state/beat contract milestone:

    uv run pytest -q tests/test_live_scene_completing_square_problem.py tests/test_live_scene_completing_square_contracts.py tests/test_live_scene_choreography_contracts.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_integrity.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_completing_square_problem.py tests/test_live_scene_completing_square_contracts.py tests/test_live_scene_choreography_contracts.py
    uv run ruff format --check backend/murmur/live_scene tests

After the V3 compiler, verifier, receipt, and certificate milestone:

    uv run pytest -q tests/test_live_scene_completing_square_compiler.py tests/test_live_scene_completing_square_verifier.py tests/test_live_scene_checkpoint_contracts.py tests/test_live_scene_choreography_contracts.py tests/test_live_scene_choreography_fixture.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_completing_square_compiler.py tests/test_live_scene_completing_square_verifier.py tests/test_live_scene_checkpoint_contracts.py
    uv run ruff format --check backend/murmur/live_scene tests

After routing, service, API, and zero-provider reflex integration:

    uv run pytest -q tests/test_live_scene_choreography_router.py tests/test_live_scene_choreography_router_prompt.py tests/test_live_scene_visual_act_engine.py tests/test_live_scene_visual_act_lowering.py tests/test_live_scene_routed_choreography_service.py tests/test_live_scene_choreography_service_contracts.py tests/test_live_scene_choreography_wire.py tests/test_live_scene_choreography_api.py tests/test_live_scene_admission.py
    uv run pytest -q tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_routed_semantic_service.py tests/test_live_scene_semantic_api.py
    uv run ruff check backend/murmur/live_scene backend/murmur/api/routers/live_scenes.py tests
    uv run ruff format --check backend/murmur/live_scene backend/murmur/api/routers/live_scenes.py tests

Generate V3 fixtures only through a deterministic repository script. Generate twice to separate temporary paths and compare SHA-256 values before replacing any checked-in V3 fixture. Never pass the Gate 1.5 V1 fixture path as an output target.

After browser contracts, runtime, and product UI:

    cd web && npm test -- src/lib/live-scene/choreography.test.ts src/lib/live-scene/checkpoint.test.ts src/lib/live-scene/choreography-planner.test.ts src/features/live-scene/choreography-model-stream.test.ts src/features/live-scene/choreography-playback.test.ts src/features/live-scene/choreography-stream-runtime.test.ts src/features/live-scene/live-parametric-choreography.test.tsx
    cd web && npm run lint
    cd web && npm run typecheck

Run provider-free browser evidence and the normal-speed capture:

    cd web && npm run e2e:choreography
    cd web && npm run capture:choreography
    cd web && npm run validate:choreography-artifacts

Before the paid routing corpus, run its dry-run mode and verify that the resolved git commit is clean, the exact prompt snapshot is hashed, all reservations fit below USD 0.07, SDK retries are zero, and no exact-reflex case accidentally bypasses the provider. Run the paid mode once only if all guards pass, then validate its private sanitized report.

Before each coherent milestone commit:

    git diff --check
    git status --short

Push each milestone immediately to the feature branch:

    git push -u origin codex/gate16-live-parametric-choreography
    git rev-parse HEAD
    git rev-parse @{upstream}

Before requesting merge, run the complete local gate:

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

Record exact counts, durations, digests, live cost, timing percentiles, artifact paths, and any nonblocking caveats in `Progress` and `Outcomes & Retrospective`. Verify the pushed SHA and GitHub Actions run rather than treating a local commit as delivery.

## Validation and Acceptance

The problem boundary passes when every one of the 36 supported equations parses from both `x²` and `x^2` notation and derives the expected half-coefficient, corner, completed right side, square-root magnitude, and two roots. Empty-state prompts with no equation, multiple equations, malformed syntax, a non-monic square, another variable, an odd or out-of-range linear coefficient, a nonpositive or out-of-range right side, a non-square completion, or a conflicting continuation all decline without client-factory or provider dispatch.

V3 compatibility passes when the exact V2 routed-beat golden hash still matches, the Gate 1.5 fixture remains byte-identical at its recorded SHA-256, every V2 test remains unchanged and green, and no V2 semantic frontier can be continued as V3 or vice versa. V3 serialization must reject unknown fields, non-strict integers, invalid problem digests, changed problem identity, and cross-problem receipt or certificate replay.

The compiler and verifier pass when every supported equation compiles deterministically from empty state through all eight main checkpoints, representative lower/middle/upper cases resume from every prefix, and the corner clarification is legal exactly once at the unclarified `missing_corner` frontier. Every displayed value and geometric dimension must derive from the problem. The independent verifier must reject a wrong split, wrong corner area, unequal balance, wrong completed square, wrong factor, either wrong root, clipped focus, collision, illegal morph, foreign node, changed problem, or later-checkpoint corruption. Any failure during complete-suffix preflight emits zero checkpoint events.

Routing passes when exact supported commands take Visual Reflex with zero provider calls and semantically looser supported requests take Director through the choreography-only schema. The model can choose only action and stage. One malformed or state-invalid decision triggers one sanitized repair; a second fails with `invalid_visual_act`. Provider timeout, error, and admission retain their retryable codes; semantic, capacity, compiler, verifier, certificate, problem, and wire failures are non-retryable where repeating the same request cannot help.

The browser and product pass when an authenticated learner can edit the initial equation on `/canvas/generate`, start `x² + 8x = 20`, watch the correct eight-checkpoint lesson, stop at the missing 4-by-4 corner, ask why it is 16, continue to `(x+4)² = 36` and roots 2 and -10, and replay without a network request. Editing is disabled while a frontier exists and available again after reset. Decode, renderer, or stream failure retains the last accepted board. Compact layout, reduced motion, cancellation, and stale-generation handling remain exact.

The visual gate passes when the normal-speed main path is between 45 and 55 seconds; first visible deterministic feedback is measured separately from authored holds; all eight settled screenshots communicate the mathematical transition with narration muted; the corner zoom remains purposeful; text stays within both certified viewports; object identity and cue order remain stable; interruption settles within the existing latency budget; and replay reproduces the same captions, node order, viewport sequence, and certificate chain with zero live-scene requests.

The live-provider gate passes when all 15 cases terminate safely, all supported route expectations and four resume identities are exact, unsupported/no-progress cases do not mutate, at least 14 of 15 cases succeed without repair, every mutation carries a valid problem-bound certificate chain, warm route median is at most 1.5 seconds and p95 at most 3 seconds, accepted route to first preflighted checkpoint is at most 100 ms p95, dispatch pacing and reservation counts match the report, and total conservative reservation does not exceed USD 0.07. A live-provider failure blocks promotion but does not invalidate provider-free compiler correctness.

Gate 1.6 is ready for merge only after the full backend and frontend matrices, production build, Gate 1.4 and Gate 1.5 regression browsers, Gate 1.6 product browser proof, artifact validation, exact pushed-head CI, and product-owner visual test all pass. Merge remains a separate explicit action after that evidence is presented.

# Teach projectile motion as one interruptible living visual model

## Purpose / Big Picture

Gate 1.7 turns Murmur from a verified visual lesson for one algebra family into a reusable live visual-model product. A signed-in learner opens `/canvas/projectile`, chooses an initial speed and launch angle, and watches one continuous projectile-motion explanation unfold: the launch velocity splits into horizontal and vertical components, the projectile traces a parabola, the board focuses the apex, and the same visual objects continue to impact and summary. The explanation must remain understandable with captions hidden. Equations, vectors, motion, labels, and spatial relationships carry the reasoning directly.

The learner can interrupt at a settled checkpoint and ask one of three high-value questions: why horizontal velocity stays constant, why acceleration is still downward when vertical velocity is zero at the apex, or why ascent and descent are symmetric in the no-drag model. The answer appears as a one-shot visual detour on the existing board, then the main explanation continues without a wipe or a restart. At any settled frontier, the learner can change to another supported launch speed or angle. Murmur preserves the semantic component and the identities of the axes, projectile, vectors, paths, apex, and range line while those objects morph to the new certified geometry. Dynamic value labels retain stable IDs but use the existing incompatible-content cross-fade, so text never snaps mid-glyph and retarget remains inside the patch-operation budget.

This gate proves a second visual domain, not a universal animation language. It adds one missing general motion capability—a verified path-trace cue with a marker that follows a fixed-topology path—and uses the existing immutable scene patches, checkpoint settlement, interruption, replay, authentication, and certificate-chain seams. The model, when explicitly selected as Director, may choose only a closed pedagogical action and stage. It may never produce physics, numbers, SVG, node identifiers, camera coordinates, timing, or integrity evidence. The primary Visual Reflex path is deterministic and provider-free.

The supported physical model is deliberately finite and explicit: ground-to-ground motion, metres and seconds, no drag or wind, zero launch height, and `g = 10 m/s²`. `speedMps` is one of 20, 25, or 30 and `angleDeg` is one of 30, 45, or 60, giving nine qualified problems. The compiler and an independently authored verifier separately derive `v_x = v_0 cos(theta)`, `v_y0 = v_0 sin(theta)`, `x(t) = v_x t`, `y(t) = v_y0 t - gt²/2`, time of flight, maximum height, range, trajectory samples, and the apex fact `v_y = 0` while `a_y = -g`. Inputs are hashed as exact integers. Displayed irrational values use one documented two-decimal half-up rule; mathematical checks use unrounded values.

The authored main lesson has six settled checkpoints: `setup`, `decompose_velocity`, `trace_ascent`, `apex_state`, `trace_descent`, and `summary`. Decomposition also establishes the two motion equations; summary settles impact, time, height, and range. This keeps every frontier pedagogically meaningful instead of manufacturing extra stops to match the previous gate. It targets 35 to 45 seconds at normal speed, has no unexplained hold longer than 1.2 seconds, and produces deterministic first meaningful visual feedback under 300 milliseconds locally. The exact pushed commit must preserve every Gate 1.4, 1.5, and 1.6 contract and fixture, pass a separate provider-free browser qualification, and produce reviewable video, board-only screenshots, and a commit-bound artifact manifest. A paid model call is neither required nor authorized for this gate.

## Progress

- [x] 2026-09-11 23:02 IST: Created clean branch `codex/gate17-live-visual-model` and isolated worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-gate17-live-visual-model` from exact `origin/main` commit `73cefb0`; confirmed the unrelated dirty voice checkout will remain untouched.
- [x] 2026-09-11 23:16 IST: Audited the Gate 1.6 request union, service delegate, independent compiler/verifier boundary, browser runtime, authenticated product route, fixture harness, artifact lifecycle, and CI layout. Re-read the repository ExecPlan rules and the relevant Next 16 page and client-component documentation before frontend work.
- [x] 2026-09-11 23:24 IST: Locked the nine-case physical domain, six-checkpoint story, three one-shot clarifications, continuity-preserving parameter retarget, additive projectile protocol, and additive path-trace choreography primitive. Chose provider-free qualification first and preserved all older protocols and evidence as sealed regressions.
- [x] 2026-09-12 00:08 IST: Implemented the strict nine-problem contract, six-checkpoint/four-stage frontier, canonical clarification ledger with one active visual detail, advance/clarify/retarget routes, exact Reflex/Director request variants, and additive projectile semantic state. Kept the top-level API union deliberately deferred until its service dispatch exists. The focused projectile/compiler/domain/request/semantic/choreography suite passes 349 tests and the wider prior-gate compiler compatibility matrix passes 2,072 tests.
- [x] 2026-09-12 00:08 IST: Added sealed backend `ChoreographyPlanV2` with one closed `trace_path` cue and its independent hash domain. V1 contracts and fixture hashes remain unchanged. The deterministic projectile compiler now exhausts all nine problems, seven resumable frontiers, three out-of-order one-shot clarifications, and all 432 legal directed retarget/frontier combinations; paths retain 33 plus 33 uniform-time points, retarget uses at most 14 operations, and the main authored duration is exactly 36.6 seconds.
- [x] 2026-09-12 00:27 IST: Mirrored sealed V1 plus additive choreography V2 in the browser and implemented the protocol-neutral trace executor. It validates lifecycle and trace ownership before DOM mutation, moves the retained marker by uniform sample time while revealing stroke by cumulative length, preserves exact pre/post-paint interruption settlement, and materializes the same terminal state under reduced motion. Five focused files pass 169 tests; the full frontend suite passes 924 tests across 68 files, with type-check, focused ESLint, and diff checks clean.
- [x] 2026-09-12 00:37 IST: Added an independently authored projectile verifier that recomputes physics, geometry, labels, layout, operations, choreography, and camera containment from primitive speed and angle only. Its strict matrix exposed a real retarget-camera defect; retarget focus now follows the settled frontier instead of naming clipped right-panel content while the plot camera is active. Compiler plus verifier pass 56 tests, including all 54 main problem/checkpoint combinations, 90 eligible clarification placements, and all 432 directed retarget/frontier combinations.
- [x] 2026-09-12 00:41 IST: Added the closed provider-free projectile routing boundary. Reflex resolves only advance, eligible one-shot clarification, or settled in-place retarget; Director output is restricted to start, continue, one clarification topic, or abstention and cannot own parameters, physics, geometry, timing, or integrity fields. The 78-test routing matrix includes every stage/frontier relation and all 432 retarget routes; the combined contract/request/routing/semantic slice passes 325 tests.
- [x] 2026-09-12 00:43 IST: Mirrored the exact projectile problem, state, route, routed beat, Reflex request, and Director request contracts in the browser without inventing the still-pending checkpoint wire. Python camelCase serialization was compared directly with every browser shape. All nine problems, strict physics exclusions, ordered one-shot topics, problem continuity, revision joins, component limits, and both request modes pass 79 focused tests; the full browser suite passes 1,003 tests across 70 files with type-check and lint clean.
- [ ] Add strict projectile problem, semantic state, route, request, choreography V2, receipt, certificate, event, and wire contracts without changing serialized V1/V2/V3 behavior.
- [ ] Implement the deterministic projectile compiler and independently authored verifier across all nine problems, every resumable frontier, three detours, and every supported retarget.
- [ ] Add the focused Reflex/Director routing kernel and projectile service, then integrate it through the existing authenticated endpoint and the narrow `SceneAuthoringService` delegate.
- [ ] Add strict browser contracts, the curved path-trace executor, the projectile stream runtime, and an authenticated `/canvas/projectile` product surface with stable object identity.
- [ ] Generate deterministic fixtures and qualify interruption, continuation, retargeting, replay, mute-first comprehension, responsive layout, reduced motion, and authenticated request behavior.
- [ ] Add a separate Gate 1.7 artifact lifecycle and CI job, run the complete backend/frontend and earlier-gate regression matrix, inspect the normal-speed evidence, and record exact pushed-head CI results.
- [ ] Obtain product-owner visual sign-off for the exact evidence commit before merge; merged-main CI remains a separate final delivery condition.

## Surprises & Discoveries

- The existing low-level scene contract has lines, rectangles, text, LaTeX, and arbitrary paths, but the sealed choreography V1 can only enter, exit, transform, emphasize, and focus. A normal transform moves a projectile along the straight chord between endpoints; it cannot truthfully render curved flight. Gate 1.7 therefore needs an additive choreography V2 trace cue instead of overloading V1.
- Gate 1.6's `ParametricChoreographyStreamRuntime`, checkpoint contracts, and service are intentionally completing-square-specific. Their architectural pattern is reusable, but generalizing those large modules while adding a second domain would place two gates at risk. Gate 1.7 will share protocol-neutral players, renderers, and low-level contracts, while keeping a focused projectile service and runtime.
- Parameter retargeting changes the problem identity without changing the semantic component identity. The new certificate must bind both base and result problem hashes; Gate 1.6's single problem hash is insufficient for a legitimate morph.
- The current patch budget is per streamed response rather than per lifetime semantic revision. A six-checkpoint fresh lesson fits the existing cap with room for safe evolution, while a clarification or retarget is a separate one-checkpoint response and can still continue afterward.
- Fixed path topology is a powerful continuity seam. Sampling every supported ascent and descent with the same point count lets the browser morph trajectories without replacing their DOM identity and lets the verifier prove a marker remains on the certified curve.

## Decision Log

- Decision: Add exact protocol `projectile_choreography_v1` as a third choreography request-union arm instead of widening `parametric_choreography_v3`. Rationale: absent protocol must remain Gate 1.5, V3 hashes and discriminators are already sealed, and cross-domain state must fail closed. Date/author: 2026-09-11, Codex with Swam Gupta as product owner.
- Decision: Support `speedMps in {20, 25, 30}` and `angleDeg in {30, 45, 60}` under fixed no-drag assumptions. Rationale: nine cases visibly vary range, height, and duration while fitting a single qualified camera and formula-width catalog. Date/author: 2026-09-11, Codex.
- Decision: Use six main checkpoints and three one-shot visual clarification topics. Rationale: `decompose_velocity` can establish both equations and `summary` can settle impact values, so six frontiers preserve setup, ascent, apex, and descent without stopping merely to reveal another line of text. Date/author: 2026-09-11, Codex.
- Decision: Retarget parameters in place at a settled frontier and preserve physical node IDs, value-label IDs, component ID, frontier, and applicable clarification flags. Changed LaTeX stays on its stable node ID and is classified as an incompatible-content cross-fade by the browser planner. Rationale: this directly proves a living model rather than a template restart, prevents glyph snapping, and avoids exhausting the 16-operation patch budget with paired exits and enters. A same-spec target declines as `no_forward_progress`; an unannounced problem change on advance or clarify declines as `problem_conflict`. Date/author: 2026-09-12, Codex.
- Decision: Add `ChoreographyPlanV2` with a `trace_path` cue, leaving V1 byte-sealed. Rationale: the marker must follow the parabola rather than interpolate along a chord, and changing V1 would invalidate earlier fixtures and evidence. Date/author: 2026-09-11, Codex.
- Decision: Represent ascent and descent as separate open paths with 33 points each, sampled uniformly in flight time. The marker follows fractional sample time while the revealed dash length follows cumulative distance to that sample. Rationale: arc-length progress would falsely imply nonconstant horizontal velocity, while equal topology makes every supported retarget morphable. Date/author: 2026-09-11, Codex.
- Decision: Make `baseProblemSpecSha256` nullable only for an empty start and require `resultProblemSpecSha256` on every transition. Ordinary advance and clarification require equal digests; retarget requires different digests. Rationale: a legitimate in-place parameter change must be distinguishable from both a fresh start and an illicit problem splice. Date/author: 2026-09-11, Codex.
- Decision: Keep the Director's output closed to action, target stage, clarification topic, or abstention. Structured parameter controls remain Reflex requests; the model never emits parameter values or physics. Rationale: determinism, latency, cost control, and mathematical trust remain server-owned. Date/author: 2026-09-11, Codex.
- Decision: Add `/canvas/projectile` beside `/canvas/generate` and link the two studios rather than replacing Gate 1.6. Rationale: both product capabilities remain independently testable and earlier user acceptance remains intact. Date/author: 2026-09-11, Codex.

## Outcomes & Retrospective

Implementation is in progress. At completion this section will record the exact branch SHA, test counts, fixture and artifact digests, first-visible and interruption percentiles, authored and observed duration, browser evidence paths, CI run, product-owner verdict, remaining limitations, and lessons for Gate 1.8's shared visual grammar. Until those facts exist, Gate 1.7 is not represented as shipped or accepted.

## Context and Orientation

The backend live-scene system is under `backend/murmur/live_scene/`. `contracts.py` defines strict low-level SVG-like nodes, immutable scene patches, revision limits, and generic stream events. `choreography_contracts.py` defines sealed Gate 1.5 presentation and choreography V1 cues. `checkpoint_choreography_player.ts` and `choreography-canvas-bridge.ts` under `web/src/features/live-scene/` are protocol-neutral browser seams that settle one checkpoint before acknowledging it. Gate 1.7 will extend choreography additively in backend and browser code so every earlier V1 serialized plan remains unchanged.

Gate 1.6 is the closest domain pattern. `completing_square_problem_contracts.py`, `completing_square_contracts.py`, `parametric_completing_square_compiler.py`, and `parametric_completing_square_verifier.py` separate accepted inputs, semantic frontier, authored output, and independent checks. `parametric_checkpoint_contracts.py` and `parametric_checkpoint_compiler.py` bind one transition to a receipt and certificate chain. `parametric_choreography_routing.py`, `parametric_choreography_service.py`, `parametric_choreography_service_contracts.py`, and `parametric_choreography_wire.py` implement closed routing, complete-suffix preflight, stream events, and strict SSE round-trips. Those files remain semantically sealed; projectile modules reuse their pattern and shared low-level types, not their domain assumptions.

`backend/murmur/live_scene/parametric_choreography_requests.py` currently owns the top-level choreography request union. `backend/murmur/api/routers/live_scenes.py` acquires authentication and admission once, then selects the protocol-specific service and encoder. `backend/murmur/live_scene/service.py` composes the focused services and exposes thin delegation methods. Gate 1.7 adds one union member, one dispatch arm, and one delegate without changing the product authentication or loopback-only lab boundaries.

The browser's strict Gate 1.6 boundary lives in `web/src/lib/live-scene/parametric-*.ts`, while transport, playback joins, and orchestration live in `web/src/features/live-scene/parametric-*.ts`. The authenticated equation studio is `web/src/features/live-scene/live-parametric-choreography.tsx`, mounted by `web/src/app/(app)/canvas/generate/page.tsx`. Gate 1.7 adds parallel focused modules and mounts `LiveProjectileChoreography` at `web/src/app/(app)/canvas/projectile/page.tsx`. It reuses the trusted SVG renderer and checkpoint player, and adds only the trace cue execution needed for curved motion.

The provider-free qualification pattern lives in `scripts/generate_parametric_choreography_fixtures.py`, `tests/test_live_scene_parametric_choreography_fixtures.py`, `web/src/app/e2e/parametric-choreography/`, `web/e2e/parametric-choreography*.spec.ts`, and `web/playwright.parametric-choreography.config.ts`. Gate 1.7 gets its own deterministic fixtures, route, Playwright configuration, artifact directory, validation schema, and CI job. It must not rewrite or reinterpret the Gate 1.5 or Gate 1.6 artifacts.

In this plan, a checkpoint is a completely rendered and verified scene revision that can safely survive interruption. A frontier is the last accepted main checkpoint plus any one-shot clarification topics already shown. A sidecar is a certified single-checkpoint detour or parameter retarget that returns to the same main lesson position. A path trace is an authored cue that reveals a path while moving one marker through its certified sample points. Stable identity means the same conceptual object retains the same scene node ID and browser DOM element through advance, interruption, replay, and retarget.

## Plan of Work

First, add the closed domain contracts. `projectile_motion_contracts.py` will define `ProjectileMotionProblemSpecV1`, the exact speed/angle sets, checkpoint and stage order, clarification topics, `ProjectileMotionStateV1`, closed route variants, and a problem-bound routed beat. `projectile_motion_requests.py` will define exact Reflex and Director bodies under `protocol: "projectile_choreography_v1"`. Reflex accepts one server-recognized route: advance to a closed stage, clarify one topic, or retarget to one complete supported problem spec. Director accepts prompt text but no client route or numeric output. Unsupported parameters, additional physical assumptions, cross-protocol semantic bases, mismatched revisions, unknown fields, and coercions fail or decline before provider resolution.

Second, extend choreography without mutating V1. `choreography_contracts.py` will gain versioned V2 phase and plan types whose cue union includes `TracePathCueV2 { cue, pathId, markerId }`. The trace path and marker already exist as low-level path nodes. The compiler emits separate 33-point ascent and descent paths sampled uniformly in flight time. The executor reveals each path progressively and places the marker at the corresponding fractional-time sample; dash length follows cumulative distance only to keep the visible stroke tip joined to that marker. Trace-owned markers are excluded from ordinary transforms, and the two target sets must be disjoint and exactly cover updated nodes. Interruption uses the existing pre-paint rollback and post-paint settle rule. Reduced motion commits the same final path and marker in one logical step. V2 has its own hash domain and strict budgets. Existing V1 constructors, adapters, encodings, and fixture digests remain byte-identical.

Third, implement the deterministic compiler in `projectile_motion_compiler.py`. It will author the six-checkpoint board with value-neutral stable IDs for axes, ground, launch vector, component vectors, acceleration, ascent path, descent path, projectile, apex, range, formula anchors, and value labels. Every supported trajectory uses the same sample count and path topology. The default cinematic board uses one fixed physical plot spanning all nine problems, with a separately qualified compact viewport. Changed numerical LaTeX is cross-faded inside its retained node identity during retarget. The compiler targets a 35-to-45-second normal lesson and keeps any single unexplained hold below 1.2 seconds.

Fourth, author `projectile_motion_verifier.py` independently. It may read primitive `speedMps` and `angleDeg` but may not import the projectile compiler or a shared derived-physics table. It separately recomputes components, time, height, range, equations, uniformly sampled trajectory points, velocity signs, marker endpoints, and plot projection. It verifies all visible captions and labels, units, path openness, exact stable-role IDs, operation targets, camera containment, text collisions, choreography partitioning, timing, and the apex invariant `v_y = 0` with `a_y = -10 m/s²`. Mutation tests prove it rejects degrees/radians confusion, rounded-value drift, wrong gravity sign, straight-chord motion, a point below ground, topology changes, clipped labels, and cue-target overlap.

Fifth, add the integrity envelope and atomic transition compiler. `projectile_motion_checkpoint_contracts.py` will define V1 receipt and certificate types with new hash domains. Both bind base and result problem hashes so a retarget is explicit, plus the route, transition ID, patch, presentation, choreography V2 plan, low-level revisions, semantic revisions, and previous certificate head. `projectile_motion_checkpoint_compiler.py` materializes every candidate transition, runs the independent verifier, and returns a complete requested suffix only if every checkpoint succeeds. Main checkpoints and sidecars each increment low-level and semantic revisions exactly once. A corrupt late checkpoint emits nothing.

Sixth, implement routing and service integration. `projectile_motion_routing.py` resolves Reflex routes synchronously with zero client-factory or provider calls. Its small Director boundary quotes prompt and state as untrusted input, accepts one strict record, permits one sanitized repair, and closes owned streams on every exit. `projectile_motion_service_contracts.py`, `projectile_motion_wire.py`, and `projectile_motion_service.py` validate paired base states, re-realize the submitted frontier, reject foreign nodes, resolve the route, preflight the full suffix, wire-roundtrip every event, and only then yield checkpoint one. `parametric_choreography_requests.py`, `backend/murmur/api/routers/live_scenes.py`, and `service.py` receive the minimal additive union, encoder, dispatch, construction, and delegate changes. Existing authentication, admission leases, cancellation, and lab loopback checks remain the sole outer boundary.

Seventh, mirror the exact contracts in the browser. Focused modules under `web/src/lib/live-scene/projectile-*.ts` strictly decode problems, routes, state, beats, checkpoints, certificates, and events with exact keys and cross-field joins. Before adding another domain runner, extract the protocol-neutral generation, abort, queue, checkpoint-settlement, restoration, stale-event, history, and zero-network Replay state machine from `parametric-choreography-stream-runtime.ts` into `certified-choreography-stream-runtime.ts`. Keep the Gate 1.6 class as a thin adapter whose existing characterization suite must remain green, then add the projectile adapter under `web/src/features/live-scene/projectile-*.ts`. Domain adapters retain strict event decoding, semantic joins, accepted-record shape, and closed error vocabularies. The existing checkpoint player gains a narrow V2 plan adapter; the SVG execution layer gains path tracing with deterministic logical time, transform/opacity-only UI motion, post-paint settlement, stale-generation suppression, and `prefers-reduced-motion` parity.

Eighth, build the product experience at `/canvas/projectile`. Extract the generic title, checkpoint rail, progress, caption, quarantine, and responsive board shell from the completing-square stage into `certified-choreography-stage.tsx`, leaving `LiveChoreographyStage` as a compatibility wrapper. `LiveProjectileChoreography` presents a cinematic physics board, tactile structured speed and angle controls, one primary teach/continue action, contextual clarification controls derived from the accepted frontier, zero-network Replay, Stop, and Reset. It distinguishes editable desired parameters from accepted parameters: controls can issue an in-place retarget only after a settled checkpoint, and remain locked during playback. The teaching surface uses Murmur's warm black board, restrained amber trajectory, sage components, and lavender analytical annotations. It relies on hierarchy, shape, equations, vector direction, and labels rather than color alone. The compact layout keeps the board primary and controls reachable at 320 pixels; keyboard focus, touch targets, high contrast, overflow, loading/error copy, and actual OS reduced motion are explicitly qualified and locked at session mount. The existing equation studio links to this route and remains unchanged otherwise.

Ninth, generate and qualify evidence. Backend fixtures cover at least `20@30`, `20@45`, `20@60`, `30@45`, and `30@60`, including the complementary-angle equal-range invariant, maximum qualified range, maximum qualified height, apex clarification, and `20@45 -> 20@60` retarget at the apex. Browser qualification covers all six checkpoints, four interruptions each during path trace, marker motion, vector morph, focus, equation morph, and hold; exact settled continuation; each clarification; in-place retarget; zero-request Replay; unsupported-input non-mutation; caption-hidden screenshots; cinematic, compact, and reduced-motion layouts; exact Firebase bearer/body behavior; and signed-out pre-fetch failure. A real-speed capture records a 35-to-45-second accepted lesson plus contact sheet and deterministic motion samples. The manifest binds the exact commit, tree, source hashes, fixtures, browser/runtime versions, reports, screenshots, WebM, timings, network evidence, and artifact hashes.

Finally, run the full release matrix and push every coherent milestone. Existing V1/V2/V3 fixture hashes and earlier gate evidence must not change. The feature is locally complete only when backend tests, Ruff, frontend tests, lint, typecheck, production build, scene E2E, Gate 1.5 artifact validation, Gate 1.6 browser suites, and the new Gate 1.7 artifact lifecycle all pass. Exact pushed-head GitHub Actions must be green before presenting the evidence for Swam Gupta's visual acceptance. Merge requires separate explicit approval after that acceptance.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-gate17-live-visual-model` unless a command starts with `cd web`.

Before and after each milestone, prove worktree and upstream identity:

    git status --short --branch
    git rev-parse HEAD
    git rev-parse @{upstream}

After the domain, request, semantic, choreography V2, and integrity contracts:

    uv run pytest -q tests/test_live_scene_projectile_motion_contracts.py tests/test_live_scene_projectile_motion_requests.py tests/test_live_scene_choreography_contracts.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_projectile_motion_checkpoint_contracts.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_projectile_motion_contracts.py tests/test_live_scene_projectile_motion_requests.py tests/test_live_scene_projectile_motion_checkpoint_contracts.py
    uv run ruff format --check backend/murmur/live_scene tests

After the compiler, independent verifier, and atomic checkpoint compiler:

    uv run pytest -q tests/test_live_scene_projectile_motion_compiler.py tests/test_live_scene_projectile_motion_verifier.py tests/test_live_scene_projectile_motion_checkpoint_compiler.py
    uv run ruff check backend/murmur/live_scene tests/test_live_scene_projectile_motion_compiler.py tests/test_live_scene_projectile_motion_verifier.py tests/test_live_scene_projectile_motion_checkpoint_compiler.py
    uv run ruff format --check backend/murmur/live_scene tests

After routing, service, wire, API, and provider-free Reflex integration:

    uv run pytest -q tests/test_live_scene_projectile_motion_routing.py tests/test_live_scene_projectile_motion_service.py tests/test_live_scene_projectile_motion_service_contracts.py tests/test_live_scene_projectile_motion_wire.py tests/test_live_scene_choreography_api.py tests/test_live_scene_admission.py
    uv run pytest -q tests/test_live_scene_parametric_choreography_service.py tests/test_live_scene_choreography_service_contracts.py tests/test_live_scene_choreography_wire.py
    uv run ruff check backend/murmur/live_scene backend/murmur/api/routers/live_scenes.py tests
    uv run ruff format --check backend/murmur/live_scene backend/murmur/api/routers/live_scenes.py tests

Generate Gate 1.7 fixtures only through `scripts/generate_projectile_motion_fixtures.py`. Generate twice into separate temporary directories, byte-compare both generations, decode them independently, and compare against checked-in files before replacement. Never pass a Gate 1.5 or Gate 1.6 fixture path as an output target.

After browser contracts, trace execution, runtime, and product UI:

    cd web && npm test -- src/lib/live-scene/projectile-motion.test.ts src/lib/live-scene/projectile-checkpoint.test.ts src/lib/live-scene/projectile-choreography-stream.test.ts src/features/live-scene/projectile-choreography-model-stream.test.ts src/features/live-scene/projectile-choreography-playback.test.ts src/features/live-scene/projectile-choreography-stream-runtime.test.ts src/features/live-scene/live-projectile-choreography.test.tsx
    cd web && npm run lint
    cd web && npm run typecheck
    cd web && npm run build

Run the provider-free Gate 1.7 evidence lifecycle:

    cd web && npm run test:projectile-motion-artifacts
    cd web && npm run prepare:projectile-motion-artifacts
    cd web && npm run e2e:projectile-motion
    cd web && npm run e2e:projectile-motion:capture
    cd web && npm run e2e:projectile-motion:product-smoke
    cd web && npm run finalize:projectile-motion-artifacts
    cd web && npm run validate:projectile-motion-artifacts

Before each coherent milestone commit:

    git diff --check
    git status --short

Push every milestone immediately and verify exact equality:

    git push -u origin codex/gate17-live-visual-model
    test "$(git rev-parse HEAD)" = "$(git rev-parse @{upstream})"

Before requesting visual acceptance, run the complete local gate:

    uv run pytest -q
    uv run ruff check .
    uv run ruff format --check .
    cd web && npm test
    cd web && npm run lint
    cd web && npm run typecheck
    cd web && npm run build
    cd web && npm run e2e:scene
    cd web && npm run prepare:choreography-artifacts
    cd web && npm run e2e:choreography
    cd web && npm run capture:choreography
    cd web && npm run finalize:choreography-artifacts
    cd web && npm run validate:choreography-artifacts
    cd web && npm run e2e:parametric-choreography
    cd web && npm run e2e:parametric-choreography:capture
    cd web && npm run e2e:parametric-choreography:product-smoke
    cd web && npm run prepare:projectile-motion-artifacts
    cd web && npm run e2e:projectile-motion
    cd web && npm run e2e:projectile-motion:capture
    cd web && npm run e2e:projectile-motion:product-smoke
    cd web && npm run finalize:projectile-motion-artifacts
    cd web && npm run validate:projectile-motion-artifacts
    git diff --check

Record exact counts, timings, digests, artifact paths, commit SHA, and CI run in this plan. No live Azure call is part of these commands. If a future Director corpus is desired, it requires fresh explicit spend approval and a separately documented budget guard.

## Validation and Acceptance

The problem boundary passes when all nine exact problem specs validate, hash deterministically, and independently derive correct component velocities, flight time, maximum height, range, and trajectory samples. Strict contracts reject booleans, numeric strings, floats, unknown keys, unsupported neighboring values, a client-supplied gravity, launch height, wind, or drag. Advance and clarification requests cannot silently change parameters. Retarget requires a complete supported target, preserves component identity, and declines a same-spec request without mutation.

The semantic and integrity boundary passes when every one of the six main checkpoints compiles from each of seven possible frontiers, all three clarification topics enforce their prerequisites and one-shot behavior, and every directed retarget is deterministic at every legal frontier. Receipts and certificates bind base and result problem hashes, component, route, transition, patch, presentation, choreography, revisions, and previous chain head. Cross-problem, cross-component, stale-revision, and cross-protocol transplants fail. A corrupt later checkpoint causes complete-suffix preflight to emit zero checkpoints.

The physics verifier passes only when it independently recomputes every displayed value and every sampled position. At normalized flight times 0.25, 0.5, and 0.75 it checks `x = sR`, `y = 4Hs(1-s)`, vertical velocity signs positive, zero, and negative, monotonic horizontal travel, tangent-consistent velocity arrows, and at most one projected CSS-pixel geometry error. It rejects nonzero apex vertical velocity, zero apex acceleration, negative-gravity direction, clipped or colliding facts, a marker off its path, a changed path point count, cue overlap, and any compiler import in the verifier dependency graph.

The compatibility boundary passes when existing Gate 1.5 and Gate 1.6 fixtures remain byte-identical, V1 choreography hashes do not change, absent protocol still dispatches only to Gate 1.5, `parametric_choreography_v3` still dispatches only to Gate 1.6, unknown protocols fail request validation, and a semantic component from one protocol cannot enter another service. All existing backend, frontend, browser, and artifact tests remain green.

The browser motion boundary passes when path revelation and projectile travel follow the certified parabola rather than a straight chord; the projectile retains the same DOM identity; stable physical IDs survive advance, detour, retarget, interruption, and Replay; dynamic labels cross-fade without residue; and the final SVG equals deterministic fixture materialization. Pre-paint cancellation restores the prior checkpoint, post-paint interruption settles the complete target with p95 under 150 milliseconds, and no stale DOM, caption, semantic, or certificate mutation occurs during the following two seconds. Replay performs zero requests and reproduces checkpoint sequence, canonical SVG, viewports, stable IDs, captions, cue trace, semantic frontier, and certificate chain.

The visual product boundary passes when a learner can open `/canvas/projectile`, run the default `20 m/s at 45 degrees` lesson, stop at the apex, reveal why `v_y = 0` does not imply `a_y = 0`, continue to impact and summary, then retarget to 60 degrees without a board wipe. Caption-hidden board-only screenshots must visibly explain component decomposition, equations, ascent and descent, the apex distinction, flight time, height, and range. The accepted normal-speed capture is 35 to 45 seconds, first meaningful visual p95 is below 300 milliseconds locally, no unexplained dead interval exceeds 1.2 seconds, KaTeX and labels remain unclipped at 1280 by 720, 375 by 812, and 320 by 568, and reduced motion reaches identical mathematical and semantic terminal state.

The authentication boundary passes when the product browser sends an exact fresh Firebase bearer, method, content type, protocol, generation, structured problem, requested route, and base scenes; signed-out use fails before the live-scene request; unauthenticated backend requests reach neither admission, service, nor provider; and the development lab requires both development mode and a socket-level loopback peer rather than forwarded headers.

Gate 1.7 is ready for product-owner review only after the exact pushed SHA has a green separate `projectile_motion` CI job, its manifest validates independently, and the complete prior-gate regression matrix is green. It is ready to merge only after Swam Gupta accepts that exact WebM and board-only contact sheet as fast, original, continuous, and understandable without relying on audio. It is delivered only after the merge commit's main-branch CI is green.

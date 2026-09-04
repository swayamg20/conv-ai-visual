# Prove compiler-verified visual teaching acts with interruption-safe commits

## Purpose / Big Picture

Gate 1 proved that Murmur can safely stream model-authored SVG scene patches, animate them, interrupt them, and replay the accepted history. The live Azure corpus also showed why that representation cannot ship as the authoring layer: the model spent most of its latency choosing coordinates and styles, still produced clipping and overlap, and made factual teaching errors. The browser and transport were safe, but none of the ten lessons fully satisfied its prompt.

This plan builds a narrow Gate 1.1 prototype in which the model describes what to teach rather than how to draw it. A user will be able to reveal a Pythagorean area identity diagram through compact semantic teaching beats. A deterministic compiler will own coordinates, stable child identities, styles, and mathematical relationships. An independent verifier will reject a realization whose geometry, equation, bounds, or identifiers do not satisfy the component's obligations. The compiler will lower each beat into separately committable visual atoms that reuse the existing `ScenePatchDraft` contract and existing SVG/GSAP runtime. This first construction illustrates the identity but does not yet constitute a geometric proof by dissection or rearrangement.

The key research hypothesis is not that streaming diagrams, semantic scene graphs, or interruptible AI tutors are new. A2UI and OpenUI already stream catalog-constrained UI; Penrose already separates mathematical content from visual style; Gemini already represents animation as declarative steps; DrawDash already applies speech-driven diagram suggestions; and PapinAI publicly claims a named-object whiteboard that draws with narration and survives learner interruption. The narrower hypothesis is that a **verified visual-act runtime** can give generative teaching an exact semantic commit frontier. A **teaching beat** is a compact model-authored request such as “relate the three area squares.” A **visual atom** is one compiler-authored, independently meaningful scene mutation such as revealing the square on side `a`. A **commit frontier** is the greatest ordered prefix of visual atoms the browser has acknowledged after canonical terminal styling and a paint barrier. On interruption, the current atom is settled to its canonical terminal state, acknowledged after paint, later atoms are abandoned, and the next generation is based only on that committed semantic prefix.

This is a novelty hypothesis, not a claim that no one has built it. The prototype must make the hypothesis falsifiable. If the mechanism cannot produce a correct, deterministic proof with exact interruption/replay semantics, or if it offers no measurable advantage over the existing raw-coordinate path, it should be discarded before building a general library.

## Progress

- [x] 2026-09-04 23:50 IST: Re-read `.agent/PLANS.md`, confirmed `codex/realtime-scene-core` is clean and matches `origin` at `556a3ec`, and traced the Gate 1 contracts, service acceptance seam, frontend planner, motion executor, and materialized-interruption behavior.
- [x] 2026-09-04 23:55 IST: Reviewed primary and official prior art across Penrose, Gemini, Visual Sketchpad, A2UI, AG-UI, OpenUI, Chalktalk, DrawDash, tldraw, and the current PapinAI product claims; narrowed the prototype away from generic streamed diagrams.
- [x] 2026-09-04 23:55 IST: Chose a Pythagorean vertical slice with compiler-owned geometry, verifier-owned obligations, stable semantic roles, and one-node visual atoms; kept Azure calls and voice integration out of this phase.
- [x] 2026-09-05 00:10 IST: Completed an adversarial architecture cut; separated compilation certificates from browser presentation acknowledgements, required whole-beat preflight and separate beat/atom budgets, and identified the current applied-on-start and false-revision interruption bugs that must be fixed before integration.
- [x] 2026-09-05 00:07 IST: Defined the strict model-authored teaching-beat boundary and immutable semantic state/receipt contracts; pushed `6de8d72` after 47 focused tests and Ruff passed.
- [x] 2026-09-05 00:09 IST: Implemented deterministic Pythagorean lowering in `semantic_compiler.py` and a separate serialized-node oracle in `semantic_verifier.py`; pushed `027914c` after all 193 live-scene backend tests and Ruff passed.
- [x] 2026-09-05 00:09 IST: Proved compactness, deterministic bytes, mathematical invariants, stable IDs, all eight resume prefixes, whole-realization failure before atom construction, and independent rejection of inward geometry, bad ratios, duplicate IDs, labels, bounds, and equations.
- [x] 2026-09-05 00:15 IST: Fixed zero-step interruption and replay in the production stream runtime and the legacy Pythagoras demo; pushed `c347ea6` after 89 live-scene browser tests, ESLint, and TypeScript passed.
- [x] 2026-09-05 00:16 IST: Added one-beat semantic prompting and strict bounded semantic NDJSON parsing; pushed `ae16e30` after 57 focused tests passed with no provider call.
- [x] 2026-09-05 00:21 IST: Added a domain-separated compiler certificate chain over the exact teaching beat, low-level patch, verifier receipt, role ordinal, adjacent semantic states, and previous certificate; pushed `22d4f25` after 152 semantic and 286 live-scene backend tests passed.
- [ ] Integrate semantic NDJSON behind a lab-only authoring mode before `_GenerationState.accept()` without weakening the existing low-level patch contracts or paid-route controls.
- [ ] Carry server-owned beat and atom metadata through the browser runtime, record renderer acknowledgements, and prove interruption/replay uses the exact semantic commit frontier.
- [ ] Exercise the Pythagorean vertical slice in Chromium with no provider call, compare it against the raw-coordinate fixture, and record the keep/discard decision.

## Surprises & Discoveries

- The broad product description is already occupied. PapinAI's public site and architecture articles claim live narrated vector drawing, named spatial objects, collision avoidance, object-relative arrows, shared editing, and mid-sentence interruption that resumes on the existing board. These are vendor claims rather than independently verified evidence, but they are sufficient to reject “an interruptible AI whiteboard tutor” as Murmur's novelty claim.
- A July 2025 defensive publication titled “Real-Time Conversational Diagram Generation System” already combines speech recognition, LLM semantic parsing, live rendering, and persistent memory. Real-time speech-to-diagram generation is prior art even without PapinAI.
- A2UI v1.0 defines streamed create/update/delete operations over catalog-restricted components with stable IDs, progressive rendering, validation, and renderer-to-agent events. Another JSONL component protocol would not be a meaningful contribution.
- The existing Gate 1 renderer already has the beginning of commit semantics: a low-level node is considered applied when its motion begins, interruption settles that node, and scheduled nodes never enter the retained scene. The missing layer is a server-owned mapping from that materialized node to a semantic teaching atom and its verified obligations.
- The older `web/src/lib/scene-kit/` prototype has a useful component vocabulary, but its model-authored props are untyped, several renderers use `Date.now()` identifiers, unknown components can be ignored, and its layout is a single vertical cursor. It should be mined for vocabulary only, not extended as the trustworthy compiler.
- A semantic scene alone does not guarantee correctness. Penrose-style layout constraints can be slow or fail to converge, and DiagrammerGPT shows that a correct plan can still lose relationships during rendering. Gate 1.1 needs a separate realization verifier and bounded deterministic recipes for its first proof.
- Animation is not automatically pedagogically better. Prior educational reviews find that transient or simultaneous motion can overload attention. Atoms therefore need to remain meaningful when frozen, replayable, and aligned to short narration beats.
- `web/src/features/live-scene/svg-motion-executor.ts` currently adds some step IDs to `appliedStepIds` when their animation starts, before completion or a browser paint. This is not a materialization receipt. Fade and scale cancellation can also leave partial presentation styles unless cancellation explicitly settles them.
- `materializeSceneTransition()` currently returns the target revision even when zero steps were applied, and `stream-runtime.ts` can append that as a materialized record. A pre-start interruption can therefore advance semantic history without displaying anything.
- A server cannot certify browser presentation over the current one-way SSE response. The server can issue a compilation certificate bound to canonical patch data; the browser owns the presentation acknowledgement. A production-grade cross-request trust claim will later require an acknowledgement endpoint or a validated chained token.
- The verifier boundary is meaningful only if it cannot consume compiler objects or helpers. `semantic_verifier.py` now accepts serialized node mappings, reparses them through the low-level contract, carries its own analytic constants and calculations, and is guarded by an import-isolation test. Its receipt proves only the listed realization obligations and remains distinct from the versioned compiler-integrity certificate that binds those obligations to a patch and semantic transition.
- Hash continuity is useful but easy to overstate. `murmur-json-v1` now gives the compiler deterministic, domain-separated SHA-256 commitments and fixed Unicode/float vectors, but an unkeyed chain is forgeable by a client that rewrites every link. In this lab it proves internal consistency. Cross-request authenticity still requires a server-held ledger, signature, or MAC before the chain head can be trusted.

## Decision Log

- 2026-09-04, Codex: Treat “verified visual acts with a renderer-acknowledged commit frontier” as the research hypothesis. Do not claim novelty for LLM-to-DSL generation, progressive UI, named scene objects, collision-aware layout, narration synchronization, or interruption independently.
- 2026-09-04, Codex: Start with one executable concept, `pythagorean_area_identity`, instead of designing an open-domain ontology. The construction is rich enough to test geometry, labels, staged reveal, stable identity, and interruption while remaining independently verifiable. Do not call it a proof until a later compiler adds and verifies an area-preserving dissection or rearrangement.
- 2026-09-04, Codex: The model may choose the teaching act, narration, component ID, and target proof stage. It may not author coordinates, colors, path points, side-to-square attachment, the equation, or verification receipts.
- 2026-09-04, Codex: Lower a teaching beat into an ordered sequence of one-operation `ScenePatchDraft` visual atoms. This aligns the semantic commit unit with the current renderer's per-node materialization acknowledgement and avoids pretending that a partially rendered multi-node component is fully committed.
- 2026-09-04, Codex: Generate stable child IDs from the semantic component ID and role, such as `proof__triangle` and `proof__square_a`. Forbid clocks, randomness, provider IDs, and model-authored child IDs.
- 2026-09-04, Codex: Independently verify the lowered nodes before returning any atom. Verification covers right-angle geometry, all three attached square edge lengths, the compiler-authored identity, board bounds including the LaTeX viewport, unique stable IDs, and conservative label separation. Any failed obligation rejects the entire beat without mutating semantic or low-level state.
- 2026-09-05, Codex: Preflight every compiled atom and every intermediate scene prefix against a cloned state, including patch application, budgets, collisions, and wire encoding, before yielding the first atom of a beat. A compilation failure emits none of that beat; a later delivery interruption may intentionally retain an already admitted prefix.
- 2026-09-05, Codex: Count model-authored beats and compiler-authored visual atoms with separate budgets. Reserve the complete atom batch before admission; the existing three-frame model target must never truncate a valid multi-atom beat.
- 2026-09-05, Codex: A compiler-issued certificate proves only that canonical semantic input lowered to patches satisfying the declared visual obligations under a named compiler version. It is not evidence that the browser rendered them. A browser presentation acknowledgement is emitted only after all motion for the atom has canonical terminal styling and one paint barrier has passed.
- 2026-09-05, Codex: Interruption finishes the one currently active atom to its canonical terminal state, waits for the paint acknowledgement, and discards queued atoms. This is the digital equivalent of completing the current chalk stroke. An interruption before any atom starts preserves the exact previous scene and semantic revision and creates no accepted record.
- 2026-09-05, Codex: Verification does not certify model-authored narration. The prototype will state that boundary explicitly; factual narration validation is separate work.
- 2026-09-05, Codex: Keep verifier evidence, compiler integrity, and browser presentation as three distinct claims. The independent verifier issues obligation receipts. The compiler will next bind a receipt, canonical patch digest, compiler version, atom ordinal, and adjacent semantic states into a deterministic certificate. The browser alone can later acknowledge post-paint presentation. A SHA-256 chain binds bytes but is not client authenticity unless a server anchors or signs its head.
- 2026-09-04, Codex: Preserve the proven Gate 1 transport, admission controls, repair loop, `ScenePatchDraft`, frontend scene decoder, transition planner, GSAP executor, and replay ledger. Insert semantic parsing and compilation before `_GenerationState.accept()`; do not replace the safe runtime.
- 2026-09-04, Codex: Keep voice and the cross-modal heard/seen watermark out of implementation scope here. Gate 1.1 will expose a visual commit receipt that the separate voice track can later join with an audio acknowledgement. This avoids coupling two unproven changes while preserving the intended protocol boundary.
- 2026-09-04, Codex: Make no paid model calls during compiler development. A live Azure comparison requires a new explicit budget after deterministic and browser acceptance pass.

## Outcomes & Retrospective

The first backend kernel now exists in pushed commits `6de8d72` and `027914c`. A model-facing beat contains only version, beat ID, short narration, teaching act, component ID, and named reveal stage. The compiler deterministically expands it to an exact missing suffix of up to eight one-node atoms. Before constructing any atom, it serializes the full target prefix and sends it through a separate verifier that recalculates the right angle, 3:4:5 ratio, outward square attachment and edge lengths, label containment and separation, stable IDs, bounds, and exact identity. The current evidence is 59 focused semantic tests and 193 passing live-scene backend tests with Ruff clean.

This is a mechanism checkpoint, not yet a visible product outcome. Versioned compiler certificates now bind every atom to its exact semantic request and verified realization, and zero-step interruption can no longer invent a revision. Atoms have not yet entered the semantic service path, and the browser has not acknowledged terminal post-paint presentation. The immediate next deliverables are all-or-nothing service admission and renderer transaction semantics; together they can make the commit-frontier hypothesis observable.

The prototype will be kept only if it reduces model-authored output substantially, produces byte-for-byte deterministic patches, rejects corrupted geometry before display, and reconstructs the same semantic prefix after interruption and replay. Passing those mechanism checks will not by itself prove learner value or global novelty. A later study must compare comprehension and clarification speed against a static diagram and a progressive but non-interruptible lesson.

## Context and Orientation

`backend/murmur/live_scene/contracts.py` defines the strict low-level board. `SceneState` is an immutable revision containing SVG-like nodes. A model-authored `ScenePatchDraft` currently contains full coordinates, styles, and node data for `put` or `remove` operations. This contract is already mirrored and defended in the browser, so Gate 1.1 compiles into it rather than changing its safety properties.

`backend/murmur/live_scene/prompt.py` currently teaches the model the entire 800 by 600 coordinate grammar. `backend/murmur/live_scene/stream_parser.py` extracts complete NDJSON frames. `backend/murmur/live_scene/service.py` streams those frames and calls `_GenerationState.accept()`, which applies each patch atomically and stamps authoritative lifecycle metadata. The semantic path will parse a smaller teaching-beat frame, compile it into one or more low-level drafts, and feed those drafts through the same acceptance method.

`web/src/lib/live-scene/patch.ts` validates server events. `web/src/lib/live-scene/planner.ts` computes stable-ID enter, update, and remove transitions. `web/src/features/live-scene/svg-motion-executor.ts` reports the IDs of motion steps that actually began or completed. `web/src/features/live-scene/stream-runtime.ts` uses those IDs to reconstruct the retained scene on interruption. Gate 1.1 will add server-owned `beatId`, `atomId`, semantic role, and obligation receipt metadata to accepted atoms, then maintain a semantic prefix beside the existing low-level scene ledger.

`web/src/lib/scene-kit/` is an older frontend-only semantic DSL compiler. It is not in the Gate 1 authoring path and is not trustworthy enough to become the new core, but component names and rendering ideas may be reused. The new compiler belongs on the server because provider output must be validated and lowered before an event is admitted to the authoritative stream.

The first semantic component is `pythagorean_area_identity`. Its compiler-owned roles are ordered as triangle, square on side `a`, its `a²` label, square on side `b`, its `b²` label, square on side `c`, its `c²` label, and the derived identity. A target stage reveals a prefix of those roles. This deliberately makes every renderer acknowledgement correspond to one independently meaningful semantic advance.

## Plan of Work

First, add `semantic_contracts.py` with two separate trust surfaces. `TeachingBeatDraft` is the small object a model may author. For this prototype it contains schema version, beat ID, short narration, a constrained teaching-act enum, and one typed `PythagoreanAreaIdentityDirective` containing only a semantic component ID and a target stage. Server-owned models describe the current revealed-role prefix, compiled visual atoms, and verification receipts. All models must forbid extras, reject non-finite values, remain immutable, and use identifier limits that leave room for deterministic child suffixes.

Second, add `semantic_compiler.py` and `semantic_verifier.py`. The compiler will derive one fixed, centered 3:4 right-triangle construction inside the board's safe area, attach all three squares with vector geometry, place labels in their associated squares, and generate the equation itself. It will compare the requested target stage with the current semantic prefix and produce only missing roles. Each visual atom contains exactly one `put` operation and a deterministic patch ID. The separate verifier will accept only serialized low-level nodes, reparse them, and recalculate the obligations without importing compiler helpers or trusting the directive or a model-authored assertion. The compiler returns atoms only after every obligation passes.

Third, add focused tests in `tests/test_live_scene_semantic_contracts.py` and `tests/test_live_scene_semantic_compiler.py`. Tests will cover extra fields, invalid stages and identifiers, no-op and backward transitions, byte-equivalent repeated compilation, absence of clocks/randomness, stable IDs across stages, exact role order, one operation per atom, all board bounds, correct side/square distances, a right-angle dot product, exact compiler-owned LaTeX, conservative label boxes, and output size relative to the raw-coordinate fixture. A fault-injection test will perturb each class of generated geometry and prove the independent verifier fails closed. Another test will stop after every atom prefix and show that recompiling toward the same stage emits exactly the missing suffix. This proves compiler-prefix behavior, not browser materialization.

Fourth, integrate the compiler behind a lab-only semantic authoring mode. Add a semantic frame parser and prompt rather than overloading the low-level parser. Before emitting anything, compile the complete beat against a cloned semantic and low-level state, apply and wire-encode every intermediate prefix, and reserve its entire atom batch. Update `SceneAuthoringService._stream_attempt()` so one accepted teaching beat can yield several low-level patch events, while the existing patch, revision, wire-size, timeout, repair, and upstream-close limits still apply. Beat budgeting limits model frames; atom budgeting limits accepted renderer mutations. The initial raw-coordinate mode remains available as a rollback baseline in the lab and stays the production default until the semantic path passes its gate.

Fifth, extend the event metadata and frontend ledger. The server, not the model, attaches the beat ID, atom ID, semantic component ID, role, compiler version, canonical patch digest, and verified obligation codes. The browser must not reuse the current applied-on-start set as proof. It records an atom only after every motion step reaches canonical terminal styling and a `requestAnimationFrame` paint barrier passes. Interrupting an active atom first settles it, waits for that acknowledgement, then removes queued work. An interruption before start keeps the exact prior revision and appends no record. Replay must reproduce both the low-level scene and semantic atom ledger. The next request's browser acknowledgement is untrusted input in this lab phase; a later production protocol must bind it to a server ledger or chained token. No audio field will be fabricated; a future voice adapter can join the visual acknowledgement with actual speech-playback acknowledgements.

Finally, add a provider-free semantic fixture to `/labs/live-scene`. It will play the three compact target stages, interrupt after each possible atom, resume to the identity, and replay. Chromium tests will assert zero console errors, no clipped or overlapping elements at desktop and 320-pixel viewports, identical final state after each interruption schedule, and no stale atom after a new generation. Record model-facing byte counts and first-meaningful-ink timings against the raw fixture. Only then decide whether to proceed to a separately budgeted Azure comparison and additional concept families.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` unless a command changes directory explicitly.

After the contract and compiler files are added, run:

    .venv/bin/pytest -q tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py
    .venv/bin/ruff check backend/murmur/live_scene/semantic_contracts.py backend/murmur/live_scene/semantic_compiler.py backend/murmur/live_scene/semantic_verifier.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py

The focused suite must pass without credentials or network access. Commit and push the contract/compiler checkpoint before service integration.

After service integration, run:

    .venv/bin/pytest -q tests/test_live_scene_service.py tests/test_live_scene_stream_parser.py tests/test_live_scene_prompt.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py
    .venv/bin/ruff check backend/murmur/live_scene tests/test_live_scene_service.py tests/test_live_scene_stream_parser.py tests/test_live_scene_prompt.py tests/test_live_scene_semantic_contracts.py tests/test_live_scene_semantic_compiler.py

The existing raw-patch service tests must remain unchanged in meaning, and new tests must show a teaching beat expands into ordered, authoritative scene revisions with bounded cleanup and one repair attempt. Commit and push this checkpoint separately.

After frontend metadata and ledger work, run:

    cd web
    npm test -- --run src/lib/live-scene src/features/live-scene
    npx eslint src/lib/live-scene src/features/live-scene
    npx tsc --noEmit

Then launch the already gated development lab and run its Chromium scenarios. The semantic fixture must make no request to Azure or any other provider. Capture the final scene and interruption-prefix evidence in the plan, then run the full repository gates:

    cd ..
    .venv/bin/pytest -q
    .venv/bin/ruff check .
    cd web
    npm test -- --run
    npm run lint
    npx tsc --noEmit
    npm run build

At each coherent checkpoint, inspect `git diff --check`, commit only intended files, push `codex/realtime-scene-core`, and verify `git rev-parse HEAD` equals `git rev-parse @{u}`.

## Validation and Acceptance

The contract/compiler checkpoint passes only when the model-facing teaching beats contain no coordinates, styles, raw path points, equations, or child IDs; repeated compilation is byte-for-byte deterministic; all generated child IDs remain stable across stages; every atom contains exactly one low-level operation; and a target stage can be resumed from every possible committed prefix by emitting only its missing suffix.

Mathematical acceptance requires an independently recalculated zero dot product at the right angle within tolerance, each generated square edge matching its attached triangle side, the hypotenuse satisfying the derived 3:4:5 ratio, and the identity node being compiler-authored as `a^2+b^2=c^2`. Spatial acceptance requires every primitive and the full 500 by 120 LaTeX viewport to remain inside the 800 by 600 board, all label boxes to remain inside their associated squares, and conservative label boxes not to intersect one another. Deliberately perturbing a point, label, equation, ID, or bound must reject the whole compilation and return no partial atoms.

Runtime acceptance requires zero stale semantic or low-level operations after interruption, exact replay of the committed atom prefix, and a next request whose semantic base contains only acknowledged atoms. Run interruption before start, during motion, after motion completion, and after the paint barrier for every atom, not only at convenient patch boundaries. A zero-atom interruption must preserve the byte-identical scene revision and create no ledger entry. After every cancellation, the actual DOM must equal the canonical committed scene with no partial opacity or scale, temporary incoming/outgoing elements, duplicate IDs, clip-path residue, or hidden stale nodes. The final low-level scene and semantic state must be identical regardless of the interruption schedule.

Product evidence for this prototype is deliberately modest. The compact semantic input should be at least 80 percent smaller in UTF-8 bytes than the equivalent raw-coordinate model frames, first meaningful ink in the deterministic lab must remain within the existing local 100 millisecond target, and the Pythagorean output must receive no critical factual or layout finding in human review. These checks decide whether the mechanism deserves an Azure comparison; they do not prove a complete Gate 1 pass.

The hypothesis is rejected or revised if a semantic beat cannot map cleanly to interruption-safe atoms, verification requires a slow general constraint solver, the component remains only a hard-coded progress slider, or a raw-patch baseline matches its correctness and interruption guarantees with comparable model output and latency. Passing this one fixed construction proves protocol conformance only. Reuse cannot be claimed until a second independently parameterized component uses the same atom, certificate, acknowledgement, and replay machinery unchanged. If the vertical slice passes, the next ExecPlan may test analytic plots, graph algorithms, and causal flows and may define an audiovisual commit watermark with the separate voice pipeline.

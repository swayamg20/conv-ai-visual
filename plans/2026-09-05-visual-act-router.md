# Qualify a small visual-act router before reconnecting generation

## Purpose / Big Picture

Murmur's verified Pythagorean scene compiler and interruption-safe browser runtime work, but the first live semantic model frequently chose the wrong reveal stage and drew an unrelated Pythagorean scene for unsupported prompts. This plan introduces Gate 1.2: a small model-authored routing decision that answers only whether to start a supported visual, continue an existing visual, or abstain. It does not ask the router to narrate, draw, style, or prove anything.

A **visual-act decision** is the strict object returned by the routing model. A start decision selects only a target stage; while one component family is supported, the server owns its kind and stable ID. A continue decision reuses an existing component ID and selects a later target stage. An abstain decision gives one closed reason code and causes no scene mutation. A **semantic prefix** is the server-accepted list of roles already revealed for a component, such as a right triangle followed by its three side squares. The router may only extend that prefix.

The observable outcome of the offline milestones is a provider-neutral router contract, bounded prompt, incremental parser, and state validator whose tests prove that unsupported or backward requests can fail closed without exposing geometry to the model. The observable outcome of the later paid milestone is a sanitized, budget-capped Azure report showing whether this smaller decision surface reaches at least 90% routing and stage accuracy. Only a passing router is eligible to reconnect to the existing compiler and browser runtime.

## Progress

- [x] 2026-09-05 02:53 IST: Re-read `.agent/PLANS.md`, preserved the unrelated dirty voice checkout, and confirmed the clean `codex/realtime-scene-core` worktree matches `origin` at `9460a8e`.
- [x] 2026-09-05 02:54 IST: Established a 137-test semantic baseline covering the current contracts, prompt, parser, and service.
- [x] 2026-09-05 02:57 IST: Defined the three-variant decision union, server-owned component ID allocation, and pure forward-only resolver; 191 contract, prompt, parser, and service tests pass with Ruff clean.
- [x] 2026-09-05 03:01 IST: Generalized the existing bounded semantic NDJSON parser and added the decision parser without duplicating UTF-8, framing, lifecycle, or redaction logic; all 35 legacy and router parser tests pass.
- [x] 2026-09-05 03:05 IST: Added a narration-free routing prompt with symmetric triangle, areas, and identity definitions, explicit unsupported/no-progress abstention, no contentful example, and shared bounded context construction.
- [x] 2026-09-05 03:05 IST: Proved the strict contract, pure resolver, prefix rules, injection-safe prompt construction, parser redaction, and model-surface isolation provider-free; 212 focused semantic tests pass with scoped Ruff checks clean. Natural-language routing accuracy remains a live-corpus question.
- [x] 2026-09-05 03:09 IST: Closed adversarial review findings: start decisions no longer repeat the sole component kind, atom capacity stays server-side, stage semantics and continuation precedence are explicit, fake JSON placeholders are gone, and parser repair hints are record-specific. All 211 focused semantic tests pass with scoped Ruff checks clean.
- [ ] Add a small cost-guarded decision evaluator and dry-run it without provider access.
- [ ] Obtain an explicit spend boundary, run the smaller Azure decision corpus, and record the gate result.
- [ ] If and only if the decision corpus passes, adapt accepted decisions into the existing compiler/runtime and run browser interruption checks.

## Surprises & Discoveries

- The current `TeachingBeatDraft` combines routing, narration, pedagogical act, component identity, and stage selection in one provider frame. The compiler uses narration in every emitted patch and binds the complete beat into its certificate chain, so splitting live narration from visual routing is a separate protocol decision rather than a harmless field move.
- The current system prompt contains one concrete example, and that example is specifically `introduce` plus `triangle`. The live corpus subsequently chose `introduce` for 13 of 19 outputs and `triangle` for 10 of 19. This correlation motivates a balanced router prompt but does not prove the model's internal cause.
- The semantic parser already implements the difficult bounded NDJSON and UTF-8 lifecycle. Gate 1.2 should reuse that framing machinery rather than copying another parser.
- A new component ID has no pedagogical meaning. Letting the model invent one adds a failure mode without adding expressive power, so start decisions can be smaller and the server can allocate the first free stable ID.
- The existing prompt and the new router need identical prompt, semantic-snapshot, and repair-error bounds. Extracting one private context builder preserved all legacy prompt tests and avoided a second validation path.
- Atom capacity is deterministic execution state, not semantic intent. Showing an unexplained atom budget to the model could recreate shallow-stage bias, so the router no longer sees it; server admission remains responsible for checking the resolved suffix.

## Decision Log

- 2026-09-05, Codex: Keep Gate 1.2 decision-only. The model may select `start_visual`, `continue_visual`, or `abstain`; it may not author narration, a teaching act, coordinates, style, equations, child IDs, receipts, revisions, or lifecycle fields.
- 2026-09-05, Codex: Allocate the single supported component kind and IDs for new components on the server. `start_visual` carries only target stage; `continue_visual` must carry the exact ID of an accepted component. These remove model choices that the runtime can derive deterministically.
- 2026-09-05, Codex: Preserve the verified compiler, verifier, certificate chain, SSE contract, and browser runtime until the isolated router passes. This prevents an unqualified semantic change from destabilizing already-passing execution.
- 2026-09-05, Codex: Treat abstention as a successful no-mutation routing outcome, not a provider or compiler error. Unsupported intent and requests that cannot advance the accepted prefix must not trigger a misleading fallback visual.
- 2026-09-05, Codex: Prefer extracting or parameterizing the existing strict NDJSON framing code over creating a parallel copy. Compatibility aliases may remain so the passing teaching-beat path does not require a broad rewrite.
- 2026-09-05, Codex: Do not issue another paid Azure request during the offline milestones. Before the decision corpus, state the exact case count, attempt ceiling, token ceiling, and conservative dollar cap.
- 2026-09-05, Codex: Keep atom capacity out of the model decision. The router selects the requested semantic terminal boundary; the server owns exact suffix size and admission.

## Outcomes & Retrospective

Work is in progress. The existing verified visual runtime remains unchanged and pushed at `9460a8e`. This section will record the router's deterministic and live qualification results, the exact commits shipped, and whether integration was unlocked.

## Context and Orientation

All work occurs in `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` on `codex/realtime-scene-core`. `backend/murmur/live_scene/semantic_contracts.py` contains the current model-authored `TeachingBeatDraft`, the Pythagorean stage vocabulary, semantic scene state, compiler outputs, verifier receipts, and certificate models. `backend/murmur/live_scene/semantic_prompt.py` builds the current one-beat prompt. `backend/murmur/live_scene/semantic_stream_parser.py` reconstructs strict NDJSON frames across arbitrary provider chunks. `backend/murmur/live_scene/service.py` closes one provider beat, preflights it, invokes `compile_teaching_beat()`, and emits all compiler-certified atoms only after the full batch passes.

The new decision contract belongs beside the semantic vocabulary because it uses the same component kind, component identifier, stage, and semantic scene state, but it remains a distinct trust surface from `TeachingBeatDraft`. Prompt construction should remain provider-neutral. The parser should validate a discriminated Pydantic union while preserving the existing maximum frame size, duplicate-key rejection, UTF-8 handling, terminal lifecycle, and sanitized repair hints.

The first supported component remains `pythagorean_area_identity`. This plan does not add another diagram family. It proves that a router can correctly reject other domains and can choose `triangle`, `areas`, or `identity` without having to generate prose or geometry.

## Plan of Work

First, define three immutable strict decision variants. `start_visual` includes only a target stage. `continue_visual` includes an existing component ID and a later target stage. `abstain` includes only a closed reason code. Add a pure validator that checks a decision against the accepted semantic scene: starts receive the single supported kind and first free deterministic server ID, continues must reuse an existing component, targets must strictly extend its role prefix, and abstentions never create a mutation plan.

Next, make the current bounded NDJSON framing reusable for a supplied Pydantic adapter and fixed public error language. Keep `TeachingBeatStreamParser` behavior byte-for-byte compatible, then expose a decision parser through the same core. This is the main code-reduction seam: UTF-8, size, duplicate-key, constant, close, abort, and incremental-frame logic should have one owner.

Then add a balanced routing prompt. It will define all three supported stages with equal prominence, give both start and continue rules, and include explicit unsupported and no-progress abstention. The prompt will contain no narration request and no low-level drawing vocabulary. It will serialize only the validated semantic scene and user prompt as untrusted JSON data. Provider-free tests will inspect the exact model surface and exercise supported, interrupted, completed, unsupported, and injection-shaped cases.

After the offline contract passes, add a small evaluator that calls the routing surface only. Its dry-run must prove no provider or credential access. Its live mode must require explicit acknowledgement and enforce an integer pre-dispatch cost ledger, fixed case and attempt ceilings, sequential execution, sanitized private output, and no hidden SDK retries. The live corpus should contain eight to ten balanced cases rather than repeating the prior twenty-case compiler qualification.

If live decision accuracy reaches at least 90% with every unsupported case abstaining and every resume case reusing the existing component, add a narrow server adapter. It will turn an accepted route into the existing compiler input without moving geometry or verification onto the model surface. The narration protocol will be designed explicitly at that point because current certificates bind narration into each atom; it will not be smuggled into the routing contract merely to avoid that decision.

## Concrete Steps

Run all commands from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core`.

After each offline milestone, run its focused tests and lint:

    .venv/bin/pytest -q tests/test_live_scene_visual_act_router_contracts.py
    .venv/bin/pytest -q tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py
    .venv/bin/ruff check backend/murmur/live_scene tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py
    .venv/bin/ruff format --check backend/murmur/live_scene tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py

Before each commit, run `git diff --check` and inspect `git status --short`. Push each coherent milestone to `origin/codex/realtime-scene-core`, then prove `git rev-parse HEAD` equals `git rev-parse @{u}`.

The exact evaluator command and cap will be added only after its cost math is implemented and tested. A dry-run command must precede any paid command.

## Validation and Acceptance

The contract milestone passes when every variant rejects unknown keys and wrong branch fields; IDs and stages use the existing semantic types; an abstain carries no component or target; and no decision can contain narration, acts, geometry, style, equations, patches, receipts, provider data, or lifecycle state.

The state milestone passes when pure tests cover new starts from an empty scene, exact existing-ID continuation from triangle and partial-area prefixes, every non-forward target, missing and colliding IDs, a completed prefix, capacity limits, and both abstain reasons. Invalid decisions fail before compilation and have no stateful side effects.

The prompt and parser milestone passes when all three stages have balanced selection guidance, unrelated domains route to abstain, current-scene JSON is canonical and bounded, prompt injection remains quoted data, parser chunk boundaries and Unicode boundaries are exhaustive, duplicate keys and non-standard constants fail closed, only one decision is accepted, and the existing teaching-beat parser suite remains unchanged.

The live router gate passes only if every stream has a safe terminal outcome, at least 90% of scored supported cases select the expected route and stage, 100% of unsupported cases abstain, 100% of resume cases reuse the existing component and move strictly forward, no forbidden field crosses the model boundary, and the conservative dispatched cost remains within the newly approved cap. Cold and warm latency must be reported separately. A failure preserves the current compiler/runtime and triggers another router revision rather than widening the component vocabulary.

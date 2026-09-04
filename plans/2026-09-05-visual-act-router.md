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
- [x] 2026-09-05 03:14 IST: Added an isolated provider-neutral routing engine with temperature zero, an absolute per-attempt deadline, first-valid-decision early close, one sanitized repair, borrowed-client ownership, and fixed public failures. All 23 focused engine tests pass without provider access.
- [x] 2026-09-05 03:28 IST: Added and adversarially reviewed a 10-case router evaluator with per-dispatch pacing, an exact 20-attempt ledger, strict category and warm-latency gates, hashed/private evidence, and no provider construction in dry-run. The pinned worst case is 105,772 input-bound plus 40,960 output-bound tokens, or USD 0.040441800; 267 focused tests pass and no provider was called.
- [x] 2026-09-05 03:31 IST: Ran the approved Azure corpus from clean source `b66c9dc` under a USD 0.05 hard cap. Gate 1.2 passed: 10/10 routing expectations, 8/8 supported-vocabulary cases, 2/2 unsupported abstentions, 1/1 no-progress abstention, and 2/2 exact resumes; warm median was 1,309.380 ms and p95 was 2,704.701 ms. Eleven of twenty possible calls were reserved, with one successful repair and a dispatched upper bound of USD 0.021856950.
- [x] 2026-09-05 03:49 IST: Added a pure server-owned lowering seam from a resolved route to the existing verified compiler input. Target stage now deterministically selects the teaching act and bounded narration, generation selects a bounded beat ID, and continuation compilation emits only the exact missing suffix. Eighty resolver, lowering, and compiler tests pass with Ruff clean.
- [x] 2026-09-05 03:52 IST: Added a semantic-only declined terminal for successful abstention. Its strict backend and browser contracts preserve an unchanged paired frontier, retain a closed reason code, render a calm `No visual change` state, ignore late frames, and allow the next generation immediately. Fifty-seven backend and forty-nine frontend tests pass; TypeScript type checking is clean.
- [x] 2026-09-05 03:55 IST: Hardened the routing boundary before service integration: a repair lifecycle step now reaches consumers before the second provider dispatch, the qualified 2,048-token router ceiling is explicit for downstream caps, and a second component start fails closed until deterministic layout can prevent overlapping geometry. One hundred focused engine, resolver, prompt, and lowering tests pass provider-free.
- [ ] If and only if the decision corpus passes, adapt accepted decisions into the existing compiler/runtime and run browser interruption checks.

## Surprises & Discoveries

- The current `TeachingBeatDraft` combines routing, narration, pedagogical act, component identity, and stage selection in one provider frame. The compiler uses narration in every emitted patch and binds the complete beat into its certificate chain, so splitting live narration from visual routing is a separate protocol decision rather than a harmless field move.
- The current system prompt contains one concrete example, and that example is specifically `introduce` plus `triangle`. The live corpus subsequently chose `introduce` for 13 of 19 outputs and `triangle` for 10 of 19. This correlation motivates a balanced router prompt but does not prove the model's internal cause.
- The semantic parser already implements the difficult bounded NDJSON and UTF-8 lifecycle. Gate 1.2 should reuse that framing machinery rather than copying another parser.
- A new component ID has no pedagogical meaning. Letting the model invent one adds a failure mode without adding expressive power, so an empty scene starts at the server-owned stable ID `areas`.
- The existing prompt and the new router need identical prompt, semantic-snapshot, and repair-error bounds. Extracting one private context builder preserved all legacy prompt tests and avoided a second validation path.
- Atom capacity is deterministic execution state, not semantic intent. Showing an unexplained atom budget to the model could recreate shallow-stage bias, so the router no longer sees it; server admission remains responsible for checking the resolved suffix.
- A provider may place a valid first decision and trailing chatter in the same chunk. Splitting only at LF boundaries lets the engine resolve and close after that first decision, avoiding both the prior trailing-frame failure mode and extra stream latency.
- The first evaluator draft reused several phrases from the router's own stage table. Those cases could reward lexical copying rather than semantic routing, so the final corpus includes an indirect prompt for every stage, an implicit continuation, and a completed-prefix `no_forward_progress` case.
- Quota pacing must happen at every actual provider dispatch, not once per evaluation case. Otherwise an engine-owned repair could silently exceed the ten-requests-per-minute deployment limit even though the dollar ledger remained valid.
- The completed-prefix request needed the one allowed repair before returning `no_forward_progress`; the other nine cases passed on their first attempt. The repaired result is correct, but it identifies completed-state abstention as the weakest prompt edge in this sample.
- The first compiler uses fixed absolute coordinates. Allocating `areas-2` would therefore pass verification but place an indistinguishable second construction directly over `areas`; second starts must abstain until deterministic layout is part of the compiler.
- A router-owned retry cannot be represented faithfully if `route()` returns only after both attempts. The engine now exposes a lifecycle stream so the service can emit its repair boundary before, rather than after, the second paid dispatch.

## Decision Log

- 2026-09-05, Codex: Keep Gate 1.2 decision-only. The model may select `start_visual`, `continue_visual`, or `abstain`; it may not author narration, a teaching act, coordinates, style, equations, child IDs, receipts, revisions, or lifecycle fields.
- 2026-09-05, Codex: Keep the supported component kind and stable ID server-owned. `start_visual` carries only target stage and is valid only on an empty semantic scene; `continue_visual` must carry the exact accepted ID. Separate constructions remain unsupported until the compiler owns non-overlapping layout.
- 2026-09-05, Codex: Preserve the verified compiler, verifier, certificate chain, SSE contract, and browser runtime until the isolated router passes. This prevents an unqualified semantic change from destabilizing already-passing execution.
- 2026-09-05, Codex: Treat abstention as a successful no-mutation routing outcome, not a provider or compiler error. Unsupported intent and requests that cannot advance the accepted prefix must not trigger a misleading fallback visual.
- 2026-09-05, Codex: Prefer extracting or parameterizing the existing strict NDJSON framing code over creating a parallel copy. Compatibility aliases may remain so the passing teaching-beat path does not require a broad rewrite.
- 2026-09-05, Codex: Do not issue another paid Azure request during the offline milestones. Before the decision corpus, state the exact case count, attempt ceiling, token ceiling, and conservative dollar cap.
- 2026-09-05, Codex: Keep atom capacity out of the model decision. The router selects the requested semantic terminal boundary; the server owns exact suffix size and admission.
- 2026-09-05, Codex: Keep asynchronous provider orchestration in an isolated `visual_act_engine.py`, not in the pure resolver or current compiler service. The engine borrows its client, fixes temperature at zero, permits one repair only for rejected decisions, and closes the stream immediately after the first resolved decision.
- 2026-09-05, Codex: Keep the paid corpus at ten cases and twenty possible dispatches: five fresh starts, two unrelated unsupported intents, two exact resumes, and one completed-prefix no-progress request. A qualifying run needs all eight supported-vocabulary expectations, both unsupported abstentions, the no-progress abstention, and both resume-ID checks to pass.
- 2026-09-05, Codex: Treat warm routing latency as part of Gate 1.2, with median at most 1,500 ms and p95 at most 3,000 ms. Report the first cold request separately so initialization does not distort the warm gate.
- 2026-09-05, Codex: Reuse the proven semantic probe's pricing, integer reservation ledger, hashing, git evidence, and atomic private writer. Parameterize only its total-attempt ceiling instead of extracting a broad shared framework.
- 2026-09-05, Codex: Gate 1.2 passed and unlocks the narrow compiler/runtime adapter. The paid result does not itself qualify compiler output, SSE behavior, browser rendering, or interruption handling; those remain the next gate.
- 2026-09-05, Codex: Lower accepted routes into the existing `TeachingBeatDraft` instead of creating a second compiler surface. The server derives act, narration, directive, and `route-{generation:x}` beat identity; abstentions cannot cross this lowering boundary.
- 2026-09-05, Codex: Represent abstention with semantic-only `semantic_scene_stream_declined`, not zero-patch completion or failure. It carries the unchanged revision and a closed reason, records no completion metrics, does not enter the raw protocol, and leaves the browser startable from the exact same frontier.
- 2026-09-05, Codex: Expose the router's one repair as an engine lifecycle step before its second dispatch. The synchronous `route()` convenience API consumes that step for evaluator compatibility; the live semantic service can translate it into SSE without misreporting retry timing.

## Outcomes & Retrospective

The isolated router gate passed against Azure `gpt-oss-120b`. The private report at `var/live-scene/evaluations/20260904T215957.626451Z/report.json` is mode `0600`, records clean source commit `b66c9dc307964cb77c8bab0af8068baea95e1e3f`, and has evidence scope `provider_to_visual_act_parser_and_resolver`. It contains no raw prompts or provider output. Its USD 0.021856950 figure is a conservative reservation upper bound, not actual billed usage. Compiler/runtime integration is now unlocked but not yet qualified.

## Context and Orientation

All work occurs in `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` on `codex/realtime-scene-core`. `backend/murmur/live_scene/semantic_contracts.py` contains the current model-authored `TeachingBeatDraft`, the Pythagorean stage vocabulary, semantic scene state, compiler outputs, verifier receipts, and certificate models. `backend/murmur/live_scene/semantic_prompt.py` builds the current one-beat prompt. `backend/murmur/live_scene/semantic_stream_parser.py` reconstructs strict NDJSON frames across arbitrary provider chunks. `backend/murmur/live_scene/service.py` closes one provider beat, preflights it, invokes `compile_teaching_beat()`, and emits all compiler-certified atoms only after the full batch passes.

The new decision contract belongs beside the semantic vocabulary because it uses the same component kind, component identifier, stage, and semantic scene state, but it remains a distinct trust surface from `TeachingBeatDraft`. Prompt construction should remain provider-neutral. The parser validates a discriminated Pydantic union while preserving the existing maximum frame size, duplicate-key rejection, UTF-8 handling, terminal lifecycle, and sanitized repair hints. `backend/murmur/live_scene/visual_act_engine.py` owns only bounded provider streaming, one repair, and pure decision resolution; it deliberately does not import the compiler, verifier, service, wire format, or SSE layer.

The first supported component remains `pythagorean_area_identity`. This plan does not add another diagram family. It proves that a router can correctly reject other domains and can choose `triangle`, `areas`, or `identity` without having to generate prose or geometry.

## Plan of Work

First, define three immutable strict decision variants. `start_visual` includes only a target stage. `continue_visual` includes an existing component ID and a later target stage. `abstain` includes only a closed reason code. Add a pure validator that checks a decision against the accepted semantic scene: an empty scene starts the single supported component at server ID `areas`, later turns must continue that exact component, targets must strictly extend its role prefix, and abstentions never create a mutation plan.

Next, make the current bounded NDJSON framing reusable for a supplied Pydantic adapter and fixed public error language. Keep `TeachingBeatStreamParser` behavior byte-for-byte compatible, then expose a decision parser through the same core. This is the main code-reduction seam: UTF-8, size, duplicate-key, constant, close, abort, and incremental-frame logic should have one owner.

Then add a balanced routing prompt. It will define all three supported stages with equal prominence, give both start and continue rules, and include explicit unsupported and no-progress abstention. The prompt will contain no narration request and no low-level drawing vocabulary. It will serialize only the validated semantic scene and user prompt as untrusted JSON data. Provider-free tests will inspect the exact model surface and exercise supported, interrupted, completed, unsupported, and injection-shaped cases.

Wrap those pieces in a small provider-neutral engine before evaluation. Each attempt gets one absolute deadline; a first rejected decision may receive one fresh repair against the same accepted snapshot, while provider errors and timeouts do not create a hidden second call. The first resolved decision wins and closes the upstream stream immediately. The caller retains client ownership so the evaluator can inject its exact pre-dispatch cost ledger.

After the offline contract passes, add a small evaluator that calls the routing surface only. Its dry-run must prove no provider or credential access. Its live mode must require explicit acknowledgement and enforce an integer pre-dispatch cost ledger, fixed case and attempt ceilings, sequential execution, sanitized private output, and no hidden SDK retries. The live corpus should contain eight to ten balanced cases rather than repeating the prior twenty-case compiler qualification.

If live decision accuracy reaches at least 90% with every unsupported case abstaining and every resume case reusing the existing component, add a narrow server adapter. It will turn an accepted route into the existing compiler input without moving geometry or verification onto the model surface. The narration protocol will be designed explicitly at that point because current certificates bind narration into each atom; it will not be smuggled into the routing contract merely to avoid that decision.

## Concrete Steps

Run all commands from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core`.

After each offline milestone, run its focused tests and lint:

    .venv/bin/pytest -q tests/test_live_scene_visual_act_router_contracts.py
    .venv/bin/pytest -q tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py
    .venv/bin/pytest -q tests/test_live_scene_visual_act_engine.py
    .venv/bin/ruff check backend/murmur/live_scene tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py
    .venv/bin/ruff format --check backend/murmur/live_scene tests/test_live_scene_visual_act_router_contracts.py tests/test_live_scene_visual_act_router_prompt.py tests/test_live_scene_visual_act_router_parser.py

Before each commit, run `git diff --check` and inspect `git status --short`. Push each coherent milestone to `origin/codex/realtime-scene-core`, then prove `git rev-parse HEAD` equals `git rev-parse @{u}`.

The provider-free dry-run is:

    .venv/bin/python scripts/manual/probe_visual_act_router.py --max-cost-usd 0.041 --case-limit 10 --max-tokens 2048 --dry-run

It must report corpus SHA-256 `374aa407164cd9ca84ec8a311792c895ea39152dcdb2daa90dfec0a931e6e549`, twenty reserved attempts, and a worst-case cost of USD 0.040441800 before any paid command is considered.

## Validation and Acceptance

The contract milestone passes when every variant rejects unknown keys and wrong branch fields; IDs and stages use the existing semantic types; an abstain carries no component or target; and no decision can contain narration, acts, geometry, style, equations, patches, receipts, provider data, or lifecycle state.

The state milestone passes when pure tests cover a start from an empty scene, rejection of a second overlapping start, exact existing-ID continuation from triangle and partial-area prefixes, every non-forward target, a missing ID, a completed prefix, and both abstain reasons. Invalid decisions fail before compilation and have no stateful side effects.

The prompt and parser milestone passes when all three stages have balanced selection guidance, unrelated domains route to abstain, current-scene JSON is canonical and bounded, prompt injection remains quoted data, parser chunk boundaries and Unicode boundaries are exhaustive, duplicate keys and non-standard constants fail closed, only one decision is accepted, and the existing teaching-beat parser suite remains unchanged.

The live router gate passes only if every stream has a safe terminal outcome, at least 90% of scored supported cases select the expected route and stage, 100% of unsupported cases abstain, the completed-prefix case abstains for no forward progress, 100% of resume cases reuse the existing component and move strictly forward, no forbidden field crosses the model boundary, warm median decision latency is at most 1,500 ms, warm p95 is at most 3,000 ms, and the conservative dispatched cost remains within the approved cap. With exactly eight supported-vocabulary cases, the 90% threshold quantizes to eight out of eight. Cold and warm latency are reported separately. A failure preserves the current compiler/runtime and triggers another router revision rather than widening the component vocabulary.

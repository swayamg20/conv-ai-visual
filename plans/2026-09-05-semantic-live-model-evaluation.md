# Qualify Azure semantic visual acts under a hard local spend ceiling

## Purpose / Big Picture

Murmur now has a provider-free verified visual-act fixture, but it has not proved that a live model can reliably choose the small semantic teaching beat that drives it. This evaluation sends twenty bounded prompts through the production `SceneAuthoringService`, the production semantic SSE encoder, and Azure `gpt-oss-120b`. The auth-free HTTP lab route is checked separately with a provider-free schema probe; the paid corpus runs in-process so a budget wrapper can reserve every provider dispatch before it occurs. An operator will receive a sanitized report showing validity, repair rate, provider-to-server latency, selected act and reveal stage, and a conservative cost ceiling. The report will not contain credentials, raw rejected provider output, or private chain-of-thought.

A **provider attempt** is one completion stream opened by the backend. A request normally uses one attempt and may use one additional repair attempt after a rejected semantic frame. A **cost ledger** is an application-side conservative bound calculated before each request from the maximum completion-token setting and a deliberately pessimistic input-token estimate. Azure does not synchronously enforce this dollar ceiling, so the client must stop before issuing a request whose worst-case cost would exceed the configured budget.

This is a model-to-server qualification, not yet a complete browser or learner-value test. Success means the live model emits compiler-accepted teaching beats cheaply and predictably enough to justify browser interruption testing. It does not prove that the narration is factually correct or that learners understand the lesson better.

## Progress

- [x] 2026-09-05 01:52 IST: Verified the existing backend on port 8000 predates the semantic route, left it untouched, and started current code separately on `127.0.0.1:8001` with Azure credentials kept process-only and `MURMUR_SCENE_LLM_MAX_TOKENS=2048`.
- [x] 2026-09-05 01:54 IST: Probed `/api/live-scenes/lab/semantic/stream` with an invalid body and received HTTP 422, proving the guarded route is active without making a provider call.
- [x] 2026-09-05 01:56 IST: Confirmed the deployed `murmur-gpt-oss-120b` uses Azure GlobalStandard token billing. The exact fixed corpus reserves 177,096 conservative input tokens and 81,920 output tokens across forty possible attempts, or USD 0.0757164 at the pinned retail rates.
- [x] 2026-09-05 02:02 IST: Disabled hidden OpenAI SDK retries for the Azure GPT-OSS scene client, passed 44 focused provider/API/probe tests plus Ruff, and pushed `407bb22` with exact local/remote parity.
- [x] 2026-09-05 02:07 IST: Added the sequential semantic evaluator with an explicit paid-run acknowledgement, twenty fixed cases including four exact resume prefixes, forty-attempt ceiling, integer pre-dispatch cost ledger, canonical SSE round-trip, and private atomic artifacts. Ten offline tests plus Ruff and formatting passed.
- [x] 2026-09-05 02:08 IST: Ran the first paid calibration. It completed on attempt one with the expected `introduce` / `triangle` beat and one compiler-certified atom, so the bounded campaign continued.
- [x] 2026-09-05 02:11 IST: Completed all twenty cases sequentially from clean pushed source `3bdf246`. Nineteen completed, the deliberate backward request failed closed after one repair, all twenty had safe terminals, and twenty-one provider attempts reserved at most USD 0.0389322.
- [x] 2026-09-05 02:12 IST: Calculated the split result: protocol and resume-prefix behavior passed; strict semantic expectation accuracy was 10/18, requested-stage accuracy was 10/17, and the two unsupported prompts were forced into unrelated Pythagorean output. Median server first atom was 1,485.046 ms and p95 was 3,450.351 ms, so the server qualification failed.
- [ ] Record the decision, run focused regression checks, commit coherent checkpoints, push, and prove local/remote parity.

## Surprises & Discoveries

- The backend process originally serving port 8000 was nearly eight hours old. Its raw lab route returned schema validation while its semantic lab route returned 404, so using it would have produced a false product failure rather than a model result.
- The OpenAI-compatible streaming adapter does not currently request or expose token usage. This evaluation can report an enforced worst-case bound, but it cannot honestly report exact Azure billed tokens or exact cost.
- The semantic service may make one repair attempt. Counting UI requests alone would understate spend, so the evaluator must count a `scene_stream_repairing` event as one additional provider attempt.
- The OpenAI Python SDK retries selected failures twice by default. Before `407bb22`, a single service-owned provider attempt could therefore dispatch up to three HTTP requests. The scene-specific Azure client now explicitly sets the SDK retry ceiling to zero; the semantic service remains the sole owner of its one visible repair attempt.
- Reserving all forty possible attempts with a byte-as-token input bound and 2,048 framing tokens per attempt costs USD 0.0757164 for this exact corpus. The earlier approximate USD 0.06 estimate was not conservative enough, so the executable cap is USD 0.08 while the user authorization remains USD 0.50.
- The model's structural behavior was much stronger than its semantic selection. Every normal case produced a schema-valid beat without repair, all three interrupted-prefix cases reused the exact component and emitted only the missing suffix, and the backward case produced no atom after two rejected attempts. However, the model chose `introduce` for 13/19 emitted beats and `triangle` for 10/19, even when prompts explicitly requested areas or the identity.
- The single triangle/introduce example in the system prompt appears to anchor model choices. This is an inference from the 13/19 `introduce` and 10/19 `triangle` skew, not direct evidence about the model's internal reasoning.
- The cold calibration case took 3,450.351 ms and made nearest-rank p95 equal that maximum. The remaining eighteen completed cases had a 1,483.811 ms median and 1,695.719 ms maximum. The official full-corpus p95 still fails; the split suggests connection warm-up rather than sustained tail latency is the first latency hypothesis to test.
- Both unsupported prompts completed as Pythagorean content—one full identity and one triangle. A closed component vocabulary without an explicit abstain or route outcome is structurally safe but product-wrong outside its domain.

## Decision Log

- 2026-09-05, Codex: Use the existing GlobalStandard Azure deployment rather than managed GPU compute. The live deployment is token-priced and already succeeded in a read-only Azure preflight.
- 2026-09-05, Codex: Lower the live server completion ceiling from 4096 to 2048 tokens for this qualification. The semantic contract is one compact JSON line, so the smaller cap materially reduces worst-case spend while leaving ample room for valid output and low-reasoning overhead.
- 2026-09-05, Codex: Keep the old server intact and run current code on port 8001. This avoids disrupting the user's existing local session and makes the evaluated commit unambiguous.
- 2026-09-05, Codex: Use the HTTP route only for a provider-free schema preflight, then run paid cases directly through `SceneAuthoringService` behind a `BudgetedSceneModelClient`. This preserves the same prompt/parser/compiler/verifier path while making every initial and repair dispatch synchronously cost-admitted. Each typed event is still encoded and reparsed through the production SSE contract.
- 2026-09-05, Codex: Start with one paid calibration request. Continue automatically only if it reaches a canonical completed or failed terminal event without transport corruption; otherwise preserve budget for diagnosis.
- 2026-09-05, Codex: Pin the 2026-03-01 Azure GlobalStandard rate at USD 0.15 per million input tokens and USD 0.60 per million output tokens, use integer nano-USD arithmetic, and force a pricing review after 2026-10-01 rather than accepting caller-supplied cheaper rates.
- 2026-09-05, Codex: Mark this live semantic server qualification **failed, revise the planner, keep the verified runtime**. The compiler, verifier, certificate chain, exact resume suffixes, wire contract, and fail-closed backward behavior all passed. Model-authored stage selection, unsupported-domain handling, and cold-start latency did not.
- 2026-09-05, Codex: Do not add another visual component or spend more on browser live runs yet. First add a planner outcome that can explicitly decline unsupported intent, remove the triangle-only example bias, and separate target-stage selection from narration wording. Requalify that small decision surface before reconnecting it to the already-passing compiler/runtime.

## Outcomes & Retrospective

The bounded Azure evaluation completed all twenty cases from clean pushed commit `3bdf246`. The cost guard pre-reserved a worst case of USD 0.0757164 for forty possible attempts. The service actually dispatched twenty-one attempts—one for each case plus the intentional backward request's repair—whose conservative byte-and-output-token ceiling is USD 0.0389322. This is an enforced upper bound, not measured Azure billing, because the current streaming adapter does not expose usage.

Runtime safety passed. All twenty streams reached a canonical terminal; nineteen produced compiler-certified atoms on their first provider attempt; no unexpected repair occurred; every emitted event survived the production semantic SSE encoder and incremental parser; and the complete-prefix backward request failed after repair with zero atoms and revision eight unchanged. The B1, B3, and B7 resume cases all reused component `areas` and emitted exactly the expected six-, four-, and one-atom suffixes.

Semantic planning failed. Only 10/18 scored cases satisfied their complete expected behavior, and only 10/17 supported completion cases selected the requested reveal stage. Fresh supported prompts matched stage in 7/14 cases, while all three resume cases matched. The model defaulted to `introduce` in 13/19 outputs and `triangle` in 10/19. All four trust-boundary prompts remained structurally safe, but only one selected the requested identity stage. Both deliberately unsupported prompts generated Pythagorean content rather than declining or routing elsewhere.

Latency narrowly missed the median target and clearly missed the p95 target: 1,485.046 ms median versus the 1,500 ms limit, and 3,450.351 ms p95 versus the 3,000 ms limit. The 3.45-second cold calibration was the only result above 1.70 seconds; excluding it, eighteen warm completions had a 1,483.811 ms median and 1,695.719 ms maximum. That split is diagnostic only and does not change the failed full-corpus gate.

The outcome is **keep the verified visual-act runtime, revise the semantic planner**. The next implementation should add a typed abstain/route result, use balanced decision guidance for triangle/areas/identity instead of a single triangle example, and make stage selection an explicit small decision before generating narration. It should be tested provider-free, then re-run against a smaller bounded decision corpus before any second component family or live-browser qualification.

## Context and Orientation

`backend/murmur/api/routers/live_scenes.py` exposes `POST /api/live-scenes/lab/semantic/stream` only for loopback clients when development lab mode is explicitly enabled. The route shares `SceneAuthoringAdmission` with the raw paid lab and returns strict data-only server-sent events.

`backend/murmur/live_scene/semantic_service_contracts.py` defines the request and response schema. Every request carries a user prompt, a positive generation number, an immutable low-level `baseScene`, and a matching `baseSemanticScene`. Sixteen cases begin from two empty revision-zero scenes. Four cases use exact compiler-generated prefixes after atoms one, three, seven, and eight to exercise interruption resume and backward-progress rejection.

`backend/murmur/live_scene/semantic_prompt.py` asks the provider for exactly one `TeachingBeatDraft`. The provider chooses a short narration, one of four teaching acts, and a target stage for the single `pythagorean_area_identity` component. It cannot choose coordinates, styles, equations, child IDs, verifier receipts, or lifecycle fields. `backend/murmur/live_scene/service.py` parses that frame, may request one repair after structural rejection, compiles it deterministically, verifies the full construction, and emits compiler-certified visual atoms.

The evaluator belongs at `scripts/manual/probe_semantic_live_scene.py`. Its deterministic guards belong in `tests/test_live_scene_semantic_probe.py`. Live output belongs only under ignored `var/live-scene/`; the checked-in plan records aggregated, sanitized evidence.

## Plan of Work

First, add a twenty-case corpus focused on what the current closed semantic vocabulary can actually express. Cases will vary wording, requested reveal depth, pedagogical act, ambiguity, prompt injection, requests for forbidden coordinates or styles, unrelated domains, and terse or verbose phrasing. These are independent model-routing cases, not twenty different diagram families.

Next, implement an in-process evaluator that refuses more than twenty cases or forty provider attempts, requires the exact acknowledgement `I_ACCEPT_PROVIDER_COST`, and reserves worst-case spend immediately before every provider stream. It will pass each service event through the production SSE encoder and a bounded incremental decoder, validate it with the production Pydantic adapter, count actual wrapper dispatches, and retain only prompt hashes, case categories, event types, timings, safe failure codes, semantic act/stage/role metadata, final revisions, and certificate continuity indicators.

The evaluator will first run in dry-run mode. Focused tests will prove it performs no network request in that mode, rejects missing acknowledgement and excessive budgets, stops before cost or attempt ceilings, parses fragmented SSE records, rejects malformed or non-terminal streams, and redacts prompts and provider bodies from the artifact.

Then issue the full live command. Its first case is the calibration gate: the evaluator continues only if that case completes with the expected triangle beat. Remaining cases run sequentially, and starts are paced for the deployment's ten-requests-per-minute quota. Summarize first-atom and terminal latency, initial-attempt validity, repair count, completion count, generated reveal stages and acts, and the conservative cost bound.

Finally, update this living plan with the observed result and decision. Commit and push the harness/tests before or immediately after calibration, then commit and push the sanitized aggregate conclusion separately. Verify that `HEAD` equals the upstream branch after every push and that no credential or Azure key entered the diff.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core`.

Run the evaluator without network access first:

    .venv/bin/python scripts/manual/probe_semantic_live_scene.py --max-cost-usd 0.08 --case-limit 20 --max-tokens 2048 --dry-run

Run its deterministic checks:

    .venv/bin/pytest -q tests/test_live_scene_semantic_probe.py
    .venv/bin/ruff check scripts/manual/probe_semantic_live_scene.py tests/test_live_scene_semantic_probe.py

After those pass, run the sequential corpus with the first case acting as an automatic calibration gate:

    .venv/bin/python scripts/manual/probe_semantic_live_scene.py --max-cost-usd 0.08 --case-limit 20 --max-tokens 2048 --acknowledge-paid-provider I_ACCEPT_PROVIDER_COST

Artifacts default to `var/live-scene/evaluations/` and remain untracked.

After recording results, run the existing semantic service/API regression checks:

    .venv/bin/pytest -q tests/test_live_scene_semantic_api.py tests/test_live_scene_semantic_service.py tests/test_live_scene_semantic_probe.py
    .venv/bin/ruff check backend/murmur/live_scene backend/murmur/api/routers/live_scenes.py scripts/manual/probe_semantic_live_scene.py tests/test_live_scene_semantic_probe.py

Inspect `git diff --check`, commit only intended files, push `codex/realtime-scene-core`, and verify `git rev-parse HEAD` equals `git rev-parse @{u}`.

## Validation and Acceptance

The harness is accepted only if dry-run cannot create a provider client; live mode requires the exact acknowledgement; at most twenty cases and forty provider attempts can be recorded; each request is issued sequentially; SDK retries are zero; and a stream that would cross either the dollar ceiling or attempt ceiling is refused before delegation. The result artifact must not contain prompt text, credentials, endpoint hostnames, raw provider output, repair error prose, or exception messages.

Every service event must survive the production semantic SSE encoder and bounded incremental decoder, generation and revisions must remain coherent, and each case must end in exactly one completed or failed terminal. The wrapper's immutable reservations are the authoritative provider-attempt count; a repair event must agree with the second dispatch. Any decoding, validation, or missing-terminal fault ends the live run rather than consuming the remaining corpus.

The live model qualification passes provisionally if all twenty cases reach safe terminal events, the first nineteen complete with compiler-certified atoms, the deliberate completed-prefix backward request fails after repair without mutation, at least eighteen completing cases are valid on the first provider attempt, and the median first-atom latency is at most 1500 milliseconds with p95 at most 3000 milliseconds. These reuse the earlier Gate 1 transport targets so the semantic redesign is directly comparable. Failing latency with high structural validity supports separating a fast talker/planner from a slower reasoner; structural failures support revising the prompt or model choice before browser work. The two unsupported-domain prompts remain diagnostic product failures because the current vocabulary has no abstention or routing contract.

The conservative cost bound, not an inferred exact bill, must remain at or below USD 0.08 for this exact run and below the user's USD 0.50 authorization. Passing this server-side qualification unlocks browser interruption and post-paint testing with selected live cases. It does not unlock production, voice coupling, a general-library claim, or a learner-effectiveness claim.

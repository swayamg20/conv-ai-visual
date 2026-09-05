# Promote verified visual lessons into the authenticated product

## Purpose / Big Picture

Murmur already proves, in a guarded development lab, that a small language-model decision can start, continue, or decline a visual lesson while the server owns the actual diagram. The server deterministically compiles the selected lesson stage, verifies its geometry and semantic obligations, and streams separately committable visual atoms to an interruption-safe browser runtime. The real `/canvas/generate` page still bypasses that safer path and lets the model author raw scene patches.

This plan delivers Gate 1.3: a signed-in learner opens `/canvas/generate`, asks a supported Pythagorean question, watches verified visual atoms appear progressively, interrupts without losing already presented work, and submits a follow-up that continues from the exact browser-held visual frontier. A **visual frontier** is the paired low-level scene and semantic scene accepted by the current mounted browser runtime, including the compiler certificate-chain head. Unsupported or non-progressing requests decline without changing that frontier.

This is an authenticated, ephemeral product slice. It does not claim that the server persists or independently anchors the frontier across refreshes, devices, or processes. It does not connect voice, add more component families, or generalize the compiler. Those changes follow only after this narrow interaction works on the real product surface.

## Progress

- [x] 2026-09-06 01:35 IST: Created clean worktree `conv-ai-visual-scene-product` and branch `codex/verified-visual-product` from merged `origin/main` at `5699731`, leaving the dirty voice worktree untouched.
- [x] 2026-09-06 01:40 IST: Re-read `.agent/PLANS.md` and mapped the authenticated raw route, guarded semantic lab route, Firebase header path, semantic runtime, and reusable API/component/browser tests.
- [ ] Add and qualify the authenticated semantic product endpoint without changing the raw rollback endpoint.
- [ ] Switch `/canvas/generate` to the semantic runtime with an explicit product endpoint and supported suggestions.
- [ ] Prove progressive presentation, interruption, exact in-memory continuation, replay, decline, and responsive behavior with provider-free tests.
- [ ] Run the full backend/frontend gates and record the resulting pushed commit SHAs.
- [ ] After separate cost approval, run one small signed-in Azure browser qualification and record only sanitized latency/outcome evidence.

## Surprises & Discoveries

- The semantic browser runtime is already product-capable: it sends paired low-level and semantic bases, rejects stale generations, retains only presented atoms on interruption, resumes exact prefixes, replays without network access, and handles declined requests without mutation. The missing product work is primarily routing, authentication coverage, and truthful UI copy rather than another runtime.
- `web/src/features/live-scene/model-stream.ts` hard-codes semantic transport to the auth-free lab URL, while its raw transport already distinguishes product and development-lab endpoints. Semantic callers need the same explicit endpoint choice so product code cannot accidentally call the lab.
- Existing Playwright coverage exercises the guarded lab rather than Firebase authentication. Product authentication can be deterministically proven at the FastAPI route and React wrapper boundaries without adding a production auth bypass. A real signed-in browser smoke remains a separate environment-backed check.
- The client-supplied compiler certificate chain protects structural continuity within the mounted runtime but is not a server-persisted or keyed proof of prior authenticated history. Product language must say “continue this board” rather than promise cross-session persistence.

## Decision Log

- 2026-09-06, Codex: Add a distinct authenticated `POST /api/live-scenes/semantic/stream` route and retain `POST /api/live-scenes/stream` unchanged as a raw-patch rollback path. Distinct routes and event discriminators make rollback explicit and prevent a silent protocol change.
- 2026-09-06, Codex: Derive admission identity only from the verified Firebase user resolved by `CurrentUserDependency`; never accept a client-authored user ID. The route reuses the existing scene feature gate, per-user/global admission lease, provider-attempt limiter, fixed error language, SSE headers, and response-owned cleanup.
- 2026-09-06, Codex: Require semantic transport callers to select `product` or `developmentLab` explicitly. The product wrapper supplies Firebase authorization; the lab remains auth-free only behind its existing environment and loopback guards.
- 2026-09-06, Codex: Keep Gate 1.3 Pythagorean-only and update suggestions accordingly. A narrow truthful capability is preferable to presenting unsupported binary-tree, gradient-descent, or derivative examples that correctly decline.
- 2026-09-06, Codex: Do not add a test-only production authentication bypass. Route tests will prove server enforcement, component tests will prove bearer forwarding and paired bases, the guarded lab will prove real browser streaming mechanics, and a genuine Firebase session will be used for the later signed-in smoke.
- 2026-09-06, Codex: No paid provider request is authorized by this plan. Provider-free fakes qualify implementation first; any Azure call needs a newly stated case count, attempt ceiling, token ceiling, and dollar cap.

## Outcomes & Retrospective

Gate 1.3 has not shipped yet. Update this section after every milestone with commit SHAs, test evidence, remaining limitations, and the final product verdict.

## Context and Orientation

`backend/murmur/api/routers/live_scenes.py` owns the live-scene HTTP boundary. Its authenticated raw route uses `CurrentUserDependency` to verify the Firebase bearer and resolve the database user, obtains a `SceneAdmissionLease` keyed by that trusted user ID, streams service events, and delegates iterator plus lease cleanup to `_OwnedStreamingResponse`. The same file exposes `/api/live-scenes/lab/semantic/stream`, which calls `SceneAuthoringService.stream_routed_semantic_events()` but is deliberately auth-free, schema-hidden, development-only, and loopback-only.

`backend/murmur/live_scene/semantic_service_contracts.py` defines `SemanticLiveSceneRequest`. It contains the prompt, generation number, low-level `baseScene`, and matching `baseSemanticScene`. The request contract rejects extra fields, bounds all content, and requires both revisions to agree. `backend/murmur/live_scene/service.py` routes the model's small `start_visual`, `continue_visual`, or `abstain` decision, lowers allowed decisions into server-owned teaching beats, deterministically compiles geometry, verifies the realization, and emits certified semantic scene events.

`web/src/app/(app)/canvas/generate/page.tsx` renders `LiveModelScene`. `web/src/features/live-scene/live-model-scene.tsx` currently gets Firebase headers but instantiates raw protocol behavior. `web/src/features/live-scene/model-stream.ts` owns HTTP/SSE transport. `web/src/features/live-scene/model-scene-demo.tsx` and `stream-runtime.ts` already support a semantic mode, including the paired frontier, progressive application, interruption, stale-event rejection, continuation, decline, and replay. `web/src/features/live-scene/live-scene-lab.tsx` demonstrates how to wire that semantic mode to the guarded lab.

`tests/test_live_scene_semantic_api.py` is the primary backend route boundary suite. `web/src/features/live-scene/model-stream.test.ts`, a new focused product-wrapper test, `web/src/features/live-scene/semantic-stream-runtime.test.ts`, and `web/e2e/live-scene.spec.ts` cover the browser boundary at increasing integration depth.

## Plan of Work

First, add the authenticated semantic product route beside the existing raw route. Extract only enough shared semantic-response construction to keep the product and lab cleanup behavior identical. The product route must validate the request before admission, authenticate before acquiring paid capacity, acquire against the trusted database user ID, call only `stream_routed_semantic_events()`, and return the existing no-store/non-buffered SSE response. Extend contract and security route inventories. Replace the existing assertion that no production semantic route exists with positive, negative, default-off, rate-limit, strict-body, and cleanup tests. Commit and push this backend boundary as its own milestone.

Second, make semantic endpoint selection explicit in `model-stream.ts`. Preserve the lab call site with `developmentLab`; make the product call site choose `product`, attach `getAuthHeaders()`, and instantiate `ModelSceneDemo` with `protocol="semantic"`. Update the page metadata, visible copy, and suggestions so they describe verified progressive Pythagorean lessons rather than a general raw-model generator or internal gate. Add transport and product-wrapper tests proving the exact URL, bearer propagation, paired request bodies, abort signal, semantic protocol selection, and no fetch when authentication is unavailable. Commit and push this frontend boundary separately.

Third, extend provider-free browser evidence only where promotion creates a new risk. Reuse the semantic fixture for first-atom-before-terminal presentation, interruption with no late atom, exact prefix continuation, replay without another request, decline with byte-equivalent visible state, and 375- and 320-pixel reachability. Do not manufacture a fake Firebase backdoor in the application. Where Playwright cannot enter the authenticated layout without real Firebase, exercise the semantic lab for the rendering behavior and rely on independent FastAPI plus React boundary tests for authentication composition.

Finally, run focused and full repository gates, inspect the complete branch diff against `origin/main`, and request an independent pre-merge review. Record all evidence and known boundaries here. A live Azure browser request is a later qualification action and will occur only after explicit cost approval.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-product` unless a command changes directory explicitly.

Install locked dependencies when the clean worktree needs them:

    uv sync --frozen
    cd web && npm ci

After the backend milestone, run:

    uv run pytest -q tests/test_live_scene_api.py tests/test_live_scene_semantic_api.py tests/test_live_scene_routed_semantic_service.py tests/test_live_scene_admission.py tests/test_authentication.py tests/test_api_contract.py tests/test_api_security.py
    uv run ruff check backend/murmur/api/routers/live_scenes.py tests/test_live_scene_semantic_api.py tests/test_api_contract.py tests/test_api_security.py
    uv run ruff format --check backend/murmur/api/routers/live_scenes.py tests/test_live_scene_semantic_api.py tests/test_api_contract.py tests/test_api_security.py

After the frontend milestone, run from `web/`:

    npm test -- src/features/live-scene/model-stream.test.ts src/features/live-scene/live-model-scene.test.tsx src/features/live-scene/semantic-stream-runtime.test.ts src/features/live-scene/model-scene-demo.test.tsx src/features/live-scene/semantic-scene-stream-fixture.test.ts
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

After every coherent milestone, commit it, push `codex/verified-visual-product`, and prove parity:

    git rev-parse HEAD
    git rev-parse @{upstream}

## Validation and Acceptance

The backend boundary passes when an unauthenticated semantic product request returns 401 without admission or provider work; disabled or unavailable scene service returns its fixed 503; malformed or mismatched paired bases return 422 before admission; admission rejection returns 429 before event iteration; an authenticated request keys admission by the resolved database user ID; a client-supplied `userId` is rejected; and the lab flag never bypasses product authentication. Successful and declined streams must use canonical semantic SSE plus `Cache-Control: no-store` and `X-Accel-Buffering: no`, and normal completion, body failure, pre-body disconnect, and cancellation must release the event iterator and lease exactly once.

The frontend boundary passes when `/canvas/generate` selects semantic protocol, chooses the product semantic endpoint explicitly, obtains a Firebase bearer, and sends the exact paired low-level and semantic frontier. Missing authentication must prevent fetch. The product path must never invoke raw scene transport, while the guarded lab must remain explicitly on the development-lab semantic endpoint.

The interaction passes provider-free when the first verified atom becomes visible before terminal completion; interrupting after any presented atom retains that exact prefix and rejects queued or late atoms; a follow-up continues the same component from the exact retained semantic revision and certificate head; replay performs no network request; an unsupported intent produces no motion or frontier mutation and does not poison the next request; and controls plus board remain usable without horizontal overflow at desktop, 375 by 812, and 320 by 568.

Gate 1.3 is ready for review only when focused tests, full backend tests, full frontend tests, lint, type-check, production build, and scene Playwright suite pass from the pushed head. It is product-qualified only after a genuine signed-in Azure browser smoke, under a separately approved spend ceiling, demonstrates warm prompt-to-first-meaningful-atom at or below 3,000 milliseconds with the same interruption and continuation behavior. Until then, describe the result as provider-free authenticated product integration, not live-provider production readiness.

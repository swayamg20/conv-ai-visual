# Connect the Gate 1 lab to Azure live scene generation

## Purpose / Big Picture

Murmur's development-only live-scene lab currently accepts a prompt but always plays a deterministic fixture. After this work, a developer can open `http://127.0.0.1:3102/labs/live-scene` without Firebase, choose the Azure live source, enter a new teaching prompt, and watch `gpt-oss-120b` stream validated SceneDoc patches onto the canvas. The authenticated product route remains unchanged. The no-login route exists only when both the backend and Next development servers are explicitly started in lab mode.

## Progress

- [x] 2026-09-04 02:42 IST: Deployed `murmur-gpt-oss-120b` version 1 as Azure `GlobalStandard` capacity 10 and proved it with an HTTP 200 smoke request.
- [x] 2026-09-04 02:47 IST: Launched the deterministic `/labs/live-scene` page and confirmed that its prompt is intentionally ignored by the fixture runner.
- [x] 2026-09-04 02:55 IST: Added and pushed the explicit `azure_openai` provider in `27a6733`, including strict endpoint normalization and `max_completion_tokens` translation.
- [x] 2026-09-04 02:58 IST: Added and pushed the loopback-only development lab stream plus Fixture/Azure selector in `7438148`; the authenticated product route remains unchanged.
- [x] 2026-09-04 03:08 IST: Diagnosed Azure's malformed tail as a 4,096-token `length` finish, then pushed bounded three-patch authoring, scene-owned temperature, Azure GPT-OSS low reasoning, and server-enforced patch targets in `905c9c7`, `6566f1f`, and `9089b13`.
- [x] 2026-09-04 03:10 IST: Verified custom prompts end to end with process-only credentials. A direct stack request completed three first-attempt patches at 2,468 ms first patch / 2,762 ms total. The browser queue run completed with three visible revisions at 2,614 ms first patch / 4,947 ms total, correct queue labels, and no console errors.
- [x] 2026-09-04 03:13 IST: Passed 1,234 backend tests plus Ruff, 369 frontend tests plus ESLint and TypeScript, and all eight dedicated Chromium scenarios with 20 ms deterministic first-visible p95. Updated the live plans; all implementation commits are pushed on `codex/realtime-scene-core`.
- [x] 2026-09-04 03:17 IST: Pushed Azure support for the explicitly cost-guarded ten-prompt corpus runner in `0581ba7`; 39 focused API/provider/probe tests and Ruff passed, and an independent offline audit confirmed dry-run performs no config, factory, or socket access.

## Surprises & Discoveries

- Azure Foundry's catalog UI tried to deploy catalog asset version 4 even though the live `eastus2` account supports deployment model version 1 only. The native Azure deployment API succeeded with version 1.
- The portal also created a second `gpt-oss-120b` deployment at capacity 2500. The Murmur deployment uses capacity 10. The duplicate consumes quota but is not dedicated hourly GPU compute; it will not be deleted without explicit approval.
- The existing lab intentionally separates Firebase-free browser QA from paid provider behavior. Its text field changes local state, but `createSceneFixtureRunner` ignores the prompt and emits scripted Pythagoras patches.
- The first Azure browser run accepted one useful binary-search patch but failed after both attempts. A redacted reproduction proved the first attempt ended with an unterminated NDJSON frame; a metadata-only provider probe then confirmed `finish_reason=length` and exactly 4,096 completion tokens.
- `gpt-oss-120b` shares `max_completion_tokens` between hidden reasoning and visible output. Default reasoning plus a 3-5 patch prompt delayed visible ink and exhausted the scene cap. Low reasoning, scene temperature 0.2, exactly three compact initial patches, and one repair patch produced clean bounded output without raising the safety cap.
- Prompt instructions alone are not an execution boundary. The service now stops and closes the provider after three accepted initial patches or one accepted repair patch, so a model cannot invalidate useful work by beginning a truncated extra frame.

## Decision Log

- 2026-09-04, Codex: Reuse `OpenAIClient` through Azure's `/openai/v1/` compatibility endpoint instead of adding an Azure SDK. This keeps one streaming adapter and makes the Azure deployment name the request model.
- 2026-09-04, Codex: Add a dedicated lab endpoint rather than bypassing `get_authenticated_user`. This keeps every existing product, session, and ownership boundary intact.
- 2026-09-04, Codex: Require both an explicit lab flag and a non-production environment. A missing or production gate returns 404 so an accidental production setting does not advertise an auth-free model route.
- 2026-09-04, Codex: Keep deterministic modes alongside Azure live mode. Fixtures remain the fast regression oracle; Azure mode proves usefulness and provider latency.
- 2026-09-04, Codex: Keep Azure keys and tokens in the backend process environment only. No credential, endpoint key, or browser-visible secret is written to the repository.
- 2026-09-04, Codex: Use low reasoning only for Azure GPT-OSS scene clients and keep the 4,096-token safety cap. This reduces time-to-first-visible and reserves output capacity without changing chat or unrelated providers.
- 2026-09-04, Codex: Ask for exactly three compact initial patches and exactly one repair patch, and enforce those targets server-side. Accepted prefix patches are a product result; an unrequested fourth frame must not trigger another paid attempt.

## Outcomes & Retrospective

The Azure lab integration and browser-visible custom-prompt proof are complete and pushed through `9089b13`. Both stack and queue prompts produced semantically relevant, progressive scenes; the queue screenshot shows four labeled boxes plus enqueue/dequeue annotations at revision 3. Full regression passed with 1,234 backend tests, 369 frontend tests, clean Ruff/ESLint/TypeScript, and eight Chromium scenarios. The single browser sample is inside the 3-second Gate 1 p95 ceiling, but it is not the required ten-prompt corpus, so the overall live-model gate remains pending that separately budgeted run.

The corpus runner now supports Azure GPT-OSS through the same provider policy as the application while retaining its exact acknowledgement and maximum-cost guards. No corpus call was made as part of that change.

## Context and Orientation

`backend/murmur/core/config.py` owns provider selection and scene-specific limits. `backend/murmur/llm/factory.py` creates provider clients. `backend/murmur/llm/openai.py` implements OpenAI-compatible completion and streaming calls. Azure's v1 endpoint is compatible with that client, but the Azure deployment name must be sent as `model`, and `gpt-oss-120b` expects `max_completion_tokens` rather than the legacy `max_tokens` request field.

`backend/murmur/api/application.py` builds one lazy `SceneAuthoringService` and a `SceneAuthoringAdmission` limiter. `backend/murmur/api/routers/live_scenes.py` currently exposes only authenticated `POST /api/live-scenes/stream`. The new lab route will reuse the same service, admission limits, canonical SSE encoding, cleanup, and request schema with a fixed internal lab identity. It will not call or weaken Firebase authentication.

`web/src/app/labs/live-scene/page.tsx` is already hidden unless Next runs in development with `MURMUR_SCENE_LAB=1`. `web/src/features/live-scene/live-scene-lab.tsx` currently supplies `createSceneFixtureRunner` to the shared `ModelSceneDemo`. `web/src/features/live-scene/model-stream.ts` already handles the real SSE wire protocol. The lab will select either a fixture runner or an unauthenticated lab runner that posts only the prompt and scene state to the local backend.

## Plan of Work

First, add Azure provider configuration, strict endpoint normalization, and request-parameter handling. The accepted endpoint must be HTTPS, contain no user information, query, or fragment, and belong to an Azure AI hostname. Unit tests will prove that a credential cannot be sent to an arbitrary host. The general OpenAI and Groq behavior must stay unchanged.

Second, add a development-only lab stream route beside the authenticated route. Factor the common lease and streaming-response construction so both routes have identical cancellation and cleanup behavior. The lab route must return 404 unless the explicit lab flag is enabled and `MURMUR_ENVIRONMENT` is not production. Tests will prove the default-off, production-off, enabled, rate-limit, and client-identity-rejection paths.

Third, extend the lab UI with a clear source choice. Azure live mode calls the new backend lab endpoint and uses the actual prompt. Normal, repair, failure, and late-output modes continue using deterministic fixtures. Frontend tests will assert the exact URL, absence of Firebase headers, prompt forwarding, source labels, and source switching.

Finally, retrieve the Azure credential only into the local backend process environment, start the backend on `127.0.0.1:8000`, keep Next on `127.0.0.1:3102`, and generate a non-Pythagoras scene from the browser. Record first patch, completion, accepted patch count, visible content, and server errors. Then run the focused suites and the broader backend/frontend tests before committing and pushing.

## Concrete Steps

Work from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-scene-core` on `codex/realtime-scene-core`.

Run the provider and route tests while implementing:

    .venv/bin/pytest -q tests/test_llm_providers.py tests/test_core_config.py tests/test_live_scene_api.py

Run the focused frontend tests:

    cd web
    npm test -- --run src/lib/live-scene src/features/live-scene

Start the backend with lab mode, scene generation, and Azure provider settings. The API key is obtained into the process environment and never printed or written:

    MURMUR_ENVIRONMENT=development MURMUR_SCENE_LAB=1 MURMUR_SCENE_ENABLED=true MURMUR_SCENE_LLM_PROVIDER=azure_openai MURMUR_SCENE_LLM_MODEL=murmur-gpt-oss-120b AZURE_OPENAI_ENDPOINT=https://guptaswayam123-4085-resource.openai.azure.com AZURE_OPENAI_DEPLOYMENT=murmur-gpt-oss-120b AZURE_OPENAI_API_KEY=<process-only-key> .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000

Start the frontend:

    cd web
    MURMUR_SCENE_LAB=1 NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev -- --hostname 127.0.0.1 --port 3102

Open `http://127.0.0.1:3102/labs/live-scene`, select Azure live mode, enter a prompt unrelated to the deterministic Pythagoras fixture, and generate. Inspect the browser and backend logs without printing credentials or raw provider error bodies.

After focused tests pass, run the repository regression commands appropriate to the touched surfaces:

    .venv/bin/pytest -q
    cd web && npm test -- --run
    cd web && npx tsc --noEmit

Commit only the intended files in small logical commits and push `codex/realtime-scene-core` to `origin`.

## Validation and Acceptance

The work is accepted only when all of the following are true. With lab mode absent, both the frontend lab page and backend lab endpoint are unavailable. With `MURMUR_ENVIRONMENT=production`, the backend lab endpoint remains unavailable even if the lab flag is set. The authenticated `/api/live-scenes/stream` route still returns 401 without a Firebase bearer token. Azure credentials are absent from git diff, logs, frontend bundles, and request headers sent by the browser.

In enabled development mode, a custom prompt reaches `murmur-gpt-oss-120b`, produces at least one schema-valid scene patch before completion, and visibly changes the canvas to match that prompt rather than the Pythagoras fixture. Interrupting a live Azure stream retains accepted ink and rejects late output. The existing deterministic normal, repair, failure, interruption, and replay scenarios still pass. Focused provider, API, and frontend tests pass, and the branch is pushed with a clean worktree.

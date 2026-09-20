# Deploy the Murmur visual application to Azure

## Purpose / Big Picture

This plan deploys the real Murmur web application and API, rather than only using an Azure-hosted language model from a developer laptop. A user will receive an HTTPS frontend URL, sign in with the existing Firebase project, open the real-time storyboard experience, and send authenticated requests to an HTTPS FastAPI backend. The backend will call the already-qualified `murmur-gpt-oss-120b` Azure model without exposing its key to the browser.

This is a deliberately bounded single-replica pilot for the visual product. It deploys the Next.js frontend and the FastAPI backend to Azure Container Apps in Central India. It does not claim that the separate voice-media pipeline is production-qualified on Container Apps. Durable multi-replica production remains a later PostgreSQL and distributed-admission milestone.

## Progress

- [x] 2026-09-20 12:41 IST: Verified that PR #38 is on `origin/main`, its full CI run passed, and no Murmur application deployment currently exists.
- [x] 2026-09-20 12:41 IST: Created isolated branch `codex/azure-app-deployment` and worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-azure-deploy` from merge commit `4eac28e` without touching the dirty primary checkout.
- [x] 2026-09-20 12:41 IST: Audited the frontend, backend, authentication, persistence, model quota, and currently enabled Azure subscription.
- [x] 2026-09-20 13:04 IST: Made the frontend container-safe, centralized the production API origin, added a public health route, and passed lint, type checking, focused tests, and a configured production build.
- [x] 2026-09-20 13:04 IST: Added secret-backed Firebase credentials, backend health/readiness, release provenance, configurable SQLite journaling, and passed 120 focused backend checks plus Ruff/format.
- [x] 2026-09-20 13:08 IST: Added reproducible non-root backend/frontend images and validated both Azure Bicep templates.
- [x] 2026-09-20 13:42 IST: Added a redaction-safe deployment driver that imports existing local credentials into Key Vault and never prints them.
- [x] 2026-09-20 13:42 IST: Passed deployment-driver lint, formatting, 21 focused tests, Python compilation, both Bicep builds, and diff checks. Earlier application checks passed 239 backend checks plus the complete 1,298-test frontend suite and production build; local container execution remains unavailable because Docker Desktop is stopped, so Azure ACR performs the image builds.
- [ ] Commit and push coherent milestones to `codex/azure-app-deployment`. First application-readiness commit `87aa7d0` is pushed; infrastructure/deployment and live-evidence commits remain.
- [ ] Provision the isolated `murmur-pilot-rg` Azure resources and build immutable images from the accepted commit. The tagged Central India foundation resources are live and healthy; the ACR builds and digest-pinned application revisions remain.
- [ ] Add the deployed frontend hostname to Firebase Authentication authorized domains.
- [ ] Verify HTTPS health, readiness, CORS, persistence across an API revision restart, authenticated UI loading, and the Gate 1.8 request path without an unbudgeted model corpus.
- [ ] Open, review, and merge the deployment pull request, then prove the live revision corresponds to merged `main`.

## Surprises & Discoveries

- Gate 1.8 used the Azure model from local processes on ports 8000 and 3102. The repo had no Dockerfiles, Azure infrastructure, GitHub environment, or deploy workflow.
- `web/src/hooks/use-chat.ts` and `web/src/hooks/use-webrtc.ts` defaulted directly to `http://localhost:8000`; cloud users of `/canvas` and `/session/[agentId]` would therefore call their own machines even though the storyboard route already used `NEXT_PUBLIC_API_URL`.
- Firebase Admin accepted only a local credential-file path. Azure managed identity is not a Google credential, so the existing service-account JSON must be injected as an Azure Key Vault-backed secret rather than baked into an image.
- The production scene runtime has process-local request admission, while the more precise 12,000-token-per-minute reservation lives in the manual Gate 1.8 harness. The pilot must run one worker and one replica with conservative scene limits.
- SQLite currently enables write-ahead logging. SQLite documents that WAL does not work over a network filesystem, while Container Apps persistent storage is an Azure Files mount. The pilot therefore needs an explicit rollback-journal setting and a one-replica invariant. PostgreSQL is required before horizontal scale.
- The enabled subscription already supports Container Apps in Central India, but no Murmur resource group or hosting resource exists. The existing Container Apps environment belongs to AgentRelay and will not be reused.
- The first foundation deployment failed before completion because the current Key Vault API rejects an explicit `enablePurgeProtection: false`; omitting that optional property preserves the intended default and makes the template portable.
- Azure Container Apps retains the submitted image reference, so a git-SHA tag alone is not an immutable deployment postcondition. The driver resolves each completed ACR build to its manifest digest and the application template deploys `repository@sha256:...`, while the full git SHA remains explicit release metadata.

## Decision Log

- 2026-09-20, Codex: Use two Azure Container Apps, one for Next.js and one for FastAPI. This matches the current server-rendered Next.js build and gives both applications managed HTTPS ingress without rewriting the frontend for static export.
- 2026-09-20, Codex: Create a separate `murmur-pilot-rg` and `murmur-pilot-env` in Central India. Product isolation is worth the small setup overhead; AgentRelay resources remain untouched.
- 2026-09-20, Codex: Use Azure Container Registry with a shared user-assigned identity and `AcrPull`, rather than registry passwords in app configuration.
- 2026-09-20, Codex: Store the Azure model key and Firebase service-account JSON in Azure Key Vault. Container Apps reads versionless Key Vault references through the managed identity, so secrets do not enter source control, image layers, or normal deployment output.
- 2026-09-20, Codex: Keep the pilot at one Uvicorn worker and `maxReplicas: 1`. Start with `minReplicas: 0` to bound idle compute cost; the first request may cold-start. Raise the backend minimum to one only after latency and recurring cost are explicitly accepted.
- 2026-09-20, Codex: Persist the pilot SQLite database on Azure Files with rollback journaling, never WAL, and prove data survives a revision restart. This is a bounded pilot compromise, not the multi-replica database architecture.
- 2026-09-20, Codex: Configure the qualified Azure model for chat and scenes, cap scene output at 2,048 tokens, and set global/per-user concurrency to one. Production scene dispatch starts at one per minute until token-window admission is promoted from the acceptance harness.
- 2026-09-20, Codex: Deployment acceptance covers authenticated text/visual operation. Voice transport qualification stays in the separate voice track because Container Apps HTTP ingress is not proof of browser WebRTC/TURN behavior.
- 2026-09-20, Codex: Build a git-SHA tag for traceability but deploy its resolved ACR manifest digest. A rerun can therefore never silently move the bytes behind a live Container Apps revision.

## Outcomes & Retrospective

No Azure application resources have been created yet. This section will be updated with the deployed URLs, immutable image tags, verification evidence, recurring-cost posture, and any remaining limitations after acceptance.

## Context and Orientation

`main.py` exposes the FastAPI application created by `backend/murmur/api/application.py`. The application initializes SQLModel persistence from `backend/murmur/persistence/database.py`, verifies Firebase bearer tokens through `backend/murmur/api/authentication.py`, and serves chat plus visual-scene routes under `backend/murmur/api/routers/`. The Azure model configuration is read in `backend/murmur/core/config.py` and consumed through the existing OpenAI-compatible provider adapter.

`web/` is a Next.js 16 application. `web/src/lib/api.ts` owns the configured API base URL and `web/src/lib/firebase.ts` owns the public Firebase browser configuration. The public Firebase values and backend URL are compiled into the Next.js browser bundle at image-build time; they are not server-only runtime settings. `web/src/features/live-scene/live-semantic-storyboard.tsx` is the Gate 1.8 product surface.

`infra/azure/` will contain Bicep templates for the resource group contents. The foundation will create a Log Analytics workspace, Container Apps environment, Basic Azure Container Registry, Key Vault, storage account and Azure Files share, and a user-assigned identity with narrowly scoped pull and secret-read roles. Application templates will create the public backend and frontend Container Apps. `scripts/deploy_azure.py` will orchestrate repeatable deployment without emitting secret values.

The resource names with non-global scope are `murmur-pilot-env`, `murmur-api`, `murmur-web`, `murmur-pilot-identity`, and `murmur-data`. Globally unique Key Vault, registry, and storage names are derived deterministically from the active subscription and resource-group identity rather than hand-entered.

## Plan of Work

First, make the application deployable without changing its user-facing contracts. The frontend will use one API-origin constant everywhere and emit a compact standalone Next.js server image. The backend will accept Firebase service-account JSON directly from a secret environment variable while retaining the local path option, expose shallow liveness and dependency-aware readiness endpoints, and allow rollback journaling for the mounted pilot database. Focused tests will lock these behaviors.

Second, add least-privilege infrastructure and images. Both images will use pinned runtime families, non-root users, deterministic lockfiles, and one application process. Bicep will create an isolated Central India environment, Key Vault references, managed-identity registry pulls, HTTPS-only ingress, explicit probes, scale-to-zero minimums, one-replica maximums, and the Azure Files volume. No provider credential will be a Bicep plain-text parameter or image build argument.

Third, add a deployment driver. It will validate Azure login, required CLI capabilities, the current git state, and local environment files. It will deploy the foundation, import only the required credential values into Key Vault, remotely build the backend, discover the backend HTTPS origin, remotely build the frontend with public Firebase settings and that API origin, deploy both apps, configure exact backend CORS, and emit only resource names, URLs, revisions, and health results. It will refuse dirty or unpushed source by default and use the full git SHA as each image tag.

Fourth, validate locally and in Azure. Local checks will cover Python lint/tests, frontend lint/types/tests/build, Docker builds, and Bicep compilation plus `what-if`. Azure checks will prove healthy revisions, exact CORS, no plaintext app secrets, one-replica limits, mounted persistence, and successful authenticated page loading. Adding the frontend domain to Firebase authorized domains is part of deployment, not a manual afterthought. Paid Azure model output is not exercised beyond an explicitly bounded acceptance call.

Finally, commit and push each coherent milestone, open a pull request, run the complete CI suite, review the diff and live evidence, merge, rebuild or retag from the merge SHA, and verify that the public apps report the merged revision.

## Concrete Steps

All repository commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-azure-deploy`.

Implement and validate application readiness:

    uv run ruff check backend tests scripts/deploy_azure.py
    uv run pytest tests/test_authentication.py tests/test_api_contract.py
    cd web && npm ci && npm run lint && npm run typecheck && npm run test && npm run build

Build the Bicep templates and inspect the change before provisioning:

    az bicep build --file infra/azure/foundation.bicep
    az bicep build --file infra/azure/apps.bicep
    az deployment group what-if --resource-group murmur-pilot-rg --template-file infra/azure/foundation.bicep

Run the deployment driver with ignored local configuration files. It reads values without printing them:

    uv run python scripts/deploy_azure.py deploy \
      --backend-env /Users/swayam.gupta/Documents/GitHub/conv-ai-visual/.env \
      --frontend-env /Users/swayam.gupta/Documents/GitHub/conv-ai-visual/web/.env.local

Run read-only live verification:

    uv run python scripts/deploy_azure.py verify

The driver must print the frontend URL, backend URL, immutable git SHA, active revision names, health status, persistence proof status, Firebase-domain status, and scale settings. It must never print an Azure key, Firebase private key, registry credential, storage key, bearer token, or connection string.

## Validation and Acceptance

Deployment is accepted only when all of the following are true.

The branch passes backend lint and tests plus frontend lint, type checking, tests, and production build. Both container images build from clean source, run as non-root, and answer their expected ports locally. Both Bicep templates compile, and Azure `what-if` contains only the named Murmur pilot resources.

The backend public URL returns HTTP 200 from `/healthz` and `/readyz` over HTTPS. The readiness endpoint proves database access and validates required Firebase and Azure model configuration without making a paid model request or revealing configuration. An `OPTIONS` request from the exact frontend origin receives the expected CORS allow-origin header, while an unrelated origin does not.

Azure reports one active revision per app, `maxReplicas` equal to one, no inline secret values for provider credentials, Key Vault references owned by the managed identity, and images tagged with a full accepted git SHA. The backend database is on the mounted share and a harmless persisted sentinel survives a backend revision restart. SQLite reports a rollback journal rather than WAL.

The frontend public URL loads over HTTPS, its browser bundle calls the Azure backend rather than localhost, and the existing Firebase user can reach `/canvas/storyboard`. The hostname is present in Firebase Authentication authorized domains. The storyboard UI can form an authenticated request and render the safe initial state. A live model beat is run only under a separately recorded strict call and cost bound.

The pull request CI must pass before merge. After merge, the live apps must be rebuilt or updated from the merge SHA and the verification report must show that exact SHA. Only then is “Murmur is deployed to Azure” a supported claim.

# Deploy the Murmur visual application to Azure

## Purpose / Big Picture

This plan deploys the real Murmur web application and API, rather than only using an Azure-hosted language model from a developer laptop. A user will receive an HTTPS frontend URL, sign in with the existing Firebase project, open the real-time storyboard experience, and send authenticated requests to an HTTPS FastAPI backend. The backend will call the already-qualified `murmur-gpt-oss-120b` Azure model without exposing its key to the browser.

This is a deliberately bounded single-replica, non-durable pilot for the visual product. It deploys the Next.js frontend and the FastAPI backend to Azure Container Apps in Central India. It does not claim that the separate voice-media pipeline is production-qualified on Container Apps, and stored agents or history may reset when the backend scales down or is redeployed. Durable use remains a PostgreSQL and distributed-admission milestone.

## Progress

- [x] 2026-09-20 12:41 IST: Verified that PR #38 is on `origin/main`, its full CI run passed, and no Murmur application deployment currently exists.
- [x] 2026-09-20 12:41 IST: Created isolated branch `codex/azure-app-deployment` and worktree `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-azure-deploy` from merge commit `4eac28e` without touching the dirty primary checkout.
- [x] 2026-09-20 12:41 IST: Audited the frontend, backend, authentication, persistence, model quota, and currently enabled Azure subscription.
- [x] 2026-09-20 13:04 IST: Made the frontend container-safe, centralized the production API origin, added a public health route, and passed lint, type checking, focused tests, and a configured production build.
- [x] 2026-09-20 13:04 IST: Added secret-backed Firebase credentials, backend health/readiness, release provenance, configurable SQLite journaling, and passed 120 focused backend checks plus Ruff/format.
- [x] 2026-09-20 13:08 IST: Added reproducible non-root backend/frontend images and validated both Azure Bicep templates.
- [x] 2026-09-20 13:42 IST: Added a redaction-safe deployment driver that imports existing local credentials into Key Vault and never prints them.
- [x] 2026-09-20 13:42 IST: Passed deployment-driver lint, formatting, 21 focused tests, Python compilation, both Bicep builds, and diff checks. Earlier application checks passed 239 backend checks plus the complete 1,298-test frontend suite and production build; local container execution remains unavailable because Docker Desktop is stopped, so Azure ACR performs the image builds.
- [x] 2026-09-20 14:30 IST: Committed and pushed application readiness, reproducible infrastructure, ACR compatibility, Azure metadata normalization, non-root runtime-home, and safe rollout fixes to `codex/azure-app-deployment`.
- [x] 2026-09-20 14:59 IST: Provisioned the isolated `murmur-pilot-rg` Azure resources and deployed digest-pinned backend and frontend images from commit `706a60e` to healthy revisions `murmur-api--0000003` and `murmur-web--0000003`.
- [x] 2026-09-20 14:30 IST: Added the deployed frontend hostname to Firebase Authentication authorized domains through the Identity Toolkit Admin API and confirmed the returned configuration.
- [x] 2026-09-20 14:59 IST: Verified public HTTPS liveness, dependency-aware readiness, exact frontend CORS, release SHA, probes, one-replica limits, Key Vault references, and both public browser routes with zero paid model calls. The landing and sign-in pages render, and unauthenticated `/canvas/storyboard` access redirects to `/login` as designed.
- [x] 2026-09-20 17:01 IST: Closed the pre-merge frontend credential-isolation blocker with separate managed identities, exact foundation identity checks, frontend Key Vault RBAC checks, and regression coverage. Upgraded Next.js to 16.3.5 and eliminated all production dependency audit findings.
- [x] 2026-09-20 17:30 IST: Pinned the deployer to an explicit Azure subscription and tenant, made temporary Key Vault writer access self-revoking, removed mutable ACR build-tag races, and bound frontend secret-denial checks to the live identity and both protected secret scopes.
- [x] 2026-09-20 17:30 IST: Split Firebase deployment authority from runtime token verification. Azure now accepts only a dedicated runtime credential, an optional domain administrator must be a different principal and key, and prior Key Vault secret versions are disabled after rotation.
- [x] 2026-09-20 17:41 IST: Bounded paid text chat with a 4,000-character request ceiling, process-local global/per-user concurrency plus a process-global rolling-minute ceiling held for the full SSE lifetime, and a 1,024-token Azure output cap. Focused chat, scene-regression, security, and continuity tests pass.
- [ ] Complete one user-authenticated Azure browser pass through `/canvas/storyboard` without pressing the model-backed continue action. Persistence is explicitly out of scope for this low-cost pilot after Azure Files incompatibility was proven live.
- [ ] Open, review, and merge the deployment pull request, then prove the live revision corresponds to merged `main`.

## Surprises & Discoveries

- Gate 1.8 used the Azure model from local processes on ports 8000 and 3102. The repo had no Dockerfiles, Azure infrastructure, GitHub environment, or deploy workflow.
- `web/src/hooks/use-chat.ts` and `web/src/hooks/use-webrtc.ts` defaulted directly to `http://localhost:8000`; cloud users of `/canvas` and `/session/[agentId]` would therefore call their own machines even though the storyboard route already used `NEXT_PUBLIC_API_URL`.
- Firebase Admin accepted only a local credential-file path. Azure managed identity is not a Google credential, so the existing service-account JSON must be injected as an Azure Key Vault-backed secret rather than baked into an image.
- The production scene runtime has process-local request admission, while the more precise 12,000-token-per-minute reservation lives in the manual Gate 1.8 harness. The pilot must run one worker and one replica with conservative scene limits.
- SQLite on the SMB-backed Azure Files mount failed with `database is locked` on a fresh zero-byte database even after all other replicas were terminated. Rollback journaling does not make this storage/database pairing safe. The low-cost visual pilot now uses local ephemeral SQLite with WAL; PostgreSQL is required for any durable deployment.
- The enabled subscription already supports Container Apps in Central India, but no Murmur resource group or hosting resource exists. The existing Container Apps environment belongs to AgentRelay and will not be reused.
- The first foundation deployment failed before completion because the current Key Vault API rejects an explicit `enablePurgeProtection: false`; omitting that optional property preserves the intended default and makes the template portable.
- Azure Container Apps retains the submitted image reference, so a git-SHA tag alone is not an immutable deployment postcondition. The driver resolves each completed ACR build to its manifest digest and the application template deploys `repository@sha256:...`, while the full git SHA remains explicit release metadata.
- Azure ACR Quick Build currently uses the classic Docker builder for this registry, so a BuildKit-only `RUN --mount=type=cache` instruction fails before dependency installation. The backend image uses the same hash-locked install without the optional cache mount; correctness is unchanged and remote layer caching still applies.
- The Container Apps control plane serializes the same managed-identity resource ID with different casing in the app identity map and its ACR/Key Vault references, and emits empty registry credential fields. Verification therefore compares Azure resource IDs case-insensitively and rejects non-empty credentials instead of rejecting Azure's normalized representation.
- The first backend revision exited before serving because Mem0 initializes client metadata below the process home and the non-root container user intentionally had no home directory. The image remains non-root but now creates and owns `/home/murmur`, giving third-party initialization a bounded writable location.
- Restarting a newly created revision before its first replica became ready caused Container Apps to overlap two backend replicas during SQLite schema initialization, producing a real `database is locked` failure despite the steady-state `maxReplicas: 1` contract. Deployment now lets the immutable new revision start once; restart persistence is tested only after the app is healthy and quiescent.
- After all replicas were terminated, Azure Files still returned `database is locked` for a single process creating the first table. The share contained only a zero-byte bootstrap file, which was removed with no user data loss. The persistent-SQLite design was rejected rather than weakened with unsafe lock suppression.
- ACR completed both immutable image builds but its registry endpoint briefly failed during the immediate manifest lookup. Digest resolution now has a small bounded retry window; it still refuses to deploy unless the final value is a valid `sha256` digest.
- The Container Apps API can briefly report the previous `latestReadyRevisionName` while a new `latestRevisionName` starts. Verification now distinguishes those fields, probes the expected SHA over HTTPS, then refuses success unless the current revision is the one Azure reports ready.
- Pre-merge review found that using one managed identity for both apps let the frontend request Key Vault data-plane access even though it had no secret reference. The frontend now has a separate ACR-pull-only identity; verification rejects shared identities and any frontend secret wiring.
- The public image used Next.js 16.3.0 and a vulnerable Sharp transitive dependency. Moving to Next.js 16.3.5 plus the compatible lockfile updates cleared `npm audit --omit=dev` with no production findings.
- The Firebase credential previously served two incompatible trust boundaries: it updated authorized-domain configuration during deployment and remained in Azure for runtime token revocation checks. The backend only needs Firebase Authentication Viewer permissions, so deployment now requires a dedicated runtime principal and keeps the optional configuration writer local to the deploy process.

## Decision Log

- 2026-09-20, Codex: Use two Azure Container Apps, one for Next.js and one for FastAPI. This matches the current server-rendered Next.js build and gives both applications managed HTTPS ingress without rewriting the frontend for static export.
- 2026-09-20, Codex: Create a separate `murmur-pilot-rg` and `murmur-pilot-env` in Central India. Product isolation is worth the small setup overhead; AgentRelay resources remain untouched.
- 2026-09-20, Codex: Use separate backend and frontend user-assigned identities for Azure Container Registry pulls. Only the backend identity receives Key Vault secret-read access; the public frontend identity is pull-only.
- 2026-09-20, Codex: Store the Azure model key and Firebase service-account JSON in Azure Key Vault. Container Apps reads versionless Key Vault references through the managed identity, so secrets do not enter source control, image layers, or normal deployment output.
- 2026-09-20, Codex: Keep the pilot at one Uvicorn worker and `maxReplicas: 1`. Start with `minReplicas: 0` to bound idle compute cost; the first request may cold-start. Raise the backend minimum to one only after latency and recurring cost are explicitly accepted.
- 2026-09-20, Codex: Do not run SQLite on Azure Files. For the cost-bounded visual acceptance pilot, keep SQLite local and ephemeral and clearly surface the limitation; add managed PostgreSQL before relying on stored agents, history, restart persistence, or multiple replicas.
- 2026-09-20, Codex: Configure the qualified Azure model for chat and scenes. Cap each chat model response at 1,024 tokens and each scene response at 2,048 tokens; hold chat and scene admission for the full stream with global/per-user concurrency of one. The pilot permits two chat starts per user per rolling minute and one scene/provider dispatch per minute.
- 2026-09-20, Codex: Deployment acceptance covers authenticated text/visual operation. Voice transport qualification stays in the separate voice track because Container Apps HTTP ingress is not proof of browser WebRTC/TURN behavior.
- 2026-09-20, Codex: Build a git-SHA tag for traceability but deploy its resolved ACR manifest digest. A rerun can therefore never silently move the bytes behind a live Container Apps revision.
- 2026-09-20, Codex: Store only a dedicated Firebase Authentication Viewer credential in Azure. A separate optional domain administrator may update the authorized hostname during deployment but is never serialized into Key Vault; rotated-out secret versions are disabled.

## Outcomes & Retrospective

The isolated Azure foundation, ACR images, Key Vault references, Firebase authorized domain, and both Container Apps are live in Central India. The backend and frontend answer at their public HTTPS origins from release `706a60e`; health, readiness, CORS, release provenance, probes, scale bounds, and current-ready revisions passed with zero paid model calls. Browser QA proved the landing and sign-in pages and the protected storyboard redirect.

The pilot is intentionally non-durable: backend SQLite lives on local ephemeral Container Apps storage because live testing proved that SQLite locking on the Azure Files SMB mount is not safe for this workload. PostgreSQL remains required before stored agents or history can be relied on across scale-down or deployment. The remaining acceptance steps are a user-authenticated storyboard page load, pull-request review and merge, and a final rebuild whose reported release SHA is the merged `main` commit.

## Context and Orientation

`main.py` exposes the FastAPI application created by `backend/murmur/api/application.py`. The application initializes SQLModel persistence from `backend/murmur/persistence/database.py`, verifies Firebase bearer tokens through `backend/murmur/api/authentication.py`, and serves chat plus visual-scene routes under `backend/murmur/api/routers/`. The Azure model configuration is read in `backend/murmur/core/config.py` and consumed through the existing OpenAI-compatible provider adapter.

`web/` is a Next.js 16 application. `web/src/lib/api.ts` owns the configured API base URL and `web/src/lib/firebase.ts` owns the public Firebase browser configuration. The public Firebase values and backend URL are compiled into the Next.js browser bundle at image-build time; they are not server-only runtime settings. `web/src/features/live-scene/live-semantic-storyboard.tsx` is the Gate 1.8 product surface.

`infra/azure/` contains Bicep templates for the resource group contents. The foundation creates a Log Analytics workspace, Container Apps environment, Basic Azure Container Registry, Key Vault, a backend identity with pull and secret-read roles, and a separate pull-only frontend identity. Application templates create the public backend and frontend Container Apps. `scripts/deploy_azure.py` orchestrates repeatable deployment without emitting secret values.

The resource names with non-global scope are `murmur-pilot-env`, `murmur-api`, `murmur-web`, `murmur-pilot-identity`, and `murmur-web-identity`. Globally unique Key Vault and registry names are derived deterministically from the active subscription and resource-group identity rather than hand-entered.

## Plan of Work

First, make the application deployable without changing its user-facing contracts. The frontend uses one API-origin constant everywhere and emits a compact standalone Next.js server image. The backend accepts Firebase service-account JSON directly from a secret environment variable while retaining the local path option, exposes shallow liveness and dependency-aware readiness endpoints, and uses local SQLite only for this non-durable pilot. Focused tests lock these behaviors.

Second, add least-privilege infrastructure and images. Both images use pinned runtime families, non-root users, deterministic lockfiles, and one application process. Bicep creates an isolated Central India environment, Key Vault references, managed-identity registry pulls, HTTPS-only ingress, explicit probes, scale-to-zero minimums, and one-replica maximums. No provider credential is a Bicep plain-text parameter or image build argument.

Third, add a deployment driver. It will validate Azure login, required CLI capabilities, the current git state, and local environment files. It will deploy the foundation, import only the required credential values into Key Vault, remotely build the backend, discover the backend HTTPS origin, remotely build the frontend with public Firebase settings and that API origin, deploy both apps, configure exact backend CORS, and emit only resource names, URLs, revisions, and health results. It will refuse dirty or unpushed source by default and use the full git SHA as each image tag.

Fourth, validate locally and in Azure. Local checks cover Python lint/tests, frontend lint/types/tests/build, Docker builds, and Bicep compilation plus `what-if`. Azure checks prove healthy revisions, exact CORS, no plaintext app secrets, one-replica limits, the explicit ephemeral-database posture, and successful authenticated page loading. Adding the frontend domain to Firebase authorized domains is part of deployment, not a manual afterthought. Paid Azure model output is not exercised beyond an explicitly bounded acceptance call.

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

The driver must print the frontend URL, backend URL, immutable git SHA, active revision names, health status, database durability posture, Firebase-domain status, and scale settings. It must never print an Azure key, Firebase private key, registry credential, bearer token, or connection string.

## Validation and Acceptance

Deployment is accepted only when all of the following are true.

The branch passes backend lint and tests plus frontend lint, type checking, tests, and production build. Both container images build from clean source, run as non-root, and answer their expected ports locally. Both Bicep templates compile, and Azure `what-if` contains only the named Murmur pilot resources.

The backend public URL returns HTTP 200 from `/healthz` and `/readyz` over HTTPS. The readiness endpoint proves database access and validates required Firebase and Azure model configuration without making a paid model request or revealing configuration. An `OPTIONS` request from the exact frontend origin receives the expected CORS allow-origin header, while an unrelated origin does not.

Azure reports one active revision per app, `maxReplicas` equal to one, no inline secret values for provider credentials, Key Vault references owned by the managed identity, and digest-pinned images carrying the full accepted git SHA. The backend database is local SQLite with WAL and no shared volume; acceptance must state that its data can reset on scale-down or deployment and cannot be treated as durable.

The frontend public URL loads over HTTPS, its browser bundle calls the Azure backend rather than localhost, and the existing Firebase user can reach `/canvas/storyboard`. The hostname is present in Firebase Authentication authorized domains. The storyboard UI can form an authenticated request and render the safe initial state. A live model beat is run only under a separately recorded strict call and cost bound.

The pull request CI must pass before merge. After merge, the live apps must be rebuilt or updated from the merge SHA and the verification report must show that exact SHA. Only then is “Murmur is deployed to Azure” a supported claim.

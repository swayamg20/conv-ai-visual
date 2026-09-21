# Deploy Murmur Voice V2 on Azure

## Purpose / Big Picture

This change makes the Voice button in the deployed Murmur pilot establish a bounded, observable audio session instead of entering the legacy `Checking voice...` state indefinitely. A signed-in user will grant microphone access, connect to a LiveKit room, and speak through Murmur's existing Deepgram speech-to-text, Groq text model, and ElevenLabs text-to-speech cascade. If any stage is unavailable, the browser must stop within a fixed deadline and show an actionable error rather than spin forever.

The Azure pilot remains deliberately small: one warm web replica and one warm backend replica. The backend replica will contain two tightly coupled containers, the FastAPI API and the LiveKit worker, and both will mount one replica-scoped `EmptyDir` at `/home/murmur/data`. This is necessary because the worker re-authorizes every job from the same `AgentRepo` and `SessionRepo` records the API wrote to local SQLite. It is a non-durable one-call canary, not the final scale architecture. After the application moves to PostgreSQL, the worker should become its own Container App so API and media work can scale and roll independently.

## Progress

- [x] 2026-09-21 22:10 IST: Reproduced the deployed failure from Azure request logs: `/offer` returned 200, but the legacy browser runtime never reached a ready event and had no whole-connection timeout.
- [x] 2026-09-21 22:35 IST: Traced the existing Voice V2 browser, API, worker, provider, and repository ownership path.
- [x] 2026-09-21 22:55 IST: Selected a LiveKit Cloud media plane with an Azure Container Apps worker sidecar and a replica-scoped shared data volume for the single-replica pilot.
- [x] 2026-09-21 23:18 IST: Exported the locked Voice V2 dependency set into the production requirements, added an image import smoke check, and passed 145 targeted backend tests.
- [x] 2026-09-22 00:02 IST: Extended the guarded Azure contract with eight version-pinned runtime secrets, a same-image worker sidecar, shared `EmptyDir`, one-call resources, release-scoped dispatch, and registration-aware health.
- [x] 2026-09-21 23:28 IST: Made `voice_v2` a required production web build input; a missing or legacy value now fails the image build instead of silently selecting aiortc.
- [x] 2026-09-22 00:09 IST: Updated live inspection and 89 deployer tests to reject image, command, runtime, secret, mount, volume, probe, worker-name, and build-selection drift.
- [x] 2026-09-22 00:42 IST: Passed 242 focused backend/deployer tests, Ruff, both Bicep compiles, the targeted frontend suite and TypeScript check, a production `voice_v2` Next build, and the exact production backend image build.
- [x] 2026-09-22 00:45 IST: Ran the production worker entrypoint in the rebuilt container without paid provider traffic; its registration-aware endpoint returned 503 while LiveKit was unreachable, proving the revision cannot become ready before registration.
- [x] 2026-09-22 00:50 IST: Prepared the completed runtime, infrastructure, deployer, tests, and this live plan as one substantive milestone while excluding the unrelated pre-existing pilot-plan edit; remote verification follows immediately after the commit.
- [ ] Provision a dedicated LiveKit Cloud project credential, rotate all voice credentials into Key Vault without printing them, and deploy the exact pushed commit.
- [ ] Run one authenticated browser acceptance call and verify microphone publication, worker registration, STT, LLM, TTS, interruption, teardown, and Azure logs.

## Surprises & Discoveries

- The currently deployed page is provably using the legacy runtime. `web/src/features/voice/session-view.ts` maps an unset or unknown `NEXT_PUBLIC_VOICE_RUNTIME` to `legacy`, and the displayed `Checking voice...` label exists only in `web/src/features/voice/session-runtime-controller.tsx`'s legacy controller.
- The legacy hook enters `connecting` before microphone, bootstrap, or RTC work but starts its only timeout after the data channel opens. A successful `/offer` therefore does not prove usable audio and the UI can remain busy forever.
- Voice V2 already has 15-second deadlines for bootstrap, dynamic transport loading, transport connect, and agent-ready acknowledgement in `web/src/hooks/use-voice-session.ts`. The safer fix is to deploy that path rather than expand the legacy aiortc path.
- The production backend image installs `requirements.txt`, but the pinned LiveKit packages currently exist only in the `voice-v2` optional dependency group in `pyproject.toml`. The live API therefore cannot create the LiveKit control plane and cannot run the worker even if environment variables are added.
- The worker directly reads the API's authoritative SQLite records in `backend/murmur/voice/worker_authorization.py`. A separate Container App with another ephemeral disk would reject every job. Azure Files is not a valid SQLite escape hatch; the pilot already observed locking incompatibility.
- The local owner-only backend dotenv contains configured Deepgram, Groq, and ElevenLabs credentials, but no LiveKit project URL, key, or secret was found. Cloud activation needs a dedicated LiveKit project credential before the final deployment step.
- Azure keeps the old and new Container Apps revisions alive together during a Single-mode rollout. A stable LiveKit worker name could therefore cross-dispatch a call to a worker backed by another revision's replica-local database and signing key. Both API and worker identities are now derived from the immutable release SHA plus the version of the per-rollout signing secret, so a same-SHA retry remains isolated.
- LiveKit Agents 1.6.9 starts its port-8081 health server before it registers the named worker, and `/` can return 200 while registration is still in flight. Murmur now starts a private port-8082 readiness server whose response turns healthy only after `worker_registered`; Azure startup and readiness use that endpoint while liveness continues to use the SDK health endpoint.
- Docker Desktop was initially stopped, then started for verification. The production backend image built successfully, imported `livekit.api`, `livekit.agents`, and Murmur Voice V2 as its non-root runtime user, and exposed the pinned LiveKit `start` CLI without contacting a paid provider.
- A Container App user-assigned identity is available to application code by default even when it was intended only for ACR pulls and platform Key Vault references. Both app resources now use the 2025-01-01 API and set that identity's lifecycle to `None`, so neither public API nor web code can obtain its token.
- ARM could otherwise update the voice-enabled frontend before the corresponding backend revision is healthy. The frontend resource now explicitly depends on the backend resource, preserving a backend-first cutover inside the deployment.

## Decision Log

- 2026-09-21, Codex: Use the existing `livekit_v2` runtime as the Azure canary. It already implements signed bootstrap, explicit dispatch, authoritative job re-authorization, canonical ready acknowledgement, bounded retries, interruption, and cleanup. Rebuilding equivalent behavior on legacy aiortc would increase risk and still leave TURN/media operations unsolved.
- 2026-09-21, Codex: Use LiveKit Cloud for signaling, ICE, and TURN while self-hosting the Murmur agent worker on Azure. Azure Container Apps exposes HTTP and TCP ingress, not the UDP surface required to operate a production WebRTC SFU; a LiveKit worker needs only an outbound WebSocket and no public ingress.
- 2026-09-21, Codex: Co-locate API and worker in one Container App for this pilot. Azure documents multiple tightly coupled containers sharing lifecycle and disk, which matches the present local-SQLite repository contract. Keep `minReplicas=1`, `maxReplicas=1`, and the worker's existing one-job load gate.
- 2026-09-21, Codex: Mount an explicit `EmptyDir` into both containers instead of relying on ambiguous container-local storage sharing. The volume is intentionally ephemeral and exists only to make the two processes observe the same pilot database.
- 2026-09-21, Codex: Treat a missing `NEXT_PUBLIC_VOICE_RUNTIME=voice_v2` as a deployment refusal for this release. Silent legacy fallback is what produced the current user-visible hang.
- 2026-09-21, Codex: Store every credential as a version-pinned Key Vault reference. The LiveKit URL and stable profile identifiers may be plain configuration; API keys, provider keys, and the Murmur job-signing secret must never enter an image, build argument, CLI output, or committed file.
- 2026-09-21, Codex: Scope `VOICE_V2_WORKER_NAME` to the immutable release SHA plus the version of the per-rollout signing secret. This preserves in-flight calls on an old revision while preventing its API or worker from pairing with a concurrently starting replacement revision, including a same-SHA retry or credential rotation.
- 2026-09-21, Codex: Gate Azure startup and readiness on LiveKit's `worker_registered` event rather than the SDK's generic HTTP health response. A revision cannot receive traffic merely because its worker process has started listening locally.
- 2026-09-22, Codex: Bound LiveKit drain to 540 seconds and retain a 600-second Azure termination grace. The worker stops waiting before the platform's hard deadline, leaving one minute for provider and process cleanup.
- 2026-09-22, Codex: Deny managed-identity token access to both application containers with `identitySettings.lifecycle=None`; the Azure platform retains the identity only for ACR and version-pinned Key Vault resolution.
- 2026-09-22, Codex: Make the frontend deployment depend on the backend resource so the first Voice V2 web cutover cannot race ahead of its API and registered worker.

## Outcomes & Retrospective

The implementation and provider-free verification are complete on the feature branch. The exact production image builds, the guarded deployer enforces the two-container runtime and eight version-pinned secrets, and the worker remains unready until LiveKit confirms registration. The remaining work is external activation and live acceptance: provide a dedicated LiveKit Cloud credential, deploy the exact pushed SHA, and verify one authenticated microphone-to-audio call. The temporary shared SQLite design remains intentionally non-durable and single-replica.

## Context and Orientation

`web/src/app/(app)/session/[agentId]/page.tsx` selects the runtime from the build-time `NEXT_PUBLIC_VOICE_RUNTIME` value. `web/src/features/voice/session-runtime-controller.tsx` mounts either the legacy `useWebRTC` controller or the Voice V2 `useVoiceSession` controller. In Voice V2, `web/src/features/voice/session-api.ts` calls `POST /api/voice/session`; the browser transport then joins the returned LiveKit room and waits for the worker's canonical ready event.

`backend/murmur/api/routers/voice.py` exposes the authenticated Voice V2 bootstrap and end routes. `backend/murmur/voice/livekit_control.py` creates the room, signs short-lived participant access, and explicitly dispatches `VOICE_V2_WORKER_NAME`. `backend/murmur/voice/worker.py` is the standalone worker composition. `backend/murmur/voice/worker_runtime.py` registers one named worker, keeps one process warm, and admits only one active job. `backend/murmur/voice/provider_profiles/livekit_cascade.py` fixes the canary cascade to Deepgram Nova-3, Groq `openai/gpt-oss-120b`, and ElevenLabs Flash v2.5.

The API creates agents and sessions through the repositories under `backend/murmur/persistence/`. The worker re-loads those records in `backend/murmur/voice/worker_authorization.py` before it accepts a dispatch. With no `MURMUR_DATABASE_URL`, `backend/murmur/persistence/database.py` uses `/home/murmur/data/murmur.db`; both Azure containers must therefore mount the same replica-local directory.

`deploy/backend.Dockerfile` builds the image used by both the API and worker. `web/Dockerfile` consumes public browser configuration at build time. `infra/azure/apps.bicep` defines both Container Apps, Key Vault references, container commands, environment, mounts, probes, and resources. `scripts/deploy_azure.py` is the guarded release driver: it builds only from a pushed git archive, rotates versioned secrets, deploys immutable image digests, and verifies the live postconditions without exposing credential values.

## Plan of Work

First, make the backend image genuinely capable of running both processes. Export a locked, hash-pinned production dependency set that includes the existing `voice-v2` optional group, update the image build to install it, and prove the worker entrypoint can be discovered in production `start` mode without contacting providers during an image smoke check.

Second, extend the deployment input and secret-rotation model. The backend dotenv loader will require the LiveKit project URL/key/secret, Deepgram key, Groq key, ElevenLabs key and voice identifier. The signing secret will be generated by the deployer when no current version exists, or supplied through an owner-only input; it must be shared by the API and worker. Each secret write will use the existing single-write reconciliation, version pinning, retirement, and postcondition checks.

Third, change `infra/azure/apps.bicep`. The API container will select `VOICE_RUNTIME=livekit_v2` and receive LiveKit control-plane and signing configuration. A `voice-worker` container built from the same immutable backend digest will run the LiveKit Agents production `start` command, receive the same repository/runtime settings plus the provider secrets, expose the SDK liveness endpoint on private port 8081 and Murmur's registration-aware readiness endpoint on private port 8082, and mount the shared data volume. The API will mount the same volume. Combined pilot resources will use a valid Azure Consumption allocation and termination grace will allow bounded cleanup.

Fourth, require the web build to compile with `NEXT_PUBLIC_VOICE_RUNTIME=voice_v2`. Preserve the existing Voice V2 phase deadlines and error panel. Add focused tests proving the production build input cannot silently select legacy and the deployment inspector rejects a missing worker, mismatched image, missing mount/probe, inline secret, or absent runtime selection.

Finally, validate locally, commit and push the deploy-ready changes, provision the missing LiveKit project credential, deploy the exact pushed SHA, and perform one authenticated acceptance call. The call is successful only when Azure logs show worker registration and job acceptance, the browser reaches `Agent ready`, spoken input reaches Deepgram, the Groq answer reaches ElevenLabs, remote audio plays, barge-in interrupts output, and disconnect releases the room and worker capacity. Provider calls will remain zero until the user explicitly approves the single paid acceptance call.

## Concrete Steps

Run all commands from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual-azure-deploy` unless a command changes directories.

Inspect and export the locked backend voice dependency set:

    uv export --locked --extra voice-v2 --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
    .venv/bin/python -m pytest tests/test_voice_v2_bootstrap.py tests/test_voice_v2_worker.py tests/test_voice_v2_direct_profile.py -q

Validate infrastructure and the guarded release contract:

    az bicep build --file infra/azure/apps.bicep --stdout >/dev/null
    .venv/bin/python -m pytest tests/test_deploy_azure.py -q

Validate the browser runtime and production bundle:

    cd web
    npm test -- --run src/features/voice/session-view.test.ts src/features/voice/session-runtime-controller.test.tsx src/features/voice/livekit-transport.test.ts src/hooks/use-voice-session.test.tsx
    npx tsc --noEmit
    NEXT_PUBLIC_VOICE_RUNTIME=voice_v2 npm run build

Build and smoke the exact backend container locally where Docker is available:

    docker build -f deploy/backend.Dockerfile -t murmur-voice-v2:local --build-arg MURMUR_RELEASE_SHA=0000000000000000000000000000000000000000 .
    docker run --rm --entrypoint python murmur-voice-v2:local -c "import livekit.api, livekit.agents; import murmur.voice.livekit_control"

After all relevant tests pass, stage only the plan and substantive implementation files, review `git diff --cached`, create a meaningful commit, and push it. The pre-existing modification in `plans/2026-09-20-azure-pilot-deployment.md` must remain untouched unless it is deliberately reconciled in a later closeout commit.

Once the LiveKit project credential is available in an owner-only dotenv, run the guarded deployment driver with the pinned subscription and tenant already used by this pilot. The driver must build from the pushed commit, not the dirty working tree. Then run its read-only `verify` command against the same exact SHA and inspect Azure logs for both `api` and `voice-worker` containers.

## Validation and Acceptance

Static acceptance requires a clean Bicep compile, passing guarded-deployer tests, passing targeted Voice V2 backend and frontend suites, a successful production Next.js build with `voice_v2`, and an image smoke import of both `livekit.api` and `livekit.agents`. The deployment inspector must prove immutable digests, exact release SHA, one warm replica, the API and worker containers, shared `EmptyDir` mounts, API health/readiness probes, registration-aware worker startup/readiness on port 8082, SDK liveness on port 8081, version-pinned Key Vault references, and absence of inline secrets.

Live acceptance uses a fresh authenticated user session. The first Voice click may request microphone permission; the second must move through `Connecting transport...`, `Transport connected - checking agent...`, and `Agent ready` or terminate with a visible error in no more than the configured deadlines. Speaking a short question must create a transcript and audible reply. Speaking over the reply must stop the old audio promptly and allow a new response. Switching to Chat, disconnecting Voice, navigating away, and ending the session must leave no active worker job. Azure logs must correlate the call identifier across API bootstrap, dispatch, worker authorization, ready publication, provider stages, and cleanup without logging tokens, API keys, raw credentials, or unbounded user audio.

The pilot is not accepted as durable or horizontally scalable. Restarting or revising the backend may erase its replica-local database. Before increasing `maxReplicas` or separating the worker, move the repositories to PostgreSQL and rerun the same authorization and real-media acceptance tests against independently deployed API and worker apps.

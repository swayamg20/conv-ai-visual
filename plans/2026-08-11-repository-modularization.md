# Make Murmur a Clean, Modular, Enforceable Repository

## Purpose / Big Picture

This refactor turns Murmur from a capable research prototype with accumulated implementation paths into a repository that a new engineer can understand, run, test, and change safely. The product behavior must remain recognizable: an authenticated user creates an agent, starts a chat or voice session, receives synchronized canvas and speech output, and can resume durable tutoring context. The change is successful when those flows still work while the code has explicit application, API, runtime, persistence, LLM, voice, canvas, and frontend feature boundaries.

“Clean” in this plan is an observable engineering property, not a formatting exercise. There must be one active implementation for each concern, runtime data must not be committed, protected data must not be exposed through unauthenticated routes, repository checks must run non-interactively in CI, and large modules must delegate to focused modules with tests at their boundaries. `main.py` should become a compatibility entrypoint rather than the application implementation. The frontend must typecheck, lint, test, and build from a clean checkout with documented environment assumptions.

A **chat session** is one text conversation backed by an `LLMPipeline`. A **voice session** is the WebRTC connection and associated STT, turn-detection, LLM, canvas, and TTS state for one authenticated user. An **agent** is a user-owned tutor configuration. **Cross-session memory** is the persisted summary and mastery context loaded when that user returns to the same agent. **Canvas mode** is the structured visual response path whose semantic scene is compiled and rendered in the browser. **Smart Turn** is the local ONNX model that decides whether a speaker has finished an utterance.

## Progress

- [x] 2026-08-11 15:17 IST: Audited the current `main` branch, file sizes, tracked artifacts, route auth boundaries, frontend build/typecheck state, dependency audit, and available tests.
- [x] 2026-08-11 15:17 IST: Re-read `.agent/PLANS.md` and created this living ExecPlan before beginning the cross-cutting refactor.
- [x] 2026-08-11 15:31 IST: Replaced the hand-maintained Python freeze with a bounded `pyproject.toml`, reproducible `uv.lock`, exported compatibility requirements, a Python 3.12 environment, and enforced Ruff lint/format gates; the authenticated session-continuity test passes against an isolated in-memory database.
- [ ] Establish deterministic repository gates: Python lint/test configuration, frontend non-interactive lint/typecheck/test/build scripts, and CI.
- [x] 2026-08-11 15:26 IST: Moved the existing SQLite/vector-store data and generated audio into gitignored `var/` storage, removed generated artifacts from version control, and separated experiments and manual provider scripts from the tested application surface.
- [ ] Protect and user-scope chat-control and observability routes before moving them into routers.
- [ ] Extract database engine/session setup, SQLModel declarations, and repositories from `funcs/models.py`; make the database path configurable and test-isolatable.
- [ ] Introduce an application factory and focused FastAPI routers; reduce root `main.py` to a compatibility entrypoint.
- [ ] Replace the collection of process-global session dictionaries with a typed session registry owned by application state.
- [ ] Extract the WebRTC/STT/turn/TTS orchestration from the API layer and cover interruption and cleanup invariants with tests.
- [ ] Split LLM provider clients and pipeline policies into focused modules while preserving the existing provider/tool contracts.
- [ ] Split the 1,520-line SVG renderer into typed scene normalization, render primitives, and timeline execution modules.
- [ ] Remove unused Excalidraw/legacy canvas implementations, duplicate Next.js configuration, stale routes, and their dependencies.
- [ ] Update README/setup/architecture documentation to describe only the resulting active system.
- [ ] Complete the security, build, test, and end-to-end acceptance audit and record the outcome here.

## Surprises & Discoveries

The frontend source is healthier than its directory history suggests. `web/tsconfig.json` enables strict mode, `tsc --noEmit` passes, and `next build` succeeds when the documented Firebase public variables are present. The most urgent frontend gaps are enforcement and duplication: the `lint` script launches an interactive setup prompt, no frontend tests exist, and obsolete Excalidraw code remains installed and globally imported.

The repository currently commits live runtime state. `memory.db` contains users, agents, sessions, LLM logs, voice logs, episodic memories, and decision records. `memory_db/` contains a vector-store database. These files must leave version control without silently deleting the developer's local copy.

The highest-risk backend issue is not naming or formatting. `main.py` exposes LLM and voice logs, including user messages and responses, without authentication. Its chat-clear and canvas-mode endpoints also lack authentication and ownership checks. These trust-boundary fixes must precede structural movement so the refactor does not merely rearrange unsafe behavior.

The existing “tests” are mostly executable demonstrations. Only `test/test_authenticated_session_continuity.py` is an assertion-based end-to-end test. Some other files perform live provider calls or write audio when invoked, so they must move to an explicit `scripts/` or `tests/manual/` location instead of being collected as automated tests.

The Python environment in the current shell has Python 3.14 but none of the backend dependencies installed. Python syntax was validated with `ast.parse`; runtime validation will use a repository-managed Python 3.11 or 3.12 environment created from the cleaned dependency manifest.

The first `uv lock` exposed a real incompatibility hidden by the old flat requirements file: the optional `kokoro-onnx` integration requires `onnxruntime>=1.20.1`, while the repository pinned `onnxruntime==1.19.2`. The active Smart Turn code does not depend on the older patch version, so the shared ONNX runtime constraint was raised to a compatible `>=1.20.1,<2` range and will be verified against both import paths.

The first Python 3.12 environment sync exposed a second stale binary pin: `aiortc==1.13.0` forces `av==14.4.0`, which has no matching macOS ARM64 wheel and cannot compile against the workstation's FFmpeg 8 headers. Resolving the supported `aiortc>=1.13,<2` range selects `aiortc==1.15.0` with a prebuilt-compatible `av==17.1.0`; the repository will keep the bounded compatibility range and prove it through a clean sync and runtime checks.

## Decision Log

2026-08-11, Codex: Preserve behavior first, but do not preserve accidental module boundaries. The target is a `backend/murmur/` package with explicit subpackages and a minimal root `main.py` compatibility entrypoint so existing `uvicorn main:app` workflows keep working during and after migration.

2026-08-11, Codex: Security fixes come before file movement. Authenticated ownership checks and user-scoped observability are easier to verify against current behavior and then carry into routers.

2026-08-11, Codex: Runtime state will default to a gitignored `var/` directory and support an environment override. Existing local databases will be preserved under `var/` before the committed copies are removed.

2026-08-11, Codex: SQLite remains acceptable for local development and a small pilot, but all database access must be isolated behind a database module and repository interfaces. The initial structural migration will not pretend that synchronous SQLModel calls are async; a subsequent milestone will either move those calls off the event loop or adopt SQLAlchemy async sessions with focused tests.

2026-08-11, Codex: There will be one production canvas implementation. The active SDL-to-SVG path stays; the unused Excalidraw component, legacy canvas renderer, global Excalidraw stylesheet, and dependency will be removed after reference checks.

2026-08-11, Codex: The user authorized ongoing GitHub pushes. Each coherent checkpoint will be committed and pushed to `origin/main` only after its relevant checks pass; partial dependency or refactor breakage will not be published as a checkpoint.

## Outcomes & Retrospective

Work is in progress. The baseline audit is complete and the refactor has not yet met acceptance. This section will record shipped module boundaries, deleted legacy paths, validation results, remaining risks, and any deviations from the plan after implementation.

## Context and Orientation

The current backend starts at root `main.py`. Importing it creates the FastAPI application, initializes the default LLM and TTS pipelines, registers a database-backed web-search tool, declares request schemas, owns roughly fifteen process-global state collections, implements WebRTC audio consumption, performs voice turn orchestration, and declares every HTTP route. This concentration makes route imports expensive, complicates tests, and couples transport behavior to persistence and provider SDKs.

The current `funcs/models.py` defines the SQLite engine, all SQLModel tables, every repository class, lexical resource scoring, and import-time schema creation. `funcs/memory.py` and `funcs/llm_pipeline.py` use those repositories directly. `funcs/auth.py` verifies Firebase ID tokens and provisions users. The refactor must preserve that identity contract: the bearer token is authoritative, client-supplied user IDs are never trusted, and every owned resource is checked against the authenticated user.

The current LLM layer is split between `funcs/llm_clients.py`, which implements provider SDK behavior, and `funcs/llm_pipeline.py`, which assembles memory, tools, canvas state, execution policy, streaming, and metrics. Both files are approximately one thousand lines. Provider-specific serialization belongs in provider modules; provider-independent tool policy and conversation orchestration belong in smaller services.

The current voice path spans `main.py`, `funcs/smart_turn.py`, `funcs/vad_gate.py`, `funcs/tts_pipeline.py`, `funcs/kokoro_tts.py`, and `funcs/interruption.py`. WebRTC peer state and per-turn tasks are stored in dictionaries keyed by peer ID. A typed registry must own these resources and expose cleanup as one idempotent operation so disconnect, interruption, idle eviction, and shutdown cannot drift apart.

The frontend lives in `web/src/`. `web/src/hooks/use-webrtc.ts` handles transport, client VAD, audio, and data-channel events. `web/src/components/svg-canvas.tsx` is the active renderer. `web/src/lib/scene-kit/` is already a useful boundary: the LLM emits semantic scene descriptions, the compiler determines layout, and the renderer draws them. `web/src/components/excalidraw-canvas.tsx` and `web/src/components/canvas-renderer.tsx` are inactive historical implementations.

The target layout is:

    backend/
      murmur/
        api/                 application factory, dependencies, schemas, routers
        core/                configuration and logging
        persistence/         database setup, models, repositories
        llm/                 provider clients, pipeline, tool policy
        memory/              context and durable-memory services
        voice/               WebRTC/STT/turn/TTS runtime
        canvas/              backend scene/tool state
      tests/
    web/
      src/features/          auth, agents, sessions, voice, canvas, observability
      src/components/ui/     reusable presentation primitives
      src/lib/scene-kit/     semantic scene compiler
    main.py                  compatibility import of the application
    var/                     ignored local runtime state

The migration may use temporary compatibility imports, but each milestone must say when they are removed. The final state must not leave two supported import paths or duplicate implementations.

## Plan of Work

First, establish enforcement and remove dangerous ambiguity. Add a Python project configuration with Ruff and pytest settings, separate automated tests from manual provider scripts, configure ESLint non-interactively, add explicit frontend scripts, and create CI jobs that run backend static/unit checks and frontend lint/typecheck/build checks. Correct `.gitignore`, preserve local database contents under `var/`, and remove generated or OS-specific files from version control. Protect the current observability and chat-control routes and add tests proving unauthenticated and cross-user requests fail.

Second, isolate persistence. Create `backend/murmur/persistence/database.py` for the engine and session factory, split table declarations from repositories, make `MURMUR_DATABASE_URL` or `MURMUR_DATA_DIR` select the database, and move schema initialization to application startup. Tests must use a temporary SQLite database rather than the repository database. Split repositories by aggregate—users/agents, sessions/messages/mastery, resources, tools, and observability—so route and service dependencies are explicit.

Third, extract the application surface. Create a FastAPI application factory and routers for auth, agents, resources, sessions, observability, chat, and voice signaling. Put request/response models beside the API rather than inside the runtime entrypoint. Introduce dependency functions for authenticated user identity and service access. Keep `main.py` as a short compatibility module exporting the constructed `app`.

Fourth, isolate runtime state. Replace the global dictionaries with a `SessionRegistry` stored on `app.state`. It will own chat pipelines, voice peers, data channels, active turn tasks, Smart Turn sessions, activity timestamps, and finalization guards through typed session records. Cleanup and idle eviction will call one idempotent registry/service boundary. Route modules will ask a chat or voice service to operate rather than manipulate maps directly.

Fifth, decompose the domain hotspots. Move provider implementations out of the shared LLM client file, split context assembly from tool execution and streaming, and add contract tests using fake clients. Move Deepgram streaming, audio-frame conversion, voice turn processing, SDL/TTS synchronization, and peer lifecycle into `voice/` modules. Split the SVG renderer without changing the SDL contract, using focused unit tests for normalization/layout and a browser-level smoke test for one synchronized visual sequence.

Sixth, remove migration scaffolding and stale implementations. Delete unused renderers, obsolete components, dead imports, duplicate configs, manual tests from automated test locations, and documentation that describes removed paths. Remove unused dependencies and refresh lockfiles. No compatibility shim remains unless an external documented contract requires it.

Finally, run the complete acceptance audit. Validate a clean dependency install, all static checks, all automated tests, production frontend build, backend startup, authenticated chat continuity, authenticated WebRTC signaling, cross-session memory, ownership failures, observability scoping, and one synchronized canvas sequence. Inspect tracked files and references to prove runtime data, generated artifacts, stale names, and obsolete modules are absent.

## Concrete Steps

All commands run from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual` unless a command changes directory explicitly.

Establish the baseline and inspect every change throughout the refactor:

    git status --short --branch
    git diff --check
    rg --files

Run backend formatting, linting, typing where configured, and automated tests:

    python3.12 -m venv .venv
    .venv/bin/pip install -e '.[dev]'
    .venv/bin/ruff format --check backend main.py
    .venv/bin/ruff check backend main.py
    .venv/bin/pytest
    .venv/bin/python -c "from main import app; print(app.title)"

If Python 3.12 is unavailable, Python 3.11 is acceptable. Python 3.14 is not the validation target until the pinned audio/ONNX dependencies declare support.

Run frontend gates:

    cd web
    npm ci
    npm run lint
    npm run typecheck
    npm test
    NEXT_PUBLIC_API_URL=http://localhost:8000 \
      NEXT_PUBLIC_FIREBASE_API_KEY=test-key \
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=test.firebaseapp.com \
      NEXT_PUBLIC_FIREBASE_PROJECT_ID=test-project \
      NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=test.appspot.com \
      NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=123456 \
      NEXT_PUBLIC_FIREBASE_APP_ID=1:123456:web:test \
      npm run build

Start the backend in a provisioned environment and check public versus protected behavior:

    .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
    curl -i http://127.0.0.1:8000/api/logs
    curl -i -X DELETE http://127.0.0.1:8000/chat/not-owned

The two requests above must return `401`, not data or a success response. Authenticated tests will use patched Firebase verification and temporary users rather than real credentials.

Before completion, inspect repository hygiene:

    git ls-files | rg '\.(DS_Store|db|db-wal|db-shm|sqlite|wav|tsbuildinfo)$'
    rg -n 'voiceai|client/test.html|ExcalidrawCanvas|CanvasRenderer' README.md docs backend web/src main.py
    git diff --check

The first command should return no runtime/generated/OS files except deliberately documented test fixtures. The second may mention historical migration notes only; active setup and architecture documentation must use the current names and paths.

## Validation and Acceptance

The repository is accepted as clean and modular only when all of the following are proven against the resulting tree.

From a clean dependency install, backend lint and tests, frontend lint/typecheck/tests, and the production frontend build all run non-interactively and pass. CI contains the same gates, so local success is not the only protection.

`main.py` is a compatibility entrypoint with no business logic. FastAPI routers contain transport mapping only; LLM, persistence, memory, and voice behavior live behind focused services. No module combines database engine creation, all models, all repositories, scoring logic, and schema initialization. No process-wide set of loosely related session dictionaries remains in the entrypoint.

Unauthenticated requests cannot clear or modify chat sessions and cannot read LLM or voice logs. Authenticated observability is scoped to the authenticated user unless a separately tested administrator role is introduced. Cross-user agent, resource, session, and log access returns `403` or an indistinguishable `404` according to the route contract.

Automated tests use temporary databases and fake provider clients. They do not write into `memory.db`, call paid APIs, or create output audio in the repository. Manual/live-provider scenarios are clearly separated and excluded from default test collection.

Exactly one active canvas renderer and one Next.js configuration remain. TypeScript strict mode remains enabled. The SDL compiler contract is tested, and the session UI can render at least one scene while consuming the same event contract used by chat and voice.

Runtime databases, vector stores, generated caches, OS metadata, build artifacts, and ad hoc audio recordings are absent from version control. Local data present at the start of the refactor is preserved in the ignored `var/` directory or an explicitly documented backup location.

The final runtime scenario creates an authenticated agent and session, sends at least two chat turns, ends the session, starts a second session for the same agent, and observes prior-session context in the fake-client response. A voice-signaling test proves authentication and ownership before a peer connection is accepted. A cleanup test proves repeated disconnect/finalize calls are safe.

Documentation names the current repository and package, gives reproducible setup commands, explains the module boundaries, and no longer advertises deleted routes, files, or implementations. `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` in this plan reflect the final evidence rather than intended work.

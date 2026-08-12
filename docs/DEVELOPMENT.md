# Development guide

Read the root [AGENTS.md](../AGENTS.md) before planning a large change. Cross-layer, persistence, authentication, session, memory, or voice work requires a living ExecPlan under [`plans/`](../plans/).

## Supported environments

- Backend: Python 3.11 or 3.12, managed with `uv`
- Frontend: Node.js 22, installed with `npm ci`
- Local persistence: SQLite under ignored `var/`

Use `uv.lock` and `web/package-lock.json` as the reproducibility contracts. Regenerate `requirements.txt` only as a compatibility export.

## Source rules

- `murmur` is the only backend package root; do not reintroduce `funcs` shims.
- `main.py` stays a compatibility launcher with no business logic.
- Routers perform transport mapping; services own workflows; repositories own persistence.
- Runtime chat and voice state belongs in typed records under `RuntimeRegistry`.
- External providers sit behind injectable contracts and must not run in default tests.
- Client identity is never authoritative; resolve ownership from Firebase claims and server state.
- Canonical canvas types live under `web/src/features/canvas/`.
- Generated output, databases, models downloaded at runtime, and live-provider artifacts belong under `var/`.

## Backend checks

```bash
uv sync --locked --extra dev --extra voice-v2
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -c "from main import app; print(app.openapi()['paths'].keys())"
```

Use `uv run ruff format <paths>` before committing edited Python files. Default pytest collection is offline and provider-free.

### Real local RTC gate

The full provider-free media proof is intentionally separate from ordinary pytest. It starts a digest-pinned LiveKit Server container, guarded FastAPI app and fake worker, an isolated production Next.js build, and Chromium on dedicated loopback ports. Install Docker and Playwright Chromium, then run from the repository root:

```bash
cd web
npx playwright install chromium
cd ..

uv run python scripts/voice_e2e_stack.py
```

The runner removes ambient provider credentials from its child environment and writes logs and machine-readable proof under ignored `var/voice-e2e/` and `var/evals/`. A pass requires non-zero inbound and decoded outbound media, canonical turns, interruption within the declared gate, and exact browser/worker/room/dispatch cleanup. It is not evidence of credentialed provider, LiveKit Cloud, or TURN/TLS quality.

## Frontend checks

```bash
cd web
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

The frontend is strict TypeScript. Put pure canvas or data transformations in feature modules with direct Vitest coverage; mount components only where behavior depends on refs, effects, or browser APIs.

## Adding behavior

### API capability

1. Define request/response types in `murmur.api.schemas` or a domain model.
2. Implement behavior in the owning service.
3. Add an authenticated thin router.
4. Add ownership and failure-path tests.
5. Update `tests/test_api_contract.py` if the public route surface changes.

### Persistence capability

1. Add or change the SQLModel declaration.
2. Add focused repository methods.
3. Decide how existing databases migrate; `create_all()` is not a migration.
4. Test against the in-memory fixture.

### Provider integration

Keep SDK request/response conversion in the adapter. Test with fake SDK responses. Never require credentials in CI.

### Voice change

Trace signaling, audio transport, transcription, turn confirmation, LLM/TTS scheduling, interruption, and cleanup before editing. Add deterministic tests at the affected boundary and prove cancellation leaves no task or registry state behind.

## Before pushing

```bash
git diff --check
git status --short
git ls-files | rg '\.(db|db-wal|db-shm|sqlite|wav|pcm|pyc|tsbuildinfo)$' \
  | rg -v '^tests/fixtures/voice/audio/.*\.wav$'
```

The tracked-artifact query should return nothing; small synthetic WAVs are allowed only under the reviewed voice-fixture directory. Commit coherent green checkpoints; do not publish a partially broken refactor.

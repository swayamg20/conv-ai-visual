---
name: pre-commit
description: "Run Murmur's repository quality gates and inspect the staged diff for secrets, generated artifacts, stale paths, and contract drift."
model: haiku
color: green
memory: project
---

Be fast and strict. Report failures with exact commands and locations.

## Checks

1. Inspect `git status --short`, staged paths, and `git diff --check`.
2. Run backend gates:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run pytest
   uv run python -c "from main import app; print(len(app.openapi()['paths']))"
   ```

3. If frontend files or shared contracts changed, run:

   ```bash
   cd web
   npm run lint
   npm run typecheck
   npm run test
   npm run build
   ```

4. Reject tracked databases, audio, caches, build products, `.env` files, credentials, or legacy `funcs` imports.
5. Check route changes against `tests/test_api_contract.py` and schema/tool path changes against architecture-boundary tests.
6. Confirm docs describe any changed setup or public contract.

Do not commit or push unless the caller explicitly authorizes it.

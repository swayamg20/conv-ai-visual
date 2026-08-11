# Database

Murmur uses SQLModel on SQLite for local development and the current small-scale deployment shape.

## Location and overrides

The default file is:

```text
var/murmur.db
```

`var/` is ignored and is the only default location for mutable repository-local state.

- `MURMUR_DATA_DIR=/path/to/data` changes the containing directory.
- `MURMUR_DATABASE_URL=sqlite:////absolute/path/murmur.db` replaces the complete SQLAlchemy URL.
- Tests use `sqlite:///:memory:` with a shared static pool.

## Lifecycle

`murmur.persistence.database` creates the engine without creating tables at import time. The FastAPI lifespan calls `init_db()` before services accept traffic. SQLite connections enable foreign keys, a busy timeout, and WAL for file-backed databases.

The project currently uses `SQLModel.metadata.create_all()`, not a versioned migration framework. It creates missing tables but does not transform existing columns. Back up production-like data and introduce a guarded migration before making an incompatible schema change.

## Table domains

Fourteen tables are declared in `murmur.persistence.models` and accessed through focused repositories:

| Domain | Tables | Repository module |
| --- | --- | --- |
| Identity | `user`, `agent` | `repositories.identities` |
| Sessions | `session`, `conversation_message`, `topic_mastery` | `repositories.sessions` |
| Memory | `episodic_memory`, `user_profile`, `decision_memory` | `repositories.memory` |
| Resources | `resource`, `resource_chunk` | `repositories.resources` |
| Tools | `tool` | `repositories.tools` |
| Observability | `llm_call_log`, `voice_pipeline_log`, `tts_resilience_log` | `repositories.observability` |

Routers and provider clients do not issue ad hoc SQL. Add persistence behavior to the repository for the owning domain.

## Programmatic access

```python
from murmur.persistence import get_session, init_db
from murmur.persistence.models import AgentModel
from murmur.persistence.repositories.identities import AgentRepo

init_db()

with get_session() as session:
    agent = session.get(AgentModel, "agent-id")

owned_agents = AgentRepo.list_by_user("firebase-user-id")
```

Prefer repository methods in application code. Direct sessions are useful for maintenance scripts and focused diagnostics.

## Inspect locally

Stop writers when performing manual edits. Read-only inspection is safe while WAL is active:

```bash
sqlite3 var/murmur.db
```

```sql
.tables
.schema agent
SELECT id, name, user_id FROM agent LIMIT 20;
.quit
```

Never commit database files, WAL/SHM companions, vector-store contents, credentials, or generated audio. If a local database must be preserved during maintenance, copy it outside tracked paths or keep it under `var/`.

## Test isolation

The pytest fixture replaces the global engine with an in-memory engine and rebuilds all tables before each test. Automated tests must use that fixture and must not read or write the developer's `var/murmur.db`.

Repository tests live in `tests/test_persistence_repositories.py`; schema and lifecycle behavior is also exercised through the application integration tests.

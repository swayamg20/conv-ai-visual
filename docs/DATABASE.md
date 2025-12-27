# Database Guide

## Location

```
voiceai/memory.db  (SQLite)
```

## Connecting

### Option 1: SQLite CLI

```bash
cd /path/to/voiceai
sqlite3 memory.db
```

Common commands:
```sql
.tables              -- List all tables
.schema tools        -- Show table structure
SELECT * FROM tools; -- Query data
.quit                -- Exit
```

### Option 2: DB Browser for SQLite (GUI)

```bash
brew install --cask db-browser-for-sqlite
```
Then open `memory.db` file.

### Option 3: TablePlus (GUI)

```bash
brew install --cask tableplus
```
Create new SQLite connection → select `memory.db`.

### Option 4: VS Code Extension

Install "SQLite Viewer" or "SQLTools" extension, then open `memory.db`.

### Option 5: Python

```python
from funcs import get_session
from funcs.models import ToolModel, ToolRepo

# Query via ORM
with get_session() as session:
    tools = session.query(ToolModel).all()
    for t in tools:
        print(t.name, t.description)

# Or use Repo classes
tools = ToolRepo.list_all()
```

---

## Tables

### `tools` - Function calling tools

```sql
CREATE TABLE tools (
    name TEXT PRIMARY KEY,           -- Tool name (snake_case)
    description TEXT NOT NULL,       -- What the tool does (LLM uses this)
    parameters TEXT NOT NULL,        -- JSON Schema for arguments
    handler_module TEXT,             -- Python module path (optional)
    handler_function TEXT,           -- Function name in module (optional)
    code TEXT,                       -- Inline Python code (preferred)
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Add a tool:**

```sql
INSERT INTO tools (name, description, parameters, code, enabled, created_at, updated_at)
VALUES (
    'get_current_time',
    'Get the current date and time. Use when user asks what time it is.',
    '{"type": "object", "properties": {}, "required": []}',
    'def get_current_time():
    now = datetime.datetime.now()
    return f"Current time is {now.strftime(''%H:%M:%S'')} on {now.strftime(''%B %d, %Y'')}"',
    1,
    datetime('now'),
    datetime('now')
);
```

Or via Python:
```python
from funcs import ToolRepo

ToolRepo.upsert(
    name="get_current_time",
    description="Get the current date and time",
    parameters={"type": "object", "properties": {}, "required": []},
    code='''
def get_current_time():
    now = datetime.datetime.now()
    return f"It is {now.strftime('%H:%M')}"
'''
)
```

---

### `episodic_memory` - Conversation summaries

```sql
CREATE TABLE episodic_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    summary TEXT NOT NULL,           -- AI-generated summary of conversation
    turn_count INTEGER,
    metadata TEXT,                   -- JSON
    created_at TIMESTAMP
);
```

**Query past conversations:**

```sql
SELECT summary, created_at 
FROM episodic_memory 
WHERE user_id = 'user123' 
ORDER BY created_at DESC 
LIMIT 5;
```

---

### `user_profile` - User identity (Layer 4)

```sql
CREATE TABLE user_profile (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    timezone TEXT,
    preferences TEXT,                -- JSON
    facts TEXT,                      -- JSON
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Set user info:**

```sql
INSERT INTO user_profile (user_id, name, timezone, preferences, facts)
VALUES ('user123', 'Swayam', 'Asia/Kolkata', '{"theme": "dark"}', '{"role": "developer"}')
ON CONFLICT(user_id) DO UPDATE SET
    name = excluded.name,
    timezone = excluded.timezone;
```

---

### `decision_memory` - Tool execution log

```sql
CREATE TABLE decision_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    action TEXT NOT NULL,            -- e.g. 'tool:get_weather'
    tool_used TEXT,
    success BOOLEAN,
    context TEXT,
    created_at TIMESTAMP
);
```

**Check recent tool failures:**

```sql
SELECT action, tool_used, created_at 
FROM decision_memory 
WHERE success = 0 
ORDER BY created_at DESC 
LIMIT 10;
```

---

## Troubleshooting

### Database is locked

SQLite only allows one writer at a time. If your server is running:

1. Stop the server, or
2. Use Python inside the same process:
   ```python
   from funcs import ToolRepo
   ToolRepo.upsert(...)
   ```

### Table doesn't exist

Run the app once to auto-create tables, or:

```python
from funcs.models import init_db
init_db()
```

### View all data

```sql
SELECT * FROM tools;
SELECT * FROM episodic_memory;
SELECT * FROM user_profile;
SELECT * FROM decision_memory;
```


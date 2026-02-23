# Claude Code Setup

Reference for all configured agents, skills, and project instructions.

## CLAUDE.md

**Location:** `/CLAUDE.md` (project root)

Loaded automatically into every Claude Code conversation in this project. Contains:
- Code style rules (import ordering, async patterns, error handling)
- Non-obvious architecture (session dicts, SSE streaming, callbacks, pipeline flow)
- Known gotchas (Gemini protobuf, GSAP timelines, canvas_mode, shape primitives)
- Run commands

**When it matters:** Always — Claude reads this before doing anything. Keep it short and focused on things Claude would get wrong without guidance.

## Agents

Agents are specialized Claude instances you can launch via the `Task` tool or `/agents` command. They run as subprocesses with their own context.

### code-reviewer

**Location:** `.claude/agents/code-reviewer.md`
**Model:** Sonnet | **Color:** Red

Reviews recently written or modified code for correctness, security, performance, and project convention adherence. Knows this project's async patterns, session management, and known pitfalls.

**When to use:**
- After implementing a new feature or endpoint
- After refactoring across multiple files
- Before committing significant changes
- When you want a second pair of eyes on tricky code

**Triggered automatically** by Claude after writing significant code.

### pre-commit

**Location:** `.claude/agents/pre-commit.md`
**Model:** Haiku (fast) | **Color:** Green

Fast checks before committing:
1. Python syntax (`py_compile`) on all modified files
2. **Import placement** — flags imports that aren't at the top of the file
3. Async correctness — missing `await`, blocking calls in async, `requests` instead of `httpx`
4. Known gotchas — hardcoded keys, Gemini protobuf without `_to_native()`, GSAP issues
5. TypeScript build (`next build`) if web/ files changed
6. Sensitive file detection — `.env`, `memory.db`, credentials

**When to use:**
- Before every commit
- After a batch of changes across Python and TypeScript files
- When you're not sure if you introduced a subtle issue

### pipeline-debugger

**Location:** `.claude/agents/pipeline-debugger.md`
**Model:** Sonnet | **Color:** Yellow

Specialized for debugging the real-time audio pipeline. Understands the full data flow from WebRTC audio input through VAD, STT, Smart Turn, LLM, TTS, and back to the client.

**When to use:**
- Audio not playing or recording
- Transcription not working or delayed
- LLM responses slow or missing
- Tool calls failing silently
- Interruption detection not firing
- Smart turn detection behaving incorrectly
- SSE/streaming events not reaching the frontend
- Session cleanup issues or memory leaks

**How it works:** Classifies the problem, traces data flow through the pipeline, checks config, and identifies the specific failure point with file paths and line numbers.

## Skills

Skills are invoked with `/skill-name` in the chat. They provide templated workflows.

### /add-tool

**Location:** `~/.claude/skills/add-tool/SKILL.md`

Scaffolds a new LLM-callable tool for the tool calling system.

**When to use:**
- Adding a new capability the LLM can call (web search, calculator, API integration, etc.)
- Need to register a tool in the SQLite database

**What it does:**
1. Asks for tool name, description, and parameters
2. Generates a registration script at `scripts/register_<name>.py`
3. Uses `ToolRepo.upsert()` with proper JSON Schema parameters
4. Runs the script to register in the database
5. Verifies registration

**Example:** `/add-tool` → "weather lookup for a city" → generates and runs `scripts/register_get_weather.py`

### /ai-sdk-tools

**Location:** `~/.claude/skills/ai-sdk-tools/SKILL.md`

For the ixigo project (not Voice AI). Creates async JavaScript tools for the ixigo native mobile/web client SDK.

## Project Memory

**Location:** `~/.claude/projects/-Users-swayam-gupta-Documents-GitHub-voiceai/memory/MEMORY.md`

Persistent memory that carries across conversations. Contains architecture notes, key patterns, and common bugs & fixes discovered over time. Claude consults this automatically.

**When to update:** When you discover a new recurring bug, pattern, or architectural decision that future conversations should know about.

## Typical Workflows

### Adding a new feature
1. Claude reads `CLAUDE.md` automatically
2. Implement the feature
3. **code-reviewer** agent runs to review the code
4. **pre-commit** agent checks before committing

### Adding a new LLM tool
1. `/add-tool` — scaffold and register
2. Implement the tool logic
3. Test via chat session with `canvas_mode: true` if needed

### Debugging audio issues
1. Describe the symptom
2. Launch **pipeline-debugger** agent
3. It traces the pipeline and identifies the failure point

### Before committing
1. Launch **pre-commit** agent
2. Fix any flagged issues (especially import placement)
3. Commit

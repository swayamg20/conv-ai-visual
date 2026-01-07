# CLAUDE.md - Project Context for AI Assistance

## Project Overview

**Voice AI** is a real-time voice assistant platform built with WebRTC, featuring Speech-to-Text, Voice Activity Detection, LLM integration, and Text-to-Speech in a low-latency pipeline.

**Current Branch:** `feat/interruption` (working on interruption handling)
**Main Branch:** `main`

## Core Architecture

```
Audio Input → WebRTC → Deepgram STT → Silero VAD → LLM (OpenAI) → TTS (ElevenLabs) → Audio Response
```

### Tech Stack
- **Backend:** FastAPI (Python)
- **Audio:** WebRTC (aiortc), Deepgram STT, ElevenLabs TTS
- **VAD:** Silero VAD v6
- **LLM:** OpenAI (GPT-4o-mini default)
- **Database:** SQLite (tools, memory)
- **Memory:** mem0ai integration
- **Frontend:** Web client (in `/web`)

## Project Structure

```
voiceai/
├── main.py                 # FastAPI server, WebRTC endpoints
├── funcs/                  # Core modular components
│   ├── llm_pipeline.py     # LLM conversation pipeline
│   ├── tts_pipeline.py     # Text-to-Speech pipeline
│   ├── vad_gate.py         # Voice Activity Detection
│   ├── tools.py            # Tool calling system
│   ├── tool_executor.py    # Sandboxed tool execution
│   ├── memory.py           # Memory management
│   ├── models.py           # Pydantic models
│   ├── config.py           # Environment config
│   ├── canvas.py           # Canvas visual mode
│   └── auth.py             # User authentication
├── docs/                   # Documentation
│   ├── TOOLS.md            # Tool calling system docs
│   ├── DATABASE.md         # Database schema
│   ├── ROADMAP.md          # Project roadmap
│   ├── next_steps.md       # TODO and future plans
│   └── *.md                # Other documentation
├── test/                   # Test files
├── scripts/                # Utility scripts
├── web/                    # Frontend
├── memory.db               # SQLite database
└── requirements.txt        # Python dependencies
```

## Key Components

### 1. LLM Pipeline (`funcs/llm_pipeline.py`)
- Manages conversation context per session
- Automatic context trimming for token limits
- Tool calling integration
- Memory integration with mem0ai
- Supports both chat and voice sessions

### 2. Tool System (`funcs/tools.py`, `funcs/tool_executor.py`)
- Two modes: inline code (stored in DB) or module-based handlers
- Sandboxed execution with RestrictedPython
- Tools stored in SQLite (`tools` table)
- Available modules in sandbox: `json`, `datetime`, `httpx`, `math`, etc.
- See `docs/TOOLS.md` for detailed documentation

### 3. VAD Gate (`funcs/vad_gate.py`)
- Silero VAD model for speech detection
- Filters audio chunks before processing
- Reduces unnecessary LLM calls

### 4. Memory (`funcs/memory.py`)
- User-specific conversation memory
- Integration with mem0ai
- SQLite-based storage
- Per-user context persistence

### 5. Canvas Mode (`funcs/canvas.py`)
- Visual mode for UI components
- Supports various widget types (text, code, image, etc.)
- Can be enabled per message

### 6. Interruption Handling (`main.py`)
- **Server-side detection** using VAD (Phase 1 - Completed)
- Detects when user speaks while TTS is playing
- Automatically cancels ongoing TTS generation
- Sends acknowledgment to client
- Fast response (~50-100ms detection latency)

**How it works:**
1. `InterruptionState` class tracks TTS playback per session
2. VAD continuously monitors for user speech
3. When speech detected during TTS → signals interruption
4. TTS streaming loop checks interruption flag on each chunk
5. Breaks loop and sends `tts_cancelled` message to client
6. Client can stop playback immediately

**Client messages:**
- `interruption_ack` - Server confirms interruption detected
- `tts_cancelled` - TTS generation stopped, includes chunks_sent count

**Future enhancements (Phase 2/3):**
- Client-side interrupt signal via datachannel
- Client-side instant playback stop for lower latency

## Environment Variables

### Required
- `OPENAI_API_KEY` - OpenAI API key
- `ELEVENLABS_API_KEY` - ElevenLabs API key

### Optional (with defaults)
- `OPENAI_MODEL` - Default: "gpt-4o-mini"
- `DEEPGRAM_KEY` - Deepgram API key (has hardcoded default)
- `DEEPGRAM_MODEL` - Default: "nova"
- `LLM_TEMPERATURE` - Default: 0.7
- `LLM_MAX_TOKENS` - Optional
- `LLM_MAX_CONTEXT_MESSAGES` - Default: 20
- `LLM_SYSTEM_PROMPT` - Custom system prompt
- `ELEVENLABS_VOICE_ID` - Default: "21m00Tcm4TlvDq8ikWAM" (Rachel)
- `ELEVENLABS_MODEL_ID` - Default: "eleven_turbo_v2_5"
- `TTS_STABILITY` - Default: 0.5
- `TTS_SIMILARITY_BOOST` - Default: 0.75
- `HOST` - Default: "0.0.0.0"
- `PORT` - Default: 8000

## Current Status & Next Steps

### Completed Features
- ✅ Real-time STT → VAD → LLM → TTS pipeline
- ✅ Tool calling system with sandboxed execution
- ✅ Memory system with database
- ✅ Canvas API for visual mode
- ✅ Basic chat and voice sessions
- ✅ WebRTC integration
- ✅ **Interruption handling** - Server-side VAD-based detection (Phase 1)

### TODO (from `docs/next_steps.md`)
- Redis integration
- Live web search
- Calendar/events integration
- Re-routing LLM calls based on complexity
- Testing different LLMs/SLMs/classifiers
- Vector search scope discovery
- Sandbox coding tools with live execution

## Development Guidelines

### Running the Server
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export OPENAI_API_KEY=your_key
export ELEVENLABS_API_KEY=your_key

# Run server
uvicorn main:app --reload
# Or with custom host/port
uvicorn main:app --host localhost --port 3000 --reload
```

### Testing
```bash
# Run specific tests
python -m pytest test/test_llm.py
python -m pytest test/test_tts.py
python -m pytest test/test_memory.py
python -m pytest test/test_tool_system.py
```

### Adding New Tools
See `docs/TOOLS.md` for detailed instructions. Quick example:

```python
from funcs import ToolRepo

ToolRepo.upsert(
    name="tool_name",
    description="What the tool does",
    parameters={...},  # JSON Schema
    code='''
def tool_name(param):
    # Implementation
    return result
'''
)
```

## Code Style & Patterns

### Async/Await
- Most I/O operations are async
- Use `async def` for handlers, pipelines
- Prefer async libraries (httpx over requests)

### Error Handling
- Log errors with `logger.error()`
- Return error messages rather than raising in tools
- Graceful degradation where possible

### Session Management
- Voice sessions: `voice_sessions` dict (peer ID → LLMPipeline)
- Chat sessions: `chat_sessions` dict (session ID → LLMPipeline)
- User IDs for memory persistence

### Configuration
- All config in `funcs/config.py`
- Uses pydantic-settings for validation
- Environment variable overrides

## Important Files to Reference

- **Architecture:** `README.md`, `funcs/README.md`
- **Tools:** `docs/TOOLS.md`
- **Database:** `docs/DATABASE.md`
- **Roadmap:** `docs/ROADMAP.md`
- **Next Steps:** `docs/next_steps.md`
- **Main Entry:** `main.py`

## Common Operations

### Adding a New Pipeline Component
1. Create module in `funcs/`
2. Follow pattern from existing pipelines
3. Use async/await for I/O
4. Add config variables to `funcs/config.py`
5. Initialize in `main.py`
6. Update `funcs/__init__.py` exports

### Debugging WebRTC Issues
- Check browser console for client-side errors
- Monitor server logs for connection issues
- Verify STUN/TURN configuration
- Check audio format conversions in `audioframe_to_pcm16_bytes()`

### Database Changes
- Use SQLite CLI or SQLModel
- Update `funcs/models.py` for schema changes
- Consider migration if breaking changes

## Security Notes

### Sandboxed Tool Execution
- Tools run in restricted environment (RestrictedPython)
- No file system access
- No subprocess/os module access
- Limited imports (httpx, json, datetime, etc.)
- See `funcs/tool_executor.py` for sandbox config

### API Keys
- Never commit `.env` files
- Use environment variables
- Keys validated on startup in `main.py`

## References

- [TEN Turn Detection](https://github.com/TEN-framework/ten-turn-detection)
- [TEN VAD](https://github.com/TEN-framework/ten-vad)
- Silero VAD v6 documentation
- FastAPI documentation
- aiortc (WebRTC) documentation

## Tips for AI Assistants

1. **Always check docs/** before implementing new features
2. **Read existing code** in `funcs/` to understand patterns
3. **Test changes** with existing test files
4. **Follow async patterns** consistently
5. **Update documentation** when adding features
6. **Check `next_steps.md`** for alignment with roadmap
7. **Use modular design** - keep components in `funcs/`
8. **Consider memory/tools** integration for new features
9. **Log appropriately** - use Python logging module
10. **Handle errors gracefully** - this is real-time audio

## Contact & Contribution

- Check `docs/next_steps.md` for current priorities
- Follow existing code patterns and style
- Update relevant documentation with changes
- Test thoroughly before committing

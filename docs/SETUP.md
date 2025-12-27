# VoiceAI Setup Guide

Complete setup guide for running the VoiceAI project locally.

## Prerequisites

- Python 3.11+
- SQLite (comes with Python)
- Conda or pip for package management

## 1. Clone & Environment Setup

```bash
# Clone the repository
git clone https://github.com/your-username/voiceai.git
cd voiceai

# Create conda environment (recommended)
conda create -n voiceai python=3.11
conda activate voiceai

# Or use venv
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows
```

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

Key dependencies:
- `fastapi`, `uvicorn` - Web server
- `openai` - LLM API
- `sqlmodel` - Database ORM
- `httpx` - HTTP client for tools
- `RestrictedPython` - Sandbox for tool execution
- `mem0ai` - Semantic memory (optional)

## 3. Environment Variables

Create a `.env` file in the project root:

```bash
# Required
OPENAI_API_KEY=sk-your-openai-api-key

# Optional - Voice features
DEEPGRAM_KEY=your-deepgram-key
ELEVENLABS_API_KEY=your-elevenlabs-key
ELEVENLABS_VOICE_ID=your-voice-id

# Optional - Memory
MEM0_API_KEY=your-mem0-key

# Server config (optional)
HOST=0.0.0.0
PORT=8000
```

### Getting API Keys

| Service | Purpose | Get Key |
|---------|---------|---------|
| OpenAI | LLM (required) | https://platform.openai.com/api-keys |
| Deepgram | Speech-to-text | https://console.deepgram.com/ |
| ElevenLabs | Text-to-speech | https://elevenlabs.io/ |
| Mem0 | Semantic memory | https://mem0.ai/ |

## 4. Database Initialization

The SQLite database (`memory.db`) is auto-created on first run. Tables:

- `tools` - Function calling tools
- `episodic_memory` - Conversation summaries
- `user_profile` - User identity
- `decision_memory` - Tool execution logs

To manually initialize:

```python
from funcs.models import init_db
init_db()
```

## 5. Add Tools (Optional but Recommended)

Tools enable the LLM to perform actions. Add a sample tool:

```bash
sqlite3 memory.db
```

```sql
INSERT INTO tools (name, description, parameters, code, enabled, created_at, updated_at)
VALUES (
    'get_weather',
    'Get current weather for any city.',
    '{"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}',
    'def get_weather(city):
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
    geo_resp = httpx.get(geo_url)
    geo_data = geo_resp.json()
    if not geo_data.get("results"):
        return f"Could not find: {city}"
    loc = geo_data["results"][0]
    weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={loc[''latitude'']}&longitude={loc[''longitude'']}&current=temperature_2m,weather_code"
    weather_resp = httpx.get(weather_url)
    data = weather_resp.json()
    temp = data["current"]["temperature_2m"]
    return f"Weather in {city}: {temp}°C"',
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
    description="Get current date and time",
    parameters={"type": "object", "properties": {}, "required": []},
    code='''
def get_current_time():
    now = datetime.datetime.now()
    return f"It is {now.strftime('%H:%M on %B %d, %Y')}"
'''
)
```

See [TOOLS.md](./TOOLS.md) for more details.

## 6. Run the Server

```bash
# Development (with auto-reload)
python main.py

# Or directly with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Server runs at: http://localhost:8000

## 7. Test the API

### Chat Endpoint (SSE Streaming)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, what can you do?"}'
```

### With Session Persistence

```bash
# First message - get session_id
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is John", "user_id": "user123"}'

# Subsequent messages - use same session
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?", "session_id": "SESSION_ID_FROM_RESPONSE", "user_id": "user123"}'
```

### End Session (Saves to Episodic Memory)

```bash
curl -X DELETE http://localhost:8000/chat/SESSION_ID
```

## 8. WebRTC Voice (Optional)

For voice interaction, open `client/test.html` in a browser after starting the server.

Requirements:
- Deepgram API key (speech-to-text)
- ElevenLabs API key (text-to-speech)

## Project Structure

```
voiceai/
├── main.py              # FastAPI server
├── funcs/
│   ├── llm_pipeline.py  # LLM with memory & tools
│   ├── memory.py        # 4-layer memory system
│   ├── models.py        # SQLModel ORM
│   ├── tools.py         # Tool registry
│   ├── tool_executor.py # Sandboxed execution
│   ├── tts_pipeline.py  # Text-to-speech
│   └── vad_gate.py      # Voice activity detection
├── docs/
│   ├── SETUP.md         # This file
│   ├── TOOLS.md         # Tool creation guide
│   └── DATABASE.md      # Database reference
├── memory.db            # SQLite database (auto-created)
└── requirements.txt
```

## Memory Architecture

The system uses 4 memory layers:

| Layer | Storage | Purpose |
|-------|---------|---------|
| 1. Context | In-memory | Current conversation (sliding window) |
| 2. Episodic | SQLite | Past conversation summaries |
| 3. Semantic | Mem0 Cloud | Facts & entities (vector search) |
| 4. Profile | SQLite | User identity (name, preferences) |

## Troubleshooting

### Database Locked

```
Error: database is locked
```

Stop the running server, then access the DB. SQLite only allows one writer.

### Tools Not Working

1. Check tools exist: `sqlite3 memory.db "SELECT name FROM tools;"`
2. Check logs for compilation errors
3. Restart server to clear handler cache

### Import Errors

```bash
# Ensure you're in the correct environment
conda activate voiceai

# Reinstall dependencies
pip install -r requirements.txt
```

### Mem0 Not Working

Mem0 is optional. If not configured, semantic memory is skipped. Set `MEM0_API_KEY` in `.env` to enable.

## Next Steps

1. [Add custom tools](./TOOLS.md)
2. [Explore the database](./DATABASE.md)
3. Build a frontend that connects to the chat API
4. Integrate with voice via WebRTC

## Support

- Check existing docs in `/docs`
- Open an issue on GitHub


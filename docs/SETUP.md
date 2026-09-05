# Local setup

These steps reproduce the development environment used by CI.

## Prerequisites

- Python 3.11 or 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 and npm
- A Firebase project for login and backend token verification
- One supported LLM provider account: OpenAI, Groq, or Gemini
- Deepgram and a TTS provider only when exercising voice

## Install

```bash
git clone https://github.com/swayamg20/conv-ai-visual.git
cd conv-ai-visual

uv sync --locked --extra dev --extra voice-v2 --extra voice-pipecat

cd web
npm ci
cd ..
```

`requirements.txt` is an exported base compatibility file. Use `uv.lock` with the explicit Voice V2 extras for development and CI. `voice-v2` installs the LiveKit Agents/RTC arm; `voice-pipecat` installs the independent Pipecat/SmallWebRTC arm.

## Configure the backend

```bash
cp .env.example .env
```

For authenticated text chat, set:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=...
FIREBASE_PROJECT_ID=your-firebase-project-id
```

Use `OPENAI_API_KEY` with `LLM_PROVIDER=openai`, or `GEMINI_API_KEY` with `LLM_PROVIDER=gemini`.

The authenticated verified visual lesson at `/canvas/generate` is deliberately default-off. To enable its current Pythagorean slice, set:

```dotenv
MURMUR_SCENE_ENABLED=true
```

Scene authoring inherits `LLM_PROVIDER` and that provider's model and credentials by default. To select a separate supported provider or model, also set `MURMUR_SCENE_LLM_PROVIDER` and `MURMUR_SCENE_LLM_MODEL` with the matching server-side provider credentials. Keep the flag off when live scene-provider calls are not intended; unsupported lesson requests decline without changing the visible frontier.

For voice, also set:

```dotenv
DEEPGRAM_KEY=...
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
```

To use local Kokoro instead:

```bash
uv sync --locked --extra dev --extra local-tts
```

```dotenv
TTS_PROVIDER=kokoro
```

Deepgram endpointing is the default turn detector. To select Smart Turn, install its focused extra and enable it explicitly:

```bash
uv sync --locked --extra dev --extra smart-turn
```

Then set `SMART_TURN_ENABLED=true`. The model initializes lazily. If the selected Smart Turn dependency or model cannot initialize, voice fails closed before Ready; set `SMART_TURN_ENABLED=false` to explicitly select Deepgram endpointing.

Optional services:

- `MEM0_API_KEY` enables semantic memory; the other memory layers work without it.
- `TAVILY_API_KEY` enables live results from the built-in web-search tool.
- `FIREBASE_SERVICE_ACCOUNT_PATH` selects an explicit Firebase Admin credential file. Otherwise the Admin SDK uses the configured project and application-default credentials.

The backend loads the repository-root `.env`. Shell environment variables take precedence.

## Configure the frontend

```bash
cd web
cp .env.example .env.local
```

Fill in the Firebase web-app values and keep this local API URL unless the backend runs elsewhere:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Firebase web configuration is distinct from backend Admin credentials.

## Run

Use two terminals from the repository root:

```bash
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd web
npm run dev
```

Open <http://localhost:3000>. The backend contract is at <http://localhost:8000/docs>.

The first application startup creates `var/murmur.db` and registers the built-in `web_search` and `canvas_update` tools. The entire `var/` directory is ignored by Git.

## Verify the checkout

Backend:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -c "from main import app; print(app.title)"
```

Frontend:

```bash
cd web
npm run lint
npm run typecheck
npm run test
npm run build
```

The production build requires valid-looking `NEXT_PUBLIC_FIREBASE_*` values; CI provides deterministic test values.

## Optional sample tools

After configuration, register two local demonstration tools with:

```bash
uv run python scripts/add_sample_tool.py
```

Built-in tools do not need a setup script; startup keeps their definitions and canonical handler paths current.

## Troubleshooting

### Every product request returns 401

Confirm the frontend and backend point at the same Firebase project. Protected API calls must carry `Authorization: Bearer <Firebase ID token>`; `X-User-ID` is not an authentication mechanism.

### Chat starts but providers fail

Check that `LLM_PROVIDER` matches the populated key. Placeholder strings copied from `.env.example` are not valid credentials.

### Voice is unavailable

The HTTP app remains available, but the voice path fails closed and offers text fallback when a required component is missing. Check the startup log and readiness error for the missing Deepgram, LLM, TTS, or selected turn-detector configuration. For Kokoro, install the `local-tts` extra.

### Smart Turn cannot initialize

Install the `smart-turn` extra and verify its model path/download access. An enabled Smart Turn configuration is required, not silently replaced. Set `SMART_TURN_ENABLED=false` to explicitly use Deepgram endpointing instead.

### Database path is unexpected

The default is `var/murmur.db`. `MURMUR_DATA_DIR` changes the runtime directory; `MURMUR_DATABASE_URL` overrides the full SQLAlchemy URL.

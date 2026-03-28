# Murmur — Voice-First AI with a Living Canvas

A real-time voice assistant that thinks by drawing. Speak naturally, and watch ideas emerge as hand-sketched visuals on a shared canvas — synchronized to every word.

Built with WebRTC for sub-second latency, Rough.js for a hand-drawn aesthetic, and a novel Scene Description Language that separates *what to show* from *how to render it*.
---

## Screenshots

<p align="center">
  <img src="docs/assets/screenshot1.png" alt="Live canvas with hand-drawn diagrams" width="80%" />
</p>
<p align="center"><em>Live canvas — diagrams drawn in real-time as the AI speaks</em></p>
q
---

## How It Works

```
Voice Input → WebRTC → Deepgram STT → Smart Turn Detection → LLM → Scene Compiler → Canvas + TTS
```

1. **You speak** — WebRTC streams audio to the server in real-time
2. **Deepgram transcribes** — streaming STT with interim results
3. **Smart Turn detects** — ML-based turn detection knows when you're done (not just silence)
4. **LLM reasons** — generates a semantic scene description (~80 tokens, not pixel coordinates)
5. **Scene Compiler layouts** — deterministic client-side engine converts descriptions to render commands
6. **Canvas draws + Voice speaks** — each step animates on canvas in sync with TTS output

## Key Features

- **Real-time voice conversation** with interruption support and natural turn-taking
- **Live visual canvas** — diagrams, equations, charts, and graphs drawn as the AI speaks
- **15+ visual components** — triangles, coordinate planes, flowcharts, bar charts, equations, and more
- **Hand-drawn aesthetic** — Rough.js strokes, chalk-on-blackboard feel, GSAP animations
- **Step-pipelined sync** — each visual step plays in parallel with its corresponding speech
- **Tool calling** — LLM can execute tools (code sandbox, web search) mid-conversation
- **Multi-provider LLM** — OpenAI, Gemini, Groq with automatic model routing
- **Observability dashboard** — session logs, latency metrics, token usage at `/dashboard`

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (Next.js)                                     │
│  ┌───────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  WebRTC   │  │  SVG Canvas  │  │  Scene Compiler  │ │
│  │  Client   │  │  (Rough.js)  │  │  (Layout Engine) │ │
│  └─────┬─────┘  └──────┬───────┘  └────────┬─────────┘ │
└────────┼───────────────┼────────────────────┼───────────┘
         │               │                    │
    audio/data      render cmds          SDL steps
         │               │                    │
┌────────┼───────────────┼────────────────────┼───────────┐
│  Backend (FastAPI)     │                    │           │
│  ┌─────┴─────┐  ┌─────┴──────┐  ┌─────────┴─────────┐ │
│  │ Deepgram  │  │   TTS      │  │   LLM Pipeline    │ │
│  │ STT + VAD │  │ (Kokoro /  │  │  (OpenAI/Gemini/  │ │
│  │           │  │ ElevenLabs)│  │   Groq + Tools)   │ │
│  └───────────┘  └────────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**Three-layer design** (see [`docs/ARCHITECTURE_V2.md`](docs/ARCHITECTURE_V2.md)):

| Layer | Role | Output |
|-------|------|--------|
| **LLM** | Decides *what* to show | ~80 tokens of SDL |
| **Scene Compiler** | Decides *where* to place it | Layout coordinates |
| **Renderer** | Decides *how* to draw it | Rough.js + GSAP pixels |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Voice Transport | WebRTC (aiortc) |
| Speech-to-Text | Deepgram Nova |
| Turn Detection | Smart Turn (pipecat-ai) + Silero VAD v6 |
| LLM | OpenAI GPT-4o-mini / Gemini Flash / Groq |
| Text-to-Speech | Kokoro (local) / ElevenLabs |
| Backend | FastAPI, SQLModel, SQLite |
| Frontend | Next.js 14, React 18, TypeScript |
| Canvas | Rough.js, GSAP, KaTeX, perfect-freehand |
| Styling | Tailwind CSS |

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- API keys for Deepgram, OpenAI (or Gemini/Groq), and optionally ElevenLabs

### 1. Clone and install

```bash
git clone https://github.com/swayamg20/voiceai.git
cd voiceai

# Backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Frontend
cd web
npm install
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Required keys:**
| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (or `GEMINI_API_KEY` for Gemini) |
| `DEEPGRAM_KEY` | Deepgram speech-to-text API key |

**Optional keys:**
| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `groq` | `openai`, `gemini`, or `groq` |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model identifier |
| `ELEVENLABS_API_KEY` | — | ElevenLabs TTS (falls back to Kokoro local) |
| `TTS_MAX_RETRIES` | `2` | Retries for transient ElevenLabs failures before fallback |
| `TTS_RETRY_BASE_DELAY_SECS` | `0.35` | Base backoff delay for ElevenLabs retries |
| `TTS_FALLBACK_TO_KOKORO` | `true` | Enable sentence-level Kokoro fallback when cloud TTS fails |
| `SMART_TURN_ENABLED` | `true` | ML-based turn detection |
| `SMART_TURN_THRESHOLD` | `0.5` | Turn completion confidence (0.0–1.0) |
| `DEEPGRAM_ENDPOINTING` | `700` | Silence timeout in ms |
| `LLM_TEMPERATURE` | `0.7` | LLM sampling temperature |
| `LLM_MAX_CONTEXT_MESSAGES` | `20` | Conversation history window |
| `ALLOWED_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated frontend origins allowed to call the backend |

See [`.env.example`](.env.example) for the full list.

Protected API routes expect a Firebase ID token in `Authorization: Bearer <token>`.
Local `.env` values override the repo defaults shown above.
The Next.js frontend reads its own env file from `web/.env.local`, using `NEXT_PUBLIC_*` variables for API and Firebase config. Start from `web/.env.example`.

### 3. Run

```bash
# Terminal 1 — Backend
uvicorn main:app --reload --port 8000

# Terminal 2 — Frontend
cd web
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and start talking.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/offer` | WebRTC SDP offer/answer exchange |
| `POST` | `/chat` | Text chat with SSE streaming response |
| `DELETE` | `/chat/{session_id}` | Clear a chat session |
| `POST` | `/chat/{session_id}/canvas-mode` | Toggle canvas mode for a session |
| `GET` | `/api/logs` | Retrieve LLM call logs |
| `GET` | `/api/logs/stats` | Aggregated latency and token stats |
| `GET` | `/api/voice-logs` | Voice session logs |
| `GET` | `/api/voice-logs/stats` | Voice session statistics |

## Design Language

The visual identity is called **Murmur** — a blackboard-native aesthetic where everything feels *drawn, not placed*.

| Token | Hex | Role |
|-------|-----|------|
| `--void` | `#08080C` | Page background |
| `--slate` | `#111116` | Elevated surfaces |
| `--chalk` | `#E8E4DC` | Primary text |
| `--amber` | `#F5A623` | Active / freshly drawn |
| `--lavender` | `#8B7EC8` | AI presence |
| `--sage` | `#6BCB77` | Listening state |
| `--ember` | `#EF4444` | Errors |

Full design system: [`docs/DESIGN_LANGUAGE.md`](docs/DESIGN_LANGUAGE.md)

## Project Structure

```
voiceai/
├── main.py                  # FastAPI app — routes, WebRTC, session management
├── funcs/
│   ├── llm_pipeline.py      # Multi-provider LLM with tool calling
│   ├── tts_pipeline.py      # TTS abstraction (Kokoro / ElevenLabs)
│   ├── animation_pipeline.py# SDL → animation event conversion
│   ├── smart_turn.py        # ML turn detection (pipecat-ai)
│   ├── config.py            # Pydantic settings from .env
│   ├── models.py            # SQLModel DB schemas
│   ├── tools/               # LLM-callable tools
│   └── ...
├── web/
│   ├── src/app/             # Next.js App Router pages
│   ├── src/components/      # React components (SVGCanvas, chat UI)
│   ├── src/lib/scene-kit/   # Scene compiler + layout engine
│   └── src/hooks/           # useWebRTC, useChat, etc.
├── models/                  # ONNX model files (VAD, Smart Turn)
├── docs/                    # Architecture, vision, design docs
└── requirements.txt
```

## Documentation

- [`docs/VISION.md`](docs/VISION.md) — Product vision and philosophy
- [`docs/ARCHITECTURE_V2.md`](docs/ARCHITECTURE_V2.md) — Technical architecture (v2)
- [`docs/DESIGN_LANGUAGE.md`](docs/DESIGN_LANGUAGE.md) — Murmur design system

## License

MIT

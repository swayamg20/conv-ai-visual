# Voice AI - Real-time Voice Assistant

WebRTC-based real-time voice pipeline with STT, LLM, and VAD.

## Pipeline Architecture

```
Audio Input -> WebRTC -> Deepgram STT -> Silero VAD -> LLM (OpenAI) -> TTS (ElevenLabs) -> Audio Response
```

## Features

- **Real-time Speech-to-Text**: Deepgram streaming API with interim results
- **Voice Activity Detection**: Silero VAD v6 for robust speech detection
- **LLM Integration**: Modular OpenAI pipeline with conversation context management
- **Text-to-Speech**: ElevenLabs TTS with natural voice synthesis
- **WebRTC**: Low-latency audio streaming over WebRTC with datachannel messaging

## Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Set environment variables:**
Create a `.env` file or export:
```bash
export OPENAI_API_KEY=your_openai_api_key_here
export ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
```

3. **Run the server:**
```bash
uvicorn main:app --reload
# Or with custom host/port
uvicorn main:app --host localhost --port 3000 --reload
```

## Environment Variables

### Required
- `OPENAI_API_KEY` - OpenAI API key for LLM
- `ELEVENLABS_API_KEY` - ElevenLabs API key for TTS

### Optional
- `OPENAI_MODEL` - Default: "gpt-4o-mini"
- `DEEPGRAM_KEY` - Deepgram API key (hardcoded default available)
- `DEEPGRAM_MODEL` - Default: "nova"
- `LLM_TEMPERATURE` - Default: 0.7
- `LLM_MAX_TOKENS` - Optional
- `LLM_MAX_CONTEXT_MESSAGES` - Default: 20
- `LLM_SYSTEM_PROMPT` - Custom system prompt
- `ELEVENLABS_VOICE_ID` - Default: "21m00Tcm4TlvDq8ikWAM" (Rachel)
- `ELEVENLABS_MODEL_ID` - Default: "eleven_turbo_v2_5"
- `TTS_STABILITY` - Default: 0.5
- `TTS_SIMILARITY_BOOST` - Default: 0.75
- `TTS_STYLE` - Default: 0.0
- `TTS_USE_SPEAKER_BOOST` - Default: true
- `HOST` - Default: "0.0.0.0"
- `PORT` - Default: 8000

## Architecture

See `funcs/README.md` for detailed component documentation.

## References

- [TEN Turn Detection](https://github.com/TEN-framework/ten-turn-detection)
- [TEN VAD](https://github.com/TEN-framework/ten-vad)
- Silero VAD v6 - robust noise vs speech detection
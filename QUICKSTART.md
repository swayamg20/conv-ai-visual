# Quick Start Guide

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. Set API Keys

```bash
export OPENAI_API_KEY=your_openai_key_here
export ELEVENLABS_API_KEY=your_elevenlabs_key_here
```

Or create a `.env` file:
```
OPENAI_API_KEY=your_openai_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
```

## 3. Run Server

```bash
uvicorn main:app --reload
```

Server will start at `http://0.0.0.0:8000`

## 4. Test Pipelines (Optional)

```bash
# Test LLM
python test_llm.py

# Test TTS
python test_tts.py
```

## 5. Connect Client

Open `test.html` in a browser or connect your WebRTC client to:
- Endpoint: `POST /offer`
- WebRTC datachannel for messages

## Message Types

Your client will receive:

```javascript
// Interim/final transcripts
{"type": "transcript", "text": "...", "is_final": true}

// LLM text responses
{"type": "llm_response", "text": "..."}

// TTS audio (base64 encoded PCM)
{"type": "tts_audio", "audio": "base64...", "format": "pcm_16000", "sample_rate": 16000}

// Errors
{"type": "error", "message": "..."}
```

## Configuration (Optional)

Customize via environment variables:

```bash
export OPENAI_MODEL=gpt-4o
export LLM_TEMPERATURE=0.8
export LLM_MAX_TOKENS=200
export LLM_SYSTEM_PROMPT="You are a helpful assistant"
```

See `README.md` for full configuration options.

## Troubleshooting

**LLM not working:**
- Check `OPENAI_API_KEY` is set
- Check server logs for initialization errors

**TTS not working:**
- Check `ELEVENLABS_API_KEY` is set
- Test standalone: `python test_tts.py`
- Check server logs for TTS errors

**No transcripts:**
- Verify Deepgram connection in logs
- Check WebRTC datachannel is open

**No audio playback:**
- Check browser console for decoding errors
- Verify sample rate (16000 Hz)
- See `TTS_INTEGRATION.md` for client code

**Import errors:**
- Ensure all dependencies installed: `pip install -r requirements.txt`


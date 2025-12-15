# Voice AI Functions

This directory contains modular components for the voice AI pipeline.

## Components

### `llm_pipeline.py`
Handles LLM conversation pipeline with context management.

**Features:**
- Async OpenAI API integration
- Conversation context management per session
- Automatic context trimming to stay within token limits
- Configurable system prompts
- Temperature and max_tokens control

**Usage:**
```python
from funcs.llm_pipeline import LLMPipeline

# Initialize
llm = LLMPipeline(
    api_key="your-key",
    model="gpt-4o-mini",
    system_prompt="You are a helpful assistant",
    max_context_messages=20
)

# Create conversation context
context = llm.create_conversation_context()

# Process user input and get response
context, response = await llm.process_user_input(
    context,
    "Hello, how are you?",
    temperature=0.7
)
```

### `tts_pipeline.py`
Handles Text-to-Speech pipeline with ElevenLabs.

**Features:**
- Async ElevenLabs API integration
- Streaming and non-streaming synthesis
- Configurable voice settings
- PCM 16kHz output for WebRTC

**Usage:**
```python
from funcs.tts_pipeline import TTSPipeline

# Initialize
tts = TTSPipeline(
    api_key="your-key",
    voice_id="21m00Tcm4TlvDq8ikWAM",
    model_id="eleven_turbo_v2_5"
)

# Generate speech
audio_bytes = await tts.text_to_speech("Hello, world!")

# Or stream
async for chunk in tts.text_to_speech_stream("Hello!"):
    process_audio_chunk(chunk)
```

### `vad_gate.py`
Voice Activity Detection using Silero VAD model.

### `config.py`
Centralized configuration management using environment variables.

**Environment Variables:**
- `OPENAI_API_KEY` - Required for LLM functionality
- `ELEVENLABS_API_KEY` - Required for TTS functionality
- `OPENAI_MODEL` - Default: "gpt-4o-mini"
- `DEEPGRAM_KEY` - For speech-to-text
- `DEEPGRAM_MODEL` - Default: "nova"
- `LLM_TEMPERATURE` - Default: 0.7
- `LLM_MAX_TOKENS` - Optional, no limit by default
- `LLM_MAX_CONTEXT_MESSAGES` - Default: 20
- `LLM_SYSTEM_PROMPT` - Custom system prompt
- `ELEVENLABS_VOICE_ID` - Default: "21m00Tcm4TlvDq8ikWAM"
- `ELEVENLABS_MODEL_ID` - Default: "eleven_turbo_v2_5"
- `TTS_STABILITY` - Default: 0.5
- `TTS_SIMILARITY_BOOST` - Default: 0.75
- `TTS_STYLE` - Default: 0.0
- `TTS_USE_SPEAKER_BOOST` - Default: true
- `HOST` - Server host, default: "0.0.0.0"
- `PORT` - Server port, default: 8000

## Setup

1. Create a `.env` file in the project root:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the server:
```bash
uvicorn main:app --reload
```


# LLM Pipeline Integration

## Overview

A modular LLM pipeline has been added after the transcription step to process user speech and generate intelligent responses.

## Architecture

```
WebRTC Audio Input
    ↓
Deepgram STT (Streaming)
    ↓
Silero VAD (Voice Activity Detection)
    ↓
[NEW] LLM Pipeline (OpenAI)
    ↓
Response via DataChannel
```

## Components

### 1. `funcs/llm_pipeline.py`
Standalone, reusable LLM pipeline module.

**Features:**
- Async OpenAI API integration
- Per-session conversation context management
- Automatic context trimming (configurable)
- Configurable temperature, max_tokens
- System prompt customization

**Key Methods:**
- `create_conversation_context()` - Initialize new session
- `process_user_input(context, message)` - Get LLM response
- `add_user_message()` / `add_assistant_message()` - Manual context management

### 2. `funcs/config.py`
Centralized configuration using environment variables.

**Key Settings:**
- `OPENAI_API_KEY` - Required
- `OPENAI_MODEL` - Default: "gpt-4o-mini"
- `LLM_TEMPERATURE` - Default: 0.7
- `LLM_MAX_TOKENS` - Optional
- `LLM_MAX_CONTEXT_MESSAGES` - Default: 20
- `LLM_SYSTEM_PROMPT` - Customizable

### 3. Main Pipeline (`main.py`)

**Flow:**
1. Deepgram returns transcription results
2. Check if `is_final=True` and transcript not empty
3. Get or create conversation context for this peer connection
4. Send transcript to LLM pipeline
5. Get assistant response
6. Send response back to client via DataChannel

**Data Channel Messages:**

Client receives these message types:

```json
// Transcription (interim or final)
{
  "type": "transcript",
  "text": "Hello, how are you?",
  "is_final": true
}

// LLM Response (only for final transcripts)
{
  "type": "llm_response",
  "text": "I'm doing great! How can I help you today?"
}

// Error
{
  "type": "error",
  "message": "Failed to process with LLM"
}
```

## Usage

### Basic Setup

1. **Set API key:**
```bash
export OPENAI_API_KEY=your_key_here
```

2. **Run server:**
```bash
uvicorn main:app --reload
```

### Advanced Configuration

```bash
export OPENAI_API_KEY=your_key
export OPENAI_MODEL=gpt-4o
export LLM_TEMPERATURE=0.8
export LLM_MAX_TOKENS=150
export LLM_MAX_CONTEXT_MESSAGES=30
export LLM_SYSTEM_PROMPT="You are a witty assistant with a sense of humor."
```

### Testing Standalone

Test the LLM pipeline without WebRTC:
```bash
python test_llm.py
```

## Context Management

Each peer connection (`pc_id`) maintains its own conversation context stored in `conversation_contexts` dict.

**Lifecycle:**
- Created on first final transcript
- Automatically trimmed to `max_context_messages`
- Cleaned up when datachannel closes or connection fails

**Context Structure:**
```python
[
  {"role": "system", "content": "You are a helpful assistant..."},
  {"role": "user", "content": "Hello"},
  {"role": "assistant", "content": "Hi there!"},
  {"role": "user", "content": "What's the weather?"},
  ...
]
```

## Error Handling

- If `OPENAI_API_KEY` not set, LLM pipeline initialization fails gracefully
- Server continues to work for STT without LLM
- LLM errors logged but don't crash the connection
- Error messages sent to client via datachannel

## Performance Considerations

- LLM calls are async and don't block audio processing
- Only processes **final** transcripts (not interim results)
- Context trimming prevents token limit issues
- Multiple simultaneous connections each have isolated contexts

## Extensibility

The modular design allows easy customization:

1. **Swap LLM providers:** Modify `LLMPipeline` to use Anthropic, etc.
2. **Add RAG:** Inject context into system prompt or messages
3. **Function calling:** Extend `process_user_input` to handle tool use
4. **Streaming responses:** Modify to use streaming API and send chunks
5. **Multi-modal:** Add image/audio processing capabilities

## Example Client Integration

```javascript
// Listen for messages
dataChannel.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === "transcript") {
    console.log(`Transcript (${data.is_final ? 'final' : 'interim'}): ${data.text}`);
  } else if (data.type === "llm_response") {
    console.log(`Assistant: ${data.text}`);
    // Display or speak the response
  } else if (data.type === "error") {
    console.error(`Error: ${data.message}`);
  }
};
```

## Testing Checklist

- [ ] Set `OPENAI_API_KEY` environment variable
- [ ] Run `python test_llm.py` to verify standalone
- [ ] Start server with `uvicorn main:app --reload`
- [ ] Connect via WebRTC client
- [ ] Speak and verify transcript appears
- [ ] Verify LLM response comes back
- [ ] Test multi-turn conversation context
- [ ] Test multiple simultaneous connections

## Dependencies Added

```
openai>=1.54.0
```

See `requirements.txt` for full dependencies.


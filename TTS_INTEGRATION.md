# TTS Pipeline Integration (ElevenLabs)

## Overview

A modular TTS pipeline using ElevenLabs has been added to convert LLM responses to speech.

## Architecture

```
WebRTC Audio Input
    ↓
Deepgram STT (Streaming)
    ↓
Silero VAD (Voice Activity Detection)
    ↓
LLM Pipeline (OpenAI)
    ↓
[NEW] TTS Pipeline (ElevenLabs)
    ↓
Audio Response via DataChannel
```

## Components

### `funcs/tts_pipeline.py`
Standalone, reusable TTS pipeline module.

**Features:**
- Async ElevenLabs API integration
- Streaming and non-streaming synthesis
- Configurable voice settings (stability, similarity, style)
- Voice management (list, get info)
- PCM 16kHz output for WebRTC compatibility

**Key Methods:**
- `text_to_speech(text)` - Get complete audio
- `text_to_speech_stream(text)` - Stream audio chunks
- `get_available_voices()` - List all voices
- `get_voice_info(voice_id)` - Get voice details

### Voice Settings

**Stability** (0.0 - 1.0, default: 0.5)
- Higher = more consistent, less expressive
- Lower = more variable, more expressive

**Similarity Boost** (0.0 - 1.0, default: 0.75)
- Higher = clearer, more similar to original voice
- Lower = more diverse, may sound different

**Style** (0.0 - 1.0, default: 0.0)
- Exaggeration of speaker style
- 0 = neutral, higher = more dramatic

**Speaker Boost** (bool, default: true)
- Enhances voice clarity and quality
- Recommended for most use cases

## Configuration

### Environment Variables

```bash
# Required
export ELEVENLABS_API_KEY=your_elevenlabs_api_key

# Optional (with defaults)
export ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM  # Rachel voice
export ELEVENLABS_MODEL_ID=eleven_turbo_v2_5      # Low latency model
export TTS_STABILITY=0.5
export TTS_SIMILARITY_BOOST=0.75
export TTS_STYLE=0.0
export TTS_USE_SPEAKER_BOOST=true
```

### Popular Voice IDs

- `21m00Tcm4TlvDq8ikWAM` - Rachel (default, female, American)
- `pNInz6obpgDQGcFmaJgB` - Adam (male, American)
- `EXAVITQu4vr4xnSDxMaL` - Sarah (female, American)
- `onwK4e9ZLuTAKqWW03F9` - Daniel (male, British)

Get full list: `python test_tts.py` or check [ElevenLabs Voice Lab](https://elevenlabs.io/app/voice-lab)

## Pipeline Flow

1. User speaks → Deepgram transcription (final)
2. Transcript sent to LLM → Get text response
3. LLM response sent to client as JSON
4. LLM response sent to TTS pipeline
5. TTS generates audio (PCM 16kHz)
6. Audio encoded as base64 and sent via datachannel

## DataChannel Messages

### From Server to Client

```javascript
// Transcript (real-time)
{
  "type": "transcript",
  "text": "What's the weather?",
  "is_final": true
}

// LLM text response
{
  "type": "llm_response",
  "text": "I can help you check the weather..."
}

// TTS audio (NEW)
{
  "type": "tts_audio",
  "audio": "base64_encoded_pcm_data...",
  "format": "pcm_16000",
  "sample_rate": 16000
}

// Errors
{
  "type": "error",
  "message": "Failed to generate TTS"
}
```

## Client-Side Integration

### Decoding and Playing Audio

```javascript
dataChannel.onmessage = async (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === "tts_audio") {
    // Decode base64
    const binaryString = atob(data.audio);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    // Convert PCM to AudioBuffer
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const audioBuffer = audioContext.createBuffer(
      1,  // mono
      bytes.length / 2,  // 16-bit = 2 bytes per sample
      data.sample_rate
    );
    
    // Convert bytes to float32
    const channelData = audioBuffer.getChannelData(0);
    const dataView = new DataView(bytes.buffer);
    for (let i = 0; i < channelData.length; i++) {
      const int16 = dataView.getInt16(i * 2, true);
      channelData[i] = int16 / 32768.0;  // Convert to float [-1, 1]
    }
    
    // Play
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);
    source.start();
    
    console.log(`Playing TTS audio: ${bytes.length} bytes`);
  }
};
```

### Alternative: Using Web Audio API with MediaStream

For streaming playback, you can use AudioWorklet or ScriptProcessor to play chunks as they arrive.

## Testing

### Standalone Test

```bash
export ELEVENLABS_API_KEY=your_key
python test_tts.py
```

This will:
- Test basic synthesis
- Test streaming synthesis
- Save audio files (`.pcm` format)
- Show voice information

### Play Generated Audio

```bash
# Install ffplay (part of ffmpeg)
brew install ffmpeg  # macOS
apt-get install ffmpeg  # Linux

# Play PCM file
ffplay -f s16le -ar 16000 -ac 1 test_tts_output_1.pcm
```

### Full Pipeline Test

1. Set both API keys:
```bash
export OPENAI_API_KEY=your_openai_key
export ELEVENLABS_API_KEY=your_elevenlabs_key
```

2. Start server:
```bash
uvicorn main:app --reload
```

3. Connect WebRTC client and speak

4. You should receive:
   - Transcript
   - LLM text response
   - TTS audio

## Performance Considerations

### Latency

- **Model**: `eleven_turbo_v2_5` optimized for low latency (~200-500ms)
- **Streaming**: Use `text_to_speech_stream()` for lower perceived latency
- **Network**: Base64 encoding adds ~33% overhead, consider binary datachannel

### Audio Format

- **PCM 16000Hz** chosen for WebRTC compatibility
- Mono channel reduces bandwidth
- 16-bit depth balances quality and size

### Optimization Tips

1. **Use streaming TTS** for long responses
2. **Cache common phrases** if repeating similar responses
3. **Adjust voice settings** for faster synthesis (higher stability)
4. **Consider binary datachannel** instead of base64 for 33% size reduction

## Error Handling

- TTS failures don't crash the connection
- Client still receives text response even if TTS fails
- Errors logged server-side
- Graceful degradation: system works without TTS if key not provided

## Cost Considerations

ElevenLabs pricing (as of Dec 2024):
- **Free tier**: 10,000 characters/month
- **Starter**: $5/month for 30,000 characters
- **Creator**: $22/month for 100,000 characters
- **Pro**: $99/month for 500,000 characters

Monitor usage in [ElevenLabs dashboard](https://elevenlabs.io/app/usage).

## Advanced Usage

### Custom Voice

1. Clone a voice in ElevenLabs Voice Lab
2. Copy the voice ID
3. Set `ELEVENLABS_VOICE_ID` environment variable

### Multiple Voices

```python
# In your code, override voice per request
audio = await tts_pipeline.text_to_speech(
    text="Hello!",
    voice_id="different_voice_id"
)
```

### Streaming for Real-time Playback

```python
async for chunk in tts_pipeline.text_to_speech_stream(text):
    # Send chunk immediately to client
    await send_audio_chunk(chunk)
```

## Troubleshooting

**No audio received:**
- Check `ELEVENLABS_API_KEY` is set
- Check server logs for TTS errors
- Verify datachannel is open

**Audio sounds distorted:**
- Check sample rate matches (16000 Hz)
- Verify PCM decoding is correct (16-bit signed little-endian)
- Check browser audio context compatibility

**High latency:**
- Use `eleven_turbo_v2_5` model
- Consider streaming synthesis
- Check network bandwidth

**API quota exceeded:**
- Monitor usage in ElevenLabs dashboard
- Upgrade plan if needed
- Implement caching for common responses

## Dependencies

```
elevenlabs>=1.33.0
```

See `requirements.txt` for full list.


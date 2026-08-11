# Client-Side Integration Guide for Interruption Handling

## Overview

The server now supports **automatic interruption detection** when users speak while the AI is responding. This document explains what changes (if any) are needed on the client side.

## Do You Need to Change Anything?

**Short Answer:** **Minimal changes required** - just handle the new message types.

The interruption system works **server-side** using Voice Activity Detection (VAD), so it detects interruptions automatically without requiring client-side changes.

## What the Server Now Does

1. **Detects interruption automatically** when:
   - User speaks (detected by server-side Silero VAD)
   - While TTS audio is being generated

2. **Stops TTS generation** immediately

3. **Sends new message types** to notify client

## New Message Types from Server

### 1. `interruption_ack`

Sent when server detects user interruption:

```json
{
  "type": "interruption_ack",
  "message": "Stopping response, listening to you"
}
```

**Recommended client action:**
- Stop playing any buffered TTS audio immediately
- Show visual feedback (e.g., "Listening..." indicator)
- Clear audio playback queue

### 2. `tts_cancelled`

Sent when TTS generation is stopped due to interruption:

```json
{
  "type": "tts_cancelled",
  "chunks_sent": 12,
  "message": "TTS interrupted by user speech"
}
```

**Recommended client action:**
- Stop expecting more `tts_audio_chunk` messages
- Don't wait for `tts_audio_end` message (it won't come)
- Discard any remaining audio chunks in buffer

## Existing Message Types (Still Work)

These continue to work as before:

- `transcript` - Transcription results from Deepgram
- `llm_response` - Text response from LLM
- `tts_audio_chunk` - TTS audio data (base64 encoded PCM)
- `tts_audio_end` - Signals TTS completed successfully (only sent if NOT interrupted)

## Minimal Client Code Changes

### JavaScript/TypeScript Example

```javascript
// In your datachannel message handler
dataChannel.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);

  switch (data.type) {
    case 'interruption_ack':
      // Server detected interruption
      console.log('[Interruption]', data.message);
      stopTTSPlayback(); // Stop playing audio immediately
      showListeningIndicator(); // Visual feedback
      break;

    case 'tts_cancelled':
      // TTS was interrupted and stopped
      console.log('[TTS Cancelled]', `${data.chunks_sent} chunks sent`);
      clearAudioBuffer(); // Clear any buffered chunks
      expectingMoreTTS = false; // Don't wait for tts_audio_end
      break;

    case 'tts_audio_chunk':
      // Normal TTS chunk (existing code)
      playAudioChunk(data.audio, data.chunk_index);
      break;

    case 'tts_audio_end':
      // TTS completed successfully (existing code)
      console.log('[TTS Complete]', `${data.total_chunks} chunks`);
      expectingMoreTTS = false;
      break;

    // ... other message types
  }
});

function stopTTSPlayback() {
  // Stop audio playback immediately
  if (audioContext) {
    audioContext.suspend();
  }

  // Clear audio queue
  audioQueue = [];

  // Stop any active audio nodes
  if (currentAudioSource) {
    currentAudioSource.stop();
    currentAudioSource = null;
  }
}

function clearAudioBuffer() {
  // Discard remaining chunks
  audioQueue = [];
}
```

### React Example

```jsx
function VoiceChat() {
  const [isInterrupted, setIsInterrupted] = useState(false);

  useEffect(() => {
    dataChannel.addEventListener('message', handleMessage);

    function handleMessage(event) {
      const data = JSON.parse(event.data);

      if (data.type === 'interruption_ack') {
        setIsInterrupted(true);
        stopAllAudio();
        // Show "Listening..." UI
      }

      if (data.type === 'tts_cancelled') {
        clearAudioQueue();
      }

      if (data.type === 'tts_audio_chunk') {
        // Only play if not interrupted
        if (!isInterrupted) {
          queueAudioChunk(data.audio);
        }
      }
    }
  }, []);

  return (
    <div>
      {isInterrupted && <div className="listening-indicator">Listening...</div>}
      {/* rest of UI */}
    </div>
  );
}
```

## What If You Don't Handle These Messages?

**The system still works!** But the user experience is suboptimal:

- ❌ Client keeps playing buffered TTS chunks even after interruption
- ❌ No visual feedback that interruption was detected
- ✅ Server still stops generating new TTS chunks
- ✅ Server still processes new user input

## Optional: Client-Side Interruption Signal (Phase 2)

If you want **instant local feedback** (0ms latency), you can detect user speech on the client and send an interrupt signal:

```javascript
// Optional: Detect when microphone captures audio while TTS playing
let isTTSPlaying = false;

function setupClientSideInterruption(audioStream) {
  const audioContext = new AudioContext();
  const analyser = audioContext.createAnalyser();
  const source = audioContext.createMediaStreamSource(audioStream);
  source.connect(analyser);

  const dataArray = new Uint8Array(analyser.frequencyBinCount);

  function checkForSpeech() {
    analyser.getByteFrequencyData(dataArray);
    const average = dataArray.reduce((a, b) => a + b) / dataArray.length;

    // If mic detects sound while TTS playing
    if (isTTSPlaying && average > 30) {
      // Immediately stop TTS playback (instant feedback)
      stopTTSPlayback();

      // Send interrupt signal to server
      dataChannel.send(JSON.stringify({
        type: 'interrupt',
        timestamp: Date.now()
      }));

      isTTSPlaying = false;
    }

    requestAnimationFrame(checkForSpeech);
  }

  checkForSpeech();
}

// Track TTS state
dataChannel.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);

  if (data.type === 'tts_audio_chunk') {
    isTTSPlaying = true;
  }

  if (data.type === 'tts_audio_end' || data.type === 'tts_cancelled') {
    isTTSPlaying = false;
  }
});
```

**Note:** This is optional! The server-side interruption already works. This just provides instant local feedback.

## Testing Interruption

1. **Start voice conversation** with server
2. **Ask a question** that generates a long response
3. **While AI is speaking**, start talking yourself
4. **Expected behavior:**
   - Server logs: `[pc-xxx] INTERRUPTION detected`
   - Client receives: `interruption_ack` message
   - TTS stops generating
   - Client receives: `tts_cancelled` message
   - Audio playback stops

## Server-Side vs Client-Side Detection

| Aspect | Server-Side (Current) | Client-Side (Optional) |
|--------|----------------------|------------------------|
| Detection latency | ~50-100ms | ~0-20ms |
| Reliability | High (Silero VAD) | Medium (ambient noise) |
| Changes required | None | Add local VAD |
| Audio continues? | Yes, to server | Yes, to server |
| Playback stops | Via server signal | Instantly |

**Audio always flows to server** - interruption only affects TTS playback, not audio capture.

## Summary

### Required Changes:
✅ Handle `interruption_ack` message - stop TTS playback
✅ Handle `tts_cancelled` message - clear audio buffer

### Optional Enhancements:
⭐ Client-side speech detection for instant feedback
⭐ Visual indicators for listening/speaking states
⭐ Smooth audio crossfading

### No Changes Needed:
✅ Audio streaming (continues as before)
✅ WebRTC connection setup
✅ Existing message handling

The server handles interruption detection automatically using VAD. Client changes are minimal and mostly for improved UX.

# Immediate TTS Interruption Flow

## Overview
The interruption system now provides **near-instant** TTS cancellation when the user speaks during AI playback. Both client and server detect and respond immediately to interruptions.

## How It Works

### Client Side (Frontend)

**Detection:**
```typescript
// On ANY transcript (interim or final) while TTS is playing:
if (data.text.trim() && pipelineState === "speaking") {
  // 1. Stop audio playback immediately
  stopAudio();

  // 2. Update state
  updatePipelineState("listening");

  // 3. Tell server to stop generating
  send({ type: "stop_tts" });
}
```

**Key Points:**
- Triggers on **ANY** transcript (not just final)
- Stops audio playback **immediately** (no waiting)
- Sends stop signal to server to halt generation
- Still processes final transcripts after interruption

### Server Side (Backend)

**Detection:**
```python
# On ANY transcript while TTS is active:
if transcript.strip() and tts_interrupt_flags.get(pc_id, False):
    # Set flag to False → signals TTS loop to stop
    tts_interrupt_flags[pc_id] = False
```

**TTS Streaming with Interruption:**
```python
tts_interrupt_flags[pc_id] = True  # Mark TTS as active

for audio_chunk in generate_tts():
    # Check BEFORE sending
    if not tts_interrupt_flags.get(pc_id, False):
        send_interrupted_message()
        break

    send_chunk(audio_chunk)

    # Check AFTER sending (double-check for faster response)
    if not tts_interrupt_flags.get(pc_id, False):
        send_interrupted_message()
        break

tts_interrupt_flags[pc_id] = False  # Clear flag
```

**Key Points:**
- Checks interrupt flag **before** each chunk
- Checks interrupt flag **after** each chunk (double-check)
- Breaks immediately when flag is False
- Sends `tts_interrupted` confirmation to client

## Flow Diagram

```
User speaks while TTS is playing
         ↓
    ┌─────────────────────────────┐
    │  STT detects speech         │
    │  (generates transcript)     │
    └─────────────────────────────┘
         ↓
    ┌──────────────────────────────────────────┐
    │  BACKEND: on_deepgram_event              │
    │  - Detects TTS active (flag = True)      │
    │  - Sets flag = False (signal stop)       │
    │  - Sends transcript to client            │
    └──────────────────────────────────────────┘
         ↓
    ┌──────────────────────────────────────────┐
    │  CLIENT: receives transcript             │
    │  - Detects state = "speaking"            │
    │  - Calls stopAudio() immediately         │
    │  - Sets state = "listening"              │
    │  - Sends "stop_tts" to server            │
    └──────────────────────────────────────────┘
         ↓
    ┌──────────────────────────────────────────┐
    │  BACKEND: TTS streaming loop             │
    │  - Checks flag before next chunk         │
    │  - Sees flag = False                     │
    │  - Breaks loop immediately               │
    │  - Sends "tts_interrupted" to client     │
    └──────────────────────────────────────────┘
         ↓
    ┌──────────────────────────────────────────┐
    │  CLIENT: receives "tts_interrupted"      │
    │  - Confirms interruption                 │
    │  - Already stopped audio                 │
    └──────────────────────────────────────────┘
         ↓
    Final transcript arrives
         ↓
    ┌──────────────────────────────────────────┐
    │  Process through LLM                     │
    │  - State: listening → processing         │
    │  - Generate new response                 │
    │  - Start new TTS session                 │
    └──────────────────────────────────────────┘
```

## Timing

### Interruption Latency Breakdown

1. **User starts speaking**: 0ms
2. **STT detects speech**: ~50-100ms (Deepgram interim result)
3. **Backend receives transcript**: ~10ms
4. **Backend sets interrupt flag**: <1ms
5. **Client receives transcript**: ~10ms
6. **Client stops audio**: <1ms
7. **Backend TTS loop checks flag**: <10ms (on next chunk iteration)

**Total interruption latency: ~70-130ms** ⚡

This is **much faster** than waiting for:
- Final transcript (~500-1000ms)
- LLM processing (~500-2000ms)
- New TTS generation (~200-500ms)

## Events

### Server → Client

**During Interruption:**
- `transcript` (interim/final) - Triggers client-side stop
- `tts_interrupted` - Confirms server stopped generation
  ```json
  {
    "type": "tts_interrupted",
    "chunks_sent": 5
  }
  ```

**Normal Flow:**
- `tts_started` - TTS beginning
- `tts_chunk` - Audio data
- `tts_complete` - TTS finished without interruption

### Client → Server

- `stop_tts` - Request immediate TTS stop
  ```json
  { "type": "stop_tts" }
  ```

## State Management

### Client States
- `listening` - Ready for input
- `processing` - LLM generating response
- `speaking` - TTS playing

### Server Flag
- `tts_interrupt_flags[pc_id]`
  - `True` = TTS is active and playing
  - `False` = Stop TTS immediately

## Edge Cases Handled

### 1. Rapid Interruptions
**Scenario:** User interrupts, then interrupts again before final transcript
**Handling:** Each interruption resets the state to "listening". Final transcript always processed.

### 2. Interrupt During Chunk Generation
**Scenario:** Flag set to False while TTS pipeline is generating a chunk
**Handling:** Double-check after sending each chunk catches this immediately

### 3. Late Interrupt Message
**Scenario:** Client sends `stop_tts` after TTS already finished
**Handling:** No-op, flag already False from cleanup

### 4. Network Delay
**Scenario:** Transcript arrives late due to network lag
**Handling:** Client-side audio still stops immediately on receipt. Server flag prevents wasted chunk generation.

### 5. Final Transcript During Interruption
**Scenario:** Final transcript arrives while handling interruption
**Handling:** Processed normally after stopping, triggers new LLM response

## Testing Interruption

### How to Test
1. Start a conversation
2. Let AI start responding (state = 🔊 speaking)
3. **Speak immediately** while AI is talking
4. Expected behavior:
   - Audio stops **instantly** (<100ms perceived delay)
   - State changes: 🔊 → 🎤
   - Console shows interrupt logs
   - Your speech is processed normally

### Console Logs to Check

**Client:**
```
[Interrupt] 🛑 User spoke during TTS: "hello" (final=false)
[Interrupt] ✓ Sent stop_tts to server
[Audio] 🛑 STOPPING all audio (had 3 scheduled sources, ...)
[Event] tts_interrupted { chunks_sent: 5 }
```

**Server:**
```
[pc-xxx] 🛑 IMMEDIATE INTERRUPT - User speaking during TTS: 'hello' (final=False)
[pc-xxx] 🛑 TTS interrupted after chunk 5 - stopping immediately
```

## Performance Characteristics

### Fast Interruption (Current)
- ✅ Interrupts on interim transcripts (~50-100ms)
- ✅ Client stops audio immediately
- ✅ Server stops generating immediately
- ✅ No wasted TTS chunks

### What We Avoided (Previous Slow Approach)
- ❌ Waiting for final transcript (~500-1000ms)
- ❌ Processing through LLM first (~500-2000ms)
- ❌ Only stopping on new TTS start (~1500-4000ms total!)

## Benefits

1. **Natural Conversation Flow**
   - Feels like talking to a human
   - Immediate feedback to interruption
   - No awkward delays

2. **Resource Efficiency**
   - Stops TTS generation immediately
   - No wasted bandwidth sending chunks
   - Reduces server load

3. **User Experience**
   - Responsive and snappy
   - Clear state transitions
   - Predictable behavior

## Future Optimizations (Optional)

### Could Add:
1. **Voice Activity Detection (VAD)** - Detect speech even before transcript
2. **Predictive Stopping** - Stop on speech probability threshold
3. **Fade Out** - Smoothly fade audio instead of hard stop
4. **Visual Feedback** - Show "interrupting..." indicator

### Probably Don't Need:
- Current implementation is fast enough (<130ms)
- Adding more complexity might introduce bugs
- Keep it simple and reliable

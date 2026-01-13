# Client-Side Changes Summary

## Files Modified

### 1. `/web/src/hooks/use-audio.ts`

**Added:**
- `currentSourceRef` - Tracks the current playing audio source
- `stopAudio()` function - Stops TTS playback and clears audio buffers

**Changes:**
```typescript
// Before
const { initAudio, playPCM } = useAudio();

// After
const { initAudio, playPCM, stopAudio } = useAudio();
```

**Implementation:**
- Tracks audio source node when playing
- Stops source with try/catch for safety
- Suspends audio context briefly to clear buffers
- Resumes context after 50ms

---

### 2. `/web/src/hooks/use-webrtc.ts`

**Added to Interface:**
```typescript
interface UseWebRTCOptions {
  // ... existing options
  onInterruptionDetected?: (message: string) => void;
  onTTSCancelled?: (chunksSent: number) => void;
}
```

**Added State:**
- `isTTSPlayingRef` - Tracks if TTS is currently active

**New Message Handlers:**

#### `interruption_ack`
```typescript
else if (data.type === "interruption_ack") {
  log(`Interruption: ${data.message}`);

  // Stop playing TTS immediately
  stopAudio();

  // Clear buffered chunks
  audioChunksRef.current = [];
  isReceivingAudioRef.current = false;
  isTTSPlayingRef.current = false;

  // Notify callback
  onInterruptionDetected?.(data.message);
}
```

#### `tts_cancelled`
```typescript
else if (data.type === "tts_cancelled") {
  log(`TTS cancelled: ${data.chunks_sent} chunks sent`);

  // Clear any remaining buffered chunks
  audioChunksRef.current = [];
  isReceivingAudioRef.current = false;
  isTTSPlayingRef.current = false;

  // Notify callback
  onTTSCancelled?.(data.chunks_sent);
}
```

**TTS State Tracking:**
- Sets `isTTSPlayingRef.current = true` when first chunk arrives
- Sets `isTTSPlayingRef.current = false` when TTS ends or is interrupted

**Dependencies Updated:**
Added `onInterruptionDetected`, `onTTSCancelled`, and `stopAudio` to useCallback dependencies.

---

### 3. `/web/src/app/page.tsx`

**Added State:**
```typescript
const [isInterrupted, setIsInterrupted] = useState(false);
```

**New Handlers:**
```typescript
const handleInterruption = useCallback((message: string) => {
  setIsInterrupted(true);
  handleLog(`Interruption: ${message}`);
}, [handleLog]);

const handleTTSCancelled = useCallback((chunksSent: number) => {
  handleLog(`TTS cancelled after ${chunksSent} chunks`);
  // Clear interruption flag after a brief moment
  setTimeout(() => setIsInterrupted(false), 1500);
}, [handleLog]);
```

**Updated useWebRTC Call:**
```typescript
const { status, connect, disconnect, initAudio } = useWebRTC({
  canvasMode,
  onTranscript: handleTranscript,
  onLLMResponse: handleLLMResponse,
  onCanvasUpdate: handleCanvasUpdate,
  onError: handleError,
  onLog: handleLog,
  onInterruptionDetected: handleInterruption,  // NEW
  onTTSCancelled: handleTTSCancelled,          // NEW
});
```

**Visual Indicator Added:**
```tsx
{/* Interruption Indicator */}
{isInterrupted && isConnected && (
  <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-4 py-3 text-sm">
    <div className="flex items-center gap-2">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-yellow-500 opacity-75"></span>
        <span className="relative inline-flex h-2 w-2 rounded-full bg-yellow-500"></span>
      </span>
      <span className="font-medium text-yellow-600">Listening to you...</span>
    </div>
  </div>
)}
```

---

## What Happens Now

### User Experience Flow:

1. **User speaks while AI is responding**

2. **Server detects interruption** (~50-100ms latency)
   - Server-side VAD detects speech
   - Interruption logic triggers

3. **Client receives `interruption_ack`**
   - TTS playback stops immediately
   - Audio buffer cleared
   - Visual indicator appears: "Listening to you..."
   - Logged to system log

4. **Server stops generating TTS**
   - Remaining chunks not sent

5. **Client receives `tts_cancelled`**
   - Logs chunks sent count
   - Clears interruption flag after 1.5s
   - Visual indicator fades away

### Console Output Example:

```
[WebRTC] Interruption: Stopping response, listening to you
[WebRTC] TTS cancelled: 8 chunks sent
```

### Visual Feedback:

Before interruption:
```
Status: Connected  🟢
```

During interruption:
```
Status: Connected  🟢

┌─────────────────────────────────────┐
│ ⚠️ Listening to you...              │
└─────────────────────────────────────┘
```

After interruption cleared:
```
Status: Connected  🟢
```

---

## Backward Compatibility

✅ **All changes are backward compatible:**
- Existing messages still work (`transcript`, `llm_response`, `tts_audio_chunk`, etc.)
- New callbacks are optional
- If not provided, system works but without visual feedback
- Old clients ignore new message types

---

## Testing Checklist

- [ ] Client compiles without TypeScript errors
- [ ] Can connect to voice server
- [ ] Audio playback works normally
- [ ] Can interrupt AI while speaking
- [ ] Visual indicator appears on interruption
- [ ] TTS playback stops immediately
- [ ] System logs show interruption messages
- [ ] Interruption flag clears after delay
- [ ] Can have normal conversation after interruption

---

## Build & Run

```bash
# Install dependencies (if needed)
cd web
npm install

# Run development server
npm run dev

# Build for production
npm run build
```

The client is now ready to handle interruptions from the server!

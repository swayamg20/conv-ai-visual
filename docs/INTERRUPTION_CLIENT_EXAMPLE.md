# Client Implementation Example for Interruption Handling

## Update for Your Existing WebRTC Hook

Here's how to update `/web/src/hooks/use-webrtc.ts` to handle the new interruption messages.

## Changes Required

### 1. Add New Callbacks to Options

```typescript
interface UseWebRTCOptions {
  apiUrl?: string;
  canvasMode?: boolean;
  onTranscript?: (event: TranscriptEvent) => void;
  onLLMResponse?: (text: string) => void;
  onCanvasUpdate?: (operations: CanvasOperation[]) => void;
  onError?: (message: string) => void;
  onLog?: (message: string) => void;
  // ADD THESE:
  onInterruptionDetected?: (message: string) => void;  // Called when server detects interruption
  onTTSCancelled?: (chunksSent: number) => void;      // Called when TTS is cancelled
}
```

### 2. Add State for TTS Playback

```typescript
export function useWebRTC(options: UseWebRTCOptions = {}) {
  const {
    apiUrl = "http://localhost:8000",
    canvasMode = false,
    onTranscript,
    onLLMResponse,
    onCanvasUpdate,
    onError,
    onLog,
    onInterruptionDetected,  // NEW
    onTTSCancelled,          // NEW
  } = options;

  const [status, setStatus] = useState<ConnectionStatus>("idle");
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Uint8Array[]>([]);
  const isReceivingAudioRef = useRef(false);
  const isTTSPlayingRef = useRef(false);  // NEW: Track TTS playback state
  const { initAudio, playPCM, stopAudio } = useAudio();  // Assuming stopAudio exists
```

### 3. Update Message Handler (lines 110-147)

```typescript
channel.addEventListener("message", (e) => {
  try {
    const data = JSON.parse(e.data);

    if (data.type === "transcript") {
      onTranscript?.({ text: data.text, isFinal: data.is_final });

    } else if (data.type === "llm_response") {
      onLLMResponse?.(data.text);

    } else if (data.type === "canvas_update") {
      log(`Canvas: ${data.operations.length} ops`);
      onCanvasUpdate?.(data.operations);

    // NEW: Handle interruption acknowledgment
    } else if (data.type === "interruption_ack") {
      log(`Interruption: ${data.message}`);

      // Stop playing TTS immediately
      stopAudio?.();

      // Clear buffered chunks
      audioChunksRef.current = [];
      isReceivingAudioRef.current = false;
      isTTSPlayingRef.current = false;

      // Notify callback
      onInterruptionDetected?.(data.message);

    // NEW: Handle TTS cancellation
    } else if (data.type === "tts_cancelled") {
      log(`TTS cancelled: ${data.chunks_sent} chunks sent`);

      // Clear any remaining buffered chunks
      audioChunksRef.current = [];
      isReceivingAudioRef.current = false;
      isTTSPlayingRef.current = false;

      // Notify callback
      onTTSCancelled?.(data.chunks_sent);

    } else if (data.type === "tts_audio_chunk") {
      if (!isReceivingAudioRef.current) {
        isReceivingAudioRef.current = true;
        isTTSPlayingRef.current = true;  // NEW
        audioChunksRef.current = [];
      }
      const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0));
      audioChunksRef.current.push(bytes);

    } else if (data.type === "tts_audio_end") {
      isReceivingAudioRef.current = false;
      const chunks = audioChunksRef.current;
      const total = chunks.reduce((s, a) => s + a.length, 0);
      const combined = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        combined.set(chunk, offset);
        offset += chunk.length;
      }
      playPCM(combined, 16000);
      audioChunksRef.current = [];
      isTTSPlayingRef.current = false;  // NEW

    } else if (data.type === "error") {
      log(`Error: ${data.message}`);
      onError?.(data.message);
    }
  } catch {
    // Ignore parse errors
  }
});
```

## Update Audio Hook (if needed)

If your `use-audio.ts` doesn't have a `stopAudio` function, add one:

```typescript
// In /web/src/hooks/use-audio.ts

export function useAudio() {
  const audioContextRef = useRef<AudioContext | null>(null);
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);

  const initAudio = useCallback(() => {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext({ sampleRate: 16000 });
    }
  }, []);

  const playPCM = useCallback((data: Uint8Array, sampleRate: number) => {
    if (!audioContextRef.current) return;

    const int16Array = new Int16Array(data.buffer);
    const float32Array = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) {
      float32Array[i] = int16Array[i] / 32768.0;
    }

    const buffer = audioContextRef.current.createBuffer(1, float32Array.length, sampleRate);
    buffer.getChannelData(0).set(float32Array);

    const source = audioContextRef.current.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContextRef.current.destination);
    source.start();

    currentSourceRef.current = source;
  }, []);

  // NEW: Add this function
  const stopAudio = useCallback(() => {
    // Stop current audio playback
    if (currentSourceRef.current) {
      try {
        currentSourceRef.current.stop();
      } catch {
        // Already stopped
      }
      currentSourceRef.current = null;
    }

    // Suspend audio context to clear buffer
    if (audioContextRef.current && audioContextRef.current.state === 'running') {
      audioContextRef.current.suspend();
      // Resume after a moment
      setTimeout(() => {
        audioContextRef.current?.resume();
      }, 100);
    }
  }, []);

  return { initAudio, playPCM, stopAudio };
}
```

## Usage Example in Your Page Component

```typescript
// In /web/src/app/page.tsx or your voice chat component

export default function VoicePage() {
  const [isInterrupted, setIsInterrupted] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");

  const { status, connect, disconnect, initAudio } = useWebRTC({
    apiUrl: "http://localhost:8000",
    canvasMode: false,

    onTranscript: (event) => {
      if (event.isFinal) {
        setTranscript(event.text);
      }
    },

    onLLMResponse: (text) => {
      setResponse(text);
      setIsInterrupted(false); // Clear interruption flag when new response starts
    },

    // NEW: Handle interruption
    onInterruptionDetected: (message) => {
      console.log("[Interruption]", message);
      setIsInterrupted(true);
      // Optionally show visual feedback
    },

    // NEW: Handle TTS cancellation
    onTTSCancelled: (chunksSent) => {
      console.log("[TTS Cancelled]", `${chunksSent} chunks sent before interruption`);
      // Update UI to show listening state
    },

    onError: (msg) => {
      console.error("[Error]", msg);
    },

    onLog: (msg) => {
      console.log("[Log]", msg);
    },
  });

  return (
    <div>
      {/* Status indicator */}
      <div className="status-bar">
        <span>Status: {status}</span>
        {isInterrupted && <span className="text-yellow-500">⚠️ Listening...</span>}
      </div>

      {/* Transcript display */}
      <div className="transcript">
        <p><strong>You:</strong> {transcript}</p>
        <p><strong>AI:</strong> {response}</p>
      </div>

      {/* Controls */}
      <button onClick={connect} disabled={status !== "idle"}>
        Connect
      </button>
      <button onClick={disconnect} disabled={status === "idle"}>
        Disconnect
      </button>
    </div>
  );
}
```

## Visual Feedback Examples

### Simple Indicator

```tsx
{isInterrupted && (
  <div className="bg-yellow-100 border-yellow-400 p-2 rounded">
    🎤 Listening to you...
  </div>
)}
```

### Animated Indicator

```tsx
{isInterrupted && (
  <div className="flex items-center gap-2 text-yellow-600">
    <div className="animate-pulse">●</div>
    <span>Interruption detected - Listening</span>
  </div>
)}
```

### Status Badge

```tsx
<div className={`status-badge ${
  status === 'connected' && !isInterrupted ? 'bg-green-500' :
  isInterrupted ? 'bg-yellow-500' :
  'bg-gray-500'
}`}>
  {status === 'connected' && !isInterrupted && 'Speaking'}
  {isInterrupted && 'Listening'}
  {status === 'idle' && 'Not Connected'}
</div>
```

## Testing Your Implementation

1. **Start the backend:**
   ```bash
   uvicorn main:app --reload
   ```

2. **Start the web client:**
   ```bash
   cd web
   npm run dev
   ```

3. **Test interruption:**
   - Connect to voice chat
   - Ask a long question
   - While AI is speaking, start talking
   - Observe: TTS stops, "Listening..." appears

4. **Check console logs:**
   ```
   [WebRTC] Interruption: Stopping response, listening to you
   [WebRTC] TTS cancelled: 8 chunks sent
   ```

## Summary of Changes

### Files to Modify:
1. ✅ `/web/src/hooks/use-webrtc.ts` - Add interruption message handlers
2. ✅ `/web/src/hooks/use-audio.ts` - Add `stopAudio()` function (if missing)
3. ✅ Your page component - Add interruption callbacks and UI feedback

### New Features:
- ✅ TTS stops immediately when user interrupts
- ✅ Visual feedback showing "Listening" state
- ✅ Proper cleanup of audio buffers
- ✅ Console logging for debugging

All changes are **backward compatible** - the existing functionality continues to work exactly as before!

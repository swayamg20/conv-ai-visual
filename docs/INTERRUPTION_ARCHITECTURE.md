# Interruption Handling Architecture (Modular Design)

## Overview

Interruption handling has been implemented as a **standalone, reusable module** in the `funcs/` directory, following the existing architectural patterns of the project.

## File Structure

```
voiceai/
├── funcs/
│   ├── interruption.py          # NEW: Interruption handling module
│   ├── __init__.py               # Updated: Exports interruption classes
│   ├── llm_pipeline.py
│   ├── tts_pipeline.py
│   ├── vad_gate.py
│   └── ...
├── main.py                       # Updated: Uses interruption module
└── docs/
    ├── INTERRUPTION_ARCHITECTURE.md    # This file
    ├── INTERRUPTION_CLIENT.md          # Client integration guide
    └── INTERRUPTION_CLIENT_EXAMPLE.md  # Specific code examples
```

## Module Design: `funcs/interruption.py`

### Components

#### 1. `InterruptionState` Class

Manages interruption state for a **single voice session**:

```python
class InterruptionState:
    """Manages interruption state for a single voice session."""

    def __init__(self):
        self.tts_active: bool               # Is TTS currently playing?
        self.tts_task: Optional[Task]       # Async task for TTS (can be cancelled)
        self.interrupt_event: asyncio.Event # Event flag for interruption signaling
        self.chunks_sent: int               # Count of TTS chunks sent before interruption

    def start_tts(task=None)        # Mark TTS as active
    def stop_tts()                  # Stop TTS and mark as inactive
    def signal_interrupt()          # Signal interruption (sets event)
    def clear_interrupt()           # Clear interruption flag
    def is_interrupted() -> bool    # Check if currently interrupted
```

**Design rationale:**
- Encapsulates all interruption state for one peer
- Uses `asyncio.Event` for clean async signaling
- Tracks TTS playback without coupling to TTS implementation

---

#### 2. `InterruptionManager` Class

Manages interruption state for **all peer connections**:

```python
class InterruptionManager:
    """Manages interruption state for all peer connections."""

    def __init__(self):
        self._states: Dict[str, InterruptionState]  # peer_id -> state

    def create_state(peer_id: str) -> InterruptionState
    def get_state(peer_id: str) -> Optional[InterruptionState]
    def cleanup_state(peer_id: str)

    def handle_interruption(
        peer_id: str,
        vad_detected: bool,
        tts_active: bool,
        transcript: str
    ) -> bool
        """Check conditions and signal interruption if needed."""

    async def stream_tts_with_interruption(
        peer_id: str,
        tts_generator,
        datachannel,
        on_chunk_callback=None,
        on_interrupted_callback=None,
        on_completed_callback=None
    )
        """Stream TTS with automatic interruption support."""
```

**Design rationale:**
- Centralized management of all peer states
- Provides high-level API for common operations
- Handles lifecycle (create, cleanup)
- Encapsulates interruption detection logic
- Provides TTS streaming with built-in interruption support

---

#### 3. Global Instance

```python
# Singleton instance for convenience
interruption_manager = InterruptionManager()
```

**Usage in main.py:**
```python
from funcs.interruption import interruption_manager

# Create state on connection
interruption_manager.create_state(pc_id)

# Check for interruption
if interruption_manager.handle_interruption(pc_id, vad_detected, tts_active, transcript):
    # Send ack to client
    ...

# Stream TTS with interruption support
await interruption_manager.stream_tts_with_interruption(
    peer_id=pc_id,
    tts_generator=tts_pipeline.text_to_speech_stream(llm_response),
    datachannel=ch
)

# Cleanup on disconnect
interruption_manager.cleanup_state(pc_id)
```

---

## Integration Points in main.py

### 1. Import (Line 22)

```python
from funcs.interruption import interruption_manager
```

### 2. State Creation (Line 562)

```python
# Initialize interruption state for this peer connection
interruption_manager.create_state(pc_id)
```

**When:** On WebRTC connection established (`/offer` endpoint)

---

### 3. Interruption Detection (Lines 226-243)

```python
# Check for interruption: user speaking while TTS is active
vad_detected = vad_speech_detected.get(pc_id, False)
state = interruption_manager.get_state(pc_id)
tts_active = state.tts_active if state else False

if interruption_manager.handle_interruption(
    pc_id, vad_detected, tts_active, transcript
):
    # Send acknowledgment to client
    if ch and ch.readyState == "open":
        interrupt_ack = json.dumps({
            "type": "interruption_ack",
            "message": "Stopping response, listening to you"
        })
        ch.send(interrupt_ack)

    # Wait for TTS to stop
    await asyncio.sleep(0.05)
```

**When:** On every Deepgram transcript event (interim and final)
**Triggers:** When VAD detected speech + TTS active + transcript exists

---

### 4. TTS Streaming (Lines 312-316)

```python
# Use interruption manager to stream TTS with cancellation support
await interruption_manager.stream_tts_with_interruption(
    peer_id=pc_id,
    tts_generator=tts_pipeline.text_to_speech_stream(llm_response),
    datachannel=ch
)
```

**When:** After LLM generates response
**Handles:**
- Marking TTS as active
- Checking interruption on each chunk
- Sending `tts_cancelled` message
- Sending `tts_audio_end` if completed
- Cleanup in finally block

---

### 5. State Cleanup (Lines 597, 629)

```python
# Cleanup interruption state and stop any active TTS
interruption_manager.cleanup_state(pc_id)
```

**When:**
- Datachannel closes
- Peer connection state becomes "failed" or "closed"

**Does:**
- Stops any active TTS task
- Removes state from manager
- Logs cleanup

---

## Data Flow

```
User speaks while AI responding
        ↓
VAD (Silero) detects speech
        ↓
Deepgram sends transcript
        ↓
on_deepgram_event() called
        ↓
interruption_manager.handle_interruption()
   ├─ Checks: vad_detected ✓
   ├─ Checks: tts_active ✓
   ├─ Checks: transcript exists ✓
   └─ Calls: state.signal_interrupt()
        ↓
        └─ Sets: interrupt_event.set()
                ↓
TTS streaming loop (in stream_tts_with_interruption)
   ├─ On each chunk iteration
   ├─ Checks: state.is_interrupted()
   ├─ If True:
   │    ├─ Breaks loop
   │    ├─ Sends: tts_cancelled message
   │    └─ Returns early
   └─ If False:
        └─ Continues streaming
```

---

## Benefits of Modular Design

### ✅ Separation of Concerns
- Interruption logic isolated from main server code
- Main.py stays focused on request handling and pipeline orchestration

### ✅ Reusability
- Can be used in other parts of the application
- Easy to test in isolation
- Can be imported in other projects

### ✅ Maintainability
- Changes to interruption logic only affect one file
- Clear API boundaries
- Self-documenting through type hints

### ✅ Testability
- Can unit test `InterruptionState` independently
- Can mock `InterruptionManager` in main.py tests
- Can test interruption detection logic separately

### ✅ Extensibility
- Easy to add new features (e.g., interruption analytics)
- Can add more sophisticated detection algorithms
- Can integrate with other modules cleanly

---

## Comparison: Before vs After

### Before (Monolithic in main.py)

```python
# main.py had:
class InterruptionState:
    ... # 30+ lines

interruption_states: Dict[str, InterruptionState] = {}

# TTS streaming: 60+ lines of nested logic
async for audio_chunk in tts_pipeline.text_to_speech_stream(...):
    if state and state.is_interrupted():
        # ... cancellation logic
    # ... chunk sending logic
    # ... cleanup logic

# Manual state management everywhere
interruption_states[pc_id] = InterruptionState()
state = interruption_states.pop(pc_id, None)
if state:
    state.stop_tts()
```

**Problems:**
- ❌ 100+ lines of interruption code in main.py
- ❌ Mixed with WebRTC connection logic
- ❌ Hard to find and modify
- ❌ Difficult to test
- ❌ No reusability

---

### After (Modular in funcs/interruption.py)

```python
# main.py now:
from funcs.interruption import interruption_manager

interruption_manager.create_state(pc_id)

if interruption_manager.handle_interruption(...):
    # Send ack

await interruption_manager.stream_tts_with_interruption(
    peer_id=pc_id,
    tts_generator=tts_pipeline.text_to_speech_stream(llm_response),
    datachannel=ch
)

interruption_manager.cleanup_state(pc_id)
```

**Benefits:**
- ✅ ~10 lines total in main.py
- ✅ Clear, declarative API
- ✅ Self-documenting
- ✅ Easy to test
- ✅ Reusable module

---

## Testing Strategy

### Unit Tests (for funcs/interruption.py)

```python
# test/test_interruption.py

async def test_interruption_state():
    state = InterruptionState()
    assert not state.tts_active

    state.start_tts()
    assert state.tts_active

    state.signal_interrupt()
    assert state.is_interrupted()
    assert not state.tts_active

async def test_interruption_manager():
    manager = InterruptionManager()
    state = manager.create_state("test-peer")

    assert manager.get_state("test-peer") == state

    # Test interruption detection
    detected = manager.handle_interruption(
        "test-peer",
        vad_detected=True,
        tts_active=True,
        transcript="Hello"
    )
    assert detected

    manager.cleanup_state("test-peer")
    assert manager.get_state("test-peer") is None
```

### Integration Tests (for main.py)

```python
# test/test_interruption_integration.py

async def test_webrtc_interruption_flow():
    # Setup WebRTC connection
    # Send audio
    # Start TTS
    # Interrupt with new audio
    # Verify tts_cancelled message sent
    # Verify state cleaned up
    ...
```

---

## Future Enhancements

### Phase 2: Client-Side Interrupt Signal

Add support for client sending explicit interrupt:

```python
# In interruption_manager
def handle_client_interrupt(peer_id: str):
    """Handle explicit interrupt signal from client."""
    state = self.get_state(peer_id)
    if state:
        state.signal_interrupt()
        return True
    return False
```

```python
# In main.py datachannel handler
@channel.on("message")
def on_message(message):
    try:
        data = json.loads(message)
        if data.get("type") == "interrupt":
            interruption_manager.handle_client_interrupt(pc_id)
    except:
        pass
```

---

### Phase 3: Analytics & Metrics

```python
class InterruptionManager:
    def __init__(self):
        self._states: Dict[str, InterruptionState] = {}
        self._metrics = {
            "total_interruptions": 0,
            "avg_chunks_before_interrupt": 0.0,
            "interruptions_by_peer": {}
        }

    def handle_interruption(self, peer_id, ...):
        detected = super().handle_interruption(...)
        if detected:
            self._metrics["total_interruptions"] += 1
            # ... track metrics
        return detected

    def get_metrics(self) -> dict:
        return self._metrics
```

---

## API Reference

### InterruptionState

| Method | Description | Returns |
|--------|-------------|---------|
| `start_tts(task=None)` | Mark TTS as active | None |
| `stop_tts()` | Stop TTS and mark inactive | None |
| `signal_interrupt()` | Signal interruption | None |
| `clear_interrupt()` | Clear interruption flag | None |
| `is_interrupted()` | Check if interrupted | bool |

### InterruptionManager

| Method | Description | Returns |
|--------|-------------|---------|
| `create_state(peer_id)` | Create state for peer | InterruptionState |
| `get_state(peer_id)` | Get state for peer | Optional[InterruptionState] |
| `cleanup_state(peer_id)` | Remove and cleanup state | None |
| `handle_interruption(...)` | Check and signal if needed | bool |
| `stream_tts_with_interruption(...)` | Stream TTS with support | None (async) |

---

## Summary

The interruption handling system is now a **first-class, reusable module** following the same patterns as other components in `funcs/`:

- **Modular:** Self-contained in `funcs/interruption.py`
- **Clean API:** Simple, intuitive methods
- **Testable:** Can be tested independently
- **Documented:** Type hints and docstrings
- **Extensible:** Easy to add features
- **Maintainable:** Clear separation of concerns

The refactoring reduces main.py complexity while providing a more powerful and flexible interruption system.

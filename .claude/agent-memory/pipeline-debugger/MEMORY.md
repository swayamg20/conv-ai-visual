# Pipeline Debugger Memory

## Critical Voice Pipeline Issue - Smart Turn Blocking LLM

### Issue Pattern
**Symptom**: Voice pipeline receives transcripts from Deepgram but never calls LLM/TTS. Chat API works fine.

**Root Cause**: Smart Turn is enabled by default (`config.py` line 145: `SMART_TURN_ENABLED: bool = os.getenv("SMART_TURN_ENABLED", "true").lower() == "true"`), but when Smart Turn is enabled without the ONNX model file, the voice pipeline blocks indefinitely.

### Code Flow Analysis

1. **main.py:603-629** - Smart Turn path logic:
   - When `st_session` exists (Smart Turn enabled), Deepgram `is_final` events accumulate transcript but do NOT trigger LLM
   - Only `speech_final` events trigger Smart Turn inference
   - Smart Turn returns `(is_complete, text)` tuple
   - **LLM only fires if `is_complete=True AND accumulated_text is truthy`** (line 628-629)

2. **main.py:631-636** - Legacy path (NO Smart Turn):
   - When `st_session is None`, ANY `is_final` or `speech_final` event triggers LLM directly
   - This is why chat works (doesn't use Smart Turn) but voice doesn't

3. **Smart Turn incomplete state**:
   - If Smart Turn predicts "incomplete", it sets `_pending=True` and starts a fallback timer (line 357-360 in smart_turn.py)
   - Fallback timer duration = `SMART_TURN_STOP_SECS` (default 2.0 seconds)
   - If fallback fires, it calls `on_fallback_complete` callback (line 545-548 in main.py)
   - **BUT**: If Smart Turn model fails to load or returns unexpected results, the fallback may never fire correctly

### Failure Modes

1. **Smart Turn model missing**: Line 54-67 in main.py attempts to initialize SmartTurnAnalyzer but catches exceptions. If init fails silently, `smart_turn_analyzer` is set to `None`, but this doesn't prevent `st_session` creation on line 540-551 if the analyzer was previously set.

2. **Model path resolution**: smart_turn.py:93-115 tries local paths first, then downloads from HuggingFace. Network issues or permission errors can cause silent failures.

3. **Threshold too high**: Default threshold is 0.5 (config.py:146). If threshold is too high, Smart Turn always returns "incomplete", and the fallback timer becomes the only path to LLM.

4. **Race condition**: User stops speaking before `speech_final` fires from Deepgram. Transcript accumulates on `is_final` events but Smart Turn never runs because `speech_final` condition isn't met (line 610).

### Fix Strategy

**Immediate**: Disable Smart Turn in .env to use legacy path:
```
SMART_TURN_ENABLED=false
```

**Proper**: Check Smart Turn model loading and error handling:
1. Verify model downloads to `/models/` directory
2. Check logs for Smart Turn init errors (line 66 in main.py)
3. Add explicit logging when `st_session` is created vs when Smart Turn is disabled
4. Ensure fallback callback is properly wired (line 550 in main.py)

### Key Files
- `/Users/swayam.gupta/Documents/GitHub/voiceai/main.py` - lines 540-636 (Smart Turn session creation and Deepgram event handler)
- `/Users/swayam.gupta/Documents/GitHub/voiceai/funcs/smart_turn.py` - SmartTurnSession state machine
- `/Users/swayam.gupta/Documents/GitHub/voiceai/funcs/config.py` - line 145 (SMART_TURN_ENABLED default)

### Diagnostic Commands
Check if Smart Turn model exists:
```bash
ls -la models/smart-turn-*.onnx
```

Check server logs for Smart Turn messages:
```bash
# Look for: "Smart Turn analyzer initialized" or "Smart Turn init failed"
```

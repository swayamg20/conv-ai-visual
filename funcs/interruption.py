"""
Interruption handling for real-time voice conversations.

This module manages interruption detection and TTS cancellation when users
speak while the AI is responding.
"""

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class InterruptionState:
    """Manages interruption state for a single voice session."""

    # Grace period after TTS starts before interruptions are allowed (seconds)
    # This prevents echo/trailing audio from immediately cancelling the response
    INTERRUPT_GRACE_PERIOD = 0.5

    def __init__(self):
        self.tts_active = False
        self.tts_task: Optional[asyncio.Task] = None
        self.interrupt_event = asyncio.Event()
        self.chunks_sent = 0
        self.interrupt_ack_sent = False
        self.tts_start_time: float = 0

    def start_tts(self, task: Optional[asyncio.Task] = None):
        """Mark TTS as active and optionally store the task."""
        import time
        logger.debug("▶️ TTS marked as active")
        self.tts_active = True
        self.tts_task = task
        self.chunks_sent = 0
        self.interrupt_event.clear()
        self.interrupt_ack_sent = False
        self.tts_start_time = time.time()

    def stop_tts(self):
        """Stop TTS generation and mark as inactive."""
        logger.debug("⏹️ TTS marked as inactive")
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
        self.tts_active = False

    def in_grace_period(self) -> bool:
        """Check if we're within the grace period after TTS started."""
        import time
        if not self.tts_active or self.tts_start_time == 0:
            return False
        elapsed = time.time() - self.tts_start_time
        in_grace = elapsed < self.INTERRUPT_GRACE_PERIOD
        if in_grace:
            logger.debug("🛡️ In grace period (%.2fs elapsed, need %.2fs)", elapsed, self.INTERRUPT_GRACE_PERIOD)
        return in_grace

    def signal_interrupt(self, force: bool = False):
        """Signal that user has interrupted.
        
        Args:
            force: If True, ignore grace period (used for explicit client interrupts)
        """
        # Skip if within grace period (unless forced)
        if not force and self.in_grace_period():
            logger.debug("🛡️ Ignoring interrupt during grace period")
            return False
        
        logger.info("🛑 Interruption signal raised - stopping TTS")
        self.interrupt_event.set()
        self.stop_tts()
        return True

    def clear_interrupt(self):
        """Clear interruption flag."""
        self.interrupt_event.clear()
        self.interrupt_ack_sent = False

    def is_interrupted(self) -> bool:
        """Check if currently interrupted."""
        return self.interrupt_event.is_set()


class InterruptionManager:
    """Manages interruption state for all peer connections."""

    def __init__(self):
        self._states: Dict[str, InterruptionState] = {}

    def create_state(self, peer_id: str) -> InterruptionState:
        """Create and track interruption state for a peer connection."""
        if peer_id in self._states:
            logger.warning("[%s] Interruption state already exists, recreating", peer_id)
            self.cleanup_state(peer_id)

        state = InterruptionState()
        self._states[peer_id] = state
        logger.debug("[%s] Interruption state created", peer_id)
        return state

    def get_state(self, peer_id: str) -> Optional[InterruptionState]:
        """Get interruption state for a peer connection."""
        return self._states.get(peer_id)

    def cleanup_state(self, peer_id: str):
        """Remove and cleanup interruption state for a peer connection."""
        state = self._states.pop(peer_id, None)
        if state:
            state.stop_tts()
            logger.debug("[%s] Interruption state cleaned up", peer_id)

    def handle_interruption(
        self,
        peer_id: str,
        vad_detected: bool,
        tts_active: bool,
        transcript: str
    ) -> bool:
        state = self.get_state(peer_id)
        if state and tts_active and vad_detected and transcript.strip():
            logger.info("[%s] INTERRUPTION detected: user said '%s' while TTS active",peer_id,transcript[:50])
            state.signal_interrupt()
            return True
        return False

    async def stream_tts_with_interruption(
        self,
        peer_id: str,
        tts_generator,
        datachannel,
        on_chunk_callback=None,
        on_interrupted_callback=None,
        on_completed_callback=None
    ):
        """
        Stream TTS audio with interruption support.

        Args:
            peer_id: Peer connection ID
            tts_generator: Async generator yielding audio chunks
            datachannel: RTCDataChannel for sending messages
            on_chunk_callback: Optional callback(chunk_index, audio_chunk)
            on_interrupted_callback: Optional callback(chunks_sent)
            on_completed_callback: Optional callback(total_chunks, total_bytes)
        """
        import base64
        import json

        state = self.get_state(peer_id)
        if not state:
            logger.error("[%s] No interruption state found for TTS streaming", peer_id)
            return

        # Mark TTS as active before starting
        state.start_tts()
        logger.info("[%s] 🎤 Starting TTS stream with interruption support (tts_active=%s)", peer_id, state.tts_active)

        chunk_count = 0
        total_bytes = 0
        interrupted = False
        first_chunk_sent = False

        async def get_next_chunk():
            """Wrapper to get next chunk from generator."""
            try:
                return await tts_generator.__anext__()
            except StopAsyncIteration:
                return None

        try:
            # Stream audio chunks as they're generated
            # Use asyncio.wait to race between next chunk and interrupt event
            while True:
                # Check for interruption BEFORE waiting for chunk
                if state.is_interrupted():
                    logger.warning("[%s] 🛑 TTS INTERRUPTED at chunk %d (pre-check) - STOPPING NOW", peer_id, chunk_count)
                    interrupted = True
                    break

                # Create task for getting next chunk
                chunk_task = asyncio.create_task(get_next_chunk())

                # Race: wait for either next chunk OR interrupt signal
                # Check interrupt every 50ms while waiting for chunk
                audio_chunk = None
                while not chunk_task.done():
                    try:
                        # Wait for chunk with short timeout, then check interrupt
                        await asyncio.wait_for(asyncio.shield(chunk_task), timeout=0.05)
                    except asyncio.TimeoutError:
                        # Check interrupt during wait
                        if state.is_interrupted():
                            logger.warning("[%s] 🛑 TTS INTERRUPTED during chunk wait - CANCELLING", peer_id)
                            chunk_task.cancel()
                            try:
                                await chunk_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            interrupted = True
                            break

                if interrupted:
                    break

                # Get the chunk result
                try:
                    audio_chunk = chunk_task.result()
                except Exception as e:
                    logger.warning("[%s] Error getting chunk: %s", peer_id, e)
                    break

                if audio_chunk is None:
                    # Generator exhausted
                    break

                # Track first chunk latency
                if not first_chunk_sent:
                    import time
                    first_chunk_sent = True
                    logger.info("[%s] ⏱️ TTS first chunk ready", peer_id)

                # Check for interruption AFTER receiving chunk (double-check)
                if state.is_interrupted():
                    logger.warning("[%s] 🛑 TTS INTERRUPTED at chunk %d (post-check) - STOPPING NOW", peer_id, chunk_count)
                    interrupted = True
                    break

                if datachannel and datachannel.readyState == "open":
                    # Encode chunk as base64
                    audio_b64 = base64.b64encode(audio_chunk).decode('utf-8')

                    # Send chunk via datachannel
                    tts_payload = json.dumps({
                        "type": "tts_audio_chunk",
                        "audio": audio_b64,
                        "format": "pcm_16000",
                        "sample_rate": 16000,
                        "chunk_index": chunk_count
                    })
                    datachannel.send(tts_payload)

                    chunk_count += 1
                    total_bytes += len(audio_chunk)
                    state.chunks_sent = chunk_count

                    # Call chunk callback
                    if on_chunk_callback:
                        await on_chunk_callback(chunk_count - 1, audio_chunk)

            # Send cancellation notice to client if interrupted
            if interrupted:
                if datachannel and datachannel.readyState == "open":
                    cancel_payload = json.dumps({
                        "type": "tts_cancelled",
                        "chunks_sent": chunk_count,
                        "message": "TTS interrupted by user speech"
                    })
                    datachannel.send(cancel_payload)
                    logger.info("[%s] 📤 Sent tts_cancelled to client", peer_id)

                # Call interrupted callback
                if on_interrupted_callback:
                    await on_interrupted_callback(chunk_count)

                logger.warning(
                    "[%s] 🛑 TTS INTERRUPTED: %d chunks sent before interruption",
                    peer_id,
                    chunk_count
                )
            else:
                # Only send end marker if completed without interruption
                if datachannel and datachannel.readyState == "open":
                    end_payload = json.dumps({
                        "type": "tts_audio_end",
                        "total_chunks": chunk_count,
                        "total_bytes": total_bytes
                    })
                    datachannel.send(end_payload)

                    # Call completed callback
                    if on_completed_callback:
                        await on_completed_callback(chunk_count, total_bytes)

                logger.info(
                    "[%s] ✅ TTS stream completed: %d chunks, %d bytes",
                    peer_id,
                    chunk_count,
                    total_bytes
                )

        except Exception as e:
            logger.exception("[%s] Error during TTS streaming: %s", peer_id, e)
            raise
        finally:
            # Mark TTS as inactive and clear interruption
            logger.debug("[%s] 🔄 Cleaning up TTS state", peer_id)
            state.tts_active = False
            state.clear_interrupt()


# Global interruption manager instance
interruption_manager = InterruptionManager()

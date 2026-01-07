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

    def __init__(self):
        self.tts_active = False
        self.tts_task: Optional[asyncio.Task] = None
        self.interrupt_event = asyncio.Event()
        self.chunks_sent = 0

    def start_tts(self, task: Optional[asyncio.Task] = None):
        """Mark TTS as active and optionally store the task."""
        self.tts_active = True
        self.tts_task = task
        self.chunks_sent = 0

    def stop_tts(self):
        """Stop TTS generation and mark as inactive."""
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
        self.tts_active = False

    def signal_interrupt(self):
        """Signal that user has interrupted."""
        self.interrupt_event.set()
        self.stop_tts()

    def clear_interrupt(self):
        """Clear interruption flag."""
        self.interrupt_event.clear()

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
        logger.info("[%s] Starting TTS stream with interruption support", peer_id)

        chunk_count = 0
        total_bytes = 0
        interrupted = False

        try:
            # Stream audio chunks as they're generated
            async for audio_chunk in tts_generator:
                # Check for interruption
                if state.is_interrupted():
                    logger.info("[%s] TTS interrupted at chunk %d", peer_id, chunk_count)
                    interrupted = True

                    # Send cancellation notice to client
                    if datachannel and datachannel.readyState == "open":
                        cancel_payload = json.dumps({
                            "type": "tts_cancelled",
                            "chunks_sent": chunk_count,
                            "message": "TTS interrupted by user speech"
                        })
                        datachannel.send(cancel_payload)

                    # Call interrupted callback
                    if on_interrupted_callback:
                        await on_interrupted_callback(chunk_count)

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

            # Only send end marker if completed without interruption
            if not interrupted and datachannel and datachannel.readyState == "open":
                end_payload = json.dumps({
                    "type": "tts_audio_end",
                    "total_chunks": chunk_count,
                    "total_bytes": total_bytes
                })
                datachannel.send(end_payload)

                # Call completed callback
                if on_completed_callback:
                    await on_completed_callback(chunk_count, total_bytes)

            if interrupted:
                logger.info(
                    "[%s] TTS interrupted: %d chunks sent before interruption",
                    peer_id,
                    chunk_count
                )
            else:
                logger.info(
                    "[%s] TTS stream completed: %d chunks, %d bytes",
                    peer_id,
                    chunk_count,
                    total_bytes
                )

        except Exception as e:
            logger.exception("[%s] Error during TTS streaming: %s", peer_id, e)
            raise
        finally:
            # Mark TTS as inactive and clear interruption
            state.tts_active = False
            state.clear_interrupt()


# Global interruption manager instance
interruption_manager = InterruptionManager()

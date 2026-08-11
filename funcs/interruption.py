"""
Interruption handling for real-time voice conversations.
"""

import asyncio
import base64
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class InterruptionState:
    """Manages interruption state for a single voice session."""

    def __init__(self):
        self.tts_active = False
        self.interrupt_event = asyncio.Event()

    def start_tts(self):
        """Mark TTS as active."""
        self.tts_active = True
        self.interrupt_event.clear()

    def stop_tts(self):
        """Stop TTS."""
        self.tts_active = False

    def signal_interrupt(self):
        """Signal interruption."""
        self.interrupt_event.set()

    def is_interrupted(self) -> bool:
        """Check if interrupted."""
        return self.interrupt_event.is_set()


class InterruptionManager:
    """Manages interruption state for all peer connections."""

    def __init__(self):
        self._states: Dict[str, InterruptionState] = {}

    def create_state(self, peer_id: str) -> InterruptionState:
        """Create interruption state."""
        state = InterruptionState()
        self._states[peer_id] = state
        return state

    def get_state(self, peer_id: str) -> Optional[InterruptionState]:
        """Get interruption state."""
        return self._states.get(peer_id)

    def cleanup_state(self, peer_id: str):
        """Cleanup interruption state."""
        self._states.pop(peer_id, None)

    async def stream_tts_with_interruption(self, peer_id: str, tts_generator, datachannel):
        """Stream TTS with interruption support."""
        state = self.get_state(peer_id)
        if not state:
            return

        state.start_tts()
        chunk_count = 0
        interrupted = False

        try:
            async for audio_chunk in tts_generator:
                # Check interrupt before sending each chunk
                if state.is_interrupted():
                    interrupted = True
                    break

                if datachannel and datachannel.readyState == "open":
                    audio_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                    datachannel.send(
                        json.dumps(
                            {
                                "type": "tts_audio_chunk",
                                "audio": audio_b64,
                                "format": "pcm_16000",
                                "sample_rate": 16000,
                                "chunk_index": chunk_count,
                            }
                        )
                    )
                    chunk_count += 1

            # Send completion or cancellation message
            if datachannel and datachannel.readyState == "open":
                if interrupted:
                    datachannel.send(json.dumps({"type": "tts_cancelled"}))
                    logger.info("[%s] 🛑 TTS interrupted at chunk %d", peer_id, chunk_count)
                else:
                    datachannel.send(json.dumps({"type": "tts_audio_end"}))
                    logger.info("[%s] ✅ TTS complete: %d chunks", peer_id, chunk_count)

        except Exception as e:
            logger.exception("[%s] TTS error: %s", peer_id, e)
        finally:
            state.stop_tts()
            state.interrupt_event.clear()


# Global instance
interruption_manager = InterruptionManager()

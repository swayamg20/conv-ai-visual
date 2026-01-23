"""
VAD-based endpoint detection to replace Deepgram's slow endpointing.

Uses Silero VAD for speech end detection combined with interim transcripts
to achieve sub-300ms endpoint detection vs 1800ms default.

Architecture:
    Audio → [Parallel] → Silero VAD (speech end detection)
                      → Deepgram STT (transcription only)

    When VAD detects sustained silence AND we have a pending transcript → trigger LLM
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from collections import deque

logger = logging.getLogger("endpoint-detector")


@dataclass
class EndpointState:
    """
    State for a single voice session's endpoint detection.

    Tracks speech activity, silence duration, and pending transcripts
    to determine when the user has finished speaking.
    """
    # VAD state tracking
    speech_active: bool = False
    speech_start_time: Optional[float] = None
    silence_start_time: Optional[float] = None
    consecutive_silence_frames: int = 0
    consecutive_speech_frames: int = 0

    # Transcript state
    pending_transcript: str = ""
    last_transcript_time: float = 0.0
    transcript_word_count: int = 0

    # Configuration thresholds
    silence_threshold_ms: float = 300.0  # Silence duration to trigger endpoint
    min_speech_duration_ms: float = 100.0  # Minimum speech before considering endpoint
    min_words_for_endpoint: int = 1  # Minimum words in transcript
    speech_confirm_frames: int = 2  # Consecutive speech frames to confirm speech start
    silence_confirm_frames: int = 3  # Consecutive silence frames to confirm silence

    # History for debugging/analysis
    vad_history: deque = field(default_factory=lambda: deque(maxlen=20))

    # Metrics
    total_speech_frames: int = 0
    total_silence_frames: int = 0
    endpoints_triggered: int = 0
    last_endpoint_time: Optional[float] = None


class EndpointDetector:
    """
    Manages VAD-based endpoint detection for all voice sessions.

    Flow:
    1. Audio comes in → fed to both VAD and Deepgram (parallel)
    2. VAD tracks speech/silence transitions
    3. Deepgram sends interim transcripts
    4. When VAD detects sustained silence AND we have transcript → trigger LLM

    This replaces Deepgram's 1800ms endpointing with ~300ms detection.
    """

    def __init__(
        self,
        silence_threshold_ms: float = 300.0,
        min_speech_duration_ms: float = 100.0,
        min_words_for_endpoint: int = 1,
    ):
        """
        Initialize the endpoint detector.

        Args:
            silence_threshold_ms: Duration of silence (ms) before triggering endpoint
            min_speech_duration_ms: Minimum speech duration (ms) before considering endpoint
            min_words_for_endpoint: Minimum word count in transcript to trigger endpoint
        """
        self._states: dict[str, EndpointState] = {}
        self.silence_threshold_ms = silence_threshold_ms
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_words_for_endpoint = min_words_for_endpoint

        logger.info(
            "EndpointDetector initialized: silence_threshold=%dms, min_speech=%dms, min_words=%d",
            silence_threshold_ms, min_speech_duration_ms, min_words_for_endpoint
        )

    def create_state(self, peer_id: str) -> EndpointState:
        """
        Create endpoint detection state for a new session.

        Args:
            peer_id: Unique identifier for the peer connection

        Returns:
            New EndpointState instance
        """
        state = EndpointState(
            silence_threshold_ms=self.silence_threshold_ms,
            min_speech_duration_ms=self.min_speech_duration_ms,
            min_words_for_endpoint=self.min_words_for_endpoint,
        )
        self._states[peer_id] = state
        logger.debug("[%s] Endpoint state created", peer_id)
        return state

    def get_state(self, peer_id: str) -> Optional[EndpointState]:
        """Get endpoint state for a session."""
        return self._states.get(peer_id)

    def cleanup_state(self, peer_id: str) -> None:
        """Clean up endpoint state when session ends."""
        if peer_id in self._states:
            state = self._states.pop(peer_id)
            logger.info(
                "[%s] Endpoint state cleaned up: %d endpoints triggered, %d speech frames, %d silence frames",
                peer_id, state.endpoints_triggered, state.total_speech_frames, state.total_silence_frames
            )

    def process_vad_result(
        self,
        peer_id: str,
        is_speech: bool,
        vad_latency_ms: float = 0.0,
    ) -> Optional[str]:
        """
        Process VAD result and check for endpoint.

        Called after each VAD inference. Tracks speech/silence transitions
        and triggers endpoint when conditions are met.

        Args:
            peer_id: Peer connection identifier
            is_speech: True if VAD detected speech in this frame
            vad_latency_ms: VAD inference latency (for logging)

        Returns:
            Transcript string if endpoint detected, None otherwise
        """
        state = self.get_state(peer_id)
        if not state:
            return None

        now = time.time()
        state.vad_history.append((now, is_speech))

        if is_speech:
            return self._handle_speech_frame(state, now, peer_id)
        else:
            return self._handle_silence_frame(state, now, peer_id)

    def _handle_speech_frame(
        self,
        state: EndpointState,
        now: float,
        peer_id: str,
    ) -> None:
        """Handle a frame where speech was detected."""
        state.total_speech_frames += 1
        state.consecutive_speech_frames += 1
        state.consecutive_silence_frames = 0

        # Confirm speech start after consecutive frames (debounce)
        if not state.speech_active and state.consecutive_speech_frames >= state.speech_confirm_frames:
            state.speech_active = True
            state.speech_start_time = now
            state.silence_start_time = None
            logger.debug("[%s] Speech started (confirmed)", peer_id)

        return None

    def _handle_silence_frame(
        self,
        state: EndpointState,
        now: float,
        peer_id: str,
    ) -> Optional[str]:
        """
        Handle a frame where silence was detected.

        Returns transcript if endpoint conditions are met.
        """
        state.total_silence_frames += 1
        state.consecutive_silence_frames += 1
        state.consecutive_speech_frames = 0

        # Only consider endpoint if we were speaking
        if not state.speech_active:
            return None

        # Start tracking silence duration
        if state.silence_start_time is None:
            state.silence_start_time = now

        # Check if silence is confirmed (debounce)
        if state.consecutive_silence_frames < state.silence_confirm_frames:
            return None

        # Calculate silence duration
        silence_duration_ms = (now - state.silence_start_time) * 1000

        # Check if silence threshold exceeded
        if silence_duration_ms < state.silence_threshold_ms:
            return None

        # Validate speech duration
        if state.speech_start_time:
            speech_duration_ms = (state.silence_start_time - state.speech_start_time) * 1000
            if speech_duration_ms < state.min_speech_duration_ms:
                logger.debug(
                    "[%s] Speech too short (%.0fms < %.0fms), ignoring",
                    peer_id, speech_duration_ms, state.min_speech_duration_ms
                )
                self._reset_state(state)
                return None

        # Check if we have sufficient transcript
        transcript = state.pending_transcript.strip()
        if not transcript:
            logger.debug("[%s] No transcript available yet, waiting...", peer_id)
            return None

        word_count = len(transcript.split())
        if word_count < state.min_words_for_endpoint:
            logger.debug(
                "[%s] Transcript too short (%d words < %d), waiting...",
                peer_id, word_count, state.min_words_for_endpoint
            )
            return None

        # ENDPOINT DETECTED
        state.endpoints_triggered += 1
        state.last_endpoint_time = now

        logger.info(
            "[%s] ENDPOINT DETECTED: silence=%.0fms, words=%d, transcript='%s'",
            peer_id,
            silence_duration_ms,
            word_count,
            transcript[:50] + "..." if len(transcript) > 50 else transcript
        )

        # Reset state for next utterance
        self._reset_state(state)

        return transcript

    def _reset_state(self, state: EndpointState) -> None:
        """Reset state after endpoint triggered or invalid speech."""
        state.speech_active = False
        state.speech_start_time = None
        state.silence_start_time = None
        state.consecutive_silence_frames = 0
        state.consecutive_speech_frames = 0
        state.pending_transcript = ""
        state.transcript_word_count = 0

    def process_interim_transcript(
        self,
        peer_id: str,
        transcript: str,
        is_final: bool = False,
    ) -> None:
        """
        Process interim transcript from Deepgram.

        Stores the transcript for when VAD triggers endpoint.

        Args:
            peer_id: Peer connection identifier
            transcript: Transcript text (interim or final)
            is_final: Whether this is a final transcript from Deepgram
        """
        state = self.get_state(peer_id)
        if not state:
            return

        transcript = transcript.strip()
        if not transcript:
            return

        state.pending_transcript = transcript
        state.last_transcript_time = time.time()
        state.transcript_word_count = len(transcript.split())

        logger.debug(
            "[%s] Transcript updated: '%s' (final=%s, words=%d)",
            peer_id,
            transcript[:30] + "..." if len(transcript) > 30 else transcript,
            is_final,
            state.transcript_word_count
        )

    def force_endpoint(self, peer_id: str) -> Optional[str]:
        """
        Force endpoint detection (e.g., when Deepgram sends final as backup).

        Args:
            peer_id: Peer connection identifier

        Returns:
            Transcript if available, None otherwise
        """
        state = self.get_state(peer_id)
        if not state or not state.pending_transcript.strip():
            return None

        transcript = state.pending_transcript.strip()
        logger.info("[%s] FORCED ENDPOINT: '%s'", peer_id, transcript[:50])

        state.endpoints_triggered += 1
        state.last_endpoint_time = time.time()
        self._reset_state(state)

        return transcript

    def get_metrics(self, peer_id: str) -> dict:
        """Get endpoint detection metrics for a session."""
        state = self.get_state(peer_id)
        if not state:
            return {}

        return {
            "endpoints_triggered": state.endpoints_triggered,
            "total_speech_frames": state.total_speech_frames,
            "total_silence_frames": state.total_silence_frames,
            "speech_active": state.speech_active,
            "pending_transcript_length": len(state.pending_transcript),
            "last_endpoint_time": state.last_endpoint_time,
        }


# Global singleton instance - will be configured from config on import
endpoint_detector: Optional[EndpointDetector] = None


def get_endpoint_detector() -> EndpointDetector:
    """Get or create the global endpoint detector instance."""
    global endpoint_detector
    if endpoint_detector is None:
        # Import config here to avoid circular imports
        from funcs.config import config

        endpoint_detector = EndpointDetector(
            silence_threshold_ms=getattr(config, 'VAD_SILENCE_THRESHOLD_MS', 300),
            min_speech_duration_ms=getattr(config, 'VAD_MIN_SPEECH_MS', 100),
            min_words_for_endpoint=getattr(config, 'VAD_MIN_WORDS', 1),
        )
    return endpoint_detector

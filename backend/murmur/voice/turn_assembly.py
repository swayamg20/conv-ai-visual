"""Transport-neutral final-segment assembly for voice turns.

STT providers may finalize several transcript segments before they signal that a
speaker has actually yielded the turn. This module owns that small state
machine so the production transcriber and deterministic evaluator cannot drift
into different definitions of a committed turn.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

DEFAULT_MAX_PENDING_SEGMENTS = 128
DEFAULT_MAX_PENDING_CHARACTERS = 12_000
DEFAULT_MAX_PENDING_AGE_SECONDS = 30.0


def normalize_transcript(text: str) -> str:
    """Collapse provider whitespace without otherwise changing transcript text."""
    return " ".join(text.split())


class TranscriptAccumulator:
    """Collect unique final segments until an explicit turn boundary arrives."""

    def __init__(
        self,
        *,
        max_segments: int,
        max_characters: int,
        max_age_seconds: float,
    ) -> None:
        if max_segments < 1:
            raise ValueError("max_segments must be positive")
        if max_characters < 1:
            raise ValueError("max_characters must be positive")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")

        self.max_segments = max_segments
        self.max_characters = max_characters
        self.max_age_seconds = max_age_seconds
        self.segments: list[str] = []
        self.first_segment_at: float | None = None
        self.character_count = 0
        self.revision = 0
        self.last_boundary_revision: int | None = None
        self.last_committed_keys: frozenset[tuple[Any, ...]] = frozenset()
        recent_capacity = max(128, max_segments)
        self._recent_keys: deque[tuple[Any, ...]] = deque(maxlen=recent_capacity)
        self._recent_key_set: set[tuple[Any, ...]] = set()

    def add_final(
        self,
        metadata: Mapping[str, Any],
        transcript: str,
        *,
        observed_at: float,
    ) -> bool:
        """Add one final segment, returning whether it changed pending state."""
        text = normalize_transcript(transcript)
        if not text:
            return False

        key = self._segment_key(metadata, text)
        if key in self._recent_key_set:
            return False
        if not self.segments and key in self.last_committed_keys:
            return False

        if len(self._recent_keys) == self._recent_keys.maxlen:
            oldest = self._recent_keys.popleft()
            self._recent_key_set.discard(oldest)
        self._recent_keys.append(key)
        self._recent_key_set.add(key)
        if self.first_segment_at is None:
            self.first_segment_at = observed_at
        self.segments.append(text)
        self.character_count += len(text)
        self.revision += 1
        return True

    def text(self) -> str:
        """Return the normalized pending turn text."""
        return normalize_transcript(" ".join(self.segments))

    def exceeded_limits(self, now: float) -> bool:
        """Return whether pending state has crossed any fail-closed bound."""
        age = 0.0 if self.first_segment_at is None else max(0.0, now - self.first_segment_at)
        return (
            len(self.segments) > self.max_segments
            or self.character_count > self.max_characters
            or age > self.max_age_seconds
        )

    def mark_boundary(self) -> bool:
        """Accept at most one explicit boundary for the current revision."""
        if not self.segments or self.last_boundary_revision == self.revision:
            return False
        self.last_boundary_revision = self.revision
        return True

    def clear(self, *, committed: bool = False) -> None:
        """Clear pending state and optionally suppress a retried committed segment."""
        if committed:
            self.last_committed_keys = frozenset(self._recent_key_set)
        else:
            self.last_committed_keys = frozenset()
        self.segments.clear()
        self._recent_keys.clear()
        self._recent_key_set.clear()
        self.first_segment_at = None
        self.character_count = 0
        self.last_boundary_revision = None

    def note_speech_resumed(self) -> None:
        """Allow a fresh turn to repeat text from the preceding committed turn."""
        if not self.segments:
            self.last_committed_keys = frozenset()
            self._recent_keys.clear()
            self._recent_key_set.clear()

    @staticmethod
    def _segment_key(metadata: Mapping[str, Any], transcript: str) -> tuple[Any, ...]:
        start = metadata.get("start")
        duration = metadata.get("duration")
        channel_index = metadata.get("channel_index")
        if start is not None or duration is not None or channel_index is not None:
            return (
                "positioned",
                repr(start),
                repr(duration),
                repr(channel_index),
                transcript,
            )
        return ("unpositioned", transcript)

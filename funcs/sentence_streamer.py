"""
Sentence-level streaming for LLM → TTS pipeline.

Detects sentence boundaries in LLM output and triggers TTS early,
reducing latency by ~300-500ms compared to waiting for full response.

Architecture:
    LLM Stream → SentenceBuffer → [sentence detected] → TTS Queue → Audio Stream
                      ↓
                [continue buffering]

This allows TTS to start on the first sentence while LLM continues generating.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import (
    AsyncGenerator,
    Callable,
    Optional,
    Any,
    Awaitable,
)

logger = logging.getLogger("sentence-streamer")

# Sentence boundary patterns
# Matches period, exclamation, or question mark followed by space or end of string
SENTENCE_END_PATTERN = re.compile(r'[.!?](?:\s|$)')

# Patterns that look like sentence ends but aren't (abbreviations, etc.)
# These should NOT trigger sentence boundary detection
FALSE_ENDINGS = re.compile(
    r'\b(?:'
    r'Mr|Mrs|Ms|Dr|Prof|Sr|Jr|'
    r'Inc|Ltd|Corp|Co|'
    r'vs|etc|e\.g|i\.e|'
    r'St|Ave|Blvd|Rd|'
    r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec|'
    r'Mon|Tue|Wed|Thu|Fri|Sat|Sun|'
    r'[A-Z]'  # Single capital letter (initials)
    r')\.$',
    re.IGNORECASE
)

# Patterns for list items that might have periods but aren't sentence ends
LIST_ITEM_PATTERN = re.compile(r'^\s*(?:\d+\.|[-*•])\s*')


@dataclass
class SentenceBuffer:
    """
    Buffer for accumulating LLM tokens and detecting sentence boundaries.

    Accumulates text chunks and returns complete sentences when detected.
    Handles edge cases like abbreviations, lists, and code blocks.
    """
    text: str = ""
    sentences_yielded: int = 0
    min_sentence_chars: int = 15  # Minimum chars for a valid sentence
    max_buffer_chars: int = 500  # Force flush if buffer gets too large

    # Track state
    in_code_block: bool = False
    last_yield_time: float = field(default_factory=time.time)

    def add(self, chunk: str) -> Optional[str]:
        """
        Add a text chunk and return complete sentence if boundary detected.

        Args:
            chunk: Text chunk from LLM stream

        Returns:
            Complete sentence if boundary detected, None otherwise
        """
        self.text += chunk

        # Track code blocks (don't split inside them)
        if "```" in chunk:
            # Toggle code block state
            code_block_count = chunk.count("```")
            if code_block_count % 2 == 1:
                self.in_code_block = not self.in_code_block

        # Don't split inside code blocks
        if self.in_code_block:
            return None

        # Look for sentence boundary
        sentence = self._find_sentence_boundary()
        if sentence:
            self.sentences_yielded += 1
            self.last_yield_time = time.time()
            logger.debug(
                "Sentence %d detected (%d chars): '%s'",
                self.sentences_yielded,
                len(sentence),
                sentence[:50] + "..." if len(sentence) > 50 else sentence
            )
            return sentence

        # Force flush if buffer is getting too large (prevents memory issues)
        if len(self.text) > self.max_buffer_chars:
            return self._force_flush_partial()

        return None

    def _find_sentence_boundary(self) -> Optional[str]:
        """
        Find and extract a complete sentence from the buffer.

        Returns sentence text if found, None otherwise.
        """
        text = self.text

        # Find potential sentence endings
        for match in SENTENCE_END_PATTERN.finditer(text):
            end_pos = match.end()
            potential_sentence = text[:end_pos].strip()

            # Skip if too short
            if len(potential_sentence) < self.min_sentence_chars:
                continue

            # Check for false endings (abbreviations)
            if FALSE_ENDINGS.search(potential_sentence):
                continue

            # Check for list items (usually not complete sentences)
            if LIST_ITEM_PATTERN.match(potential_sentence):
                # Only skip if it's short (long list items are probably sentences)
                if len(potential_sentence) < 50:
                    continue

            # Valid sentence found - extract and update buffer
            self.text = text[end_pos:].lstrip()
            return potential_sentence

        return None

    def _force_flush_partial(self) -> Optional[str]:
        """
        Force flush part of the buffer when it gets too large.

        Tries to find a natural break point (comma, semicolon, etc.)
        """
        text = self.text

        # Try to find a natural break point
        break_patterns = [
            r',\s+',      # Comma followed by space
            r';\s+',      # Semicolon followed by space
            r':\s+',      # Colon followed by space
            r'\s+and\s+', # "and" conjunction
            r'\s+but\s+', # "but" conjunction
            r'\s+or\s+',  # "or" conjunction
        ]

        for pattern in break_patterns:
            matches = list(re.finditer(pattern, text))
            if matches:
                # Use the last match to get maximum content
                last_match = matches[-1]
                break_pos = last_match.start() + 1  # Include the comma/etc
                if break_pos > self.min_sentence_chars:
                    partial = text[:break_pos].strip()
                    self.text = text[break_pos:].lstrip()
                    self.sentences_yielded += 1
                    logger.debug("Forced partial flush: '%s'", partial[:50])
                    return partial

        # No good break point - flush everything
        partial = text.strip()
        self.text = ""
        if partial:
            self.sentences_yielded += 1
            logger.debug("Forced full flush: '%s'", partial[:50])
            return partial

        return None

    def flush(self) -> Optional[str]:
        """
        Get any remaining text in the buffer.

        Called when LLM stream ends to ensure all content is processed.
        """
        if self.text.strip():
            remaining = self.text.strip()
            self.text = ""
            self.sentences_yielded += 1
            logger.debug("Final flush: '%s'", remaining[:50] if remaining else "")
            return remaining
        return None

    def reset(self) -> None:
        """Reset buffer state for reuse."""
        self.text = ""
        self.sentences_yielded = 0
        self.in_code_block = False
        self.last_yield_time = time.time()


async def stream_sentences_to_tts(
    llm_stream: AsyncGenerator[str, None],
    tts_func: Callable[[str], AsyncGenerator[bytes, None]],
    on_llm_chunk: Optional[Callable[[str], Any]] = None,
    on_sentence: Optional[Callable[[str, int], Any]] = None,
    on_tts_chunk: Optional[Callable[[bytes, int], Any]] = None,
    check_interrupt: Optional[Callable[[], bool]] = None,
    min_sentence_chars: int = 15,
) -> AsyncGenerator[bytes, None]:
    """
    Stream LLM output sentence-by-sentence to TTS for lower latency.

    This function:
    1. Consumes LLM stream in background
    2. Detects sentence boundaries
    3. Queues sentences for TTS processing
    4. Yields audio chunks as they're generated

    TTS starts on the first complete sentence, not the full response.

    Args:
        llm_stream: Async generator yielding LLM text chunks
        tts_func: Function that takes text and returns async generator of audio bytes
        on_llm_chunk: Optional callback for each LLM chunk
        on_sentence: Optional callback when sentence detected (sentence, index)
        on_tts_chunk: Optional callback for each TTS chunk (audio, sentence_index)
        check_interrupt: Optional function returning True to stop streaming
        min_sentence_chars: Minimum characters for valid sentence

    Yields:
        TTS audio chunks (bytes) as sentences are processed
    """
    buffer = SentenceBuffer(min_sentence_chars=min_sentence_chars)
    sentence_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    full_response = ""
    llm_done = asyncio.Event()
    error_occurred: Optional[Exception] = None

    async def llm_consumer():
        """Consume LLM stream and queue sentences as they're detected."""
        nonlocal full_response, error_occurred

        try:
            async for chunk in llm_stream:
                # Check for interruption
                if check_interrupt and check_interrupt():
                    logger.info("LLM stream interrupted by user")
                    break

                full_response += chunk

                # Callback for raw chunks
                if on_llm_chunk:
                    try:
                        result = on_llm_chunk(chunk)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.warning("on_llm_chunk callback error: %s", e)

                # Check for complete sentence
                sentence = buffer.add(chunk)
                if sentence:
                    if on_sentence:
                        try:
                            result = on_sentence(sentence, buffer.sentences_yielded)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.warning("on_sentence callback error: %s", e)

                    await sentence_queue.put(sentence)

            # Flush remaining text
            remaining = buffer.flush()
            if remaining:
                if on_sentence:
                    try:
                        result = on_sentence(remaining, buffer.sentences_yielded)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.warning("on_sentence callback error: %s", e)

                await sentence_queue.put(remaining)

        except Exception as e:
            logger.exception("LLM consumer error: %s", e)
            error_occurred = e

        finally:
            # Signal end of sentences
            await sentence_queue.put(None)
            llm_done.set()

    async def tts_producer() -> AsyncGenerator[bytes, None]:
        """Process sentences from queue and yield TTS audio chunks."""
        sentence_index = 0

        while True:
            # Get next sentence from queue
            try:
                sentence = await sentence_queue.get()
            except asyncio.CancelledError:
                break

            if sentence is None:
                # End of sentences
                break

            # Check for interruption
            if check_interrupt and check_interrupt():
                logger.info("TTS producer interrupted")
                break

            logger.info(
                "TTS processing sentence %d: '%s'",
                sentence_index,
                sentence[:40] + "..." if len(sentence) > 40 else sentence
            )

            try:
                # Generate TTS for this sentence
                async for audio_chunk in tts_func(sentence):
                    # Check for interruption
                    if check_interrupt and check_interrupt():
                        logger.info("TTS interrupted mid-sentence")
                        break

                    # Callback for TTS chunks
                    if on_tts_chunk:
                        try:
                            result = on_tts_chunk(audio_chunk, sentence_index)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.warning("on_tts_chunk callback error: %s", e)

                    yield audio_chunk

            except Exception as e:
                logger.exception("TTS error for sentence %d: %s", sentence_index, e)
                # Continue with next sentence

            sentence_index += 1

    # Start LLM consumer in background
    llm_task = asyncio.create_task(llm_consumer())

    try:
        # Yield TTS chunks as they're produced
        async for audio_chunk in tts_producer():
            yield audio_chunk

    finally:
        # Ensure LLM task completes or is cancelled
        if not llm_task.done():
            llm_task.cancel()
            try:
                await llm_task
            except asyncio.CancelledError:
                pass

        # Re-raise any error from LLM consumer
        if error_occurred:
            raise error_occurred


class SentenceStreamingPipeline:
    """
    Convenience class for managing sentence-level LLM → TTS streaming.

    Tracks metrics and provides a cleaner interface for the main pipeline.
    """

    def __init__(
        self,
        tts_func: Callable[[str], AsyncGenerator[bytes, None]],
        min_sentence_chars: int = 15,
    ):
        """
        Initialize the pipeline.

        Args:
            tts_func: Function that converts text to audio stream
            min_sentence_chars: Minimum characters for valid sentence
        """
        self.tts_func = tts_func
        self.min_sentence_chars = min_sentence_chars

        # Metrics
        self.total_sentences = 0
        self.total_tts_chunks = 0
        self.full_response = ""
        self.first_sentence_time: Optional[float] = None
        self.first_audio_time: Optional[float] = None

    async def stream(
        self,
        llm_stream: AsyncGenerator[str, None],
        check_interrupt: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream LLM response through TTS with sentence-level chunking.

        Args:
            llm_stream: Async generator of LLM text chunks
            check_interrupt: Optional function to check for interruption

        Yields:
            TTS audio chunks
        """
        start_time = time.time()
        self.full_response = ""
        self.total_sentences = 0
        self.total_tts_chunks = 0
        self.first_sentence_time = None
        self.first_audio_time = None

        def on_llm_chunk(chunk: str):
            self.full_response += chunk

        def on_sentence(sentence: str, index: int):
            self.total_sentences = index
            if self.first_sentence_time is None:
                self.first_sentence_time = time.time()
                logger.info(
                    "[LATENCY] first_sentence: %.0fms",
                    (self.first_sentence_time - start_time) * 1000
                )

        def on_tts_chunk(chunk: bytes, sentence_index: int):
            self.total_tts_chunks += 1
            if self.first_audio_time is None:
                self.first_audio_time = time.time()
                logger.info(
                    "[LATENCY] first_audio: %.0fms (sentence streaming)",
                    (self.first_audio_time - start_time) * 1000
                )

        async for audio_chunk in stream_sentences_to_tts(
            llm_stream=llm_stream,
            tts_func=self.tts_func,
            on_llm_chunk=on_llm_chunk,
            on_sentence=on_sentence,
            on_tts_chunk=on_tts_chunk,
            check_interrupt=check_interrupt,
            min_sentence_chars=self.min_sentence_chars,
        ):
            yield audio_chunk

        total_time = time.time() - start_time
        logger.info(
            "Sentence streaming complete: %d sentences, %d chunks, %.0fms total",
            self.total_sentences,
            self.total_tts_chunks,
            total_time * 1000
        )

    def get_full_response(self) -> str:
        """Get the complete LLM response text."""
        return self.full_response

    def get_metrics(self) -> dict:
        """Get streaming metrics."""
        return {
            "total_sentences": self.total_sentences,
            "total_tts_chunks": self.total_tts_chunks,
            "first_sentence_time": self.first_sentence_time,
            "first_audio_time": self.first_audio_time,
            "response_length": len(self.full_response),
        }

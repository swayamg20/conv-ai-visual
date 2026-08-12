"""Audio-track consumption, Deepgram events, and Smart Turn confirmation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aiortc import MediaStreamTrack
from aiortc.mediastreams import AudioFrame

from murmur.core.config import config
from murmur.runtime import RuntimeRegistry, VoiceRuntimeSession
from murmur.voice.audio import audioframe_to_pcm16_bytes, stream_deepgram
from murmur.voice.smart_turn import SmartTurnAnalyzer, SmartTurnSession
from murmur.voice.turn_assembly import (
    DEFAULT_MAX_PENDING_AGE_SECONDS,
    DEFAULT_MAX_PENDING_CHARACTERS,
    DEFAULT_MAX_PENDING_SEGMENTS,
    TranscriptAccumulator,
)

logger = logging.getLogger(__name__)

AnalyzerProvider = Callable[[], SmartTurnAnalyzer | None]
ConfirmedTurnHandler = Callable[[str, str], Awaitable[None]]
DeepgramStreamer = Callable[..., Awaitable[None]]
ReadinessCheck = Callable[[str], Awaitable[Mapping[str, Any]]]
MonotonicClock = Callable[[], float]
WaitForChannel = Callable[[VoiceRuntimeSession, asyncio.Event], Awaitable[None]]

AUDIO_QUEUE_MAX_CHUNKS = 64
PENDING_TRANSCRIPT_MAX_SEGMENTS = DEFAULT_MAX_PENDING_SEGMENTS
PENDING_TRANSCRIPT_MAX_CHARACTERS = DEFAULT_MAX_PENDING_CHARACTERS
PENDING_TRANSCRIPT_MAX_AGE_SECS = DEFAULT_MAX_PENDING_AGE_SECONDS
LIFECYCLE_DELIVERY_TIMEOUT_SECS = 2.0


class VoiceReadinessError(RuntimeError):
    """A safe, client-reportable failure for one required voice component."""

    def __init__(self, component: str, message: str) -> None:
        super().__init__(message)
        self.component = component


@dataclass(slots=True)
class _DeliveryCoordinator:
    """Publish one pending Ready or terminal error when the channel becomes usable."""

    voice_session: VoiceRuntimeSession
    pending_payload: dict[str, Any] | None = None
    delivered_type: str | None = None
    ready_delivered: bool = False
    error_delivered: bool = False

    def stage_ready(self, payload: Mapping[str, Any]) -> None:
        if not self.ready_delivered and not self.error_delivered and self.pending_payload is None:
            self.pending_payload = dict(payload)

    def stage_error(self, payload: Mapping[str, Any]) -> None:
        if self.error_delivered:
            return
        # A terminal failure supersedes an undelivered Ready signal.
        if not self.error_delivered:
            self.pending_payload = dict(payload)

    def try_publish(self) -> bool:
        if self.pending_payload is None:
            return False
        channel = self.voice_session.datachannel
        if not channel or channel.readyState != "open":
            return False
        try:
            channel.send(json.dumps(self.pending_payload))
        except Exception:
            logger.exception("Failed to publish pending voice lifecycle event")
            return False
        self.delivered_type = str(self.pending_payload.get("type", ""))
        self.ready_delivered = self.ready_delivered or self.delivered_type == "ready"
        self.error_delivered = self.error_delivered or self.delivered_type == "error"
        self.pending_payload = None
        return True


async def _poll_for_open_channel(
    voice_session: VoiceRuntimeSession,
    channel_changed: asyncio.Event,
) -> None:
    """Wake the delivery loop when aiortc exposes an open channel."""
    while True:
        channel = voice_session.datachannel
        if channel and channel.readyState == "open":
            channel_changed.set()
            return
        peer_state = getattr(voice_session.peer, "connectionState", None)
        if peer_state in {"closed", "failed"}:
            return
        await asyncio.sleep(0.01)


class VoiceTranscriber:
    """Convert a peer audio track into confirmed user turns."""

    def __init__(
        self,
        runtime: RuntimeRegistry,
        *,
        analyzer_provider: AnalyzerProvider,
        confirmed_turn_handler: ConfirmedTurnHandler,
        deepgram_streamer: DeepgramStreamer = stream_deepgram,
        readiness_check: ReadinessCheck,
        clock: MonotonicClock = time.perf_counter,
        wait_for_channel: WaitForChannel = _poll_for_open_channel,
    ) -> None:
        self.runtime = runtime
        self.analyzer_provider = analyzer_provider
        self.confirmed_turn_handler = confirmed_turn_handler
        self.deepgram_streamer = deepgram_streamer
        self.readiness_check = readiness_check
        self.clock = clock
        self.wait_for_channel = wait_for_channel

    def _session(self, peer_id: str) -> VoiceRuntimeSession:
        session = self.runtime.get_voice(peer_id)
        if session is None:
            raise RuntimeError(f"Missing voice runtime for peer {peer_id}")
        return session

    async def consume(self, track: MediaStreamTrack, peer_id: str) -> None:
        """Stream audio to STT and dispatch only confirmed user turns."""
        logger.info("[%s] Audio consumer started", peer_id)
        voice_session = self._session(peer_id)
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        transcript_accumulator = TranscriptAccumulator(
            max_segments=PENDING_TRANSCRIPT_MAX_SEGMENTS,
            max_characters=PENDING_TRANSCRIPT_MAX_CHARACTERS,
            max_age_seconds=PENDING_TRANSCRIPT_MAX_AGE_SECS,
        )
        smart_turn: SmartTurnSession | None = None
        deepgram_task: asyncio.Task[None] | None = None
        channel_wait_task: asyncio.Task[None] | None = None
        delivery_task: asyncio.Task[None] | None = None
        pending_age_task: asyncio.Task[None] | None = None
        terminal_event = asyncio.Event()
        channel_changed = asyncio.Event()
        delivery = _DeliveryCoordinator(voice_session)
        provider_ready = False
        terminal_component: str | None = None
        active_receive_task: asyncio.Task[AudioFrame] | None = None

        def send_event(payload: Mapping[str, Any]) -> bool:
            channel = voice_session.datachannel
            if not channel or channel.readyState != "open":
                return False
            try:
                channel.send(json.dumps(dict(payload)))
            except Exception:
                logger.exception("[%s] Voice event delivery failed", peer_id)
                return False
            return True

        def ensure_channel_waiter() -> None:
            nonlocal channel_wait_task
            if channel_wait_task is None or channel_wait_task.done():
                channel_wait_task = asyncio.create_task(
                    self.wait_for_channel(voice_session, channel_changed)
                )

        async def deliver_pending() -> None:
            nonlocal provider_ready
            while not terminal_event.is_set() or delivery.pending_payload is not None:
                await channel_changed.wait()
                channel_changed.clear()
                if delivery.try_publish():
                    provider_ready = delivery.ready_delivered and not terminal_event.is_set()
                    if delivery.delivered_type == "error":
                        return

        async def publish_unavailable(component: str, message: str, *, code: str) -> None:
            nonlocal provider_ready, terminal_component
            if terminal_component is not None:
                return
            terminal_component = component
            provider_ready = False
            transcript_accumulator.clear()
            if smart_turn:
                smart_turn._reset_turn()
            delivery.stage_error(
                {
                    "type": "error",
                    "code": code,
                    "component": component,
                    "message": message,
                    "fallback": "text",
                    "recoverable": False,
                }
            )
            if delivery.try_publish():
                logger.warning("[%s] Voice unavailable (%s): %s", peer_id, component, message)
            else:
                logger.warning(
                    "[%s] Voice unavailable before event channel opened (%s): %s",
                    peer_id,
                    component,
                    message,
                )
                ensure_channel_waiter()
                channel_changed.set()
            terminal_event.set()

        async def publish_committed_turn(text: str) -> None:
            send_event({"type": "turn_committed", "text": text})
            await self.confirmed_turn_handler(peer_id, text)

        async def clear_missing_eot() -> None:
            await publish_unavailable(
                "stt",
                "Speech recognition did not receive a bounded end-of-turn signal. "
                "Continue in text mode and reconnect voice.",
                code="stt_eot_missing",
            )

        def schedule_pending_age_guard(expected_revision: int) -> asyncio.Task[None]:
            async def guard() -> None:
                deadline = transcript_accumulator.first_segment_at
                if deadline is None:
                    return
                deadline += PENDING_TRANSCRIPT_MAX_AGE_SECS
                while not terminal_event.is_set():
                    if (
                        transcript_accumulator.revision < expected_revision
                        or not transcript_accumulator.segments
                    ):
                        return
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        await clear_missing_eot()
                        return
                    await asyncio.sleep(min(remaining, 0.05))

            return asyncio.create_task(guard())

        async def commit_explicit_eot() -> None:
            nonlocal pending_age_task
            if not transcript_accumulator.mark_boundary():
                return
            text = transcript_accumulator.text()
            if not text:
                return
            if pending_age_task:
                pending_age_task.cancel()
                pending_age_task = None

            timing = voice_session.turn_timing
            if smart_turn:
                logger.info("[%s] Explicit EOT -> Smart Turn (text='%s')", peer_id, text[:60])
                smart_start = self.clock()
                is_complete, accumulated_text = await smart_turn.on_speech_final("")
                timing["turn_detection_ms"] = round((self.clock() - smart_start) * 1000, 2)
                timing["smart_turn_result"] = "complete" if is_complete else "incomplete"
                send_event(
                    {
                        "type": "smart_turn",
                        "is_complete": is_complete,
                        "latency_ms": timing["turn_detection_ms"],
                    }
                )
                if is_complete:
                    transcript_accumulator.clear(committed=True)
                    if accumulated_text:
                        await publish_committed_turn(accumulated_text)
                return

            transcript_accumulator.clear(committed=True)
            logger.info("[%s] Explicit EOT transcript: '%s'", peer_id, text)
            await publish_committed_turn(text)

        try:
            analyzer = await asyncio.to_thread(self.analyzer_provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[%s] Smart Turn initialization failed", peer_id)
            await publish_unavailable(
                "turn_detection",
                "Turn detection could not initialize. Continue in text mode and verify the "
                "Smart Turn dependency.",
                code="voice_unavailable",
            )
            analyzer = None
        if analyzer and not terminal_event.is_set():
            smart_turn = SmartTurnSession(analyzer)
            voice_session.smart_turn = smart_turn

            async def on_fallback_complete(text: str) -> None:
                voice_session.turn_timing["smart_turn_result"] = "fallback"
                transcript_accumulator.clear(committed=True)
                await publish_committed_turn(text)

            smart_turn.set_turn_complete_callback(on_fallback_complete)
            logger.info("[%s] Smart Turn session created", peer_id)

        delivery_task = asyncio.create_task(deliver_pending())
        channel_changed.set()

        async def on_deepgram_event(data: dict[str, Any]) -> None:
            nonlocal pending_age_task
            if terminal_event.is_set():
                return
            event_type_value = data.get("type")
            event_type = event_type_value.lower() if isinstance(event_type_value, str) else ""
            if event_type in ("error", "warning"):
                await publish_unavailable(
                    "stt",
                    "Speech recognition became unavailable. Continue in text mode and verify "
                    "the Deepgram credentials.",
                    code="voice_unavailable",
                )
                return
            if not provider_ready:
                return
            if event_type in ("utteranceend", "utterance_end"):
                if data.get("last_word_end") == -1:
                    return
                await commit_explicit_eot()
                return
            if event_type in ("speechstarted", "speech_started"):
                transcript_accumulator.note_speech_resumed()
                if smart_turn:
                    smart_turn._cancel_fallback()
                return
            if event_type != "results":
                return

            is_final = data.get("is_final") is True
            speech_final = data.get("speech_final") is True
            transcript = ""
            channel_obj = data.get("channel")
            if isinstance(channel_obj, Mapping):
                alternatives = channel_obj.get("alternatives")
                if isinstance(alternatives, list) and alternatives:
                    first_alternative = alternatives[0]
                    if isinstance(first_alternative, Mapping):
                        transcript_value = first_alternative.get("transcript")
                        if isinstance(transcript_value, str):
                            transcript = transcript_value

            normalized_transcript = transcript.strip()
            timing = voice_session.turn_timing
            if normalized_transcript and "first_transcript_ts" not in timing:
                timing["first_transcript_ts"] = self.clock()

            if normalized_transcript and voice_session.tts_active:
                logger.warning(
                    "[%s] User interrupted TTS: '%s'", peer_id, normalized_transcript[:30]
                )
                voice_session.tts_active = False
                transcript_accumulator.clear()
                if smart_turn:
                    smart_turn._reset_turn()
            send_event(
                {
                    "type": "transcript",
                    "text": transcript,
                    "is_final": is_final,
                    "speech_final": speech_final,
                }
            )

            if normalized_transcript and not (is_final or speech_final):
                transcript_accumulator.note_speech_resumed()
                if smart_turn:
                    smart_turn._cancel_fallback()

            if normalized_transcript and (is_final or speech_final):
                observed_at = self.clock()
                segment_added = transcript_accumulator.add_final(
                    data,
                    normalized_transcript,
                    observed_at=observed_at,
                )
                if segment_added:
                    timing["last_final_segment_ts"] = observed_at
                    if smart_turn:
                        smart_turn.accumulate_transcript(normalized_transcript)
                    if pending_age_task:
                        pending_age_task.cancel()
                    pending_age_task = schedule_pending_age_guard(transcript_accumulator.revision)
                    if transcript_accumulator.exceeded_limits(observed_at):
                        await clear_missing_eot()
                        return

            if speech_final:
                await commit_explicit_eot()

        try:
            if terminal_event.is_set():
                return
            first_frame: AudioFrame = await track.recv()
            sample_rate = getattr(first_frame, "sample_rate", 48000)
            channel_count = self._channel_count(first_frame)
            logger.info("[%s] Audio: %sHz, %dch", peer_id, sample_rate, channel_count)

            endpointing = 1000 if smart_turn else config.DEEPGRAM_ENDPOINTING
            utterance_end = 1000 if smart_turn else config.DEEPGRAM_UTTERANCE_END_MS
            websocket_url = (
                f"wss://api.deepgram.com/v1/listen?model={config.DEEPGRAM_MODEL}"
                f"&encoding=linear16&sample_rate={sample_rate}&channels={channel_count}"
                f"&interim_results=true&endpointing={endpointing}"
                f"&utterance_end_ms={utterance_end}&vad_events=true"
                "&smart_format=false&punctuate=false&diarize=false"
            )

            async def on_deepgram_connected() -> None:
                nonlocal provider_ready
                try:
                    checks = await self.readiness_check(peer_id)
                except VoiceReadinessError as exc:
                    await publish_unavailable(exc.component, str(exc), code="voice_unavailable")
                    return
                except Exception:
                    logger.exception("[%s] Voice readiness check failed", peer_id)
                    await publish_unavailable(
                        "runtime",
                        "Voice dependencies could not be verified. Continue in text mode and "
                        "check the server provider configuration.",
                        code="voice_unavailable",
                    )
                    return

                delivery.stage_ready(
                    {
                        "type": "ready",
                        "profile": "legacy",
                        "checks": {
                            "event_channel": {"state": "ready"},
                            "stt": {"provider": "deepgram", "state": "ready"},
                            **dict(checks),
                        },
                    }
                )
                if delivery.try_publish():
                    provider_ready = True
                    logger.info("[%s] Sent verified ready signal to client", peer_id)
                else:
                    ensure_channel_waiter()
                    channel_changed.set()

            deepgram_task = asyncio.create_task(
                self.deepgram_streamer(
                    websocket_url,
                    config.DEEPGRAM_KEY,
                    audio_queue,
                    on_deepgram_event,
                    on_connected=on_deepgram_connected,
                )
            )

            first_pcm = audioframe_to_pcm16_bytes(first_frame)
            await audio_queue.put(first_pcm)
            if smart_turn:
                smart_turn.feed_audio(first_pcm, sample_rate, channel_count)

            terminal_wait = asyncio.create_task(terminal_event.wait())
            try:
                while not terminal_event.is_set():
                    active_receive_task = asyncio.create_task(track.recv())
                    done, _ = await asyncio.wait(
                        {active_receive_task, deepgram_task, terminal_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if terminal_wait in done:
                        active_receive_task.cancel()
                        await asyncio.gather(active_receive_task, return_exceptions=True)
                        active_receive_task = None
                        break
                    if deepgram_task in done:
                        active_receive_task.cancel()
                        await asyncio.gather(active_receive_task, return_exceptions=True)
                        active_receive_task = None
                        try:
                            await deepgram_task
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception("[%s] Deepgram streaming task failed", peer_id)
                        await publish_unavailable(
                            "stt",
                            "Speech recognition disconnected. Continue in text mode and retry voice.",
                            code="voice_unavailable",
                        )
                        break
                    frame = active_receive_task.result()
                    active_receive_task = None
                    pcm_bytes = audioframe_to_pcm16_bytes(frame)
                    await audio_queue.put(pcm_bytes)
                    if smart_turn:
                        smart_turn.feed_audio(pcm_bytes, sample_rate, channel_count)
            finally:
                terminal_wait.cancel()
                await asyncio.gather(terminal_wait, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("[%s] Audio consumer cancelled", peer_id)
            raise
        except Exception as exc:
            logger.exception("[%s] Audio consumer failed: %s", peer_id, exc)
            await publish_unavailable(
                "audio",
                "The microphone audio stream failed. Continue in text mode and reconnect voice.",
                code="voice_unavailable",
            )
        finally:
            terminal_event.set()
            if active_receive_task:
                active_receive_task.cancel()
                await asyncio.gather(active_receive_task, return_exceptions=True)
            if pending_age_task:
                pending_age_task.cancel()
            if deepgram_task and not deepgram_task.done():
                deepgram_task.cancel()
            if audio_queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    audio_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                audio_queue.put_nowait(None)
            if deepgram_task:
                await asyncio.gather(deepgram_task, return_exceptions=True)
            channel_changed.set()
            if (
                delivery_task
                and delivery.pending_payload is not None
                and delivery.pending_payload.get("type") == "error"
            ):
                ensure_channel_waiter()
                if channel_wait_task:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(channel_wait_task),
                            timeout=LIFECYCLE_DELIVERY_TIMEOUT_SECS,
                        )
                    except TimeoutError:
                        channel_wait_task.cancel()
                        await asyncio.gather(channel_wait_task, return_exceptions=True)
                delivery.try_publish()
                provider_ready = False
            elif delivery_task:
                delivery_task.cancel()
            if delivery_task:
                delivery_task.cancel()
                await asyncio.gather(delivery_task, return_exceptions=True)
            if channel_wait_task:
                channel_wait_task.cancel()
                await asyncio.gather(channel_wait_task, return_exceptions=True)
            if pending_age_task:
                await asyncio.gather(pending_age_task, return_exceptions=True)
            if smart_turn:
                smart_turn.cleanup()
                if voice_session.smart_turn is smart_turn:
                    voice_session.smart_turn = None
            logger.info("[%s] Audio consumer finished", peer_id)

    @staticmethod
    def _channel_count(frame: AudioFrame) -> int:
        channel_count: Any = None
        layout = getattr(frame, "layout", None)
        if layout is not None:
            channel_count = getattr(layout, "channels", None)
        if channel_count is None:
            channel_count = getattr(frame, "channels", None)
        if isinstance(channel_count, Sequence) and not isinstance(channel_count, (str, bytes)):
            channel_count = len(channel_count)
        if isinstance(channel_count, int):
            return channel_count

        samples_array = frame.to_ndarray()
        return 1 if samples_array.ndim == 1 else samples_array.shape[-1]

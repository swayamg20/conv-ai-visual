"""Audio-track consumption, Deepgram events, and Smart Turn confirmation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from aiortc import MediaStreamTrack
from aiortc.mediastreams import AudioFrame

from murmur.core.config import config
from murmur.runtime import RuntimeRegistry, VoiceRuntimeSession
from murmur.voice.audio import audioframe_to_pcm16_bytes, stream_deepgram
from murmur.voice.smart_turn import SmartTurnAnalyzer, SmartTurnSession

logger = logging.getLogger(__name__)

AnalyzerProvider = Callable[[], SmartTurnAnalyzer | None]
ConfirmedTurnHandler = Callable[[str, str], Awaitable[None]]
DeepgramStreamer = Callable[..., Awaitable[None]]


class VoiceTranscriber:
    """Convert a peer audio track into confirmed user turns."""

    def __init__(
        self,
        runtime: RuntimeRegistry,
        *,
        analyzer_provider: AnalyzerProvider,
        confirmed_turn_handler: ConfirmedTurnHandler,
        deepgram_streamer: DeepgramStreamer = stream_deepgram,
        watchdog_timeout_seconds: float = 3.0,
    ) -> None:
        self.runtime = runtime
        self.analyzer_provider = analyzer_provider
        self.confirmed_turn_handler = confirmed_turn_handler
        self.deepgram_streamer = deepgram_streamer
        self.watchdog_timeout_seconds = watchdog_timeout_seconds

    def _session(self, peer_id: str) -> VoiceRuntimeSession:
        session = self.runtime.get_voice(peer_id)
        if session is None:
            raise RuntimeError(f"Missing voice runtime for peer {peer_id}")
        return session

    async def consume(self, track: MediaStreamTrack, peer_id: str) -> None:
        """Stream audio to STT and dispatch only confirmed user turns."""
        logger.info("[%s] Audio consumer started", peer_id)
        voice_session = self._session(peer_id)
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        smart_turn: SmartTurnSession | None = None
        watchdog_task: asyncio.Task[None] | None = None
        deepgram_task: asyncio.Task[None] | None = None

        analyzer = self.analyzer_provider()
        if analyzer:
            smart_turn = SmartTurnSession(analyzer)
            voice_session.smart_turn = smart_turn

            async def on_fallback_complete(text: str) -> None:
                voice_session.turn_timing["smart_turn_result"] = "fallback"
                await self.confirmed_turn_handler(peer_id, text)

            smart_turn.set_turn_complete_callback(on_fallback_complete)
            logger.info("[%s] Smart Turn session created", peer_id)

        async def on_deepgram_event(data: dict[str, Any]) -> None:
            nonlocal watchdog_task
            event_type = data.get("type")
            if event_type not in ("Results", "results"):
                return

            channel_obj = data.get("channel", {})
            alternatives = channel_obj.get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "")
            is_final = data.get("is_final", False)
            speech_final = data.get("speech_final", False)
            if transcript.strip() and (is_final or speech_final):
                logger.debug(
                    "[%s] Deepgram event: is_final=%s speech_final=%s text='%s'",
                    peer_id,
                    is_final,
                    speech_final,
                    transcript[:40],
                )

            channel = voice_session.datachannel
            tts_was_active = voice_session.tts_active
            timing = voice_session.turn_timing
            if transcript.strip() and "speech_start_ts" not in timing:
                timing["speech_start_ts"] = time.perf_counter()

            if channel and channel.readyState == "open":
                if transcript.strip() and tts_was_active:
                    logger.warning(
                        "[%s] User interrupted TTS: '%s'",
                        peer_id,
                        transcript[:30],
                    )
                    voice_session.tts_active = False
                    if smart_turn:
                        smart_turn._reset_turn()
                channel.send(
                    json.dumps(
                        {
                            "type": "transcript",
                            "text": transcript,
                            "is_final": is_final,
                            "speech_final": speech_final,
                        }
                    )
                )

            if smart_turn:
                if is_final and transcript.strip():
                    smart_turn.accumulate_transcript(transcript)
                    timing["stt_final_ts"] = time.perf_counter()
                    if watchdog_task is not None:
                        watchdog_task.cancel()

                    async def force_turn_after_timeout() -> None:
                        try:
                            await asyncio.sleep(self.watchdog_timeout_seconds)
                            text = smart_turn.accumulated_transcript.strip()
                            if text:
                                logger.warning(
                                    "[%s] No speech_final event; forcing turn: '%s'",
                                    peer_id,
                                    text[:60],
                                )
                                timing["smart_turn_result"] = "watchdog"
                                smart_turn._reset_turn()
                                await self.confirmed_turn_handler(peer_id, text)
                        except asyncio.CancelledError:
                            raise

                    watchdog_task = asyncio.create_task(force_turn_after_timeout())

                if speech_final and smart_turn.accumulated_transcript.strip():
                    if watchdog_task is not None:
                        watchdog_task.cancel()
                        watchdog_task = None

                    logger.info(
                        "[%s] Speech final -> Smart Turn (text='%s')",
                        peer_id,
                        smart_turn.accumulated_transcript[:60],
                    )
                    smart_start = time.perf_counter()
                    is_complete, accumulated_text = await smart_turn.on_speech_final("")
                    timing["turn_detection_ms"] = round(
                        (time.perf_counter() - smart_start) * 1000,
                        2,
                    )
                    timing["smart_turn_result"] = "complete" if is_complete else "incomplete"

                    if channel and channel.readyState == "open":
                        channel.send(
                            json.dumps(
                                {
                                    "type": "smart_turn",
                                    "is_complete": is_complete,
                                    "latency_ms": timing["turn_detection_ms"],
                                }
                            )
                        )
                    if is_complete and accumulated_text:
                        await self.confirmed_turn_handler(peer_id, accumulated_text)
            elif (is_final or speech_final) and transcript.strip():
                logger.info("[%s] Final transcript: '%s'", peer_id, transcript)
                timing["stt_final_ts"] = time.perf_counter()
                await self.confirmed_turn_handler(peer_id, transcript)

        try:
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
                f"&utterance_end_ms={utterance_end}"
                "&smart_format=false&punctuate=false&diarize=false"
            )

            async def on_deepgram_connected() -> None:
                channel = voice_session.datachannel
                if channel and channel.readyState == "open":
                    channel.send(json.dumps({"type": "ready"}))
                    logger.info("[%s] Sent ready signal to client", peer_id)

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

            while True:
                frame = await track.recv()
                pcm_bytes = audioframe_to_pcm16_bytes(frame)
                await audio_queue.put(pcm_bytes)
                if smart_turn:
                    smart_turn.feed_audio(pcm_bytes, sample_rate, channel_count)
        except asyncio.CancelledError:
            logger.info("[%s] Audio consumer cancelled", peer_id)
            raise
        except Exception as exc:
            logger.exception("[%s] Audio consumer failed: %s", peer_id, exc)
        finally:
            await audio_queue.put(None)
            if watchdog_task:
                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)
            if deepgram_task:
                try:
                    await asyncio.wait_for(deepgram_task, timeout=2.0)
                except Exception:
                    deepgram_task.cancel()
                    await asyncio.gather(deepgram_task, return_exceptions=True)
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

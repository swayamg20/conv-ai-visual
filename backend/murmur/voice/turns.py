"""Confirmed-turn LLM, visual synchronization, TTS, and metrics orchestration."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from typing import Any, Protocol

from murmur.core.config import config
from murmur.llm.routing import route_model
from murmur.persistence.repositories.observability import (
    TTSResilienceLogRepo,
    VoicePipelineLogRepo,
)
from murmur.runtime import RuntimeRegistry, VoiceRuntimeSession
from murmur.voice.pipeline import VoicePipelineFactory
from murmur.voice.synthesis import SpeechSynthesizer

logger = logging.getLogger(__name__)


class VoiceTurnContext(Protocol):
    runtime: RuntimeRegistry
    pipeline_factory: VoicePipelineFactory
    synthesizer: SpeechSynthesizer


def _get_voice_session(service: VoiceTurnContext, peer_id: str) -> VoiceRuntimeSession:
    voice_session = service.runtime.get_voice(peer_id)
    if voice_session is None:
        raise RuntimeError(f"Missing voice runtime for peer {peer_id}")
    return voice_session


def _touch_voice_session(service: VoiceTurnContext, peer_id: str) -> None:
    service.runtime.touch_voice(peer_id)


async def cancel_turn(service: VoiceTurnContext, pc_id: str) -> None:
    """Cancel any in-flight LLM+TTS processing for this peer."""
    voice_session = service.runtime.get_voice(pc_id)
    if voice_session is None:
        return
    prev = voice_session.turn_task
    voice_session.turn_task = None
    if prev and not prev.done():
        logger.info("[%s] Cancelling previous turn processing", pc_id)
        voice_session.tts_active = False
        prev.cancel()
        try:
            await prev
        except (asyncio.CancelledError, Exception):
            pass


async def schedule_turn(service: VoiceTurnContext, pc_id: str, user_text: str) -> None:
    """
    Schedule LLM → TTS pipeline for a confirmed user turn.
    Cancels any previous in-flight turn for this peer first.
    """
    voice_session = _get_voice_session(service, pc_id)
    _touch_voice_session(service, pc_id)
    # Stamp turn-confirmed time
    timing = voice_session.turn_timing
    timing["turn_confirmed_ts"] = time.perf_counter()
    await cancel_turn(service, pc_id)
    task = asyncio.create_task(_run_turn(service, pc_id, user_text))
    voice_session.turn_task = task


def _split_sentence(buf: str) -> tuple[str | None, str]:
    """Split buffer at the first sentence boundary, returning (sentence, remainder).
    Returns (None, buf) if no boundary found yet."""
    # Match sentence-ending punctuation followed by a space, newline, or end-of-string
    m = re.search(r"[.!?](?:\s|$)", buf)
    if m:
        idx = m.end()
        return buf[:idx].strip(), buf[idx:]
    return None, buf


async def _run_sdl_steps(
    service: VoiceTurnContext,
    pc_id: str,
    sdl: dict[str, Any],
) -> None:
    """
    Step-pipelined SDL: stream per-step TTS + visual commands.

    For each SDL step, sends the visual commands and TTS audio together,
    creating a 'person drawing while talking' effect.
    """
    voice_session = _get_voice_session(service, pc_id)
    ch = voice_session.datachannel
    steps = sdl.get("steps", [])
    seq_id = f"seq_{uuid.uuid4().hex[:8]}"

    if ch and ch.readyState == "open":
        ch.send(
            json.dumps(
                {
                    "type": "sdl_start",
                    "sequence_id": seq_id,
                    "total_steps": len(steps),
                    "sdl": sdl,
                }
            )
        )

    voice_session.tts_active = True

    for step_idx, step in enumerate(steps):
        if not voice_session.tts_active:
            break  # interrupted

        say_text = step.get("say", "").strip()

        # Generate TTS for this step's say text
        if say_text and service.synthesizer.available():
            total_audio_bytes = 0

            async def emit_step_audio(
                audio_chunk: bytes,
                step_index: int = step_idx,
            ) -> None:
                nonlocal total_audio_bytes
                if not voice_session.tts_active:
                    raise asyncio.CancelledError("TTS interrupted")

                # Send sdl_step on first chunk so frontend starts animation with audio
                if total_audio_bytes == 0 and ch and ch.readyState == "open":
                    ch.send(
                        json.dumps(
                            {
                                "type": "sdl_step",
                                "sequence_id": seq_id,
                                "step_index": step_index,
                            }
                        )
                    )

                total_audio_bytes += len(audio_chunk)
                if ch and ch.readyState == "open":
                    ch.send(
                        json.dumps(
                            {
                                "type": "tts_chunk",
                                "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                                "sequence_id": seq_id,
                                "step_index": step_index,
                            }
                        )
                    )

            try:
                await service.synthesizer.stream(say_text, emit_step_audio)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[%s] SDL step %d TTS failed: %s", pc_id, step_idx, exc)

            # Audio duration from PCM byte count: 16-bit (2 bytes) @ 16kHz
            audio_duration_ms = round(total_audio_bytes / 32) if total_audio_bytes > 0 else 0
            if ch and ch.readyState == "open":
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_step_complete",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                            "audio_duration_ms": audio_duration_ms,
                        }
                    )
                )
        else:
            # No speech for this step — send sdl_step and complete immediately
            if ch and ch.readyState == "open":
                ch.send(
                    json.dumps(
                        {
                            "type": "sdl_step",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                        }
                    )
                )
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_step_complete",
                            "sequence_id": seq_id,
                            "step_index": step_idx,
                            "audio_duration_ms": 0,
                        }
                    )
                )

    # Sequence complete
    voice_session.tts_active = False
    if ch and ch.readyState == "open":
        ch.send(
            json.dumps(
                {
                    "type": "sdl_complete",
                    "sequence_id": seq_id,
                }
            )
        )


async def _run_turn(service: VoiceTurnContext, pc_id: str, user_text: str) -> None:
    """
    LLM → TTS pipeline with sentence-pipelined audio.

    Key optimisation: TTS starts on the FIRST complete sentence from the LLM
    text stream, rather than waiting for the full response. This cuts perceived
    latency roughly in half.

    Drawings (tool calls) are dispatched during the LLM tool-calling rounds,
    which happen BEFORE text streaming, so the user sees whiteboard activity
    even while waiting for audio.

    If the LLM calls teach_with_visuals (SDL), after text TTS completes,
    the SDL steps are streamed with per-step TTS for voice-visual sync.
    """
    voice_session = _get_voice_session(service, pc_id)
    ch = voice_session.datachannel
    pipeline = service.pipeline_factory.get_or_create(pc_id)

    # Model routing: fast model by default, escalate for complex queries
    routed_provider, routed_key, routed_model = route_model(user_text)
    pipeline.switch_provider(routed_provider, routed_key, routed_model)

    # Clear any stale SDL from previous turns
    voice_session.pending_sdl = None

    # Grab timing context from STT/turn detection phase
    timing = dict(voice_session.turn_timing)
    voice_session.turn_timing.clear()
    t_first_transcript = timing.get("first_transcript_ts") or timing.get("speech_start_ts")
    t_last_final_segment = timing.get("last_final_segment_ts") or timing.get("stt_final_ts")
    t_turn_confirmed = timing.get("turn_confirmed_ts", time.perf_counter())
    smart_turn_result = timing.get("smart_turn_result")
    t_turn_detection = timing.get("turn_detection_ms")

    t_llm_start = None
    t_llm_end = None
    t_tts_start = None
    t_tts_first_chunk = None
    t_tts_end = None
    tts_chunks_sent = 0
    tts_interrupted = False
    tts_retry_count = 0
    tts_fallback_used = False
    tts_fallback_provider = None
    tts_providers_used: list[str] = []
    llm_response = ""
    error_msg = None

    # Sentence queue: LLM text stream → sentence buffer → TTS tasks
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_task: asyncio.Task[None] | None = None

    async def _tts_sender():
        """Consume sentences from the queue and stream TTS audio to client."""
        nonlocal tts_chunks_sent
        nonlocal tts_fallback_provider
        nonlocal tts_fallback_used
        nonlocal tts_interrupted
        nonlocal tts_retry_count
        nonlocal t_tts_first_chunk
        nonlocal t_tts_end
        nonlocal t_tts_start
        nonlocal error_msg

        tts_started_sent = False

        async def _emit_audio_chunk(audio_chunk: bytes) -> None:
            nonlocal tts_chunks_sent, t_tts_first_chunk, tts_interrupted
            if not voice_session.tts_active:
                tts_interrupted = True
                raise asyncio.CancelledError("TTS interrupted")

            if ch and ch.readyState == "open":
                if tts_chunks_sent == 0:
                    t_tts_first_chunk = time.perf_counter()
                    logger.info("[%s] First TTS chunk (%d bytes)", pc_id, len(audio_chunk))
                ch.send(
                    json.dumps(
                        {
                            "type": "tts_chunk",
                            "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                        }
                    )
                )
                tts_chunks_sent += 1

        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                break  # poison pill — no more sentences

            if not sentence.strip():
                continue

            if not service.synthesizer.available():
                continue

            if not tts_started_sent:
                t_tts_start = time.perf_counter()
                voice_session.tts_active = True
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({"type": "tts_started"}))
                tts_started_sent = True

            try:
                sentence_meta = await service.synthesizer.stream(sentence, _emit_audio_chunk)
                tts_retry_count += sentence_meta.retry_count
                if sentence_meta.fallback_used:
                    tts_fallback_used = True
                    tts_fallback_provider = sentence_meta.fallback_provider or "kokoro"
                provider_used = sentence_meta.provider_used
                if provider_used and provider_used not in tts_providers_used:
                    tts_providers_used.append(provider_used)
            except asyncio.CancelledError:
                tts_interrupted = True
                logger.warning("[%s] TTS interrupted after %d chunks", pc_id, tts_chunks_sent)
                if ch and ch.readyState == "open":
                    ch.send(json.dumps({"type": "tts_interrupted", "chunks_sent": tts_chunks_sent}))
                return
            except Exception as tts_err:
                logger.exception("[%s] TTS error on sentence: %s", pc_id, tts_err)
                error_msg = error_msg or str(tts_err)

        # All sentences processed
        t_tts_end = time.perf_counter()
        voice_session.tts_active = False
        if tts_started_sent and not tts_interrupted:
            logger.info("[%s] TTS complete (%d chunks)", pc_id, tts_chunks_sent)
            if ch and ch.readyState == "open":
                ch.send(json.dumps({"type": "tts_complete"}))

    try:
        logger.info("[%s] → LLM: '%s'", pc_id, user_text[:60])
        t_llm_start = time.perf_counter()

        # Start TTS sender task — it blocks on the queue until sentences arrive
        tts_task = asyncio.create_task(_tts_sender())

        sentence_buf = ""
        async for chunk in pipeline.chat_with_tools_stream(
            user_text,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
        ):
            llm_response += chunk
            sentence_buf += chunk

            # Try to extract complete sentences and push them to TTS immediately
            while True:
                sentence, sentence_buf = _split_sentence(sentence_buf)
                if sentence is None:
                    break
                await sentence_queue.put(sentence)

        t_llm_end = time.perf_counter()
        logger.info("[%s] ← LLM (%d chars)", pc_id, len(llm_response))

        # Flush remaining text to TTS
        if sentence_buf.strip():
            await sentence_queue.put(sentence_buf.strip())

        # Signal TTS sender to finish
        await sentence_queue.put(None)

        # Send full response to client (for display)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "llm_response", "text": llm_response}))

        # Wait for sentence TTS to finish
        await tts_task

        # If SDL was captured during tool execution, run step-pipelined sync
        pending_sdl = voice_session.pending_sdl
        voice_session.pending_sdl = None
        if pending_sdl and pending_sdl.get("steps"):
            logger.info(
                "[%s] Starting SDL step pipeline (%d steps)", pc_id, len(pending_sdl["steps"])
            )
            await _run_sdl_steps(service, pc_id, pending_sdl)

    except asyncio.CancelledError:
        logger.info("[%s] Turn processing cancelled (new turn arrived)", pc_id)
        tts_interrupted = True
        voice_session.tts_active = False
        # Kill TTS sender
        sentence_queue.put_nowait(None)
        ch = voice_session.datachannel
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "tts_interrupted", "reason": "new_turn"}))
    except Exception as e:
        error_msg = str(e)
        logger.exception("[%s] Pipeline error: %s", pc_id, e)
        sentence_queue.put_nowait(None)
        if ch and ch.readyState == "open":
            ch.send(json.dumps({"type": "error", "message": error_msg}))
    finally:
        t_end = time.perf_counter()
        if tts_task and not tts_task.done():
            tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
        if voice_session.turn_task is asyncio.current_task():
            voice_session.turn_task = None
        voice_session.tts_active = False

        # ── Compute all latencies ──
        def _ms(start, end):
            if start is not None and end is not None:
                return round((end - start) * 1000, 2)
            return None

        llm_metrics = pipeline.get_last_call_metrics() or {}

        latency_vad_ms = None
        segment_finalization_ms = (
            _ms(t_first_transcript, t_last_final_segment)
            if t_first_transcript and t_last_final_segment
            else None
        )
        turn_commit_to_llm_start_ms = _ms(t_turn_confirmed, t_llm_start)
        latency_llm_ms = llm_metrics.get("latency_llm_ms") or _ms(t_llm_start, t_llm_end)
        latency_llm_first_token_ms = llm_metrics.get("latency_llm_first_token_ms")
        latency_tool_ms = llm_metrics.get("latency_tool_ms")
        latency_tts_ms = _ms(t_tts_start, t_tts_end)
        backend_tts_start_to_first_chunk_ms = _ms(t_tts_start, t_tts_first_chunk)
        legacy_backend_interval_ms = _ms(t_first_transcript or t_turn_confirmed, t_end)
        turn_detection_ms = t_turn_detection

        metrics_payload = {
            "segment_finalization_ms": segment_finalization_ms,
            "turn_detection_ms": turn_detection_ms,
            "turn_commit_to_llm_start_ms": turn_commit_to_llm_start_ms,
            "latency_llm_ms": latency_llm_ms,
            "latency_llm_first_token_ms": latency_llm_first_token_ms,
            "latency_context_ms": llm_metrics.get("latency_context_ms"),
            "latency_tools_schema_ms": llm_metrics.get("latency_tools_schema_ms"),
            "latency_tool_ms": latency_tool_ms,
            "backend_tts_generation_ms": latency_tts_ms,
            "backend_tts_start_to_first_chunk_ms": backend_tts_start_to_first_chunk_ms,
            "legacy_backend_interval_ms": legacy_backend_interval_ms,
            "acoustic_speech_end_to_commit_ms": None,
            "acoustic_speech_end_to_first_audible_ms": None,
            "interruption_to_local_silence_ms": None,
            "measurement_limitations": [
                "No acoustic speech-boundary timestamp is available in the legacy path",
                "Backend TTS chunks do not prove browser playout",
                "The legacy backend interval is not end-to-end latency",
            ],
            "tts_chunks_sent": tts_chunks_sent,
            "tts_interrupted": tts_interrupted,
            "tts_provider_used": ",".join(tts_providers_used) if tts_providers_used else None,
            "tts_retry_count": tts_retry_count,
            "tts_fallback_used": tts_fallback_used,
            "tts_fallback_provider": tts_fallback_provider,
            "smart_turn_result": smart_turn_result,
        }

        # Send metrics to client
        ch = voice_session.datachannel
        if ch and ch.readyState == "open":
            try:
                ch.send(json.dumps({"type": "pipeline_metrics", **metrics_payload}))
            except Exception:
                pass

        # Log to console
        logger.info(
            "[%s] METRICS: segment_finalization=%s turn=%s llm=%s tool=%s backend_tts=%s backend_tts_ttfb=%s legacy_interval=%s interrupted=%s provider=%s retries=%s fallback=%s",
            pc_id,
            segment_finalization_ms,
            turn_detection_ms,
            latency_llm_ms,
            latency_tool_ms,
            latency_tts_ms,
            backend_tts_start_to_first_chunk_ms,
            legacy_backend_interval_ms,
            tts_interrupted,
            metrics_payload["tts_provider_used"],
            tts_retry_count,
            tts_fallback_used,
        )

        # ── Save to DB ──
        try:
            user_id = voice_session.user_id
            log_record = VoicePipelineLogRepo.save(
                session_id=getattr(pipeline, "session_id", pc_id),
                user_id=user_id,
                mode="voice",
                user_message=user_text,
                response_text=(llm_response[:2000] if llm_response else None),
                llm_provider=pipeline.provider,
                llm_model=getattr(pipeline.client, "model", pipeline.provider),
                latency_vad_ms=latency_vad_ms,
                # These legacy database columns predate named Voice V2 spans.
                # Preserve their historical calculations, but the UI labels
                # them explicitly and never presents them as acoustic/E2E.
                latency_stt_ms=segment_finalization_ms,
                latency_turn_detection_ms=turn_detection_ms,
                latency_stt_to_llm_ms=turn_commit_to_llm_start_ms,
                latency_llm_ms=latency_llm_ms,
                latency_llm_first_token_ms=latency_llm_first_token_ms,
                latency_tool_ms=latency_tool_ms,
                latency_tts_ms=latency_tts_ms,
                latency_tts_first_chunk_ms=backend_tts_start_to_first_chunk_ms,
                latency_total_ms=legacy_backend_interval_ms,
                tool_calls_json=json.dumps(llm_metrics.get("tool_calls", []))
                if llm_metrics.get("tool_calls")
                else None,
                tokens_in=llm_metrics.get("tokens_in"),
                tokens_out=llm_metrics.get("tokens_out"),
                tts_chunks_sent=tts_chunks_sent,
                tts_interrupted=tts_interrupted,
                smart_turn_used=smart_turn_result is not None,
                smart_turn_result=smart_turn_result,
                error=error_msg,
            )
            TTSResilienceLogRepo.save(
                voice_log_id=log_record.id,
                provider_used=metrics_payload["tts_provider_used"],
                retry_count=tts_retry_count,
                fallback_used=tts_fallback_used,
                fallback_provider=tts_fallback_provider,
                final_error=error_msg,
            )
        except Exception as log_err:
            logger.warning("[%s] Failed to save voice pipeline log: %s", pc_id, log_err)

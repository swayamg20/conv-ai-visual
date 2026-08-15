"""One-call Pipecat 1.7 pipeline ownership and canonical RTVI events."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, WorkerParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies, UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from murmur.voice.contracts import EventEnvelope, EventType
from murmur.voice.profile import VoiceProfileScope
from murmur.voice.provider_profiles.pipecat_cascade import PreparedPipecatProfile
from murmur.voice.runtime_contracts import VoiceCallClaims

_CORE_READY_COMPONENTS = ("worker", "input", "output", "event_channel")
_DEFAULT_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS = 300.0
_MAX_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS = 900.0
_ACTIVE_CALL_IDLE_FRAMES = (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    BotStartedSpeakingFrame,
    BotSpeakingFrame,
    BotStoppedSpeakingFrame,
)


class PipecatRuntimeError(RuntimeError):
    """The owned pipeline could not preserve its lifecycle contract."""


def _canonical_smallwebrtc_connection_id(raw_pc_id: object) -> str | None:
    """Project an SDK peer ID to a stable nonsecret canonical identifier."""

    if not isinstance(raw_pc_id, str) or not raw_pc_id:
        return None
    digest = hashlib.sha256(raw_pc_id.encode("utf-8")).hexdigest()
    return f"smallwebrtc-{digest}"


def _validate_active_call_idle_timeout(seconds: float) -> None:
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not 0 < seconds <= _MAX_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS
    ):
        raise ValueError("Pipecat active-call idle timeout must be between zero and 900 seconds")


class PipecatProfileProvider(Protocol):
    async def prepare(self, scope: VoiceProfileScope) -> PreparedPipecatProfile: ...


class PipecatPeerConnection(Protocol):
    """Structural connection boundary supplied and retained by signaling."""

    @property
    def pc_id(self) -> str: ...

    def add_event_handler(
        self,
        event_name: str,
        handler: Callable[..., Awaitable[None]],
    ) -> None: ...

    async def disconnect(self) -> None: ...


PipecatScopeFactory = Callable[[VoiceCallClaims], VoiceProfileScope | Awaitable[VoiceProfileScope]]
PipecatTransportFactory = Callable[[PipecatPeerConnection, PreparedPipecatProfile], BaseTransport]


class GatedRTVIProcessor(RTVIProcessor):
    """Delay Pipecat's automatic bot-ready until Murmur canonical Ready."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ready_release = False
        self._ready_requested = False
        self._ready_about: Mapping[str, Any] | None = None
        self._ready_lock = asyncio.Lock()
        self._ready_published = False
        self._ready_failed = False

    async def set_bot_ready(self, about: Mapping[str, Any] | None = None) -> None:
        async with self._ready_lock:
            self._ready_requested = True
            self._ready_about = about
            if self._ready_release and not self._ready_published and not self._ready_failed:
                await super().set_bot_ready(about=about)
                self._ready_published = True

    async def release_bot_ready(self) -> None:
        """Allow the already-requested public RTVI handshake to complete once."""

        async with self._ready_lock:
            self._ready_release = True
            if self._ready_requested and not self._ready_published and not self._ready_failed:
                await super().set_bot_ready(about=self._ready_about)
                self._ready_published = True

    async def fail_ready(self) -> None:
        async with self._ready_lock:
            self._ready_failed = True


class PipecatEventChannel:
    """Serialize the canonical Murmur envelope inside RTVI server-message data."""

    def __init__(
        self,
        rtvi: RTVIProcessor,
        claims: VoiceCallClaims,
        *,
        producer_id: str | None = None,
        max_buffered_events: int = 128,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        failure_signal: _RuntimeFailureSignal | None = None,
    ) -> None:
        if isinstance(max_buffered_events, bool) or max_buffered_events <= 0:
            raise ValueError("Pipecat event buffer capacity must be positive")
        self._rtvi = rtvi
        self._claims = claims
        self._producer_id = producer_id or f"pipecat-{claims.voice_call_id}"
        self._max_buffered_events = max_buffered_events
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (lambda: "event-" + uuid4().hex)
        self._failure_signal = failure_signal
        self._pending: deque[tuple[EventType, Mapping[str, object], str | None]] = deque()
        self._lock = asyncio.Lock()
        self._producer_sequence = 0
        self._activated = False
        self._closed = False

    @property
    def activated(self) -> bool:
        return self._activated

    @property
    def producer_sequence(self) -> int:
        return self._producer_sequence

    async def emit(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        *,
        turn_id: str | None = None,
    ) -> None:
        if event_type is EventType.AGENT_READY:
            raise PipecatRuntimeError("Ready can only be emitted by channel activation")
        async with self._lock:
            self._ensure_open()
            if not self._activated:
                if len(self._pending) >= self._max_buffered_events:
                    raise PipecatRuntimeError("Pipecat event buffer overflowed before Ready")
                self._pending.append((event_type, dict(payload), turn_id))
                return
            await self._publish(event_type, payload, turn_id=turn_id)

    async def activate(self, profile: PreparedPipecatProfile) -> None:
        async with self._lock:
            self._ensure_open()
            if self._activated:
                raise PipecatRuntimeError("Pipecat event channel is already active")
            readiness = profile.readiness
            required = list(
                dict.fromkeys((*_CORE_READY_COMPONENTS, *readiness.required_components))
            )
            ready = list(dict.fromkeys((*_CORE_READY_COMPONENTS, *readiness.ready_components)))
            await self._publish(
                EventType.AGENT_READY,
                {
                    "profile_id": profile.profile_id,
                    "required_components": required,
                    "ready_components": ready,
                    "profile_config_hash": readiness.config_hash,
                    "provider_models": [
                        {
                            "component": item.component,
                            "provider": item.provider,
                            "model": item.model,
                        }
                        for item in readiness.provider_models
                    ],
                },
            )
            self._activated = True
            while self._pending:
                event_type, payload, turn_id = self._pending[0]
                await self._publish(event_type, payload, turn_id=turn_id)
                self._pending.popleft()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._pending.clear()

    async def _publish(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        *,
        turn_id: str | None = None,
    ) -> None:
        next_sequence = self._producer_sequence + 1
        envelope = EventEnvelope(
            event_id=self._event_id_factory(),
            event_type=event_type,
            trace_id=self._claims.trace_id,
            voice_call_id=self._claims.voice_call_id,
            session_id=self._claims.session_id,
            turn_id=turn_id,
            producer_id=self._producer_id,
            producer_sequence=next_sequence,
            emitted_at=self._clock(),
            payload=payload,
        )
        try:
            await self._rtvi.send_server_message(
                envelope.model_dump(mode="json", exclude_none=True)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._failure_signal is not None:
                self._failure_signal.fail(exc)
            raise
        self._producer_sequence = next_sequence

    def _ensure_open(self) -> None:
        if self._closed:
            raise PipecatRuntimeError("Pipecat event channel is closed")


class CanonicalVoiceEventProcessor(FrameProcessor):
    """Map public Pipecat frames to the existing Murmur event vocabulary."""

    def __init__(
        self,
        channel: PipecatEventChannel,
        *,
        contract_id_factory: Callable[[str], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._channel = channel
        self._contract_id_factory = contract_id_factory or (
            lambda prefix: f"{prefix}-{uuid4().hex}"
        )
        self._final_segments: list[str] = []
        self._active_turn_id: str | None = None
        self._active_speech_id: str | None = None
        self._active_speech_turn_id: str | None = None
        self._speech_interrupted = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterimTranscriptionFrame):
            if frame.text:
                await self._channel.emit(
                    EventType.TRANSCRIPT_SEGMENT,
                    {
                        "segment_id": self._contract_id_factory("segment"),
                        "text": frame.text,
                        "is_final": False,
                    },
                )
        elif isinstance(frame, TranscriptionFrame):
            if frame.text:
                self._final_segments.append(frame.text)
                await self._channel.emit(
                    EventType.TRANSCRIPT_SEGMENT,
                    {
                        "segment_id": self._contract_id_factory("segment"),
                        "text": frame.text,
                        "is_final": True,
                    },
                )
        elif isinstance(frame, UserStartedSpeakingFrame) and self._active_speech_id:
            self._speech_interrupted = True
        elif isinstance(frame, UserStoppedSpeakingFrame) and self._final_segments:
            self._active_turn_id = self._contract_id_factory("turn")
            text = " ".join(self._final_segments).strip()
            self._final_segments.clear()
            if text:
                await self._channel.emit(
                    EventType.TURN_COMMITTED,
                    {"text": text},
                    turn_id=self._active_turn_id,
                )
        elif isinstance(frame, BotStartedSpeakingFrame) and self._active_turn_id:
            self._active_speech_id = self._contract_id_factory("speech")
            self._active_speech_turn_id = self._active_turn_id
            self._speech_interrupted = False
            await self._channel.emit(
                EventType.ASSISTANT_SPEECH_STARTED,
                {"speech_id": self._active_speech_id},
                turn_id=self._active_speech_turn_id,
            )
        elif (
            isinstance(frame, BotStoppedSpeakingFrame)
            and self._active_speech_id
            and self._active_speech_turn_id
        ):
            await self._channel.emit(
                EventType.ASSISTANT_SPEECH_STOPPED,
                {
                    "speech_id": self._active_speech_id,
                    "reason": "interrupted" if self._speech_interrupted else "completed",
                },
                turn_id=self._active_speech_turn_id,
            )
            self._active_speech_id = None
            self._active_speech_turn_id = None
            self._speech_interrupted = False
        await self.push_frame(frame, direction)


class _RuntimeFailureSignal:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._exception: Exception | None = None

    def fail(self, exception: Exception) -> None:
        if self._exception is None:
            self._exception = exception
            self._event.set()

    @property
    def exception(self) -> Exception | None:
        return self._exception

    async def wait(self) -> Exception:
        await self._event.wait()
        assert self._exception is not None
        return self._exception


class _ObservedPipelineWorker(PipelineWorker):
    """Surface raw worker failures that Pipecat's runner otherwise absorbs."""

    def __init__(self, *args: Any, failure_signal: _RuntimeFailureSignal, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._murmur_failure_signal = failure_signal
        self._murmur_cleanup_complete = False
        self._murmur_cleanup_lock = asyncio.Lock()

    @property
    def murmur_cleanup_complete(self) -> bool:
        return self._murmur_cleanup_complete

    async def ensure_murmur_cleanup(self) -> None:
        """Cancel tasks first, then retry the pinned worker's full cleanup."""

        async with self._murmur_cleanup_lock:
            if self._murmur_cleanup_complete:
                return
            await self._cancel_tasks()
            await self._cleanup(cleanup_pipeline=True)
            self._murmur_cleanup_complete = True

    async def run(self, params: WorkerParams) -> None:
        try:
            await super().run(params)
            self._murmur_cleanup_complete = True
        except asyncio.CancelledError as exc:
            # Setup and task creation occur before PipelineWorker's own try/finally.
            # A cancellation there otherwise skips both task and processor cleanup.
            if not self.has_finished():
                try:
                    await self.ensure_murmur_cleanup()
                except asyncio.CancelledError:
                    raise
                except Exception as cleanup_exc:
                    exc.add_note(f"Pipecat cancellation cleanup also failed: {cleanup_exc!r}")
            else:
                self._murmur_cleanup_complete = True
            raise
        except Exception as exc:
            self._murmur_failure_signal.fail(exc)
            # WorkerRunner intentionally consumes child-task exceptions. The
            # pinned worker also skips its normal pipeline cleanup when an
            # exception escapes outside the CancelFrame/EndFrame path, so the
            # observing subclass must finish that owned cleanup before the
            # runner can report completion.
            try:
                await self.ensure_murmur_cleanup()
            except asyncio.CancelledError:
                raise
            except Exception as cleanup_exc:
                exc.add_note(f"Pipecat failure cleanup also failed: {cleanup_exc!r}")
            raise


class _ReadinessGate:
    def __init__(
        self,
        profile: PreparedPipecatProfile,
        channel: PipecatEventChannel,
        rtvi: GatedRTVIProcessor,
        failure_signal: _RuntimeFailureSignal | None = None,
    ) -> None:
        self._profile = profile
        self._channel = channel
        self._rtvi = rtvi
        self._failure_signal = failure_signal or _RuntimeFailureSignal()
        self._pipeline_started = False
        self._providers_ready = False
        self._rtc_connected = False
        self._client_ready = False
        self._failed = False
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()

    async def mark_pipeline_started(self) -> None:
        async with self._lock:
            self._pipeline_started = True
            await self._try_activate_locked()

    async def mark_providers_ready(self) -> None:
        async with self._lock:
            self._providers_ready = True
            await self._try_activate_locked()

    async def mark_rtc_connected(self) -> None:
        async with self._lock:
            self._rtc_connected = True
            await self._try_activate_locked()

    async def mark_client_ready(self) -> None:
        async with self._lock:
            self._client_ready = True
            await self._try_activate_locked()

    async def fail(self) -> None:
        async with self._lock:
            self._failed = True
            await self._rtvi.fail_ready()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    async def _try_activate_locked(self) -> None:
        if self._failed or self._channel.activated:
            return
        if not (
            self._pipeline_started
            and self._providers_ready
            and self._rtc_connected
            and self._client_ready
        ):
            return
        try:
            await self._rtvi.release_bot_ready()
            # Canonical Ready is the final commit point. A failed Pipecat
            # bot-ready handshake must never leave the browser falsely Ready.
            await self._channel.activate(self._profile)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed = True
            await self._rtvi.fail_ready()
            self._failure_signal.fail(exc)
            raise
        self._ready.set()


class PipecatRuntime:
    """Own exactly one Pipecat pipeline worker and runner for one call."""

    def __init__(
        self,
        *,
        claims: VoiceCallClaims,
        profile: PreparedPipecatProfile,
        transport: BaseTransport,
        cleanup_timeout_seconds: float = 5.0,
        readiness_timeout_seconds: float = 10.0,
        active_call_idle_timeout_seconds: float = _DEFAULT_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        if claims.profile_id != profile.profile_id:
            raise PipecatRuntimeError("Pipecat profile does not match authoritative claims")
        if isinstance(cleanup_timeout_seconds, bool) or not 0 < cleanup_timeout_seconds <= 15:
            raise ValueError("Pipecat cleanup timeout must be between zero and 15 seconds")
        if isinstance(readiness_timeout_seconds, bool) or not 0 < readiness_timeout_seconds <= 30:
            raise ValueError("Pipecat readiness timeout must be between zero and 30 seconds")
        _validate_active_call_idle_timeout(active_call_idle_timeout_seconds)
        self._claims = claims
        self._profile = profile
        self._transport = transport
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._rtvi = GatedRTVIProcessor(name=f"rtvi-{claims.voice_call_id}")
        self._failure_signal = _RuntimeFailureSignal()
        self._events = PipecatEventChannel(
            self._rtvi,
            claims,
            failure_signal=self._failure_signal,
        )
        self._event_processor = CanonicalVoiceEventProcessor(
            self._events,
            name=f"events-{claims.voice_call_id}",
        )
        context = LLMContext(messages=[{"role": "system", "content": profile.instructions}])
        aggregators = LLMContextAggregatorPair(
            context,
            realtime_service_mode=False,
            user_params=LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies()),
        )
        self._turn_processor = UserTurnProcessor(
            name=f"turns-{claims.voice_call_id}",
            user_turn_strategies=UserTurnStrategies(
                start=[TranscriptionUserTurnStartStrategy(use_interim=True)],
                stop=[
                    SpeechTimeoutUserTurnStopStrategy(
                        user_speech_timeout=0.3,
                        wait_for_transcript=True,
                    )
                ],
            ),
        )
        self._pipeline = Pipeline(
            [
                transport.input(),
                self._rtvi,
                profile.stt,
                self._turn_processor,
                self._event_processor,
                aggregators.user(),
                profile.llm,
                profile.tts,
                transport.output(),
                aggregators.assistant(),
            ]
        )
        observer = self._rtvi.create_rtvi_observer()

        # Register our client-ready observer before PipelineWorker adds Pipecat's
        # automatic bot-ready callback.  GatedRTVIProcessor makes that callback
        # inert until canonical Murmur Ready has been sent.
        self._gate = _ReadinessGate(
            profile,
            self._events,
            self._rtvi,
            self._failure_signal,
        )

        @self._rtvi.event_handler("on_client_ready")
        async def on_client_ready(_rtvi: RTVIProcessor) -> None:
            try:
                # Record the handshake request ourselves. PipelineWorker also
                # requests it, but its detached callback order is not a safe
                # readiness boundary.
                await self._rtvi.set_bot_ready()
                await self._gate.mark_client_ready()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failure_signal.fail(exc)
                await self._terminate("Pipecat client-ready handling failed")
                raise

        self._worker = _ObservedPipelineWorker(
            self._pipeline,
            name=f"pipeline-{claims.voice_call_id}",
            failure_signal=self._failure_signal,
            params=PipelineParams(
                audio_in_sample_rate=profile.media_policy.input_sample_rate,
                audio_out_sample_rate=profile.media_policy.output_sample_rate,
            ),
            enable_rtvi=True,
            rtvi_processor=self._rtvi,
            observers=[observer],
            # This protects an attached active call. Signaling reservation and
            # assignment expiry remain separate pre-runtime lifecycle policies.
            # This pipeline intentionally has no VAD analyzer, so Pipecat's
            # default UserSpeakingFrame alone cannot observe real user input.
            idle_timeout_frames=_ACTIVE_CALL_IDLE_FRAMES,
            idle_timeout_secs=active_call_idle_timeout_seconds,
            check_dangling_tasks=True,
        )
        self._runner = WorkerRunner(
            name=f"runner-{claims.voice_call_id}",
            handle_sigint=False,
            handle_sigterm=False,
            check_dangling_tasks=True,
        )

        @self._worker.event_handler("on_pipeline_started")
        async def on_pipeline_started(_worker: PipelineWorker, _frame: Frame) -> None:
            self._pipeline_started = True
            try:
                await self._gate.mark_pipeline_started()
                if self._provider_ready_task is None:
                    self._provider_ready_task = asyncio.create_task(
                        self._await_provider_readiness(),
                        name=f"pipecat-provider-readiness:{self._claims.voice_call_id}",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failure_signal.fail(exc)
                await self._terminate("Pipecat pipeline readiness failed")
                raise

        @self._worker.event_handler("on_pipeline_error")
        async def on_pipeline_error(_worker: PipelineWorker, _frame: Frame) -> None:
            self._failure_signal.fail(PipecatRuntimeError("Pipecat pipeline failed"))
            await self._terminate("Pipecat pipeline failed")

        @transport.event_handler("on_client_connected")
        async def on_transport_connected(_transport: BaseTransport, client: object) -> None:
            try:
                connection_id = _canonical_smallwebrtc_connection_id(getattr(client, "pc_id", None))
                payload = {"connection_id": connection_id} if connection_id is not None else {}
                await self._events.emit(EventType.TRANSPORT_CONNECTED, payload)
                await self._gate.mark_rtc_connected()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failure_signal.fail(exc)
                await self._terminate("Pipecat transport connection handling failed")
                raise

        @transport.event_handler("on_client_disconnected")
        async def on_transport_disconnected(_transport: BaseTransport, _client: object) -> None:
            await self._gate.fail()
            try:
                await self._events.emit(
                    EventType.TRANSPORT_DISCONNECTED,
                    {"recoverable": True, "reason": "SmallWebRTC client disconnected"},
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failure_signal.fail(exc)
                raise
            finally:
                await self._cancel_runner_once("SmallWebRTC client disconnected")

        self._run_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._running = False
        self._pipeline_started = False
        self._provider_ready_task: asyncio.Task[None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._ownership_transferred = False
        self._profile_closed = False
        self._closing = False
        self._closed = False
        self._runner_cancelled = False
        self._runner_cancel_lock = asyncio.Lock()

    @property
    def worker(self) -> PipelineWorker:
        return self._worker

    @property
    def runner(self) -> WorkerRunner:
        return self._runner

    @property
    def rtvi(self) -> GatedRTVIProcessor:
        return self._rtvi

    @property
    def events(self) -> PipecatEventChannel:
        return self._events

    async def _terminate(self, reason: str) -> None:
        await self._gate.fail()
        await self._cancel_runner_once(reason)

    async def _cancel_runner_once(self, reason: str) -> None:
        async with self._runner_cancel_lock:
            if self._runner_cancelled:
                return
            await self._runner.cancel(reason)
            self._runner_cancelled = True

    async def _monitor_lifecycle(self) -> None:
        ready_task = asyncio.create_task(self._gate.wait_ready())
        failure_task = asyncio.create_task(self._failure_signal.wait())
        try:
            done, _ = await asyncio.wait(
                (ready_task, failure_task),
                timeout=self._readiness_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in done:
                await self._terminate("Pipecat event channel failed")
                return
            if ready_task not in done:
                error = PipecatRuntimeError("Pipecat runtime readiness timed out")
                self._failure_signal.fail(error)
                await self._terminate(str(error))
                return
            await failure_task
            await self._terminate("Pipecat event channel failed")
        finally:
            for task in (ready_task, failure_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(ready_task, failure_task, return_exceptions=True)

    async def _await_provider_readiness(self) -> None:
        try:
            async with asyncio.timeout(self._readiness_timeout_seconds):
                await self._profile.wait_streams_ready()
            await self._gate.mark_providers_ready()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure_signal.fail(exc)
            await self._terminate("Pipecat provider stream readiness failed")

    async def _close_profile_once(self) -> None:
        if self._profile_closed:
            return
        result = self._profile.close_callback()
        if inspect.isawaitable(result):
            await result
        self._profile_closed = True

    async def run(self) -> None:
        async with self._run_lock:
            if self._closing or self._closed:
                raise PipecatRuntimeError("Pipecat runtime is closed")
            if self._running:
                raise PipecatRuntimeError("Pipecat runtime is already running")
            self._running = True
        failed = False
        monitor_task: asyncio.Task[None] | None = None
        try:
            async with self._run_lock:
                if self._closing or self._closed:
                    raise PipecatRuntimeError("Pipecat runtime closed during startup")
                await self._runner.add_workers(self._worker)
                if self._closing or self._closed:
                    raise PipecatRuntimeError("Pipecat runtime closed during startup")
                # From this point the runner owns worker/pipeline cleanup even if
                # cancellation arrives before StartFrame finishes traversing.
                self._ownership_transferred = True
                monitor_task = asyncio.create_task(
                    self._monitor_lifecycle(),
                    name=f"pipecat-lifecycle:{self._claims.voice_call_id}",
                )
                self._runner_task = asyncio.create_task(
                    self._runner.run(),
                    name=f"pipecat-runner:{self._claims.voice_call_id}",
                )
                # WorkerRunner clears its cancellation event synchronously before
                # its first suspension. Keep startup serialized until that happens.
                await asyncio.sleep(0)
            await self._runner_task
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise asyncio.CancelledError
            if self._failure_signal.exception is not None:
                raise PipecatRuntimeError("Pipecat runtime terminated after failure") from (
                    self._failure_signal.exception
                )
        except asyncio.CancelledError:
            failed = True
            await self._gate.fail()
            raise
        except Exception:
            failed = True
            await self._gate.fail()
            raise
        finally:
            self._running = False
            if self._provider_ready_task is not None:
                self._provider_ready_task.cancel()
                await asyncio.gather(self._provider_ready_task, return_exceptions=True)
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)
            if failed:
                with suppress(Exception):
                    await asyncio.shield(self.close())
            else:
                await self.close()

    async def close(self) -> None:
        """Bound cancellation and release every call-owned object idempotently."""

        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            errors: list[BaseException] = []
            async with self._run_lock:
                try:
                    async with asyncio.timeout(self._cleanup_timeout_seconds):
                        await self._cancel_runner_once("Pipecat runtime closing")
                except Exception as exc:
                    errors.append(exc)
            # After runner attachment, PipelineWorker owns and cleans every
            # service processor, including cancellation during startup.  The
            # profile closer remains the fallback only before that transfer.
            if not self._ownership_transferred:
                try:
                    async with asyncio.timeout(self._cleanup_timeout_seconds):
                        await self._close_profile_once()
                except Exception as exc:
                    errors.append(exc)
            elif not self._running and not self._worker.murmur_cleanup_complete:
                try:
                    async with asyncio.timeout(self._cleanup_timeout_seconds):
                        await self._worker.ensure_murmur_cleanup()
                except Exception as exc:
                    errors.append(exc)
                if not self._worker.murmur_cleanup_complete:
                    try:
                        async with asyncio.timeout(self._cleanup_timeout_seconds):
                            await self._close_profile_once()
                    except Exception as exc:
                        errors.append(exc)
            if errors:
                raise PipecatRuntimeError("Pipecat runtime cleanup failed") from errors[0]
            await self._events.close()
            # A concurrent caller may request close while the owned runner is
            # still unwinding. Let run() perform the final cleanup retry before
            # making the closed state terminal.
            if not self._running:
                self._closed = True


def build_pipecat_runtime(
    *,
    claims: VoiceCallClaims,
    profile: PreparedPipecatProfile,
    transport: BaseTransport,
    cleanup_timeout_seconds: float = 5.0,
    readiness_timeout_seconds: float = 10.0,
    active_call_idle_timeout_seconds: float = _DEFAULT_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS,
) -> PipecatRuntime:
    """Build the owned runtime after signaling constructs its exact transport."""

    return PipecatRuntime(
        claims=claims,
        profile=profile,
        transport=transport,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        readiness_timeout_seconds=readiness_timeout_seconds,
        active_call_idle_timeout_seconds=active_call_idle_timeout_seconds,
    )


class PipecatRuntimeHandle:
    """Bounded, idempotent handle returned to the signaling owner."""

    def __init__(
        self,
        runtime: PipecatRuntime,
        run_task: asyncio.Task[None],
        *,
        close_timeout_seconds: float,
    ) -> None:
        self.runtime = runtime
        self._run_task = run_task
        self._close_timeout_seconds = close_timeout_seconds
        self._close_lock = asyncio.Lock()
        self._closed = False

    @property
    def done(self) -> bool:
        return self._run_task.done()

    async def wait_closed(self) -> None:
        """Wait for terminal runtime completion without transferring cancellation."""

        try:
            await asyncio.shield(self._run_task)
        except asyncio.CancelledError as exc:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            raise PipecatRuntimeError("Pipecat owned runtime task was cancelled") from exc

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            close_error: BaseException | None = None
            unwind_error: BaseException | None = None
            try:
                await self.runtime.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                close_error = exc
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._run_task),
                    timeout=self._close_timeout_seconds,
                )
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                # Cancellation of the owned task is still terminal completion
                # of the unwind barrier. runtime.close() remains authoritative
                # for whether call-owned resources were released successfully.
            except TimeoutError as exc:
                unwind_error = exc
            except Exception:
                # Runtime operation failures are observed through wait_closed().
                # aclose() awaits the task only as an unwind barrier; once it is
                # terminal, a successful runtime.close() proves cleanup.
                pass
            cleanup_error = close_error or unwind_error
            if cleanup_error is not None:
                raise PipecatRuntimeError(
                    "Pipecat runtime handle cleanup failed"
                ) from cleanup_error
            self._closed = True


async def _invoke_cleanup_callback(cleanup: Callable[[], object]) -> None:
    result = cleanup()
    if inspect.isawaitable(result):
        await result


def _consume_cleanup_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _retry_bounded_cleanup(
    cleanup: Callable[[], object],
    *,
    timeout_seconds: float,
    task_name: str,
) -> list[Exception]:
    """Try cleanup twice without trusting a provider coroutine to honor cancellation."""

    errors: list[Exception] = []
    task: asyncio.Task[None] | None = None
    for _attempt in range(2):
        if task is None or task.done():
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            task = asyncio.create_task(_invoke_cleanup_callback(cleanup), name=task_name)
        try:
            done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_cleanup_task_result)
            raise
        if task not in done:
            task.cancel()
            await asyncio.sleep(0)
            errors.append(TimeoutError(f"{task_name} timed out"))
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
                task = None
            continue
        if task.cancelled():
            errors.append(PipecatRuntimeError(f"{task_name} was cancelled"))
            task = None
            continue
        try:
            task.result()
        except Exception as exc:
            errors.append(exc)
            task = None
        else:
            return []
    if task is not None and not task.done():
        task.add_done_callback(_consume_cleanup_task_result)
    return errors


async def _raise_after_failed_cleanup(
    primary: BaseException,
    cleanup: Callable[[], object],
    *,
    timeout_seconds: float,
    task_name: str,
    message: str,
) -> None:
    errors = await _retry_bounded_cleanup(
        cleanup,
        timeout_seconds=timeout_seconds,
        task_name=task_name,
    )
    if not errors:
        return
    if isinstance(primary, asyncio.CancelledError) or not isinstance(primary, Exception):
        primary.add_note(f"{message}: {errors[-1]!r}")
        return
    raise PipecatRuntimeError(message) from ExceptionGroup(
        message,
        [primary, *errors],
    )


async def _cleanup_runtime_handoff_after_failure(
    primary: BaseException,
    runtime: PipecatRuntime,
    run_task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    errors: list[Exception] = []
    for _attempt in range(2):
        try:
            async with asyncio.timeout(timeout_seconds):
                # Keep the original sequential ownership transfer: runtime.run
                # must finish its finally block before an explicit close retry.
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
                await runtime.close()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
    if isinstance(primary, asyncio.CancelledError) or not isinstance(primary, Exception):
        primary.add_note(f"Pipecat runtime handoff cleanup failed: {errors[-1]!r}")
        return
    message = "Pipecat runtime handoff failed and cleanup failed"
    raise PipecatRuntimeError(message) from ExceptionGroup(message, [primary, *errors])


class PipecatRuntimeStarter:
    """Prepare one authoritative profile, then start one SmallWebRTC pipeline."""

    def __init__(
        self,
        profile_provider: PipecatProfileProvider,
        scope_factory: PipecatScopeFactory,
        *,
        transport_factory: PipecatTransportFactory | None = None,
        cleanup_timeout_seconds: float = 5.0,
        readiness_timeout_seconds: float = 10.0,
        active_call_idle_timeout_seconds: float = _DEFAULT_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(scope_factory):
            raise ValueError("Pipecat scope factory must be callable")
        if isinstance(cleanup_timeout_seconds, bool) or not 0 < cleanup_timeout_seconds <= 15:
            raise ValueError("Pipecat cleanup timeout must be between zero and 15 seconds")
        if isinstance(readiness_timeout_seconds, bool) or not 0 < readiness_timeout_seconds <= 30:
            raise ValueError("Pipecat readiness timeout must be between zero and 30 seconds")
        _validate_active_call_idle_timeout(active_call_idle_timeout_seconds)
        self._profile_provider = profile_provider
        self._scope_factory = scope_factory
        self._transport_factory = transport_factory or _smallwebrtc_transport
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._active_call_idle_timeout_seconds = active_call_idle_timeout_seconds

    async def start(
        self,
        *,
        connection: PipecatPeerConnection,
        claims: VoiceCallClaims,
    ) -> PipecatRuntimeHandle:
        scope_result = self._scope_factory(claims)
        scope = await scope_result if inspect.isawaitable(scope_result) else scope_result
        if not isinstance(scope, VoiceProfileScope):
            raise PipecatRuntimeError("Pipecat scope factory returned an invalid scope")
        if (
            scope.profile_id != claims.profile_id
            or scope.user_id != claims.user_id
            or scope.session_id != claims.session_id
            or scope.agent_id != claims.agent_id
            or scope.voice_call_id != claims.voice_call_id
            or scope.trace_id != claims.trace_id
        ):
            raise PipecatRuntimeError("Pipecat scope does not match authoritative claims")

        profile = await self._profile_provider.prepare(scope)
        try:
            transport = self._transport_factory(connection, profile)
            runtime = build_pipecat_runtime(
                claims=claims,
                profile=profile,
                transport=transport,
                cleanup_timeout_seconds=self._cleanup_timeout_seconds,
                readiness_timeout_seconds=self._readiness_timeout_seconds,
                active_call_idle_timeout_seconds=self._active_call_idle_timeout_seconds,
            )
        except asyncio.CancelledError as exc:
            await _raise_after_failed_cleanup(
                exc,
                profile.close_callback,
                timeout_seconds=self._cleanup_timeout_seconds,
                task_name=f"pipecat-pre-runtime-profile-cleanup:{claims.voice_call_id}",
                message="Pipecat startup cancellation cleanup failed",
            )
            raise
        except Exception as exc:
            await _raise_after_failed_cleanup(
                exc,
                profile.close_callback,
                timeout_seconds=self._cleanup_timeout_seconds,
                task_name=f"pipecat-pre-runtime-profile-cleanup:{claims.voice_call_id}",
                message="Pipecat startup failed and prepared-profile cleanup failed",
            )
            raise

        run_task = asyncio.create_task(
            runtime.run(),
            name=f"pipecat-runtime:{claims.voice_call_id}",
        )
        try:
            await asyncio.sleep(0)
            if run_task.done():
                run_task.result()
            return PipecatRuntimeHandle(
                runtime,
                run_task,
                close_timeout_seconds=self._cleanup_timeout_seconds,
            )
        except BaseException as exc:
            await _cleanup_runtime_handoff_after_failure(
                exc,
                runtime,
                run_task,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            raise


def _smallwebrtc_transport(
    connection: PipecatPeerConnection,
    profile: PreparedPipecatProfile,
) -> BaseTransport:
    media = profile.media_policy
    return SmallWebRTCTransport(
        webrtc_connection=connection,  # type: ignore[arg-type]
        params=TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=media.input_sample_rate,
            audio_in_channels=media.input_channels,
            audio_in_stream_on_start=False,
            audio_in_passthrough=True,
            audio_out_enabled=True,
            audio_out_sample_rate=media.output_sample_rate,
            audio_out_channels=media.output_channels,
            # Pipecat's default batches four 10 ms chunks, and SmallWebRTC does
            # not flush RawAudioTrack's already-enqueued tail on interruption.
            # One chunk bounds that non-flushable tail at 10 ms instead of 40 ms.
            audio_out_10ms_chunks=1,
        ),
    )

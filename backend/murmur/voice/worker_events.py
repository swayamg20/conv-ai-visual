"""Serialized LiveKit event publication and public AgentSession event bridging."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from murmur.voice.bootstrap_contracts import VoiceJobMetadata, is_contract_id
from murmur.voice.contracts import EventEnvelope, EventType
from murmur.voice.profile import ProfilePreflight
from murmur.voice.worker_contracts import VoiceSessionLifecycleError

_CORE_READY_COMPONENTS = ("worker", "input", "output", "event_channel")


class VoiceDataPublisher(Protocol):
    async def publish_data(
        self,
        payload: bytes | str,
        *,
        reliable: bool,
        destination_identities: list[str],
        topic: str,
    ) -> None: ...


class AgentSessionEventSource(Protocol):
    def on(self, event: str, callback: Callable[..., None]) -> object: ...

    def off(self, event: str, callback: Callable[..., None]) -> None: ...


class SpeechHandleView(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def interrupted(self) -> bool: ...

    def done(self) -> bool: ...

    def exception(self) -> BaseException | None: ...

    def add_done_callback(self, callback: Callable[[Any], None]) -> None: ...

    def remove_done_callback(self, callback: Callable[[Any], None]) -> None: ...


@dataclass(frozen=True)
class _PendingEvent:
    event_type: EventType
    payload: Mapping[str, object]
    turn_id: str | None = None


@dataclass(frozen=True)
class _WriteRequest:
    event: _PendingEvent
    acknowledged: asyncio.Future[None] | None = None


class VoiceEventChannel:
    """One per-job writer for the signed server-to-browser event stream.

    Session callbacks submit synchronously in callback order. Before activation,
    semantic events stay in a bounded buffer. Activation queues Ready first and
    only then releases that buffer to the single writer task.
    """

    def __init__(
        self,
        publisher: VoiceDataPublisher,
        metadata: VoiceJobMetadata,
        *,
        max_buffered_events: int = 128,
        publish_timeout_seconds: float = 3.0,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if isinstance(max_buffered_events, bool) or max_buffered_events <= 0:
            raise ValueError("VoiceEventChannel buffer capacity must be positive")
        if isinstance(publish_timeout_seconds, bool) or publish_timeout_seconds <= 0:
            raise ValueError("VoiceEventChannel publish timeout must be positive")
        self._publisher = publisher
        self._metadata = metadata
        self._max_buffered_events = max_buffered_events
        self._publish_timeout_seconds = publish_timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (lambda: "event-" + uuid4().hex)
        self._buffered: deque[_PendingEvent] = deque()
        self._writes: asyncio.Queue[_WriteRequest | None] = asyncio.Queue(
            maxsize=max_buffered_events + 1
        )
        self._writer = asyncio.create_task(
            self._run_writer(),
            name=f"voice-event-writer:{metadata.voice_call_id}",
        )
        self._close_lock = asyncio.Lock()
        self._producer_sequence = 0
        self._activated = False
        self._closed = False
        self._failure: BaseException | None = None
        self._failure_signal = asyncio.Event()

    @property
    def activated(self) -> bool:
        return self._activated

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def producer_sequence(self) -> int:
        return self._producer_sequence

    def emit(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        *,
        turn_id: str | None = None,
    ) -> None:
        """Submit one semantic event without creating an additional writer task."""
        if event_type is EventType.AGENT_READY:
            raise VoiceSessionLifecycleError("Ready can only be emitted by channel activation")
        self._ensure_usable()
        pending = _PendingEvent(event_type=event_type, payload=dict(payload), turn_id=turn_id)
        if not self._activated:
            if len(self._buffered) >= self._max_buffered_events:
                self.fail(
                    VoiceSessionLifecycleError("voice event pre-activation buffer overflowed")
                )
                raise self._lifecycle_failure()
            self._buffered.append(pending)
            return
        self._enqueue(_WriteRequest(pending))

    async def activate(self, preflight: ProfilePreflight) -> None:
        """Publish Ready first, then make all buffered semantic events writable."""
        self._ensure_usable()
        if self._activated:
            raise VoiceSessionLifecycleError("voice event channel is already activated")
        required = list(dict.fromkeys((*_CORE_READY_COMPONENTS, *preflight.required_components)))
        ready = list(dict.fromkeys((*_CORE_READY_COMPONENTS, *preflight.ready_components)))
        acknowledged = asyncio.get_running_loop().create_future()
        self._enqueue(
            _WriteRequest(
                _PendingEvent(
                    event_type=EventType.AGENT_READY,
                    payload={
                        "profile_id": self._metadata.profile_id,
                        "required_components": required,
                        "ready_components": ready,
                        "profile_config_hash": preflight.config_hash,
                        "provider_models": [
                            {
                                "component": descriptor.component,
                                "provider": descriptor.provider,
                                "model": descriptor.model,
                            }
                            for descriptor in preflight.provider_models
                        ],
                    },
                ),
                acknowledged=acknowledged,
            )
        )
        self._activated = True
        while self._buffered:
            self._enqueue(_WriteRequest(self._buffered.popleft()))
        await acknowledged
        await self.wait_for_idle()

    async def wait_for_idle(self) -> None:
        """Wait until every currently queued event has been handled."""
        await self._writes.join()
        if self._failure is not None:
            raise self._lifecycle_failure()

    async def wait_for_failure(self) -> None:
        """Block until publication is terminally unusable, then raise its error."""
        await self._failure_signal.wait()
        raise self._lifecycle_failure()

    def fail(self, error: BaseException) -> None:
        """Record a terminal callback-side failure for the owning runtime."""
        if self._failure is None:
            self._failure = error
            self._failure_signal.set()

    async def close(self) -> None:
        """Stop the writer and release every buffered/queued object idempotently."""
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._buffered.clear()
            self._writer.cancel()
            with suppress(asyncio.CancelledError):
                await self._writer
            while not self._writes.empty():
                request = self._writes.get_nowait()
                if request is not None and request.acknowledged is not None:
                    self._set_acknowledgement_error(request.acknowledged)
                self._writes.task_done()

    def _ensure_usable(self) -> None:
        if self._closed:
            raise VoiceSessionLifecycleError("voice event channel is closed")
        if self._failure is not None:
            raise self._lifecycle_failure()

    def _enqueue(self, request: _WriteRequest) -> None:
        try:
            self._writes.put_nowait(request)
        except asyncio.QueueFull as exc:
            self.fail(VoiceSessionLifecycleError("voice event writer queue overflowed"))
            raise self._lifecycle_failure() from exc

    async def _run_writer(self) -> None:
        while True:
            request = await self._writes.get()
            try:
                if request is None:
                    return
                await self._publish(request.event)
                if request.acknowledged is not None and not request.acknowledged.done():
                    request.acknowledged.set_result(None)
            except asyncio.CancelledError:
                if request is not None and request.acknowledged is not None:
                    self._set_acknowledgement_error(request.acknowledged)
                raise
            except BaseException as exc:
                self.fail(exc)
                if request is not None and request.acknowledged is not None:
                    self._set_acknowledgement_error(request.acknowledged)
                self._fail_queued_acknowledgements()
                return
            finally:
                self._writes.task_done()

    async def _publish(self, pending: _PendingEvent) -> None:
        self._producer_sequence += 1
        event = EventEnvelope(
            event_id=self._event_id_factory(),
            event_type=pending.event_type,
            trace_id=self._metadata.trace_id,
            voice_call_id=self._metadata.voice_call_id,
            session_id=self._metadata.session_id,
            turn_id=pending.turn_id,
            producer_id=self._metadata.worker_name,
            producer_sequence=self._producer_sequence,
            emitted_at=self._clock(),
            payload=pending.payload,
        )
        await asyncio.wait_for(
            self._publisher.publish_data(
                event.model_dump_json(exclude_none=True),
                reliable=True,
                destination_identities=[self._metadata.participant_identity],
                topic=self._metadata.event_topic,
            ),
            timeout=self._publish_timeout_seconds,
        )

    def _fail_queued_acknowledgements(self) -> None:
        while not self._writes.empty():
            request = self._writes.get_nowait()
            if request is not None and request.acknowledged is not None:
                self._set_acknowledgement_error(request.acknowledged)
            self._writes.task_done()

    def _set_acknowledgement_error(self, acknowledged: asyncio.Future[None]) -> None:
        if not acknowledged.done():
            acknowledged.set_exception(self._lifecycle_failure())

    def _lifecycle_failure(self) -> VoiceSessionLifecycleError:
        failure = VoiceSessionLifecycleError("voice event channel publication failed")
        if self._failure is not None:
            failure.__cause__ = self._failure
        return failure


@dataclass
class _SpeechAssociation:
    handle: SpeechHandleView
    speech_id: str
    turn_id: str | None = None
    started: bool = False
    stopped: bool = False


class AgentSessionEventBridge:
    """Translate pinned public AgentSession events to Murmur semantic events.

    Milestone 1 deliberately associates the next committed user turn with the
    next public ``SpeechHandle`` in bounded FIFO order and exposes one active
    assistant speech at a time. Multi-step/tool speech ownership is a later
    conductor concern; overflow fails the per-job channel closed rather than
    guessing at an association or reading private session state.
    """

    _EVENT_CALLBACK_NAMES = (
        "user_input_transcribed",
        "conversation_item_added",
        "speech_created",
        "agent_state_changed",
        "close",
    )

    def __init__(
        self,
        session: AgentSessionEventSource,
        channel: VoiceEventChannel,
        *,
        max_pending_speeches: int = 32,
        contract_id_factory: Callable[[str], str] | None = None,
        on_session_closed: Callable[[object], None] | None = None,
    ) -> None:
        if isinstance(max_pending_speeches, bool) or max_pending_speeches <= 0:
            raise ValueError("speech association capacity must be positive")
        self._session = session
        self._channel = channel
        self._max_pending_speeches = max_pending_speeches
        self._contract_id_factory = contract_id_factory or (
            lambda prefix: f"{prefix}-{uuid4().hex}"
        )
        self._on_session_closed_callback = on_session_closed
        self._pending_turns: deque[str] = deque()
        self._unpaired_speeches: deque[_SpeechAssociation] = deque()
        self._pending_starts: deque[_SpeechAssociation] = deque()
        self._deferred_terminal_stops: deque[_SpeechAssociation] = deque()
        self._associations: dict[int, _SpeechAssociation] = {}
        self._active: _SpeechAssociation | None = None
        self._pending_speaking = 0
        self._bound = False
        self._closed = False
        self._registered_events: list[str] = []
        self._callbacks: dict[str, Callable[..., None]] = {
            "user_input_transcribed": self._guard(self._on_user_input_transcribed),
            "conversation_item_added": self._guard(self._on_conversation_item_added),
            "speech_created": self._guard(self._on_speech_created),
            "agent_state_changed": self._guard(self._on_agent_state_changed),
            "close": self._guard(self._on_session_closed),
        }
        self._speech_done_callback = self._guard(self._on_speech_done)

    @property
    def bound(self) -> bool:
        return self._bound

    def bind(self) -> None:
        if self._closed:
            raise VoiceSessionLifecycleError("agent session event bridge is closed")
        if self._bound:
            return
        try:
            for event_name in self._EVENT_CALLBACK_NAMES:
                self._session.on(event_name, self._callbacks[event_name])
                self._registered_events.append(event_name)
        except Exception:
            for event_name in reversed(self._registered_events):
                self._session.off(event_name, self._callbacks[event_name])
            self._registered_events.clear()
            raise
        self._bound = True

    def unbind(self) -> None:
        if not self._bound and not self._registered_events:
            return
        for event_name in reversed(self._registered_events):
            self._session.off(event_name, self._callbacks[event_name])
        self._registered_events.clear()
        for association in self._associations.values():
            association.handle.remove_done_callback(self._speech_done_callback)
        self._bound = False
        self._pending_turns.clear()
        self._unpaired_speeches.clear()
        self._pending_starts.clear()
        self._deferred_terminal_stops.clear()
        self._associations.clear()
        self._active = None
        self._pending_speaking = 0

    def close(self) -> None:
        if self._closed:
            return
        self.unbind()
        self._closed = True

    def _on_user_input_transcribed(self, event: object) -> None:
        transcript = getattr(event, "transcript", None)
        is_final = getattr(event, "is_final", None)
        if not isinstance(transcript, str) or not isinstance(is_final, bool):
            return
        segment_id = self._validated_or_generated_id(getattr(event, "item_id", None), "segment")
        self._channel.emit(
            EventType.TRANSCRIPT_SEGMENT,
            {
                "segment_id": segment_id,
                "text": transcript,
                "is_final": is_final,
            },
        )

    def _on_conversation_item_added(self, event: object) -> None:
        item = getattr(event, "item", None)
        if getattr(item, "role", None) != "user":
            return
        text = getattr(item, "text_content", None)
        if not isinstance(text, str) or not text.strip():
            return
        turn_id = self._validated_or_generated_id(getattr(item, "id", None), "turn")
        self._channel.emit(
            EventType.TURN_COMMITTED,
            {"text": text},
            turn_id=turn_id,
        )
        self._bounded_append(self._pending_turns, turn_id, "pending turn")
        self._pair_turns_and_speeches()

    def _on_speech_created(self, event: object) -> None:
        handle = getattr(event, "speech_handle", None)
        if handle is None or id(handle) in self._associations:
            return
        required = ("done", "exception", "add_done_callback", "remove_done_callback")
        if any(not callable(getattr(handle, name, None)) for name in required):
            return
        if len(self._associations) >= self._max_pending_speeches:
            raise VoiceSessionLifecycleError("agent session speech association FIFO overflowed")
        speech_id = self._validated_or_generated_id(getattr(handle, "id", None), "speech")
        association = _SpeechAssociation(handle=handle, speech_id=speech_id)
        self._associations[id(handle)] = association
        handle.add_done_callback(self._speech_done_callback)
        self._bounded_append(self._unpaired_speeches, association, "unpaired speech")
        self._pair_turns_and_speeches()

    def _on_agent_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking":
            if self._active is None and self._pending_starts:
                self._start_next_speech()
            elif self._active is None:
                if self._pending_speaking >= self._max_pending_speeches:
                    raise VoiceSessionLifecycleError("agent session speaking-state FIFO overflowed")
                self._pending_speaking += 1

    def _on_session_closed(self, event: object) -> None:
        if self._on_session_closed_callback is not None:
            self._on_session_closed_callback(event)

    def _on_speech_done(self, handle: object) -> None:
        association = self._associations.get(id(handle))
        if association is None:
            return
        if association is self._active:
            self._stop_active_speech()
            return
        if not association.started:
            self._discard_unstarted_speech(association)

    def _discard_unstarted_speech(self, association: _SpeechAssociation) -> None:
        """Resolve a handle that terminated before LiveKit emitted speaking state."""
        consumes_pending_speaking = (
            self._pending_speaking > 0
            and bool(self._unpaired_speeches)
            and self._unpaired_speeches[0] is association
        )
        with suppress(ValueError):
            self._unpaired_speeches.remove(association)
        with suppress(ValueError):
            self._pending_starts.remove(association)
        if consumes_pending_speaking:
            self._pending_speaking -= 1
        association.handle.remove_done_callback(self._speech_done_callback)
        self._associations.pop(id(association.handle), None)
        if association.turn_id is None:
            return
        if self._active is not None:
            self._bounded_append(
                self._deferred_terminal_stops,
                association,
                "deferred terminal speech",
            )
            return
        self._emit_terminal_speech_stop(association)

    def _pair_turns_and_speeches(self) -> None:
        # LiveKit can invalidate a preemptive generation before its handle is
        # fully done, then publish the replacement handle. Never bind a later
        # committed user turn to that interrupted stale FIFO head.
        while self._unpaired_speeches:
            candidate = self._unpaired_speeches[0]
            if not (candidate.handle.interrupted or candidate.handle.done()):
                break
            self._discard_unstarted_speech(candidate)
        while self._pending_turns and self._unpaired_speeches:
            association = self._unpaired_speeches.popleft()
            association.turn_id = self._pending_turns.popleft()
            self._bounded_append(self._pending_starts, association, "pending speech")
        while self._pending_speaking and self._active is None and self._pending_starts:
            self._pending_speaking -= 1
            self._start_next_speech()

    def _start_next_speech(self) -> None:
        if self._active is not None or not self._pending_starts:
            return
        association = self._pending_starts.popleft()
        if association.turn_id is None:
            return
        association.started = True
        self._active = association
        self._channel.emit(
            EventType.ASSISTANT_SPEECH_STARTED,
            {"speech_id": association.speech_id},
            turn_id=association.turn_id,
        )
        if association.handle.done():
            self._stop_active_speech()

    def _stop_active_speech(self) -> None:
        association = self._active
        if association is None or association.stopped or association.turn_id is None:
            return
        self._active = None
        try:
            self._emit_terminal_speech_stop(association)
        finally:
            association.handle.remove_done_callback(self._speech_done_callback)
            self._associations.pop(id(association.handle), None)
            self._flush_deferred_terminal_stops()

    def _emit_terminal_speech_stop(self, association: _SpeechAssociation) -> None:
        if association.stopped or association.turn_id is None:
            return
        association.stopped = True
        reason = "cancelled"
        if association.handle.interrupted:
            reason = "interrupted"
        elif association.handle.done():
            reason = "error" if association.handle.exception() is not None else "completed"
        self._channel.emit(
            EventType.ASSISTANT_SPEECH_STOPPED,
            {"speech_id": association.speech_id, "reason": reason},
            turn_id=association.turn_id,
        )

    def _flush_deferred_terminal_stops(self) -> None:
        while self._active is None and self._deferred_terminal_stops:
            self._emit_terminal_speech_stop(self._deferred_terminal_stops.popleft())

    def _bounded_append(self, queue: deque[Any], value: Any, label: str) -> None:
        if len(queue) >= self._max_pending_speeches:
            error = VoiceSessionLifecycleError(f"agent session {label} FIFO overflowed")
            self._channel.fail(error)
            raise error
        queue.append(value)

    def _validated_or_generated_id(self, value: object, prefix: str) -> str:
        if is_contract_id(value):
            return value
        generated = self._contract_id_factory(prefix)
        if not is_contract_id(generated):
            raise VoiceSessionLifecycleError("event bridge generated an invalid contract ID")
        return generated

    def _guard(self, callback: Callable[[object], None]) -> Callable[[object], None]:
        def guarded(event: object) -> None:
            try:
                callback(event)
            except Exception as exc:
                # LiveKit's EventEmitter logs and suppresses callback exceptions.
                # Preserve the failure on the job-owned channel so the runtime and
                # focused tests can observe a deterministic terminal condition.
                self._channel.fail(exc)

        return guarded
